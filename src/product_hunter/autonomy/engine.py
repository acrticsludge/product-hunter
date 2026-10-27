"""The loop that keeps the operation running.

One cycle reads the clock, checks the bounds, does the upkeep that is due,
claims a job if the queue has room for one, runs it and records what
happened. The order is the point: the bounds are checked before anything is
claimed, so an operation that has run out of budget stops before it starts
work it cannot pay for.
"""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import threading
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import datetime, timedelta

from product_hunter.autonomy.budget import (
    STAGE_SCOPE_PREFIX,
    SOURCE_SCOPE_PREFIX,
    OperationBudget,
    build_budget_spec,
)
from product_hunter.autonomy.cadence import CadenceConfig, load_cadence, upkeep_beat
from product_hunter.autonomy.crash import recover
from product_hunter.autonomy.health import WorkerHealth, list_worker_health
from product_hunter.autonomy.limits import ConcurrencyLimiter, SourcePacer
from product_hunter.autonomy.maintenance import run_maintenance
from product_hunter.autonomy.states import (
    OperationState,
    TERMINAL_STATES,
    classify_failure,
)
from product_hunter.config import AutonomyConfig
from product_hunter.db.connection import rollback, transaction, utc_now
from product_hunter.db.jobs import Job, defer_queued_job, get_job, peek_job
from product_hunter.db.scheduler import (
    Priority,
    Schedule,
    ScheduleTick,
    Scheduler,
    default_schedules,
)
from product_hunter.fetch import utcnow
from product_hunter.research.budget import BudgetDimension, BudgetExceeded
from product_hunter.runtime.events import EventSink
from product_hunter.runtime.identity import WorkerIdentity
from product_hunter import probes
from product_hunter.registry import CreateRequest

# The runtime's own masker, so the failure text that reaches a row is masked
# the same way in both layers.
from product_hunter.runtime.worker import Handler, WorkerRuntime, _safe_error

from product_hunter.preflight import PreflightFailure, preflight_run
from product_hunter.probe_watchdog import report_stalls, thresholds_for

from product_hunter.sources import load_registry

DEFAULT_OPERATION_ID = "autonomy"

# How many times a cycle looks at the next job before it gives up. A source
# that is not due pushes its job back, and the next job may name the same
# source, so the cycle stops rather than spin.
_MAX_DEFERRALS = 10

# The counters the operation row keeps.
_COUNTERS = frozenset(
    {
        "jobs_completed",
        "jobs_failed",
        "jobs_dead",
        "jobs_blocked",
        "leases_recovered",
        "missed_occurrences",
    }
)


@dataclass(frozen=True)
class OperationStep:
    """What one cycle of the operation did."""

    claimed: int = 0
    completed: int = 0
    failed: int = 0
    deferred: int = 0
    blocked: int = 0
    missed_occurrences: int = 0
    leases_recovered: int = 0
    probes_started: int = 0
    probes_stalled: int = 0
    state: OperationState = OperationState.running
    reason: str = ""

    def to_dict(self) -> dict[str, object]:
        """The step as plain data."""
        return {
            "claimed": self.claimed,
            "completed": self.completed,
            "failed": self.failed,
            "deferred": self.deferred,
            "blocked": self.blocked,
            "missed_occurrences": self.missed_occurrences,
            "leases_recovered": self.leases_recovered,
            "probes_started": self.probes_started,
            "probes_stalled": self.probes_stalled,
            "state": self.state.value,
            "reason": self.reason,
        }


class OperationEngine:
    """One operation: it schedules, claims, runs and accounts for the work."""

    def __init__(
        self,
        conn: sqlite3.Connection,
        identity: WorkerIdentity,
        handlers: Mapping[str, Handler],
        *,
        config: AutonomyConfig,
        schedules: Sequence[Schedule] | None = None,
        cadence: CadenceConfig | None = None,
        events: EventSink | None = None,
        clock: Callable[[], datetime] | None = None,
        connection_factory: Callable[[], sqlite3.Connection] | None = None,
        operation_id: str = DEFAULT_OPERATION_ID,
    ) -> None:
        self.conn = conn
        self.identity = identity
        self.handlers = dict(handlers)
        self.config = config
        self.operation_id = operation_id
        self.events = events
        self.clock = clock if clock is not None else utcnow
        self.cadence = cadence if cadence is not None else load_cadence()
        self.schedules = (
            list(schedules) if schedules is not None else default_schedules()
        )
        self.scheduler = Scheduler(self.conn, self.schedules)
        self.budget = OperationBudget(
            self.conn, operation_id, build_budget_spec(config)
        )
        self.health = WorkerHealth(self.conn, identity)
        self.pacer = SourcePacer(
            self.conn,
            self._source_intervals(),
            default_interval_seconds=config.source_pacing.default_min_interval_seconds,
        )
        self.limiter = ConcurrencyLimiter(self.conn, config.max_concurrent_jobs)
        self._connection_factory = connection_factory
        self._runtime: WorkerRuntime | None = None
        self._started = False
        self._stop = threading.Event()
        self._events = 0
        # The faults of the cycles that follow each other without a good one
        # in between.
        self._consecutive_errors = 0
        # The wall time is charged from the moment the engine exists, so the
        # first cycle charges what it took to get there.
        self._last_charge: datetime | None = utc_now(self.clock())
        self._last_scheduler: datetime | None = None
        self._last_watchdog: datetime | None = None
        self._last_recovery: datetime | None = None
        self._last_maintenance: datetime | None = None

    @property
    def runtime(self) -> WorkerRuntime:
        """The worker runtime that claims the jobs and runs the handlers."""
        if self._runtime is None:
            self._runtime = self._build_runtime()
        return self._runtime

    def start(self) -> None:
        """Open the operation: the worker is registered and the row is written.

        Preflight runs first: invalid configuration refuses to start the worker
        so no job is claimed and no backoff is consumed on a config that cannot
        succeed.
        """
        report = preflight_run()
        if not report.ok:
            failure = report.failures[0]
            raise PreflightFailure(
                f"preflight refused to start: {failure.file} {failure.error or 'invalid config'}"
            )
        self.health.register(now=self.clock())
        self._set_state(OperationState.running, "")
        self._record_event(
            "operation_started", {"worker_id": self.identity.worker_id}
        )
        # The runtime is built here, so a setting it refuses is reported when
        # the operation opens rather than in the middle of a cycle.
        self._runtime = self._build_runtime()
        self._started = True

    def run_once(self) -> OperationStep:
        """One cycle of the operation."""
        now = utc_now(self.clock())
        state = self._state()
        if state in TERMINAL_STATES:
            return OperationStep(state=state, reason=self._reason())
        if not self._started:
            self.start()
        step = OperationStep(state=state)
        if self._stop.is_set():
            step = self._finish(OperationState.paused, "stop requested", step)
            return self._charge_wall_time(step)

        # The budget is checked before anything is claimed, so an operation
        # that has run out of it stops before it starts new work.
        exhausted = self.budget.exhausted()
        if exhausted:
            return self._finish(
                OperationState.budget_exhausted, ", ".join(exhausted), step
            )

        missed = self._tick_scheduler(now)
        probes = self._tick_probes(now)
        # Recovery runs before the watchdog: a lease that expired during a
        # restart is requeued in this same cycle, so the watchdog only names a
        # lease that recovery is not reclaiming.
        leases = self._run_recovery(now)
        stalled = self._run_watchdog(now)
        self._run_maintenance(now)
        step = replace(
            step,
            missed_occurrences=missed,
            leases_recovered=leases,
            probes_started=probes,
            probes_stalled=stalled,
        )

        # The queue is full, so another process is doing the work. This cycle
        # claims nothing.
        if self.limiter.at_capacity(now=now):
            return self._charge_wall_time(step)

        job, deferred, blocked = self._next_job(now)
        step = replace(step, deferred=deferred, blocked=blocked)
        if blocked:
            self._add_counter("jobs_blocked", blocked)
        if job is None:
            return self._charge_wall_time(step)

        outcome = self.runtime.run_once()
        if outcome is None:
            return self._charge_wall_time(step)
        # The job that ran is the one the outcome names, not the one that was
        # peeked: the queue may have moved between the two.
        claimed = get_job(self.conn, outcome.job_id) or job
        step = replace(step, claimed=1)
        completed, failed, stopping = self._record_outcome(outcome, claimed, now)
        step = replace(step, completed=completed, failed=failed)
        if stopping is not None:
            step = self._finish(stopping, outcome.error or "", step)
        return self._charge_wall_time(step)

    def run_forever(self, stop_event: threading.Event | None = None) -> None:
        """Run cycles until a stop is asked for.

        A cycle that found nothing to do waits for the poll interval, so an
        idle operation does not spin on the database. A cycle that raised is
        recorded and the loop goes on, until too many of them follow each
        other: then the operation ends with a state an operator can read.
        """
        self.start()
        while not self._stopping(stop_event):
            try:
                step = self.run_once()
            except Exception as exc:
                # An unexpected fault must not leave the operation looking
                # alive. The error is recorded, and the operation ends when
                # the faults keep coming.
                error = _safe_error(exc)
                self._consecutive_errors += 1
                self._record_error_event(error)
                if self._consecutive_errors >= self.config.max_consecutive_errors:
                    # The terminal write can itself fail when the store is not
                    # writable. The cause is already in the operation_error event
                    # above, so the process ends cleanly and the operator reads
                    # the cause from the event log rather than from a traceback.
                    try:
                        self._finish(
                            OperationState.permanent_failure,
                            error,
                            OperationStep(),
                        )
                    except sqlite3.Error:
                        pass
                    break
                step = None
            else:
                self._consecutive_errors = 0
            if step is not None and step.state in TERMINAL_STATES:
                # The operation ended, so the loop ends with it.
                break
            if step is None or not step.claimed:
                self._stop.wait(self.config.poll_seconds)
        if self._state() not in TERMINAL_STATES:
            self._set_state(OperationState.paused, "stop requested")
            self._record_event("operation_paused", {"reason": "stop requested"})

    def request_stop(self) -> None:
        """Ask for a graceful stop.

        The operation ends paused, and the next process picks the queue up
        where it was left.
        """
        self._stop.set()
        if self._runtime is not None:
            self._runtime.request_stop()

    def status(self) -> dict[str, object]:
        """What the operation looks like right now."""
        return operation_status(self.conn, operation_id=self.operation_id)

    # --- the cycle --------------------------------------------------------

    def _tick_scheduler(self, now: datetime) -> int:
        """Write the jobs of the beats whose moment has passed.

        The number that comes back is the count of the beats that came while
        nothing was running.
        """
        if not self._due(
            self._last_scheduler, self.config.scheduler_interval_seconds, now
        ):
            return 0
        self._last_scheduler = now
        tick = self.scheduler.tick(now=now)
        missed = _missed_occurrences(tick, now)
        if missed:
            self._add_counter("missed_occurrences", missed)
        return missed

    def _run_recovery(self, now: datetime) -> int:
        """Recover the work a dead process left behind."""
        if not self._due(
            self._last_recovery, self.config.recovery_interval_seconds, now
        ):
            return 0
        self._last_recovery = now
        report = recover(self.conn, now=now)
        if report.leases_recovered:
            self._add_counter("leases_recovered", report.leases_recovered)
        # Resolve probe pause/stop requests whose worker is gone, so a probe
        # left in PAUSE_REQUESTED/STOP_REQUESTED after a crash returns to the
        # RUNNING cadence instead of dying silently.
        from product_hunter import probes as probe_registry

        probe_registry.recover_probes(self.conn, now=now)
        return report.leases_recovered

    def _tick_probes(self, now: datetime) -> int:
        """Start a finite research run for each due RUNNING probe with no open
        cycle, and record the result of a cycle whose run has ended.

        A probe owns at most one open cycle at a time (the partial index on
        ``research_runs(probe_id) WHERE probe_cycle_closed_at IS NULL``
        enforces it). A cycle is open until its result is recorded, because
        run.status is operator state and stays QUEUED for a probe run. When
        the cycle ends the result is written once, the cycle is closed, and
        next_cycle_at and backoff advance; the probe then waits for its next
        cadence moment before starting another run.
        """
        from product_hunter.registry import CreateRequest, create_run

        from product_hunter import probes as probe_registry

        started = 0
        now_iso = now.isoformat()
        terminal = ("COMPLETED", "CANCELLED", "FAILED_PERMANENT")
        # A probe is due when it has never run (next_cycle_at is NULL) or its
        # next cadence moment has passed.
        rows = self.conn.execute(
            "SELECT * FROM probes WHERE status = 'RUNNING' "
            "AND (next_cycle_at IS NULL OR next_cycle_at <= ?) "
            "ORDER BY next_cycle_at, public_id",
            (now_iso,),
        ).fetchall()
        for row in rows:
            probe_id = row["probe_id"]
            # The open cycle is the one whose result has not been recorded.
            # Run.status cannot answer this: a probe run stays QUEUED.
            open_run = self.conn.execute(
                "SELECT run_id, status FROM research_runs WHERE probe_id = ? "
                "AND probe_cycle_closed_at IS NULL "
                "ORDER BY created_at DESC LIMIT 1",
                (probe_id,),
            ).fetchone()
            if open_run is not None:
                current = open_run["run_id"]
                status = str(open_run["status"]).upper()
                if status not in terminal:
                    if status not in ("QUEUED", "RUNNING"):
                        # An operator holds the run. The probe waits for the
                        # operator rather than counting a cycle nobody ran.
                        continue
                    outcome = self._cycle_outcome(current)
                    if outcome is None:
                        continue
                else:
                    outcome = "success" if status == "COMPLETED" else "failed"
                probe_registry.record_probe_cycle(
                    self.conn, probe_id, current, outcome
                )
                # record_probe_cycle wrote next_cycle_at and closed the cycle.
                # Starting another run in this same tick would erase the
                # cadence and the backoff.
                continue
            try:
                view, reused = create_run(
                    self.conn,
                    CreateRequest(objective=row["objective"]),
                    probe_id=probe_id,
                    now=now,
                )
            except sqlite3.IntegrityError:
                # A concurrent engine process won the open-cycle slot. Treat
                # it as already started: re-read the open run next tick.
                continue
            if reused:
                continue
            self.conn.execute(
                "UPDATE probes SET current_run_id = ? WHERE probe_id = ?",
                (view.run_id, probe_id),
            )
            try:
                configuration = json.loads(row["configuration"] or "{}")
            except (ValueError, TypeError):
                configuration = {}
            interval_seconds = int(
                configuration.get("cycle_interval_seconds", 300) or 300
            )
            maximum_backoff_seconds = int(
                configuration.get("maximum_backoff_seconds", 3600) or 3600
            )
            multiplier = int(row["backoff_multiplier"] or 1)
            delay_seconds = min(
                interval_seconds * multiplier, maximum_backoff_seconds
            )
            next_cycle_at = now + timedelta(seconds=delay_seconds)
            self.conn.execute(
                "UPDATE probes SET next_cycle_at = ?, updated_at = ? "
                "WHERE probe_id = ?",
                (next_cycle_at.isoformat(), now.isoformat(), probe_id),
            )
            started += 1
        return started

    def _cycle_outcome(self, run_id: str) -> str | None:
        """What a probe's run ended as, or None while it can still run.

        Run.status is operator state, so it never becomes COMPLETED by itself.
        The execution ends when the run has no job left that can run: every
        pipeline stage succeeded is a successful cycle; a dead job that names
        the gate is a blocked cycle, not a failure, so the probe keeps its
        cadence and the promotion stays refused.
        """
        from product_hunter.registry import PIPELINE_STAGES

        rows = self.conn.execute(
            "SELECT stage, status, last_error FROM jobs WHERE run_id = ?",
            (run_id,),
        ).fetchall()
        if not rows:
            return None
        if any(str(row["status"]) in ("queued", "leased") for row in rows):
            return None
        done = {str(row["stage"]): str(row["status"]) for row in rows}
        if all(done.get(stage) == "succeeded" for stage in PIPELINE_STAGES):
            return "success"
        if any(
            "block" in str(row["last_error"] or "").lower() for row in rows
        ):
            return "blocked"
        return "failed"

    def _run_watchdog(self, now: datetime) -> int:
        """Name a probe that has stopped moving, once per reporting window.

        A probe that dies is silent, so this runs on a short interval of its
        own and records the stall where both the operator and the AI read it.
        """
        limits = thresholds_for(self.config.probe_watchdog)
        interval = min(60, limits.report_interval_seconds)
        if not self._due(self._last_watchdog, interval, now):
            return 0
        self._last_watchdog = now
        stalls = report_stalls(
            self.conn, thresholds=limits, now=now,
            recovery_grace_seconds=self.config.recovery_interval_seconds,
        )
        return len(stalls)

    def _run_maintenance(self, now: datetime) -> None:
        """Do the upkeep that is due."""
        interval = self._upkeep_interval
        if interval is None:
            # The cadence file turns the upkeep off.
            return
        if not self._due(self._last_maintenance, interval, now):
            return
        self._last_maintenance = now
        run_maintenance(self.conn, now=now)

    def _next_job(self, now: datetime) -> tuple[Job | None, int, int]:
        """The job to run, deferring the ones that may not run yet.

        Two kinds of job wait. One names a source that is not due, and one
        names a stage that has no handler. Both are pushed back with a later
        time, and neither burns an attempt: the work did not fail, it is
        waiting.
        """
        aging = self.config.priority_aging
        deferred = 0
        blocked = 0
        reported: set[str] = set()
        # Every pass either returns a job or moves one due job out of the due
        # set, so the number of due jobs bounds the passes. A fixed cap let a
        # batch of unhandled-stage jobs starve the runnable work behind them.
        due = self.conn.execute(
            "SELECT COUNT(*) FROM jobs "
            "WHERE status = 'queued' AND scheduled_for <= ?",
            (now.isoformat(),),
        ).fetchone()[0]
        for _ in range(max(_MAX_DEFERRALS, int(due) + 1)):
            job = peek_job(
                self.conn,
                now=now,
                aging_seconds=aging.interval_seconds,
                aging_cap=aging.max_boost,
            )
            if job is None:
                return None, deferred, blocked
            stage = str(job.stage or "")
            if stage not in self.handlers:
                defer_queued_job(
                    self.conn,
                    job.id,
                    delay_seconds=self.config.unhandled_stage_delay_seconds,
                    now=now,
                )
                blocked += 1
                if stage not in reported:
                    # One event per stage per cycle, so a queue full of jobs
                    # for the same stage does not fill the log.
                    reported.add(stage)
                    self._record_event(
                        "job_blocked", {"stage": stage, "job_id": job.id}
                    )
                continue
            source_id = str(job.payload.get("source_id") or "")
            if not source_id:
                return job, deferred, blocked
            wait = self.pacer.seconds_until_allowed(source_id, now=now)
            if wait <= 0:
                return job, deferred, blocked
            defer_queued_job(self.conn, job.id, delay_seconds=wait, now=now)
            deferred += 1
        return None, deferred, blocked

    def _charge_wall_time(self, step: OperationStep) -> OperationStep:
        """Charge the wall time the cycle took.

        An operation that sat there for an hour spent an hour, whether it ran
        a job or not. The spend goes to the ledger, so a restart picks it up
        instead of handing the operation a fresh allowance.
        """
        ended = utc_now(self.clock())
        last = self._last_charge
        self._last_charge = ended
        if last is None:
            return step
        elapsed = (ended - last).total_seconds()
        if elapsed <= 0:
            return step
        try:
            self.budget.record(BudgetDimension.wall_time_seconds, elapsed)
        except BudgetExceeded as exc:
            if step.state in TERMINAL_STATES:
                # The cycle already ended the operation for a better reason.
                return step
            # The budget running out is a completion and not a failure.
            return self._finish(
                OperationState.budget_exhausted, exc.dimension, step
            )
        return step

    def _record_outcome(
        self, outcome, job: Job, now: datetime
    ) -> tuple[int, int, OperationState | None]:
        """Count the outcome, tell the health row, and read the failure.

        The state that comes back is the one that stops the operation. A
        failure that may be tried again returns nothing, because the
        operation goes on.

        The counters, the event and the last failure are one cycle's
        bookkeeping, so they are written in one transaction. The job itself
        ran before this, in the runtime's own transaction, and is not part of
        it.
        """
        with transaction(self.conn):
            source_id = str(job.payload.get("source_id") or "")
            if source_id:
                self.pacer.mark_used(source_id, now=now)
            self.health.beat(job_id=job.id, stage=job.stage, now=now)
            if outcome.status == "succeeded":
                self._add_counter("jobs_completed", 1)
                self.health.count("jobs_completed", now=now)
                self._record_event(
                    "job_completed", {"job_id": job.id, "stage": job.stage}
                )
                return 1, 0, None
            if outcome.status != "failed":
                # A lease that was lost is not a failure of the work: the job
                # is left to the next worker.
                return 0, 0, None
            self._add_counter("jobs_failed", 1)
            self.health.count("jobs_failed", now=now)
            if job.status == "dead":
                self._add_counter("jobs_dead", 1)
            self.health.record_error(outcome.error or "", now=now)
            self._record_event(
                "job_failed",
                {
                    "job_id": job.id,
                    "stage": job.stage,
                    "error": outcome.error or "",
                },
            )
            state = classify_failure(outcome.error)
            self._set_last_failure(state)
            if state in (OperationState.permanent_failure, OperationState.blocked):
                return 0, 1, state
            # A failure that may be tried again is a failed condition and not
            # an end, so the operation carries on with the next job.
            return 0, 1, None

    # --- the operation row ------------------------------------------------

    def _set_state(self, state: OperationState, reason: str) -> None:
        """Write the state of the operation, and the reason for it."""
        now_iso = utc_now(self.clock()).isoformat()
        ended = now_iso if state in TERMINAL_STATES else None
        self.conn.execute(
            """
            INSERT INTO operation_runs (
                operation_id, mode, state, reason, started_at, updated_at, ended_at
            ) VALUES (?, 'continuous', ?, ?, ?, ?, ?)
            ON CONFLICT(operation_id) DO UPDATE SET
                state = excluded.state,
                reason = excluded.reason,
                updated_at = excluded.updated_at,
                ended_at = excluded.ended_at
            """,
            (
                self.operation_id,
                state.value,
                reason or None,
                now_iso,
                now_iso,
                ended,
            ),
        )

    def _state(self) -> OperationState:
        """The state the operation is in."""
        row = self.conn.execute(
            "SELECT state FROM operation_runs WHERE operation_id = ?",
            (self.operation_id,),
        ).fetchone()
        if row is None:
            return OperationState.running
        try:
            return OperationState(str(row["state"]))
        except ValueError:
            return OperationState.running

    def _reason(self) -> str:
        """The reason the operation last gave for its state."""
        row = self.conn.execute(
            "SELECT reason FROM operation_runs WHERE operation_id = ?",
            (self.operation_id,),
        ).fetchone()
        return "" if row is None else str(row["reason"] or "")

    def _set_last_failure(self, state: OperationState) -> None:
        """Name the last failure on the operation row.

        A failure that may be tried again is named there as well: the
        operation carries on, and the row says what went wrong last.
        """
        self.conn.execute(
            "UPDATE operation_runs SET last_failure = ?, updated_at = ? "
            "WHERE operation_id = ?",
            (
                state.value,
                utc_now(self.clock()).isoformat(),
                self.operation_id,
            ),
        )

    def _add_counter(self, name: str, amount: int) -> None:
        """Add to a counter of the operation row."""
        if name not in _COUNTERS:
            raise ValueError(f"unknown operation counter {name!r}")
        self.conn.execute(
            f"UPDATE operation_runs SET {name} = {name} + ? WHERE operation_id = ?",
            (amount, self.operation_id),
        )

    def _record_error_event(self, error: str) -> None:
        """Write the error of a cycle that raised.

        A store that will not take the event is broken in its own right, and
        the failure of that write must not hide the error it was written for.
        """
        try:
            self._record_event("operation_error", {"error": error})
        except sqlite3.Error:
            pass

    def _record_event(self, event_type: str, detail: Mapping[str, object]) -> None:
        """Append one transition to the operation's log."""
        self._events += 1
        now_iso = utc_now(self.clock()).isoformat()
        event_id = _hash_id(
            "ev_", f"{self.operation_id}|{event_type}|{now_iso}|{self._events}"
        )
        self.conn.execute(
            """
            INSERT INTO operation_events (
                event_id, operation_id, event_type, detail_json, created_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                event_id,
                self.operation_id,
                event_type,
                json.dumps(dict(detail), sort_keys=True),
                now_iso,
            ),
        )

    def _finish(
        self, state: OperationState, reason: str, step: OperationStep
    ) -> OperationStep:
        """Move the operation to a state it does not leave.

        The state and the event that says so are written in one transaction,
        so a crash between them cannot leave the row and the log telling
        different stories.
        """
        with transaction(self.conn):
            self._set_state(state, reason)
            self._record_event(f"operation_{state.value}", {"reason": reason})
        return replace(step, state=state, reason=reason)

    # --- the settings -----------------------------------------------------

    def _build_runtime(self) -> WorkerRuntime:
        """The worker runtime the engine claims the jobs with."""
        aging = self.config.priority_aging
        return WorkerRuntime(
            self.conn,
            self.identity,
            self.handlers,
            lease_seconds=self.config.lease_seconds,
            heartbeat_seconds=self.config.heartbeat_seconds,
            poll_seconds=self.config.poll_seconds,
            aging_seconds=aging.interval_seconds,
            aging_cap=aging.max_boost,
            events=self.events,
            connection_factory=self._connection_factory,
            clock=self.clock,
        )

    def _source_intervals(self) -> dict[str, int]:
        """The interval each source may be contacted at.

        The registry holds the source's own rate limit, and the autonomy
        config overrides it. A source the registry does not name keeps the
        config's default interval.
        """
        intervals = {
            source.source_id: int(math.ceil(source.rate_limit.min_interval_seconds))
            for source in load_registry().list()
        }
        intervals.update(
            {
                str(key): int(value)
                for key, value in self.config.source_pacing.per_source.items()
            }
        )
        return intervals

    @property
    def _upkeep_interval(self) -> float | None:
        """How often the upkeep runs, or None when it does not run at all.

        The upkeep beat of the cadence file holds the interval, so the number
        lives in the file and not in the code. A beat that is disabled means
        the operator turned the upkeep off, and a file without the beat falls
        back to the recovery interval, because the upkeep is then as rare as
        the recovery.
        """
        beat = upkeep_beat(self.cadence)
        if beat is None:
            return float(self.config.recovery_interval_seconds)
        if not beat.enabled:
            return None
        return float(beat.interval_seconds)

    def _due(self, last: datetime | None, interval: float, now: datetime) -> bool:
        """Whether a periodic job is due."""
        if last is None:
            return True
        return (now - last).total_seconds() >= interval

    def _stopping(self, stop_event: threading.Event | None) -> bool:
        """Whether a stop was asked for, by anybody."""
        if self._stop.is_set():
            return True
        if stop_event is not None and stop_event.is_set():
            return True
        return self._runtime is not None and self._runtime.stop_event.is_set()


# The transaction helper moved to product_hunter.db.connection, so the engine,
# the budget ledger and the evidence store share one copy. The engine imports
# it at the top of the module.


def operation_status(
    conn: sqlite3.Connection, *, operation_id: str = DEFAULT_OPERATION_ID
) -> dict[str, object]:
    """What the operation looks like right now.

    The row of the operation, the depth of the queue per band, the dead
    letters, the spend per dimension and the health of the workers. It is
    read straight from the store, so an operator sees the state of a process
    that is not running.
    """
    row = conn.execute(
        "SELECT * FROM operation_runs WHERE operation_id = ?", (operation_id,)
    ).fetchone()
    if row is None:
        operation: dict[str, object] = {
            "operation_id": operation_id,
            "state": OperationState.running.value,
            "reason": None,
        }
    else:
        operation = dict(row)
    return {
        "operation": operation,
        "queue_depth": _queue_depth(conn),
        "dead_letters": _dead_letters(conn),
        "budget": _budget_spend(conn, operation_id),
        "workers": [worker.to_dict() for worker in list_worker_health(conn)],
    }


def _queue_depth(conn: sqlite3.Connection) -> dict[str, int]:
    """The queued jobs per priority band."""
    depth: dict[str, int] = {band.name: 0 for band in Priority}
    depth["other"] = 0
    for row in conn.execute(
        "SELECT priority, count(*) AS n FROM jobs "
        "WHERE status = 'queued' GROUP BY priority"
    ):
        name = _band_name(int(row["priority"]))
        depth[name] = depth.get(name, 0) + int(row["n"])
    return depth


def _band_name(priority: int) -> str:
    """The band a priority belongs to, or other when it belongs to none."""
    for band in Priority:
        if int(band) == priority:
            return band.name
    return "other"


def _dead_letters(conn: sqlite3.Connection) -> int:
    """The jobs that ran out of attempts."""
    row = conn.execute("SELECT count(*) FROM jobs WHERE status = 'dead'").fetchone()
    return int(row[0])


def _budget_spend(
    conn: sqlite3.Connection, operation_id: str
) -> dict[str, object]:
    """The spend of the operation, per scope and per dimension."""
    operation: dict[str, dict[str, float]] = {}
    stage: dict[str, dict[str, float]] = {}
    source: dict[str, dict[str, float]] = {}
    for row in conn.execute(
        "SELECT scope, dimension, spent FROM operation_budget "
        "WHERE operation_id = ? ORDER BY scope, dimension",
        (operation_id,),
    ):
        scope = str(row["scope"])
        dimension = str(row["dimension"])
        amount = float(row["spent"])
        if scope.startswith(STAGE_SCOPE_PREFIX):
            stage.setdefault(scope[len(STAGE_SCOPE_PREFIX) :], {})[dimension] = amount
        elif scope.startswith(SOURCE_SCOPE_PREFIX):
            source.setdefault(scope[len(SOURCE_SCOPE_PREFIX) :], {})[dimension] = amount
        else:
            operation.setdefault(scope, {})[dimension] = amount
    return {"operation": operation, "stage": stage, "source": source}


def _missed_occurrences(tick: ScheduleTick, now: datetime) -> int:
    """The occurrences whose moment had passed when the job was created.

    A beat that came while nothing was running is work that was missed, and
    the operation says so instead of pretending the schedule kept up.
    """
    missed = 0
    for job in tick.created:
        occurrence = str(job.payload.get("occurrence") or "")
        if not occurrence:
            continue
        try:
            moment = datetime.fromisoformat(occurrence)
        except ValueError:
            continue
        if moment < now:
            missed += 1
    return missed


def _hash_id(prefix: str, basis: str) -> str:
    """A deterministic id from a basis."""
    return prefix + hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16]

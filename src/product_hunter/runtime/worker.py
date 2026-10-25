"""The supervised worker runtime: claim, lease, dispatch, record, shut down.

The runtime owns the process level behaviour the database layer deliberately
leaves out. It renews the lease of a running handler, it stops claiming on a
stop request, it reports every transition as a structured event, and it never
writes a job whose lease it no longer holds.
"""

from __future__ import annotations

import functools
import importlib
import signal
import sqlite3
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from threading import Event, Thread
from typing import Any

from product_hunter import redaction
from product_hunter.db.connection import (
    BUSY_ATTEMPTS,
    BUSY_DELAY_SECONDS,
    connect,
    is_busy,
    with_busy_retry,
)
from product_hunter.db.jobs import (
    Job,
    JobContext,
    JobOutcome,
    LeaseLost,
    claim_job,
    complete_job,
    fail_job,
    recover_expired_leases,
    renew_lease,
)
from product_hunter.db.research_gate import evaluate_stage_gate, load_run_evidence
from product_hunter.fetch import utcnow
from product_hunter.research.contract import ResearchContract, load_research_contract
from product_hunter.runtime.events import EventSink, JsonlEventSink, WorkerEvent
from product_hunter.runtime.identity import WorkerIdentity

from product_hunter.registry import advance_run_progress

Handler = Callable[[JobContext], dict[str, object] | None]

# A renewal that is still running when the outcome is written would race the
# completion, so the heartbeat is joined with a bound and the job is left
# alone when the bound is reached.
_HEARTBEAT_JOIN_TIMEOUT = 30.0

# A handler can raise an exception whose message carries page text, model
# output or a credential, so the failure text is masked and cut before it
# reaches the job row or the event stream. The rule lives in one place:
# product_hunter.redaction, which the control plane uses as well.


@dataclass(frozen=True)
class RetryPolicy:
    """How long a failed job waits before it is offered again."""

    base_seconds: float = 5.0
    multiplier: float = 2.0
    max_seconds: float = 300.0

    def delay_for(self, attempt: int) -> int:
        """The backoff in whole seconds for the given attempt number."""
        delay = self.base_seconds * (self.multiplier ** (attempt - 1))
        return int(min(delay, self.max_seconds))


@functools.lru_cache(maxsize=1)
def _research_contract() -> ResearchContract:
    """Load the research contract once per process.

    One YAML read is enough; the gate step runs for every gated job, so the
    contract is cached instead of re-read per job.
    """
    return load_research_contract()


class WorkerRuntime:
    """One supervised worker: it claims jobs, runs handlers and records outcomes."""

    def __init__(
        self,
        conn: sqlite3.Connection,
        identity: WorkerIdentity,
        handlers: Mapping[str, Handler],
        *,
        lease_seconds: int = 60,
        heartbeat_seconds: float = 15.0,
        poll_seconds: float = 1.0,
        aging_seconds: int = 0,
        aging_cap: int = 0,
        retry_policy: RetryPolicy | None = None,
        events: EventSink | None = None,
        connection_factory: Callable[[], sqlite3.Connection] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if lease_seconds < 1:
            raise ValueError("lease_seconds must be at least 1")
        if heartbeat_seconds < 0:
            raise ValueError("heartbeat_seconds must not be negative")
        if heartbeat_seconds > 0 and heartbeat_seconds >= lease_seconds:
            # The first renewal would land on or after the expiry.
            raise ValueError(
                f"heartbeat_seconds ({heartbeat_seconds}) must be smaller than "
                f"lease_seconds ({lease_seconds})"
            )
        if poll_seconds <= 0:
            raise ValueError("poll_seconds must be positive")
        if aging_seconds < 0:
            raise ValueError("aging_seconds must not be negative")
        if aging_cap < 0:
            raise ValueError("aging_cap must not be negative")
        self.conn = conn
        self.identity = identity
        self.handlers = dict(handlers)
        self.lease_seconds = lease_seconds
        self.heartbeat_seconds = heartbeat_seconds
        self.poll_seconds = poll_seconds
        self.aging_seconds = aging_seconds
        self.aging_cap = aging_cap
        self.retry_policy = retry_policy if retry_policy is not None else RetryPolicy()
        self.events = events if events is not None else JsonlEventSink(sys.stderr)
        self.clock = clock if clock is not None else utcnow
        self.stop_event = Event()
        self._connection_factory = connection_factory
        if connection_factory is None and heartbeat_seconds > 0:
            path = _database_file(conn)
            if not path:
                raise ValueError(
                    "a heartbeat needs a second connection, so it needs a file "
                    "backed database; pass connection_factory instead"
                )
            self._connection_factory = lambda: connect(path)
        self._heartbeat_stop: Event | None = None
        self._heartbeat_thread: Thread | None = None
        self._heartbeat_lost = Event()

    def recover(self) -> int:
        """Requeue the jobs whose lease expired. Returns how many were touched."""
        count = recover_expired_leases(self.conn, now=self.clock())
        self._emit("lease_recovered", count=count)
        return count

    def run_once(self) -> JobOutcome | None:
        """Claim one job, run it and record the outcome.

        Returns None when nothing was claimed. With no handler registered the
        runtime claims nothing, so a worker started before the handlers exist
        cannot burn the queue.
        """
        if not self.handlers:
            return None
        job = with_busy_retry(
            lambda: claim_job(
                self.conn,
                self.identity.worker_id,
                lease_seconds=self.lease_seconds,
                now=self.clock(),
                aging_seconds=self.aging_seconds,
                aging_cap=self.aging_cap,
            )
        )
        if job is None:
            return None
        self._emit(
            "job_claimed",
            job_id=job.id,
            stage=job.stage,
            attempt=job.attempts,
            lease_epoch=job.lease_epoch,
        )
        # Mark where the run stands now that a worker has claimed this job.
        try:
            completed_stages = int(
                self.conn.execute(
                    "SELECT count(DISTINCT stage) FROM stage_attempts "
                    "WHERE run_id = ? AND status = 'succeeded'",
                    (job.run_id,),
                ).fetchone()[0]
                or 0
            )
        except sqlite3.Error:
            completed_stages = 0
        advance_run_progress(
            self.conn,
            run_id=job.run_id,
            stage=job.stage,
            completed_stages=completed_stages,
            now=self.clock(),
        )
        handler = self.handlers.get(job.stage)
        if handler is None:
            self._emit(
                "handler_missing",
                job_id=job.id,
                stage=job.stage,
                attempt=job.attempts,
                lease_epoch=job.lease_epoch,
            )
            return self._record_failure(job, f"no handler for stage {job.stage!r}")
        context = JobContext(
            self.conn,
            job,
            worker_id=self.identity.worker_id,
            lease_seconds=self.lease_seconds,
        )
        started = self.clock()
        self._start_heartbeat(job)
        try:
            result = handler(context)
            if result is not None and not isinstance(result, dict):
                raise TypeError("a job handler must return a dict or None")
        except LeaseLost as exc:
            self._stop_heartbeat()
            return self._lease_lost(job, error=_safe_error(exc))
        except Exception as exc:
            self._stop_heartbeat()
            return self._record_failure(job, _safe_error(exc))
        except BaseException:
            # A handler that leaves the runtime must not keep the lease alive.
            self._stop_heartbeat()
            raise
        if not self._stop_heartbeat() or self._heartbeat_lost.is_set():
            return self._lease_lost(job)
        try:
            result = self._evaluate_stage_gate(job, result)
        except LeaseLost as exc:
            return self._lease_lost(job, error=_safe_error(exc))
        except Exception as exc:
            return self._record_failure(job, _safe_error(exc))
        try:
            completed = with_busy_retry(
                lambda: complete_job(
                    self.conn,
                    job,
                    worker_id=self.identity.worker_id,
                    lease_epoch=job.lease_epoch,
                    result=result,
                    now=self.clock(),
                )
            )
            # Advance the run's execution view: current stage and derived
            # progress. This is separate from operator state and never
            # overwrites QUEUED/RUNNING/etc.
            try:
                completed_stages = int(
                    self.conn.execute(
                        "SELECT count(DISTINCT stage) FROM stage_attempts "
                        "WHERE run_id = ? AND status = 'succeeded'",
                        (job.run_id,),
                    ).fetchone()[0]
                    or 0
                )
            except sqlite3.Error:
                completed_stages = 0
            advance_run_progress(
                self.conn,
                run_id=job.run_id,
                stage=job.stage,
                completed_stages=completed_stages,
                now=self.clock(),
            )
        except LeaseLost as exc:
            return self._lease_lost(job, error=_safe_error(exc))
        except sqlite3.Error as exc:
            # The outcome could not be written, so nothing more is written for
            # this job and the next worker picks it up.
            self._emit(
                "job_outcome_failed",
                job_id=job.id,
                stage=job.stage,
                attempt=job.attempts,
                lease_epoch=job.lease_epoch,
                error=_safe_error(exc),
            )
            return JobOutcome(job.id, "lease_lost")
        self._emit(
            "job_succeeded",
            job_id=job.id,
            stage=job.stage,
            attempt=job.attempts,
            lease_epoch=job.lease_epoch,
            status="succeeded",
            duration_ms=_duration_ms(started, self.clock()),
        )
        return JobOutcome(job.id, "succeeded", result=completed.result)

    def _evaluate_stage_gate(
        self, job: Job, result: dict[str, object] | None
    ) -> dict[str, object] | None:
        """Evaluate and persist the stage gate for a completed job.

        Returns the job result with ``stage_gate`` merged in, or the unchanged
        result when the stage has no contract gate. A gate that cannot be
        computed raises, so the caller routes it to the failure path.
        """
        from product_hunter.handlers import gate_stage_for

        if job.run_id is None:
            return result
        contract_stage = gate_stage_for(job.stage)
        if contract_stage is None:
            return result
        contract = _research_contract()
        evidence = load_run_evidence(
            self.conn,
            run_id=job.run_id,
            contract_version=contract.contract_version,
        )
        gate_result = evaluate_stage_gate(
            self.conn,
            contract,
            evidence,
            contract_stage,
            worker_id=self.identity.worker_id,
            lease_epoch=job.lease_epoch,
            lease_stage=job.stage,
        )
        stage_gate = {
            "stage": contract_stage,
            "passed": gate_result.passed,
            "violations": [
                violation.model_dump(mode="json") for violation in gate_result.violations
            ],
        }
        if result is None:
            return {"stage_gate": stage_gate}
        return {**result, "stage_gate": stage_gate}

    def run_forever(self, stop_event: Event | None = None) -> None:
        """Claim and run jobs until a stop is requested.

        The stop request ends the claim loop, never the handler that is
        already running. A database error is reported and the loop keeps
        polling, because a busy file must not end the supervised process.
        """
        self._emit("worker_started")
        if not self.handlers:
            self._emit("no_handlers")
        try:
            self.recover()
        except sqlite3.Error as exc:
            self._emit("worker_error", error=_safe_error(exc))
        while not self.stop_event.is_set() and not _is_set(stop_event):
            try:
                outcome = self.run_once()
            except sqlite3.Error as exc:
                self._emit("worker_error", error=_safe_error(exc))
                outcome = None
            if outcome is None:
                # Waiting on the internal event keeps a stop request prompt.
                self.stop_event.wait(self.poll_seconds)
        self._emit("worker_stopped")

    def request_stop(self) -> None:
        """Ask the runtime to stop claiming new jobs."""
        if not self.stop_event.is_set():
            self._emit("shutdown_requested")
        self.stop_event.set()

    def _record_failure(self, job: Job, error: str) -> JobOutcome:
        """Record a failed attempt. The database decides requeue or dead."""
        try:
            failed = with_busy_retry(
                lambda: fail_job(
                    self.conn,
                    job,
                    worker_id=self.identity.worker_id,
                    lease_epoch=job.lease_epoch,
                    error=error,
                    backoff_seconds=self.retry_policy.delay_for(job.attempts),
                    now=self.clock(),
                )
            )
        except LeaseLost as exc:
            return self._lease_lost(job, error=_safe_error(exc))
        except sqlite3.Error as exc:
            self._emit(
                "job_outcome_failed",
                job_id=job.id,
                stage=job.stage,
                attempt=job.attempts,
                lease_epoch=job.lease_epoch,
                error=_safe_error(exc),
            )
            return JobOutcome(job.id, "lease_lost")
        self._emit(
            "job_failed",
            job_id=job.id,
            stage=job.stage,
            attempt=job.attempts,
            lease_epoch=job.lease_epoch,
            status=failed.status,
            error=error,
        )
        if failed.status == "dead":
            self._emit(
                "job_dead",
                job_id=job.id,
                stage=job.stage,
                attempt=job.attempts,
                lease_epoch=job.lease_epoch,
                status="dead",
                error=error,
            )
        return JobOutcome(job.id, "failed", error=failed.last_error)

    def _start_heartbeat(self, job: Job) -> None:
        """Start the renewal thread for the job that is about to run."""
        self._heartbeat_lost = Event()
        factory = self._connection_factory
        if factory is None or self.heartbeat_seconds <= 0:
            return
        stop = Event()
        thread = Thread(
            target=self._heartbeat_loop,
            args=(job, stop),
            name=f"heartbeat-{job.id}",
            daemon=True,
        )
        self._heartbeat_stop = stop
        self._heartbeat_thread = thread
        thread.start()

    def _stop_heartbeat(self) -> bool:
        """Stop the renewal thread. Returns False when it did not stop in time."""
        stop = self._heartbeat_stop
        thread = self._heartbeat_thread
        self._heartbeat_stop = None
        self._heartbeat_thread = None
        if stop is None or thread is None:
            return True
        stop.set()
        thread.join(timeout=_HEARTBEAT_JOIN_TIMEOUT)
        return not thread.is_alive()

    def _heartbeat_loop(self, job: Job, stop: Event) -> None:
        """Renew the lease of the running job until the handler is done."""
        factory = self._connection_factory
        if factory is None:  # pragma: no cover - the caller guarantees one
            return
        conn = factory()
        try:
            while not stop.wait(self.heartbeat_seconds):
                if not self._renew(job, conn, stop):
                    return
                self._emit(
                    "lease_renewed",
                    job_id=job.id,
                    stage=job.stage,
                    attempt=job.attempts,
                    lease_epoch=job.lease_epoch,
                )
        finally:
            conn.close()

    def _renew(self, job: Job, conn: sqlite3.Connection, stop: Event) -> bool:
        """Renew the lease of the running job. Returns False when it must stop.

        A busy file is transient, so the renewal is tried again and every
        failed attempt is reported. A renewal that keeps failing means the
        lease is no longer safe to keep, so the runtime refuses the outcome.
        """
        for attempt in range(BUSY_ATTEMPTS):
            try:
                renew_lease(
                    conn,
                    job,
                    worker_id=self.identity.worker_id,
                    lease_epoch=job.lease_epoch,
                    lease_seconds=self.lease_seconds,
                    now=self.clock(),
                )
                return True
            except LeaseLost as exc:
                self._heartbeat_lost.set()
                self._emit(
                    "lease_renewal_failed",
                    job_id=job.id,
                    stage=job.stage,
                    attempt=job.attempts,
                    lease_epoch=job.lease_epoch,
                    error=_safe_error(exc),
                )
                return False
            except sqlite3.Error as exc:
                self._emit(
                    "lease_renewal_failed",
                    job_id=job.id,
                    stage=job.stage,
                    attempt=job.attempts,
                    lease_epoch=job.lease_epoch,
                    error=_safe_error(exc),
                )
                if not is_busy(exc) or attempt == BUSY_ATTEMPTS - 1:
                    self._heartbeat_lost.set()
                    return False
                if stop.wait(BUSY_DELAY_SECONDS):
                    return False
        return False

    def _lease_lost(self, job: Job, *, error: str | None = None) -> JobOutcome:
        """Report a lost claim. The job is left to the next worker."""
        self._emit(
            "job_lease_lost",
            job_id=job.id,
            stage=job.stage,
            attempt=job.attempts,
            lease_epoch=job.lease_epoch,
            error=error,
        )
        return JobOutcome(job.id, "lease_lost")

    def _emit(
        self,
        event: str,
        *,
        job_id: str | None = None,
        stage: str | None = None,
        attempt: int | None = None,
        lease_epoch: int | None = None,
        status: str | None = None,
        duration_ms: int | None = None,
        error: str | None = None,
        count: int | None = None,
    ) -> None:
        self.events.emit(
            WorkerEvent(
                event=event,
                worker_id=self.identity.worker_id,
                ts=self.clock().isoformat(),
                job_id=job_id,
                stage=stage,
                attempt=attempt,
                lease_epoch=lease_epoch,
                status=status,
                duration_ms=duration_ms,
                error=error,
                count=count,
            )
        )


def load_handlers(spec: str | None) -> dict[str, Handler]:
    """The handler mapping a ``module:attribute`` spec names.

    The spec is operator configuration: it names a module the operator chose
    and the attribute that builds the mapping. Both entry points read the same
    environment variable, so the parsing lives with the worker that runs the
    handlers.
    """
    if not spec or not spec.strip():
        return {}
    module_name, _, attribute = spec.strip().partition(":")
    if not module_name or not attribute:
        raise ValueError("PH_WORKER_HANDLERS must be 'module:attribute'")
    module = importlib.import_module(module_name)
    factory = getattr(module, attribute)
    return dict(factory())


def install_shutdown_handlers(runtime: WorkerRuntime) -> Callable[[], None]:
    """Route SIGINT and SIGTERM to the runtime stop request.

    Returns a function that puts the previous handlers back. Outside the main
    thread the handlers cannot be installed, so the uninstaller does nothing.
    """
    previous: dict[int, Any] = {}

    def _request_stop(_signum: int, _frame: object) -> None:
        runtime.request_stop()

    try:
        for sig in (signal.SIGINT, signal.SIGTERM):
            previous[sig] = signal.getsignal(sig)
            signal.signal(sig, _request_stop)
    except (OSError, ValueError):
        _restore(previous)
        return lambda: None
    return lambda: _restore(previous)


def _restore(previous: dict[int, Any]) -> None:
    for sig, handler in previous.items():
        try:
            signal.signal(sig, handler)
        except (OSError, ValueError):  # pragma: no cover - the thread moved on
            pass


def _safe_error(exc: BaseException) -> str:
    """The failure text, without the labelled secrets and without the bulk.

    A handler can raise an exception whose message carries page text or a
    credential. The type always stays. The message is masked where it names a
    secret and cut where it runs long. The mask is best effort.
    """
    return redaction.safe_error(exc)


def _is_set(event: Event | None) -> bool:
    return event is not None and event.is_set()


def _database_file(conn: sqlite3.Connection) -> str:
    """The file behind a connection. Empty for an in-memory database."""
    row = conn.execute("PRAGMA database_list").fetchone()
    if row is None:
        return ""
    return str(row[2] or "")


def _duration_ms(started: datetime, ended: datetime) -> int:
    return int((ended - started).total_seconds() * 1000)

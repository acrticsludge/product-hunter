"""Deterministic scheduler above the SQLite job queue.

The scheduler derives the work that exists from a fixed UTC grid, writes it
through ``enqueue_job`` and reports what it did. It never runs anything: the
worker claims and runs the jobs the scheduler writes, and the scheduler owns
the priority vocabulary and the backoff policy.
"""

from __future__ import annotations

import copy
import json
import logging
import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import IntEnum

from product_hunter.db.connection import utc_now
from product_hunter.db.jobs import Job, JobConflict, enqueue_job, get_job, list_jobs

_ORIGIN = datetime(1970, 1, 1, tzinfo=timezone.utc)
_MAX_PAYLOAD_BYTES = 65_536
_MAX_OCCURRENCES = 10_000
_BACKOFF_BASE_SECONDS = 30
_BACKOFF_FACTOR = 2
_BACKOFF_MAX_SECONDS = 3600
# The largest exponent the backoff ladder uses. Thirty times 2**31 is past
# the one hour cap anyway, so the ladder does not change for any attempt.
_BACKOFF_MAX_EXPONENT = 32
# The range SQLite stores an integer in.
_INT64_MIN = -(2**63)
_INT64_MAX = 2**63 - 1
# The seconds between the earliest and the latest instant a datetime holds.
_DATETIME_SPAN_SECONDS = int((datetime.max - datetime.min).total_seconds())

logger = logging.getLogger(__name__)


class Priority(IntEnum):
    """Job priority bands. A larger number is served first."""

    P0 = 50  # user-requested continuation
    P1 = 40  # high-potential investigation
    P2 = 30  # scheduled validation
    P3 = 20  # background discovery
    P4 = 10  # maintenance and reprocessing


class ScheduleError(Exception):
    """A schedule or a scheduling request was refused."""


@dataclass(frozen=True)
class Schedule:
    """One recurring definition. The occurrence time identifies its work."""

    name: str
    stage: str
    interval_seconds: int
    priority: int = int(Priority.P3)
    payload: Mapping[str, object] = field(default_factory=dict)
    max_attempts: int = 3
    offset_seconds: int = 0
    source_id: str | None = None
    run_id: str | None = None
    catchup_limit: int | None = None
    enabled: bool = True

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ScheduleError("a schedule needs a name")
        if not self.stage.strip():
            raise ScheduleError("a schedule needs a stage")
        # Every numeric field is an integer SQLite stores as one, so a float
        # can never reach the table and be read back as another number.
        object.__setattr__(
            self,
            "interval_seconds",
            _check_int("interval_seconds", self.interval_seconds),
        )
        if self.interval_seconds < 1:
            raise ScheduleError("interval_seconds must be at least 1")
        _check_datetime_span("interval_seconds", self.interval_seconds)
        object.__setattr__(
            self, "offset_seconds", _check_int("offset_seconds", self.offset_seconds)
        )
        if self.offset_seconds < 0:
            raise ScheduleError("offset_seconds must not be negative")
        _check_datetime_span("offset_seconds", self.offset_seconds)
        object.__setattr__(
            self, "max_attempts", _check_int("max_attempts", self.max_attempts)
        )
        if self.max_attempts < 1:
            raise ScheduleError("max_attempts must be at least 1")
        # A plain int, so SQLite stores a number and not an enum member.
        object.__setattr__(self, "priority", _check_int("priority", self.priority))
        if self.catchup_limit is not None:
            object.__setattr__(
                self, "catchup_limit", _check_int("catchup_limit", self.catchup_limit)
            )
            if self.catchup_limit < 1:
                raise ScheduleError("catchup_limit must be at least 1")
        try:
            payload_copy = copy.deepcopy(dict(self.payload))
            payload = json.dumps(payload_copy, sort_keys=True)
        except (TypeError, ValueError) as exc:
            raise ScheduleError(f"payload is not JSON serialisable: {exc}") from exc
        size = len(payload.encode("utf-8"))
        if size > _MAX_PAYLOAD_BYTES:
            raise ScheduleError(
                f"payload is {size} bytes, over the {_MAX_PAYLOAD_BYTES} byte limit"
            )
        # The schedule keeps its own deep copy, so a caller that mutates the
        # mapping it passed, or a value inside it, cannot change a frozen
        # schedule.
        object.__setattr__(self, "payload", payload_copy)


@dataclass(frozen=True)
class BlockedSchedule:
    """A schedule the source gate refused, with the occurrence it refused."""

    name: str
    reason: str
    occurrence: str


@dataclass(frozen=True)
class DroppedSchedule:
    """The occurrences a cap dropped: how many, and the oldest of them."""

    name: str
    count: int
    oldest: str


@dataclass(frozen=True)
class ConflictedSchedule:
    """An occurrence whose stored job holds different content."""

    name: str
    occurrence: str
    detail: str


@dataclass(frozen=True)
class ScheduleTick:
    """What one tick did: jobs created, jobs that already existed, refusals."""

    now: str
    created: tuple[Job, ...]
    existing: tuple[Job, ...]
    blocked: tuple[BlockedSchedule, ...]
    dropped: tuple[DroppedSchedule, ...]
    conflicts: tuple[ConflictedSchedule, ...]


class Scheduler:
    """Turns schedule definitions into queued jobs on a fixed UTC grid."""

    def __init__(
        self,
        conn: sqlite3.Connection,
        schedules: Sequence[Schedule],
        *,
        lookback_seconds: int = 86400,
        max_occurrences: int = 100,
    ) -> None:
        names = [schedule.name for schedule in schedules]
        if len(set(names)) != len(names):
            raise ScheduleError("schedule names must be unique")
        lookback_seconds = _check_int("lookback_seconds", lookback_seconds)
        if lookback_seconds < 1:
            raise ScheduleError("lookback_seconds must be at least 1")
        _check_datetime_span("lookback_seconds", lookback_seconds)
        max_occurrences = _check_int("max_occurrences", max_occurrences)
        if max_occurrences < 1:
            raise ScheduleError("max_occurrences must be at least 1")
        if max_occurrences > _MAX_OCCURRENCES:
            raise ScheduleError(
                f"max_occurrences is {max_occurrences}, over the "
                f"{_MAX_OCCURRENCES} limit"
            )
        self.conn = conn
        self.schedules = list(schedules)
        self.lookback_seconds = lookback_seconds
        self.max_occurrences = max_occurrences

    # --- the tick ---------------------------------------------------------

    def tick(self, *, now: datetime | None = None) -> ScheduleTick:
        """Materialise the occurrences in the catch-up window, once each."""
        now_dt = utc_now(now)
        created: list[Job] = []
        existing: list[Job] = []
        blocked: list[BlockedSchedule] = []
        dropped: list[DroppedSchedule] = []
        conflicts: list[ConflictedSchedule] = []
        # The tick is all or nothing. A transaction the caller already opened
        # is joined with a savepoint, so a tick that raises undoes its own
        # work and leaves the caller's transaction open. A transaction the
        # tick opened is the tick's own to commit or roll back.
        joined = self.conn.in_transaction
        if joined:
            self.conn.execute("SAVEPOINT scheduler_tick")
        else:
            self.conn.execute("BEGIN IMMEDIATE")
        try:
            # Every run is checked before the first write, so a tick either
            # does all of its work or none of it.
            self._require_runs()
            for schedule in self.schedules:
                if not schedule.enabled:
                    continue
                reason = (
                    self._source_reason(schedule.source_id)
                    if schedule.source_id is not None
                    else None
                )
                if reason is not None:
                    logger.warning("schedule %s is blocked: %s", schedule.name, reason)
                try:
                    occurrences, drop = self._occurrences(schedule, now_dt)
                except OverflowError as exc:
                    # The span passed the guard but the grid does not fit on
                    # the calendar. That is a refusal, not a crash.
                    raise ScheduleError(
                        f"schedule {schedule.name!r} does not fit on the calendar"
                    ) from exc
                if drop is not None:
                    dropped.append(drop)
                    logger.warning(
                        "schedule %s dropped %d occurrences, the oldest at %s",
                        drop.name,
                        drop.count,
                        drop.oldest,
                    )
                for occurrence in occurrences:
                    if reason is not None:
                        blocked.append(
                            BlockedSchedule(
                                schedule.name, reason, occurrence.isoformat()
                            )
                        )
                        continue
                    key = f"schedule:{schedule.name}:{occurrence.isoformat()}"
                    # The lookup only classifies the job the write returns; the
                    # write itself always happens, so the queue's conflict
                    # check runs and a changed schedule is reported.
                    known = self.conn.execute(
                        "SELECT 1 FROM jobs WHERE idempotency_key = ?", (key,)
                    ).fetchone()
                    try:
                        job = enqueue_job(
                            self.conn,
                            stage=schedule.stage,
                            idempotency_key=key,
                            payload={
                                **dict(schedule.payload),
                                "schedule": schedule.name,
                                "occurrence": occurrence.isoformat(),
                            },
                            priority=schedule.priority,
                            max_attempts=schedule.max_attempts,
                            scheduled_for=occurrence.isoformat(),
                            run_id=schedule.run_id,
                            now=now_dt,
                        )
                    except JobConflict as exc:
                        # The stored job keeps its old content and stays as it
                        # is. The conflict is reported and the tick keeps
                        # going, so one stale occurrence does not stop every
                        # other schedule from getting work.
                        conflicts.append(
                            ConflictedSchedule(
                                schedule.name, occurrence.isoformat(), str(exc)
                            )
                        )
                        logger.warning(
                            "schedule %s occurrence %s conflicts with the "
                            "stored job: %s",
                            schedule.name,
                            occurrence.isoformat(),
                            exc,
                        )
                        continue
                    if known is None:
                        created.append(job)
                        continue
                    # The job was already there. The queue compares the stage
                    # and the payload; the schedule also fixes the attempt
                    # ceiling and the run, and raises the priority.
                    detail = _difference(schedule, job)
                    if detail:
                        conflicts.append(
                            ConflictedSchedule(
                                schedule.name, occurrence.isoformat(), detail
                            )
                        )
                        logger.warning(
                            "schedule %s occurrence %s does not match the "
                            "stored job: %s",
                            schedule.name,
                            occurrence.isoformat(),
                            detail,
                        )
                        continue
                    existing.append(job)
            if joined:
                self.conn.execute("RELEASE SAVEPOINT scheduler_tick")
            else:
                self.conn.execute("COMMIT")
        except BaseException:
            if joined:
                _rollback_to_savepoint(self.conn)
            else:
                _rollback(self.conn)
            raise
        return ScheduleTick(
            now=now_dt.isoformat(),
            created=tuple(created),
            existing=tuple(existing),
            blocked=tuple(blocked),
            dropped=tuple(dropped),
            conflicts=tuple(conflicts),
        )

    # --- the queue's view -------------------------------------------------

    def overdue(
        self, *, now: datetime | None = None, limit: int | None = None
    ) -> list[Job]:
        """Queued jobs whose scheduled time is strictly earlier than now."""
        picked = self._queued(utc_now(now).isoformat(), due=False)
        return picked if limit is None else picked[:limit]

    def plan(self, *, now: datetime | None = None) -> list[Job]:
        """Queued jobs a worker would claim now, in the order it claims them."""
        return self._queued(utc_now(now).isoformat(), due=True)

    def promote(self, job_id: str, priority: int = int(Priority.P0)) -> Job:
        """Raise the priority of a queued job. A leased job cannot be moved."""
        priority = _check_int("priority", priority)
        row = self.conn.execute(
            "SELECT status, priority FROM jobs WHERE id = ?", (job_id,)
        ).fetchone()
        if row is None:
            raise ScheduleError(f"job {job_id!r} does not exist")
        if row["status"] != "queued":
            raise ScheduleError(
                f"job {job_id!r} is {row['status']!r}, not queued"
            )
        current = int(row["priority"])
        if priority < current:
            # A demotion would leave the job below the priority its schedule
            # says, and every later tick would report it as a conflict.
            raise ScheduleError(
                f"job {job_id!r} has priority {current}, so priority {priority} "
                "is a demotion and is refused"
            )
        # The status and the floor are in the WHERE clause, so a job a worker
        # leased between the read and the write is not reprioritised, and two
        # promotions cannot interleave so a lower one lands after a higher
        # one.
        changed = self.conn.execute(
            "UPDATE jobs SET priority = ? WHERE id = ? AND status = 'queued' "
            "AND priority <= ?",
            (priority, job_id, current),
        ).rowcount
        if changed != 1:
            raise ScheduleError(f"job {job_id!r} is no longer queued")
        job = get_job(self.conn, job_id)
        if job is None:  # pragma: no cover - the row was just read
            raise ScheduleError(f"job {job_id!r} disappeared")
        return job

    # --- backoff and retry ------------------------------------------------

    def backoff_seconds(self, attempt: int) -> int:
        """The delay for an attempt: base * factor ** (attempt - 1), capped."""
        attempt = _check_int("attempt", attempt)
        # The exponent is capped, so an attempt as large as the signed 64-bit
        # range does not build an enormous integer. The ladder for any attempt
        # a real job reaches is unchanged.
        exponent = min(max(1, attempt) - 1, _BACKOFF_MAX_EXPONENT)
        delay = _BACKOFF_BASE_SECONDS * (_BACKOFF_FACTOR**exponent)
        return min(delay, _BACKOFF_MAX_SECONDS)

    def schedule_retry(
        self, job: Job, *, now: datetime | None = None, reason: str | None = None
    ) -> Job:
        """Queue a fresh job for the same work, one backoff from now.

        The key is the job id and the attempt, so a repeated call returns the
        same job and creates no second one. The reason describes the request,
        not the work, so it is logged and kept out of the payload the key
        identifies.
        """
        if job.status != "dead":
            raise ScheduleError(f"job {job.id!r} is {job.status!r}, not dead")
        now_dt = utc_now(now)
        attempt = job.attempts + 1
        if reason is not None:
            logger.info("retrying job %s at attempt %s: %s", job.id, attempt, reason)
        # The retry is the next attempt of the same work, so it waits that
        # rung of the ladder and keeps the run it belongs to.
        return enqueue_job(
            self.conn,
            stage=job.stage,
            idempotency_key=f"retry:{job.id}:{attempt}",
            payload={
                **job.payload,
                "retry_of": job.id,
                "retry_attempt": attempt,
            },
            priority=job.priority,
            max_attempts=job.max_attempts,
            scheduled_for=(
                now_dt + timedelta(seconds=self.backoff_seconds(attempt))
            ).isoformat(),
            run_id=job.run_id,
            now=now_dt,
        )

    # --- internals --------------------------------------------------------

    def _occurrences(
        self, schedule: Schedule, now: datetime
    ) -> tuple[list[datetime], DroppedSchedule | None]:
        """The grid points in ``(now - lookback, now]``, oldest first.

        The cap keeps the newest occurrences; the rest are reported so a
        schedule never loses work silently.
        """
        interval = timedelta(seconds=schedule.interval_seconds)
        anchor = _ORIGIN + timedelta(seconds=schedule.offset_seconds)
        first = max(
            0,
            (now - timedelta(seconds=self.lookback_seconds) - anchor) // interval + 1,
        )
        last = (now - anchor) // interval
        if last < first:
            return [], None
        cap = self.max_occurrences
        if schedule.catchup_limit is not None:
            cap = min(cap, schedule.catchup_limit)
        kept_first = max(first, last - cap + 1)
        kept = [anchor + k * interval for k in range(kept_first, last + 1)]
        dropped = kept_first - first
        if dropped == 0:
            return kept, None
        oldest = anchor + first * interval
        return kept, DroppedSchedule(schedule.name, dropped, oldest.isoformat())

    def _source_reason(self, source_id: str) -> str | None:
        """Why the gate refuses this source, or None when it may be used."""
        row = self.conn.execute(
            "SELECT enabled, terms_status FROM sources WHERE source_id = ?",
            (source_id,),
        ).fetchone()
        if row is None:
            return "unknown_source"
        if not int(row["enabled"]):
            return "source_disabled"
        if row["terms_status"] not in ("reviewed", "permitted"):
            return "terms_not_permitted"
        return None

    def _require_runs(self) -> None:
        """Every named run must exist before the tick writes anything."""
        for schedule in self.schedules:
            if not schedule.enabled:
                # A disabled schedule is inert, so it cannot veto the tick.
                continue
            if schedule.run_id is None:
                continue
            row = self.conn.execute(
                "SELECT 1 FROM research_runs WHERE run_id = ?", (schedule.run_id,)
            ).fetchone()
            if row is None:
                raise ScheduleError(
                    f"schedule {schedule.name!r} names run {schedule.run_id!r}, "
                    "which does not exist"
                )

    def _queued(self, now_iso: str, *, due: bool) -> list[Job]:
        jobs = list_jobs(self.conn, status="queued")
        if due:
            picked = [
                job
                for job in jobs
                if job.scheduled_for and job.scheduled_for <= now_iso
            ]
        else:
            picked = [
                job
                for job in jobs
                if job.scheduled_for and job.scheduled_for < now_iso
            ]
        # The job id is the last tiebreak, so the plan and the queue cannot
        # order two equal jobs differently.
        picked.sort(
            key=lambda job: (
                -job.priority,
                job.scheduled_for,
                job.created_at,
                job.id,
            )
        )
        return picked


def default_schedules() -> list[Schedule]:
    """The spec's cadence as data. Nothing runs it yet.

    Every schedule caps its catch-up at two rounds, so a fresh install
    backfills two occurrences per schedule instead of a whole day of them.
    """
    return [
        Schedule(
            name="source_collection",
            stage="COLLECT",
            interval_seconds=900,
            priority=int(Priority.P3),
            catchup_limit=2,
        ),
        Schedule(
            name="evidence_processing",
            stage="PROCESS",
            interval_seconds=3600,
            priority=int(Priority.P2),
            catchup_limit=2,
        ),
        Schedule(
            name="reclustering",
            stage="CLUSTER",
            interval_seconds=21600,
            priority=int(Priority.P3),
            catchup_limit=2,
        ),
        Schedule(
            name="deep_investigation",
            stage="INVESTIGATE",
            interval_seconds=86400,
            offset_seconds=7200,
            priority=int(Priority.P1),
            catchup_limit=2,
        ),
        Schedule(
            name="morning_brief",
            stage="BRIEF",
            interval_seconds=86400,
            offset_seconds=25200,
            priority=int(Priority.P2),
            catchup_limit=2,
        ),
    ]


def _check_datetime_span(label: str, seconds: int) -> None:
    """The seconds must fit the range a datetime can express.

    A timedelta holds more seconds than a datetime can place on a calendar,
    so a value that survives the timedelta check can still overflow the
    arithmetic the grid does.
    """
    if seconds > _DATETIME_SPAN_SECONDS:
        raise ScheduleError(
            f"{label} is {seconds}, larger than the {_DATETIME_SPAN_SECONDS} "
            "seconds a datetime can express"
        )


def _check_int(label: str, value: object) -> int:
    """A plain int in the signed 64-bit range SQLite stores.

    A bool is an int in Python, and a float is a number SQLite would store as
    a REAL, so both are refused here rather than reaching the table.
    """
    if not isinstance(value, int) or isinstance(value, bool):
        raise ScheduleError(f"{label} must be an integer, not {value!r}")
    if not _INT64_MIN <= value <= _INT64_MAX:
        raise ScheduleError(
            f"{label} {value} is outside the signed 64-bit range "
            f"[{_INT64_MIN}, {_INT64_MAX}]"
        )
    return int(value)


def _difference(schedule: Schedule, job: Job) -> str:
    """How a stored job differs from the schedule that describes it.

    Only a difference the schedule caused is a difference. The queue owns the
    scheduled time and can promote a job, so neither of those is compared.
    The attempt ceiling and the run are written by nothing else. The stage
    is not compared: the queue refuses a changed stage first, so the tick
    catches that conflict before it gets here.
    """
    differences: list[str] = []
    # A job above the schedule's priority was promoted on purpose.
    if job.priority < schedule.priority:
        differences.append(
            f"priority is {job.priority}, the schedule says {schedule.priority}"
        )
    if job.max_attempts != schedule.max_attempts:
        differences.append(
            f"max_attempts is {job.max_attempts}, the schedule says "
            f"{schedule.max_attempts}"
        )
    if job.run_id != schedule.run_id:
        differences.append(
            f"run_id is {job.run_id!r}, the schedule says {schedule.run_id!r}"
        )
    return "; ".join(differences)


def _rollback(conn: sqlite3.Connection) -> None:
    try:
        conn.execute("ROLLBACK")
    except sqlite3.Error:
        pass


def _rollback_to_savepoint(conn: sqlite3.Connection) -> None:
    """Undo the tick's work inside a transaction the caller owns.

    A failed rollback must not replace the exception that caused it, so the
    error is swallowed and the caller's transaction is left as it is.
    """
    try:
        conn.execute("ROLLBACK TO SAVEPOINT scheduler_tick")
        conn.execute("RELEASE SAVEPOINT scheduler_tick")
    except sqlite3.Error:
        pass

"""The upkeep the operation does on its own interval.

The pass recovers the expired leases, sweeps the workers that stopped saying
so, reviews the dead letters and prunes the checkpoints of the runs that are
finished. It never deletes a job row. A dead letter is parked, counted and
reported, and only an operator moves it.
"""

from __future__ import annotations

import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta

from product_hunter.autonomy.health import (
    DEFAULT_STALE_AFTER_SECONDS,
    sweep_stale_workers,
)
from product_hunter.autonomy.states import OperationState, classify_failure
from product_hunter.db.connection import utc_now
from product_hunter.db.jobs import Job, list_jobs, recover_expired_leases
from product_hunter.db.outbox import recover_projection_leases
from product_hunter.db.scheduler import Scheduler

MAINTENANCE_STAGE = "MAINTENANCE"

# The checkpoints of a finished run are kept for a week. The table grows with
# every run, and the checkpoint of a run nobody resumes is dead weight.
CHECKPOINT_RETENTION_DAYS = 7


@dataclass(frozen=True)
class MaintenanceReport:
    """What the pass did."""

    leases_recovered: int
    projections_recovered: int
    workers_marked_stale: int
    dead_letters: int
    dead_letters_retried: int
    checkpoints_pruned: int

    def to_dict(self) -> dict[str, object]:
        """The report as plain data."""
        return asdict(self)


def run_maintenance(
    conn: sqlite3.Connection,
    *,
    now: datetime | None = None,
    retry_dead_letters: bool = False,
    max_dead_letter_retries: int = 3,
    checkpoint_retention_days: int = CHECKPOINT_RETENTION_DAYS,
) -> MaintenanceReport:
    """The upkeep pass, and what it changed."""
    now_dt = utc_now(now)
    leases = recover_expired_leases(conn, now=now_dt)
    projections = recover_projection_leases(conn, now=now_dt)
    stale = sweep_stale_workers(
        conn, stale_after_seconds=DEFAULT_STALE_AFTER_SECONDS, now=now_dt
    )
    dead = list_jobs(conn, status="dead")
    retried = 0
    if retry_dead_letters:
        retried = _retry_dead_letters(
            conn, dead, now=now_dt, max_retries=max_dead_letter_retries
        )
    pruned = _prune_checkpoints(
        conn, now=now_dt, retention_days=checkpoint_retention_days
    )
    return MaintenanceReport(
        leases_recovered=leases,
        projections_recovered=projections,
        workers_marked_stale=stale,
        dead_letters=len(dead),
        dead_letters_retried=retried,
        checkpoints_pruned=pruned,
    )


def make_maintenance_handler(conn: sqlite3.Connection, **options: object):
    """The handler the MAINTENANCE stage runs.

    The handler is what a maintenance job on the queue calls. It does the
    same pass the engine does on its own interval, so a process that has no
    other handler still does the upkeep.
    """

    def handle(context) -> dict[str, object]:
        report = run_maintenance(conn, now=utc_now(), **options)
        return report.to_dict()

    return handle


def _retry_dead_letters(
    conn: sqlite3.Connection,
    dead: list[Job],
    *,
    now: datetime,
    max_retries: int,
) -> int:
    """Queue a new job for the dead letters that may still succeed.

    A dead letter whose failure names a permanent condition is not tried
    again, because it will not work. The retry is a new job with its own
    key, so the dead row stays where it is.
    """
    scheduler = Scheduler(conn, [])
    retried = 0
    for job in dead:
        if classify_failure(job.last_error) is OperationState.permanent_failure:
            continue
        if _retry_count(conn, job) >= max_retries:
            continue
        scheduler.schedule_retry(job, now=now, reason="maintenance")
        retried += 1
    return retried


def _retry_count(conn: sqlite3.Connection, job: Job) -> int:
    """How many times the job has already been retried."""
    row = conn.execute(
        "SELECT count(*) FROM jobs WHERE idempotency_key LIKE ?",
        (f"retry:{job.id}:%",),
    ).fetchone()
    return int(row[0])


def _prune_checkpoints(
    conn: sqlite3.Connection, *, now: datetime, retention_days: int
) -> int:
    """Drop the checkpoints of the finished runs that are past the retention.

    A run that is still going keeps its checkpoints, because that is where a
    resume starts from.
    """
    cutoff = (now - timedelta(days=retention_days)).isoformat()
    changed = conn.execute(
        "DELETE FROM research_checkpoints WHERE created_at < ? AND run_id IN ("
        "  SELECT run_id FROM research_runs WHERE completed_at IS NOT NULL"
        ")",
        (cutoff,),
    ).rowcount
    return int(changed)

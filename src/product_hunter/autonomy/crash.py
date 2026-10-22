"""What a worker does when it starts after a crash.

The sequence is the one the contract names: recover the expired leases, count
the work that was interrupted, check the checkpoints, and report what is left.
The report says what was found, so an operator can see what the last run left
behind without reading the tables by hand.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime

from product_hunter.autonomy.health import (
    DEFAULT_STALE_AFTER_SECONDS,
    sweep_stale_workers,
)
from product_hunter.db.checkpoints import Checkpoint, load_valid_checkpoint
from product_hunter.db.connection import utc_now
from product_hunter.db.jobs import recover_expired_leases
from product_hunter.db.outbox import recover_projection_leases


@dataclass(frozen=True)
class CrashRecoveryReport:
    """What the recovery found and changed."""

    leases_recovered: int
    projections_recovered: int
    interrupted_jobs: int
    checkpoints_validated: int
    checkpoints_skipped: int
    requeued: int
    dead_letters: int
    workers_marked_stale: int = 0


def recover(
    conn: sqlite3.Connection,
    *,
    now: datetime | None = None,
    stale_after_seconds: int = DEFAULT_STALE_AFTER_SECONDS,
) -> CrashRecoveryReport:
    """The startup sequence of a worker that comes back after a crash.

    The expired leases are recovered first, so the work that was interrupted
    is queued again before anything counts it.
    """
    now_dt = utc_now(now)
    interrupted = _interrupted_jobs(conn, now=now_dt)
    leases = recover_expired_leases(conn, now=now_dt)
    projections = recover_projection_leases(conn, now=now_dt)
    validated, skipped = _checkpoints(conn)
    requeued = _requeued(conn)
    dead_letters = _dead_letters(conn)
    stale = sweep_stale_workers(
        conn, stale_after_seconds=stale_after_seconds, now=now_dt
    )
    return CrashRecoveryReport(
        leases_recovered=leases,
        projections_recovered=projections,
        interrupted_jobs=interrupted,
        checkpoints_validated=validated,
        checkpoints_skipped=skipped,
        requeued=requeued,
        dead_letters=dead_letters,
        workers_marked_stale=stale,
    )


def resume_point(
    conn: sqlite3.Connection, *, run_id: str, stage: str, step: str | None = None
) -> Checkpoint | None:
    """The checkpoint a run resumes from.

    It is the newest checkpoint whose state parses. A row that was edited
    outside the worker is stepped over rather than stopping the resume.
    """
    return load_valid_checkpoint(conn, run_id=run_id, stage=stage, step=step)


def _interrupted_jobs(conn: sqlite3.Connection, *, now: datetime) -> int:
    """The attempts that were running when the process died.

    An attempt is interrupted when it still says it is running and the job it
    belongs to is not held by a live lease: the worker that held it is gone.
    The count is taken before the leases are recovered, because the recovery
    is what turns the interrupted attempts into lost ones.
    """
    row = conn.execute(
        "SELECT count(*) FROM stage_attempts a JOIN jobs j ON j.id = a.job_id "
        "WHERE a.status = 'running' AND (j.status != 'leased' "
        "  OR j.lease_expires_at IS NULL OR j.lease_expires_at <= ?)",
        (now.isoformat(),),
    ).fetchone()
    return int(row[0])


def _checkpoints(conn: sqlite3.Connection) -> tuple[int, int]:
    """The checkpoints that parse, and the ones recovery had to step over."""
    pairs = conn.execute(
        "SELECT DISTINCT run_id, stage FROM jobs "
        "WHERE run_id IS NOT NULL AND run_id != '' AND stage IS NOT NULL"
    ).fetchall()
    validated = 0
    skipped = 0
    for pair in pairs:
        run_id = str(pair["run_id"])
        stage = str(pair["stage"])
        newest = conn.execute(
            "SELECT max(checkpoint_version) FROM research_checkpoints "
            "WHERE run_id = ? AND stage = ?",
            (run_id, stage),
        ).fetchone()[0]
        if newest is None:
            continue
        checkpoint = load_valid_checkpoint(conn, run_id=run_id, stage=stage)
        if checkpoint is None:
            # Every checkpoint of this stage is unreadable, so there is
            # nothing to resume from.
            skipped += 1
            continue
        validated += 1
        if int(checkpoint.checkpoint_version) < int(newest):
            # The newest one could not be read, so recovery used the one
            # before it.
            skipped += 1
    return validated, skipped


def _requeued(conn: sqlite3.Connection) -> int:
    """The work that was tried and is queued again."""
    row = conn.execute(
        "SELECT count(*) FROM jobs WHERE status = 'queued' AND attempts > 0"
    ).fetchone()
    return int(row[0])


def _dead_letters(conn: sqlite3.Connection) -> int:
    """The jobs that ran out of attempts."""
    row = conn.execute("SELECT count(*) FROM jobs WHERE status = 'dead'").fetchone()
    return int(row[0])

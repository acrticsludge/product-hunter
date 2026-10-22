"""The worker's health, so a silent worker is visible.

One row per worker. The row says what the worker is doing, when it last said
so, and how many jobs it finished or failed. A worker that stopped saying so
is marked stale by the sweep, and the sweep is what tells an operator that a
process died without saying so itself.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta

from product_hunter.db.connection import utc_now
from product_hunter.runtime.identity import WorkerIdentity

# The runtime's own masker, so a failure that reaches the health row is
# masked the way a failure that reaches the job row is.
from product_hunter.runtime.worker import _safe_error

# The silence after which a worker is stale. It is longer than a heartbeat
# interval, so a worker that is busy but alive is not marked stale.
DEFAULT_STALE_AFTER_SECONDS = 90

STATUS_STARTING = "starting"
STATUS_HEALTHY = "healthy"
STATUS_STOPPING = "stopping"
STATUS_STALE = "stale"

# The counters a health row keeps, and the column each one writes.
_COUNTERS = {
    "jobs_completed": "jobs_completed",
    "jobs_failed": "jobs_failed",
}


class HealthError(Exception):
    """A health counter was asked for that does not exist."""


@dataclass(frozen=True)
class WorkerHealthRow:
    """One worker's health."""

    worker_id: str
    hostname: str
    pid: int
    status: str
    current_job_id: str | None
    current_stage: str | None
    started_at: str
    heartbeat_at: str
    stopped_at: str | None
    jobs_completed: int
    jobs_failed: int
    last_error: str | None

    def to_dict(self) -> dict[str, object]:
        """The health row as plain data."""
        return {
            "worker_id": self.worker_id,
            "hostname": self.hostname,
            "pid": self.pid,
            "status": self.status,
            "current_job_id": self.current_job_id,
            "current_stage": self.current_stage,
            "started_at": self.started_at,
            "heartbeat_at": self.heartbeat_at,
            "stopped_at": self.stopped_at,
            "jobs_completed": self.jobs_completed,
            "jobs_failed": self.jobs_failed,
            "last_error": self.last_error,
        }


class WorkerHealth:
    """The health of one worker, kept in the worker_health table."""

    def __init__(
        self,
        conn: sqlite3.Connection,
        identity: WorkerIdentity,
        *,
        stale_after_seconds: int = DEFAULT_STALE_AFTER_SECONDS,
    ) -> None:
        if stale_after_seconds < 1:
            raise ValueError("the stale threshold must be at least one second")
        self.conn = conn
        self.identity = identity
        self.stale_after_seconds = int(stale_after_seconds)

    def register(self, *, now: datetime | None = None) -> None:
        """Add the worker, or say again that it is alive."""
        now_iso = utc_now(now).isoformat()
        self.conn.execute(
            """
            INSERT INTO worker_health (
                worker_id, hostname, pid, status, started_at, heartbeat_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(worker_id) DO UPDATE SET
                status = excluded.status,
                heartbeat_at = excluded.heartbeat_at
            """,
            (
                self.identity.worker_id,
                self.identity.hostname,
                self.identity.pid,
                STATUS_HEALTHY,
                self.identity.started_at,
                now_iso,
            ),
        )

    def beat(
        self,
        *,
        job_id: str | None = None,
        stage: str | None = None,
        now: datetime | None = None,
    ) -> None:
        """Say the worker is alive, and what it is working on.

        The current job and its stage are written only when a job is named,
        so a heartbeat between two jobs does not clear what the worker is on.
        """
        now_iso = utc_now(now).isoformat()
        self.conn.execute(
            """
            UPDATE worker_health
            SET heartbeat_at = ?, status = ?,
                current_job_id = COALESCE(?, current_job_id),
                current_stage = COALESCE(?, current_stage)
            WHERE worker_id = ?
            """,
            (
                now_iso,
                STATUS_HEALTHY,
                job_id,
                stage,
                self.identity.worker_id,
            ),
        )

    def count(self, event: str, *, now: datetime | None = None) -> None:
        """Add one to a counter of the worker's health."""
        column = _COUNTERS.get(event)
        if column is None:
            raise HealthError(f"unknown health counter {event!r}")
        self.conn.execute(
            f"UPDATE worker_health SET {column} = {column} + 1, heartbeat_at = ? "
            "WHERE worker_id = ?",
            (utc_now(now).isoformat(), self.identity.worker_id),
        )

    def record_error(self, error: str, *, now: datetime | None = None) -> None:
        """Name the last failure of the worker, and say it is still alive.

        The text is masked the way the runtime masks a failure, so a message
        that carries a secret does not reach the row. The mask brings its own
        type prefix, and the text the caller has is already masked, so the
        row reads a little heavier than the failure did.
        """
        self.conn.execute(
            "UPDATE worker_health SET last_error = ?, heartbeat_at = ?, status = ? "
            "WHERE worker_id = ?",
            (
                _safe_error(Exception(error)),
                utc_now(now).isoformat(),
                STATUS_HEALTHY,
                self.identity.worker_id,
            ),
        )

    def mark_stopping(self, *, now: datetime | None = None) -> None:
        """The worker is on its way out."""
        now_iso = utc_now(now).isoformat()
        self.conn.execute(
            "UPDATE worker_health SET status = ?, stopped_at = ?, heartbeat_at = ? "
            "WHERE worker_id = ?",
            (STATUS_STOPPING, now_iso, now_iso, self.identity.worker_id),
        )

    def sweep(self, *, now: datetime | None = None) -> int:
        """Mark the workers that stopped saying so as stale."""
        return sweep_stale_workers(
            self.conn, stale_after_seconds=self.stale_after_seconds, now=now
        )

    def workers(self, *, now: datetime | None = None) -> list[WorkerHealthRow]:
        """Every worker's health, the freshest heartbeat first."""
        return list_worker_health(self.conn)


def list_worker_health(conn: sqlite3.Connection) -> list[WorkerHealthRow]:
    """Every worker's health, the freshest heartbeat first."""
    rows = conn.execute(
        "SELECT * FROM worker_health ORDER BY heartbeat_at DESC, worker_id"
    ).fetchall()
    return [_row_to_health(row) for row in rows]


def sweep_stale_workers(
    conn: sqlite3.Connection,
    *,
    stale_after_seconds: int = DEFAULT_STALE_AFTER_SECONDS,
    now: datetime | None = None,
) -> int:
    """Mark the workers whose heartbeat is older than the threshold.

    A worker that is already stale is not counted again, so the number is
    the number of workers this sweep changed.
    """
    cutoff = (
        utc_now(now) - timedelta(seconds=stale_after_seconds)
    ).isoformat()
    changed = conn.execute(
        "UPDATE worker_health SET status = ? "
        "WHERE status != ? AND heartbeat_at < ?",
        (STATUS_STALE, STATUS_STALE, cutoff),
    ).rowcount
    return int(changed)


def _row_to_health(row: sqlite3.Row) -> WorkerHealthRow:
    """One row of the worker_health table."""
    return WorkerHealthRow(
        worker_id=str(row["worker_id"]),
        hostname=str(row["hostname"]),
        pid=int(row["pid"]),
        status=str(row["status"]),
        current_job_id=row["current_job_id"],
        current_stage=row["current_stage"],
        started_at=str(row["started_at"]),
        heartbeat_at=str(row["heartbeat_at"]),
        stopped_at=row["stopped_at"],
        jobs_completed=int(row["jobs_completed"]),
        jobs_failed=int(row["jobs_failed"]),
        last_error=row["last_error"],
    )

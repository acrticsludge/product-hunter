"""Persistent, fenced SQLite job queue.

Every job has a deterministic id from its idempotency key. A claim increments
``lease_epoch``, and every write a worker makes must carry the worker id and
that epoch. A write that does not match the current lease updates zero rows and
raises ``LeaseLost``. An expired lease is recovered as queued work, never as a
successful job.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterator

from product_hunter.fetch import utcnow


class JobError(Exception):
    """A queue operation was refused."""


class JobConflict(JobError):
    """An idempotency key was reused with different content."""


class LeaseLost(JobError):
    """The caller no longer owns the lease it tried to write with."""


@dataclass(frozen=True)
class Job:
    id: str
    run_id: str | None
    stage: str
    step: str | None
    payload: dict[str, object]
    priority: int
    status: str
    attempts: int
    max_attempts: int
    idempotency_key: str
    worker_id: str | None
    lease_epoch: int
    lease_expires_at: str | None
    created_at: str
    scheduled_for: str
    completed_at: str | None
    last_error: str | None
    result: dict[str, object] | None


@dataclass(frozen=True)
class JobOutcome:
    job_id: str
    status: str
    result: dict[str, object] | None = None
    error: str | None = None


def enqueue_job(
    conn: sqlite3.Connection,
    *,
    stage: str,
    idempotency_key: str,
    payload: dict[str, object] | None = None,
    run_id: str | None = None,
    step: str | None = None,
    priority: int = 0,
    max_attempts: int = 3,
    scheduled_for: str | None = None,
    now: datetime | None = None,
) -> Job:
    """Insert a job once. The same key always returns the same row."""
    if not stage.strip():
        raise JobError("a job needs a stage")
    if not idempotency_key.strip():
        raise JobError("a job needs an idempotency key")
    if max_attempts < 1:
        raise JobError("max_attempts must be at least 1")
    payload_json = json.dumps(payload or {}, sort_keys=True)
    job_id = _hash_id("job_", idempotency_key)
    existing = get_job(conn, job_id)
    if existing is not None:
        if existing.stage != stage or existing.payload != json.loads(payload_json):
            raise JobConflict(
                f"job key {idempotency_key!r} is already used by different content"
            )
        return existing
    now_dt = _now(now)
    scheduled = scheduled_for or now_dt.isoformat()
    conn.execute(
        """
        INSERT INTO jobs (
            id, run_id, stage, step, payload_json, priority, status,
            attempts, max_attempts, idempotency_key, created_at, scheduled_for
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            job_id,
            run_id,
            stage,
            step,
            payload_json,
            priority,
            "queued",
            0,
            max_attempts,
            idempotency_key,
            now_dt.isoformat(),
            scheduled,
        ),
    )
    job = get_job(conn, job_id)
    if job is None:  # pragma: no cover - the insert either raised or made a row
        raise JobError("job insert did not produce a row")
    return job


def get_job(conn: sqlite3.Connection, job_id: str) -> Job | None:
    row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    return _row_to_job(row) if row is not None else None


def claim_job(
    conn: sqlite3.Connection,
    worker_id: str,
    *,
    lease_seconds: int = 60,
    now: datetime | None = None,
) -> Job | None:
    """Recover expired leases, then claim the highest-priority queued job."""
    if lease_seconds < 1:
        raise JobError("lease_seconds must be at least 1")
    now_dt = _now(now)
    recover_expired_leases(conn, now=now_dt)
    now_iso = now_dt.isoformat()
    expires = _after(now_dt, lease_seconds)
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            """
            SELECT id FROM jobs
            WHERE status = 'queued' AND scheduled_for <= ?
            ORDER BY priority DESC, scheduled_for ASC, created_at ASC, id ASC
            LIMIT 1
            """,
            (now_iso,),
        ).fetchone()
        if row is None:
            conn.execute("COMMIT")
            return None
        job_id = row["id"]
        changed = conn.execute(
            """
            UPDATE jobs
            SET status = 'leased', worker_id = ?, lease_epoch = lease_epoch + 1,
                lease_expires_at = ?, attempts = attempts + 1
            WHERE id = ? AND status = 'queued'
            """,
            (worker_id, expires, job_id),
        ).rowcount
        if changed != 1:
            conn.execute("ROLLBACK")
            return None
        job = get_job(conn, job_id)
        if job is None:  # pragma: no cover
            conn.execute("ROLLBACK")
            raise JobError("claimed job disappeared")
        conn.execute(
            """
            INSERT INTO stage_attempts (
                attempt_id, job_id, run_id, stage, attempt, worker_id,
                lease_epoch, status, started_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                _hash_id("att_", f"{job.id}|{job.attempts}"),
                job.id,
                job.run_id,
                job.stage,
                job.attempts,
                worker_id,
                job.lease_epoch,
                "running",
                now_iso,
            ),
        )
        conn.execute("COMMIT")
        return job
    except BaseException:
        _rollback(conn)
        raise


def renew_lease(
    conn: sqlite3.Connection,
    job: Job,
    *,
    worker_id: str,
    lease_epoch: int,
    lease_seconds: int = 60,
    now: datetime | None = None,
) -> str:
    """Extend a live lease. A stale worker gets ``LeaseLost``."""
    now_dt = _now(now)
    now_iso = now_dt.isoformat()
    expires = _after(now_dt, lease_seconds)
    changed = conn.execute(
        """
        UPDATE jobs SET lease_expires_at = ?
        WHERE id = ? AND status = 'leased'
          AND worker_id = ? AND lease_epoch = ?
          AND lease_expires_at > ?
        """,
        (expires, job.id, worker_id, lease_epoch, now_iso),
    ).rowcount
    if changed != 1:
        raise LeaseLost(
            f"job {job.id!r} is no longer leased to {worker_id!r} at epoch {lease_epoch}"
        )
    return expires


def complete_job(
    conn: sqlite3.Connection,
    job: Job,
    *,
    worker_id: str,
    lease_epoch: int,
    result: dict[str, object] | None = None,
    now: datetime | None = None,
) -> Job:
    """Mark a job succeeded only while the caller still owns the lease."""
    now_iso = _now(now).isoformat()
    result_json = json.dumps(result, sort_keys=True) if result is not None else None
    try:
        conn.execute("BEGIN IMMEDIATE")
        changed = conn.execute(
            """
            UPDATE jobs
            SET status = 'succeeded', result_json = ?, completed_at = ?,
                worker_id = NULL, lease_expires_at = NULL
            WHERE id = ? AND status = 'leased'
              AND worker_id = ? AND lease_epoch = ?
              AND lease_expires_at > ?
            """,
            (result_json, now_iso, job.id, worker_id, lease_epoch, now_iso),
        ).rowcount
        if changed != 1:
            conn.execute("ROLLBACK")
            raise LeaseLost(
                f"job {job.id!r} is no longer leased to {worker_id!r} at epoch {lease_epoch}"
            )
        conn.execute(
            """
            UPDATE stage_attempts
            SET status = 'succeeded', finished_at = ?, result_json = ?
            WHERE job_id = ? AND attempt = ?
            """,
            (now_iso, result_json, job.id, job.attempts),
        )
        conn.execute("COMMIT")
    except BaseException:
        _rollback(conn)
        raise
    completed = get_job(conn, job.id)
    if completed is None:  # pragma: no cover
        raise JobError("completed job disappeared")
    return completed


def fail_job(
    conn: sqlite3.Connection,
    job: Job,
    *,
    worker_id: str,
    lease_epoch: int,
    error: str,
    backoff_seconds: int = 5,
    now: datetime | None = None,
) -> Job:
    """Record a handler failure, requeue it, or mark it dead."""
    now_dt = _now(now)
    now_iso = now_dt.isoformat()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            """
            SELECT attempts, max_attempts, status, worker_id, lease_epoch
            FROM jobs WHERE id = ?
            """,
            (job.id,),
        ).fetchone()
        if row is None or not _owns(row, worker_id, lease_epoch):
            conn.execute("ROLLBACK")
            raise LeaseLost(
                f"job {job.id!r} is no longer leased to {worker_id!r} at epoch {lease_epoch}"
            )
        dead = int(row["attempts"]) >= int(row["max_attempts"])
        status = "dead" if dead else "queued"
        completed_at = now_iso if dead else None
        scheduled_for = _after(now_dt, backoff_seconds)
        changed = conn.execute(
            """
            UPDATE jobs
            SET status = ?, worker_id = NULL, lease_expires_at = NULL,
                last_error = ?, completed_at = ?, scheduled_for = ?
            WHERE id = ? AND status = 'leased'
              AND worker_id = ? AND lease_epoch = ?
              AND lease_expires_at > ?
            """,
            (
                status,
                error,
                completed_at,
                scheduled_for,
                job.id,
                worker_id,
                lease_epoch,
                now_iso,
            ),
        ).rowcount
        if changed != 1:
            conn.execute("ROLLBACK")
            raise LeaseLost(
                f"job {job.id!r} is no longer leased to {worker_id!r} at epoch {lease_epoch}"
            )
        conn.execute(
            """
            UPDATE stage_attempts
            SET status = 'failed', finished_at = ?, error = ?
            WHERE job_id = ? AND attempt = ?
            """,
            (now_iso, error, job.id, job.attempts),
        )
        conn.execute("COMMIT")
    except BaseException:
        _rollback(conn)
        raise
    failed = get_job(conn, job.id)
    if failed is None:  # pragma: no cover
        raise JobError("failed job disappeared")
    return failed


def recover_expired_leases(
    conn: sqlite3.Connection, *, now: datetime | None = None
) -> int:
    """Requeue expired leases, or mark them dead at the attempt ceiling."""
    now_dt = _now(now)
    now_iso = now_dt.isoformat()
    try:
        conn.execute("BEGIN IMMEDIATE")
        rows = conn.execute(
            """
            SELECT id, attempts, max_attempts FROM jobs
            WHERE status = 'leased' AND lease_expires_at IS NOT NULL
              AND lease_expires_at <= ?
            """,
            (now_iso,),
        ).fetchall()
        for row in rows:
            dead = int(row["attempts"]) >= int(row["max_attempts"])
            status = "dead" if dead else "queued"
            conn.execute(
                """
                UPDATE jobs
                SET status = ?, worker_id = NULL, lease_expires_at = NULL,
                    last_error = 'lease_expired', completed_at = ?,
                    scheduled_for = ?
                WHERE id = ? AND status = 'leased'
                """,
                (
                    status,
                    now_iso if dead else None,
                    now_iso,
                    row["id"],
                ),
            )
            conn.execute(
                """
                UPDATE stage_attempts
                SET status = 'lease_lost', finished_at = ?, error = 'lease_expired'
                WHERE job_id = ? AND attempt = ?
                """,
                (now_iso, row["id"], int(row["attempts"])),
            )
        conn.execute("COMMIT")
        return len(rows)
    except BaseException:
        _rollback(conn)
        raise


def list_jobs(
    conn: sqlite3.Connection, *, status: str | None = None
) -> list[Job]:
    if status is None:
        rows = conn.execute("SELECT * FROM jobs ORDER BY created_at, id").fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM jobs WHERE status = ? ORDER BY created_at, id", (status,)
        ).fetchall()
    return [_row_to_job(row) for row in rows]


class JobContext:
    """The fenced handle a handler uses for authoritative writes."""

    def __init__(
        self,
        conn: sqlite3.Connection,
        job: Job,
        *,
        worker_id: str,
        lease_seconds: int = 60,
    ) -> None:
        self.conn = conn
        self.job = job
        self.worker_id = worker_id
        self.lease_seconds = lease_seconds

    def assert_lease(self) -> None:
        row = self.conn.execute(
            "SELECT status, worker_id, lease_epoch, lease_expires_at "
            "FROM jobs WHERE id = ?",
            (self.job.id,),
        ).fetchone()
        if (
            row is None
            or not _owns(row, self.worker_id, self.job.lease_epoch)
            or row["lease_expires_at"] is None
            or row["lease_expires_at"] <= utcnow().isoformat()
        ):
            raise LeaseLost(
                f"job {self.job.id!r} is no longer leased to "
                f"{self.worker_id!r} at epoch {self.job.lease_epoch}"
            )

    def heartbeat(self, *, now: datetime | None = None) -> str:
        return renew_lease(
            self.conn,
            self.job,
            worker_id=self.worker_id,
            lease_epoch=self.job.lease_epoch,
            lease_seconds=self.lease_seconds,
            now=now,
        )

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """One fenced transaction: no stale worker can commit a write."""
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            self.assert_lease()
            yield self.conn
            self.assert_lease()
            self.conn.execute("COMMIT")
        except BaseException:
            _rollback(self.conn)
            raise


def _owns(row: sqlite3.Row, worker_id: str, lease_epoch: int) -> bool:
    return (
        row["status"] == "leased"
        and row["worker_id"] == worker_id
        and int(row["lease_epoch"]) == int(lease_epoch)
    )


def _row_to_job(row: sqlite3.Row) -> Job:
    return Job(
        id=row["id"],
        run_id=row["run_id"],
        stage=row["stage"],
        step=row["step"],
        payload=json.loads(row["payload_json"] or "{}"),
        priority=int(row["priority"]),
        status=row["status"],
        attempts=int(row["attempts"]),
        max_attempts=int(row["max_attempts"]),
        idempotency_key=row["idempotency_key"],
        worker_id=row["worker_id"],
        lease_epoch=int(row["lease_epoch"]),
        lease_expires_at=row["lease_expires_at"],
        created_at=row["created_at"],
        scheduled_for=row["scheduled_for"],
        completed_at=row["completed_at"],
        last_error=row["last_error"],
        result=json.loads(row["result_json"]) if row["result_json"] else None,
    )


def _rollback(conn: sqlite3.Connection) -> None:
    try:
        conn.execute("ROLLBACK")
    except sqlite3.Error:
        pass


def _hash_id(prefix: str, basis: str) -> str:
    return prefix + hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16]


def _now(value: datetime | None) -> datetime:
    """The instant to compare against, always in UTC.

    An aware value is converted, a naive one is read as UTC, so the queue and
    the scheduler read the clock the same way.
    """
    if value is None:
        return utcnow()
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _after(now: datetime, seconds: int) -> str:
    return (now + timedelta(seconds=seconds)).isoformat()

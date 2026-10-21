"""Transactional outbox for downstream projections.

The authoritative SQLite transaction writes a projection task in the same
transaction as the evidence. This module claims those tasks, runs an idempotent
downstream handler, and retries or fails without touching the authoritative
row. A downstream failure cannot roll back committed evidence.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from product_hunter.db.connection import rollback, utc_now
from product_hunter.db.jobs import LeaseLost


class OutboxError(Exception):
    """An outbox operation was refused."""


class OutboxConflict(OutboxError):
    """An idempotency key was reused with different content."""


@dataclass(frozen=True)
class ProjectionTask:
    task_id: str
    target: str
    entity_type: str
    entity_id: str
    entity_version: str | None
    idempotency_key: str
    status: str
    attempts: int
    max_attempts: int
    last_error: str | None
    created_at: str
    available_at: str | None
    completed_at: str | None
    lease_expires_at: str | None
    worker_id: str | None
    lease_epoch: int
    payload: dict[str, object]


ProjectionHandler = Callable[[ProjectionTask], None]


def enqueue_projection(
    conn: sqlite3.Connection,
    *,
    target: str,
    entity_type: str,
    entity_id: str,
    idempotency_key: str,
    payload: dict[str, object] | None = None,
    entity_version: str | None = None,
    max_attempts: int = 5,
    available_at: str | None = None,
    now: datetime | None = None,
) -> ProjectionTask:
    """Insert one projection task, or return the existing one."""
    if not target.strip() or not entity_type.strip() or not entity_id.strip():
        raise OutboxError("a projection task needs a target and an entity")
    if not idempotency_key.strip():
        raise OutboxError("a projection task needs an idempotency key")
    if max_attempts < 1:
        raise OutboxError("max_attempts must be at least 1")
    payload_json = json.dumps(payload or {}, sort_keys=True)
    task_id = _hash_id("proj_", idempotency_key)
    existing = get_projection_task(conn, task_id)
    if existing is not None:
        if (
            existing.target != target
            or existing.entity_type != entity_type
            or existing.entity_id != entity_id
            or existing.payload != json.loads(payload_json)
        ):
            raise OutboxConflict(
                f"projection key {idempotency_key!r} is already used by different content"
            )
        return existing
    now_dt = utc_now(now)
    conn.execute(
        """
        INSERT INTO projection_tasks (
            task_id, target, entity_type, entity_id, entity_version,
            idempotency_key, status, attempts, max_attempts, last_error,
            created_at, available_at, completed_at, lease_expires_at,
            worker_id, lease_epoch, payload_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            task_id,
            target,
            entity_type,
            entity_id,
            entity_version,
            idempotency_key,
            "pending",
            0,
            max_attempts,
            None,
            now_dt.isoformat(),
            available_at or now_dt.isoformat(),
            None,
            None,
            None,
            0,
            payload_json,
        ),
    )
    task = get_projection_task(conn, task_id)
    if task is None:  # pragma: no cover
        raise OutboxError("projection insert did not produce a row")
    return task


def get_projection_task(
    conn: sqlite3.Connection, task_id: str
) -> ProjectionTask | None:
    row = conn.execute(
        "SELECT * FROM projection_tasks WHERE task_id = ?", (task_id,)
    ).fetchone()
    return _row_to_task(row) if row is not None else None


def claim_projection(
    conn: sqlite3.Connection,
    worker_id: str,
    *,
    target: str | None = None,
    lease_seconds: int = 60,
    now: datetime | None = None,
) -> ProjectionTask | None:
    """Recover expired projections, then claim one pending task."""
    if lease_seconds < 1:
        raise OutboxError("lease_seconds must be at least 1")
    now_dt = utc_now(now)
    now_iso = now_dt.isoformat()
    recover_projection_leases(conn, now=now_dt)
    expires = _after(now_dt, lease_seconds)
    try:
        conn.execute("BEGIN IMMEDIATE")
        sql = (
            "SELECT task_id FROM projection_tasks "
            "WHERE status = 'pending' AND available_at <= ?"
        )
        params: list[object] = [now_iso]
        if target is not None:
            sql += " AND target = ?"
            params.append(target)
        sql += " ORDER BY available_at ASC, created_at ASC LIMIT 1"
        row = conn.execute(sql, params).fetchone()
        if row is None:
            conn.execute("COMMIT")
            return None
        changed = conn.execute(
            """
            UPDATE projection_tasks
            SET status = 'processing', worker_id = ?,
                lease_epoch = lease_epoch + 1, lease_expires_at = ?,
                attempts = attempts + 1
            WHERE task_id = ? AND status = 'pending'
            """,
            (worker_id, expires, row["task_id"]),
        ).rowcount
        if changed != 1:
            conn.execute("ROLLBACK")
            return None
        task = get_projection_task(conn, row["task_id"])
        conn.execute("COMMIT")
        return task
    except BaseException:
        rollback(conn)
        raise


def complete_projection(
    conn: sqlite3.Connection,
    task: ProjectionTask,
    *,
    worker_id: str,
    lease_epoch: int,
    now: datetime | None = None,
) -> ProjectionTask:
    now_iso = utc_now(now).isoformat()
    try:
        conn.execute("BEGIN IMMEDIATE")
        changed = conn.execute(
            """
            UPDATE projection_tasks
            SET status = 'done', completed_at = ?, worker_id = NULL,
                lease_expires_at = NULL
            WHERE task_id = ? AND status = 'processing'
              AND worker_id = ? AND lease_epoch = ?
              AND lease_expires_at > ?
            """,
            (now_iso, task.task_id, worker_id, lease_epoch, now_iso),
        ).rowcount
        if changed != 1:
            conn.execute("ROLLBACK")
            raise LeaseLost(
                f"projection {task.task_id!r} is no longer leased to "
                f"{worker_id!r} at epoch {lease_epoch}"
            )
        conn.execute("COMMIT")
    except BaseException:
        rollback(conn)
        raise
    completed = get_projection_task(conn, task.task_id)
    if completed is None:  # pragma: no cover
        raise OutboxError("completed projection disappeared")
    return completed


def fail_projection(
    conn: sqlite3.Connection,
    task: ProjectionTask,
    *,
    worker_id: str,
    lease_epoch: int,
    error: str,
    backoff_seconds: int = 5,
    now: datetime | None = None,
) -> ProjectionTask:
    now_dt = utc_now(now)
    now_iso = now_dt.isoformat()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            """
            SELECT attempts, max_attempts, status, worker_id, lease_epoch
            FROM projection_tasks WHERE task_id = ?
            """,
            (task.task_id,),
        ).fetchone()
        if row is None or not _owns(row, worker_id, lease_epoch):
            conn.execute("ROLLBACK")
            raise LeaseLost(
                f"projection {task.task_id!r} is no longer leased to "
                f"{worker_id!r} at epoch {lease_epoch}"
            )
        dead = int(row["attempts"]) >= int(row["max_attempts"])
        status = "failed" if dead else "pending"
        changed = conn.execute(
            """
            UPDATE projection_tasks
            SET status = ?, worker_id = NULL, lease_expires_at = NULL,
                last_error = ?, completed_at = ?, available_at = ?
            WHERE task_id = ? AND status = 'processing'
              AND worker_id = ? AND lease_epoch = ?
              AND lease_expires_at > ?
            """,
            (
                status,
                error,
                now_iso if dead else None,
                _after(now_dt, backoff_seconds),
                task.task_id,
                worker_id,
                lease_epoch,
                now_iso,
            ),
        ).rowcount
        if changed != 1:
            conn.execute("ROLLBACK")
            raise LeaseLost(
                f"projection {task.task_id!r} is no longer leased to "
                f"{worker_id!r} at epoch {lease_epoch}"
            )
        conn.execute("COMMIT")
    except BaseException:
        rollback(conn)
        raise
    failed = get_projection_task(conn, task.task_id)
    if failed is None:  # pragma: no cover
        raise OutboxError("failed projection disappeared")
    return failed


def recover_projection_leases(
    conn: sqlite3.Connection, *, now: datetime | None = None
) -> int:
    now_iso = utc_now(now).isoformat()
    try:
        conn.execute("BEGIN IMMEDIATE")
        rows = conn.execute(
            """
            SELECT task_id, attempts, max_attempts FROM projection_tasks
            WHERE status = 'processing' AND lease_expires_at IS NOT NULL
              AND lease_expires_at <= ?
            """,
            (now_iso,),
        ).fetchall()
        for row in rows:
            dead = int(row["attempts"]) >= int(row["max_attempts"])
            conn.execute(
                """
                UPDATE projection_tasks
                SET status = ?, worker_id = NULL, lease_expires_at = NULL,
                    last_error = 'lease_expired', completed_at = ?,
                    available_at = ?
                WHERE task_id = ? AND status = 'processing'
                """,
                (
                    "failed" if dead else "pending",
                    now_iso if dead else None,
                    now_iso,
                    row["task_id"],
                ),
            )
        conn.execute("COMMIT")
        return len(rows)
    except BaseException:
        rollback(conn)
        raise


def process_outbox(
    conn: sqlite3.Connection,
    handlers: dict[str, ProjectionHandler],
    worker_id: str,
    *,
    max_tasks: int = 10,
    target: str | None = None,
    lease_seconds: int = 60,
    now: datetime | None = None,
) -> int:
    """Process up to ``max_tasks`` pending projections and return the count."""
    processed = 0
    while processed < max_tasks:
        task = claim_projection(
            conn,
            worker_id,
            target=target,
            lease_seconds=lease_seconds,
            now=now,
        )
        if task is None:
            break
        handler = handlers.get(task.target)
        if handler is None:
            try:
                fail_projection(
                    conn,
                    task,
                    worker_id=worker_id,
                    lease_epoch=task.lease_epoch,
                    error=f"no handler for target {task.target!r}",
                    now=now,
                )
            except LeaseLost:
                break
            processed += 1
            continue
        try:
            handler(task)
        except Exception as exc:
            try:
                fail_projection(
                    conn,
                    task,
                    worker_id=worker_id,
                    lease_epoch=task.lease_epoch,
                    error=f"{type(exc).__name__}: {exc}",
                    now=now,
                )
            except LeaseLost:
                break
        else:
            try:
                complete_projection(
                    conn,
                    task,
                    worker_id=worker_id,
                    lease_epoch=task.lease_epoch,
                    now=now,
                )
            except LeaseLost:
                break
        processed += 1
    return processed


def list_projection_tasks(
    conn: sqlite3.Connection, *, status: str | None = None
) -> list[ProjectionTask]:
    if status is None:
        rows = conn.execute(
            "SELECT * FROM projection_tasks ORDER BY created_at, task_id"
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM projection_tasks WHERE status = ? "
            "ORDER BY created_at, task_id",
            (status,),
        ).fetchall()
    return [_row_to_task(row) for row in rows]


def _owns(row: sqlite3.Row, worker_id: str, lease_epoch: int) -> bool:
    return (
        row["status"] == "processing"
        and row["worker_id"] == worker_id
        and int(row["lease_epoch"]) == int(lease_epoch)
    )


def _row_to_task(row: sqlite3.Row) -> ProjectionTask:
    return ProjectionTask(
        task_id=row["task_id"],
        target=row["target"],
        entity_type=row["entity_type"],
        entity_id=row["entity_id"],
        entity_version=row["entity_version"],
        idempotency_key=row["idempotency_key"],
        status=row["status"],
        attempts=int(row["attempts"]),
        max_attempts=int(row["max_attempts"]),
        last_error=row["last_error"],
        created_at=row["created_at"],
        available_at=row["available_at"],
        completed_at=row["completed_at"],
        lease_expires_at=row["lease_expires_at"],
        worker_id=row["worker_id"],
        lease_epoch=int(row["lease_epoch"]),
        payload=json.loads(row["payload_json"] or "{}"),
    )


def _hash_id(prefix: str, basis: str) -> str:
    return prefix + hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16]


def _after(now: datetime, seconds: int) -> str:
    return (now + timedelta(seconds=seconds)).isoformat()

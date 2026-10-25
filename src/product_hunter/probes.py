"""The 24/7 on-call research probe registry.

A probe is a long-lived research request that runs repeated finite research
runs on a cadence, one active run at a time, and accumulates evidence into
canonical opportunities across cycles.

The module mirrors registry.py style: every transition is one transaction,
every state is stored in SQLite, and the worker owns execution while this
module owns the durable probe state.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Iterator

from product_hunter.db import (
    enqueue_job,
    recover_expired_leases,
)
from product_hunter.db.connection import utc_now
from product_hunter.fetch import utcnow as _utcnow
from product_hunter.registry import _latest_checkpoint, _transaction

PROBE_STATE_STATUSES = frozenset(
    {
        "CREATED",
        "RUNNING",
        "PAUSE_REQUESTED",
        "PAUSED",
        "STOP_REQUESTED",
        "STOPPED",
        "FAILED",
    }
)

DISCOVERY_STAGE = "DISCOVERY"
PROBE_INTERVAL_KEY = "cycle_interval_seconds"
PROBE_BACKOFF_KEY = "maximum_backoff_seconds"


class ProbeError(Exception):
    """A probe command was refused."""

    code = "probe_error"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class UnknownProbe(ProbeError):
    """The id names no probe."""

    code = "unknown_probe"


class InvalidTransition(ProbeError):
    """The action does not apply to the current state."""

    code = "invalid_transition"


@dataclass(frozen=True)
class ProbeView:
    """The read model over one probe."""

    public_id: str
    probe_id: str
    status: str
    objective: str
    created_at: str
    updated_at: str
    started_at: str | None
    paused_at: str | None
    stopped_at: str | None
    last_cycle_at: str | None
    next_cycle_at: str | None
    current_run_id: str | None
    total_cycles: int
    successful_cycles: int
    failed_cycles: int
    blocked_cycles: int
    backoff_multiplier: int
    configuration: str
    last_error: str | None
    worker_active: bool
    job_count: int
    checkpoint_count: int
    latest_checkpoint: str = ""
    last_activity: str = ""
    errors: list = None  # type: ignore[assignment]
    active_run: dict | None = None


def _validate_state(status: str) -> None:
    if status.upper() not in PROBE_STATE_STATUSES:
        raise ProbeError(f"unknown probe state {status!r}")


def _normalize_state(value: str | None) -> str:
    text = (value or "").strip().upper()
    if text not in PROBE_STATE_STATUSES:
        return text
    return text


def _resolve_probe_row(conn: sqlite3.Connection, probe_id: str) -> sqlite3.Row:
    row = conn.execute(
        "SELECT * FROM probes WHERE public_id = ?", (probe_id,)
    ).fetchone()
    if row is None:
        row = conn.execute("SELECT * FROM probes WHERE probe_id = ?", (probe_id,)).fetchone()
    if row is None:
        known = conn.execute(
            "SELECT public_id FROM probes ORDER BY created_at DESC LIMIT 10"
        ).fetchall()
        message = f"no probe matches {probe_id!r}"
        if known:
            message += ". Known probes: " + ", ".join(r[0] for r in known)
        raise UnknownProbe(message)
    return row


def _count(conn: sqlite3.Connection, sql: str, params: tuple) -> int:
    row = conn.execute(sql, params).fetchone()
    return int(row[0]) if row is not None else 0


def _view_probe(conn: sqlite3.Connection, row: sqlite3.Row, *, now: datetime) -> ProbeView:
    probe_id = row["probe_id"]
    current_run_id = row["current_run_id"]
    active_run = None
    if current_run_id is not None:
        run_row = conn.execute(
            "SELECT status, current_stage, current_step, progress_percent, updated_at "
            "FROM research_runs WHERE run_id = ?",
            (current_run_id,),
        ).fetchone()
        if run_row is not None:
            job_stages = [
                {"stage": jr["stage"], "status": jr["status"]}
                for jr in conn.execute(
                    "SELECT stage, status FROM jobs WHERE run_id = ? ORDER BY created_at, id",
                    (current_run_id,),
                ).fetchall()
            ]
            tool_calls_spent = _count(
                conn,
                "SELECT count(*) FROM tool_calls WHERE run_id = ?",
                (current_run_id,),
            )
            active_run = {
                "run_id": current_run_id,
                "status": run_row["status"],
                "current_stage": run_row["current_stage"],
                "current_step": run_row["current_step"],
                "progress_percent": int(run_row["progress_percent"] or 0),
                "updated_at": run_row["updated_at"],
                "worker_active": _probe_has_live_lease(conn, probe_id, now=now),
                "job_stages": job_stages,
                "tool_calls_spent": tool_calls_spent,
                "checkpoint": _latest_checkpoint(conn, current_run_id),
            }
    return ProbeView(
        public_id=row["public_id"],
        probe_id=probe_id,
        status=_normalize_state(row["status"]),
        objective=row["objective"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        started_at=row["started_at"],
        paused_at=row["paused_at"],
        stopped_at=row["stopped_at"],
        last_cycle_at=row["last_cycle_at"],
        next_cycle_at=row["next_cycle_at"],
        current_run_id=current_run_id,
        total_cycles=int(row["total_cycles"] or 0),
        successful_cycles=int(row["successful_cycles"] or 0),
        failed_cycles=int(row["failed_cycles"] or 0),
        blocked_cycles=int(row["blocked_cycles"] or 0),
        backoff_multiplier=int(row["backoff_multiplier"] or 1),
        configuration=row["configuration"],
        last_error=row["last_error"],
        worker_active=_probe_has_live_lease(conn, probe_id, now=now),
        job_count=_count(
            conn, "SELECT count(*) FROM jobs WHERE run_id = ?",
            (current_run_id,) if current_run_id else ("",),
        ),
        checkpoint_count=_count(
            conn, "SELECT count(*) FROM research_checkpoints WHERE run_id = ?",
            (current_run_id,) if current_run_id else ("",),
        ),
        errors=[],
        active_run=active_run,
    )


def _probe_has_live_lease(conn: sqlite3.Connection, probe_id: str, *, now: datetime) -> bool:
    row = conn.execute(
        "SELECT 1 FROM jobs j JOIN research_runs r ON j.run_id = r.run_id "
        "WHERE r.probe_id = ? AND j.status = 'leased' AND j.lease_expires_at IS NOT NULL "
        "AND j.lease_expires_at > ? LIMIT 1",
        (probe_id, now.isoformat()),
    ).fetchone()
    return row is not None


def _active_run(conn: sqlite3.Connection, probe_id: str) -> sqlite3.Row | None:
    """The run this probe currently owns, if any."""
    return conn.execute(
        "SELECT * FROM research_runs WHERE probe_id = ? "
        "ORDER BY created_at DESC LIMIT 1",
        (probe_id,),
    ).fetchone()


def create_probe(
    conn: sqlite3.Connection,
    objective: str,
    configuration: str,
    *,
    requester: str | None = None,
    now: datetime | None = None,
):
    """Create one probe in CREATED state. Returns (ProbeView, created)."""
    moment = now if now is not None else _utcnow()
    _validate_state("CREATED")
    objective = " ".join(objective.split())
    if not objective:
        raise ProbeError("a probe needs an objective")
    with _transaction(conn):
        existing = conn.execute(
            "SELECT * FROM probes WHERE objective = ? ORDER BY created_at DESC LIMIT 1",
            (objective,),
        ).fetchone()
        if existing is not None:
            return _view_probe(conn, existing, now=moment), True
        probe_id = _probe_id(objective)
        public_id = _probe_public_id(conn, moment)
        conn.execute(
            """
            INSERT INTO probes (
                probe_id, public_id, status, objective, created_at, updated_at,
                started_at, paused_at, stopped_at, last_cycle_at, next_cycle_at,
                current_run_id, total_cycles, successful_cycles, failed_cycles,
                blocked_cycles, backoff_multiplier, configuration, last_error
            ) VALUES (?, ?, 'CREATED', ?, ?, ?, NULL, NULL, NULL, NULL, NULL,
                NULL, 0, 0, 0, 0, 1, ?, NULL)
            """,
            (
                probe_id,
                public_id,
                objective,
                moment.isoformat(),
                moment.isoformat(),
                configuration,
            ),
        )
    return _view_probe(conn, _resolve_probe_row(conn, public_id), now=moment), False


def _probe_id(objective: str) -> str:
    return "prb_" + hashlib.sha256(objective.encode("utf-8")).hexdigest()[:16]


def _probe_public_id(conn: sqlite3.Connection, moment: datetime) -> str:
    prefix = f"PRB-{moment.strftime('%Y%m%d')}-"
    used = []
    for row in conn.execute(
        "SELECT public_id FROM probes WHERE public_id LIKE ?", (f"{prefix}%",)
    ).fetchall():
        suffix = row["public_id"][len(prefix):]
        if suffix.isdigit():
            used.append(int(suffix))
    return f"{prefix}{(max(used) + 1 if used else 1):04d}"


def get_probe(conn: sqlite3.Connection, probe_id: str) -> ProbeView:
    """One probe, by public id or probe id."""
    return _view_probe(conn, _resolve_probe_row(conn, probe_id), now=_utcnow())


def list_probes(conn: sqlite3.Connection) -> list[ProbeView]:
    """Every probe, newest first."""
    now = _utcnow()
    rows = conn.execute(
        "SELECT * FROM probes ORDER BY created_at DESC, public_id"
    ).fetchall()
    return [_view_probe(conn, row, now=now) for row in rows]


def start_probe(
    conn: sqlite3.Connection, public_id: str, *, requester: str | None = None
) -> ProbeView:
    """CREATED/PAUSED -> RUNNING; set started_at."""
    moment = _utcnow()
    with _transaction(conn):
        row = _resolve_probe_row(conn, public_id)
        status = _normalize_state(row["status"])
        if status not in ("CREATED", "PAUSED"):
            raise InvalidTransition(
                f"probe {public_id} is {status}; only CREATED or PAUSED can start"
            )
        conn.execute(
            "UPDATE probes SET status = 'RUNNING', updated_at = ?, started_at = COALESCE(started_at, ?) WHERE probe_id = ?",
            (moment.isoformat(), moment.isoformat(), row["probe_id"]),
        )
    return get_probe(conn, public_id)


def pause_probe(
    conn: sqlite3.Connection, public_id: str, *, requester: str | None = None
) -> ProbeView:
    """RUNNING -> PAUSE_REQUESTED if an active run has a live lease, else PAUSED."""
    moment = _utcnow()
    with _transaction(conn):
        row = _resolve_probe_row(conn, public_id)
        status = _normalize_state(row["status"])
        if status not in ("RUNNING", "PAUSED"):
            raise InvalidTransition(
                f"probe {public_id} is {status}; only RUNNING or PAUSED can pause"
            )
        run = _active_run(conn, row["probe_id"])
        live = bool(run) and _probe_has_live_lease(conn, row["probe_id"], now=moment)
        target = "PAUSE_REQUESTED" if live else "PAUSED"
        conn.execute(
            "UPDATE probes SET status = ?, updated_at = ?, paused_at = ? WHERE probe_id = ?",
            (target, moment.isoformat(), moment.isoformat(), row["probe_id"]),
        )
        if not live and run:
            # Relinquish the active run: its queued work is cancelled and the
            # probe may start a fresh cycle on resume.
            conn.execute(
                "UPDATE jobs SET status = 'cancelled' WHERE run_id = ? "
                "AND status = 'queued'",
                (run["run_id"],),
            )
            conn.execute(
                "UPDATE probes SET current_run_id = NULL WHERE probe_id = ?",
                (row["probe_id"],),
            )
    return get_probe(conn, public_id)


def resume_probe(
    conn: sqlite3.Connection, public_id: str, *, requester: str | None = None
) -> ProbeView:
    """PAUSED -> RUNNING."""
    moment = _utcnow()
    with _transaction(conn):
        row = _resolve_probe_row(conn, public_id)
        status = _normalize_state(row["status"])
        if status != "PAUSED":
            raise InvalidTransition(
                f"probe {public_id} is {status}; only PAUSED can resume"
            )
        conn.execute(
            "UPDATE probes SET status = 'RUNNING', updated_at = ? WHERE probe_id = ?",
            (moment.isoformat(), row["probe_id"]),
        )
    return get_probe(conn, public_id)


def stop_probe(
    conn: sqlite3.Connection, public_id: str, *, requester: str | None = None
) -> ProbeView:
    """RUNNING/PAUSED -> STOPPED."""
    moment = _utcnow()
    with _transaction(conn):
        row = _resolve_probe_row(conn, public_id)
        status = _normalize_state(row["status"])
        if status not in ("RUNNING", "PAUSED"):
            raise InvalidTransition(
                f"probe {public_id} is {status}; only RUNNING or PAUSED can stop"
            )
        conn.execute(
            "UPDATE probes SET status = 'STOPPED', updated_at = ?, stopped_at = ? WHERE probe_id = ?",
            (moment.isoformat(), moment.isoformat(), row["probe_id"]),
        )
        run = _active_run(conn, row["probe_id"])
        if run:
            conn.execute(
                "UPDATE jobs SET status = 'cancelled' WHERE run_id = ? "
                "AND status = 'queued'",
                (run["run_id"],),
            )
            # The probe is done with this run; a later restart starts fresh.
            conn.execute(
                "UPDATE probes SET current_run_id = NULL WHERE probe_id = ?",
                (row["probe_id"],),
            )
    return get_probe(conn, public_id)


def record_probe_cycle(
    conn: sqlite3.Connection, probe_id: str, run_id: str, outcome: str
) -> ProbeView:
    """Record a completed cycle: update counters, last_cycle_at, next_cycle_at."""
    moment = _utcnow()
    row = _resolve_probe_row(conn, probe_id)
    ok = outcome == "success"
    failed = outcome == "failed"
    blocked = outcome == "blocked"
    total = int(row["total_cycles"] or 0) + 1
    succ = int(row["successful_cycles"] or 0) + (1 if ok else 0)
    fl = int(row["failed_cycles"] or 0) + (1 if failed else 0)
    bl = int(row["blocked_cycles"] or 0) + (1 if blocked else 0)
    multiplier = int(row["backoff_multiplier"] or 1)
    if blocked or failed:
        multiplier = min(multiplier * 2, 32)
    else:
        multiplier = 1
    try:
        configuration = json.loads(row["configuration"] or "{}")
    except (ValueError, TypeError):
        configuration = {}
    interval_seconds = int(configuration.get("cycle_interval_seconds", 300) or 300)
    maximum_backoff_seconds = int(
        configuration.get("maximum_backoff_seconds", 3600) or 3600
    )
    # Backoff: after an empty or low-yield cycle the interval doubles (tracked
    # in backoff_multiplier) up to maximum_backoff_seconds; a productive cycle
    # resets the multiplier to one. next_cycle_at is the next grid moment.
    delay_seconds = min(interval_seconds * multiplier, maximum_backoff_seconds)
    next_cycle_at = moment + timedelta(seconds=delay_seconds)
    with _transaction(conn):
        conn.execute(
            """
            UPDATE probes SET total_cycles = ?, successful_cycles = ?, failed_cycles = ?,
                blocked_cycles = ?, backoff_multiplier = ?, last_cycle_at = ?,
                next_cycle_at = ?, current_run_id = NULL, updated_at = ? WHERE probe_id = ?
            """,
            (
                total, succ, fl, bl, multiplier,
                moment.isoformat(),
                next_cycle_at.isoformat(),
                moment.isoformat(),
                row["probe_id"],
            ),
        )
    return get_probe(conn, row["public_id"])


def get_active_run(conn: sqlite3.Connection, probe_id: str):
    """The run this probe currently owns, if any (run_id or None)."""
    row = _active_run(conn, probe_id)
    return row["run_id"] if row is not None else None


def recover_probes(conn: sqlite3.Connection) -> list[str]:
    """Resolve PAUSE/STOP requests whose worker is gone; recover expired leases.

    Returns the public ids of probes whose state was changed.
    """
    moment = _utcnow()
    changed: list[str] = []
    rows = conn.execute(
        "SELECT probe_id, public_id, status FROM probes "
        "WHERE status IN ('PAUSE_REQUESTED', 'STOP_REQUESTED') "
        "ORDER BY created_at, public_id"
    ).fetchall()
    with _transaction(conn):
        for row in rows:
            probe_id = row["probe_id"]
            if _probe_has_live_lease(conn, probe_id, now=moment):
                continue
            target = "PAUSED" if row["status"] == "PAUSE_REQUESTED" else "STOPPED"
            conn.execute(
                "UPDATE probes SET status = ?, updated_at = ? WHERE probe_id = ?",
                (target, moment.isoformat(), probe_id),
            )
            run = _active_run(conn, probe_id)
            if run:
                conn.execute(
                    "UPDATE jobs SET status = 'cancelled' WHERE run_id = ? AND status = 'queued'",
                    (run["run_id"],),
                )
            changed.append(row["public_id"])
    recover_expired_leases(conn)
    return changed

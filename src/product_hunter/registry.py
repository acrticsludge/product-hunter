"""The state layer of the research registry.

OpenCode asks for work; the worker does the work. This module owns the
durable run state: the request schemas, the transitions the interface may
request, the wording of every reply, and the command line entry point that
both the ``ph`` console script and the OpenCode plugin reach.

The interface never runs a stage and never guesses. A run with no live worker
lease says so in words, and a request that could apply to more than one run
returns the candidates instead of picking one.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Iterator

from pydantic import BaseModel, Field, ValidationError, field_validator

from product_hunter.config import database_path, repo_root
from product_hunter.db import (
    connect,
    enqueue_job,
    list_checkpoints,
    save_checkpoint,
)
from product_hunter.fetch import utcnow

ACTIONS = (
    "create",
    "list",
    "status",
    "continue",
    "resume",
    "pause",
    "retry",
    "cancel",
    "log",
)

CONTRACT_VERSION = "phase2-v1"


class RunState(str, Enum):
    """The run states the interface reads and writes."""

    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    PAUSE_REQUESTED = "PAUSE_REQUESTED"
    PAUSED = "PAUSED"
    WAITING_HUMAN = "WAITING_HUMAN"
    BLOCKED_TOOL = "BLOCKED_TOOL"
    FAILED_RETRYABLE = "FAILED_RETRYABLE"
    FAILED_PERMANENT = "FAILED_PERMANENT"
    COMPLETED = "COMPLETED"
    CANCEL_REQUESTED = "CANCEL_REQUESTED"
    CANCELLED = "CANCELLED"


TERMINAL = frozenset({RunState.COMPLETED, RunState.CANCELLED})
_TERMINAL = frozenset(state.value for state in TERMINAL)

# A stored value from an earlier phase maps onto the same state. A value with
# no mapping is reported as it stands, so a later state does not break the
# interface.
_STATE_ALIASES = {
    "COMPLETED": RunState.COMPLETED,
    "FAILED": RunState.FAILED_RETRYABLE,
    "CANCELLED": RunState.CANCELLED,
    "RUNNING": RunState.RUNNING,
    "QUEUED": RunState.QUEUED,
    "PAUSED": RunState.PAUSED,
}

# The list order: running, paused, failed, waiting, blocked, then the rest.
_STATE_RANK = {
    RunState.RUNNING.value: 0,
    RunState.PAUSED.value: 1,
    RunState.PAUSE_REQUESTED.value: 1,
    RunState.FAILED_RETRYABLE.value: 2,
    RunState.WAITING_HUMAN.value: 3,
    RunState.BLOCKED_TOOL.value: 4,
}

# The states a retry may clear.
_RETRYABLE = frozenset(
    {
        RunState.FAILED_RETRYABLE.value,
        RunState.FAILED_PERMANENT.value,
        RunState.BLOCKED_TOOL.value,
    }
)

# The states that hold a request the worker has not carried out yet. A run in
# one of them is not eligible to continue: the request is already outstanding.
_REQUESTED = frozenset(
    {
        RunState.CANCEL_REQUESTED.value,
        RunState.PAUSE_REQUESTED.value,
    }
)


# --- schemas ---------------------------------------------------------------


class CreateRequest(BaseModel):
    """A request to start one research run."""

    objective: str = Field(min_length=3, max_length=2000)
    chat_id: str | None = None

    @field_validator("objective", mode="before")
    @classmethod
    def _strip(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


class TargetRequest(BaseModel):
    """A request that names one run."""

    research_id: str = Field(min_length=3, max_length=128, pattern=r"^[A-Za-z0-9._-]+$")


class LogRequest(TargetRequest):
    """A request for the history of one run."""

    limit: int = Field(default=50, ge=1, le=500)


class ListRequest(BaseModel):
    """A request for the runs."""

    limit: int = Field(default=50, ge=1, le=500)


class RunView(BaseModel):
    """The read model over one run."""

    public_id: str
    run_id: str
    objective: str
    status: str
    current_stage: str | None
    current_step: str | None
    progress_percent: int
    resume_count: int
    created_at: str
    updated_at: str
    started_at: str | None
    completed_at: str | None
    last_heartbeat: str | None
    last_error: str | None
    chat_id: str | None
    worker_active: bool
    job_count: int
    checkpoint_count: int
    event_count: int
    artifact_count: int


class CommandResult(BaseModel):
    """The single response shape every command returns."""

    ok: bool
    action: str
    message: str
    text: str = ""
    run: RunView | None = None
    runs: list[RunView] = Field(default_factory=list)
    needs_selection: bool = False
    code: str | None = None


# --- errors ----------------------------------------------------------------


class RegistryError(Exception):
    """A command was refused."""

    code = "internal_error"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class UnknownResearch(RegistryError):
    """The id names no run."""

    code = "unknown_research"


class InvalidRequest(RegistryError):
    """The arguments do not satisfy the schema."""

    code = "invalid_request"


class InvalidTransition(RegistryError):
    """The action does not apply to the current state."""

    code = "invalid_transition"


# --- reading ---------------------------------------------------------------

# The ceiling on how many rows a listing returns. The request schemas cap the
# limit too, but a caller inside the process can pass any number.
MAX_LIMIT = 500


def _clamp_limit(limit: int | None) -> int:
    """The limit as it is applied: never below zero, never above the ceiling.

    No limit at all means the ceiling.
    """
    if limit is None:
        return MAX_LIMIT
    return max(0, min(int(limit), MAX_LIMIT))


_RUN_SQL = """
SELECT r.*,
  (SELECT count(*) FROM jobs j WHERE j.run_id = r.run_id) AS job_count,
  (SELECT count(*) FROM research_checkpoints c WHERE c.run_id = r.run_id)
    AS checkpoint_count,
  (SELECT count(*) FROM research_events e WHERE e.run_id = r.run_id) AS event_count,
  (SELECT count(*) FROM research_artifacts a WHERE a.run_id = r.run_id)
    AS artifact_count
FROM research_runs r
"""


def _normalize_state(value: str | None) -> str:
    """The stored status, uppercased and mapped onto a known state."""
    text = (value or "").strip().upper()
    alias = _STATE_ALIASES.get(text)
    return alias.value if alias is not None else text


def _has_live_lease(conn: sqlite3.Connection, run_id: str, *, now: datetime) -> bool:
    """Whether a worker holds this run.

    A worker holds a live lease when a job of that run is leased and its lease
    has not expired. An expired lease means the run is not executing.
    """
    row = conn.execute(
        "SELECT 1 FROM jobs WHERE run_id = ? AND status = 'leased' "
        "AND lease_expires_at IS NOT NULL AND lease_expires_at > ? LIMIT 1",
        (run_id, now.isoformat()),
    ).fetchone()
    return row is not None


def _view(conn: sqlite3.Connection, row: sqlite3.Row, *, now: datetime) -> RunView:
    return RunView(
        public_id=row["public_id"],
        run_id=row["run_id"],
        objective=row["objective"],
        status=_normalize_state(row["status"]),
        current_stage=row["current_stage"],
        current_step=row["current_step"],
        progress_percent=int(row["progress_percent"] or 0),
        resume_count=int(row["resume_count"] or 0),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        started_at=row["started_at"],
        completed_at=row["completed_at"],
        last_heartbeat=row["last_heartbeat"],
        last_error=row["last_error"],
        chat_id=row["chat_id"],
        worker_active=_has_live_lease(conn, row["run_id"], now=now),
        job_count=int(row["job_count"]),
        checkpoint_count=int(row["checkpoint_count"]),
        event_count=int(row["event_count"]),
        artifact_count=int(row["artifact_count"]),
    )


def _known_ids(conn: sqlite3.Connection) -> list[str]:
    """The public ids that do exist, capped at ten."""
    rows = conn.execute(
        "SELECT public_id FROM research_runs ORDER BY created_at DESC, public_id"
    ).fetchall()
    return [row["public_id"] for row in rows[:10]]


def _resolve_row(conn: sqlite3.Connection, research_id: str) -> sqlite3.Row:
    """The run row for a public id or a run id."""
    row = conn.execute(
        _RUN_SQL + " WHERE r.public_id = ?", (research_id,)
    ).fetchone()
    if row is None:
        row = conn.execute(
            _RUN_SQL + " WHERE r.run_id = ?", (research_id,)
        ).fetchone()
    if row is None:
        known = _known_ids(conn)
        message = f"no research run matches {research_id!r}"
        if known:
            message += ". Known runs: " + ", ".join(known)
        raise UnknownResearch(message)
    return row


def _all_views(conn: sqlite3.Connection, *, now: datetime) -> list[RunView]:
    """Every run, in the list order."""
    views = [_view(conn, row, now=now) for row in conn.execute(_RUN_SQL).fetchall()]
    views.sort(key=lambda view: view.public_id)
    views.sort(key=lambda view: view.updated_at, reverse=True)
    views.sort(key=lambda view: _STATE_RANK.get(view.status, 5))
    return views


def list_runs(
    conn: sqlite3.Connection, *, status: str | None = None, limit: int | None = 50
) -> list[RunView]:
    """The runs, ordered by state and then by the most recent activity.

    The limit never returns more than MAX_LIMIT runs, and no limit at all
    returns the ceiling.
    """
    views = _all_views(conn, now=utcnow())
    if status is not None:
        wanted = _normalize_state(status)
        views = [view for view in views if view.status == wanted]
    return views[: _clamp_limit(limit)]


def get_run(conn: sqlite3.Connection, research_id: str) -> RunView:
    """One run, by public id or run id."""
    return _view(conn, _resolve_row(conn, research_id), now=utcnow())


def eligible_runs(conn: sqlite3.Connection) -> list[RunView]:
    """The runs a bare continue may act on.

    A run is eligible when it is not terminal, not permanently failed, not
    waiting for the worker to carry out a cancel or a pause, and no worker
    holds a live lease on it.
    """
    views = _all_views(conn, now=utcnow())
    return [
        view
        for view in views
        if view.status not in _TERMINAL
        and view.status != RunState.FAILED_PERMANENT.value
        and view.status not in _REQUESTED
        and not view.worker_active
    ]


def run_log(
    conn: sqlite3.Connection, research_id: str, *, limit: int = 50
) -> dict[str, object]:
    """The run, its events, its checkpoints, its jobs, and its stage attempts.

    Every list is bounded by the same limit, and the jobs are the ones of
    this run: the log never loads the jobs of the whole database.
    """
    row = _resolve_row(conn, research_id)
    bounded = _clamp_limit(limit)
    events = [
        dict(event)
        for event in conn.execute(
            "SELECT * FROM research_events WHERE run_id = ? "
            "ORDER BY created_at DESC, event_id DESC LIMIT ?",
            (row["run_id"], bounded),
        ).fetchall()
    ]
    checkpoints = [
        asdict(checkpoint)
        for checkpoint in list_checkpoints(conn, run_id=row["run_id"])[:bounded]
    ]
    jobs = [
        dict(job)
        for job in conn.execute(
            "SELECT * FROM jobs WHERE run_id = ? ORDER BY created_at, id LIMIT ?",
            (row["run_id"], bounded),
        ).fetchall()
    ]
    attempts = [
        dict(attempt)
        for attempt in conn.execute(
            "SELECT * FROM stage_attempts WHERE run_id = ? "
            "ORDER BY started_at, attempt LIMIT ?",
            (row["run_id"], bounded),
        ).fetchall()
    ]
    return {
        "run": _view(conn, row, now=utcnow()),
        "events": events,
        "checkpoints": checkpoints,
        "jobs": jobs,
        "attempts": attempts,
    }


# --- writing ---------------------------------------------------------------


@contextmanager
def _transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """One transaction. A failure leaves the run as it was."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
        conn.execute("COMMIT")
    except BaseException:
        try:
            conn.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        raise


def _fold(text: str) -> str:
    """The objective as it is compared: whitespace collapsed, case folded."""
    return " ".join(text.split()).casefold()


def _next_public_id(conn: sqlite3.Connection, moment: datetime) -> str:
    """The next free public id for that day."""
    prefix = f"RH-{moment.strftime('%Y%m%d')}-"
    used = []
    for row in conn.execute(
        "SELECT public_id FROM research_runs WHERE public_id LIKE ?", (f"{prefix}%",)
    ).fetchall():
        suffix = row["public_id"][len(prefix) :]
        if suffix.isdigit():
            used.append(int(suffix))
    return f"{prefix}{(max(used) + 1 if used else 1):04d}"


def _run_id(public_id: str) -> str:
    """The internal run id, derived from the public id."""
    return "run_" + hashlib.sha256(public_id.encode("utf-8")).hexdigest()[:16]


def _insert_event(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    event_type: str,
    now: datetime,
    stage: str | None = None,
    agent: str | None = None,
    payload: dict[str, object] | None = None,
) -> str:
    """Append one event to the run history.

    The stage is where the run stood when the event was written, and the agent
    is who asked for it. Both are empty when the caller knows neither.
    """
    seq = int(
        conn.execute(
            "SELECT count(*) FROM research_events WHERE run_id = ?", (run_id,)
        ).fetchone()[0]
    )
    event_id = "evt_" + hashlib.sha256(
        f"{run_id}|{event_type}|{seq}".encode("utf-8")
    ).hexdigest()[:16]
    conn.execute(
        "INSERT INTO research_events (event_id, run_id, event_type, stage, tool, "
        "agent, payload_ref, payload_json, created_at) "
        "VALUES (?, ?, ?, ?, NULL, ?, NULL, ?, ?)",
        (
            event_id,
            run_id,
            event_type,
            stage,
            agent,
            json.dumps(payload or {}, sort_keys=True),
            now.isoformat(),
        ),
    )
    return event_id


def _reusable_row(conn: sqlite3.Connection, objective: str) -> sqlite3.Row | None:
    """The live run that already holds this objective, if there is one."""
    wanted = _fold(objective)
    for row in conn.execute(
        _RUN_SQL + " ORDER BY r.created_at DESC, r.public_id"
    ).fetchall():
        if _fold(row["objective"]) != wanted:
            continue
        if _normalize_state(row["status"]) in _TERMINAL:
            continue
        return row
    return None


def create_run(
    conn: sqlite3.Connection,
    request: CreateRequest,
    *,
    now: datetime | None = None,
) -> tuple[RunView, bool]:
    """Create one run, or return the live run that already holds the objective.

    The run row, the first checkpoint, the first event, and the first job are
    written in one transaction. The second value says whether the run already
    existed, so the caller never looks for it a second time.
    """
    moment = now if now is not None else utcnow()
    with _transaction(conn):
        existing = _reusable_row(conn, request.objective)
        if existing is not None:
            return _view(conn, existing, now=moment), True
        objective = " ".join(request.objective.split())
        public_id = _next_public_id(conn, moment)
        run_id = _run_id(public_id)
        conn.execute(
            """
            INSERT INTO research_runs (
                run_id, public_id, objective, original_request, status,
                current_stage, current_step, priority, created_at, started_at,
                updated_at, last_heartbeat, completed_at, last_error,
                contract_version, agent_version, model_policy_version,
                chat_id, workspace_id, parent_run_id, resume_count,
                progress_percent, lease_epoch, budget_json
            ) VALUES (?, ?, ?, ?, 'QUEUED', 'PLAN', 'research_requested', 0, ?,
                NULL, ?, NULL, NULL, NULL, ?, NULL, NULL, ?, NULL, NULL, 0, 0, 0,
                NULL)
            """,
            (
                run_id,
                public_id,
                objective,
                request.objective,
                moment.isoformat(),
                moment.isoformat(),
                CONTRACT_VERSION,
                request.chat_id,
            ),
        )
        save_checkpoint(
            conn,
            run_id=run_id,
            stage="PLAN",
            step="research_requested",
            state={"objective": objective},
            now=moment,
        )
        _insert_event(
            conn,
            run_id=run_id,
            event_type="RESEARCH_STARTED",
            now=moment,
            stage="PLAN",
            agent=request.chat_id,
            payload={"public_id": public_id, "objective": objective},
        )
        enqueue_job(
            conn,
            stage="DISCOVERY",
            idempotency_key=f"research_start:{run_id}",
            run_id=run_id,
            priority=0,
            payload={"public_id": public_id, "objective": objective},
            now=moment,
        )
    return get_run(conn, public_id), False


def _pending_job(
    conn: sqlite3.Connection, run_id: str, *, now: datetime
) -> sqlite3.Row | None:
    """The job of that run that is still waiting to be done.

    A queued job is waiting. A leased job whose lease has expired is waiting
    too: the worker that held it is gone, so the job has to run again. A
    leased job with a live lease is not waiting: that worker is still on it.
    """
    return conn.execute(
        "SELECT id FROM jobs WHERE run_id = ? AND (status = 'queued' OR "
        "(status = 'leased' AND lease_expires_at IS NOT NULL "
        "AND lease_expires_at <= ?)) ORDER BY created_at, id LIMIT 1",
        (run_id, now.isoformat()),
    ).fetchone()


def _queue_continuation(
    conn: sqlite3.Connection, row: sqlite3.Row, *, now: datetime
) -> int:
    """Queue the run again and raise its resume count. Returns the new count.

    A run whose work is already pending is requeued, not given a second job:
    two jobs would run the stage twice and duplicate the evidence. Pending
    work is a queued job and a leased job whose lease has expired. The lease
    epoch of that job rises by one, so a stale worker cannot claim it.
    """
    count = int(row["resume_count"] or 0) + 1
    conn.execute(
        "UPDATE research_runs SET status = 'QUEUED', resume_count = ?, updated_at = ? "
        "WHERE run_id = ?",
        (count, now.isoformat(), row["run_id"]),
    )
    pending = _pending_job(conn, row["run_id"], now=now)
    if pending is None:
        enqueue_job(
            conn,
            stage="DISCOVERY",
            idempotency_key=f"research_continue:{row['run_id']}:{count}",
            run_id=row["run_id"],
            priority=0,
            payload={"public_id": row["public_id"], "resume_count": count},
            now=now,
        )
    else:
        conn.execute(
            "UPDATE jobs SET status = 'queued', worker_id = NULL, "
            "lease_expires_at = NULL, lease_epoch = lease_epoch + 1, "
            "scheduled_for = ? WHERE id = ?",
            (now.isoformat(), pending["id"]),
        )
    return count


def continue_run(
    conn: sqlite3.Connection, research_id: str, *, requester: str | None = None
) -> RunView:
    """Queue a run that stopped, and let the worker take it again."""
    moment = utcnow()
    with _transaction(conn):
        row = _resolve_row(conn, research_id)
        status = _normalize_state(row["status"])
        if status in _TERMINAL:
            raise InvalidTransition(
                f"run {row['public_id']} is {status} and cannot continue"
            )
        if status == RunState.FAILED_PERMANENT.value:
            raise InvalidTransition(
                f"run {row['public_id']} failed permanently; retry it instead"
            )
        if status == RunState.CANCEL_REQUESTED.value:
            # A cancel the user asked for is not undone by a continue. The
            # worker owns the transition out of this state.
            raise InvalidTransition(
                f"run {row['public_id']} is {status}; the worker finishes the cancel"
            )
        if _has_live_lease(conn, row["run_id"], now=moment):
            raise InvalidTransition(
                f"run {row['public_id']} has a live worker lease and is already running"
            )
        _queue_continuation(conn, row, now=moment)
        _insert_event(
            conn,
            run_id=row["run_id"],
            event_type="RESEARCH_RESUMED",
            now=moment,
            stage=row["current_stage"],
            agent=requester,
        )
    return get_run(conn, row["public_id"])


def resume_run(
    conn: sqlite3.Connection, research_id: str, *, requester: str | None = None
) -> RunView:
    """Resume a paused run.

    A run the interface paused itself is paused too: the worker owns the
    transition to PAUSED, and the request waits for it.

    A resume obeys the same rule as a continue: the run holds at most one
    pending job. Work that is already pending is requeued, and a worker that
    holds a live lease on the run keeps it: the resume is refused.
    """
    moment = utcnow()
    with _transaction(conn):
        row = _resolve_row(conn, research_id)
        status = _normalize_state(row["status"])
        if status not in (RunState.PAUSED.value, RunState.PAUSE_REQUESTED.value):
            raise InvalidTransition(
                f"run {row['public_id']} is {status}; only a paused run can resume"
            )
        if _has_live_lease(conn, row["run_id"], now=moment):
            raise InvalidTransition(
                f"run {row['public_id']} has a live worker lease and is already running"
            )
        _queue_continuation(conn, row, now=moment)
        _insert_event(
            conn,
            run_id=row["run_id"],
            event_type="RESEARCH_RESUMED",
            now=moment,
            stage=row["current_stage"],
            agent=requester,
        )
    return get_run(conn, row["public_id"])


def pause_run(
    conn: sqlite3.Connection, research_id: str, *, requester: str | None = None
) -> RunView:
    """Stop a run.

    A run with a live worker is only asked to pause: the worker owns the
    transition. A run with no live worker is stopped, because a pause request
    nobody acknowledges would leave a run that looks alive.
    """
    moment = utcnow()
    with _transaction(conn):
        row = _resolve_row(conn, research_id)
        status = _normalize_state(row["status"])
        if status in _TERMINAL:
            raise InvalidTransition(
                f"run {row['public_id']} is {status} and cannot pause"
            )
        live = _has_live_lease(conn, row["run_id"], now=moment)
        conn.execute(
            "UPDATE research_runs SET status = ?, updated_at = ? WHERE run_id = ?",
            (
                RunState.PAUSE_REQUESTED.value if live else RunState.PAUSED.value,
                moment.isoformat(),
                row["run_id"],
            ),
        )
        _insert_event(
            conn,
            run_id=row["run_id"],
            event_type="RESEARCH_PAUSE_REQUESTED" if live else "RESEARCH_PAUSED",
            now=moment,
            stage=row["current_stage"],
            agent=requester,
        )
    return get_run(conn, row["public_id"])


def retry_run(
    conn: sqlite3.Connection, research_id: str, *, requester: str | None = None
) -> RunView:
    """Requeue the failed work of a run.

    The lease epoch of every requeued job rises by one, so a stale worker
    cannot complete the job it lost.
    """
    moment = utcnow()
    with _transaction(conn):
        row = _resolve_row(conn, research_id)
        status = _normalize_state(row["status"])
        if status not in _RETRYABLE:
            raise InvalidTransition(
                f"run {row['public_id']} is {status}; only a failed or blocked "
                "run can be retried"
            )
        conn.execute(
            "UPDATE jobs SET status = 'queued', attempts = 0, worker_id = NULL, "
            "lease_expires_at = NULL, lease_epoch = lease_epoch + 1, "
            "scheduled_for = ? WHERE run_id = ? AND status IN ('failed', 'dead')",
            (moment.isoformat(), row["run_id"]),
        )
        conn.execute(
            "UPDATE research_runs SET status = 'QUEUED', updated_at = ? WHERE run_id = ?",
            (moment.isoformat(), row["run_id"]),
        )
        _insert_event(
            conn,
            run_id=row["run_id"],
            event_type="RESEARCH_RETRIED",
            now=moment,
            stage=row["current_stage"],
            agent=requester,
        )
    return get_run(conn, row["public_id"])


def cancel_run(
    conn: sqlite3.Connection, research_id: str, *, requester: str | None = None
) -> RunView:
    """Cancel a run. The history stays: nothing is deleted."""
    moment = utcnow()
    with _transaction(conn):
        row = _resolve_row(conn, research_id)
        status = _normalize_state(row["status"])
        if status in _TERMINAL:
            raise InvalidTransition(
                f"run {row['public_id']} is {status} and cannot be cancelled"
            )
        live = _has_live_lease(conn, row["run_id"], now=moment)
        conn.execute(
            "UPDATE research_runs SET status = ?, updated_at = ? WHERE run_id = ?",
            (
                RunState.CANCEL_REQUESTED.value if live else RunState.CANCELLED.value,
                moment.isoformat(),
                row["run_id"],
            ),
        )
        if not live:
            conn.execute(
                "UPDATE jobs SET status = 'cancelled' WHERE run_id = ? "
                "AND status = 'queued'",
                (row["run_id"],),
            )
        _insert_event(
            conn,
            run_id=row["run_id"],
            event_type="RESEARCH_CANCEL_REQUESTED" if live else "RESEARCH_CANCELLED",
            now=moment,
            stage=row["current_stage"],
            agent=requester,
        )
    return get_run(conn, row["public_id"])


# --- formatting ------------------------------------------------------------


def _last_activity(run: RunView) -> str:
    """When the run was last touched."""
    return run.last_heartbeat or run.updated_at


# What a state that only holds a request means for the reader. The worker owns
# the transition out of these states, so the text says that the request waits
# for the worker instead of pretending the run already stopped.
_STATE_NOTES = {
    RunState.CANCEL_REQUESTED.value: (
        "cancel requested; the worker stops the run when it sees the request"
    ),
    RunState.PAUSE_REQUESTED.value: (
        "pause requested; the worker pauses the run when it sees the request"
    ),
}


def render_run(run: RunView) -> str:
    """The block that describes one run."""
    lines = [
        f"{run.public_id}  {run.objective}",
        f"  state: {run.status}   stage: {run.current_stage or '-'}   "
        f"progress: {run.progress_percent}%   resumes: {run.resume_count}",
        f"  worker: {'running' if run.worker_active else 'not running'}",
        f"  updated: {run.updated_at}",
    ]
    note = _STATE_NOTES.get(run.status)
    if note:
        lines.insert(2, f"  note: {note}")
    return "\n".join(lines)


def render_runs(runs: list[RunView]) -> str:
    """The block that lists the runs, one line each."""
    if not runs:
        return "no research runs"
    lines = [f"{len(runs)} research run(s)"]
    for run in runs:
        lines.append(
            f"{run.public_id}  {run.objective}  {run.status}  "
            f"{run.current_stage or '-'}  {run.progress_percent}%  "
            f"last {_last_activity(run)}"
        )
    return "\n".join(lines)


def render_log(payload: dict[str, object]) -> str:
    """The block that shows the history of one run."""
    run = payload["run"]
    lines = [render_run(run), "events:"]
    for event in payload["events"]:
        lines.append(f"  {event['created_at']}  {event['event_type']}")
    if not payload["events"]:
        lines.append("  (none)")
    lines.append("checkpoints:")
    for checkpoint in payload["checkpoints"]:
        lines.append(
            f"  {checkpoint['stage']}/{checkpoint['step']}  "
            f"v{checkpoint['checkpoint_version']}  {checkpoint['created_at']}"
        )
    if not payload["checkpoints"]:
        lines.append("  (none)")
    lines.append("jobs:")
    for job in payload["jobs"]:
        lines.append(
            f"  {job['id']}  {job['stage']}  {job['status']}  "
            f"attempts {job['attempts']}"
        )
    if not payload["jobs"]:
        lines.append("  (none)")
    lines.append("attempts:")
    for attempt in payload["attempts"]:
        lines.append(
            f"  {attempt['job_id']}  attempt {attempt['attempt']}  {attempt['status']}"
        )
    if not payload["attempts"]:
        lines.append("  (none)")
    return "\n".join(lines)


# --- the dispatcher --------------------------------------------------------


def _validated(model: type[BaseModel], **kwargs: object) -> BaseModel:
    """Validate the request, or refuse it with the offending value named."""
    try:
        return model(**kwargs)
    except ValidationError as exc:
        problems = []
        for error in exc.errors():
            where = ".".join(str(part) for part in error["loc"]) or "argument"
            problems.append(f"{where}: {error['msg']} (got {error.get('input')!r})")
        raise InvalidRequest("; ".join(problems)) from exc


def _record_refusal(
    conn: sqlite3.Connection,
    *,
    action: str,
    research_id: str | None,
    requester: str | None,
    error: RegistryError,
) -> None:
    """Leave a durable trace of a refused command.

    The event names the run, the action, the requester and the reason. It is
    written in its own transaction, after the refused transaction rolled back,
    so the refusal survives and the state of the run stays as it was. A
    refusal against an id that names no run cannot be recorded: the event
    table points at a run.
    """
    if action == "create" or not research_id:
        return
    try:
        row = _resolve_row(conn, research_id)
    except UnknownResearch:
        return
    try:
        with _transaction(conn):
            _insert_event(
                conn,
                run_id=row["run_id"],
                event_type="RESEARCH_REQUEST_REFUSED",
                now=utcnow(),
                stage=row["current_stage"],
                agent=requester,
                payload={
                    "action": action,
                    "code": error.code,
                    "reason": error.message,
                },
            )
    except sqlite3.Error:
        # A refusal that cannot be written is still a refusal.
        pass


def execute(
    conn: sqlite3.Connection,
    action: str,
    *,
    research_id: str | None = None,
    objective: str | None = None,
    chat_id: str | None = None,
    limit: int = 50,
) -> CommandResult:
    """Run one command. It never raises: a refusal is a failed result."""
    try:
        return _execute(
            conn,
            action,
            research_id=research_id,
            objective=objective,
            chat_id=chat_id,
            limit=limit,
        )
    except RegistryError as exc:
        _record_refusal(
            conn,
            action=action,
            research_id=research_id,
            requester=chat_id,
            error=exc,
        )
        return CommandResult(
            ok=False, action=action, message=exc.message, text=exc.message, code=exc.code
        )
    except Exception as exc:  # pragma: no cover - the guard for a broken store
        message = f"{type(exc).__name__}: {exc}"
        return CommandResult(
            ok=False,
            action=action,
            message=message,
            text=message,
            code="internal_error",
        )


def _execute(
    conn: sqlite3.Connection,
    action: str,
    *,
    research_id: str | None,
    objective: str | None,
    chat_id: str | None,
    limit: int,
) -> CommandResult:
    # The action is checked first: a wrong action is named as such, not as a
    # wrong id.
    if action not in ACTIONS:
        raise InvalidRequest(
            f"unknown action {action!r}; expected one of {', '.join(ACTIONS)}"
        )

    if action == "create":
        request = _validated(
            CreateRequest, objective=objective or "", chat_id=chat_id
        )
        run, reused = create_run(conn, request)
        message = (
            f"run {run.public_id} already holds this objective; reused it"
            if reused
            else f"created run {run.public_id}"
        )
        return CommandResult(
            ok=True, action="create", message=message, text=render_run(run), run=run
        )

    if action == "list":
        request = _validated(ListRequest, limit=limit)
        runs = list_runs(conn, limit=request.limit)
        return CommandResult(
            ok=True,
            action="list",
            message=f"{len(runs)} research run(s)",
            text=render_runs(runs),
            runs=runs,
        )

    if action == "log":
        request = _validated(
            LogRequest, research_id=research_id or "", limit=limit
        )
        payload = run_log(conn, request.research_id, limit=request.limit)
        return CommandResult(
            ok=True,
            action="log",
            message=f"history of run {request.research_id}",
            text=render_log(payload),
            run=payload["run"],
        )

    if action == "continue" and research_id is None:
        candidates = eligible_runs(conn)
        if not candidates:
            message = "no research run is eligible to continue"
            return CommandResult(
                ok=True,
                action="continue",
                message=message,
                text=message,
            )
        if len(candidates) > 1:
            return CommandResult(
                ok=True,
                action="continue",
                message="more than one run is eligible; pick one",
                text=render_runs(candidates),
                runs=candidates,
                needs_selection=True,
            )
        research_id = candidates[0].public_id

    target = _validated(TargetRequest, research_id=research_id or "")
    if action == "status":
        run = get_run(conn, target.research_id)
        return CommandResult(
            ok=True,
            action="status",
            message=f"run {run.public_id} is {run.status}",
            text=render_run(run),
            run=run,
        )
    if action == "continue":
        run = continue_run(conn, target.research_id, requester=chat_id)
        return CommandResult(
            ok=True,
            action="continue",
            message=f"run {run.public_id} is queued again",
            text=render_run(run),
            run=run,
        )
    if action == "resume":
        run = resume_run(conn, target.research_id, requester=chat_id)
        return CommandResult(
            ok=True,
            action="resume",
            message=f"run {run.public_id} is queued again",
            text=render_run(run),
            run=run,
        )
    if action == "pause":
        run = pause_run(conn, target.research_id, requester=chat_id)
        return CommandResult(
            ok=True,
            action="pause",
            message=f"run {run.public_id} is {run.status}",
            text=render_run(run),
            run=run,
        )
    if action == "retry":
        run = retry_run(conn, target.research_id, requester=chat_id)
        return CommandResult(
            ok=True,
            action="retry",
            message=f"run {run.public_id} is queued again",
            text=render_run(run),
            run=run,
        )
    if action == "cancel":
        run = cancel_run(conn, target.research_id, requester=chat_id)
        return CommandResult(
            ok=True,
            action="cancel",
            message=f"run {run.public_id} is {run.status}",
            text=render_run(run),
            run=run,
        )
    raise InvalidRequest(f"action {action!r} has no handler")


# --- the command line entry point ------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="product_hunter.registry",
        description="Control the research runs of Product Hunter.",
    )
    parser.add_argument("action", help="one of: " + ", ".join(ACTIONS))
    parser.add_argument(
        "target",
        nargs="?",
        default=None,
        help="the research id, or the objective for create",
    )
    parser.add_argument("--objective", default=None)
    parser.add_argument("--research-id", dest="research_id", default=None)
    parser.add_argument("--chat-id", dest="chat_id", default=None)
    parser.add_argument("--limit", type=int, default=50)
    return parser


def main(argv: list[str] | None = None, *, db_path: str | Path | None = None) -> int:
    """Run one command and print one JSON object. Returns 0, or 1 on failure."""
    arguments = list(argv) if argv is not None else sys.argv[1:]
    if arguments and arguments[0] == "research":
        arguments = arguments[1:]
    args = _build_parser().parse_args(arguments)

    action = args.action
    if action == "create":
        objective = args.objective if args.objective is not None else args.target
        research_id = None
    else:
        objective = args.objective
        research_id = args.research_id if args.research_id is not None else args.target

    path = Path(db_path) if db_path is not None else database_path(repo_root())
    if not path.is_file():
        # The path stays out of the message: it can point anywhere, and the
        # text reaches the model.
        message = (
            "the research database does not exist yet. Create it with `ph db init`."
        )
        print(
            CommandResult(
                ok=False, action=action, message=message, text=message, code="database_missing"
            ).model_dump_json()
        )
        return 1

    conn = connect(path)
    try:
        result = execute(
            conn,
            action,
            research_id=research_id,
            objective=objective,
            chat_id=args.chat_id,
            limit=args.limit,
        )
    finally:
        conn.close()
    print(result.model_dump_json())
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

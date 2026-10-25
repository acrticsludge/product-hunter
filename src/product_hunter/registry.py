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

from product_hunter import redaction
from product_hunter.config import database_path, repo_root
from product_hunter.db import (
    connect,
    enqueue_job,
    list_checkpoints,
    recover_expired_leases,
    save_checkpoint,
)
from product_hunter.db.connection import with_busy_retry
from product_hunter.fetch import utcnow
from product_hunter.research.contract import ContractError, load_research_contract

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
    "doctor",
    "recover",
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

# The states the interface knows. A stored status outside this set is
# malformed: it is reported as it stands and it is never handed to a worker.
_KNOWN_STATES = frozenset(state.value for state in RunState)

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

# The states a retry may clear. A permanent failure is not one of them: the
# same objective again fails the same way.
_RETRYABLE = frozenset(
    {
        RunState.FAILED_RETRYABLE.value,
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
    # The five fields below cost a handful of queries each, so they are
    # filled for one run at a time and not for a listing.
    budget: str = ""
    blockers: list[str] = Field(default_factory=list)
    latest_checkpoint: str = ""
    last_activity: str = ""
    errors: list[str] = Field(default_factory=list)


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


def _has_expired_lease(
    conn: sqlite3.Connection, run_id: str, *, now: datetime
) -> bool:
    """Whether the lease of that run expired: the worker is gone."""
    row = conn.execute(
        "SELECT 1 FROM jobs WHERE run_id = ? AND status = 'leased' "
        "AND lease_expires_at IS NOT NULL AND lease_expires_at <= ? LIMIT 1",
        (run_id, now.isoformat()),
    ).fetchone()
    return row is not None


def _any_live_lease(conn: sqlite3.Connection, *, now: datetime) -> bool:
    """Whether any worker holds a live lease on any run."""
    row = conn.execute(
        "SELECT 1 FROM jobs WHERE status = 'leased' AND lease_expires_at IS NOT NULL "
        "AND lease_expires_at > ? LIMIT 1",
        (now.isoformat(),),
    ).fetchone()
    return row is not None


def _count(conn: sqlite3.Connection, sql: str, params: tuple[object, ...]) -> int:
    """The number of rows a query counts."""
    row = conn.execute(sql, params).fetchone()
    return int(row[0]) if row is not None else 0


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
        # The error is masked here, so the raw text never enters a view. The
        # view is what the plugin hands to the model beside the message.
        last_error=(
            redaction.redact(row["last_error"]) if row["last_error"] else None
        ),
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
        "SELECT public_id FROM research_runs ORDER BY created_at DESC, public_id LIMIT 10"
    ).fetchall()
    return [row["public_id"] for row in rows]


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


class _Newest:
    """A value that sorts the other way round.

    The list order mixes directions: the state rank rises, the update time
    falls. One tuple key carries both when the falling part is wrapped.
    """

    __slots__ = ("value",)

    def __init__(self, value: str) -> None:
        self.value = value

    def __eq__(self, other: object) -> bool:
        # A tuple key compares with equality first, so two runs of the same
        # update time have to compare equal for the tie break to be reached.
        return isinstance(other, _Newest) and other.value == self.value

    def __lt__(self, other: "_Newest") -> bool:
        return other.value < self.value


def _all_views(conn: sqlite3.Connection, *, now: datetime) -> list[RunView]:
    """Every run, in the list order."""
    views = [_view(conn, row, now=now) for row in conn.execute(_RUN_SQL).fetchall()]
    views.sort(
        key=lambda view: (
            _STATE_RANK.get(view.status, 5),
            _Newest(view.updated_at),
            view.public_id,
        )
    )
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


def _detail(
    conn: sqlite3.Connection, view: RunView, *, now: datetime | None = None
) -> RunView:
    """The run with the fields that cost queries to fill.

    The listing keeps the cheap view, so a list of five hundred runs costs
    no extra query. One run at a time pays for the budget, the blockers, the
    checkpoint, the last activity and the errors.
    """
    moment = now if now is not None else utcnow()
    return view.model_copy(
        update={
            "budget": _budget_text(conn, view.run_id),
            "blockers": _blockers(conn, view, now=moment),
            "latest_checkpoint": _latest_checkpoint(conn, view.run_id),
            "last_activity": view.last_heartbeat or view.updated_at,
            "errors": _errors(conn, view.run_id, view.last_error),
        }
    )


def _budget_text(conn: sqlite3.Connection, run_id: str) -> str:
    """The limits of the run and the spend the database can prove.

    A run that recorded its own budget is telling the truth about itself, so
    the recorded budget wins. Otherwise the limits are the ones the research
    contract declares, and the spend is the tool calls the run made.
    """
    recorded = conn.execute(
        "SELECT budget_json FROM research_runs WHERE run_id = ?", (run_id,)
    ).fetchone()
    budget_json = recorded["budget_json"] if recorded is not None else None
    spend = _tool_call_count(conn, run_id)
    if budget_json:
        return f"{redaction.redact(budget_json)} (tool calls spent {spend})"
    try:
        contract = load_research_contract()
    except (ContractError, OSError, RuntimeError):
        return f"limits unknown (tool calls spent {spend})"
    limits = contract.budgets.per_run
    if not limits:
        return f"limits unknown (tool calls spent {spend})"
    # The measured dimension comes first, so the eye lands on the number the
    # database can prove.
    measured = "tool_calls_total"
    parts = []
    if measured in limits:
        parts.append(f"{measured} {limits[measured]:g} (spent {spend})")
    parts.extend(
        f"{name} {limit:g}" for name, limit in limits.items() if name != measured
    )
    return ", ".join(parts)


def _tool_call_count(conn: sqlite3.Connection, run_id: str) -> int:
    """The tool calls of the run: the one spend the database can prove."""
    try:
        return _count(
            conn, "SELECT count(*) FROM tool_calls WHERE run_id = ?", (run_id,)
        )
    except sqlite3.Error:
        # A store without the tool-call table cannot prove the spend.
        return 0


def _blockers(
    conn: sqlite3.Connection, view: RunView, *, now: datetime
) -> list[str]:
    """Why the run is not moving, as short phrases."""
    status = view.status
    blockers: list[str] = []
    if view.worker_active:
        blockers.append("a worker holds the run")
    if status == RunState.RUNNING.value and _has_expired_lease(
        conn, view.run_id, now=now
    ):
        blockers.append("the worker lease expired while the state still says RUNNING")
    if status == RunState.WAITING_HUMAN.value:
        blockers.append("the run waits for a human")
    if status == RunState.BLOCKED_TOOL.value:
        blockers.append("the run is blocked on a tool")
    if status == RunState.PAUSE_REQUESTED.value:
        blockers.append("the pause waits for the worker to carry it out")
    if status == RunState.CANCEL_REQUESTED.value:
        blockers.append("the cancel waits for the worker to carry it out")
    if status == RunState.FAILED_RETRYABLE.value:
        blockers.append("the failure is retryable")
    if status == RunState.FAILED_PERMANENT.value:
        blockers.append("the failure is permanent")
    if status not in _KNOWN_STATES:
        blockers.append("the stored state is not one the interface knows")
    if status == RunState.QUEUED.value and _pending_job(
        conn, view.run_id, now=now
    ) is None:
        blockers.append("the run is queued and no job is waiting for it")
    if not _any_live_lease(conn, now=now):
        blockers.append("no worker holds a lease on any run")
    return blockers


def _latest_checkpoint(conn: sqlite3.Connection, run_id: str) -> str:
    """The newest checkpoint of the run, as it is read."""
    # A checkpoint whose state does not parse is left out: one bad row must not
    # turn the status command into an internal error.
    checkpoints = list_checkpoints(conn, run_id=run_id, skip_invalid=True)
    if not checkpoints:
        return ""
    newest = max(
        checkpoints, key=lambda item: (item.created_at, item.checkpoint_version)
    )
    return (
        f"{newest.stage}/{newest.step or '-'} "
        f"v{newest.checkpoint_version} {newest.created_at}"
    )


def _errors(
    conn: sqlite3.Connection, run_id: str, run_error: str | None
) -> list[str]:
    """The error text of the run and of its failed jobs, masked."""
    entries: list[str] = []
    if run_error:
        entries.append(redaction.redact(run_error))
    for row in conn.execute(
        "SELECT last_error FROM jobs WHERE run_id = ? "
        "AND status IN ('failed', 'dead') AND last_error IS NOT NULL "
        "AND last_error != '' ORDER BY created_at, id",
        (run_id,),
    ).fetchall():
        entries.append(redaction.redact(row["last_error"]))
    return entries[:5]


def eligible_runs(conn: sqlite3.Connection) -> list[RunView]:
    """The runs a bare continue may act on.

    A run is eligible when it is not terminal, not permanently failed, not
    waiting for the worker to carry out a cancel or a pause, no worker holds
    a live lease on it, and its stored state is one the interface knows.
    """
    views = _all_views(conn, now=utcnow())
    return [
        view
        for view in views
        if view.status in _KNOWN_STATES
        and view.status not in _TERMINAL
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
        for checkpoint in list_checkpoints(
            conn, run_id=row["run_id"], skip_invalid=True
        )[:bounded]
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


def doctor(conn: sqlite3.Connection) -> dict[str, object]:
    """The health of the run store. It reads and writes nothing.

    The report names the contract the runs are held to, how many runs sit in
    each state, which leases are live and which have expired, the requests
    the worker will never carry out, and the runs whose stored state the
    interface does not know.
    """
    moment = utcnow()
    now_iso = moment.isoformat()
    runs_by_state: dict[str, int] = {}
    malformed: list[dict[str, str]] = []
    outstanding: list[dict[str, str]] = []
    for row in conn.execute(
        "SELECT run_id, public_id, status FROM research_runs "
        "ORDER BY created_at, public_id"
    ).fetchall():
        stored = (row["status"] or "").strip()
        state = _normalize_state(stored)
        runs_by_state[state] = runs_by_state.get(state, 0) + 1
        if state not in _KNOWN_STATES:
            malformed.append({"public_id": row["public_id"], "status": stored})
        elif state in _REQUESTED and not _has_live_lease(
            conn, row["run_id"], now=moment
        ):
            outstanding.append({"public_id": row["public_id"], "status": state})
    return {
        "contract_version": _contract_version(),
        "runs_by_state": runs_by_state,
        "live_leases": _count(
            conn,
            "SELECT count(*) FROM jobs WHERE status = 'leased' "
            "AND lease_expires_at IS NOT NULL AND lease_expires_at > ?",
            (now_iso,),
        ),
        "outstanding_requests": outstanding,
        "malformed_states": malformed,
        "expired_leases": _count(
            conn,
            "SELECT count(*) FROM jobs WHERE status = 'leased' "
            "AND lease_expires_at IS NOT NULL AND lease_expires_at <= ?",
            (now_iso,),
        ),
        "queued_jobs": _count(
            conn, "SELECT count(*) FROM jobs WHERE status = 'queued'", ()
        ),
    }


def _contract_version() -> str:
    """The version of the research contract, or the word unknown."""
    try:
        return load_research_contract().contract_version
    except (ContractError, OSError, RuntimeError):
        return "unknown"


# --- writing ---------------------------------------------------------------


@contextmanager
def _transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """One transaction. A failure leaves the run as it was.

    A nested call joins the transaction that is already open, so a command
    that reads and writes inside one write does not open a second one. The
    write waits out a busy file instead of failing.
    """
    if conn.in_transaction:
        yield conn
        return
    with_busy_retry(lambda: conn.execute("BEGIN IMMEDIATE"))
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
    """Queue a run that stopped, and let the worker take it again.

    A request the operator asked for is not undone by a continue: a run in
    PAUSE_REQUESTED or CANCEL_REQUESTED waits for the worker. A run whose
    stored state the interface does not know is refused as well, so a corrupt
    row is never handed to a worker.
    """
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
                f"run {row['public_id']} failed permanently; a permanent failure "
                "is not retryable. Start a new research request with a "
                "different objective."
            )
        if status in _REQUESTED:
            # A request the operator asked for is not undone by a continue.
            # The worker owns the transition out of this state.
            raise InvalidTransition(
                f"run {row['public_id']} is {status}; the worker finishes the request"
            )
        if status not in _KNOWN_STATES:
            raise InvalidTransition(
                f"run {row['public_id']} is stored as {row['status']!r}, which is "
                "not a state the interface knows; the run has to be repaired "
                "before it can continue"
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
    transition to PAUSED, and the request waits for it. A run in
    CANCEL_REQUESTED is not a paused run: the cancel stands until the worker
    carries it out.

    A resume obeys the same rule as a continue: the run holds at most one
    pending job. Work that is already pending is requeued, and a worker that
    holds a live lease on the run keeps it: the resume is refused.
    """
    moment = utcnow()
    with _transaction(conn):
        row = _resolve_row(conn, research_id)
        status = _normalize_state(row["status"])
        if status not in _KNOWN_STATES:
            raise InvalidTransition(
                f"run {row['public_id']} is stored as {row['status']!r}, which is "
                "not a state the interface knows; the run has to be repaired "
                "before it can resume"
            )
        if status == RunState.CANCEL_REQUESTED.value:
            # A cancel is not a pause. The request the operator asked for
            # stands until the worker carries it out.
            raise InvalidTransition(
                f"run {row['public_id']} is {status}; the cancel is still outstanding"
            )
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
    nobody acknowledges would leave a run that looks alive. Its pending work
    is cancelled with it, the way a cancel stops the work of a run.
    """
    moment = utcnow()
    with _transaction(conn):
        row = _resolve_row(conn, research_id)
        status = _normalize_state(row["status"])
        if status in _TERMINAL:
            raise InvalidTransition(
                f"run {row['public_id']} is {status} and cannot pause"
            )
        if status == RunState.CANCEL_REQUESTED.value:
            # A cancel is the stronger intent: a pause must not erase it.
            raise InvalidTransition(
                f"run {row['public_id']} is {status}; the cancel is still outstanding"
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
        if not live:
            # The jobs table has no paused status, so the queued job of a
            # paused run is cancelled. The resume queues a fresh one.
            conn.execute(
                "UPDATE jobs SET status = 'cancelled' WHERE run_id = ? "
                "AND status = 'queued'",
                (row["run_id"],),
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
    cannot complete the job it lost. A run a worker still holds is refused:
    the retry would report that it queued something while nothing was
    requeued and the worker still holds the run.
    """
    moment = utcnow()
    with _transaction(conn):
        row = _resolve_row(conn, research_id)
        status = _normalize_state(row["status"])
        if status not in _RETRYABLE:
            if status == RunState.FAILED_PERMANENT.value:
                raise InvalidTransition(
                    f"run {row['public_id']} failed permanently, and a permanent "
                    "failure is not retryable. Start a new research request with "
                    "a different objective."
                )
            raise InvalidTransition(
                f"run {row['public_id']} is {status}; only a failed or blocked "
                "run can be retried"
            )
        if _has_live_lease(conn, row["run_id"], now=moment):
            raise InvalidTransition(
                f"run {row['public_id']} has a live worker lease and is already running"
            )
        # Clear stage_attempts for the retried jobs so the next claim can
        # insert fresh attempt rows without a UNIQUE(job_id, attempt)
        # conflict. Without this, a job retried after exhausting its attempts
        # would fail on re-claim because a stage_attempts row already exists
        # for the same (job_id, attempt) from the previous run.
        conn.execute(
            "DELETE FROM stage_attempts WHERE job_id IN ("
            "SELECT id FROM jobs WHERE run_id = ? AND status IN ('failed', 'dead')"
            ")",
            (row["run_id"],),
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


def recover(
    conn: sqlite3.Connection, research_id: str | None = None
) -> tuple[list[RunView], int]:
    """Resolve the outstanding requests whose worker is gone.

    A pause request becomes a pause and a cancel request becomes a
    cancellation, and the pending work of a cancelled run is cancelled with
    it. The stored status is compared the way the doctor compares it, so the
    recover finds the rows the doctor reports. Every run that is touched gets
    a RESEARCH_RECOVERED event that names the state it was in and the state
    it is in now.

    The jobs whose lease expired are requeued after the state changes: the
    lease recovery opens a transaction of its own, so it cannot run inside
    another one. The two values are the runs that were recovered and the
    number of jobs that were requeued.
    """
    moment = utcnow()
    recovered: list[str] = []
    stopped: list[str] = []
    with _transaction(conn):
        if research_id is not None:
            _resolve_row(conn, research_id)
        rows = conn.execute(
            "SELECT run_id, public_id, status, current_stage FROM research_runs "
            "WHERE UPPER(TRIM(status)) IN (?, ?) ORDER BY created_at, public_id",
            (RunState.PAUSE_REQUESTED.value, RunState.CANCEL_REQUESTED.value),
        ).fetchall()
        for row in rows:
            if research_id is not None and research_id not in (
                row["public_id"],
                row["run_id"],
            ):
                continue
            if _has_live_lease(conn, row["run_id"], now=moment):
                # The worker is still on it: it carries the request out.
                continue
            state = _normalize_state(row["status"])
            target = (
                RunState.PAUSED.value
                if state == RunState.PAUSE_REQUESTED.value
                else RunState.CANCELLED.value
            )
            conn.execute(
                "UPDATE research_runs SET status = ?, updated_at = ? WHERE run_id = ?",
                (target, moment.isoformat(), row["run_id"]),
            )
            if target == RunState.CANCELLED.value:
                conn.execute(
                    "UPDATE jobs SET status = 'cancelled' WHERE run_id = ? "
                    "AND status = 'queued'",
                    (row["run_id"],),
                )
                stopped.append(row["run_id"])
            else:
                # A pause holds the run: its pending work must not stay
                # runnable, the same way a pause without a worker does.
                conn.execute(
                    "UPDATE jobs SET status = 'cancelled' WHERE run_id = ? "
                    "AND status = 'queued'",
                    (row["run_id"],),
                )
                stopped.append(row["run_id"])
            _insert_event(
                conn,
                run_id=row["run_id"],
                event_type="RESEARCH_RECOVERED",
                now=moment,
                stage=row["current_stage"],
                payload={"from": state, "to": target},
            )
            recovered.append(row["public_id"])
    requeued = recover_expired_leases(conn)
    if stopped:
        # The lease recovery requeued a job whose lease expired, so the
        # pending work of a cancelled run is cancelled again: a cancelled run
        # must not leave a job a worker can pick up.
        with _transaction(conn):
            for run_id in stopped:
                conn.execute(
                    "UPDATE jobs SET status = 'cancelled' WHERE run_id = ? "
                    "AND status = 'queued'",
                    (run_id,),
                )
    return [get_run(conn, public_id) for public_id in recovered], requeued


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
    if run.budget:
        lines.append(f"  budget: {run.budget}")
    if run.latest_checkpoint:
        lines.append(f"  checkpoint: {run.latest_checkpoint}")
    if run.last_activity:
        lines.append(f"  last activity: {run.last_activity}")
    if run.blockers:
        lines.append(f"  blockers: {'; '.join(run.blockers)}")
    if run.errors:
        lines.append(f"  errors: {'; '.join(run.errors)}")
    note = _STATE_NOTES.get(run.status)
    if note:
        lines.insert(2, f"  note: {note}")
    return "\n".join(lines)


def _is_continuable(run: RunView) -> bool:
    """Whether the run can be continued right now.

    The same rule the bare continue uses: the run is not terminal, not
    permanently failed, not waiting for the worker to carry out a pause or a
    cancel, no worker holds a live lease on it, and its stored state is one
    the interface knows.
    """
    return (
        run.status in _KNOWN_STATES
        and run.status not in _TERMINAL
        and run.status != RunState.FAILED_PERMANENT.value
        and run.status not in _REQUESTED
        and not run.worker_active
    )


def render_runs(runs: list[RunView]) -> str:
    """The block that lists the runs, one line each.

    A run that can be continued right now says so, so the reader never has
    to guess which one the next continue would pick.
    """
    if not runs:
        return "no research runs"
    lines = [f"{len(runs)} research run(s)"]
    for run in runs:
        line = (
            f"{run.public_id}  {run.objective}  {run.status}  "
            f"{run.current_stage or '-'}  {run.progress_percent}%  "
            f"last {_last_activity(run)}"
        )
        if _is_continuable(run):
            line += "  continuable"
        lines.append(line)
    return "\n".join(lines)


def render_doctor(report: dict[str, object]) -> str:
    """The block that describes the health of the run store."""
    states = report["runs_by_state"]
    lines = [
        "research doctor",
        f"  contract version: {report['contract_version']}",
        "  runs by state: "
        + (
            ", ".join(f"{name} {count}" for name, count in sorted(states.items()))
            if states
            else "none"
        ),
        f"  live leases: {report['live_leases']}",
        f"  expired leases: {report['expired_leases']}",
        f"  queued jobs: {report['queued_jobs']}",
    ]
    outstanding = report["outstanding_requests"]
    if outstanding:
        lines.append("  outstanding requests:")
        for item in outstanding:
            lines.append(f"    {item['public_id']}  {item['status']}")
    else:
        lines.append("  outstanding requests: none")
    malformed = report["malformed_states"]
    if malformed:
        lines.append("  malformed states:")
        for item in malformed:
            lines.append(f"    {item['public_id']}  {item['status']}")
    else:
        lines.append("  malformed states: none")
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
            # The value the operator typed is shown, except where it names a
            # secret.
            problems.append(
                redaction.redact(
                    f"{where}: {error['msg']} (got {error.get('input')!r})"
                )
            )
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
        # The text reaches the model, so it is masked the way every other
        # failure text is.
        message = redaction.safe_error(exc)
        _record_refusal(
            conn,
            action=action,
            research_id=research_id,
            requester=chat_id,
            error=RegistryError(message),
        )
        return CommandResult(
            ok=False,
            action=action,
            message=message,
            text=message,
            code="internal_error",
        )


def _continue_command(
    conn: sqlite3.Connection, research_id: str, chat_id: str | None
) -> CommandResult:
    """Queue the named run and report it."""
    run = _detail(conn, continue_run(conn, research_id, requester=chat_id))
    return CommandResult(
        ok=True,
        action="continue",
        message=f"run {run.public_id} is queued again",
        text=render_run(run),
        run=run,
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
        run = _detail(conn, run)
        message = (
            f"run {run.public_id} already holds this objective; reused it"
            if reused
            else f"created run {run.public_id}"
        )
        # The model reads the text, not the metadata, so the text opens with
        # what happened before the run block.
        return CommandResult(
            ok=True,
            action="create",
            message=message,
            text=f"{message}\n{render_run(run)}",
            run=run,
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
        payload["run"] = _detail(conn, payload["run"])
        return CommandResult(
            ok=True,
            action="log",
            message=f"history of run {request.research_id}",
            text=render_log(payload),
            run=payload["run"],
        )

    if action == "doctor":
        # The doctor reads and writes nothing, and it names no run.
        report = doctor(conn)
        return CommandResult(
            ok=True,
            action="doctor",
            message=f"contract {report['contract_version']}",
            text=render_doctor(report),
        )

    if action == "recover":
        # The id is optional: without one every stale request is resolved.
        wanted = (
            _validated(TargetRequest, research_id=research_id).research_id
            if research_id
            else None
        )
        recovered, requeued = recover(conn, wanted)
        summary = f"recovered {len(recovered)} run(s), requeued {requeued} job(s)"
        lines = [summary]
        for run in recovered:
            lines.append(render_run(run))
        if not recovered:
            lines.append("no stale request was found")
        return CommandResult(
            ok=True,
            action="recover",
            message=summary,
            text="\n".join(lines),
            runs=recovered,
        )

    if action == "continue" and research_id is None:
        # The selection happens inside the write, so the read and the claim
        # are one transaction and two bare continues cannot both pick the
        # same run before either of them claims it.
        with _transaction(conn):
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
                # The model reads the text, not the metadata, so the text
                # carries the instruction as well as the candidates.
                instruction = (
                    "More than one run is eligible; ask the user which one to "
                    "continue, then call continue with the research id the user "
                    "picked. Never pick one on the user's behalf."
                )
                return CommandResult(
                    ok=True,
                    action="continue",
                    message="more than one run is eligible; pick one",
                    text=f"{instruction}\n{render_runs(candidates)}",
                    runs=candidates,
                    needs_selection=True,
                )
            return _continue_command(conn, candidates[0].public_id, chat_id)

    target = _validated(TargetRequest, research_id=research_id or "")
    if action == "status":
        run = _detail(conn, get_run(conn, target.research_id))
        return CommandResult(
            ok=True,
            action="status",
            message=f"run {run.public_id} is {run.status}",
            text=render_run(run),
            run=run,
        )
    if action == "continue":
        return _continue_command(conn, target.research_id, chat_id)
    if action == "resume":
        run = _detail(conn, resume_run(conn, target.research_id, requester=chat_id))
        return CommandResult(
            ok=True,
            action="resume",
            message=f"run {run.public_id} is queued again",
            text=render_run(run),
            run=run,
        )
    if action == "pause":
        run = _detail(conn, pause_run(conn, target.research_id, requester=chat_id))
        return CommandResult(
            ok=True,
            action="pause",
            message=f"run {run.public_id} is {run.status}",
            text=render_run(run),
            run=run,
        )
    if action == "retry":
        run = _detail(conn, retry_run(conn, target.research_id, requester=chat_id))
        return CommandResult(
            ok=True,
            action="retry",
            message=f"run {run.public_id} is queued again",
            text=render_run(run),
            run=run,
        )
    if action == "cancel":
        run = _detail(conn, cancel_run(conn, target.research_id, requester=chat_id))
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

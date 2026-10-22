"""The trace of one run: the chain of what was done and why.

The trace is a read-side union over the tables that already hold the chain.
It is not a new table and it needs no writer: a tool call, a model call, a
stage and an event each carry their part of the story, and this module puts
the four together in one order.

The order is stable. The entries are sorted by the moment they happened, then
by the kind of the entry, then by the identifier of the row they came from,
so two reads of the same data give the same list.

Two gaps in the chain are real, and the report names them instead of hiding
them: a model execution carries no agent and no result reference, and a stage
execution carries no result reference.

Nothing here reads the clock or draws a random number.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from product_hunter.observability.metrics import OBSERVABILITY_VERSION

# The four kinds of entry the trace is made of.
TOOL_CALL = "tool_call"
MODEL_EXECUTION = "model_execution"
STAGE_EXECUTION = "stage_execution"
EVENT = "event"

# The kinds, in the order the counts are printed.
KINDS: tuple[str, ...] = (TOOL_CALL, MODEL_EXECUTION, STAGE_EXECUTION, EVENT)

# The two places where the chain is broken. They are named in every report,
# because a reader who is not told will assume the chain is whole.
MODEL_EXECUTION_GAP = "a model execution carries no agent and no result reference"
STAGE_EXECUTION_GAP = "a stage execution carries no result reference"


class TraceEntry(BaseModel):
    """One step of the chain, and the fields the step could fill."""

    model_config = ConfigDict(extra="forbid")

    run_id: str
    kind: str
    stage: str | None
    agent: str | None
    tool: str
    purpose: str | None
    occurred_at: str
    result_ref: str | None
    status: str
    duration_ms: int | None
    attempt: int | None
    detail: dict[str, Any] = Field(default_factory=dict)


class TraceReport(BaseModel):
    """The chain of one run, and what the chain could not show."""

    model_config = ConfigDict(extra="forbid")

    observability_version: str = OBSERVABILITY_VERSION
    run_id: str
    entries: list[TraceEntry]
    counts: dict[str, int]
    gaps: list[str]


def collect_trace(
    conn: sqlite3.Connection, *, run_id: str, stage: str | None = None
) -> TraceReport:
    """Read the chain of one run out of the four tables that hold it.

    A source table that is not in the schema is skipped and named in the
    gaps, so the read never raises because of an older database.
    """
    entries: list[tuple[str, str, str, TraceEntry]] = []
    gaps: list[str] = []

    rows = _tool_call_rows(conn, run_id, stage)
    if rows is None:
        gaps.append("the tool_calls table is not in the schema")
    else:
        for row in rows:
            entries.append(
                (
                    str(row["occurred_at"]),
                    TOOL_CALL,
                    str(row["tool_call_id"]),
                    TraceEntry(
                        run_id=str(row["run_id"]),
                        kind=TOOL_CALL,
                        stage=row["stage"],
                        agent=row["agent"],
                        tool=str(row["tool"]),
                        purpose=row["purpose"],
                        occurred_at=str(row["occurred_at"]),
                        result_ref=row["result_ref"],
                        status=str(row["status"]),
                        duration_ms=_int_or_none(row["duration_ms"]),
                        attempt=_int_or_none(row["attempt"]),
                        detail={
                            "query_family": row["query_family"],
                            "source_id": row["source_id"],
                            "error": row["error"],
                        },
                    ),
                )
            )

    rows = _model_execution_rows(conn, run_id, stage)
    if rows is None:
        gaps.append("the model_executions table is not in the schema")
    else:
        for row in rows:
            entries.append(
                (
                    str(row["created_at"]),
                    MODEL_EXECUTION,
                    str(row["execution_id"]),
                    TraceEntry(
                        run_id=str(row["run_id"] or run_id),
                        kind=MODEL_EXECUTION,
                        stage=row["stage"],
                        # The model id is the tool of the step: it is what
                        # answered.
                        agent=None,
                        tool=str(row["model_id"]),
                        purpose=str(row["role"]),
                        occurred_at=str(row["created_at"]),
                        result_ref=None,
                        status=str(row["status"]),
                        duration_ms=_int_or_none(row["latency_ms"]),
                        attempt=None,
                        detail={
                            "provider_id": row["provider_id"],
                            "is_fallback": bool(row["is_fallback"]),
                            "prompt_version": row["prompt_version"],
                        },
                    ),
                )
            )

    rows = _stage_execution_rows(conn, run_id, stage)
    if rows is None:
        gaps.append("the research_stage_executions table is not in the schema")
    else:
        for row in rows:
            started = row["started_at"]
            entries.append(
                (
                    str(started or ""),
                    STAGE_EXECUTION,
                    str(row["execution_id"]),
                    TraceEntry(
                        run_id=str(row["run_id"]),
                        kind=STAGE_EXECUTION,
                        stage=str(row["stage"]),
                        agent=None,
                        tool=str(row["stage"]),
                        purpose=str(row["stage"]),
                        occurred_at=str(started or ""),
                        result_ref=None,
                        status=str(row["status"]),
                        duration_ms=None,
                        attempt=_int_or_none(row["attempt"]),
                        detail={
                            "started_at": started,
                            "finished_at": row["finished_at"],
                            "error": row["error"],
                        },
                    ),
                )
            )

    rows = _event_rows(conn, run_id, stage)
    if rows is None:
        gaps.append("the research_events table is not in the schema")
    else:
        for row in rows:
            entries.append(
                (
                    str(row["created_at"]),
                    EVENT,
                    str(row["event_id"]),
                    TraceEntry(
                        run_id=str(row["run_id"]),
                        kind=EVENT,
                        stage=row["stage"],
                        agent=row["agent"],
                        tool=str(row["tool"] or ""),
                        purpose=str(row["event_type"]),
                        occurred_at=str(row["created_at"]),
                        result_ref=row["payload_ref"],
                        status="recorded",
                        duration_ms=None,
                        attempt=None,
                        detail={"event_type": str(row["event_type"])},
                    ),
                )
            )

    entries.sort(key=lambda item: (item[0], item[1], item[2]))
    ordered = [entry[3] for entry in entries]
    counts = {kind: 0 for kind in KINDS}
    for entry in ordered:
        counts[entry.kind] = counts.get(entry.kind, 0) + 1
    counts["total"] = len(ordered)
    return TraceReport(
        run_id=run_id,
        entries=ordered,
        counts=counts,
        gaps=[MODEL_EXECUTION_GAP, STAGE_EXECUTION_GAP, *gaps],
    )


def _tool_call_rows(
    conn: sqlite3.Connection, run_id: str, stage: str | None
) -> list[sqlite3.Row] | None:
    """The tool calls of the run."""
    if not _has_table(conn, "tool_calls"):
        return None
    return conn.execute(
        "SELECT tool_call_id, run_id, stage, agent, tool, purpose, result_ref, "
        "status, duration_ms, attempt, query_family, source_id, error, occurred_at "
        "FROM tool_calls "
        "WHERE run_id = :run_id AND (:stage IS NULL OR stage = :stage) "
        "ORDER BY occurred_at, tool_call_id",
        {"run_id": run_id, "stage": stage},
    ).fetchall()


def _model_execution_rows(
    conn: sqlite3.Connection, run_id: str, stage: str | None
) -> list[sqlite3.Row] | None:
    """The model calls of the run."""
    if not _has_table(conn, "model_executions"):
        return None
    return conn.execute(
        "SELECT execution_id, run_id, stage, role, provider_id, model_id, "
        "is_fallback, prompt_version, latency_ms, status, created_at "
        "FROM model_executions "
        "WHERE run_id = :run_id AND (:stage IS NULL OR stage = :stage) "
        "ORDER BY created_at, execution_id",
        {"run_id": run_id, "stage": stage},
    ).fetchall()


def _stage_execution_rows(
    conn: sqlite3.Connection, run_id: str, stage: str | None
) -> list[sqlite3.Row] | None:
    """The stages the run went through."""
    if not _has_table(conn, "research_stage_executions"):
        return None
    return conn.execute(
        "SELECT execution_id, run_id, stage, status, attempt, started_at, "
        "finished_at, error FROM research_stage_executions "
        "WHERE run_id = :run_id AND (:stage IS NULL OR stage = :stage) "
        "ORDER BY started_at, execution_id",
        {"run_id": run_id, "stage": stage},
    ).fetchall()


def _event_rows(
    conn: sqlite3.Connection, run_id: str, stage: str | None
) -> list[sqlite3.Row] | None:
    """The events the run recorded."""
    if not _has_table(conn, "research_events"):
        return None
    return conn.execute(
        "SELECT event_id, run_id, stage, tool, agent, event_type, payload_ref, "
        "created_at FROM research_events "
        "WHERE run_id = :run_id AND (:stage IS NULL OR stage = :stage) "
        "ORDER BY created_at, event_id",
        {"run_id": run_id, "stage": stage},
    ).fetchall()


def _has_table(conn: sqlite3.Connection, name: str) -> bool:
    """Whether the store holds a table under that name."""
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)
    ).fetchone()
    return row is not None


def _int_or_none(value: Any) -> int | None:
    """A number read from a row, or None when the row left it empty."""
    if isinstance(value, bool) or value is None:
        return None
    return int(value)

"""The interfaces the SQLite wave will implement, and the migration it will run.

This wave does not change SQLite. It defines the two store protocols the
persistence wave codes against, so the gate's output shape is fixed before
any table exists.

The planned migration is ``db/migrations/0003_research_gate.sql``:

.. code-block:: sql

    CREATE TABLE tool_calls (
        tool_call_id  TEXT PRIMARY KEY NOT NULL,
        run_id        TEXT NOT NULL REFERENCES research_runs(run_id),
        stage         TEXT NOT NULL,
        agent         TEXT NOT NULL,
        tool          TEXT NOT NULL,
        purpose       TEXT NOT NULL,
        query_or_operation TEXT NOT NULL,
        result_ref    TEXT,
        result_count  INTEGER NOT NULL DEFAULT 0,
        status        TEXT NOT NULL,
        query_family  TEXT,
        duration_ms   INTEGER,
        error         TEXT,
        occurred_at   TEXT NOT NULL
    );

    CREATE TABLE stage_gate_results (
        gate_result_id TEXT PRIMARY KEY NOT NULL,
        run_id         TEXT NOT NULL REFERENCES research_runs(run_id),
        stage          TEXT,
        passed         INTEGER NOT NULL,
        violations_json TEXT NOT NULL,
        metrics_json   TEXT NOT NULL,
        checked_at     TEXT NOT NULL
    );

The migration ships with the wave that persists tool calls. The protocols
below are the contract that wave codes against.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from product_hunter.research.evidence import ToolCallProvenance
from product_hunter.research.gate import GateResult


@runtime_checkable
class ToolCallStore(Protocol):
    """Where tool-call provenance is written and read back."""

    def append_tool_call(self, call: ToolCallProvenance) -> None:
        """Store one tool call."""

    def list_tool_calls(
        self, run_id: str, *, stage: str | None = None
    ) -> list[ToolCallProvenance]:
        """Return the tool calls of a run, optionally of one stage."""


@runtime_checkable
class GateResultStore(Protocol):
    """Where gate results are written and read back."""

    def record_gate_result(self, result: GateResult) -> None:
        """Store one gate result."""

    def list_gate_results(self, run_id: str) -> list[GateResult]:
        """Return the gate results of a run."""

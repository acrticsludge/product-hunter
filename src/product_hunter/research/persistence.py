"""The store protocols the SQLite implementation codes against.

The migration is ``db/migrations/0003_research_gate.sql`` and it exists: the
SQLite implementation of the two protocols below lives in
``product_hunter.db.research_gate``. The protocols are the contract that
implementation codes against, and they changed once, at the end of this
docstring.

The migration creates the two tables the protocols name:

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
        occurred_at   TEXT NOT NULL,
        attempt       INTEGER NOT NULL DEFAULT 1,
        source_id     TEXT,
        id_is_declared INTEGER NOT NULL DEFAULT 1
    );

    CREATE TABLE stage_gate_results (
        gate_result_id TEXT PRIMARY KEY NOT NULL,
        run_id         TEXT NOT NULL REFERENCES research_runs(run_id),
        stage          TEXT,
        passed         INTEGER NOT NULL,
        violations_json TEXT NOT NULL,
        metrics_json   TEXT NOT NULL,
        contract_fingerprint TEXT,
        checked_at     TEXT NOT NULL
    );

and three more that hold the evidence the gate judges:
``research_stage_executions``, ``research_stage_artifacts`` and
``research_claims``.

The protocols changed twice: ``GateResultStore.record_gate_result`` takes the
fingerprint of the contract the verdict was judged under, and it is required.
A verdict with no contract behind it is bound to nothing, and the gate
compares a stored fingerprint with the one of the contract it is judged
under, so a verdict written without one would be promoted under any
contract. ``ToolCallStore.append_tool_call`` takes the attempt of the stage
that made the call and the source it was made against, so the gate can keep
the attempts of a stage apart and measure the per-source request budget,
and the attempt is required: a call that a worker files under an attempt it
does not name is filed under the wrong attempt, and the gate then judges a
re-run stage on the evidence of the attempt before it. The implementation
in ``product_hunter.db.research_gate`` carries the same parameters, and it
refuses a run whose stage verdicts do not share one fingerprint.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from product_hunter.research.evidence import ToolCallProvenance
from product_hunter.research.gate import GateResult


@runtime_checkable
class ToolCallStore(Protocol):
    """Where tool-call provenance is written and read back."""

    def append_tool_call(
        self,
        call: ToolCallProvenance,
        *,
        attempt: int,
        source_id: str | None = None,
    ) -> None:
        """Store one tool call.

        The attempt is the attempt of the stage that made the call, so a
        stage that re-ran keeps its evidence apart from the attempt before
        it. It is required: a caller states the attempt of the call it
        files, and a call filed under an attempt nobody named is filed
        under the wrong one. The source is the source the call was made
        against, so the per-source request budget can be measured.
        """

    def list_tool_calls(
        self, run_id: str, *, stage: str | None = None
    ) -> list[ToolCallProvenance]:
        """Return the tool calls of a run, optionally of one stage."""


@runtime_checkable
class GateResultStore(Protocol):
    """Where gate results are written and read back."""

    def record_gate_result(
        self, result: GateResult, *, contract_fingerprint: str
    ) -> None:
        """Store one gate result and the contract it was judged under.

        The fingerprint is required: a verdict that names no contract is
        bound to none, and the gate cannot tell it apart from a verdict
        judged under a contract that has since changed.
        """

    def list_gate_results(self, run_id: str) -> list[GateResult]:
        """Return the gate results of a run."""

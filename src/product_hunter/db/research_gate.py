"""The SQLite home of the research gate: where the evidence lives and where the verdict is kept.

The research package holds data only and never touches a database, so the
persistence of its evidence lives here, beside every other SQLite access in
the project. The dependency runs one way: this module imports
``product_hunter.research`` and nothing in there imports this module.

The gate is judged from the database and not from a hand-built fixture.
``load_run_evidence`` rebuilds the evidence a run wrote, and
``require_promotable`` is the fail-closed primitive a promotion path calls:
it refuses a run the gate does not pass.

The verdict of a run is kept in two kinds of row: one row per stage, and
one row for the run as a whole, which holds the violations the gate found
for the run and not for any stage. A verdict read back from the database
carries the run-level violations the computed verdict carried, and the set
of rows is replaced as a whole when the run is judged again.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import Counter
from datetime import datetime

from product_hunter.db.leases import (
    assert_fence_pair,
    assert_live_lease,
    fenced_write,
)
from product_hunter.db.connection import utc_now
from product_hunter.research.budget import (
    BudgetDimension,
    BudgetExceeded,
    BudgetTracker,
)
from product_hunter.research.contract import (
    ResearchContract,
    contract_fingerprint as fingerprint_of_contract,
)
from product_hunter.research.evidence import (
    ArtifactRecord,
    ResearchClaim,
    RunEvidence,
    StageExecution,
    StageStatus,
    ToolCallProvenance,
    ToolCallStatus,
    ZeroResultOutcome,
)
from product_hunter.research.gate import (
    GateResult,
    PromotionResult,
    RunGateResult,
    Violation,
    evaluate_promotion,
    evaluate_run,
    evaluate_stage,
)


class GateBlocked(Exception):
    """Raised when a run must not be promoted.

    The message names the run and the violation codes and nothing else, or
    it is fixed text: a violation can carry page text, a tool error or an
    artifact's invalid reason, and none of that may reach a log through
    this message.
    """


class GateConflict(Exception):
    """A write conflicts with what the store already holds.

    The id of a row is its identity: a row that belongs to another run is
    not moved, and the write that tried to move it is refused.
    """


class MeasuredPromotion(PromotionResult):
    """The promotion verdict of a run and the measurement report with it.

    The report names the budget dimensions the run recorded no spend for.
    Such a dimension has a source and the source holds no row for the run,
    so the spend was not recorded: it is a gap in what the caller recorded
    and not a measurement the gate can fail. The report never changes the
    verdict, and the verdict is a plain ``PromotionResult`` to every caller
    that reads one.
    """

    unmeasured_dimensions: tuple[str, ...] = ()


# The refusals of a run whose verdicts do not share one contract. They are
# fixed text: no run id, no stage, no fingerprint.
_MIXED_FINGERPRINTS = (
    "the gate results of the run were judged under more than one contract"
)
_OTHER_FINGERPRINT = (
    "the gate results of the run were judged under another contract"
)
_NO_FINGERPRINT = "the gate results of the run carry no contract fingerprint"

# The budget dimensions the stored rows can measure. A dimension the
# contract limits but no stored row measures has no source of truth here,
# and a builder that measured nothing for it would under-count the spend
# and let an over-budget run pass.
_SPEND_LEDGER_DIMENSIONS = (
    "llm_input_tokens",
    "llm_output_tokens",
    "embedding_items",
    "subq_calls",
    "browser_minutes",
    "crawl_items",
)

_MEASURED_DIMENSIONS = frozenset(
    {
        BudgetDimension.wall_time_seconds.value,
        BudgetDimension.tool_calls_total.value,
        BudgetDimension.search_queries.value,
        BudgetDimension.fetch_requests.value,
        BudgetDimension.source_requests_per_source.value,
        BudgetDimension.model_calls.value,
        BudgetDimension.usd_cost.value,
    }
) | frozenset(_SPEND_LEDGER_DIMENSIONS)

# The cost of a run is stored in micros, so the ledger sums to a fraction of
# a dollar and the contract's limit is in dollars.
_MICROS_PER_DOLLAR = 1_000_000

# The run-level verdict is a row of its own: the stage column is nullable
# and the row with no stage holds the violations the gate found for the run
# as a whole. Its id is minted on its own prefix, so a stage that happens
# to be named like the run-level basis cannot collide with it: a per-stage
# id is "gate_" and sixteen hex characters, and this one is longer.
_RUN_LEVEL_GATE_PREFIX = "gate_run_"

# The stage the lease fence looks for when a verdict set holds no stage of
# its own. No job carries it, so a fenced write of such a set is refused:
# there is no lease to fence it with.
_RUN_LEVEL_STAGE = "run-level"


class SqliteToolCallStore:
    """The tool-call provenance of a run, in SQLite.

    The store satisfies ``research.persistence.ToolCallStore``.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def append_tool_call(
        self,
        call: ToolCallProvenance,
        *,
        attempt: int,
        source_id: str | None = None,
        worker_id: str | None = None,
        lease_epoch: int | None = None,
    ) -> None:
        """Store one tool call.

        A call that carries no id is given a deterministic one, so the same
        call always lands on the same row. A row that already holds that id
        is left alone when it holds the same call and refused when it holds a
        different one: a call is a fact about the past and is not rewritten.

        The minted id is the row key and nothing more: a call the caller did
        not give an id to has no provenance, and the gate has to see that
        after the row is read back. The row records whether the id was
        declared, and the read path hands the gate a call with no id when it
        was not.

        The attempt is the attempt of the stage that made the call, so the
        gate can judge a stage on the evidence of its latest attempt alone.
        It is required: a caller states the attempt of the call it files.
        The attempt is part of the minted id, so the same call made again
        under a later attempt is a second row and not a refusal.
        """
        if attempt < 1:
            raise ValueError("the attempt must be at least 1")
        declared = call.tool_call_id is not None
        tool_call_id = call.tool_call_id or _hash_id(
            "tc_",
            f"{call.run_id}|{call.stage}|{call.tool}|"
            f"{call.query_or_operation}|{call.timestamp.isoformat()}|{attempt}",
        )
        values = (
            call.run_id,
            call.stage,
            call.agent,
            call.tool,
            call.purpose,
            call.query_or_operation,
            call.result_ref,
            call.result_count,
            call.status.value,
            call.query_family,
            call.duration_ms,
            call.error,
            call.timestamp.isoformat(),
            source_id,
            attempt,
            int(declared),
        )
        with fenced_write(
            self.conn,
            run_id=call.run_id,
            stage=call.stage,
            worker_id=worker_id,
            lease_epoch=lease_epoch,
        ) as conn:
            existing = conn.execute(
                "SELECT run_id, stage, agent, tool, purpose, query_or_operation, "
                "result_ref, result_count, status, query_family, duration_ms, "
                "error, occurred_at, source_id, attempt, id_is_declared "
                "FROM tool_calls WHERE tool_call_id = ?",
                (tool_call_id,),
            ).fetchone()
            if existing is not None:
                if tuple(existing) != values:
                    raise ValueError(
                        f"tool call {tool_call_id!r} already exists with different content"
                    )
                return
            conn.execute(
                """
                INSERT INTO tool_calls (
                    tool_call_id, run_id, stage, agent, tool, purpose,
                    query_or_operation, result_ref, result_count, status,
                    query_family, duration_ms, error, occurred_at,
                    source_id, attempt, id_is_declared
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (tool_call_id, *values),
            )

    def list_tool_calls(
        self, run_id: str, *, stage: str | None = None
    ) -> list[ToolCallProvenance]:
        """Return the tool calls of a run, optionally of one stage."""
        sql = "SELECT * FROM tool_calls WHERE run_id = ?"
        params: list[object] = [run_id]
        if stage is not None:
            sql += " AND stage = ?"
            params.append(stage)
        sql += " ORDER BY occurred_at, tool_call_id"
        rows = self.conn.execute(sql, params).fetchall()
        return [_tool_call_from_row(row) for row in rows]


class SqliteGateResultStore:
    """The gate verdicts of a run, in SQLite.

    The store satisfies ``research.persistence.GateResultStore``. One row
    holds the current verdict of one stage, so a stage that is judged again
    replaces its verdict instead of piling a second one up beside it. The
    contract the verdict was judged under travels with it: a verdict read
    back under another contract must not be promoted.

    The run-level verdict is a row of its own, with no stage. The gate
    finds violations for the run as a whole -- the run-level thresholds --
    and they are not the violations of any stage, so a row that holds no
    stage keeps them. A verdict read back from the database then carries
    the same run-level violations as the verdict that was computed.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def record_gate_result(
        self,
        result: GateResult,
        *,
        contract_fingerprint: str,
        worker_id: str | None = None,
        lease_epoch: int | None = None,
    ) -> None:
        """Store one gate result and the contract fingerprint it carries.

        The fingerprint is required: a verdict with no contract behind it is
        bound to nothing and would be promoted under any contract.

        The verdict joins the contract the run was already judged under: a
        run whose stages carry two contracts has no single verdict, and the
        merged one would be bound to whichever of the two came first. The
        check and the write share one transaction, so a run cannot end up
        with two contracts.
        """
        with fenced_write(
            self.conn,
            run_id=result.run_id,
            stage=result.stage,
            worker_id=worker_id,
            lease_epoch=lease_epoch,
        ) as conn:
            _assert_run_fingerprint(
                conn, result.run_id, contract_fingerprint, exclude_stage=result.stage
            )
            _insert_gate_result(conn, result, contract_fingerprint)

    def record_run_result(
        self,
        run_result: RunGateResult,
        *,
        worker_id: str | None = None,
        lease_epoch: int | None = None,
    ) -> None:
        """Store the verdict of a whole run: every stage and the run itself.

        The contract the run was judged under travels with the verdict: the
        run result carries the fingerprint of its contract, and a run result
        with no fingerprint has no verdict to keep. That is the case of an
        invalid contract, which the gate refuses before it judges anything.

        The set is replaced, not merged: a run is judged under one contract
        and a run judged under a contract that has since changed has to be
        re-judgeable. Every row of the run is written in one transaction, so
        a run never holds the verdicts of one contract beside the verdicts
        of another.

        The lease of every stage the set covers is checked in that same
        transaction, so a worker that lost the lease of a stage cannot
        write the verdict of that stage, and a write that is refused leaves
        the set the run carried before it untouched.
        """
        fingerprint = run_result.contract_fingerprint
        if not fingerprint:
            return
        stages = list(run_result.stages.values())
        fence_stage = stages[0].stage if stages else _RUN_LEVEL_STAGE
        with fenced_write(
            self.conn,
            run_id=run_result.run_id,
            stage=fence_stage,
            worker_id=worker_id,
            lease_epoch=lease_epoch,
        ) as conn:
            if worker_id is not None and lease_epoch is not None:
                for stage_result in stages[1:]:
                    assert_live_lease(
                        conn,
                        run_id=run_result.run_id,
                        stage=stage_result.stage,
                        worker_id=worker_id,
                        lease_epoch=lease_epoch,
                    )
            conn.execute(
                "DELETE FROM stage_gate_results WHERE run_id = ?",
                (run_result.run_id,),
            )
            for stage_result in stages:
                _insert_gate_result(conn, stage_result, fingerprint)
            _insert_run_level_result(conn, run_result, fingerprint)

    def list_gate_results(self, run_id: str) -> list[GateResult]:
        """Return the gate results of a run, one per stage.

        The run-level verdict is not one of them: it is not the verdict of
        a stage, and a caller that expects a stage cannot tell it apart
        from one. ``load_run_result`` is the way to read the run-level
        verdict back.
        """
        rows = self.conn.execute(
            "SELECT * FROM stage_gate_results WHERE run_id = ? "
            "AND stage IS NOT NULL ORDER BY stage",
            (run_id,),
        ).fetchall()
        return [_gate_result_from_row(row) for row in rows]

    def load_run_result(self, run_id: str) -> RunGateResult:
        """Rebuild the run verdict from the verdicts its stages left.

        The rows have to share one fingerprint: a run whose stages were
        judged under two contracts has no single verdict, and a verdict
        bound to one of the two would be promoted under a contract it was
        never judged under.

        The run-level row is merged back in, so the verdict read from the
        database carries the run-level violations the gate found: the
        violations of the run as a whole are not the violations of any
        stage, and without the row they would be lost.
        """
        rows = self.conn.execute(
            "SELECT * FROM stage_gate_results WHERE run_id = ? ORDER BY stage",
            (run_id,),
        ).fetchall()
        fingerprint = _shared_fingerprint(rows)
        stages: dict[str, GateResult] = {}
        violations: list[Violation] = []
        run_level_passed = False
        for row in rows:
            if row["stage"] is None:
                # The run-level row: the violations of the run as a whole.
                run_level_passed = bool(row["passed"])
                violations.extend(_violations_from_row(row))
                continue
            result = _gate_result_from_row(row)
            stages[result.stage] = result
            violations.extend(result.violations)
        return RunGateResult(
            run_id=run_id,
            passed=bool(stages)
            and all(result.passed for result in stages.values())
            and run_level_passed,
            stages=stages,
            violations=violations,
            contract_fingerprint=fingerprint,
        )


def record_stage_execution(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    execution: StageExecution,
    attempt: int | None = None,
    worker_id: str | None = None,
    lease_epoch: int | None = None,
) -> None:
    """Record one stage execution of a run, upserted by run, stage and attempt.

    The attempt of the row is the attempt of the execution unless the caller
    names one: the attempt says which try of the stage the row belongs to,
    and the gate judges a stage on the evidence of its latest attempt alone.
    """
    row_attempt = execution.attempt if attempt is None else attempt
    if row_attempt < 1:
        raise ValueError("the attempt must be at least 1")
    execution_id = _hash_id(
        "exec_", f"{run_id}|{execution.stage}|{row_attempt}"
    )
    with fenced_write(
        conn,
        run_id=run_id,
        stage=execution.stage,
        worker_id=worker_id,
        lease_epoch=lease_epoch,
    ) as conn:
        conn.execute(
            """
            INSERT INTO research_stage_executions (
                execution_id, run_id, stage, status, attempt, started_at,
                finished_at, zero_result_json, error
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(execution_id) DO UPDATE SET
                run_id = excluded.run_id,
                stage = excluded.stage,
                status = excluded.status,
                attempt = excluded.attempt,
                started_at = excluded.started_at,
                finished_at = excluded.finished_at,
                zero_result_json = excluded.zero_result_json,
                error = excluded.error
            """,
            (
                execution_id,
                run_id,
                execution.stage,
                execution.status.value,
                row_attempt,
                _iso(execution.started_at),
                _iso(execution.finished_at),
                json.dumps(execution.zero_result.model_dump(mode="json"), sort_keys=True),
                execution.error,
            ),
        )


def record_artifact(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    artifact: ArtifactRecord,
    attempt: int,
    worker_id: str | None = None,
    lease_epoch: int | None = None,
) -> None:
    """Record one stage artifact of a run, upserted by its artifact id.

    The artifact id is the identity of the row and the run is its owner: an
    artifact that already belongs to another run is not moved into this one,
    and the write that tried to move it is refused.

    The attempt is the attempt of the stage that made the artifact, and it
    is required: the gate judges a stage on the evidence of its latest
    attempt alone, so an artifact filed under an attempt nobody named is
    filed under the wrong one.
    """
    if attempt < 1:
        raise ValueError("the attempt must be at least 1")
    with fenced_write(
        conn,
        run_id=run_id,
        stage=artifact.stage,
        worker_id=worker_id,
        lease_epoch=lease_epoch,
    ) as conn:
        existing = conn.execute(
            "SELECT run_id FROM research_stage_artifacts WHERE artifact_id = ?",
            (artifact.artifact_id,),
        ).fetchone()
        if existing is not None and existing["run_id"] != run_id:
            raise GateConflict(
                f"artifact {artifact.artifact_id!r} already belongs to another run"
            )
        conn.execute(
            """
            INSERT INTO research_stage_artifacts (
                artifact_id, run_id, stage, kind, ref, item_count, content_hash,
                schema_version, valid, invalid_reason, produced_by_tool_call_id,
                created_at, attempt
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(artifact_id) DO UPDATE SET
                stage = excluded.stage,
                kind = excluded.kind,
                ref = excluded.ref,
                item_count = excluded.item_count,
                content_hash = excluded.content_hash,
                schema_version = excluded.schema_version,
                valid = excluded.valid,
                invalid_reason = excluded.invalid_reason,
                produced_by_tool_call_id = excluded.produced_by_tool_call_id,
                created_at = excluded.created_at,
                attempt = excluded.attempt
            """,
            (
                artifact.artifact_id,
                run_id,
                artifact.stage,
                artifact.kind,
                artifact.ref,
                artifact.item_count,
                artifact.content_hash,
                artifact.schema_version,
                int(artifact.valid),
                artifact.invalid_reason,
                artifact.produced_by_tool_call_id,
                artifact.created_at.isoformat(),
                attempt,
            ),
        )


def record_claim(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    claim: ResearchClaim,
    worker_id: str | None = None,
    lease_epoch: int | None = None,
) -> None:
    """Record one claim of a run, upserted by its claim id.

    The claim id is the identity of the row and the run is its owner: a
    claim that already belongs to another run is not moved into this one.
    """
    with fenced_write(
        conn,
        run_id=run_id,
        stage=claim.stage,
        worker_id=worker_id,
        lease_epoch=lease_epoch,
    ) as conn:
        existing = conn.execute(
            "SELECT run_id FROM research_claims WHERE claim_id = ?",
            (claim.claim_id,),
        ).fetchone()
        if existing is not None and existing["run_id"] != run_id:
            raise GateConflict(
                f"claim {claim.claim_id!r} already belongs to another run"
            )
        conn.execute(
            """
            INSERT INTO research_claims (
                claim_id, run_id, kind, statement, stage, evidence_refs_json,
                created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(claim_id) DO UPDATE SET
                kind = excluded.kind,
                statement = excluded.statement,
                stage = excluded.stage,
                evidence_refs_json = excluded.evidence_refs_json
            """,
            (
                claim.claim_id,
                run_id,
                claim.kind.value,
                claim.statement,
                claim.stage,
                json.dumps(claim.evidence_refs, sort_keys=True),
                utc_now().isoformat(),
            ),
        )


def build_budget_tracker(
    conn: sqlite3.Connection, contract: ResearchContract, run_id: str
) -> BudgetTracker:
    """Rebuild the spend of a run from the rows it wrote.

    The contract is the authority for the limits and the tracker is the
    ledger for the spend, so the overrun is measured against the contract
    and not against the tracker's own spec: a tracker built from a looser
    spec cannot widen the budget the contract declares.

    The spend is recorded with the stage and the source it belongs to, so
    the per-stage and the per-source limits bite as well as the per-run
    ones. A record that pushes a scope over its limit raises, and the
    overrun is what the gate reports, so the raise is caught and the spend
    stays recorded.

    A dimension the contract limits that has no measurement source at all
    is refused: there is nowhere to read its spend from, and a builder that
    measured nothing for it would under-count the spend and let an
    over-budget run pass.

    A dimension that has a source and no rows for this run is not refused.
    It is measured as zero, and ``unmeasured_budget_dimensions`` names it:
    the run recorded no spend for it, which is a gap in what the caller
    recorded and not a measurement the gate can fail.
    """
    _require_measured_dimensions(contract)
    tracker = BudgetTracker(contract.budgets)
    for dimension, quantity, stage, source_id in _measure_run(conn, run_id):
        try:
            tracker.record(dimension, quantity, stage=stage, source_id=source_id)
        except BudgetExceeded:
            continue
    return tracker


def measure_run_spend(conn: sqlite3.Connection, run_id: str) -> dict[str, float]:
    """Return the spend of a run per dimension, measured from its rows.

    A dimension the run recorded nothing for is absent from the mapping:
    no rows is not a spend of zero, it is a spend that was not recorded.
    """
    totals: dict[str, float] = {}
    for dimension, quantity, _stage, _source in _measure_run(conn, run_id):
        totals[dimension] = totals.get(dimension, 0.0) + quantity
    return totals


def unmeasured_budget_dimensions(
    conn: sqlite3.Connection, contract: ResearchContract, run_id: str
) -> tuple[str, ...]:
    """Return the limited dimensions the run recorded no spend for.

    A dimension the contract limits is measured from a source. When the
    source holds no row for the run, the spend was not recorded, and the
    dimension is named here so a caller can see the gap instead of the gate
    treating it as a spend of zero.
    """
    measured = measure_run_spend(conn, run_id)
    return tuple(
        sorted(
            name for name in _limited_dimensions(contract) if name not in measured
        )
    )


def load_run_evidence(
    conn: sqlite3.Connection, *, run_id: str, contract_version: str
) -> RunEvidence:
    """Rebuild the evidence of a run from what it wrote to the database.

    A stage is judged on the evidence of its latest attempt and on nothing
    else: a stage that re-ran and produced nothing is judged as a stage that
    produced nothing, and the evidence of the attempt before it is not
    handed to it.

    A run that wrote nothing yields an empty evidence and not an error: the
    gate then reports every stage as missing, which is the safe answer.
    """
    calls = _load_calls_by_attempt(conn, run_id)
    artifacts = _load_artifacts_by_attempt(conn, run_id)

    stages: dict[str, StageExecution] = {}
    rows = conn.execute(
        "SELECT * FROM research_stage_executions WHERE run_id = ? "
        "ORDER BY stage, attempt",
        (run_id,),
    ).fetchall()
    for row in rows:
        stage = row["stage"]
        attempt = int(row["attempt"])
        # The rows are ordered by attempt, so the last row of a stage is its
        # latest attempt: the verdict judges where the stage stands now, and
        # only the evidence of that attempt belongs to it.
        stages[stage] = _execution_from_row(
            row,
            calls.get((stage, attempt), []),
            artifacts.get((stage, attempt), []),
        )

    return RunEvidence(
        run_id=run_id,
        contract_version=contract_version,
        stages=stages,
        claims=_load_claims(conn, run_id),
    )


def load_run_gate_result(
    conn: sqlite3.Connection, *, run_id: str
) -> RunGateResult:
    """Rebuild the run verdict a run left in the database."""
    return SqliteGateResultStore(conn).load_run_result(run_id)


def evaluate_stage_gate(
    conn: sqlite3.Connection,
    contract: ResearchContract,
    evidence: RunEvidence,
    stage: str,
    *,
    worker_id: str | None = None,
    lease_epoch: int | None = None,
) -> GateResult:
    """Judge one stage of the evidence and keep the verdict.

    The verdict is written under the lease fence when the caller names one:
    a worker that lost its lease must not write a verdict over the state of
    a newer worker.
    """
    fingerprint = fingerprint_of_contract(contract)
    tracker = build_budget_tracker(conn, contract, evidence.run_id)
    result = evaluate_stage(
        contract,
        evidence.run_id,
        stage,
        evidence.stages.get(stage),
        budget=tracker,
    )
    SqliteGateResultStore(conn).record_gate_result(
        result,
        contract_fingerprint=fingerprint,
        worker_id=worker_id,
        lease_epoch=lease_epoch,
    )
    return result


def evaluate_run_gate(
    conn: sqlite3.Connection,
    contract: ResearchContract,
    evidence: RunEvidence,
    *,
    worker_id: str | None = None,
    lease_epoch: int | None = None,
) -> RunGateResult:
    """Judge every stage of the run and keep the verdicts.

    The whole run is judged under one contract and the verdicts replace the
    ones the run carried: a run judged under a contract that has since
    changed is re-judgeable, and the set is replaced in one transaction so
    a run never holds the verdicts of two contracts.
    """
    tracker = build_budget_tracker(conn, contract, evidence.run_id)
    run_result = evaluate_run(
        contract, evidence.run_id, evidence.stages, budget=tracker
    )
    SqliteGateResultStore(conn).record_run_result(
        run_result,
        worker_id=worker_id,
        lease_epoch=lease_epoch,
    )
    return run_result


def evaluate_promotion_gate(
    conn: sqlite3.Connection,
    contract: ResearchContract,
    run_result: RunGateResult,
) -> PromotionResult:
    """Judge whether the run's result may be promoted.

    The claims are the ones the run wrote, so the promotion is judged on the
    database and not on what a caller happens to hold.
    """
    claims = _load_claims(conn, run_result.run_id)
    return evaluate_promotion(contract, run_result.run_id, run_result, claims)


def require_promotable(
    conn: sqlite3.Connection,
    contract: ResearchContract,
    run_id: str,
    *,
    worker_id: str | None = None,
    lease_epoch: int | None = None,
) -> MeasuredPromotion:
    """Return the promotion verdict of a run, or refuse it.

    This is the fail-closed primitive: the run is judged from the database,
    and a run the gate does not pass raises ``GateBlocked`` instead of
    returning a verdict the caller might ignore. A run that is not in the
    store is refused the same way: it has no evidence, and the foreign key
    of the gate results would refuse to keep a verdict for it anyway.

    This function writes the stage verdicts it produced: a caller that must
    abort has to run it inside its own transaction.

    The write is fenced when the caller names a fence, so a worker that
    lost its lease does not write a verdict over the state of a newer
    worker. A run that is not in the store writes nothing, so the fence of
    such a run is only checked for its shape.

    The verdict carries the budget dimensions the run recorded no spend
    for. The gap never changes the verdict: a run whose stages passed is
    promoted, and the report says what was not measured.
    """
    assert_fence_pair(worker_id, lease_epoch)
    evidence = load_run_evidence(
        conn, run_id=run_id, contract_version=contract.contract_version
    )
    if _run_is_stored(conn, run_id):
        run_result = evaluate_run_gate(
            conn,
            contract,
            evidence,
            worker_id=worker_id,
            lease_epoch=lease_epoch,
        )
    else:
        run_result = evaluate_run(
            contract,
            run_id,
            evidence.stages,
            budget=build_budget_tracker(conn, contract, run_id),
        )
    promotion = evaluate_promotion_gate(conn, contract, run_result)
    unmeasured = unmeasured_budget_dimensions(conn, contract, run_id)
    if not promotion.promoted:
        # The message names the codes and the run and nothing else: a
        # violation can carry page text, and that text must not reach a log
        # through this message. The budget dimensions are the contract's own
        # names, and they are fixed.
        codes = sorted(
            {violation.code.value for violation in promotion.violations}
            | {violation.code.value for violation in run_result.violations}
        )
        message = f"run {run_id!r} is blocked by the gate: {', '.join(codes)}"
        if unmeasured:
            message += (
                f"; the budget dimensions {', '.join(unmeasured)} "
                "have no recorded spend"
            )
        raise GateBlocked(message)
    return MeasuredPromotion(
        run_id=promotion.run_id,
        promoted=promotion.promoted,
        violations=promotion.violations,
        unmeasured_dimensions=unmeasured,
    )


def _run_is_stored(conn: sqlite3.Connection, run_id: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM research_runs WHERE run_id = ?", (run_id,)
    ).fetchone()
    return row is not None


def _load_calls_by_attempt(
    conn: sqlite3.Connection, run_id: str
) -> dict[tuple[str, int], list[ToolCallProvenance]]:
    """Return the tool calls of a run, keyed by the stage and attempt."""
    rows = conn.execute(
        "SELECT * FROM tool_calls WHERE run_id = ? "
        "ORDER BY occurred_at, tool_call_id",
        (run_id,),
    ).fetchall()
    calls: dict[tuple[str, int], list[ToolCallProvenance]] = {}
    for row in rows:
        key = (row["stage"], int(row["attempt"]))
        calls.setdefault(key, []).append(_tool_call_from_row(row))
    return calls


def _load_artifacts_by_attempt(
    conn: sqlite3.Connection, run_id: str
) -> dict[tuple[str, int], list[ArtifactRecord]]:
    """Return the artifacts of a run, keyed by the stage and attempt."""
    rows = conn.execute(
        "SELECT * FROM research_stage_artifacts WHERE run_id = ? "
        "ORDER BY artifact_id",
        (run_id,),
    ).fetchall()
    artifacts: dict[tuple[str, int], list[ArtifactRecord]] = {}
    for row in rows:
        key = (row["stage"], int(row["attempt"]))
        artifacts.setdefault(key, []).append(_artifact_from_row(row))
    return artifacts


def _load_claims(
    conn: sqlite3.Connection, run_id: str
) -> list[ResearchClaim]:
    rows = conn.execute(
        "SELECT * FROM research_claims WHERE run_id = ? ORDER BY claim_id",
        (run_id,),
    ).fetchall()
    return [
        ResearchClaim(
            claim_id=row["claim_id"],
            kind=row["kind"],
            statement=row["statement"],
            stage=row["stage"],
            evidence_refs=list(json.loads(row["evidence_refs_json"])),
        )
        for row in rows
    ]


def _tool_call_from_row(row: sqlite3.Row) -> ToolCallProvenance:
    return ToolCallProvenance(
        run_id=row["run_id"],
        stage=row["stage"],
        agent=row["agent"],
        tool=row["tool"],
        purpose=row["purpose"],
        timestamp=datetime.fromisoformat(row["occurred_at"]),
        query_or_operation=row["query_or_operation"],
        result_ref=row["result_ref"] or "",
        result_count=int(row["result_count"]),
        status=ToolCallStatus(row["status"]),
        # The id the caller declared is the provenance. A row whose id was
        # minted for the sake of the primary key carries none, and the gate
        # has to see that.
        tool_call_id=row["tool_call_id"] if row["id_is_declared"] else None,
        query_family=row["query_family"],
        duration_ms=(
            int(row["duration_ms"]) if row["duration_ms"] is not None else None
        ),
        error=row["error"],
    )


def _artifact_from_row(row: sqlite3.Row) -> ArtifactRecord:
    return ArtifactRecord(
        artifact_id=row["artifact_id"],
        stage=row["stage"],
        kind=row["kind"],
        ref=row["ref"],
        item_count=int(row["item_count"]),
        content_hash=row["content_hash"],
        schema_version=row["schema_version"],
        valid=bool(row["valid"]),
        invalid_reason=row["invalid_reason"],
        produced_by_tool_call_id=row["produced_by_tool_call_id"],
        created_at=datetime.fromisoformat(row["created_at"]),
    )


def _execution_from_row(
    row: sqlite3.Row,
    calls: list[ToolCallProvenance],
    artifacts: list[ArtifactRecord],
) -> StageExecution:
    zero_result = ZeroResultOutcome()
    if row["zero_result_json"]:
        zero_result = ZeroResultOutcome.model_validate(
            json.loads(row["zero_result_json"])
        )
    return StageExecution(
        stage=row["stage"],
        status=StageStatus(row["status"]),
        attempt=int(row["attempt"]),
        started_at=_parse_timestamp(row["started_at"]),
        finished_at=_parse_timestamp(row["finished_at"]),
        tool_calls=list(calls),
        artifacts=list(artifacts),
        zero_result=zero_result,
        error=row["error"],
    )


def _gate_result_from_row(row: sqlite3.Row) -> GateResult:
    return GateResult(
        run_id=row["run_id"],
        stage=row["stage"],
        passed=bool(row["passed"]),
        violations=_violations_from_row(row),
        metrics={
            str(name): int(value)
            for name, value in json.loads(row["metrics_json"]).items()
        },
    )


def _violations_from_row(row: sqlite3.Row) -> list[Violation]:
    return [
        Violation.model_validate(item) for item in json.loads(row["violations_json"])
    ]


def _run_level_gate_id(run_id: str) -> str:
    """The id of the run-level verdict row of a run."""
    return _hash_id(_RUN_LEVEL_GATE_PREFIX, run_id)


def _run_level_violations(run_result: RunGateResult) -> list[Violation]:
    """The violations the gate found for the run as a whole.

    A violation with no stage belongs to the run and not to any stage, so
    the run-level row is the one that keeps it.
    """
    return [
        violation for violation in run_result.violations if violation.stage is None
    ]


def _insert_gate_result(
    conn: sqlite3.Connection, result: GateResult, contract_fingerprint: str
) -> None:
    """Write the verdict of one stage."""
    conn.execute(
        """
        INSERT INTO stage_gate_results (
            gate_result_id, run_id, stage, passed, violations_json,
            metrics_json, contract_fingerprint, checked_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(gate_result_id) DO UPDATE SET
            run_id = excluded.run_id,
            stage = excluded.stage,
            passed = excluded.passed,
            violations_json = excluded.violations_json,
            metrics_json = excluded.metrics_json,
            contract_fingerprint = excluded.contract_fingerprint,
            checked_at = excluded.checked_at
        """,
        (
            _hash_id("gate_", f"{result.run_id}|{result.stage}"),
            result.run_id,
            result.stage,
            int(result.passed),
            json.dumps(
                [violation.model_dump(mode="json") for violation in result.violations],
                sort_keys=True,
            ),
            json.dumps(result.metrics, sort_keys=True),
            contract_fingerprint,
            utc_now().isoformat(),
        ),
    )


def _insert_run_level_result(
    conn: sqlite3.Connection, run_result: RunGateResult, contract_fingerprint: str
) -> None:
    """Write the verdict of the run as a whole, as a row with no stage.

    The row carries the violations the gate found for the run and the
    contract fingerprint the per-stage rows carry, so a verdict read back
    from the database is bound to the same contract as its stages.
    """
    violations = _run_level_violations(run_result)
    passed = not any(violation.blocking for violation in violations)
    conn.execute(
        """
        INSERT INTO stage_gate_results (
            gate_result_id, run_id, stage, passed, violations_json,
            metrics_json, contract_fingerprint, checked_at
        ) VALUES (?, ?, NULL, ?, ?, ?, ?, ?)
        ON CONFLICT(gate_result_id) DO UPDATE SET
            run_id = excluded.run_id,
            stage = excluded.stage,
            passed = excluded.passed,
            violations_json = excluded.violations_json,
            metrics_json = excluded.metrics_json,
            contract_fingerprint = excluded.contract_fingerprint,
            checked_at = excluded.checked_at
        """,
        (
            _run_level_gate_id(run_result.run_id),
            run_result.run_id,
            int(passed),
            json.dumps(
                [violation.model_dump(mode="json") for violation in violations],
                sort_keys=True,
            ),
            "{}",
            contract_fingerprint,
            utc_now().isoformat(),
        ),
    )


def _shared_fingerprint(rows: list[sqlite3.Row]) -> str:
    """Return the one fingerprint the rows share, or refuse the set.

    A run whose stages were judged under two contracts has no single
    verdict, and a verdict bound to one of the two would be promoted under a
    contract it was never judged under. A row with no fingerprint is a
    verdict bound to no contract at all, which is the same failure.
    """
    if not rows:
        return ""
    fingerprints = {row["contract_fingerprint"] or "" for row in rows}
    if len(fingerprints) > 1:
        raise GateBlocked(_MIXED_FINGERPRINTS)
    fingerprint = fingerprints.pop()
    if not fingerprint:
        raise GateBlocked(_NO_FINGERPRINT)
    return fingerprint


def _assert_run_fingerprint(
    conn: sqlite3.Connection,
    run_id: str,
    fingerprint: str,
    *,
    exclude_stage: str | None = None,
) -> None:
    """Refuse a verdict that would give the run a second contract.

    The rows of the other stages are the ones the run already carries: a
    verdict that does not share their fingerprint would leave the run with
    two contracts, and the merged verdict would be bound to one of them.
    """
    sql = (
        "SELECT DISTINCT contract_fingerprint FROM stage_gate_results "
        "WHERE run_id = ?"
    )
    params: list[object] = [run_id]
    if exclude_stage is not None:
        sql += " AND stage IS NOT ?"
        params.append(exclude_stage)
    rows = conn.execute(sql, params).fetchall()
    fingerprints = {row["contract_fingerprint"] or "" for row in rows}
    if len(fingerprints) > 1:
        raise GateBlocked(_MIXED_FINGERPRINTS)
    if fingerprints and fingerprint not in fingerprints:
        raise GateBlocked(_OTHER_FINGERPRINT)


def _limited_dimensions(contract: ResearchContract) -> set[str]:
    """Return the budget dimensions the contract limits."""
    names = set(contract.budgets.per_run)
    names.update(contract.budgets.soft_thresholds)
    for limits in contract.budgets.per_stage.values():
        names.update(limits)
    if contract.budgets.per_source:
        names.add(BudgetDimension.source_requests_per_source.value)
    return names


def _require_measured_dimensions(contract: ResearchContract) -> None:
    """Refuse a contract that limits a dimension no stored row measures."""
    for name in sorted(_limited_dimensions(contract)):
        if name not in _MEASURED_DIMENSIONS:
            raise ValueError(f"the budget dimension {name!r} has no measurement source")


def _measure_run(
    conn: sqlite3.Connection, run_id: str
) -> list[tuple[str, float, str | None, str | None]]:
    """Return the spend of a run as (dimension, quantity, stage, source) rows.

    The table each dimension is measured from:

    ==============================  =====================================
    tool_calls                      tool_calls_total, search_queries,
                                   fetch_requests,
                                   source_requests_per_source
    model_executions                model_calls
    spend_ledger                    llm_input_tokens, llm_output_tokens,
                                   embedding_items, subq_calls,
                                   browser_minutes, crawl_items, usd_cost
    research_stage_executions       wall_time_seconds
    ==============================  =====================================

    ``model_calls`` is measured from ``model_executions`` alone. It is the
    execution log and it has a writer, and the ledger can hold a
    ``model_calls`` row for the same call: the two sources overlap, so
    adding them would count one call twice and halve the limit the
    contract declares.
    """
    measurements: list[tuple[str, float, str | None, str | None]] = []

    calls = Counter()
    searches = Counter()
    fetches = Counter()
    for row in conn.execute(
        "SELECT stage, tool, status, source_id FROM tool_calls WHERE run_id = ?",
        (run_id,),
    ).fetchall():
        key = (row["stage"], row["source_id"])
        calls[key] += 1
        if row["status"] == "success":
            if row["tool"] == "search":
                searches[key] += 1
            elif row["tool"] == "fetch":
                fetches[key] += 1
    for (stage, source_id), count in calls.items():
        measurements.append(
            (BudgetDimension.tool_calls_total.value, count, stage, source_id)
        )
        measurements.append(
            (
                BudgetDimension.source_requests_per_source.value,
                count,
                stage,
                source_id,
            )
        )
    for (stage, source_id), count in searches.items():
        measurements.append(
            (BudgetDimension.search_queries.value, count, stage, source_id)
        )
    for (stage, source_id), count in fetches.items():
        measurements.append(
            (BudgetDimension.fetch_requests.value, count, stage, source_id)
        )

    model_calls = Counter()
    for row in conn.execute(
        "SELECT stage FROM model_executions WHERE run_id = ?", (run_id,)
    ).fetchall():
        model_calls[row["stage"]] += 1

    ledger_units: dict[str, Counter] = {
        name: Counter() for name in _SPEND_LEDGER_DIMENSIONS
    }
    costs: Counter = Counter()
    for row in conn.execute(
        "SELECT stage, unit_type, units, cost_micros FROM spend_ledger "
        "WHERE run_id = ?",
        (run_id,),
    ).fetchall():
        stage = row["stage"]
        unit_type = row["unit_type"]
        if unit_type in ledger_units:
            ledger_units[unit_type][stage] += int(row["units"] or 0)
        # A ledger row of any other unit type still costs what it cost, and
        # the cost of a model call is the cost of the call.
        costs[stage] += int(row["cost_micros"] or 0)

    for stage, count in model_calls.items():
        measurements.append((BudgetDimension.model_calls.value, count, stage, None))
    for name, counter in ledger_units.items():
        for stage, units in counter.items():
            measurements.append((name, units, stage, None))
    for stage, micros in costs.items():
        measurements.append(
            (BudgetDimension.usd_cost.value, micros / _MICROS_PER_DOLLAR, stage, None)
        )

    durations: Counter = Counter()
    for row in conn.execute(
        "SELECT stage, started_at, finished_at FROM research_stage_executions "
        "WHERE run_id = ?",
        (run_id,),
    ).fetchall():
        durations[row["stage"]] += _duration_seconds(
            row["started_at"], row["finished_at"]
        )
    for stage, seconds in durations.items():
        measurements.append(
            (BudgetDimension.wall_time_seconds.value, seconds, stage, None)
        )

    return measurements


def _duration_seconds(started: str | None, finished: str | None) -> float:
    """Return how long a stage execution ran, zero when it cannot be read."""
    if not started or not finished:
        return 0.0
    try:
        start = datetime.fromisoformat(started)
        end = datetime.fromisoformat(finished)
    except ValueError:
        return 0.0
    if start.tzinfo is None or end.tzinfo is None:
        return 0.0
    elapsed = (end - start).total_seconds()
    return elapsed if elapsed > 0 else 0.0


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _parse_timestamp(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _hash_id(prefix: str, basis: str) -> str:
    return prefix + hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16]

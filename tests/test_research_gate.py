from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from product_hunter.research.budget import BudgetExceeded, BudgetSpec, BudgetTracker
from product_hunter.research.contract import ResearchContract, load_research_contract
from product_hunter.research.evidence import (
    ArtifactRecord,
    ResearchClaim,
    StageExecution,
    StageStatus,
    ToolCallProvenance,
    ToolCallStatus,
    ZeroResultOutcome,
)
from product_hunter.research.gate import (
    GateResult,
    ViolationCode,
    evaluate_promotion,
    evaluate_run,
    evaluate_stage,
)
from product_hunter.research.predicates import PREDICATES, PredicateContext

FROZEN = datetime(2026, 10, 21, 12, 0, 0, tzinfo=timezone.utc)

ARTIFACT_KIND = {
    "discovery": "source_records",
    "counter_evidence": "counter_evidence",
    "investigation": "competitors",
}

STAGES = {
    "discovery": {
        "required_tools": ["search"],
        "required_outputs": ["source_records"],
        "quality_checks": ["non_empty_queries"],
        "gate_key": "discovery",
    },
    "counter_evidence": {
        "required_tools": ["search"],
        "required_outputs": ["counter_evidence"],
        "quality_checks": [],
        "gate_key": "counter_evidence",
    },
    "investigation": {
        "required_tools": ["search"],
        "required_outputs": ["competitors"],
        "quality_checks": [],
        "gate_key": "competitor_search",
    },
}

THRESHOLDS = {
    "discovery": {"min_distinct_queries": 1},
    "counter_evidence": {"min_counter_evidence_items_or_explicit_zero": 1},
    "competitor_search": {"min_distinct_queries": 1},
}


def _contract(
    *,
    discovery: dict | None = None,
    counter_evidence: dict | None = None,
    investigation: dict | None = None,
    **overrides,
) -> ResearchContract:
    stages = {name: dict(values) for name, values in STAGES.items()}
    for name, updates in (
        ("discovery", discovery),
        ("counter_evidence", counter_evidence),
        ("investigation", investigation),
    ):
        if updates:
            stages[name].update(updates)
    hard = {"require_qdrant_check": False, "require_duckdb_analysis": False}
    hard.update(overrides.pop("hard_requirements", {}))
    data = {
        "contract_version": "test-v1",
        "required_stages": list(STAGES),
        "stages": stages,
        "evidence_thresholds": {key: dict(value) for key, value in THRESHOLDS.items()},
        "budgets": {"version": "test-v1"},
        "hard_requirements": hard,
    }
    data.update(overrides)
    return ResearchContract.model_validate(data)


def _call(stage: str = "discovery", **overrides) -> ToolCallProvenance:
    data = {
        "run_id": "run-1",
        "stage": stage,
        "agent": "researcher",
        "tool": "search",
        "purpose": "collect evidence",
        "timestamp": FROZEN,
        "query_or_operation": f"{stage} query",
        "result_ref": f"raw/{stage}",
        "result_count": 3,
        "status": ToolCallStatus.success,
        "tool_call_id": f"{stage}-call-1",
        "query_family": "problem",
    }
    data.update(overrides)
    return ToolCallProvenance(**data)


def _artifact(stage: str = "discovery", **overrides) -> ArtifactRecord:
    data = {
        "artifact_id": f"{stage}-art-1",
        "stage": stage,
        "kind": ARTIFACT_KIND[stage],
        "ref": f"data/artifacts/run-1/{stage}.jsonl",
        "item_count": 1,
        "produced_by_tool_call_id": f"{stage}-call-1",
        "created_at": FROZEN,
    }
    data.update(overrides)
    return ArtifactRecord(**data)


def _execution(stage: str = "discovery", **overrides) -> StageExecution:
    data = {
        "stage": stage,
        "status": StageStatus.succeeded,
        "started_at": FROZEN,
        "finished_at": FROZEN,
        "tool_calls": [_call(stage)],
        "artifacts": [_artifact(stage)],
    }
    data.update(overrides)
    return StageExecution(**data)


def _codes(result: GateResult) -> set[str]:
    return {violation.code.value for violation in result.violations}


def _reasons(result) -> list[str]:
    return [
        str(violation.detail.get("reason", ""))
        for violation in result.violations
        if violation.code is ViolationCode.promotion_blocked
    ]


def test_a_missing_stage_is_stage_missing():
    result = evaluate_stage(_contract(), "run-1", "discovery", None)

    assert _codes(result) == {"stage_missing"}
    assert result.passed is False
    assert result.metrics == {}


def test_a_run_cannot_pass_by_skipping_a_stage():
    result = evaluate_run(_contract(), "run-1", {})

    assert result.passed is False
    assert _codes(result.stages["discovery"]) == {"stage_missing"}
    assert set(result.stages) == {"discovery", "counter_evidence", "investigation"}


def test_a_failed_stage_is_stage_failed():
    execution = _execution("discovery", status=StageStatus.failed, error="upstream refused")

    result = evaluate_stage(_contract(), "run-1", "discovery", execution)

    assert _codes(result) == {"stage_failed"}
    assert result.passed is False


def test_a_failed_stage_over_budget_reports_both():
    tracker = BudgetTracker(BudgetSpec(version="test-v1", per_run={"search_queries": 1.0}))
    with pytest.raises(BudgetExceeded):
        tracker.record("search_queries", 5.0, stage="discovery")
    execution = _execution("discovery", status=StageStatus.failed, error="upstream refused")

    result = evaluate_stage(
        _contract(budgets={"version": "test-v1", "per_run": {"search_queries": 1.0}}),
        "run-1",
        "discovery",
        execution,
        tracker,
    )

    assert _codes(result) == {"stage_failed", "budget_exceeded"}
    assert result.passed is False


def test_a_timed_out_stage_is_stage_timed_out():
    execution = _execution("discovery", status=StageStatus.timed_out)

    result = evaluate_stage(_contract(), "run-1", "discovery", execution)

    assert _codes(result) == {"stage_timed_out"}


def test_a_stage_that_outran_its_timeout_is_timed_out():
    contract = _contract(discovery={"timeout_seconds": 30})
    execution = _execution(
        "discovery",
        started_at=FROZEN,
        finished_at=FROZEN + timedelta(seconds=31),
    )

    result = evaluate_stage(contract, "run-1", "discovery", execution)

    assert _codes(result) == {"stage_timed_out"}


def test_a_budget_overrun_is_budget_exceeded():
    tracker = BudgetTracker(BudgetSpec(version="test-v1", per_run={"search_queries": 1.0}))
    with pytest.raises(BudgetExceeded):
        tracker.record("search_queries", 5.0, stage="discovery")

    result = evaluate_stage(
        _contract(budgets={"version": "test-v1", "per_run": {"search_queries": 1.0}}),
        "run-1",
        "discovery",
        _execution("discovery"),
        tracker,
    )

    assert _codes(result) == {"budget_exceeded"}
    assert result.passed is False


def test_an_invalid_artifact_is_artifact_invalid():
    execution = _execution(
        "discovery",
        artifacts=[_artifact("discovery", valid=False, invalid_reason="schema mismatch")],
    )

    result = evaluate_stage(_contract(), "run-1", "discovery", execution)

    assert _codes(result) == {"artifact_invalid"}


def test_an_invalid_artifact_is_reported_even_without_required_tools():
    contract = _contract(
        discovery={"required_tools": [], "required_outputs": ["source_records"]}
    )
    execution = _execution(
        "discovery",
        artifacts=[_artifact("discovery", valid=False, invalid_reason="schema mismatch")],
    )

    result = evaluate_stage(contract, "run-1", "discovery", execution)

    assert _codes(result) == {"artifact_invalid"}
    assert result.passed is False


def test_missing_provenance_is_provenance_missing():
    contract = _contract(discovery={"required_outputs": []})
    execution = _execution("discovery", tool_calls=[_call("discovery", tool_call_id=None)])

    result = evaluate_stage(contract, "run-1", "discovery", execution)

    assert _codes(result) == {"provenance_missing"}


def test_provenance_from_another_run_is_provenance_mismatch():
    execution = _execution("discovery", tool_calls=[_call("discovery", run_id="run-2")])

    result = evaluate_stage(_contract(), "run-1", "discovery", execution)

    assert _codes(result) == {"provenance_mismatch"}


def test_an_unmet_threshold_is_evidence_threshold_unmet():
    contract = _contract(
        evidence_thresholds={**THRESHOLDS, "discovery": {"min_distinct_queries": 2}}
    )

    result = evaluate_stage(contract, "run-1", "discovery", _execution("discovery"))

    assert _codes(result) == {"evidence_threshold_unmet"}
    assert result.metrics["distinct_queries"] == 1


def test_an_artifact_without_a_producing_tool_call_is_artifact_invalid():
    execution = _execution(
        "discovery", artifacts=[_artifact("discovery", produced_by_tool_call_id=None)]
    )

    result = evaluate_stage(_contract(), "run-1", "discovery", execution)

    assert _codes(result) == {"artifact_invalid"}


def test_an_artifact_from_another_stage_is_artifact_invalid():
    contract = _contract(hard_requirements={"require_provenance": False})
    execution = _execution(
        "discovery",
        tool_calls=[_call("discovery"), _call("validation", tool_call_id="validation-call-1")],
        artifacts=[_artifact("discovery", produced_by_tool_call_id="validation-call-1")],
    )

    result = evaluate_stage(contract, "run-1", "discovery", execution)

    assert _codes(result) == {"artifact_invalid"}


def test_a_tool_that_never_ran_is_tool_missing():
    contract = _contract(discovery={"required_tools": ["search", "qdrant"]})

    result = evaluate_stage(contract, "run-1", "discovery", _execution("discovery"))

    assert _codes(result) == {"tool_missing"}


def test_a_failed_quality_check_is_quality_check_failed():
    contract = _contract(
        discovery={"required_tools": [], "required_outputs": [], "quality_checks": ["source_diversity"]},
        evidence_thresholds={
            "counter_evidence": dict(THRESHOLDS["counter_evidence"]),
            "competitor_search": dict(THRESHOLDS["competitor_search"]),
        },
    )
    execution = _execution(
        "discovery",
        tool_calls=[
            _call(
                "discovery",
                tool="fetch",
                query_or_operation="https://news.ycombinator.com/item?id=1",
            )
        ],
    )

    result = evaluate_stage(contract, "run-1", "discovery", execution)

    assert _codes(result) == {"quality_check_failed"}


def test_the_non_empty_queries_detail_does_not_leak_the_query_text():
    secret = "https://alice:hunter2@example.com/search?q=invoices"
    call = _call("discovery", query_or_operation=secret, tool_call_id=None)
    context = PredicateContext(
        run_id="run-1", execution=_execution("discovery", tool_calls=[call]), claims=()
    )

    passed, detail = PREDICATES["non_empty_queries"](context)

    assert passed is True
    assert secret not in detail
    assert "hunter2" not in detail
    assert "alice" not in detail


def test_a_failed_quality_check_does_not_leak_the_query_text():
    secret = "https://alice:hunter2@example.com/search?q=invoices"
    call = _call(
        "discovery",
        status=ToolCallStatus.failure,
        result_ref="",
        error="upstream refused",
        query_or_operation=secret,
    )
    contract = _contract(
        discovery={
            "required_tools": [],
            "required_outputs": [],
            "quality_checks": ["non_empty_queries"],
        },
        evidence_thresholds={
            key: dict(value) for key, value in THRESHOLDS.items() if key != "discovery"
        },
    )
    execution = _execution("discovery", tool_calls=[call], artifacts=[])

    result = evaluate_stage(contract, "run-1", "discovery", execution)

    assert _codes(result) == {"quality_check_failed"}
    violation = result.violations[0]
    assert secret not in violation.message
    assert "hunter2" not in violation.message
    assert secret not in str(violation.detail)
    assert "hunter2" not in str(violation.detail)


def test_a_missing_required_output_is_artifact_missing():
    execution = _execution("discovery", artifacts=[])

    result = evaluate_stage(_contract(), "run-1", "discovery", execution)

    assert _codes(result) == {"artifact_missing"}


def test_an_invalid_contract_stops_the_evaluation():
    contract = _contract(required_stages=["discovery", "ghost"])

    result = evaluate_stage(contract, "run-1", "discovery", _execution("discovery"))

    assert _codes(result) == {"contract_invalid"}
    assert result.metrics == {}


def test_one_counter_evidence_item_satisfies_the_threshold():
    execution = _execution(
        "counter_evidence", artifacts=[_artifact("counter_evidence", item_count=1)]
    )

    result = evaluate_stage(_contract(), "run-1", "counter_evidence", execution)

    assert result.violations == []
    assert result.passed is True


def test_a_recorded_zero_with_every_family_completed_satisfies_the_threshold():
    execution = _execution(
        "counter_evidence",
        tool_calls=[
            _call("counter_evidence"),
            _call(
                "counter_evidence",
                tool_call_id="counter_evidence-call-2",
                query_family="solution",
            ),
        ],
        artifacts=[_artifact("counter_evidence", item_count=0)],
        zero_result=ZeroResultOutcome(
            recorded=True,
            required_query_families=["problem", "solution"],
            completed_query_families=["problem", "solution"],
        ),
    )

    result = evaluate_stage(_contract(), "run-1", "counter_evidence", execution)

    assert result.passed is True
    assert result.violations == []


def test_a_recorded_zero_with_a_missing_family_does_not_satisfy_the_threshold():
    execution = _execution(
        "counter_evidence",
        artifacts=[_artifact("counter_evidence", item_count=0)],
        zero_result=ZeroResultOutcome(
            recorded=True,
            required_query_families=["problem", "solution"],
            completed_query_families=["problem"],
        ),
    )

    result = evaluate_stage(_contract(), "run-1", "counter_evidence", execution)

    assert _codes(result) == {"evidence_threshold_unmet"}


def test_a_failed_search_does_not_count_as_zero():
    execution = _execution(
        "counter_evidence",
        tool_calls=[
            _call("counter_evidence"),
            _call(
                "counter_evidence",
                tool_call_id="counter_evidence-call-2",
                status=ToolCallStatus.failure,
                result_ref="",
                error="upstream timeout",
            ),
        ],
        artifacts=[_artifact("counter_evidence", item_count=0)],
        zero_result=ZeroResultOutcome(
            recorded=True,
            required_query_families=["problem"],
            completed_query_families=["problem"],
        ),
    )

    result = evaluate_stage(_contract(), "run-1", "counter_evidence", execution)

    assert _codes(result) == {"evidence_threshold_unmet"}


def test_an_unsearched_stage_does_not_count_as_zero():
    execution = _execution(
        "counter_evidence",
        artifacts=[_artifact("counter_evidence", item_count=0)],
    )

    result = evaluate_stage(_contract(), "run-1", "counter_evidence", execution)

    assert _codes(result) == {"evidence_threshold_unmet"}


def _passing_executions() -> dict[str, StageExecution]:
    return {
        "discovery": _execution("discovery"),
        "counter_evidence": _execution(
            "counter_evidence", artifacts=[_artifact("counter_evidence", item_count=1)]
        ),
        "investigation": _execution("investigation"),
    }


def test_promotion_passes_when_every_stage_passes():
    contract = _contract()
    run_result = evaluate_run(contract, "run-1", _passing_executions())
    claim = ResearchClaim(
        claim_id="claim-1",
        kind="opportunity_exists",
        statement="teams lose hours to spreadsheets",
        stage="investigation",
        evidence_refs=["doc-1"],
    )

    promotion = evaluate_promotion(contract, "run-1", run_result, [claim])

    assert run_result.passed is True
    assert promotion.promoted is True
    assert promotion.violations == []


def test_promotion_is_blocked_when_the_competitor_stage_is_missing():
    contract = _contract()
    executions = _passing_executions()
    del executions["investigation"]
    run_result = evaluate_run(contract, "run-1", executions)

    promotion = evaluate_promotion(contract, "run-1", run_result, [])

    assert promotion.promoted is False
    assert "promotion_blocked" in {v.code.value for v in promotion.violations}
    assert any("investigation" in reason for reason in _reasons(promotion))


def test_promotion_is_blocked_when_the_counter_evidence_stage_failed():
    contract = _contract()
    executions = _passing_executions()
    executions["counter_evidence"] = _execution(
        "counter_evidence", status=StageStatus.failed, error="upstream refused"
    )
    run_result = evaluate_run(contract, "run-1", executions)

    promotion = evaluate_promotion(contract, "run-1", run_result, [])

    assert promotion.promoted is False
    assert any("counter_evidence" in reason for reason in _reasons(promotion))


def test_promotion_is_blocked_when_a_claim_has_no_evidence_reference():
    contract = _contract()
    run_result = evaluate_run(contract, "run-1", _passing_executions())
    claim = ResearchClaim(
        claim_id="claim-1",
        kind="opportunity_exists",
        statement="teams lose hours to spreadsheets",
        stage="investigation",
    )

    promotion = evaluate_promotion(contract, "run-1", run_result, [claim])

    assert promotion.promoted is False
    assert "claim_evidence_missing" in {v.code.value for v in promotion.violations}
    assert "promotion_blocked" in {v.code.value for v in promotion.violations}


def test_the_claim_evidence_rule_fires_on_the_hard_requirement_flag():
    contract = _contract(
        hard_requirements={"require_claim_evidence_links": True},
        promotion={"require_claim_evidence_links": False},
    )
    run_result = evaluate_run(contract, "run-1", _passing_executions())
    claim = ResearchClaim(
        claim_id="claim-1",
        kind="opportunity_exists",
        statement="teams lose hours to spreadsheets",
        stage="investigation",
    )

    promotion = evaluate_promotion(contract, "run-1", run_result, [claim])

    assert promotion.promoted is False
    assert "claim_evidence_missing" in {v.code.value for v in promotion.violations}
    assert "promotion_blocked" in {v.code.value for v in promotion.violations}


def test_the_claim_evidence_rule_fires_on_the_promotion_flag():
    contract = _contract(
        hard_requirements={"require_claim_evidence_links": False},
        promotion={"require_claim_evidence_links": True},
    )
    run_result = evaluate_run(contract, "run-1", _passing_executions())
    claim = ResearchClaim(
        claim_id="claim-1",
        kind="opportunity_exists",
        statement="teams lose hours to spreadsheets",
        stage="investigation",
    )

    promotion = evaluate_promotion(contract, "run-1", run_result, [claim])

    assert promotion.promoted is False
    assert "claim_evidence_missing" in {v.code.value for v in promotion.violations}
    assert "promotion_blocked" in {v.code.value for v in promotion.violations}


def test_promotion_is_blocked_when_a_competitor_claim_rides_on_a_failed_gate():
    contract = _contract()
    executions = _passing_executions()
    executions["investigation"] = _execution(
        "investigation", status=StageStatus.failed, error="upstream refused"
    )
    run_result = evaluate_run(contract, "run-1", executions)
    claim = ResearchClaim(
        claim_id="claim-1",
        kind="no_strong_competitor",
        statement="no competitor ships this",
        stage="investigation",
        evidence_refs=["doc-1"],
    )

    promotion = evaluate_promotion(contract, "run-1", run_result, [claim])

    assert promotion.promoted is False
    assert any(
        "no_strong_competitor" in reason for reason in _reasons(promotion)
    )


def test_the_same_evidence_produces_the_same_violations_twice():
    contract = _contract()
    executions = _passing_executions()
    executions["discovery"] = _execution("discovery", artifacts=[])

    first = evaluate_run(contract, "run-1", executions)
    second = evaluate_run(contract, "run-1", executions)

    assert first.model_dump_json() == second.model_dump_json()
    assert [v.code.value for v in first.violations] == [
        v.code.value for v in second.violations
    ]


def test_run_violations_are_sorted_by_stage_then_code():
    contract = _contract()
    executions = _passing_executions()
    executions["discovery"] = _execution("discovery", artifacts=[])
    executions["investigation"] = _execution("investigation", status=StageStatus.failed)

    result = evaluate_run(contract, "run-1", executions)

    stages = [violation.stage for violation in result.violations]
    assert stages == sorted(stages)
    assert result.violations[0].stage == "discovery"
    assert result.violations[-1].stage == "investigation"


def test_a_failed_stage_violation_does_not_carry_the_error_text():
    secret = "https://alice:hunter2@example.com/search?q=invoices"
    execution = _execution("discovery", status=StageStatus.failed, error=secret)

    result = evaluate_stage(_contract(), "run-1", "discovery", execution)

    assert _codes(result) == {"stage_failed"}
    violation = result.violations[0]
    assert secret not in violation.message
    assert secret not in str(violation.detail)
    assert "hunter2" not in str(violation.detail)

    # The timeout violation carries the same error text and must drop it too.
    timed_out = evaluate_stage(
        _contract(),
        "run-1",
        "discovery",
        _execution("discovery", status=StageStatus.timed_out, error=secret),
    )

    assert _codes(timed_out) == {"stage_timed_out"}
    assert secret not in str(timed_out.violations[0].detail)
    assert "hunter2" not in str(timed_out.violations[0].detail)


def test_an_invalid_artifact_violation_does_not_carry_the_invalid_reason():
    secret = "https://alice:hunter2@example.com/artifacts/invoices"
    execution = _execution(
        "discovery",
        artifacts=[_artifact("discovery", valid=False, invalid_reason=secret)],
    )

    result = evaluate_stage(_contract(), "run-1", "discovery", execution)

    assert _codes(result) == {"artifact_invalid"}
    violation = result.violations[0]
    assert secret not in str(violation.detail)
    assert "hunter2" not in str(violation.detail)
    assert violation.detail["artifact_id"] == "discovery-art-1"
    assert violation.detail["kind"] == "source_records"


def test_the_counter_evidence_rule_fires_on_the_hard_requirement_flag():
    contract = _contract(
        hard_requirements={"require_counter_evidence": True},
        promotion={"require_counter_evidence_gate": False},
    )
    executions = _passing_executions()
    executions["counter_evidence"] = _execution(
        "counter_evidence", status=StageStatus.failed, error="upstream refused"
    )
    run_result = evaluate_run(contract, "run-1", executions)

    promotion = evaluate_promotion(contract, "run-1", run_result, [])

    assert promotion.promoted is False
    assert any(
        "counter_evidence gate did not pass" in reason for reason in _reasons(promotion)
    )


def test_the_competitor_rule_fires_on_the_hard_requirement_flag():
    contract = _contract(
        hard_requirements={"require_competitor_search": True},
        promotion={"require_competitor_gate": False},
    )
    executions = _passing_executions()
    executions["investigation"] = _execution(
        "investigation", status=StageStatus.failed, error="upstream refused"
    )
    run_result = evaluate_run(contract, "run-1", executions)

    promotion = evaluate_promotion(contract, "run-1", run_result, [])

    assert promotion.promoted is False
    assert any(
        "competitor_search gate did not pass" in reason for reason in _reasons(promotion)
    )


def test_a_run_that_fails_its_run_level_thresholds_is_not_promoted():
    contract = _contract(
        evidence_thresholds={**THRESHOLDS, "promotion": {"min_independent_authors": 10}}
    )
    run_result = evaluate_run(contract, "run-1", _passing_executions())

    promotion = evaluate_promotion(contract, "run-1", run_result, [])

    assert run_result.passed is False
    assert promotion.promoted is False
    assert "promotion_blocked" in {v.code.value for v in promotion.violations}
    assert any("run-level thresholds" in reason for reason in _reasons(promotion))


def test_a_recorded_zero_with_no_query_families_does_not_satisfy_the_threshold():
    execution = _execution(
        "counter_evidence",
        artifacts=[_artifact("counter_evidence", item_count=0)],
        zero_result=ZeroResultOutcome(recorded=True),
    )

    result = evaluate_stage(_contract(), "run-1", "counter_evidence", execution)

    assert _codes(result) == {"evidence_threshold_unmet"}


def test_a_stage_that_has_not_produced_a_result_is_stage_missing():
    not_started = evaluate_stage(
        _contract(),
        "run-1",
        "discovery",
        _execution("discovery", status=StageStatus.not_started),
    )
    running = evaluate_stage(
        _contract(),
        "run-1",
        "discovery",
        _execution("discovery", status=StageStatus.running),
    )

    assert _codes(not_started) == {"stage_missing"}
    assert _codes(running) == {"stage_missing"}


def test_an_artifact_may_be_untraceable_when_the_stage_declares_no_tools():
    contract = _contract(
        discovery={"required_tools": [], "required_outputs": ["source_records"]}
    )
    execution = _execution(
        "discovery", artifacts=[_artifact("discovery", produced_by_tool_call_id=None)]
    )

    result = evaluate_stage(contract, "run-1", "discovery", execution)

    assert result.passed is True
    assert result.violations == []


def test_a_misfiled_execution_does_not_pass_the_stage_it_is_judged_against():
    contract = _contract()
    execution = _execution("discovery")

    result = evaluate_stage(contract, "run-1", "investigation", execution)

    assert _codes(result) == {"provenance_mismatch"}
    assert result.passed is False
    violation = result.violations[0]
    assert violation.detail["stage"] == "investigation"
    assert violation.detail["execution_stage"] == "discovery"

    # The same check guards the run level, because the run calls the stage gate.
    run_result = evaluate_run(contract, "run-1", {"investigation": execution})

    assert "provenance_mismatch" in {
        violation.code.value for violation in run_result.violations
    }
    assert run_result.passed is False


def test_a_recorded_zero_whose_families_were_never_searched_is_not_a_zero():
    execution = _execution(
        "counter_evidence",
        tool_calls=[_call("counter_evidence", query_family="unrelated")],
        artifacts=[_artifact("counter_evidence", item_count=0)],
        zero_result=ZeroResultOutcome(
            recorded=True,
            required_query_families=["problem", "solution"],
            completed_query_families=["problem", "solution"],
        ),
    )

    result = evaluate_stage(_contract(), "run-1", "counter_evidence", execution)

    assert _codes(result) == {"evidence_threshold_unmet"}
    assert result.passed is False

    # The unsearched families are free text from the evidence: only the count
    # reaches the reason.
    context = PredicateContext(run_id="run-1", execution=execution, claims=())
    passed, detail = PREDICATES["counter_evidence_present_or_explicit_zero"](context)

    assert passed is False
    assert "2 query families" in detail
    assert "problem" not in detail
    assert "solution" not in detail


def test_promotion_still_passes_when_a_stage_fails_and_all_stages_need_not_pass():
    contract = _contract(
        promotion={"require_all_stages_passed": False},
        evidence_thresholds={**THRESHOLDS, "discovery": {"min_distinct_queries": 2}},
    )
    run_result = evaluate_run(contract, "run-1", _passing_executions())

    promotion = evaluate_promotion(contract, "run-1", run_result, [])

    assert run_result.passed is False
    assert promotion.promoted is True
    assert not any("run-level thresholds" in reason for reason in _reasons(promotion))


def test_promotion_is_blocked_when_the_run_is_over_budget():
    contract = _contract(
        budgets={"version": "test-v1", "per_run": {"search_queries": 1.0}}
    )
    tracker = BudgetTracker(BudgetSpec(version="test-v1", per_run={"search_queries": 1.0}))
    with pytest.raises(BudgetExceeded):
        tracker.record("search_queries", 5.0, stage="discovery")
    run_result = evaluate_run(contract, "run-1", _passing_executions(), tracker)

    # No tracker is passed to the promotion gate: the fact comes from the run.
    promotion = evaluate_promotion(contract, "run-1", run_result, [])

    assert promotion.promoted is False
    assert any("'search_queries'" in reason for reason in _reasons(promotion))


def test_the_run_level_thresholds_count_only_the_stages_the_gate_evaluated():
    contract = _contract(
        evidence_thresholds={**THRESHOLDS, "promotion": {"min_independent_authors": 10}}
    )
    executions = _passing_executions()
    # An execution for a stage the contract does not require: the gate never
    # evaluates it, so its evidence is not part of the run.
    executions["ghost"] = StageExecution(
        stage="ghost",
        status=StageStatus.succeeded,
        tool_calls=[
            ToolCallProvenance(
                run_id="run-1",
                stage="ghost",
                agent="researcher",
                tool="search",
                purpose="collect authors",
                timestamp=FROZEN,
                query_or_operation="ghost query",
                result_ref="raw/ghost",
                result_count=10,
                status=ToolCallStatus.success,
                tool_call_id=None,
            )
        ],
        artifacts=[
            ArtifactRecord(
                artifact_id="ghost-author-1",
                stage="ghost",
                kind="author",
                ref="data/artifacts/run-1/ghost.jsonl",
                item_count=10,
                valid=False,
                produced_by_tool_call_id=None,
                created_at=FROZEN,
            )
        ],
    )

    run_result = evaluate_run(contract, "run-1", executions)
    promotion = evaluate_promotion(contract, "run-1", run_result, [])

    assert run_result.passed is False
    assert promotion.promoted is False
    assert "promotion_blocked" in {v.code.value for v in promotion.violations}
    assert any("run-level thresholds" in reason for reason in _reasons(promotion))


def test_a_misfiled_failed_execution_still_reports_the_failure():
    contract = _contract(promotion={"require_all_stages_passed": False})
    execution = _execution(
        "counter_evidence", status=StageStatus.failed, error="upstream refused"
    )

    result = evaluate_stage(contract, "run-1", "discovery", execution)

    assert _codes(result) == {"provenance_mismatch", "stage_failed"}
    assert result.passed is False

    # The status code is what blocks promotion, even where a failed stage is
    # allowed through.
    executions = _passing_executions()
    executions["discovery"] = execution
    run_result = evaluate_run(contract, "run-1", executions)
    promotion = evaluate_promotion(contract, "run-1", run_result, [])

    assert promotion.promoted is False
    assert any(
        "discovery" in reason and "failed" in reason for reason in _reasons(promotion)
    )


def test_a_stage_with_a_naive_timestamp_does_not_silently_skip_the_timeout():
    contract = _contract(discovery={"timeout_seconds": 30})

    # A naive timestamp is refused at construction, so it cannot switch the
    # timeout check off.
    with pytest.raises(ValidationError):
        _execution(
            "discovery",
            started_at=FROZEN.replace(tzinfo=None),
            finished_at=FROZEN.replace(tzinfo=None),
        )

    execution = _execution(
        "discovery",
        started_at=FROZEN,
        finished_at=FROZEN + timedelta(seconds=31),
    )

    result = evaluate_stage(contract, "run-1", "discovery", execution)

    assert _codes(result) == {"stage_timed_out"}
    assert result.passed is False


def test_the_run_level_thresholds_count_only_the_evidence_the_gate_accepted():
    contract = _contract(
        promotion={"require_all_stages_passed": False},
        evidence_thresholds={**THRESHOLDS, "promotion": {"min_independent_authors": 10}},
    )
    executions = _passing_executions()
    # The execution is filed under 'discovery' but belongs to another stage, so
    # the stage gate refuses it. Its evidence is acceptable in itself: it is
    # valid and it traces to a successful call of its own stage. The run-level
    # sum must not count it anyway, because the gate never accepted it.
    executions["discovery"] = StageExecution(
        stage="ghost",
        status=StageStatus.succeeded,
        tool_calls=[
            ToolCallProvenance(
                run_id="run-1",
                stage="ghost",
                agent="researcher",
                tool="search",
                purpose="collect authors",
                timestamp=FROZEN,
                query_or_operation="ghost query",
                result_ref="raw/ghost",
                result_count=10,
                status=ToolCallStatus.success,
                tool_call_id="ghost-call-1",
                query_family="problem",
            )
        ],
        artifacts=[
            ArtifactRecord(
                artifact_id="ghost-author-1",
                stage="ghost",
                kind="author",
                ref="data/artifacts/run-1/ghost.jsonl",
                item_count=10,
                valid=True,
                produced_by_tool_call_id="ghost-call-1",
                created_at=FROZEN,
            )
        ],
    )

    run_result = evaluate_run(contract, "run-1", executions)
    promotion = evaluate_promotion(contract, "run-1", run_result, [])

    assert "provenance_mismatch" in {
        violation.code.value for violation in run_result.violations
    }
    assert run_result.passed is False
    assert promotion.promoted is False
    assert any("run-level thresholds" in reason for reason in _reasons(promotion))


def test_a_none_execution_does_not_crash_the_run_gate():
    contract = _contract(
        evidence_thresholds={**THRESHOLDS, "promotion": {"min_independent_authors": 10}}
    )
    executions = _passing_executions()
    executions["discovery"] = None

    run_result = evaluate_run(contract, "run-1", executions)

    assert run_result.passed is False
    assert _codes(run_result.stages["discovery"]) == {"stage_missing"}
    # The missing execution contributes nothing to the run-level sum, and the
    # sum does not dereference it.
    assert any(
        violation.code is ViolationCode.evidence_threshold_unmet
        and violation.stage is None
        for violation in run_result.violations
    )


def test_a_metric_does_not_count_an_artifact_the_gate_rejected():
    contract = _contract()
    executions = _passing_executions()
    executions["counter_evidence"] = _execution(
        "counter_evidence",
        artifacts=[
            _artifact(
                "counter_evidence",
                artifact_id="counter_evidence-art-1",
                valid=False,
                invalid_reason="schema mismatch",
            ),
            _artifact(
                "counter_evidence",
                artifact_id="counter_evidence-art-2",
                produced_by_tool_call_id=None,
            ),
        ],
    )

    run_result = evaluate_run(contract, "run-1", executions)
    stage_result = run_result.stages["counter_evidence"]

    # The gate refused both artifacts, so the metric counts neither of them.
    assert stage_result.metrics["counter_evidence_items"] == 0
    assert "artifact_invalid" in _codes(stage_result)
    assert run_result.passed is False


def test_promotion_is_blocked_when_the_run_result_belongs_to_another_run():
    contract = _contract()
    run_result = evaluate_run(contract, "run-1", _passing_executions())

    promotion = evaluate_promotion(contract, "run-OTHER", run_result, [])

    assert promotion.promoted is False
    assert any("another run" in reason for reason in _reasons(promotion))
    violation = promotion.violations[0]
    assert violation.detail["run_id"] == "run-OTHER"
    assert violation.detail["result_run_id"] == "run-1"


def test_the_status_violations_carry_their_codes_messages_and_details():
    def _tuples(result: GateResult) -> list[tuple]:
        return [
            (violation.code, violation.message, violation.detail)
            for violation in result.violations
        ]

    contract = _contract()

    failed = evaluate_stage(
        contract, "run-1", "discovery", _execution("discovery", status=StageStatus.failed)
    )
    timed_out = evaluate_stage(
        contract,
        "run-1",
        "discovery",
        _execution("discovery", status=StageStatus.timed_out),
    )
    not_started = evaluate_stage(
        contract,
        "run-1",
        "discovery",
        _execution("discovery", status=StageStatus.not_started),
    )

    assert _tuples(failed) == [
        (ViolationCode.stage_failed, "stage 'discovery' failed", {"stage": "discovery"})
    ]
    assert _tuples(timed_out) == [
        (
            ViolationCode.stage_timed_out,
            "stage 'discovery' timed out",
            {"stage": "discovery"},
        )
    ]
    assert _tuples(not_started) == [
        (
            ViolationCode.stage_missing,
            "stage 'discovery' has not produced a result",
            {"stage": "discovery", "status": "not_started"},
        )
    ]


def test_a_malformed_fetch_url_does_not_crash_the_gate():
    contract = _contract(
        evidence_thresholds={
            **THRESHOLDS,
            "discovery": {"min_distinct_queries": 1, "min_source_domains": 2},
        }
    )
    execution = _execution(
        "discovery",
        tool_calls=[
            _call("discovery"),
            _call(
                "discovery",
                tool="fetch",
                tool_call_id="discovery-fetch-1",
                query_or_operation="http://[::1",
            ),
        ],
    )

    result = evaluate_stage(contract, "run-1", "discovery", execution)

    # A URL the parser cannot read has no host, so it counts for no domain.
    assert result.metrics["source_domains"] == 0
    assert any(
        violation.code is ViolationCode.evidence_threshold_unmet
        and violation.detail.get("metric") == "source_domains"
        for violation in result.violations
    )

    # The run gate computes the same metric, so it does not raise either.
    executions = _passing_executions()
    executions["discovery"] = execution
    run_result = evaluate_run(contract, "run-1", executions)

    assert run_result.stages["discovery"].metrics["source_domains"] == 0
    assert run_result.passed is False


def test_the_metric_is_stricter_than_the_gate_for_a_stage_that_declares_no_tools():
    contract = _contract(
        discovery={"required_tools": [], "required_outputs": ["document"]},
        evidence_thresholds={**THRESHOLDS, "discovery": {"min_new_documents": 1}},
    )
    execution = _execution(
        "discovery",
        artifacts=[
            _artifact("discovery", kind="document", produced_by_tool_call_id=None)
        ],
    )

    result = evaluate_stage(contract, "run-1", "discovery", execution)

    # The gate accepts the artifact: the stage declares no required tools, so
    # its derivation check does not apply.
    assert "artifact_invalid" not in _codes(result)
    # The metric refuses it anyway, because nothing ties it to a tool call.
    assert result.metrics["new_documents"] == 0
    assert "evidence_threshold_unmet" in _codes(result)


def test_the_artifact_predicates_require_a_traceable_artifact():
    def _artifact(kind: str, artifact_id: str, handle: str | None) -> ArtifactRecord:
        return ArtifactRecord(
            artifact_id=artifact_id,
            stage="clustering",
            kind=kind,
            ref="data/artifacts/run-1/clustering.jsonl",
            item_count=1,
            produced_by_tool_call_id=handle,
            created_at=FROZEN,
        )

    untraceable = StageExecution(
        stage="clustering",
        status=StageStatus.succeeded,
        tool_calls=[],
        artifacts=[
            _artifact("neighbor_check", "neighbor-check-1", None),
            _artifact("duplicate_review", "duplicate-review-1", None),
        ],
    )
    context = PredicateContext(run_id="run-1", execution=untraceable, claims=())

    assert PREDICATES["nearest_neighbor_checked"](context)[0] is False
    assert PREDICATES["duplicate_candidates_reviewed"](context)[0] is False

    # The same artifacts, tracing to a successful call of the stage, pass.
    traceable = StageExecution(
        stage="clustering",
        status=StageStatus.succeeded,
        tool_calls=[
            ToolCallProvenance(
                run_id="run-1",
                stage="clustering",
                agent="researcher",
                tool="qdrant",
                purpose="cluster the records",
                timestamp=FROZEN,
                query_or_operation="cluster query",
                result_ref="raw/clustering",
                result_count=1,
                status=ToolCallStatus.success,
                tool_call_id="clustering-call-1",
            )
        ],
        artifacts=[
            _artifact("neighbor_check", "neighbor-check-1", "clustering-call-1"),
            _artifact("duplicate_review", "duplicate-review-1", "clustering-call-1"),
        ],
    )
    context = PredicateContext(run_id="run-1", execution=traceable, claims=())

    passed, detail = PREDICATES["nearest_neighbor_checked"](context)

    assert passed is True
    assert "neighbor-check-1" in detail
    assert PREDICATES["duplicate_candidates_reviewed"](context)[0] is True


def test_promotion_reads_the_claims_of_a_one_shot_iterator():
    contract = _contract()
    run_result = evaluate_run(contract, "run-1", _passing_executions())
    claim = ResearchClaim(
        claim_id="claim-1",
        kind="opportunity_exists",
        statement="teams lose hours to spreadsheets",
        stage="investigation",
    )

    from_list = evaluate_promotion(contract, "run-1", run_result, [claim])
    from_generator = evaluate_promotion(
        contract, "run-1", run_result, (item for item in [claim])
    )

    # The claim carries no evidence reference, so the rule blocks the
    # promotion. A one-shot iterator must not lose the claim on the second
    # read and satisfy the rule by accident.
    assert from_list.promoted is False
    assert from_generator.promoted is False
    assert from_list.model_dump_json() == from_generator.model_dump_json()
    assert "claim_evidence_missing" in {
        violation.code.value for violation in from_generator.violations
    }


def test_a_stage_with_no_end_timestamp_does_not_skip_its_timeout():
    contract = _contract(discovery={"timeout_seconds": 30})
    execution = _execution("discovery", finished_at=None)

    result = evaluate_stage(contract, "run-1", "discovery", execution)

    # A stage that cannot prove it finished inside its timeout is treated as
    # having overrun it.
    assert _codes(result) == {"stage_timed_out"}
    assert result.passed is False
    violation = result.violations[0]
    assert violation.detail["timeout_seconds"] == 30
    assert violation.detail["elapsed_seconds"] is None


def test_one_host_with_trailing_whitespace_is_one_source():
    contract = _contract(
        evidence_thresholds={
            **THRESHOLDS,
            "discovery": {"min_distinct_queries": 1, "min_source_domains": 2},
        }
    )
    execution = _execution(
        "discovery",
        tool_calls=[
            _call("discovery"),
            _call(
                "discovery",
                tool="fetch",
                tool_call_id="discovery-fetch-1",
                query_or_operation="https://a.com",
            ),
            _call(
                "discovery",
                tool="fetch",
                tool_call_id="discovery-fetch-2",
                query_or_operation="https://a.com ",
            ),
            _call(
                "discovery",
                tool="fetch",
                tool_call_id="discovery-fetch-3",
                query_or_operation="https://a.com  ",
            ),
        ],
    )

    result = evaluate_stage(contract, "run-1", "discovery", execution)

    # One real source, so the two-source minimum is not met.
    assert result.metrics["source_domains"] == 1
    assert any(
        violation.code is ViolationCode.evidence_threshold_unmet
        and violation.detail.get("metric") == "source_domains"
        for violation in result.violations
    )


def test_search_source_domains_are_counted_from_durable_provenance():
    contract = _contract(
        evidence_thresholds={
            **THRESHOLDS,
            "discovery": {"min_distinct_queries": 1, "min_source_domains": 2},
        }
    )
    execution = _execution(
        "discovery",
        tool_calls=[
            _call(source_domains=["hn.algolia.com"]),
            _call(
                tool_call_id="github-search",
                query_or_operation="second query",
                source_domains=["api.github.com"],
            ),
        ],
    )

    result = evaluate_stage(contract, "run-1", "discovery", execution)

    assert result.metrics["source_domains"] == 2


def test_one_query_with_trailing_whitespace_is_one_query():
    execution = _execution(
        "discovery",
        tool_calls=[
            _call(
                "discovery",
                query_or_operation="alpha",
                query_family="problem",
            ),
            _call(
                "discovery",
                tool_call_id="discovery-call-2",
                query_or_operation="alpha ",
                query_family="problem ",
            ),
            _call(
                "discovery",
                tool_call_id="discovery-call-3",
                query_or_operation="alpha  ",
                query_family="problem\t",
            ),
        ],
    )

    result = evaluate_stage(_contract(), "run-1", "discovery", execution)

    assert result.metrics["distinct_queries"] == 1
    assert result.metrics["query_families"] == 1


def test_a_run_result_judged_under_another_contract_is_not_promoted():
    loose = _contract(
        evidence_thresholds={**THRESHOLDS, "discovery": {"min_distinct_queries": 2}}
    )
    tight = _contract(
        evidence_thresholds={**THRESHOLDS, "discovery": {"min_distinct_queries": 8}}
    )
    executions = _passing_executions()
    executions["discovery"] = _execution(
        "discovery",
        tool_calls=[
            _call("discovery", query_or_operation="alpha"),
            _call(
                "discovery",
                tool_call_id="discovery-call-2",
                query_or_operation="beta",
            ),
            _call(
                "discovery",
                tool_call_id="discovery-call-3",
                query_or_operation="gamma",
            ),
        ],
    )

    run_result = evaluate_run(loose, "run-1", executions)
    control = evaluate_run(tight, "run-1", executions)

    # The loose contract passes this evidence, the tight one does not.
    assert run_result.passed is True
    assert control.passed is False

    promotion = evaluate_promotion(tight, "run-1", run_result, [])

    # The result was judged under another contract, so it says nothing about
    # the tight one.
    assert promotion.promoted is False
    assert "promotion_blocked" in {violation.code.value for violation in promotion.violations}
    assert any("another contract" in reason for reason in _reasons(promotion))
    violation = promotion.violations[0]
    assert violation.detail["contract_fingerprint"] != (
        violation.detail["result_contract_fingerprint"]
    )

    # The same result under the contract it was judged with is promoted.
    same = evaluate_promotion(loose, "run-1", run_result, [])

    assert same.promoted is True


def test_a_run_level_counter_evidence_threshold_accepts_a_recorded_zero():
    contract = _contract(
        evidence_thresholds={
            **THRESHOLDS,
            "promotion": {"min_counter_evidence_items_or_explicit_zero": 1},
        }
    )
    executions = _passing_executions()
    executions["counter_evidence"] = _execution(
        "counter_evidence",
        tool_calls=[
            _call("counter_evidence"),
            _call(
                "counter_evidence",
                tool_call_id="counter_evidence-call-2",
                query_family="solution",
            ),
        ],
        artifacts=[_artifact("counter_evidence", item_count=0)],
        zero_result=ZeroResultOutcome(
            recorded=True,
            required_query_families=["problem", "solution"],
            completed_query_families=["problem", "solution"],
        ),
    )

    run_result = evaluate_run(contract, "run-1", executions)
    promotion = evaluate_promotion(contract, "run-1", run_result, [])

    # The stage-level check accepts the recorded zero, so the run-level check
    # accepts the same one.
    assert run_result.stages["counter_evidence"].passed is True
    assert run_result.passed is True
    assert not any(
        violation.code is ViolationCode.evidence_threshold_unmet
        and violation.stage is None
        for violation in run_result.violations
    )
    assert promotion.promoted is True
    assert not any("run-level thresholds" in reason for reason in _reasons(promotion))


def test_a_whitespace_only_query_is_not_a_query():
    call = _call("discovery", query_or_operation="   ")
    execution = _execution("discovery", tool_calls=[call])
    context = PredicateContext(run_id="run-1", execution=execution, claims=())

    passed, detail = PREDICATES["non_empty_queries"](context)

    assert passed is False
    assert "no successful search call carries a query" in detail

    # The stage gate reads the same thing, and the metric agrees with it.
    result = evaluate_stage(_contract(), "run-1", "discovery", execution)

    assert "quality_check_failed" in _codes(result)
    assert result.metrics["distinct_queries"] == 0
    assert result.passed is False


def test_a_run_level_counter_evidence_zero_comes_from_the_counter_evidence_stage():
    contract = _contract(
        required_stages=["discovery", "counter_evidence"],
        stages={
            "discovery": {
                "required_tools": ["search"],
                "required_outputs": ["source_records"],
                "quality_checks": ["non_empty_queries"],
                "gate_key": "discovery",
            },
            "counter_evidence": {
                "required_tools": ["search"],
                "required_outputs": ["counter_evidence"],
                "quality_checks": [],
                "gate_key": "counter_evidence",
            },
        },
        evidence_thresholds={
            "promotion": {"min_counter_evidence_items_or_explicit_zero": 1},
        },
        hard_requirements={"require_competitor_search": False},
        promotion={"require_competitor_gate": False},
    )
    executions = {
        # The discovery stage searched and found nothing, so it recorded a
        # legitimate zero of its own.
        "discovery": _execution(
            "discovery",
            artifacts=[_artifact("discovery", item_count=0)],
            zero_result=ZeroResultOutcome(
                recorded=True,
                required_query_families=["problem"],
                completed_query_families=["problem"],
            ),
        ),
        # The counter-evidence stage produced no item and recorded no zero.
        "counter_evidence": _execution(
            "counter_evidence",
            artifacts=[_artifact("counter_evidence", item_count=0)],
        ),
    }

    run_result = evaluate_run(contract, "run-1", executions)
    promotion = evaluate_promotion(contract, "run-1", run_result, [])

    # The zero belongs to the discovery stage, so it says nothing about the
    # run-level counter-evidence minimum: only the counter-evidence stage can
    # satisfy it with a zero.
    assert any(
        violation.code is ViolationCode.evidence_threshold_unmet
        and violation.stage is None
        and violation.detail.get("metric") == "counter_evidence_items"
        for violation in run_result.violations
    )
    assert run_result.passed is False
    assert promotion.promoted is False
    assert any("run-level thresholds" in reason for reason in _reasons(promotion))


def test_a_run_level_distinct_threshold_counts_the_union_not_the_sum():
    contract = _contract(
        evidence_thresholds={
            **THRESHOLDS,
            "promotion": {
                "min_source_domains": 3,
                "min_distinct_queries": 2,
                "min_query_families": 2,
            },
        },
    )
    executions = {}
    for stage in ("discovery", "counter_evidence", "investigation"):
        # Every stage sees the same host, the same query, and the same family.
        executions[stage] = _execution(
            stage,
            tool_calls=[
                _call(stage, query_or_operation="alpha", query_family="problem"),
                _call(
                    stage,
                    tool="fetch",
                    tool_call_id=f"{stage}-fetch-1",
                    query_or_operation="https://a.com",
                ),
            ],
        )

    run_result = evaluate_run(contract, "run-1", executions)
    run_level = {
        violation.detail.get("metric"): violation.detail.get("value")
        for violation in run_result.violations
        if violation.code is ViolationCode.evidence_threshold_unmet
        and violation.stage is None
    }

    # Each stage saw one host, one query, and one family. The run saw one of
    # each too: the sum of the per-stage counts would say three.
    for stage in ("discovery", "counter_evidence", "investigation"):
        metrics = run_result.stages[stage].metrics
        assert metrics["source_domains"] == 1
        assert metrics["distinct_queries"] == 1
        assert metrics["query_families"] == 1
    assert run_level["source_domains"] == 1
    assert run_level["distinct_queries"] == 1
    assert run_level["query_families"] == 1
    assert run_result.passed is False


def test_the_budget_checked_is_the_contract_budget():
    contract = _contract(
        budgets={"version": "test-v1", "per_run": {"search_queries": 2.0}}
    )
    tracker = BudgetTracker(
        BudgetSpec(version="test-v1", per_run={"search_queries": 1000.0})
    )
    tracker.record("search_queries", 50.0)

    # The tracker's own spec is looser than the contract's, so it reports no
    # overrun of its own.
    assert tracker.exceeded() == []

    run_result = evaluate_run(contract, "run-1", _passing_executions(), tracker)
    dimensions = {
        violation.detail.get("dimension")
        for violation in run_result.violations
        if violation.code is ViolationCode.budget_exceeded
    }

    # The contract's limit is the one that was checked.
    assert dimensions == {"search_queries"}
    assert run_result.passed is False

    promotion = evaluate_promotion(contract, "run-1", run_result, [])

    assert promotion.promoted is False
    assert any("'search_queries'" in reason for reason in _reasons(promotion))


def test_an_artifact_belonging_to_another_stage_is_a_mismatch():
    contract = _contract(
        discovery={"required_tools": [], "required_outputs": ["source_records"]}
    )
    execution = _execution(
        "discovery",
        artifacts=[_artifact("discovery").model_copy(update={"stage": "ghost"})],
    )

    result = evaluate_stage(contract, "run-1", "discovery", execution)

    assert "provenance_mismatch" in _codes(result)
    assert result.passed is False
    violation = next(
        violation
        for violation in result.violations
        if violation.code is ViolationCode.provenance_mismatch
    )
    assert violation.detail["stage"] == "discovery"
    assert violation.detail["artifact_stage"] == "ghost"


def test_the_timeout_message_says_which_case_it_is():
    contract = _contract(discovery={"timeout_seconds": 30})

    overrun = evaluate_stage(
        contract,
        "run-1",
        "discovery",
        _execution(
            "discovery",
            started_at=FROZEN,
            finished_at=FROZEN + timedelta(seconds=31),
        ),
    )
    unknown = evaluate_stage(
        contract, "run-1", "discovery", _execution("discovery", finished_at=None)
    )

    assert _codes(overrun) == {"stage_timed_out"}
    assert _codes(unknown) == {"stage_timed_out"}
    # A proven overrun says so, and an elapsed time that cannot be computed
    # says that instead.
    assert (
        overrun.violations[0].message
        == "stage 'discovery' ran longer than its timeout"
    )
    assert (
        unknown.violations[0].message
        == "stage 'discovery' cannot be shown to have finished inside its timeout"
    )
    assert overrun.violations[0].message != unknown.violations[0].message
    # The detail keys stay as they are.
    assert overrun.violations[0].detail["elapsed_seconds"] == 31
    assert unknown.violations[0].detail["elapsed_seconds"] is None


def test_a_misfiled_artifact_is_not_counted_towards_a_threshold():
    contract = _contract(
        discovery={"required_outputs": ["source_records", "document"]},
        evidence_thresholds={
            **THRESHOLDS,
            "discovery": {"min_distinct_queries": 1, "min_new_documents": 3},
        },
    )
    execution = _execution(
        "discovery",
        artifacts=[
            _artifact("discovery"),
            *[
                _artifact("discovery").model_copy(
                    update={
                        "artifact_id": f"ghost-document-{index}",
                        "stage": "ghost",
                        "kind": "document",
                        "ref": f"data/artifacts/run-1/ghost-{index}.jsonl",
                    }
                )
                for index in range(5)
            ],
        ],
    )

    result = evaluate_stage(contract, "run-1", "discovery", execution)

    # The five documents are filed under another stage, so the metric of this
    # stage counts none of them and its threshold is not met.
    assert result.metrics["new_documents"] == 0
    assert "evidence_threshold_unmet" in _codes(result)
    assert result.passed is False
    threshold_violation = next(
        violation
        for violation in result.violations
        if violation.code is ViolationCode.evidence_threshold_unmet
    )
    assert threshold_violation.detail["metric"] == "new_documents"
    assert threshold_violation.detail["value"] == 0

    run_result = evaluate_run(
        contract, "run-1", {**_passing_executions(), "discovery": execution}
    )

    assert run_result.passed is False


def test_evaluate_run_does_not_raise_on_an_invalid_contract():
    contract = _contract(
        evidence_thresholds={
            **THRESHOLDS,
            # The extra field names a metric that does not exist, so the
            # contract is invalid, and its value cannot be serialised.
            "discovery": {"min_distinct_queries": 1, "unexpected": object()},
        }
    )

    result = evaluate_run(contract, "run-1", _passing_executions())

    # The gate reports the invalid contract instead of raising.
    assert "contract_invalid" in {
        violation.code.value for violation in result.violations
    }
    assert result.passed is False
    assert result.contract_fingerprint == ""


def test_a_stage_that_finished_before_it_started_does_not_skip_its_timeout():
    contract = _contract(discovery={"timeout_seconds": 30})
    execution = _execution(
        "discovery",
        started_at=FROZEN,
        finished_at=FROZEN - timedelta(seconds=5),
    )

    result = evaluate_stage(contract, "run-1", "discovery", execution)

    # A stage that finished before it started has no duration, so it cannot
    # be shown to have finished inside its timeout.
    assert _codes(result) == {"stage_timed_out"}
    assert result.passed is False
    assert (
        result.violations[0].message
        == "stage 'discovery' cannot be shown to have finished inside its timeout"
    )


def test_a_misfiled_artifact_does_not_satisfy_a_quality_check():
    def _artifact(kind: str, artifact_id: str, stage: str) -> ArtifactRecord:
        return ArtifactRecord(
            artifact_id=artifact_id,
            stage=stage,
            kind=kind,
            ref="data/artifacts/run-1/clustering.jsonl",
            item_count=1,
            produced_by_tool_call_id="clustering-call-1",
            created_at=FROZEN,
        )

    def _execution(artifacts: list[ArtifactRecord]) -> StageExecution:
        return StageExecution(
            stage="clustering",
            status=StageStatus.succeeded,
            tool_calls=[
                ToolCallProvenance(
                    run_id="run-1",
                    stage="clustering",
                    agent="researcher",
                    tool="qdrant",
                    purpose="cluster the records",
                    timestamp=FROZEN,
                    query_or_operation="cluster query",
                    result_ref="raw/clustering",
                    result_count=1,
                    status=ToolCallStatus.success,
                    tool_call_id="clustering-call-1",
                )
            ],
            artifacts=artifacts,
        )

    # The artifacts are valid and trace to a successful call of the stage, but
    # they are filed under another stage.
    misfiled = _execution(
        [
            _artifact("neighbor_check", "neighbor-check-1", "validation"),
            _artifact("duplicate_review", "duplicate-review-1", "validation"),
        ]
    )
    context = PredicateContext(run_id="run-1", execution=misfiled, claims=())

    assert PREDICATES["nearest_neighbor_checked"](context)[0] is False
    assert PREDICATES["duplicate_candidates_reviewed"](context)[0] is False

    # The same artifacts, filed under the stage that owns the execution, pass.
    filed = _execution(
        [
            _artifact("neighbor_check", "neighbor-check-1", "clustering"),
            _artifact("duplicate_review", "duplicate-review-1", "clustering"),
        ]
    )
    context = PredicateContext(run_id="run-1", execution=filed, claims=())

    passed, detail = PREDICATES["nearest_neighbor_checked"](context)

    assert passed is True
    assert "neighbor-check-1" in detail
    assert PREDICATES["duplicate_candidates_reviewed"](context)[0] is True


def test_a_run_level_minimum_threshold_takes_the_smallest_stage_value():
    contract = _contract(
        required_stages=["discovery", "counter_evidence"],
        stages={
            "discovery": {
                "required_tools": ["search"],
                "required_outputs": ["cluster_neighbors_checked"],
                "quality_checks": [],
                "gate_key": "discovery",
            },
            "counter_evidence": {
                "required_tools": ["search"],
                "required_outputs": ["cluster_neighbors_checked"],
                "quality_checks": [],
                "gate_key": "counter_evidence",
            },
        },
        evidence_thresholds={"promotion": {"min_neighbors_checked_per_cluster": 5}},
        hard_requirements={"require_competitor_search": False},
        promotion={"require_competitor_gate": False},
    )
    executions = {
        stage: _execution(
            stage,
            artifacts=[
                _artifact(
                    stage,
                    kind="cluster_neighbors_checked",
                    item_count=3,
                )
            ],
        )
        for stage in ("discovery", "counter_evidence")
    }

    run_result = evaluate_run(contract, "run-1", executions)
    run_level = {
        violation.detail.get("metric"): violation.detail.get("value")
        for violation in run_result.violations
        if violation.code is ViolationCode.evidence_threshold_unmet
        and violation.stage is None
    }

    # Each stage checked three neighbours, so the smallest cluster check of
    # the run is three. The metric is a minimum, not a count: summing the two
    # stage values would say six and meet the minimum of five.
    for stage in ("discovery", "counter_evidence"):
        assert run_result.stages[stage].metrics["neighbors_checked_per_cluster"] == 3
    assert run_level["neighbors_checked_per_cluster"] == 3
    assert run_result.passed is False


def test_a_foreign_call_does_not_count_for_the_stage_that_is_judged():
    contract = _contract(
        discovery={"required_tools": ["search", "fetch"]},
        evidence_thresholds={**THRESHOLDS, "discovery": {"min_distinct_queries": 2}},
        hard_requirements={"require_provenance": False},
    )
    execution = _execution(
        "discovery",
        tool_calls=[
            _call("discovery", query_or_operation="alpha"),
            _call("other", tool_call_id="other-call-1", query_or_operation="beta"),
            _call(
                "other",
                tool="fetch",
                tool_call_id="other-fetch-1",
                query_or_operation="https://a.com",
            ),
        ],
    )

    result = evaluate_stage(contract, "run-1", "discovery", execution)

    # The stage made one search of its own. The other calls belong to another
    # stage, so they are not this stage's evidence.
    assert result.metrics["distinct_queries"] == 1
    # The fetch the stage never made cannot be borrowed from another stage.
    assert "tool_missing" in _codes(result)
    # One own query is not the two the threshold asks for.
    assert "evidence_threshold_unmet" in _codes(result)

    # The same evidence under a contract that requires provenance reports the
    # foreign calls instead of counting them.
    strict = _contract(
        discovery={"required_tools": ["search", "fetch"]},
        evidence_thresholds={**THRESHOLDS, "discovery": {"min_distinct_queries": 2}},
    )

    strict_result = evaluate_stage(strict, "run-1", "discovery", execution)

    assert "provenance_mismatch" in _codes(strict_result)


def test_the_shipped_contract_fails_a_stage_that_missed_a_mandatory_query_family():
    contract = load_research_contract()
    # Six distinct queries over three worker-owned families: everything the
    # shipped contract asks of the counter-evidence stage is met, except the
    # negative family, which is mandatory for validation.
    families = ("direct_pain", "alternative_to", "manual_workflow", "direct_pain")
    calls = [
        _call(
            "counter_evidence",
            tool_call_id=f"counter_evidence-call-{index}",
            query_or_operation=f"query {index}",
            query_family=family,
        )
        for index, family in enumerate(families, start=1)
    ]
    calls.extend(
        _call(
            "counter_evidence",
            tool_call_id=f"counter_evidence-call-{index}",
            query_or_operation=f"query {index}",
            query_family="direct_pain",
        )
        for index in range(5, 7)
    )
    execution = _execution(
        "counter_evidence",
        tool_calls=calls,
        artifacts=[_artifact("counter_evidence", item_count=1)],
    )

    result = evaluate_stage(contract, "run-1", "counter_evidence", execution)

    # The gate enforces the plan, so the stage fails on the family it never
    # searched and on nothing else.
    checks = {
        violation.detail.get("check")
        for violation in result.violations
        if violation.code is ViolationCode.quality_check_failed
    }
    assert checks == {"mandatory_query_families"}
    assert result.passed is False
    assert "failed_solution" in str(result.violations[0].detail)


def test_a_ran_stage_with_no_record_for_a_limited_dimension_fails_budget_unmeasured():
    contract = _contract(
        discovery={"required_tools": [], "required_outputs": []},
        budgets={
            "version": "test-v1",
            "per_stage": {"discovery": {"search_queries": 40}},
        },
    )
    tracker = BudgetTracker(contract.budgets)
    # The stage ran but never recorded search_queries: the gate fails it,
    # because an unmeasured limited dimension is a verdict, not a gap.
    result = evaluate_stage(
        contract, "run-1", "discovery", _execution("discovery"), tracker
    )
    assert _codes(result) == {"budget_unmeasured"}
    assert result.passed is False
    violation = result.violations[0]
    assert "search_queries" in violation.message
    assert "discovery" in violation.message
    assert violation.detail == {"dimension": "search_queries", "stage": "discovery"}


def test_a_ran_stage_that_records_a_zero_for_a_limited_dimension_passes():
    contract = _contract(
        discovery={"required_tools": [], "required_outputs": []},
        budgets={
            "version": "test-v1",
            "per_stage": {"discovery": {"search_queries": 40}},
        },
    )
    tracker = BudgetTracker(contract.budgets)
    tracker.record("search_queries", 0.0, stage="discovery")
    assert tracker.spent("search_queries", stage="discovery") == 0.0
    result = evaluate_stage(
        contract, "run-1", "discovery", _execution("discovery"), tracker
    )
    assert result.passed is True
    assert _codes(result) == set()


def test_a_per_run_only_dimension_never_triggers_budget_unmeasured():
    contract = _contract(
        discovery={"required_tools": [], "required_outputs": []},
        budgets={
            "version": "test-v1",
            "per_run": {"browser_minutes": 30},
            "per_stage": {"discovery": {"search_queries": 40}},
        },
    )
    tracker = BudgetTracker(contract.budgets)
    tracker.record("search_queries", 0.0, stage="discovery")
    # browser_minutes is limited per run only: no tool incurs it, no stage can
    # declare it, and the gate never flags a per-run-only dimension per stage.
    result = evaluate_stage(
        contract, "run-1", "discovery", _execution("discovery"), tracker
    )
    assert result.passed is True
    assert _codes(result) == set()

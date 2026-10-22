from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from product_hunter.research.investigation.areas import AreaFinding, ResearchArea
from product_hunter.research.investigation.checkpoint import (
    InvestigationCheckpoint,
    InvestigationStep,
    StepStatus,
    apply_step,
    build_checkpoint,
    completed_steps,
    input_hash_for,
    pending_steps,
    step_id_for,
)
from product_hunter.research.investigation.competitors import (
    Competitor,
    CompetitorConfidence,
)
from product_hunter.research.investigation.counter_evidence import (
    CounterEvidenceItem,
    CounterEvidenceKind,
)
from product_hunter.research.investigation.pipeline import (
    InvestigationRequest,
    build_plan,
)

FROZEN = datetime(2026, 10, 21, 12, 0, 0, tzinfo=timezone.utc)
PROBLEM = "teams hate spreadsheets"


def _request() -> InvestigationRequest:
    return InvestigationRequest(
        investigation_id="inv-1",
        run_id="run-1",
        problem=PROBLEM,
    )


def _step(step_id: str = "step-1", **overrides) -> InvestigationStep:
    data = {
        "step_id": step_id,
        "area": ResearchArea.competitor,
        "family": "competitor_alternative",
        "query": "teams hate spreadsheets alternative",
        "status": StepStatus.completed,
        "input_hash": input_hash_for("teams hate spreadsheets alternative"),
        "tool_call_id": "call-1",
    }
    data.update(overrides)
    return InvestigationStep(**data)


def _checkpoint(
    steps: list[InvestigationStep], contract_fingerprint: str = ""
) -> InvestigationCheckpoint:
    return build_checkpoint(
        "inv-1",
        "run-1",
        PROBLEM,
        steps,
        contract_fingerprint,
        created_at=FROZEN,
    )


def test_the_step_id_and_the_input_hash_are_deterministic():
    first = step_id_for(ResearchArea.competitor, "competitor_alternative", "a query")
    second = step_id_for("competitor", "competitor_alternative", "a query")

    assert first == second
    assert len(first) == 16
    assert input_hash_for("a query") == input_hash_for("a query")
    assert len(input_hash_for("a query")) == 16
    assert step_id_for(ResearchArea.competitor, "competitor_alternative", "a query") != (
        step_id_for(ResearchArea.competitor, "competitor_alternative", "another query")
    )


def test_apply_step_is_idempotent():
    checkpoint = _checkpoint([])
    step = _step()

    once = apply_step(checkpoint, step)
    twice = apply_step(once, step)

    assert once == twice
    assert len(twice.steps) == 1
    # The checkpoint is a value: the old one is untouched.
    assert checkpoint.steps == []


def test_pending_steps_returns_only_the_missing_steps():
    plan = build_plan(_request())
    done = plan.steps[0]
    checkpoint = _checkpoint([_step(step_id=done.step_id, query=done.query)])

    pending = pending_steps(checkpoint, plan)

    assert len(pending) == len(plan.steps) - 1
    assert done.step_id not in {step.step_id for step in pending}


def test_completed_steps_reads_the_finished_ones():
    step = _step()
    checkpoint = _checkpoint([step, _step(step_id="step-2", status=StepStatus.failed)])

    completed = completed_steps(checkpoint)

    assert list(completed) == [step.step_id]


def test_a_checkpoint_from_a_different_problem_is_refused():
    with pytest.raises(ValueError) as excinfo:
        build_checkpoint(
            "inv-1",
            "run-1",
            PROBLEM,
            [],
            expected_problem_hash=input_hash_for("another problem"),
        )

    assert "different problem" in str(excinfo.value)


def test_a_step_with_the_wrong_input_hash_is_refused():
    with pytest.raises(ValueError) as excinfo:
        build_checkpoint(
            "inv-1",
            "run-1",
            PROBLEM,
            [_step(input_hash=input_hash_for("another query"))],
        )

    assert "input hash" in str(excinfo.value)


def test_a_checkpoint_from_a_different_contract_is_allowed_and_the_fingerprint_is_carried():
    checkpoint = build_checkpoint(
        "inv-1",
        "run-1",
        PROBLEM,
        [],
        contract_fingerprint="contract-v2",
        created_at=FROZEN,
    )

    assert checkpoint.contract_fingerprint == "contract-v2"
    assert checkpoint.problem_hash == input_hash_for(PROBLEM)


def test_a_step_holds_the_results_its_call_produced():
    step = _step(
        competitors=[
            Competitor(
                competitor_id="comp-1",
                name="Sheetwise",
                confidence=CompetitorConfidence.high,
            )
        ],
        counter_evidence=[
            CounterEvidenceItem(
                item_id="ce-1",
                kind=CounterEvidenceKind.existing_solution,
                summary="a suite already does this",
            )
        ],
        findings=[
            AreaFinding(
                finding_id="finding-1",
                area=ResearchArea.market_validation,
                summary="a vendor already sells this",
            )
        ],
    )

    assert len(step.competitors) == 1
    assert len(step.counter_evidence) == 1
    assert len(step.findings) == 1


def test_a_checkpoint_from_another_investigation_is_refused():
    with pytest.raises(ValueError) as excinfo:
        build_checkpoint(
            "inv-1",
            "run-1",
            PROBLEM,
            [],
            expected_investigation_id="inv-2",
        )

    assert "different investigation" in str(excinfo.value)


def test_a_checkpoint_from_another_run_is_refused():
    with pytest.raises(ValueError) as excinfo:
        build_checkpoint("inv-1", "run-1", PROBLEM, [], expected_run_id="run-2")

    assert "different run" in str(excinfo.value)


def test_the_same_checkpoint_is_accepted():
    checkpoint = build_checkpoint(
        "inv-1",
        "run-1",
        PROBLEM,
        [],
        expected_investigation_id="inv-1",
        expected_run_id="run-1",
    )

    assert checkpoint.investigation_id == "inv-1"
    assert checkpoint.run_id == "run-1"


def test_a_naive_created_at_is_refused():
    with pytest.raises(ValidationError) as excinfo:
        InvestigationCheckpoint(
            investigation_id="inv-1",
            run_id="run-1",
            problem_hash=input_hash_for(PROBLEM),
            steps=[],
            created_at=datetime(2026, 10, 21, 12, 0, 0),
        )

    assert "created_at must be timezone-aware" in str(excinfo.value)

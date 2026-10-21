from __future__ import annotations

import re
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from product_hunter.research.budget import BudgetDimension, BudgetSpec, BudgetTracker
from product_hunter.research.contract import load_research_contract
from product_hunter.research.evidence import ClaimKind, StageStatus, ToolCallStatus
from product_hunter.research.investigation.areas import (
    AreaFinding,
    ResearchArea,
    WtpTier,
)
from product_hunter.research.investigation.budget import InvestigationBudget
from product_hunter.research.investigation.checkpoint import (
    InvestigationCheckpoint,
    InvestigationStep,
    StepStatus,
    build_checkpoint,
    input_hash_for,
)
from product_hunter.research.investigation.claims import (
    ClaimEvidence,
    ClaimStatus,
    InvestigationClaim,
)
from product_hunter.research.investigation.competitors import (
    CONFIDENCE_VALUES,
    Competitor,
    CompetitorConfidence,
)
from product_hunter.research.investigation.counter_evidence import (
    CounterEvidenceItem,
    CounterEvidenceKind,
    CounterEvidenceStatus,
)
from product_hunter.research.investigation.coverage import CoverageRequirements
from product_hunter.research.investigation.opportunity import (
    InvestigationOutcome,
    OpportunityState,
)
from product_hunter.research.investigation.pipeline import (
    MAX_CHECKPOINT_ITEMS,
    MAX_OUTCOME_ITEMS,
    MAX_SOURCE_URLS,
    OVER_CAP_ERROR,
    InvestigationRequest,
    InvestigationResult,
    SearchOutcome,
    build_plan,
    investigate,
    investigation_stage_execution,
)
from product_hunter.research.investigation.provenance import InvestigationProvenance
from product_hunter.research.investigation.startup_killer import (
    DimensionStatus,
    StartupKillerVerdict,
)
from product_hunter.research.predicates import (
    candidate_urls,
    counter_evidence_items,
    explicit_zero_outcome,
    stage_calls,
    traces_to_successful_call,
)

FROZEN = datetime(2026, 10, 21, 12, 0, 0, tzinfo=timezone.utc)
PROBLEM = "teams hate spreadsheets"
DOCUMENTS = ["https://one.example/a", "https://two.example/b"]
DOMAINS = ["one.example", "two.example"]


def _clock() -> datetime:
    return FROZEN


def _provenance(*refs: str) -> InvestigationProvenance:
    return InvestigationProvenance(
        run_id="run-1",
        stage="investigation",
        agent="researcher",
        tool_call_ids=list(refs),
        recorded_at=FROZEN,
    )


class FakeExecutor:
    """An executor that answers every query the same way."""

    def __init__(
        self,
        *,
        documents: list[str] | None = None,
        competitors=(),
        counter_evidence=(),
        findings=(),
        fail_families: tuple[str, ...] = (),
    ) -> None:
        self.documents = list(DOCUMENTS if documents is None else documents)
        self.competitors = list(competitors)
        self.counter_evidence = list(counter_evidence)
        self.findings = list(findings)
        self.fail_families = set(fail_families)
        self.calls: list[tuple[str, str]] = []
        self.outcomes: list[SearchOutcome] = []

    def search(self, query: str, *, family: str) -> SearchOutcome:
        failed = family in self.fail_families
        outcome = SearchOutcome(
            query=query,
            family=family,
            status=ToolCallStatus.failure if failed else ToolCallStatus.success,
            result_count=0 if failed else len(self.documents),
            documents=[] if failed else list(self.documents),
            competitors=[] if failed else list(self.competitors),
            counter_evidence=[] if failed else list(self.counter_evidence),
            findings=[] if failed else list(self.findings),
            tool_call_id=f"call-{len(self.calls) + 1}",
            error="upstream refused" if failed else None,
        )
        self.calls.append((query, family))
        self.outcomes.append(outcome)
        return outcome


class _ManyUrlExecutor:
    """An executor that hands over a different thousand urls on every call."""

    def __init__(self) -> None:
        self.calls = 0

    def search(self, query: str, *, family: str) -> SearchOutcome:
        self.calls += 1
        base = self.calls * MAX_OUTCOME_ITEMS
        return SearchOutcome(
            query=query,
            family=family,
            status=ToolCallStatus.success,
            result_count=MAX_OUTCOME_ITEMS,
            documents=[
                f"https://one.example/{base + index}"
                for index in range(MAX_OUTCOME_ITEMS)
            ],
            tool_call_id=f"call-{self.calls}",
        )


class _OddIdExecutor:
    """An executor whose call ids carry spaces and non-ASCII characters."""

    def __init__(self) -> None:
        self.calls = 0

    def search(self, query: str, *, family: str) -> SearchOutcome:
        self.calls += 1
        return SearchOutcome(
            query=query,
            family=family,
            status=ToolCallStatus.success,
            result_count=1,
            documents=["https://one.example/a"],
            competitors=[
                Competitor(
                    competitor_id=f"comp-{self.calls}",
                    name=f"Sheetwise {self.calls}",
                    url="https://sheetwise.example/",
                    confidence=CompetitorConfidence.high,
                )
            ],
            tool_call_id=f"call {self.calls} é",
        )


def _request(**overrides) -> InvestigationRequest:
    data = {
        "investigation_id": "inv-1",
        "run_id": "run-1",
        "problem": PROBLEM,
    }
    data.update(overrides)
    return InvestigationRequest(**data)


def _checkpoint_from(
    request: InvestigationRequest, executor: FakeExecutor
) -> InvestigationCheckpoint:
    """Build the checkpoint a caller would have persisted after the first run."""
    plan = build_plan(request)
    steps = [
        InvestigationStep(
            step_id=step.step_id,
            area=step.area,
            family=step.family,
            query=step.query,
            status=StepStatus.completed,
            input_hash=input_hash_for(step.query),
            tool_call_id=outcome.tool_call_id,
            document_count=len(outcome.documents),
            domains=list(DOMAINS),
            source_urls=list(outcome.documents),
            result_count=outcome.result_count,
        )
        for step, outcome in zip(plan.steps, executor.outcomes)
    ]
    return build_checkpoint(
        request.investigation_id,
        request.run_id,
        request.problem,
        steps,
        created_at=FROZEN,
    )


def test_the_plan_is_a_pure_function_of_the_request():
    request = _request()

    first = build_plan(request)
    second = build_plan(request)

    assert first == second
    assert [step.step_id for step in first.steps] == [
        step.step_id for step in second.steps
    ]


def test_the_plan_covers_every_area_and_the_killer_dimensions():
    plan = build_plan(_request())

    areas = [step.area.value for step in plan.steps]
    assert areas[0] == "competitor"
    assert areas[-1] == "startup_killer"
    assert sum(1 for area in areas if area == "startup_killer") == 10


def test_a_request_may_not_remove_a_mandatory_family():
    with pytest.raises(ValidationError) as excinfo:
        _request(competitor_families=["competitor_alternative"])

    assert "mandatory competitor query families" in str(excinfo.value)


def test_the_executor_is_called_once_per_pending_step():
    executor = FakeExecutor()

    result = investigate(_request(), executor, clock=_clock)

    assert len(executor.calls) == len(build_plan(_request()).steps)
    assert result.steps_completed == result.steps_total


def test_a_resumed_investigation_calls_the_executor_zero_times_and_returns_the_same_result():
    request = _request()
    executor = FakeExecutor()

    first = investigate(request, executor, clock=_clock)
    checkpoint = _checkpoint_from(request, executor)
    resumed = FakeExecutor()

    second = investigate(request, resumed, checkpoint=checkpoint, clock=_clock)

    assert resumed.calls == []
    assert second == first


def test_a_partly_finished_checkpoint_runs_only_the_missing_steps():
    request = _request()
    plan = build_plan(request)
    first = plan.steps[0]
    checkpoint = build_checkpoint(
        request.investigation_id,
        request.run_id,
        request.problem,
        [
            InvestigationStep(
                step_id=first.step_id,
                area=first.area,
                family=first.family,
                query=first.query,
                status=StepStatus.completed,
                input_hash=input_hash_for(first.query),
                tool_call_id="call-1",
                document_count=len(DOCUMENTS),
                domains=list(DOMAINS),
                source_urls=list(DOCUMENTS),
                result_count=len(DOCUMENTS),
            )
        ],
        created_at=FROZEN,
    )
    executor = FakeExecutor()

    result = investigate(request, executor, checkpoint=checkpoint, clock=_clock)

    assert len(executor.calls) == len(plan.steps) - 1
    assert result.steps_completed == result.steps_total


def test_an_area_with_findings_and_a_complete_coverage_is_claimed():
    finding = AreaFinding(
        finding_id="finding-1",
        area=ResearchArea.market_validation,
        summary="a vendor already sells this",
        tool_call_id="call-8",
    )
    executor = FakeExecutor(findings=[finding])

    result = investigate(_request(), executor, clock=_clock)

    claim = next(
        claim
        for claim in result.claims
        if claim.kind is ClaimKind.market_validated
    )
    assert claim.supporting_evidence
    assert result.provenance.covers(claim.supporting_evidence[0].ref)


def test_an_area_that_saw_one_source_domain_is_not_complete():
    executor = FakeExecutor(documents=["https://one.example/a"])

    result = investigate(_request(), executor, clock=_clock)

    # The run's distinct-domain floor is two, so one source is not coverage.
    assert result.competitors.coverage.status.value == "incomplete"
    assert result.outcome is InvestigationOutcome.incomplete


def test_a_checkpoint_from_a_different_problem_is_refused():
    request = _request()
    executor = FakeExecutor()
    checkpoint = _checkpoint_from(request, executor)
    other = build_checkpoint(
        "inv-1",
        "run-1",
        "another problem",
        checkpoint.steps,
        created_at=FROZEN,
    )

    with pytest.raises(ValueError) as excinfo:
        investigate(request, FakeExecutor(), checkpoint=other, clock=_clock)

    assert "different problem" in str(excinfo.value)


def test_a_run_that_produced_counter_evidence_ends_contradicted():
    item = CounterEvidenceItem(
        item_id="ce-1",
        kind=CounterEvidenceKind.existing_solution,
        summary="a spreadsheet suite already does this",
        tool_call_id="call-1",
    )
    executor = FakeExecutor(counter_evidence=[item])

    result = investigate(_request(), executor, clock=_clock)

    assert result.counter_evidence.status is CounterEvidenceStatus.found
    assert result.outcome is InvestigationOutcome.contradicted
    assert result.recommended_state is OpportunityState.rejected


def test_a_run_whose_coverage_is_incomplete_ends_incomplete_and_emits_no_absence_claim():
    executor = FakeExecutor(fail_families=("competitor_pricing",))

    result = investigate(_request(), executor, clock=_clock)

    assert result.outcome is InvestigationOutcome.incomplete
    assert result.recommended_state is OpportunityState.insufficient_evidence
    kinds = {claim.kind for claim in result.claims}
    # The competitor search was thin, so the run may not claim that no
    # competitor exists.
    assert ClaimKind.no_strong_competitor not in kinds
    assert result.incomplete_reasons


def test_a_run_that_survived_every_dimension_ends_supported():
    executor = FakeExecutor()

    result = investigate(_request(), executor, clock=_clock)

    assert result.killer.verdict is StartupKillerVerdict.survived
    assert result.outcome is InvestigationOutcome.supported
    assert result.recommended_state is OpportunityState.human_review


def test_the_recommended_state_is_never_validated():
    for state in (
        OpportunityState.candidate,
        OpportunityState.verifying,
        OpportunityState.promising,
        OpportunityState.human_review,
        OpportunityState.stale,
    ):
        result = investigate(_request(current_state=state), FakeExecutor(), clock=_clock)

        assert result.recommended_state is not OpportunityState.validated


def test_a_result_that_recommends_a_validated_opportunity_is_refused():
    result = investigate(_request(), FakeExecutor(), clock=_clock)
    data = result.model_dump()
    data["recommended_state"] = OpportunityState.validated

    with pytest.raises(ValidationError) as excinfo:
        InvestigationResult.model_validate(data)

    assert "never recommends a validated opportunity" in str(excinfo.value)


def test_the_reasons_never_quote_a_query_a_url_or_a_domain():
    executor = FakeExecutor(fail_families=("competitor_pricing",))

    result = investigate(_request(), executor, clock=_clock)

    reasons = [
        *result.incomplete_reasons,
        *result.coverage.reasons,
        *result.counter_evidence.reasons,
        *result.killer.reasons,
        *(reason for research in result.areas.values() for reason in research.reasons),
    ]
    assert reasons
    for reason in reasons:
        assert "spreadsheets" not in reason
        assert "http" not in reason.lower()
        assert "example" not in reason.lower()


def test_the_claims_carry_evidence_and_a_provenance_that_resolves():
    executor = FakeExecutor()

    result = investigate(_request(), executor, clock=_clock)

    assert result.claims
    for claim in result.claims:
        assert claim.supporting_evidence
        assert claim.provenance is not None
        for item in claim.supporting_evidence:
            assert result.provenance.covers(item.ref)


def test_the_absence_claims_are_emitted_only_on_a_complete_coverage():
    executor = FakeExecutor()

    result = investigate(_request(), executor, clock=_clock)

    kinds = {claim.kind for claim in result.claims}
    assert ClaimKind.no_strong_competitor in kinds
    assert ClaimKind.no_counter_evidence_found in kinds
    assert result.competitors.coverage.status.value == "complete"


def test_a_budget_denial_stops_the_calls_and_the_run_ends_incomplete():
    tracker = BudgetTracker(
        BudgetSpec(version="test-v1", per_run={"search_queries": 100.0})
    )
    budget = InvestigationBudget(tracker, {"competitor": {"search_queries": 2.0}})
    executor = FakeExecutor()

    result = investigate(_request(), executor, budget=budget, clock=_clock)

    # The cap allowed two calls in the competitor area, and the third one
    # stopped the run.
    assert len(executor.calls) == 2
    assert result.outcome is InvestigationOutcome.incomplete
    assert result.steps_completed < result.steps_total


def test_the_result_records_the_calls_it_made():
    executor = FakeExecutor()

    result = investigate(_request(), executor, clock=_clock)

    assert len(result.tool_calls) == len(executor.calls)
    for call in result.tool_calls:
        assert call.run_id == "run-1"
        assert call.status is ToolCallStatus.success


def test_a_clean_counter_search_records_a_zero_the_gate_accepts():
    result = investigate(_request(), FakeExecutor(), clock=_clock)

    execution = investigation_stage_execution(result)

    assert execution.zero_result.recorded is True
    assert execution.zero_result.required_query_families == (
        result.counter_evidence.required_families
    )
    assert execution.zero_result.completed_query_families == (
        result.counter_evidence.coverage.completed_families
    )
    ok, detail = explicit_zero_outcome(execution)
    assert ok is True
    assert detail


def test_an_incomplete_counter_search_records_no_zero():
    executor = FakeExecutor(fail_families=("counter_not_worth",))

    result = investigate(_request(), executor, clock=_clock)
    execution = investigation_stage_execution(result)

    assert result.counter_evidence.status is CounterEvidenceStatus.incomplete
    assert execution.zero_result.recorded is False
    assert execution.zero_result.required_query_families == []
    assert execution.zero_result.completed_query_families == []
    ok, _ = explicit_zero_outcome(execution)
    assert ok is False


def test_a_tool_failure_records_no_zero_even_when_the_counter_search_was_clean():
    executor = FakeExecutor(fail_families=("competitor_pricing",))

    result = investigate(_request(), executor, clock=_clock)
    execution = investigation_stage_execution(result)

    # The counter families were all searched, but a search failed, so the
    # investigation never authorised a zero.
    assert result.counter_evidence.status is CounterEvidenceStatus.none_found
    assert result.coverage.tool_failures == 1
    assert execution.zero_result.recorded is False
    ok, _ = explicit_zero_outcome(execution)
    assert ok is False


def test_the_stage_calls_are_bound_to_the_investigation_stage():
    result = investigate(_request(), FakeExecutor(), clock=_clock)

    execution = investigation_stage_execution(result)

    assert execution.stage == "investigation"
    assert execution.status is StageStatus.succeeded
    assert stage_calls(execution) == result.tool_calls


def test_the_artifacts_trace_to_the_calls_that_produced_them():
    competitor = Competitor(
        competitor_id="comp-1",
        name="Sheetwise",
        url="https://sheetwise.example/",
        confidence=CompetitorConfidence.high,
    )
    item = CounterEvidenceItem(
        item_id="ce-1",
        kind=CounterEvidenceKind.existing_solution,
        summary="a spreadsheet suite already does this",
    )
    finding = AreaFinding(
        finding_id="finding-1",
        area=ResearchArea.market_validation,
        summary="a vendor already sells this",
    )
    executor = FakeExecutor(
        competitors=[competitor],
        counter_evidence=[item],
        findings=[finding],
    )

    result = investigate(_request(), executor, clock=_clock)
    execution = investigation_stage_execution(result)

    # The pipeline keeps one competitor, so one call produced it.
    assert candidate_urls(execution) == 1
    # The counter-evidence item came back with every call.
    assert counter_evidence_items(execution) == len(executor.calls)
    for artifact in execution.artifacts:
        assert artifact.stage == execution.stage
        assert traces_to_successful_call(execution, artifact)


def test_a_resumed_investigation_keeps_the_evidence_the_first_run_found():
    competitor = Competitor(
        competitor_id="comp-1",
        name="Sheetwise",
        url="https://sheetwise.example/",
        confidence=CompetitorConfidence.high,
    )
    item = CounterEvidenceItem(
        item_id="ce-1",
        kind=CounterEvidenceKind.existing_solution,
        summary="a spreadsheet suite already does this",
    )
    finding = AreaFinding(
        finding_id="finding-1",
        area=ResearchArea.market_validation,
        summary="a vendor already sells this",
    )
    request = _request()
    executor = FakeExecutor(
        competitors=[competitor],
        counter_evidence=[item],
        findings=[finding],
    )

    first = investigate(request, executor, clock=_clock)
    resumed_executor = FakeExecutor()
    second = investigate(
        request, resumed_executor, checkpoint=first.checkpoint, clock=_clock
    )

    # The searches found things, so the resumed run has to reach the same
    # conclusion and must not publish an absence claim.
    assert first.outcome is InvestigationOutcome.contradicted
    assert resumed_executor.calls == []
    assert second == first
    assert second.recommended_state is first.recommended_state
    kinds = {claim.kind for claim in second.claims}
    assert ClaimKind.no_strong_competitor not in kinds
    assert ClaimKind.no_counter_evidence_found not in kinds


def test_a_request_without_the_counter_evidence_area_is_refused():
    with pytest.raises(ValidationError) as excinfo:
        _request(
            areas=[
                area
                for area in ResearchArea
                if area is not ResearchArea.counter_evidence
            ]
        )

    assert "counter-evidence areas may not be dropped" in str(excinfo.value)


def test_a_request_without_the_competitor_area_is_refused():
    with pytest.raises(ValidationError) as excinfo:
        _request(
            areas=[
                area for area in ResearchArea if area is not ResearchArea.competitor
            ]
        )

    assert "counter-evidence areas may not be dropped" in str(excinfo.value)


def test_a_request_may_narrow_the_other_areas():
    request = _request(
        areas=[
            ResearchArea.competitor,
            ResearchArea.counter_evidence,
            ResearchArea.market_validation,
        ]
    )

    plan = build_plan(request)

    assert request.areas
    assert "competitor" in {step.area.value for step in plan.steps}


def test_a_supported_outcome_on_an_incomplete_coverage_is_refused():
    result = investigate(_request(), FakeExecutor(), clock=_clock)
    data = result.model_dump()
    data["outcome"] = InvestigationOutcome.supported
    data["coverage"]["status"] = "incomplete"

    with pytest.raises(ValidationError) as excinfo:
        InvestigationResult.model_validate(data)

    assert "needs a coverage that is complete" in str(excinfo.value)


def test_a_recommended_state_that_disagrees_with_the_outcome_is_refused():
    result = investigate(_request(), FakeExecutor(), clock=_clock)
    data = result.model_dump()
    data["recommended_state"] = OpportunityState.rejected

    with pytest.raises(ValidationError) as excinfo:
        InvestigationResult.model_validate(data)

    assert "does not follow from the outcome" in str(excinfo.value)


def test_the_wave_1_package_re_exports_the_investigation():
    from product_hunter.research import investigate as re_exported

    assert re_exported is investigate


def test_the_contract_per_stage_limit_binds_the_investigation():
    contract = load_research_contract()
    tracker = BudgetTracker(contract.budgets)
    budget = InvestigationBudget(tracker, {"competitor": {"search_queries": 1000.0}})
    limit = contract.budgets.per_stage["investigation"]["search_queries"]

    for _ in range(int(limit)):
        decision = budget.check("competitor", BudgetDimension.search_queries, 1)
        assert decision.allowed is True
        budget.record("competitor", BudgetDimension.search_queries, 1)

    decision = budget.check("competitor", BudgetDimension.search_queries, 1)

    # The per-area cap is far away, so the contract's per-stage limit is the
    # one that refuses the call.
    assert decision.allowed is False
    assert decision.limit == limit
    # The run total is counted once, not once per scope.
    assert tracker.spent("search_queries") == limit


def test_a_checkpoint_from_another_investigation_is_refused():
    request = _request()
    checkpoint = build_checkpoint(
        "inv-2",
        "run-1",
        request.problem,
        [],
        created_at=FROZEN,
    )

    with pytest.raises(ValueError) as excinfo:
        investigate(request, FakeExecutor(), checkpoint=checkpoint, clock=_clock)

    assert "different investigation" in str(excinfo.value)


def test_a_checkpoint_from_another_run_is_refused():
    request = _request()
    checkpoint = build_checkpoint(
        "inv-1",
        "run-2",
        request.problem,
        [],
        created_at=FROZEN,
    )

    with pytest.raises(ValueError) as excinfo:
        investigate(request, FakeExecutor(), checkpoint=checkpoint, clock=_clock)

    assert "different run" in str(excinfo.value)


def test_the_same_checkpoint_is_accepted():
    request = _request()
    checkpoint = build_checkpoint(
        "inv-1",
        "run-1",
        request.problem,
        [],
        created_at=FROZEN,
    )

    result = investigate(request, FakeExecutor(), checkpoint=checkpoint, clock=_clock)

    assert result.steps_total == len(build_plan(request).steps)


def test_an_item_url_with_credentials_is_stored_without_them():
    competitor = Competitor(
        competitor_id="comp-1",
        name="Sheetwise",
        url="https://user:secret@sheetwise.example/",
        confidence=CompetitorConfidence.high,
    )
    item = CounterEvidenceItem(
        item_id="ce-1",
        kind=CounterEvidenceKind.existing_solution,
        summary="a suite already does this",
        source_url="https://user:secret@one.example/a",
    )
    finding = AreaFinding(
        finding_id="finding-1",
        area=ResearchArea.market_validation,
        summary="a vendor already sells this",
        source_url="https://user:secret@two.example/b",
    )
    executor = FakeExecutor(
        competitors=[competitor],
        counter_evidence=[item],
        findings=[finding],
    )

    result = investigate(_request(), executor, clock=_clock)

    assert result.competitors.competitors[0].url == "https://sheetwise.example/"
    assert result.counter_evidence.items[0].source_url == "https://one.example/a"
    finding_read = result.areas["market_validation"].findings[0]
    assert finding_read.source_url == "https://two.example/b"
    assert "secret" not in str(result.model_dump())


def test_a_finding_whose_url_cannot_be_read_is_stored_with_no_url():
    finding = AreaFinding(
        finding_id="finding-1",
        area=ResearchArea.market_validation,
        summary="a vendor already sells this",
        source_url="http://[::1/a",
    )
    executor = FakeExecutor(findings=[finding])

    result = investigate(_request(), executor, clock=_clock)

    assert result.areas["market_validation"].findings[0].source_url is None


def test_a_dimension_on_one_source_is_not_survived():
    executor = FakeExecutor(documents=["https://one.example/a"])

    result = investigate(_request(), executor, clock=_clock)

    # The run's distinct-domain floor is two, so one source is not an attempt.
    assert result.killer.verdict is StartupKillerVerdict.inconclusive
    assert all(
        assessment.status is not DimensionStatus.survived
        for assessment in result.killer.assessments
    )
    assert result.outcome is InvestigationOutcome.incomplete


def test_the_wtp_area_carries_the_tier_the_classifier_read():
    finding = AreaFinding(
        finding_id="finding-1",
        area=ResearchArea.willingness_to_pay,
        summary="a buyer named a budget",
        wtp_tier=WtpTier.w3,
    )
    executor = FakeExecutor(findings=[finding])

    result = investigate(_request(), executor, clock=_clock)

    assert result.areas["willingness_to_pay"].wtp_tier is WtpTier.w3


def test_the_strongest_tier_wins():
    findings = [
        AreaFinding(
            finding_id="finding-1",
            area=ResearchArea.willingness_to_pay,
            summary="a buyer complained",
            wtp_tier=WtpTier.w1,
        ),
        AreaFinding(
            finding_id="finding-2",
            area=ResearchArea.willingness_to_pay,
            summary="a buyer named a budget",
            wtp_tier=WtpTier.w4,
        ),
    ]
    executor = FakeExecutor(findings=findings)

    result = investigate(_request(), executor, clock=_clock)

    assert result.areas["willingness_to_pay"].wtp_tier is WtpTier.w4


def test_a_wtp_area_without_a_tier_has_none():
    result = investigate(_request(), FakeExecutor(), clock=_clock)

    assert result.areas["willingness_to_pay"].wtp_tier is None


def test_evidence_that_counts_for_nothing_is_not_evidence():
    claim = InvestigationClaim(
        claim_id="claim-1",
        kind=ClaimKind.competitors_exist,
        statement="a competitor ships this",
        stage="investigation",
        supporting_evidence=[
            ClaimEvidence(ref="call-1", summary="a competitor", weight=0.0)
        ],
    )

    assert claim.status is ClaimStatus.unknown


def test_a_placeholder_reconciliation_does_not_reconcile():
    claim = InvestigationClaim(
        claim_id="claim-1",
        kind=ClaimKind.competitors_exist,
        statement="a competitor ships this",
        stage="investigation",
        status=ClaimStatus.inconclusive,
        supporting_evidence=[ClaimEvidence(ref="call-1", summary="a competitor")],
        contradicting_evidence=[ClaimEvidence(ref="call-2", summary="a counter example")],
        provenance=_provenance("call-1", "call-2"),
        reconciliation="n/a",
    )

    assert claim.status is ClaimStatus.inconclusive


def test_a_reconciliation_that_names_the_ref_reconciles():
    claim = InvestigationClaim(
        claim_id="claim-1",
        kind=ClaimKind.competitors_exist,
        statement="a competitor ships this",
        stage="investigation",
        status=ClaimStatus.supported,
        supporting_evidence=[ClaimEvidence(ref="call-1", summary="a competitor")],
        contradicting_evidence=[ClaimEvidence(ref="call-2", summary="a counter example")],
        provenance=_provenance("call-1", "call-2"),
        reconciliation="the counter example call-2 was out of date",
    )

    assert claim.status is ClaimStatus.supported


def test_the_plan_holds_no_duplicate_search():
    plan = build_plan(_request())

    queries = [step.query for step in plan.steps]
    assert len(set(queries)) == len(queries)
    assert len(queries) == 23


def test_the_mission_families_are_still_in_the_plan():
    plan = build_plan(_request())
    families = {step.family for step in plan.steps}

    for family in (
        "competitor_alternative",
        "competitor_existing_solution",
        "competitor_software",
        "competitor_pricing",
        "counter_not_worth",
        "counter_complaints",
        "counter_workaround",
    ):
        assert family in families


def test_the_absence_claim_carries_the_confidence():
    result = investigate(_request(), FakeExecutor(), clock=_clock)

    claim = next(
        claim
        for claim in result.claims
        if claim.kind is ClaimKind.no_strong_competitor
    )

    assert claim.confidence is not None
    assert claim.confidence == CONFIDENCE_VALUES[result.competitors.confidence]


def test_a_run_whose_query_floor_is_above_the_plan_size_is_incomplete():
    request = _request(
        requirements=CoverageRequirements(
            min_queries=100, min_documents=1, min_domains=2
        )
    )

    result = investigate(request, FakeExecutor(), clock=_clock)

    # The areas are complete, but the run's own floor is not cleared, so the
    # run is incomplete rather than a conclusion the research cannot support.
    assert result.outcome is InvestigationOutcome.incomplete
    assert result.recommended_state is OpportunityState.insufficient_evidence


def test_a_recommended_state_the_ladder_does_not_choose_is_refused():
    result = investigate(_request(), FakeExecutor(), clock=_clock)
    data = result.model_dump()
    # From CANDIDATE the ladder picks VERIFYING, not HUMAN_REVIEW.
    data["current_state"] = OpportunityState.candidate
    data["recommended_state"] = OpportunityState.human_review

    with pytest.raises(ValidationError) as excinfo:
        InvestigationResult.model_validate(data)

    assert "does not follow from the outcome" in str(excinfo.value)


def test_a_run_that_would_overfill_the_checkpoint_refuses_the_step():
    competitors = [
        Competitor(
            competitor_id=f"comp-{index}",
            name=f"Sheetwise {index}",
            confidence=CompetitorConfidence.high,
        )
        for index in range(100)
    ]
    executor = FakeExecutor(competitors=competitors)

    result = investigate(_request(), executor, clock=_clock)

    items = sum(
        len(step.competitors) + len(step.counter_evidence) + len(step.findings)
        for step in result.checkpoint.steps
    )
    assert items <= MAX_CHECKPOINT_ITEMS
    assert result.outcome is InvestigationOutcome.incomplete
    assert OVER_CAP_ERROR in result.incomplete_reasons
    # The refused run publishes no zero.
    assert investigation_stage_execution(result).zero_result.recorded is False


def test_a_resumed_run_does_not_double_the_checkpoint_cap():
    # The items the checkpoint already holds count against the run's cap, so
    # a resume may not end up holding twice the bound it resumed under.
    competitors = [
        Competitor(
            competitor_id=f"comp-{index}",
            name=f"Sheetwise {index}",
            confidence=CompetitorConfidence.high,
        )
        for index in range(100)
    ]
    request = _request()
    first = investigate(request, FakeExecutor(competitors=competitors), clock=_clock)
    held = sum(
        len(step.competitors) + len(step.counter_evidence) + len(step.findings)
        for step in first.checkpoint.steps
    )
    assert held == MAX_CHECKPOINT_ITEMS

    second = investigate(
        request,
        FakeExecutor(competitors=competitors),
        checkpoint=first.checkpoint,
        clock=_clock,
    )

    after = sum(
        len(step.competitors) + len(step.counter_evidence) + len(step.findings)
        for step in second.checkpoint.steps
    )
    assert after <= MAX_CHECKPOINT_ITEMS
    assert second.outcome is InvestigationOutcome.incomplete


def test_the_evidence_refs_are_stored_without_their_credentials():
    competitor = Competitor(
        competitor_id="comp-1",
        name="Sheetwise",
        url="https://sheetwise.example/",
        evidence_refs=["https://user:secret@one.example/a", "call-1"],
        confidence=CompetitorConfidence.high,
    )
    finding = AreaFinding(
        finding_id="finding-1",
        area=ResearchArea.market_validation,
        summary="a vendor already sells this",
        evidence_refs=["https://user:secret@two.example/b"],
    )
    executor = FakeExecutor(competitors=[competitor], findings=[finding])

    result = investigate(_request(), executor, clock=_clock)

    assert result.competitors.competitors[0].evidence_refs == [
        "https://one.example/a",
        "call-1",
    ]
    assert result.areas["market_validation"].findings[0].evidence_refs == [
        "https://two.example/b"
    ]
    assert "secret" not in str(result.model_dump())


def test_the_pipeline_assessments_carry_the_runs_requirements():
    result = investigate(_request(), FakeExecutor(), clock=_clock)

    for assessment in result.killer.assessments:
        assert assessment.requirements.min_domains == 2
        assert assessment.requirements.min_documents == 1


def test_a_call_that_produced_nothing_gets_no_artifact():
    result = investigate(_request(), FakeExecutor(), clock=_clock)

    execution = investigation_stage_execution(result)

    # The fake executor classified nothing, so the stage holds no artifact.
    assert execution.artifacts == []


def test_a_result_over_the_cap_is_refused_and_the_run_ends_incomplete():
    executor = FakeExecutor(
        documents=[f"https://one.example/{index}" for index in range(MAX_OUTCOME_ITEMS + 1)]
    )

    result = investigate(_request(), executor, clock=_clock)

    # The run keeps going: the other steps are still searched.
    assert len(executor.calls) == len(build_plan(_request()).steps)
    assert result.steps_completed == 0
    # The refused data is not held, so the attempt failed with the fixed
    # phrase and the coverage carries the failure.
    assert result.coverage.tool_failures == len(executor.calls)
    assert OVER_CAP_ERROR in result.incomplete_reasons
    assert result.outcome is InvestigationOutcome.incomplete


def test_a_refused_result_makes_the_gate_refuse_the_zero():
    executor = FakeExecutor(
        documents=[f"https://one.example/{index}" for index in range(MAX_OUTCOME_ITEMS + 1)]
    )

    result = investigate(_request(), executor, clock=_clock)
    execution = investigation_stage_execution(result)

    assert execution.zero_result.recorded is False
    ok, _ = explicit_zero_outcome(execution)
    assert ok is False


def test_the_provenance_keeps_at_most_the_capped_number_of_urls():
    executor = _ManyUrlExecutor()

    result = investigate(_request(), executor, clock=_clock)

    assert len(result.provenance.source_urls) == MAX_SOURCE_URLS
    assert len(set(result.provenance.source_urls)) == MAX_SOURCE_URLS


def test_a_url_with_credentials_is_stored_without_them():
    executor = FakeExecutor(
        documents=[
            "https://user:secret@one.example/a",
            "https://two.example/b",
        ]
    )

    result = investigate(_request(), executor, clock=_clock)

    assert result.provenance.source_urls == [
        "https://one.example/a",
        "https://two.example/b",
    ]
    assert "secret" not in " ".join(result.provenance.source_urls)


def test_a_url_with_a_malformed_port_is_stored_without_its_credentials():
    # The port cannot be read, but the userinfo can, so the credential goes
    # and the malformed port is dropped rather than kept.
    executor = FakeExecutor(
        documents=[
            "https://user:secret@host:notaport/a",
            "https://two.example/b",
        ]
    )

    result = investigate(_request(), executor, clock=_clock)

    assert result.provenance.source_urls == [
        "https://host/a",
        "https://two.example/b",
    ]
    assert "secret" not in " ".join(result.provenance.source_urls)


def test_a_url_the_pipeline_cannot_parse_is_not_stored():
    # An unclosed IPv6 bracket is not a url the parser can read at all, so
    # there is no way to be sure it carries no secret.
    executor = FakeExecutor(
        documents=[
            "https://[::1/a",
            "https://two.example/b",
        ]
    )

    result = investigate(_request(), executor, clock=_clock)

    assert result.provenance.source_urls == ["https://two.example/b"]
    assert "user" not in " ".join(result.provenance.source_urls)


def test_the_crawl_spend_is_the_items_the_call_consumed():
    tracker = BudgetTracker(
        BudgetSpec(
            version="test-v1",
            per_run={"search_queries": 1000.0, "crawl_items": 100000.0},
        )
    )
    budget = InvestigationBudget(tracker)
    executor = FakeExecutor()

    investigate(_request(), executor, budget=budget, clock=_clock)

    # The fake executor hands over two documents and nothing else per call.
    assert tracker.spent("crawl_items") == 2 * len(executor.calls)
    assert budget.spent("competitor", "crawl_items") == 8


def test_a_crawl_spend_over_the_cap_stops_the_run_and_stays_recorded():
    tracker = BudgetTracker(
        BudgetSpec(
            version="test-v1",
            per_run={"search_queries": 1000.0, "crawl_items": 1.0},
        )
    )
    budget = InvestigationBudget(tracker)
    executor = FakeExecutor()

    result = investigate(_request(), executor, budget=budget, clock=_clock)

    # The fake executor hands over two documents, so the first call is over
    # the cap: the spend stays recorded and the run stops there.
    assert len(executor.calls) == 1
    assert tracker.spent("crawl_items") == 2
    assert result.steps_completed < result.steps_total


def test_an_odd_tool_call_id_still_produces_a_safe_artifact_id():
    first = _OddIdExecutor()
    second = _OddIdExecutor()

    one = investigation_stage_execution(investigate(_request(), first, clock=_clock))
    two = investigation_stage_execution(investigate(_request(), second, clock=_clock))

    ids = [artifact.artifact_id for artifact in one.artifacts]
    assert ids
    for artifact_id in ids:
        assert re.fullmatch(r"[A-Za-z0-9._:/-]+", artifact_id)
    # The id is deterministic and unique per artifact.
    assert ids == [artifact.artifact_id for artifact in two.artifacts]
    assert len(set(ids)) == len(ids)

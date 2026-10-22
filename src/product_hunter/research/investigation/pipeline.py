"""The investigation pipeline: the plan, the executor seam, and the result.

The pipeline never searches. It plans the steps, calls the injected executor,
counts what came back, applies the coverage rule, and decides what the
investigation may conclude. It never fabricates a competitor, a finding or a
number, and it never validates an opportunity.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Protocol
from urllib.parse import urlparse, urlunparse

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from product_hunter.research.budget import BudgetDimension, BudgetExceeded
from product_hunter.research.evidence import (
    ArtifactRecord,
    ClaimKind,
    StageExecution,
    StageStatus,
    ToolCallProvenance,
    ToolCallStatus,
    ZeroResultOutcome,
)
from product_hunter.research.investigation.areas import (
    AreaFinding,
    AreaResearch,
    ResearchArea,
    strongest_wtp_tier,
)
from product_hunter.research.investigation.budget import InvestigationBudget
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
from product_hunter.research.investigation.claims import (
    ClaimEvidence,
    ClaimStatus,
    InvestigationClaim,
)
from product_hunter.research.investigation.competitors import (
    CONFIDENCE_VALUES,
    Competitor,
    CompetitorAnalysis,
    CompetitorAnalysisStatus,
    analyse_competitors,
)
from product_hunter.research.investigation.counter_evidence import (
    CounterEvidenceItem,
    CounterEvidenceResult,
    CounterEvidenceStatus,
    assess_counter_evidence,
)
from product_hunter.research.investigation.coverage import (
    CoverageRequirements,
    CoverageStatus,
    ResearchCoverage,
    SearchAttempt,
    assess_coverage,
)
from product_hunter.research.investigation.opportunity import (
    InvalidTransition,
    InvestigationOutcome,
    OpportunityState,
    allowed_transitions,
    next_state_for_outcome,
)
from product_hunter.research.investigation.provenance import InvestigationProvenance
from product_hunter.research.investigation.queries import (
    QueryFamily,
    QueryTemplate,
    area_templates,
    competitor_templates,
    counter_evidence_templates,
    mandatory_templates,
    render,
    startup_killer_templates,
)
from product_hunter.research.investigation.startup_killer import (
    DIMENSION_CLAIM_KINDS,
    DIMENSION_FAMILIES,
    DimensionAssessment,
    DimensionStatus,
    FalsificationDimension,
    StartupKillerReport,
    StartupKillerVerdict,
    derive_killer_verdict,
)
from product_hunter.research.predicates import (
    KIND_CANDIDATE_URL,
    KIND_COUNTER_EVIDENCE,
)

# The artifact kinds the bridge records. The first two are the wave 1 kinds;
# the last two are the contract's output names for the investigation stage.
KIND_COMPETITORS = "competitors"
KIND_ALTERNATIVES = "alternatives"

# The cap on what one call may hand over. The cap exists because a result the
# pipeline cannot hold is a result it must not rest a conclusion on.
MAX_OUTCOME_ITEMS = 1000

# The cap on the urls the provenance keeps, for the same reason.
MAX_SOURCE_URLS = 5000

# The cap on the items the checkpoint carries across the run. The per-call cap
# bounds one call; this bounds the run.
MAX_CHECKPOINT_ITEMS = 1000

# The phrase a refused result carries. It is fixed, because an error ends up
# in a persisted record.
OVER_CAP_ERROR = "the search returned more items than the pipeline holds"

# The stage and the agent the investigation's evidence is filed under.
STAGE = "investigation"
AGENT = "researcher"
TOOL = "search"

# The order the areas are planned in, so the same request always produces the
# same plan and the same step ids.
_AREA_ORDER: tuple[ResearchArea, ...] = (
    ResearchArea.competitor,
    ResearchArea.existing_solution,
    ResearchArea.alternative,
    ResearchArea.workaround,
    ResearchArea.market_validation,
    ResearchArea.willingness_to_pay,
    ResearchArea.distribution,
    ResearchArea.counter_evidence,
    ResearchArea.startup_killer,
)

# The areas an investigation researches when the caller names none. The
# startup killer is not one of them: it is planned on top of them.
_DEFAULT_AREAS: tuple[ResearchArea, ...] = tuple(
    area for area in _AREA_ORDER if area is not ResearchArea.startup_killer
)

# The families a caller may not remove. The source of that list is the
# package's own templates: the competitor families come first, then the
# counter-evidence ones, so there is one place that says what is mandatory.
_MANDATORY_TEMPLATES = mandatory_templates()
_COMPETITOR_FAMILY_COUNT = len(competitor_templates())
_MANDATORY_COMPETITOR_FAMILIES: tuple[QueryFamily, ...] = tuple(
    template.family
    for template in _MANDATORY_TEMPLATES[:_COMPETITOR_FAMILY_COUNT]
)
_MANDATORY_COUNTER_FAMILIES: tuple[QueryFamily, ...] = tuple(
    template.family for template in _MANDATORY_TEMPLATES[_COMPETITOR_FAMILY_COUNT:]
)

# The areas that carry the mandatory families, and may not be dropped.
_MANDATORY_AREAS: tuple[ResearchArea, ...] = (
    ResearchArea.competitor,
    ResearchArea.counter_evidence,
)


def _template_registry() -> dict[QueryFamily, QueryTemplate]:
    """Return the template of every family the package knows."""
    registry: dict[QueryFamily, QueryTemplate] = {}
    for template in (
        *competitor_templates(),
        *counter_evidence_templates(),
        *startup_killer_templates(),
        *(template for area in _AREA_ORDER for template in area_templates(area)),
    ):
        registry.setdefault(template.family, template)
    return registry


_TEMPLATES: dict[QueryFamily, QueryTemplate] = _template_registry()


class SearchOutcome(BaseModel):
    """What one search returned: the documents and the executor's reading of them."""

    model_config = ConfigDict(extra="forbid")

    query: str
    family: str
    status: ToolCallStatus
    result_count: int = Field(default=0, ge=0)
    documents: list[str] = Field(default_factory=list)
    competitors: list[Competitor] = Field(default_factory=list)
    counter_evidence: list[CounterEvidenceItem] = Field(default_factory=list)
    findings: list[AreaFinding] = Field(default_factory=list)
    tool_call_id: str = ""
    error: str | None = None


class ResearchExecutor(Protocol):
    """The seam the investigation searches through."""

    def search(self, query: str, *, family: str) -> SearchOutcome: ...


class PlanStep(BaseModel):
    """One search the plan calls for."""

    model_config = ConfigDict(extra="forbid")

    step_id: str = Field(min_length=1)
    area: ResearchArea
    family: str = Field(min_length=1)
    query: str = Field(min_length=1)
    purpose: str = Field(min_length=1)


class InvestigationPlan(BaseModel):
    """The searches an investigation runs."""

    model_config = ConfigDict(extra="forbid")

    request_id: str = Field(min_length=1)
    problem: str = Field(min_length=1)
    steps: list[PlanStep] = Field(default_factory=list)


class InvestigationRequest(BaseModel):
    """What the caller asks the investigation to look at."""

    model_config = ConfigDict(extra="forbid")

    investigation_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    problem: str = Field(min_length=1)
    current_state: OpportunityState = OpportunityState.promising
    areas: list[ResearchArea] = Field(default_factory=lambda: list(_DEFAULT_AREAS))
    competitor_families: list[QueryFamily] = Field(
        default_factory=lambda: list(_MANDATORY_COMPETITOR_FAMILIES)
    )
    counter_families: list[QueryFamily] = Field(
        default_factory=lambda: list(_MANDATORY_COUNTER_FAMILIES)
    )
    requirements: CoverageRequirements = Field(default_factory=CoverageRequirements)

    @field_validator("problem")
    @classmethod
    def _problem_is_not_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("the problem statement must not be empty")
        return value

    @model_validator(mode="after")
    def _check_invariants(self) -> InvestigationRequest:
        missing = [
            family
            for family in _MANDATORY_COMPETITOR_FAMILIES
            if family not in self.competitor_families
        ]
        if missing:
            # A caller may add a family; it may not drop a mandatory one.
            raise ValueError("the mandatory competitor query families may not be removed")
        missing = [
            family
            for family in _MANDATORY_COUNTER_FAMILIES
            if family not in self.counter_families
        ]
        if missing:
            raise ValueError(
                "the mandatory counter-evidence query families may not be removed"
            )
        missing_areas = [
            area.value
            for area in _MANDATORY_AREAS
            if area not in self.areas
        ]
        if missing_areas:
            # The areas carry the mandatory families, so dropping one of them
            # would drop the families with it.
            raise ValueError(
                "the competitor and counter-evidence areas may not be dropped"
            )
        return self


class InvestigationResult(BaseModel):
    """What the investigation concluded, and the evidence behind it."""

    model_config = ConfigDict(extra="forbid")

    investigation_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    problem: str = Field(min_length=1)
    started_at: datetime
    finished_at: datetime
    outcome: InvestigationOutcome
    current_state: OpportunityState
    recommended_state: OpportunityState
    coverage: ResearchCoverage
    competitors: CompetitorAnalysis
    counter_evidence: CounterEvidenceResult
    areas: dict[str, AreaResearch]
    killer: StartupKillerReport
    claims: list[InvestigationClaim] = Field(default_factory=list)
    provenance: InvestigationProvenance
    # The wave 1 audit trail of the calls the investigation made.
    tool_calls: list[ToolCallProvenance] = Field(default_factory=list)
    # The checkpoint the run left behind, so the caller can resume it.
    checkpoint: InvestigationCheckpoint
    steps_completed: int = Field(default=0, ge=0)
    steps_total: int = Field(default=0, ge=0)
    incomplete_reasons: list[str] = Field(default_factory=list)

    @field_validator("started_at", "finished_at")
    @classmethod
    def _timestamps_are_aware(cls, value: datetime) -> datetime:
        # A naive timestamp cannot be compared with an aware one, so it
        # silently switches off the gate's duration check.
        if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
            raise ValueError("the run timestamps must be timezone-aware")
        return value

    @model_validator(mode="after")
    def _check_invariants(self) -> InvestigationResult:
        if self.outcome is InvestigationOutcome.supported and (
            self.coverage.status is not CoverageStatus.complete
        ):
            # A conclusion the research could not support is the failure this
            # wave exists to prevent, so the model refuses it.
            raise ValueError(
                "a supported outcome needs a coverage that is complete"
            )
        if self.recommended_state is OpportunityState.validated:
            # The machine never validates an opportunity, so a result that
            # recommends it is refused.
            raise ValueError("the machine never recommends a validated opportunity")
        # The pipeline's own function decides the state, so the check holds in
        # the states where the transition table has no move.
        expected = _recommended_state(self.current_state, self.outcome)
        if self.recommended_state is not expected:
            raise ValueError(
                "the recommended state does not follow from the outcome"
            )
        return self


def _utc_now() -> datetime:
    """Return the current time, aware."""
    return datetime.now(timezone.utc)


def _host_of(url: str) -> str:
    """Return the host of a URL, or an empty string when it has none."""
    try:
        host = urlparse(url).hostname
    except ValueError:
        return ""
    return (host or "").strip().lower()


def _without_credentials(url: str) -> str | None:
    """Return the url without its userinfo, so a password never persists.

    A url the parser cannot read is not stored at all: a reference the
    pipeline cannot parse is not worth keeping, and a url it cannot parse
    is a url it cannot be sure carries no secret.
    """
    try:
        parts = urlparse(url)
        username, password = parts.username, parts.password
    except ValueError:
        return None
    if not username and not password:
        return url
    try:
        host = parts.hostname or ""
    except ValueError:
        return None
    if ":" in host:
        # An IPv6 literal needs its brackets back.
        host = f"[{host}]"
    try:
        port = parts.port
    except ValueError:
        # A malformed port is dropped rather than kept: the host and the
        # path still identify the source, and the port is not worth a secret.
        port = None
    if port is not None:
        host = f"{host}:{port}"
    try:
        return urlunparse(parts._replace(netloc=host))
    except ValueError:
        return None


def _stripped[ItemT: (Competitor, CounterEvidenceItem, AreaFinding)](
    item: ItemT, field: str
) -> ItemT:
    """Return the item with the credentials removed from its url.

    A url the pipeline cannot read is dropped rather than stored: the item
    keeps its finding, and the provenance keeps no secret.
    """
    url = getattr(item, field, None)
    if url is None:
        return item
    return item.model_copy(update={field: _without_credentials(url)})


def _clean_refs(refs: Sequence[str]) -> list[str]:
    """Return the refs the pipeline stores, without their credentials."""
    cleaned: list[str] = []
    for ref in refs:
        clean = _without_credentials(ref)
        if clean is not None:
            cleaned.append(clean)
    return cleaned


def _domains(urls: Sequence[str]) -> list[str]:
    """Return the distinct hosts of the documents, in the order they appear."""
    domains: list[str] = []
    for url in urls:
        host = _host_of(url)
        if host and host not in domains:
            domains.append(host)
    return domains


def _over_cap(outcome: SearchOutcome) -> bool:
    """Report whether the outcome holds more items than the pipeline keeps."""
    return (
        len(outcome.documents) > MAX_OUTCOME_ITEMS
        or len(outcome.competitors) > MAX_OUTCOME_ITEMS
        or len(outcome.counter_evidence) > MAX_OUTCOME_ITEMS
        or len(outcome.findings) > MAX_OUTCOME_ITEMS
    )


def _area_templates(
    area: ResearchArea, request: InvestigationRequest
) -> tuple[QueryTemplate, ...]:
    """Return the templates one area is searched with."""
    if area is ResearchArea.competitor:
        wanted = request.competitor_families
    elif area is ResearchArea.counter_evidence:
        wanted = request.counter_families
    else:
        return area_templates(area)
    templates: list[QueryTemplate] = []
    for family in wanted:
        template = _TEMPLATES.get(family)
        if template is None:
            raise ValueError("a query family has no template to render")
        templates.append(template)
    return tuple(templates)


def build_plan(request: InvestigationRequest) -> InvestigationPlan:
    """Plan the searches of an investigation. The plan is a pure function of the request."""
    areas = [area for area in _AREA_ORDER if area in request.areas]
    if ResearchArea.startup_killer not in areas:
        # The startup killer is the investigation, so its dimensions are
        # planned on top of the areas the caller asked for.
        areas.append(ResearchArea.startup_killer)

    steps: list[PlanStep] = []
    for area in areas:
        for template in _area_templates(area, request):
            query = render(template, request.problem)
            steps.append(
                PlanStep(
                    step_id=step_id_for(area, template.family.value, query.text),
                    area=area,
                    family=template.family.value,
                    query=query.text,
                    purpose=template.purpose,
                )
            )
    return InvestigationPlan(
        request_id=request.investigation_id,
        problem=request.problem,
        steps=steps,
    )


@dataclass
class _Run:
    """What the calls of a run produced."""

    checkpoint: InvestigationCheckpoint
    attempts: list[SearchAttempt] = field(default_factory=list)
    tool_calls: list[ToolCallProvenance] = field(default_factory=list)
    competitors: list[Competitor] = field(default_factory=list)
    competitor_refs: dict[str, str] = field(default_factory=dict)
    counter_items: list[CounterEvidenceItem] = field(default_factory=list)
    counter_by_family: dict[str, list[CounterEvidenceItem]] = field(default_factory=dict)
    findings: list[AreaFinding] = field(default_factory=list)
    source_urls: list[str] = field(default_factory=list)
    area_attempts: dict[str, list[SearchAttempt]] = field(default_factory=dict)
    checkpoint_items: int = 0
    stopped: bool = False


def _tool_call(
    request: InvestigationRequest,
    step: PlanStep,
    tool_call_id: str,
    result_count: int,
    clock: Callable[[], datetime],
) -> ToolCallProvenance:
    """Record the call a step made, in the wave 1 shape."""
    return ToolCallProvenance(
        run_id=request.run_id,
        stage=STAGE,
        agent=AGENT,
        tool=TOOL,
        purpose=step.purpose,
        timestamp=clock(),
        query_or_operation=step.query,
        result_ref=f"investigation/{step.step_id}",
        result_count=result_count,
        status=ToolCallStatus.success,
        tool_call_id=tool_call_id,
        query_family=step.family,
    )


def _step(
    step: PlanStep,
    status: StepStatus,
    *,
    tool_call_id: str = "",
    attempt: SearchAttempt | None = None,
    source_urls: Sequence[str] = (),
    result_count: int = 0,
    competitors: Sequence[Competitor] = (),
    counter_evidence: Sequence[CounterEvidenceItem] = (),
    findings: Sequence[AreaFinding] = (),
    error: str | None = None,
) -> InvestigationStep:
    """Build the checkpoint's record of a step."""
    return InvestigationStep(
        step_id=step.step_id,
        area=step.area,
        family=step.family,
        query=step.query,
        status=status,
        input_hash=input_hash_for(step.query),
        tool_call_id=tool_call_id,
        document_count=attempt.document_count if attempt else 0,
        domains=list(attempt.domains) if attempt else [],
        source_urls=list(source_urls),
        result_count=result_count,
        competitors=list(competitors),
        counter_evidence=list(counter_evidence),
        findings=list(findings),
        error=error,
    )


def _stamped[ProducedT: (Competitor, CounterEvidenceItem, AreaFinding)](
    item: ProducedT, tool_call_id: str
) -> ProducedT:
    """Return the item with the call that produced it, when it names none.

    The wave 1 gate traces an artifact back to the call that produced it, so
    an item that does not name its call is stamped with the one the pipeline
    made.
    """
    if item.tool_call_id:
        return item
    return item.model_copy(update={"tool_call_id": tool_call_id})


def _run_plan(
    request: InvestigationRequest,
    plan: InvestigationPlan,
    executor: ResearchExecutor,
    budget: InvestigationBudget | None,
    checkpoint: InvestigationCheckpoint,
    clock: Callable[[], datetime],
) -> _Run:
    """Run the plan, reusing the steps the checkpoint already holds."""
    run = _Run(checkpoint=checkpoint)
    done = completed_steps(checkpoint)
    # The steps the checkpoint does not hold are the ones to search.
    missing = {step.step_id for step in pending_steps(checkpoint, plan)}
    # The checkpoint already holds items, and they count against the run's
    # cap: a resume may not double the bound it is resuming under.
    run.checkpoint_items = sum(
        len(step.competitors) + len(step.counter_evidence) + len(step.findings)
        for step in checkpoint.steps
    )

    for step in plan.steps:
        if run.stopped:
            # The budget stopped the calls, so the rest of the plan waits for
            # a run with more budget.
            break
        if step.step_id not in missing:
            # The checkpoint holds the step: the executor is not called, the
            # attempt is rebuilt from the checkpoint, and the results the call
            # classified come back with it, so the resumed run reaches the same
            # conclusion the first run reached.
            recorded = done[step.step_id]
            attempt = SearchAttempt(
                family=recorded.family,
                query=recorded.query,
                status=ToolCallStatus.success,
                document_count=recorded.document_count,
                domains=list(recorded.domains),
                tool_call_id=recorded.tool_call_id,
            )
            run.attempts.append(attempt)
            run.area_attempts.setdefault(step.area.value, []).append(attempt)
            run.source_urls.extend(recorded.source_urls)
            run.tool_calls.append(
                _tool_call(request, step, recorded.tool_call_id, recorded.result_count, clock)
            )
            # The classified results come back with the step, so the resumed
            # run reaches the same conclusion the first run reached.
            for competitor in recorded.competitors:
                key = competitor.name.strip().lower()
                if key in run.competitor_refs:
                    continue
                run.competitor_refs[key] = recorded.tool_call_id
                run.competitors.append(_stamped(competitor, recorded.tool_call_id))
            run.counter_items.extend(
                _stamped(item, recorded.tool_call_id) for item in recorded.counter_evidence
            )
            run.counter_by_family.setdefault(step.family, []).extend(
                _stamped(item, recorded.tool_call_id)
                for item in recorded.counter_evidence
            )
            run.findings.extend(
                _stamped(finding, recorded.tool_call_id) for finding in recorded.findings
            )
            continue

        if budget is not None:
            decision = budget.check(step.area, BudgetDimension.search_queries, 1)
            if not decision.allowed:
                # The budget stops the calls; the run aggregates what it has.
                run.checkpoint = apply_step(
                    run.checkpoint,
                    _step(step, StepStatus.failed, error="the budget refused the call"),
                )
                run.stopped = True
                break

        outcome = executor.search(step.query, family=step.family)
        if budget is not None:
            # The call happened, so the call is spent whether or not the
            # result is used.
            budget.record(step.area, BudgetDimension.search_queries, 1)
        tool_call_id = outcome.tool_call_id or step.step_id
        if _over_cap(outcome):
            # The call handed over more than the pipeline holds, so none of it
            # is kept or used: the attempt fails, the step is marked failed,
            # and the run carries on with the areas that are still worth
            # researching.
            attempt = SearchAttempt(
                family=step.family,
                query=step.query,
                status=ToolCallStatus.failure,
                document_count=0,
                domains=[],
                tool_call_id=tool_call_id,
                error=OVER_CAP_ERROR,
            )
            run.attempts.append(attempt)
            run.area_attempts.setdefault(step.area.value, []).append(attempt)
            run.checkpoint = apply_step(
                run.checkpoint,
                _step(
                    step,
                    StepStatus.failed,
                    tool_call_id=tool_call_id,
                    attempt=attempt,
                    error=OVER_CAP_ERROR,
                ),
            )
            continue
        attempt = SearchAttempt(
            family=step.family,
            query=step.query,
            status=outcome.status,
            document_count=len(outcome.documents),
            domains=_domains(outcome.documents),
            tool_call_id=tool_call_id,
            error=outcome.error,
        )
        run.attempts.append(attempt)
        run.area_attempts.setdefault(step.area.value, []).append(attempt)
        # The urls are stored without their credentials, because a stored url
        # can carry a password. A url the pipeline cannot parse is not stored.
        documents = [
            cleaned
            for url in outcome.documents
            if (cleaned := _without_credentials(url)) is not None
        ]

        found_competitors: list[Competitor] = []
        found_items: list[CounterEvidenceItem] = []
        found_findings: list[AreaFinding] = []
        if outcome.status is ToolCallStatus.success:
            # The items are stored without the credentials in their urls, and
            # their evidence refs are cleaned the same way.
            found_competitors = [
                _stamped(_stripped(competitor, "url"), tool_call_id)
                for competitor in outcome.competitors
            ]
            found_items = [
                _stamped(_stripped(item, "source_url"), tool_call_id)
                for item in outcome.counter_evidence
            ]
            found_findings = [
                _stamped(_stripped(finding, "source_url"), tool_call_id)
                for finding in outcome.findings
            ]
            # The evidence refs are handles, and a handle can be a url, so
            # they are cleaned the same way.
            found_competitors = [
                competitor.model_copy(
                    update={"evidence_refs": _clean_refs(competitor.evidence_refs)}
                )
                for competitor in found_competitors
            ]
            found_findings = [
                finding.model_copy(
                    update={"evidence_refs": _clean_refs(finding.evidence_refs)}
                )
                for finding in found_findings
            ]
            items = len(found_competitors) + len(found_items) + len(found_findings)
            if run.checkpoint_items + items > MAX_CHECKPOINT_ITEMS:
                # The checkpoint cannot hold this step's items, so none of them
                # is kept or used: the attempt fails, the step is marked
                # failed, and the run carries on.
                attempt = SearchAttempt(
                    family=step.family,
                    query=step.query,
                    status=ToolCallStatus.failure,
                    document_count=0,
                    domains=[],
                    tool_call_id=tool_call_id,
                    error=OVER_CAP_ERROR,
                )
                run.attempts.append(attempt)
                run.area_attempts.setdefault(step.area.value, []).append(attempt)
                run.checkpoint = apply_step(
                    run.checkpoint,
                    _step(
                        step,
                        StepStatus.failed,
                        tool_call_id=tool_call_id,
                        attempt=attempt,
                        error=OVER_CAP_ERROR,
                    ),
                )
                continue
            run.checkpoint_items += items
            run.source_urls.extend(documents)
            run.tool_calls.append(
                _tool_call(request, step, tool_call_id, outcome.result_count, clock)
            )
            for competitor in found_competitors:
                key = competitor.name.strip().lower()
                if key in run.competitor_refs:
                    # The same competitor found twice is one competitor.
                    continue
                run.competitor_refs[key] = tool_call_id
                run.competitors.append(competitor)
            run.counter_items.extend(found_items)
            run.counter_by_family.setdefault(step.family, []).extend(found_items)
            run.findings.extend(found_findings)

        run.checkpoint = apply_step(
            run.checkpoint,
            _step(
                step,
                (
                    StepStatus.completed
                    if outcome.status is ToolCallStatus.success
                    else StepStatus.failed
                ),
                tool_call_id=tool_call_id,
                attempt=attempt,
                source_urls=documents,
                result_count=outcome.result_count,
                competitors=found_competitors,
                counter_evidence=found_items,
                findings=found_findings,
                error=outcome.error,
            ),
        )

        if budget is not None and outcome.status is ToolCallStatus.success:
            # The spend is what the call consumed, not one unit per call.
            consumed = (
                len(outcome.documents)
                + len(outcome.competitors)
                + len(outcome.counter_evidence)
                + len(outcome.findings)
            )
            try:
                budget.record(step.area, BudgetDimension.crawl_items, consumed)
            except BudgetExceeded:
                # The call already happened, so the spend stays recorded, and
                # the run stops here rather than starting another call.
                run.stopped = True
                break

    return run


def _search_requirements(
    queries: Sequence[str], requirements: CoverageRequirements
) -> CoverageRequirements:
    """Return the floor a set of searches has to clear to count as covered.

    The set has to have searched every family it serves, and it has to meet
    the run's document and domain floors: an area that backs a "nothing
    exists" conclusion has to clear the same floors as the run, and so does
    the dimension that is judged on its own search.
    """
    return requirements.model_copy(
        update={"min_queries": max(1, len({query for query in queries}))}
    )


def _killer_assessments(
    run: _Run, requirements: CoverageRequirements
) -> list[DimensionAssessment]:
    """Judge every falsification dimension against what the run found."""
    attempts = run.area_attempts.get(ResearchArea.startup_killer.value, [])
    assessments: list[DimensionAssessment] = []
    for dimension in FalsificationDimension:
        family = DIMENSION_FAMILIES[dimension]
        dimension_attempts = [
            attempt for attempt in attempts if attempt.family == family.value
        ]
        disproofs = run.counter_by_family.get(family.value, [])
        # The bar the dimension is judged against: the run's floors with the
        # dimension's own query count.
        bar = _search_requirements(
            [attempt.query for attempt in dimension_attempts], requirements
        )
        if not dimension_attempts:
            status = DimensionStatus.inconclusive
            reason = "the dimension was not attacked"
        elif disproofs:
            status = DimensionStatus.falsified
            reason = "the search for this dimension returned a disproof"
        else:
            coverage = assess_coverage([family.value], dimension_attempts, bar)
            if coverage.status is CoverageStatus.complete:
                status = DimensionStatus.survived
                reason = "the attempt to kill this dimension did not succeed"
            else:
                status = DimensionStatus.inconclusive
                reason = "the coverage was too thin to decide"
        assessments.append(
            DimensionAssessment(
                dimension=dimension,
                status=status,
                attempts=dimension_attempts,
                evidence_refs=[
                    attempt.tool_call_id
                    for attempt in dimension_attempts
                    if attempt.tool_call_id
                ],
                reason=reason,
                # The assessment records the bar it was judged against.
                requirements=bar,
            )
        )
    return assessments


def _killer_reasons(assessments: Sequence[DimensionAssessment]) -> list[str]:
    """Name what the pass did and did not decide, in counts."""
    reasons: list[str] = []
    falsified = [
        assessment
        for assessment in assessments
        if assessment.status is DimensionStatus.falsified
    ]
    inconclusive = [
        assessment
        for assessment in assessments
        if assessment.status is DimensionStatus.inconclusive
    ]
    if falsified:
        reasons.append(f"{len(falsified)} dimensions were disproved")
    if inconclusive:
        reasons.append(f"{len(inconclusive)} dimensions were not decided")
    return reasons


@dataclass
class _Aggregate:
    """What the run found, area by area."""

    coverage: ResearchCoverage
    competitor_analysis: CompetitorAnalysis
    counter_result: CounterEvidenceResult
    areas: dict[str, AreaResearch]
    killer: StartupKillerReport
    outcome: InvestigationOutcome


def _aggregate(plan: InvestigationPlan, run: _Run, request: InvestigationRequest) -> _Aggregate:
    """Turn the attempts into the coverage, the analyses and the outcome."""
    areas = list(dict.fromkeys(step.area for step in plan.steps))
    area_coverage: dict[ResearchArea, ResearchCoverage] = {}
    for area in areas:
        area_steps = [step for step in plan.steps if step.area is area]
        area_coverage[area] = assess_coverage(
            [step.family for step in area_steps],
            run.area_attempts.get(area.value, []),
            _search_requirements([step.query for step in area_steps], request.requirements),
        )

    competitor_coverage = area_coverage[ResearchArea.competitor]
    competitor_analysis = analyse_competitors(
        run.competitors,
        competitor_coverage,
        [family.value for family in request.competitor_families],
    )

    counter_families = [family.value for family in request.counter_families]
    counter_steps = [step for step in plan.steps if step.area is ResearchArea.counter_evidence]
    counter_result = assess_counter_evidence(
        run.counter_items,
        run.area_attempts.get(ResearchArea.counter_evidence.value, []),
        counter_families,
        _search_requirements([step.query for step in counter_steps], request.requirements),
    )

    area_research: dict[str, AreaResearch] = {}
    for area in areas:
        coverage = area_coverage[area]
        area_findings = [finding for finding in run.findings if finding.area is area]
        area_research[area.value] = AreaResearch(
            area=area,
            findings=area_findings,
            coverage=coverage,
            complete=coverage.status is CoverageStatus.complete,
            # The tier is the strongest one the classifier read, and there is
            # no tier when the classifier read none.
            wtp_tier=strongest_wtp_tier(
                [
                    finding.wtp_tier
                    for finding in area_findings
                    if finding.wtp_tier is not None
                ]
            ),
            reasons=list(coverage.reasons),
        )

    assessments = _killer_assessments(run, request.requirements)
    killer = StartupKillerReport(
        verdict=derive_killer_verdict(assessments),
        assessments=assessments,
        dimensions_covered=len({assessment.dimension for assessment in assessments}),
        reasons=_killer_reasons(assessments),
    )

    coverage = assess_coverage(
        [step.family for step in plan.steps],
        run.attempts,
        request.requirements,
    )

    incomplete = [area for area in areas if not area_research[area.value].complete]
    if incomplete or coverage.status is not CoverageStatus.complete:
        # The run's own coverage binds as well: a run whose floor it does not
        # clear has not researched enough, however complete its areas are.
        outcome = InvestigationOutcome.incomplete
    elif (
        killer.verdict is StartupKillerVerdict.falsified
        or counter_result.status is CounterEvidenceStatus.found
    ):
        outcome = InvestigationOutcome.contradicted
    elif killer.verdict is StartupKillerVerdict.inconclusive:
        outcome = InvestigationOutcome.inconclusive
    else:
        outcome = InvestigationOutcome.supported

    return _Aggregate(
        coverage=coverage,
        competitor_analysis=competitor_analysis,
        counter_result=counter_result,
        areas=area_research,
        killer=killer,
        outcome=outcome,
    )


def _family_evidence(
    coverage: ResearchCoverage,
    attempts: Sequence[SearchAttempt],
    provenance: InvestigationProvenance,
    summary: str,
) -> list[ClaimEvidence]:
    """Return one piece of evidence per family the coverage completed."""
    by_family: dict[str, SearchAttempt] = {}
    for attempt in attempts:
        if attempt.status is ToolCallStatus.success:
            by_family.setdefault(attempt.family, attempt)
    evidence: list[ClaimEvidence] = []
    for family in coverage.completed_families:
        attempt = by_family.get(family)
        if attempt is None or not attempt.tool_call_id:
            continue
        if not provenance.covers(attempt.tool_call_id):
            continue
        evidence.append(
            ClaimEvidence(
                ref=attempt.tool_call_id,
                summary=summary.format(family=family),
                query_family=family,
            )
        )
    return evidence


def _finding_evidence(
    findings: Sequence[AreaFinding], provenance: InvestigationProvenance
) -> list[ClaimEvidence]:
    """Return one piece of evidence per finding the provenance can resolve."""
    evidence: list[ClaimEvidence] = []
    for finding in findings:
        ref: str | None = None
        for candidate in finding.evidence_refs:
            if candidate and provenance.covers(candidate):
                ref = candidate
                break
        if ref is None and finding.tool_call_id and provenance.covers(finding.tool_call_id):
            ref = finding.tool_call_id
        if ref is None and finding.source_url and provenance.covers(finding.source_url):
            ref = finding.source_url
        if ref is None:
            # A finding the provenance cannot point at is not evidence yet.
            continue
        evidence.append(
            ClaimEvidence(
                ref=ref,
                summary=f"the {finding.area.value} research found a result",
                query_family=finding.query_family,
            )
        )
    return evidence


def _build_claims(
    request: InvestigationRequest,
    run: _Run,
    aggregate: _Aggregate,
    provenance: InvestigationProvenance,
) -> list[InvestigationClaim]:
    """Build the claims the evidence supports, and nothing else."""
    claims: list[InvestigationClaim] = []
    counter = [0]

    def _claim(
        kind: ClaimKind,
        statement: str,
        evidence: list[ClaimEvidence],
        confidence: float | None = None,
    ) -> InvestigationClaim:
        counter[0] += 1
        return InvestigationClaim(
            claim_id=f"{request.investigation_id}-{kind.value}-{counter[0]}",
            kind=kind,
            statement=statement,
            stage=STAGE,
            status=ClaimStatus.supported,
            supporting_evidence=evidence,
            provenance=provenance,
            confidence=confidence,
        )

    if aggregate.competitor_analysis.status is (
        CompetitorAnalysisStatus.no_competitors_found
    ):
        evidence = _family_evidence(
            aggregate.competitor_analysis.coverage,
            run.area_attempts.get(ResearchArea.competitor.value, []),
            provenance,
            "the {family} family was searched and returned no competitor",
        )
        if evidence:
            claims.append(
                _claim(
                    ClaimKind.no_strong_competitor,
                    "the required competitor families were searched and no competitor was found",
                    evidence,
                    # The claim carries the confidence the analysis reached, so
                    # a reader does not have to look it up.
                    confidence=CONFIDENCE_VALUES[
                        aggregate.competitor_analysis.confidence
                    ],
                )
            )

    if aggregate.counter_result.status is CounterEvidenceStatus.none_found:
        evidence = _family_evidence(
            aggregate.counter_result.coverage,
            run.area_attempts.get(ResearchArea.counter_evidence.value, []),
            provenance,
            "the {family} family was searched and returned no counter-evidence",
        )
        if evidence:
            claims.append(
                _claim(
                    ClaimKind.no_counter_evidence_found,
                    "the required counter-evidence families were searched "
                    "and no counter-evidence was found",
                    evidence,
                )
            )

    if run.competitors:
        competitor_evidence: list[ClaimEvidence] = []
        for competitor in run.competitors:
            ref: str | None = None
            for candidate in competitor.evidence_refs:
                if candidate and provenance.covers(candidate):
                    ref = candidate
                    break
            if ref is None:
                ref = run.competitor_refs.get(competitor.name.strip().lower())
            if ref is None or not provenance.covers(ref):
                continue
            competitor_evidence.append(
                ClaimEvidence(
                    ref=ref,
                    summary="the search returned a competitor",
                    query_family=(
                        competitor.discovered_via[0] if competitor.discovered_via else None
                    ),
                )
            )
        if competitor_evidence:
            claims.append(
                _claim(
                    ClaimKind.competitors_exist,
                    "the search found a competitor for this problem",
                    competitor_evidence,
                )
            )

    area_claims = (
        (ResearchArea.existing_solution, ClaimKind.existing_solution_exists),
        (ResearchArea.market_validation, ClaimKind.market_validated),
        (ResearchArea.willingness_to_pay, ClaimKind.willingness_to_pay),
        (ResearchArea.distribution, ClaimKind.distribution_accessible),
    )
    for area, kind in area_claims:
        research = aggregate.areas.get(area.value)
        if research is None or not research.complete or not research.findings:
            continue
        evidence = _finding_evidence(research.findings, provenance)
        if not evidence:
            continue
        claims.append(
            _claim(
                kind,
                f"the {area.value} research completed and found a result",
                evidence,
            )
        )

    if aggregate.killer.verdict is StartupKillerVerdict.survived:
        evidence = []
        for assessment in aggregate.killer.assessments:
            for ref in assessment.evidence_refs:
                if provenance.covers(ref):
                    evidence.append(
                        ClaimEvidence(
                            ref=ref,
                            summary="the dimension survived the falsification attempt",
                        )
                    )
        if evidence:
            claims.append(
                _claim(
                    ClaimKind.opportunity_exists,
                    "the startup killer's dimensions all survived the attempt",
                    evidence,
                )
            )

    for assessment in aggregate.killer.assessments:
        if assessment.status is not DimensionStatus.survived:
            continue
        kind = DIMENSION_CLAIM_KINDS[assessment.dimension]
        evidence = [
            ClaimEvidence(
                ref=ref,
                summary="the dimension survived the falsification attempt",
            )
            for ref in assessment.evidence_refs
            if provenance.covers(ref)
        ]
        if not evidence:
            continue
        claims.append(
            _claim(
                kind,
                f"the {assessment.dimension.value} dimension survived the falsification attempt",
                evidence,
            )
        )

    return claims


def _incomplete_reasons(aggregate: _Aggregate, run: _Run) -> list[str]:
    """Name what the run could not answer, in counts and fixed phrases."""
    reasons: list[str] = []

    def _add(items: Sequence[str]) -> None:
        for item in items:
            if item not in reasons:
                reasons.append(item)

    _add(aggregate.coverage.reasons)
    for research in aggregate.areas.values():
        if not research.complete:
            _add(research.reasons)
    _add(aggregate.counter_result.reasons)
    _add(aggregate.killer.reasons)
    if any(attempt.error == OVER_CAP_ERROR for attempt in run.attempts):
        # The fixed phrase, so the caller can see why a step was refused
        # without the reason carrying anything from the refused data.
        _add([OVER_CAP_ERROR])
    return reasons


# The state the run recommends when the table does not allow the outcome's
# own state. It never promotes past what the transition table allows.
_FALLBACK_STATES: dict[InvestigationOutcome, tuple[OpportunityState, ...]] = {
    InvestigationOutcome.supported: (
        OpportunityState.promising,
        OpportunityState.verifying,
        OpportunityState.insufficient_evidence,
    ),
    InvestigationOutcome.contradicted: (
        OpportunityState.insufficient_evidence,
        OpportunityState.verifying,
    ),
    InvestigationOutcome.already_solved: (
        OpportunityState.insufficient_evidence,
        OpportunityState.verifying,
    ),
    InvestigationOutcome.inconclusive: (
        OpportunityState.insufficient_evidence,
        OpportunityState.verifying,
    ),
    InvestigationOutcome.incomplete: (
        OpportunityState.insufficient_evidence,
        OpportunityState.verifying,
    ),
}


def _recommended_state(
    state: OpportunityState, outcome: InvestigationOutcome
) -> OpportunityState:
    """Return the state the investigation recommends."""
    try:
        return next_state_for_outcome(state, outcome)
    except InvalidTransition:
        pass
    allowed = allowed_transitions(state)
    for candidate in _FALLBACK_STATES[outcome]:
        if candidate in allowed:
            return candidate
    if state is not OpportunityState.validated:
        return state
    return OpportunityState.insufficient_evidence


def investigate(
    request: InvestigationRequest,
    executor: ResearchExecutor,
    *,
    budget: InvestigationBudget | None = None,
    checkpoint: InvestigationCheckpoint | None = None,
    clock: Callable[[], datetime] = _utc_now,
) -> InvestigationResult:
    """Run an investigation over the injected executor."""
    started_at = clock()
    plan = build_plan(request)
    if checkpoint is None:
        checkpoint = build_checkpoint(
            request.investigation_id,
            request.run_id,
            request.problem,
            steps=[],
            created_at=clock(),
        )
    else:
        # The checkpoint has to belong to this investigation, this run and
        # this problem, or its evidence would be published under another
        # run's provenance.
        checkpoint = build_checkpoint(
            request.investigation_id,
            request.run_id,
            request.problem,
            checkpoint.steps,
            checkpoint.contract_fingerprint,
            created_at=checkpoint.created_at,
            expected_problem_hash=checkpoint.problem_hash,
            expected_investigation_id=checkpoint.investigation_id,
            expected_run_id=checkpoint.run_id,
        )

    run = _run_plan(request, plan, executor, budget, checkpoint, clock)
    aggregate = _aggregate(plan, run, request)

    provenance = InvestigationProvenance(
        run_id=request.run_id,
        stage=STAGE,
        agent=AGENT,
        tool_call_ids=[
            attempt.tool_call_id for attempt in run.attempts if attempt.tool_call_id
        ],
        source_urls=list(dict.fromkeys(run.source_urls))[:MAX_SOURCE_URLS],
        source_domains=sorted(
            {domain for attempt in run.attempts for domain in attempt.domains}
        ),
        query_families=sorted({attempt.family for attempt in run.attempts}),
        recorded_at=clock(),
        contract_fingerprint=run.checkpoint.contract_fingerprint,
    )

    claims = _build_claims(request, run, aggregate, provenance)

    return InvestigationResult(
        investigation_id=request.investigation_id,
        run_id=request.run_id,
        problem=request.problem,
        started_at=started_at,
        finished_at=clock(),
        outcome=aggregate.outcome,
        current_state=request.current_state,
        recommended_state=_recommended_state(request.current_state, aggregate.outcome),
        coverage=aggregate.coverage,
        competitors=aggregate.competitor_analysis,
        counter_evidence=aggregate.counter_result,
        areas=aggregate.areas,
        killer=aggregate.killer,
        claims=claims,
        provenance=provenance,
        tool_calls=run.tool_calls,
        checkpoint=run.checkpoint,
        steps_completed=len(completed_steps(run.checkpoint)),
        steps_total=len(plan.steps),
        incomplete_reasons=_incomplete_reasons(aggregate, run),
    )


def _grouped_by_call[ProducedT: (Competitor, CounterEvidenceItem, AreaFinding)](
    items: Sequence[ProducedT],
) -> dict[str, list[ProducedT]]:
    """Group the items by the call that produced them."""
    grouped: dict[str, list[ProducedT]] = {}
    for item in items:
        if not item.tool_call_id:
            # An item that names no call cannot be traced to one.
            continue
        grouped.setdefault(item.tool_call_id, []).append(item)
    return grouped


def _artifact_id(tool_call_id: str, kind: str) -> str:
    """Return a charset-safe id for one artifact of one call.

    The call id comes from the executor, so it is hashed rather than quoted:
    an id that carries spaces or non-ASCII characters is not an id the wave 1
    record can hold.
    """
    digest = hashlib.sha256(f"{tool_call_id}|{kind}".encode()).hexdigest()[:16]
    return f"{digest}-{kind}"


def _artifact(
    call: ToolCallProvenance, kind: str, item_count: int, created_at: datetime
) -> ArtifactRecord:
    """Record one artifact of the stage, bound to the call that produced it."""
    return ArtifactRecord(
        artifact_id=_artifact_id(call.tool_call_id, kind),
        stage=STAGE,
        kind=kind,
        # The call's result ref is already a durable handle in the wave 1
        # shape, so the artifact points at the same raw result.
        ref=f"{call.result_ref}/{kind}",
        item_count=item_count,
        produced_by_tool_call_id=call.tool_call_id,
        created_at=created_at,
        valid=True,
    )


def _zero_result(result: InvestigationResult) -> ZeroResultOutcome:
    """Record a zero only when the investigation's own rule authorised one.

    The wave 1 rule accepts a zero when the required families were searched
    and nothing failed. The investigation's rule is stricter: the counter
    search has to be complete, and no search anywhere in the run may have
    failed. A zero the investigation did not authorise must never reach the
    gate.
    """
    if (
        result.counter_evidence.status is not CounterEvidenceStatus.none_found
        or result.coverage.tool_failures != 0
    ):
        return ZeroResultOutcome(recorded=False)
    return ZeroResultOutcome(
        recorded=True,
        required_query_families=list(result.counter_evidence.required_families),
        completed_query_families=list(
            result.counter_evidence.coverage.completed_families
        ),
    )


def investigation_stage_execution(result: InvestigationResult) -> StageExecution:
    """Turn an investigation's result into the stage execution the gate judges.

    The stage succeeded: the investigation ran, and what it concluded is a
    separate fact. The artifacts are decomposed per producing call, so each
    one traces to exactly one successful search call of this stage.
    """
    competitors = _grouped_by_call(result.competitors.competitors)
    items = _grouped_by_call(result.counter_evidence.items)
    findings = _grouped_by_call(
        [
            finding
            for research in result.areas.values()
            for finding in research.findings
        ]
    )

    artifacts: list[ArtifactRecord] = []
    for call in result.tool_calls:
        if call.status is not ToolCallStatus.success or not call.tool_call_id:
            # A call that did not succeed, or that names no handle, cannot
            # carry an artifact the gate would trace.
            continue
        produced = competitors.get(call.tool_call_id, [])
        if produced:
            urls = {
                competitor.url for competitor in produced if competitor.url
            }
            artifacts.append(
                _artifact(call, KIND_CANDIDATE_URL, len(urls), result.finished_at)
            )
            artifacts.append(
                _artifact(call, KIND_COMPETITORS, len(produced), result.finished_at)
            )
        counter_items = items.get(call.tool_call_id, [])
        if counter_items:
            artifacts.append(
                _artifact(
                    call, KIND_COUNTER_EVIDENCE, len(counter_items), result.finished_at
                )
            )
        area_findings = findings.get(call.tool_call_id, [])
        if area_findings:
            artifacts.append(
                _artifact(call, KIND_ALTERNATIVES, len(area_findings), result.finished_at)
            )

    return StageExecution(
        stage=STAGE,
        status=StageStatus.succeeded,
        started_at=result.started_at,
        finished_at=result.finished_at,
        tool_calls=list(result.tool_calls),
        artifacts=artifacts,
        zero_result=_zero_result(result),
    )

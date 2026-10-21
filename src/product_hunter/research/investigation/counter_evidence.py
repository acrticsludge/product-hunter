"""The counter-evidence assessment: what argues against the idea, and how sure.

"None found" is only reachable when the search actually ran, so an incomplete
search reads as incomplete, which is the mission's UNKNOWN rather than a
claim that no counter-evidence exists.
"""

from __future__ import annotations

from collections.abc import Sequence
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from product_hunter.research.investigation.coverage import (
    CoverageRequirements,
    CoverageStatus,
    ResearchCoverage,
    SearchAttempt,
    assess_coverage,
)


class CounterEvidenceKind(str, Enum):
    """What kind of counter-evidence the item is."""

    existing_solution = "existing_solution"
    strong_competitor = "strong_competitor"
    free_alternative = "free_alternative"
    low_willingness_to_pay = "low_willingness_to_pay"
    weak_pain = "weak_pain"
    infrequent_problem = "infrequent_problem"
    market_saturation = "market_saturation"
    technical_blocker = "technical_blocker"
    regulatory_constraint = "regulatory_constraint"
    problem_not_important = "problem_not_important"
    contradicting_thesis = "contradicting_thesis"


class CounterEvidenceItem(BaseModel):
    """One piece of evidence against the idea."""

    model_config = ConfigDict(extra="forbid")

    item_id: str = Field(min_length=1)
    kind: CounterEvidenceKind
    summary: str = Field(min_length=1)
    source_url: str | None = None
    source_domain: str | None = None
    tool_call_id: str | None = None
    query_family: str | None = None
    strength: float = Field(default=1.0, ge=0.0, le=1.0)


class CounterEvidenceStatus(str, Enum):
    """What the counter-evidence search concluded."""

    found = "found"
    none_found = "none_found"
    incomplete = "incomplete"


class CounterEvidenceResult(BaseModel):
    """The counter-evidence picture, with the coverage it rests on."""

    model_config = ConfigDict(extra="forbid")

    status: CounterEvidenceStatus
    items: list[CounterEvidenceItem] = Field(default_factory=list)
    coverage: ResearchCoverage
    required_families: list[str] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_invariants(self) -> CounterEvidenceResult:
        if self.status is CounterEvidenceStatus.none_found:
            if self.coverage.status is not CoverageStatus.complete:
                # A zero needs a real coverage, so an incomplete search never
                # reads as "no counter-evidence exists".
                raise ValueError(
                    "none found is only reachable on a complete coverage"
                )
            if self.items:
                raise ValueError("none found cannot carry counter-evidence items")
        if self.status is CounterEvidenceStatus.found and not self.items:
            raise ValueError("found needs counter-evidence items")
        if self.status is CounterEvidenceStatus.incomplete and self.items:
            # The items are real, but the search behind them was thin, so the
            # result is not a conclusion yet.
            raise ValueError("an incomplete result carries no items")
        return self


def assess_counter_evidence(
    items: Sequence[CounterEvidenceItem],
    attempts: Sequence[SearchAttempt],
    required_families: Sequence[str],
    requirements: CoverageRequirements,
) -> CounterEvidenceResult:
    """Judge what the counter-evidence search found, and what it could not answer."""
    coverage = assess_coverage(required_families, attempts, requirements)
    required = [family.strip() for family in required_families if family.strip()]

    reasons: list[str] = []
    if items:
        reasons.append(f"{len(items)} counter-evidence items were found")
    for reason in coverage.reasons:
        if reason not in reasons:
            reasons.append(reason)

    if items:
        status = CounterEvidenceStatus.found
    elif coverage.status is CoverageStatus.complete:
        status = CounterEvidenceStatus.none_found
    else:
        status = CounterEvidenceStatus.incomplete

    return CounterEvidenceResult(
        status=status,
        items=list(items),
        coverage=coverage,
        required_families=required,
        reasons=reasons,
    )

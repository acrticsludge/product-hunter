"""The startup killer: the ten dimensions it attacks and the verdict they add up to.

A dimension reads survived only when it was actually attacked and the search
behind it ran completely, so a report that covers eight of the ten dimensions
is inconclusive rather than a pass.
"""

from __future__ import annotations

from collections.abc import Sequence
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from product_hunter.research.evidence import ClaimKind
from product_hunter.research.investigation.coverage import (
    CoverageRequirements,
    CoverageStatus,
    SearchAttempt,
    assess_coverage,
)
from product_hunter.research.investigation.queries import QueryFamily


class FalsificationDimension(str, Enum):
    """One way the idea can be killed."""

    problem_existence = "problem_existence"
    recurrence = "recurrence"
    severity = "severity"
    independent_population = "independent_population"
    willingness_to_pay = "willingness_to_pay"
    market_size = "market_size"
    competition = "competition"
    distribution = "distribution"
    buildability = "buildability"
    timing = "timing"


# The family that attacks each dimension. The ten killer families line up with
# the ten dimensions one for one, so a dimension's coverage is judged on its
# own family.
DIMENSION_FAMILIES: dict[FalsificationDimension, QueryFamily] = {
    FalsificationDimension.problem_existence: QueryFamily.killer_problem_existence,
    FalsificationDimension.recurrence: QueryFamily.killer_recurrence,
    FalsificationDimension.severity: QueryFamily.killer_severity,
    FalsificationDimension.independent_population: (
        QueryFamily.killer_independent_population
    ),
    FalsificationDimension.willingness_to_pay: QueryFamily.killer_willingness_to_pay,
    FalsificationDimension.market_size: QueryFamily.killer_market_size,
    FalsificationDimension.competition: QueryFamily.killer_competition,
    FalsificationDimension.distribution: QueryFamily.killer_distribution,
    FalsificationDimension.buildability: QueryFamily.killer_buildability,
    FalsificationDimension.timing: QueryFamily.killer_timing,
}

# The family that attacks a dimension, read the other way round.
FAMILY_DIMENSIONS: dict[QueryFamily, FalsificationDimension] = {
    family: dimension for dimension, family in DIMENSION_FAMILIES.items()
}

# The claim each dimension's survival is published as.
DIMENSION_CLAIM_KINDS: dict[FalsificationDimension, ClaimKind] = {
    FalsificationDimension.problem_existence: ClaimKind.problem_exists,
    FalsificationDimension.recurrence: ClaimKind.problem_recurs,
    FalsificationDimension.severity: ClaimKind.problem_is_severe,
    FalsificationDimension.independent_population: (
        ClaimKind.independent_population_exists
    ),
    FalsificationDimension.willingness_to_pay: ClaimKind.willingness_to_pay,
    FalsificationDimension.market_size: ClaimKind.market_is_large,
    FalsificationDimension.competition: ClaimKind.competitors_exist,
    FalsificationDimension.distribution: ClaimKind.distribution_accessible,
    FalsificationDimension.buildability: ClaimKind.buildable,
    FalsificationDimension.timing: ClaimKind.timing_is_right,
}

# The bar a dimension has to clear when nothing else says: its family ran, a
# document came back, and the document came from two sources. The pipeline
# passes the run's own requirements, which are the same floors the areas
# clear.
_DIMENSION_REQUIREMENTS = CoverageRequirements(
    min_queries=1, min_documents=1, min_domains=2
)


class DimensionStatus(str, Enum):
    """What the attempt to kill a dimension found."""

    survived = "survived"
    falsified = "falsified"
    inconclusive = "inconclusive"


class DimensionAssessment(BaseModel):
    """One dimension and the attempt that was made against it."""

    model_config = ConfigDict(extra="forbid")

    dimension: FalsificationDimension
    status: DimensionStatus
    attempts: list[SearchAttempt] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    reason: str = ""
    # The bar this assessment was judged against, so the record and the
    # pipeline agree on what a dimension had to clear.
    requirements: CoverageRequirements = Field(
        default_factory=lambda: _DIMENSION_REQUIREMENTS.model_copy()
    )

    @model_validator(mode="after")
    def _check_invariants(self) -> DimensionAssessment:
        if self.dimension not in FAMILY_DIMENSIONS.values():
            # A dimension with no family has no search behind it, so it is
            # caught here rather than at run time.
            raise ValueError("the dimension has no query family to search")
        if self.status is DimensionStatus.survived:
            if not self.attempts:
                # A dimension nobody attacked has not survived anything.
                raise ValueError("a dimension nobody attacked has not survived anything")
            coverage = assess_coverage(
                [DIMENSION_FAMILIES[self.dimension].value],
                self.attempts,
                self.requirements,
            )
            if coverage.status is not CoverageStatus.complete:
                raise ValueError("a dimension survives only on a complete coverage")
        if self.status is DimensionStatus.falsified:
            if not self.attempts:
                raise ValueError("a dimension nobody attacked has not been disproved")
            if not self.reason.strip():
                # A disproof nobody can name is not a disproof.
                raise ValueError("a falsified dimension must name the disproof")
        return self


class StartupKillerVerdict(str, Enum):
    """What the whole pass adds up to."""

    survived = "survived"
    falsified = "falsified"
    inconclusive = "inconclusive"


def derive_killer_verdict(
    assessments: Sequence[DimensionAssessment],
    dimensions_required: int = len(FalsificationDimension),
) -> StartupKillerVerdict:
    """Add the dimension assessments up into one verdict."""
    statuses = [assessment.status for assessment in assessments]
    if any(status is DimensionStatus.falsified for status in statuses):
        return StartupKillerVerdict.falsified
    if any(status is DimensionStatus.inconclusive for status in statuses):
        return StartupKillerVerdict.inconclusive
    covered = {assessment.dimension for assessment in assessments}
    if covered and len(covered) >= dimensions_required:
        return StartupKillerVerdict.survived
    # A report that covers fewer dimensions than it needs is inconclusive,
    # not a pass.
    return StartupKillerVerdict.inconclusive


class StartupKillerReport(BaseModel):
    """The startup killer's pass over the dimensions."""

    model_config = ConfigDict(extra="forbid")

    verdict: StartupKillerVerdict
    assessments: list[DimensionAssessment] = Field(default_factory=list)
    dimensions_covered: int = Field(default=0, ge=0)
    dimensions_required: int = Field(
        default=len(FalsificationDimension), ge=1, le=len(FalsificationDimension)
    )
    reasons: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_invariants(self) -> StartupKillerReport:
        covered = {assessment.dimension for assessment in self.assessments}
        if self.dimensions_covered != len(covered):
            raise ValueError("the dimensions covered count the dimensions the report holds")
        if self.verdict is not derive_killer_verdict(
            self.assessments, self.dimensions_required
        ):
            raise ValueError("the verdict does not match the assessments")
        return self

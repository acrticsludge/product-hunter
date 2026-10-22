"""The competitor analysis, and the rule that separates "none found" from "none exist".

A complete search of a narrow slice of the market is not proof of absence, so
"no competitors found" is a claim with a confidence and a list of unknowns, and
an incomplete search never gets that far.
"""

from __future__ import annotations

from collections.abc import Sequence
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from product_hunter.research.investigation.coverage import (
    CoverageStatus,
    ResearchCoverage,
)

# The pricing vocabulary a competitor reports.
PRICING_MODELS = ("free", "freemium", "paid", "one_time", "unknown")


class CompetitorConfidence(str, Enum):
    """How sure the analysis is about a competitor."""

    high = "high"
    medium = "medium"
    low = "low"
    unknown = "unknown"


# The order the confidences are compared in.
_CONFIDENCE_ORDER: dict[CompetitorConfidence, int] = {
    CompetitorConfidence.high: 3,
    CompetitorConfidence.medium: 2,
    CompetitorConfidence.low: 1,
    CompetitorConfidence.unknown: 0,
}

# The number a confidence level carries on a claim. The claim's confidence is
# a number and the analysis's is a level, so the levels are read on a fixed
# scale.
CONFIDENCE_VALUES: dict[CompetitorConfidence, float] = {
    CompetitorConfidence.high: 0.8,
    CompetitorConfidence.medium: 0.5,
    CompetitorConfidence.low: 0.2,
    CompetitorConfidence.unknown: 0.0,
}


class Competitor(BaseModel):
    """One competitor the search found."""

    model_config = ConfigDict(extra="forbid")

    competitor_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    domain: str | None = None
    url: str | None = None
    pricing: str | None = None
    pricing_model: str | None = None
    positioning: str = ""
    customer_segment: str = ""
    overlap: list[str] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    unknowns: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    discovered_via: list[str] = Field(default_factory=list)
    # The call that found this competitor, so the wave 1 gate can trace the
    # artifact of that call back to it.
    tool_call_id: str | None = None
    confidence: CompetitorConfidence = CompetitorConfidence.unknown

    @model_validator(mode="after")
    def _check_invariants(self) -> Competitor:
        if self.pricing_model is not None and self.pricing_model not in PRICING_MODELS:
            # A pricing model outside the vocabulary is a phrase the analysis
            # cannot compare, so it is refused rather than stored.
            raise ValueError("the pricing model must be one of the known models")
        if self.confidence is CompetitorConfidence.unknown and not self.unknowns:
            # An unknown confidence that names nothing is a shrug.
            raise ValueError(
                "a competitor with an unknown confidence must name what is unknown"
            )
        return self


class CompetitorAnalysisStatus(str, Enum):
    """What the competitor search concluded."""

    competitors_found = "competitors_found"
    no_competitors_found = "no_competitors_found"
    unknown = "unknown"


class CompetitorAnalysis(BaseModel):
    """The competitor picture, with the coverage it rests on."""

    model_config = ConfigDict(extra="forbid")

    status: CompetitorAnalysisStatus
    competitors: list[Competitor] = Field(default_factory=list)
    confidence: CompetitorConfidence
    coverage: ResearchCoverage
    unknowns: list[str] = Field(default_factory=list)
    notes: str = ""

    @model_validator(mode="after")
    def _check_invariants(self) -> CompetitorAnalysis:
        if self.status is CompetitorAnalysisStatus.no_competitors_found:
            if self.coverage.status is not CoverageStatus.complete:
                # "No competitor found" on a thin search is "we did not look",
                # so it never reaches this status.
                raise ValueError(
                    "no competitors found is only reachable on a complete coverage"
                )
            if self.confidence is CompetitorConfidence.high:
                # A complete search of a narrow slice is not proof of absence.
                raise ValueError(
                    "a search that found no competitor is never a high confidence"
                )
            if self.competitors:
                raise ValueError("no competitors found cannot carry competitors")
        if (
            self.status is CompetitorAnalysisStatus.competitors_found
            and not self.competitors
        ):
            raise ValueError("competitors found needs competitors")
        if (
            self.status is CompetitorAnalysisStatus.unknown
            and self.confidence is not CompetitorConfidence.unknown
        ):
            raise ValueError("an unknown competitor analysis carries an unknown confidence")
        return self


def _family_name(family: str) -> str:
    """Return the plain name of a family."""
    return family.strip()


def analyse_competitors(
    competitors: Sequence[Competitor],
    coverage: ResearchCoverage,
    required_families: Sequence[str],
) -> CompetitorAnalysis:
    """Judge what the competitor search found, and what it could not answer."""
    required = [
        name for name in (_family_name(family) for family in required_families) if name
    ]
    completed = set(coverage.completed_families)
    families_covered = all(family in completed for family in required)
    complete = coverage.status is CoverageStatus.complete and families_covered

    if competitors:
        confidence = max(
            (competitor.confidence for competitor in competitors),
            key=lambda value: _CONFIDENCE_ORDER[value],
        )
        unknowns: list[str] = []
        for competitor in competitors:
            for unknown in competitor.unknowns:
                if unknown not in unknowns:
                    unknowns.append(unknown)
        for reason in coverage.reasons:
            if reason not in unknowns:
                unknowns.append(reason)
        return CompetitorAnalysis(
            status=CompetitorAnalysisStatus.competitors_found,
            competitors=list(competitors),
            confidence=confidence,
            coverage=coverage,
            unknowns=unknowns,
        )

    if complete:
        return CompetitorAnalysis(
            status=CompetitorAnalysisStatus.no_competitors_found,
            competitors=[],
            confidence=CompetitorConfidence.medium,
            coverage=coverage,
            unknowns=[
                "a complete search of a narrow slice of the market is not proof of absence"
            ],
            notes="the required competitor families were searched and returned no competitor",
        )

    return CompetitorAnalysis(
        status=CompetitorAnalysisStatus.unknown,
        competitors=[],
        confidence=CompetitorConfidence.unknown,
        coverage=coverage,
        unknowns=[
            *coverage.reasons,
            "the competitor search did not cover every required family",
        ],
    )

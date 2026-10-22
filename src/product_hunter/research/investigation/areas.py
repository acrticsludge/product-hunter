"""The research areas an investigation covers, and what each one found.

An area is complete only when its coverage is complete: a finding from a
thin search is a finding, but it is not the whole picture.
"""

from __future__ import annotations

from collections.abc import Sequence
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from product_hunter.research.investigation.coverage import (
    CoverageStatus,
    ResearchCoverage,
)


class ResearchArea(str, Enum):
    """What part of the idea the research looked at."""

    competitor = "competitor"
    existing_solution = "existing_solution"
    alternative = "alternative"
    workaround = "workaround"
    market_validation = "market_validation"
    willingness_to_pay = "willingness_to_pay"
    distribution = "distribution"
    counter_evidence = "counter_evidence"
    startup_killer = "startup_killer"


class WtpTier(str, Enum):
    """How strong the payment signal is."""

    w0 = "W0"
    w1 = "W1"
    w2 = "W2"
    w3 = "W3"
    w4 = "W4"


class AreaFinding(BaseModel):
    """One thing the research found in an area."""

    model_config = ConfigDict(extra="forbid")

    finding_id: str = Field(min_length=1)
    area: ResearchArea
    summary: str = Field(min_length=1)
    detail: str = ""
    source_url: str | None = None
    source_domain: str | None = None
    tool_call_id: str | None = None
    query_family: str | None = None
    evidence_refs: list[str] = Field(default_factory=list)
    # The payment tier the classifier read. Only the willingness-to-pay area
    # carries one, because only there means anything.
    wtp_tier: WtpTier | None = None

    @model_validator(mode="after")
    def _check_invariants(self) -> AreaFinding:
        if self.wtp_tier is not None and self.area is not ResearchArea.willingness_to_pay:
            raise ValueError(
                "the willingness-to-pay tier is only meaningful for that area"
            )
        return self


# The tiers from the weakest signal to the strongest.
_WTP_ORDER: dict[WtpTier, int] = {
    WtpTier.w0: 0,
    WtpTier.w1: 1,
    WtpTier.w2: 2,
    WtpTier.w3: 3,
    WtpTier.w4: 4,
}


def strongest_wtp_tier(tiers: Sequence[WtpTier]) -> WtpTier | None:
    """Return the strongest tier among them, or none when there is none."""
    if not tiers:
        return None
    return max(tiers, key=lambda tier: _WTP_ORDER[tier])


class AreaResearch(BaseModel):
    """What one area's research found, and how completely it ran."""

    model_config = ConfigDict(extra="forbid")

    area: ResearchArea
    findings: list[AreaFinding] = Field(default_factory=list)
    coverage: ResearchCoverage
    complete: bool = False
    wtp_tier: WtpTier | None = None
    reasons: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_invariants(self) -> AreaResearch:
        if self.wtp_tier is not None and self.area is not ResearchArea.willingness_to_pay:
            # A payment tier on another area is a number nobody asked for.
            raise ValueError(
                "the willingness-to-pay tier is only meaningful for that area"
            )
        covered = self.coverage.status is CoverageStatus.complete
        if self.complete != covered:
            # An area that reports itself complete on a thin coverage is the
            # "we did not look" mistake, so it is refused here.
            raise ValueError("an area is complete only when its coverage is complete")
        for finding in self.findings:
            if finding.area is not self.area:
                raise ValueError("a finding is filed under the area it names")
        return self

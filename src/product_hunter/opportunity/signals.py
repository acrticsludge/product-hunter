"""The input types of the opportunity scorer, and the independence computation.

Every type here is an input or a summary of an input. Nothing in this module
calls a model, reads a clock, or touches a database: the numbers the scorer
produces are a pure function of the arguments it is handed and the policy it
is given.
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Sequence
from datetime import date, datetime
from enum import Enum
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from product_hunter.extraction import Frequency

if TYPE_CHECKING:  # pragma: no cover - the import is for the type only
    from product_hunter.opportunity.policy import ScoringPolicy

# The severity scale of the extraction stage: a pain point runs from no pain at
# all to a compliance, legal, or safety risk.
SEVERITY_MAX = 5


def _require_aware(value: datetime | None, field: str) -> None:
    """Refuse a timestamp that carries no timezone.

    A naive timestamp cannot be compared with an aware one, so it silently
    switches off every check that measures a duration. The name of the field
    that was refused reaches the message.
    """
    if value is None:
        return
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        raise ValueError(f"{field} must be timezone-aware")


class SourceReliability(str, Enum):
    """How much weight a source carries in the reliability mix."""

    tier_a = "tier_a"
    tier_b = "tier_b"
    tier_c = "tier_c"
    tier_d = "tier_d"
    unrated = "unrated"


class EvidenceItem(BaseModel):
    """One collected piece of evidence and the identity behind it.

    The propagation confidence is how strongly the item is believed to BE a
    propagated copy, the same reading the propagation edge carries: a
    confidence of zero means no propagation is believed. The weight a copy
    carries is therefore one minus that confidence, so a copy believed with
    ninety percent confidence of being a copy carries a tenth of the weight
    of an original observation.
    """

    model_config = ConfigDict(extra="forbid")

    evidence_id: str = Field(min_length=1)
    author_id: str = Field(min_length=1)
    community_id: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    source_reliability: SourceReliability
    published_at: datetime
    identity_confidence: float = Field(ge=0, le=1)
    is_canonical: bool = True
    propagation_confidence: float = Field(default=0.0, ge=0, le=1)

    @field_validator("published_at")
    @classmethod
    def _published_at_is_aware(cls, value: datetime) -> datetime:
        _require_aware(value, "published_at")
        return value

    @model_validator(mode="after")
    def _check_invariants(self) -> EvidenceItem:
        if self.is_canonical and self.propagation_confidence != 0.0:
            raise ValueError(
                "a canonical observation is not a copy, so it carries no "
                "propagation confidence"
            )
        if not self.is_canonical and self.propagation_confidence <= 0.0:
            raise ValueError(
                "a propagated copy carries the confidence that it is a copy, "
                "which is above zero"
            )
        return self


class PainObservation(BaseModel):
    """One pain point the extraction stage already resolved."""

    model_config = ConfigDict(extra="forbid")

    pain_id: str = Field(min_length=1)
    severity: int = Field(ge=0, le=SEVERITY_MAX)
    frequency: Frequency


class WtpTier(str, Enum):
    """How willing the reported people are to pay, from W0 to W4."""

    W0 = "W0"
    W1 = "W1"
    W2 = "W2"
    W3 = "W3"
    W4 = "W4"


# The tiers from the least willing to the most willing. Two tiers are compared
# by their place in this order, never by their names.
WTP_TIER_ORDER: tuple[WtpTier, ...] = (
    WtpTier.W0,
    WtpTier.W1,
    WtpTier.W2,
    WtpTier.W3,
    WtpTier.W4,
)


def wtp_tier_rank(tier: WtpTier) -> int:
    """Return the tier's place in the willingness-to-pay order."""
    return WTP_TIER_ORDER.index(tier)


class WtpSignalKind(str, Enum):
    """The kinds of signal that can support a willingness-to-pay tier."""

    complaint = "complaint"
    stated_budget = "stated_budget"
    purchase_intent = "purchase_intent"
    repeated_payment = "repeated_payment"
    paid_customer = "paid_customer"


class WtpSignal(BaseModel):
    """One signal behind a willingness-to-pay tier.

    The signal is only as good as the evidence it names, so it has to name
    at least one.
    """

    model_config = ConfigDict(extra="forbid")

    kind: WtpSignalKind
    detail: str = ""
    evidence_ids: list[str] = Field(min_length=1)


class WtpAssessment(BaseModel):
    """The declared willingness to pay and the signals behind it.

    The tier may not exceed the strongest signal present. The cap of every
    signal kind lives in the policy, so the rule is checked when the
    assessment is scored, where the policy is in hand.
    """

    model_config = ConfigDict(extra="forbid")

    tier: WtpTier
    signals: list[WtpSignal] = Field(default_factory=list)


class MarketAssumptions(BaseModel):
    """One assumption set of a market estimate.

    The numbers are finite: an infinite assumption would put a broken figure
    into the market factor and into the snapshot id.
    """

    model_config = ConfigDict(extra="forbid")

    reachable_population: float = Field(ge=0, allow_inf_nan=False)
    problem_incidence: float = Field(ge=0, le=1, allow_inf_nan=False)
    adoption_rate: float = Field(ge=0, le=1, allow_inf_nan=False)
    annual_price_usd: float = Field(ge=0, allow_inf_nan=False)

    @property
    def annual_opportunity_usd(self) -> float:
        """The annual opportunity the assumptions multiply out to."""
        return (
            self.reachable_population
            * self.problem_incidence
            * self.adoption_rate
            * self.annual_price_usd
        )


class MarketEstimate(BaseModel):
    """Three assumption sets: the low, the base, and the high.

    An estimate with no band is refused: a point estimate carries no
    uncertainty, and the range is built from the band.
    """

    model_config = ConfigDict(extra="forbid")

    low: MarketAssumptions
    base: MarketAssumptions
    high: MarketAssumptions

    @model_validator(mode="after")
    def _check_band(self) -> MarketEstimate:
        low = self.low.annual_opportunity_usd
        base = self.base.annual_opportunity_usd
        high = self.high.annual_opportunity_usd
        if not low <= base <= high:
            raise ValueError(
                "the market assumption sets are not ordered low <= base <= high"
            )
        if low >= high:
            raise ValueError(
                "a market estimate with no uncertainty band is refused"
            )
        return self


class CompetitionDirection(str, Enum):
    """The competitive position, as the caller declares it."""

    tailwind = "tailwind"
    neutral = "neutral"
    headwind = "headwind"


class CompetitiveAssessment(BaseModel):
    """The competitive position and the search behind it.

    The direction has no default. A default is how an unexamined market
    becomes a tailwind. A count of competitors above zero needs a search
    that ran: a search that did not run cannot have found anything.
    """

    model_config = ConfigDict(extra="forbid")

    direction: CompetitionDirection
    competitors_found: int = Field(default=0, ge=0)
    search_ran: bool = False
    evidence_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_invariants(self) -> CompetitiveAssessment:
        if self.competitors_found > 0 and not self.search_ran:
            raise ValueError(
                "competitors_found above zero needs search_ran: a search that "
                "did not run cannot have found competitors"
            )
        return self


class BuildabilityAssessment(BaseModel):
    """How feasible the build is, and what holds it back."""

    model_config = ConfigDict(extra="forbid")

    feasibility: float = Field(ge=0, le=1)
    blockers: list[str] = Field(default_factory=list)


class TrendDirection(str, Enum):
    """Which way the evidence is moving over the observation window."""

    rising = "rising"
    flat = "flat"
    falling = "falling"
    unknown = "unknown"


class TrendWindow(BaseModel):
    """The period the canonical evidence was observed in."""

    model_config = ConfigDict(extra="forbid")

    first_seen: date
    last_seen: date
    distinct_months: int = Field(ge=0)

    @model_validator(mode="after")
    def _check_window(self) -> TrendWindow:
        if self.last_seen < self.first_seen:
            raise ValueError("the observation window ends before it starts")
        return self


class TrendSignal(BaseModel):
    """The direction the evidence is moving.

    This is a report, never an input: it is not a field of either input
    model, and neither scorer reads it.
    """

    model_config = ConfigDict(extra="forbid")

    direction: TrendDirection
    window: TrendWindow | None = None
    earlier_mentions: int = Field(default=0, ge=0)
    recent_mentions: int = Field(default=0, ge=0)


class ExtractionAgreement(BaseModel):
    """How the extractions of the same evidence agreed with the consensus.

    The extractors are the distinct models that ran. Ten extractions from
    one model are not agreement across models, so the count matters as much
    as the rate.
    """

    model_config = ConfigDict(extra="forbid")

    agreements: int = Field(default=0, ge=0)
    disagreements: int = Field(default=0, ge=0)
    extractors: int = Field(default=0, ge=0)


class CounterEvidenceSummary(BaseModel):
    """What the counter-evidence search covered and what it contradicted."""

    model_config = ConfigDict(extra="forbid")

    search_ran: bool = False
    families_covered: int = Field(default=0, ge=0)
    families_required: int = Field(default=0, ge=0)
    contradictions_found: int = Field(default=0, ge=0)
    contradictions_checked: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def _check_invariants(self) -> CounterEvidenceSummary:
        if not self.search_ran and (
            self.families_covered or self.contradictions_found
        ):
            raise ValueError(
                "a counter-evidence search that did not run covered no query "
                "family and found no contradiction"
            )
        if self.contradictions_found > self.contradictions_checked:
            raise ValueError(
                "contradictions_found cannot exceed contradictions_checked"
            )
        return self


class PotentialInputs(BaseModel):
    """Everything the potential scorer may read.

    Nothing about evidence quality, source reliability, independence, or
    extraction agreement appears here.
    """

    model_config = ConfigDict(extra="forbid")

    pain_observations: list[PainObservation] = Field(default_factory=list)
    wtp: WtpAssessment
    market: MarketEstimate
    competition: CompetitiveAssessment
    buildability: BuildabilityAssessment


class ConfidenceInputs(BaseModel):
    """Everything the confidence scorer may read.

    Nothing about severity, willingness to pay, market size, competition, or
    buildability appears here.
    """

    model_config = ConfigDict(extra="forbid")

    evidence: list[EvidenceItem] = Field(default_factory=list)
    extraction_agreement: ExtractionAgreement = Field(
        default_factory=ExtractionAgreement
    )
    counter_evidence: CounterEvidenceSummary = Field(
        default_factory=CounterEvidenceSummary
    )


class IndependenceSummary(BaseModel):
    """The numbers the independence computation produces."""

    model_config = ConfigDict(extra="forbid")

    raw_mention_count: int = Field(ge=0)
    canonical_count: int = Field(ge=0)
    independent_author_count: int = Field(ge=0)
    uncertain_author_count: int = Field(ge=0)
    independent_community_count: int = Field(ge=0)
    community_concentration: float = Field(ge=0, le=1)
    source_concentration: float = Field(ge=0, le=1)
    effective_population: float = Field(ge=0)
    propagation_adjusted_count: float = Field(ge=0)
    window: TrendWindow | None = None

    @property
    def concentration(self) -> float:
        """The larger of the community and the source Herfindahl index."""
        return max(self.community_concentration, self.source_concentration)


def _herfindahl(counts: Counter[str]) -> float:
    """Return the Herfindahl index over the shares of a counter.

    The squares are summed with fsum, so the index is a function of the
    shares and not of the order they were counted in.
    """
    total = sum(counts.values())
    if total <= 0:
        return 0.0
    return math.fsum((count / total) ** 2 for count in counts.values())


def _observation_window(canonical: Sequence[EvidenceItem]) -> TrendWindow | None:
    """Return the window the canonical evidence was observed in."""
    if not canonical:
        return None
    days = [item.published_at.date() for item in canonical]
    months = {(day.year, day.month) for day in days}
    return TrendWindow(
        first_seen=min(days),
        last_seen=max(days),
        distinct_months=len(months),
    )


def compute_independence(
    evidence: Sequence[EvidenceItem], policy: ScoringPolicy
) -> IndependenceSummary:
    """Compute the independence numbers over a set of evidence.

    An author at or above the policy's identity threshold counts as
    independent; one below it is uncertain and is reported beside the
    independent count, never folded into it. A propagated copy never adds to
    the canonical count, and the confidence that it is a copy discounts its
    weight: the weight it carries is one minus that confidence.
    """
    threshold = policy.identity.min_confidence
    canonical = [item for item in evidence if item.is_canonical]

    strengths: dict[str, float] = {}
    for item in canonical:
        author = item.author_id
        if author not in strengths or item.identity_confidence > strengths[author]:
            strengths[author] = item.identity_confidence
    independent = {
        author for author, value in strengths.items() if value >= threshold
    }
    uncertain = {author for author, value in strengths.items() if value < threshold}

    community_counts: Counter[str] = Counter()
    source_counts: Counter[str] = Counter()
    for item in canonical:
        if item.author_id in independent:
            community_counts[item.community_id] += 1
            source_counts[item.source_id] += 1

    # The sums are exact, so they are a function of the records and not of
    # the order they arrived in.
    effective_population = math.fsum(strengths[author] for author in independent)
    propagation_adjusted = float(len(canonical)) + math.fsum(
        1.0 - item.propagation_confidence
        for item in evidence
        if not item.is_canonical
    )

    return IndependenceSummary(
        raw_mention_count=len(evidence),
        canonical_count=len(canonical),
        independent_author_count=len(independent),
        uncertain_author_count=len(uncertain),
        independent_community_count=len(community_counts),
        community_concentration=_herfindahl(community_counts),
        source_concentration=_herfindahl(source_counts),
        effective_population=effective_population,
        propagation_adjusted_count=propagation_adjusted,
        window=_observation_window(canonical),
    )


def compute_trend(evidence: Sequence[EvidenceItem]) -> TrendSignal:
    """Compute the trend of the canonical evidence over its own window.

    The window is split in half by month, and the two halves are compared by
    RATE, not by count: a stream of one mention a month is steady whether
    the window holds two months or five, and a raw count would call it
    rising in every window with an odd number of months. A window of one
    month carries no direction, so the signal is unknown rather than flat.
    """
    canonical = [item for item in evidence if item.is_canonical]
    window = _observation_window(canonical)
    if window is None:
        return TrendSignal(direction=TrendDirection.unknown)

    months = sorted(
        {
            (item.published_at.date().year, item.published_at.date().month)
            for item in canonical
        }
    )
    if len(months) < 2:
        return TrendSignal(
            direction=TrendDirection.unknown,
            window=window,
            earlier_mentions=0,
            recent_mentions=len(canonical),
        )

    split = len(months) // 2
    earlier_months = set(months[:split])
    later_months = set(months[split:])
    earlier = sum(
        1
        for item in canonical
        if (item.published_at.date().year, item.published_at.date().month)
        in earlier_months
    )
    recent = len(canonical) - earlier
    earlier_rate = earlier / len(earlier_months)
    recent_rate = recent / len(later_months)
    if recent_rate > earlier_rate:
        direction = TrendDirection.rising
    elif recent_rate < earlier_rate:
        direction = TrendDirection.falling
    else:
        direction = TrendDirection.flat
    return TrendSignal(
        direction=direction,
        window=window,
        earlier_mentions=earlier,
        recent_mentions=recent,
    )

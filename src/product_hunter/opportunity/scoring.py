"""The two scorers: potential and confidence.

Potential answers whether solving the problem is attractive. Confidence answers
whether the collected evidence supports the thesis. The two never touch: each
scorer takes one input model, and the two models share no field, so a scorer
cannot read what it was not handed.

Neither scorer calls a model, reads a clock, or touches a database. Every
timestamp arrives as an argument, so two runs over the same inputs and the
same policy produce the same numbers.
"""

from __future__ import annotations

import math
from collections import Counter
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator

from product_hunter.extraction import Frequency
from product_hunter.opportunity.policy import ScoringPolicy
from product_hunter.opportunity.signals import (
    SEVERITY_MAX,
    CompetitionDirection,
    ConfidenceInputs,
    CounterEvidenceSummary,
    EvidenceItem,
    ExtractionAgreement,
    IndependenceSummary,
    MarketAssumptions,
    PotentialInputs,
    TrendSignal,
    WtpAssessment,
    WtpTier,
    compute_independence,
    compute_trend,
    wtp_tier_rank,
)

# The phrase a basis carries when nothing behind the dimension can be named.
# A dimension says so itself rather than borrowing an id from another one.
NO_EVIDENCE = "no evidence behind this dimension"

# The workflow frequencies, from the most frequent to the least. A tie in the
# mode is broken by this order.
FREQUENCY_ORDER: tuple[Frequency, ...] = (
    Frequency.daily,
    Frequency.weekly,
    Frequency.monthly,
    Frequency.occasional,
    Frequency.one_off,
    Frequency.unknown,
)


class ScoringError(Exception):
    """The inputs do not pass validation and cannot be scored."""

    reason_code = "scoring_error"

    def __init__(self, reason_code: str, detail: str = "") -> None:
        self.reason_code = reason_code
        self.detail = detail
        super().__init__(detail or reason_code)


class ComponentScore(BaseModel):
    """One component of a score: its factor, its weight, and its points."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    factor: float = Field(ge=0, le=1)
    weight: float = Field(ge=0, le=1)
    contribution: float = Field(ge=0, le=100)


class ScoreProvenance(BaseModel):
    """One row per dimension: what moved it and which records moved it.

    The ids are the records behind the dimension: pain observation ids on the
    potential side, evidence ids on the confidence side.
    """

    model_config = ConfigDict(extra="forbid")

    dimension: str = Field(min_length=1)
    factor: float = Field(ge=0, le=1)
    weight: float = Field(ge=0, le=1)
    contribution: float = Field(ge=0, le=100)
    basis: str = Field(min_length=1)
    evidence_ids: list[str] = Field(default_factory=list)


class CompetitionOverride(BaseModel):
    """A rule that narrowed the competition factor, and the value it set."""

    model_config = ConfigDict(extra="forbid")

    reason_code: str = Field(min_length=1)
    detail: str = Field(min_length=1)
    factor: float = Field(ge=0, le=1)


class NicheOverride(BaseModel):
    """The lift a small market was given, and what triggered it."""

    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=1)
    severity: int = Field(ge=0, le=SEVERITY_MAX)
    wtp_tier: WtpTier
    market_annual_usd: float = Field(ge=0)
    factor_before: float = Field(ge=0, le=1)
    factor_after: float = Field(ge=0, le=1)


class PotentialScore(BaseModel):
    """The potential number, the range the market band allows, and its parts."""

    model_config = ConfigDict(extra="forbid")

    score: float = Field(ge=0, le=100)
    low: float = Field(ge=0, le=100)
    high: float = Field(ge=0, le=100)
    components: list[ComponentScore] = Field(default_factory=list)
    provenance: list[ScoreProvenance] = Field(default_factory=list)
    competition_overrides: list[CompetitionOverride] = Field(default_factory=list)
    niche_override: NicheOverride | None = None

    @model_validator(mode="after")
    def _range_is_ordered(self) -> PotentialScore:
        if not self.low <= self.score <= self.high:
            raise ValueError(
                "the potential range is not ordered: "
                f"low {self.low}, score {self.score}, high {self.high}"
            )
        return self


class ConfidenceScore(BaseModel):
    """The confidence number, the range the identities allow, and its parts.

    The high end of the range is a bound on what the identities and the
    extractors are entitled to, not a score: a consumer must not read it as
    one when ranking two opportunities.
    """

    model_config = ConfigDict(extra="forbid")

    score: float = Field(ge=0, le=100)
    low: float = Field(ge=0, le=100)
    high: float = Field(ge=0, le=100)
    components: list[ComponentScore] = Field(default_factory=list)
    provenance: list[ScoreProvenance] = Field(default_factory=list)
    independence: IndependenceSummary
    concentration_penalty: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def _range_is_ordered(self) -> ConfidenceScore:
        if not self.low <= self.score <= self.high:
            raise ValueError(
                "the confidence range is not ordered: "
                f"low {self.low}, score {self.score}, high {self.high}"
            )
        return self


class OpportunityScore(BaseModel):
    """Both numbers, and the trend reported next to them."""

    model_config = ConfigDict(extra="forbid")

    potential: PotentialScore
    confidence: ConfidenceScore
    trend: TrendSignal


def _contribution(weight: float, factor: float) -> float:
    """Return the points a factor contributes to a 0-100 score."""
    return weight * factor * 100.0


def _row(
    dimension: str,
    factor: float,
    weight: float,
    basis: str,
    evidence_ids: list[str],
) -> ScoreProvenance:
    """Return one provenance row, with its basis and its ids in canonical order.

    The ids are sorted, so two runs over the same records in a different order
    store the same row, and a dimension with nothing behind it says so in
    its basis rather than borrowing an id from another dimension.
    """
    ids = sorted(evidence_ids)
    return ScoreProvenance(
        dimension=dimension,
        factor=factor,
        weight=weight,
        contribution=_contribution(weight, factor),
        basis=basis if ids else f"{basis}; {NO_EVIDENCE}",
        evidence_ids=ids,
    )


def _wtp_factor(wtp: WtpAssessment, policy: ScoringPolicy) -> float:
    """Return the willingness-to-pay factor, refusing a tier the signals do not support."""
    strongest = 0
    for signal in wtp.signals:
        cap = policy.wtp_factors.signal_caps[signal.kind]
        strongest = max(strongest, wtp_tier_rank(cap))
    declared = wtp_tier_rank(wtp.tier)
    if declared > strongest:
        raise ScoringError(
            "wtp_tier_exceeds_signals",
            f"the declared tier {wtp.tier.value} is above the strongest signal "
            "behind it",
        )
    return policy.wtp_factors.tiers[wtp.tier]


def _market_factor(annual_usd: float, policy: ScoringPolicy) -> float:
    """Return the market factor: a logarithmic map onto the policy's ceiling."""
    denominator = math.log10(1.0 + policy.market.ceiling_usd)
    if denominator <= 0:
        return 0.0
    value = math.log10(1.0 + annual_usd) / denominator
    return min(1.0, max(0.0, value))


def _mode_frequency(pain_observations) -> Frequency:
    """Return the modal workflow frequency, ties broken by FREQUENCY_ORDER."""
    if not pain_observations:
        return Frequency.unknown
    counts = Counter(observation.frequency for observation in pain_observations)
    # max returns the first of the maxima, and FREQUENCY_ORDER is the tie
    # break, so the most frequent frequency wins and the order settles ties.
    return max(FREQUENCY_ORDER, key=lambda frequency: counts.get(frequency, 0))


def _niche_override(
    market_factor: float,
    severity: int,
    wtp_tier: WtpTier,
    annual_usd: float,
    policy: ScoringPolicy,
) -> NicheOverride | None:
    """Return the lift a small market gets, or nothing when it does not apply."""
    niche = policy.market.niche
    if market_factor >= niche.factor_ceiling:
        return None
    if severity < niche.min_severity:
        return None
    if wtp_tier_rank(wtp_tier) < wtp_tier_rank(niche.min_wtp_tier):
        return None
    return NicheOverride(
        reason=(
            f"the market factor {market_factor:.4f} is below the policy ceiling "
            f"{niche.factor_ceiling} and the severity {severity} and the tier "
            f"{wtp_tier.value} are both strong"
        ),
        severity=severity,
        wtp_tier=wtp_tier,
        market_annual_usd=annual_usd,
        factor_before=market_factor,
        factor_after=niche.factor_floor,
    )


def _competition(
    inputs: PotentialInputs, policy: ScoringPolicy
) -> tuple[float, list[CompetitionOverride], list[str]]:
    """Return the competition factor, the overrides that narrowed it, and their notes."""
    competition = inputs.competition
    factor = policy.competition_factors[competition.direction]
    overrides: list[CompetitionOverride] = []
    notes: list[str] = []

    if not competition.search_ran:
        # An unexamined market is not a tailwind, so the factor is the
        # neutral one and the declared direction stays in the record.
        neutral = policy.competition_factors[CompetitionDirection.neutral]
        factor = neutral
        overrides.append(
            CompetitionOverride(
                reason_code="competitor_search_missing",
                detail=(
                    "no competitor search ran, so the competitive position is "
                    "unknown and the factor is the neutral one"
                ),
                factor=neutral,
            )
        )
        notes.append("no competitor search ran, so the factor was forced to the neutral value")

    if competition.search_ran and competition.competitors_found == 0:
        # An empty search result is evidence about the search, not proof that
        # the market is open. A search that never ran is not an empty result,
        # so the cap is not what applies there.
        cap = policy.competition_absent_factor_cap
        factor = min(factor, cap)
        overrides.append(
            CompetitionOverride(
                reason_code="no_competitors_found",
                detail=(
                    f"no competitor was found, so the factor may not exceed the "
                    f"cap of {cap}; it is {factor}"
                ),
                factor=factor,
            )
        )
        notes.append(
            f"no competitor was found, so the factor is {factor} under the cap "
            f"of {cap}"
        )

    return factor, overrides, notes


def _potential_factors(
    inputs: PotentialInputs,
    policy: ScoringPolicy,
    assumptions: MarketAssumptions,
    severity: int,
    niche_override: NicheOverride | None,
) -> tuple[dict[str, float], list[ScoreProvenance], list[CompetitionOverride]]:
    """Return the six potential factors, their provenance, and the overrides.

    The niche override is decided once, from the base assumption set, and the
    lift applies to every assumption set: the floor is a floor. The raw
    market factor grows with the annual opportunity, and the larger of it and
    a floor grows with it too, so the three runs stay ordered.
    """
    weights = policy.potential_weights
    factors: dict[str, float] = {}
    provenance: list[ScoreProvenance] = []

    factors["severity"] = severity / SEVERITY_MAX
    pain_ids = [observation.pain_id for observation in inputs.pain_observations]
    provenance.append(
        _row(
            "severity",
            factors["severity"],
            weights["severity"],
            f"the strongest of {len(inputs.pain_observations)} pain "
            f"observations is severity {severity} of {SEVERITY_MAX}",
            pain_ids,
        )
    )

    mode = _mode_frequency(inputs.pain_observations)
    factors["frequency"] = policy.frequency_factors[mode]
    mode_ids = [
        observation.pain_id
        for observation in inputs.pain_observations
        if observation.frequency is mode
    ]
    provenance.append(
        _row(
            "frequency",
            factors["frequency"],
            weights["frequency"],
            f"the modal workflow frequency is {mode.value}",
            mode_ids,
        )
    )

    wtp_factor = _wtp_factor(inputs.wtp, policy)
    factors["wtp"] = wtp_factor
    wtp_ids = [
        evidence_id
        for signal in inputs.wtp.signals
        for evidence_id in signal.evidence_ids
    ]
    provenance.append(
        _row(
            "wtp",
            wtp_factor,
            weights["wtp"],
            f"the declared tier is {inputs.wtp.tier.value}",
            wtp_ids,
        )
    )

    annual_usd = assumptions.annual_opportunity_usd
    raw_market_factor = _market_factor(annual_usd, policy)
    market_factor = raw_market_factor
    market_basis = f"the assumption set makes {annual_usd:.2f} USD a year"
    if niche_override is not None:
        market_factor = max(raw_market_factor, niche_override.factor_after)
        if market_factor > raw_market_factor:
            market_basis = (
                f"{market_basis}; the niche override lifted the market factor "
                f"to {market_factor}"
            )
    factors["market"] = market_factor
    provenance.append(
        _row(
            "market",
            market_factor,
            weights["market"],
            market_basis,
            [],
        )
    )

    competition_factor, competition_overrides, notes = _competition(inputs, policy)
    factors["competition"] = competition_factor
    provenance.append(
        _row(
            "competition",
            competition_factor,
            weights["competition"],
            "the declared direction is "
            f"{inputs.competition.direction.value}"
            + ("" if not notes else "; " + "; ".join(notes)),
            inputs.competition.evidence_ids,
        )
    )

    buildability = inputs.buildability
    buildability_factor = max(
        0.0,
        buildability.feasibility
        - policy.buildability.blocker_penalty * len(buildability.blockers),
    )
    factors["buildability"] = buildability_factor
    provenance.append(
        _row(
            "buildability",
            buildability_factor,
            weights["buildability"],
            f"the feasibility is {buildability.feasibility} less "
            f"{len(buildability.blockers)} blockers at "
            f"{policy.buildability.blocker_penalty} each",
            [],
        )
    )

    return factors, provenance, competition_overrides


def _weighted_score(factors: dict[str, float], weights: dict[str, float]) -> float:
    """Return the 0-100 score the factors and weights make."""
    return sum(weights[name] * factors[name] for name in weights) * 100.0


def _components(
    factors: dict[str, float], weights: dict[str, float]
) -> list[ComponentScore]:
    """Return one component row per dimension."""
    return [
        ComponentScore(
            name=name,
            factor=factors[name],
            weight=weights[name],
            contribution=_contribution(weights[name], factors[name]),
        )
        for name in weights
    ]


def score_potential(
    inputs: PotentialInputs, policy: ScoringPolicy
) -> PotentialScore:
    """Score how attractive solving the problem is.

    The range comes from the market estimate's own band: the potential is
    computed once with the low assumption set and once with the high one, and
    the other five components do not change between the runs. The niche
    override is decided from the base assumption set, because it is a
    property of the opportunity and not of an assumption set, and the lift it
    gives applies to all three runs, so the range cannot invert.
    """
    weights = policy.potential_weights
    severity = max(
        (observation.severity for observation in inputs.pain_observations),
        default=0,
    )
    base_annual = inputs.market.base.annual_opportunity_usd
    override = _niche_override(
        _market_factor(base_annual, policy),
        severity,
        inputs.wtp.tier,
        base_annual,
        policy,
    )
    base_factors, provenance, competition_overrides = _potential_factors(
        inputs, policy, inputs.market.base, severity, override
    )
    low_factors, _, _ = _potential_factors(
        inputs, policy, inputs.market.low, severity, override
    )
    high_factors, _, _ = _potential_factors(
        inputs, policy, inputs.market.high, severity, override
    )

    return PotentialScore(
        score=min(100.0, max(0.0, _weighted_score(base_factors, weights))),
        low=min(100.0, max(0.0, _weighted_score(low_factors, weights))),
        high=min(100.0, max(0.0, _weighted_score(high_factors, weights))),
        components=_components(base_factors, weights),
        provenance=provenance,
        competition_overrides=competition_overrides,
        niche_override=override,
    )


def _independent_authors(
    evidence: list[EvidenceItem], policy: ScoringPolicy
) -> dict[str, EvidenceItem]:
    """Return the representative item of every author that clears the threshold.

    The representative is the item with the strongest identity confidence,
    and the smallest evidence id among the items that tie on it, so the same
    records in another order name the same person.
    """
    strongest: dict[str, EvidenceItem] = {}
    for item in evidence:
        if not item.is_canonical:
            continue
        current = strongest.get(item.author_id)
        if current is None or (
            -item.identity_confidence,
            item.evidence_id,
        ) < (
            -current.identity_confidence,
            current.evidence_id,
        ):
            strongest[item.author_id] = item
    return {
        author: item
        for author, item in strongest.items()
        if item.identity_confidence >= policy.identity.min_confidence
    }


def _reliability_mean(
    evidence: list[EvidenceItem], policy: ScoringPolicy
) -> tuple[float, list[str]]:
    """Return the reliability-weighted mean over the evidence and the ids behind it.

    A propagated copy is not a canonical observation, so the confidence that
    it is a copy discounts its weight in the mix: a copy believed with
    ninety percent confidence of being a copy carries a tenth of the weight
    of an original observation.
    """
    ids: list[str] = []
    weights: list[float] = []
    weighted: list[float] = []
    for item in evidence:
        weight = 1.0 if item.is_canonical else 1.0 - item.propagation_confidence
        weights.append(weight)
        weighted.append(
            weight * policy.source_reliability_weights[item.source_reliability]
        )
        ids.append(item.evidence_id)
    if not weights:
        return 0.0, ids
    # The sums are exact, so the mix is a function of the items and not of
    # the order they arrived in.
    total = math.fsum(weights)
    accumulated = math.fsum(weighted)
    return accumulated / total, ids


def _persistence(
    independence: IndependenceSummary,
    evidence: list[EvidenceItem],
    policy: ScoringPolicy,
    as_of: datetime,
) -> tuple[float, list[str], str]:
    """Return the temporal persistence factor, the window's edge ids, and its basis.

    Evidence dated after the reference date is refused rather than scored: a
    future date is clock skew or a fabrication, and a negative age would
    otherwise earn full persistence credit. The comparison is at date
    granularity, so evidence recorded later on the same day is not an error.
    """
    window = independence.window
    if window is None:
        return 0.0, [], "no canonical evidence was supplied"

    reference_date = as_of.date()
    if window.last_seen > reference_date:
        raise ScoringError(
            "evidence_dated_in_the_future",
            f"the observation window ends on {window.last_seen.isoformat()}, "
            f"after the reference date {reference_date.isoformat()}",
        )

    canonical = [item for item in evidence if item.is_canonical]
    ids: list[str] = []
    if canonical:
        # The id breaks the tie when two items share a date, so the same
        # records in another order name the same edges.
        earliest = min(
            canonical, key=lambda item: (item.published_at, item.evidence_id)
        )
        latest = max(
            canonical, key=lambda item: (item.published_at, item.evidence_id)
        )
        ids = [earliest.evidence_id]
        if latest.evidence_id != earliest.evidence_id:
            ids.append(latest.evidence_id)

    span_days = (window.last_seen - window.first_seen).days
    basis = (
        f"the evidence spans {window.distinct_months} months and {span_days} days"
    )
    age_days = (reference_date - window.last_seen).days
    if age_days > policy.lifecycle.staleness_days:
        # The newest evidence is stale, so nothing about persistence holds.
        return 0.0, ids, (
            f"{basis}; the newest evidence is {age_days} days old, past the "
            f"{policy.lifecycle.staleness_days} day staleness window"
        )
    months = min(window.distinct_months / policy.confidence.target_months, 1.0)
    span = min(span_days / policy.confidence.target_span_days, 1.0)
    return min(months, span), ids, basis


def _counter_evidence_factor(
    summary: CounterEvidenceSummary, policy: ScoringPolicy
) -> tuple[float, int]:
    """Return the counter-evidence factor and the requirement it was judged by.

    The requirement is the caller's own number or the policy's floor,
    whichever is higher: a caller may raise the bar but not lower it. A
    check that found nothing contradicting cannot lower the score, and a
    check that never ran contributes nothing to it.
    """
    required = max(
        summary.families_required, policy.confidence.min_counter_evidence_families
    )
    if required <= 0:
        coverage = 0.0
    else:
        coverage = min(summary.families_covered / required, 1.0)
    if summary.contradictions_checked <= 0:
        contradiction_rate = 0.0
    else:
        contradiction_rate = (
            summary.contradictions_found / summary.contradictions_checked
        )
    return coverage * (1.0 - contradiction_rate), required


def _agreement_factor(
    agreement: ExtractionAgreement, policy: ScoringPolicy
) -> float:
    """Return the extraction agreement factor, discounted for a small sample.

    The discount counts the extractors, not the extractions: ten extractions
    from one model are not agreement across models. A record that names no
    extractor agrees with nothing, so it scores zero.
    """
    total = agreement.agreements + agreement.disagreements
    if total <= 0 or agreement.extractors <= 0:
        return 0.0
    rate = agreement.agreements / total
    discount = min(
        1.0, agreement.extractors / policy.confidence.agreement_min_extractors
    )
    return rate * discount


def _concentration_penalty(concentration: float, policy: ScoringPolicy) -> float:
    """Return the penalty the concentration of the evidence carries."""
    neutral = policy.concentration.neutral_index
    maximum = policy.concentration.max_penalty
    if concentration <= neutral:
        return 1.0
    penalty = maximum * (concentration - neutral) / (1.0 - neutral)
    return max(1.0 - maximum, 1.0 - penalty)


def score_confidence(
    inputs: ConfidenceInputs, policy: ScoringPolicy, *, as_of: datetime
) -> ConfidenceScore:
    """Score how well the collected evidence supports the thesis.

    The reference time arrives as an argument: the scorer never reads the
    clock, so the same evidence and the same policy always produce the same
    number.
    """
    if as_of.tzinfo is None or as_of.tzinfo.utcoffset(as_of) is None:
        raise ScoringError(
            "as_of_not_aware",
            "as_of must be timezone-aware: a naive timestamp cannot be "
            "compared with an aware one",
        )

    weights = policy.confidence_weights
    independence = compute_independence(inputs.evidence, policy)
    independent = _independent_authors(inputs.evidence, policy)

    people_factor = min(
        independence.effective_population / policy.confidence.target_independent_people,
        1.0,
    )
    community_factor = min(
        independence.independent_community_count / policy.confidence.target_communities,
        1.0,
    )
    reliability_factor, reliability_ids = _reliability_mean(inputs.evidence, policy)
    persistence_factor, persistence_ids, persistence_basis = _persistence(
        independence, inputs.evidence, policy, as_of
    )
    counter_factor, counter_required = _counter_evidence_factor(
        inputs.counter_evidence, policy
    )
    agreement_factor = _agreement_factor(inputs.extraction_agreement, policy)

    factors = {
        "independent_people": people_factor,
        "community_diversity": community_factor,
        "source_reliability": reliability_factor,
        "temporal_persistence": persistence_factor,
        "counter_evidence": counter_factor,
        "extraction_agreement": agreement_factor,
    }

    people_ids = [item.evidence_id for item in independent.values()]
    # The representative of a community is its smallest evidence id, so the
    # same community is named by the same record whatever order it came in.
    per_community: dict[str, list[str]] = {}
    for item in independent.values():
        per_community.setdefault(item.community_id, []).append(item.evidence_id)
    community_ids = [min(ids) for ids in per_community.values()]

    provenance = [
        _row(
            "independent_people",
            people_factor,
            weights["independent_people"],
            f"{independence.independent_author_count} independent people "
            f"with an effective population of "
            f"{independence.effective_population:.2f}",
            people_ids,
        ),
        _row(
            "community_diversity",
            community_factor,
            weights["community_diversity"],
            f"{independence.independent_community_count} distinct communities",
            community_ids,
        ),
        _row(
            "source_reliability",
            reliability_factor,
            weights["source_reliability"],
            f"the reliability-weighted mean of {len(inputs.evidence)} items "
            f"is {reliability_factor:.4f}",
            reliability_ids,
        ),
        _row(
            "temporal_persistence",
            persistence_factor,
            weights["temporal_persistence"],
            persistence_basis,
            persistence_ids,
        ),
        _row(
            "counter_evidence",
            counter_factor,
            weights["counter_evidence"],
            f"{inputs.counter_evidence.families_covered} of {counter_required} "
            "query families were covered",
            [],
        ),
        _row(
            "extraction_agreement",
            agreement_factor,
            weights["extraction_agreement"],
            f"{inputs.extraction_agreement.agreements} of "
            f"{inputs.extraction_agreement.agreements + inputs.extraction_agreement.disagreements}"
            " extractions agreed with the consensus",
            [],
        ),
    ]

    penalty = _concentration_penalty(independence.concentration, policy)
    score = _weighted_score(factors, weights) * penalty

    author_total = (
        independence.independent_author_count + independence.uncertain_author_count
    )
    uncertain_share = (
        independence.uncertain_author_count / author_total if author_total else 0.0
    )
    extractions = (
        inputs.extraction_agreement.agreements
        + inputs.extraction_agreement.disagreements
    )
    disagreement_rate = (
        inputs.extraction_agreement.disagreements / extractions if extractions else 0.0
    )
    margin = policy.confidence.range_max_fraction * 100.0 * (
        policy.confidence.uncertain_share_weight * uncertain_share
        + policy.confidence.disagreement_share_weight * disagreement_rate
    )

    return ConfidenceScore(
        score=min(100.0, max(0.0, score)),
        low=min(100.0, max(0.0, score - margin)),
        high=min(100.0, max(0.0, score + margin)),
        components=_components(factors, weights),
        provenance=provenance,
        independence=independence,
        concentration_penalty=penalty,
    )


def score_opportunity(
    potential_inputs: PotentialInputs,
    confidence_inputs: ConfidenceInputs,
    policy: ScoringPolicy,
    *,
    as_of: datetime,
) -> OpportunityScore:
    """Score an opportunity on both dimensions and report the trend beside them.

    The two scorers are called separately, each with its own inputs, so
    neither can read the other's terms.
    """
    return OpportunityScore(
        potential=score_potential(potential_inputs, policy),
        confidence=score_confidence(confidence_inputs, policy, as_of=as_of),
        trend=compute_trend(confidence_inputs.evidence),
    )

"""The two scorers: determinism, the isolation of the dimensions, and the rules.

Every check here is a pure function of the inputs it is handed: the same
inputs produce the same numbers, and neither scorer can read the terms of the
other dimension.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from product_hunter.extraction import Frequency
from product_hunter.opportunity import (
    BuildabilityAssessment,
    CompetitionDirection,
    CompetitiveAssessment,
    ConfidenceInputs,
    ConfidenceScore,
    CounterEvidenceSummary,
    EvidenceItem,
    ExtractionAgreement,
    MarketAssumptions,
    MarketEstimate,
    PainObservation,
    PolicyError,
    PotentialInputs,
    PotentialScore,
    ScoringError,
    SourceReliability,
    ThresholdUnit,
    TrendDirection,
    TrendSignal,
    WtpAssessment,
    WtpSignal,
    WtpSignalKind,
    WtpTier,
    compute_independence,
    compute_trend,
    load_scoring_policy,
    score_confidence,
    score_opportunity,
    score_potential,
    snapshot_id,
    validate_policy,
)
from product_hunter.config import repo_root

NOW = datetime(2026, 10, 21, tzinfo=timezone.utc)
POLICY_FILE = repo_root() / "config" / "scoring_policy.yaml"


def _monthly_stream(mentions: list[int]) -> list[EvidenceItem]:
    """One evidence item per mention, the counts given per month in order.

    The first count is the earliest month and the last is October 2026, so
    the stream never reaches past the reference date.
    """
    items: list[EvidenceItem] = []
    index = 0
    first = 10 - (len(mentions) - 1)
    for offset, count in enumerate(mentions):
        published = datetime(2026, first + offset, 15, tzinfo=timezone.utc)
        for _ in range(count):
            index += 1
            items.append(_evidence(index, published_at=published))
    return items


def _policy():
    """The shipped policy."""
    return load_scoring_policy()


def _market(multiplier: float = 1.0) -> MarketAssumptions:
    """One assumption set, scaled by the multiplier."""
    return MarketAssumptions(
        reachable_population=1_000_000.0 * multiplier,
        problem_incidence=0.2,
        adoption_rate=0.1,
        annual_price_usd=120.0,
    )


def _small_market(annual_usd: float) -> MarketAssumptions:
    """One assumption set that makes the given annual opportunity."""
    population = 500.0
    return MarketAssumptions(
        reachable_population=population,
        problem_incidence=1.0,
        adoption_rate=1.0,
        annual_price_usd=annual_usd / population,
    )


def _potential(
    *,
    severity: int = 4,
    frequency: Frequency = Frequency.weekly,
    tier: WtpTier = WtpTier.W2,
    signals: list[WtpSignal] | None = None,
    market: MarketEstimate | None = None,
    competition: CompetitiveAssessment | None = None,
    feasibility: float = 0.8,
    blockers: list[str] | None = None,
) -> PotentialInputs:
    """One set of potential inputs."""
    if signals is None:
        signals = [
            WtpSignal(kind=WtpSignalKind.stated_budget, evidence_ids=["ev_1"])
        ]
    if market is None:
        market = MarketEstimate(
            low=_market(0.5), base=_market(1.0), high=_market(2.0)
        )
    if competition is None:
        competition = CompetitiveAssessment(
            direction=CompetitionDirection.neutral,
            competitors_found=2,
            search_ran=True,
            evidence_ids=["ev_1"],
        )
    return PotentialInputs(
        pain_observations=[
            PainObservation(pain_id="pp_1", severity=severity, frequency=frequency)
        ],
        wtp=WtpAssessment(tier=tier, signals=signals),
        market=market,
        competition=competition,
        buildability=BuildabilityAssessment(
            feasibility=feasibility, blockers=blockers or []
        ),
    )


def _evidence(
    index: int,
    *,
    community: str = "com_1",
    author: str | None = None,
    source: str | None = None,
    identity_confidence: float = 0.9,
    days: int = 10,
    published_at: datetime | None = None,
    is_canonical: bool = True,
    propagation_confidence: float = 0.0,
    reliability: SourceReliability = SourceReliability.tier_b,
) -> EvidenceItem:
    """One evidence item.

    The propagation confidence is how strongly the item is believed to be a
    copy, so a canonical item carries none of it.
    """
    return EvidenceItem(
        evidence_id=f"ev_{index}",
        author_id=author or f"au_{index}",
        community_id=community,
        source_id=source or f"src_{index}",
        source_reliability=reliability,
        published_at=published_at or (NOW - timedelta(days=days)),
        identity_confidence=identity_confidence,
        is_canonical=is_canonical,
        propagation_confidence=propagation_confidence,
    )


def _confidence(
    evidence: list[EvidenceItem] | None = None,
    *,
    agreement: ExtractionAgreement | None = None,
    counter_evidence: CounterEvidenceSummary | None = None,
) -> ConfidenceInputs:
    """One set of confidence inputs."""
    return ConfidenceInputs(
        evidence=evidence if evidence is not None else [_evidence(1)],
        extraction_agreement=agreement
        or ExtractionAgreement(agreements=8, disagreements=2, extractors=3),
        counter_evidence=counter_evidence
        or CounterEvidenceSummary(
            search_ran=True,
            families_covered=4,
            families_required=4,
            contradictions_found=1,
            contradictions_checked=10,
        ),
    )


def _row(score, dimension: str):
    """The provenance row of one dimension of a score."""
    for row in score.provenance:
        if row.dimension == dimension:
            return row
    raise AssertionError(f"no provenance row for {dimension!r}")


def _factor(score, dimension: str) -> float:
    """The factor of one dimension of a score."""
    return _row(score, dimension).factor


# --- determinism ------------------------------------------------------------


def test_the_same_inputs_give_the_same_numbers_twice():
    policy = _policy()
    potential = _potential()
    confidence = _confidence([_evidence(index) for index in range(1, 6)])

    first_potential = score_potential(potential, policy)
    second_potential = score_potential(potential, policy)
    first_confidence = score_confidence(confidence, policy, as_of=NOW)
    second_confidence = score_confidence(confidence, policy, as_of=NOW)

    assert first_potential.model_dump() == second_potential.model_dump()
    assert first_confidence.model_dump() == second_confidence.model_dump()


def test_the_score_is_built_from_the_policy_weights():
    policy = _policy()
    score = score_potential(_potential(), policy)

    for row in score.components:
        weight = policy.potential_weights[row.name]
        assert row.weight == weight
        assert row.contribution == pytest.approx(weight * row.factor * 100.0)
    assert sum(row.contribution for row in score.components) == pytest.approx(score.score)


# --- the isolation of the two dimensions ------------------------------------


def test_the_two_input_models_share_no_field():
    potential_fields = set(PotentialInputs.model_fields)
    confidence_fields = set(ConfidenceInputs.model_fields)

    assert potential_fields & confidence_fields == set()
    assert potential_fields == {
        "pain_observations",
        "wtp",
        "market",
        "competition",
        "buildability",
    }
    assert confidence_fields == {
        "evidence",
        "extraction_agreement",
        "counter_evidence",
    }


def test_trend_is_a_field_of_neither_input_model():
    for model in (PotentialInputs, ConfidenceInputs):
        for name, field in model.model_fields.items():
            assert field.annotation is not TrendSignal
            assert "TrendSignal" not in str(field.annotation), name


def test_changing_a_confidence_input_leaves_the_potential_bit_identical():
    policy = _policy()
    potential = _potential()

    first = score_potential(potential, policy)
    thin = score_confidence(
        _confidence([_evidence(1)]), policy, as_of=NOW
    )
    thick = score_confidence(
        _confidence(
            [_evidence(index) for index in range(1, 12)],
            agreement=ExtractionAgreement(agreements=1, disagreements=9, extractors=3),
            counter_evidence=CounterEvidenceSummary(),
        ),
        policy,
        as_of=NOW,
    )
    second = score_potential(potential, policy)

    assert thin.score != thick.score
    assert first.model_dump() == second.model_dump()


def test_changing_a_potential_input_leaves_the_confidence_bit_identical():
    policy = _policy()
    confidence = _confidence([_evidence(index) for index in range(1, 6)])

    first = score_confidence(confidence, policy, as_of=NOW)
    score_confidence(
        _confidence([_evidence(1)]), policy, as_of=NOW
    )
    second = score_confidence(confidence, policy, as_of=NOW)

    assert first.model_dump() == second.model_dump()


def test_the_trend_is_neither_the_potential_nor_the_confidence():
    policy = _policy()
    potential = _potential()
    # Two mentions in the earlier month and one in the later one, then the
    # other way round: the same evidence, dated differently.
    falling = [
        _evidence(1, days=60),
        _evidence(2, days=60),
        _evidence(3, days=1),
    ]
    rising = [
        _evidence(1, days=60),
        _evidence(2, days=1),
        _evidence(3, days=1),
    ]

    down = score_opportunity(potential, _confidence(falling), policy, as_of=NOW)
    up = score_opportunity(potential, _confidence(rising), policy, as_of=NOW)

    assert down.trend.direction is TrendDirection.falling
    assert up.trend.direction is TrendDirection.rising
    assert down.potential.model_dump() == up.potential.model_dump()
    assert down.confidence.score == up.confidence.score


@pytest.mark.parametrize("months", [2, 3, 4, 5])
def test_a_steady_stream_reads_flat_over_any_number_of_months(months):
    trend = compute_trend(_monthly_stream([1] * months))

    assert trend.direction is TrendDirection.flat


def test_a_stream_that_accelerates_reads_rising():
    trend = compute_trend(_monthly_stream([1, 2, 3]))

    assert trend.direction is TrendDirection.rising


def test_a_stream_that_slows_reads_falling():
    trend = compute_trend(_monthly_stream([3, 2, 1]))

    assert trend.direction is TrendDirection.falling


def test_a_potential_score_with_an_inverted_range_is_refused():
    with pytest.raises(ValidationError):
        PotentialScore(score=80.0, low=10.0, high=60.0)


def test_a_confidence_score_with_an_inverted_range_is_refused():
    policy = _policy()
    independence = compute_independence([], policy)

    with pytest.raises(ValidationError):
        ConfidenceScore(
            score=80.0,
            low=10.0,
            high=60.0,
            independence=independence,
        )


def test_a_naive_reference_time_is_refused():
    policy = _policy()

    with pytest.raises(ScoringError) as failure:
        score_confidence(_confidence(), policy, as_of=NOW.replace(tzinfo=None))

    assert failure.value.reason_code == "as_of_not_aware"


def test_evidence_dated_after_the_reference_date_is_refused():
    policy = _policy()
    future = [_evidence(1, published_at=NOW + timedelta(days=1))]

    with pytest.raises(ScoringError) as failure:
        score_confidence(_confidence(future), policy, as_of=NOW)

    assert failure.value.reason_code == "evidence_dated_in_the_future"
    assert "2026-10-22" in failure.value.detail
    assert "2026-10-21" in failure.value.detail


def test_the_refusal_reaches_the_caller_of_the_whole_score():
    policy = _policy()
    future = [_evidence(1, published_at=NOW + timedelta(days=1))]

    with pytest.raises(ScoringError) as failure:
        score_opportunity(_potential(), _confidence(future), policy, as_of=NOW)

    assert failure.value.reason_code == "evidence_dated_in_the_future"


def test_evidence_dated_on_the_same_day_as_the_reference_date_is_not_an_error():
    policy = _policy()
    same_day = [_evidence(1, published_at=NOW + timedelta(hours=18))]

    score = score_confidence(_confidence(same_day), policy, as_of=NOW)

    assert score.independence.window is not None
    assert score.independence.window.last_seen == NOW.date()


# --- missing and weak evidence ----------------------------------------------


def test_an_empty_evidence_list_scores_a_low_confidence_and_does_not_crash():
    policy = _policy()
    score = score_confidence(
        ConfidenceInputs(), policy, as_of=NOW
    )

    assert score.score < 10.0
    assert score.independence.raw_mention_count == 0
    assert score.independence.independent_author_count == 0
    assert score.independence.effective_population == 0.0
    assert score.independence.window is None


def test_an_author_below_the_identity_threshold_is_uncertain_and_not_independent():
    policy = _policy()
    evidence = [
        _evidence(index, author=f"au_{index}", identity_confidence=0.5)
        for index in range(1, 4)
    ]

    summary = compute_independence(evidence, policy)

    assert summary.independent_author_count == 0
    assert summary.uncertain_author_count == 3
    assert summary.effective_population == 0.0


def test_a_propagated_copy_is_not_a_canonical_observation():
    policy = _policy()
    evidence = [
        _evidence(1),
        _evidence(
            2,
            is_canonical=False,
            propagation_confidence=0.4,
        ),
    ]

    summary = compute_independence(evidence, policy)

    assert summary.raw_mention_count == 2
    assert summary.canonical_count == 1
    assert summary.propagation_adjusted_count == pytest.approx(1.6)


def test_a_mention_count_is_not_an_independent_population():
    policy = _policy()
    few = [
        _evidence(index, author="au_1", community="com_1")
        for index in range(1, 3)
    ]
    many = [
        _evidence(index, author="au_1", community="com_1")
        for index in range(1, 8)
    ]

    few_summary = compute_independence(few, policy)
    many_summary = compute_independence(many, policy)

    assert few_summary.raw_mention_count == 2
    assert many_summary.raw_mention_count == 7
    assert many_summary.independent_author_count == 1
    assert many_summary.independent_author_count == few_summary.independent_author_count
    # The author counts once, at their strongest confidence, so five more
    # mentions do not make five more people.
    assert many_summary.effective_population == pytest.approx(0.9)
    assert many_summary.effective_population == few_summary.effective_population

    few_score = score_confidence(_confidence(few), policy, as_of=NOW)
    many_score = score_confidence(_confidence(many), policy, as_of=NOW)

    assert _factor(many_score, "independent_people") == _factor(
        few_score, "independent_people"
    )


def test_a_copy_believed_with_ninety_percent_confidence_carries_a_tenth_of_the_weight():
    policy = _policy()
    evidence = [
        _evidence(1, reliability=SourceReliability.tier_d),
        _evidence(
            2,
            reliability=SourceReliability.tier_a,
            is_canonical=False,
            propagation_confidence=0.9,
        ),
    ]

    score = score_confidence(_confidence(evidence), policy, as_of=NOW)
    tier_d = policy.source_reliability_weights[SourceReliability.tier_d]
    tier_a = policy.source_reliability_weights[SourceReliability.tier_a]

    # The copy carries a tenth of the weight of the original observation, so
    # the mix is weighted 1.0 against 0.1 and not 1.0 against 1.0.
    assert _factor(score, "source_reliability") == pytest.approx(
        (1.0 * tier_d + 0.1 * tier_a) / 1.1
    )
    assert _factor(score, "source_reliability") < (tier_d + tier_a) / 2


# --- the concentration penalty ----------------------------------------------


def test_evidence_from_one_community_is_penalised():
    policy = _policy()
    evidence = [
        _evidence(index, community="com_1", author=f"au_{index}")
        for index in range(1, 7)
    ]

    score = score_confidence(_confidence(evidence), policy, as_of=NOW)

    assert score.concentration_penalty < 1.0
    assert score.independence.community_concentration == pytest.approx(1.0)


def test_evidence_spread_over_three_communities_is_not_penalised():
    policy = _policy()
    evidence = [
        _evidence(
            index,
            community=f"com_{(index - 1) % 3 + 1}",
            author=f"au_{index}",
        )
        for index in range(1, 7)
    ]

    score = score_confidence(_confidence(evidence), policy, as_of=NOW)

    assert score.concentration_penalty == pytest.approx(1.0)
    assert score.independence.community_concentration == pytest.approx(1 / 3)


def test_the_concentrated_evidence_scores_lower_than_the_spread_evidence():
    policy = _policy()
    concentrated = [
        _evidence(index, community="com_1", author=f"au_{index}")
        for index in range(1, 7)
    ]
    spread = [
        _evidence(
            index,
            community=f"com_{(index - 1) % 3 + 1}",
            author=f"au_{index}",
        )
        for index in range(1, 7)
    ]

    tight = score_confidence(_confidence(concentrated), policy, as_of=NOW)
    wide = score_confidence(_confidence(spread), policy, as_of=NOW)

    assert tight.score < wide.score


# --- willingness to pay -----------------------------------------------------


@pytest.mark.parametrize(
    ("tier", "factor", "signal"),
    [
        (WtpTier.W0, 0.0, None),
        (WtpTier.W1, 0.25, WtpSignalKind.complaint),
        (WtpTier.W2, 0.5, WtpSignalKind.purchase_intent),
        (WtpTier.W3, 0.75, WtpSignalKind.stated_budget),
        (WtpTier.W4, 1.0, WtpSignalKind.paid_customer),
    ],
)
def test_every_wtp_tier_maps_to_its_factor(tier, factor, signal):
    policy = _policy()
    signals = [] if signal is None else [WtpSignal(kind=signal, evidence_ids=["ev_1"])]

    score = score_potential(
        _potential(tier=tier, signals=signals), policy
    )

    assert policy.wtp_factors.tiers[tier] == factor
    assert _factor(score, "wtp") == pytest.approx(factor)


def test_a_tier_above_the_signals_behind_it_is_refused():
    policy = _policy()
    potential = _potential(
        tier=WtpTier.W4, signals=[WtpSignal(kind=WtpSignalKind.complaint, evidence_ids=["ev_1"])]
    )

    with pytest.raises(ScoringError) as failure:
        score_potential(potential, policy)

    assert failure.value.reason_code == "wtp_tier_exceeds_signals"


def test_a_tier_the_signals_support_is_scored():
    policy = _policy()
    potential = _potential(
        tier=WtpTier.W1, signals=[WtpSignal(kind=WtpSignalKind.complaint, evidence_ids=["ev_1"])]
    )

    score = score_potential(potential, policy)

    assert _factor(score, "wtp") == pytest.approx(0.25)


def test_a_wtp_signal_with_no_evidence_is_refused():
    with pytest.raises(ValidationError):
        WtpSignal(kind=WtpSignalKind.paid_customer, evidence_ids=[])

    with pytest.raises(ValidationError):
        WtpSignal(kind=WtpSignalKind.paid_customer)


# --- the niche override -----------------------------------------------------


def test_a_small_market_with_strong_severity_and_wtp_is_lifted():
    policy = _policy()
    market = MarketEstimate(
        low=_small_market(250.0),
        base=_small_market(500.0),
        high=_small_market(1000.0),
    )
    potential = _potential(
        severity=4,
        tier=WtpTier.W3,
        signals=[WtpSignal(kind=WtpSignalKind.stated_budget, evidence_ids=["ev_1"])],
        market=market,
    )

    score = score_potential(potential, policy)
    override = score.niche_override

    assert override is not None
    assert override.severity == 4
    assert override.wtp_tier is WtpTier.W3
    assert override.market_annual_usd == pytest.approx(500.0)
    assert override.factor_before < policy.market.niche.factor_ceiling
    assert override.factor_after == policy.market.niche.factor_floor
    assert _factor(score, "market") == pytest.approx(policy.market.niche.factor_floor)
    assert "severity 4" in override.reason


def test_a_small_market_without_a_strong_severity_is_not_lifted():
    policy = _policy()
    market = MarketEstimate(
        low=_small_market(250.0),
        base=_small_market(500.0),
        high=_small_market(1000.0),
    )
    potential = _potential(
        severity=2,
        tier=WtpTier.W3,
        signals=[WtpSignal(kind=WtpSignalKind.stated_budget, evidence_ids=["ev_1"])],
        market=market,
    )

    score = score_potential(potential, policy)

    assert score.niche_override is None
    assert _factor(score, "market") < policy.market.niche.factor_ceiling


def test_the_override_does_not_move_the_market_estimate():
    policy = _policy()
    market = MarketEstimate(
        low=_small_market(250.0),
        base=_small_market(500.0),
        high=_small_market(1000.0),
    )
    potential = _potential(
        severity=4,
        tier=WtpTier.W3,
        signals=[WtpSignal(kind=WtpSignalKind.stated_budget, evidence_ids=["ev_1"])],
        market=market,
    )

    score = score_potential(potential, policy)

    assert potential.market.base.annual_opportunity_usd == pytest.approx(500.0)
    assert score.niche_override is not None


def test_the_range_cannot_invert_when_the_niche_override_applies():
    policy = _policy()
    # The base factor is below the policy ceiling, so the override lifts it.
    # The high factor is above the ceiling but below the floor, so without the
    # lift it would land under the base and invert the range.
    market = MarketEstimate(
        low=_small_market(100.0),
        base=_small_market(250.0),
        high=_small_market(10000.0),
    )
    potential = _potential(
        severity=4,
        tier=WtpTier.W3,
        signals=[WtpSignal(kind=WtpSignalKind.stated_budget, evidence_ids=["ev_1"])],
        market=market,
    )

    score = score_potential(potential, policy)

    assert score.niche_override is not None
    assert score.low <= score.score <= score.high
    assert _factor(score, "market") == pytest.approx(policy.market.niche.factor_floor)


# --- the plausible range ----------------------------------------------------


def test_the_potential_range_follows_the_market_assumption_sets():
    policy = _policy()
    market = MarketEstimate(
        low=_market(0.25), base=_market(1.0), high=_market(4.0)
    )

    score = score_potential(_potential(market=market), policy)

    assert score.low < score.score < score.high
    low_only = score_potential(
        _potential(market=MarketEstimate(low=market.low, base=market.low, high=market.high)),
        policy,
    )
    high_only = score_potential(
        _potential(market=MarketEstimate(low=market.low, base=market.high, high=market.high)),
        policy,
    )
    assert score.low == pytest.approx(low_only.score)
    assert score.high == pytest.approx(high_only.score)


# --- the agreement between extractors ---------------------------------------


def test_the_agreement_discount_counts_the_extractors_not_the_extractions():
    policy = _policy()
    one_extractor = score_confidence(
        _confidence(
            agreement=ExtractionAgreement(
                agreements=9, disagreements=1, extractors=1
            )
        ),
        policy,
        as_of=NOW,
    )
    three_extractors = score_confidence(
        _confidence(
            agreement=ExtractionAgreement(
                agreements=9, disagreements=1, extractors=3
            )
        ),
        policy,
        as_of=NOW,
    )

    assert _factor(one_extractor, "extraction_agreement") == pytest.approx(0.9 / 3)
    assert _factor(three_extractors, "extraction_agreement") == pytest.approx(0.9)


def test_an_agreement_record_without_an_extractor_scores_zero():
    policy = _policy()

    score = score_confidence(
        _confidence(
            agreement=ExtractionAgreement(
                agreements=9, disagreements=1, extractors=0
            )
        ),
        policy,
        as_of=NOW,
    )

    assert _factor(score, "extraction_agreement") == 0.0


# --- the confidence range ----------------------------------------------------


def test_the_range_is_wide_when_the_identities_are_uncertain():
    policy = _policy()
    evidence = [
        _evidence(index, identity_confidence=0.9 if index <= 3 else 0.5)
        for index in range(1, 7)
    ]

    score = score_confidence(_confidence(evidence), policy, as_of=NOW)

    # The margin is a fraction of the 0-100 scale, not of the score.
    assert score.high - score.low > 1.0
    assert score.high - score.low == pytest.approx(17.5)


def test_the_range_is_narrow_when_nothing_is_uncertain():
    policy = _policy()
    evidence = [_evidence(index, identity_confidence=0.9) for index in range(1, 7)]

    score = score_confidence(
        _confidence(
            evidence,
            agreement=ExtractionAgreement(
                agreements=10, disagreements=0, extractors=3
            ),
        ),
        policy,
        as_of=NOW,
    )

    assert score.high - score.low == pytest.approx(0.0)


# --- the counter-evidence floor ---------------------------------------------


def test_one_covered_family_cannot_earn_full_counter_evidence_coverage():
    policy = _policy()
    counter = CounterEvidenceSummary(
        search_ran=True,
        families_covered=1,
        families_required=1,
        contradictions_found=0,
        contradictions_checked=1,
    )

    score = score_confidence(_confidence(counter_evidence=counter), policy, as_of=NOW)

    assert policy.confidence.min_counter_evidence_families == 3
    assert _factor(score, "counter_evidence") == pytest.approx(1 / 3)
    assert "1 of 3" in _row(score, "counter_evidence").basis


def test_a_caller_may_raise_the_counter_evidence_bar():
    policy = _policy()
    counter = CounterEvidenceSummary(
        search_ran=True,
        families_covered=4,
        families_required=6,
        contradictions_found=0,
        contradictions_checked=4,
    )

    score = score_confidence(_confidence(counter_evidence=counter), policy, as_of=NOW)

    assert _factor(score, "counter_evidence") == pytest.approx(4 / 6)
    assert "4 of 6" in _row(score, "counter_evidence").basis


# --- the rules with no test --------------------------------------------------


def test_stale_evidence_zeroes_the_persistence_factor():
    policy = _policy()
    evidence = [_evidence(index, days=index * 10) for index in range(1, 4)]

    fresh = score_confidence(_confidence(evidence), policy, as_of=NOW)
    stale = score_confidence(
        _confidence(evidence),
        policy,
        as_of=NOW + timedelta(days=policy.lifecycle.staleness_days + 1),
    )

    assert _factor(fresh, "temporal_persistence") > 0.0
    assert _factor(stale, "temporal_persistence") == 0.0


def test_the_community_diversity_factor_counts_the_distinct_communities():
    policy = _policy()
    one = score_confidence(
        _confidence([_evidence(index, community="com_1") for index in range(1, 4)]),
        policy,
        as_of=NOW,
    )
    three = score_confidence(
        _confidence(
            [_evidence(index, community=f"com_{index}") for index in range(1, 4)]
        ),
        policy,
        as_of=NOW,
    )

    assert _factor(one, "community_diversity") == pytest.approx(
        1 / policy.confidence.target_communities
    )
    assert _factor(three, "community_diversity") == pytest.approx(1.0)


def test_the_source_concentration_drives_the_penalty():
    policy = _policy()
    one_source = score_confidence(
        _confidence(
            [
                _evidence(index, community=f"com_{index}", source="src_1")
                for index in range(1, 7)
            ]
        ),
        policy,
        as_of=NOW,
    )
    many = score_confidence(
        _confidence(
            [
                _evidence(index, community=f"com_{index}", source=f"src_{index}")
                for index in range(1, 7)
            ]
        ),
        policy,
        as_of=NOW,
    )

    assert one_source.independence.source_concentration == pytest.approx(1.0)
    assert many.independence.source_concentration < policy.concentration.neutral_index
    assert one_source.concentration_penalty < 1.0
    assert many.concentration_penalty == pytest.approx(1.0)


def test_the_zero_competitor_override_text_agrees_with_the_factor():
    policy = _policy()
    competition = CompetitiveAssessment(
        direction=CompetitionDirection.headwind,
        competitors_found=0,
        search_ran=True,
    )

    score = score_potential(_potential(competition=competition), policy)
    override = score.competition_overrides[0]

    assert override.reason_code == "no_competitors_found"
    assert override.factor == pytest.approx(
        policy.competition_factors[CompetitionDirection.headwind]
    )
    assert str(override.factor) in override.detail
    assert str(policy.competition_absent_factor_cap) in override.detail


# --- the competition rules --------------------------------------------------


def test_the_competition_direction_is_required():
    with pytest.raises(ValidationError):
        CompetitiveAssessment(competitors_found=1, search_ran=True)


def test_a_competitor_count_needs_a_search_that_ran():
    with pytest.raises(ValidationError):
        CompetitiveAssessment(
            direction=CompetitionDirection.tailwind,
            competitors_found=2,
            search_ran=False,
        )


def test_a_competitor_search_that_did_not_run_forces_the_neutral_factor():
    policy = _policy()
    competition = CompetitiveAssessment(
        direction=CompetitionDirection.tailwind,
        competitors_found=0,
        search_ran=False,
    )

    score = score_potential(_potential(competition=competition), policy)

    neutral = policy.competition_factors[CompetitionDirection.neutral]
    assert _factor(score, "competition") == pytest.approx(neutral)
    assert [
        override.reason_code for override in score.competition_overrides
    ] == ["competitor_search_missing"]


def test_a_zero_competitor_assessment_is_capped():
    policy = _policy()
    competition = CompetitiveAssessment(
        direction=CompetitionDirection.tailwind,
        competitors_found=0,
        search_ran=True,
    )

    score = score_potential(_potential(competition=competition), policy)

    cap = policy.competition_absent_factor_cap
    assert _factor(score, "competition") == pytest.approx(cap)
    assert cap < policy.competition_factors[CompetitionDirection.tailwind]
    assert [
        override.reason_code for override in score.competition_overrides
    ] == ["no_competitors_found"]


def test_the_declared_direction_survives_the_absence_rules():
    policy = _policy()
    competition = CompetitiveAssessment(
        direction=CompetitionDirection.headwind,
        competitors_found=0,
        search_ran=False,
    )

    score = score_potential(_potential(competition=competition), policy)

    assert _factor(score, "competition") == pytest.approx(
        policy.competition_factors[CompetitionDirection.neutral]
    )
    assert "the declared direction is headwind" in _row(score, "competition").basis


# --- the model independence -------------------------------------------------


def test_the_scoring_package_calls_no_model_and_no_clock():
    import product_hunter.opportunity as package

    forbidden = (
        "product_hunter.llm",
        "httpx",
        "urllib.request",
        "sqlite3",
        "product_hunter.db",
        "random.",
        "datetime.now(",
        "utcnow(",
        "time.time(",
    )
    for path in sorted(Path(package.__file__).parent.glob("*.py")):
        source = path.read_text(encoding="utf-8")
        for needle in forbidden:
            assert needle not in source, f"{path.name} mentions {needle}"


def test_the_numbers_do_not_change_when_the_model_layer_is_broken(monkeypatch):
    from product_hunter.llm import opencode

    def explode(self, request):
        raise AssertionError("the scorer called a model")

    monkeypatch.setattr(opencode.OpenCodeGenerateProvider, "generate", explode)
    policy = _policy()
    potential = _potential()
    confidence = _confidence([_evidence(index) for index in range(1, 6)])

    first = score_potential(potential, policy)
    second = score_potential(potential, policy)
    first_confidence = score_confidence(confidence, policy, as_of=NOW)
    second_confidence = score_confidence(confidence, policy, as_of=NOW)

    assert first.model_dump() == second.model_dump()
    assert first_confidence.model_dump() == second_confidence.model_dump()


# --- the policy loader ------------------------------------------------------


def _policy_dict() -> dict:
    """The shipped policy file, parsed."""
    return yaml.safe_load(POLICY_FILE.read_text(encoding="utf-8"))


def _write_policy(tmp_path, data: dict) -> Path:
    """Write a policy file and return its path."""
    path = tmp_path / "scoring_policy.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path


def test_the_shipped_policy_loads_and_validates():
    policy = load_scoring_policy()

    assert policy.policy_version == "opportunity-v1"
    assert validate_policy(policy) == []
    assert sum(policy.potential_weights.values()) == pytest.approx(1.0)
    assert sum(policy.confidence_weights.values()) == pytest.approx(1.0)


def test_the_loader_refuses_a_missing_policy_file(tmp_path):
    missing = tmp_path / "not_there.yaml"

    with pytest.raises(PolicyError) as failure:
        load_scoring_policy(missing)

    assert not isinstance(failure.value, OSError)
    assert any("not_there.yaml" in error for error in failure.value.errors)


def test_the_loader_refuses_weights_that_do_not_sum_to_one(tmp_path):
    data = _policy_dict()
    data["potential_weights"]["severity"] = 0.5

    with pytest.raises(PolicyError) as failure:
        load_scoring_policy(_write_policy(tmp_path, data))

    assert any("potential_weights" in error for error in failure.value.errors)


def test_the_loader_refuses_a_factor_table_that_names_an_unknown_member(tmp_path):
    data = _policy_dict()
    data["frequency_factors"]["twice_daily"] = 0.9

    with pytest.raises(PolicyError):
        load_scoring_policy(_write_policy(tmp_path, data))


def test_the_loader_refuses_a_negative_threshold(tmp_path):
    data = _policy_dict()
    data["lifecycle"]["min_independent_people"] = -1

    with pytest.raises(PolicyError):
        load_scoring_policy(_write_policy(tmp_path, data))


def test_the_loader_refuses_a_factor_table_that_is_missing_a_member(tmp_path):
    data = _policy_dict()
    del data["frequency_factors"]["unknown"]

    with pytest.raises(PolicyError) as failure:
        load_scoring_policy(_write_policy(tmp_path, data))

    assert any("frequency_factors" in error for error in failure.value.errors)


def test_the_loader_refuses_a_counter_evidence_floor_of_zero(tmp_path):
    data = _policy_dict()
    data["confidence"]["min_counter_evidence_families"] = 0

    with pytest.raises(PolicyError):
        load_scoring_policy(_write_policy(tmp_path, data))


def test_the_loader_accepts_a_percent_threshold_the_sample_can_reach(tmp_path):
    data = _policy_dict()
    commercial = data["experiment"]["templates"]["W0"]["commercial"]
    commercial["sample_size"] = 30
    commercial["threshold"] = {
        "comparator": "at_least",
        "value": 50,
        "unit": "percent",
    }

    policy = load_scoring_policy(_write_policy(tmp_path, data))

    threshold = policy.experiment.templates[WtpTier.W0].commercial.threshold
    assert threshold.value == 50
    assert threshold.unit is ThresholdUnit.percent


def test_a_market_assumption_must_be_finite():
    for value in (float("inf"), float("-inf"), float("nan")):
        with pytest.raises(ValidationError):
            MarketAssumptions(
                reachable_population=value,
                problem_incidence=0.2,
                adoption_rate=0.1,
                annual_price_usd=120.0,
            )


def test_a_policy_that_changed_without_a_version_bump_moves_the_snapshot_id():
    policy = _policy()
    weights = dict(policy.potential_weights)
    weights["severity"] = 0.25
    weights["buildability"] = 0.05
    other = policy.model_copy(update={"potential_weights": weights})
    assert other.policy_version == policy.policy_version

    first = snapshot_id(
        _potential(), _confidence(), policy, as_of=NOW, input_snapshot_ids=[]
    )
    second = snapshot_id(
        _potential(), _confidence(), other, as_of=NOW, input_snapshot_ids=[]
    )

    assert first != second


def test_a_policy_that_differs_only_in_a_comment_makes_the_same_snapshot_id(tmp_path):
    policy = _policy()
    commented = tmp_path / "scoring_policy.yaml"
    commented.write_text(
        "# a comment that changes nothing\n" + yaml.safe_dump(_policy_dict()),
        encoding="utf-8",
    )
    other = load_scoring_policy(commented)

    assert other.policy_version == policy.policy_version
    assert snapshot_id(
        _potential(), _confidence(), policy, as_of=NOW, input_snapshot_ids=[]
    ) == snapshot_id(
        _potential(), _confidence(), other, as_of=NOW, input_snapshot_ids=[]
    )

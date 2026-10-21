"""The stored record: the snapshot id, the provenance, and the experiments.

The snapshot id is a content hash, so the same inputs under the same policy
produce the same id, and a changed input produces a different one.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from product_hunter.extraction import Frequency
from product_hunter.opportunity import (
    BuildabilityAssessment,
    Comparator,
    CompetitionDirection,
    CompetitiveAssessment,
    Conclusion,
    ConfidenceInputs,
    CounterEvidenceSummary,
    EvidenceItem,
    ExtractionAgreement,
    MarketAssumptions,
    MarketEstimate,
    ObservedResult,
    PainObservation,
    PotentialInputs,
    ScoreSnapshot,
    SourceReliability,
    Threshold,
    ThresholdUnit,
    ValidationExperiment,
    WtpAssessment,
    WtpSignal,
    WtpSignalKind,
    WtpTier,
    build_snapshot,
    conclude,
    load_scoring_policy,
    recommend_experiment,
    snapshot_id,
    snapshot_payload,
)

NOW = datetime(2026, 10, 21, tzinfo=timezone.utc)


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


def _potential() -> PotentialInputs:
    """One set of potential inputs."""
    return PotentialInputs(
        pain_observations=[
            PainObservation(pain_id="pp_1", severity=4, frequency=Frequency.weekly)
        ],
        wtp=WtpAssessment(
            tier=WtpTier.W2,
            signals=[
                WtpSignal(kind=WtpSignalKind.stated_budget, evidence_ids=["ev_1"])
            ],
        ),
        market=MarketEstimate(
            low=_market(0.5), base=_market(1.0), high=_market(2.0)
        ),
        competition=CompetitiveAssessment(
            direction=CompetitionDirection.neutral,
            competitors_found=2,
            search_ran=True,
            evidence_ids=["ev_1"],
        ),
        buildability=BuildabilityAssessment(feasibility=0.8, blockers=[]),
    )


def _evidence(
    index: int,
    community: str = "com_1",
    published_at: datetime | None = None,
) -> EvidenceItem:
    """One evidence item."""
    return EvidenceItem(
        evidence_id=f"ev_{index}",
        author_id=f"au_{index}",
        community_id=community,
        source_id=f"src_{index}",
        source_reliability=SourceReliability.tier_b,
        published_at=published_at or (NOW - timedelta(days=index * 20)),
        identity_confidence=0.9,
    )


def _confidence(
    count: int = 6, evidence: list[EvidenceItem] | None = None
) -> ConfidenceInputs:
    """One set of confidence inputs."""
    return ConfidenceInputs(
        evidence=evidence
        if evidence is not None
        else [
            _evidence(index, community=f"com_{(index - 1) % 3 + 1}")
            for index in range(1, count + 1)
        ],
        extraction_agreement=ExtractionAgreement(
            agreements=8, disagreements=2, extractors=3
        ),
        counter_evidence=CounterEvidenceSummary(
            search_ran=True,
            families_covered=4,
            families_required=4,
            contradictions_found=1,
            contradictions_checked=10,
        ),
    )


def _potential_with(
    *,
    blockers: list[str],
    signals: list[WtpSignal],
    competition_evidence_ids: list[str],
) -> PotentialInputs:
    """Potential inputs whose order-free lists the test controls."""
    return PotentialInputs(
        pain_observations=[
            PainObservation(pain_id="pp_1", severity=4, frequency=Frequency.weekly)
        ],
        wtp=WtpAssessment(tier=WtpTier.W2, signals=signals),
        market=MarketEstimate(
            low=_market(0.5), base=_market(1.0), high=_market(2.0)
        ),
        competition=CompetitiveAssessment(
            direction=CompetitionDirection.neutral,
            competitors_found=2,
            search_ran=True,
            evidence_ids=competition_evidence_ids,
        ),
        buildability=BuildabilityAssessment(feasibility=0.7, blockers=blockers),
    )


def _result(value: float, sample_size: int = 30) -> ObservedResult:
    """One observed result, recorded."""
    return ObservedResult(
        value=value,
        sample_size=sample_size,
        taken_at=NOW,
        taken_by="a reviewer",
        evidence_ids=["ev_1"],
    )


def _experiment(**overrides) -> ValidationExperiment:
    """One validation experiment."""
    data = {
        "experiment_id": "exp_1",
        "hypothesis": "the people who report this pain will pay for a tool",
        "method": "sell the tool to 30 reporters and count the buyers",
        "success_threshold": Threshold(
            comparator=Comparator.at_least, value=5, unit=ThresholdUnit.count
        ),
    }
    data.update(overrides)
    return ValidationExperiment(**data)


# --- the snapshot id --------------------------------------------------------


def test_the_same_inputs_make_the_same_snapshot_id():
    policy = _policy()
    potential = _potential()
    confidence = _confidence()

    first = build_snapshot(potential, confidence, policy, as_of=NOW)
    second = build_snapshot(potential, confidence, policy, as_of=NOW)

    assert first.snapshot_id == second.snapshot_id
    assert first.model_dump() == second.model_dump()


def test_a_different_input_makes_a_different_snapshot_id():
    policy = _policy()
    potential = _potential()
    confidence = _confidence()

    first = snapshot_id(
        potential, confidence, policy, as_of=NOW, input_snapshot_ids=[]
    )
    second = snapshot_id(
        potential, _confidence(7), policy, as_of=NOW, input_snapshot_ids=[]
    )

    assert first != second


def test_the_snapshot_id_does_not_depend_on_the_order_of_the_evidence():
    policy = _policy()
    potential = _potential()
    evidence = [_evidence(index) for index in range(1, 5)]

    first = snapshot_id(
        potential,
        ConfidenceInputs(evidence=evidence),
        policy,
        as_of=NOW,
        input_snapshot_ids=[],
    )
    second = snapshot_id(
        potential,
        ConfidenceInputs(evidence=list(reversed(evidence))),
        policy,
        as_of=NOW,
        input_snapshot_ids=[],
    )

    assert first == second


def test_the_snapshot_id_carries_the_policy_version():
    policy = _policy()
    other = policy.model_copy(update={"policy_version": "opportunity-v2"})

    assert snapshot_id(
        _potential(), _confidence(), policy, as_of=NOW, input_snapshot_ids=[]
    ) != snapshot_id(
        _potential(), _confidence(), other, as_of=NOW, input_snapshot_ids=[]
    )


def test_the_snapshot_payload_carries_no_redundant_top_level_version():
    policy = _policy()

    payload = snapshot_payload(
        _potential(), _confidence(), policy, as_of=NOW, input_snapshot_ids=[]
    )

    assert "policy_version" not in payload
    assert payload["policy"]["policy_version"] == policy.policy_version


def test_a_different_reference_time_makes_a_different_snapshot_id():
    policy = _policy()
    potential = _potential()
    confidence = _confidence()

    now = build_snapshot(potential, confidence, policy, as_of=NOW)
    later = build_snapshot(
        potential,
        confidence,
        policy,
        as_of=NOW + timedelta(days=200),
    )

    # The reference time moves the score, so the id has to move with it.
    assert now.confidence != later.confidence
    assert now.snapshot_id != later.snapshot_id


def test_a_different_set_of_input_snapshot_ids_makes_a_different_snapshot_id():
    policy = _policy()
    potential = _potential()
    confidence = _confidence()

    first = build_snapshot(
        potential, confidence, policy, as_of=NOW, input_snapshot_ids=["in_1"]
    )
    second = build_snapshot(
        potential, confidence, policy, as_of=NOW, input_snapshot_ids=["in_2"]
    )

    assert first.snapshot_id != second.snapshot_id


def test_the_order_of_the_input_snapshot_ids_does_not_reach_the_record():
    policy = _policy()
    potential = _potential()
    confidence = _confidence()

    first = build_snapshot(
        potential,
        confidence,
        policy,
        as_of=NOW,
        input_snapshot_ids=["in_2", "in_1"],
    )
    second = build_snapshot(
        potential,
        confidence,
        policy,
        as_of=NOW,
        input_snapshot_ids=["in_1", "in_2"],
    )

    assert first.snapshot_id == second.snapshot_id
    assert first.model_dump() == second.model_dump()


def test_a_reference_time_in_another_offset_is_the_same_instant():
    policy = _policy()
    potential = _potential()
    confidence = _confidence()
    elsewhere = NOW.astimezone(timezone(timedelta(hours=5)))

    first = build_snapshot(potential, confidence, policy, as_of=NOW)
    second = build_snapshot(potential, confidence, policy, as_of=elsewhere)

    assert first.snapshot_id == second.snapshot_id
    assert first.model_dump() == second.model_dump()


# --- the contents of the snapshot -------------------------------------------


def test_the_snapshot_carries_both_numbers_and_both_ranges():
    policy = _policy()

    snapshot = build_snapshot(
        _potential(), _confidence(), policy, as_of=NOW, input_snapshot_ids=["in_1"]
    )

    assert 0.0 <= snapshot.potential <= 100.0
    assert 0.0 <= snapshot.confidence <= 100.0
    assert snapshot.potential_low <= snapshot.potential <= snapshot.potential_high
    assert snapshot.confidence_low <= snapshot.confidence <= snapshot.confidence_high
    assert snapshot.input_snapshot_ids == ["in_1"]
    assert snapshot.policy_version == policy.policy_version


def test_the_snapshot_carries_the_independence_summary_and_the_trend():
    policy = _policy()

    snapshot = build_snapshot(_potential(), _confidence(), policy, as_of=NOW)

    assert snapshot.independence.independent_author_count == 6
    assert snapshot.independence.uncertain_author_count == 0
    assert snapshot.trend.window is not None
    assert snapshot.trend.direction is not None


def test_the_snapshot_carries_the_wtp_tier_the_market_and_the_direction():
    policy = _policy()
    potential = _potential()

    snapshot = build_snapshot(potential, _confidence(), policy, as_of=NOW)

    assert snapshot.wtp_tier is WtpTier.W2
    assert snapshot.competition_direction is CompetitionDirection.neutral
    assert snapshot.market_estimate == potential.market
    assert snapshot.niche_override is None


def test_a_snapshot_with_an_inverted_range_is_refused():
    policy = _policy()
    snapshot = build_snapshot(_potential(), _confidence(), policy, as_of=NOW)
    payload = snapshot.model_dump()
    payload["potential_high"] = payload["potential_low"] - 1.0

    with pytest.raises(ValidationError):
        ScoreSnapshot.model_validate(payload)


def test_the_snapshot_id_survives_a_reordering_of_the_order_free_lists():
    policy = _policy()
    first = _potential_with(
        blockers=["a blocker", "another blocker"],
        signals=[
            WtpSignal(
                kind=WtpSignalKind.stated_budget,
                detail="one",
                evidence_ids=["ev_2", "ev_1"],
            ),
            WtpSignal(
                kind=WtpSignalKind.complaint, detail="two", evidence_ids=["ev_3"]
            ),
        ],
        competition_evidence_ids=["ev_1", "ev_2"],
    )
    second = _potential_with(
        blockers=["another blocker", "a blocker"],
        signals=[
            WtpSignal(
                kind=WtpSignalKind.complaint, detail="two", evidence_ids=["ev_3"]
            ),
            WtpSignal(
                kind=WtpSignalKind.stated_budget,
                detail="one",
                evidence_ids=["ev_1", "ev_2"],
            ),
        ],
        competition_evidence_ids=["ev_2", "ev_1"],
    )

    assert snapshot_id(
        first, _confidence(), policy, as_of=NOW, input_snapshot_ids=[]
    ) == snapshot_id(
        second, _confidence(), policy, as_of=NOW, input_snapshot_ids=[]
    )
    # The reordering is semantically inert, so the number does not move.
    assert build_snapshot(
        first, _confidence(), policy, as_of=NOW
    ).potential == build_snapshot(
        second, _confidence(), policy, as_of=NOW
    ).potential


def test_changing_one_of_the_order_free_lists_changes_the_snapshot_id():
    policy = _policy()
    first = _potential_with(
        blockers=["a blocker", "another blocker"],
        signals=[
            WtpSignal(
                kind=WtpSignalKind.stated_budget,
                detail="one",
                evidence_ids=["ev_1"],
            )
        ],
        competition_evidence_ids=["ev_1"],
    )
    other_blockers = _potential_with(
        blockers=["a blocker", "a third blocker"],
        signals=[
            WtpSignal(
                kind=WtpSignalKind.stated_budget,
                detail="one",
                evidence_ids=["ev_1"],
            )
        ],
        competition_evidence_ids=["ev_1"],
    )
    other_signal = _potential_with(
        blockers=["a blocker", "another blocker"],
        signals=[
            WtpSignal(
                kind=WtpSignalKind.stated_budget,
                detail="one",
                evidence_ids=["ev_2"],
            )
        ],
        competition_evidence_ids=["ev_1"],
    )
    other_competition = _potential_with(
        blockers=["a blocker", "another blocker"],
        signals=[
            WtpSignal(
                kind=WtpSignalKind.stated_budget,
                detail="one",
                evidence_ids=["ev_1"],
            )
        ],
        competition_evidence_ids=["ev_2"],
    )

    baseline = snapshot_id(
        first, _confidence(), policy, as_of=NOW, input_snapshot_ids=[]
    )
    assert snapshot_id(
        other_blockers, _confidence(), policy, as_of=NOW, input_snapshot_ids=[]
    ) != baseline
    assert snapshot_id(
        other_signal, _confidence(), policy, as_of=NOW, input_snapshot_ids=[]
    ) != baseline
    assert snapshot_id(
        other_competition, _confidence(), policy, as_of=NOW, input_snapshot_ids=[]
    ) != baseline


def test_two_orderings_of_the_same_inputs_store_the_same_snapshot():
    policy = _policy()
    first = _potential_with(
        blockers=["a blocker", "another blocker"],
        signals=[
            WtpSignal(
                kind=WtpSignalKind.stated_budget,
                detail="one",
                evidence_ids=["ev_2", "ev_1"],
            ),
            WtpSignal(
                kind=WtpSignalKind.complaint, detail="two", evidence_ids=["ev_3"]
            ),
        ],
        competition_evidence_ids=["ev_1", "ev_2"],
    )
    second = _potential_with(
        blockers=["another blocker", "a blocker"],
        signals=[
            WtpSignal(
                kind=WtpSignalKind.complaint, detail="two", evidence_ids=["ev_3"]
            ),
            WtpSignal(
                kind=WtpSignalKind.stated_budget,
                detail="one",
                evidence_ids=["ev_1", "ev_2"],
            ),
        ],
        competition_evidence_ids=["ev_2", "ev_1"],
    )
    evidence = [_evidence(index) for index in range(1, 7)]

    one = build_snapshot(
        first, _confidence(evidence=evidence), policy, as_of=NOW
    )
    two = build_snapshot(
        second,
        _confidence(evidence=list(reversed(evidence))),
        policy,
        as_of=NOW,
    )

    assert one.snapshot_id == two.snapshot_id
    # The record is the same too, not only the id that names it.
    assert one.model_dump() == two.model_dump()


def test_two_orderings_of_evidence_that_shares_a_date_store_the_same_snapshot():
    policy = _policy()
    evidence = [
        _evidence(index, published_at=NOW - timedelta(days=5))
        for index in range(1, 7)
    ]
    potential = _potential()

    one = build_snapshot(potential, _confidence(evidence=evidence), policy, as_of=NOW)
    two = build_snapshot(
        potential,
        _confidence(evidence=list(reversed(evidence))),
        policy,
        as_of=NOW,
    )

    assert one.snapshot_id == two.snapshot_id
    assert one.model_dump() == two.model_dump()


def test_the_snapshot_carries_a_provenance_row_for_every_dimension():
    policy = _policy()

    snapshot = build_snapshot(_potential(), _confidence(), policy, as_of=NOW)
    dimensions = [row.dimension for row in snapshot.provenance]

    assert dimensions == [
        "severity",
        "frequency",
        "wtp",
        "market",
        "competition",
        "buildability",
        "independent_people",
        "community_diversity",
        "source_reliability",
        "temporal_persistence",
        "counter_evidence",
        "extraction_agreement",
    ]
    assert len(snapshot.components) == len(dimensions)


def test_every_provenance_row_carries_the_factor_the_weight_and_the_contribution():
    policy = _policy()

    snapshot = build_snapshot(_potential(), _confidence(), policy, as_of=NOW)

    for row in snapshot.provenance:
        assert row.basis
        assert 0.0 <= row.factor <= 1.0
        assert 0.0 <= row.weight <= 1.0
        assert row.contribution == pytest.approx(row.weight * row.factor * 100.0)


def test_a_dimension_without_evidence_says_so_in_its_basis():
    policy = _policy()

    snapshot = build_snapshot(_potential(), _confidence(), policy, as_of=NOW)
    rows = {row.dimension: row for row in snapshot.provenance}

    for dimension in ("market", "buildability", "counter_evidence"):
        assert rows[dimension].evidence_ids == []
        assert "no evidence behind this dimension" in rows[dimension].basis


def test_the_provenance_names_the_records_that_moved_a_dimension():
    policy = _policy()

    snapshot = build_snapshot(_potential(), _confidence(), policy, as_of=NOW)
    rows = {row.dimension: row for row in snapshot.provenance}

    assert rows["severity"].evidence_ids == ["pp_1"]
    assert rows["independent_people"].evidence_ids == [
        f"ev_{index}" for index in range(1, 7)
    ]
    assert rows["source_reliability"].evidence_ids == [
        f"ev_{index}" for index in range(1, 7)
    ]


# --- the experiments --------------------------------------------------------


def test_an_experiment_with_no_observed_result_is_pending():
    experiment = _experiment()

    assert experiment.observed_result is None
    assert conclude(experiment, _policy()) is Conclusion.pending


@pytest.mark.parametrize(
    ("comparator", "threshold", "value", "expected"),
    [
        (Comparator.at_least, 5.0, 5.0, Conclusion.supported),
        (Comparator.at_least, 5.0, 4.9, Conclusion.refuted),
        (Comparator.at_most, 5.0, 5.0, Conclusion.supported),
        (Comparator.at_most, 5.0, 5.1, Conclusion.refuted),
        (Comparator.above, 5.0, 5.1, Conclusion.supported),
        (Comparator.above, 5.0, 5.0, Conclusion.refuted),
        (Comparator.below, 5.0, 4.9, Conclusion.supported),
        (Comparator.below, 5.0, 5.0, Conclusion.refuted),
    ],
)
def test_the_conclusion_follows_the_comparison(
    comparator, threshold, value, expected
):
    experiment = _experiment(
        success_threshold=Threshold(
            comparator=comparator, value=threshold, unit=ThresholdUnit.count
        ),
        observed_result=_result(value),
    )

    assert conclude(experiment, _policy()) is expected


def test_every_shipped_template_proposes_a_reachable_threshold():
    policy = load_scoring_policy()
    minimum = policy.experiment.min_sample_size

    for tier, templates in policy.experiment.templates.items():
        for name, template in (
            ("validation", templates.validation),
            ("commercial", templates.commercial),
        ):
            assert template.sample_size >= minimum, (
                f"the {name} template for tier {tier.value} proposes a sample "
                f"below the policy minimum"
            )
            threshold = template.threshold
            if (
                threshold.comparator is Comparator.at_least
                and threshold.unit is not ThresholdUnit.money_usd
            ):
                assert threshold.value <= template.sample_size, (
                    f"the {name} template for tier {tier.value} settles on "
                    f"{threshold.value} {threshold.unit.value}, which its own "
                    "sample cannot reach"
                )


def test_a_result_under_the_minimum_sample_size_is_inconclusive():
    policy = _policy()
    experiment = _experiment(observed_result=_result(99.0, sample_size=1))

    assert conclude(experiment, policy) is Conclusion.inconclusive


def test_a_result_under_the_minimum_sample_size_is_inconclusive_whatever_it_says():
    policy = _policy()
    under = _experiment(
        success_threshold=Threshold(
            comparator=Comparator.at_least, value=5, unit=ThresholdUnit.count
        ),
        observed_result=_result(99.0, sample_size=policy.experiment.min_sample_size - 1),
    )
    at_the_minimum = _experiment(
        success_threshold=Threshold(
            comparator=Comparator.at_least, value=5, unit=ThresholdUnit.count
        ),
        observed_result=_result(99.0, sample_size=policy.experiment.min_sample_size),
    )

    assert conclude(under, policy) is Conclusion.inconclusive
    assert conclude(at_the_minimum, policy) is Conclusion.supported


def test_an_observed_result_must_record_who_took_it_and_the_evidence():
    with pytest.raises(ValidationError):
        ObservedResult(
            value=5.0,
            sample_size=30,
            taken_at=NOW,
            taken_by="",
            evidence_ids=["ev_1"],
        )
    with pytest.raises(ValidationError):
        ObservedResult(
            value=5.0,
            sample_size=30,
            taken_at=NOW,
            taken_by="a reviewer",
            evidence_ids=[],
        )
    with pytest.raises(ValidationError):
        ObservedResult(
            value=5.0,
            sample_size=30,
            taken_at=NOW.replace(tzinfo=None),
            taken_by="a reviewer",
            evidence_ids=["ev_1"],
        )


@pytest.mark.parametrize("value", [float("inf"), float("-inf"), float("nan")])
def test_an_observed_result_must_be_finite(value):
    with pytest.raises(ValidationError):
        ObservedResult(
            value=value,
            sample_size=30,
            taken_at=NOW,
            taken_by="a reviewer",
            evidence_ids=["ev_1"],
        )


@pytest.mark.parametrize("value", [float("inf"), float("-inf"), float("nan")])
def test_a_threshold_value_must_be_finite(value):
    with pytest.raises(ValidationError):
        Threshold(
            comparator=Comparator.at_least,
            value=value,
            unit=ThresholdUnit.count,
        )


def test_a_recommendation_never_carries_an_observed_result():
    policy = _policy()

    recommendation = recommend_experiment(
        "opp_1",
        wtp_tier=WtpTier.W3,
        confidence=80.0,
        independent_people=20,
        policy=policy,
    )

    assert recommendation.observed_result is None
    assert recommendation.hypothesis
    assert recommendation.method
    assert recommendation.success_threshold.value >= 0


def test_the_recommendation_follows_the_tier():
    policy = _policy()

    low = recommend_experiment(
        "opp_1",
        wtp_tier=WtpTier.W0,
        confidence=80.0,
        independent_people=20,
        policy=policy,
    )
    high = recommend_experiment(
        "opp_1",
        wtp_tier=WtpTier.W4,
        confidence=80.0,
        independent_people=20,
        policy=policy,
    )

    assert low.hypothesis != high.hypothesis
    assert low.experiment_id != high.experiment_id


def test_a_thin_evidence_base_gets_the_validation_recommendation():
    policy = _policy()

    thin = recommend_experiment(
        "opp_1",
        wtp_tier=WtpTier.W3,
        confidence=10.0,
        independent_people=1,
        policy=policy,
    )
    thick = recommend_experiment(
        "opp_1",
        wtp_tier=WtpTier.W3,
        confidence=90.0,
        independent_people=20,
        policy=policy,
    )

    assert thin.hypothesis == policy.experiment.templates[WtpTier.W3].validation.hypothesis
    assert (
        thick.hypothesis
        == policy.experiment.templates[WtpTier.W3].commercial.hypothesis
    )


def test_changing_the_template_moves_the_experiment_id():
    policy = load_scoring_policy()
    templates = dict(policy.experiment.templates)
    commercial = templates[WtpTier.W3].commercial
    templates[WtpTier.W3] = templates[WtpTier.W3].model_copy(
        update={
            "commercial": commercial.model_copy(
                update={"method": "Ask 30 teams a different question."}
            )
        }
    )
    other = policy.model_copy(
        update={
            "experiment": policy.experiment.model_copy(
                update={"templates": templates}
            )
        }
    )

    first = recommend_experiment(
        "opp_1",
        wtp_tier=WtpTier.W3,
        confidence=80.0,
        independent_people=20,
        policy=policy,
    )
    second = recommend_experiment(
        "opp_1",
        wtp_tier=WtpTier.W3,
        confidence=80.0,
        independent_people=20,
        policy=other,
    )

    assert first.method != second.method
    assert first.experiment_id != second.experiment_id

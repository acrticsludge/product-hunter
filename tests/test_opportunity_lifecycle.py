"""The state machine: the legal edges, the illegal ones, and the requirements.

The machine never reads the clock, so every verdict here is reached from a
context the test hands it. The promotion gate reads the engine's own
confidence score, so a test that wants a promotion has to make the engine
compute one.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from product_hunter.opportunity import (
    ALLOWED_TRANSITIONS,
    ConfidenceInputs,
    ConfidenceScore,
    CounterEvidenceSummary,
    EvidenceItem,
    ExtractionAgreement,
    OpportunityState,
    SourceReliability,
    TransitionContext,
    TransitionRefused,
    apply_transition,
    evaluate_transition,
    load_scoring_policy,
    score_confidence,
)

NOW = datetime(2026, 10, 21, tzinfo=timezone.utc)

LEGAL_EDGES = [
    (current, target)
    for current in OpportunityState
    for target in sorted(ALLOWED_TRANSITIONS[current], key=lambda state: state.value)
]

ILLEGAL_EDGES = [
    (current, target)
    for current in OpportunityState
    for target in OpportunityState
    if target not in ALLOWED_TRANSITIONS[current]
]


def _policy():
    """The shipped policy."""
    return load_scoring_policy()


def _confidence_score(
    count: int = 12,
    *,
    identity_confidence: float = 0.9,
    agreements: int = 9,
    disagreements: int = 1,
    extractors: int = 3,
    counter_evidence_searched: bool = True,
    newest_evidence_at: datetime | None = None,
    spacing_days: int = 10,
    as_of: datetime = NOW,
) -> ConfidenceScore:
    """A confidence score the engine computed, never a number the test asserts."""
    newest = newest_evidence_at or (NOW - timedelta(days=10))
    evidence = [
        EvidenceItem(
            evidence_id=f"ev_{index}",
            author_id=f"au_{index}",
            community_id=f"com_{(index - 1) % 3 + 1}",
            source_id=f"src_{index}",
            source_reliability=SourceReliability.tier_a,
            published_at=newest - timedelta(days=(index - 1) * spacing_days),
            identity_confidence=identity_confidence,
        )
        for index in range(1, count + 1)
    ]
    counter_evidence = (
        CounterEvidenceSummary(
            search_ran=True,
            families_covered=4,
            families_required=4,
            contradictions_found=0,
            contradictions_checked=4,
        )
        if counter_evidence_searched
        else CounterEvidenceSummary()
    )
    return score_confidence(
        ConfidenceInputs(
            evidence=evidence,
            extraction_agreement=ExtractionAgreement(
                agreements=agreements,
                disagreements=disagreements,
                extractors=extractors,
            ),
            counter_evidence=counter_evidence,
        ),
        _policy(),
        as_of=as_of,
    )


def _context(**overrides) -> TransitionContext:
    """One transition context that satisfies every requirement."""
    data = {
        "as_of": NOW,
        "confidence_score": _confidence_score(),
        "counter_evidence_searched": True,
        "human_decision": "a reviewer read it and approved it",
        "human_marked_stale": True,
        "newest_evidence_at": NOW - timedelta(days=200),
        "new_evidence_count": 3,
        "reason": "the new evidence answers the old objection",
    }
    data.update(overrides)
    return TransitionContext(**data)


@pytest.mark.parametrize(("current", "target"), LEGAL_EDGES)
def test_every_legal_edge_is_allowed(current, target):
    decision = evaluate_transition(current, target, _context(), _policy())

    assert decision.allowed is True
    assert decision.reason_code == "allowed"
    assert decision.from_state is current
    assert decision.to_state is target


@pytest.mark.parametrize(("current", "target"), ILLEGAL_EDGES)
def test_every_edge_outside_the_table_is_refused(current, target):
    decision = evaluate_transition(current, target, _context(), _policy())

    assert decision.allowed is False
    assert decision.reason_code == "illegal_transition"


def test_dead_is_terminal():
    policy = _policy()

    for target in OpportunityState:
        decision = evaluate_transition(
            OpportunityState.DEAD, target, _context(), policy
        )
        assert decision.allowed is False
        assert decision.reason_code == "illegal_transition"


@pytest.mark.parametrize(
    ("current", "target", "overrides", "reason_code"),
    [
        (
            OpportunityState.VERIFYING,
            OpportunityState.PROMISING,
            {"confidence_score": _confidence_score(identity_confidence=0.5)},
            "insufficient_independent_people",
        ),
        (
            OpportunityState.VERIFYING,
            OpportunityState.PROMISING,
            {"confidence_score": None},
            "confidence_score_missing",
        ),
        (
            OpportunityState.PROMISING,
            OpportunityState.HUMAN_REVIEW,
            {"counter_evidence_searched": False},
            "counter_evidence_missing",
        ),
        (
            OpportunityState.HUMAN_REVIEW,
            OpportunityState.VALIDATED,
            {"human_decision": None},
            "human_decision_missing",
        ),
        (
            OpportunityState.CANDIDATE,
            OpportunityState.REJECTED,
            {"reason": ""},
            "reason_missing",
        ),
        (
            OpportunityState.VERIFYING,
            OpportunityState.ALREADY_SOLVED,
            {"reason": ""},
            "reason_missing",
        ),
        (
            OpportunityState.VERIFYING,
            OpportunityState.INSUFFICIENT_EVIDENCE,
            {"reason": ""},
            "reason_missing",
        ),
        (
            OpportunityState.VALIDATED,
            OpportunityState.DEAD,
            {"reason": ""},
            "reason_missing",
        ),
        (
            OpportunityState.REJECTED,
            OpportunityState.RESURRECTED,
            {"new_evidence_count": 0},
            "no_new_evidence",
        ),
        (
            OpportunityState.REJECTED,
            OpportunityState.RESURRECTED,
            {"reason": ""},
            "reason_missing",
        ),
        (
            OpportunityState.VERIFYING,
            OpportunityState.STALE,
            {"newest_evidence_at": NOW - timedelta(days=5), "human_marked_stale": False},
            "evidence_not_stale",
        ),
    ],
)
def test_a_legal_edge_with_an_unmet_requirement_is_refused(
    current, target, overrides, reason_code
):
    decision = evaluate_transition(
        current, target, _context(**overrides), _policy()
    )

    assert decision.allowed is False
    assert decision.reason_code == reason_code
    assert decision.detail


def test_a_promotion_needs_the_policy_minimum_of_independent_people():
    policy = _policy()
    score = _confidence_score(count=policy.lifecycle.min_independent_people - 1)
    context = _context(confidence_score=score)

    decision = evaluate_transition(
        OpportunityState.VERIFYING, OpportunityState.PROMISING, context, policy
    )

    assert score.independence.independent_author_count < (
        policy.lifecycle.min_independent_people
    )
    assert decision.reason_code == "insufficient_independent_people"


def test_a_promotion_needs_the_policy_minimum_confidence():
    policy = _policy()
    score = _confidence_score(
        count=policy.lifecycle.min_independent_people,
        identity_confidence=policy.identity.min_confidence,
        agreements=0,
        disagreements=0,
        extractors=0,
        counter_evidence_searched=False,
        as_of=NOW + timedelta(days=policy.lifecycle.staleness_days + 1),
    )
    context = _context(confidence_score=score)

    decision = evaluate_transition(
        OpportunityState.VERIFYING, OpportunityState.PROMISING, context, policy
    )

    # The engine found enough people, but the evidence behind them is too
    # thin for the confidence the policy demands.
    assert score.independence.independent_author_count >= (
        policy.lifecycle.min_independent_people
    )
    assert score.score < policy.lifecycle.min_confidence
    assert decision.reason_code == "insufficient_confidence"


def test_a_promotion_without_a_score_is_refused():
    decision = evaluate_transition(
        OpportunityState.VERIFYING,
        OpportunityState.PROMISING,
        _context(confidence_score=None),
        _policy(),
    )

    assert decision.allowed is False
    assert decision.reason_code == "confidence_score_missing"
    assert "engine" in decision.detail


def test_the_promotion_gate_ignores_uncertain_authors():
    policy = _policy()
    # Ten authors at an identity confidence below the policy threshold: the
    # engine counts no independent people at all.
    score = _confidence_score(count=10, identity_confidence=0.5)
    context = _context(confidence_score=score)

    decision = evaluate_transition(
        OpportunityState.VERIFYING, OpportunityState.PROMISING, context, policy
    )

    assert score.independence.independent_author_count == 0
    assert score.independence.uncertain_author_count == 10
    assert decision.reason_code == "insufficient_independent_people"


def test_no_caller_supplied_number_can_override_the_promotion_gate():
    with pytest.raises(ValidationError):
        TransitionContext(
            as_of=NOW,
            independent_people=100,
            confidence=99.0,
        )


def test_evidence_older_than_the_staleness_window_makes_the_opportunity_stale():
    policy = _policy()
    # The engine's own evidence ended long ago, so the opportunity is stale
    # even though the context asserts a fresh date.
    score = _confidence_score(
        newest_evidence_at=NOW - timedelta(days=policy.lifecycle.staleness_days + 1)
    )
    context = _context(
        confidence_score=score,
        newest_evidence_at=NOW - timedelta(days=1),
        human_marked_stale=False,
    )

    decision = evaluate_transition(
        OpportunityState.VERIFYING, OpportunityState.STALE, context, policy
    )

    assert score.independence.window is not None
    assert score.independence.window.last_seen == (
        NOW - timedelta(days=policy.lifecycle.staleness_days + 1)
    ).date()
    assert decision.allowed is True
    assert decision.reason_code == "allowed"


def test_a_fresh_engine_window_cannot_be_forced_stale_by_an_asserted_date():
    policy = _policy()
    score = _confidence_score(newest_evidence_at=NOW - timedelta(days=1))
    context = _context(
        confidence_score=score,
        newest_evidence_at=NOW - timedelta(days=300),
        human_marked_stale=False,
    )

    decision = evaluate_transition(
        OpportunityState.VERIFYING, OpportunityState.STALE, context, policy
    )

    assert score.independence.window is not None
    assert score.independence.window.last_seen == (NOW - timedelta(days=1)).date()
    assert decision.allowed is False
    assert decision.reason_code == "evidence_not_stale"


def test_a_context_with_no_score_still_uses_the_asserted_date():
    policy = _policy()
    old = _context(
        confidence_score=None,
        newest_evidence_at=NOW - timedelta(days=policy.lifecycle.staleness_days + 1),
        human_marked_stale=False,
    )
    fresh = _context(
        confidence_score=None,
        newest_evidence_at=NOW - timedelta(days=1),
        human_marked_stale=False,
    )

    assert (
        evaluate_transition(
            OpportunityState.VERIFYING, OpportunityState.STALE, old, policy
        ).allowed
        is True
    )
    assert (
        evaluate_transition(
            OpportunityState.VERIFYING, OpportunityState.STALE, fresh, policy
        ).allowed
        is False
    )


def test_an_engine_window_that_ends_in_the_future_is_refused():
    policy = _policy()
    # A score taken against a later reference time carries a window that
    # ends after this context's reference time.
    score = _confidence_score(
        newest_evidence_at=NOW + timedelta(days=5),
        as_of=NOW + timedelta(days=10),
    )
    context = _context(confidence_score=score, as_of=NOW)

    decision = evaluate_transition(
        OpportunityState.VERIFYING, OpportunityState.STALE, context, policy
    )

    assert decision.allowed is False
    assert decision.reason_code == "evidence_dated_in_the_future"


def test_a_future_dated_evidence_refuses_a_legal_edge():
    decision = evaluate_transition(
        OpportunityState.VERIFYING,
        OpportunityState.STALE,
        _context(
            confidence_score=None,
            newest_evidence_at=NOW + timedelta(days=1),
            human_marked_stale=False,
        ),
        _policy(),
    )

    assert decision.allowed is False
    assert decision.reason_code == "evidence_dated_in_the_future"
    assert "2026-10-22" in decision.detail
    assert "2026-10-21" in decision.detail


def test_a_future_dated_evidence_refuses_every_legal_edge():
    policy = _policy()
    context = _context(
        confidence_score=None,
        newest_evidence_at=NOW + timedelta(days=1),
    )

    for current, target in LEGAL_EDGES:
        decision = evaluate_transition(current, target, context, policy)
        assert decision.allowed is False, f"{current.value} to {target.value}"
        assert decision.reason_code == "evidence_dated_in_the_future"


def test_a_future_dated_evidence_does_not_hide_an_illegal_edge():
    decision = evaluate_transition(
        OpportunityState.DEAD,
        OpportunityState.CANDIDATE,
        _context(
            confidence_score=None,
            newest_evidence_at=NOW + timedelta(days=1),
        ),
        _policy(),
    )

    assert decision.allowed is False
    assert decision.reason_code == "illegal_transition"


def test_apply_transition_refuses_a_future_dated_evidence():
    with pytest.raises(TransitionRefused) as failure:
        apply_transition(
            OpportunityState.VERIFYING,
            OpportunityState.STALE,
            _context(
                confidence_score=None,
                newest_evidence_at=NOW + timedelta(days=1),
                human_marked_stale=False,
            ),
            _policy(),
        )

    assert failure.value.decision.reason_code == "evidence_dated_in_the_future"


def test_fresh_evidence_keeps_the_opportunity_out_of_the_stale_state():
    policy = _policy()
    context = _context(
        newest_evidence_at=NOW - timedelta(days=1),
        human_marked_stale=False,
    )

    decision = evaluate_transition(
        OpportunityState.VERIFYING, OpportunityState.STALE, context, policy
    )

    assert decision.allowed is False
    assert decision.reason_code == "evidence_not_stale"


def test_a_human_can_mark_an_opportunity_stale():
    policy = _policy()
    context = _context(
        newest_evidence_at=NOW - timedelta(days=1),
        human_marked_stale=True,
    )

    decision = evaluate_transition(
        OpportunityState.VERIFYING, OpportunityState.STALE, context, policy
    )

    assert decision.allowed is True


@pytest.mark.parametrize(
    "current",
    [
        OpportunityState.REJECTED,
        OpportunityState.INSUFFICIENT_EVIDENCE,
        OpportunityState.STALE,
    ],
)
def test_a_resurrection_needs_new_evidence_and_a_reason(current):
    policy = _policy()

    allowed = evaluate_transition(
        current, OpportunityState.RESURRECTED, _context(), policy
    )
    no_evidence = evaluate_transition(
        current,
        OpportunityState.RESURRECTED,
        _context(new_evidence_count=0),
        policy,
    )
    no_reason = evaluate_transition(
        current, OpportunityState.RESURRECTED, _context(reason=""), policy
    )

    assert allowed.allowed is True
    assert no_evidence.reason_code == "no_new_evidence"
    assert no_reason.reason_code == "reason_missing"


def test_a_resurrected_opportunity_goes_back_to_verifying():
    decision = evaluate_transition(
        OpportunityState.RESURRECTED,
        OpportunityState.VERIFYING,
        _context(),
        _policy(),
    )

    assert decision.allowed is True


def test_a_rejected_opportunity_cannot_go_straight_to_verifying():
    decision = evaluate_transition(
        OpportunityState.REJECTED, OpportunityState.VERIFYING, _context(), _policy()
    )

    assert decision.allowed is False
    assert decision.reason_code == "illegal_transition"


def test_apply_transition_returns_the_new_state():
    result = apply_transition(
        OpportunityState.CANDIDATE,
        OpportunityState.VERIFYING,
        _context(),
        _policy(),
    )

    assert result is OpportunityState.VERIFYING


def test_apply_transition_raises_when_the_edge_is_refused():
    with pytest.raises(TransitionRefused) as failure:
        apply_transition(
            OpportunityState.VERIFYING,
            OpportunityState.PROMISING,
            _context(confidence_score=_confidence_score(identity_confidence=0.5)),
            _policy(),
        )

    assert failure.value.decision.reason_code == "insufficient_independent_people"
    assert failure.value.decision.allowed is False


def test_the_transition_table_holds_the_states_the_spec_names():
    assert set(ALLOWED_TRANSITIONS) == set(OpportunityState)
    assert len(OpportunityState) == 11
    assert ALLOWED_TRANSITIONS[OpportunityState.DEAD] == frozenset()

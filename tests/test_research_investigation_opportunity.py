from __future__ import annotations

import pytest

from product_hunter.research.investigation.opportunity import (
    HumanDecision,
    InvalidTransition,
    InvestigationOutcome,
    OpportunityState,
    allowed_transitions,
    apply_human_decision,
    next_state_for_outcome,
)

# The transition table the machine and the human share.
TRANSITIONS = {
    OpportunityState.candidate: (OpportunityState.verifying,),
    OpportunityState.verifying: (
        OpportunityState.promising,
        OpportunityState.insufficient_evidence,
    ),
    OpportunityState.promising: (
        OpportunityState.human_review,
        OpportunityState.rejected,
        OpportunityState.already_solved,
        OpportunityState.insufficient_evidence,
        OpportunityState.stale,
    ),
    OpportunityState.human_review: (
        OpportunityState.verifying,
        OpportunityState.validated,
        OpportunityState.rejected,
        OpportunityState.already_solved,
        OpportunityState.insufficient_evidence,
    ),
    OpportunityState.stale: (
        OpportunityState.verifying,
        OpportunityState.resurrected,
    ),
}


def test_the_allowed_transitions_are_the_table():
    for state, targets in TRANSITIONS.items():
        assert allowed_transitions(state) == targets


def test_the_machine_path_never_returns_validated():
    for state in OpportunityState:
        for outcome in InvestigationOutcome:
            try:
                moved = next_state_for_outcome(state, outcome)
            except InvalidTransition:
                continue
            assert moved is not OpportunityState.validated


def test_the_human_path_reaches_validated():
    assert (
        apply_human_decision(OpportunityState.human_review, HumanDecision.validate)
        is OpportunityState.validated
    )


def test_the_human_path_can_ask_for_more_research():
    assert (
        apply_human_decision(
            OpportunityState.human_review, HumanDecision.request_more_research
        )
        is OpportunityState.verifying
    )


def test_a_transition_outside_the_table_raises():
    with pytest.raises(InvalidTransition) as excinfo:
        next_state_for_outcome(OpportunityState.candidate, InvestigationOutcome.supported)

    assert excinfo.value.state is OpportunityState.candidate
    assert excinfo.value.target is OpportunityState.human_review


def test_a_human_decision_outside_the_table_raises():
    with pytest.raises(InvalidTransition):
        apply_human_decision(OpportunityState.promising, HumanDecision.validate)


def test_the_rejected_and_already_solved_outcomes_land_on_their_own_states():
    assert (
        next_state_for_outcome(
            OpportunityState.promising, InvestigationOutcome.contradicted
        )
        is OpportunityState.rejected
    )
    assert (
        next_state_for_outcome(
            OpportunityState.promising, InvestigationOutcome.already_solved
        )
        is OpportunityState.already_solved
    )


def test_the_supported_outcome_stops_at_human_review():
    assert (
        next_state_for_outcome(OpportunityState.promising, InvestigationOutcome.supported)
        is OpportunityState.human_review
    )


def test_the_incomplete_outcome_reads_as_insufficient_evidence():
    assert (
        next_state_for_outcome(
            OpportunityState.promising, InvestigationOutcome.incomplete
        )
        is OpportunityState.insufficient_evidence
    )
    assert (
        next_state_for_outcome(
            OpportunityState.verifying, InvestigationOutcome.inconclusive
        )
        is OpportunityState.insufficient_evidence
    )

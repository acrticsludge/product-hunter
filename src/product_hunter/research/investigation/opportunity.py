"""The opportunity transition table: the machine never validates an idea.

The machine path stops at HUMAN_REVIEW. VALIDATED is reachable only when a
human decides it, and a transition the table does not allow raises.
"""

from __future__ import annotations

from enum import Enum


class OpportunityState(str, Enum):
    """Where an opportunity stands."""

    # The member names are lower case, the values are the spec's state
    # names, so a persisted state reads the way the spec writes it.
    candidate = "CANDIDATE"
    verifying = "VERIFYING"
    promising = "PROMISING"
    human_review = "HUMAN_REVIEW"
    validated = "VALIDATED"
    rejected = "REJECTED"
    already_solved = "ALREADY_SOLVED"
    insufficient_evidence = "INSUFFICIENT_EVIDENCE"
    stale = "STALE"
    dead = "DEAD"
    resurrected = "RESURRECTED"


class InvestigationOutcome(str, Enum):
    """What an investigation concluded."""

    supported = "supported"
    contradicted = "contradicted"
    already_solved = "already_solved"
    inconclusive = "inconclusive"
    incomplete = "incomplete"


class HumanDecision(str, Enum):
    """What a human decided about an opportunity."""

    validate = "validate"
    reject = "reject"
    insufficient_evidence = "insufficient_evidence"
    already_solved = "already_solved"
    request_more_research = "request_more_research"


class InvalidTransition(Exception):
    """A transition the table does not allow."""

    def __init__(self, state: OpportunityState, target: OpportunityState) -> None:
        self.state = state
        self.target = target
        super().__init__(
            f"the transition from {state.value} to {target.value} is not allowed"
        )


# The machine transition table. A state that is not in it is a terminal one.
_TRANSITIONS: dict[OpportunityState, tuple[OpportunityState, ...]] = {
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

# The state an outcome moves to. VALIDATED is not in it: the machine never
# validates an opportunity.
_OUTCOME_STATES: dict[InvestigationOutcome, OpportunityState] = {
    InvestigationOutcome.supported: OpportunityState.human_review,
    InvestigationOutcome.contradicted: OpportunityState.rejected,
    InvestigationOutcome.already_solved: OpportunityState.already_solved,
    InvestigationOutcome.inconclusive: OpportunityState.insufficient_evidence,
    InvestigationOutcome.incomplete: OpportunityState.insufficient_evidence,
}

# The state a human decision moves to.
_DECISION_STATES: dict[HumanDecision, OpportunityState] = {
    HumanDecision.validate: OpportunityState.validated,
    HumanDecision.reject: OpportunityState.rejected,
    HumanDecision.insufficient_evidence: OpportunityState.insufficient_evidence,
    HumanDecision.already_solved: OpportunityState.already_solved,
    HumanDecision.request_more_research: OpportunityState.verifying,
}


def allowed_transitions(state: OpportunityState) -> tuple[OpportunityState, ...]:
    """Return the states this state may move to."""
    return _TRANSITIONS.get(state, ())


def next_state_for_outcome(
    state: OpportunityState, outcome: InvestigationOutcome
) -> OpportunityState:
    """Move a state the way an investigation outcome says it may move."""
    target = _OUTCOME_STATES[outcome]
    if target not in allowed_transitions(state):
        raise InvalidTransition(state, target)
    return target


def apply_human_decision(
    state: OpportunityState, decision: HumanDecision
) -> OpportunityState:
    """Move a state the way a human decision says it may move."""
    target = _DECISION_STATES[decision]
    if target not in allowed_transitions(state):
        raise InvalidTransition(state, target)
    return target

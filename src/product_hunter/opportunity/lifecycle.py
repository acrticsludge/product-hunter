"""The opportunity state machine: the states, the edges, and the requirements.

The machine never reads the clock. A transition carries the context it was
judged against, so the same context and the same policy always reach the same
verdict.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator

from product_hunter.opportunity.policy import ScoringPolicy
from product_hunter.opportunity.scoring import ConfidenceScore


class OpportunityState(str, Enum):
    """The states an opportunity can be in.

    This is separate from the pipeline stage and from the research-run state.
    """

    CANDIDATE = "CANDIDATE"
    VERIFYING = "VERIFYING"
    PROMISING = "PROMISING"
    HUMAN_REVIEW = "HUMAN_REVIEW"
    VALIDATED = "VALIDATED"
    REJECTED = "REJECTED"
    ALREADY_SOLVED = "ALREADY_SOLVED"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    STALE = "STALE"
    DEAD = "DEAD"
    RESURRECTED = "RESURRECTED"


# The allowed edges. An edge that is not here is refused as an illegal
# transition, whatever reason the caller gives. DEAD has no outgoing edge:
# a dead opportunity is not coming back without a human reopening it.
ALLOWED_TRANSITIONS: dict[OpportunityState, frozenset[OpportunityState]] = {
    OpportunityState.CANDIDATE: frozenset(
        {OpportunityState.VERIFYING, OpportunityState.REJECTED}
    ),
    OpportunityState.VERIFYING: frozenset(
        {
            OpportunityState.PROMISING,
            OpportunityState.INSUFFICIENT_EVIDENCE,
            OpportunityState.REJECTED,
            OpportunityState.ALREADY_SOLVED,
            OpportunityState.STALE,
        }
    ),
    OpportunityState.PROMISING: frozenset(
        {
            OpportunityState.HUMAN_REVIEW,
            OpportunityState.ALREADY_SOLVED,
            OpportunityState.REJECTED,
            OpportunityState.INSUFFICIENT_EVIDENCE,
            OpportunityState.STALE,
        }
    ),
    OpportunityState.HUMAN_REVIEW: frozenset(
        {
            OpportunityState.VALIDATED,
            OpportunityState.REJECTED,
            OpportunityState.STALE,
        }
    ),
    OpportunityState.VALIDATED: frozenset(
        {OpportunityState.STALE, OpportunityState.DEAD}
    ),
    OpportunityState.REJECTED: frozenset(
        {OpportunityState.STALE, OpportunityState.RESURRECTED}
    ),
    OpportunityState.INSUFFICIENT_EVIDENCE: frozenset(
        {
            OpportunityState.VERIFYING,
            OpportunityState.STALE,
            OpportunityState.RESURRECTED,
        }
    ),
    OpportunityState.STALE: frozenset(
        {OpportunityState.VERIFYING, OpportunityState.RESURRECTED}
    ),
    OpportunityState.RESURRECTED: frozenset({OpportunityState.VERIFYING}),
    # The market has an answer for this one, so there is nowhere to go.
    OpportunityState.ALREADY_SOLVED: frozenset(),
    OpportunityState.DEAD: frozenset(),
}

# The states a decision needs a reason for.
REASON_REQUIRED: frozenset[OpportunityState] = frozenset(
    {
        OpportunityState.REJECTED,
        OpportunityState.ALREADY_SOLVED,
        OpportunityState.INSUFFICIENT_EVIDENCE,
        OpportunityState.DEAD,
    }
)


class TransitionContext(BaseModel):
    """What a transition is judged against.

    The reference time comes in here, so the machine never reads the clock,
    and the age of the evidence comes in only when the caller has no engine
    score to supply it. The promotion gate reads the engine's own confidence
    score rather than a number the caller asserts: the count of independent
    people inside that score is already thresholded by the identity policy.

    The human decision records that a human decided and what they decided.
    The engine does not parse it: any non-empty value means the decision was
    recorded, and what the decision means stays with the human who took it.

    The newest evidence date is consulted only when no confidence score is
    attached. With a score, the engine's own window is the answer to how old
    the evidence is, and it wins over the asserted date.
    """

    model_config = ConfigDict(extra="forbid")

    as_of: datetime
    confidence_score: ConfidenceScore | None = None
    counter_evidence_searched: bool = False
    human_decision: str | None = None
    human_marked_stale: bool = False
    newest_evidence_at: datetime | None = None
    new_evidence_count: int = Field(default=0, ge=0)
    reason: str = ""

    @field_validator("as_of", "newest_evidence_at")
    @classmethod
    def _timestamps_are_aware(cls, value: datetime | None) -> datetime | None:
        if value is not None and (
            value.tzinfo is None or value.tzinfo.utcoffset(value) is None
        ):
            raise ValueError("a transition timestamp must be timezone-aware")
        return value


class TransitionDecision(BaseModel):
    """The verdict on one requested edge, with the reason code."""

    model_config = ConfigDict(extra="forbid")

    allowed: bool
    reason_code: str = Field(min_length=1)
    detail: str = ""
    from_state: OpportunityState
    to_state: OpportunityState


class TransitionRefused(Exception):
    """The requested edge was refused, and the decision says why."""

    def __init__(self, decision: TransitionDecision) -> None:
        self.decision = decision
        super().__init__(decision.detail or decision.reason_code)


def _effective_newest_evidence(context: TransitionContext) -> date | None:
    """Return the day the context's newest evidence falls on.

    The engine's confidence score carries the window its evidence was
    observed in, and that window is the engine's answer to how old the
    evidence is. When a score is attached, its answer wins over the date the
    caller asserts, so the two cannot contradict each other. The comparison
    is at day granularity, the way the scorer compares.
    """
    score = context.confidence_score
    if score is not None and score.independence.window is not None:
        return score.independence.window.last_seen
    if context.newest_evidence_at is None:
        return None
    return context.newest_evidence_at.date()


def _evidence_is_stale(context: TransitionContext, policy: ScoringPolicy) -> bool:
    """Report whether the newest evidence is older than the staleness window."""
    if context.human_marked_stale:
        return True
    newest = _effective_newest_evidence(context)
    if newest is None:
        # There is no evidence to age, so the window cannot be exceeded.
        return False
    age_days = (context.as_of.date() - newest).days
    return age_days > policy.lifecycle.staleness_days


def _future_evidence(context: TransitionContext) -> str | None:
    """Return the detail of an impossible evidence date, or nothing.

    Evidence dated after the reference time is clock skew or a fabrication,
    and a negative age is not older than the staleness window. A context the
    machine cannot trust must not move an opportunity, so the date is
    refused before any requirement is judged.
    """
    newest = _effective_newest_evidence(context)
    if newest is None:
        return None
    reference = context.as_of.date()
    if newest <= reference:
        return None
    return (
        f"the newest evidence is dated {newest.isoformat()}, "
        f"after the reference date {reference.isoformat()}"
    )


def evaluate_transition(
    current: OpportunityState,
    target: OpportunityState,
    context: TransitionContext,
    policy: ScoringPolicy,
) -> TransitionDecision:
    """Judge one requested edge against the table and the requirements.

    An edge that is not in the table is refused as an illegal transition. A
    legal edge whose context carries an impossible date is refused as such. A
    legal edge whose requirement is unmet is refused with the requirement's
    own reason code, so the caller can tell a wrong request from a premature
    one.
    """
    if target not in ALLOWED_TRANSITIONS[current]:
        return TransitionDecision(
            allowed=False,
            reason_code="illegal_transition",
            detail=(
                f"there is no edge from {current.value} to {target.value}"
            ),
            from_state=current,
            to_state=target,
        )

    impossible_date = _future_evidence(context)
    if impossible_date is not None:
        return TransitionDecision(
            allowed=False,
            reason_code="evidence_dated_in_the_future",
            detail=impossible_date,
            from_state=current,
            to_state=target,
        )

    refusal = _requirement_refusal(current, target, context, policy)
    if refusal is not None:
        reason_code, detail = refusal
        return TransitionDecision(
            allowed=False,
            reason_code=reason_code,
            detail=detail,
            from_state=current,
            to_state=target,
        )

    return TransitionDecision(
        allowed=True,
        reason_code="allowed",
        detail=f"the edge from {current.value} to {target.value} is allowed",
        from_state=current,
        to_state=target,
    )


def _requirement_refusal(
    current: OpportunityState,
    target: OpportunityState,
    context: TransitionContext,
    policy: ScoringPolicy,
) -> tuple[str, str] | None:
    """Return the reason the edge is refused, or nothing when it is allowed."""
    if (
        current is OpportunityState.VERIFYING
        and target is OpportunityState.PROMISING
    ):
        score = context.confidence_score
        if score is None:
            return (
                "confidence_score_missing",
                "the promotion gate needs the engine's own confidence score, "
                "not a number the caller asserts",
            )
        independent = score.independence.independent_author_count
        if independent < policy.lifecycle.min_independent_people:
            return (
                "insufficient_independent_people",
                f"{independent} independent people, the policy requires "
                f"{policy.lifecycle.min_independent_people}",
            )
        if score.score < policy.lifecycle.min_confidence:
            return (
                "insufficient_confidence",
                f"the confidence is {score.score}, the policy requires "
                f"{policy.lifecycle.min_confidence}",
            )
        return None

    if (
        current is OpportunityState.PROMISING
        and target is OpportunityState.HUMAN_REVIEW
    ):
        if not context.counter_evidence_searched:
            return (
                "counter_evidence_missing",
                "no counter-evidence search ran, so there is nothing to review",
            )
        return None

    if (
        current is OpportunityState.HUMAN_REVIEW
        and target is OpportunityState.VALIDATED
    ):
        if not context.human_decision:
            return (
                "human_decision_missing",
                "no human decision was recorded",
            )
        return None

    if target is OpportunityState.STALE:
        if not _evidence_is_stale(context, policy):
            return (
                "evidence_not_stale",
                "the evidence is inside the staleness window and no human "
                "marked the opportunity stale",
            )
        return None

    if target is OpportunityState.RESURRECTED:
        if context.new_evidence_count <= 0:
            return (
                "no_new_evidence",
                "no new evidence arrived since the previous state",
            )
        if not context.reason:
            return (
                "reason_missing",
                "a resurrection needs a reason",
            )
        return None

    if target in REASON_REQUIRED and not context.reason:
        return (
            "reason_missing",
            f"a move to {target.value} needs a reason",
        )
    return None


def apply_transition(
    current: OpportunityState,
    target: OpportunityState,
    context: TransitionContext,
    policy: ScoringPolicy,
) -> OpportunityState:
    """Return the new state, or refuse the edge and raise."""
    decision = evaluate_transition(current, target, context, policy)
    if not decision.allowed:
        raise TransitionRefused(decision)
    return target

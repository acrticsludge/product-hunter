"""The states an operation ends in, and how a failure is read.

The names are the run states of the contract, lowercased, because the column
in ``operation_runs`` stores them lowercased too. A budget that ran out is a
completion and not a failure, so it has a state of its own.

Every state except running and retryable_failure is terminal: the operation
stops there and says why. A retryable failure is a failed condition and not
an end, so the operation carries on with the next job and the failure is
recorded on the operation row.

The failure text carries a fixed marker, and the raisers below write the
marker while the classifier reads it. The two therefore agree on what a
permanent failure and a blocked one look like.
"""

from __future__ import annotations

from enum import Enum

# The markers the failure text carries. They are fixed text, so a message
# that names one of them is a message the operation wrote on purpose.
PERMANENT_MARKER = "permanent_failure"
BLOCKED_MARKER = "blocked_failure"


class OperationState(str, Enum):
    """Where the operation is."""

    running = "running"
    completed = "completed"
    budget_exhausted = "budget_exhausted"
    blocked = "blocked"
    retryable_failure = "retryable_failure"
    permanent_failure = "permanent_failure"
    cancelled = "cancelled"
    paused = "paused"


# The states an operation does not leave. A retryable failure is not one of
# them: the job is offered again and the operation goes on.
TERMINAL_STATES = frozenset(
    state
    for state in OperationState
    if state not in (OperationState.running, OperationState.retryable_failure)
)

# The states a failed operation is in. A budget that ran out and a pause are
# not failures, so they are not in here. A retryable failure is a failure
# even though the operation carries on.
FAILED_STATES = frozenset(
    {
        OperationState.blocked,
        OperationState.retryable_failure,
        OperationState.permanent_failure,
    }
)


class OperationError(Exception):
    """The operation could not go on."""


class PermanentError(OperationError):
    """The handler says the work will not succeed if it is tried again."""

    def __init__(self, detail: str = "") -> None:
        self.detail = detail
        # The colon is part of the marker: the classifier reads the marker
        # with it, so the marker always carries one.
        message = f"{PERMANENT_MARKER}: {detail}" if detail else f"{PERMANENT_MARKER}:"
        super().__init__(message)


class BlockedError(OperationError):
    """A gate refused the work, so it waits for an operator."""

    def __init__(self, detail: str = "") -> None:
        self.detail = detail
        message = f"{BLOCKED_MARKER}: {detail}" if detail else f"{BLOCKED_MARKER}:"
        super().__init__(message)


def classify_failure(error: str | None) -> OperationState:
    """The state a failure text names.

    A text that names nothing in particular is a failure that may be tried
    again, because that is the safe reading of an unknown fault. The marker
    is read with its colon, because the raisers write the marker and the
    detail behind it, so a message that merely holds the words is not read
    as one the operation wrote.
    """
    if not error:
        return OperationState.retryable_failure
    if f"{PERMANENT_MARKER}:" in error:
        return OperationState.permanent_failure
    if f"{BLOCKED_MARKER}:" in error:
        return OperationState.blocked
    return OperationState.retryable_failure

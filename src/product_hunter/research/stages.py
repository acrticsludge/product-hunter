"""Stage contracts and the exceptions a stage raises when it cannot honour them.

A stage is not successful because its handler returned. It is successful only
when it produces the output its contract requires, and the worker checks that
before it marks the job done. A stage that is not implemented yet must raise
``StageNotImplemented`` rather than silently return, so an unimplemented
stage can never be mistaken for a completed one.
"""

from __future__ import annotations

from typing import Any


class StageNotImplemented(Exception):
    """A pipeline stage has no real implementation yet.

    Raising this is the machine-detectable form of "BLOCKED / NOT_IMPLEMENTED".
    The worker records the job as dead with this error and does not retry it,
    because retrying an unimplemented stage cannot succeed.
    """

    def __init__(self, stage: str, *, reason: str = "not implemented") -> None:
        self.stage = stage
        self.reason = reason
        super().__init__(f"NOT_IMPLEMENTED: {stage}: {reason}")


class StageOutputMissing(Exception):
    """A stage ran but did not produce its required output.

    Unlike ``StageNotImplemented`` this is retryable: the stage exists, but
    this attempt failed to produce what it had to.
    """

    def __init__(self, stage: str, missing: list[str]) -> None:
        self.stage = stage
        self.missing = list(missing)
        super().__init__(
            f"stage {stage!r} missing required output: {', '.join(missing)}"
        )


# The output keys a stage must return in its result dict for the worker to
# accept the attempt as successful. An empty set means the stage's contract
# is enforced elsewhere (for example by the research gate) and the worker
# only requires a non-None dict.
STAGE_OUTPUT_REQUIREMENTS: dict[str, set[str]] = {
    "DISCOVERY": {"discovered"},
    "PAIN_EXTRACTION": {"extracted"},
    "COLLECTION": set(),
    "NORMALIZATION": set(),
    "DEDUPLICATION": set(),
    "ENTITY_RESOLUTION": set(),
    "EMBEDDING": set(),
    "CLUSTERING": set(),
    "INDEPENDENCE_ANALYSIS": set(),
    "VALIDATION": set(),
    "COMPETITOR_RESEARCH": set(),
    "COUNTER_EVIDENCE": set(),
    "SCORING": set(),
    "REPORTING": set(),
    "OBSIDIAN_SYNC": set(),
}


def required_output_keys(stage: str) -> set[str]:
    return STAGE_OUTPUT_REQUIREMENTS.get(stage, set())


def validate_stage_result(stage: str, result: Any) -> list[str]:
    """Return the missing required output keys, or an empty list on success."""
    if not isinstance(result, dict):
        return sorted(required_output_keys(stage)) or ["result"]
    missing: list[str] = []
    for key in required_output_keys(stage):
        if key not in result:
            missing.append(key)
            continue
        value = result.get(key)
        # A zero count is still a value, but a None is not.
        if value is None:
            missing.append(key)
    return sorted(missing)

"""The checkpoint of an investigation: the steps that ran, and their evidence.

Research resumes, it does not restart. The checkpoint holds the completed
steps with the evidence they produced, so a resumed investigation re-runs only
the steps that are missing.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from enum import Enum
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field, field_validator

from product_hunter.research.investigation.areas import AreaFinding, ResearchArea
from product_hunter.research.investigation.competitors import Competitor
from product_hunter.research.investigation.counter_evidence import CounterEvidenceItem

if TYPE_CHECKING:
    from product_hunter.research.investigation.pipeline import (
        InvestigationPlan,
        PlanStep,
    )


class StepStatus(str, Enum):
    """Where a plan step stands."""

    pending = "pending"
    running = "running"
    completed = "completed"
    failed = "failed"


class InvestigationStep(BaseModel):
    """One step of the plan, with what its call produced."""

    model_config = ConfigDict(extra="forbid")

    step_id: str = Field(min_length=1)
    area: ResearchArea
    family: str = Field(min_length=1)
    query: str
    status: StepStatus = StepStatus.pending
    attempt: int = Field(default=1, ge=1)
    input_hash: str = Field(min_length=1)
    tool_call_id: str = ""
    document_count: int = Field(default=0, ge=0)
    domains: list[str] = Field(default_factory=list)
    # The documents the call returned. The checkpoint holds the evidence, so a
    # resumed investigation can rebuild the provenance the first run built.
    source_urls: list[str] = Field(default_factory=list)
    result_count: int = Field(default=0, ge=0)
    # What the call classified. Without these the resumed investigation would
    # find nothing and publish an absence claim about searches that found
    # something.
    competitors: list[Competitor] = Field(default_factory=list)
    counter_evidence: list[CounterEvidenceItem] = Field(default_factory=list)
    findings: list[AreaFinding] = Field(default_factory=list)
    error: str | None = None


class InvestigationCheckpoint(BaseModel):
    """The steps an investigation has already run."""

    model_config = ConfigDict(extra="forbid")

    investigation_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    problem_hash: str = Field(min_length=1)
    contract_fingerprint: str = ""
    steps: list[InvestigationStep] = Field(default_factory=list)
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def _created_at_is_aware(cls, value: datetime) -> datetime:
        # A naive timestamp cannot be compared with an aware one, so it
        # silently switches off every freshness check downstream.
        if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
            raise ValueError("created_at must be timezone-aware")
        return value


def _hash(text: str) -> str:
    """Return the short hash of a text."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _area_name(area: ResearchArea | str) -> str:
    """Return the plain name of a research area."""
    return str(getattr(area, "value", area))


def step_id_for(area: ResearchArea | str, family: str, query: str) -> str:
    """Return the id of the step that searches one family in one area."""
    return _hash(f"{_area_name(area)}|{family}|{query}")


def input_hash_for(query: str) -> str:
    """Return the hash of a query, which is the input a step is keyed on."""
    return _hash(query)


def build_checkpoint(
    investigation_id: str,
    run_id: str,
    problem: str,
    steps: list[InvestigationStep],
    contract_fingerprint: str = "",
    *,
    created_at: datetime | None = None,
    expected_problem_hash: str | None = None,
    expected_investigation_id: str | None = None,
    expected_run_id: str | None = None,
) -> InvestigationCheckpoint:
    """Build the checkpoint of one investigation."""
    problem_hash = input_hash_for(problem)
    if expected_problem_hash is not None and expected_problem_hash != problem_hash:
        # A checkpoint of another problem is not a resume of this one.
        raise ValueError("the checkpoint is for a different problem")
    if (
        expected_investigation_id is not None
        and expected_investigation_id != investigation_id
    ):
        # A checkpoint of another investigation is not a resume of this one.
        raise ValueError("the checkpoint is for a different investigation")
    if expected_run_id is not None and expected_run_id != run_id:
        # A checkpoint of another run carries another run's evidence.
        raise ValueError("the checkpoint is for a different run")
    for step in steps:
        if step.input_hash != input_hash_for(step.query):
            raise ValueError("a step records an input hash that does not match its query")
    return InvestigationCheckpoint(
        investigation_id=investigation_id,
        run_id=run_id,
        problem_hash=problem_hash,
        contract_fingerprint=contract_fingerprint,
        steps=list(steps),
        created_at=created_at or datetime.now(timezone.utc),
    )


def completed_steps(checkpoint: InvestigationCheckpoint) -> dict[str, InvestigationStep]:
    """Return the completed steps of a checkpoint, by step id."""
    return {
        step.step_id: step
        for step in checkpoint.steps
        if step.status is StepStatus.completed
    }


def pending_steps(
    checkpoint: InvestigationCheckpoint, plan: InvestigationPlan
) -> list[PlanStep]:
    """Return the plan steps the checkpoint does not hold as completed."""
    done = completed_steps(checkpoint)
    return [step for step in plan.steps if step.step_id not in done]


def apply_step(
    checkpoint: InvestigationCheckpoint, step: InvestigationStep
) -> InvestigationCheckpoint:
    """Return a checkpoint that holds the step, leaving the old one untouched."""
    steps = list(checkpoint.steps)
    for index, existing in enumerate(steps):
        if existing.step_id == step.step_id:
            steps[index] = step
            break
    else:
        steps.append(step)
    # The checkpoint is a value: the caller keeps the old one, and applying
    # the same step twice yields the same checkpoint.
    return checkpoint.model_copy(update={"steps": steps}, deep=True)

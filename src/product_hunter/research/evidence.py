"""Tool-call provenance, artifact records, and the evidence a stage produced.

This module holds data only. It never calls a tool, never runs a stage, and
never reads a file: it records what a collector or analyst did so the gate
can judge it later.
"""

from __future__ import annotations

import re
from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# A result reference is an opaque handle, not prose: a hand-written summary
# carries spaces and cannot pass as the original tool result.
_RESULT_REF_RE = re.compile(r"^[A-Za-z0-9._:/-]+$")


def _require_aware(value: datetime | None, field: str) -> None:
    """Refuse a timestamp that carries no timezone.

    A naive timestamp cannot be compared with an aware one, so it silently
    switches off every check that measures a duration. The name of the field
    that was refused reaches the message.
    """
    if value is None:
        return
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        raise ValueError(f"{field} must be timezone-aware")


class ToolCallStatus(str, Enum):
    success = "success"
    failure = "failure"
    timeout = "timeout"
    partial = "partial"


class StageStatus(str, Enum):
    not_started = "not_started"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"
    timed_out = "timed_out"


class ClaimKind(str, Enum):
    no_strong_competitor = "no_strong_competitor"
    no_counter_evidence_found = "no_counter_evidence_found"
    opportunity_exists = "opportunity_exists"
    competitors_exist = "competitors_exist"
    existing_solution_exists = "existing_solution_exists"
    market_validated = "market_validated"
    willingness_to_pay = "willingness_to_pay"
    distribution_accessible = "distribution_accessible"
    problem_exists = "problem_exists"
    problem_recurs = "problem_recurs"
    problem_is_severe = "problem_is_severe"
    independent_population_exists = "independent_population_exists"
    market_is_large = "market_is_large"
    buildable = "buildable"
    timing_is_right = "timing_is_right"


class ToolCallProvenance(BaseModel):
    """One material tool execution and the handle an artifact points back to."""

    model_config = ConfigDict(extra="forbid")

    run_id: str = Field(min_length=1)
    stage: str = Field(min_length=1)
    agent: str = Field(min_length=1)
    tool: str = Field(min_length=1)
    purpose: str = Field(min_length=1)
    timestamp: datetime
    query_or_operation: str = Field(min_length=1)
    result_ref: str
    result_count: int = Field(ge=0)
    status: ToolCallStatus
    tool_call_id: str | None = None
    query_family: str | None = None
    duration_ms: int | None = None
    error: str | None = None

    @field_validator("timestamp")
    @classmethod
    def _timestamp_is_aware(cls, value: datetime) -> datetime:
        _require_aware(value, "timestamp")
        return value

    @model_validator(mode="after")
    def _check_invariants(self) -> ToolCallProvenance:
        if self.status is ToolCallStatus.success and not self.result_ref:
            raise ValueError("result_ref is required when status is 'success'")
        if self.result_ref:
            if any(character.isspace() for character in self.result_ref):
                raise ValueError("result_ref must not contain whitespace")
            if not _RESULT_REF_RE.match(self.result_ref):
                raise ValueError("result_ref must match ^[A-Za-z0-9._:/-]+$")
        for name in ("tool_call_id", "query_family", "error"):
            value = getattr(self, name)
            if value is not None and not value:
                raise ValueError(f"{name} must not be empty")
        return self


class ArtifactRecord(BaseModel):
    """One produced artifact and the tool call that produced it."""

    model_config = ConfigDict(extra="forbid")

    artifact_id: str = Field(min_length=1)
    stage: str = Field(min_length=1)
    kind: str = Field(min_length=1)
    ref: str = Field(min_length=1)
    item_count: int = Field(ge=0)
    content_hash: str | None = None
    schema_version: str | None = None
    valid: bool = True
    invalid_reason: str | None = None
    produced_by_tool_call_id: str | None = None
    created_at: datetime


class ZeroResultOutcome(BaseModel):
    """A recorded search that found nothing, with the families it covered."""

    model_config = ConfigDict(extra="forbid")

    recorded: bool = False
    required_query_families: list[str] = Field(default_factory=list)
    completed_query_families: list[str] = Field(default_factory=list)
    note: str = ""


class StageExecution(BaseModel):
    """What one stage did: the calls it made, the artifacts it produced."""

    model_config = ConfigDict(extra="forbid")

    stage: str = Field(min_length=1)
    status: StageStatus
    attempt: int = Field(default=1, ge=1)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    tool_calls: list[ToolCallProvenance] = Field(default_factory=list)
    artifacts: list[ArtifactRecord] = Field(default_factory=list)
    zero_result: ZeroResultOutcome = Field(default_factory=ZeroResultOutcome)
    error: str | None = None

    @field_validator("started_at", "finished_at")
    @classmethod
    def _timestamps_are_aware(cls, value: datetime | None, info) -> datetime | None:
        # A naive timestamp would switch the stage timeout check off.
        _require_aware(value, info.field_name)
        return value


class ResearchClaim(BaseModel):
    """A conclusion the run wants to publish, with the evidence behind it."""

    model_config = ConfigDict(extra="forbid")

    claim_id: str = Field(min_length=1)
    kind: ClaimKind
    statement: str = Field(min_length=1)
    stage: str = Field(min_length=1)
    evidence_refs: list[str] = Field(default_factory=list)


class RunEvidence(BaseModel):
    """Every stage execution and claim of one research run."""

    model_config = ConfigDict(extra="forbid")

    run_id: str = Field(min_length=1)
    contract_version: str = Field(min_length=1)
    stages: dict[str, StageExecution] = Field(default_factory=dict)
    claims: list[ResearchClaim] = Field(default_factory=list)

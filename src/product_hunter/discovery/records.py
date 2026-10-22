"""The discovery record: one search, what it returned, and what it cost.

The record is the unit of accountability. It says what was searched, what
came back, and what it cost, and it keeps the status vocabulary honest: a
search that ran and found nothing is ``empty``, and a search whose tool
broke is ``failure``. The two are never the same value.

This module holds data only. It never calls a tool, never runs a search, and
never computes a novelty: the frontier owns that arithmetic, and it is the
frontier computing it that makes a novelty true. The record checks its own
arithmetic, and refuses a novelty that does not add up to its duplicate
rate.
"""

from __future__ import annotations

import hashlib
import math
import re
from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from product_hunter.discovery.families import QueryFamily
from product_hunter.research.evidence import ToolCallProvenance, ToolCallStatus

# The identifier prefix of a discovery record.
RECORD_ID_PREFIX = "dq_"

# The query id a record points at is this shape.
_QUERY_ID_RE = re.compile(r"^q_[0-9a-f]{16}$")

# A result reference is an opaque handle, not prose: the same rule the
# research provenance enforces, so a record and a tool call agree.
_RESULT_REF_RE = re.compile(r"^[A-Za-z0-9._:/-]+$")


class DiscoveryStatus(str, Enum):
    """How a search ended."""

    success = "success"
    empty = "empty"
    failure = "failure"
    timeout = "timeout"
    partial = "partial"
    duplicate = "duplicate"
    over_budget = "over_budget"


def _status_value(status: DiscoveryStatus | str) -> str:
    """Return the plain name of a status."""
    if isinstance(status, DiscoveryStatus):
        return status.value
    return str(status)


def record_id(
    run_id: str,
    query_id: str,
    timestamp: datetime,
    status: DiscoveryStatus | str,
) -> str:
    """Return the identifier of a record.

    The identifier is the prefix and the first sixteen hex characters of the
    SHA-256 of the run id, the query id, the timestamp and the status,
    joined by a pipe. The same record always carries the same id, so a
    replay reproduces it.
    """
    basis = f"{run_id}|{query_id}|{timestamp.isoformat()}|{_status_value(status)}"
    return RECORD_ID_PREFIX + hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16]


class DiscoveryRecord(BaseModel):
    """One search the frontier ran, with everything it returned."""

    model_config = ConfigDict(extra="forbid")

    record_id: str = ""
    run_id: str = Field(min_length=1)
    stage: str = Field(min_length=1)
    tool: str = Field(min_length=1)
    agent: str = Field(min_length=1)
    query_id: str
    query: str = Field(min_length=1)
    family: QueryFamily
    source_id: str = Field(min_length=1)
    timestamp: datetime
    result_count: int = Field(ge=0)
    useful_count: int = Field(ge=0)
    novelty: float = Field(ge=0.0, le=1.0)
    duplicate_rate: float = Field(ge=0.0, le=1.0)
    cost: float = Field(ge=0.0)
    status: DiscoveryStatus
    result_ref: str = ""
    error: str = ""

    @field_validator("timestamp")
    @classmethod
    def _timestamp_is_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
            raise ValueError("timestamp must be timezone-aware")
        return value

    @field_validator("query_id")
    @classmethod
    def _query_id_shape(cls, value: str) -> str:
        if not _QUERY_ID_RE.match(value):
            raise ValueError("query_id must be a query id")
        return value

    @field_validator("cost")
    @classmethod
    def _cost_is_finite(cls, value: float) -> float:
        # A non-finite cost is not a spend: every comparison against it is
        # false, so the cost limit would switch itself off.
        if not math.isfinite(value):
            raise ValueError("cost must be a finite number")
        return value

    @model_validator(mode="before")
    @classmethod
    def _fill_record_id(cls, value: object) -> object:
        """Give the record its identifier when the caller did not."""
        if not isinstance(value, dict) or value.get("record_id"):
            return value
        run_id = value.get("run_id")
        query_id = value.get("query_id")
        timestamp = value.get("timestamp")
        status = value.get("status")
        if not run_id or not query_id or timestamp is None or status is None:
            return value
        if isinstance(timestamp, str):
            try:
                timestamp = datetime.fromisoformat(timestamp)
            except ValueError:
                return value
        return {
            **value,
            "record_id": record_id(run_id, query_id, timestamp, status),
        }

    @model_validator(mode="after")
    def _check_invariants(self) -> DiscoveryRecord:
        expected_id = record_id(
            self.run_id, self.query_id, self.timestamp, self.status
        )
        if self.record_id != expected_id:
            raise ValueError(f"record_id must be {expected_id} for these fields")
        if self.status is DiscoveryStatus.success and self.result_count <= 0:
            raise ValueError("status 'success' needs a result count above zero")
        if self.status is DiscoveryStatus.empty and self.result_count != 0:
            raise ValueError("status 'empty' needs a result count of zero")
        if self.status in (
            DiscoveryStatus.failure,
            DiscoveryStatus.timeout,
            DiscoveryStatus.duplicate,
            DiscoveryStatus.over_budget,
        ) and self.result_count != 0:
            raise ValueError(
                f"status {self.status.value!r} needs a result count of zero"
            )
        if self.useful_count > self.result_count:
            raise ValueError("useful_count must not exceed result_count")
        if self.result_count > 0:
            if not math.isclose(
                self.novelty + self.duplicate_rate, 1.0, abs_tol=1e-9
            ):
                raise ValueError("novelty and duplicate_rate must sum to one")
        elif self.novelty != 0.0 or self.duplicate_rate != 0.0:
            raise ValueError(
                "novelty and duplicate_rate must be zero when no result came back"
            )
        if self.result_ref:
            if any(character.isspace() for character in self.result_ref):
                raise ValueError("result_ref must not contain whitespace")
            if not _RESULT_REF_RE.match(self.result_ref):
                raise ValueError("result_ref must match ^[A-Za-z0-9._:/-]+$")
        return self

    def to_tool_call_provenance(self) -> ToolCallProvenance | None:
        """Return the research provenance of this record.

        A search that ran and found nothing is a successful call: the tool
        ran. A query that was refused as a duplicate, or that the budget
        would not admit, is no call at all, because no tool ran for it.
        """
        status = {
            DiscoveryStatus.success: ToolCallStatus.success,
            DiscoveryStatus.empty: ToolCallStatus.success,
            DiscoveryStatus.failure: ToolCallStatus.failure,
            DiscoveryStatus.timeout: ToolCallStatus.timeout,
            DiscoveryStatus.partial: ToolCallStatus.partial,
        }.get(self.status)
        if status is None:
            return None
        return ToolCallProvenance(
            run_id=self.run_id,
            stage=self.stage,
            agent=self.agent,
            tool=self.tool,
            purpose="discovery query",
            timestamp=self.timestamp,
            query_or_operation=self.query,
            result_ref=self.result_ref or self.record_id,
            result_count=self.result_count,
            status=status,
            tool_call_id=self.record_id,
            query_family=self.family.value,
            error=self.error or None,
        )

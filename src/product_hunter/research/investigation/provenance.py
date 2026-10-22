"""The record of which calls and sources produced a finding.

The provenance is the audit trail of an investigation: a claim can only be
supported when every piece of its evidence resolves to a ref the provenance
knows.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


class InvestigationProvenance(BaseModel):
    """Which calls, sources and families a finding rests on."""

    model_config = ConfigDict(extra="forbid")

    run_id: str = Field(min_length=1)
    stage: str = Field(min_length=1)
    agent: str = Field(min_length=1)
    model: str | None = None
    tool_call_ids: list[str] = Field(default_factory=list)
    source_urls: list[str] = Field(default_factory=list)
    source_domains: list[str] = Field(default_factory=list)
    query_families: list[str] = Field(default_factory=list)
    recorded_at: datetime
    contract_fingerprint: str = ""

    @field_validator("model")
    @classmethod
    def _model_is_not_empty(cls, value: str | None) -> str | None:
        if value is not None and not value:
            raise ValueError("model must not be empty")
        return value

    @field_validator("recorded_at")
    @classmethod
    def _recorded_at_is_aware(cls, value: datetime) -> datetime:
        # A naive timestamp cannot be compared with an aware one, so it
        # silently switches off every freshness check downstream.
        if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
            raise ValueError("recorded_at must be timezone-aware")
        return value

    def known_refs(self) -> set[str]:
        """Return the refs this provenance can resolve."""
        return {ref for ref in (*self.tool_call_ids, *self.source_urls) if ref}

    def covers(self, ref: str) -> bool:
        """Report whether this provenance knows the ref."""
        return ref in self.known_refs()

    @property
    def has_evidence(self) -> bool:
        """Report whether this provenance points at anything at all."""
        return bool(self.known_refs())

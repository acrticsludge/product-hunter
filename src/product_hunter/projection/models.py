"""The records the projection passes between its modules.

Every record is closed: an extra key is refused, so a field the renderer
would silently ignore cannot be attached. A value the database does not hold
is ``None``, and the renderer writes that as "not recorded" rather than as
a zero.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class EvidenceRef(BaseModel):
    """One captured quote and the place it came from."""

    model_config = ConfigDict(extra="forbid")

    span_id: str
    quote: str
    stance: str
    live_status: str = "live"
    source_id: str
    source_type: str
    url: str
    community_id: str | None
    community_name: str
    author_id: str | None
    author_handle: str
    document_id: str
    captured_at: str
    content_hash: str


class EvidenceSummary(BaseModel):
    """What the evidence of one opportunity adds up to."""

    model_config = ConfigDict(extra="forbid")

    mentions: int
    canonical_documents: int
    independent_authors: int
    uncertain_authors: int
    independent_communities: int
    propagated_copies: int
    source_concentration: float


class Opportunity(BaseModel):
    """One opportunity and the evidence behind it."""

    model_config = ConfigDict(extra="forbid")

    opportunity_id: str
    title: str
    statement: str
    domain: str
    actor: str
    action: str
    object: str
    consequence: str
    severity: float | None
    frequency: str
    wtp_tier: str | None
    competition: float | None
    potential: float | None
    confidence: float | None
    trend: str
    uncertainty: str | None
    lifecycle: str
    recommended_experiment: str | None
    first_seen: str
    last_updated: str
    evidence: list[EvidenceRef]
    evidence_summary: EvidenceSummary
    claims: list[str]
    communities: list[str]


class SourceRef(BaseModel):
    """One source and the evidence captured from it."""

    model_config = ConfigDict(extra="forbid")

    source_id: str
    source_type: str
    terms_status: str
    enabled: bool
    evidence: list[EvidenceRef]
    communities: list[str]


class CommunityRef(BaseModel):
    """One community and the evidence captured in it."""

    model_config = ConfigDict(extra="forbid")

    community_id: str
    name: str
    url: str
    source_id: str
    evidence: list[EvidenceRef]
    authors: list[str]


class MarketReport(BaseModel):
    """The opportunities and the evidence of one domain."""

    model_config = ConfigDict(extra="forbid")

    market: str
    opportunity_ids: list[str]
    evidence: list[EvidenceRef]
    communities: list[str]
    sources: list[str]


class Experiment(BaseModel):
    """One validation experiment."""

    model_config = ConfigDict(extra="forbid")

    experiment_id: str
    opportunity_id: str | None
    title: str
    hypothesis: str | None
    status: str
    started_at: str | None
    completed_at: str | None
    result: str | None


class ResearchRequest(BaseModel):
    """One queued research request."""

    model_config = ConfigDict(extra="forbid")

    request_id: str
    title: str
    body: str | None
    status: str
    created_at: str


class Snapshot(BaseModel):
    """Everything one projection run reads."""

    model_config = ConfigDict(extra="forbid")

    opportunities: list[Opportunity]
    sources: list[SourceRef]
    communities: list[CommunityRef]
    markets: list[MarketReport]
    experiments: list[Experiment]
    requests: list[ResearchRequest]
    reference_date: str

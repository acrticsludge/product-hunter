"""The records the independence analysis reads and writes.

Every record forbids extra fields, so a caller cannot smuggle a number into
the analysis. The observations say what was collected, the relationships say
what was detected between two observations, and the metrics say what the graph
adds up to. Only ``analyze()`` builds a metrics record.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr

# The only proof that a metrics record came from analyze(). It is module
# private and is never exported, so a caller cannot produce one.
_ANALYSIS_TOKEN = object()


class AuthorObservation(BaseModel):
    """One account, as the collector saw it."""

    model_config = ConfigDict(extra="forbid")

    author_id: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    handle: str
    display_name: str | None = None
    identity_key: str | None = None
    profile_url: str | None = None
    community_ids: list[str] = Field(default_factory=list)
    doc_ids: list[str] = Field(default_factory=list)
    first_seen_at: str | None = None
    last_seen_at: str | None = None
    deletion_status: str = "active"


class CommunityObservation(BaseModel):
    """One community, as the collector saw it."""

    model_config = ConfigDict(extra="forbid")

    community_id: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    name: str
    url: str | None = None
    alias_of: str | None = None


class MentionObservation(BaseModel):
    """One mention: a document, not an extracted quote."""

    model_config = ConfigDict(extra="forbid")

    doc_id: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    community_id: str | None = None
    author_id: str | None = None
    url: str = ""
    text: str = ""
    text_sha256: str = ""
    published_at: str | None = None
    live_status: str = "live"
    deleted_at: str | None = None
    span_ids: list[str] = Field(default_factory=list)


class RelationshipProvenance(BaseModel):
    """Where a relationship came from, and what it rests on."""

    model_config = ConfigDict(extra="forbid")

    source_ids: list[str] = Field(default_factory=list)
    doc_ids: list[str] = Field(default_factory=list)
    span_ids: list[str] = Field(default_factory=list)
    detail: str = ""


class IdentitySignal(BaseModel):
    """One caller-supplied signal about a pair of accounts.

    A signal is evidence, not a verdict. The resolver applies the configured
    weight to it and decides the relation itself.
    """

    model_config = ConfigDict(extra="forbid")

    kind: str = Field(min_length=1)
    author_id: str = Field(min_length=1)
    related_author_id: str = Field(min_length=1)
    detail: str = ""


class AuthorIdentityLink(BaseModel):
    """A detected relation between two accounts."""

    model_config = ConfigDict(extra="forbid")

    link_id: str = Field(min_length=1)
    author_id: str = Field(min_length=1)
    related_author_id: str = Field(min_length=1)
    relation: str = Field(min_length=1)
    detection_method: str = Field(min_length=1)
    confidence: float = Field(ge=0.0, le=1.0)
    signals: list[str] = Field(default_factory=list)
    conflict: bool = False
    provenance: RelationshipProvenance
    detected_at: str = Field(min_length=1)


class CommunityLink(BaseModel):
    """A detected relation between two communities."""

    model_config = ConfigDict(extra="forbid")

    link_id: str = Field(min_length=1)
    community_id: str = Field(min_length=1)
    related_community_id: str = Field(min_length=1)
    relation: str = Field(min_length=1)
    detection_method: str = Field(min_length=1)
    confidence: float = Field(ge=0.0, le=1.0)
    provenance: RelationshipProvenance
    detected_at: str = Field(min_length=1)


class PropagationEdge(BaseModel):
    """A directed relation from one mention to a later one."""

    model_config = ConfigDict(extra="forbid")

    edge_id: str = Field(min_length=1)
    from_doc_id: str = Field(min_length=1)
    to_doc_id: str = Field(min_length=1)
    relation: str = Field(min_length=1)
    detection_method: str = Field(min_length=1)
    confidence: float = Field(ge=0.0, le=1.0)
    provenance: RelationshipProvenance
    detected_at: str = Field(min_length=1)


class AuthorCluster(BaseModel):
    """One identity group: a single account, or a set that may be one person."""

    model_config = ConfigDict(extra="forbid")

    cluster_id: str = Field(min_length=1)
    status: str = Field(min_length=1)
    member_author_ids: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    member_confidences: dict[str, float] = Field(default_factory=dict)
    canonical_author_id: str | None = None
    basis: list[str] = Field(default_factory=list)


class IndependenceMetrics(BaseModel):
    """What the graph adds up to. Only ``analyze()`` builds one."""

    model_config = ConfigDict(extra="forbid")

    _analysis_token: object = PrivateAttr(default=None)

    policy_version: str = Field(min_length=1)
    raw_mentions: int = Field(ge=0)
    canonical_content_count: int = Field(ge=0)
    independent_author_count: int = Field(ge=0)
    uncertain_author_count: int = Field(ge=0)
    independent_community_count: int = Field(ge=0)
    propagation_adjusted_evidence: int = Field(ge=0)
    source_concentration: float = Field(ge=0.0, le=1.0)
    author_cluster_concentration: float = Field(ge=0.0, le=1.0)
    community_concentration: float = Field(ge=0.0, le=1.0)
    propagation_chain_concentration: float = Field(ge=0.0, le=1.0)
    propagation_chain_count: int = Field(ge=0)
    unattributed_mentions: int = Field(ge=0)
    deleted_source_mentions: int = Field(ge=0)
    conflicting_identity_pairs: int = Field(ge=0)
    suspect_identity_groups: int = Field(ge=0)
    uncertain_identity_groups: int = Field(ge=0)
    independent_community_ids: list[str] = Field(default_factory=list)
    independent_author_cluster_ids: list[str] = Field(default_factory=list)

    @classmethod
    def _built_by_analysis(cls, **values: object) -> IndependenceMetrics:
        """The one constructor. A caller cannot build a metrics record."""
        metrics = cls.model_validate(values)
        metrics._analysis_token = _ANALYSIS_TOKEN
        return metrics

    def _came_from_analysis(self) -> bool:
        """Whether the analysis built this record."""
        return self._analysis_token is _ANALYSIS_TOKEN


class ConcentrationViolation(BaseModel):
    """One rule the evidence breaks, and by how much."""

    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1)
    detail: str
    observed: float
    limit: float


class ConcentrationVerdict(BaseModel):
    """The metrics and the rules they break."""

    model_config = ConfigDict(extra="forbid")

    satisfied: bool
    violations: list[ConcentrationViolation] = Field(default_factory=list)
    metrics: IndependenceMetrics

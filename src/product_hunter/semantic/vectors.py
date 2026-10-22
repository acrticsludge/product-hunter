"""The identity of an indexed entity and the payload every point carries.

A point is one entity: an evidence span, a pain point or a cluster. The
point id is a digest of the entity type and the entity id, so a changed
entity lands on the same point and replaces it instead of leaving the old
vector beside the new one.
"""

from __future__ import annotations

import hashlib
import uuid

from pydantic import BaseModel, ConfigDict, Field

ENTITY_EVIDENCE = "evidence"
ENTITY_PAIN_POINT = "pain_point"
ENTITY_CLUSTER = "cluster"
ENTITY_TYPES = (ENTITY_EVIDENCE, ENTITY_PAIN_POINT, ENTITY_CLUSTER)

# The separator between the parts of the point id digest.
_SEPARATOR = "\x1f"

# The payload fields that narrow a search.
_FILTER_FIELDS = (
    "entity_type",
    "source_id",
    "community_id",
    "domain",
    "cluster_id",
    "live_status",
    "space_id",
)


def vector_id(entity_type: str, entity_id: str) -> str:
    """The id of the point that holds one entity's vector.

    uuid(sha256(entity_type + "\\x1f" + entity_id)). The content hash is NOT
    in it: the point is the entity, and the content is what the payload says
    about the entity. So a re-index of a changed entity replaces its point.
    """
    if entity_type not in ENTITY_TYPES:
        raise ValueError(
            f"unknown entity type {entity_type!r}, expected one of "
            + ", ".join(ENTITY_TYPES)
        )
    if not entity_id.strip():
        raise ValueError("an entity id cannot be empty")
    basis = f"{entity_type}{_SEPARATOR}{entity_id}"
    digest = hashlib.sha256(basis.encode("utf-8")).hexdigest()[:32]
    return str(uuid.UUID(digest))


class VectorPayload(BaseModel):
    """What every point carries."""

    model_config = ConfigDict(extra="forbid")

    entity_id: str = Field(min_length=1)
    entity_type: str = Field(min_length=1)
    content_hash: str = Field(min_length=1)
    embedding_provider: str = Field(min_length=1)
    embedding_model: str = Field(min_length=1)
    embedding_model_version: str = Field(min_length=1)
    space_id: str = Field(min_length=1)
    dimensions: int = Field(gt=0)
    index_version: str = Field(min_length=1)
    created_at: str = Field(min_length=1)
    updated_at: str = Field(min_length=1)
    source_id: str = ""
    community_id: str = ""
    domain: str = ""
    cluster_id: str = ""
    live_status: str = "live"
    embedding_status: str = "embedded"

    def to_payload(self) -> dict[str, object]:
        """The dict Qdrant stores."""
        return self.model_dump()

    def same_content(self, other: VectorPayload) -> bool:
        """Whether the two payloads say the same thing apart from the stamp.

        The index skips an entity whose payload did not change, and a
        cluster that gained a member changed even though its vector did
        not, so the whole payload counts except the update stamp.
        """
        mine = self.to_payload()
        theirs = other.to_payload()
        mine.pop("updated_at", None)
        theirs.pop("updated_at", None)
        return mine == theirs


class SearchFilter(BaseModel):
    """The narrowing a search asks for. Each field narrows; none widens."""

    model_config = ConfigDict(extra="forbid")

    entity_type: str = ""
    source_id: str = ""
    community_id: str = ""
    domain: str = ""
    cluster_id: str = ""
    live_status: str = ""
    space_id: str = ""
    since: str = ""
    until: str = ""

    def to_qdrant_filter(self) -> dict[str, object]:
        """The Qdrant filter: one must clause per field that was set."""
        clauses: list[dict[str, object]] = []
        for field in _FILTER_FIELDS:
            value = getattr(self, field)
            if value:
                clauses.append({"key": field, "match": {"value": value}})
        window: dict[str, str] = {}
        if self.since:
            window["gte"] = self.since
        if self.until:
            window["lte"] = self.until
        if window:
            clauses.append({"key": "created_at", "range": window})
        if not clauses:
            return {}
        return {"must": clauses}

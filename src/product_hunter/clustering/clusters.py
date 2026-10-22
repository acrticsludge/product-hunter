"""The shapes of a cluster: the cluster, its members, its decisions.

Nothing here writes and nothing here reads. The engine proposes, the store
applies, and the rows between them keep the history of what a cluster meant
when a score was attached to it.
"""

from __future__ import annotations

import hashlib
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from product_hunter.normalization import Domain

# The separator between the parts of an id.
_SEPARATOR = "\x1f"


class ClusterStatus(str, Enum):
    """What happened to a cluster."""

    active = "active"
    merged = "merged"
    split = "split"
    dissolved = "dissolved"


class DecisionKind(str, Enum):
    """What a recorded decision is about."""

    duplicate = "duplicate"
    ambiguous = "ambiguous"
    adjudication = "adjudication"


class HeldReason(str, Enum):
    """Why a pain point was held instead of being clustered.

    A held pain point belongs to no cluster. It is never dropped: it stays
    visible in the result and in the store.

    A pain point that no candidate wanted is not held: it gets a cluster of
    its own, because there is nothing left to decide. Holding is for a pair
    the adjudicator could not decide.
    """

    ambiguous_unresolved = "ambiguous_unresolved"
    ambiguous_unadjudicated = "ambiguous_unadjudicated"
    adjudicator_failed = "adjudicator_failed"


class ClusterProfile(BaseModel):
    """The controlled fields of a cluster's anchor."""

    model_config = ConfigDict(extra="forbid")

    domain: Domain
    actor: str = Field(min_length=1)
    action: str = Field(min_length=1)
    object: str = Field(min_length=1)
    consequence: str = Field(min_length=1)


class Cluster(BaseModel):
    """One cluster: a set of pain points that name one problem."""

    model_config = ConfigDict(extra="forbid")

    cluster_id: str = Field(min_length=1)
    version: int = Field(ge=1)
    status: ClusterStatus
    label: str = Field(min_length=1)
    profile: ClusterProfile
    anchor_pain_id: str = Field(min_length=1)
    member_ids: list[str]


class Assignment(BaseModel):
    """A proposed membership: which pain point joins which cluster, and why.

    The store turns an assignment into a membership row. The row carries the
    interval the membership holds for, which only the store can know: it
    owns the clock.
    """

    model_config = ConfigDict(extra="forbid")

    cluster_id: str = Field(min_length=1)
    pain_id: str = Field(min_length=1)
    basis: str = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)


class Membership(BaseModel):
    """One membership row: a pain point in a cluster, over an interval.

    A row without a valid_to is the current membership. A membership that
    ends gets its valid_to set, and it is never deleted.
    """

    model_config = ConfigDict(extra="forbid")

    member_id: str = Field(min_length=1)
    cluster_id: str = Field(min_length=1)
    pain_id: str = Field(min_length=1)
    valid_from: str = Field(min_length=1)
    valid_to: str | None = None
    assignment_confidence: float = Field(ge=0, le=1)
    basis: str = Field(min_length=1)
    run_id: str | None = None


class ClusterVersion(BaseModel):
    """One snapshot of a cluster, as it was at one version."""

    model_config = ConfigDict(extra="forbid")

    cluster_id: str = Field(min_length=1)
    version: int = Field(ge=1)
    status: ClusterStatus
    label: str = ""
    profile: ClusterProfile | None = None
    member_ids: list[str] = Field(default_factory=list)
    member_count: int = Field(default=0, ge=0)
    run_id: str | None = None
    config_version: str | None = None
    space_id: str | None = None
    created_at: str = Field(min_length=1)


class ClusterDecision(BaseModel):
    """One decision the run made about a pair of pain points."""

    model_config = ConfigDict(extra="forbid")

    decision_id: str = Field(min_length=1)
    kind: DecisionKind
    relation: str = Field(min_length=1)
    pain_a: str = Field(min_length=1)
    pain_b: str | None = None
    cluster_id: str | None = None
    decision: str = Field(min_length=1)
    score: float | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)
    basis: str = Field(min_length=1)
    provider: str | None = None
    model: str | None = None
    model_version: str | None = None
    prompt_version: str | None = None


class NeighborCheck(BaseModel):
    """One pain point that was checked against a new cluster.

    A neighbour is recorded whether or not it was above the threshold: the
    check is there to show what else was near the cluster, not only what
    was close enough to join it.
    """

    model_config = ConfigDict(extra="forbid")

    cluster_id: str = Field(min_length=1)
    pain_id: str = Field(min_length=1)
    score: float
    above_threshold: bool


class HeldPainPoint(BaseModel):
    """A pain point that was not assigned to a cluster, and why."""

    model_config = ConfigDict(extra="forbid")

    pain_id: str = Field(min_length=1)
    reason: HeldReason
    detail: str = ""


class LineageEdge(BaseModel):
    """One edge of the cluster history."""

    model_config = ConfigDict(extra="forbid")

    edge_id: str = Field(min_length=1)
    from_cluster_id: str = Field(min_length=1)
    to_cluster_id: str = Field(min_length=1)
    relation: str = Field(min_length=1)
    reason: str | None = None
    run_id: str | None = None
    created_at: str = Field(min_length=1)


def hash_id(prefix: str, basis: str) -> str:
    """A deterministic id: the prefix and sixteen hex characters."""
    return prefix + hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16]


def provisional_cluster_id(anchor_pain_id: str) -> str:
    """The id the engine proposes for a new cluster.

    The store decides the durable identity, because the store knows what
    already exists. The proposal carries no timestamp, no run id and no
    counter, so the same input proposes the same id.
    """
    return hash_id("cl_", f"origin|{anchor_pain_id}")


def merged_cluster_id(left: str, right: str, anchor_pain_id: str) -> str:
    """The id of the successor of a merge."""
    smaller, larger = sorted((left, right))
    return hash_id("cl_", f"merge|{smaller}|{larger}|{anchor_pain_id}")


def split_cluster_id(parent_cluster_id: str, anchor_pain_id: str) -> str:
    """The id of one child of a split."""
    return hash_id("cl_", f"split|{parent_cluster_id}|{anchor_pain_id}")


def member_id(cluster_id: str, pain_id: str, valid_from: str) -> str:
    """The id of one membership row."""
    return hash_id("mem_", f"{cluster_id}|{pain_id}|{valid_from}")


def decision_id(
    kind: str,
    relation: str,
    pain_a: str,
    pain_b: str | None,
    decision: str,
    basis: str,
) -> str:
    """The id of one decision.

    The id carries the content and not the run, so the same decision about
    the same pair always has the same id. The store scopes the id of the
    row it writes to the run, so two runs that reach the same decision write
    two rows and the same run applied twice writes one.
    """
    basis_text = f"{kind}|{relation}|{pain_a}|{pain_b or ''}|{decision}|{basis}"
    return hash_id("dec_", basis_text)

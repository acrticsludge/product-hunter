"""Problem clustering: which pieces of evidence describe the same problem.

The package decides identity and keeps the history of what it decided. It
never decides whether a problem is a good opportunity, and it never counts
mentions, authors, communities or people: a raw mention count is not
evidence of independent people, and independence is another wave's job.

shortcut: the package ships no command line and no worker handler; the
caller is the research stage that runs the engine and applies the result to
the store.
"""

from __future__ import annotations

import hashlib
from datetime import datetime

from product_hunter.clustering.adjudication import (
    Adjudication,
    AdjudicationError,
    Adjudicator,
    LlmAdjudicator,
)
from product_hunter.clustering.clusters import (
    Cluster,
    ClusterDecision,
    ClusterProfile,
    ClusterStatus,
    Membership,
    NeighborCheck,
    DecisionKind,
)
from product_hunter.clustering.engine import ClusteringEngine, ClusteringResult
from product_hunter.clustering.problems import (
    CanonicalProblem,
    PainPoint,
    canonicalize,
)
from product_hunter.clustering.similarity import (
    Embedder,
    EmbeddingUnavailable,
    MixedVectorSpaceError,
    SentenceTransformerEmbedder,
    VectorSpace,
    cosine_similarity,
)
from product_hunter.config import ClusteringConfigError, ClusteringError
from product_hunter.research.evidence import ArtifactRecord
from product_hunter.research.predicates import (
    KIND_CLUSTER_NEIGHBORS_CHECKED,
    KIND_DUPLICATE_REVIEW,
    KIND_NEIGHBOR_CHECK,
)

# The artifact kinds the research contract's clustering stage declares. Two of
# the five have no constant in the predicates module, so they are named here.
KIND_CLUSTER_ASSIGNMENTS = "cluster_assignments"
KIND_SIMILARITY_CHECKS = "similarity_checks"

# The stage the artifacts belong to.
CLUSTERING_STAGE = "clustering"


def clustering_artifacts(
    result: ClusteringResult,
    *,
    run_id: str,
    tool_call_id: str,
    created_at: datetime,
    stage: str = CLUSTERING_STAGE,
) -> list[ArtifactRecord]:
    """The artifacts of one clustering run, in the kinds the gate reads.

    The gate counts an artifact that traces to a successful call of the
    stage, so every artifact carries the handle of the call that made it.
    The neighbour check is one artifact per cluster, so the metric that
    reads the smallest neighbour count of the run reads the smallest check
    of the run.
    """
    neighbors: dict[str, int] = {}
    for check in result.neighbor_checks:
        neighbors[check.cluster_id] = neighbors.get(check.cluster_id, 0) + 1

    artifacts = [
        _artifact(
            run_id=run_id,
            kind=KIND_CLUSTER_ASSIGNMENTS,
            key="all",
            count=len(result.assignments),
            tool_call_id=tool_call_id,
            stage=stage,
            created_at=created_at,
        ),
        _artifact(
            run_id=run_id,
            kind=KIND_SIMILARITY_CHECKS,
            key="all",
            count=sum(
                1 for decision in result.decisions if decision.score is not None
            ),
            tool_call_id=tool_call_id,
            stage=stage,
            created_at=created_at,
        ),
        _artifact(
            run_id=run_id,
            kind=KIND_DUPLICATE_REVIEW,
            key="all",
            count=sum(
                1
                for decision in result.decisions
                if decision.kind is DecisionKind.duplicate
            ),
            tool_call_id=tool_call_id,
            stage=stage,
            created_at=created_at,
        ),
        _artifact(
            run_id=run_id,
            kind=KIND_NEIGHBOR_CHECK,
            key="all",
            count=len(result.neighbor_checks),
            tool_call_id=tool_call_id,
            stage=stage,
            created_at=created_at,
        ),
    ]
    for cluster in result.clusters:
        artifacts.append(
            _artifact(
                run_id=run_id,
                kind=KIND_CLUSTER_NEIGHBORS_CHECKED,
                key=cluster.cluster_id,
                count=neighbors.get(cluster.cluster_id, 0),
                tool_call_id=tool_call_id,
                stage=stage,
                created_at=created_at,
            )
        )
    return artifacts


def _artifact(
    *,
    run_id: str,
    kind: str,
    key: str,
    count: int,
    tool_call_id: str,
    stage: str,
    created_at: datetime,
) -> ArtifactRecord:
    """One artifact of the clustering stage."""
    basis = f"{run_id}|{kind}|{key}"
    artifact_id = "art_" + hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16]
    return ArtifactRecord(
        artifact_id=artifact_id,
        stage=stage,
        kind=kind,
        ref=f"data/artifacts/{run_id}/{kind}.jsonl",
        item_count=count,
        produced_by_tool_call_id=tool_call_id,
        created_at=created_at,
    )


__all__ = [
    "Adjudication",
    "AdjudicationError",
    "Adjudicator",
    "CanonicalProblem",
    "Cluster",
    "ClusterDecision",
    "ClusterProfile",
    "ClusterStatus",
    "ClusteringConfigError",
    "ClusteringEngine",
    "ClusteringError",
    "ClusteringResult",
    "Embedder",
    "EmbeddingUnavailable",
    "LlmAdjudicator",
    "Membership",
    "MixedVectorSpaceError",
    "NeighborCheck",
    "PainPoint",
    "SentenceTransformerEmbedder",
    "VectorSpace",
    "canonicalize",
    "cosine_similarity",
]

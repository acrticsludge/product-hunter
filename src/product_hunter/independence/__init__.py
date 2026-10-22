"""Source independence and entity resolution.

The numbers are a function of the graph: ``analyze()`` builds them from the
graph and from nothing else. A model may supply a signal; it may never supply
a number, and a signal on its own can never merge two accounts.
"""

from __future__ import annotations

from product_hunter.independence.analytics import analyze, check_concentration
from product_hunter.independence.graph import (
    ContentGroup,
    IndependenceGraph,
    PropagationChain,
    build_graph,
)
from product_hunter.independence.identity import (
    MERGED,
    SUSPECT,
    author_confidence,
    build_author_clusters,
    canonical_community_ids,
    hash_identity_key,
    normalise_name,
    resolve_authors,
    resolve_communities,
)
from product_hunter.independence.policy import (
    NEGATIVE_SIGNALS,
    STRONG_SIGNALS,
    WEAK_SIGNALS,
    IdentityPolicy,
    IndependencePolicy,
    PolicyError,
    load_independence_policy,
    require_valid_policy,
    validate_policy,
)
from product_hunter.independence.propagation import (
    CONTENT_RELATIONS,
    CROSSPOST,
    QUOTE,
    REPOST,
    SYNDICATION,
    detect_propagation,
    longest_common_block,
    token_set,
    token_similarity,
    verbatim_ratio,
)
from product_hunter.independence.records import (
    AuthorCluster,
    AuthorIdentityLink,
    AuthorObservation,
    CommunityLink,
    CommunityObservation,
    ConcentrationVerdict,
    ConcentrationViolation,
    IdentitySignal,
    IndependenceMetrics,
    MentionObservation,
    PropagationEdge,
    RelationshipProvenance,
)

__all__ = [
    "CONTENT_RELATIONS",
    "CROSSPOST",
    "MERGED",
    "NEGATIVE_SIGNALS",
    "QUOTE",
    "REPOST",
    "STRONG_SIGNALS",
    "SUSPECT",
    "SYNDICATION",
    "WEAK_SIGNALS",
    "AuthorCluster",
    "AuthorIdentityLink",
    "AuthorObservation",
    "CommunityLink",
    "CommunityObservation",
    "ConcentrationVerdict",
    "ConcentrationViolation",
    "ContentGroup",
    "IdentityPolicy",
    "IdentitySignal",
    "IndependenceGraph",
    "IndependenceMetrics",
    "IndependencePolicy",
    "MentionObservation",
    "PolicyError",
    "PropagationChain",
    "PropagationEdge",
    "RelationshipProvenance",
    "analyze",
    "author_confidence",
    "build_author_clusters",
    "build_graph",
    "canonical_community_ids",
    "check_concentration",
    "detect_propagation",
    "hash_identity_key",
    "load_independence_policy",
    "longest_common_block",
    "normalise_name",
    "require_valid_policy",
    "resolve_authors",
    "resolve_communities",
    "token_set",
    "token_similarity",
    "validate_policy",
    "verbatim_ratio",
]

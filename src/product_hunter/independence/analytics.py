"""The metrics: what the graph adds up to, and the limits it must stay inside.

Every number here comes from the graph. A caller may not supply one, and a
model may not declare one.
"""

from __future__ import annotations

from collections import Counter

from product_hunter.independence.graph import IndependenceGraph
from product_hunter.independence.identity import RESOLVED, SUSPECT_GROUP
from product_hunter.independence.policy import IndependencePolicy
from product_hunter.independence.records import (
    ConcentrationVerdict,
    ConcentrationViolation,
    IndependenceMetrics,
)


def analyze(graph: IndependenceGraph, policy: IndependencePolicy) -> ConcentrationVerdict:
    """Build the metrics from the graph and check them against the policy."""
    return check_concentration(_metrics(graph, policy), policy)


def check_concentration(
    metrics: IndependenceMetrics, policy: IndependencePolicy
) -> ConcentrationVerdict:
    """The rules the metrics break, and the verdict on them."""
    concentration = policy.concentration
    violations: list[ConcentrationViolation] = []

    if metrics.independent_author_count < concentration.min_independent_authors:
        violations.append(
            ConcentrationViolation(
                code="independent_authors_below_minimum",
                detail=(
                    f"the graph has {metrics.independent_author_count} independent authors, "
                    f"the policy requires {concentration.min_independent_authors}"
                ),
                observed=float(metrics.independent_author_count),
                limit=float(concentration.min_independent_authors),
            )
        )
    if metrics.independent_community_count < concentration.min_independent_communities:
        violations.append(
            ConcentrationViolation(
                code="independent_communities_below_minimum",
                detail=(
                    f"the graph has {metrics.independent_community_count} independent "
                    f"communities, the policy requires {concentration.min_independent_communities}"
                ),
                observed=float(metrics.independent_community_count),
                limit=float(concentration.min_independent_communities),
            )
        )
    for code, observed, limit, name in (
        (
            "source_concentration_exceeded",
            metrics.source_concentration,
            concentration.max_single_source_share,
            "source",
        ),
        (
            "community_concentration_exceeded",
            metrics.community_concentration,
            concentration.max_single_community_share,
            "community",
        ),
        (
            "author_cluster_concentration_exceeded",
            metrics.author_cluster_concentration,
            concentration.max_single_author_cluster_share,
            "author cluster",
        ),
        (
            "propagation_chain_concentration_exceeded",
            metrics.propagation_chain_concentration,
            concentration.max_single_propagation_chain_share,
            "propagation chain",
        ),
    ):
        if observed > limit:
            violations.append(
                ConcentrationViolation(
                    code=code,
                    detail=(
                        f"one {name} holds {observed:.2f} of the mentions, "
                        f"the policy allows {limit:.2f}"
                    ),
                    observed=observed,
                    limit=limit,
                )
            )

    return ConcentrationVerdict(
        satisfied=not violations,
        violations=sorted(violations, key=lambda violation: violation.code),
        metrics=metrics,
    )


def _metrics(graph: IndependenceGraph, policy: IndependencePolicy) -> IndependenceMetrics:
    """Every number the report shows, computed from the graph."""
    raw = len(graph.mentions)
    threshold = policy.identity.confident_threshold

    independent_ids = {
        cluster.cluster_id
        for cluster in graph.author_clusters
        if cluster.status == RESOLVED and cluster.confidence >= threshold
    }
    uncertain = [
        cluster for cluster in graph.author_clusters if cluster.cluster_id not in independent_ids
    ]

    community_ids = set()
    for doc_id in graph.root_doc_ids():
        mention = graph.mentions[doc_id]
        if mention.community_id:
            community_ids.add(graph.canonical_community_of(mention.community_id))

    source_counts = Counter(mention.source_id for mention in graph.mentions.values())
    community_counts: Counter[str] = Counter()
    for mention in graph.mentions.values():
        if mention.community_id:
            community_counts[graph.canonical_community_of(mention.community_id)] += 1
    cluster_counts: Counter[str] = Counter()
    for mention in graph.mentions.values():
        cluster_id = graph.cluster_of_doc(mention.doc_id)
        if cluster_id:
            cluster_counts[cluster_id] += 1
        elif mention.author_id:
            # The author is not in the author list, so no group holds the
            # mention. It counts against that account, not against nobody.
            cluster_counts[f"author:{mention.author_id}"] += 1
    chain_counts = Counter(
        {chain.chain_id: len(chain.doc_ids) for chain in graph.propagation_chains}
    )

    return IndependenceMetrics._built_by_analysis(
        policy_version=policy.version,
        raw_mentions=raw,
        canonical_content_count=len(graph.content_groups),
        independent_author_count=len(independent_ids),
        uncertain_author_count=len(uncertain),
        independent_community_count=len(community_ids),
        propagation_adjusted_evidence=sum(
            1 for group in graph.content_groups if not group.derived
        ),
        source_concentration=_share(source_counts, raw),
        author_cluster_concentration=_share(cluster_counts, raw),
        community_concentration=_share(community_counts, raw),
        propagation_chain_concentration=_share(chain_counts, raw),
        propagation_chain_count=len(graph.propagation_chains),
        unattributed_mentions=sum(
            1 for mention in graph.mentions.values() if not mention.author_id
        ),
        deleted_source_mentions=sum(
            1 for mention in graph.mentions.values() if mention.live_status == "deleted"
        ),
        conflicting_identity_pairs=sum(1 for link in graph.identity_links if link.conflict),
        suspect_identity_groups=sum(
            1 for cluster in graph.author_clusters if cluster.status == SUSPECT_GROUP
        ),
        uncertain_identity_groups=len(uncertain),
        independent_community_ids=sorted(community_ids),
        independent_author_cluster_ids=sorted(independent_ids),
    )


def _share(counts: Counter[str], base: int) -> float:
    """The largest share one holder has of the base."""
    if base <= 0 or not counts:
        return 0.0
    return max(counts.values()) / base

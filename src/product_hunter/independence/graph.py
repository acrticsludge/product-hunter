"""The independence graph: mentions, identities, communities, and propagation.

The graph is data. It reads no clock, opens no file, and runs no query, so the
same observations always produce the same graph and the same numbers.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from product_hunter.independence.identity import (
    build_author_clusters,
    canonical_community_ids,
)
from product_hunter.independence.policy import IndependencePolicy
from product_hunter.independence.propagation import CONTENT_RELATIONS, QUOTE
from product_hunter.independence.records import (
    AuthorCluster,
    AuthorIdentityLink,
    AuthorObservation,
    CommunityLink,
    CommunityObservation,
    MentionObservation,
    PropagationEdge,
)


@dataclass(frozen=True)
class ContentGroup:
    """The mentions that carry the same content."""

    group_id: str
    doc_ids: list[str]
    derived: bool = False


@dataclass(frozen=True)
class PropagationChain:
    """The mentions that are connected by propagation."""

    chain_id: str
    doc_ids: list[str]


@dataclass(frozen=True)
class IndependenceGraph:
    """Everything the analytics read, and nothing they decide themselves."""

    mentions: dict[str, MentionObservation]
    authors: dict[str, AuthorObservation]
    communities: dict[str, CommunityObservation]
    identity_links: tuple[AuthorIdentityLink, ...]
    community_links: tuple[CommunityLink, ...]
    propagation_edges: tuple[PropagationEdge, ...]
    content_groups: tuple[ContentGroup, ...]
    author_clusters: tuple[AuthorCluster, ...]
    propagation_chains: tuple[PropagationChain, ...]
    canonical_communities: dict[str, str]
    group_of_doc: dict[str, str]
    chain_of_doc: dict[str, str]
    cluster_of_author: dict[str, str]

    def canonical_community_of(self, community_id: str) -> str:
        """The community that stands for the group this community belongs to."""
        return self.canonical_communities.get(community_id, community_id)

    def cluster_of_doc(self, doc_id: str) -> str | None:
        """The identity group the mention's author belongs to, if any."""
        mention = self.mentions.get(doc_id)
        if mention is None or not mention.author_id:
            return None
        return self.cluster_of_author.get(mention.author_id)

    def root_doc_ids(self) -> list[str]:
        """The mentions that carry a content of their own, not a derived one."""
        return sorted(
            doc_id
            for group in self.content_groups
            if not group.derived
            for doc_id in group.doc_ids
        )


def build_graph(
    mentions: Sequence[MentionObservation],
    authors: Sequence[AuthorObservation],
    communities: Sequence[CommunityObservation],
    identity_links: Sequence[AuthorIdentityLink],
    community_links: Sequence[CommunityLink],
    edges: Sequence[PropagationEdge],
    policy: IndependencePolicy,
) -> IndependenceGraph:
    """Assemble the graph from the observations and the detected relationships."""
    mention_by_id = {mention.doc_id: mention for mention in mentions}
    author_by_id = {author.author_id: author for author in authors}
    community_by_id = {community.community_id: community for community in communities}

    content_edges = [
        (edge.from_doc_id, edge.to_doc_id)
        for edge in edges
        if edge.relation in CONTENT_RELATIONS
    ]
    group_members = _components(sorted(mention_by_id), content_edges)
    group_of_doc = {
        doc_id: _hash_id("cg_", "|".join(members))
        for members in group_members
        for doc_id in members
    }

    derived_groups: set[str] = set()
    for edge in edges:
        if edge.relation != QUOTE:
            continue
        source_group = group_of_doc.get(edge.from_doc_id)
        target_group = group_of_doc.get(edge.to_doc_id)
        if (
            source_group is not None
            and target_group is not None
            and source_group != target_group
        ):
            derived_groups.add(target_group)

    content_groups = tuple(
        ContentGroup(
            group_id=_hash_id("cg_", "|".join(members)),
            doc_ids=sorted(members),
            derived=_hash_id("cg_", "|".join(members)) in derived_groups,
        )
        for members in group_members
    )

    chain_members = _components(
        sorted(mention_by_id),
        [(edge.from_doc_id, edge.to_doc_id) for edge in edges],
    )
    propagation_chains = tuple(
        PropagationChain(
            chain_id=_hash_id("pc_", "|".join(members)),
            doc_ids=sorted(members),
        )
        for members in chain_members
        if len(members) > 1
    )
    chain_of_doc = {
        doc_id: _hash_id("pc_", "|".join(members))
        for members in chain_members
        for doc_id in members
    }

    anonymous_groups = [
        group.group_id
        for group in content_groups
        if not any(
            mention_by_id[doc_id].author_id for doc_id in group.doc_ids
        )
    ]
    author_clusters = build_author_clusters(
        list(author_by_id.values()),
        list(identity_links),
        policy,
        anonymous_groups=anonymous_groups,
    )
    cluster_of_author = {
        author_id: cluster.cluster_id
        for cluster in author_clusters
        for author_id in cluster.member_author_ids
    }

    return IndependenceGraph(
        mentions=mention_by_id,
        authors=author_by_id,
        communities=community_by_id,
        identity_links=tuple(identity_links),
        community_links=tuple(community_links),
        propagation_edges=tuple(edges),
        content_groups=content_groups,
        author_clusters=tuple(author_clusters),
        propagation_chains=propagation_chains,
        canonical_communities=canonical_community_ids(
            list(community_by_id.values()), list(community_links)
        ),
        group_of_doc=group_of_doc,
        chain_of_doc=chain_of_doc,
        cluster_of_author=cluster_of_author,
    )


def _components(
    nodes: Sequence[str], edges: Iterable[tuple[str, str]]
) -> list[list[str]]:
    """The connected components over the edges, one list per component."""
    parent = {node: node for node in nodes}

    def find(node: str) -> str:
        while parent[node] != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node

    for left, right in edges:
        if left not in parent or right not in parent:
            continue
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            parent[max(left_root, right_root)] = min(left_root, right_root)

    members_by_root: dict[str, list[str]] = {}
    for node in sorted(parent):
        members_by_root.setdefault(find(node), []).append(node)
    return [members for _, members in sorted(members_by_root.items())]


def _hash_id(prefix: str, basis: str) -> str:
    return prefix + hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16]

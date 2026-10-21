"""Author and community resolution: the signals, the score, the caps, the edges.

A caller may supply a signal; the resolver applies the configured weight to it
and decides the relation itself. A display-name match alone produces no edge,
a contradicted pair is never a merge, and a model signal alone can never merge
two accounts.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterator, Sequence
from typing import NamedTuple

from product_hunter.independence.policy import (
    NEGATIVE_SIGNALS,
    STRONG_SIGNALS,
    IdentityPolicy,
    IndependencePolicy,
)
from product_hunter.independence.records import (
    AuthorCluster,
    AuthorIdentityLink,
    AuthorObservation,
    CommunityLink,
    CommunityObservation,
    IdentitySignal,
    MentionObservation,
    RelationshipProvenance,
)

MERGED = "same-person-merged"
SUSPECT = "same-person-suspect"

# The status of an identity group. A group whose edges are all merges is
# resolved; a group that holds a suspect edge is suspect; an anonymous content
# group holds no account at all.
RESOLVED = "resolved"
SUSPECT_GROUP = "suspect"
ANONYMOUS = "anonymous"

# The signals a caller may supply. They are evidence, never a verdict. The
# signals the resolver derives from the observations are not on this list: a
# strong signal decides a merge, and that stays with the data.
SUPPLIED_SIGNALS = (
    "cross_linking",
    "model_signal",
    "signature_reuse",
    "explicit_disavow",
)

# The order a signal is reported in: the strongest first, so the detection
# method names the evidence that decided the edge.
_METHOD_ORDER = (
    "identity_key",
    "same_platform_handle",
    "cross_linking",
    "signature_reuse",
    "model_signal",
    "community_overlap",
    "display_name",
    "identity_key_conflict",
    "explicit_disavow",
)

_DECORATION = re.compile(r"[@.]")

_IDENTITY_KEY_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


class _Compare(NamedTuple):
    """The form one account is compared in, worked out once.

    The pair loop reads these instead of folding the names and hashing the
    identity key again for every pair it looks at.
    """

    handle: str
    display_name: str
    identity_key: str | None


def normalise_name(value: str) -> str:
    """Fold a handle or a display name to the form two accounts compare in.

    ``Jane Doe``, ``jane.doe`` and ``Jane  Doe`` all fold to the same string.
    A space is a word boundary, so ``ab cd`` stays distinct from ``a bcd``.
    """
    folded = _DECORATION.sub(" ", value.strip().casefold())
    return " ".join(folded.split())


def hash_identity_key(raw_key: str) -> str:
    """The stored form of an identity key: a tagged hash, never the key.

    The value is tagged so a stored hash is recognisable and hashing it again
    is a no-op. The digest is domain separated, so the stored value is not the
    plain hash of the key and a low-entropy key is not one dictionary lookup
    away from the raw.
    """
    if _IDENTITY_KEY_RE.match(raw_key):
        return raw_key
    digest = hashlib.sha256(
        f"independence-identity-key:v1|{raw_key}".encode("utf-8")
    ).hexdigest()
    return f"sha256:{digest}"


def resolve_authors(
    authors: Sequence[AuthorObservation],
    mentions: Sequence[MentionObservation],
    policy: IndependencePolicy,
    *,
    detected_at: str,
    signals: Sequence[IdentitySignal] = (),
) -> list[AuthorIdentityLink]:
    """Detect the relation between every pair of accounts.

    Every unordered pair of distinct accounts is a candidate, and the pairs are
    walked in a fixed order, so the output order is stable.
    """
    identity = policy.identity
    by_id = {author.author_id: author for author in authors}
    ordered = [by_id[key] for key in sorted(by_id)]
    # The names an account is compared in, worked out once rather than once
    # per pair.
    compare = {
        author_id: _Compare(
            handle=normalise_name(author.handle),
            display_name=normalise_name(author.display_name or ""),
            identity_key=(
                hash_identity_key(author.identity_key) if author.identity_key else None
            ),
        )
        for author_id, author in by_id.items()
    }
    doc_ids, community_ids, span_ids = _mentions_by_author(mentions)
    for author in ordered:
        if author.community_ids:
            community_ids.setdefault(author.author_id, set()).update(author.community_ids)
    supplied = _supplied_by_pair(signals)

    links: list[AuthorIdentityLink] = []
    for left, right in _pairs(ordered):
        fired = _fired_signals(left, right, community_ids, supplied, compare)
        if not fired:
            continue
        score, conflict = _score(fired, identity)
        positive = bool(fired - set(NEGATIVE_SIGNALS))
        relation = _relation(score, conflict, positive, identity)
        if relation is None:
            continue
        links.append(
            _identity_link(
                left,
                right,
                fired,
                score,
                conflict,
                relation,
                doc_ids,
                span_ids,
                detected_at,
            )
        )
    return links


def author_confidence(
    author: AuthorObservation,
    policy: IndependencePolicy,
    *,
    conflicted: bool = False,
) -> float:
    """The confidence that one account is one person.

    An account in a contradicted pair is capped: the pair says the accounts
    are different people, so the resolver does not trust either one.
    """
    if not author.handle.strip():
        return 0.0
    identity = policy.identity
    score = identity.standalone_handle_confidence
    if author.identity_key:
        score += identity.identity_key_bonus
    if author.profile_url:
        score += identity.profile_url_bonus
    score = min(score, 1.0)
    if conflicted:
        score = min(score, identity.conflict_score_cap)
    return _round(score)


def build_author_clusters(
    authors: Sequence[AuthorObservation],
    links: Sequence[AuthorIdentityLink],
    policy: IndependencePolicy,
    *,
    anonymous_groups: Sequence[str] = (),
) -> list[AuthorCluster]:
    """Group the accounts into identity groups.

    The groups are the connected components over the merge and suspect edges.
    A group whose edges are all merges is resolved; a group that holds a
    suspect edge is suspect, and is counted once, as one possible person.
    """
    by_id = {author.author_id: author for author in authors}
    conflicted = {
        author_id
        for link in links
        if link.conflict
        for author_id in (link.author_id, link.related_author_id)
    }
    confidence = {
        author_id: author_confidence(
            author, policy, conflicted=author_id in conflicted
        )
        for author_id, author in by_id.items()
    }

    parent = {author_id: author_id for author_id in by_id}

    def find(node: str) -> str:
        while parent[node] != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node

    def union(left: str, right: str) -> None:
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            parent[max(left_root, right_root)] = min(left_root, right_root)

    for link in links:
        if link.author_id in parent and link.related_author_id in parent:
            union(link.author_id, link.related_author_id)

    members_by_root: dict[str, list[str]] = {}
    for author_id in sorted(by_id):
        members_by_root.setdefault(find(author_id), []).append(author_id)

    # Bucket the links by member once, so a cluster reads its own links instead
    # of scanning every link in the graph.
    links_by_member: dict[str, list[AuthorIdentityLink]] = {}
    for link in links:
        if link.author_id in parent and link.related_author_id in parent:
            links_by_member.setdefault(link.author_id, []).append(link)
            links_by_member.setdefault(link.related_author_id, []).append(link)

    clusters: list[AuthorCluster] = []
    for members in members_by_root.values():
        inner = list(
            {
                link.link_id: link
                for member in members
                for link in links_by_member.get(member, ())
            }.values()
        )
        status = (
            SUSPECT_GROUP
            if any(link.relation == SUSPECT for link in inner)
            else RESOLVED
        )
        edge_confidence = max(
            (link.confidence for link in inner if link.relation == MERGED), default=0.0
        )
        cluster_confidence = max(
            max((confidence[member] for member in members), default=0.0), edge_confidence
        )
        canonical = min(members, key=lambda member: (-confidence[member], member))
        clusters.append(
            AuthorCluster(
                cluster_id=_hash_id("ac_", "|".join(members)),
                status=status,
                member_author_ids=sorted(members),
                confidence=cluster_confidence,
                member_confidences={
                    member: confidence[member] for member in sorted(members)
                },
                canonical_author_id=canonical,
                basis=sorted(link.link_id for link in inner),
            )
        )

    for group_id in sorted(anonymous_groups):
        clusters.append(
            AuthorCluster(
                cluster_id=_hash_id("ac_", f"anonymous|{group_id}"),
                status=ANONYMOUS,
                member_author_ids=[],
                confidence=0.0,
                canonical_author_id=None,
                basis=[],
            )
        )
    return sorted(clusters, key=lambda cluster: cluster.cluster_id)


def resolve_communities(
    communities: Sequence[CommunityObservation],
    policy: IndependencePolicy,
    *,
    detected_at: str,
) -> list[CommunityLink]:
    """Detect the relation between every pair of communities."""
    by_id = {community.community_id: community for community in communities}
    ordered = [by_id[key] for key in sorted(by_id)]
    links: list[CommunityLink] = []

    for community in ordered:
        if not community.alias_of:
            continue
        links.append(
            _community_link(
                community.community_id,
                community.alias_of,
                "same-community-merged",
                "explicit_alias",
                1.0,
                community,
                by_id.get(community.alias_of),
                detected_at,
            )
        )

    aliased = {
        frozenset({link.community_id, link.related_community_id}) for link in links
    }
    for left, right in _pairs(ordered):
        if left.source_id == right.source_id:
            continue
        if frozenset({left.community_id, right.community_id}) in aliased:
            continue
        left_name = normalise_name(left.name)
        right_name = normalise_name(right.name)
        if not left_name or left_name != right_name:
            continue
        links.append(
            _community_link(
                left.community_id,
                right.community_id,
                "same-community-suspect",
                "same_name_cross_source",
                policy.identity.cross_source_name_weight,
                left,
                right,
                detected_at,
            )
        )
    return sorted(
        links,
        key=lambda link: (
            link.community_id,
            link.related_community_id,
            link.detection_method,
        ),
    )


def canonical_community_ids(
    communities: Sequence[CommunityObservation],
    links: Sequence[CommunityLink],
) -> dict[str, str]:
    """Map every community to the one that stands for its group.

    A merged group stands for the alias target; any other group stands for its
    smallest community id.
    """
    by_id = {community.community_id: community for community in communities}
    parent = {community_id: community_id for community_id in by_id}

    def find(node: str) -> str:
        while parent[node] != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node

    for link in links:
        if link.community_id in parent and link.related_community_id in parent:
            left, right = find(link.community_id), find(link.related_community_id)
            if left != right:
                parent[max(left, right)] = min(left, right)

    members_by_root: dict[str, list[str]] = {}
    for community_id in sorted(by_id):
        members_by_root.setdefault(find(community_id), []).append(community_id)

    canonical: dict[str, str] = {}
    for members in members_by_root.values():
        targets = [
            target
            for target in (_alias_target(member, by_id) for member in members)
            if target is not None
        ]
        chosen = min(targets) if targets else min(members)
        for member in members:
            canonical[member] = chosen
    return canonical


def _identity_link(
    left: AuthorObservation,
    right: AuthorObservation,
    fired: set[str],
    score: float,
    conflict: bool,
    relation: str,
    doc_ids: dict[str, set[str]],
    span_ids: dict[str, set[str]],
    detected_at: str,
) -> AuthorIdentityLink:
    method = next(name for name in _METHOD_ORDER if name in fired)
    return AuthorIdentityLink(
        link_id=_hash_id(
            "ail_", f"{left.author_id}|{right.author_id}|{method}|{relation}"
        ),
        author_id=left.author_id,
        related_author_id=right.author_id,
        relation=relation,
        detection_method=method,
        confidence=score,
        signals=sorted(fired),
        conflict=conflict,
        provenance=RelationshipProvenance(
            source_ids=sorted({left.source_id, right.source_id}),
            doc_ids=sorted(
                set(left.doc_ids)
                | set(right.doc_ids)
                | doc_ids.get(left.author_id, set())
                | doc_ids.get(right.author_id, set())
            ),
            span_ids=sorted(
                span_ids.get(left.author_id, set()) | span_ids.get(right.author_id, set())
            ),
            detail="matched on " + ", ".join(name for name in _METHOD_ORDER if name in fired),
        ),
        detected_at=detected_at,
    )


def _community_link(
    community_id: str,
    related_community_id: str,
    relation: str,
    method: str,
    confidence: float,
    left: CommunityObservation,
    right: CommunityObservation | None,
    detected_at: str,
) -> CommunityLink:
    source_ids = {left.source_id}
    if right is not None:
        source_ids.add(right.source_id)
    return CommunityLink(
        link_id=_hash_id("cl_", f"{community_id}|{related_community_id}|{method}"),
        community_id=community_id,
        related_community_id=related_community_id,
        relation=relation,
        detection_method=method,
        confidence=confidence,
        provenance=RelationshipProvenance(
            source_ids=sorted(source_ids),
            detail=(
                "explicit alias"
                if method == "explicit_alias"
                else "same name on two sources"
            ),
        ),
        detected_at=detected_at,
    )


def _fired_signals(
    left: AuthorObservation,
    right: AuthorObservation,
    community_ids: dict[str, set[str]],
    supplied: dict[frozenset[str], set[str]],
    compare: dict[str, _Compare],
) -> set[str]:
    """Every signal that fired for one pair, positive and negative."""
    fired: set[str] = set()
    left_names = compare[left.author_id]
    right_names = compare[right.author_id]
    if left_names.identity_key and right_names.identity_key:
        if left_names.identity_key == right_names.identity_key:
            fired.add("identity_key")
        else:
            fired.add("identity_key_conflict")
    if (
        left.source_id == right.source_id
        and left_names.handle
        and left_names.handle == right_names.handle
    ):
        fired.add("same_platform_handle")
    if left_names.display_name and left_names.display_name == right_names.display_name:
        fired.add("display_name")
    if community_ids.get(left.author_id, set()) & community_ids.get(right.author_id, set()):
        fired.add("community_overlap")
    fired |= supplied.get(frozenset({left.author_id, right.author_id}), set())
    return fired


def _score(fired: set[str], identity: IdentityPolicy) -> tuple[float, bool]:
    """The pair's score and whether a negative signal contradicted it.

    A contradiction with a positive signal beside it is recorded, not dropped:
    the pair is a suspect at the conflict cap, and the edge carries the
    conflict so the analytics can count it.
    """
    positive_names = [name for name in fired if name not in NEGATIVE_SIGNALS]
    conflict = any(name in NEGATIVE_SIGNALS for name in fired)
    if conflict and positive_names:
        return _round(identity.conflict_score_cap), conflict
    positive = sum(identity.signal_weights.get(name, 0.0) for name in positive_names)
    negative = sum(
        identity.negative_signal_weights.get(name, 0.0)
        for name in fired
        if name in NEGATIVE_SIGNALS
    )
    score = max(0.0, min(1.0, positive - negative))
    if not fired & set(STRONG_SIGNALS):
        score = min(score, identity.suspect_threshold)
    return _round(score), conflict


def _relation(
    score: float, conflict: bool, positive: bool, identity: IdentityPolicy
) -> str | None:
    """The relation a score earns, or None when the pair earns no edge."""
    if conflict:
        # Only negative signals: two accounts with different identity keys are
        # two strangers, not a contradiction.
        return SUSPECT if positive else None
    if score >= identity.merge_threshold:
        return MERGED
    if score >= identity.suspect_threshold:
        return SUSPECT
    return None


def _supplied_by_pair(
    signals: Sequence[IdentitySignal],
) -> dict[frozenset[str], set[str]]:
    """The caller-supplied signals, keyed by the unordered pair they name."""
    supplied: dict[frozenset[str], set[str]] = {}
    for signal in signals:
        if signal.kind not in SUPPLIED_SIGNALS:
            raise ValueError(
                f"signal kind {signal.kind!r} may not be supplied by a caller; "
                "a supplied signal is evidence, not a verdict"
            )
        key = frozenset({signal.author_id, signal.related_author_id})
        supplied.setdefault(key, set()).add(signal.kind)
    return supplied


def _mentions_by_author(
    mentions: Sequence[MentionObservation],
) -> tuple[dict[str, set[str]], dict[str, set[str]], dict[str, set[str]]]:
    """The doc ids, community ids, and span ids each account produced."""
    doc_ids: dict[str, set[str]] = {}
    community_ids: dict[str, set[str]] = {}
    span_ids: dict[str, set[str]] = {}
    for mention in mentions:
        if not mention.author_id:
            continue
        doc_ids.setdefault(mention.author_id, set()).add(mention.doc_id)
        if mention.community_id:
            community_ids.setdefault(mention.author_id, set()).add(mention.community_id)
        if mention.span_ids:
            span_ids.setdefault(mention.author_id, set()).update(mention.span_ids)
    return doc_ids, community_ids, span_ids


def _alias_target(
    community_id: str, by_id: dict[str, CommunityObservation]
) -> str | None:
    """The community an alias chain ends at, or None when there is no alias."""
    community = by_id[community_id]
    if not community.alias_of:
        return None
    seen = {community.community_id}
    current = community
    while current.alias_of:
        target = by_id.get(current.alias_of)
        if target is None:
            return current.alias_of
        if target.community_id in seen:
            return None
        seen.add(target.community_id)
        current = target
    return current.community_id


def _pairs[T](ordered: Sequence[T]) -> Iterator[tuple[T, T]]:
    """Every unordered pair, in a fixed order."""
    for index in range(len(ordered)):
        for other in range(index + 1, len(ordered)):
            yield ordered[index], ordered[other]


def _hash_id(prefix: str, basis: str) -> str:
    return prefix + hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16]


def _round(value: float) -> float:
    """A score with the binary noise taken off, so a report reads cleanly."""
    return round(value, 6)

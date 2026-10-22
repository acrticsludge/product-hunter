"""Propagation detection: repost, crosspost, quote, and syndication.

Two mentions are compared on their text. The measures are the token-set
similarity and the longest verbatim block. The classification takes the first
rule that fires, in the order the spec fixes, and the edge points from the
earlier mention to the later one.

The matcher is quadratic in the length of the two texts, so a cheap screen
runs in front of it: two texts that share no run of the length a quote needs
cannot earn a quote, and never pay for the matcher.
"""

from __future__ import annotations

import difflib
import hashlib
import math
import re
from collections.abc import Iterator, Sequence
from datetime import datetime

from product_hunter.independence.policy import IndependencePolicy
from product_hunter.independence.records import (
    MentionObservation,
    PropagationEdge,
    RelationshipProvenance,
)

REPOST = "repost"
CROSSPOST = "crosspost"
QUOTE = "quote"
SYNDICATION = "syndication"

# The relations that carry the same content from one mention to the next. A
# quote is not one of them: a quote adds the quoting author's own words.
CONTENT_RELATIONS = (REPOST, CROSSPOST, SYNDICATION)

# Unicode-aware: a post in any script is tokenised, not split into nothing.
_TOKEN = re.compile(r"[\W_]+", re.UNICODE)


def token_set(text: str) -> set[str]:
    """The words of a text, lowercased, with the short ones dropped."""
    return {token for token in _TOKEN.split(text.casefold()) if len(token) >= 2}


def token_similarity(left: str, right: str) -> float:
    """The Jaccard index of the two token sets."""
    return _similarity(token_set(left), token_set(right), texts_equal=left == right)


def _similarity(
    left_tokens: set[str], right_tokens: set[str], *, texts_equal: bool
) -> float:
    """The Jaccard index of two token sets, already built."""
    if not left_tokens and not right_tokens:
        return 1.0 if texts_equal else 0.0
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / len(left_tokens | right_tokens)


def longest_common_block(left: str, right: str) -> int:
    """The length of the longest common contiguous block of the two texts."""
    if not left or not right:
        return 0
    matcher = difflib.SequenceMatcher(None, left, right, autojunk=False)
    return matcher.find_longest_match(0, len(left), 0, len(right)).size


def verbatim_ratio(left: str, right: str) -> float:
    """The longest common block as a share of the shorter text."""
    shorter = min(len(left), len(right))
    if shorter == 0:
        return 0.0
    return longest_common_block(left, right) / shorter


def _shingle_chars(policy: IndependencePolicy) -> int:
    """The length of the run two texts must share before a quote is possible.

    A quote needs a verbatim block of at least
    ``quote_min_overlap * min_comparable_chars`` characters, and a common
    block of that length holds a common run of that length. The screen is
    never longer than the shortest block a quote can have, so it never
    rejects a real quote.
    """
    required = (
        policy.propagation.quote_min_overlap * policy.propagation.min_comparable_chars
    )
    # Floor, not ceil: a run one character longer than the shortest block a
    # quote can have would reject a quote that earns its edge.
    return max(1, math.floor(required))


def _shingles(text: str, size: int) -> frozenset[int]:
    """The hashes of every run of ``size`` characters in the text.

    Two texts that share a run of this length share a shingle hash, because
    equal strings hash equal. A pair whose sets do not intersect cannot share
    a run of that length.

    The hash only has to agree with itself inside one run of the analysis, so
    the salted value of the platform hash is enough: a collision lets a pair
    through to the matcher, which decides it, and never hides a quote.
    """
    if len(text) < size:
        return frozenset()
    return frozenset(
        hash(text[index : index + size]) for index in range(len(text) - size + 1)
    )


def detect_propagation(
    mentions: Sequence[MentionObservation],
    policy: IndependencePolicy,
    *,
    detected_at: str,
) -> list[PropagationEdge]:
    """Detect every propagation edge between the mentions.

    The pairs are walked in a fixed order and every edge carries the measure
    that fired, so the same mentions always produce the same edges.
    """
    if len(mentions) > policy.propagation.max_scope_mentions:
        raise ValueError(
            f"the scope holds {len(mentions)} mentions, the policy allows "
            f"{policy.propagation.max_scope_mentions}; split the scope and run it twice"
        )
    ordered = sorted(mentions, key=lambda mention: mention.doc_id)
    limit = policy.propagation.max_text_chars
    texts = {mention.doc_id: mention.text[:limit] for mention in ordered}
    tokens = {mention.doc_id: token_set(texts[mention.doc_id]) for mention in ordered}
    size = _shingle_chars(policy)
    shingles = {
        mention.doc_id: _shingles(texts[mention.doc_id], size) for mention in ordered
    }
    edges: list[PropagationEdge] = []
    for left, right in _pairs(ordered):
        relation, method, confidence = _classify(
            left,
            right,
            texts[left.doc_id],
            texts[right.doc_id],
            tokens[left.doc_id],
            tokens[right.doc_id],
            shingles[left.doc_id],
            shingles[right.doc_id],
            policy,
        )
        if relation is None:
            continue
        earlier, later = _direction(left, right)
        edges.append(
            PropagationEdge(
                edge_id=_hash_id("pe_", f"{earlier.doc_id}|{later.doc_id}|{relation}"),
                from_doc_id=earlier.doc_id,
                to_doc_id=later.doc_id,
                relation=relation,
                detection_method=method,
                confidence=min(1.0, confidence),
                provenance=RelationshipProvenance(
                    source_ids=sorted({earlier.source_id, later.source_id}),
                    doc_ids=[earlier.doc_id, later.doc_id],
                    span_ids=sorted(set(earlier.span_ids) | set(later.span_ids)),
                    detail=_detail(relation, confidence),
                ),
                detected_at=detected_at,
            )
        )
    return sorted(edges, key=lambda edge: (edge.from_doc_id, edge.to_doc_id, edge.relation))


def _classify(
    left: MentionObservation,
    right: MentionObservation,
    left_text: str,
    right_text: str,
    left_tokens: set[str],
    right_tokens: set[str],
    left_shingles: frozenset[int],
    right_shingles: frozenset[int],
    policy: IndependencePolicy,
) -> tuple[str | None, str, float]:
    """The relation a pair earns, the method that found it, and its confidence.

    The texts and token sets are the ones the caller already bounded and
    computed, so a pair costs a set intersection rather than two tokenisations.
    The shingle sets are the screen in front of the matcher: they are a
    necessary condition for a quote, so a pair that shares no run of that
    length cannot earn one and never pays for the matcher.
    """
    propagation = policy.propagation
    if not left_text.strip() or not right_text.strip():
        return None, "", 0.0
    exact_copy = bool(left.text_sha256) and left.text_sha256 == right.text_sha256
    if not exact_copy and (
        len(left_text) < propagation.min_comparable_chars
        or len(right_text) < propagation.min_comparable_chars
    ):
        return None, "", 0.0

    similarity = _similarity(
        left_tokens, right_tokens, texts_equal=left_text == right_text
    )
    if similarity >= propagation.repost_similarity_threshold:
        if left.source_id != right.source_id:
            return CROSSPOST, "same_content_cross_source", similarity
        return REPOST, "same_content_same_source", similarity

    if left_shingles.isdisjoint(right_shingles):
        ratio = 0.0
    else:
        ratio = verbatim_ratio(left_text, right_text)
    if ratio >= propagation.quote_min_overlap:
        return QUOTE, "verbatim_quote_overlap", ratio

    if similarity >= propagation.syndication_similarity_threshold:
        return SYNDICATION, "near_duplicate_content", similarity
    return None, "", 0.0


def _direction(
    left: MentionObservation, right: MentionObservation
) -> tuple[MentionObservation, MentionObservation]:
    """The earlier mention and the later one.

    Earlier is the earlier timestamp when both parse, compared as instants so
    a mixed-precision pair orders by time and not by codepoint. Otherwise the
    pair is ordered by doc id, which is deterministic either way.
    """
    left_at = _timestamp(left.published_at)
    right_at = _timestamp(right.published_at)
    if left_at is not None and right_at is not None and left_at != right_at:
        try:
            return (left, right) if left_at < right_at else (right, left)
        except TypeError:
            # One carries a zone and the other does not, so the two instants
            # are not comparable; the doc id decides.
            pass
    return (left, right) if left.doc_id < right.doc_id else (right, left)


def _timestamp(value: str | None) -> datetime | None:
    """A timestamp as a datetime, or None when it is missing or unparsable."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _detail(relation: str, confidence: float) -> str:
    """A short note naming the measure that fired."""
    if relation == QUOTE:
        return f"verbatim overlap {confidence:.2f}"
    return f"token similarity {confidence:.2f}"


def _pairs[T](ordered: Sequence[T]) -> Iterator[tuple[T, T]]:
    """Every unordered pair, in a fixed order."""
    for index in range(len(ordered)):
        for other in range(index + 1, len(ordered)):
            yield ordered[index], ordered[other]


def _hash_id(prefix: str, basis: str) -> str:
    return prefix + hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16]

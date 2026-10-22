"""The analytics cases: the numbers the graph adds up to, and the limits."""

from __future__ import annotations

import pytest

from product_hunter.independence import (
    AuthorObservation,
    CommunityObservation,
    IdentitySignal,
    MentionObservation,
    analyze,
    build_graph,
    detect_propagation,
    load_independence_policy,
    resolve_authors,
    resolve_communities,
)

DETECTED_AT = "2026-10-21T00:00:00+00:00"
POST = "the invoice export takes three hours every month and nobody knows why"
OTHER = "a different complaint about payroll exports and the totals each month"


def _author(author_id: str, *, source_id: str = "hn_algolia", handle: str | None = None):
    return AuthorObservation(
        author_id=author_id, source_id=source_id, handle=handle or author_id
    )


def _community(community_id: str, *, source_id: str = "hn_algolia", name: str = "Hacker News"):
    return CommunityObservation(
        community_id=community_id, source_id=source_id, name=name
    )


def _mention(
    doc_id: str,
    author_id: str | None = None,
    community_id: str | None = None,
    *,
    source_id: str = "hn_algolia",
    text: str = OTHER,
    live_status: str = "live",
) -> MentionObservation:
    return MentionObservation(
        doc_id=doc_id,
        source_id=source_id,
        community_id=community_id,
        author_id=author_id,
        text=text,
        text_sha256=doc_id * 8,
        live_status=live_status,
    )


def _verdict(
    mentions: list[MentionObservation],
    authors: list[AuthorObservation] | None = None,
    communities: list[CommunityObservation] | None = None,
    policy=None,
    signals: list[IdentitySignal] | None = None,
):
    policy = policy or load_independence_policy()
    authors = authors or []
    communities = communities or []
    links = resolve_authors(
        authors, mentions, policy, detected_at=DETECTED_AT, signals=signals or []
    )
    community_links = resolve_communities(communities, policy, detected_at=DETECTED_AT)
    edges = detect_propagation(mentions, policy, detected_at=DETECTED_AT)
    graph = build_graph(
        mentions, authors, communities, links, community_links, edges, policy
    )
    return analyze(graph, policy)


def test_three_communities_are_three_independent_communities() -> None:
    mentions = [
        _mention("doc_1", "au_1", "co_1"),
        _mention("doc_2", "au_2", "co_2"),
        _mention("doc_3", "au_3", "co_3"),
    ]
    authors = [_author("au_1"), _author("au_2"), _author("au_3")]
    communities = [_community("co_1"), _community("co_2"), _community("co_3")]

    verdict = _verdict(mentions, authors, communities)
    assert verdict.metrics.independent_community_count == 3
    assert verdict.metrics.independent_community_ids == ["co_1", "co_2", "co_3"]
    assert verdict.metrics.community_concentration == pytest.approx(1 / 3)
    assert "independent_communities_below_minimum" not in _codes(verdict)


def test_every_mention_in_one_community_breaks_the_community_rule() -> None:
    mentions = [
        _mention("doc_1", "au_1", "co_1"),
        _mention("doc_2", "au_2", "co_1"),
        _mention("doc_3", "au_3", "co_1"),
    ]
    authors = [_author("au_1"), _author("au_2"), _author("au_3")]
    communities = [_community("co_1")]

    verdict = _verdict(mentions, authors, communities)
    assert verdict.metrics.community_concentration == 1.0
    assert verdict.metrics.independent_community_count == 1
    violation = _violation(verdict, "community_concentration_exceeded")
    assert violation.observed == 1.0
    assert violation.limit == 0.6
    assert verdict.satisfied is False


def test_a_chain_of_copies_breaks_the_chain_rule() -> None:
    mentions = [
        _mention("doc_1", "au_1", "co_1", text=POST),
        _mention("doc_2", "au_2", "co_1", text=POST),
        _mention("doc_3", "au_3", "co_1", text=POST),
    ]
    authors = [_author("au_1"), _author("au_2"), _author("au_3")]
    communities = [_community("co_1")]

    verdict = _verdict(mentions, authors, communities)
    assert verdict.metrics.propagation_chain_count == 1
    assert verdict.metrics.propagation_chain_concentration == 1.0
    assert verdict.metrics.canonical_content_count == 1
    assert verdict.metrics.propagation_adjusted_evidence == 1
    violation = _violation(verdict, "propagation_chain_concentration_exceeded")
    assert violation.limit == 0.3


def test_a_mention_with_no_author_is_uncertain_and_never_independent() -> None:
    verdict = _verdict([_mention("doc_1")])

    assert verdict.metrics.independent_author_count == 0
    assert verdict.metrics.uncertain_author_count == 1
    assert verdict.metrics.unattributed_mentions == 1
    assert verdict.metrics.uncertain_identity_groups == 1
    # An anonymous content group holds no account, so it is not a suspect
    # identity group.
    assert verdict.metrics.suspect_identity_groups == 0


def test_a_mention_whose_author_is_unknown_counts_against_that_author() -> None:
    verdict = _verdict(
        [_mention("doc_1", "au_missing", "co_1")], [], [_community("co_1")]
    )

    assert verdict.metrics.author_cluster_concentration == 1.0
    assert "author_cluster_concentration_exceeded" in _codes(verdict)


def test_a_deleted_mention_stays_in_the_counts_and_is_reported_as_deleted() -> None:
    mentions = [
        _mention("doc_1", "au_1", "co_1", live_status="deleted"),
        _mention("doc_2", "au_2", "co_2"),
    ]
    authors = [_author("au_1"), _author("au_2")]
    communities = [_community("co_1"), _community("co_2")]

    verdict = _verdict(mentions, authors, communities)
    assert verdict.metrics.raw_mentions == 2
    assert verdict.metrics.deleted_source_mentions == 1
    assert verdict.metrics.independent_author_count == 2
    assert verdict.metrics.independent_community_count == 2


def test_one_source_holding_every_mention_breaks_the_source_rule() -> None:
    mentions = [_mention("doc_1", "au_1", "co_1")]
    verdict = _verdict(mentions, [_author("au_1")], [_community("co_1")])

    assert verdict.metrics.source_concentration == 1.0
    violation = _violation(verdict, "source_concentration_exceeded")
    assert violation.limit == 0.8


def test_one_author_holding_every_mention_breaks_the_cluster_rule() -> None:
    mentions = [
        _mention("doc_1", "au_1", "co_1"),
        _mention("doc_2", "au_1", "co_1"),
        _mention("doc_3", "au_2", "co_1"),
    ]
    authors = [_author("au_1"), _author("au_2")]
    verdict = _verdict(mentions, authors, [_community("co_1")])

    assert verdict.metrics.author_cluster_concentration == pytest.approx(2 / 3)
    violation = _violation(verdict, "author_cluster_concentration_exceeded")
    assert violation.limit == 0.4


def test_the_limits_come_from_the_configuration() -> None:
    policy = load_independence_policy()
    mentions = [_mention("doc_1", "au_1", "co_1")]
    loose = policy.concentration.model_copy(update={"max_single_source_share": 1.0})
    relaxed = policy.model_copy(update={"concentration": loose})

    assert "source_concentration_exceeded" in _codes(_verdict(mentions, [_author("au_1")]))
    assert "source_concentration_exceeded" not in _codes(
        _verdict(mentions, [_author("au_1")], policy=relaxed)
    )


def test_the_numbers_are_a_function_of_the_graph() -> None:
    policy = load_independence_policy()
    one = _verdict([_mention("doc_1", "au_1", "co_1")], [_author("au_1")])
    two = _verdict(
        [
            _mention("doc_1", "au_1", "co_1"),
            _mention("doc_2", "au_2", "co_2"),
        ],
        [_author("au_1"), _author("au_2")],
        [_community("co_1"), _community("co_2")],
    )

    assert one.metrics.raw_mentions == 1
    assert one.metrics.independent_author_count == 1
    assert two.metrics.raw_mentions == 2
    assert two.metrics.independent_author_count == 2
    assert one.metrics.policy_version == policy.version


def test_the_same_graph_always_gives_the_same_numbers() -> None:
    mentions = [
        _mention("doc_1", "au_1", "co_1"),
        _mention("doc_2", "au_2", "co_2"),
    ]
    authors = [_author("au_1"), _author("au_2")]
    communities = [_community("co_1"), _community("co_2")]

    first = _verdict(mentions, authors, communities)
    second = _verdict(list(reversed(mentions)), list(reversed(authors)), communities)
    assert first.metrics.model_dump() == second.metrics.model_dump()


def test_an_uncertain_group_is_counted_once_and_never_as_two_people() -> None:
    policy = load_independence_policy()
    left = AuthorObservation(
        author_id="au_1", source_id="hn_algolia", handle="jane", community_ids=["co_1"]
    )
    right = AuthorObservation(
        author_id="au_2", source_id="github_issues", handle="john", community_ids=["co_1"]
    )
    mentions = [
        _mention("doc_1", "au_1", "co_1"),
        _mention("doc_2", "au_2", "co_1"),
    ]
    signal = IdentitySignal(
        kind="signature_reuse", author_id="au_1", related_author_id="au_2"
    )

    verdict = _verdict(mentions, [left, right], [_community("co_1")], policy, [signal])
    # The two accounts share a community and a signature: a suspect pair, so
    # they are one possible person and not two.
    assert verdict.metrics.uncertain_author_count == 1
    assert verdict.metrics.independent_author_count == 0
    assert verdict.metrics.suspect_identity_groups == 1


def _codes(verdict) -> list[str]:
    return [violation.code for violation in verdict.violations]


def _violation(verdict, code: str):
    for violation in verdict.violations:
        if violation.code == code:
            return violation
    raise AssertionError(f"the verdict has no {code!r} violation: {_codes(verdict)}")

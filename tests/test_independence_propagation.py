"""The propagation cases: what counts as the same content, and what quotes it."""

from __future__ import annotations

import pytest

from product_hunter.independence import (
    AuthorObservation,
    CommunityObservation,
    MentionObservation,
    analyze,
    build_graph,
    detect_propagation,
    load_independence_policy,
    longest_common_block,
    token_set,
)

DETECTED_AT = "2026-10-21T00:00:00+00:00"
POST = "the invoice export takes three hours every month and nobody knows why"


def _mention(
    doc_id: str,
    text: str,
    *,
    source_id: str = "hn_algolia",
    published_at: str | None = None,
    sha: str | None = None,
) -> MentionObservation:
    return MentionObservation(
        doc_id=doc_id,
        source_id=source_id,
        text=text,
        text_sha256=sha if sha is not None else doc_id * 8,
        published_at=published_at,
    )


def _analyze(mentions: list[MentionObservation]):
    policy = load_independence_policy()
    edges = detect_propagation(mentions, policy, detected_at=DETECTED_AT)
    graph = build_graph(mentions, [], [], [], [], edges, policy)
    return analyze(graph, policy)


def test_five_copies_of_one_post_collapse_to_one_observation() -> None:
    policy = load_independence_policy()
    mentions = [
        _mention(
            f"doc_{index}",
            POST,
            published_at=f"2026-01-{index + 1:02d}T00:00:00+00:00",
            sha="a" * 64,
        )
        for index in range(5)
    ]

    edges = detect_propagation(mentions, policy, detected_at=DETECTED_AT)
    # Every pair of copies is compared, and every one of them is a repost.
    assert len(edges) == 10
    assert {edge.relation for edge in edges} == {"repost"}
    assert {edge.detection_method for edge in edges} == {"same_content_same_source"}

    verdict = _analyze(mentions)
    assert verdict.metrics.raw_mentions == 5
    assert verdict.metrics.canonical_content_count == 1
    assert verdict.metrics.propagation_adjusted_evidence == 1
    assert verdict.metrics.propagation_chain_count == 1


def test_the_same_content_on_two_platforms_is_one_canonical_content() -> None:
    policy = load_independence_policy()
    mentions = [
        _mention("doc_1", POST, source_id="hn_algolia", sha="b" * 64),
        _mention("doc_2", POST, source_id="github_issues", sha="b" * 64),
    ]

    edges = detect_propagation(mentions, policy, detected_at=DETECTED_AT)
    assert len(edges) == 1
    assert edges[0].relation == "crosspost"
    assert edges[0].detection_method == "same_content_cross_source"
    assert edges[0].confidence == 1.0

    verdict = _analyze(mentions)
    assert verdict.metrics.canonical_content_count == 1
    assert verdict.metrics.propagation_adjusted_evidence == 1


def test_a_quote_chain_has_one_root_and_two_derived_observations() -> None:
    policy = load_independence_policy()
    first = POST
    second = POST + " and we gave up on it"
    third = second + " so the whole team moved on and gave up entirely"
    mentions = [
        _mention("doc_a", first, published_at="2026-01-01T00:00:00+00:00"),
        _mention("doc_b", second, published_at="2026-02-01T00:00:00+00:00"),
        _mention("doc_c", third, published_at="2026-03-01T00:00:00+00:00"),
    ]

    edges = detect_propagation(mentions, policy, detected_at=DETECTED_AT)
    assert edges
    assert all(edge.relation == "quote" for edge in edges)
    assert all(edge.detection_method == "verbatim_quote_overlap" for edge in edges)
    assert ("doc_a", "doc_b") in {(edge.from_doc_id, edge.to_doc_id) for edge in edges}
    assert ("doc_b", "doc_c") in {(edge.from_doc_id, edge.to_doc_id) for edge in edges}

    verdict = _analyze(mentions)
    assert verdict.metrics.canonical_content_count == 3
    assert verdict.metrics.propagation_adjusted_evidence == 1
    assert verdict.metrics.propagation_chain_count == 1


def test_a_near_duplicate_is_a_syndication() -> None:
    policy = load_independence_policy()
    # The token sets sit between the syndication and the repost threshold, and
    # the two texts share no block long enough to be a quote.
    left = "alpha bravo charlie delta echo mike foxtrot golf hotel india juliet kilo"
    right = "alpha bravo charlie delta echo foxtrot golf hotel india juliet kilo lima"
    mentions = [_mention("doc_1", left), _mention("doc_2", right)]

    edges = detect_propagation(mentions, policy, detected_at=DETECTED_AT)
    assert len(edges) == 1
    assert edges[0].relation == "syndication"
    assert edges[0].detection_method == "near_duplicate_content"
    assert 0.75 <= edges[0].confidence < 0.9


def test_a_quote_is_still_detected() -> None:
    policy = load_independence_policy()
    # The quoting post carries the whole of the first one and adds its own
    # words, so the block is longer than the shorter text and the pair is a
    # quote.
    mentions = [
        _mention("doc_1", POST),
        _mention("doc_2", POST + " and we gave up on it entirely"),
    ]

    edges = detect_propagation(mentions, policy, detected_at=DETECTED_AT)
    assert len(edges) == 1
    assert edges[0].relation == "quote"
    assert edges[0].detection_method == "verbatim_quote_overlap"


def test_the_screen_does_not_reject_a_real_quote() -> None:
    policy = load_independence_policy()
    # A quote needs a block of quote_min_overlap * min_comparable_chars
    # characters, which is 24. The two texts share exactly that many, so the
    # screen has to let the pair through.
    shared = "the quick brown fox jump"
    left = shared + " over a lazy dog"  # 40 characters
    right = shared + "beside a tall tree"  # 42 characters
    assert len(left) == 40
    assert longest_common_block(left, right) == 24
    mentions = [_mention("doc_1", left), _mention("doc_2", right)]

    edges = detect_propagation(mentions, policy, detected_at=DETECTED_AT)
    assert len(edges) == 1
    assert edges[0].relation == "quote"
    assert edges[0].confidence == pytest.approx(0.6)


def test_a_run_one_character_short_is_not_a_quote() -> None:
    policy = load_independence_policy()
    # The same pair one character short of the run a quote needs: the block is
    # 23 characters of a 39 character text, so the pair earns no quote.
    shared = "the quick brown fox jum"
    left = shared + " over a lazy dog"
    right = shared + "beside a tall tree"
    mentions = [_mention("doc_1", left), _mention("doc_2", right)]

    assert detect_propagation(mentions, policy, detected_at=DETECTED_AT) == []


def test_a_scope_above_the_limit_is_refused() -> None:
    policy = load_independence_policy()
    assert policy.propagation.max_scope_mentions == 2000
    tight = policy.propagation.model_copy(update={"max_scope_mentions": 3})
    policy = policy.model_copy(update={"propagation": tight})
    mentions = [_mention(f"doc_{index}", POST) for index in range(4)]

    with pytest.raises(ValueError, match="the policy allows 3"):
        detect_propagation(mentions, policy, detected_at=DETECTED_AT)


def test_the_edge_points_from_the_earlier_mention_to_the_later_one() -> None:
    policy = load_independence_policy()
    mentions = [
        _mention("doc_z", POST, published_at="2026-01-01T00:00:00+00:00", sha="c" * 64),
        _mention("doc_a", POST, published_at="2026-02-01T00:00:00+00:00", sha="c" * 64),
    ]

    edges = detect_propagation(mentions, policy, detected_at=DETECTED_AT)
    assert len(edges) == 1
    assert edges[0].from_doc_id == "doc_z"
    assert edges[0].to_doc_id == "doc_a"


def test_a_pair_without_timestamps_is_ordered_by_doc_id() -> None:
    policy = load_independence_policy()
    mentions = [
        _mention("doc_b", POST, sha="d" * 64),
        _mention("doc_a", POST, sha="d" * 64),
    ]

    edges = detect_propagation(mentions, policy, detected_at=DETECTED_AT)
    assert len(edges) == 1
    assert edges[0].from_doc_id == "doc_a"
    assert edges[0].to_doc_id == "doc_b"


def test_mixed_precision_timestamps_order_by_time_not_by_codepoint() -> None:
    policy = load_independence_policy()
    mentions = [
        _mention("doc_a", POST, published_at="2026-10-21T10:00:00.500Z", sha="c" * 64),
        _mention("doc_b", POST, published_at="2026-10-21T10:00:00Z", sha="c" * 64),
    ]

    edges = detect_propagation(mentions, policy, detected_at=DETECTED_AT)
    assert len(edges) == 1
    assert edges[0].from_doc_id == "doc_b"
    assert edges[0].to_doc_id == "doc_a"


def test_a_post_in_another_script_is_tokenised() -> None:
    assert token_set("Привет мир") == {"привет", "мир"}

    policy = load_independence_policy()
    text = "Привет мир, это тестовый пост о выставлении счетов и о сроках"
    mentions = [
        _mention("doc_1", text, sha="a" * 64),
        _mention("doc_2", text, sha="b" * 64),
    ]
    edges = detect_propagation(mentions, policy, detected_at=DETECTED_AT)
    assert len(edges) == 1
    assert edges[0].relation == "repost"


def test_a_document_longer_than_the_bound_is_compared_on_its_head() -> None:
    policy = load_independence_policy()
    limit = policy.propagation.max_text_chars
    head = ("alpha " * (limit // 6 + 1))[:limit]
    # The heads are identical and the tails are not; only the bounded text is
    # compared, so the pair is a repost.
    left = head + " beta " * 200
    right = head + " gamma " * 200
    mentions = [
        _mention("doc_1", left, sha="1" * 64),
        _mention("doc_2", right, sha="2" * 64),
    ]

    edges = detect_propagation(mentions, policy, detected_at=DETECTED_AT)
    assert len(edges) == 1
    assert edges[0].relation == "repost"


def test_a_short_text_is_not_compared_with_a_different_one() -> None:
    policy = load_independence_policy()
    mentions = [
        _mention("doc_1", "too short to compare"),
        _mention("doc_2", "also short but different"),
    ]

    assert detect_propagation(mentions, policy, detected_at=DETECTED_AT) == []


def test_an_exact_copy_is_compared_even_when_it_is_short() -> None:
    policy = load_independence_policy()
    mentions = [
        _mention("doc_1", "short copy", sha="e" * 64),
        _mention("doc_2", "short copy", sha="e" * 64),
    ]

    edges = detect_propagation(mentions, policy, detected_at=DETECTED_AT)
    assert len(edges) == 1
    assert edges[0].relation == "repost"


def test_every_edge_carries_its_method_confidence_provenance_and_timestamp() -> None:
    policy = load_independence_policy()
    mention = _mention("doc_1", POST, sha="f" * 64)
    mention = mention.model_copy(update={"span_ids": ["span_1"]})
    other = _mention("doc_2", POST, sha="f" * 64).model_copy(
        update={"span_ids": ["span_2"]}
    )

    edges = detect_propagation([mention, other], policy, detected_at=DETECTED_AT)
    edge = edges[0]
    assert edge.detection_method == "same_content_same_source"
    assert edge.confidence == 1.0
    assert edge.detected_at == DETECTED_AT
    assert edge.provenance.source_ids == ["hn_algolia"]
    assert edge.provenance.doc_ids == ["doc_1", "doc_2"]
    assert edge.provenance.span_ids == ["span_1", "span_2"]
    assert edge.provenance.detail


def test_the_detection_is_the_same_every_time_it_runs() -> None:
    policy = load_independence_policy()
    mentions = [
        _mention("doc_1", POST, published_at="2026-01-01T00:00:00+00:00"),
        _mention(
            "doc_2",
            POST + " and we gave up on it",
            published_at="2026-02-01T00:00:00+00:00",
        ),
    ]

    first = detect_propagation(mentions, policy, detected_at=DETECTED_AT)
    second = detect_propagation(list(reversed(mentions)), policy, detected_at=DETECTED_AT)
    assert [edge.model_dump() for edge in first] == [edge.model_dump() for edge in second]


def test_the_propagation_does_not_change_the_author_count() -> None:
    policy = load_independence_policy()
    author = AuthorObservation(
        author_id="au_1", source_id="hn_algolia", handle="jane", community_ids=["co_1"]
    )
    mentions = [
        _mention("doc_1", POST, sha="1" * 64).model_copy(
            update={"author_id": "au_1", "community_id": "co_1"}
        ),
        _mention("doc_2", POST, sha="1" * 64).model_copy(
            update={"author_id": "au_1", "community_id": "co_1"}
        ),
    ]
    community = CommunityObservation(
        community_id="co_1", source_id="hn_algolia", name="Hacker News"
    )

    edges = detect_propagation(mentions, policy, detected_at=DETECTED_AT)
    graph = build_graph(mentions, [author], [community], [], [], edges, policy)
    verdict = analyze(graph, policy)

    assert verdict.metrics.raw_mentions == 2
    assert verdict.metrics.canonical_content_count == 1
    assert verdict.metrics.independent_author_count == 1
    assert verdict.metrics.propagation_adjusted_evidence == 1


@pytest.mark.parametrize("relation", ["repost", "crosspost", "quote", "syndication"])
def test_every_relation_is_detected(relation: str) -> None:
    policy = load_independence_policy()
    if relation == "repost":
        mentions = [
            _mention("doc_1", POST, sha="1" * 64),
            _mention("doc_2", POST, sha="1" * 64),
        ]
    elif relation == "crosspost":
        mentions = [
            _mention("doc_1", POST, source_id="hn_algolia", sha="1" * 64),
            _mention("doc_2", POST, source_id="github_issues", sha="1" * 64),
        ]
    elif relation == "quote":
        mentions = [
            _mention("doc_1", POST),
            _mention("doc_2", POST + " and we gave up on it"),
        ]
    else:
        mentions = [
            _mention(
                "doc_1", "alpha bravo charlie delta echo mike foxtrot golf hotel india juliet kilo"
            ),
            _mention(
                "doc_2", "alpha bravo charlie delta echo foxtrot golf hotel india juliet kilo lima"
            ),
        ]

    edges = detect_propagation(mentions, policy, detected_at=DETECTED_AT)
    assert relation in {edge.relation for edge in edges}

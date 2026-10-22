"""The identity cases: who is one person, and what the resolver refuses to say."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from product_hunter.config import repo_root
from product_hunter.independence import (
    AuthorObservation,
    CommunityObservation,
    IdentitySignal,
    IndependencePolicy,
    MentionObservation,
    PolicyError,
    analyze,
    author_confidence,
    build_author_clusters,
    build_graph,
    canonical_community_ids,
    hash_identity_key,
    load_independence_policy,
    normalise_name,
    resolve_authors,
    resolve_communities,
    validate_policy,
)

DETECTED_AT = "2026-10-21T00:00:00+00:00"
POLICY_FILE = Path("config") / "independence.yaml"
CONTRACT_FILE = Path("config") / "research_contract.yaml"


def _policy() -> IndependencePolicy:
    return load_independence_policy()


def _author(
    author_id: str,
    *,
    source_id: str = "hn_algolia",
    handle: str = "jane",
    **kwargs: object,
) -> AuthorObservation:
    return AuthorObservation(
        author_id=author_id, source_id=source_id, handle=handle, **kwargs
    )


def _mention(
    doc_id: str,
    author_id: str | None,
    community_id: str | None = None,
    *,
    source_id: str = "hn_algolia",
    text: str = "",
) -> MentionObservation:
    return MentionObservation(
        doc_id=doc_id,
        source_id=source_id,
        community_id=community_id,
        author_id=author_id,
        text=text,
        text_sha256=doc_id * 8,
    )


def _set(raw: dict, path: str, value: object) -> dict:
    """Set one value in a parsed policy file."""
    keys = path.split(".")
    node = raw
    for key in keys[:-1]:
        node = node[key]
    node[keys[-1]] = value
    return raw


def _raw_policy() -> dict:
    return yaml.safe_load((repo_root() / POLICY_FILE).read_text(encoding="utf-8"))


def test_the_shipped_policy_loads_and_is_valid() -> None:
    policy = _policy()
    assert policy.version == "independence-v1"
    assert validate_policy(policy) == []


def test_the_policy_minima_mirror_the_research_contract() -> None:
    contract = yaml.safe_load((repo_root() / CONTRACT_FILE).read_text(encoding="utf-8"))
    validation = contract["evidence_thresholds"]["validation"]
    policy = _policy()
    assert policy.concentration.min_independent_authors == validation["min_independent_authors"]
    assert (
        policy.concentration.min_independent_communities
        == validation["min_independent_communities"]
    )


def test_one_account_with_many_mentions_is_one_independent_author() -> None:
    policy = _policy()
    author = _author("au_1", handle="jane", community_ids=["co_1"])
    mentions = [
        _mention("doc_1", "au_1", "co_1", text="the invoice export takes three hours every month"),
        _mention("doc_2", "au_1", "co_1", text="a completely different note about spreadsheets"),
        _mention("doc_3", "au_1", "co_1", text="and a third one about rounding the totals up"),
    ]

    links = resolve_authors([author], mentions, policy, detected_at=DETECTED_AT)
    assert links == []

    clusters = build_author_clusters([author], links, policy)
    assert len(clusters) == 1
    assert clusters[0].status == "resolved"
    assert clusters[0].member_author_ids == ["au_1"]
    assert clusters[0].canonical_author_id == "au_1"


def test_two_accounts_are_two_independent_authors() -> None:
    policy = _policy()
    left = _author("au_1", handle="jane", community_ids=["co_1"])
    right = _author("au_2", handle="john", community_ids=["co_2"])
    mentions = [
        _mention("doc_1", "au_1", "co_1", text="the invoice export takes three hours every month"),
        _mention("doc_2", "au_2", "co_2", text="a completely different note about spreadsheets"),
    ]

    links = resolve_authors([left, right], mentions, policy, detected_at=DETECTED_AT)
    assert links == []

    clusters = build_author_clusters([left, right], links, policy)
    assert len(clusters) == 2
    assert all(cluster.status == "resolved" for cluster in clusters)


def test_a_display_name_match_alone_creates_no_edge() -> None:
    policy = _policy()
    left = _author("au_1", source_id="hn_algolia", handle="jdoe", display_name="Jane Doe")
    right = _author("au_2", source_id="github_issues", handle="jdoe2", display_name="Jane Doe")

    links = resolve_authors([left, right], [], policy, detected_at=DETECTED_AT)
    assert links == []

    clusters = build_author_clusters([left, right], links, policy)
    assert len(clusters) == 2


def test_name_spellings_fold_without_losing_a_word_boundary() -> None:
    assert (
        normalise_name("Jane Doe")
        == normalise_name("jane.doe")
        == normalise_name("Jane  Doe")
    )
    # A space is a word boundary, so two different names do not collapse.
    assert normalise_name("ab cd") != normalise_name("a bcd")
    assert normalise_name("@JaneDoe") != normalise_name("Jane Doe")


def test_a_stored_hash_and_the_raw_key_compare_equal() -> None:
    policy = _policy()
    stored = hash_identity_key("raw-key")
    assert hash_identity_key(stored) == stored

    left = _author("au_1", handle="jane", identity_key="raw-key")
    right = _author("au_2", handle="john", identity_key=stored)
    links = resolve_authors([left, right], [], policy, detected_at=DETECTED_AT)
    assert len(links) == 1
    assert links[0].relation == "same-person-merged"
    assert "identity_key" in links[0].signals


def test_a_model_signal_alone_never_merges() -> None:
    policy = _policy()
    left = _author("au_1", source_id="hn_algolia", handle="jane")
    right = _author("au_2", source_id="github_issues", handle="john")
    signal = IdentitySignal(
        kind="model_signal", author_id="au_1", related_author_id="au_2"
    )

    links = resolve_authors(
        [left, right], [], policy, detected_at=DETECTED_AT, signals=[signal]
    )
    # 0.35 alone is below the suspect threshold, so the pair earns no edge.
    assert links == []

    clusters = build_author_clusters([left, right], links, policy)
    assert len(clusters) == 2


def test_a_model_signal_beside_a_second_weak_signal_is_a_suspect() -> None:
    policy = _policy()
    left = _author("au_1", source_id="hn_algolia", handle="jane")
    right = _author("au_2", source_id="github_issues", handle="john")
    signals = [
        IdentitySignal(kind="model_signal", author_id="au_1", related_author_id="au_2"),
        IdentitySignal(kind="signature_reuse", author_id="au_1", related_author_id="au_2"),
    ]

    links = resolve_authors(
        [left, right], [], policy, detected_at=DETECTED_AT, signals=signals
    )
    assert len(links) == 1
    assert links[0].relation == "same-person-suspect"
    assert "model_signal" in links[0].signals
    assert links[0].confidence == policy.identity.suspect_threshold


def test_weak_signals_cannot_merge_even_when_their_weights_add_up() -> None:
    policy = _policy()
    raised = policy.identity.model_copy(
        update={"signal_weights": {**policy.identity.signal_weights, "model_signal": 0.5}}
    )
    policy = policy.model_copy(update={"identity": raised})
    assert validate_policy(policy) == []

    left = _author("au_1", source_id="hn_algolia", handle="jane", community_ids=["co_1"])
    right = _author("au_2", source_id="github_issues", handle="john", community_ids=["co_1"])
    signals = [
        IdentitySignal(kind="model_signal", author_id="au_1", related_author_id="au_2"),
        IdentitySignal(kind="signature_reuse", author_id="au_1", related_author_id="au_2"),
    ]

    links = resolve_authors(
        [left, right], [], policy, detected_at=DETECTED_AT, signals=signals
    )
    assert len(links) == 1
    assert links[0].relation == "same-person-suspect"
    assert links[0].confidence == policy.identity.suspect_threshold


def test_a_caller_may_not_supply_a_strong_signal() -> None:
    policy = _policy()
    left = _author("au_1", handle="jane")
    right = _author("au_2", handle="john")
    signal = IdentitySignal(
        kind="identity_key", author_id="au_1", related_author_id="au_2"
    )

    with pytest.raises(ValueError):
        resolve_authors([left, right], [], policy, detected_at=DETECTED_AT, signals=[signal])


def test_a_contradicted_pair_is_a_suspect_and_never_a_merge() -> None:
    policy = _policy()
    left = _author("au_1", handle="jane", identity_key="key-1")
    right = _author("au_2", handle="jane", identity_key="key-1")
    disavow = IdentitySignal(
        kind="explicit_disavow", author_id="au_1", related_author_id="au_2"
    )

    links = resolve_authors(
        [left, right], [], policy, detected_at=DETECTED_AT, signals=[disavow]
    )
    assert len(links) == 1
    link = links[0]
    assert link.relation == "same-person-suspect"
    assert link.conflict is True
    assert link.confidence == policy.identity.conflict_score_cap
    assert link.signals == ["explicit_disavow", "identity_key", "same_platform_handle"]
    assert link.detected_at == DETECTED_AT
    assert link.provenance.source_ids == ["hn_algolia"]

    clusters = build_author_clusters([left, right], links, policy)
    assert len(clusters) == 1
    assert clusters[0].status == "suspect"
    assert clusters[0].confidence <= policy.identity.conflict_score_cap

    graph = build_graph([], [left, right], [], links, [], [], policy)
    verdict = analyze(graph, policy)
    assert verdict.metrics.conflicting_identity_pairs == 1
    assert verdict.metrics.independent_author_count == 0
    assert verdict.metrics.uncertain_author_count == 1


def test_a_conflicting_key_beside_a_shared_handle_is_recorded_as_a_conflict() -> None:
    policy = _policy()
    left = _author("au_1", handle="jane", identity_key="key-1")
    right = _author("au_2", handle="jane", identity_key="key-2")

    links = resolve_authors([left, right], [], policy, detected_at=DETECTED_AT)
    assert len(links) == 1
    assert links[0].relation == "same-person-suspect"
    assert links[0].conflict is True
    assert links[0].confidence == policy.identity.conflict_score_cap
    assert "identity_key_conflict" in links[0].signals


def test_conflicting_keys_alone_are_two_strangers_not_a_contradiction() -> None:
    policy = _policy()
    left = _author("au_1", handle="jane", identity_key="key-1")
    right = _author("au_2", handle="john", identity_key="key-2")

    assert resolve_authors([left, right], [], policy, detected_at=DETECTED_AT) == []


def test_a_cluster_keeps_each_members_own_confidence() -> None:
    policy = _policy()
    left = _author(
        "au_1",
        handle="jane",
        identity_key="key-1",
        profile_url="https://example.com/jane",
    )
    right = _author("au_2", handle="jane", identity_key="key-1")

    links = resolve_authors([left, right], [], policy, detected_at=DETECTED_AT)
    clusters = build_author_clusters([left, right], links, policy)
    assert len(clusters) == 1
    cluster = clusters[0]
    assert cluster.confidence == 1.0
    assert cluster.member_confidences == {"au_1": 1.0, "au_2": 0.9}


def test_the_author_confidence_follows_the_policy() -> None:
    policy = _policy()
    assert author_confidence(_author("au_1", handle=""), policy) == 0.0
    assert (
        author_confidence(_author("au_1", handle="jane"), policy)
        == policy.identity.standalone_handle_confidence
    )
    keyed = _author("au_1", handle="jane", identity_key="key-1")
    assert author_confidence(keyed, policy) == pytest.approx(0.9)
    profiled = _author(
        "au_1", handle="jane", identity_key="key-1", profile_url="https://example.com/jane"
    )
    assert author_confidence(profiled, policy) == 1.0
    assert (
        author_confidence(keyed, policy, conflicted=True)
        == policy.identity.conflict_score_cap
    )


def test_an_explicit_alias_merges_two_communities() -> None:
    policy = _policy()
    alias = CommunityObservation(
        community_id="co_2", source_id="hn_algolia", name="Hacker News", alias_of="co_1"
    )
    target = CommunityObservation(
        community_id="co_1", source_id="github_issues", name="news.ycombinator.com"
    )

    links = resolve_communities([alias, target], policy, detected_at=DETECTED_AT)
    assert len(links) == 1
    assert links[0].relation == "same-community-merged"
    assert links[0].detection_method == "explicit_alias"
    assert links[0].confidence == 1.0
    assert links[0].detected_at == DETECTED_AT

    canonical = canonical_community_ids([alias, target], links)
    assert canonical["co_1"] == "co_1"
    assert canonical["co_2"] == "co_1"


def test_the_same_name_on_two_sources_is_a_suspect_community() -> None:
    policy = _policy()
    left = CommunityObservation(
        community_id="co_1", source_id="hn_algolia", name="Hacker News"
    )
    right = CommunityObservation(
        community_id="co_2", source_id="github_issues", name="hacker news"
    )

    links = resolve_communities([left, right], policy, detected_at=DETECTED_AT)
    assert len(links) == 1
    assert links[0].relation == "same-community-suspect"
    assert links[0].detection_method == "same_name_cross_source"
    assert links[0].confidence == policy.identity.cross_source_name_weight

    canonical = canonical_community_ids([left, right], links)
    assert canonical["co_1"] == canonical["co_2"] == "co_1"


def test_the_same_name_on_one_source_is_the_same_community() -> None:
    policy = _policy()
    left = CommunityObservation(
        community_id="co_1", source_id="hn_algolia", name="Hacker News"
    )
    right = CommunityObservation(
        community_id="co_2", source_id="hn_algolia", name="Hacker News"
    )
    assert resolve_communities([left, right], policy, detected_at=DETECTED_AT) == []


@pytest.mark.parametrize(
    ("path", "value", "rule"),
    [
        ("identity.signal_weights.model_signal", 0.9, "model_signal"),
        ("identity.signal_weights.display_name", 0.6, "display_name"),
        ("identity.signal_weights.cross_linking", 1.4, "cross_linking"),
        ("identity.merge_threshold", 0.4, "merge_threshold"),
        ("identity.confident_threshold", 0.95, "confident_threshold"),
        ("propagation.repost_similarity_threshold", 0.0, "repost_similarity_threshold"),
        ("propagation.min_comparable_chars", -1, "min_comparable_chars"),
        ("propagation.max_scope_mentions", 0, "max_scope_mentions"),
        ("concentration.max_single_source_share", 1.5, "max_single_source_share"),
        ("concentration.min_independent_authors", -1, "min_independent_authors"),
    ],
)
def test_an_invalid_policy_is_refused_and_names_the_rule(
    tmp_path: Path, path: str, value: object, rule: str
) -> None:
    raw = _set(_raw_policy(), path, value)
    policy_file = tmp_path / "independence.yaml"
    policy_file.write_text(yaml.safe_dump(raw), encoding="utf-8")

    with pytest.raises(PolicyError) as caught:
        load_independence_policy(policy_file)
    assert any(rule in error for error in caught.value.errors)


def test_a_policy_file_that_is_not_yaml_is_refused(tmp_path: Path) -> None:
    policy_file = tmp_path / "independence.yaml"
    policy_file.write_text("version: [unclosed\n", encoding="utf-8")

    with pytest.raises(PolicyError) as caught:
        load_independence_policy(policy_file)
    assert caught.value.errors

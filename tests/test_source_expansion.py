"""The candidate workflow: the signals, the score, and the decision."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import date, datetime, timezone
from pathlib import Path

import yaml

from product_hunter.communities import CommunityConfig
from product_hunter.config import repo_root
from product_hunter.db import Store
from product_hunter.discovery.queries import CandidateSignals, SourceCandidate
from product_hunter.source_expansion import (
    ReviewPolicy,
    approve_candidate,
    candidate_id,
    discover_candidates,
    list_candidates,
    reject_candidate,
    score_candidate,
    select_candidates,
    select_communities,
)
from product_hunter.sources import (
    AllowedUse,
    Collector,
    RateLimit,
    RobotsPolicy,
    TermsStatus,
    load_registry,
)

NOW = "2026-10-22T00:00:00+00:00"
MOMENT = datetime(2026, 10, 22, tzinfo=timezone.utc)


def _rate() -> RateLimit:
    return RateLimit(
        min_interval_seconds=1.0,
        max_requests_per_run=10,
        max_retries=1,
        backoff_base_seconds=1.0,
        backoff_max_seconds=30.0,
    )


def _policy(**overrides) -> ReviewPolicy:
    data = {
        "terms_status": TermsStatus.reviewed,
        "allowed_use": [
            AllowedUse.discovery,
            AllowedUse.retrieval,
            AllowedUse.storage,
        ],
        "domains": ["forum.example.com"],
        "collector": Collector.http,
        "rate_limit": _rate(),
        "robots": RobotsPolicy.required,
        "review_basis": "the forum publishes the terms its users agree to",
        "reviewed_at": date(2026, 10, 22),
        "expires_at": date(2027, 4, 20),
        "robots_or_api_notes": "robots.txt is honoured per domain",
    }
    data.update(overrides)
    return ReviewPolicy(**data)


def _candidate() -> SourceCandidate:
    return SourceCandidate(
        candidate_id=candidate_id(
            "forum.example.com", "forum.example.com", "r/programming"
        ),
        source_id="forum.example.com",
        domain="forum.example.com",
        community="r/programming",
        discovered_by="discovery",
        discovered_at=MOMENT,
    )


def _store_with_a_forum(tmp_path: Path) -> Store:
    """A store that saw a forum the registry does not know."""
    store = Store.open(tmp_path / "product_hunter.db")
    store.sync_registry(load_registry())
    conn = store.conn
    conn.execute(
        "INSERT INTO fetches (fetch_id, source_id, requested_url, final_url, "
        "status, fetched_at) VALUES (?, ?, ?, ?, ?, ?)",
        (
            "fetch_1",
            "hn_algolia",
            "https://forum.example.com/thread/1",
            "https://forum.example.com/thread/1",
            200,
            NOW,
        ),
    )
    conn.execute(
        "INSERT INTO communities (community_id, source_id, name, url) "
        "VALUES (?, ?, ?, ?)",
        ("co_1", "hn_algolia", "r/programming", "https://forum.example.com/r/p"),
    )
    conn.execute(
        "INSERT INTO documents (doc_id, fetch_id, source_id, community_id, url, "
        "title, author, published_at, text_sha256, text_path, raw_path, "
        "raw_sha256, parser_version) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "doc_1",
            "fetch_1",
            "hn_algolia",
            "co_1",
            "https://forum.example.com/thread/1",
            "a thread",
            "alice",
            NOW,
            "a" * 64,
            "data/processed/text/a.txt",
            "data/raw/a.txt",
            "b" * 64,
            "phase1-0.3",
        ),
    )
    conn.execute(
        "INSERT INTO evidence (span_id, doc_id, quote, char_start, char_end, "
        "match_mode, occurrence) VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("span_1", "doc_1", "a quote", 0, 8, "exact", 1),
    )
    conn.execute(
        "INSERT INTO pain_points (pain_id, span_id, doc_id, actor, action, "
        "object, severity, frequency, pain_statement, accepted) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "pp_1",
            "span_1",
            "doc_1",
            "engineer",
            "pastes totals",
            "spreadsheet",
            3,
            1,
            "it takes a whole afternoon",
            1,
        ),
    )
    # A document on a host the registry already allows is not a proposal.
    conn.execute(
        "INSERT INTO fetches (fetch_id, source_id, requested_url, final_url, "
        "status, fetched_at) VALUES (?, ?, ?, ?, ?, ?)",
        (
            "fetch_2",
            "hn_algolia",
            "https://news.ycombinator.com/item?id=1",
            "https://news.ycombinator.com/item?id=1",
            200,
            NOW,
        ),
    )
    conn.execute(
        "INSERT INTO communities (community_id, source_id, name, url) "
        "VALUES (?, ?, ?, ?)",
        ("co_2", "hn_algolia", "Hacker News", "https://news.ycombinator.com"),
    )
    conn.execute(
        "INSERT INTO documents (doc_id, fetch_id, source_id, community_id, url, "
        "title, author, published_at, text_sha256, text_path, raw_path, "
        "raw_sha256, parser_version) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "doc_2",
            "fetch_2",
            "hn_algolia",
            "co_2",
            "https://news.ycombinator.com/item?id=1",
            "a post",
            "bob",
            NOW,
            "c" * 64,
            "data/processed/text/c.txt",
            "data/raw/c.txt",
            "d" * 64,
            "phase1-0.3",
        ),
    )
    return store


def test_the_discovery_reports_a_host_neither_registry_holds(tmp_path: Path):
    store = _store_with_a_forum(tmp_path)
    try:
        candidates = discover_candidates(store.conn, load_registry())
        assert [candidate.domain for candidate in candidates] == [
            "forum.example.com"
        ]
        candidate = candidates[0]
        assert candidate.community == "r/programming"
        assert candidate.status == "proposed"
        assert candidate.terms_status == "unknown"
        assert candidate.signals.volume == 1
        assert candidate.signals.recurring_problem_density == 1.0
        assert candidate.signals.evidence_yield == 1.0
        assert candidate.signals.duplication_risk == 0.0
        stored = list_candidates(store.conn)
        assert [row.candidate_id for row in stored] == [candidate.candidate_id]
    finally:
        store.close()


def test_a_candidate_is_a_proposal_and_never_a_source(tmp_path: Path):
    store = Store.open(tmp_path / "product_hunter.db")
    try:
        registry_file = repo_root() / "config" / "sources.yaml"
        before = registry_file.read_text(encoding="utf-8")
        outcome = approve_candidate(
            store.conn, _candidate(), _policy(), reviewer="operator"
        )
        assert outcome.approved is True
        assert registry_file.read_text(encoding="utf-8") == before
        assert len(load_registry().list()) == 3
    finally:
        store.close()


def test_the_selection_score_ignores_raw_volume():
    signals = CandidateSignals(
        recurring_problem_density=0.5,
        evidence_yield=0.5,
        independence=0.5,
        community_diversity=0.5,
        freshness=0.5,
        source_reliability=0.5,
        volume=10,
    )
    crowded = replace(signals, volume=10_000)
    assert score_candidate(signals).total == score_candidate(crowded).total
    ranked = select_candidates(
        [score_candidate(signals), score_candidate(crowded)], limit=2
    )
    assert ranked[0].volume == 10_000


def test_a_popular_source_with_no_density_is_not_selected():
    dense = CandidateSignals(
        recurring_problem_density=0.5,
        evidence_yield=0.5,
        independence=0.5,
        community_diversity=0.5,
        freshness=0.5,
        source_reliability=0.5,
        volume=10,
    )
    popular = CandidateSignals(
        evidence_yield=0.9,
        independence=0.9,
        community_diversity=0.9,
        freshness=0.9,
        source_reliability=0.9,
        volume=10_000,
    )
    dense_score = score_candidate(dense)
    popular_score = score_candidate(popular)
    assert dense_score.eligible is True
    assert popular_score.eligible is False
    assert any(
        "recurring_problem_density" in reason for reason in popular_score.reasons
    )
    assert select_candidates([popular_score, dense_score], limit=1) == [dense_score]


def test_an_ineligible_candidate_is_kept_out_of_the_selection():
    dense = CandidateSignals(
        recurring_problem_density=0.5,
        evidence_yield=0.5,
        independence=0.5,
        community_diversity=0.5,
        freshness=0.5,
        source_reliability=0.5,
    )
    popular = CandidateSignals(
        evidence_yield=1.0,
        independence=1.0,
        community_diversity=1.0,
        freshness=1.0,
        source_reliability=1.0,
        volume=10_000,
    )
    dense_score = score_candidate(dense)
    popular_score = score_candidate(popular)
    assert popular_score.eligible is False
    assert popular_score.total > dense_score.total
    # A limit larger than the number of eligible candidates must not let an
    # ineligible one back in.
    assert select_candidates([popular_score, dense_score], limit=10) == [dense_score]
    communities = [
        CommunityConfig(
            community_id="noisy",
            source_id="hn_algolia",
            name="Noisy",
            independence_group="one",
        ),
        CommunityConfig(
            community_id="good",
            source_id="hn_algolia",
            name="Good",
            independence_group="two",
        ),
    ]
    chosen = select_communities(
        communities, {"noisy": popular_score, "good": dense_score}
    )
    assert [community.community_id for community in chosen] == ["good"]


def test_the_selection_holds_one_community_per_independence_group():
    communities = [
        CommunityConfig(
            community_id="first",
            source_id="hn_algolia",
            name="First",
            independence_group="one",
        ),
        CommunityConfig(
            community_id="second",
            source_id="hn_algolia",
            name="Second",
            independence_group="one",
        ),
        CommunityConfig(
            community_id="third",
            source_id="github_issues",
            name="Third",
            independence_group="two",
        ),
    ]
    scores = {
        community.community_id: score_candidate(
            CandidateSignals(
                recurring_problem_density=0.5,
                evidence_yield=0.5,
                independence=0.5,
                community_diversity=0.5,
                freshness=0.5,
                source_reliability=0.5,
            )
        )
        for community in communities
    }
    chosen = select_communities(communities, scores)
    assert [community.community_id for community in chosen] == ["first", "third"]


def test_an_approval_without_the_full_policy_is_refused(tmp_path: Path):
    store = Store.open(tmp_path / "product_hunter.db")
    try:
        outcome = approve_candidate(
            store.conn, _candidate(), ReviewPolicy(), reviewer="operator"
        )
        assert outcome.approved is False
        assert outcome.reason_code == "incomplete_policy"
        assert "terms_status" in outcome.missing
        assert "domains" in outcome.missing
        assert "reviewed_at" in outcome.missing
        assert outcome.registry_entry == ""
        assert (
            store.conn.execute("SELECT count(*) FROM source_reviews").fetchone()[0]
            == 0
        )
    finally:
        store.close()


def test_an_approval_with_terms_that_do_not_permit_is_refused(tmp_path: Path):
    store = Store.open(tmp_path / "product_hunter.db")
    try:
        outcome = approve_candidate(
            store.conn,
            _candidate(),
            _policy(terms_status=TermsStatus.unknown),
            reviewer="operator",
        )
        assert outcome.approved is False
        assert outcome.reason_code == "terms_not_permitted"
    finally:
        store.close()


def test_the_approved_entry_carries_https_by_default(tmp_path: Path):
    store = Store.open(tmp_path / "product_hunter.db")
    try:
        outcome = approve_candidate(
            store.conn, _candidate(), _policy(), reviewer="operator"
        )
        assert yaml.safe_load(outcome.registry_entry)[0]["schemes"] == ["https"]
    finally:
        store.close()


def test_the_approved_entry_carries_the_schemes_the_decision_allows(
    tmp_path: Path,
):
    store = Store.open(tmp_path / "product_hunter.db")
    try:
        outcome = approve_candidate(
            store.conn,
            _candidate(),
            _policy(schemes=["http", "https"]),
            reviewer="operator",
        )
        assert yaml.safe_load(outcome.registry_entry)[0]["schemes"] == [
            "http",
            "https",
        ]
    finally:
        store.close()


def test_an_approval_the_registry_would_refuse_is_refused(tmp_path: Path):
    store = Store.open(tmp_path / "product_hunter.db")
    try:
        outcome = approve_candidate(
            store.conn,
            _candidate(),
            _policy(domains=["localhost"]),
            reviewer="operator",
        )
        assert outcome.approved is False
        assert outcome.reason_code == "invalid_entry"
        assert "localhost" in outcome.detail
        assert outcome.registry_entry == ""
        assert (
            store.conn.execute("SELECT count(*) FROM source_reviews").fetchone()[0]
            == 0
        )
    finally:
        store.close()


def test_an_approval_is_recorded_with_its_reviewer(tmp_path: Path):
    store = Store.open(tmp_path / "product_hunter.db")
    try:
        outcome = approve_candidate(
            store.conn, _candidate(), _policy(), reviewer="operator"
        )
        assert outcome.approved is True
        assert outcome.review_id
        row = store.conn.execute("SELECT * FROM source_reviews").fetchone()
        assert row["reviewer"] == "operator"
        assert row["outcome"] == "approved"
        assert row["terms_status"] == "reviewed"
        assert row["candidate_id"] == outcome.candidate_id
        candidate_row = store.conn.execute(
            "SELECT * FROM source_candidates"
        ).fetchone()
        assert candidate_row["status"] == "approved"
        # The candidate itself never carries a terms status.
        assert candidate_row["terms_status"] == "unknown"
    finally:
        store.close()


def test_the_decision_is_read_back_from_the_store(tmp_path: Path):
    store = Store.open(tmp_path / "product_hunter.db")
    try:
        approve_candidate(store.conn, _candidate(), _policy(), reviewer="operator")
        stored = list_candidates(store.conn)
        assert len(stored) == 1
        assert stored[0].decision == "approved"
        # The status is the proposal's own state, and it never changes.
        assert stored[0].status == "proposed"
    finally:
        store.close()


def test_the_approved_source_metadata_is_recorded(tmp_path: Path):
    store = Store.open(tmp_path / "product_hunter.db")
    try:
        outcome = approve_candidate(
            store.conn, _candidate(), _policy(), reviewer="operator"
        )
        entry = yaml.safe_load(outcome.registry_entry)
        assert entry[0]["source_id"] == "forum.example.com"
        assert entry[0]["domains"] == ["forum.example.com"]
        assert entry[0]["collector"] == "http"
        assert entry[0]["robots"] == "required"
        assert entry[0]["rate_limit_policy"]["max_requests_per_run"] == 10
        row = store.conn.execute("SELECT * FROM source_reviews").fetchone()
        assert json.loads(row["domains_json"]) == ["forum.example.com"]
        assert json.loads(row["allowed_use_json"]) == [
            "discovery",
            "retrieval",
            "storage",
        ]
        assert row["collector"] == "http"
        assert row["robots"] == "required"
    finally:
        store.close()


def test_a_rejection_is_recorded_with_its_reason(tmp_path: Path):
    store = Store.open(tmp_path / "product_hunter.db")
    try:
        outcome = reject_candidate(
            store.conn,
            _candidate(),
            reviewer="operator",
            reason="the terms forbid it",
        )
        assert outcome.approved is False
        assert outcome.reason_code == "rejected"
        row = store.conn.execute("SELECT * FROM source_reviews").fetchone()
        assert row["outcome"] == "rejected"
        assert row["reason"] == "the terms forbid it"
        assert row["reviewer"] == "operator"
        candidate_row = store.conn.execute(
            "SELECT * FROM source_candidates"
        ).fetchone()
        assert candidate_row["status"] == "rejected"
    finally:
        store.close()

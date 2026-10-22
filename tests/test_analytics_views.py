"""The views and the named queries: what the derived state answers."""

from __future__ import annotations

import json
import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest

from product_hunter.analytics import Analytics, AnalyticsError
from product_hunter.analytics.queries import (
    concentration_hotspots,
    growing_pain_points,
    independent_evidence_communities,
    opportunity_movement,
    run_budget_consumers,
    saturated_query_families,
    source_useful_yield,
)
from product_hunter.db.connection import connect
from product_hunter.db.migrations import migrate
from product_hunter.discovery.store import EVENT_TYPE
from product_hunter.projection.contract import CANONICAL_DDL

# The pain point keys the fixture holds. The first one grows, the second one
# falls, the third is a single sighting and the fourth is spread over months.
KEY_GROWING = "ops|paste|invoice|spreadsheet"
KEY_FALLING = "ops|email|contract|pdf"
KEY_SINGLE = "ops|manual|reconcile|ledger"
KEY_SPREAD = "ops|sync|inventory|warehouse"

# One evidence span per row: the span, the document, the day it was seen, the
# key it belongs to and whether the pain point was accepted.
EVIDENCE = (
    ("ev_1", "doc_1", "2026-09-10T00:00:00Z", KEY_GROWING, 1),
    ("ev_2", "doc_1", "2026-10-05T00:00:00Z", KEY_GROWING, 0),
    ("ev_3", "doc_2", "2026-10-20T00:00:00Z", KEY_GROWING, 0),
    ("ev_4", "doc_3", "2026-10-21T00:00:00Z", KEY_GROWING, 0),
    ("ev_5", "doc_4", "2026-10-22T00:00:00Z", KEY_GROWING, 1),
    ("ev_6", "doc_5", "2026-09-05T00:00:00Z", KEY_FALLING, 0),
    ("ev_7", "doc_5", "2026-09-20T00:00:00Z", KEY_FALLING, 0),
    ("ev_8", "doc_5", "2026-10-02T00:00:00Z", KEY_FALLING, 0),
    ("ev_9", "doc_5", "2026-10-03T00:00:00Z", KEY_SINGLE, 0),
    ("ev_10", "doc_6", "2026-08-01T00:00:00Z", KEY_SPREAD, 0),
    ("ev_11", "doc_6", "2026-09-01T00:00:00Z", KEY_SPREAD, 0),
    ("ev_12", "doc_6", "2026-10-05T00:00:00Z", KEY_SPREAD, 0),
)


@pytest.fixture(scope="session")
def template(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    """The fixture database and a snapshot of it, built once.

    Every test gets its own copy of the two files, so the tests stay
    independent without mirroring the whole schema again and again.
    """
    root = tmp_path_factory.mktemp("template")
    database = root / "product_hunter.db"
    conn = connect(database)
    migrate(conn)
    conn.executescript(CANONICAL_DDL)
    _write_fixture(conn)
    conn.close()
    derived = root / "analytics.duckdb"
    store = Analytics.open(derived, database)
    store.snapshot()
    store.close()
    return database, derived


@pytest.fixture
def db_path(tmp_path: Path, template: tuple[Path, Path]) -> Path:
    """A copy of the fixture database."""
    target = tmp_path / "product_hunter.db"
    shutil.copyfile(template[0], target)
    return target


@pytest.fixture
def store(
    db_path: Path, tmp_path: Path, template: tuple[Path, Path]
) -> Iterator[Analytics]:
    """The analytical store, open on the copied state."""
    derived = tmp_path / "analytics.duckdb"
    shutil.copyfile(template[1], derived)
    analytics = Analytics.open(derived, db_path)
    yield analytics
    analytics.close()


def _write_fixture(conn: object) -> None:
    """Write the rows the fixture holds."""
    conn.execute(
        "INSERT INTO sources (source_id, source_type, terms_status) VALUES "
        "('src_hn', 'aggregator', 'permitted'), "
        "('src_reddit', 'forum', 'permitted')"
    )
    conn.execute(
        "INSERT INTO fetches (fetch_id, source_id, requested_url, status, bytes) "
        "VALUES ('fetch_1', 'src_hn', 'https://example.test/one', 200, 1200), "
        "('fetch_2', 'src_reddit', 'https://example.test/two', 200, 2400)"
    )
    conn.execute(
        "INSERT INTO communities (community_id, source_id, name) VALUES "
        "('com_1', 'src_hn', 'alpha'), "
        "('com_2', 'src_reddit', 'beta'), "
        "('com_3', 'src_reddit', 'gamma')"
    )
    documents = (
        ("doc_1", "fetch_1", "src_hn", "com_1", "sha1"),
        ("doc_2", "fetch_1", "src_hn", "com_1", "sha2"),
        ("doc_3", "fetch_2", "src_reddit", "com_2", "sha3"),
        ("doc_4", "fetch_2", "src_reddit", "com_2", "sha4"),
        ("doc_5", "fetch_2", "src_reddit", "com_3", "sha5"),
        ("doc_6", "fetch_2", "src_reddit", "com_3", "sha6"),
    )
    for doc_id, fetch_id, source_id, community_id, digest in documents:
        conn.execute(
            "INSERT INTO documents (doc_id, fetch_id, source_id, community_id, url, "
            "text_sha256, text_path, raw_path, raw_sha256, parser_version) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'parser-v1')",
            (
                doc_id,
                fetch_id,
                source_id,
                community_id,
                f"https://example.test/{doc_id}",
                digest,
                f"text/{doc_id}.txt",
                f"raw/{doc_id}.json",
                f"raw-{digest}",
            ),
        )
    conn.execute(
        "INSERT INTO authors (author_id, source_id, handle, identity_confidence, "
        "cluster_id) VALUES "
        "('aut_1', 'src_hn', 'one', 0.9, 'clu_1'), "
        "('aut_2', 'src_reddit', 'two', 0.8, 'clu_2'), "
        "('aut_3', 'src_reddit', 'three', 0.2, 'clu_3'), "
        "('aut_4', 'src_reddit', 'four', 0.85, 'clu_4')"
    )
    conn.execute(
        "INSERT INTO author_links (link_id, author_id, doc_id, confidence, basis) "
        "VALUES "
        "('link_1', 'aut_1', 'doc_1', 0.9, 'handle'), "
        "('link_2', 'aut_1', 'doc_2', 0.9, 'handle'), "
        "('link_3', 'aut_3', 'doc_3', 0.2, 'handle'), "
        "('link_4', 'aut_2', 'doc_4', 0.8, 'handle'), "
        "('link_5', 'aut_2', 'doc_5', 0.8, 'handle'), "
        "('link_6', 'aut_2', 'doc_1', 0.3, 'signature'), "
        "('link_7', 'aut_4', 'doc_6', 0.85, 'handle')"
    )
    for span_id, doc_id, created_at, _key, _accepted in EVIDENCE:
        conn.execute(
            "INSERT INTO evidence (span_id, doc_id, quote, char_start, char_end, "
            "created_at) VALUES (?, ?, 'a quote', 0, 10, ?)",
            (span_id, doc_id, created_at),
        )
    for index, (span_id, doc_id, created_at, key, accepted) in enumerate(EVIDENCE, 1):
        conn.execute(
            'INSERT INTO pain_points (pain_id, span_id, doc_id, domain, actor, '
            'action, "object", consequence, severity, frequency, normalized_key, '
            "accepted, created_at) "
            "VALUES (?, ?, ?, 'operations', 'finance lead', 'paste totals', "
            "'spreadsheet', 'a lost Saturday', 4, 3, ?, ?, ?)",
            (f"pp_{index}", span_id, doc_id, key, accepted, created_at),
        )
    conn.execute(
        "INSERT INTO clusters (cluster_id, status, label, created_at, updated_at) "
        "VALUES ('cluster_1', 'active', 'invoice totals', "
        "'2026-10-01T00:00:00Z', '2026-10-01T00:00:00Z')"
    )
    for index in range(1, 6):
        conn.execute(
            "INSERT INTO cluster_members (member_id, cluster_id, pain_id, valid_from, "
            "basis) VALUES (?, 'cluster_1', ?, '2026-10-01T00:00:00Z', 'embedding')",
            (f"member_{index}", f"pp_{index}"),
        )
    conn.execute(
        "INSERT INTO opportunity_scores (score_id, opportunity_id, potential, "
        "confidence, created_at) VALUES "
        "('sc_1', 'opp_1', 0.5, 0.4, '2026-10-01T00:00:00Z'), "
        "('sc_2', 'opp_1', 0.7, 0.6, '2026-10-15T00:00:00Z')"
    )
    conn.execute(
        "INSERT INTO research_runs (run_id, public_id, objective, original_request, "
        "status, created_at, started_at, completed_at) VALUES "
        "('run_1', 'pub_1', 'find the invoice pain', 'find the invoice pain', "
        "'COMPLETED', '2026-10-20T10:00:00Z', '2026-10-20T10:01:00Z', "
        "'2026-10-20T10:11:00Z'), "
        "('run_2', 'pub_2', 'find the vendor pain', 'find the vendor pain', "
        "'RUNNING', '2026-10-21T10:00:00Z', '2026-10-21T10:01:00Z', NULL)"
    )
    conn.execute(
        "INSERT INTO tool_calls (tool_call_id, run_id, stage, agent, tool, purpose, "
        "query_or_operation, status, occurred_at, duration_ms, result_count) VALUES "
        "('tc_1', 'run_1', 'discovery', 'discovery', 'search', 'find evidence', "
        "'invoice spreadsheet', 'success', '2026-10-20T10:02:00Z', 120, 10), "
        "('tc_2', 'run_1', 'extraction', 'extractor', 'extract', 'read the quotes', "
        "'span extraction', 'failure', '2026-10-20T10:03:00Z', 40, 0), "
        "('tc_3', 'run_1', 'discovery', 'discovery', 'search', 'find evidence', "
        "'invoice vendors', 'success', '2026-10-20T10:04:00Z', 90, 4)"
    )
    conn.execute(
        "INSERT INTO stage_gate_results (gate_result_id, run_id, stage, passed, "
        "violations_json, metrics_json, checked_at) VALUES "
        "('gr_1', 'run_1', 'extraction', 1, '[]', '{}', '2026-10-20T10:05:00Z'), "
        "('gr_2', 'run_1', NULL, 0, '[\"no evidence\"]', '{}', "
        "'2026-10-20T10:06:00Z')"
    )
    conn.execute(
        "INSERT INTO spend_ledger (entry_id, run_id, stage, tool, provider, model, "
        "units, unit_type, cost_micros, created_at) VALUES "
        "('spn_1', 'run_1', 'discovery', 'search', 'opencode-go', 'model-a', 3, "
        "'search_queries', 300, '2026-10-20T10:02:00Z'), "
        "('spn_2', 'run_1', 'extraction', 'extract', 'opencode-go', 'model-a', 1, "
        "'calls', 700, '2026-10-20T10:03:00Z'), "
        "('spn_3', 'run_2', 'discovery', 'search', 'opencode-go', 'model-a', 1, "
        "'search_queries', 100, '2026-10-21T10:02:00Z')"
    )
    conn.execute(
        "INSERT INTO collection_items (run_id, source_id, url, attempt_no, outcome, "
        "reason_code, detail, latency_ms, bytes, attempted_at) VALUES "
        "('run_1', 'src_hn', 'https://example.test/one', 1, 'fetched', '', '', "
        "100, 1200, '2026-10-20T10:02:00Z'), "
        "('run_1', 'src_hn', 'https://example.test/two', 1, 'failed', 'http_500', "
        "'the server said no', 50, 0, '2026-10-20T10:03:00Z')"
    )
    records = (
        (
            "dq_1",
            "workflow_pain",
            "invoice spreadsheet",
            10,
            2,
            0.05,
            0.95,
            1.0,
        ),
        ("dq_2", "competitor", "invoice vendors", 10, 5, 0.5, 0.5, 2.0),
    )
    for index, record in enumerate(records, 1):
        record_id, family, query, results, useful, novelty, duplicate, cost = record
        payload = json.dumps(
            {
                "record_id": record_id,
                "run_id": "run_1",
                "family": family,
                "query": query,
                "status": "success",
                "result_count": results,
                "useful_count": useful,
                "novelty": novelty,
                "duplicate_rate": duplicate,
                "cost": cost,
            }
        )
        conn.execute(
            "INSERT INTO research_events (event_id, run_id, event_type, payload_json, "
            "created_at) VALUES (?, 'run_1', ?, ?, '2026-10-20T10:07:00Z')",
            (f"evt_{index}", EVENT_TYPE, payload),
        )


def test_the_views_are_created(store: Analytics) -> None:
    """Every view whose tables are there is created."""
    expected = {
        "v_best_author",
        "v_evidence_mentions",
        "v_pain_point_evidence",
        "v_pain_point_trend",
        "v_source_yield",
        "v_community_evidence",
        "v_independence_summary",
        "v_source_concentration",
        "v_opportunity_evidence_trend",
        "v_score_history",
        "v_run_analytics",
        "v_run_budget",
        "v_collection_health",
        "v_discovery_yield",
        "v_query_family_performance",
        "v_temporal_persistence",
    }
    assert expected <= set(store.views())


def test_the_score_history_reads_the_scores(store: Analytics) -> None:
    """Two score rows become two history rows with the change between them."""
    rows = store.sql(
        "SELECT score_id, potential, confidence, potential_change, confidence_change "
        "FROM v_score_history ORDER BY created_at, score_id"
    )
    assert len(rows) == 2
    assert rows[0]["potential_change"] is None
    assert rows[1]["potential_change"] == pytest.approx(0.2)
    assert rows[1]["confidence_change"] == pytest.approx(0.2)


def test_a_pain_point_that_is_growing_is_found(store: Analytics) -> None:
    """The pain point with the rising monthly count is in the trend view."""
    trend = store.sql(
        "SELECT month, mention_count, mention_change FROM v_pain_point_trend "
        "WHERE normalized_key = ? ORDER BY month",
        [KEY_GROWING],
    )
    assert [row["mention_count"] for row in trend] == [1, 4]
    assert trend[1]["mention_change"] == 3
    growing = growing_pain_points(store)
    assert [row["normalized_key"] for row in growing] == [KEY_GROWING]


def test_a_stale_pain_point_is_not_reported_as_growing(store: Analytics) -> None:
    """A pain point whose counts fall is not reported as growing."""
    trend = store.sql(
        "SELECT month, mention_count, mention_change FROM v_pain_point_trend "
        "WHERE normalized_key = ? ORDER BY month",
        [KEY_FALLING],
    )
    assert [row["mention_count"] for row in trend] == [2, 1]
    growing = growing_pain_points(store)
    assert KEY_FALLING not in [row["normalized_key"] for row in growing]


def test_the_concentration_view_names_the_source(store: Analytics) -> None:
    """The source that holds the evidence has the largest share."""
    rows = store.sql(
        "SELECT normalized_key, source_count, largest_source_share, "
        "top_three_share, herfindahl_index, concentrated, top_source_id "
        "FROM v_source_concentration WHERE normalized_key = ?",
        [KEY_GROWING],
    )
    assert len(rows) == 1
    row = rows[0]
    assert row["top_source_id"] == "src_hn"
    assert row["source_count"] == 2
    assert row["largest_source_share"] == pytest.approx(0.6)
    assert row["herfindahl_index"] == pytest.approx(0.36 + 0.16)
    assert row["concentrated"] is True
    hotspots = concentration_hotspots(store)
    assert KEY_GROWING in [row["normalized_key"] for row in hotspots]
    assert hotspots[0]["largest_source_share"] == pytest.approx(1.0)


def test_the_independence_counters_are_counted(store: Analytics) -> None:
    """The independent, uncertain and community counts match the fixture."""
    rows = store.sql(
        "SELECT * FROM v_independence_summary WHERE normalized_key = ?",
        [KEY_GROWING],
    )
    assert len(rows) == 1
    row = rows[0]
    assert row["raw_mentions"] == 5
    assert row["canonical_observations"] == 4
    assert row["independent_authors"] == 2
    assert row["uncertain_authors"] == 1
    assert row["independent_communities"] == 2
    assert row["propagation_adjusted_evidence"] == 5
    assert row["source_concentration"] == pytest.approx(0.6)


def test_the_query_family_view_marks_the_saturated_family(store: Analytics) -> None:
    """The family with the low novelty is marked saturated."""
    rows = store.sql(
        "SELECT family, mean_novelty, saturated FROM v_query_family_performance "
        "ORDER BY family"
    )
    by_family = {row["family"]: row for row in rows}
    assert by_family["workflow_pain"]["saturated"] is True
    assert by_family["competitor"]["saturated"] is False
    saturated = saturated_query_families(store)
    assert [row["family"] for row in saturated] == ["workflow_pain"]


def test_the_run_budget_view_ranks_the_runs(store: Analytics) -> None:
    """The run with the larger spend is first."""
    rows = run_budget_consumers(store)
    assert [row["run_id"] for row in rows] == ["run_1", "run_2"]
    assert rows[0]["cost_micros"] == 1000
    assert rows[0]["units"] == 4
    assert rows[0]["total_cost_micros"] == 1100
    assert rows[0]["cost_share"] == pytest.approx(1000 / 1100)


def test_the_collection_health_view_counts_the_outcomes(store: Analytics) -> None:
    """The success and the failure are counted."""
    rows = store.sql("SELECT * FROM v_collection_health WHERE source_id = 'src_hn'")
    assert len(rows) == 1
    row = rows[0]
    assert row["attempts"] == 2
    assert row["successes"] == 1
    assert row["failures"] == 1
    assert row["success_rate"] == pytest.approx(0.5)
    assert row["mean_latency_ms"] == pytest.approx(100)
    assert row["total_bytes"] == 1200
    assert row["distinct_reason_codes"] == 1


def test_the_temporal_persistence_view_classifies(store: Analytics) -> None:
    """A single sighting is a single sighting and a spread one persists."""
    rows = {
        row["normalized_key"]: row
        for row in store.sql("SELECT * FROM v_temporal_persistence")
    }
    assert rows[KEY_SINGLE]["persistence_class"] == "single_sighting"
    assert rows[KEY_SINGLE]["active_months"] == 1
    assert rows[KEY_SPREAD]["persistence_class"] == "persistent"
    assert rows[KEY_SPREAD]["active_months"] == 3
    assert rows[KEY_GROWING]["persistence_class"] == "emerging"


def test_the_community_view_counts_the_independent_authors(store: Analytics) -> None:
    """The communities are ranked by the independent evidence they hold."""
    rows = independent_evidence_communities(store)
    by_community = {row["community_id"]: row for row in rows}
    assert by_community["com_1"]["mention_count"] == 3
    assert by_community["com_1"]["independent_authors"] == 1
    assert by_community["com_2"]["independent_authors"] == 1
    assert by_community["com_2"]["uncertain_authors"] == 1
    assert by_community["com_3"]["independent_authors"] == 2
    assert rows[0]["community_id"] == "com_3"


def test_the_source_view_measures_the_useful_yield(store: Analytics) -> None:
    """The source with the most useful evidence per document is first."""
    rows = source_useful_yield(store)
    assert [row["source_id"] for row in rows] == ["src_hn", "src_reddit"]
    assert rows[0]["document_count"] == 2
    assert rows[0]["accepted_pain_points"] == 1
    assert rows[0]["useful_evidence_yield"] == pytest.approx(0.5)
    assert rows[0]["fetch_count"] == 1


def test_the_opportunity_view_follows_the_cluster(store: Analytics) -> None:
    """The cluster's evidence is counted by month."""
    rows = store.sql(
        "SELECT month, mention_count, mention_change FROM v_opportunity_evidence_trend "
        "ORDER BY month"
    )
    assert [row["mention_count"] for row in rows] == [1, 4]
    movement = opportunity_movement(store)
    assert [row["cluster_id"] for row in movement] == ["cluster_1"]
    assert movement[0]["mention_change"] == 3


def test_a_missing_view_is_refused_by_name(store: Analytics) -> None:
    """A query whose view is gone says which view is gone."""
    store.sql("DROP VIEW v_pain_point_trend")
    with pytest.raises(AnalyticsError) as failure:
        growing_pain_points(store)
    assert "v_pain_point_trend" in str(failure.value)


def test_an_author_without_an_identity_group_counts_on_its_own(
    tmp_path: Path,
) -> None:
    """Two authors without a group do not collapse into one holder.

    The independence package counts a mention whose author has no identity
    group against that author's own account. The view has to count it the
    same way, or the concentration says one account holds the evidence when
    two do.
    """
    database = tmp_path / "loose.db"
    conn = connect(database)
    migrate(conn)
    conn.execute(
        "INSERT INTO sources (source_id, source_type, terms_status) "
        "VALUES ('src_x', 'forum', 'permitted')"
    )
    conn.execute(
        "INSERT INTO fetches (fetch_id, source_id, requested_url, status, bytes) "
        "VALUES ('fetch_x', 'src_x', 'https://example.test/x', 200, 10)"
    )
    conn.execute(
        "INSERT INTO authors (author_id, source_id, handle, identity_confidence) "
        "VALUES ('aut_x', 'src_x', 'x', 0.9), ('aut_y', 'src_x', 'y', 0.9)"
    )
    for index, (doc_id, author_id) in enumerate(
        (("doc_x", "aut_x"), ("doc_y", "aut_y")), 1
    ):
        conn.execute(
            "INSERT INTO documents (doc_id, fetch_id, source_id, url, text_sha256, "
            "text_path, raw_path, raw_sha256, parser_version) "
            "VALUES (?, 'fetch_x', 'src_x', ?, ?, 't.txt', 'r.json', 'rsha', 'p1')",
            (doc_id, f"https://example.test/{doc_id}", f"sha_{index}"),
        )
        conn.execute(
            "INSERT INTO author_links (link_id, author_id, doc_id, confidence, basis) "
            "VALUES (?, ?, ?, 0.9, 'handle')",
            (f"link_{index}", author_id, doc_id),
        )
        conn.execute(
            "INSERT INTO evidence (span_id, doc_id, quote, char_start, char_end, "
            "created_at) VALUES (?, ?, 'a quote', 0, 10, '2026-10-01T00:00:00Z')",
            (f"ev_{index}", doc_id),
        )
        conn.execute(
            'INSERT INTO pain_points (pain_id, span_id, doc_id, domain, actor, '
            'action, "object", consequence, severity, frequency, normalized_key, '
            "accepted, created_at) "
            "VALUES (?, ?, ?, 'operations', 'finance lead', 'paste totals', "
            "'spreadsheet', 'a lost Saturday', 4, 3, 'ops|paste|invoice|spreadsheet', "
            "1, '2026-10-01T00:00:00Z')",
            (f"pp_{index}", f"ev_{index}", doc_id),
        )
    conn.close()
    analytics = Analytics.open(tmp_path / "loose.duckdb", database)
    try:
        analytics.snapshot()
        rows = analytics.sql(
            "SELECT * FROM v_independence_summary WHERE normalized_key = ?",
            ["ops|paste|invoice|spreadsheet"],
        )
        assert len(rows) == 1
        # Two mentions, one per account, so no account holds more than half.
        assert rows[0]["raw_mentions"] == 2
        assert rows[0]["author_cluster_concentration"] == pytest.approx(0.5)
    finally:
        analytics.close()

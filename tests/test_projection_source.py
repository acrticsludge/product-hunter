"""The reader turns the authoritative database into a snapshot.

The reader is tested against a temporary database that holds the tables the
projection reads. The canonical tables of the contract are applied to it, so
a change to the contract shows up here.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from product_hunter.db import connect, migrate
from product_hunter.projection.contract import CANONICAL_DDL
from product_hunter.projection.source import SqliteProjectionSource

KEY = "operations|finance lead|paste totals|spreadsheet|a lost Saturday"
OTHER_KEY = "marketing|marketer|write reports|spreadsheet|another lost evening"


def _database(tmp_path: Path) -> sqlite3.Connection:
    """A database with the tables the projection reads, and rows in them."""
    conn = connect(tmp_path / "store.db")
    migrate(conn)
    _seed(conn)
    return conn


def _seed(conn: sqlite3.Connection) -> None:
    conn.executemany(
        "INSERT INTO sources (source_id, source_type, terms_status, enabled) "
        "VALUES (?, ?, ?, ?)",
        [
            ("hn_algolia", "forum", "permitted", 1),
            ("github_issues", "repo", "permitted", 1),
        ],
    )
    conn.executemany(
        "INSERT INTO fetches (fetch_id, source_id, requested_url, status) "
        "VALUES (?, ?, ?, 200)",
        [
            ("f1", "hn_algolia", "https://news.ycombinator.com/item?id=1"),
            ("f2", "hn_algolia", "https://news.ycombinator.com/item?id=2"),
            ("f3", "hn_algolia", "https://news.ycombinator.com/item?id=3"),
            ("f4", "github_issues", "https://github.com/acme/app/issues/7"),
        ],
    )
    conn.executemany(
        "INSERT INTO communities (community_id, source_id, name, url) "
        "VALUES (?, ?, ?, ?)",
        [
            ("co_1", "hn_algolia", "Hacker News", "https://news.ycombinator.com"),
            ("co_2", "github_issues", "acme/app", "https://github.com/acme/app"),
        ],
    )
    conn.executemany(
        "INSERT INTO documents (doc_id, fetch_id, source_id, community_id, url,"
        " title, author, text_sha256, text_path, raw_path, raw_sha256,"
        " parser_version, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'p', 'p',"
        " 'r', 'v1', ?)",
        [
            (
                "doc_1",
                "f1",
                "hn_algolia",
                "co_1",
                "https://news.ycombinator.com/item?id=1",
                "a thread",
                "alice",
                "hash-1",
                "2026-10-18T08:00:00Z",
            ),
            (
                "doc_2",
                "f2",
                "hn_algolia",
                "co_1",
                "https://news.ycombinator.com/item?id=2",
                "another thread",
                "bob",
                "hash-2",
                "2026-10-19T08:00:00Z",
            ),
            (
                "doc_3",
                "f3",
                "hn_algolia",
                "co_1",
                "https://news.ycombinator.com/item?id=3",
                "a copy",
                "bob",
                "hash-2",
                "2026-10-19T08:00:00Z",
            ),
            (
                "doc_4",
                "f4",
                "github_issues",
                "co_2",
                "https://github.com/acme/app/issues/7",
                "an issue",
                "carol",
                "hash-3",
                "2026-10-20T08:00:00Z",
            ),
        ],
    )
    conn.executemany(
        "INSERT INTO authors (author_id, source_id, handle, display_name) "
        "VALUES (?, ?, ?, ?)",
        [
            ("au_1", "hn_algolia", "alice", "Alice"),
            ("au_2", "hn_algolia", "bob", "Bob"),
            ("au_3", "github_issues", "carol", "Carol"),
        ],
    )
    conn.executemany(
        "INSERT INTO author_links (link_id, author_id, doc_id, confidence, basis) "
        "VALUES (?, ?, ?, ?, 'declared')",
        [
            ("al_1", "au_1", "doc_1", 0.9),
            ("al_2", "au_2", "doc_2", 0.9),
            ("al_3", "au_2", "doc_3", 0.9),
            ("al_4", "au_3", "doc_4", 0.3),
        ],
    )
    conn.executemany(
        "INSERT INTO evidence (span_id, doc_id, quote, char_start, char_end,"
        " match_mode, created_at) VALUES (?, ?, ?, 0, 10, 'exact', ?)",
        [
            (
                "ev_1",
                "doc_1",
                "We paste the invoice totals by hand every month.",
                "2026-10-18T09:00:00Z",
            ),
            (
                "ev_2",
                "doc_2",
                "Nobody wants another invoicing tool.",
                "2026-10-19T09:00:00Z",
            ),
            (
                "ev_3",
                "doc_3",
                "Nobody wants another invoicing tool.",
                "2026-10-19T10:00:00Z",
            ),
            (
                "ev_4",
                "doc_4",
                "The export is still a CSV we paste into a sheet.",
                "2026-10-20T09:00:00Z",
            ),
            (
                "ev_5",
                "doc_1",
                "We would pay for a tool that does this.",
                "2026-10-20T10:00:00Z",
            ),
        ],
    )
    conn.executemany(
        "INSERT INTO pain_points (pain_id, span_id, doc_id, domain, actor, action,"
        " object, consequence, severity, frequency, pain_statement, normalized_key,"
        " accepted, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)",
        [
            (
                "pp_1",
                "ev_1",
                "doc_1",
                "operations",
                "finance lead",
                "paste totals",
                "spreadsheet",
                "a lost Saturday",
                4,
                "monthly",
                "the team pastes invoice totals by hand",
                KEY,
                "2026-10-18T09:00:00Z",
            ),
            (
                "pp_2",
                "ev_2",
                "doc_2",
                "operations",
                "finance lead",
                "paste totals",
                "spreadsheet",
                "a lost Saturday",
                3,
                "monthly",
                "the same paste happens in every invoice run",
                KEY,
                "2026-10-19T09:00:00Z",
            ),
            (
                "pp_3",
                "ev_3",
                "doc_3",
                "operations",
                "finance lead",
                "paste totals",
                "spreadsheet",
                "a lost Saturday",
                3,
                "monthly",
                "the paste comes up again in every thread",
                KEY,
                "2026-10-19T10:00:00Z",
            ),
            (
                "pp_4",
                "ev_4",
                "doc_4",
                "operations",
                "finance lead",
                "paste totals",
                "spreadsheet",
                "a lost Saturday",
                2,
                "monthly",
                "the export is still a CSV to paste by hand",
                KEY,
                "2026-10-20T09:00:00Z",
            ),
            (
                "pp_5",
                "ev_5",
                "doc_1",
                "marketing",
                "marketer",
                "write reports",
                "spreadsheet",
                "another lost evening",
                5,
                "weekly",
                "a marketer writes the weekly report by hand",
                OTHER_KEY,
                "2026-10-20T10:00:00Z",
            ),
        ],
    )
    conn.executemany(
        "INSERT INTO evidence_pain_links (pain_id, span_id, created_at) "
        "VALUES (?, ?, ?)",
        [
            ("pp_1", "ev_1", "2026-10-18T09:00:00Z"),
            ("pp_2", "ev_2", "2026-10-19T09:00:00Z"),
            ("pp_3", "ev_3", "2026-10-19T10:00:00Z"),
            ("pp_4", "ev_4", "2026-10-20T09:00:00Z"),
            ("pp_5", "ev_5", "2026-10-20T10:00:00Z"),
        ],
    )
    conn.execute(
        "INSERT INTO propagation_edges (edge_id, from_doc_id, to_doc_id, relation,"
        " confidence, created_at) VALUES ('pe_1', 'doc_2', 'doc_3', 'copy', 0.8,"
        " '2026-10-19T11:00:00Z')"
    )
    conn.executemany(
        "INSERT INTO human_decisions (decision_id, entity_type, entity_id, decision,"
        " created_at) VALUES (?, 'pain_point', ?, ?, ?)",
        [
            ("hd_1", "pp_5", "human_review", "2026-10-20T12:00:00Z"),
            ("hd_2", "pp_1", "shipped", "2026-10-20T13:00:00Z"),
        ],
    )
    conn.commit()


def _snapshot(conn: sqlite3.Connection, *, as_of: str | None = None):
    return SqliteProjectionSource(conn).snapshot(as_of=as_of)


def _by_key(snapshot, key: str):
    for opportunity in snapshot.opportunities:
        if opportunity.opportunity_id.startswith("opp_") and key in opportunity.title:
            return opportunity
    raise AssertionError(f"no opportunity for {key!r}")


# --- the fallback path ------------------------------------------------------


def test_pain_points_are_grouped_into_opportunities(tmp_path: Path) -> None:
    conn = _database(tmp_path)
    try:
        snapshot = _snapshot(conn)
        assert len(snapshot.opportunities) == 2
    finally:
        conn.close()


def test_the_counts_are_counted_and_not_estimated(tmp_path: Path) -> None:
    conn = _database(tmp_path)
    try:
        snapshot = _snapshot(conn)
        opportunity = next(
            item
            for item in snapshot.opportunities
            if len(item.evidence) == 4
        )
        summary = opportunity.evidence_summary
        assert summary.mentions == 4
        assert summary.canonical_documents == 3
        assert summary.independent_authors == 2
        assert summary.uncertain_authors == 1
        assert summary.independent_communities == 2
        assert summary.propagated_copies == 1
        assert summary.source_concentration == 0.75
    finally:
        conn.close()


def test_an_author_with_a_low_confidence_link_is_uncertain(tmp_path: Path) -> None:
    conn = _database(tmp_path)
    try:
        snapshot = _snapshot(conn)
        opportunity = next(
            item for item in snapshot.opportunities if len(item.evidence) == 4
        )
        carol = [
            ref for ref in opportunity.evidence if ref.author_handle == "carol"
        ]
        assert len(carol) == 1
        assert opportunity.evidence_summary.independent_authors == 2
    finally:
        conn.close()


def test_the_severity_is_the_highest_of_the_group(tmp_path: Path) -> None:
    conn = _database(tmp_path)
    try:
        snapshot = _snapshot(conn)
        opportunity = next(
            item for item in snapshot.opportunities if len(item.evidence) == 4
        )
        assert opportunity.severity == 4.0
    finally:
        conn.close()


def test_a_field_the_database_does_not_hold_stays_empty(tmp_path: Path) -> None:
    conn = _database(tmp_path)
    try:
        snapshot = _snapshot(conn)
        opportunity = snapshot.opportunities[0]
        assert opportunity.potential is None
        assert opportunity.confidence is None
        assert opportunity.wtp_tier is None
        assert opportunity.competition is None
        assert opportunity.uncertainty is None
        assert opportunity.recommended_experiment is None
    finally:
        conn.close()


def test_a_human_decision_names_the_lifecycle(tmp_path: Path) -> None:
    conn = _database(tmp_path)
    try:
        snapshot = _snapshot(conn)
        reviewed = [
            item for item in snapshot.opportunities if item.lifecycle == "HUMAN_REVIEW"
        ]
        assert len(reviewed) == 1
    finally:
        conn.close()


def test_an_unknown_decision_leaves_the_lifecycle_at_candidate(
    tmp_path: Path,
) -> None:
    conn = _database(tmp_path)
    try:
        snapshot = _snapshot(conn)
        opportunity = next(
            item for item in snapshot.opportunities if len(item.evidence) == 4
        )
        assert opportunity.lifecycle == "CANDIDATE"
    finally:
        conn.close()


def test_the_reference_date_is_the_newest_date_in_the_data(tmp_path: Path) -> None:
    conn = _database(tmp_path)
    try:
        assert _snapshot(conn).reference_date == "2026-10-20"
    finally:
        conn.close()


def test_the_caller_can_pass_the_reference_date(tmp_path: Path) -> None:
    conn = _database(tmp_path)
    try:
        assert _snapshot(conn, as_of="2026-10-01").reference_date == "2026-10-01"
    finally:
        conn.close()


def test_the_first_and_last_seen_come_from_the_evidence(tmp_path: Path) -> None:
    conn = _database(tmp_path)
    try:
        snapshot = _snapshot(conn)
        opportunity = next(
            item for item in snapshot.opportunities if len(item.evidence) == 4
        )
        assert opportunity.first_seen == "2026-10-18"
        assert opportunity.last_updated == "2026-10-20"
    finally:
        conn.close()


def test_the_sources_and_communities_are_read(tmp_path: Path) -> None:
    conn = _database(tmp_path)
    try:
        snapshot = _snapshot(conn)
        assert [source.source_id for source in snapshot.sources] == [
            "github_issues",
            "hn_algolia",
        ]
        assert [community.community_id for community in snapshot.communities] == [
            "co_1",
            "co_2",
        ]
    finally:
        conn.close()


def test_the_markets_are_the_domains(tmp_path: Path) -> None:
    conn = _database(tmp_path)
    try:
        snapshot = _snapshot(conn)
        assert [market.market for market in snapshot.markets] == [
            "marketing",
            "operations",
        ]
    finally:
        conn.close()


# --- the canonical path -----------------------------------------------------


def _canonical_database(tmp_path: Path) -> sqlite3.Connection:
    conn = _database(tmp_path)
    conn.executescript(CANONICAL_DDL)
    conn.execute(
        "INSERT INTO opportunities (opportunity_id, title, normalized_problem,"
        " domain, actor, action, object, consequence, severity, frequency,"
        " wtp_tier, competition, potential, confidence, uncertainty, lifecycle,"
        " recommended_experiment, first_seen, last_updated, created_at,"
        " updated_at) VALUES ('opp_canonical', 'Invoice reconciliation by hand',"
        " 'the team reconciles invoices by hand', 'finance', 'accountant',"
        " 'reconcile', 'invoice', 'a late payment', 7.5, 'weekly',"
        " 'would pay 200 a month', 0.4, 88.0, 0.7, 'one company so far',"
        " 'PROMISING', 'a desktop trial with ten accountants', '2026-10-01',"
        " '2026-10-19', '2026-10-01T00:00:00Z', '2026-10-19T00:00:00Z')"
    )
    conn.executemany(
        "INSERT INTO opportunity_evidence (opportunity_id, span_id, stance,"
        " created_at) VALUES (?, ?, ?, '2026-10-19T00:00:00Z')",
        [
            ("opp_canonical", "ev_1", "supporting"),
            ("opp_canonical", "ev_4", "counter"),
        ],
    )
    conn.executemany(
        "INSERT INTO opportunity_scores (score_id, opportunity_id, potential,"
        " confidence, created_at) VALUES (?, ?, ?, ?, ?)",
        [
            ("sc_1", "opp_canonical", 60.0, 0.5, "2026-10-10T00:00:00Z"),
            ("sc_2", "opp_canonical", 88.0, 0.7, "2026-10-19T00:00:00Z"),
        ],
    )
    conn.execute(
        "INSERT INTO opportunity_claims (claim_id, opportunity_id, statement, kind,"
        " span_id, created_at) VALUES ('cl_1', 'opp_canonical', 'the paste costs a"
        " morning every week', 'workflow', 'ev_1', '2026-10-19T00:00:00Z')"
    )
    conn.commit()
    return conn


def test_the_canonical_table_is_the_source_when_it_is_there(tmp_path: Path) -> None:
    conn = _canonical_database(tmp_path)
    try:
        snapshot = _snapshot(conn)
        assert len(snapshot.opportunities) == 1
        opportunity = snapshot.opportunities[0]
        assert opportunity.opportunity_id == "opp_canonical"
        assert opportunity.potential == 88.0
        assert opportunity.confidence == 0.7
        assert opportunity.wtp_tier == "would pay 200 a month"
        assert opportunity.competition == 0.4
        assert opportunity.uncertainty == "one company so far"
        assert opportunity.recommended_experiment == "a desktop trial with ten accountants"
        assert opportunity.lifecycle == "PROMISING"
        assert opportunity.severity == 7.5
    finally:
        conn.close()


def test_the_trend_compares_the_newest_score_with_the_one_before(
    tmp_path: Path,
) -> None:
    conn = _canonical_database(tmp_path)
    try:
        snapshot = _snapshot(conn)
        assert snapshot.opportunities[0].trend == "rising"
    finally:
        conn.close()


def test_one_score_is_not_a_movement(tmp_path: Path) -> None:
    conn = _canonical_database(tmp_path)
    try:
        conn.execute("DELETE FROM opportunity_scores WHERE score_id = 'sc_1'")
        conn.commit()
        assert _snapshot(conn).opportunities[0].trend == "not recorded"
    finally:
        conn.close()


def test_the_evidence_table_names_the_stance_of_each_span(tmp_path: Path) -> None:
    conn = _canonical_database(tmp_path)
    try:
        opportunity = _snapshot(conn).opportunities[0]
        stances = {ref.span_id: ref.stance for ref in opportunity.evidence}
        assert stances == {"ev_1": "supporting", "ev_4": "counter"}
    finally:
        conn.close()


def test_a_claim_names_the_span_behind_it(tmp_path: Path) -> None:
    conn = _canonical_database(tmp_path)
    try:
        claims = _snapshot(conn).opportunities[0].claims
        assert claims == [
            "the paste costs a morning every week (spans: ev_1)"
        ]
    finally:
        conn.close()


def test_a_canonical_row_falls_back_to_the_pain_points(tmp_path: Path) -> None:
    """A canonical row that leaves a field empty falls back, field by field."""
    conn = _database(tmp_path)
    try:
        conn.executescript(CANONICAL_DDL)
        conn.execute(
            "INSERT INTO opportunities (opportunity_id, title, created_at,"
            " updated_at) VALUES ('opp_canonical', 'the team pastes invoice"
            " totals by hand', '2026-10-01T00:00:00Z', '2026-10-19T00:00:00Z')"
        )
        conn.commit()
        opportunity = _snapshot(conn).opportunities[0]
        assert opportunity.statement == "the team pastes invoice totals by hand"
        assert opportunity.domain == "operations"
        assert opportunity.actor == "finance lead"
        assert opportunity.action == "paste totals"
        assert opportunity.object == "spreadsheet"
        assert opportunity.consequence == "a lost Saturday"
        assert opportunity.severity == 4.0
    finally:
        conn.close()


def test_a_canonical_row_wins_over_the_pain_points(tmp_path: Path) -> None:
    conn = _database(tmp_path)
    try:
        conn.executescript(CANONICAL_DDL)
        conn.execute(
            "INSERT INTO opportunities (opportunity_id, title, normalized_problem,"
            " domain, actor, action, object, consequence, severity, created_at,"
            " updated_at) VALUES ('opp_canonical', 'the team pastes invoice totals"
            " by hand', 'the canonical statement', 'finance', 'accountant',"
            " 'reconcile', 'invoice', 'a late payment', 7.5, '2026-10-01T00:00:00Z',"
            " '2026-10-19T00:00:00Z')"
        )
        conn.commit()
        opportunity = _snapshot(conn).opportunities[0]
        assert opportunity.statement == "the canonical statement"
        assert opportunity.domain == "finance"
        assert opportunity.actor == "accountant"
        assert opportunity.action == "reconcile"
        assert opportunity.object == "invoice"
        assert opportunity.consequence == "a late payment"
        assert opportunity.severity == 7.5
    finally:
        conn.close()


# --- the tolerance of a different shape -------------------------------------


def test_a_table_with_a_different_shape_degrades_the_projection(
    tmp_path: Path,
) -> None:
    conn = _database(tmp_path)
    try:
        conn.execute("DROP TABLE opportunities")
        conn.execute(
            "CREATE TABLE opportunities (opportunity_id TEXT PRIMARY KEY NOT NULL,"
            " title TEXT NOT NULL, potential REAL)"
        )
        conn.execute(
            "INSERT INTO opportunities (opportunity_id, title, potential) VALUES"
            " ('opp_other', 'Another shape', 12.0)"
        )
        conn.commit()
        snapshot = _snapshot(conn)
        assert len(snapshot.opportunities) == 1
        opportunity = snapshot.opportunities[0]
        assert opportunity.potential == 12.0
        assert opportunity.wtp_tier is None
        assert opportunity.lifecycle == "CANDIDATE"
        assert opportunity.evidence == []
    finally:
        conn.close()


def test_a_quote_that_is_not_valid_utf8_is_read(tmp_path: Path) -> None:
    conn = _database(tmp_path)
    try:
        conn.execute(
            "INSERT INTO evidence (span_id, doc_id, quote, char_start, char_end,"
            " match_mode, created_at) VALUES ('ev_bad', 'doc_1', ?, 0, 3, 'exact',"
            " '2026-10-20T10:00:00Z')",
            (b"\xff\xfe raw",),
        )
        # The pain point is backed by the span it links to, because the
        # database refuses a link to a pain point that no span supports.
        conn.execute(
            "INSERT INTO pain_points (pain_id, span_id, doc_id, accepted,"
            " created_at) VALUES ('pp_bad', 'ev_bad', 'doc_1', 1,"
            " '2026-10-20T10:00:00Z')"
        )
        conn.execute(
            "INSERT INTO evidence_pain_links (pain_id, span_id, created_at) VALUES"
            " ('pp_bad', 'ev_bad', '2026-10-20T10:00:00Z')"
        )
        conn.commit()
        snapshot = _snapshot(conn)
        quote = next(
            ref.quote
            for opportunity in snapshot.opportunities
            for ref in opportunity.evidence
            if ref.span_id == "ev_bad"
        )
        assert "raw" in quote
        assert "�" in quote
    finally:
        conn.close()


def test_a_quote_from_a_deleted_document_carries_the_liveness(
    tmp_path: Path,
) -> None:
    # The store knows a document is gone. The vault has to be able to say so,
    # or a reader cannot tell a live quote from a taken-down one.
    conn = _database(tmp_path)
    try:
        conn.execute(
            "INSERT INTO evidence (span_id, doc_id, quote, char_start, char_end,"
            " match_mode, live_status, created_at) VALUES ('ev_gone', 'doc_1', ?,"
            " 0, 3, 'exact', 'deleted', '2026-10-20T10:00:00Z')",
            ("a dead quote",),
        )
        conn.execute(
            "INSERT INTO pain_points (pain_id, span_id, doc_id, accepted,"
            " created_at) VALUES ('pp_gone', 'ev_gone', 'doc_1', 1,"
            " '2026-10-20T10:00:00Z')"
        )
        conn.execute(
            "INSERT INTO evidence_pain_links (pain_id, span_id, created_at) VALUES"
            " ('pp_gone', 'ev_gone', '2026-10-20T10:00:00Z')"
        )
        conn.commit()
        snapshot = _snapshot(conn)
        ref = next(
            ref
            for opportunity in snapshot.opportunities
            for ref in opportunity.evidence
            if ref.span_id == "ev_gone"
        )
        assert ref.live_status == "deleted"
        live = next(
            ref
            for opportunity in snapshot.opportunities
            for ref in opportunity.evidence
            if ref.span_id == "ev_1"
        )
        assert live.live_status == "live"
    finally:
        conn.close()


def test_a_quote_with_a_control_character_is_read(tmp_path: Path) -> None:
    conn = _database(tmp_path)
    try:
        conn.execute(
            "INSERT INTO evidence (span_id, doc_id, quote, char_start, char_end,"
            " match_mode, created_at) VALUES ('ev_ctrl', 'doc_1', ?, 0, 3, 'exact',"
            " '2026-10-20T10:00:00Z')",
            ("a\x00b",),
        )
        # The pain point is backed by the span it links to, because the
        # database refuses a link to a pain point that no span supports.
        conn.execute(
            "INSERT INTO pain_points (pain_id, span_id, doc_id, accepted,"
            " created_at) VALUES ('pp_ctrl', 'ev_ctrl', 'doc_1', 1,"
            " '2026-10-20T10:00:00Z')"
        )
        conn.execute(
            "INSERT INTO evidence_pain_links (pain_id, span_id, created_at) VALUES"
            " ('pp_ctrl', 'ev_ctrl', '2026-10-20T10:00:00Z')"
        )
        conn.commit()
        snapshot = _snapshot(conn)
        quote = next(
            ref.quote
            for opportunity in snapshot.opportunities
            for ref in opportunity.evidence
            if ref.span_id == "ev_ctrl"
        )
        assert quote == "a\x00b"
    finally:
        conn.close()

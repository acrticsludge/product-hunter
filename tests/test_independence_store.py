"""The persistence cases: the migration, the round trip, and the conflict rules."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from product_hunter.db import (
    MIGRATIONS_DIR,
    IndependenceConflict,
    IndependenceError,
    IndependenceStore,
    connect,
    current_version,
    migrate,
)
from product_hunter.independence import (
    AuthorObservation,
    ConcentrationVerdict,
    IdentitySignal,
    IndependenceMetrics,
    analyze,
    build_author_clusters,
    build_graph,
    detect_propagation,
    hash_identity_key,
    load_independence_policy,
    resolve_authors,
    resolve_communities,
)

DETECTED_AT = "2026-10-21T00:00:00+00:00"
CREATED_AT = "2026-10-21T01:00:00+00:00"
POST = "the invoice export takes three hours every month and nobody knows why"
OTHER = "a different complaint about payroll exports and the totals each month"


def _seed(store: IndependenceStore, root: Path) -> None:
    """Fill the authoritative tables with two documents, three accounts, one community."""
    conn = store.conn
    for source_id in ("hn_algolia", "github_issues"):
        conn.execute(
            "INSERT INTO sources (source_id, source_type, terms_status) "
            "VALUES (?, 'community', 'permitted')",
            (source_id,),
        )
    conn.execute(
        "INSERT INTO communities (community_id, source_id, name) "
        "VALUES ('co_1', 'hn_algolia', 'Hacker News')"
    )
    # The same community name on another platform, so the resolver has a pair
    # to compare.
    conn.execute(
        "INSERT INTO communities (community_id, source_id, name) "
        "VALUES ('co_2', 'github_issues', 'Hacker News')"
    )
    texts = {"doc_1": POST, "doc_2": POST, "doc_3": OTHER}
    for index in (1, 2, 3):
        doc_id = f"doc_{index}"
        text_dir = root / "text"
        text_dir.mkdir(parents=True, exist_ok=True)
        (text_dir / f"{doc_id}.txt").write_text(texts[doc_id], encoding="utf-8")
        conn.execute(
            "INSERT INTO fetches (fetch_id, source_id, requested_url) "
            "VALUES (?, 'hn_algolia', 'https://example.com')",
            (f"fetch_{index}",),
        )
        conn.execute(
            """
            INSERT INTO documents (
                doc_id, fetch_id, source_id, community_id, url, text_sha256,
                text_path, raw_path, raw_sha256, parser_version
            ) VALUES (?, ?, 'hn_algolia', 'co_1', ?, ?, ?, 'data/raw/hn/a.json', ?, 'phase1-0.3')
            """,
            (
                doc_id,
                f"fetch_{index}",
                f"https://example.com/{doc_id}",
                doc_id * 8,
                f"text/{doc_id}.txt",
                doc_id * 8,
            ),
        )
        conn.execute(
            "INSERT INTO authors (author_id, source_id, handle, identity_key) "
            "VALUES (?, 'hn_algolia', ?, ?)",
            (
                f"au_{index}",
                f"handle_{index}",
                # The first two accounts carry the same identity key, so the
                # resolver sees one person behind them.
                hash_identity_key("raw-key-shared" if index < 3 else "raw-key-3"),
            ),
        )
        conn.execute(
            "INSERT INTO author_links (link_id, author_id, doc_id, confidence, basis) "
            "VALUES (?, ?, ?, 1.0, 'declared')",
            (f"al_{index}", f"au_{index}", doc_id),
        )
        conn.execute(
            "INSERT INTO evidence (span_id, doc_id, quote) VALUES (?, ?, 'a quote')",
            (f"span_{index}", doc_id),
        )


def _store(tmp_path: Path) -> IndependenceStore:
    return IndependenceStore.open(tmp_path / "product_hunter.db", root=tmp_path)


def test_the_migration_applies_to_a_fresh_database() -> None:
    conn = connect(":memory:")
    try:
        assert migrate(conn) == len(list(MIGRATIONS_DIR.glob("*.sql")))
        assert current_version(conn) == len(list(MIGRATIONS_DIR.glob("*.sql")))
        tables = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        assert {
            "author_identity_links",
            "community_links",
            "author_clusters",
            "author_cluster_members",
            "independence_reports",
        } <= tables
        author_columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(authors)").fetchall()
        }
        assert {
            "identity_key",
            "identity_confidence",
            "identity_basis",
            "first_seen_at",
            "last_seen_at",
            "deletion_status",
            "deleted_at",
            "cluster_id",
        } <= author_columns
        document_columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(documents)").fetchall()
        }
        assert {"live_status", "deleted_at"} <= document_columns
        edge_columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(propagation_edges)").fetchall()
        }
        assert {
            "detection_method",
            "provenance_json",
            "from_span_id",
            "to_span_id",
            "source_id",
            "detected_at",
        } <= edge_columns
    finally:
        conn.close()


def test_the_migration_applies_to_a_version_two_database(tmp_path: Path) -> None:
    old = tmp_path / "migrations"
    old.mkdir()
    for name in ("0001_initial.sql", "0002_model_routing.sql"):
        shutil.copy(MIGRATIONS_DIR / name, old / name)
    conn = connect(":memory:")
    try:
        assert migrate(conn, migrations_dir=old) == 2
        assert migrate(conn) == len(list(MIGRATIONS_DIR.glob("*.sql")))
        assert current_version(conn) == len(list(MIGRATIONS_DIR.glob("*.sql")))
    finally:
        conn.close()


def test_the_store_reads_the_authoritative_state(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        _seed(store, tmp_path)

        authors = store.load_authors()
        assert [author.author_id for author in authors] == ["au_1", "au_2", "au_3"]
        assert authors[0].doc_ids == ["doc_1"]
        assert authors[0].community_ids == ["co_1"]
        assert authors[0].identity_key == hash_identity_key("raw-key-shared")

        communities = store.load_communities()
        assert [community.community_id for community in communities] == ["co_1", "co_2"]

        mentions = store.load_mentions()
        assert [mention.doc_id for mention in mentions] == ["doc_1", "doc_2", "doc_3"]
        assert mentions[0].text == POST
        assert mentions[0].author_id == "au_1"
        assert mentions[0].span_ids == ["span_1"]
        assert mentions[0].live_status == "live"

        assert [mention.doc_id for mention in store.load_mentions(["doc_3"])] == ["doc_3"]
        assert store.load_mentions([]) == []
    finally:
        store.close()


def test_the_relationships_and_the_report_survive_a_round_trip(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        _seed(store, tmp_path)
        policy = load_independence_policy()
        authors = store.load_authors()
        communities = store.load_communities()
        mentions = store.load_mentions()

        links = resolve_authors(authors, mentions, policy, detected_at=DETECTED_AT)
        community_links = resolve_communities(communities, policy, detected_at=DETECTED_AT)
        edges = detect_propagation(mentions, policy, detected_at=DETECTED_AT)
        graph = build_graph(
            mentions, authors, communities, links, community_links, edges, policy
        )
        verdict = analyze(graph, policy)
        clusters = graph.author_clusters

        assert store.record_identity_link(links[0]) is True
        assert store.record_community_link(community_links[0]) is True
        assert store.record_propagation_edge(edges[0]) is True
        assert store.record_author_clusters(clusters, created_at=CREATED_AT) == len(clusters)
        assert store.record_report(verdict, scope_id="scope-1", created_at=CREATED_AT) is True

        row = store.conn.execute(
            "SELECT relation, detection_method, confidence, signals_json, conflict, "
            "provenance_json, detected_at FROM author_identity_links"
        ).fetchone()
        assert row["relation"] == links[0].relation
        assert row["detection_method"] == links[0].detection_method
        assert row["confidence"] == links[0].confidence
        assert row["detected_at"] == DETECTED_AT
        assert row["provenance_json"]
        assert row["signals_json"]

        edge_row = store.conn.execute(
            "SELECT relation, detection_method, confidence, provenance_json, detected_at, "
            "from_span_id, to_span_id FROM propagation_edges"
        ).fetchone()
        assert edge_row["detection_method"] == edges[0].detection_method
        assert edge_row["detected_at"] == DETECTED_AT
        assert edge_row["provenance_json"]
        assert edge_row["from_span_id"] == "span_1"
        assert edge_row["to_span_id"] == "span_2"

        report = store.conn.execute(
            "SELECT policy_version, metrics_json, violations_json, satisfied "
            "FROM independence_reports"
        ).fetchone()
        assert report["policy_version"] == policy.version
        assert report["metrics_json"]
        assert report["satisfied"] == int(verdict.satisfied)

        # A second write of the same content changes nothing.
        assert store.record_identity_link(links[0]) is False
        assert store.record_community_link(community_links[0]) is False
        assert store.record_propagation_edge(edges[0]) is False
        assert store.record_author_clusters(clusters, created_at=CREATED_AT) == 0
        assert (
            store.record_report(verdict, scope_id="scope-1", created_at=CREATED_AT) is False
        )
        assert store.conn.execute("SELECT count(*) FROM author_identity_links").fetchone()[0] == 1
        assert store.conn.execute("SELECT count(*) FROM propagation_edges").fetchone()[0] == 1
        assert store.conn.execute("SELECT count(*) FROM independence_reports").fetchone()[0] == 1
    finally:
        store.close()


def test_a_changed_record_under_the_same_id_is_refused(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        _seed(store, tmp_path)
        policy = load_independence_policy()
        mentions = store.load_mentions()
        links = resolve_authors(
            store.load_authors(), mentions, policy, detected_at=DETECTED_AT
        )
        assert store.record_identity_link(links[0]) is True

        changed = links[0].model_copy(update={"confidence": 0.1})
        with pytest.raises(IndependenceConflict):
            store.record_identity_link(changed)
    finally:
        store.close()


def test_the_identity_key_is_hashed_before_it_is_written(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        _seed(store, tmp_path)
        from product_hunter.independence import AuthorObservation

        author = AuthorObservation(
            author_id="au_keyed",
            source_id="hn_algolia",
            handle="keyholder",
            identity_key="raw-identity-key",
            first_seen_at=CREATED_AT,
            last_seen_at=CREATED_AT,
        )
        assert store.record_author(author) is True
        assert store.record_author(author) is False

        stored = store.conn.execute(
            "SELECT identity_key FROM authors WHERE author_id = 'au_keyed'"
        ).fetchone()["identity_key"]
        assert stored != "raw-identity-key"
        assert stored == hash_identity_key("raw-identity-key")
    finally:
        store.close()


def test_record_author_fills_the_key_of_a_collector_created_row(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        conn = store.conn
        conn.execute(
            "INSERT INTO sources (source_id, source_type, terms_status) "
            "VALUES ('hn_algolia', 'community', 'permitted')"
        )
        # The Phase 1 store creates the row first, with no identity key.
        conn.execute(
            "INSERT INTO authors (author_id, source_id, handle, first_seen_at, last_seen_at) "
            "VALUES ('au_1', 'hn_algolia', 'jane', '2026-01-02', '2026-01-03')"
        )
        author = AuthorObservation(
            author_id="au_1",
            source_id="hn_algolia",
            handle="jane",
            identity_key="raw-key",
            first_seen_at="2026-01-01",
            last_seen_at="2026-01-04",
            deletion_status="deleted",
        )
        assert store.record_author(author) is False

        row = conn.execute(
            "SELECT identity_key, first_seen_at, last_seen_at, deletion_status "
            "FROM authors WHERE author_id = 'au_1'"
        ).fetchone()
        assert row["identity_key"] == hash_identity_key("raw-key")
        assert row["first_seen_at"] == "2026-01-01"
        assert row["last_seen_at"] == "2026-01-04"
        assert row["deletion_status"] == "deleted"

        # Deleted is final: a later active record does not resurrect the row.
        assert (
            store.record_author(author.model_copy(update={"deletion_status": "active"}))
            is False
        )
        assert (
            conn.execute(
                "SELECT deletion_status FROM authors WHERE author_id = 'au_1'"
            ).fetchone()[0]
            == "deleted"
        )
    finally:
        store.close()


def test_a_second_identity_key_for_the_same_author_is_refused(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        conn = store.conn
        conn.execute(
            "INSERT INTO sources (source_id, source_type, terms_status) "
            "VALUES ('hn_algolia', 'community', 'permitted')"
        )
        author = AuthorObservation(
            author_id="au_1",
            source_id="hn_algolia",
            handle="jane",
            identity_key="raw-key",
        )
        assert store.record_author(author) is True
        with pytest.raises(IndependenceConflict):
            store.record_author(author.model_copy(update={"identity_key": "other-key"}))
    finally:
        store.close()


def test_a_text_path_that_leaves_the_root_reads_nothing(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    store = IndependenceStore.open(tmp_path / "product_hunter.db", root=root)
    try:
        (tmp_path / "outside.txt").write_text("a file outside the root", encoding="utf-8")
        conn = store.conn
        conn.execute(
            "INSERT INTO sources (source_id, source_type, terms_status) "
            "VALUES ('hn_algolia', 'community', 'permitted')"
        )
        conn.execute(
            "INSERT INTO fetches (fetch_id, source_id, requested_url) "
            "VALUES ('fetch_1', 'hn_algolia', 'https://example.com')"
        )
        conn.execute(
            """
            INSERT INTO documents (
                doc_id, fetch_id, source_id, url, text_sha256, text_path,
                raw_path, raw_sha256, parser_version
            ) VALUES ('doc_1', 'fetch_1', 'hn_algolia', 'https://example.com/doc_1',
                      'abc', '../outside.txt', 'data/raw/a.json', 'abc', 'phase1-0.3')
            """
        )

        mentions = store.load_mentions()
        assert [mention.doc_id for mention in mentions] == ["doc_1"]
        assert mentions[0].text == ""
    finally:
        store.close()


def test_the_seen_window_is_ordered_by_instant_not_by_text(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        conn = store.conn
        conn.execute(
            "INSERT INTO sources (source_id, source_type, terms_status) "
            "VALUES ('hn_algolia', 'community', 'permitted')"
        )
        conn.execute(
            "INSERT INTO authors (author_id, source_id, handle, first_seen_at, last_seen_at) "
            "VALUES ('au_1', 'hn_algolia', 'jane', "
            "'2026-10-21T10:00:00Z', '2026-10-21T10:00:00Z')"
        )
        # A millisecond value sorts before a plain one by codepoint, because
        # '.' sorts before 'Z'. The window is ordered by instant.
        author = AuthorObservation(
            author_id="au_1",
            source_id="hn_algolia",
            handle="jane",
            first_seen_at="2026-10-21T10:00:00.500Z",
            last_seen_at="2026-10-21T10:00:00.500Z",
        )
        assert store.record_author(author) is False

        row = conn.execute(
            "SELECT first_seen_at, last_seen_at FROM authors WHERE author_id = 'au_1'"
        ).fetchone()
        assert row["first_seen_at"] == "2026-10-21T10:00:00Z"
        assert row["last_seen_at"] == "2026-10-21T10:00:00.500Z"
    finally:
        store.close()


def test_each_member_is_written_with_its_own_confidence(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        _seed(store, tmp_path)
        policy = load_independence_policy()
        authors = store.load_authors()
        links = resolve_authors(
            authors, store.load_mentions(), policy, detected_at=DETECTED_AT
        )
        clusters = build_author_clusters(authors, links, policy)
        merged = next(
            cluster
            for cluster in clusters
            if {"au_1", "au_2"} <= set(cluster.member_author_ids)
        )
        # The merge edge outranks every member, so writing the cluster's value
        # for each member would fail the per-member check below.
        assert merged.confidence > max(merged.member_confidences.values())

        store.record_author_clusters(clusters, created_at=CREATED_AT)
        for author_id, confidence in merged.member_confidences.items():
            stored = store.conn.execute(
                "SELECT identity_confidence FROM authors WHERE author_id = ?",
                (author_id,),
            ).fetchone()[0]
            assert stored == confidence
    finally:
        store.close()


def test_a_link_that_names_a_missing_author_is_refused(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        _seed(store, tmp_path)
        policy = load_independence_policy()
        links = resolve_authors(
            store.load_authors(),
            store.load_mentions(),
            policy,
            detected_at=DETECTED_AT,
        )
        dangling = links[0].model_copy(update={"related_author_id": "au_ghost"})
        with pytest.raises(IndependenceError, match="au_ghost"):
            store.record_identity_link(dangling)
    finally:
        store.close()


def test_a_link_that_names_a_missing_community_is_refused(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        _seed(store, tmp_path)
        policy = load_independence_policy()
        links = resolve_communities(
            store.load_communities(), policy, detected_at=DETECTED_AT
        )
        dangling = links[0].model_copy(update={"related_community_id": "co_ghost"})
        with pytest.raises(IndependenceError, match="co_ghost"):
            store.record_community_link(dangling)
    finally:
        store.close()


def test_a_changed_relation_is_recorded_beside_the_original(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        _seed(store, tmp_path)
        policy = load_independence_policy()
        authors = store.load_authors()
        mentions = store.load_mentions()
        pair = {"au_1", "au_2"}

        merged = next(
            link
            for link in resolve_authors(
                authors, mentions, policy, detected_at=DETECTED_AT
            )
            if {link.author_id, link.related_author_id} == pair
        )
        assert merged.relation == "same-person-merged"

        disavow = IdentitySignal(
            kind="explicit_disavow", author_id="au_1", related_author_id="au_2"
        )
        suspect = next(
            link
            for link in resolve_authors(
                authors,
                mentions,
                policy,
                detected_at=DETECTED_AT,
                signals=[disavow],
            )
            if {link.author_id, link.related_author_id} == pair
        )
        assert suspect.relation == "same-person-suspect"
        assert suspect.link_id != merged.link_id

        assert store.record_identity_link(merged) is True
        assert store.record_identity_link(suspect) is True
        assert (
            store.conn.execute("SELECT count(*) FROM author_identity_links").fetchone()[0]
            == 2
        )
    finally:
        store.close()


def test_a_changed_membership_clears_the_author_left_behind(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        _seed(store, tmp_path)
        policy = load_independence_policy()
        authors = store.load_authors()
        links = resolve_authors(
            authors, store.load_mentions(), policy, detected_at=DETECTED_AT
        )
        store.record_author_clusters(
            build_author_clusters(authors, links, policy), created_at=CREATED_AT
        )
        assert (
            store.conn.execute(
                "SELECT cluster_id FROM authors WHERE author_id = 'au_3'"
            ).fetchone()[0]
            is not None
        )

        only_one = build_author_clusters([authors[0]], [], policy)
        store.record_author_clusters(only_one, created_at=CREATED_AT)

        assert (
            store.conn.execute(
                "SELECT cluster_id FROM authors WHERE author_id = 'au_1'"
            ).fetchone()[0]
            == only_one[0].cluster_id
        )
        assert (
            store.conn.execute(
                "SELECT cluster_id FROM authors WHERE author_id = 'au_3'"
            ).fetchone()[0]
            is None
        )
    finally:
        store.close()


def test_a_re_grouping_replaces_the_old_cluster_rows(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        _seed(store, tmp_path)
        policy = load_independence_policy()
        authors = store.load_authors()
        links = resolve_authors(
            authors, store.load_mentions(), policy, detected_at=DETECTED_AT
        )
        first = build_author_clusters(authors, links, policy)
        old = next(
            cluster
            for cluster in first
            if {"au_1", "au_2"} <= set(cluster.member_author_ids)
        )
        store.record_author_clusters(first, created_at=CREATED_AT)
        assert (
            store.conn.execute(
                "SELECT count(*) FROM author_cluster_members WHERE cluster_id = ?",
                (old.cluster_id,),
            ).fetchone()[0]
            == len(old.member_author_ids)
        )

        # The same accounts, grouped differently: every account on its own.
        regrouped = build_author_clusters(authors, [], policy)
        store.record_author_clusters(regrouped, created_at=CREATED_AT)

        # The old group and its member rows are gone.
        assert (
            store.conn.execute(
                "SELECT count(*) FROM author_clusters WHERE cluster_id = ?",
                (old.cluster_id,),
            ).fetchone()[0]
            == 0
        )
        assert (
            store.conn.execute(
                "SELECT count(*) FROM author_cluster_members WHERE cluster_id = ?",
                (old.cluster_id,),
            ).fetchone()[0]
            == 0
        )
        # The new groups are there, and the account points at its own.
        for cluster in regrouped:
            assert (
                store.conn.execute(
                    "SELECT count(*) FROM author_clusters WHERE cluster_id = ?",
                    (cluster.cluster_id,),
                ).fetchone()[0]
                == 1
            )
        alone = next(
            cluster for cluster in regrouped if cluster.member_author_ids == ["au_1"]
        )
        assert (
            store.conn.execute(
                "SELECT cluster_id FROM authors WHERE author_id = 'au_1'"
            ).fetchone()[0]
            == alone.cluster_id
        )

        # Writing the same groups again changes nothing.
        assert store.record_author_clusters(regrouped, created_at=CREATED_AT) == 0
    finally:
        store.close()


def test_the_store_refuses_metrics_that_no_graph_produced(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        _seed(store, tmp_path)
        policy = load_independence_policy()
        hand_built = IndependenceMetrics(
            policy_version=policy.version,
            raw_mentions=3,
            canonical_content_count=1,
            independent_author_count=99,
            uncertain_author_count=0,
            independent_community_count=1,
            propagation_adjusted_evidence=1,
            source_concentration=0.34,
            author_cluster_concentration=0.34,
            community_concentration=0.34,
            propagation_chain_concentration=1.0,
            propagation_chain_count=1,
            unattributed_mentions=0,
            deleted_source_mentions=0,
            conflicting_identity_pairs=0,
            suspect_identity_groups=0,
            uncertain_identity_groups=0,
        )
        verdict = ConcentrationVerdict(
            satisfied=True, violations=[], metrics=hand_built
        )

        with pytest.raises(IndependenceError, match="not built by analyze"):
            store.record_report(verdict, scope_id="scope-1", created_at=CREATED_AT)
        assert (
            store.conn.execute("SELECT count(*) FROM independence_reports").fetchone()[0]
            == 0
        )

        # A verdict the analysis built is accepted.
        authors = store.load_authors()
        communities = store.load_communities()
        mentions = store.load_mentions()
        links = resolve_authors(authors, mentions, policy, detected_at=DETECTED_AT)
        community_links = resolve_communities(
            communities, policy, detected_at=DETECTED_AT
        )
        edges = detect_propagation(mentions, policy, detected_at=DETECTED_AT)
        graph = build_graph(
            mentions, authors, communities, links, community_links, edges, policy
        )
        analysed = analyze(graph, policy)
        assert store.record_report(analysed, scope_id="scope-1", created_at=CREATED_AT)
    finally:
        store.close()


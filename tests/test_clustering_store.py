"""The clustering store: the rows, the history and the refusals."""

from __future__ import annotations

import sqlite3

import pytest

from product_hunter.clustering.clusters import (
    Assignment,
    Cluster,
    ClusterDecision,
    ClusterProfile,
    ClusterStatus,
    DecisionKind,
    HeldPainPoint,
    HeldReason,
)
from product_hunter.clustering.engine import ClusteringResult
from product_hunter.clustering.similarity import (
    MixedVectorSpaceError,
    VectorSpace,
)
from product_hunter.db import MIGRATIONS_DIR, connect, current_version, migrate
from product_hunter.db.clustering import (
    ClusterStore,
    ClusterStoreConflict,
    ClusterStoreError,
    ClusteringRun,
)
from product_hunter.normalization import Domain

CLUSTER_TABLES = {
    "clustering_runs",
    "clusters",
    "cluster_versions",
    "cluster_members",
    "cluster_lineage",
    "cluster_decisions",
}


def _space(model: str = "a-model", version: str = "1") -> VectorSpace:
    return VectorSpace.of(
        provider="local", model=model, model_version=version, dimensions=3
    )


def _run(run_id: str = "run-1", space: VectorSpace | None = None, **overrides):
    space = space or _space()
    data = {
        "run_id": run_id,
        "algorithm_version": "cluster-v1",
        "config_version": "clustering-v1-test",
        "prompt_version": "cluster-prompt-v1",
        "space_id": space.space_id,
        "provider": "test-provider",
        "model": "test-model",
        "model_version": "test-model-v1",
        "embedding_provider": "local",
        "embedding_model": "a-model",
        "embedding_model_version": "1",
        "params": {"merge_threshold": 0.86},
    }
    data.update(overrides)
    return ClusteringRun(**data)


def _profile() -> ClusterProfile:
    return ClusterProfile(
        domain=Domain.finance,
        actor="the bookkeeping team",
        action="paste invoice totals by hand",
        object="invoice totals",
        consequence="a Saturday of manual work every month",
    )


def _result(
    clusters: dict[str, list[str]],
    space: VectorSpace | None = None,
    held: list[str] | None = None,
    decisions: list[ClusterDecision] | None = None,
) -> ClusteringResult:
    """A result built by hand, so the store is tested on its own."""
    space = space or _space()
    records: list[Cluster] = []
    assignments: list[Assignment] = []
    for cluster_id, members in clusters.items():
        records.append(
            Cluster(
                cluster_id=cluster_id,
                version=1,
                status=ClusterStatus.active,
                label="the bookkeeping team pastes invoice totals by hand",
                profile=_profile(),
                anchor_pain_id=min(members),
                member_ids=sorted(members),
            )
        )
        for pain_id in members:
            assignments.append(
                Assignment(
                    cluster_id=cluster_id,
                    pain_id=pain_id,
                    basis="semantic",
                    confidence=0.9,
                )
            )
    return ClusteringResult(
        space=space,
        algorithm_version="cluster-v1",
        config_version="clustering-v1-test",
        prompt_version="cluster-prompt-v1",
        clusters=records,
        assignments=assignments,
        decisions=list(decisions or []),
        held=[
            HeldPainPoint(
                pain_id=pain_id,
                reason=HeldReason.ambiguous_unresolved,
                detail="the adjudicator could not tell the two apart",
            )
            for pain_id in (held or [])
        ],
    )


def _decision(
    decision_id: str = "dec-1",
    *,
    kind: DecisionKind = DecisionKind.adjudication,
    relation: str = "same_problem",
    pain_a: str = "pp-1",
    pain_b: str | None = "pp-2",
    decision: str = "same",
    score: float | None = 0.8,
    confidence: float | None = 0.75,
    basis: str = "adjudicated_same",
    cluster_id: str | None = None,
) -> ClusterDecision:
    """One decision row, with the provenance of the call behind it."""
    return ClusterDecision(
        decision_id=decision_id,
        kind=kind,
        relation=relation,
        pain_a=pain_a,
        pain_b=pain_b,
        cluster_id=cluster_id,
        decision=decision,
        score=score,
        confidence=confidence,
        basis=basis,
        provider="test-provider",
        model="test-model",
        model_version="test-model-v1",
        prompt_version="cluster-prompt-v1",
    )


def _store() -> tuple[sqlite3.Connection, ClusterStore]:
    conn = connect(":memory:")
    migrate(conn)
    return conn, ClusterStore(conn)


def _insert_pain_point(
    conn: sqlite3.Connection,
    pain_id: str,
    doc_id: str = "doc-1",
    quote: str = "we paste the invoice totals by hand",
) -> None:
    """One pain point with its document, its span and its link."""
    conn.execute(
        "INSERT OR IGNORE INTO sources (source_id, source_type, terms_status) "
        "VALUES ('src-1', 'community', 'permitted')"
    )
    conn.execute(
        "INSERT OR IGNORE INTO fetches (fetch_id, source_id, requested_url) "
        "VALUES ('fetch-1', 'src-1', 'https://example.com/post')"
    )
    conn.execute(
        "INSERT OR IGNORE INTO documents (doc_id, fetch_id, source_id, url, "
        "text_sha256, text_path, raw_path, raw_sha256, parser_version) "
        "VALUES (?, 'fetch-1', 'src-1', 'https://example.com/post', ?, 't', 'r', "
        "'s', 'phase1-0.3')",
        (doc_id, "a" * 64),
    )
    conn.execute(
        "INSERT OR IGNORE INTO evidence (span_id, doc_id, quote, char_start, "
        "char_end) VALUES (?, ?, ?, 0, 10)",
        (f"sp-{pain_id}", doc_id, quote),
    )
    conn.execute(
        "INSERT OR IGNORE INTO pain_points (pain_id, span_id, doc_id, domain, "
        "actor, action, object, consequence, severity, frequency, "
        "pain_statement, normalized_key, normalization_version, accepted) "
        "VALUES (?, ?, ?, 'finance', 'the bookkeeping team', "
        "'paste invoice totals by hand', 'invoice totals', "
        "'a Saturday of manual work every month', 3, 0, "
        "'the bookkeeping team pastes invoice totals by hand', ?, 'norm-v1', 1)",
        (pain_id, f"sp-{pain_id}", doc_id, f"finance:paste:{pain_id}"),
    )
    conn.execute(
        "INSERT OR IGNORE INTO evidence_pain_links (pain_id, span_id) "
        "VALUES (?, ?)",
        (pain_id, f"sp-{pain_id}"),
    )


def _seed(conn: sqlite3.Connection, result: ClusteringResult) -> None:
    """Seed the pain points a result names.

    A membership names a pain point the store holds: the database refuses a
    membership for a pain point it does not know. The fixture seeds the pain
    points of the result, so the tests exercise the store and not the
    absence of a row.
    """
    for cluster in result.clusters:
        for pain_id in cluster.member_ids:
            _insert_pain_point(conn, pain_id)


def _apply(
    conn: sqlite3.Connection,
    store: ClusterStore,
    result: ClusteringResult,
    run,
):
    """Seed the pain points of a result and apply it."""
    _seed(conn, result)
    return store.apply_result(result, run)


# --- the migration ---------------------------------------------------------


def test_the_migration_creates_the_clustering_tables() -> None:
    conn = connect(":memory:")
    try:
        migrate(conn)
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' "
            "AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
        assert CLUSTER_TABLES <= {row[0] for row in rows}
    finally:
        conn.close()


def test_the_shipped_migrations_are_contiguous_and_the_new_one_is_applied() -> None:
    files = sorted(MIGRATIONS_DIR.glob("*.sql"))
    numbers = [int(path.name.split("_")[0]) for path in files]
    assert numbers == list(range(1, len(numbers) + 1))
    assert "0006_clustering.sql" in {path.name for path in files}
    conn = connect(":memory:")
    try:
        assert migrate(conn) == len(numbers)
        assert current_version(conn) == len(numbers)
    finally:
        conn.close()


# --- the round trip --------------------------------------------------------


def test_the_result_is_written_and_read_back() -> None:
    conn, store = _store()
    try:
        result = _result({"cl-1": ["pp-1", "pp-2"]})
        summary = _apply(conn, store, result, _run())
        assert summary.clusters_written == 1
        assert summary.memberships_opened == 2
        assert summary.versions_written == 1
        cluster = store.cluster("cl-1")
        assert cluster is not None
        assert cluster.member_ids == ["pp-1", "pp-2"]
        assert cluster.version == 1
        assert cluster.status is ClusterStatus.active
        assert [version.version for version in store.cluster_versions("cl-1")] == [1]
        assert {membership.pain_id for membership in store.current_memberships()} == {
            "pp-1",
            "pp-2",
        }
    finally:
        conn.close()


def test_the_run_row_carries_the_versions_and_the_models() -> None:
    conn, store = _store()
    try:
        store.record_run(_run("run-1"))
        row = store.run("run-1")
        assert row is not None
        assert row.provider == "test-provider"
        assert row.model == "test-model"
        assert row.model_version == "test-model-v1"
        assert row.embedding_model == "a-model"
        assert row.space_id == _space().space_id
    finally:
        conn.close()


def test_a_run_that_disagrees_with_the_stored_row_is_a_conflict() -> None:
    conn, store = _store()
    try:
        store.record_run(_run("run-1"))
        with pytest.raises(ClusterStoreConflict):
            store.record_run(_run("run-1", model="another-model"))
    finally:
        conn.close()


def test_applying_the_same_result_twice_changes_nothing() -> None:
    conn, store = _store()
    try:
        result = _result({"cl-1": ["pp-1", "pp-2"]})
        _apply(conn, store, result, _run("run-1"))
        before = _counts(conn)
        summary = _apply(conn, store, result, _run("run-1"))
        assert summary.clusters_written == 0
        assert summary.clusters_unchanged == 1
        assert summary.versions_written == 0
        assert summary.memberships_opened == 0
        assert _counts(conn) == before
    finally:
        conn.close()


def test_a_rerun_keeps_the_cluster_id_and_the_version() -> None:
    conn, store = _store()
    try:
        result = _result({"cl-1": ["pp-1", "pp-2"]})
        _apply(conn, store, result, _run("run-1"))
        _apply(conn, store, result, _run("run-2"))
        cluster = store.cluster("cl-1")
        assert cluster is not None
        assert cluster.version == 1
        assert [version.version for version in store.cluster_versions("cl-1")] == [1]
    finally:
        conn.close()


def test_a_member_that_leaves_bumps_the_version() -> None:
    conn, store = _store()
    try:
        _apply(conn, store, _result({"cl-1": ["pp-1", "pp-2"]}), _run("run-1"))
        first = store.cluster_versions("cl-1")[0]
        _apply(conn, store, _result({"cl-1": ["pp-1"]}), _run("run-2"))
        cluster = store.cluster("cl-1")
        assert cluster is not None
        assert cluster.version == 2
        assert cluster.member_ids == ["pp-1"]
        versions = store.cluster_versions("cl-1")
        assert [version.version for version in versions] == [1, 2]
        assert versions[0].member_ids == first.member_ids
        assert versions[1].member_ids == ["pp-1"]
    finally:
        conn.close()


def test_a_pain_point_has_one_open_membership_and_no_overlapping_intervals() -> None:
    conn, store = _store()
    try:
        _apply(conn, store, _result({"cl-1": ["pp-1"]}), _run("run-1"))
        _apply(conn, store, _result({"cl-1": ["pp-2"]}), _run("run-2"))
        _apply(conn, store, _result({"cl-1": ["pp-1", "pp-2"]}), _run("run-3"))
        memberships = store.current_memberships()
        assert sorted(membership.pain_id for membership in memberships) == [
            "pp-1",
            "pp-2",
        ]
        rows = conn.execute(
            "SELECT valid_from, valid_to FROM cluster_members WHERE pain_id = ? "
            "ORDER BY valid_from",
            ("pp-1",),
        ).fetchall()
        assert len(rows) == 2
        closed, opened = rows
        assert closed["valid_to"] is not None
        assert opened["valid_to"] is None
        assert closed["valid_to"] <= opened["valid_from"]
    finally:
        conn.close()


def test_a_result_that_puts_a_pain_point_in_two_clusters_is_refused() -> None:
    conn, store = _store()
    try:
        result = _result({"cl-1": ["pp-1"], "cl-2": ["pp-1"]})
        with pytest.raises(ClusterStoreError):
            _apply(conn, store, result, _run())
    finally:
        conn.close()


def test_a_result_from_another_space_is_refused() -> None:
    conn, store = _store()
    try:
        _apply(conn, store, _result({"cl-1": ["pp-1"]}), _run("run-1"))
        other = _space("another-model")
        with pytest.raises(MixedVectorSpaceError) as excinfo:
            _apply(
                conn,
                store,
                _result({"cl-2": ["pp-2"]}, space=other),
                _run("run-2", space=other),
            )
        assert _space().space_id in str(excinfo.value)
        assert other.space_id in str(excinfo.value)
    finally:
        conn.close()


def test_a_run_of_another_space_is_refused() -> None:
    conn, store = _store()
    try:
        other = _space("another-model")
        # The run names one space and the result was built in another.
        with pytest.raises(MixedVectorSpaceError):
            _apply(
                conn,
                store,
                _result({"cl-1": ["pp-1"]}, space=other),
                _run("run-1"),
            )
    finally:
        conn.close()


def test_the_model_change_is_recorded_and_the_membership_does_not_move() -> None:
    conn, store = _store()
    try:
        result = _result({"cl-1": ["pp-1", "pp-2"]})
        _apply(conn, store, result, _run("run-1"))
        _apply(
            conn,
            store,
            result,
            _run(
                "run-2",
                provider="another-provider",
                model="another-model",
                model_version="another-model-v2",
            ),
        )
        row = store.run("run-2")
        assert row is not None
        assert row.provider == "another-provider"
        assert row.model == "another-model"
        assert row.model_version == "another-model-v2"
        cluster = store.cluster("cl-1")
        assert cluster is not None
        assert cluster.version == 1
    finally:
        conn.close()


def test_a_held_pain_point_is_visible_in_the_store() -> None:
    conn, store = _store()
    try:
        _apply(conn, store, _result({"cl-1": ["pp-1"]}, held=["pp-9"]), _run())
        rows = conn.execute(
            "SELECT * FROM cluster_decisions WHERE kind = 'ambiguous'"
        ).fetchall()
        assert len(rows) == 1
        assert rows[0]["pain_a"] == "pp-9"
        assert rows[0]["basis"] == "ambiguous_unresolved"
    finally:
        conn.close()


# --- the history -----------------------------------------------------------


def test_a_merge_leaves_the_predecessors_alone() -> None:
    conn, store = _store()
    try:
        _apply(
            conn, store, _result({"cl-1": ["pp-1"], "cl-2": ["pp-2"]}), _run("run-1")
        )
        successor = store.merge_clusters("cl-1", "cl-2", run=_run("run-2"))
        assert successor != "cl-1"
        assert successor != "cl-2"
        for predecessor in ("cl-1", "cl-2"):
            cluster = store.cluster(predecessor)
            assert cluster is not None
            assert cluster.status is ClusterStatus.merged
            assert store.current_memberships()
            edges = [
                edge
                for edge in store.lineage(predecessor)
                if edge.relation == "merged_into"
            ]
            assert [edge.to_cluster_id for edge in edges] == [successor]
        merged = store.cluster(successor)
        assert merged is not None
        assert merged.member_ids == ["pp-1", "pp-2"]
        # The predecessors keep their rows and their versions.
        assert store.cluster_versions("cl-1")
        assert store.cluster_versions("cl-2")
        assert conn.execute(
            "SELECT count(*) FROM cluster_members WHERE cluster_id = 'cl-1' "
            "AND valid_to IS NULL"
        ).fetchone()[0] == 0
    finally:
        conn.close()


def test_a_split_leaves_the_parent_alone() -> None:
    conn, store = _store()
    try:
        _apply(conn, store, _result({"cl-1": ["pp-1", "pp-2"]}), _run("run-1"))
        children = store.split_cluster(
            "cl-1", [["pp-1"], ["pp-2"]], run=_run("run-2")
        )
        assert len(children) == 2
        assert children[0] != "cl-1"
        parent = store.cluster("cl-1")
        assert parent is not None
        assert parent.status is ClusterStatus.split
        for child in children:
            child_cluster = store.cluster(child)
            assert child_cluster is not None
            assert child_cluster.member_ids
            edges = [
                edge for edge in store.lineage(child) if edge.relation == "split_from"
            ]
            assert [edge.to_cluster_id for edge in edges] == ["cl-1"]
        assert conn.execute(
            "SELECT count(*) FROM cluster_members WHERE cluster_id = 'cl-1' "
            "AND valid_to IS NULL"
        ).fetchone()[0] == 0
        assert conn.execute(
            "SELECT count(*) FROM cluster_members WHERE valid_to IS NULL"
        ).fetchone()[0] == 2
    finally:
        conn.close()


def test_the_evidence_lineage_walks_to_the_spans() -> None:
    conn, store = _store()
    try:
        _insert_pain_point(conn, "pp-1", doc_id="doc-1")
        _apply(conn, store, _result({"cl-1": ["pp-1"]}), _run())
        rows = store.evidence_lineage("cl-1")
        assert len(rows) == 1
        assert rows[0].pain_id == "pp-1"
        assert rows[0].doc_id == "doc-1"
        assert rows[0].quote == "we paste the invoice totals by hand"
        assert rows[0].valid_to is None
    finally:
        conn.close()


def test_the_store_lists_the_spaces_the_clusters_were_built_in() -> None:
    conn, store = _store()
    try:
        _apply(conn, store, _result({"cl-1": ["pp-1"]}), _run())
        assert store.embedding_spaces() == [_space().space_id]
    finally:
        conn.close()


def test_the_store_reads_the_accepted_pain_points() -> None:
    conn, store = _store()
    try:
        _insert_pain_point(conn, "pp-1")
        points = store.pain_points()
        assert [point.pain_id for point in points] == ["pp-1"]
        assert points[0].domain is Domain.finance
        assert points[0].quote == "we paste the invoice totals by hand"
    finally:
        conn.close()


# --- the constraints of the schema -----------------------------------------


def test_a_second_open_membership_for_one_pain_point_is_refused() -> None:
    conn, store = _store()
    try:
        _apply(conn, store, _result({"cl-1": ["pp-1"]}), _run())
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO cluster_members (
                    member_id, cluster_id, pain_id, valid_from, valid_to,
                    assignment_confidence, basis, run_id
                ) VALUES ('mem-second', 'cl-1', 'pp-1', '2026-10-21T12:00:00',
                          NULL, 0.9, 'semantic', 'run-1')
                """
            )
    finally:
        conn.close()


def test_a_closed_membership_does_not_block_a_new_one() -> None:
    conn, store = _store()
    try:
        _apply(conn, store, _result({"cl-1": ["pp-1"]}), _run("run-1"))
        _apply(conn, store, _result({"cl-1": ["pp-2"]}), _run("run-2"))
        # The first membership is closed, so the pain point may be a member
        # of a cluster again.
        conn.execute(
            """
            INSERT INTO cluster_members (
                member_id, cluster_id, pain_id, valid_from, valid_to,
                assignment_confidence, basis, run_id
            ) VALUES ('mem-again', 'cl-1', 'pp-1', '2026-10-21T12:00:00',
                      NULL, 0.9, 'semantic', 'run-3')
            """
        )
        assert conn.execute(
            "SELECT count(*) FROM cluster_members WHERE pain_id = 'pp-1' "
            "AND valid_to IS NULL"
        ).fetchone()[0] == 1
    finally:
        conn.close()


def test_a_cluster_row_with_a_bad_status_is_refused() -> None:
    conn, store = _store()
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO clusters (
                    cluster_id, version, status, created_at, updated_at
                ) VALUES ('cl-bad', 1, 'retired', '2026-10-21T12:00:00',
                          '2026-10-21T12:00:00')
                """
            )
    finally:
        conn.close()


def test_a_version_row_with_a_bad_status_is_refused() -> None:
    conn, store = _store()
    try:
        _apply(conn, store, _result({"cl-1": ["pp-1"]}), _run())
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO cluster_versions (
                    cluster_id, version, status, created_at
                ) VALUES ('cl-1', 9, 'retired', '2026-10-21T12:00:00')
                """
            )
    finally:
        conn.close()


def test_a_membership_for_a_pain_point_the_store_does_not_hold_is_refused() -> None:
    conn, store = _store()
    try:
        _apply(conn, store, _result({"cl-1": ["pp-1"]}), _run())
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO cluster_members (
                    member_id, cluster_id, pain_id, valid_from, valid_to,
                    assignment_confidence, basis, run_id
                ) VALUES ('mem-missing', 'cl-1', 'pp-unknown',
                          '2026-10-21T12:00:00', NULL, 0.9, 'semantic', 'run-1')
                """
            )
    finally:
        conn.close()


def test_a_membership_for_a_cluster_that_does_not_exist_is_refused() -> None:
    conn, store = _store()
    try:
        _insert_pain_point(conn, "pp-1")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO cluster_members (
                    member_id, cluster_id, pain_id, valid_from, valid_to,
                    assignment_confidence, basis, run_id
                ) VALUES ('mem-no-cluster', 'cl-missing', 'pp-1',
                          '2026-10-21T12:00:00', NULL, 0.9, 'semantic', 'run-1')
                """
            )
    finally:
        conn.close()


def test_a_lineage_edge_for_a_cluster_that_does_not_exist_is_refused() -> None:
    conn, store = _store()
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO cluster_lineage (
                    edge_id, from_cluster_id, to_cluster_id, relation, created_at
                ) VALUES ('edge-missing', 'cl-missing', 'cl-other',
                          'merged_into', '2026-10-21T12:00:00')
                """
            )
    finally:
        conn.close()


def test_a_decision_for_a_run_that_does_not_exist_is_refused() -> None:
    conn, store = _store()
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO cluster_decisions (
                    decision_id, run_id, kind, relation, pain_a, decision,
                    basis, created_at
                ) VALUES ('dec-no-run', 'run-missing', 'adjudication',
                          'same_problem', 'pp-1', 'same', 'adjudicated_same',
                          '2026-10-21T12:00:00')
                """
            )
    finally:
        conn.close()


def test_a_version_row_for_a_cluster_that_does_not_exist_is_refused() -> None:
    conn, store = _store()
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO cluster_versions (
                    cluster_id, version, status, created_at
                ) VALUES ('cl-missing', 1, 'active', '2026-10-21T12:00:00')
                """
            )
    finally:
        conn.close()


# --- the decisions and the held rows ---------------------------------------


def test_a_decision_row_carries_the_confidence_of_the_adjudication() -> None:
    conn, store = _store()
    try:
        result = _result(
            {"cl-1": ["pp-1", "pp-2"]}, decisions=[_decision(confidence=0.75)]
        )
        _apply(conn, store, result, _run())
        row = conn.execute(
            "SELECT * FROM cluster_decisions WHERE pain_a = 'pp-1' "
            "AND pain_b = 'pp-2'"
        ).fetchone()
        assert row["confidence"] == pytest.approx(0.75)
        assert row["score"] == pytest.approx(0.8)
    finally:
        conn.close()


def test_a_held_row_carries_the_detail_and_no_confidence() -> None:
    conn, store = _store()
    try:
        _apply(conn, store, _result({"cl-1": ["pp-1"]}, held=["pp-9"]), _run())
        row = conn.execute(
            "SELECT * FROM cluster_decisions WHERE kind = 'ambiguous'"
        ).fetchone()
        assert row["pain_a"] == "pp-9"
        assert row["basis"] == "ambiguous_unresolved"
        assert row["detail"] == "the adjudicator could not tell the two apart"
        assert row["confidence"] is None
    finally:
        conn.close()


def test_the_parameters_of_a_run_that_cannot_be_written_fail_the_write() -> None:
    conn, store = _store()
    try:
        with pytest.raises(ClusterStoreError):
            store.record_run(_run("run-1", params={"threshold": object()}))
        assert store.run("run-1") is None
    finally:
        conn.close()


# --- the identity, the moves and the refusals -------------------------------


def test_the_durable_id_is_kept_when_the_proposal_is_renamed() -> None:
    conn, store = _store()
    try:
        _apply(conn, store, _result({"cl-1": ["pp-1"]}), _run("run-1"))
        _apply(conn, store, _result({"cl-2": ["pp-1"]}), _run("run-2"))
        # The proposal overlaps the stored cluster, so it takes over its id
        # and the provisional id is dropped.
        assert store.cluster("cl-1") is not None
        assert store.cluster("cl-2") is None
        cluster = store.cluster("cl-1")
        assert cluster is not None
        assert cluster.version == 1
        assert conn.execute("SELECT count(*) FROM clusters").fetchone()[0] == 1
    finally:
        conn.close()


def test_a_pain_point_that_moved_leaves_one_open_membership() -> None:
    conn, store = _store()
    try:
        _apply(conn, store, _result({"cl-1": ["pp-1", "pp-2"]}), _run("run-1"))
        # The first proposal takes over the stored cluster, the second one
        # is new, so pp-2 moves into it. The new cluster comes first, so
        # the membership it opens is the one that would break the rule of
        # one open membership per pain point.
        _apply(
            conn,
            store,
            _result({"cl-3": ["pp-2"], "cl-2": ["pp-1"]}),
            _run("run-2"),
        )
        rows = conn.execute(
            "SELECT cluster_id, valid_to FROM cluster_members "
            "WHERE pain_id = 'pp-2' ORDER BY valid_from"
        ).fetchall()
        assert len(rows) == 2
        assert rows[0]["cluster_id"] == "cl-1"
        assert rows[0]["valid_to"] is not None
        assert rows[1]["cluster_id"] == "cl-3"
        assert rows[1]["valid_to"] is None
        assert [m.pain_id for m in store.current_memberships()] == ["pp-1", "pp-2"]
    finally:
        conn.close()


def test_the_closed_count_covers_a_row_that_moved() -> None:
    conn, store = _store()
    try:
        _apply(conn, store, _result({"cl-1": ["pp-1", "pp-2"]}), _run("run-1"))
        summary = _apply(
            conn,
            store,
            _result({"cl-3": ["pp-2"], "cl-2": ["pp-1"]}),
            _run("run-2"),
        )
        assert summary.memberships_closed == 1
    finally:
        conn.close()


def test_a_cluster_that_lost_its_last_member_is_dissolved() -> None:
    conn, store = _store()
    try:
        _apply(conn, store, _result({"cl-1": ["pp-1", "pp-2"]}), _run("run-1"))
        _apply(
            conn,
            store,
            _result({"cl-2": ["pp-9"]}, held=["pp-1", "pp-2"]),
            _run("run-2"),
        )
        cluster = store.cluster("cl-1")
        assert cluster is not None
        # The cluster holds nothing, and a cluster that holds nothing is
        # dissolved.
        assert cluster.status is ClusterStatus.dissolved
        assert cluster.member_ids == []
        versions = store.cluster_versions("cl-1")
        assert [version.version for version in versions] == [1, 2]
        assert versions[1].status is ClusterStatus.dissolved
    finally:
        conn.close()


def test_a_pain_point_that_is_both_assigned_and_held_is_refused() -> None:
    conn, store = _store()
    try:
        result = _result({"cl-1": ["pp-1"]}, held=["pp-1"])
        with pytest.raises(ClusterStoreError):
            _apply(conn, store, result, _run())
        # Nothing was written.
        assert conn.execute("SELECT count(*) FROM clusters").fetchone()[0] == 0
    finally:
        conn.close()


def test_a_held_pain_point_loses_its_membership() -> None:
    conn, store = _store()
    try:
        _apply(conn, store, _result({"cl-1": ["pp-9"]}), _run("run-1"))
        summary = _apply(
            conn, store, _result({"cl-2": ["pp-1"]}, held=["pp-9"]), _run("run-2")
        )
        assert summary.memberships_closed == 1
        assert [m.pain_id for m in store.current_memberships()] == ["pp-1"]
        # The cluster that lost the held pain point moved a version, and a
        # snapshot records the change.
        cluster = store.cluster("cl-1")
        assert cluster is not None
        assert cluster.version == 2
        assert cluster.member_ids == []
        versions = store.cluster_versions("cl-1")
        assert [version.version for version in versions] == [1, 2]
        assert versions[1].member_ids == []
    finally:
        conn.close()


def test_a_held_pain_point_in_a_cluster_of_the_result_moves_its_version() -> None:
    conn, store = _store()
    try:
        _apply(conn, store, _result({"cl-1": ["pp-1", "pp-9"]}), _run("run-1"))
        _apply(
            conn, store, _result({"cl-1": ["pp-1"]}, held=["pp-9"]), _run("run-2")
        )
        cluster = store.cluster("cl-1")
        assert cluster is not None
        assert cluster.member_ids == ["pp-1"]
        assert cluster.version == 2
        versions = store.cluster_versions("cl-1")
        assert [version.version for version in versions] == [1, 2]
        assert versions[1].member_ids == ["pp-1"]
    finally:
        conn.close()


def test_a_cluster_that_names_a_member_it_does_not_assign_is_refused() -> None:
    conn, store = _store()
    try:
        result = _result({"cl-1": ["pp-1", "pp-2"]})
        result = result.model_copy(
            update={
                "assignments": [
                    assignment
                    for assignment in result.assignments
                    if assignment.pain_id == "pp-1"
                ]
            }
        )
        with pytest.raises(ClusterStoreError):
            _apply(conn, store, result, _run())
    finally:
        conn.close()


def test_two_runs_that_reach_the_same_decision_write_two_rows() -> None:
    conn, store = _store()
    try:
        result = _result({"cl-1": ["pp-1", "pp-2"]}, decisions=[_decision()])
        _apply(conn, store, result, _run("run-1"))
        _apply(conn, store, result, _run("run-2"))
        rows = conn.execute("SELECT * FROM cluster_decisions").fetchall()
        assert len(rows) == 2
        assert {row["run_id"] for row in rows} == {"run-1", "run-2"}
    finally:
        conn.close()


def test_the_same_run_applied_twice_writes_one_decision_row() -> None:
    conn, store = _store()
    try:
        result = _result({"cl-1": ["pp-1", "pp-2"]}, decisions=[_decision()])
        _apply(conn, store, result, _run("run-1"))
        _apply(conn, store, result, _run("run-1"))
        rows = conn.execute("SELECT * FROM cluster_decisions").fetchall()
        assert len(rows) == 1
    finally:
        conn.close()


def test_the_decision_row_carries_the_durable_cluster_id() -> None:
    conn, store = _store()
    try:
        _apply(conn, store, _result({"cl-9": ["pp-1"]}), _run("run-1"))
        result = _result(
            {"cl-2": ["pp-1"]}, decisions=[_decision(cluster_id="cl-2")]
        )
        _apply(conn, store, result, _run("run-2"))
        row = conn.execute("SELECT * FROM cluster_decisions").fetchone()
        assert row["cluster_id"] == "cl-9"
    finally:
        conn.close()


# --- the space of a merge and a split ---------------------------------------


def test_a_merge_in_another_space_is_refused() -> None:
    conn, store = _store()
    try:
        _apply(
            conn, store, _result({"cl-1": ["pp-1"], "cl-2": ["pp-2"]}), _run("run-1")
        )
        other = _space("another-model")
        with pytest.raises(MixedVectorSpaceError) as excinfo:
            store.merge_clusters("cl-1", "cl-2", run=_run("run-2", space=other))
        assert _space().space_id in str(excinfo.value)
        assert other.space_id in str(excinfo.value)
        # Nothing was written: the predecessors are still active.
        for predecessor in ("cl-1", "cl-2"):
            cluster = store.cluster(predecessor)
            assert cluster is not None
            assert cluster.status is ClusterStatus.active
    finally:
        conn.close()


def test_a_split_in_another_space_is_refused() -> None:
    conn, store = _store()
    try:
        _apply(conn, store, _result({"cl-1": ["pp-1", "pp-2"]}), _run("run-1"))
        other = _space("another-model")
        with pytest.raises(MixedVectorSpaceError):
            store.split_cluster(
                "cl-1", [["pp-1"], ["pp-2"]], run=_run("run-2", space=other)
            )
        cluster = store.cluster("cl-1")
        assert cluster is not None
        assert cluster.status is ClusterStatus.active
    finally:
        conn.close()


# --- the coverage of a split ------------------------------------------------


def test_a_split_that_drops_a_member_is_refused() -> None:
    conn, store = _store()
    try:
        _apply(conn, store, _result({"cl-1": ["pp-1", "pp-2"]}), _run("run-1"))
        with pytest.raises(ClusterStoreError):
            store.split_cluster("cl-1", [["pp-1"]], run=_run("run-2"))
        # Nothing was written: the parent still holds both members.
        cluster = store.cluster("cl-1")
        assert cluster is not None
        assert cluster.status is ClusterStatus.active
        assert cluster.member_ids == ["pp-1", "pp-2"]
    finally:
        conn.close()


def test_a_split_that_adopts_a_stranger_is_refused() -> None:
    conn, store = _store()
    try:
        _apply(conn, store, _result({"cl-1": ["pp-1", "pp-2"]}), _run("run-1"))
        with pytest.raises(ClusterStoreError):
            store.split_cluster(
                "cl-1", [["pp-1"], ["pp-2"], ["pp-9"]], run=_run("run-2")
            )
        cluster = store.cluster("cl-1")
        assert cluster is not None
        assert cluster.status is ClusterStatus.active
        assert cluster.member_ids == ["pp-1", "pp-2"]
    finally:
        conn.close()


# --- the version of a status ------------------------------------------------


def test_a_merged_predecessor_gets_a_new_version() -> None:
    conn, store = _store()
    try:
        _apply(
            conn, store, _result({"cl-1": ["pp-1"], "cl-2": ["pp-2"]}), _run("run-1")
        )
        store.merge_clusters("cl-1", "cl-2", run=_run("run-2"))
        for predecessor in ("cl-1", "cl-2"):
            cluster = store.cluster(predecessor)
            assert cluster is not None
            assert cluster.status is ClusterStatus.merged
            # The status is part of what the cluster means, so the version
            # moved with it.
            assert cluster.version == 2
            versions = store.cluster_versions(predecessor)
            assert [version.version for version in versions] == [1, 2]
            assert versions[0].status is ClusterStatus.active
            assert versions[1].status is ClusterStatus.merged
    finally:
        conn.close()


def test_a_split_parent_gets_a_new_version() -> None:
    conn, store = _store()
    try:
        _apply(conn, store, _result({"cl-1": ["pp-1", "pp-2"]}), _run("run-1"))
        store.split_cluster("cl-1", [["pp-1"], ["pp-2"]], run=_run("run-2"))
        parent = store.cluster("cl-1")
        assert parent is not None
        assert parent.status is ClusterStatus.split
        assert parent.version == 2
        versions = store.cluster_versions("cl-1")
        assert [version.version for version in versions] == [1, 2]
        assert versions[0].status is ClusterStatus.active
        assert versions[1].status is ClusterStatus.split
    finally:
        conn.close()


def _counts(conn: sqlite3.Connection) -> dict[str, int]:
    """The number of rows of every table the store writes."""
    return {
        table: conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        for table in (
            "clustering_runs",
            "clusters",
            "cluster_versions",
            "cluster_members",
            "cluster_lineage",
            "cluster_decisions",
        )
    }

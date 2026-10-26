from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from product_hunter.db import (
    MIGRATIONS_DIR,
    MigrationError,
    connect,
    current_version,
    migrate,
)

# The number of migration files in db/migrations, and so the schema version a
# migrated database carries. One number, so a new migration is a one-line
# change here instead of a hunt through the file.
EXPECTED_VERSION = 16

EXPECTED_TABLES = {
    "sources",
    "fetches",
    "documents",
    "authors",
    "communities",
    "author_links",
    "evidence",
    "pain_points",
    "evidence_pain_links",
    "propagation_edges",
    "research_runs",
    "research_artifacts",
    "research_events",
    "research_checkpoints",
    "jobs",
    "stage_attempts",
    "projection_tasks",
    "spend_ledger",
    "human_decisions",
    "opportunities",
    "opportunity_scores",
    "opportunity_evidence",
    "opportunity_claims",
    "experiments",
    "research_requests",
    "model_catalogs",
    "model_inventory",
    "model_role_bindings",
    "model_executions",
    "tool_calls",
    "research_stage_executions",
    "research_stage_artifacts",
    "research_claims",
    "stage_gate_results",
    "collection_source_state",
    "collection_run_state",
    "collection_items",
    "document_versions",
    "evidence_observations",
    "evidence_lineage",
    "clustering_runs",
    "clusters",
    "cluster_versions",
    "cluster_members",
    "cluster_lineage",
    "cluster_decisions",
    "author_identity_links",
    "community_links",
    "author_clusters",
    "author_cluster_members",
    "independence_reports",
    "source_candidates",
    "source_reviews",
    "model_comparisons",
    "worker_health",
    "operation_runs",
    "operation_events",
    "operation_budget",
    "source_pacing",
}


def _tables(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    return {row[0] for row in rows}


def _write_migration(directory: Path, name: str, body: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / name).write_text(body, encoding="utf-8")


def test_initial_migration_creates_the_authoritative_schema() -> None:
    conn = connect(":memory:")
    try:
        assert migrate(conn) == EXPECTED_VERSION
        assert current_version(conn) == EXPECTED_VERSION
        assert EXPECTED_TABLES <= _tables(conn)
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        conn.close()


def test_second_migrate_is_a_noop() -> None:
    conn = connect(":memory:")
    try:
        assert migrate(conn) == EXPECTED_VERSION
        assert migrate(conn) == EXPECTED_VERSION
        assert current_version(conn) == EXPECTED_VERSION
    finally:
        conn.close()


def test_research_gate_migration_creates_the_gate_tables() -> None:
    conn = connect(":memory:")
    try:
        assert migrate(conn) == EXPECTED_VERSION
        assert current_version(conn) == EXPECTED_VERSION
        assert {
            "tool_calls",
            "research_stage_executions",
            "research_stage_artifacts",
            "research_claims",
            "stage_gate_results",
        } <= _tables(conn)
        # The stage artifacts of the gate are not the file artifacts of an
        # extraction run: the two tables are separate on purpose.
        assert "research_artifacts" in _tables(conn)
    finally:
        conn.close()

def test_database_newer_than_migrations_raises() -> None:
    conn = connect(":memory:")
    try:
        conn.execute("PRAGMA user_version = 99")
        with pytest.raises(MigrationError):
            migrate(conn)
    finally:
        conn.close()


def test_migrations_apply_in_number_order(tmp_path: Path) -> None:
    migrations_dir = tmp_path / "migrations"
    _write_migration(migrations_dir, "0001_first.sql", "CREATE TABLE first (id TEXT);")
    _write_migration(migrations_dir, "0002_second.sql", "CREATE TABLE second (id TEXT);")
    conn = connect(":memory:")
    try:
        assert migrate(conn, migrations_dir=migrations_dir) == 2
        assert current_version(conn) == 2
        assert {"first", "second"} <= _tables(conn)
    finally:
        conn.close()


def test_failed_migration_rolls_back_schema_and_version(tmp_path: Path) -> None:
    migrations_dir = tmp_path / "migrations"
    _write_migration(
        migrations_dir,
        "0001_broken.sql",
        "-- a valid table followed by broken SQL\n"
        "CREATE TABLE created_first (id TEXT PRIMARY KEY);\n"
        "THIS IS NOT SQL;\n",
    )
    conn = connect(":memory:")
    try:
        with pytest.raises(MigrationError):
            migrate(conn, migrations_dir=migrations_dir)
        assert current_version(conn) == 0
        assert "created_first" not in _tables(conn)
    finally:
        conn.close()


def test_duplicate_migration_numbers_raise(tmp_path: Path) -> None:
    migrations_dir = tmp_path / "migrations"
    _write_migration(migrations_dir, "0001_first.sql", "CREATE TABLE first (id TEXT);")
    _write_migration(migrations_dir, "0001_second.sql", "CREATE TABLE second (id TEXT);")
    conn = connect(":memory:")
    try:
        with pytest.raises(MigrationError):
            migrate(conn, migrations_dir=migrations_dir)
        assert current_version(conn) == 0
    finally:
        conn.close()


def test_gap_in_migration_numbers_raises(tmp_path: Path) -> None:
    migrations_dir = tmp_path / "migrations"
    _write_migration(migrations_dir, "0001_first.sql", "CREATE TABLE first (id TEXT);")
    _write_migration(migrations_dir, "0003_third.sql", "CREATE TABLE third (id TEXT);")
    conn = connect(":memory:")
    try:
        with pytest.raises(MigrationError):
            migrate(conn, migrations_dir=migrations_dir)
        assert current_version(conn) == 0
    finally:
        conn.close()


def test_file_database_creates_parent_directory_and_uses_wal(tmp_path: Path) -> None:
    db_path = tmp_path / "nested" / "product_hunter.db"
    conn = connect(db_path)
    try:
        assert db_path.exists()
        assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    finally:
        conn.close()


def test_read_only_open_of_missing_database_raises(tmp_path: Path) -> None:
    missing = tmp_path / "missing.db"
    with pytest.raises(FileNotFoundError):
        connect(missing, read_only=True)
    assert not missing.exists()


def test_read_only_connection_reads_an_existing_database(tmp_path: Path) -> None:
    db_path = tmp_path / "product_hunter.db"
    conn = connect(db_path)
    try:
        migrate(conn)
        conn.execute(
            "INSERT INTO sources (source_id, source_type, terms_status) VALUES (?, ?, ?)",
            ("hn_algolia", "community", "permitted"),
        )
    finally:
        conn.close()
    read_only = connect(db_path, read_only=True)
    try:
        row = read_only.execute(
            "SELECT source_id FROM sources WHERE source_id = ?", ("hn_algolia",)
        ).fetchone()
        assert row["source_id"] == "hn_algolia"
        with pytest.raises(sqlite3.OperationalError):
            read_only.execute(
                "INSERT INTO sources (source_id, source_type, terms_status) VALUES (?, ?, ?)",
                ("other", "community", "permitted"),
            )
    finally:
        read_only.close()


def test_initial_migration_refuses_missing_foreign_key() -> None:
    conn = connect(":memory:")
    try:
        migrate(conn)
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO documents (doc_id, fetch_id, source_id, url) VALUES (?, ?, ?, ?)",
                ("doc-1", "missing-fetch", "missing-source", "https://example.com/post"),
            )
    finally:
        conn.close()


def test_initial_migration_accepts_a_valid_source_fetch_document_chain() -> None:
    conn = connect(":memory:")
    try:
        migrate(conn)
        conn.execute(
            "INSERT INTO sources (source_id, source_type, terms_status) VALUES (?, ?, ?)",
            ("hn_algolia", "community", "permitted"),
        )
        conn.execute(
            "INSERT INTO fetches (fetch_id, source_id, requested_url) VALUES (?, ?, ?)",
            ("fetch-1", "hn_algolia", "https://hn.algolia.com/api/v1/search"),
        )
        conn.execute(
            "INSERT INTO documents "
            "(doc_id, fetch_id, source_id, url, text_sha256, text_path, "
            "raw_path, raw_sha256, parser_version) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "doc-1",
                "fetch-1",
                "hn_algolia",
                "https://example.com/post",
                "a" * 64,
                "data/processed/text/a.txt",
                "data/raw/hn/a.json",
                "b" * 64,
                "phase1-0.3",
            ),
        )
        assert conn.execute("SELECT count(*) FROM documents").fetchone()[0] == 1
    finally:
        conn.close()


def test_probe_health_migration_backfills_open_cycles(tmp_path: Path) -> None:
    """Upgrading a database that already has probe history must not violate
    the new unique index: only the probe's currently open run stays open."""
    import shutil

    # Migrate only up to 15 so we can seed probe history under the old index.
    migdir15 = tmp_path / "mig15"
    migdir15.mkdir()
    for path in MIGRATIONS_DIR.iterdir():
        if path.is_file() and int(path.name.split("_")[0]) <= 15:
            shutil.copy(path, migdir15 / path.name)

    db = tmp_path / "probe-upgrade.db"
    conn = connect(db)
    try:
        assert migrate(conn, migrations_dir=migdir15) == 15
        conn.execute(
            "INSERT INTO probes (probe_id, public_id, objective, status, "
            "configuration, created_at, updated_at) VALUES "
            "('p1','PRB-X','obj','RUNNING','{}','2026-01-01T00:00:00+00:00',"
            "'2026-01-01T00:00:00+00:00')"
        )
        conn.execute(
            "INSERT INTO research_runs (run_id, public_id, objective, "
            "original_request, status, created_at, updated_at, probe_id, "
            "completed_at) VALUES "
            "('r1','RH-1','obj','obj','COMPLETED',"
            "'2026-01-01T00:00:00+00:00','2026-01-01T00:00:00+00:00','p1',"
            "'2026-01-01T01:00:00+00:00')"
        )
        conn.execute(
            "INSERT INTO research_runs (run_id, public_id, objective, "
            "original_request, status, created_at, updated_at, probe_id) VALUES "
            "('r2','RH-2','obj','obj','QUEUED',"
            "'2026-01-02T00:00:00+00:00','2026-01-02T00:00:00+00:00','p1')"
        )
        conn.execute("UPDATE probes SET current_run_id='r2' WHERE probe_id='p1'")
        conn.commit()

        # Upgrade to 16: the backfill must close the historical run and keep
        # the open one, so the unique index can be created.
        assert migrate(conn) == EXPECTED_VERSION
        rows = conn.execute(
            "SELECT run_id, probe_cycle_closed_at FROM research_runs "
            "WHERE probe_id='p1' ORDER BY run_id"
        ).fetchall()
        assert rows[0][0] == "r1" and rows[0][1] is not None
        assert rows[1][0] == "r2" and rows[1][1] is None
        assert conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' "
            "AND name='idx_research_runs_one_open_probe_cycle'"
        ).fetchone() is not None
    finally:
        conn.close()


def test_fresh_database_applies_full_migration_chain(tmp_path: Path) -> None:
    """A production-shaped fresh database must migrate cleanly to version 16
    with every probe table, index, and constraint in place."""
    db = tmp_path / "fresh.db"
    conn = connect(db)
    try:
        version = migrate(conn)
        assert version == EXPECTED_VERSION
        assert current_version(conn) == EXPECTED_VERSION
        # Integrity and foreign keys.
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        tables = _tables(conn)
        for table in (
            "probes",
            "research_runs",
            "probe_events",
            "jobs",
            "stage_attempts",
            "opportunities",
            "evidence",
            "clusters",
            "pain_points",
            "spend_ledger",
        ):
            assert table in tables, f"missing table {table}"
        # Probe index present, old index dropped.
        assert conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' "
            "AND name='idx_research_runs_one_open_probe_cycle'"
        ).fetchone() is not None
        assert conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' "
            "AND name='idx_research_runs_one_active_probe_run'"
        ).fetchone() is None
        # probe_cycle_closed_at exists and is nullable.
        cols = {r[1] for r in conn.execute("PRAGMA table_info(research_runs)")}
        assert "probe_cycle_closed_at" in cols
        # No probe history yet -> no open-cycle rows.
        assert conn.execute("SELECT count(*) FROM probes").fetchone()[0] == 0
    finally:
        conn.close()


def test_second_migrate_is_idempotent_on_version_16(tmp_path: Path) -> None:
    """Migrating an already-16 database is a no-op and stays at 16."""
    db = tmp_path / "idem.db"
    conn = connect(db)
    try:
        assert migrate(conn) == EXPECTED_VERSION
        first = current_version(conn)
        assert migrate(conn) == EXPECTED_VERSION
        assert current_version(conn) == first
    finally:
        conn.close()


def test_upgrade_preserves_existing_opportunity_and_evidence_data(
    tmp_path: Path,
) -> None:
    """Upgrading to 16 must leave existing research data intact."""
    import shutil

    migdir15 = tmp_path / "mig15"
    migdir15.mkdir()
    for path in MIGRATIONS_DIR.iterdir():
        if path.is_file() and int(path.name.split("_")[0]) <= 15:
            shutil.copy(path, migdir15 / path.name)

    db = tmp_path / "upgrade-data.db"
    conn = connect(db)
    try:
        assert migrate(conn, migrations_dir=migdir15) == 15
        # Seed existing opportunity/evidence/probe data under the old schema.
        conn.execute(
            "INSERT INTO probes (probe_id, public_id, objective, status, "
            "configuration, created_at, updated_at) VALUES "
            "('p1','PRB-X','obj','RUNNING','{}','2026-01-01T00:00:00+00:00',"
            "'2026-01-01T00:00:00+00:00')"
        )
        conn.execute(
            "INSERT INTO research_runs (run_id, public_id, objective, "
            "original_request, status, created_at, updated_at, probe_id, "
            "completed_at) VALUES "
            "('r1','RH-1','obj','obj','COMPLETED',"
            "'2026-01-01T00:00:00+00:00','2026-01-01T00:00:00+00:00','p1',"
            "'2026-01-01T01:00:00+00:00')"
        )
        conn.execute(
            "INSERT INTO clusters (cluster_id, version, status, label, member_count, "
            "created_at, updated_at) VALUES "
            "('cl-1',1,'active','AI review bottleneck',1,'2026-01-01T00:00:00+00:00',"
            "'2026-01-01T00:00:00+00:00')"
        )
        conn.execute(
            "INSERT INTO opportunities (opportunity_id, title, run_id, cluster_id, canonical_id, "
            "lifecycle, severity, confidence, created_at, updated_at) VALUES "
            "('opp-1','AI review bottleneck','r1','cl-1','opp-1','CANDIDATE',4,16,"
            "'2026-01-01T00:00:00+00:00','2026-01-01T00:00:00+00:00')"
        )
        conn.commit()
        before = conn.execute(
            "SELECT count(*) FROM opportunities"
        ).fetchone()[0]
        before_probes = conn.execute("SELECT count(*) FROM probes").fetchone()[0]
        before_runs = conn.execute("SELECT count(*) FROM research_runs").fetchone()[0]

        assert migrate(conn) == EXPECTED_VERSION
        after = conn.execute("SELECT count(*) FROM opportunities").fetchone()[0]
        after_probes = conn.execute("SELECT count(*) FROM probes").fetchone()[0]
        after_runs = conn.execute("SELECT count(*) FROM research_runs").fetchone()[0]
        assert after == before
        assert after_probes == before_probes
        assert after_runs == before_runs
        # Canonical opportunity identity unchanged.
        row = conn.execute(
            "SELECT opportunity_id, canonical_id FROM opportunities WHERE opportunity_id='opp-1'"
        ).fetchone()
        assert row["opportunity_id"] == row["canonical_id"]
        # Probe history backfilled correctly: r1 closed, index present.
        run_row = conn.execute(
            "SELECT probe_cycle_closed_at FROM research_runs WHERE run_id='r1'"
        ).fetchone()
        assert run_row["probe_cycle_closed_at"] is not None
        assert conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' "
            "AND name='idx_research_runs_one_open_probe_cycle'"
        ).fetchone() is not None
    finally:
        conn.close()


def test_migrations_directory_holds_the_initial_migration() -> None:
    assert MIGRATIONS_DIR.is_dir()
    assert (MIGRATIONS_DIR / "0001_initial.sql").is_file()

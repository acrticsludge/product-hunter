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
EXPECTED_VERSION = 8

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


def test_migrations_directory_holds_the_initial_migration() -> None:
    assert MIGRATIONS_DIR.is_dir()
    assert (MIGRATIONS_DIR / "0001_initial.sql").is_file()

"""The ingestion: the mirror, the refresh, the rebuild and the reconciliation."""

from __future__ import annotations

import hashlib
import shutil
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from product_hunter.analytics import Analytics, AnalyticsError
from product_hunter.analytics import ingest as ingest_module
from product_hunter.analytics.tables import ColumnSpec, TableSpec, discover, row_hash
from product_hunter.db.connection import connect
from product_hunter.db.migrations import migrate


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    """A migrated database with a source and a table of notes in it."""
    path = tmp_path / "product_hunter.db"
    conn = connect(path)
    migrate(conn)
    conn.execute(
        "INSERT INTO sources (source_id, source_type, terms_status) "
        "VALUES ('src_1', 'forum', 'permitted')"
    )
    conn.execute(
        "CREATE TABLE notes ("
        "note_id TEXT PRIMARY KEY NOT NULL, "
        "body TEXT NOT NULL, "
        "updated_at TEXT NOT NULL)"
    )
    conn.execute(
        "INSERT INTO notes (note_id, body, updated_at) "
        "VALUES ('n1', 'first', '2026-01-01T00:00:00Z')"
    )
    conn.close()
    return path


@pytest.fixture
def store(db_path: Path, tmp_path: Path) -> Iterator[Analytics]:
    """The analytical store, open on the database."""
    analytics = Analytics.open(tmp_path / "analytics.duckdb", db_path)
    yield analytics
    analytics.close()


def write(db_path: Path, statement: str) -> None:
    """Run one statement against the database, the way a writer would."""
    conn = connect(db_path)
    conn.execute(statement)
    conn.close()


def mirrored_state(store: Analytics) -> dict[str, list[dict]]:
    """Every mirrored row, by table, in a stable order."""
    names = store.sql(
        "SELECT table_name FROM duckdb_tables() "
        "WHERE NOT internal AND NOT temporary "
        "AND database_name = current_database() AND table_name <> 'analytics_state' "
        "ORDER BY table_name"
    )
    return {
        str(row["table_name"]): store.sql(
            f'SELECT * FROM "{str(row["table_name"])}" ORDER BY ALL'
        )
        for row in names
    }


def test_the_insert_statement_names_every_column() -> None:
    """The write statement covers every column of the mirror, hash included."""
    spec = TableSpec(
        name="notes",
        columns=(
            ColumnSpec("note_id", "VARCHAR", False),
            ColumnSpec("body", "VARCHAR", False),
        ),
        primary_key=("note_id",),
    )
    statement = spec.insert_sql()
    assert statement.startswith('INSERT OR REPLACE INTO "notes"')
    assert '"note_id"' in statement
    assert '"body"' in statement
    assert '"_ph_hash"' in statement


def test_snapshot_mirrors_every_table(store: Analytics, db_path: Path) -> None:
    """Every table of the database has a mirror with the same rows."""
    store.snapshot()
    conn = connect(db_path, read_only=True)
    try:
        names = conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
        for row in names:
            name = str(row["name"])
            expected = conn.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
            mirrored = store.sql(f'SELECT COUNT(*) AS n FROM "{name}"')[0]["n"]
            assert mirrored == expected, name
    finally:
        conn.close()


def test_snapshot_stores_the_row_hash(store: Analytics) -> None:
    """The stored hash is the SHA-256 of the canonical form of the row."""
    store.snapshot()
    row = store.sql('SELECT note_id, body, updated_at, "_ph_hash" FROM notes')[0]
    assert row["_ph_hash"] == row_hash(["n1", "first", "2026-01-01T00:00:00Z"])


def test_refresh_reads_only_the_new_rows(store: Analytics, db_path: Path) -> None:
    """A new row lands, and only that row is written."""
    store.snapshot()
    write(
        db_path,
        "INSERT INTO notes (note_id, body, updated_at) "
        "VALUES ('n2', 'second', '2026-06-01T00:00:00Z')",
    )
    report = store.refresh()
    assert report.rows_written == 1
    assert "1 rows written" in report.summary()
    notes = {table.name: table for table in report.tables}["notes"]
    assert notes.mode == "incremental"
    rows = store.sql("SELECT note_id FROM notes ORDER BY note_id")
    assert [row["note_id"] for row in rows] == ["n1", "n2"]


def test_a_view_whose_table_is_missing_is_skipped(store: Analytics) -> None:
    """A view over a table that is not there is reported, not faked."""
    report = store.snapshot()
    views = {view.name: view for view in report.views}
    assert views["v_score_history"].created is False
    assert "opportunity_scores" in views["v_score_history"].reason
    assert views["v_pain_point_trend"].created is True
    assert "v_score_history" not in store.views()


def test_duplicate_ingestion_is_a_no_op(store: Analytics) -> None:
    """Two snapshots of the same data leave one row per key."""
    store.snapshot()
    store.snapshot()
    rows = store.sql("SELECT note_id, COUNT(*) AS n FROM notes GROUP BY note_id")
    assert rows == [{"note_id": "n1", "n": 1}]


def test_changed_source_is_rewritten(store: Analytics, db_path: Path) -> None:
    """A changed row is written again, with the hash that belongs to it."""
    store.snapshot()
    write(
        db_path,
        "UPDATE notes SET body = 'changed', updated_at = '2026-07-01T00:00:00Z' "
        "WHERE note_id = 'n1'",
    )
    report = store.refresh()
    assert report.rows_written == 1
    row = store.sql('SELECT body, updated_at, "_ph_hash" FROM notes')[0]
    assert row["body"] == "changed"
    assert row["_ph_hash"] == row_hash(["n1", "changed", "2026-07-01T00:00:00Z"])


def test_deleted_source_is_removed(store: Analytics, db_path: Path) -> None:
    """A row deleted in the database is gone from the mirror."""
    store.snapshot()
    write(db_path, "DELETE FROM notes WHERE note_id = 'n1'")
    report = store.refresh()
    assert report.rows_removed == 1
    assert store.sql("SELECT note_id FROM notes") == []


def test_rebuild_matches_the_snapshot(store: Analytics) -> None:
    """The rebuilt state is the snapshot state, row for row and hash for hash."""
    store.snapshot()
    before = mirrored_state(store)
    store.rebuild()
    assert mirrored_state(store) == before


def test_reconcile_names_the_drift(store: Analytics, db_path: Path) -> None:
    """A row that changed behind the layer's back is named."""
    store.snapshot()
    write(
        db_path,
        "UPDATE notes SET body = 'changed behind our back' WHERE note_id = 'n1'",
    )
    report = store.reconcile()
    drift = {table.name: table for table in report.tables}["notes"]
    assert drift.changed == ('["n1"]',)
    assert drift.sqlite_rows == 1
    assert drift.duckdb_rows == 1
    assert not report.in_sync
    assert report.drifted_tables() == ("notes",)


def test_reconcile_is_in_sync_after_a_snapshot(store: Analytics) -> None:
    """A fresh snapshot reconciles clean."""
    store.snapshot()
    report = store.reconcile()
    assert report.in_sync
    assert report.drifted_tables() == ()


def test_rebuild_repairs_the_drift(store: Analytics, db_path: Path) -> None:
    """The rebuild is the repair: the drift is gone after it."""
    store.snapshot()
    write(
        db_path,
        "UPDATE notes SET body = 'changed behind our back' WHERE note_id = 'n1'",
    )
    assert not store.reconcile().in_sync
    store.rebuild()
    assert store.reconcile().in_sync


def test_a_failed_refresh_leaves_the_state_readable(
    store: Analytics, db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A refresh that fails leaves the state it had, and a rebuild converges it."""
    store.snapshot()
    before = mirrored_state(store)
    conn = connect(db_path, read_only=True)
    specs = discover(conn)
    conn.close()
    ghost = TableSpec(
        name="ghost",
        columns=(ColumnSpec("id", "BIGINT", False),),
        primary_key=("id",),
    )
    monkeypatch.setattr(ingest_module, "discover", lambda _conn: (*specs, ghost))
    with pytest.raises(AnalyticsError):
        store.refresh()
    assert mirrored_state(store) == before
    monkeypatch.undo()
    store.rebuild()
    assert store.reconcile().in_sync


def test_the_sqlite_file_is_never_written(store: Analytics, db_path: Path) -> None:
    """The database cannot be written through the layer, and does not change.

    The layer opens the database read-only, so a write through the very
    connection the layer uses is refused by SQLite itself. The test reaches
    into the store for that connection on purpose: the claim is about the
    connection the layer holds, not about a connection the test opens.
    """
    with pytest.raises(sqlite3.OperationalError):
        store._sqlite.execute(
            "INSERT INTO sources (source_id, source_type, terms_status) "
            "VALUES ('src_x', 'forum', 'permitted')"
        )
    before = hashlib.sha256(db_path.read_bytes()).hexdigest()
    store.snapshot()
    store.refresh()
    assert hashlib.sha256(db_path.read_bytes()).hexdigest() == before


def test_a_missing_sqlite_file_is_refused(tmp_path: Path) -> None:
    """A missing database is refused before anything is written."""
    with pytest.raises(AnalyticsError):
        Analytics.open(tmp_path / "analytics.duckdb", tmp_path / "missing.db")


def test_a_table_with_a_nullable_key_is_refused(
    db_path: Path, tmp_path: Path
) -> None:
    """A table whose key can hold null is left alone, and the rest still lands.

    SQLite lets a key column hold null and DuckDB refuses a null in a key
    column, so such a table has rows the mirror cannot hold. The ingest
    says so and carries on with the other tables rather than failing.
    """
    write(db_path, "CREATE TABLE loose (loose_id TEXT PRIMARY KEY, body TEXT)")
    write(db_path, "INSERT INTO loose (loose_id, body) VALUES (NULL, 'x')")
    analytics = Analytics.open(tmp_path / "analytics.duckdb", db_path)
    try:
        report = analytics.snapshot()
        refused = [table for table in report.tables if table.mode == "refused"]
        assert [table.name for table in refused] == ["loose"]
        assert "null" in refused[0].reason
        # The tables that can be mirrored were.
        assert analytics.sql("SELECT count(*) AS n FROM notes")[0]["n"] == 1
        # And the refusal is visible as drift rather than as silence.
        assert "loose" in analytics.reconcile().drifted_tables()
    finally:
        analytics.close()


def test_the_analytics_file_may_not_be_the_database(tmp_path: Path) -> None:
    """The derived state may not sit on the authoritative file."""
    path = tmp_path / "product_hunter.db"
    conn = connect(path)
    migrate(conn)
    conn.close()
    with pytest.raises(AnalyticsError):
        Analytics.open(path, path)

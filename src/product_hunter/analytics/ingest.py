"""The analytical store: the derived DuckDB state, and the read path that fills it.

SQLite is the authoritative store and stays that way. This module opens it
read-only, so the layer has no write path to the authoritative store at all,
and it mirrors the database into DuckDB. The DuckDB file is derived state: it
can be thrown away and rebuilt, and the rebuild is deterministic.

Every table is written in its own transaction, so a table that fails to
mirror does not undo the tables that already landed, and the repair is
always the rebuild.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import duckdb

from product_hunter.analytics.queries import QUERIES
from product_hunter.analytics.tables import (
    AnalyticsError,
    TableSpec,
    discover,
    row_hash,
)

logger = logging.getLogger(__name__)
from product_hunter.analytics.views import ViewReport, create_views, required_tables
from product_hunter.db.connection import connect
from product_hunter.fetch import utcnow

# The table the derived state keeps its own bookkeeping in.
STATE_TABLE = "analytics_state"

# The temporary table the delete pass compares the keys against.
_KEY_TABLE = "_ph_keys"

# The rows one insert statement carries. The statement grows with the batch,
# and a very long list of values is parsed more slowly than a short one.
_BATCH_ROWS = 1000

# The type names this layer writes. A type outside this set is not treated
# as a difference: the table is left alone rather than dropped and written
# again for a spelling this version of DuckDB chose.
_MIRROR_TYPES = frozenset({"VARCHAR", "BIGINT", "DOUBLE", "BLOB", "TIMESTAMP"})


def _coerce_value(value: object, duckdb_type: str) -> object:
    """Coerce a SQLite value to the DuckDB column type.

    SQLite's type affinity lets any value sit in any column, so an INTEGER
    column can hold a text value such as ``'success'``. DuckDB is strict: a
    BIGINT column refuses a string. Coerce numeric columns so the mirror
    never fails on SQLite's loose typing.
    """
    if value is None:
        return None
    if duckdb_type == "BIGINT":
        try:
            return int(value)
        except (ValueError, TypeError):
            return 0
    if duckdb_type == "DOUBLE":
        try:
            return float(value)
        except (ValueError, TypeError):
            return 0.0
    return value

_STATE_DDL = """
CREATE TABLE IF NOT EXISTS analytics_state (
    table_name  VARCHAR PRIMARY KEY,
    watermark   VARCHAR,
    row_count   BIGINT NOT NULL DEFAULT 0,
    ingested_at VARCHAR NOT NULL
)
"""


# (AnalyticsError moved to tables.py)


@dataclass(frozen=True)
class TableReport:
    """What happened to one table.

    The mode is ``snapshot`` when the table was read in full, ``incremental``
    when the read was filtered by the stored watermark, ``skipped`` when the
    database no longer holds the table and its mirror was dropped, and
    ``refused`` when the table cannot be mirrored faithfully and was left
    alone rather than half mirrored.
    """

    name: str
    mode: str
    rows_read: int
    rows_written: int
    rows_removed: int
    watermark: str | None
    reason: str = ""


@dataclass(frozen=True)
class IngestReport:
    """What one ingestion did."""

    started_at: str
    tables: tuple[TableReport, ...]
    views: tuple[ViewReport, ...] = ()

    @property
    def rows_written(self) -> int:
        """The number of rows written."""
        return sum(table.rows_written for table in self.tables)

    @property
    def rows_removed(self) -> int:
        """The number of rows removed."""
        return sum(table.rows_removed for table in self.tables)

    def summary(self) -> str:
        """A one-line account of the ingestion."""
        created = sum(1 for view in self.views if view.created)
        skipped = len(self.views) - created
        return (
            f"{len(self.tables)} tables, {self.rows_written} rows written, "
            f"{created} views created, {skipped} views skipped"
        )


@dataclass(frozen=True)
class TableDrift:
    """The drift of one table between the database and the derived state.

    ``added`` names the keys the derived state holds and the database does
    not, ``removed`` the keys the database holds and the derived state does
    not, and ``changed`` the keys both hold whose content moved.
    """

    name: str
    sqlite_rows: int
    duckdb_rows: int
    added: tuple[str, ...]
    removed: tuple[str, ...]
    changed: tuple[str, ...]


@dataclass(frozen=True)
class ReconcileReport:
    """The drift of every table between the database and the derived state."""

    tables: tuple[TableDrift, ...]

    @property
    def in_sync(self) -> bool:
        """Whether the derived state holds what the database holds."""
        return not self.drifted_tables()

    def drifted_tables(self) -> tuple[str, ...]:
        """The names of the tables that drifted."""
        return tuple(
            table.name
            for table in self.tables
            if table.added or table.removed or table.changed
        )


class Analytics:
    """The derived analytical state, and the read path that fills it.

    The class is not thread-safe: it holds one DuckDB connection and one
    SQLite connection, and neither may be used from two threads at once.
    The SQLite connection refuses a cross-thread use on its own
    (``check_same_thread=True``); the DuckDB connection has no such
    refusal, so the rule is stated here instead. A caller that needs the
    state from several threads opens one ``Analytics`` per thread.
    """

    def __init__(
        self,
        duck: duckdb.DuckDBPyConnection,
        sqlite: sqlite3.Connection,
    ) -> None:
        self._duck = duck
        self._sqlite = sqlite

    @classmethod
    def open(cls, duckdb_path: str | Path, sqlite_path: str | Path) -> Analytics:
        """Open the derived state and the authoritative store.

        The database is opened read-only, so this layer cannot write to it.
        A missing database is refused before anything is written, and the two
        paths may not be the same file.
        """
        duck_path = Path(duckdb_path).expanduser()
        database = Path(sqlite_path).expanduser()
        if duck_path.resolve() == database.resolve():
            raise AnalyticsError(
                f"the analytics file and the database are the same file: {duck_path}"
            )
        try:
            sqlite_conn = connect(database, read_only=True)
        except FileNotFoundError as exc:
            logger.error("the database does not exist: %s", database)
            raise AnalyticsError(f"the database does not exist: {database}") from exc
        try:
            duck_path.parent.mkdir(parents=True, exist_ok=True)
            duck_conn = duckdb.connect(str(duck_path))
            duck_conn.execute("SET TimeZone='UTC'")
        except BaseException:
            sqlite_conn.close()
            raise
        return cls(duck_conn, sqlite_conn)

    def close(self) -> None:
        """Close both connections."""
        self._duck.close()
        self._sqlite.close()

    def __enter__(self) -> Analytics:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    # --- the operations ---------------------------------------------------

    def snapshot(self) -> IngestReport:
        """Read every table in full and write the derived state.

        Every table is written in its own transaction, so a table that fails
        does not undo the tables that already landed.
        """
        return self._ingest(self._snapshot_table, drop_stale=True)

    def refresh(self) -> IngestReport:
        """Read the rows newer than the stored watermark and write what changed.

        A row that changed without moving its watermark is not seen by this
        read. The reconciliation is what finds it.
        """
        return self._ingest(self._refresh_table, drop_stale=False)

    def _ingest(
        self,
        per_table: Callable[[TableSpec], TableReport],
        *,
        drop_stale: bool,
    ) -> IngestReport:
        """Mirror every table of the database and write the views."""
        started_at = utcnow().isoformat()
        specs = discover(self._sqlite)
        self._ensure_state_table()
        refused = [spec for spec in specs if spec.mirror_problem]
        mirrorable = [spec for spec in specs if not spec.mirror_problem]
        reports: list[TableReport] = []
        for spec in refused:
            logger.warning(
                "refused to mirror %s: %s", spec.name, spec.mirror_problem
            )
            reports.append(
                TableReport(
                    name=spec.name,
                    mode="refused",
                    rows_read=0,
                    rows_written=0,
                    rows_removed=0,
                    watermark=None,
                    reason=spec.mirror_problem,
                )
            )
        reports.extend(per_table(spec) for spec in mirrorable)
        if drop_stale:
            try:
                self._duck.execute("BEGIN TRANSACTION")
                stale_reports = self._drop_stale_tables(specs)
                self._duck.execute("COMMIT")
            except BaseException as exc:
                try:
                    self._duck.execute("ROLLBACK")
                except duckdb.Error:
                    pass
                logger.error("stale-table cleanup failed: %s", exc)
                raise AnalyticsError(f"stale-table cleanup failed: {exc}") from exc
            reports.extend(stale_reports)
        try:
            self._duck.execute("BEGIN TRANSACTION")
            views = create_views(
                self._duck, frozenset(spec.name for spec in mirrorable)
            )
            self._duck.execute("COMMIT")
        except BaseException as exc:
            try:
                self._duck.execute("ROLLBACK")
            except duckdb.Error:
                pass
            logger.error("view creation failed: %s", exc)
            raise AnalyticsError(f"view creation failed: {exc}") from exc
        return IngestReport(
            started_at=started_at, tables=tuple(reports), views=views
        )

    def rebuild(self) -> IngestReport:
        """Drop the derived state and build it again from the database.

        The rebuild is the repair: what it writes is what the database
        describes, and it writes it the same way every time.
        """
        try:
            self._duck.execute("BEGIN TRANSACTION")
            for view in self._view_names():
                self._duck.execute(f'DROP VIEW IF EXISTS "{view}"')
            for name in self._table_names():
                self._duck.execute(f'DROP TABLE IF EXISTS "{name}"')
            self._duck.execute(f'DROP TABLE IF EXISTS "{STATE_TABLE}"')
            self._duck.execute("COMMIT")
        except BaseException as exc:
            try:
                self._duck.execute("ROLLBACK")
            except duckdb.Error:
                pass
            logger.error("rebuild failed while dropping: %s", exc)
            raise AnalyticsError(f"rebuild failed while dropping: {exc}") from exc
        return self.snapshot()

    def reconcile(self) -> ReconcileReport:
        """Compare the database and the derived state, row by row.

        The report writes nothing: it names the drift so the caller can
        decide whether to rebuild.
        """
        specs = discover(self._sqlite)
        return ReconcileReport(tables=tuple(self._drift(spec) for spec in specs))

    # --- the read path ----------------------------------------------------

    def sql(
        self,
        statement: str,
        parameters: Sequence[object] | Mapping[str, object] = (),
    ) -> list[dict]:
        """Run a statement and return its rows as dictionaries.

        The statement is the caller's, and it is expected to be a read.
        """
        try:
            result = self._duck.execute(statement, parameters)
            names = [str(column[0]) for column in result.description]
            rows = result.fetchall()
        except duckdb.Error as exc:
            raise AnalyticsError(f"the statement failed: {exc}") from exc
        return [dict(zip(names, row)) for row in rows]

    def query(self, name: str, **parameters: object) -> list[dict]:
        """Run a named query and return its rows.

        A query whose view is not there is refused by name, with the table it
        needs: an empty answer would hide a table that never arrived.
        """
        spec = QUERIES.get(name)
        if spec is None:
            raise AnalyticsError(f"there is no query named {name!r}")
        self._require_view(spec.view)
        return self.sql(spec.sql, parameters)

    def views(self) -> tuple[str, ...]:
        """The names of the views the derived state holds."""
        return self._view_names()

    # --- the mirror -------------------------------------------------------

    def _snapshot_table(self, spec: TableSpec) -> TableReport:
        """Mirror one table in full."""
        self._duck.execute("BEGIN TRANSACTION")
        try:
            self._ensure_table(spec)
            rows = self._read_rows(spec)
            written = self._write_rows(spec, rows)
            removed, remaining = self._delete_missing(spec, self._keys(spec, rows))
            watermark = self._watermark_of(spec, rows)
            self._store_state(spec, watermark, remaining)
            self._duck.execute("COMMIT")
        except BaseException as exc:
            self._rollback()
            logger.error("snapshot of %s failed: %s", spec.name, exc)
            raise self._as_error(spec, exc)
        logger.info(
            "snapshot %s: read=%d written=%d removed=%d watermark=%s",
            spec.name, len(rows), written, removed, watermark,
        )
        return TableReport(
            name=spec.name,
            mode="snapshot",
            rows_read=len(rows),
            rows_written=written,
            rows_removed=removed,
            watermark=watermark,
        )

    def _refresh_table(self, spec: TableSpec) -> TableReport:
        """Mirror the rows of one table that changed."""
        column = spec.watermark
        stored = self._stored_watermark(spec.name)
        incremental = column is not None and stored is not None
        self._duck.execute("BEGIN TRANSACTION")
        try:
            self._ensure_table(spec)
            if incremental and column is not None:
                rows = self._read_rows(
                    spec,
                    f'"{column}" > ? OR "{column}" IS NULL',
                    [stored],
                )
            else:
                rows = self._read_rows(spec)
            known = self._stored_hashes(spec)
            changed = [
                values
                for values in rows
                if known.get(self._key_of(spec, values)) != row_hash(values)
            ]
            written = self._write_rows(spec, changed)
            removed, remaining = self._delete_missing(spec, self._read_keys(spec))
            watermark = self._new_watermark(spec, stored, rows)
            self._store_state(spec, watermark, remaining)
            self._duck.execute("COMMIT")
        except BaseException as exc:
            self._rollback()
            logger.error("mirror of %s failed: %s", spec.name, exc)
            raise self._as_error(spec, exc)
        mode = "incremental" if incremental else "snapshot"
        logger.info(
            "%s %s: read=%d written=%d removed=%d watermark=%s",
            mode, spec.name, len(rows), written, removed, watermark,
        )
        return TableReport(
            name=spec.name,
            mode=mode,
            rows_read=len(rows),
            rows_written=written,
            rows_removed=removed,
            watermark=watermark,
        )

    def _drift(self, spec: TableSpec) -> TableDrift:
        """The drift of one table, read from both sides."""
        database: dict[str, str] = {}
        for values in self._read_rows(spec):
            database[self._key_of(spec, values)] = row_hash(values)
        derived: dict[str, str] = {}
        if self._table_exists(spec.name):
            columns = ", ".join(f'"{name}"' for name in spec.primary_key)
            rows = self._duck.execute(
                f'SELECT {columns}, "{spec.hash_column}" FROM "{spec.name}"'
            ).fetchall()
            positions = _key_positions(spec)
            for row in rows:
                key = spec.key_of([row[position] for position in positions])
                derived[key] = str(row[-1])
        added = tuple(sorted(key for key in derived if key not in database))
        removed = tuple(sorted(key for key in database if key not in derived))
        changed = tuple(
            sorted(
                key
                for key, digest in database.items()
                if key in derived and derived[key] != digest
            )
        )
        return TableDrift(
            name=spec.name,
            sqlite_rows=len(database),
            duckdb_rows=len(derived),
            added=added,
            removed=removed,
            changed=changed,
        )

    def _ensure_table(self, spec: TableSpec) -> None:
        """Create the mirrored table, or create it again when the shape moved.

        The database schema is the authority, so a table whose shape changed
        is dropped and created again rather than kept.
        """
        if not self._table_exists(spec.name):
            self._duck.execute(spec.create_ddl())
            return
        rows = self._duck.execute(f'PRAGMA table_info("{spec.name}")').fetchall()
        current_names = [str(row[1]) for row in rows]
        current_types = [str(row[2]) for row in rows]
        current_key = tuple(
            str(row[1])
            for row in sorted((row for row in rows if row[5]), key=lambda row: row[5])
        )
        wanted_names = [column.name for column in spec.columns] + [spec.hash_column]
        wanted_types = [column.duckdb_type for column in spec.columns] + ["VARCHAR"]
        types_agree = len(current_types) == len(wanted_types) and all(
            _types_agree(reported, want)
            for reported, want in zip(current_types, wanted_types)
        )
        if (
            current_names != wanted_names
            or not types_agree
            or current_key != spec.primary_key
        ):
            self._duck.execute(f'DROP TABLE "{spec.name}"')
            self._duck.execute(spec.create_ddl())

    def _read_rows(
        self,
        spec: TableSpec,
        where: str = "",
        parameters: Sequence[object] = (),
    ) -> list[tuple]:
        """Read the rows of one table from the database."""
        columns = ", ".join(f'"{name}"' for name in spec.column_names())
        statement = f'SELECT {columns} FROM "{spec.name}"'
        if where:
            statement = f"{statement} WHERE {where}"
        try:
            rows = self._sqlite.execute(statement, parameters).fetchall()
        except sqlite3.Error as exc:
            raise AnalyticsError(
                f"the table {spec.name!r} could not be read: {exc}"
            ) from exc
        return [tuple(row) for row in rows]

    def _read_keys(self, spec: TableSpec) -> list[tuple]:
        """Read every key one table holds, in full.

        A row deleted in the database cannot be noticed by a filter on a
        timestamp, so the key set is read whole.
        """
        columns = ", ".join(f'"{name}"' for name in spec.primary_key)
        try:
            rows = self._sqlite.execute(
                f'SELECT {columns} FROM "{spec.name}"'
            ).fetchall()
        except sqlite3.Error as exc:
            raise AnalyticsError(
                f"the table {spec.name!r} could not be read: {exc}"
            ) from exc
        return [tuple(row) for row in rows]

    def _write_rows(self, spec: TableSpec, rows: Sequence[Sequence[object]]) -> int:
        """Write the rows into the mirrored table, hash included."""
        if not rows:
            return 0
        types = {column.name: column.duckdb_type for column in spec.columns}
        try:
            for batch in _batches(rows, _BATCH_ROWS):
                coerced_batch = []
                for values in batch:
                    coerced = tuple(
                        _coerce_value(v, types.get(name, "VARCHAR"))
                        for v, name in zip(values, spec.column_names())
                    )
                    coerced_batch.append(coerced)
                parameters = [
                    value
                    for values in coerced_batch
                    for value in (*values, row_hash(values))
                ]
                self._duck.execute(
                    spec.insert_batch_sql(len(batch)), parameters
                )
        except duckdb.Error as exc:
            raise AnalyticsError(
                f"the table {spec.name!r} could not be written: {exc}"
            ) from exc
        return len(rows)

    def _delete_missing(
        self, spec: TableSpec, keys: Sequence[Sequence[object]]
    ) -> tuple[int, int]:
        """Remove the rows the database no longer holds.

        It returns how many rows were removed and how many the mirror holds
        afterwards.
        """
        before = self._count(spec.name)
        if before == 0:
            return 0, 0
        types = {column.name: column.duckdb_type for column in spec.columns}
        key_types = ", ".join(
            f'"{name}" {types[name]}' for name in spec.primary_key
        )
        key_columns = ", ".join(f'"{name}"' for name in spec.primary_key)
        self._duck.execute(f'DROP TABLE IF EXISTS temp."{_KEY_TABLE}"')
        self._duck.execute(f'CREATE TEMP TABLE "{_KEY_TABLE}" ({key_types})')
        if keys:
            group = "(" + ", ".join("?" for _ in spec.primary_key) + ")"
            for batch in _batches(keys, _BATCH_ROWS):
                groups = ", ".join(group for _ in batch)
                self._duck.execute(
                    f'INSERT INTO temp."{_KEY_TABLE}" ({key_columns}) VALUES {groups}',
                    [value for key in batch for value in key],
                )
        condition = " AND ".join(
            f'k."{name}" = "{spec.name}"."{name}"' for name in spec.primary_key
        )
        self._duck.execute(
            f'DELETE FROM "{spec.name}" WHERE NOT EXISTS '
            f'(SELECT 1 FROM temp."{_KEY_TABLE}" k WHERE {condition})'
        )
        after = self._count(spec.name)
        return before - after, after

    def _stored_hashes(self, spec: TableSpec) -> dict[str, str]:
        """The hash of every mirrored row, by key."""
        columns = ", ".join(f'"{name}"' for name in spec.primary_key)
        rows = self._duck.execute(
            f'SELECT {columns}, "{spec.hash_column}" FROM "{spec.name}"'
        ).fetchall()
        positions = _key_positions(spec)
        return {
            spec.key_of([row[position] for position in positions]): str(row[-1])
            for row in rows
        }

    def _ensure_state_table(self) -> None:
        """Create the bookkeeping table of the derived state."""
        self._duck.execute(_STATE_DDL)

    def _stored_watermark(self, name: str) -> str | None:
        """The watermark the derived state holds for a table."""
        rows = self._duck.execute(
            f'SELECT watermark FROM "{STATE_TABLE}" WHERE table_name = ?', [name]
        ).fetchall()
        if not rows or rows[0][0] is None:
            return None
        return str(rows[0][0])

    def _store_state(
        self, spec: TableSpec, watermark: str | None, row_count: int
    ) -> None:
        """Record the watermark and the row count of a mirrored table."""
        self._duck.execute(
            f'INSERT OR REPLACE INTO "{STATE_TABLE}" '
            "(table_name, watermark, row_count, ingested_at) VALUES (?, ?, ?, ?)",
            [spec.name, watermark, row_count, utcnow().isoformat()],
        )

    def _watermark_of(
        self, spec: TableSpec, rows: Sequence[Sequence[object]]
    ) -> str | None:
        """The largest watermark value among the rows that were read."""
        column = spec.watermark
        if column is None:
            return None
        index = spec.column_names().index(column)
        values = [str(row[index]) for row in rows if row[index] is not None]
        return max(values) if values else None

    def _new_watermark(
        self,
        spec: TableSpec,
        stored: str | None,
        rows: Sequence[Sequence[object]],
    ) -> str | None:
        """The watermark after a refresh: the largest one seen so far."""
        found = self._watermark_of(spec, rows)
        if stored is None:
            return found
        if found is None:
            return stored
        return max(stored, found)

    def _keys(self, spec: TableSpec, rows: Sequence[Sequence[object]]) -> list[tuple]:
        """The key of every row, in key order."""
        positions = _key_positions(spec)
        return [
            tuple(values[position] for position in positions) for values in rows
        ]

    def _key_of(self, spec: TableSpec, values: Sequence[object]) -> str:
        """The key string of one row."""
        positions = _key_positions(spec)
        return spec.key_of([values[position] for position in positions])

    def _drop_stale_tables(self, specs: Sequence[TableSpec]) -> list[TableReport]:
        """Drop the mirrored tables the database no longer holds."""
        known = {spec.name for spec in specs}
        stale = [name for name in self._table_names() if name not in known]
        if not stale:
            return []
        for view in self._view_names():
            self._duck.execute(f'DROP VIEW IF EXISTS "{view}"')
        reports = []
        for name in stale:
            removed = self._count(name)
            self._duck.execute(f'DROP TABLE "{name}"')
            logger.warning("dropped stale table %s (%d rows)", name, removed)
            reports.append(
                TableReport(
                    name=name,
                    mode="skipped",
                    rows_read=0,
                    rows_written=0,
                    rows_removed=removed,
                    watermark=None,
                )
            )
        return reports

    def _table_names(self) -> tuple[str, ...]:
        """The names of the base tables the derived state holds."""
        rows = self._duck.execute(
            "SELECT table_name FROM duckdb_tables() "
            "WHERE NOT internal AND NOT temporary "
            "AND database_name = current_database() AND table_name <> ? "
            "ORDER BY table_name",
            [STATE_TABLE],
        ).fetchall()
        return tuple(str(row[0]) for row in rows)

    def _view_names(self) -> tuple[str, ...]:
        """The names of the views the derived state holds."""
        rows = self._duck.execute(
            "SELECT view_name FROM duckdb_views() "
            "WHERE NOT internal AND database_name = current_database() "
            "ORDER BY view_name"
        ).fetchall()
        return tuple(str(row[0]) for row in rows)

    def _table_exists(self, name: str) -> bool:
        """Whether the derived state holds the table."""
        rows = self._duck.execute(
            "SELECT table_name FROM duckdb_tables() "
            "WHERE NOT internal AND NOT temporary "
            "AND database_name = current_database() AND table_name = ?",
            [name],
        ).fetchall()
        return bool(rows)

    def _require_view(self, view: str) -> None:
        """Refuse a query whose view is not there, naming the table it needs."""
        rows = self._duck.execute(
            "SELECT view_name FROM duckdb_views() "
            "WHERE NOT internal AND database_name = current_database() "
            "AND view_name = ?",
            [view],
        ).fetchall()
        if rows:
            return
        missing = [
            table
            for table in required_tables(view)
            if not self._table_exists(table)
        ]
        if missing:
            raise AnalyticsError(
                f"the view {view!r} is not there; it needs the table(s): "
                + ", ".join(missing)
            )
        raise AnalyticsError(f"the view {view!r} is not there")

    def _count(self, name: str) -> int:
        """The number of rows a mirrored table holds."""
        rows = self._duck.execute(f'SELECT COUNT(*) FROM "{name}"').fetchall()
        return int(rows[0][0])

    def _rollback(self) -> None:
        """Undo the open transaction, best effort.

        A rollback that fails must not hide the error that caused it, so the
        failure is swallowed: the caller is already raising the real problem.
        """
        try:
            self._duck.execute("ROLLBACK")
        except duckdb.Error:
            pass

    def _as_error(self, spec: TableSpec, exc: BaseException) -> BaseException:
        """The error a failed table raises, naming the table."""
        if isinstance(exc, AnalyticsError):
            return exc
        return AnalyticsError(
            f"the table {spec.name!r} could not be mirrored: {exc}"
        )


def _key_positions(spec: TableSpec) -> tuple[int, ...]:
    """The positions of the key columns inside a row."""
    positions = {column.name: index for index, column in enumerate(spec.columns)}
    return tuple(positions[name] for name in spec.primary_key)


def _types_agree(reported: str, wanted: str) -> bool:
    """Whether the type DuckDB reports means the same as the wanted one.

    The width and the precision are dropped before the comparison, so a
    ``VARCHAR(50)`` and a ``VARCHAR`` agree. A type this layer does not
    write is not a difference either: the table is left alone rather than
    dropped and written again for a name this version of DuckDB chose.
    """
    reported_base = reported.split("(", 1)[0].strip().upper()
    if reported_base == wanted.split("(", 1)[0].strip().upper():
        return True
    return reported_base not in _MIRROR_TYPES


def _batches(
    rows: Sequence[Sequence[object]], size: int
) -> Iterator[Sequence[Sequence[object]]]:
    """The rows in batches of at most size."""
    for start in range(0, len(rows), size):
        yield rows[start : start + size]

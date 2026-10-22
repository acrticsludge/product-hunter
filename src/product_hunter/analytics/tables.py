"""The mirrored-table spec: what a SQLite table becomes in DuckDB.

The analytics layer mirrors the SQLite schema instead of listing it, so a
table a later wave adds is mirrored without an edit here, and a table a
later wave changes shape does not break the layer. This module holds the
rules of the mirror: the type mapping, the timestamp rule, the choice of the
watermark column, the row key and the row hash.

The hash is the link between a derived row and the SQLite row it came from.
It is computed here, on the SQLite side, and stored in the mirrored row,
because DuckDB hands a timestamp back as a datetime and SQLite hands the
same column back as a string: a hash computed on each side would not
agree.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass


class AnalyticsError(Exception):
    """The analytics layer cannot do what it was asked to do."""

# The suffix that makes a column a timestamp.
TIMESTAMP_SUFFIX = "_at"

# The columns that are timestamps even though they do not end in the suffix.
EXTRA_TIMESTAMP_COLUMNS = frozenset({"valid_from", "valid_to"})

# The watermark columns, most preferred first. The first one a table holds is
# the column the incremental read filters on.
WATERMARK_PRIORITY = (
    "updated_at",
    "last_updated",
    "last_seen_at",
    "created_at",
    "recorded_at",
    "observed_at",
    "occurred_at",
    "detected_at",
    "checked_at",
    "attempted_at",
    "started_at",
    "last_request_at",
    "last_fetched_at",
    "valid_from",
    "published_at",
    "fetched_at",
    "selected_at",
    "discovered_at",
    "completed_at",
    "deleted_at",
    "superseded_at",
)

# The column a mirrored table carries the row hash in.
HASH_COLUMN = "_ph_hash"

# The key a table without a declared primary key is read by.
ROWID_KEY = "rowid"


@dataclass(frozen=True)
class ColumnSpec:
    """One column of a mirrored table."""

    name: str
    duckdb_type: str
    is_timestamp: bool


@dataclass(frozen=True)
class TableSpec:
    """One mirrored table: its columns, its key and the rules that apply to it.

    ``mirror_problem`` names why the table cannot be mirrored faithfully.
    It is empty for a table that can.
    """

    name: str
    columns: tuple[ColumnSpec, ...]
    primary_key: tuple[str, ...]
    mirror_problem: str = ""

    @property
    def hash_column(self) -> str:
        """The column the row hash is stored in."""
        return HASH_COLUMN

    @property
    def watermark(self) -> str | None:
        """The column the incremental read filters on, or nothing.

        A table with no watermark column is read in full every time.
        """
        names = self.column_names()
        for candidate in WATERMARK_PRIORITY:
            if candidate in names:
                return candidate
        return None

    def column_names(self) -> tuple[str, ...]:
        """The column names, in the order the table holds them."""
        return tuple(column.name for column in self.columns)

    def key_values(self, values: Sequence[object]) -> tuple[object, ...]:
        """The key values of a row, in key order."""
        positions = {column.name: index for index, column in enumerate(self.columns)}
        return tuple(values[positions[name]] for name in self.primary_key)

    def key_of(self, key_values: Sequence[object]) -> str:
        """The key string of a row that carries only its key values."""
        return json.dumps(list(key_values))

    def create_ddl(self) -> str:
        """The DDL of the mirrored table.

        The key columns are not null, because a row without its key is not a
        row. Every other column keeps the nullability SQLite declared.
        """
        lines = []
        for column in self.columns:
            required = " NOT NULL" if column.name in self.primary_key else ""
            lines.append(f'    "{column.name}" {column.duckdb_type}{required},')
        lines.append(f'    "{self.hash_column}" VARCHAR NOT NULL,')
        keys = ", ".join(f'"{name}"' for name in self.primary_key)
        lines.append(f"    PRIMARY KEY ({keys})")
        body = "\n".join(lines)
        return f'CREATE TABLE IF NOT EXISTS "{self.name}" (\n{body}\n)'

    def insert_sql(self) -> str:
        """The statement that writes a mirrored row, hash included."""
        return self._insert_sql(1)

    def insert_batch_sql(self, rows: int) -> str:
        """The statement that writes a batch of mirrored rows, hash included.

        DuckDB's ``executemany`` costs about a millisecond per row and a
        hundred per statement, so the rows are written in one statement
        instead.
        """
        return self._insert_sql(rows)

    def _insert_sql(self, rows: int) -> str:
        names = (*self.column_names(), self.hash_column)
        columns = ", ".join(f'"{name}"' for name in names)
        group = "(" + ", ".join("?" for _ in names) + ")"
        groups = ", ".join(group for _ in range(rows))
        return f'INSERT OR REPLACE INTO "{self.name}" ({columns}) VALUES {groups}'


def is_timestamp_column(name: str) -> bool:
    """Whether a column holds a timestamp."""
    return name.endswith(TIMESTAMP_SUFFIX) or name in EXTRA_TIMESTAMP_COLUMNS


def duckdb_type(declared: str) -> str:
    """The DuckDB type a declared SQLite type is mirrored as."""
    kind = declared.strip().upper()
    if kind.startswith("INT"):
        return "BIGINT"
    if kind.startswith(("REAL", "FLOA", "DOUB")):
        return "DOUBLE"
    if kind.startswith("BLOB"):
        return "BLOB"
    return "VARCHAR"


def _valid_identifier(name: str) -> bool:
    """Whether a name is safe to interpolate into a SQL statement.

    The names come from SQLite's own catalog, so a name that fails this
    check means the catalog itself is unusable, not that an attacker got
    in. The check is defense in depth: it keeps a strange name from
    reaching the SQL text.
    """
    return re.fullmatch(r"[a-z_][a-z0-9_]*", name) is not None


def discover(conn: sqlite3.Connection) -> tuple[TableSpec, ...]:
    """The specs of every user table in the database.

    The tables are read from the schema rather than listed, so a table a
    later wave adds is mirrored without an edit here. The internal SQLite
    tables are skipped.
    """
    rows = conn.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()
    names = [str(row["name"]) for row in rows]
    for name in names:
        if not _valid_identifier(name):
            raise AnalyticsError(f"the table name is not a safe identifier: {name!r}")
    return tuple(_spec(conn, name) for name in names)


def row_hash(values: Sequence[object]) -> str:
    """The SHA-256 of the canonical form of a row.

    The canonical form is the JSON of the values in column order, with null
    for a missing value and a hex string for bytes, so the same row hashes
    the same way in every run and in every process.
    """
    canonical = [_canonical(value) for value in values]
    return hashlib.sha256(json.dumps(canonical).encode("utf-8")).hexdigest()


def row_key(spec: TableSpec, values: Sequence[object]) -> str:
    """The key of a row: the JSON of its key values, in key order.

    A composite key is one string, so the key of a row is one value whether
    the table has one key column or three.
    """
    return spec.key_of(spec.key_values(values))


def _spec(conn: sqlite3.Connection, table: str) -> TableSpec:
    """The spec of one table, read from its declared schema."""
    info = conn.execute(f'PRAGMA table_info("{table}")').fetchall()
    columns: list[ColumnSpec] = []
    keys: list[tuple[int, str]] = []
    for row in info:
        name = str(row["name"])
        if not _valid_identifier(name):
            raise AnalyticsError(
                f"the column name is not a safe identifier: {name!r}"
            )
        timestamp = is_timestamp_column(name)
        columns.append(
            ColumnSpec(
                name=name,
                duckdb_type=(
                    "TIMESTAMP" if timestamp else duckdb_type(str(row["type"] or ""))
                ),
                is_timestamp=timestamp,
            )
        )
        if row["pk"]:
            keys.append((int(row["pk"]), name))
    primary_key = tuple(name for _, name in sorted(keys))
    if not primary_key:
        # A table without a declared key is read by its rowid, which SQLite
        # keeps for every row of an ordinary table.
        if ROWID_KEY not in {column.name for column in columns}:
            columns.append(ColumnSpec(ROWID_KEY, "BIGINT", False))
        primary_key = (ROWID_KEY,)
    return TableSpec(
        name=table,
        columns=tuple(columns),
        primary_key=primary_key,
        mirror_problem=_key_problem(info),
    )


def _key_problem(info: Sequence[sqlite3.Row]) -> str:
    """Why a table cannot be mirrored faithfully, or nothing.

    SQLite lets a key column hold null unless the column is declared not
    null, and DuckDB refuses a null in a key column of any kind. A table
    whose key can hold null therefore has rows the mirror cannot hold, and
    the table is refused rather than half mirrored.
    """
    for row in info:
        if not row["pk"]:
            continue
        declared = str(row["type"] or "").strip().upper()
        if row["notnull"] or declared == "INTEGER":
            # An integer primary key is the rowid, which SQLite fills in.
            continue
        return (
            f"the key column {str(row['name'])!r} can hold null, and DuckDB "
            "refuses a null in a key column"
        )
    return ""


def _canonical(value: object) -> object:
    """The canonical JSON form of one value."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (bytes, bytearray, memoryview)):
        return {"__bytes__": bytes(value).hex()}
    return str(value)

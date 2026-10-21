from __future__ import annotations

import re
import sqlite3
from pathlib import Path

MIGRATIONS_DIR = Path(__file__).resolve().parents[3] / "db" / "migrations"

_MIGRATION_RE = re.compile(r"^(\d+)_.+\.sql$")


class MigrationError(Exception):
    """Raised when migrations cannot be listed or applied safely."""


def current_version(conn: sqlite3.Connection) -> int:
    """Return the schema version stored in ``PRAGMA user_version``."""
    return int(conn.execute("PRAGMA user_version").fetchone()[0])


def migrate(conn: sqlite3.Connection, *, migrations_dir: Path = MIGRATIONS_DIR) -> int:
    """Apply every pending migration file and return the resulting version.

    Each file is applied in a single transaction: on failure the schema and
    ``user_version`` are left exactly as they were.
    """
    migrations = _discover(Path(migrations_dir))
    version = current_version(conn)
    latest = migrations[-1][0]
    if version > latest:
        raise MigrationError(
            f"database version {version} is newer than the latest migration {latest}"
        )
    for number, path in migrations:
        if number > version:
            _apply(conn, number, path)
    return current_version(conn)


def _discover(migrations_dir: Path) -> list[tuple[int, Path]]:
    if not migrations_dir.is_dir():
        raise MigrationError(f"migrations directory not found: {migrations_dir}")
    found: dict[int, Path] = {}
    for path in sorted(migrations_dir.iterdir()):
        if not path.is_file():
            continue
        match = _MIGRATION_RE.match(path.name)
        if not match:
            continue
        number = int(match.group(1))
        if number in found:
            raise MigrationError(
                f"duplicate migration number {number}: "
                f"{found[number].name} and {path.name}"
            )
        found[number] = path
    if not found:
        raise MigrationError(f"no migration files found in {migrations_dir}")
    numbers = sorted(found)
    if numbers[0] != 1:
        raise MigrationError(f"first migration must be numbered 0001, found {numbers[0]}")
    for previous, number in zip(numbers, numbers[1:]):
        if number != previous + 1:
            raise MigrationError(
                f"gap in migration numbers between {previous} and {number}"
            )
    return [(number, found[number]) for number in numbers]


def _apply(conn: sqlite3.Connection, number: int, path: Path) -> None:
    statements = _split_statements(path.read_text(encoding="utf-8"))
    statement = "<begin>"
    try:
        conn.execute("BEGIN IMMEDIATE")
        for statement in statements:
            conn.execute(statement)
        conn.execute(f"PRAGMA user_version = {number}")
        conn.execute("COMMIT")
    except BaseException as exc:
        _rollback(conn)
        if isinstance(exc, sqlite3.Error):
            raise MigrationError(
                f"{path.name} failed on statement {statement!r}: {exc}"
            ) from exc
        raise


def _rollback(conn: sqlite3.Connection) -> None:
    try:
        conn.execute("ROLLBACK")
    except sqlite3.Error:
        pass


def _split_statements(sql: str) -> list[str]:
    # shortcut: statements are split on ';' and full-line '--' comments dropped,
    # so a semicolon inside a string literal is not supported.
    body = _strip_comment_lines(sql)
    statements: list[str] = []
    for chunk in body.split(";"):
        statement = _strip_comment_lines(chunk).strip()
        if statement:
            statements.append(statement)
    return statements


def _strip_comment_lines(sql: str) -> str:
    return "\n".join(
        line for line in sql.splitlines() if not line.strip().startswith("--")
    )

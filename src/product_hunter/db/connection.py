from __future__ import annotations

import sqlite3
from pathlib import Path

_MEMORY = ":memory:"


def connect(db_path: str | Path, *, read_only: bool = False) -> sqlite3.Connection:
    """Open a SQLite connection with the project pragmas applied.

    ``:memory:`` stays in memory. A file path creates its parent directory.
    Read-only connections open the existing file with a ``file:...?mode=ro``
    URI and never create it.
    """
    raw = str(db_path)
    if raw == _MEMORY:
        conn = sqlite3.connect(_MEMORY, isolation_level=None, check_same_thread=True)
    else:
        path = Path(raw).expanduser()
        if read_only:
            if not path.is_file():
                raise FileNotFoundError(f"database does not exist: {path}")
            uri = path.resolve().as_uri() + "?mode=ro"
            conn = sqlite3.connect(uri, uri=True, isolation_level=None, check_same_thread=True)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(str(path), isolation_level=None, check_same_thread=True)
    conn.row_factory = sqlite3.Row
    _apply_pragmas(conn, wal=not read_only and raw != _MEMORY)
    return conn


def _apply_pragmas(conn: sqlite3.Connection, *, wal: bool) -> None:
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute("PRAGMA synchronous = NORMAL")
    if wal:
        # An in-memory database has no journal to switch; WAL applies to files.
        conn.execute("PRAGMA journal_mode = WAL")

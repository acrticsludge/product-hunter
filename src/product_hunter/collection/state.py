"""The durable state of the collection layer: what a run spent and what it got.

The tables are the record. There is no health table: the health report is
derived from the attempts, so it cannot drift from the records it summarises.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field, replace

# The outcomes that mean a request completed. The mean latency is taken over
# these, because the other attempts never reached the server.
COMPLETED_OUTCOMES = ("fetched", "not_modified")

# The outcomes that mean the source let the run down.
FAILURE_OUTCOMES = ("rejected", "failed")


@dataclass(frozen=True)
class SourceState:
    """The validators, the cursor and the last outcome of one target.

    The validators outlive a run on purpose: a later run sends them back and
    the server answers 304 when nothing changed.
    """

    source_id: str
    url: str
    cursor: str | None = None
    etag: str | None = None
    last_modified: str | None = None
    last_status: int | None = None
    last_fetch_id: str | None = None
    last_fetched_at: str | None = None
    content_sha256: str | None = None
    updated_at: str = ""


@dataclass(frozen=True)
class RunState:
    """The counters of one ``(scope, source_id)``."""

    run_id: str
    source_id: str
    request_count: int = 0
    fetch_count: int = 0
    last_request_at: str | None = None


@dataclass(frozen=True)
class CollectionItem:
    """One attempt at one target in one run.

    The row carries hashes, paths, status codes and reason codes. It never
    carries page text: the body stays in the raw store, addressed by hash.
    """

    run_id: str
    source_id: str
    url: str
    outcome: str
    attempt_no: int = 1
    reason_code: str = ""
    detail: str = ""
    rung: str = ""
    terms_status: str = ""
    allowed_use: list[str] = field(default_factory=list)
    review_basis: str = ""
    fetch_id: str | None = None
    status: int | None = None
    bytes: int = 0
    raw_sha256: str | None = None
    raw_path: str | None = None
    latency_ms: int = 0
    retry_count: int = 0
    attempted_at: str = ""


@dataclass(frozen=True)
class SourceHealth:
    """The report for one source, across every run."""

    source_id: str
    attempts: int = 0
    outcomes: dict[str, int] = field(default_factory=dict)
    failures: dict[str, int] = field(default_factory=dict)
    last_success_at: str | None = None
    last_failure_at: str | None = None
    consecutive_failures: int = 0
    mean_latency_ms: float = 0.0


class CollectionState:
    """The reads and the writes of the collection tables."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    # --- the state of one target -------------------------------------------

    def source_state(self, source_id: str, url: str) -> SourceState | None:
        row = self.conn.execute(
            "SELECT * FROM collection_source_state WHERE source_id = ? AND url = ?",
            (source_id, url),
        ).fetchone()
        return _row_to_source_state(row) if row is not None else None

    def save_source_state(self, state: SourceState) -> None:
        self.conn.execute(
            """
            INSERT INTO collection_source_state (
                source_id, url, cursor, etag, last_modified, last_status,
                last_fetch_id, last_fetched_at, content_sha256, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source_id, url) DO UPDATE SET
                etag = excluded.etag,
                last_modified = excluded.last_modified,
                last_status = excluded.last_status,
                last_fetch_id = excluded.last_fetch_id,
                last_fetched_at = excluded.last_fetched_at,
                content_sha256 = excluded.content_sha256,
                updated_at = excluded.updated_at,
                cursor = COALESCE(excluded.cursor, collection_source_state.cursor)
            """,
            (
                state.source_id,
                state.url,
                state.cursor,
                state.etag,
                state.last_modified,
                state.last_status,
                state.last_fetch_id,
                state.last_fetched_at,
                state.content_sha256,
                state.updated_at,
            ),
        )

    # --- the ledger of one run ---------------------------------------------

    def run_state(self, run_id: str, source_id: str) -> RunState:
        row = self.conn.execute(
            "SELECT * FROM collection_run_state WHERE run_id = ? AND source_id = ?",
            (run_id, source_id),
        ).fetchone()
        if row is None:
            return RunState(run_id=run_id, source_id=source_id)
        return _row_to_run_state(row)

    def run_states(self, run_id: str) -> list[RunState]:
        rows = self.conn.execute(
            "SELECT * FROM collection_run_state WHERE run_id = ? ORDER BY source_id",
            (run_id,),
        ).fetchall()
        return [_row_to_run_state(row) for row in rows]

    def add_request(self, run_id: str, source_id: str, *, at: str) -> RunState:
        """Record one HTTP request spent against the source in this scope.

        The count is incremented by one, atomically, so a worker that writes
        its own total cannot overwrite what another worker spent.
        """
        self.conn.execute(
            """
            INSERT INTO collection_run_state (
                run_id, source_id, request_count, last_request_at
            ) VALUES (?, ?, 1, ?)
            ON CONFLICT(run_id, source_id) DO UPDATE SET
                request_count = request_count + 1,
                last_request_at = excluded.last_request_at
            """,
            (run_id, source_id, at),
        )
        return self.run_state(run_id, source_id)

    def add_fetch(self, run_id: str, source_id: str, *, at: str) -> RunState:
        """Record one fetch the run spent against the source.

        An attempt that went out is a fetch the run spent, so the ledger
        counts it: the counter the contract's tracker is rebuilt from has to
        agree with what the run actually spent. A refusal that stopped the
        request before it went out is not charged, and a skip is not a
        fetch.
        """
        self.conn.execute(
            """
            INSERT INTO collection_run_state (run_id, source_id, fetch_count)
            VALUES (?, ?, 1)
            ON CONFLICT(run_id, source_id) DO UPDATE SET
                fetch_count = fetch_count + 1
            """,
            (run_id, source_id),
        )
        return self.run_state(run_id, source_id)

    def request_counts(self, run_id: str) -> dict[str, int]:
        """The HTTP requests spent per source in this run."""
        rows = self.conn.execute(
            "SELECT source_id, request_count FROM collection_run_state "
            "WHERE run_id = ?",
            (run_id,),
        ).fetchall()
        return {row["source_id"]: int(row["request_count"]) for row in rows}

    # --- the attempts ------------------------------------------------------

    def item(self, run_id: str, source_id: str, url: str) -> CollectionItem | None:
        """The latest attempt at the target, or None when there is none."""
        row = self.conn.execute(
            "SELECT * FROM collection_items "
            "WHERE run_id = ? AND source_id = ? AND url = ? "
            "ORDER BY attempt_no DESC LIMIT 1",
            (run_id, source_id, url),
        ).fetchone()
        return _row_to_item(row) if row is not None else None

    def next_attempt_no(self, run_id: str, source_id: str, url: str) -> int:
        """The attempt number the next attempt at the target carries."""
        row = self.conn.execute(
            "SELECT COALESCE(MAX(attempt_no), 0) FROM collection_items "
            "WHERE run_id = ? AND source_id = ? AND url = ?",
            (run_id, source_id, url),
        ).fetchone()
        return int(row[0]) + 1

    def save_item(self, item: CollectionItem) -> CollectionItem:
        """Write one row per attempt, and return the row that was written.

        The attempt number is one above the last one for the target, so a
        retry does not erase the attempt it follows.
        """
        attempt_no = self.next_attempt_no(item.run_id, item.source_id, item.url)
        written = replace(item, attempt_no=attempt_no)
        self.conn.execute(
            """
            INSERT INTO collection_items (
                run_id, source_id, url, attempt_no, outcome, reason_code, detail,
                rung, terms_status, allowed_use_json, review_basis, fetch_id,
                status, bytes, raw_sha256, raw_path, latency_ms, retry_count,
                attempted_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                written.run_id,
                written.source_id,
                written.url,
                written.attempt_no,
                written.outcome,
                written.reason_code,
                written.detail,
                written.rung,
                written.terms_status,
                json.dumps(written.allowed_use),
                written.review_basis,
                written.fetch_id,
                written.status,
                written.bytes,
                written.raw_sha256,
                written.raw_path,
                written.latency_ms,
                written.retry_count,
                written.attempted_at,
            ),
        )
        return written


def source_health(conn: sqlite3.Connection) -> dict[str, SourceHealth]:
    """The health report for every source, derived from the attempts.

    The numbers come from ``collection_items`` and nothing else, so the report
    cannot drift from the records it summarises.
    """
    rows = conn.execute(
        "SELECT source_id, outcome, reason_code, latency_ms, attempted_at "
        "FROM collection_items ORDER BY attempted_at, rowid"
    ).fetchall()
    grouped: dict[str, list[sqlite3.Row]] = {}
    for row in rows:
        grouped.setdefault(row["source_id"], []).append(row)
    return {
        source_id: _health(source_id, source_rows)
        for source_id, source_rows in grouped.items()
    }


def _health(source_id: str, rows: list[sqlite3.Row]) -> SourceHealth:
    outcomes: dict[str, int] = {}
    failures: dict[str, int] = {}
    latencies: list[int] = []
    last_success: str | None = None
    last_failure: str | None = None
    for row in rows:
        outcome = row["outcome"]
        outcomes[outcome] = outcomes.get(outcome, 0) + 1
        if outcome in FAILURE_OUTCOMES:
            code = row["reason_code"] or outcome
            failures[code] = failures.get(code, 0) + 1
            last_failure = row["attempted_at"]
        elif outcome in COMPLETED_OUTCOMES:
            last_success = row["attempted_at"]
            latencies.append(int(row["latency_ms"]))
    consecutive = 0
    for row in reversed(rows):
        if row["outcome"] in FAILURE_OUTCOMES:
            consecutive += 1
        else:
            break
    return SourceHealth(
        source_id=source_id,
        attempts=len(rows),
        outcomes=outcomes,
        failures=failures,
        last_success_at=last_success,
        last_failure_at=last_failure,
        consecutive_failures=consecutive,
        mean_latency_ms=(sum(latencies) / len(latencies)) if latencies else 0.0,
    )


def _row_to_source_state(row: sqlite3.Row) -> SourceState:
    return SourceState(
        source_id=row["source_id"],
        url=row["url"],
        cursor=row["cursor"],
        etag=row["etag"],
        last_modified=row["last_modified"],
        last_status=row["last_status"],
        last_fetch_id=row["last_fetch_id"],
        last_fetched_at=row["last_fetched_at"],
        content_sha256=row["content_sha256"],
        updated_at=row["updated_at"],
    )


def _row_to_run_state(row: sqlite3.Row) -> RunState:
    return RunState(
        run_id=row["run_id"],
        source_id=row["source_id"],
        request_count=int(row["request_count"]),
        fetch_count=int(row["fetch_count"]),
        last_request_at=row["last_request_at"],
    )


def _row_to_item(row: sqlite3.Row) -> CollectionItem:
    return CollectionItem(
        run_id=row["run_id"],
        source_id=row["source_id"],
        url=row["url"],
        outcome=row["outcome"],
        attempt_no=int(row["attempt_no"]),
        reason_code=row["reason_code"],
        detail=row["detail"],
        rung=row["rung"],
        terms_status=row["terms_status"],
        allowed_use=json.loads(row["allowed_use_json"] or "[]"),
        review_basis=row["review_basis"],
        fetch_id=row["fetch_id"],
        status=row["status"],
        bytes=int(row["bytes"]),
        raw_sha256=row["raw_sha256"],
        raw_path=row["raw_path"],
        latency_ms=int(row["latency_ms"]),
        retry_count=int(row["retry_count"]),
        attempted_at=row["attempted_at"],
    )

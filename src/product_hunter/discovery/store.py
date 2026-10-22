"""Persistence for the discovery records and the frontier state.

A query execution is a ``research_events`` row, the cost of a search is a
``spend_ledger`` row, and the frontier state is a ``research_checkpoints``
row. No migration is added: the tables the repository already has are the
ones the frontier writes to.

This module owns its own event insert, because the two event writers that
exist are private to the modules that own them.
"""

from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager

from product_hunter.db.checkpoints import (
    Checkpoint,
    list_checkpoints,
    load_checkpoint,
    save_checkpoint,
)
from product_hunter.db.store import StoreConflict
from product_hunter.discovery.budget import DISCOVERY_STAGE
from product_hunter.discovery.frontier import QueryFrontier
from product_hunter.discovery.records import DiscoveryRecord
from product_hunter.research.budget import BudgetTracker

# The event type one discovery query execution is filed under.
EVENT_TYPE = "DISCOVERY_QUERY_EXECUTED"

# The default step the frontier checkpoint is filed under.
DEFAULT_STEP = "frontier"

# The unit a spend row counts: one search query.
UNIT_TYPE = "search_queries"

# A dollar is a million millionths, so the cost of a search is written in
# millionths of a dollar.
_MICROS_PER_DOLLAR = 1_000_000


def _hash_id(prefix: str, basis: str) -> str:
    """Return the identifier of the house pattern: a prefix and sixteen hex."""
    return prefix + hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16]


def event_id(record: DiscoveryRecord) -> str:
    """Return the event identifier of a record.

    The basis is the run id, the event type, the query id and the
    timestamp, so a replay of the same record produces the same identifier.
    """
    return _hash_id(
        "evt_",
        f"{record.run_id}|{EVENT_TYPE}|{record.query_id}|{record.timestamp.isoformat()}",
    )


def spend_entry_id(record: DiscoveryRecord) -> str:
    """Return the spend ledger identifier of a record."""
    return _hash_id("spn_", f"{record.run_id}|{record.record_id}")


def write_record(conn: sqlite3.Connection, record: DiscoveryRecord) -> str:
    """Write one discovery record and its spend, and return the event id.

    The event and the spend are written under one savepoint, so a failure
    cannot leave the event without its spend row. Writing the same record
    twice is a no-op: the identifiers are derived from the record, so a
    replay lands on the same rows, and a replay repairs a spend row that
    is missing.
    """
    payload = record.model_dump_json()
    identifier = event_id(record)
    with _savepoint(conn):
        existing = conn.execute(
            "SELECT payload_json FROM research_events WHERE event_id = ?",
            (identifier,),
        ).fetchone()
        if existing is not None:
            if existing["payload_json"] != payload:
                raise StoreConflict(
                    f"event {identifier!r} already exists with a different payload"
                )
            _write_spend(conn, record)
            return identifier
        created_at = record.timestamp.isoformat()
        conn.execute(
            """
            INSERT INTO research_events (
                event_id, run_id, event_type, stage, tool, agent,
                payload_ref, payload_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, NULL, ?, ?)
            """,
            (
                identifier,
                record.run_id,
                EVENT_TYPE,
                record.stage,
                record.tool,
                record.agent,
                payload,
                created_at,
            ),
        )
        _write_spend(conn, record)
    return identifier


def _write_spend(conn: sqlite3.Connection, record: DiscoveryRecord) -> None:
    """Write the spend row of a record, if it is not there already."""
    conn.execute(
        """
        INSERT OR IGNORE INTO spend_ledger (
            entry_id, run_id, stage, tool, provider, model,
            units, unit_type, cost_micros, created_at
        ) VALUES (?, ?, ?, ?, NULL, NULL, ?, ?, ?, ?)
        """,
        (
            spend_entry_id(record),
            record.run_id,
            record.stage,
            record.tool,
            1,
            UNIT_TYPE,
            round(record.cost * _MICROS_PER_DOLLAR),
            record.timestamp.isoformat(),
        ),
    )


@contextmanager
def _savepoint(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """One unit of work. A failure undoes it and nothing else.

    A savepoint is the right tool here because the caller may already own
    a transaction. A rollback to the savepoint undoes the event insert and
    leaves the caller's outer transaction exactly as it was, so the caller
    can commit the rest of its work or roll it back itself. A connection
    that is free gets the savepoint's own transaction, and the release
    commits it.
    """
    conn.execute("SAVEPOINT discovery_record")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK TO discovery_record")
        conn.execute("RELEASE discovery_record")
        raise
    conn.execute("RELEASE discovery_record")


def list_records(conn: sqlite3.Connection, run_id: str) -> list[DiscoveryRecord]:
    """Return the discovery records of a run, oldest first."""
    rows = conn.execute(
        "SELECT payload_json FROM research_events "
        "WHERE run_id = ? AND event_type = ? ORDER BY created_at, rowid",
        (run_id, EVENT_TYPE),
    ).fetchall()
    return [DiscoveryRecord.model_validate_json(row["payload_json"]) for row in rows]


def save_frontier(
    conn: sqlite3.Connection,
    run_id: str,
    frontier: QueryFrontier,
    *,
    version: int | None = None,
    step: str = DEFAULT_STEP,
) -> Checkpoint:
    """Write the frontier state through the checkpoint container.

    The frontier carries its own checkpoint version: the first save writes
    version one, and the version advances after every successful save, so
    a changed state never lands on a version that already holds another
    state. An explicit version is written as it was given, and the counter
    still advances, so the next automatic save lands on a free version.

    The state records the version the row is written at, so a restore
    continues after that row and not after the version the frontier
    happened to hold.
    """
    checkpoint_version = frontier.checkpoint_version if version is None else version
    checkpoint = save_checkpoint(
        conn,
        run_id=run_id,
        stage=DISCOVERY_STAGE,
        state=frontier.checkpoint_state(checkpoint_version),
        step=step,
        checkpoint_version=checkpoint_version,
    )
    frontier.checkpoint_version = checkpoint_version + 1
    return checkpoint


def load_frontier(
    conn: sqlite3.Connection,
    run_id: str,
    *,
    step: str = DEFAULT_STEP,
    version: int | None = None,
    tracker: BudgetTracker | None = None,
) -> QueryFrontier | None:
    """Read the frontier state back and rebuild the frontier.

    The contract tracker is not part of the checkpoint, so the caller hands
    the tracker back and the contract's limits still bind. The budget's own
    counters come from the checkpoint, so a resume never forgets what the
    run already spent.

    The next write lands on the version after the newest checkpoint of the
    run, whatever version the state itself happens to record, so a restore
    never writes over a state that is already there.
    """
    checkpoint = load_checkpoint(
        conn,
        run_id=run_id,
        stage=DISCOVERY_STAGE,
        step=step,
        version=version,
    )
    if checkpoint is None:
        return None
    frontier = QueryFrontier.from_state(checkpoint.state, tracker=tracker)
    frontier.checkpoint_version = _newest_version(conn, run_id, step) + 1
    return frontier


def _newest_version(conn: sqlite3.Connection, run_id: str, step: str) -> int:
    """Return the highest checkpoint version the run holds for this step."""
    return max(
        (
            checkpoint.checkpoint_version
            for checkpoint in list_checkpoints(conn, run_id=run_id, skip_invalid=True)
            if checkpoint.stage == DISCOVERY_STAGE
            and (checkpoint.step or "") == (step or "")
        ),
        default=0,
    )

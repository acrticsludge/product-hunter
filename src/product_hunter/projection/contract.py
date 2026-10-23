"""The tables the projection reads, and the DDL the wave that owns them lands.

The projection never writes to the database, so it holds its read contract
here rather than in a migration. A wave that owns a table checks this map
against what it lands, and a table that arrives with a different shape
degrades the projection instead of breaking it: the reader asks for the
columns that exist and ignores the ones that do not.
"""

from __future__ import annotations

import re
import sqlite3

CONTRACT_VERSION = "projection-v1"

# The DDL of the tables this projection expects. The wave that owns the
# scoring and experiment tables lands it; the test suite applies it to a
# temporary database, which pins the contract.
CANONICAL_DDL = """\
CREATE TABLE IF NOT EXISTS opportunities (
    opportunity_id         TEXT PRIMARY KEY NOT NULL,
    title                  TEXT NOT NULL,
    normalized_problem     TEXT,
    domain                 TEXT,
    actor                  TEXT,
    action                 TEXT,
    object                 TEXT,
    consequence            TEXT,
    severity               REAL,
    frequency              TEXT,
    wtp_tier               TEXT,
    competition            REAL,
    potential              REAL,
    confidence             REAL,
    trend                  TEXT,
    uncertainty            TEXT,
    lifecycle              TEXT,
    recommended_experiment TEXT,
    first_seen             TEXT,
    last_updated           TEXT,
    created_at             TEXT NOT NULL,
    updated_at             TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS opportunity_scores (
    score_id       TEXT PRIMARY KEY NOT NULL,
    opportunity_id TEXT NOT NULL,
    potential      REAL,
    confidence     REAL,
    created_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS opportunity_evidence (
    opportunity_id TEXT NOT NULL,
    span_id        TEXT NOT NULL,
    stance         TEXT NOT NULL,
    created_at     TEXT NOT NULL,
    PRIMARY KEY (opportunity_id, span_id, stance)
);

CREATE TABLE IF NOT EXISTS opportunity_claims (
    claim_id       TEXT PRIMARY KEY NOT NULL,
    opportunity_id TEXT NOT NULL,
    statement      TEXT NOT NULL,
    kind           TEXT,
    span_id        TEXT,
    created_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS experiments (
    experiment_id  TEXT PRIMARY KEY NOT NULL,
    opportunity_id TEXT,
    title          TEXT NOT NULL,
    hypothesis     TEXT,
    status         TEXT NOT NULL,
    started_at     TEXT,
    completed_at   TEXT,
    result         TEXT
);

CREATE TABLE IF NOT EXISTS research_requests (
    request_id TEXT PRIMARY KEY NOT NULL,
    title      TEXT NOT NULL,
    body       TEXT,
    status     TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""

# The lifecycle vocabulary the projection knows. A decision text outside it
# leaves the lifecycle at CANDIDATE rather than being guessed at.
LIFECYCLES: tuple[str, ...] = (
    "CANDIDATE",
    "PROMISING",
    "HUMAN_REVIEW",
    "ALREADY_SOLVED",
    "REJECTED",
    "INSUFFICIENT_EVIDENCE",
    "STALE",
    "DEAD",
)

# The columns the reader wants from each table. A column that is not in the
# table is not read, and the field it would have filled is reported as not
# recorded.
COLUMN_MAP: dict[str, tuple[str, ...]] = {
    "sources": ("source_id", "source_type", "terms_status", "enabled"),
    "documents": (
        "doc_id",
        "source_id",
        "community_id",
        "url",
        "title",
        "author",
        "text_sha256",
        "published_at",
        "live_status",
        "created_at",
    ),
    "communities": ("community_id", "source_id", "name", "url"),
    "authors": ("author_id", "source_id", "handle", "display_name"),
    "author_links": ("link_id", "author_id", "doc_id", "confidence", "basis"),
    "evidence": (
        "span_id",
        "doc_id",
        "quote",
        "match_mode",
        "live_status",
        "created_at",
    ),
    "pain_points": (
        "pain_id",
        "span_id",
        "doc_id",
        "domain",
        "actor",
        "action",
        "object",
        "consequence",
        "severity",
        "frequency",
        "pain_statement",
        "normalized_key",
        "created_at",
    ),
    "evidence_pain_links": ("pain_id", "span_id", "created_at"),
    "propagation_edges": (
        "edge_id",
        "from_doc_id",
        "to_doc_id",
        "relation",
        "confidence",
    ),
    "human_decisions": (
        "decision_id",
        "entity_type",
        "entity_id",
        "decision",
        "rationale",
        "created_at",
    ),
    "opportunities": (
        "opportunity_id",
        "title",
        "normalized_problem",
        "domain",
        "actor",
        "action",
        "object",
        "consequence",
        "severity",
        "frequency",
        "wtp_tier",
        "competition",
        "potential",
        "confidence",
        "trend",
        "uncertainty",
        "lifecycle",
        "recommended_experiment",
        "first_seen",
        "last_updated",
        "created_at",
        "updated_at",
    ),
    "opportunity_scores": (
        "score_id",
        "opportunity_id",
        "potential",
        "confidence",
        "created_at",
    ),
    "opportunity_evidence": ("opportunity_id", "span_id", "stance", "created_at"),
    "opportunity_claims": (
        "claim_id",
        "opportunity_id",
        "statement",
        "kind",
        "span_id",
        "created_at",
    ),
    "experiments": (
        "experiment_id",
        "opportunity_id",
        "title",
        "hypothesis",
        "status",
        "started_at",
        "completed_at",
        "result",
    ),
    "research_requests": ("request_id", "title", "body", "status", "created_at"),
}

# A table name reaches the SQL text, so it is checked before it is used.
_TABLE_NAME = re.compile(r"^[a-z_]+$")


def table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    """The columns a table has, or an empty set when it has no such table."""
    if not _TABLE_NAME.match(table):
        return set()
    try:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    except sqlite3.Error:
        return set()
    return {str(row["name"]) for row in rows}


def has_table(conn: sqlite3.Connection, table: str) -> bool:
    """Whether the database holds the table."""
    return bool(table_columns(conn, table))


def select_columns(
    conn: sqlite3.Connection, table: str
) -> list[dict[str, object]]:
    """Every row of a table, read through the columns it actually has.

    A table that is not there returns nothing, and a table with a different
    shape returns the columns it shares with the contract.
    """
    wanted = COLUMN_MAP.get(table, ())
    available = table_columns(conn, table)
    if not available or not wanted:
        return []
    chosen = [column for column in wanted if column in available]
    if not chosen:
        return []
    sql = "SELECT " + ", ".join(chosen) + " FROM " + table
    try:
        rows = conn.execute(sql).fetchall()
    except sqlite3.Error:
        return []
    return [dict(row) for row in rows]

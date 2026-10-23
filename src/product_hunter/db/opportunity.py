"""The opportunity store: the rows and the history of them.

The store owns every write. It never deletes an opportunity row, a score row,
an evidence link or a claim: a score is appended, and a lifecycle change is
recorded as a new row in the opportunity's history through the caller.

The handler proposes; the store decides nothing. It persists what it is given
and refuses a write that breaks a foreign key or a primary key.
"""

from __future__ import annotations

import hashlib
import sqlite3
import time
from collections.abc import Sequence
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class OpportunityStoreError(Exception):
    """The store could not accept the input."""

    reason_code = "opportunity_store_error"

    def __init__(self, detail: str = "") -> None:
        self.detail = detail
        super().__init__(detail or self.reason_code)


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class OpportunityRecord(BaseModel):
    """One opportunity row, as it was written."""

    model_config = ConfigDict(extra="forbid")

    opportunity_id: str = Field(min_length=1)
    run_id: str | None = None
    cluster_id: str | None = None
    title: str = Field(min_length=1)
    normalized_problem: str | None = None
    domain: str | None = None
    actor: str | None = None
    action: str | None = None
    object: str | None = None
    consequence: str | None = None
    severity: float | None = None
    frequency: str | None = None
    wtp_tier: str | None = None
    competition: float | None = None
    potential: float | None = None
    confidence: float | None = None
    trend: str | None = None
    uncertainty: str | None = None
    lifecycle: str = "CANDIDATE"
    recommended_experiment: str | None = None
    first_seen: str | None = None
    last_updated: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


class OpportunityStore:
    """The SQLite state of the opportunities: the rows and the links."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def upsert(self, record: OpportunityRecord) -> str:
        """Write one opportunity row. Returns the opportunity id.

        A row written twice keeps its first row unless the lifecycle or the
        score moved, in which case the row is updated. The primary key is the
        opportunity id, so a second write with the same id updates the
        mutable fields and leaves the immutable ones alone.
        """
        now = record.created_at or _now()
        updated = record.updated_at or now
        self.conn.execute(
            """
            INSERT INTO opportunities (
                opportunity_id, run_id, cluster_id, title, normalized_problem,
                domain, actor, action, object, consequence, severity, frequency,
                wtp_tier, competition, potential, confidence, trend, uncertainty,
                lifecycle, recommended_experiment, first_seen, last_updated,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(opportunity_id) DO UPDATE SET
                title = excluded.title,
                normalized_problem = excluded.normalized_problem,
                domain = excluded.domain,
                actor = excluded.actor,
                action = excluded.action,
                object = excluded.object,
                consequence = excluded.consequence,
                severity = excluded.severity,
                frequency = excluded.frequency,
                wtp_tier = excluded.wtp_tier,
                competition = excluded.competition,
                potential = excluded.potential,
                confidence = excluded.confidence,
                trend = excluded.trend,
                uncertainty = excluded.uncertainty,
                lifecycle = excluded.lifecycle,
                recommended_experiment = excluded.recommended_experiment,
                last_updated = excluded.last_updated,
                updated_at = excluded.updated_at
            """,
            (
                record.opportunity_id,
                record.run_id,
                record.cluster_id,
                record.title,
                record.normalized_problem,
                record.domain,
                record.actor,
                record.action,
                record.object,
                record.consequence,
                record.severity,
                record.frequency,
                record.wtp_tier,
                record.competition,
                record.potential,
                record.confidence,
                record.trend,
                record.uncertainty,
                record.lifecycle,
                record.recommended_experiment,
                record.first_seen or now,
                record.last_updated or now,
                now,
                updated,
            ),
        )
        self.conn.commit()
        return record.opportunity_id

    def add_score(
        self,
        opportunity_id: str,
        run_id: str | None,
        potential: float,
        confidence: float,
        created_at: str | None = None,
    ) -> str:
        """Append one score row. Returns the score id."""
        import uuid

        score_id = f"score_{uuid.uuid4().hex[:16]}"
        now = created_at or _now()
        self.conn.execute(
            "INSERT INTO opportunity_scores (score_id, opportunity_id, run_id, "
            "potential, confidence, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (score_id, opportunity_id, run_id, potential, confidence, now),
        )
        self.conn.commit()
        return score_id

    def add_evidence(
        self,
        opportunity_id: str,
        span_id: str,
        stance: str,
        created_at: str | None = None,
    ) -> None:
        """Link one evidence span to an opportunity. Idempotent."""
        now = created_at or _now()
        self.conn.execute(
            "INSERT OR IGNORE INTO opportunity_evidence (opportunity_id, span_id, "
            "stance, created_at) VALUES (?, ?, ?, ?)",
            (opportunity_id, span_id, stance, now),
        )
        self.conn.commit()

    def add_claim(
        self,
        claim_id: str,
        opportunity_id: str,
        statement: str,
        kind: str | None = None,
        span_id: str | None = None,
        created_at: str | None = None,
    ) -> None:
        """Record one claim behind an opportunity."""
        now = created_at or _now()
        self.conn.execute(
            "INSERT OR IGNORE INTO opportunity_claims (claim_id, opportunity_id, "
            "statement, kind, span_id, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (claim_id, opportunity_id, statement, kind, span_id, now),
        )
        self.conn.commit()

    def get(self, opportunity_id: str) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM opportunities WHERE opportunity_id = ?",
            (opportunity_id,),
        ).fetchone()
        if row is None:
            return None
        return dict(row)

    def list(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM opportunities ORDER BY created_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]

    def count(self) -> int:
        return int(self.conn.execute("SELECT COUNT(*) FROM opportunities").fetchone()[0])


def create_opportunity_id(title: str, run_id: str | None = None) -> str:
    """Return a deterministic id for an opportunity from its title."""
    seed = (run_id or "") + "|" + title
    return "opp_" + hashlib.sha256(seed.encode()).hexdigest()[:16]

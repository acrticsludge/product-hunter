"""SQL for the evidence layer: spans, observations, versions, and lineage.

Every function here takes one SQLite connection and returns rows or plain
values.

There are two writers of an evidence row: ``write_evidence_span``, which the
pipeline uses, and ``Store._insert_evidence``, which the Wave 1 import path
uses. They agree because both derive the facts the same way, through
``derive_reliability`` and ``derive_strength`` here, and through
``evidence_hash``. The pipeline's writer is authoritative for the conflict
rule and the idempotency rule: the same span written twice is one row, and a
span id that carries different content is refused.
"""

from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone

from product_hunter.fetch import utcnow
from product_hunter.quote import canonicalize

# The rule version that produced the derived facts on a row.
DERIVATION_VERSION = "evidence-v1"

# The strength of a span, from the candidate that produced it. A candidate
# that is not accepted is never direct: there is no pain point behind it. A
# candidate that names a real problem but was not accepted still says more
# than one that names no problem at all.
_STRENGTHS = {
    "genuine": ("direct", 0.8),
    "weak": ("single", 0.4),
    "not_pain": ("indirect", 0.1),
}
_SINGLE = ("single", 0.4)

# The fields of an evidence row that never change once the row is written. A
# second write that disagrees on one of them is a conflict, not an update.
IMMUTABLE_FIELDS = (
    "doc_id",
    "quote",
    "char_start",
    "char_end",
    "match_mode",
    "model_quote",
    "occurrence",
)


class EvidenceError(Exception):
    """An evidence write was refused."""


class EvidenceConflict(EvidenceError):
    """A stored span disagrees with the record that was being written."""


@dataclass(frozen=True)
class EvidenceSpan:
    """One evidence span, as the pipeline wants it written."""

    span_id: str
    doc_id: str
    quote: str
    char_start: int
    char_end: int
    match_mode: str | None = None
    model_quote: str | None = None
    occurrence: int = 1
    provider: str | None = None
    model: str | None = None
    extractor_version: str | None = None
    prompt_version: str | None = None
    normalization_version: str | None = None
    raw_path: str | None = None
    raw_sha256: str | None = None
    text_sha256: str | None = None
    evidence_hash: str = ""
    strength: str = "unknown"
    strength_score: float = 0.0
    reliability: str = "unknown"
    reliability_score: float = 0.0
    derivation_version: str = DERIVATION_VERSION
    live_status: str = "live"
    canonical_span_id: str | None = None


@dataclass(frozen=True)
class SpanWrite:
    """The result of writing a span: the row, and whether it was new."""

    span_id: str
    created: bool
    row: sqlite3.Row


@dataclass(frozen=True)
class LineageEvent:
    """One lifecycle event.

    The id is a hash of the event, so a replay of the same event is one row.
    ``event_id`` and ``created_at`` are filled in when the event is written.
    """

    event_type: str
    detail: str = ""
    span_id: str | None = None
    doc_id: str | None = None
    actor: str = "evidence-pipeline"
    event_id: str = ""
    created_at: str = ""


@dataclass(frozen=True)
class DocumentVersion:
    """One observed version of a document."""

    doc_id: str
    content_version: int
    text_sha256: str
    raw_sha256: str | None = None
    text_path: str | None = None
    title: str | None = None
    published_at: str | None = None
    live_status: str = "live"
    reason: str = ""


# --- derived facts --------------------------------------------------------


def evidence_hash(quote: str) -> str:
    """The content hash of a quote: the hash of its canonical form.

    Two quotes that differ only in curly quotes, dashes, or whitespace share
    a hash.
    """
    return hashlib.sha256(canonicalize(quote).encode("utf-8")).hexdigest()


def derive_reliability(conn: sqlite3.Connection, source_id: str) -> tuple[str, float]:
    """The reliability of a source, read from the sources row.

    A source that is not in the store is unverified: nothing is known about
    it, and nothing is assumed.
    """
    row = conn.execute(
        "SELECT terms_status, review_basis FROM sources WHERE source_id = ?",
        (source_id,),
    ).fetchone()
    if row is None:
        return "unverified", 0.3
    terms = str(row["terms_status"] or "").strip().lower()
    basis = str(row["review_basis"] or "").strip()
    if terms in ("permitted", "reviewed"):
        return ("reviewed", 1.0) if basis else ("permitted", 0.7)
    if terms in ("restricted", "prohibited"):
        return "blocked", 0.0
    return "unverified", 0.3


def derive_strength(pain_validity: str, accepted: bool) -> tuple[str, float]:
    """The strength of a span, from the candidate that produced it.

    The candidate has to be accepted to be direct: a candidate that is not
    accepted has no pain point behind it, so it is never direct evidence. A
    candidate that names a real problem but was not accepted is still more
    than one that names no problem at all.
    """
    if pain_validity == "genuine":
        return _STRENGTHS["genuine"] if accepted else _SINGLE
    if pain_validity == "weak":
        return _STRENGTHS["weak"]
    return _STRENGTHS["not_pain"]


# --- spans ----------------------------------------------------------------


def write_evidence_span(
    conn: sqlite3.Connection, span: EvidenceSpan, *, now: datetime | None = None
) -> SpanWrite:
    """Write one evidence span, or return the row that is already there.

    The second write of a span compares the immutable fields and refuses a
    disagreement. It never updates the row, so a replay is a no-op.
    """
    existing = load_span(conn, span.span_id)
    if existing is not None:
        for field in IMMUTABLE_FIELDS:
            if existing[field] != getattr(span, field):
                raise EvidenceConflict(
                    f"evidence {span.span_id!r} already exists with a different {field}"
                )
        return SpanWrite(span_id=span.span_id, created=False, row=existing)
    now_iso = _now(now).isoformat()
    conn.execute(
        """
        INSERT INTO evidence (
            span_id, doc_id, quote, char_start, char_end, match_mode,
            model_quote, occurrence, provider, model, extractor_version,
            prompt_version, normalization_version, raw_path, raw_sha256,
            text_sha256, evidence_hash, strength, strength_score, reliability,
            reliability_score, derivation_version, live_status,
            canonical_span_id, first_seen_at, last_seen_at, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            span.span_id,
            span.doc_id,
            span.quote,
            span.char_start,
            span.char_end,
            span.match_mode,
            span.model_quote,
            span.occurrence,
            span.provider,
            span.model,
            span.extractor_version,
            span.prompt_version,
            span.normalization_version,
            span.raw_path,
            span.raw_sha256,
            span.text_sha256,
            span.evidence_hash,
            span.strength,
            span.strength_score,
            span.reliability,
            span.reliability_score,
            span.derivation_version,
            span.live_status,
            span.canonical_span_id,
            now_iso,
            now_iso,
            now_iso,
        ),
    )
    row = load_span(conn, span.span_id)
    if row is None:  # pragma: no cover - the insert either raised or made a row
        raise EvidenceError("the evidence insert did not produce a row")
    return SpanWrite(span_id=span.span_id, created=True, row=row)


def load_span(conn: sqlite3.Connection, span_id: str) -> sqlite3.Row | None:
    """The evidence row for one span, or None."""
    return conn.execute(
        "SELECT * FROM evidence WHERE span_id = ?", (span_id,)
    ).fetchone()


def spans_for_document(conn: sqlite3.Connection, doc_id: str) -> list[sqlite3.Row]:
    """Every evidence span of one document, oldest first."""
    return list(
        conn.execute(
            "SELECT * FROM evidence WHERE doc_id = ? ORDER BY created_at, span_id",
            (doc_id,),
        ).fetchall()
    )


def find_canonical_span(
    conn: sqlite3.Connection, evidence_hash: str, *, exclude: str | None = None
) -> str | None:
    """The earliest live span that carries this content, if there is one.

    A span that points at another one through ``canonical_span_id`` is a copy
    of it, so only the root of a chain is a candidate.
    """
    row = conn.execute(
        """
        SELECT span_id FROM evidence
        WHERE evidence_hash = ? AND live_status = 'live'
          AND canonical_span_id IS NULL AND span_id <> ?
        ORDER BY COALESCE(first_seen_at, created_at), created_at, span_id
        LIMIT 1
        """,
        (evidence_hash, exclude or ""),
    ).fetchone()
    return str(row["span_id"]) if row is not None else None


def recompute_strength(conn: sqlite3.Connection, normalized_key: str) -> int:
    """Promote the live spans of a key to corroborated when two documents have it.

    The document count is taken across the whole key, not inside one span's
    group: a span belongs to one document, so counting inside the group
    would always find one. Returns the number of spans that changed. A span
    is never downgraded.
    """
    documents = conn.execute(
        """
        SELECT COUNT(DISTINCT p.doc_id)
        FROM pain_points p
        JOIN evidence e ON e.span_id = p.span_id
        WHERE p.normalized_key = ? AND e.live_status = 'live'
        """,
        (normalized_key,),
    ).fetchone()
    if int(documents[0]) < 2:
        return 0
    spans = conn.execute(
        """
        SELECT e.span_id
        FROM evidence e
        JOIN pain_points p ON p.span_id = e.span_id
        WHERE p.normalized_key = ? AND e.live_status = 'live'
        """,
        (normalized_key,),
    ).fetchall()
    updated = 0
    for row in spans:
        updated += conn.execute(
            """
            UPDATE evidence SET strength = 'corroborated', strength_score = 1.0
            WHERE span_id = ? AND live_status = 'live'
              AND (strength <> 'corroborated' OR strength_score <> 1.0)
            """,
            (row["span_id"],),
        ).rowcount
    return updated


# --- observations ---------------------------------------------------------


def record_observation(
    conn: sqlite3.Connection, span: EvidenceSpan, *, now: datetime | None = None
) -> bool:
    """Record one sighting of a span. Returns True when a row was new.

    The sighting is identified by the span, the document, and the version of
    the document's text, so the same span seen again in the same version is
    the same sighting.
    """
    now_iso = _now(now).isoformat()
    document = load_document(conn, span.doc_id)
    content_version = int(document["content_version"]) if document is not None else 1
    live_status = str(document["live_status"]) if document is not None else "unknown"
    text_sha256 = str(document["text_sha256"]) if document is not None else span.text_sha256
    observation_id = _hash_id(
        "obs_", f"{span.span_id}|{span.doc_id}|{content_version}|{text_sha256 or ''}"
    )
    changed = conn.execute(
        """
        INSERT OR IGNORE INTO evidence_observations (
            observation_id, span_id, doc_id, content_version, text_sha256,
            quote, char_start, char_end, match_mode, provider, model,
            extractor_version, prompt_version, live_status, observed_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            observation_id,
            span.span_id,
            span.doc_id,
            content_version,
            text_sha256,
            span.quote,
            span.char_start,
            span.char_end,
            span.match_mode,
            span.provider,
            span.model,
            span.extractor_version,
            span.prompt_version,
            live_status,
            now_iso,
        ),
    ).rowcount
    if changed:
        conn.execute(
            """
            UPDATE evidence
            SET observation_count = (
                SELECT count(*) FROM evidence_observations WHERE span_id = ?
            )
            WHERE span_id = ?
            """,
            (span.span_id, span.span_id),
        )
    return changed > 0


# --- lineage --------------------------------------------------------------


def append_lineage(
    conn: sqlite3.Connection, event: LineageEvent, *, now: datetime | None = None
) -> bool:
    """Record one lifecycle event. Returns True when the row was new.

    The event id is a hash of the event, so the same event recorded twice is
    one row.
    """
    now_iso = _now(now).isoformat()
    event_id = _hash_id(
        "lin_",
        f"{event.event_type}|{event.span_id or ''}|{event.doc_id or ''}|{event.detail}",
    )
    changed = conn.execute(
        """
        INSERT OR IGNORE INTO evidence_lineage (
            event_id, span_id, doc_id, event_type, detail, actor, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event_id,
            event.span_id,
            event.doc_id,
            event.event_type,
            event.detail,
            event.actor,
            now_iso,
        ),
    ).rowcount
    return changed > 0


def count_lineage_events(
    conn: sqlite3.Connection, *, span_id: str | None = None, doc_id: str | None = None
) -> int:
    """How many lifecycle events an entity already has.

    The count is the sequence number of the next event, so two occurrences
    of the same transition are two rows.
    """
    clauses: list[str] = []
    values: list[str] = []
    if span_id is not None:
        clauses.append("span_id = ?")
        values.append(span_id)
    if doc_id is not None:
        clauses.append("doc_id = ?")
        values.append(doc_id)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    row = conn.execute(
        f"SELECT count(*) FROM evidence_lineage {where}", tuple(values)
    ).fetchone()
    return int(row[0])


def lineage_events(
    conn: sqlite3.Connection, *, span_id: str | None = None, doc_id: str | None = None
) -> list[sqlite3.Row]:
    """The lineage of a span, of a document, or of both."""
    clauses: list[str] = []
    values: list[str] = []
    if span_id is not None:
        clauses.append("span_id = ?")
        values.append(span_id)
    if doc_id is not None:
        clauses.append("doc_id = ?")
        values.append(doc_id)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    return list(
        conn.execute(
            f"SELECT * FROM evidence_lineage {where} ORDER BY created_at, event_id",
            tuple(values),
        ).fetchall()
    )


# --- documents ------------------------------------------------------------


def load_document(conn: sqlite3.Connection, doc_id: str) -> sqlite3.Row | None:
    """The document row for one document, or None."""
    return conn.execute(
        "SELECT * FROM documents WHERE doc_id = ?", (doc_id,)
    ).fetchone()


def write_document_version(
    conn: sqlite3.Connection, version: DocumentVersion, *, now: datetime | None = None
) -> bool:
    """Record one observed version of a document. Returns True when it was new.

    The row is written when the version is observed and it stays when the
    document moves on, so the history of a document is complete.
    """
    now_iso = _now(now).isoformat()
    version_id = _hash_id(
        "dv_", f"{version.doc_id}|{version.content_version}|{version.text_sha256}"
    )
    changed = conn.execute(
        """
        INSERT OR IGNORE INTO document_versions (
            version_id, doc_id, content_version, text_sha256, raw_sha256,
            text_path, title, published_at, live_status, reason, recorded_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            version_id,
            version.doc_id,
            version.content_version,
            version.text_sha256,
            version.raw_sha256,
            version.text_path,
            version.title,
            version.published_at,
            version.live_status,
            version.reason,
            now_iso,
        ),
    ).rowcount
    return changed > 0


def document_versions(
    conn: sqlite3.Connection, doc_id: str
) -> list[sqlite3.Row]:
    """Every recorded version of a document, oldest first."""
    return list(
        conn.execute(
            "SELECT * FROM document_versions WHERE doc_id = ? "
            "ORDER BY content_version, version_id",
            (doc_id,),
        ).fetchall()
    )


def set_document_status(
    conn: sqlite3.Connection,
    doc_id: str,
    status: str,
    *,
    now: datetime | None = None,
) -> int:
    """Set the status of a document and of every span of it.

    The document and its spans never disagree about liveness, so both are
    written here. The deletion timestamp is written with the status, so the
    ``deleted_at`` column the independence analysis reads holds the instant a
    document went out of the live set and not a null.

    Returns the number of spans that changed.
    """
    moment = _now(now).isoformat()
    conn.execute(
        "UPDATE documents SET live_status = ? WHERE doc_id = ?", (status, doc_id)
    )
    if status == "live":
        conn.execute(
            "UPDATE documents SET deleted_at = NULL WHERE doc_id = ?", (doc_id,)
        )
    else:
        conn.execute(
            "UPDATE documents SET deleted_at = ? WHERE doc_id = ? AND deleted_at IS NULL",
            (moment, doc_id),
        )
    return conn.execute(
        "UPDATE evidence SET live_status = ? WHERE doc_id = ?", (status, doc_id)
    ).rowcount


def set_span_status(conn: sqlite3.Connection, span_id: str, status: str) -> int:
    """Set the status of one span. Returns the number of rows changed."""
    return conn.execute(
        "UPDATE evidence SET live_status = ? WHERE span_id = ?", (status, span_id)
    ).rowcount


# --- pain points ----------------------------------------------------------


def link_pain_point(
    conn: sqlite3.Connection,
    pain_id: str,
    span_id: str,
    *,
    basis: str = "extraction",
    confidence: float = 0.0,
    created_by: str | None = None,
    now: datetime | None = None,
) -> bool:
    """Link a pain point to the span that backs it. Returns True when it was new."""
    now_iso = _now(now).isoformat()
    existing = conn.execute(
        "SELECT 1 FROM evidence_pain_links WHERE pain_id = ? AND span_id = ?",
        (pain_id, span_id),
    ).fetchone()
    conn.execute(
        """
        INSERT INTO evidence_pain_links (
            pain_id, span_id, basis, confidence, created_by, created_at
        ) VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(pain_id, span_id) DO UPDATE SET
            basis = excluded.basis,
            confidence = excluded.confidence,
            created_by = excluded.created_by
        """,
        (pain_id, span_id, basis, confidence, created_by, now_iso),
    )
    return existing is None


def _hash_id(prefix: str, basis: str) -> str:
    return prefix + hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16]


def _now(value: datetime | None) -> datetime:
    """The instant to write, always in UTC."""
    if value is None:
        return utcnow()
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)

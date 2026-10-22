"""Turn collected documents and extracted candidates into durable evidence.

The pipeline is a library. It never calls a model, never opens a socket, and
never runs a command: captured web content is data, and every identifier it
writes is a hash of that data. The quote of a span is re-derived from the
stored document text at write time, so a candidate that does not say what the
document says is refused whatever it claims.
"""

from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Iterable
from contextlib import contextmanager
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from product_hunter.config import load_config, repo_root as default_repo_root
from product_hunter.db.evidence import (
    EvidenceConflict,
    EvidenceSpan,
    LineageEvent,
    append_lineage,
    count_lineage_events,
    derive_reliability,
    derive_strength,
    evidence_hash,
    find_canonical_span,
    lineage_events,
    link_pain_point as write_pain_link,
    load_document,
    load_span,
    record_observation,
    recompute_strength,
    set_document_status,
    set_span_status,
    spans_for_document,
    write_evidence_span,
)
from product_hunter.db.store import Store, StoreConflict, StoreError
from product_hunter.documents import Document
from product_hunter.extraction import ExtractedCandidate, derive_span_id
from product_hunter.fetch import utcnow
from product_hunter.quote import QuoteError, locate_quote

# The reason codes of a refusal. They are the API of the pipeline: a caller
# branches on the code, never on the message.
MISSING_PROVENANCE = "missing_provenance"
DOCUMENT_NOT_FOUND = "document_not_found"
SPAN_NOT_FOUND = "span_not_found"
DOCUMENT_TEXT_UNAVAILABLE = "document_text_unavailable"
PROVENANCE_MISMATCH = "provenance_mismatch"
INVALID_SPAN = "invalid_span"
SPAN_MISMATCH = "span_mismatch"
INVALID_QUOTE = "invalid_quote"
QUOTE_TOO_SHORT = "quote_too_short"
QUOTE_TOO_LONG = "quote_too_long"
SPAN_ID_MISMATCH = "span_id_mismatch"
EVIDENCE_CONFLICT = "evidence_conflict"

# shortcut: the text of a document is capped at 16 MB, eight times the 2 MB
# capture limit in the source config; raise it when a legitimate source
# needs more.
MAX_DOCUMENT_BYTES = 16 * 1024 * 1024

# The quote bounds used when the repository config cannot be read. The
# configured values are the same today; the config is the source of truth.
_FALLBACK_MIN_QUOTE_CHARS = 20
_FALLBACK_MAX_QUOTE_CHARS = 2000


class EvidenceRejected(Exception):
    """The pipeline refused a candidate. The reason code is the API."""

    def __init__(self, reason_code: str, detail: str = "") -> None:
        self.reason_code = reason_code
        self.detail = detail
        super().__init__(detail or reason_code)


class EvidenceStoreError(Exception):
    """The store could not accept the write.

    A store failure is not a refusal of the candidate. A refusal means the
    candidate is bad; a store failure means the database could not take the
    write, and the caller has to know which one happened.
    """

    def __init__(self, reason_code: str = "store_error", detail: str = "") -> None:
        self.reason_code = reason_code
        self.detail = detail
        super().__init__(detail or reason_code)


@dataclass(frozen=True)
class DocumentIngest:
    """What one ingest of a document did."""

    doc_id: str
    content_version: int
    created: bool
    version_changed: bool
    live_status: str
    revalidation: RevalidationOutcome | None = None


@dataclass(frozen=True)
class EvidenceWrite:
    """The span one candidate produced."""

    span_id: str
    doc_id: str
    quote: str
    created: bool
    strength: str
    strength_score: float
    reliability: str
    reliability_score: float
    canonical_span_id: str | None
    observation_count: int
    pain_id: str | None


@dataclass(frozen=True)
class BatchOutcome:
    """What one batch of candidates produced."""

    recorded: int
    rejected: int
    writes: list[EvidenceWrite] = field(default_factory=list)
    rejections: list[EvidenceRejected] = field(default_factory=list)


@dataclass(frozen=True)
class RevalidationOutcome:
    """What one revalidation of a document found."""

    doc_id: str
    verified: int = 0
    stale: int = 0
    observations: int = 0


@dataclass(frozen=True)
class EvidenceAudit:
    """The whole chain behind one span."""

    span: sqlite3.Row | None
    document: sqlite3.Row | None
    fetch: sqlite3.Row | None
    source: sqlite3.Row | None
    observations: list[sqlite3.Row] = field(default_factory=list)
    pain_points: list[sqlite3.Row] = field(default_factory=list)
    lineage: list[LineageEvent] = field(default_factory=list)
    document_lineage: list[LineageEvent] = field(default_factory=list)


@lru_cache(maxsize=1)
def _quote_bounds() -> tuple[int, int]:
    """The quote bounds from the repository config.

    The extractor's length rule and the pipeline's length rule have to be the
    same rule, so both read the config. A config that cannot be read falls
    back to the values the config holds today.
    """
    try:
        limits = load_config().limits
        return int(limits.min_quote_chars), int(limits.max_quote_chars)
    except (OSError, ValueError):  # a missing or broken config must not hide it
        return _FALLBACK_MIN_QUOTE_CHARS, _FALLBACK_MAX_QUOTE_CHARS


def _quote_reason_code(exc: QuoteError) -> str:
    """The pipeline's reason code for a quote that does not verify.

    The upstream code names the rule that refused the quote; the pipeline
    keeps the rule and says what it means for the candidate.
    """
    if exc.reason_code == "quote_too_short":
        return QUOTE_TOO_SHORT
    if exc.reason_code == "quote_too_long":
        return QUOTE_TOO_LONG
    return INVALID_QUOTE


def _status_detail(previous: str, status: str, sequence: int, detail: str) -> str:
    """The detail of a status change: the state it moved from and to.

    The state and the event sequence are in the detail, so two occurrences
    of the same transition are two rows, while the same transition applied
    twice is one.
    """
    text = f"{previous}->{status}#{sequence}"
    return f"{text}:{detail}" if detail else text


@contextmanager
def _transaction(conn: sqlite3.Connection):
    """Run the body in one transaction, or join the one that is open."""
    if conn.in_transaction:
        yield
        return
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


@contextmanager
def _store_errors():
    """Turn a raw SQLite error into a store error.

    A store failure is not a refusal of the candidate: the caller has to be
    able to tell the two apart. A refusal travels as ``EvidenceRejected``, a
    store failure as ``EvidenceStoreError``.
    """
    try:
        yield
    except sqlite3.Error as exc:
        raise EvidenceStoreError("store_error", str(exc)) from exc


class EvidencePipeline:
    """Turn parsed documents and extracted candidates into durable evidence.

    A refused candidate raises ``EvidenceRejected`` with a reason code. A
    store that cannot take the write raises ``EvidenceStoreError``. The two
    are different answers, and the caller has to be able to tell them apart.
    """

    def __init__(
        self, store: Store, *, repo_root: Path | None = None, now=utcnow
    ) -> None:
        self.store = store
        self.conn = store.conn
        self._root = Path(repo_root) if repo_root is not None else default_repo_root()
        self._now = now

    # --- documents --------------------------------------------------------

    def ingest_document(self, document: Document, *, fetch=None) -> DocumentIngest:
        """Put a parsed document into the store, with its version and liveness."""
        return self._ingest(document, fetch=fetch, reason="ingest")

    def reprocess_document(self, document: Document, *, fetch=None) -> DocumentIngest:
        """Record a new content version of a document and revalidate its spans."""
        ingest = self._ingest(document, fetch=fetch, reason="reprocess")
        revalidation = self.revalidate_document(document.doc_id)
        return DocumentIngest(
            doc_id=ingest.doc_id,
            content_version=ingest.content_version,
            created=ingest.created,
            version_changed=ingest.version_changed,
            live_status=ingest.live_status,
            revalidation=revalidation,
        )

    def _ingest(self, document: Document, *, fetch, reason: str) -> DocumentIngest:
        if fetch is not None:
            if fetch.fetch_id != document.fetch_id:
                raise StoreError(
                    f"document {document.doc_id!r} names fetch "
                    f"{document.fetch_id!r}, but the fetch is {fetch.fetch_id!r}"
                )
            # The document and its fetch have to agree on the raw bytes, or
            # the store holds a document and a capture that disagree.
            if fetch.raw_sha256 != document.raw_sha256:
                raise StoreConflict(
                    f"document {document.doc_id!r} has raw hash "
                    f"{document.raw_sha256!r}, but its fetch {fetch.fetch_id!r} "
                    f"has {fetch.raw_sha256!r}"
                )
        with _store_errors(), _transaction(self.conn):
            if fetch is not None:
                self.store.import_fetch(fetch)
            imported = self.store.import_document_version(document, reason=reason)
            version = imported.content_version
            append_lineage(
                self.conn,
                LineageEvent(
                    event_type="document_ingested",
                    detail=f"{reason}:v{version}",
                    doc_id=document.doc_id,
                ),
                now=self._now(),
            )
            if imported.version_changed:
                append_lineage(
                    self.conn,
                    LineageEvent(
                        event_type="document_versioned",
                        detail=f"{reason}:v{version}",
                        doc_id=document.doc_id,
                    ),
                    now=self._now(),
                )
        return DocumentIngest(
            doc_id=document.doc_id,
            content_version=imported.content_version,
            created=imported.document,
            version_changed=imported.version_changed,
            live_status=document.live_status,
        )

    def revalidate_document(
        self, doc_id: str, *, document_text: str | None = None
    ) -> RevalidationOutcome:
        """Check every span of a document against the text it now holds.

        A span that still verifies is observed again. A span that no longer
        verifies goes stale, with a lineage event. The pain points are left
        alone: a pain point was a real observation of a real quote even when
        the page around it has since changed.
        """
        with _store_errors(), _transaction(self.conn):
            document = load_document(self.conn, doc_id)
            if document is None:
                raise EvidenceRejected(
                    DOCUMENT_NOT_FOUND, f"document {doc_id!r} is not in the store"
                )
            text = self._read_text(document, document_text)
            now = self._now()
            verified = stale = observations = 0
            for row in spans_for_document(self.conn, doc_id):
                span = _span_from_row(row)
                if _verifies(text, row):
                    self.conn.execute(
                        "UPDATE evidence SET text_sha256 = ?, last_seen_at = ? "
                        "WHERE span_id = ?",
                        (document["text_sha256"], now.isoformat(), row["span_id"]),
                    )
                    if record_observation(self.conn, span, now=now):
                        observations += 1
                    verified += 1
                else:
                    self.conn.execute(
                        "UPDATE evidence SET live_status = 'stale', superseded_at = ? "
                        "WHERE span_id = ?",
                        (now.isoformat(), row["span_id"]),
                    )
                    append_lineage(
                        self.conn,
                        LineageEvent(
                            event_type="span_stale",
                            detail=f"v{int(document['content_version'])}",
                            span_id=row["span_id"],
                            doc_id=doc_id,
                        ),
                        now=now,
                    )
                    stale += 1
        return RevalidationOutcome(
            doc_id=doc_id, verified=verified, stale=stale, observations=observations
        )

    # --- candidates -------------------------------------------------------

    def record_candidate(
        self, candidate: ExtractedCandidate, *, document_text: str | None = None
    ) -> EvidenceWrite:
        """Validate one candidate against the stored document and persist it.

        The checks run in the order of the spec, so the reason code names the
        earliest thing that went wrong.
        """
        try:
            return self._record(candidate, document_text=document_text)
        except EvidenceRejected as exc:
            self._note_rejection(candidate, exc)
            raise
        except StoreConflict as exc:
            rejected = EvidenceRejected(EVIDENCE_CONFLICT, str(exc))
            self._note_rejection(candidate, rejected)
            raise rejected from exc
        except sqlite3.IntegrityError as exc:
            # The database refused the write, most often because a trigger
            # found a pain point without a verified span. The caller gets a
            # reason code, never a raw SQLite error.
            rejected = EvidenceRejected(EVIDENCE_CONFLICT, str(exc))
            self._note_rejection(candidate, rejected)
            raise rejected from exc
        except sqlite3.Error as exc:
            # The store could not take the write. This is not a refusal of
            # the candidate, and it is not recorded as one.
            raise EvidenceStoreError("store_error", str(exc)) from exc

    def record_batch(
        self,
        candidates: Iterable[ExtractedCandidate],
        *,
        document_text: str | None = None,
    ) -> BatchOutcome:
        """Record many candidates. One refusal does not stop the rest."""
        digest = (
            hashlib.sha256(document_text.encode("utf-8")).hexdigest()
            if document_text is not None
            else None
        )
        writes: list[EvidenceWrite] = []
        rejections: list[EvidenceRejected] = []
        for candidate in candidates:
            # The text belongs to the document it was read from. A candidate
            # from another document reads its own file instead.
            text = (
                document_text
                if document_text is not None and candidate.text_sha256 == digest
                else None
            )
            try:
                writes.append(self.record_candidate(candidate, document_text=text))
            except EvidenceRejected as exc:
                rejections.append(exc)
        return BatchOutcome(
            recorded=len(writes), rejected=len(rejections), writes=writes, rejections=rejections
        )

    def _record(
        self, candidate: ExtractedCandidate, *, document_text: str | None
    ) -> EvidenceWrite:
        self._check_provenance(candidate)
        document = load_document(self.conn, candidate.doc_id)
        if document is None:
            raise EvidenceRejected(
                DOCUMENT_NOT_FOUND,
                f"document {candidate.doc_id!r} is not in the store",
            )
        text = self._read_text(document, document_text)
        self._check_provenance_match(candidate, document)
        self._check_span(candidate, text)
        self._check_quote(candidate, text)
        self._check_span_id(candidate)

        reliability, reliability_score = derive_reliability(
            self.conn, str(document["source_id"])
        )
        strength, strength_score = derive_strength(
            candidate.pain_validity.value, candidate.accepted
        )
        content_hash = evidence_hash(candidate.quote)
        content_version = int(document["content_version"])
        canonical = find_canonical_span(
            self.conn, content_hash, exclude=candidate.span_id
        )
        span = EvidenceSpan(
            span_id=candidate.span_id,
            doc_id=candidate.doc_id,
            quote=candidate.quote,
            char_start=candidate.char_start,
            char_end=candidate.char_end,
            match_mode=candidate.match_mode,
            model_quote=candidate.model_quote,
            occurrence=candidate.occurrence,
            provider=candidate.provider,
            model=candidate.model,
            extractor_version=candidate.extractor_version,
            prompt_version=candidate.prompt_version,
            normalization_version=candidate.normalization_version,
            raw_path=candidate.raw_path,
            raw_sha256=candidate.raw_sha256,
            text_sha256=candidate.text_sha256,
            evidence_hash=content_hash,
            strength=strength,
            strength_score=strength_score,
            reliability=reliability,
            reliability_score=reliability_score,
            live_status=str(document["live_status"] or "live"),
            canonical_span_id=canonical,
        )
        try:
            with _transaction(self.conn):
                write = write_evidence_span(self.conn, span, now=self._now())
                record_observation(self.conn, span, now=self._now())
                append_lineage(
                    self.conn,
                    LineageEvent(
                        event_type="span_recorded",
                        detail=f"v{content_version}",
                        span_id=candidate.span_id,
                        doc_id=candidate.doc_id,
                    ),
                    now=self._now(),
                )
                pain_id = None
                if candidate.accepted:
                    self.store.insert_pain_point(
                        candidate,
                        basis="extraction",
                        confidence=candidate.confidence,
                        created_by=candidate.provider,
                    )
                    if candidate.normalized_key:
                        recompute_strength(self.conn, candidate.normalized_key)
                    pain_id = candidate.pain_id
        except EvidenceConflict as exc:
            raise EvidenceRejected(EVIDENCE_CONFLICT, str(exc)) from exc

        # The row is read again, outside the transaction, so the caller sees
        # the committed state: the strength and the observation count can have
        # moved while the candidate was written.
        row = load_span(self.conn, candidate.span_id)
        if row is None:  # pragma: no cover - the span was written or refused
            raise EvidenceRejected(
                EVIDENCE_CONFLICT, f"evidence {candidate.span_id!r} is not in the store"
            )
        return EvidenceWrite(
            span_id=candidate.span_id,
            doc_id=candidate.doc_id,
            quote=candidate.quote,
            created=write.created,
            strength=str(row["strength"]),
            strength_score=float(row["strength_score"]),
            reliability=str(row["reliability"]),
            reliability_score=float(row["reliability_score"]),
            canonical_span_id=row["canonical_span_id"],
            observation_count=int(row["observation_count"]),
            pain_id=pain_id,
        )

    def _check_provenance(self, candidate: ExtractedCandidate) -> None:
        missing = [
            name
            for name, value in (
                ("text_sha256", candidate.text_sha256),
                ("raw_path", candidate.raw_path),
                ("raw_sha256", candidate.raw_sha256),
                ("extractor_version", candidate.extractor_version),
            )
            if not str(value or "").strip()
        ]
        if missing:
            raise EvidenceRejected(
                MISSING_PROVENANCE,
                f"the candidate has no {', '.join(missing)}",
            )

    def _check_provenance_match(
        self, candidate: ExtractedCandidate, document: sqlite3.Row
    ) -> None:
        if candidate.text_sha256 != document["text_sha256"]:
            raise EvidenceRejected(
                PROVENANCE_MISMATCH,
                "the candidate's text hash does not match the stored document",
            )
        if candidate.raw_sha256 != document["raw_sha256"]:
            raise EvidenceRejected(
                PROVENANCE_MISMATCH,
                "the candidate's raw hash does not match the stored document",
            )

    def _check_span(self, candidate: ExtractedCandidate, text: str) -> None:
        if (
            candidate.char_start < 0
            or candidate.char_end <= candidate.char_start
            or candidate.char_end > len(text)
        ):
            raise EvidenceRejected(
                INVALID_SPAN,
                f"the span [{candidate.char_start}, {candidate.char_end}) does not "
                f"fit in {len(text)} characters of text",
            )

    def _check_quote(self, candidate: ExtractedCandidate, text: str) -> None:
        min_chars, max_chars = _quote_bounds()
        try:
            locate_quote(
                text, candidate.quote, min_chars=min_chars, max_chars=max_chars
            )
        except QuoteError as exc:
            # The quote does not verify against the document at all.
            raise EvidenceRejected(_quote_reason_code(exc), exc.detail) from exc
        if text[candidate.char_start : candidate.char_end] != candidate.quote:
            # The quote is in the document, but not at these offsets.
            raise EvidenceRejected(
                SPAN_MISMATCH,
                "the quote is in the document, but not at the offsets",
            )

    def _check_span_id(self, candidate: ExtractedCandidate) -> None:
        derived = derive_span_id(
            candidate.doc_id, candidate.char_start, candidate.char_end, candidate.quote
        )
        if candidate.span_id != derived:
            raise EvidenceRejected(
                SPAN_ID_MISMATCH,
                f"the span id {candidate.span_id!r} is not the hash of its own fields",
            )

    def _read_text(self, document: sqlite3.Row, document_text: str | None) -> str:
        """The text of a document, read from the file its row points at.

        The hash of that text is checked against the row, so the text that is
        verified is the text that was captured. A file over the cap is
        refused before it is read, so the memory is never used.
        """
        if document_text is None:
            path = self._root / str(document["text_path"])
            try:
                size = path.stat().st_size
            except OSError as exc:
                raise EvidenceRejected(
                    DOCUMENT_TEXT_UNAVAILABLE,
                    f"the document text could not be read: {exc}",
                ) from exc
            if size > MAX_DOCUMENT_BYTES:
                raise EvidenceRejected(
                    DOCUMENT_TEXT_UNAVAILABLE,
                    f"the document text is {size} bytes, the cap is "
                    f"{MAX_DOCUMENT_BYTES} bytes",
                )
            try:
                text = path.read_text(encoding="utf-8")
            except OSError as exc:
                raise EvidenceRejected(
                    DOCUMENT_TEXT_UNAVAILABLE,
                    f"the document text could not be read: {exc}",
                ) from exc
        else:
            text = document_text
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        if digest != document["text_sha256"]:
            raise EvidenceRejected(
                DOCUMENT_TEXT_UNAVAILABLE,
                "the text does not match the hash the document row carries",
            )
        return text

    def _note_rejection(
        self, candidate: ExtractedCandidate, exc: EvidenceRejected
    ) -> None:
        """Record why a candidate was refused."""
        document = load_document(self.conn, candidate.doc_id)
        append_lineage(
            self.conn,
            LineageEvent(
                event_type="evidence_rejected",
                detail=f"{exc.reason_code}:{candidate.candidate_id}",
                doc_id=candidate.doc_id if document is not None else None,
            ),
            now=self._now(),
        )

    # --- status -----------------------------------------------------------

    def mark_document_status(self, doc_id: str, status: str, *, detail: str = "") -> int:
        """Set the status of a document and of every span of it.

        A document that is not in the store is refused with
        ``document_not_found``: the lineage row names the document, and the
        database would refuse a lineage row that names a document that is not
        there. The status of the document and of its spans move together, in
        one transaction.
        """
        with _store_errors(), _transaction(self.conn):
            document = load_document(self.conn, doc_id)
            if document is None:
                raise EvidenceRejected(
                    DOCUMENT_NOT_FOUND, f"document {doc_id!r} is not in the store"
                )
            previous = str(document["live_status"] or "live")
            before = {
                str(row["span_id"]): str(row["live_status"])
                for row in spans_for_document(self.conn, doc_id)
            }
            changed = set_document_status(self.conn, doc_id, status)
            if previous != status:
                # The document moved, so the move is on the record.
                sequence = count_lineage_events(self.conn, doc_id=doc_id)
                append_lineage(
                    self.conn,
                    LineageEvent(
                        event_type="document_status",
                        detail=_status_detail(previous, status, sequence, detail),
                        doc_id=doc_id,
                    ),
                    now=self._now(),
                )
            for span_id, span_status in before.items():
                if span_status != status:
                    sequence = count_lineage_events(self.conn, span_id=span_id)
                    append_lineage(
                        self.conn,
                        LineageEvent(
                            event_type="span_status",
                            detail=_status_detail(
                                span_status, status, sequence, detail
                            ),
                            span_id=span_id,
                            doc_id=doc_id,
                        ),
                        now=self._now(),
                    )
        return changed

    def mark_span_status(self, span_id: str, status: str, *, detail: str = "") -> int:
        """Set the status of one span.

        A span that is not in the store is refused with ``span_not_found``:
        the lineage row names the span, and the database would refuse a
        lineage row that names a span that is not there.
        """
        with _store_errors(), _transaction(self.conn):
            span = load_span(self.conn, span_id)
            if span is None:
                raise EvidenceRejected(
                    SPAN_NOT_FOUND, f"span {span_id!r} is not in the store"
                )
            previous = str(span["live_status"] or "live")
            if previous == status:
                # A no-op repeat moves nothing, so it writes no event.
                return 0
            sequence = count_lineage_events(self.conn, span_id=span_id)
            changed = set_span_status(self.conn, span_id, status)
            append_lineage(
                self.conn,
                LineageEvent(
                    event_type="span_status",
                    detail=_status_detail(previous, status, sequence, detail),
                    span_id=span_id,
                ),
                now=self._now(),
            )
        return changed

    def link_pain_point(
        self,
        pain_id: str,
        span_id: str,
        *,
        basis: str = "extraction",
        confidence: float = 0.0,
        created_by: str | None = None,
    ) -> bool:
        """Link a pain point to the span that backs it.

        The link has to be backed by the pain point's own span. The database
        refuses any other span: the trigger on ``evidence_pain_links`` needs a
        pain point whose span is the one the link names, and a link that
        names another span is refused with ``evidence_conflict``.
        """
        try:
            return write_pain_link(
                self.conn,
                pain_id,
                span_id,
                basis=basis,
                confidence=confidence,
                created_by=created_by,
                now=self._now(),
            )
        except sqlite3.IntegrityError as exc:
            raise EvidenceRejected(EVIDENCE_CONFLICT, str(exc)) from exc
        except sqlite3.Error as exc:
            raise EvidenceStoreError("store_error", str(exc)) from exc

    # --- reads ------------------------------------------------------------

    def audit(self, span_id: str) -> EvidenceAudit:
        """The whole chain behind one span."""
        span = load_span(self.conn, span_id)
        document = load_document(self.conn, str(span["doc_id"])) if span else None
        fetch = None
        source = None
        if document is not None:
            fetch = self.conn.execute(
                "SELECT * FROM fetches WHERE fetch_id = ?", (document["fetch_id"],)
            ).fetchone()
            source = self.conn.execute(
                "SELECT * FROM sources WHERE source_id = ?", (document["source_id"],)
            ).fetchone()
        observations = list(
            self.conn.execute(
                "SELECT * FROM evidence_observations WHERE span_id = ? "
                "ORDER BY observed_at, observation_id",
                (span_id,),
            ).fetchall()
        )
        pain_points = list(
            self.conn.execute(
                "SELECT * FROM pain_points WHERE span_id = ? ORDER BY created_at, pain_id",
                (span_id,),
            ).fetchall()
        )
        return EvidenceAudit(
            span=span,
            document=document,
            fetch=fetch,
            source=source,
            observations=observations,
            pain_points=pain_points,
            lineage=self.lineage(span_id=span_id),
            document_lineage=(
                self.lineage(doc_id=str(document["doc_id"])) if document else []
            ),
        )

    def lineage(
        self, *, span_id: str | None = None, doc_id: str | None = None
    ) -> list[LineageEvent]:
        """The lifecycle events of a span, of a document, or of both."""
        return [
            LineageEvent(
                event_id=str(row["event_id"]),
                span_id=row["span_id"],
                doc_id=row["doc_id"],
                event_type=str(row["event_type"]),
                detail=str(row["detail"] or ""),
                actor=row["actor"],
                created_at=str(row["created_at"]),
            )
            for row in lineage_events(self.conn, span_id=span_id, doc_id=doc_id)
        ]

    def independent_evidence_count(self, normalized_key: str) -> int:
        """How many independent pieces of evidence a key has.

        A copy of the same complaint counts once: the count is of distinct
        canonical spans, not of rows.
        """
        row = self.conn.execute(
            """
            SELECT count(DISTINCT COALESCE(e.canonical_span_id, e.span_id))
            FROM evidence e
            JOIN pain_points p ON p.span_id = e.span_id
            WHERE p.normalized_key = ? AND e.live_status = 'live'
            """,
            (normalized_key,),
        ).fetchone()
        return int(row[0])


def _verifies(text: str, row: sqlite3.Row) -> bool:
    """Whether a span still holds its quote at its offsets."""
    start = int(row["char_start"])
    end = int(row["char_end"])
    if start < 0 or end <= start or end > len(text):
        return False
    return text[start:end] == row["quote"]


def _span_from_row(row: sqlite3.Row) -> EvidenceSpan:
    """The span a stored evidence row describes."""
    return EvidenceSpan(
        span_id=str(row["span_id"]),
        doc_id=str(row["doc_id"]),
        quote=str(row["quote"]),
        char_start=int(row["char_start"]),
        char_end=int(row["char_end"]),
        match_mode=row["match_mode"],
        model_quote=row["model_quote"],
        occurrence=int(row["occurrence"]),
        provider=row["provider"],
        model=row["model"],
        extractor_version=row["extractor_version"],
        prompt_version=row["prompt_version"],
        normalization_version=row["normalization_version"],
        raw_path=row["raw_path"],
        raw_sha256=row["raw_sha256"],
        text_sha256=row["text_sha256"],
        evidence_hash=str(row["evidence_hash"] or ""),
        strength=str(row["strength"]),
        strength_score=float(row["strength_score"]),
        reliability=str(row["reliability"]),
        reliability_score=float(row["reliability_score"]),
        derivation_version=str(row["derivation_version"]),
        live_status=str(row["live_status"]),
        canonical_span_id=row["canonical_span_id"],
    )

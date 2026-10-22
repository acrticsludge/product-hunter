"""Import Phase 1 run artifacts into the authoritative SQLite store.

The import is one transaction. A run is replayed by importing the same
directory again: existing rows are checked against the incoming content, and a
changed value under the same deterministic id is a conflict, not an overwrite.
The downstream projection is enqueued in the same transaction as the evidence.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar
from urllib.parse import urlparse

from pydantic import BaseModel

from product_hunter.db.connection import connect
from product_hunter.db.evidence import (
    DERIVATION_VERSION,
    DocumentVersion,
    EvidenceSpan,
    derive_reliability,
    derive_strength,
    evidence_hash,
    record_observation,
    set_document_status,
    write_document_version,
)
from product_hunter.db.migrations import migrate
from product_hunter.db.model_routing import (
    RunProvenance,
    record_catalog,
    record_model_executions,
    record_selection,
)
from product_hunter.db.outbox import enqueue_projection
from product_hunter.documents import Document
from product_hunter.extraction import ExtractedCandidate, ExtractionCall, ExtractionReject
from product_hunter.fetch import FetchRecord, utcnow
from product_hunter.sources import SourceConfig, SourceRegistry

T = TypeVar("T", bound=BaseModel)

# The manifest count that describes each run artifact, and the file that count
# is read from. `accepted` is a count derived from `candidates.jsonl`, so two
# manifest keys are checked against one file.
_MANIFEST_ARTIFACTS = {
    "documents": "documents.jsonl",
    "candidates": "candidates.jsonl",
    "calls": "calls.jsonl",
    "accepted": "candidates.jsonl",
    "rejected": "rejects.jsonl",
}

# shortcut: `pain_points.jsonl` is not completeness-checked at import time
# because the import never reads it; a divergent sidecar is caught later by
# the artifact hash comparison on replay.


class StoreError(Exception):
    """The store could not accept the input."""


class StoreConflict(StoreError):
    """An existing row disagrees with the incoming content for the same id."""


@dataclass(frozen=True)
class ImportSummary:
    run_id: str
    sources: int = 0
    fetches: int = 0
    documents: int = 0
    evidence: int = 0
    pain_points: int = 0
    authors: int = 0
    communities: int = 0
    author_links: int = 0
    rejects: int = 0
    artifacts: int = 0
    projections: int = 0


@dataclass(frozen=True)
class DocumentImport:
    document: bool
    community: bool
    author: bool
    author_link: bool


@dataclass(frozen=True)
class DocumentVersionImport:
    """The result of putting a document into the store.

    ``document`` says the document row was new, ``version_changed`` says the
    content moved to a new version, and ``content_version`` is the version
    the row holds now.
    """

    document: bool
    version_changed: bool
    content_version: int
    community: bool
    author: bool
    author_link: bool


class Store:
    """A thin authoritative-store facade over one SQLite connection."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    @classmethod
    def open(cls, db_path: str | Path, *, migrate_schema: bool = True) -> Store:
        conn = connect(db_path)
        if migrate_schema:
            migrate(conn)
        return cls(conn)

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> Store:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    # --- registry ---------------------------------------------------------

    def sync_registry(self, registry: SourceRegistry) -> int:
        """Upsert every configured source. Returns the number seen."""
        sources = registry.list()
        for source in sources:
            self._upsert_source(source)
        return len(sources)

    def _upsert_source(self, source: SourceConfig) -> None:
        now = utcnow().isoformat()
        self.conn.execute(
            """
            INSERT INTO sources (
                source_id, source_type, terms_status, allowed_use_json, enabled,
                reviewed_at, review_basis, collector, domains_json,
                rate_limit_json, robots_notes, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source_id) DO UPDATE SET
                source_type = excluded.source_type,
                terms_status = excluded.terms_status,
                allowed_use_json = excluded.allowed_use_json,
                enabled = excluded.enabled,
                reviewed_at = excluded.reviewed_at,
                review_basis = excluded.review_basis,
                collector = excluded.collector,
                domains_json = excluded.domains_json,
                rate_limit_json = excluded.rate_limit_json,
                robots_notes = excluded.robots_notes,
                updated_at = excluded.updated_at
            """,
            (
                source.source_id,
                source.source_type,
                source.terms_status.value,
                json.dumps([use.value for use in source.allowed_use]),
                int(source.enabled),
                source.reviewed_at.isoformat() if source.reviewed_at else None,
                source.review_basis,
                source.collector.value,
                json.dumps(source.domains),
                source.rate_limit.model_dump_json(),
                source.robots_or_api_notes,
                now,
                now,
            ),
        )

    # --- captures and documents ------------------------------------------

    def import_fetch(self, fetch: FetchRecord) -> bool:
        """Insert a fetch once. Returns True when the row was new."""
        self._require_source(fetch.source_id)
        existing = self.conn.execute(
            "SELECT raw_sha256, raw_path, final_url, status FROM fetches "
            "WHERE fetch_id = ?",
            (fetch.fetch_id,),
        ).fetchone()
        if existing is not None:
            if (
                existing["raw_sha256"] != fetch.raw_sha256
                or existing["raw_path"] != fetch.raw_path
                or existing["final_url"] != fetch.final_url
                or existing["status"] != fetch.status
            ):
                raise StoreConflict(
                    f"fetch {fetch.fetch_id!r} already exists with different content"
                )
            return False
        self.conn.execute(
            """
            INSERT INTO fetches (
                fetch_id, source_id, requested_url, final_url, status,
                content_type, raw_sha256, raw_path, bytes, fetched_at, etag,
                last_modified, not_modified, terms_status, allowed_use_json,
                review_basis, user_agent, latency_ms, retry_count, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                fetch.fetch_id,
                fetch.source_id,
                fetch.requested_url,
                fetch.final_url,
                fetch.status,
                fetch.content_type,
                fetch.raw_sha256,
                fetch.raw_path,
                fetch.bytes,
                fetch.fetched_at,
                fetch.etag,
                fetch.last_modified,
                int(fetch.not_modified),
                fetch.terms_status,
                json.dumps(fetch.allowed_use),
                fetch.review_basis,
                fetch.user_agent,
                fetch.latency_ms,
                fetch.retry_count,
                utcnow().isoformat(),
            ),
        )
        return True

    def import_document(self, document: Document) -> DocumentImport:
        """Insert a document once and build its community/author links."""
        community_id, community_new = self._ensure_community(document)
        author_id, author_new = self._ensure_author(document)
        existing = self.conn.execute(
            """
            SELECT text_sha256, raw_sha256, parser_version, text_path,
                   community_id, title, author, published_at, url, content_type,
                   live_status, fetch_id
            FROM documents WHERE doc_id = ?
            """,
            (document.doc_id,),
        ).fetchone()
        if existing is not None:
            if (
                existing["text_sha256"] != document.text_sha256
                or existing["raw_sha256"] != document.raw_sha256
                or existing["parser_version"] != document.parser_version
                or existing["text_path"] != document.text_path
                or existing["community_id"] != community_id
                or existing["title"] != document.title
                or existing["author"] != document.author
                or existing["published_at"] != document.published_at
                or existing["url"] != document.url
                or existing["content_type"] != document.content_type
            ):
                raise StoreConflict(
                    f"document {document.doc_id!r} already exists with different content"
                )
            # A new capture is a fresh observation of the source, so the
            # source's status wins. A replay of the same capture is not an
            # observation, so the stored status stands.
            stored = str(existing["live_status"] or "live")
            if document.fetch_id != existing["fetch_id"]:
                effective = document.live_status
            else:
                effective = stored
            if effective != stored:
                # The document and its spans never disagree about liveness.
                set_document_status(self.conn, document.doc_id, effective)
            self.conn.execute(
                "UPDATE documents SET last_seen_at = ? WHERE doc_id = ?",
                (utcnow().isoformat(), document.doc_id),
            )
            link_new = self._ensure_author_link(document, author_id)
            return DocumentImport(
                document=False,
                community=community_new,
                author=author_new,
                author_link=link_new,
            )
        self.conn.execute(
            """
            INSERT INTO documents (
                doc_id, fetch_id, source_id, community_id, url, title, author,
                published_at, text_sha256, text_path, raw_path, raw_sha256,
                parser_version, content_type, live_status, first_seen_at,
                last_seen_at, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                document.doc_id,
                document.fetch_id,
                document.source_id,
                community_id,
                document.url,
                document.title,
                document.author,
                document.published_at,
                document.text_sha256,
                document.text_path,
                document.raw_path,
                document.raw_sha256,
                document.parser_version,
                document.content_type,
                document.live_status,
                utcnow().isoformat(),
                utcnow().isoformat(),
                utcnow().isoformat(),
            ),
        )
        link_new = self._ensure_author_link(document, author_id)
        return DocumentImport(
            document=True,
            community=community_new,
            author=author_new,
            author_link=link_new,
        )

    def import_document_version(
        self, document: Document, *, reason: str
    ) -> DocumentVersionImport:
        """Put a document into the store, or move it to a new content version.

        A document that reappears with the same text is a no-op: only
        ``last_seen_at`` moves. A document that reappears with different text
        is a re-capture: the row moves to the next content version, and the
        version it leaves behind stays in ``document_versions``.
        """
        community_id, community_new = self._ensure_community(document)
        author_id, author_new = self._ensure_author(document)
        now = utcnow().isoformat()
        existing = self.conn.execute(
            """
            SELECT content_version, text_sha256, raw_sha256, raw_path, text_path,
                   title, published_at, url, live_status, fetch_id
            FROM documents WHERE doc_id = ?
            """,
            (document.doc_id,),
        ).fetchone()
        if existing is None:
            self.conn.execute(
                """
                INSERT INTO documents (
                    doc_id, fetch_id, source_id, community_id, url, title, author,
                    published_at, text_sha256, text_path, raw_path, raw_sha256,
                    parser_version, content_type, content_version, live_status,
                    first_seen_at, last_seen_at, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    document.doc_id,
                    document.fetch_id,
                    document.source_id,
                    community_id,
                    document.url,
                    document.title,
                    document.author,
                    document.published_at,
                    document.text_sha256,
                    document.text_path,
                    document.raw_path,
                    document.raw_sha256,
                    document.parser_version,
                    document.content_type,
                    1,
                    document.live_status,
                    now,
                    now,
                    now,
                ),
            )
            write_document_version(
                self.conn,
                _document_version(document, 1, reason),
                now=utcnow(),
            )
            link_new = self._ensure_author_link(document, author_id)
            return DocumentVersionImport(
                document=True,
                version_changed=False,
                content_version=1,
                community=community_new,
                author=author_new,
                author_link=link_new,
            )

        content_version = int(existing["content_version"])
        if existing["text_sha256"] == document.text_sha256:
            # The same text seen again: the document is the same document.
            stored = str(existing["live_status"] or "live")
            # A new capture is a fresh observation of the source, so the
            # source's status wins. A replay of the same capture is not an
            # observation, so the stored status stands.
            if document.fetch_id != existing["fetch_id"]:
                effective = document.live_status
            else:
                effective = stored
            if effective != stored:
                # The document and its spans never disagree about liveness.
                set_document_status(self.conn, document.doc_id, effective)
            self.conn.execute(
                "UPDATE documents SET last_seen_at = ? WHERE doc_id = ?",
                (now, document.doc_id),
            )
            write_document_version(
                self.conn,
                _document_version(document, content_version, reason),
                now=utcnow(),
            )
            link_new = self._ensure_author_link(document, author_id)
            return DocumentVersionImport(
                document=False,
                version_changed=False,
                content_version=content_version,
                community=community_new,
                author=author_new,
                author_link=link_new,
            )

        # The text changed: a re-capture of the same document.
        # The fetch follows the text only when the new fetch is in the store;
        # a document never points at a fetch that is not there.
        fetch_id = document.fetch_id
        if (
            self.conn.execute(
                "SELECT 1 FROM fetches WHERE fetch_id = ?", (fetch_id,)
            ).fetchone()
            is None
        ):
            fetch_id = str(existing["fetch_id"])
        self.conn.execute(
            """
            UPDATE documents
            SET content_version = ?, text_sha256 = ?, text_path = ?, raw_path = ?,
                raw_sha256 = ?, title = ?, published_at = ?, url = ?, fetch_id = ?,
                last_seen_at = ?
            WHERE doc_id = ?
            """,
            (
                content_version + 1,
                document.text_sha256,
                document.text_path,
                document.raw_path,
                document.raw_sha256,
                document.title,
                document.published_at,
                document.url,
                fetch_id,
                now,
                document.doc_id,
            ),
        )
        # The liveness of the document and of its spans never disagree. The
        # status is not written on the row above, because a status that only
        # reached the document would leave the spans of the same document
        # saying something else.
        if document.live_status != str(existing["live_status"] or "live"):
            set_document_status(
                self.conn, document.doc_id, document.live_status, now=utcnow()
            )
        # The version the document leaves behind stays in the history.
        write_document_version(
            self.conn,
            DocumentVersion(
                doc_id=document.doc_id,
                content_version=content_version,
                text_sha256=str(existing["text_sha256"]),
                raw_sha256=existing["raw_sha256"],
                text_path=existing["text_path"],
                title=existing["title"],
                published_at=existing["published_at"],
                live_status=str(existing["live_status"] or "live"),
                reason=reason,
            ),
            now=utcnow(),
        )
        write_document_version(
            self.conn,
            _document_version(document, content_version + 1, reason),
            now=utcnow(),
        )
        link_new = self._ensure_author_link(document, author_id)
        return DocumentVersionImport(
            document=False,
            version_changed=True,
            content_version=content_version + 1,
            community=community_new,
            author=author_new,
            author_link=link_new,
        )

    def _require_source(self, source_id: str) -> None:
        row = self.conn.execute(
            "SELECT 1 FROM sources WHERE source_id = ?", (source_id,)
        ).fetchone()
        if row is None:
            raise StoreError(f"source {source_id!r} is not in the store; sync the registry first")

    def _ensure_community(self, document: Document) -> tuple[str, bool]:
        name, url = _community_for(document)
        community_id = _hash_id("co_", f"{document.source_id}|{name}")
        existing = self.conn.execute(
            "SELECT 1 FROM communities WHERE community_id = ?", (community_id,)
        ).fetchone()
        self.conn.execute(
            """
            INSERT INTO communities (community_id, source_id, name, url, created_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(community_id) DO UPDATE SET
                name = excluded.name,
                url = excluded.url
            """,
            (community_id, document.source_id, name, url, utcnow().isoformat()),
        )
        return community_id, existing is None

    def _ensure_author(self, document: Document) -> tuple[str | None, bool]:
        handle = (document.author or "").strip()
        if not handle:
            return None, False
        author_id = _hash_id("au_", f"{document.source_id}|{handle}")
        existing = self.conn.execute(
            "SELECT 1 FROM authors WHERE author_id = ?", (author_id,)
        ).fetchone()
        profile_url = _profile_url(document.source_id, handle)
        self.conn.execute(
            """
            INSERT INTO authors (
                author_id, source_id, handle, display_name, profile_url, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(author_id) DO UPDATE SET
                display_name = excluded.display_name,
                profile_url = excluded.profile_url
            """,
            (author_id, document.source_id, handle, handle, profile_url, utcnow().isoformat()),
        )
        return author_id, existing is None

    def _ensure_author_link(self, document: Document, author_id: str | None) -> bool:
        if author_id is None:
            return False
        link_id = _hash_id("al_", f"{author_id}|{document.doc_id}|declared")
        link_existing = self.conn.execute(
            "SELECT 1 FROM author_links WHERE link_id = ?", (link_id,)
        ).fetchone()
        self.conn.execute(
            """
            INSERT OR IGNORE INTO author_links (
                link_id, author_id, doc_id, confidence, basis, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (link_id, author_id, document.doc_id, 1.0, "declared", utcnow().isoformat()),
        )
        return link_existing is None

    # --- evidence and pain points ----------------------------------------

    def import_candidate(self, candidate: ExtractedCandidate) -> tuple[bool, bool]:
        """Insert an evidence span and, when accepted, a pain point.

        Returns ``(evidence_new, pain_point_new)``. A non-accepted candidate
        contributes its evidence span but no pain point.
        """
        evidence_new = self._insert_evidence(candidate)
        if not candidate.accepted:
            return evidence_new, False
        pain_new = self.insert_pain_point(candidate)
        return evidence_new, pain_new

    def insert_pain_point(
        self,
        candidate: ExtractedCandidate,
        *,
        basis: str = "extraction",
        confidence: float = 0.0,
        created_by: str | None = None,
    ) -> bool:
        """Insert the pain point of an accepted candidate, and its evidence link.

        Returns True when the pain point row was new. The span it points at
        has to be in the store already: the database refuses a pain point
        that no verified evidence span backs.
        """
        if not candidate.pain_id or not candidate.normalized_key:
            raise StoreConflict(
                f"accepted candidate {candidate.candidate_id!r} has no pain id or key"
            )
        existing = self.conn.execute(
            """
            SELECT span_id, doc_id, domain, actor, action, object, consequence,
                   severity, frequency, pain_statement, normalized_key,
                   normalization_version, provider, model, extractor_version,
                   prompt_version, accepted
            FROM pain_points WHERE pain_id = ?
            """,
            (candidate.pain_id,),
        ).fetchone()
        if existing is not None:
            if (
                existing["span_id"] != candidate.span_id
                or existing["doc_id"] != candidate.doc_id
                or existing["domain"] != candidate.domain
                or existing["actor"] != candidate.actor
                or existing["action"] != candidate.action
                or existing["object"] != candidate.object
                or existing["consequence"] != candidate.consequence
                or existing["severity"] != candidate.severity
                or existing["frequency"] != candidate.frequency.value
                or existing["pain_statement"] != candidate.pain_statement
                or existing["normalized_key"] != candidate.normalized_key
                or existing["normalization_version"] != candidate.normalization_version
                or existing["provider"] != candidate.provider
                or existing["model"] != candidate.model
                or existing["extractor_version"] != candidate.extractor_version
                or existing["prompt_version"] != candidate.prompt_version
                or existing["accepted"] != 1
            ):
                raise StoreConflict(
                    f"pain point {candidate.pain_id!r} already exists with different content"
                )
            pain_new = False
        else:
            self.conn.execute(
                """
                INSERT INTO pain_points (
                    pain_id, span_id, doc_id, domain, actor, action, object,
                    consequence, severity, frequency, pain_statement,
                    normalized_key, normalization_version, provider, model,
                    extractor_version, prompt_version, accepted, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    candidate.pain_id,
                    candidate.span_id,
                    candidate.doc_id,
                    candidate.domain,
                    candidate.actor,
                    candidate.action,
                    candidate.object,
                    candidate.consequence,
                    candidate.severity,
                    candidate.frequency.value,
                    candidate.pain_statement,
                    candidate.normalized_key,
                    candidate.normalization_version,
                    candidate.provider,
                    candidate.model,
                    candidate.extractor_version,
                    candidate.prompt_version,
                    1,
                    candidate.created_at,
                ),
            )
            pain_new = True
        self.conn.execute(
            """
            INSERT OR IGNORE INTO evidence_pain_links (
                pain_id, span_id, basis, confidence, created_by, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                candidate.pain_id,
                candidate.span_id,
                basis,
                confidence,
                created_by,
                utcnow().isoformat(),
            ),
        )
        return pain_new

    def _insert_evidence(self, candidate: ExtractedCandidate) -> bool:
        derived = self._derived_facts(candidate)
        document = self.conn.execute(
            "SELECT live_status, content_version FROM documents WHERE doc_id = ?",
            (candidate.doc_id,),
        ).fetchone()
        live_status = (
            str(document["live_status"] or "live")
            if document is not None
            else "unknown"
        )
        existing = self.conn.execute(
            """
            SELECT doc_id, quote, char_start, char_end, match_mode, model_quote,
                   occurrence, provider, model, extractor_version, prompt_version,
                   normalization_version, raw_path, raw_sha256, text_sha256,
                   evidence_hash
            FROM evidence WHERE span_id = ?
            """,
            (candidate.span_id,),
        ).fetchone()
        if existing is not None:
            if (
                existing["doc_id"] != candidate.doc_id
                or existing["quote"] != candidate.quote
                or existing["char_start"] != candidate.char_start
                or existing["char_end"] != candidate.char_end
                or existing["match_mode"] != candidate.match_mode
                or existing["model_quote"] != candidate.model_quote
                or existing["occurrence"] != candidate.occurrence
                or existing["provider"] != candidate.provider
                or existing["model"] != candidate.model
                or existing["extractor_version"] != candidate.extractor_version
                or existing["prompt_version"] != candidate.prompt_version
                or existing["normalization_version"] != candidate.normalization_version
                or existing["raw_path"] != candidate.raw_path
                or existing["raw_sha256"] != candidate.raw_sha256
                or existing["text_sha256"] != candidate.text_sha256
            ):
                raise StoreConflict(
                    f"evidence {candidate.span_id!r} already exists with different content"
                )
            if existing["evidence_hash"] is None:
                # A row written before this wave: the derived facts are filled
                # in now, and they are not part of the conflict comparison.
                self._write_derived_facts(candidate.span_id, derived)
            return False
        now = utcnow().isoformat()
        self.conn.execute(
            """
            INSERT INTO evidence (
                span_id, doc_id, quote, char_start, char_end, match_mode,
                model_quote, occurrence, provider, model, extractor_version,
                prompt_version, normalization_version, raw_path, raw_sha256,
                text_sha256, evidence_hash, strength, strength_score,
                reliability, reliability_score, derivation_version, live_status,
                observation_count, first_seen_at, last_seen_at, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                candidate.span_id,
                candidate.doc_id,
                candidate.quote,
                candidate.char_start,
                candidate.char_end,
                candidate.match_mode,
                candidate.model_quote,
                candidate.occurrence,
                candidate.provider,
                candidate.model,
                candidate.extractor_version,
                candidate.prompt_version,
                candidate.normalization_version,
                candidate.raw_path,
                candidate.raw_sha256,
                candidate.text_sha256,
                derived["evidence_hash"],
                derived["strength"],
                derived["strength_score"],
                derived["reliability"],
                derived["reliability_score"],
                DERIVATION_VERSION,
                live_status,
                1,
                now,
                now,
                candidate.created_at,
            ),
        )
        # The sighting is recorded with the row, so the observation count and
        # the observation table agree.
        record_observation(self.conn, _span_from_candidate(candidate), now=utcnow())
        return True

    def _derived_facts(self, candidate: ExtractedCandidate) -> dict[str, object]:
        """The derived facts of one evidence row: its hash, strength, and
        reliability. They are facts about the evidence, not about the model
        that proposed it."""
        reliability, reliability_score = derive_reliability(
            self.conn, candidate.source_id
        )
        strength, strength_score = derive_strength(
            candidate.pain_validity.value, candidate.accepted
        )
        return {
            "evidence_hash": evidence_hash(candidate.quote),
            "strength": strength,
            "strength_score": strength_score,
            "reliability": reliability,
            "reliability_score": reliability_score,
        }

    def _write_derived_facts(
        self, span_id: str, derived: dict[str, object]
    ) -> None:
        """Fill in the derived facts of a row that was written without them."""
        now = utcnow().isoformat()
        self.conn.execute(
            """
            UPDATE evidence
            SET evidence_hash = ?, strength = ?, strength_score = ?,
                reliability = ?, reliability_score = ?, derivation_version = ?,
                first_seen_at = COALESCE(first_seen_at, ?),
                last_seen_at = COALESCE(last_seen_at, ?)
            WHERE span_id = ?
            """,
            (
                derived["evidence_hash"],
                derived["strength"],
                derived["strength_score"],
                derived["reliability"],
                derived["reliability_score"],
                DERIVATION_VERSION,
                now,
                now,
                span_id,
            ),
        )

    # --- one run ----------------------------------------------------------

    def import_run(
        self,
        run_dir: str | Path,
        *,
        fetch_log: str | Path,
        registry: SourceRegistry,
        provenance: RunProvenance | None = None,
    ) -> ImportSummary:
        """Import one ``data/processed/<run_id>`` directory in a transaction.

        The routing state the run recorded is imported with it, in the same
        transaction: the catalogue the run decided against, the model the
        role resolved to, and the model calls. A run without that state
        imports as it is, and nothing extra is written.
        """
        root = Path(run_dir)
        manifest = _read_manifest(root)
        run_id = str(manifest.get("run_id") or root.name)
        documents = _read_jsonl(root / "documents.jsonl", Document)
        candidates = _read_jsonl(root / "candidates.jsonl", ExtractedCandidate)
        rejects = _read_jsonl(root / "rejects.jsonl", ExtractionReject)
        calls = _read_jsonl(root / "calls.jsonl", ExtractionCall)
        fetch_by_id = {
            fetch.fetch_id: fetch
            for fetch in _read_jsonl(Path(fetch_log), FetchRecord)
        }
        _check_manifest_counts(
            manifest,
            {
                "documents": len(documents),
                "candidates": len(candidates),
                "calls": len(calls),
                "accepted": sum(1 for candidate in candidates if candidate.accepted),
                "rejected": len(rejects),
            },
        )

        counts = {
            "run_id": run_id,
            "sources": 0,
            "fetches": 0,
            "documents": 0,
            "evidence": 0,
            "pain_points": 0,
            "authors": 0,
            "communities": 0,
            "author_links": 0,
            "rejects": 0,
            "artifacts": 0,
            "projections": 0,
        }
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            self._upsert_run(manifest, run_id)
            counts["sources"] = self.sync_registry(registry)
            for document in documents:
                fetch = fetch_by_id.get(document.fetch_id)
                if fetch is None:
                    raise StoreError(
                        f"document {document.doc_id!r} references fetch "
                        f"{document.fetch_id!r}, which is not in the fetch log"
                    )
                if fetch.source_id != document.source_id:
                    raise StoreConflict(
                        f"document {document.doc_id!r} source {document.source_id!r} "
                        f"does not match fetch source {fetch.source_id!r}"
                    )
                if fetch.raw_sha256 != document.raw_sha256:
                    raise StoreConflict(
                        f"document {document.doc_id!r} raw hash does not match its fetch"
                    )
                counts["fetches"] += int(self.import_fetch(fetch))
                imported = self.import_document(document)
                counts["documents"] += int(imported.document)
                counts["communities"] += int(imported.community)
                counts["authors"] += int(imported.author)
                counts["author_links"] += int(imported.author_link)
            for candidate in candidates:
                evidence_new, pain_new = self.import_candidate(candidate)
                counts["evidence"] += int(evidence_new)
                counts["pain_points"] += int(pain_new)
                if not candidate.accepted:
                    self._insert_event(
                        run_id,
                        "extraction_candidate",
                        candidate.candidate_id,
                        candidate.model_dump_json(),
                    )
            for reject in rejects:
                if self._insert_event(
                    run_id,
                    "extraction_reject",
                    reject.reject_id,
                    reject.model_dump_json(),
                ):
                    counts["rejects"] += 1
            for call in calls:
                self._insert_event(
                    run_id,
                    "extraction_call",
                    call.call_id,
                    call.model_dump_json(),
                )
            counts["artifacts"] = self._record_artifacts(root, run_id)
            self._write_provenance(provenance, manifest)
            self._insert_event(
                run_id,
                "run_imported",
                run_id,
                json.dumps(
                    {
                        "documents": len(documents),
                        "candidates": len(candidates),
                        "rejects": len(rejects),
                        "calls": len(calls),
                    },
                    sort_keys=True,
                ),
            )
            counts["projections"] = int(self._enqueue_projection(run_id))
            self.conn.execute("COMMIT")
        except sqlite3.IntegrityError as exc:
            # The database refused a row, most often because a trigger found a
            # pain point without a verified span. The run is not imported, and
            # the caller gets a conflict, not a raw SQLite error.
            self.conn.execute("ROLLBACK")
            raise StoreConflict(
                f"the run {run_id!r} could not be imported: {exc}"
            ) from exc
        except BaseException:
            self.conn.execute("ROLLBACK")
            raise
        return ImportSummary(**counts)

    def _write_provenance(
        self, provenance: RunProvenance | None, manifest: dict[str, object]
    ) -> None:
        """Write the routing state of the run, in the import's transaction.

        The writes join the transaction the import already owns, so the run
        and its provenance are all there or none of them are.
        """
        if provenance is None:
            return
        if provenance.catalog is not None:
            record_catalog(self.conn, provenance.catalog)
        config_version = _routing_config_version(manifest)
        for selection in provenance.selections:
            record_selection(self.conn, selection, config_version=config_version)
        record_model_executions(self.conn, provenance.executions)

    def _upsert_run(self, manifest: dict[str, object], run_id: str) -> None:
        started_at = _text(manifest.get("started_at")) or utcnow().isoformat()
        completed_at = _text(manifest.get("completed_at"))
        provider = _text(manifest.get("provider")) or ""
        model = _text(manifest.get("model")) or ""
        agent_version = _text(manifest.get("extractor_version")) or ""
        model_policy = f"{provider}/{model}" if provider or model else ""
        budget = {
            "limits": manifest.get("limits", {}),
            "prompt_version": manifest.get("prompt_version", ""),
            "normalization_version": manifest.get("normalization_version", ""),
        }
        now = utcnow().isoformat()
        existing = self.conn.execute(
            "SELECT agent_version, model_policy_version FROM research_runs WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        if existing is not None:
            if (
                existing["agent_version"] != agent_version
                or existing["model_policy_version"] != model_policy
            ):
                raise StoreConflict(
                    f"run {run_id!r} already exists with different model provenance"
                )
            self.conn.execute(
                "UPDATE research_runs SET status = ?, completed_at = ?, updated_at = ? "
                "WHERE run_id = ?",
                ("completed", completed_at, now, run_id),
            )
            return
        self.conn.execute(
            """
            INSERT INTO research_runs (
                run_id, public_id, objective, original_request, status,
                current_stage, current_step, priority, created_at, started_at,
                updated_at, last_heartbeat, completed_at, last_error,
                contract_version, agent_version, model_policy_version,
                chat_id, workspace_id, parent_run_id, resume_count,
                progress_percent, lease_epoch, budget_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                run_id,
                "Phase 1 extraction run",
                "",
                "completed",
                "PAIN_EXTRACTION",
                "imported",
                0,
                started_at,
                started_at,
                now,
                now,
                completed_at,
                None,
                "phase2-v1",
                agent_version,
                model_policy,
                None,
                None,
                None,
                0,
                100.0,
                0,
                json.dumps(budget, sort_keys=True),
            ),
        )

    def _record_artifacts(self, root: Path, run_id: str) -> int:
        names = (
            "manifest.json",
            "documents.jsonl",
            "candidates.jsonl",
            "pain_points.jsonl",
            "rejects.jsonl",
            "calls.jsonl",
            "model_executions.jsonl",
            "model_catalog.json",
        )
        inserted = 0
        for name in names:
            path = root / name
            if not path.is_file():
                continue
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            existing = self.conn.execute(
                "SELECT content_hash FROM research_artifacts "
                "WHERE run_id = ? AND path_or_uri = ?",
                (run_id, name),
            ).fetchone()
            if existing is not None:
                if existing["content_hash"] != digest:
                    raise StoreConflict(
                        f"artifact {name!r} of run {run_id!r} changed after import"
                    )
                continue
            artifact_id = _hash_id("art_", f"{run_id}|{name}|{digest}")
            self.conn.execute(
                """
                INSERT INTO research_artifacts (
                    artifact_id, run_id, artifact_type, path_or_uri,
                    content_hash, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (artifact_id, run_id, path.suffix.lstrip("."), name, digest, utcnow().isoformat()),
            )
            inserted += 1
        return inserted

    def _insert_event(
        self, run_id: str, event_type: str, entity_id: str, payload: str
    ) -> bool:
        event_id = _hash_id("evt_", f"{run_id}|{event_type}|{entity_id}")
        existing = self.conn.execute(
            "SELECT payload_json FROM research_events WHERE event_id = ?",
            (event_id,),
        ).fetchone()
        if existing is not None:
            if existing["payload_json"] != payload:
                raise StoreConflict(
                    f"event {event_id!r} already exists with a different payload"
                )
            return False
        self.conn.execute(
            """
            INSERT INTO research_events (
                event_id, run_id, event_type, stage, tool, agent,
                payload_ref, payload_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_id,
                run_id,
                event_type,
                "PAIN_EXTRACTION",
                None,
                None,
                None,
                payload,
                utcnow().isoformat(),
            ),
        )
        return True

    def _enqueue_projection(self, run_id: str) -> bool:
        key = f"run_imported:{run_id}"
        existing = self.conn.execute(
            "SELECT 1 FROM projection_tasks WHERE idempotency_key = ?", (key,)
        ).fetchone()
        if existing is not None:
            return False
        enqueue_projection(
            self.conn,
            target="obsidian",
            entity_type="run",
            entity_id=run_id,
            entity_version=run_id,
            idempotency_key=key,
            payload={"run_id": run_id},
        )
        return True

    # --- checks -----------------------------------------------------------

    def integrity_check(self) -> str:
        return str(self.conn.execute("PRAGMA integrity_check").fetchone()[0])

    def foreign_key_violations(self) -> list[sqlite3.Row]:
        return list(self.conn.execute("PRAGMA foreign_key_check").fetchall())


def _read_manifest(root: Path) -> dict[str, object]:
    path = root / "manifest.json"
    if not path.is_file():
        raise StoreError(f"run manifest is missing: {path}")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise StoreError(f"run manifest is unreadable: {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise StoreError(f"run manifest must be an object: {path}")
    return raw


def _read_jsonl(path: Path, model: type[T]) -> list[T]:
    if not path.is_file():
        raise StoreError(f"run artifact is missing: {path}")
    out: list[T] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            out.append(model.model_validate_json(line))
        except Exception as exc:
            raise StoreError(f"{path}: line {number}: {exc}") from exc
    return out


def _check_manifest_counts(
    manifest: dict[str, object], observed: dict[str, int]
) -> None:
    """Refuse a run whose artifacts hold a different number of records than the manifest says.

    A truncated file parses cleanly and hashes cleanly, so the declared count
    is the only thing that can tell a complete run from a partial one. A
    manifest that declares no count, or a count that is not an integer, is
    skipped: the check only refuses a disagreement it can prove.
    """
    for key, name in _MANIFEST_ARTIFACTS.items():
        declared = manifest.get(key)
        if not isinstance(declared, int) or isinstance(declared, bool):
            continue
        count = observed[key]
        if count == declared:
            continue
        raise StoreError(
            f"the manifest declares {key}={declared} but the run has {count} {key}; "
            f"checked against {name!r}"
        )


def _routing_config_version(manifest: dict[str, object]) -> str:
    """The routing config version the run recorded, or an empty string.

    A run that recorded no routing has no version, and the binding it leaves
    says so.
    """
    block = manifest.get("model_routing")
    if not isinstance(block, dict):
        return ""
    version = block.get("config_version")
    return version if isinstance(version, str) else ""


def _span_from_candidate(candidate: ExtractedCandidate) -> EvidenceSpan:
    """The span a candidate describes, as the evidence layer writes it."""
    return EvidenceSpan(
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
    )


def _document_version(
    document: Document, content_version: int, reason: str
) -> DocumentVersion:
    """One observed version of a document, as the history records it."""
    return DocumentVersion(
        doc_id=document.doc_id,
        content_version=content_version,
        text_sha256=document.text_sha256,
        raw_sha256=document.raw_sha256,
        text_path=document.text_path,
        title=document.title,
        published_at=document.published_at,
        live_status=document.live_status,
        reason=reason,
    )


def _community_for(document: Document) -> tuple[str, str | None]:
    if document.source_id == "hn_algolia":
        return "Hacker News", "https://news.ycombinator.com"
    if document.source_id == "github_issues":
        parsed = urlparse(document.url)
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) >= 2:
            name = f"{parts[0]}/{parts[1]}"
            return name, f"https://github.com/{name}"
    return document.source_id, None


def _profile_url(source_id: str, handle: str) -> str | None:
    if source_id == "github_issues":
        return f"https://github.com/{handle}"
    if source_id == "hn_algolia":
        return f"https://news.ycombinator.com/user?id={handle}"
    return None


def _hash_id(prefix: str, basis: str) -> str:
    return prefix + hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16]


def _text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None

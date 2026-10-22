"""The independence store: read the authoritative state, write the relationships.

Every write is idempotent. The id of a row is a content hash, so recording the
same record twice is a no-op, and a changed record under the same id is a
conflict rather than an overwrite. The identity key is hashed before it is
written, so a raw identifier never reaches the database.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Self

from product_hunter.config import repo_root
from product_hunter.db.connection import connect
from product_hunter.db.migrations import migrate
from product_hunter.independence.identity import hash_identity_key
from product_hunter.independence.records import (
    AuthorCluster,
    AuthorIdentityLink,
    AuthorObservation,
    CommunityLink,
    CommunityObservation,
    ConcentrationVerdict,
    MentionObservation,
    PropagationEdge,
)


class IndependenceError(Exception):
    """The store could not accept the input."""


class IndependenceConflict(IndependenceError):
    """An existing row disagrees with the incoming content for the same id."""


# A scope is read this many documents per query, well under the SQLite
# parameter cap.
_DOC_CHUNK = 500


class IndependenceStore:
    """A thin facade over the independence tables of one SQLite connection."""

    def __init__(self, conn: sqlite3.Connection, *, root: Path | None = None) -> None:
        self.conn = conn
        self._root = root if root is not None else repo_root()

    @classmethod
    def open(
        cls,
        db_path: str | Path,
        *,
        migrate_schema: bool = True,
        root: Path | None = None,
    ) -> IndependenceStore:
        conn = connect(db_path)
        if migrate_schema:
            migrate(conn)
        return cls(conn, root=root)

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    # --- reads ------------------------------------------------------------

    def load_authors(self) -> list[AuthorObservation]:
        """Every account in the store, with the documents it produced."""
        rows = self.conn.execute(
            """
            SELECT a.author_id, a.source_id, a.handle, a.display_name, a.identity_key,
                   a.profile_url, a.first_seen_at, a.last_seen_at, a.deletion_status,
                   l.doc_id AS doc_id, d.community_id AS community_id
            FROM authors a
            LEFT JOIN author_links l ON l.author_id = a.author_id
            LEFT JOIN documents d ON d.doc_id = l.doc_id
            ORDER BY a.author_id, l.doc_id
            """
        ).fetchall()
        grouped: dict[str, list[sqlite3.Row]] = {}
        for row in rows:
            grouped.setdefault(row["author_id"], []).append(row)

        authors: list[AuthorObservation] = []
        for author_id in sorted(grouped):
            group = grouped[author_id]
            first = group[0]
            authors.append(
                AuthorObservation(
                    author_id=first["author_id"],
                    source_id=first["source_id"],
                    handle=first["handle"] or "",
                    display_name=first["display_name"],
                    identity_key=first["identity_key"],
                    profile_url=first["profile_url"],
                    community_ids=sorted(
                        {row["community_id"] for row in group if row["community_id"]}
                    ),
                    doc_ids=sorted({row["doc_id"] for row in group if row["doc_id"]}),
                    first_seen_at=first["first_seen_at"],
                    last_seen_at=first["last_seen_at"],
                    deletion_status=first["deletion_status"] or "active",
                )
            )
        return authors

    def load_communities(self) -> list[CommunityObservation]:
        """Every community in the store, with the alias it was mapped to."""
        aliases = {
            row["community_id"]: row["related_community_id"]
            for row in self.conn.execute(
                "SELECT community_id, related_community_id FROM community_links "
                "WHERE detection_method = 'explicit_alias'"
            ).fetchall()
        }
        rows = self.conn.execute(
            "SELECT community_id, source_id, name, url FROM communities "
            "ORDER BY community_id"
        ).fetchall()
        return [
            CommunityObservation(
                community_id=row["community_id"],
                source_id=row["source_id"],
                name=row["name"],
                url=row["url"],
                alias_of=aliases.get(row["community_id"]),
            )
            for row in rows
        ]

    def load_mentions(
        self, doc_ids: Sequence[str] | None = None
    ) -> list[MentionObservation]:
        """The mentions in the store, with their text, author, and spans."""
        wanted = sorted(set(doc_ids)) if doc_ids is not None else None
        if wanted is not None and not wanted:
            return []
        sql = """
            SELECT d.doc_id, d.source_id, d.community_id, d.url, d.text_sha256,
                   d.text_path, d.published_at, d.live_status, d.deleted_at,
                   l.author_id AS author_id
            FROM documents d
            LEFT JOIN author_links l ON l.doc_id = d.doc_id
        """
        rows: list[sqlite3.Row] = []
        if wanted is None:
            rows = self.conn.execute(sql + " ORDER BY d.doc_id, l.author_id").fetchall()
        else:
            # SQLite caps the bound parameters, so a large scope is read in
            # chunks rather than as one IN list.
            for start in range(0, len(wanted), _DOC_CHUNK):
                chunk = wanted[start : start + _DOC_CHUNK]
                rows.extend(
                    self.conn.execute(
                        sql
                        + " WHERE d.doc_id IN ("
                        + ",".join("?" for _ in chunk)
                        + ") ORDER BY d.doc_id, l.author_id",
                        tuple(chunk),
                    ).fetchall()
                )
        rows.sort(key=lambda row: row["doc_id"])

        grouped: dict[str, list[sqlite3.Row]] = {}
        for row in rows:
            grouped.setdefault(row["doc_id"], []).append(row)

        mentions: list[MentionObservation] = []
        for doc_id in sorted(grouped):
            group = grouped[doc_id]
            first = group[0]
            authors = sorted({row["author_id"] for row in group if row["author_id"]})
            mentions.append(
                MentionObservation(
                    doc_id=first["doc_id"],
                    source_id=first["source_id"],
                    community_id=first["community_id"],
                    author_id=authors[0] if authors else None,
                    url=first["url"] or "",
                    text=self._read_text(first["text_path"]),
                    text_sha256=first["text_sha256"] or "",
                    published_at=first["published_at"],
                    live_status=first["live_status"] or "live",
                    deleted_at=first["deleted_at"],
                    span_ids=self._span_ids(doc_id),
                )
            )
        return mentions

    # --- writes -----------------------------------------------------------

    def record_author(self, author: AuthorObservation) -> bool:
        """Write one account's identity fields, hashing the identity key.

        Returns True when the row was new. The handle and the display name
        belong to the collector, so this only owns the identity fields.

        The collector creates the row first, with no identity key, so this is
        an upsert: a missing key is filled, a seen window is widened, and
        ``deleted`` is final. A different key is two accounts claiming one id,
        which is a conflict.
        """
        key = hash_identity_key(author.identity_key) if author.identity_key else None
        existing = self.conn.execute(
            "SELECT identity_key, first_seen_at, last_seen_at, deletion_status "
            "FROM authors WHERE author_id = ?",
            (author.author_id,),
        ).fetchone()
        if existing is not None:
            stored_key = existing["identity_key"]
            if stored_key is not None and key is not None and stored_key != key:
                raise IndependenceConflict(
                    f"author {author.author_id!r} already exists with a different "
                    "identity key"
                )
            deletion_status = (
                "deleted" if "deleted" in (existing["deletion_status"], author.deletion_status)
                else author.deletion_status
            )
            self.conn.execute(
                "UPDATE authors SET identity_key = ?, first_seen_at = ?, "
                "last_seen_at = ?, deletion_status = ? WHERE author_id = ?",
                (
                    stored_key or key,
                    _earliest(existing["first_seen_at"], author.first_seen_at),
                    _latest(existing["last_seen_at"], author.last_seen_at),
                    deletion_status,
                    author.author_id,
                ),
            )
            return False
        self.conn.execute(
            """
            INSERT INTO authors (
                author_id, source_id, handle, display_name, profile_url, identity_key,
                first_seen_at, last_seen_at, deletion_status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                author.author_id,
                author.source_id,
                author.handle,
                author.display_name,
                author.profile_url,
                key,
                author.first_seen_at,
                author.last_seen_at,
                author.deletion_status,
            ),
        )
        return True

    def record_identity_link(self, link: AuthorIdentityLink) -> bool:
        """Write one author-to-author relation. Returns True when it was new."""
        self._require_author(link.author_id)
        self._require_author(link.related_author_id)
        payload = (
            link.relation,
            link.detection_method,
            link.confidence,
            json.dumps(link.signals, sort_keys=True),
            int(link.conflict),
            link.provenance.model_dump_json(),
            link.detected_at,
        )
        existing = self.conn.execute(
            "SELECT relation, detection_method, confidence, signals_json, conflict, "
            "provenance_json, detected_at FROM author_identity_links WHERE link_id = ?",
            (link.link_id,),
        ).fetchone()
        if existing is not None:
            if tuple(existing) != payload:
                raise IndependenceConflict(
                    f"identity link {link.link_id!r} already exists with different content"
                )
            return False
        self.conn.execute(
            """
            INSERT INTO author_identity_links (
                link_id, author_id, related_author_id, relation, detection_method,
                confidence, signals_json, conflict, provenance_json, detected_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                link.link_id,
                link.author_id,
                link.related_author_id,
                link.relation,
                link.detection_method,
                link.confidence,
                json.dumps(link.signals, sort_keys=True),
                int(link.conflict),
                link.provenance.model_dump_json(),
                link.detected_at,
            ),
        )
        return True

    def record_community_link(self, link: CommunityLink) -> bool:
        """Write one community-to-community relation. Returns True when it was new."""
        self._require_community(link.community_id)
        self._require_community(link.related_community_id)
        payload = (
            link.relation,
            link.detection_method,
            link.confidence,
            link.provenance.model_dump_json(),
            link.detected_at,
        )
        existing = self.conn.execute(
            "SELECT relation, detection_method, confidence, provenance_json, detected_at "
            "FROM community_links WHERE link_id = ?",
            (link.link_id,),
        ).fetchone()
        if existing is not None:
            if tuple(existing) != payload:
                raise IndependenceConflict(
                    f"community link {link.link_id!r} already exists with different content"
                )
            return False
        self.conn.execute(
            """
            INSERT INTO community_links (
                link_id, community_id, related_community_id, relation, detection_method,
                confidence, provenance_json, detected_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                link.link_id,
                link.community_id,
                link.related_community_id,
                link.relation,
                link.detection_method,
                link.confidence,
                link.provenance.model_dump_json(),
                link.detected_at,
            ),
        )
        return True

    def record_propagation_edge(self, edge: PropagationEdge) -> bool:
        """Write one propagation edge. Returns True when it was new."""
        payload = (
            edge.from_doc_id,
            edge.to_doc_id,
            edge.relation,
            edge.detection_method,
            edge.confidence,
            edge.provenance.model_dump_json(),
            edge.detected_at,
        )
        existing = self.conn.execute(
            "SELECT from_doc_id, to_doc_id, relation, detection_method, confidence, "
            "provenance_json, detected_at FROM propagation_edges WHERE edge_id = ?",
            (edge.edge_id,),
        ).fetchone()
        if existing is not None:
            if tuple(existing) != payload:
                raise IndependenceConflict(
                    f"propagation edge {edge.edge_id!r} already exists with different content"
                )
            return False
        self.conn.execute(
            """
            INSERT INTO propagation_edges (
                edge_id, from_doc_id, to_doc_id, relation, detection_method, confidence,
                provenance_json, from_span_id, to_span_id, source_id, detected_at, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                edge.edge_id,
                edge.from_doc_id,
                edge.to_doc_id,
                edge.relation,
                edge.detection_method,
                edge.confidence,
                edge.provenance.model_dump_json(),
                self._first_span(edge.from_doc_id),
                self._first_span(edge.to_doc_id),
                _source_of(edge.provenance.source_ids),
                edge.detected_at,
                edge.detected_at,
            ),
        )
        return True

    def record_author_clusters(
        self, clusters: Sequence[AuthorCluster], *, created_at: str
    ) -> int:
        """Write the identity groups and their members. Returns the new rows.

        A re-grouping replaces the old rows. The cluster id is a hash of the
        member list, so a group whose membership changed is a different
        group, and the old one is removed rather than left behind with a
        member row that names an author it no longer holds.

        This call writes the complete set of identity groups. An author whose
        cluster is not in that set is ungrouped, so a run that analysed only
        part of the corpus must not call it, or it clears the groups of the
        authors it did not analyse. The caller decides the scope.
        """
        incoming_ids = {cluster.cluster_id for cluster in clusters}
        # The clusters the affected authors currently point at. An author is
        # affected when it points at a cluster, so this is every group in play.
        previous_ids = {
            row["cluster_id"]
            for row in self.conn.execute(
                "SELECT DISTINCT cluster_id FROM authors WHERE cluster_id IS NOT NULL"
            ).fetchall()
        }
        stale_ids = previous_ids - incoming_ids

        written = 0
        for cluster in clusters:
            payload = (
                cluster.status,
                cluster.canonical_author_id,
                len(cluster.member_author_ids),
                cluster.confidence,
                json.dumps(cluster.basis, sort_keys=True),
            )
            existing = self.conn.execute(
                "SELECT status, canonical_author_id, member_count, confidence, basis_json "
                "FROM author_clusters WHERE cluster_id = ?",
                (cluster.cluster_id,),
            ).fetchone()
            if existing is not None:
                if tuple(existing) != payload:
                    raise IndependenceConflict(
                        f"author cluster {cluster.cluster_id!r} already exists with "
                        "different content"
                    )
            else:
                self.conn.execute(
                    """
                    INSERT INTO author_clusters (
                        cluster_id, status, canonical_author_id, member_count,
                        confidence, basis_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        cluster.cluster_id,
                        cluster.status,
                        cluster.canonical_author_id,
                        len(cluster.member_author_ids),
                        cluster.confidence,
                        json.dumps(cluster.basis, sort_keys=True),
                        created_at,
                    ),
                )
                written += 1
            for author_id in cluster.member_author_ids:
                self.conn.execute(
                    "INSERT OR IGNORE INTO author_cluster_members (cluster_id, author_id) "
                    "VALUES (?, ?)",
                    (cluster.cluster_id, author_id),
                )
                self.conn.execute(
                    "UPDATE authors SET cluster_id = ?, identity_confidence = ?, "
                    "identity_basis = ? WHERE author_id = ?",
                    (
                        cluster.cluster_id,
                        cluster.member_confidences.get(author_id, cluster.confidence),
                        json.dumps(cluster.basis, sort_keys=True),
                        author_id,
                    ),
                )
            # A member row that is no longer in the group is stale.
            members = set(cluster.member_author_ids)
            for row in self.conn.execute(
                "SELECT author_id FROM author_cluster_members WHERE cluster_id = ?",
                (cluster.cluster_id,),
            ).fetchall():
                if row["author_id"] not in members:
                    self.conn.execute(
                        "DELETE FROM author_cluster_members "
                        "WHERE cluster_id = ? AND author_id = ?",
                        (cluster.cluster_id, row["author_id"]),
                    )

        # An author whose previous cluster is not one of the clusters written
        # here no longer belongs to any group this run resolved.
        for row in self.conn.execute(
            "SELECT author_id, cluster_id FROM authors WHERE cluster_id IS NOT NULL"
        ).fetchall():
            if row["cluster_id"] not in incoming_ids:
                self.conn.execute(
                    "UPDATE authors SET cluster_id = NULL WHERE author_id = ?",
                    (row["author_id"],),
                )

        # The groups this run replaced are stale: their members moved, so the
        # member rows and the group row would name a group nobody is in.
        for cluster_id in sorted(stale_ids):
            still_pointed = self.conn.execute(
                "SELECT 1 FROM authors WHERE cluster_id = ? LIMIT 1", (cluster_id,)
            ).fetchone()
            if still_pointed is not None:
                continue
            self.conn.execute(
                "DELETE FROM author_cluster_members WHERE cluster_id = ?", (cluster_id,)
            )
            self.conn.execute(
                "DELETE FROM author_clusters WHERE cluster_id = ?", (cluster_id,)
            )
        return written

    def record_report(
        self, verdict: ConcentrationVerdict, *, scope_id: str, created_at: str
    ) -> bool:
        """Write one report. Returns True when it was new."""
        if not verdict.metrics._came_from_analysis():
            raise IndependenceError(
                "the metrics were not built by analyze(); the store will not write a "
                "number that no graph produced"
            )
        metrics_json = json.dumps(verdict.metrics.model_dump(mode="json"), sort_keys=True)
        violations_json = json.dumps(
            [violation.model_dump(mode="json") for violation in verdict.violations],
            sort_keys=True,
        )
        report_id = _hash_id(
            "ir_", f"{scope_id}|{verdict.metrics.policy_version}|{created_at}"
        )
        payload = (
            scope_id,
            verdict.metrics.policy_version,
            metrics_json,
            violations_json,
            int(verdict.satisfied),
        )
        existing = self.conn.execute(
            "SELECT scope_id, policy_version, metrics_json, violations_json, satisfied "
            "FROM independence_reports WHERE report_id = ?",
            (report_id,),
        ).fetchone()
        if existing is not None:
            if tuple(existing) != payload:
                raise IndependenceConflict(
                    f"report {report_id!r} already exists with different content"
                )
            return False
        self.conn.execute(
            """
            INSERT INTO independence_reports (
                report_id, scope_id, policy_version, metrics_json, violations_json,
                satisfied, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                report_id,
                scope_id,
                verdict.metrics.policy_version,
                metrics_json,
                violations_json,
                int(verdict.satisfied),
                created_at,
            ),
        )
        return True

    # --- helpers ----------------------------------------------------------

    def _require_author(self, author_id: str) -> None:
        """Refuse a link whose author is not in the table."""
        row = self.conn.execute(
            "SELECT 1 FROM authors WHERE author_id = ?", (author_id,)
        ).fetchone()
        if row is None:
            raise IndependenceError(f"no such author: {author_id!r}")

    def _require_community(self, community_id: str) -> None:
        """Refuse a link whose community is not in the table."""
        row = self.conn.execute(
            "SELECT 1 FROM communities WHERE community_id = ?", (community_id,)
        ).fetchone()
        if row is None:
            raise IndependenceError(f"no such community: {community_id!r}")

    def _span_ids(self, doc_id: str) -> list[str]:
        rows = self.conn.execute(
            "SELECT span_id FROM evidence WHERE doc_id = ? ORDER BY span_id", (doc_id,)
        ).fetchall()
        return [row["span_id"] for row in rows]

    def _first_span(self, doc_id: str) -> str | None:
        """The first evidence span of a document, when it has one."""
        spans = self._span_ids(doc_id)
        return spans[0] if spans else None

    def _read_text(self, text_path: str | None) -> str:
        """The parsed text of a document, or an empty string when it is gone.

        A stored path that leaves the repository root is refused: the path comes
        from the database, and the text it names is untrusted.
        """
        if not text_path:
            return ""
        root = self._root.resolve()
        try:
            path = (root / text_path).resolve()
        except OSError:
            return ""
        if path != root and root not in path.parents:
            return ""
        try:
            return path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return ""


def _earliest(stored: str | None, incoming: str | None) -> str | None:
    """The earlier of two ISO-8601 values; a missing value loses.

    The two are compared as instants, so a value with milliseconds and a value
    without order by time and not by codepoint.
    """
    if stored is None:
        return incoming
    if incoming is None:
        return stored
    left, right = _instant(stored), _instant(incoming)
    if left is None or right is None:
        return min(stored, incoming)
    try:
        return stored if left <= right else incoming
    except TypeError:
        # One carries a zone and the other does not, so the two instants are
        # not comparable; the text decides.
        return min(stored, incoming)


def _latest(stored: str | None, incoming: str | None) -> str | None:
    """The later of two ISO-8601 values; a missing value loses."""
    if stored is None:
        return incoming
    if incoming is None:
        return stored
    left, right = _instant(stored), _instant(incoming)
    if left is None or right is None:
        return max(stored, incoming)
    try:
        return stored if left >= right else incoming
    except TypeError:
        return max(stored, incoming)


def _instant(value: str) -> datetime | None:
    """A timestamp as a datetime, or None when it is missing or unparsable."""
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed


def _source_of(source_ids: list[str]) -> str | None:
    """The source an edge came from, when it came from exactly one."""
    return source_ids[0] if len(source_ids) == 1 else None


def _hash_id(prefix: str, basis: str) -> str:
    return prefix + hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16]

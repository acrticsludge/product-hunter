"""The authoritative rows, as the index sees them.

The reader turns the store's rows into the entities the index holds a vector
for. It reads SQLite and writes nothing: the store stays the source of truth
and the index is a projection of it.

A pain point is read into the clustering wave's own model, so the text that
gets embedded is the text the clustering wave compares. A row the model
refuses is skipped rather than fatal, because one bad row must not stop the
index of the rest.
"""

from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass, field

from pydantic import ValidationError

from product_hunter.clustering.problems import PainPoint, canonical_text
from product_hunter.normalization import Domain
from product_hunter.semantic.vectors import (
    ENTITY_CLUSTER,
    ENTITY_EVIDENCE,
    ENTITY_PAIN_POINT,
)

# The only live status. Anything else means the entity left the index.
_LIVE = "live"


@dataclass(frozen=True)
class Entity:
    """One thing the index can hold a vector for."""

    entity_id: str
    entity_type: str
    text: str
    content_hash: str
    source_id: str = ""
    community_id: str = ""
    domain: str = ""
    cluster_id: str = ""
    live_status: str = "live"
    created_at: str = ""
    updated_at: str = ""
    members: tuple[str, ...] = field(default=())


@dataclass(frozen=True)
class _Maps:
    """The memberships and the links, read once for a set of entities."""

    cluster_of_pain: dict[str, str]
    members_of_cluster: dict[str, tuple[str, ...]]
    cluster_of_span: dict[str, str]


class SemanticSource:
    """The authoritative rows, as the index sees them."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self._skipped: list[str] = []

    # --- the entities -------------------------------------------------------

    def evidence(self, span_ids: Sequence[str] | None = None) -> list[Entity]:
        """The evidence spans, with the text that gets embedded."""
        return self._evidence(span_ids, self._maps())

    def pain_points(self, pain_ids: Sequence[str] | None = None) -> list[Entity]:
        """The accepted pain points, with the canonical text that gets embedded."""
        return self._pain_points(pain_ids, self._maps())

    def clusters(self, cluster_ids: Sequence[str] | None = None) -> list[Entity]:
        """The active clusters. A cluster carries its members, not its own text."""
        return self._clusters(cluster_ids, self._maps())

    def entities(
        self, wanted: Sequence[tuple[str, str]] | None = None
    ) -> list[Entity]:
        """The entities to index: all of them, or only the named ones."""
        maps = self._maps()
        if wanted is None:
            return [
                *self._evidence(None, maps),
                *self._pain_points(None, maps),
                *self._clusters(None, maps),
            ]
        by_type: dict[str, list[str]] = {}
        for entity_type, entity_id in wanted:
            by_type.setdefault(entity_type, []).append(entity_id)
        entities: list[Entity] = []
        if ENTITY_EVIDENCE in by_type:
            entities.extend(self._evidence(by_type[ENTITY_EVIDENCE], maps))
        if ENTITY_PAIN_POINT in by_type:
            entities.extend(self._pain_points(by_type[ENTITY_PAIN_POINT], maps))
        if ENTITY_CLUSTER in by_type:
            entities.extend(self._clusters(by_type[ENTITY_CLUSTER], maps))
        return entities

    def entity(self, entity_type: str, entity_id: str) -> Entity | None:
        """One entity, or None when it is gone."""
        readers = {
            ENTITY_EVIDENCE: self.evidence,
            ENTITY_PAIN_POINT: self.pain_points,
            ENTITY_CLUSTER: self.clusters,
        }
        reader = readers.get(entity_type)
        if reader is None:
            return None
        found = reader([entity_id])
        # A single lookup reports nothing, so a row it skipped does not
        # reach the report of the next sync.
        self._skipped.clear()
        return found[0] if found else None

    def counts(self) -> dict[str, int]:
        """How many entities the store holds."""
        evidence = self.conn.execute(
            """
            SELECT count(*) FROM evidence e
            JOIN documents d ON d.doc_id = e.doc_id
            WHERE e.live_status = 'live' AND d.live_status = 'live'
            """
        ).fetchone()[0]
        pain_points = self.conn.execute(
            "SELECT count(*) FROM pain_points "
            "WHERE accepted = 1 AND normalized_key IS NOT NULL "
            "AND normalized_key != ''"
        ).fetchone()[0]
        clusters = self.conn.execute(
            "SELECT count(*) FROM clusters WHERE status = 'active'"
        ).fetchone()[0]
        return {
            "evidence": int(evidence),
            "pain_points": int(pain_points),
            "clusters": int(clusters),
        }

    # --- the rows the reader needs -----------------------------------------

    def take_skipped(self) -> list[str]:
        """The rows the last read skipped, and the reason for each."""
        skipped = list(self._skipped)
        self._skipped.clear()
        return skipped

    def _evidence(
        self, span_ids: Sequence[str] | None, maps: _Maps
    ) -> list[Entity]:
        """The evidence spans, with the text that gets embedded."""
        rows = self.conn.execute(
            """
            SELECT e.span_id, e.doc_id, e.quote, e.evidence_hash, e.live_status,
                   e.created_at, e.last_seen_at,
                   d.source_id, d.community_id, d.live_status AS document_status
            FROM evidence e
            JOIN documents d ON d.doc_id = e.doc_id
            """
            + _in_clause("e.span_id", span_ids, where=True)
            + " ORDER BY e.span_id",
            _in_params(span_ids),
        ).fetchall()
        entities: list[Entity] = []
        for row in rows:
            quote = str(row["quote"] or "")
            created_at = str(row["created_at"] or "")
            entities.append(
                Entity(
                    entity_id=str(row["span_id"]),
                    entity_type=ENTITY_EVIDENCE,
                    text=quote,
                    content_hash=str(row["evidence_hash"] or "") or _digest(quote),
                    source_id=str(row["source_id"] or ""),
                    community_id=str(row["community_id"] or ""),
                    cluster_id=maps.cluster_of_span.get(str(row["span_id"]), ""),
                    live_status=_live_status(
                        row["live_status"], row["document_status"]
                    ),
                    created_at=created_at,
                    updated_at=str(row["last_seen_at"] or "") or created_at,
                )
            )
        return entities

    def _pain_points(
        self, pain_ids: Sequence[str] | None, maps: _Maps
    ) -> list[Entity]:
        """The accepted pain points, with the canonical text that gets embedded."""
        rows = self.conn.execute(
            """
            SELECT p.pain_id, p.doc_id, p.span_id, p.domain, p.actor, p.action,
                   p.object, p.consequence, p.pain_statement, p.normalized_key,
                   p.normalization_version, p.severity, p.frequency,
                   d.source_id, d.community_id, p.created_at
            FROM pain_points p
            JOIN documents d ON d.doc_id = p.doc_id
            WHERE p.accepted = 1
              AND p.normalized_key IS NOT NULL
              AND p.normalized_key != ''
            """
            + _in_clause("p.pain_id", pain_ids)
            + " ORDER BY p.pain_id",
            _in_params(pain_ids),
        ).fetchall()
        entities: list[Entity] = []
        for row in rows:
            pain_id = str(row["pain_id"])
            pain = self._pain_point(row)
            if pain is None:
                continue
            text = canonical_text(pain)
            created_at = str(row["created_at"] or "")
            entities.append(
                Entity(
                    entity_id=pain_id,
                    entity_type=ENTITY_PAIN_POINT,
                    text=text,
                    content_hash=_digest(text),
                    source_id=str(row["source_id"] or ""),
                    community_id=str(row["community_id"] or ""),
                    domain=pain.domain.value,
                    cluster_id=maps.cluster_of_pain.get(pain_id, ""),
                    created_at=created_at,
                    updated_at=created_at,
                )
            )
        return entities

    def _clusters(
        self, cluster_ids: Sequence[str] | None, maps: _Maps
    ) -> list[Entity]:
        """The active clusters. A cluster carries its members, not its own text."""
        rows = self.conn.execute(
            """
            SELECT cluster_id, label, domain, created_at, updated_at
            FROM clusters
            WHERE status = 'active'
            """
            + _in_clause("cluster_id", cluster_ids)
            + " ORDER BY cluster_id",
            _in_params(cluster_ids),
        ).fetchall()
        entities: list[Entity] = []
        for row in rows:
            cluster_id = str(row["cluster_id"])
            member_ids = maps.members_of_cluster.get(cluster_id, ())
            entities.append(
                Entity(
                    entity_id=cluster_id,
                    entity_type=ENTITY_CLUSTER,
                    text="",
                    content_hash=_digest(",".join(sorted(member_ids))),
                    domain=str(row["domain"] or ""),
                    cluster_id=cluster_id,
                    created_at=str(row["created_at"] or ""),
                    updated_at=str(row["updated_at"] or ""),
                    members=member_ids,
                )
            )
        return entities

    def _maps(self) -> _Maps:
        """The memberships and the links the entities carry.

        The cluster of a pain point, the members of a cluster and the cluster
        of a span all come from these two reads.
        """
        members: dict[str, list[str]] = {}
        cluster_of_pain: dict[str, str] = {}
        rows = self.conn.execute(
            "SELECT pain_id, cluster_id FROM cluster_members "
            "WHERE valid_to IS NULL ORDER BY cluster_id, pain_id"
        ).fetchall()
        for row in rows:
            pain_id = str(row["pain_id"])
            cluster_id = str(row["cluster_id"])
            members.setdefault(cluster_id, []).append(pain_id)
            cluster_of_pain.setdefault(pain_id, cluster_id)
        links = self.conn.execute(
            "SELECT pain_id, span_id FROM evidence_pain_links ORDER BY pain_id, span_id"
        ).fetchall()
        cluster_of_span: dict[str, str] = {}
        for row in links:
            cluster_id = cluster_of_pain.get(str(row["pain_id"]))
            if cluster_id:
                cluster_of_span.setdefault(str(row["span_id"]), cluster_id)
        return _Maps(
            cluster_of_pain=cluster_of_pain,
            members_of_cluster={
                cluster: tuple(ids) for cluster, ids in members.items()
            },
            cluster_of_span=cluster_of_span,
        )

    def _pain_point(self, row: sqlite3.Row) -> PainPoint | None:
        """The pain point of a row, or None when the model refuses it.

        A domain outside the controlled set, or a field the model requires
        and the row leaves empty, skips the row. The index keeps going and
        the report says which rows were left out.
        """
        pain_id = str(row["pain_id"])
        raw_domain = str(row["domain"] or "")
        try:
            domain = Domain(raw_domain)
        except ValueError:
            self._skipped.append(
                f"pain point {pain_id} has the domain {raw_domain!r}, which is "
                "not one of the controlled domains"
            )
            return None
        try:
            return PainPoint(
                pain_id=pain_id,
                doc_id=str(row["doc_id"]),
                source_id=str(row["source_id"] or ""),
                span_id=str(row["span_id"] or ""),
                quote="",
                domain=domain,
                actor=str(row["actor"] or ""),
                action=str(row["action"] or ""),
                object=str(row["object"] or ""),
                consequence=str(row["consequence"] or ""),
                pain_statement=str(row["pain_statement"] or ""),
                normalized_key=str(row["normalized_key"] or ""),
                normalization_version=str(row["normalization_version"] or ""),
                severity=int(row["severity"] or 0),
                frequency="" if row["frequency"] is None else str(row["frequency"]),
            )
        except ValidationError as exc:
            self._skipped.append(
                f"pain point {pain_id} is not a pain point the wave reads: "
                f"{exc.error_count()} field(s) refused"
            )
            return None


# --- the helpers ------------------------------------------------------------


def _digest(text: str) -> str:
    """The digest of a text."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _live_status(span_status: object, document_status: object) -> str:
    """The liveness of a span.

    A closed document takes its spans with it, so the document's status wins
    when it is not live. Otherwise the span's own status stands, and a store
    that knows neither says live.
    """
    document = str(document_status or "")
    if document and document != _LIVE:
        return document
    return str(span_status or "") or document or _LIVE


def _in_clause(column: str, ids: Sequence[str] | None, *, where: bool = False) -> str:
    """The id filter of a query, or nothing when every row was asked for."""
    if ids is None:
        return ""
    clause = f"{column} IN ({','.join('?' for _ in ids)})"
    return f" WHERE {clause}" if where else f" AND {clause}"


def _in_params(ids: Sequence[str] | None) -> tuple[str, ...]:
    """The parameters of the id filter."""
    if ids is None:
        return ()
    return tuple(str(value) for value in ids)

"""The index: the derived vectors, reconciled against the authoritative store.

SQLite holds the truth. Qdrant holds what can be searched. Every sync reads
the truth, writes what changed and removes what is gone, and the report says
what it did.

The vector space is the guard. The space id is written into the collection
metadata and into every payload, and a collection that holds another space is
never written to: the answer is a rebuild.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from product_hunter.clustering.similarity import VectorSpace, centroid
from product_hunter.config import SemanticConfig
from product_hunter.db.outbox import ProjectionTask
from product_hunter.semantic.embeddings import embed_all
from product_hunter.semantic.providers import EmbeddingProvider
from product_hunter.semantic.qdrant import QdrantAdapter, vector_size
from product_hunter.semantic.source import Entity, SemanticSource
from product_hunter.semantic.vectors import (
    ENTITY_CLUSTER,
    ENTITY_EVIDENCE,
    ENTITY_PAIN_POINT,
    ENTITY_TYPES,
    SearchFilter,
    VectorPayload,
    vector_id,
)

# The payload fields Qdrant indexes, so a filter on one of them does not
# scan the collection. The entity id is here because the cluster vector is
# the centroid of the members the index already holds.
_PAYLOAD_INDEX_FIELDS = (
    "entity_type",
    "source_id",
    "community_id",
    "domain",
    "cluster_id",
    "live_status",
    "space_id",
    "entity_id",
)

# The only live status. Anything else means the entity left the index.
_LIVE = "live"

# How many points one scroll reads. The adapter does not page, so the limit
# has to cover the collection.
_SCROLL_LIMIT = 10_000


class EmbeddingSpaceMismatch(Exception):
    """The collection holds another vector space."""

    reason_code = "embedding_space_mismatch"

    def __init__(self, detail: str = "") -> None:
        self.detail = detail
        super().__init__(detail or self.reason_code)


class IndexSyncFailed(Exception):
    """The index could not be written completely."""

    reason_code = "semantic_index_sync_failed"

    def __init__(self, detail: str = "") -> None:
        self.detail = detail
        super().__init__(detail or self.reason_code)


@dataclass
class _Counts:
    """The counters of a sync, while it runs."""

    indexed: int = 0
    unchanged: int = 0
    removed: int = 0
    failed: int = 0
    errors: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class SyncReport:
    """What one sync did."""

    indexed: int = 0
    unchanged: int = 0
    removed: int = 0
    failed: int = 0
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        """The report as a dict."""
        return {
            "indexed": self.indexed,
            "unchanged": self.unchanged,
            "removed": self.removed,
            "failed": self.failed,
            "errors": list(self.errors),
        }


class SearchHit(BaseModel):
    """One point the search returned."""

    model_config = ConfigDict(extra="forbid")

    entity_id: str
    entity_type: str
    score: float
    text: str = ""
    payload: dict[str, object] = Field(default_factory=dict)


class SemanticIndex:
    """The derived index, reconciled against the authoritative store."""

    def __init__(
        self,
        conn: sqlite3.Connection,
        adapter: QdrantAdapter,
        provider: EmbeddingProvider,
        *,
        config: SemanticConfig,
    ) -> None:
        self.conn = conn
        self.adapter = adapter
        self.provider = provider
        self.config = config
        self.source = SemanticSource(conn)

    @property
    def space(self) -> VectorSpace:
        """The space the vectors of this index live in."""
        return self.provider.space

    @property
    def collection(self) -> str:
        """The collection the index writes to."""
        return self.config.collection

    # --- the collection -----------------------------------------------------

    def ensure_ready(self) -> None:
        """The collection, under this space, with the payload indexes it needs.

        A collection that holds another space is refused: a vector of one
        space and a vector of another never share a collection.
        """
        info = self.adapter.collection_info(self.collection)
        if info is None:
            self.adapter.create_collection(
                self.collection,
                size=self.space.dimensions,
                distance=self.config.distance,
                metadata=self._metadata(),
            )
            for field in _PAYLOAD_INDEX_FIELDS:
                self.adapter.create_payload_index(self.collection, field)
            return
        held_size = vector_size(info)
        held_space = str((info.get("metadata") or {}).get("space_id") or "")
        if held_size != self.space.dimensions:
            raise EmbeddingSpaceMismatch(
                f"the collection {self.collection!r} holds vectors of "
                f"{held_size} dimensions and the configuration names "
                f"{self.space.dimensions}"
            )
        if held_space and held_space != self.space.space_id:
            raise EmbeddingSpaceMismatch(
                f"the collection {self.collection!r} holds the space "
                f"{held_space} and the configuration names "
                f"{self.space.space_id}"
            )
        held_fields = set(info.get("payload_schema") or {})
        for field in _PAYLOAD_INDEX_FIELDS:
            if field not in held_fields:
                self.adapter.create_payload_index(self.collection, field)

    def rebuild(self) -> SyncReport:
        """Replace the collection and index everything again."""
        if self.adapter.collection_exists(self.collection):
            self.adapter.delete_collection(self.collection)
        self.ensure_ready()
        return self.sync()

    # --- the sync -----------------------------------------------------------

    def sync(self, wanted: Sequence[tuple[str, str]] | None = None) -> SyncReport:
        """Reconcile the index with the store.

        A named subset is indexed on its own and never prunes: the points it
        did not ask about are not its business. A full sync prunes what the
        store no longer holds.
        """
        self.ensure_ready()
        counts = _Counts()
        entities = self.source.entities(wanted)
        counts.errors.extend(self.source.take_skipped())
        held = {(entity.entity_type, entity.entity_id) for entity in entities}
        if wanted is not None:
            for entity_type, entity_id in wanted:
                if (entity_type, entity_id) in held:
                    continue
                if entity_type not in ENTITY_TYPES:
                    # A task that names an entity the index does not hold
                    # types for has no point to remove.
                    continue
                self._remove(entity_type, entity_id, counts)
        pending: list[tuple[Entity, VectorPayload]] = []
        for entity in entities:
            point = self.adapter.get_point(
                self.collection, vector_id(entity.entity_type, entity.entity_id)
            )
            if entity.live_status != _LIVE:
                # A closed document takes its evidence with it.
                if point is not None:
                    self.adapter.delete_points(
                        self.collection,
                        [vector_id(entity.entity_type, entity.entity_id)],
                    )
                    counts.removed += 1
                continue
            try:
                payload = self._payload(entity)
            except ValidationError as exc:
                counts.failed += 1
                counts.errors.append(
                    f"{entity.entity_type} {entity.entity_id} has no payload the "
                    f"index can write: {exc.error_count()} field(s) refused"
                )
                continue
            if point is not None and _same_payload(point.get("payload"), payload):
                counts.unchanged += 1
                continue
            pending.append((entity, payload))
        self._embed_and_write(pending, counts)
        if wanted is None:
            self._prune(held, counts)
        return SyncReport(
            indexed=counts.indexed,
            unchanged=counts.unchanged,
            removed=counts.removed,
            failed=counts.failed,
            errors=list(counts.errors),
        )

    def handle_projection_task(self, task: ProjectionTask) -> None:
        """Index what one outbox task produced.

        A task that names nothing syncs everything. A vector that could not
        be produced raises, so the outbox keeps the task pending and
        schedules the retry.
        """
        payload = task.payload
        if not payload:
            # A task with no payload syncs everything.
            report = self.sync(None)
        else:
            wanted = [
                (ENTITY_EVIDENCE, span_id)
                for span_id in _ids_of(payload.get("evidence"))
            ] + [
                (ENTITY_PAIN_POINT, pain_id)
                for pain_id in _ids_of(payload.get("pain_points"))
            ]
            report = self.sync(wanted)
        if report.failed:
            raise IndexSyncFailed(
                "; ".join(report.errors) or "the index could not be written"
            )

    # --- the search ---------------------------------------------------------

    def search(
        self,
        text: str,
        *,
        filters: SearchFilter | None = None,
        limit: int = 10,
    ) -> list[SearchHit]:
        """The points nearest the text, with the text of each entity."""
        self.ensure_ready()
        vectors = embed_all(self.provider, [text])
        if not vectors:
            return []
        effective = (filters or SearchFilter()).model_copy(
            update={"space_id": self.space.space_id}
        )
        points = self.adapter.search(
            self.collection,
            vectors[0],
            limit=limit,
            filter=effective.to_qdrant_filter(),
        )
        hits: list[SearchHit] = []
        for point in points:
            payload = point.get("payload")
            payload = dict(payload) if isinstance(payload, dict) else {}
            entity_type = str(payload.get("entity_type") or "")
            entity_id = str(payload.get("entity_id") or "")
            entity = (
                self.source.entity(entity_type, entity_id)
                if entity_type and entity_id
                else None
            )
            hits.append(
                SearchHit(
                    entity_id=entity_id,
                    entity_type=entity_type,
                    score=float(point.get("score") or 0.0),
                    text=entity.text if entity is not None else "",
                    payload=payload,
                )
            )
        return hits

    # --- the status ---------------------------------------------------------

    def status(self) -> dict[str, object]:
        """What the index holds. A probe that fails is a value, not an error."""
        status: dict[str, object] = {
            "space_id": self.space.space_id,
            "provider": self.space.provider,
            "model": self.space.model,
            "model_version": self.space.model_version,
            "dimensions": self.space.dimensions,
            "collection": self.collection,
            "collection_exists": False,
            "vector_size": None,
            "points": 0,
            "counts": {},
            "errors": [],
        }
        errors: list[str] = []
        try:
            info = self.adapter.collection_info(self.collection)
            if info is not None:
                status["collection_exists"] = True
                status["vector_size"] = vector_size(info)
                status["points"] = self.adapter.count(self.collection)
        except Exception as exc:
            errors.append(f"qdrant: {type(exc).__name__}")
        try:
            status["counts"] = self.source.counts()
        except Exception as exc:
            errors.append(f"store: {type(exc).__name__}")
        status["errors"] = errors
        return status

    # --- the writes ---------------------------------------------------------

    def _embed_and_write(
        self, pending: list[tuple[Entity, VectorPayload]], counts: _Counts
    ) -> None:
        """The entities that have a text, and then the clusters."""
        texts = [item for item in pending if item[0].entity_type != ENTITY_CLUSTER]
        clusters = [item for item in pending if item[0].entity_type == ENTITY_CLUSTER]
        for chunk in _chunks(texts, self.config.write_batch_size):
            self._write_texts(chunk, counts)
        # The members of a cluster are indexed before it, so their vectors are
        # already in the collection when the centroid is taken.
        self._write_clusters(clusters, counts)

    def _write_texts(
        self, chunk: list[tuple[Entity, VectorPayload]], counts: _Counts
    ) -> None:
        """One batch of texts, embedded and written."""
        try:
            vectors = embed_all(self.provider, [entity.text for entity, _ in chunk])
        except Exception as exc:
            # One text the model refuses must not cost the whole batch, so the
            # batch is tried one entity at a time.
            for entity, payload in chunk:
                self._write_one(entity, payload, counts, exc)
            return
        if len(vectors) != len(chunk):
            counts.failed += len(chunk)
            counts.errors.append(
                f"the provider returned {len(vectors)} vectors for "
                f"{len(chunk)} texts"
            )
            return
        points = [
            _point(entity, payload, vector)
            for (entity, payload), vector in zip(chunk, vectors)
        ]
        self.adapter.upsert(self.collection, points)
        counts.indexed += len(points)

    def _write_one(
        self,
        entity: Entity,
        payload: VectorPayload,
        counts: _Counts,
        cause: Exception,
    ) -> None:
        """One entity on its own, so a bad neighbour does not cost it."""
        try:
            vectors = embed_all(self.provider, [entity.text])
        except Exception as exc:
            counts.failed += 1
            counts.errors.append(
                f"{entity.entity_type} {entity.entity_id} could not be embedded: "
                f"{type(exc).__name__} ({type(cause).__name__})"
            )
            return
        self.adapter.upsert(self.collection, [_point(entity, payload, vectors[0])])
        counts.indexed += 1

    def _write_clusters(
        self, clusters: list[tuple[Entity, VectorPayload]], counts: _Counts
    ) -> None:
        """The clusters, each the centroid of the vectors of its members."""
        for entity, payload in clusters:
            vectors = self._member_vectors(entity)
            if not vectors:
                counts.errors.append(
                    f"cluster {entity.entity_id} has no indexed member to take "
                    "the vector of"
                )
                continue
            self.adapter.upsert(
                self.collection, [_point(entity, payload, centroid(vectors))]
            )
            counts.indexed += 1

    def _member_vectors(self, entity: Entity) -> list[tuple[float, ...]]:
        """The vectors of the members of a cluster the index holds."""
        if not entity.members:
            return []
        points = self.adapter.scroll(
            self.collection,
            limit=len(entity.members),
            with_vector=True,
            filter={
                "must": [
                    {"key": "entity_type", "match": {"value": ENTITY_PAIN_POINT}},
                    {
                        "key": "entity_id",
                        "match": {"any": list(entity.members)},
                    },
                    {"key": "space_id", "match": {"value": self.space.space_id}},
                ]
            },
        )
        vectors: list[tuple[float, ...]] = []
        for point in points:
            vector = point.get("vector")
            if isinstance(vector, list) and vector:
                vectors.append(tuple(float(value) for value in vector))
        return vectors

    def _remove(
        self, entity_type: str, entity_id: str, counts: _Counts
    ) -> None:
        """The point of an entity the store no longer holds."""
        point_id = vector_id(entity_type, entity_id)
        if self.adapter.get_point(self.collection, point_id) is None:
            return
        self.adapter.delete_points(self.collection, [point_id])
        counts.removed += 1

    def _prune(self, held: set[tuple[str, str]], counts: _Counts) -> None:
        """The points the store no longer holds."""
        points = self.adapter.scroll(
            self.collection, limit=_SCROLL_LIMIT, with_vector=False
        )
        for point in points:
            payload = point.get("payload")
            payload = payload if isinstance(payload, dict) else {}
            key = (
                str(payload.get("entity_type") or ""),
                str(payload.get("entity_id") or ""),
            )
            if key in held:
                continue
            self.adapter.delete_points(self.collection, [str(point.get("id") or "")])
            counts.removed += 1

    def _payload(self, entity: Entity) -> VectorPayload:
        """The payload of an entity."""
        return VectorPayload(
            entity_id=entity.entity_id,
            entity_type=entity.entity_type,
            content_hash=entity.content_hash,
            embedding_provider=self.space.provider,
            embedding_model=self.space.model,
            embedding_model_version=self.space.model_version,
            space_id=self.space.space_id,
            dimensions=self.space.dimensions,
            index_version=self.config.index_version,
            created_at=entity.created_at,
            updated_at=entity.updated_at,
            source_id=entity.source_id,
            community_id=entity.community_id,
            domain=entity.domain,
            cluster_id=entity.cluster_id,
            live_status=entity.live_status,
        )

    def _metadata(self) -> dict[str, object]:
        """The metadata of the collection: the space it holds."""
        return {
            "space_id": self.space.space_id,
            "provider": self.space.provider,
            "model": self.space.model,
            "model_version": self.space.model_version,
            "dimensions": self.space.dimensions,
        }


# --- the helpers ------------------------------------------------------------


def _point(entity: Entity, payload: VectorPayload, vector: Sequence[float]) -> dict:
    """One point in the wire shape."""
    return {
        "id": vector_id(entity.entity_type, entity.entity_id),
        "vector": [float(value) for value in vector],
        "payload": payload.to_payload(),
    }


def _same_payload(held: object, payload: VectorPayload) -> bool:
    """Whether the point the index holds says the same thing as this payload."""
    if not isinstance(held, dict):
        return False
    try:
        current = VectorPayload.model_validate(held)
    except ValidationError:
        return False
    return current.same_content(payload)


def _ids_of(value: object) -> list[str]:
    """The ids of a task payload."""
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item)]


def _chunks(items: list, size: int) -> Iterator[list]:
    """The items, in batches of at most the given size."""
    step = max(1, int(size))
    for start in range(0, len(items), step):
        yield items[start : start + step]

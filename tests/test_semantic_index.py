"""The index over a store, driven against an in-memory Qdrant.

No test here reaches a Qdrant server and no test loads a model: the
adapter runs over the fake of the adapter's own test file, and the provider
is a deterministic embedder of the configured width. The store is the real
one, imported from the run the other tests use.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import httpx
import pytest

from conftest import _build_run, evidence_store
from product_hunter.clustering.similarity import MixedVectorSpaceError, VectorSpace
from product_hunter.config import EmbeddingConfig, SemanticConfig
from product_hunter.db import Store, connect
from product_hunter.db.outbox import (
    enqueue_projection,
    get_projection_task,
    process_outbox,
)
from product_hunter.semantic.embeddings import embed_all
from product_hunter.semantic.index import EmbeddingSpaceMismatch, SemanticIndex
from product_hunter.semantic.qdrant import QdrantAdapter
from product_hunter.semantic.vectors import (
    ENTITY_CLUSTER,
    ENTITY_EVIDENCE,
    ENTITY_PAIN_POINT,
    SearchFilter,
    vector_id,
)
from product_hunter.sources import load_registry
from test_semantic_qdrant import FakeQdrant, Sleeps

COLLECTION = "product_hunter_evidence"
BASE_URL = "http://qdrant.test:6333"
DIMENSIONS = 8
STAMP = "2026-10-21T00:00:00+00:00"


# --- the provider ------------------------------------------------------------


class FakeProvider:
    """A provider that embeds a text into a vector of the space's width."""

    def __init__(self, space: VectorSpace) -> None:
        self._space = space
        self.calls: list[list[str]] = []

    @property
    def space(self) -> VectorSpace:
        """The space the vectors of this provider live in."""
        return self._space

    def embed(self, texts):
        """One vector per text, and a record of what was asked for."""
        wanted = list(texts)
        self.calls.append(wanted)
        return [_vector(text, self._space.dimensions) for text in wanted]

    def diagnostics(self) -> dict[str, object]:
        """What an operator needs to know about this provider."""
        return {
            "provider": self._space.provider,
            "model": self._space.model,
            "revision": "",
            "cache_dir": "",
            "cached": False,
            "device": "cpu",
            "device_requested": "cpu",
            "cuda_available": False,
            "dimensions": self._space.dimensions,
        }

    def release(self) -> None:
        """Let go of whatever the provider holds."""


class BrokenProvider(FakeProvider):
    """A provider whose model is not there."""

    def embed(self, texts):
        """Every text fails."""
        self.calls.append(list(texts))
        raise RuntimeError("the model is not available")


class WrongWidthProvider(FakeProvider):
    """A provider whose model answers with a vector of another width."""

    def embed(self, texts):
        """Every text comes back too narrow."""
        self.calls.append(list(texts))
        return [(1.0, 0.0)] * len(list(texts))


def _vector(text: str, width: int) -> tuple[float, ...]:
    """A vector that depends on the text, so two texts are not one point."""
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    return tuple((digest[index % len(digest)] / 127.5) - 1.0 for index in range(width))


def _space(model: str = "BAAI/bge-m3") -> VectorSpace:
    """The space the tests embed in."""
    return VectorSpace.of(
        provider="bge-m3-local",
        model=model,
        model_version="1",
        dimensions=DIMENSIONS,
    )


def _config() -> SemanticConfig:
    """The config the tests run the index with."""
    return SemanticConfig(
        version=1,
        index_version="semantic-v1",
        collection=COLLECTION,
        url=BASE_URL,
        api_key_env="QDRANT_API_KEY",
        embedding=EmbeddingConfig(
            provider="bge-m3-local",
            model="BAAI/bge-m3",
            model_version="1",
            dimensions=DIMENSIONS,
        ),
        distance="Cosine",
        timeout_seconds=5,
        max_attempts=2,
        backoff_seconds=0,
        write_batch_size=64,
    )


# --- the fixtures -----------------------------------------------------------


@pytest.fixture
def store(tmp_path: Path):
    """A store with one run imported: two evidence spans and one pain point."""
    run_dir, fetch_log, _documents, _accepted = _build_run(tmp_path)
    store = evidence_store(tmp_path)
    store.import_run(run_dir, fetch_log=fetch_log, registry=load_registry())
    yield store
    store.close()


@pytest.fixture
def fake() -> FakeQdrant:
    """The in-memory Qdrant."""
    return FakeQdrant()


@pytest.fixture
def provider() -> FakeProvider:
    """The provider that embeds without a model."""
    return FakeProvider(_space())


@pytest.fixture
def index(tmp_path: Path, store: Store, fake: FakeQdrant, provider: FakeProvider):
    """An index over a read-only connection to the store."""
    conn = connect(tmp_path / "product_hunter.db", read_only=True)
    adapter = QdrantAdapter(BASE_URL, transport=fake.transport(), sleep=Sleeps())
    yield SemanticIndex(conn, adapter, provider, config=_config())
    conn.close()
    adapter.close()


# --- the store helpers ------------------------------------------------------


def _spans(store: Store) -> list[str]:
    """The evidence spans of the store."""
    return [
        str(row["span_id"])
        for row in store.conn.execute("SELECT span_id FROM evidence ORDER BY span_id")
    ]


def _pain_id(store: Store) -> str:
    """The accepted pain point of the store."""
    return str(
        store.conn.execute("SELECT pain_id FROM pain_points").fetchone()["pain_id"]
    )


def _doc_of(store: Store, span_id: str) -> str:
    """The document a span belongs to."""
    return str(
        store.conn.execute(
            "SELECT doc_id FROM evidence WHERE span_id = ?", (span_id,)
        ).fetchone()["doc_id"]
    )


def _add_pain_point(
    store: Store, pain_id: str, span_id: str, doc_id: str, *, accepted: int = 1
) -> None:
    """Another pain point, on the span the store already holds."""
    store.conn.execute(
        """
        INSERT INTO pain_points (
            pain_id, span_id, doc_id, domain, actor, action, object, consequence,
            severity, frequency, pain_statement, normalized_key,
            normalization_version, accepted, created_at
        ) VALUES (?, ?, ?, 'operations', 'finance lead', 'paste totals',
                  'spreadsheet', 'a lost Saturday', 4, 'monthly', ?,
                  'operations:paste-totals:spreadsheet', 'norm-v1', ?, ?)
        """,
        (pain_id, span_id, doc_id, f"the team pastes {pain_id} by hand", accepted, STAMP),
    )
    store.conn.execute(
        "INSERT INTO evidence_pain_links (pain_id, span_id, created_at) "
        "VALUES (?, ?, ?)",
        (pain_id, span_id, STAMP),
    )


def _add_cluster(store: Store, cluster_id: str, member_ids: list[str]) -> None:
    """A cluster with the given members."""
    store.conn.execute(
        "INSERT INTO clusters (cluster_id, status, label, domain, created_at, "
        "updated_at) VALUES (?, 'active', 'the cluster', 'operations', ?, ?)",
        (cluster_id, STAMP, STAMP),
    )
    for pain_id in member_ids:
        store.conn.execute(
            "INSERT INTO cluster_members (member_id, cluster_id, pain_id, "
            "valid_from, basis) VALUES (?, ?, ?, ?, 'test')",
            (f"mem_{pain_id}", cluster_id, pain_id, STAMP),
        )


def _add_second_source(store: Store, span_id: str = "ev_other") -> str:
    """A document of another source, with one evidence span."""
    store.conn.execute(
        "INSERT INTO documents (doc_id, fetch_id, source_id, url, text_sha256, "
        "text_path, raw_path, raw_sha256, parser_version, created_at) "
        "VALUES ('doc_other', 'fetch_hn_test', 'github_issues', "
        "'https://api.github.com/issues/1', 'hash-other', 'p', 'r', 'raw-other', "
        "'v1', ?)",
        (STAMP,),
    )
    store.conn.execute(
        "INSERT INTO evidence (span_id, doc_id, quote, char_start, char_end, "
        "match_mode, created_at) VALUES (?, 'doc_other', "
        "'the invoices are exported by hand too', 0, 10, 'exact', ?)",
        (span_id, STAMP),
    )
    return span_id


def _points(fake: FakeQdrant) -> dict:
    """The points the collection holds."""
    return fake.collections.get(COLLECTION, {}).get("points", {})


def _row_counts(store: Store) -> dict[str, int]:
    """The number of rows in the tables the index reads."""
    tables = (
        "documents",
        "evidence",
        "pain_points",
        "evidence_pain_links",
        "clusters",
        "cluster_members",
    )
    return {
        table: int(
            store.conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        )
        for table in tables
    }


# --- the sync ---------------------------------------------------------------


def test_a_first_sync_indexes_the_evidence_and_the_pain_points(
    index: SemanticIndex, fake: FakeQdrant, store: Store
) -> None:
    report = index.sync()

    assert report.indexed == 3
    assert report.unchanged == 0
    assert report.removed == 0
    assert report.failed == 0
    assert report.errors == []

    points = _points(fake)
    assert len(points) == 3
    kinds = sorted(point["payload"]["entity_type"] for point in points.values())
    assert kinds == ["evidence", "evidence", "pain_point"]
    for point in points.values():
        assert point["payload"]["space_id"] == index.space.space_id
        assert point["payload"]["embedding_model"] == "BAAI/bge-m3"
        assert point["payload"]["index_version"] == "semantic-v1"


def test_the_point_id_is_the_entitys(
    index: SemanticIndex, fake: FakeQdrant, store: Store
) -> None:
    index.sync()

    assert set(_points(fake)) == {
        vector_id(ENTITY_EVIDENCE, span) for span in _spans(store)
    } | {vector_id(ENTITY_PAIN_POINT, _pain_id(store))}


def test_an_unchanged_entity_is_skipped(
    index: SemanticIndex, provider: FakeProvider
) -> None:
    index.sync()
    asked = len(provider.calls)

    report = index.sync()

    assert report.indexed == 0
    assert report.unchanged == 3
    assert report.failed == 0
    assert len(provider.calls) == asked


def test_the_same_entity_indexed_twice_is_one_point(
    index: SemanticIndex, fake: FakeQdrant
) -> None:
    index.sync()
    index.sync()

    assert len(_points(fake)) == 3


def test_a_changed_entity_replaces_its_point(
    index: SemanticIndex, fake: FakeQdrant, store: Store
) -> None:
    index.sync()
    pain_id = _pain_id(store)
    point_id = vector_id(ENTITY_PAIN_POINT, pain_id)
    before = _points(fake)[point_id]["payload"]["content_hash"]

    store.conn.execute(
        "UPDATE pain_points SET pain_statement = ? WHERE pain_id = ?",
        ("the team copies every invoice total by hand", pain_id),
    )
    report = index.sync()

    assert len(_points(fake)) == 3
    assert report.indexed == 1
    assert _points(fake)[point_id]["payload"]["content_hash"] != before


def test_a_deleted_entity_loses_its_point(
    index: SemanticIndex, fake: FakeQdrant, store: Store
) -> None:
    index.sync()
    pain_id = _pain_id(store)

    store.conn.execute(
        "DELETE FROM evidence_pain_links WHERE pain_id = ?", (pain_id,)
    )
    store.conn.execute("DELETE FROM pain_points WHERE pain_id = ?", (pain_id,))
    index.sync()

    assert vector_id(ENTITY_PAIN_POINT, pain_id) not in _points(fake)
    assert len(_points(fake)) == 2


def test_a_closed_document_takes_its_evidence_with_it(
    index: SemanticIndex, fake: FakeQdrant, store: Store
) -> None:
    index.sync()
    span_id = _spans(store)[0]

    store.conn.execute(
        "UPDATE documents SET live_status = 'closed' WHERE doc_id = ?",
        (_doc_of(store, span_id),),
    )
    report = index.sync()

    assert vector_id(ENTITY_EVIDENCE, span_id) not in _points(fake)
    assert report.removed == 1
    assert len(_points(fake)) == 2


def test_a_pain_point_outside_the_domain_vocabulary_is_skipped(
    index: SemanticIndex, fake: FakeQdrant, store: Store
) -> None:
    store.conn.execute("UPDATE pain_points SET domain = 'not-a-domain'")

    report = index.sync()

    assert report.indexed == 2
    assert report.failed == 0
    assert len(_points(fake)) == 2
    assert any("not-a-domain" in error for error in report.errors)


# --- the space guard --------------------------------------------------------


def test_a_model_change_is_refused(
    tmp_path: Path, store: Store, fake: FakeQdrant, provider: FakeProvider
) -> None:
    conn = connect(tmp_path / "product_hunter.db", read_only=True)
    adapter = QdrantAdapter(BASE_URL, transport=fake.transport(), sleep=Sleeps())
    try:
        first = SemanticIndex(conn, adapter, provider, config=_config())
        first.sync()
        other = FakeProvider(_space("BAAI/bge-m3-v2"))
        changed = SemanticIndex(conn, adapter, other, config=_config())

        with pytest.raises(EmbeddingSpaceMismatch) as caught:
            changed.ensure_ready()

        assert first.space.space_id in str(caught.value)
        assert other.space.space_id in str(caught.value)
    finally:
        conn.close()
        adapter.close()


def test_a_dimension_change_is_refused(index: SemanticIndex) -> None:
    index.adapter.create_collection(
        COLLECTION,
        size=DIMENSIONS + 4,
        distance="Cosine",
        metadata={"space_id": index.space.space_id},
    )

    with pytest.raises(EmbeddingSpaceMismatch) as caught:
        index.ensure_ready()

    assert str(DIMENSIONS + 4) in str(caught.value)
    assert str(DIMENSIONS) in str(caught.value)


def test_a_rebuild_clears_the_mismatch(
    tmp_path: Path, store: Store, fake: FakeQdrant, provider: FakeProvider
) -> None:
    conn = connect(tmp_path / "product_hunter.db", read_only=True)
    adapter = QdrantAdapter(BASE_URL, transport=fake.transport(), sleep=Sleeps())
    try:
        first = SemanticIndex(conn, adapter, provider, config=_config())
        first.sync()
        other = FakeProvider(_space("BAAI/bge-m3-v2"))
        changed = SemanticIndex(conn, adapter, other, config=_config())

        report = changed.rebuild()

        assert report.indexed == 3
        info = adapter.collection_info(COLLECTION)
        assert info is not None
        assert info["metadata"]["space_id"] == other.space.space_id
        assert len(_points(fake)) == 3
    finally:
        conn.close()
        adapter.close()


def test_a_space_mismatch_is_refused_at_search_time(index: SemanticIndex) -> None:
    index.adapter.create_collection(
        COLLECTION,
        size=DIMENSIONS,
        distance="Cosine",
        metadata={"space_id": "the-space-of-another-model"},
    )

    with pytest.raises(EmbeddingSpaceMismatch):
        index.search("the invoices are exported by hand")


# --- the search -------------------------------------------------------------


def test_a_filter_narrows_the_result(
    index: SemanticIndex, fake: FakeQdrant, store: Store
) -> None:
    other_span = _add_second_source(store)
    index.sync()

    hits = index.search(
        "the invoices are exported by hand",
        filters=SearchFilter(source_id="hn_algolia"),
    )

    assert hits
    assert all(hit.payload["source_id"] == "hn_algolia" for hit in hits)
    assert all(hit.entity_id != other_span for hit in hits)
    body = fake.bodies[-1]
    assert {
        "key": "space_id",
        "match": {"value": index.space.space_id},
    } in body["filter"]["must"]


def test_the_search_answers_with_the_text_of_the_entity(
    index: SemanticIndex, store: Store
) -> None:
    index.sync()

    hits = index.search("anything at all")

    assert hits
    assert any(hit.text for hit in hits)
    assert all(hit.entity_type in ("evidence", "pain_point") for hit in hits)


# --- the partial sync -------------------------------------------------------


def test_a_partial_sync_touches_only_its_entities(
    index: SemanticIndex, fake: FakeQdrant, store: Store
) -> None:
    pain_id = _pain_id(store)

    report = index.sync([(ENTITY_PAIN_POINT, pain_id)])

    assert report.indexed == 1
    assert set(_points(fake)) == {vector_id(ENTITY_PAIN_POINT, pain_id)}


def test_a_partial_sync_does_not_prune(
    index: SemanticIndex, fake: FakeQdrant, store: Store
) -> None:
    index.sync()

    index.sync([(ENTITY_PAIN_POINT, _pain_id(store))])

    assert len(_points(fake)) == 3


def test_a_partial_index_converges(
    index: SemanticIndex, fake: FakeQdrant, store: Store
) -> None:
    index.sync([(ENTITY_PAIN_POINT, _pain_id(store))])

    index.sync()

    assert len(_points(fake)) == 3


# --- the failures -----------------------------------------------------------


def test_a_failed_provider_writes_no_vector(
    index: SemanticIndex, fake: FakeQdrant, store: Store
) -> None:
    index.sync()
    before = len(_points(fake))
    pain_id = _pain_id(store)
    store.conn.execute(
        "UPDATE pain_points SET pain_statement = ? WHERE pain_id = ?",
        ("the team copies every invoice total by hand", pain_id),
    )
    index.provider = BrokenProvider(index.space)

    report = index.sync()

    assert report.indexed == 0
    assert report.failed == 1
    assert len(_points(fake)) == before
    assert any("RuntimeError" in error for error in report.errors)


def test_a_provider_of_the_wrong_width_is_refused(
    index: SemanticIndex, fake: FakeQdrant
) -> None:
    index.provider = WrongWidthProvider(index.space)

    with pytest.raises(MixedVectorSpaceError):
        embed_all(index.provider, ["a text"])

    report = index.sync()

    assert report.indexed == 0
    assert report.failed == 3
    assert _points(fake) == {}


# --- the projection task ----------------------------------------------------


def _task(store: Store, payload: dict[str, object]):
    """A qdrant task, as the store writes one."""
    return enqueue_projection(
        store.conn,
        target="qdrant",
        entity_type="run",
        entity_id="run-test",
        idempotency_key="semantic_index:run-test:test",
        payload=payload,
    )


def test_the_projection_task_indexes_only_its_entities(
    index: SemanticIndex, fake: FakeQdrant, store: Store
) -> None:
    span_id = _spans(store)[0]
    pain_id = _pain_id(store)
    task = _task(store, {"run_id": "run-test", "evidence": [span_id], "pain_points": [pain_id]})

    index.handle_projection_task(task)

    assert set(_points(fake)) == {
        vector_id(ENTITY_EVIDENCE, span_id),
        vector_id(ENTITY_PAIN_POINT, pain_id),
    }


def test_a_failed_projection_task_leaves_the_task_pending(
    index: SemanticIndex, store: Store
) -> None:
    span_id = _spans(store)[0]
    store.conn.execute("DELETE FROM projection_tasks WHERE target = 'qdrant'")
    task = _task(store, {"run_id": "run-test", "evidence": [span_id]})
    index.provider = BrokenProvider(index.space)

    processed = process_outbox(
        store.conn,
        {"qdrant": index.handle_projection_task},
        "worker-1",
        target="qdrant",
    )

    assert processed == 1
    after = get_projection_task(store.conn, task.task_id)
    assert after is not None
    assert after.status == "pending"
    assert after.attempts == 1


# --- the clusters -----------------------------------------------------------


def test_the_cluster_vector_is_the_centroid_of_its_members(
    index: SemanticIndex, fake: FakeQdrant, store: Store
) -> None:
    pain_id = _pain_id(store)
    span_id = _spans(store)[0]
    _add_pain_point(store, "pp_second", span_id, _doc_of(store, span_id))
    _add_cluster(store, "cl_1", [pain_id, "pp_second"])

    index.sync()

    points = _points(fake)
    left = points[vector_id(ENTITY_PAIN_POINT, pain_id)]["vector"]
    right = points[vector_id(ENTITY_PAIN_POINT, "pp_second")]["vector"]
    cluster = points[vector_id(ENTITY_CLUSTER, "cl_1")]["vector"]
    assert cluster == pytest.approx(
        [(a + b) / 2 for a, b in zip(left, right)]
    )


def test_a_cluster_with_no_indexed_members_is_skipped(
    index: SemanticIndex, fake: FakeQdrant, store: Store
) -> None:
    span_id = _spans(store)[0]
    _add_pain_point(
        store, "pp_unaccepted", span_id, _doc_of(store, span_id), accepted=0
    )
    _add_cluster(store, "cl_1", ["pp_unaccepted"])

    report = index.sync()

    assert vector_id(ENTITY_CLUSTER, "cl_1") not in _points(fake)
    assert any("cl_1" in error for error in report.errors)


# --- the status and the store ----------------------------------------------


def test_the_status_answers_without_a_qdrant(
    tmp_path: Path, store: Store, provider: FakeProvider
) -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    conn = connect(tmp_path / "product_hunter.db", read_only=True)
    adapter = QdrantAdapter(
        BASE_URL, transport=httpx.MockTransport(refuse), sleep=Sleeps()
    )
    try:
        index = SemanticIndex(conn, adapter, provider, config=_config())

        status = index.status()

        assert status["collection_exists"] is False
        assert status["points"] == 0
        assert status["counts"] == {"evidence": 2, "pain_points": 1, "clusters": 0}
        assert status["errors"]
    finally:
        conn.close()
        adapter.close()


def test_the_index_never_writes_to_the_store(
    index: SemanticIndex, store: Store
) -> None:
    before = _row_counts(store)

    index.sync()

    assert _row_counts(store) == before

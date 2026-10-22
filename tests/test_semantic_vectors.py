"""The shapes of the semantic layer: the point id, the payload, the config."""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from product_hunter.clustering.similarity import (
    Embedder,
    MixedVectorSpaceError,
    VectorSpace,
)
from product_hunter.config import (
    SemanticConfig,
    SemanticConfigError,
    load_semantic_config,
)
from product_hunter.semantic.embeddings import build_embedder, embed_all
from product_hunter.semantic.vectors import (
    ENTITY_CLUSTER,
    ENTITY_EVIDENCE,
    ENTITY_PAIN_POINT,
    ENTITY_TYPES,
    SearchFilter,
    VectorPayload,
    vector_id,
)

# The fields every point carries, and nothing else.
PAYLOAD_FIELDS = {
    "entity_id",
    "entity_type",
    "content_hash",
    "embedding_provider",
    "embedding_model",
    "embedding_model_version",
    "space_id",
    "dimensions",
    "index_version",
    "created_at",
    "updated_at",
    "source_id",
    "community_id",
    "domain",
    "cluster_id",
    "live_status",
    "embedding_status",
}


def _payload(**overrides) -> VectorPayload:
    data = {
        "entity_id": "ev_1",
        "entity_type": ENTITY_EVIDENCE,
        "content_hash": "sha256:abc",
        "embedding_provider": "local",
        "embedding_model": "a-model",
        "embedding_model_version": "1",
        "space_id": "space-1",
        "dimensions": 384,
        "index_version": "semantic-v1",
        "created_at": "2026-10-21T00:00:00+00:00",
        "updated_at": "2026-10-21T00:00:00+00:00",
    }
    data.update(overrides)
    return VectorPayload(**data)


def _config_data(**overrides) -> dict:
    data = {
        "version": 1,
        "index_version": "semantic-v1",
        "collection": "product_hunter_evidence",
        "url": "http://localhost:6333",
        "api_key_env": "QDRANT_API_KEY",
        "embedding": {
            "provider": "bge-m3-local",
            "model": "BAAI/bge-m3",
            "model_version": "1",
            "dimensions": 1024,
        },
        "distance": "Cosine",
        "timeout_seconds": 30,
        "max_attempts": 4,
        "backoff_seconds": 1,
        "write_batch_size": 64,
    }
    data.update(overrides)
    return data


def _write_config(path: Path, data: dict) -> Path:
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path


@pytest.fixture(autouse=True)
def _no_qdrant_environment(monkeypatch):
    """The qdrant overrides are off unless a test sets them."""
    monkeypatch.delenv("PH_QDRANT_URL", raising=False)
    monkeypatch.delenv("PH_QDRANT_COLLECTION", raising=False)


# --- the point id -----------------------------------------------------------


def test_the_point_id_is_deterministic():
    first = vector_id(ENTITY_EVIDENCE, "ev_1")
    second = vector_id(ENTITY_EVIDENCE, "ev_1")
    assert first == second


def test_the_point_id_is_a_uuid():
    point = vector_id(ENTITY_PAIN_POINT, "pp_1")
    assert str(uuid.UUID(point)) == point


def test_the_entity_type_is_part_of_the_point_id():
    assert vector_id(ENTITY_EVIDENCE, "x_1") != vector_id(ENTITY_CLUSTER, "x_1")


def test_an_unknown_entity_type_is_refused():
    with pytest.raises(ValueError):
        vector_id("document", "x_1")


def test_an_empty_entity_id_is_refused():
    with pytest.raises(ValueError):
        vector_id(ENTITY_EVIDENCE, "")


def test_every_entity_type_has_a_point_id():
    for entity_type in ENTITY_TYPES:
        assert vector_id(entity_type, "x_1")


# --- the payload ------------------------------------------------------------


def test_the_payload_takes_the_contract_fields():
    payload = _payload()
    assert payload.entity_id == "ev_1"
    assert payload.live_status == "live"
    assert payload.source_id == ""
    assert payload.embedding_status == "embedded"


def test_the_payload_refuses_an_unknown_field():
    with pytest.raises(ValidationError):
        _payload(api_key="secret")


def test_to_payload_returns_only_the_contract_fields():
    assert set(_payload().to_payload()) == PAYLOAD_FIELDS


def test_same_content_ignores_the_update_stamp():
    first = _payload(updated_at="2026-10-21T00:00:00+00:00")
    second = _payload(updated_at="2026-10-22T00:00:00+00:00")
    assert first.same_content(second)


def test_same_content_sees_a_changed_field():
    first = _payload()
    second = _payload(content_hash="sha256:def")
    assert not first.same_content(second)


def test_same_content_sees_a_changed_narrowing_field():
    first = _payload()
    second = _payload(cluster_id="cl_1")
    assert not first.same_content(second)


# --- the filter -------------------------------------------------------------


def test_an_empty_filter_is_no_filter():
    assert SearchFilter().to_qdrant_filter() == {}


def test_the_filter_makes_one_clause_per_field():
    clauses = SearchFilter(
        entity_type=ENTITY_EVIDENCE, source_id="hn_algolia"
    ).to_qdrant_filter()["must"]
    assert clauses == [
        {"key": "entity_type", "match": {"value": ENTITY_EVIDENCE}},
        {"key": "source_id", "match": {"value": "hn_algolia"}},
    ]


def test_the_filter_dates_are_a_range_on_created_at():
    clauses = SearchFilter(
        since="2026-10-01T00:00:00+00:00", until="2026-10-31T00:00:00+00:00"
    ).to_qdrant_filter()["must"]
    assert clauses == [
        {
            "key": "created_at",
            "range": {
                "gte": "2026-10-01T00:00:00+00:00",
                "lte": "2026-10-31T00:00:00+00:00",
            },
        }
    ]


def test_the_filter_carries_the_space_guard():
    clauses = SearchFilter(space_id="space-1").to_qdrant_filter()["must"]
    assert clauses == [{"key": "space_id", "match": {"value": "space-1"}}]


def test_the_filter_refuses_an_unknown_field():
    with pytest.raises(ValidationError):
        SearchFilter(unknown="x")


class _FakeEmbedder:
    """An embedder of the clustering wave's protocol."""

    def __init__(self, space: VectorSpace, vectors: list[tuple[float, ...]]) -> None:
        self._space = space
        self._vectors = vectors

    @property
    def space(self) -> VectorSpace:
        return self._space

    def embed(self, texts):
        return self._vectors


def _space(dimensions: int = 3) -> VectorSpace:
    return VectorSpace.of(
        provider="local", model="a-model", model_version="1", dimensions=dimensions
    )


# --- the embedder -----------------------------------------------------------


def test_the_embedder_is_the_model_the_config_names(tmp_path):
    path = _write_config(tmp_path / "semantic.yaml", _config_data())
    embedder = build_embedder(load_semantic_config(path))

    assert isinstance(embedder, Embedder)
    assert embedder.space.provider == "bge-m3-local"
    assert embedder.space.model == "BAAI/bge-m3"
    assert embedder.space.dimensions == 1024


def test_embed_all_returns_one_vector_per_text():
    embedder = _FakeEmbedder(_space(), [(1.0, 0.0, 0.0), (0.0, 1.0, 0.0)])

    assert embed_all(embedder, ["one", "two"]) == [(1.0, 0.0, 0.0), (0.0, 1.0, 0.0)]


def test_embed_all_refuses_a_vector_of_another_width():
    embedder = _FakeEmbedder(_space(), [(1.0, 0.0)])

    with pytest.raises(MixedVectorSpaceError):
        embed_all(embedder, ["one"])


# --- the config -------------------------------------------------------------

def test_a_valid_config_loads(tmp_path):
    path = _write_config(tmp_path / "semantic.yaml", _config_data())
    config = load_semantic_config(path)
    assert isinstance(config, SemanticConfig)
    assert config.collection == "product_hunter_evidence"
    assert config.embedding.dimensions == 1024
    assert config.api_key_env == "QDRANT_API_KEY"
    assert config.write_batch_size == 64


def test_a_missing_config_is_refused(tmp_path):
    with pytest.raises(SemanticConfigError):
        load_semantic_config(tmp_path / "semantic.yaml")


def test_an_unknown_key_is_refused(tmp_path):
    data = _config_data()
    data["surprise"] = 1
    path = _write_config(tmp_path / "semantic.yaml", data)
    with pytest.raises(SemanticConfigError):
        load_semantic_config(path)


def test_an_unreadable_config_is_refused(tmp_path):
    path = tmp_path / "semantic.yaml"
    path.write_text("version: [1\n", encoding="utf-8")
    with pytest.raises(SemanticConfigError):
        load_semantic_config(path)


def test_the_old_batch_size_key_is_refused(tmp_path):
    data = _config_data()
    data["batch_size"] = 64
    path = _write_config(tmp_path / "semantic.yaml", data)
    with pytest.raises(SemanticConfigError):
        load_semantic_config(path)


def test_an_unknown_device_is_refused(tmp_path):
    data = _config_data()
    data["embedding"]["device"] = "gpu"
    path = _write_config(tmp_path / "semantic.yaml", data)
    with pytest.raises(SemanticConfigError):
        load_semantic_config(path)


def test_the_device_may_be_auto_cpu_or_cuda(tmp_path):
    for device in ("auto", "cpu", "cuda"):
        data = _config_data()
        data["embedding"]["device"] = device
        path = _write_config(tmp_path / f"semantic-{device}.yaml", data)
        assert load_semantic_config(path).embedding.device == device


def test_the_space_version_carries_a_pinned_revision(tmp_path):
    data = _config_data()
    data["embedding"]["revision"] = "9f8a7b"
    path = _write_config(tmp_path / "semantic.yaml", data)
    config = load_semantic_config(path)
    assert config.space_model_version == "1@9f8a7b"
    assert load_semantic_config(_write_config(tmp_path / "plain.yaml", _config_data())).space_model_version == "1"


def test_the_url_is_overridable_by_the_environment(tmp_path, monkeypatch):
    path = _write_config(tmp_path / "semantic.yaml", _config_data())
    monkeypatch.setenv("PH_QDRANT_URL", "http://qdrant.internal:6333")
    config = load_semantic_config(path)
    assert config.url == "http://qdrant.internal:6333"


def test_the_collection_is_overridable_by_the_environment(tmp_path, monkeypatch):
    path = _write_config(tmp_path / "semantic.yaml", _config_data())
    monkeypatch.setenv("PH_QDRANT_COLLECTION", "other")
    config = load_semantic_config(path)
    assert config.collection == "other"


def test_an_empty_environment_value_is_ignored(tmp_path, monkeypatch):
    path = _write_config(tmp_path / "semantic.yaml", _config_data())
    monkeypatch.setenv("PH_QDRANT_URL", "")
    config = load_semantic_config(path)
    assert config.url == "http://localhost:6333"


def test_the_shipped_config_loads():
    config = load_semantic_config()
    assert config.collection == "product_hunter_evidence"
    assert config.embedding.provider == "bge-m3-local"
    assert config.embedding.model == "BAAI/bge-m3"
    assert config.embedding.dimensions == 1024
    assert config.distance == "Cosine"
    assert config.write_batch_size == 64

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
import hashlib

import pytest

from product_hunter.db import Store, connect, migrate, enqueue_job
from product_hunter.db.jobs import JobContext
from product_hunter.db.worker import Worker
from product_hunter.llm.base import ProviderError
from product_hunter.llm.routing import (
    ModelIdentity,
    ModelRole,
    ModelSelection,
    SelectionSource,
)
from product_hunter.handlers import (
    _collection_handler,
    _collection_url,
    _clustering_handler,
    _competitor_handler,
    _counter_evidence_handler,
    _extraction_handler,
    _normalization_handler,
    _deduplication_handler,
    _embedding_handler,
    _reporting_handler,
    _scoring_handler,
    _obsidian_sync_handler,
    _write_spend_ledger,
)
from product_hunter.runtime.identity import WorkerIdentity
from product_hunter.runtime.worker import WorkerRuntime


def _store_with_run() -> Store:
    store = Store.open(":memory:")
    now = datetime.now(timezone.utc).isoformat()
    store.conn.execute(
        "INSERT INTO research_runs (run_id, public_id, objective, original_request, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("run-1", "run-1", "test", "", "running", now, now),
    )
    store.conn.commit()
    return store


def test_collection_handler_no_targets_enqueues_normalization():
    store = _store_with_run()
    try:
        enqueue_job(store.conn, stage="COLLECTION", idempotency_key="coll-1", run_id="run-1")
        worker = Worker(store.conn, "w", {"COLLECTION": _collection_handler})
        outcome = worker.run_once()
        assert outcome is not None
        assert outcome.status == "succeeded"
        assert outcome.result["targets"] == 0
        execution = store.conn.execute(
            "SELECT stage, status FROM research_stage_executions "
            "WHERE run_id = 'run-1'"
        ).fetchone()
        assert tuple(execution) == ("evidence_collection", "succeeded")
        assert store.conn.execute(
            "SELECT COUNT(*) FROM research_stage_artifacts WHERE run_id = 'run-1'"
        ).fetchone()[0] == 0
        assert store.conn.execute(
            "SELECT COUNT(*) FROM tool_calls WHERE run_id = 'run-1'"
        ).fetchone()[0] == 0
        nxt = store.conn.execute("SELECT 1 FROM jobs WHERE stage='NORMALIZATION' AND run_id='run-1'").fetchone()
        assert nxt is not None
    finally:
        store.close()


def test_extraction_handler_uses_stored_document_text(tmp_path, monkeypatch):
    store = _store_with_run()
    try:
        source = "hn_algolia"
        now = datetime.now(timezone.utc).isoformat()
        store.conn.execute(
            "INSERT INTO sources (source_id, source_type, terms_status, allowed_use_json, "
            "enabled, reviewed_at, review_basis, collector, domains_json, rate_limit_json, "
            "robots_notes, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (source, "community", "permitted", '[\"discovery\",\"retrieval\",\"analysis\",\"storage\"]',
             1, now, "test source", "api", '[\"hn.algolia.com\"]', "{}", "", now, now),
        )
        store.conn.execute(
            "INSERT INTO fetches (fetch_id, source_id, requested_url, final_url, status, "
            "content_type, raw_sha256, bytes, fetched_at, terms_status, allowed_use_json, "
            "review_basis, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("fetch-1", source, "https://hn.algolia.com/api/v1/items/1",
             "https://hn.algolia.com/api/v1/items/1", 200, "application/json",
             "raw-hash", 10, now, "permitted", "[]", "test source", now),
        )
        body = "I spend every Friday reconciling invoices by hand and routinely miss errors."
        text_path = tmp_path / "document.txt"
        text_path.write_text(body, encoding="utf-8")
        digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
        store.conn.execute(
            "INSERT INTO documents (doc_id, fetch_id, source_id, url, title, text_sha256, "
            "text_path, raw_path, raw_sha256, parser_version, content_type, created_at, "
            "live_status, first_seen_at, last_seen_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("hn_1", "fetch-1", source, "https://news.ycombinator.com/item?id=1",
             "Title alone is not evidence", digest, str(text_path), "raw/path.json",
             "raw-hash", "v1", "application/json", now, "live", now, now),
        )
        call_id = "tc_discovery_test"
        store.conn.execute(
            "INSERT INTO tool_calls (tool_call_id, run_id, stage, agent, tool, purpose, "
            "occurred_at, query_or_operation, result_ref, result_count, status, source_id) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (call_id, "run-1", "discovery", "worker", "search", "test", now,
             "test query", "test-result", 1, "success", source),
        )
        store.conn.execute(
            "INSERT INTO research_tool_results (run_id, stage, tool_call_id, result_ref, "
            "payload_json, payload_sha256, saved_at) VALUES (?,?,?,?,?,?,?)",
            ("run-1", "discovery", call_id, "test-result", json.dumps([{"objectID": "1"}]),
             hashlib.sha256(json.dumps([{"objectID": "1"}]).encode()).hexdigest(), now),
        )
        store.conn.commit()

        captured = {}
        monkeypatch.setattr("product_hunter.handlers.repo_root", lambda: tmp_path)

        class _FakeConfig:
            provider_id = "test-provider"
            model_id = "test-model"
            extractor_version = "test-extractor"
            prompt_version = "test-prompt"
            normalization_version = "test-norm"

            class Limits:
                max_documents = 20
                max_calls = 5
                max_input_chars = 12000
                timeout_seconds = 120
                min_quote_chars = 20
                max_quote_chars = 2000
                chunk_chars = 8000
                chunk_overlap_chars = 400
                max_candidates_per_chunk = 3

            limits = Limits()

        monkeypatch.setattr("product_hunter.handlers.load_config", lambda: _FakeConfig())

        from product_hunter.llm.routing import (
            ModelRole,
            ModelSelection,
            ModelIdentity,
            SelectionSource,
        )

        class _MockRouter:
            def select(self, role):
                return ModelSelection(
                    role=role,
                    identity=ModelIdentity(
                        provider_id="test-provider", model_id="test-model"
                    ),
                    selection_source=SelectionSource.default,
                    fallback_index=0,
                    is_fallback=False,
                    catalog_version="test-catalog",
                    resolved_at=datetime.now(timezone.utc),
                )

        class _MockGenerator:
            def check(self):
                pass

            attempts = []
            name = "mock"

        monkeypatch.setattr(
            "product_hunter.handlers.build_router",
            lambda routing, config, discovery=None: _MockRouter(),
        )
        monkeypatch.setattr(
            "product_hunter.handlers.FallbackGenerator",
            lambda router, role, build: _MockGenerator(),
        )
        monkeypatch.setattr("product_hunter.handlers.OpenCodeGenerateProvider", lambda _config: object())
        def fake_extract(documents, *_args, **_kwargs):
            captured["documents"] = documents
            return SimpleNamespace(candidates=[])

        monkeypatch.setattr("product_hunter.handlers.extract_documents", fake_extract)
        monkeypatch.setattr(
            "product_hunter.evidence.EvidencePipeline.ingest_document",
            lambda *_args, **_kwargs: None,
        )
        job = enqueue_job(
            store.conn, stage="PAIN_EXTRACTION", idempotency_key="extract-text-1", run_id="run-1"
        )
        result = _extraction_handler(JobContext(store.conn, job, worker_id="w", lease_seconds=60))

        assert result["extracted"] == 0
        assert captured["documents"][0].text == body
    finally:
        store.close()


def test_collection_url_maps_only_reviewed_result_shapes():
    assert _collection_url(
        "hn_algolia", "https://news.ycombinator.com/item?id=123"
    ) == "https://hn.algolia.com/api/v1/items/123"
    assert _collection_url(
        "github_issues", "https://github.com/acme/app/issues/42"
    ) == "https://api.github.com/repos/acme/app/issues/42"
    assert _collection_url(
        "github_issues", "https://github.com/acme/app/pull/42"
    ) == "https://github.com/acme/app/pull/42"
    assert _collection_url(
        "github_issues", "https://github.com.evil.test/acme/app/issues/42"
    ) == "https://github.com.evil.test/acme/app/issues/42"


def test_scoring_handler_persists_empty_stage_evidence(tmp_path, monkeypatch):
    db_path = tmp_path / "authoritative.db"
    store = Store.open(db_path)
    try:
        now = datetime.now(timezone.utc).isoformat()
        store.conn.execute(
            "INSERT INTO research_runs (run_id, public_id, objective, original_request, "
            "status, created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
            ("run-score", "run-score", "test scoring", "test scoring", "running", now, now),
        )
        monkeypatch.setattr("product_hunter.handlers.repo_root", lambda: tmp_path)
        enqueue_job(
            store.conn, stage="SCORING", idempotency_key="score-empty", run_id="run-score"
        )
        outcome = Worker(store.conn, "w", {"SCORING": _scoring_handler}).run_once()
        assert outcome is not None and outcome.status == "succeeded"
        result = outcome.result

        assert result == {"scored": 0, "opportunities_created": 0}
        from product_hunter.db.research_gate import SqliteToolCallStore, load_run_evidence

        calls = SqliteToolCallStore(store.conn).list_tool_calls("run-score", stage="scoring")
        assert len(calls) == 1
        assert calls[0].tool == "duckdb"
        assert calls[0].result_is_durable is True
        evidence = load_run_evidence(store.conn, run_id="run-score", contract_version="test-v1")
        assert {artifact.kind for artifact in evidence.stages["scoring"].artifacts} == {
            "opportunity_score", "confidence"
        }
        assert all(artifact.item_count == 0 for artifact in evidence.stages["scoring"].artifacts)
    finally:
        store.close()


def test_clustering_handler_persists_empty_qdrant_stage_evidence(tmp_path, monkeypatch):
    from product_hunter.clustering.similarity import VectorSpace

    store = Store.open(tmp_path / "authoritative.db")
    try:
        now = datetime.now(timezone.utc).isoformat()
        store.conn.execute(
            "INSERT INTO research_runs (run_id, public_id, objective, original_request, "
            "status, created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
            ("run-cluster", "run-cluster", "test clustering", "test clustering", "running", now, now),
        )

        class EmptyEmbedder:
            space = VectorSpace.of(
                provider="test", model="test-model", model_version="1", dimensions=2
            )

            def embed(self, texts):
                assert texts == []
                return []

        class EmptyQdrant:
            def __init__(self, *_args, **_kwargs):
                pass

            def count(self, _collection):
                return 0

            def close(self):
                pass

        monkeypatch.setattr("product_hunter.handlers.build_embedder", lambda *_args, **_kwargs: EmptyEmbedder())
        monkeypatch.setattr("product_hunter.semantic.qdrant.QdrantAdapter", EmptyQdrant)
        enqueue_job(
            store.conn, stage="CLUSTERING", idempotency_key="cluster-empty", run_id="run-cluster"
        )
        outcome = Worker(store.conn, "w", {"CLUSTERING": _clustering_handler}).run_once()

        assert outcome is not None and outcome.status == "succeeded"
        assert outcome.result["clusters"] == 0
        from product_hunter.db.research_gate import SqliteToolCallStore, load_run_evidence

        calls = SqliteToolCallStore(store.conn).list_tool_calls("run-cluster", stage="clustering")
        assert any(call.tool == "qdrant" and call.result_is_durable for call in calls)
        evidence = load_run_evidence(store.conn, run_id="run-cluster", contract_version="test-v1")
        assert {artifact.kind for artifact in evidence.stages["clustering"].artifacts} >= {
            "cluster_assignments", "similarity_checks", "cluster_neighbors_checked",
            "neighbor_check", "duplicate_review",
        }
    finally:
        store.close()


class _EmptyEmbedder:
    def __init__(self, *_a, **_k):
        self.space = SimpleNamespace(space_id="test-space")

    def embed(self, texts):
        return []


class _EmptyQdrant:
    def __init__(self, *_a, **_k):
        pass

    def count(self, _collection):
        return 0

    def close(self):
        pass


class _FakeRouter:
    def select(self, role):
        return ModelSelection(
            role=role,
            identity=ModelIdentity(provider_id="opencode-go", model_id="beam-free"),
            selection_source=SelectionSource.pattern,
            fallback_index=0,
            is_fallback=False,
            catalog_version="cat-1",
            resolved_at=datetime.now(timezone.utc),
        )


class _FakeGenerator:
    def __init__(self, *_a, **_k):
        self.attempts = [
            SimpleNamespace(
                selection=SimpleNamespace(
                    identity=ModelIdentity(provider_id="opencode-go", model_id="beam-free"),
                    role=ModelRole.clustering,
                    selection_source=SelectionSource.pattern,
                    fallback_index=0,
                    is_fallback=False,
                    catalog_version="cat-1",
                ),
                latency_ms=5,
                ok=True,
                error=None,
                created_at="2026-01-01T00:00:00+00:00",
            )
        ]

    def check(self):
        return {}


def _capturing_engine(captured):
    class _CapturedEngine:
        def __init__(self, _embedder, _config, *, adjudicator=None, **_kwargs):
            captured["adjudicator"] = adjudicator

        def cluster(self, _pain_points):
            return SimpleNamespace(
                algorithm_version="v1",
                config_version="cv1",
                prompt_version="pv1",
                clusters=[],
                assignments=[],
                decisions=[],
                neighbor_checks=[],
                held=[],
                space=SimpleNamespace(space_id="test-space"),
                model_dump=lambda mode="json": {},
            )

    return _CapturedEngine


def test_clustering_wires_adjudicator_when_router_answers(tmp_path, monkeypatch):
    store = Store.open(tmp_path / "authoritative.db")
    captured = {}
    try:
        now = datetime.now(timezone.utc).isoformat()
        store.conn.execute(
            "INSERT INTO research_runs (run_id, public_id, objective, original_request, "
            "status, created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
            ("run-cluster", "run-cluster", "test clustering", "test clustering", "running", now, now),
        )
        store.conn.commit()

        monkeypatch.setattr(
            "product_hunter.handlers.build_router",
            lambda _routing, _config: _FakeRouter(),
        )
        monkeypatch.setattr("product_hunter.handlers.FallbackGenerator", _FakeGenerator)
        monkeypatch.setattr(
            "product_hunter.clustering.engine.ClusteringEngine", _capturing_engine(captured)
        )
        monkeypatch.setattr(
            "product_hunter.handlers.build_embedder", lambda *_a, **_k: _EmptyEmbedder()
        )
        monkeypatch.setattr(
            "product_hunter.semantic.qdrant.QdrantAdapter", _EmptyQdrant
        )

        enqueue_job(
            store.conn, stage="CLUSTERING", idempotency_key="cluster-wired", run_id="run-cluster"
        )
        outcome = Worker(store.conn, "w", {"CLUSTERING": _clustering_handler}).run_once()

        assert outcome is not None and outcome.status == "succeeded"
        assert captured["adjudicator"] is not None
        rows = store.conn.execute(
            "SELECT count(*) FROM model_executions WHERE run_id = ? AND stage = 'clustering'",
            ("run-cluster",),
        ).fetchone()[0]
        assert rows == 1
    finally:
        store.close()


def test_clustering_holds_pair_when_routing_fails(tmp_path, monkeypatch):
    store = Store.open(tmp_path / "authoritative.db")
    captured = {}
    try:
        now = datetime.now(timezone.utc).isoformat()
        store.conn.execute(
            "INSERT INTO research_runs (run_id, public_id, objective, original_request, "
            "status, created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
            ("run-cluster-2", "run-cluster-2", "test clustering", "test clustering", "running", now, now),
        )
        store.conn.commit()

        def boom(_routing, _config):
            raise ProviderError("no models in catalogue")

        monkeypatch.setattr("product_hunter.handlers.build_router", boom)
        monkeypatch.setattr(
            "product_hunter.clustering.engine.ClusteringEngine", _capturing_engine(captured)
        )
        monkeypatch.setattr(
            "product_hunter.handlers.build_embedder", lambda *_a, **_k: _EmptyEmbedder()
        )
        monkeypatch.setattr(
            "product_hunter.semantic.qdrant.QdrantAdapter", _EmptyQdrant
        )

        enqueue_job(
            store.conn, stage="CLUSTERING", idempotency_key="cluster-fail", run_id="run-cluster-2"
        )
        outcome = Worker(store.conn, "w", {"CLUSTERING": _clustering_handler}).run_once()

        assert outcome is not None and outcome.status == "succeeded"
        assert captured["adjudicator"] is None
        rows = store.conn.execute(
            "SELECT count(*) FROM model_executions WHERE run_id = ? AND stage = 'clustering'",
            ("run-cluster-2",),
        ).fetchone()[0]
        assert rows == 0
    finally:
        store.close()


def test_counter_evidence_searches_required_families_and_persists_explicit_zero(monkeypatch):
    store = _store_with_run()
    try:
        now = datetime.now(timezone.utc).isoformat()
        store.conn.execute(
            "INSERT INTO sources (source_id, source_type, terms_status, allowed_use_json, "
            "enabled, reviewed_at, review_basis, collector, domains_json, rate_limit_json, "
            "robots_notes, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("hn_algolia", "community", "permitted", "[]", 1, now, "test source",
             "api", '["hn.algolia.com"]', "{}", "", now, now),
        )
        store.conn.commit()
        monkeypatch.setattr(
            "product_hunter.handlers._live_search",
            lambda _query: ([], None, []),
        )
        job = enqueue_job(
            store.conn, stage="COUNTER_EVIDENCE", idempotency_key="counter-zero",
            run_id="run-1",
        )
        outcome = Worker(
            store.conn, "w", {"COUNTER_EVIDENCE": _counter_evidence_handler}
        ).run_once()

        assert outcome is not None and outcome.status == "succeeded"
        calls = store.conn.execute(
            "SELECT query_family, status FROM tool_calls WHERE run_id = ? AND stage = ?",
            ("run-1", "counter_evidence"),
        ).fetchall()
        assert len(calls) == 11
        assert {row[0] for row in calls} >= {
            "direct_pain", "failed_solution", "alternative_to",
            "counter_not_worth", "counter_complaints", "counter_workaround",
        }
        artifacts = store.conn.execute(
            "SELECT kind, item_count FROM research_stage_artifacts "
            "WHERE run_id = ? AND stage = ?",
            ("run-1", "counter_evidence"),
        ).fetchall()
        counter_artifact = next(row for row in artifacts if row[0] == "counter_evidence")
        assert counter_artifact[1] == 0
        execution = store.conn.execute(
            "SELECT zero_result_json FROM research_stage_executions "
            "WHERE run_id = ? AND stage = ?",
            ("run-1", "counter_evidence"),
        ).fetchone()
        assert execution is not None
        zero = json.loads(execution[0])
        assert zero["recorded"] is True
        assert set(zero["required_query_families"]) >= {
            "direct_pain", "failed_solution", "alternative_to",
        }
    finally:
        store.close()


def test_competitor_searches_required_families_and_specialist_queries(monkeypatch):
    store = _store_with_run()
    try:
        now = datetime.now(timezone.utc).isoformat()
        store.conn.execute(
            "INSERT INTO sources (source_id, source_type, terms_status, allowed_use_json, "
            "enabled, reviewed_at, review_basis, collector, domains_json, rate_limit_json, "
            "robots_notes, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("hn_algolia", "community", "permitted", "[]", 1, now, "test source",
             "api", '["hn.algolia.com"]', "{}", "", now, now),
        )
        store.conn.commit()
        monkeypatch.setattr(
            "product_hunter.handlers._live_search",
            lambda _query: (
                [("hn_algolia", {"title": "Invoice workflow tool", "url": "https://example.com/tool"})],
                None,
                ["example.com"],
            ),
        )
        enqueue_job(
            store.conn, stage="COMPETITOR_RESEARCH", idempotency_key="competitor-plan",
            run_id="run-1",
        )
        outcome = Worker(
            store.conn, "w", {"COMPETITOR_RESEARCH": _competitor_handler}
        ).run_once()

        assert outcome is not None and outcome.status == "succeeded"
        calls = store.conn.execute(
            "SELECT query_family FROM tool_calls WHERE run_id = ? AND stage = ?",
            ("run-1", "investigation"),
        ).fetchall()
        assert len(calls) == 13
        assert {row[0] for row in calls} >= {
            "direct_pain", "failed_solution", "alternative_to",
            "competitor_alternative", "competitor_existing_solution",
            "competitor_software", "competitor_pricing",
        }
    finally:
        store.close()


def test_normalization_handler_persists_normalized_fields():
    store = _store_with_run()
    try:
        job = enqueue_job(store.conn, stage="NORMALIZATION", idempotency_key="norm-1", run_id="run-1")
        ctx = JobContext(store.conn, job, worker_id="w", lease_seconds=60)
        result = _normalization_handler(ctx)
        assert result["normalized"] == 0
        assert result["total"] == 0
        nxt = store.conn.execute("SELECT 1 FROM jobs WHERE stage='DEDUPLICATION' AND run_id='run-1'").fetchone()
        assert nxt is not None
    finally:
        store.close()


def test_deduplication_handler_sets_canonical():
    store = _store_with_run()
    try:
        # minimal evidence rows with same hash
        store.conn.execute(
            "INSERT INTO sources (source_id, source_type, terms_status, allowed_use_json, enabled, reviewed_at, review_basis, collector, domains_json, rate_limit_json, robots_notes, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("hn_algolia", "community", "permitted", "[]", 1, "2026-01-01T00:00:00Z", "", "api", "[]", "{}", "", "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
        )
        store.conn.execute(
            "INSERT INTO fetches (fetch_id, source_id, requested_url, final_url, status, content_type, raw_sha256, bytes, fetched_at, terms_status, allowed_use_json, review_basis, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("f1", "hn_algolia", "u", "u", 200, "text/html", "h", 0, "2026-01-01T00:00:00Z", "permitted", "[]", "", "2026-01-01T00:00:00Z"),
        )
        store.conn.execute(
            "INSERT INTO documents (doc_id, fetch_id, source_id, url, title, text_sha256, text_path, raw_path, raw_sha256, parser_version, content_type, created_at, live_status, first_seen_at, last_seen_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("hn_1", "f1", "hn_algolia", "u", "t", "h", "p", "r", "h", "v1", "text/html", "2026-01-01T00:00:00Z", "live", "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
        )
        call_id = "tc_dedup_discovery"
        result_payload = json.dumps([{"objectID": "1"}], separators=(",", ":"))
        store.conn.execute(
            "INSERT INTO tool_calls (tool_call_id, run_id, stage, agent, tool, purpose, "
            "query_or_operation, result_ref, result_count, status, occurred_at, source_id) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (call_id, "run-1", "discovery", "worker", "search", "test", "test", "result-1", 1,
             "success", "2026-01-01T00:00:00Z", "hn_algolia"),
        )
        store.conn.execute(
            "INSERT INTO research_tool_results (tool_call_id, run_id, stage, result_ref, "
            "payload_json, payload_sha256, saved_at) VALUES (?,?,?,?,?,?,?)",
            (call_id, "run-1", "discovery", "result-1", result_payload,
             hashlib.sha256(result_payload.encode()).hexdigest(), "2026-01-01T00:00:00Z"),
        )
        store.conn.execute(
            "INSERT INTO evidence (span_id, doc_id, quote, char_start, char_end, match_mode, occurrence, provider, model, extractor_version, prompt_version, normalization_version, raw_path, raw_sha256, text_sha256, evidence_hash, strength, strength_score, reliability, reliability_score, derivation_version, live_status, created_at, first_seen_at, last_seen_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("s1", "hn_1", "quote one here", 0, 14, "exact", 1, "p", "m", "v1", "p1", "norm-v1", "r", "h", "h", "hash1", "weak", 0.1, "low", 0.1, "v1", "live", "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
        )
        store.conn.execute(
            "INSERT INTO evidence (span_id, doc_id, quote, char_start, char_end, match_mode, occurrence, provider, model, extractor_version, prompt_version, normalization_version, raw_path, raw_sha256, text_sha256, evidence_hash, strength, strength_score, reliability, reliability_score, derivation_version, live_status, created_at, first_seen_at, last_seen_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("s2", "hn_1", "quote one here", 0, 14, "exact", 1, "p", "m", "v1", "p1", "norm-v1", "r", "h", "h", "hash1", "weak", 0.1, "low", 0.1, "v1", "live", "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
        )
        store.conn.commit()
        job = enqueue_job(store.conn, stage="DEDUPLICATION", idempotency_key="dedup-1", run_id="run-1")
        ctx = JobContext(store.conn, job, worker_id="w", lease_seconds=60)
        result = _deduplication_handler(ctx)
        assert result["deduplicated"] == 1
        nxt = store.conn.execute("SELECT 1 FROM jobs WHERE stage='ENTITY_RESOLUTION' AND run_id='run-1'").fetchone()
        assert nxt is not None
    finally:
        store.close()


def test_embedding_handler_monkeypatched(monkeypatch):
    store = _store_with_run()
    try:
        class FakeReport:
            indexed = 3
            unchanged = 1
            removed = 0
            failed = 0

        class FakeIndex:
            def __init__(self, *a, **k):
                pass

            def sync(self):
                return FakeReport()

            @property
            def space(self):
                class S:
                    space_id = "space-1"
                    provider = "bge-m3-local"
                    model = "BAAI/bge-m3"
                    model_version = "1"
                    dimensions = 1024

                return S()

        monkeypatch.setattr("product_hunter.semantic.index.SemanticIndex", FakeIndex)
        monkeypatch.setattr("product_hunter.config.load_semantic_config", lambda: None)
        # load_semantic_config returns object with attributes; patch more directly
        import product_hunter.handlers as h

        def fake_load():
            class C:
                url = "http://localhost:6333"
                api_key_env = "QDRANT_API_KEY"
                timeout_seconds = 5
                max_attempts = 2
                backoff_seconds = 0

            return C()

        monkeypatch.setattr("product_hunter.config.load_semantic_config", fake_load)
        monkeypatch.setattr("product_hunter.semantic.embeddings.build_embedder", lambda *a, **k: None)
        monkeypatch.setattr("product_hunter.semantic.qdrant.QdrantAdapter", lambda *a, **k: None)

        job = enqueue_job(store.conn, stage="EMBEDDING", idempotency_key="emb-1", run_id="run-1")
        ctx = JobContext(store.conn, job, worker_id="w", lease_seconds=60)
        result = _embedding_handler(ctx)
        assert result["embedded"] is True
        assert result["space_id"] == "space-1"
        nxt = store.conn.execute("SELECT 1 FROM jobs WHERE stage='CLUSTERING' AND run_id='run-1'").fetchone()
        assert nxt is not None
    finally:
        store.close()


def test_reporting_handler_writes_summary(tmp_path, monkeypatch):
    store = _store_with_run()
    try:
        monkeypatch.setattr("product_hunter.config.repo_root", lambda: tmp_path)
        job = enqueue_job(store.conn, stage="REPORTING", idempotency_key="rep-1", run_id="run-1")
        ctx = JobContext(store.conn, job, worker_id="w", lease_seconds=60)
        result = _reporting_handler(ctx)
        assert result["reported"] is True
        out = tmp_path / "data" / "reports" / "run-1.json"
        assert out.is_file()
        data = json.loads(out.read_text())
        assert "documents" in data
        nxt = store.conn.execute("SELECT 1 FROM jobs WHERE stage='OBSIDIAN_SYNC' AND run_id='run-1'").fetchone()
        assert nxt is not None
    finally:
        store.close()


def test_production_worker_does_not_complete_a_run_blocked_by_the_contract(
    tmp_path, monkeypatch
):
    store = _store_with_run()
    try:
        monkeypatch.setattr("product_hunter.config.repo_root", lambda: tmp_path)
        job = enqueue_job(store.conn, stage="OBSIDIAN_SYNC", idempotency_key="obs-1", run_id="run-1")
        runtime = WorkerRuntime(
            store.conn,
            WorkerIdentity("w", "test", 1, datetime.now(timezone.utc).isoformat()),
            {"OBSIDIAN_SYNC": _obsidian_sync_handler},
            lease_seconds=60,
            heartbeat_seconds=0,
        )

        outcome = runtime.run_once()

        assert outcome is not None
        assert outcome.status == "failed"
        current = store.conn.execute(
            "SELECT status, last_error FROM jobs WHERE id = ?", (job.id,)
        ).fetchone()
        assert current["status"] == "queued"
        assert "gate" in current["last_error"].lower()
        assert not (tmp_path / "data" / "vault").exists()
    finally:
        store.close()


def test_spend_ledger_rows_are_written_under_contract_stages():
    store = _store_with_run()
    try:
        conn = store.conn
        _write_spend_ledger(
            conn,
            run_id="run-1",
            stage="DISCOVERY",
            tool="search",
            unit_type="search_queries",
            units=3,
        )
        _write_spend_ledger(
            conn,
            run_id="run-1",
            stage="PAIN_EXTRACTION",
            tool="llm",
            unit_type="model_calls",
            units=0,
        )
        # A stage with no contract mapping keeps its job name.
        _write_spend_ledger(
            conn,
            run_id="run-1",
            stage="REPORTING",
            tool="report",
            unit_type="usd_cost",
            units=0,
        )
        conn.commit()
        rows = conn.execute(
            "SELECT stage, unit_type, units FROM spend_ledger "
            "WHERE run_id = 'run-1' ORDER BY stage, unit_type"
        ).fetchall()
        assert {tuple(row) for row in rows} == {
            ("discovery", "search_queries", 3),
            ("normalization", "model_calls", 0),
            ("REPORTING", "usd_cost", 0),
        }
    finally:
        store.close()

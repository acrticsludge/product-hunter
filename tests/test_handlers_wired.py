from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from product_hunter.db import Store, connect, migrate, enqueue_job
from product_hunter.db.jobs import JobContext
from product_hunter.handlers import (
    _collection_handler,
    _normalization_handler,
    _deduplication_handler,
    _embedding_handler,
    _reporting_handler,
    _obsidian_sync_handler,
)


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
        job = enqueue_job(store.conn, stage="COLLECTION", idempotency_key="coll-1", run_id="run-1")
        ctx = JobContext(store.conn, job, worker_id="w", lease_seconds=60)
        result = _collection_handler(ctx)
        assert result["targets"] == 0
        nxt = store.conn.execute("SELECT 1 FROM jobs WHERE stage='NORMALIZATION' AND run_id='run-1'").fetchone()
        assert nxt is not None
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
            ("f1", "hn_algolia", "u", "u", "success", "text/html", "h", 0, "2026-01-01T00:00:00Z", "permitted", "[]", "", "2026-01-01T00:00:00Z"),
        )
        store.conn.execute(
            "INSERT INTO documents (doc_id, fetch_id, source_id, url, title, text_sha256, text_path, raw_path, raw_sha256, parser_version, content_type, created_at, live_status, first_seen_at, last_seen_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("d1", "f1", "hn_algolia", "u", "t", "h", "p", "r", "h", "v1", "text/html", "2026-01-01T00:00:00Z", "live", "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
        )
        store.conn.execute(
            "INSERT INTO evidence (span_id, doc_id, quote, char_start, char_end, match_mode, occurrence, provider, model, extractor_version, prompt_version, normalization_version, raw_path, raw_sha256, text_sha256, evidence_hash, strength, strength_score, reliability, reliability_score, derivation_version, live_status, created_at, first_seen_at, last_seen_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("s1", "d1", "quote one here", 0, 14, "exact", 1, "p", "m", "v1", "p1", "norm-v1", "r", "h", "h", "hash1", "weak", 0.1, "low", 0.1, "v1", "live", "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
        )
        store.conn.execute(
            "INSERT INTO evidence (span_id, doc_id, quote, char_start, char_end, match_mode, occurrence, provider, model, extractor_version, prompt_version, normalization_version, raw_path, raw_sha256, text_sha256, evidence_hash, strength, strength_score, reliability, reliability_score, derivation_version, live_status, created_at, first_seen_at, last_seen_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("s2", "d1", "quote one here", 0, 14, "exact", 1, "p", "m", "v1", "p1", "norm-v1", "r", "h", "h", "hash1", "weak", 0.1, "low", 0.1, "v1", "live", "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
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


def test_obsidian_sync_handler_generates_vault(tmp_path, monkeypatch):
    store = _store_with_run()
    try:
        monkeypatch.setattr("product_hunter.config.repo_root", lambda: tmp_path)
        job = enqueue_job(store.conn, stage="OBSIDIAN_SYNC", idempotency_key="obs-1", run_id="run-1")
        ctx = JobContext(store.conn, job, worker_id="w", lease_seconds=60)
        result = _obsidian_sync_handler(ctx)
        assert "written" in result
        assert (tmp_path / "data" / "vault").is_dir()
    finally:
        store.close()
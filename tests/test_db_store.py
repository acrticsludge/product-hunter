from __future__ import annotations

from pathlib import Path

import pytest

from conftest import (
    EXTRACTOR,
    NORMALIZATION,
    PROMPT,
    _build_run,
    _fixture_fetch,
    _write_jsonl,
)
from product_hunter.db import Store, StoreConflict, StoreError
from product_hunter.extraction import ExtractedCandidate
from product_hunter.sources import load_registry

def _open_store(tmp_path: Path) -> Store:
    return Store.open(tmp_path / "product_hunter.db")


def test_import_run_populates_the_authoritative_store(tmp_path: Path) -> None:
    run_dir, fetch_log, documents, accepted = _build_run(tmp_path)
    store = _open_store(tmp_path)
    try:
        summary = store.import_run(
            run_dir, fetch_log=fetch_log, registry=load_registry()
        )
        assert summary.run_id == "run-test"
        assert summary.sources == 3
        assert summary.fetches == 1
        assert summary.documents == 5
        assert summary.evidence == 2
        assert summary.pain_points == 1
        assert summary.authors == 5
        assert summary.communities == 1
        assert summary.author_links == 5
        assert summary.rejects == 1
        assert summary.artifacts == 6
        assert summary.projections == 1

        assert store.integrity_check() == "ok"
        assert store.foreign_key_violations() == []

        count = lambda table: store.conn.execute(  # noqa: E731
            f"SELECT count(*) FROM {table}"
        ).fetchone()[0]
        assert count("sources") == 3
        assert count("fetches") == 1
        assert count("documents") == 5
        assert count("evidence") == 2
        assert count("pain_points") == 1
        assert count("authors") == 5
        assert count("communities") == 1
        assert count("author_links") == 5
        assert count("research_runs") == 1
        assert count("research_artifacts") == 6
        assert count("research_events") == 4
        assert count("projection_tasks") == 1

        joined = store.conn.execute(
            """
            SELECT p.pain_id, p.normalized_key, d.raw_sha256, f.raw_path
            FROM pain_points p
            JOIN documents d ON d.doc_id = p.doc_id
            JOIN fetches f ON f.fetch_id = d.fetch_id
            WHERE p.pain_id = ?
            """,
            (accepted.pain_id,),
        ).fetchone()
        assert joined["normalized_key"] == "operations:paste-totals:spreadsheet"
        assert joined["raw_sha256"] == accepted.raw_sha256
        assert joined["raw_path"].startswith("data/raw/hn_algolia/")

        non_accepted = store.conn.execute(
            "SELECT provider, model, extractor_version, prompt_version, "
            "normalization_version, raw_path, raw_sha256, text_sha256 "
            "FROM evidence WHERE doc_id = ?",
            (documents[1].doc_id,),
        ).fetchone()
        assert non_accepted["provider"] == "opencode-go"
        assert non_accepted["model"] == "deepseek-v4.1-flash"
        assert non_accepted["extractor_version"] == EXTRACTOR
        assert non_accepted["prompt_version"] == PROMPT
        assert non_accepted["normalization_version"] == NORMALIZATION
        assert non_accepted["raw_sha256"] == documents[1].raw_sha256
        assert non_accepted["text_sha256"] == documents[1].text_sha256
        assert non_accepted["raw_path"].startswith("data/raw/hn_algolia/")

        projection = store.conn.execute(
            "SELECT target, entity_type, entity_id, entity_version, status, "
            "idempotency_key, payload_json, max_attempts "
            "FROM projection_tasks"
        ).fetchone()
        assert projection["target"] == "obsidian"
        assert projection["entity_type"] == "run"
        assert projection["entity_id"] == "run-test"
        assert projection["entity_version"] == "run-test"
        assert projection["status"] == "pending"
        assert projection["idempotency_key"] == "run_imported:run-test"
        assert projection["payload_json"] == '{"run_id": "run-test"}'
        assert projection["max_attempts"] == 5
    finally:
        store.close()


def test_replaying_a_run_is_idempotent(tmp_path: Path) -> None:
    run_dir, fetch_log, _documents, _accepted = _build_run(tmp_path)
    store = _open_store(tmp_path)
    try:
        first = store.import_run(run_dir, fetch_log=fetch_log, registry=load_registry())
        second = store.import_run(run_dir, fetch_log=fetch_log, registry=load_registry())
        assert first.pain_points == 1
        assert second.sources == 3
        assert second.fetches == 0
        assert second.documents == 0
        assert second.evidence == 0
        assert second.pain_points == 0
        assert second.authors == 0
        assert second.communities == 0
        assert second.author_links == 0
        assert second.rejects == 0
        assert second.artifacts == 0
        assert second.projections == 0
        assert store.conn.execute("SELECT count(*) FROM pain_points").fetchone()[0] == 1
        assert store.conn.execute("SELECT count(*) FROM projection_tasks").fetchone()[0] == 1
    finally:
        store.close()


def test_changed_document_under_the_same_id_is_refused(tmp_path: Path) -> None:
    run_dir, fetch_log, documents, _accepted = _build_run(tmp_path)
    store = _open_store(tmp_path)
    try:
        store.import_run(run_dir, fetch_log=fetch_log, registry=load_registry())
        changed = documents[0].model_copy(update={"text_sha256": "f" * 64})
        _write_jsonl(run_dir / "documents.jsonl", [changed, documents[1]])
        with pytest.raises(StoreConflict):
            store.import_run(run_dir, fetch_log=fetch_log, registry=load_registry())
        assert store.conn.execute("SELECT count(*) FROM documents").fetchone()[0] == 5
    finally:
        store.close()


def test_a_changed_pain_point_detail_is_refused(tmp_path: Path) -> None:
    run_dir, fetch_log, _documents, _accepted = _build_run(tmp_path)
    store = _open_store(tmp_path)
    try:
        store.import_run(run_dir, fetch_log=fetch_log, registry=load_registry())
        rows = [
            ExtractedCandidate.model_validate_json(line)
            for line in (run_dir / "candidates.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        rows[0] = rows[0].model_copy(update={"consequence": "a different cost"})
        _write_jsonl(run_dir / "candidates.jsonl", rows)
        with pytest.raises(StoreConflict):
            store.import_run(run_dir, fetch_log=fetch_log, registry=load_registry())
    finally:
        store.close()


def test_a_changed_evidence_detail_is_refused(tmp_path: Path) -> None:
    run_dir, fetch_log, _documents, _accepted = _build_run(tmp_path)
    store = _open_store(tmp_path)
    try:
        store.import_run(run_dir, fetch_log=fetch_log, registry=load_registry())
        rows = [
            ExtractedCandidate.model_validate_json(line)
            for line in (run_dir / "candidates.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        rows[0] = rows[0].model_copy(update={"model_quote": "a paraphrase"})
        _write_jsonl(run_dir / "candidates.jsonl", rows)
        with pytest.raises(StoreConflict):
            store.import_run(run_dir, fetch_log=fetch_log, registry=load_registry())
    finally:
        store.close()


def test_a_changed_document_title_is_refused(tmp_path: Path) -> None:
    run_dir, fetch_log, documents, _accepted = _build_run(tmp_path)
    store = _open_store(tmp_path)
    try:
        store.import_run(run_dir, fetch_log=fetch_log, registry=load_registry())
        changed = documents[0].model_copy(update={"title": "A different title"})
        _write_jsonl(run_dir / "documents.jsonl", [changed, *documents[1:]])
        with pytest.raises(StoreConflict):
            store.import_run(run_dir, fetch_log=fetch_log, registry=load_registry())
    finally:
        store.close()


def test_a_document_without_its_fetch_is_refused(tmp_path: Path) -> None:
    run_dir, fetch_log, _documents, _accepted = _build_run(tmp_path)
    fetch_log.unlink()
    _write_jsonl(fetch_log, [])
    store = _open_store(tmp_path)
    try:
        with pytest.raises(StoreError):
            store.import_run(run_dir, fetch_log=fetch_log, registry=load_registry())
        assert store.conn.execute("SELECT count(*) FROM documents").fetchone()[0] == 0
    finally:
        store.close()


def test_a_missing_manifest_is_refused(tmp_path: Path) -> None:
    run_dir, fetch_log, _documents, _accepted = _build_run(tmp_path)
    (run_dir / "manifest.json").unlink()
    store = _open_store(tmp_path)
    try:
        with pytest.raises(StoreError):
            store.import_run(run_dir, fetch_log=fetch_log, registry=load_registry())
    finally:
        store.close()


def test_a_fetch_needs_its_source_in_the_store(tmp_path: Path) -> None:
    fetch, _raw = _fixture_fetch(tmp_path)
    store = _open_store(tmp_path)
    try:
        with pytest.raises(StoreError):
            store.import_fetch(fetch)
    finally:
        store.close()

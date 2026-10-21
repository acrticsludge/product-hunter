from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from product_hunter.db import Store, StoreConflict, StoreError
from product_hunter.documents import Document, parse_documents
from product_hunter.extraction import ExtractedCandidate, ExtractionCall, ExtractionReject
from product_hunter.fetch import FetchRecord
from product_hunter.normalization import Domain, normalize_fields
from product_hunter.sources import load_registry

FIXTURES = Path(__file__).parent / "fixtures"
NOW = "2026-10-21T00:00:00+00:00"
EXTRACTOR = "phase1-0.3"
PROMPT = "extract-v3"
NORMALIZATION = "norm-v1"


def _fixture_fetch(tmp_path: Path) -> tuple[FetchRecord, bytes]:
    raw = (FIXTURES / "hn_search.json").read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    raw_path = f"data/raw/hn_algolia/{digest}.json"
    destination = tmp_path / raw_path
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(raw)
    fetch = FetchRecord(
        fetch_id="fetch_hn_test",
        source_id="hn_algolia",
        requested_url="https://hn.algolia.com/api/v1/search?query=invoice",
        final_url="https://hn.algolia.com/api/v1/search?query=invoice",
        status=200,
        content_type="application/json",
        raw_sha256=digest,
        raw_path=raw_path,
        bytes=len(raw),
        fetched_at=NOW,
        terms_status="permitted",
        allowed_use=["discovery", "retrieval", "analysis", "storage"],
        review_basis="fixture",
        user_agent="ProductHunter/test",
        latency_ms=5,
        retry_count=0,
    )
    return fetch, raw


def _candidate(
    document: Document,
    *,
    quote: str,
    accepted: bool,
) -> ExtractedCandidate:
    start = document.text.find(quote)
    assert start >= 0, f"quote is not in document {document.doc_id}"
    end = start + len(quote)
    span_id = "ev_" + hashlib.sha256(
        f"{document.doc_id}|{start}|{end}|{quote}".encode()
    ).hexdigest()[:16]
    candidate_id = "cand_" + hashlib.sha256(
        f"{document.doc_id}|{start}|{end}|{quote}|{EXTRACTOR}".encode()
    ).hexdigest()[:16]
    pain_id = (
        "pp_" + hashlib.sha256(f"{span_id}|{EXTRACTOR}".encode()).hexdigest()[:16]
        if accepted
        else None
    )
    normalized = None
    if accepted:
        normalized = normalize_fields(
            domain=Domain.operations,
            actor="finance lead",
            action="paste totals",
            object_="spreadsheet",
            consequence="a lost Saturday",
            normalization_version=NORMALIZATION,
        )
    return ExtractedCandidate(
        candidate_id=candidate_id,
        pain_id=pain_id,
        doc_id=document.doc_id,
        fetch_id=document.fetch_id,
        source_id=document.source_id,
        url=document.url,
        span_id=span_id,
        quote=quote,
        char_start=start,
        char_end=end,
        match_mode="exact",
        model_quote=quote,
        occurrence=1,
        pain_validity="genuine" if accepted else "not_pain",
        reason="recurring_manual_work" if accepted else "negative_opinion",
        pain_statement=(
            "the team pastes invoice totals by hand"
            if accepted
            else "generic complaint with no workflow"
        ),
        severity=4 if accepted else 0,
        frequency="monthly" if accepted else "unknown",
        confidence=0.9,
        rationale="the source states the workflow and the cost",
        domain=normalized.domain.value if normalized else None,
        actor=normalized.actor if normalized else None,
        action=normalized.action if normalized else None,
        object=normalized.object if normalized else None,
        consequence=normalized.consequence if normalized else None,
        normalized_key=normalized.normalized_key if normalized else None,
        normalization_version=NORMALIZATION,
        accepted=accepted,
        provider="opencode-go",
        model="deepseek-v4.1-flash",
        extractor_version=EXTRACTOR,
        prompt_version=PROMPT,
        raw_path=document.raw_path,
        raw_sha256=document.raw_sha256,
        text_sha256=document.text_sha256,
        created_at=NOW,
    )


def _reject(document: Document) -> ExtractionReject:
    return ExtractionReject(
        reject_id="rej_1",
        doc_id=document.doc_id,
        fetch_id=document.fetch_id,
        source_id=document.source_id,
        reason_code="quote_not_found",
        detail="the quote is not in the document",
        model_quote="a paraphrase",
        provider="opencode-go",
        model="deepseek-v4.1-flash",
        extractor_version=EXTRACTOR,
        prompt_version=PROMPT,
        created_at=NOW,
    )


def _call(document: Document) -> ExtractionCall:
    return ExtractionCall(
        call_id="call_1",
        doc_id=document.doc_id,
        chunk_index=0,
        provider="opencode-go",
        model="deepseek-v4.1-flash",
        extractor_version=EXTRACTOR,
        prompt_version=PROMPT,
        input_chars=500,
        latency_ms=50,
        candidate_count=1,
        accepted_count=1,
        rejected_count=0,
        status="ok",
        created_at=NOW,
    )


def _write_jsonl(path: Path, rows: list[object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(row.model_dump_json() + "\n")  # type: ignore[attr-defined]


def _build_run(tmp_path: Path) -> tuple[Path, Path, list[Document], ExtractedCandidate]:
    fetch, raw = _fixture_fetch(tmp_path)
    documents = parse_documents(fetch, raw, repo_root=tmp_path)
    accepted_doc = documents[0]
    rejected_doc = documents[1]
    accepted = _candidate(
        accepted_doc,
        quote="Every month I export the invoices to CSV and paste the totals into a spreadsheet by hand.",
        accepted=True,
    )
    rejected = _candidate(
        rejected_doc,
        quote="This is just another invoicing tool and honestly none of these apps are any good.",
        accepted=False,
    )
    run_id = "run-test"
    run_dir = tmp_path / "data" / "processed" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "run_id": run_id,
        "started_at": NOW,
        "completed_at": NOW,
        "provider": "opencode-go",
        "model": "deepseek-v4.1-flash",
        "extractor_version": EXTRACTOR,
        "prompt_version": PROMPT,
        "normalization_version": NORMALIZATION,
        "limits": {"max_documents": 150, "max_calls": 300},
    }
    (run_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    _write_jsonl(run_dir / "documents.jsonl", documents)
    _write_jsonl(run_dir / "candidates.jsonl", [accepted, rejected])
    _write_jsonl(run_dir / "pain_points.jsonl", [accepted])
    _write_jsonl(run_dir / "rejects.jsonl", [_reject(accepted_doc)])
    _write_jsonl(run_dir / "calls.jsonl", [_call(accepted_doc)])
    fetch_log = tmp_path / "data" / "raw" / "fetches.jsonl"
    _write_jsonl(fetch_log, [fetch])
    return run_dir, fetch_log, documents, accepted


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
            "SELECT target, entity_type, entity_id, status, idempotency_key "
            "FROM projection_tasks"
        ).fetchone()
        assert projection["target"] == "obsidian"
        assert projection["entity_type"] == "run"
        assert projection["entity_id"] == "run-test"
        assert projection["status"] == "pending"
        assert projection["idempotency_key"] == "run_imported:run-test"
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

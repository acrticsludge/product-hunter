"""Helpers shared by the store tests and the crash-recovery suite.

The run-directory builders live here so the crash suite reuses the same
builder instead of copying it, and so a copied builder cannot drift.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import textwrap
from collections.abc import Sequence
from pathlib import Path

import pytest

from product_hunter.db import Store
from product_hunter.documents import Document, parse_documents
from product_hunter.extraction import ExtractedCandidate, ExtractionCall, ExtractionReject
from product_hunter.fetch import FetchRecord
from product_hunter.normalization import Domain, normalize_fields

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


def open_store(db_path: Path) -> Store:
    """Open a store on a file path. The migration runs on the first open."""
    return Store.open(db_path)


def crash_child(
    db_path: Path,
    body: str,
    *,
    timeout: int = 60,
    extra_args: Sequence[str] = (),
) -> subprocess.CompletedProcess:
    """Run `body` in a child process against `db_path`; the child kills itself.

    `body` is Python source. It receives the database path in sys.argv[1] and
    is expected to end in os._exit(9) so nothing is cleaned up.

    `body` is trusted, test-authored source and is run as it stands. Run-time
    values travel through sys.argv, never through the source string: a value
    spliced into the program is a program nobody wrote.

    `timeout` is the number of seconds the child may run before it is killed
    and the call fails. `extra_args` are passed to the child after the
    database path, so the child reads them as sys.argv[2] and later.
    """
    program = "import os, sys\n" + textwrap.dedent(body)
    try:
        return subprocess.run(
            [sys.executable, "-c", program, str(db_path), *extra_args],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        pytest.fail(
            f"the crash child did not exit within {timeout} seconds; it hung.\n"
            f"child program:\n{program}"
        )

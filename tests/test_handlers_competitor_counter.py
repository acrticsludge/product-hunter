from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pytest

from product_hunter.db import connect, migrate, enqueue_job
from product_hunter.db.jobs import JobContext
from product_hunter.handlers import (
    _competitor_handler,
    _counter_evidence_handler,
    _ensure_fetch,
    _ensure_source,
)
from product_hunter.research.investigation.coverage import (
    CoverageRequirements,
    SearchAttempt,
    assess_coverage,
)
from product_hunter.research.investigation.counter_evidence import (
    CounterEvidenceItem,
    CounterEvidenceKind,
    CounterEvidenceStatus,
    assess_counter_evidence,
)


def _setup(conn: sqlite3.Connection, run_id: str = "run_test") -> tuple[str, object]:
    migrate(conn)
    conn.execute(
        "INSERT INTO research_runs (run_id, public_id, objective, original_request) "
        "VALUES (?, ?, ?, ?)",
        (run_id, run_id, "test", "test"),
    )
    conn.commit()
    _ensure_source(conn, "hn_algolia")
    fetch_id = _ensure_fetch(conn, "hn_algolia", "https://hn.algolia.com/api/v1/search")
    conn.execute(
        "INSERT INTO documents (doc_id, fetch_id, source_id, url, title, text_sha256, "
        "text_path, raw_path, raw_sha256, parser_version, content_type, created_at, "
        "live_status, first_seen_at, last_seen_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "d1",
            fetch_id,
            "hn_algolia",
            "https://example.com",
            "teams hate spreadsheets",
            "h",
            "p",
            "r",
            "h",
            "v1",
            "text/html",
            "2026-01-01T00:00:00Z",
            "live",
            "2026-01-01T00:00:00Z",
            "2026-01-01T00:00:00Z",
        ),
    )
    conn.commit()
    job = enqueue_job(conn, stage="COMPETITOR_RESEARCH", idempotency_key=f"comp:{run_id}", run_id=run_id)
    return run_id, job


def test_competitor_found(monkeypatch):
    conn = connect(":memory:")
    run_id, job = _setup(conn)
    monkeypatch.setattr(
        "product_hunter.handlers._live_search",
        lambda q: ([("hn_algolia", {"title": "FooTool", "url": "https://foo.com"})], None, ["foo.com"]),
    )
    ctx = JobContext(conn, job, worker_id="w", lease_seconds=60)
    result = _competitor_handler(ctx)
    assert result["competitors"] == 1
    assert result["queries"] == 4
    assert conn.execute("SELECT COUNT(*) FROM tool_calls WHERE stage='investigation'").fetchone()[0] == 4


def test_multiple_competitors(monkeypatch):
    conn = connect(":memory:")
    run_id, job = _setup(conn)
    monkeypatch.setattr(
        "product_hunter.handlers._live_search",
        lambda q: (
            [
                ("hn_algolia", {"title": "FooTool", "url": "https://foo.com"}),
                ("github_issues", {"title": "BarApp", "url": "https://bar.com"}),
            ],
            None,
            ["foo.com", "bar.com"],
        ),
    )
    ctx = JobContext(conn, job, worker_id="w", lease_seconds=60)
    result = _competitor_handler(ctx)
    assert result["competitors"] == 2


def test_no_competitor(monkeypatch):
    conn = connect(":memory:")
    run_id, job = _setup(conn)
    monkeypatch.setattr(
        "product_hunter.handlers._live_search",
        lambda q: ([], None, ["foo.com", "bar.com"]),
    )
    ctx = JobContext(conn, job, worker_id="w", lease_seconds=60)
    result = _competitor_handler(ctx)
    assert result["competitors"] == 0


def test_competitor_search_failure(monkeypatch):
    conn = connect(":memory:")
    run_id, job = _setup(conn)
    monkeypatch.setattr(
        "product_hunter.handlers._live_search",
        lambda q: ([], "boom", []),
    )
    ctx = JobContext(conn, job, worker_id="w", lease_seconds=60)
    result = _competitor_handler(ctx)
    assert result["queries"] == 4
    assert conn.execute(
        "SELECT COUNT(*) FROM tool_calls WHERE stage='investigation' AND status='failure'"
    ).fetchone()[0] == 4


def test_counter_evidence_found(monkeypatch):
    conn = connect(":memory:")
    run_id, job = _setup(conn)
    # counter handler expects COMPETITOR_RESEARCH job stage but uses same setup; re-enqueue counter stage
    job2 = enqueue_job(conn, stage="COUNTER_EVIDENCE", idempotency_key=f"ce:{run_id}", run_id=run_id)
    monkeypatch.setattr(
        "product_hunter.handlers._live_search",
        lambda q: ([("hn_algolia", {"title": "Not worth it", "url": "https://x.com"})], None, ["x.com"]),
    )
    ctx = JobContext(conn, job2, worker_id="w", lease_seconds=60)
    result = _counter_evidence_handler(ctx)
    assert result["counter_evidence"] == 3
    assert result["status"] == "found"


def test_counter_evidence_timeout(monkeypatch):
    conn = connect(":memory:")
    run_id, job = _setup(conn)
    job2 = enqueue_job(conn, stage="COUNTER_EVIDENCE", idempotency_key=f"ce2:{run_id}", run_id=run_id)
    monkeypatch.setattr(
        "product_hunter.handlers._live_search",
        lambda q: ([], "timeout", []),
    )
    ctx = JobContext(conn, job2, worker_id="w", lease_seconds=60)
    result = _counter_evidence_handler(ctx)
    assert result["counter_evidence"] == 0
    assert result["status"] == "incomplete"


def test_genuine_nontrivial_zero():
    attempts = [
        SearchAttempt(family="counter_not_worth", query="q1", status=__import__("product_hunter.research.evidence", fromlist=["ToolCallStatus"]).ToolCallStatus.success, document_count=2, domains=["a.com", "b.com"], tool_call_id="t1"),
        SearchAttempt(family="counter_complaints", query="q2", status=__import__("product_hunter.research.evidence", fromlist=["ToolCallStatus"]).ToolCallStatus.success, document_count=2, domains=["a.com", "b.com"], tool_call_id="t2"),
        SearchAttempt(family="counter_workaround", query="q3", status=__import__("product_hunter.research.evidence", fromlist=["ToolCallStatus"]).ToolCallStatus.success, document_count=2, domains=["a.com", "b.com"], tool_call_id="t3"),
    ]
    req = CoverageRequirements(min_queries=3, min_documents=1, min_domains=2)
    cov = assess_coverage(["counter_not_worth", "counter_complaints", "counter_workaround"], attempts, req)
    res = assess_counter_evidence([], attempts, ["counter_not_worth", "counter_complaints", "counter_workaround"], req)
    assert cov.status.value == "complete"
    assert res.status is CounterEvidenceStatus.none_found


def test_trivial_zero():
    attempts = [
        SearchAttempt(family="counter_not_worth", query="q1", status=__import__("product_hunter.research.evidence", fromlist=["ToolCallStatus"]).ToolCallStatus.success, document_count=0, domains=[], tool_call_id="t1"),
    ]
    req = CoverageRequirements(min_queries=3, min_documents=1, min_domains=2)
    cov = assess_coverage(["counter_not_worth", "counter_complaints", "counter_workaround"], attempts, req)
    assert cov.status.value == "incomplete"
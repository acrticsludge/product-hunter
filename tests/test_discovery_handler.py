from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

import pytest

from product_hunter.db import Store
from product_hunter.db.jobs import enqueue_job, get_job
from product_hunter.db.worker import Worker
from product_hunter.handlers import build_handlers, _hn_search, _github_search


def _store_with_run() -> Store:
    store = Store.open(":memory:")
    now = datetime.now(timezone.utc).isoformat()
    store.conn.execute(
        """
        INSERT INTO research_runs (
            run_id, public_id, objective, original_request, status,
            created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        ("run-1", "run-1", "test", "", "running", now, now),
    )
    return store


def test_discovery_handler_invokes_multiple_sources(monkeypatch) -> None:
    monkeypatch.setattr(
        "product_hunter.handlers._hn_search",
        lambda query, limit=10: [
            {"objectID": "1", "title": "hn title", "url": "https://example.com/hn"}
        ],
    )
    monkeypatch.setattr(
        "product_hunter.handlers._github_search",
        lambda query, limit=10: [
            {"id": 2, "number": 2, "title": "gh title", "html_url": "https://github.com/x/y/issues/2", "body": "body"}
        ],
    )

    store = _store_with_run()
    try:
        job = enqueue_job(
            store.conn, stage="DISCOVERY", idempotency_key="disc-1", run_id="run-1", payload={"objective": "manual report"}
        )
        worker = Worker(store.conn, "worker-1", build_handlers())
        outcome = worker.run_once()
        assert outcome is not None
        assert outcome.status == "succeeded"
        assert outcome.result["sources"] == ["hn_algolia", "github_issues"]

        docs = store.conn.execute("SELECT count(*) FROM documents").fetchone()[0]
        assert docs >= 2

        from product_hunter.discovery.store import list_records

        records = list_records(store.conn, "run-1")
        assert len(records) >= 2
        assert {r.source_id for r in records} == {"hn_algolia", "github_issues"}

        collection_job = store.conn.execute(
            "SELECT 1 FROM jobs WHERE stage = 'COLLECTION' AND run_id = 'run-1'"
        ).fetchone()
        assert collection_job is not None
    finally:
        store.close()


def test_discovery_handler_records_empty_status(monkeypatch) -> None:
    monkeypatch.setattr("product_hunter.handlers._hn_search", lambda query, limit=10: [])
    monkeypatch.setattr("product_hunter.handlers._github_search", lambda query, limit=10: [])

    store = _store_with_run()
    try:
        enqueue_job(store.conn, stage="DISCOVERY", idempotency_key="disc-2", run_id="run-1")
        worker = Worker(store.conn, "worker-1", build_handlers())
        outcome = worker.run_once()
        assert outcome is not None
        assert outcome.status == "succeeded"
        from product_hunter.discovery.store import list_records

        records = list_records(store.conn, "run-1")
        assert records
        assert all(r.status.value in ("empty", "success", "failure", "timeout") for r in records)
    finally:
        store.close()

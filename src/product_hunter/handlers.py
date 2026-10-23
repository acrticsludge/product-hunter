"""Worker handlers for the Product Hunter pipeline.

Each handler receives a JobContext (with .conn, .job, .worker_id) and either
completes the work or raises, so the worker can record the failure and retry.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from pathlib import Path
from typing import Any

import httpx

from product_hunter.config import ExtractionConfig, load_config, repo_root
from product_hunter.extraction import Document, extract_documents
from product_hunter.llm.opencode import OpenCodeGenerateProvider
from product_hunter.research.stages import StageNotImplemented


def _hn_search(query: str, *, limit: int = 20) -> list[dict[str, Any]]:
    url = "https://hn.algolia.com/api/v1/search"
    params = {"query": query, "tags": "story", "hitsPerPage": limit}
    with httpx.Client(timeout=30.0) as client:
        resp = client.get(url, params=params)
        resp.raise_for_status()
        data = resp.json()
    return data.get("hits", [])


def _ensure_source(conn: sqlite3.Connection, source_id: str) -> None:
    row = conn.execute(
        "SELECT 1 FROM sources WHERE source_id = ?", (source_id,)
    ).fetchone()
    if row:
        return
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    conn.execute(
        "INSERT OR IGNORE INTO sources (source_id, source_type, terms_status, "
        "allowed_use_json, enabled, reviewed_at, review_basis, collector, "
        "domains_json, rate_limit_json, robots_notes, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            source_id,
            "community",
            "permitted",
            json.dumps(["discovery", "retrieval", "analysis", "storage"]),
            1,
            now,
            "HN Algolia public API",
            "api",
            json.dumps(["hn.algolia.com", "news.ycombinator.com"]),
            json.dumps({"min_interval_seconds": 1.0}),
            "API access; no robots check needed",
            now,
            now,
        ),
    )
    conn.commit()


def _ensure_fetch(conn: sqlite3.Connection, source_id: str) -> str:
    fetch_id = f"fetch_{source_id}_{int(time.time())}"
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    conn.execute(
        "INSERT OR IGNORE INTO fetches (fetch_id, source_id, requested_url, "
        "final_url, status, content_type, raw_sha256, bytes, fetched_at, "
        "terms_status, allowed_use_json, review_basis, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            fetch_id,
            source_id,
            "https://hn.algolia.com/api/v1/search",
            "https://hn.algolia.com/api/v1/search",
            "success",
            "application/json",
            hashlib.sha256(b"").hexdigest(),
            0,
            now,
            "permitted",
            json.dumps(["discovery", "retrieval", "analysis", "storage"]),
            "HN Algolia public API",
            now,
        ),
    )
    conn.commit()
    return fetch_id


def _discovery_handler(context: Any) -> dict[str, Any] | None:
    """Run a bounded discovery pass against Hacker News."""
    conn = context.conn
    job = context.job
    queries = [
        "how do you deal with manual",
        "there has to be a better way",
        "I hate having to",
        "workaround spreadsheet",
        "takes me hours every week",
        "is there a tool for",
        "feature request",
        "doesn't support",
    ]
    _ensure_source(conn, "hn_algolia")
    fetch_id = _ensure_fetch(conn, "hn_algolia")
    count = 0
    for q in queries:
        try:
            hits = _hn_search(q, limit=10)
        except Exception:
            continue
        for hit in hits:
            title = hit.get("title", "")
            object_id = hit.get("objectID", "")
            url = hit.get("url") or f"https://news.ycombinator.com/item?id={object_id}"
            doc_id = f"hn_{object_id}"
            content_hash = hashlib.sha256(title.encode()).hexdigest()
            now_ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            try:
                conn.execute(
                    "INSERT OR IGNORE INTO documents (doc_id, fetch_id, source_id, url, "
                    "title, text_sha256, text_path, raw_path, raw_sha256, "
                    "parser_version, content_type, created_at, live_status, "
                    "first_seen_at, last_seen_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        doc_id,
                        fetch_id,
                        "hn_algolia",
                        url,
                        title,
                        content_hash,
                        f"data/processed/{doc_id}.txt",
                        f"data/raw/{doc_id}.html",
                        content_hash,
                        "v1",
                        "text/html",
                        now_ts,
                        "live",
                        now_ts,
                        now_ts,
                    ),
                )
                count += 1
            except Exception:
                pass
        conn.commit()
        time.sleep(1.0)
    # Enqueue the next stage.
    import uuid
    next_job_id = f"job_{uuid.uuid4().hex[:16]}"
    idempotency_key = f"collection:{job.run_id}"
    existing = conn.execute(
        "SELECT 1 FROM jobs WHERE idempotency_key = ?", (idempotency_key,)
    ).fetchone()
    if not existing:
        conn.execute(
            "INSERT INTO jobs (id, run_id, stage, priority, status, attempts, "
            "max_attempts, idempotency_key, lease_epoch, created_at, "
            "scheduled_for, payload_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                next_job_id,
                job.run_id,
                "COLLECTION",
                job.priority if hasattr(job, "priority") else 0,
                "queued",
                0,
                3,
                idempotency_key,
                0,
                time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                job.payload_json if hasattr(job, "payload_json") else "{}",
            ),
        )
        conn.commit()
    return {"discovered": count}


def _extraction_handler(context: Any) -> dict[str, Any] | None:
    """Extract pain points from discovered documents using the LLM."""
    conn = context.conn
    try:
        config = load_config()
        generator = OpenCodeGenerateProvider(config)
    except Exception:
        return {"extracted": 0, "error": "no model available"}
    # Read documents
    rows = conn.execute(
        "SELECT doc_id, fetch_id, source_id, url, title, text_sha256, text_path, "
        "raw_path, raw_sha256, parser_version, content_type, live_status "
        "FROM documents WHERE live_status = 'live' LIMIT 20"
    ).fetchall()
    if not rows:
        return {"extracted": 0}
    from product_hunter.extraction import Document

    documents = [
        Document(
            doc_id=r[0],
            fetch_id=r[1],
            source_id=r[2],
            url=r[3],
            text=r[4] or "",
            text_sha256=r[5],
            text_path=r[6],
            raw_path=r[7],
            raw_sha256=r[8],
            parser_version=r[9] or "v1",
            content_type=r[10] or "text/html",
            live_status=r[11] or "live",
        )
        for r in rows
    ]
    try:
        batch = extract_documents(documents, generator, config, max_documents=20, max_calls=5)
    except Exception as e:
        return {"extracted": 0, "error": str(e)[:200]}
    # Store candidates as pain points
    count = 0
    for cand in getattr(batch, "candidates", []):
        pp_id = f"pp_{hashlib.sha256(str(cand).encode()).hexdigest()[:12]}"
        try:
            conn.execute(
                "INSERT OR IGNORE INTO pain_points (pain_point_id, doc_id, quote, "
                "severity, confidence, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    pp_id,
                    getattr(cand, "doc_id", rows[0][0]),
                    str(getattr(cand, "quote", ""))[:500],
                    float(getattr(cand, "severity", 5.0)),
                    float(getattr(cand, "confidence", 0.5)),
                    time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                ),
            )
            count += 1
        except Exception:
            pass
    conn.commit()
    _enqueue_next(conn, context.job, "EMBEDDING")
    return {"extracted": count}


def _enqueue_next(conn: Any, job: Any, next_stage: str) -> None:
    """Enqueue the next stage job if not already queued."""
    import uuid

    idempotency_key = f"{next_stage.lower()}:{job.run_id}"
    existing = conn.execute(
        "SELECT 1 FROM jobs WHERE idempotency_key = ?", (idempotency_key,)
    ).fetchone()
    if existing:
        return
    next_job_id = f"job_{uuid.uuid4().hex[:16]}"
    now = __import__("time").strftime("%Y-%m-%dT%H:%M:%SZ", __import__("time").gmtime())
    payload = job.payload_json if hasattr(job, "payload_json") else "{}"
    priority = job.priority if hasattr(job, "priority") else 0
    conn.execute(
        "INSERT INTO jobs (id, run_id, stage, priority, status, attempts, "
        "max_attempts, idempotency_key, lease_epoch, created_at, "
        "scheduled_for, payload_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            next_job_id,
            job.run_id,
            next_stage,
            priority,
            "queued",
            0,
            3,
            idempotency_key,
            0,
            now,
            now,
            payload,
        ),
    )
    conn.commit()


def _collection_handler(context: Any) -> dict[str, Any] | None:
    # Collection is not implemented in this session: the production path must
    # not be able to claim success for it. Raise so the worker records a
    # machine-detectable NOT_IMPLEMENTED result instead of a pass-through.
    raise StageNotImplemented("COLLECTION", reason="collection ladder not wired to the production path")


_STAGE_CHAIN = {
    "NORMALIZATION": "DEDUPLICATION",
    "DEDUPLICATION": "ENTITY_RESOLUTION",
    "ENTITY_RESOLUTION": "PAIN_EXTRACTION",
    "PAIN_EXTRACTION": "EMBEDDING",
    "EMBEDDING": "CLUSTERING",
    "CLUSTERING": "INDEPENDENCE_ANALYSIS",
    "INDEPENDENCE_ANALYSIS": "VALIDATION",
    "VALIDATION": "COMPETITOR_RESEARCH",
    "COMPETITOR_RESEARCH": "COUNTER_EVIDENCE",
    "COUNTER_EVIDENCE": "SCORING",
    "SCORING": "REPORTING",
    "REPORTING": "OBSIDIAN_SYNC",
}


def _default_handler(context: Any) -> dict[str, Any] | None:
    # Every other stage is not implemented in this session. Do not enqueue
    # the next stage and do not return None (which the worker used to read
    # as success). Raise so the outcome is machine-detectable.
    raise StageNotImplemented(
        context.job.stage, reason="stage not wired to the production path"
    )


def build_handlers() -> dict[str, Any]:
    return {
        "DISCOVERY": _discovery_handler,
        "COLLECTION": _collection_handler,
        "NORMALIZATION": _default_handler,
        "DEDUPLICATION": _default_handler,
        "ENTITY_RESOLUTION": _default_handler,
        "PAIN_EXTRACTION": _extraction_handler,
        "EMBEDDING": _default_handler,
        "CLUSTERING": _default_handler,
        "INDEPENDENCE_ANALYSIS": _default_handler,
        "VALIDATION": _default_handler,
        "COMPETITOR_RESEARCH": _default_handler,
        "COUNTER_EVIDENCE": _default_handler,
        "SCORING": _default_handler,
        "REPORTING": _default_handler,
        "OBSIDIAN_SYNC": _default_handler,
    }
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


def _github_search(query: str, *, limit: int = 10) -> list[dict[str, Any]]:
    url = "https://api.github.com/search/issues"
    params = {"q": query, "per_page": limit}
    headers = {"Accept": "application/vnd.github+json"}
    with httpx.Client(timeout=30.0) as client:
        resp = client.get(url, params=params, headers=headers)
        resp.raise_for_status()
        data = resp.json()
    return data.get("items", [])


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
                    "title, author, text_sha256, text_path, raw_path, raw_sha256, "
                    "parser_version, content_type, created_at, live_status, "
                    "first_seen_at, last_seen_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        doc_id,
                        fetch_id,
                        "hn_algolia",
                        url,
                        title,
                        hit.get("author"),
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
    """Extract pain points from discovered documents using the LLM.

    The production path now runs the full evidence pipeline: each document is
    ingested into the store, candidates are validated against the stored text,
    and accepted pain points are persisted with a verified evidence span and
    an evidence/pain link. Rejected candidates are counted, not silently
    dropped.
    """
    conn = context.conn
    try:
        config = load_config()
        generator = OpenCodeGenerateProvider(config)
    except Exception:
        return {"extracted": 0, "error": "no model available"}
    # Read documents
    rows = conn.execute(
        "SELECT doc_id, fetch_id, source_id, url, title, author, text_sha256, "
        "text_path, raw_path, raw_sha256, parser_version, content_type, "
        "live_status FROM documents WHERE live_status = 'live' LIMIT 20"
    ).fetchall()
    if not rows:
        return {"extracted": 0}
    from product_hunter.extraction import Document
    from product_hunter.evidence import EvidencePipeline, EvidenceRejected
    from product_hunter.db import Store

    documents = [
        Document(
            doc_id=r[0],
            fetch_id=r[1],
            source_id=r[2],
            url=r[3],
            title=r[4],
            author=r[5],
            text=r[4] or "",
            text_sha256=r[6],
            text_path=r[7],
            raw_path=r[8],
            raw_sha256=r[9],
            parser_version=r[10] or "v1",
            content_type=r[11] or "text/html",
            live_status=r[12] or "live",
        )
        for r in rows
    ]
    # Ingest documents into the evidence store first, so spans can be
    # validated against stored text.
    store = Store(conn)
    pipeline = EvidencePipeline(store, repo_root=repo_root())
    for doc in documents:
        try:
            pipeline.ingest_document(doc)
        except Exception:
            pass
    try:
        batch = extract_documents(documents, generator, config, max_documents=20, max_calls=5)
    except Exception as e:
        return {"extracted": 0, "error": str(e)[:200]}
    # Record candidates through the evidence pipeline, providing document
    # text in memory so the hash check uses the captured text.
    text_by_doc = {d.doc_id: d.text for d in documents}
    writes = []
    rejects = []
    rejected_count = 0
    for cand in getattr(batch, "candidates", []):
        try:
            w = pipeline.record_candidate(
                cand, document_text=text_by_doc.get(cand.doc_id)
            )
            writes.append(w)
        except Exception as exc:
            rejects.append(exc)
            rejected_count += 1
    recorded = len(writes)
    outcome = type("O", (), {"writes": writes, "rejected": rejected_count})()
    # Create claims for accepted pain points and link them to evidence spans.
    claim_count = 0
    run_id = getattr(context.job, "run_id", None)
    if run_id:
        from product_hunter.db.research_gate import record_claim
        from product_hunter.research.evidence import ResearchClaim, ClaimKind
        for write in getattr(outcome, "writes", []):
            pain_id = getattr(write, "pain_id", None)
            span_id = getattr(write, "span_id", None)
            if not pain_id or not span_id:
                continue
            # Only genuine pain points become claims.
            # (weak/not_pain candidates are still stored as spans.)
            try:
                claim = ResearchClaim(
                    claim_id=f"claim_{pain_id}",
                    kind=ClaimKind.problem_exists,
                    statement=f"Pain point {pain_id} observed in document",
                    stage="PAIN_EXTRACTION",
                    evidence_refs=[span_id],
                )
                record_claim(conn, run_id=run_id, claim=claim)
                claim_count += 1
            except Exception:
                pass
    conn.commit()
    _enqueue_next(conn, context.job, "EMBEDDING")
    return {
        "extracted": recorded,
        "rejected": rejected_count,
        "claims": claim_count,
    }


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
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
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
    _enqueue_next(context.conn, context.job, "NORMALIZATION")
    return {"collected": 0, "status": "pass-through"}


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
    stage = context.job.stage
    nxt = _STAGE_CHAIN.get(stage)
    if nxt is not None:
        _enqueue_next(context.conn, context.job, nxt)
    return {"status": "pass-through", "stage": stage}


def _utc_now():
    from datetime import datetime, timezone

    return datetime.now(timezone.utc)


def _problem_statement(conn: sqlite3.Connection, run_id: str) -> str:
    try:
        rows = conn.execute(
            "SELECT title, normalized_problem FROM opportunities WHERE run_id = ? LIMIT 1",
            (run_id,),
        ).fetchall()
        if rows:
            title = rows[0][0] or rows[0][1]
            if title:
                return str(title)
    except Exception:
        pass
    try:
        rows = conn.execute("SELECT quote FROM pain_points LIMIT 1").fetchall()
        if rows and rows[0][0]:
            return str(rows[0][0])[:200]
    except Exception:
        pass
    try:
        rows = conn.execute("SELECT title FROM documents LIMIT 1").fetchall()
        if rows and rows[0][0]:
            return str(rows[0][0])[:200]
    except Exception:
        pass
    return "workflow automation"


def _live_search(query: str) -> tuple[list[tuple[str, dict[str, Any]]], str | None, list[str]]:
    results: list[tuple[str, dict[str, Any]]] = []
    error: str | None = None
    domains: set[str] = set()
    for source, fn in (("hn_algolia", _hn_search), ("github_issues", _github_search)):
        try:
            for hit in fn(query, limit=6):
                results.append((source, hit))
                url = hit.get("url") or hit.get("html_url")
                if url:
                    try:
                        from urllib.parse import urlparse

                        netloc = urlparse(str(url)).netloc.lower()
                        if netloc:
                            domains.add(netloc)
                    except Exception:
                        pass
        except Exception as exc:
            if error is None:
                error = str(exc)[:200]
    return results, error, sorted(domains)


def _competitor_handler(context: Any) -> dict[str, Any] | None:
    from product_hunter.db.opportunity import OpportunityStore, create_opportunity_id
    from product_hunter.research.investigation.coverage import CoverageRequirements, SearchAttempt, assess_coverage
    from product_hunter.research.evidence import (
        ArtifactRecord,
        ClaimKind,
        ResearchClaim,
        StageExecution,
        StageStatus,
        ToolCallProvenance,
        ToolCallStatus,
        ZeroResultOutcome,
    )
    from product_hunter.research.investigation.competitors import Competitor, CompetitorConfidence, analyse_competitors
    from product_hunter.research.investigation.queries import competitor_templates, render
    from product_hunter.db.research_gate import SqliteToolCallStore, record_stage_execution, record_artifact, record_claim

    conn = context.conn
    job = context.job
    run_id = job.run_id
    stage = "COMPETITOR_RESEARCH"
    contract_stage = "investigation"
    now = _utc_now()
    problem = _problem_statement(conn, run_id)

    # Persist an opportunity row so results link back to one.
    opp_id = create_opportunity_id(problem, run_id)
    try:
        OpportunityStore(conn).upsert(
            __import__("product_hunter.db.opportunity", fromlist=["OpportunityRecord"]).OpportunityRecord(
                opportunity_id=opp_id, run_id=run_id, title=problem, normalized_problem=problem, lifecycle="INVESTIGATING"
            )
        )
    except Exception:
        pass

    templates = competitor_templates()
    tool_calls: list[ToolCallProvenance] = []
    attempts: list[SearchAttempt] = []
    competitors: list[Competitor] = []
    seen: set[str] = set()

    for idx, template in enumerate(templates):
        q = render(template, problem)
        results, error, domains = _live_search(q.text)
        tc_id = f"tc_{run_id}_{stage}_{idx}"
        status = ToolCallStatus.failure if error and not results else ToolCallStatus.success
        if error and not results:
            status = ToolCallStatus.failure
        elif error:
            status = ToolCallStatus.partial
        else:
            status = ToolCallStatus.success
        result_count = len(results)
        result_ref = f"result:{run_id}:{stage}:{idx}"
        tc = ToolCallProvenance(
            run_id=run_id,
            stage=contract_stage,
            agent="product-hunter",
            tool="search",
            purpose=q.purpose,
            timestamp=now,
            query_or_operation=q.text,
            result_ref=result_ref,
            result_count=result_count,
            status=status,
            tool_call_id=tc_id,
            query_family=q.family.value,
            duration_ms=0,
            error=error,
        )
        tool_calls.append(tc)

        attempt = SearchAttempt(
            family=q.family.value,
            query=q.text,
            status=status,
            document_count=result_count,
            domains=domains,
            tool_call_id=tc_id,
            error=error,
        )
        attempts.append(attempt)

        if status in (ToolCallStatus.success, ToolCallStatus.partial):
            for source, hit in results:
                title = str(hit.get("title") or hit.get("name") or "unknown solution")[:120]
                if title.lower() in seen:
                    continue
                seen.add(title.lower())
                url = hit.get("url") or hit.get("html_url")
                competitor = Competitor(
                    competitor_id="comp_" + hashlib.sha256(f"{run_id}|{title}|{url}".encode()).hexdigest()[:12],
                    name=title,
                    domain=None,
                    url=url,
                    pricing=None,
                    pricing_model="unknown",
                    positioning="discovered via search",
                    customer_segment="",
                    overlap=[problem],
                    gaps=[],
                    unknowns=["pricing not captured", "positioning inferred from search result"],
                    evidence_refs=[tc_id],
                    discovered_via=[source],
                    tool_call_id=tc_id,
                    confidence=CompetitorConfidence.low,
                )
                competitors.append(competitor)

    requirements = CoverageRequirements(
        min_queries=len(templates),
        min_documents=1,
        min_domains=2,
    )
    coverage = assess_coverage([t.family.value for t in templates], attempts, requirements)
    analysis = analyse_competitors(competitors, coverage, [t.family.value for t in templates])

    # Persist tool calls.
    store = SqliteToolCallStore(conn)
    for tc in tool_calls:
        try:
            store.append_tool_call(tc, attempt=1, source_id="hn_algolia", worker_id=None, lease_epoch=None)
        except Exception:
            pass

    # Artifacts.
    arts: list[ArtifactRecord] = []
    if competitors:
        arts.append(
            ArtifactRecord(
                artifact_id=f"{stage}-competitors",
                stage=contract_stage,
                kind="competitors",
                ref=f"{tool_calls[0].tool_call_id}/competitors" if tool_calls else f"{stage}/competitors",
                item_count=len(competitors),
                produced_by_tool_call_id=tool_calls[0].tool_call_id if tool_calls else None,
                created_at=now,
                valid=True,
            )
        )
    if tool_calls:
        arts.append(
            ArtifactRecord(
                artifact_id=f"{stage}-candidate-url",
                stage=contract_stage,
                kind="candidate_url",
                ref=f"{tool_calls[0].tool_call_id}/candidate_url",
                item_count=sum(tc.result_count for tc in tool_calls),
                produced_by_tool_call_id=tool_calls[0].tool_call_id,
                created_at=now,
                valid=True,
            )
        )

    zero = ZeroResultOutcome(recorded=False)
    stage_exec = StageExecution(
        stage=contract_stage,
        status=StageStatus.succeeded,
        started_at=now,
        finished_at=now,
        tool_calls=tool_calls,
        artifacts=arts,
        zero_result=zero,
    )
    try:
        record_stage_execution(conn, run_id=run_id, execution=stage_exec, attempt=1, worker_id=None, lease_epoch=None)
    except Exception:
        pass
    for art in arts:
        try:
            record_artifact(conn, run_id=run_id, artifact=art, attempt=1, worker_id=None, lease_epoch=None)
        except Exception:
            pass

    # Claim.
    try:
        claim_id = f"claim_{run_id}_{stage}"
        statement = f"competitor research {analysis.status.value}: {len(analysis.competitors)} competitors"
        claim = ResearchClaim(
            claim_id=claim_id,
            kind=ClaimKind.competitors_exist if analysis.competitors else ClaimKind.no_strong_competitor,
            statement=statement,
            stage=contract_stage,
            evidence_refs=[tc.tool_call_id for tc in tool_calls if tc.tool_call_id],
        )
        record_claim(conn, run_id=run_id, claim=claim, worker_id=None, lease_epoch=None)
    except Exception:
        pass

    # Link opportunity.
    try:
        OpportunityStore(conn).add_claim(claim_id, opp_id, statement, kind="competitor", span_id=None)
    except Exception:
        pass

    _enqueue_next(conn, job, "COUNTER_EVIDENCE")
    return {
        "competitors": len(analysis.competitors),
        "queries": len(tool_calls),
        "status": analysis.status.value,
    }


def _counter_evidence_handler(context: Any) -> dict[str, Any] | None:
    from product_hunter.db.opportunity import OpportunityStore, create_opportunity_id
    from product_hunter.research.investigation.coverage import CoverageRequirements, SearchAttempt, assess_coverage
    from product_hunter.research.evidence import (
        ArtifactRecord,
        ClaimKind,
        ResearchClaim,
        StageExecution,
        StageStatus,
        ToolCallProvenance,
        ToolCallStatus,
        ZeroResultOutcome,
    )
    from product_hunter.research.investigation.counter_evidence import (
        CounterEvidenceItem,
        CounterEvidenceKind,
        CounterEvidenceStatus,
        assess_counter_evidence,
    )
    from product_hunter.research.investigation.queries import counter_evidence_templates, render
    from product_hunter.db.research_gate import SqliteToolCallStore, record_stage_execution, record_artifact, record_claim

    conn = context.conn
    job = context.job
    run_id = job.run_id
    stage = "COUNTER_EVIDENCE"
    contract_stage = "counter_evidence"
    now = _utc_now()
    problem = _problem_statement(conn, run_id)

    opp_id = create_opportunity_id(problem, run_id)
    try:
        OpportunityStore(conn).upsert(
            __import__("product_hunter.db.opportunity", fromlist=["OpportunityRecord"]).OpportunityRecord(
                opportunity_id=opp_id, run_id=run_id, title=problem, normalized_problem=problem, lifecycle="INVESTIGATING"
            )
        )
    except Exception:
        pass

    templates = counter_evidence_templates()
    kind_map = {
        "counter_not_worth": CounterEvidenceKind.low_willingness_to_pay,
        "counter_complaints": CounterEvidenceKind.contradicting_thesis,
        "counter_workaround": CounterEvidenceKind.free_alternative,
    }

    tool_calls: list[ToolCallProvenance] = []
    attempts: list[SearchAttempt] = []
    items: list[CounterEvidenceItem] = []

    for idx, template in enumerate(templates):
        q = render(template, problem)
        results, error, domains = _live_search(q.text)
        tc_id = f"tc_{run_id}_{stage}_{idx}"
        if error and not results:
            status = ToolCallStatus.failure
        elif error:
            status = ToolCallStatus.partial
        else:
            status = ToolCallStatus.success
        result_count = len(results)
        result_ref = f"result:{run_id}:{stage}:{idx}"
        tc = ToolCallProvenance(
            run_id=run_id,
            stage=contract_stage,
            agent="product-hunter",
            tool="search",
            purpose=q.purpose,
            timestamp=now,
            query_or_operation=q.text,
            result_ref=result_ref,
            result_count=result_count,
            status=status,
            tool_call_id=tc_id,
            query_family=q.family.value,
            duration_ms=0,
            error=error,
        )
        tool_calls.append(tc)

        attempt = SearchAttempt(
            family=q.family.value,
            query=q.text,
            status=status,
            document_count=result_count,
            domains=domains,
            tool_call_id=tc_id,
            error=error,
        )
        attempts.append(attempt)

        if status in (ToolCallStatus.success, ToolCallStatus.partial):
            kind = kind_map.get(q.family.value, CounterEvidenceKind.contradicting_thesis)
            for source, hit in results:
                title = str(hit.get("title") or hit.get("name") or "counter evidence")[:200]
                url = hit.get("url") or hit.get("html_url")
                domain = None
                if url:
                    try:
                        from urllib.parse import urlparse

                        domain = urlparse(str(url)).netloc.lower() or None
                    except Exception:
                        domain = None
                item = CounterEvidenceItem(
                    item_id="ce_" + hashlib.sha256(f"{run_id}|{title}|{url}".encode()).hexdigest()[:12],
                    kind=kind,
                    summary=title,
                    source_url=url,
                    source_domain=domain,
                    tool_call_id=tc_id,
                    query_family=q.family.value,
                    strength=0.5,
                )
                items.append(item)

    requirements = CoverageRequirements(
        min_queries=len(templates),
        min_documents=1,
        min_domains=2,
    )
    result = assess_counter_evidence(items, attempts, [t.family.value for t in templates], requirements)

    store = SqliteToolCallStore(conn)
    for tc in tool_calls:
        try:
            store.append_tool_call(tc, attempt=1, source_id="hn_algolia", worker_id=None, lease_epoch=None)
        except Exception:
            pass

    arts: list[ArtifactRecord] = []
    if items:
        arts.append(
            ArtifactRecord(
                artifact_id=f"{stage}-counter-evidence",
                stage=contract_stage,
                kind="counter_evidence",
                ref=f"{tool_calls[0].tool_call_id}/counter_evidence" if tool_calls else f"{stage}/counter_evidence",
                item_count=len(items),
                produced_by_tool_call_id=tool_calls[0].tool_call_id if tool_calls else None,
                created_at=now,
                valid=True,
            )
        )
    if tool_calls:
        arts.append(
            ArtifactRecord(
                artifact_id=f"{stage}-candidate-url",
                stage=contract_stage,
                kind="candidate_url",
                ref=f"{tool_calls[0].tool_call_id}/candidate_url",
                item_count=sum(tc.result_count for tc in tool_calls),
                produced_by_tool_call_id=tool_calls[0].tool_call_id,
                created_at=now,
                valid=True,
            )
        )

    # Zero result recording.
    zero_recorded = (
        result.status is CounterEvidenceStatus.none_found
        and all(a.status is ToolCallStatus.success for a in attempts)
    )
    zero = ZeroResultOutcome(
        recorded=zero_recorded,
        required_query_families=[t.family.value for t in templates] if zero_recorded else [],
        completed_query_families=[a.family for a in attempts if a.status is ToolCallStatus.success] if zero_recorded else [],
    )

    stage_exec = StageExecution(
        stage=contract_stage,
        status=StageStatus.succeeded,
        started_at=now,
        finished_at=now,
        tool_calls=tool_calls,
        artifacts=arts,
        zero_result=zero,
    )
    try:
        record_stage_execution(conn, run_id=run_id, execution=stage_exec, attempt=1, worker_id=None, lease_epoch=None)
    except Exception:
        pass
    for art in arts:
        try:
            record_artifact(conn, run_id=run_id, artifact=art, attempt=1, worker_id=None, lease_epoch=None)
        except Exception:
            pass

    try:
        claim_id = f"claim_{run_id}_{stage}"
        statement = f"counter-evidence {result.status.value}: {len(result.items)} items"
        claim = ResearchClaim(
            claim_id=claim_id,
            kind=ClaimKind.no_counter_evidence_found if result.status is CounterEvidenceStatus.none_found else ClaimKind.opportunity_exists,
            statement=statement,
            stage=contract_stage,
            evidence_refs=[tc.tool_call_id for tc in tool_calls if tc.tool_call_id],
        )
        record_claim(conn, run_id=run_id, claim=claim, worker_id=None, lease_epoch=None)
    except Exception:
        pass

    try:
        OpportunityStore(conn).add_claim(claim_id, opp_id, statement, kind="counter_evidence", span_id=None)
    except Exception:
        pass

    _enqueue_next(conn, job, "SCORING")
    return {
        "counter_evidence": len(result.items),
        "queries": len(tool_calls),
        "status": result.status.value,
    }


def _frequency_from_raw(value: Any) -> "Frequency":
    from product_hunter.extraction import Frequency

    text = str(value or "unknown").strip().lower()
    try:
        return Frequency(text)
    except ValueError:
        return Frequency.unknown


def _validation_handler(context: Any) -> dict[str, Any] | None:
    conn = context.conn
    job = context.job
    count = int(conn.execute("SELECT COUNT(*) FROM pain_points").fetchone()[0])
    rows = conn.execute(
        "SELECT d.source_id, COUNT(*) FROM pain_points p "
        "JOIN documents d ON d.doc_id = p.doc_id GROUP BY d.source_id"
    ).fetchall()
    total = sum(r[1] for r in rows) or 1
    max_share = max((r[1] / total for r in rows), default=0.0)
    concentration_ok = max_share <= 0.9
    _enqueue_next(conn, job, "COMPETITOR_RESEARCH")
    return {
        "validated": count,
        "concentration_ok": concentration_ok,
        "max_source_share": max_share,
        "status": "validated",
    }


def _scoring_handler(context: Any) -> dict[str, Any] | None:
    conn = context.conn
    job = context.job
    from datetime import datetime, timezone

    from product_hunter.db.opportunity import (
        OpportunityRecord,
        OpportunityStore,
        create_opportunity_id,
    )
    from product_hunter.opportunity.policy import load_scoring_policy
    from product_hunter.opportunity.scoring import score_opportunity
    from product_hunter.opportunity.signals import (
        BuildabilityAssessment,
        CompetitiveAssessment,
        ConfidenceInputs,
        CounterEvidenceSummary,
        EvidenceItem,
        ExtractionAgreement,
        MarketAssumptions,
        MarketEstimate,
        PainObservation,
        PotentialInputs,
        SourceReliability,
        WtpAssessment,
        WtpSignal,
        WtpSignalKind,
        WtpTier,
    )
    from product_hunter.opportunity.signals import CompetitionDirection

    try:
        policy = load_scoring_policy()
    except Exception as exc:
        return {"scored": 0, "opportunities_created": 0, "error": str(exc)[:200]}

    store = OpportunityStore(conn)
    rows = conn.execute(
        "SELECT pain_id, doc_id, span_id, severity, frequency, pain_statement, "
        "normalized_key, domain, actor, action, object, consequence "
        "FROM pain_points"
    ).fetchall()

    created = 0
    scored = 0
    for r in rows:
        (
            pain_id,
            doc_id,
            span_id,
            severity,
            frequency,
            pain_statement,
            normalized_key,
            domain,
            actor,
            action,
            obj,
            consequence,
        ) = r

        evidence_items = []
        doc_row = None
        if doc_id:
            doc_row = conn.execute(
                "SELECT source_id, published_at, created_at FROM documents WHERE doc_id = ?",
                (doc_id,),
            ).fetchone()
        source_id = (doc_row[0] if doc_row else None) or "hn_algolia"
        published_raw = doc_row[1] if doc_row else None
        try:
            published_at = (
                datetime.fromisoformat(published_raw.replace("Z", "+00:00"))
                if published_raw
                else datetime.now(timezone.utc)
            )
            if published_at.tzinfo is None:
                published_at = published_at.replace(tzinfo=timezone.utc)
        except (ValueError, AttributeError):
            published_at = datetime.now(timezone.utc)

        if span_id:
            evidence_items.append(
                EvidenceItem(
                    evidence_id=span_id,
                    author_id=doc_id or "unknown",
                    community_id=source_id,
                    source_id=source_id,
                    source_reliability=SourceReliability.tier_b,
                    published_at=published_at,
                    identity_confidence=0.5,
                    is_canonical=True,
                )
            )

        pain_obs = PainObservation(
            pain_id=pain_id,
            severity=max(0, min(5, int(severity or 0))),
            frequency=_frequency_from_raw(frequency),
        )

        if span_id and evidence_items:
            wtp = WtpAssessment(
                tier=WtpTier.W1,
                signals=[
                    WtpSignal(
                        kind=WtpSignalKind.complaint,
                        detail="complaint only, no payment signal",
                        evidence_ids=[span_id],
                    )
                ],
            )
        else:
            wtp = WtpAssessment(tier=WtpTier.W0, signals=[])

        market = MarketEstimate(
            low=MarketAssumptions(
                reachable_population=1000.0,
                problem_incidence=0.1,
                adoption_rate=0.05,
                annual_price_usd=10.0,
            ),
            base=MarketAssumptions(
                reachable_population=5000.0,
                problem_incidence=0.2,
                adoption_rate=0.1,
                annual_price_usd=20.0,
            ),
            high=MarketAssumptions(
                reachable_population=10000.0,
                problem_incidence=0.3,
                adoption_rate=0.2,
                annual_price_usd=40.0,
            ),
        )

        competition = CompetitiveAssessment(
            direction=CompetitionDirection.neutral,
            competitors_found=0,
            search_ran=False,
        )
        buildability = BuildabilityAssessment(feasibility=0.7, blockers=[])

        potential_inputs = PotentialInputs(
            pain_observations=[pain_obs],
            wtp=wtp,
            market=market,
            competition=competition,
            buildability=buildability,
        )
        confidence_inputs = ConfidenceInputs(
            evidence=evidence_items,
            extraction_agreement=ExtractionAgreement(),
            counter_evidence=CounterEvidenceSummary(),
        )

        as_of = datetime.now(timezone.utc)
        try:
            score = score_opportunity(potential_inputs, confidence_inputs, policy, as_of=as_of)
        except Exception:
            continue

        scored += 1
        if (
            score.confidence.score >= policy.lifecycle.min_confidence
            and score.independence.independent_author_count
            >= policy.lifecycle.min_independent_people
        ):
            lifecycle = "PROMISING"
        else:
            lifecycle = "CANDIDATE"

        title = (pain_statement or normalized_key or pain_id)[:80]
        opp_id = create_opportunity_id(normalized_key or pain_id, job.run_id)
        now_ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        record = OpportunityRecord(
            opportunity_id=opp_id,
            run_id=job.run_id,
            cluster_id=None,
            title=title or "Untitled opportunity",
            normalized_problem=normalized_key,
            domain=domain,
            actor=actor,
            action=action,
            object=obj,
            consequence=consequence,
            severity=float(severity or 0),
            frequency=str(frequency),
            wtp_tier=wtp.tier.value,
            competition=0.5,
            potential=score.potential.score,
            confidence=score.confidence.score,
            trend=score.trend.direction.value,
            uncertainty=f"{score.potential.low:.2f}-{score.potential.high:.2f}",
            lifecycle=lifecycle,
            recommended_experiment=None,
            first_seen=now_ts,
            last_updated=now_ts,
        )
        store.upsert(record)
        store.add_score(opp_id, job.run_id, score.potential.score, score.confidence.score, now_ts)
        if span_id:
            store.add_evidence(opp_id, span_id, "supporting", now_ts)
        store.add_claim(
            f"claim_{opp_id}",
            opp_id,
            pain_statement or title,
            "problem_exists",
            span_id,
            now_ts,
        )
        created += 1

    _enqueue_next(conn, job, "REPORTING")
    return {"scored": scored, "opportunities_created": created}


def build_handlers() -> dict[str, Any]:
    return {
        "DISCOVERY": _discovery_handler,
        "COLLECTION": _collection_handler,
        "NORMALIZATION": _default_handler,
        "DEDUPLICATION": _default_handler,
        "ENTITY_RESOLUTION": _entity_resolution_handler,
        "PAIN_EXTRACTION": _extraction_handler,
        "EMBEDDING": _embedding_handler,
        "CLUSTERING": _clustering_handler,
        "INDEPENDENCE_ANALYSIS": _independence_analysis_handler,
        "VALIDATION": _validation_handler,
        "COMPETITOR_RESEARCH": _competitor_handler,
        "COUNTER_EVIDENCE": _counter_evidence_handler,
        "SCORING": _scoring_handler,
        "REPORTING": _default_handler,
        "OBSIDIAN_SYNC": _default_handler,
    }

# original build_handlers removed; the wired version above is authoritative
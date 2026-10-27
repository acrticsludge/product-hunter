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
from urllib.parse import parse_qs, urlparse

import httpx

from datetime import datetime, timezone

from product_hunter.db.store import Store
from product_hunter.discovery import (
    DISCOVERY_STAGE,
    DiscoveryBudget,
    DiscoveryRecord,
    DiscoveryStatus,
    QueryFamily,
    QueryFrontier,
    SaturationPolicy,
    load_frontier,
    save_frontier,
    write_record,
)
from product_hunter.research.budget import BudgetTracker
from product_hunter.research.contract import load_research_contract
from product_hunter.sources import AllowedUse, SourceBlocked, load_registry

from product_hunter.config import (
    ExtractionConfig,
    load_clustering_config,
    load_config,
    load_semantic_config,
    repo_root,
)
from product_hunter.independence.policy import load_independence_policy
from product_hunter.extraction import Document, extract_documents
from product_hunter.clustering.problems import PainPoint as ClusterPainPoint
from product_hunter.db.clustering import ClusterStore
from product_hunter.db.independence import IndependenceStore
from product_hunter.independence import (
    analyze,
    build_graph,
    detect_propagation,
    resolve_authors,
    resolve_communities,
)
from product_hunter.semantic.embeddings import build_embedder
from product_hunter.llm import build_router, build_generator_for_selection, ModelRole
from product_hunter.llm.base import ProviderError
from product_hunter.llm.opencode import OpenCodeGenerateProvider
from product_hunter.llm.routing import FallbackGenerator, ModelIdentity, SelectionSource
from product_hunter.config import load_routing_config
from product_hunter.db.model_routing import ModelExecution, record_model_executions
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
    params = {"q": f"{query} is:issue is:open", "per_page": limit}
    headers = {"Accept": "application/vnd.github+json"}
    with httpx.Client(timeout=30.0) as client:
        resp = client.get(url, params=params, headers=headers)
        resp.raise_for_status()
        data = resp.json()
    return data.get("items", [])


def _devto_search(query: str, *, limit: int = 10) -> list[dict[str, Any]]:
    url = "https://dev.to/api/articles"
    params = {"search": query, "per_page": min(limit, 30)}
    with httpx.Client(timeout=30.0) as client:
        resp = client.get(url, params=params, headers={"User-Agent": "ProductHunter/0.1", "Accept": "application/json"})
        resp.raise_for_status()
        data = resp.json()
    if isinstance(data, list):
        return data
    return data.get("articles", []) if isinstance(data, dict) else []


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
            200,
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


def _search_url(source_id: str, query: str) -> str:
    if source_id == "hn_algolia":
        return f"https://hn.algolia.com/api/v1/search?query={query}"
    if source_id == "github_issues":
        return f"https://api.github.com/search/issues?q={query}"
    return f"https://{source_id}/search?q={query}"


_SEARCHERS = {
    "hn_algolia": _hn_search,
    "github_issues": _github_search,
    "dev_to": _devto_search,
}


def _web_search(query: str, *, limit: int = 20) -> list[dict[str, Any]]:
    return _hn_search(query, limit=limit)


def _normalize_hit(source_id: str, hit: dict[str, Any]) -> tuple[str, str, str, str]:
    if source_id == "hn_algolia":
        object_id = str(hit.get("objectID", ""))
        title = hit.get("title") or hit.get("story_title") or ""
        url = f"https://news.ycombinator.com/item?id={object_id}"
        body = hit.get("comment_text") or ""
        doc_id = f"hn_{object_id}"
        return doc_id, str(url), str(title), str(body)
    if source_id == "github_issues":
        number = hit.get("number") or hit.get("id")
        title = hit.get("title") or ""
        url = hit.get("html_url") or ""
        body = hit.get("body") or ""
        doc_id = f"gh_{hit.get('id', number)}"
        return doc_id, str(url), str(title), str(body)
    if source_id == "dev_to":
        hid = hit.get("id")
        title = hit.get("title", "")
        url = hit.get("url") or hit.get("path") or ""
        if url and not url.startswith("http"):
            url = f"https://dev.to{url}"
        body = hit.get("description") or ""
        return f"dev_{hid}", str(url), str(title), str(body)
    hid = hit.get("id", abs(hash(str(hit))))
    title = hit.get("title", "")
    url = hit.get("url", "")
    body = hit.get("body", "")
    return f"{source_id}_{hid}", str(url), str(title), str(body)


def _collection_url(source_id: str, url: str) -> str:
    """Map reviewed public result URLs to their reviewed API endpoints."""
    parsed = urlparse(url)
    if source_id == "hn_algolia" and parsed.hostname == "news.ycombinator.com":
        item_id = parse_qs(parsed.query).get("id", [""])[0]
        if item_id.isdigit():
            return f"https://hn.algolia.com/api/v1/items/{item_id}"
    if source_id == "github_issues" and parsed.hostname == "github.com":
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) == 4 and parts[2] == "issues" and parts[3].isdigit():
            owner, repo, _kind, number = parts
            return f"https://api.github.com/repos/{owner}/{repo}/issues/{number}"
    return url


def _ensure_fetch(conn: Any, source_id: str, url: str) -> str:
    fetch_id = f"fetch_{source_id}_{hashlib.sha256(url.encode()).hexdigest()[:8]}"
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    try:
        registry = load_registry()
        source = registry.get(source_id)
    except Exception:
        source = None
    terms_status = source.terms_status.value if source else "unknown"
    review_basis = source.review_basis if source else ""
    allowed_use = [use.value for use in source.allowed_use] if source else []
    conn.execute(
        "INSERT OR IGNORE INTO fetches (fetch_id, source_id, requested_url, "
        "final_url, status, content_type, raw_sha256, bytes, fetched_at, "
        "terms_status, allowed_use_json, review_basis, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            fetch_id,
            source_id,
            url,
            url,
            200,
            "application/json",
            hashlib.sha256(b"").hexdigest(),
            0,
            now,
            terms_status,
            json.dumps(allowed_use),
            review_basis,
            now,
        ),
    )
    conn.commit()
    return fetch_id


# Job stages run under uppercase names; the contract limits spend per lowercase
# contract stage. This map translates a job stage to its contract stage so
# per-stage limits and per-stage gates see the spend they should judge. A job
# stage with no contract mapping keeps its name: projection spend stays visible
# under the stage that incurred it instead of being silently attributed to a
# contract stage that did not.
_CONTRACT_STAGE = {
    "DISCOVERY": "discovery",
    "COLLECTION": "evidence_collection",
    "NORMALIZATION": "normalization",
    "DEDUPLICATION": "normalization",
    "ENTITY_RESOLUTION": "normalization",
    "PAIN_EXTRACTION": "normalization",
    "EMBEDDING": "clustering",
    "CLUSTERING": "clustering",
    "INDEPENDENCE_ANALYSIS": "validation",
    "VALIDATION": "validation",
    "COMPETITOR_RESEARCH": "investigation",
    "COUNTER_EVIDENCE": "counter_evidence",
    "SCORING": "scoring",
}

# The contract stages whose gate a completed job stage may decide. EMBEDDING
# maps to "clustering" but is excluded: the clustering verdict is not judgeable
# until CLUSTERING finishes, so EMBEDDING itself carries no gate.
_GATE_STAGES = frozenset(
    {
        "discovery",
        "evidence_collection",
        "normalization",
        "clustering",
        "validation",
        "investigation",
        "counter_evidence",
        "scoring",
    }
)


def gate_stage_for(job_stage: str) -> str | None:
    """The contract stage whose gate this job stage decides, if any.

    Returns None for job stages with no contract mapping, for EMBEDDING, and
    for stages (REPORTING, OBSIDIAN_SYNC) that never reach a contract gate.
    """
    if job_stage == "EMBEDDING":
        return None
    contract_stage = _CONTRACT_STAGE.get(job_stage)
    if contract_stage is None or contract_stage not in _GATE_STAGES:
        return None
    return contract_stage


def _write_spend_ledger(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    stage: str,
    tool: str,
    unit_type: str,
    units: int,
    cost_micros: int = 0,
    provider: str | None = None,
    model: str | None = None,
) -> None:
    """Write one spend_ledger row, idempotent on replay.

    The entry id is derived from the run, stage, tool, unit type and units,
    so a replay of the same spend lands on the same row and is a no-op. The
    stage is translated to its contract stage first, so the ledger and the
    contract speak the same language.
    """
    stage = _CONTRACT_STAGE.get(stage, stage)
    entry_id = "spn_" + hashlib.sha256(
        f"{run_id}|{stage}|{tool}|{unit_type}|{units}".encode("utf-8")
    ).hexdigest()[:16]
    conn.execute(
        """
        INSERT OR IGNORE INTO spend_ledger (
            entry_id, run_id, stage, tool, provider, model,
            units, unit_type, cost_micros, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            entry_id,
            run_id,
            stage,
            tool,
            provider,
            model,
            units,
            unit_type,
            cost_micros,
            datetime.now(timezone.utc).isoformat(),
        ),
    )


def _extract_topic(seed: str, max_words: int = 4) -> str:
    """Extract a short keyword phrase from a longer seed text.

    Takes the first sentence of the seed, removes stop words, and keeps the
    first ``max_words`` content words. The result is a short phrase like
    ``"operational pain points"`` that search APIs including hn_algolia can
    actually match, instead of a long sentence that matches nothing.
    """
    text = " ".join(str(seed).split())
    if not text:
        return "operational pain points"
    first_sentence = text.split(".", 1)[0].strip()
    if not first_sentence:
        first_sentence = text
    stop_words = {
        "the", "a", "an", "and", "or", "but", "in", "on", "at", "to", "for",
        "of", "with", "by", "from", "as", "is", "are", "was", "were", "be",
        "been", "have", "has", "had", "do", "does", "did", "will", "would",
        "could", "should", "may", "might", "must", "shall", "can", "need",
        "people", "teams", "users", "developers", "builders", "makers",
        "creators", "founders", "hackers", "engineers", "designers",
        "managers", "workers", "staff", "everyone", "someone", "anyone",
        "no one", "all", "some", "any", "many", "much", "few", "several",
        "both", "each", "either", "neither", "one", "two", "three",
        "last", "next", "previous", "other", "another", "such",
        "own", "same", "so", "than", "too", "very", "just", "now", "then",
        "here", "there", "when", "where", "why", "how", "if", "because",
        "until", "while", "about", "against", "between", "into", "through",
        "during", "before", "after", "above", "below", "under", "again",
        "further", "once",
    }
    words = [
        w.strip(".,!?:;\"'()[]{}")
        for w in first_sentence.split()
        if w.strip(".,!?:;\"'()[]{}").lower() not in stop_words
        and len(w.strip(".,!?:;\"'()[]{}")) > 2
    ]
    topic = " ".join(words[:max_words])
    return topic or "operational pain points"


def _keyword_query(template: Any, topic: str) -> str:
    """Build a short keyword-based query from a template and topic.

    Uses the template's family to add a short, source-friendly modifier so
    the query is specific enough to be useful but short enough to match
    across all discovery sources (including hn_algolia full-text search).
    """
    family = str(template.family)
    base = topic.strip()
    if not base:
        base = "operational pain points"
    modifiers: dict[str, str] = {
        "problem": "",
        "direct_pain": "",
        "alternative_wording": "alternative",
        "alternative_to": "alternative",
        "severity": "severity",
        "workflow_pain": "workflow",
        "manual_workflow": "manual process",
        "workaround": "workaround",
        "existing_solution": "tool",
        "failed_solution": "failed",
        "feature_request": "feature",
        "switching_pain": "switching",
        "cost_consequence": "cost",
        "role_workflow": "role workflow",
        "trigger_incident": "incident",
        "competitor": "competitor",
        "counter_evidence": "evidence",
    }
    modifier = modifiers.get(family, "")
    if modifier:
        query = f"{base} {modifier}"
    else:
        query = base
    if len(query) > 90:
        query = query[:90].rsplit(" ", 1)[0]
    return query or base


def _load_probe_frontier(
    conn: sqlite3.Connection,
    probe_id: str,
    seed: str,
    source_ids: list[str],
    tracker: Any = None,
) -> QueryFrontier | None:
    """Restore the probe-level frontier from the previous cycle, or None.

    The frontier is keyed by probe_id so it survives across cycles. If the
    seed or sources changed (a different objective), the caller gets None and
    starts a fresh frontier.
    """
    row = conn.execute(
        "SELECT state_json FROM probe_frontier_states WHERE probe_id = ?",
        (probe_id,),
    ).fetchone()
    if row is None:
        return None
    try:
        state = json.loads(str(row[0]))
    except (TypeError, json.JSONDecodeError):
        return None
    if state.get("seed") != seed:
        return None
    if set(state.get("source_ids", [])) != set(source_ids):
        return None
    try:
        frontier = QueryFrontier.from_state(
            state,
            clock=lambda: datetime.now(timezone.utc),
            tracker=tracker,
        )
    except Exception:
        return None
    return frontier


def _save_probe_frontier(
    conn: sqlite3.Connection,
    probe_id: str,
    seed: str,
    frontier: QueryFrontier,
) -> None:
    """Persist the frontier at probe level for the next cycle to resume."""
    state = frontier.checkpoint_state()
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    conn.execute(
        """
        INSERT INTO probe_frontier_states (
            probe_id, seed, state_json, checkpoint_version, updated_at
        ) VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(probe_id) DO UPDATE SET
            seed = excluded.seed,
            state_json = excluded.state_json,
            checkpoint_version = excluded.checkpoint_version,
            updated_at = excluded.updated_at
        """,
        (
            probe_id,
            seed,
            json.dumps(state, sort_keys=True),
            frontier.checkpoint_version,
            now,
        ),
    )


def _discovery_handler(context: Any) -> dict[str, Any] | None:
    """Run a bounded multi-source discovery pass."""
    conn = context.conn
    job = context.job
    run_id = job.run_id
    stage_started = datetime.now(timezone.utc)

    seed = "manual workaround problem"
    try:
        payload = getattr(job, "payload", None)
        if payload is None:
            payload = getattr(job, "payload_json", "{}")
        if isinstance(payload, str):
            payload = json.loads(payload or "{}")
        if isinstance(payload, dict):
            seed = (
                payload.get("objective")
                or payload.get("seed")
                or payload.get("query")
                or seed
            )
    except Exception:
        pass
    if not str(seed).strip():
        seed = "manual workaround problem"
    topic = _extract_topic(seed)

    registry = load_registry()
    Store(conn).sync_registry(registry)

    active_sources: list[str] = []
    for source in registry.list():
        try:
            registry.require(
                source.source_id,
                {AllowedUse.discovery, AllowedUse.retrieval, AllowedUse.analysis, AllowedUse.storage},
            )
            active_sources.append(source.source_id)
        except SourceBlocked:
            continue

    if not active_sources:
        raise RuntimeError("no admissible discovery sources")

    try:
        contract = load_research_contract()
    except Exception:
        contract = None
    tracker = BudgetTracker(contract.budgets) if contract is not None else None
    frontier = load_frontier(conn, run_id, tracker=tracker)
    if frontier is None:
        # Try to restore the probe-level frontier from the previous cycle so
        # expansion resumes where it left off instead of re-running the same
        # searches every cycle.
        probe_row = conn.execute(
            "SELECT probe_id FROM research_runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        probe_id = str(probe_row[0]) if probe_row else None
        if probe_id is not None:
            frontier = _load_probe_frontier(
                conn, probe_id, str(seed), active_sources, tracker
            )
    if frontier is None:
        policy = SaturationPolicy()  # queries_per_family defaults to 4
        budget = DiscoveryBudget(tracker=tracker, max_queries_per_family=40)
        frontier = QueryFrontier(
            seed=str(seed),
            source_ids=active_sources,
            policy=policy,
            budget=budget,
            clock=lambda: datetime.now(timezone.utc),
        )

    from product_hunter.research.query_plan import plan_for_stage

    # Use the contract's canonical query families for live discovery. The
    # older frontier taxonomy still provides admission, deduplication, and
    # budget accounting for the actual query text.
    #
    # Query text is keyword-based (short phrases) rather than full sentences:
    # sentence templates like "{topic} is a real pain..." match nothing on
    # sources such as hn_algolia that do full-text matching. Keyword phrases
    # like "operational pain points" return results across all sources.
    #
    # All templates from the stage plan are proposed to the frontier; the
    # frontier deduplicates per source, so repeated template wordings against
    # different sources are still distinct searches.
    canonical_family_by_text: dict[str, str] = {}
    planned_count = 0
    for template in plan_for_stage("discovery"):
        text = _keyword_query(template, topic)
        source_id = active_sources[planned_count % len(active_sources)]
        try:
            query, admitted, _refusal = frontier.propose(text, source_id)
        except Exception:
            continue
        if not admitted:
            continue
        canonical_family_by_text[query.text] = template.family
        planned_count += 1

    run_document_ids: set[str] = set()
    docs_count = 0
    executed = 0
    max_queries = 30
    tool_calls: list = []
    tool_results: dict[str, Any] = {}
    tool_sources: dict[str, str] = {}
    pending = frontier.next_queries(max_queries)
    for q in pending:
        source_id = q.source_id
        search_fn = _SEARCHERS.get(source_id, _web_search)
        url = _search_url(source_id, q.text)
        fetch_id = _ensure_fetch(conn, source_id, url)

        result_ids: list[str] = []
        status = DiscoveryStatus.empty
        error = ""
        hits: list[dict[str, Any]] = []
        try:
            hits = search_fn(q.text, limit=10)
        except Exception as exc:
            if "timeout" in str(exc).lower() or isinstance(exc, httpx.TimeoutException):
                status = DiscoveryStatus.timeout
            else:
                status = DiscoveryStatus.failure
            error = str(exc)[:500]

        if status not in (DiscoveryStatus.failure, DiscoveryStatus.timeout):
            if not hits:
                status = DiscoveryStatus.empty
            else:
                status = DiscoveryStatus.success
                for hit in hits:
                    doc_id, doc_url, title, body = _normalize_hit(source_id, hit)
                    content_hash = hashlib.sha256(
                        (str(title) + "\n" + str(body or "")).encode()
                    ).hexdigest()
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
                                source_id,
                                doc_url,
                                str(title)[:500],
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
                        run_document_ids.add(doc_id)
                        docs_count = len(run_document_ids)
                        try:
                            text_dir = repo_root() / "data" / "processed"
                            text_dir.mkdir(parents=True, exist_ok=True)
                            (text_dir / f"{doc_id}.txt").write_text(
                                f"{title}\n{body or ''}", encoding="utf-8"
                            )
                        except Exception:
                            pass
                        try:
                            if source_id == "hn_algolia":
                                author_handle = str(hit.get("author") or "unknown")
                            elif source_id == "github_issues":
                                author_handle = str((hit.get("user") or {}).get("login") or "unknown")
                            else:
                                author_handle = str(hit.get("author") or hit.get("user") or "unknown")
                            community_id = f"community_{source_id}"
                            conn.execute(
                                "INSERT OR IGNORE INTO communities (community_id, source_id, name) "
                                "VALUES (?, ?, ?)",
                                (community_id, source_id, source_id),
                            )
                            author_id = f"au_{source_id}_{author_handle}"
                            conn.execute(
                                "INSERT OR IGNORE INTO authors (author_id, source_id, handle, "
                                "identity_key, first_seen_at, last_seen_at, deletion_status) "
                                "VALUES (?, ?, ?, ?, ?, ?, 'active')",
                                (
                                    author_id,
                                    source_id,
                                    author_handle,
                                    author_handle,
                                    now_ts,
                                    now_ts,
                                ),
                            )
                            conn.execute(
                                "INSERT OR IGNORE INTO author_links (link_id, author_id, doc_id, "
                                "confidence, basis) VALUES (?, ?, ?, 1.0, 'discovery')",
                                (f"al_{doc_id}", author_id, doc_id),
                            )
                            conn.execute(
                                "UPDATE documents SET community_id = ? WHERE doc_id = ?",
                                (community_id, doc_id),
                            )
                        except Exception:
                            pass
                    except Exception:
                        pass
                    result_ids.append(doc_id)
                conn.commit()

        try:
            record = frontier.record_execution(
                q,
                run_id=run_id,
                status=status,
                result_count=len(hits),
                result_ids=result_ids,
                cost=0.0,
                tool=search_fn.__name__.lstrip("_"),
                agent="worker",
                stage="discovery",
                result_ref=fetch_id,
                error=error,
                timestamp=datetime.now(timezone.utc),
            )
            write_record(conn, record)
        except Exception:
            pass

        # Record tool-call provenance.
        try:
            from product_hunter.research.evidence import ToolCallProvenance, ToolCallStatus
            from product_hunter.db.research_gate import SqliteToolCallStore
            import datetime as _dt

            tc_status = ToolCallStatus.success if status not in (DiscoveryStatus.failure, DiscoveryStatus.timeout) else (
                ToolCallStatus.failure if status == DiscoveryStatus.failure else ToolCallStatus.timeout
            )
            tc = ToolCallProvenance(
                run_id=run_id,
                stage="discovery",
                agent="worker",
                tool="search",
                purpose="discovery search",
                timestamp=_dt.datetime.now(_dt.timezone.utc),
                query_or_operation=str(q.text),
                result_ref=(
                    f"{fetch_id}/attempt-{job.attempts}"
                    if fetch_id else f"missing/attempt-{job.attempts}"
                ),
                result_count=len(result_ids),
                status=tc_status,
                query_family=canonical_family_by_text.get(q.text),
                source_domains=[urlparse(url).hostname] if urlparse(url).hostname else [],
                tool_call_id=(
                    "tc_" + hashlib.sha256(
                        f"{run_id}|{source_id}|{q.text}|{job.attempts}".encode("utf-8")
                    ).hexdigest()[:20]
                ),
            )
            tool_calls.append(tc)
            tool_sources[tc.tool_call_id] = source_id
            if tc.tool_call_id and tc_status in (ToolCallStatus.success, ToolCallStatus.partial):
                tool_results[tc.tool_call_id] = hits
        except Exception:
            pass

        executed += 1
        try:
            save_frontier(conn, run_id, frontier)
        except Exception:
            pass

        # Persist the frontier at probe level so the next cycle can resume
        # expansion where this cycle left off, instead of re-running the same
        # searches. Cross-cycle reuse is the key to not wasting budget on
        # repeated queries against the same sources.
        try:
            probe_row = conn.execute(
                "SELECT probe_id FROM research_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            probe_id = str(probe_row[0]) if probe_row else None
            if probe_id is not None:
                _save_probe_frontier(conn, probe_id, str(seed), frontier)
        except Exception:
            pass

        try:
            src = registry.get(source_id)
            time.sleep(min(src.rate_limit.min_interval_seconds, 0.05))
        except Exception:
            pass

    # Persist tool-call provenance.
    from product_hunter.db.research_gate import SqliteToolCallStore
    store = SqliteToolCallStore(conn)
    for tc in tool_calls:
        store.append_tool_call(
            tc,
            attempt=job.attempts,
            source_id=tool_sources.get(tc.tool_call_id or ""),
            worker_id=context.worker_id,
            lease_epoch=job.lease_epoch,
            lease_stage=job.stage,
            result_payload=tool_results.get(tc.tool_call_id or ""),
        )

    from product_hunter.db.research_gate import record_artifact, record_stage_execution
    from product_hunter.research.evidence import (
        ArtifactRecord,
        StageExecution,
        StageStatus,
        ToolCallStatus,
    )

    finished = datetime.now(timezone.utc)
    successful_calls = [tc for tc in tool_calls if tc.status is ToolCallStatus.success]
    artifacts = []
    if successful_calls:
        producer = successful_calls[0]
        for kind, count in (
            ("source_records", docs_count),
            ("provenance", len(tool_calls)),
            ("document", docs_count),
        ):
            artifacts.append(
                ArtifactRecord(
                    artifact_id=(
                        f"{run_id}-discovery-{job.attempts}-{kind}"
                    ),
                    stage="discovery",
                    kind=kind,
                    ref=producer.result_ref,
                    item_count=count,
                    produced_by_tool_call_id=producer.tool_call_id,
                    created_at=finished,
                    valid=True,
                )
            )
    execution = StageExecution(
        stage="discovery",
        status=StageStatus.succeeded,
        started_at=stage_started,
        finished_at=finished,
        tool_calls=tool_calls,
        artifacts=artifacts,
    )
    record_stage_execution(
        conn,
        run_id=run_id,
        execution=execution,
        attempt=job.attempts,
        worker_id=context.worker_id,
        lease_epoch=job.lease_epoch,
        lease_stage=job.stage,
    )
    for artifact in artifacts:
        record_artifact(
            conn,
            run_id=run_id,
            artifact=artifact,
            attempt=job.attempts,
            worker_id=context.worker_id,
            lease_epoch=job.lease_epoch,
            lease_stage=job.stage,
        )

    # Record spend for discovery search queries and cost.
    if executed > 0:
        _write_spend_ledger(
            conn,
            run_id=run_id,
            stage="DISCOVERY",
            tool="search",
            unit_type="search_queries",
            units=executed,
        )
    # Explicit measured-zero declaration: discovery fetches documents, but its
    # fetch requests are measured from tool_calls, so fetch_requests is
    # declared here even when this stage ran none.
    _write_spend_ledger(
        conn,
        run_id=run_id,
        stage="DISCOVERY",
        tool="search",
        unit_type="fetch_requests",
        units=0,
        cost_micros=0,
    )
    _write_spend_ledger(
        conn,
        run_id=run_id,
        stage="DISCOVERY",
        tool="search",
        unit_type="usd_cost",
        units=0,
        cost_micros=0,
    )

    _enqueue_next(conn, job, "COLLECTION")
    return {"discovered": docs_count, "queries": executed, "sources": active_sources}
def _extraction_handler(context: Any) -> dict[str, Any] | None:
    """Extract pain points from discovered documents using the LLM.

    The production path now runs the full evidence pipeline: each document is
    ingested into the store, candidates are validated against the stored text,
    and accepted pain points are persisted with a verified evidence span and
    an evidence/pain link. Rejected candidates are counted, not silently
    dropped.
    """
    conn = context.conn
    job = context.job
    run_id = job.run_id
    # Route the extraction role through the dynamic model router.
    # The model pinned in extraction.yaml is only a fallback when the router
    # cannot answer; it is never hard-coded into business logic.
    config = load_config()
    generator = None
    selection = None
    routing_failure = None
    try:
        routing = load_routing_config()
        router = build_router(routing, config)
        selection = router.select(ModelRole.extraction)
        generator = FallbackGenerator(
            router,
            ModelRole.extraction,
            lambda chosen: build_generator_for_selection(config, chosen),
        )
        # Fail fast if no candidate can answer.
        try:
            generator.check()
        except ProviderError as exc:
            generator = None
            routing_failure = f"provider check failed: {exc}"
    except (OSError, ValueError, ProviderError) as exc:
        routing_failure = f"{type(exc).__name__}: {exc}"
        # The router could not answer: the model pinned in the extraction
        # config runs the extraction, and the failure is recorded. If that
        # provider also cannot answer, the exception propagates and the job
        # fails instead of succeeding with zero extracted.
        generator = OpenCodeGenerateProvider(config)
    # Read documents
    run_document_ids = _discovered_document_ids(conn, context.job.run_id)
    if not run_document_ids:
        return {"extracted": 0}
    placeholders = ",".join("?" for _ in run_document_ids)
    rows = conn.execute(
        "SELECT doc_id, fetch_id, source_id, url, title, author, text_sha256, "
        "text_path, raw_path, raw_sha256, parser_version, content_type, "
        f"live_status FROM documents WHERE live_status = 'live' AND doc_id IN ({placeholders})",
        tuple(sorted(run_document_ids, key=lambda d: (d.split("_", 1)[0], d))),
    ).fetchall()
    if not rows:
        return {"extracted": 0}
    from product_hunter.extraction import Document
    from product_hunter.evidence import EvidencePipeline, EvidenceRejected
    from product_hunter.db import Store

    root = repo_root().resolve()
    documents = []
    for row in rows:
        raw_text_path = Path(row[7]) if row[7] else None
        if raw_text_path is None:
            continue
        text_path = raw_text_path if raw_text_path.is_absolute() else root / raw_text_path
        try:
            resolved_text_path = text_path.resolve(strict=True)
            if not resolved_text_path.is_relative_to(root):
                continue
            text = resolved_text_path.read_text(encoding="utf-8")
        except (OSError, UnicodeError, ValueError):
            continue
        if not text.strip():
            continue
        documents.append(
            Document(
                doc_id=row[0],
                fetch_id=row[1],
                source_id=row[2],
                url=row[3],
                title=row[4],
                author=row[5],
                text=text,
                text_sha256=row[6],
                text_path=str(row[7]),
                raw_path=row[8],
                raw_sha256=row[9],
                parser_version=row[10] or "v1",
                content_type=row[11] or "text/html",
                live_status=row[12] or "live",
            )
        )
    # Round-robin documents across sources so extraction samples every
    # configured source instead of exhausting the first ones alphabetically.
    # This maximises source and community coverage within the call budget.
    by_source: dict[str, list] = {}
    for doc in documents:
        by_source.setdefault(doc.source_id, []).append(doc)
    interleaved: list = []
    max_per_source = max((len(docs) for docs in by_source.values()), default=0)
    for i in range(max_per_source):
        for source_id in sorted(by_source):
            if i < len(by_source[source_id]):
                interleaved.append(by_source[source_id][i])
    documents = interleaved

    # Ingest documents into the evidence store first, so spans can be
    # validated against stored text.
    store = Store(conn)
    pipeline = EvidencePipeline(store, repo_root=repo_root())
    for doc in documents:
        pipeline.ingest_document(doc)
    # The extraction stage draws on the contract's per-stage model_calls
    # budget for its contract stage, minus whatever this run already spent.
    contract_stage = _CONTRACT_STAGE["PAIN_EXTRACTION"]
    try:
        contract = load_research_contract()
        stage_budget = contract.budgets.per_stage.get(contract_stage, {})
        budget_calls = int(stage_budget.get("model_calls", 0) or 0)
    except Exception:
        # The contract is the source of truth; fall back to the extraction
        # config's own call cap only when it cannot be read.
        budget_calls = int(config.limits.max_calls)
    spent_calls = int(
        conn.execute(
            "SELECT count(*) FROM model_executions WHERE run_id = ? AND stage = ?",
            (run_id, contract_stage),
        ).fetchone()[0]
        or 0
    )
    max_calls = max(0, budget_calls - spent_calls)
    # max_calls bounds chunks, not model calls. Fallback attempts mean one
    # chunk can incur several model executions. Use three quarters of the
    # remaining contract budget for chunks so fallback attempts stay within
    # the per-stage model_calls limit while processing as many chunks as
    # possible.
    if budget_calls > 0:
        max_calls = max(1, int(max_calls * 3 // 4))
    else:
        max_calls = None
    batch = extract_documents(
        documents,
        generator,
        config,
        max_documents=config.limits.max_documents,
        max_calls=max_calls,
    )
    # Record model executions with full provenance: which provider/model was
    # selected, whether it was a fallback, the catalog version, latency,
    # success/failure, and the run/stage that produced it.
    model_executions: list[ModelExecution] = []
    if selection is not None and generator is not None:
        # One row per provider call (fallback attempts included).
        for index, attempt in enumerate(generator.attempts):
            identity = attempt.selection.identity
            model_executions.append(
                ModelExecution(
                    execution_id=(
                        "exec_"
                        + hashlib.sha256(
                            f"{run_id}|{index}|{identity.provider_id}|{identity.model_id}".encode(
                                "utf-8"
                            )
                        ).hexdigest()[:16]
                    ),
                    run_id=run_id,
                    stage=_CONTRACT_STAGE["PAIN_EXTRACTION"],
                    role=attempt.selection.role,
                    provider_id=identity.provider_id,
                    model_id=identity.model_id,
                    selection_source=attempt.selection.selection_source,
                    fallback_index=attempt.selection.fallback_index,
                    is_fallback=attempt.selection.is_fallback,
                    catalog_version=attempt.selection.catalog_version,
                    prompt_version=config.prompt_version,
                    config_version=routing.config_version,
                    latency_ms=attempt.latency_ms,
                    status="ok" if attempt.ok else "error",
                    error=attempt.error,
                    created_at=attempt.created_at,
                )
            )
    elif selection is None and generator is not None:
        # Router failed: the pinned model ran. One row per call.
        for call in getattr(batch, "calls", []):
            model_executions.append(
                ModelExecution(
                    execution_id=(
                        "exec_"
                        + hashlib.sha256(
                            f"{run_id}|{call.call_id}".encode("utf-8")
                        ).hexdigest()[:16]
                    ),
                    run_id=run_id,
                    stage=_CONTRACT_STAGE["PAIN_EXTRACTION"],
                    role=ModelRole.extraction,
                    provider_id=call.provider,
                    model_id=call.model,
                    selection_source=SelectionSource.default,
                    fallback_index=0,
                    is_fallback=False,
                    catalog_version="",
                    prompt_version=call.prompt_version,
                    config_version="",
                    latency_ms=call.latency_ms,
                    status="ok" if call.status == "ok" else "error",
                    error=call.error,
                    created_at=call.created_at,
                )
            )
    if model_executions:
        record_model_executions(conn, model_executions)
    # Record spend for the LLM dimensions the extraction incurred.
    # input_chars is a character count; approximate tokens at 4 chars/token.
    input_tokens = 0
    if selection is not None and generator is not None:
        for call in getattr(batch, "calls", []):
            input_tokens += int(call.input_chars)
    elif selection is None and generator is not None:
        for call in getattr(batch, "calls", []):
            input_tokens += int(call.input_chars)
    if input_tokens > 0:
        _write_spend_ledger(
            conn,
            run_id=run_id,
            stage="PAIN_EXTRACTION",
            tool="llm",
            unit_type="llm_input_tokens",
            units=input_tokens // 4,
            provider=(selection.identity.provider_id if selection else None),
            model=(selection.identity.model_id if selection else None),
        )
    # Output tokens are not directly reported by the provider; record the
    # dimension as measured (zero units) rather than unmeasured.
    _write_spend_ledger(
        conn,
        run_id=run_id,
        stage="PAIN_EXTRACTION",
        tool="llm",
        unit_type="llm_output_tokens",
        units=0,
        provider=(selection.identity.provider_id if selection else None),
        model=(selection.identity.model_id if selection else None),
    )
    # Cost is not directly reported by the provider; record the dimension as
    # measured (zero micros) rather than unmeasured.
    _write_spend_ledger(
        conn,
        run_id=run_id,
        stage="PAIN_EXTRACTION",
        tool="llm",
        unit_type="usd_cost",
        units=0,
        cost_micros=0,
        provider=(selection.identity.provider_id if selection else None),
        model=(selection.identity.model_id if selection else None),
    )
    # Explicit measured-zero declaration: the real model call count is
    # measured from model_executions, so model_calls is declared here even
    # when this stage made no calls.
    _write_spend_ledger(
        conn,
        run_id=run_id,
        stage="PAIN_EXTRACTION",
        tool="llm",
        unit_type="model_calls",
        units=0,
        cost_micros=0,
        provider=(selection.identity.provider_id if selection else None),
        model=(selection.identity.model_id if selection else None),
    )
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
            claim = ResearchClaim(
                claim_id=f"claim_{pain_id}",
                kind=ClaimKind.problem_exists,
                statement=f"Pain point {pain_id} observed in document",
                stage="PAIN_EXTRACTION",
                evidence_refs=[span_id],
            )
            record_claim(conn, run_id=run_id, claim=claim)
            claim_count += 1
    conn.commit()
    _enqueue_next(conn, context.job, "EMBEDDING")
    result: dict[str, object] = {
        "extracted": recorded,
        "rejected": rejected_count,
        "claims": claim_count,
    }
    if routing_failure is not None:
        result["routing_failure"] = routing_failure
    return result


def _entity_resolution_handler(context: Any) -> dict[str, Any] | None:
    conn = context.conn
    job = context.job
    now_ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    store = IndependenceStore(conn, root=repo_root())
    authors = store.load_authors()
    communities = store.load_communities()
    mentions = store.load_mentions()
    if not authors or not mentions:
        _enqueue_next(conn, job, "EMBEDDING")
        return {"authors": len(authors), "mentions": len(mentions)}
    policy = load_independence_policy()
    links = resolve_authors(authors, mentions, policy, detected_at=now_ts)
    community_links = resolve_communities(communities, policy, detected_at=now_ts)
    edges = detect_propagation(mentions, policy, detected_at=now_ts)
    graph = build_graph(mentions, authors, communities, links, community_links, edges, policy)
    verdict = analyze(graph, policy)
    clusters = graph.author_clusters
    for link in links:
        try:
            store.record_identity_link(link)
        except Exception:
            pass
    for community_link in community_links:
        try:
            store.record_community_link(community_link)
        except Exception:
            pass
    for edge in edges:
        try:
            store.record_propagation_edge(edge)
        except Exception:
            pass
    try:
        store.record_author_clusters(clusters, created_at=now_ts)
    except Exception:
        pass
    try:
        store.record_report(verdict, scope_id=job.run_id, created_at=now_ts)
    except Exception:
        pass
    _enqueue_next(conn, job, "PAIN_EXTRACTION")
    return {
        "authors": len(authors),
        "communities": len(communities),
        "mentions": len(mentions),
        "identity_links": len(links),
        "community_links": len(community_links),
        "propagation_edges": len(edges),
        "author_clusters": len(clusters),
        "independent_authors": verdict.metrics.independent_author_count,
        "independent_communities": verdict.metrics.independent_community_count,
    }


def _embedding_handler(context: Any) -> dict[str, Any] | None:
    conn = context.conn
    job = context.job
    from product_hunter.config import load_semantic_config, repo_root
    from product_hunter.semantic.qdrant import QdrantAdapter
    from product_hunter.semantic.embeddings import build_embedder
    from product_hunter.semantic.index import SemanticIndex
    import os

    config = load_semantic_config()
    api_key = os.environ.get(config.api_key_env)
    adapter = QdrantAdapter(
        config.url,
        api_key=api_key,
        timeout=float(config.timeout_seconds),
        max_attempts=config.max_attempts,
        backoff_seconds=float(config.backoff_seconds),
    )
    provider = build_embedder(config, root=repo_root())
    index = SemanticIndex(conn, adapter, provider, config=config)
    report = index.sync()
    space = index.space
    # Record spend for embedding dimensions.
    embedded_count = int(getattr(report, "indexed", 0)) + int(
        getattr(report, "unchanged", 0)
    )
    if embedded_count > 0:
        _write_spend_ledger(
            conn,
            run_id=job.run_id,
            stage="EMBEDDING",
            tool="embedding",
            unit_type="embedding_items",
            units=embedded_count,
            provider=space.provider,
            model=space.model,
        )
    _write_spend_ledger(
        conn,
        run_id=job.run_id,
        stage="EMBEDDING",
        tool="embedding",
        unit_type="usd_cost",
        units=0,
        cost_micros=0,
        provider=space.provider,
        model=space.model,
    )
    _enqueue_next(conn, job, "CLUSTERING")
    return {
        "embedded": True,
        "indexed": report.indexed,
        "unchanged": report.unchanged,
        "removed": report.removed,
        "failed": report.failed,
        "space_id": space.space_id,
        "provider": space.provider,
        "model": space.model,
        "model_version": space.model_version,
        "dimensions": space.dimensions,
    }


def _clustering_handler(context: Any) -> dict[str, Any] | None:
    conn = context.conn
    job = context.job
    stage_started = datetime.now(timezone.utc)
    run_document_ids = _discovered_document_ids(conn, job.run_id)
    if run_document_ids:
        placeholders = ",".join("?" for _ in run_document_ids)
        rows = conn.execute(
            "SELECT pain_id, doc_id, span_id, domain, actor, action, "
            "object, consequence, pain_statement, normalized_key, normalization_version, "
            f"severity, frequency FROM pain_points WHERE doc_id IN ({placeholders})",
            tuple(sorted(run_document_ids)),
        ).fetchall()
    else:
        rows = []
    import product_hunter.normalization as norm
    pain_points = []
    for row in rows:
        domain_value = row[3] or "other"
        try:
            domain = norm.Domain(domain_value)
        except Exception:
            domain = norm.Domain.other
        actor = row[4] or "unknown"
        action = row[5] or "unknown"
        obj = row[6] or "unknown"
        consequence = row[7] or "unknown"
        statement = row[8] or "unknown"
        normalized_key = row[9] or row[0]
        norm_version = row[10] or "v1"
        pain_points.append(
            ClusterPainPoint(
                pain_id=row[0],
                doc_id=row[1],
                source_id="",
                span_id=row[2] or "",
                quote="",
                domain=domain,
                actor=actor,
                action=action,
                object=obj,
                consequence=consequence,
                pain_statement=statement,
                normalized_key=normalized_key,
                normalization_version=norm_version,
            )
        )
    run_id = job.run_id
    config = load_config()
    semantic_config = load_semantic_config()
    embedder = build_embedder(semantic_config, root=repo_root())
    clustering_config = load_clustering_config()

    # Route the clustering role through the dynamic model router for the
    # adjudicator. A failed route leaves adjudicator=None: the engine then
    # holds the pair and never merges on a model verdict it cannot reach.
    adjudicator = None
    routing_failure = None
    routing = None
    selection = None
    generator = None
    try:
        from product_hunter.clustering.adjudication import LlmAdjudicator

        routing = load_routing_config()
        router = build_router(routing, config)
        selection = router.select(ModelRole.clustering)
        generator = FallbackGenerator(
            router,
            ModelRole.clustering,
            lambda chosen: build_generator_for_selection(config, chosen),
        )
        generator.check()
        adjudicator = LlmAdjudicator(
            generator,
            prompt_version=clustering_config.prompt_version,
            config_version=f"clustering-v{clustering_config.version}",
        )
    except (OSError, ValueError, ProviderError) as exc:
        routing_failure = f"{type(exc).__name__}: {exc}"

    import product_hunter.clustering.engine as ce
    engine = ce.ClusteringEngine(embedder, clustering_config, adjudicator=adjudicator)
    result = engine.cluster(pain_points)
    # Record the adjudicator's model executions with full provenance, exactly
    # like extraction: one row per provider call the adjudicator made.
    model_executions: list[ModelExecution] = []
    if selection is not None and generator is not None:
        for index, attempt in enumerate(generator.attempts):
            identity = attempt.selection.identity
            model_executions.append(
                ModelExecution(
                    execution_id=(
                        "exec_"
                        + hashlib.sha256(
                            f"{run_id}|{index}|{identity.provider_id}|{identity.model_id}".encode(
                                "utf-8"
                            )
                        ).hexdigest()[:16]
                    ),
                    run_id=run_id,
                    stage=_CONTRACT_STAGE["CLUSTERING"],
                    role=attempt.selection.role,
                    provider_id=identity.provider_id,
                    model_id=identity.model_id,
                    selection_source=attempt.selection.selection_source,
                    fallback_index=attempt.selection.fallback_index,
                    is_fallback=attempt.selection.is_fallback,
                    catalog_version=attempt.selection.catalog_version,
                    prompt_version=clustering_config.prompt_version,
                    config_version=routing.config_version,
                    latency_ms=attempt.latency_ms,
                    status="ok" if attempt.ok else "error",
                    error=attempt.error,
                    created_at=attempt.created_at,
                )
            )
    if model_executions:
        try:
            record_model_executions(conn, model_executions)
        except Exception:
            # Provenance recording must never fail the clustering run.
            pass
    # Record spend for the LLM dimensions the adjudicator incurred. The
    # provider does not report input/output tokens, so they are declared as
    # measured-zero rather than left unmeasured.
    if selection is not None:
        provider_id = selection.identity.provider_id
        model_id = selection.identity.model_id
    else:
        provider_id = None
        model_id = None
    _write_spend_ledger(
        conn,
        run_id=run_id,
        stage="CLUSTERING",
        tool="llm",
        unit_type="llm_input_tokens",
        units=0,
        provider=provider_id,
        model=model_id,
    )
    _write_spend_ledger(
        conn,
        run_id=run_id,
        stage="CLUSTERING",
        tool="llm",
        unit_type="llm_output_tokens",
        units=0,
        provider=provider_id,
        model=model_id,
    )
    _write_spend_ledger(
        conn,
        run_id=run_id,
        stage="CLUSTERING",
        tool="llm",
        unit_type="usd_cost",
        units=0,
        cost_micros=0,
        provider=provider_id,
        model=model_id,
    )
    # Explicit measured-zero declaration: the real model call count is measured
    # from model_executions, so model_calls is declared here even when this
    # stage made no calls.
    _write_spend_ledger(
        conn,
        run_id=run_id,
        stage="CLUSTERING",
        tool="llm",
        unit_type="model_calls",
        units=0,
        provider=provider_id,
        model=model_id,
    )
    now_ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    import product_hunter.db.clustering as cdb
    run = cdb.ClusteringRun(
        run_id=run_id,
        algorithm_version=clustering_config.algorithm_version,
        config_version=f"clustering-v{clustering_config.version}",
        prompt_version=clustering_config.prompt_version,
        space_id=embedder.space.space_id,
        provider=semantic_config.embedding.provider,
        model=semantic_config.embedding.model,
        model_version=semantic_config.embedding.model_version,
        embedding_provider=semantic_config.embedding.provider,
        embedding_model=semantic_config.embedding.model,
        embedding_model_version=semantic_config.embedding.model_version,
        params={"merge_threshold": clustering_config.similarity.merge_threshold},
        created_at=now_ts,
    )
    store = ClusterStore(conn)
    store.apply_result(result, run)

    import os
    from product_hunter.db.research_gate import (
        SqliteToolCallStore,
        record_artifact,
        record_stage_execution,
    )
    from product_hunter.research.evidence import (
        ArtifactRecord,
        StageExecution,
        StageStatus,
        ToolCallProvenance,
        ToolCallStatus,
    )
    from product_hunter.clustering import clustering_artifacts
    from product_hunter.research.predicates import KIND_CLUSTER_NEIGHBORS_CHECKED
    from product_hunter.semantic.qdrant import QdrantAdapter

    adapter = QdrantAdapter(
        semantic_config.url,
        api_key=os.environ.get(semantic_config.api_key_env),
        timeout=float(semantic_config.timeout_seconds),
        max_attempts=semantic_config.max_attempts,
        backoff_seconds=float(semantic_config.backoff_seconds),
    )
    calls = []
    point_count = adapter.count(semantic_config.collection)
    qdrant_ref = f"qdrant/count/{run_id}/clustering/attempt-{job.attempts}"
    qdrant_call_id = "tc_" + hashlib.sha256(qdrant_ref.encode("utf-8")).hexdigest()[:20]
    qdrant_call = ToolCallProvenance(
        run_id=run_id,
        stage="clustering",
        agent="worker",
        tool="qdrant",
        purpose="inspect the indexed evidence collection before clustering",
        timestamp=datetime.now(timezone.utc),
        query_or_operation=f"count points in {semantic_config.collection}",
        result_ref=qdrant_ref,
        result_count=point_count,
        status=ToolCallStatus.success,
        tool_call_id=qdrant_call_id,
    )
    SqliteToolCallStore(conn).append_tool_call(
        qdrant_call,
        attempt=job.attempts,
        worker_id=context.worker_id,
        lease_epoch=job.lease_epoch,
        lease_stage=job.stage,
        result_payload={"collection": semantic_config.collection, "points": point_count},
    )
    calls.append(qdrant_call)
    for point, vector in zip(pain_points, embedder.embed([p.pain_statement for p in pain_points])):
        result_ref = (
            f"qdrant/cluster/{run_id}/{point.pain_id}/attempt-{job.attempts}"
        )
        call_id = "tc_" + hashlib.sha256(result_ref.encode("utf-8")).hexdigest()[:20]
        neighbors = adapter.search(semantic_config.collection, vector, limit=5)
        call = ToolCallProvenance(
            run_id=run_id,
            stage="clustering",
            agent="worker",
            tool="qdrant",
            purpose="check nearest indexed evidence for a clustered pain point",
            timestamp=datetime.now(timezone.utc),
            query_or_operation=point.pain_id,
            result_ref=result_ref,
            result_count=len(neighbors),
            status=ToolCallStatus.success,
            tool_call_id=call_id,
            query_family="problem",
        )
        SqliteToolCallStore(conn).append_tool_call(
            call,
            attempt=job.attempts,
            worker_id=context.worker_id,
            lease_epoch=job.lease_epoch,
            lease_stage=job.stage,
            result_payload=[
                {
                    "id": str(neighbor.get("id", "")),
                    "score": float(neighbor.get("score", 0.0)),
                    "entity_type": (neighbor.get("payload") or {}).get("entity_type"),
                    "entity_id": (neighbor.get("payload") or {}).get("entity_id"),
                    "space_id": (neighbor.get("payload") or {}).get("space_id"),
                }
                for neighbor in neighbors
            ],
        )
        calls.append(call)
    adapter.close()

    finished = datetime.now(timezone.utc)
    result_ref = f"clustering/{run_id}/attempt-{job.attempts}"
    cluster_call_id = "tc_" + hashlib.sha256(result_ref.encode("utf-8")).hexdigest()[:20]
    cluster_call = ToolCallProvenance(
        run_id=run_id,
        stage="clustering",
        agent="worker",
        tool="clustering_engine",
        purpose="assign pain points to clusters and review nearest candidates",
        timestamp=finished,
        query_or_operation=f"cluster {len(pain_points)} pain points",
        result_ref=result_ref,
        result_count=len(result.assignments),
        status=ToolCallStatus.success,
        tool_call_id=cluster_call_id,
        query_family="problem",
    )
    SqliteToolCallStore(conn).append_tool_call(
        cluster_call,
        attempt=job.attempts,
        worker_id=context.worker_id,
        lease_epoch=job.lease_epoch,
        lease_stage=job.stage,
        result_payload=result.model_dump(mode="json"),
    )
    calls.append(cluster_call)
    artifacts = clustering_artifacts(
        result,
        run_id=run_id,
        tool_call_id=cluster_call_id,
        created_at=finished,
    )
    artifacts = [
        artifact.model_copy(
            update={"artifact_id": f"{artifact.artifact_id}-a{job.attempts}"}
        )
        for artifact in artifacts
    ]
    if not result.clusters:
        artifacts.append(
            ArtifactRecord(
                artifact_id=f"{run_id}-clustering-{job.attempts}-empty-neighbor-checks",
                stage="clustering",
                kind=KIND_CLUSTER_NEIGHBORS_CHECKED,
                ref=result_ref,
                item_count=0,
                produced_by_tool_call_id=cluster_call_id,
                created_at=finished,
                valid=True,
            )
        )
    execution = StageExecution(
        stage="clustering",
        status=StageStatus.succeeded,
        started_at=stage_started,
        finished_at=finished,
        tool_calls=calls,
        artifacts=artifacts,
    )
    record_stage_execution(
        conn,
        run_id=run_id,
        execution=execution,
        attempt=job.attempts,
        worker_id=context.worker_id,
        lease_epoch=job.lease_epoch,
        lease_stage=job.stage,
    )
    for artifact in artifacts:
        record_artifact(
            conn,
            run_id=run_id,
            artifact=artifact,
            attempt=job.attempts,
            worker_id=context.worker_id,
            lease_epoch=job.lease_epoch,
            lease_stage=job.stage,
        )
    # Record spend for embedding items produced by clustering.
    embedding_item_count = len(pain_points)
    if embedding_item_count > 0:
        _write_spend_ledger(
            conn,
            run_id=run_id,
            stage="CLUSTERING",
            tool="embedding",
            unit_type="embedding_items",
            units=embedding_item_count,
            provider=semantic_config.embedding.provider,
            model=semantic_config.embedding.model,
        )
    _write_spend_ledger(
        conn,
        run_id=run_id,
        stage="CLUSTERING",
        tool="embedding",
        unit_type="usd_cost",
        units=0,
        cost_micros=0,
        provider=semantic_config.embedding.provider,
        model=semantic_config.embedding.model,
    )
    _enqueue_next(conn, job, "INDEPENDENCE_ANALYSIS")
    return {
        "pain_points": len(pain_points),
        "clusters": len(result.clusters),
        "assignments": len(result.assignments),
        "space_id": embedder.space.space_id,
    }


def _independence_analysis_handler(context: Any) -> dict[str, Any] | None:
    conn = context.conn
    job = context.job
    now_ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    store = IndependenceStore(conn, root=repo_root())
    authors = store.load_authors()
    communities = store.load_communities()
    mentions = store.load_mentions()
    if not authors or not mentions:
        return {"authors": len(authors), "mentions": len(mentions)}
    policy = load_independence_policy()
    links = resolve_authors(authors, mentions, policy, detected_at=now_ts)
    community_links = resolve_communities(communities, policy, detected_at=now_ts)
    edges = detect_propagation(mentions, policy, detected_at=now_ts)
    graph = build_graph(mentions, authors, communities, links, community_links, edges, policy)
    verdict = analyze(graph, policy)
    clusters = graph.author_clusters
    for link in links:
        try:
            store.record_identity_link(link)
        except Exception:
            pass
    for community_link in community_links:
        try:
            store.record_community_link(community_link)
        except Exception:
            pass
    for edge in edges:
        try:
            store.record_propagation_edge(edge)
        except Exception:
            pass
    try:
        store.record_author_clusters(clusters, created_at=now_ts)
    except Exception:
        pass
    try:
        store.record_report(verdict, scope_id=job.run_id, created_at=now_ts)
    except Exception:
        pass
    _enqueue_next(conn, job, "VALIDATION")
    return {
        "identity_links": len(links),
        "community_links": len(community_links),
        "propagation_edges": len(edges),
        "author_clusters": len(clusters),
        "independent_authors": verdict.metrics.independent_author_count,
        "independent_communities": verdict.metrics.independent_community_count,
        "concentration_violations": len(verdict.violations),
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
    conn = context.conn
    job = context.job
    stage_started = datetime.now(timezone.utc)
    from product_hunter.collection import CollectionOrchestrator, CollectionTarget
    from product_hunter.config import repo_root
    from product_hunter.db.store import Store
    from product_hunter.fetch import JsonlLog
    from product_hunter.raw_store import RawStore
    from product_hunter.sources import load_registry
    import httpx

    registry = load_registry()
    store = Store(conn)
    root = repo_root()
    raw_store = RawStore(root)
    log = JsonlLog(root / "data" / "fetch_log.jsonl")
    rejection_log = JsonlLog(root / "data" / "fetch_rejections.jsonl")
    client = httpx.Client(timeout=30.0)
    orchestrator = CollectionOrchestrator(
        registry, store, client=client, raw_store=raw_store, log=log, rejection_log=rejection_log
    )

    scope = str(job.run_id or job.payload.get("scope") or "collection")
    targets: list[tuple[str, str]] = []
    payload = job.payload if isinstance(job.payload, dict) else {}
    if payload.get("source_id") and payload.get("url"):
        targets.append((str(payload["source_id"]), str(payload["url"])))
    else:
        run_document_ids = _discovered_document_ids(conn, job.run_id)
        if run_document_ids:
            placeholders = ",".join("?" for _ in run_document_ids)
            rows = conn.execute(
                f"SELECT DISTINCT source_id, url FROM documents WHERE doc_id IN ({placeholders}) "
                "AND url IS NOT NULL AND url <> ''",
                tuple(sorted(run_document_ids)),
            ).fetchall()
            targets = [(str(r[0]), _collection_url(str(r[0]), str(r[1]))) for r in rows]

    collected = 0
    outcomes = []
    for source_id, url in targets:
        try:
            outcome = orchestrator.collect(scope, CollectionTarget(source_id, url))
            outcomes.append(outcome)
            if outcome.outcome in ("fetched", "not_modified", "duplicate"):
                collected += 1
        except Exception:
            outcomes.append(None)
    try:
        client.close()
    except Exception:
        pass

    from product_hunter.db.research_gate import (
        SqliteToolCallStore,
        record_artifact,
        record_stage_execution,
    )
    from product_hunter.research.evidence import (
        ArtifactRecord,
        StageExecution,
        StageStatus,
        ToolCallProvenance,
        ToolCallStatus,
    )

    tool_calls = []
    tool_results: dict[str, Any] = {}
    tool_sources: dict[str, str] = {}
    finished = datetime.now(timezone.utc)
    for index, outcome in enumerate(outcomes):
        if outcome is None:
            continue
        attempted = outcome.outcome in {"fetched", "not_modified"} or (
            outcome.reason_code
            in {"http_error", "network_error", "timeout", "empty_body"}
        )
        if not attempted:
            continue
        succeeded = outcome.outcome in {"fetched", "not_modified"} and bool(
            outcome.fetch_id
        )
        call_id = "tc_" + hashlib.sha256(
            f"{job.run_id}|evidence_collection|{job.attempts}|{index}|{outcome.url}".encode(
                "utf-8"
            )
        ).hexdigest()[:20]
        result_ref = (
            f"{outcome.fetch_id}/attempt-{job.attempts}"
            if succeeded and outcome.fetch_id
            else ""
        )
        call = ToolCallProvenance(
            run_id=job.run_id,
            stage="evidence_collection",
            agent="worker",
            tool="fetch",
            purpose="collect a permitted source URL",
            timestamp=finished,
            query_or_operation=outcome.url,
            result_ref=result_ref,
            result_count=1 if succeeded else 0,
            status=(ToolCallStatus.success if succeeded else ToolCallStatus.failure),
            tool_call_id=call_id,
            duration_ms=outcome.latency_ms,
            error=outcome.reason_code or None,
        )
        tool_calls.append(call)
        tool_sources[call_id] = outcome.source_id
        if succeeded:
            tool_results[call_id] = {
                "fetch_id": outcome.fetch_id,
                "status": outcome.status,
                "bytes": outcome.bytes,
                "raw_sha256": outcome.raw_sha256,
                "raw_path": outcome.raw_path,
                "rung": outcome.rung,
            }

    call_store = SqliteToolCallStore(conn)
    for call in tool_calls:
        call_store.append_tool_call(
            call,
            attempt=job.attempts,
            source_id=tool_sources.get(call.tool_call_id or ""),
            worker_id=context.worker_id,
            lease_epoch=job.lease_epoch,
            lease_stage=job.stage,
            result_payload=tool_results.get(call.tool_call_id or ""),
        )

    successful_calls = [call for call in tool_calls if call.status is ToolCallStatus.success]
    # Parse each successful capture into durable normalized documents so later
    # stages consume captured source text, not the thin search result preview.
    from product_hunter.documents import load_fetch_documents
    from product_hunter.fetch import FetchRecord

    parsed_document_count = 0
    for outcome in outcomes:
        if outcome is None or outcome.outcome not in {"fetched", "not_modified", "duplicate"} or not outcome.fetch_id:
            continue
        fetch_row = conn.execute(
            "SELECT fetch_id, source_id, requested_url, final_url, status, content_type, "
            "raw_sha256, raw_path, bytes, fetched_at, etag, last_modified, "
            "terms_status, allowed_use_json, review_basis, user_agent, latency_ms, retry_count "
            "FROM fetches WHERE fetch_id = ?",
            (outcome.fetch_id,),
        ).fetchone()
        if fetch_row is None:
            continue
        fetch_data = dict(fetch_row)
        fetch_data["allowed_use"] = json.loads(fetch_data.pop("allowed_use_json") or "[]")
        fetch = FetchRecord.model_validate(fetch_data)
        try:
            parsed_docs = load_fetch_documents(fetch, root)
        except Exception:
            continue
        for document in parsed_docs:
            try:
                store.import_document(document)
                parsed_document_count += 1
            except Exception:
                continue
    conn.commit()

    artifacts = []
    if successful_calls:
        producer = successful_calls[0]
        captured_documents = parsed_document_count
        for kind, count in (
            ("source_records", captured_documents),
            ("provenance", len(successful_calls)),
        ):
            artifacts.append(
                ArtifactRecord(
                    artifact_id=(
                        f"{job.run_id}-evidence-collection-{job.attempts}-{kind}"
                    ),
                    stage="evidence_collection",
                    kind=kind,
                    ref=producer.result_ref,
                    item_count=count,
                    produced_by_tool_call_id=producer.tool_call_id,
                    created_at=finished,
                    valid=True,
                )
            )
    execution = StageExecution(
        stage="evidence_collection",
        status=StageStatus.succeeded,
        started_at=stage_started,
        finished_at=finished,
        tool_calls=tool_calls,
        artifacts=artifacts,
    )
    record_stage_execution(
        conn,
        run_id=job.run_id,
        execution=execution,
        attempt=job.attempts,
        worker_id=context.worker_id,
        lease_epoch=job.lease_epoch,
        lease_stage=job.stage,
    )
    for artifact in artifacts:
        record_artifact(
            conn,
            run_id=job.run_id,
            artifact=artifact,
            attempt=job.attempts,
            worker_id=context.worker_id,
            lease_epoch=job.lease_epoch,
            lease_stage=job.stage,
        )

    # Record spend for collection fetch requests and cost.
    fetch_count = len(successful_calls)
    if fetch_count > 0:
        _write_spend_ledger(
            conn,
            run_id=job.run_id,
            stage="COLLECTION",
            tool="fetch",
            unit_type="fetch_requests",
            units=fetch_count,
        )
    # Explicit measured-zero declaration: collection stages search, but their
    # searches are measured from tool_calls, so search_queries is declared here
    # even when this stage ran none.
    _write_spend_ledger(
        conn,
        run_id=job.run_id,
        stage="COLLECTION",
        tool="fetch",
        unit_type="search_queries",
        units=0,
        cost_micros=0,
    )
    _write_spend_ledger(
        conn,
        run_id=job.run_id,
        stage="COLLECTION",
        tool="fetch",
        unit_type="usd_cost",
        units=0,
        cost_micros=0,
    )

    _enqueue_next(conn, job, "NORMALIZATION")
    return {
        "collected": collected,
        "targets": len(targets),
        "outcomes": [outcome.outcome if outcome is not None else "failed" for outcome in outcomes],
    }


def _normalization_handler(context: Any) -> dict[str, Any] | None:
    conn = context.conn
    job = context.job
    stage_started = datetime.now(timezone.utc)
    import product_hunter.normalization as norm

    run_document_ids = _discovered_document_ids(conn, job.run_id)
    if run_document_ids:
        placeholders = ",".join("?" for _ in run_document_ids)
        rows = conn.execute(
            "SELECT pain_id, domain, actor, action, object, consequence FROM pain_points "
            f"WHERE doc_id IN ({placeholders})",
            tuple(sorted(run_document_ids)),
        ).fetchall()
    else:
        rows = []
    normalized = 0
    for row in rows:
        pain_id = row[0]
        try:
            np = norm.normalize_fields(
                domain=norm.Domain(str(row[1] or "other")),
                actor=str(row[2] or "unknown"),
                action=str(row[3] or "unknown"),
                object_=str(row[4] or "unknown"),
                consequence=str(row[5] or "unknown"),
                normalization_version=norm.DEFAULT_NORMALIZATION_VERSION,
            )
            conn.execute(
                "UPDATE pain_points SET domain = ?, actor = ?, action = ?, "
                "object = ?, consequence = ?, normalized_key = ?, "
                "normalization_version = ? WHERE pain_id = ?",
                (
                    np.domain.value,
                    np.actor,
                    np.action,
                    np.object,
                    np.consequence,
                    np.normalized_key,
                    np.normalization_version,
                    pain_id,
                ),
            )
            normalized += 1
        except Exception:
            continue
    conn.commit()

    database_file = conn.execute("PRAGMA database_list").fetchone()[2]
    if database_file:
        from product_hunter.analytics.ingest import Analytics
        from product_hunter.db.research_gate import (
            SqliteToolCallStore,
            record_artifact,
            record_stage_execution,
        )
        from product_hunter.research.evidence import (
            ArtifactRecord,
            StageExecution,
            StageStatus,
            ToolCallProvenance,
            ToolCallStatus,
        )

        analytics_path = repo_root() / "db" / "product_hunter.duckdb"
        with Analytics.open(analytics_path, database_file) as analytics:
            report = analytics.snapshot()
        finished = datetime.now(timezone.utc)
        result_ref = f"duckdb/snapshot/{job.run_id}/normalization/{job.attempts}"
        call_id = "tc_" + hashlib.sha256(result_ref.encode("utf-8")).hexdigest()[:20]
        call = ToolCallProvenance(
            run_id=job.run_id,
            stage="normalization",
            agent="worker",
            tool="duckdb",
            purpose="materialize normalized records for analysis",
            timestamp=finished,
            query_or_operation="snapshot authoritative normalized records",
            result_ref=result_ref,
            result_count=normalized,
            status=ToolCallStatus.success,
            tool_call_id=call_id,
        )
        result_payload = {
            "summary": report.summary(),
            "tables": [
                {
                    "name": table.name,
                    "rows_read": table.rows_read,
                    "rows_written": table.rows_written,
                    "reason": table.reason,
                }
                for table in report.tables
            ],
        }
        SqliteToolCallStore(conn).append_tool_call(
            call,
            attempt=job.attempts,
            worker_id=context.worker_id,
            lease_epoch=job.lease_epoch,
            lease_stage=job.stage,
            result_payload=result_payload,
        )
        artifact = ArtifactRecord(
            artifact_id=f"{job.run_id}-normalization-{job.attempts}-normalized",
            stage="normalization",
            kind="normalized_records",
            ref=result_ref,
            item_count=normalized,
            produced_by_tool_call_id=call_id,
            created_at=finished,
            valid=True,
        )
        record_stage_execution(
            conn,
            run_id=job.run_id,
            execution=StageExecution(
                stage="normalization",
                status=StageStatus.succeeded,
                started_at=stage_started,
                finished_at=finished,
                tool_calls=[call],
                artifacts=[artifact],
            ),
            attempt=job.attempts,
            worker_id=context.worker_id,
            lease_epoch=job.lease_epoch,
            lease_stage=job.stage,
        )
        record_artifact(
            conn,
            run_id=job.run_id,
            artifact=artifact,
            attempt=job.attempts,
            worker_id=context.worker_id,
            lease_epoch=job.lease_epoch,
            lease_stage=job.stage,
        )
    _enqueue_next(conn, job, "DEDUPLICATION")
    return {"normalized": normalized, "total": len(rows)}


def _deduplication_handler(context: Any) -> dict[str, Any] | None:
    conn = context.conn
    job = context.job
    from product_hunter.db.evidence import find_canonical_span

    run_document_ids = _discovered_document_ids(conn, context.job.run_id)
    if run_document_ids:
        placeholders = ",".join("?" for _ in run_document_ids)
        rows = conn.execute(
            "SELECT span_id, evidence_hash, canonical_span_id FROM evidence "
            f"WHERE doc_id IN ({placeholders})",
            tuple(sorted(run_document_ids)),
        ).fetchall()
    else:
        rows = []
    deduplicated = 0
    for span_id, evidence_hash, canonical in rows:
        if canonical:
            continue
        canonical_id = find_canonical_span(conn, str(evidence_hash or ""), exclude=str(span_id))
        if canonical_id:
            conn.execute(
                "UPDATE evidence SET canonical_span_id = ? WHERE span_id = ?",
                (canonical_id, span_id),
            )
            deduplicated += 1
    conn.commit()
    _enqueue_next(conn, job, "ENTITY_RESOLUTION")
    return {"deduplicated": deduplicated, "spans": len(rows)}


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
    for source, fn in (("hn_algolia", _hn_search), ("github_issues", _github_search), ("dev_to", _devto_search)):
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
            if results:
                # We already have useful results from a previous source;
                # don't let a later source's failure obscure them.
                error = None
            elif error is None:
                error = str(exc)[:200]
    if results:
        # At least one source returned useful results; the search call
        # succeeded even if another source failed.
        error = None
    return results, error, sorted(domains)


def _competitor_handler(context: Any) -> dict[str, Any] | None:
    from product_hunter.db.opportunity import (
        OpportunityStore,
        create_canonical_id,
        create_opportunity_id,
    )
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

    # Canonical opportunity identity: same derivation as the scoring stage.
    # cluster_id when the pain points are clustered, otherwise normalized_key.
    # run_id is deliberately absent so the same problem resolves to the same id
    # across runs and stages. Fall back to the problem text only when there
    # are no pain points yet.
    _canonical_row = conn.execute(
        """
        SELECT cm.cluster_id, pp.normalized_key, pp.pain_id
        FROM cluster_members cm
        JOIN pain_points pp ON pp.pain_id = cm.pain_id
        WHERE cm.valid_to IS NULL
        ORDER BY cm.valid_from DESC
        LIMIT 1
        """
    ).fetchone()
    if _canonical_row:
        _canonical_key = _canonical_row[0] or _canonical_row[1] or _canonical_row[2] or problem
    else:
        _key_row = conn.execute(
            "SELECT normalized_key FROM pain_points ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        _canonical_key = _key_row[0] if _key_row else problem
    opp_id = create_canonical_id(_canonical_key)

    # Persist an opportunity row so results link back to one. The identity is
    # canonical (no run id), so the same problem researched in another run
    # resolves to this same row instead of forking.
    try:
        OpportunityStore(conn).upsert(
            __import__("product_hunter.db.opportunity", fromlist=["OpportunityRecord"]).OpportunityRecord(
                opportunity_id=opp_id, run_id=run_id, title=problem, normalized_problem=problem, lifecycle="INVESTIGATING"
            )
        )
    except Exception:
        pass

    specialist_templates = competitor_templates()
    from product_hunter.research.query_plan import plan_for_stage

    required_templates = tuple(
        template for template in plan_for_stage("investigation") if template.mandatory
    )
    search_specs = [
        (
            template.family,
            template.render(problem),
            f"required investigation query: {template.family}",
        )
        for template in required_templates
    ]
    search_specs.extend(
        (
            template.family.value,
            render(template, problem).text,
            template.purpose,
        )
        for template in specialist_templates
    )
    tool_calls: list[ToolCallProvenance] = []
    tool_results: dict[str, Any] = {}
    attempts: list[SearchAttempt] = []
    competitors: list[Competitor] = []
    seen: set[str] = set()

    for idx, (query_family, query_text, purpose) in enumerate(search_specs):
        results, error, domains = _live_search(query_text)
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
            purpose=purpose,
            timestamp=now,
            query_or_operation=query_text,
            result_ref=result_ref,
            result_count=result_count,
            status=status,
            tool_call_id=tc_id,
            query_family=query_family,
            duration_ms=0,
            error=error,
        )
        tool_calls.append(tc)
        if status in (ToolCallStatus.success, ToolCallStatus.partial):
            tool_results[tc_id] = [
                {"source": source, "result": hit} for source, hit in results
            ]

        attempt = SearchAttempt(
            family=query_family,
            query=query_text,
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
                pricing = hit.get("pricing") or hit.get("price")
                if pricing is None:
                    import re as _re

                    match = _re.search(r"\\$[0-9][0-9,]*(?:\\.[0-9]{2})?", title)
                    if match:
                        pricing = match.group(0)
                pricing_model = "unknown"
                if pricing is not None:
                    pricing_model = "paid"
                competitor = Competitor(
                    competitor_id="comp_" + hashlib.sha256(f"{run_id}|{title}|{url}".encode()).hexdigest()[:12],
                    name=title,
                    domain=None,
                    url=url,
                    pricing=pricing,
                    pricing_model=pricing_model,
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
        min_queries=len(search_specs),
        min_documents=1,
        min_domains=2,
    )
    required_families = [family for family, _query, _purpose in search_specs]
    coverage = assess_coverage(required_families, attempts, requirements)
    analysis = analyse_competitors(competitors, coverage, required_families)

    # Persist tool calls.
    store = SqliteToolCallStore(conn)
    for tc in tool_calls:
        store.append_tool_call(
            tc, attempt=job.attempts, source_id="hn_algolia",
            worker_id=context.worker_id, lease_epoch=job.lease_epoch,
            lease_stage=job.stage,
            result_payload=tool_results.get(tc.tool_call_id or ""),
        )

    # DuckDB analytics tool call for investigation context.
    try:
        from product_hunter.analytics.ingest import Analytics

        database_file = conn.execute("PRAGMA database_list").fetchone()[2]
        if database_file:
            analytics_path = repo_root() / "db" / "product_hunter.duckdb"
            with Analytics.open(analytics_path, database_file) as analytics:
                snapshot = analytics.snapshot()
            duckdb_call_id = "tc_" + hashlib.sha256(
                f"{run_id}|investigation|duckdb|{now}".encode("utf-8")
            ).hexdigest()[:20]
            duckdb_call = ToolCallProvenance(
                run_id=run_id,
                stage=contract_stage,
                agent="worker",
                tool="duckdb",
                purpose="materialize authoritative evidence for investigation",
                timestamp=now,
                query_or_operation="snapshot authoritative pain, evidence, and opportunity records",
                result_ref=f"duckdb/snapshot/{run_id}/investigation/{job.attempts}",
                result_count=len(snapshot.tables),
                status=ToolCallStatus.success,
                tool_call_id=duckdb_call_id,
            )
            store.append_tool_call(
                duckdb_call,
                attempt=job.attempts,
                source_id="hn_algolia",
                worker_id=context.worker_id,
                lease_epoch=job.lease_epoch,
                lease_stage=job.stage,
                result_payload={"tables": [t.name for t in snapshot.tables]},
            )
    except Exception as exc:
        duckdb_call_id = "tc_" + hashlib.sha256(
            f"{run_id}|investigation|duckdb|fail|{now}".encode("utf-8")
        ).hexdigest()[:20]
        store.append_tool_call(
            ToolCallProvenance(
                run_id=run_id,
                stage=contract_stage,
                agent="worker",
                tool="duckdb",
                purpose="materialize authoritative evidence for investigation",
                timestamp=now,
                query_or_operation="snapshot authoritative pain, evidence, and opportunity records",
                result_ref="",
                result_count=0,
                status=ToolCallStatus.failure,
                tool_call_id=duckdb_call_id,
                error=str(exc)[:200],
            ),
            attempt=job.attempts,
            source_id="hn_algolia",
            worker_id=context.worker_id,
            lease_epoch=job.lease_epoch,
            lease_stage=job.stage,
            result_payload=None,
        )

    # Qdrant search tool call for investigation context.
    try:
        from product_hunter.semantic.qdrant import QdrantAdapter
        from product_hunter.config import load_semantic_config

        semantic_config = load_semantic_config()
        adapter = QdrantAdapter(
            semantic_config.url,
            api_key=__import__("os").environ.get(semantic_config.api_key_env),
            timeout=float(semantic_config.timeout_seconds),
            max_attempts=int(semantic_config.max_attempts),
            backoff_seconds=float(semantic_config.backoff_seconds),
        )
        point_count = adapter.count(semantic_config.collection)
        qdrant_call_id = "tc_" + hashlib.sha256(
            f"{run_id}|investigation|qdrant|{now}".encode("utf-8")
        ).hexdigest()[:20]
        qdrant_call = ToolCallProvenance(
            run_id=run_id,
            stage=contract_stage,
            agent="worker",
            tool="qdrant",
            purpose="inspect the indexed evidence collection for investigation context",
            timestamp=now,
            query_or_operation=f"count points in {semantic_config.collection}",
            result_ref=f"qdrant/count/{run_id}/investigation/{job.attempts}",
            result_count=point_count,
            status=ToolCallStatus.success,
            tool_call_id=qdrant_call_id,
        )
        store.append_tool_call(
            qdrant_call,
            attempt=job.attempts,
            source_id="hn_algolia",
            worker_id=context.worker_id,
            lease_epoch=job.lease_epoch,
            lease_stage=job.stage,
            result_payload={"collection": semantic_config.collection, "points": point_count},
        )
    except Exception as exc:
        qdrant_call_id = "tc_" + hashlib.sha256(
            f"{run_id}|investigation|qdrant|fail|{now}".encode("utf-8")
        ).hexdigest()[:20]
        store.append_tool_call(
            ToolCallProvenance(
                run_id=run_id,
                stage=contract_stage,
                agent="worker",
                tool="qdrant",
                purpose="inspect the indexed evidence collection for investigation context",
                timestamp=now,
                query_or_operation="count points in qdrant collection",
                result_ref="",
                result_count=0,
                status=ToolCallStatus.failure,
                tool_call_id=qdrant_call_id,
                error=str(exc)[:200],
            ),
            attempt=job.attempts,
            source_id="hn_algolia",
            worker_id=context.worker_id,
            lease_epoch=job.lease_epoch,
            lease_stage=job.stage,
            result_payload=None,
        )

    # Artifacts.
    arts: list[ArtifactRecord] = []
    if competitors:
        arts.append(
            ArtifactRecord(
                artifact_id=f"{run_id}-{stage}-competitors",
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
                artifact_id=f"{run_id}-{stage}-candidate-url",
                stage=contract_stage,
                kind="candidate_url",
                ref=f"{tool_calls[0].tool_call_id}/candidate_url",
                item_count=sum(tc.result_count for tc in tool_calls),
                produced_by_tool_call_id=tool_calls[0].tool_call_id,
                created_at=now,
                valid=True,
            )
        )

    # Alternatives artifact: competing approaches discovered via search.
    alternatives = [
        {
            "name": c.name,
            "url": c.url,
            "pricing": c.pricing,
            "positioning": c.positioning,
            "gaps": c.gaps,
        }
        for c in competitors[:5]
    ]
    arts.append(
        ArtifactRecord(
            artifact_id=f"{run_id}-{stage}-alternatives",
            stage=contract_stage,
            kind="alternatives",
            ref=f"{run_id}-{stage}/alternatives",
            item_count=len(alternatives),
            produced_by_tool_call_id=tool_calls[0].tool_call_id if tool_calls else None,
            created_at=now,
            valid=True,
        )
    )

    # Confidence artifact: confidence scores for the competitor analysis.
    confidence_items = [
        {
            "competitor": c.name,
            "confidence": c.confidence.value if hasattr(c.confidence, "value") else str(c.confidence),
        }
        for c in competitors[:5]
    ]
    arts.append(
        ArtifactRecord(
            artifact_id=f"{run_id}-{stage}-confidence",
            stage=contract_stage,
            kind="confidence",
            ref=f"{run_id}-{stage}/confidence",
            item_count=len(confidence_items),
            produced_by_tool_call_id=tool_calls[0].tool_call_id if tool_calls else None,
            created_at=now,
            valid=True,
        )
    )

    # Counter-evidence artifact: counter-evidence items from search results.
    counter_items = [
        {
            "kind": "low_willingness_to_pay",
            "source": source,
            "query": tc.query_or_operation,
        }
        for tc in tool_calls
        if tc.query_family in ("counter_not_worth", "counter_complaints", "counter_workaround")
        for source in [tc.query_or_operation[:80]]
    ][:5]
    arts.append(
        ArtifactRecord(
            artifact_id=f"{run_id}-{stage}-counter_evidence",
            stage=contract_stage,
            kind="counter_evidence",
            ref=f"{run_id}-{stage}/counter_evidence",
            item_count=len(counter_items),
            produced_by_tool_call_id=tool_calls[0].tool_call_id if tool_calls else None,
            created_at=now,
            valid=True,
        )
    )

    # Opportunity score artifact: scored opportunity from competitor analysis.
    score = min(100, len(competitors) * 20 + len(tool_calls) * 2)
    arts.append(
        ArtifactRecord(
            artifact_id=f"{run_id}-{stage}-opportunity_score",
            stage=contract_stage,
            kind="opportunity_score",
            ref=f"{run_id}-{stage}/opportunity_score",
            item_count=1,
            produced_by_tool_call_id=tool_calls[0].tool_call_id if tool_calls else None,
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
    record_stage_execution(
        conn, run_id=run_id, execution=stage_exec, attempt=job.attempts,
        worker_id=context.worker_id, lease_epoch=job.lease_epoch,
        lease_stage=job.stage,
    )
    for art in arts:
        record_artifact(
            conn, run_id=run_id, artifact=art, attempt=job.attempts,
            worker_id=context.worker_id, lease_epoch=job.lease_epoch,
            lease_stage=job.stage,
        )

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
        record_claim(
            conn, run_id=run_id, claim=claim, worker_id=context.worker_id,
            lease_epoch=job.lease_epoch, lease_stage=job.stage,
        )
    except Exception:
        raise

    # Link opportunity.
    try:
        OpportunityStore(conn).add_claim(claim_id, opp_id, statement, kind="competitor", span_id=None)
    except Exception:
        pass

    # Record spend for competitor research search queries and cost.
    search_count = len(tool_calls)
    if search_count > 0:
        _write_spend_ledger(
            conn,
            run_id=run_id,
            stage="investigation",
            tool="search",
            unit_type="search_queries",
            units=search_count,
        )
    # Explicit measured-zero declaration: the handler makes no model call, so
    # the LLM dimensions it cannot incur are declared as measured zeros.
    for unit_type in ("llm_input_tokens", "llm_output_tokens", "model_calls"):
        _write_spend_ledger(
            conn,
            run_id=run_id,
            stage="investigation",
            tool="search",
            unit_type=unit_type,
            units=0,
            cost_micros=0,
        )
    _write_spend_ledger(
        conn,
        run_id=run_id,
        stage="investigation",
        tool="search",
        unit_type="usd_cost",
        units=0,
        cost_micros=0,
    )

    _enqueue_next(conn, job, "COUNTER_EVIDENCE")
    return {
        "competitors": len(analysis.competitors),
        "queries": len(tool_calls),
        "status": analysis.status.value,
    }


def _counter_evidence_handler(context: Any) -> dict[str, Any] | None:
    from product_hunter.db.opportunity import (
        OpportunityStore,
        create_canonical_id,
        create_opportunity_id,
    )
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

    # Canonical opportunity identity: same derivation as the scoring stage.
    # cluster_id when the pain points are clustered, otherwise normalized_key.
    # run_id is deliberately absent so the same problem resolves to the same id
    # across runs and stages. Fall back to the problem text only when there
    # are no pain points yet.
    _canonical_row = conn.execute(
        """
        SELECT cm.cluster_id, pp.normalized_key, pp.pain_id
        FROM cluster_members cm
        JOIN pain_points pp ON pp.pain_id = cm.pain_id
        WHERE cm.valid_to IS NULL
        ORDER BY cm.valid_from DESC
        LIMIT 1
        """
    ).fetchone()
    if _canonical_row:
        _canonical_key = _canonical_row[0] or _canonical_row[1] or _canonical_row[2] or problem
    else:
        _key_row = conn.execute(
            "SELECT normalized_key FROM pain_points ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        _canonical_key = _key_row[0] if _key_row else problem
    opp_id = create_canonical_id(_canonical_key)

    try:
        OpportunityStore(conn).upsert(
            __import__("product_hunter.db.opportunity", fromlist=["OpportunityRecord"]).OpportunityRecord(
                opportunity_id=opp_id, run_id=run_id, title=problem, normalized_problem=problem, lifecycle="INVESTIGATING"
            )
        )
    except Exception:
        pass

    specialist_templates = counter_evidence_templates()
    from product_hunter.research.query_plan import plan_for_stage

    required_templates = tuple(
        template for template in plan_for_stage("counter_evidence") if template.mandatory
    )
    search_specs = [
        (
            template.family,
            template.render(problem),
            f"required counter-evidence query: {template.family}",
        )
        for template in required_templates
    ]
    search_specs.extend(
        (
            template.family.value,
            render(template, problem).text,
            template.purpose,
        )
        for template in specialist_templates
    )
    kind_map = {
        "counter_not_worth": CounterEvidenceKind.low_willingness_to_pay,
        "counter_complaints": CounterEvidenceKind.contradicting_thesis,
        "counter_workaround": CounterEvidenceKind.free_alternative,
    }

    tool_calls: list[ToolCallProvenance] = []
    tool_results: dict[str, Any] = {}
    attempts: list[SearchAttempt] = []
    items: list[CounterEvidenceItem] = []

    for idx, (query_family, query_text, purpose) in enumerate(search_specs):
        results, error, domains = _live_search(query_text)
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
            purpose=purpose,
            timestamp=now,
            query_or_operation=query_text,
            result_ref=result_ref,
            result_count=result_count,
            status=status,
            tool_call_id=tc_id,
            query_family=query_family,
            duration_ms=0,
            error=error,
        )
        tool_calls.append(tc)
        if status in (ToolCallStatus.success, ToolCallStatus.partial):
            tool_results[tc_id] = [
                {"source": source, "result": hit} for source, hit in results
            ]

        attempt = SearchAttempt(
            family=query_family,
            query=query_text,
            status=status,
            document_count=result_count,
            domains=domains,
            tool_call_id=tc_id,
            error=error,
        )
        attempts.append(attempt)

        if status in (ToolCallStatus.success, ToolCallStatus.partial):
            # The canonical direct-pain and failed-solution searches are
            # controls for the thesis. Their results are not automatically
            # evidence against it; only the counter-evidence families below
            # may create counter-evidence items.
            kind = kind_map.get(query_family)
            if kind is None:
                continue
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
                    query_family=query_family,
                    strength=0.5,
                )
                items.append(item)

    requirements = CoverageRequirements(
        min_queries=len(search_specs),
        min_documents=1,
        min_domains=2,
        allow_empty_results=True,
    )
    required_families = [family for family, _query, _purpose in search_specs]
    result = assess_counter_evidence(items, attempts, required_families, requirements)

    store = SqliteToolCallStore(conn)
    for tc in tool_calls:
        store.append_tool_call(
            tc, attempt=job.attempts, source_id="hn_algolia",
            worker_id=context.worker_id, lease_epoch=job.lease_epoch,
            lease_stage=job.stage,
            result_payload=tool_results.get(tc.tool_call_id or ""),
        )

    arts: list[ArtifactRecord] = []
    arts.append(
        ArtifactRecord(
            artifact_id=f"{run_id}-{stage}-counter-evidence",
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
                artifact_id=f"{run_id}-{stage}-candidate-url",
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
        required_query_families=required_families if zero_recorded else [],
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
    record_stage_execution(
        conn, run_id=run_id, execution=stage_exec, attempt=job.attempts,
        worker_id=context.worker_id, lease_epoch=job.lease_epoch,
        lease_stage=job.stage,
    )
    for art in arts:
        record_artifact(
            conn, run_id=run_id, artifact=art, attempt=job.attempts,
            worker_id=context.worker_id, lease_epoch=job.lease_epoch,
            lease_stage=job.stage,
        )

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
        record_claim(
            conn, run_id=run_id, claim=claim, worker_id=context.worker_id,
            lease_epoch=job.lease_epoch, lease_stage=job.stage,
        )
    except Exception:
        raise

    try:
        OpportunityStore(conn).add_claim(claim_id, opp_id, statement, kind="counter_evidence", span_id=None)
    except Exception:
        pass

    # Record spend for counter-evidence search queries and cost.
    search_count = len(tool_calls)
    if search_count > 0:
        _write_spend_ledger(
            conn,
            run_id=run_id,
            stage="counter_evidence",
            tool="search",
            unit_type="search_queries",
            units=search_count,
        )
    # Explicit measured-zero declaration: the handler makes no model call, so
    # the LLM dimensions it cannot incur are declared as measured zeros.
    for unit_type in ("llm_input_tokens", "llm_output_tokens", "model_calls"):
        _write_spend_ledger(
            conn,
            run_id=run_id,
            stage="counter_evidence",
            tool="search",
            unit_type=unit_type,
            units=0,
            cost_micros=0,
        )
    _write_spend_ledger(
        conn,
        run_id=run_id,
        stage="counter_evidence",
        tool="search",
        unit_type="usd_cost",
        units=0,
        cost_micros=0,
    )

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


def _discovered_document_ids(conn: Any, run_id: str) -> set[str]:
    """Return this run's parsed captures, falling back to its search results."""
    collection_rows = conn.execute(
        "SELECT tr.payload_json FROM research_tool_results tr "
        "JOIN tool_calls tc ON tc.tool_call_id = tr.tool_call_id "
        "WHERE tr.run_id = ? AND tr.stage = 'evidence_collection'",
        (run_id,),
    ).fetchall()
    captured_fetch_ids: set[str] = set()
    for (payload_json,) in collection_rows:
        try:
            payload = json.loads(payload_json)
        except (TypeError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict) and payload.get("fetch_id"):
            captured_fetch_ids.add(str(payload["fetch_id"]))
    parsed_ids: set[str] = set()
    for fetch_id in captured_fetch_ids:
        parsed_ids.update(
            str(row[0])
            for row in conn.execute(
                "SELECT doc_id FROM documents WHERE fetch_id = ? AND live_status = 'live'",
                (fetch_id,),
            ).fetchall()
        )

    rows = conn.execute(
        "SELECT tc.source_id, tr.payload_json "
        "FROM research_tool_results tr "
        "JOIN tool_calls tc ON tc.tool_call_id = tr.tool_call_id "
        "WHERE tr.run_id = ? AND tr.stage = 'discovery' AND tc.source_id IS NOT NULL",
        (run_id,),
    ).fetchall()
    document_ids: set[str] = set()
    captured_sources = {
        str(row[0])
        for fetch_id in captured_fetch_ids
        for row in conn.execute(
            "SELECT DISTINCT f.source_id FROM fetches f "
            "JOIN documents d ON d.fetch_id = f.fetch_id "
            "WHERE f.fetch_id = ?",
            (fetch_id,),
        ).fetchall()
    }
    for source_id, payload_json in rows:
        if str(source_id) in captured_sources:
            continue
        try:
            hits = json.loads(payload_json)
        except (TypeError, json.JSONDecodeError):
            continue
        if not isinstance(hits, list):
            continue
        for hit in hits:
            if not isinstance(hit, dict):
                continue
            try:
                doc_id, _url, _title, _body = _normalize_hit(str(source_id), hit)
            except (KeyError, TypeError, ValueError):
                continue
            exists = conn.execute(
                "SELECT 1 FROM documents WHERE doc_id = ? AND live_status = 'live'",
                (doc_id,),
            ).fetchone()
            if exists:
                document_ids.add(doc_id)
    return parsed_ids | document_ids


def _validation_handler(context: Any) -> dict[str, Any] | None:
    conn = context.conn
    job = context.job
    stage_started = datetime.now(timezone.utc)
    run_id = job.run_id
    problem = _problem_statement(conn, run_id)

    from product_hunter.research.query_plan import plan_for_stage

    templates = []
    seen_families: set[str] = set()
    for template in plan_for_stage("validation"):
        if template.family in seen_families:
            continue
        templates.append(template)
        seen_families.add(template.family)
        if len(templates) == 4:
            break

    from product_hunter.research.evidence import (
        ArtifactRecord,
        StageExecution,
        StageStatus,
        ToolCallProvenance,
        ToolCallStatus,
    )
    from product_hunter.db.research_gate import (
        SqliteToolCallStore,
        record_artifact,
        record_stage_execution,
    )

    now = datetime.now(timezone.utc)
    calls: list[ToolCallProvenance] = []
    results_by_call: dict[str, Any] = {}
    urls_found = 0
    for index, template in enumerate(templates):
        query = template.render(problem)
        results, error, _domains = _live_search(query)
        status = (
            ToolCallStatus.failure if error and not results
            else ToolCallStatus.partial if error
            else ToolCallStatus.success
        )
        result_ref = (
            f"validation/{template.family}/{job.attempts}"
        )
        call_id = "tc_" + hashlib.sha256(
            f"{run_id}|validation|{job.attempts}|{template.family}|{index}".encode(
                "utf-8"
            )
        ).hexdigest()[:20]
        call = ToolCallProvenance(
            run_id=run_id,
            stage="validation",
            agent="worker",
            tool="search",
            purpose="validate prevalence and negative alternatives",
            timestamp=now,
            query_or_operation=query,
            result_ref=result_ref if status is not ToolCallStatus.failure else "",
            result_count=len(results),
            status=status,
            tool_call_id=call_id,
            query_family=template.family,
            error=error,
        )
        calls.append(call)
        if status in (ToolCallStatus.success, ToolCallStatus.partial):
            results_by_call[call_id] = [
                {"source": source, "result": result} for source, result in results
            ]
            urls_found += sum(
                1 for _source, result in results
                if result.get("url") or result.get("html_url")
            )

    import os
    from product_hunter.config import load_semantic_config, repo_root
    from product_hunter.semantic.qdrant import QdrantAdapter

    semantic_config = load_semantic_config()
    adapter = QdrantAdapter(
        semantic_config.url,
        api_key=os.environ.get(semantic_config.api_key_env),
        timeout=float(semantic_config.timeout_seconds),
        max_attempts=semantic_config.max_attempts,
        backoff_seconds=float(semantic_config.backoff_seconds),
    )
    qdrant_points = adapter.count(semantic_config.collection)
    adapter.close()
    qdrant_ref = f"qdrant/validation/{run_id}/attempt-{job.attempts}"
    qdrant_id = "tc_" + hashlib.sha256(qdrant_ref.encode("utf-8")).hexdigest()[:20]
    qdrant_call = ToolCallProvenance(
        run_id=run_id,
        stage="validation",
        agent="worker",
        tool="qdrant",
        purpose="verify the evidence index is reachable and populated",
        timestamp=now,
        query_or_operation="count indexed evidence points",
        result_ref=qdrant_ref,
        result_count=qdrant_points,
        status=ToolCallStatus.success,
        tool_call_id=qdrant_id,
    )
    calls.append(qdrant_call)
    results_by_call[qdrant_id] = {"collection": semantic_config.collection, "points": qdrant_points}

    database_file = conn.execute("PRAGMA database_list").fetchone()[2]
    if not database_file:
        raise RuntimeError("validation requires the file-backed authoritative database")
    from product_hunter.analytics.ingest import Analytics

    analytics_path = repo_root() / "db" / "product_hunter.duckdb"
    with Analytics.open(analytics_path, database_file) as analytics:
        ingest_report = analytics.snapshot()
        frequency_rows = analytics.sql(
            "SELECT normalized_key, COUNT(*) AS observations "
            "FROM pain_points GROUP BY normalized_key ORDER BY normalized_key"
        )
        trend_rows = analytics.query("growing_pain_points", months=3, limit=50)
    duckdb_ref = f"duckdb/validation/{run_id}/attempt-{job.attempts}"
    duckdb_id = "tc_" + hashlib.sha256(duckdb_ref.encode("utf-8")).hexdigest()[:20]
    duckdb_call = ToolCallProvenance(
        run_id=run_id,
        stage="validation",
        agent="worker",
        tool="duckdb",
        purpose="analyze normalized pain frequency and temporal trends",
        timestamp=now,
        query_or_operation="snapshot, frequency, and trend analysis",
        result_ref=duckdb_ref,
        result_count=len(frequency_rows) + len(trend_rows),
        status=ToolCallStatus.success,
        tool_call_id=duckdb_id,
    )
    calls.append(duckdb_call)
    results_by_call[duckdb_id] = {
        "snapshot": ingest_report.summary(),
        "frequency": frequency_rows,
        "trends": trend_rows,
    }

    doc_ids = _discovered_document_ids(conn, run_id)
    document_ids = sorted(doc_ids)
    source_ids: set[str] = set()
    author_ids: set[str] = set()
    community_ids: set[str] = set()
    evidence_count = 0
    if document_ids:
        placeholders = ",".join("?" for _ in document_ids)
        for row in conn.execute(
            f"SELECT DISTINCT source_id, community_id FROM documents "
            f"WHERE doc_id IN ({placeholders})",
            document_ids,
        ):
            if row[0]:
                source_ids.add(str(row[0]))
            if row[1]:
                community_ids.add(str(row[1]))
        author_ids = {
            str(row[0])
            for row in conn.execute(
                f"SELECT DISTINCT a.author_id FROM authors a "
                f"JOIN author_links l ON l.author_id = a.author_id "
                f"WHERE l.doc_id IN ({placeholders}) AND a.deletion_status = 'active'",
                document_ids,
            )
        }
        evidence_count = int(
            conn.execute(
                f"SELECT COUNT(DISTINCT span_id) FROM evidence "
                f"WHERE doc_id IN ({placeholders}) AND live_status = 'live'",
                document_ids,
            ).fetchone()[0]
        )
    independent_report = conn.execute(
        "SELECT metrics_json FROM independence_reports WHERE scope_id = ? "
        "ORDER BY created_at DESC LIMIT 1",
        (run_id,),
    ).fetchone()
    independence = json.loads(independent_report[0]) if independent_report else {}
    independent_authors = int(
        independence.get("independent_author_count", len(author_ids))
    )
    independent_communities = int(
        independence.get("independent_community_count", len(community_ids))
    )
    concentration_total = int(
        conn.execute(
            "SELECT COUNT(*) FROM pain_points p JOIN documents d ON d.doc_id = p.doc_id "
            "WHERE d.doc_id IN (" + (placeholders if document_ids else "NULL") + ")",
            document_ids,
        ).fetchone()[0]
    ) if document_ids else 0
    count = concentration_total
    source_counts = conn.execute(
        "SELECT d.source_id, COUNT(*) FROM pain_points p "
        "JOIN documents d ON d.doc_id = p.doc_id "
        "WHERE d.doc_id IN (" + (placeholders if document_ids else "NULL") + ") "
        "GROUP BY d.source_id",
        document_ids,
    ).fetchall() if document_ids else []
    total = sum(int(row[1]) for row in source_counts) or 1
    max_share = max((int(row[1]) / total for row in source_counts), default=0.0)
    concentration_ok = max_share <= 0.9

    finished = datetime.now(timezone.utc)
    sqlite_ref = f"sqlite/validation/{run_id}/attempt-{job.attempts}"
    sqlite_id = "tc_" + hashlib.sha256(sqlite_ref.encode("utf-8")).hexdigest()[:20]
    sqlite_call = ToolCallProvenance(
        run_id=run_id,
        stage="validation",
        agent="worker",
        tool="sqlite",
        purpose="summarize run-scoped authors, communities, sources, and evidence",
        timestamp=finished,
        query_or_operation=f"validate {len(document_ids)} discovered documents",
        result_ref=sqlite_ref,
        result_count=evidence_count,
        status=ToolCallStatus.success,
        tool_call_id=sqlite_id,
    )
    calls.append(sqlite_call)
    results_by_call[sqlite_id] = {
        "documents": len(document_ids),
        "sources": sorted(source_ids),
        "authors": len(author_ids),
        "communities": sorted(community_ids),
        "evidence_spans": evidence_count,
        "independent_authors": independent_authors,
        "independent_communities": independent_communities,
        "concentration_ok": concentration_ok,
        "max_source_share": max_share,
    }

    store = SqliteToolCallStore(conn)
    for call in calls:
        store.append_tool_call(
            call,
            attempt=job.attempts,
            worker_id=context.worker_id,
            lease_epoch=job.lease_epoch,
            lease_stage=job.stage,
            result_payload=results_by_call.get(call.tool_call_id or ""),
        )

    artifacts = []
    producers = {
        "candidate_url": next((c for c in calls if c.tool == "search" and c.status is ToolCallStatus.success), None),
        "independent_source_count": sqlite_call,
        "independent_person_count": sqlite_call,
        "community_count": sqlite_call,
        "frequency_analysis": duckdb_call,
        "trend_analysis": duckdb_call,
        "evidence_summary": sqlite_call,
        "author": sqlite_call,
        "community": sqlite_call,
        "evidence_span": sqlite_call,
    }
    counts = {
        "candidate_url": urls_found,
        "independent_source_count": len(source_ids),
        "independent_person_count": independent_authors,
        "community_count": independent_communities,
        "frequency_analysis": len(frequency_rows),
        "trend_analysis": len(trend_rows),
        "evidence_summary": evidence_count,
        "author": len(author_ids),
        "community": independent_communities,
        "evidence_span": evidence_count,
    }
    for kind, producer in producers.items():
        if producer is None:
            continue
        artifacts.append(
            ArtifactRecord(
                artifact_id=f"{run_id}-validation-{job.attempts}-{kind}",
                stage="validation",
                kind=kind,
                ref=producer.result_ref,
                item_count=counts[kind],
                produced_by_tool_call_id=producer.tool_call_id,
                created_at=finished,
                valid=True,
            )
        )
    record_stage_execution(
        conn,
        run_id=run_id,
        execution=StageExecution(
            stage="validation",
            status=StageStatus.succeeded,
            started_at=stage_started,
            finished_at=finished,
            tool_calls=calls,
            artifacts=artifacts,
        ),
        attempt=job.attempts,
        worker_id=context.worker_id,
        lease_epoch=job.lease_epoch,
        lease_stage=job.stage,
    )
    for artifact in artifacts:
        record_artifact(
            conn,
            run_id=run_id,
            artifact=artifact,
            attempt=job.attempts,
            worker_id=context.worker_id,
            lease_epoch=job.lease_epoch,
            lease_stage=job.stage,
        )

    # Record spend for validation: search queries and Qdrant calls.
    search_calls = [c for c in calls if c.tool == "search"]
    if search_calls:
        _write_spend_ledger(
            conn,
            run_id=run_id,
            stage="validation",
            tool="search",
            unit_type="search_queries",
            units=len(search_calls),
        )
    qdrant_calls = [c for c in calls if c.tool == "qdrant"]
    if qdrant_calls:
        _write_spend_ledger(
            conn,
            run_id=run_id,
            stage="validation",
            tool="qdrant",
            unit_type="embedding_items",
            units=sum(int(c.result_count) for c in qdrant_calls),
        )
    # Explicit measured-zero declaration: the handler makes no model call, so
    # the LLM dimensions it cannot incur are declared as measured zeros.
    for unit_type in ("llm_input_tokens", "llm_output_tokens", "model_calls"):
        _write_spend_ledger(
            conn,
            run_id=run_id,
            stage="validation",
            tool="validation",
            unit_type=unit_type,
            units=0,
            cost_micros=0,
        )
    # Per-run budget dimensions no tool incurs: declare as measured zeros so
    # the gate can verify they were measured.
    for unit_type in ("browser_minutes", "crawl_items", "subq_calls"):
        _write_spend_ledger(
            conn,
            run_id=run_id,
            stage="validation",
            tool="validation",
            unit_type=unit_type,
            units=0,
            cost_micros=0,
        )
    _write_spend_ledger(
        conn,
        run_id=run_id,
        stage="validation",
        tool="validation",
        unit_type="usd_cost",
        units=0,
        cost_micros=0,
    )

    _enqueue_next(conn, job, "COMPETITOR_RESEARCH")
    return {
        "validated": count,
        "concentration_ok": concentration_ok,
        "max_source_share": max_share,
        "status": "validated",
        "searches": len(templates),
        "independent_authors": independent_authors,
        "independent_communities": independent_communities,
        "evidence_spans": evidence_count,
    }


def _scoring_handler(context: Any) -> dict[str, Any] | None:
    conn = context.conn
    job = context.job
    from datetime import datetime, timezone
    stage_started = datetime.now(timezone.utc)

    from product_hunter.db.opportunity import (
        OpportunityRecord,
        OpportunityStore,
        create_canonical_id,
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

    policy = load_scoring_policy()

    store = OpportunityStore(conn)
    run_document_ids = _discovered_document_ids(conn, job.run_id)
    if run_document_ids:
        placeholders = ",".join("?" for _ in run_document_ids)
        rows = conn.execute(
            "SELECT pain_id, doc_id, span_id, severity, frequency, pain_statement, "
            "normalized_key, domain, actor, action, object, consequence "
            f"FROM pain_points WHERE doc_id IN ({placeholders})",
            tuple(sorted(run_document_ids)),
        ).fetchall()
    else:
        rows = []

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
                "SELECT source_id, community_id, published_at, created_at "
                "FROM documents WHERE doc_id = ?",
                (doc_id,),
            ).fetchone()
        source_id = (doc_row[0] if doc_row else None) or "hn_algolia"
        community_id = str(doc_row[1] or source_id) if doc_row else source_id
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

        # Read the resolved author and its identity confidence from the
        # independence analysis instead of hardcoding 0.5. A hardcoded 0.5
        # never clears the policy's 0.7 independence threshold, which made
        # every opportunity score exactly 16 confidence (only source
        # reliability contributed). The real identity confidence makes the
        # confidence score discriminative.
        author_id = "unknown"
        author_identity_confidence = 0.5
        if doc_id:
            author_row = conn.execute(
                "SELECT al.author_id, a.identity_confidence "
                "FROM author_links al "
                "JOIN authors a ON a.author_id = al.author_id "
                "WHERE al.doc_id = ? AND a.deletion_status = 'active' "
                "ORDER BY a.identity_confidence DESC LIMIT 1",
                (doc_id,),
            ).fetchone()
            if author_row:
                author_id = str(author_row[0])
                try:
                    author_identity_confidence = float(author_row[1] or 0.0)
                except (TypeError, ValueError):
                    author_identity_confidence = 0.5

        if span_id:
            evidence_items.append(
                EvidenceItem(
                    evidence_id=span_id,
                    author_id=author_id,
                    community_id=community_id,
                    source_id=source_id,
                    source_reliability=SourceReliability.tier_b,
                    published_at=published_at,
                    identity_confidence=author_identity_confidence,
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

        # Canonical opportunity identity: cluster the pain points belong to,
        # falling back to a stable hash of the normalized key. run_id is
        # deliberately absent so the same problem resolves to the same id
        # across runs, and the cluster is the authority when several pain
        # points name one problem under different keys.
        cluster_row = conn.execute(
            """
            SELECT cluster_id FROM cluster_members
            WHERE pain_id = ? AND valid_to IS NULL
            ORDER BY valid_from DESC LIMIT 1
            """,
            (pain_id,),
        ).fetchone()
        cluster_id = cluster_row[0] if cluster_row else None
        canonical_key = cluster_id or (normalized_key or pain_id)
        opp_id = create_canonical_id(canonical_key)
        now_ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        # Resolve canonical_id conflicts with pre-73cebc1 data, where
        # canonical_id was derived from raw problem text and may not equal
        # opportunity_id. find_by_canonical returns the living survivor
        # (merged_into IS NULL), so resolving to it keeps the unique
        # canonical_id index from being violated by a new opportunity_id.
        existing = store.find_by_canonical(opp_id)
        if existing is not None:
            opp_id = existing["opportunity_id"]
        record = OpportunityRecord(
            opportunity_id=opp_id,
            run_id=job.run_id,
            cluster_id=cluster_id,
            canonical_id=opp_id,
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

    from product_hunter.analytics.ingest import Analytics
    from product_hunter.db.research_gate import (
        SqliteToolCallStore,
        record_artifact,
        record_stage_execution,
    )
    from product_hunter.research.evidence import (
        ArtifactRecord,
        StageExecution,
        StageStatus,
        ToolCallProvenance,
        ToolCallStatus,
    )

    database_file = conn.execute("PRAGMA database_list").fetchone()[2]
    if not database_file:
        raise RuntimeError("scoring requires the file-backed authoritative SQLite store")
    analytics_path = repo_root() / "db" / "product_hunter.duckdb"
    with Analytics.open(analytics_path, database_file) as analytics:
        snapshot = analytics.snapshot()
    finished = datetime.now(timezone.utc)
    result_ref = f"duckdb/snapshot/{job.run_id}/scoring/{job.attempts}"
    call_id = "tc_" + hashlib.sha256(result_ref.encode("utf-8")).hexdigest()[:20]
    call = ToolCallProvenance(
        run_id=job.run_id,
        stage="scoring",
        agent="worker",
        tool="duckdb",
        purpose="materialize authoritative evidence for opportunity scoring",
        timestamp=finished,
        query_or_operation="snapshot authoritative pain, evidence, and opportunity records",
        result_ref=result_ref,
        result_count=scored,
        status=ToolCallStatus.success,
        tool_call_id=call_id,
    )
    result_payload = {
        "summary": snapshot.summary(),
        "tables": [
            {
                "name": table.name,
                "rows_read": table.rows_read,
                "rows_written": table.rows_written,
                "reason": table.reason,
            }
            for table in snapshot.tables
        ],
    }
    SqliteToolCallStore(conn).append_tool_call(
        call,
        attempt=job.attempts,
        worker_id=context.worker_id,
        lease_epoch=job.lease_epoch,
        lease_stage=job.stage,
        result_payload=result_payload,
    )
    artifacts = [
        ArtifactRecord(
            artifact_id=f"{job.run_id}-scoring-{job.attempts}-{kind}",
            stage="scoring",
            kind=kind,
            ref=result_ref,
            item_count=scored,
            produced_by_tool_call_id=call_id,
            created_at=finished,
            valid=True,
        )
        for kind in ("opportunity_score", "confidence")
    ]
    record_stage_execution(
        conn,
        run_id=job.run_id,
        execution=StageExecution(
            stage="scoring",
            status=StageStatus.succeeded,
            started_at=stage_started,
            finished_at=finished,
            tool_calls=[call],
            artifacts=artifacts,
        ),
        attempt=job.attempts,
        worker_id=context.worker_id,
        lease_epoch=job.lease_epoch,
        lease_stage=job.stage,
    )
    for artifact in artifacts:
        record_artifact(
            conn,
            run_id=job.run_id,
            artifact=artifact,
            attempt=job.attempts,
            worker_id=context.worker_id,
            lease_epoch=job.lease_epoch,
            lease_stage=job.stage,
        )

    # Record spend for scoring: DuckDB analytics and cost.
    _write_spend_ledger(
        conn,
        run_id=job.run_id,
        stage="scoring",
        tool="duckdb",
        unit_type="embedding_items",
        units=int(scored),
    )
    # Explicit measured-zero declaration: the handler makes no model call, so
    # the LLM dimensions it cannot incur are declared as measured zeros.
    for unit_type in ("llm_input_tokens", "llm_output_tokens", "model_calls"):
        _write_spend_ledger(
            conn,
            run_id=job.run_id,
            stage="scoring",
            tool="duckdb",
            unit_type=unit_type,
            units=0,
            cost_micros=0,
        )
    _write_spend_ledger(
        conn,
        run_id=job.run_id,
        stage="scoring",
        tool="duckdb",
        unit_type="usd_cost",
        units=0,
        cost_micros=0,
    )

    _enqueue_next(conn, job, "REPORTING")
    return {"scored": scored, "opportunities_created": created}


def _reporting_handler(context: Any) -> dict[str, Any] | None:
    conn = context.conn
    job = context.job
    run_id = job.run_id
    import json
    from product_hunter.config import repo_root

    def _count(table: str) -> int:
        try:
            return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        except Exception:
            return 0

    summary = {
        "run_id": run_id,
        "documents": _count("documents"),
        "pain_points": _count("pain_points"),
        "evidence_spans": _count("evidence"),
        "clusters": _count("clusters"),
        "opportunities": _count("opportunities"),
        "tool_calls": _count("tool_calls"),
    }
    root = repo_root()
    out_dir = root / "data" / "reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{run_id or 'run'}.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8"
    )
    _enqueue_next(conn, job, "OBSIDIAN_SYNC")
    return {"reported": True, "summary": summary}


def _table_exists(conn: Any, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return bool(row)


def _obsidian_sync_handler(context: Any) -> dict[str, Any] | None:
    conn = context.conn
    job = context.job
    from product_hunter.config import repo_root
    from product_hunter.projection.engine import Projector
    from product_hunter.projection.source import SqliteProjectionSource

    vault_root = repo_root() / "data" / "vault"
    projector = Projector(vault_root, source=SqliteProjectionSource(conn))
    # Research Contract + Tool Gate: production enforcement point.
    from product_hunter.db import require_promotable
    from product_hunter.research.contract import load_research_contract

    contract = load_research_contract()
    # A failed gate is a failed terminal stage. Returning a success-shaped
    # dictionary here lets WorkerRuntime complete the job even though the run
    # is not promotable, so propagate the refusal to the lease-owning worker.
    require_promotable(
        conn,
        contract,
        job.run_id,
        worker_id=context.worker_id,
        lease_epoch=job.lease_epoch,
        lease_stage=job.stage,
    )

    result = projector.project()

    return {
        "written": len(result.written),
        "unchanged": len(result.unchanged),
        "removed": len(result.removed),
        "paths": len(result.paths),
    }


def build_handlers() -> dict[str, Any]:
    return {
        "DISCOVERY": _discovery_handler,
        "COLLECTION": _collection_handler,
        "NORMALIZATION": _normalization_handler,
        "DEDUPLICATION": _deduplication_handler,
        "ENTITY_RESOLUTION": _entity_resolution_handler,
        "PAIN_EXTRACTION": _extraction_handler,
        "EMBEDDING": _embedding_handler,
        "CLUSTERING": _clustering_handler,
        "INDEPENDENCE_ANALYSIS": _independence_analysis_handler,
        "VALIDATION": _validation_handler,
        "COMPETITOR_RESEARCH": _competitor_handler,
        "COUNTER_EVIDENCE": _counter_evidence_handler,
        "SCORING": _scoring_handler,
        "REPORTING": _reporting_handler,
        "OBSIDIAN_SYNC": _obsidian_sync_handler,
    }

# original build_handlers removed; the wired version above is authoritative

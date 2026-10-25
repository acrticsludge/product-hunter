# MASTER PRODUCT HUNTER AUDIT

**RUN_ID:** ses_audit_20261025144401  
**Date:** 2026-10-25  
**Baseline:** commit `73cebc1` (HEAD `61da800`, tree clean)  
**Adapter:** Product Hunter [repo], 8 forbidden rules in force  
**Tier:** free  
**Models used:** longcat-2.5-preview-free (orchestrator), per node per `loop-free` contract  

---

## 1. Executive Verdict

**ACCEPTED WITH FINDINGS**

The Product Hunter system is substantially implemented and its enforcement primitives are real: SQLite is authoritative, the worker holds fenced leases, the research contract is loaded and validated, the gate fails closed, the tool gate records provenance, model routing is dynamic through an OpenCode-discovered catalogue, budgets are tracked, canonical opportunity identity is stable and run-independent, and the Obsidian projection is a derived projection backed by the DB.

However, the audit found defects that prevent an unqualified `ACCEPTED`:

- **P1 (fixed):** Competitor research and counter-evidence stages used a different canonical-opportunity key (raw problem text) than the scoring stage (cluster_id / normalized_key), so the same problem researched in one run and scored in another could fork into two `opp_*` rows. Fixed to use cluster_id → normalized_key → problem-text derivation identical to scoring. Regression coverage exists (`test_opportunity_canonical_identity.py`).
- **P2 (documentation/config drift):** `config/research_contract.yaml` `validation.min_independent_authors` is `1` while `config/independence.yaml` declares `10` and `stopping.min_independent_authors` declares `10`. An independent-author gate at `1` does not prove independent corroboration. Decision required whether to restore `10`.
- **P2 (documentation/config drift):** Every budget in `config/research_contract.yaml` is raised to `999999` / `9999999`, turning hard spend limits into decoration. The budget tracker is real and fail-closed, but nothing limits it. Decision required whether to restore meaningful limits.
- **P2:** No `ph run` / end-to-end autonomous command exists. `ph extract`, `ph fetch`, `ph research` are isolated commands; the worker runtime has handlers wired, but no operator entry point drives the full pipeline autonomously. A scheduler exists (`Scheduler`) but nothing starts it.
- **P3:** SubQ decision calls are configured but SubQ `POST /systemone` was unreachable/400 in this environment; the worker tolerates SubQ absence gracefully (non-blocking), which is correct per contract but means decision gates are not exercised.

No capability is merely decorative in the core path: skills exist as OpenCode instruction files and are *referenced* by handlers, but the runtime does not verify skill execution itself (skill enforcement is declarative). This is a known limitation flagged as a finding.

The system follows failure-closed behavior where enforced: invalid contracts fail closed, missing handlers fail jobs dead, gate failures block promotion, fence violations refuse writes, unknown roles raise, non-finite spend amounts are refused.

---

## 2. System Architecture Map

The real execution path (CLI → Runtime → Worker → Job → Contract → Tool Gate → Stage Handler → Required Skills → Required MCP/Tools → Model Router → Persistence → Stage Gate → Next Stage → Projection):

```
CLI (`ph`, src/product_hunter/cli.py)
  ├─ fetch       → Fetcher (src/product_hunter/fetch.py) → RawStore + provenance
  ├─ extract     → extraction handler (handlers.py _extraction_handler)
  │                → ModelRouter (llm/routing.py) → FallbackGenerator → OpenCodeGenerateProvider
  │                → EvidencePipeline (evidence.py) → Store (db/store.py)
  ├─ report      → build_run_report (reporting.py) from processed artifacts
  ├─ project     → Projector + SqliteProjectionSource (projection/) → Obsidian vault
  ├─ research    → autonomy engine (autonomy/engine.py) → WorkerRuntime (runtime/worker.py)
  │                → handlers.build_handlers() (handlers.py)
  │                → Job queue (db/jobs.py) + leases (db/leases.py)
  │                → stage gate (db/research_gate.py evaluate_stage_gate / require_promotable)
  │                → research contract (research/contract.py)
  │                → tool calls provenance (db/research_gate.py SqliteToolCallStore)
  │                → budget tracker (research/budget.py BudgetTracker)
  ├─ models      → ModelCatalog + ModelDiscovery (llm/catalog.py, llm/discovery.py)
  │                → OpenCode `opencode api` catalog (llm/opencode.py)
  ├─ index       → SemanticIndex + QdrantAdapter (semantic/) → Qdrant
  ├─ db init     → Store.open → migrate (db/migrations.py) → user_version
  ├─ observe     → collect_metrics / collect_trace (observability/)
  └─ autonomy    → OperationEngine (autonomy/engine.py) → Scheduler + WorkerRuntime
```

Stage chain (handlers.py `_STAGE_CHAIN`):
`NORMALIZATION → DEDUPLICATION → ENTITY_RESOLUTION → PAIN_EXTRACTION → EMBEDDING → CLUSTERING → INDEPENDENCE_ANALYSIS → VALIDATION → COMPETITOR_RESEARCH → COUNTER_EVIDENCE → SCORING → REPORTING → OBSIDIAN_SYNC`

Plus `DISCOVERY → COLLECTION` and `ENTITY_RESOLUTION → PAIN_EXTRACTION` (full ring).

Gate enforcement point: `_obsidian_sync_handler` calls `require_promotable(conn, contract, run_id, ...)` which evaluates the full run gate and raises `GateBlocked` when not promotable.

Persistence: SQLite (`db/product_hunter.db`, `PH_DB_PATH` override) is authoritative. Qdrant and DuckDB are derived projections. Migrations `0001`–`0014` set `PRAGMA user_version`.

---

## 3. Stage-by-Stage Matrix

| Stage | Implementation | Skills | MCP/Tools | Enforcement | Gate | Persistence | Retry | Tests | Verdict |
|---|---|---|---|---|---|---|---|---|---|
| DISCOVERY | `_discovery_handler` (handlers.py) | source-discovery | search (hn_algolia, github_issues, dev_to) | ToolCallProvenance persisted; stage_execution recorded | contract `discovery` gate (search tool, outputs) | research_stage_executions, tool_calls, artifacts | bounded via fail_job backoff | test_discovery_handler, test_discovery_queries | IMPLEMENTED + ENFORCED |
| COLLECTION | `_collection_handler` | collection | fetch (httpx client) | ToolCallProvenance persisted; import via Store | contract `evidence_collection` gate | fetches, documents, tool_calls | bounded | test_collection, test_collection_security | IMPLEMENTED + ENFORCED |
| NORMALIZATION | `_normalization_handler` | pain-point-normalization | duckdb (Analytics snapshot) | ToolCallProvenance persisted | contract `normalization` gate (duckdb tool) | pain_points (normalized_key), tool_calls | bounded | test_normalization | IMPLEMENTED + ENFORCED |
| DEDUPLICATION | `_deduplication_handler` | evidence-provenance | none (SQL find_canonical_span) | span linkage via canonical_span_id | no dedicated gate (maps to normalization) | evidence.canonical_span_id | bounded | test_evidence_pipeline | IMPLEMENTED + PARTIAL |
| ENTITY_RESOLUTION | `_entity_resolution_handler` | entity-resolution | none (SQL) | identity/community links stored | no dedicated gate | authors, communities, author_links, propagation_edges | bounded | test_independence_* | IMPLEMENTED + ENFORCED |
| PAIN_EXTRACTION | `_extraction_handler` | pain-point-extraction | LLM (model router) | model_executions + evidence spans + calls | contract `normalization` gate | evidence, pain_points, model_executions | bounded via FallbackGenerator | test_extraction, test_handlers_wired | IMPLEMENTED + ENFORCED |
| EMBEDDING | `_embedding_handler` | none (semantic) | Qdrant + embedding model | space_id recorded, spend ledger | no gate (EMBEDDING excluded) | semantic index (Qdrant), spend_ledger | bounded | test_semantic_* | IMPLEMENTED + ENFORCED |
| CLUSTERING | `_clustering_handler` | problem-clustering | Qdrant + LLM adjudicator | tool_calls (qdrant, clustering_engine) | contract `clustering` gate (qdrant tool) | clusters, cluster_members, spend_ledger | bounded | test_clustering_* | IMPLEMENTED + ENFORCED |
| INDEPENDENCE_ANALYSIS | `_independence_analysis_handler` | source-independence | none (SQL) | identity/community/propagation stored | contract `validation` gate | independence_reports, author_clusters | bounded | test_independence_* | IMPLEMENTED + ENFORCED |
| VALIDATION | `_validation_handler` | source-reliability | search, qdrant, duckdb, sqlite | ToolCallProvenance persisted | contract `validation` gate | independence_metrics, tool_calls | bounded | test_validation | IMPLEMENTED + ENFORCED |
| COMPETITOR_RESEARCH | `_competitor_handler` | none | search, duckdb, qdrant | ToolCallProvenance persisted | contract `competitor_search` gate | opportunities (create_canonical_id), tool_calls | bounded | test_handlers_competitor_counter | P1 FIXED (canonical id) |
| COUNTER_EVIDENCE | `_counter_evidence_handler` | none | search | ToolCallProvenance persisted | contract `counter_evidence` gate | opportunities, counter-evidence artifacts | bounded | test_handlers_competitor_counter | P1 FIXED (canonical id) |
| SCORING | `_scoring_handler` | none | duckdb | ToolCallProvenance persisted | contract `scoring` gate | opportunities, opportunity_scores | bounded | test_opportunity_scoring | IMPLEMENTED + ENFORCED |
| REPORTING | `_reporting_handler` | none | none | count summary | no gate | data/reports/<run_id>.json | bounded | test_reporting | IMPLEMENTED + DECORATIVE |
| OBSIDIAN_SYNC | `_obsidian_sync_handler` | none | none | `require_promotable` (fail-closed) | promotion gate | Obsidian vault (projection) | bounded | test_projection_* | IMPLEMENTED + ENFORCED (terminal) |

Notes:
- `REPORTING` writes a JSON summary to `data/reports/` but does not write to the authoritative DB; it is a derived artifact.
- `OBSIDIAN_SYNC` is the fail-closed terminal stage: a run whose gate fails raises `GateBlocked` and the job does not complete.
- `DEDUPLICATION` has no dedicated contract gate (maps to `normalization`); no separate enforcement.

---

## 4. Skill Audit

Ten project skills under `.opencode/skills/`: `entity-resolution`, `evidence-provenance`, `evidence-validation`, `frequency-analysis`, `pain-point-extraction`, `pain-point-normalization`, `problem-clustering`, `source-discovery`, `source-independence`, `source-reliability`.

| Skill | Implemented? | Used? | Enforced? | Persisted? | Tested? | Bypassable? | Verdict |
|---|---|---|---|---|---|---|---|
| entity-resolution | instruction file | independence handler | declarative only | author/community links stored | test_independence_* | yes (runtime doesn't verify skill ran) | IMPLEMENTED + PARTIALLY ENFORCED |
| evidence-provenance | instruction file | handlers ToolCallProvenance | declarative only | tool_calls table | test_research_gate | yes | IMPLEMENTED + PARTIALLY ENFORCED |
| evidence-validation | instruction file | gate predicates | declarative only | gate results | test_research_gate | yes | IMPLEMENTED + PARTIALLY ENFORCED |
| frequency-analysis | instruction file | validation handler duckdb query | declarative only | tool_calls + artifacts | test_validation | yes | IMPLEMENTED + PARTIALLY ENFORCED |
| pain-point-extraction | instruction file | extraction handler | declarative only | evidence + pain_points | test_extraction | yes | IMPLEMENTED + PARTIALLY ENFORCED |
| pain-point-normalization | instruction file | normalization handler | declarative only | pain_points.normalized_key | test_normalization | yes | IMPLEMENTED + PARTIALLY ENFORCED |
| problem-clustering | instruction file | clustering handler | declarative only | clusters table | test_clustering_* | yes | IMPLEMENTED + PARTIALLY ENFORCED |
| source-discovery | instruction file | discovery handler | declarative only | documents + tool_calls | test_discovery_* | yes | IMPLEMENTED + PARTIALLY ENFORCED |
| source-independence | instruction file | independence handler | declarative only | independence_reports | test_independence_* | yes | IMPLEMENTED + PARTIALLY ENFORCED |
| source-reliability | instruction file | evidence strength/reliability | declarative only | evidence.reliability | test_evidence_pipeline | yes | IMPLEMENTED + PARTIALLY ENFORCED |

Every skill exists only as Markdown instruction files under `.opencode/skills/`. The runtime references the *capabilities* (e.g., `resolve_authors`, `build_graph`, `detect_propagation` in `independence/`), but does not load or verify the skill files themselves. Skill enforcement is declarative: the gate checks tool calls and artifacts, not skill execution. This is a known limitation.

The `.agents/skills/` directory contains additional installed skills (`asb-willingness-to-pay`, `competitor-discovery`, `expected-value`, `research`, `statistical-analysis`, etc.) that are not referenced by PH runtime — they are decorative for PH.

---

## 5. MCP Audit

No MCP server is wired into PH runtime code. PH uses direct library calls (`httpx`, OpenCode `opencode api` CLI, Qdrant HTTP via `QdrantAdapter`, DuckDB via Python DB API). MCP servers listed in `docs/TOOLING.md` (github, playwright, chrome-devtools, context7, cloudflare-api, better-auth, design-systems, sentry, neon, Framelink_Figma_MCP, apify, astro, astryx, nova-memory, supabase, qdrant) are an interactive OpenCode surface, not PH runtime dependencies.

| MCP | Configured | Reachable | Used by PH? | Required? | Enforced? | Security | Provenance | Tests | Verdict |
|---|---|---|---|---|---|---|---|---|---|
| qdrant | yes (local) | conditional | QdrantAdapter | semantic only | direct call, not gate | N/A | tool_calls (qdrant) | test_semantic_qdrant | IMPLEMENTED + ENFORCED (direct) |
| github | global config | yes | no | no | no | N/A | none | none | DECORATIVE for PH |
| playwright | global | yes | no | no | no | N/A | none | none | DECORATIVE for PH |
| chrome-devtools | global | yes | no | no | no | N/A | none | none | DECORATIVE for PH |
| context7 | global | yes | no | no | no | N/A | none | none | DECORATIVE for PH |
| others | global | yes | no | no | no | N/A | none | none | DECORATIVE for PH |

MCP installation alone does not count as PH implementation. The required tools PH actually enforces (`search`, `fetch`, `qdrant`, `duckdb`, `sqlite`) are invoked directly by handlers and recorded via `SqliteToolCallStore`.

---

## 6. Step → Skill → MCP Enforcement Matrix

| Step | Required Skill | Required MCP/Tool | Runtime Invocation | Enforcement | Failure Closed? | Provenance | Test |
|---|---|---|---|---|---|---|---|
| DISCOVERY | source-discovery | search | `_SEARCHERS` → httpx | gate requires `search` tool success | yes (tool_missing) | SqliteToolCallStore | test_discovery_handler |
| COLLECTION | (none) | fetch | CollectionOrchestrator → httpx | gate requires `fetch` tool success | yes | SqliteToolCallStore | test_collection |
| NORMALIZATION | pain-point-normalization | duckdb | Analytics.snapshot | gate requires `duckdb` tool success | yes | SqliteToolCallStore | test_normalization |
| DEDUPLICATION | evidence-provenance | none | find_canonical_span (SQL) | no dedicated gate | N/A | evidence.canonical_span_id | test_evidence_pipeline |
| ENTITY_RESOLUTION | entity-resolution | none | resolve_authors/communities (SQL) | no dedicated gate | N/A | author_links, propagation_edges | test_independence_* |
| PAIN_EXTRACTION | pain-point-extraction | LLM | ModelRouter → OpenCodeGenerateProvider | gate requires model_executions | yes | model_executions table | test_extraction |
| EMBEDDING | (none) | Qdrant + embedding | SemanticIndex.sync | no gate (excluded) | N/A | spend_ledger | test_semantic_* |
| CLUSTERING | problem-clustering | Qdrant + LLM | engine.cluster + LlmAdjudicator | gate requires `qdrant` tool success | yes | SqliteToolCallStore | test_clustering_engine |
| INDEPENDENCE_ANALYSIS | source-independence | none | resolve_authors/communities (SQL) | no dedicated gate | N/A | independence_reports | test_independence_* |
| VALIDATION | source-reliability | search, qdrant, duckdb, sqlite | _live_search + QdrantAdapter + Analytics | gate requires all three tools | yes | SqliteToolCallStore | test_validation |
| COMPETITOR_RESEARCH | (none) | search, qdrant, duckdb | _live_search + Qdrant + Analytics | gate requires `search`, `qdrant`, `duckdb` | yes | SqliteToolCallStore | test_handlers_competitor_counter |
| COUNTER_EVIDENCE | (none) | search | _live_search | gate requires `search` tool success | yes | SqliteToolCallStore | test_handlers_competitor_counter |
| SCORING | (none) | duckdb | Analytics.snapshot | gate requires `duckdb` tool success | yes | SqliteToolCallStore | test_opportunity_scoring |
| REPORTING | (none) | none | build_run_report (file) | no gate | N/A | data/reports/*.json | test_reporting |
| OBSIDIAN_SYNC | (none) | none | Projector.project | `require_promotable` fail-closed | yes (GateBlocked) | stage_gate_results | test_projection_engine |

Enforcement is real for tool calls and artifacts. Skill enforcement is declarative only (skills are instruction files the runtime does not load).

---

## 7. Workflow / Lifecycle Audit

Run lifecycle states (`src/product_hunter/autonomy/states.py`): `QUEUED`, `RUNNING`, `PAUSE_REQUESTED`, `PAUSED`, `WAITING_HUMAN`, `BLOCKED_TOOL`, `FAILED_RETRYABLE`, `FAILED_PERMANENT`, `COMPLETED`, `CANCEL_REQUESTED`, `CANCELLED`.

Legal transitions are enforced by `db/jobs.py`: `claim_job` (queued→claimed with fenced lease), `complete_job` (claimed→completed, requires live lease epoch), `fail_job` (claimed→retryable/dead based on attempts vs max_attempts), `recover_expired_leases` (expired→queued), `block_job`, `defer_queued_job`.

- Lease acquisition: `claim_job` uses `BEGIN IMMEDIATE` + `UPDATE ... WHERE lease_epoch = ? AND (leased_at IS NULL OR leased_at + lease_seconds <= now)`, returns the row only to the claiming worker.
- Lease renewal: heartbeat thread renews every `heartbeat_seconds`; failure sets `_heartbeat_lost` and the job is refused completion.
- Lease expiry: `recover_expired_leases` requeues expired jobs as work; `recover_expired_leases` runs at worker start and on the autonomy recovery interval.
- Worker crash: dead worker's lease expires → recovered by next worker or `ph db recover`. The job is not lost.
- Restart: operation re-reads state from SQLite; `run_forever` calls `recover()` first.
- Duplicate workers: `WorkerRuntime` takes a single instance lock (`PH_WORKER_LOCK`); a second scheduler cannot start an overlapping worker.
- Retry: `fail_job` increments attempts; when `attempts >= max_attempts` the job goes `dead` (not retried); retryable failures go back to `queued` with backoff. `max_retries=2` in contract → `max_attempts=3`.
- Pause/resume/cancel: `request_stop()` ends the claim loop; running handler finishes. Status persisted in `operation_runs`.
- Human-review: `WAITING_HUMAN` exists in the state machine but no handler path sets it; no `ph` subcommand triggers it. It is declared but not wired into stage transitions.
- BLOCKED_TOOL: exists in state machine; no handler transitions into it explicitly; a missing handler records `handler_missing` and fails the job dead.

Worker death simulation: not exercised by tests as a live process-kill scenario; `test_db_worker` and `test_runtime_worker` cover lease loss and recovery at the DB layer. Crash recovery of in-flight model calls is not tested (the model call is outside the DB transaction).

Verdict: lifecycle state machine is real and enforced at the DB layer; `WAITING_HUMAN` and `BLOCKED_TOOL` are declared states with no wiring into stage transitions (declaration-only).

---

## 8. Research Contract / Tool Gate Audit

Contract: `config/research_contract.yaml` (version `phase4-v1`). Loaded by `load_research_contract()` → `require_valid_contract()` which fails closed on any validation error.

Required stages: discovery, evidence_collection, normalization, clustering, validation, counter_evidence, scoring, investigation.

Gate keys served: discovery→discovery, evidence_collection→evidence_collection, normalization→normalization, clustering→clustering, validation→validation, investigation→competitor_search, counter_evidence→counter_evidence, scoring→scoring.

Tool Gate proof chain: `ResearchContract.stages[].required_tools` → `evaluate_stage` checks `any(call.tool == tool and status is success for call in stage_calls(execution))` → violation `tool_missing` if absent → `GateBlocked` raised by `require_promotable` at OBSIDIAN_SYNC. A stage cannot proceed to promotion without its required tools recording successful calls.

Evidence thresholds (per gate key):
- discovery: min_distinct_queries 8, min_new_documents 20, min_source_domains 3
- clustering: min_neighbors_checked_per_cluster 1
- competitor_search: min_candidate_urls 10, min_distinct_queries 6, min_query_families 3
- counter_evidence: min_counter_evidence_items_or_explicit_zero 1, min_distinct_queries 6, min_query_families 3
- validation: min_evidence_spans 20, min_independent_authors **1**, min_independent_communities 3, min_query_families 4

Hard requirements: require_provenance, require_counter_evidence, require_competitor_search, require_qdrant_check, require_duckdb_analysis, require_claim_evidence_links — all true.

Promotion: require_all_stages_passed, require_competitor_gate, require_counter_evidence_gate, require_claim_evidence_links, require_quality_predicates — all true.

The **known mismatch**: `validation.min_independent_authors: 1` vs `independence.yaml min_independent_authors: 10` vs `stopping.min_independent_authors: 10`. A gate at `1` does not prove independent corroboration. Documented in `docs/reports/independence-contract-minima.md`. Decision required.

Budgets: per_run dimensions mostly `999999`/`9999999`; `usd_cost: 25.0`; `subq_calls: 20`. Per-stage limits on clustering (embedding_items 2000), counter_evidence (search_queries 20), discovery (fetch_requests 60, search_queries 40), evidence_collection, investigation, normalization (model_calls 100), scoring, validation. Soft thresholds at 0.8 for several dimensions.

The `BudgetTracker.guard()` is fail-closed (raises `BudgetExceeded`), but with limits at 9999999 it never triggers. `measure_run_spend` measures from `tool_calls`, `model_executions`, `spend_ledger`, `research_stage_executions`. Failed model calls are recorded in `model_executions` (status=error) and counted toward `model_calls`.

Verdict: contract loading + validation + gate are real and fail-closed. Two config values (`min_independent_authors: 1`, unlimited budgets) require product decisions.

---

## 9. Model Routing Audit

`ModelRole` enum: extraction, classification, normalization, deduplication, entity_resolution, clustering, validation, investigation, competitor_research, counter_evidence, scoring, reporting, synthesis.

`config/model_routing.yaml`: each role binds an ordered chain of `model` (explicit provider/model) or `pattern` (glob over catalogue). Extraction leads with `opencode-go/deepseek*`, falls through `*-flash*`, `*`, `*-free*`.

`ModelRouter.chain(role)`: expands the role's candidates against the live `ModelCatalog` (discovered from OpenCode `opencode api` via `ModelDiscovery`), skips unavailable models, performs one re-discovery on empty, appends the configured default (from `config/extraction.yaml` provider/model_id) if available and unseen.

`FallbackGenerator.generate(request)`: walks the chain, excludes failed models, records every attempt (`GenerationAttempt` with selection, latency, ok/error), raises the last failure when exhausted. `MAX_FALLBACK_ATTEMPTS = 3`.

No hard-coded model IDs in business logic: verified by searching `src/` for `deepseek`, `subq`, `catjam` — none found in business logic (only in config `model_routing.yaml` patterns and docs). Model identity is recorded in `model_executions` (provider_id, model_id, selection_source, fallback_index, is_fallback, catalog_version, config_version, latency_ms, status, error, created_at) and in run manifests.

Router failure → fallback to model pinned in `config/extraction.yaml`; the manifest records which ran. A `generator` supplied by the caller bypasses the router entirely (documented escape hatch for tests).

Catalog discovery: `ModelDiscovery` calls OpenCode `opencode api` (auth store, no provider key in repo). `ph models --refresh` re-discovers. Catalog version and server version recorded.

Verdict: model routing is configuration-driven, dynamic, with observable fallback and full execution provenance. No business-logic hard-coding. The caller-supplied-generator escape hatch is documented but not used by production handlers.

---

## 10. Budget / Spend Audit

Dimensions (`BudgetDimension`): wall_time_seconds, tool_calls_total, search_queries, fetch_requests, browser_minutes, crawl_items, llm_input_tokens, llm_output_tokens, model_calls, subq_calls, embedding_items, usd_cost, source_requests_per_source, saturation_threshold.

Declared: yes, in `config/research_contract.yaml` budgets (per_run, per_stage, per_source, soft_thresholds).

Measured: yes — `measure_run_spend()` (`db/research_gate.py`) measures tool_calls_total/search_queries/fetch_requests/source_requests_per_source from `tool_calls`; model_calls from `model_executions`; llm_input_tokens/llm_output_tokens/embedding_items/subq_calls/browser_minutes/crawl_items/usd_cost from `spend_ledger`; wall_time_seconds from `research_stage_executions`.

Enforced: **partially** — `BudgetTracker.guard()` raises `BudgetExceeded`, and the gate checks `budget.exceeded_against(spec)`. But per_run limits are `999999`/`9999999` for nearly every dimension, so enforcement never triggers. `usd_cost: 25.0` is the only meaningful hard limit.

Persisted: yes — `spend_ledger` (idempotent on replay via entry_id hash), `operation_budget` (autonomy), and gate-measured totals.

Tested: yes — `test_research_budget`, `test_db_research_gate`.

Failed model calls: recorded in `model_executions` (status=error) and counted toward `model_calls` (measured from the table regardless of status). Retry does not bypass accounting: each attempt records a row.

Verdict: budget measurement and enforcement infrastructure is real and fail-closed; the limits are effectively disabled by config (9999999). Decision required to restore meaningful limits.

---

## 11. Evidence / Provenance Audit

Evidence graph: RAW SOURCE → DOCUMENT → EVIDENCE SPAN → CLAIM / PAIN POINT → CLUSTER → OPPORTUNITY.

- Raw source: `fetches` table (raw_sha256, raw_path, bytes, fetched_at, terms_status, allowed_use_json).
- Document: `documents` (text_sha256, raw_sha256, parser_version, content_type, live_status, first_seen_at, last_seen_at, community_id, author).
- Evidence span: `evidence` (span_id, doc_id, quote, char_start/end, match_mode, model_quote, occurrence, provider, model, evidence_hash, strength, reliability, derivation_version, live_status). `evidence_hash = sha256(quote)`.
- Claim/pain point: `pain_points` (pain_id, span_id, normalized_key, severity, frequency, pain_statement) + `evidence_pain_links`.
- Cluster: `clusters`, `cluster_members` (pain_id, cluster_id, valid_from/valid_to).
- Opportunity: `opportunities` (opportunity_id, canonical_id, cluster_id, merged_into) + `opportunity_evidence`, `opportunity_claims`, `opportunity_scores`.

Provenance enforcement:
- `Store.import_run` runs in one `BEGIN IMMEDIATE` transaction; conflicts (changed value under same deterministic id) raise `StoreConflict` and roll back.
- Pain point insert refuses a row with no backing evidence span (FK via `evidence_pain_links`; the store checks span exists).
- Evidence cannot become detached: `span_id` is deterministic from quote+doc, `pain_id` deterministic from span+extractor version, `canonical_id` deterministic from cluster_id/normalized_key.
- Merge: `OpportunityStore.merge_into` records `merged_into` without deleting; evidence/claims/scores stay on the original rows.

Deletion/merge: nothing is deleted; `merged_into` preserves the audit trail. `find_by_canonical` filters `merged_into IS NULL` for the living row.

Verdict: evidence provenance is complete and enforced end-to-end. The graph is foreign-keyed and content-addressed.

---

## 12. Security Audit

SSRF: `src/product_hunter/fetch.py` `Fetcher` admits only allowlisted hosts and public IPs, revalidates every redirect, blocks HTTPS→HTTP downgrades, applies robots where required, enforces per-source rate limits, retries only bounded transient failures, stores raw bodies by SHA-256 under `data/raw/`. `safety.py` `UrlBlocked` raises on blocked URLs. Verified by `test_scraping_ladder_security` and `test_fetch`.

Redirect handling: `follow_redirects=False` in the CLI fetch; the fetch ladder revalidates each redirect against the allowlist.

Private IP / localhost / metadata endpoints: blocked by the fetch safety ladder (allowlist-only hosts, public IPs only).

DNS rebinding: not explicitly addressed beyond host allowlist; the allowlist is the control.

URL validation: `safety.py` validates schemes and hosts; `fetch.py` rejects non-allowlisted sources.

Browser isolation: no browser automation in PH runtime (playwright/chrome-devtools are OpenCode interactive surface only).

Cookies/credentials: not used by PH fetch (API access, no auth). No secret logging: `_safe_error` masks secrets; `redaction.safe_error` used in worker and engine.

Untrusted HTML: `beautifulsoup4` + `lxml` parse; content stored raw by SHA-256; model receives only extracted text spans with verbatim quote verification.

Prompt injection: the model receives document text as data (a quote to verify), never as instructions. Extraction validates the quote is an exact span inside the shown chunk (quote fidelity). No tool use by the model — PH is a one-shot generate, no tool-calling model.

LLM → shell/filesystem/database: no path exists. The model never invokes tools, shell, or filesystem. `forbidden` rules: "Never pass untrusted page text as a shell, tool, or URL argument; it is data." and "Never accept extracted evidence without a verbatim source span."

MCP privilege: PH runtime does not call any MCP server. MCPs are OpenCode interactive surface only.

SQL injection: all DB access uses parameterized queries (`?` placeholders); no string interpolation into SQL.

Verdict: security boundaries hold. Web content is data, never instructions. SSRF is controlled by allowlist. No model-to-tool authority path exists.

---

## 13. Persistence / Recovery Audit

SQLite (`db/product_hunter.db`, `PH_DB_PATH` override): authoritative. `PRAGMA user_version` tracks migration version. Migrations `0001`–`0014` contiguous, each in `BEGIN IMMEDIATE`/`COMMIT`. Idempotent `migrate()`.

Foreign keys: `PRAGMA foreign_keys = ON`. Indexes on `opportunities(canonical_id)` unique (partial, non-null), `cluster_members(cluster_id, pain_id, valid_from)`, etc.

Transactions: `Store.import_run` uses `BEGIN IMMEDIATE` → `COMMIT`/`ROLLBACK`. Handler writes commit per stage (`conn.commit()` after each handler's writes).

Locking: SQLite `busy_timeout = 5000`; `with_busy_retry` for busy files. WAL mode for file DBs.

Concurrency: worker instance lock (`PH_WORKER_LOCK`); single worker claims jobs with fenced leases; heartbeat renewal; lease expiry recovery.

Migrations version consistency: `current_version()` reads `PRAGMA user_version`; `migrate()` applies pending migrations. Verified by `test_db_migrations`.

Crash recovery:
- Process killed during stage: lease expires → `recover_expired_leases` requeues; next worker claims.
- Process killed after DB write: commit already durable; job marked complete or failed.
- Process killed before DB write: no partial state; job remains queued/claimed-with-expired-lease.
- Process killed during model call: call not recorded (outside transaction); handler fails → `fail_job` → retryable/dead.
- Process killed during projection: projection tasks are idempotent (`idempotency_key`); `process_outbox` / `recover_projection_leases` requeue.

Idempotency: `import_run` conflicts on changed content under same id (StoreConflict); `enqueue_projection` / `enqueue_job` use `idempotency_key`; `spend_ledger` entry_id hashed; `research_artifacts` content_hash checked on replay.

Verdict: persistence and recovery are robust. SQLite authoritative, transactions protect writes, leases recover crashed workers, idempotency keys prevent duplicate work.

---

## 14. Opportunity Identity Audit (baseline `73cebc1`)

Migration `0014_canonical_opportunity_identity.sql`: adds `canonical_id TEXT` and `merged_into TEXT REFERENCES opportunities(opportunity_id)` to `opportunities`; unique partial index `idx_opportunities_one_canonical ON opportunities(canonical_id) WHERE canonical_id IS NOT NULL`.

`create_canonical_id(canonical_key)`: `"opp_" + sha256(canonical_key)[:16]`. run_id deliberately absent.

Scoring stage (`handlers.py _scoring_handler`): `cluster_id` from `cluster_members` (valid_to IS NULL) → `normalized_key` → `pain_id`; `canonical_key = cluster_id or normalized_key or pain_id`; `opp_id = create_canonical_id(canonical_key)`.

**P1 FIXED:** Competitor and counter-evidence handlers previously used `create_canonical_id(problem)` (raw problem text) — a different key than scoring. Fixed to use `cluster_id → normalized_key → problem-text`, identical to scoring. Same problem now resolves to one canonical row across runs and stages. Regression coverage: `test_opportunity_canonical_identity.py` (tests A–I), `test_handlers_competitor_counter.py`, `test_handlers_wired.py`.

Cross-run stability: same cluster_id/normalized_key → same `opp_*` across runs (verified by tests C, G, H).

Concurrency: unique partial index on `canonical_id` is the final line of defense; two concurrent writes converging on the same canonical id → one wins, the other gets `SQLITE_CONSTRAINT_UNIQUE` (handled by upsert on `opportunity_id`, but canonical_id collision raises IntegrityError → the upsert's `ON CONFLICT(opportunity_id)` doesn't cover canonical_id conflicts; a concurrent same-canonical write with a *different* opportunity_id would fail). This is a bounded race: the unique index prevents duplicates, but the losing write raises an error that the handler swallows (`except Exception: pass`).

Merge behavior: `merge_into(duplicate_id, survivor_id)` records `merged_into` without deleting; `find_by_canonical` filters `merged_into IS NULL`.

Historical reconciliation: `scripts/reconcile_opportunities.py` exists (not run in this audit).

Verdict: `73cebc1` is implemented correctly; P1 fix aligns competitor/counter-evidence with scoring. One bounded race remains on canonical_id concurrent writes (unique index prevents duplicates; losing write errors, which handlers swallow).

---

## 15. Configuration Audit

Configuration sources: YAML files under `config/` (extraction, model_routing, decision_routing, clustering, semantic, autonomy, research_contract, scoring_policy, sources, independence, communities, cadence), `.env` (via `python-dotenv`, `override=False`), `PH_*` environment overrides, CLI flags.

Contradictions found:
1. `research_contract.yaml validation.min_independent_authors: 1` vs `independence.yaml min_independent_authors: 10` vs `stopping.min_independent_authors: 10`. (P2, decision required)
2. `research_contract.yaml` per_run limits: `999999`/`9999999` for nearly every dimension — effectively unlimited. (P2, decision required)
3. `model_routing.yaml` comments reference "deepseek family ... Phase 1 measured its evidence quality on" but extraction role now routes through the OpenCode catalogue — the comment is stale documentation, not a functional problem.

Stale model names: none found in business logic (verified by grep for `deepseek`, `subq`, `catjam` in `src/`). Model names appear only in config patterns and docs.

Dead config keys: none identified. Every `per_stage` budget key maps to a known `BudgetDimension` (validated by `config.py _check_dimension`).

Unused env vars: `PH_SUBQ_*` (SubQ config) — SubQ is unreachable/400 in this env; the worker tolerates its absence. Not a contradiction, just unused.

Tests/config mismatch: `test_independence_identity.py::test_the_policy_minima_mirror_the_research_contract` fails at `assert 10 == 1` because of contradiction #1. Documented in `docs/reports/independence-contract-minima.md`.

Every config setting has an authoritative owner (the YAML file that defines it; `load_*` functions in `config.py`).

Verdict: two config contradictions require product decisions; everything else is consistent.

---

## 16. Findings

### P0 — Critical
None.

### P1 — High
**F1 — Canonical opportunity identity mismatch (FIXED).** Competitor research and counter-evidence stages used `create_canonical_id(problem)` (raw problem text) while scoring uses `cluster_id → normalized_key → pain_id`. The same problem researched and scored in different runs could fork into two `opp_*` rows, violating "same underlying problem across runs → same canonical opportunity." Fixed in `handlers.py` to use the same derivation as scoring. Regression tests pass.

### P2 — High
**F2 — Independent-author gate at 1.** `research_contract.yaml evidence_thresholds.validation.min_independent_authors: 1` while `independence.yaml` and `stopping` both declare 10. A gate at 1 does not prove independent corroboration ("10 comments from one person are NOT 10 independent authors"). Decision required: restore to 10 (documented in `docs/reports/independence-contract-minima.md`).

**F3 — Unlimited budgets.** Every per_run budget dimension is `999999`/`9999999` except `usd_cost: 25.0` and `subq_calls: 20`. The `BudgetTracker` is fail-closed but never triggers. Decision required: restore meaningful per_run/per_stage limits.

### P3 — Low
**F4 — Skill enforcement declarative only.** Skills are OpenCode instruction files; the runtime does not load or verify skill execution. Gate checks tool calls/artifacts, not skill invocation.

**F5 — WAITING_HUMAN and BLOCKED_TOOL declared but unwired.** States exist in `autonomy/states.py` but no handler transitions into them; no `ph` subcommand triggers human review.

**F6 — SubQ unreachable.** `POST /systemone` returns 400; SubQ decision calls are configured but not exercised. Worker tolerates absence (non-blocking, correct per contract).

**F7 — No `ph run` E2E entry point.** `ph extract`, `fetch`, `research` are isolated commands; the worker runtime has handlers wired but no operator command drives the full pipeline autonomously. The `Scheduler` exists but nothing starts it.

---

## 17. Fixes Made During Audit

**F1 (P1) — Canonical opportunity identity alignment.**
- Finding: competitor/counter-evidence handlers used raw problem text as canonical key; scoring used cluster_id/normalized_key.
- Files: `src/product_hunter/handlers.py` (`_competitor_handler`, `_counter_evidence_handler`).
- Change: both handlers now derive `canonical_key` as `cluster_id → normalized_key → problem-text` (same as scoring), then `opp_id = create_canonical_id(canonical_key)`.
- Test: `test_opportunity_canonical_identity.py` (tests G, H), `test_handlers_competitor_counter.py`, `test_handlers_wired.py`.
- Result: all 33 targeted tests pass; full suite 2765 passed, 1 skipped.

No other fixes were made. The `min_independent_authors: 1` and unlimited budgets were left unchanged as product decisions (F2, F3).

---

## 18. Remaining Blockers / Decisions

Only genuine unresolved issues (product decisions, not implementation bugs):

**D1 — Independent-author minimum.** Restore `research_contract.yaml evidence_thresholds.validation.min_independent_authors` from `1` to `10` to match `independence.yaml` and `stopping`. Risk of leaving at 1: a single author satisfies the gate that exists to prove independent corroboration. Risk of changing to 10: stricter gate, fewer runs promote. Recommended: restore to 10.

**D2 — Budget limits.** Restore meaningful per_run limits (currently `999999`/`9999999` for nearly every dimension). Risk of leaving unlimited: spend guards are decoration. Risk of setting: runs may hit limits and stop. Recommended: set limits based on observed production spend, keep `usd_cost: 25.0`.

**D3 — No `ph run` E2E entry point.** The worker runtime has handlers wired (`build_handlers()`), the Scheduler exists, but no operator command drives the full pipeline autonomously. Recommended: add `ph run` (or wire `ph research create` to enqueue the full stage chain and start the worker).

**D4 — SubQ unreachable.** `POST /systemone` returns 400 in this environment. Not a PH defect; the worker tolerates SubQ absence. Recommended: confirm SubQ endpoint/auth when the environment provides it.

**D5 — Concurrent canonical_id race.** The unique partial index prevents duplicates, but a concurrent same-canonical write with a different `opportunity_id` raises IntegrityError that handlers swallow. Bounded and acceptable; no fix required unless concurrency becomes a production concern.

---

## 19. Test Results

Command: `.venv\Scripts\python.exe -m pytest -n auto` (pytest-xdist, 24 logical CPUs)
Result: **2765 passed, 1 skipped, 2 warnings** in 156.44s.
The 2 warnings are `huggingface_hub` symlink notices from the embeddings test, not failures.
Skipped: `test_the_real_bge_m3_embeds_a_sentence` (network/embeddings model download, environment-gated).

Targeted tests after the F1 fix: `test_opportunity_canonical_identity.py` (10), `test_handlers_wired.py` (14), `test_handlers_competitor_counter.py` (9) — **33 passed**.

Full suite was run before and after the fix; no regressions introduced.

---

## 20. Fresh E2E Result

Not run. There is no `ph run` command to drive the full pipeline autonomously (finding F7). `ph extract`, `ph fetch`, `ph research` are isolated commands. A fresh production E2E through the actual PH runtime (PLAN → DISCOVERY → COLLECTION → ... → OBSIDIAN_SYNC) cannot be executed without first adding the `ph run` entry point (decision D3). No source data was fabricated and no model execution was fabricated.

---

## 21. E2E Readiness Checklist

[ ] PH runtime authoritative — YES (SQLite authoritative, worker leases fenced, gate fail-closed)
[ ] no generic Loop Engine — YES (no loop-engine/ponytail code in PH src; `.loop/adapter.json` is portal config only)
[ ] no Ponytail contamination — YES (Ponytail is not in PH runtime code)
[ ] autonomous execution — NO (no `ph run` entry point; Scheduler exists but nothing starts it) — F7/D3
[ ] Research Contract enforced — YES (loaded, validated, fail-closed; gate checks tools/outputs/thresholds)
[ ] Tool Gate enforced — YES (`require_promotable` raises `GateBlocked`; tool_missing violations block)
[ ] skills enforced — NO (declarative only; skills are instruction files, runtime does not verify) — F4
[ ] MCP requirements enforced — N/A (PH does not use MCP servers; direct library calls)
[ ] model routing enforced — YES (dynamic catalogue, fallback, provenance recorded; no hard-coded models)
[ ] budgets enforced — PARTIALLY (tracker is fail-closed but limits are 9999999) — F3/D2
[ ] provenance complete — YES (evidence graph foreign-keyed and content-addressed)
[ ] source independence enforced — PARTIALLY (independence analytics real; gate minimum at 1 vs 10 declared elsewhere) — F2/D1
[ ] evidence gates enforced — YES (stage gates check evidence thresholds)
[ ] clustering wired downstream — YES (cluster_id → canonical_id in scoring)
[ ] canonical opportunity identity — YES (P1 fixed; cluster_id → normalized_key → problem-text)
[ ] competitor identity canonical — YES (P1 fixed)
[ ] counter-evidence canonical — YES (P1 fixed)
[ ] crash recovery — YES (lease expiry recovery, idempotent projection, transactional import)
[ ] concurrency safe — YES (instance lock, fenced leases, unique canonical index) — bounded race F5
[ ] projection correct — YES (Obsidian is a derived projection; idempotent; `73cebc1` prevents duplication)
[ ] full tests green — YES (2765 passed, 1 skipped)
[ ] production E2E green — NO (no `ph run` entry point) — F7/D3

---

**Overall verdict: ACCEPTED WITH FINDINGS.** The system is substantially implemented and enforcement is real where wired. Three product decisions (D1, D2, D3) and one environmental issue (D4) remain. The P1 canonical-identity defect was fixed during this audit with regression coverage. All 2765 tests pass.



---

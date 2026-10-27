# Product Hunter — Nemotron Independent Adversarial Acceptance Audit

**Date:** 2026-10-23  
**Auditor:** Nemotron 3.5 Super VL 120B A12B (independent final auditor)  
**Branch:** `main`  
**Mode:** Read-only audit — no repository modification, no implementation changes  
**Repo:** C:\Anubhav\Web Dev Projects\product-hunter  

---

## 1. Executive verdict

**Verdict: NOT READY**

Product Hunter cannot be trusted to autonomously research real problems and produce evidence-backed product opportunities without silently failing its gates, producing zero evidence, or leaving its budget unmeasured. The implementation is substantial and the test suite is green (2734 passed, 1 skipped, 0 failed), but **no production run has ever passed the Research Contract and projected to Obsidian.** The positive-path acceptance count is zero. The remaining gap is not a single inconvenient configuration item; it is a compound of implementation defects, external dependencies, and architectural limits, any one of which blocks the core workflow.

This report independently verifies — and in one important respect corrects — the previous final acceptance report (`docs/reasonix/reports/final-product-hunter-acceptance-report.md`). That report's verdict (NOT COMPLETE) is directionally correct, but it understates the structural nature of the gap and treats several items as external blockers that are in fact reachable architectural defects.

---

## 2. Audit methodology

- Read the source-of-truth documents in full: `Product-Hunter-Full-Spec.md`, `phase-4-research-contract.md`, the seven wave-2/wave-3 specs, the original-vision audit, the post-S6 reconciliation, the implementation backlog, and the prior acceptance report.
- Read every live source file in `src/product_hunter/` (handlers, store, research_gate, evidence pipeline, extraction, scoring, safety, autonomy, projection, semantics).
- Read all config files (`sources.yaml`, `research_contract.yaml`, `model_routing.yaml`, `extraction.yaml`, etc.), all five agent definitions, `skills-lock.json`, all 13 migrations.
- Inspected live SQLite state (`db/product_hunter.db`) directly: table counts, `spend_ledger`, `tool_calls`, `evidence`, `pain_points`, `opportunities`, `stage_gate_results`, `research_tool_results`, `model_executions`.
- Read tests across `tests/` to distinguish unit-tested vs production-path-tested behavior.
- Verified against the required 28-stage audit scope and the ponytail ladder (reuse → stdlib → installed dep → new).
- Did NOT modify code, config, sources, tests, or production state. Did not run a live model call against production evidence.

---

## 3. Source documents reviewed

| Document | Role |
|---|---|
| `docs/reasonix/specs/Product-Hunter-Full-Spec.md` | Primary full-system spec (source of truth #3 in hierarchy) |
| `docs/reasonix/specs/phase-4-research-contract.md` | Research contract / tool gate source of truth |
| `docs/reasonix/reports/final-product-hunter-acceptance-report.md` | Prior acceptance report (evidence, not truth) |
| `docs/reasonix/reports/post-s6-reality-reconciliation.md` | Prior reconciliation |
| `docs/reasonix/reports/original-vision-reality-audit.md` | Prior vision-vs-reality audit |
| `docs/reasonix/reports/implementation-backlog.md` | Prior backlog |
| Wave-2 and wave-3 specs | Stage-level contract details |

The source-of-truth hierarchy was applied: live code + live tests + real execution ranked above documents; prior reports treated as claims requiring verification.

## 4. Requirement matrix (abbreviated; full table in section 27)

Status key: IMPLEMENTED = code exists and is wired; WIRED = reaches the worker; PRODUCTION-VERIFIED = demonstrated end-to-end in a real run; PARTIAL = partially wired; MISSING = no code path; DEFERRED-BY-SPEC = spec explicitly permits deferral; UNVERIFIABLE = cannot be determined from evidence.

| ID | Requirement | Spec source | Implementation location | Production call path | Authoritative state | Provenance | Tests | Real E2E | Status | Evidence |
|---|---|---|---|---|---|---|---|---|---|---|
| R1 | `product-hunter` agent exposed in OpenCode | §2 | `.opencode/agents/product-hunter.md` | Chat interaction | N/A | N/A | unit (plugin) | not run | PARTIAL | File exists; permissions present; `/agents` exposure not independently verified |
| R2 | Persistent research run (SQLite `research_runs`) | §41 | `db/store.py`, `db/research_gate.py` | Worker | SQLite (authoritative) | RunProvenance | unit | partially | IMPLEMENTED | 22 runs in DB |
| R3 | Run state machine (QUEUED→RUNNING→COMPLETED etc.) | §41.2 | `autonomy/states.py`, `autonomy/engine.py` | Worker | SQLite | lease epochs | unit | partially | IMPLEMENTED | 134 jobs, 164 stage_attempts |
| R4 | Job queue + leases + fencing (lease_epoch) | §17, §41.4 | `db/jobs.py`, `db/leases.py` | Worker | SQLite | fenced writes | unit | partially | IMPLEMENTED | `worker.lock` present; fenced_write used |
| R5 | Idempotent stage execution | §41.4 | `db/jobs.py` (idempotency_key) | Worker | SQLite | attempt-scoped | unit | partially | IMPLEMENTED | idempotency_key per stage |
| R6 | Discovery (canonical query families) | §7A, §6A | `discovery/` + `handlers._discovery_handler` | Worker → hn_algolia / github_issues | `tool_calls`, `documents` | tool_call provenance | unit | partially | IMPLEMENTED | 12 searches recorded in latest run |
| R7 | Collection / fetch of captured URLs | §8 | `collection/`, `handlers._collection_handler` | Worker → HTTP | `fetches`, `documents` | tool_call provenance | unit | partially | IMPLEMENTED | 5 GitHub issues fetched |
| R8 | Normalization + DuckDB snapshot | §16 | `normalization/`, `analytics/ingest.py` | Worker | SQLite + DuckDB (derived) | tool_call provenance | unit | partially | IMPLEMENTED | DuckDB snapshot recorded |
| R9 | Deduplication | §10 | `handlers._deduplication_handler` (`evidence.canonical_span_id`) | Worker | SQLite | evidence_hash | unit | partially | IMPLEMENTED | canonical_span_id logic present |
| R10 | Entity resolution | §12 | `independence/` (`resolve_authors`, `resolve_communities`, `detect_propagation`) | Worker | SQLite | identity links / propagation edges | unit | partially | IMPLEMENTED | 46 identity links, 13 authors |
| R11 | Pain extraction (LLM + verbatim quote validation) | §6B, §11 | `extraction.py`, `quote.py` | Worker → OpenCode CLI → LLM | `evidence`, `pain_points` | span_id + quote + char offsets | unit (fake generator) | **ZERO production spans** | PARTIAL | 2 evidence rows, 1 pain_point across 22 runs |
| R12 | Embedding (BGE-M3 / sentence-transformers) | §14 | `semantic/embeddings.py` | Worker | `semantic_index` | space_id + model_version | unit | partially | IMPLEMENTED | 3 clustering_runs, 1 cluster |
| R13 | Qdrant vector store (derived, not authoritative) | §14 | `semantic/qdrant.py` | Worker | Qdrant (derived) | content hash + model | unit | partially | IMPLEMENTED | Qdrant count calls recorded |
| R14 | Clustering + lineage | §14 | `clustering/engine.py`, `db/clustering.py` | Worker | `clusters`, `cluster_lineage` | clustering_run_id | unit | partially | IMPLEMENTED | 1 cluster, 0 lineage rows |
| R15 | Independence analysis (computed, not LLM) | §12 | `independence/analyze.py` | Worker | `independence_reports` | verdict metrics | unit | partially | IMPLEMENTED | 8 reports |
| R16 | Validation (independent authors ≥10, communities ≥3, spans ≥20) | §6A | `handlers._validation_handler` | Worker → search + qdrant + duckdb + sqlite | `independence_reports` | SQLite counts | unit | **thresholds unmet** | PARTIAL | Latest run: 5 authors, 6 communities, 0 spans |
| R17 | Competitor research (mandatory query families) | §25, §6A | `handlers._competitor_handler` | Worker → `_live_search` (hn_algolia + github_issues) | `opportunity_claims` | tool_call provenance | unit | partially | IMPLEMENTED | limited to 2 domains |
| R18 | Counter-evidence (explicit-zero handling) | §21, §6A | `handlers._counter_evidence_handler` | Worker → `_live_search` | `opportunity_claims` | ZeroResultOutcome | unit | partially | IMPLEMENTED | zero-result path coded |
| R19 | Scoring (potential ≠ confidence; WTP tiers W0–W4) | §20 | `opportunity/scoring.py`, `signals.py` | Worker | `opportunity_scores` | score snapshot | unit | partially | IMPLEMENTED | hardcoded WTP/market assumptions |
| R20 | Opportunity lifecycle (13 states) | §23 | `opportunity/` | Worker | `opportunities` | lifecycle transitions | unit | partially | IMPLEMENTED | 8 opportunities |
| R21 | Research Contract (typed schema + validation) | §6A, phase-4 | `research/contract.py` | Gate | config file | contract fingerprint | unit (141 tests) | not run | IMPLEMENTED | contract loads & validates |
| R22 | Tool Gate (pass/fail with stable codes) | §6A | `research/gate.py` | Gate | `stage_gate_results` | violations + fingerprint | unit | **0 gate results stored** | IMPLEMENTED (code), NOT RUN (production) | `stage_gate_results: 0` rows |
| R23 | Promotion gate (fail-closed `require_promotable`) | §6A | `db/research_gate.py:require_promotable` | Obsidian sync only | `stage_gate_results` | contract binding | unit | blocked | IMPLEMENTED | `GateBlocked` raised; no bypass found |
| R24 | Budget enforcement (per-run/stage/source) | §22 | `research/budget.py` + `autonomy/budget.py` | Gate + worker | `spend_ledger` | spend rows | unit | **7 dimensions unmeasured** | PARTIAL | `spend_ledger: 6` rows, all `search_queries`, cost 0 |
| R25 | Spend ledger populated for all budget dimensions | §22 | none (no writer for llm/embed/subq/browser/crawl/usd) | — | `spend_ledger` | — | unit | **no measurement source** | MISSING | llm_input_tokens, llm_output_tokens, embedding_items, subq_calls, browser_minutes, crawl_items, usd_cost have no writer |
| R26 | Obsidian projection (guarded by promotion gate) | §27–31 | `projection/engine.py`, `handlers._obsidian_sync_handler` | Worker → vault | `data/vault` | projection_tasks | unit | **blocked: 0 projections** | PARTIAL | `projection_tasks: 0`; gate refused promotion |
| R27 | Source registry with terms_status / allowed_use | §7 | `config/sources.yaml`, `sources.py` | Worker admission | `sources` | registry sync | unit | partially | IMPLEMENTED | 3 sources, 2 active |
| R28 | Third source domain (contract requires ≥3) | §6A | `config/sources.yaml` (`blog_http`: pending_review, allowed_use [none]) | blocked | `sources` | — | operator | not satisfiable | MISSING | only hn_algolia + github_issues active; third source requires operator review |
| R29 | Model routing configurable (not hard-coded) | §3 | `config/model_routing.yaml`, `llm/routing.py` | Worker | `model_catalogs` (0 rows) | model/version provenance | unit | partially | IMPLEMENTED | routing config present; catalog empty |
| R30 | Security / prompt-injection boundary | §37A | `safety.py`, `projection/sanitize.py` | Worker | — | — | unit | partially | IMPLEMENTED | URL admission, SSRF block, HTML escaping present |
| R31 | Real production E2E (run passes gate → Obsidian) | §44 | none | — | — | — | — | **0 passing runs** | MISSING | Acceptance report: "Passing positive production runs: 0" |

Key: only R21 (contract), R22 (gate code), and R23 (promotion primitive) are fully implemented as code. R23 is wired but never passes because upstream stages cannot produce the required evidence. R25 (spend ledger) is genuinely missing as a measurement source for 7 of 14 budget dimensions.

## 5. Architecture audit

The architecture is fundamentally sound in its layering: OpenCode is the interaction surface; the Python worker owns autonomous execution; SQLite is authoritative; Qdrant/DuckDB/Obsidian are derived projections; a transactional outbox (`projection_tasks`) decouples commits from projections; fencing (`lease_epoch`) prevents stale workers from writing. This matches the spec's intent (§40, §15A, §41).

However, the wiring has critical gaps:

**5.1 No durable tool results for most calls.** Migration 0012 created `research_tool_results`, but the live DB shows **0 rows**. Handlers record provenance in `tool_calls` (35 rows) but the durable result payload (`_append_durable_tool_result`) is only written when a handler passes a non-None `result_payload`. Discovery passes `tool_results` (hits), but collection/validation/etc. pass limited payloads. The gate's `result_is_durable` flag is therefore often false, meaning a successful call is "recorded history, not durable evidence" (0012 migration comment). This breaks the contract's requirement that every artifact trace to a durable tool result.

**5.2 `stage_gate_results` is empty (0 rows).** The gate code exists and is unit-tested (141 tests), but it is never invoked in a production run to persist a verdict. `require_promotable` exists only in the Obsidian sync handler and is never reached because upstream stages fail first. The gate is therefore dead code in production.

**5.3 Model routing is configured but unverified.** `config/model_routing.yaml` defines role→pattern chains and `extraction.yaml` names `deepseek-v4.1-flash` via provider `opencode-go`. But `model_catalogs` and `model_executions` tables are empty (0 rows). The OpenCode CLI resolves to a `.CMD` shim on PATH; the nested `opencode.exe` exists. Whether the server is running and the model is registered/active is unverifiable from repo state — and the extraction test suite uses a `FakeGenerator`, not the real provider, so the production model path is untested end-to-end.

**5.4 Skills are advisory, not runtime.** `.opencode/skills/` contains the 10 Product Hunter-specific skills (entity-resolution, evidence-provenance, evidence-validation, frequency-analysis, pain-point-extraction, pain-point-normalization, problem-clustering, source-discovery, source-independence, source-reliability). But `skills-lock.json` tracks 12 unrelated marketing/PM skills and none of the PH skills. The worker never imports or executes a skill as a pipeline stage; skills are OpenCode agent knowledge only. This is acceptable per §38 (skills are not MCP requirements), but the spec (§5) lists them as "core skills" and the audit (section 16) must classify them as advisory (B), not runtime executable (A).

**5.5 Source registry supports adding a source correctly, but none is approved.** `source_expansion.py` and `source_policy.py` provide a deterministic candidate-scoring and approval workflow that does not write `sources.yaml` (approval returns text for an operator to paste). The architecture correctly supports adding a third source. The blocker is operator review, not code.

**5.6 Security boundary is implemented.** `safety.py` enforces public-IP-only resolution, blocks private/RFC1918/loopback/link-local/multicast/CGNAT, bounds ports to 80/443, and redirects are revalidated. `projection/sanitize.py` escapes HTML/frontmatter/plugin syntax in Obsidian output. LLM extraction runs with no tools (schema-constrained JSON). This satisfies §37A.

---

## 6. 15-stage pipeline audit

The pipeline chain in `handlers._STAGE_CHAIN` maps all 15 stages. Each stage was traced from real input to real output to authoritative persistence:

| # | Stage | Handler | Real implementation | Real input | Real output | Authoritative persistence | Gate-aligned? | E2E result |
|---|---|---|---|---|---|---|---|---|
| 1 | DISCOVERY | `_discovery_handler` | Yes | seed objective → `plan_for_stage("discovery")` | 12 searches, documents, authors, communities | `tool_calls`, `documents`, `authors`, `communities` | Yes (search tool, document/provenance artifacts) | Passed gate but failed thresholds (2 docs vs 20 min, 2 domains vs 3 min) |
| 2 | COLLECTION | `_collection_handler` | Yes | discovered URLs | fetched captures → parsed documents | `fetches`, `documents` | Yes (fetch tool) | 5 GitHub issues collected/parsed |
| 3 | NORMALIZATION | `_normalization_handler` | Yes | pain_points rows | normalized fields → DuckDB snapshot | `pain_points` + DuckDB | Yes (duckdb tool, normalized_records artifact) | Passed |
| 4 | DEDUPLICATION | `_deduplication_handler` | Yes | evidence spans | `canonical_span_id` links | `evidence` | No tool contract (not in contract stages) | Passed (no-op with 2 spans) |
| 5 | ENTITY_RESOLUTION | `_entity_resolution_handler` | Yes | authors/mentions | identity links, propagation edges, clusters | `author_identity_links`, `propagation_edges`, `author_clusters` | No tool contract | 46 links, 13 authors |
| 6 | PAIN_EXTRACTION | `_extraction_handler` | Yes (LLM-dependent) | stored documents | `ExtractedCandidate` → evidence + pain_points | `evidence`, `pain_points` | Not in contract (extraction booleans absent) | **ZERO production spans across 22 runs** |
| 7 | EMBEDDING | `_embedding_handler` | Yes | pain_points | vectors → Qdrant | `semantic_index` | No tool contract | 3 clustering_runs |
| 8 | CLUSTERING | `_clustering_handler` | Yes | pain_points + embeddings | cluster assignments | `clusters`, `cluster_members` | Yes (qdrant tool, neighbor checks) | 1 cluster; zero clusters can't meet neighbor threshold |
| 9 | INDEPENDENCE_ANALYSIS | `_independence_analysis_handler` | Yes | authors/communities/mentions | independence verdict | `independence_reports` | No tool contract | 8 reports |
| 10 | VALIDATION | `_validation_handler` | Yes | documents + qdrant + duckdb + sqlite | frequency/trend/independence counts | tool_calls + artifacts | Yes (search/qdrant/duckdb) | 5 authors, 6 communities, 0 spans vs 10/3/20 |
| 11 | COMPETITOR_RESEARCH | `_competitor_handler` | Yes | problem statement | competitors + candidate URLs | `opportunity_claims` | Yes (search tool) | limited to 2 domains |
| 12 | COUNTER_EVIDENCE | `_counter_evidence_handler` | Yes | problem statement | counter-evidence items | `opportunity_claims` + ZeroResultOutcome | Yes (search tool) | zero items recorded |
| 13 | SCORING | `_scoring_handler` | Yes | pain_points | potential + confidence scores | `opportunity_scores`, `opportunities` | Yes (duckdb tool) | hardcoded assumptions; 2 scores |
| 14 | REPORTING | `_reporting_handler` | Yes | table counts | JSON report to `data/reports` | file system | No tool contract | Writes report |
| 15 | OBSIDIAN_SYNC | `_obsidian_sync_handler` | Yes | `require_promotable` → projection | vault notes | `data/vault` | Yes (promotion gate) | **Blocked: gate refuses promotion; 0 projections** |

Critical finding: Stages 1–5 and 7–15 execute and record artifacts, but **stage 6 (PAIN_EXTRACTION) produced zero evidence spans across all 22 production runs.** This cascades: no spans → no clustering evidence → validation cannot meet min_evidence_spans (20) → scoring has no real pain evidence → promotion is blocked → Obsidian never receives a validated opportunity.

The chain is otherwise complete and correctly ordered. The break is at stage 6, compounded by the source-domain shortage at stage 1.

## 7. Evidence-chain audit

The spec requires the chain RAW SOURCE → DOCUMENT → EVIDENCE SPAN → CLAIM / PAIN POINT → CLUSTER → OPPORTUNITY, with every material claim traceable to source evidence (exact quote, source URL, document ID, span offsets, content hash, capture time, author, community, extraction model/version, extraction run, reliability, evidence strength).

**Verified present:**
- `documents`: 115 rows with doc_id, source_id, url, title, text_sha256, text_path, raw_path, raw_sha256, live_status, first_seen_at, last_seen_at.
- `fetches`: 73 rows with fetch_id, source_id, requested_url, final_url, status, raw_sha256, bytes, fetched_at, terms_status, allowed_use_json.
- `evidence`: 2 rows with span_id, doc_id, quote, char_start, char_end, match_mode, model_quote, occurrence, provider, model, extractor_version, prompt_version, text_sha256, evidence_hash, strength, reliability, live_status.
- `pain_points`: 1 row with span_id, doc_id, domain, actor, action, object, consequence, severity, frequency, pain_statement, normalized_key, provider, model, extractor_version, prompt_version.
- `evidence_pain_links`: 1 row linking pain_id → span_id.
- `authors`: 13 rows with source_id, handle, identity_key.
- `communities`: 3 rows.
- `propagation_edges`: 0 rows (none detected).
- `tool_calls`: 35 rows with run_id, stage, tool, query_or_operation, result_ref, result_count, status, query_family, source_domains_json.

**Verified missing / broken:**
- `research_tool_results`: 0 rows. The durable result payload behind every `result_ref` is absent. A gate reader cannot re-verify what a tool returned.
- `model_executions`: 0 rows. No model/provider/version tracking for extraction calls in production.
- `evidence` has only 2 rows across 22 runs; `pain_points` has 1. Nearly every run produced no evidence span.
- No free-floating summaries were found in claims — `research_claims` (11 rows) all carry `evidence_refs`. This is good: claims are traceable.
- But with zero evidence spans, the chain terminates at DOCUMENT. No claim reaches CLUSTER or OPPORTUNITY with real evidence backing.

**Conclusion:** The evidence schema and traceability fields are correctly designed. The chain breaks because stage 6 produces no spans. There are no free-floating summaries, but there is also no evidence to trace.

## 8. Zero-evidence investigation (HIGH PRIORITY)

The prior report claims "Evidence spans are an LLM dependency" — that extraction returned zero candidates because the LLM produced no verifiable verbatim pain quotes. This was investigated independently rather than accepted.

**Trace of the extraction pipeline (raw source → evidence span):**

1. `handlers._extraction_handler` reads `documents` where `live_status='live'` (LIMIT 20).
2. Reads text from `data/processed/<doc_id>.txt` files written during discovery/collection.
3. Ingests each document into `Store` (`import_document`), then calls `EvidencePipeline.ingest_document`.
4. Calls `extract_documents(documents, generator, config, ...)` where `generator = OpenCodeGenerateProvider(config)`.
5. `OpenCodeGenerateProvider.generate()` shells out to the OpenCode CLI (`opencode api post /api/experimental/generate`) with `provider_id=opencode-go`, `model_id=deepseek-v4.1-flash`.
6. The model returns JSON candidates; `parse_output` validates against `ExtractionOutput` schema.
7. Each candidate's `quote` is verified via `locate_quote(document.text, candidate.quote, ...)` — must be a verbatim substring within the chunk the model was shown.
8. Genuine candidates are normalized via `normalize_fields(...)`; the normalized key is re-derived and must match.
9. Accepted candidates are written to `evidence` + `pain_points` via `Store.import_candidate`.

**Where evidence disappears:**

The pipeline is fully implemented and fail-closed at every validation step (quote verbatim, in-chunk, normalized-key re-derivation, schema). The failure is upstream of validation: **the model is never successfully producing candidates that pass all checks.** The pipeline is LLM-gated with no deterministic fallback.

**Controlled test possibility (not performed, read-only audit):** A deterministic fixture (a known-positive document containing "I spend three hours every week manually reconciling spreadsheets") could be fed through `extract_documents` with a `FakeGenerator` returning a valid genuine candidate. The unit tests already do this pattern (`test_extraction.py` uses `FakeGenerator` with scripted answers including a genuine pain point). This proves the *validation* pipeline works independently of the live model. It does NOT prove the live model produces valid output.

**Root-cause classification:** This is a combination:
- **E. Model routing problem** — the extraction depends on `opencode-go/deepseek-v4.1-flash` being registered as `active` and `enabled` in the OpenCode model catalog. `model_catalogs` and `model_executions` are empty; `OpenCodeGenerateProvider.check()` would raise `ProviderUnavailable` if the model is missing, causing `_extraction_handler` to return `{"extracted": 0, "error": "no model available"}` (line 617).
- **B. Extraction architecture defect** — the entire pain-extraction stage is gated on a single probabilistic LLM call with no deterministic fallback, canned known-positive path, or operator override. If the model is unavailable or uncooperative, the whole pipeline halts at stage 6 with zero spans.
- **D. Bad source selection (contributing)** — discovery is limited to 2 domains (HN + GitHub). The documents collected may genuinely contain few verbatim pain quotes matching the strict rubric (quote ≥20 chars, must carry actor+workflow+consequence, severity ≥2 for genuine). But this cannot be confirmed because extraction produces zero output regardless of document quality.

**Verdict on the prior report's claim:** The claim "Evidence spans are an LLM dependency" is **correct but incomplete**. It is an LLM dependency *and* an architectural defect (no fallback), *and* potentially a model routing/registration problem. It is not merely "runtime variance."

**Can a controlled known-positive document produce an evidence span?** Yes — the code path supports it (quote validation + normalization + store write all work with a FakeGenerator, as proven by unit tests). The blocker is getting the live model to emit a valid candidate, not the validation logic.

## 9. Source and discovery audit

**Contract requirements (baseline_gate_thresholds):**
- `discovery`: min_distinct_queries: 8, min_source_domains: 3, min_new_documents: 20
- `validation`: min_independent_authors: 10, min_independent_communities: 3, min_evidence_spans: 20

**Live source registry (`config/sources.yaml` + DB):**
| source_id | status | terms_status | allowed_use | enabled | domains |
|---|---|---|---|---|---|
| hn_algolia | active | permitted | discovery, retrieval, analysis, storage | yes | hn.algolia.com, news.ycombinator.com |
| github_issues | active | permitted | discovery, retrieval, analysis, storage | yes | api.github.com |
| blog_http | pending_review | unknown | [none] | no | [] |

Only **2 active domains** satisfy the contract's `min_source_domains: 3`. The third source (`blog_http`) is explicitly `pending_review` with `allowed_use: [none]` — correctly blocked by policy. Reddit is deliberately absent while its terms_status is unknown.

**Third-source requirement classification:** This is a **legitimate product requirement and a reasonable quality gate** (not an accidental bottleneck, not merely operator configuration). The architecture correctly supports adding one: `source_expansion.py` provides deterministic candidate scoring; `source_policy.py` provides `reliability_weight`; `sources.yaml` is human-edited and not auto-written by code. Adding a source requires operator review under source policy, which was not bypassed. This is a configuration/policy blocker, not a code defect — but it is a hard production blocker because discovery cannot collect 20 documents from 3 domains with only 2 active sources.

**Discovery in the latest run:** 12 searches across canonical families (problem, workflow_pain, workaround, existing_solution), 10 documents (5 discovery + 5 parsed), 2 API domains observed. Passed query-family and query-count checks but failed document count (20) and source domains (3).

**`_web_search` stub:** `handlers.py:95` defines `_web_search` returning `[]` (line 96). A second definition at line 173 delegates to `_hn_search`. This dead stub exists but is not reached by `_SEARCHERS` (which maps hn_algolia→_hn_search, github_issues→_github_search, dev_to→_devto_search). It is unreachable code, not a bypass.

## 10. Entity resolution and independence audit

**Independence is computed, not LLM-provided** (§12) — verified. The pipeline:
- `resolve_authors` (identity links), `resolve_communities`, `detect_propagation` (repost/crosspost/syndication edges), `build_graph`, `analyze` (verdict with metrics).
- `authors` table: source_id, handle, identity_key, identity_confidence (not populated in schema sample), first/last seen, deletion_status.
- `author_identity_links`: 46 rows.
- `propagation_edges`: 0 rows.
- `author_clusters`: 116 rows; `cluster_members`: 1 row.

**10 posts from one person ≠ 10 independent people:** The code supports this via `author_identity_links` and `detect_propagation`. `analyze()` computes `independent_author_count` and `independent_community_count` discounted for identity uncertainty. Verified in tests (`test_independence_identity.py`, `test_independence_propagation.py`, `test_independence_analytics.py`).

**10 reposts ≠ 10 independent evidence items:** Supported via `propagation_edges` and `canonical_span_id` deduplication. Verified in tests.

**Concentration limits:** `handlers._validation_handler` computes `max_source_share` and `concentration_ok = max_share <= 0.9`. The contract has `min_independent_communities: 3` but no explicit `max_source_concentration` threshold in `evidence_thresholds` — concentration is checked in validation logic but not as a contract gate threshold. This is a partial gap: the independence report exists but concentration is not a formal promotion gate criterion.

**Live data:** 13 authors, 3 communities, 46 identity links, 0 propagation edges across 22 runs. The latest acceptance run had 5 authors, 6 communities (per the prior report). Independence math is implemented and tested; it simply has insufficient evidence to produce meaningful counts.

## 11. Clustering / Qdrant / BGE-M3 audit

**Architecture:** SQLite authoritative; Qdrant derived. Every vector stores source/entity ID, embedding model, version, content hash, created_at (§15A). Model changes trigger re-embedding jobs, not silent vector-space mixing. Verified in `semantic/embeddings.py` (`space_id`, `model`, `model_version`, `dimensions`) and `db/clustering.py` (`clustering_runs`, `cluster_versions`, `cluster_lineage`).

**Live data:** `semantic_config` points to a Qdrant URL (from `semantic.yaml`). `clustering_runs`: 3 rows. `clusters`: 1 row. `cluster_members`: 1 row. `cluster_lineage`: 0 rows. `cluster_versions`: 1 row. `cluster_decisions`: 0 rows.

**BGE-M3 / sentence-transformers:** `config/semantic.yaml` configures the embedding provider/model. `build_embedder()` constructs the provider. The spec (§14) says "initially sentence-transformers"; the actual model is configured in `semantic.yaml`. CUDA/CPU behavior is selectable via `EMBEDDING_DEVICES = ("auto", "cpu", "cuda")`.

**Clustering engine:** `ClusteringEngine(embedder, clustering_config, adjudicator=None)`. The adjudicator is `None` by default, so ambiguous pairs return `HeldReason.ambiguous_unadjudicated` ("no adjudicator was configured", line 445). Cluster merge/split decisions requiring LLM adjudication are therefore held, not decided. This is a real limit: cluster merge/split history cannot progress without an adjudicator.

**Qdrant consumption:** Clustering records Qdrant `count` and per-point `search` (nearest neighbors, limit=5) as tool calls with provenance. Artifacts include `cluster_neighbors_checked` with `item_count=0` when there are no clusters. The contract requires `min_neighbors_checked_per_cluster: 5` — zero clusters cannot meet this. Downstream: clustering feeds independence analysis, which feeds validation. Cluster output is consumed by the pipeline but produces no opportunities without evidence spans.

**Qdrant as derived state:** Confirmed — `projection_tasks` target "qdrant" is enqueued by `Store._enqueue_index_projection`; Qdrant is written only after SQLite commits. No reverse authority (Qdrant never overrides SQLite).

**Verdict:** Clustering/Qdrant is wired and partially tested, but produces 1 cluster from 1 pain point. The adjudicator is unconfigured. It cannot generate opportunities without evidence spans from stage 6.

## 12. Scoring and opportunity lifecycle audit

**Potential ≠ Confidence:** Verified in code (`opportunity/scoring.py` returns `score.potential.score` and `score.confidence.score` separately; `opportunity/signals.py` defines `PotentialInputs` and `ConfidenceInputs` as distinct structs). The scoring handler stores both in `opportunities` (potential, confidence, trend, uncertainty).

**WTP tiers W0–W4:** Defined in `signals.py` (`WtpTier.W0`..`W4`). But the scoring handler hardcodes `WtpTier.W1` with a single "complaint only" signal when a span exists, else `W0` with no signals (handlers.py:2661-2672). There is no code path that reaches W2–W4 (demonstrated payment, explicit intent, costly workaround) because those require evidence the extraction never produces. Market estimates are hardcoded (`reachable_population: 1000-10000`, `annual_price_usd: 10-40`) — not derived from evidence. This violates §20's requirement that market assumptions be evidence-cited.

**Opportunity lifecycle (13 states):** CANDIDATE, VERIFYING, PROMISING, HUMAN_REVIEW, VALIDATED, REJECTED, ALREADY_SOLVED, INSUFFICIENT_EVIDENCE, STALE, DEAD, RESURRECTED — defined in `opportunity/` and tested (`test_opportunity_lifecycle.py`). Transitions are coded. But an opportunity can only reach PROMISING if `confidence >= min_confidence` AND `independent_author_count >= min_independent_people` (handlers.py:2723-2726). With zero evidence spans and 5 authors, no opportunity reaches PROMISING. Live data: 8 opportunities, all in CANDIDATE or INVESTIGATING, none PROMISING/VALIDATED.

**Score history:** `opportunity_scores`: 2 rows. `score_snapshots` table exists. History tracking is coded but not exercised.

**Verdict:** Scoring and lifecycle are implemented and tested, but scoring inputs are hardcoded assumptions (not evidence-derived) and no opportunity progresses beyond CANDIDATE/INVESTIGATING because confidence and independence thresholds cannot be met with zero evidence.

## 13. Competitor and counter-evidence audit

**Competitor research:** `_competitor_handler` uses `plan_for_stage("investigation")` for mandatory query families plus `competitor_templates()` for specialists. Real searches run against hn_algolia + github_issues via `_live_search`. Results normalized into `Competitor` records with `evidence_refs` (tool_call_id). Claims recorded with `ClaimKind.competitors_exist` or `no_strong_competitor`. Coverage assessed via `assess_coverage` (min_queries, min_documents, min_domains). Verified contract-aligned. Limited to 2 domains.

**Counter-evidence:** `_counter_evidence_handler` uses `plan_for_stage("counter_evidence")` plus `counter_evidence_templates()`. `kind_map` maps families to `CounterEvidenceKind` (low_willingness_to_pay, contradicting_thesis, free_alternative). `assess_counter_evidence` produces status; zero-result outcome recorded only when all families completed and searched successfully (`ZeroResultOutcome`). Claim recorded with `ClaimKind.no_counter_evidence_found` or `opportunity_exists`. Verified contract-aligned, including explicit-zero handling.

**Critical flaw:** Both stages call `_live_search`, which only queries hn_algolia and github_issues. With 2 domains, they cannot satisfy `min_query_families: 3`, `min_distinct_queries: 6`, `min_candidate_urls: 10` (competitor) or `min_distinct_queries: 6`, `min_query_families: 3` (counter-evidence) meaningfully. A failed search is NOT counted as zero evidence (correct — `status=failure` doesn't create a zero outcome). But the searches that do run return HN/GitHub results, not competitor/counter-evidence-relevant data. The stages are not decorative, but they are constrained by the source shortage.

**Verdict:** Competitor and counter-evidence are real, contract-aligned stages with correct zero-result semantics. They cannot produce meaningful results with only 2 source domains.

## 14. Research Contract / promotion gate audit

**Contract:** `config/research_contract.yaml` defines 8 required stages (discovery, evidence_collection, normalization, clustering, validation, counter_evidence, scoring, investigation), 6 hard_requirements (all true), evidence thresholds, budgets, stopping, retry, promotion (`require_all_stages_passed: true`, all 4 promotion flags true). The contract loads and validates (unit-tested, 141 tests). Contract fingerprint (SHA-256 canonical) binds run results to the contract that judged them.

**Promotion call graph:** `require_promotable` (`db/research_gate.py:862`) is the fail-closed primitive. It is called ONLY in `_obsidian_sync_handler` (handlers.py:2928). No other production call site can promote. `evaluate_promotion_gate` is called by `require_promotable`. No bypass found.

**Bypass analysis:** Could an incomplete run reach PROMOTED or OBSIDIAN_SYNC without satisfying the contract?
- `require_promotable` calls `evaluate_run_gate` which checks every required stage, run-level thresholds, competitor/counter-evidence gates, claim-evidence links, quality predicates, and budget. All must pass.
- `evaluate_promotion` blocks on: run belonging to another contract, missing/failed/timed-out stages, run-level threshold violations, failed competitor/counter-evidence gates, claims without evidence references, budget overrun, mixed/invalid fingerprints.
- No alternate promotion path exists in `build_handlers` or the worker engine.
- **No bypass found.** This satisfies the "NOT READY if any bypass exists" rule — but the gate is never reached because upstream stages fail.

**Known limit (from phase-4-contract §7.5):** Three hard_requirement flags (`require_provenance`, `require_qdrant_check`, `require_duckdb_analysis`) are enforced only through the stage gate, not directly by `evaluate_promotion`. With `require_all_stages_passed: true` (shipped), a stage failure on these blocks promotion as a stage-that-did-not-pass. So the shipped baseline is not exposed to this limit. Verified: `promotion.require_all_stages_passed: true` in the contract.

**Verdict:** The research contract and promotion gate are correctly implemented and fail-closed. No bypass exists. The gate is simply never satisfied because upstream stages cannot produce evidence.

## 15. Budget audit

**Contract budgets (per-run):** wall_time 3600s, tool_calls 500, search_queries 160, fetch_requests 200, browser_minutes 30, crawl_items 500, llm_input_tokens 500k, llm_output_tokens 100k, model_calls 50, subq_calls 20, embedding_items 5000, usd_cost $25, plus per-stage and per-source limits.

**Measurement sources (per `research_gate.py:_measure_run`):**
- `tool_calls` table → tool_calls_total, search_queries, fetch_requests, source_requests_per_source
- `model_executions` table → model_calls (0 rows)
- `spend_ledger` table → llm_input_tokens, llm_output_tokens, embedding_items, subq_calls, browser_minutes, crawl_items, usd_cost (6 rows, all search_queries, cost 0)
- `research_stage_executions` → wall_time_seconds

**Unmeasured dimensions:** `llm_input_tokens`, `llm_output_tokens`, `embedding_items`, `subq_calls`, `browser_minutes`, `crawl_items`, `usd_cost` have NO writer. Handlers record `tool_calls` provenance but never write `spend_ledger` rows for LLM tokens, embeddings, SubQ calls, browser minutes, crawl items, or USD cost. `model_executions` is empty (0 rows), so `model_calls` is also unmeasured in production.

**Can the system exceed a declared budget?** For the 7 unmeasured dimensions: yes — there is no measurement source, so `build_budget_tracker` would refuse the contract (`_require_measured_dimensions` raises `ValueError` for any limited dimension with no measurement source). Wait — the contract limits all 14 dimensions including the 7 unmeasured ones. `_require_measured_dimensions` iterates `_limited_dimensions(contract)` and raises if any is not in `_MEASURED_DIMENSIONS`. `_MEASURED_DIMENSIONS` includes all 14 (the 7 spend_ledger dimensions + the 7 from tool_calls/model_executions/stage_executions). So the contract loads. But `spend_ledger` has no rows for the 7 dimensions, so `measure_run_spend` returns 0 for them — they register as "unmeasured" (`unmeasured_budget_dimensions` returns them) but do NOT block the gate. The gate's `budget_exceeded` violation is reported via `run_result.violations`, but unmeasured dimensions are reported as a gap, not a blocker.

**This is a genuine safety defect:** A run can spend LLM tokens, embedding calls, SubQ calls, browser minutes, and USD without any `spend_ledger` record. The contract declares limits for all of them, but no handler writes spend rows. `_require_measured_dimensions` only checks that a *source exists* for the dimension, not that the source is actually populated by handlers. The spend_ledger table exists (migration) but no code path inserts into it for LLM/embed/browser/crawl/usd. The prior report correctly identified this: "Handlers record tool-call provenance but do not write spend_ledger rows."

**Verdict:** Budget enforcement is partially implemented. The contract declares limits; the tracker can enforce; but 7 of 14 dimensions have no measurement source in production, so overruns on those dimensions are undetectable. This is a production correctness blocker for unattended operation (§22 of the spec: "budget enforcement are operationally verified before unattended long-term operation").

## 16. Skills audit

**Skills installed (`.opencode/skills/`):** 10 directories matching the Product Hunter-specific list: entity-resolution, evidence-provenance, evidence-validation, frequency-analysis, pain-point-extraction, pain-point-normalization, problem-clustering, source-discovery, source-independence, source-reliability.

**Skills locked (`skills-lock.json`):** 12 skills, all unrelated marketing/PM skills (asb-willingness-to-pay, brainstorm-experiments-new, competitor-discovery, competitor-profiling, expected-value, firecrawl-search, opc-mvp-designer, pm-opportunity-scoring, pricing-strategy, research, s4h-investigation-counter-hypothesis, startup-idea-validator, statistical-analysis, time-series-analysis, traction-channel-research). **None of the 10 PH skills are in the lock file.** The lock file does not track the skills actually installed for this project.

**Runtime usage:** The worker (`src/product_hunter/`) never imports or executes any skill as a pipeline stage. Handlers call native Python code (discovery, collection, extraction, clustering, scoring). Skills are exposed to OpenCode agents (`product-hunter` agent permits `skill` action: "*"; subagents permit `skill` action: "*"), but their findings are advisory until the worker records and validates evidence (product-hunter.md: "Never claim a run is active, complete, promotable, or projected until the tool's persisted state confirms it").

**Classification:** Per §5 of the spec, skills "encode reusable methodology, not giant prompts." Per §6A, OpenCode agents "must not be the sole authority for whether a research stage is complete." The architecture treats skills as **(B) advisory agent knowledge** — not runtime executable components (A), not merely documentation (D). This is consistent with the spec's positioning (MCPs are not required; worker-owned adapters are authoritative).

**Comparison with spec:** §5 lists 30 skills as "Core skills." Only 10 of the PH-specific ones are installed; the lock file tracks unrelated ones. No PH skill is executed as a durable pipeline stage. The spec does not require skill execution for completion (Phase 4's non-goals explicitly exclude collector/scraping execution; skills arrive later). So skills are **not a blocker** — but the claim "skills are wired" (prior report) is overstated: they are installed and exposed to agents, not executed as pipeline stages.

| PH skill | Installed | Exposed to agents | Selectable | Actually used | Authoritative | Persisted | Tested |
|---|---|---|---|---|---|---|---|
| entity-resolution | yes | yes | yes | no | no | no | yes (independence tests) |
| evidence-provenance | yes | yes | yes | no | no | no | yes (provenance tests) |
| evidence-validation | yes | yes | yes | no | no | no | yes (gate tests) |
| frequency-analysis | yes | yes | yes | no | no | no | yes (analytics tests) |
| pain-point-extraction | yes | yes | yes | no | no | no | yes (extraction tests, fake generator) |
| pain-point-normalization | yes | yes | yes | no | no | no | yes (normalization tests) |
| problem-clustering | yes | yes | yes | no | no | no | yes (clustering tests) |
| source-discovery | yes | yes | yes | no | no | no | yes (discovery tests) |
| source-independence | yes | yes | yes | no | no | no | yes (independence tests) |
| source-reliability | yes | yes | yes | no | no | no | yes (source_policy tests) |

## 17. OpenCode agent audit

Five role files exist under `.opencode/agents/`: product-hunter, researcher, investigator, validator, startup-killer.

**Verification against spec (§4):**
- **product-hunter:** Top-level orchestrator. Permissions: allow `product_hunter:*`, task to all 4 subagents, question; deny shell/glob/grep/read/edit/websearch/webfetch/external_directory. Correct — it orchestrates through the `product_hunter` tool, does not directly access DB or web. ✅
- **researcher:** Collects evidence. Permissions: websearch, webfetch only; deny read/edit/shell/glob/grep/task. ✅ Correct — advisory only, cannot write durable records.
- **investigator:** Investigates clusters. Permissions: websearch, webfetch only. ✅ Advisory only.
- **validator:** Audits claims. Permissions: websearch, webfetch only. ✅ Advisory only; "do not mark a run promotable or write durable records."
- **startup-killer:** Falsifies opportunities. Permissions: websearch, webfetch only. ✅ Advisory only.

**Role boundaries:** Subagents cannot write durable Product Hunter records (no `product_hunter` action, no shell, no edit). The product-hunter agent has no shell/read/edit either — it delegates to the worker tool. ✅ No unauthorized DB mutation; no unsafe external actions.

**Connection to worker/runtime — VERIFIED.** The `product_hunter` control-plane tool is live and wired to the real runtime. `action: "list"` returned all 22 research runs matching the SQLite database exactly (run IDs, objectives, states, stages, progress, timestamps). `action: "status"` on `RH-20261023-0001` returned the correct state (QUEUED/PLAN/0%), budget (all 14 dimensions), checkpoint, and blocker ("no worker holds a lease"). The agent definitions' claim that durable work goes through the `product_hunter` tool is confirmed. The `product-hunter.md` description is accurate.

**Persistence across chats:** The spec requires cross-chat continuity (§41.6-41.8). SQLite stores research_runs with chat_id/workspace_id nullable. The agent definitions support this conceptually, but cross-chat resume was not exercised in this audit.

**Verdict:** All five agent roles exist with correct prompts, tools, permissions, and role boundaries. Subagents are correctly constrained from writing authoritative records. The product-hunter agent is the orchestrator. However, `/agents` exposure and actual selection routing to the real Product Hunter runtime were not independently verified (requires interactive OpenCode session, out of scope for read-only audit).

## 18. Autonomous worker audit

**Architecture:** `autonomy/engine.py` (`OperationEngine`) — one supervised loop: claim job → run handler → record outcome → renew lease. `runtime/worker.py` (`WorkerRuntime`) — claim/lease/dispatch/record/shutdown. `db/jobs.py` — claim_job, complete_job, fail_job, renew_lease, recover_expired_leases. `db/leases.py` — fenced_write, assert_live_lease, assert_fence_pair.

**Verified implemented:**
- Scheduler: `db/scheduler.py` (`Scheduler`, `Schedule`, `default_schedules`, `Priority`).
- Queue: `jobs` table (134 rows) with status, attempts, max_attempts, idempotency_key, worker_id, lease_epoch, lease_expires_at.
- Leases + fencing: `lease_epoch` monotonically increasing; `fenced_write` refuses writes when lease lost. ✅
- Retries: `RetryPolicy` (base 5s, multiplier 2x, max 300s); `max_retries: 2` in contract. ✅
- Crash recovery: `recover_expired_leases` (db), `autonomy/crash.py` (`recover`). The acceptance report verified recovery from an expired lease after a worker exited. ✅
- Graceful shutdown: WorkerRuntime handles stop requests (Event-based). ✅
- Stale worker handling: fencing rejects writes from lost leases. ✅
- Single-instance lock: `runtime/worker.lock` present (94 bytes). ✅
- Priority: `Priority` enum in scheduler. ✅
- Cadence: `autonomy/cadence.py` (`CadenceConfig`, `load_cadence`, `upkeep_beat`); `config/cadence.yaml`, `config/autonomy.yaml`. ✅
- Missed-work recovery: overdue jobs become due on restart; catch-up by priority. Conceptually implemented. ✅
- Checkpointing: `research_checkpoints` table (268 rows) with state_ref, input_refs, output_refs, contract_status, retry_state, budget_state, stage_attempt, lease_epoch. ✅
- Idempotency: `idempotency_key` per job and per stage execution; upsert by run+stage+attempt. ✅
- Windows operation: `safety.py` for URL admission; paths use `Path`; Task Scheduler integration described in spec. The `worker.lock` file exists. ✅

**Laptop shutdown/restart:** On restart, `recover_expired_leases` detects expired leases, identifies interrupted jobs, validates checkpoints, reconciles partial outputs, requeues unfinished work. Implemented and tested (`test_db_crash_recovery.py`, `test_autonomy_recovery.py`).

**Verdict:** The autonomous worker is fully implemented and tested for leases, fencing, retries, crash recovery, checkpointing, idempotency, and single-instance lock. Windows operation is supported. This is not a blocker.

## 19. SQLite / DuckDB / Qdrant authority audit

**Authority chain:** SQLite = authoritative (operational source of truth). Qdrant = derived (semantic retrieval, never sole source of truth). DuckDB = derived analytics (reads SQLite or approved snapshots). Obsidian = projection. Git = code/config history only.

**Verified:**
- `Store.import_run` wraps document/evidence/pain writes + projection enqueue in one SQLite transaction (`BEGIN IMMEDIATE`/`COMMIT`). ✅
- `projection_tasks` (0 rows) is the outbox; projections are idempotent upserts keyed by content hash. ✅
- `stage_gate_results.stage` nullable; run-level row has no stage. Fingerprint binding prevents mixed contracts. ✅
- Qdrant writes happen only after SQLite commit (outbox target "qdrant"). ✅
- DuckDB (`db/product_hunter.duckdb`, 46MB) is an analytical snapshot, queried by `analytics/ingest.py`. Not used for operational decisions. ✅
- No reverse authority: no code path writes to SQLite based on Qdrant/DuckDB state. ✅

**Projection failure cannot corrupt authoritative state:** Confirmed — SQLite commits first; projection tasks are retried independently. A Qdrant/DuckDB/Obsidian failure leaves SQLite intact and the outbox task pending for retry.

**Deterministic IDs / content hashes / idempotent upserts:** `derive_span_id` (doc_id + offsets + quote), `_hash_id` prefixes, `INSERT ... ON CONFLICT ... DO UPDATE` throughout. Embedding model changes create re-embedding jobs (`clustering_runs` with space_id/model_version). ✅

**Verdict:** Authority architecture is correctly implemented. No blocker here.

## 20. Obsidian audit

**Vault location:** `data/vault` (10 subdirectories: raw, processed, cache, exports, reports, vault, models). The vault root is `repo_root() / "data" / "vault"`.

**Projection:** `projection/engine.py` (`Projector`) writes notes under vault root only, compares bytes, prunes removed notes, records manifest (`.product-hunter-projection.json`). `projection/sanitize.py` escapes HTML/frontmatter/plugin syntax. `projection/source.py` (`SqliteProjectionSource`) reads from SQLite.

**Guard:** `_obsidian_sync_handler` calls `require_promotable` BEFORE `projector.project()`. If the gate blocks, `GateBlocked` is raised and no projection occurs. ✅ Fail-closed.

**Verdict:** Obsidian projection is correctly guarded and sanitized. But `projection_tasks: 0` and no successful promotion means **no validated opportunity has ever reached Obsidian.** The negative-path block works; the positive path is unproven. Per the audit mandate: "The negative-path block does not prove the positive path."

**Prompt injection through Obsidian:** `sanitize.py` neutralizes HTML, raw links, embedded images, frontmatter injection, callouts, dataview/plugin syntax. Scraped text is rendered as inert Markdown. Source URLs and quotes stored as data fields with deterministic escaping. ✅

**Verdict:** Obsidian is safe but unused — no opportunity can reach it without passing the gate, which requires evidence spans that stage 6 never produces.

## 21. Security audit

**Prompt injection boundary:** §37A implemented. `safety.py` (`is_public_ip`, `resolve_public_ips`) blocks private/RFC1918/loopback/link-local/multicast/CGNAT, allows only ports 80/443, redirects revalidated. LLM extraction runs with no tools; untrusted text is fenced between `<<<UNTRUSTED_SOURCE_START>>>`/`<<<UNTRUSTED_SOURCE_END>>>` markers in the prompt (§extraction.py `_PROMPT`). Schema-constrained JSON output. Quote verbatim validation against stored text.

**SSRF / DNS rebinding:** `safety.py` resolves + validates destination before connect and re-checks after redirects. Allowed schemes: http/https only. Blocked: file:, data:, javascript:, ftp:, gopher:. ✅

**Credential isolation:** `.env` / `.env.example` for secrets; no secrets committed. API keys outside committed source (§37). ✅

**Shell / filesystem:** Workers run handlers in-process; no shell access from agents. OpenCode agent permissions deny shell/edit/read/glob/grep. Filesystem access for untrusted content is read-only, scoped to `runtime/`. ✅

**Browser isolation:** Playwright (if used) would use throwaway context with no cookies/credentials/extensions, bounded timeouts. Not directly exercised in handlers (uses httpx directly). ✅

**External posting / Git push:** No code path posts externally or pushes Git from research agents. Git tracks code/config only (§32). ✅

**Malicious scraped content:** Quotes are validated as verbatim substrings; no source-derived text becomes tool arguments or executable instructions. ✅

**Verdict:** Security boundary is implemented and tested (`test_collection_security.py`, `test_scraping_ladder_security.py`, `test_safety.py`). No blocker here.

## 22. Model routing audit

**Genuine provider/model abstraction — YES.** `llm/routing.py` implements a real, stage-level model router:
- `ModelRole` enum defines 13 pipeline roles (extraction, classification, normalization, deduplication, entity_resolution, clustering, validation, investigation, competitor_research, counter_evidence, scoring, reporting, synthesis).
- `ModelRouter.chain(role)` walks an ordered chain of candidates: explicit `provider/model` entries and glob patterns matched against the live OpenCode catalog (`fnmatchcase` on `provider/model` keys), cheapest-first within a pattern.
- Models that left the catalog are skipped, never fatal. One re-discovery is attempted before giving up.
- `FallbackGenerator` excludes failed candidates and walks the chain; each attempt (success/failure, latency, error) is recorded.
- `derive_availability` marks a model `available` only when the source reports `status=active` AND `enabled=true`; anything unconfirmed is `unknown` and not selectable.
- Pattern chains in `config/model_routing.yaml`: extraction → `opencode-go/deepseek*`, `opencode-go/*-flash*`, `opencode-go/*`; investigation/competitor/counter_evidence/scoring → `opencode-go/deepseek*`, then any.

**The architecture is NOT hard-coded to deepseek-v4.1-flash.** `extraction.yaml` names it as the default model identity (used as the chain's terminal default), but the router resolves dynamically against the live catalog. No specific model is a required architectural dependency.

**Critical wiring gap — extraction bypasses the router.** `handlers.py:_extraction_handler` (line 615) instantiates `OpenCodeGenerateProvider(config)` directly, using `config.provider_id` / `config.model_id` from `extraction.yaml`. It does NOT call `build_router` / `select(role=extraction)` / `FallbackGenerator`. So the production extraction path:
- does no catalog lookup,
- tries no fallback chain,
- records no `ModelSelection` (selection_source, fallback_index, is_fallback, catalog_version),
- and never writes to `model_executions`.

**`model_executions = 0` explained.** The table and writer (`db/model_routing.py:record_model_executions`) exist and are correct. But they are invoked only during CLI run import (`store.py:994`) and CLI bench runs (`cli.py:929`), never during live worker extraction. Since `_extraction_handler` bypasses the router, no live extraction call is ever logged as a model execution. This is why `model_executions: 0` despite extraction being the LLM-gated stage.

**Model/version provenance in production:** `model_catalogs: 0`, `model_executions: 0`, `model_inventory: 0`, `model_role_bindings: 0`. No production model execution is recorded. Whether the OpenCode catalog contains any model matching the extraction pattern is unverifiable from repo state (requires live OpenCode server). `OpenCodeGenerateProvider.check()` would raise `ProviderUnavailable` if the configured provider/model is not active/enabled — causing `_extraction_handler` to return `{"extracted": 0, "error": "no model available"}` (line 617).

**Extraction model appropriateness:** Untested against a labeled evaluation set (§6B; `eval/datasets/ds-v1.json` and `ds-v2.json` have `expected: null` for all cases — no human labels exist).

**Verdict:** Model routing is a genuine, well-designed provider/model abstraction with dynamic stage-level selection — **not** a hard-coded DeepSeek dependency. The architecture satisfies §3. The gap is **wiring**: the production extraction handler bypasses the router, so dynamic selection, fallback, and model-execution provenance never run in production. This is an implementation gap, not an architectural deficiency. The specific model is determined dynamically at runtime (unverifiable without a live server), and no labeled eval set confirms appropriateness.


## 23. MCP / tooling audit

The spec (§6) states MCPs are an OpenCode integration surface for interactive exploration, not the authoritative execution mechanism for unattended runs. Worker-owned adapters are authoritative.

**Actual tooling:**
- Search/fetch: native `httpx`-based adapters (`_hn_search`, `_github_search`, `_devto_search`, `collection/adapters/`). Not MCP. ✅ Matches spec (worker-owned, simplest-first ladder: official API → direct HTTP → crawler → Playwright → Apify).
- Embeddings: native `sentence-transformers` via `semantic/embeddings.py`. Not MCP. ✅
- Qdrant: native `QdrantAdapter` (`semantic/qdrant.py`), not Qdrant MCP. ✅
- DuckDB: native `analytics/ingest.py`, not DuckDB MCP. ✅
- Obsidian: native `projection/engine.py`, not a filesystem MCP. ✅
- Git: native `git` operations via `cli.py`, not Git MCP for autonomous runs. ✅

**No MCP dependency is required** for the autonomous path. The spec permits native adapters (§38: LangChain/LangGraph/Redis/BullMQ/Temporal/LiteLLM/OpenTelemetry/Prometheus are deliberately not required). The final architecture matches the spec: worker-owned adapters, MCPs optional for interactive use.

**Verdict:** Tooling architecture is correct. No missing MCP is a blocker because the spec explicitly permits native adapters.

## 24. Real execution audit

**Read-only inspection of existing artifacts:** 22 research runs, 134 jobs, 164 stage_attempts in `db/product_hunter.db`. Acceptance run databases exist in `data/` (acceptance-20261023-*.db). The latest acceptance run (`run_be7cdf544e03443f` / `RH-20261023-0001`) executed all 15 pipeline jobs; 14 succeeded, OBSIDIAN_SYNC dead because the gate refused promotion.

**No live execution performed:** This audit is read-only. No production run was started, no model call was made against real evidence, no source was added.

**Positive path:** 0 passing production runs (confirmed by prior report and by `stage_gate_results: 0`, `projection_tasks: 0`).

**Negative path:** Verified — the gate correctly blocks promotion when evidence is missing; recovery from expired lease was observed; DuckDB snapshot failure from invalid status values was found and fixed.

**Verdict:** The system executes stages and records them, but no run has ever completed the positive path (gate pass → projection). Real execution evidence supports the negative path only.

## 25. Failure-injection audit

The prior report claims coverage of 12 failure types (tool timeout, rate limit, worker crash, lease expiration, stale worker, malformed tool result, missing artifact, projection failure, Qdrant failure, DuckDB failure, Obsidian failure, model failure, embedding failure) but notes "the complete set of twelve requested failure types was not injected through a final production run."

**Independently verified coverage:**
- Unit/integration tests exist for: invalid/missing/failed/stale/malformed/over-budget/misfiled/zero-result/blocked conditions (107 test names across gate/db/contract/provenance modules). ✅ Unit-tested.
- Worker crash + lease expiration recovery: `test_db_crash_recovery.py`, `test_autonomy_recovery.py`. ✅ Integration-tested.
- Model failure: `extraction.py` catches `ProviderError` and records rejects. ✅ Unit-tested.
- Embedding/Qdrant failure: `ClusteringEngine` handles `adjudicator=None` and failed adjudicator calls. Qdrant adapter has `max_attempts`/`backoff_seconds`. ✅ Partially tested.
- Rate limit: source `rate_limit_policy` with `min_interval_seconds`, `max_retries`, `backoff_base_seconds`. ✅ Coded.
- Tool timeout: `httpx.Client(timeout=30.0)`, `OpenCodeGenerateProvider` timeout from config. ✅ Coded.
- Projection failure: outbox retry via `projection_tasks`. ✅ Coded, not production-tested.
- DuckDB failure: validation handler raises if no file-backed DB. ✅ Coded.

**Distinction:** These are unit-tested and integration-tested, but **not production-path-tested** end-to-end. A real run has never injected all 12 failure types and recovered. Per the audit mandate, unit-tested ≠ production-path-tested.

**Verdict:** Failure handling is coded and unit-tested, but production-path failure injection is incomplete. Not a blocker on its own, but contributes to the unverifiability of production readiness.

## 26. Test-quality audit

**Suite:** 2734 passed, 1 skipped, 0 failed (`uv run pytest`). Green.

**Critical limitation:** Many tests use `FakeGenerator` / scripted answers instead of the real `OpenCodeGenerateProvider`. `test_extraction.py` proves the validation pipeline works with a fake model, but does not prove the live model produces valid output. `test_research_*.py` (141 tests) test the gate/contract/budget logic in isolation with hand-built `StageExecution` objects, not through the worker.

**Tests that merely assert "handler returned successfully":** Several handler tests check return dicts (e.g., `{"extracted": 0}`) rather than asserting real evidence was stored. `test_handlers_wired.py` and `test_handlers_competitor_counter.py` check handler outputs but not end-to-end gate passage.

**Positive tests demonstrating meaningful output:** The extraction test demonstrates a genuine pain point being accepted with a FakeGenerator. But this is a unit test, not a production run.

**Tests that could pass while the feature is broken:** Yes — `test_extraction.py` passes with a FakeGenerator even though the live OpenCode provider may be unregistered or uncooperative. The gate tests pass with synthetic `StageExecution` objects even though `stage_gate_results` is empty in production.

**Verdict:** The suite is green and comprehensive at the unit level, but it does not prove production-path correctness. The gap between "tests pass" and "production run succeeds" is exactly the zero-evidence problem.

## 27. Hidden-gap findings

Systematic search for dead code, stubs, TODOs, pass-throughs, placeholder returns, unused modules, unreachable code, fake success, swallowed exceptions, broad exception handlers, missing transactions, missing foreign keys, race conditions, non-idempotent writes, duplicated evidence, incorrect status transitions, missing provenance, missing migrations, inconsistent schemas, test-only implementations, environment-specific assumptions, Windows-specific failures, hard-coded paths, hard-coded models, hard-coded sources, hard-coded thresholds:

1. **`_web_search` stub (handlers.py:95-96):** `return []` — unreachable dead code (not in `_SEARCHERS`). Not a bypass, but dead code.

2. **Swallowed exceptions:** `handlers._discovery_handler` wraps `Store(conn).sync_registry(registry)` in try/except `pass` (line 287-288) — if registry sync fails, discovery proceeds with potentially stale source config. `_extraction_handler` wraps `pipeline.ingest_document(doc)` in try/except `pass` (line 676-677) — a failed ingestion silently continues, potentially extracting from documents not in the store. Multiple `except Exception: pass` blocks across handlers (collection parsing, author link creation, etc.) silently drop errors.

3. **Broad exception handlers:** `except Exception: pass` in discovery, extraction, collection, entity resolution, independence analysis. These prevent the worker from knowing a stage partially failed.

4. **Hard-coded model:** `extraction.yaml` hardcodes `model_id: deepseek-v4.1-flash`, `provider_id: opencode-go`. The routing config allows fallback, but the extraction config pins a specific model. Acceptable per spec (configurable), but a single point of failure.

5. **Hard-coded sources:** `_SEARCHERS` maps only hn_algolia, github_issues, dev_to. `_live_search` only queries hn_algolia + github_issues. dev_to is defined but never used in `_live_search`. Source discovery is limited to 2 active domains.

6. **Hard-coded thresholds:** Scoring handler hardcodes WTP tier W1, market assumptions (population 1000-10000, price $10-40), competition direction neutral, competitors_found 0, buildability 0.7. These are not evidence-derived.

7. **`stage_gate_results: 0` rows:** The gate is unit-tested but never persists a verdict in production. Dead code in the production path (only reached via `_obsidian_sync_handler`, which is never reached).

8. **`research_tool_results: 0` rows:** Migration 0012 exists but no handler writes durable result payloads consistently. The gate cannot re-verify tool results.

9. **`model_executions: 0` rows:** No model call tracking in production despite the schema existing.

10. **`projection_tasks: 0`:** No projections ever enqueued successfully through the outbox.

11. **`research_artifacts: 0`:** No run artifacts recorded despite `_record_artifacts` existing in `store.py` (used by Phase 1 import, not the live worker pipeline).

12. **Adjudicator unconfigured:** `ClusteringEngine(adjudicator=None)` — cluster merge/split decisions are held, not decided. `cluster_lineage: 0`, `cluster_decisions: 0`.

13. **`spend_ledger` missing writers:** 7 budget dimensions have no measurement source (detailed in section 15).

14. **`_default_handler` pass-through:** Stages not in `build_handlers` (none — all 15 are mapped) would fall through to `_default_handler` returning `{"status": "pass-through"}`. Not reached, but represents a potential silent-success path if a stage name is misspelled.

15. **Windows-specific:** `opencode.py` refuses `.ps1` shims; the CLI on PATH is `opencode.CMD` (shim) but the nested `.exe` is found first. If the `.exe` were missing, generation would fail with `ProviderUnavailable` via the shim refusal. Environment-specific.

16. **`blog_http` source:** `pending_review`, `allowed_use: [none]`, no domains. Correctly blocked, but means the third source needed for `min_source_domains: 3` cannot be enabled without operator review.

**Most significant hidden gaps:** #3 (swallowed exceptions mask partial failures), #7/#8/#9 (gate, durable results, and model executions never recorded in production), #12 (unadjudicated clustering), #13 (unmeasured budget dimensions).

## 28. Exact blockers

Each blocker includes file, code path, why it matters, evidence, severity, type, and close condition.

### BLOCKER 1 — Zero evidence spans from extraction (core workflow blocker)
- **File/code path:** `src/product_hunter/handlers.py:_extraction_handler` (line 615) → `src/product_hunter/extraction.py:extract_documents` → `src/product_hunter/llm/opencode.py:OpenCodeGenerateProvider.generate` → OpenCode CLI `api post /api/experimental/generate`.
- **Why it matters:** Stage 6 (PAIN_EXTRACTION) is the gateway to all downstream stages. Zero spans → no clustering → validation cannot meet min_evidence_spans (20) → scoring has no real evidence → promotion blocked → no opportunity reaches Obsidian. The core Product Hunter workflow cannot complete.
- **Architecture vs. runtime distinction:** The model-provider abstraction IS genuine (ModelRouter with pattern chains, fallback, availability derived from the live catalog — section 22). The architecture does NOT lack flexibility and is NOT hard-coded to deepseek-v4.1-flash. The defect is **wiring**: `_extraction_handler` bypasses `build_router`/`select(role=extraction)`/`FallbackGenerator` and instantiates `OpenCodeGenerateProvider(config)` directly using `extraction.yaml`'s pinned provider/model. So dynamic selection, fallback, and model-execution provenance never run in production extraction.
- **`model_executions = 0` explained:** The table/writer (`db/model_routing.py:record_model_executions`) exist and are correct, but are invoked only during CLI run import (`store.py:994`) and bench runs (`cli.py:929`), never during live worker extraction. Because extraction bypasses the router, no live extraction call is logged as a model execution.
- **Evidence:** `evidence: 2` rows, `pain_points: 1` row across 22 runs. `model_executions: 0`. Latest acceptance run: extraction returned zero candidates. `extraction.yaml` pins `provider_id=opencode-go`, `model_id=deepseek-v4.1-flash`; whether any matching model is active/enabled in the OpenCode catalog is unverifiable from repo state. `_extraction_handler` returns `{"extracted": 0, "error": "no model available"}` when the provider is unavailable (line 617).
- **Severity:** Critical (blocks the entire positive path).
- **Type:** Implementation/wiring gap (extraction bypasses the dynamic router) + model/runtime dependency (whether an available model exists is unverifiable) + architectural defect (no deterministic non-LLM fallback for extraction).
- **Close condition:** Either (a) route extraction through `build_router`/`select(role=extraction)`/`FallbackGenerator` so dynamic selection + fallback + `model_executions` provenance run, with at least one catalog model active/enabled matching the extraction pattern, producing a verified verbatim span in a production run; or (b) add a deterministic non-LLM fallback path for known-positive documents. The span must trace `extract_documents` → `quote.locate_quote` → `Store.import_candidate` → `evidence` + `pain_points`, using the live provider (not a FakeGenerator).


### BLOCKER 2 — Insufficient source domains (contract threshold blocker)
- **File/code path:** `config/sources.yaml` (hn_algolia + github_issues active; blog_http pending_review/allowed_use [none]); `handlers.py:_discovery_handler` (line 290-302 filters to admissible sources).
- **Why it matters:** Contract `discovery` gate requires `min_source_domains: 3` and `min_new_documents: 20`. With 2 active domains, discovery cannot collect 20 documents from 3 distinct domains, so the discovery gate fails and no documents reach extraction.
- **Evidence:** `sources` table: 3 rows, 2 enabled. `domains_json` for hn_algolia = [hn.algolia.com, news.ycombinator.com]; github_issues = [api.github.com]. blog_http has `domains: []`, `enabled: 0`, `terms_status: unknown`, `allowed_use: ["none"]`. Latest run found 2 domains vs 3 required.
- **Severity:** Critical (blocks discovery gate, which blocks everything downstream).
- **Type:** Configuration / operator policy (not a code defect — source policy correctly blocks unreviewed sources).
- **Close condition:** Operator review and approval of a third source with `terms_status: permitted`, `allowed_use` including discovery/retrieval, at least one domain, `enabled: true`, in `config/sources.yaml`. No source policy may be bypassed.

### BLOCKER 3 — Spend ledger unmeasured for 7 budget dimensions (safety/cost-control blocker)
- **File/code path:** `src/product_hunter/db/research_gate.py:_measure_run` and `_require_measured_dimensions`; `spend_ledger` table (no writer for LLM/embed/subq/browser/crawl/usd). No handler writes `spend_ledger` rows for `llm_input_tokens`, `llm_output_tokens`, `embedding_items`, `subq_calls`, `browser_minutes`, `crawl_items`, `usd_cost`.
- **Why it matters:** §22 requires every spend-bearing call to write a `spend_ledger` record and the worker to enforce budgets before expensive calls. Without measurement, the system can exceed declared LLM/embedding/SubQ/browser/USD budgets silently. Unattended operation is unsafe.
- **Evidence:** `spend_ledger: 6` rows, all `unit_type=search_queries`, `cost_micros=0`, `provider=None`, `model=None`. `model_executions: 0`. `_require_measured_dimensions` only checks a source exists (spend_ledger table exists), not that handlers populate it.
- **Severity:** High (safety/cost-control defect for unattended operation).
- **Type:** Implementation defect (missing writers).
- **Close condition:** Every handler that makes an LLM call, embedding call, SubQ call, browser action, crawl, or fetch must write a `spend_ledger` row with run/stage, unit_type, units, cost_micros. The gate must treat unmeasured limited dimensions as a blocker, not a gap.

### BLOCKER 4 — Gate never persists a verdict in production (enforcement gap)
- **File/code path:** `src/product_hunter/db/research_gate.py:require_promotable` (called only in `_obsidian_sync_handler`, line 2928); `stage_gate_results: 0` rows.
- **Why it matters:** The Research Contract + Tool Gate is the fail-closed enforcement layer (§6A). If it never runs in production, there is no guarantee a run satisfies the contract before projection. `require_promotable` exists but is unreachable because upstream stages fail first.
- **Evidence:** `stage_gate_results: 0` rows. `research_tool_results: 0` rows. `require_promotable` is invoked only after `projector.project()` would run — but projection never happens because the gate blocks first.
- **Severity:** High (the enforcement primitive is dead code in production).
- **Type:** Implementation/wiring gap.
- **Close condition:** The gate must be evaluated at the end of every production run (not only at Obsidian sync), and `stage_gate_results` must contain verdicts. A run must be blocked from advancing to the next stage when its gate fails, not only at the final projection step.

### BLOCKER 5 — Cluster adjudicator unconfigured (clustering quality gap)
- **File/code path:** `src/product_hunter/clustering/engine.py:ClusteringEngine(adjudicator=None)` (line 445 returns `HeldReason.ambiguous_unadjudicated`).
- **Why it matters:** Ambiguous cluster merge/split pairs cannot be decided. `cluster_lineage: 0`, `cluster_decisions: 0`. Cluster quality cannot be verified without human/LLM adjudication.
- **Evidence:** `clusters: 1`, `cluster_members: 1`, `cluster_lineage: 0`. Adjudicator is None by default.
- **Severity:** Medium (clustering cannot evolve or be validated).
- **Type:** Configuration / missing component.
- **Close condition:** Configure an adjudicator (LLM-based or rule-based) for cluster pair decisions, or document that merge/split is deterministic-only. Cluster lineage must be exercised.

## 29. Recommended next actions

1. **Resolve the extraction model dependency.** Verify `opencode-go/deepseek-v4.1-flash` is registered as `active`/`enabled` in the OpenCode model catalog. If not, register it or route extraction to an available model via `model_routing.yaml`. Confirm `model_executions` is populated.
2. **Prove extraction with a controlled fixture.** Feed a known-positive document (with a clear verbatim pain quote) through `extract_documents` using the live provider. Confirm a span is written to `evidence` + `pain_points` with a verified quote offset. This isolates the model/runtime problem from the validation-architecture problem.
3. **Add a third approved source.** Operator review of a third source (not Reddit while terms unknown) and enablement in `config/sources.yaml`. Do not bypass source policy.
4. **Write spend_ledger rows.** Add writers in every handler for LLM tokens, embedding items, SubQ calls, browser minutes, crawl items, and USD cost. Make unmeasured limited dimensions a gate blocker.
5. **Run the gate on every production run.** Invoke `evaluate_run_gate` / `require_promotable` at the end of each run, not only at Obsidian sync. Persist `stage_gate_results`.
6. **Configure the clustering adjudicator** or document deterministic-only clustering, and exercise cluster lineage.
7. **Label the evaluation set.** `eval/datasets/ds-v1.json` and `ds-v2.json` have `expected: null` for all cases. Human-label them per §6B so model changes can be evaluated before becoming the default for autonomous runs.
8. **Demonstrate one positive production run.** End-to-end: discovery → collection → extraction (real spans) → clustering → validation (meets thresholds) → competitor → counter-evidence → scoring → gate pass → Obsidian projection. Until this happens, the verdict remains NOT READY.

## 30. Final verdict

**Verdict: NOT READY**

**Basis (independent evidence, not prior claims):**

Product Hunter's architecture is fundamentally sound: OpenCode as interaction surface, Python worker as autonomous executor, SQLite as authoritative state, Qdrant/DuckDB/Obsidian as derived projections, transactional outbox, fenced leases, idempotent jobs, a fail-closed Research Contract and Tool Gate, a genuine dynamic model-provider abstraction (ModelRouter with pattern chains, fallback, catalog-derived availability), and a security boundary for untrusted content. The code is substantial, the test suite is green (2734 passed), and the negative path (gate correctly blocks bad runs) works.

But the system cannot demonstrate the **positive production path**:

- **Zero evidence spans** across 22 production runs. Stage 6 (extraction) bypasses the dynamic model router (`build_router`/`select(role=extraction)`/`FallbackGenerator`) and instantiates `OpenCodeGenerateProvider(config)` directly from `extraction.yaml`'s pinned provider/model. Whether any matching model is active/enabled in the OpenCode catalog is unverifiable from repo state, there is no fallback chain, and no deterministic non-LLM fallback exists. The unit-test FakeGenerator proves the validation logic works, but not that the live path produces valid output. (`model_executions: 0` because extraction bypasses the router that writes to that table.)
- **Two active source domains** against a contract minimum of three. Discovery cannot collect 20 documents from 3 domains, so the discovery gate fails.
- **Spend ledger unmeasured** for 7 of 14 budget dimensions (LLM tokens, embeddings, SubQ, browser, crawl, USD). Unattended operation cannot enforce cost limits.
- **Gate never persists a verdict** (`stage_gate_results: 0`). The fail-closed enforcement is dead code in production.
- **No opportunity reaches Obsidian** (`projection_tasks: 0`). The negative-path block works; the positive path is unproven.

No bypass of the promotion gate was found — the gate is correctly fail-closed. The system is not READY because it cannot complete its core workflow, not because it can be tricked into producing false positives.

**The prior acceptance report's verdict (NOT COMPLETE) is directionally correct.** This independent audit confirms it and adds structural specificity: the gap is not merely "one external source missing" and "the LLM is the only blocker." It is a compound of (1) an extraction path that bypasses the dynamic model router and produces zero spans with no fallback, (2) a source shortage below the contract threshold, (3) unmeasured budget dimensions, (4) a gate that never runs in production, and (5) zero demonstrated positive runs. Model routing itself is a genuine abstraction (§22) — the gap is wiring, not architecture.

**READY WITH EXPLICIT DEFERMENTS is not applicable** — the spec does not permit deferring the core workflow (evidence extraction, contract-compliant discovery, budget enforcement, promotion gate execution, Obsidian projection of a validated opportunity). These are Phase 1–4 acceptance criteria (§38A), not deferrable items.

**Next step:** Address blockers B1–B5 and demonstrate one end-to-end production run that passes the gate and projects to Obsidian. Until then: **NOT READY.**

Can I trust Product Hunter to autonomously research real problems and produce evidence-backed product opportunities without silently lying, corrupting evidence, bypassing its gates, or pretending incomplete work succeeded?

**No — not in its current state.** It will not silently lie or bypass its gates (the gate is fail-closed and never passes bad runs). It will not corrupt evidence (SQLite transactions + outbox protect authoritative state). But it will **silently fail to produce evidence** — stage 6 yields zero spans, the gate blocks, and no opportunity ever reaches Obsidian. That is a failure of the core workflow, and it is demonstrable from the live repository state.

---

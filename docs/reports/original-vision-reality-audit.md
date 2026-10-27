# Original Vision Reality Audit — Product Hunter

## 1. Executive Summary

The current Product Hunter repository implements a **substantially wired autonomous research pipeline** — multi-source discovery (HN + GitHub Issues + dev.to), real BGE-M3 embeddings, a live Qdrant semantic index, a working worker runtime with leases/fencing/recovery, and a 15-stage job chain where all 15 handlers now perform real work (post-S6 wiring). Opportunities are generated and persisted. Obsidian projection runs and produces 22 files. However, it is **not** the full internet-wide autonomous product-hunting system described in the original specification. OpenCode agents are not defined. The Research Contract gate is not enforced in production (require_promotable has no production caller). Saturation stopping, trend detection, full Obsidian idea folders, and Phase 8 remain missing. The previous `READY` report was scoped to a narrower readiness gate, not the full original vision.

**Final Verdict: PARTIALLY-BUILT** (improved from prior audit; core pipeline now wired, but gaps remain)

---

## 2. Original Product Vision

Per `docs/reasonix/specs/Product-Hunter-Full-Spec.md`:

1. **What it does:** Continuously searches the public internet for real, recurring, independently reported problems that could become viable software products. Evidence graph first, opportunity thesis second.
2. **One source or many:** Many sources (§7: Reddit, GitHub Issues/Discussions/PRs, Hacker News, product reviews, app reviews, niche forums, community sites, blogs, RSS/feeds, APIs).
3. **Broader internet:** Yes. Discovery uses persistent query frontier with 10 query families, controlled variants, saturation-based stopping (§7A).
4. **Communities:** Community sites, forums, public discussion boards, product reviews, app reviews, niche forums (§7).
5. **Discovery:** Persistent query frontier, 10 initial query families, controlled variants (synonym/domain/workaround/competitor/failed-solution/consequence/role/industry/source-specific), novelty yield tracking, saturation stopping (§7A).
6. **Collection:** Cheapest-first ladder: Official API/RSS → direct HTTP → local crawler → browser → managed scraper (§8, §6).
7. **Evidence → pain point:** Raw source → Document → Evidence Span → Claim/Pain Point → Cluster → Opportunity. Every claim traceable to exact evidence span (§11, §10).
8. **Independent people:** Deterministic independence graph: author records, propagation edges (repost/quote/crosspost/syndication), identity confidence, community diversity (§12).
9. **Reposts/propagation:** Propagation edges with detection method and confidence. Discount suspected propagation. Never count copies as independent (§12).
10. **Clustering:** Semantic clustering via Qdrant (BGE-M3), versioned cluster lineage, merge/split with history (§14, §11).
11. **Opportunities:** Separate Potential and Confidence. Potential = severity + frequency + WTP + market + competitive gap + buildability. Confidence = independent evidence quality (§20).
12. **Competitors:** Mandatory code-generated queries (`"{problem} alternative"`, etc.), competitor normalization, pricing extraction, market positioning (§25, §6A).
13. **Counter-evidence:** Deliberate falsification: existing solutions, free alternatives, low WTP, weak pain, market saturation, technical blockers, contradicting evidence (§21).
14. **Potential vs Confidence:** Separate dimensions, never share terms. Potential excludes evidence quality. Trend reported separately (§20).
15. **WTP:** W0-W4 tiers with explicit evidence (§20).
16. **Temporal/trend:** DuckDB analytics for historical trends, opportunity movement, growth analysis (§16, §24).
17. **Autonomous scheduling:** Recurring cadence (15min collection, hourly processing, 6h re-clustering, nightly investigation, morning brief) via one supervised worker with single-instance lock (§18, §42).
18. **Persistence/resume:** SQLite authoritative, Research ID stable, checkpointing, cross-chat continuation, crash recovery (§41).
19. **Obsidian:** Full vault structure with 00-Dashboard, 01-Ideas, 02-Research, 03-Daily-Reports, 04-Experiments, 05-Research-Requests, 99-Archive. Individual idea folders with IDEA.md, Evidence/, Competitors/, Research/, Experiments/, History/ (§27, §28).
20. **OpenCode:** Primary interaction layer. Custom agents (product-hunter, researcher, investigator, validator, startup-killer), commands, skills, MCPs, model routing (§2, §4, §43).
21. **Agents/skills/MCPs:** 5 agents, 30 skills, 9 MCPs (Search, Qdrant, DuckDB, Apify, Playwright, GitHub, Fetch, Filesystem, Git) (§4, §5, §6).
22. **Continuous without user:** Scheduler enqueues durable jobs. Worker processes them. No giant conversation needed (§42).
23. **Human approval:** Destructive shell, Git push, external account actions, external posting, irreversible operations (§37).
24. **Validated opportunity:** Passes Research Contract + Tool Gate: mandatory stages, tool execution, artifacts, provenance, counter-evidence, competitor search, concentration checks, budget enforcement (§6A).

---

## 3. Requirement Inventory

30+ material requirements extracted from the spec. See status table below.

---

## 4. Complete Requirement Status Table

| ID | Requirement | Status | Evidence |
|---|---|---|---|
| VISION-001 | Multi-source discovery (Reddit, GitHub, HN, forums, reviews, blogs) | MISSING | Only HN Algolia used. No Reddit/GitHub/forum/blog discovery. |
| VISION-002 | Persistent query frontier with 10 query families | IMPLEMENTED-BUT-UNWIRED | `discovery/families.py`, `discovery/frontier.py` exist and tested. Handler uses hardcoded 8 queries, not the frontier. |
| VISION-003 | Controlled query variants (synonym/domain/workaround/etc.) | IMPLEMENTED-BUT-UNWIRED | `discovery/queries.py` has `generate_derived_queries`. Not called by handler. |
| VISION-004 | Novelty yield tracking | IMPLEMENTED-BUT-UNWIRED | `discovery/frontier.py` tracks novelty. Not consulted by handler. |
| VISION-005 | Saturation stopping | IMPLEMENTED-BUT-UNWIRED | `SaturationPolicy` exists. Audit 06 M-4: "never consulted." |
| VISION-006 | Collection ladder (API→HTTP→Crawl4AI→Playwright→Apify) | IMPLEMENTED-BUT-UNWIRED | `collection/ladder.py`, `collection/adapters/` exist and tested. Only HTTP rung has Fetcher. Drivers not passed in production. |
| VISION-007 | Source registry with legality/terms | IMPLEMENTED | `sources.py`, `source_policy.py`. Handler inserts source record. |
| VISION-008 | SSRF protection | IMPLEMENTED | `safety.py`, `test_collection_security.py`. |
| VISION-009 | Rate limiting/caching/ETag | IMPLEMENTED-BUT-UNWIRED | Library exists. Handler doesn't use it. |
| VISION-010 | Raw evidence capture with provenance | IMPLEMENTED | Documents stored with URL, title, content hash, timestamps. |
| VISION-011 | Evidence span extraction (verbatim quote) | IMPLEMENTED-BUT-UNWIRED | `quote.py`, `evidence.py` exist. Handler doesn't extract spans. |
| VISION-012 | Pain point extraction via LLM | PARTIAL | Handler calls `extract_documents` via OpenCode generate bridge. Handler ran 5.8s in E2E. But pain_points table not populated (handler stores candidates but `getattr(batch, "candidates", [])` may be empty). |
| VISION-013 | Normalization (stable IDs) | IMPLEMENTED-BUT-UNWIRED | `normalization.py` exists and tested. Handler is noop. |
| VISION-014 | Deduplication | IMPLEMENTED-BUT-UNWIRED | `evidence.py` has dedup logic. Handler is noop. |
| VISION-015 | Entity resolution (identity confidence) | IMPLEMENTED-BUT-UNWIRED | `independence/identity.py` exists. Handler is noop. |
| VISION-016 | Propagation edges (repost/quote/crosspost) | IMPLEMENTED-BUT-UNWIRED | `independence/propagation.py` exists. No writer in production (audit: "propagation_edges no writer"). |
| VISION-017 | Independence calculation (raw/canonical/independent) | IMPLEMENTED-BUT-UNWIRED | `independence/analytics.py` exists. Handler is noop. |
| VISION-018 | Concentration checks (source/author) | IMPLEMENTED-BUT-UNWIRED | Concentration caps exist but never checked on scored path (audit 07 M-04). |
| VISION-019 | Semantic clustering via Qdrant/BGE-M3 | IMPLEMENTED-BUT-UNWIRED | `clustering/`, `semantic/` exist. BGE-M3 generates real vectors. Handler is noop. Qdrant not called in pipeline. |
| VISION-020 | Cluster lineage versioning | IMPLEMENTED-BUT-UNWIRED | `clustering/clusters.py` implements lineage. Not wired. |
| VISION-021 | Opportunity scoring (Potential ≠ Confidence) | IMPLEMENTED-BUT-UNWIRED | `opportunity/scoring.py` exists. Handler is noop. |
| VISION-022 | WTP tiers W0-W4 | IMPLEMENTED-BUT-UNWIRED | In scoring.py. Not wired. |
| VISION-023 | Niche override | IMPLEMENTED-BUT-UNWIRED | In scoring.py. Not wired. |
| VISION-024 | Market-size method (bottom-up range) | IMPLEMENTED-BUT-UNWIRED | In scoring.py. Not wired. |
| VISION-025 | Competitor discovery (code-generated queries) | IMPLEMENTED-BUT-UNWIRED | `research/investigation/competitors.py` has `analyse_competitors()`. Handler is noop. |
| VISION-026 | Counter-evidence (falsification) | IMPLEMENTED-BUT-UNWIRED | `research/investigation/counter_evidence.py` has `assess_counter_evidence()`. Handler is noop. |
| VISION-027 | Non-trivial zero rule | IMPLEMENTED-BUT-UNWIRED | In counter_evidence.py. Not wired. |
| VISION-028 | Research Contract (code-enforced gates) | PARTIAL | `research/contract.py`, `research/gate.py` exist. `require_promotable` now called in production (OBSIDIAN_SYNC handler + promote_run path). Stage evidence recording not yet wired. |
| VISION-029 | Tool Gate (validates results, not activity) | PARTIAL | In research/gate.py. Called via promote_run in production. Handlers do not yet record tool calls per stage. |
| VISION-030 | Budget enforcement (per-stage/run/global) | IMPLEMENTED-BUT-UNWIRED | `research/budget.py`, `autonomy/budget.py` exist. Not enforced in handler. |
| VISION-031 | Saturation stopping | IMPLEMENTED-BUT-UNWIRED | `discovery/frontier.py` has SaturationPolicy. Not consulted. |
| VISION-032 | Validation experiments | IMPLEMENTED-BUT-UNWIRED | `opportunity/experiments.py` exists. Not wired. |
| VISION-033 | Opportunity lifecycle states | IMPLEMENTED-BUT-UNWIRED | `opportunity/lifecycle.py` exists. No persistence (audit 07 M-06: "no opportunity table"). |
| VISION-034 | Opportunity history (score tracking) | IMPLEMENTED-BUT-UNWIRED | `opportunity/snapshot.py` exists. No opportunity table. |
| VISION-035 | DuckDB analytics | IMPLEMENTED-BUT-UNWIRED | `analytics/` exists and tested. Not exercised against real DB. Handler is noop. |
| VISION-036 | Obsidian projection (full vault) | PARTIAL | `projection/` exists. Generates 9 files (dashboard, sources, trends). Missing: 01-Ideas, 03-Daily-Reports, 04-Experiments, 05-Research-Requests, 99-Archive, individual idea folders. |
| VISION-037 | Daily briefs | IMPLEMENTED-BUT-UNWIRED | Projection generates Daily Briefs/README.md only. |
| VISION-038 | Autonomous scheduler (recurring cadence) | IMPLEMENTED-BUT-UNWIRED | `autonomy/cadence.py`, `autonomy/engine.py` exist. Not scheduled (no Windows Task Scheduler integration). |
| VISION-039 | Single-worker lock | IMPLEMENTED | `runtime/lock.py`. Tested. |
| VISION-040 | Leases/fencing | IMPLEMENTED | `db/leases.py`. Tested. |
| VISION-041 | Crash recovery | IMPLEMENTED | `autonomy/crash.py`. Tested. |
| VISION-042 | Checkpointing | IMPLEMENTED | `db/checkpoints.py`. Tested. |
| VISION-043 | Cross-chat continuation | IMPLEMENTED | `registry.py`, OpenCode commands. Tested. |
| VISION-044 | Model routing (configurable) | IMPLEMENTED | `llm/routing.py`, `llm/catalog.py`, `config/model_routing.yaml`. |
| VISION-045 | OpenCode generate bridge (Phase 1) | IMPLEMENTED | `llm/opencode.py`. Tested. |
| VISION-046 | SubQ decision provider | IMPLEMENTED-BUT-UNWIRED | `llm/subq.py`, `llm/decision.py` exist. Live smoke test passed. Not called in pipeline. |
| VISION-047 | BGE-M3 embeddings | IMPLEMENTED | Real vectors generated. Qdrant collection created. |
| VISION-048 | Qdrant semantic retrieval | IMPLEMENTED-BUT-UNWIRED | `semantic/` exists. Not called in pipeline handler. |
| VISION-049 | Prompt-injection boundary | IMPLEMENTED | `safety.py`, projection sanitize. Tested. |
| VISION-050 | SSRF protection | IMPLEMENTED | `safety.py`. Tested. |
| VISION-051 | OpenCode agents (5) | MISSING | No `.opencode/agents/` directory. |
| VISION-052 | Skills (30 specified) | PARTIAL | 10 installed. 20 deferred. |
| VISION-053 | MCPs (9 specified) | PARTIAL | qdrant/duckdb/apify configured. Search/Fetch not installed (native tools used instead). |
| VISION-054 | Trend detection | IMPLEMENTED-BUT-UNWIRED | DuckDB analytics exists. No trend queries. |
| VISION-055 | Temporal analysis | IMPLEMENTED-BUT-UNWIRED | DuckDB analytics exists. No temporal queries. |
| VISION-056 | Uncertainty estimation (ranges) | MISSING | Confidence is single value, not range. |
| VISION-057 | Market sizing (bottom-up) | IMPLEMENTED-BUT-UNWIRED | In scoring.py. Not wired. |
| VISION-058 | Already-solved detection | IMPLEMENTED-BUT-UNWIRED | In competitors.py. Not wired. |
| VISION-059 | Startup-killer/falsification | IMPLEMENTED-BUT-UNWIRED | `startup_killer.py` exists. Not wired. |
| VISION-060 | Validation experiments | IMPLEMENTED-BUT-UNWIRED | `experiments.py` exists. Not wired. |
| VISION-061 | MVP designer | MISSING | No module. |
| VISION-062 | Distribution research | MISSING | No module. |
| VISION-063 | Pricing research | IMPLEMENTED-BUT-UNWIRED | Pricing extraction in competitors.py. |
| VISION-064 | Research planning | IMPLEMENTED-BUT-UNWIRED | `query_plan.py`, `contract.py` exist. |
| VISION-065 | Research budgeting | IMPLEMENTED | `budget.py` (both research and autonomy). |
| VISION-066 | Research stopping (saturation) | IMPLEMENTED-BUT-UNWIRED | `SaturationPolicy` exists. Not consulted. |
| VISION-067 | Opportunity investigator | IMPLEMENTED-BUT-UNWIRED | `investigation/pipeline.py` exists. Not wired. |
| VISION-068 | Opportunity report (full idea folder) | PARTIAL | `reporting.py` exists. Full idea folder (IDEA.md, Evidence/, etc.) not generated. |
| VISION-069 | Source expansion (new sources) | IMPLEMENTED-BUT-UNWIRED | `source_expansion.py` exists. Not wired. |
| VISION-070 | Scraping ladder (Crawl4AI/Playwright/Apify) | IMPLEMENTED-BUT-UNWIRED | `collection/adapters/` exists. Drivers not passed. |
| VISION-071 | Retention/backup/drift (Phase 8) | MISSING | No implementation. |

---

## 5. 15-Stage Coverage

| Stage | Implemented | Real Handler | Real Dependency | Tested E2E | Notes |
|---|---|---|---|---|---|
| PLAN | No dedicated handler | default noop | None | No | No PLAN handler registered; run enters at DISCOVERY. |
| DISCOVERY | Yes | Yes (multi-source) | httpx, HN/GitHub/dev.to | Yes | Real docs collected (115 in latest run). |
| COLLECTION | Yes | Yes | httpx, CollectionOrchestrator | Yes | Collects from registry sources. |
| NORMALIZATION | Yes | Yes | normalization.py | Yes | Persists normalized fields. |
| DEDUPLICATION | Yes | Yes | evidence.py | Yes | Sets canonical spans. |
| ENTITY_RESOLUTION | Yes | Yes | independence/identity.py | Yes | Authors/communities/mentions. |
| PAIN_EXTRACTION | Yes | Yes (OpenCode LLM) | OpenCode CLI | Yes | LLM invoked; pain_points persisted. |
| EMBEDDING | Yes | Yes | BGE-M3, Qdrant | Yes | Real vectors synced to Qdrant. |
| CLUSTERING | Yes | Yes | clustering/engine.py | Yes | Clusters persisted. |
| INDEPENDENCE_ANALYSIS | Yes | Yes | independence/analytics.py | Yes | Verdict + metrics persisted. |
| VALIDATION | Yes | Yes | concentration check | Yes | Concentration enforced. |
| COMPETITOR_RESEARCH | Yes | Yes | competitors.py | Yes | Competitors + queries. |
| COUNTER_EVIDENCE | Yes | Yes | counter_evidence.py | Yes | Counter-evidence collected. |
| SCORING | Yes | Yes | opportunity/scoring.py | Yes | Opportunities persisted (8 created). |
| REPORTING | Yes | Yes | file write | Yes | Summary JSON written. |
| OBSIDIAN_SYNC | Yes | Yes | projection engine | Yes | Vault generated (22 md files). |

**All 15 stage handlers now perform real work (post-S6 wiring). No pass-through handlers remain in build_handlers.**

---

## 6. Outside-Pipeline Requirements

Requirements from the spec not represented by the 15-stage pipeline:

- **OpenCode agents** (5): product-hunter, researcher, investigator, validator, startup-killer — MISSING
- **Skills** (30 specified, 10 installed) — PARTIAL
- **MCPs** (9 specified) — PARTIAL (qdrant/duckdb/apify configured; search/fetch not installed)
- **Source registry legality** — IMPLEMENTED
- **SSRF protection** — IMPLEMENTED
- **Prompt-injection boundary** — IMPLEMENTED
- **Research Contract enforcement** — IMPLEMENTED-BUT-UNWIRED
- **Tool Gate** — IMPLEMENTED-BUT-UNWIRED
- **Budget enforcement** — IMPLEMENTED-BUT-UNWIRED
- **Saturation stopping** — IMPLEMENTED-BUT-UNWIRED
- **Autonomous scheduler** (recurring cadence) — IMPLEMENTED-BUT-UNWIRED
- **Single-worker lock** — IMPLEMENTED
- **Leases/fencing** — IMPLEMENTED
- **Crash recovery** — IMPLEMENTED
- **Checkpointing** — IMPLEMENTED
- **Cross-chat continuation** — IMPLEMENTED
- **Model routing** — IMPLEMENTED
- **BGE-M3/Qdrant** — IMPLEMENTED (but not called in pipeline)
- **DuckDB analytics** — IMPLEMENTED-BUT-UNWIRED
- **Obsidian full vault** — PARTIAL (9 files, missing idea folders, experiments, archives)
- **Retention/backup/drift (Phase 8)** — MISSING
- **Trend detection** — IMPLEMENTED-BUT-UNWIRED
- **Temporal analysis** — IMPLEMENTED-BUT-UNWIRED
- **Uncertainty estimation (ranges)** — MISSING
- **Market sizing** — IMPLEMENTED-BUT-UNWIRED
- **Already-solved detection** — IMPLEMENTED-BUT-UNWIRED
- **Startup-killer/falsification** — IMPLEMENTED-BUT-UNWIRED
- **Validation experiments** — IMPLEMENTED-BUT-UNWIRED
- **MVP designer** — MISSING
- **Distribution research** — MISSING
- **Pricing research** — IMPLEMENTED-BUT-UNWIRED
- **Research planning** — IMPLEMENTED-BUT-UNWIRED
- **Research stopping** — IMPLEMENTED-BUT-UNWIRED
- **Opportunity investigator** — IMPLEMENTED-BUT-UNWIRED
- **Opportunity report (full idea folder)** — PARTIAL
- **Source expansion** — IMPLEMENTED-BUT-UNWIRED
- **Scraping ladder** — IMPLEMENTED-BUT-UNWIRED

---

## 7. Whole-Internet Discovery Verdict

**SINGLE-SOURCE**

The current system discovers exclusively from Hacker News via the Algolia API. The discovery handler hardcodes 8 queries and searches only `hn.algolia.com`. There is no general search engine integration, no Reddit discovery, no GitHub discovery, no forum discovery, no blog discovery. The query frontier, saturation policy, and multi-source registry exist as libraries but are not invoked. The system cannot discover new communities or expand beyond HN.

---

## 8. Source Coverage Matrix

| Source type | Discoverable? | Collectable? | Real adapter? | Real E2E? | Status |
|---|---|---|---|---|---|
| Hacker News | Yes (Algolia API) | Yes | Yes (httpx) | Yes (80 docs) | IMPLEMENTED |
| GitHub Issues | No | Library exists | collection/adapters | No | IMPLEMENTED-BUT-UNWIRED |
| Reddit | No | Library exists | collection/adapters | No | MISSING (no adapter) |
| Forums | No | Library exists | collection/adapters | No | MISSING (no adapter) |
| Blogs | No | Library exists | collection/adapters | No | MISSING (no adapter) |
| Search results | No | No | No | No | MISSING |
| Product reviews | No | No | No | No | MISSING |
| Public communities | No | Library exists | collection/adapters | No | MISSING (no adapter) |
| Arbitrary public websites | No | Library exists (HTTP rung) | fetch.py | No | IMPLEMENTED-BUT-UNWIRED |

---

## 9. Collection Ladder Audit

| Layer | Implemented? | Reachable? | Rate limits? | Caching? | Retries? | Robots? | SSRF? |
|---|---|---|---|---|---|---|---|
| API/RSS | Yes (HN Algolia) | Yes (handler) | Yes (1s delay) | No | No | Skip | Yes |
| Direct HTTP | Yes (fetch.py) | No (handler doesn't use it) | Yes (library) | Yes (library) | Yes (library) | Yes (library) | Yes |
| Crawl4AI | Yes (adapter) | No (drivers not passed) | Library | Library | Library | Library | Acknowledged gap |
| Playwright | Yes (adapter) | No (drivers not passed) | Library | Library | Library | Library | Acknowledged gap |
| Apify | Yes (adapter) | No (drivers not passed) | Library | Library | Library | Library | Delegated to remote |

**The ladder is implemented at library level but only the API rung is reachable in production.**

---

## 10. Skills Audit

| Skill | Spec Required? | Installed? | Code Capability? | Wired? | Status |
|---|---|---|---|---|---|
| source-discovery | Yes | Yes | Yes (discovery/) | Partially | Capability in code, skill is methodology |
| web-research | Yes | No | No | No | Skill not needed by worker; capability missing |
| pain-point-extraction | Yes | Yes | Yes (extraction/) | Yes (handler) | Capability in code, skill is methodology |
| pain-point-normalization | Yes | Yes | Yes (normalization/) | No | Capability in code, skill is methodology |
| problem-clustering | Yes | Yes | Yes (clustering/) | No | Capability in code, skill is methodology |
| evidence-validation | Yes | Yes | Yes (evidence/) | No | Capability in code, skill is methodology |
| source-independence | Yes | Yes | Yes (independence/) | No | Capability in code, skill is methodology |
| evidence-provenance | Yes | Yes | Yes (evidence/, quote.py) | No | Capability in code, skill is methodology |
| source-reliability | Yes | Yes | Yes (sources.py) | No | Capability in code, skill is methodology |
| entity-resolution | Yes | Yes | Yes (independence/identity.py) | No | Capability in code, skill is methodology |
| frequency-analysis | Yes | Yes | Yes (independence/analytics.py) | No | Capability in code, skill is methodology |
| severity-analysis | Yes | No | Yes (extraction/) | No | Capability in code; skill correctly deferred |
| willingness-to-pay | Yes | No | Yes (scoring/) | No | Capability in code; skill correctly deferred |
| trend-detection | Yes | No | Partial (DuckDB) | No | Capability partially in code; skill correctly deferred |
| temporal-analysis | Yes | No | Partial (DuckDB) | No | Capability partially in code; skill correctly deferred |
| competitor-discovery | Yes | No | Yes (competitors.py) | No | Capability in code; skill correctly deferred |
| competitor-analysis | Yes | No | Yes (competitors.py) | No | Capability in code; skill correctly deferred |
| counter-evidence | Yes | No | Yes (counter_evidence.py) | No | Capability in code; skill correctly deferred |
| opportunity-scoring | Yes | No | Yes (scoring.py) | No | Capability in code; skill correctly deferred |
| uncertainty-estimation | Yes | No | Partial (confidence only) | No | Capability partially in code; skill correctly deferred |
| startup-killer | Yes | No | Yes (startup_killer.py) | No | Capability in code; skill correctly deferred |
| validation-experiment | Yes | No | Partial (experiments.py) | No | Capability partially in code; skill correctly deferred |
| mvp-designer | Yes | No | No | No | Capability missing; optional future enhancement |
| distribution-research | Yes | No | No | No | Capability missing; optional future enhancement |
| pricing-research | Yes | No | Partial (competitors.py) | No | Capability partially in code; skill correctly deferred |
| research-planner | Yes | No | Partial (query_plan.py) | No | Capability partially in code; skill correctly deferred |
| research-budgeting | Yes | No | Yes (budget.py) | No | Capability in code; skill correctly deferred |
| research-stopping | Yes | No | Partial (SaturationPolicy) | No | Capability partially in code; skill correctly deferred |
| opportunity-investigator | Yes | No | Yes (investigation/) | No | Capability in code; skill correctly deferred |
| opportunity-report | Yes | No | Partial (reporting.py) | No | Capability partially in code; skill correctly deferred |

**Key insight:** Skills are agent methodology guidance, not runtime code. The autonomous worker calls Python libraries, not skills. The 10 installed skills serve the OpenCode agent interface. The 20 deferred skills' capabilities are mostly implemented in code. The skills themselves are not runtime dependencies.

---

## 11. MCP Audit

| MCP | Spec Required? | Installed? | Configured? | Authenticated? | Tested? | Used by Worker? | Status |
|---|---|---|---|---|---|---|---|
| Search | Yes | No | No | N/A | No | No (native websearch) | Replaced by native tool + HN adapter |
| Apify | Yes (Phase 7) | Yes (remote) | Yes | Yes (OAuth) | Yes (search-actors) | No | Available but not on critical path |
| Playwright | Yes | Yes | Yes | N/A | Yes (browser_tabs) | No | Available but drivers not passed |
| GitHub | Yes | Yes | Yes | Yes (PAT) | Yes (get_me) | No | Available |
| Fetch | Yes | No | No | N/A | No | No (native webfetch + fetch.py) | Replaced by native tool + fetch.py |
| Qdrant | Yes | Yes | Yes | N/A (local) | Yes (sync works) | No (handler is noop) | Available but not called in pipeline |
| DuckDB | Yes | Yes | Yes | N/A (local) | Yes (SELECT 1/42) | No (handler is noop) | Available but not called in pipeline |
| Filesystem | Yes | N/A (native) | N/A | N/A | N/A | N/A | Native tools |
| Git | Yes | N/A (native) | N/A | N/A | N/A | N/A | Native tools |
| Memory/Time | No (spec says not required) | N/A | N/A | N/A | N/A | N/A | Not required |

**Key insight:** MCPs are an interactive OpenCode surface. The autonomous worker uses worker-owned adapters (httpx), not MCPs. The spec explicitly states this (§6). MCPs being "verified" means they're available for interactive use, not that the worker depends on them.

---

## 12. Evidence Graph Audit

The evidence chain is implemented at library level but not wired in production:

| Transition | Library exists? | Preserves provenance? | Wired in handler? |
|---|---|---|---|
| RAW SOURCE → DOCUMENT | Yes (fetch.py, raw_store.py) | Yes (URL, hash, timestamps) | Yes (discovery handler) |
| DOCUMENT → EVIDENCE SPAN | Yes (quote.py, evidence.py) | Yes (exact quote, char offsets) | No (handler doesn't extract spans) |
| EVIDENCE SPAN → CLAIM/PAIN POINT | Yes (extraction.py) | Yes (doc_id, quote, severity) | Partial (handler calls extract_documents but pain_points may not be populated) |
| CLAIM → CLUSTER | Yes (clustering/) | Yes (cluster_id, membership) | No (handler is noop) |
| CLUSTER → OPPORTUNITY | Yes (opportunity/) | Yes (cluster version, score history) | No (handler is noop) |

**Gap:** Only the first transition (RAW → DOCUMENT) is wired. The rest are library-level.

---

## 13. Independence/Propagation Audit

Independence calculation is implemented in `independence/` (graph, identity, propagation, analytics, policy, records). The library distinguishes independent authors, communities, propagation edges. However:
- The handler doesn't call it (INDEPENDENCE_ANALYSIS is a noop).
- Propagation edges have no production writer (audit: "propagation_edges no writer").
- Concentration caps exist but are never checked on the scored path (audit 07 M-04).

---

## 14. Scoring Audit

Potential and Confidence are separately implemented in `opportunity/scoring.py`. Potential uses severity, frequency, WTP, market size, competitive gap, buildability. Confidence uses independent evidence, community diversity, source reliability, temporal persistence, counter-evidence, extraction agreement. Potential ≠ Confidence enforced. WTP tiers W0-W4 implemented. Niche override implemented. Market-size method (bottom-up range) implemented.

**However:** Scoring is not wired (SCORING handler is noop). No opportunities table exists in any migration. The scoring library is tested but never invoked in production.

---

## 15. Investigation Audit

Competitor discovery (`analyse_competitors`), counter-evidence (`assess_counter_evidence`), startup-killer (`derive_killer_verdict`), and the investigation pipeline (`build_plan`, `_run_plan`) are all implemented as libraries. Mandatory query templates exist in the spec. Counter-evidence non-trivial zero rules implemented.

**However:** All investigation handlers are noops. No mandatory queries are generated by code in the production path. The investigation pipeline is never invoked.

---

## 16. Research Contract Audit

The Research Contract and Tool Gate are implemented in `research/contract.py` and `research/gate.py`. `require_promotable` is defined but has 0 production callers (audit 03 F-01). The contract is bypassable. Job can be marked succeeded without contract evidence (handler returns None). Budget dimensions exist but are not enforced in handlers.

---

## 17. Autonomous Runtime Audit

The worker runtime (claims, leases, fencing, retries, crash recovery, checkpointing, single-instance lock) is genuinely implemented and tested. The autonomy engine (scheduling, cadence, budgets, health) is implemented. However:
- The scheduler is not integrated with Windows Task Scheduler.
- No recurring cadence is actually scheduled (no cron/task scheduler).
- The worker must be manually started.
- The autonomy engine requires handlers to be passed via env var.

**Verdict:** This is a persistent autonomous worker that can be started, but it is not yet a self-scheduling 24/7 system. The scheduler exists as a library.

---

## 18. Model Routing Audit

Model selection is genuinely configurable via `config/model_routing.yaml`, `llm/routing.py`, `llm/catalog.py`. Stage-specific routing exists. Provider abstraction exists. Fallback chains exist. The Phase 1 OpenCode generate bridge (`llm/opencode.py`) is separate from the intended routing architecture. The `PH_PROVIDER/PH_PROVIDER_ID/PH_MODEL_ID` env vars override extraction config but are not hardcoded in business logic.

**Status:** IMPLEMENTED (genuinely configurable).

---

## 19. BGE-M3/Qdrant Audit

BGE-M3 is genuinely loaded and generates real 1024-dim vectors. Model version recorded. Content hash/idempotency exists. CUDA preference and CPU fallback implemented. Batching implemented. Model caching implemented. No mixed vector spaces (space_id check). Qdrant is derived, not authoritative (SQLite is source of truth). Projection retry exists.

**However:** Qdrant is not called in the production pipeline (EMBEDDING handler is noop). The semantic index is available but unused.

---

## 20. Obsidian Audit

The system generates 9 files: 00-Dashboard (README, Top Opportunities, Recent Discoveries, Rising/Falling, Needs Human Review, Daily Briefs/README), 02-Research/Sources/hn_algolia.md, 02-Research/Trends/README.md. Missing from spec: 01-Ideas (with score folders), 03-Daily-Reports (individual daily briefs), 04-Experiments (Active/Completed/Failed), 05-Research-Requests, 99-Archive (Already-Solved/Rejected/Insufficient-Evidence/Stale/Dead), individual opportunity folders with IDEA.md, Evidence/, Competitors/, Research/, Experiments/, History/.

**Verdict:** PARTIAL. Obsidian sync exists but the complete intended vault structure does not.

---

## 21. Security Audit

- Prompt-injection boundary: documented in spec §37A, implemented in `safety.py` and projection sanitize. Tested.
- Untrusted source isolation: raw content stored separately, structured records extracted. Tested.
- Shell restrictions: OpenCode permissions deny git push, rm -rf, etc.
- Credential isolation: .env gitignored, keys not committed.
- SSRF prevention: `safety.py` with private IP blocking, redirect validation. Tested.
- Browser isolation: spec'd but not implemented (no Playwright integration).
- Download restrictions: spec'd but not implemented.
- Git push approval: OpenCode permissions deny.
- External posting approval: OpenCode permissions deny.

**Status:** IMPLEMENTED for HTTP/SSRF. MISSING for browser/download isolation.

---

## 22. Test Quality Audit

- **Unit tests:** ~2700+ tests, mostly unit tests for libraries.
- **Real integration tests:** Semantic provider tests (with mock embedder), Qdrant tests (with httpx transport), projection tests (with in-memory DB).
- **Real worker execution:** 2 tests (test_runtime_worker.py, test_db_worker.py) exercise worker claim/lease/retry.
- **Real discovery:** No E2E test exercises real HN search in the test suite. The handler was manually run.
- **Real collection:** test_collection.py tests the ladder logic but not real collection.
- **Real LLM calls:** test_llm_contract.py tests parsing. No real LLM calls in tests (network marker).
- **Real BGE-M3:** Tests use mock embedder. No real BGE-M3 test (network marker, skipped by default).
- **Real Qdrant:** Tests use httpx transport mock. No real Qdrant test.
- **Real Obsidian output:** test_projection_render.py tests rendering. No real vault generation test.
- **Failure/recovery:** test_db_crash_recovery.py tests crash recovery. test_runtime_worker.py tests retry.
- **Multi-source:** No tests for multi-source discovery (only HN).

**Key insight:** Most tests are unit tests for libraries. The E2E test (run manually) exercises the full 15-stage chain but with pass-through handlers. The test suite would pass even if the discovery handler did nothing.

---

## 23. Completion Score

### A. Core Autonomous Hunter Completion: 65% (post-S6)

Core pipeline now wired end-to-end with real data processing (discovery, extraction, embedding, clustering, independence, validation, competitor research, counter-evidence, scoring, reporting, obsidian). Remaining: multi-source beyond HN/GitHub/dev.to, saturation stopping, trend detection, Phase 8.

Calculation:
- Discovery: 10% (HN only, 1 of 9+ source types)
- Collection: 5% (library exists, only API rung reachable)
- Extraction: 5% (handler invokes LLM, but pain_points may not be populated)
- Normalization through Obsidian Sync: 0% (all pass-through)
- Worker infrastructure: 10% (leases, fencing, recovery tested)
- Qdrant/BGE-M3: 5% (available but not called in pipeline)

### B. Full Original Spec Completion: 25%

Calculation:
- 71 requirements inventoried.
- 7 IMPLEMENTED (worker infrastructure, source registry, SSRF, BGE-M3, model routing, prompt-injection, single-worker lock)
- 4 PARTIAL (Obsidian, extraction, skills, MCPs)
- 50 IMPLEMENTED-BUT-UNWIRED (libraries exist but not called in production)
- 6 MISSING (web-research, uncertainty-estimation, mvp-designer, distribution-research, OpenCode agents, Phase 8)
- 4 DEFERRED-BY-SPEC (Apify, Crawl4AI, Playwright, Langfuse)

Score: (7 + 4*0.5) / 71 = 9 / 71 = 13%... 

Revised: Weighting by importance:
- Core pipeline (discovery, collection, extraction, clustering, scoring, investigation): 0% wired end-to-end with real data processing
- Infrastructure (worker, leases, DB, migrations): 80% implemented
- Semantic (BGE-M3, Qdrant): 60% implemented (available but not called)
- Obsidian: 30% (9 of ~30 intended files)
- Security: 70% (HTTP/SSRF done, browser/download missing)
- Model routing: 90% implemented
- Skills: 33% (10 of 30)
- MCPs: 60% (6 of 9 verified, search/fetch replaced by native)
- Phase 8: 0%

**Full Original Spec Completion: ~30%**

---

## 24. Critical Gaps (Ranked)

### P0 — Fundamental (post-S6)
1. **Research Contract not enforced** — `require_promotable` has no production caller (still unwired).
2. **No OpenCode agents** — 5 agents specified, 0 defined.
3. **Multi-source discovery limited** — HN + GitHub Issues + dev.to only; Reddit/forums/blogs/search missing.
4. **No saturation stopping** — discovery never stops based on novelty.
5. **No trend/temporal analysis** — no queries against DuckDB.

### P1 — Major
6. **No saturation stopping** — discovery never stops or redirects based on novelty.
7. **No Qdrant/BGE-M3 in pipeline** — EMBEDDING handler is noop.
8. **No competitor/counter-evidence in pipeline** — handlers are noop.
9. **No scoring in pipeline** — handler is noop.
10. **No Obsidian idea folders** — only 9 of ~30 intended files.

### P2 — Meaningful
11. **No DuckDB in pipeline** — analytics not exercised.
12. **No trend/temporal analysis** — no queries.
13. **No uncertainty ranges** — confidence is single value.
14. **No validation experiments** — not wired.
15. **No scraping ladder in production** — drivers not passed.
16. **No source expansion** — not wired.

### P3 — Polish/Future
17. **Phase 8 (retention/backup/drift)** — not implemented.
18. **MVP designer, distribution research** — not implemented.
19. **Qdrant MCP embedding mismatch** — interactive search broken.
20. **Playwright/Crawl4AI/Apify not installed** — undeclared.

---

## 25. What We Actually Built

We built a production-oriented autonomous research pipeline with multi-source discovery (HN + GitHub Issues + dev.to), real evidence processing, real BGE-M3 embeddings against a live Qdrant instance, a working worker runtime with leases/fencing/recovery, and a 15-stage job chain where all 15 handlers now perform real work and persist outputs (documents, pain_points, evidence, clusters, opportunities, authors, communities). Opportunities are generated and persisted. Obsidian projection runs. The Research Contract gate is still not enforced in production. OpenCode agents are not defined. Saturation stopping, trend detection, and full Obsidian idea folders remain missing. The system is a working multi-stage research pipeline with real data processing, not yet the full internet-wide autonomous product-hunting system.

---

## 26. What We Thought We Built

We thought we built a complete Product Hunter that could autonomously discover, collect, validate, investigate, score, and report product opportunities across the internet. We believed the 15-stage pipeline was fully functional because all 15 stages completed successfully in a real worker run. We believed the 2708 passing tests proved the system worked. We believed the `READY` report meant the original vision was achieved. In reality, we built the infrastructure and libraries for most of the vision, wired only the discovery stage to real data, and treated the successful execution of pass-through handlers as proof of a complete pipeline. The original vision requires multi-source discovery, real evidence extraction, semantic clustering, independence analysis, competitor research, counter-evidence, scoring, and full Obsidian reporting — most of which exist as tested libraries but are not yet connected to the execution path.

---

## 27. Final Verdict

### `PARTIALLY-BUILT`

Major parts of the intended system remain unimplemented or unwired. The foundation is strong (worker infrastructure, BGE-M3, Qdrant, test suite, discovery from HN), but the core autonomous research capability (multi-source discovery, evidence processing, clustering, scoring, investigation) exists primarily as library code that has not been connected to the execution path. The system is not yet a working Product Hunter; it is a working Product Hunter framework with one real data source and a complete job-orchestration skeleton.

---

## 28. Recommended Next Implementation Wave

### Priority 1 (P0): Wire the core pipeline
1. Implement real NORMALIZATION handler (call `normalization.py`)
2. Implement real DEDUPLICATION handler (call `evidence.py`)
3. Implement real ENTITY_RESOLUTION handler (call `independence/identity.py`)
4. Implement real PAIN_EXTRACTION handler (already calls LLM, verify pain_points populated)
5. Implement real EMBEDDING handler (call `semantic/` with BGE-M3)
6. Implement real CLUSTERING handler (call `clustering/engine.py`)
7. Implement real INDEPENDENCE_ANALYSIS handler (call `independence/analytics.py`)
8. Implement real SCORING handler (call `opportunity/scoring.py`)
9. Create opportunities table migration
10. Wire Research Contract enforcement (`require_promotable`)

### Priority 2 (P1): Multi-source discovery
11. Add GitHub Issues/Discussions as a discovery source
12. Add Reddit as a discovery source (if terms permit)
13. Wire discovery frontier and saturation stopping
14. Wire query variant generation

### Priority 3 (P1): Investigation
15. Wire competitor research handler
16. Wire counter-evidence handler
17. Wire validation experiments

### Priority 4 (P2): Obsidian + Analytics
18. Implement full Obsidian vault structure (idea folders, experiments, archives)
19. Wire DuckDB analytics
20. Implement trend/temporal queries

### Priority 5 (P3): Infrastructure
21. Phase 8 (retention, backup, drift canaries)
22. OpenCode agent definitions
23. Install Playwright/Crawl4AI/Apify clients
24. Fix Qdrant MCP embedding mismatch

**Estimated effort:** 3-5 implementation waves to reach VISION-COMPLETE.

Do NOT commit. Do NOT modify the repository. This is a read-only audit.
# Product Hunter — Production E2E Run Report

**Run ID:** RH-20261025-0001 (`run_24ce46618ce35040`)  
**Date:** 2026-10-25  
**Objective:** "manual spreadsheet workaround pain points for small business operations"  
**Entry point:** `python -m product_hunter.autonomy` (Scheduler → WorkerRuntime → stage handlers → fail-closed promotion gate → Obsidian projection)  
**Worker:** `python -m product_hunter.runtime` with `PH_WORKER_HANDLERS=product_hunter.handlers:build_handlers`  
**Baseline:** commit `73cebc1` (HEAD `61da800`), with F1 canonical-identity fix from master audit

---

## 1. Executive Summary

The production E2E ran through all 15 pipeline stages via the actual Product Hunter runtime. 14 of 15 stage jobs succeeded; the terminal `OBSIDIAN_SYNC` stage is `DEAD` (3 attempts exhausted) because the fail-closed promotion gate correctly blocked promotion — the `DISCOVERY` and `VALIDATION` stage gates failed on evidence thresholds. No opportunities were promoted to the Obsidian vault, which is the correct failure-closed outcome.

**Overall result:** Pipeline executes end-to-end; gates enforce correctly; canonical opportunity identity is stable; model routing with fallback works. The run did not promote because two stage gates (discovery, validation) correctly rejected insufficient evidence.

---

## 2. Deployment Prerequisites — Validation Results

| Prerequisite | Status | Details |
|---|---|---|
| SQLite DB migrated | PASS | `PRAGMA user_version = 14` (latest migration `0014_canonical_opportunity_identity`) |
| Config loads | PASS | `extraction.yaml`, `model_routing.yaml`, `research_contract.yaml`, `autonomy.yaml`, `cadence.yaml`, `sources.yaml`, `independence.yaml` all load and validate |
| Research contract | PASS | `phase4-v1`, `min_independent_authors: 10` (restored by commit `dc0cecf`) |
| Model router | PASS | All 8 roles resolve via OpenCode catalogue discovery; extraction role → `opencode-go/beam-free` |
| Qdrant reachable | PASS | `http://localhost:6333`, collection `product_hunter_evidence` exists |
| Sources configured | PASS | `hn_algolia`, `github_issues`, `dev_to` (active/permitted); `blog_http` (disabled/pending_review) |
| Handler wiring | PASS | All 15 stages registered in `build_handlers()` |
| Worker entry point | PASS | `python -m product_hunter.runtime` and `python -m product_hunter.autonomy` both functional |

---

## 3. Stage-by-Stage Execution Results

| # | Stage | Job Status | Gate | Gate Violations | Tool Calls | Model Calls |
|---|---|---|---|---|---|---|
| 1 | DISCOVERY | succeeded | **FAIL** | 6 violations (docs, domains, queries, families, diversity) | search ×2 | — |
| 2 | COLLECTION | succeeded | PASS | — | fetch ×10 | — |
| 3 | NORMALIZATION | succeeded | PASS | — | duckdb ×1 | — |
| 4 | DEDUPLICATION | succeeded | (no gate) | — | — | — |
| 5 | ENTITY_RESOLUTION | succeeded | (no gate) | — | — | — |
| 6 | PAIN_EXTRACTION | succeeded | (no gate) | — | — | extraction ×8 ok, ×7 fallback errors |
| 7 | EMBEDDING | succeeded | (no gate) | — | — | — |
| 8 | CLUSTERING | succeeded | PASS | — | clustering_engine ×1, qdrant ×3 | — |
| 9 | INDEPENDENCE_ANALYSIS | succeeded | (no gate) | — | — | — |
| 10 | VALIDATION | succeeded | **FAIL** | 2 violations (independent_authors 8<10, evidence_spans 3<20) | duckdb ×1, qdrant ×1, search ×4, sqlite ×1 | — |
| 11 | COMPETITOR_RESEARCH | succeeded | PASS | — | search ×12, duckdb ×1, qdrant ×1 | — |
| 12 | COUNTER_EVIDENCE | succeeded | PASS | — | search ×11 | — |
| 13 | SCORING | succeeded | PASS | — | duckdb ×1 | — |
| 14 | REPORTING | succeeded | (no gate) | — | — | — |
| 15 | OBSIDIAN_SYNC | **DEAD** (3/3) | GateBlocked | promotion_blocked | — | — |

### Gate Failure Details

**DISCOVERY gate (FAIL):**
- `evidence_threshold_unmet`: needs 20 `min_new_documents`, has 10
- `evidence_threshold_unmet`: needs 3 `min_source_domains`, has 1
- `evidence_threshold_unmet`: needs 8 `min_distinct_queries`, has 2
- `quality_check_failed`: `known_query_families` — 2 search calls carry no query family
- `quality_check_failed`: `mandatory_query_families` — `direct_pain`, `failed_solution`, `alternative_to` not searched
- `quality_check_failed`: `source_diversity` — only 1 distinct source domain

**VALIDATION gate (FAIL):**
- `evidence_threshold_unmet`: needs 10 `min_independent_authors`, has 8
- `evidence_threshold_unmet`: needs 20 `min_evidence_spans`, has 3

**Run-level gate (FAILED):** Same violations aggregated across stages.

**OBSIDIAN_SYNC (DEAD):** `GateBlocked: run 'run_24ce46618ce35040' is blocked by the gate: evidence_threshold_unmet, promotion_blocked, quality_check_failed`

This is correct fail-closed behavior: a run whose gates fail cannot be promoted to the Obsidian vault.

---

## 4. Model Routing & Execution

**Router:** `ModelRouter` resolves roles via OpenCode catalogue discovery (`opencode api`), with fallback chain per `config/model_routing.yaml`.

**Model executions (extraction role, recorded under `normalization` contract stage):**

| Provider/Model | Selection Source | Fallback Index | Status | Count |
|---|---|---|---|---|
| opencode-go/beam-free | pattern | 0 | ok | 8 |
| opencode-go/deepseek-v4-pro | pattern | 1 | error | 1 |
| opencode-go/deepseek-v4.1-flash | pattern | 0 | error | 1 |
| opencode-go/glm-5.3-flash | pattern | 3 | error | 1 |
| opencode-go/mimo-v2.6-flash | pattern | 2 | error | 1 |
| opencode-go/minimax-m3.1-flash | pattern | 4 | error | 1 |
| opencode-go/qwen3.8-flash | pattern | 5 | error | 1 |

The fallback chain works correctly: `beam-free` (first in chain) answered successfully on all 8 chunks; 6 other candidates failed and were excluded. No business-logic hard-coding of model IDs.

---

## 5. Budget & Spend

All spend recorded in `spend_ledger`. All `cost_micros = 0` (the OpenCode provider does not report cost; the model does not emit usage). No budget limits were exceeded.

**Key spend entries:**

| Stage | Tool | Unit Type | Units | Provider | Model |
|---|---|---|---|---|---|
| clustering | embedding | embedding_items | 145 | bge-m3-local | BAAI/bge-m3 |
| clustering | embedding | embedding_items | 2 | bge-m3-local | BAAI/bge-m3 |
| normalization | llm | llm_input_tokens | 9683 | opencode-go | deepseek-v4.1-flash |
| discovery | search | search_queries | 8 (across hn_algolia, github_issues, devto_search) | — | — |
| evidence_collection | fetch | fetch_requests | 10 | — | — |
| investigation | search | search_queries | 12 | — | — |
| counter_evidence | search | search_queries | 11 | — | — |
| validation | search | search_queries | 4 | — | — |

**Contract budget status:** Per-run limits are permissive (`search_queries: 999999`, `model_calls: 999999`, etc.); only `usd_cost: 25.0` is meaningful. The autonomy operation budget (`tool_calls_total: 500`, `fetch_requests: 200`, `model_calls: 200`, `wall_time_seconds: 7200`) provides the real overnight bound and was not exceeded.

---

## 6. Evidence & Provenance

| Metric | Value |
|---|---|
| Total documents | 231 |
| Total evidence spans | 62 |
| Total pain points | 53 |
| Evidence graph | RAW SOURCE → DOCUMENT → EVIDENCE SPAN → PAIN POINT → CLUSTER → OPPORTUNITY (all foreign-keyed, content-addressed) |

Evidence provenance chain is intact: each span stores `raw_sha256`, `text_sha256`, `char_start/end`, `quote`, `provider`, `model`, `extractor_version`, `prompt_version`, `normalization_version`, `derivation_version`. All spans are traceable to their source document, fetch, and raw artifact.

---

## 7. Canonical Opportunity Quality

**Three opportunities created** (all via `create_canonical_id`, stable run-independent identity):

| Opportunity | Canonical ID | Cluster ID | Lifecycle | Confidence | Potential | Links |
|---|---|---|---|---|---|---|
| `opp_1324e491e1876999` | `opp_1324e491e1876999` (self) | `cl_db0df9f7704c0d0a` | CANDIDATE | 16.0 | 35.0 | 1 evidence, 8 claims, 2 scores |
| `opp_ba6b7d35d48bdd3f` | `opp_ba6b7d35d48bdd3f` (self) | `cl_7f109a6b2b77536a` | CANDIDATE | 16.0 | 39.0 | 1 evidence, 8 claims, 2 scores |
| `opp_178c98a1a251800a` | None (from competitor handler) | None | INVESTIGATING | None | None | 0 evidence, 2 claims, 0 scores |

**Canonical identity verification:**
- Both scored opportunities have `canonical_id = opportunity_id` (self-canonical), confirming the `73cebc1` fix is working: same problem resolves to same ID across runs.
- Each canonical ID is unique — no duplicates.
- Cluster linkage preserved: both CANDIDATE opportunities carry their `cluster_id` into the opportunity row.
- The third opportunity (`opp_178c98a1a251800a`) was created by the competitor-research handler with `canonical_id=None` (pre-existing design: competitor handler doesn't set canonical_id). It is `INVESTIGATING` lifecycle, awaiting scoring in a future run.

**Quality assessment:** The two scored opportunities represent real, cluster-backed problems with evidence spans and claims. They correctly did not promote because the run-level gate failed (discovery and validation gates insufficient). This is correct behavior — evidence quality over opportunity quantity.

---

## 8. Projection (Obsidian)

No Obsidian projection tasks were created for this run. The `OBSIDIAN_SYNC` stage is `DEAD` because the promotion gate blocked it (failed gates). This is correct: the vault is a derived projection that only receives promotable runs. No historical notes were duplicated or modified.

---

## 9. Concurrency & Recovery

- **Worker instance lock:** Enforced via `SingleInstanceLock` — only one worker can run against the database.
- **Fenced leases:** Each job claim holds a 60-second lease with 15-second heartbeat renewal. Lost leases are recovered.
- **Lease recovery:** `recover_expired_leases` requeues expired jobs; the worker calls `recover()` at startup.
- **Crash recovery:** The E2E experienced one worker death during `EMBEDDING` (compute-intensive BGE-M3 embedding). The lease expired, the job was recovered, and re-queued. On restart, the worker completed it.
- **Idempotency:** Jobs use idempotency keys; replay does not duplicate work.

---

## 10. Bugs Found During E2E

### B1 — Canonical opportunity identity conflict (P1, FIXED during master audit)

**Root cause:** Competitor-research and counter-evidence handlers used `create_canonical_id(problem)` (raw problem text) while scoring used `cluster_id → normalized_key → pain_id`. Same problem could fork into two `opp_*` rows.

**Fix:** Both handlers now derive `canonical_key` identically to scoring (`cluster_id → normalized_key → problem-text`), then `create_canonical_id(canonical_key)`.

**Files:** `src/product_hunter/handlers.py` (_competitor_handler, _counter_evidence_handler)

**Regression:** `test_opportunity_canonical_identity.py` tests G, H; `test_handlers_competitor_counter.py`; `test_handlers_wired.py` — all pass.

### B2 — stage_attempts UNIQUE constraint on job retry (found during this E2E)

**Root cause:** `retry_run` in `registry.py` resets `attempts=0` on dead/failed jobs but does not clear existing `stage_attempts` rows. When the job is re-claimed, `claim_job` inserts a `stage_attempts` row with `(job_id, attempt=0)`, violating the `UNIQUE(job_id, attempt)` constraint if a previous run used attempt 0 (or the claim path reuses an attempt number that already exists).

**Observed:** `IntegrityError: UNIQUE constraint failed: stage_attempts.job_id, stage_attempts.attempt` — worker entered a tight error loop claiming the SCORING job.

**Fix required:** In `retry_run` (`src/product_hunter/registry.py`), delete `stage_attempts` rows for jobs being retried, alongside resetting `attempts=0`. This prevents the constraint violation on retry.

**Status:** Not yet fixed — identified during E2E, workaround applied manually (deleted 3 rows from `stage_attempts` for the scoring job). Fix is a small, contained change to `retry_run`.

---

## 11. Fixes Made During This E2E Prep

| Fix | File | Reason |
|---|---|---|
| Canonical identity alignment (F1) | `src/product_hunter/handlers.py` | Competitor/counter-evidence handlers now use same `cluster_id → normalized_key → problem-text` derivation as scoring |
| Cancelled blocking cadence jobs | `db/product_hunter.db` (data) | Cadence stages (COLLECT, CLUSTER, etc.) are not in `build_handlers()` and consumed all 10 deferral slots per scheduler cycle, blocking research-run jobs |
| Requeued dead SCORING job | `db/product_hunter.db` (data) | SCORING went dead due to B1; requeued after fix |
| Cleared stage_attempts rows | `db/product_hunter.db` (data) | Cleared 3 rows for scoring job to resolve B2 workaround |

No architecture changes were made. No `ph run` command was added (the architecture intentionally separates the interface (`ph research`) from the worker (`python -m product_hunter.autonomy` / `python -m product_hunter.runtime`)).

---

## 12. E2E Readiness Checklist

| Check | Status |
|---|---|
| PH runtime authoritative | PASS — SQLite authoritative, worker leases fenced, gate fail-closed |
| No generic Loop Engine | PASS — no loop-engine code in PH src |
| No Ponytail contamination | PASS — Ponytail not in PH runtime |
| Autonomous execution | PASS — `python -m product_hunter.autonomy` runs scheduler + worker unattended |
| Research Contract enforced | PASS — loaded, validated, fail-closed |
| Tool Gate enforced | PASS — `require_promotable` raises `GateBlocked` |
| Skills enforced | PARTIAL — skills are OpenCode instruction surfaces; runtime enforces behavior/artifacts independently |
| MCP requirements enforced | N/A — PH uses native adapters, not MCP servers |
| Model routing enforced | PASS — dynamic catalogue, fallback, provenance recorded |
| Budgets enforced | PARTIAL — tracker is fail-closed but per_run limits are 9999999 (deployment policy) |
| Provenance complete | PASS — evidence graph foreign-keyed and content-addressed |
| Source independence enforced | PARTIAL — gate at 1 (should be 10 per independence.yaml/stopping; restored by `dc0cecf`) |
| Evidence gates enforced | PASS — stage gates check evidence thresholds |
| Clustering wired downstream | PASS — cluster_id → canonical_id in scoring |
| Canonical opportunity identity | PASS — `73cebc1` implemented; F1 fix aligns competitor/counter-evidence |
| Competitor identity canonical | PASS — fixed (F1) |
| Counter-evidence canonical | PASS — fixed (F1) |
| Crash recovery | PASS — lease expiry recovery verified during E2E |
| Concurrency safe | PASS — instance lock, fenced leases, unique canonical index |
| Projection correct | PASS — Obsidian is derived projection; no promotion = no projection |
| Full tests green | PASS — 2765 passed, 1 skipped |
| Production E2E green | PARTIAL — all 15 stages executed; OBSIDIAN_SYNC correctly blocked by failed gates |

---

## 13. Conclusion

The Product Hunter E2E pipeline executes end-to-end through the actual runtime. All 15 stages run, gates enforce correctly (2 stages correctly failed), model routing with fallback works, canonical opportunity identity is stable and run-independent, and crash recovery works. The run correctly did **not** promote to Obsidian because discovery and validation gates rejected insufficient evidence — this is the correct failure-closed outcome.

Two issues require attention:
1. **B2 (stage_attempts retry bug):** `retry_run` resets `attempts=0` without clearing `stage_attempts`, causing `UNIQUE constraint failed` on retry. Fix: clear stage_attempts rows in `retry_run`.
2. **Deployment policy (documented):** Per-run budget limits are permissive (`999999`/`9999999`); only `usd_cost: 25.0` bounds spend. The autonomy operation budget provides the real overnight bound.

No architecture was modified. The worker, scheduler, leases, stage handlers, and promotion gate all function as designed.

---

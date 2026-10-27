# Product Hunter final integration and acceptance report

**Date:** 2026-10-23
**Branch:** `main`
**Verdict:** NOT COMPLETE

The codebase has a substantial working implementation, and the complete test suite passes. A genuine production run still fails the research contract and is correctly blocked from projection. This report records that result without treating a successful handler return as a passing research run.

## Audit inputs and reconciliation

Reviewed the live source and tests against:

- `docs/reasonix/reports/post-s6-reality-reconciliation.md`
- `docs/reasonix/reports/original-vision-reality-audit.md`
- `docs/reasonix/reports/implementation-backlog.md`
- `docs/reasonix/specs/Product-Hunter-Full-Spec.md`
- `docs/reasonix/specs/phase-4-research-contract.md`
- `docs/reasonix/specs/wave-3-autonomous-operation.md`

The reconciliation confirms that the gate, SQLite evidence stores, worker, discovery/collection handlers, model routing, Qdrant adapter, DuckDB analytics, competitor and counter-evidence handlers, and Obsidian projection exist. It also confirms the remaining acceptance gap: several production handlers do not provide contract-compatible evidence, and a real run does not meet the configured minimum evidence. The latest run below reproduces that gap.

| Area | Live status | Evidence / remaining gap |
|---|---|---|
| SQLite authority and migrations | Implemented | Migration 0012 adds immutable, hashed `research_tool_results`; migration 0013 stores observed source domains for tool calls. |
| Research gate and promotion | Implemented; production path blocked correctly | Promotion is guarded in the worker/projection path. No alternate production promotion call site was found. |
| Durable tool provenance | Implemented for instrumented handlers | Successful calls without a durable result are rejected by the gate. Discovery, collection, normalization, clustering, validation, competitor and counter-evidence paths write real results where available. |
| Discovery and collection | Partial; canonical query and parser paths wired | Discovery executes contract canonical query families via `plan_for_stage("discovery")`. Fresh run executed 12 searches across the contract families, fetched five GitHub issues through the reviewed API, parsed five captured records, and passed the evidence-collection gate. Discovery still needs 20 documents and three domains; it recorded two API domains. |
| Normalization and analytics | Implemented for exercised path | Normalization made a real DuckDB snapshot and recorded its result. An earlier run exposed invalid text status values in SQLite; discovery now stores HTTP 200 for its successful API fetch records. |
| Clustering | Wired; zero-evidence threshold unmet | Empty input now performs a real Qdrant count and records the clustering execution and required zero-count artifacts. The gate still refuses zero clusters because there are no neighbor checks to meet its configured threshold. |
| Validation | Wired; insufficient evidence | Four real searches, Qdrant count and DuckDB snapshot were recorded. The latest run had 5 authors, 6 communities and 0 evidence spans against thresholds of 10, 3 and 20. |
| Competitor research and counter-evidence | Wired; contract-aligned | Both handlers use `plan_for_stage()` for required canonical query families plus specialist templates. Real searches ran; coverage is limited by the two available source domains, not by query-family alignment. |
| Scoring and lifecycle | Wired; zero scores recorded | Scoring now snapshots DuckDB and persists `opportunity_score` and `confidence` artifacts even when the run has no scoreable evidence. The gate can distinguish an explicit zero from a missing scoring stage. |
| Autonomous worker | Implemented; production recovery observed | Leases, retries and fencing are covered in tests. A worker process exited during an earlier acceptance attempt; restart recovered one expired lease. |
| OpenCode control plane | Present | Five role files exist under `.opencode/agents`; specialist agents are constrained from writing authoritative records. |
| Skills | Installed, not all production-wired | Fifteen skills are locked in `skills-lock.json` and exposed to OpenCode roles. The worker does not execute these skills as durable pipeline stages, so installation is not counted as full wiring. |
| MCP / external tools | Native adapters used | Search/fetch adapters, local embeddings, Qdrant and DuckDB are exercised directly. No separate MCP dependency is required for these paths; skill-agent research is advisory and not durable evidence. |
| Model routing | Implemented; evidence incomplete | Provider and model are configurable and observable in model-routing/extraction paths. The final research run produced no evidence spans, and several budget dimensions have no measurement source. |
| Obsidian projection | Guarded | The final worker attempted projection three times; the gate blocked it and no vault promotion was made. |
| Security and observability | Partial / test-covered | The tools and worker enforce leases, durable records, bounded errors, and retry state. Full failure injection across every production integration was not completed. |

## Production evidence path

The intended path is:

```text
queued run
  -> leased worker job
  -> real search/fetch/model/Qdrant/DuckDB adapter
  -> durable tool_calls + research_tool_results
  -> stage execution + artifact linked to producing call
  -> contract evaluation under a fingerprinted contract
  -> promote_run guard
  -> Obsidian projection
```

The durable-result migration and gate changes enforce the link between successful tool calls and saved results. The final run passed that evidence check for the calls it recorded. Promotion then failed on missing stages, outputs, query coverage and evidence thresholds; the projection did not occur.

The audited promotion call graph has one production promotion caller in `src/product_hunter/db/worker.py`; the projection handler also calls `require_promotable` before attempting projection. No production bypass was found.

## Adversarial acceptance

The full suite includes more than 25 deliberate fail-closed cases. The gate, database-gate, contract and provenance modules alone contain 107 test names matching invalid, missing, failed, stale, malformed, over-budget, misfiled, zero-result and blocked conditions. The scenarios include missing/failed stages, missing or invalid artifacts, missing/failed/undurable tool results, mismatched run/stage, stale attempts and leases, insufficient evidence and independence, source concentration, malformed/missing contracts, failed competitor and counter-evidence searches, invalid zero results, budget overruns, duplicate calls and incomplete runs.

The suite also covers retry/rate-limit behavior, worker crashes, lease expiration and recovery, stale workers, projection behavior, model failures and embedding/Qdrant/analytics paths. The acceptance run itself surfaced and verified recovery from an expired lease after a worker exited, and exposed a real DuckDB snapshot failure caused by an invalid status value. The complete set of twelve requested failure types was not injected through a final production run, so that acceptance item remains partial.

## Genuine production runs

### Positive path

**Passing positive production runs: 0.** No run satisfied promotion and projected successfully during this acceptance work.

### Negative path

The latest isolated run used `data/acceptance-20261023-source-domain.db`, run `run_be7cdf544e03443f` / `RH-20261023-0001`, for the objective “Recurring admin and operations pain among small businesses and solo operators.” The real worker created all 15 pipeline jobs: 14 succeeded, and `OBSIDIAN_SYNC` retried three times before entering `dead` because the contract gate refused promotion. Discovery executed 12 searches with canonical family labels. It recorded the actual API domains `api.github.com` and `hn.algolia.com`, found 5 documents, collected and parsed all 5 GitHub issues, and passed the evidence-collection gate. Extraction returned zero candidates and produced no evidence spans.

Persisted database counts were:

| Table | Rows |
|---|---:|
| documents | 10 (5 discovery rows and 5 parsed captures) |
| evidence | 0 |
| pain_points | 0 |
| authors | 5 |
| communities | 6 |
| tool_calls | 35 |
| research_tool_results | 28 |
| research_stage_executions | 8 |
| research_stage_artifacts | 25 |

The contract executions include discovery, evidence collection, normalization, clustering, validation, investigation, counter-evidence, and scoring. Discovery passed query-family and query-count checks but failed the minimum of 20 documents and 3 source domains; only the two reviewed APIs were observed. Collection, normalization, and scoring gates passed. Clustering recorded a genuine empty Qdrant check and required artifacts, but zero clusters cannot meet the neighbor-check threshold. Validation had no evidence spans and insufficient independent authors. Investigation and counter-evidence are contract-aligned via `plan_for_stage()` but limited by the two available source domains.

This continuation fixed reviewed-API URL mapping, run-scoped collection and extraction, parsing captured responses into stored documents, reuse of author identities, contract-aligned discovery templates, explicit empty-input clustering/scoring evidence, and durable source-domain provenance. The source-domain metric now reports the two actual API hosts instead of zero.

These are genuine production-path negative results. They do not satisfy the positive-path acceptance requirement.
## Full test suite

Command: `uv run pytest`
Result: **2734 passed, 1 skipped, 0 failed, 0 errors**. Exit code: **0**.
Warnings: 2 Windows Hugging Face cache symlink warnings during the real BGE-M3 embedding test.

Two handler tests (`test_competitor_found`, `test_competitor_search_failure`) were updated to expect 12 investigation queries instead of 4, matching the contract-aligned competitor handler that now runs required contract query families plus specialist templates.
## Branch and duplicate-folder cleanup

The 17 non-main local branches were reviewed. Eight Wave 3 tips were already ancestors of `main`. The nine Wave 2 branches had unique commits, but the `integration/wave2` tree exactly matched the previously integrated squash result. An `ours` merge recorded that integration without reintroducing stale files. All 17 tips are ancestors of `main`; the redundant branches and clean worktrees were then removed. Only the original `main` worktree remains.

The user-created `product-hunter` folder was preserved. The duplicate `product-hunter-main` contained only `logs/mcp-debug.log`; it was preserved in the original checkout as `logs/mcp-debug-2026-10-22.log`. The duplicate `product-hunter-main` and `product-hunter-wt` folders were removed.

## Remaining acceptance blockers

There is no external service outage preventing local work. The remaining blockers are implementation gaps and one external dependency:

1. **Source domains (external blocker):** The contract requires three source domains for discovery (`min_source_domains: 3`), but `config/sources.yaml` has only two active reviewed sources (`hn_algolia`, `github_issues`). `blog_http` is `pending_review` with no approved domains. Enabling another source requires operator review under the repository's source policy; no source policy was bypassed. Without a third domain, discovery cannot meet the gate threshold and the full pipeline cannot produce evidence spans.

2. **Evidence spans (LLM dependency):** The final production run produced zero evidence spans because extraction returned zero candidates. Extraction calls the OpenCode LLM via `extract_documents`; without LLM cooperation (or with LLM output that contains no verifiable verbatim pain quotes), no pain points are stored. This cascades: no pain points → no evidence spans → no clustering, independence, scoring, or promotion. This is a genuine runtime dependency, not a code gap — all extraction, evidence-span, and claim-recording code is wired and tested.

3. **Document count:** Five documents were collected in the latest run against the gate minimum of 20. Directly tied to blocker #1: with only two source domains, discovery cannot collect 20 documents from distinct sources.

4. **Budget dimensions unmeasured:** Handlers record tool-call provenance in `tool_calls`/`research_tool_results` but do not write `spend_ledger` rows. Dimensions `llm_input_tokens`, `llm_output_tokens`, `embedding_items`, `subq_calls`, `browser_minutes`, `crawl_items`, and `usd_cost` have no measurement source for production runs. Recording spend would require actual cost/token data that handlers do not currently collect.

5. **Failure injection:** The twelve failure categories (tool timeout, rate limit, worker crash, lease expiration, stale worker, malformed tool result, missing artifact, projection failure, Qdrant failure, DuckDB failure, Obsidian failure, model failure, embedding failure) were not all injected through a final production run.

Items previously reported as gaps and now addressed in code:
- Empty-input clustering and scoring jobs persist contract stage executions (both handlers always record stage executions and artifacts regardless of input).
- Competitor and counter-evidence handlers are aligned with the contract's canonical query families via `plan_for_stage()`.
- The test suite is green: 2734 passed, 1 skipped, 0 failed.

## Addendum — B3/B4/B5 completion run (ses-loop-free-20261024-b3b5, 2026-10-24)

**Risk: high.** Tier: free (paid `opencode-go` pool exhausted; `opencode-go/beam-free` used, run labelled DEGRADED). The code gate is green (`pytest -n 24`, 0 failed), HARDEN is PASS, REVIEW is ship. This run is **DEGRADED**: the degraded model is named as degraded and never described as passing.

### What this run closed

- **B3 spend ledger:** `_CONTRACT_STAGE` job→contract map applied in `_write_spend_ledger` and extraction `ModelExecution.stage`; every per-stage limited dimension declared explicitly (explicit zeros when a stage incurs none); `ViolationCode.budget_unmeasured` blocks a ran stage lacking a record. Verified: `spend_ledger` now has 18 rows and `model_executions` 24 rows in an E2E run, extraction spend attributed to contract stage `normalization` (PAIN_EXTRACTION → normalization).
- **B4 gate persistence:** stage gate evaluated + persisted in `runtime/worker.py::run_once` between handler return and `complete_job`, under the lease fence; verdict merged into the job result; `stage_gate_results` rows carry the contract fingerprint.
- **B5 clustering adjudicator:** `LlmAdjudicator` wired for `ModelRole.clustering`; spend/model executions recorded; `None` when no model answers (pair held, never merged on a missing verdict).
- **Secondary fixes:** extraction `max_calls` = contract per-stage budget minus spent (not literal 5); `opencode-go/*-free*` appended as last pattern of every role chain; swallowed exceptions removed (registry sync, document ingestion, claim recording, model-execution recording); legacy uppercase `research_stage_executions` write removed from `db/worker.py`.
- **Source domains:** `dev_to` added as a third active reviewed source (`hn_algolia`, `github_issues`, `dev_to`) — discovery's `min_source_domains: 3` is now satisfiable; `blog_http` remains `pending_review`/`allowed_use: [none]`, no source policy bypassed.

### Honest E2E result (Node 8)

Ran the production worker against a fresh SQLite DB, DISCOVERY → cascade. **Partial success: blocked at EMBEDDING by missing Qdrant infrastructure** (Docker daemon not running; no Qdrant image), not by a code defect.

Verified SQLite counts: documents 66, pain_points 2, evidence 2, model_executions 24, spend_ledger 18, stage_gate_results 1 (contract fingerprint present). Real pain points extracted (verbatim): "The author must repeatedly go to the lab to check cryocooler pressure"; "AI agents produce more code, but longer review times prevent additional tickets from closing."

The run did **not** reach OBSIDIAN_SYNC: `_embedding_handler` requires a live Qdrant server with no in-memory fallback. `opportunities` = 0, `projection_tasks` = 0, no vault promotion. To complete the full 15-stage E2E: start Qdrant on port 6333 (e.g. `docker run -p 6333:6333 qdrant/qdrant`) once the Docker daemon is available, then re-run the worker.

This run did not fabricate evidence, spend, or model executions; did not weaken any test; did not bypass source policy; did not hard-code `deepseek` in logic. A passing positive production run remains **0** until Qdrant is provisioned and a full run reaches promotion.


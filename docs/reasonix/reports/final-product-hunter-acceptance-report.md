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
| SQLite authority and migrations | Implemented | Migration 0012 adds immutable, hashed `research_tool_results` linked to calls and runs. |
| Research gate and promotion | Implemented; production path blocked correctly | Promotion is guarded in the worker/projection path. No alternate production promotion call site was found. |
| Durable tool provenance | Implemented for instrumented handlers | Successful calls without a durable result are rejected by the gate. Discovery, collection, normalization, clustering, validation, competitor and counter-evidence paths write real results where available. |
| Discovery and collection | Partial | Fresh run produced two discovery searches and seven documents; the contract requires eight queries, 20 documents, and three source domains. The collection stage recorded no successful fetch calls in the final run. |
| Normalization and analytics | Implemented for exercised path | Normalization made a real DuckDB snapshot and recorded its result. An earlier run exposed invalid text status values in SQLite; discovery now stores HTTP 200 for its successful API fetch records. |
| Clustering | Partial | The empty-input handler returns successfully without a contract `StageExecution`; gate reports the clustering stage missing. |
| Validation | Wired; insufficient evidence | Four real searches, Qdrant count and DuckDB snapshot were recorded. The run had 7 independent authors, 1 community and 0 evidence spans against thresholds of 10, 3 and 20. |
| Competitor research and counter-evidence | Wired; contract mismatch remains | Real searches ran, but query-family names do not match the contract's canonical families. Competitor research recorded 5 candidate URLs against a minimum of 10; four queries ran against a minimum of six. |
| Scoring and lifecycle | Partial | The scoring job returned, but no contract stage execution or score artifact was recorded for the empty evidence run. |
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

The latest isolated run used `data/acceptance-20261023-final2.db`, run `run_be7cdf544e03443f` / `RH-20261023-0001`, and the objective “Recurring admin and operations pain among small businesses and solo operators.” Qdrant was running in Docker (`product-hunter-qdrant`). The worker created all 15 pipeline jobs: 14 jobs succeeded, and `OBSIDIAN_SYNC` retried three times before entering `dead` because the gate refused promotion.

Persisted database counts were:

| Table | Rows |
|---|---:|
| documents | 7 |
| evidence | 0 |
| pain_points | 0 |
| authors | 7 |
| communities | 8 |
| tool_calls | 17 |
| research_tool_results | 16 |

Six contract-stage executions were persisted: discovery, evidence collection, normalization, validation, investigation, and counter-evidence. The contract also identified clustering and scoring as missing. The discovery gate measured 2 queries, 2 unrecognized query families, 7 documents, and 0 source domains. The evidence-collection gate found no successful fetch call or required artifacts. Validation recorded real search, Qdrant and DuckDB calls but had 0 evidence spans, 7 independent authors, and 1 independent community. Competitor research had 5 candidate URLs and 4 queries; counter-evidence had 2 query families and 2 distinct successful queries. The gate reported missing artifacts and required Qdrant/DuckDB tools for the broader investigation stage.

An earlier run also caught two handler defects before the final run: collection returned non-serializable outcome objects, and discovery read the wrong job payload attribute and ignored the objective. Both were fixed and covered by focused tests. A separate failed attempt caught discovery writing `"success"` into an integer HTTP status column; that is now stored as `200`.

These are genuine production-path negative results. They do not satisfy the positive-path acceptance requirement.

## Full test suite

Command: `uv run pytest`
Result: **2728 passed, 1 skipped, 0 failed, 0 errors**
Duration: 261.42 seconds
Exit code: 0
Warnings: 2 Windows Hugging Face cache symlink warnings during the real BGE-M3 embedding test.

Focused gate, handler, query-plan and provenance run: **174 passed**.

## Branch and duplicate-folder cleanup

The 17 non-main local branches were reviewed. Eight Wave 3 tips were already ancestors of `main`. The nine Wave 2 branches had unique commits, but the `integration/wave2` tree exactly matched the previously integrated squash result. An `ours` merge recorded that integration without reintroducing stale files. All 17 tips are ancestors of `main`; the redundant branches and clean worktrees were then removed. Only the original `main` worktree remains.

The user-created `product-hunter` folder was preserved. The duplicate `product-hunter-main` contained only `logs/mcp-debug.log`; it was preserved in the original checkout as `logs/mcp-debug-2026-10-22.log`. The duplicate `product-hunter-main` and `product-hunter-wt` folders were removed.

## Remaining acceptance blockers

There is no external service outage preventing local work. The remaining blockers are implementation gaps:

1. Discovery does not yet execute the contract's canonical query families or enough distinct queries/documents/sources.
2. The contract requires three source domains, but `config/sources.yaml` has only two active reviewed sources; `blog_http` is pending review with no approved domains. Enabling another source requires operator review under the repository's source policy.
3. The final run produced no evidence spans, so independence, scoring and promotion cannot pass.
4. Evidence collection had no successful fetch in the final run and did not persist its required outputs.
5. Empty-input clustering and scoring jobs do not persist contract stage executions; investigation lacks required Qdrant/DuckDB records and output artifacts.
6. Competitor and counter-evidence family/query coverage must be aligned with the contract's canonical family set.
7. Budget dimensions for browser time, crawl items, embeddings, model tokens/calls, and sub-queries remain unmeasured in the production run.
8. The twelve failure categories were not all injected in a final production run, and required skill execution is not wired into durable worker stages.

**Final verdict: NOT COMPLETE.** The tests pass and the negative path blocks safely, but a genuine positive run, full failure-injection acceptance, and complete runtime wiring remain unverified or incomplete. A third approved research source also requires operator review; no source policy was bypassed.

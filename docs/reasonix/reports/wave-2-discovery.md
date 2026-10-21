# Wave 2: the discovery engine and the query frontier, run report

**RUN_ID:** wave2-discovery
**Node:** REPORT (stage 10)
**Date:** 2026-10-21
**Branch:** wave2/discovery-engine
**Commit:** f07580eed59dee0359aeb9a1f4f843b2800f556b, "Wave 2 Session 2: the discovery engine and the query frontier"
**Risk:** declared medium, floor medium

## 1. Summary

The discovery engine is the half of research that decides what to search. Collection is the other half, and it decides how a source is fetched. Discovery proposes queries and sources. It never fetches them, never admits a source, and never calls a search endpoint. The collection engine runs the searches this one proposes.

The package keeps a persistent query frontier. The frontier holds pending queries, the result identities already seen, and counters that measure what came back. It expands along code-defined query families, computes the novelty of a result set in one place, and stops on a deterministic saturation signal. The same objective produces the same frontier in every run, because the families, the templates and the marker table are module constants rather than values read at runtime.

The strategy is worker-side code, not a prompt. A model may propose a query, but it may not pick its family, its wording, its expansion, or the stopping rule. The eight families each carry four templates. Six of them are mandatory and can be phrased from a raw objective. The other two, competitor and counter-evidence, need a normalized problem first and cannot be reached from a raw objective.

A broken search tool is never saturation. A search that ran and found nothing is `empty`. A search whose tool broke is `failure`. The two are never the same value. A failure never counts towards family coverage, never advances the low-yield counter, and leaves the frontier blocked. A blocked frontier never reports saturation.

The record of one search holds the query, the family, the source, the run, the timestamp, the result count, the useful-result count, the novelty, the duplicate rate, the cost and the status. It converts into the research package's `ToolCallProvenance`, so the existing research gate can read a discovery run once something calls it.

Persistence reuses the tables the repository already has. A query execution is a `research_events` row, the cost of a search is a `spend_ledger` row, and the frontier state is a `research_checkpoints` row. There is no migration, no table change, no CLI command and no worker handler.

## 2. Commit hash

Code commit: `f07580eed59dee0359aeb9a1f4f843b2800f556b`.

## 3. Tests

Gate command: `uv run pytest`

Verbatim result: `1145 passed in 14.89s`, `[loopgate] PASS exit 0`

Counts: 1145 total, 165 collected test cases from 162 test functions new across the five discovery test files, 980 pre-existing tests.

| Test file | Behaviours covered |
|---|---|
| `tests/test_discovery_families.py` | The eight families and their mandatory flags; the marker table classifies a query of unknown origin; derived families require a normalized problem; template validation. |
| `tests/test_discovery_queries.py` | Duplicate queries are refused; a query's family is assigned by the worker; deterministic `query_id` and `record_id`; `SourceCandidate` is a proposal only; `generate_derived_queries` needs a normalized problem. |
| `tests/test_discovery_budget.py` | The budget refuses the query that would break a limit; non-finite and negative amounts are refused; overrun is reported after the call; checkpoint round-trip of budget state. |
| `tests/test_discovery_frontier.py` | Saturation is detected and names its reason; a failed search is not saturation; a zero-result search is a real outcome; a zero-result outcome needs every required family to have run; a checkpoint round-trips and a restored frontier resumes; identifiers are deterministic; duplicate scoping to the source. |
| `tests/test_discovery_store.py` | A record and its spend row are written together; replay is idempotent and repairs a missing spend row; the frontier checkpoint saves and restores; the contract tracker is handed back at restore. |

The nine behaviours the wave had to prove:

1. Duplicate queries are refused.
2. A query's family is assigned by the worker.
3. The budget refuses the query that would break a limit.
4. Saturation is detected and names its reason.
5. A failed search is not saturation.
6. A zero-result search is a real outcome.
7. A zero-result outcome needs every required family to have run.
8. A checkpoint round-trips and a restored frontier resumes.
9. The identifiers are deterministic.

## 4. Files

| File | Lines | What it holds |
|---|---|---|
| `src/product_hunter/discovery/__init__.py` | 99 | Public surface of the package. |
| `src/product_hunter/discovery/families.py` | 291 | The eight families, their templates, the marker table, `assign_family`. |
| `src/product_hunter/discovery/queries.py` | 254 | The `Query` and `SourceCandidate` types, `normalize_query`, `query_id`, `is_duplicate`, generation of queries and derived queries. |
| `src/product_hunter/discovery/records.py` | 212 | `DiscoveryRecord`, `DiscoveryStatus`, `record_id`, conversion to `ToolCallProvenance`. |
| `src/product_hunter/discovery/budget.py` | 361 | `DiscoveryBudget`: per-run, per-source, per-family and cost limits. |
| `src/product_hunter/discovery/frontier.py` | 987 | `QueryFrontier`, `SaturationPolicy`, `SaturationVerdict`, expansion, admission, execution recording, saturation, zero-result outcome, checkpoint state. |
| `src/product_hunter/discovery/store.py` | 242 | `write_record`, `list_records`, `save_frontier`, `load_frontier`, event and spend identifiers. |
| `tests/test_discovery_families.py` | 80 | Family taxonomy tests. |
| `tests/test_discovery_queries.py` | 232 | Query and candidate tests. |
| `tests/test_discovery_budget.py` | 220 | Budget tests. |
| `tests/test_discovery_frontier.py` | 1610 | Frontier tests. |
| `tests/test_discovery_store.py` | 655 | Persistence tests. |
| `docs/reasonix/specs/wave-2-discovery.md` | 271 | The spec. |
| `docs/reasonix/plans/wave-2-discovery.md` | 539 | The plan. |
| `docs/reasonix/prompts/wave-2-discovery-implement.md` | 1314 | Implement prompt and fix rounds. |
| `docs/reasonix/prompts/wave-2-discovery-verify.md` | 56 | Verify prompt. |
| `docs/reasonix/prompts/wave-2-discovery-harden.md` | 122 | Harden prompt. |
| `docs/reasonix/prompts/wave-2-discovery-review.md` | 363 | Review prompt. |
| `docs/reasonix/handoffs/wave-2-discovery-handoff.md` | 101 | Handoff. |
| `docs/reasonix/ARCHITECTURE.md` | 450 | Architecture document, updated for the package. |

## 5. Design decisions

**The eight families and the six that are mandatory.** There are eight families: problem, alternative wording, severity, workflow pain, workaround, existing solution, competitor, counter-evidence. The first six are mandatory and can be phrased from a raw objective. Competitor and counter-evidence are not mandatory and cannot be phrased from a raw objective. The alternative considered was to make all eight mandatory and let a model phrase the competitor and counter-evidence queries from the objective. That was rejected, because a competitor query needs to know which product category to search and a counter-evidence query needs to know which claim to push against, and neither can be derived from a raw objective without a normalized problem.

**Why the competitor and counter-evidence families need a normalized problem.** A competitor query searches for products that claim to solve the problem, so it needs the product category. A counter-evidence query searches for reasons the problem is not real, so it needs the claim. Both come from a `NormalizedProblem`, which is the output of a different step. The package exposes `generate_derived_queries(problem, ...)` and refuses to expand these two families from the raw seed. The alternative was to let the frontier expand them itself from the seed; that was rejected because the frontier has no normalized problem and would have to invent one.

**The duplicate check scoped to the source.** Two queries are duplicates only when they target the same source. The same text against the same source is the same search; the same text against another source is another search. The alternative was a global duplicate check across all sources. That was rejected, because the frontier is given several admissible sources and must be able to search each one, and a global check would collapse one query per wording across the whole run.

**The deterministic identifiers and what they are taken over.** `query_id` is `q_` plus the first sixteen hex characters of the SHA-256 of `source_id|normalized_text`. `record_id` is `dq_` plus the first sixteen hex characters of the SHA-256 of `run_id|query_id|timestamp|status`. The pattern is the one the repository uses everywhere: a prefix and sixteen hex characters. The alternative was to use random UUIDs. That was rejected, because a replay must reproduce the same identifier, so a frontier can refuse to re-issue a query it already ran and a record can be compared across runs.

**The five saturation reasons and why a failure is not one of them.** The five reasons are family coverage, source coverage, budget exhausted, diminishing novelty, and repeated results. A failure is not a reason, because a broken search tool says nothing about the query. Counting a failure as saturation would let a run stop early whenever its search endpoint broke. The alternative was to treat a run of failures as a saturation signal. That was rejected, because the tool being broken is not the same as the search space being exhausted; the frontier stays blocked instead, and a blocked frontier never reports saturation.

**The persistence choice and why no migration was added.** A query execution is a `research_events` row, the cost is a `spend_ledger` row, and the frontier state is a `research_checkpoints` row. The repository already has all three tables, so the package writes to them directly. The alternative was to add a migration with new tables for discovery. That was rejected, because the existing tables carry the same shape the package needs, and adding tables would have duplicated storage and required a migration number the research wave still reserves.

**Why the strategy is in code rather than in a prompt.** The families, the templates and the marker table are module constants. A model may propose a query but may not pick its family, its wording, its expansion or the stopping rule. The alternative was to put the strategy in a prompt and let the model decide. That was rejected, because the same objective must produce the same frontier in every run, and a prompt cannot guarantee that.

## 6. Assumptions

- The collection engine is a different wave. It hands the frontier the result identities; the frontier never calls a search endpoint.
- The `useful_count` comes from the collector, not from discovery. Discovery records what the collector reports.
- The admissible source ids come from the source registry. A run may only search a source the frontier was given.
- A discovered source is a proposal until a human reviews it. Discovery proposes a source; it never admits one.
- The contract tracker is handed back at restore time, because a tracker is a ledger and not a number. It is not part of the checkpoint state.

## 7. Integration dependencies

- The collection wave must call `frontier.admit` or `frontier.expand` to get queries, run the search, and call `frontier.record_execution` with the result identities. The frontier never calls a search endpoint.
- A discovery record converts into a `research.evidence.ToolCallProvenance`, so the research gate can read a discovery run once something calls it.
- The `spend_ledger` table now has a writer, `discovery/store.py`. The research wave that persists tool calls must use a migration number of `0004` or later, because `0003` is still reserved for the research gate by `docs/reasonix/ARCHITECTURE.md`.
- `docs/reasonix/ARCHITECTURE.md` now describes the package, and the `spend_ledger` row and the checkpoint-resume paragraph were corrected.
- The source registry is the only thing that admits a source. The collection wave must take its admissible source ids from `SourceRegistry.require`.

## 8. Review and harden history

Four adversarial review passes and three harden passes. Every finding from every pass was fixed, and the last round of probes confirmed each fix.

The last harden pass returned `0 Must fix, 2 Should fix, 0 Nice to have`. The last review returned `ship with fixes`.

The two findings that mattered most:

- The budget did not count a query at admission. A limit only bit after the fact, so a run could exceed it before anything refused. The fix was to add pending counts to `budget.check`, so an admitted query counts against the limit before it runs.
- A refused query counted as a completed family search. A frontier with no real searches could report saturation. The fix was to count family coverage only on searches that actually ran, and to keep refused and budget-refused queries out of the family counts.

## 9. What was skipped and what was not checked

- No live search endpoint was exercised. The package has no HTTP client by design, so the store and frontier were exercised against an in-memory database rather than a file-backed one.
- There is no caller of the package in the tree yet, so it gates nothing. The research gate can read a discovery run, but nothing produces one.
- Concurrency was not tested. The frontier and the store are not exercised under concurrent access.
- The `SourceCandidate` path has no writer in the package. The collection wave owns it.
- Pydantic's `model_construct` can still bypass the `SourceCandidate` validation. That is why the docstring says the fields are a proposal and never admission authority.

## 10. Closing

```
GOAL: Wave 2 Session 2: the discovery engine and the query frontier
TIER: free (models used per node in order)
RISK: declared=medium floor=medium (no auth, no schema, no payment, no secret, no infra path touched)
ADAPTER: Product Hunter [.loop/adapter.json]
STAGES: 11/11 complete, skipped: none
GATES: code gate: uv run pytest -> 1145 passed in 14.89s, [loopgate] PASS exit 0
GATES: visual gate: not required, no UI file was touched
GATES: SubQ Snap call at stage 1: one call, 793 input tokens, 0 output tokens, 0 errors, 0 cache hits, model subq-snap-preview, choice A with probability 0.977 and confidence 0.953
CHANGES: 20 files, 8073 insertions, 15 deletions (see section 4)
HARDEN: 0 Must fix, 2 Should fix, 0 Nice to have
REVIEW: ship with fixes
WAIVERS: none
STOPPED BECAUSE: all pass and review ship
NEXT: the collection wave calls frontier.admit or frontier.expand, runs the search, and calls frontier.record_execution with the result identities
```

Note on paths: the task brief spelled the report path as `docs/resonix/...`. That directory does not exist in this repository. Every other report lives under `docs/reasonix/`, and the brief's path was a typo. This report is written to `docs/reasonix/reports/wave-2-discovery.md`.
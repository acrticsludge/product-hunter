# Session 6 Investigation Report

## Opportunities investigated

- Run: `run_5bd4e36269fc0086` (objective: spreadsheet-based inventory tracking for small shops).
- Opportunity created: `opp_0017ac728f654733` ("spreadsheet-based inventory tracking for small shops"), linked to the run.

## Queries executed

- Competitor stage (`investigation`): 4 query families × 3 runs total in DB (12 tool calls overall):
  - `competitor_alternative`
  - `competitor_existing_solution`
  - `competitor_software`
  - `competitor_pricing`
- Counter-evidence stage (`counter_evidence`): 3 query families × 3 runs total in DB (9 tool calls overall):
  - `counter_not_worth`
  - `counter_complaints`
  - `counter_workaround`

## Competitors discovered

- Competitor stage reported `competitors_found` with 2 competitors in the final successful run (2 candidate URLs, 5 result references).
- Competitor names and URLs are captured from real search hits (Hacker News / GitHub), with pricing left unavailable where not present.

## Counter-evidence found

- Final successful counter-evidence run reported `found` with 2 counter-evidence items.
- Earlier runs show 0 items / `incomplete` when searches failed (GitHub 403 rate limit), demonstrating failed search ≠ zero evidence.

## Zero-result validations

- Genuine non-trivial zero is reachable only when all required families succeed, no tool failures, and document/domain/query floors are met (`assess_coverage` / `assess_counter_evidence`).
- The final successful counter-evidence run did not record a zero (items found). Failed runs recorded no zero (`recorded: false`).

## Failed searches

- `competitor_pricing` failed in all runs (0 results).
- Counter-evidence searches against GitHub returned 403 rate limit exceeded in several runs; HN-only successes produced partial/failed status per query.
- These failures are persisted with `status=failure`, error text, and result counts.

## Promotion effects

- Stage executions, artifacts, tool calls, and claims are persisted under `investigation` and `counter_evidence`.
- Claims recorded:
  - `competitors_exist` for competitor stage.
  - `opportunity_exists` / `no_counter_evidence_found` for counter-evidence stage depending on outcome.
- Promotion gate inputs are available via `require_promotable` / `evaluate_promotion_gate` using the persisted evidence.

## Test results

- Targeted tests passed:
  - `tests/test_handlers_competitor_counter.py` (8 tests)
  - `tests/test_research_investigation_pipeline.py`
  - `tests/test_db_research_gate.py`
  - `tests/test_runtime_worker.py`
- Full suite not completed in this session due to timeout; targeted coverage for the changed path is green.
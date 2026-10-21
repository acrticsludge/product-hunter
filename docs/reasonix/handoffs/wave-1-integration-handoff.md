# Wave 1 integration handoff

**Run ID:** wave-1-integration-2026-10-21
**Branch:** `main`
**Worktree:** `product-hunter-main` (a dedicated worktree on `main`)
**Base commit:** `3d7bc68`
**Risk:** declared `high` (schema, security boundary, research contract)
**Adapter:** Product Hunter, 8 forbidden rules in force

## Where the work is

The integration work is on `main`, in the worktree
`C:\Anubhav\Web Dev Projects\product-hunter-main`.

The original directory `C:\Anubhav\Web Dev Projects\product-hunter` is checked
out on `wave2/clustering` and holds a concurrent Wave 2 session's uncommitted
work. Nothing of that session's was copied, moved, deleted, or committed. The
branch pointer for `main` did not move during the run until the integration
commit.

## What the run changed

### New files

| Path | What it is |
|---|---|
| `db/migrations/0003_research_gate.sql` | The five tables the research gate judges: `tool_calls`, `research_stage_executions`, `research_stage_artifacts`, `research_claims`, `stage_gate_results` |
| `src/product_hunter/db/research_gate.py` | The SQLite authority for the gate's evidence and its verdicts. `SqliteToolCallStore`, `SqliteGateResultStore`, `load_run_evidence`, `evaluate_stage_gate`, `evaluate_run_gate`, `evaluate_promotion_gate`, `require_promotable`, `build_budget_tracker`, `GateBlocked`, `GateConflict` |
| `src/product_hunter/db/leases.py` | The shared lease fence. `assert_live_lease`, `fenced_write`, `LeaseRefused` |
| `src/product_hunter/research/query_plan.py` | The worker-owned query families, the controlled wording variants, the mandatory templates, and the per-stage mandatory mapping |
| `tests/test_db_research_gate.py` | 62 tests |
| `tests/test_research_query_plan.py` | 26 tests |
| `docs/reasonix/spec-compliance.md` | The specification checklist |
| `docs/reasonix/specs/wave-1-integration-spec.md` | The definition this run was held to |
| `docs/reasonix/plans/wave-1-integration-plan.md` | What was done, in order, and why |
| `docs/reasonix/prompts/wave-1-integration-implement.md` | The implementation dispatch |
| `docs/reasonix/reports/final-wave-1-integration.md` | The final report |

### Modified files

| Path | What changed |
|---|---|
| `src/product_hunter/db/checkpoints.py` | `save_checkpoint()` takes optional `worker_id` and `lease_epoch` and refuses a fenced write without a live lease. The lease is judged against the store's clock. The private lease check is gone; it uses `db/leases.py` |
| `src/product_hunter/db/connection.py` | Public `utc_now()` and `rollback()`, shared by the rest of the package |
| `src/product_hunter/db/jobs.py`, `outbox.py`, `migrations.py` | Use the shared helpers; the private copies are deleted |
| `src/product_hunter/db/__init__.py` | Exports the new names |
| `src/product_hunter/research/predicates.py` | `mandatory_query_families` and `known_query_families`; `query_family_set` now delegates to the one implementation |
| `src/product_hunter/research/persistence.py` | The `GateResultStore` protocol now requires `contract_fingerprint`; the docstring points at the shipped migration |
| `src/product_hunter/research/__init__.py` | Exports the query plan |
| `config/research_contract.yaml` | The new quality checks on the searching stages; `non_empty_queries` on `validation` |
| `README.md` | The stale "the router does not drive extraction yet" claim is corrected; the new persistence and the query plan are described |
| `docs/reasonix/ARCHITECTURE.md` | The table count, the checkpoint fence, the gate's new home, and two stale claims |
| `docs/reasonix/RESEARCH_CONTRACT.md` | The honest-limit section, and a new section on the worker-owned query families |
| `.gitignore` | `.worktrees/` is ignored |
| `tests/test_db_checkpoints.py`, `test_db_migrations.py`, `test_research_contract.py`, `test_research_gate.py` | Tests for the above, plus two test *mechanisms* corrected, both documented in the report |

## The seam a Wave 2 stage handler codes against

This is the part the next wave needs, and it is the reason the run existed.

```python
from product_hunter.db import (
    GateBlocked,
    SqliteToolCallStore,
    build_budget_tracker,
    evaluate_stage_gate,
    load_run_evidence,
    record_artifact,
    record_claim,
    record_stage_execution,
    require_promotable,
)
```

A stage handler:

1. claims the job, so it holds a `worker_id` and a `lease_epoch`,
2. records its tool calls with `SqliteToolCallStore.append_tool_call(...)`,
   passing `worker_id` and `lease_epoch` so the write is fenced,
3. records its stage execution with `record_stage_execution(...)`, and its
   artifacts with `record_artifact(...)`, the same way,
4. lets the gate judge the stage with `evaluate_stage_gate(...)`, which
   returns a `GateResult` and persists it,
5. raises on a failed gate, so the worker marks the job failed instead of
   succeeded,
6. at the end of the run, calls `require_promotable(...)`, which raises
   `GateBlocked` when the run may not be promoted.

Every write is fenced, so a handler that lost its lease cannot write. Every
verdict carries the fingerprint of the contract it was judged under, so a
verdict cannot be promoted under a different contract. The budget is measured
from the stored rows, so an over-budget run is refused.

## The three rules a stage handler must not break

1. **A query family is worker-owned.** `ToolCallProvenance.query_family` must
   name one of the ten families in `product_hunter.research.query_plan`. The
   gate refuses a search that names an unknown family, and a search that names
   no family at all.
2. **A mandatory family needs a real query.** A search whose
   `query_or_operation` is whitespace does not cover a family.
3. **A tool call that declares no `tool_call_id` keeps that.** The store mints
   a row key, but it does not repair the missing provenance, so the gate still
   reports it. A caller that wants the check to pass publishes the id.

## What a stage handler must not do

- It must not write a file that the gate then reads. The gate judges the
  database.
- It must not catch `GateBlocked` and continue. A refused promotion is a
  refusal.
- It must not call `require_promotable()` inside a transaction it cannot
  commit, because the function writes the stage verdicts it produced. The
  function's docstring says so.

## Known limits a handler will hit

- The gate judges the `research_stage_executions` row of a stage, and the
  evidence of that stage's **latest** attempt. A handler that retries a stage
  must write the failed attempt's evidence under its own attempt number, or
  the rebuild will not attribute it.
- Three of the six hard-requirement flags are enforced through the stage gate
  only. The shipped contract sets `promotion.require_all_stages_passed: true`,
  so the shipped baseline is not exposed to it.
- The budget's token and cost dimensions are read from `spend_ledger`, which
  has no writer. A run that spends tokens without recording them under-counts
  against those two dimensions. Recording the spend is the handler's job.
- `saturation_threshold` has no measurement source, so a contract that limits
  it makes `build_budget_tracker()` refuse. The shipped contract does not set
  it.

## What the run did not do

- It did not build a stage handler. The eight Wave 2 sessions own that.
- It did not build the promotion path, the opportunity lifecycle, the
  clustering, the independence analysis, the scoring, or the projections.
- It did not merge, rebase, or touch any `wave2/*` branch.
- It did not push.
- It did not add the operational work of spec §38B: no backup, no restore
  canary, no retention tiers, no parser-drift canary, and no rollback notes
  on the migrations.

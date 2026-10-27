# Spec: close audit B3/B4/B5 and prove one honest E2E run

Status: DEFINE (stage 1)
Contract: `/loop-free`, risk `high`
Baseline: `HEAD 098894d`, mixed dirty tree, 6 red tests
Input: `docs/reasonix/reports/nemotron-independent-audit.md` (verdict NOT READY)

## Problem

The independent audit found five blockers. B1 (extraction bypasses the router) and
B2 (only two admissible source domains) are already closed in the working tree.
Three are open, and the code gate is red, so nothing can be committed or claimed.

## In scope

### 1. Code gate is green

Six tests fail today. Five fail only because they hard-code the old registry of
three sources; `dev_to` was added as a human-reviewed active source and the
collector exists in `handlers.py`. The assertions must be updated to the real
registry without weakening what they prove:

- `tests/test_sources.py::test_real_registry_has_exactly_three_ids` — assert the
  reviewed set, including `dev_to`, and keep the "no unreviewed source" intent.
- `tests/test_fetch.py::test_cli_sources_lists_registry` — same set.
- `tests/test_source_expansion.py::test_a_candidate_is_a_proposal_and_never_a_source`
  — compare against a count read before the proposal, not a literal 3.
- `tests/test_discovery_handler.py::test_discovery_handler_invokes_multiple_sources`
  — expect the active set, including `dev_to`.
- `tests/test_db_store.py` two tests — the fixture imports a run whose registry
  snapshot now has four sources; update the expected count, do not delete the
  assertion.

### 2. B3 — spend is measured for every dimension a stage can incur

Two defects, not one.

**Stage labels do not match the contract.** Every `spend_ledger` and
`model_executions` row is written with an uppercase job stage (`DISCOVERY`,
`PAIN_EXTRACTION`, `EMBEDDING`), while `config/research_contract.yaml` limits
budgets per lowercase contract stage (`discovery`, `normalization`, `clustering`).
Per-stage limits therefore never apply. One mapping table, applied inside
`_write_spend_ledger` and to the model-execution rows, fixes both.

**Dimensions are unmeasured.** `llm_input_tokens`, `llm_output_tokens`,
`embedding_items` are written only by some handlers; `model_calls` is measured
from `model_executions` and has no way to say "this stage made no calls".

Required behaviour: a stage the contract limits on a dimension must record that
dimension, including an explicit zero when the stage incurred none. A stage that
records nothing for a dimension the contract limits it on is a **gate failure**,
not a gap. Dimensions limited only per run and never incurred by any tool
(`browser_minutes`, `crawl_items`, `subq_calls`) stay a reported gap: the pipeline
has no such tool, so no stage can declare them, and failing on them would fail
every honest run.

### 3. B4 — the gate runs and persists in the production lifecycle

`stage_gate_results` has zero rows in production because `require_promotable`
is reachable only from Obsidian sync, which upstream failures never reach.

Required behaviour: after a job completes in `runtime/worker.py`, evaluate the
gate for the contract stage that job maps to and persist the verdict with the
contract fingerprint, stage, status, attempt, thresholds and violations. A
verdict that cannot be computed is recorded as a failure of that job, not
swallowed. Promotion stays fail-closed: `require_promotable` still refuses.

### 4. B5 — the clustering adjudicator is configured

`_clustering_handler` passes `adjudicator=None`, so every ambiguous pair is held
as `ambiguous_unadjudicated` and `cluster_decisions` stays empty.

Required behaviour: build the generator for `ModelRole.clustering` through the
same router/fallback path extraction uses, wrap it in the existing
`LlmAdjudicator`, and pass it to `ClusteringEngine`. Record the adjudication
spend and model executions like any other model call. When no model answers, the
engine keeps holding the pair — a failed call must never merge two clusters.

### 5. Extraction call ceiling is structural, not a budget

`_extraction_handler` calls `extract_documents(..., max_documents=20, max_calls=5)`.
Five calls at three candidates per chunk cannot reach the validation threshold of
20 spans. The ceiling must come from the contract's `model_calls` budget, not a
literal, so the run is bounded by the same number the gate enforces.

### 6. Model routing can fall back to the free pool

`config/model_routing.yaml` chains only paid `opencode-go/*` patterns. When the
paid pool is exhausted every role fails. A free pattern must sit at the end of
every chain, so an exhausted paid pool degrades instead of stopping. The paid
patterns keep their order.

### 7. Swallowed failures

`except Exception: pass` around registry sync, document ingestion, claim
recording, model-execution recording and the legacy worker's stage rows hides
real failures. Each must either propagate or record the error where the gate can
see it. The legacy `db/worker.py` also writes uppercase stage executions that
pollute the gate's stage table; that write is removed, since the production
runtime is `runtime/worker.py`.

## Out of scope

- Labelling `eval/datasets/ds-v1.json` and `ds-v2.json`. Human labels cannot be
  invented; the deferral stays written down.
- Any change to quote validation, source policy, or the promotion rules.
- New sources beyond the reviewed `dev_to` entry.

## Acceptance

1. `uv run pytest` exits 0 with no test skipped, weakened, or deleted.
2. A production run reaches `OBSIDIAN_SYNC` and leaves, in SQLite:
   `stage_gate_results` rows with a contract fingerprint, `model_executions`
   rows with provider and model, `spend_ledger` rows for every dimension a stage
   incurred, `evidence` and `pain_points` above zero, `opportunities` above zero,
   `projection_tasks` rows, and notes under `data/vault`.
3. If the only answering model cannot produce valid extraction output, the run is
   reported NOT READY with the failing evidence. A degraded model is named as
   degraded; it is never described as a passing production run.

## Assumptions

- The user already chose `opencode-go/beam-free` for this run because the paid Go
  pool is exhausted; the run is labelled DEGRADED wherever a model is named.
- The working tree's uncommitted B1/B2 work is kept, not reverted.

# Plan: close audit B3/B4/B5 and prove one honest E2E run

Status: PLAN (stage 3)
Spec: `docs/specs/b3-b5-e2e-completion.md`
Risk: high (gate, budget, and clustering enforcement code)

## Architecture decisions

### One stage map, two consumers

The pipeline runs jobs under uppercase stage names (`DISCOVERY`,
`PAIN_EXTRACTION`); the contract and every `research_stage_executions` row use
lowercase contract names (`discovery`, `normalization`). Today only the execution
rows speak the contract's language, so per-stage budgets and per-stage gates
never see the spend they should judge.

One table in `handlers.py`:

| job stage | contract stage |
|---|---|
| `DISCOVERY` | `discovery` |
| `COLLECTION` | `evidence_collection` |
| `NORMALIZATION`, `DEDUPLICATION`, `ENTITY_RESOLUTION`, `PAIN_EXTRACTION` | `normalization` |
| `EMBEDDING`, `CLUSTERING` | `clustering` |
| `INDEPENDENCE_ANALYSIS`, `VALIDATION` | `validation` |
| `COMPETITOR_RESEARCH` | `investigation` |
| `COUNTER_EVIDENCE` | `counter_evidence` |
| `SCORING` | `scoring` |
| `REPORTING`, `OBSIDIAN_SYNC` | none (no contract stage) |

Consumers: `_write_spend_ledger` (before insert) and the extraction handler's
`ModelExecution` rows. A stage with no contract mapping keeps its job name, so
projection spend stays visible and is not silently attributed to a stage that
did not incur it.

The gate-persistence map is the subset that actually records a contract stage
execution: `discovery`, `evidence_collection`, `normalization`, `clustering`,
`validation`, `investigation`, `counter_evidence`, `scoring`. `EMBEDDING` is
excluded because the clustering verdict is not judgeable until `CLUSTERING`
finishes.

### Unmeasured is a verdict, not a footnote

`BudgetTracker` already keeps `(stage, dimension) -> spent`, and `record` keeps
a zero. Presence is therefore the measurement claim, and absence is the gap.

- `research/budget.py`: add `measured(dimension, stage) -> bool`.
- `research/gate.py`: add `ViolationCode.budget_unmeasured`, blocking; emit one
  violation per `(stage, dimension)` the contract limits per stage, where the
  stage ran and the tracker holds no record.
- The check only applies to stages that produced an execution. A dimension
  limited per run and never incurred by any tool (`browser_minutes`,
  `crawl_items`, `subq_calls`) stays a reported gap: no stage can declare it.
- `db/research_gate.py::_measure_run` keeps `model_calls` sourced from
  `model_executions`. A `spend_ledger` row of unit type `model_calls` with zero
  units is the explicit "this stage made no model call" declaration and is not
  added to the total, so the two sources cannot double-count.

### Gate persistence in the production lifecycle

`runtime/worker.py::run_once`, between the handler returning and `complete_job`:

1. map `job.stage` to a contract stage; skip when there is none;
2. `load_run_evidence` and `evaluate_stage_gate` under the job's fence
   (`worker_id`, `lease_epoch`, `lease_stage=job.stage`);
3. put `{"stage", "passed", "violations"}` in the job result.

A verdict that cannot be computed raises and fails the job. A computed failing
verdict is persisted and the job completes: the evidence is kept, and
`require_promotable` still refuses the run. Nothing is swallowed.

### Adjudicator

`LlmAdjudicator` already exists and is unused. `_clustering_handler` builds the
generator through the same router/fallback path extraction uses, for
`ModelRole.clustering`, wraps it, and passes it in. Adjudication calls are
recorded as model executions and spend like any other call. When no model
answers, `adjudicator` stays `None` and the engine keeps holding the pair; a
failed call never merges two clusters.

## Nodes, in order

### Node 1 — green the six red tests

Files: `tests/test_sources.py`, `tests/test_fetch.py`,
`tests/test_source_expansion.py`, `tests/test_discovery_handler.py`,
`tests/test_db_store.py`.

`dev_to` is a reviewed active source; update the expected sets and counts. For
the proposal test, read the registry length before the proposal and assert it is
unchanged. Do not delete, skip, or loosen any assertion; do not touch
`config/sources.yaml`.

### Node 2 — B3 stage attribution

Files: `src/product_hunter/handlers.py`.

Add the map and apply it in `_write_spend_ledger` and to the extraction
handler's `ModelExecution.stage`. Add a unit test that a `PAIN_EXTRACTION` spend
row lands under `normalization` and a `DISCOVERY` row under `discovery`.

### Node 3 — B3 completeness and the unmeasured blocker

Files: `src/product_hunter/handlers.py`, `src/product_hunter/research/budget.py`,
`src/product_hunter/research/gate.py`, `tests/test_research_gate.py`,
`tests/test_research_budget.py`.

Write explicit rows for every dimension the contract limits per stage:

- extraction: `model_calls` declaration (units 0 is fine; the executions carry
  the count), already writes the LLM dimensions;
- normalization: `llm_input_tokens`, `llm_output_tokens`, `usd_cost`,
  `model_calls` — zeros when it makes no model call;
- validation, scoring, investigation: the LLM dimensions the contract limits,
  zero when deterministic;
- clustering: `embedding_items` (already written).

Then the `measured()` predicate and the `budget_unmeasured` violation. Tests:
a run that records nothing for a limited stage dimension fails the gate; a run
that records an explicit zero passes; a per-run-only dimension never fails it.

### Node 4 — B4 persistence

Files: `src/product_hunter/runtime/worker.py`, `tests/test_runtime_worker.py`.

Implement the insertion above. Test: a completed mapped job leaves a
`stage_gate_results` row carrying the contract fingerprint; a job whose stage has
no contract stage leaves none; a gate that raises fails the job.

### Node 5 — B5 adjudicator

Files: `src/product_hunter/handlers.py`, `tests/test_handlers_wired.py`.

Wire `LlmAdjudicator` for `ModelRole.clustering`, record its model executions
and spend, and keep `None` when no generator answers. Test: the engine receives
a non-`None` adjudicator when the router answers, and `None` when it does not.

### Node 6 — extraction ceiling and free routing

Files: `src/product_hunter/handlers.py`, `config/model_routing.yaml`,
`config/extraction.yaml`.

Replace the `max_calls=5` literal with the contract's `model_calls` budget
(minus calls already spent this run), and keep `max_documents` bounded by the
discovery threshold. Add `opencode-go/*-free*` as the last pattern of every
role chain; paid patterns keep their order.

### Node 7 — swallowed failures

Files: `src/product_hunter/handlers.py`, `src/product_hunter/db/worker.py`.

Registry sync, document ingestion, claim recording, model-execution recording:
each either propagates or records the error on the stage execution the gate
reads. Remove the legacy uppercase `research_stage_executions` write in
`db/worker.py`; the production runtime records them.

### Node 8 — E2E, docs, report

Run one production run through `runtime` with `PH_WORKER_HANDLERS` and the
`product_hunter` MCP. Verify the SQLite counts and `data/vault` from the spec.
Write `docs/reports/b3-b5-e2e-completion.md` and update the acceptance report
honestly. No commit until the code gate passes and review returns ship.

## Verification

- `uv run pytest` after every node; the full gate at stage 5.
- Node 8 counts come from SQLite queries, quoted in the report, not from logs.
- A degraded model is named as degraded in the report and in the run result.

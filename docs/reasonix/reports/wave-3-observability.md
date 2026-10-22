# Wave 3 Session 7: observability and evaluation engine

**Branch:** `wave3/observability-evaluation`
**Base:** `main` @ `00a6ca3`
**Commits:** `6d4eacb` (the engine), `0cd11ea` (two metric defects the live report found)
**Spec:** [../specs/wave-3-observability.md](../specs/wave-3-observability.md)
**Plan:** [../plans/wave-3-observability.md](../plans/wave-3-observability.md)
**Decision:** [../decisions/2026-10-22-observability-engine.md](../decisions/2026-10-22-observability-engine.md)
**Handoff:** [../handoffs/wave-3-observability-handoff.md](../handoffs/wave-3-observability-handoff.md)

## The goal

A 24/7 autonomous research system is useless if nobody can answer six
questions about it: what it did, why it did it, what failed, what it cost,
whether its conclusions improved, and whether a model change regressed
quality.

The repository already held the evidence in forty-five tables. This wave adds
the reader that turns it into one report, the durable record of a model
comparison, and the two evaluation families that were missing.

## What was built

| Piece | Where | Lines |
|---|---|---|
| the twenty metric families | `src/product_hunter/observability/metrics.py` | 2 571 |
| the read-side trace | `src/product_hunter/observability/trace.py` | 257 |
| the markdown rendering | `src/product_hunter/observability/report.py` | 178 |
| the comparison record | `src/product_hunter/observability/comparison.py` | 270 |
| the package surface | `src/product_hunter/observability/__init__.py` | 56 |
| the new table | `db/migrations/0008_observability.sql` | 43 |
| the CLI additions | `src/product_hunter/cli.py` | 163 added |
| the two new evaluation families | `src/product_hunter/eval/` | 165 added |
| the versioned dataset | `eval/datasets/ds-v2.json` | 835 |
| the tests | `tests/test_observability_*.py`, `tests/test_eval_expansion.py` | 1 531 |

Three commands reach it:

```text
ph observe metrics [--run RUN_ID] [--db PATH] [--out PATH]
ph observe trace --run RUN_ID [--stage STAGE] [--db PATH] [--out PATH]
ph bench compare --baseline P --candidate P --out P [--db PATH]
```

`ph observe` reads the store and never creates one. `ph bench compare --db`
creates and migrates the database when it is absent, and writes the
comparison row.

## The measurement contract

A number has three states, not two. `measured` means the number was read from
rows that exist. `not_measured` means there was nothing to measure, and the
metric carries a reason that says what was missing. `not_applicable` means the
metric does not apply to the scope it was asked about.

A count over a table that exists is measured, and zero is a legitimate
count. A rate whose denominator is zero is never zero: it is `not_measured`
with the reason `no denominator`. Nothing is estimated or imputed.

## The metrics, read against a real store

The report was read against the live development database, which is at schema
version 2, and against a copy of it that the wave migrated to version 8.

| Store | Metrics | Measured | Not measured |
|---|---|---|---|
| the live database, schema 2 | 95 | 19 | 76 |
| the same store, schema 8, one comparison recorded | 237 | 109 | 128 |

The 76 unmeasured metrics of the schema-2 store each name what is missing:

| Reason | Count |
|---|---|
| the tool_calls table is not in the schema | 19 |
| the model_comparisons table is not in the schema | 11 |
| no run was selected | 8 |
| the clustering_runs table is not in the schema | 8 |
| no denominator | 8 |
| no independence report exists | 5 |
| the stage_gate_results table is not in the schema | 4 |
| no row of model_executions carries a latency_ms | 3 |
| no row of fetches carries a latency_ms | 3 |
| the research_stage_executions table is not in the schema | 3 |
| no spend row carries a token unit | 2 |
| the discovery record carries no author field | 1 |
| the collection_items table is not in the schema | 1 |

The engine did not raise against the older schema, and it did not pretend the
missing tables were empty. That is the point of the contract.

The twenty groups are `run`, `stage`, `tool_call`, `source_health`,
`discovery_yield`, `evidence_yield`, `duplicate_rate`, `independence_rate`,
`cluster_quality`, `completion`, `failure_retry`, `budget`, `model_usage`,
`provider_usage`, `latency`, `cost_accounting`, `regression`,
`model_comparison`, `evaluation_coverage` and `evaluation_status`.

### The trace

The trace resolves run, stage, agent, tool, purpose, timestamp, result
reference and status from four tables: `tool_calls`, `model_executions`,
`research_stage_executions` and `research_events`. It is a read-side union,
so it needs no writer and no new table.

Two gaps in that chain are real and are named in every report:

- a model execution carries no agent and no result reference
- a stage execution carries no result reference

## The model comparison

`model_comparisons` is a new table because `research_events` demands a
`research_runs` foreign key, and a benchmark comparison is not a research
run. Writing one there would mean inventing a run to hang the event on.

A comparison recorded live during the wave, read back through
`ph observe metrics`:

```json
{
  "comparison_id": "cmp_cc445d3f99315f10",
  "dataset_version": "ds-v2",
  "evaluator_version": "eval-v2",
  "baseline_run_id": "ds-v2-demo-baseline",
  "baseline_provider": "demo-provider",
  "baseline_model": "demo-model-a",
  "baseline_model_version": "2026-10-22",
  "baseline_prompt_version": "demo-prompt-v1",
  "candidate_run_id": "ds-v2-demo-candidate",
  "candidate_model": "demo-model-b",
  "candidate_model_version": "2026-10-22",
  "candidate_prompt_version": "demo-prompt-v2",
  "verdict": "regression",
  "comparable": 1,
  "regressions": 5,
  "improvements": 0,
  "created_at": "2026-10-22T10:45:22.612960+00:00"
}
```

The `tasks_json` column holds one entry per task family, with the prompt
version each run answered it with, how many cases it held, how many were
scored, how many carry a human label, whether it was blocked for want of one,
and what each of its metrics did between the two runs. No model is named in
the code: the provider, the model, the model version and the prompt version
all come from the run that produced the answers.

## The evaluation expansion

The benchmark covered ten families. The mission names ten areas, and two of
them had no family. `entity_resolution` and `scoring_consistency` bring it
to twelve.

| Family | Cases in ds-v2 | Human labels | Quality metrics |
|---|---|---|---|
| pain_extraction | 3 | 0 | NOT YET MEASURED |
| false_positive_rejection | 3 | 0 | NOT YET MEASURED |
| quote_fidelity | 3 | 0, none needed | measured against the source text |
| severity | 3 | 0 | NOT YET MEASURED |
| normalization | 3 | 0 | NOT YET MEASURED |
| duplicate_detection | 3 | 0 | NOT YET MEASURED |
| author_independence | 3 | 0 | NOT YET MEASURED |
| clustering | 3 | 0 | NOT YET MEASURED |
| competitor_identification | 3 | 0 | NOT YET MEASURED |
| counter_evidence_classification | 3 | 0 | NOT YET MEASURED |
| entity_resolution | 3 | 0 | NOT YET MEASURED |
| scoring_consistency | 3 | 0 | NOT YET MEASURED |

Thirty-six cases, three per family, and not one carries a human label. Every
`expected` block in `ds-v2.json` is `null`, so every threshold behind the
eleven families that need a label is reported `blocked` with the reason
`the task has no human label`.

**No precision, recall, accuracy or rate was written anywhere in this wave.**
The only number the benchmark can produce today is
`quote_fidelity.verbatim_rate`, which is checked against the source text.

The seed inputs are real data the repository already captured: the author
accounts come from the `author_independence` cases of `ds-v1`, with the
source id and the account id the store derives, and the three
`scoring_consistency` opportunities are condensations of the bookkeeping text
the capture recorded.

The evaluator version moved from `eval-v1` to `eval-v2`, because the metric
set changed. Two runs scored by different evaluator versions are
`not_comparable`, the same way two different dataset versions are, and the
note says which versions differ. The dataset-version rule is untouched and
both rules fire independently.

## The tests

```text
2209 passed in 40.61s
```

The baseline before the wave was 2138, so the wave added 71 tests. The new
tests cover the nine areas the mission named: metrics aggregation, missing
telemetry, failed runs, retries, model comparisons, evaluation
reproducibility, version changes, cost accounting and incomplete datasets.

Three of them are worth naming because they are the ones that would have
caught a lie:

- `test_a_rate_with_no_denominator_is_never_zero` holds that a rate with
  nothing to divide by reads `not_measured`, not `0`.
- `test_a_database_at_schema_version_seven_does_not_raise` holds that the
  engine degrades against a store that has no `model_comparisons` table.
- `test_two_reads_of_the_same_database_agree` holds that a report is
  reproducible byte for byte.

Two of the tests were added after the live report found two defects, and
both defects were fixed:

- a provider named by both sides of a comparison produced two metrics with the
  same name, each holding half the count
- a family that needs no human label reported every one of its cases as
  labelled, which for quote fidelity was three cases that carry no human
  answer

## The gates

| Stage | Result |
|---|---|
| 5 VERIFY | `2207 passed`, exit 0, schema version 8, CLI wired. Recorded as PASS. |
| 6 HARDEN | `Verdict: findings`, no Must finding. Three Should items, two fixed, one moot. |
| 7 REVIEW | `Verdict: ship`. Two Should items fixed, one optional refactor declined. |

The hardener's third Should item was the trace folding rows with a null run
id into the queried run. The review checked it: all four row queries filter
on `run_id = :run_id`, so the concern was moot.

The review's optional refactor was worth about twenty-five lines across the
twenty families. It was not done. Touching twenty families to save
twenty-five lines risks a contract that is already tested, and the size is
the work rather than the duplication.

## The remaining measurement gaps

| Gap | Why | What would close it |
|---|---|---|
| no human label exists | a label is human work, and no code writes one | labelling `eval/datasets/ds-v2.json` |
| `discovery_yield.independent_author_yield` | `DiscoveryRecord` carries no author field | a writer that records the author count, or the frontier that already tracks it |
| `cost_accounting.input_tokens` and `output_tokens` | no spend row carries a token unit | a model call path that writes a row with `unit_type` of `llm_input_tokens` or `llm_output_tokens` |
| most metrics of the live store | the live database is at schema version 2 | migrating it, and letting a worker write the rows |
| no run has a full trace | no worker handler records tool calls or stage executions yet | the stage handler the architecture calls for |
| no independence report, no clustering run | nothing calls those packages yet | the same stage handler |
| the elapsed time of a run | `research_runs` carries `started_at` and `completed_at`, but no run has both | a run that finishes |

## What was skipped, and what was not checked

- **Not measured:** every quality number behind the eleven evaluation
  families that need a label. No label exists, so no number was written.
- **Not measured:** the token accounting, for the reason above.
- **Not checked:** the new commands were never run against a database the
  real pipeline wrote. The live store used above predates the Wave 2
  migrations, so the tool-call, stage and clustering families were exercised
  against the test suite's own store rather than against production rows.
- **Not checked:** no model was run against `ds-v2.json`. The scorer shapes
  are exercised against hand-built answers, not against a real model's
  output.
- **Skipped:** the twenty-five-line refactor of the metric families.
- **Skipped:** an external observability platform. Spec §34 says the core
  observability is SQLite, and §38 lists Langfuse and OpenTelemetry as
  deliberately excluded until a measured need justifies them.
- **Skipped:** a metrics cache table, a telemetry writer, and a DuckDB or
  Qdrant projection. Two other Wave 3 sessions own the last two.

## The risks a reader must know

**The migration number is a coordination risk.** `0008_observability.sql` is
numbered `0008` because the sequence must stay contiguous and `0007` was the
last file on `main`. Six other Wave 3 branches forked from the same base, and
any of them may also have written a `0008`. The migration loader refuses a
duplicate number and refuses a gap, so an unreconciled merge fails loudly at
`db init` rather than silently. The integrator renumbers, the same way the
Wave 2 integration renumbered four `0003` files to `0004` through `0007`.

**The run was degraded.** The `step-5-preview-free` pool was exhausted during
the wave, so the second implementation node and the review ran on
`longcat-2.5-preview-free` and `nvidia/nvidia/nemotron-3.5-super-vl`. Every
gate still had to pass, and none of them was softened. The code gate, the
harden pass and the review all passed on the models that were left.

**The risk floor is high** because the change touches `db/migrations/**`, and
the stages that a high risk makes non-skippable, ARCHITECT, HARDEN and
REVIEW, all ran and all passed.

## The next step

Label `eval/datasets/ds-v2.json`. That is the one piece of human work that
turns this engine from a measurement of the system into a measurement of the
model, and nothing else in this wave is waiting on anything.

# Handoff — Wave 3 Session 7: observability and evaluation engine

**Date:** 2026-10-22
**Branch:** `wave3/observability-evaluation`
**Base:** `main` @ `00a6ca3`
**Spec:** [../specs/wave-3-observability.md](../specs/wave-3-observability.md)
**Plan:** [../plans/wave-3-observability.md](../plans/wave-3-observability.md)
**Report:** [../reports/wave-3-observability.md](../reports/wave-3-observability.md)
**Decision:** [../decisions/2026-10-22-observability-engine.md](../decisions/2026-10-22-observability-engine.md)

## What this wave added

A read-side observability engine over the tables the repository already had,
one new table for the durable record of a model comparison, and two new
evaluation task families.

Nothing existing was rewritten. The engine computes every number on demand
from the forty-six tables, and it never writes to them.

## The repo map this wave touched

| Path | What it holds now |
|---|---|
| `src/product_hunter/observability/metrics.py` | the twenty metric families, `collect_metrics()` |
| `src/product_hunter/observability/trace.py` | the read-side union of the four trace tables, `collect_trace()` |
| `src/product_hunter/observability/comparison.py` | the writer and the reader of `model_comparisons` |
| `src/product_hunter/observability/report.py` | the Markdown rendering of the metrics and the trace |
| `db/migrations/0008_observability.sql` | the `model_comparisons` table and one index |
| `src/product_hunter/cli.py` | `ph observe metrics`, `ph observe trace`, `--db` on `ph bench compare`, the `__main__` guard |
| `src/product_hunter/eval/cases.py` | `TaskName` with twelve tasks, `EVALUATOR_VERSION = "eval-v2"` |
| `src/product_hunter/eval/tasks.py` | `score_entity_resolution()`, `score_scoring_consistency()` |
| `src/product_hunter/eval/run.py` | the evaluator-version comparability rule, the four new gateable rates |
| `eval/datasets/ds-v2.json` | thirty-six seed cases, every `expected` block null |
| `eval/thresholds.yaml` | a floor for each new family |
| `eval/README.md` | the twelve families, the two datasets, the two versions a number carries |

## The commands

```text
ph observe metrics [--run RUN_ID] [--db PATH] [--out PATH]
ph observe trace --run RUN_ID [--stage STAGE] [--db PATH] [--out PATH]
ph bench compare --baseline P --candidate P --out P [--db PATH]
```

`ph observe` requires the database to exist and never creates one. `ph bench
compare --db` creates and migrates the database when it is absent, and writes
the comparison row.

## What a later session should know

**No human label exists.** Every quality number behind the twelve evaluation
families is `NOT YET MEASURED`. The only number the benchmark can produce
today is `quote_fidelity.verbatim_rate`, which is checked against the source
text and needs no label. Labelling `eval/datasets/ds-v2.json` is the one
piece of human work that turns the benchmark into a quality measurement.

**The discovery payload carries no author.** `DiscoveryRecord` has no author
field, so `discovery_yield.independent_author_yield` reads `not_measured` with
the reason that the record carries no author field. The frontier in
`discovery/frontier.py` tracks independent-author yield per query, so that is
where the number belongs when a writer is added.

**No token spend row exists.** `cost_accounting.input_tokens` and
`output_tokens` read `not_measured` with the reason `no spend row carries a
token unit`. The `spend_ledger` schema already accepts a `unit_type` of
`llm_input_tokens` and `llm_output_tokens`, so the writer is the only thing
missing.

**No worker handler writes telemetry.** The trace reads `tool_calls`,
`model_executions`, `research_stage_executions` and `research_events`. A
stage handler that records its calls does not exist yet, so a run that never
reached a stage has no trace to read.

**The migration number is a coordination risk.** `0008` is forced by
contiguity and six other Wave 3 branches forked from the same base. The
loader refuses a duplicate and refuses a gap, so an unreconciled merge fails
at `db init` rather than silently.

**`metrics.py` is 2566 lines.** The twenty families each carry their own SQL
and their own guard clauses. The review named an optional refactor worth
about twenty-five lines. It was not done, because touching twenty families
risks a contract that is already tested.

## The seam that matters for a merge

The observability package reads the model version, the provider, the model
and the prompt version off the `EvalRun` objects, and it never imports
`EVALUATOR_VERSION`. It reads `run.evaluator_version` instead. That is why
the two implementation nodes could run in parallel, and it is why the package
keeps working when the evaluator version moves again.

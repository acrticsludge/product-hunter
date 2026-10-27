# Wave 3 Session 7: observability and evaluation engine

**Status.** Approved for implementation. 2026-10-22.
**Branch.** `wave3/observability-evaluation`.
**Baseline.** `main` at `00a6ca3`.

## The problem

A 24/7 autonomous research system is useless if nobody can answer six
questions about it:

- what did it do
- why it did it
- what failed
- what it cost
- whether its conclusions improved
- whether a model change regressed quality

The repository already answers the first four questions *in the database*. It
does not answer them in one place, and it does not answer the last two at all
across sessions.

## What already exists, and is reused unchanged

This wave adds a reader, not a second system.

| What | Where | Used for |
|---|---|---|
| 45 tables over 7 migrations | `db/migrations/0001` to `0007` | every metric below reads them |
| `tool_calls` | migration 0003 | the tool part of the trace chain |
| `model_executions` | migration 0002 | model, provider, prompt version, latency, status |
| `spend_ledger` | migration 0001 | budget, units, cost in millionths of a dollar |
| `research_stage_executions`, `stage_attempts` | 0003, 0001 | stage status, attempts, retries |
| `stage_gate_results` | 0003 | stage and run gate verdicts |
| `research_events` | 0001 | the discovery records and their spend |
| `documents`, `evidence`, `pain_points` | 0001 | discovery yield, evidence yield, duplicate rate |
| `clusters`, `cluster_members`, `cluster_decisions` | 0006 | cluster quality |
| `author_clusters`, `author_cluster_members`, `independence_reports` | 0007 | independence rate |
| `collection_source_state`, `collection_items` | 0004 | source health, failures, retries |
| the benchmark package | `src/product_hunter/eval/` | cases, scorers, thresholds, comparison, reports |

No table is rewritten. No existing writer changes. The only schema change is
one new table, for the one thing the repository cannot already hold.

## The measurement contract

### A number has three states, not two

The whole point of this wave is that a missing number is not a zero.

| State | Meaning | How it is written |
|---|---|---|
| `measured` | the number was computed from rows that exist | `value` holds the number |
| `not_measured` | there was nothing to measure | `value` is `null`, `reason` says what was missing |
| `not_applicable` | the metric does not apply to this scope | `value` is `null`, `reason` says why it does not apply |

The report prints `NOT YET MEASURED` for every `not_measured` metric. A reader
must never be able to mistake an empty table for a zero.

### The rules

1. A **count** over a table that exists is `measured`, and `0` is a legitimate
   count. "There were no tool calls" is a measurement.
2. A **rate** whose denominator is zero is `not_measured`, with the reason
   `no denominator`. A rate never reads `0` because nothing happened.
3. A metric whose **source table is not in the schema** is `not_measured`,
   with the reason naming the table. The engine must run against a database at
   an older schema version without raising.
4. A metric scoped to a **run that does not exist** is `not_measured`, with the
   reason naming the run.
5. A metric whose source rows exist but do not carry the **field** the metric
   needs is `not_measured`, with the reason naming the field. A `NULL`
   `duration_ms` on every tool call means latency is not measured, not zero.
6. **No number is invented.** Nothing in this package estimates, imputes, or
   defaults a value it did not read.

### The metric shape

```python
class Metric(BaseModel):
    name: str                      # "tool_call.failure_rate"
    group: str                     # "tool_call"
    value: float | int | None
    unit: str                      # "count" | "rate" | "milliseconds" | "seconds" | "micros" | "tokens" | "text"
    status: str                    # "measured" | "not_measured" | "not_applicable"
    reason: str | None = None      # why it is not measured
    detail: dict[str, Any] = {}    # the counts behind the number
```

`detail` carries the numbers the value was built from, so a reader can check
the arithmetic without running the query again.

## The twenty metric families

Every family is read from the tables named. A family that is empty in a
database is reported as `not_measured` or as a measured zero, never as a
fabricated rate.

| # | Group | Metrics | Source |
|---|---|---|---|
| 1 | `run` | status, started_at, completed_at, duration_seconds, progress_percent, contract_version, model_policy_version, current_stage | `research_runs` |
| 2 | `stage` | per stage: status, attempts, duration_ms, tool_calls, failed_tool_calls, gate_passed, violations | `research_stage_executions`, `stage_gate_results`, `tool_calls` |
| 3 | `tool_call` | total, failed, failure_rate, retried, result_count_total, duration mean/p50/max, per-tool breakdown | `tool_calls` |
| 4 | `source_health` | per source: fetches, failures, success_rate, last_seen_at, latency mean/p50, collection outcomes by reason_code | `fetches`, `collection_items`, `collection_source_state`, `sources` |
| 5 | `discovery_yield` | queries_executed, records, novel_records, duplicate_yield, source_yield, independent_author_yield, per-family breakdown | `research_events` where `event_type = 'DISCOVERY_QUERY_EXECUTED'` |

`DiscoveryRecord` carries no author field, so `independent_author_yield` has no
source in the discovery payload today. The metric reads `not_measured` with
the reason that the record carries no author field, until a writer adds one.
The frontier in `discovery/frontier.py` tracks independent-author yield per
query, so that is where the number belongs when it is written.
| 6 | `evidence_yield` | documents, evidence_spans, pain_points, accepted_pain_points, accepted_rate, evidence_per_document | `documents`, `evidence`, `pain_points` |
| 7 | `duplicate_rate` | duplicate_text_rate, duplicate_key_rate, duplicate_spans | `documents.text_sha256`, `pain_points.normalized_key`, `propagation_edges` |
| 8 | `independence_rate` | authors, independent_authors, independent_author_rate, independent_communities, uncertain_authors, source_concentration | `authors`, `author_clusters`, `author_cluster_members`, `independence_reports` |
| 9 | `cluster_quality` | clustering_runs, clusters, mean_cluster_size, singletons, singleton_rate, merge_decisions, split_decisions | `clustering_runs`, `clusters`, `cluster_members`, `cluster_decisions` |
| 10 | `completion` | runs_total, runs_completed, completion_rate, stages_total, stages_passed, stage_pass_rate, gate_pass_rate | `research_runs`, `stage_gate_results` |
| 11 | `failure_retry` | tool_call_failures, model_execution_failures, job_failures, dead_jobs, retries, lease_lost_attempts, failure_rate | `tool_calls`, `model_executions`, `jobs`, `stage_attempts` |
| 12 | `budget` | entries, units_by_type, cost_micros_total, cost_by_stage, cost_by_provider, cost_by_unit_type | `spend_ledger` |
| 13 | `model_usage` | per model: calls, failures, fallback_calls, latency mean/p50 | `model_executions` |
| 14 | `provider_usage` | per provider: calls, models, failures | `model_executions` |
| 15 | `latency` | tool_call ms mean/p50/max, model_execution ms mean/p50/max, fetch ms mean/p50/max, stage ms mean/max | `tool_calls`, `model_executions`, `fetches`, `research_stage_executions` |
| 16 | `cost_accounting` | cost_micros_total, cost_per_tool_call, cost_per_accepted_pain_point, input_tokens, output_tokens | `spend_ledger` |
| 17 | `regression` | comparisons recorded, last verdict, regressions, improvements, per-metric deltas of the last comparison | `model_comparisons` |
| 18 | `model_comparison` | comparisons by verdict, per provider and per model call counts, datasets compared | `model_comparisons` |
| 19 | `evaluation_coverage` | per task: cases, labelled, blocked, scored | `model_comparisons.tasks_json` |
| 20 | `evaluation_status` | dataset versions seen, evaluator versions seen, blocked task count, last comparison | `model_comparisons` |

Family 16 is the one that must not lie. `input_tokens` and `output_tokens` are
`not_measured` with the reason `no spend row carries a token unit` until some
writer puts a row with `unit_type` of `llm_input_tokens` or
`llm_output_tokens` into `spend_ledger`. The unit type already exists in the
schema, so no migration is needed for it.

### Scope

`--run RUN_ID` scopes the run-scoped families. The tables that carry a
`run_id` are: `research_runs`, `research_events`, `research_stage_executions`,
`research_stage_artifacts`, `research_claims`, `tool_calls`,
`model_executions`, `spend_ledger`, `jobs`, `stage_attempts`,
`collection_run_state`, `collection_items`. The tables that do not carry one
(`documents`, `evidence`, `pain_points`, `fetches`, `sources`, `clusters`,
`author_clusters`) are corpus-scoped and are reported at corpus scope in both
modes, with the run-scoped families carrying the run. A report that mixes the
two must say which is which, so every metric carries a `scope` of `run`,
`corpus` or `database`.

## The trace

Every important research action must be traceable to:

```text
run -> stage -> agent -> tool -> purpose -> timestamp -> result reference -> status
```

The trace is a **read-side union** over the tables that already hold the
chain. It is not a new table and it does not need a writer.

| Kind | Source | agent | tool | purpose | result reference | timestamp | status |
|---|---|---|---|---|---|---|---|
| `tool_call` | `tool_calls` | `agent` | `tool` | `purpose` | `result_ref` | `occurred_at` | `status` |
| `model_execution` | `model_executions` | none | `role` | `role` | none | `created_at` | `status` |
| `stage_execution` | `research_stage_executions` | none | `stage` | `stage` | none | `started_at` | `status` |
| `event` | `research_events` | `agent` | `tool` | `event_type` | `payload_ref` | `created_at` | `recorded` |

Two gaps are real and are reported, not hidden:

- A model execution carries no agent and no result reference. The role is the
  purpose, and the model id is the tool.
- A stage execution carries no result reference.

The trace is ordered by timestamp, then by kind, then by the row's own
identifier, so the order is stable for two reads of the same data.

## The model comparison record

The mission requires a durable record of every comparison, with the model, the
provider, the version, the prompt version, the task, the result, the metrics
and the timestamp.

`research_events` cannot hold it: it requires a `research_runs` foreign key,
and a benchmark comparison is not a research run. Writing one would mean
inventing a run to hang the event on. So the comparison gets its own table,
`model_comparisons`, in migration `0008_observability.sql`.

```sql
CREATE TABLE model_comparisons (
    comparison_id            TEXT PRIMARY KEY NOT NULL,
    dataset_version          TEXT NOT NULL,
    evaluator_version        TEXT NOT NULL,
    baseline_run_id          TEXT NOT NULL,
    baseline_provider        TEXT NOT NULL,
    baseline_model           TEXT NOT NULL,
    baseline_model_version   TEXT,
    baseline_prompt_version  TEXT,
    candidate_run_id         TEXT NOT NULL,
    candidate_provider       TEXT NOT NULL,
    candidate_model          TEXT NOT NULL,
    candidate_model_version  TEXT,
    candidate_prompt_version TEXT,
    verdict                  TEXT NOT NULL,
    comparable               INTEGER NOT NULL DEFAULT 1,
    regressions              INTEGER NOT NULL DEFAULT 0,
    improvements             INTEGER NOT NULL DEFAULT 0,
    tasks_json               TEXT NOT NULL DEFAULT '[]',
    created_at               TEXT NOT NULL
);
```

`tasks_json` holds one entry per task family:

```json
{
  "task": "pain_extraction",
  "baseline_prompt_version": "extract-v3",
  "candidate_prompt_version": "extract-v4",
  "cases": 12, "scored": 0, "labelled": 0, "blocked": true,
  "metrics": [
    {"metric": "precision", "baseline": null, "candidate": null,
     "delta": null, "status": "not_comparable"}
  ]
}
```

No model is named in the code. The provider, the model, the model version and
the prompt version all come from the run that was scored.

**Migration hazard.** The file is numbered `0008` because the sequence must be
contiguous and `0007` is the last one on `main`. Six other Wave 3 branches
exist and any of them may also have written a `0008`. The migration loader
refuses a duplicate number and refuses a gap, so an unreconciled merge fails
loudly at `db init` rather than silently. The integrator renumbers, exactly as
the Wave 2 integration renumbered four `0003` files to `0004` to `0007`.

## The evaluation expansion

### Two new task families

The benchmark covered ten families. The mission names ten areas, and two of
them had no family: `entity_resolution` and `scoring_consistency` were
missing while `severity` and `quote_fidelity` were not in the mission's
list. The two new families bring the benchmark to twelve.

| Mission area | Family | State |
|---|---|---|
| pain extraction | `pain_extraction` | exists |
| genuine-pain classification | `false_positive_rejection` | exists |
| duplicate/repost detection | `duplicate_detection` | exists |
| normalization | `normalization` | exists |
| clustering | `clustering` | exists |
| **entity resolution** | `entity_resolution` | **new** |
| independence | `author_independence` | exists |
| competitor identification | `competitor_identification` | exists |
| counter-evidence classification | `counter_evidence_classification` | exists |
| **scoring consistency** | `scoring_consistency` | **new** |

#### `entity_resolution`

The question the family answers: are these two accounts the same person?

It is not `author_independence`. That family groups *mentions* into authors.
This one groups *author accounts* into identity groups, which is the step
`independence/identity.py` performs.

| | |
|---|---|
| Case input | `accounts[]` with `account_id`, `handle`, `display_name`, `source_id`, and an optional `identity_key` |
| Expected block | `groups[]`, each a list of `account_id` values |
| Model output | `groups[]`, each a list of `account_id` values |
| Metrics | `true_positive`, `false_positive`, `false_negative`, `precision`, `recall`, `f1`, `group_count`, `expected_group_count` |

The scorer reuses `pairwise_metrics`, the same primitive the duplicate,
independence and clustering families use, so the numbers mean the same thing
across the four grouping families. A group that names an account the case does
not hold is a case error, exactly as it is in the other three.

#### `scoring_consistency`

The question the family answers: does the score the system produces match the
score a human thinks the opportunity deserves?

| | |
|---|---|
| Case input | `opportunity` (free text describing the opportunity), `tolerance` (the number of points within which the two scores count as the same) |
| Expected block | `potential` and `confidence`, both numbers from 0 to 100 |
| Model output | `potential` and `confidence`, both numbers from 0 to 100 |
| Metrics | `n`, `potential_exact_rate`, `confidence_exact_rate`, `potential_within_rate`, `confidence_within_rate`, `potential_mean_absolute_error`, `confidence_mean_absolute_error` |

The tolerance lives in the case, not in the code. A family that hard-codes a
tolerance would make the number a property of the scorer instead of a
property of the case.

The two rates that count a match are better when they are higher, so they may
carry a threshold. The two errors are better when they are lower, so they may
not, which is the rule `eval/run.py` already enforces.

### The evaluator version moves

`EVALUATOR_VERSION` goes from `eval-v1` to `eval-v2`. The metric set changed,
so a v1 result and a v2 result are not the same measurement.

`compare_runs` refuses to compare two runs scored by different evaluator
versions, the same way it refuses two different dataset versions. The
verdict is `not_comparable` and the note says which versions differ. This is
the "version changes" rule and it is enforced, not documented.

### The datasets

| File | Version | Holds |
|---|---|---|
| `eval/datasets/ds-v1.json` | `ds-v1` | the thirty seed cases, three per family, unchanged |
| `eval/datasets/ds-v2.json` | `ds-v2` | the thirty seed cases plus three seed cases for each of the two new families, thirty-six in all |

Both files stay. A dataset is a versioned artefact, so the version that was
scored can always be scored again.

**No case in either file carries a human label.** Every `expected` block is
`null`, including the new ones. So:

- every quality metric of every family except `quote_fidelity` is `null`
- every new family is reported as `blocked`
- `ph bench labels` reports the same
- the report says `NOT YET MEASURED`, never a number

**No precision or recall number is written anywhere in this wave.** A seed
case with no label produces no rate. A rate that cannot be computed is `null`,
and the reason is the missing label.

## The command surface

Two new commands and one extended command. Every command prints one JSON
object on stdout, so a script reading the output always reads JSON.

| Command | What it does |
|---|---|
| `ph observe metrics [--run RUN_ID] [--db PATH] [--out PATH]` | the twenty metric families, as JSON on stdout and as a markdown report at `--out` |
| `ph observe trace --run RUN_ID [--stage STAGE] [--db PATH] [--out PATH]` | the trace chain, as JSON on stdout and as a markdown report at `--out` |
| `ph bench compare --baseline P --candidate P --out P [--db PATH]` | unchanged, except that `--db` also records the comparison in `model_comparisons` |

`ph bench compare` keeps its exit code: 1 on a regression, 0 otherwise. Adding
`--db` does not change it.

`--db` is opt-in. Without it the comparison is written to the file and
nothing else, so a command that used to write one file still writes one
file. With it, the database is created and migrated if it does not exist, so
`ph bench compare --db` works on a fresh machine.

## What is deliberately not built

- **No external observability platform.** Spec §34 says the core observability
  is SQLite, and §38 lists Langfuse and OpenTelemetry as `OPTIONAL` and
  deliberately excluded. A dashboard platform is added only after a measured
  need, and there is none.
- **No DuckDB, no Qdrant.** Two other Wave 3 sessions own those.
- **No new telemetry writer.** The trace reads what the workers already write.
  A stage handler that records tool calls does not exist yet, and this wave
  does not write one.
- **No metrics cache table.** Every number is computed from the authoritative
  tables on demand. A cache would be a second source of truth that could go
  stale.
- **No token accounting writer.** The `spend_ledger` schema already carries a
  `unit_type` that can hold `llm_input_tokens`. Writing those rows is the job
  of whoever owns the model call path.

## Acceptance criteria

1. `ph observe metrics` runs against an empty database, a populated database
   and a database at an older schema version, and produces a report in which
   every unmeasured number reads `NOT YET MEASURED` with a reason.
2. No metric reads `0` where it has no denominator.
3. The trace of a run resolves every step of the chain, and the two known gaps
   are named in the report.
4. `ph bench compare --db` writes one `model_comparisons` row that carries the
   model, the provider, the model version, the prompt version, the task, the
   result, the metrics and the timestamp, and the row is read back intact.
5. Two runs scored by different evaluator versions are `not_comparable`.
6. The two new families appear in `ph bench labels` as blocked, with zero
   labelled cases.
7. `ds-v2.json` loads, and its cases score without error against a well-formed
   output.
8. Every new number is reproducible: two reads of the same database produce the
   same report, byte for byte.
9. The whole suite stays green, and the new code has tests for metrics
   aggregation, missing telemetry, failed runs, retries, model comparison,
   evaluation reproducibility, version changes, cost accounting and incomplete
   datasets.

# Wave 3 Session 7: observability and evaluation engine — plan

**Spec.** [../specs/wave-3-observability.md](../specs/wave-3-observability.md)
**Branch.** `wave3/observability-evaluation`
**Baseline.** `main` at `00a6ca3`, 2138 tests green.

## Stage 2 — architecture

### The shape

```text
                    SQLite (the only source of truth)
                    45 tables over 7 migrations
                              |
        +---------------------+---------------------+
        |                                           |
   read side (new)                            write side (new, one table)
        |                                           |
  observability/trace.py                     model_comparisons
  observability/metrics.py                   (migration 0008)
  observability/comparison.py                        ^
        |                                           |
  observability/report.py                   ph bench compare --db
        |
     ph observe metrics / ph observe trace
```

The read side never writes. The write side writes exactly one table, and only
the comparison recorder writes it.

### The module boundaries

| Module | Owns | Never does |
|---|---|---|
| `observability/metrics.py` | the metric families and the three states of a number | write to the database, read the clock, format text |
| `observability/trace.py` | the union of the four trace sources | write to the database, format text |
| `observability/comparison.py` | writing and reading `model_comparisons` | score anything, decide a verdict |
| `observability/report.py` | the markdown rendering and the JSON writing | read the database, decide a number |
| `eval/` (extended) | the cases, the scorers, the thresholds, the comparison | read the database |

The dependency runs one way. `report.py` imports from `metrics.py` and
`trace.py`. `comparison.py` imports from `eval.run`. Nothing in `observability`
imports from `cli.py`. `cli.py` imports from both.

### Two rules that shape the code

**The engine is a reader, not a writer of truth.** Every number is computed
from the authoritative tables on demand. There is no metrics cache table, so
there is no second source of truth that can go stale.

**A missing number is a value, not an absence.** `Metric.value` is `null` and
`Metric.reason` says why, rather than the metric being absent from the
report. A reader can therefore tell "zero" from "unknown" without guessing.

### The one schema change

`model_comparisons`, in `db/migrations/0008_observability.sql`. Additive, one
table, one index. The migration loader refuses a duplicate number and a gap,
so the file is numbered `0008` and the integrator renumbers it if another Wave
3 branch took the number.

## Stage 3 — the plan

### The split

Two implementation nodes, on files that do not overlap. Node A builds the
observability package and the command surface. Node B expands the benchmark.
They run in parallel.

| Node | Files |
|---|---|
| **A** | `src/product_hunter/observability/` (5 files), `db/migrations/0008_observability.sql`, `src/product_hunter/cli.py`, `tests/test_observability_*.py` (4 files) |
| **B** | `src/product_hunter/eval/cases.py`, `eval/tasks.py`, `eval/run.py`, `eval/__init__.py`, `eval/datasets/ds-v2.json`, `eval/thresholds.yaml`, `eval/README.md`, `tests/test_eval_expansion.py` |

### The one seam between them

Node A reads `EvalRun` and `ComparisonReport` from `eval/run.py`. Node B
changes `eval/run.py`. To keep the nodes independent:

- Node A's recorder takes the two `EvalRun` objects and the
  `ComparisonReport`. It reads the model version and the prompt version off
  the `EvalRun`, never off a field Node B adds.
- Node A never imports `EVALUATOR_VERSION`. It reads
  `run.evaluator_version`, which is already on the object.
- Node A's `ph bench compare --db` therefore works against the `eval` package
  as it stands today, and keeps working after Node B lands.

If Node B adds a field to `ComparisonReport`, Node A does not notice and does
not need to.

### The order of work inside a node

1. The migration file and the store function.
2. The metric families, one at a time, each with its test.
3. The trace.
4. The report rendering.
5. The command surface.
6. The whole suite.

### The test plan

Every item below is a test, not a hope.

| Area | The test |
|---|---|
| metrics aggregation | a populated database produces the twenty families with the expected values |
| missing telemetry | an empty database, and a database at schema version 7, produce `NOT YET MEASURED` with a reason and never raise |
| failed runs | a run with a failed tool call and a failed model execution counts both |
| retries | a tool call with `attempt > 1` counts as a retry, and a job with `attempts > 1` counts as a retry |
| model comparisons | the row is written and read back with every field intact |
| evaluation reproducibility | two runs over the same dataset and the same outputs produce byte-identical JSON |
| version changes | two runs with different evaluator versions are `not_comparable` |
| cost accounting | the cost in millionths of a dollar is summed per stage, per provider and per unit type; the token metrics read `not_measured` while no token row exists |
| incomplete datasets | a dataset with cases but no labels blocks every family that needs a label |

### The gates

| Stage | What proves it |
|---|---|
| 5 | `uv run pytest` green, with the new tests among them |
| 6 | the security and observability pass finds no unauthenticated write, no unbounded read, no secret in the output |
| 7 | the adversarial review reads the diff against the spec and the tests |
| 8 | `docs/reasonix/ARCHITECTURE.md` names the new table and the new package |
| 9 | the report names the commit, the tests, the metrics, the coverage and the gaps |

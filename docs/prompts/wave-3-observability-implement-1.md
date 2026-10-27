# Implement node A: the observability package, the migration and the commands

**You are implementing one node of a graph.** The spec is
[../../specs/wave-3-observability.md](../../specs/wave-3-observability.md) and
the plan is [../../plans/wave-3-observability.md](../../plans/wave-3-observability.md).
Read both before you write a line. The spec is the contract; this prompt is
the work list.

**Working directory.** The worktree you are already in. The branch is
`wave3/observability-evaluation`. Do not touch `main`. Do not merge anything.

**A second agent is working in parallel** on the `src/product_hunter/eval/`
package and the `eval/` directory. Do not edit those files. You may read them.

## The files you own

Create:

- `src/product_hunter/observability/__init__.py`
- `src/product_hunter/observability/metrics.py`
- `src/product_hunter/observability/trace.py`
- `src/product_hunter/observability/report.py`
- `src/product_hunter/observability/comparison.py`
- `db/migrations/0008_observability.sql`
- `tests/test_observability_metrics.py`
- `tests/test_observability_trace.py`
- `tests/test_observability_report.py`
- `tests/test_observability_comparison.py`

Modify:

- `src/product_hunter/cli.py`

Do not edit any other file. If you believe another file must change, stop and
say so in your final report instead of editing it.

## The API you must provide

The names and signatures below are the contract. The other node and the
command surface depend on them.

### `observability/metrics.py`

```python
OBSERVABILITY_VERSION = "observability-v1"

class MetricStatus(str, Enum):
    measured = "measured"
    not_measured = "not_measured"
    not_applicable = "not_applicable"

class Metric(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    group: str
    scope: str                    # "run" | "corpus" | "database"
    value: float | int | None
    unit: str
    status: MetricStatus
    reason: str | None = None
    detail: dict[str, Any] = Field(default_factory=dict)

class MetricReport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    observability_version: str = OBSERVABILITY_VERSION
    run_id: str | None = None
    schema_version: int
    groups: list[str]
    metrics: list[Metric]
    notes: list[str] = Field(default_factory=list)

def collect_metrics(conn: sqlite3.Connection, *, run_id: str | None = None) -> MetricReport
```

`collect_metrics` is the only entry point. It reads, it never writes, and it
never raises because a table is missing or empty.

### `observability/trace.py`

```python
class TraceEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run_id: str
    kind: str                     # "tool_call" | "model_execution" | "stage_execution" | "event"
    stage: str | None
    agent: str | None
    tool: str
    purpose: str | None
    occurred_at: str
    result_ref: str | None
    status: str
    duration_ms: int | None
    attempt: int | None
    detail: dict[str, Any] = Field(default_factory=dict)

class TraceReport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    observability_version: str = OBSERVABILITY_VERSION
    run_id: str
    entries: list[TraceEntry]
    counts: dict[str, int]
    gaps: list[str]

def collect_trace(conn, *, run_id: str, stage: str | None = None) -> TraceReport
```

### `observability/comparison.py`

```python
class ComparisonRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")
    comparison_id: str
    dataset_version: str
    evaluator_version: str
    baseline_run_id: str
    baseline_provider: str
    baseline_model: str
    baseline_model_version: str | None
    baseline_prompt_version: str | None
    candidate_run_id: str
    candidate_provider: str
    candidate_model: str
    candidate_model_version: str | None
    candidate_prompt_version: str | None
    verdict: str
    comparable: bool
    regressions: int
    improvements: int
    tasks: list[dict[str, Any]]
    created_at: str

def record_comparison(conn, baseline: EvalRun, candidate: EvalRun,
                      comparison: ComparisonReport, *,
                      now: datetime | None = None) -> str
def list_comparisons(conn) -> list[ComparisonRecord]
```

`record_comparison` takes the two `EvalRun` objects. It reads the model
version, the provider, the model and the prompt version off those objects. It
never imports `EVALUATOR_VERSION`; it reads `run.evaluator_version`.

`comparison_id` is `cmp_` plus sixteen hex characters of the SHA-256 of
`baseline_run_id|candidate_run_id|created_at`. Writing the same comparison
twice upserts on the id, like `db/model_routing.py` does for an execution.

### `observability/report.py`

```python
def render_metrics_markdown(report: MetricReport) -> str
def write_metrics_report(report: MetricReport, path: Path) -> None
def render_trace_markdown(report: TraceReport) -> str
def write_trace_report(report: TraceReport, path: Path) -> None
```

A metric that is not `measured` prints as `NOT YET MEASURED`, followed by its
reason. A metric that is measured prints its value. Never print `0` for a
metric that has no denominator.

### The commands

Add a `ph observe` group to `src/product_hunter/cli.py`:

| Command | Behaviour |
|---|---|
| `ph observe metrics [--run RUN_ID] [--db PATH] [--out PATH]` | JSON on stdout, markdown at `--out` |
| `ph observe trace --run RUN_ID [--stage STAGE] [--db PATH] [--out PATH]` | JSON on stdout, markdown at `--out` |

`ph observe trace` requires `--run`. `ph observe metrics` takes an optional
`--run`.

Both require the database to exist. Use the existing `_db_path` and
`_require_db` helpers. Do not create a database from `ph observe`.

Extend `ph bench compare` with an optional `--db PATH`. When it is given, the
comparison is also written to `model_comparisons` through
`observability.comparison.record_comparison`. When it is absent, the command
behaves exactly as it does today. The exit code does not change: 1 on a
regression, 0 otherwise.

Every command prints one JSON object on stdout. A failure prints
`{"ok": false, "error": "..."}` and returns 1, which is the house pattern.

## The rules that matter

**A missing number is a value, not an absence.** `value` is `null` and
`reason` says why. The six rules in the spec are the contract:

1. A count over a table that exists is `measured`, and `0` is legitimate.
2. A rate with a zero denominator is `not_measured`, reason `no denominator`.
3. A table that is not in the schema is `not_measured`, reason naming the
   table.
4. A run that does not exist is `not_measured`, reason naming the run.
5. A field every row leaves null is `not_measured`, reason naming the field.
6. Nothing is invented, estimated or imputed.

**The engine must run against a database at an older schema version.** The
newest table is `model_comparisons` in migration `0008`. Against a database
at version 7 that table does not exist, and the three comparison families
read `not_measured` with a reason that names the table. The engine does not
raise.

**Reproducible.** `MetricReport` holds no timestamp and no random value, so
two reads of the same database produce the same JSON, byte for byte. The
trace report is the same: it holds no timestamp either.

**No clock, no randomness** in `metrics.py` or `trace.py`. The only clock in
the package is the `now` argument of `record_comparison`, and it defaults to
`product_hunter.fetch.utcnow()`.

**Never write from the read side.** `collect_metrics` and `collect_trace`
take a connection and issue `SELECT` statements only.

**The SQL is parameterised.** A run id, a stage name and a source id all come
from outside, so every one of them goes in as a bound parameter. Never
f-string a value into a statement.

**The report is a reader's report.** It names what it measured, what it could
not measure, and why. A report that shows a number without saying where it
came from is not finished.

## The twenty metric families

The spec lists all twenty with their source tables. Build them in that order.
The group names are exactly the ones in the spec's table, lower-cased with
underscores: `run`, `stage`, `tool_call`, `source_health`,
`discovery_yield`, `evidence_yield`, `duplicate_rate`, `independence_rate`,
`cluster_quality`, `completion`, `failure_retry`, `budget`, `model_usage`,
`provider_usage`, `latency`, `cost_accounting`, `regression`,
`model_comparison`, `evaluation_coverage`, `evaluation_status`.

`MetricReport.groups` holds those twenty names in that order, whether or not
the database has the rows to fill them.

`Metric.name` is `group.sub_name`, for example `tool_call.failure_rate` and
`stage.PAIN_EXTRACTION.status`.

`Metric.scope` is `run` for the run-scoped families, `corpus` for the
families read from tables without a `run_id`, and `database` for the ones
that describe the whole store.

### The two that are easy to get wrong

`cost_accounting.input_tokens` and `cost_accounting.output_tokens` sum the
`spend_ledger` rows whose `unit_type` is `llm_input_tokens` or
`llm_output_tokens`. While no such row exists, both read `not_measured` with
the reason `no spend row carries a token unit`. When the rows exist, they
read the sum.

`failure_retry.retries` counts two different things, so it is two metrics:
`failure_retry.tool_call_retries` counts the `tool_calls` rows whose `attempt`
is above one, and `failure_retry.job_retries` counts the `jobs` rows whose
`attempts` is above one.

### The discovery yield

The discovery records are `research_events` rows whose `event_type` is
`DISCOVERY_QUERY_EXECUTED`, written by `discovery/store.py`. Their
`payload_json` holds a `DiscoveryRecord`. Read the payload as JSON and take
the yield fields it carries: the novel count, the duplicate count, the source
count and the independent-author count. A payload that does not parse is
counted as an unreadable record and named in the metric's `detail`, not
raised.

Read `src/product_hunter/discovery/records.py` for the field names.

## The trace

The spec's table gives the four sources and the field each one fills. Two
things are named as gaps and must appear in `TraceReport.gaps`:

- a model execution carries no agent and no result reference
- a stage execution carries no result reference

Order the entries by `occurred_at`, then by `kind`, then by the row's own
identifier, so two reads of the same data give the same order.

`research_events` rows are events. Their `status` is `recorded`. Their
`purpose` is the `event_type`. Their `result_ref` is the `payload_ref`.

## The tests

Write them. They are not optional.

| File | What it proves |
|---|---|
| `tests/test_observability_metrics.py` | a populated database produces the expected values; an empty database produces `not_measured` with a reason; a database at schema version 7 does not raise; a rate with no denominator is never `0`; a failed run counts its failures; a retry is counted |
| `tests/test_observability_trace.py` | the four kinds are read; the chain fields are filled; the two gaps are named; the order is stable; a stage filter works |
| `tests/test_observability_report.py` | an unmeasured metric prints `NOT YET MEASURED` and its reason; a measured metric prints its value; the JSON is byte-identical across two reads |
| `tests/test_observability_comparison.py` | the row is written and read back with every field intact; the same comparison upserts; a comparison against a database without the table is refused, not silently dropped |

Build a populated database with plain `sqlite3` inserts in the test. Do not
depend on the pipeline to make one.

`tests/conftest.py` has helpers you may reuse (`open_store`, `evidence_store`).
Look at how `tests/test_db_store.py` builds a store before you write yours.

## The house style

Read `src/product_hunter/eval/report.py` and `src/product_hunter/db/model_routing.py`
before you write. The house style is: a module docstring that says what the
module owns and what it never does, type hints everywhere, `from __future__
import annotations`, pydantic models with `ConfigDict(extra="forbid")`, plain
functions over classes where a class is not needed, and a comment only where
the code cannot say why.

The comments and the docstrings are prose. They must not read like a machine
wrote them: no "In today's fast-paced world", no "tapestry", no "delve", no
"not just X, but Y", no em-dash-heavy sentences, no rule-of-three lists, no
uniform sentence lengths.

## Before you report

1. `uv run pytest` is green, all of it, including the 2138 tests that were
   already there.
2. `uv run python -m compileall src/product_hunter` is clean, or `uv run ruff
   check src/product_hunter` if ruff is available.
3. `git status --short` shows only your files.
4. You changed no test that was already there, except where you added a case
   to it. If you believe an existing test is wrong, say so in your report
   instead of editing it.

Report what you built, the test results, the metrics you could and could not
measure, and anything you left out and why.

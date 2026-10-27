# Phase 2 Evaluation Infrastructure — Specification

**Status:** approved for implementation.
**Date:** 2026-10-21
**Source of truth:** `docs/reasonix/specs/Product-Hunter-Full-Spec.md` §6B (LLM output validation and evaluation) and §38A.
**Phase under spec:** the evaluation infrastructure, ahead of the Phase 3 semantics work it will score.
**Lane:** backend. No UI, so no `DESIGN.md` is required.

---

## 1. What this wave delivers

The repository already has a manual evaluation harness. `ph review-sheet`
writes a CSV of one run, a human fills the verdict columns, and `ph eval`
turns that sheet into quote fidelity, precision, recall, severity agreement,
and normalization agreement.

That harness scores one model against one sheet. It cannot compare two
models, it holds no version of the dataset it scored, it has no threshold, and
it cannot score the seven task families that Phase 3 and Phase 4 will
introduce.

This wave adds the missing layer: a versioned case dataset, a metric per task
family, deterministic scoring, a run record that names the model under test,
a threshold check, a comparison between two runs, and regression detection.

The existing harness keeps working. Nothing in `src/product_hunter/evaluation.py`
changes.

## 2. The rule that shapes everything

**A quality number exists only where a human label exists.**

The A1 acceptance gate of Phase 1 is blocked until a person labels the data.
The same rule applies here. A task whose cases carry no `expected` block
produces `null` for every quality metric and reports itself as blocked. The
system never derives a precision, a recall, a severity agreement, or a
similarity rate from the model's own output and presents it as quality.

One task is exempt by construction, not by convention: `quote_fidelity` is
checked against the source text, so it needs no label and is always
computable. Every other task needs `expected` on the case.

No label is ever generated. The shipped dataset carries real inputs and empty
`expected` blocks. Filling them is a human job, and the label status report
says so in numbers.

## 3. Modules

A new package `src/product_hunter/eval/` sits beside the existing
`src/product_hunter/evaluation.py`. Four modules, one job each.

| Module | Job |
|---|---|
| `cases.py` | the case, the dataset, the label status, and the on-disk format |
| `tasks.py` | the ten task families, their metrics, and the scoring contract |
| `run.py` | the model identity, the run record, thresholds, comparison, regression |
| `report.py` | the benchmark report and the label status rendering |

`__init__.py` re-exports the public names so callers import from
`product_hunter.eval` and not from the private modules.

### What is reused from Phase 1

| Need | Reused from |
|---|---|
| locating a model quote in source text | `product_hunter.quote.locate_quote` |
| folding curly quotes and whitespace for comparison | `product_hunter.quote.canonicalize` |
| building a normalized key from the five controlled fields | `product_hunter.normalization.normalize_fields` |
| the controlled domain list | `product_hunter.normalization.Domain` |
| repository root discovery and relative path resolution | `product_hunter.config.repo_root` |

No new dependency. `pydantic` and `PyYAML` are already installed.

## 4. Cases and the dataset

### 4.1 The case

```python
class EvalCase(BaseModel):
    case_id: str
    task: TaskName
    case_version: int = 1
    input: dict[str, Any]
    expected: dict[str, Any] | None = None
```

`extra="forbid"` on every model in this package, as everywhere else in the
repository. A typo in a dataset field is an error, not a silently ignored key.

`expected` is the human label. `None` means unlabeled, and the case still
runs: the scorer records the attempt and reports the metric as blocked.

`case_id` is unique inside a dataset and stable across dataset versions. A
case keeps its id when its input changes, so a comparison across dataset
versions can still say which case moved.

### 4.2 The dataset

```python
class EvalDataset(BaseModel):
    dataset_version: str
    created_at: str | None = None
    description: str = ""
    cases: list[EvalCase]
```

`dataset_version` is a free-form string chosen by the author, following the
`norm-v1` convention already used in the repository. A dataset with a
different version is a different dataset, and a run against a dataset records
the version it used.

The on-disk form is one JSON document:

```json
{
  "dataset_version": "ds-v1",
  "created_at": "2026-10-21",
  "description": "Seed cases. No human labels yet.",
  "cases": [
    {
      "case_id": "quote-hn-0001",
      "task": "quote_fidelity",
      "case_version": 1,
      "input": {"text": "..."},
      "expected": null
    }
  ]
}
```

JSON only for the dataset. YAML is used for the threshold file because that
is configuration and the repository already reads configuration from YAML.

`load_dataset(path)` reads and validates the file. A missing file, a bad
JSON document, a duplicate `case_id`, an unknown task, or a case whose
`input` is not an object raises `EvalError` naming the file and the problem.
The loader never guesses.

### 4.3 Label status

```python
class TaskLabelStatus(BaseModel):
    task: TaskName
    cases: int
    labeled: int
    unlabeled: int
    blocked: bool

class LabelStatus(BaseModel):
    dataset_version: str
    total_cases: int
    total_labeled: int
    tasks: list[TaskLabelStatus]
```

A task is `blocked` when it has no labeled case and the family needs a label.
`quote_fidelity` does not need a label, so it is never blocked, and the run
result and the label status agree about it. The status is reported for every
task in the dataset, including the ones with no cases at all, so a reader can
see which families have no data yet.

## 5. The ten task families

Every task shares one shape: a case carries an input, the model produces an
output, and a scorer compares the two. The scorer is a pure function of
`(case, output)`. No clock, no randomness, no network, no filesystem beyond
the two files the caller hands in. The same inputs always produce the same
output bytes.

The output shapes below are the contract between the runner and the scorer.
A runner that cannot produce them has not produced a valid output, and the
scorer records a case error rather than a number.

### 5.1 Shared metric primitives

Four primitives cover all ten families.

**Classification.** `predicted: list[str]` against `expected: list[str]` over
a fixed label set. Returns `n`, `accuracy`, macro `precision`, `recall`, `f1`,
a per-label breakdown, and a confusion matrix keyed by expected label then
predicted label.

The macro averages run over the labels the comparison touched, which is the
expected set together with the predicted set. A label nobody used is left out
entirely. A label the comparison touched and the model got wrong scores
`0.0`, not an empty value: the `zero_division` convention. A macro average
that dropped the wrong labels would report a perfect score for a model that
got the case wrong, and a threshold on that score would pass a bad model.

**Detection.** Two sets of identifiers. Returns `true_positive`,
`false_positive`, `false_negative`, `precision`, `recall`, and `f1`.

The rule is the same as the classification one: a value is empty only when
there was nothing on either side to compare, and a zero denominator otherwise
gives `0.0`. So a model that found nothing against a label that found
something gives `precision` `0.0`, `recall` `0.0` and `f1` `0.0` — it
matched none of what was there. A comparison where neither side had anything
gives three empty values, because there was nothing to compare.

This matters for the gate. A threshold on the `precision` of a detection
family must be able to fail a model that found nothing; a `precision` that
was left empty would make the check blocked, and the one model behaviour
where precision is undefined would be the one behaviour the gate skipped.

**Pairwise grouping.** Two lists of groups of item identifiers. Every
unordered pair of items inside a group counts as a positive pair. Returns the
pair counts, `precision`, `recall`, and `f1` over the pairs.

A group of `n` items holds `n(n-1)/2` pairs, so a case that groups four
thousand items in one group holds eight million pairs. A case whose groups
hold more pairs than that is refused with an error, the way an oversized file
is refused, rather than being scored after building the set.

The `f1` of a grouping family follows the same rule as the detection `f1`:
`0.0` when the comparison had something on either side and the model matched
none of it, and empty only when there was nothing on either side.

**Agreement.** Two lists of integers. Returns `n`, `exact`, `exact_rate`,
`within_one`, `within_one_rate`, and `mean_absolute_error`.

### 5.1.1 How a task value is built from its case values

A task value is the aggregate of the case values, and the aggregate depends
on what the metric is.

| Metric | Task value |
|---|---|
| `precision`, `recall`, `f1` of a detection or grouping family | pooled from the summed counts: `Σtp / (Σtp + Σfp)` and the rest |
| `accuracy`, `exact_rate`, `within_one_rate` | pooled from the summed `n` and the summed right answers |
| `mean_absolute_error` | pooled by `n` |
| any other rate | the mean of the case values |
| any count | the sum of the case values |

A rate is a rate and a count is a count, and the two are told apart by what
the case value is, not by the name of the metric. Every family writes a rate
as a number with a fraction and a count as a whole number, so the task value
follows from the value itself. A list of metric names to keep in step with
the families is a list that will drift, and a name that drifts turns a rate
into a sum.

Pooled is the reading a reader expects from a metric called `precision`. The
mean of the case values is the right answer for a metric that has no count
behind it, such as `verbatim_rate`, where every case is one quote.

The classification families are the exception the primitive decides: their
`precision`, `recall`, and `f1` are macro averages over the labels inside one
case, so their task value is the mean of the case values. A task value
therefore means what its family means, and the report says which rule each
metric follows.

### 5.2 The families

| Task | Input | Model output | Human label (`expected`) | Metrics |
|---|---|---|---|---|
| `pain_extraction` | `text`, `doc_id` | `candidates[]` with `quote`, `validity` | `pain_points[]` with `quote` | detection over the genuine quotes |
| `false_positive_rejection` | `text`, `span` | `is_pain`, `reason` | `is_pain` | classification, plus `false_accept_rate` and `false_reject_rate` |
| `quote_fidelity` | `text` | `quote` | none needed | `verbatim_rate`, `exact_rate`, `canonical_rate`, `failures` |
| `severity` | `text`, `quote` | `severity` 0..5 | `severity` 0..5 | agreement |
| `normalization` | `text`, `quote` | `domain`, `actor`, `action`, `object`, `consequence` | the same five fields | `domain_accuracy`, `key_agreement`, per-field agreement |
| `duplicate_detection` | `documents[]` with `doc_id`, `text_sha256` | `groups[]` | `groups[]` | pairwise grouping, plus group count |
| `author_independence` | `mentions[]`, `propagation_edges[]` | `groups[]` | `groups[]` | pairwise grouping, plus independent author count |
| `clustering` | `items[]` with `item_id`, `text` | `groups[]` | `groups[]` | pairwise grouping, plus group count |
| `competitor_identification` | `product`, `candidates[]` | `competitors[]` | `competitors[]`, `not_competitors[]` | detection over the judged candidates |
| `counter_evidence_classification` | `claim`, `items[]` | `labels[]` | `labels[]` | classification with a confusion matrix |

Notes that matter:

**`pain_extraction` matching is by quote text.** A predicted candidate matches
a labelled pain point when the two quotes fold to the same canonical string.
Only `genuine` candidates are counted as predictions. A quote that appears
twice in the text is the same pain point for this purpose.

**`quote_fidelity` is checked against the source text.** The scorer calls
`quote.locate_quote(text, quote)`. An exact span is `exact`, a curly-quote or
whitespace variant is `canonical`, and anything else is a failure. A
paraphrase never passes, which is the point of the check.

**`normalization` compares keys built by production code.** The predicted key
comes from `normalization.normalize_fields` applied to the model's five
fields, so the benchmark measures the same key rule the pipeline uses. The
labelled key comes from the same function applied to the human's five fields.
A model that names a domain outside the fifteen controlled values is a case
error, not a wrong answer, and it is recorded as one.

**`author_independence` groups mentions by underlying author.** The model
returns the groups; the independent author count is computed from the groups
by the code. The number of independent people is a computed property, never a
number the model supplies. §12 of the system spec says so directly, and the
scorer follows it.

**`competitor_identification` separates judged from unjudged.** The human
label names both the competitors and the candidates that are not competitors.
A prediction that names a candidate the human never judged is counted as
`unjudged_predictions` and kept out of precision. It is not silently a false
positive.

**`counter_evidence_classification` labels are `supports`, `refutes`, and
`neutral`.** The label order in the output is the order of the items in the
input. A label outside the three is a case error.

### 5.3 The case result

```python
class CaseScore(BaseModel):
    case_id: str
    task: TaskName
    passed: bool | None
    metrics: dict[str, float | int | None]
    detail: dict[str, Any]
    error: str | None = None
```

`passed` is `None` when the case has nothing to be checked against, which
happens when the case carries no label. `error` holds the reason a case
could not be scored. A case with an error never counts as a pass.

## 6. The run record

### 6.1 Model identity

Every model evaluation records where it came from.

| Field | Required | Meaning |
|---|---|---|
| `provider` | yes | the provider identifier, for example `opencode-go` |
| `model` | yes | the model identifier |
| `model_version` | no | the model version, where the provider publishes one |
| `model_catalog` | no | the catalog or reference the model was selected from |
| `prompt_version` | yes, per task | the version of the prompt that produced the output |
| `evaluator_version` | yes | the version of the scoring code |
| `dataset_version` | yes | the version of the dataset that was scored |

`model_version` and `model_catalog` are nullable on purpose. The OpenCode
route in `src/product_hunter/llm/opencode.py` reports a model id, a provider
id, and the server version, and no model version. A field that cannot be
filled is left empty rather than filled with a guess.

`evaluator_version` is a constant in the code, `eval-v1`. The caller cannot
override it, because the code is the evaluator. Bumping the constant is how a
scoring change is announced.

`prompt_version` is per task, because one model answers ten task families
with ten different prompts. The run spec carries a default `prompt_version`
and an optional `prompt_versions` map keyed by task. The resolved version for
a task is the map entry when present and the default otherwise. A task with
no resolved prompt version is reported as `null` and named in the run's
warnings, so a comparison of two runs never hides a prompt change.

### 6.2 The run spec

The input to a run. It is a JSON file.

```json
{
  "run_id": "ds-v1-deepseek-2026-10-21",
  "provider": "opencode-go",
  "model": "deepseek-v4.1-flash",
  "model_version": null,
  "model_catalog": null,
  "prompt_version": "extract-v3",
  "prompt_versions": {"clustering": "cluster-v1"},
  "dataset_version": "ds-v1",
  "outputs": "outputs.jsonl"
}
```

`dataset_version` is optional in the spec. When present it must equal the
dataset's version, and a mismatch is an error. The run always records the
dataset's version either way.

`outputs` points at the model's answers, one JSON object per line:

```json
{"case_id": "quote-hn-0001", "output": {"quote": "..."}}
```

An output line may carry its own `prompt_version` to override the run
default. An output for a case the dataset does not hold is an error. A case
with no output is recorded as an error for that case, not as a silent skip.

### 6.3 The run result

```python
class TaskResult(BaseModel):
    task: TaskName
    prompt_version: str | None
    cases: int
    scored: int
    unlabeled: int
    errored: int
    metrics: dict[str, float | int | None]
    blocked: bool
    blocked_reason: str | None
    case_results: list[CaseScore]

class EvalRun(BaseModel):
    run_id: str
    evaluator_version: str
    dataset_version: str
    provider: str
    model: str
    model_version: str | None
    model_catalog: str | None
    prompt_version: str | None
    prompt_versions: dict[str, str]
    tasks: list[TaskResult]
    label_status: LabelStatus
    thresholds: ThresholdReport
    passed: bool | None
    warnings: list[str]
```

The result holds **no timestamp and no random value**. Two runs over the same
dataset and the same outputs produce byte-identical results, so a result file
can be committed and diffed. The time a run happened belongs to the run
manifest of whatever produced the outputs, not to the score.

`passed` is `None` when no threshold could be evaluated. A run with no
evaluable threshold has not passed; it has not been measured. The difference
matters, and the report says which one it is.

## 7. Thresholds

A YAML file.

```yaml
version: 1
tolerance: 0.0
defaults:
  min_verbatim_rate: 0.99
tasks:
  pain_extraction:
    min_precision: 0.80
    min_recall: 0.60
  severity:
    min_within_one_rate: 0.80
```

Each task maps a metric name to the lowest value that passes. The `min_`
prefix is dropped before the name is matched, so `min_precision` and
`precision` name the same metric. A metric that is not listed is not
checked. A metric that is `null` is reported as `blocked`, and the report
says which of the two reasons it was: the task has no human label, or the
metric has no denominator in this task. A metric that is present and below
its threshold fails.

A blocked metric does not fail the run, because there is no number to
compare. It is not silently excused either: the reason is on the check, and
the run's warnings name it.

The key names a metric the family produces. A key in a task block must name
a metric that task produces, because a task that cannot produce the metric
can never check it and a threshold that can never be checked is worse than
no threshold. A key in the `defaults` block must name a metric some family
produces, and it applies to every task that has that metric. The quote
fidelity family produces `verbatim_rate`, so the key is `min_verbatim_rate`;
a key that names a metric no family produces is never checked, and the
threshold file is rejected when it does.

Only a metric that is better when it is higher may carry a threshold. The
check is a floor, and a floor on a rate that is better when it is lower
would pass the worse model. The rates that are better when they are lower
are `false_accept_rate`, `false_reject_rate` and `mean_absolute_error`.

```python
class ThresholdCheck(BaseModel):
    task: str
    metric: str
    threshold: float
    value: float | None
    status: str        # "pass" | "fail" | "blocked"

class ThresholdReport(BaseModel):
    checks: list[ThresholdCheck]
    passed: bool | None
    failed: list[str]
    blocked: list[str]
```

The `defaults` block applies to every task that does not name the metric
itself. A task's own entry wins.

## 8. Comparison and regression

`compare_runs(baseline, candidate, thresholds)` produces the comparison.

**Two runs are comparable only when they scored the same dataset version.**
Across different datasets the metric deltas mean nothing, so the verdict is
`not_comparable` and no delta is reported. The report still names both runs
and both models.

**A metric regresses when the candidate moved the wrong way by more than the
tolerance.** The tolerance comes from the threshold file and defaults to
zero.

The direction depends on the metric. A rate that is better when it is higher
— `precision`, `recall`, `f1`, `accuracy`, `exact_rate`, `within_one_rate`,
`verbatim_rate`, `key_agreement`, `domain_accuracy`, and the per-field
agreement rates — regresses when the candidate is below the baseline. A rate
that is better when it is lower — `false_accept_rate`, `false_reject_rate`,
`mean_absolute_error` — regresses when the candidate is above the baseline. A
drop in a false-accept rate is an improvement, and a comparison that called it
a regression would be wrong.

A count is not compared. `false_positive`, `failures`, `n`, and the
`expected_*` values are shown with their numbers so a reader can see them, and
they never move the verdict: a count on its own says nothing about whether
the model got better. The `expected_*` values are a property of the label,
so they are the same in every run over one dataset.

A metric that is blocked in either run is not compared, because a number that
does not exist cannot fall.

**A case regresses when it passed in the baseline and fails in the
candidate.** The comparison lists the flipped cases with both verdicts, so a
regression can be traced to the cases that caused it.

```python
class MetricDelta(BaseModel):
    task: str
    metric: str
    baseline: float | None
    candidate: float | None
    delta: float | None
    status: str        # "improved" | "regressed" | "unchanged" | "not_comparable"

class CaseFlip(BaseModel):
    task: str
    case_id: str
    baseline: bool | None
    candidate: bool | None
    kind: str          # "regressed" | "improved"

class ComparisonReport(BaseModel):
    baseline_run_id: str
    candidate_run_id: str
    baseline_model: str
    candidate_model: str
    baseline_provider: str
    candidate_provider: str
    dataset_version_match: bool
    metric_deltas: list[MetricDelta]
    case_flips: list[CaseFlip]
    regressions: int
    improvements: int
    verdict: str       # "regression" | "improvement" | "no_change" | "not_comparable"
    notes: list[str]
```

The verdict is `regression` when any metric regressed or any case flipped
from pass to fail, `improvement` when metrics rose and no case flipped
backwards, `no_change` when nothing moved, and `not_comparable` when the
dataset versions differ.

## 9. Reports

Two renderings, both from the same records.

**The benchmark report** names the model under test with its provider, model,
model version, catalog, and prompt versions, states the dataset version and
the evaluator version, and then shows one table per task: the metric, its
value, and its threshold status. A blocked task says so and says why. A
metric with no label shows `n/a` and the word `blocked`, never a number.

**The label status report** shows, per task, how many cases exist, how many
carry a human label, and whether the task is blocked. It is the document a
person reads to know what work is left before the A1 gate can open.

The report also states what was not measured. A benchmark that measured quote
fidelity and nothing else must not read like a benchmark that measured
quality.

## 10. The command surface

A new group `ph bench`. The existing `ph eval` keeps its name, its
arguments, and its behaviour.

| Command | What it does | stdout |
|---|---|---|
| `ph bench run --cases PATH --spec PATH --out PATH [--thresholds PATH]` | score one model's outputs against a dataset | the run result |
| `ph bench compare --baseline PATH --candidate PATH --out PATH [--thresholds PATH]` | compare two run results and detect regressions | the comparison |
| `ph bench report --run PATH --out PATH [--compare PATH]` | write the benchmark report | a summary of what was written |
| `ph bench labels --cases PATH [--out PATH]` | print the label status of a dataset | the label status |

Every command prints a JSON object on stdout, like the rest of the CLI. The
markdown reports are written to `--out`; they are not printed, so a script
reading the output always reads JSON.

`--out` is required for `bench run`, `bench compare` and `bench report`,
because each of them produces a file. `bench labels` writes the label status
table to `--out` when it is given, and the status is on stdout either way.
The label status table is what a person works from when they fill the
dataset in, so writing it out is worth the one option.

`bench run` exits `1` when the thresholds fail, and `0` when they pass or
when nothing could be evaluated. The JSON carries the reason either way, so
the exit code is never the only evidence.

`bench compare` exits `1` when the verdict is `regression`, and `0`
otherwise.

`bench report` and `bench labels` exit `1` on a broken or missing input
file.

## 11. The shipped dataset and thresholds

`eval/datasets/ds-v1.json` is the seed dataset. Its inputs are real text
taken from the pages this repository has already captured. Every `expected`
block is `null`.

That is the honest state of the world today: the inputs exist, the labels do
not. `ph bench labels --cases eval/datasets/ds-v1.json` prints the count.

`eval/thresholds.yaml` holds the starting thresholds. They are a starting
point, not a claim. A threshold nobody has measured against is a guess, and
the report says the numbers behind it are blocked.

## 12. What this wave does not do

- It does not label the dataset. A human does that.
- It does not open the A1 gate.
- It does not run a model. The infra consumes outputs; producing them is the
  runner's job and belongs to the phase that introduces the task.
- It does not implement clustering, independence, competitor search, or
  counter-evidence search. It defines how those will be measured.
- It does not change `src/product_hunter/evaluation.py`, `ph eval`, or any
  other Phase 1 command.
- It does not add a dependency.

## 13. Acceptance

| # | Criterion | How it is proved |
|---|---|---|
| E1 | the ten task families are supported | a test per family, over a known input and a known output |
| E2 | cases and datasets are versioned | the dataset carries a version, each case carries a version, and a run records both |
| E3 | scoring is deterministic | the same dataset and outputs produce byte-identical result JSON, twice |
| E4 | a metric needs a label | a case with no `expected` yields `null` metrics and a blocked task |
| E5 | no label is fabricated | the shipped dataset has `null` in every `expected` block, and a test asserts it |
| E6 | thresholds are configurable | the threshold file drives the pass and fail verdicts, and a test flips one |
| E7 | two models can be compared | a comparison of two run results reports the deltas |
| E8 | regressions are detected | a test drops one metric and one case, and the comparison reports both |
| E9 | the output is machine-readable | every command prints JSON, and a result file round-trips through the model |
| E10 | the run names the model under test | the result carries provider, model, model version, catalog, prompt version, evaluator version, and dataset version |
| E11 | Phase 1 still works | the existing test suite passes unchanged, and `ph eval` behaves as before |
| E12 | the label status is reported | `ph bench labels` prints the per-task counts |

# Phase 2 Evaluation Infrastructure — Implementation Plan

**Status:** implemented. The spec was amended twice during the review, so the
metric rules in sections 3 and 4 below are as amended; where this plan and
the spec differ, the spec is the contract.
**Date:** 2026-10-21
**Spec:** `docs/reasonix/specs/phase-2-evaluation-infrastructure.md`
**Architecture:** `docs/reasonix/plans/phase-2-evaluation-architecture.md`

---

## 1. The order of work

The modules stack from the bottom up, and each one is testable before the
next one exists.

| Step | What | Depends on |
|---|---|---|
| 1 | `cases.py` and its test | nothing |
| 2 | `tasks.py` and its test | `cases.py` |
| 3 | `run.py` and its test | `cases.py`, `tasks.py` |
| 4 | `report.py` and its test | `run.py` |
| 5 | the CLI group and its test | the package |
| 6 | the seed dataset and the threshold file | the package |
| 7 | the README and the docs trail | everything |

## 2. Step 1 — `src/product_hunter/eval/cases.py`

**New file.** Roughly 150 lines.

### Public names

- `EVALUATOR_VERSION = "eval-v1"`
- `TaskName(str, Enum)` with the ten values from the spec.
- `EvalCase(BaseModel)`, `extra="forbid"`, fields `case_id`, `task`,
  `case_version`, `input`, `expected`.
- `EvalDataset(BaseModel)`, `extra="forbid"`, fields `dataset_version`,
  `created_at`, `description`, `cases`.
- `TaskLabelStatus(BaseModel)`, `LabelStatus(BaseModel)`.
- `EvalError(Exception)`.
- `load_dataset(path: Path) -> EvalDataset`
- `label_status(dataset: EvalDataset) -> LabelStatus`
- `dataset_cases_for(dataset, task) -> list[EvalCase]`

### Behaviour

`load_dataset` reads a JSON document. A missing file, a JSON syntax error, a
document that is not an object, a missing `dataset_version`, a `cases` value
that is not a list, a duplicate `case_id`, or an unknown task name raises
`EvalError` whose message names the file and the problem. The loader never
guesses and never skips a bad case.

`label_status` counts, per task in the dataset, the cases and the labeled
cases, and sets `blocked` when a task has no labeled case and the family
needs a label. `quote_fidelity` needs no label, so it is never blocked and
the label status agrees with the run result about it. Tasks that are in the
`TaskName` enum but not in the dataset are reported with a count of zero,
so the status shows which families have no data at all.

### Test file `tests/test_eval_cases.py`

- a valid dataset loads and keeps its version
- a missing file raises
- a JSON syntax error raises and names the file
- a duplicate case id raises
- an unknown task raises
- a case with a missing expected block loads as unlabeled
- the label status counts the labeled cases
- a task with no labeled case is blocked
- a task with no case at all is reported with a count of zero
- the label status is stable across the order of the tasks

## 3. Step 2 — `src/product_hunter/eval/tasks.py`

**New file.** Roughly 380 lines. The largest module in the package.

### Public names

- `ScoreError(Exception)`
- `CaseScore(BaseModel)`: `case_id`, `task`, `passed`, `metrics`, `detail`,
  `error`.
- `score_case(case: EvalCase, output: dict) -> CaseScore`

### The four primitives

Each is a module-level function with a name that says what it returns.

- `classification_metrics(predicted, expected, labels) -> dict`
- `detection_metrics(predicted, expected) -> dict`
- `pairwise_metrics(predicted_groups, expected_groups) -> dict`
- `agreement_metrics(predicted, expected) -> dict`

`classification_metrics` returns `n`, `accuracy`, `precision`, `recall`,
`f1` (macro averages over the labels the comparison touched, which is the
expected set together with the predicted set), `per_label`, and `confusion`.
A touched label with a zero denominator scores `0.0`, not `None`; a label
nobody used keeps `None` and stays out of the macro.

`detection_metrics` returns `true_positive`, `false_positive`,
`false_negative`, `precision`, `recall`, `f1`, and `unjudged`. A value is
`None` only when there was nothing on either side to compare.

`pairwise_metrics` returns `predicted_pairs`, `expected_pairs`,
`true_positive`, `false_positive`, `false_negative`, `precision`, `recall`,
`f1`, and `group_count`. A case whose groups hold more than
`MAX_GROUP_PAIRS` pairs is refused rather than scored.

`agreement_metrics` returns `n`, `exact`, `exact_rate`, `within_one`,
`within_one_rate`, `mean_absolute_error`.

### The ten scorers

One private function per family, named after the family. Each takes the case
and the model output and returns the metric dict. Each raises `ScoreError`
with a clear message when the output does not match the shape the spec
defines, or when the case input is missing something the metric needs.

- `score_pain_extraction` — detection over the canonical forms of the
  genuine quotes. A predicted candidate matches a labelled pain point when
  `quote.canonicalize` folds them to the same string.
- `score_false_positive_rejection` — classification over the labels
  `pain` and `not_pain`, plus `false_accept_rate` and `false_reject_rate`.
- `score_quote_fidelity` — calls `quote.locate_quote(text, quote)`. The
  metric is `verbatim_rate`, `exact_rate`, `canonical_rate`, and `failures`.
  This scorer needs no label.
- `score_severity` — agreement over the integers.
- `score_normalization` — builds the predicted key with
  `normalization.normalize_fields` and compares it with the key built from
  the labelled fields. Returns `domain_accuracy`, `key_agreement`, and a
  per-field agreement rate.
- `score_duplicate_detection` — pairwise over the groups, plus a group
  count comparison.
- `score_author_independence` — pairwise over the groups, plus the
  independent author count computed from the groups. The count is computed
  by the code, never taken from the model.
- `score_clustering` — pairwise over the groups, plus a group count
  comparison.
- `score_competitor_identification` — detection over the judged candidates.
  A prediction naming a candidate that was never judged is counted in
  `unjudged_predictions` and kept out of precision.
- `score_counter_evidence_classification` — classification over `supports`,
  `refutes`, and `neutral`, with the confusion matrix.

### The dispatcher

`score_case` looks up the family by `case.task`, calls the scorer, and
wraps the result in a `CaseScore`. A case with no `expected` returns a
`CaseScore` whose `passed` is `None` and whose `metrics` are `None` values,
unless the family is `quote_fidelity`, which is always computable.

### Test file `tests/test_eval_tasks.py`

One test per family, over a known input and a known output, asserting the
exact metric values. Plus:

- a case with no label yields `None` metrics and `passed is None`
- a case with no label on `quote_fidelity` still yields a metric
- a malformed output raises `ScoreError`
- an unknown task name is impossible by construction
- the same case and output scored twice gives the same result

## 4. Step 3 — `src/product_hunter/eval/run.py`

**New file.** Roughly 330 lines.

### Public names

- `ModelIdentity(BaseModel)`: `provider`, `model`, `model_version`,
  `model_catalog`, `prompt_version`, `prompt_versions`, with a
  `prompt_for(task)` method.
- `RunSpec(BaseModel)`: `run_id` plus the identity fields plus
  `dataset_version` and `outputs`.
- `ModelOutput(BaseModel)`: `case_id`, `output`, `prompt_version`.
- `TaskResult(BaseModel)`, `EvalRun(BaseModel)`.
- `ThresholdCheck(BaseModel)`, `ThresholdReport(BaseModel)`.
- `MetricDelta(BaseModel)`, `CaseFlip(BaseModel)`, `ComparisonReport(BaseModel)`.
- `Thresholds(BaseModel)`, `load_thresholds(path) -> Thresholds`.
- `run_dataset(dataset, spec, thresholds) -> EvalRun`
- `check_thresholds(run, thresholds) -> ThresholdReport`
- `compare_runs(baseline, candidate, thresholds) -> ComparisonReport`
- `load_run(path) -> EvalRun`
- `read_outputs(path) -> list[ModelOutput]`

### Behaviour

`run_dataset` scores every case of the dataset. A case with no output is
recorded as a case error. An output for an unknown case is an error. The
result holds no timestamp and no random value, so the same inputs give
byte-identical JSON.

`check_thresholds` reads the `defaults` block and the per-task blocks. A
metric in `defaults` that a task does not override is checked for that task.
A metric whose value is `None` is `blocked` and does not fail the run. The
report's `passed` is `None` when no check could be evaluated.

`compare_runs` refuses across dataset versions: the verdict is
`not_comparable` and no delta is produced. Within one dataset version it
lines the cases up by `case_id`, computes the metric deltas, and lists the
cases that flipped. A metric that is `None` in either run is `not_comparable`
and is not compared.

### Test file `tests/test_eval_run.py`

- the run record carries the model identity and the versions
- a run over a dataset with no labels blocks every task that needs a label
- the result has no timestamp and is byte-identical across two runs
- an output for an unknown case is refused
- a case with no output is a case error
- a threshold below the value passes, above fails
- a blocked threshold does not fail the run
- a run with no evaluable threshold reports `passed is None`
- the default threshold applies to a task that does not name the metric
- a task entry overrides the default
- comparing two runs reports the metric deltas
- a metric that fell is a regression
- a case that flipped from pass to fail is a case flip
- comparing across dataset versions is `not_comparable` and reports no delta

## 5. Step 4 — `src/product_hunter/eval/report.py`

**New file.** Roughly 170 lines.

### Public names

- `render_benchmark_markdown(run, comparison=None) -> str`
- `render_label_status(status) -> str`
- `write_benchmark_report(run, path, comparison=None) -> None`

### Behaviour

The benchmark report names the provider, the model, the model version, the
catalog, the prompt version per task, the evaluator version, and the dataset
version. It shows one table per task with the metric, the value, and the
threshold status. A blocked metric shows `n/a` and `blocked`, never a
number. The report ends with a sentence naming what was not measured, so a
report that measured quote fidelity alone does not read like a quality
benchmark.

The label status report shows, per task, the cases, the labeled cases, and
whether the task is blocked.

### Test file `tests/test_eval_report.py`

Folded into `tests/test_eval_run.py` to keep the file count down. The tests
cover the blocked rendering, the metric table, the identity block, and the
"not measured" sentence.

## 6. Step 5 — the CLI group

**Modified file:** `src/product_hunter/cli.py`.

Add a `bench` group next to the `db` group, with `run`, `compare`, `report`,
and `labels` subcommands. Register them in `build_parser()` and dispatch
them in `main()`.

| Command | Arguments |
|---|---|
| `bench run` | `--cases`, `--spec`, `--out`, `--thresholds` (optional) |
| `bench compare` | `--baseline`, `--candidate`, `--out`, `--thresholds` (optional) |
| `bench report` | `--run`, `--out`, `--compare` (optional) |
| `bench labels` | `--cases` |

Each command follows the existing pattern: resolve the paths against the
repository root with `_resolve_path`, print a JSON object, return `0` or `1`.
`bench run` returns `1` when the thresholds fail. `bench compare` returns `1`
when the verdict is `regression`.

The default threshold file is `eval/thresholds.yaml` under the repository
root. `--thresholds` overrides it.

### Test file `tests/test_cli_bench.py`

- `bench labels` prints the label status as JSON
- `bench run` scores a dataset and writes the result file
- `bench run` exits `1` when a threshold fails
- `bench compare` prints the comparison and exits `1` on a regression
- `bench report` writes the markdown report
- a missing file exits `1` with an `ok: false` payload
- the existing `ph eval` and `ph review-sheet` still behave as before

## 7. Step 6 — the shipped files

**`eval/datasets/ds-v1.json`**: the seed dataset. Real inputs taken from the
text this repository has already captured, `null` in every `expected` block.
About three cases per family.

**`eval/thresholds.yaml`**: the starting thresholds, with the `defaults`
block and the per-task blocks.

**`eval/README.md`**: the formats. The dataset shape, the run spec shape, the
outputs shape, the result shape, and the rule that a quality number needs a
label.

## 8. Step 7 — the documentation

**Modified:** `README.md`. A new section on the benchmark, the four
commands, the two file formats, and the rule about labels.

**New:** `docs/reasonix/reports/phase-2-evaluation-infrastructure.md`, the run
report.

## 9. The gates

| Gate | Command |
|---|---|
| code | `uv run pytest` |
| typecheck | not configured in this repository; the code is checked by the suite |
| lint | not configured in this repository |

The code gate is the suite. It must be green before the run moves on, and it
must stay green after any fix.

## 10. The blast radius

| Path | Change |
|---|---|
| `src/product_hunter/eval/__init__.py` | new |
| `src/product_hunter/eval/cases.py` | new |
| `src/product_hunter/eval/tasks.py` | new |
| `src/product_hunter/eval/run.py` | new |
| `src/product_hunter/eval/report.py` | new |
| `src/product_hunter/cli.py` | the `bench` group |
| `tests/test_eval_cases.py` | new |
| `tests/test_eval_tasks.py` | new |
| `tests/test_eval_run.py` | new |
| `tests/test_cli_bench.py` | new |
| `eval/datasets/ds-v1.json` | new |
| `eval/thresholds.yaml` | new |
| `eval/README.md` | new |
| `README.md` | the benchmark section |
| `docs/reasonix/specs/phase-2-evaluation-infrastructure.md` | new |
| `docs/reasonix/plans/phase-2-evaluation-architecture.md` | new |
| `docs/reasonix/plans/phase-2-evaluation-infrastructure.md` | new |
| `docs/reasonix/prompts/phase-2-*.md` | new |
| `docs/reasonix/reports/phase-2-*.md` | new |

No file outside this list is touched. `src/product_hunter/evaluation.py`,
`config/`, `db/`, and `.loop/adapter.json` are not in the radius.

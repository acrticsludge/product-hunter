# Benchmark

The benchmark scores one model's answers against a fixed set of cases, so two
models, two providers, or two prompt versions can be compared over the same
ground. The infrastructure lives in `src/product_hunter/eval/` and the
commands are `ph bench run`, `ph bench compare`, `ph bench report`, and
`ph bench labels`.

## The rule that shapes it

**A quality number exists only where a human label exists.** A case carries
an `expected` block, and that block is the label. A case with no label still
runs, but the metrics that would need one stay `null`, the task is reported as
`blocked`, and the run does not pass or fail on it. `quote_fidelity` is the
one family that needs no label, because it is checked against the source text.

The shipped dataset `datasets/ds-v1.json` holds real inputs and `null` in
every `expected` block. The labels are a human job, and no code writes one.

## The dataset

One JSON document.

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
      "input": {"text": "the team pastes the totals by hand"},
      "expected": null
    }
  ]
}
```

| Field | Meaning |
|---|---|
| `dataset_version` | the version of the dataset, free-form, `ds-v1` by convention |
| `created_at` | the day the dataset was written, optional |
| `description` | what the dataset holds, optional |
| `cases` | the cases, in the order they are scored |

A case carries a `case_id` that is unique inside the dataset and stable
across dataset versions, a `task` from the ten families, a `case_version`
that starts at 1, an `input`, and an `expected` block that holds the human
label or `null`.

A file that is not valid JSON, a case with an unknown task, a duplicate case
id, or a case whose `input` is not an object is refused. The loader never
guesses and never skips a case.

### The input of each family

| Task | The input holds |
|---|---|
| `pain_extraction` | `text`, `doc_id` |
| `false_positive_rejection` | `text`, `span` |
| `quote_fidelity` | `text` |
| `severity` | `text`, `quote` |
| `normalization` | `text`, `quote` |
| `duplicate_detection` | `documents[]` with `doc_id` and `text_sha256` |
| `author_independence` | `mentions[]` with `mention_id`, `author_id`, `text`, and `propagation_edges[]` |
| `clustering` | `items[]` with `item_id` and `text` |
| `competitor_identification` | `product` with a name and a description, `candidates[]` with a name and a description each |
| `counter_evidence_classification` | `claim`, `items[]` with `item_id` and `text` |

The `expected` block of each family holds the human answer:

| Task | The label holds |
|---|---|
| `pain_extraction` | `pain_points[]` with `quote` |
| `false_positive_rejection` | `is_pain` |
| `quote_fidelity` | nothing |
| `severity` | `severity`, 0 to 5 |
| `normalization` | `domain`, `actor`, `action`, `object`, `consequence` |
| `duplicate_detection` | `groups[]` |
| `author_independence` | `groups[]` |
| `clustering` | `groups[]` |
| `competitor_identification` | `competitors[]`, `not_competitors[]` |
| `counter_evidence_classification` | `labels[]` of `supports`, `refutes`, `neutral` |

A group is a list of identifiers: document ids for the duplicate family,
mention ids for the independence family, item ids for the clustering family.
A label the model gives outside the fixed set is a case error, not a wrong
answer.

## The run spec

One JSON file naming the model under test, the dataset, and the answers.

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

`dataset_version` is optional. When it is there it must equal the version of
the dataset that is scored, and a mismatch is an error. The run always records
the version of the dataset it scored.

`model_version` and `model_catalog` are nullable on purpose: a field that
cannot be filled is left empty rather than filled with a guess.

`prompt_version` is the default, and `prompt_versions` holds the version per
task. The version that answered a task is the map entry when it is there and
the default otherwise. A task with no version is reported as `null` and named
in the warnings of the run.

## The outputs file

The answers of the model, one JSON object per line.

```json
{"case_id": "quote-hn-0001", "output": {"quote": "..."}}
{"case_id": "pain-hn-0001", "output": {"candidates": [{"quote": "...", "validity": "genuine"}]}}
```

A line may carry its own `prompt_version`, which overrides the run default. An
output for a case the dataset does not hold is an error, and a case with no
output is recorded as an error for that case rather than skipped.

The `output` object holds the answer in the shape the family defines. The
scorers in `src/product_hunter/eval/tasks.py` are the authority on it.

## The result file

`ph bench run` writes the result as JSON. It holds no timestamp and no random
value, so the same dataset and the same answers produce byte-identical bytes
and a result file can be committed and diffed.

```json
{
  "run_id": "ds-v1-deepseek-2026-10-21",
  "evaluator_version": "eval-v1",
  "dataset_version": "ds-v1",
  "provider": "opencode-go",
  "model": "deepseek-v4.1-flash",
  "tasks": [],
  "label_status": {},
  "thresholds": {},
  "passed": null,
  "warnings": []
}
```

`passed` is `null` when no threshold could be evaluated. A run with nothing
measurable has not passed; it has not been measured.

## The threshold file

One YAML file.

```yaml
version: 1
tolerance: 0.0
defaults:
  min_verbatim_rate: 0.99
tasks:
  pain_extraction:
    min_precision: 0.80
    min_recall: 0.60
```

Every value is a floor: a metric passes when it is at or above the number. The
`min_` prefix is dropped before the name is matched, so `min_precision` and
`precision` name the same metric. A metric that is not listed is not checked,
and a metric that is `null` is reported as `blocked` and does not fail the run.
A blocked check says which of the two reasons it was: the task has no human
label, or the metric has no denominator in this task. The run's warnings name
it.

Only a metric that is better when it is higher may carry a threshold. The
check is a floor, and a floor on a rate that is better when it is lower would
pass the worse model. `false_accept_rate`, `false_reject_rate` and
`mean_absolute_error` are better when they are lower, so they cannot carry a
floor, and the file is rejected when it names one.

The `defaults` block applies to every task that does not name the metric
itself, and a task's own entry wins.

The key names a metric the family produces. A key in a task block must name a
metric that task produces: a task that cannot produce the metric can never
check it, and a threshold that can never be checked is worse than no threshold.
A key in the `defaults` block must name a metric some family produces, and it
applies to every task that has that metric. A key that names a metric no
family produces is never checked, and the file is rejected when it does. The
quote fidelity family produces `verbatim_rate`, so the key is
`min_verbatim_rate`.

`tolerance` is the distance a metric may move before the comparison calls it
a regression. It defaults to zero.

## The commands

| Command | What it does |
|---|---|
| `ph bench run --cases PATH --spec PATH --out PATH [--thresholds PATH]` | score one model's answers against a dataset |
| `ph bench compare --baseline PATH --candidate PATH --out PATH [--thresholds PATH]` | compare two results and detect regressions |
| `ph bench report --run PATH --out PATH [--compare PATH]` | write the benchmark report |
| `ph bench labels --cases PATH [--out PATH]` | print the label status of a dataset |

Every command prints one JSON object on stdout. The markdown reports go to
the file named by `--out`, so a script reading the output always reads JSON.

`--thresholds` defaults to `eval/thresholds.yaml` under the repository root.
When that file is absent the run still happens, and the result says in its
warnings that no threshold was checked.

`bench run` exits 1 when a threshold failed and 0 otherwise, including when
nothing could be measured. `bench compare` exits 1 when the verdict is a
regression. The JSON always carries the reason, so the exit code is never the
only evidence.

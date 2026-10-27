# Phase 2 Evaluation Infrastructure — Architecture Notes

**Date:** 2026-10-21
**Spec:** `docs/reasonix/specs/phase-2-evaluation-infrastructure.md`

---

## 1. The boundary question

The repository already has `src/product_hunter/evaluation.py`, 698 lines that
score a filled review sheet. The obvious move is to grow it. The other move is
a new package next to it.

Three facts settle it.

**The two things have different inputs.** The review sheet is a CSV a person
filled in, one row per candidate, with a fixed set of columns. The benchmark
case is a JSON document with a task name, a versioned input, and an optional
label. The Phase 1 reader is a strict CSV parser with 22 named columns and a
long list of refused values. Folding a second input format into it would
mean two parsers, two column vocabularies, and two error styles in one
module.

**The two things have different outputs.** `EvalReport` is a flat set of
counters for one sheet. The benchmark run is a tree: per task, per case,
per metric, plus thresholds, plus a label status, plus a comparison.

**The Phase 1 module is already in use.** `ph eval` and its 34 tests depend
on `evaluate()` and `read_review_sheet()`. Touching it risks the one part of
the system that currently works and has been reviewed.

So: a new package `src/product_hunter/eval/`, and `evaluation.py` stays
exactly as it is.

## 2. Module boundaries

```
eval/
  cases.py    the case, the dataset, the label status
  tasks.py    the ten task families and the four metric primitives
  run.py      the model identity, the run record, thresholds, comparison
  report.py   the benchmark report and the label status rendering
```

The dependency arrow points one way: `report.py` reads `run.py`, `run.py`
reads `tasks.py` and `cases.py`, `tasks.py` reads `cases.py`. Nothing reads
upward. `cases.py` imports nothing from the package.

`tasks.py` holds the four metric primitives and the ten scorers. The
primitives are the reason the file is not ten unrelated functions: a
classification, a detection, a pairwise grouping, and an agreement cover all
ten families, and each family is a small adapter that extracts the right
lists from the case and the output.

`run.py` holds the record types and the comparison. The comparison lives
here rather than in `report.py` because a comparison is a result, not a
rendering. The report renders results; it does not compute them.

`report.py` is the only module that produces text. Every other module
produces data.

## 3. The Phase 1 reuse points

| Need | Call | Why it is the right reuse |
|---|---|---|
| locate a model quote in source text | `quote.locate_quote` | the pipeline already decides what a verbatim quote is. A second definition would drift. |
| fold quotes and whitespace for comparison | `quote.canonicalize` | same reason. Two pain points are the same pain point when their canonical forms match. |
| build a normalized key | `normalization.normalize_fields` | the benchmark must measure the key rule the pipeline uses, not a copy of it. |
| the controlled domain list | `normalization.Domain` | fifteen values, one place. |
| repository root | `config.repo_root` | the CLI already resolves relative paths against it. |

The one thing that is *not* reused is `evaluation.evaluate`. The two systems
score different things over different inputs, and merging them would mean one
module with two unrelated vocabularies. The relationship between them is the
shared repo, the shared conventions, and the shared answer to "how do we
score a model" — not a shared function.

## 4. The two decisions that shaped the design

### 4.1 A metric needs a label, and the system says so out loud

The alternative is to derive quality from the model's own output: score the
model against itself, or score the agreement between two prompt orderings.
Both produce a number. Neither is a quality number, and the difference is
invisible in a JSON file unless the system refuses to produce it.

So the absence of a label is a first-class state. A metric is `null`, a task
is `blocked`, a threshold on a blocked metric does not fail the run, and the
report names the blocked tasks in a sentence. The pass verdict of a run with
nothing measurable is `None`, not `True`.

`quote_fidelity` is the one family that does not need a label, because it is
checked against the source text. It is exempt by construction, not by
convention.

### 4.2 The run result has no timestamp

A result file that carries a clock cannot be diffed, and a benchmark whose
numbers change when nothing changed is not evidence of anything. So the
result holds no timestamp and no random value. The same dataset and the same
outputs produce byte-identical JSON, and that is a test.

The time the outputs were produced belongs to the run manifest of whatever
produced them. That file already exists in the repository layout, and it is
the right owner of that fact.

## 5. The comparison is refused across datasets

Comparing a run over `ds-v1` with a run over `ds-v2` produces deltas that
mean nothing: the cases are not the same cases, so a metric that fell may
have fallen because the dataset changed. The comparison refuses, says
`not_comparable`, and names both runs and both models. It does not produce
the deltas anyway with a warning attached, because a number in a report gets
read even when the note next to it says not to.

Within one dataset version, the comparison lines the cases up by `case_id`
and reports the metrics that moved and the cases that flipped.

## 6. The CLI surface

`ph bench` is a new group, mirroring the existing `ph db` group, because
four related commands would otherwise take four flat top-level names. The
existing `ph eval` cannot become a group without breaking its name, so the
new group gets a new name.

Exit codes follow the repository: `0` for success, `1` for failure. A
threshold failure is a failure, so `bench run` exits `1` when a threshold
fails and `0` when the thresholds pass or nothing could be measured. The
JSON always carries the reason, so the exit code is never the only evidence.

## 7. What the seed dataset is

`eval/datasets/ds-v1.json` holds real inputs taken from the pages the
repository has already captured, and `null` in every `expected` block.

The inputs are real so that a person labelling the dataset labels real text.
The labels are absent because nobody has labelled them yet, and a fabricated
label would be worse than an absent one: it would make the A1 gate look
open.

## 8. The size of the change

Roughly 900 lines of product code and 600 lines of test code, plus one CLI
group, one dataset file, one threshold file, and the documentation. No
dependency is added. The existing 34 evaluation tests and the rest of the
suite are untouched.

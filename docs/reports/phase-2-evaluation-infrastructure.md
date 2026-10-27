# Phase 2 Evaluation Infrastructure — Run Report

**Date:** 2026-10-21
**Node:** `loop-free` (free tier)
**Spec:** `docs/reasonix/specs/phase-2-evaluation-infrastructure.md`
**Architecture:** `docs/reasonix/plans/phase-2-evaluation-architecture.md`
**Plan:** `docs/reasonix/plans/phase-2-evaluation-infrastructure.md`
**Handoff:** `docs/reasonix/handoffs/phase-2-evaluation-infrastructure-handoff.md`

---

## 1. What was built

A benchmark harness that scores a model's answers against a versioned,
human-labelled dataset, so two models, two providers or two prompt versions
can be compared over the same ground.

The Phase 1 harness stays exactly as it was. `src/product_hunter/evaluation.py`
has no diff, `ph eval` still scores a filled review sheet, and the 46 tests
that cover it pass unchanged. The new work sits beside it in
`src/product_hunter/eval/` and reaches it through the new `ph bench` command
group.

### The package

| Module | Lines | Job |
|---|---|---|
| `eval/cases.py` | 191 | the case, the versioned dataset, the label status |
| `eval/tasks.py` | 768 | the four metric primitives and the ten family scorers |
| `eval/run.py` | 862 | the model identity, the run record, thresholds, comparison |
| `eval/report.py` | 198 | the benchmark report and the label status rendering |
| `eval/__init__.py` | 130 | the public names |

### The surface

| Path | Lines | |
|---|---|---|
| `eval/datasets/ds-v1.json` | 703 | the seed dataset: 30 cases, 3 per family, every `expected` is `null` |
| `eval/thresholds.yaml` | 35 | the starting thresholds |
| `eval/README.md` | 182 | the four file formats and the rule about labels |
| `src/product_hunter/cli.py` | +241 | the `ph bench` group |

### The tests

185 new tests, in `tests/test_eval_cases.py` (249 lines),
`tests/test_eval_tasks.py` (534), `tests/test_eval_run.py` (1321) and
`tests/test_cli_bench.py` (430).

## 2. The ten task families

Every family shares one shape: a case carries an input, the model produces an
output, and a scorer is a pure function of the two. The scorers reuse
`quote.locate_quote`, `quote.canonicalize` and
`normalization.normalize_fields`, so the benchmark measures the rules the
pipeline uses rather than copies of them.

| Family | Metric it leads with | Needs a label |
|---|---|---|
| `pain_extraction` | `f1` | yes |
| `false_positive_rejection` | `accuracy` | yes |
| `quote_fidelity` | `verbatim_rate` | no |
| `severity` | `exact_rate` | yes |
| `normalization` | `key_agreement` | yes |
| `duplicate_detection` | `f1` | yes |
| `author_independence` | `f1` | yes |
| `clustering` | `f1` | yes |
| `competitor_identification` | `f1` | yes |
| `counter_evidence_classification` | `accuracy` | yes |

## 3. The rule the whole thing rests on

**A quality number exists only where a human label exists.**

An unlabelled case still runs, and its metrics stay `None`. Its task is
reported as blocked. The threshold check reports it as blocked with the
reason, and the run's warnings name it. The benchmark report prints `n/a` and
the word `blocked`, never a number.

`quote_fidelity` is the one family that needs no label, because it is checked
against the source text through `quote.locate_quote`. That exemption is
narrow by construction: the scorer reads `case.input["text"]` and
`output["quote"]` and never `case.expected`.

The shipped dataset carries `null` in every one of its 30 `expected` blocks.
A test globs `eval/datasets/*.json` and asserts it. The label status reports
0 labelled and nine blocked tasks, with `quote_fidelity` not blocked.

**The A1 human-labelled acceptance gate is still blocked.** This wave built
the instrument; it did not fill it.

## 4. The gates

| Gate | Result |
|---|---|
| code | `uv run pytest` → **921 passed**, exit 0, run twice with identical output |
| the four new test files | **185 passed** |
| the Phase 1 evaluation tests | **46 passed** |
| determinism | two `ph bench run` invocations over the same inputs produced byte-identical result files (same SHA-256) |
| the seed dataset | 30 cases, 0 labelled, every `expected` is `null` |
| the label status | 9 tasks blocked, `quote_fidelity` measurable, `total_labeled` 0 |

## 5. What the review and the hardening pass found

Neither pass was a formality. Both changed the code.

### The hardening pass

- **Closed:** every eval file reader now checks the file size before reading
  it, with a 32 MiB cap, the way `fetch.py` caps a network body. Six readers.
- **Closed:** every untrusted value that reaches an exception message is cut
  to a bounded length, so a one-megabyte string in a wrong field cannot
  produce a one-megabyte error message. `brief_value` cannot itself raise.
- **Declined by agreement:** repository-root containment for the CLI path
  arguments. They come from the operator's own command line, and the Phase 1
  commands set the same precedent.

### The adversarial review

Two must-fix items, both confirmed by reproduction before they were fixed:

- **The classification `f1` was empty for a wrong answer**, so the macro
  average ran over the labels the model happened to get right. A model that
  answered half the cases wrong scored `f1` 1.0 and passed a `min_f1` gate
  with exit 0. Fixed with the `zero_division` convention: a label the
  comparison touched and the model got wrong scores `0.0`.
- **The four per-field agreement rates were summed, not averaged**, so the
  report printed agreement rates above 1.0 and a threshold on them would
  always pass. Fixed at the root: a rate is told from a count by the type of
  the case value, not by a list of metric names that can drift.

Eight should-fix and nice-to-have items were also closed: the pair
explosion, a task-scoped threshold naming another task's metric, the reason
a blocked metric gives, the label status and the run result disagreeing about
`quote_fidelity`, the threshold rules missing from the README, a UTF-8 BOM
reported as invalid JSON, and three smaller ones.

The re-review returned **ship**.

## 6. What is deliberately not here

- **No label.** Filling the dataset is a human job.
- **No runner.** The harness consumes model outputs; producing them belongs
  to the phase that introduces each task.
- **No production code** for clustering, independence, competitor search or
  counter-evidence search. This defines how they will be measured.
- **No change** to `src/product_hunter/evaluation.py`, to `ph eval`, or to
  any other Phase 1 command.
- **No dependency** added.
- **No way to raise the pair cap** from the command line. It is a module
  constant; an operator with a case that needs more pairs must split the case.

## 7. The repository is shared

Other agents were working in this repository at the same time. Twelve
commits landed while this run was open, and the documentation tree was
renamed to `docs/reasonix/` underneath it.

Two files are therefore shared: `src/product_hunter/cli.py` and `README.md`.
This change is committed with only its own hunks in those two files, so the
other agents' work stays uncommitted in the working tree and under their own
messages. The `ph bench` group, the `ph models` group and the `ph research`
group all coexist in the working tree.

## 8. The next step

Fill `eval/datasets/ds-v1.json`. A person writes the `expected` block of
each case, and `ph bench labels` reports the count. Until then the benchmark
measures quote fidelity and nothing else, and says so.

The first runner should produce outputs for the quote fidelity family, so
that one real number exists in the record before the labels arrive.

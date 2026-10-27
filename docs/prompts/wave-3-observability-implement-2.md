# Implement node B: the evaluation expansion

**You are implementing one node of a graph.** The spec is
[../../specs/wave-3-observability.md](../../specs/wave-3-observability.md) and
the plan is [../../plans/wave-3-observability.md](../../plans/wave-3-observability.md).
Read both before you write a line. The spec is the contract; this prompt is
the work list.

**Working directory.** The worktree you are already in. The branch is
`wave3/observability-evaluation`. Do not touch `main`. Do not merge anything.

**A second agent is working in parallel** on a new `src/product_hunter/observability/`
package, a migration and `src/product_hunter/cli.py`. Do not edit those files.
You may read them.

## The files you own

Modify:

- `src/product_hunter/eval/cases.py`
- `src/product_hunter/eval/tasks.py`
- `src/product_hunter/eval/run.py`
- `src/product_hunter/eval/__init__.py`
- `eval/thresholds.yaml`
- `eval/README.md`

Create:

- `eval/datasets/ds-v2.json`
- `tests/test_eval_expansion.py`

Do not edit any other file. If you believe another file must change, stop and
say so in your final report instead of editing it.

## What to build

### 1. Two new task families

The benchmark covers ten families. The mission names ten areas, and two of
them have no family. Add them.

#### `entity_resolution`

The question: are these two accounts the same person?

It is not `author_independence`. That family groups *mentions* into authors.
This one groups *author accounts* into identity groups, which is the step
`independence/identity.py` performs.

| | |
|---|---|
| Case input | `accounts[]` with `account_id`, `handle`, `display_name`, `source_id`, and an optional `identity_key` |
| Expected block | `groups[]`, each a list of `account_id` values |
| Model output | `groups[]`, each a list of `account_id` values |
| Metrics | `true_positive`, `false_positive`, `false_negative`, `precision`, `recall`, `f1`, `group_count`, `expected_group_count` |

Reuse `pairwise_metrics` from `eval/tasks.py`, the same primitive the
duplicate, independence and clustering families use, so the numbers mean the
same thing across the four grouping families. A group that names an account
the case does not hold is a case error, exactly as it is in the other three.

The scorer's name is `score_entity_resolution`.

#### `scoring_consistency`

The question: does the score the system produces match the score a human
thinks the opportunity deserves?

| | |
|---|---|
| Case input | `opportunity` (free text), `tolerance` (the number of points within which the two scores count as the same) |
| Expected block | `potential` and `confidence`, both numbers from 0 to 100 |
| Model output | `potential` and `confidence`, both numbers from 0 to 100 |
| Metrics | `n`, `potential_exact_rate`, `confidence_exact_rate`, `potential_within_rate`, `confidence_within_rate`, `potential_mean_absolute_error`, `confidence_mean_absolute_error` |

The tolerance lives in the case, not in the code. A family that hard-codes a
tolerance would make the number a property of the scorer instead of a
property of the case.

The two rates that count a match are better when they are higher, so they may
carry a threshold. The two errors are better when they are lower, so they may
not. `eval/run.py` already refuses a threshold on a lower-is-better metric,
and the new metrics must be classified the same way.

The scorer's name is `score_scoring_consistency`.

Both scorers are pure functions of a case and one output. No clock, no
randomness, no network, no database.

### 2. The evaluator version moves

`EVALUATOR_VERSION` in `eval/cases.py` goes from `eval-v1` to `eval-v2`. The
metric set changed, so a v1 result and a v2 result are not the same
measurement.

### 3. Two runs scored by different evaluator versions are not comparable

`compare_runs` in `eval/run.py` already refuses two runs scored against
different dataset versions. Add the same rule for the evaluator version.

Two runs whose `evaluator_version` differs are `not_comparable`. The verdict
says `not_comparable`, no metric delta is reported, and a note says which
versions differ. The existing dataset-version rule must keep working exactly
as it does.

Do not remove or weaken the existing dataset-version rule. The two rules are
independent, and a run that differs in both is still `not_comparable`.

### 4. The dataset `ds-v2`

Create `eval/datasets/ds-v2.json`.

It holds the seed cases of `ds-v1.json`, unchanged, with the same `case_id`
values and the same `case_version`, plus seed cases for the two new
families. `dataset_version` is `ds-v2`. `created_at` is the date the file was
written. The description says what the file holds and that no case carries a
human label.

`ds-v1.json` stays exactly as it is. A dataset is a versioned artefact, so the
version that was scored can always be scored again.

**Every `expected` block in `ds-v2.json` is `null`, including the new ones.**
No precision, no recall, no accuracy and no rate is written anywhere in this
node. A seed case with no label produces no rate, and the rate is `null` with
the reason that the task has no human label. That is the whole point of the
rule, and it is tested below.

The seed inputs for the new families must be real data this repository has
already captured, not text you made up:

- For `entity_resolution`, use the author accounts that appear in the
  `author_independence` cases of `ds-v1.json`: the handles `agumonkey`,
  `hoodmane`, `sama`, `dmon` and `pg`, with the source id each came from and
  the display name the capture recorded. Read the `documents` and `authors`
  tables in `db/migrations/0001_initial.sql` for the column names, and read
  `independence/records.py` for the shape of an author observation.
- For `scoring_consistency`, use the bookkeeping problem the captured text
  describes. The `opportunity` field holds a short description of the
  opportunity, and the `tolerance` field holds the number of points within
  which two scores count as the same. Pick a tolerance you can justify in
  one line of the file's description.

### 5. The thresholds

`eval/thresholds.yaml` gains a block for each new family. Every value is a
floor, and only a metric that is better when it is higher may carry one.

```yaml
  entity_resolution:
    min_f1: 0.80
  scoring_consistency:
    min_potential_within_rate: 0.70
    min_confidence_within_rate: 0.70
```

The exact numbers are yours to choose, but they must be starting values, not
measured facts, and the file's header comment must keep saying so.

### 6. The documentation

`eval/README.md` gains:

- the two new families in the table of task inputs and in the table of
  expected blocks
- the `ds-v2` dataset and the rule that governs dataset versions
- the evaluator-version rule, next to the dataset-version rule
- the new commands, if any changed

The README is prose. It must not read like a machine wrote it: no "In
today's fast-paced world", no "tapestry", no "delve", no "not just X, but
Y", no em-dash-heavy sentences, no rule-of-three lists, no uniform sentence
lengths. Keep the voice the file already has.

### 7. The exports

`eval/__init__.py` exports the two new scorers and anything else the new
families add, following the pattern the file already uses. The `__all__`
list stays sorted the way it is sorted.

## The tests

Create `tests/test_eval_expansion.py`. It is not optional.

| What | The test |
|---|---|
| the new families load | `ds-v2.json` loads, and its case count is the `ds-v1` count plus the new cases |
| nothing is fabricated | every case in `ds-v2.json` has `expected: null`, so every family except `quote_fidelity` is blocked and every quality metric is `null` |
| the label status | `label_status` on `ds-v2` reports the two new families as blocked with zero labelled cases |
| the entity scorer | a perfect grouping scores 1.0, a wrong grouping scores 0.0, and a group naming an unknown account is a case error |
| the scoring scorer | a score inside the tolerance counts, a score outside it does not, and the mean absolute error is the distance |
| the version rule | two runs with different evaluator versions are `not_comparable` and report no delta |
| the dataset rule still works | two runs with different dataset versions are still `not_comparable` |
| reproducibility | the same dataset and the same outputs produce byte-identical JSON, and `eval-v2` is what the run records |
| the thresholds | the new floors are accepted, and a floor on a lower-is-better metric is still refused |

Look at `tests/test_eval_run.py` and `tests/test_eval_tasks.py` for the house
pattern. Build a case with a helper, the way those files do.

## The house style

Read `src/product_hunter/eval/cases.py` and `src/product_hunter/eval/tasks.py`
before you write. The house style is: a module docstring that says what the
module owns, type hints everywhere, `from __future__ import annotations`,
pydantic models with `ConfigDict(extra="forbid")`, and a comment only where
the code cannot say why.

The comment in `eval/tasks.py` that says "the ten task families" becomes "the
twelve task families". Check every other place the count is written: the
module docstring, the `TaskName` docstring, the README, the report text. A
count that is wrong in one place is a bug.

## Before you report

1. `uv run pytest` is green, all of it, including the 2138 tests that were
   already there.
2. `git status --short` shows only your files.
3. You changed no test that was already there, except where you added a case
   to it. If you believe an existing test is wrong, say so in your report
   instead of editing it.

Report what you built, the test results, the coverage of the new families,
and the fact that no human label exists yet, so every number behind them is
`NOT YET MEASURED`.

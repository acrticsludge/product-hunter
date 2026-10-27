# Handoff: evaluation infrastructure, review-fix pass

**RUN_ID:** eval-review-fixes-20261021-7c1e4a90
**Date:** 2026-10-21
**Risk:** declared medium, floor medium. No auth, payment, secret, schema, or
deploy-config change. The trust boundary is a set of files another program can
write: the dataset, the model outputs, and the threshold file.
**Follows:** `9455deb phase2: expand evaluation infrastructure`, which the
feature wave committed while this pass was open.

## 1. Goal

The feature commit landed the benchmark harness. The stage-6 and stage-7
passes then found defects in it. This pass closed the blocking one, closed
the small ones, corrected three stale lines in `README.md`, and produced a
commit that contains only this pass's changes.

## 2. Git baseline

| Item | Value |
|---|---|
| Branch | `main` |
| HEAD at the start | `205a4ba`, then `610c880`, then `0eb75c2`, then `2580f4d`, then `9455deb`, then `9543f9a` |
| HEAD at the end | `9543f9a docs: record the phase 2 evaluation run report` |
| Remote | `github.com/acrticsludge/product-hunter.git`. Not pushed. |

The tree moved underneath this pass several times. The documentation tree was
renamed from a misspelled directory name to `docs/reasonix/`, and its reports
moved with it. The radius was
re-declared once for the new paths.

## 3. What changed

| File | Change |
|---|---|
| `src/product_hunter/eval/run.py` | `check_thresholds` evaluates only `_HIGHER_IS_BETTER` metrics; `load_thresholds` refuses any other threshold key; `Thresholds.tolerance` is `ge=0` |
| `src/product_hunter/eval/report.py` | `_not_measured` cannot claim a task was measured when the dataset holds no task |
| `src/product_hunter/eval/cases.py` | `load_dataset` refuses a deeply nested document with an `EvalError` |
| `src/product_hunter/cli.py` | the bench compare payload carries a `reason`; the bench handlers probe the thresholds file with `exists()`, not `is_file()` |
| `README.md` | the ten-skills line, the Phase 2 model-router correction, the `ph db` command entry, and the `ph bench labels --out PATH` flag |
| `tests/test_eval_run.py`, `tests/test_cli_bench.py` | 8 new tests |

`src/product_hunter/cli.py` and `README.md` are shared with other waves and are
partial-staged: `cli.py` carries only the compare `reason` change, `README.md`
only the four corrections. Every other wave's hunks in both files stay
uncommitted.

## 4. The finding that mattered

A thresholds file could gate a run on a number the model under test produced.

`check_thresholds` treated every limit as a floor and accepted any metric name
some family produces, because `_known_metrics()` unions `family_metrics` over
every task, and that union mixes label-derived rates with counts such as
`group_count`, `predicted_pairs` and `independent_author_count`.

Reproduced before the fix: a labelled clustering case whose correct answer is
one group of three items, answered by a model that emitted three singleton
groups, with `tasks: { clustering: { min_group_count: 3 } }` reported
`passed: true` and exit 0. The model produced zero correct pairs.

The module already held the rule. `run.py` says "The metrics a fall is a
regression. Everything not named here is a count or a property of the label,
and a count never moves the verdict", and `compare_runs` honoured it. The
threshold gate did not.

The fix has two layers. The gate evaluates only the metrics in
`_HIGHER_IS_BETTER`, a label-derived rate where higher is better, so a count
cannot create a passing check, and a run whose limits are all elsewhere ends
as `passed is None`. The loader refuses a threshold key naming anything else,
so the misconfiguration is loud at load time rather than quiet at gate time.

## 5. Gate evidence

| Tree | Result |
|---|---|
| The working tree | 946 passed, 6 failed |
| The four eval test files | 190 passed |
| The commit tree, in a clean worktree | 821 passed, 0 failed |

The 6 failures are in `tests/test_research_contract.py` and
`tests/test_research_gate.py`. Those files, `src/product_hunter/research/` and
`config/research_contract.yaml` are untracked and in flight with the
research-contract wave, so they are in no commit and the commit tree does not
contain them. They belong to that wave.

## 6. The model-cost slip, recorded

The loop's model table names `step-5-preview-free`. No such model is
published. The nearest published id is `step-5-preview`, and it is metered:
the provider reports input 1 and output 2.7 per unit.

One complete harden pass ran on it before the price was checked. Three later
review attempts on metered models (`step-5-preview`, `deepseek-v4.1-flash`,
`minimax-m3`) each failed immediately on "Go usage limit exceeded" and
produced no work.

Only three ids are genuinely free: `longcat-2.5-preview-free`,
`minimax-m3.1-flash-free` and `beam-free`. After the slip was found, every
dispatch stopped overriding the model so the configured selector applies, and
the remaining nodes ran on a free id.

Check the price before substituting a model id. `opencode-go` is a free
*provider*, but not every model under it is free.

## 7. Open findings, unowned

Left open on purpose. Each changes a measurement or a contract this pass does
not own.

- `score_normalization` compares `domain` with exact equality while the other
  four fields strip whitespace.
- The dataset carries public Reddit handles in `author_id`.
- The `eval/thresholds.yaml` comment says "every threshold behind quote
  fidelity is blocked", which is wrong: `defaults.min_verbatim_rate` gates
  quote fidelity, and quote fidelity is label-free and never blocked.
- Bench paths are not contained, and the three writers truncate in place
  rather than writing atomically.
- Absolute paths are embedded in result files, so a degraded run is not
  byte-identical across two checkouts.
- `ThresholdReport` records no provenance for the thresholds that produced
  `passed`.
- `spec.outputs` resolves against a different root than every other path.
- The `not_comparable` and `improvement` reason strings were not exercised
  against a live command.

## 8. Still unverified

- No live `ph bench run` against a real model. Every input is a fixture.
- The real accuracy of the 30 dataset inputs was not checked, only their
  shape, their task distribution, and that they carry no credential.
- No type checker and no linter is configured in this repository.
- The A1 human-labelled acceptance gate is still blocked. `eval/datasets/ds-v1.json`
  has `expected: null` in all 30 cases, no code writes a label, and
  `ph bench labels` reports the count.

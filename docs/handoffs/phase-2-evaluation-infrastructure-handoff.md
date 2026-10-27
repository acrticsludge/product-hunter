# Handoff — Phase 2 evaluation infrastructure

**Date:** 2026-10-21
**Report:** `docs/reasonix/reports/phase-2-evaluation-infrastructure.md`
**Spec:** `docs/reasonix/specs/phase-2-evaluation-infrastructure.md`

---

## 1. What is where

The evaluation infrastructure is a package beside the Phase 1 harness.

```
src/product_hunter/evaluation.py   Phase 1: scores a filled review sheet. UNCHANGED.
src/product_hunter/eval/           Phase 2: the benchmark.
  cases.py                         the case, the dataset, the label status
  tasks.py                         the four metric primitives, the ten scorers
  run.py                           the run record, the thresholds, the comparison
  report.py                        the two renderings
eval/datasets/ds-v1.json           the seed dataset. 30 cases, 0 labels.
eval/thresholds.yaml               the starting thresholds
eval/README.md                     the four file formats
```

The dependency arrow points one way: `report.py` reads `run.py`, `run.py`
reads `tasks.py` and `cases.py`, `tasks.py` reads `cases.py`. `cases.py`
imports nothing from the package.

## 2. The commands

```
ph bench run --cases PATH --spec PATH --out PATH [--thresholds PATH]
ph bench compare --baseline PATH --candidate PATH --out PATH [--thresholds PATH]
ph bench report --run PATH --out PATH [--compare PATH]
ph bench labels --cases PATH [--out PATH]
```

`bench run` exits 1 when a threshold fails, `bench compare` exits 1 when the
verdict is `regression`, and both exit 0 when there was nothing to measure.
The JSON always carries the reason.

Every command prints one JSON object. The markdown goes to `--out`.

`--thresholds` defaults to `eval/thresholds.yaml` under the repository root.
A missing threshold file is not a failure; the run happens and the result
says in its warnings that no threshold was checked.

## 3. The four file formats

| File | Shape |
|---|---|
| dataset | one JSON object: `dataset_version`, `created_at`, `description`, `cases[]` |
| run spec | one JSON object: `run_id`, the identity fields, `dataset_version`, `outputs` |
| outputs | JSONL, one `{"case_id": ..., "output": {...}}` per line |
| thresholds | YAML: `version`, `tolerance`, `defaults`, `tasks` |

`eval/README.md` has the details and the field names.

## 4. The invariants

**A quality number exists only where a human label exists.** An unlabelled
case's metrics stay `None`, its task is blocked, the threshold check says
`blocked` with a reason, and the report prints `n/a`. `quote_fidelity` is the
one exemption, because it is checked against the source text.

**The result is deterministic.** No timestamp, no random value, no clock. The
same dataset and the same outputs produce byte-identical JSON, so a result
file can be committed and diffed. The time a run happened belongs to the run
manifest of whatever produced the outputs.

**The independent author count is computed, not supplied.** The model returns
the groups; the count of independent authors is a property of the groups.
§12 of the system spec says so, and the scorer follows it.

**Fail closed.** A malformed dataset, a malformed output, an unknown task, a
duplicate case id, an output for a case the dataset does not hold, a file over
the size cap, a threshold naming a metric no family produces: each raises an
error that names the file and the problem.

**An untrusted value never reaches an error message unbounded.** Every value
cut from a file or a model output goes through `brief_value`, which cannot
itself raise.

## 5. The gates

```
uv run pytest                                                   921 passed
uv run pytest tests/test_eval_*.py tests/test_cli_bench.py      185 passed
uv run pytest tests/test_evaluation.py                           46 passed
```

The `ph bench` commands, and the four formats, are covered by
`tests/test_cli_bench.py`.

## 6. What is left for a person

Fill the `expected` block of each case in `eval/datasets/ds-v1.json`.
`ph bench labels --cases eval/datasets/ds-v1.json` reports the count per
task. The A1 gate opens when a person has labelled enough of it.

Until then the benchmark measures quote fidelity and nothing else, and says
so in every report it writes.

## 7. For the next implementer

- A **runner** is the obvious next piece: something that reads the dataset,
  calls a model, and writes the outputs file. The infra consumes the outputs
  and does not run models.
- The **pair cap** (`MAX_GROUP_PAIRS` in `eval/tasks.py`) is a module
  constant. An operator with a legitimate case that needs more pairs has no
  recourse but to split the case. Raising it from the command line is a
  small change, if it is ever wanted.
- The **evaluation metrics** of the duplicate, independence and clustering
  families are verified against synthetic groups only. Nobody has validated
  them against a human-meaningful ground truth, because no label exists.

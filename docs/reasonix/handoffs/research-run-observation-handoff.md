# Handoff — research run observation

**Run:** loop-free, 2026-10-21
**Run id:** `robs-20261021-ea2759ad`
**Session:** `ses_ea2759ad1ffe6u6vLLuemyrJrq`
**Baseline:** branch `main`. HEAD was `351d962` at stage 0 and `39d81b9` when this file was written, because another agent is committing to this repository while the run is live. Working tree dirty throughout, with that agent's work in `src/`, `tests/`, `config/`, and untracked `eval/`, `src/product_hunter/research/`, `src/product_hunter/db/scheduler.py`.
**Risk:** declared medium, floored medium by `.loop/adapter.json`. SubQ Snap advised `low`; the floor wins.
**Radius:** eight documentation files under `docs/reasonix/`. No source, test, config, or database file was edited.

## 1. What was observed

Two calls to the `product_hunter` tool, in this order, with their outputs verbatim.

Call 1, `action=list`:

```text
3 research run(s)
RH-20261021-0002  shift handover paperwork for nurses  CANCELLED  PLAN  0%  last 2026-10-21T08:18:42.187613+00:00
RH-20261021-0001  manual inventory reconciliation for small retailers  CANCELLED  PLAN  0%  last 2026-10-21T08:18:41.344451+00:00
RH-20261021-0003  Track the billing pain  QUEUED  PLAN  0%  last 2026-10-21T07:49:20.417250+00:00
```

Call 2, `action=status` with `research_id=RH-20261021-0003`:

```text
RH-20261021-0003  Track the billing pain
  state: QUEUED   stage: PLAN   progress: 0%   resumes: 0
  worker: not running
  updated: 2026-10-21T07:49:20.417250+00:00
```

Two notes on these blocks. The tool returned 359 bytes for the first and 164 for the second; the fences add one trailing newline each, so a byte comparison against a raw capture reads 360 and 165. The review node re-ran both calls and confirmed every other byte matches, including the double spaces and the `+00:00` offsets.

The database file was byte-identical before and after both calls, SHA-256 `AD967A8E343B4A1F2F87084F7ED68838A16CE2ED8198DDC06B34342EC50EE72E`, mtime `2026-10-21 08:18:42Z`. The read path still created and removed `-wal` and `-shm` sidecars next to the file. That is this run's own finding 3, and it is why the file's own bytes were unchanged while the directory was not.

## 2. What the state means

`QUEUED` at stage `PLAN` with 0% progress is the state a run is created in, not a stalled one. `create_run()` writes exactly that row (`src/product_hunter/registry.py:526-536`), and `tests/test_registry.py:117-145` asserts all four fields.

`worker: not running` means no job for that run is `leased` with an unexpired lease (`src/product_hunter/registry.py:251-262`). The worker entry point is `python -m product_hunter.runtime`, and it loads its handlers from `PH_WORKER_HANDLERS` as `module:attribute` (`src/product_hunter/runtime/__main__.py:41`). The worker dispatches on the stage of the job, not on the run's current stage: `self.handlers.get(job.stage)` (`src/product_hunter/runtime/worker.py:160`), and the job a new run creates is a `DISCOVERY` job (`src/product_hunter/registry.py:567`). With no handler registered for it the worker starts idle and claims nothing, which `README.md:194-198` says outright. So RH-20261021-0003 will stay queued until a `DISCOVERY` handler is registered. That is the single most useful thing to know about this run, and it is not a defect in the read path.

The other two runs, RH-20261021-0001 and RH-20261021-0002, are `CANCELLED` at the same stage and 0% progress, so they never started either.

## 3. Repo map

| Path | What it is |
|---|---|
| `.opencode/plugins/product-hunter.ts` | The tool. Registers `product_hunter` with nine actions and spawns the registry with `shell: false` |
| `src/product_hunter/registry.py` | The state layer. `ACTIONS`, `execute()`, the run view, and the text rendering |
| `src/product_hunter/db/connection.py` | `connect(db_path, read_only=False)`; the read path does not use `read_only` |
| `src/product_hunter/runtime/` | The supervised worker: `python -m product_hunter.runtime` |
| `src/product_hunter/research/` and `src/product_hunter/eval/` | Research contract, provenance and budget; benchmark cases, scorers, thresholds |
| `db/product_hunter.db` | SQLite file, table `research_runs` plus `research_events`, `research_checkpoints`, `research_artifacts`, `jobs`, `stage_attempts` |
| `tests/` | 35 test modules plus `conftest.py`, one per module, `def test_...` style, plain asserts |
| `docs/reasonix/` | `specs/`, `plans/`, `prompts/`, `handoffs/`, `reports/`, `decisions/` |

Entry points: `ph` console script (`product_hunter.cli:main`), `python -m product_hunter.registry`, `python -m product_hunter.runtime`. Code gate: `uv run pytest`.

## 4. Stage results

| Stage | Result |
|---|---|
| 0 ORIENT | Radius declared, eight docs files. SubQ: risk `low` (P = 0.95, conf 0.90), lane tooling/observability (P = 0.94, conf 0.87). Floor is medium, so the run is medium |
| 1 DEFINE | `docs/reasonix/specs/research-run-observation.md` |
| 2 ARCHITECT | Skipped, no architecture decision to take |
| 3 PLAN | `docs/reasonix/plans/research-run-observation.md` |
| 4 IMPLEMENT | No code change required. `list` and `status` verified as SELECT-only paths, database byte-identical after both calls |
| 5 VERIFY | `uv run pytest`: 875 passed, exit 0. `tests/test_registry.py` alone: 50 passed, exit 0. Zero skips. See section 6 for what these numbers do and do not prove |
| 6 HARDEN | Four findings, all pre-existing, none in the radius. Re-harden confirmed attribution |
| 7 REVIEW | First pass: ship with fixes. The three Must-fix items and one restatement were applied, the re-review returned ship with fixes with two further items, and those are applied too |
| 8 PRODUCTIONIZE | This handoff and the memory entry |
| 9 SHIP | Pending when this file was written. The report file records the outcome |
| 10 REPORT | Pending when this file was written. The report file records the outcome |

## 5. Open findings from stage 6

All four predate this run and sit in `src/product_hunter/`, outside the declared radius. They are recorded here rather than fixed, because fixing them means editing files another agent is actively changing, and the request behind this run was a read.

| # | Severity | Where | The gap | Smallest fix |
|---|---|---|---|---|
| 1 | medium | `registry.py:1197` | `conn = connect(path)` sits outside the `try`, so a DB open failure raises an uncaught `sqlite3.Error` or `OSError` and prints a raw traceback instead of the structured error every other failure path returns | Wrap the `connect()` call in its own `try/except (sqlite3.Error, OSError)` and return `CommandResult(ok=False, code="database_error")`. Moving the call into the existing `try` is not enough on its own: the `finally: conn.close()` at `registry.py:1207-1208` then raises `UnboundLocalError` on a failed connect and masks the real error. Handle the open separately, or set `conn = None` before the `try` and guard the close with `if conn is not None` |
| 2 | medium, re-harden says low | `registry.py:959-988`, `1036`, `1089` | A successful `list` or `status` writes no event and no log line, so an operator cannot tell that a run was queried | Emit an access log line for reads, to a log file rather than the database |
| 3 | low | `db/connection.py:9,21,30` | The read path opens SQLite read-write, and a WAL database needs a `-shm` index next to the file. The read therefore cannot be sidecar-free, and it fails outright on read-only media | Restated after the review: `read_only=True` alone does not solve it. A read-only open still wants a `-shm` it cannot create on a read-only directory, and the file would have to leave WAL mode, or the open would need `immutable=1`, which is unsafe while a writer holds the file. Decide first whether reads need to work on read-only media at all |
| 4 | low | `registry.py:842`, `861` | The renderer interpolates the `objective` column raw, with no escaping for the render target | Not a live injection path today. `create_run()` stores `" ".join(request.objective.split())` (`registry.py:521`), which collapses newlines, and the only other writer of that column writes a fixed string (`db/store.py:656`). The raw text survives in `original_request`, which these two actions do not render, and `last_error` is never rendered either. So this is a hardening note about the renderer: it is safe only as long as every writer normalises first |

The first two are worth a separate run. The third and fourth are cheap once that run is open.

## 6. Gate evidence

Full suite, final run:

```text
875 passed in 11.47s
exit 0
```

`tests/test_registry.py` on its own, run at the same time:

```text
..................................................                       [100%]
50 passed in 1.81s
exit 0
```

Both numbers are point-in-time and neither reproduces on the tree as it stands now. The full-suite count of 875 was measured while the other agent was adding files, and the tree now collects 899 or more items. The registry count of 50 is worse: at the time of writing the file collects 53 items and is byte-identical to HEAD, so the 50 came from a transient version of that file that the other agent edited and then reverted. Treat 50 as a measurement of a file in motion, not a property of commit `39d81b9`.

Earlier runs of the same command in the same session failed:

```text
FAILED tests/test_cli_bench.py::test_bench_run_refuses_a_threshold_path_that_is_not_a_file
FAILED tests/test_cli_bench.py::test_bench_compare_refuses_a_threshold_path_that_is_not_a_file
FAILED tests/test_eval_cases.py::test_a_deeply_nested_dataset_is_refused
FAILED tests/test_eval_run.py::test_read_outputs_refuses_a_deeply_nested_line
4 failed, 861 passed in 11.62s
```

and then, on the next run, the same two `test_cli_bench.py` failures with `2 failed, 872 passed in 12.02s`. The two bench failures were `assert 0 == 1` at `tests/test_cli_bench.py:244` and `:321`, where a directory path was accepted as a threshold instead of being refused. The two eval failures were `RecursionError` from `json.loads` in `src/product_hunter/eval/cases.py:159` and `src/product_hunter/eval/run.py:614`, where a deeply nested input was expected to raise `EvalError`. All four files are untracked additions belonging to the other agent, who repaired them during the session, and the line numbers above are the positions at the moment of the failure rather than now, because all four files have been edited since. The final run was green.

Zero tests were skipped. The `network` marker in `pyproject.toml` is applied to no test.

The visual gate did not run. No UI file changed and the adapter sets `visualRequired: never`.

## 7. SubQ Snap

Stage 0 was the only significant-stage hook that applied. It answered two questions, risk tier and specialization lane, both as `score` with a legend.

```text
[calls 25 attempts, 4 with a 200] [tokens 732 in / 0 out] [errors 21] [cache_hits 0] [model subq-snap-preview] [schema systemone-v1]
```

The 21 errors were 422 schema rejections while the question shape was being discovered. The working shape is `questions: {"<key>": {"type": "noul|score", "instructions": "...", "criteria": ["low", "medium", "high"]}}`, where `score` needs `criteria` as an array of legend labels and `noul` needs neither. The `choice` type was never pinned down; its `criteria` object rejected every key that was tried.

## 8. Not verified

- Whether the chat surface renders tool output as HTML, which decides how finding 4 should be read.
- Whether `connect()` actually raises in production. The path is unguarded, but the scenario was not reproduced.
- Whether the other agent's concurrent commits changed the read path after the last read. `registry.py` was clean against HEAD `39d81b9` at the re-harden.
- Whether `chat_id` value `ses_smoke` in the sample rows is sensitive in this deployment.
- The full-repo audit at stage 8 was scoped to the diff subset, which is documentation only, so the wider repository was not audited.
- The stage 5 and stage 6 node reports exist only in the session transcript. The four findings and the two failing runs are known to later stages only through the summary in this file.

## 9. State of the index

The index moves under this run, because the other agent stages and commits while the run is live. It held 14 paths when this file was first written, four source files and one test file among them, and a bare `git commit` at that moment would have swept them into this run's documentation commit. The other agent committed them as `ec998ad` at 14:47, and the index was clean afterwards. It has since moved again, to `2580f4d`.

So stage 9 must not trust a frozen list. It should read the index at the moment of the commit:

```text
git diff --cached --name-only
```

and commit this run's eight paths by name, whatever that command returns.

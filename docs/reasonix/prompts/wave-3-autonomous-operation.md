# Dispatch: wave 3 autonomous operation, implement

Node: `loop-free-implement-1`. Stage 4.

Worktree: `C:/Anubhav/Web Dev Projects/product-hunter-w3a`.
Branch: `wave3/autonomous-operation`.

## Read first, in this order

1. `docs/reasonix/specs/wave-3-autonomous-operation.md` (the spec)
2. `docs/reasonix/plans/wave-3-autonomous-operation.md` (the build order and the
   API contract)
3. `src/product_hunter/db/jobs.py`, `src/product_hunter/db/scheduler.py`,
   `src/product_hunter/db/checkpoints.py`, `src/product_hunter/db/outbox.py`,
   `src/product_hunter/runtime/worker.py`, `src/product_hunter/runtime/lock.py`,
   `src/product_hunter/runtime/events.py`,
   `src/product_hunter/research/budget.py`,
   `src/product_hunter/collection/orchestrator.py`
4. `tests/test_db_jobs.py`, `tests/test_db_scheduler.py`,
   `tests/test_runtime_worker.py`, `tests/test_db_crash_recovery.py`

The plan is the contract. Follow it. When the plan and the code disagree, the
code wins for the existing behaviour and the plan wins for the new behaviour.

## What to build

Everything in the plan, in the plan's build order. Nine source files under
`src/product_hunter/autonomy/`, two configuration files, one migration, three
changed existing modules, one CLI command group, four test files.

## Rules that are not negotiable

- **Reuse before you write.** The queue, the scheduler, the checkpoints, the
  outbox, the runtime worker, the lock, the event sink, the research budget and
  the collection orchestrator already exist. Do not write a second one. The
  plan names the module for every reuse.
- **Do not weaken a test to make it pass.** `tests/test_db_scheduler.py` and
  `tests/test_db_jobs.py` must keep passing untouched. If a change you made
  breaks one of them, the change is wrong, not the test.
- **No new dependency.** `pydantic`, `yaml`, `httpx` and the standard library.
- **No secret, no absolute path from this machine, no network call** in any
  file you write. The tests must run offline.
- **No `assert` in the library.** Raise a named error. `assert` is for the
  tests.
- **Type annotations on every public function**, and a docstring that says why
  the code is the way it is, not what the line does.
- **Failure text is masked** before it reaches a database row. Reuse
  `runtime.worker._safe_error`; do not write another masker.
- **Every new table gets an index on the column that is queried.**

## The prose rule

The files you write contain docstrings, comments and a migration header, and
those are prose. Load the `anti-ai-slop-writing` skill before you write them
and follow it: no banned vocabulary, no rule-of-three defaults, no em dash
runs, no uniform sentence lengths, no "not just X but Y". The house voice in
this repository is plain and specific; read `src/product_hunter/db/jobs.py` and
copy that register.

## The gate

`uv run pytest` must be green before you report. Run it. Do not report a
passing gate you did not observe.

Report back: the files you created and changed, the gate line verbatim, what
you could not build and why, and the one thing a reviewer should look at
hardest.

# Prompt — implement the Phase 2 worker runtime

Node: `loop-free-implement-1`
Stage: 4 IMPLEMENT
Risk: medium
Blast radius: `src/product_hunter/runtime/**`, `tests/test_runtime_worker.py`,
`tests/test_runtime_lock.py`. `README.md` and `docs/resonix/**` are edited by
the orchestrator, not by you.

## Read first

1. `docs/resonix/specs/phase-2-worker-runtime.md` — the contracts. This is
   the contract. Follow it exactly; where it names a signature, use that
   signature.
2. `docs/resonix/plans/phase-2-worker-runtime.md` — the task order and the
   determinism rules for the tests.
3. `docs/resonix/handoff/phase-2-worker-runtime-handoff.md` — the database
   API you build on, the schema facts, the house style, and what already
   exists that you must reuse instead of rewriting.
4. `src/product_hunter/db/jobs.py`, `src/product_hunter/db/connection.py`,
   `src/product_hunter/db/worker.py` — the code you call.
5. `tests/test_db_worker.py` — the test style to match.

## What to build

The six modules of the runtime package:

- `src/product_hunter/runtime/identity.py` — `WorkerIdentity`.
- `src/product_hunter/runtime/events.py` — `WorkerEvent`, `EventSink`,
  `JsonlEventSink`.
- `src/product_hunter/runtime/lock.py` — `SingleInstanceLock`.
- `src/product_hunter/runtime/worker.py` — `RetryPolicy`, `WorkerRuntime`,
  `install_shutdown_handlers`.
- `src/product_hunter/runtime/__main__.py` — the process entry point.
- `src/product_hunter/runtime/__init__.py` — the public exports.

And the two test files:

- `tests/test_runtime_worker.py` — the runtime cases.
- `tests/test_runtime_lock.py` — the lock cases.

## Rules

- The database layer is read-only. Do not edit `src/product_hunter/db/**`,
  `db/migrations/**`, or any existing test.
- Standard library only. No new dependency, no `pip install`.
- Reuse `product_hunter.db.jobs`. Do not re-implement claiming, leases,
  fencing, retries or recovery.
- The heartbeat thread must use its own connection. A `sqlite3.Connection`
  belongs to the thread that created it.
- Stop the heartbeat before `complete_job` or `fail_job`, so a renewal cannot
  race the completion and report a lease loss that did not happen.
- On `LeaseLost`, write nothing else for that job.
- With no handlers registered, claim nothing.
- Every test must be deterministic: no wall clock sleeps, no timing
  assumptions. Use a file backed database in `tmp_path` when a test needs the
  heartbeat, and `Store.open(":memory:")` with `heartbeat_seconds=0` when it
  does not.
- A new branch, a loop, a thread or a parser needs a test. Every case in the
  spec matrix needs a test.
- Match the house style in the handoff: module docstring, `from __future__
  import annotations`, type hints, `X | None`.
- Comments explain why, in one line. No comment that restates the code.
- English prose in docstrings and comments. No emoji, no exclamation marks.

## Check your own work before you report

Run `uv run pytest` and report the result. The whole suite must be green,
including the tests that existed before you started.

## Report back

- The files you created, with the line count of each.
- The verbatim tail of the `uv run pytest` output.
- Every place where you deviated from the spec, and why.
- Anything you could not build safely, and what is missing.

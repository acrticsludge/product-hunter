# Implement node prompt — Phase 2 crash-recovery hardening

You are implementing one node of a loop run on the `product-hunter` repo at
`C:\Anubhav\Web Dev Projects\product-hunter`.

Read these first, in this order:

1. `docs/resonix/specs/phase2-crash-recovery.md` — the spec: interruption
   model, invariants V1 to V12, defects D1 to D3, fixes F1 to F3.
2. `docs/resonix/plans/phase2-crash-recovery.md` — the plan: exact test
   names, exact fix shapes, the order of work.
3. `src/product_hunter/db/` — `store.py`, `checkpoints.py`, `jobs.py`,
   `outbox.py`, `worker.py`, `connection.py`.
4. `tests/test_db_store.py` — the existing store tests and the fixture
   builders you will move.

## Goal

Prove and harden crash/restart behaviour of the Phase 2 DB and worker layer.
The claim to support:

```text
WORK -> INTERRUPTION -> PERSISTED STATE -> RECOVERY -> SAFE RESUME
```

Do not redesign the architecture. Do not add a product feature. Do not add a
schema migration. Do not change the worker or handler contract.

## Files you may touch

- `tests/conftest.py` (new)
- `tests/test_db_crash_recovery.py` (new)
- `tests/test_db_store.py` (fixture builders move to `conftest.py`; no
  assertion changes)
- `src/product_hunter/db/store.py` (fixes F1 and F3)
- `src/product_hunter/db/checkpoints.py` (fix F2)

Anything else is out of radius. If you believe a change is needed elsewhere,
say so in your report instead of making it.

## Hard rules

- The gate is `uv run pytest` from the repo root. It must be green.
- The 361 tests that exist today must still pass, unchanged. You may move
  fixture helpers between files; you may not weaken, skip, or delete an
  assertion.
- Never weaken a test to make it pass. A failing test is the signal.
- No new dependencies. Standard library only.
- `import subprocess` and `import os` are fine for the crash helper.
- Python 3.14, pytest 9, sqlite3 from the standard library.
- Keep the code short. Reuse before you write: the fixture builders already
  exist in `tests/test_db_store.py`, and the crash helper is one small
  function.
- A new non-trivial branch needs a test. Every fix below ships with one.

## Work order

Do it in this order and stop to check between steps.

### Step 1: move the shared fixtures

Move `_fixture_fetch`, `_candidate`, `_reject`, `_call`, `_write_jsonl`, and
`_build_run` from `tests/test_db_store.py` into `tests/conftest.py`, verbatim.
Import back into `test_db_store.py` only the names that file still uses.

Run `uv run pytest`. It must still report 361 passed.

### Step 2: the crash helper

Add to `tests/conftest.py`:

```python
def crash_child(db_path: Path, body: str) -> subprocess.CompletedProcess:
    """Run `body` in a child process against `db_path`; the child kills itself.

    `body` is Python source. It receives the database path in sys.argv[1] and
    is expected to end in os._exit(9) so nothing is cleaned up.
    """
```

The child must call `os._exit(9)`. A clean exit would flush and close the
connection, which proves nothing about a killed process.

### Step 3: the twelve tests

Write `tests/test_db_crash_recovery.py` with one test per invariant, exactly
as the plan describes. Each test's docstring names the invariant it holds.

Run `uv run pytest tests/test_db_crash_recovery.py -v` before applying any
fix. Four tests are expected to fail at this point, and only these four:

- the corrupt-checkpoint test (needs F2)
- the outbox-lands-with-evidence test's second half (needs F2 for the corrupt
  case, and the first half must already pass)
- the replay-creates-no-duplicates test (must already pass; it proves the
  deterministic-id defence)
- the truncated-run-directory test (needs F1)

Any other failure means the harness is wrong. Fix the harness first.

### Step 4: the fixes

Apply F1, F2 and F3 from the spec, in the shape the plan gives.

### Step 5: the gate

`uv run pytest` must be green. Quote the exact final line in your report.

## Report back

Give me:

1. The final `uv run pytest` summary line, verbatim.
2. The list of test names in `tests/test_db_crash_recovery.py`.
3. Each fix, with the file and the line count you changed.
4. Anything you found that the spec did not predict, and anything in the spec
   you could not deliver.
5. What you skipped and what you did not check.

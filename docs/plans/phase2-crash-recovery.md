# Phase 2 crash-recovery implementation plan

Spec: `docs/reasonix/specs/phase2-crash-recovery.md`
Gate: `uv run pytest`
Baseline before the change: 361 passed.

## Step 1 — shared fixtures (`tests/conftest.py`, `tests/test_db_store.py`)

Move the run-directory builder out of `tests/test_db_store.py` and into
`tests/conftest.py`, then import it in both places. No assertion changes, no
behaviour changes.

`tests/test_db_store.py` currently defines `_fixture_fetch`, `_candidate`,
`_reject`, `_call`, `_write_jsonl`, and `_build_run`. They move verbatim to
`conftest.py` as module-level functions. `test_db_store.py` imports them:

```python
from conftest import _build_run, _fixture_fetch, _write_jsonl
```

`test_db_store.py` keeps its own `_candidate`, `_reject`, and `_call` only if
they are used by its own tests. Check before moving: `_fixture_fetch` and
`_write_jsonl` and `_build_run` are used by its tests; `_candidate`, `_reject`
and `_call` are used only inside `_build_run`. Move all six, import back only
the three that are still referenced.

Why `conftest.py` and not a copy: the crash suite needs the same run
directory, and a copied builder is a builder that drifts.

## Step 2 — the crash helpers (`tests/conftest.py`)

Add to `conftest.py`:

```python
def crash_child(db_path: Path, body: str) -> subprocess.CompletedProcess:
    """Run `body` in a child process against `db_path`; the child kills itself.

    `body` is Python source. It receives the database path in sys.argv[1] and
    is expected to end in os._exit(9) so nothing is cleaned up.
    """
```

The child program:

```python
import os, sys
<imports the caller needs>
<body>
```

`os._exit(9)` is the point of the helper. A normal `sys.exit` or an exception
would let Python flush and close the connection, which is a clean shutdown and
proves nothing about a killed process.

The helper returns the `CompletedProcess` so the test can assert the child
died the way it meant to (`returncode == 9`).

Also add a helper that opens a store on a file path:

```python
def open_store(db_path: Path) -> Store:
    return Store.open(db_path)
```

`Store.open` already creates the parent directory and runs the migration, so
no fixture is needed for that.

## Step 3 — the crash-recovery suite (`tests/test_db_crash_recovery.py`)

Twelve tests, one per invariant. Each names the invariant in its docstring so
a failure says which claim broke.

### V1 — a committed transaction survives a process kill

Child: open the file database, `BEGIN IMMEDIATE`, insert, `COMMIT`, then
`os._exit(9)`. Parent: reopen, assert the row is there and
`PRAGMA integrity_check` is `ok`.

### V2 — an open transaction leaves nothing behind

Child: open, `BEGIN IMMEDIATE`, insert, no commit, `os._exit(9)`. Parent:
reopen, assert the table is empty and the integrity check is `ok`.

The two tests together are the only honest evidence for "a killed process
cannot tear a transaction".

### V3 — a job whose worker vanished is recoverable

Child: open, enqueue a job, claim it as `worker-dead`, then `os._exit(9)`
before completing. Parent: reopen, assert the job is `leased` with
`worker-dead` and `lease_epoch == 1`. Expire the lease by writing
`lease_expires_at` into the past, then run a `Worker` and assert the job
reaches `succeeded` and that `stage_attempts` holds `lease_lost` then
`succeeded`.

### V4 — a worker whose lease expired cannot write

Claim a job, expire the lease, then open a `JobContext` for the old worker and
assert `context.transaction()` raises `LeaseLost` and writes nothing.

### V5 — a superseded epoch cannot complete, fail, renew, or write

Claim as `worker-a`, expire the lease, reclaim as `worker-b` (epoch 2), then
assert `LeaseLost` for `complete_job`, `fail_job`, `renew_lease`, and
`JobContext.transaction()` at epoch 1.

### V6 — two workers racing for one job produce one claim

Open two `Store` connections on the same file. Claim from both. Assert exactly
one claim returned a job and the other returned `None`, and that the
`jobs` table holds one row.

### V7 — a rollback inside a fenced transaction discards every write

Handler opens `context.transaction()`, writes two rows, then raises. Assert
`LeaseLost` is not raised, the job is `queued` again, and neither row exists.

### V8 — a checkpoint survives a restart

Save a checkpoint, close the store, reopen the same file, assert the
checkpoint is readable with the same state and version.

### V9 — a corrupt checkpoint does not stop recovery (fix F2)

Save version 1 and version 2. Corrupt version 2's `state_json` with a direct
UPDATE. Assert `load_checkpoint` raises `CheckpointError`, and that
`load_valid_checkpoint` returns version 1. Assert `list_checkpoints` with
`skip_invalid=True` returns version 1 and without the flag raises.

### V10 — an outbox task lands with the evidence, or with neither

Two halves. First: enqueue the evidence and the projection task inside one
`JobContext.transaction()`, kill the child before the commit, reopen, assert
neither the evidence nor the projection task exists. Second: the same
transaction committed, then the worker is killed before `complete_job`;
reopen, assert the projection task is still `pending` and the evidence is
there, and that running the outbox completes it.

### V11 — a replayed import creates no duplicate rows

Import the run directory, then import it again, as a separate connection
after a close, the way a restarted worker would. Assert the row counts in
`documents`, `evidence`, `pain_points`, `research_events`, `research_artifacts`
and `projection_tasks` are unchanged, and that the pain point id is the same
one.

### V12 — a truncated run directory is refused (fix F1)

Build the run directory, then drop the last document from
`documents.jsonl` while the manifest still declares the original count.
Assert `import_run` raises `StoreError` and that no document was imported.

## Step 4 — the fixes

### F1 in `src/product_hunter/db/store.py`

`import_run` already reads the manifest. After reading the artifacts and
before the import transaction, compare the counts.

The manifest values may be missing, in which case the check is skipped for
that artifact. The manifest values may be non-integer, in which case the
artifact is skipped too rather than raising on a malformed manifest. Only a
present, integer count that disagrees with the file is a refusal.

Raise `StoreError` with a message naming the artifact, the declared count and
the observed count.

The check belongs before `BEGIN IMMEDIATE`, so a refused import never opens a
transaction. That also keeps the failure cheap.

### F2 in `src/product_hunter/db/checkpoints.py`

```python
def load_valid_checkpoint(
    conn, *, run_id, stage, step=None, version=None
) -> Checkpoint | None:
    """Return the newest checkpoint whose state parses.

    A row can be edited outside the worker. Recovery must not stop at a row
    it cannot read: the previous version is still good.
    """
```

Implementation: reuse the row filter that `load_checkpoint` already builds,
fetch the matching rows newest first, and return the first that converts.
`_row_to_checkpoint` raises `CheckpointError` for an unreadable state, so the
loop catches it and moves to the next row.

`list_checkpoints` gains `skip_invalid: bool = False`. With the flag set, rows
that fail to convert are left out.

### F3 in `src/product_hunter/db/store.py`

`_enqueue_projection` calls `outbox.enqueue_projection` and drops its own
INSERT. The existing pre-check for the idempotency key stays, because
`import_run` counts a task as new only when the row did not exist before.

`outbox.enqueue_projection` raises `OutboxConflict` when the key exists with
different content. That is a better failure than the current silent return,
but it must not fire on a normal replay: the payload is `{"run_id": run_id}`
and the entity fields are the run id in both calls, so the comparison holds.

## Step 5 — verify

```powershell
uv run pytest
```

Green means the pre-existing 361 tests still pass and the new ones pass too.

Then run the new file alone with `-v` so the report can quote the names.

## Step 6 — report

`docs/reasonix/reports/phase2-crash-recovery.md` records, per invariant, the
test name and the result, plus the three fixes and the residual risk.

## Order of work

1. `conftest.py` and the `test_db_store.py` import change. Run the suite; it
   must still be 361 passed.
2. The crash suite, tests only, with F1 to F3 not yet applied. The V9, V10
   second half, V11, and V12 tests are expected to fail. A failure that is not
   one of those four means the harness is wrong; fix the harness before going
   further.
3. F1, F2, F3.
4. Full suite green.
5. Report.

This order matters: it separates "the harness works" from "the fix works". A
test that fails for a reason nobody expected is a bug in the test.

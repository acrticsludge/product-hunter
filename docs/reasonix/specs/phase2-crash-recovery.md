# Phase 2 crash-recovery specification

Status: accepted for implementation
Scope: the Phase 2 SQLite core (`src/product_hunter/db/`) and its tests
Non-goal: any new product surface. No new stage, no new CLI command, no new table.

## 1. Why this run exists

Phase 2 shipped the authoritative store, the fenced job queue, the outbox, and
the checkpoint writer. Every test in the repo runs against `:memory:`.

That single fact hides the whole failure surface this run targets. An in-memory
database disappears with the process, so no existing test has ever observed a
worker dying, a lease expiring, a transaction torn in half, or a queue row
surviving a restart. The fencing code looks right on paper; nothing has
challenged it.

This run writes the missing tests and fixes whatever they break. The claim to
prove, end to end:

```text
WORK -> INTERRUPTION -> PERSISTED STATE -> RECOVERY -> SAFE RESUME
```

## 2. The interruption model

A crash means one of four things. Each is simulated, not asserted.

| # | Interruption | How the test triggers it |
|---|---|---|
| I-A | Process killed mid-transaction | `subprocess` runs a child that opens the DB, begins a transaction, writes, then calls `os._exit(9)`. The parent never gets a chance to clean up. |
| I-B | Worker vanishes holding a lease | A child process claims a job and is killed before completing it. The lease row stays `leased`. |
| I-C | Handler raises after writing | The handler commits its own fenced transaction, then raises. The job is never completed. |
| I-D | Downstream handler wrote, then the outbox task was not closed | The projection handler performs its side effect, then the task is abandoned with an expired lease. |

### 2.1 What this run does not claim

`PRAGMA synchronous = NORMAL` is set in `db/connection.py`. In WAL mode that
means SQLite does not fsync on every commit, so a host power loss can discard
recently committed transactions.

**No test in this run proves power-loss durability, and no report may claim it.**
What the tests prove is process-crash durability: a killed process cannot tear
a committed transaction, and cannot leave a partial one visible.

## 3. The persistence model the tests must respect

`db/connection.py` opens every connection with `isolation_level=None`, so
SQLite runs in autocommit unless a statement issues an explicit `BEGIN`. The
project pragmas are `foreign_keys=ON`, `busy_timeout=5000`,
`synchronous=NORMAL`, and `journal_mode=WAL` for file-backed databases.

Three consequences shape every test:

1. A file-backed database is required for anything that mentions restart. WAL
   and the `-wal` sidecar file only exist on disk.
2. A test that wants a transaction must issue `BEGIN IMMEDIATE` itself, the
   way `JobContext.transaction()` and `Store.import_run()` do.
3. Two connections to the same file are two writers. `BEGIN IMMEDIATE` plus
   `busy_timeout` serializes them; that is the single-writer model the spec
   asks for.

## 4. Invariants to prove

Each row is a test name, not a wish.

| # | Invariant | Where it must hold |
|---|---|---|
| V1 | A transaction committed before a process kill is visible after restart | `db/connection.py`, WAL |
| V2 | A transaction open during a process kill leaves no trace | SQLite atomicity |
| V3 | A job whose worker vanished is recoverable, never half-applied | `db/jobs.py` |
| V4 | A worker whose lease expired cannot write anything | `JobContext.transaction()` |
| V5 | A worker holding a superseded epoch cannot complete, fail, renew, or write | `complete_job`, `fail_job`, `renew_lease` |
| V6 | Two workers racing for one job produce exactly one claim | `claim_job` |
| V7 | A rollback inside a fenced transaction discards every write in it | `JobContext.transaction()` |
| V8 | A checkpoint written before a crash is readable after restart | `db/checkpoints.py` |
| V9 | A corrupt checkpoint does not stop recovery | `db/checkpoints.py` (defect D2) |
| V10 | An outbox task is committed with the evidence it projects, or with neither | `db/store.py` |
| V11 | Replaying an interrupted import creates no duplicate authoritative rows | `db/store.py` |
| V12 | A truncated run directory is refused rather than imported | `db/store.py` (defect D1) |

## 5. Defects found by inspection

Three, each reproduced by a probe against the current code before any change.

### D1 — a truncated run directory imports as authoritative

`Store.import_run` reads `documents.jsonl`, `candidates.jsonl`, `calls.jsonl` and
`rejects.jsonl`, then records each file's SHA-256 in `research_artifacts`. The
manifest already declares the number of records each artifact holds
(`documents`, `candidates`, `calls`, `rejected`), and the number of accepted
candidates inside `candidates.jsonl` (`accepted`). Nothing compares the two
numbers.

A file truncated at a line boundary parses cleanly. The import therefore
succeeds, and `research_artifacts` records the hash of the *short* file as
authoritative. The hash check that guards a later replay cannot catch this: a
truncated file has a perfectly valid hash, and it is the truncated hash that
was recorded.

The extraction CLI writes the manifest last, so this needs a specific failure
to trigger. A run directory written by another producer, a truncated copy, or a
filesystem that lost the tail of a file all reach the same state. Spec 41.9
step 4 asks recovery to "reconcile partial outputs using hashes and
idempotency keys"; the hash cannot answer "is this file complete", so the
manifest count has to.

### D2 — one corrupt checkpoint row stops the whole recovery

`checkpoints._row_to_checkpoint` parses `state_json` and raises
`CheckpointError` when the JSON is invalid. `load_checkpoint` and
`list_checkpoints` both go through it.

So a single unreadable row makes `load_checkpoint` raise, and makes
`list_checkpoints` raise for the entire run. There is no way to ask for the
previous good version, which is exactly what a resume needs: the latest
checkpoint is the one most likely to be the damaged one, because it was
written last.

SQLite cannot produce a half-written row, so this needs an external writer or
a bug to trigger. The consequence is severe enough to justify the fix: a run
that cannot be resumed and cannot be listed.

### D3 — the outbox has two writers

`Store._enqueue_projection` inserts into `projection_tasks` with its own SQL
instead of calling `outbox.enqueue_projection`. The two paths agree today
only because both derive the task id from the same key with the same hash
basis.

A crash-recovery bug lives in that disagreement. If the two ever drift, a
replayed import creates a second projection task for the same run, and the
downstream projection runs twice. One writer removes the class of bug.

## 6. Non-defects, recorded so nobody reopens them

These were checked and hold. They need tests, not changes.

- **The queue is at-least-once.** A crash between the handler's commit and
  `complete_job` re-runs the handler. That is inherent to a two-transaction
  design and changing it would mean changing the handler contract, which this
  run forbids. The defence against duplicate authoritative mutations lives one
  level down, in the deterministic ids of `Store`: replaying an import produces
  the same ids and inserts no second row. V11 proves that end to end.
- **A stale worker can still run raw SQL.** `JobContext.transaction()` fences
  writes that go through it. A handler that writes with `conn.execute` outside
  the context is unfenced by construction; the framework cannot close that
  without owning every write.
- **The lease clock is the caller's.** `complete_job`, `fail_job`, and
  `renew_lease` compare `lease_expires_at` against the `now` they are given,
  while `JobContext.assert_lease` compares it against the real clock. No
  production caller passes `now`, so the two agree. Leaving it alone keeps the
  tests deterministic.

## 7. Fix design

Three changes, all inside the declared radius.

### F1 — refuse a partial run artifact (D1)

In `Store.import_run`, compare each artifact's record count against the
manifest count that describes it, and raise `StoreError` on a mismatch. The
manifest is optional in practice: a run directory whose manifest declares no
counts skips the check, which keeps the existing store fixtures valid.

The mapping comes from `cli.py`:

| Manifest key | Artifact |
|---|---|
| `documents` | `documents.jsonl` |
| `candidates` | `candidates.jsonl` |
| `calls` | `calls.jsonl` |
| `accepted` | the accepted-candidate count in `candidates.jsonl` |
| `rejected` | `rejects.jsonl` |

`pain_points.jsonl` is a completeness sidecar the import never reads, so it is
not part of the check; a divergent sidecar is caught later by the artifact hash
comparison on replay.

The check runs before the import transaction opens, so a refused import never
starts a transaction and the store is left exactly as it was.

### F2 — recover past a corrupt checkpoint (D2)

Add `checkpoints.load_valid_checkpoint(conn, *, run_id, stage, step=None,
version=None)`. It walks the matching rows newest first and returns the first
one whose state parses. `load_checkpoint` stays strict and keeps raising: a
caller that asked for a specific version still gets told the truth.

`list_checkpoints` gains `skip_invalid: bool = False`. With the flag set, an
unreadable row is left out of the listing instead of breaking it.

### F3 — one writer for projection tasks (D3)

`Store._enqueue_projection` calls `outbox.enqueue_projection` instead of
carrying its own INSERT. The count it returns to `import_run` still has to
mean "a new task was created", so the existing pre-check stays.

## 8. Test harness design

New file `tests/test_db_crash_recovery.py`, plus `tests/conftest.py` for the
fixtures that two test modules now share.

**File-backed database.** Every crash test uses `tmp_path / "ph.db"`. The
migration runs once on the first open; later opens reuse the file.

**Process kill.** A helper builds a child program that opens the database
path, does what the test needs, and dies:

```python
def crash_child(
    db_path: Path,
    body: str,
    *,
    timeout: int = 60,
    extra_args: Sequence[str] = (),
) -> subprocess.CompletedProcess:
    """Run `body` in a child process against `db_path`; the child kills itself."""
```

`timeout` is the number of seconds the child may run before it is killed and
the call fails; `extra_args` are passed to the child after the database path,
so the child reads them as `sys.argv[2]` and later.

`body` is Python source that receives the database path in `sys.argv[1]`. The
child ends with `os._exit(9)`, which skips every cleanup path: no `close()`,
no `atexit`, no flush of the connection's buffers. The parent then reopens the
file and checks what survived.

**Shared fixtures.** `tests/test_db_store.py` already builds a run directory
from `tests/fixtures/hn_search.json`. That builder moves to
`tests/conftest.py` so the crash suite reuses it instead of copying it. The
builders stay byte-identical; only their location changes.

**Time.** Lease expiry is driven by writing `lease_expires_at` directly, the
way the existing job tests do. Wall-clock waiting would make the suite slow
and flaky.

## 9. Acceptance

The run is done when all of the following hold.

1. `uv run pytest` is green, with the pre-existing 361 tests still passing
   unchanged.
2. Every invariant V1 to V12 has a named test that fails if the invariant is
   broken. A test that cannot fail is not evidence.
3. F1 and F2 each have a test that fails on the pre-fix code. F3 is a
   behaviour-preserving refactor, so it is pinned by asserting the whole
   projection row it writes.
4. The report states plainly which interruptions are covered and that
   power-loss durability is not among them.
5. Nothing outside the declared radius changes, and no schema migration is
   added.

## 10. Grill record

The task arrived with a precise list of behaviours to prove and a commit
message, so the questions below were answered from the brief rather than by
asking. Each answer is the conservative reading.

1. *Should the handler contract change so a job's success and its writes
   commit together?* No. It would close the at-least-once window, but it
   changes every handler and the worker, which the brief rules out. The window
   is documented instead, and V11 shows the deterministic-id defence holds.
2. *May the fixes touch `db/migrations/`?* No. All three fixes fit in existing
   tables, so the risk floor stays at medium and no migration is needed.
3. *Is a subprocess kill acceptable in the test suite?* Yes. The brief asks
   for realistic process-interruption tests, and no in-process trick reproduces
   a killed process. The child is a few lines and runs in milliseconds.
4. *Should the existing store tests be edited?* Only to move shared fixture
   builders into `conftest.py`. No assertion changes, and the adapter forbids
   weakening a test to make it pass.

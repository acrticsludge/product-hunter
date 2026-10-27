# Handoff — Phase 2 crash recovery

RUN_ID: wave1-crash-recovery-20261021
Commit: `0eb75c2` ("phase2: harden crash recovery"), branch `main`
Base commit at the time of the commit: `679d0df`. The base moved from
`205a4ba` to `679d0df` while this run worked, because other loop runs share
this worktree and were committing alongside it.
Risk: medium (store, queue, outbox and checkpoint layer; no schema, auth, payment, secret or infrastructure path)
Blast radius: `src/product_hunter/db/store.py`,
`src/product_hunter/db/checkpoints.py`, `tests/conftest.py`,
`tests/test_db_crash_recovery.py`, `tests/test_db_store.py`, and this run's
docs.

## Repo map

- `src/product_hunter/db/` — the authoritative layer. `connection.py` opens
  every database with `isolation_level=None` (autocommit unless a statement
  issues `BEGIN`), `foreign_keys=ON`, `busy_timeout=5000`,
  `synchronous=NORMAL`, and `journal_mode=WAL` for file-backed databases.
- `db/migrations/0001_initial.sql` — the only schema. This run added no
  migration and changed no table.
- `tests/` — pytest, one file per module, plain helpers, no classes.
  `tests/conftest.py` now holds the shared run-directory builders, so a new
  test file can import them rather than copying them.
- `docs/reasonix/` — `specs/`, `plans/`, `prompts/`, `handoffs/`. The phase-1
  reports live in `docs/reasonix/reports/`.
- `.loop/adapter.json` — the gate is `uv run pytest`. Python 3.14.

## What changed

Three fixes, all inside the radius:

| Fix | Where | What it does |
|---|---|---|
| F1 | `store.py` `import_run` | Refuses a run whose artifacts hold a different number of records than the manifest declares. The check runs before the import transaction opens, so a refused import never starts one. |
| F2 | `checkpoints.py` | `load_valid_checkpoint` returns the newest checkpoint whose `state_json` parses; `list_checkpoints` gained `skip_invalid`. `load_checkpoint` stays strict. |
| F3 | `store.py` `_enqueue_projection` | Calls `outbox.enqueue_projection` instead of carrying its own INSERT, so the outbox has one writer. |

One new suite: `tests/test_db_crash_recovery.py`, 21 cases, all of them on a
file-backed database with a real killed child process.

## Invariants now covered by a test

The next track does not have to re-derive these. Each one is a test name in
`tests/test_db_crash_recovery.py`.

- A committed transaction survives a process kill; an open one leaves nothing.
- A job whose worker vanished is recovered, never half-applied.
- A worker whose lease expired cannot write, and a superseded epoch cannot
  complete, fail, renew or write.
- Two workers racing for one job produce exactly one claim.
- A fenced transaction rolls back every write inside it.
- A checkpoint survives a restart, and a corrupt one does not stop recovery.
- An outbox task lands with the evidence it projects, or with neither.
- A replayed import creates no duplicate rows and the same ids.
- A truncated run directory is refused.
- A handler that raises after its own commit is retried without duplicating.
- A projection abandoned with an expired lease converges to one row.

## What the next track should know

**The queue is at-least-once.** A crash between the handler's commit and
`complete_job` re-runs the handler, because those are two transactions.
Duplicate authoritative mutations are prevented by the deterministic ids in
`Store`, not by the queue. A handler that writes with non-deterministic ids
can still double-apply.

**Power-loss durability is not proven.** `synchronous=NORMAL` means a host
power loss can discard recent commits. The tests prove process-crash
durability only. Do not report otherwise.

**`load_valid_checkpoint` has no production caller.** `registry.run_log` still
calls `list_checkpoints` without `skip_invalid`, so a corrupt checkpoint still
stops the run's listing. Recorded as a `shortcut:` comment in
`checkpoints.py`. The follow-up is one argument at the call site.

**`load_valid_checkpoint` is not re-exported** from `product_hunter.db`. That
module was mid-change in a concurrent run, so it was left alone. Two lines in
`db/__init__.py` close it.

**`pain_points.jsonl` is not completeness-checked**, because the import never
reads it. A divergent sidecar is caught by the artifact hash comparison on
replay. Recorded as a `shortcut:` comment in `store.py`.

**No audit trail.** A refused import and a skipped checkpoint leave nothing
queryable. The codebase has no logging, and an audit table would need a
migration.

**`crash_child` runs a Python source string.** Values must travel through
`sys.argv` and never be spliced into the program. The child inherits the
parent environment, and the child's stderr reaches an assertion message when
the return code is wrong.

## Reusable pieces

`tests/conftest.py` gives a new test file:

- `open_store(db_path)` — a `Store` on a file path.
- `crash_child(db_path, body, *, timeout=60, extra_args=())` — runs `body` in
  a child process that ends in `os._exit(9)`, and fails the test rather than
  hanging if the child stalls.
- `_build_run`, `_write_jsonl`, `_fixture_fetch` and the other builders — the
  run-directory fixtures, moved verbatim from `tests/test_db_store.py`.

`db/store.py` gained nothing that a caller must adopt. `db/checkpoints.py`
gained two opt-in functions and one optional keyword; every existing signature
is unchanged.

## Gate

`uv run pytest` — `747 passed` in the shared worktree, `485 passed` on the
exact committed tree `0eb75c2` in a clean scratch worktree. The second number
is the one that describes this change.

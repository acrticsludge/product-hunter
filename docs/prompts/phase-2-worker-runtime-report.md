# Prompt — write the run report for the worker runtime

Node: `loop-free-scribe`
Stage: 10 REPORT
Write one file: `docs/reasonix/reports/phase-2-worker-runtime.md`

Do not touch any other file. Do not commit. Do not run the test suite.

## The facts

The change adds a supervised worker runtime on top of the authoritative SQLite
job queue, in the repository `C:\Anubhav\Web Dev Projects\product-hunter`. The
base commit is `205a4ba`. The commit message will be
`phase2: implement supervised worker runtime`.

### Files added

| File | Lines | What it holds |
|---|---|---|
| `src/product_hunter/runtime/__init__.py` | 32 | public exports |
| `src/product_hunter/runtime/identity.py` | 60 | `WorkerIdentity` |
| `src/product_hunter/runtime/events.py` | 74 | `WorkerEvent`, `EventSink`, `JsonlEventSink` |
| `src/product_hunter/runtime/lock.py` | 103 | `SingleInstanceLock`, `LockError` |
| `src/product_hunter/runtime/worker.py` | 526 | `RetryPolicy`, `WorkerRuntime`, `install_shutdown_handlers` |
| `src/product_hunter/runtime/__main__.py` | 126 | the process entry point and the shutdown watchdog |
| `tests/test_runtime_worker.py` | 1019 | 42 tests |
| `tests/test_runtime_lock.py` | 82 | 6 tests |

One existing file changed: `.gitignore`, where the unanchored pattern
`runtime/` became `/runtime/`. The unanchored pattern also matched
`src/product_hunter/runtime/`, so git ignored the new package and would not
have committed it. The repository root `runtime/` directory stays ignored,
which is what the pattern was for.

Documentation added: `docs/reasonix/specs/phase-2-worker-runtime.md`,
`docs/reasonix/plans/phase-2-worker-runtime.md`,
`docs/reasonix/handoffs/phase-2-worker-runtime-handoff.md`, the four prompt
files under `docs/reasonix/prompts/`, this report, and a README section on the
worker runtime.

### What the runtime does

Claims a job from the SQLite queue, holds a fenced lease while the handler
runs, renews that lease on a background thread, records the outcome, retries a
failure until the attempt ceiling, recovers leases that expired, stops on a
signal without abandoning the job in flight, reports every transition as one
JSON object per line, and refuses to run a second worker beside the first
one through an operating system file lock.

SQLite is the only queue and the only authority. Nothing under
`src/product_hunter/db/**` or `db/migrations/**` changed. No dependency was
added.

### The gates

- Repository gate, `uv run pytest`: `740 passed in 9.07s`.
- The same gate against the base commit plus this change alone, in a clean
  worktree: `409 passed in 4.28s`.
- The two new test files on their own: `48 passed in 2.32s`.

The repository tree is shared with another session that has a large amount of
uncommitted in-flight work, so the isolated number is the one that describes
this change on its own.

### What the hardening pass found, and what closed it

1. A handler failure recorded the raw exception message in `jobs.last_error`
   and in the event stream, so page text or a credential could reach the
   durable record. Closed by `_safe_error`, which keeps the exception type,
   masks a labelled secret and cuts the message at 300 characters.
2. The watchdog called `os._exit` without closing the database connection.
   The proposed fix is impossible: the connection was created with
   `check_same_thread=True`, so closing it from the watchdog thread raises
   `sqlite3.ProgrammingError`. The hard exit stays, the write ahead log is
   left to SQLite's own crash recovery, and the abandoned job is recovered
   when its lease expires.
3. The lock file deleted while held can be locked by a second worker, because
   the operating system lock binds to the file and not to the path. The
   assumption is now stated in the module docstring and in the spec. The
   database fencing bounds the effect to duplicated work.
4. The constructor accepted a heartbeat interval that was not smaller than
   the lease. Closed with a check and a test.

### What the review found, and what closed it

The review came back "ship with fixes" and reproduced its main finding: a
handler that held a write transaction for longer than the five second
`busy_timeout` killed the lease renewal thread with an uncaught
`sqlite3.OperationalError`, nothing noticed, the lease never renewed again,
and the job was recovered and the handler ran a second time.

1. The heartbeat died silently on any error other than a lost lease. Closed:
   it now reports `lease_renewal_failed`, sets the lost flag, and stops
   treating the lease as healthy.
2. A busy database killed the worker process at the claim site. Closed with a
   bounded retry around the claim, the completion and the failure record.
3. An outcome that cannot be written is reported as `job_outcome_failed`, and
   nothing further is written for that job.
4. A database error in the claim loop is reported as `worker_error` and the
   loop keeps polling.
5. Two tests passed for the wrong reason. The entry point test now checks
   that the handler mapping from the environment reaches the runtime, and the
   in-memory refusal has its own test.
6. Four copies of the same lease lost block became one helper.
7. The heartbeat event uses the masked error, the mask covers the common
   `Bearer` form, and the two lock events carry the time the event happened.

### Integration assumptions

- A later track registers the handlers for the real stages. The registry is a
  plain mapping from stage name to callable.
- `python -m product_hunter.runtime` reads the handler factory from
  `PH_WORKER_HANDLERS` as `module:attribute`, the lock file from
  `PH_WORKER_LOCK`, and the database from `PH_DB_PATH`. Whoever sets those
  variables decides what code runs, so they are operator configuration and
  never come from untrusted input.
- Without a handler factory the worker starts idle and claims nothing, so a
  worker started before the handler tracks exist cannot burn the queue.

### Known limitations

- The `fcntl.flock` branch of the lock was never exercised, because the
  machine runs Windows.
- A real Ctrl+C delivered to a running worker is not covered by a test. On
  Windows `os.kill` with `SIGINT` ends the process instead of delivering a
  Python level signal, so the routing is covered in process and the loop exit
  is covered by two tests.
- The lock file must not be deleted or replaced while a worker holds it.
- The secret mask needs a label, so a bare token in free text is not masked.
- A handler that reaches outside the database has to be idempotent on its own.
  The database fence protects the job row, not that side effect.

## How to write it

- Read the house style in `docs/reasonix/handoffs/phase-2-worker-runtime-handoff.md`
  and follow it.
- Plain prose, no marketing, no filler. No exclamation marks, no em dashes,
  no rule of three where two will do.
- Do not invent numbers, quotes, dates or facts. Everything above is measured
  or checked. If a number is not in the facts above, leave it out.
- Sections: what the runtime does, the files, the gates, what the hardening
  and the review changed, the integration assumptions, the known limitations,
  and what was not checked.
- The commit hash is not known yet, so do not write one.

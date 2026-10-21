# Phase 2 worker runtime — implementation plan

Spec: `docs/resonix/specs/phase-2-worker-runtime.md`
Base commit: `205a4ba`
Branch: `main`

## Order of work

The tasks run in this order. Task 1 has to exist before task 4, because the
runtime needs a heartbeat connection and the heartbeat needs the runtime
contract.

### Task 1 — identity and events

Files: `src/product_hunter/runtime/identity.py`, `src/product_hunter/runtime/events.py`

- `WorkerIdentity` frozen dataclass with `create()` and `to_dict()`.
- `WorkerEvent` frozen dataclass with `to_dict()` and `to_json()`.
- `EventSink` protocol, `JsonlEventSink` writing one flushed JSON line per event.

Done when the worker id format holds and a sink round trips an event through
`json.loads`.

### Task 2 — single instance lock

File: `src/product_hunter/runtime/lock.py`

- `SingleInstanceLock` with a non blocking `acquire()`, a `release()`, and the
  context manager protocol.
- `msvcrt.locking` on Windows, `fcntl.flock` elsewhere.
- The lock file carries the worker id, the pid and the start time.

Done when a second lock on the same path returns `False` and succeeds after
the first one releases.

### Task 3 — the runtime

File: `src/product_hunter/runtime/worker.py`

- `RetryPolicy.delay_for(attempt)`.
- `WorkerRuntime` with `recover`, `run_once`, `run_forever`, `request_stop`.
- The heartbeat thread, on its own connection, renewing while the handler runs
  and stopped before the outcome is written.
- `install_shutdown_handlers(runtime)` returning an uninstaller.

Rules that must hold in the code, not only in the tests:

- The heartbeat never touches the connection the loop uses.
- The heartbeat stops before `complete_job` or `fail_job` runs, so a renewal
  cannot race a completion and report a false lease loss.
- A lease loss means the runtime writes nothing else for that job. No
  `fail_job`, no `complete_job`.
- No handler registered means no claim.

Done when `run_once` returns `JobOutcome` with the status `succeeded`,
`failed` or `lease_lost`, and the lease epoch travels with every write.

### Task 4 — the process entry point

File: `src/product_hunter/runtime/__main__.py`

- Read the handler factory from `PH_WORKER_HANDLERS` as `module:attribute`.
- Read the database path from `PH_DB_PATH` and the lock path from
  `PH_WORKER_LOCK`.
- Acquire the single instance lock, then run the worker.
- Install the signal handlers, and run a watchdog that logs `shutdown_timeout`
  and exits with code 3 when the handler overruns `shutdown_grace_seconds`.

Done when `python -m product_hunter.runtime` starts, stays idle without
handlers, and stops on `SIGINT`.

### Task 5 — tests

Files: `tests/test_runtime_worker.py`, `tests/test_runtime_lock.py`

The matrix in the spec, one test per row: successful job, failed job, retry,
expired lease, stale worker, lease loss, graceful shutdown, duplicate
execution, single instance lock, events, idle mode.

Determinism rules:

- No wall clock waits. A test that needs an expired lease writes a past
  timestamp into the job row first.
- A test that needs a stolen lease updates the row from a second connection
  while the handler waits on a `threading.Event`, so the order is fixed.
- The heartbeat test uses a file backed database in `tmp_path`, because an
  in-memory database cannot give the heartbeat a second connection.
- Tests that do not exercise the heartbeat pass `heartbeat_seconds=0` and can
  use `Store.open(":memory:")`.

Done when `uv run pytest` is green, and the new tests fail when the runtime
misbehaves.

### Task 6 — package exports and docs

Files: `src/product_hunter/runtime/__init__.py`, `README.md`

- Export the public names from the package root.
- One README section on the worker runtime: what it guarantees, how to start
  it, what it does not do.

## Not in this plan

No change under `src/product_hunter/db/`, no schema change, no CLI command, no
handler registration for real stages. The CLI wiring belongs to the track that
owns those stages.

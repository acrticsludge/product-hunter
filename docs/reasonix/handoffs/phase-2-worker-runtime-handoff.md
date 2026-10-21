# Handoff — Phase 2 worker runtime

RUN_ID: wave1-worker-runtime-20261021
Base commit: `205a4ba` (Phase 2 step 1, authoritative SQLite evidence core)
Branch: `main`
Risk: medium (backend runtime, no schema/auth/payment/secret/infra path)
Blast radius: `src/product_hunter/runtime/**`, `tests/test_runtime_*.py`,
`README.md`, `docs/resonix/**` for this track.

## Repo map

- `src/product_hunter/` — the package. Flat modules: `cli.py`, `config.py`,
  `documents.py`, `evaluation.py`, `extraction.py`, `fetch.py`,
  `normalization.py`, `quote.py`, `raw_store.py`, `reporting.py`, `safety.py`,
  `sources.py`, plus the `db/` and `llm/` packages.
- `src/product_hunter/db/` — the authoritative layer. `connection.py`,
  `migrations.py`, `store.py`, `jobs.py`, `outbox.py`, `checkpoints.py`,
  `worker.py`. Read-only for this track.
- `db/migrations/0001_initial.sql` — the only schema. Read-only for this track.
- `tests/` — pytest, one file per module, no classes, no fixtures beyond
  plain helpers.
- `docs/resonix/` — `specs/`, `plans/`, `reports/`.
- `.loop/adapter.json` — the gate is `uv run pytest`. Python 3.14.

## What exists that the runtime reuses

`product_hunter.db.jobs`, read directly, and used as it is:

| Name | Signature | Notes |
|---|---|---|
| `claim_job` | `(conn, worker_id, *, lease_seconds=60, now=None) -> Job \| None` | Calls `recover_expired_leases` first, orders by `priority DESC, scheduled_for ASC, created_at ASC`, sets `status='leased'`, `lease_epoch += 1`, `attempts += 1`, inserts a `stage_attempts` row with status `running`. Uses `BEGIN IMMEDIATE`. |
| `renew_lease` | `(conn, job, *, worker_id, lease_epoch, lease_seconds=60, now=None) -> str` | Updates only when `status='leased'` AND `worker_id` matches AND `lease_epoch` matches AND `lease_expires_at > now`. Otherwise raises `LeaseLost`. Returns the new expiry. |
| `complete_job` | `(conn, job, *, worker_id, lease_epoch, result=None, now=None) -> Job` | Same fence plus a live lease. Sets `succeeded`, clears `worker_id` and `lease_expires_at`, closes the `stage_attempts` row. Raises `LeaseLost` when the fence fails. |
| `fail_job` | `(conn, job, *, worker_id, lease_epoch, error, backoff_seconds=5, now=None) -> Job` | Same fence. Sets `dead` when `attempts >= max_attempts`, else `queued` with `scheduled_for = now + backoff_seconds`. Raises `LeaseLost` when the fence fails. |
| `recover_expired_leases` | `(conn, *, now=None) -> int` | Requeues expired leases, or marks them dead at the attempt ceiling. Marks the attempt `lease_lost`. Returns the number of rows touched. |
| `enqueue_job` | `(conn, *, stage, idempotency_key, payload=None, run_id=None, step=None, priority=0, max_attempts=3, scheduled_for=None, now=None) -> Job` | The job id is `job_` plus a SHA-256 prefix of the idempotency key. The same key returns the same row; a different payload raises `JobConflict`. |
| `get_job` | `(conn, job_id) -> Job \| None` | |
| `list_jobs` | `(conn, *, status=None) -> list[Job]` | |

`Job` fields: `id`, `run_id`, `stage`, `step`, `payload`, `priority`, `status`,
`attempts`, `max_attempts`, `idempotency_key`, `worker_id`, `lease_epoch`,
`lease_expires_at`, `created_at`, `scheduled_for`, `completed_at`,
`last_error`, `result`.

`JobOutcome` fields: `job_id`, `status`, `result`, `error`.

`JobContext(conn, job, *, worker_id, lease_seconds=60)`:
`assert_lease()` raises `LeaseLost` when the row is no longer leased to this
worker at this epoch, or when the lease already expired. `heartbeat()` calls
`renew_lease`. `transaction()` is a context manager that runs
`BEGIN IMMEDIATE`, asserts the lease, yields the connection, asserts the lease
again, then commits; any exception rolls back and re-raises.

`LeaseLost` is a subclass of `JobError`.

`product_hunter.db.connection.connect(db_path, *, read_only=False)`: opens the
file, creates the parent directory, sets `foreign_keys=ON`,
`busy_timeout=5000`, `synchronous=NORMAL`, and `journal_mode=WAL` for a file
database. `row_factory` is `sqlite3.Row`. `check_same_thread=True`, so a
connection belongs to the thread that made it. A `:memory:` database has no
file, and `PRAGMA database_list` reports an empty file for it.

`product_hunter.db.store.Store.open(db_path, *, migrate_schema=True)` returns
a `Store` holding the connection in `store.conn`; `Store.close()` closes it.
`Store.open(":memory:")` gives an in-memory database, which is the pattern
the existing tests use.

`product_hunter.fetch.utcnow()` is the project clock (UTC, timezone aware).

## What exists that the runtime must not duplicate

- `product_hunter.db.worker.Worker` stays as it is. It claims inside
  `run_once()` and never exposes the claimed job, so it cannot renew a lease
  for a long handler. The runtime owns the process semantics instead.
- `product_hunter.db.outbox` owns the projection queue. The runtime does not
  touch it.
- `product_hunter.db.checkpoints` owns checkpoints. The runtime does not
  touch it.

## Schema facts the tests rely on

`jobs`: `id` (PK), `run_id`, `stage`, `step`, `payload_ref`, `payload_json`,
`priority`, `status` (`queued|leased|succeeded|failed|dead|cancelled`),
`attempts`, `max_attempts`, `idempotency_key` (unique), `worker_id`,
`lease_epoch`, `lease_expires_at`, `created_at`, `scheduled_for`,
`completed_at`, `last_error`, `result_json`.

`stage_attempts`: `attempt_id` (PK), `job_id` (FK), `run_id`, `stage`,
`attempt`, `worker_id`, `lease_epoch`, `status`, `started_at`, `finished_at`,
`error`, `result_json`, unique on `(job_id, attempt)`.

There is no worker registry table, no lease table and no event table for
worker events. `research_events` needs a `run_id` that references
`research_runs`, so worker events do not belong there. The runtime writes its
events as JSON lines and leaves the authoritative per attempt record to
`stage_attempts`.

## House style

- Module docstring on every module, one paragraph, plain and specific.
- `from __future__ import annotations` at the top of every module.
- Type hints everywhere, `X | None` instead of `Optional[X]`.
- Private helpers with a leading underscore, defined after the public names.
- Errors are exceptions with a short message, never a printed string.
- The CLI prints JSON and returns an exit code. The runtime returns values and
  raises; it does not print.

## Forbidden for this track

- No change under `db/migrations/**` or `src/product_hunter/db/**`.
- No schema, auth, payment, secret or infrastructure path.
- No new dependency.
- No second queue or second database.
- Never change a test to make it pass.

## Final state, after the review and the hardening pass

The runtime shipped with these additions over the first version:

- `_safe_error` masks a labelled secret and cuts the message at 300 characters
  before it reaches `jobs.last_error` or the event stream.
- The constructor refuses `heartbeat_seconds >= lease_seconds`, because a
  renewal that lands on or after the expiry cannot renew anything.
- The heartbeat catches a database error, reports `lease_renewal_failed` and
  stops treating the lease as healthy, instead of dying silently.
- `_with_busy_retry` waits out another writer that holds the file. The claim,
  the completion and the failure record go through it. A database error in the
  claim loop is reported as `worker_error` and the loop keeps polling.
- An outcome that cannot be written reports `job_outcome_failed` and writes
  nothing further for that job.
- `SingleInstanceLock` takes an optional `identity`, and `__enter__` raises
  `LockError` when the lock is refused.

The spec at `docs/reasonix/specs/phase-2-worker-runtime.md` was corrected to
match this, and it carries the known limitations.

The database layer was not touched. `product_hunter.db.worker.Worker` still
carries the raw exception message in `jobs.last_error`; the runtime masks it
before it writes, because the runtime is the boundary where handler output
reaches the durable record.

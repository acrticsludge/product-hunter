# Prompt — hardening pass for the worker runtime

Node: `loop-free-harden`
Stage: 6 HARDEN
Change under review: the new `src/product_hunter/runtime/` package and
`tests/test_runtime_worker.py`, `tests/test_runtime_lock.py`.
Base commit: `205a4ba`.

## What the change is

A supervised worker runtime over the authoritative SQLite job queue. It
claims a job, holds a fenced lease while the handler runs, renews that lease
on a background thread, records the outcome, retries failures, recovers
expired leases, stops gracefully, reports structured JSON events, and refuses
to run a second worker beside the first one.

The files to read, in this order:

1. `src/product_hunter/runtime/worker.py` — the runtime.
2. `src/product_hunter/runtime/lock.py` — the single instance lock.
3. `src/product_hunter/runtime/events.py` — the event records.
4. `src/product_hunter/runtime/identity.py` — the worker identity.
5. `src/product_hunter/runtime/__main__.py` — the process entry point.
6. `docs/resonix/specs/phase-2-worker-runtime.md` — what it promises.

The code it calls, which you must read but not change:
`src/product_hunter/db/jobs.py`, `src/product_hunter/db/connection.py`.

## What to look for

The never-cut list applies here: validation at trust boundaries, error
handling that prevents data loss, security, accessibility where it applies, and
the calibration real hardware needs. Nothing in this change may be cut to
make it smaller.

Look at least at these, and add whatever the code itself suggests:

- **Trust boundaries.** The handler factory is loaded by module path from the
  `PH_WORKER_HANDLERS` environment variable, and the lock path comes from
  `PH_WORKER_LOCK`. Say plainly what an operator with those two variables
  controls, and whether that is acceptable for a local supervised process.
- **Untrusted data in the logs.** A handler failure is recorded as
  `f"{type(exc).__name__}: {exc}"` and then written to the job row and to the
  event stream. A handler can raise an exception whose message carries page
  text or model output. Say whether the runtime can leak untrusted content
  into logs or into the database, and what the cheapest fix is.
- **The lock.** Whether the lock really excludes a second worker on the same
  path, what happens when the lock file is deleted while it is held, and
  whether the record written into the lock file can be trusted.
- **Lease safety.** Whether any path exists where the runtime writes a job
  outcome without a current lease, where a lost lease still produces a write,
  or where a renewal races a completion.
- **Threads and shutdown.** Whether the heartbeat thread can outlive the
  runtime, whether a connection can leak, and what a `SIGINT` during a
  handler does.
- **Resource handling.** Open file handles, open connections, the watchdog
  thread, and the `os._exit` path.
- **Observability.** Whether the events are enough to reconstruct what a
  worker did, and whether any event can be lost silently.

## Rules

- You are the hardening node. You report. You do not edit, create or move any
  file in the repository.
- Do not propose a change to `src/product_hunter/db/**` or `db/migrations/**`.
  The database layer is read-only for this track and is under review by
  another session.
- A finding needs a name, the file and line, why it matters, and the smallest
  fix that would close it. Rank them: must fix, should fix, nice to have.
- Do not manufacture findings. If the code is sound in an area, say so in one
  line and move on.
- Do not report style preferences as findings. Report the things that bite in
  production.

## Report back

- The ranked findings, each with file and line.
- The areas you checked and found sound, in one line each.
- The single most important thing to fix, and why it is that one.

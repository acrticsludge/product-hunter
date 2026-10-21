# Phase 2 scheduler handoff

**Date:** 2026-10-21
**Scope:** the scheduler orchestration layer above the SQLite job queue.
**Run:** loop-free, seven implement rounds, three harden passes, six review
passes.

## What landed

| File | Change |
|---|---|
| `src/product_hunter/db/scheduler.py` | new, 644 lines: `Priority`, `Schedule`, `BlockedSchedule`, `DroppedSchedule`, `ConflictedSchedule`, `ScheduleTick`, `Scheduler`, `default_schedules` |
| `src/product_hunter/db/worker.py` | +42/-2: an optional `backoff` callable on `Worker`, a `_delay` helper that clamps both ends and falls back when the hook breaks, a module logger |
| `src/product_hunter/db/jobs.py` | +12/-3: `claim_job` orders by `id ASC` as a final tiebreak, `_now` reads the clock in UTC |
| `src/product_hunter/db/__init__.py` | the scheduler exports |
| `tests/test_db_scheduler.py` | new, 1866 lines, 64 tests |

No schema change. No migration. No new dependency. The gate command
`uv run pytest` passes with 908 tests, 64 of them in the scheduler suite.

## The contract in one page

A `Schedule` is a recurring definition: a name, a stage, an interval, a
priority band, an optional source and run, and an optional catch-up cap.

Occurrence times come from a fixed UTC grid, `epoch + offset + k * interval`.
Nothing about the schedule's state is stored. The occurrence is the identity
of the work, and the idempotency key `schedule:<name>:<occurrence>` carries
it. Two consequences follow, and they are the reason the design works: a
tick never depends on what the process remembered, and materialising the same
occurrence twice is a no-op.

One tick walks the enabled schedules, computes the occurrences in the
catch-up window, applies the source gate, and writes each occurrence through
`enqueue_job`. It reports what it did: created, existing, blocked, dropped and
conflicting. It runs in one transaction, so a tick that raises leaves the
table as it was, and a transaction the caller already opened is joined
through a savepoint rather than committed.

Priorities are P0 to P4, fifty down to ten, because the queue orders by
`priority DESC` and P0 is the most urgent band.

## The source gate

A schedule that names a `source_id` creates no work for a source that is
unknown, disabled, or whose terms are not reviewed or permitted. The gate
reads the `sources` table, the same mirror `Store.sync_registry` writes, and
mirrors `SourceRegistry.require`. A blocked schedule is reported and logged,
and no job is created.

## What the reviews changed

The reviews were not a formality. They found, among others:

- The tick looked the idempotency key up itself and skipped the write, so a
  changed schedule silently ran the old work. Now the write always happens
  and the queue's conflict check runs.
- A priority that was not an integer was stored as text in an INTEGER column,
  which made every read of the jobs table raise. The queue was bricked by one
  bad argument.
- A content conflict aborted the whole tick, and it recurred for as long as
  the stale occurrence sat in the catch-up window, so one stale schedule
  stopped every other schedule for up to a day.
- The conflict check compared columns the queue itself writes, so a job that
  had failed once, or that the operator had promoted to P0, was reported as a
  conflict on every tick.
- `promote` read the status and then wrote the row with a second statement,
  so a job leased in between was still reprioritised.
- The first tick on a fresh database queued 126 backfill jobs the operator
  never asked for.

## Open items for the next phase

1. **A single-instance lock.** The spec asks for one supervised worker. The
   idempotency key stops a second scheduler from creating the same job twice,
   but nothing stops two workers from running at the same time.
2. **Stage handlers.** The cadence names `COLLECT`, `PROCESS`, `CLUSTER`,
   `INVESTIGATE` and `BRIEF`. None has a handler, which is why no supervisor
   ships yet.
3. **Durable scheduler events.** A blocked, dropped or conflicting occurrence
   is logged and reported, never written to the database. The schema has no
   place for it.
4. **Research run creation.** A schedule may reference a run that already
   exists. Creating one is the run service's job.
5. **Local time.** Cadence times are UTC. A cadence that must land on local
   time needs the offset configured per machine.

## Known limits

- `plan` and `overdue` load every queued job into memory and sort in Python.
  Fine at hundreds of jobs, not at millions.
- The UTC normalisation rule is written twice, once in `jobs.py` and once in
  `scheduler.py`. The shared helper belongs in `fetch.py`, which was outside
  this run's radius.
- The scheduler was never exercised against a file-backed WAL database with
  two connections. Every test uses an in-memory database.
- A schedule's priority applies to the occurrences it creates. A stored
  occurrence keeps the priority it was created with, which is also what a
  deliberate `promote` changes.

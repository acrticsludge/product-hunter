# Phase 2 scheduler report

**Date:** 2026-10-21
**Scope:** the scheduler orchestration layer above the SQLite job queue.

## What was built

Phase 2 now has a scheduler. It decides what work exists, in what order, and
when a failed attempt runs again. It creates jobs and reports what it did. It
never runs anything: the worker claims and runs what the scheduler writes.

Files added or changed:

- `src/product_hunter/db/scheduler.py`: new, 644 lines. `Priority`, `Schedule`,
  `BlockedSchedule`, `DroppedSchedule`, `ConflictedSchedule`, `ScheduleTick`,
  `Scheduler` and `default_schedules`.
- `src/product_hunter/db/worker.py`: +42/-2. An optional `backoff` callable on
  `Worker`, a `_delay` helper that clamps the delay at both ends and falls
  back to the flat delay when the hook breaks, and a module logger.
- `src/product_hunter/db/jobs.py`: +12/-3. `claim_job` orders by `id ASC` as a
  final tiebreak, and `_now` reads the clock in UTC so the queue and the
  scheduler compare the same text.
- `src/product_hunter/db/__init__.py`: the scheduler exports.
- `tests/test_db_scheduler.py`: new, 1866 lines, 64 tests.
- `docs/reasonix/specs/phase-2-scheduler.md`,
  `docs/reasonix/plans/phase-2-scheduler.md`, five prompt files under
  `docs/reasonix/prompts/`, this report and
  `docs/reports/phase-2-scheduler-handoff.md`, and a README section.

No schema change, no migration file, no new dependency. The database schema
does not have a scheduler table, and this phase did not add one.

## The state model

A scheduler usually keeps its own state: the last time each schedule fired,
the next due time, whether a schedule is paused. This one keeps none of that.
Occurrence times come from a fixed UTC grid, `epoch + offset + k * interval`,
and the occurrence itself is the identity of the work. The job carries the
key `schedule:<name>:<occurrence>`, and the queue's own idempotency check
turns a repeated occurrence into a no-op.

Two things follow. A tick never depends on what the process remembered, so a
restart computes the same occurrences from the clock and creates nothing
twice. And the schedule's history is not stored, which is a real limit, not an
oversight: pausing a schedule and answering "when did this last run" need a
table the schema does not have. That gap is written down in the spec instead
of designed around.

## The priorities

| Band | Meaning | Value |
|---|---|---|
| P0 | user-requested continuation | 50 |
| P1 | high-potential investigation | 40 |
| P2 | scheduled validation | 30 |
| P3 | background discovery | 20 |
| P4 | maintenance and reprocessing | 10 |

The queue orders by `priority DESC`, so a larger number is served first. The
values are ten apart so a caller can nudge a job inside a band. `promote` is
how a P0 continuation overtakes work that is already waiting, and it refuses
a job that is not queued and a priority below the job's current one.

## The source gate

A schedule that names a `source_id` creates no work for a source that is
unknown, disabled, or whose terms are not reviewed or permitted. The gate
reads the `sources` table, the same mirror `Store.sync_registry` writes, and
mirrors what `SourceRegistry.require` already enforces. A blocked schedule is
reported by the tick and logged as a warning, and no job is created.

## One tick

The tick walks the enabled schedules, computes the occurrences inside the
catch-up window, applies the gate, and writes each occurrence through
`enqueue_job`. It reports five things: the jobs it created, the jobs that
already existed, the blocked schedules, the occurrences a cap dropped, and
the occurrences that conflicted with a stored job.

The tick is all or nothing. It runs inside one transaction, and when the
caller has already opened one, the tick joins it through a savepoint and
leaves the commit to the caller.

## What the reviews found

The review and harden passes were not a formality. They found, among others:

- "The tick checks the idempotency key itself, so a changed schedule is
  silently ignored." The stored job kept the old stage and the old payload,
  and the occurrence ran the wrong work with no error and no log.
- "A priority that is not a number bricks the queue." SQLite kept the string
  in an INTEGER column, and every read of the jobs table then raised. One
  mistyped argument made the queue unreadable.
- "A conflicting occurrence aborts the whole tick with an untyped
  `ScheduleError`, and it recurs on every tick for as long as the stale
  occurrence stays inside the catch-up window." One stale schedule stopped
  every other schedule for up to 24 hours.
- "The conflict check fires on changes the queue and the scheduler itself
  made." A job that had failed once, or that the operator had promoted to
  P0, was reported as a conflict on every tick, and disappeared from the
  queue's own view.
- "`promote` reads `status` in one statement, then updates with `WHERE id = ?`
  only." A job the worker leased in between was still reprioritised.
- "The default catch-up window against the default cadence queues 96
  identical collection jobs on the first tick." A fresh install started by
  doing one day's collection 96 times over.

All of it is fixed and pinned by a test that fails when the fix is removed.

Three existing assertions changed during the work, each because the contract
changed and not to make a failing test pass. The retry test now asserts the
reason left the job payload, because the reason moved to a log. The grid
test lifts the catch-up cap to check the grid, because the cap moved to its
own test. The conflict test used to expect an exception and now expects a
report, because the tick no longer stops for a conflict.

## The gate

`uv run pytest`:

```text
........................................................................ [  7%]
........................................................................ [ 15%]
........................................................................ [ 23%]
........................................................................ [ 31%]
........................................................................ [ 39%]
........................................................................ [ 47%]
........................................................................ [ 55%]
........................................................................ [ 63%]
........................................................................ [ 71%]
........................................................................ [ 79%]
........................................................................ [ 87%]
........................................................................ [ 95%]
............................................                             [100%]
908 passed in 13.45s
```

The scheduler's own suite is 64 tests, all against an in-memory database with
fixed instants. The queue's own tests, `tests/test_db_jobs.py` and
`tests/test_db_worker.py`, pass unchanged. The total grows as the other
nodes in this repository add their own tests; the scheduler's count does not
move.

## The review verdict

The seventh review pass ended with `Looks good. Ship.` and the verdict
`Ship.`, with one Should fix and two Nice to have left. The Should fix, a log
line that named the configured delay rather than the delay applied, and the
Nice-to-have that put the promotion floor inside the write, were folded in
and the gate re-run. The other Nice-to-have, the clock helper written twice,
is a limit of this run's radius, not of the design.

## Known limits

- `plan` and `overdue` load every queued job into memory and sort in Python.
  That is nothing at hundreds of jobs and wrong at millions.
- The UTC normalisation rule is written twice, once in `jobs.py` and once in
  `scheduler.py`. The shared helper belongs in `fetch.py`, which was outside
  this run's radius.
- A blocked, dropped or conflicting occurrence is reported and logged, never
  written to the database. The schema has no table for scheduler events.
- No single-instance lock. The idempotency key stops a second scheduler from
  creating the same job twice, but nothing stops two workers running at the
  same time.
- The scheduler was never exercised against a file-backed WAL database with
  two connections. Every test uses an in-memory database.
- A refused promotion says the job "is no longer queued". That is right when
  a worker leased it, and imprecise in the rarer case where a concurrent
  promotion moved the priority above the floor.
- No stage handler implements `COLLECT`, `PROCESS`, `CLUSTER`, `INVESTIGATE`
  or `BRIEF`, so no supervisor ships yet.
- Cadence times are UTC. A cadence that must land on local time needs the
  offset configured per machine.

## What was not checked

- Two connections writing at once. The race in `promote` is closed by one
  guarded statement and staged with a statement trace callback, not with real
  concurrency.
- The scheduler against a database file, as opposed to memory.
- A worker running the scheduled stages, because no handler exists for them.
- `src/product_hunter/runtime/worker.py`, which is another node's file and
  calls `fail_job` with a delay of its own. The clamp added here does not
  cover that path.

## Next step

The scheduler primitives are done. The next piece is the first stage handler,
which is what makes the cadence meaningful, and the single-instance lock that
keeps one worker in charge of the queue.

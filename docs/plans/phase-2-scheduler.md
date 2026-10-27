# Phase 2 scheduler plan

## The decision

The occurrence state is derived, not stored. Occurrence times sit on a fixed
UTC grid, and the occurrence itself identifies the work through the job's
idempotency key. No table is added, and no migration runs.

The alternative was a per-schedule anchor with the last materialised
occurrence read back from the jobs table. SubQ weighted the grid plan at 0.95
against 0.05 for the anchor plan, with a confidence of 0.90, and put the
chance of a restart skipping or duplicating work at 0.03. The code tells the
same story: the grid needs one function and one loop, the anchor needs a query
per tick and still needs a burst cap.

The full contract is in `docs/reasonix/specs/phase-2-scheduler.md`. This plan
covers how the code is laid out and in what order it lands.

## Reuse decisions

The ladder came out in this order, and the first rung won every time.

| Need | Reused | New |
|---|---|---|
| create a job | `enqueue_job` | nothing |
| order work | `claim_job` ordering | a `plan` that reads the same order |
| move a job to running | `claim_job`, `stage_attempts` | nothing |
| retry a failed job | `fail_job` | a backoff policy, and an optional hook in `Worker` |
| cancel or pause work | not possible in this schema | out of scope |
| source gate | the `sources` table that `Store.sync_registry` writes | three rules in the scheduler |
| clock | `product_hunter.fetch.utcnow` | nothing |
| dependencies | standard library only | nothing |

## Files

### `src/product_hunter/db/scheduler.py`, new

| Symbol | Role |
|---|---|
| `Priority` | `IntEnum` with the P0 to P4 bands |
| `ScheduleError` | a schedule or scheduling request was refused |
| `Schedule` | frozen dataclass: one recurring definition, validated on construction |
| `BlockedSchedule` | name plus reason, for a schedule the source gate refused |
| `DroppedSchedule` | name, count and oldest timestamp, for the occurrences a cap threw away |
| `ConflictedSchedule` | name, occurrence and difference, for a stored job that no longer matches its schedule |
| `ScheduleTick` | the result of one tick: created, existing, blocked, dropped, conflicts |
| `Scheduler` | the scheduler: `tick`, `overdue`, `plan`, `promote`, `backoff_seconds`, `schedule_retry` |
| `default_schedules` | the spec's cadence as schedule definitions |

Internal helpers: the grid calculation, the source gate, and the timestamp
writers. The grid calculation is the only piece with real logic, so it is the
one the tests pin hardest.

`Scheduler.tick` is the heart. It walks the enabled schedules, computes the
occurrences in the window, applies the source gate, and writes each occurrence
through `enqueue_job`. It returns what happened rather than printing anything,
so a caller can log or assert on it.

### `src/product_hunter/db/worker.py`, changed

One addition: an optional `backoff` callable on `Worker`. When it is absent
the worker uses its flat `backoff_seconds` exactly as before, so the default
path is untouched. When it is present, the worker asks it for the delay after
a failure, which is how the scheduler's exponential policy reaches the queue.

### `src/product_hunter/db/__init__.py`, changed

Export the new symbols next to the existing job exports, keeping the file's
one-import-per-symbol style and the `__all__` list in the same order.

### `tests/test_db_scheduler.py`, new

The suite listed in the specification, one test per row of that table, plus a
fixture that opens a migrated in-memory database the way the other database
tests do.

## Order of work

1. `Priority`, `ScheduleError`, `Schedule` and its validation.
2. The grid calculation and the occurrence key.
3. `Scheduler.tick` with `enqueue_job`, and the source gate.
4. `overdue`, `plan` and `promote`.
5. `backoff_seconds`, `schedule_retry` and the `Worker` hook.
6. `default_schedules`.
7. The tests, then the exports.

The grid comes before the scheduler because every other behaviour depends on
what an occurrence is.

## Decisions worth stating

**The tick returns a report, it does not log.** Printing from a library
function makes the scheduler untestable and unembeddable. The caller decides
what to do with the report.

**Blocked schedules are reported, not raised.** A schedule pointing at a source
that has not been synced yet is a normal state, not a crash. The tick reports
the reason and creates nothing.

**`promote` refuses a leased job.** The running attempt is already committed;
the new priority would only take effect on the next attempt. Raising would
promise something the queue cannot deliver. The check and the write are one
statement, so a job the worker leases in between is refused too, and a
demotion is refused because it is not a promotion.

**`schedule_retry` creates a new job rather than reviving the dead one.** The
dead row is the record of what went wrong. The new job has its own attempt
count and its own key, `retry:<job id>:<attempt>`, which makes a repeated
retry a no-op. The reason for the retry is logged, not stored, because it
describes the request and not the work.

**A conflict is reported, not raised.** A schedule that changed after an
occurrence was materialised leaves a stored job with the old content. Raising
would stop every schedule for as long as the stale occurrence sat in the
catch-up window, so the tick records the difference, logs it, and carries on.

**The tick is one transaction.** A tick that raises leaves the jobs table as
it was. When the caller already opened a transaction, the tick joins it
through a savepoint and leaves the commit to the caller.

**No CLI command.** The task asked for primitives and tests, and a command
that schedules work nobody can execute would be a trap. The command lands with
the first stage handlers.

## Risks

| Risk | Control |
|---|---|
| A tick creates a burst of catch-up work after a long sleep | the lookback window and the per-schedule cap |
| Two schedulers run at once and double-create work | the idempotency key makes the second create a no-op; a single-instance lock is still owed |
| A scheduled stage has no handler and the worker spins on it | no supervisor ships until handlers exist |
| The clock moves backwards and an occurrence is materialised twice | the idempotency key makes the second write a no-op |
| `scheduled_for` is compared as text | every timestamp is UTC ISO-8601 from `isoformat()`, the same shape the queue already writes |
| The schedule grid drifts from a local-time cadence | times are UTC by contract, and the offset is configurable |

## Gate

`uv run pytest` must pass with the new suite included. The suite is the
evidence for the behaviour the task named: priority ordering, overdue jobs,
retry delay, duplicate scheduled jobs, idempotency, restart, and disabled or
blocked jobs.

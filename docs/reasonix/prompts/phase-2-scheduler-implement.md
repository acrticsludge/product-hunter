# Implement prompt: Phase 2 scheduler

You are implementing one node of a loop run on the `product-hunter` repository.
Work only inside the declared radius. Do not ask questions; the contract below
is complete.

## Read first

- `docs/reasonix/specs/phase-2-scheduler.md` (the contract)
- `docs/reasonix/plans/phase-2-scheduler.md` (the layout and the order of work)
- `src/product_hunter/db/jobs.py` (the queue you build on)
- `src/product_hunter/db/worker.py` (the worker you touch once)
- `src/product_hunter/db/__init__.py` (the export style)
- `src/product_hunter/db/connection.py` and `src/product_hunter/db/store.py`
  (how a migrated database is opened)
- `tests/test_db_jobs.py` (the test style)
- `db/migrations/0001_initial.sql` (the schema, which does not change)

## Radius

- `src/product_hunter/db/scheduler.py` (new)
- `src/product_hunter/db/worker.py` (one small addition)
- `src/product_hunter/db/__init__.py` (exports)
- `tests/test_db_scheduler.py` (new)

Nothing else. No new dependency. No migration file. No change to any existing
test. No change to the schema.

## What to build

### `src/product_hunter/db/scheduler.py`

```python
class Priority(IntEnum):
    P0 = 50   # user-requested continuation
    P1 = 40   # high-potential investigation
    P2 = 30   # scheduled validation
    P3 = 20   # background discovery
    P4 = 10   # maintenance and reprocessing

class ScheduleError(Exception):
    """A schedule or a scheduling request was refused."""

@dataclass(frozen=True)
class Schedule:
    name: str
    stage: str
    interval_seconds: int
    priority: int = int(Priority.P3)
    payload: Mapping[str, object] = field(default_factory=dict)
    max_attempts: int = 3
    offset_seconds: int = 0
    source_id: str | None = None
    run_id: str | None = None
    catchup_limit: int | None = None
    enabled: bool = True
```

`Schedule.__post_init__` raises `ScheduleError` when the name or the stage is
blank, when `interval_seconds` is below 1, when `offset_seconds` is negative,
when `max_attempts` is below 1, or when `catchup_limit` is set and below 1.

```python
@dataclass(frozen=True)
class BlockedSchedule:
    name: str
    reason: str
    occurrence: str

@dataclass(frozen=True)
class ScheduleTick:
    now: str
    created: tuple[Job, ...]
    existing: tuple[Job, ...]
    blocked: tuple[BlockedSchedule, ...]

class Scheduler:
    def __init__(self, conn, schedules, *, lookback_seconds=86400,
                 backoff_base_seconds=30, backoff_factor=2,
                 backoff_max_seconds=3600) -> None: ...
    def tick(self, *, now: datetime | None = None) -> ScheduleTick: ...
    def overdue(self, *, now=None, limit: int | None = None) -> list[Job]: ...
    def plan(self, *, now=None) -> list[Job]: ...
    def promote(self, job_id: str, priority: int = int(Priority.P0)) -> Job: ...
    def backoff_seconds(self, attempt: int) -> int: ...
    def schedule_retry(self, job: Job, *, now=None, reason: str | None = None) -> Job: ...

def default_schedules() -> list[Schedule]: ...
```

The `Scheduler` constructor raises `ScheduleError` when two schedules share a
name, because the name is part of the idempotency key.

### The grid

The grid origin is the Unix epoch in UTC. A schedule with interval `S` and
offset `O` fires at `origin + O + k * S` for every integer `k`. For a given
`now`, the occurrences inside the catch-up window are the grid points in
`(now - lookback, now]`, oldest first.

- `lookback_seconds` is the scheduler's default window, 24 hours.
- `catchup_limit`, when set on a schedule, caps how many occurrences one tick
  materialises for that schedule. The cap keeps the newest occurrences, so a
  schedule that was asleep for a week queues the most recent work, not the
  oldest.
- When `now` is before the first grid point, the schedule has no occurrences.

Every timestamp is UTC and is written with `datetime.isoformat()`, the same
shape `product_hunter.fetch.utcnow().isoformat()` produces. The queue compares
`scheduled_for` as text, so the shape must not change.

### The tick

`tick(now)` walks the enabled schedules in definition order. For each
occurrence, oldest first:

1. Apply the source gate when the schedule names a `source_id`.
2. Build the idempotency key `f"schedule:{schedule.name}:{occurrence.isoformat()}"`.
3. If a job already holds that key, count it as `existing`.
4. Otherwise create it through `enqueue_job` and count it as `created`.

The job it writes:

| Field | Value |
|---|---|
| `stage` | the schedule's stage |
| `idempotency_key` | the key above |
| `payload` | the schedule's payload plus `schedule` and `occurrence` |
| `priority` | the schedule's priority |
| `max_attempts` | the schedule's `max_attempts` |
| `scheduled_for` | the occurrence time |
| `run_id` | the schedule's `run_id` when it has one |

`run_id` refers to an existing row in `research_runs`. If it does not exist,
the tick raises `ScheduleError` rather than writing a row that breaks the
foreign key.

### The source gate

The gate reads the `sources` table, the mirror `Store.sync_registry` writes.

| Condition | Reason |
|---|---|
| no row for the source | `unknown_source` |
| `enabled` is false | `source_disabled` |
| `terms_status` is not `reviewed` and not `permitted` | `terms_not_permitted` |

A blocked schedule creates no job. The tick records a `BlockedSchedule` with
the schedule name, the reason and the occurrence time. The gate does not
raise.

### Overdue, plan, promote

- `overdue(now, limit=None)`: queued jobs whose `scheduled_for` is strictly
  earlier than `now`, ordered by priority descending, then scheduled time,
  then creation time. A job scheduled exactly now is due, not overdue.
- `plan(now)`: queued jobs whose `scheduled_for` is at or before `now`, in the
  same order. This is the order `claim_job` uses, so the plan and the queue
  agree.
- `promote(job_id, priority)`: raise the priority of a queued job and return
  the updated job. Raise `ScheduleError` when no job has that id, or when the
  job is not queued, because a running job cannot be reprioritised.

### Backoff and retry

- `backoff_seconds(attempt)`: `base * factor ** (attempt - 1)`, capped at
  `backoff_max_seconds`. The attempt number is 1-based, so the first failure
  waits the base delay. An attempt below 1 is treated as 1.
- `schedule_retry(job, now=None, reason=None)`: create a fresh job for the
  same work, scheduled one backoff from now. The key is
  `f"retry:{job.id}:{job.attempts + 1}"`, the stage, priority, payload and
  `max_attempts` come from the original job, and the payload gains
  `retry_of`, `retry_attempt` and `reason`. A repeated call with the same job
  returns the same job.

### The cadence

`default_schedules()` returns the spec's cadence as data. Nothing runs it.

| Schedule | Interval | Offset | Priority | Stage |
|---|---|---|---|---|
| source collection | 15 min | 0 | P3 | `COLLECT` |
| evidence processing | 1 h | 0 | P2 | `PROCESS` |
| re-clustering and re-evaluation | 6 h | 0 | P3 | `CLUSTER` |
| deep investigation | 24 h | 02:00 UTC | P1 | `INVESTIGATE` |
| morning brief | 24 h | 07:00 UTC | P2 | `BRIEF` |

## `src/product_hunter/db/worker.py`

Add one optional keyword argument to `Worker.__init__`: `backoff`, a callable
that takes the job and returns the delay in seconds. When it is absent the
worker keeps its flat `backoff_seconds`, so the current behaviour does not
change. When it is present, the worker uses it for the delay after a handler
raises, and for the "no handler for stage" path.

Keep the change to that. No other edit to the worker.

## `src/product_hunter/db/__init__.py`

Export `Priority`, `Schedule`, `ScheduleError`, `ScheduleTick`,
`BlockedSchedule`, `Scheduler` and `default_schedules`, following the file's
existing style, with `__all__` in the same order.

## `tests/test_db_scheduler.py`

Use the style of `tests/test_db_jobs.py`: `Store.open(":memory:")` for a
migrated database, fixed instants, one behaviour per test, `try/finally` with
`store.close()`.

Write one test for each row of this table:

1. the priority bands order the queue, and `claim_job` agrees
2. overdue jobs are reported, a future job is not
3. the backoff follows 30, 60, 120 seconds and stops at the cap
4. a failed job is requeued with the computed delay, through the worker hook
5. two ticks at the same instant create one job
6. a second scheduler over the same database creates nothing
7. a disabled schedule, an unknown source, a disabled source and a source with
   unreviewed terms create no job and report a reason
8. a scheduled job carries the right stage, priority, payload and scheduled time
9. the queued to running lifecycle, queued then leased with a running attempt
   then succeeded
10. a dead job can be retried later, and the dead row stays
11. a queued job can be promoted, and the plan order changes
12. the cadence definitions land on the grid, quarter hour, hour and 07:00 UTC
13. the catch-up window bounds the backlog after a long sleep

The tests must not depend on the wall clock. Pass `now=` everywhere the API
allows it, and use a fixed instant such as `datetime(2026, 10, 21, 6, 0,
tzinfo=timezone.utc)`.

## Gate

`uv run pytest` must pass with no failures. The existing suite must stay
green: do not change a test to make it pass.

## Report back

- the files you wrote and the line count of each
- the gate output, verbatim
- anything in the contract you could not implement, and why
- what you did not check

## Round 2: the harden findings to fold in

The harden node read this change and returned six findings. Fold all of them
in, in the same files, in the same style. The radius is unchanged.

### F1 and F4, the tick is unbounded and what it drops is invisible

A schedule with a one second interval and the default 24 hour lookback
creates 86,400 jobs in one tick, and `catchup_limit` is optional, so nothing
bounds it. When the cap does truncate the list, the dropped occurrences
appear nowhere.

- `Scheduler.__init__` gains a keyword argument `max_occurrences: int = 100`.
  It is the hard ceiling on the occurrences one tick materialises for one
  schedule.
- The constructor refuses `max_occurrences < 1` and `lookback_seconds < 1`
  with `ScheduleError`. A zero lookback is not a small window, it is a
  silent no-op, so it is refused rather than accepted.
- The effective cap for a schedule is `max_occurrences`, lowered to
  `schedule.catchup_limit` when the schedule sets one. The cap keeps the
  NEWEST occurrences, because the newest work is the work that matters.
- A new frozen dataclass `DroppedSchedule` with the fields `name: str`,
  `count: int`, `oldest: str`, where `oldest` is the ISO timestamp of the
  oldest dropped occurrence.
- `ScheduleTick` gains the field `dropped: tuple[DroppedSchedule, ...]`.
  Every occurrence the cap dropped is counted there, once per schedule.
- When `max_occurrences` truncates a schedule, the tick reports the drop. A
  schedule that silently loses work is the failure mode this closes.

### F2, the payload is unbounded

`Schedule.payload` accepts any mapping, and the tick serialises it into a
job row.

- `Schedule.__post_init__` refuses a payload whose JSON serialisation is
  larger than 65,536 bytes, with `ScheduleError`. Measure it with
  `json.dumps(dict(payload), sort_keys=True)`.
- The limit is on the schedule's own payload. The tick adds two small keys,
  so the stored row can be slightly larger than the limit.

### F3, `lookback_seconds` is not validated

Covered above, in the constructor.

### F5, a blocked schedule is invisible after the fact

The tick reports blocks in memory and writes nothing.

- `scheduler.py` gains a module level logger, `logger = logging.getLogger(__name__)`.
  The standard library `logging` module, nothing else.
- The tick logs one WARNING per blocked schedule, with the schedule name and
  the reason. No payload contents and no source text go into the message.
- The blocked schedule stays in `ScheduleTick.blocked` as well. The log and
  the report are two audiences, not one.

### F6, the worker backoff hook is unvalidated

`Worker._delay` casts whatever the hook returns and hands it to `fail_job`.

- The hook's result is clamped to at least 1 second. A zero or negative
  delay requeues the job as immediately claimable, which is a busy loop.
- A hook that raises must not kill the worker. Call it inside a try, and on
  an exception fall back to the flat `self.backoff_seconds` and log a
  WARNING with the job id.
- The clamp and the fallback apply only to the hook path. With no hook the
  worker keeps `self.backoff_seconds` exactly as it is today.
- `worker.py` gains its own module level logger for that WARNING.

### The tests for round 2

Add to `tests/test_db_scheduler.py`:

1. a one second interval with the default window creates at most
   `max_occurrences` jobs, and the rest are reported as dropped
2. `catchup_limit` lowers the cap, and the dropped count names the oldest
   dropped occurrence
3. `lookback_seconds` below 1 and `max_occurrences` below 1 are refused
4. a payload over 65,536 bytes is refused
5. a backoff hook that returns 0 or a negative number is clamped to at least
   one second, and the failed job is not immediately claimable
6. a backoff hook that raises falls back to the flat delay, and the job is
   still requeued

Keep the existing 14 tests passing. Do not change them.

### The gate for round 2

`uv run pytest` must pass. Run it from the repository root. Report the
output verbatim.

## Round 3: the second harden pass

The harden node re-read the change and returned two findings, both Should fix.
Fold both in, plus the test gap the round 2 report raised.

### R3-1, `max_occurrences` has no upper bound

`max_occurrences` is validated as at least 1, so a caller can pass a value
that defeats the cap: a large `max_occurrences` with a one second interval
over a 24 hour lookback writes tens of thousands of jobs in one tick.

- The constructor refuses `max_occurrences` above 10,000 with `ScheduleError`.
- The message names the limit and the value that was refused.
- Add a module constant for the ceiling, next to the payload byte limit, and
  name it in the same style.

### R3-2, dropped occurrences are reported but never logged

Blocked schedules are logged as warnings, but the occurrences the cap
dropped live only in `ScheduleTick.dropped`. A caller that ignores the tick
report loses the signal that work was dropped, and a dropped occurrence is
work that will never run.

- The tick logs one WARNING per truncated schedule, naming the schedule, the
  number of dropped occurrences, and the oldest dropped occurrence.
- The log line carries the schedule name, a count, and a timestamp. No
  payload contents and no source text.
- The drop stays in `ScheduleTick.dropped` as well.

### R3-3, the log paths have no test

The round 2 report says the two WARNING paths were checked by hand, not by a
test. Close that with pytest's `caplog` fixture, which needs no new
dependency.

- One test asserts that a blocked schedule produces a WARNING record on the
  `product_hunter.db.scheduler` logger, and that the record names the
  schedule and the reason.
- One test asserts that a truncated schedule produces a WARNING record naming
  the schedule, the dropped count, and the oldest dropped occurrence.
- One test asserts that the `product_hunter.db.worker` logger carries a
  WARNING when the backoff hook raises, and that the job is still requeued.

Use `caplog.at_level(logging.WARNING, logger="product_hunter.db.scheduler")`
so the assertions are about this module's records and not about the root
handler's configuration.

### The gate for round 3

`uv run pytest` must pass, with the 20 existing tests unchanged. Report the
output verbatim.

## Round 4: the review findings

The review node read the change adversarially and returned ten findings, two
of them Must fix. Fold all of them in.

NOTE: this prompt file now lives at
`docs/reasonix/prompts/phase-2-scheduler-implement.md`. Another node renamed
the docs tree. The paths in this file are otherwise unchanged.

THE RADIUS GREW BY ONE FILE. `src/product_hunter/db/jobs.py` is now in the
radius, because findings 5 and 7 are fixed there. Touch nothing else. The
existing job tests must still pass unchanged.

### R4-1, the tick threw away the conflict check (Must fix)

The tick looks the idempotency key up itself and counts a hit as `existing`,
so `enqueue_job`'s conflict check never runs. A schedule whose stage or
payload changed keeps running the OLD work, with no error and no log.

- The tick calls `enqueue_job` for EVERY occurrence, with the new content.
- The pre-lookup stays, but only to decide whether the returned job goes in
  `created` or in `existing`. The write is never skipped.
- `enqueue_job` raises `JobConflict` when the key exists with different
  content. The tick catches it and raises `ScheduleError` instead, so the
  module still has one error type for the caller to catch. The message names
  the schedule and the occurrence.
- A test: tick a schedule, change its stage and payload, tick again, and
  assert that the second tick raises `ScheduleError` and that the stored job
  still holds the old stage.

### R4-2, a priority that is not a number bricks the queue (Must fix)

`Schedule` does not validate `priority`, and `promote` writes whatever it is
handed. SQLite keeps a non-numeric value in an `INTEGER` column as text, and
`jobs.py` then does `int(row["priority"])`, so one bad value makes
`list_jobs`, `claim_job`, `plan` and `overdue` raise forever, on an already
committed row.

- `Schedule.__post_init__` refuses a priority that is not an integer.
  `isinstance(priority, int)` and not `isinstance(priority, bool)` is the
  test. `Priority.P0` and other `IntEnum` members pass, because an IntEnum
  is an int. Anything else, including a numeric string and a float, is
  refused with `ScheduleError`.
- `Schedule` is frozen. Store the plain int with
  `object.__setattr__(self, "priority", int(priority))` so the value written
  to SQLite is a plain int and not an enum member.
- `promote` applies the same check and raises `ScheduleError`, never
  `ValueError`.
- Two tests: a schedule with a string priority is refused, and `promote`
  with a string priority is refused and leaves the stored job unchanged.

### R4-3, a repeated retry with a different reason raised the wrong error

`schedule_retry` puts the `reason` in the job payload, and `enqueue_job`
compares the whole payload, so a second retry with a different reason raised
`JobConflict` instead of being the no-op the spec promises.

- The reason leaves the job payload. It describes the retry REQUEST, not the
  work, so it does not belong in the payload that the key identifies.
- Log the reason at INFO level with the job id and the attempt, so the
  reason is still recorded somewhere.
- The payload keeps `retry_of` and `retry_attempt`.
- The docstring says what a repeated call does: the same key, the same job,
  no second job.
- A test: two retries of the same dead job with different reasons return the
  same job id and leave one job in the table.

### R4-4, a retry of a job that is not dead queued the work twice

`schedule_retry` never looks at the job's status.

- It refuses a job whose status is not `dead`, with `ScheduleError` naming
  the status. The spec frames this path for a job that reached its attempt
  ceiling.
- A test: a queued job is refused, and the table still holds one job.

### R4-5, the plan and the queue could order two equal jobs differently

`plan` sorts in Python by `(-priority, scheduled_for, created_at)` and
`claim_job` sorts in SQL by `priority DESC, scheduled_for ASC, created_at
ASC`. Neither has a final tiebreak, so two jobs that are equal in every
visible field can come out in a different order, which breaks the promise
that the plan and the queue cannot disagree.

- `src/product_hunter/db/scheduler.py`: the sort key gains `job.id` as the
  last element.
- `src/product_hunter/db/jobs.py`: `claim_job`'s `ORDER BY` gains
  `, id ASC`. That is the only change in that file for this finding.
- A test: two jobs with the same priority, the same scheduled time and the
  same creation time, and the plan lists them in the order `claim_job`
  claims them.

### R4-6, the first tick on a fresh database queued 126 jobs

The default lookback is 24 hours and the default collection cadence is 15
minutes, so the first tick backfilled a whole day of collection: 96
identical COLLECT jobs, 24 PROCESS, 4 CLUSTER and one each of the two daily
schedules.

- Every schedule in `default_schedules()` gets `catchup_limit=2`. The
  default cadence backfills at most two rounds, so a fresh install starts
  with 8 jobs instead of 126.
- A test: the first tick with `default_schedules()` creates 8 jobs, and no
  schedule creates more than 2.

### R4-7, the queue and the scheduler read the clock differently

The scheduler normalises `now` to UTC. The queue did not, so a naive `now`
made `plan` and `claim_job` disagree, and a non-UTC `now` made the queue
compare wall clock text instead of instants.

- `src/product_hunter/db/jobs.py`: `_now` converts an aware value to UTC and
  treats a naive value as UTC, the way `scheduler._utc` already does. That
  is the only change in that file for this finding.
- The existing job tests must still pass unchanged.
- A test in the scheduler suite: `plan` and `claim_job` agree on what is
  due when the caller passes a naive `now`.

### R4-8, one bad schedule stopped every other schedule

A `run_id` that does not exist raised out of the tick, after the schedules
before it had already been materialised.

- Every `run_id` is checked BEFORE the loop, so a tick either does all of
  its work or none of it.
- The error names the schedule whose `run_id` does not exist.
- A test: a scheduler with one good schedule and one schedule naming a
  missing run raises, and the good schedule's job was not created.

### R4-9, three reachable branches had no test

- The payload that cannot be JSON serialised raises `ScheduleError`.
- A window that holds no grid point returns no occurrences.
- A naive `now` is read as UTC.
- One test each.

### R4-10, lean: the backoff knobs are config nobody sets

`backoff_base_seconds`, `backoff_factor` and `backoff_max_seconds` are
constructor arguments on `Scheduler`, and nothing in the repository sets
them. The policy is fixed by the contract: a 30 second base, a factor of
two, a one hour cap.

- They become module constants next to `_MAX_PAYLOAD_BYTES`, and the
  constructor loses the three arguments.
- `backoff_seconds(attempt)` and the ladder it returns do not change.

### The gate for round 4

`uv run pytest` must pass. The 24 existing scheduler tests, and every test in
`tests/test_db_jobs.py` and `tests/test_db_worker.py`, must pass unchanged.
Report the output verbatim.

## Round 5: the second review findings

The review node came back with seven findings: two Must fix, three Should fix,
two Nice to have. Fold in the five that matter. The two Nice-to-have items
are accepted as limits and are NOT to be changed.

NOTE: this prompt file is at `docs/reasonix/prompts/phase-2-scheduler-implement.md`.

THE RADIUS DOES NOT GROW. `src/product_hunter/fetch.py` is NOT in the radius,
so the duplicated UTC rule stays where it is, and `plan` and `overdue` keep
sorting in Python.

### R5-1, a content conflict aborted the whole tick (Must fix)

When an occurrence already holds a job with different content, the tick
raised and stopped. It raised again on every tick for as long as the stale
occurrence sat inside the catch-up window, so one stale schedule stopped
every other schedule from getting work for up to 24 hours.

- A conflict is reported per occurrence, the way a blocked schedule is, and
  the tick keeps going.
- A new frozen dataclass `ConflictedSchedule` with the fields `name: str`,
  `occurrence: str`, `detail: str`, following the pattern of
  `BlockedSchedule` and `DroppedSchedule`.
- `ScheduleTick` gains the field `conflicts: tuple[ConflictedSchedule, ...]`.
- The tick catches `JobConflict` from `enqueue_job`, records the conflict,
  logs a WARNING naming the schedule and the occurrence, and continues with
  the next occurrence. The job that already holds the key is left exactly as
  it is. The occurrence is immutable, so the old job keeps running the old
  work; the report and the log are how the operator finds out.
- A test: tick a schedule, change its stage and payload, tick again, and
  assert that the second tick does NOT raise, that the conflict is in
  `tick.conflicts`, that the other schedule in the same scheduler still got
  its job, and that the stored job still holds the old stage.

### R5-2, a tick was not all-or-nothing (Must fix)

The connection is in autocommit, so `enqueue_job`'s INSERT committed per
occurrence. A tick that raised left the earlier occurrences written. Only the
missing-run pre-check was all-or-nothing.

- The tick runs inside one transaction: `BEGIN IMMEDIATE` at the start and
  `COMMIT` at the end, with a `ROLLBACK` on any exception, the way
  `jobs.py` does it in `claim_job` and `fail_job`.
- The tick must also work when the caller already has a transaction open,
  for example inside a `JobContext.transaction()`. Use `conn.in_transaction`
  to decide: when a transaction is already open the tick joins it and does
  not commit or roll it back; when the tick opened it, the tick commits and
  rolls it back.
- A test: a tick that raises leaves the jobs table exactly as it was.
- A test: a tick inside an open transaction joins it and does not commit
  it.

### R5-3, plausible values died as OverflowError instead of ScheduleError

`Schedule.__post_init__` checks lower bounds only, so a priority of `10**30`
built fine and then died as an unhandled `OverflowError` from inside sqlite3,
and an interval or offset of `10**18` died as an `OverflowError` from
`timedelta`.

- A priority must fit a signed 64-bit integer, the range SQLite stores.
  Anything outside it is refused with `ScheduleError`.
- An `interval_seconds` or an `offset_seconds` that `timedelta` cannot
  represent is refused with `ScheduleError`. Build the `timedelta` in the
  check and turn the `OverflowError` into `ScheduleError`.
- `lookback_seconds` on the `Scheduler` gets the same treatment.
- Three tests, one for each.

### R5-4, the payload was kept by reference

`Schedule.__post_init__` validated a copy of the payload but stored the
caller's mutable mapping, so a caller that mutated it between ticks silently
changed the content of a frozen schedule, which is what produces a content
conflict.

- Store a copy: `object.__setattr__(self, "payload", dict(self.payload))`
  after the serialisation check.
- A test: mutating the mapping the caller passed does not change the
  schedule afterwards.

### R5-5, the two Nice-to-have items are NOT to be changed

- The UTC normalisation rule stays written twice, once in `jobs.py` and once
  in `scheduler.py`. `src/product_hunter/fetch.py` is outside the radius.
- `plan` and `overdue` keep loading the queued rows and sorting them in
  Python. At the measured load that is nothing.

### The gate for round 5

`uv run pytest` must pass. The 37 existing scheduler tests and every test in
`tests/test_db_jobs.py` and `tests/test_db_worker.py` must pass unchanged.
Report the output verbatim.

## Round 6: the third review findings

The review node came back with one Must fix, two Should fix, three Nice to
have. Fold in the four code items. The two documentation items are the
orchestrator's job and are listed here only so you do not touch them.

NOTE: this prompt file is at `docs/reasonix/prompts/phase-2-scheduler-implement.md`.

THE RADIUS DOES NOT GROW. Do not touch `docs/`, and do not touch
`src/product_hunter/fetch.py`.

### R6-1, a failed tick inside a caller's transaction left a half tick (Must fix)

The tick's comment says it is all or nothing. That is true only when the tick
owns the transaction. When the caller already opened one, a tick that raised
after writing a job left that job in the caller's transaction, and the
caller's COMMIT made it durable.

- When the tick joins a transaction the caller already opened, it uses a
  SAVEPOINT: `SAVEPOINT scheduler_tick` before the work, `RELEASE
  SAVEPOINT scheduler_tick` on success, and on an exception `ROLLBACK TO
  SAVEPOINT scheduler_tick` followed by `RELEASE SAVEPOINT scheduler_tick`.
- The rollback to the savepoint must be inside a try that swallows
  `sqlite3.Error`, so a failed rollback cannot replace the original
  exception with a new one.
- When the tick owns the transaction, the behaviour stays as it is now:
  `BEGIN IMMEDIATE`, `COMMIT`, `ROLLBACK` on an exception.
- Move the COMMIT inside the try block. A COMMIT that fails must roll the
  transaction back rather than leave it open for the next tick to join.
- After a failed tick inside a caller's transaction, the caller's transaction
  is still open and still usable. That is the point of the savepoint.
- Two tests: a tick that raises inside an open transaction leaves the jobs
  table unchanged and the caller's transaction open; a successful tick inside
  an open transaction joins it and leaves the job for the caller to commit.

### R6-2, the overflow guards checked the wrong bound (Should fix)

`offset_seconds=10**12` and `lookback_seconds=10**12` pass the `timedelta`
check, because `timedelta` holds 10¹² seconds, and then die with a raw
`OverflowError: date value out of range` from the datetime arithmetic.
`max_attempts` reaches SQLite with no range check at all, so `10**30` raises
`OverflowError: Python int too large to convert to SQLite INTEGER`.

- The bound for `offset_seconds` and `lookback_seconds` is what `datetime`
  can represent, not what `timedelta` can. Use the range between
  `datetime.min` and `datetime.max` expressed in seconds, and refuse a
  value outside it with `ScheduleError`.
- `max_attempts` is range checked against the signed 64-bit range, the same
  bound as the priority, and refused with `ScheduleError` outside it.
- The existing tests pin 10¹⁸. They must pin the new bound as well, so a
  value of 10¹² is refused for the right reason.

### R6-3, a schedule edit to the priority or the attempt ceiling was ignored (Should fix)

The queue's conflict check compares the stage and the payload only. A
schedule whose priority or `max_attempts` changed kept the old values in the
stored job, and the tick reported the occurrence as `existing` and logged
nothing.

- After `enqueue_job` returns a job that already existed, the tick compares
  it with the schedule: the stage, the priority, the attempt ceiling and the
  scheduled time must match.
- A job that differs is reported the way a content conflict is reported: a
  `ConflictedSchedule` entry naming the schedule, the occurrence, and the
  difference, plus a WARNING. It is not added to `existing`, because the
  stored job is not the job the schedule describes.
- A job the tick just created is not compared, since it matches by
  construction.
- A test: tick a schedule, change its priority, tick again, and assert that
  the second tick reports a conflict naming the priority, that the stored job
  still holds the old priority, and that the tick did not raise.

### R6-4, the payload copy was shallow

`dict(self.payload)` copies the top level only, so a caller that mutates a
nested value still changes the "frozen" schedule.

- Copy the payload deeply, with `copy.deepcopy`, and keep the copy on the
  schedule. `copy` is standard library.
- A test: mutating a NESTED value of the mapping the caller passed does not
  change the schedule afterwards. The existing test only mutates a top level
  key; add the nested case.

### R6-5, one dead assignment in the test file

`tests/test_db_scheduler.py` has an unused local, the `job =` on the line
that calls `scheduler.tick(now=NOW).created[0]`. Drop the assignment, keep
the call.

### The gate for round 6

`uv run pytest` must pass. The 43 existing scheduler tests and every test in
`tests/test_db_jobs.py` and `tests/test_db_worker.py` must pass unchanged.
Report the output verbatim.

## Round 7: the fourth review findings

The review node found one Must fix, two Should fix, two Nice to have. Fold in
all five. The round 6 conflict comparison is the problem, so read R7-1
carefully.

NOTE: this prompt file is at `docs/reasonix/prompts/phase-2-scheduler-implement.md`.

THE RADIUS DOES NOT GROW. Do not touch `docs/`.

### R7-1, the conflict check fired on changes the queue itself made (Must fix)

The tick compares the stored job with the schedule, but two of the compared
columns are written by the queue, not by the schedule:

- `fail_job` rewrites `scheduled_for` to now plus the backoff, and
  `recover_expired_leases` rewrites it to now. So any job that failed once,
  or whose lease expired, was reported as a conflict on every tick for a day:
  "scheduled_for is 06:00:30, the schedule says 06:00:00". The job then
  appeared in neither `created` nor `existing`.
- `promote` rewrites `priority`. So a job the operator promoted to P0 was
  reported as a conflict: "priority is 50, the schedule says 20".

Fix the comparison so it only reports a difference the SCHEDULE caused:

- The `scheduled_for` clause is deleted. The same idempotency key means the
  same instant, so only the queue can move that column, and the occurrence
  identity is already compared through the payload.
- The `priority` clause fires only when the stored job sits BELOW the
  schedule's priority, `job.priority < schedule.priority`. That is the
  schedule raising its priority after the occurrence was created. A stored
  job at the same priority matches, and a stored job ABOVE the schedule's
  priority was promoted deliberately by the queue and is not a conflict.
- The `stage` and `max_attempts` clauses stay. Nothing else writes those
  columns.
- Add `run_id` to the comparison: `job.run_id != schedule.run_id` is a
  difference, and nothing else writes that column, so it cannot misfire. A
  schedule re-pointed at another research run must be reported, because the
  stored job books the work against the old run.
- Three new tests: a job whose `scheduled_for` was moved by `fail_job` is
  NOT reported as a conflict and the tick does not raise; a job promoted to
  P0 is NOT reported as a conflict; a schedule whose `max_attempts` changed
  and one whose `run_id` changed ARE reported as conflicts.

### R7-2, the two clauses nothing pinned

`max_attempts` was compared but no test failed when the clause was removed.
The new test in R7-1 pins it.

### R7-3, the backoff exponent was uncapped

`backoff_seconds` computes `2 ** (attempt - 1)`, and an attempt of 10**18
tries to build an enormous integer. `max_attempts` may be as large as the
signed 64-bit range.

- Cap the exponent at 32: `min(max(1, attempt) - 1, 32)`. Thirty times
  2**31 is far past the one hour cap anyway, so the ladder does not change
  for any attempt a real job reaches.
- A test: `backoff_seconds(10**18)` returns the cap, and does not hang.

### R7-4, lean: `_check_duration` is dead weight

`_check_datetime_span` is strictly tighter than the `timedelta` range, so
everything `_check_duration` rejects is rejected anyway.

- `interval_seconds` is checked with `_check_datetime_span`.
- `_check_duration` is deleted.
- The existing test that pins the refusal still passes.

### The gate for round 7

`uv run pytest` must pass. The 48 existing scheduler tests and every test in
`tests/test_db_jobs.py` and `tests/test_db_worker.py` must pass unchanged.
Report the output verbatim.

## Round 8: the fifth review findings

The review node found no Must fix and three Should fix, plus one dead branch.
Fold in all four.

NOTE: this prompt file is at `docs/reasonix/prompts/phase-2-scheduler-implement.md`.

THE RADIUS DOES NOT GROW. Do not touch `docs/`.

### R8-1, `promote` could lower a priority and the tick then called it a conflict

`promote` takes any integer, so `promote(job, 5)` on a job whose schedule
says 20 left the stored job below the schedule's priority. The next tick
reported it as a conflict: "priority is 5, the schedule says 20". The job
then appeared in neither `created` nor `existing`, and the conflict repeated
on every tick.

- `promote` refuses a priority LOWER than the job's current one. A promotion
  that does not promote is a demotion, and a demotion is not what this method
  is for. The error names the current priority and the refused one.
- A priority EQUAL to the job's current one is allowed, so promoting an
  already-P0 job is a no-op rather than an error.
- The `SELECT` that `promote` already runs gains the `priority` column, so
  the comparison needs no second query.
- Two tests: a demotion is refused and the stored job keeps its priority, and
  the next tick after a refused demotion reports no conflict.

### R8-2, the numeric fields were not all integers

`max_attempts` and `catchup_limit` were range checked but their checked
value was thrown away, so a float survived: `max_attempts=2.5` was stored as
a REAL in an INTEGER column, read back as 2, and every later tick reported a
conflict about it. `catchup_limit=2.5` killed the tick with a raw
`TypeError`.

- Every numeric field on a schedule and on the scheduler is an INTEGER. The
  fields are: `interval_seconds`, `offset_seconds`, `max_attempts`,
  `catchup_limit`, `priority`, and on the scheduler `lookback_seconds` and
  `max_occurrences`.
- One helper does the work: it refuses a bool and any non-int with
  `ScheduleError`, checks the signed 64-bit range, and returns the value to
  store.
- The schedule stores the checked value with `object.__setattr__`, so a float
  can never reach SQLite. `Schedule` is frozen.
- The scheduler's two fields are checked the same way in its constructor.
- Tests: a float `max_attempts`, a float `catchup_limit` and a float
  `interval_seconds` are each refused with `ScheduleError`, and the stored
  `max_attempts` reads back as an `int`.

### R8-3, the backoff hook could kill the worker with a huge delay

`Worker._delay` clamps the hook's result to at least one second, but not to
a maximum. A hook returning `10**18` reached `fail_job`, which built a
`timedelta` and raised `OverflowError` out of `run_once` and out of
`run_forever`, so the worker loop died and the job stayed leased.

- The hook's delay is clamped to a maximum as well. The maximum is a module
  constant in `worker.py`, one day in seconds, named so the reason is clear.
  A retry delay longer than a day is a bug in the hook, and the worker does
  not park a job for longer than that.
- The clamp applies only to the hook path. With no hook the worker keeps its
  flat `backoff_seconds` exactly as before.
- A test: a hook returning `10**18` does not raise, the job is still
  requeued, and the delay is the maximum.

### R8-4, lean: the stage clause in `_difference` was dead

`enqueue_job` raises the conflict on a stage mismatch first, so the tick
catches it and the stage clause in `_difference` is never reached.

- Delete the stage clause.
- The queue's own conflict message still reports a stage change, so nothing
  observable is lost.

### The gate for round 8

`uv run pytest` must pass. The 53 existing scheduler tests and every test in
`tests/test_db_jobs.py` and `tests/test_db_worker.py` must pass unchanged.
Report the output verbatim.

## Round 9: the sixth review findings

The review node found one Must fix, two Should fix, and one gap. All four
are one line each. Fold them in.

NOTE: this prompt file is at `docs/reasonix/prompts/phase-2-scheduler-implement.md`.

THE RADIUS DOES NOT GROW. Do not touch `docs/`.

### R9-1, `promote` could reprioritise a job that was leased in between (Must fix)

`promote` reads the job's status in one statement and then updates the row
with `WHERE id = ?` alone. A worker that leases the job in between is still
reprioritised: `promote` returned a job with `status='leased'` and
`priority=50`. The contract says a leased job cannot be moved.

- The update becomes `UPDATE jobs SET priority = ? WHERE id = ? AND status =
  'queued'`, and the method refuses when the update changed no row.
- The error says the job is no longer queued.
- A test: a job that is leased is refused by `promote`, and its priority is
  unchanged.

### R9-2, the worker's fallback delay was not clamped

The hook's delay is clamped to `[1, 86400]`, but the fallback branch returns
the raw `self.backoff_seconds`. A worker built with `backoff_seconds=10**18`,
or a hook that raises while the flat delay is huge, still reaches
`fail_job` -> `timedelta(seconds=10**18)` and raises `OverflowError` out of
`run_once`, which kills `run_forever`. That is the crash the clamp was added
for, on the path beside it.

- The fallback is clamped with the same bounds as the hook: `min(max(1,
  delay), _MAX_BACKOFF_SECONDS)`.
- A test: a worker built with an enormous `backoff_seconds` does not raise
  when a handler fails, and the job is still requeued.

### R9-3, a paused schedule could veto every other schedule

`_require_runs` checks the `run_id` of every schedule, including the ones
with `enabled=False`. A disabled schedule naming a run that no longer exists
aborts the whole tick, while the tick loop itself treats a disabled schedule
as inert.

- `_require_runs` skips a schedule that is not enabled, the same way the tick
  loop does.
- A test: a scheduler with one good schedule and one disabled schedule whose
  `run_id` does not exist ticks without raising, and the good schedule's job
  is created.

### R9-4, `backoff_seconds` accepted a fractional attempt

`backoff_seconds(2.5)` returned a float delay, and a fractional delay
reaches `timedelta` and the stored `scheduled_for`.

- The attempt must be an integer. A non-integer attempt is refused with
  `ScheduleError`, the same error type the rest of the module uses.
- A test: a fractional attempt is refused, and an integer attempt still
  returns the ladder.

### The gate for round 9

`uv run pytest` must pass. The 58 existing scheduler tests and every test in
`tests/test_db_jobs.py` and `tests/test_db_worker.py` must pass unchanged.
Report the output verbatim.

## Round 10: the seventh review findings

The seventh review returned the verdict `Ship`, with one Should fix and two
Nice to have. Fold in the two that change behaviour or what an operator
reads. The third one is accepted as a limit.

NOTE: this prompt file is at `docs/reasonix/prompts/phase-2-scheduler-implement.md`.

THE RADIUS DOES NOT GROW. Do not touch `docs/`.

### R10-1, the warning named the configured delay, not the delay applied

The worker logs the fallback delay and clamps it afterwards. With
`backoff_seconds=0` the log says "using 0 seconds" while the job waits one
second, and with `backoff_seconds=10**18` it says "using 1000000000000000000
seconds" while the job waits a day. The clamp is invisible in the one log
line that explains it.

- Clamp first, then log the value that was actually applied.
- The message keeps the job id and the reason the hook failed.
- A test: with a hook that raises and a flat delay of 0, the log carries the
  applied delay, which is one second.

### R10-2, the demotion floor was the one guard outside the write

`promote` reads the job's current priority and compares against that stale
read, then writes. Two promotions of the same job can interleave so the
second, lower priority is written after the higher one, and the call still
reports success. The plan says the check and the write are one statement; the
status is, the floor is not.

- The floor moves into the write: `UPDATE jobs SET priority = ? WHERE id = ?
  AND status = 'queued' AND priority <= ?`, with the priority the job had
  when the method read it.
- The method still refuses when the update changed no row, and the error
  still says the job is no longer queued.
- The separate read stays, because it produces the error text and the floor
  value.
- A test: a job whose stored priority is above the requested one is refused,
  and its priority is unchanged.

### R10-3, the duplicated clock helper is accepted as a limit

`scheduler._utc` and `jobs._now` are the same function. The shared helper
belongs in `fetch.py`, which is outside this run's radius, so the duplication
stays. Do not change it.

### The gate for round 10

`uv run pytest` must pass. The 62 existing scheduler tests and every test in
`tests/test_db_jobs.py` and `tests/test_db_worker.py` must pass unchanged.
Report the output verbatim.

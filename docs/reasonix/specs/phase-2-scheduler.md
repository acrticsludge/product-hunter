# Phase 2 scheduler specification

## What this phase adds

A scheduler sits above the SQLite job queue and decides what work exists, in
what order, and when a failed attempt runs again. It creates jobs, reports
overdue work, and owns the priority vocabulary. It does not execute anything:
the existing worker claims and runs the jobs the scheduler writes.

The spec (sections 17, 18, 41.10 and 42) asks for a durable queue that is also
the scheduler of record, one supervised worker, priorities P0 to P4, and a
recurring cadence of collection, processing, clustering, deep investigation and
a morning brief. This phase delivers the deterministic primitives behind that
cadence. The long-running supervisor does not ship yet, because no stage
handlers exist for the scheduled stages, and a running scheduler without
handlers would only spin on jobs it cannot execute.

## Hard constraints

- The database schema does not change. No migration file is added or edited.
- The job system's semantics do not change. `enqueue_job`, `claim_job`,
  `complete_job`, `fail_job` and `recover_expired_leases` keep their current
  behaviour, including lease fencing and the attempt ceiling.
- No scraping, no model routing, no Qdrant, no Obsidian, no Phase 4 research
  gates.
- No new dependency. The module uses the standard library and the existing
  `product_hunter.db` modules.

## State model: why there is no new table

A scheduler normally keeps its own state: the last time each schedule fired,
the next due time, whether a schedule is paused. This phase keeps none of that
in the database. Instead, occurrence times are derived from a fixed grid and
the occurrence itself is the state.

The grid origin is the Unix epoch in UTC. A schedule with interval `S` and
offset `O` fires at `origin + O + k * S` for every integer `k`. Two properties
follow, and both are needed:

1. A tick never depends on what the process remembered. A fresh process, a
   restarted worker and a first run all compute the same occurrence times from
   the same clock, so a restart cannot silently skip or duplicate work.
2. The occurrence time is a complete identity for the work. The idempotency
   key `schedule:<name>:<occurrence-iso>` is unique, and `enqueue_job` already
   refuses a duplicate key. Materialising the same occurrence twice is
   therefore a no-op that returns the existing row.

What is given up: the database cannot answer "when did this schedule last
run" without scanning the jobs table for the schedule's keys, and a schedule
cannot be paused durably. Both are recoverable later from the jobs table, and
both are recorded here as the required interface rather than designed now.

## The contract

### Priority bands

`jobs.priority` is an integer and `claim_job` orders by `priority DESC`, so a
larger number is served first. The scheduler names the bands the spec uses:

| Band | Meaning | Value |
|---|---|---|
| P0 | user-requested continuation | 50 |
| P1 | high-potential investigation | 40 |
| P2 | scheduled validation | 30 |
| P3 | background discovery | 20 |
| P4 | maintenance and reprocessing | 10 |

The values are spaced by ten so a caller can nudge a job inside a band. A job
with no band, the legacy default of zero, sits below every band, which is the
right place for work nobody has classified yet.

### Schedule

A `Schedule` is a recurring definition, not a row in the database.

| Field | Rule |
|---|---|
| `name` | non-empty, unique inside one scheduler; it is part of the idempotency key |
| `stage` | non-empty; the worker dispatches on it |
| `interval_seconds` | an integer of at least 1 |
| `offset_seconds` | an integer of at least 0; shifts the grid, so a daily brief can land at 07:00 UTC |
| `priority` | an integer, one of the bands by default |
| `payload` | merged into the job payload, plus the schedule name and occurrence; at most 65,536 bytes |
| `max_attempts` | an integer of at least 1 |
| `source_id` | optional; when set, the source gate applies |
| `run_id` | optional; when set, the run must already exist |
| `catchup_limit` | optional integer; caps how many missed occurrences one tick materialises |
| `enabled` | when false the schedule produces no work |

Every numeric field is an integer, and an integer SQLite can hold. A float or
a bool is refused rather than rounded, because a value stored in an INTEGER
column as a REAL reads back as a different number. The payload is copied when
the schedule is built, so a caller that mutates its own mapping cannot change
a frozen schedule.

### One tick

`Scheduler.tick(now)` does three things and returns what happened:

1. For every enabled schedule, compute the occurrence times in the catch-up
   window, oldest first.
2. For each occurrence, create the job unless the source gate blocks it.
3. Report the outcome: jobs created, jobs that already existed, blocked
   schedules with a reason, occurrences the cap dropped, and occurrences that
   conflict with a stored job.

The tick is idempotent. Running it twice at the same instant creates one job.

The tick is also all or nothing. It runs inside one transaction, so a tick
that raises leaves the jobs table as it was. When the caller already opened a
transaction, the tick joins it through a savepoint and leaves the commit to
the caller.

One tick materialises at most `max_occurrences` occurrences per schedule, 100
by default. The cap keeps the newest occurrences, because the newest work is
the work that matters, and the tick reports every occurrence the cap dropped.

### Job creation

Each materialised job is written through `enqueue_job`, so it inherits the
existing guarantees: a deterministic id from the idempotency key, a conflict
check when the same key is reused with different content, and a fenced
`scheduled_for` that the worker respects.

The tick always calls `enqueue_job`, even when it already knows the key
exists, so the queue's conflict check runs. A conflict is reported rather
than raised: the stored job keeps its old content, the tick records the
difference and carries on with the next occurrence. One stale occurrence
therefore cannot stop every other schedule from getting work.

| Job field | Value |
|---|---|
| `idempotency_key` | `schedule:<name>:<occurrence isoformat>` |
| `stage` | the schedule's stage |
| `priority` | the schedule's priority |
| `scheduled_for` | the occurrence time, in UTC ISO-8601 |
| `payload` | the schedule's payload plus `schedule` and `occurrence` |
| `max_attempts` | the schedule's attempt ceiling |

The occurrence time is the scheduled time, not the time of the tick. A job
created late is already overdue, and the queue sees it that way.

### Source gate

A schedule that names a `source_id` may not create work for a source that
cannot be used. The gate reads the `sources` table, the same authoritative
mirror `Store.sync_registry` writes, and mirrors the rules
`SourceRegistry.require` already applies:

| Condition | Blocked reason |
|---|---|
| no row for the source | `unknown_source` |
| `enabled` is false | `source_disabled` |
| `terms_status` is not `reviewed` or `permitted` | `terms_not_permitted` |

A blocked schedule creates no job. The reason is reported by the tick, and it
is not recorded in the database: `research_events` requires a `run_id`, and no
table holds scheduler state in this schema. The gap is recorded below.

### Overdue work

`Scheduler.overdue(now)` returns queued jobs whose `scheduled_for` is in the
past, most urgent first. A job scheduled exactly now is due, not overdue.
Overdue work is work the system missed, which after a sleep or a shutdown is
the normal case, and the spec is explicit that missed work runs late rather
than being written off.

### Prioritisation

`Scheduler.plan(now)` returns the queued jobs a worker would claim, in the
order it would claim them: priority descending, then scheduled time, then
creation time, then the job id. `claim_job` orders the same way, down to the
id tiebreak, so the plan and the queue cannot disagree about which job runs
next.

`Scheduler.promote(job_id, priority)` raises the priority of a queued job. It
is how a P0 user continuation overtakes background work that is already
waiting. Three things are refused: a job that does not exist, a job that is
not queued, and a priority below the job's current one. A running job cannot
be reprioritised, because its next attempt is what carries the new priority,
and a demotion is not a promotion. The check and the write are one statement,
so a job the worker leases in between is refused too.

### Retry and backoff

Two paths cover the same need.

The in-place path is the one a worker takes when a handler raises. `fail_job`
already requeues the job with a delay, and the delay is the backoff the
scheduler computes. The worker takes an optional backoff function so the
exponential policy lives in one place instead of a flat constant in the
worker. The default worker behaviour is unchanged.

The policy is exponential: `base * factor ** (attempt - 1)`, capped. The
defaults are a 30 second base, a factor of two and a one hour cap, so the
delays run 30 s, 60 s, 120 s, and stop at one hour.

The new-job path is `Scheduler.schedule_retry(job)`. A job that reached its
attempt ceiling is dead, and the dead row stays as evidence. The scheduler can
then create a fresh job for the same work, scheduled one backoff from now, with
the key `retry:<job id>:<attempt>`. The key makes the call idempotent, so a
repeated retry does not queue the work twice, whatever reason the caller
passes. Only a dead job is retried; retrying a live one would book the same
work twice. The reason describes the request rather than the work, so it is
logged instead of stored in the payload.

The worker bounds what a backoff function returns. A delay below one second
would requeue the job straight back into the queue, and a delay beyond what a
`timedelta` holds would raise out of the worker loop, so both ends are
clamped.

### Cadences

The spec's cadence is expressed as schedule definitions. They are data, and
nothing starts them in this phase.

| Schedule | Interval | Priority | Stage |
|---|---|---|---|
| source collection | 15 min | P3 | `COLLECT` |
| evidence processing | 1 h | P2 | `PROCESS` |
| re-clustering and re-evaluation | 6 h | P3 | `CLUSTER` |
| deep investigation | 24 h, offset 02:00 UTC | P1 | `INVESTIGATE` |
| morning brief | 24 h, offset 07:00 UTC | P2 | `BRIEF` |

Times are UTC. A cadence that must land on local time needs the offset
configured for the machine, and the choice of timezone is a configuration
decision this phase does not make.

The stage names come from the spec's autonomous loop. No handler implements
them yet.

Every default schedule caps its catch-up at two rounds. Without that cap the
first tick on a fresh database would backfill a whole day of collection, 126
jobs the operator never asked for, and every one of them would fail and be
retried while no handler exists for `COLLECT`.

## Required interface, not built here

Four things this phase deliberately leaves to later work.

1. **Creating a research run.** `research_runs` needs a unique `public_id` and
   a set of provenance fields that only the run service knows how to fill. The
   scheduler creates jobs, and a schedule may reference a run that already
   exists. A `run_id` that does not exist is refused rather than written.
2. **Durable schedule state.** Pausing a schedule, changing its interval, and
   answering "when did this last run" need a table the schema does not have.
   The scheduler derives occurrence times instead, which covers the current
   needs but not pause and history.
3. **Durable record of a blocked or conflicting occurrence.** Both are
   reported by the tick and logged, and neither is written to the database:
   `research_events` requires a `run_id`, and no table holds scheduler state
   in this schema. When the schema has a place for scheduler events, those
   reports belong there.
4. **A single-instance lock.** The spec asks for one supervised worker with a
   lock, so that two scheduler invocations cannot overlap. The idempotency key
   already stops the second invocation from creating the same job twice, but
   nothing stops two workers from running at the same time. That lock is not
   built here.

## Assumptions

- The database is the only scheduler state, and one process owns it at a time.
  The single-instance lock the spec asks for is not implemented here.
- `scheduled_for` values are compared as text, and the queue compares them as
  text. Every timestamp the scheduler writes is UTC ISO-8601 from
  `datetime.isoformat()`, the same shape `utcnow().isoformat()` produces, so
  the ordering holds. A naive `now` is read as UTC, on both sides of the
  queue.
- The clock is injected. Every entry point takes an optional `now`, and the
  tests use fixed instants.
- A tick holds a write lock for as long as it runs. The queue is written by
  one process at a time, which is the same assumption the lease fencing
  already makes.

## Tests

The suite is `tests/test_db_scheduler.py`, 62 tests. It covers the behaviour
the task named, and the behaviour the reviews found:

| Area | What it pins |
|---|---|
| priority bands | P0 first, then P1 to P4, and `claim_job` agrees |
| overdue work | a past job is overdue, a future job is not |
| the backoff ladder | 30, 60, 120 seconds, capped at an hour |
| the worker's backoff hook | the delay reaches `fail_job`, and a broken hook falls back |
| duplicate occurrences | two ticks at the same instant create one job |
| a restart | a second scheduler over the same database creates nothing |
| blocked and disabled schedules | unknown, disabled and unreviewed sources, and a disabled schedule |
| a scheduled job | stage, priority, payload and scheduled time |
| the queued to running lifecycle | queued, leased with a running attempt, succeeded |
| a dead job | a fresh job one backoff out, the dead row kept |
| promotion | P0 overtakes the queue, a demotion and a leased job are refused |
| the cadence grid | quarter hour, hour, 07:00 UTC, and a two-round catch-up cap |
| the catch-up window | a long sleep does not flood the queue |
| the transaction | a tick that raises leaves the table unchanged, a joined transaction is not committed |
| input validation | a float, a bool, an oversized value and a missing run are refused |
| the report | blocked, dropped and conflicting occurrences are all reported and logged |

The queue's own tests, `tests/test_db_jobs.py` and `tests/test_db_worker.py`,
still pass unchanged, apart from the two lines the review asked for in
`jobs.py`: the claim order gained an id tiebreak, and the queue reads the
clock the way the scheduler does.

## Out of scope

Scraping, model routing, Qdrant, Obsidian, Phase 4 research gates, a CLI
command, a supervisor process, a single-instance lock, a system task
registration, and any change to the schema.

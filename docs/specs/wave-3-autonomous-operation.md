# Wave 3: autonomous operation

## 1. The problem

Product Hunter can run a pipeline once. It cannot run itself for a week. The
repository already holds the pieces a supervised worker needs: a fenced SQLite
job queue, a deterministic scheduler, an operating system single instance lock,
lease renewal, durable checkpoints, a transactional outbox. But nothing
assembles them into a process that keeps running, notices what it missed, and
says honestly why it stopped.

Wave 3 adds the operation layer. The operation layer is the thing that turns
"a pipeline that can run" into "a system that can keep running".

## 2. Scope

Twenty capabilities, grouped into six modules:

| Group | Capabilities |
|---|---|
| Process | supervised worker process, single instance lock, graceful shutdown, crash recovery |
| Scheduling | scheduler loop, priority queues, research cadence, missed work recovery, research starvation prevention |
| Execution | job claiming, lease renewal, lease expiration recovery, checkpoint/resume, retry policy, dead letter handling |
| Bounds | budget enforcement, per source rate limiting, global concurrency limits |
| Observability | worker health/heartbeat, operation events, status command |
| Upkeep | maintenance jobs |

## 3. What already exists and is reused

| Existing | Module |
|---|---|
| fenced job queue, leases, retry, dead status | `product_hunter.db.jobs` |
| deterministic UTC-grid scheduler, `Priority` bands | `product_hunter.db.scheduler` |
| lease fence shared by checkpoints and the research gate | `product_hunter.db.leases` |
| durable versioned checkpoints | `product_hunter.db.checkpoints` |
| transactional outbox for projections | `product_hunter.db.outbox` |
| supervised worker, heartbeat, shutdown handlers | `product_hunter.runtime.worker` |
| operating system file lock | `product_hunter.runtime.lock` |
| worker identity, JSONL event sink | `product_hunter.runtime.identity`, `product_hunter.runtime.events` |
| budget dimensions, decisions, refusal | `product_hunter.research.budget` |
| collection handler and its per source pacing | `product_hunter.collection.orchestrator` |

Wave 3 writes no second queue, no second lock, no second budget model and no
second event format. It composes what is there.

## 4. Cadence

The spec (§18) says the exact intervals are configurable and that scheduling must
not start a fresh process per interval. So the intervals live in
`config/cadence.yaml` and the business logic never mentions a number.

Cadence primitives, and the beat each one produces:

| Primitive | Beat | Default stage | Default interval |
|---|---|---|---|
| `frequent` | source collection | `COLLECT` | 15 min |
| `periodic` | evidence processing | `PROCESS` | 1 hour |
| `periodic` | clustering and re-evaluation | `CLUSTER` | 6 hours |
| `periodic` | deep investigation | `INVESTIGATE` | 24 hours, offset 2 h |
| `daily` | daily brief | `BRIEF` | 24 hours, offset 7 h |
| `periodic` | maintenance | `MAINTENANCE` | 1 hour |

A beat is: name, kind, stage, interval, offset, priority band, catch-up limit,
attempt ceiling, enabled flag. The kind is descriptive: `frequent`, `periodic`
and `daily` all reduce to "every N seconds on a fixed UTC grid with an offset",
which is what `db.scheduler.Schedule` already does. The kind exists so an
operator can read the file and see which beats are hot, and so a future
primitive that is not a plain interval has a place to live.

The maintenance beat is the one beat that is not turned into a queued job. The
operation layer runs the upkeep itself on that beat's interval, so queueing it
as well would run the same pass twice. The beat stays in the file as the
operator-visible knob for how often the upkeep runs, and the engine reads its
interval from the file. The five work beats (COLLECT, PROCESS, CLUSTER,
INVESTIGATE, BRIEF) are the queued schedules, and they are what
`default_schedules()` returns.

`db.scheduler.default_schedules()` reads `config/cadence.yaml`. The numbers
leave the Python source; the function keeps its name and its behaviour.

Catch-up is bounded by `catchup_limit`, so a laptop that was asleep for a week
does not enqueue a week of collection rounds. What it does enqueue is reported
as missed work.

## 5. Priority

The spec's bands (§41.10) are the ones `db.scheduler.Priority` already holds:

| Band | Meaning | Value |
|---|---|---|
| P0 | user continuation | 50 |
| P1 | high potential investigation | 40 |
| P2 | scheduled validation | 30 |
| P3 | background discovery | 20 |
| P4 | maintenance and reprocessing | 10 |

### Starvation

Strict priority order starves the low bands: an endless stream of P0 work
keeps P4 maintenance from ever running. The queue therefore ages a job that has
been due and unclaimed.

The effective priority of a queued job is

```
effective = priority + min(max_boost, floor(due_seconds / aging_seconds))
```

`aging_seconds` and `max_boost` are configuration. The guarantee:

> A job that has been due for `max_boost * aging_seconds` reaches effective
> priority `priority + max_boost`. With `max_boost` at least the width of the
> band spread (40), such a job outranks every band, and the tie is broken by
> the older `scheduled_for`, so it is claimed before any newer job of any band.

So no band can starve another band permanently. The proof is in
`tests/test_autonomy_engine.py::test_a_low_band_job_is_not_starved`.

Aging changes the claim order only when the queue is configured to age it. The
default for `claim_job` is no aging, so the existing queue behaviour and its
tests are untouched.

## 6. Termination states

The spec (§41.2) defines the run state machine, and says budget exhaustion is
a completion reason and not a stage. The operation layer uses the spec's names:

| State | Meaning | Fatal |
|---|---|---|
| `COMPLETED` | the work finished | yes |
| `BUDGET_EXHAUSTED` | a hard budget limit was reached | yes, and it is a success |
| `BLOCKED` | a gate refused the work (tool gate, source terms, missing handler) | yes, until the operator unblocks it |
| `RETRYABLE_FAILURE` | a failure that will be tried again (a condition, not an end) | no |
| `PERMANENT_FAILURE` | a failure that will not be tried again | yes |
| `CANCELLED` | an operator cancelled the operation | yes |
| `PAUSED` | the process was asked to stop and can be resumed | yes, and it is resumable |

`INTERRUPTED` is derived, as the spec says: a job whose lease expired without a
terminal transition is interrupted work, not a stored state.

Budget exhaustion is never reported as a failure. The engine stops claiming new
work, records `BUDGET_EXHAUSTED` with the dimension that ran out, and exits
cleanly.

## 7. Bounds

### Budget

`product_hunter.research.budget` owns the budget model. Wave 3 adds a persisted
ledger so an operation's spend survives a crash: a laptop that dies at 80% of
its budget comes back at 80%, not at 0%.

The ledger lives in `operation_budget`, keyed by operation, scope and dimension.
Scopes mirror the contract: the operation itself, a stage, a source.

The operation itself is charged its own wall time, every cycle, idle cycles
included, because an operation that sat there for an hour spent an hour of wall
time. The shipped budget bounds one session to an hour; an operator who wants
a longer session raises the number. A handler charges the dimensions it knows
through the same ledger.

A job whose stage has no handler is not failed and does not burn an attempt. It
is deferred to a later time, counted as blocked, and reported in the status.
The operation keeps running, so one unwired stage does not stop the others.

### Per source rate limiting

A source is not contacted more often than its own interval allows. The pacer
keeps `next_allowed_at` per source in `source_pacing`. A job whose payload
names a source that is not due yet is put back to `queued` with a later
`scheduled_for`; it is not failed and it does not burn an attempt.

### Global concurrency

The number of leased jobs in the database, across every process, is the number
of jobs in flight. The engine refuses to claim when that count has reached the
configured limit. The limit is global because the count is global.

## 8. Recovery

The spec (§41.9) lists what a worker does when it starts after a crash:

1. detect expired leases
2. identify interrupted jobs
3. validate the latest checkpoints
4. reconcile partial output by hash and idempotency key
5. requeue unfinished work
6. resume by priority and budget

`product_hunter.autonomy.crash` implements that sequence and returns a report.
It wires `db.checkpoints.load_valid_checkpoint`, which until now had no caller.

### The crashes that are simulated

| Crash | What must hold afterwards |
|---|---|
| worker killed during a job | the job is leased, not done; the lease expires; the job is requeued and runs again |
| worker killed after an external fetch | the fetch is recorded; the retry does not fetch the same URL twice |
| worker killed during a database transaction | the transaction is rolled back; no partial write survives |
| worker killed after a commit | the job stays done; recovery does not run it again |
| worker killed before the projection | the outbox task stays pending; the projection is built again |
| an expired lease | the job is requeued and its attempt is recorded as lost |
| a duplicate worker | the second worker is refused the lock, or the lease fence stops the duplicate write |
| a stale worker write | the write is refused with `LeaseLost` |

Each is a test in `tests/test_autonomy_recovery.py`. The crash tests use a
child process that calls `os._exit(9)`, so nothing is cleaned up on the way
out.

## 9. Observability

- `worker_health`: one row per worker, with its heartbeat, its current job and
  its counters. A worker that stops heartbeating is marked stale.
- `operation_runs`: one row per operation, with its state, its reason and its
  counters.
- `operation_events`: an append-only transition log.
- `ph autonomy status`: the operation state, the queue depth by band, the dead
  letter count, the budget spend and the worker health, as JSON.

## 10. Maintenance

Maintenance is a stage like any other, at P4, on the cadence. It runs:

- expired lease recovery for jobs and for projections
- the health sweep that marks silent workers stale
- the dead letter review that reports and, where the failure is retryable,
  re-queues a dead letter
- the checkpoint prune that keeps the checkpoint table bounded

Maintenance never deletes a job row. A dead letter is parked, counted and
reported; only an explicit operator action moves it.

## 11. What this wave does not do

- No distributed queue. The SQLite queue is the queue.
- No Redis, no Temporal, no BullMQ. The current architecture does not need
  them and the spec does not ask for them.
- No assumption that the laptop is up. Every beat is derived from a fixed UTC
  grid, so a machine that was asleep catches up when it wakes.
- No stage handler is invented. The operation layer dispatches to the handlers
  it is given. The collection handler that Wave 2 wrote is wired in; the other
  Wave 2 subsystems stay unwired, as they were, and the report says so.

## 12. Configuration

| File | Holds |
|---|---|
| `config/cadence.yaml` | the beats: kind, stage, interval, offset, priority, catch-up limit |
| `config/autonomy.yaml` | poll and lease timings, concurrency limit, priority aging, the operation budget, per source pacing intervals |

Neither file is a secret and neither holds a key.

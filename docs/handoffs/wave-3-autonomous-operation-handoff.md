# Handoff: wave 3 autonomous operation

RUN_ID: w3a-20261022-autonomy
Worktree: `C:/Anubhav/Web Dev Projects/product-hunter-w3a`
Branch: `wave3/autonomous-operation`
Base commit: `00a6ca3` (main)

## The repository

| Entry point | What it is |
|---|---|
| `src/product_hunter/cli.py` | the `ph` command: fetch, extract, eval, report, db, project, bench, autonomy |
| `src/product_hunter/runtime/__main__.py` | the supervised worker process (`python -m product_hunter.runtime`) |
| `src/product_hunter/autonomy/__main__.py` | the supervised operation process (`python -m product_hunter.autonomy`) |
| `src/product_hunter/db/store.py` | the SQLite store; `Store.open` migrates the schema |
| `src/product_hunter/db/migrations.py` | the migration runner; files in `db/migrations/` in numeric order |

Modules the operation layer sits on:

| Module | What it owns |
|---|---|
| `db/jobs.py` | the fenced queue: claim, renew, complete, fail, defer, aging |
| `db/scheduler.py` | the UTC grid, the `Priority` bands, `default_schedules()` |
| `db/checkpoints.py` | durable versioned checkpoints, the lease fence |
| `db/outbox.py` | the transactional outbox for the projection |
| `db/leases.py` | the shared fence: worker id plus lease epoch |
| `runtime/worker.py` | `WorkerRuntime`: claim, heartbeat, retry, shutdown handlers |
| `runtime/lock.py` | the operating system single instance lock, `lock_path` |
| `runtime/events.py` | the JSONL event sink |
| `research/budget.py` | the budget dimensions, decisions and refusal |
| `collection/orchestrator.py` | the collection handler and the per source pacing |

## What this wave added

| File | What it is |
|---|---|
| `src/product_hunter/autonomy/cadence.py` | reads `config/cadence.yaml` into schedules |
| `src/product_hunter/autonomy/states.py` | the operation states and the failure classifier |
| `src/product_hunter/autonomy/budget.py` | the persisted budget ledger over the research budget model |
| `src/product_hunter/autonomy/limits.py` | the source pacer and the global concurrency limiter |
| `src/product_hunter/autonomy/health.py` | the worker health rows and the stale sweep |
| `src/product_hunter/autonomy/crash.py` | the startup recovery sequence and the resume point |
| `src/product_hunter/autonomy/maintenance.py` | the upkeep pass and the dead letter review |
| `src/product_hunter/autonomy/engine.py` | `OperationEngine`: the cycle that schedules, claims and accounts |
| `src/product_hunter/autonomy/__main__.py` | the supervised operation process |
| `db/migrations/0008_autonomy.sql` | `worker_health`, `operation_runs`, `operation_events`, `operation_budget`, `source_pacing` |
| `config/cadence.yaml` | the beats: interval, offset, priority, catch-up limit |
| `config/autonomy.yaml` | poll and lease timings, concurrency, aging, budget, pacing |

Changed existing modules:

| File | What changed |
|---|---|
| `db/jobs.py` | `claim_job` gained `aging_seconds` and `aging_cap`; `peek_job` and `defer_queued_job` are new |
| `runtime/worker.py` | the two aging settings, and `load_handlers` moved here from `runtime/__main__.py` |
| `runtime/lock.py` | `lock_path` moved here from `runtime/__main__.py`, so both entry points take the same file |
| `runtime/__main__.py` | uses the two moved helpers |
| `db/scheduler.py` | `default_schedules()` reads `config/cadence.yaml` through a lazy import |
| `config.py` | `AutonomyConfig` and `load_autonomy_config()` |
| `cli.py` | the `ph autonomy status` command group |

## The commands

```
uv run pytest                                              the code gate
python -m product_hunter.autonomy --once                   one cycle, printed as JSON
python -m product_hunter.autonomy                          the supervised operation
ph autonomy status --path db/product_hunter.db             the state of the operation
```

## The configuration

`config/cadence.yaml` holds the beats. `config/autonomy.yaml` holds the
operation limits. Neither holds a secret.

The shipped `per_operation.wall_time_seconds` is 3600, so a continuous process
stops with `budget_exhausted` after an hour. An operator who wants a longer
session raises that number.

## What is not wired

The Wave 2 subsystems are still unwired, the way they were before this wave.
The shipped entry point registers the upkeep handler and whatever
`PH_WORKER_HANDLERS` names. With no handlers named, the cadence's COLLECT,
PROCESS, CLUSTER, INVESTIGATE and BRIEF jobs are deferred, not failed, and the
operation reports them as blocked.

# Wave 3 plan: autonomous operation

Branch: `wave3/autonomous-operation`. Worktree: `C:/Anubhav/Web Dev Projects/product-hunter-w3a`.

Read `docs/reasonix/specs/wave-3-autonomous-operation.md` first. This plan is
the build order and the API contract.

## 1. Build order

1. `db/migrations/0008_autonomy.sql` — the new tables.
2. `config/cadence.yaml`, `config/autonomy.yaml` — the data.
3. `src/product_hunter/db/jobs.py` — aging, peek, defer.
4. `src/product_hunter/runtime/worker.py` — pass the aging settings to the claim.
5. `src/product_hunter/db/scheduler.py` — read the cadence from the file.
6. `src/product_hunter/autonomy/` — states, budget, limits, health, crash, maintenance, engine, entry point.
7. `src/product_hunter/cli.py` — `ph autonomy status`.
8. Tests.
9. Report.

## 2. The schema

`db/migrations/0008_autonomy.sql`. Four tables. Nothing else changes.

```sql
CREATE TABLE worker_health (
    worker_id      TEXT PRIMARY KEY NOT NULL,
    hostname       TEXT NOT NULL,
    pid            INTEGER NOT NULL,
    status         TEXT NOT NULL DEFAULT 'starting'
                   CHECK (status IN ('starting', 'healthy', 'stopping', 'stale')),
    current_job_id TEXT,
    current_stage  TEXT,
    started_at     TEXT NOT NULL,
    heartbeat_at   TEXT NOT NULL,
    stopped_at     TEXT,
    jobs_completed INTEGER NOT NULL DEFAULT 0,
    jobs_failed    INTEGER NOT NULL DEFAULT 0,
    last_error     TEXT
);
CREATE INDEX idx_worker_health_heartbeat ON worker_health(heartbeat_at);

CREATE TABLE operation_runs (
    operation_id       TEXT PRIMARY KEY NOT NULL,
    mode               TEXT NOT NULL DEFAULT 'continuous',
    state              TEXT NOT NULL DEFAULT 'running'
                        CHECK (state IN ('running', 'completed', 'budget_exhausted',
                                         'blocked', 'retryable_failure',
                                         'permanent_failure', 'cancelled', 'paused')),
    reason             TEXT,
    started_at         TEXT NOT NULL,
    updated_at         TEXT NOT NULL,
    ended_at           TEXT,
    jobs_completed     INTEGER NOT NULL DEFAULT 0,
    jobs_failed        INTEGER NOT NULL DEFAULT 0,
    jobs_dead          INTEGER NOT NULL DEFAULT 0,
    leases_recovered   INTEGER NOT NULL DEFAULT 0,
    missed_occurrences INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE operation_events (
    event_id     TEXT PRIMARY KEY NOT NULL,
    operation_id TEXT NOT NULL,
    event_type   TEXT NOT NULL,
    detail_json  TEXT,
    created_at   TEXT NOT NULL
);
CREATE INDEX idx_operation_events_operation
    ON operation_events(operation_id, created_at);

CREATE TABLE operation_budget (
    operation_id TEXT NOT NULL,
    scope       TEXT NOT NULL,
    dimension   TEXT NOT NULL,
    spent       REAL NOT NULL DEFAULT 0,
    updated_at  TEXT NOT NULL,
    PRIMARY KEY (operation_id, scope, dimension)
);

CREATE TABLE source_pacing (
    source_id        TEXT PRIMARY KEY NOT NULL,
    next_allowed_at  TEXT,
    last_used_at     TEXT,
    use_count        INTEGER NOT NULL DEFAULT 0,
    updated_at       TEXT NOT NULL
);
```

The migration number is 0008. The migration runner applies files in numeric
order, so a new file with the next number is enough.

## 3. The configuration

### `config/cadence.yaml`

```yaml
version: 1
cadence:
  - name: source_collection
    kind: frequent
    stage: COLLECT
    interval_seconds: 900
    offset_seconds: 0
    priority: P3
    catchup_limit: 2
    max_attempts: 3
    enabled: true
  - name: evidence_processing
    kind: periodic
    stage: PROCESS
    interval_seconds: 3600
    offset_seconds: 0
    priority: P2
    catchup_limit: 2
    max_attempts: 3
    enabled: true
  - name: reclustering
    kind: periodic
    stage: CLUSTER
    interval_seconds: 21600
    offset_seconds: 0
    priority: P3
    catchup_limit: 2
    max_attempts: 3
    enabled: true
  - name: deep_investigation
    kind: periodic
    stage: INVESTIGATE
    interval_seconds: 86400
    offset_seconds: 7200
    priority: P1
    catchup_limit: 2
    max_attempts: 3
    enabled: true
  - name: morning_brief
    kind: daily
    stage: BRIEF
    interval_seconds: 86400
    offset_seconds: 25200
    priority: P2
    catchup_limit: 2
    max_attempts: 3
    enabled: true
  - name: maintenance
    kind: periodic
    stage: MAINTENANCE
    interval_seconds: 3600
    offset_seconds: 0
    priority: P4
    catchup_limit: 1
    max_attempts: 3
    enabled: true
```

Those numbers are the ones `db.scheduler.default_schedules()` hard-codes today.
The existing test `tests/test_db_scheduler.py::test_the_cadence_definitions_land_on_the_grid`
asserts them and must keep passing without being edited.

### `config/autonomy.yaml`

```yaml
version: 1
poll_seconds: 1.0
lease_seconds: 60
heartbeat_seconds: 15.0
scheduler_interval_seconds: 30.0
recovery_interval_seconds: 30.0
max_concurrent_jobs: 4
priority_aging:
  interval_seconds: 300
  max_boost: 50
budget:
  per_operation:
    tool_calls_total: 500
    fetch_requests: 200
    model_calls: 200
  per_stage:
    COLLECT:
      fetch_requests: 100
  per_source:
    hn_algolia:
      source_requests_per_source: 100
source_pacing:
  default_min_interval_seconds: 60
  per_source:
    hn_algolia: 30
```

## 4. The queue changes

`src/product_hunter/db/jobs.py`. Three additions and one signature change.

### 4.1 Priority aging

`claim_job` gains two keyword arguments, both defaulting to the current
behaviour:

```python
def claim_job(conn, worker_id, *, lease_seconds=60, now=None,
              aging_seconds: int = 0, aging_cap: int = 0) -> Job | None
```

With `aging_seconds <= 0` or `aging_cap <= 0` the ORDER BY is exactly what it
is today. With both positive, the effective priority of a queued job is

```
priority + min(aging_cap, max(0, floor((now - scheduled_for) / aging_seconds)))
```

measured in seconds from the moment the job became due. The SQL keeps the
`priority DESC, scheduled_for ASC, created_at ASC, id ASC` tiebreak, so the
order stays total and deterministic.

Do the arithmetic in SQL, inside the same `BEGIN IMMEDIATE` transaction the
claim already uses, so the pick and the update stay atomic:

```sql
ORDER BY priority + MIN(:cap, MAX(0, COALESCE(
         CAST((julianday(:now) - julianday(scheduled_for)) * 86400.0 / :aging
              AS INTEGER), 0))) DESC,
         scheduled_for ASC, created_at ASC, id ASC
```

`MIN` and `MAX` with two arguments are the scalar forms in SQLite. `COALESCE`
keeps a row with an unreadable `scheduled_for` from sorting as NULL.

### 4.2 Peek

```python
def peek_job(conn, *, now=None, aging_seconds: int = 0,
             aging_cap: int = 0) -> Job | None
```

The job `claim_job` would pick, without claiming it. It runs the same SELECT
with the same ORDER BY. It is used by the engine to look at the next job
before it decides whether that job may start.

### 4.3 Defer

```python
def defer_queued_job(conn, job_id, *, delay_seconds: int,
                     now=None) -> Job
```

Push a queued job's `scheduled_for` into the future. Only a job with status
`queued` may be deferred. A deferral is not a failure: the attempt counter is
untouched, no error is written, and the job stays in the queue.

### 4.4 The runtime worker

`src/product_hunter/runtime/worker.py`: `WorkerRuntime.__init__` gains
`aging_seconds: int = 0` and `aging_cap: int = 0`, validated as
non-negative, and passes them to `claim_job`. Nothing else changes.

### 4.5 The scheduler

`src/product_hunter/db/scheduler.py`: `default_schedules()` reads
`config/cadence.yaml` and builds the same `Schedule` objects it builds today.

The read must not import the autonomy package from `db`, because `db` is the
lower layer. Put the file reader in `src/product_hunter/autonomy/cadence.py`
and import it lazily inside `default_schedules()`:

```python
def default_schedules() -> list[Schedule]:
    from product_hunter.autonomy.cadence import load_cadence_schedules
    return load_cadence_schedules()
```

`autonomy.cadence` imports `product_hunter.db.scheduler` at module level, so
the lazy import is what keeps the cycle from forming.

When the file is missing, `default_schedules()` raises a `ScheduleError` that
names the file. A missing cadence file is a configuration fault, not a silent
fallback to hard-coded numbers.

## 5. The autonomy package

`src/product_hunter/autonomy/`.

### 5.1 `cadence.py`

```python
CADENCE_NAME = Path("config") / "cadence.yaml"

class CadenceError(ValueError): ...

class CadenceKind(str, Enum):
    frequent = "frequent"
    periodic = "periodic"
    daily = "daily"

class CadenceBeat(BaseModel):       # pydantic, extra="forbid"
    name: str
    kind: CadenceKind
    stage: str
    interval_seconds: int = Field(gt=0)
    offset_seconds: int = Field(default=0, ge=0)
    priority: str = "P3"             # P0..P4
    catchup_limit: int | None = Field(default=None, ge=1)
    max_attempts: int = Field(default=3, ge=1)
    enabled: bool = True

class CadenceConfig(BaseModel):
    version: int = Field(gt=0)
    cadence: list[CadenceBeat]

def load_cadence(path: Path | None = None) -> CadenceConfig
def load_cadence_schedules(path: Path | None = None) -> list[Schedule]
```

`load_cadence_schedules` maps each beat to a `db.scheduler.Schedule`, with
`priority` resolved through `db.scheduler.Priority`. An unknown band name is
refused by name.

The default path is `repo_root() / "config" / "cadence.yaml"`. Use
`product_hunter.config.repo_root()`; it is the one place the repository root
is found.

### 5.2 `states.py`

```python
class OperationState(str, Enum):
    running = "running"
    completed = "completed"
    budget_exhausted = "budget_exhausted"
    blocked = "blocked"
    retryable_failure = "retryable_failure"
    permanent_failure = "permanent_failure"
    cancelled = "cancelled"
    paused = "paused"

TERMINAL_STATES = frozenset({...every state except running...})

class OperationError(Exception): ...

class PermanentError(Exception): ...      # the handler says it will not work again
class BlockedError(Exception): ...        # a gate refused the work

def classify_failure(error: str | None) -> OperationState
```

`classify_failure` reads the failure text and returns `permanent_failure` when
the text names a permanent condition, `blocked` when it names a blocked one,
and `retryable_failure` otherwise. The permanent and blocked markers are fixed
substrings the engine defines, so the classifier and the raisers agree.

`OperationState` values are the spec's (§41.2) run states lowercased, with
`budget_exhausted` added because the spec calls budget exhaustion a completion
reason. `db/migrations/0008_autonomy.sql` holds the same names in lower case,
so the column and the enum agree.

### 5.3 `budget.py`

```python
class OperationBudget:
    def __init__(self, conn, operation_id: str, spec: BudgetSpec) -> None
    def check(self, dimension, quantity, *, stage=None, source_id=None) -> BudgetDecision
    def record(self, dimension, quantity, *, stage=None, source_id=None) -> None
    def spent(self, dimension, *, stage=None, source_id=None) -> float
    def exhausted(self) -> list[str]
```

`BudgetSpec`, `BudgetDecision`, `BudgetDimension` and `BudgetExceeded` come
from `product_hunter.research.budget`. The spend is persisted in
`operation_budget`, so a crash does not buy a fresh allowance.

The scopes are the operation itself, `stage:<stage>` and `source:<source_id>`.
The spec's `per_run` is the operation scope, `per_stage` is the stage scope and
`per_source` is the source scope.

`check` returns the same `BudgetDecision` shape as `BudgetTracker.check`, with
the same fields and the same reasons. `record` writes the spend and then
raises `BudgetExceeded` when the spend left a limit behind, exactly like
`BudgetTracker.record`.

### 5.4 `limits.py`

```python
class SourcePacer:
    def __init__(self, conn, intervals: Mapping[str, int],
                 default_interval_seconds: int = 0) -> None
    def seconds_until_allowed(self, source_id: str, *, now=None) -> int
    def mark_used(self, source_id: str, *, now=None) -> None
    def state(self, source_id: str) -> SourcePacing | None

class ConcurrencyLimiter:
    def __init__(self, conn, limit: int) -> None
    def in_flight(self, *, now=None) -> int
    def at_capacity(self, *, now=None) -> bool
```

`SourcePacer` keeps `next_allowed_at` in `source_pacing`. `mark_used` sets the
next allowed moment to now plus the source's interval. The interval comes from
the mapping the caller passes; the engine builds that mapping from the source
registry (`source.rate_limit.min_interval_seconds`) with the autonomy config
overriding it.

`ConcurrencyLimiter.in_flight` counts the rows with status `leased` across
every worker, so the limit is global rather than per process.

### 5.5 `health.py`

```python
class WorkerHealth:
    def __init__(self, conn, identity: WorkerIdentity, *,
                 stale_after_seconds: int = 90) -> None
    def register(self, *, now=None) -> None
    def beat(self, *, job_id=None, stage=None, now=None) -> None
    def count(self, event: str, *, now=None) -> None   # jobs_completed / jobs_failed
    def mark_stopping(self, *, now=None) -> None
    def sweep(self, *, now=None) -> int                # stale workers
    def workers(self, *, now=None) -> list[WorkerHealthRow]
```

`beat` writes the heartbeat and, when a job is named, the current job and its
stage. `sweep` marks every worker whose heartbeat is older than
`stale_after_seconds` as `stale`, and returns how many it marked.

### 5.6 `crash.py`

```python
class CrashRecoveryReport(BaseModel-ish dataclass):
    leases_recovered: int
    projections_recovered: int
    interrupted_jobs: int
    checkpoints_validated: int
    checkpoints_skipped: int
    requeued: int
    dead_letters: int

def recover(conn, *, now=None, stale_after_seconds=90) -> CrashRecoveryReport
def resume_point(conn, *, run_id, stage, step=None) -> Checkpoint | None
```

`recover` runs the spec's (§41.9) startup sequence: recover the expired job
leases, recover the expired projection leases, count the interrupted jobs,
validate the latest checkpoints with `db.checkpoints.load_valid_checkpoint`,
count the dead letters and report the requeued work.

`resume_point` is the resume path that `db.checkpoints.load_valid_checkpoint`
was waiting for. It returns the newest checkpoint of a run and stage whose
state parses, or None.

The interrupted jobs are the jobs that were leased when the process died. They
are counted from `stage_attempts` rows with status `running` whose job is no
longer leased.

### 5.7 `maintenance.py`

```python
MAINTENANCE_STAGE = "MAINTENANCE"

class MaintenanceReport(BaseModel-ish dataclass):
    leases_recovered: int
    projections_recovered: int
    workers_marked_stale: int
    dead_letters: int
    dead_letters_retried: int
    checkpoints_pruned: int

def run_maintenance(conn, *, now=None, retry_dead_letters=False,
                    max_dead_letter_retries=3) -> MaintenanceReport
def make_maintenance_handler(conn, **options): -> handler for the MAINTENANCE stage
```

The maintenance pass recovers the expired leases, sweeps the stale workers,
reviews the dead letters and prunes the checkpoints older than
`checkpoint_retention_days` for runs that are finished.

A dead letter is a job with status `dead`. The review counts them. It re-queues
one only when `retry_dead_letters` is set, the failure text does not name a
permanent condition, and the job has been retried fewer than
`max_dead_letter_retries` times. The retry uses
`db.scheduler.Scheduler.schedule_retry`, so the new job gets its own
idempotency key and the dead row stays where it is.

Maintenance never deletes a job row.

### 5.8 `engine.py`

```python
class OperationEngine:
    def __init__(self, conn, identity, handlers, *, config: AutonomyConfig,
                 schedules=None, events=None, clock=None,
                 connection_factory=None) -> None
    def start(self) -> None
    def run_once(self) -> OperationStep
    def run_forever(self, stop_event=None) -> None
    def request_stop(self) -> None
    def status(self) -> dict
```

`OperationStep` is a frozen dataclass: what one cycle did. It carries the
counts (claimed, completed, failed, deferred, missed occurrences, leases
recovered), the state the operation moved to, and the reason.

`run_once` does this, in this order:

1. Read the clock. If the operation is in a terminal state, return.
2. Check the budget. When a dimension is over its limit, move the operation to
   `budget_exhausted` with that dimension as the reason, and return. Budget
   exhaustion is a completion, not a failure.
3. Every `scheduler_interval_seconds`, run the scheduler tick. Count the
   occurrences whose moment had already passed when the job was created. Those
   are the missed occurrences: work whose beat came while nothing was running.
4. Every `recovery_interval_seconds`, run the crash recovery pass.
5. Every `maintenance_interval_seconds`, run the maintenance pass.
6. Count the leased jobs. When the count has reached `max_concurrent_jobs`,
   return without claiming.
7. Peek the next due job. When its payload names a source that is not due
   yet, defer it to the source's next allowed moment and peek again. Do this at
   most ten times, then give up the cycle.
8. Claim and run the job through the runtime worker.
9. Record the outcome: the counters, the health row, the operation events.
10. Classify a failure: `permanent_failure` and `blocked` stop the operation,
    `retryable_failure` does not.

`run_forever` loops over `run_once` until a stop is requested, and waits
`poll_seconds` when a cycle found nothing to do.

`request_stop` asks for a graceful stop. The operation ends in `paused`, which
is resumable: the next process picks the queue up where it was left.

`status` returns the operation row, the queue depth by priority band, the dead
letter count, the budget spend and the worker health.

### 5.9 `__main__.py`

`python -m product_hunter.autonomy`.

It takes the single instance lock at the same path the runtime worker uses
(`runtime/worker.lock` under the repository root, or `PH_WORKER_LOCK`), so the
two entry points cannot both run against one database. It opens the store,
builds the engine from the configuration, installs the shutdown handlers, and
runs until a signal arrives.

`--once` runs a single cycle and prints the step as JSON. That is what the
tests and the operator use.

The handlers come from `PH_WORKER_HANDLERS` in the same `module:attribute` form
the runtime worker uses. The `MAINTENANCE` handler is always registered, so a
process with no other handler still does the upkeep.

## 6. The CLI

`src/product_hunter/cli.py` gains a command group:

```
ph autonomy status [--path DB] [--operation ID]
```

It prints one JSON object: the operation state and reason, the counters, the
queue depth by band, the dead letter count, the budget spend per dimension and
the worker health rows. It returns 0 when the operation is healthy or paused,
and 1 when the operation is in a failed state.

The other commands of the CLI stay exactly as they are.

## 7. The tests

Four files. The house style: a plain function per behaviour, a name that says
what must hold, and no test that reaches into a private attribute.

### `tests/test_autonomy_cadence.py`

- the cadence file parses into the beats it declares
- the file's numbers are the ones the schedule grid uses
- `default_schedules()` returns the beats of the file, in file order
- an unknown priority band is refused by name
- a beat with a non-positive interval is refused
- a missing file is refused by name, and nothing falls back to hard-coded numbers

### `tests/test_autonomy_engine.py`

- a tick creates the jobs of the beats whose moment has passed
- a beat whose moment passed while nothing ran is counted as a missed occurrence
- a P0 job is claimed before a P3 job
- a low band job that has waited long enough is claimed before a newer job of a
  higher band, so no band starves another
- the operation stops with `budget_exhausted` when the budget is over, and it
  is not reported as a failure
- a permanent failure ends the operation with `permanent_failure`
- a retryable failure leaves the operation running
- the operation ends with `paused` when a stop is requested
- the engine refuses to claim when the leased job count has reached the limit
- a job for a source that is not due is deferred, not failed, and keeps its
  attempt count

### `tests/test_autonomy_limits.py`

- the pacer refuses a source before its interval has passed and allows it after
- the pacer's state survives a new pacer over the same database
- the concurrency limiter counts the leased jobs of every worker
- the budget ledger refuses the call that would break a limit and records the
  spend that did happen
- the budget ledger survives a new tracker over the same database

### `tests/test_autonomy_recovery.py`

The eight crashes. Use `crash_child` from `tests/conftest.py`: it runs the
child with `os._exit(9)`, so nothing is cleaned up.

- a worker killed during a job leaves the job leased, and the lease recovery
  requeues it
- a worker killed after a fetch does not fetch the same URL twice
- a worker killed during a transaction leaves no partial write
- a worker killed after a commit leaves the job done, and the recovery does not
  run it again
- a worker killed before the projection leaves the outbox task pending
- an expired lease is requeued and its attempt is recorded as lost
- a second worker is refused the lock
- a stale worker cannot complete a job another worker has taken

## 8. The rules that apply to every file

- Type annotations on every public function.
- A docstring on every module and every public function that says why the code
  is the way it is, not what the line does.
- No `assert` in the library. Raise a named error.
- No new dependency. `pydantic`, `yaml`, `httpx` and the standard library are
  what the project has.
- No secret in any file, and no absolute path from this machine in any file.
- The failure text that reaches a database row is masked the way
  `runtime/worker.py` masks it. Reuse `_safe_error` rather than writing
  another masker.
- Every new table gets an index on the column that is queried.

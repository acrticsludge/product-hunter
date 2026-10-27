# Phase 2 worker runtime — definition

Status: defined for the worker runtime track, then corrected to match the
shipped code.
Owner: parallel engineering worker, Wave 1.
Base commit: `205a4ba` (Phase 2 step 1, authoritative SQLite evidence core).

## Goal

One supervised worker process that consumes the SQLite job queue: claim a job,
hold a fenced lease while the handler runs, renew that lease, record the
outcome, retry failures, recover expired jobs, and shut down without leaving a
half written job behind.

SQLite stays the only queue and the only authority. The runtime never keeps
queue state in memory, in a file, or in a chat.

## Constraints

- `db/migrations/**`, the core schema, and the semantics of
  `src/product_hunter/db/**` are read-only for this track. Another session was
  reviewing Phase 2 step 1 while this work ran.
- The runtime calls the public API of `product_hunter.db.jobs`:
  `claim_job`, `renew_lease`, `complete_job`, `fail_job`,
  `recover_expired_leases`, `JobContext`, `JobOutcome`, `LeaseLost`.
- No second queue, no second database, no scheduler cadence, no Qdrant, no
  clustering, no Obsidian, no model routing, no discovery, no scraping.
- Python 3.14, standard library only. No new dependency.

## Shape, and why

SubQ Snap was asked twice before the code was written, and once more at the
review.

| Question | Answer | Confidence |
|---|---|---|
| Lane | `backend-runtime` | 0.97 |
| Risk floor | `medium` | 0.96 |
| Where the process layer lives | new `product_hunter.runtime` package | 0.97 |
| Single instance lock and process entry point now | include | 0.91 |

The runtime owns its own claim, dispatch and completion path instead of
wrapping `product_hunter.db.worker.Worker`. The reason is mechanical: that
class claims the job inside `run_once()` and never hands the claimed job to a
caller, so a supervisor around it cannot renew the lease of a job it is
running. A long handler would lose its lease and get its work stolen.

Some dispatch logic therefore exists in two places: `db.worker.Worker` stays
where it is, untouched, and `runtime.WorkerRuntime` implements the process
semantics that the database layer deliberately does not have.

## Module layout

```text
src/product_hunter/runtime/
    __init__.py     public exports
    identity.py     WorkerIdentity
    events.py       WorkerEvent, EventSink, JsonlEventSink
    lock.py         SingleInstanceLock, LockError
    worker.py       RetryPolicy, WorkerRuntime, install_shutdown_handlers
    __main__.py     python -m product_hunter.runtime
```

## Contracts

### identity.WorkerIdentity

```python
@dataclass(frozen=True)
class WorkerIdentity:
    worker_id: str
    hostname: str
    pid: int
    started_at: str

    @classmethod
    def create(cls, *, prefix: str = "worker", now: datetime | None = None) -> "WorkerIdentity"
    def to_dict(self) -> dict[str, object]
```

`worker_id` looks like `worker-<hostname>-<pid>-<8 hex>`. The token comes from
`secrets.token_hex(4)`, so two processes on the same host never share an id.
The hostname is reduced to `[A-Za-z0-9_-]`. `started_at` is an ISO timestamp.

### events.WorkerEvent

```python
@dataclass(frozen=True)
class WorkerEvent:
    event: str
    worker_id: str
    ts: str
    job_id: str | None = None
    stage: str | None = None
    attempt: int | None = None
    lease_epoch: int | None = None
    status: str | None = None
    duration_ms: int | None = None
    error: str | None = None
    count: int | None = None

    def to_dict(self) -> dict[str, object]
    def to_json(self) -> str
```

One JSON object per line. `EventSink` is a protocol with a single method,
`emit(event)`. `JsonlEventSink(stream)` writes and flushes each line. The
runtime default sink writes to `sys.stderr`.

`to_dict` leaves out the fields that carry no value, so a line holds what the
event actually knows. `count` was added after the first review because
`lease_recovered` has to report how many leases it recovered, and no other
field carried that number.

Event names, all stable strings: `worker_started`, `worker_stopped`,
`worker_error`, `lease_recovered`, `job_claimed`, `job_succeeded`,
`job_failed`, `job_dead`, `job_lease_lost`, `job_outcome_failed`,
`lease_renewed`, `lease_renewal_failed`, `handler_missing`,
`shutdown_requested`, `shutdown_timeout`, `lock_acquired`, `lock_denied`,
`no_handlers`.

### lock.SingleInstanceLock

```python
class SingleInstanceLock:
    def __init__(self, path: str | Path, *, identity: WorkerIdentity | None = None) -> None
    def acquire(self) -> bool      # never blocks
    def release(self) -> None
    def __enter__(self) -> "SingleInstanceLock"
    def __exit__(self, *exc: object) -> None
```

An OS file lock, `msvcrt.locking` on Windows and `fcntl.flock` elsewhere, so
the lock dies with the process instead of leaving a stale flag behind. The
lock file holds the worker id, the pid and the start time for diagnosis.

The specification asks for one supervised worker at a time, and a second
scheduler invocation must not start an overlapping worker. The refusal holds
while the lock file stays the same file. The operating system lock binds to
the file rather than to the path, so a lock file that is deleted and recreated
under a running worker can be locked by a second worker. Nothing in this
project deletes that file, and the database fencing bounds the effect to
duplicated work rather than corruption.

`identity` is an addition to the first contract. A lock cannot derive the
worker id from a path, and the lock file has to name the holder.

`__enter__` raises `LockError` when the lock is refused, because a context
manager that silently does nothing is worse than one that says so. `acquire()`
still returns `False` for a caller that wants to decide.

### worker.RetryPolicy

```python
@dataclass(frozen=True)
class RetryPolicy:
    base_seconds: float = 5.0
    multiplier: float = 2.0
    max_seconds: float = 300.0

    def delay_for(self, attempt: int) -> int
```

`delay_for` returns whole seconds: `base * multiplier ** (attempt - 1)`,
capped at `max_seconds`. The value is passed to `fail_job` as
`backoff_seconds`, which is what re-arms `scheduled_for`. The attempt ceiling
itself stays in the database, in `jobs.max_attempts`.

### worker.WorkerRuntime

```python
class WorkerRuntime:
    def __init__(
        self,
        conn: sqlite3.Connection,
        identity: WorkerIdentity,
        handlers: Mapping[str, Handler],
        *,
        lease_seconds: int = 60,
        heartbeat_seconds: float = 15.0,
        poll_seconds: float = 1.0,
        retry_policy: RetryPolicy | None = None,
        events: EventSink | None = None,
        connection_factory: Callable[[], sqlite3.Connection] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None

    def recover(self) -> int
    def run_once(self) -> JobOutcome | None
    def run_forever(self, stop_event: Event | None = None) -> None
    def request_stop(self) -> None
```

`Handler` is `Callable[[JobContext], dict[str, object] | None]`, the same shape
`db.worker.Worker` uses. A handler writes authoritative rows inside
`context.transaction()`, which asserts the lease before and after the write.

Rules the constructor enforces:

- `lease_seconds >= 1`, `heartbeat_seconds >= 0`, `poll_seconds > 0`.
- `heartbeat_seconds` must be smaller than `lease_seconds` when it is not
  zero. A renewal that lands on or after the expiry cannot renew anything, so
  the job would be lost every time.
- A heartbeat needs its own connection, because a `sqlite3.Connection` belongs
  to the thread that made it. When `connection_factory` is omitted, the runtime
  opens a second connection to the same database file. An in-memory database
  has no file, so a runtime with `heartbeat_seconds > 0` on an in-memory
  database raises at construction instead of silently running without renewal.

### worker.install_shutdown_handlers

```python
def install_shutdown_handlers(runtime: WorkerRuntime) -> Callable[[], None]
```

Routes `SIGINT` and `SIGTERM` to `runtime.request_stop()` and returns a
function that restores the previous handlers. Only valid in the main thread,
so it returns a no-op uninstaller when it cannot install.

## Behaviour

### Poll and claim

`run_forever` recovers expired leases once at startup, then loops: claim a job,
run it, repeat. `claim_job` orders by priority, then schedule time, then
creation time, and it recovers expired leases on every call, so an expired
job is picked up without a separate sweep. When no job is available the loop
waits `poll_seconds`.

With no handlers registered the runtime stays idle and claims nothing, so a
worker started before the handler tracks exist cannot burn the queue.

### A busy database

SQLite allows one writer. When another writer holds the file for longer than
the five second `busy_timeout`, a write raises
`sqlite3.OperationalError: database is locked`. That is a transient
condition, not a fault.

The claim, the completion and the failure record each go through a bounded
retry: three attempts, a quarter of a second apart. Only an error that names
a locked or busy file is retried, and the last attempt is raised to the
caller. `LeaseLost` is not an `OperationalError`, so a lost claim passes
straight through.

When the outcome still cannot be written, the runtime writes nothing further
for that job, reports `job_outcome_failed`, and returns the outcome
`lease_lost`. The job stays leased and the next worker picks it up when the
lease expires. A database error anywhere else in the claim loop is reported as
`worker_error` and the loop keeps polling, because a busy file must not end
the supervised process.

### Lease

A claim increments `lease_epoch` and sets `lease_expires_at`. While the
handler runs, a background thread renews the lease every `heartbeat_seconds`
through `renew_lease`, using its own connection. The heartbeat stops before
the runtime writes the outcome, so a renewal cannot race a completion.

`renew_lease` refuses to renew a lease that already expired, and it refuses a
lease that belongs to another worker or another epoch. Either way the runtime
raises `LeaseLost`.

A renewal that fails for another reason, a busy file for instance, ends the
heartbeat with a `lease_renewal_failed` event and sets the lost flag, so the
runtime stops treating the lease as healthy. A busy file is retried on the
next tick before the flag is set, so a long write transaction in a handler
does not by itself lose the lease.

### Fencing

Every write the runtime makes carries the worker id and the lease epoch. If
the lease was taken over while the handler ran, `complete_job` and `fail_job`
update zero rows and raise `LeaseLost`. The runtime then records
`job_lease_lost` and writes nothing else. A stale worker cannot mark a job
succeeded.

A handler that writes through `context.transaction()` gets the same
protection, because the transaction asserts the lease before the write and
again before the commit.

### Dispatch

`handlers` maps a job stage to a handler. A stage with no handler is a
configuration error: the runtime records the failure with `fail_job` and the
message `no handler for stage '<stage>'`, which matches what
`db.worker.Worker` does today. The job then follows the normal attempt
ceiling.

### Retry

A handler that raises is recorded with `fail_job`, using the error text
`"<ExceptionType>: <message>"` and a backoff from `RetryPolicy`. The database
decides between a requeue and a dead job by comparing `attempts` against
`max_attempts`. The runtime reports `job_failed` with the attempt number, and
`job_dead` when the job reached the ceiling.

### Failure text

A handler can raise an exception whose message carries page text, model output
or a credential. Every exception that the runtime records goes through
`_safe_error` first: the type always stays, a labelled secret such as
`api_key=`, `token:` or `Bearer` is masked, and the message is cut at 300
characters. The mask is best effort, and it is applied to the event stream as
well as to the job row, so the two never disagree.

This is deliberately stricter than `db.worker.Worker`, which stores the raw
message. The runtime is the boundary where handler output reaches the durable
record, so it does the masking.

### Recovery

`recover()` calls `recover_expired_leases` and reports the count as
`lease_recovered`. An expired lease is requeued as work, never as a success.

### Graceful shutdown

`request_stop()` sets the stop event. The loop then stops claiming new jobs
and lets the running handler finish, so the job is completed normally and its
authoritative write is committed. A job enqueued after the stop request is
never claimed.

`run_forever` only reads the stop event it is given. It never writes to it.
`request_stop()` sets the runtime's own event, and the loop watches both.

`python -m product_hunter.runtime` runs a watchdog: if the handler is still
running `shutdown_grace_seconds` after the stop request, the process logs
`shutdown_timeout` and exits with code 3. The exit codes are 0 for a clean
stop, 2 for a refused lock or a broken handler specification, and 3 for a
shutdown that overran the grace period.

The hard exit leaves the write ahead log to SQLite's own crash recovery, and
the abandoned job is recovered when its lease expires. The connection cannot
be closed from the watchdog thread, because it was created with
`check_same_thread=True` and closing it from another thread raises
`sqlite3.ProgrammingError`.

### Identity and events

Every event carries the worker id. The identity is created once per process
and never changes, so a job row that names a worker can be traced back to a
process and a host.

### Idempotency

The runtime does not add a second execution guard, because the database
already holds one: a job is claimed once per lease, a lease carries an epoch,
and a write outside the current lease is refused. What the runtime promises is
that a re-executed job sees the same `idempotency_key`, so a handler that
derives its row id from that key converges on the same row instead of writing
a second one.

A handler that reaches outside the database, an outbound call for instance,
has to be idempotent on its own. A job that is abandoned and recovered will
run its handler again, and the database fence protects the job row but not
that side effect.

## Test matrix

| Case | What it pins down |
|---|---|
| successful job | handler runs, result stored, write applied once, job `succeeded` |
| failed job | handler raises, error stored, job requeued, `job_failed` emitted |
| retry | first attempt fails, second attempt succeeds, `attempts` is 2 |
| job at the attempt ceiling | the job is reported dead and stays dead |
| expired lease | an expired lease is recovered and the job runs, the old attempt reads `lease_lost` |
| stale worker | a stolen lease rejects the write, nothing is completed |
| lease loss | the heartbeat sees `LeaseLost`, the runtime reports `job_lease_lost` and writes nothing |
| graceful shutdown | the stop event ends the loop, the in-flight job completes, later jobs are not claimed |
| duplicate execution | one idempotency key means one job, and a re-execution converges on the same row |
| busy file | the heartbeat survives a locked file, the outcome write is retried, the loop survives a database error |
| masked failure text | a labelled secret never reaches the job row or the event stream |
| single instance lock | a second lock on the same path fails, and succeeds after release |
| events | each line is valid JSON with the expected fields |
| idle mode | a runtime with no handlers claims nothing |
| entry point | the handler mapping from the environment reaches the runtime, and a second worker is refused |

Tests are deterministic. Time is controlled by writing past timestamps into
the job row, by handing the worker a job row that a test already leased, or by
synchronising two connections through a `threading.Event`.

## Known limitations

- The `fcntl.flock` branch of the lock is unexercised on a Windows machine.
- A real Ctrl+C delivered to a running worker is not covered by a test. On
  Windows `os.kill` with `SIGINT` ends the process instead of delivering a
  Python level signal, so the routing is covered in process and the loop exit
  is covered by two tests.
- The lock file must not be deleted or replaced while a worker holds it.
- The secret mask needs a label. A bare token in free text is not masked.

## Out of scope

Scheduler cadences, Qdrant, clustering, Obsidian, model routing, discovery,
scraping, the CLI wiring that registers real handlers, and any change to the
database layer.

## Integration assumptions

- Later tracks register handlers for the real stages. The registry is a plain
  mapping from stage name to callable, so a track adds a mapping and nothing
  else.
- `python -m product_hunter.runtime` reads the handler factory from
  `PH_WORKER_HANDLERS` as `module:attribute`. The attribute is called with no
  arguments and must return the mapping. Without it the worker starts idle.
  Whoever sets that variable decides what code runs in the worker process, so
  it is operator configuration and never comes from untrusted input.
- `PH_WORKER_LOCK` names the lock file and is operator configuration in the
  same way. It defaults to `runtime/worker.lock` under the repository root,
  which is inside the ignored `runtime/` tree.
- The database path comes from `PH_DB_PATH`, the same variable the CLI uses.

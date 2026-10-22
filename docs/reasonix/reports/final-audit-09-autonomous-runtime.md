# Final Audit 09 — Product Hunter as a 24/7 Autonomous Application

**Probe:** Final Audit Probe #9 (read-only)
**Date:** 2026-10-22
**Base:** `main` @ `00a6ca3` ("fix: close the second review round")
**Primary subject:** `wave3/autonomous-operation` @ `befd59f`, worktree `C:\Anubhav\Web Dev Projects\product-hunter-w3a`
**Scope:** worker, scheduler, job queue, leases, lease epochs, single-instance lock, priority handling, cadence, missed work, retry behaviour, crash recovery, graceful shutdown, checkpoints, idempotency, budget enforcement, rate limits, stale-lease recovery, interrupted-research recovery, worker restart, partial stage completion — across all 8 Wave 3 worktrees
**Mutation:** none. No branch was checked out, merged, committed, reconfigured, or written to. Every simulation ran against a throwaway SQLite file under `%TEMP%`. The only file this probe created is this report.

---

## 1. Executive verdict

**The runtime mechanics are sound. The 24/7 lifecycle is not. This branch cannot run unattended for more than one hour, and when it is stopped it does not stop.**

Two findings are hard blockers for autonomous operation, and both are in the operation layer rather than in the queue layer:

- **The operation process has no working graceful stop.** `autonomy/__main__.py:103` installs the signal handler on one `WorkerRuntime` object; `engine.run_forever()` → `start()` → `engine.py:178` builds and installs a *different* runtime. The handler holds the first one forever. `_stopping()` (`engine.py:646`) reads the second one's stop event, which nobody ever sets. SIGINT and SIGTERM on `python -m product_hunter.autonomy` do not stop the engine.
- **The operation is dead after 3600 seconds of wall time, and every subsequent start is dead on arrival.** `config/autonomy.yaml` grants `wall_time_seconds: 3600`; `_charge_wall_time` (`engine.py:381-406`) charges elapsed wall time whether or not any work happened. At the limit the operation moves to the terminal state `budget_exhausted`. The spend is in a durable ledger, so `OperationBudget._load()` restores it on the next start and `budget.exhausted()` is already true on cycle one — the restarted operation claims nothing and terminates again. There is no supervisor, no reset path, no trigger, and no CLI surface for `operation_id`.

Everything *underneath* the operation layer is in good shape, and this is worth stating plainly because it is the part that is hard to get right:

- The lease fence is real. Two workers racing for the same job produce exactly one winner (`db/jobs.py:129-202`). A stale worker's `complete_job`, `fail_job`, `renew_lease`, fenced checkpoint write, and `JobContext.transaction()` are all refused with `LeaseLost`, and the refused transaction leaves no rows behind.
- Lease expiry is genuinely recoverable. A crashed job is requeued, its `stage_attempts` row is marked `lease_lost`, and the epoch advances on re-claim.
- A hard kill (`TerminateProcess`) leaves `PRAGMA integrity_check` = `ok` and the abandoned job recoverable.
- The budget ledger survives a restart exactly, including per-stage and per-source breakdowns, and a refused over-limit spend is still written down.
- The scheduler is deterministic and idempotent. A re-tick at the same instant creates nothing; a 10-minute outage backfills exactly the 10 missed occurrences, all immediately due.

**The infrastructure footprint is already correct and should not be touched.** There is no Redis, no Temporal, no Celery, no message broker, no container or orchestrator manifest in any of the 8 worktrees. The queue, leases, scheduler, checkpoints, budget and pacing are all in-process Python over one SQLite file with WAL. The three optional external services (Qdrant, Apify, SubQ) are off by default and gated on an env var. Probe #1 already found that three waves ship code wired to nothing; the correct remedy for autonomy is to wire the handlers and add a supervisor, **not** to introduce a broker. Adding Redis or Temporal here would replace a queue that already passes the exclusivity test with one that does not need to.

The gap is the lifecycle, not the plumbing.

**Integration status: none of the 8 Wave 3 branches is merged.** `git merge-base --is-ancestor` is false for all seven wave-3 merge commits against `main`; `src/product_hunter/autonomy/` does not exist on `main`. The entire autonomy layer is branch-only, so every finding below is a fix the integration wave must carry, not a regression to repair.

---

## 2. Findings

Severity: **CRITICAL** = blocks autonomous operation. **HIGH** = the 24/7 loop is broken or a safety property does not hold. **MEDIUM** = real defect or spec gap, survivable. **LOW** = hygiene, doc, or consistency.

### F-01 — CRITICAL — The operation engine has no working graceful stop

`src/product_hunter/autonomy/__main__.py:103` (on `wave3/autonomous-operation`):

```python
uninstall = install_shutdown_handlers(engine.runtime)
try:
    engine.run_forever()
```

`engine.runtime` is a property that builds a runtime and caches it (`engine.py:163-167`):

```python
@property
def runtime(self) -> WorkerRuntime:
    if self._runtime is None:
        self._runtime = self._build_runtime()
    return self._runtime
```

`run_forever()` calls `self.start()` as its first statement (`engine.py:240`), and `start()` **replaces** the cached runtime (`engine.py:178`):

```python
self._runtime = self._build_runtime()
```

So the sequence is: build R1 → install the handler on R1 → `start()` builds R2 and overwrites `self._runtime` → the engine runs on R2, the handler still holds R1.

The stop condition reads the runtime the engine uses (`engine.py:640-646`):

```python
def _stopping(self, stop_event: threading.Event | None) -> bool:
    if self._stop.is_set():
        return True
    if stop_event is not None and stop_event.is_set():
        return True
    return self._runtime is not None and self._runtime.stop_event.is_set()
```

`self._runtime` is R2. R2's `stop_event` is never set. `self._stop` is never set (nothing calls `engine.request_stop()`; the signal path calls `runtime.request_stop()` on R1). `stop_event` is `None`.

**Simulated proof** (`%TEMP%\opencode\ph-audit\sim_proc.py`, in-process, exactly the `__main__.py` wiring):

```
[FAIL] the runtime the signal handler holds is the one the engine uses :: bound=2196570274656 engine=2196571476752
[FAIL] after the signal the engine's stop condition is met :: _stop=False engine_runtime_stop=False bound_runtime_stop=True
```

`bound_runtime_stop=True` — the signal *was* received and *did* reach a runtime. `engine_runtime_stop=False` — the engine does not know.

**Contrast: the supervised worker entry point is correct.** `runtime/__main__.py:67-68` builds the runtime once and installs the handler on that same object:

```python
runtime = WorkerRuntime(store.conn, identity, handlers, events=sink)
uninstall = install_shutdown_handlers(runtime)
```

`python -m product_hunter.runtime` therefore stops correctly; `python -m product_hunter.autonomy` does not. The autonomy entry point is the one an operator would actually leave running.

**Impact.** On a laptop lid close or an `ssh` disconnect, the operation process ignores SIGINT/SIGTERM and is killed hard by the OS or the supervisor's SIGKILL. State is not corrupted (F-05 shows the lease recovery covers it), but the job in flight is abandoned to its lease and the "graceful shutdown" claim in the module docstring (`runtime/worker.py:1-8`) is false for the operation path.

**Fix.** Install the handler on the engine, not on a runtime instance, and have the engine forward the stop to whichever runtime it is currently using. The smallest correct change is to give `OperationEngine` a `request_stop()` that the signal handler calls, and to have `start()` **not** replace an existing runtime:

```python
# autonomy/__main__.py
uninstall = install_shutdown_handlers(engine)   # engine.request_stop() -> self._stop.set()
```

```python
# autonomy/engine.py:start()
if self._runtime is None:
    self._runtime = self._build_runtime()
```

`install_shutdown_handlers` currently takes a `WorkerRuntime` and calls `runtime.request_stop()` (`runtime/worker.py:473-491`); give it a duck-typed object with `request_stop()` and the engine qualifies.

---

### F-02 — CRITICAL — The operation dies after one hour of wall time, and every restart is dead on arrival

`config/autonomy.yaml`:

```yaml
budget:
  per_operation:
    wall_time_seconds: 3600
```

`engine._charge_wall_time` (`engine.py:381-406`) charges the elapsed wall time of every cycle, **including idle cycles**:

```python
elapsed = (ended - last).total_seconds()
if elapsed <= 0:
    return step
try:
    self.budget.record(BudgetDimension.wall_time_seconds, elapsed)
except BudgetExceeded as exc:
    ...
    return self._finish(OperationState.budget_exhausted, exc.dimension, step)
```

`budget_exhausted` is in `TERMINAL_STATES` (`autonomy/states.py:42-46`), so the operation stops and the state is written to `operation_runs`.

The spend is durable. `OperationBudget._load()` (`autonomy/budget.py:166-199`) replays the ledger into the tracker, so the next start begins with `wall_time_seconds = 3601` already spent. `run_once` checks the budget **before** it does anything (`engine.py:196`):

```python
exhausted = self.budget.exhausted()
if exhausted:
    return self._finish(OperationState.budget_exhausted, ", ".join(exhausted), step)
```

**Simulated proof** (`sim_budget.py`, `sim_restart.py`):

```
[PASS] an hour of IDLE wall time ends the operation :: state=budget_exhausted reason=wall_time_seconds
     ledger: operation/wall_time_seconds=3601.0
[PASS] a RESTART is instantly budget_exhausted (no fresh allowance) :: state=budget_exhausted reason=wall_time_seconds
[PASS] after the restart no work is claimed :: claimed=0 queued=10
[PASS] the restarted operation is instantly budget_exhausted again :: state=budget_exhausted claimed=0
     -> triggers on operation_budget: 0
```

The last line matters: there is no trigger, no view, no scheduled job that resets the allowance. The only reset is a **different `operation_id`** — confirmed working:

```
[PASS] a new operation_id gets a fresh allowance (the only reset path) :: state=running claimed=1
```

…but `DEFAULT_OPERATION_ID = "autonomy"` (`engine.py:59`) is a module constant, `autonomy/__main__.py` never passes one, and the CLI exposes no flag for it (`cli.py:189-199` offers `ph autonomy status` and nothing else).

**A second, related trap.** `run_once` checks the terminal state **before** `start()` (`engine.py:185-188`):

```python
state = self._state()
if state in TERMINAL_STATES:
    return OperationStep(state=state, reason=self._reason())
if not self._started:
    self.start()
```

`paused` is a terminal state. So after a graceful stop, `python -m product_hunter.autonomy --once` does nothing at all — it returns the stale `paused` state and claims zero jobs. `run_forever()` is different (it calls `start()` first, which resets the row to `running`), which makes the two entry modes disagree about whether a stopped operation can resume.

**Impact.** This is the single largest blocker to "24/7". The operation is designed with a one-hour lifetime, and the lifetime is a lifetime rather than a rate. After one hour — of which most is idle — the operation is dead and stays dead across every restart.

**Fix.** Two decisions are needed, and they are separate:

1. **Make the wall-time allowance a rate, not a lifetime.** Either drop `wall_time_seconds` from `per_operation` and let the real cost dimensions (model calls, fetches, tool calls) do the bounding, or give the operation a rolling window (for example, "3600 seconds per calendar day") that resets at a known boundary and stores the window start in `operation_runs`.
2. **Give the operator a way to start a fresh operation.** Add `--operation` to `python -m product_hunter.autonomy` and to `ph autonomy`, mirroring the existing `--operation` flag on `ph autonomy status` (`cli.py:199`). A new id is a fresh ledger, a fresh state, and a fresh budget.

While fixing this, move the terminal-state check in `run_once` **after** the `start()` call, or make `start()` explicitly clear a `paused` state — otherwise `--once` stays dead against a stopped operation even after the budget fix.

---

### F-03 — HIGH — No handler exists for any stage the cadence names

`runtime/worker.py:455-470` loads handlers from an operator-supplied env var:

```python
def load_handlers(spec: str | None) -> dict[str, Handler]:
    ...
    module = importlib.import_module(module_name)
    factory = getattr(module, attribute)
    return dict(factory())
```

`autonomy/__main__.py:51` reads `PH_WORKER_HANDLERS`; `runtime/__main__.py:42` reads the same variable. **No module in the repository builds that mapping.** The only handler-ish factory, `make_collection_handler` (`collection/orchestrator.py:835`), takes a `CollectionOrchestrator` argument and so cannot be loaded by `load_handlers` (which calls the attribute with no arguments).

`config/cadence.yaml` names six stages — `COLLECT`, `PROCESS`, `CLUSTER`, `INVESTIGATE`, `BRIEF`, `MAINTENANCE` — and `autonomy/cadence.py:101-114` deliberately excludes `MAINTENANCE` from the schedule list (the engine runs the upkeep itself). So five stages have no handler, and the engine's behaviour is to defer them forever (`engine.py:355-369`).

The branch's own README is honest about this (`README.md:242-243`):

> "No handler for the real stages is registered yet, so a worker started today recovers expired leases and reports that it has no handlers."

Probe #1 reached the same conclusion independently (F-04, "three of eight branches ship code that is wired to nothing"). It is repeated here because the runtime consequence is sharper than the architecture consequence: **the loop has nothing to run.**

**Simulated proof** — the `--once` cycle of a real process against a fresh store:

```
[FAIL/PASS] OPERATION: --once runs a single cycle and prints JSON :: rc=0
  stdout={"blocked": 6, "claimed": 1, "completed": 1, ...}
```

`blocked: 6` — six jobs deferred for want of a handler in a single cycle.

**Fix.** Ship a handler module per cadence stage and make it loadable by `load_handlers` (a zero-argument `build()` returning `dict[str, Handler]`), then set `PH_WORKER_HANDLERS` from `config/autonomy.yaml` rather than from the operator's shell. Until that exists, the autonomy branch must be documented as a dormant capability, not as a 24/7 loop.

---

### F-04 — HIGH — The `COLLECT` handler's writes are not fenced, so a stale worker can still write

The lease fence is real for the job row and for the checkpoint API. It is not applied to the one handler in the tree.

`collection/orchestrator.py:854-877`:

```python
def handle(context: JobContext) -> dict[str, object]:
    payload = context.job.payload
    source_id = str(payload.get("source_id") or "")
    url = str(payload.get("url") or "")
    ...
    outcome = orchestrator.collect(scope, CollectionTarget(source_id, url))
```

The `JobContext` is used for **nothing but reading the payload**. It never calls `context.assert_lease()`, never uses `context.transaction()`, and never passes a fence on. The writes go through `orchestrator._transaction()` (`collection/orchestrator.py:815-832`), which is a bare `BEGIN IMMEDIATE` / `COMMIT` with no lease check:

```python
@contextmanager
def _transaction(self) -> Iterator[sqlite3.Connection]:
    if self._conn.in_transaction:
        yield self._conn
        return
    self._conn.execute("BEGIN IMMEDIATE")
    ...
```

And the checkpoint write is explicitly **unfenced** (`collection/orchestrator.py:787-794`):

```python
save_checkpoint(
    self._conn,
    run_id=scope,
    stage=COLLECT_STAGE,
    step=f"{target.source_id} {target.url}",
    state={"source_id": target.source_id, "url": target.url},
    checkpoint_version=1,
)
```

No `worker_id`, no `lease_epoch`. `db/checkpoints.py:73-76` describes an unfenced write as "the control plane rather than a leased worker" — but this is a leased worker writing unfenced.

The fenced path exists and is unused: `JobContext.transaction()` (`db/jobs.py:516-527`) checks the lease before and after the write and rolls back if it was lost.

**Impact.** A worker whose lease expired mid-fetch (a long source, a slow page, a laptop sleep) can still commit `collection_source_state`, `collection_items`, the fetch record and a checkpoint, while a second worker owns the same job. The *job outcome* is correctly refused — the queue stays consistent — but the *data* is not fenced. The invariant "stale workers cannot write after lease loss" holds for `jobs` and `research_checkpoints`, and does not hold for the collection write path.

**Fix.** Route the handler's writes through the fence. The smallest change that keeps the existing shape: have `make_collection_handler` pass `context.worker_id` and `context.job.lease_epoch` down to `orchestrator.collect(...)` and `save_checkpoint(...)`, and assert the lease inside `orchestrator._transaction()` before it opens the write transaction.

---

### F-05 — HIGH — Source pacing and the source gate are inert for the shipped cadence

Two separate rate controls exist. Neither is reachable for the work the cadence creates.

**The scheduler's source gate.** `db/scheduler.py:217-219` consults `_source_reason` only when the schedule names a source:

```python
reason = (
    self._source_reason(schedule.source_id)
    if schedule.source_id is not None
    else None
)
```

`_source_reason` (`db/scheduler.py:457-469`) is what refuses a disabled source or one whose terms are not `reviewed`/`permitted`. But `config/cadence.yaml` beats have no `source_id` key, and `autonomy/cadence.py:130-148` (`_to_schedule`) constructs the `Schedule` without one:

```python
return Schedule(
    name=beat.name,
    stage=beat.stage,
    interval_seconds=beat.interval_seconds,
    offset_seconds=beat.offset_seconds,
    priority=priority,
    catchup_limit=beat.catchup_limit,
    max_attempts=beat.max_attempts,
    enabled=beat.enabled,
)
```

`Schedule.source_id` defaults to `None`, so the gate never runs. A source that is disabled in `config/sources.yaml`, or whose terms have not been reviewed, is still scheduled for collection.

**The engine's pacer.** `engine._next_job` (`engine.py:371-378`) reads the source out of the **job payload**:

```python
source_id = str(job.payload.get("source_id") or "")
if not source_id:
    return job, deferred, blocked
wait = self.pacer.seconds_until_allowed(source_id, now=now)
```

The scheduler writes the payload as `schedule.payload` plus `schedule` and `occurrence` (`db/scheduler.py:258-270`). Since the cadence payload is empty and no `source_id` is added, the payload has no `source_id` and the pacer is never consulted. `SourcePacer` (`autonomy/limits.py:60-94`) is therefore dead for cadence-driven work, and with it `config/autonomy.yaml`'s `source_pacing.default_min_interval_seconds: 60`, `source_pacing.per_source`, and each source's own `rate_limit.min_interval_seconds` from `config/sources.yaml`.

**Simulated proof** (`sim_budget.py`):

```
[PASS] the COLLECT beat names no source in the cadence file :: beat keys=[... 'stage']
[PASS] the built Schedule carries source_id=None :: source_id=None
[PASS] the source gate never fires (the schedule names no source) :: blocked=0 created=2
[PASS] the created job carries no source_id in its payload
[PASS] the pacer records no source use (no source in the payload) :: source_pacing rows=0
```

The last check disabled `hn_algolia` in the `sources` table first. The tick still created the jobs.

**Impact.** `COLLECT` fires every 900 seconds against whatever the handler targets, ignoring both the declared per-source rate limits and the source's terms status. On a source whose `rate_limit.min_interval_seconds` is larger than 900, this is a terms and politeness problem, not just a throughput one.

**Fix.** Add `source_id` to `CadenceBeat` and to `config/cadence.yaml`, set it in `_to_schedule`, and put the same `source_id` into the created job payload so the pacer can see it. `Schedule.payload` should carry `source_id` alongside the schedule's own source gate.

---

### F-06 — HIGH — Duplicate execution is not safe by construction

The lease fence guarantees one owner at a time. It does not make a replayed side effect safe.

`db/jobs.py:412-458` (`recover_expired_leases`) requeues a crashed job with `status = 'queued'` and `last_error = 'lease_expired'`, keeping the attempt count. The next claim runs the handler again. There is no dedupe token handed to the handler and no comparison of the previous attempt's output.

**Simulated proof** (`sim_core.py`) — a handler that appends a row, the process dies after the append but before the outcome write:

```
[PASS] the job is still 'leased' after the crash :: leased
[PASS] the crashed job is recovered as queued work :: recovered=1
[PASS] the recovered job is claimable again
[PASS] the handler runs a second time :: handler ran 2x
[PASS] the side effect is applied twice (no dedupe at the queue layer) :: side-effect rows=2
```

The current tree gets partial protection from the *job* key, not from the queue: `enqueue_collection_jobs` (`collection/orchestrator.py:882-926`) derives `collect:<scope>:<source_id>:<url>`, so the same target in the same scope is one job. That protects against a duplicate enqueue. It does not protect against a handler that crashed between its side effect and its outcome write — the job is re-run and the side effect lands twice.

**Impact.** Correctness of every side effect depends entirely on each handler being idempotent. Nothing in the runtime enforces or checks that, and the queue gives a handler no key it could use for dedupe.

**Fix.** Expose the job's `idempotency_key` on the `JobContext` so a handler can key its own writes on it, and require that any handler performing a non-idempotent write use `context.transaction()` (which is fenced) so the replay is either fully applied or not applied at all. The `COLLECT` handler is the case to fix first, and it is the same fix as F-04.

---

### F-07 — MEDIUM — The two counters that would signal missed work are false alarms on a healthy run

**"dropped" occurrences.** `db/scheduler.py:429-455` computes the catch-up window as *every* grid point in `(now - lookback, now]` and keeps only the newest `catchup_limit` of them, reporting the rest as `DroppedSchedule`. The default `lookback_seconds` is 86400, and the `source_collection` beat has `interval_seconds: 900` with `catchup_limit: 2`, so each tick walks 96 grid points, keeps 2, and reports 94 as dropped — every tick, forever, even though those 94 already exist as jobs from earlier ticks.

**"missed occurrences."** `engine._missed_occurrences` (`engine.py:735-752`) counts every created job whose occurrence timestamp is in the past. On a perfectly on-time run the newest grid point is always slightly in the past, so the count is never zero.

**Simulated proof** (`sim_budget.py`):

```
     first tick:  dropped={'source_collection': 94, 'evidence_processing': 22, 'reclustering': 2}
     second tick: dropped={'source_collection': 94, 'evidence_processing': 22, 'reclustering': 2}
     missed_occurrences reported by the engine on the first tick: 5
```

And in a live process, every tick prints the warning to stderr:

```
schedule source_collection dropped 94 occurrences, the oldest at 2025-12-31T00:15:00+00:00
schedule evidence_processing dropped 22 occurrences, ...
schedule reclustering dropped 2 occurrences, ...
```

**Impact.** Both counters fire continuously on a healthy system. An operator cannot use them as alerts, and the log volume from a 30-second scheduler interval is significant. Worse, the `missed_occurrences` counter on `operation_runs` (`engine.py:67-76`, `_COUNTERS`) accumulates a permanently non-zero value that will look like a real backlog.

**Fix.** Report `dropped` only for occurrences that are genuinely absent — i.e. compute the drop count against what already exists, not against the raw grid. Count `missed` only when an occurrence is older than one scheduler interval at the moment it is created, not merely in the past.

---

### F-08 — MEDIUM — The operation budget only charges wall time; the cost ceilings never bind

`config/autonomy.yaml` declares three cost dimensions per operation:

```yaml
budget:
  per_operation:
    wall_time_seconds: 3600
    tool_calls_total: 500
    fetch_requests: 200
    model_calls: 200
```

Across the whole package there is exactly **one** `budget.record(...)` call in the operation layer (`engine.py:397`), and it charges `wall_time_seconds`. The other three dimensions are never charged by the engine.

The research and discovery budgets (`research/budget.py`, `research/investigation/budget.py`, `discovery/budget.py`) are separate objects with separate scopes; they do not write the `operation_budget` ledger.

**Impact.** The ceilings that would actually bound spend on a 24/7 operation — model calls, fetch requests, tool calls — are inert. A run that spends without limit is bounded only by the wall clock, and the wall clock is itself the F-02 bug. The `per_stage` and `per_source` budget scopes in the same file are equally inert for the same reason.

**Fix.** Have the engine charge the operation budget from the handler's own accounting: the handler already knows its tool calls, fetches and model calls, so `JobContext` should carry a budget handle, or the engine should charge a fixed per-stage cost per job. The per-source and per-stage scopes in `config/autonomy.yaml` become meaningful at the same time.

---

### F-09 — MEDIUM — Jobs for stages with no handler accumulate without bound

`engine._next_job` (`engine.py:355-369`) defers a job whose stage has no handler by `unhandled_stage_delay_seconds` (300 in `config/autonomy.yaml`) and emits one `job_blocked` event per stage per cycle. The job stays `queued` forever: it is never claimed, never fails, never dies, never reaches a terminal state.

`autonomy/maintenance.py:6` states the policy plainly: "It never deletes a job row."

**Simulated proof** (`sim_budget.py`): 8 queued jobs after 5 cycles, with the event log showing only `job_blocked` and `operation_started`.

Over 24/7 this grows without limit. With the shipped cadence (COLLECT every 15 min = 96/day, PROCESS hourly = 24/day, CLUSTER 6-hourly = 4/day, INVESTIGATE daily, BRIEF daily) the leak is roughly 130 rows a day per unhandled stage, and `jobs` is the table the `db jobs` CLI command lists.

**Fix.** Either give the stages handlers (F-03), or make the deferral bounded — a job deferred for a stage with no handler should go to `dead` after N deferrals so it lands in the dead-letter count that `operation_status` already reports (`engine.py:705-708`).

---

### F-10 — MEDIUM — A corrupt checkpoint still stops the operator-facing run log

`db/checkpoints.py:210-212` carries the repo's own shortcut marker:

> "shortcut: `load_valid_checkpoint` and `skip_invalid` have no production caller yet, so a corrupt checkpoint still stops `registry.run_log`; wire them into the resume path when that path exists."

That marker is now half-stale. `autonomy/crash.py` calls `load_valid_checkpoint` in two places (`crash.py:80` and `crash.py:118`), so the *recovery* path does step over an unreadable checkpoint. But `registry.run_log` (`registry.py:363`) still does:

```python
checkpoints = [
    asdict(checkpoint)
    for checkpoint in list_checkpoints(conn, run_id=row["run_id"])[:bounded]
]
```

`list_checkpoints` raises `CheckpointError` on a row whose `state_json` does not parse, unless `skip_invalid=True` is passed — and it is not. So `ph` run-log output still dies on a corrupt checkpoint.

**Impact.** Small, but it is the one reader of a run's history that an operator reaches for after an incident, and it is the last unhardened reader of the checkpoint table. The recovery path and the log path disagree about whether a corrupt checkpoint is survivable.

**Fix.** Pass `skip_invalid=True` in `registry.run_log`, and update the shortcut comment to say what is true: recovery steps over a bad checkpoint, the log does not.

---

### F-11 — MEDIUM — Windows: `CTRL_BREAK` is not routed to the stop path

`runtime/worker.py:485` installs handlers for exactly two signals:

```python
for sig in (signal.SIGINT, signal.SIGTERM):
    previous[sig] = signal.getsignal(sig)
    signal.signal(sig, _request_stop)
```

On Windows, `CTRL_BREAK_EVENT` arrives as `SIGBREAK`, which is not in the list. The default handler terminates the process. `CTRL_C_EVENT` arrives as `SIGINT` and is handled.

**Simulated proof** (`sim_proc.py`): sending `CTRL_BREAK_EVENT` to `python -m product_hunter.autonomy` and to `python -m product_hunter.runtime` terminated both with exit code `3221225786` (`0xC000013A`, STATUS_CONTROL_C_EXIT) and **no** `shutdown_requested` event in the log — the Python handler never ran.

Combined with F-01, the operation process on the target OS has **no** working graceful stop: SIGINT is caught but ignored (F-01), SIGTERM is caught but ignored (F-01), and CTRL_BREAK is not caught at all.

**Fix.** Add `signal.SIGBREAK` to the tuple, guarded by `hasattr(signal, "SIGBREAK")` so the module stays importable on POSIX.

---

### F-12 — LOW — `max_concurrent_jobs` can never bind in the shipped topology

`config/autonomy.yaml` sets `max_concurrent_jobs: 4`. `ConcurrencyLimiter.at_capacity` (`autonomy/limits.py:137-139`) counts leased jobs with unexpired leases across the whole store, which is the right global measure.

But the engine runs **one** job at a time — `run_once` claims a single job (`engine.py:219`) and returns — and the single-instance lock prevents a second process from sharing the database. So the limit of 4 is never approached. It is a guard against a topology that does not exist.

**Fix.** Leave it. It is cheap and it becomes live the moment someone runs two workers against one store, which the fence is built to survive. Worth a comment saying so, so the next reader does not assume the engine is concurrent.

---

### F-13 — LOW — Two worker implementations coexist

`runtime/worker.py` is the supervised worker: lease renewal on a background thread, busy-retry, structured events, signal handling. `db/worker.py` is an older worker with the same claim/complete/fail shape but **no heartbeat**, no busy-retry, and no events. The entry points use the first. The second is exported from `product_hunter.db` and is reachable by anyone who reads the package exports.

**Fix.** Delete `db/worker.py` and its exports, or mark it explicitly as the in-process test double. Two workers with different renewal behaviour is a trap for whoever writes the next handler.

---

### F-14 — LOW — The operation is not reachable from the CLI, and nothing supervises it

`cli.py:189-199` adds `autonomy status` and nothing else. There is no `ph autonomy run`, no `--operation` flag on `start`, and no `--once` equivalent in the CLI (the `--once` flag exists only on `python -m product_hunter.autonomy`).

There is no systemd unit, no Windows service definition, no Task Scheduler XML, no Dockerfile, no compose file, and no restart policy anywhere in the 8 worktrees. The 24/7 claim has no process that brings the operation back when the laptop reboots.

**Fix.** Add `ph autonomy run` and `ph autonomy reset --operation <id>`; ship a systemd unit and a Windows scheduled task; and decide explicitly whether the operation is meant to survive a reboot (it currently cannot, because of F-02).

---

## 3. What was verified working

These are not findings. They are the properties that hold, established by simulation rather than by reading, and they are the reason the fixes above are small.

| Property | Result | Evidence |
|---|---|---|
| Two workers cannot claim the same job | PASS | Two threads, two connections, same instant → one winner; row shows one owner, `lease_epoch = 1`, `attempts = 1` |
| A stale worker cannot complete a job | PASS | `complete_job` with the old epoch → `LeaseLost` |
| A stale worker cannot fail a job | PASS | `fail_job` with the old epoch → `LeaseLost` |
| A stale worker cannot renew a lease | PASS | `renew_lease` with the old epoch → `LeaseLost` |
| A stale worker cannot write a checkpoint | PASS | `save_checkpoint` → `CheckpointError: the lease is not yours or has expired` |
| A stale worker's transaction rolls back | PASS | `JobContext.transaction()` → `LeaseLost`, 0 rows written |
| A live worker's fenced checkpoint write succeeds | PASS | `ckpt_…` returned |
| Expired jobs become recoverable | PASS | `recover_expired_leases` → 1 job requeued, `last_error = 'lease_expired'` |
| The lease epoch advances on re-claim | PASS | epoch 1 → 2 |
| A crashed job keeps its attempt count | PASS | `attempts = 2` after the second claim |
| A hard kill leaves the database intact | PASS | `PRAGMA integrity_check` = `ok` after `TerminateProcess` mid-handler |
| A hard-killed job is recovered | PASS | job requeued; `stage_attempts` row → `status = 'lease_lost'` |
| A lease lost mid-handler does not corrupt the job | PASS | first worker's completion refused; job still leased to the second worker |
| The scheduler creates no duplicate job | PASS | re-tick at the same instant → 0 created, 60 existing |
| Missed scheduled work becomes overdue work | PASS | a 10-minute gap backfills exactly 10 occurrences, all immediately due; `overdue()` reports them |
| A 30-day outage is bounded, not unbounded | PASS | 60 created, 0 dropped (catch-up limit respected) |
| The budget survives a restart | PASS | operation, stage and source spends all restored exactly |
| A refused over-limit spend is still written down | PASS | ledger shows `220.0` after the refusal |
| Exhaustion is detected after a restart | PASS | `exhausted = ['model_calls']` |
| Checkpoints survive a crash and are idempotent | PASS | resume point returns the same state; re-saving the same version+state returns the same checkpoint |
| A different state under the same version is refused | PASS | `CheckpointConflict` |
| Crash recovery counts the interrupted attempt | PASS | `interrupted = 1`, `leases = 1`, `requeued = 1` |
| A second worker on the same lock file is refused | PASS | exit code 2, `lock_denied` event |
| The lock is released when the holder exits | PASS | a new worker then gets `lock_acquired` |
| The supervised worker stops on CTRL_BREAK | PASS | process exits |
| The operation's `--once` mode runs a cycle | PASS | prints the step as JSON, exit 0 |

The repo's own suite is green on this branch: `pytest tests/test_autonomy_engine.py tests/test_autonomy_recovery.py tests/test_autonomy_cadence.py tests/test_autonomy_limits.py tests/test_autonomy_main.py tests/test_runtime_worker.py tests/test_runtime_lock.py tests/test_db_jobs.py tests/test_db_scheduler.py tests/test_db_worker.py tests/test_db_crash_recovery.py` → **198 passed**.

---

## 4. Method

Four simulation harnesses, all under `%TEMP%\opencode\ph-audit\`, none inside the repository. Every one writes only to a throwaway SQLite file in a temp directory.

| Script | Covers |
|---|---|
| `sim_core.py` | lease exclusivity, stale-writer refusal, expired-lease recovery, duplicate execution, missed-work backfill, budget persistence, checkpoint resume |
| `sim_proc.py` | the engine's stop path in-process, `CTRL_BREAK` to a real process, single-instance lock, hard kill mid-handler, `--once` mode |
| `sim_budget.py` | the wall-time budget across a restart, source-pacing wiring, the `dropped`/`missed` counters, blocked-job growth |
| `sim_restart.py` | whether a terminal operation can ever be restarted, whether a new `operation_id` is a usable reset |

Windows-specific care was needed in two places, and both are worth recording:

- `subprocess.send_signal(SIGTERM)` on Windows calls `TerminateProcess` and **does not** deliver a signal to a Python handler. The first round of the shutdown test therefore measured the wrong thing (the process died with exit 1 and the test "passed" for the wrong reason). The correct test uses `CTRL_BREAK_EVENT` with `CREATE_NEW_PROCESS_GROUP`, and the in-process test that reproduces the `__main__.py` wiring exactly.
- `sqlite3.connect` defaults to `check_same_thread=True` (`db/connection.py:46-56`), so a connection cannot be shared across threads. The first round of the two-worker race test failed with `ProgrammingError` for that reason rather than because of the product. The corrected harness opens its connection inside the thread. This is worth knowing for the operation layer: the heartbeat thread gets its own connection through `connection_factory` (`runtime/worker.py:125-132`), which is the correct pattern.

Read-only inspection also confirmed the following, with no simulation needed: no `redis`, `temporal`, `celery`, `rq`, `dramatiq`, `arq`, `prefect`, `airflow`, `dagster`, `kafka`, `rabbitmq`, `amqp`, `sqs`, `pubsub`, `nats`, `consul`, `etcd`, `zookeeper`, `docker`, `compose`, `kubernetes`, `k8s` or `helm` reference in any `.py`, `pyproject.toml`, `config/*.yaml` or `.env.example` in any of the 8 worktrees. The only `temporal` hits are the scoring factor `temporal_persistence` in `config/scoring_policy.yaml:48`.

---

## 5. Infrastructure verdict: do not add Redis or Temporal

The question "does autonomous operation require unnecessary Redis/Temporal infrastructure" has a clean answer: **it does not use any, and it should not.**

The evidence, across all 8 worktrees:

- `[project] dependencies` is `pydantic`, `httpx`, `beautifulsoup4`, `lxml`, `PyYAML`, `python-dotenv` on every branch. `wave3/duckdb-analytics` adds `duckdb`. `wave3/qdrant-semantic` adds an *optional* `embeddings` extra (`flagembedding`, `huggingface-hub`, `torch`) that is not installed by default. Nothing else.
- No broker, no scheduler service, no workflow engine, no container file, no orchestrator manifest.
- The queue is `db/jobs.py` over SQLite with WAL, `BEGIN IMMEDIATE` transactions, `busy_timeout = 5000` and a busy-retry wrapper. The lease, the epoch, the scheduler, the checkpoint, the budget and the pacer are all in the same file-backed store.

The exclusivity test that a broker would exist to provide is already satisfied by `BEGIN IMMEDIATE` plus the `status = 'queued'` predicate in the claim's `WHERE` clause (`db/jobs.py:163-171`). Two concurrent workers on two connections produce one winner, as measured above.

The three optional external services are correctly gated and off by default:

| Service | Branch | Gate |
|---|---|---|
| Qdrant | `wave3/qdrant-semantic` | `PH_QDRANT_URL=http://localhost:6333`, optional `embeddings` extra |
| Apify | `wave3/scraping-ladder` | `APIFY_TOKEN` / `APIFY_ACTOR_ID` |
| SubQ | `wave3/subq-integration` | `SUBQ_API_KEY` plus a dozen `PH_SUBQ_*` settings |

**Recommendation: keep the current footprint.** The blockers are F-01 (no stop), F-02 (one-hour lifetime), F-03 (no handlers), F-04 (unfenced writes) and F-05 (inert rate limits). None of them is a queue-capability problem, and each of the five is a small, local, testable change to code that already exists. A Redis or Temporal migration would be a large, high-risk rewrite that solves none of them.

The repo's own docs agree: Redis, BullMQ and Temporal are explicitly deferred to "only after measured need" (`docs/reasonix/ARCHITECTURE.md:688/706/726`). The measurement has not been made, and the code that would need it is not the code that is broken.

---

## 6. Required integration fixes, in order

These are the fixes the integration wave must carry. Ordered by whether the branch can be merged at all, then by blast radius.

| # | Fix | Files | Severity |
|---|---|---|---|
| 1 | Route the signal to the engine, and stop `start()` from replacing the runtime | `src/product_hunter/autonomy/__main__.py`, `src/product_hunter/autonomy/engine.py` | CRITICAL |
| 2 | Make the wall-time budget a window, not a lifetime; add `--operation` so an operator can start a fresh operation | `config/autonomy.yaml`, `src/product_hunter/autonomy/engine.py`, `src/product_hunter/autonomy/budget.py`, `src/product_hunter/autonomy/__main__.py`, `src/product_hunter/cli.py` | CRITICAL |
| 3 | Ship a handler module per cadence stage and load it by default | `src/product_hunter/autonomy/handlers/` (new), `config/autonomy.yaml`, `src/product_hunter/autonomy/__main__.py` | HIGH |
| 4 | Fence the `COLLECT` handler's writes | `src/product_hunter/collection/orchestrator.py` | HIGH |
| 5 | Give the cadence beats a `source_id` and carry it into the job payload | `config/cadence.yaml`, `src/product_hunter/autonomy/cadence.py`, `src/product_hunter/db/scheduler.py` | HIGH |
| 6 | Expose the idempotency key on `JobContext`; require fenced transactions for non-idempotent writes | `src/product_hunter/db/jobs.py` | HIGH |
| 7 | Charge the operation budget for tool calls, fetches and model calls | `src/product_hunter/autonomy/engine.py`, `src/product_hunter/db/jobs.py` | MEDIUM |
| 8 | Report `dropped` and `missed` only when they are real | `src/product_hunter/db/scheduler.py`, `src/product_hunter/autonomy/engine.py` | MEDIUM |
| 9 | Bound the deferral of a job whose stage has no handler | `src/product_hunter/autonomy/engine.py` | MEDIUM |
| 10 | Pass `skip_invalid=True` in the run log; refresh the stale shortcut comment | `src/product_hunter/registry.py`, `src/product_hunter/db/checkpoints.py` | MEDIUM |
| 11 | Handle `SIGBREAK` on Windows | `src/product_hunter/runtime/worker.py` | MEDIUM |
| 12 | Add `ph autonomy run` and a supervisor unit | `src/product_hunter/cli.py`, `deploy/` (new) | LOW |
| 13 | Delete the superseded `db/worker.py` | `src/product_hunter/db/worker.py`, `src/product_hunter/db/__init__.py` | LOW |

Fixes 1, 2 and 3 are the difference between "a scheduler that runs for an hour" and "a 24/7 application". Fixes 4, 5 and 6 are the difference between "the queue is safe" and "the data is safe".

**Cross-branch conflicts to expect.** Probe #1 already recorded that three branches claim migration `0008` and two set `EXPECTED_VERSION = 8`. The autonomy branch is one of the three. Its `0008_autonomy.sql` is table-disjoint from the other two, so renumbering is mechanical — but note that this probe's findings add two more shared surfaces to reconcile: `config/autonomy.yaml` and `config/cadence.yaml` are new config files (no conflict), while `src/product_hunter/db/connection.py` is edited by both the autonomy and control-plane branches (Probe #1 F-06) and `src/product_hunter/runtime/worker.py` by both autonomy and control-plane (Probe #1 F-07). Fixes 1, 4 and 11 all touch `runtime/worker.py`, so they land on top of that conflict.

---

## 7. Exact file index

Every file this probe read or cited, with the reason.

**Autonomy layer** (`wave3/autonomous-operation`, worktree `product-hunter-w3a`)

| File | Role in the findings |
|---|---|
| `src/product_hunter/autonomy/__main__.py` | F-01 (line 103), F-02, F-03 (line 51), F-14 |
| `src/product_hunter/autonomy/engine.py` | F-01 (163-178, 640-646), F-02 (59, 185-196, 240, 381-406), F-05 (371-378), F-07 (735-752), F-08 (397), F-09 (355-369) |
| `src/product_hunter/autonomy/budget.py` | F-02 (116-118, 166-199) |
| `src/product_hunter/autonomy/cadence.py` | F-05 (101-114, 130-148) |
| `src/product_hunter/autonomy/states.py` | F-02 (42-46) |
| `src/product_hunter/autonomy/limits.py` | F-05 (60-94), F-12 (112-139) |
| `src/product_hunter/autonomy/health.py` | worker liveness, `DEFAULT_STALE_AFTER_SECONDS = 90` |
| `src/product_hunter/autonomy/maintenance.py` | F-09 (line 6) |
| `src/product_hunter/autonomy/crash.py` | F-10 (19, 80, 118) |
| `config/autonomy.yaml` | F-02, F-05, F-08, F-09, F-12 |
| `config/cadence.yaml` | F-03, F-05, F-07 |
| `db/migrations/0008_autonomy.sql` | `worker_health`, `operation_runs`, `operation_events`, `operation_budget`, `source_pacing` |

**Runtime and queue layer** (present on every branch)

| File | Role in the findings |
|---|---|
| `src/product_hunter/runtime/worker.py` | F-01, F-03, F-06, F-11, F-13 |
| `src/product_hunter/runtime/__main__.py` | F-01 (contrast: correct wiring at 67-68) |
| `src/product_hunter/runtime/lock.py` | single-instance lock (verified working) |
| `src/product_hunter/runtime/identity.py`, `events.py` | worker identity and the JSONL event stream |
| `src/product_hunter/db/jobs.py` | F-04, F-06, F-08; the queue itself (verified working) |
| `src/product_hunter/db/leases.py` | the shared fence (`assert_live_lease`, `fenced_write`) |
| `src/product_hunter/db/checkpoints.py` | F-04, F-10 |
| `src/product_hunter/db/scheduler.py` | F-05, F-07 |
| `src/product_hunter/db/connection.py` | `connect()` (`check_same_thread=True`), `transaction()` |
| `src/product_hunter/db/outbox.py` | projection queue: same lease+epoch pattern as `jobs` |
| `src/product_hunter/db/worker.py` | F-13 |

**Domain layer**

| File | Role in the findings |
|---|---|
| `src/product_hunter/collection/orchestrator.py` | F-04 (787-794, 815-832, 854-877), F-06 (882-926) |
| `src/product_hunter/registry.py` | F-10 (363, 379-381); the run-level lease at 605-660, 740-790 |
| `src/product_hunter/research/investigation/checkpoint.py` | in-memory value object; persistence is via `db/checkpoints.py` |
| `src/product_hunter/research/investigation/pipeline.py` | writes route through `db/research_gate.py`, which is fenced |
| `src/product_hunter/db/research_gate.py` | the fenced research write path (verified working) |
| `src/product_hunter/cli.py` | F-02, F-14 (189-199) |
| `src/product_hunter/config.py` | `AutonomyConfig`, `load_autonomy_config` (440-460), `repo_root` (349-354) |
| `README.md` | F-03 (242-243) |

---

## 8. What was not checked

- **No live 24/7 soak.** There is no handler to soak against (F-03), so a soak would have measured an idle loop. The cycle-level behaviour was simulated instead, with a controllable clock.
- **No real LLM interruption.** The research pipeline was traced to its fenced persistence path (`db/research_gate.py`) and the checkpoint layer was exercised directly, but no run was killed mid-token-stream against a live model. The lease-expiry-mid-handler case was simulated with a sleeping handler instead.
- **No host-level deployment inspection.** The repository contains no supervisor manifest, and none was looked for outside the repository. Whether a Windows service or Task Scheduler entry exists on the host was not checked.
- **Wave 3 branches other than autonomy were read, not simulated.** The other seven branches were swept for infrastructure dependencies, new modules and shared-surface conflicts. Their own domain logic — clustering, semantic retrieval, source expansion, observability, the SubQ provider, the scraping ladder — was not exercised.
- **The `wave3/source-expansion`, `wave3/observability-evaluation` and `wave3/qdrant-semantic` migrations were not applied** to a merged schema. Probe #1 already established that three branches claim migration number `0008`; that blocker stands and is not re-litigated here.
- **`db/worker.py`'s behaviour was read but not run**, since it is superseded (F-13).
- **The DuckDB and Qdrant integrations were not exercised.** They are outside the autonomous-runtime scope and neither is wired into the operation path.

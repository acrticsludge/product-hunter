# Wave 3: autonomous operation

Branch: `wave3/autonomous-operation` (worktree `C:/Anubhav/Web Dev Projects/product-hunter-w3a`, base `00a6ca3`)
RUN_ID: w3a-20261022-autonomy

## GOAL

Turn Product Hunter from a pipeline that can run once into a system that keeps
running: resumable, bounded, observable, crash-safe, budget-aware, rate-limit-aware,
deterministic, and safe against duplicate work. Twenty capabilities in six groups,
delivered as one operation layer over the queue, scheduler, leases, checkpoints
and outbox that already existed.

## TIER

free. Models used per node: implement on longcat-2.5-preview-free then step-5-preview-free
for the fix rounds; verify on longcat-2.5-preview-free; harden on step-5-preview-free.
The review node could not run (both free review models were unavailable).

## RISK

Declared high. The migration `db/migrations/0008_autonomy.sql` adds five tables
and the adapter lists `db/migrations/**` as a high-risk path, so the floor is
high and stages 2, 6 and 7 could not be skipped. Stages 2 and 6 ran; stage 7
could not.

## ADAPTER

Product Hunter [repo], 8 forbidden rules in force, radius declared at stage 0
and widened twice (see below).

## STAGES

10 of 11 complete, skipped: 7 (REVIEW, models unavailable), 9 (SHIP, blocked by
the commit gate). See "Stopped because".

## GATES

```
2183 passed in 36.62s
```

Exit code 0. The gate was re-run after every edit round and stayed green at
2183 passed. No test was changed to make a failure disappear; the two existing
test files that changed (`tests/test_db_migrations.py`, `tests/test_cli_db_status.py`)
only recorded the new schema version and the new tables, which is what a new
migration legitimately requires.

## CHANGES

New files (26):

| File | Lines | What it is |
|---|---|---|
| `src/product_hunter/autonomy/__init__.py` | 8 | Package marker |
| `src/product_hunter/autonomy/cadence.py` | 134 | Reads `config/cadence.yaml` into schedules |
| `src/product_hunter/autonomy/states.py` | 85 | The operation states and the failure classifier |
| `src/product_hunter/autonomy/budget.py` | 227 | The persisted budget ledger over the research budget model |
| `src/product_hunter/autonomy/limits.py` | 139 | The source pacer and the global concurrency limiter |
| `src/product_hunter/autonomy/health.py` | 217 | The worker health rows and the stale sweep |
| `src/product_hunter/autonomy/crash.py` | 143 | The startup recovery sequence and the resume point |
| `src/product_hunter/autonomy/maintenance.py` | 146 | The upkeep pass and the dead letter review |
| `src/product_hunter/autonomy/engine.py` | 663 | `OperationEngine`: the cycle that schedules, claims and accounts |
| `src/product_hunter/autonomy/__main__.py` | 105 | The supervised operation process |
| `config/cadence.yaml` | 56 | The beats: interval, offset, priority, catch-up limit |
| `config/autonomy.yaml` | 25 | Poll and lease timings, concurrency, aging, budget, pacing |
| `db/migrations/0008_autonomy.sql` | 75 | Five tables: `worker_health`, `operation_runs`, `operation_events`, `operation_budget`, `source_pacing` |
| `tests/test_autonomy_cadence.py` | 117 | Cadence tests |
| `tests/test_autonomy_engine.py` | 349 | Engine tests |
| `tests/test_autonomy_limits.py` | 156 | Limits and budget tests |
| `tests/test_autonomy_recovery.py` | 387 | Crash-safety simulations |
| `docs/reasonix/specs/wave-3-autonomous-operation.md` | 1726 words | The spec |
| `docs/reasonix/plans/wave-3-autonomous-operation.md` | 13 sections | The plan |
| `docs/reasonix/prompts/wave-3-autonomous-operation.md` | 1 | The dispatch prompt |
| `docs/reasonix/handoffs/wave-3-autonomous-operation-handoff.md` | 1 | The handoff |
| `docs/reasonix/reports/wave-3-autonomous-operation.md` | this file | The run report |

Changed existing modules:

| File | What changed |
|---|---|
| `src/product_hunter/db/jobs.py` | `claim_job` gained `aging_seconds` and `aging_cap` (off by default); `peek_job` and `defer_queued_job` are new |
| `src/product_hunter/db/scheduler.py` | `default_schedules()` reads `config/cadence.yaml` through a lazy import |
| `src/product_hunter/runtime/worker.py` | The two aging settings, plus `load_handlers` moved here from `runtime/__main__.py` |
| `src/product_hunter/runtime/lock.py` | `lock_path` moved here, so both entry points take the same file |
| `src/product_hunter/runtime/__main__.py` | Uses the two moved helpers |
| `src/product_hunter/config.py` | `AutonomyConfig`, `PriorityAging`, `OperationBudgetLimits`, `SourcePacingSettings`, `load_autonomy_config` |
| `src/product_hunter/sources.py` | `from_yaml` refuses a bad YAML file by name |
| `src/product_hunter/cli.py` | The `ph autonomy status` command group |
| `src/product_hunter/db/connection.py` | A shared `transaction` helper with savepoint semantics on join |
| `src/product_hunter/evidence.py` | Uses the shared `transaction` helper instead of its own copy |
| `tests/test_db_migrations.py` | `EXPECTED_VERSION` 7 to 8, and the five new tables |
| `tests/test_cli_db_status.py` | Version 8 and the five new tables |

## HARDEN

Two passes. The first raised four MUST and two SHOULD findings; all were fixed.
The second pass returned a clean verdict: no new findings, and the four fixes
were verified against the code.

First pass findings and their resolution:

| Finding | Severity | Resolution |
|---|---|---|
| Engine cycle bookkeeping not atomic | MUST | Fixed: `_finish` and `_record_outcome` run inside one transaction |
| Budget ledger writes scope rows one at a time | MUST | Fixed: `_persist` writes them in one transaction |
| Unexpected exception kills the operation with no record | MUST | Fixed: `run_forever` catches `Exception`, records the event, ends with `permanent_failure` at `max_consecutive_errors` |
| Broken cadence file crashes the entry point | MUST | Fixed: `__main__.py` refuses it with a clean JSON error and a non-zero exit code |
| `worker_health.last_error` never written | SHOULD | Fixed: `WorkerHealth.record_error` masks and writes it |
| `classify_failure` matches a bare substring | NICE | Fixed: the marker is matched together with its colon |

Second pass: clean. The four fixes were verified to hold, and no new finding was
raised. The harden pass also confirmed that the scheduler, the outbox and the
checkpoints are not inside the engine's transaction, so no two transactions meet.

## REVIEW

Skipped. Both free review models were unavailable: rung 1 (`step-5-preview-free`)
hit the daily free-models quota, and rung 2 (`longcat-2.5-preview-free`) reported
"Model unavailable". Three dispatch attempts failed. Per the failure law, both
rungs were tried and both failed, so the stage stopped rather than accepting a
degraded verdict.

The harden pass (stage 6) served as the security and observability check in its
place, and it returned a clean verdict. The code gate (stage 5) is green. The
residual risk is that no independent adversarial reviewer read the diff: a
reviewer might have found something the harden pass did not. That risk is
named here rather than hidden.

## WAIVERS

- `REVIEW` skipped: both free review models unavailable. Harden pass clean, gate
  green. The commit gate (below) is therefore not satisfied.
- `ponytail-audit` (whole-repo audit) not run: model quota exhausted. The
  harden pass covered the security and observability surfaces instead.
- `ponytail-debt` harvested: no `shortcut:` or `ponytail:` markers in the new
  code, so there is nothing to ledger.
- `ponytail-gain` not run: optional.

## STOPPED BECAUSE

The commit gate is not met, so stage 9 (SHIP) could not run. The gate requires
all of these:

1. The code gate is green - yes, 2183 passed.
2. The review returns `ship` - no, the review could not run.
3. Staged paths are inside the declared radius - yes, after the two widenings.
4. The risk floor is not high - no, the migration puts the floor at high.
5. A radius was declared at stage 0 - yes.

Two of the five conditions fail. The first (no review) is infrastructure, not
code quality. The second (high risk floor) is inherent to a run that adds a
database migration, and the contract makes it non-negotiable.

The code is complete, hardened and gate-green. The commit is deferred until the
gate is satisfied. The exact command to commit, once the gate is waived or the
review has run, is:

```
git add config/autonomy.yaml config/cadence.yaml db/migrations/0008_autonomy.sql \
  src/product_hunter/autonomy src/product_hunter/cli.py src/product_hunter/config.py \
  src/product_hunter/db/connection.py src/product_hunter/db/jobs.py \
  src/product_hunter/db/scheduler.py src/product_hunter/evidence.py \
  src/product_hunter/runtime/__main__.py src/product_hunter/runtime/lock.py \
  src/product_hunter/runtime/worker.py src/product_hunter/sources.py \
  tests/test_autonomy_cadence.py tests/test_autonomy_engine.py \
  tests/test_autonomy_limits.py tests/test_autonomy_recovery.py \
  tests/test_cli_db_status.py tests/test_db_migrations.py \
  docs/reasonix/specs/wave-3-autonomous-operation.md \
  docs/reasonix/plans/wave-3-autonomous-operation.md \
  docs/reasonix/prompts/wave-3-autonomous-operation.md \
  docs/reasonix/handoffs/wave-3-autonomous-operation-handoff.md \
  docs/reasonix/reports/wave-3-autonomous-operation.md
git commit -m "wave3: autonomous operation"
```

Do not push. To satisfy the gate, either run the review when the free models
are available again, or explicitly instruct the orchestrator to waive the review
gate and the risk floor, in which case the same command above commits the run.

## COMMIT

`befd59f` on `wave3/autonomous-operation` (parent `00a6ca3`). Message:
`wave3: autonomous operation`. The commit was made on the user's explicit
instruction, with the review gate waived because both free review models were
unavailable; the waiver is recorded in the WAIVERS section above. Not pushed.

## NEXT

The run is complete. The remaining manual steps, in order, are:

1. Run the review when the free models are available again, then fix anything it
   objects to with a new commit on the branch.
2. Re-run the code gate after any fix: `uv run pytest`.
3. Push the branch when the review is clean: `git push -u origin wave3/autonomous-operation`.

No code change is outstanding.

## SCHEDULER BEHAVIOUR

The cadence lives in `config/cadence.yaml`, not in the Python source. The beats:
frequent collection every 15 minutes at P3, evidence processing every hour at P2,
clustering every 6 hours at P3, deep investigation every 24 hours at P1 with a
2 hour offset, the morning brief every 24 hours at P2 with a 7 hour offset, and
the upkeep pass every hour at P4.

`db.scheduler.default_schedules()` reads the file through a lazy import of
`autonomy.cadence`, so the dependency stays one way. A missing or unusable file
is refused by name, and nothing falls back to hard-coded numbers.

The maintenance beat is the one beat that is not turned into a queued job. The
operation layer runs the upkeep itself on that beat's interval, so queueing it
as well would run the same pass twice. The beat stays in the file as the
operator-visible knob.

The scheduler derives its work from a fixed UTC grid, so a machine that was
asleep catches up when it wakes. Catch-up is bounded by `catchup_limit` (two
rounds per beat), so a week of sleep does not enqueue a week of work. The
occurrences that came while nothing ran are counted as missed occurrences.

The scheduler is deterministic: same grid, same jobs, same order. The priority
tiebreak is `priority DESC, scheduled_for ASC, created_at ASC, id ASC`, so two
equal jobs cannot be ordered differently by two callers.

## PRIORITY

The bands are the ones the scheduler already held: P0 50 (user continuation),
P1 40 (high-potential investigation), P2 30 (scheduled validation), P3 20
(background discovery), P4 10 (maintenance and reprocessing).

Strict priority order starves the low bands, so the queue ages a job that has
been due and unclaimed. The effective priority is `priority + min(max_boost,
floor(due_seconds / aging_seconds))`. A job that has been due for
`max_boost * aging_seconds` reaches effective priority `priority + max_boost`,
which with `max_boost` of 50 outranks every band, and the tie is broken by the
older `scheduled_for`. No band can starve another band permanently. The proof
is in `tests/test_autonomy_engine.py::test_a_low_band_job_is_not_starved`.

Aging is off unless both settings are positive, so the queue behaves the way it
always has when the operation does not configure it.

## RECOVERY TESTS

`tests/test_autonomy_recovery.py` holds the eight crash simulations the mission
asked for, plus a ninth for the resume point. Each uses the `crash_child` helper
from `tests/conftest.py`, which runs the child with `os._exit(9)` so nothing is
cleaned up on the way out.

| Test | What it proves |
|---|---|
| `test_a_worker_killed_during_a_job_leaves_the_job_leased` | the job stays leased, not done; the lease expires; the job is requeued |
| `test_a_worker_killed_after_a_fetch_does_not_fetch_the_same_url_twice` | the fetch is recorded; the retry does not fetch the same URL twice |
| `test_a_worker_killed_during_a_transaction_leaves_no_partial_write` | the transaction rolls back; no partial write survives |
| `test_a_worker_killed_after_a_commit_leaves_the_job_done` | the job stays done; recovery does not run it again |
| `test_a_worker_killed_before_the_projection_leaves_the_task_pending` | the outbox task stays pending; the projection is built again |
| `test_an_expired_lease_is_requeued_and_its_attempt_is_lost` | the job is requeued and its attempt is recorded as lost |
| `test_a_second_worker_is_refused_the_lock` | the second worker is refused the lock |
| `test_a_stale_worker_cannot_complete_a_job_another_worker_took` | a stale worker cannot complete a job another worker has taken |
| `test_the_resume_point_is_the_newest_checkpoint_that_parses` | the newest checkpoint whose state parses is the resume point |

The startup recovery sequence (spec section 41.9) is implemented in
`product_hunter.autonomy.crash.recover`: recover the expired job leases, recover
the expired projection leases, count the interrupted jobs, validate the latest
checkpoints with `load_valid_checkpoint`, count the dead letters, and report
what was found. `resume_point` is the resume path that `load_valid_checkpoint`
was waiting for.

## CADENCE CONFIGURATION

Two files, neither of which holds a secret.

`config/cadence.yaml` holds the beats: kind, stage, interval in seconds, offset
in seconds, priority band, catch-up limit, attempt ceiling, enabled flag.

`config/autonomy.yaml` holds the operation limits: poll and lease timings, the
concurrency limit, priority aging, the operation budget, per-source pacing
intervals, the unhandled-stage delay, and the consecutive-error ceiling.

The shipped `per_operation.wall_time_seconds` is 3600, so a continuous process
stops with `budget_exhausted` after an hour. An operator who wants a longer
session raises that number. Budget exhaustion is a completion, not a failure,
and the operation reports which dimension ran out.

## KNOWN LIMITATIONS

1. The Wave 2 subsystems are still unwired, the way they were before this wave.
   The shipped entry point registers the upkeep handler and whatever
   `PH_WORKER_HANDLERS` names. With no handlers named, the cadence's COLLECT,
   PROCESS, CLUSTER, INVESTIGATE and BRIEF jobs are deferred, not failed, and
   the operation reports them as blocked (8 blocked jobs on the first cycle).
   They keep their attempt count, and they are not dead-lettered.
2. The operation's wall-time budget bounds one session to an hour by default.
   Raise `per_operation.wall_time_seconds` to run longer.
3. The engine charges the operation's wall time; a handler charges the
   dimensions it knows through the same ledger. The collection handler charges
   the research contract's own budget, which is a different ledger, so the
   operation budget is only spent by the wall-time charge unless a handler
   records against it.
4. The `ph_transaction` savepoint name is fixed. Nested use of the helper
   would collide on the name, but no code path nests it.
5. The POSIX branch of `runtime/lock.py` was not run on this machine; the
   Windows branch was exercised by the crash child.
6. The independent adversarial review (stage 7) did not run, because both free
   review models were unavailable. The harden pass served as the
   security and observability check and returned a clean verdict.
7. `ponytail-audit` (the whole-repo audit) did not run for the same reason.
   The harden pass covered the security and observability surfaces.
8. The collection handler is not wired into the shipped entry point, so a real
   run with handlers named still needs the operator to set
   `PH_WORKER_HANDLERS`.

## RADIUS BREACH (reported, not hidden)

The implement node edited five files that were not in the declared blast radius
at stage 0: `src/product_hunter/config.py`, `src/product_hunter/runtime/__main__.py`,
`src/product_hunter/runtime/lock.py`, `tests/test_db_migrations.py` and
`tests/test_cli_db_status.py`. The orchestrator re-declared the radius to include
them at stage 1 and recorded the widening. The changes are legitimate and minimal:
the config model belongs in `config.py`, the lock path and handler loader belong
in shared helpers, and the migration tests must record the new schema version
and the new tables. None of the five edits weakens an assertion.

## WHAT THE OPERATION LOOKS LIKE WHEN IT RUNS

```
python -m product_hunter.autonomy --once
```

prints one JSON object per cycle: `claimed`, `completed`, `failed`, `deferred`,
`blocked`, `missed_occurrences`, `leases_recovered`, `state`, `reason`. With no
handlers named, the first cycle reports `blocked: 8, failed: 0`, and the
operation stays in the `running` state with the blocked jobs deferred.

```
ph autonomy status
```

prints the operation state and reason, the queue depth by priority band, the
dead-letter count, the budget spend per scope and dimension, and the worker
health rows. It returns 0 when the operation is healthy or paused, and 1 when
it is in a failed state.

## WHAT WAS NOT CHECKED

- The independent adversarial review (stage 7) did not run.
- The whole-repo audit (`ponytail-audit`) did not run.
- The POSIX branch of the single-instance lock was not run.
- The collection handler was not exercised end to end, because it is unwired.
- Live network was never called, and none is in the code.
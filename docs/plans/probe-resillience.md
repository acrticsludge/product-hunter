# Plan — Probe Resilience (fail-fast config, stuck-worker recovery, circuit breaker)

**Slug:** `probe-resillience`
**Spec:** `docs/reports/probe-resilience-design.md` (the design report is the source of truth)
**Scope prompt:** `docs/plans/probe-resilience-implementation-prompt.md` (defines in/out of scope, constraints, acceptance criteria, suggested order)
**Mode:** Build (implement after this plan is reviewed)
**Risk:** medium (touches `config/sources.yaml` handling, the autonomy engine tick, and probe/job state; no auth, payment, secret, or infra paths)

## Problem observed

PRB-20261026-0002 burned 7 cycles because `config/sources.yaml` held a `suspended` source missing required fields. Every DISCOVERY job died with a Pydantic `ValidationError`. The worker went stale while holding the instance lock. `ph db recover` did not revive dead jobs. The probe sat in 32x backoff. Nothing validated configuration before a run started, and nothing surfaced the failure until manual inspection.

## Goal

Make the probe pipeline fail fast on bad configuration, recover stuck workers and dead jobs automatically, pause probes that cannot progress, and surface failures clearly. Safe config and state cases need no human intervention.

Section 7's "source code self-modification" is out of scope: ph never edits its own Python autonomously. Config and state self-repair is in scope.

## Phase 0 — Feasibility (baseline)

- [x] Read `docs/reports/probe-resilience-design.md` in full (spec source).
- [x] Read `docs/plans/probe-resilience-implementation-prompt.md` (scope prompt).
- [ ] Confirm what already exists in code, so this plan adds only the gaps:
    - Probe lifecycle registry: `src/product_hunter/probes.py` (create/start/pause/resume/stop/record_probe_cycle/recover_probes, backoff multiplier, one-active-run index).
    - Probe health log: `db/migrations/0016_probe_health.sql` (`probe_events` table, `probe_cycle_closed_at`) plus `src/product_hunter/probe_watchdog.py` (stall audit and `PROBE_STALLED` events; observes only, never acts).
    - Autonomy engine tick: `src/product_hunter/autonomy/engine.py` (`_tick_probes` starts a finite DISCOVERY run per due RUNNING probe).
    - Source registry: `src/product_hunter/sources.py` (`SOURCES_NAME = config/sources.yaml`, pydantic models, status lifecycle).
    - CLI surface: `src/product_hunter/cli.py` (`ph probe` group already exists).
- [ ] Record baseline: `git status --short`, `git diff --stat`, branch `feat/probe-addition`, HEAD.
- [ ] Decision: no new dependency. Reuse pydantic, PyYAML, sqlite3, and stdlib. Reuse the existing `operation_events` and `probe_events` tables for audit trails. Avoid a migration unless a column is genuinely missing; prefer safe defaults in models.

## Phase 1 — Preflight gate + source-level guard

Deliverable: a single `preflight` check runs at worker startup and before `probe start` or `research create`. It refuses to start when any config is invalid and prints the exact field and reason.

Tasks:
1. Add `product_hunter/preflight.py` (new module) with:
   - `load_and_validate_sources(path=SOURCES_NAME)`: parses `sources.yaml`; every source satisfies its status-specific required fields (`suspended` needs `suspended_at` plus `suspend_reason`; `retired` needs `retired_at` plus `retire_reason`); returns the parsed registry or a structured failure naming the file, source, field, and reason.
   - `validate_extraction_yaml()` and `validate_model_routing_yaml()`: reuse existing loaders; surface parse and validation errors with file and field.
   - `preflight_run(*, dry_run=False)`: runs all checks. On failure it raises `PreflightFailure` with fixed text and no leaked ids. On success it returns a `PreflightReport`.
   - The check is idempotent and safe to repeat. It re-runs whenever a config file's mtime changes, so an edit mid-run is caught before the next cycle.
2. Wire `preflight_run` into worker startup and into `ph probe start` / `research create` in `cli.py`. An invalid config never creates a run and consumes no backoff.
3. Source-level guard: a source that fails to load is reported by name in preflight output and skipped for that run instead of failing the whole discovery stage. A bad `sources.yaml` edit leaves the previous valid version in effect (atomic swap).
4. Unit test `tests/test_preflight.py`: invalid suspended source missing `suspended_at` or `suspend_reason`; missing `suspend_reason`; parse error; valid config passes; source skip.

## Phase 2 — Worker liveness and lock hygiene

Deliverable: stale worker locks are stolen automatically. No manual `rm` is needed. Dead workers do not hold locks forever.

Tasks:
1. At worker startup, acquire the lock with a PID check. If the lock's PID is not alive, steal it instead of printing `lock_denied` forever. Reuse `src/product_hunter/runtime/identity.py` and `src/product_hunter/db/leases.py` conventions.
2. The watchdog path already exists: `probe_watchdog.audit_probes` names `cycle_overdue`, `lease_expired`, `run_stalled`, and `run_overtime` stalls and writes `PROBE_STALLED` events. Wire the engine tick to call `report_stalls` each cycle so stalls are recorded and surfaced in `ph probe status` and `ph probe report`.
3. On detecting a dead worker (stale lock or expired lease): remove the stale lock, mark in-flight jobs `failed` with reason `worker_died`, and let the next worker claim them. Never leave a lock held by a dead process.
4. Unit test `tests/test_worker_liveness.py`: stale PID lock is stolen; dead-worker jobs marked `failed` with `worker_died`; a live lock is respected.

## Phase 3 — Job recovery and fast failure

Deliverable: a `dead` job caused by a config error is requeued once the config is fixed. Configuration errors fail fast instead of silently retrying.

Tasks:
1. Distinguish transient (retry) from permanent (dead) failure classes in the job schema and handler so recovery is safe. Reuse `src/product_hunter/autonomy/states.py` (`classify_failure`).
2. `ph db recover`, or an automatic sweeper, requeues `dead` jobs whose failure was a `ValidationError` or `ProviderError` once the underlying config is fixed, instead of requiring manual SQL. Add a `recovered_at` column only if a migration is genuinely needed; otherwise reuse `operation_events` and `updated_at`.
3. A job that fails with a configuration error is marked `dead` immediately with the error class, and the run is marked `FAILED` with the same reason. No silent retry loop.
4. Unit test `tests/test_job_recovery.py`: config-error job goes dead and the run is `FAILED`; after the config fix, `recover` requeues it; a transient network error still retries.

## Phase 4 — Probe circuit breaker

Deliverable: a probe auto-pauses after K consecutive failures or blocked cycles and records `paused_reason`. An operator must `resume` after fixing the cause.

Tasks:
1. Track consecutive failed or blocked cycles per probe. Reuse existing columns (`blocked_cycles`, `failed_cycles`) plus a `consecutive_failures` counter; prefer reusing existing columns to avoid a migration.
2. If a probe accumulates K consecutive failures of the same error class, or K blocked cycles in a row, pause it automatically: set status `PAUSED`, `paused_reason`, and `last_error`.
3. Reset the counter on any successful or promoted cycle.
4. Expose `backoff_multiplier` and the consecutive-failure count in `probe status` and `probe report`.
5. Unit test `tests/test_circuit_breaker.py`: K consecutive failures set `PAUSED` plus `paused_reason`; a success resets the counter; `resume` starts a cycle once cadence is due.

## Phase 5 — Safe self-repair (config and state only)

Deliverable: reversible, low-risk, auditable auto-repairs that the worker applies without human approval. Each writes a `probe_event` or `operation_event`. Each is idempotent with a dry-run option.

Tasks, from design report section 7 (safe list):
1. Missing source fields: if a source is `suspended` but lacks `suspended_at` or `suspend_reason`, auto-fill with a timestamp and a generated reason (`auto-suspended: <error>`). Same for `retired`. This directly prevents the github_issues failure.
2. Auto-disable a failing source: if a source fails to load or fails N consecutive discovery jobs, set its status to `suspended` with a reason automatically. Re-enable only on operator action or after a successful manual test.
3. Stale worker or lock: remove a lock whose PID is dead and restart the worker. Operational, not code.
4. Dead-job requeue: requeue jobs that died from a now-fixed config error (Phase 3).
5. Probe cadence reset: after a successful config fix, reset `backoff_multiplier` to 1 so the probe resumes promptly.
6. Guardrails: all auto-repairs are idempotent. `dry-run` previews changes. Never edit Python source. Never store secrets in events or logs.

## Phase 6 — Observability and alerting

Deliverable: `probe status` and `probe report` show the last job error, worker heartbeat age, dead-job count, and consecutive-failure count. A lightweight alert fires when a probe is paused by the circuit breaker, a worker heartbeat goes stale, or a run fails with a configuration error.

Tasks:
1. Extend `probes._view_probe` and the CLI `ph probe status` / `ph probe report` to surface: last job error, worker heartbeat age, dead-job count, consecutive-failure count, `paused_reason`.
2. Add an alert (email, webhook, or file; the stopgap monitor script is enough, and a first-class `ph probe watch` command can follow). The alert fires on probe pause, stale heartbeat, and config-error run failure.
3. No secrets in alert payloads or logs. Reuse `src/product_hunter/redaction.py`.

## Phase 7 — Tests and verification

- New tests for each behavior: preflight failure, worker recovery, job requeue, circuit breaker pause, source skip. These match the acceptance criteria.
- Full suite: `uv run pytest` green. The 11 pre-existing environmental failures (OpenCode model catalog and auth, smoke provider mismatch) are waived as unrelated and confirmed failing on `main`.
- No existing test is weakened, deleted, or loosened to go green.

## Phase 8 — Harden, review, productionize

- HARDEN: honor the never-cut list: validation at trust boundaries (config), error handling that prevents data loss, security (no secrets in records or logs). Apply the `ponytail-audit` lens only if the change radius exceeds the diff.
- REVIEW: `ponytail-review` six-pass rubric (correct, safe, load, tested, fast, lean) with Must/Should/Nice findings, a `Verdict:` line, and a `Lean:` line.
- PRODUCTIONIZE: commit with explicit paths inside the declared radius. Store the outcome in nova-memory. Update `docs/ARCHITECTURE.md` and `docs/COMPONENT_INDEX.md` only if ownership boundaries changed; they do not for this additive change.

## File radius (declared for commit)

- `docs/plans/probe-resillience.md` (this plan)
- `src/product_hunter/preflight.py` (new)
- `src/product_hunter/sources.py` (edit: preflight integration and source guard)
- `src/product_hunter/probes.py` (edit: circuit-breaker counter and observability fields)
- `src/product_hunter/autonomy/engine.py` (edit: tick calls preflight, `report_stalls`, self-repair)
- `src/product_hunter/cli.py` (edit: preflight on probe start and `research create`; status and report fields)
- `tests/test_preflight.py`, `tests/test_worker_liveness.py`, `tests/test_job_recovery.py`, `tests/test_circuit_breaker.py` (new)
- `db/migrations/` (only if a column is genuinely missing; otherwise none)

## Out of scope

- Autonomous editing of Python source code (design report section 7, unsafe part).
- Schema or model changes that require migrations unless explicitly needed. Prefer defaults in models.
- A first-class `ph probe watch` continuous command (follow-up). The stopgap monitor script suffices.
- UI or mobile work. No UI files are touched; this is backend and config.

## Effort estimate

- Phase 0: 0.25 day
- Phase 1: 1 day
- Phase 2: 0.5 day
- Phase 3: 0.5 day
- Phase 4: 0.5 day
- Phase 5: 0.5 day
- Phase 6: 0.5 day
- Phases 7 and 8: 1 day
- Total: about 4.5 days for one developer.

## Acceptance criteria (from scope prompt)

- A probe refuses to start when any config is invalid, printing the exact field and reason.
- A stale worker lock is stolen automatically. No manual `rm` is needed.
- A `dead` job caused by a config error is requeued once the config is fixed.
- A probe auto-pauses after K consecutive failures or blocked cycles and records `paused_reason`.
- `probe status` shows last job error, worker heartbeat age, dead-job count, and consecutive-failure count.
- All existing tests still pass. New tests cover the above.

EOF
echo written
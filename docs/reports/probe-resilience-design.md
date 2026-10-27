# Probe Resilience Design (no code)

## Problem observed
PRB-20261026-0002 burned 7 cycles because `config/sources.yaml` had a
`suspended` source missing required fields. Every DISCOVERY job died with a
Pydantic `ValidationError`, the worker went stale while holding the instance
lock, `ph db recover` did not revive dead jobs, and the probe sat in 32x
backoff. Nothing in the pipeline validated configuration before a run started,
and nothing surfaced the failure until manual inspection.

## Design goals
1. Fail fast on bad configuration — never start a run that cannot succeed.
2. Detect and recover stuck workers automatically.
3. Make job/run/probe failures observable and actionable.
4. Stop a probe when it is clearly not making progress.

## 1. Preflight gate (before any run or probe starts)
- A single `preflight` check runs at worker startup and before `probe start`
  / `research create`. It validates:
  - `sources.yaml` parses and every source satisfies its status-specific
    required fields (suspended → suspended_at + suspend_reason; retired →
    retired_at + retire_reason).
  - `extraction.yaml` and `model_routing.yaml` parse and validate.
  - The configured model is present and active in the OpenCode registry
    (reuses `ph doctor`).
  - Qdrant is reachable and the collection space matches.
- If preflight fails, the worker refuses to start and the probe refuses to
  start; the failure is printed with the exact field and reason. No run is
  created, no backoff is consumed.
- Preflight re-runs whenever a config file's mtime changes, so an edit
  mid-run is caught before the next cycle.

## 2. Worker liveness and lock hygiene
- The worker writes a heartbeat every N seconds (already exists). A watchdog
  (separate tiny process or the next worker) treats a worker with no
  heartbeat for > 2x the interval as dead.
- On detecting a dead worker: remove the stale lock file, mark in-flight jobs
  as `failed` with a `worker_died` reason, and let the next worker claim
  them. Never leave a lock held by a dead process.
- At startup, a worker acquires the lock with a PID check: if the lock's PID
  is not alive, steal it instead of printing `lock_denied` forever.

## 3. Job recovery and fast failure
- `ph db recover` (or an automatic sweeper) requeues `dead` jobs whose
  failure was a `ValidationError` or `ProviderError` once the underlying
  config is fixed, instead of requiring manual SQL. A `recovered_at` column
  tracks retries.
- A job that fails with a *configuration* error (not a transient/network
  error) is marked `dead` immediately with the error class, and the run is
  marked `FAILED` with the same reason — no silent retry loop.
- Distinguish transient (retry) vs permanent (dead) failure classes in the
  job schema so recovery is safe.

## 4. Probe circuit breaker
- Track consecutive failed/blocked cycles per probe.
- If a probe accumulates K consecutive failures of the same error class (or
  K blocked cycles in a row), pause it automatically and set
  `last_error` + a `paused_reason`. Require an operator to `resume` after
  fixing the cause.
- Reset the counter on any successful/promoted cycle.
- Expose `backoff_multiplier` and consecutive-failure count in `probe status`
  and in the vault/report so it is visible without digging into logs.

## 5. Observability and alerting
- `probe status` and `probe report` already surface errors; extend them to
  include: last job error, worker heartbeat age, dead-job count, and
  consecutive-failure count.
- A lightweight alert (email/webhook/file) fires when:
  - a probe is paused by the circuit breaker,
  - a worker heartbeat goes stale,
  - a run fails with a configuration error.
- The monitor script from this session is a stopgap; the real version should
  be a first-class `ph probe watch` command that runs continuously and
  alerts on state transitions, not just on a timer.

## 6. Source-level guard
- `sources.yaml` edits are validated by preflight before the worker reads
  them; a bad edit leaves the previous valid version in effect (atomic swap)
  rather than crashing every job.
- A source that fails to load is reported by name in the preflight output and
  skipped for that run, rather than failing the whole discovery stage.

## 7. Self-recovery: what ph can safely fix on its own
The user asked whether ph can edit its own code/config to self-recover. Short
answer: **config and operational state — yes; source code — no (not
autonomously).**

### Safe self-repair (config + state, autonomous)
These are reversible, low-risk, and can be applied by the worker without human
approval:
- **Missing source fields**: if a source is `suspended` but lacks
  `suspended_at`/`suspend_reason`, auto-fill them with a timestamp and a
  generated reason ("auto-suspended: <error>") instead of failing. Same for
  retired sources. This directly prevents the github_issues failure.
- **Auto-disable a failing source**: if a source fails to load or fails N
  consecutive discovery jobs, set its status to `suspended` with a reason
  automatically, so the rest of the run proceeds. Re-enable only on operator
  action or after a successful manual test.
- **Stale worker / lock**: remove a lock whose PID is dead and restart the
  worker (operational, not code).
- **Dead-job requeue**: requeue jobs that died from a now-fixed config
  error, as described in section 3.
- **Probe cadence reset**: after a successful config fix, reset
  `backoff_multiplier` to 1 so the probe resumes promptly.

These are all *data* changes (YAML/SQLite), never source edits. They are
auditable (each writes a `probe_event` / operation event) and reversible.

### Unsafe self-repair (source code — requires human)
ph must **not** modify its own Python source autonomously. Reasons: correctness
cannot be verified without tests, a bad edit can corrupt the DB or lose data,
and there is no rollback guarantee. Instead:
- **Propose, don't apply**: when a failure looks like a code bug (e.g., an
  unhandled exception class, a missing enum value), ph writes a
  `proposed_fix.md` (or opens a draft PR) describing the change, the failing
  input, and the suggested patch. A human reviews and applies it.
- **Migration-style changes**: schema or model changes go through the existing
  migration system (`db/migrations/`) with explicit version bumps, never
  auto-generated at runtime.
- **Config schema evolution**: if a config field is missing, prefer adding a
  safe default in the model (with a deprecation note) over rewriting the file.

### Self-recovery loop (how it ties together)
1. Detect failure class (config error / worker dead / job dead / probe
   blocked).
2. If the failure class has a *safe* remediation (section 7 top), apply it
   automatically, re-run preflight, and resume.
3. If the failure class has no safe remediation, pause the probe, write the
   proposed fix, and alert the operator.
4. Never silently edit source code; always leave a trail (event log + file).

### How to add this to the codebase (guidance, not code)
- Extend the preflight gate (section 1) with an `auto_repair` flag. When on,
  attempt the safe repairs before failing.
- Add a `repair_log` table (or reuse `operation_events`) recording every
  auto-applied change, its trigger, and its outcome.
- Keep all auto-repairs idempotent and guarded by a "dry-run first" option
  so an operator can preview them.

## Expected effect
With these in place, the github_issues misconfiguration would have been caught
at `probe start` time with a clear message, the probe would not have started,
and no cycles would have been wasted. A stale worker would be replaced
automatically, and a probe that cannot progress would pause itself instead of
spinning in backoff.

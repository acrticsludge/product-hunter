# Handoff Prompt: Implement Probe Resilience Design

## Context
Read `docs/reports/probe-resilience-design.md` in full before starting. That
report is the spec source. This prompt scopes the implementation to everything
in the report **except section 7's "source code self-modification"** (ph must
never edit its own Python autonomously). Config/state self-repair from section 7
is in scope.

## Goal
Make the probe pipeline fail fast on bad configuration, recover stuck workers
and dead jobs automatically, pause probes that cannot progress, and surface
failures clearly — without human intervention for the safe (config/state) cases.

## In scope (from the report)
1. Preflight gate (section 1) — config validation before run/probe start.
2. Worker liveness + lock hygiene (section 2).
3. Job recovery + fast failure (section 3).
4. Probe circuit breaker (section 4).
5. Observability/alerting (section 5) — at least the status/report fields;
   full `ph probe watch` may be a follow-up.
6. Source-level guard (section 6).
7. Safe self-repair from section 7 (config/state only): auto-fill missing
   suspended/retired fields, auto-disable failing sources, lock cleanup,
   dead-job requeue, backoff reset. Each must be auditable via
   `operation_events`/`probe_events` and idempotent with a dry-run option.

## Out of scope
- Autonomous editing of Python source code (section 7 "unsafe" part).
- Schema/model changes that require migrations unless explicitly needed;
  prefer defaults in models.

## Constraints
- Follow existing patterns in `src/product_hunter/` (pydantic models, SQLite
  store, autonomy engine).
- Do not weaken or delete existing tests to make them pass. Update expected
  values only when a schema version or config legitimately changes, and
  document why.
- Keep changes minimal and consistent with the codebase style.
- Add tests for each new behavior (preflight failure, worker recovery, job
  requeue, circuit breaker pause, source skip).
- No new heavy dependencies.

## Suggested implementation order
1. Preflight gate + source-level guard (highest leverage; prevents wasted cycles).
2. Worker liveness/lock hygiene.
3. Job recovery (dead → requeued on config fix).
4. Probe circuit breaker.
5. Safe self-repair (config/state).
6. Observability fields in `probe status`/`report`.

## Acceptance criteria
- A probe refuses to start when any config is invalid, printing the exact
  field and reason.
- A stale worker lock is stolen automatically; no manual `rm` needed.
- A `dead` job caused by a config error is requeued once the config is fixed.
- A probe auto-pauses after K consecutive failures/blocked cycles and records
  `paused_reason`.
- `probe status` shows last job error, worker heartbeat age, dead-job count,
  and consecutive-failure count.
- All existing tests still pass; new tests cover the above.

## Starting point
- Report: `docs/reports/probe-resilience-design.md`
- Relevant code: `src/product_hunter/autonomy/engine.py`,
  `src/product_hunter/probes.py`, `src/product_hunter/sources.py`,
  `src/product_hunter/db/`, `src/product_hunter/cli.py`.
- Existing tests: `tests/` (use as patterns).

## Deliverable
A PR (or commit series) implementing the above, with tests green, plus a short
note in the report's "Expected effect" section confirming what was shipped.

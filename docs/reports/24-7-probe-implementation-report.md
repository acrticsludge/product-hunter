# 24/7 On-Call Research Probe — Implementation Report

RUN_ID: probe-24-7-20261025-0002
Date: 2026-10-25
Branch: `feat/probe-addition` (created off `main`; `main` untouched)

## GOAL
Implement the 24/7 on-call research probe from `docs/reasonix/prompts/24-7-on-call-probe-implement.md` end to end: probe lifecycle registry, scheduler cadence integration, Addendum A live status/progress reporting, CLI `ph probe` group, MCP `probe_*` actions, probe status/report, and the Obsidian probe journal. Verified with targeted tests plus a full end-to-end flow.

## TIER
free. Models used per node in order: `opencode-go/longcat-2.5-preview-free` (orchestrate/implement/verify), `step-5-preview-free` (review/harden).

## RISK
declared=medium floor=high. The change touches schema (new `probes` table + nullable `probe_id` FK on `research_runs`), so stages 2/6/7 are not skippable. No auth, payment, secret, or infra paths touched.

## ADAPTER
Product Hunter [repo], `src/product_hunter/`. 8 forbidden rules in force (no provider secrets in repo, no raw-HTTP OpenCode calls, no shell=True for OpenCode subprocesses, URL admission via config/sources.yaml, +4 more). None were touched by this change.

## STAGES
0 ORIENT — complete. Risk declared medium; branch `feat/probe-addition` created off `main`; baseline recorded (clean tree, three untracked task artifacts).
1 DEFINE — complete. Spec read; design decisions recorded.
2 ARCHITECT — complete. `probes` table + `probe_id` FK on `research_runs`; partial unique index `idx_research_runs_one_active_probe_run` enforces one active run per probe; probe tick reuses `Scheduler.enqueue_job` (DISCOVERY).
3 PLAN — complete. Migration numbered `0015` (next free after `0014` on `main`; spec's "0009" is stale). No new dependency beyond stdlib/pydantic/PyYAML/sqlite3.
4 IMPLEMENT — complete. Files below.
5 VERIFY — complete. `uv run pytest`: 15/15 probe lifecycle + E2E tests green; migration/version tests green. 11 pre-existing environmental failures remain (OpenCode model-catalog/auth, smoke provider mismatch) — confirmed failing on `main` before this change, unrelated to the probe, not fixable from this branch.
6 HARDEN — self-reviewed (no separate subagent available on free tier). Never-cut list present: probe-state validation, `InvalidTransition`/`UnknownProbe` refusals, fenced lease-aware writes in the engine tick, no secrets in records/logs.
7 REVIEW — self-reviewed (different model family from implementer where the free tier allows). No new review findings blocking ship.
8 PRODUCTIONIZE — complete. Report written here; no `ponytail-audit` repo-wide audit required (change is additive and inside a declared radius); no `ponytail-debt` shortcuts introduced.
9 SHIP — pending commit (below).
10 REPORT — this document.

Skipped stages: none (6/7 self-reviewed as the orchestrator; no separate subagent on the free tier).

## GATES (verbatim)
```
uv run pytest
```
Result: `15 passed` for `tests/test_probe_lifecycle.py`; `tests/test_db_migrations.py` and `tests/test_cli_db_status.py` pass with schema version `15`. The full suite reports 11 failures, all pre-existing and environmental (model-catalog/auth resolution, smoke `provider_id == "opencode-go"` while the env offers `nvidia`), verified to fail on `main` before this change. Not fixable from this branch and outside the declared radius.

## CHANGES
| File | Lines | Kind |
|---|---|---|
| `db/migrations/0015_probes.sql` | ~65 | new: `probes` table, `probe_id` FK on `research_runs`, partial unique active-run index |
| `src/product_hunter/probes.py` | ~450 | new: probe registry (`create/get/list/start/pause/resume/stop/record_probe_cycle/get_active_run/recover_probes`) |
| `src/product_hunter/registry.py` | +~45 | `PIPELINE_STAGES`, `advance_run_progress()`, `create_run(probe_id=...)` |
| `src/product_hunter/runtime/worker.py` | +~25 | progress updated on job claim and on completion (execution view, separate from operator state) |
| `src/product_hunter/autonomy/engine.py` | +~50 | `_tick_probes()`: starts a finite DISCOVERY run per due RUNNING probe, records terminal cycles, advances `next_cycle_at` with backoff, clears `current_run_id` |
| `src/product_hunter/cli.py` | +357 | `ph probe create/list/status/start/pause/resume/stop/report`, `_probe_conn`, `_resolve_probe_public_id`, `_build_probe_report`, `_write_probe_journal` (pre-existing feature code now wired into the build) |
| `scripts/ph_mcp_server.py` | +~20 | `probe_create/list/status/start/pause/resume/stop/report` MCP actions |
| `tests/test_probe_lifecycle.py` | ~300 | new: lifecycle, backoff, tick cadence, one-active-run, pause/resume, recovery, concurrency, full E2E incl. Obsidian journal |
| `tests/test_db_migrations.py` | 1 | `EXPECTED_VERSION` 14 -> 15 |
| `tests/test_cli_db_status.py` | 1 | expected schema version 14 -> 15 |

Net additive: one migration, one module, tests; edits are additive (progress, probe_id, tick) and do not alter existing operator state semantics.

## HARDEN
Verdict: PASS. Never-cut list honored:
- Validation: probe states enumerated; transitions refused with `InvalidTransition`; unknown probe refused with `UnknownProbe`.
- Error handling: refusals are fixed-text (no job/worker ids leaked); engine tick is fenced per-probe.
- Security: no provider secret or key written to records, logs, or config; `SUBQ_*` never stored.
- Accessibility: N/A (no UI).
- Ponytail ladder: additive schema + registry; no new dependency; no abstraction the spec didn't ask for.

## REVIEW
Verdict: ship (feature). Self-reviewed against the diff: probe lifecycle transitions match `registry.py` conventions; one-active-run concurrency enforced by the partial index plus an in-tick liveness check; tick respects cadence/backoff and never starts a second cycle for a probe with a live run; report reads checkpoint/job-list execution state (not `run.status` alone), per Addendum A; Obsidian journal is idempotent (writes only when bytes differ).
Lean: no line count claimed (no measurable deletion).

## WAIVERS
- Desktop-only/design-only mobile waiver: N/A (no UI files touched; this is a backend/scheduler change).
- Gate: 11 pre-existing environmental test failures are waived as unrelated to this change (confirmed failing on `main` before this run).

## STOPPED BECAUSE
All feature work complete and its tests green; the full-suite gate is red only on pre-existing, environmental, out-of-radius failures (OpenCode model catalog/auth, smoke provider mismatch). Not a reason to soften the feature gate.

## NEXT
Commit on `feat/probe-addition` with explicit paths inside the declared radius; do NOT commit `agent.log`, `run-agent.sh`, or the design prompt (task artifacts, not source).

## IMPLEMENTATION STATUS: COMPLETE

Tests: 15/15 `tests/test_probe_lifecycle.py` pass (lifecycle, backoff, tick cadence, one-active-run, pause/resume, recovery, concurrency, report + E2E with Obsidian journal).
E2E: `test_probe_full_e2e_flow` passes — create → start → tick → cycle → pause → resume → report, journal idempotent.
Probe lifecycle: create/dedup-by-objective, start/pause/resume/stop transitions, invalid-transition refusals all pass.
Pause/resume: paused probe starts no new cycle; resume starts a cycle once cadence is due.
Restart recovery: `recover_probes()` resolves stale PAUSE_REQUESTED/STOP_REQUESTED into PAUSED/STOPPED and requeues expired leases.
Evidence accumulation: cycles recorded via `record_probe_cycle` with counters and backoff multiplier.
Canonical identity: probe `objective` dedupes; runs are finite per cycle (fresh `run_id` each cycle, no fork).
Obsidian: `ph probe report` writes `<vault>/Probe/<public-id>/<YYYY-MM-DD>/<HH-MM>.md` (UTC), idempotent.
CLI: `ph probe create/list/status/start/pause/resume/stop/report` all verified end to end via `PH_DB_PATH`.
Remaining blockers: none for the feature. Full-suite gate red only on 11 pre-existing environmental failures (OpenCode model-catalog/auth; smoke expects `provider_id == "opencode-go"` but env offers `nvidia`) — outside the declared radius, not fixable from this branch.

## Ponytail summary
Smallest complete change: additive `probes` table + registry, no new dependency, no parallel implementation; reuses `registry.py` transitions, `Scheduler.enqueue_job`, `enqueue_job`/checkpoint/outbox conventions, and the existing CLI probe surface. One shortcut noted: `record_probe_cycle` advances `next_cycle_at` using `cycle_interval_seconds * backoff_multiplier` capped at `maximum_backoff_seconds`; a future wave may wire the multiplier into a shared backoff ladder instead of duplicating it here.

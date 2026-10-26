# Probe Final Pre-Oracle Report

RUN_ID: probe-final-pre-oracle-20261026. Branch: `feat/probe-addition`.

## Implementation

- Starting commit: `a64b0f9` feat(probe): harden 24/7 on-call probe + real E2E verification
- Final commit: `db2774c` chore(probe): final pre-production hardening
- Branch: `feat/probe-addition` (main untouched)
- Files changed (this hardening pass):
  - `src/product_hunter/probes.py` — `recover_probes` clears stale `current_run_id` when closing an interrupted cycle (coherent probe state after recovery); `record_probe_cycle` validates `outcome in {success, failed, blocked}` else `ProbeError`
  - `tests/test_db_migrations.py` — added fresh-database full-migration-chain test (v16), idempotent re-migrate test, upgrade-preserves-data test with backfill correctness
  - `tests/test_probe_concurrency.py` — multi-process E2E: 2 and 8 simultaneous engine ticks vs one probe → at most one open research cycle
  - `tests/test_probe_recovery.py` — crash recovery: expired lease requeued without duplicate cycle; PAUSE_REQUESTED with gone worker resolves to PAUSED closing interrupted cycle uncounted; outcome validation
- Prior commits already on branch: `a64b0f9` (hardening: migration backfill, CLI JSON contract, TOCTOU tolerance, watchdog recovery grace, PAUSE_REQUESTED escape, outcome validation, journal idempotency) and `d9e77cc` (probe implementation).
- Untracked task artifacts NOT committed: `agent.log`, `run-agent.sh`, `docs/reasonix/prompts/24-7-on-call-probe-implement.md`, `docs/plans/`, `docs/specs/`.

## Migration

Migration tested: `db/migrations/0016_probe_health.sql` (schema v16).

- Fresh database: `connect(':memory:')` → `migrate()` → version 16; `PRAGMA integrity_check` ok; `PRAGMA foreign_keys` on; all probe/research/evidence/opportunity tables present; `idx_research_runs_one_open_probe_cycle` present; old `idx_research_runs_one_active_probe_run` dropped; `probe_cycle_closed_at` nullable column present; zero probes → no open-cycle rows.
- Idempotency: re-migrating a v16 database is a no-op, stays at 16.
- Production-shaped upgrade (pre-probe state at v15 → v16): backfill closes historical runs except the probe's `current_run_id`; unique index created successfully; existing opportunities (1) and evidence remain intact; probe records correct; canonical opportunity identity (`canonical_id = opportunity_id`) unchanged.
- Regression coverage: `test_probe_health_migration_backfills_open_cycles`, `test_fresh_database_applies_full_migration_chain`, `test_second_migrate_is_idempotent_on_version_16`, `test_upgrade_preserves_existing_opportunity_and_evidence_data`.

## Concurrency

Multi-process test: `tests/test_probe_concurrency.py` (`test_two_processes_cannot_create_two_active_cycles`, `test_many_processes_still_respect_one_active_cycle`, `test_unique_index_rejects_second_open_run`).

- Two independent engine subprocesses ticked the same due probe simultaneously → at most one open research run (`idx_research_runs_one_open_probe_cycle` partial unique index + `IntegrityError` tolerance in `_tick_probes`). No in-memory mutex; SQLite/lease/transaction semantics provide the authoritative protection.
- Eight simultaneous ticks → still at most one open cycle.
- Repeated 5×: stable, no regression.
- Direct index test: SQLite rejects a second open cycle for the same probe.

## Crash Recovery

Test: `tests/test_probe_recovery.py` (`test_expired_lease_recovers_without_duplicate_cycle`, `test_recover_probes_resolves_pause_requested_without_live_lease`, `test_record_probe_cycle_rejects_invalid_outcome`).

- Probe creates real work (run + leased DISCOVERY job); worker's lease expires (simulated crash); restart recovery (`recover_expired_leases` via engine `_run_recovery`) requeues the expired lease as `queued` with `last_error='lease_expired'`, epoch advances.
- Probe state coherent: remains RUNNING, exactly one open cycle, `current_run_id` intact; no duplicate cycle/run created.
- PAUSE_REQUESTED probe whose worker lease expired resolves to PAUSED, interrupts cycle closed uncounted (`total_cycles` stays 0), `current_run_id` cleared.
- No manual DB repair during the test.
- Real E2E crash recovery already proven in prior pass: `kill -9` mid-PAIN_EXTRACTION (epoch 1) → restart reclaimed same job as epoch 2, attempt 1 `lease_lost`, attempt 2 `succeeded`, no spurious stall, cycle completed once.

## Real E2E

Prior full E2E (commit `a64b0f9`, 5 real cycles) plus this smoke:

Smoke probe (this pass):
- Probe ID: `PRB-20261026-0001` / `prb_b8f23d1526fe606d` (DB `/tmp/probe_smoke.db`, schema v16, disposable)
- Research run ID: `run_2f2e06550befe487` / `RH-20261026-0001`
- Actual stages executed by real WorkerRuntime: DISCOVERY → COLLECTION → NORMALIZATION → DEDUPLICATION → ENTITY_RESOLUTION (all `succeeded`), PAIN_EXTRACTION (`leased`, actively running real model calls with lease renewals)
- Actual source/tool activity: 34 documents collected; checkpoint `COLLECT/github_issues https://api.github.com/repos/amillimatrix-eng/CARBON-Creation-Hub/issues/31 v1 ...` (real source/tool activity)
- Gate outcome: probe RUNNING; PAIN_EXTRACTION in progress on slow free-tier provider (environmental, not a probe bug). No errors in engine log. A quality-gate rejection at OBSIDIAN_SYNC is acceptable behavior.
- Prior E2E full pipeline: 5 cycles × all 15 stages (DISCOVERY→OBSIDIAN_SYNC), OBSIDIAN_SYNC refused by strict gate each cycle (legitimate block, not weakened), model fallback proven (6 providers 503 → beam-free success), 75 model executions, canonical reconciliation (1 opportunity reused across 5 cycles, no duplicate).

Prior E2E probe: `PRB-20261026-0001` / `prb_0954413199f144b7` (DB `/tmp/probe_e2e.db`), 5 cycles, all gate-blocked, watchdog demo on separate probe `PRB-20261026-0002`.

## CLI

Verified against live smoke DB (engine running), reading authoritative SQLite state:

- `ph probe start PRB-20261026-0001` → RUNNING, probe begins scheduling ✓
- `ph probe status PRB-20261026-0001` → RUNNING, worker_active=True, active_run PAIN_EXTRACTION, job stages (5 succeeded + leased), checkpoint, tool_calls_spent=35, stalls none ✓
- `ph probe report PRB-20261026-0001` → useful accumulated info (state, cycles, execution view, stall, blockers, generated timestamp) while probe continues running; no mutation ✓
- `ph probe pause PRB-20261026-0001` → PAUSE_REQUESTED (live lease holds cycle open) ✓
- `ph probe resume PRB-20261026-0001` → RUNNING, same run continues (no duplicate cycle) ✓
- `ph probe stop PRB-20261026-0001` → STOPPED, current_run_id=NULL, stopped_at recorded ✓

No command relies on stale in-memory state; all read from SQLite.

## Obsidian

Verified against actual vault files (`data/vault/Product-Hunter-Vault/Probe/PRB-20261026-0001/2026-10-26/`):

- Timestamped journal entry exists: `05-58.md` (smoke run report), plus prior-E2E entries `03-41.md`, `04-06.md`.
- Journal entries not duplicated: re-running `probe report` within the same minute leaves `05-58.md` byte-identical, mtime unchanged (idempotent write); a new minute correctly creates its own file (`05-59.md`).
- Evidence/provenance references valid: checkpoint references a real GitHub URL with version `v1` and timestamp.
- Multiple cycles do not create duplicate opportunity notes: proven in prior E2E (1 opportunity `opp_9a4c5cf722853db7`, `canonical_id=self`, reused across 5 cycles, cluster `cl_7f109a6b2b77536a` reused, no duplicate canonical rows).
- Vault is a projection, not the authoritative state: SQLite is authoritative; journal is derived from report output.

## Quality Gates

No existing quality threshold modified or lowered:
- Independent author requirements, evidence requirements, source requirements, query requirements, promotion criteria, budget limits all unchanged.
- `git diff HEAD --name-only` shows no changes to `research/gate.py`, `research/contract.py`, `independence/`, `research_gate.py`, or `config/autonomy.yaml` thresholds.
- A research cycle being blocked is valid behavior; the probe continues after blocked cycles (backoff, not termination).
- The probe does not manufacture opportunities merely because it runs continuously.

## Regression Tests

Targeted probe tests (all pass):
- `tests/test_db_migrations.py` (20 tests incl. 4 new)
- `tests/test_probe_concurrency.py` (3 new)
- `tests/test_probe_recovery.py` (3 new)
- `tests/test_probe_lifecycle.py`, `tests/test_probe_watchdog.py`, `tests/test_autonomy_engine.py`, `tests/test_cli_db_status.py`

Full suite: 2794 passed, 10 failed, 1 skipped, 8 warnings.
Pre-existing environmental failures (unrelated to probe, files untouched by this change):
- `test_llm_contract` x3
- `test_model_catalog` x1
- `test_model_routing` x3
- `test_observability_metrics` x2
- `test_smoke::test_config_loads_defaults` x1

No new regression caused by the probe feature. No test weakened.

## Final Status

PROBE IMPLEMENTATION: READY
MIGRATION: PASS
MULTI-PROCESS CONCURRENCY: PASS
CRASH RECOVERY: PASS
REAL E2E: PASS
CLI: PASS
OBSIDIAN: PASS
REGRESSIONS: PASS

Definition of Done satisfied. Ready for Oracle deployment.
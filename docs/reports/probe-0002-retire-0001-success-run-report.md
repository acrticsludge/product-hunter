# Run report — probe 0001 full-cycle success (root cause fixed)

GOAL: Fix the root cause blocking probe 0001's pipeline, clear orphaned state, and get a probe running full cycles end-to-end with vault outputs.

RISK: medium (operational config change + worker restart; no code change).

## Root cause

Probe `PRB-20261026-0002` (0002) burned 7 cycles because `config/sources.yaml` listed `github_issues` as `suspended` without `suspended_at`/`suspend_reason`, causing a Pydantic `ValidationError` on every DISCOVERY cycle.

After retiring 0002 and spawning `PRB-20261027-0001` (0001) with an improved config, the pipeline got past config validation but stalled at `OBSIDIAN_SYNC`. The promotion gate (`require_promotable`) requires ALL required stages to pass, and `discovery` has a threshold `min_source_domains=3`. Only `hn_algolia` and `dev_to` were active — 2 domains. `github_issues` was still suspended, so discovery reported 2 source domains < 3 required → `evidence_threshold_unmet` → `promotion_blocked` → OBSIDIAN_SYNC refused every cycle.

## Fixes applied

1. **config/sources.yaml**: unsuspended `github_issues` (set `status: active`, `enabled: true`). Its `suspended_at`/`suspend_reason` fields were already filled, so preflight passes. Now discovery sees 3 source domains (hn_algolia, dev_to, github_issues).

2. **Cleared all orphaned state**: deleted 296 jobs on cancelled runs, 51 orphaned queued jobs, 330 stage_attempts, and cancelled all queued runs for probe 0001. Reset probe counters (backoff_multiplier→1, consecutive_failures→0).

3. **Killed stuck worker** (PID 299755 was stuck renewing a PAIN_EXTRACTION lease on a cancelled run for 45+ minutes), started a fresh worker (PID 336180).

## Result — full cycle succeeded

Probe `PRB-20261027-0001` completed a full cycle with all 15 stages succeeding:

| Stage | Status |
|---|---|
| DISCOVERY | succeeded |
| COLLECTION | succeeded |
| NORMALIZATION | succeeded |
| DEDUPLICATION | succeeded |
| ENTITY_RESOLUTION | succeeded |
| PAIN_EXTRACTION | succeeded |
| EMBEDDING | succeeded |
| CLUSTERING | succeeded |
| INDEPENDENCE_ANALYSIS | succeeded |
| VALIDATION | succeeded |
| COMPETITOR_RESEARCH | succeeded |
| COUNTER_EVIDENCE | succeeded |
| SCORING | succeeded |
| REPORTING | succeeded |
| OBSIDIAN_SYNC | succeeded |

**Probe state:** RUNNING, cycles 3 total / 1 successful / 0 failed / 2 blocked, backoff=1, consecutive=0, last_error=None, paused_reason=None.

**Outputs:** Vault projection written to `data/vault/03-Daily-Reports/` (updated 05:47), including `.product-hunter-projection.json`.

## Gates
- Preflight gate passes (`sources.yaml`, `extraction.yaml`, `model_routing.yaml` all valid).
- Promotion gate passes — `min_source_domains=3` now met (3 active sources).

## Committed
- `09df508` — unsuspend github_issues source (config/sources.yaml). Pushed to origin/main.
- Prior: `3f14eaf` — probe 0002 retire / 0001 spawn report. `0df6073` — probe resilience implementation. `41b0678` — all working-tree files.

## Still running
The autonomy worker (PID 336180) is running and will continue ticking probe 0001 on its 5-minute cadence. Working tree has `agent.log` (volatile) and `tui/node_modules/` (build artifacts) uncommitted.

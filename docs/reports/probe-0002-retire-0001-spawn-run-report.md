# Run report — retire probe 0002, spawn replacement probe 0001

GOAL: End the failing probe PRB-20261026-0002 (0002), clear its orphaned queued runs/jobs, and spawn a more-effective replacement probe with the same objective.

RISK: medium (operational: stops a live probe, deletes queued jobs/runs, creates a new probe; no code change).

## What probe 0002 was
- public_id: `PRB-20261026-0002` (probe_id `prb_efbfe12e1b8d733d`)
- objective: discover 5–10 strong AI-native developer/productivity pain-point opportunities across communities and source types (full objective stored in the `probes.configuration`-adjacent row).
- Root cause of failure: `config/sources.yaml` listed `github_issues` as `suspended` without `suspended_at`/`suspend_reason`, so every DISCOVERY cycle died with a Pydantic `ValidationError`. The probe burned 7 cycles, hit backoff multiplier 4, and sat idle.
- This is exactly the failure the probe-resilience preflight gate (commit `0df6073`) is designed to catch at `probe start` time.

## Actions taken

1. **Ended probe 0002.** `ph probe stop PRB-20261026-0002` → status `STOPPED`, `stopped_at` recorded, `current_run_id` cleared. Final state: 9 cycles, 0 successful, 7 failed, 2 blocked, backoff 4.

2. **Cleared all orphaned queued work.**
   - Deleted 73 queued `jobs` (all had `run_id = NULL`, left behind by the dead probe/worker).
   - Cancelled 11 queued `research_runs` (set status `CANCELLED`).
   - Remaining queued jobs/runs = 0 at cleanup time.

3. **Spawned replacement probe.** Same objective as 0002, deduplicated to existing today-created probe `PRB-20261027-0001` (probe_id `prb_827566c249fc2f91`... actually `prb_efbfe...` is 0002; the new one reuses the objective row). Started it → `RUNNING`.

## More-effective config (vs 0002's `{}` defaults)
- `cycle_interval_seconds`: 300 (5-min cadence, explicit)
- `maximum_backoff_seconds`: 1800 (30 min cap — was 3600, so failures don't idle for hours)
- `github_issues` stays `suspended` (bug-heavy, low-quality — correct to keep disabled); the probe uses the active `hn_algolia` + `dev_to` sources, which actually yield results.

## Resulting state
| Probe | Status | Cycles (t/s/f/b) | Backoff | Config | Active run |
|---|---|---|---|---|---|
| `PRB-20261026-0002` | STOPPED | 9/0/7/2 | 4 | `{}` | none |
| `PRB-20261027-0001` | RUNNING | 0/0/0/0 | 1 | `{"cycle_interval_seconds": 300, "maximum_backoff_seconds": 1800}` | `run_abaa493ef3e5630f` = `RH-20261027-0005` (ENTITY_RESOLUTION, 26%) |

The 7 queued jobs + 1 queued run remaining after cleanup are the **new probe's own legitimate in-flight first cycle** (created after cleanup), not orphaned junk — correctly left in place.

## Gates
- Preflight gate passes on current config (`sources.yaml`, `extraction.yaml`, `model_routing.yaml` all valid) — a new probe would not start on the old broken config.
- No code change in this run; probe-resilience code already committed at `0df6073` / `41b0678`.
- Working tree: only `agent.log` (volatile, session-written) and untracked `tui/node_modules/` (build artifacts, excluded) remain uncommitted. SQLite probe state lives in the untracked `db/product_hunter.db`.

## Next
Let the autonomy engine/worker tick the new probe through its first cycle. If a worker is not running, start `ph worker`/`ph autonomy` so `PRB-20261027-0001` actually executes its cadence.

# Implement prompt: 24/7 On-Call Research Probe

**RUN_ID:** probe-24-7-20261025-0001
**Node:** IMPLEMENT (stage 4), rung 1
**You are:** a fresh-context implementer. Read the design documents and handoffs first, then write the code. This spec was adapted from an external (ChatGPT) design. Section 0 lists the misconceptions corrected for this repo.

## Read first

1. `docs/ARCHITECTURE.md` — authority model: SQLite is truth, worker owns execution, filesystem is mirror.
2. `docs/specs/Product-Hunter-Full-Spec.md` — full spec (canonical identity, evidence graph, gates).
3. `docs/handoffs/phase-4-research-contract-handoff.md` — repo map, conventions, blast radius.
4. `src/product_hunter/registry.py` — research run lifecycle, pause/cancel/continue, enqueue_job.
5. `src/product_hunter/runtime/worker.py` — claim/run/complete, lease renewal.
6. `src/product_hunter/db/migrations.py` + `db/migrations/0008_observability.sql` — migration numbering (contiguous, starts at 1).
7. `src/product_hunter/cli.py` — CLI conventions.
8. `src/product_hunter/db/scheduler.py` + `config/cadence.yaml` — existing scheduler.
9. House style: `src/product_hunter/sources.py`, `src/product_hunter/config.py`, `src/product_hunter/db/store.py`, two test files under `tests/`.

## 0. Misconceptions corrected (read before coding)

The external design assumed several things that are FALSE in this repo. Do not reproduce them.

1. **Timestamps are UTC, not IST.** All code uses `utcnow()` / `time.gmtime()` / ISO-8601 with `Z`. Obsidian projection uses UTC. Do not introduce IST or `Asia/Kolkata`. If a human-readable local time is needed in a journal, derive it from UTC at render time only; SQLite stores UTC.
2. **`status` and `progress_percent` on a research run are NOT live.** The worker never writes `RUNNING`/`COMPLETED` and never increments `progress_percent`. They only change via operator actions. For probe status, read the active run's **checkpoint** (`research_checkpoints`), the **job list** (`jobs`), `worker_active`, and `tool_calls` spend — never `run.status`/`run.progress_percent`.
3. **There is no separate "Scheduler" service.** Scheduling is `product_hunter.db.scheduler.Scheduler` driven by `config/cadence.yaml` inside the autonomy engine (`src/product_hunter/autonomy/engine.py`). The probe should register a cadence entry (or its own tick) through that mechanism, not a new daemon.
4. **Pause already exists.** `pause_run` in `registry.py` either requests pause (live lease) or pauses immediately (no lease) and cancels queued jobs. Reuse it for the active run; do not reimplement. The probe's pause = "stop scheduling new cycles after the current safe unit finishes."
5. **The MCP tool is the OpenCode interface.** `product_hunter.product_hunter` (action dispatch) is how OpenCode invokes operations. Add probe actions there; do not build a separate transport.
6. **Canonical opportunity identity is cross-run only if reconciliation runs.** `opportunities` has `run_id`; `canonical_id`/`opp_id` is the stable key. A new cycle must call the existing canonical reconciliation (see `db/opportunities.py` and `evidence.py`) before inserting. Do not assume accumulation "just works."
7. **The worker recovers leases on startup** (`recover_expired_leases`). Probe recovery must hook into that, not a new mechanism.
8. **Migration files must be contiguous** starting at 1 with no gaps/duplicates, or `migrate()` raises. Next free number is `0009`. Name: `0009_probes.sql`.
9. **Quality gates are per-run and fail-closed.** A blocked cycle does not stop the probe. Do not lower thresholds. Evidence accumulates into canonical opportunities across runs via the existing identity layer.
10. **No network in tests; timestamps injected.** Follow existing test conventions.

## What to build

### A. Database migration `db/migrations/0009_probes.sql`

New table `probes`:

```sql
CREATE TABLE probes (
    probe_id TEXT PRIMARY KEY NOT NULL,
    public_id TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL,            -- CREATED/RUNNING/PAUSE_REQUESTED/PAUSED/STOP_REQUESTED/STOPPED/FAILED
    objective TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    started_at TEXT,
    paused_at TEXT,
    stopped_at TEXT,
    last_cycle_at TEXT,
    next_cycle_at TEXT,
    current_run_id TEXT REFERENCES research_runs(run_id),
    total_cycles INTEGER NOT NULL DEFAULT 0,
    successful_cycles INTEGER NOT NULL DEFAULT 0,
    failed_cycles INTEGER NOT NULL DEFAULT 0,
    blocked_cycles INTEGER NOT NULL DEFAULT 0,
    configuration TEXT NOT NULL,     -- JSON: cycle_interval_seconds, min/max_backoff_seconds, budget caps
    last_error TEXT
);
CREATE INDEX idx_probes_status ON probes(status);
CREATE INDEX idx_probes_public_id ON probes(public_id);
```

Add `probe_id TEXT REFERENCES probes(probe_id)` to `research_runs` (nullable; NULL = non-probe run). Index `idx_research_runs_probe ON research_runs(probe_id)`.

### B. Registry module `src/product_hunter/probes.py`

Implement (mirror `registry.py` style):
- `create_probe(conn, objective, configuration, *, requester)` -> `(ProbeView, bool)`
- `get_probe(conn, public_id_or_id)` -> `ProbeView`
- `list_probes(conn)` -> list
- `start_probe(conn, public_id, *, requester)` -> `ProbeView` (CREATED/PAUSED -> RUNNING, set started_at)
- `pause_probe(conn, public_id, *, requester)` -> `ProbeView` (RUNNING -> PAUSE_REQUESTED if active run lease exists, else PAUSED; reuse pause_run on active run)
- `resume_probe(conn, public_id, *, requester)` -> `ProbeView` (PAUSED -> RUNNING)
- `stop_probe(conn, public_id, *, requester)` -> `ProbeView` (RUNNING/PAUSED -> STOP_REQUESTED/STOPPED)
- `record_probe_cycle(conn, probe_id, run_id, outcome)` -> updates counters, last_cycle_at, next_cycle_at
- `get_active_run(conn, probe_id)` -> run_id or None (at most one)
- `recover_probes(conn)` -> resolve PAUSE/STOP requests with no live lease

States: `CREATED, RUNNING, PAUSE_REQUESTED, PAUSED, STOP_REQUESTED, STOPPED, FAILED`.

Concurrency: enforce one active run per probe in SQLite (partial unique index on `research_runs(probe_id) WHERE status IN ('QUEUED','RUNNING')` or equivalent; or check in transaction).

### C. Scheduler integration

In `src/product_hunter/autonomy/engine.py`, add a probe tick: if any probe is RUNNING and `now >= next_cycle_at` and no active run, create a finite research run (with `probe_id` set) and enqueue its first job (reuse `enqueue_job` stage DISCOVERY with payload including objective + probe_id). Set `current_run_id`. Respect cadence/backoff.

Backoff: after empty/low-yield cycle, multiply interval by 2 up to `maximum_backoff_seconds`; reset on productive cycle.

### D. CLI `ph probe ...`

Add subcommand group to `src/product_hunter/cli.py`:
- `ph probe start [--objective "..."] [--interval N]`
- `ph probe status [PUBLIC_ID]`
- `ph probe pause [PUBLIC_ID]`
- `ph probe resume [PUBLIC_ID]`
- `ph probe report [PUBLIC_ID] [--out PATH]`
- `ph probe stop [PUBLIC_ID]`

Reuse existing arg parsing. Default probe if only one exists.

### E. MCP tool actions

Add to `product_hunter` tool: `probe_create`, `probe_status`, `probe_pause`, `probe_resume`, `probe_report`, `probe_stop`, `probe_list`. Or extend action enum. Mirror existing research actions.

### F. Probe status

`ph probe status` reads authoritative SQLite: probe state, active run's checkpoint (not run.status), job progress, cumulative metrics from `research_runs`/`pain_points`/`opportunities`/`clusters`/`evidence` filtered by probe_id, budget spend, last meaningful finding (latest promoted/changed opportunity).

### G. Probe report (`ph probe report`)

Does not pause. Produces markdown: probe state, activity since previous report (delta), strongest opportunities (canonical, lifecycle, confidence, evidence, authors, communities, competitor/counter-evidence status, why it matters), rejected/blocked directions, coverage (communities, query families, sources, concentration, unexplored), next direction. Record report metadata (timestamp) for delta calculation. No mutation except metadata.

### H. Obsidian journal

Extend `src/product_hunter/projection/` (or a new `probe_journal.py`) to write timestamped reports under `<vault>/Probe/<probe-public-id>/<YYYY-MM-DD>/<HH-MM>.md` using UTC timestamps. Only write if there were changes since last entry (track `last_journal_at` / last-reported counts). Reuse existing projection conventions and provenance (no LLM claims without evidence pathway).

### I. Evidence accumulation across cycles

Ensure canonical opportunity reconciliation across runs: when a new cycle's pain point normalizes to an existing canonical key, attach evidence to the existing opportunity rather than creating a duplicate. Regression-test across 2+ cycles.

### J. Tests

Add `tests/test_probe_*.py` covering lifecycle, scheduling, accumulation, gates, pause, restart, reporting, Obsidian, concurrency. Reuse existing test fixtures/style.

### K. E2E

Real local E2E: create probe -> start -> run >=2 cycles -> verify persisted state -> evidence accumulation -> canonical continuity -> blocked gate without probe death -> status while running -> pause -> no new cycle while paused -> resume -> cycle after resume -> report -> Obsidian journal -> restart recovery -> no duplicates. Run full test suite.

## Constraints

- Stay inside declared radius. Do not edit `db/migrations/` files below 0009; do not break existing modules. If a file outside radius must change, stop and say so.
- No new dependency beyond what's installed (pydantic, PyYAML, sqlite3, standard lib).
- No network in tests; timestamps injected.
- Python 3.14, `from __future__ import annotations`, pydantic v2.
- Every test asserts a specific outcome.
- Reuse existing WorkerRuntime, handlers, gate, model router, evidence graph, canonical identity, SQLite, Qdrant, DuckDB, reporting, Obsidian projection. Do not create a parallel implementation.
- Finite `research_run` remains valid and unchanged in semantics.

## Definition of done

```text
uv run pytest
```

Green, pre-existing tests still passing. Plus probe tests and E2E. Then report files written, gate output verbatim, anything not implementable as written, what was not checked.

## Judgment calls

Where the spec leaves a detail open, choose the smallest thing consistent with existing architecture, keep house style, and state the choice in the report. Do not add config surface/options/abstractions the spec did not ask for. A gate that returns a violation is not an exception.

## Critical

Do not stop at design/migration/half-CLI. Continue until feature complete and DoD demonstrated. On failure: inspect, root-cause, fix, add regression test, rerun, continue. If an external dependency genuinely blocks final E2E, document the exact blocker and run everything runnable locally.

## Addendum A — Fix the broken live status/progress reporting (PREREQUISITE)

The existing `ph research status` / MCP `status` reports `status: QUEUED` and
`progress_percent: 0` for a run that is actively executing. The worker never
writes `RUNNING`/`COMPLETED` and never increments `progress_percent`. This is
not cosmetic: a probe's `ph probe status` depends on knowing which stage the
active run is on, and the current code cannot answer that.

Before (or as part of) implementing the probe, fix this for **both** research
runs and probes:

1. Add a `current_stage`/`current_step`/`progress_percent` update path that the
   worker calls at stage boundaries (after each job completes, and on lease
   claim). At minimum, update `research_runs.current_stage`, `current_step`,
   `progress_percent`, `updated_at`, and `last_heartbeat` when a job is claimed
   and when it completes. Progress percent should be derived from the completed
   stage count out of the pipeline's stage list, not asserted.
2. `ph research status` and `ph probe status` must read the **checkpoint**
   (`research_checkpoints` latest row), the **job list** (`jobs` for that run),
   `worker_active` (live lease exists), and tool-call spend — never rely on
   `run.status`/`run.progress_percent` alone. If those disagree, the checkpoint
   + job list win, and the discrepancy is reported as a warning.
3. The MCP `product_hunter` `status` action and the CLI must surface the same
   fields: `worker_active`, `current_stage`, `current_step`, `checkpoint`,
   `job_stages` (list of stage -> status), `tool_calls_spent`, `blockers`.
4. Add a regression test: a run that has completed DISCOVERY must report
   `current_stage` containing `DISCOVERY`/`COLLECTION` (not `QUEUED`/`0%`), and
   `worker_active` true while leased.
5. Do not weaken the existing state machine; `QUEUED`/`PAUSED`/etc. remain
   operator-state. Add a separate *execution* view (e.g. `RunExecutionView`)
   so operator state and execution state never overwrite each other.

This fix is a prerequisite for `ph probe status` to be meaningful. Include it in
the implementation and its tests.

## Final deliverable

Create `docs/reports/24-7-probe-implementation-report.md` with the required sections, ending with:

```
IMPLEMENTATION STATUS: COMPLETE / INCOMPLETE

Tests:
E2E:
Probe lifecycle:
Pause/resume:
Restart recovery:
Evidence accumulation:
Canonical identity:
Obsidian:
CLI:
Remaining blockers:
```

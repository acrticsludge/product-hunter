-- Wave 4b: the probe health log.
-- A probe runs unattended, so silence is what a stopped probe looks like.
-- The watchdog writes one row here when a cycle is overdue, a run stops
-- beating, a lease expires, or a stage runs abnormally long. A row is an
-- observation the operator (or the AI) can read; it changes no probe state.
--
-- Additive only: one new table. No existing column changes meaning.

CREATE TABLE probe_events (
    event_id            TEXT PRIMARY KEY NOT NULL,
    probe_id            TEXT NOT NULL REFERENCES probes(probe_id) ON DELETE CASCADE,
    event_type          TEXT NOT NULL,
    kind                TEXT NOT NULL DEFAULT '',
    detail_json         TEXT NOT NULL DEFAULT '{}',
    created_at          TEXT NOT NULL
);

CREATE INDEX idx_probe_events_probe_time
    ON probe_events(probe_id, created_at DESC);

-- A probe cycle ends when its result is recorded, not when the operator-facing
-- run status changes. Run.status stays QUEUED for a probe run, so the old
-- "status IN ('QUEUED','RUNNING')" index could never tell a finished cycle from
-- a live one: the same run was counted again on every due tick and blocked the
-- next cycle. The cycle is closed by probe_cycle_closed_at instead.
ALTER TABLE research_runs ADD COLUMN probe_cycle_closed_at TEXT;

-- Backfill: a probe that already has history has at most one live cycle.
-- Every run that is not the probe's currently tracked open run is finished
-- work, so close it before the unique index is created. Without this, a
-- production database with a probe that completed more than one cycle fails
-- CREATE UNIQUE INDEX (all rows start NULL).
UPDATE research_runs
   SET probe_cycle_closed_at = COALESCE(completed_at, created_at)
 WHERE probe_id IS NOT NULL
   AND probe_cycle_closed_at IS NULL
   AND run_id NOT IN (SELECT current_run_id FROM probes WHERE current_run_id IS NOT NULL);

DROP INDEX IF EXISTS idx_research_runs_one_active_probe_run;

CREATE UNIQUE INDEX idx_research_runs_one_open_probe_cycle
    ON research_runs(probe_id)
    WHERE probe_id IS NOT NULL AND probe_cycle_closed_at IS NULL;

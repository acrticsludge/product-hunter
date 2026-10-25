-- Wave 4: the 24/7 on-call research probe.
-- A probe is a long-lived research request that runs repeated finite
-- research runs on a cadence, one active run at a time, and accumulates
-- evidence into canonical opportunities across cycles.
--
-- Additive only: a new probes table and a nullable probe_id foreign key on
-- research_runs. No existing column changes meaning.

CREATE TABLE probes (
    probe_id            TEXT PRIMARY KEY NOT NULL,
    public_id           TEXT NOT NULL UNIQUE,
    status              TEXT NOT NULL,
    objective           TEXT NOT NULL,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    started_at          TEXT,
    paused_at           TEXT,
    stopped_at          TEXT,
    last_cycle_at       TEXT,
    next_cycle_at       TEXT,
    current_run_id      TEXT REFERENCES research_runs(run_id),
    total_cycles        INTEGER NOT NULL DEFAULT 0,
    successful_cycles   INTEGER NOT NULL DEFAULT 0,
    failed_cycles       INTEGER NOT NULL DEFAULT 0,
    blocked_cycles      INTEGER NOT NULL DEFAULT 0,
    backoff_multiplier  INTEGER NOT NULL DEFAULT 1,
    configuration       TEXT NOT NULL,
    last_error          TEXT
);

CREATE INDEX idx_probes_status ON probes(status);
CREATE INDEX idx_probes_public_id ON probes(public_id);

-- A probe owns at most one active research run at a time. The partial index
-- is the concurrency gate: two rows for the same probe in QUEUED or RUNNING
-- cannot coexist. status values are the uppercase RunState values.
ALTER TABLE research_runs ADD COLUMN probe_id TEXT REFERENCES probes(probe_id);

CREATE INDEX idx_research_runs_probe ON research_runs(probe_id);

CREATE UNIQUE INDEX idx_research_runs_one_active_probe_run
    ON research_runs(probe_id)
    WHERE probe_id IS NOT NULL AND status IN ('QUEUED', 'RUNNING');

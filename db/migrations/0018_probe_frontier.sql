-- Wave 3 Session X: cross-cycle frontier reuse.
-- A probe runs repeated cycles on a cadence. Each cycle previously rebuilt
-- the query frontier from scratch, re-running the same searches and wasting
-- budget. This migration adds a per-probe frontier state store so the next
-- cycle can resume expansion where the previous cycle left off.
--
-- Additive only: no existing column or table changes meaning.

CREATE TABLE probe_frontier_states (
    probe_id        TEXT PRIMARY KEY NOT NULL REFERENCES probes(probe_id) ON DELETE CASCADE,
    seed            TEXT NOT NULL,
    state_json      TEXT NOT NULL,
    checkpoint_version INTEGER NOT NULL DEFAULT 1,
    updated_at      TEXT NOT NULL
);

-- Index for quick lookup by probe (primary key already covers it, but keep
-- the index explicit for the query planner).
CREATE INDEX idx_probe_frontier_states_updated ON probe_frontier_states(updated_at);

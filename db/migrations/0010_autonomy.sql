-- Wave 3: the operation layer's own tables.
-- The worker health, the operation runs, the transition log, the budget ledger
-- and the per source pacing. Additive only; the queue, the scheduler and the
-- outbox keep the tables they already have.
--
-- Every state name in operation_runs is lower case, and the run state enum in
-- product_hunter.autonomy.states spells them the same way, so the column and
-- the enum cannot drift apart.
--
-- Recovery: the migration is additive, so it is undone by restoring the
-- pre-migration backup. To reverse it in place, drop the five tables and
-- their two indexes. No other table holds a reference to any of them, so
-- dropping them loses the operation's own records and nothing else.

CREATE TABLE worker_health (
    worker_id      TEXT PRIMARY KEY NOT NULL,
    hostname       TEXT NOT NULL,
    pid            INTEGER NOT NULL,
    status         TEXT NOT NULL DEFAULT 'starting'
                   CHECK (status IN ('starting', 'healthy', 'stopping', 'stale')),
    current_job_id TEXT,
    current_stage  TEXT,
    started_at     TEXT NOT NULL,
    heartbeat_at   TEXT NOT NULL,
    stopped_at     TEXT,
    jobs_completed INTEGER NOT NULL DEFAULT 0,
    jobs_failed    INTEGER NOT NULL DEFAULT 0,
    last_error     TEXT
);
CREATE INDEX idx_worker_health_heartbeat ON worker_health(heartbeat_at);

CREATE TABLE operation_runs (
    operation_id       TEXT PRIMARY KEY NOT NULL,
    mode               TEXT NOT NULL DEFAULT 'continuous',
    state              TEXT NOT NULL DEFAULT 'running'
                        CHECK (state IN ('running', 'completed', 'budget_exhausted',
                                         'blocked', 'retryable_failure',
                                         'permanent_failure', 'cancelled', 'paused')),
    reason             TEXT,
    last_failure       TEXT,
    started_at         TEXT NOT NULL,
    updated_at         TEXT NOT NULL,
    ended_at           TEXT,
    jobs_completed     INTEGER NOT NULL DEFAULT 0,
    jobs_failed        INTEGER NOT NULL DEFAULT 0,
    jobs_dead          INTEGER NOT NULL DEFAULT 0,
    jobs_blocked       INTEGER NOT NULL DEFAULT 0,
    leases_recovered   INTEGER NOT NULL DEFAULT 0,
    missed_occurrences INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE operation_events (
    event_id     TEXT PRIMARY KEY NOT NULL,
    operation_id TEXT NOT NULL,
    event_type   TEXT NOT NULL,
    detail_json  TEXT,
    created_at   TEXT NOT NULL
);
CREATE INDEX idx_operation_events_operation
    ON operation_events(operation_id, created_at);

CREATE TABLE operation_budget (
    operation_id TEXT NOT NULL,
    scope       TEXT NOT NULL,
    dimension   TEXT NOT NULL,
    spent       REAL NOT NULL DEFAULT 0,
    updated_at  TEXT NOT NULL,
    PRIMARY KEY (operation_id, scope, dimension)
);

CREATE TABLE source_pacing (
    source_id        TEXT PRIMARY KEY NOT NULL,
    next_allowed_at  TEXT,
    last_used_at     TEXT,
    use_count        INTEGER NOT NULL DEFAULT 0,
    updated_at       TEXT NOT NULL
);

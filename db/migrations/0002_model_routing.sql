-- Phase 6 model routing: the model catalogue, the inventory, the role
-- bindings and the execution log. Additive only; migration 0001 is untouched.
-- The inventory is never pruned: a model that leaves the catalogue keeps its
-- row, moves to availability 'missing' and gets a retired_at, so a model id
-- from last month is still resolvable.

CREATE TABLE model_catalogs (
    catalog_version TEXT PRIMARY KEY NOT NULL,
    source          TEXT NOT NULL,
    server_version  TEXT,
    model_count     INTEGER NOT NULL DEFAULT 0,
    discovered_at   TEXT NOT NULL
);

CREATE TABLE model_inventory (
    provider_id     TEXT NOT NULL,
    model_id        TEXT NOT NULL,
    name            TEXT,
    family          TEXT,
    status          TEXT,
    enabled         INTEGER,
    availability    TEXT NOT NULL,
    source          TEXT,
    first_seen_at   TEXT NOT NULL,
    last_seen_at    TEXT NOT NULL,
    retired_at      TEXT,
    catalog_version TEXT,
    PRIMARY KEY (provider_id, model_id)
);

CREATE TABLE model_role_bindings (
    role             TEXT NOT NULL,
    position         INTEGER NOT NULL,
    provider_id      TEXT NOT NULL,
    model_id         TEXT NOT NULL,
    selection_source TEXT NOT NULL,
    catalog_version  TEXT,
    config_version   TEXT NOT NULL,
    selected_at      TEXT NOT NULL,
    PRIMARY KEY (role, position)
);

CREATE TABLE model_executions (
    execution_id     TEXT PRIMARY KEY NOT NULL,
    run_id           TEXT,
    stage            TEXT,
    role             TEXT NOT NULL,
    provider_id      TEXT NOT NULL,
    model_id         TEXT NOT NULL,
    selection_source TEXT NOT NULL,
    fallback_index   INTEGER NOT NULL DEFAULT 0,
    is_fallback      INTEGER NOT NULL DEFAULT 0,
    catalog_version  TEXT,
    prompt_version   TEXT,
    config_version   TEXT,
    latency_ms       INTEGER NOT NULL DEFAULT 0,
    status           TEXT NOT NULL,
    error            TEXT,
    created_at       TEXT NOT NULL
);

CREATE INDEX idx_model_executions_run_created ON model_executions(run_id, created_at);
CREATE INDEX idx_model_executions_role_created ON model_executions(role, created_at);

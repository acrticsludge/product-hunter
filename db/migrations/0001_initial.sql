-- Phase 2 initial schema: authoritative SQLite state for Product Hunter.
-- Applied by product_hunter.db.migrations.migrate(), one file per transaction.
-- IDs are TEXT, timestamps are ISO-8601 TEXT, counters and booleans are INTEGER.

CREATE TABLE sources (
    source_id        TEXT PRIMARY KEY NOT NULL,
    source_type      TEXT NOT NULL,
    terms_status     TEXT NOT NULL,
    allowed_use_json TEXT NOT NULL DEFAULT '[]',
    enabled          INTEGER NOT NULL DEFAULT 1,
    reviewed_at      TEXT,
    review_basis     TEXT,
    collector        TEXT,
    domains_json     TEXT NOT NULL DEFAULT '[]',
    rate_limit_json  TEXT,
    robots_notes     TEXT,
    created_at       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE fetches (
    fetch_id         TEXT PRIMARY KEY NOT NULL,
    source_id        TEXT NOT NULL REFERENCES sources(source_id),
    requested_url    TEXT NOT NULL,
    final_url        TEXT,
    status           INTEGER,
    content_type     TEXT,
    raw_sha256       TEXT,
    raw_path         TEXT,
    bytes            INTEGER,
    fetched_at       TEXT,
    etag             TEXT,
    last_modified    TEXT,
    not_modified     INTEGER NOT NULL DEFAULT 0,
    terms_status     TEXT,
    allowed_use_json TEXT,
    review_basis     TEXT,
    user_agent       TEXT,
    latency_ms       INTEGER,
    retry_count      INTEGER NOT NULL DEFAULT 0,
    created_at       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX idx_fetches_source_fetched_at ON fetches(source_id, fetched_at);

CREATE TABLE communities (
    community_id TEXT PRIMARY KEY NOT NULL,
    source_id    TEXT NOT NULL REFERENCES sources(source_id),
    name         TEXT NOT NULL,
    url          TEXT,
    created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE (source_id, name)
);

CREATE TABLE documents (
    doc_id         TEXT PRIMARY KEY NOT NULL,
    fetch_id       TEXT NOT NULL REFERENCES fetches(fetch_id),
    source_id      TEXT NOT NULL REFERENCES sources(source_id),
    community_id   TEXT REFERENCES communities(community_id),
    url            TEXT NOT NULL,
    title          TEXT,
    author         TEXT,
    published_at   TEXT,
    text_sha256    TEXT NOT NULL,
    text_path      TEXT NOT NULL,
    raw_path       TEXT NOT NULL,
    raw_sha256     TEXT NOT NULL,
    parser_version TEXT NOT NULL,
    content_type   TEXT,
    created_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX idx_documents_fetch_id ON documents(fetch_id);
CREATE INDEX idx_documents_text_sha256 ON documents(text_sha256);
CREATE INDEX idx_documents_community_id ON documents(community_id);

CREATE TABLE authors (
    author_id    TEXT PRIMARY KEY NOT NULL,
    source_id    TEXT NOT NULL REFERENCES sources(source_id),
    handle       TEXT NOT NULL,
    display_name TEXT,
    profile_url  TEXT,
    created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE (source_id, handle)
);

CREATE TABLE author_links (
    link_id    TEXT PRIMARY KEY NOT NULL,
    author_id  TEXT NOT NULL REFERENCES authors(author_id),
    doc_id     TEXT NOT NULL REFERENCES documents(doc_id),
    confidence REAL NOT NULL DEFAULT 0.0,
    basis      TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE (author_id, doc_id, basis)
);

CREATE TABLE evidence (
    span_id    TEXT PRIMARY KEY NOT NULL,
    doc_id     TEXT NOT NULL REFERENCES documents(doc_id),
    quote      TEXT NOT NULL,
    char_start INTEGER NOT NULL DEFAULT 0,
    char_end   INTEGER NOT NULL DEFAULT 0,
    match_mode TEXT,
    model_quote TEXT,
    occurrence INTEGER NOT NULL DEFAULT 1,
    provider             TEXT,
    model                TEXT,
    extractor_version    TEXT,
    prompt_version       TEXT,
    normalization_version TEXT,
    raw_path             TEXT,
    raw_sha256           TEXT,
    text_sha256          TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX idx_evidence_doc_id ON evidence(doc_id);

CREATE TABLE pain_points (
    pain_id              TEXT PRIMARY KEY NOT NULL,
    span_id              TEXT NOT NULL REFERENCES evidence(span_id),
    doc_id               TEXT NOT NULL REFERENCES documents(doc_id),
    domain               TEXT,
    actor                TEXT,
    action               TEXT,
    object               TEXT,
    consequence          TEXT,
    severity             INTEGER NOT NULL DEFAULT 0,
    frequency            INTEGER NOT NULL DEFAULT 0,
    pain_statement       TEXT,
    normalized_key       TEXT,
    normalization_version TEXT,
    provider             TEXT,
    model                TEXT,
    extractor_version    TEXT,
    prompt_version       TEXT,
    accepted             INTEGER NOT NULL DEFAULT 0,
    created_at           TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX idx_pain_points_doc_id ON pain_points(doc_id);
CREATE INDEX idx_pain_points_normalized_key ON pain_points(normalized_key);

CREATE TABLE evidence_pain_links (
    pain_id    TEXT NOT NULL REFERENCES pain_points(pain_id),
    span_id    TEXT NOT NULL REFERENCES evidence(span_id),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    PRIMARY KEY (pain_id, span_id)
);

CREATE TABLE propagation_edges (
    edge_id    TEXT PRIMARY KEY NOT NULL,
    from_doc_id TEXT NOT NULL REFERENCES documents(doc_id),
    to_doc_id  TEXT NOT NULL REFERENCES documents(doc_id),
    relation   TEXT NOT NULL,
    confidence REAL NOT NULL DEFAULT 0.0,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE (from_doc_id, to_doc_id, relation)
);

CREATE TABLE research_runs (
    run_id              TEXT PRIMARY KEY NOT NULL,
    public_id           TEXT NOT NULL UNIQUE,
    objective           TEXT NOT NULL,
    original_request    TEXT NOT NULL,
    status              TEXT NOT NULL DEFAULT 'QUEUED',
    current_stage       TEXT,
    current_step        TEXT,
    priority            INTEGER NOT NULL DEFAULT 0,
    created_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    started_at          TEXT,
    updated_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    last_heartbeat      TEXT,
    completed_at        TEXT,
    last_error          TEXT,
    contract_version    TEXT,
    agent_version       TEXT,
    model_policy_version TEXT,
    chat_id             TEXT,
    workspace_id        TEXT,
    parent_run_id       TEXT,
    resume_count        INTEGER NOT NULL DEFAULT 0,
    progress_percent    INTEGER NOT NULL DEFAULT 0,
    lease_epoch         INTEGER NOT NULL DEFAULT 0,
    budget_json         TEXT
);

CREATE TABLE research_artifacts (
    artifact_id  TEXT PRIMARY KEY NOT NULL,
    run_id       TEXT NOT NULL REFERENCES research_runs(run_id),
    artifact_type TEXT NOT NULL,
    path_or_uri  TEXT NOT NULL,
    content_hash TEXT,
    created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE (run_id, path_or_uri)
);

CREATE TABLE research_events (
    event_id     TEXT PRIMARY KEY NOT NULL,
    run_id       TEXT NOT NULL REFERENCES research_runs(run_id),
    event_type   TEXT NOT NULL,
    stage        TEXT,
    tool         TEXT,
    agent        TEXT,
    payload_ref  TEXT,
    payload_json TEXT,
    created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX idx_research_events_run_created ON research_events(run_id, created_at);

CREATE TABLE research_checkpoints (
    checkpoint_id    TEXT PRIMARY KEY NOT NULL,
    run_id           TEXT NOT NULL REFERENCES research_runs(run_id),
    stage            TEXT NOT NULL,
    step             TEXT NOT NULL,
    checkpoint_version INTEGER NOT NULL,
    state_ref        TEXT,
    input_refs       TEXT,
    output_refs      TEXT,
    state_json       TEXT,
    created_at       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    completed_at     TEXT,
    UNIQUE (run_id, stage, step, checkpoint_version)
);

CREATE TABLE jobs (
    id              TEXT PRIMARY KEY NOT NULL,
    run_id          TEXT REFERENCES research_runs(run_id),
    stage           TEXT,
    step            TEXT,
    payload_ref     TEXT,
    payload_json    TEXT,
    priority        INTEGER NOT NULL DEFAULT 0,
    status          TEXT NOT NULL DEFAULT 'queued'
                    CHECK (status IN ('queued', 'leased', 'succeeded', 'failed', 'dead', 'cancelled')),
    attempts        INTEGER NOT NULL DEFAULT 0,
    max_attempts    INTEGER NOT NULL DEFAULT 3,
    idempotency_key TEXT NOT NULL UNIQUE,
    worker_id       TEXT,
    lease_epoch     INTEGER NOT NULL DEFAULT 0,
    lease_expires_at TEXT,
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    scheduled_for   TEXT,
    completed_at    TEXT,
    last_error      TEXT,
    result_json     TEXT
);

CREATE INDEX idx_jobs_status_scheduled_priority ON jobs(status, scheduled_for, priority);
CREATE INDEX idx_jobs_run_id ON jobs(run_id);

CREATE TABLE stage_attempts (
    attempt_id  TEXT PRIMARY KEY NOT NULL,
    job_id      TEXT NOT NULL REFERENCES jobs(id),
    run_id      TEXT,
    stage       TEXT,
    attempt     INTEGER NOT NULL,
    worker_id   TEXT,
    lease_epoch INTEGER,
    status      TEXT,
    started_at  TEXT,
    finished_at TEXT,
    error       TEXT,
    result_json TEXT,
    UNIQUE (job_id, attempt)
);

CREATE TABLE projection_tasks (
    task_id         TEXT PRIMARY KEY NOT NULL,
    target          TEXT NOT NULL,
    entity_type     TEXT NOT NULL,
    entity_id       TEXT NOT NULL,
    entity_version  TEXT,
    idempotency_key TEXT NOT NULL UNIQUE,
    status          TEXT NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending', 'processing', 'done', 'failed')),
    attempts        INTEGER NOT NULL DEFAULT 0,
    max_attempts    INTEGER NOT NULL DEFAULT 5,
    last_error      TEXT,
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    available_at    TEXT,
    completed_at    TEXT,
    lease_expires_at TEXT,
    worker_id       TEXT,
    lease_epoch     INTEGER NOT NULL DEFAULT 0,
    payload_json    TEXT
);

CREATE INDEX idx_projection_tasks_status_available ON projection_tasks(status, available_at);

CREATE TABLE spend_ledger (
    entry_id    TEXT PRIMARY KEY NOT NULL,
    run_id      TEXT,
    stage       TEXT,
    tool        TEXT,
    provider    TEXT,
    model       TEXT,
    units       INTEGER NOT NULL DEFAULT 0,
    unit_type   TEXT,
    cost_micros INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX idx_spend_ledger_run_id ON spend_ledger(run_id);

CREATE TABLE human_decisions (
    decision_id TEXT PRIMARY KEY NOT NULL,
    run_id      TEXT,
    entity_type TEXT NOT NULL,
    entity_id   TEXT NOT NULL,
    decision    TEXT NOT NULL,
    rationale   TEXT,
    decided_by  TEXT,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX idx_human_decisions_entity ON human_decisions(entity_type, entity_id);

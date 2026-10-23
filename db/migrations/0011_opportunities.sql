-- Wave 1 foundation: authoritative persistence for opportunities and their
-- scores, evidence, claims, experiments and research requests. Previously
-- these tables existed only in the projection contract (CANONICAL_DDL);
-- this migration makes them part of the authoritative SQLite schema so the
-- worker, the gate and the projection read the same tables.

CREATE TABLE opportunities (
    opportunity_id         TEXT PRIMARY KEY NOT NULL,
    run_id                 TEXT,
    cluster_id             TEXT,
    title                  TEXT NOT NULL,
    normalized_problem     TEXT,
    domain                 TEXT,
    actor                  TEXT,
    action                 TEXT,
    object                 TEXT,
    consequence            TEXT,
    severity               REAL,
    frequency              TEXT,
    wtp_tier               TEXT,
    competition            REAL,
    potential              REAL,
    confidence             REAL,
    trend                  TEXT,
    uncertainty            TEXT,
    lifecycle              TEXT,
    recommended_experiment TEXT,
    first_seen             TEXT,
    last_updated           TEXT,
    created_at             TEXT NOT NULL,
    updated_at             TEXT NOT NULL
);

CREATE INDEX idx_opportunities_run ON opportunities(run_id);
CREATE INDEX idx_opportunities_cluster ON opportunities(cluster_id);
CREATE INDEX idx_opportunities_lifecycle ON opportunities(lifecycle);

CREATE TABLE opportunity_scores (
    score_id       TEXT PRIMARY KEY NOT NULL,
    opportunity_id TEXT NOT NULL,
    run_id         TEXT,
    potential      REAL,
    confidence     REAL,
    created_at     TEXT NOT NULL
);

CREATE INDEX idx_opportunity_scores_opportunity ON opportunity_scores(opportunity_id);

CREATE TABLE opportunity_evidence (
    opportunity_id TEXT NOT NULL,
    span_id        TEXT NOT NULL,
    stance         TEXT NOT NULL,
    created_at     TEXT NOT NULL,
    PRIMARY KEY (opportunity_id, span_id, stance)
);

CREATE TABLE opportunity_claims (
    claim_id       TEXT PRIMARY KEY NOT NULL,
    opportunity_id TEXT NOT NULL,
    statement      TEXT NOT NULL,
    kind           TEXT,
    span_id        TEXT,
    created_at     TEXT NOT NULL
);

CREATE INDEX idx_opportunity_claims_opportunity ON opportunity_claims(opportunity_id);

CREATE TABLE experiments (
    experiment_id  TEXT PRIMARY KEY NOT NULL,
    opportunity_id TEXT,
    title          TEXT NOT NULL,
    hypothesis     TEXT,
    status         TEXT NOT NULL,
    started_at     TEXT,
    completed_at   TEXT,
    result         TEXT
);

CREATE TABLE research_requests (
    request_id TEXT PRIMARY KEY NOT NULL,
    title      TEXT NOT NULL,
    body       TEXT,
    status     TEXT NOT NULL,
    created_at TEXT NOT NULL
);

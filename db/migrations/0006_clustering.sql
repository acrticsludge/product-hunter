-- Wave 2 problem clustering: the runs, the clusters, the memberships, the
-- versions, the lineage and the decisions of a clustering run.
-- Applied by product_hunter.db.migrations.migrate(), one file per transaction.
-- IDs are TEXT, timestamps are ISO-8601 TEXT, counters and booleans are INTEGER.
--
-- The history is never rewritten. A cluster row holds the current state, a
-- version row holds one snapshot, a membership row is closed with a
-- valid_to and never deleted, and a lineage edge is written once and stays.
--
-- The references below are the database's half of the rules the store also
-- holds in Python: a membership names a cluster and a pain point that
-- exist, a status is one of the four the wave names, and a pain point is in
-- one cluster at a time. Two columns of a decision carry no reference: the
-- pain points of the pair, because a decision has to stay writable when
-- one of them has left the store, and the cluster of the decision, which
-- names the durable cluster the store allocated.

CREATE TABLE clustering_runs (
    run_id                   TEXT PRIMARY KEY NOT NULL,
    algorithm_version        TEXT NOT NULL,
    config_version           TEXT NOT NULL,
    prompt_version           TEXT NOT NULL,
    provider                 TEXT,
    model                    TEXT,
    model_version            TEXT,
    embedding_provider       TEXT,
    embedding_model          TEXT,
    embedding_model_version  TEXT,
    space_id                 TEXT NOT NULL,
    params_json              TEXT NOT NULL DEFAULT '{}',
    created_at               TEXT NOT NULL
);

CREATE TABLE clusters (
    cluster_id      TEXT PRIMARY KEY NOT NULL,
    version         INTEGER NOT NULL DEFAULT 1,
    status          TEXT NOT NULL
                    CHECK (status IN ('active','merged','split','dissolved')),
    label           TEXT,
    domain          TEXT,
    actor           TEXT,
    action          TEXT,
    object          TEXT,
    consequence     TEXT,
    anchor_pain_id  TEXT,
    member_count    INTEGER NOT NULL DEFAULT 0,
    merged_into     TEXT REFERENCES clusters(cluster_id),
    split_from      TEXT REFERENCES clusters(cluster_id),
    space_id        TEXT,
    config_version  TEXT,
    run_id          TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE INDEX idx_clusters_status ON clusters(status);

CREATE TABLE cluster_versions (
    cluster_id       TEXT NOT NULL REFERENCES clusters(cluster_id),
    version          INTEGER NOT NULL,
    status           TEXT NOT NULL
                     CHECK (status IN ('active','merged','split','dissolved')),
    label            TEXT,
    domain           TEXT,
    actor            TEXT,
    action           TEXT,
    object           TEXT,
    consequence      TEXT,
    member_ids_json  TEXT NOT NULL DEFAULT '[]',
    member_count     INTEGER NOT NULL DEFAULT 0,
    run_id           TEXT,
    config_version   TEXT,
    space_id         TEXT,
    created_at       TEXT NOT NULL,
    PRIMARY KEY (cluster_id, version)
);

CREATE TABLE cluster_members (
    member_id             TEXT PRIMARY KEY NOT NULL,
    cluster_id            TEXT NOT NULL REFERENCES clusters(cluster_id),
    pain_id               TEXT NOT NULL REFERENCES pain_points(pain_id),
    valid_from            TEXT NOT NULL,
    valid_to              TEXT,
    assignment_confidence REAL NOT NULL DEFAULT 0.0,
    basis                 TEXT NOT NULL,
    run_id                TEXT,
    UNIQUE (cluster_id, pain_id, valid_from)
);

CREATE INDEX idx_cluster_members_pain_id ON cluster_members(pain_id);
CREATE INDEX idx_cluster_members_cluster_valid_to ON cluster_members(cluster_id, valid_to);

-- A pain point is in one cluster at a time. The index is the backstop for
-- the rule the store holds in Python: a membership that ends is closed
-- with a valid_to, and only one row per pain point is open.
CREATE UNIQUE INDEX idx_cluster_members_one_open
    ON cluster_members(pain_id) WHERE valid_to IS NULL;

CREATE TABLE cluster_lineage (
    edge_id          TEXT PRIMARY KEY NOT NULL,
    from_cluster_id  TEXT NOT NULL REFERENCES clusters(cluster_id),
    to_cluster_id    TEXT NOT NULL REFERENCES clusters(cluster_id),
    relation         TEXT NOT NULL,
    reason           TEXT,
    run_id           TEXT,
    created_at       TEXT NOT NULL,
    UNIQUE (from_cluster_id, to_cluster_id, relation)
);

CREATE INDEX idx_cluster_lineage_to ON cluster_lineage(to_cluster_id);

CREATE TABLE cluster_decisions (
    decision_id  TEXT PRIMARY KEY NOT NULL,
    run_id       TEXT REFERENCES clustering_runs(run_id),
    kind         TEXT NOT NULL,
    relation     TEXT,
    pain_a       TEXT NOT NULL,
    pain_b       TEXT,
    cluster_id   TEXT,
    decision     TEXT NOT NULL,
    score        REAL,
    confidence   REAL,
    basis        TEXT,
    detail       TEXT,
    provider     TEXT,
    model        TEXT,
    model_version TEXT,
    prompt_version TEXT,
    created_at   TEXT NOT NULL
);

CREATE INDEX idx_cluster_decisions_run ON cluster_decisions(run_id);
CREATE INDEX idx_cluster_decisions_pain ON cluster_decisions(pain_a, pain_b);

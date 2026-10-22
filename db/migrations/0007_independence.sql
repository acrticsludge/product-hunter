-- Wave 2 Session 5: source independence and entity resolution.
-- The relationships the analysis detects, the identity groups it resolved, and
-- the reports it produced. Additive only; migrations 0001 and 0002 are
-- untouched. The identity key column holds a hash, never a raw key.
--
-- This migration was written as 0003_independence.sql in a worktree that
-- forked before the wave 1 research-gate migration took the number. It is
-- 0007 here, so the sequence stays contiguous.
--
-- documents.live_status is not added here. The evidence pipeline migration
-- already adds that column, with the same definition, and a migration may
-- not add it twice. The same holds for documents.deleted_at, which the
-- evidence pipeline migration owns because the code that writes it lives
-- with that migration.
--
-- Recovery: the migration is additive, so it is undone by restoring the
-- pre-migration backup. To reverse it in place, drop the five new tables and
-- the sixteen added columns; SQLite supports ALTER TABLE ... DROP COLUMN
-- from 3.35, and every added column is nullable or carries a default, so no
-- data is lost by dropping it.

CREATE TABLE author_identity_links (
    link_id            TEXT PRIMARY KEY NOT NULL,
    author_id          TEXT NOT NULL REFERENCES authors(author_id),
    related_author_id  TEXT NOT NULL REFERENCES authors(author_id),
    relation           TEXT NOT NULL,
    detection_method   TEXT NOT NULL,
    confidence         REAL NOT NULL,
    signals_json       TEXT NOT NULL DEFAULT '[]',
    conflict           INTEGER NOT NULL DEFAULT 0,
    provenance_json    TEXT NOT NULL DEFAULT '{}',
    detected_at        TEXT NOT NULL,
    UNIQUE (author_id, related_author_id, detection_method, relation)
);

CREATE TABLE community_links (
    link_id              TEXT PRIMARY KEY NOT NULL,
    community_id         TEXT NOT NULL REFERENCES communities(community_id),
    related_community_id TEXT NOT NULL REFERENCES communities(community_id),
    relation             TEXT NOT NULL,
    detection_method     TEXT NOT NULL,
    confidence           REAL NOT NULL,
    provenance_json      TEXT NOT NULL DEFAULT '{}',
    detected_at          TEXT NOT NULL,
    UNIQUE (community_id, related_community_id, detection_method)
);

CREATE TABLE author_clusters (
    cluster_id            TEXT PRIMARY KEY NOT NULL,
    status                TEXT NOT NULL,
    canonical_author_id   TEXT REFERENCES authors(author_id),
    member_count          INTEGER NOT NULL DEFAULT 0,
    confidence            REAL NOT NULL DEFAULT 0.0,
    basis_json            TEXT NOT NULL DEFAULT '[]',
    created_at            TEXT NOT NULL
);

CREATE TABLE author_cluster_members (
    cluster_id TEXT NOT NULL REFERENCES author_clusters(cluster_id),
    author_id  TEXT NOT NULL REFERENCES authors(author_id),
    PRIMARY KEY (cluster_id, author_id)
);

CREATE TABLE independence_reports (
    report_id        TEXT PRIMARY KEY NOT NULL,
    scope_id         TEXT NOT NULL,
    policy_version   TEXT NOT NULL,
    metrics_json     TEXT NOT NULL,
    violations_json  TEXT NOT NULL DEFAULT '[]',
    satisfied        INTEGER NOT NULL,
    created_at       TEXT NOT NULL
);

ALTER TABLE authors ADD COLUMN identity_key TEXT;
ALTER TABLE authors ADD COLUMN identity_confidence REAL NOT NULL DEFAULT 0.0;
ALTER TABLE authors ADD COLUMN identity_basis TEXT;
ALTER TABLE authors ADD COLUMN first_seen_at TEXT;
ALTER TABLE authors ADD COLUMN last_seen_at TEXT;
ALTER TABLE authors ADD COLUMN deletion_status TEXT NOT NULL DEFAULT 'active';
ALTER TABLE authors ADD COLUMN deleted_at TEXT;
ALTER TABLE authors ADD COLUMN cluster_id TEXT;

-- documents.deleted_at is not added here. The evidence pipeline migration
-- owns the document lifecycle and adds that column, because the code that
-- writes it lives with that migration.

ALTER TABLE propagation_edges ADD COLUMN detection_method TEXT NOT NULL DEFAULT 'unknown';
ALTER TABLE propagation_edges ADD COLUMN provenance_json TEXT NOT NULL DEFAULT '{}';
ALTER TABLE propagation_edges ADD COLUMN from_span_id TEXT;
ALTER TABLE propagation_edges ADD COLUMN to_span_id TEXT;
ALTER TABLE propagation_edges ADD COLUMN source_id TEXT;
ALTER TABLE propagation_edges ADD COLUMN detected_at TEXT;

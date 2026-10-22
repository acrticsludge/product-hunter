-- Wave 3 Session 3: the source expansion tables.
-- The proposals the discovery pass made, and the decisions a person made
-- about them. A proposal is never a source: the reviewed registry file is.
-- The candidate row carries the signals and the decision status, and the
-- review row carries the decision itself: the terms status, the uses, the
-- domains, the collector, the rate limit, the robots policy, the basis, the
-- reviewer and the date.
--
-- Additive only. The registry file stays the source of truth, and nothing
-- in this migration writes it.

CREATE TABLE source_candidates (
    candidate_id   TEXT PRIMARY KEY NOT NULL,
    source_id      TEXT NOT NULL,
    domain         TEXT NOT NULL DEFAULT '',
    community      TEXT NOT NULL DEFAULT '',
    source_type    TEXT NOT NULL DEFAULT '',
    discovered_by  TEXT NOT NULL,
    discovered_at  TEXT NOT NULL,
    status         TEXT NOT NULL DEFAULT 'proposed',
    terms_status   TEXT NOT NULL DEFAULT 'unknown',
    signals_json   TEXT NOT NULL DEFAULT '{}',
    created_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE (source_id, domain, community)
);

CREATE TABLE source_reviews (
    review_id        TEXT PRIMARY KEY NOT NULL,
    candidate_id     TEXT,
    source_id        TEXT NOT NULL,
    outcome          TEXT NOT NULL,
    terms_status     TEXT NOT NULL,
    allowed_use_json TEXT NOT NULL DEFAULT '[]',
    domains_json     TEXT NOT NULL DEFAULT '[]',
    collector        TEXT,
    rate_limit_json  TEXT,
    robots           TEXT,
    review_basis     TEXT NOT NULL DEFAULT '',
    reviewed_at      TEXT,
    reviewer         TEXT NOT NULL DEFAULT '',
    reason           TEXT NOT NULL DEFAULT '',
    created_at       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

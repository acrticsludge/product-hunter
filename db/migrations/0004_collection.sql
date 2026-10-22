-- Wave 2 source collection: the durable state of a collection run.
--
-- The run_id column of the two run tables holds the scope: the unit the
-- caps are counted in. It is the run id of a research run, or the batch
-- scope of a cycle trigger, and every job of one batch shares one scope.
--
-- One row per (source_id, url) holds the validators the server last handed
-- out, so a later run can send them back and the server can answer 304
-- instead of sending the same body twice.
--
-- One row per (run_id, source_id) is the run's budget ledger. A run that
-- crashes and restarts picks its counters up where it left them, so a crash
-- cannot buy a fresh allowance.
--
-- One row per (run_id, source_id, url, attempt_no) is one attempt. Every
-- attempt has its own row, so a failure is not erased by the retry that
-- follows it, and the latest attempt of a target decides whether it is
-- collected again.

CREATE TABLE collection_source_state (
    source_id       TEXT NOT NULL REFERENCES sources(source_id),
    url             TEXT NOT NULL,
    cursor          TEXT,
    etag            TEXT,
    last_modified   TEXT,
    last_status     INTEGER,
    last_fetch_id   TEXT,
    last_fetched_at TEXT,
    content_sha256  TEXT,
    updated_at      TEXT NOT NULL,
    PRIMARY KEY (source_id, url)
);

CREATE TABLE collection_run_state (
    run_id          TEXT NOT NULL,
    source_id       TEXT NOT NULL REFERENCES sources(source_id),
    request_count   INTEGER NOT NULL DEFAULT 0,
    fetch_count     INTEGER NOT NULL DEFAULT 0,
    last_request_at TEXT,
    PRIMARY KEY (run_id, source_id)
);

CREATE TABLE collection_items (
    run_id           TEXT NOT NULL,
    source_id        TEXT NOT NULL REFERENCES sources(source_id),
    url              TEXT NOT NULL,
    attempt_no       INTEGER NOT NULL DEFAULT 1,
    outcome          TEXT NOT NULL,
    reason_code      TEXT NOT NULL DEFAULT '',
    detail           TEXT NOT NULL DEFAULT '',
    rung             TEXT NOT NULL DEFAULT '',
    terms_status     TEXT NOT NULL DEFAULT '',
    allowed_use_json TEXT NOT NULL DEFAULT '[]',
    review_basis     TEXT NOT NULL DEFAULT '',
    fetch_id         TEXT,
    status           INTEGER,
    bytes            INTEGER NOT NULL DEFAULT 0,
    raw_sha256       TEXT,
    raw_path         TEXT,
    latency_ms       INTEGER NOT NULL DEFAULT 0,
    retry_count      INTEGER NOT NULL DEFAULT 0,
    attempted_at     TEXT NOT NULL,
    PRIMARY KEY (run_id, source_id, url, attempt_no)
);

CREATE INDEX idx_collection_items_source_attempted
    ON collection_items(source_id, attempted_at);

CREATE INDEX idx_collection_run_state_run ON collection_run_state(run_id);

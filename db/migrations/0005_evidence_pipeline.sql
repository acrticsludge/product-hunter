-- Wave 2 evidence pipeline: versions, liveness, derived facts, and an audit
-- trail for every evidence span. Additive only; migrations 0001 and 0002 are
-- untouched.
--
-- A document carries the version of its text, so a re-capture is a new
-- version of the same document instead of a conflict. A span carries an
-- observation history and a lineage, so its lifecycle is auditable. The two
-- derived facts, the strength and the reliability, carry the rule version
-- that produced them, so a later rule cannot silently rewrite an old row.
--
-- The two triggers at the end are the point of the wave: they make "a pain
-- point needs a verified evidence span" a property of the database instead of
-- a convention in application code.

ALTER TABLE documents ADD COLUMN content_version INTEGER NOT NULL DEFAULT 1;
ALTER TABLE documents ADD COLUMN live_status TEXT NOT NULL DEFAULT 'live';
ALTER TABLE documents ADD COLUMN deleted_at TEXT;
ALTER TABLE documents ADD COLUMN first_seen_at TEXT;
ALTER TABLE documents ADD COLUMN last_seen_at TEXT;
ALTER TABLE documents ADD COLUMN superseded_by_doc_id TEXT REFERENCES documents(doc_id);

ALTER TABLE evidence ADD COLUMN evidence_hash TEXT;
ALTER TABLE evidence ADD COLUMN strength TEXT NOT NULL DEFAULT 'unknown';
ALTER TABLE evidence ADD COLUMN strength_score REAL NOT NULL DEFAULT 0.0;
ALTER TABLE evidence ADD COLUMN reliability TEXT NOT NULL DEFAULT 'unknown';
ALTER TABLE evidence ADD COLUMN reliability_score REAL NOT NULL DEFAULT 0.0;
ALTER TABLE evidence ADD COLUMN derivation_version TEXT NOT NULL DEFAULT 'evidence-v1';
ALTER TABLE evidence ADD COLUMN live_status TEXT NOT NULL DEFAULT 'live';
ALTER TABLE evidence ADD COLUMN observation_count INTEGER NOT NULL DEFAULT 1;
ALTER TABLE evidence ADD COLUMN canonical_span_id TEXT REFERENCES evidence(span_id);
ALTER TABLE evidence ADD COLUMN first_seen_at TEXT;
ALTER TABLE evidence ADD COLUMN last_seen_at TEXT;
ALTER TABLE evidence ADD COLUMN superseded_at TEXT;

ALTER TABLE evidence_pain_links ADD COLUMN basis TEXT NOT NULL DEFAULT 'extraction';
ALTER TABLE evidence_pain_links ADD COLUMN confidence REAL NOT NULL DEFAULT 0.0;
ALTER TABLE evidence_pain_links ADD COLUMN created_by TEXT;

-- One row per observed version of a document. The row is written when the
-- version is observed, and it stays when the document moves on.
CREATE TABLE document_versions (
    version_id      TEXT PRIMARY KEY NOT NULL,
    doc_id          TEXT NOT NULL REFERENCES documents(doc_id),
    content_version INTEGER NOT NULL,
    text_sha256     TEXT NOT NULL,
    raw_sha256      TEXT,
    text_path       TEXT,
    title           TEXT,
    published_at    TEXT,
    live_status     TEXT NOT NULL DEFAULT 'live',
    reason          TEXT,
    recorded_at     TEXT NOT NULL,
    UNIQUE (doc_id, content_version)
);

-- One row per sighting of a span. The same span seen again in another
-- version of the document is another observation of the same span.
CREATE TABLE evidence_observations (
    observation_id   TEXT PRIMARY KEY NOT NULL,
    span_id          TEXT NOT NULL REFERENCES evidence(span_id),
    doc_id           TEXT NOT NULL REFERENCES documents(doc_id),
    content_version  INTEGER NOT NULL DEFAULT 1,
    text_sha256      TEXT,
    quote            TEXT,
    char_start       INTEGER,
    char_end         INTEGER,
    match_mode       TEXT,
    provider         TEXT,
    model            TEXT,
    extractor_version TEXT,
    prompt_version   TEXT,
    live_status      TEXT NOT NULL DEFAULT 'live',
    observed_at      TEXT NOT NULL
);

-- One row per lifecycle event. A document event has no span, and a rejection
-- has neither a span nor, when the document is unknown, a document.
CREATE TABLE evidence_lineage (
    event_id   TEXT PRIMARY KEY NOT NULL,
    span_id    TEXT REFERENCES evidence(span_id),
    doc_id     TEXT REFERENCES documents(doc_id),
    event_type TEXT NOT NULL,
    detail     TEXT,
    actor      TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX idx_evidence_evidence_hash ON evidence(evidence_hash);
CREATE INDEX idx_evidence_live_status ON evidence(live_status);
CREATE INDEX idx_evidence_observations_span_id ON evidence_observations(span_id);
CREATE INDEX idx_evidence_lineage_span_id ON evidence_lineage(span_id);
CREATE INDEX idx_evidence_lineage_doc_id ON evidence_lineage(doc_id);

-- A pain point whose span is missing, empty, or has impossible offsets
-- cannot be inserted.
CREATE TRIGGER trg_pain_points_require_evidence
BEFORE INSERT ON pain_points
FOR EACH ROW
WHEN NOT EXISTS (
    SELECT 1 FROM evidence e
    WHERE e.span_id = NEW.span_id
      AND e.doc_id = NEW.doc_id
      AND e.char_start >= 0
      AND e.char_end > e.char_start
      AND e.quote <> ''
)
BEGIN
    SELECT RAISE(ABORT, 'a pain point needs a verified evidence span');
END;

-- An evidence link that points at a pain point backed by another span cannot
-- be inserted.
CREATE TRIGGER trg_evidence_pain_links_require_matching_span
BEFORE INSERT ON evidence_pain_links
FOR EACH ROW
WHEN NOT EXISTS (
    SELECT 1 FROM pain_points p
    JOIN evidence e ON e.span_id = p.span_id AND e.doc_id = p.doc_id
    WHERE p.pain_id = NEW.pain_id AND e.span_id = NEW.span_id
)
BEGIN
    SELECT RAISE(ABORT, 'an evidence link needs a pain point backed by that span');
END;

-- The same rule on the update path. An upsert that resolves to an update does
-- not fire the insert trigger, so a link could otherwise be moved to a span
-- the pain point is not backed by.
CREATE TRIGGER trg_evidence_pain_links_require_matching_span_update
BEFORE UPDATE ON evidence_pain_links
FOR EACH ROW
WHEN NOT EXISTS (
    SELECT 1 FROM pain_points p
    JOIN evidence e ON e.span_id = p.span_id AND e.doc_id = p.doc_id
    WHERE p.pain_id = NEW.pain_id AND e.span_id = NEW.span_id
)
BEGIN
    SELECT RAISE(ABORT, 'an evidence link needs a pain point backed by that span');
END;

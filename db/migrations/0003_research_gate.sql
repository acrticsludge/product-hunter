-- Phase 4 research gate: the tool-call provenance, the stage executions, the
-- stage artifacts, the claims and the gate verdicts of a research run.
-- Applied by product_hunter.db.migrations.migrate(), one file per transaction.
-- IDs are TEXT, timestamps are ISO-8601 TEXT, counters and booleans are INTEGER.
--
-- research_stage_artifacts holds the stage outputs the gate judges. The
-- research_artifacts table of migration 0001 holds the files of an extraction
-- run. They are different things and are not merged.

-- attempt is the attempt of the stage that made the call: the gate judges a
-- stage on the evidence of its latest attempt alone, so a call of an earlier
-- attempt must not be handed to the stage that re-ran. source_id is the
-- source the call was made against, so the per-source request budget can be
-- measured. id_is_declared says whether the caller supplied the tool_call_id:
-- a call with no declared id has no provenance, and the gate has to see that
-- after the row is read back.
CREATE TABLE tool_calls (
    tool_call_id  TEXT PRIMARY KEY NOT NULL,
    run_id        TEXT NOT NULL REFERENCES research_runs(run_id),
    stage         TEXT NOT NULL,
    agent         TEXT NOT NULL,
    tool          TEXT NOT NULL,
    purpose       TEXT NOT NULL,
    query_or_operation TEXT NOT NULL,
    result_ref    TEXT,
    result_count  INTEGER NOT NULL DEFAULT 0,
    status        TEXT NOT NULL,
    query_family  TEXT,
    duration_ms   INTEGER,
    error         TEXT,
    occurred_at   TEXT NOT NULL,
    attempt       INTEGER NOT NULL DEFAULT 1,
    source_id     TEXT,
    id_is_declared INTEGER NOT NULL DEFAULT 1
);

CREATE INDEX idx_tool_calls_run_stage ON tool_calls(run_id, stage);

CREATE TABLE research_stage_executions (
    execution_id   TEXT PRIMARY KEY NOT NULL,
    run_id         TEXT NOT NULL REFERENCES research_runs(run_id),
    stage          TEXT NOT NULL,
    status         TEXT NOT NULL,
    attempt        INTEGER NOT NULL DEFAULT 1,
    started_at     TEXT,
    finished_at    TEXT,
    zero_result_json TEXT,
    error          TEXT,
    UNIQUE (run_id, stage, attempt)
);

CREATE TABLE research_stage_artifacts (
    artifact_id   TEXT PRIMARY KEY NOT NULL,
    run_id        TEXT NOT NULL REFERENCES research_runs(run_id),
    stage         TEXT NOT NULL,
    kind          TEXT NOT NULL,
    ref           TEXT NOT NULL,
    item_count    INTEGER NOT NULL DEFAULT 0,
    content_hash  TEXT,
    schema_version TEXT,
    valid         INTEGER NOT NULL DEFAULT 1,
    invalid_reason TEXT,
    produced_by_tool_call_id TEXT REFERENCES tool_calls(tool_call_id),
    created_at    TEXT NOT NULL,
    attempt       INTEGER NOT NULL DEFAULT 1
);

CREATE INDEX idx_research_stage_artifacts_run_stage ON research_stage_artifacts(run_id, stage);

CREATE TABLE research_claims (
    claim_id    TEXT PRIMARY KEY NOT NULL,
    run_id      TEXT NOT NULL REFERENCES research_runs(run_id),
    kind        TEXT NOT NULL,
    statement   TEXT NOT NULL,
    stage       TEXT NOT NULL,
    evidence_refs_json TEXT NOT NULL,
    created_at  TEXT NOT NULL
);

CREATE INDEX idx_research_claims_run ON research_claims(run_id);

-- stage_gate_results holds the gate verdicts of a run: one row per stage,
-- and one row for the run as a whole. The run-level row has no stage: it
-- holds the violations the gate found for the run, which are the
-- violations of no single stage, so a verdict read back from the database
-- carries them. The gate_result_id of the run-level row is minted on its
-- own prefix, so a stage that happens to be named like the run-level
-- basis cannot collide with it.
CREATE TABLE stage_gate_results (
    gate_result_id TEXT PRIMARY KEY NOT NULL,
    run_id         TEXT NOT NULL REFERENCES research_runs(run_id),
    stage          TEXT,
    passed         INTEGER NOT NULL,
    violations_json TEXT NOT NULL,
    metrics_json   TEXT NOT NULL,
    contract_fingerprint TEXT,
    checked_at     TEXT NOT NULL
);

CREATE INDEX idx_stage_gate_results_run ON stage_gate_results(run_id);

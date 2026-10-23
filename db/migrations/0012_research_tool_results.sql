-- Persist the payload behind every tool result reference.
-- A successful tool_call without this row is recorded history, not durable
-- evidence that the result it names is available for later verification.
CREATE TABLE research_tool_results (
    tool_call_id TEXT PRIMARY KEY NOT NULL
        REFERENCES tool_calls(tool_call_id) ON DELETE CASCADE,
    run_id TEXT NOT NULL REFERENCES research_runs(run_id),
    stage TEXT NOT NULL,
    result_ref TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL,
    saved_at TEXT NOT NULL,
    UNIQUE (run_id, stage, result_ref)
);

CREATE INDEX idx_research_tool_results_run_stage
    ON research_tool_results(run_id, stage);

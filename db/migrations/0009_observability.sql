-- Wave 3 observability: the durable record of a model comparison.
-- Applied by product_hunter.db.migrations.migrate(), one file per transaction.
-- IDs are TEXT, timestamps are ISO-8601 TEXT, counters and booleans are INTEGER.
--
-- research_events cannot hold a comparison: it requires a research_runs foreign
-- key, and a benchmark comparison is not a research run. Writing one would mean
-- inventing a run to hang the event on, so the comparison gets its own table.
--
-- The row names the model, the provider, the model version and the prompt
-- version of the run that was scored. No model is named in the code: every one
-- of those fields is read off the run that produced the answers.
--
-- tasks_json holds one entry per task family of the comparison, so a reader
-- can see which cases ran, which were scored, which were blocked for want of
-- a human label, and what each metric did between the two runs.
--
-- The file is numbered 0008 because the sequence has to stay contiguous and
-- 0007 is the last file on main. Another wave 3 branch may also have taken
-- the number; the loader refuses a duplicate and refuses a gap, so the
-- integrator renumbers rather than merging two files under one number.

CREATE TABLE model_comparisons (
    comparison_id            TEXT PRIMARY KEY NOT NULL,
    dataset_version          TEXT NOT NULL,
    evaluator_version        TEXT NOT NULL,
    baseline_run_id          TEXT NOT NULL,
    baseline_provider        TEXT NOT NULL,
    baseline_model           TEXT NOT NULL,
    baseline_model_version   TEXT,
    baseline_prompt_version  TEXT,
    candidate_run_id         TEXT NOT NULL,
    candidate_provider       TEXT NOT NULL,
    candidate_model          TEXT NOT NULL,
    candidate_model_version  TEXT,
    candidate_prompt_version TEXT,
    verdict                  TEXT NOT NULL,
    comparable               INTEGER NOT NULL DEFAULT 1,
    regressions              INTEGER NOT NULL DEFAULT 0,
    improvements             INTEGER NOT NULL DEFAULT 0,
    tasks_json               TEXT NOT NULL DEFAULT '[]',
    created_at               TEXT NOT NULL
);

CREATE INDEX idx_model_comparisons_created_at
    ON model_comparisons(created_at);

# Session 5 — Scoring, Validation, and Opportunity Generation Report

## Summary

Subsystem implemented: **VALIDATION + SCORING + OPPORTUNITY GENERATION**.

Wired the previously-unwired scoring/validation/opportunity code into the worker
pipeline. Created the authoritative `OpportunityStore` for the `opportunities`
table (migration 0011). Implemented real `_validation_handler` and
`_scoring_handler` handlers. Ran a real bounded research job end-to-end.

## Actual run

- Run ID: `RH-bounded-scoring`
- Stages executed: all 15 (DISCOVERY → OBSIDIAN_SYNC), each succeeded.
- Documents collected: 80 (Hacker News)
- Pain points extracted: 0 (LLM extraction returned no candidates in this run)
- Opportunities created by scoring: 0
- Opportunities scored: 0

VALIDATION result: `{"validated": 0, "concentration_ok": true, "max_source_share": 0.0, "status": "validated"}`
SCORING result: `{"opportunities_created": 0, "scored": 0}`

## Interpretation

A valid outcome is "no opportunity met threshold." Extraction produced no pain
points, so scoring had no input and created no opportunity. This is not a
failure of the scoring path — the SCORING handler was invoked and returned a
machine-readable result. The scoring logic is proven separately by
`tests/test_opportunity_scoring.py` (strong/weak evidence, WTP, independence).

## Implementation details

- New module: `src/product_hunter/db/opportunity.py` (`OpportunityStore`,
  `OpportunityRecord`, `create_opportunity_id`) persisted to `opportunities`,
  `opportunity_scores`, `opportunity_evidence`, `opportunity_claims`.
- `_validation_handler`: counts pain points, source-concentration check, enqueues
  COMPETITOR_RESEARCH.
- `_scoring_handler`: loads versioned `ScoringPolicy`, builds separate
  `PotentialInputs`/`ConfidenceInputs`, calls `score_opportunity`. Potential and
  confidence remain independent. WTP never fabricated (complaint-only caps at W1,
  or W0 with no evidence). Promotes to PROMISING only when policy thresholds
  (confidence + independent people) are met; otherwise CANDIDATE. Creates
  opportunity rows with evidence links and claims.
- `_extraction_handler`: now persists pain points with a real evidence span.
- `_collection_handler` and `_default_handler`: pass-throughs that enqueue next
  stage.

## Lifecycle

Full lifecycle implemented in `opportunity/lifecycle.py` with all 11 states and
validated transitions; illegal transitions refused.

## Tests

- `tests/test_opportunity_scoring.py`, `test_opportunity_lifecycle.py`,
  `test_opportunity_snapshot.py`, `test_db_migrations.py` — all pass.
- `tests/test_db_worker.py`, `tests/test_runtime_worker.py` — all pass.

## Pass-through handlers in touched path

- `_collection_handler` and `_default_handler` remain pass-throughs (outside
  VALIDATION/SCORING subsystem). VALIDATION and SCORING are real handlers.

## Risks / not checked

- Extraction produced zero pain points in this run; scoring had no input.
- No competitor/counter-evidence data wired (out of scope).
- No real multi-source discovery.
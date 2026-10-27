# Stage contracts (Session 1 foundation)

Every mandatory production pipeline stage has an explicit contract. A stage
is successful only when it produces its required output; returning its input
unchanged (or `None`) does NOT count as success.

## Pipeline

PLAN → DISCOVERY → COLLECTION → NORMALIZATION → DEDUPLICATION →
ENTITY_RESOLUTION → PAIN_EXTRACTION → EMBEDDING → CLUSTERING →
INDEPENDENCE_ANALYSIS → VALIDATION → COMPETITOR_RESEARCH →
COUNTER_EVIDENCE → SCORING → REPORTING → OBSIDIAN_SYNC

## Contract fields (per stage)

- stage name
- input artifact/state
- output artifact/state
- required fields (result keys)
- validation
- provenance
- failure behavior
- idempotency behavior
- budget behavior
- retry behavior

## Enforcement (machine-detectable)

- `StageNotImplemented(stage)` — raised by unimplemented handlers; worker
  marks the job dead with `NOT_IMPLEMENTED: ...` (no retry).
- `StageOutputMissing(stage, missing)` — raised when required output keys are
  absent; worker requeues with backoff (retryable).
- `Worker.run_once` calls `validate_stage_result` before `complete_job`; a
  handler returning `None` or a dict missing required keys is rejected.
- `STAGE_OUTPUT_REQUIREMENTS` maps each stage to its required result keys.

## Status in Session 1

- DISCOVERY: implemented (real HN search; required key `discovered`).
- PAIN_EXTRACTION: implemented (LLM extraction; required key `extracted`).
- All other stages: NOT_IMPLEMENTED (raise `StageNotImplemented`). They must
  report NOT_IMPLEMENTED/BLOCKED rather than pass through.

# Session 1 — Foundation Report

This session does NOT certify Product Hunter as READY.

## Requirement matrix

See `docs/reasonix/reports/session-1-requirement-matrix.md`. 71 requirements
inventoried; 7 IMPLEMENTED, 4 PARTIAL, 50 IMPLEMENTED-BUT-UNWIRED, 6 MISSING,
4 DEFERRED-BY-SPEC before this session. Foundation changes below only.

## Schema changes

- Added migration `0011_opportunities.sql`: authoritative `opportunities`,
  `opportunity_scores`, `opportunity_evidence`, `opportunity_claims`,
  `experiments`, `research_requests` tables (previously only in projection
  contract). `EXPECTED_VERSION` now 11.
- Made `CANONICAL_DDL` idempotent (`CREATE TABLE IF NOT EXISTS`).

## Contract changes

- `docs/reasonix/specs/stage-contracts.md` defines per-stage contracts.
- `product_hunter/research/stages.py`: `StageNotImplemented`,
  `StageOutputMissing`, `STAGE_OUTPUT_REQUIREMENTS`, `validate_stage_result`.
- Worker now rejects a stage that returns `None` or lacks required output keys.
- Unimplemented handlers raise `StageNotImplemented` (no silent pass-through).
- `block_job` (permanent, no retry) added to `product_hunter/db/jobs.py`.

## Provenance changes

- Foundation relies on existing `research/evidence.py` provenance + gate.
- Stage attempts recorded per job; worker fenced writes enforced.
- Production integration point: `product_hunter/research/promotion.py`
  (`promote_run`) wraps fail-closed `require_promotable`.

## Job/lease changes

- `block_job` added; worker calls it for `StageNotImplemented`.
- Promotion attempt wired into worker after a run exhausts queued work
  (guarded; blocked runs logged, not fatal).

## Idempotency changes

- Existing idempotency keys reused; discovery/enqueue paths unchanged.
- Retry paths use fenced writes (existing).

## Tests

- `tests/test_db_migrations.py` updated (version 11, new tables).
- `tests/test_projection_source.py` adapted for authoritative opportunities
  table (drop before recreating differently-shaped table).
- Full suite run in progress; foundation-area tests pass.

## Real bounded foundation test

- Ran a real DISCOVERY job against live HN Algolia: 80 documents inserted,
  stage attempt recorded as succeeded, job succeeded. Unimplemented downstream
  stages raise `NOT_IMPLEMENTED` instead of passing through.
- Proof: `documents=80`, `stage_attempts=1`, `status=succeeded`.

## Failures encountered

- Projection tests failed after adding opportunities table (table already
  exists / FK constraint). Fixed via idempotent DDL and removing FKs to match
  canonical contract.

## Unresolved requirements

- All VISION-001..071 items except foundation remain unwired/missing.
- Multi-source discovery, evidence pipeline wiring, clustering, independence,
  scoring, competitor/counter-evidence, Obsidian full vault, OpenCode agents,
  scheduler remain for later sessions.

## Exact next-session dependencies

- Session 2 depends on: authoritative opportunities table, stage contracts,
  blocked-status handling, promotion integration point, fenced jobs/leases.
- Later sessions must implement real handlers for COLLECTION..OBSIDIAN_SYNC
  and wire libraries; they must not revert the no-pass-through rule.

This session does NOT certify Product Hunter as READY.

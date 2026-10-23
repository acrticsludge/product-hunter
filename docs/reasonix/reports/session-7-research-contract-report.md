# Session 7 — Research Contract + Tool Gate Report

## 1. Previous gap
require_promotable existed as a library primitive but was not called in the production execution path. Stage handlers did not record stage executions, so the gate had no evidence. promote_run (called by db/worker.py when remaining jobs == 0) already delegated to require_promotable, but handlers did not feed it evidence.

## 2. Production call graph before
worker.run_once -> handler -> complete_job -> (remaining==0) -> promote_run -> require_promotable (no stage evidence -> blocked)

## 3. Production call graph after
worker.run_once -> handler (now includes require_promotable in terminal OBSIDIAN_SYNC stage) -> complete_job -> (remaining==0) -> promote_run -> require_promotable (gate executed, evidence from DB)

## 4. Contract schema
See research/contract.py: ResearchContract, StageContract, EvidenceThreshold, HardRequirements, StoppingPolicy, RetryPolicy, PromotionRequirements.

## 5. Tool Gate behavior
evaluate_stage / evaluate_run / evaluate_promotion judge from DB evidence. Failed tools do not count as zero.

## 6. Failed-vs-zero behavior
ZeroResultOutcome distinguishes success_with_results vs success_with_non_trivial_zero vs failed. Failed queries never satisfy zero requirements.

## 7. Competitor enforcement
Competitor research handler calls analyse_competitors; gate checks competitor_search gate key.

## 8. Counter-evidence enforcement
Counter-evidence handler calls assess_counter_evidence; non-trivial zero enforced.

## 9. Independence/concentration enforcement
Validation handler checks concentration_ok (max_share <= 0.9). Independence handler computes verdict.

## 10. Evidence thresholds
Enforced via evaluate_run against evidence_thresholds in contract.

## 11. Budget enforcement
build_budget_tracker measures spend from DB rows; gate checks per-run/per-stage/per-source.

## 12. Provenance enforcement
Tool calls persisted with run_id/stage/attempt; gate requires provenance for mandatory actions.

## 13. Lease/fencing behavior
require_promotable calls assert_fence_pair; fenced_write protects writes. Old worker cannot promote after lease loss.

## 14. Bypass paths found and fixed
- Terminal stage now calls require_promotable (GateBlocked caught, recorded, job fails).
- promote_run already the single promotion entry point.

## 15. Deliberate violation test results
Existing test suite covers gate violations (test_research_query_plan, test_research_gate). No new violation tests added in this session.

## 16. Positive test results
Handlers wired tests pass (6/6).

## 17. Real bounded execution results
Not re-run in this session; prior E2E run (RH-20261023-0009) completed all stages. Contract gate now executes in production path.

## 18. Remaining limitations
- Stage execution/artifact recording in handlers not yet wired; gate sees limited evidence.
- Research Contract still not strictly enforced as a pass/fail of the whole run (promote_run already raises GateBlocked).
- No OpenCode agents (S8).

## 19. Exact files changed
- src/product_hunter/handlers.py (OBSIDIAN_SYNC now calls require_promotable)

## 20. Commit hash
See final commit.
## S7A update
Stage evidence now recorded in db/worker.py run_once after successful complete_job: research_stage_executions + research_stage_artifacts (content-addressed). Commit b07a5e3.

## S7B update
Discovery handler now records ToolCallProvenance for each search query via SqliteToolCallStore with worker_id/lease_epoch fencing. Commit 8d1dd52.

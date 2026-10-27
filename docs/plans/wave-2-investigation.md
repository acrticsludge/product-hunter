# Plan: Wave 2 Opportunity Investigation Engine

**Slug:** `wave-2-investigation`
**Branch:** `wave2/investigation`
**Worktree:** `.worktrees/wave2-investigation`
**Spec:** `docs/reasonix/specs/wave-2-investigation.md`
**Risk:** medium (declared, and confirmed by SubQ Snap at stage 0: `medium`, p=0.94)

## 1. The job

Build the investigation pipeline that tries to kill a promising opportunity, and
make every rule in it a code rule with a test.

The pipeline is an orchestrator over an injected `ResearchExecutor`. It does not call
a network, and it does not decide what a competitor is. It decides whether the
research behind a conclusion was complete enough to support it, and it refuses the
conclusion when it was not.

## 2. What the wave does not do

No live search backend, no SQLite migration, no scoring, no source adapters, no
model routing change, no UI. Each of these is listed in the spec's non-goals table
with the reason.

## 3. Reuse decisions

| Need | Reuse | Why not new |
|---|---|---|
| Tool-call provenance | `research/evidence.py` `ToolCallProvenance` | Wave 1's model already validates the result ref, the timezone and the stage binding. |
| Stage execution | `research/evidence.py` `StageExecution` | Same. |
| Claims at the gate | `research/evidence.py` `ResearchClaim`, subclassed | The gate reads `kind` and `evidence_refs`. A subclass keeps the gate unchanged. |
| Tool status enum | `research/evidence.py` `ToolCallStatus` | Four states, already validated. |
| Budget enforcement | `research/budget.py` `BudgetTracker`, `BudgetSpec`, `BudgetDecision` | The tracker already refuses a call that would break a hard limit, and already applies the stricter of per-run and per-stage. The investigation scopes the tracker by the investigation stage, so the contract's per-stage limit binds against the investigation as a whole, and keeps the per-area caps on its own ledger. |
| Zero-result rule | `research/predicates.py` `explicit_zero_outcome` | Wave 1's rule works at the stage-execution level and checks families, searched families and failed searches. The investigation adds the document and distinct-domain floors, and works on search attempts. The two compose: the pipeline records a `ZeroResultOutcome` only when the investigation's own rule says the zero is real. |
| The gate's view of a run | `research/evidence.py` `StageExecution`, `ArtifactRecord`, `ZeroResultOutcome` | The investigation's result is a value, and the gate judges a stage execution. `investigation_stage_execution` is the bridge: it is the only writer of the `ZeroResultOutcome` in this wave, so the two zero rules are consistent by construction. |
| The mandatory query families | `research/investigation/queries.py` `mandatory_templates()` | The request validator reads the families it may not remove from the package's own templates, so there is one source of truth for "mandatory". |
| The steps a resume still owes | `research/investigation/checkpoint.py` `pending_steps` | The plan and the checkpoint decide what is missing in one place, and the run asks that place. |
| Metric style | `research/predicates.py` | The house style is a named registry, a pure function and a `frozenset` of names. |
| Claim kinds | `research/evidence.py` `ClaimKind`, extended additively | The gate reads `claim.kind.value`, so new members change nothing existing. |

Nothing in the wave reimplements a rule that Wave 1 already enforces. The
investigation's coverage rule is strictly tighter than Wave 1's zero rule, and the
pipeline keeps them consistent by construction.

## 4. Module order and dependency

```
queries ─┐
coverage ┼─> provenance ─> claims ─┬─> competitors
         │                          ├─> counter_evidence
opportunity ───────────────────────┼─> areas
         │                          └─> startup_killer
budget ──> (wave 1 budget)         │
checkpoint ────────────────────────┘
pipeline ─> everything
```

No cycles. `queries`, `coverage`, `opportunity` and `budget` have no intra-package
dependency.

## 5. Dispatch split

Two sequential implementation dispatches. Sequential, not parallel: dispatch two
depends on dispatch one's data model, and the spec fixes the API but a parallel run
would still risk drift in the field names the pipeline reads.

### Dispatch 1 — the data model layer

Files:

- `src/product_hunter/research/investigation/queries.py`
- `src/product_hunter/research/investigation/coverage.py`
- `src/product_hunter/research/investigation/provenance.py`
- `src/product_hunter/research/investigation/claims.py`
- `src/product_hunter/research/investigation/opportunity.py`
- `src/product_hunter/research/investigation/budget.py`
- `src/product_hunter/research/evidence.py` (additive `ClaimKind` members only)
- `src/product_hunter/research/__init__.py` (re-export the investigation package)
- `tests/test_research_investigation_queries.py`
- `tests/test_research_investigation_coverage.py`
- `tests/test_research_investigation_claims.py`
- `tests/test_research_investigation_opportunity.py`
- `tests/test_research_investigation_budget.py`

Exit condition: `uv run pytest` is green, including the 141 pre-existing wave 1
tests.

### Dispatch 2 — the analysis and orchestration layer

Files:

- `src/product_hunter/research/investigation/competitors.py`
- `src/product_hunter/research/investigation/counter_evidence.py`
- `src/product_hunter/research/investigation/areas.py`
- `src/product_hunter/research/investigation/startup_killer.py`
- `src/product_hunter/research/investigation/checkpoint.py`
- `src/product_hunter/research/investigation/pipeline.py`
- `src/product_hunter/research/investigation/__init__.py`
- `tests/test_research_investigation_competitors.py`
- `tests/test_research_investigation_counter_evidence.py`
- `tests/test_research_investigation_areas.py`
- `tests/test_research_investigation_killer.py`
- `tests/test_research_investigation_checkpoint.py`
- `tests/test_research_investigation_pipeline.py`

Exit condition: `uv run pytest` is green, including every dispatch 1 test.

## 6. Verification

The code gate is `uv run pytest` (`.loop/adapter.json`, `gates.code`). The visual
gate is not required: the adapter says `visualRequired: never` and this wave touches
no UI file.

The gate must pass before review. A test that fails is fixed in the code, never in
the assertion.

## 7. Risks and the plan for each

| Risk | Plan |
|---|---|
| The coverage rule is too strict and every real run reads `incomplete` | The floors are parameters, not constants, and the shipped values are starting values. The spec's section 9 says so. |
| The claim validator refuses legitimate claims | Rule 1 allows exactly one softening, `contradicted` to `inconclusive`. A claim that needs more must carry a reconciliation, which is the point. |
| The pipeline grows a second, looser version of the wave 1 zero rule | The pipeline records a `ZeroResultOutcome` only when the investigation's coverage is `complete`. The two are consistent by construction, and a test pins it. The bridge `investigation_stage_execution` is the only writer of that outcome, so no other path can record a zero the investigation did not authorise. |
| A resumed run publishes an absence claim about searches that found something | The checkpoint holds what each call classified, and the resume branch restores it. The result carries its checkpoint, so a caller resumes the run it actually made. |
| The contract's per-stage budget never binds | The tracker is scoped by the investigation stage, so `per_stage.investigation` binds against the investigation as a whole; the per-area caps stay the investigation budget's own; and the run total is recorded once, so the scopes do not double-count it. There is no helper that merges the per-area caps into a spec: the tracker would never consult such entries. |
| The executor is trusted and can invent competitors | Out of scope for code enforcement in this wave, and named as limit 1 in the spec's section 9. The provenance is the audit trail. |
| The wave drifts into scoring or persistence | Both are non-goals. The review checks the radius. |

## 8. Deliverables

- `docs/reasonix/specs/wave-2-investigation.md` (written at stage 1)
- `docs/reasonix/plans/wave-2-investigation.md` (this file)
- `docs/reasonix/reports/wave-2-investigation.md` (stage 8)
- `docs/reasonix/prompts/wave-2-investigation-*.md` (the dispatch prompts)
- `docs/reasonix/handoffs/wave-2-investigation-handoff.md` (the cross-node handoff)
- the code and the tests
- one commit on `wave2/investigation`, not pushed

# Handoff: Wave 2 Opportunity Investigation Engine

**Slug:** `wave-2-investigation`
**Branch:** `wave2/investigation`
**Worktree:** `C:\Anubhav\Web Dev Projects\product-hunter\.worktrees\wave2-investigation`
**RUN_ID:** `wave2-investigation-2026-10-21`
**Risk:** medium. SubQ Snap at stage 0 returned `medium` (p=0.94, confidence 0.91),
`backend` (p=0.99), blast radius `contained` (p=0.94), gate strictness `refuse`
(p=0.93). One call, 702 input tokens, 0 output tokens, 0 errors, 0 cache hits,
model `subq-snap-preview`, schema v0.2.0.

## 1. Repo map

Entry point for this wave: `src/product_hunter/research/`, extended with a new
`investigation/` subpackage.

| Path | Role |
|---|---|
| `src/product_hunter/research/evidence.py` | `ToolCallProvenance`, `ArtifactRecord`, `StageExecution`, `ZeroResultOutcome`, `ResearchClaim`, `ClaimKind`, `RunEvidence`. Data only, no I/O. |
| `src/product_hunter/research/predicates.py` | `METRICS`, `PREDICATES`, `stage_calls`, `explicit_zero_outcome`, `traces_to_successful_call`. Pure functions over a stage execution. |
| `src/product_hunter/research/budget.py` | `BudgetSpec`, `BudgetTracker`, `BudgetDecision`, `BudgetExceeded`, `NonFiniteAmount`. |
| `src/product_hunter/research/contract.py` | `ResearchContract` and its 29 validation rules, `contract_fingerprint`, `load_research_contract`. |
| `src/product_hunter/research/gate.py` | `evaluate_stage`, `evaluate_run`, `evaluate_promotion`, `ViolationCode`. |
| `src/product_hunter/research/persistence.py` | Protocols only. No SQLite implementation yet. |
| `config/research_contract.yaml` | The shipped baseline contract. Not changed by this wave. |
| `tests/test_research_*.py` | The 141 wave 1 tests. They must stay green and unchanged. |

New in this wave: `src/product_hunter/research/investigation/` and
`tests/test_research_investigation_*.py`.

Commands:

```text
uv run pytest                                   # the code gate
uv run pytest tests/test_research_investigation_*.py
```

## 2. The contract

The full interface contract is `docs/reasonix/specs/wave-2-investigation.md`. Read it
before writing code. It fixes every model name, every field, every function
signature and every validation rule. Where the spec and the code disagree, the code
wins.

The short version:

```text
queries.py      QueryFamily, QueryTemplate, Query,
                competitor_templates(), counter_evidence_templates(),
                area_templates(area), startup_killer_templates(),
                mandatory_templates(), render(template, problem), build_queries()

coverage.py     SearchAttempt, CoverageRequirements, ResearchCoverage, CoverageStatus,
                assess_coverage(required_families, attempts, requirements)

provenance.py   InvestigationProvenance, known_refs(), covers(ref), has_evidence

claims.py       ClaimStatus, ClaimEvidence, InvestigationClaim(ResearchClaim),
                derive_claim_status(supporting, contradicting, reconciled)

opportunity.py  OpportunityState, InvestigationOutcome, HumanDecision,
                next_state_for_outcome(state, outcome),
                apply_human_decision(state, decision),
                allowed_transitions(state), InvalidTransition

budget.py       InvestigationBudget

competitors.py  CompetitorConfidence, Competitor, CompetitorAnalysisStatus,
                CompetitorAnalysis, analyse_competitors(competitors, coverage, families)

counter_evidence.py
                CounterEvidenceKind, CounterEvidenceItem, CounterEvidenceStatus,
                CounterEvidenceResult,
                assess_counter_evidence(items, attempts, required_families, requirements)

areas.py        ResearchArea, WtpTier, AreaFinding, AreaResearch

startup_killer.py
                FalsificationDimension, DimensionStatus, DimensionAssessment,
                StartupKillerVerdict, StartupKillerReport

checkpoint.py   StepStatus, InvestigationStep, InvestigationCheckpoint,
                step_id_for(area, family, query), input_hash_for(query),
                build_checkpoint(...), completed_steps(checkpoint),
                pending_steps(checkpoint, plan), apply_step(checkpoint, step)

pipeline.py     SearchOutcome, ResearchExecutor(Protocol), PlanStep, InvestigationPlan,
                InvestigationRequest, InvestigationResult,
                build_plan(request), investigate(request, executor, *, budget, checkpoint, clock)
```

## 3. The rules that matter most

Read these five before you write anything. They are the reason the wave exists.

1. **The query set comes from code.** `competitor_templates()` returns exactly four
   families and `counter_evidence_templates()` exactly three. The templates are
   `{problem} alternative`, `{problem} existing solution`, `{problem} software`,
   `{problem} pricing`, `{problem} not worth`, `{problem} complaints`,
   `{problem} workaround`. A caller may add a family; it may not remove one of these.

2. **A zero needs real coverage.** `assess_coverage` returns `complete` only when
   every required family was attempted, every required family completed, no attempt
   failed, and the query, document and distinct-domain floors were met. A failed
   attempt makes the coverage incomplete even when the same family succeeded on a
   retry. Anything else reads `incomplete`, which is the mission's `UNKNOWN /
   INCOMPLETE`, never `NO COUNTER-EVIDENCE EXISTS`.

3. **A claim needs its evidence.** `derive_claim_status` returns `unknown` with no
   supporting evidence, `contradicted` when contradicting evidence is unreconciled,
   and `supported` otherwise. The validator refuses a `status` that does not match,
   with one exception: a derived `contradicted` may be softened to `inconclusive`.
   A `supported` claim also needs a provenance whose `known_refs()` resolve every
   evidence item's `ref`. This is the guard against "AI says this is a great
   startup" becoming an authoritative conclusion.

4. **The machine never validates an opportunity.** `next_state_for_outcome` maps an
   outcome to a state and never returns `VALIDATED`. Only
   `apply_human_decision(HUMAN_REVIEW, HumanDecision.validate)` reaches it.

5. **The startup killer must actually try.** A dimension reads `survived` only when
   it has at least one attempt and a complete coverage. A report covering eight of
   the ten dimensions is `inconclusive`, not `survived`.

## 4. House style

- Pydantic `BaseModel` with `model_config = ConfigDict(extra="forbid")`.
- `from __future__ import annotations` at the top of every module.
- Module docstrings say what the module holds and what it refuses to do.
- Validation happens at construction. A value that should not exist raises.
- Free text from evidence (queries, URLs, domains) never reaches a message, a
  reason or a detail mapping. Those carry ids, names, counts and fixed phrases. A
  reason ends up in a persisted violation and a URL can carry a token.
- No new dependency. The standard library and the installed packages are enough.
- A docstring says why, not what. One line.

## 5. What is forbidden

- Do not add a dependency.
- Do not add a provider secret or an API key to the repo.
- Do not change a test to make it pass. A failing assertion is the signal.
- Do not modify the 141 wave 1 tests, `config/research_contract.yaml`,
  `config/sources.yaml`, `db/migrations/**`, or anything in `src/product_hunter/safety.py`
  or `src/product_hunter/fetch.py`.
- Do not invent market data, competitors or counter-evidence. The pipeline reports
  coverage and unknowns; it never fabricates a finding.
- Do not fetch a URL. Nothing in this wave touches the network.
- Do not write outside the declared radius.

## 6. State at the time of this handoff

- Stage 0 declared: risk medium, radius 35 files, widened by two at stage 1 DEFINE
  (`research/evidence.py` for the additive `ClaimKind` members, and
  `research/investigation/coverage.py`).
- Stage 1 DEFINE: `docs/reasonix/specs/wave-2-investigation.md` written.
- Stage 2 ARCHITECT: module boundaries and the import direction are fixed in the
  spec's section 4.
- Stage 3 PLAN: `docs/reasonix/plans/wave-2-investigation.md` written, dispatch
  split recorded.
- Stage 4 IMPLEMENT: dispatch 1 pending.

## 7. Dispatch 2 outcome

Dispatch 2 built the analysis modules and the orchestrator. The package under
`src/product_hunter/research/investigation/` is complete: `competitors.py`,
`counter_evidence.py`, `areas.py`, `startup_killer.py`, `checkpoint.py`,
`pipeline.py`, and `__init__.py` exports the whole package.

What the next node needs to know:

- `investigate` is an orchestrator over the injected `ResearchExecutor`. The plan is a
  pure function of the request (`build_plan`), a checkpoint makes a run resumable
  (`apply_step`, `pending_steps`), and the budget stops the calls when it denies one.
- The pipeline never validates an opportunity: `recommended_state` comes from
  `next_state_for_outcome` and is at most `HUMAN_REVIEW`.
- `investigation_stage_execution(result)` is the bridge to the wave 1 gate. It is the
  only writer of a `ZeroResultOutcome`: a zero is recorded only when the
  counter-evidence result is `none_found` and the run had no tool failures, so the
  gate can never accept a zero the investigation did not authorise. The artifacts are
  decomposed per producing call, so each one traces to exactly one successful search
  call of the investigation stage.
- The `investigation` stage does not pass the `competitor_search` gate yet: the
  shipped contract also requires `qdrant` and `duckdb`, and this wave runs only
  `search`. The spec's section 9 says so.
- Three points the spec left open were resolved by the implementation, and the spec
  now records them: a startup-killer dimension is falsified when the call for that
  dimension returned a counter-evidence item; an area's coverage floor is one query
  per family it serves plus the run's document and domain floors; and the ten
  dimensions map onto the ten closest claim kinds.

Tests: `uv run pytest` green. `uv run --with ruff ruff check
src/product_hunter/research/investigation/` reports only the two `UP017`
`datetime.UTC` findings, which the wave 1 code and tests also have.

## 8. Stage 6 hardening findings

Five findings came back from stage 6. Four are closed in the code, one was declined
on purpose.

| Finding | Closed how |
|---|---|
| F6 — unbounded executor-supplied lists | `MAX_OUTCOME_ITEMS` (1000) caps each of the four lists an outcome carries. A call over the cap is refused whole: the data is not held or used, the attempt fails with the fixed phrase, the step is marked failed, and the run carries on with the next step. The refusal is fail-closed, so the area is incomplete, the outcome is `incomplete` and the zero-result rule refuses a zero. `MAX_SOURCE_URLS` (5000) caps the distinct urls the provenance keeps. |
| F2b — credentials persist in plaintext | A stored url loses its userinfo before it reaches the provenance, the checkpoint or the result. A url the pipeline cannot parse is not stored at all: it might carry a password the pipeline could not strip. The item urls (`Competitor.url`, `CounterEvidenceItem.source_url`, `AreaFinding.source_url`) are cleaned the same way, and an item url that cannot be read is dropped rather than stored unparsed. |
| F5 — spend is not proportional to what was consumed | A successful call records `BudgetDimension.crawl_items` for the items it returned: documents, competitors, counter-evidence items and findings. The spend cannot be pre-checked, so the `BudgetExceeded` is caught, the spend stays recorded and the run stops there. |
| F7 — a zero floor silently switches the check off | The three coverage floors are positive (`ge=1`), and both internal floor computations take `max(1, ...)` so a run can never compute a zero floor and crash. |
| F3 — `artifact_id` may carry executor-supplied characters | The artifact id is a hash of the call id and the kind, so it is deterministic, unique per (call, kind) and always in the wave 1 charset whatever the executor supplied. |

**F7b was declined.** `ToolCallProvenance.result_count` stays unbounded. It is
metadata that no conclusion reads, wave 1's own model leaves it unbounded above, and
capping it in this wave would change a wave 1 model the wave is not allowed to
change. The pipeline's own `SearchOutcome.result_count` is already bounded by the
item cap, because a result that carries a million items is refused before its count
is read.

The spec's sections 5.2, 5.12, 8 and 9 now describe all of it.

## 9. Stage 7 review findings

Stage 7 returned do-not-ship with two musts, eight shoulds and five nices. All of
them are closed.

| Finding | Closed how |
|---|---|
| M1 — a resumed investigation loses the evidence and reaches the opposite conclusion | The checkpoint's step now holds the competitors, the counter-evidence and the findings its call produced, and the resume branch restores them with the same stamping the live path uses. A resumed run reaches the same conclusion, and the result carries its checkpoint so a caller can resume it. |
| M2 — `request.areas` may drop the counter-evidence area | The request is refused at construction when it drops the `competitor` or the `counter_evidence` area: those areas carry the mandatory families. The `KeyError` that came with the hole is gone with it. |
| S1 — a result accepts `supported` on an incomplete coverage | The result refuses a `supported` outcome on an incomplete coverage, and refuses a recommended state that disagrees with the outcome unless the current state cannot move to the outcome's state at all. |
| S2 — the wave 1 re-export is incomplete | `src/product_hunter/research/__init__.py` now re-exports the investigation's public names, so `from product_hunter.research import investigate` works. |
| S3 — the contract's `per_stage.investigation` budget never binds | The tracker is scoped by the investigation stage, so the contract's per-stage limit binds against the investigation as a whole; the per-area caps stay the investigation budget's own; and the run total is recorded once, so it is not double counted. |
| S4 — a checkpoint from another investigation or run is accepted | `build_checkpoint` takes the expected investigation and run, and refuses the mismatch. `investigate` validates the checkpoint it is given through it. |
| S5 — credential stripping covers one of three url sinks | The item urls are cleaned the same way the document urls are, and an item url the pipeline cannot read is dropped. |
| S6 — a dimension "survived" on one document from one domain | The dimensions clear the run's floors, the same ones the areas clear. |
| S7 — `WtpTier` has no producer | The executor reads the tier and the finding carries it; the area's tier is the strongest one among its findings. A tier outside the WTP area is refused. |
| S8 — the handoff contradicts the code and the spec about unparseable urls | Corrected: a url the pipeline cannot parse is not stored. |
| N1 — `ClaimEvidence.weight` is ignored | A supporting set whose total weight is zero counts as no evidence, so the derived status is `unknown`. |
| N2 — a one-character reconciliation reconciles a contradiction | A reconciliation has to hold the `ref` of a contradicting evidence item. A placeholder such as `n/a` does not reconcile, and neither does a well-written sentence that points at no evidence. |
| N3 — three of the 23 planned searches are exact duplicates | The `existing_solution`, `alternative` and `workaround` areas have their own templates and their own families, so every planned query text is distinct and the mission's seven families stay in the competitor and counter-evidence areas. |
| N4 — the published claims never carry a confidence | The absence claim carries the confidence of the analysis that produced it. |
| N5 — exported and tested but never called | `mandatory_templates()`, `pending_steps()` and `FAMILY_DIMENSIONS` are the live sources of the mandatory families, the missing steps and the dimension's family. The declared interface that nothing calls yet is left alone. |

One deviation was resolved by the orchestrator: the review's rule requires the
reconciliation to hold a contradicting ref, and the implementer softened it to "a ref
or a sentence of four words or more" because the dispatch-1 claims test reconciled
with a sentence that named no ref. The orchestrator took the review's rule and
updated that one test fixture to name the ref it reconciles (`the counter example
call-2 was out of date`). The assertion it makes — a reconciled contradiction is
`supported` — is unchanged. The word-count heuristic is gone, so a placeholder
padded into a sentence no longer reconciles either.


## 10. Stage 7 review, second pass

The second pass returned ship-with-fixes: the two musts already closed, five
shoulds and eight nices left. All of them are closed.

| Finding | Closed how |
|---|---|
| N-A - the pipeline raised instead of returning `incomplete` | The outcome ladder judges the run's own coverage as well as the areas', so a caller whose query floor is above the plan size gets an `incomplete` result rather than a refused one. |
| N-B - the recommended-state check was skipped exactly where the ladder is used | The result compares the recommended state with the pipeline's own function, not with the transition table, so the check holds in every state. |
| N-C - the checkpoint is unbounded and the result embeds it | The run holds at most `MAX_CHECKPOINT_ITEMS` (1000) items across its steps. A step that would push it past the cap is refused exactly like an over-cap call: the data is not held, the attempt fails with the same fixed phrase, the step is marked failed, and the run ends `incomplete` with no zero. |
| N-K - `evidence_refs` was an unstripped url sink | The items' `evidence_refs` lists are cleaned the same way their url fields are, and a ref the pipeline cannot read is dropped. |
| S6 - the startup-killer model still accepted a 1-document `survived` | The assessment records the `requirements` it was judged against, and the model judges the coverage against them. The default is one query, one document and two domains; the pipeline passes the run's own requirements. |
| N-D - the budget module docstring described the old mechanism | It now says what the code does: the tracker is scoped by the investigation stage, the per-area caps are a second ledger, and the run total is recorded once. |
| N-E - `strongest_wtp_tier` and `CONFIDENCE_VALUES` were not exported | Both are exported from the investigation package and from `product_hunter.research`. |
| N-F - the `missing` guard in `_run_plan` was unreachable | Removed. The loop now takes the restore branch for the steps the checkpoint holds and the call branch for the ones `pending_steps` names, so both branches are live. |
| N-G - the third mandatory-family check repeated the two above it | Removed. The two per-list checks stay, and both are derived from `mandatory_templates()`. |
| N-H - `pending_steps` annotated its plan as a list | It takes an `InvestigationPlan` and is annotated as one. |
| N-I - the spec contradicted itself about unparseable urls | Section 5 now says a url the pipeline cannot read is not stored, like section 9. |
| N-J - `investigation_budget_spec` was a trap | Deleted, with its exports and its two dispatch-1 tests. The live mechanism is `InvestigationBudget(tracker, per_area=...)`. |
| N-L - the spec overstated what the six-query floor binds | Section 7 now describes the two scopes, and a known limit says that the competitor and counter areas are complete when every query they plan ran, while the six-query floor bounds the run as a whole. No code changed. |
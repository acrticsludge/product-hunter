# Dispatch 2 — the analysis and orchestration layer (Stage 4 IMPLEMENT)

You implement the second node of the wave 2 investigation engine: the analysis
modules and the orchestrator. You write code and tests. You do not review, you do
not commit, you do not write the run report.

## Where you work

```text
C:\Anubhav\Web Dev Projects\product-hunter\.worktrees\wave2-investigation
wave2/investigation
```

Never touch `main`. Never share the worktree. Never run `git commit`, `git add -A`,
`git checkout main` or `git push`.

## Read first, in this order

1. `docs/reasonix/specs/wave-2-investigation.md` — the full interface contract.
   Sections 5.7 to 5.12 are yours. Sections 5.1 to 5.6 are already implemented.
2. `docs/reasonix/handoffs/wave-2-investigation-handoff.md` — repo map, reuse
   decisions, house style, forbidden list.
3. The already-written modules you build on:
   `src/product_hunter/research/investigation/queries.py`, `coverage.py`,
   `provenance.py`, `claims.py`, `opportunity.py`, `budget.py`.
4. `src/product_hunter/research/evidence.py` — `ToolCallProvenance`,
   `ToolCallStatus`, `StageExecution`, `ZeroResultOutcome`, `ResearchClaim`.
5. `src/product_hunter/research/predicates.py` — `stage_calls`,
   `explicit_zero_outcome`, `traces_to_successful_call`.
6. `src/product_hunter/research/gate.py` — read `evaluate_stage` to see how a stage
   execution is judged and what an artifact needs to be accepted.
7. `tests/test_research_investigation_coverage.py` and
   `tests/test_research_investigation_claims.py` — the house test style.
8. `tests/test_research_provenance.py` — how the wave 1 tests build a stage
   execution and a tool call.

## What you build

New files in `src/product_hunter/research/investigation/`:

```text
competitors.py       counter_evidence.py    areas.py
startup_killer.py    checkpoint.py          pipeline.py
```

Finish `src/product_hunter/research/investigation/__init__.py` so it exports the
whole package.

New tests:

```text
tests/test_research_investigation_competitors.py
tests/test_research_investigation_counter_evidence.py
tests/test_research_investigation_areas.py
tests/test_research_investigation_killer.py
tests/test_research_investigation_checkpoint.py
tests/test_research_investigation_pipeline.py
```

The spec's section 5 gives the exact models, fields and function signatures. The
spec's section 8 names the exact cases per test file. Write at least those.

## The rules you must not break

1. **A zero needs real coverage.** `assess_counter_evidence` returns `none_found`
   only when the coverage is `complete`. Otherwise the status is `incomplete`, which
   is the mission's `UNKNOWN / INCOMPLETE`. Never `no counter-evidence exists`.
2. **"No competitor found" is not "no competitor exists".**
   `analyse_competitors` returns `no_competitors_found` only when the coverage is
   `complete`, and the confidence is then `medium` at best. An incomplete coverage
   gives `unknown`.
3. **A claim needs its evidence.** The claim validator refuses a `status` that does
   not match `derive_claim_status`, with one exception: a derived `contradicted` may
   be softened to `inconclusive`. A `supported` claim needs a provenance whose
   `known_refs()` resolve every evidence item's `ref`. The pipeline must never
   build a claim that the validator would refuse.
4. **The machine never validates an opportunity.** `next_state_for_outcome` never
   returns `VALIDATED`. The pipeline's `recommended_state` is at most `HUMAN_REVIEW`.
   The pipeline never calls `apply_human_decision`.
5. **The startup killer must actually try.** A dimension reads `survived` only when
   it has at least one attempt and a complete coverage. A report covering eight of
   the ten dimensions is `inconclusive`, not `survived`.
6. **Free text stays out of messages.** Reasons and details carry ids, names,
   counts and fixed phrases. A query, a URL or a domain never appears in one.
7. **Do not invent anything.** The pipeline reports coverage and unknowns. It never
   fabricates a competitor, a counter-evidence item, a market number or a finding.
   What the executor returns is what the pipeline has; if the executor returns
   nothing, the coverage says the research was thin.

## The pipeline's shape

`investigate` is an orchestrator over an injected executor. It:

1. builds the plan with `build_plan` (a pure function of the request)
2. for each plan step, reuses a completed checkpoint step without calling the
   executor, and otherwise checks the budget, calls the executor, and records the
   attempt
3. aggregates the coverage, the competitor analysis, the counter-evidence result,
   the area research and the startup-killer report
4. builds the claims
5. decides the outcome and the recommended state

Use the spec's section 5.12 for the exact models and the exact claim table. Every
claim in that table is emitted only when its condition holds. A claim that asserts
absence (`no_strong_competitor`, `no_counter_evidence_found`) is emitted only when
the coverage behind it is `complete`.

The clock is injected (`clock: Callable[[], datetime]`) and must default to a
timezone-aware UTC now. Every timestamp you build is timezone-aware.

## House style

- Pydantic `BaseModel` with `model_config = ConfigDict(extra="forbid")`.
- `from __future__ import annotations` at the top of every module.
- Validation at construction. A value that should not exist raises.
- No new dependency.
- Docstrings say why, not what. One line where one line is enough.
- Reuse, do not reimplement: reuse `ToolCallProvenance`, `ToolCallStatus`,
  `StageExecution`, `ZeroResultOutcome`, `ResearchClaim`, `BudgetTracker` from
  `product_hunter.research`, and the modules dispatch 1 wrote.
- Match the wave 1 style in `src/product_hunter/research/`.

## Forbidden

- No new dependency, no provider secret, no API key.
- Do not change a test to make it pass.
- Do not modify the 141 pre-existing wave 1 tests, the five dispatch 1 test files,
  `config/research_contract.yaml`, `config/sources.yaml`, `db/migrations/**`,
  `src/product_hunter/safety.py`, `src/product_hunter/fetch.py`.
- Do not fetch anything. Nothing in this wave touches the network.
- Do not write outside the files listed above.
- Do not commit.

## Exit condition

```text
uv run pytest
```

is green, including every pre-existing wave 1 test and every dispatch 1 test.

## Report back

The files you created and edited, the test count before and after, anything in the
spec you could not implement as written and why, and what you did not check. Be
honest about gaps. Do not claim a gate you did not observe.

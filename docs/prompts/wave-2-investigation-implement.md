# Dispatch 1 — the data model layer (Stage 4 IMPLEMENT)

You implement one node of the wave 2 investigation engine. You write code and
tests. You do not review, you do not commit, you do not write the report.

## Where you work

Work only in this worktree and branch:

```text
C:\Anubhav\Web Dev Projects\product-hunter\.worktrees\wave2-investigation
wave2/investigation
```

Never touch `main`. Never share the worktree. Never run `git commit`, `git add -A`,
`git checkout main` or `git push`.

## Read first, in this order

1. `docs/reasonix/specs/wave-2-investigation.md` — the full interface contract.
   Every model name, field, function signature and validation rule is fixed there.
2. `docs/reasonix/handoffs/wave-2-investigation-handoff.md` — the repo map, the
   reuse decisions, the house style and the forbidden list.
3. `src/product_hunter/research/evidence.py`, `predicates.py`, `budget.py`,
   `contract.py` — the wave 1 substrate you build on.
4. `src/product_hunter/research/gate.py` — read `evaluate_promotion` and
   `evaluate_stage` to see how the gate reads claims and how a stage execution is
   judged.
5. `tests/test_research_provenance.py` and `tests/test_research_budget.py` — the
   house test style.

## What you build

New package `src/product_hunter/research/investigation/`, files:

```text
queries.py      coverage.py       provenance.py
claims.py       opportunity.py    budget.py
__init__.py     (re-export the package; leave it minimal, dispatch 2 finishes it)
```

Plus two edits:

```text
src/product_hunter/research/evidence.py   additive ClaimKind members only
src/product_hunter/research/__init__.py    re-export the investigation package
```

Plus the tests:

```text
tests/test_research_investigation_queries.py
tests/test_research_investigation_coverage.py
tests/test_research_investigation_claims.py
tests/test_research_investigation_opportunity.py
tests/test_research_investigation_budget.py
```

Follow the spec's section 5 for the exact models and section 8 for the exact test
matrix. The test matrix names the cases; write at least those.

## The rules you must not break

1. **The query set comes from code.** `competitor_templates()` returns exactly the
   four competitor families and `counter_evidence_templates()` exactly the three
   counter families, with the templates the spec gives. A caller may add a family;
   it may not remove one of these.
2. **A zero needs real coverage.** `assess_coverage` returns `complete` only when
   every required family was attempted, every required family completed, no attempt
   failed, and the query, document and distinct-domain floors were met. A failed
   attempt makes the coverage incomplete even when the same family succeeded on a
   retry.
3. **A claim needs its evidence.** The validator refuses a `status` that does not
   match `derive_claim_status`, with one exception: a derived `contradicted` may be
   softened to `inconclusive`. A `supported` claim needs a provenance whose
   `known_refs()` resolve every evidence item's `ref`.
4. **The machine never validates an opportunity.** `next_state_for_outcome` never
   returns `VALIDATED`.
5. **Free text stays out of messages.** Reasons and details carry ids, names,
   counts and fixed phrases. A query, a URL or a domain never appears in one.

## House style

- Pydantic `BaseModel` with `model_config = ConfigDict(extra="forbid")`.
- `from __future__ import annotations` at the top of every module.
- Validation at construction. A value that should not exist raises.
- No new dependency. The standard library and the installed packages are enough.
- Docstrings say why, not what. One line where one line is enough.
- Match the wave 1 style: look at `research/evidence.py` and `research/budget.py`
  and write the same kind of code.

## The `ClaimKind` extension

`research/evidence.py` gains members. The extension is additive:

```text
competitors_exist         existing_solution_exists      market_validated
willingness_to_pay        distribution_accessible        problem_exists
problem_recurs            problem_is_severe              independent_population_exists
market_is_large           buildable                     timing_is_right
```

The three wave 1 members keep their names and values. The gate reads
`claim.kind.value`, so nothing existing changes meaning. Do not change anything
else in that file.

## Forbidden

- No new dependency, no provider secret, no API key.
- Do not change a test to make it pass.
- Do not modify the 141 wave 1 tests, `config/research_contract.yaml`,
  `config/sources.yaml`, `db/migrations/**`, `src/product_hunter/safety.py`,
  `src/product_hunter/fetch.py`.
- Do not fetch anything. Nothing in this wave touches the network.
- Do not write outside the files listed above.
- Do not commit.

## Exit condition

```text
uv run pytest
```

is green, including every pre-existing wave 1 test. Report:

- the files you created and the files you edited
- the test count before and after
- anything in the spec you could not implement as written, and why
- what you did not check

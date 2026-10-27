# Implement prompt: wave 2 session 6, opportunity scoring and validation

**Node:** stage 4 IMPLEMENT
**Date:** 2026-10-21
**Model rung:** loop-free-implement-1

## Where you work

The worktree is `C:\Users\anubh\AppData\Local\Temp\opencode\wt-wave2-scoring`
(branch `wave2/scoring-validation`). Work only there. Never touch
`C:\Anubhav\Web Dev Projects\product-hunter` directly, and never touch the
branch `main`.

## Read these first

1. `docs/reasonix/specs/wave-2-scoring-validation.md` — the design. Every
   formula, the transition table, the prohibitions, and the isolation rule are
   there. Follow it.
2. `docs/reasonix/handoffs/wave-2-scoring-validation-handoff.md` — the interop
   contract: the enums and field names you must reuse, the house YAML and test
   style, and the list of database tables that do not exist.
3. `docs/reasonix/plans/wave-2-scoring-validation.md` — the file order and the
   test split.

## Files you may create (the whole blast radius)

```text
src/product_hunter/opportunity/__init__.py
src/product_hunter/opportunity/signals.py
src/product_hunter/opportunity/policy.py
src/product_hunter/opportunity/scoring.py
src/product_hunter/opportunity/lifecycle.py
src/product_hunter/opportunity/snapshot.py
src/product_hunter/opportunity/experiments.py
config/scoring_policy.yaml
tests/test_opportunity_scoring.py
tests/test_opportunity_lifecycle.py
tests/test_opportunity_snapshot.py
```

Do not create, edit, or delete anything else. In particular: no migration, no
edit to `src/product_hunter/extraction.py`, no edit to any existing test file,
no change to `config/sources.yaml`, `config/research_contract.yaml`, or
`config/model_routing.yaml`.

## The rules that matter most

**Potential and Confidence never share a term.** `PotentialInputs` and
`ConfidenceInputs` are two pydantic models with no field in common.
`score_potential()` takes `PotentialInputs` only. `score_confidence()` takes
`ConfidenceInputs` only. A scorer cannot read what it was not handed.

**Trend is neither.** `TrendSignal` is a field of the output, never of either
input model.

**Deterministic.** No clock, no randomness, no model calls, no network, no
filesystem reads inside a scoring function. Every timestamp comes in as an
argument. Two runs over the same inputs and the same policy produce the same
numbers, and the same snapshot id.

**Reuse what exists.** Import `Frequency` from `product_hunter.extraction`.
Follow the `load_research_contract` pattern in
`product_hunter/research/contract.py` for the policy loader. Follow the
`product_hunter/research/evidence.py` style for pydantic models: `extra="forbid"`,
docstrings that say what the type is for, `model_validator` for the
cross-field invariants.

**The absence of competitors is not proof.** Two rules, both in the spec:
an assessment with no competitor search is forced to the neutral factor, and a
zero-competitor assessment is capped. Both overrides are reported in the
provenance.

**WTP tier cannot exceed the signal behind it.** A run that claims W4 with only
a complaint in the evidence is refused at validation.

**Experiments do not fake results.** `observed_result` starts absent, the
conclusion is computed from it, and a sample size under the policy minimum is
inconclusive whatever the value says.

**Every formula in the spec is a formula, not a hint.** The weights, the
factor tables, the thresholds, and the mapping constants live in
`config/scoring_policy.yaml` and are read from there. Do not hardcode a number
in Python that the policy holds. The one exception is the log base and the
arithmetic itself.

**Do not write code nobody asked for.** No abstract base classes, no plugin
registry, no factory helpers, no `__all__` of things that do not exist. Six
modules, the types the spec names, the functions the spec names.

## The policy file

`config/scoring_policy.yaml` carries `policy_version: opportunity-v1` and the
blocks the spec's formulas read: `potential_weights`, `confidence_weights`,
`frequency_factors`, `wtp_factors`, `competition_factors`,
`competition_absent_factor_cap`, `market` (with the `niche` block),
`buildability`, `identity`, `confidence`, `concentration`,
`source_reliability_weights`, `lifecycle`, `experiment`.

A comment block at the top says where the numbers came from, in the style of
`config/research_contract.yaml`. The weights sum to one. The loader refuses a
policy whose weights do not sum to one, whose factor table names a member the
enum does not have, or whose thresholds are negative.

## The tests

Three files, matching the spec's test list. Plain `def test_*` functions, no
classes, `@pytest.mark.parametrize` for list-shaped cases.

`tests/test_opportunity_scoring.py` must cover:

- score determinism: the same inputs give the same numbers, twice
- dimension isolation: the two input models share no field; changing any
  confidence input leaves the potential bit-identical, and the other way round
- missing evidence: an empty evidence list produces a low confidence and does
  not crash
- low-confidence identity: authors below the identity threshold land in the
  uncertain count and never in the independent count
- concentration penalty: evidence from one community is penalised, evidence
  spread over three is not
- WTP tiers: every tier maps to its factor, and a tier above the signals
  behind it is refused
- niche override: a small market with strong severity and WTP is lifted, and
  the override records its reason
- market range: the potential range follows the market estimate's low and high
  assumption sets
- competition: the direction is explicit and required, no search forces
  neutral, zero competitors caps the factor
- model independence: no model is called and the numbers do not change

`tests/test_opportunity_lifecycle.py` must cover every legal edge, the illegal
edges, the requirement refusals (promotion without evidence, human review
without a counter-evidence search, validation without a human decision),
staleness, and resurrection.

`tests/test_opportunity_snapshot.py` must cover snapshot determinism and id
stability, the provenance content, the experiment conclusion rules, and the
rule that no experiment ships with an observed result it did not record.

## Before you finish

Run the new tests on their own, then the whole suite:

```text
uv run pytest tests/test_opportunity_scoring.py tests/test_opportunity_lifecycle.py tests/test_opportunity_snapshot.py
uv run pytest
```

Both must be green. Report the exact counts and the exit code.

## Report back

Return: the files you created with their line counts, the exact pytest output
lines for both runs, the weights you shipped, any place where you had to make a
judgment call the spec did not settle, and anything you skipped.

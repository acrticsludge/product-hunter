# Plan: wave 2 session 6, opportunity scoring and validation

**Spec:** `docs/reasonix/specs/wave-2-scoring-validation.md`
**Handoff:** `docs/reasonix/handoffs/wave-2-scoring-validation-handoff.md`
**Date:** 2026-10-21

## Architecture decisions

**A package, not a module.** The engine has six separable concerns: input
types, policy, computation, state machine, output record, experiment record.
One file would be six hundred lines of unrelated things. Six files of two
hundred lines each is easier to read, and the package boundary is where the
next wave's integration lands.

**No database migration.** The snapshot is a record with a content-addressed
id. The table that holds it belongs to the wave that owns the opportunity
entity, and `db/migrations/**` is a high-risk path in the adapter. Writing a
migration now would collide with the parallel evidence and discovery branches,
which are writing the tables this engine's inputs would come from.

**Reuse the `Frequency` enum.** `product_hunter.extraction` already defines
`daily, weekly, monthly, occasional, one_off, unknown`. A second enum in the
opportunity package would drift from it. Import it.

**The two input models are the isolation mechanism.** `PotentialInputs` and
`ConfidenceInputs` share no field. The scorers take one each. The separation
the mission demands is a type-level property, not a code-review promise.

**Interfaces, not engines.** The mission asks for a reachable-market
estimate interface and a competitive-gap interface. Both are input types with
a protocol-shaped role: the caller produces them from whatever real research
runs, and the scorer consumes them. This wave ships the types and the scorer,
not the search.

## File order

The order follows the dependency graph. Nothing imports from a module that
does not exist yet.

1. `src/product_hunter/opportunity/signals.py`
   Every input type and the independence computation. No dependencies beyond
   pydantic and the `Frequency` enum.

   - `SourceReliability` enum: `tier_a`, `tier_b`, `tier_c`, `tier_d`,
     `unrated`.
   - `EvidenceItem`: evidence_id, author_id, community_id, source_id,
     source_reliability, published_at, identity_confidence, is_canonical,
     propagation_confidence.
   - `PainObservation`: pain_id, severity 0-5, frequency.
   - `WtpTier` enum W0-W4, `WtpSignalKind` enum, `WtpSignal`,
     `WtpAssessment` with the tier-must-not-exceed-signal rule.
   - `MarketAssumptions` and `MarketEstimate` with the low/base/high
     assumption sets and the annual opportunity multiplication.
   - `CompetitionDirection` enum, `CompetitiveAssessment` with the required
     direction.
   - `BuildabilityAssessment`.
   - `TrendSignal` and `TrendWindow`.
   - `ExtractionAgreement`, `CounterEvidenceSummary`.
   - `PotentialInputs`, `ConfidenceInputs`.
   - `IndependenceSummary` and `compute_independence(evidence, policy)`.

2. `src/product_hunter/opportunity/policy.py`
   `ScoringPolicy` and the loader. The loader refuses weights that do not sum
   to one, factor tables that name an unknown enum member, and negative
   thresholds. Follows the `load_research_contract` pattern.

3. `config/scoring_policy.yaml`
   The shipped policy. The weights, the factor tables, the thresholds, the
   mapping constants, the lifecycle requirements, the experiment
   recommendations. A comment block states where each number came from.

4. `src/product_hunter/opportunity/scoring.py`
   `score_potential(inputs, policy)` and `score_confidence(inputs, policy)`,
   plus `OpportunityScore` holding both numbers, both ranges, the component
   scores, the provenance, the independence summary, the trend signal, and the
   niche override. The potential range comes from re-running the potential
   against the market estimate's low and high assumption sets.

5. `src/product_hunter/opportunity/lifecycle.py`
   `OpportunityState`, the transition table, `TransitionContext`,
   `TransitionDecision`, `evaluate_transition`, and `apply_transition`.

6. `src/product_hunter/opportunity/snapshot.py`
   `ScoreSnapshot`, `ScoreProvenance`, `ComponentScore`, and
   `build_snapshot(...)`. The snapshot id is a content hash of the canonical
   inputs and the policy version.

7. `src/product_hunter/opportunity/experiments.py`
   `Comparator`, `Threshold`, `ObservedResult`, `Conclusion`,
   `ValidationExperiment`, `conclude(experiment)`, and
   `recommend_experiment(...)`.

8. `src/product_hunter/opportunity/__init__.py`
   The public exports.

## Test files

- `tests/test_opportunity_scoring.py`: score determinism, dimension isolation,
  missing evidence, low-confidence identity, concentration penalty, WTP tiers,
  niche override, market range, competition direction and the absence rules,
  and model independence.
- `tests/test_opportunity_lifecycle.py`: every legal edge, the illegal edges,
  the requirement refusals, staleness, and resurrection.
- `tests/test_opportunity_snapshot.py`: snapshot determinism, provenance
  content, experiment conclusions, and the no-faked-results rule.

Plain `def test_*` functions, `@pytest.mark.parametrize` where the cases are a
list, no classes. The house style from `tests/conftest.py`.

## Gates

The code gate is `uv run pytest`, from `.loop/adapter.json`. The new tests run
on their own before the whole suite. No visual gate: no UI file is in the
radius.

## Documentation

`docs/reasonix/reports/wave-2-scoring-validation.md` records the assumptions,
the formulas, the test counts, the commit hash, and the integration
requirements. `docs/reasonix/ARCHITECTURE.md` gets the new package described
and its "opportunity states and scoring" line moved from `PLANNED` to
`IMPLEMENTED`.

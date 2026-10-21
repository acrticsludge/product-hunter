# Harden prompt: wave 2 session 6, opportunity scoring and validation

**Node:** stage 6 HARDEN
**Date:** 2026-10-21
**Model rung:** loop-free-harden

## Where you work

The worktree is `C:\Users\anubh\AppData\Local\Temp\opencode\wt-wave2-scoring`
(branch `wave2/scoring-validation`). READ-ONLY: report findings, change
nothing.

## What this run added

A new package `src/product_hunter/opportunity/` with six modules:

```text
signals.py      the input types and the independence computation
policy.py       ScoringPolicy, the loader, config/scoring_policy.yaml
scoring.py      score_potential(), score_confidence(), score_opportunity()
lifecycle.py    OpportunityState, the transition table, the requirement checks
snapshot.py     ScoreSnapshot, the snapshot id, the provenance
experiments.py  ValidationExperiment, the threshold, the conclusion
```

Plus `config/scoring_policy.yaml` and three test files. Nothing else in the
repo was touched.

## The threat model for this run

This is a pure library. It calls no model, reads no clock, touches no
database, opens no socket, and reads no file except the policy YAML the
caller names. So most of the classic surface does not exist. The surface that
does:

1. **Untrusted input.** The inputs come from a research pipeline that handles
   crawled and model-generated text. Severity, frequency, WTP tiers, market
   assumptions, evidence identities, counter-evidence counts, and observation
   timestamps all arrive from outside the library. Check that every one of
   them is validated at the boundary, that no field can carry a value that
   makes the arithmetic meaningless, and that a missing value fails closed
   rather than defaulting to something flattering.
2. **Numeric failure modes.** Division by zero, a zero or negative denominator,
   a NaN or an infinity, a value so large it overflows the log mapping, a
   timestamp far in the past or far in the future, an empty list where a
   denominator is expected. Go through every division and every logarithm in
   the six modules and say what happens at each boundary.
3. **Determinism as a security property.** The scores drive which opportunities
   a human looks at, so a score that changes between two runs of the same
   inputs is a correctness bug with a business consequence. Check that
   nothing depends on dict ordering, set iteration, the clock, the locale, a
   random seed, or the order of the input lists, and that the snapshot id is
   stable under a reordering of the evidence.
4. **Error messages.** The house pattern in this repo is that a violation
   detail may not carry free text from the evidence, because the detail ends
   up persisted. Check every message this package builds: does any of them
   embed a caller-supplied string, a URL, or a quote?
5. **Resource use.** `compute_independence` and `_reliability_mean` are O(n)
   over the evidence list, and `_observation_window` and `compute_trend` walk
   the list again. The scorer calls several of them. Is there a case where a
   large evidence list turns into quadratic work, and is the number of passes
   bounded? Report the count, not a guess.
6. **The policy file.** `config/scoring_policy.yaml` is read from disk and
   validated. Check the loader: does it fail closed on a malformed file, a
   file with unknown keys, a file with a weight map that does not sum to one, a
   factor table that names a value the enums do not have, and a negative
   threshold? Check that a policy file cannot smuggle a value that later
   divides by zero.

## The hard rules this run must not break

- Potential and Confidence are separate dimensions. Neither scorer may read
  the other's inputs, and no evidence-quality term may enter the potential.
- A mention count is not an independent population.
- The absence of competitors is not proof of opportunity.
- Trend is neither potential nor confidence.
- No experiment result may be asserted rather than recorded.

## What to report

For each finding: the file, the line, what breaks, and the smallest change
that fixes it. Rank them Must-fix, Should-fix, and Nice-to-have. If you find
nothing in a category, say so in one line rather than padding.

Quote the code. Do not fix anything. Do not edit any file.

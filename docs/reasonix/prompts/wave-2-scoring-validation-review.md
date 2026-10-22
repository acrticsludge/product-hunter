# Review prompt: wave 2 session 6, opportunity scoring and validation

**Node:** stage 7 REVIEW
**Date:** 2026-10-21
**Model rung:** loop-free-review-1

## Where you work

The worktree is `C:\Users\anubh\AppData\Local\Temp\opencode\wt-wave2-scoring`
(branch `wave2/scoring-validation`). READ-ONLY: your job is to find what is
wrong, not to fix it. Do not edit any file.

## What to review

Everything under `src/product_hunter/opportunity/` (seven modules), plus
`config/scoring_policy.yaml` and the three new test files. The whole change
is new code on a fresh branch, so read the files themselves rather than a
diff.

Read these first:

- `docs/reasonix/specs/wave-2-scoring-validation.md` — the design. The
  formulas, the transition table, the prohibitions, and the isolation rule.
- `docs/reasonix/handoffs/wave-2-scoring-validation-handoff.md` — the interop
  contract: what the package must reuse from the repo, and the list of inputs
  the repo cannot yet supply.

The mission this implements asked for a deterministic opportunity scoring and
validation framework, with these rules stated as absolute:

- Potential and Confidence are separate dimensions and must never be collapsed
  into one magical score.
- Trend is not Potential and not Confidence. Trend is its own signal.
- Evidence quality is not market potential.
- A mention count is not an independent population.
- The absence of competitors is not proof of opportunity.
- The competition direction must be explicit.
- No experiment result may be faked.

## How to review

Load the `ponytail-review` skill and use its six-pass rubric: correct, safe,
load, tested, fast, lean. Read the connected code, not only the new files: the
package imports `Frequency` from `product_hunter.extraction`, and the house
patterns live in `product_hunter/research/` and `product_hunter/config.py`.

Try to prove the code wrong. Concretely:

1. Can you find an input, or a sequence of inputs, that produces a number the
   package should not produce? Work the arithmetic, do not wave at it.
2. Can any evidence-quality term reach the potential, or any attractiveness
   term reach the confidence? Check the type boundary, and check that no
   helper smuggles one across.
3. Can two different runs over the same inputs produce different numbers? Look
   for float summation order, dict and set iteration, the clock, the input
   list order, and anything derived from an id.
4. Is there a path to a score that a caller can reach by lying in a way the
   package does not catch? The competition rules, the WTP tier rule, and the
   identity threshold are the gates; look for a way around each.
5. Can the transition table be walked into a state that should be impossible?
   Can a requirement be met with a value that does not mean what it claims?
6. Do the tests actually pin the behaviour, or do they restate the
   implementation? A test that would still pass if the rule were inverted is
   not a test. Name the rules that have no test.

Also check the repo's own rules, which are not negotiable: never change a test
to make it pass, never weaken an assertion, no new dependency, and nothing
outside the declared radius.

## What to report

Use the `ponytail-review` finding format: Must-fix, Should-fix, Nice-to-have,
each finding explained in plain English with the file and the line, and what
breaks if it is not fixed. A finding with no consequence is not a finding.

Finish with:

```text
Verdict: ship | fix first | fix 1, 2 first
Lean: -N lines possible
```

`Lean` is the number of lines that could be deleted with no loss of behaviour.
Count them from the code, do not guess. If nothing can go, write
`Lean: -0 lines possible` and say why in one line.

Say plainly what you checked and what you did not. If you did not read a
module, name it.

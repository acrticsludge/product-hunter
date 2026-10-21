# Wave 2 Session 6: opportunity scoring and validation, wave report

**RUN_ID:** wave2-20261021-scoring-validation-01
**Node:** PRODUCTIONIZE (stage 8)
**Date:** 2026-10-21
**Risk:** declared medium, floor medium
**Branch:** `wave2/scoring-validation`, branched from `main` at `3d7bc68`
**Spec:** [specs/wave-2-scoring-validation.md](../specs/wave-2-scoring-validation.md)
**Plan:** [plans/wave-2-scoring-validation.md](../plans/wave-2-scoring-validation.md)
**Handoff:** [handoffs/wave-2-scoring-validation-handoff.md](../handoffs/wave-2-scoring-validation-handoff.md)

## What this wave delivered

A deterministic library that scores one opportunity on two separate scales, plus
the evidence-quality computation underneath the second one, the state machine
around the result, the record that stores it, and the experiment record that
says what would settle the thesis.

Potential answers whether solving the problem is attractive. Confidence answers
whether the collected evidence supports the thesis. The spec's example is the
reason they stay apart: `Potential: 96, Confidence: 27%` and `Potential: 94,
Confidence: 91%` are not the same situation wearing different numbers.

The wave shipped seven modules, one policy file, three test files, and seven
documents.

| File | Lines |
|---|---|
| `src/product_hunter/opportunity/signals.py` | 535 |
| `src/product_hunter/opportunity/policy.py` | 376 |
| `src/product_hunter/opportunity/scoring.py` | 834 |
| `src/product_hunter/opportunity/lifecycle.py` | 361 |
| `src/product_hunter/opportunity/snapshot.py` | 247 |
| `src/product_hunter/opportunity/experiments.py` | 185 |
| `src/product_hunter/opportunity/__init__.py` | 136 |
| `config/scoring_policy.yaml` | 267 |
| `tests/test_opportunity_scoring.py` | 1184 |
| `tests/test_opportunity_lifecycle.py` | 569 |
| `tests/test_opportunity_snapshot.py` | 845 |

2 674 lines of source, 2 598 lines of test, 119 test functions over 9
parametrised cases.

## Gate evidence

The code gate is `uv run pytest`, from `.loop/adapter.json`.

```text
uv run pytest tests/test_opportunity_scoring.py tests/test_opportunity_lifecycle.py tests/test_opportunity_snapshot.py
268 passed in 6.22s
exit code 0

uv run pytest
1248 passed in 28.03s
exit code 0
```

The 268 is this wave's own count and the 1 248 is the whole suite. The suite
figure moved from 980 at the start of the wave to 1 248 at the end, and the
difference is this wave's tests. The gate was re-run after every fix round, so
the number above is the last state of the tree, not the first green run.

The reviewer measured the same two runs independently and reported 1 248
passing.

No visual gate. The radius contains no UI file, and `.loop/adapter.json` sets
`visualRequired` to `never`.

## The scoring assumptions

Every number below lives in `config/scoring_policy.yaml` under
`policy_version: opportunity-v1`, and the loader refuses a file whose weights
do not sum to one, whose factor table names an unknown enum member, whose
threshold is negative, or whose counter-evidence floor is zero.

### Potential

Six components, each scaled to 0-1, weighted into 0-100.

| Component | Weight | Factor |
|---|---|---|
| severity | 0.20 | severity / 5, the strongest pain observation |
| frequency | 0.15 | daily 1.0, weekly 0.85, monthly 0.6, occasional 0.35, one_off 0.1, unknown 0.0 |
| willingness to pay | 0.25 | W0 0.0, W1 0.25, W2 0.5, W3 0.75, W4 1.0 |
| market size | 0.20 | `log10(1 + annual_usd) / log10(1 + 100_000_000)`, clamped |
| competition | 0.10 | tailwind 1.0, neutral 0.55, headwind 0.1 |
| buildability | 0.10 | feasibility less 0.1 per blocker |

The market map is logarithmic on purpose. Annual opportunity spans five orders
of magnitude between a hobby project and a category, and a linear scale would
collapse everything under a million dollars into noise. The ceiling is the
policy's, so the mapping is versioned with everything else.

The market estimate is bottom-up and arrives resolved:
`reachable_population × problem_incidence × adoption_rate × annual_price_usd`,
with a low, a base and a high assumption set. The engine refuses an estimate
whose band is empty, and the potential range comes from re-running the
potential against the low and the high set.

The niche override is the answer to a small market that is still worth
building. Below a market factor of 0.35, with severity 4 or higher and a tier
of W3 or better, the market factor is lifted to 0.55 and the override records
its reason. The override is decided once, from the base assumption set, and
applies to the low and the high runs too, so the three runs stay ordered. The
consequence is worth stating plainly: while the override applies, the market
band no longer moves the potential, and the range collapses to the other five
components. The market estimate still carries the band for anyone who wants to
read it.

### Confidence

Six components, weighted into 0-100, then multiplied by the concentration
penalty.

| Component | Weight | Factor |
|---|---|---|
| independent people | 0.25 | effective population / 10, capped |
| community diversity | 0.15 | independent communities / 3, capped |
| source reliability | 0.20 | the reliability-weighted mean over the evidence |
| temporal persistence | 0.15 | the smaller of months / 6 and days / 180, zero when the newest evidence is stale |
| counter-evidence coverage | 0.15 | families covered / families required, times one minus the contradiction rate |
| extraction agreement | 0.10 | the agreement rate, discounted by the number of distinct extractors |

The concentration penalty comes from the larger of the community and source
Herfindahl indices. Above an index of 0.34 the penalty grows linearly to a
maximum of half the score, so one community carrying everything is not the same
claim as three communities carrying it.

The confidence range brackets the score by a margin built from the share of
uncertain identities and the extraction disagreement rate. The margin is a
fraction of the 0-100 scale, so the widest band the policy allows is 25 points
either side. A low score can carry a band that sits above it; a consumer that
ranks two opportunities ranks on the point estimate, not on the band.

### Evidence quality

The seven numbers from spec §12, computed in that order: raw mentions,
canonical observations, independent authors, uncertain authors, independent
communities, source concentration, and the propagation-adjusted count.

A propagated copy is not a canonical observation. The propagation confidence
says how strongly the item is believed to be a copy, which is the convention
`propagation_edges.confidence` already uses in the schema, where an edge's
confidence is zero when no propagation is believed. A copy believed with
ninety percent confidence of being a copy carries a tenth of the weight of an
original observation.

An author at or above the identity threshold of 0.7 counts as independent. One
below it is uncertain, and the uncertain count is reported beside the
independent count and never folded into it. The effective population, which is
what the score reads, is the sum of the identity confidence over the distinct
independent authors, each author counted once at their strongest confidence.

The trend compares the two halves of the observation window by rate, not by
count. A window split into unequal halves gives the longer half more mentions,
so a steady one-mention-a-month stream would read as rising over an odd number
of months. The rate comparison keeps it flat.

### The lifecycle

| From | To |
|---|---|
| CANDIDATE | VERIFYING, REJECTED |
| VERIFYING | PROMISING, INSUFFICIENT_EVIDENCE, REJECTED, ALREADY_SOLVED, STALE |
| PROMISING | HUMAN_REVIEW, ALREADY_SOLVED, REJECTED, INSUFFICIENT_EVIDENCE, STALE |
| HUMAN_REVIEW | VALIDATED, REJECTED, STALE |
| VALIDATED | STALE, DEAD |
| REJECTED | STALE, RESURRECTED |
| INSUFFICIENT_EVIDENCE | VERIFYING, STALE, RESURRECTED |
| STALE | VERIFYING, RESURRECTED |
| RESURRECTED | VERIFYING |
| ALREADY_SOLVED, DEAD | none |

ALREADY_SOLVED and DEAD are terminal. A rejected or stale opportunity comes
back when new evidence arrives, because the thesis was never disproved. A dead
opportunity is not coming back without a human reopening it, and the engine has
no state that says otherwise.

The requirements on each edge: VERIFYING to PROMISING needs the policy minimum
of independent people and the policy minimum confidence; PROMISING to
HUMAN_REVIEW needs a counter-evidence search that ran; HUMAN_REVIEW to
VALIDATED needs a recorded human decision; any edge to STALE needs evidence
past the staleness window or a human decision; any edge to RESURRECTED needs
new evidence and a reason; the four decision states need a reason.

The promotion gate reads the engine's own score rather than numbers the caller
asserts. A context with no score attached cannot promote anything, and the
independent-person count inside a score is already thresholded by the identity
policy.

## The prohibitions, and where each one is enforced

| Prohibition | Where it lives |
|---|---|
| Potential and Confidence collapsed into one score | two input models with no field in common; the two scorers take one each |
| Trend as Potential or as Confidence | `TrendSignal` is a field of the output, of neither input model |
| Evidence quality as market potential | `MarketEstimate` arrives resolved; the scorer never derives it from evidence |
| Mention count as independent population | the population counts distinct resolved authors; a test moves seven mentions of one author and watches the count stay at one |
| Absence of competitors as proof | a zero-competitor assessment is capped; an assessment with no search is forced to the neutral factor |
| One magical score | two numbers, two ranges, two provenance lists |
| A faked experiment result | the conclusion is computed; a result under the minimum sample size is inconclusive |

## What the review and the hardening pass found

Six review rounds ran. The verdicts and the findings are recorded here in the
order they came, because they are the reason the code looks the way it does.

### The first review: two Must-fix, seven Should-fix

The trend split the window into unequal halves, so a steady stream read as
rising over an odd number of months. The promotion gate was judged on numbers
the caller asserted, and a context claiming ten independent people and 99
confidence promoted an opportunity the engine itself rated at 16 with zero
independent people. Both were real and both were fixed.

The Should-fix items were a WTP tier unlockable by a signal naming no
evidence, a confidence range half a point wide, a snapshot id that did not move
when the policy changed without a version bump, a loader that refused a percent
threshold, non-finite numbers accepted where they broke the arithmetic, a
counter-evidence requirement the caller could declare down to one family, and
an experiment id that did not move when its template changed.

### The hardening pass: two Must-fix, one Should-fix

Evidence dated after the reference time received full persistence credit,
because a negative age is not "older than the staleness window". The policy
loader raised a raw `OSError` for a missing file instead of a `PolicyError`.
The snapshot id was not stable under every reordering of the input lists.

### The confirmation review: two more, both real

The staleness gate still read a caller-asserted date that could contradict the
score the same context carried, and the counter-evidence floor could be
switched off by a policy file that set it to zero.

### What was declared and not changed

The confirmation review raised a WTP signal with no evidence id at tier W0. It
stays refused. An unsourced signal is an unsourced claim, and this repo's rule
is that a claim without a traceable record is refused rather than stored. A
caller with no evidence does not record the signal, and a W0 assessment with no
signals constructs fine.

The review also noted that the confidence band can sit above a low score. That
is the documented consequence of the band being a fraction of the scale, not a
defect.

## Integration requirements

The engine is a library. It has no producer and no consumer in this repo yet,
and the gaps are the handoff's integration list.

1. **An opportunities table.** No table holds a score, a confidence, or a
   snapshot. The snapshot is a record with a content-addressed id, and the table
   that stores it belongs to the wave that owns the opportunity entity.
2. **Author identity confidence.** `author_links.confidence` exists and
   defaults to 1.0 for a declared link. A resolved cross-source identity
   confidence is what the independent-author count reads, and no column holds
   one today.
3. **Cluster membership.** No clusters table and no cluster-assignment writer.
   The pain observations the potential side reads have to come from somewhere.
4. **A market estimate producer.** `MarketEstimate` is an interface with a
   resolved assumption set behind it, and nothing in this repo builds one.
5. **A competitive assessment producer.** `CompetitiveAssessment` needs the
   competitor search to have run and the number of competitors it found. The
   contract has a `competitor_search` gate key with no persistence behind it.
6. **A source reliability tier.** `SourceReliability` names tier A to D and
   unrated. The registry carries the terms status, the collector, and the rate
   limit policy; it does not carry a reliability tier, and a tier without a
   named basis is not a tier.
7. **A counter-evidence summary producer.** The coverage count, the families
   required, and the contradiction count come from the counter-evidence stage.

## What this wave did not do

No web scraping, no discovery, no clustering, no Obsidian projection, and no
competitor research engine. The competitive assessment is an input here; the
search that produces it belongs to another wave.

No database migration. `db/migrations/**` is a high-risk path in
`.loop/adapter.json`, and the parallel evidence and discovery branches are
writing the tables this engine's inputs would come from. A migration written
now would collide with them.

No scoring against real evidence. The engine has only ever seen fixtures. No
weight has been fitted to a labelled outcome, so every weight in the policy is
a judgment call with a stated reason, not a measurement.

No Obsidian or dashboard output, and no way to consume the confidence band yet.

## The two path notes

The mission named the deliverable `docs/resonix/reports/wave-2-scoring-validation.md`.
This repo's documentation tree is `docs/reasonix/`, so the report is at
`docs/reasonix/reports/wave-2-scoring-validation.md`. A second tree with the
misspelling would have been a parallel directory nothing reads.

The commit hash is reported by the orchestrator at the end of the run. The
report is part of the commit, so the hash cannot be inside it.

## Not checked

The review did not read `product_hunter/research/contract.py` in full,
`product_hunter/config.py` in full, `product_hunter/extraction.py` beyond the
`Frequency` enum, the `db/` and `research/` modules, or the full specification
beyond the sections the wave spec paraphrases. The hardening pass did not
read the three test files line by line.

The runtime cost of the engine on a very large evidence list was not
benchmarked. The work is O(n) over the evidence in about five passes, none of
them quadratic, and the largest list the tests build is a few dozen items.

# Wave 2 Session 6: opportunity scoring and validation

**Status:** approved for implementation
**Date:** 2026-10-21
**Spec basis:** `docs/reasonix/specs/Product-Hunter-Full-Spec.md` sections 11, 12, 13, 20, 21, 23, 24, 26

## What this is

A deterministic scoring engine for one opportunity. It reads a fixed set of
inputs, produces two numbers that never touch each other, and explains how each
number was reached.

The engine is a library. It calls no model, reads no clock, and touches no
database. Everything it produces is a pure function of its arguments and the
versioned policy file.

## The two dimensions

Potential and Confidence answer different questions and are stored apart.

| | Potential | Confidence |
|---|---|---|
| Question | Is solving this attractive? | Do the collected evidence support the thesis? |
| Scale | 0-100 | 0-100 (a percentage) |
| Reads | severity, workflow frequency, WTP tier, market size, competition, buildability | independent people, community spread, source reliability, persistence, counter-evidence coverage, extraction agreement |
| Never reads | evidence quality, source reliability, independence, extraction agreement, confidence | severity, WTP, market size, competition, buildability |

The separation is structural, not a convention. `PotentialInputs` and
`ConfidenceInputs` are two models with no field in common, and the two scorer
functions take one each. A scorer cannot read what it was not given, so no
future edit can smuggle an evidence term into Potential without changing a
type. A test asserts the field sets are disjoint and that mutating one side
leaves the other side's output bit-identical.

Trend is a third thing. It is reported next to both numbers, computed from the
observation window, and is an input to neither.

## The prohibitions, and how each one is enforced

| Prohibition | Enforcement |
|---|---|
| Trend as Potential | `TrendSignal` is not a field of `PotentialInputs` nor of `ConfidenceInputs` |
| Trend as Confidence | same |
| Evidence quality as market potential | `MarketEstimate` arrives as a resolved assumption set; the scorer never derives it from evidence |
| Mention count as independent population | `independent_people` counts distinct resolved authors. A mention count is reported beside it and never used as a population |
| Absence of competitors as proof of opportunity | a zero-competitor assessment has its factor capped, and an assessment with no competitor search is forced to the neutral factor |
| One magical score | two numbers, two ranges, two provenance lists |

## Package layout

```text
src/product_hunter/opportunity/
  __init__.py      public exports
  policy.py        ScoringPolicy, the loader, config/scoring_policy.yaml
  signals.py       every input type, plus the independence computation
  scoring.py       score_potential(), score_confidence(), OpportunityScore
  lifecycle.py     OpportunityState, the transition table, transition checks
  snapshot.py      ScoreSnapshot, ScoreProvenance, the snapshot builder
  experiments.py   ValidationExperiment, Threshold, ObservedResult, conclusion
```

Six modules. The split follows the data flow: inputs, policy, computation,
state machine, output record, experiment record.

## Inputs

### The potential side

```text
PotentialInputs
  pain_observations: list[PainObservation]   # pain_id, severity 0-5, workflow frequency
  wtp: WtpAssessment                         # tier W0-W4 plus the signals behind it
  market: MarketEstimate                     # bottom-up assumption set
  competition: CompetitiveAssessment         # explicit direction
  buildability: BuildabilityAssessment       # feasibility 0-1 plus blockers
```

`PainObservation` carries the severity and the workflow frequency the extraction
stage already resolved. The engine does not re-read the evidence behind a pain
point, and it does not care how many pain points share the same span. Severity
for the opportunity is the maximum over the observations. Workflow frequency is
the mode over the observations, ties broken by the order daily, weekly, monthly,
occasional, one_off, unknown.

`WtpAssessment` holds a tier and the signals that support it. The declared tier
may not exceed the strongest signal present, so a run cannot claim W4 with only
a complaint behind it. A signal must also name the evidence behind it, so a
tier can never ride on a claim with no record under it.

`MarketEstimate` holds three assumption sets (low, base, high), each with a
reachable population, a problem incidence, an adoption range, and an annualised
price. The engine multiplies them:

```text
annual opportunity = reachable_population
                   × problem_incidence
                   × adoption_rate
                   × annual_price_usd
```

It never invents a figure it was not given. A missing assumption set is a
validation error, not a zero.

`CompetitiveAssessment` names a direction, and the direction is required. There
is no default, because a default is how an unexamined market becomes a
tailwind. The assessment also carries the number of competitors found, whether
a competitor search actually ran, and the evidence behind both.

`BuildabilityAssessment` carries a feasibility figure from 0 to 1 and the
blocking constraints that hold it back. Each blocker takes a fixed bite out of
the factor.

### The confidence side

```text
ConfidenceInputs
  evidence: list[EvidenceItem]
  extraction_agreement: ExtractionAgreement
  counter_evidence: CounterEvidenceSummary
```

`EvidenceItem` carries an evidence id, an author id, a community id, a source
id, a source reliability tier, the publication date, the identity confidence of
the author record, whether the item is a canonical observation or a propagated
copy, and the propagation confidence when it is a copy.

`ExtractionAgreement` carries how many extractions agreed with the consensus,
how many disagreed, and how many distinct extractors ran.

`CounterEvidenceSummary` carries whether a counter-evidence search ran, how many
query families it covered against how many were required, how many
contradicting items it found, and how many contradicting items were checked.
The requirement is floored by the policy, so a caller can ask for more
coverage than the policy wants but never for less.

## The independence computation

The seven numbers from spec section 12, computed in that order:

```text
raw mention count
canonical-content count
independent author count
uncertain-author count
independent community count
source concentration
propagation-adjusted evidence count
```

Rules:

- An author whose identity confidence sits at or above the policy threshold
  counts as independent. One below it is uncertain, and the uncertain count is
  reported beside the independent count and never folded into it.
- A propagated copy is not a canonical observation. It never adds to the
  canonical count, and the propagation confidence discounts the copy's weight
  in the source-reliability mix. The propagation confidence is how strongly
  the item is believed to BE a copy, which is the convention the
  `propagation_edges` table already uses, where an edge's confidence is zero
  when no propagation is believed. A copy believed with ninety percent
  confidence of being a copy carries a tenth of the weight of an original
  observation.
- The effective population is the sum of the identity confidence over the
  distinct independent authors, each author counted once at their strongest
  confidence. This is the number Confidence uses. The headcount stays separate.
- Concentration is the Herfindahl index over the community shares and over the
  source shares. The larger of the two is the one that matters.

A count without a window is not a number, so the summary carries the observed
window: the first and last publication date, and the number of distinct months
the canonical evidence touches.

The trend compares the two halves of that window by rate, not by count. A
window split into unequal halves gives the longer half more mentions, so a
steady one-mention-a-month stream would read as rising over an odd number of
months. The rate comparison is what keeps a steady stream flat.

## Potential

Six components, each 0 to 1, combined with the policy weights into 0 to 100.

| Component | Factor |
|---|---|
| Severity | severity / 5 |
| Workflow frequency | daily 1.0, weekly 0.85, monthly 0.6, occasional 0.35, one_off 0.1, unknown 0.0 |
| Willingness to pay | W0 0.0, W1 0.25, W2 0.5, W3 0.75, W4 1.0 |
| Market size | `log10(1 + annual_usd) / log10(1 + ceiling_usd)`, clamped to 0-1 |
| Competition | tailwind 1.0, neutral 0.55, headwind 0.1, then the absence cap |
| Buildability | feasibility, less the blocker penalty |

The market mapping is logarithmic on purpose. Annual opportunity spans five
orders of magnitude between a hobby project and a category, and a linear scale
would collapse everything under a million dollars into noise. The ceiling is
the policy's, so the mapping is versioned with everything else.

### The competition rules

The direction is explicit, always. Two rules then narrow it:

- A competitor search that did not run leaves the competitive position unknown,
  so the factor is forced to the neutral value. The declared direction is kept
  in the record and the override is reported.
- Zero competitors found caps the factor at the policy cap. An empty search
  result is evidence about the search, not proof that the market is open, and
  the cap is what stops it from reading as proof.

An assessment that names competitors must have run a search. Without that
rule a caller could list the competitors it knows about and still claim that
no search ran, and the neutral factor would then overstate the opportunity.

### The niche override

A small reachable market can still be the right product, so a market-size floor
is not applied. When the market factor falls below the policy ceiling while
severity and WTP are both strong, the market factor is lifted to the policy
floor and the override is recorded with its reason, the severity, the WTP tier,
and the market value that triggered it.

The override never touches the market estimate itself. The estimate stays what
the assumptions say; only the scoring factor moves.

The override is decided once, from the base assumption set, and it is a property
of the opportunity rather than of an assumption set. When it applies, the
market factor in the low and the high runs is the larger of the raw factor and
the floor, so the three runs stay ordered. A consequence worth stating: while
the override applies, the market band no longer moves the potential, and the
range collapses to the other five components. That is the truth of the model,
and the market estimate still carries the band for anyone who wants to read
it.

### The plausible range

The range comes from the market estimate's own uncertainty. The engine computes
the potential three times, once with the low assumption set, once with the base
set, once with the high set, and reports all three. The other five components
do not change between the three runs.

A market estimate with no uncertainty band is a refused input, not a point
estimate.

## Confidence

Six components, each 0 to 1, combined with the policy weights into 0 to 100,
then multiplied by the concentration penalty.

| Component | Factor |
|---|---|
| Independent people | effective population / target independent people, capped at 1 |
| Community diversity | distinct communities / target communities, capped at 1 |
| Source reliability | the reliability-weighted mean over the canonical evidence |
| Temporal persistence | the smaller of months / target months and span days / target span days, zero when the newest evidence is stale |
| Counter-evidence coverage | families covered / families required, times one minus the contradiction rate |
| Extraction agreement | agreements / total extractions, times a small-sample discount |

The contradiction rate divides contradicting items found by contradicting items
checked. A check that found nothing contradicting cannot lower the score, and a
check that never ran contributes nothing to it.

The concentration penalty comes from the larger of the community and source
Herfindahl indices. Above the neutral index the penalty grows linearly to the
policy maximum. One community carrying everything is not the same claim as three
communities carrying it.

The confidence range brackets the score by a margin built from the share of
uncertain identities and the extraction disagreement rate. The score is the
point estimate; the range is how much of it the identities and the extractors
are entitled to. The margin is a fraction of the 0-100 scale, not a fraction
of the score, so the widest band the policy allows is a quarter of the scale.

The band is a bound, not a second score. A low score can carry a band that
sits above it, and a consumer that ranks two opportunities must rank on the
point estimate.

## Snapshots and provenance

Every score is stored as a snapshot. A snapshot carries the policy version, the
ids of the input snapshots it was computed from, both numbers, both ranges, the
component scores, the provenance list, the independence summary, the WTP tier,
the market estimate, the competition direction, the trend signal, and the
niche override when one applied.

The snapshot id is a content hash of the canonical inputs, the policy, the
reference time, and the input snapshot ids. The reference time is in the hash
because the confidence score depends on it: the same evidence scored on two
different days is two different scores. The policy goes in by its body and not
only by its version string, so a policy edited without a version bump cannot
serve a stale id. The same inputs, the same policy, and the same reference
time therefore produce the same id, and the same id names the same record,
which is what makes a snapshot diffable and a replay checkable. The id is
deliberately coarser than the score it names: it moves for a policy edit that
cannot move the score.

Provenance records one row per dimension: the factor, the weight, the
contribution, a short basis in words, and the evidence ids that moved it. A
dimension with no evidence behind it says so in its basis rather than borrowing
one from another dimension.

## The lifecycle

Opportunity state is separate from pipeline stage and from research-run state.

```text
CANDIDATE
VERIFYING
PROMISING
HUMAN_REVIEW
VALIDATED
REJECTED
ALREADY_SOLVED
INSUFFICIENT_EVIDENCE
STALE
DEAD
RESURRECTED
```

The allowed edges:

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
| DEAD | none |

DEAD is terminal. A rejected or stale opportunity can come back when new
evidence arrives, because the thesis was never disproved. A dead opportunity
is not coming back without a human reopening it, and the engine has no state
that says otherwise.

A legal edge can still be refused by its requirement. The requirements are:

| Edge | Requirement |
|---|---|
| VERIFYING to PROMISING | at least the policy minimum of independent people, and the policy minimum confidence |
| PROMISING to HUMAN_REVIEW | a counter-evidence search that ran |
| HUMAN_REVIEW to VALIDATED | a recorded human decision |
| any to STALE | the evidence is older than the policy staleness window, or a human decision says so |
| any to RESURRECTED | new evidence since the previous state, and a reason |
| any to REJECTED, ALREADY_SOLVED, INSUFFICIENT_EVIDENCE, DEAD | a reason |

An edge that is not in the table is refused as an illegal transition, whatever
the reason given. A legal edge whose requirement is unmet is refused with the
requirement's own reason code, so the caller can tell a wrong request from a
premature one.

The promotion gate reads the engine's own score rather than numbers the
caller asserts. The count of independent people inside that score is already
thresholded by the identity policy, and a promotion with no score at all is
refused. The same rule covers the staleness edge: when a score is attached,
the age of the evidence comes from the window that score computed, so the
caller cannot assert a fresher date than the engine's own evidence supports.

The state machine never reads the clock. A transition carries the context it was
judged against.

## Validation experiments

An experiment is a record, not a result.

```text
hypothesis          what the run believes and would be wrong about
method              how the belief would be tested
success_threshold   the comparison that would settle it
observed_result     the measurement, recorded after the method ran
conclusion          derived from the observed result
```

The observed result is optional and starts absent. It carries a value, a
sample size, when it was taken, who took it, and the evidence behind it.

The conclusion is computed, never asserted. With no observed result the
experiment is pending. With one, the conclusion follows from the comparison
against the threshold. A result whose sample size falls under the policy
minimum is inconclusive, whatever its value says, because a measurement of
nothing settles nothing.

The engine also recommends an experiment for an opportunity that has none, from
the WTP tier, the confidence, and the independent population. The
recommendation carries a hypothesis, a method, and a threshold drawn from the
policy. It never carries an observed result, because nothing has been run.

## Policy

`config/scoring_policy.yaml` holds the weights, the factor tables, the
thresholds, the mapping constants, and the lifecycle requirements. The file
carries a version string, and every snapshot records the version it was scored
under. Changing a weight changes the version, so two snapshots are only
comparable when their versions match.

The loader refuses a policy whose weights do not sum to one, whose factor
tables name a value the enums do not have, or whose thresholds are negative.

## What this does not do

No web scraping, no discovery, no clustering, no Obsidian projection, and no
competitor research engine. The competitive assessment is an input here; the
search that produces it is another wave's work.

No database migration in this session. The snapshot is a record with a stable
id, and the table that holds it belongs to the wave that owns the opportunity
entity.

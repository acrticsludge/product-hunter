# Final Audit 08: The Deep Investigation System

Run: final-audit-08, probe 8 of the final audit series
Date: 2026-10-22
Target: `main` at `00a6ca3` ("fix: close the second review round"), tree clean
Adapter: Product Hunter (`repo`), 8 forbidden rules in force
Risk declared: medium (floor medium)
Mode: READ-ONLY. No file in the repository was modified. The four verification scripts live in `%TEMP%\opencode\`, outside the repo.
Baseline: `uv run pytest` → **2138 passed in 43.55s** (code gate green on `main` as it stands)
Report path: the task named `docs/resonix/reports/final-audit-08-investigation.md`. Every worktree in the repository, and every prior report, uses `docs/reasonix/`, and the adapter's `docsRoot` is `docs/`. The file is written to `docs/reasonix/reports/final-audit-08-investigation.md`; the `resonix` spelling would have created a stray directory.

---

## 1. Scope and method

### 1.1 The eight Wave 3 worktrees

The task said to discover these rather than assume them. `git worktree list` returns eight
Wave 3 worktrees, and each maps to one branch:

| Branch | Worktree path |
|---|---|
| `wave3/autonomous-operation` | `C:\Anubhav\Web Dev Projects\product-hunter-w3a` |
| `wave3/source-expansion` | `C:\Anubhav\Web Dev Projects\product-hunter-w3c` |
| `wave3/duckdb-analytics` | `C:\Anubhav\Web Dev Projects\product-hunter-wt\wave3-analytics` |
| `wave3/observability-evaluation` | `C:\Anubhav\Web Dev Projects\product-hunter-wt\wave3-observability` |
| `wave3/scraping-ladder` | `C:\Anubhav\Web Dev Projects\product-hunter-wt\wave3-scraping-ladder` |
| `wave3/opencode-control-plane` | `C:\Anubhav\Web Dev Projects\product-hunter\.worktrees\wave3-opencode-control-plane` |
| `wave3/subq-integration` | `C:\Users\anubh\.local\share\opencode\worktree\44767c\wave3\subq-integration` |
| `wave3/qdrant-semantic` | `C:\Users\anubh\AppData\Local\Temp\opencode\wt-wave3-qdrant` |

**Not one of them touches the investigation system.** `git diff --name-only main...<branch> --
src/product_hunter/research/investigation/` returns empty for all eight. The investigation
code is a wave 2 artefact and the Wave 3 branches leave it alone.

`git diff main integration/wave2 -- src/product_hunter/research/investigation/` is also empty:
the two branches hold byte-identical copies. So the audit target is unambiguous: the
investigation system on `main`.

### 1.2 What was read

All 13 modules under `src/product_hunter/research/investigation/` (3,375 lines), plus
`src/product_hunter/research/{gate,predicates,query_plan,contract,evidence}.py`,
`src/product_hunter/opportunity/{lifecycle,scoring,policy,signals}.py`,
`src/product_hunter/runtime/{worker,__main__}.py`, `src/product_hunter/db/{scheduler,research_gate}.py`,
`config/research_contract.yaml`, `config/sources.yaml`, `db/migrations/*.sql`,
the 11 investigation test files, and the wave-2 investigation spec and handoff.

### 1.3 How claims were verified

Reading a rule and confirming it exists is not evidence that it holds. Every finding below
that can be executed was executed: a script imported the package, drove `investigate()` with
a fake executor, and printed the result. The scripts are in `%TEMP%\opencode\audit08_verify*.py`.
Outputs quoted in this report are copied from those runs.

---

## 2. The headline answer

> **Verify that promising opportunities trigger structured investigation rather than simply
> receiving a higher LLM score.**

**They receive a higher score. Nothing triggers an investigation.**

The deep investigation system is a library with no caller.

- `investigate()` is defined at `src/product_hunter/research/investigation/pipeline.py:1292`.
  The only call sites in the entire repository are in
  `tests/test_research_investigation_pipeline.py`.
- No production code constructs an `InvestigationRequest`. The class is defined at
  `pipeline.py:232` and instantiated nowhere outside tests.
- No `ResearchExecutor` implementation exists outside tests. The protocol is at
  `pipeline.py:204`; the only implementations are fakes in the test file.
- `src/product_hunter/db/scheduler.py:543-550` defines a `deep_investigation` schedule with
  `stage="INVESTIGATE"`. It creates jobs. Nothing consumes them: `grep -r "INVESTIGATE" src/`
  returns exactly one hit, the schedule definition itself.
- The worker dispatches on the job's stage, `self.handlers.get(job.stage)`
  (`src/product_hunter/runtime/worker.py:160`), from a mapping the operator supplies through
  `PH_WORKER_HANDLERS` (`src/product_hunter/runtime/__main__.py:41`). No handler in this
  repository handles `INVESTIGATE`.
- `db/migrations/` has no `investigations` table, no `investigation_claims` table and no
  `opportunities` table. `research_claims` (migration `0003`) exists but nothing writes to it
  from the investigation path.
- `src/product_hunter/opportunity/` contains no reference to "investigation" in any form.
  Nothing maps a score or a state to an investigation.

The master spec is explicit about what should happen. `docs/reasonix/specs/Product-Hunter-Full-Spec.md`
places "deep investigation" after "score + confidence" in the stage pipeline (line 669),
§21 opens with "Every promising opportunity must have a deliberate attempt to kill it"
(line 1514), and §4 names an `investigator` agent whose job is to "Deeply investigate promising
clusters" (line 190). The wave 2 spec scoped the wiring out deliberately: a live search
backend is listed under "Non-goals" (`docs/reasonix/specs/wave-2-investigation.md:40`) and
"Model routing for the new stages" is a non-goal too (line 45).

So the gap is known and scoped. What was never scoped is the handoff: nothing in Wave 2 or
Wave 3 picked it up, and the wave 2 handoff records the engine as delivered without naming
the missing caller.

---

## 3. Findings

Severity: **Must** = the invariant is broken or the stage cannot ship; **Should** = the rule
is weaker than the invariant it claims to enforce; **Nice** = hygiene.

### MUST-1: The investigation stage can never pass the shipped contract

Two systems define "query family" and they share a stage name without sharing a vocabulary.

Wave 1 owns ten families: `direct_pain`, `manual_workflow`, `workaround`, `alternative_to`,
`failed_solution`, `feature_request`, `switching_pain`, `cost_consequence`, `role_workflow`,
`trigger_incident` (`src/product_hunter/research/query_plan.py:21-45`). The gate binds the
`investigation` stage to three of them (`query_plan.py:100-104`):

```python
    "investigation": (
        FAMILY_DIRECT_PAIN,
        FAMILY_FAILED_SOLUTION,
        FAMILY_ALTERNATIVE_TO,
    ),
```

Wave 2 owns a disjoint set: `competitor_alternative`, `competitor_existing_solution`,
`competitor_software`, `competitor_pricing`, `counter_not_worth`, `counter_complaints`,
`counter_workaround`, the `area_*` and `killer_*` families
(`src/product_hunter/research/investigation/queries.py:19-48`). The pipeline writes those
names onto the tool call (`pipeline.py:479` sets `step.family`, `pipeline.py:529` sets
`query_family=step.family`).

The shipped contract asks that stage to run both wave-1 quality checks
(`config/research_contract.yaml:114-129`):

```yaml
  investigation:
    required_tools: [search, qdrant, duckdb]
    required_outputs: [competitors, alternatives, counter_evidence,
                       opportunity_score, confidence, candidate_url]
    quality_checks: [mandatory_query_families, known_query_families]
    gate_key: competitor_search
```

Executed against a wave-2 investigation execution:

```
tool_call families    : ['area_alternative', 'area_existing_solution', 'area_workaround',
                          'competitor_alternative', 'competitor_existing_solution', ...]
mandatory_query_families -> (False, 'the mandatory query families were not searched:
                                       direct_pain, failed_solution, alternative_to')
known_query_families     -> (False, '23 search calls name a query family the worker does not own')
```

Both fail. Because `served_gate_keys` (`src/product_hunter/research/contract.py`) maps
`competitor_search` to the `investigation` stage, and `hard_requirements.require_competitor_search`
plus `promotion.require_competitor_gate` are both `true` in the shipped YAML
(`config/research_contract.yaml:134`, `:232`), **the run can never be promoted**. The
promotion gate is blocked by a stage whose failure is a vocabulary collision, not a research
failure.

Executed end to end against the shipped contract, with only the investigation execution
supplied:

```
served gates    : {..., 'competitor_search': 'investigation'}
investigation stage passed: False
    quality_check_failed | quality check 'known_query_families' failed in stage 'investigation':
                            23 search calls name a query family the worker does not own
    quality_check_failed | quality check 'mandatory_query_families' failed in stage 'investigation':
                            the mandatory query families were not searched: direct_pain,
                            failed_solution, alternative_to
    tool_missing          | required tool 'duckdb' never ran successfully in stage 'investigation'
    tool_missing          | required tool 'qdrant' never ran successfully in stage 'investigation'
    artifact_missing      | required output 'competitors' has no artifact in stage 'investigation'
    ... (six artifact_missing violations)
promotion.promoted: False
    promotion_blocked | promotion is blocked: the competitor_search gate did not pass
    promotion_blocked | promotion is blocked: required stage 'investigation' did not pass
    promotion_blocked | promotion is blocked: stage 'investigation' failed a quality predicate:
                            mandatory_query_families
```

The `tool_missing` and `artifact_missing` violations are the ones the spec already
documents. The two `quality_check_failed` violations are not, and they are the ones that
survive every attempt to satisfy the stage by adding tools or outputs.

One of the `artifact_missing` violations deserves more attention than the spec gives it.
The `counter_evidence` artifact is emitted only when the run found counter-evidence
(`pipeline.py:1471-1477`), so an investigation that finds nothing, which is the desirable
outcome, produces no artifact and fails `required_outputs`. The contract's own threshold
wants the zero accepted (`min_counter_evidence_items_or_explicit_zero: 1`,
`config/research_contract.yaml:158`) and `explicit_zero_outcome` honours it, but
`required_outputs` has no zero exception. The run above recorded a legitimate zero and was
still marked down for the missing artifact.

Probe 03 of this audit series reached the same conclusion about this block from the
contract side (`docs/reasonix/reports/final-audit-03-research-contract.md`, F-03), and
identified the artifact and tool defects. It did not identify the family-vocabulary
collision, which is why it is worth stating separately: fixing the artifacts and the tools
would leave the two `quality_check_failed` violations in place.

The wave-2 spec's known-limit 6 lists `tool_missing` for `qdrant`/`duckdb` and
`artifact_missing` for `opportunity_score`/`confidence`. It does not mention these two
quality-check failures. They are undocumented.

Fix: Pick one vocabulary. The cheaper direction is to make wave 2 emit wave-1 family
names: add `query_family` translation in `_tool_call` (`pipeline.py:509-530`) so each wave-2
family maps to the wave-1 family it serves, and emit the three mandatory wave-1 families
(`direct_pain`, `failed_solution`, `alternative_to`) as additional plan steps in the
competitor and counter-evidence areas. The alternative is to rename the wave-2 stage to
`deep_investigation` and give it its own gate key, which then requires a new
`MANDATORY_FAMILIES_BY_STAGE` entry and a new `evidence_thresholds` block. Either way the
two registries must agree, and a test must assert the investigation stage's emitted families
are a subset of `known_families()`.

### MUST-2: Weak counter-evidence invalidates the problem

`CounterEvidenceItem.strength` is declared at
`src/product_hunter/research/investigation/counter_evidence.py:52`:

```python
    strength: float = Field(default=1.0, ge=0.0, le=1.0)
```

`grep -r "strength" src/` shows this declaration is the only occurrence inside the
investigation package. Nothing reads it, anywhere.

The consequence sits in `_aggregate` (`pipeline.py:986-990`):

```python
    elif (
        killer.verdict is StartupKillerVerdict.falsified
        or counter_result.status is CounterEvidenceStatus.found
    ):
        outcome = InvestigationOutcome.contradicted
```

`CounterEvidenceStatus.found` means "at least one item exists", regardless of its strength.
Executed, with a single item at each extreme:

```
[weakest allowed] strength=0.0  counter.status=found  outcome=contradicted  next=REJECTED
[default       ] strength=1.0  counter.status=found  outcome=contradicted  next=REJECTED
```

Identical. One forum post saying "this does not matter" rejects the opportunity as firmly as
a market-wide saturation finding.

The spec's §21 list of counter-evidence kinds is a list of *kinds*, not of weights, and the
wave-2 spec never mentions strength. The rule the audit was asked to check, that weak
counter-evidence does not automatically invalidate a problem, is not implemented anywhere.

Fix: Give `assess_counter_evidence` a strength floor. `CounterEvidenceResult` needs a
minimum total strength for `found`, and a result whose items all fall under that floor should
read `incomplete` or a new `weak` status rather than `found`. A defensible floor is
`policy.confidence.min_counter_evidence_families`-shaped: require the items to come from at
least two distinct families, or to carry a total strength above a configured bar, before they
can move an opportunity to `REJECTED`. The floor belongs in `config/scoring_policy.yaml`
alongside the other thresholds, not hardcoded in the pipeline.

### MUST-3: Contradictions are not preserved in the claim set

`InvestigationClaim.contradicting_evidence` is declared at
`src/product_hunter/research/investigation/claims.py:70` and read in two places: the
reconciliation check (`claims.py:77`) and the provenance check (`claims.py:96`).

`_build_claims` (`pipeline.py:1062-1223`) never passes it. Every claim it builds carries
`supporting_evidence` and an empty `contradicting_evidence`. So when a counter-evidence
search finds something, the run rejects the opportunity and the claim set says the opposite:

```
counter_evidence.status : found
outcome                 : contradicted -> next: REJECTED
claims                  : [('no_strong_competitor', 'supported'), ('opportunity_exists', 'supported'),
                            ('problem_exists', 'supported'), ('problem_recurs', 'supported'),
                            ('problem_is_severe', 'supported'), ('market_is_large', 'supported'),
                            ('competitors_exist', 'supported'), ... ]
any claim contradicting  : []
any claim 'contradicted': []
```

Thirteen claims, all `supported`, on a run the same object calls `contradicted`. The
reconciliation machinery (`_reconciles`, `claims.py:57-62`) and the `softened` branch
(`claims.py:81-89`) are unreachable from the pipeline. `tests/test_research_investigation_claims.py:144`
exercises reconciliation by constructing a claim by hand, so the code path has coverage; the
pipeline just never uses it.

This is the difference between a system that records why it rejected something and one that
rejects on evidence nobody can later read.

The persistence path makes it worse. `record_claim`
(`src/product_hunter/db/research_gate.py:550-599`) writes a claim to `research_claims`
(migration `0003`) with `claim_id`, `kind`, `statement`, `stage` and `evidence_refs_json`.
It stores no status, no supporting evidence, no contradicting evidence and no provenance.
So even once the pipeline populates `contradicting_evidence`, the persisted row drops it.

Fix: When `counter_result.status is found`, build a claim with `contradicting_evidence` set
to the items, and let `derive_claim_status` derive `contradicted` rather than asserting
`supported`. The `_claim` helper at `pipeline.py:1072-1088` hardcodes
`status=ClaimStatus.supported`; it needs to derive the status from the evidence it is given,
the way `claims.py:40-51` already knows how to do. The claim table needs a column for the
status and for the contradicting evidence, or a second table for the claim's evidence.

### MUST-4: A zero from a search that returned nothing is treated as absence

The coverage rule applies its document and domain floors to the whole area rather than to
each family (`src/product_hunter/research/investigation/coverage.py:96-147`). A family whose
call succeeded but which returned zero documents still counts as `completed`.

Executed: two of the three mandatory counter-evidence families return zero documents; the
third returns two documents from two domains.

```
counter_evidence.status  : none_found
coverage.status          : complete
coverage.documents       : 2
coverage.distinct_domains: ['a.example.com', 'b.example.com']
coverage.distinct_queries: 3
coverage.completed       : ['counter_not_worth', 'counter_complaints', 'counter_workaround']
outcome                  : supported -> next: HUMAN_REVIEW
claims                   : [... 'no_counter_evidence_found' ...]
zero_result.recorded     : True
explicit_zero_outcome    : (True, 'a zero-result outcome completed every required query family')
```

The system published "no counter-evidence found", moved the opportunity to `HUMAN_REVIEW`,
and recorded a zero that the wave-1 gate accepted as legitimate. Two of three mandatory
counter-evidence searches returned literally nothing.

The wave-2 spec's known-limit 11 says "the floors bind the run, not each area" and argues
this is acceptable. The argument covers the *query* floor. It does not cover the document and
domain floors, which is where the absence claim actually rests: the rule that decides
"nothing was found" needs evidence that the families that found nothing were actually
searched against a real corpus.

Fix: Add a per-family floor to the coverage assessment. `assess_coverage` needs a
per-family minimum document count, or the counter-evidence and competitor assessments need
their own per-family check: a required family that returned zero documents cannot count
toward the area's document or domain floor. This is the difference between "we searched and
found nothing" and "we did not search".

### MUST-5: An item can change the verdict and never reach a claim

`_stamped` (`pipeline.py:566-577`) fills in a `tool_call_id` only when the item has none:

```python
    if item.tool_call_id:
        return item
    return item.model_copy(update={"tool_call_id": tool_call_id})
```

If the executor supplies an id that is not one of the pipeline's own calls, the item keeps
it. The provenance cannot resolve it, so `_finding_evidence` (`pipeline.py:1034-1059`) and the
competitor branch (`pipeline.py:1130-1158`) skip the item. But `run.counter_items`,
`run.competitors` and `run.findings` still drive the analysis and the outcome.

Executed, with one counter-evidence item stamped `call-that-never-happened`:

```
counter_evidence.status : found
outcome                 : contradicted -> next: REJECTED
any claim carrying the counter-evidence? False
```

The opportunity is rejected and no claim names the evidence that did it. The pipeline has no
validation that rejects a foreign call id; it silently loses the item from the record while
keeping its effect on the verdict. The same path applies to competitors.

Fix: Validate the executor's `tool_call_id` against the attempt it came from. An item
whose `tool_call_id` is not the call the pipeline made for that step should raise, not be
silently retained. If the pipeline prefers to be lenient, the item must at least be excluded
from the analyses that drive the outcome, so what drives the verdict is always what the
claims show.

### SHOULD-1: Supporting evidence is counted more than once

`_finding_evidence` (`pipeline.py:1034-1059`) emits one `ClaimEvidence` per finding and never
deduplicates. Findings produced by the same call share that call's id. `derive_claim_status`
(`claims.py:40-51`) sums `item.weight` across the supporting set, and
`InvestigationClaim._check_invariants` copies those refs into `evidence_refs` verbatim
(`claims.py:99-102`).

Executed, with three findings returned by one search call:

```
claim AUD-1-willingness_to_pay-3  kind=willingness_to_pay  status=supported
   supporting refs : 69 entries
   DISTINCT refs   : 23
   sum of weights  : 69.0
   evidence_refs   : 69 entries, with duplicates
```

One search call, three findings, three "pieces of evidence". The weight sum reads 69 for
what is really 23 distinct observations, and `evidence_refs` carries the duplicates into
every downstream consumer.

Today the duplicate counting does not flip a verdict: the only threshold in
`derive_claim_status` is `sum > 0`, and a duplicate has the same weight as its original, so
the status is the same either way. The defect is latent, not active, and `weight` being
free to be `0.0` means the sum is already the wrong shape for a rule that wants to compare
magnitudes. No test asserts uniqueness.

Fix: Deduplicate by `ref` when building `ClaimEvidence`, and validate uniqueness in
`InvestigationClaim._check_invariants` so a hand-built claim cannot carry the same ref
twice.

### SHOULD-2: The startup killer's disproof is not attributable to a source

`DimensionAssessment` (`src/product_hunter/research/investigation/startup_killer.py:96-135`)
has no field for the disproof item itself. A `falsified` dimension requires `attempts` and a
non-empty `reason` (`startup_killer.py:129-134`), and the reason is a fixed string the
pipeline generates ("the search for this dimension returned a disproof",
`pipeline.py:859`). `evidence_refs` are built from the attempt's tool call id
(`pipeline.py:873-877`), so they point at the search, not at the disproof.

Executed, with a counter-evidence item that has no `source_url` and no `query_family`:

```
dimension.status        : falsified
dimension.reason        : the search for this dimension returned a disproof
dimension.evidence_refs : ['call-killer_market_size-19']   <- the search call
disproof.query_family   : None                              <- unset, unattachable
counter_evidence items  : [('ce-1', None, None)]
```

The dimension is falsified on the strength of an executor assertion with no source, and
nothing in the report ties the disproof to the dimension or to a document. `query_family` on
`CounterEvidenceItem` exists for exactly this purpose and the pipeline never populates it.

This is the "startup killer cannot fabricate market facts" invariant, and it does not hold.
The pipeline does not fabricate anything itself; it accepts a fabrication from the executor
and publishes it as a dimension verdict.

Fix: Require `source_url` or a resolvable `tool_call_id` on a counter-evidence item that
falsifies a dimension, stamp `query_family` from the step's family when the item is
collected (`pipeline.py:779-780`), and add the disproof's ref to the dimension's
`evidence_refs` so the reader can trace the verdict to the document that produced it.

### SHOULD-3: Two opportunity state machines, and they disagree

`src/product_hunter/research/investigation/opportunity.py:62-86` defines `_TRANSITIONS`.
`src/product_hunter/opportunity/lifecycle.py:41-90` defines `ALLOWED_TRANSITIONS`. They are
different machines with different edges:

| From | Wave 2 (`investigation/opportunity.py`) | Wave 1 (`opportunity/lifecycle.py`) |
|---|---|---|
| CANDIDATE | VERIFYING | REJECTED, VERIFYING |
| VERIFYING | PROMISING, INSUFFICIENT_EVIDENCE | ALREADY_SOLVED, INSUFFICIENT_EVIDENCE, PROMISING, REJECTED, STALE |
| HUMAN_REVIEW | VERIFYING, VALIDATED, REJECTED, ALREADY_SOLVED, INSUFFICIENT_EVIDENCE | REJECTED, STALE, VALIDATED |
| REJECTED | (none) | RESURRECTED, STALE |
| INSUFFICIENT_EVIDENCE | (none) | RESURRECTED, STALE, VERIFYING |
| RESURRECTED | (none) | VERIFYING |
| VALIDATED | (none) | DEAD, STALE |

Two concrete divergences, both executed:

- Wave 2 allows a human to ask for more research from `HUMAN_REVIEW`
  (`HumanDecision.request_more_research` → VERIFYING, `opportunity.py:104`). Wave 1 refuses
  that edge outright: `evaluate_transition(HUMAN_REVIEW, VERIFYING, ...)` returns
  `allowed=False, reason_code="illegal_transition"`. A human who asks for more research
  would be rejected by the real opportunity machine.
- Wave 2 has no outgoing edge from `REJECTED`, so it can never resurrect. Wave 1 can, given
  new evidence and a reason.

The pipeline uses the wave-2 table for its recommendation (`_recommended_state`,
`pipeline.py:1275-1289`), while the wave-2 scoring and validation spec defines the wave-1
table as the authoritative one (`docs/reasonix/specs/wave-2-scoring-validation.md:321-322`).
So the investigation's recommendation is not directly usable by the opportunity machine.

Fix: Delete the wave-2 transition table and have
`research/investigation/opportunity.py` import `OpportunityState`,
`ALLOWED_TRANSITIONS` and the requirement checks from `opportunity/lifecycle.py`. If wave 2
needs its own states, add them to the wave-1 machine instead. Two tables for one state
machine will drift again.

### SHOULD-4: The mandatory counter-family guard has no test

`pipeline.py:267-275` refuses an `InvestigationRequest` that drops a mandatory
counter-evidence family. The guard works; executed, it raises on all three of the
documented removal attempts (mandatory competitor family, mandatory counter family,
mandatory area).

`tests/test_research_investigation_pipeline.py:240` exercises only `competitor_families`.
`grep -n "counter_families" tests/` returns no hits. The counter-family guard and the
counter-evidence-area guard are untested, so a refactor could drop them without a red test.

Fix: Add two tests mirroring `test_a_request_may_not_remove_a_mandatory_family`: one
dropping a mandatory counter family, one dropping the counter-evidence area.

### SHOULD-5: No source-concentration check inside an investigation

`coverage.py:110-114` adds domains to a set with no per-domain cap. One domain can supply
every document in the run while a second domain satisfies `min_domains=2`, and the run reads
as well-diversified.

There is no concentration check anywhere in `src/product_hunter/research/investigation/`
(`grep -r "concentration" src/product_hunter/research/investigation/` returns nothing). The
repo's concentration logic lives elsewhere and measures something else:
`_concentration_penalty` (`src/product_hunter/opportunity/scoring.py:671-678`) penalizes
author concentration in the confidence score, and `src/product_hunter/independence/analytics.py`
measures author and community concentration. Neither looks at source diversity inside an
investigation.

Fix: Add a per-domain document cap to the coverage assessment, or a concentration ratio
(a domain may not supply more than a configured share of the documents). Record the ratio in
`ResearchCoverage` so a reader can see it.

### NICE-1: The per-area query floor cannot fail

`_search_requirements` (`pipeline.py:822-834`) sets an area's `min_queries` to the number of
that area's own planned step queries:

```python
    return requirements.model_copy(
        update={"min_queries": max(1, len({query for query in queries}))}
    )
```

A floor equal to the number of steps the area plans is satisfied whenever every step
succeeds, which the area already requires for completeness. The only binding query floor is
the run-level one, `min_queries=6` by default (`coverage.py:48`), applied to the whole plan
of 23 steps.

The competitor area therefore needs four distinct queries, not six. This is the wave-2
spec's known-limit 11, and it is worth restating because the run-level floor of six is
comfortably cleared by a plan of 23 steps, so the floor constrains almost nothing.

### NICE-2: The coverage reason overstates what happened

`coverage.py:133-134` reports "N documents examined". The pipeline counts documents; it
never opens one. `_domains(outcome.documents)` (`pipeline.py:424-431`) reads the host of
each URL and nothing else. The count is of documents *received*, not examined.

---

## 4. The invariants that hold

These are real, and several are better than the audit expected. Each was confirmed by
reading the rule and, where executable, by running it.

| Invariant | Where it holds | Confirmed |
|---|---|---|
| Mandatory competitor families cannot be removed | `pipeline.py:257-266` | executed, raises |
| Mandatory counter-evidence families cannot be removed | `pipeline.py:267-275` | executed, raises |
| The competitor and counter-evidence areas cannot be dropped | `pipeline.py:276-286` | executed, raises |
| The query set comes from code, not from the model | `queries.py:81-233`, `build_plan` is pure (`pipeline.py:463-488`) | read |
| A template without `{problem}` is refused | `queries.py:60-67` | test at `test_research_investigation_queries.py:84` |
| "No competitor found" needs a complete coverage | `competitors.py:111-117` | test at `test_research_investigation_competitors.py:112` |
| "No competitor found" is never high confidence | `competitors.py:118-122` | test at `..._competitors.py:91` |
| "No competitor found" carries its unknowns | `competitors.py:183-186`, includes "a complete search of a narrow slice of the market is not proof of absence" | read |
| A failed search makes the coverage incomplete, even after a successful retry | `coverage.py:100-104`, `143` | test at `..._coverage.py:90` |
| "None found" needs a complete coverage | `counter_evidence.py:76-82` | test at `..._counter_evidence.py:113` |
| A zero floor is refused | `coverage.py:48-50` (`ge=1` on all three) | test at `..._areas.py:149` |
| A claim with no evidence cannot be `supported` | `claims.py:46-48`, `90-93` | test at `..._claims.py:65` |
| A claim whose evidence the provenance cannot resolve is refused | `claims.py:94-98` | test at `..._claims.py:83` |
| A contradiction without a reconciliation cannot be `supported` | `claims.py:49-50` | test at `..._claims.py` |
| A reconciliation must name the contradiction it addresses | `claims.py:57-62` | test at `..._claims.py` |
| Contradicting evidence is preserved when a claim is reconciled | `claims.py:70` is never cleared; `claims.py:77` reads it | the field survives, but the pipeline never populates it (MUST-3) |
| The machine never validates an opportunity | `pipeline.py:337-340`, `opportunity.py:88-96` | test at `..._opportunity.py` |
| A `supported` outcome needs a complete coverage | `pipeline.py:329-336` | read |
| A zero is recorded only on a clean counter search with no tool failure anywhere in the run | `pipeline.py:1414-1434` | read |
| The wave-1 gate accepts that zero only when every required family was searched successfully | `predicates.py:324-370` | executed |
| URLs are stored without credentials; unparseable URLs are not stored | `pipeline.py:365-397` | test in the pipeline suite |
| An over-cap result is refused whole | `pipeline.py:662-688` | test in the pipeline suite |
| A resumed investigation reaches the same conclusion | `pipeline.py:600-643` | test in the pipeline suite |
| The startup killer is not allowed to pass a dimension it did not attack | `startup_killer.py:118-128` | test at `..._killer.py:98` |
| A falsified dimension must name the disproof | `startup_killer.py:132-134` | test at `..._killer.py:131` |
| A report covering eight of ten dimensions is inconclusive, not a pass | `startup_killer.py:156-161` | read |
| A human decision is required before VALIDATED | `opportunity/lifecycle.py:310-319` | read |

---

## 5. The audited areas, item by item

The task listed nineteen areas. Each gets a verdict here.

1. **Opportunity investigator**: not implemented as an agent. The wave-2 spec is a library.
   The spec's `investigator` agent (`Product-Hunter-Full-Spec.md:188-190`) has no module.
2. **Competitor discovery**: implemented and sound. `competitors.py` enforces the
   none-found/none-exists distinction. Wired to nothing.
3. **Competitor analysis**: implemented. Confidence, pricing vocabulary, positioning,
   overlap, gaps, unknowns all present (`competitors.py:52-86`). `discovered_via` and
   `tool_call_id` are carried.
4. **Alternative solutions**: implemented as the `alternative` research area
   (`areas.py:26`) with its own template (`queries.py:145-149`). The `alternative` area's
   findings are not turned into a claim: `_build_claims` only claims
   `existing_solution`, `market_validation`, `willingness_to_pay` and `distribution`
   (`pipeline.py:1160-1165`). The `alternative` area produces findings and no conclusion.
5. **Pricing research**: implemented. `competitor_pricing` family (`queries.py:96-100`),
   `Competitor.pricing` and `pricing_model` with a closed vocabulary
   (`competitors.py:21`, `77-80`).
6. **Counter-evidence**: implemented, but MUST-2 and MUST-4 apply.
7. **Startup-killer reasoning**: implemented. Ten dimensions, one family each
   (`startup_killer.py:43-56`), verdict derived (`startup_killer.py:146-161`). SHOULD-2
   applies.
8. **Validation experiments**: not in this package. `src/product_hunter/opportunity/experiments.py`
   exists and is separate from the investigation engine; nothing joins them.
9. **Claims**: implemented. MUST-3 and SHOULD-1 apply.
10. **Claim evidence**: implemented. `ClaimEvidence` (`claims.py:27-37`), provenance
    resolution enforced (`claims.py:94-98`). MUST-5 applies to how evidence is collected.
11. **Support/contradict stance**: implemented in the model, not reached by the pipeline.
    MUST-3.
12. **Mandatory query families**: enforced at the request level (`pipeline.py:257-287`).
    The gate level is broken by MUST-1. The executor can still report a search that did not
    happen: the pipeline has no way to verify that a `tool_call_id` corresponds to a real
    recorded call, since the tool calls are constructed by the pipeline itself
    (`pipeline.py:509-530`). Wave 1's own artifact-traceability check
    (`gate.py:414-430`) runs against the same self-asserted records.
13. **Negative queries**: present in both systems, disjoint. Wave 1 has `failed_solution`
    and `alternative_to`; wave 2 has `counter_not_worth`, `counter_complaints`,
    `counter_workaround`. MUST-1 is what stops them meeting.
14. **Counter-evidence stopping conditions**: implemented via the coverage rule, but with
    the per-family hole in MUST-4.
15. **Non-trivial zero-result definition**: the definition exists
    (`coverage.py:140-147`) and the gate accepts it (`predicates.py:324-370`). The threshold
    is low: `min_documents=1`, `min_domains=2`, and the spec calls these "placeholders for
    measured numbers" (known limit 2). MUST-4 is the sharper problem.
16. **Source diversity**: `min_domains=2` (`coverage.py:50`), enforced in
    `coverage.py:135-138`. SHOULD-5: no concentration ratio.
17. **Concentration checks**: none inside the investigation package. See SHOULD-5.
18. **Human review triggers**: two disconnected implementations. The wave-1 machine
    requires `counter_evidence_searched=True` to move PROMISING to HUMAN_REVIEW
    (`opportunity/lifecycle.py:299-308`); that flag is a boolean the caller asserts.
    The wave-2 machine has no such requirement (`investigation/opportunity.py:68-74`). The
    pipeline's own `supported` outcome does imply a complete counter-evidence search, but
    only because `found` would have produced `contradicted` first (`pipeline.py:986-990`).
    SHOULD-3.
19. **Wave 3 branches**: none of the eight touch the investigation system. The integration
    work has not started.

---

## 6. Mandatory integration fixes, in order

These are the changes the repository needs before the deep investigation system can be
called shipped. Each names the file to change.

1. **Wire an executor.** Implement `ResearchExecutor.search` against the existing source
   adapters in `src/product_hunter/collection/`, honouring the adapter rule that a URL must
   be declared in `config/sources.yaml`. Place it in a new module under
   `src/product_hunter/research/investigation/` or in `src/product_hunter/collection/`.
2. **Register an `INVESTIGATE` worker handler.** The worker dispatches by
   `job.stage` (`runtime/worker.py:160`) from `PH_WORKER_HANDLERS`
   (`runtime/__main__.py:41`). Provide a handler factory that builds an
   `InvestigationRequest` from the run's opportunity, calls `investigate()`, and records
   the result. Without this the `deep_investigation` schedule
   (`db/scheduler.py:543-550`) produces jobs nobody runs.
3. **Make the trigger structural.** A promising opportunity must create the investigation,
   not merely score higher. The spec is explicit (`Product-Hunter-Full-Spec.md:1514`).
   The promotion edge `PROMISING → HUMAN_REVIEW` in `opportunity/lifecycle.py:299-308`
   currently accepts a caller-asserted boolean; it should require an investigation record
   that exists, the way `VALIDATED` requires a human decision
   (`opportunity/lifecycle.py:310-319`).
4. **Resolve the family vocabulary (MUST-1).** Until this is done the investigation stage
   fails the shipped contract and no run can be promoted.
5. **Give the counter-evidence a strength floor (MUST-2)** and read `strength` where it
   decides the outcome.
6. **Derive claim status from evidence (MUST-3).** Stop hardcoding
   `status=ClaimStatus.supported` in `_claim` (`pipeline.py:1084`), and populate
   `contradicting_evidence` from the counter-evidence result.
7. **Add the per-family document floor (MUST-4)** in `assess_coverage` or in the
   competitor and counter-evidence assessments.
8. **Validate executor-supplied call ids (MUST-5)** and raise on a foreign id rather than
   dropping the item silently.
9. **Unify the two opportunity state machines (SHOULD-3)**.
10. **Deduplicate supporting evidence (SHOULD-1)** and enforce uniqueness in the claim
    validator.
11. **Make the killer's disproof traceable (SHOULD-2)**: stamp `query_family`, require a
    source, and put the disproof's ref in the dimension's `evidence_refs`.
12. **Add the missing tests (SHOULD-4)** for the counter-family and counter-area guards.
13. **Persist the investigation record.** `research_claims` exists (migration `0003`) and
    `record_claim` (`db/research_gate.py:550-599`) writes to it, and an `InvestigationClaim`
    is a `ResearchClaim`, so the claims can be stored as they are. The investigation record
    itself has no table: there is nowhere to keep the coverage, the areas, the killer report
    or the provenance. The wave-2 spec listed persistence as a non-goal
    (`wave-2-investigation.md:43`), so nothing was built. The claims table also needs a
    status column and a place for the contradicting evidence (MUST-3), and the duplicate
    refs (SHOULD-1) are stored verbatim as `evidence_refs_json`.
14. **Raise the coverage floors from their placeholder values** (`min_documents=1`,
    `min_domains=2`) once the executor is live and the real cost of a search is known.

---

## 7. What this audit did not check

- **The adversarial review pass did not run.** It was dispatched three times against the
  free review rungs and failed on provider errors every time: `HTTP 429 Too Many Requests`
  on rung 1 twice, and `Model unavailable: opencode-go/longcat-2.5-preview-free` on rung 2.
  No reviewer verdict was obtained, and none is claimed. The findings below rest on three
  other legs: every Must was demonstrated by executing the package and quoting the output,
  two independent gather passes confirmed the wiring and test-coverage facts with line
  references, and probe 03 of this series reached the same conclusions as MUST-1 and
  SHOULD-3 from the contract side. A fresh-context reviewer would still be worth running
  before anything here is implemented.
- **Runtime behaviour under load.** Every finding was produced with a fake executor in a
  single-process script. Nothing here measures latency, memory or database behaviour.
- **The executor's own honesty.** The pipeline trusts the executor's `status`, its
  `document_count` and its `domains`. MUST-1's gate failure and MUST-5's silent drop are
  what the pipeline can detect; a lying executor that reports successful searches it never
  made is undetectable from inside the pipeline, and needs the database gate to catch it.
  That path was read, not executed.
- **The seven other Wave 3 branches.** Only their diff against the investigation directory
  was checked, which is empty. Whether they change the opportunity scoring that feeds the
  trigger was out of scope.
- **`db/research_gate.py` in full.** Its promotion path was traced at the level of the
  `evaluate_promotion` call (`db/research_gate.py:777-788`) and its claim writer at
  `record_claim` (`:550-599`). The per-stage execution bookkeeping below that was not
  audited line by line.
- **The wave-2 investigation handoff.** Read for scope; not audited for accuracy against
  the shipped code.
- **`docs/reasonix/spec-compliance.md`** was not used as a checklist. The audit used the
  master spec and the wave-2 spec, which are the normative documents.
- **Model routing for the investigation stage.** `config/model_routing.yaml` names an
  `investigation` role; whether the executor should route through it was not examined.

---

## 8. Reproducing this audit

```
git worktree list
git diff --name-only main...wave3/<branch> -- src/product_hunter/research/investigation/
uv run pytest                                              # 2138 passed
.venv\Scripts\python.exe %TEMP%\opencode\audit08_verify.py
.venv\Scripts\python.exe %TEMP%\opencode\audit08_verify2.py
.venv\Scripts\python.exe %TEMP%\opencode\audit08_verify3.py
.venv\Scripts\python.exe %TEMP%\opencode\audit08_verify4.py
```

The four scripts are read-only. They import the package and print results; they write
nothing into the repository.

---

## 9. Verdict

The internal rules of the deep investigation system are better than most of the repo's
protections. The mandatory families are code-generated and cannot be dropped. A failed
search never becomes a zero. The machine never validates an opportunity. A competitor that
was not found is recorded as a claim with a confidence and a list of unknowns, not as a
fact. Those are the rules the audit came to check, and most of them hold.

Two things are broken, and they are the two the audit was asked to find.

First, nothing triggers an investigation. A promising opportunity gets a score and stops.
The engine exists, is tested, and has no caller.

Second, the engine's own conclusions are weaker than the invariants they are meant to
uphold. A zero-strength counter-evidence item rejects an opportunity. A counter-evidence
search that returned nothing counts as a completed family, and the area can publish "no
counter-evidence found" on two documents. A rejection carries no claim, so the record
cannot say why the opportunity died. And the stage fails the shipped contract on a
vocabulary collision that no document mentions, which blocks promotion of every run.

Fix the wiring and the four Musts before this system is called live. The Shoulds can
follow; NICE-1 and NICE-2 are documentation-grade.

## 10. Where this report sits in the audit series

| Probe | Covers | Overlap with this report |
|---|---|---|
| 01 architecture | layering, module boundaries | none |
| 02 evidence pipeline | extraction and evidence storage | 11 mentions of the investigation stage as a consumer |
| 03 research contract | the gate, the contract, promotion | F-03 is the same block as MUST-1, found from the contract side; its F-04 covers the coverage floors |
| 04 storage | migrations and persistence | fix 13 touches `research_claims` |
| 05 bge-m3 | embeddings | none |
| 09 autonomous runtime | the worker and the scheduler | MUST-2 touches the same wiring |
| 10 control security | secrets and the control plane | none |

The findings here are specific to the investigation system. Where a sibling probe reached
the same conclusion from a different angle, this report says so rather than claiming the
finding twice.

---

Audit complete. The full finding set, the executed proof for each, and the ordered
integration fixes are above. Two things were not checked and are named in section 7:
runtime behaviour under load, and the honesty of a real executor.

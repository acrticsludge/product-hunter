# Phase 4 Wave 1: research contract and tool gate foundation, wave report

**RUN_ID:** phase4-20261021-resume-01
**Node:** PRODUCTIONIZE (stage 8)
**Date:** 2026-10-21
**Risk:** declared medium, floor medium

## What this wave delivered

The research contract and the tool gate, as code. A typed contract of what a
research run must do, a gate that turns evidence into a pass or a fail with
stable violation codes, tool-call provenance records, a budget model that
refuses the call that would break a limit, and 141 deterministic tests.

The wave does not run anything. There is no collector, no search, no scrape, no
stage execution, no score, and no SQLite write. The gate is a library that
nothing calls yet.

The code was written in stage 4 and then went through one security and
observability pass, a re-harden pass, and a run of review rounds that returned
findings. The nine review rounds that are named below each produced a fix
round, and two later fix rounds came from re-checks of the round before them.
Each pass returned a verdict, each accepted finding was fixed, and each
behavioural fix has a test that pins it. A further review pass was running in
parallel with this document pass, and this node did not see its output. This
report is the record of what those passes found, what was left alone on
purpose, and what a reader must not trust yet.

## Gate evidence

The code gate is `uv run pytest`, from `.loop/adapter.json`.

```text
uv run pytest
980 passed in 13.31s
exit code 0
```

The research package on its own:

```text
uv run pytest tests/test_research_budget.py tests/test_research_contract.py tests/test_research_gate.py tests/test_research_provenance.py
141 passed in 0.59s
exit code 0
```

Both runs were made on 2026-10-21 at stage 8, in the shared working tree. The
tree holds other waves' uncommitted work, and the 980 figure is the whole suite
as it stood at that moment. The research package's own 141 is the stable number
for this wave. The four files split as 38 contract, 75 gate, 13 budget, and 15
provenance tests.

## What was found and fixed, in order

The verdicts below are the ones the passes returned. The rounds match the
round order in `docs/reasonix/prompts/phase-4-implement.md`, so every item can
be traced to the prompt that asked for it.

### 1. Security and observability pass, `fix first`, two Must-fix

- The `stage_failed` and `stage_timed_out` violation details carried
  `execution.error`. That is prose from the evidence, and a later wave writes
  these details to a database where a URL can carry a token. The error stays on
  the `StageExecution`.
- The `artifact_invalid` detail for an invalid artifact carried
  `artifact.invalid_reason`, which is free text set by whoever produced the
  artifact. The detail keeps `artifact_id` and `kind` and nothing else.

Two smaller items came with them: promotion now honours the hard requirement
flags for the counter-evidence and competitor gates, and the planned migration
number was corrected to `0003`.

### 2. Re-harden, `ship`, one Should-fix

The shipped contract declared thresholds its own stages could not satisfy. Four
stages were missing the artifact kind that their own threshold reads, so those
thresholds always read zero and the stage always failed. The YAML was corrected,
the mapping from a metric to the artifact kind it reads was published as
`METRIC_ARTIFACT_KINDS`, and a test walks the shipped baseline so the two
cannot drift apart again.

### 3. First review, `fix 1, 2 first`, two Must-fix

- A run that failed its own run-level thresholds was still promoted. The
  run-level `evidence_threshold_unmet` violation carries no stage, and the
  promotion check never read it. Promotion now blocks on it.
- A recorded zero naming no required query family counted as a real zero.
  `explicit_zero_outcome` refuses that case now.

Four Should-fix items came with them: the validator refuses the run-level
`claim_evidence_links` in a stage's quality checks; `discovery` requires
`fetch`, which its own source-diversity check and its domain threshold read;
`clustering` declares the two artifact kinds its own quality checks read, and
`PREDICATE_ARTIFACT_KINDS` was published; the per-run search-query limit was
raised above the sum of the per-stage limits it contains.

### 4. Second review, `fix 1 first`

The gate never compared the stage it was judging with the stage the execution
claimed to belong to. A misfiled execution could pass the stage it was filed
under. `evaluate_stage` now reports `provenance_mismatch` for that case.

Four smaller items: a recorded zero whose claimed families were never searched
is refused; the behaviour of `promotion.require_all_stages_passed: false` got a
test, because nothing in the package exercised it; the promotion budget reason
is read back from the run result instead of from a tracker the caller had to
remember, and the `budget` parameter left `evaluate_promotion`; the comment on
`PREDICATE_ARTIFACT_KINDS` was corrected, because two of the predicates it
called absent do read artifacts.

### 5. Third review, `fix 1 first`

The run-level promotion threshold summed over stages the gate never evaluated,
so the run-level evidence floor could be met by evidence nothing had judged.
The sum now runs over `contract.required_stages`.

Three smaller items: a misfiled execution now reports its own status next to
the mismatch, so a misfiled failed stage still carries `stage_failed` and still
blocks promotion; the two stage timestamps are timezone-aware and a naive one
is refused at construction instead of silently switching the timeout check off;
a test pins that `PREDICATE_ARTIFACT_KINDS` covers every artifact-reading
predicate, so the next one forces a decision.

### 6. Fourth review, `fix 1, 2 first`

- The run-level threshold counted a misfiled execution, and the same path
  dereferenced a `None` execution and raised `AttributeError` where the gate's
  own docstring promises a verdict. Both are closed. The run-level sum is
  built in one place and keeps an execution only when it exists, is not
  `None`, and belongs to the stage it is filed under.
- The metrics counted artifacts the gate had already rejected, so a run-level
  floor of ten independent authors could be met by an artifact the gate threw
  out. A metric now counts an artifact only when the artifact is valid and
  traces to a successful tool call in the same stage.

Three smaller items: promotion refuses a run result that belongs to another
run; the validator refuses a contract that could never promote; a test pins the
three status violations' codes, messages, and details, because those strings
are what a later wave writes to the database.

### 7. Fifth review, `fix 1 first`, the crash path

A successful fetch call carrying `http://[::1` made `urlparse` raise, and the
gate raised instead of returning a verdict. The host parse is guarded. A URL
the parser cannot read contributes no domain, and the gate carries on to a
verdict.

Three smaller items: the metric filter is stricter than the gate for a stage
that declares no required tools, and the docstring now says so instead of
claiming the opposite; the two neighbour and duplicate predicates now require
a traceable artifact, so one registry has one standard; the validator refuses a
stage that serves the reserved `promotion` gate key.

### 8. Sixth review, `fix 1, 2 first`

- Promotion read the claims twice, so a one-shot iterator was empty on the
  second read and the claim-evidence rule was silently satisfied. The claims
  are materialised into a tuple before anything reads them.
- A NaN budget limit passed validation and switched the dimension off, because
  every comparison against NaN is false. The three limit checks now require a
  limit greater than zero, which refuses NaN with it.

Five smaller items: the timeout check no longer switches off when the elapsed
time cannot be computed, and a stage that cannot prove it finished inside its
timeout is treated as having overrun it; the host is trimmed before it is
lowered; the query and the query family are trimmed before they are counted;
the metric docstring reads the divergence the right way round; a YAML syntax
error in a contract file now raises `ContractError` instead of a yaml
exception.

### 9. Seventh review, `fix 1 first`

Promotion applied a run result that had been judged under another contract, so
a run that failed a tight contract could be promoted under it. The run result
now carries a fingerprint of the contract it was judged under, and promotion
compares it. The fingerprint is over the whole contract, because two contracts
can carry the same version and different thresholds.

Four smaller items: the run-level counter-evidence threshold accepts the same
recorded explicit zero the stage-level check accepts, so one contract and one
execution cannot pass at stage level and fail at run level; a contract file
that is not UTF-8 raises `ContractError`; a whitespace-only query is not a
query; the timeout message for a stage whose elapsed time cannot be computed
says the stage cannot be shown to have finished inside its timeout, which is
what the code knows.

### 10. Eighth review, `fix 1, 2 first`, six changes

The eighth review fuzzed the three evaluators over 4000 generated executions,
found zero exceptions, checked every validation rule, and audited every
violation detail. It found two fail-open paths in the run-level threshold
guard and three smaller items. Fix round 10 closed all of them.

- The run-level counter-evidence minimum accepted a recorded zero from any
  stage of the run. A zero recorded by the discovery stage satisfied a
  counter-evidence floor that the counter-evidence stage had not met. The
  run-level check now reads the zero of the execution of the stage that serves
  the `counter_evidence` gate key, and a contract in which no stage serves
  that key accepts no run-level zero at all.
- The run-level value of a distinct metric was the sum of the per-stage
  counts, so a host seen by three stages counted three times and the run-level
  floor could be met by one source. The three distinct metrics now have a
  set-returning form, `DISTINCT_METRIC_SETS` names them, and the run-level
  value is the size of the union of the per-stage sets. Every other metric
  still sums.
- The gate measured a budget overrun against the tracker's own spec, so a
  caller could hand the gate a tracker built from a looser budget and widen
  the limit the contract declares. `BudgetTracker.exceeded_against(spec)` takes
  the limits from a spec, and the gate asks for the overrun against
  `contract.budgets`.
- An artifact of a required output kind filed under another stage was
  accepted as that stage's output. A misfiled artifact now reports
  `provenance_mismatch`, and a kind whose artifacts are all misfiled reports
  `artifact_missing` as well.
- The two timeout cases shared one message, so a proven overrun read the same
  as an elapsed time that could not be computed. They carry different
  messages now.
- The contract fingerprint was taken over the contract as written, so two
  contracts that produce identical verdicts fingerprinted differently when the
  order of a list that carries no meaning changed. The fingerprint is now taken
  over a canonical form in which `required_stages`, each stage's
  `required_tools`, `required_outputs` and `quality_checks`, and the retry
  policy's `retry_on` are sorted.

Six tests were added, one per change, and the package's own test count went
from 125 to 131.

### 11. Fix round 11, four findings

- **K1. A misfiled artifact was still counted towards a threshold.** The metric
  required the artifact to be valid and to trace to a successful call, and the
  trace check looked at the call's stage but not the artifact's own stage
  field, so an artifact that a collector filed under the wrong stage was
  counted towards the threshold of the stage being judged. The counting rule is
  one helper now, and it requires the artifact to be valid, to belong to the
  execution's stage, and to trace to a successful call of that stage. The
  validator now refuses a contract whose stage declares a threshold on an
  artifact kind it does not declare in `required_outputs`, so the stage cannot
  be written in a shape that always reads zero. The rule reads the stage that
  owns the gate key, so a threshold under the reserved `promotion` key, which
  no stage serves, is not covered by it.
- **K2. `evaluate_run` raised on an invalid contract.** The fingerprint is
  taken over the whole contract, and an invalid contract can hold a value that
  cannot be serialised, so the invalid-contract path now returns the violation
  before the fingerprint is taken. The result carries an empty fingerprint,
  because no verdict was reached. The other two entry points already returned
  a verdict for the same contract.
- **K3. A non-finite amount reached the budget ledger.** Every comparison
  against NaN or infinity is false, so an amount that got in would have
  switched its dimension off. `check` denies a non-finite amount and returns a
  fixed reason that does not quote the value, `record` refuses the spend and
  does not record it, and `guard` refuses it. The refusal is a `ValueError`,
  not a `BudgetExceeded`: a NaN is not a number of calls.
- **K4. A stage that finished before it started satisfied the timeout
  check.** The duration was negative, and a negative duration is not greater
  than any timeout. A stage whose duration cannot be computed cannot be shown
  to have finished inside its timeout, and the timeout check now fires. A
  zero-length stage is a real duration of zero and is still in time.

### 12. Fix round 12, two findings

- **L1. A misfiled artifact could still satisfy a quality check.** The two
  artifact-reading quality predicates did not carry the stage binding the
  metrics had. An artifact filed under another stage satisfied
  `nearest_neighbor_checked` and `duplicate_candidates_reviewed` even though
  the same artifact was not counted towards the stage's threshold. Both
  predicates go through the same helper the metrics use now, so the stage rule
  has one implementation.
- **L2. The validator did not check the quality-check kinds.** The rule that
  refuses a threshold on an artifact kind the stage does not declare now
  covers the quality checks as well. A stage that names
  `nearest_neighbor_checked` without declaring `neighbor_check` in its
  `required_outputs` is an error, for the same reason the threshold case is.

Five tests came with round 11 and two with round 12, and the package's own test
count went from 131 to 138.

A review also found that `require_provenance`, `require_qdrant_check` and
`require_duckdb_analysis` are enforced only through the stage gate. With
`promotion.require_all_stages_passed: false` they are not enforced at
promotion time. The run deliberately did not change this, and it is recorded
below as a known residual.

### 13. Fix round 13, three changes

- **M1. A run-level threshold on a minimum metric could never fail.**
  `neighbors_checked_per_cluster` is a minimum over what it counts: the stage
  value is the smallest cluster check the stage made. The run-level value was
  the sum of the per-stage values, so two stages that each checked three
  neighbours per cluster reported six, and a run-level minimum of five on that
  metric was met by a number the run never had. A new mapping,
  `MINIMUM_METRICS`, names the metrics that are a minimum rather than a total,
  and the run-level value of such a metric is now the smallest of the run's
  per-stage values, and zero for a run with no executions.
- **M2. A call filed under another stage counted as this stage's evidence.**
  The distinct metrics counted the queries, families, and hosts of every call
  the execution held, so a call that named another stage could satisfy a
  threshold, a quality check, or a required tool. One helper, `stage_calls`,
  now yields the calls whose own `stage` field equals the execution's stage,
  and it is used at every site that counts a call: the three distinct metric
  set functions, `non_empty_queries`, the searched families and the failed
  searches in `explicit_zero_outcome`, and the gate's `required_tools` check.
  The provenance check is unchanged, so a foreign call still reports
  `provenance_mismatch` when the contract requires provenance.
- **M3. A refused budget amount had no name of its own.** `record` and `guard`
  refused a non-finite amount with a bare `ValueError`, so a caller could not
  tell a refused amount from an overrun without reading the message. The
  refusal is now a named class, `NonFiniteAmount`, which subclasses
  `ValueError`, so a caller that already catches one keeps catching it, and a
  caller that wants to tell the two refusals apart has a class of its own to
  catch. The package exports it next to `BudgetExceeded`. `check` still
  returns a decision.

Three tests came with round 13, and the package's own test count went from 138
to 141.

## What was deliberately left, and why

- **The budget violation names the dimension, not the scope that overran.**
  `BudgetTracker.exceeded_against` returns dimension names, and the tracker
  does not attribute an overrun to a scope, so the gate cannot name a culprit
  stage. Naming the scope needs the tracker to change. That is a later wave.
- **No length or count bounds on the models.** The contract is
  operator-controlled YAML and the evidence comes from internal collectors, so
  an unbounded list is a memory risk rather than a security one on a local
  single-operator tool.
- **No logging in the package.** Two of the 48 modules under
  `src/product_hunter/` use `logging`, and both are unattended process loops
  with no caller to log for. Every other module raises or returns structured
  values and lets the caller decide. The research package returns structured
  results, and its consumer does not exist yet.
- **A hand-built run result keeps working.** `RunGateResult.contract_fingerprint`
  defaults to an empty string, and an empty fingerprint is not compared with
  anything. A result that `evaluate_run` did not produce is not blocked on the
  contract binding. A run judged under an invalid contract also carries an
  empty fingerprint, because no verdict was reached, and a promotion against
  that contract is blocked by `contract_invalid` before the fingerprint is
  read.
- **A hand-built `BudgetSpec` accepts a non-positive limit.** The contract path
  refuses it. A spec built outside the contract is outside the trust boundary
  in the same way a hand-built run result is, and moving the error to model
  construction would change what an existing test asserts.
- **The zero-result predicate compares family names verbatim.** It neither
  trims them nor discards a name that is only whitespace, while the two
  metrics do both. A recorded zero that names `"alpha "` does not match a
  search call that carries `"alpha"`, and a family name of `"   "` counts as
  searched. The zero path is stricter about the family having been searched
  and looser about the exact spelling.
- **`require_claim_evidence_links` is satisfied by a run with no claims.** The
  vacuous reading is deliberate: a run with no claims has no unlinked claim.
- **The two tool-call metrics count every call the execution holds.** Round 13's
  `stage_calls` rule binds the distinct metrics, the predicates, and the
  `required_tools` check to the calls the stage itself made. `tool_calls` and
  `successful_tool_calls` were left counting the whole execution, so they
  report the activity the execution recorded rather than the activity of the
  stage. No threshold reads them, so they change no verdict, and the run level
  sums them like any other additive metric.
- **The lean findings.** The unreachable unknown-predicate branch and the
  duplicated early-return blocks in the gate. A refactor of a fail-closed gate
  late in the wave is a worse trade than the lines it saves. The branch stays
  because the gate promises never to raise for a failed gate.
- **Three hard-requirement flags are enforced only through the stage gate.**
  `require_provenance` is read by `evaluate_stage`. `require_qdrant_check` and
  `require_duckdb_analysis` are read by contract validation, which requires a
  stage to declare the tool, and the stage gate's `tool_missing` check is
  what proves the tool ran. `evaluate_promotion` reads none of the three, so
  with `promotion.require_all_stages_passed: false` a stage that failed on
  provenance, or on a qdrant or duckdb tool that never ran, does not block
  promotion. Closing the gap would mean reading the three flags at promotion
  time. The run deliberately did not change it.

## Known residuals

- **No live research run has exercised this gate.** Every input in the 141
  tests is a fixture written by hand.
- **Three hard-requirement flags are not enforced at promotion time.**
  `require_provenance`, `require_qdrant_check` and `require_duckdb_analysis`
  are enforced through the stage gate only. The promotion check carries a
  stage failure forward only while `promotion.require_all_stages_passed` is
  set, so with that flag false a run whose stage failed on missing
  provenance, or on a qdrant or duckdb tool that never ran, can still be
  promoted. The shipped contract sets the flag to true, so the shipped
  baseline is not exposed to it. The review that found this recorded it
  without asking for a change, and the run left it alone.
- **The budget limits in the shipped contract are starting values, not
  measured ones.** They came from the spec's baseline, and no run has produced
  a spend figure to check them against.
- **The budget violation names the dimension, not the stage that overran.**
  The overrun is measured against the contract's budgets, so the limit is
  right, and a reader still cannot tell which stage went over from the
  violation alone. The tracker would have to attribute the spend to a scope.
- **The threshold values have not been validated against a labelled corpus.**
  They are a design baseline.
- **The package has no caller.** Nothing outside
  `src/product_hunter/research/` imports it, no CLI command calls it, and
  there is no worker handler. The gate is reachable as a library and gates
  nothing yet.
- **The persistence protocols have no implementation.** `ToolCallStore` and
  `GateResultStore` are `Protocol` classes, and nothing in the product
  satisfies them. The migration `0003_research_gate.sql` is a plan in a
  docstring and the file does not exist.
- **The stopping policy and the retry policy are carried by the contract and
  read by no code in this wave.** The gate does not decide when a run has
  enough evidence and it does not retry.
- **The gate is stricter than the contract in one place.** A stage that
  declares no required tools can hold artifacts the gate accepts and the
  metric refuses, because nothing ties an untraceable artifact to a tool call.
  It fails closed. The metric docstring says so and a test pins it.
- **The `promotion` gate key has no stage of its own.** It is evaluated at run
  level, and a threshold block under it is what guards promotion.

## Debts

There are no `shortcut:` or `ponytail:` comments in
`src/product_hunter/research/`, in `config/research_contract.yaml`, or in the
four test files. A search for `shortcut`, `ponytail`, `TODO`, `FIXME`, `XXX`
and `HACK` across those three sets returns nothing.

The deferrals listed under "What was deliberately left" are this wave's debts.
They are recorded in this report rather than as markers in the code, because
the code carries no such markers.

## What stage 8 changed in the documents

The code was the source of truth. The documents were brought back to it. The
first pass closed the drift the handoff listed; the second pass closed the
drift that fix round 10 opened; the third pass closed the drift that fix
rounds 11 and 12 opened; the fourth pass closed the drift that fix round 13
opened.

- `docs/reasonix/specs/phase-4-research-contract.md`: sections 4.2, 4.3, 4.5,
  5.2, 5.4, 6.1, 6.2, 7.1, 7.2, 7.3, 7.4, 8, 9, 10, 11 and 12 rewritten to describe
  the code that exists, and section 4.6 added for the contract fingerprint.
  The validation rule list now matches the code's order and count. The gate
  check order now matches `evaluate_stage` line by line. The planned migration
  is `0003_research_gate.sql`. The second pass added the run-level counter-
  evidence scoping, the union rule for the three distinct metrics, the
  contract's budget as the budget the gate checks, the artifact stage binding,
  the two timeout messages, and the canonical fingerprint. The third pass added
  the two validation rules of rounds 11 and 12 and renumbered the list, the
  stage binding in the metric counting rule, the shared helper behind the two
  artifact-reading quality predicates, the invalid-contract path of the run
  gate, the negative-duration timeout case, the refusal of a non-finite budget
  amount, and section 7.5 for the promotion path's known limit. The fourth pass
  added the stage rule for calls in sections 4.2, 6.1, 6.2 and 7.2, the
  minimum metric in sections 6.1 and 7.3, the named budget refusal in section
  8, and the three new cases in the test matrix of section 11. It also
  corrected the two tool-call metric rows of section 6.1, which described the
  execution's calls as the stage's.
- `docs/reasonix/RESEARCH_CONTRACT.md`: the persistence docstring is named
  `0003`, and the rest of the document was checked against the code. The
  second pass corrected the claim that the two levels read the same recorded
  zero, and added the fingerprint, the union rule, the contract's budget, and
  the artifact stage binding. The third pass added the stage binding in the
  counting rule, the two validation rules, the non-finite amount refusal, and
  the promotion path's known limit. The fourth pass added the stage rule for
  calls, the minimum metric at run level, and the named refusal.
- `docs/reasonix/ARCHITECTURE.md`: the migration-number collision is closed,
  and the research package paragraph was checked against the code. The third
  pass re-checked both against the code as it stands, and the fourth pass
  added the three round-13 behaviours to the research package paragraph.
- `docs/reasonix/handoffs/phase-4-research-contract-handoff.md`: a closing
  section records that the wave is finished and names what the next wave
  inherits. The second pass refreshed the gate evidence and the state of the
  wave. The third pass refreshed them again and recorded that rounds 11 and
  12 closed the last of the misfiled-artifact class. The fourth pass
  refreshed the gate evidence again and added the round-13 changes.
- `docs/reasonix/prompts/phase-4-implement.md`: the record of the fix rounds
  gained a "Fix round 13" section with the three changes and the three tests
  that pin them.

## What was not checked

- The code was not re-reviewed. This node wrote documents. A further review
  pass ran in parallel with it, reading the code rather than these documents,
  and this node did not see its output, so nothing from it is recorded here.
  The review output behind round 13 was not seen either, so round 13 is
  recorded here from the code and the three tests that pin it.
- The wave was not committed. The tree is shared and dirty with other waves'
  work, so the commit is stage 9's job.
- No research run was executed, so nothing here is evidence that the gate
  behaves correctly against a real stage.
- The other 839 tests were not read. The 980 figure is the suite's result, not
  a review of it.
- `docs/reasonix/specs/Product-Hunter-Full-Spec.md` was not re-read against
  the code. The baseline threshold values and the stage requirements in
  `config/research_contract.yaml` were traced back to the spec's blocks, and
  the rest of the spec was taken as the design of record.

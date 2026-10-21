# Phase 4 Wave 1: research contract and tool gate foundation, run report

**RUN_ID:** phase4-20261021-resume-01
**Node:** REPORT (stage 10)
**Date:** 2026-10-21
**Commit:** f5af3ae, "Phase 4 wave 1: research contract and tool gate foundation"
**Risk:** declared medium, floor medium

## 1. What the wave delivered

The enforcement substrate for research quality, as code. A typed Research Contract that says what a research run must do. A gate that turns evidence into a pass or a fail with stable violation codes. Tool-call provenance records. A budget model that refuses the call that would break a limit. 141 deterministic tests.

The wave runs nothing. There is no collector, no search, no scrape, no stage execution, no score, and no SQLite write. The gate is a library that nothing calls yet. Its point is that the worker, not an agent instruction, decides whether a research stage happened.

The code lives in `src/product_hunter/research/`: `evidence.py`, `predicates.py`, `budget.py`, `contract.py`, `gate.py`, `persistence.py`, and `__init__.py`. The shipped contract is `config/research_contract.yaml`. Four test files sit under `tests/test_research_*.py`. The commit added 22 files, all new, including the spec, the handoff, the plan, and the prompt records.

## 2. Gate evidence

The code gate from `.loop/adapter.json`:

```text
uv run pytest
980 passed in 12.61s
exit code 0
```

The research package on its own:

```text
uv run pytest tests/test_research_budget.py tests/test_research_contract.py tests/test_research_gate.py tests/test_research_provenance.py
141 passed in 0.55s
exit code 0
```

The four research files split as 38 contract, 75 gate, 13 budget, and 15 provenance tests. The 980 figure is the whole suite as it stood when I ran it. The shared tree holds other waves' work, and the suite is green in it.

## 3. What the reviews found

Two security passes and a run of review passes. The harden prompt records two passes: an initial security and observability pass and a re-harden. The initial pass returned fix first with two Must-fix items. The re-harden returned ship.

The review prompt file records five passes: the initial pass plus four re-reviews. The implement prompt's fix-round headings reference nine review passes, because fix rounds 3 through 11 are each labelled "after the Nth REVIEW". The two files disagree on the count. I report both rather than choosing: five passes are written out in `phase-4-review.md`, and nine are implied by the labels in `phase-4-implement.md`.

Fix rounds: `phase-4-implement.md` records thirteen, Fix round 1 through Fix round 13. The wave report counts the same thirteen. The initial build came before them.

### 3.1 The two security passes

Initial pass, fix first, two Must-fix:

- Violation details carried `execution.error` for `stage_failed` and `stage_timed_out`. Error text from evidence reaching a persisted violation detail is untrusted text. Removed from the detail; the error stays on the `StageExecution`.
- The `artifact_invalid` detail for an invalid artifact carried `artifact.invalid_reason`, which is free text set by whoever produced the artifact. Removed; the detail keeps `artifact_id` and `kind`.

Two smaller items came with them: promotion now honours the hard requirement flags for the counter-evidence and competitor gates, and the planned migration number was corrected to `0003`.

Re-harden, ship, one Should-fix: the shipped contract declared thresholds its own stages could not satisfy, because four stages were missing the artifact kind their own threshold reads. The YAML was corrected, the metric-to-kind mapping was published as `METRIC_ARTIFACT_KINDS`, and a test walks the shipped baseline so the two cannot drift apart again.

### 3.2 The fail-open paths the reviews closed

Each entry names the class. That class is what a later wave should watch for.

- Run-level threshold ignored at promotion. A run that failed its own run-level thresholds was still promoted, because the promotion check never read the run-level `evidence_threshold_unmet` violation. Class: a run-level verdict not consulted at promotion time.
- Recorded zero with no required query family. A run with no searches counted as a real zero. Class: an empty self-report accepted as evidence.
- Misfiled execution passed the stage it was judged against. The gate never compared the stage it was judging with the stage the execution claimed to belong to. Class: stage binding unchecked at the root.
- Recorded zero claiming families never searched. `completed_query_families` was taken at face value. Class: self-reported completion not verified against calls.
- Run-level threshold counting stages never evaluated. The sum used a different set of executions than the gate. Class: summing over a wider set than the gate judged.
- Misfiled execution hid its status code. The early return dropped the status violation, so a misfiled failed stage stopped blocking promotion. Class: an early return that discards a blocking code.
- Naive stage timestamps switched the timeout check off. `_elapsed_seconds` returned `None` and the check was skipped. Class: an optional field not validated at the boundary.
- Run-level threshold counted a misfiled execution. The intersection was on the mapping key, never compared to the execution's own `stage`. Class: a mapping key not compared to the value's own field.
- Metrics counted artifacts the gate rejected. `_sum_items` summed `item_count` without checking `valid` or traceability. Class: the metric and the gate disagree about the same artifact.
- Promotion applied a run result from another run. `run_id` was never compared. Class: run binding unchecked.
- Contract that could never promote accepted. The validator did not mirror the hard-requirement checks for the promotion flags. Class: validation missing a derived impossibility.
- Gate raised on a malformed URL. `urlparse` raised on `http://[::1`. Class: an unguarded parse in a metric.
- Two predicates read artifacts without checking traceability. `nearest_neighbor_checked` and `duplicate_candidates_reviewed` checked only `valid`. Class: an inconsistent standard inside one registry.
- Validator accepted a stage serving the reserved `promotion` gate key. Class: missing reserved-key check.
- Promotion applied a run result judged under another contract. The contract identity was unchecked. Class: contract binding unchecked.
- Run-level counter-evidence threshold ignored a recorded zero. The stage-level fallback was not mirrored at run level. Class: a level mismatch in a special-case rule.
- Contract file not UTF-8 escaped as a codec error. `read_text` sat inside the same try as `safe_load`. Class: an uncaught decode error.
- `non_empty_queries` accepted a whitespace-only query. Class: two definitions of "is this a query?" in one module.
- Timeout message asserted more than the code knew. Class: over-claiming in a message.
- Run-level counter-evidence zero taken from any stage. Round 9's fallback was scoped to any execution, not the one serving the gate key. Class: scoping error in a fallback.
- Run-level thresholds summed per-stage distinct counts. The same host, query, or family counted once per stage. Class: summing distinct values.
- Budget checked against the tracker's own spec, not the contract's. A caller could pass a looser tracker. Class: authority mismatch between contract and ledger.
- Artifact naming another stage accepted as this stage's output. `ArtifactRecord.stage` was read nowhere. Class: artifact stage binding unchecked.
- Timeout message understated a proven overrun. One message covered two cases. Class: a message that does not distinguish cases.
- Contract fingerprint sensitive to list order. `required_stages: [a, b]` and `[b, a]` fingerprinted differently. Class: a non-canonical hash.
- Misfiled artifact counted towards a threshold. `_counted_artifacts` checked the call's stage, not the artifact's own stage. Class: the stage rule not applied to the artifact.
- `evaluate_run` raised on an invalid contract. The fingerprint was computed on the invalid path and an unserialisable extra field raised. Class: work done on a path that should return early.
- A NaN budget amount switched a dimension off. Every comparison against NaN is false. Class: non-finite not refused on the quantity side.
- A stage that finished before it started satisfied the timeout check. A negative duration is not greater than any timeout. Class: a negative duration treated as provable.
- Misfiled artifact satisfied a quality check. The two artifact-reading predicates did not carry the stage binding the metrics had. Class: the stage rule not shared.
- Validator did not check quality-check kinds. The rule covering thresholds was missing for quality checks. Class: validation gap.
- Run-level threshold on a minimum metric could never fail. `neighbors_checked_per_cluster` is a minimum, but the run-level value was the sum. Class: summing a minimum.
- A call filed under another stage counted as this stage's evidence. The distinct metrics, predicates, and required-tools check all counted it. Class: foreign call counted.
- A refused budget amount had no name. `record` and `guard` raised a bare `ValueError`. Class: unnamed refusal.

### 3.3 What was already adjudicated

The harden prompt lists three dispositions the security pass challenged and the run declined: no logging in the package, no length or count bounds on the models, and the budget violation naming the dimension rather than the stage. The run held those. The review passes also recorded the lean findings as deferred: an unreachable unknown-predicate branch and six duplicated early-return blocks in the gate.

## 4. What was deliberately left, and why

- Budget scope naming. `BudgetTracker.exceeded_against` returns dimension names, and the tracker does not attribute an overrun to a scope, so the gate cannot name a culprit stage. Naming the scope needs the tracker to change. Later wave.
- No length or count bounds on the models. The contract is operator-controlled YAML and the evidence comes from internal collectors, so an unbounded list is a memory risk rather than a security one on a local single-operator tool.
- No logging in the package. Two of the 48 modules under `src/product_hunter/` use `logging`, and both are unattended process loops with no caller to log for. Every other module raises or returns structured values and lets the caller decide. The research package returns structured results, and its consumer does not exist yet.
- A hand-built run result keeps working. `RunGateResult.contract_fingerprint` defaults to an empty string, and an empty fingerprint is not compared with anything. A result that `evaluate_run` did not produce is not blocked on the contract binding. A run judged under an invalid contract also carries an empty fingerprint, because no verdict was reached, and a promotion against that contract is blocked by `contract_invalid` before the fingerprint is read.
- A hand-built `BudgetSpec` accepts a non-positive limit. The contract path refuses it. A spec built outside the contract is outside the trust boundary in the same way a hand-built run result is, and moving the error to model construction would change what an existing test asserts.
- The zero-result predicate compares family names verbatim. It neither trims them nor discards a name that is only whitespace, while the two metrics do both. A recorded zero that names `"alpha "` does not match a search call that carries `"alpha"`, and a family name of `"   "` counts as searched. The zero path is stricter about the family having been searched and looser about the exact spelling.
- `require_claim_evidence_links` is satisfied by a run with no claims. The vacuous reading is deliberate: a run with no claims has no unlinked claim.
- The two tool-call metrics count every call the execution holds. `tool_calls` and `successful_tool_calls` were left counting the whole execution, so they report the activity the execution recorded rather than the activity of the stage. No threshold reads them, so they change no verdict.
- The lean findings. The unreachable unknown-predicate branch and the duplicated early-return blocks in the gate. A refactor of a fail-closed gate late in the wave is a worse trade than the lines it saves. The branch stays because the gate promises never to raise for a failed gate.
- Three hard-requirement flags enforced only through the stage gate. `require_provenance` is read by `evaluate_stage`. `require_qdrant_check` and `require_duckdb_analysis` are read by contract validation, which requires a stage to declare the tool, and the stage gate's `tool_missing` check is what proves the tool ran. `evaluate_promotion` reads none of the three, so with `promotion.require_all_stages_passed: false` a stage that failed on provenance, or on a qdrant or duckdb tool that never ran, does not block promotion. The shipped contract sets the flag to true. Section 7.5 of the spec states this as a known limit.
- The stopping policy and the retry policy are carried by the contract and read by no code in this wave. The gate does not decide when a run has enough evidence and it does not retry.

## 5. Known residuals

- No live research run has exercised this gate. Every input in the 141 tests is a fixture written by hand.
- Three hard-requirement flags are not enforced at promotion time. `require_provenance`, `require_qdrant_check`, and `require_duckdb_analysis` are enforced through the stage gate only. The promotion check carries a stage failure forward only while `promotion.require_all_stages_passed` is set. The shipped contract sets the flag to true, so the shipped baseline is not exposed to it.
- The budget limits in the shipped contract are starting values, not measured ones. They came from the spec's baseline, and no run has produced a spend figure to check them against.
- The budget violation names the dimension, not the stage that overran. The overrun is measured against the contract's budgets, so the limit is right, and a reader still cannot tell which stage went over from the violation alone.
- The threshold values have not been validated against a labelled corpus. They are a design baseline.
- The package has no caller. Nothing outside `src/product_hunter/research/` imports it, no CLI command calls it, and there is no worker handler. The gate is reachable as a library and gates nothing yet.
- The persistence protocols have no implementation. `ToolCallStore` and `GateResultStore` are `Protocol` classes, and nothing in the product satisfies them. The migration `0003_research_gate.sql` is a plan in a docstring and the file does not exist.
- The `promotion` gate key has no stage of its own. It is evaluated at run level, and a threshold block under it is what guards promotion.
- The gate is stricter than the contract in one place. A stage that declares no required tools can hold artifacts the gate accepts and the metric refuses, because nothing ties an untraceable artifact to a tool call. It fails closed. The metric docstring says so and a test pins it.
- The wave report recorded the wave as not committed, because the tree was shared and dirty. It is now committed as `f5af3ae`.

## 6. What the next wave inherits

The persistence wave. It writes the tool calls to SQLite.

It inherits the package: `src/product_hunter/research/`, seven files. Fix rounds 11, 12, and 13 changed five of them: `gate.py`, `predicates.py`, `budget.py`, `contract.py`, and `__init__.py`. `evidence.py` and `persistence.py` are as the wave wrote them, and `__init__.py` carries the exports, one of which round 13 added.

It inherits the planned migration `db/migrations/0003_research_gate.sql`, with the two tables written out in the `persistence.py` docstring: `tool_calls` and `stage_gate_results`. The number is 0003 because 0002 is taken by `0002_model_routing.sql`. The `run_id` foreign key points at `research_runs(run_id)`, which `0001_initial.sql` already creates.

It inherits the shapes to write. `ToolCallProvenance` has fourteen fields, ten required and four optional. `GateResult` carries the run id, the stage, the verdict, the violations, and the metrics. `RunGateResult` carries the contract fingerprint of the contract it was judged under, and `evaluate_promotion` refuses a result judged under another contract, so a stored result has to keep the fingerprint or the binding is lost. The fingerprint is a hash of a canonical form of the contract, so a stored fingerprint has to be recomputed the same way when it is checked. A run judged under an invalid contract reaches no verdict and carries no fingerprint, so an empty fingerprint in a stored row means either that the row was hand-built or that the contract was invalid at the time.

It inherits the handle. `tool_call_id` is the primary key of the planned table and the handle an artifact points back to. The gate's derivation check depends on the stored value being the same handle the artifact carries, and so does the metric that counts the artifact.

It inherits the untrusted-text rule. A violation message and its detail mapping hold ids, names, counts, and fixed phrases, and nothing else. The stage error text and the artifact invalid reason are not in them, on purpose: the security pass in this wave found both reaching a violation detail. If the persistence wave wants them, it reads them from the `StageExecution` and the `ArtifactRecord` and writes them under its own rules.

It inherits the residuals above: no live research run has exercised the gate, the budget limits and threshold values are starting values, a budget violation names the dimension and not the stage, the package has no caller, the `promotion` gate key has no stage of its own, and three hard-requirement flags are enforced through the stage gate only.

What it must do first: implement the migration and the two store protocols, so tool calls and gate results can be written and read back. Until that exists, the gate gates nothing and the provenance has nowhere to live.

## 7. Debts

There are no `shortcut:` or `ponytail:` comments in `src/product_hunter/research/`, in `config/research_contract.yaml`, or in the four test files. A search for `shortcut`, `ponytail`, `TODO`, `FIXME`, `XXX`, and `HACK` across those returns nothing.

The deferrals listed under section 4 are this wave's debts. They are recorded here rather than as markers in the code, because the code carries no such markers.

## 8. What was not checked

- The code was not re-reviewed by this node. This node wrote a report. A further review pass ran in parallel with the productionize pass and its output was not seen by that pass, so nothing from it is recorded in the wave report either.
- No research run was executed, so nothing here is evidence that the gate behaves correctly against a real stage.
- The other 839 tests were not read. The 980 figure is the suite's result, not a review of it.
- `docs/reasonix/specs/Product-Hunter-Full-Spec.md` was not re-read against the code. The baseline threshold values and the stage requirements in `config/research_contract.yaml` were traced back to the spec's blocks, and the rest of the spec was taken as the design of record.

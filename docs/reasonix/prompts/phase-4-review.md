# Review prompt: Phase 4 Wave 1, research contract and tool gate foundation

**RUN_ID:** phase4-20261021-resume-01
**Node:** REVIEW (stage 7), rung 1
**You are:** a fresh-context reviewer. You did not write this code and you have
no stake in it. Your job is to prove it wrong. You do not edit any file.

## What this change is

A new Python package, `src/product_hunter/research/`, that lets a worker
decide in code whether a research stage really happened. An agent instruction
promising that a stage ran is not enforcement. This package is the enforcement
substrate: a typed contract of what a research run must do, a gate that turns
evidence into a pass or fail with stable violation codes, tool-call provenance
records, and a budget model that refuses the call that would break a limit.

It ships with `config/research_contract.yaml`, the baseline contract, and four
test files under `tests/test_research_*.py`.

The whole package is new. There is no diff against a previous version of these
files, so read the files as they stand.

| File | What it does |
|---|---|
| `research/evidence.py` | Provenance, artifact records, stage executions, claims. Data plus validation. |
| `research/predicates.py` | The metric and predicate registries. Pure functions over a stage execution. |
| `research/budget.py` | Budget dimensions, limits, a tracker that blocks the next call. |
| `research/contract.py` | The contract schema, contract validation, YAML loading. |
| `research/gate.py` | Violation codes, and the stage, run, and promotion evaluators. |
| `research/persistence.py` | Two store Protocols for a future SQLite wave. No implementation. |
| `research/__init__.py` | Public exports. |

## The design of record

`docs/reasonix/specs/phase-4-research-contract.md` is the approved design:
schema, validation rules, gate check order, violation codes, the metric and
predicate registries, the budget model, the persistence plan, the test matrix,
and the acceptance criteria.

`docs/reasonix/plans/phase-4-research-contract.md` is the build plan.
`docs/reasonix/handoffs/phase-4-research-contract-handoff.md` is the handoff,
with the repo map and the conventions.

Read the spec and the code, then judge the code against the spec and against
the connected code, not against the diff alone.

## What the gate must never do

Fail open. A gate that returns `passed=True` when it should not is the worst
outcome in this package, because a promotion decision rests on it. Read the
gate's check order in spec section 7.2 and check the code follows it.

## What to look for

1. **Correctness.** Walk the gate's check order and the contract's twelve
   validation rules against the code. Find the case where the code and the
   spec disagree, or where the code does the right thing for the wrong reason.
   Check the edge cases: an empty contract, a stage that never ran, a stage
   that ran but produced nothing, a threshold of zero, a metric that cannot be
   computed, a budget dimension with no limit, a zero-result outcome whose
   query families were never attempted.
2. **Safe.** Untrusted text must not reach a violation message or a violation
   detail, because a later wave writes those to a database. Spec section 7.1
   allows ids, names, counts, and fixed phrases. Audit every `detail=` mapping
   and every `reasons.append` in `gate.py` yourself.
3. **Load.** The expected load is a local single-operator tool, not a service.
   Judge against that load and say which load you assumed. If a finding only
   matters at a load this tool will never see, say so.
4. **Tested.** Read the four test files. Find the risky behaviour with no test
   that fails when it breaks. A test that asserts a tautology is a gap, not a
   test. Check that the tests assert specific outcomes, not just that
   something raised.
5. **Fast.** The suite is 834 tests in about 11 seconds. Say if anything is
   pathologically slow. Small wins are suggestions, not findings.
6. **Lean.** Name code that should not exist, should be smaller, should reuse
   something the repo already has (name the path), or should be stdlib instead
   of new code. Say how many lines could go.

## What is already adjudicated, so you do not re-report it

The code has been through a security and observability pass. These were raised
and settled. If you disagree, disagree with the evidence, not by restating
the point.

- **No logging in the package.** Only 2 of the 48 modules under
  `src/product_hunter/` use `logging`: `db/scheduler.py` and `db/worker.py`.
  Both are unattended process loops with no caller to log for them. Every
  other module raises or returns structured values and lets the caller decide.
  The research package returns structured results, and its consumer does not
  exist yet.
- **No length or count bounds on the models.** The contract is operator
  controlled YAML and the evidence comes from internal collectors, so this is
  a memory risk, not a security one, on a local tool.

## The four known spec/code disagreements

The spec is half-synced with the code. These are known and the code has been
judged authoritative. Stage 8 rewrites the spec to match. Do not report these
four as code findings. Do report any OTHER divergence you find, because that
one is not on the list.

1. Spec section 10 says the extraction booleans become
   `hard_requirements.require_quote_verbatim` and `require_source_span`.
   Spec section 4.3, the shipped YAML, and the code all say they are absent
   until the extraction wave. `HardRequirements` is `extra="forbid"`.
2. The metrics in spec section 6.1 read a fixed set of artifact kinds. The
   `required_outputs` in the contract use a different vocabulary. The contract
   has now been aligned so each stage declares the kinds its own thresholds
   read, and one test pins that alignment.
3. The planned migration is now `0003_research_gate.sql`, because `0002` was
   taken by `0002_model_routing.sql`.
4. The code is stricter than the spec in three places: a stage that has not
   produced a result reports `stage_missing`, a budget overrun is attached to
   every stage including one that failed or never ran, and promotion is
   blocked when the run is over budget.

## Also check

- **The spec against the tests and the code, not only the code against the
  spec.** If the spec promises something the code does not do, or the tests
  do not cover, that is a finding. Stage 7 compares the change to the tests
  and the docs, not to the source alone.
- **The shipped contract.** `config/research_contract.yaml` is a deliverable,
  not configuration somebody else owns. Is it coherent? Does every stage have
  a gating mechanism? Does every threshold have a stage behind it? Does the
  budget make sense against the thresholds?
- **The test matrix in spec section 11.** Go down it. Say which case is
  missing, if any.

## Rules

- Do not edit any file. Report only.
- Another agent is working in the same tree on unrelated files
  (`src/product_hunter/llm/`, `src/product_hunter/eval/`, `src/product_hunter/db/`,
  `src/product_hunter/registry.py`, `src/product_hunter/runtime/`, and their
  tests). Ignore them.
- Every finding needs a concrete case: this input or situation leads to this
  wrong result. No case, no finding. Re-read the lines before you report and
  confirm the caller exists, the value can really be empty, and the code is
  really reached.
- Do not report style taste. Do not write "consider" or "might be worth".
- If you find nothing, say you found nothing and say what you checked. A clean
  review is a valid result and a valuable one.
- The code gate is `uv run pytest`, green at 834 passed.

## Output

Start with `What this change does:` in two or three sentences.

Then the findings in three groups, skipping empty groups:

- **Must fix:** a bug, a security hole, data loss, or something that breaks at
  the expected load.
- **Should fix:** risky code with no test, real slowness, duplication, a
  function that mixes jobs, code that should not exist.
- **Nice to have:** small speed-ups, shorter forms.

Number the findings across all groups so they can be referred to by number.
Every finding has four parts: what this is, what the problem is, the smallest
fix, and what happens if it is skipped.

End with:

- `VERDICT: ship` or `VERDICT: fix <numbers> first`
- `Lean: -<N> lines possible.` when lean findings exist
- `Not checked:` one line if something mattered and you could not check it

---

# Re-review

**RUN_ID:** phase4-20261021-resume-01

Your first pass returned `VERDICT: fix 1, 2 first`. Both were fixed, along with
five of the Should-fix items. This is the re-review: judge the code as it
stands now and give the final verdict.

## What changed

| Fix | File | Change |
|---|---|---|
| M1 | `research/gate.py` | `evaluate_promotion` now blocks when the run carries an unresolved run-level `evidence_threshold_unmet` violation, that is a violation with `stage is None`. It does not gate on `run_result.passed`, so `promotion.require_all_stages_passed: false` still works. The budget check is untouched, so no fact is reported twice. |
| M2 | `research/predicates.py` | `explicit_zero_outcome` returns false when the recorded zero-result outcome names no required query family. |
| S3 | `research/contract.py` | `validate_contract` rejects `claim_evidence_links` in a stage's `quality_checks`. It is a run-level predicate. The constant is `RUN_LEVEL_QUALITY_CHECK`. |
| S5 | `config/research_contract.yaml` | The `discovery` stage requires `fetch` as well as `search`, because its `source_diversity` check and its `min_source_domains` threshold both read fetch calls. |
| S6 | `config/research_contract.yaml`, `research/predicates.py`, `research/__init__.py` | The `clustering` stage declares `neighbor_check` and `duplicate_review`. A `PREDICATE_ARTIFACT_KINDS` mapping names the two predicates that read an artifact kind, and the shipped-baseline alignment test now walks quality checks as well as thresholds. |
| S10 | `config/research_contract.yaml` | `per_run.search_queries` is 160, so the per-run limit is not tighter than the sum of the per-stage limits it contains. |

Five tests were added: one per Must-fix, one for the contract error, one that
pins the `not_started` and `running` statuses, and one that pins the
deliberate reading of spec section 5.2, where an artifact may be untraceable
when the stage declares no required tools.

The code gate is `uv run pytest`, green at 875 passed.

## What you must do now

1. **Verify the fixes.** Read the changed code. Does each fix do what it says,
   at the boundary it says, and not more than it says? Is M1 too narrow, so a
   run-level violation of another kind still promotes a failed run? Does M2
   close the case you reproduced, without closing a case that should stay
   open?
2. **Look for what the fixes broke or introduced.** Five edits, four files, two
   contract changes. A fix that changes the shipped contract's behaviour
   changes what a stage must do.
3. **Give the final verdict.** The wave is ready to ship when the code is
   right, the tests pin it, and the contract it ships is coherent.

## Already settled, do not re-report

- **No logging in the package.** Two of 48 modules under `src/product_hunter/`
  use `logging`, and both are unattended process loops. The research package
  returns structured results and its consumer does not exist yet.
- **No length or count bounds on the models.** A memory risk, not a security
  one, on a local single-operator tool.
- **Finding 7, the budget violation naming a stage.** Deferred on purpose.
  Spec section 8 pins `BudgetTracker.exceeded()` to return dimension names,
  and the tracker does not attribute an overrun to a scope, so the gate
  reports the dimension correctly and cannot name a culprit stage. The
  dimension is what the spec requires the violation to carry. A later wave
  that wants the scope should change the tracker, not the gate.
- **Finding 9, a stage serving the `promotion` gate key.** Deferred on
  purpose. It fails closed rather than open, and the shipped contract does not
  do it.
- **The lean findings 11 and 12.** The unreachable unknown-predicate branch
  and the six duplicated early-return blocks. A refactor of a fail-closed gate
  late in the wave is a worse trade than the lines it saves. The branch stays
  because the gate promises never to raise for a failed gate.

The four known spec/code disagreements are still known. Stage 8 rewrites the
spec to match the code, and the spec is not under review here.

## Rules for this round

- Do not edit any file. Report only.
- If you still find a Must-fix, say so plainly. Do not lower a severity to
  reach agreement, and do not invent a finding to justify another round.
- A clean verdict is a valid result. Say it is clean and say what you checked.

---

# Re-review 2

**RUN_ID:** phase4-20261021-resume-01

Your second pass returned `VERDICT: fix 1 first`. It is fixed, along with the
three Should-fix items and the nice-to-have. This is the third pass and the
last one before the wave ships.

## What changed

| Fix | File | Change |
|---|---|---|
| F1 | `research/gate.py` | `evaluate_stage` now reports `provenance_mismatch` when the execution's own `stage` is not the stage being judged. The check sits after the missing-execution branch, so `evaluate_run` inherits it. The message and detail carry stage names only. |
| F2 | `research/predicates.py` | `explicit_zero_outcome` rejects a zero whose claimed query families are not carried by any successful search call in the execution. The reason reports the count of the unsearched families, never their names. |
| F3 | `tests/test_research_gate.py` | A new test pins that `promotion.require_all_stages_passed: false` still promotes a run whose stage failed, and that no reason mentions the run-level thresholds. No production code changed. |
| F4 | `research/gate.py` | `evaluate_promotion` takes its budget reason from the `budget_exceeded` violations already in `run_result.violations`, one reason per dimension, sorted. The `budget` parameter is gone from its signature; `evaluate_run` keeps its own. |
| F5 | `research/predicates.py` | `PREDICATE_ARTIFACT_KINDS` gains `counter_evidence_present_or_explicit_zero`, mapped to `KIND_COUNTER_EVIDENCE`. `artifacts_valid` stays out because it reads every kind. The comment now says which predicates are absent and why. |

Four tests were added, one per item except F3, which is a test of its own.

One existing test had its fixture completed:
`test_a_recorded_zero_with_every_family_completed_satisfies_the_threshold`
previously claimed two completed query families while making one search call
that carried only one of them. Under F2 that is not a genuine zero, so the
execution now makes a second search call carrying `query_family="solution"`.
The assertions are unchanged. The fixture was previously self-contradictory
and the test was weaker than it read.

The code gate is `uv run pytest`, green at 899 passed.

## What you must do now

1. **Verify the five fixes.** Read the changed code. Does each do what it says,
   at the boundary it says, and not more? Is F1 in the right place in the check
   order, or does it fire before something that should fire first? Is F2 too
   strict, so a legitimate zero is refused? Does F4 report the right thing
   when the run is over budget in more than one dimension?
2. **Look for what the fixes broke or introduced.** F1 adds a new early return
   in the gate's check order. F4 changes a public function's signature.
3. **Give the final verdict.** The wave ships when the code is right, the tests
   pin it, the shipped contract is coherent, and the spec can be written to
   match.

## What is deliberately left, and why

- **Spec section 7.2 still shows the old check order and section 7.4 still
  shows the old `evaluate_promotion` signature.** Stage 8 rewrites the spec to
  match the code. The spec is not under review here.
- **The budget violation names the dimension, not the scope that overran.**
  Spec section 8 pins `BudgetTracker.exceeded()` to return dimension names,
  and the tracker does not attribute an overrun to a scope, so the gate cannot
  name a culprit stage. A later wave that wants the scope should change the
  tracker.
- **No logging in the package, and no length bounds on the models.** Two of 48
  modules under `src/product_hunter/` use `logging`, and both are unattended
  process loops. The bounds are a memory risk, not a security one, on a local
  single-operator tool.
- **The lean findings, the unreachable unknown-predicate branch and the six
  duplicated early-return blocks.** A refactor of a fail-closed gate late in
  the wave is a worse trade than the lines it saves. The branch stays because
  the gate promises never to raise for a failed gate.

## Rules for this round

- Do not edit any file. Report only.
- If you still find a Must-fix, say so plainly. Do not lower a severity to
  reach agreement, and do not invent a finding to justify another round.
- A clean verdict is a valid result. Say it is clean and say what you checked.

---

# Re-review 3

**RUN_ID:** phase4-20261021-resume-01

Your third pass returned `VERDICT: fix 1 first`. It is fixed, along with the two
Should-fix items and the nice-to-have. This is the fourth pass. The question
it has to answer is the one you left open: is the package closed?

## What changed

| Fix | File | Change |
|---|---|---|
| F1 | `research/gate.py` | `_promotion_threshold_violations` now sums over `contract.required_stages` intersected with the executions mapping, so an execution for a stage the contract does not require, and the gate never evaluated, cannot satisfy a run-level threshold. |
| F2 | `research/gate.py` | The three status branches in `evaluate_stage` became one module-level `_status_violation(stage, execution)` helper with the same codes, messages and details. The misfiling branch now reports the status violation next to the `provenance_mismatch`, so a misfiled failed stage still carries `stage_failed` and still blocks promotion when `require_all_stages_passed` is false. |
| F3 | `research/evidence.py` | One module-level `_require_aware(value, field)` implements the timezone rule. The tool call timestamp uses it with the unchanged message. A new validator on `StageExecution.started_at` and `finished_at` uses it with the field name, so a naive timestamp is refused at construction instead of silently switching the timeout check off. |
| F4 | `tests/test_research_contract.py` | A new test asserts that the predicates absent from `PREDICATE_ARTIFACT_KINDS` are exactly the five the comment names, so the next artifact-reading predicate forces a decision. |

Five tests were added, all new. No existing test was modified in this round.

The research package is green on its own: 105 passed in 0.55s.

The full suite is currently red in `tests/test_eval_run.py`, which another
agent rewrote at 14:53. Those three failures are not this package's. Judge
against the research tests.

## What you must do now

1. **Verify the four fixes.** Read the changed code. Does each do what it says,
   at the boundary it says, and not more? Is F2's helper exactly equivalent to
   the three branches it replaced, including the messages and the details?
   Does F3's helper accept `None` for both optional stage timestamps?
2. **Hunt for what is still open.** Three passes have each found a fail-open
   path in this package, so look with that in mind. Specifically: is there any
   input to `evaluate_stage`, `evaluate_run`, or `evaluate_promotion` that
   makes one of them return `passed=True` or `promoted=True` when it should
   not? Is there any input to the contract validator that makes it accept a
   contract it should refuse? Is there any way the run-level threshold guard,
   the promotion guard, or the budget guard can be sidestepped?
3. **Give the final verdict.** If the package is closed, say so and say what
   you checked. If it is not, say exactly what is still open.

## What is deliberately left, and why

- **The spec is out of date on purpose.** Section 7.2 still shows the old check
  order, section 7.4 still shows the old `evaluate_promotion` signature, and
  section 9 still names the old migration number. Stage 8 rewrites the spec to
  match the code. The spec is not under review here.
- **The budget violation names the dimension, not the scope that overran.**
  Spec section 8 pins `BudgetTracker.exceeded()` to return dimension names,
  and the tracker does not attribute an overrun to a scope, so the gate cannot
  name a culprit stage. A later wave that wants the scope should change the
  tracker, not the gate.
- **No logging in the package, and no length bounds on the models.** Two of 48
  modules under `src/product_hunter/` use `logging`, and both are unattended
  process loops. The bounds are a memory risk, not a security one, on a local
  single-operator tool.
- **The lean findings.** The unreachable unknown-predicate branch and the
  duplicated early-return blocks. A refactor of a fail-closed gate late in
  the wave is a worse trade than the lines it saves. The branch stays because
  the gate promises never to raise for a failed gate.

## Rules for this round

- Do not edit any file. Report only.
- If you still find a fail-open path, say so plainly and show the input that
  triggers it. Do not lower a severity to reach agreement, and do not invent a
  finding to justify another round.
- A clean verdict is a valid result. Say it is clean and say what you checked.

---

# Re-review 4

**RUN_ID:** phase4-20261021-resume-01

Your fourth pass returned `VERDICT: fix 1, 2 first`. Both are fixed, along with
three smaller items. This is the fifth pass. Answer the question you have been
answering: is the package closed?

## What changed

| Fix | File | Change |
|---|---|---|
| F1 | `research/gate.py` | A new `_run_executions()` helper is the one place the run-level sum is built. It keeps an execution only when it exists, is not `None`, and `execution.stage` matches the stage it is filed under. `evaluate_run` now types the mapping as `Mapping[str, StageExecution \| None]`, so a `None` is a typed case, not a crash. |
| F2 | `research/predicates.py`, `research/gate.py` | The trace check moved into `predicates.py` as `traces_to_successful_call(execution, artifact)`, and `gate.py` imports it from there, so the import direction stays one way. A new `_counted_artifacts()` filter makes a metric count an artifact only when the artifact is valid AND traces to a successful tool call in the same stage. `_sum_items` and `neighbors_checked_per_cluster` both use it. The gate's own derivation check keeps its conditional reading of spec section 5.2. |
| F3 | `research/gate.py` | `evaluate_promotion` blocks with a `promotion_blocked` violation when `run_result.run_id` is not the run being judged. The reason is a fixed phrase and the detail carries the two ids. |
| F4 | `research/contract.py` | Validation mirrors rule 10 for `promotion.require_counter_evidence_gate` and `promotion.require_competitor_gate`. |
| F5 | `tests/test_research_gate.py` | A new test pins the three `(code, message, detail)` tuples `_status_violation` produces. No production code changed. |

Six tests were added, all new.

**Two existing test fixtures changed, no assertion changed.** The shared
`_contract()` helper in `tests/test_research_contract.py` built a contract with
`require_competitor_search: false`, no stage serving `competitor_search`, and
the default `promotion.require_competitor_gate: true`. That is exactly the
"contract that can never promote" shape F4 rejects, so five assertions that
expect a clean contract went red. The helper now sets
`promotion: {require_competitor_gate: false}`, and the one inline contract in
`test_an_invalid_contract_raises_contract_error_carrying_every_error` sets
both promotion gate flags false so its count of four errors still holds. Every
assertion is byte-identical.

The code gate is `uv run pytest`, green at 927 passed.

## What you must do now

1. **Verify the five fixes.** Read the changed code. Does each do what it says,
   at the boundary it says, and not more? Is the F2 filter equivalent to the
   gate's own artifact rules for an artifact of a required kind? Does F1
   change what the run-level threshold counts in any case it should not?
2. **Hunt for what is still open.** Four passes have each found a fail-open
   path. Look for an input to `evaluate_stage`, `evaluate_run`, or
   `evaluate_promotion` that makes one of them return `passed=True` or
   `promoted=True` when it should not, and for an input to the contract
   validator that makes it accept a contract it should refuse.
3. **Give the final verdict.** If the package is closed, say so and say what
   you checked. If it is not, say exactly what is still open, with the input
   that triggers it.

## One residual you should look at

The implementer reported that `nearest_neighbor_checked` and
`duplicate_candidates_reviewed` read artifacts directly and check only
`artifact.valid`, not traceability. That is the same metric-versus-gate
disagreement F2 fixed, left out of the brief's scope. Say whether it is a
finding, and at what severity.

## What is deliberately left, and why

- **The spec is out of date on purpose.** Section 7.2 still shows the old check
  order, section 7.4 still shows the old `evaluate_promotion` signature, and
  section 9 still names the old migration number. Stage 8 rewrites the spec to
  match the code. The spec is not under review here.
- **The budget violation names the dimension, not the scope that overran.**
  Spec section 8 pins `BudgetTracker.exceeded()` to return dimension names,
  and the tracker does not attribute an overrun to a scope, so the gate cannot
  name a culprit stage. A later wave that wants the scope should change the
  tracker, not the gate.
- **No logging in the package, and no length bounds on the models.** Two of 48
  modules under `src/product_hunter/` use `logging`, and both are unattended
  process loops. The bounds are a memory risk, not a security one, on a local
  single-operator tool.
- **`require_claim_evidence_links` is satisfied by a run with no claims.** The
  vacuous reading is deliberate: a run with no claims has no unlinked claim.
- **The lean findings.** The unreachable unknown-predicate branch and the
  duplicated early-return blocks. A refactor of a fail-closed gate late in
  the wave is a worse trade than the lines it saves. The branch stays because
  the gate promises never to raise for a failed gate.

## Rules for this round

- Do not edit any file. Report only.
- If you still find a fail-open path, say so plainly and show the input that
  triggers it. Do not lower a severity to reach agreement, and do not invent a
  finding to justify another round.
- A clean verdict is a valid result. Say it is clean and say what you checked.

# Implement prompt: Phase 4 Wave 1, research contract and tool gate foundation

**RUN_ID:** phase4-20261021-a307a42
**Node:** IMPLEMENT (stage 4), rung 1
**You are:** a fresh-context implementer. Read the two design documents first, then write the code.

## Read first

1. `docs/reasonix/specs/phase-4-research-contract.md` — the design of record. Schema, validation rules, gate check order, violation codes, metric and predicate registries, budget model, persistence plan, test matrix.
2. `docs/reasonix/handoffs/phase-4-research-contract-handoff.md` — repo map, conventions, blast radius, adapter rules.
3. `docs/reasonix/plans/phase-4-research-contract.md` — build order and the rules the implementation must not break.

Then read the house style before writing: `src/product_hunter/sources.py` and `src/product_hunter/config.py` for pydantic and YAML patterns, `src/product_hunter/db/store.py` for error-class style, and two test files under `tests/` for test style.

## What to build

New package `src/product_hunter/research/` with the seven modules the spec names, plus `config/research_contract.yaml` and the four test files. The spec holds the field lists; implement them exactly.

```
src/product_hunter/research/__init__.py
src/product_hunter/research/evidence.py
src/product_hunter/research/predicates.py
src/product_hunter/research/budget.py
src/product_hunter/research/contract.py
src/product_hunter/research/gate.py
src/product_hunter/research/persistence.py
config/research_contract.yaml
tests/test_research_provenance.py
tests/test_research_budget.py
tests/test_research_contract.py
tests/test_research_gate.py
```

## Constraints

- Stay inside the declared radius. Do not edit `src/product_hunter/db/`, `db/migrations/`, `pyproject.toml`, or any existing module. If you believe a file outside the radius must change, stop and say so instead of changing it.
- No new dependency. `pydantic` and `PyYAML` are already installed. `datetime`, `enum`, `re`, `urllib.parse`, `collections` come from the standard library.
- No network, no filesystem writes outside the repo, no clock dependency in the tests. Timestamps are injected values.
- No collectors, no scraping, no stage execution, no CLI wiring, no SQLite migration. The persistence module holds protocols and the planned migration in its docstring, nothing more.
- Python 3.14, `from __future__ import annotations`, pydantic v2.
- Every test asserts a specific outcome. "Something raised" is not a test.

## Definition of done

```text
uv run pytest
```

Green, with the 18 pre-existing test files still passing. Then report:

- the files written, with line counts
- the gate output verbatim
- anything in the spec you could not implement as written, and why
- what you did not check

## Judgment calls

The spec fixes the schema, the codes, the check order, and the rules. Where it leaves a detail open, choose the smallest thing that satisfies the spec, keep the house style, and say which choice you made in your report. Do not add configuration surface, options, or abstractions the spec did not ask for. A gate that returns a violation is not an exception; only an invalid contract raises.

---

# Fix round 1

**RUN_ID:** phase4-20261021-resume-01
**Node:** IMPLEMENT (stage 4), rung 1, after a HARDEN pass returned `fix first`.

The wave is built and green. You are not building it. You are applying four
fixes to the code that is already there, and adding the four tests that pin
them. Read `src/product_hunter/research/gate.py`,
`src/product_hunter/research/persistence.py`, and
`tests/test_research_gate.py` before you change anything.

## F1. A violation detail must not carry the stage error text

Spec section 7.1: a violation message and its detail mapping hold ids, names,
counts, and fixed phrases, and never raw untrusted text, because a later wave
writes them to the database and a URL can carry a token.

`evaluate_stage` puts `execution.error` into the detail of the `stage_failed`
and the `stage_timed_out` violation. That is prose from the evidence. Remove
the `error` key from both details. The stage name stays. The error text stays
on the `StageExecution` itself, which is where it came from and where the
persistence wave will read it.

## F2. A violation detail must not carry the artifact invalid reason

`ArtifactRecord.invalid_reason` is free text set by whoever produced the
artifact. The `artifact_invalid` violation for an invalid artifact copies it
into the detail as `reason`. Remove that key. Keep `artifact_id` and `kind`.

The other `artifact_invalid` violation, the one for an artifact that does not
trace to a successful tool call, carries no free text. Leave it alone.

## F3. Promotion must honour the hard requirement flags

`HardRequirements.require_counter_evidence` and
`HardRequirements.require_competitor_search` are validated at contract level
but are not read by `evaluate_promotion`. A contract can set the hard flag true
and the matching `promotion.require_counter_evidence_gate` /
`promotion.require_competitor_gate` false, and then the contract promises
counter-evidence enforcement that promotion never applies.

Make `evaluate_promotion` block on a failed counter-evidence or competitor
gate when the hard requirement flag OR the promotion flag is set. Follow the
pattern the file already uses for `require_claim_evidence_links`, which already
combines `contract.hard_requirements.require_claim_evidence_links` with
`contract.promotion.require_claim_evidence_links` in one condition.

The shipped `config/research_contract.yaml` sets both flags true, so its
behaviour does not change.

## F4. The planned migration number collides

`research/persistence.py` names the planned migration
`db/migrations/0002_research_gate.sql`. `db/migrations/0002_model_routing.sql`
already exists. `docs/reasonix/ARCHITECTURE.md` records the collision and says
the next research-gate migration has to be `0003`.

Change the name to `0003_research_gate.sql` in the docstring, in both the prose
and the SQL block. Nothing else in the file changes. The file is not created.

## The four tests to add

Add them to `tests/test_research_gate.py`. Match the file's existing style:
plain functions, plain asserts, no classes, no fixtures beyond `tmp_path`.
The file already has a `_contract(...)` helper that takes `hard_requirements=`
and `promotion=` overrides, a `_execution(...)` helper, a `_codes(result)`
helper, and a `_reasons(result)` helper. Use them.

1. `test_a_failed_stage_violation_does_not_carry_the_error_text`
   A stage that failed with an error containing a secret-looking string. Assert
   the secret is not in the violation detail, and not in the violation message.
2. `test_an_invalid_artifact_violation_does_not_carry_the_invalid_reason`
   An artifact with `valid=False` and an `invalid_reason` containing a
   secret-looking string. Assert the string is not in the violation detail.
3. `test_the_counter_evidence_rule_fires_on_the_hard_requirement_flag`
   `hard_requirements={"require_counter_evidence": True}` and
   `promotion={"require_counter_evidence_gate": False}`, with the
   counter-evidence stage not passing. Assert promotion is blocked.
4. `test_the_competitor_rule_fires_on_the_hard_requirement_flag`
   The same for `require_competitor_search` and `require_competitor_gate`, with
   the competitor gate not passing.

Do not modify any existing test. Do not change the spec or any document under
`docs/`. The spec sync is stage 8 and belongs to another node.

## Rules

- Stay inside the declared radius. You may touch `src/product_hunter/research/gate.py`,
  `src/product_hunter/research/persistence.py`, and `tests/test_research_gate.py`.
  Nothing else.
- No new dependency, no new module, no new configuration surface.
- The smallest complete change. Do not refactor anything around the fixes.

## Definition of done

```text
uv run pytest
```

Green, and the four new tests are in the run. Then report:

- the files you changed, with line counts
- the gate output verbatim
- the four tests you added and what each one pins
- what you did not check

---

# Fix round 2

**RUN_ID:** phase4-20261021-resume-01
**Node:** IMPLEMENT (stage 4), rung 1, after the re-harden.

The re-harden returned `ship` with an empty Must-fix list, and one Should-fix
that is a contract defect, not a code defect. Fix that.

## F5. The shipped contract declares thresholds its own stages cannot satisfy

Spec section 6.1 says a metric that cannot be computed from the stage returns
zero, and a missing artifact is never read as a satisfied threshold. The gate
honours that and fails closed. The defect is in
`config/research_contract.yaml`: four of the five threshold blocks name an
artifact kind that the serving stage never declares in its `required_outputs`,
so those thresholds always read zero and the stage always fails.

The metrics that count artifacts read these kinds:

| Metric | Kind |
|---|---|
| `new_documents` | `document` |
| `candidate_urls` | `candidate_url` |
| `independent_authors` | `author` |
| `independent_communities` | `community` |
| `evidence_spans` | `evidence_span` |
| `counter_evidence_items` | `counter_evidence` |
| `neighbors_checked_per_cluster` | `cluster_neighbors_checked` |

Fix the four stages in the shipped YAML by adding the kinds their own
thresholds read to their `required_outputs`:

- `discovery` reads `document` through `min_new_documents`. Add `document`.
- `investigation` serves the `competitor_search` gate key and reads
  `candidate_url` through `min_candidate_urls`. Add `candidate_url`.
- `validation` reads `author` through `min_independent_authors`, `community`
  through `min_independent_communities`, and `evidence_span` through
  `min_evidence_spans`. Add all three.
- `clustering` reads `cluster_neighbors_checked` through
  `min_neighbors_checked_per_cluster`. Add it.

`counter_evidence` already declares the kind it reads. Leave it alone.

Keep the existing entries in every `required_outputs` list. You are adding to
the lists, not replacing them. Keep the YAML's existing style and its comment
header.

## F6. Expose the metric-to-kind vocabulary

The mapping above is the vocabulary the thresholds read, and it currently lives
only as a literal inside each metric function in
`src/product_hunter/research/predicates.py`. Spec section 6.1 documents it in
prose, so the code and the spec state it twice and cannot be checked against
each other.

Add one module-level mapping in `predicates.py`:

```text
METRIC_ARTIFACT_KINDS: dict[str, str]
```

It maps the seven artifact-counting metrics to the kind each one reads. Use
the `KIND_*` constants that are already defined in that module, so the mapping
cannot drift from the metric functions. Do not include the tool-based metrics
(`distinct_queries`, `query_families`, `source_domains`, `tool_calls`,
`successful_tool_calls`): they count tool calls, not artifacts.

Export it from `src/product_hunter/research/__init__.py`, next to
`METRIC_NAMES` and `PREDICATE_NAMES`, and add it to `__all__` in the same
sorted position the file already uses.

## F7. Pin the alignment with one test

Add one test to `tests/test_research_contract.py`:

```text
test_the_shipped_baseline_declares_the_kinds_its_thresholds_read
```

It loads the shipped baseline with `load_research_contract()` and asserts, for
every gate key that has a threshold block and is served by a stage, that each
threshold field which maps to an artifact-counting metric names a kind that the
serving stage declares in its `required_outputs`.

The test walks: `contract.evidence_thresholds` for the gate keys,
`served_gate_keys(contract)` for the stage behind each gate key,
`THRESHOLD_METRICS` for the metric behind each threshold field, and
`METRIC_ARTIFACT_KINDS` for the kind behind each artifact-counting metric. The
`promotion` gate key is served by no stage, so skip it.

The failure message must name the gate key, the threshold field, the kind, and
the stage, so a failure says which declaration is missing.

This test is the check for F5. Without it, the next edit to the shipped YAML
can reintroduce the same misalignment silently.

## Rules for this round

- Stay inside the declared radius. You may touch
  `config/research_contract.yaml`, `src/product_hunter/research/predicates.py`,
  `src/product_hunter/research/__init__.py`, and
  `tests/test_research_contract.py`. Nothing else.
- Do not change the threshold values. Do not change `required_tools`,
  `quality_checks`, `gate_key`, `required_stages`, the budgets, the stopping
  policy, the retry policy, the promotion block, or `hard_requirements`.
- Do not add a validation rule to `validate_contract`. The gate already fails
  closed on a misaligned contract. A later wave may add such a rule; this wave
  pins the shipped baseline with a test instead.
- Do not modify any existing test. Do not touch any file under `docs/`.
- No new dependency, no new module, no new configuration surface.

## Definition of done

```text
uv run pytest
```

Green, and the new test is in the run. Then report:

- the files you changed, with line counts
- the gate output verbatim
- the new test and what it pins
- what you did not check

---

# Fix round 3

**RUN_ID:** phase4-20261021-resume-01
**Node:** IMPLEMENT (stage 4), rung 1, after the REVIEW returned `fix 1, 2 first`.

An adversarial review of the whole package found two fail-open paths and five
defects worth fixing. Fix them. Every claim below was reproduced before this
prompt was written.

## M1. A run that fails its own run-level thresholds is still promoted

`evaluate_run` evaluates the `promotion` gate key's threshold block at run
level and writes the `evidence_threshold_unmet` violation with `stage=None`
onto the run, so `RunGateResult.passed` becomes `False`.
`evaluate_promotion` then judges promotion using only the per-stage results.
It never reads the run-level violations, so the one threshold block the
schema lets you place at run level, the block that guards promotion itself,
does not guard promotion.

Reproduced:

```text
contract: a valid contract with evidence_thresholds.promotion.min_independent_authors = 10
all stages passing
run.passed      : False
run violations  : [('evidence_threshold_unmet', None)]
PROMOTED        : True
promo violations: []
```

**The fix.** In `evaluate_promotion`, block promotion when the run carries an
unresolved run-level violation. Be surgical about it:

- Only violations whose `stage` is `None` count. The per-stage violations are
  already handled by the stage loop in `evaluate_promotion`.
- Only the `evidence_threshold_unmet` code counts. The budget check is
  already handled separately at the end of `evaluate_promotion`, and you must
  not report the same fact twice.
- Do NOT gate on `run_result.passed`. `promotion.require_all_stages_passed` is
  a documented flag that lets a failed stage through, and `run_result.passed`
  is `False` for a failed stage too. Gating on it would silently override
  that flag.
- One reason is enough. The run-level violations are already in
  `run_result.violations` for anyone reading the run result.
- The reason is a fixed phrase. Do not interpolate free text.

## M2. A recorded zero with no required query families counts as a real zero

`explicit_zero_outcome` computes the missing families as "required families
not in completed families" and accepts the outcome when that set is empty. It
never checks that `required_query_families` is non-empty, and its "no failed
search" guard only rejects searches that failed. A stage with no searches at
all passes both.

Reproduced:

```text
StageExecution(status=succeeded, tool_calls=[], artifacts=[],
               zero_result=ZeroResultOutcome(recorded=True))
explicit_zero_outcome -> (True, 'a zero-result outcome completed every required query family')
```

The full spec says the gate "does not permit an unsearched or failed search
to count as zero evidence", and the function's own docstring says "an
unsearched or failed query is not a zero".

**The fix.** A recorded zero-result outcome that names no required query
family is not a zero. Return `False` with a fixed phrase that says so. Put
the check right after the `recorded` check.

**The scope of this fix.** Do NOT add a check that the completed families are
backed by successful search calls carrying that `query_family`. That would
break an existing test, and the reviewer's reproduced case is the empty
family list. Leave the deeper check for a later wave.

## S3. `claim_evidence_links` cannot fail when a stage uses it as a quality check

`evaluate_stage` builds its predicate context with `claims=()`. The
`claim_evidence_links` predicate counts claims with no evidence references,
so with zero claims it returns `True` unconditionally. A contract can name it
in a stage's `quality_checks`, validation accepts the name, and the check then
enforces nothing.

**The fix.** Reject it in `validate_contract`: a stage's `quality_checks` may
not name `claim_evidence_links`. It is a run-level predicate. The run-level
way to ask for it is `hard_requirements.require_claim_evidence_links` or
`promotion.require_claim_evidence_links`, and the gate already reads both.

The error message must say which stage named it and that the check is
run-level. It is a contract error, like the unknown-predicate error beside it.

## S5. The shipped `discovery` stage declares `search` but two of its gates read `fetch`

`config/research_contract.yaml` gives `discovery` `required_tools: [search]`.
Its `source_diversity` quality check and its `min_source_domains: 3`
threshold both read the `source_domains` metric, which counts hosts among
successful `fetch` calls. The stage therefore cannot pass its own gates, and
`tool_missing` cannot prove the fetches behind them.

**The fix.** Add `fetch` to the `discovery` stage's `required_tools`. Change
nothing else about that stage.

## S6. The shipped `clustering` stage's quality checks read kinds the contract never declares

`clustering` declares `required_outputs: [cluster_assignments,
similarity_checks, cluster_neighbors_checked]` and `quality_checks:
[nearest_neighbor_checked, duplicate_candidates_reviewed]`. Those two
predicates read the artifact kinds `neighbor_check` and `duplicate_review`,
neither of which the stage declares.

**The fix.**

1. In `config/research_contract.yaml`, add `neighbor_check` and
   `duplicate_review` to the `clustering` stage's `required_outputs`, keeping
   the entries it already has.
2. In `src/product_hunter/research/predicates.py`, add a module-level
   `PREDICATE_ARTIFACT_KINDS: dict[str, str]` mapping the two predicates that
   read an artifact kind to that kind. Use the `KIND_*` constants already in
   that module, the way `METRIC_ARTIFACT_KINDS` does. The other six
   predicates read tool calls, run-level claims, or nothing, so they are not
   in the mapping.
3. Export `PREDICATE_ARTIFACT_KINDS` from
   `src/product_hunter/research/__init__.py` next to `METRIC_ARTIFACT_KINDS`.
4. Extend the existing test
   `test_the_shipped_baseline_declares_the_kinds_its_thresholds_read` in
   `tests/test_research_contract.py` so it also walks each stage's
   `quality_checks` and asserts that a predicate which reads an artifact kind
   has that kind in the stage's `required_outputs`. Rename the test to
   `test_the_shipped_baseline_declares_the_kinds_its_thresholds_and_checks_read`.

## S10. The per-run search-query limit is below the sum of the per-stage limits

`config/research_contract.yaml` sets `per_run.search_queries: 120`. The
per-stage limits for `discovery` (40), `evidence_collection` (40),
`validation` (20), `counter_evidence` (20), and `investigation` (30) sum to
150. The per-run cap always binds first, so the per-stage limits for the
later stages are unreachable.

**The fix.** Raise `per_run.search_queries` to `160`, so the per-run limit is
not tighter than the per-stage limits it contains. Change no other budget
number. `fetch_requests` is already consistent: 140 against a per-run 200.

## The tests to add

Add them to `tests/test_research_gate.py` and
`tests/test_research_contract.py`. Match each file's existing style: plain
functions, plain asserts, the `_contract`, `_execution`, `_call`, `_artifact`,
`_codes` and `_reasons` helpers that are already there.

1. `test_a_run_that_fails_its_run_level_thresholds_is_not_promoted`
   A contract carrying a `promotion` threshold block that cannot be met, with
   every stage passing. Assert `run.passed is False` and
   `promotion.promoted is False`, and that a `promotion_blocked` violation
   names the run-level thresholds.
2. `test_a_recorded_zero_with_no_query_families_does_not_satisfy_the_threshold`
   A stage with `zero_result=ZeroResultOutcome(recorded=True)` and no
   families. Assert the threshold is not met.
3. `test_a_stage_may_not_use_the_run_level_claim_evidence_check`
   A contract with `claim_evidence_links` in a stage's `quality_checks`.
   Assert `validate_contract` returns an error naming the stage.
4. `test_a_stage_that_has_not_produced_a_result_is_stage_missing`
   A stage with status `not_started`, and one with status `running`. Assert
   both report `stage_missing`.
5. `test_an_artifact_may_be_untraceable_when_the_stage_declares_no_tools`
   A stage with `required_tools: []`, a `required_outputs` kind, and an
   artifact with no `produced_by_tool_call_id`. Assert the stage passes.
   This pins the deliberate reading of spec section 5.2: the derivation check
   applies when the stage declares required tools.

Do not modify any existing test except the one named in S4, which is renamed
and extended. Do not touch any file under `docs/`.

## Rules for this round

- Stay inside the declared radius. You may touch `src/product_hunter/research/gate.py`,
  `src/product_hunter/research/predicates.py`,
  `src/product_hunter/research/contract.py`,
  `src/product_hunter/research/__init__.py`,
  `config/research_contract.yaml`, `tests/test_research_gate.py`, and
  `tests/test_research_contract.py`. Nothing else.
- No new dependency, no new module, no new configuration surface.
- The smallest complete change. Do not refactor anything around the fixes.
- Do not change the shipped threshold values, the stage list, the
  `required_stages`, `hard_requirements`, `stopping`, `retry`, or `promotion`.

## Definition of done

```text
uv run pytest
```

Green, and the new tests are in the run. Then report:

- the files you changed, with line counts
- the gate output verbatim
- each new test and what it pins
- what you did not check

---

# Fix round 4

**RUN_ID:** phase4-20261021-resume-01
**Node:** IMPLEMENT (stage 4), rung 1, after the second REVIEW returned
`VERDICT: fix 1 first`.

The second review found one more fail-open path, reachable through the shipped
contract, and four smaller items. Every claim below was reproduced before this
prompt was written.

## F1. The gate never checks that the execution belongs to the stage it judges

`evaluate_stage(contract, run_id, stage, execution)` takes the stage name and
the execution as separate arguments and never compares them.
`StageExecution.stage` is the field that says which stage an execution is, and
the gate ignores it.

The gate polices run and stage binding for the leaves: `mismatched_calls`
rejects a tool call from another run or stage, and `_traces_to_successful_call`
requires an artifact to trace to a call in `execution.stage`. The root of that
tree is unchecked.

Reproduced against the shipped contract:

```text
execution.stage = discovery
judged as discovery            -> passed: False  ['artifact_missing', 'evidence_threshold_unmet', ..., 'quality_check_failed']
judged as evidence_collection  -> passed: True   []
```

The same evidence fails the discovery gate and passes the evidence_collection
gate when it is misfiled. `evidence_collection` declares the same tools as
`discovery`, requires outputs `discovery` also produces, has one quality check
`discovery` satisfies, and has no threshold block, so there is no floor to
fail. `evaluate_run` has the same hole: it does `executions.get(stage)` and
never checks the value's own `stage` field.

**The fix.** In `evaluate_stage`, right after the branch that handles a missing
execution, reject an execution whose `stage` is not the stage being judged.

Use the `provenance_mismatch` violation code. It is already in the violation
list, and spec section 7.1 defines it as "provenance belongs to another run or
stage", which is exactly this case. The message and the detail carry stage
names only, so the untrusted-text rule holds. Put the check in
`evaluate_stage`, not in `evaluate_run`: `evaluate_run` calls `evaluate_stage`
for every required stage, so one check covers both.

## F2. A recorded zero still claims query families that were never searched

`explicit_zero_outcome` checks that the outcome names at least one required
query family, but it never compares the claimed families against the stage's
own search calls. `completed_query_families` is a self-report and is taken at
face value. The function's own docstring says "an unsearched or failed query
is not a zero", and spec section 4.2 says "A failed or unsearched query never
counts as zero".

Reproduced:

```text
tool_calls: one successful search carrying query_family "unrelated"
zero_result: recorded=True, required=["problem","solution"], completed=["problem","solution"]
-> 0 violations, passed=True
```

**The fix.** Every family in `outcome.completed_query_families` must appear as
the `query_family` of at least one successful search call in the execution. If
any does not, the outcome is not a zero.

Report only the COUNT of the unsearched families. The family names are free
text from the evidence and must never reach the detail, the same way the
existing missing-family check reports a count instead of the names.

**One existing test needs its fixture completed, and this is deliberate.**
`test_a_recorded_zero_with_every_family_completed_satisfies_the_threshold`
currently declares two completed families while its execution makes one search
call with no matching family. Under the new rule that is not a genuine zero, so
the fixture has to become one: give the execution the search calls that carry
the `query_family` values it claims. The assertion does not change, and it
does not get weaker. It still asserts that a genuine zero passes.

Do not weaken any other existing test.

## F3. The behaviour that `require_all_stages_passed: false` still works is not pinned

The M1 fix in round 3 deliberately does not gate on `run_result.passed`, so
that `promotion.require_all_stages_passed: false` keeps its meaning. No test
in the package sets that flag, so the behaviour is one condition away from
breaking with every test still green.

**The fix.** One test: a contract with
`promotion={"require_all_stages_passed": False}` and a stage whose threshold
is unmet. Assert `promotion.promoted is True`, and that no promotion reason
mentions the run-level thresholds.

## F4. The promotion budget rule depends on a caller remembering an argument

`evaluate_promotion` takes a `budget: BudgetTracker | None` argument and takes
the "the run is over its X budget" reason from `budget.exceeded()`. The same
fact is already in `run_result.violations` as a `budget_exceeded` violation on
every stage, because `evaluate_run` computed it. Forget the tracker at
promotion and the rule disappears silently. The shipped contract cannot reach
it, because `require_all_stages_passed: true` makes the over-budget stages
fail anyway, but `require_all_stages_passed: false` is a supported setting.

**The fix.** Derive the reason from the run result, not from the tracker:

- Take the `budget_exceeded` violations in `run_result.violations`.
- Collect the dimensions from their `detail` and drop the empty ones.
- Report one reason per dimension, sorted, with the same fixed phrase the
  code uses today.

Then the `budget` parameter of `evaluate_promotion` has no job left. Remove
it from the signature and from the docstring. `evaluate_run` keeps its
`budget` parameter: that is where the fact is created.

Check every caller before you remove it. The only callers are the tests and
the package's own exports.

## F5. The `PREDICATE_ARTIFACT_KINDS` comment is wrong about two predicates

The comment says the predicates that are absent from the mapping read "tool
calls, run-level claims, or nothing". Two of them read artifacts:
`artifacts_valid` reads every artifact, and
`counter_evidence_present_or_explicit_zero` reads `counter_evidence` artifacts
through `counter_evidence_items`. Because the second is absent from the
mapping, the alignment test cannot catch a stage that declares that check
without declaring the `counter_evidence` kind.

**The fix.** Add `counter_evidence_present_or_explicit_zero` to
`PREDICATE_ARTIFACT_KINDS`, mapped to `KIND_COUNTER_EVIDENCE`. Leave
`artifacts_valid` out, because it reads every kind and cannot be mapped to
one. Correct the comment so it says which predicates are absent and why.

The shipped `counter_evidence` stage already declares the `counter_evidence`
kind, so the alignment test stays green.

## The tests to add

1. `test_a_misfiled_execution_does_not_pass_the_stage_it_is_judged_against`
   An execution whose `stage` is one stage, judged against another. Assert the
   result reports `provenance_mismatch` and does not pass. Put it in
   `tests/test_research_gate.py`.
2. `test_a_recorded_zero_whose_families_were_never_searched_is_not_a_zero`
   A stage whose zero claims families that no successful search call carries.
   Assert the counter-evidence threshold is not met. Put it in
   `tests/test_research_gate.py`.
3. `test_promotion_still_passes_when_a_stage_fails_and_all_stages_need_not_pass`
   The F3 case. Put it in `tests/test_research_gate.py`.
4. `test_promotion_is_blocked_when_the_run_is_over_budget`
   A run whose budget is exceeded, evaluated for promotion without a tracker
   argument. Assert promotion is blocked and the reason names the dimension.
   Put it in `tests/test_research_gate.py`.

Do not touch any other existing test. Do not touch any file under `docs/`.

## Rules for this round

- Stay inside the declared radius. You may touch `src/product_hunter/research/gate.py`,
  `src/product_hunter/research/predicates.py`, and
  `tests/test_research_gate.py`. Nothing else.
- No new dependency, no new module, no new configuration surface.
- The smallest complete change. Do not refactor anything around the fixes.

## Definition of done

```text
uv run pytest
```

Green, and the new tests are in the run. Then report:

- the files you changed, with line counts
- the gate output verbatim
- each new test and what it pins
- the exact fixture change you made to
  `test_a_recorded_zero_with_every_family_completed_satisfies_the_threshold`
  and why it is not a weakening
- what you did not check

---

# Fix round 5

**RUN_ID:** phase4-20261021-resume-01
**Node:** IMPLEMENT (stage 4), rung 1, after the third REVIEW returned
`VERDICT: fix 1 first`.

The third review found one more fail-open path and three smaller items. Every
claim below was reproduced before this prompt was written.

## F1. The run-level promotion threshold counts stages the gate never evaluated

`_promotion_threshold_violations` sums the metric over every value in the
`executions` mapping the caller passed to `evaluate_run`. But `evaluate_run`
only evaluates `contract.required_stages`, and a stage outside
`required_stages` is not part of the run: `validate_contract` rejects a stage
contract for one, because a contract for a stage that can never run is a
configuration bug. The sum uses a different set of executions than the gate
does.

Reproduced:

```text
contract: one required stage, plus evidence_thresholds.promotion.min_independent_authors = 10
executions: the required stages, plus one execution for a stage the contract does not require,
            carrying a 'author' artifact with item_count 10, valid=False, and a tool call with no tool_call_id
run.passed  : True
PROMOTED    : True
```

The gate never evaluated that extra stage, so its invalid artifact and its
missing provenance were never reported, and the sum picked up its count
anyway. The run-level threshold guard can be satisfied by evidence the gate
never looked at.

**The fix.** Sum over the stages the run actually evaluated, which is
`contract.required_stages`, not over the whole mapping. For a stage in
`required_stages` that is absent from the mapping, there is no execution to
count, so it contributes nothing.

One line. No existing test changes: the existing run-level threshold test
passes exactly the required stages.

## F2. A misfiled execution hides the status code that always blocks promotion

The F1 fix in round 4 returns from `evaluate_stage` as soon as it finds an
execution filed under the wrong stage name, and the only violation it reports
is `provenance_mismatch`. The execution's own status is never read.

`evaluate_promotion` decides whether a stage blocks promotion by looking for
`stage_missing`, `stage_failed`, and `stage_timed_out` among that stage's
violation codes. So a misfiled failed execution reports only
`provenance_mismatch`, the status code is lost, and with
`promotion.require_all_stages_passed: false` the stage stops blocking
promotion.

**The fix.** In the misfiling branch, report the status violation next to the
`provenance_mismatch`, not instead of it. A misfiled execution that failed is
still a failed execution, and the contract author did not ask for that
failure to be ignored.

Map the execution's status the way the rest of `evaluate_stage` does:
`failed` is `stage_failed`, `timed_out` is `stage_timed_out`, and
`not_started` or `running` is `stage_missing`. A succeeded execution has no
status violation to report, so it reports the `provenance_mismatch` alone.

## F3. Naive stage timestamps switch the timeout check off

`ToolCallProvenance.timestamp` is validated as timezone-aware, and the spec
says naive timestamps are rejected. `StageExecution.started_at` and
`finished_at` carry no such rule, so `_elapsed_seconds` returns `None` and
the timeout check is skipped. A stage with `timeout_seconds: 30` that ran 31
seconds reports no violation when its timestamps are naive. A mixed pair, one
naive and one aware, is skipped too.

**The fix.** `StageExecution.started_at` and `StageExecution.finished_at` must
be timezone-aware, the same way the tool call timestamp is.

Extract the awareness check into one module-level helper in `evidence.py` and
use it from both places, so there is a single implementation of the rule. The
error message must name the field that was rejected. The helper must accept
`None`, because both stage timestamps are optional.

The gate's timeout check then works for the aware case, and a naive timestamp
is refused at construction instead of silently disabling the check.

## F4. Nothing pins that `PREDICATE_ARTIFACT_KINDS` covers every artifact-reading predicate

The alignment test skips a quality check whose predicate is missing from the
mapping, so a new artifact-reading predicate added without a mapping entry
silently stops being covered. The comment in `predicates.py` is the only
guard.

**The fix.** One test in `tests/test_research_contract.py` that asserts the
set difference between `PREDICATE_NAMES` and `PREDICATE_ARTIFACT_KINDS` is
exactly the five predicates the comment names, so the next predicate forces a
decision.

The five are the ones the comment lists: the predicates that read tool calls,
`artifacts_valid` because it reads every kind, and `claim_evidence_links`
because it reads run-level claims. Read the comment in `predicates.py` for
the exact list rather than guessing it.

## The tests to add

1. `test_the_run_level_thresholds_count_only_the_stages_the_gate_evaluated`
   A contract with a `promotion` threshold block, one required stage that
   produces no author artifacts, and an extra execution for a stage the
   contract does not require carrying author artifacts. Assert the run does
   not pass and promotion is blocked. Put it in `tests/test_research_gate.py`.
2. `test_a_misfiled_failed_execution_still_reports_the_failure`
   A failed execution filed under the wrong stage name. Assert the result
   reports both `provenance_mismatch` and `stage_failed`. Put it in
   `tests/test_research_gate.py`.
3. `test_a_stage_with_naive_timestamps_is_rejected`
   A stage execution with a naive `started_at` and a naive `finished_at`.
   Assert the model refuses it, and that the error names the field. Put it in
   `tests/test_research_provenance.py`.
4. `test_a_stage_with_a_naive_timestamp_does_not_silently_skip_the_timeout`
   A stage with `timeout_seconds` set, aware timestamps, and a duration past
   the timeout, reports `stage_timed_out`. Put it in
   `tests/test_research_gate.py`.
5. The `PREDICATE_ARTIFACT_KINDS` coverage test from F4.

Do not modify any existing test. Do not touch any file under `docs/`.

## Rules for this round

- Stay inside the declared radius. You may touch
  `src/product_hunter/research/gate.py`, `src/product_hunter/research/evidence.py`,
  `src/product_hunter/research/predicates.py`, `tests/test_research_gate.py`,
  `tests/test_research_provenance.py`, and `tests/test_research_contract.py`.
  Nothing else.
- No new dependency, no new module, no new configuration surface.
- The smallest complete change. Do not refactor anything around the fixes.

## Definition of done

```text
uv run pytest
```

Green, and the new tests are in the run. Then report:

- the files you changed, with line counts
- the gate output verbatim
- each new test and what it pins
- what you did not check

---

# Fix round 6

**RUN_ID:** phase4-20261021-resume-01
**Node:** IMPLEMENT (stage 4), rung 1, after the fourth REVIEW returned
`VERDICT: fix 1, 2 first`.

The fourth review found two more fail-open paths and three smaller items. The
two must-fix items share one root: the metrics read raw evidence that the gate
itself had already rejected. Fix the root, not just the two symptoms.

## F1. The run-level threshold counts a misfiled execution

`_promotion_threshold_violations` sums over `contract.required_stages`
intersected with the executions mapping. The intersection is on the mapping
KEY, and the key is never compared with the execution's own `stage` field. The
stage gate does compare them and returns `provenance_mismatch` for a
misfiled execution, dropping that execution's metrics. The run-level sum then
counts the same execution anyway.

Reproduced:

```text
contract: evidence_thresholds.promotion.min_independent_authors = 10,
          promotion.require_all_stages_passed = False
executions: {"discovery": StageExecution(stage="ghost", ... 10 author artifacts ...),
              "counter_evidence": <passing>, "investigation": <passing>}
run_result.passed : False   (provenance_mismatch on discovery)
PROMOTED          : True
promotion.violations : []
```

A run that failed promotes, and the run-level evidence floor is met only by an
execution the gate refused.

**The fix.** The run-level sum counts an execution only when it exists, is not
`None`, and belongs to the stage it is filed under. Put all three conditions
in the one place the sum is built.

This also fixes a crash: `evaluate_run(contract, "run-1", {"discovery": None})`
on a contract with a `promotion` threshold currently raises `AttributeError`,
because the sum dereferences the `None`. The gate's docstring promises it
never raises for a failed gate.

## F2. The metrics count artifacts the gate rejected

`_sum_items` sums `item_count` over artifacts of a kind without looking at
`artifact.valid`. The gate validates an artifact only when its kind is in the
stage's `required_outputs`, and it reports `artifact_invalid` when the
artifact is invalid. The metric and the gate therefore disagree about the same
artifact: the gate rejects it, the metric counts it.

Reproduced:

```text
contract: one stage, required_outputs ["author"],
          evidence_thresholds.promotion.min_independent_authors = 10,
          promotion.require_all_stages_passed = False
execution: one 'author' artifact, item_count 10, valid=False
run_result.passed : False   (artifact_invalid)
PROMOTED          : True
```

A run-level floor of ten independent authors is met by an artifact the gate
threw out.

**The fix.** A metric counts an artifact only when the artifact is valid AND
it traces to a successful tool call in the same stage. That is the same rule
the gate applies to an artifact of a required kind, so the two agree.

Do this in the metric layer, not in the gate:

- Move the trace check into `src/product_hunter/research/predicates.py`, which
  is the leaf module and already holds the other shared helpers. `gate.py`
  imports it from there. The check is: the artifact names a tool call that
  belongs to this execution's stage, and that call succeeded. Keep the
  existing behaviour of the gate's use of it exactly as it is today.
- Apply it in `_sum_items` and in `neighbors_checked_per_cluster`, which sums
  its own artifact list and needs the same filter.
- The other metrics count tool calls or run-level claims, not artifacts, so
  they are unchanged.

This is deliberately stricter than the gate in one case: a stage that declares
no required tools can still hold artifacts, but those artifacts do not count
toward a metric, because nothing ties them to a tool call. That is the
fail-closed direction and it is intended. The gate's own derivation check
keeps the conditional reading of spec section 5.2; do not change it.

## F3. The promotion never checks that the run result belongs to the run

`evaluate_promotion` takes `run_id` and `run_result` as separate arguments and
never compares `run_result.run_id` to `run_id`.
`evaluate_promotion(contract, "run-OTHER", a_green_result_for_run_1, [])`
returns `promoted=True` for another run's evidence.

**The fix.** Block the promotion when the run result belongs to a different
run. Use a fixed phrase, and carry the two run ids in the detail: they are
ids, not free text.

## F4. The validator accepts a contract that can never promote

Validation rule 10 checks `hard_requirements.require_counter_evidence` and
`require_competitor_search` against the served gate keys, but it does not
check `promotion.require_counter_evidence_gate` and
`promotion.require_competitor_gate`, which default to true. A contract with
`require_competitor_search: false`, no stage serving `competitor_search`, and
the default promotion flags validates clean and then blocks every promotion
with "the competitor_search gate did not pass".

**The fix.** Mirror rule 10 for the two promotion flags. It fails closed, so
nothing is wrongly promoted.

## F5. Nothing pins the status violation's messages and details

`_status_violation` in `gate.py` is the single source of the three status
messages, and the suite asserts only codes. A reworded message or a dropped
detail key passes every test, and those strings are what a later wave writes
to the database.

**The fix.** One test asserting the three `(code, message, detail)` tuples
the helper produces for `failed`, `timed_out`, and `not_started`.

## The tests to add

1. `test_the_run_level_thresholds_count_only_the_evidence_the_gate_accepted`
   A contract with a `promotion` threshold block, a required stage, and a
   misfiled execution carrying the artifacts that would meet the threshold.
   Assert the run does not pass and promotion is blocked. Put it in
   `tests/test_research_gate.py`.
2. `test_a_none_execution_does_not_crash_the_run_gate`
   `evaluate_run` with a required stage mapped to `None` on a contract with a
   `promotion` threshold. Assert it returns a result, does not raise, and
   reports the stage as missing. Put it in `tests/test_research_gate.py`.
3. `test_a_metric_does_not_count_an_artifact_the_gate_rejected`
   An invalid artifact and an untraceable artifact of a kind a threshold
   counts. Assert the metric is zero and the run does not pass. Put it in
   `tests/test_research_gate.py`.
4. `test_promotion_is_blocked_when_the_run_result_belongs_to_another_run`
   A green run result judged for a different run id. Assert promotion is
   blocked. Put it in `tests/test_research_gate.py`.
5. `test_a_contract_that_can_never_promote_is_an_error`
   The F4 case. Assert `validate_contract` returns an error. Put it in
   `tests/test_research_contract.py`.
6. The F5 test.

Do not modify any existing test. Do not touch any file under `docs/`.

## Rules for this round

- Stay inside the declared radius. You may touch
  `src/product_hunter/research/gate.py`,
  `src/product_hunter/research/predicates.py`,
  `src/product_hunter/research/contract.py`,
  `tests/test_research_gate.py`, and `tests/test_research_contract.py`.
  Nothing else.
- No new dependency, no new module, no new configuration surface.
- The smallest complete change. Do not refactor anything around the fixes.

## Definition of done

```text
uv run pytest
```

Green, and the new tests are in the run. Then report:

- the files you changed, with line counts
- the gate output verbatim
- each new test and what it pins
- what you did not check

---

# Fix round 7

**RUN_ID:** phase4-20261021-resume-01
**Node:** IMPLEMENT (stage 4), rung 1, after the fifth REVIEW returned
`VERDICT: fix 1 first`.

The fifth review found one crash path and three smaller items. The crash path
is a Must fix: the gate raised instead of returning a verdict, which breaks
the promise in its own docstring.

## G1. The gate raises instead of returning a verdict on a malformed URL

`_host_of` in `src/product_hunter/research/predicates.py` calls
`urlparse(url).hostname` unguarded. `urlparse` raises `ValueError` when the
netloc contains an opening bracket with no matching closing bracket, for
example `http://[::1`.

`source_domains` parses the `query_or_operation` of every successful `fetch`
call, and the gate computes all the metrics for every stage, so one malformed
URL kills the whole evaluation before the artifact, quality, and threshold
checks run.

Reproduced against the public API:

```text
a successful fetch call with query_or_operation "http://[::1"
evaluate_stage(...) -> ValueError: Invalid IPv6 URL
evaluate_run(...)   -> ValueError: Invalid IPv6 URL
```

The shipped baseline reaches this: `discovery` requires `fetch`, carries a
`source_diversity` check, and carries a `min_source_domains` threshold. A
truncated IPv6 literal or a stray bracket in a scraped URL is ordinary
evidence.

**The fix.** Guard the parse in `_host_of`. A URL the parser cannot read has
no host, so it contributes no domain. The metric returns zero for it and the
gate carries on and returns a verdict.

Do not swallow anything else. Catch only the parse failure, and only in this
one function.

## G2. The metric filter is stricter than the gate, and the docstring says otherwise

`_counted_artifacts` requires traceability unconditionally. The gate applies
its trace check only when the stage declares required tools. So for a stage
that declares no required tools, the gate accepts a valid but untraceable
artifact while the metric refuses to count it, and the stage fails
`evidence_threshold_unmet` with no artifact violation pointing at the cause.

The metric cannot be made conditional: it has no view of the contract. The
divergence is inherent, and it fails closed, so it is the right direction.
The problem is that the docstring states the metric counts only what the gate
accepts, which is not true in that one case.

**The fix.** Correct the docstring to say that the metric is stricter than the
gate's conditional rule, that a stage declaring no required tools holds
artifacts that do not count toward a metric, and that this is deliberate
because nothing ties those artifacts to a tool call.

Add one test that pins it: a stage with `required_tools: []`, a
`required_outputs` kind, an artifact threshold on that kind, and a valid
artifact with no producing tool call. Assert the gate does not report the
artifact invalid and the metric is zero. The test documents the divergence.

## G3. Two predicates read artifacts without checking traceability

`nearest_neighbor_checked` and `duplicate_candidates_reviewed` read artifacts
directly and check only `artifact.valid`. The third artifact-reading
predicate goes through the metric, which now applies the trace filter. Two
standards inside one registry.

**The fix.** Have both predicates use `traces_to_successful_call`, which
already lives in `predicates.py` and needs no import. The detail strings must
not change shape: they report the artifact id and the count, never anything
from the artifact's free text.

## G4. The validator accepts a stage that serves the reserved `promotion` gate key

`PROMOTION_GATE_KEY` is documented as "the gate key that is evaluated at run
level; no stage serves it", and nothing refuses a stage that claims it. Such
a stage has the run-level minimum applied twice: against its own metrics, and
summed across the run.

**The fix.** One line in `validate_contract`: refuse a stage whose resolved
gate key is `PROMOTION_GATE_KEY`. It fails closed, so this is hygiene, not a
safety hole.

## The tests to add

1. `test_a_malformed_fetch_url_does_not_crash_the_gate`
   A successful `fetch` call whose `query_or_operation` is `http://[::1`.
   Assert `evaluate_stage` returns a result instead of raising, that
   `source_domains` is zero, and that the result reports a threshold failure
   for the source domains rather than a crash. Put it in
   `tests/test_research_gate.py`.
2. The G2 test.
3. `test_the_artifact_predicates_require_a_traceable_artifact`
   A valid but untraceable artifact of the kind each of the two predicates
   reads. Assert both predicates refuse it. Put it in
   `tests/test_research_gate.py`.
4. `test_a_stage_may_not_serve_the_promotion_gate_key`
   A contract with a stage whose `gate_key` is `promotion`. Assert
   `validate_contract` returns an error. Put it in
   `tests/test_research_contract.py`.

Do not modify any existing test. Do not touch any file under `docs/`.

## Rules for this round

- Stay inside the declared radius. You may touch
  `src/product_hunter/research/predicates.py`,
  `src/product_hunter/research/contract.py`, `tests/test_research_gate.py`,
  and `tests/test_research_contract.py`. Nothing else.
- No new dependency, no new module, no new configuration surface.
- The smallest complete change. Do not refactor anything around the fixes.

## Definition of done

```text
uv run pytest
```

Green, and the new tests are in the run. Then report:

- the files you changed, with line counts
- the gate output verbatim
- each new test and what it pins
- what you did not check

---

# Fix round 9

**RUN_ID:** phase4-20261021-resume-01
**Node:** IMPLEMENT (stage 4), rung 1, after the seventh REVIEW returned
`VERDICT: fix 1 first`.

The seventh review found one more fail-open path and four smaller items. Fix
them. Every claim below was reproduced before this prompt was written.

## I1. The promotion applies a run result that was judged under another contract

`evaluate_promotion(contract, run_id, run_result, claims)` takes the contract
and the run result separately and never checks that the result was produced
under that contract. Every verdict it returns about a stage comes from
`run_result.stages`, the run-level threshold verdict comes from
`run_result.violations`, and the budget verdict comes from
`run_result.violations`. All three belong to whatever contract built the
result.

The gate already polices the other half of that binding: `run_result.run_id`
is compared to the `run_id` being judged. The contract identity is unchecked.

Reproduced:

```text
loose = the contract with evidence_thresholds.s1.min_distinct_queries = 2
tight = the contract with evidence_thresholds.s1.min_distinct_queries = 8
executions = one stage with three distinct queries

rr = evaluate_run(loose, "run-1", executions)        # rr.passed is True
evaluate_promotion(tight, "run-1", rr, []).promoted   # True

the control: evaluate_run(tight, "run-1", executions).passed is False
```

Under `tight` the same execution fails its threshold and
`tight.promotion.require_all_stages_passed` is true, so the promotion must be
blocked. The same trick skips a run-level `promotion` threshold entirely.

**The fix.** Bind the run result to the contract.

- Give `RunGateResult` a field that carries the contract it was judged under.
  The contract's own `contract_version` string is not enough: two contracts
  can carry the same version and different thresholds, and the reviewer's
  case does exactly that. Use a fingerprint of the whole contract instead.
- Put the fingerprint helper in `src/product_hunter/research/contract.py`,
  next to the other contract helpers, and export it from
  `src/product_hunter/research/__init__.py`. Use the standard library only:
  `hashlib.sha256` over the contract's JSON dump. The dump is stable for a
  given contract, so the same contract always gives the same fingerprint.
- `evaluate_run` sets the field on the result it returns.
- `evaluate_promotion` reports a blocking `promotion_blocked` violation when
  the result carries a fingerprint and it is not the fingerprint of the
  contract it was given. Use a fixed phrase and put the two fingerprints in
  the detail. They are hashes, not free text.
- The field's default is the empty string, and an empty fingerprint means the
  result was not produced by `evaluate_run`, so there is nothing to compare
  and nothing is blocked. A hand-built result keeps working the way it does
  today.

## I2. The run-level counter-evidence threshold ignores a recorded explicit zero

`_threshold_violations` gives the counter-evidence field a zero-outcome
fallback at the stage level. `_promotion_threshold_violations` compares the
summed metric against the minimum with no such fallback, so the same contract
and the same execution give two answers: the stage gate passes a legitimate
recorded zero, and the run-level block of the same field fails it and blocks
the promotion.

The spec defines the field once, with no level distinction.

**The fix.** Mirror the stage-level fallback in the run-level check: when the
field is the counter-evidence one and the summed value does not meet the
minimum, accept it when any of the run's executions recorded a legitimate
zero-result outcome.

## I3. A contract file that is not UTF-8 escapes as a codec error

`load_research_contract` catches `yaml.YAMLError` but the
`read_text(encoding="utf-8")` sits inside the same `try`, so a file that is
not UTF-8 text raises `UnicodeDecodeError` rather than the documented
`ContractError`.

**The fix.** Catch the decode error too and raise a `ContractError` carrying
the path and the message. Use a different message for the decode failure than
for the YAML failure, so the operator can tell which one it was.

## I4. `non_empty_queries` accepts a whitespace-only query

The predicate tests `if call.query_or_operation:`, and `"   "` is truthy, so
it reports that the call carries a query. The round-8 fix made
`distinct_queries` strip and discard an empty result, so the module now
answers "is this a query?" two different ways in two functions.

The shipped `evidence_collection` stage carries this check and no threshold,
so that stage passes its only query gate on a search call whose query is
whitespace.

**The fix.** Strip the query before the emptiness test, so the predicate and
the metric agree. The detail string must not change shape: it reports the
tool call id and a fixed phrase, never the query.

## I5. The timeout message asserts more than the code knows

The timeout violation reads "stage 'x' did not finish inside its timeout",
but it fires whenever the elapsed time cannot be computed, including when
`started_at` is missing and `finished_at` is set, where the stage did record
a finish.

**The fix.** Word the message as the fact the code has: the stage cannot be
shown to have finished inside its timeout. The detail already separates the
two cases, so keep the detail keys as they are.

## The tests to add

1. `test_a_run_result_judged_under_another_contract_is_not_promoted`
   The reviewer's case: a run judged under a loose contract, promoted under a
   tight one. Assert the promotion is blocked, and that the violation names
   the mismatch. Put it in `tests/test_research_gate.py`.
2. `test_a_run_level_counter_evidence_threshold_accepts_a_recorded_zero`
   A contract with a run-level `promotion` counter-evidence threshold, and a
   run whose stage recorded a legitimate zero. Assert the run passes its
   run-level thresholds and promotion is not blocked by them. Put it in
   `tests/test_research_gate.py`.
3. `test_a_contract_file_that_is_not_utf8_raises_contract_error`
   A file written as bytes that are not UTF-8. Assert `ContractError` is
   raised. Put it in `tests/test_research_contract.py`.
4. `test_a_whitespace_only_query_is_not_a_query`
   A search call whose query is whitespace. Assert the predicate refuses it.
   Put it in `tests/test_research_gate.py`.

Do not modify any existing test. Do not touch any file under `docs/`.

## One item deliberately not in this round

The review also asked for `BudgetSpec` to refuse a non-positive limit when it
is built by hand, bypassing the contract. It is deferred on purpose: the
contract path already rejects such a limit, the tracker is safe when it is
built from a contract, and a hand-built spec is outside the trust boundary in
the same way a hand-built run result is. Making the model refuse it would
move the error from contract validation to model construction, which would
change what an existing test asserts. The residual is recorded in the wave
report instead.

## Rules for this round

- Stay inside the declared radius. You may touch
  `src/product_hunter/research/gate.py`,
  `src/product_hunter/research/contract.py`,
  `src/product_hunter/research/predicates.py`,
  `src/product_hunter/research/__init__.py`,
  `tests/test_research_gate.py`, and `tests/test_research_contract.py`.
  Nothing else.
- No new dependency, no new module, no new configuration surface.
- The smallest complete change. Do not refactor anything around the fixes.

## Definition of done

```text
uv run pytest
```

Green, and the new tests are in the run. Then report:

- the files you changed, with line counts
- the gate output verbatim
- each new test and what it pins
- what you did not check

---

# Fix round 10

**RUN_ID:** phase4-20261021-resume-01
**Node:** IMPLEMENT (stage 4), rung 1, after the eighth REVIEW returned
`VERDICT: fix 1, 2 first`.

The eighth review fuzzed the three evaluators over 4000 generated executions
and found zero exceptions, checked every validation rule, and audited every
violation detail. It found two fail-open paths in the run-level threshold
guard, one of which this run's own round 9 introduced, and three smaller
items. Fix them all.

## J1. The run-level counter-evidence zero is taken from any stage

Round 9's I2 made the run-level check accept the counter-evidence minimum
when any of the run's executions recorded a legitimate zero. The stage-level
rule it was meant to mirror is scoped to the execution being judged, and the
comment above the code says so. The code does something else, and the two
rules disagree about the same evidence.

Reproduced: a contract with two required stages, `discovery` and
`counter_evidence`, and a run-level `promotion` counter-evidence minimum of
one. The `discovery` execution recorded a legitimate zero, because discovery
searched for products and found nothing. The `counter_evidence` execution
produced zero items and recorded no zero. The run passed and the promotion
went through. The same evidence against a stage-level counter-evidence
threshold correctly fails the `counter_evidence` stage.

**The fix.** The run-level counter-evidence minimum may only be satisfied by a
zero recorded by the execution of the stage that serves the `counter_evidence`
gate key. If no stage serves that key, the run-level counter-evidence minimum
accepts no zero at all, only counted items.

Use the helper that maps a gate key to the stage that serves it. It is already
in `contract.py` and `gate.py` imports it.

## J2. The run-level thresholds sum per-stage distinct counts

The run-level value is the sum of the per-execution metric values. That is
right for the additive metrics, which count items: documents, candidate urls,
authors, communities, evidence spans, counter-evidence items. It is wrong for
the three distinct metrics, which count distinct hosts, distinct queries, and
distinct query families. Summing them counts the same host, query, or family
once per stage.

Reproduced: three stages that each fetch the same host give a run-level
`min_source_domains: 3` with no violation, although the run saw one host. Two
stages that each run the same single query with the same single family give
a run-level `min_distinct_queries: 2` with no violation, although the run
made one query in one family.

**The fix.** For the three distinct metrics, the run-level value is the
distinct count over the union of the run's executions, not the sum of the
per-execution counts.

Implement it in the metric layer, not in the gate:

- In `src/product_hunter/research/predicates.py`, split each of the three
  distinct metrics into a function that returns the set of values it counts
  and a function that returns the count. The count function is the set's
  length. The set functions reuse the existing logic, including the trimming.
- Expose a mapping from the three distinct metric names to their set
  functions, next to the existing `METRICS` mapping. Export it from
  `src/product_hunter/research/__init__.py` next to `METRIC_NAMES`.
- In the gate's run-level threshold check, use the union of the per-execution
  sets for a metric in that mapping, and the sum for every other metric.

The gate's stage-level check is unchanged: it reads one execution, and the
count of one execution is already distinct.

## J3. The budget the gate checks is not the budget the contract declares

`contract.budgets` is validated at length, but the gate never reads it. The
gate asks the tracker for its overage, and the tracker compares the spend
against its own spec. Nothing checks that the tracker's spec is the
contract's budgets.

Reproduced: a contract that declares `per_run.search_queries: 2`, a run that
makes fifty search calls, and a caller who passes a tracker built with a
limit of a thousand. The run passes and the promotion goes through, with no
budget violation anywhere. The contract's declared limit is not the limit
that was checked.

**The fix.** The contract's budgets are the authority for the limit, and the
tracker is the ledger for the spend.

- In `src/product_hunter/research/budget.py`, add a method that reports the
  overage of a tracker against a given `BudgetSpec`, and make the existing
  `exceeded()` call it with the tracker's own spec. One implementation, two
  callers.
- In the gate, ask the tracker for its overage against `contract.budgets`,
  not against the tracker's own spec. The gate has the contract; use it.

A tracker built from a different spec then reports the contract's overage,
and a caller cannot widen the contract's budget by passing a looser tracker.

## J4. An artifact that names another stage is accepted as this stage's output

The package checks the stage binding for the other two evidence levels, the
execution and the tool call, but `ArtifactRecord.stage` is read nowhere. A
stage satisfies its output requirement with a record that says it belongs to
another stage.

Reproduced: one stage with no required tools, one required output kind, and
an execution holding an artifact whose `stage` names a different stage. No
violation, the run passes.

**The fix.** In the gate's `required_outputs` loop, an artifact of a required
kind must also belong to the stage being judged. When it does not, report
`provenance_mismatch`, the same treatment a misfiled execution gets. The
detail carries stage names only.

## J5. The timeout message understates a proven overrun

The timeout violation reads "stage 'x' cannot be shown to have finished inside
its timeout" for both cases: a missing timestamp, where it is exactly right,
and a proven overrun, where the detail shows the elapsed time is past the
limit.

**The fix.** Two messages. When the elapsed time is known and over the limit,
the message says the stage ran longer than its timeout. When the elapsed time
cannot be computed, the message says the stage cannot be shown to have
finished inside its timeout. The detail keys stay as they are.

## J6. The contract fingerprint is sensitive to a list order that changes nothing

`contract_fingerprint` hashes the whole model dump, so `required_stages: [a,
b]` and `[b, a]` fingerprint differently even though the two contracts produce
identical verdicts, because the violations are sorted. A caller whose
promotion contract lists the stages in a different order gets a false "the run
result was judged under another contract" block.

**The fix.** Hash a canonical form of the contract in which the lists that
carry no order are sorted. The lists are `required_stages`, and each stage's
`required_tools`, `required_outputs` and `quality_checks`, and the retry
policy's `retry_on`. The threshold blocks are mappings and their order is
already stable under the sorted dump.

Do not sort anything that carries meaning. If a list's order matters to the
verdict, it stays as it is.

## The tests to add

1. `test_a_run_level_counter_evidence_zero_comes_from_the_counter_evidence_stage`
   The reviewer's case: a discovery stage that recorded a zero, a
   counter-evidence stage that recorded none. Assert the run-level
   counter-evidence minimum is not met and the promotion is blocked. Put it
   in `tests/test_research_gate.py`.
2. `test_a_run_level_distinct_threshold_counts_the_union_not_the_sum`
   Three stages that each fetch the same host. Assert the run-level
   `source_domains` is one and the threshold is not met by the repetition.
   Do the same for `distinct_queries` and `query_families`. Put it in
   `tests/test_research_gate.py`.
3. `test_the_budget_checked_is_the_contract_budget`
   A contract that declares a limit of two, a run that spends fifty, and a
   tracker built with a limit of a thousand. Assert the gate reports
   `budget_exceeded` against the contract's limit. Put it in
   `tests/test_research_gate.py`.
4. `test_an_artifact_belonging_to_another_stage_is_a_mismatch`
   An artifact of a required kind whose `stage` names another stage. Assert
   the result reports `provenance_mismatch`. Put it in
   `tests/test_research_gate.py`.
5. `test_the_timeout_message_says_which_case_it_is`
   A proven overrun and an uncomputable elapsed time. Assert the two
   messages differ and each says the right thing. Put it in
   `tests/test_research_gate.py`.
6. `test_the_contract_fingerprint_ignores_a_list_order_that_changes_nothing`
   Two contracts that differ only in the order of `required_stages` and of a
   stage's `required_tools`. Assert they fingerprint the same, and that two
   contracts that differ in a threshold fingerprint differently. Put it in
   `tests/test_research_contract.py`.

Do not modify any existing test. Do not touch any file under `docs/`.

## Rules for this round

- Stay inside the declared radius. You may touch
  `src/product_hunter/research/gate.py`,
  `src/product_hunter/research/predicates.py`,
  `src/product_hunter/research/contract.py`,
  `src/product_hunter/research/budget.py`,
  `src/product_hunter/research/__init__.py`,
  `tests/test_research_gate.py`, and `tests/test_research_contract.py`.
  Nothing else.
- No new dependency, no new module, no new configuration surface.
- The smallest complete change. Do not refactor anything around the fixes.

## Definition of done

```text
uv run pytest
```

Green, and the new tests are in the run. Then report:

- the files you changed, with line counts
- the gate output verbatim
- each new test and what it pins
- what you did not check

---

# Fix round 11

**RUN_ID:** phase4-20261021-resume-01
**Node:** IMPLEMENT (stage 4), rung 1, after the ninth REVIEW returned
`VERDICT: fix 1 first`.

The ninth review checked the round-10 fixes, agreed with every deferral, and
found one more fail-open path plus three smaller items. Fix them.

## K1. A misfiled artifact is still counted towards a threshold

`_counted_artifacts` counts an artifact when it is valid and traces to a
successful call. The trace check compares the CALL's stage with the
execution's stage; it never looks at `artifact.stage`. So an artifact filed
under another stage is counted by the metric of the stage it sits in. Round
10's J4 stopped the gate from accepting such an artifact as a required
output; the metric did not follow.

Reproduced: a stage with one required output kind and a threshold on a
different kind, one successful search call, and five artifacts of the
threshold's kind filed under another stage. The stage passed with no
violations, the run passed, and the promotion went through, on a stage that
produced none of the documents its threshold asked for.

**The fix.** In `_counted_artifacts`, count an artifact only when it also
belongs to the execution's stage. One condition.

The reviewer applied this change without touching the repository and re-ran
the research suite: all 131 research tests still pass, and the reproduced
case then reports the threshold as unmet with a value of zero.

**Also.** `validate_contract` should refuse a stage whose threshold names a
metric whose artifact kind, from `METRIC_ARTIFACT_KINDS`, is not among that
stage's `required_outputs`. The shipped contract satisfies this and a test
already asserts it for the shipped file, but a hand-written contract can
declare a threshold on a kind it never declares as an output.

## K2. `evaluate_run` raises on an invalid contract that the other two entry points handle

`evaluate_run` computes the contract fingerprint on the invalid-contract
path, and the fingerprint dumps the whole contract in JSON mode.
`EvidenceThreshold` allows extra fields, so an extra field's value reaches
the dump and a value pydantic cannot serialize raises.

Reproduced: a threshold block carrying an extra field with an unserializable
value. `evaluate_stage` returns `contract_invalid`; `evaluate_promotion`
returns `promoted=False`; `evaluate_run` raises a serialization error. The
module docstring promises the gate does not raise for a failed gate.

**The fix.** Do not compute the fingerprint on the invalid-contract path. The
result's fingerprint field already documents the empty string as "not produced
by `evaluate_run`", and an invalid contract never produced a usable result.

## K3. A NaN quantity silently switches a budget dimension off

`BudgetTracker.record` adds the amount to the ledger and every later
comparison is `spent > limit`. With a NaN amount every comparison is false,
so the dimension is unlimited and the gate never reports the overrun.

Reproduced: `record("usd_cost", float("nan"))` on a tracker with a limit of
five, then a check for a thousand is allowed, and `exceeded()` is empty.

The contract validator already guards the NaN LIMIT for exactly this reason.
The quantity side has no such guard.

**The fix.** Refuse a non-finite amount. `check` returns `allowed=False` with
a fixed reason. `record` refuses the spend and does not record it. Use
`math.isfinite`. The reason must not quote the value.

## K4. A stage that finished before it started satisfies the timeout check

`_elapsed_seconds` returns a negative number for an inverted pair of
timestamps, and the timeout check only fires when the elapsed time is over
the limit, so a negative elapsed passes. The gate already fails closed when
the elapsed time cannot be computed; an inverted pair is the same class of
unprovable evidence.

**The fix.** Treat an elapsed time that is not greater than zero as one that
cannot be computed, so the timeout branch fires and the message says the
stage cannot be shown to have finished inside its timeout. A stage that
finished at exactly its start time is a zero-length stage, which is provable
and in time, so the boundary is strictly negative, not zero.

## The tests to add

1. `test_a_misfiled_artifact_is_not_counted_towards_a_threshold`
   The reviewer's case: five artifacts of a threshold's kind filed under
   another stage. Assert the metric is zero, the stage reports
   `evidence_threshold_unmet`, and the run does not pass.
2. `test_a_threshold_on_a_kind_the_stage_does_not_declare_is_a_contract_error`
   A contract whose stage declares a threshold on a kind it does not declare
   in `required_outputs`. Assert `validate_contract` returns an error naming
   the stage and the kind.
3. `test_evaluate_run_does_not_raise_on_an_invalid_contract`
   A contract carrying an extra threshold field with an unserializable value.
   Assert `evaluate_run` returns a result with `contract_invalid` and does
   not raise.
4. `test_a_non_finite_budget_amount_is_refused`
   A NaN amount in `check` and in `record`. Assert `check` denies it and
   `record` does not add it to the ledger.
5. `test_a_stage_that_finished_before_it_started_does_not_skip_its_timeout`
   An execution whose `finished_at` precedes its `started_at`, with a
   timeout set. Assert the result reports `stage_timed_out`.

Do not modify any existing test. Do not touch any file under `docs/`.

## Rules for this round

- Stay inside the declared radius. You may touch
  `src/product_hunter/research/gate.py`,
  `src/product_hunter/research/predicates.py`,
  `src/product_hunter/research/contract.py`,
  `src/product_hunter/research/budget.py`,
  `tests/test_research_gate.py`, `tests/test_research_contract.py`, and
  `tests/test_research_budget.py`. Nothing else.
- No new dependency, no new module, no new configuration surface.
- The smallest complete change. Do not refactor anything around the fixes.

## Definition of done

```text
uv run pytest
```

Green, and the new tests are in the run. Then report:

- the files you changed, with line counts
- the gate output verbatim
- each new test and what it pins
- what you did not check

---

# Fix round 12

**RUN_ID:** phase4-20261021-resume-01
**Node:** IMPLEMENT (stage 4), rung 1, after round 11 landed.

Round 11 fixed the metric half of a class. Two pieces of the same class are
left. Close them so the class is closed.

## L1. A misfiled artifact can still satisfy a quality check

`nearest_neighbor_checked` and `duplicate_candidates_reviewed` read artifacts
directly, through `_artifacts` and `traces_to_successful_call`, and not
through `_counted_artifacts`. Round 11's K1 made `_counted_artifacts` require
the artifact to belong to the execution's stage. These two predicates did not
follow, so an artifact filed under another stage can still satisfy a quality
check in a stage that does not declare that kind as a required output.

**The fix.** Both predicates must apply the same stage rule the metric applies:
the artifact must belong to the execution's stage. Reuse the shared helper
rather than repeating the condition, so there is one implementation of the
rule.

The detail strings must not change shape. They report the artifact id and a
count, never anything from the artifact's free text.

## L2. The validator does not check the quality-check kinds

Round 11's K1 added the validator rule that a stage's threshold must name a
metric whose artifact kind is among that stage's `required_outputs`. The same
rule is missing for the quality checks: a stage may name a quality check
whose predicate reads an artifact kind it never declares as a required
output.

The shipped contract satisfies this, and a test already asserts the alignment
for the shipped file, but a hand-written contract can break it.

**The fix.** Extend the validator rule to the quality checks. A stage whose
quality check names a predicate that reads an artifact kind, from
`PREDICATE_ARTIFACT_KINDS`, that is not among that stage's `required_outputs`
is an error. The error must name the stage, the check, and the kind.

Check the shipped file before you finish: if the shipped contract turns out
to break this rule, say so in your report rather than changing the shipped
contract.

## The tests to add

1. `test_a_misfiled_artifact_does_not_satisfy_a_quality_check`
   A valid but misfiled artifact of the kind each of the two predicates
   reads. Assert both predicates refuse it, and that the same artifact
   properly filed satisfies them.
2. `test_a_quality_check_on_a_kind_the_stage_does_not_declare_is_a_contract_error`
   A contract whose stage names a quality check whose kind it does not
   declare in `required_outputs`. Assert `validate_contract` returns an
   error naming the stage, the check, and the kind.

Do not modify any existing test. Do not touch any file under `docs/`.

## Rules for this round

- Stay inside the declared radius. You may touch
  `src/product_hunter/research/predicates.py`,
  `src/product_hunter/research/contract.py`, `tests/test_research_gate.py`,
  and `tests/test_research_contract.py`. Nothing else.
- No new dependency, no new module, no new configuration surface.
- The smallest complete change. Do not refactor anything around the fixes.

## Definition of done

```text
uv run pytest
```

Green, and the new tests are in the run. Then report:

- the files you changed, with line counts
- the gate output verbatim
- each new test and what it pins
- what you did not check

---

# Fix round 8

**RUN_ID:** phase4-20261021-resume-01
**Node:** IMPLEMENT (stage 4), rung 1, after the sixth REVIEW returned
`VERDICT: fix 1, 2 first`.

The sixth review swept the three evaluators and the validator with 2205
crafted contract and execution combinations and found two more fail-open
paths, three ways the metrics can be gamed, and two smaller items. Fix all of
them.

## H1. The promotion reads the claims twice and loses them the second time

`evaluate_promotion` consumes `claims` in a set comprehension, then reads it
again further down. When `claims` is a one-shot iterator, the second read sees
an empty tuple, so the `require_claim_evidence_links` rule, which is on by
default, is silently satisfied.

Reproduced: the same run with a list gives `promoted=False` with the reason
"a claim carries no evidence reference"; with a generator it gives
`promoted=True` with zero violations.

**The fix.** Materialise the claims into a tuple as the first statement of
`evaluate_promotion`. A list or a tuple is unaffected.

## H2. A NaN budget limit passes validation and turns the budget off

The three limit checks in `validate_contract` test `limit <= 0`, which is
false for NaN, so a limit that is not positive is accepted. The soft-threshold
check three lines below already uses the NaN-safe form `not 0 < fraction <= 1`.

Reproduced end to end: a contract with `budgets.per_run.search_queries: .nan`
loads without a `ContractError`; recording ten million search queries leaves
`exceeded()` empty, the run passes with zero violations, and the promotion is
promoted. Every comparison against NaN is false, so the dimension never
blocks.

**The fix.** Use the NaN-safe form in the three limit checks. A limit must be
greater than zero, and NaN is not greater than zero.

## H3. The timeout check is skipped when the execution has no end timestamp

`_elapsed_seconds` returns `None` when either timestamp is missing, and the
timeout branch only runs when the elapsed time is a number. A stage with
`timeout_seconds: 30` and a `finished_at` of `None` returns `passed=True` with
no violations. The same execution with a finish timestamp five hours later
reports `stage_timed_out`.

This is the class the round-5 fix closed for naive timestamps: an absent
timestamp switches the check off and nothing refuses it. `finished_at` is
optional in the model and in the spec, so the fix belongs in the gate.

**The fix.** When the stage declares a timeout and the elapsed time cannot be
computed, report `stage_timed_out`. The gate fails closed: a stage that
cannot prove it finished inside its timeout is treated as having overrun it.

## H4. One host with trailing whitespace counts as several sources

`_host_of` lowercases the host but does not trim it, and
`urlparse("https://a.com ").hostname` is `"a.com "`. Three fetch calls to
`https://a.com`, `https://a.com ` and `https://a.com  ` give
`source_domains == 3`, so `min_source_domains: 3` and the `source_diversity`
check are met from a single real source.

**The fix.** Trim the host before lowering it.

## H5. One query with trailing whitespace counts as several queries

`distinct_queries` and `query_families` put the raw string into a set, so
`"alpha"`, `"alpha "`, `"alpha  "` and `"alpha\t"` count as four distinct
queries and four distinct families. `min_distinct_queries: 8` for discovery
can be met with two real searches.

**The fix.** Trim the string before it goes into the set, in both metrics.

## H6. The `_counted_artifacts` docstring reads the divergence backwards

The docstring says "A stage that declares no required tools therefore holds
artifacts that do not count towards a metric". What follows from the rule
above it is the other way round: such a stage can hold artifacts the gate
accepts and the metric refuses. A traceable artifact in a no-tools stage does
count.

**The fix.** One sentence: the stage can hold artifacts the gate accepts and
the metric refuses.

## H7. A YAML syntax error escapes as a `yaml` exception

`load_research_contract` calls `yaml.safe_load` outside the `try` that turns
a pydantic error into a `ContractError`. A malformed file raises
`yaml.scanner.ScannerError`, which is not the documented `ContractError`.

**The fix.** Catch `yaml.YAMLError` around the `safe_load` and raise a
`ContractError` carrying the message, the way the pydantic failure already
does.

## The tests to add

1. `test_promotion_reads_the_claims_of_a_one_shot_iterator`
   The same run and claims passed as a list and as a generator. Assert both
   give the same promotion result and the same violations. Put it in
   `tests/test_research_gate.py`.
2. `test_a_nan_budget_limit_is_a_contract_error`
   A contract with a NaN limit in each of the three places. Assert
   `validate_contract` returns an error naming the dimension. Put it in
   `tests/test_research_contract.py`.
3. `test_a_stage_with_no_end_timestamp_does_not_skip_its_timeout`
   A stage with `timeout_seconds` set and no `finished_at`. Assert the result
   reports `stage_timed_out`. Put it in `tests/test_research_gate.py`.
4. `test_one_host_with_trailing_whitespace_is_one_source`
   Three fetch calls to the same host with trailing whitespace. Assert
   `source_domains` is one. Put it in `tests/test_research_gate.py`.
5. `test_one_query_with_trailing_whitespace_is_one_query`
   Search calls carrying the same query with trailing whitespace. Assert
   `distinct_queries` and `query_families` count them once. Put it in
   `tests/test_research_gate.py`.
6. `test_a_broken_contract_file_with_a_yaml_syntax_error_raises_contract_error`
   A file with broken YAML. Assert `ContractError` is raised, not a yaml
   exception. Put it in `tests/test_research_contract.py`.

Do not modify any existing test. Do not touch any file under `docs/`.

## Rules for this round

- Stay inside the declared radius. You may touch
  `src/product_hunter/research/gate.py`,
  `src/product_hunter/research/predicates.py`,
  `src/product_hunter/research/contract.py`,
  `tests/test_research_gate.py`, and `tests/test_research_contract.py`.
  Nothing else.
- No new dependency, no new module, no new configuration surface.
- The smallest complete change. Do not refactor anything around the fixes.

## Definition of done

```text
uv run pytest
```

Green, and the new tests are in the run. Then report:

- the files you changed, with line counts
- the gate output verbatim
- each new test and what it pins
- what you did not check

---

# Fix round 13

**RUN_ID:** phase4-20261021-resume-01
**Node:** IMPLEMENT (stage 4), rung 1, after a further REVIEW round returned
findings.

Round 12 closed the last of the misfiled-artifact class. The round after it
found a run-level threshold that could never fail, a way one stage could
borrow another stage's evidence, and one gap in the budget refusal. This
section records that round, which has already landed. The review's own output
is not kept in the repository, so the round is recorded here from the code and
from the three tests that pin it.

## M1. A run-level threshold on a minimum metric can never fail

`neighbors_checked_per_cluster` is a minimum over what it counts. The stage
value is the smallest `item_count` over the cluster checks it counts, and zero
when there are none. The run-level value in `_run_metric_value` is the sum of
the per-stage values, so a run-level threshold on that metric became a total,
and a total on a minimum can only fail when the stages together are small.

The case:

```text
contract: two required stages, evidence_thresholds.promotion.min_neighbors_checked_per_cluster = 5
executions: each stage holds one cluster_neighbors_checked artifact with item_count 3
stage values : 3 and 3
run value    : 6
run.passed   : True
```

The run checked three neighbours in one cluster, not six. A sum reports a
number the run never had, and a run-level minimum on this metric could never
fail.

**The change.** Name the metrics that are a minimum rather than a total, the
way `DISTINCT_METRIC_SETS` names the metrics that count distinct values, and
read the run-level value of such a metric as the smallest of the run's
per-stage values.

- In `src/product_hunter/research/predicates.py`, add a module-level
  `MINIMUM_METRICS` next to `DISTINCT_METRIC_SETS`, holding the names of the
  metrics that are a minimum over what they count.
  `neighbors_checked_per_cluster` is the only one today.
- Export it from `src/product_hunter/research/__init__.py` next to
  `DISTINCT_METRIC_SETS`.
- In `gate.py`, `_run_metric_value`, read a metric in that mapping as the
  minimum of the per-execution values, and as zero when the run has no
  executions.

The stage-level check is unchanged: it reads one execution, and the count of
one execution is already a minimum.

## M2. A call filed under another stage counts as this stage's evidence

The metrics count the calls the execution holds, and `execution.tool_calls` can
hold a call that names another stage. The distinct metrics counted the
queries, families, and hosts of those calls, so a call filed under another
stage satisfied a threshold, a quality check, and a required tool.

The case:

```text
contract: a stage that requires "search" and "fetch", with min_distinct_queries = 2
execution: one search of its own, one search filed under another stage, one fetch filed under another stage
distinct_queries : 2
tool_missing     : absent
```

The stage borrowed a query and a fetch it never made.

**The change.** One helper in `src/product_hunter/research/predicates.py` that
yields the calls whose own `stage` field equals the execution's stage, and use
it at every site that counts a call:

- the three distinct metric set functions, `distinct_query_set`,
  `query_family_set`, and `source_domain_set`
- `non_empty_queries`
- `explicit_zero_outcome`, both the set of searched families and the set of
  failed searches
- the gate's `required_tools` check in `evaluate_stage`

The provenance check does not change. A foreign call still reports
`provenance_mismatch` when `require_provenance` is set, so the gate names what
it found instead of ignoring the call.

The two tool-call metrics, `tool_calls` and `successful_tool_calls`, keep
counting every call the execution holds. No threshold reads them.

## M3. A refused budget amount has no name of its own

`record` and `guard` refuse a non-finite amount by raising a bare
`ValueError`. A caller that wants to tell a refused amount from a budget
overrun has to read the message, and a caller that catches `ValueError` cannot
tell the two apart.

**The change.** A named class, `NonFiniteAmount`, that subclasses `ValueError`,
so a caller that already catches `ValueError` keeps catching it, and a caller
that wants the two refusals apart has a class of its own to catch. `record` and
`guard` raise it; `check` still returns a decision. Export it from
`src/product_hunter/research/__init__.py` next to `BudgetExceeded`.

Do not raise `BudgetExceeded` for it. A NaN is not a number of calls, and a
caller that catches `BudgetExceeded` around `guard` must still see the
refusal.

## The tests that came with this round

1. `test_a_run_level_minimum_threshold_takes_the_smallest_stage_value`
   A contract with a run-level `promotion` threshold on
   `min_neighbors_checked_per_cluster`, and two stages whose cluster checks
   are three each. Assert the run-level value is three, that the stage values
   are three, and that the run does not pass. In `tests/test_research_gate.py`.
2. `test_a_foreign_call_does_not_count_for_the_stage_that_is_judged`
   A stage whose execution holds calls filed under another stage. Assert the
   foreign calls count towards no metric, that the required tool that only ran
   in the other stage reports `tool_missing`, and that the foreign calls still
   report `provenance_mismatch` when the contract requires provenance. In
   `tests/test_research_gate.py`.
3. `test_a_non_finite_amount_raises_the_named_refusal`
   A NaN and an infinity in `record` and in `guard`. Assert both raise
   `NonFiniteAmount`, that the class is a `ValueError`, and that the ledger
   never saw the refused spend. In `tests/test_research_budget.py`.

The package's own test count went from 138 to 141 with this round.

## The radius of this round

- `src/product_hunter/research/predicates.py`
- `src/product_hunter/research/gate.py`
- `src/product_hunter/research/budget.py`
- `src/product_hunter/research/__init__.py`
- `tests/test_research_gate.py`
- `tests/test_research_budget.py`

Nothing else. No new dependency, no new module, no new configuration surface,
and no refactor around the three changes.

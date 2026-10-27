# Implement: the discovery engine and the query frontier

## Where to work

Work only in the worktree `C:\Anubhav\Web Dev Projects\product-hunter-wave2-discovery`
on the branch `wave2/discovery-engine`. Do not touch `main`, do not touch the
other worktrees, do not merge anything.

## What to read first

1. `docs/reasonix/specs/wave-2-discovery.md` — the reasoning and the rules.
2. `docs/reasonix/plans/wave-2-discovery.md` — the module layout, the field
   lists, the invariants, the test plan. Follow it exactly.
3. `docs/reasonix/handoffs/wave-2-discovery-handoff.md` — the repo map and the
   verified integration points.
4. The code you are about to reuse, so you match its conventions:
   - `src/product_hunter/research/budget.py` (`BudgetTracker`, `BudgetDecision`)
   - `src/product_hunter/research/evidence.py` (`ToolCallProvenance`,
     `ToolCallStatus`, `ZeroResultOutcome`)
   - `src/product_hunter/normalization.py` (`NormalizedProblem`)
   - `src/product_hunter/db/checkpoints.py` (`save_checkpoint`, `load_checkpoint`)
   - `db/migrations/0001_initial.sql` (the `research_events`, `spend_ledger`
     and `research_checkpoints` tables)
   - `tests/test_db_checkpoints.py` (how a test gets a database with a run row)
   - `src/product_hunter/normalization.py` and `src/product_hunter/fetch.py`
     (the house style for a module docstring, for validation, for identifiers)

## What to build

A new package `src/product_hunter/discovery/` with six modules and an
`__init__.py`:

- `families.py` — the eight query families, their templates, the marker table
  and `assign_family`.
- `queries.py` — `Query`, `SourceCandidate`, `normalize_query`, `query_id`,
  `is_duplicate`, `generate_queries`, `generate_derived_queries`.
- `records.py` — `DiscoveryStatus`, `DiscoveryRecord` and the invariants the
  record enforces.
- `budget.py` — `DiscoveryBudget`, its limits and its counters.
- `frontier.py` — `SaturationPolicy`, `SaturationVerdict`, `QueryFrontier`.
- `store.py` — `write_record`, `list_records`, `save_frontier`, `load_frontier`.

And five test files:

- `tests/test_discovery_families.py`
- `tests/test_discovery_queries.py`
- `tests/test_discovery_budget.py`
- `tests/test_discovery_frontier.py`
- `tests/test_discovery_store.py`

## The rules that are not negotiable

**The strategy lives in worker-side code.** No query template, family marker
or saturation threshold may come from a model, a config file read at runtime,
or an environment variable. They are module constants. A query's family is
assigned by the template that generated it, or by the marker table for a query
that arrived from outside. Never from a call out to anything.

**A broken search tool is not saturation.** The status vocabulary keeps `empty`
(a search that ran and found nothing) apart from `failure` (the tool broke).
A failure never counts towards family coverage, never advances the low-yield
counter, and leaves the frontier `blocked` rather than `saturated`. A blocked
frontier never reports saturation.

**A zero result is only meaningful when the search was real.** The zero-result
outcome is recorded only when a search came back empty and every required
family has been executed. A failed or unsearched family keeps it unrecorded.

**Identical inputs give identical ids.** The query id is derived from the
source id and the normalized query text. The record id is derived from the
record's own fields. A replay reproduces them.

**Novelty is computed in one place.** The frontier owns the set of result
identities it has seen and computes the novelty and the duplicate rate. The
record never carries a novelty number the frontier did not compute.

**Reuse what exists.** `BudgetTracker`, `ToolCallProvenance`, `ToolCallStatus`,
`ZeroResultOutcome`, `save_checkpoint`, `load_checkpoint` and
`NormalizedProblem` all exist. Use them. Do not write a second budget
tracker, a second provenance model or a second checkpoint helper.

**Validate at the boundary.** A record that contradicts itself is refused, not
stored: a `success` with no results, an `empty` with results, a useful count
above the result count, novelty that does not add up to the duplicate rate, a
naive timestamp, a result reference with whitespace in it.

**One small test per piece of non-trivial logic.** Every branch, every parser
and every arithmetic rule above needs a test that fails when the rule is
broken. The plan lists the cases per file.

## Style

Match the repository. A module opens with a docstring that says what the
module owns and what it refuses to do. Use `from __future__ import annotations`.
Use pydantic models for the data, and plain classes where there is mutable
state (the budget, the frontier). Validators carry the reason in the message.
Identifiers follow the house pattern: a prefix, then 16 hex characters of a
SHA-256.

Keep the diff small. No abstract base classes, no registry pattern, no
plugin hooks, no configuration file, no CLI command, no HTTP client. The
frontier never calls a search endpoint: it is handed result identities and it
reasons about them.

## Blast radius

Only these files, and nothing else:

```
src/product_hunter/discovery/__init__.py
src/product_hunter/discovery/families.py
src/product_hunter/discovery/queries.py
src/product_hunter/discovery/records.py
src/product_hunter/discovery/budget.py
src/product_hunter/discovery/frontier.py
src/product_hunter/discovery/store.py
tests/test_discovery_families.py
tests/test_discovery_queries.py
tests/test_discovery_frontier.py
tests/test_discovery_budget.py
tests/test_discovery_store.py
```

## Before you report back

Run the full suite: `uv run pytest`. The whole suite must be green, not only
the new tests. If an existing test fails because of your change, fix your
change; never edit an existing test to make it pass.

Then report:

- the exact `uv run pytest` output, including the pass count
- the list of files you created, with the line count of each
- the public names exported from `product_hunter.discovery`
- anything in the plan you could not implement, and why
- anything you implemented that the plan did not ask for

---

# Fix round

The review came back with a verdict of "ship with fixes". The findings below
are the reviewer's, with the reproductions it ran. Two are must-fix, the rest
are should-fix or nice-to-have. Fix all of them, in the order given, and keep
the blast radius the same.

The reviewer's scratch scripts are under
`C:\Users\anubh\AppData\Local\Temp\opencode\disc-review\`. You may run them
to see each bug, and you should re-run them after the fix.

## Must fix 1: the budget does not count a query when it is admitted

`budget.py::check` compares against `self._queries`, and only `record()`
increments it. `frontier.py::admit` calls `check()` and never reserves the
query, so a batch of admissions all see the same stale count and all pass. The
reviewer admitted and ran 24 queries against one source with a
`max_queries_per_source` of 20, and the limit never refused anything.

The spec says "A query that would exceed either limit is not started". The
limit has to hold at the moment the query is admitted, not after it ran.

Fix: the frontier's admission check must account for the queries it has
already admitted and not yet run. Give `DiscoveryBudget.check` the pending
counts to add, and have the frontier pass them.

```python
def check(
    self,
    source_id: str,
    family: QueryFamily,
    cost: float = 0.0,
    *,
    pending_total: int = 0,
    pending_source: int = 0,
    pending_family: int = 0,
) -> BudgetDecision:
```

The pending counts are the queries the frontier has admitted and not yet run.
They are added to the spend, because an admitted query is one the run is
committed to. The budget's own counters stay "executed" only, so nothing is
counted twice.

## Must fix 2: a refused query counts as a completed family search

`frontier.py::record_execution` returns early for `failure` and `timeout` only.
`duplicate` and `over_budget`, the two statuses that mean *no tool ran*, fall
through into `_searched_sources.add()`, `_family_counts[...] += 1` and the
low-yield counter.

The reviewer showed a frontier where three refused queries and zero real
searches reported `saturated: True` with the reasons `family_coverage`,
`source_coverage` and `diminishing_novelty`, and where the zero-result
outcome listed a family as completed that was never searched.

This also contradicts the record's own `to_tool_call_provenance`, which returns
`None` for these statuses because no tool ran.

Fix: a refusal is not a search and not a failure. Return early for
`duplicate` and `over_budget` as well, before the coverage, the source and the
low-yield bookkeeping, without incrementing the consecutive-failure counter. A
refusal must not clear the failure counter either: a refusal is not evidence
that the tool works.

## Should fix 3: `budget_exhausted` is invisible, and fires too eagerly

`frontier.py::_budget_exhausted` returns False when the pending set is empty,
and otherwise asks the budget about the single top-priority pending query.

Two consequences the reviewer showed: a frontier that spent its budget and has
nothing left reports `saturated=False, reasons=[]`, so the caller cannot tell
"stopped" from "has more to do"; and a per-family refusal on the top query
reports `budget_exhausted` and `saturated=True` while a different pending query
is still admissible.

Fix: `budget_exhausted` is true when the run-level limit is spent, even with
nothing pending, and when no pending query is still admissible. Ask the budget
about every pending query, not only the first.

## Should fix 4: the result count and the result identities are never compared

`frontier.py::record_execution` takes `result_count` and `result_ids`
separately and nothing checks that they agree. The reviewer passed
`result_count=1` with fifty identities and got a record that says one result
while the frontier tracked fifty.

Fix: refuse a `result_count` that is not the length of the result identity
list. And compute the novelty over the list rather than over a set of it, so
an identity repeated inside one result set counts as a repeat. That makes
`novelty * result_count` the exact number of new results, and it lets you
delete the set handling.

## Should fix 5: the two store inserts are not atomic, and a replay never repairs a missing spend row

`store.py::write_record` inserts a `research_events` row and a `spend_ledger`
row with no transaction between them. The connections are opened with
`isolation_level=None`, so those are two independent transactions. A crash
between them leaves the event without the spend row, and the replay path finds
the existing event and returns early, so the spend row is never written. The
reviewer showed a record whose event existed and whose spend row did not,
before and after a replay.

Fix: wrap the two inserts in one transaction, following the house pattern in
`src/product_hunter/registry.py` and `src/product_hunter/db/store.py`. In the
replay path, make sure the spend row exists before returning.

## Should fix 6: the discovery budget accepts a non-finite or negative amount

`research/budget.py` refuses a non-finite amount with `NonFiniteAmount` and
says why: "every comparison against a non-finite value is false and the
dimension would switch itself off". `discovery/budget.py` has no such guard.

The reviewer showed `check(nan)` returning "within budget", a NaN then
poisoning `_cost` so that every later check passes, and a negative amount
reducing the recorded spend. `frontier.admit(q, cost=nan)` bypasses the cost
check outright, and `record_execution(cost=inf)` is accepted by the record's
own `ge=0.0` because infinity is not less than zero, and then makes every
later query fail the per-cost check.

Fix: refuse a non-finite or negative cost before it reaches the ledger.
Reuse `product_hunter.research.budget.NonFiniteAmount` for the non-finite
case. The `DiscoveryRecord.cost` field must refuse a non-finite amount too.

## Should fix 7: the contract tracker does not survive a checkpoint

`DiscoveryBudget.from_state` restores the counters but not the tracker, and
`QueryFrontier.from_state` has no parameter to supply one, so after a resume
the contract's `search_queries` limit no longer binds. The spec says the
contract's limits still bind.

Fix: let the caller hand a budget or a tracker back at restore time. Give
`QueryFrontier.from_state` and `store.load_frontier` a parameter for it.

## Should fix 8: `repeated_results` is decided on one number and reported as another

`frontier.py::saturation` decides `repeated_results` on
`self._last_duplicate_rate`, and the same verdict reports
`self._duplicate_rate()`, the cumulative rate over the run, in its signals. Two
different numbers are called the duplicate rate in one object. The reviewer
showed a verdict that reported `repeated_results` while its own reported
cumulative rate was below the threshold.

Fix: decide on the cumulative rate, which is what the verdict already reports,
and keep the last execution's rate as a separate signal under its own name.
The two signals must not be confusable. Update the test that pins the current
behaviour, and do not weaken it.

## Should fix 9: a caller can choose a query's own family, and the frontier never expands itself

Two problems, both found by the reviewer.

First, `frontier.admit` takes the family from the `Query` it is handed. A
caller that builds its own `Query` can set any family, and the frontier then
counts that family towards coverage. The reviewer admitted a query whose text
the marker table reads as `problem` with the family `severity`, and the
frontier counted `severity`.

Fix: the frontier re-derives the family and refuses a mismatch. A query whose
`template_id` is empty is one of unknown origin, so its family must be the one
`assign_family` gives its text. A query whose `template_id` is set must carry
the family that template belongs to.

Second, nothing in `src/` calls `generate_queries`, and the frontier has no
expansion method at all. The spec says "The frontier expands itself along
code-defined families", and right now the caller decides which family to
expand, which is exactly what the spec forbids.

Fix: give the frontier an `expand` method that grows the frontier along the
code-owned families. Each required family has a cursor over its own templates.
The next variant of the least covered family is rendered for the seed and
admitted. The families, the templates and their order are code-owned; the
caller chooses how many queries to take, never which family. The cursor is
part of the frontier state, so it survives a checkpoint.

With one policy knob, `queries_per_family`, the frontier generates that many
variants per required family, and a family counts as covered once that many of
its searches have run. Replace the `min_queries_per_family` field with it. The
default is 3.

## Should fix 10: the record's docstring promises a guarantee it does not enforce

`records.py` says the record "refuses a novelty the frontier did not compute".
The validator only checks that `novelty + duplicate_rate == 1`. A caller can
build a record claiming novelty 1.0 for a result set the frontier had already
seen. The ids *are* protected, because they are recomputed from the fields.

Fix: correct the docstring to say what is actually enforced. The spec and the
plan say the same thing in their own words, so correct them too. Do not invent
a mechanism to make the novelty unforgeable; the frontier computing it is the
guarantee, and the record checking its own arithmetic is the rest of it.

## Should fix 11: the zero-result outcome is sticky

`frontier._saw_empty` is set once and never cleared, so a run whose first
search came back empty still reports a recorded zero-result outcome after a
later search found plenty. The research gate's explicit-zero predicate accepts
that as a legitimate zero.

Fix: clear it when a search comes back with results. The zero-result outcome
must describe the run as it stands, not as it once stood.

## Should fix 12: six rules have no test that would fail if the rule broke

The reviewer ran mutation testing against the repo's own tests, by copying the
package to a temp directory and pointing `PYTHONPATH` at the copy. These
mutations survived, so the rule they break is not protected by any test:

| Mutation | Survived |
|---|---|
| drop the `result_ref` regex check, keep the whitespace check | yes |
| drop the source weight from the priority | yes |
| drop the novelty estimate from the priority | yes |
| drop the status from the record id basis | yes |
| swap the query id basis from `source|text` to `text|source` | yes |
| change the event id basis | yes |
| remove the exact-duplicate branch in `admit` | yes |
| let a refused query count towards family coverage | yes |

Fix: add one test per surviving rule, and pin the derivation with a golden
value rather than by comparing a function with itself. A test that says "the
id of this exact input is this exact string" pins the basis; a test that says
`query_id(text, source) == query_id(text, source)` does not.

For the exact-duplicate branch, the near-duplicate refusal message also
contains the word "duplicate", so assert on the part that differs: the exact
branch names the duplicate query id, and the near branch says "near duplicate".

## Nice to have, fix if it is cheap

- `frontier.py` reads `status.value` off the status, so a plain string status
  raises `AttributeError` after the record was built, and the search result is
  lost. Accept both, the way `records.record_id` does.
- `store.py::save_frontier` defaults to version 1, so a second save of a
  changed frontier raises `CheckpointConflict`, the old state stays, and
  `load_frontier` returns the stale frontier. The pending query is silently
  lost. Let the frontier carry its own checkpoint version, and advance it after
  a successful save.

## Do not change

- The blast radius. The same twelve files, plus the two documents this
  prompt names.
- The status vocabulary. The seven statuses stay as they are.
- The id prefixes: `q_`, `dq_`, `evt_`, `spn_`.
- The families, their templates and the marker table. They are the strategy.
- The rule that a failure is never saturation.

---

# Fix round 2

The first fix round closed the reviewer's twelve findings. One residual hole
is left, and it is on the rule the whole wave exists for.

## The residual: a caller can still forge a family

`frontier.py::_family_of` decides a query's family like this:

- a query with no `template_id` takes the family the marker table gives its
  text, which is right;
- a query with a `template_id` takes the family named by the prefix of that
  id, and the text is never checked.

So a caller that builds its own `Query` with `template_id="severity-0"` and
`family=QueryFamily.severity` gets the severity family, whatever the text
says. The frontier counts that towards the severity coverage, and the family
was chosen by the caller rather than by the code.

The mission is explicit about this. "Worker-assigned query-family labels", and
"Do NOT allow the LLM to freely decide all discovery strategy." A family that
a caller can name is a family the caller chose.

Fix: a query that names a template must actually be a rendering of that
template. The check belongs in `_family_of`, because that is the one place
that decides the family, and both `admit` and `record_execution` already go
through it.

The template id is `f"{family.value}-{index}"`, so the family is the part
before the last dash and the index is the part after it. The family holds
`templates` in `FAMILY_SPECS`, so the named template is
`FAMILY_SPECS[family].templates[index]`.

Build a pattern from that template: escape the template, replace the escaped
`{subject}` slot with `.+?`, and match the query text against it with
`re.fullmatch`. A text that is a rendering of the template matches; a text
that is not does not.

```python
pattern = re.escape(template).replace(re.escape(SUBJECT_SLOT), ".+?")
if re.fullmatch(pattern, query.text) is None:
    return None
```

Fail closed: when the family cannot be determined, `_family_of` returns
`None`, exactly as it does today for an id it cannot parse. `admit` already
refuses a `None` family. Do not raise from `_family_of`; it is a classifier.

## The second half of the same fix

`record_execution` does `family = _family_of(query) or query.family`. That
fallback hands the caller their family back whenever the code cannot work it
out, which is the same hole from the other side. Remove the fallback: when
the family cannot be determined, the execution is refused.

Raise a `ValueError` naming the query id, the way the other refusals in that
method do.

## Tests

Two tests, each of which fails when the rule is broken:

1. A query that claims a template it was not rendered from is refused, and its
   family is not counted towards coverage.
2. An execution of such a query is refused rather than counted under the
   family the caller claimed.

And one test that the legitimate paths still work, so the new check cannot
break the real ones:

3. A query built by `generate_queries` and a query built by
   `generate_derived_queries` are both admitted, and both are counted under
   the family their template belongs to. The derived one renders its subject
   rather than its seed, so it is the case that would break if the check
   assumed the seed.

## Do not change

- The blast radius, the twelve files.
- The families, the templates, the marker table and the `template_id` format.
- Anything else from the first fix round.

## Before you report back

Run `uv run pytest`. The whole suite must be green.

Report the verbatim output, the two changes with their file and line, and the
three tests you added.

---

# Fix round 3

The harden pass found one gap that matters. Fix it, and one small thing it
noted alongside it.

## The gap: an execution does not check that the query was admitted

`frontier.py::record_execution` records a search without checking that the
query came through `admit`, `propose` or `expand`. It never checks that the
source is one the caller admitted, it never runs the budget admission check,
and it never runs the duplicate check.

The harden node proved the bypass. It built a `Query` by hand against a source
that was not in `source_ids`, called `record_execution` on it, and the
frontier accepted it: `searched_sources` gained the inadmissible source, which
pollutes the `source_coverage` verdict, `budget.queries` went to one, which
bypasses the per-run, per-source, per-family and cost limits, and the family
coverage counter moved.

The admission gate is the point of the frontier, and the harden report calls
the bypass a trust-boundary problem. Close it.

Fix: a query that represents a real search must have been admitted. Every
admitted query that has not yet run sits in `_pending`, and `record_execution`
removes it when the search is recorded, so `_pending` membership is exactly
"admitted and not yet run". Require it, and refuse an execution whose query is
not in `_pending`, naming the query id the way the other refusals in that
method do.

The two refusal statuses are the exception, and deliberately so. `duplicate`
and `over_budget` describe a query that was *not* admitted, so requiring
admission for them would make them impossible to record. They skip the check.

Be careful about the order of the checks. The "this query already ran" refusal
and the new "this query was never admitted" refusal are different, and the
message must say which one fired.

## The small thing: a source candidate's fields are not admission authority

The harden node noted that pydantic's `model_construct` bypasses validation, so
a caller who deliberately calls it can build a `SourceCandidate` that claims
`terms_status="permitted"` and an allowed use. That is general pydantic
behaviour and not a defect in the validation, and nothing in this package
consumes a candidate's trust fields to admit a source: admission is the
frontier's `source_ids` gate, and the source registry is the only thing that
admits a source.

Do not add a mechanism to stop it. Say it in the docstring instead: the
fields of a candidate are a proposal, and no consumer may treat them as
admission authority. That is the fact a later wave needs to know.

## Tests

Two tests:

1. An execution of a query that was never admitted is refused, and the
   frontier's counters do not move: not the family coverage, not the searched
   sources, not the budget.
2. A refusal status is still recordable for a query that was never admitted,
   because that is what a refusal is.

One test for the docstring's claim is not needed. A comment is not a rule.

## Do not change

- The blast radius, the same twelve files.
- The families, the templates, the marker table, the `template_id` format.
- The status vocabulary and the id prefixes.
- The rule that a failure is never saturation.
- Anything from the earlier fix rounds.

## Before you report back

Run `uv run pytest`. The whole suite must be green.

Report the verbatim output, the two changes with their file and line, and the
two tests you added.

---

# Fix round 4

The second review pass returned "ship with fixes" with seven items, and a Lean
finding. Fix all of them.

## F1: a raw objective can still reach a derived family

The spec says the competitor and counter-evidence families "cannot be written
from a raw objective … Both are generated later, from a `NormalizedProblem`".
`FamilySpec.requires_normalized_problem` says so in code, but nothing reads
that field except the one line that builds `DERIVED_FAMILIES`.

The marker table carries markers for the two derived families, so the marker
path in `_family_of` assigns `competitor` or `counter_evidence` to a query that
was written from a raw objective, with no normalized problem anywhere. The
reviewer showed it: the text "alternatives to invoice reconciliation" was
assigned `competitor` by the marker `alternatives to`, admitted, and counted
under `competitor`.

That matters beyond tidiness. The `competitor_search` and `counter_evidence`
gate keys in `config/research_contract.yaml` have a `min_query_families`
threshold, and a family that a model can reach with a wording is not the
mandatory validation the spec asks for.

Fix, in two places:

1. In `_family_of`, the marker path must refuse a family that
   `requires_normalized_problem`. A raw objective cannot name a derived
   family. Return `None`, which `admit` already refuses.
2. `expand` must not render a derived family's templates from the raw seed. A
   derived query needs a `NormalizedProblem`, and the frontier has none. Exclude
   the derived families from the expansion order.

The saturation coverage check keeps counting every required family, derived
ones included, because the caller can add derived queries itself: render them
with `generate_derived_queries` from a `NormalizedProblem` and admit them, and
the frontier counts them like any other admitted query.

## F2: the expansion can stop with no reason, and a multi-line seed kills it

Two triggers, both silent. The reviewer showed both.

(a) A multi-line or blank seed. The rendering check builds a pattern whose
subject slot is `.+?`, and `.` does not match a newline. A seed with a newline
renders a text that no longer matches its template, every variant is refused,
and the frontier can never expand. A user objective is routinely multi-line.
The reviewer showed `expand(3)` returning 3 for a single-line seed and 0 for a
seed with a newline.

Fix, in three parts:

- Normalise the seed once, in the `QueryFrontier` constructor: collapse the
  whitespace runs into single spaces, so a multi-line objective renders a
  single-line query.
- Refuse an empty seed. A frontier with no objective has nothing to expand.
- Build the rendering pattern with `re.DOTALL`, so the check is not defeated
  by a newline in a subject that did not come from the seed. The derived
  subject is the problem's action and object, which are free text, so this
  case is real. Normalise that subject the same way in
  `generate_derived_queries`.

(b) A policy that wants more variants than a family has templates. Every
family has four templates, and `queries_per_family` has no upper bound, so
`queries_per_family=5` admits the four templates and then every attempt is
refused as a duplicate. The family can never reach its own coverage, and the
caller sees "not saturated, no reasons, nothing to run".

Fix: bound `queries_per_family` by the number of templates the required
families hold, in a `model_validator` on `SaturationPolicy`. A policy that
asks for more variants than the templates can supply is refused rather than
silently stalling.

And make the expansion say why it stopped. The caller needs to tell "I have
more to do" from "I am done". Give the frontier a public property that carries
the reason the last `expand` produced nothing, and set it whenever `expand`
stops with the expansion order still non-empty.

## F3: a caller-supplied budget at restore time throws the persisted spend away

`from_state` rebuilds the budget from the checkpoint only when the caller does
not supply one. Supplying a budget replaces the whole thing, counters
included, so the queries the run already spent are forgotten: the reviewer
showed a run with 2 queries and 1.0 of spend restoring as 0 and 0.0.

The tracker is the only thing that cannot be checkpointed. The budget's spend
*is* in the checkpoint. So the `budget` parameter is a second door that loses
it.

Fix: drop the `budget` parameter from `QueryFrontier.from_state` and from
`store.load_frontier`. Keep the tracker parameter, and let
`DiscoveryBudget.from_state` take the tracker, which it already does.

## F4: the cost limit does not hold

`check` refuses a query whose cost would exceed `max_cost_per_run`, but
`admit` passes a cost of 0.0 by default, `expand` passes no cost at all, and
`record` adds the spend without looking at the limit. The limit therefore
bites after the spend, not before it, which is the opposite of what the plan
promises.

The house tracker does it the right way. `research/budget.py::record` records
the spend and then raises `BudgetExceeded`, because the call already happened
and the overrun is reported from the recorded total.

Fix: `DiscoveryBudget.record` does the same. Record the spend, then raise
`BudgetExceeded` when the recorded spend is over `max_cost_per_run`. Reuse the
house `research.budget.BudgetExceeded`, so a caller that already catches one
keeps catching it.

## F5: the zero-result outcome is recorded while the frontier is blocked

The frontier is blocked when a search failed, and it reports a recorded
zero-result outcome anyway. The repository's own contract is stricter:
`research/predicates.py` refuses a zero when "a search call did not succeed,
so its result is not a zero". The discovery package asserts the opposite.

A policy with no required family at all also records a zero, which means
nothing.

Fix, two conditions on the recorded zero-result outcome: not while the
frontier is blocked, and not with an empty required-family set.

## F6: an explicit version breaks the version counter

`save_frontier` advances `frontier.checkpoint_version` only when the caller
passes no version. An explicit version writes that version and leaves the
counter where it was, so the next automatic save lands on a version that
already holds a different state and raises `CheckpointConflict`. The
reviewer showed it.

Fix: advance the counter on every successful save, whatever version was
written, so a changed state never lands on a version that already holds
another state.

## F7: four rules the tests claim to protect are not protected

The reviewer ran mutation testing and four mutations survive. Add the tests
that would kill them.

1. **The run-scope limit at admission.** The per-source and per-family scopes
   are tested at admission. The run scope is not, and it is the scope that has
   to hold: with the pending accounting removed, 60 queries are admitted
   against a run limit of 40. Add a test that admits queries across several
   sources and families until the run limit refuses one, and assert the
   refusal.
2. **The source weight in the priority.** The existing test passes for the
   wrong reason: with the source weight removed the two queries tie, and the
   tie-break on the query id happens to agree with the expected order. Assert
   the priority value itself, not the order that falls out of it.
3. **The expansion cursor.** The existing test asserts only that the second
   expansion produced a different text. With the cursor dropped, the same
   text is refused as a duplicate and the next template is rendered, so the
   assertion still holds. Assert the refusal count, and that the restored
   frontier expands without refusing anything.
4. **The checkpoint version.** Assert the version numbers explicitly, and that
   the version advances by exactly one per save, including when the caller
   passes an explicit version.

## F10: the marker table validates itself at import, and no test covers it

`families.py` builds the marker table through a `_markers` helper that raises
when a marker is not case-folded. That is a real class of typo, but it is 13
lines of runtime validation of a literal table, sitting two functions away
from where the table is used, and no test covers it.

Fix: move the check into the test suite. One line in
`tests/test_discovery_families.py` asserts that every marker is case-folded,
and `FAMILY_MARKERS` becomes the table's items directly. That is 16 lines
removed from `src/` and the protection kept.

## F9: one small test, no behaviour change

`is_duplicate` has a guard for an empty token set that is only reachable when
`near_duplicate_threshold` is 0.0, which is a legal policy value. No test
covers it, so the guard could be deleted by accident.

Keep the guard, and add a test that pins the threshold-0 behaviour. No
behaviour change.

## Do not change

- The blast radius, the same twelve files.
- The families, the templates, the marker table, the `template_id` format.
- The status vocabulary and the id prefixes.
- The rule that a failure is never saturation.
- The rule that a derived family needs a normalized problem.
- Anything from the earlier fix rounds.

## Before you report back

Run `uv run pytest`. The whole suite must be green.

Report the verbatim output, one line per finding with what you changed and
where, and the tests you added. The reviewer's probes for this round are in
`C:\Users\anubh\AppData\Local\Temp\opencode\disc-review2\`; run them and
report what they print now.

---

# Fix round 5

The harden re-pass found one gap, of the same class as the one round 3 closed.
The gap is that a query's admission is proven by its presence in `_pending`,
and `_pending` is restored from a checkpoint without being re-checked.

## The gap: a forged checkpoint can smuggle a query past the admission gates

`QueryFrontier.from_state` repopulates `_pending` directly from the checkpoint
state: the `queries` dict and the `pending` id list. It never re-runs `admit`.
So a query restored that way never passed the source gate, the family
re-derivation, the duplicate check or the budget check.

`record_execution` treats `_pending` membership as proof of admission, so a
smuggled query is recorded as a real execution and every counter moves.

The harden node proved it. It built a state whose `queries` held a valid
`Query` against a source the frontier was never given, listed that query id in
`pending`, restored the frontier, and recorded the execution: the record came
back `success`, and `family_counts`, `budget.queries`, `searched_sources` and
`seen_results` all moved. The harm is the harm round 3 closed, reached by a
different door.

Fix it in two places, because the point is that a state is data and data is
not proof.

**First, `from_state` must not trust the state.** A restored pending query must
pass the same gates it passed when it was first admitted, or it is not
pending. The cheapest correct form: check the source gate and the family
re-derivation for each restored pending query, and leave out the ones that
fail. A query the caller may not search against is not pending, and a query
whose family the code cannot work out is not pending.

Do not call `admit` to do this. `admit` compares a query against the queries
the frontier already holds, and the restored query is one of them, so it would
refuse every restored query as a duplicate of itself. Write the check where
the state is restored.

**Second, `record_execution` must re-check the source gate.** A search is only
recorded for a source the frontier was given. This is the backstop for any
path that puts a query into `_pending` without a gate, including a future one
nobody has thought of yet.

**Third, the budget must hold at execution.** `DiscoveryBudget.record` counts
the query and adds the spend, and it currently raises only when the spend is
over the cost limit. The house tracker, `research/budget.py::record`, raises
`BudgetExceeded` when the recorded spend is over *any* applicable limit, and
the query counts are limits too. Make the discovery budget do the same: record
the query, then raise `BudgetExceeded` when the run, the source or the family
count is over its limit. The same house pattern as the cost limit, so the
spend stays recorded and the overrun is reported from the recorded total.

The legitimate flow must not break. A query that was admitted holds a slot in
the pending accounting, so the count at execution time is always within the
limit. Prove that with a test rather than with an argument.

## Tests

Three tests:

1. A state whose pending list holds a query against a source the frontier was
   never given does not restore that query as pending, and recording an
   execution of it is refused. No counter moves.
2. A state whose pending list holds a query whose family the code cannot work
   out does not restore it as pending.
3. The budget raises when the query count goes over its limit, and a frontier
   that admits, runs and admits again never goes over it. Pin the count
   overrun and the cost overrun as two separate cases.

## Do not change

- The blast radius, the same twelve files.
- The families, the templates, the marker table, the `template_id` format.
- The status vocabulary and the id prefixes.
- The rule that a failure is never saturation.
- The rule that a derived family needs a normalized problem.
- Anything from the earlier fix rounds.

## Before you report back

Run `uv run pytest`. The whole suite must be green.

Report the verbatim output, the three changes with their file and line, and
the three tests you added.

---

# Fix round 6

The third review pass found one must-fix and three should-fix items, plus a
plan that has fallen behind the code. Fix all of them. This is the last round.

## M1: a budget overrun leaves the frontier half-updated, and a broken tool then reads as saturation

`frontier.py::record_execution` calls `self._count_search(query, family, cost)`
*before* it applies its own counters. `_count_search` calls `budget.record`,
which records the spend and then raises the house `BudgetExceeded` when the
cost is over `max_cost_per_run`. Everything after the raise is skipped:

- the search is not counted towards family coverage
- its result identities are not remembered, so a later search counts them as
  novel again and the novelty arithmetic is wrong
- `_consecutive_failures` is not incremented, so **a failure does not block the
  frontier**
- the record is never returned, so the caller cannot write the
  `research_events` and `spend_ledger` rows for a search that was paid for

The reviewer reached this in normal operation, with no forged state: with the
default `max_cost_per_run` of 5.0 and a search that costs 0.2, the 26th search
crosses it. The frontier then reported `blocked = False` and
`saturated = True` after a broken search, which breaks the rule that a broken
search tool is never saturation.

The cost limit cannot be enforced at admission, because the cost of a search
is not known before the search runs. So the raise is the right place for it,
and the frontier's own accounting has to survive it.

Fix, in three parts:

1. **The bookkeeping comes first, the budget comes last.** In the failure
   path, increment `_consecutive_failures` before counting the search. In the
   success path, apply the counters, then count the search last. After the
   move, a raise from the budget cannot skip a counter.
2. **The record is never lost.** `record_execution` must return the record
   even when the budget is over. A search that was paid for must be
   persistable; losing the record loses the accountability for the spend. So
   the frontier catches the house `BudgetExceeded` from `budget.record` and
   keeps it rather than letting it escape. Do not change the house
   `BudgetExceeded` and do not wrap it; catch the house class as it is.
3. **The overrun is visible.** The frontier keeps the exception and exposes
   it, so a caller can see that the run went over. Give it a public property
   that returns the exception or nothing, and put it in the saturation
   verdict's signals, so a caller that only reads the verdict still sees it.

The budget's own `record` keeps raising the house `BudgetExceeded`: a caller
that uses the budget directly still gets the house behaviour. Only the
frontier absorbs it, because the frontier is the one that must not lose the
record.

## S1: the expansion stop reason is never cleared

`expand` only sets `_expansion_stop_reason`; it never clears it. After an
expansion that stopped, a later expansion that succeeds leaves the old reason
in place, so the caller is told "no required family can be expanded any
further" while the expansion just produced a query. The property exists so a
caller can tell "more to do" from "done", and a stale answer is worse than no
answer.

Fix: clear the reason at the start of every `expand`, so it describes the last
expansion and nothing else.

## S2: the frontier records a zero-result outcome the house gate refuses

The house rule in `research/predicates.py` is per call: `explicit_zero_outcome`
refuses a zero when *any* search call in the stage did not succeed, because
"a search call did not succeed, so its result is not a zero".

The frontier only tracks **consecutive** failures, so a run whose first search
failed and whose next search came back empty reports a recorded zero, and the
gate then refuses that zero. The two halves of the same contract disagree, and
the frontier's own docstring claims the house rule.

Fix: keep a total failure count beside the consecutive one, and require it to
be zero before the zero-result outcome is recorded. The count has to survive a
checkpoint, so it goes into the frontier's state and is restored with it.

## S3: the plan is behind the code in four places

The plan is the contract the implementation is measured against, and the next
session reads it. Amend `docs/reasonix/plans/wave-2-discovery.md`:

1. The restore section still says the caller hands "the budget or the contract
   tracker" back, and shows a `load_frontier` signature with a `budget`
   parameter. The `budget=` parameter was removed: the counters always come
   from the checkpoint and only the tracker is handed back. Correct the
   signature and the prose.
2. The budget section says `record` counts the spend and nothing else. It now
   records the query, records the spend, and raises the house
   `BudgetExceeded` when the run, source, family or cost limit is over. Say so.
3. The `zero_result_outcome` paragraph does not mention the conditions that
   were added: not while the frontier is blocked, not with an empty
   required-family set, and not when any search in the run failed.
4. `expansion_stop_reason` is not in the plan at all. Add it.

## N1: the store cannot be called from inside a caller's transaction

`store.py::_transaction` runs `BEGIN IMMEDIATE` unconditionally, so a caller
that already owns a transaction gets "cannot start a transaction within a
transaction". The house has the pattern in `db/model_routing.py`: it checks
`conn.in_transaction` and only begins when the connection is not already in
one. It is private there, so the guard is copied.

Fix: only begin a transaction when the connection is not already in one.

## N2: the expansion stop reason test cannot see a stale reason

`test_the_expansion_says_when_every_required_family_is_covered` asserts only
that the reason is truthy, so it passes with a stale reason. That is the test
that should have caught S1.

Fix: strengthen it, and add the S1 case: expand to the stop, free a family
again with a failure, expand once more, and assert the reason is empty.

The other two weak tests the reviewer named are already covered by a stronger
test elsewhere, so leave them.

## Do not change

- The blast radius, the same twelve files, plus the plan document.
- The families, the templates, the marker table, the `template_id` format.
- The status vocabulary and the id prefixes.
- The house `BudgetExceeded` class itself.
- The rule that a failure is never saturation.
- The rule that a derived family needs a normalized problem.
- Anything from the earlier fix rounds.

## Before you report back

Run `uv run pytest`. The whole suite must be green.

Report the verbatim output, one line per finding with what you changed and
where, and the tests you added. The reviewer's probes for this round are in
`C:\Users\anubh\AppData\Local\Temp\opencode\disc-review6\`; run them and report
what they print now.

---

# Fix round 7

The final harden pass found one gap. It is the same gap as round 5, one gate
short. This is the last round.

## The gap: a restored pending query is not re-checked against the budget

Round 5 made `from_state` re-check the source gate and the family
re-derivation for every restored pending query. It did not re-check the
budget.

`_pending` membership is the frontier's proof of admission, and
`record_execution` trusts it. A checkpoint whose `pending` list holds more
queries than the budget allows restores them all as pending, and each of them
can then be run, past the per-run, per-source and per-family limits.

The harden node's smallest fix: re-check the budget in `from_state` for each
restored pending query, and leave out the ones the budget will not admit.

Two details matter.

**The check has to count the queries restored so far.** The budget's admission
check takes the pending counts, because an admitted query is a query the run is
committed to. So the restore has to pass the same counts, growing as the
restore goes on: the number of pending queries restored so far in total, for
that source, and for that family. The frontier already has a helper for the
per-source and per-family counts.

**The legitimate restore must not lose a query.** Here is the argument, so you
can check the code against it rather than trusting it. The sum of the executed
count and the pending count for a scope is invariant across the admit-and-run
cycle: an admission adds one to pending, an execution removes one from pending
and adds one to executed. So the total at save time is the total that the
admission checks already approved, and re-checking it at restore time approves
the same queries. The order the pending queries are restored in does not
change the totals, and the intermediate counts are never larger than the
total.

Prove that with a test rather than with an argument: a frontier that admitted
queries, ran some of them, and checkpointed must restore exactly the pending
queries it had, with no query dropped.

## Tests

Two tests:

1. A state whose pending list holds more queries than the budget allows does
   not restore them all as pending: the ones the budget refuses are left out.
2. A legitimate checkpoint restores exactly the pending queries it had. The
   frontier admitted a set of queries, ran some of them, saved, restored, and
   the pending set is the same. No query is dropped and none is added.

## Do not change

- The blast radius, the same twelve files.
- The families, the templates, the marker table, the `template_id` format.
- The status vocabulary and the id prefixes.
- The house `BudgetExceeded` class itself.
- The rules that a failure is never saturation and that a derived family needs
  a normalized problem.
- Anything from the earlier fix rounds.

## Before you report back

Run `uv run pytest`. The whole suite must be green.

Report the verbatim output, the change with its file and line, and the two
tests you added.

---

# Fix round 8

The last round. Two items, one of them a document.

## The budget overrun signal does not survive a checkpoint

The frontier keeps the house `BudgetExceeded` it absorbed and exposes it as
`budget_overrun`, and the verdict carries a `budget_overrun` signal. The
checkpoint has no field for the exception, so a restored frontier reports
`budget_overrun: 0.0` even when the recorded spend is over the limit. The
signal is a fact about the run, and the run's spend is in the checkpoint, so
the signal has to survive the restore.

Fix: derive the signal from the budget rather than from the kept exception.
Give `DiscoveryBudget` a way to say whether it is over any of its limits, in
the spirit of `research.budget.BudgetTracker.exceeded()`, and let the frontier
report the signal from that. The kept exception stays as it is: it says what
happened in this session, which is a different fact from whether the run is
over.

Check what the house `BudgetTracker.exceeded()` returns and follow its shape.

Add a test: a frontier whose spend is over the limit, checkpointed and
restored, reports the overrun signal.

## The plan is behind the code

`docs/reasonix/plans/wave-2-discovery.md` is inside the declared radius of
this run, so amend it. The plan is the contract the next session reads, and
rounds 4 to 7 moved the code past it.

Bring the plan into line with the code as it is now. The parts that drifted:

- the restore section: a restored pending query passes the source gate, the
  family re-derivation and the budget check before it is pending again, and
  the caller hands back the tracker, not the budget;
- the budget section: `record` counts the query and the spend and raises the
  house `BudgetExceeded` when the run, source, family or cost limit is over,
  and the frontier absorbs that exception so a paid-for search keeps its
  record, exposing the overrun as `budget_overrun`;
- the `zero_result_outcome` conditions: not while the frontier is blocked, not
  with an empty required-family set, and not when any search of the run
  failed;
- `expansion_stop_reason`, which the plan does not mention at all;
- the frontier section: the execution refuses a query that was never admitted,
  and the refusal statuses are the exception;
- the store section: the write joins a caller's transaction instead of always
  beginning one;
- the test plan: the tests that were added in rounds 1 to 7, named.

Then read `docs/reasonix/specs/wave-2-discovery.md` and amend it too if the
code moved past it. The spec is the reasoning; it should not describe a
mechanism the code no longer has.

## Do not change

- The source and test files, apart from the budget signal and its test.
- The families, the templates, the marker table, the `template_id` format.
- The status vocabulary and the id prefixes.
- The house `BudgetExceeded` class itself.
- The rules that a failure is never saturation and that a derived family needs
  a normalized problem.

## Before you report back

Run `uv run pytest`. The whole suite must be green.

Report the verbatim output, the two changes with their file and line, the test
you added, and a list of the plan and spec paragraphs you amended.

---

# Fix round 9

The fourth review pass and the last harden pass both came back with no
must-fix and a set of should-fix items. Fix all of them. This is the last
round, so be complete.

Two of the findings are the same defect seen from two sides, so read them
together: the duplicate check is not scoped to a source, and it is not
re-applied when a checkpoint is restored.

## F1: an explicit checkpoint version makes the next write land on an old version

`store.py::save_frontier` advances `frontier.checkpoint_version` on every
successful save, and `from_state` computes the next version from the version
the state records. But the state records the frontier's counter, not the
version the row was actually written at.

So `save_frontier(conn, run_id, frontier, version=7)` writes a row at version
7 whose state says version 3. A restore from that row computes 4 as the next
version, and the next write lands on version 4, which is older than 7.
`load_frontier` reads the newest version first, so it never sees the write.
Or the write lands on a version that already holds a different state and
`save_checkpoint` raises `CheckpointConflict`, and the run cannot be saved at
all. The reviewer proved both.

Fix it in two places, so the two agree whatever version was written:

- The state that goes into the row must record the version the row was
  written at. Build the state with that version in it.
- The version the frontier lands on after a restore must be one past the
  newest checkpoint, not one past what the state happens to say. `load_frontier`
  reads the checkpoint, and the checkpoint knows its own version, so use it.

## F2: a partial search counts as a success for the zero-result outcome

The frontier counts `failure` and `timeout` as searches that did not succeed,
and a `partial` search as one that did. The house gate counts it the other
way: `research/predicates.py` refuses a zero-result outcome when "a search
call did not succeed", and a `ToolCallStatus.partial` call is not a successful
one. So a run whose only search was partial reports a recorded zero, and the
gate then refuses that zero. The reviewer proved it.

Fix: two counters, with two jobs.

- The counter that blocks the frontier keeps counting `failure` and `timeout`
  only. That is the tool-broke rule, and it must not change.
- The counter the zero-result outcome reads counts every search that did not
  succeed, so `partial` joins `failure` and `timeout` there.

Rename the second counter so its name says what it counts, and update the
checkpoint key with it.

## F3: the frontier can never spread a family over the admissible sources

Two defects, one consequence.

**The duplicate check is not scoped to a source.** The query id is
`source|text`, so two queries with the same text against different sources are
different queries and have different ids. The duplicate check compares the
text alone, so the second one is refused as a duplicate of the first. The
frontier can therefore hold at most one query per distinct text, across every
source, and the same wording can never be searched against a second source.

**`_least_used_source` counts executed queries, not admitted ones.** It reads
`_source_runs`, which is incremented when a search is recorded, not when a
query is admitted. A caller that expands a batch before running anything sees
every source at zero and gets the same source every time.

The consequence the reviewer reported: `source_coverage` is unreachable
through the code's own expansion, and the per-source discovery limits are
never exercised because only one source is ever used.

Fix both.

- Scope the duplicate checks to the source. Two queries with the same text
  against the same source are duplicates. The same text against a different
  source is a different search and is admitted. Say this in the spec and the
  plan, because the rule as it stands says the text alone decides.
- Make the source choice count the queries that are admitted, not only the
  ones that have run.

Then the frontier can search every admissible source, and the source coverage
reason can actually fire.

**And re-apply the duplicate check when a checkpoint is restored.** The
harden pass found this: the restore re-checks the source gate, the family
re-derivation and the budget, but not the duplicate check, so a forged
checkpoint can smuggle in a query that duplicates one the frontier already
holds. Run the same duplicate check the restore's other gates run, scoped to
the source the same way.

## F4: three load-bearing rules have no test that would fail if the rule broke

The reviewer's mutation testing found three. Add one test each, each of which
fails when the rule is broken:

1. The record id is checked against its fields. Removing that check lets two
   searches share a hand-written id, and the store then writes two events and
   one spend row, so the spend is under-recorded.
2. The restore's family-equality check: a restored pending query whose declared
   family is not the family the code derives is not restored as pending.
3. The `budget_exhausted` branch that fires when no pending query is still
   admissible. Pin that branch specifically, not just the case where the run
   limit is spent.

## F5: the event and the spend row can disagree

`store.py::write_record` writes the event row and the spend row. When the
connection is not already in a transaction, `_transaction` begins one, so the
two writes roll back together. When the caller already owns a transaction,
`write_record` joins it, and a failure of the spend write leaves the event
row inside the caller's uncommitted transaction. If the caller commits anyway,
the event persists without its spend row.

The harden pass proved it with a trigger that refuses the spend insert: after
the failure and a commit, `events: 1 | spend rows: 0`.

Fix: make the two writes atomic in both cases, without taking the caller's
transaction away from the caller. SQLite has the tool for that: a savepoint.
Begin a savepoint before the two writes, release it when they succeed, and
roll back to it when they fail. A rollback to a savepoint undoes the event
insert and leaves the caller's outer transaction exactly as it was.

The replay path, which writes a missing spend row, belongs inside the same
protection.

## F6: two small ones and one deletion

- `DISCOVERY_STAGE` is defined twice, once in `frontier.py` and once in
  `budget.py`. Define it once and import it. The reviewer counts this as two
  lines that can go.
- The package docstring in `__init__.py` calls the saturation thresholds
  "module constants". They are not: they are fields of a policy the caller
  supplies, and the architecture document was already corrected to say so.
  Correct the package docstring the same way.
- `admit` takes a `cost`, and `record_execution` takes six keyword parameters,
  and none of them has a test. Add one test that passes a non-default cost
  through `admit` and a non-default set of the keyword parameters through
  `record_execution`, and asserts the values reach the record.

## F7: the Lean finding

The reviewer found seven removable lines and named them.

- `queries.py::_render` is called from exactly one place, so the helper can be
  inlined there. Keep the one-line reason as a comment. The reviewer says five
  lines.
- The duplicated `DISCOVERY_STAGE`, above. Two lines.

Take both. Do not look for more: the reviewer ran a dead-code scan over every
module-level definition and constant in the package and found nothing else.

## Do not change

- The blast radius, the same twelve files, plus the plan and the spec.
- The families, the templates, the marker table, the `template_id` format.
- The status vocabulary and the id prefixes.
- The house `BudgetExceeded` class itself.
- The rules that a failure is never saturation and that a derived family needs
  a normalized problem.
- The `partial` status: it stays a status, it stays a completed search for
  family coverage, and it is only the zero-result outcome that changes.

## Before you report back

Run `uv run pytest`. The whole suite must be green.

Report the verbatim output, one line per finding with what you changed and
where, the tests you added, and the plan and spec paragraphs you amended. The
reviewer's probes for this round are in
`C:\Users\anubh\AppData\Local\Temp\opencode\disc-review8\`; run them and report
what they print now.

## Before you report back again

Run `uv run pytest`. The whole suite must be green.

Then re-run the reviewer's reproductions under
`C:\Users\anubh\AppData\Local\Temp\opencode\disc-review\` and report what each
one prints now. A fix that the reproduction still reproduces is not a fix.

Report: the verbatim `uv run pytest` output, one line per finding with what
you changed and where, the reviewer's reproduction output after the fix, and
anything you did not fix and why.


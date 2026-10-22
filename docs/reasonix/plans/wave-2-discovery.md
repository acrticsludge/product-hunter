# Wave 2, Session 2: implementation plan

The spec is `docs/reasonix/specs/wave-2-discovery.md`. This plan turns it into
code. The spec holds the reasoning; this file holds the shape of the diff.

## Module layout

```
src/product_hunter/discovery/
  __init__.py     the public surface
  families.py     the family taxonomy, the templates, the marker table
  queries.py      the Query type, ids, normalization, dedup, generation
  records.py      the DiscoveryRecord and its status vocabulary
  budget.py       the discovery budget and its limits
  frontier.py     the frontier, the yield accounting, the saturation verdict
  store.py        persistence into research_events, spend_ledger, research_checkpoints
```

Six modules and a package init. Each one has a single job, and the dependency
arrows only point one way: `families` and `records` and `budget` know nothing
about the frontier, and `queries` knows only `families`.

```
families ──> queries ──┐
                        ├──> frontier ──> store
records ────────────────┘
budget ─────────────────┘
```

`store` depends on `frontier` for the checkpoint round trip, and on `records`
for the event payload. Nothing depends on `store`.

## `families.py`

`QueryFamily` is a `str` enum with eight members: `problem`,
`alternative_wording`, `severity`, `workflow_pain`, `workaround`,
`existing_solution`, `competitor`, `counter_evidence`.

`FamilySpec` is a pydantic model with `family`, `description`, `templates`,
`mandatory`, `requires_normalized_problem` and `priority_weight`. The
`FAMILY_SPECS` mapping holds one spec per family. `MANDATORY_FAMILIES` holds
the six that can be generated from a raw objective.
`DERIVED_FAMILIES` holds `competitor` and `counter_evidence`, the two that
need a `NormalizedProblem` first.

Every template holds one `{subject}` slot. The templates are the code-owned
search strategy from spec §7, so the wording comes from the repository rather
than from a model. Each family gets four templates.

`FAMILY_MARKERS` is an ordered tuple of `(family, markers)`. The order is the
specificity order, and the first family whose marker appears in the
case-folded text wins.

`assign_family(text)` returns `(family, reason)`. The reason is
`"marker:<the marker that matched>"` or `"default"`. It is a pure function of
the text, so it returns the same pair every time.

Two code paths assign a family, and they are different on purpose. A query the
frontier generated takes the family of the template that generated it. A query
that arrived from outside, from a model for instance, takes the family the
marker table assigns. Both are worker-side decisions, and neither consults a
model.

## `queries.py`

`normalize_query(text)` case-folds, drops everything that is not a word
character or whitespace, and collapses the whitespace runs.

`query_id(text, source_id)` is `q_` plus the first 16 hex characters of the
SHA-256 of `source_id|normalized_query`. The same query against the same source
is the same id in every run.

`Query` is a pydantic model with `query_id`, `text`, `normalized`, `family`,
`source_id`, `seed`, `parent_query_id`, `template_id`, `assigned_by`,
`assignment_reason`, `depth` and `created_at`. `assigned_by` is always
`"worker"`. The timestamp is timezone-aware, and the model refuses one that is
not.

`SourceCandidate` is a pydantic model with `candidate_id`, `source_id`,
`domain`, `community`, `source_type`, `discovered_by`, `discovered_at`,
`status`, `terms_status` and `allowed_use`. The status starts at `proposed`,
the terms status at `unknown`, and the allowed use is empty. A candidate is a
proposal until a human reviews it, and the model refuses to be built any other
way.

`is_duplicate(left, right, threshold)` compares the normalized text. Equal text
is a duplicate. Otherwise the token sets are compared by Jaccard similarity,
and a similarity at or above the threshold is a duplicate. The threshold
default is 0.9.

`generate_queries(seed, families, source_id, ...)` renders the templates of
each family with the seed as the subject and returns the queries. The family
of each query is the family of its template, and the template id travels with
it.

`generate_derived_queries(problem, source_id, ...)` takes a
`product_hunter.normalization.NormalizedProblem` and returns the `competitor`
and `counter_evidence` queries. The subject is the problem's action and
object, and the seed is the normalized key.

## `records.py`

`DiscoveryStatus` is a `str` enum with `success`, `empty`, `failure`,
`timeout`, `partial`, `duplicate` and `over_budget`.

`DiscoveryRecord` is a pydantic model with `record_id`, `run_id`, `stage`,
`tool`, `agent`, `query_id`, `query`, `family`, `source_id`, `timestamp`,
`result_count`, `useful_count`, `novelty`, `duplicate_rate`, `cost`, `status`,
`result_ref` and `error`.

The record id is `dq_` plus the first 16 hex characters of the SHA-256 of
`run_id|query_id|timestamp|status`, so the same record always carries the same
id.

The model enforces the rules that keep the vocabulary honest:

- `success` needs a result count above zero, and `empty` needs exactly zero.
- `failure` and `timeout` need a result count of zero.
- `duplicate` and `over_budget` need a result count of zero.
- `useful_count` never exceeds `result_count`.
- `novelty` and `duplicate_rate` lie in `[0, 1]`.
- with a result count above zero, `novelty + duplicate_rate == 1`.
- with a result count of zero, both are zero.
- the timestamp is timezone-aware.
- the cost is a finite number and never negative.
- a `result_ref`, when present, holds no whitespace and matches
  `^[A-Za-z0-9._:/-]+$`, the rule `research.evidence` already enforces.

The record checks its own arithmetic, and nothing more. It does not know what
the frontier has already seen, so it cannot tell a novelty the frontier
computed from one a caller wrote: the frontier computing the novelty is the
guarantee, and the record refusing a novelty that does not add up is the rest
of it.

`to_tool_call_provenance()` turns a record into a
`product_hunter.research.evidence.ToolCallProvenance`, so the research gate
can read a discovery run without knowing this package. The mapping is
`query_or_operation` from the query text, `query_family` from the family, and
the status mapped onto `ToolCallStatus`: `success` and `empty` both become
`success`, because an empty search did run. `duplicate` and `over_budget`
return `None`, because no tool ran for those.

## `budget.py`

`DiscoveryBudget` is a plain class, not a pydantic model, because it holds
counters and may hold a `product_hunter.research.BudgetTracker`.

`DISCOVERY_STAGE` is the stage the discovery queries run in. It is defined
once, in this module, and the frontier and the store import it.

Its limits are `max_queries_per_run`, `max_queries_per_source`,
`max_queries_per_family` and `max_cost_per_run`. It counts the queries per
run, per source and per family, and the total cost.

`check(source_id, family, cost, pending_total, pending_source, pending_family)`
returns a `research.budget.BudgetDecision` naming the scope that refused. The
scopes are `per_run`, `per_source`, `per_family` and `per_cost`. The pending
counts are the queries the frontier has admitted and not yet run: they are
added to the spend, so the limit holds at the moment of admission and a batch
of admissions cannot overrun it. The counters stay the executed ones, so
nothing is counted twice. When a `BudgetTracker` is supplied, the tracker is
consulted too, with the `search_queries` dimension at the `discovery` stage, so
the research contract's limits still bind.

A non-finite or negative cost is refused: a non-finite amount makes every
comparison against it false, and the research budget refuses it with
`research.budget.NonFiniteAmount` for the same reason. `record` raises it; the
counters and the spend are left alone.

`record(source_id, family, cost)` counts the query and the spend, and then
raises the house `research.budget.BudgetExceeded` when the run, the source, the
family or the cost limit is over. The overrun is reported from the recorded
total, the way the research budget reports it: the call already happened, so
the limit is measured from what was spent. The frontier absorbs that exception
rather than letting it escape, so a search that was paid for keeps its record,
and it exposes the overrun as `budget_overrun`.

`exceeded()` returns the names of the limits the run is over: the scopes the
house `BudgetExceeded` carries, so `per_run`, `per_source`, `per_family` and
`per_cost` are the names it returns. The list is empty while the run is within
every limit, and it is measured from the recorded totals, so it says whether
the run is over and not whether a call was refused in this session. The
contract tracker is not measured there: it is the contract's ledger, and the
contract's gate reports an overrun against the contract's own limits.

`from_state(state, tracker=None)` rebuilds a budget from a checkpoint. The
tracker is not part of the state: a tracker is a ledger that lives in the run,
so the caller hands it back.

## `frontier.py`

`SaturationPolicy` is a pydantic model with `min_novelty`,
`max_consecutive_low_yield_batches`, `max_duplicate_rate`, `required_families`,
`queries_per_family` and `near_duplicate_threshold`. `queries_per_family` is
the number of searches a family needs before it counts as covered, and the
number of variants the expansion generates for it. It defaults to 3.

`SaturationVerdict` is a pydantic model with `saturated`, `reasons`,
`blocked`, `blocked_reason` and `signals`.

`QueryFrontier` holds the seed, the admissible source ids, the policy, the
budget, a clock and a checkpoint version.

`expand(count)` grows the frontier along the code-owned families. Each required
family has a cursor over its own templates, and the next variant of the least
covered family is rendered for the seed and admitted. The caller says how many
queries it wants; the frontier says which family they come from. The cursor is
part of the state, so a resume continues where the frontier left off.

The source a variant is rendered for is the admissible source the frontier has
admitted the fewest queries against. The count is the admitted queries, not
the executed ones: a batch of admissions that has not run yet is already a load
on that source. So the searches spread over the sources the caller gave, and
the source coverage reason can fire.

When the expansion cannot go on, `expansion_stop_reason` says why: no required
family can be expanded any further, or no variant of the families that still
need a search could be admitted. The reason describes the last expansion and
nothing else, so it is cleared at the start of every `expand`.

`admit(query, cost=0.0)` refuses a query whose text duplicates one already in
the frontier against the same source, refuses one against a source the
frontier was not given, and refuses one the budget will not admit. The
duplicate check is scoped to the source: the same text against the same source
is the same search, and the same text against another source is another search,
so the frontier holds one query per wording per source. It also refuses a
query whose family is not the one the code owns: a query of a template takes
the family of that template, and only when its text really is a rendering of
it, and a query of unknown origin takes the family the marker table gives its
text. A refusal is counted, and the reason is returned. The cost of the
admission is checked against the budget, so an admission that would cross the
spend limit is refused.

`record_execution(query, ...)` takes the status, the result count, the result
identities, the useful count and the cost, and returns a
`DiscoveryRecord`. The novelty is computed here, against the result identities
the frontier has already seen, so one implementation owns the arithmetic. The
result count must equal the number of result identities, because the novelty of
a result set that cannot be identified is not knowable. A status may be a
`DiscoveryStatus` or its plain name.

A query the frontier refused as a duplicate, or that the budget would not
admit, never ran a tool. Such a query costs nothing, it counts towards no
family and no source, and it neither advances nor clears the consecutive
failure counter.

A query that represents a real search must have been admitted: an execution of
a query the frontier never admitted is refused, because that query never went
through the source gate, the budget check or the duplicate check. The two
refusal statuses, `duplicate` and `over_budget`, are the exception, because a
refusal describes a query that was not admitted. The source gate is checked
again for every execution, so a search is only recorded for a source the
frontier was given, whatever else put the query into the frontier.

The frontier's own bookkeeping comes before the budget, and the budget comes
last, so a raise from the budget cannot skip a counter. The frontier keeps the
house `BudgetExceeded` that the budget raises instead of letting it escape: a
search that was paid for must leave a record behind, and the record of a
broken search must not be lost either. The kept exception is exposed as
`budget_overrun` and says what happened in this session. The verdict's
`budget_overrun` signal is a different fact: it is the budget's own answer to
whether the run is over one of its limits, measured from the recorded totals,
so a restored frontier still reports the overrun.

`next_queries(limit)` returns the pending queries in priority order. The
priority is `0.5 * family weight + 0.3 * source weight + 0.2 * novelty
estimate`, and ties break on the query id, so the order is stable.

`saturation()` returns the verdict. The five reasons are
`family_coverage`, `source_coverage`, `budget_exhausted`,
`diminishing_novelty` and `repeated_results`. `repeated_results` is decided on
the duplicate rate of the run, which is the rate the verdict reports; the rate
of the last execution is a separate signal under its own name.

`budget_exhausted` is true when the run-level limit is spent, even with
nothing pending, and when no pending query is still admissible.

The blocked rule is the important one. A recorded failure starts a consecutive
failure count, and a success or an empty result clears it. While the count is
above zero the frontier is blocked, and a blocked frontier never reports
saturation. A failure never counts towards family coverage and never advances
the low-yield counter. The frontier also keeps the number of the searches of
the run that did not succeed beside the consecutive ones, and that number is
part of the state. A failure, a timeout and a partial search all count there,
because the house gate refuses a zero when any search call in the stage did not
succeed, and a partial call is not a successful one.

`zero_result_outcome()` returns a `product_hunter.research.evidence.ZeroResultOutcome`.
It is recorded only when a search came back empty, every required family has
been executed, the frontier is not blocked, the required-family set is not
empty, and no search of the run that did not succeed. The house gate in
`research/predicates.py` is per call: it refuses a zero when any search call
in the stage did not succeed, so a run whose first search failed does not
record a zero either. A partial search is a completed search and counts
towards the coverage, but it is not a successful one, so it keeps the outcome
unrecorded. A failed or unsearched family keeps it unrecorded. A search that
comes back with results clears the outcome, so it describes the run as it
stands.

`checkpoint_state()` returns a JSON-ready dict, and `QueryFrontier.from_state`
builds a frontier back from one. The caller hands the contract tracker back at
restore time, so the contract's limits still bind after a resume. The budget
is not handed back: the counters always come from the checkpoint, and a
supplied budget would replace them and lose the spend the run already made.

The state is data and data is not proof. A restored pending query passes the
source gate, the family re-derivation, the duplicate check and the budget check
again before it is pending again, so a forged state cannot smuggle a query
past the gates it never passed. The duplicate check is scoped to the source
there too, so a restore cannot smuggle in a query that duplicates one the
frontier already holds against the same source. The budget check takes the
pending counts the restore has approved so far, which are the counts the
admission itself saw, so a legitimate restore drops no query and adds none.

## `store.py`

`write_record(conn, record)` inserts a `research_events` row with the event
type `DISCOVERY_QUERY_EXECUTED`, and a `spend_ledger` row for the cost.

There is no public event writer in the repository. `registry._insert_event` and
`store.Store._insert_event` are both private, so this module owns its insert.
The event id follows the convention the other two use: `evt_` plus the first
16 hex characters of the SHA-256 of a basis string. The basis here is
`run_id|event_type|query_id|timestamp`, so a replay of the same record
produces the same event id.

The spend row uses `units` for the query count and `cost_micros` for the cost
in millionths of a dollar, which is the unit the column name says. The event
and the spend are written under one savepoint, so a failure cannot leave the
event without its spend row, and a replay repairs a spend row that is missing.
A savepoint is the tool for it because the caller may already own a
transaction: a rollback to the savepoint undoes the event insert and leaves
the caller's outer transaction exactly as it was, so the caller still owns the
commit. A connection that is free gets the savepoint's own transaction, and
the release commits it. The replay path, which writes the missing spend row,
is inside the same protection.

`list_records(conn, run_id)` reads the records of a run back, oldest first.

`save_frontier(conn, run_id, frontier, version, step)` writes the frontier
state through `product_hunter.db.checkpoints.save_checkpoint`. The frontier
carries its own checkpoint version: the first save writes version one, and the
version advances after every successful save, so a changed state never lands on
a version that already holds another state. The state records the version the
row is written at, so a restore continues after that row and not after the
version the frontier happened to hold.

`load_frontier(conn, run_id, step, version, tracker)` reads it back
through `load_checkpoint` and rebuilds the frontier. The contract tracker is
not part of the checkpoint, so the caller hands the tracker back. The budget's
own counters come from the checkpoint, so a resume never forgets what the run
already spent. The restored frontier lands on the version after the newest
checkpoint of the run, so a restore never writes a version the run has moved
past and never writes over a state that is already there.

## The tests

Five files, one per module, plus the store.

`tests/test_discovery_families.py` covers the taxonomy and the assignment: every
family has a spec, the mandatory and derived sets are disjoint and together
cover every family, a marker text maps to its family, an unknown text falls
back to the default family, and the same text always maps to the same family.

`tests/test_discovery_queries.py` covers the ids, the normalization and the
dedup: the same text and source give the same id, a different source gives a
different id, normalization folds case and punctuation, an equal text is a
duplicate, a reworded text above the threshold is a duplicate, a different text
is not, generation assigns the family of the template, derived generation
needs a normalized problem and returns only the two derived families, and a
source candidate starts as a proposal.

`tests/test_discovery_budget.py` covers the limits: the per-run, per-source,
per-family and cost limits each refuse, the refusal names the scope, and a
supplied contract tracker still binds. The pending queries bind the limits at
the moment of admission, a non-finite or negative cost is refused and not
recorded, a tracker can be handed back at restore time, the budget raises when
the query count goes over its limit, and `exceeded` names the limits the run
is over.

`tests/test_discovery_frontier.py` covers the behaviour: a duplicate query is
refused and counted, a proposed query takes the marker family, the budget stops
admission, each of the five saturation reasons fires, a failed search leaves the
frontier blocked and not saturated, a failed search does not count towards
family coverage, an empty result is a real outcome that does not saturate on
its own, the zero-result outcome is recorded only when every required family
completed, the novelty arithmetic holds, and the priority order is stable. A
refused query counts towards nothing and does not block the frontier, the
budget is exhausted only when no pending query is still admissible, the result
count must match the result identities, a caller cannot choose the family of a
query, the expansion follows the code-owned families and its cursor survives a
checkpoint, and the priority counts the source weight and the novelty estimate.
A budget overrun does not lose the record and does not skip a counter, a
broken search still blocks the frontier however much it cost, the expansion
stop reason describes the last expansion only, and a zero result is not
recorded when any search of the run failed. An execution of a query the
frontier never admitted is refused, and the overrun signal survives a
checkpoint.

`tests/test_discovery_store.py` covers the persistence: a record writes an event
and a spend row, the records read back, the frontier checkpoints and reloads
with its pending queries and its seen results intact, and a restored frontier
refuses to run a query it already ran. The event and the spend are written in
one transaction, a replay repairs a missing spend row, a second save writes the
next checkpoint version, a write joins a caller's transaction, and a restored
frontier still binds the contract tracker.

The identifiers are pinned by golden values, not by comparing a function with
itself: a test that says the id of this exact input is this exact string.

### The tests the review rounds added

The rounds after the first one added these, named, so the next session can
find the test that protects a rule instead of reading the whole file:

- The limits hold at the moment of admission:
  `test_the_limit_binds_on_admission_and_not_after_the_fact`,
  `test_the_run_limit_binds_at_admission_across_sources_and_families` and
  `test_the_pending_queries_bind_the_limits`.
- A refusal is not a search and not a failure:
  `test_a_refused_query_counts_towards_no_family_and_no_source`,
  `test_a_refusal_does_not_block_the_frontier` and
  `test_a_refusal_does_not_clear_the_failure_counter`.
- The exhaustion signal says when the run has no room left:
  `test_a_frontier_with_room_and_nothing_pending_is_not_exhausted` and
  `test_budget_exhausted_is_not_reported_while_a_pending_query_is_admissible`.
- The result count and the result identities agree:
  `test_a_result_count_that_does_not_match_the_identities_is_refused` and
  `test_an_identity_repeated_inside_one_result_set_counts_as_a_repeat`.
- The family belongs to the code:
  `test_a_caller_cannot_choose_the_family_of_a_query`,
  `test_a_forged_family_never_reaches_the_coverage_counters`,
  `test_a_query_that_claims_a_template_it_was_not_rendered_from_is_refused`,
  `test_an_execution_of_a_query_that_claims_a_template_it_was_not_rendered_from_is_refused`
  and `test_the_queries_the_code_generates_are_admitted_under_their_own_family`.
- A raw objective cannot name a derived family:
  `test_a_raw_objective_cannot_name_a_derived_family`,
  `test_an_execution_of_a_derived_family_from_a_raw_objective_is_refused` and
  `test_the_expansion_never_renders_a_derived_family`.
- The frontier expands itself:
  `test_expand_generates_the_code_owned_families`,
  `test_expand_gives_every_required_family_its_queries`,
  `test_the_expansion_cursor_survives_a_checkpoint` and
  `test_the_expansion_says_when_every_required_family_is_covered`.
- The expansion never stalls without a reason:
  `test_a_multi_line_seed_is_normalised_and_still_expands`,
  `test_an_empty_seed_is_refused`,
  `test_a_policy_that_wants_more_variants_than_the_templates_is_refused`,
  `test_the_expansion_names_the_family_it_could_not_expand` and
  `test_a_later_expansion_clears_the_stop_reason`.
- An execution is only recorded for an admitted query:
  `test_an_execution_of_a_query_that_was_never_admitted_is_refused` and
  `test_a_refusal_is_recordable_for_a_query_that_was_never_admitted`.
- The state is data and not proof:
  `test_a_forged_checkpoint_does_not_smuggle_a_query_past_the_source_gate`,
  `test_a_forged_checkpoint_does_not_smuggle_a_query_with_an_unknown_family`,
  `test_a_checkpoint_that_holds_more_queries_than_the_budget_drops_the_rest`,
  `test_a_legitimate_checkpoint_restores_exactly_its_pending_queries` and
  `test_a_legitimate_pending_query_survives_the_restore`.
- The budget reports an overrun and keeps the record:
  `test_the_overrun_is_reported_as_the_house_budget_exceeded`,
  `test_a_search_that_costs_more_than_the_limit_is_reported`,
  `test_a_budget_overrun_does_not_skip_a_counter`,
  `test_a_broken_search_that_overruns_the_budget_still_blocks_the_frontier`,
  `test_a_frontier_that_admits_runs_and_admits_again_never_goes_over_the_budget`,
  `test_the_budget_raises_when_the_query_count_goes_over_its_limit`,
  `test_recording_over_the_cost_limit_records_the_spend_and_raises`,
  `test_exceeded_names_the_limits_the_run_is_over` and
  `test_the_tracker_can_be_handed_back_at_restore`.
- The overrun signal survives a checkpoint:
  `test_a_restored_frontier_still_reports_the_budget_overrun`.
- The zero-result outcome follows the house gate:
  `test_a_zero_result_is_not_recorded_after_an_earlier_failure`,
  `test_the_total_failure_count_survives_a_checkpoint`,
  `test_a_later_search_with_results_clears_the_zero_result_outcome`,
  `test_a_blocked_frontier_does_not_record_a_zero_result` and
  `test_a_policy_with_no_required_family_records_no_zero`.
- The duplicate rate of the run is the one the verdict reports:
  `test_saturation_names_repeated_results_on_the_rate_of_the_run` and
  `test_one_unlucky_query_does_not_trip_the_repeated_results_reason`.
- The priority counts its parts:
  `test_the_priority_counts_the_source_weight` and
  `test_the_priority_counts_the_novelty_estimate`.
- The exact duplicate names the query it duplicates:
  `test_the_exact_duplicate_refusal_names_the_duplicate_query_id`.
- The marker table is checked by the test suite:
  `test_every_marker_is_case_folded`.
- The dedup guard holds at a threshold of zero:
  `test_a_threshold_of_zero_still_refuses_an_empty_token_set`.
- The derived subject is rendered on one line:
  `test_the_derived_subject_is_rendered_on_one_line`.
- The persistence is atomic and the versions advance:
  `test_the_event_and_the_spend_are_written_in_one_transaction`,
  `test_a_replay_repairs_a_missing_spend_row`,
  `test_the_write_joins_a_callers_transaction`,
  `test_the_checkpoint_version_advances_by_one_per_save`,
  `test_a_restore_does_not_take_a_budget` and
  `test_a_restored_frontier_still_binds_the_contract_tracker`.
- The ids are pinned by their basis:
  `test_the_query_id_is_the_golden_value_of_its_basis`,
  `test_the_id_is_taken_over_the_normalized_text`,
  `test_the_event_id_is_the_golden_value_of_its_basis`,
  `test_the_record_id_is_the_golden_value_of_its_basis`,
  `test_the_status_is_part_of_the_record_id` and
  `test_a_result_ref_with_an_invalid_character_is_refused`.

### The tests fix round 9 added

- The checkpoint version agrees with the row it is written at:
  `test_an_explicit_checkpoint_version_leaves_the_next_write_on_a_free_version`
  and `test_a_restore_of_an_older_version_writes_past_the_newest_one`.
- A partial search is a completed search and not a successful one:
  `test_a_partial_search_keeps_the_zero_result_unrecorded`.
- The duplicate check is scoped to the source:
  `test_the_same_text_against_another_source_is_a_different_search` and
  `test_the_same_text_against_the_same_source_is_a_duplicate`.
- The frontier spreads over the sources it was given:
  `test_the_expansion_spreads_over_the_admissible_sources`.
- The restore re-checks the duplicate and the family:
  `test_a_forged_checkpoint_does_not_smuggle_a_near_duplicate_query` and
  `test_a_forged_checkpoint_does_not_smuggle_a_query_with_a_forged_family`.
- The exhaustion signal fires when no pending query is admissible:
  `test_budget_exhausted_when_no_pending_query_is_still_admissible`.
- The record id is checked against its fields:
  `test_a_record_with_a_hand_written_id_is_refused`.
- The event and its spend roll back together in a caller's transaction:
  `test_a_failed_write_undoes_itself_and_leaves_the_callers_transaction_alone`
  and `test_the_replay_repairs_the_spend_inside_a_callers_transaction`.
- The parameters reach the record:
  `test_the_admitted_cost_and_the_execution_parameters_reach_the_record`.

## What is deliberately not in the diff

- no migration, no change to any table
- no CLI command
- no worker handler, no job dispatch
- no change to the source registry or to the collection code
- no clustering, no scoring, no Obsidian
- no HTTP client: the frontier never calls a search endpoint

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from product_hunter.discovery.budget import DiscoveryBudget
from product_hunter.discovery.families import FAMILY_SPECS, SUBJECT_SLOT, QueryFamily
from product_hunter.discovery.frontier import (
    REASON_BUDGET_EXHAUSTED,
    REASON_DIMINISHING_NOVELTY,
    REASON_FAMILY_COVERAGE,
    REASON_REPEATED_RESULTS,
    REASON_SOURCE_COVERAGE,
    QueryFrontier,
    SaturationPolicy,
)
from product_hunter.discovery.queries import (
    Query,
    generate_derived_queries,
    generate_queries,
    normalize_query,
    query_id,
)
from product_hunter.discovery.records import DiscoveryStatus
from product_hunter.normalization import Domain, NormalizedProblem, normalize_fields
from product_hunter.research.budget import BudgetExceeded
from product_hunter.research.evidence import StageExecution, StageStatus, ZeroResultOutcome
from product_hunter.research.predicates import explicit_zero_outcome

FROZEN = datetime(2026, 10, 21, 12, 0, 0, tzinfo=timezone.utc)
RUN = "run-1"


def _frontier(**overrides) -> QueryFrontier:
    return QueryFrontier(
        seed="invoice reconciliation",
        source_ids=overrides.pop("source_ids", ["hn_algolia", "github_issues"]),
        clock=lambda: FROZEN,
        **overrides,
    )


def _query(
    text: str,
    source_id: str = "hn_algolia",
    family: QueryFamily = QueryFamily.problem,
) -> Query:
    """A query rendered from the template it names, so it carries that family."""
    rendered = FAMILY_SPECS[family].templates[0].replace(SUBJECT_SLOT, text).strip()
    return Query(
        query_id=query_id(rendered, source_id),
        text=rendered,
        normalized=normalize_query(rendered),
        family=family,
        source_id=source_id,
        template_id=f"{family.value}-0",
        created_at=FROZEN,
        assignment_reason=f"template:{family.value}-0",
    )


def _forged(
    text: str,
    template_id: str,
    family: QueryFamily,
    source_id: str = "hn_algolia",
) -> Query:
    """A query that claims a template it was not rendered from."""
    return Query(
        query_id=query_id(text, source_id),
        text=text,
        normalized=normalize_query(text),
        family=family,
        source_id=source_id,
        template_id=template_id,
        created_at=FROZEN,
        assignment_reason=f"template:{template_id}",
    )


def _problem() -> NormalizedProblem:
    return normalize_fields(
        domain=Domain.operations,
        actor="finance lead",
        action="reconcile invoices",
        object_="spreadsheet",
        consequence="a lost afternoon",
        normalization_version="norm-v1",
    )


def _unknown(
    text: str,
    family: QueryFamily,
    source_id: str = "hn_algolia",
) -> Query:
    """A query of unknown origin: it carries no template."""
    return Query(
        query_id=query_id(text, source_id),
        text=text,
        normalized=normalize_query(text),
        family=family,
        source_id=source_id,
        created_at=FROZEN,
        assignment_reason="marker:default",
    )


def _admit(frontier: QueryFrontier, query: Query) -> Query:
    admitted, _reason = frontier.admit(query)
    assert admitted is True
    return query


def _forged_state(frontier: QueryFrontier, query: Query) -> dict[str, object]:
    """A checkpoint state whose pending list holds the query it names."""
    return _state_with_pending(frontier, [query])


def _state_with_pending(
    frontier: QueryFrontier, queries: list[Query]
) -> dict[str, object]:
    """A checkpoint state whose pending list holds the queries it names."""
    state = frontier.checkpoint_state()
    stored = dict(state["queries"])
    for query in queries:
        stored[query.query_id] = query.model_dump(mode="json")
    state["queries"] = stored
    state["pending"] = [query.query_id for query in queries]
    return state


def _run(
    frontier: QueryFrontier,
    query: Query,
    *,
    status: DiscoveryStatus = DiscoveryStatus.success,
    result_count: int = 1,
    result_ids: tuple[str, ...] = ("r1",),
    cost: float = 0.1,
):
    return frontier.record_execution(
        query,
        run_id=RUN,
        status=status,
        result_count=result_count,
        result_ids=list(result_ids),
        cost=cost,
        timestamp=FROZEN,
    )


def test_a_duplicate_query_is_refused_and_counted():
    frontier = _frontier()
    _admit(frontier, _query("invoice reconciliation problem"))
    admitted, reason = frontier.admit(_query("Invoice Reconciliation, Problem!"))
    assert admitted is False
    assert "duplicate" in reason
    assert frontier.refusals == 1


def test_the_exact_duplicate_refusal_names_the_duplicate_query_id():
    frontier = _frontier()
    first = _admit(frontier, _query("invoice reconciliation problem"))
    admitted, reason = frontier.admit(_query("Invoice Reconciliation, Problem!"))
    assert admitted is False
    assert first.query_id in reason
    assert "near duplicate" not in reason


def test_a_near_duplicate_query_is_refused_and_counted():
    frontier = _frontier(policy=SaturationPolicy(near_duplicate_threshold=0.5))
    _admit(frontier, _query("invoice reconciliation for accountants"))
    admitted, reason = frontier.admit(_query("invoice reconciliation for bookkeepers"))
    assert admitted is False
    assert "near duplicate" in reason
    assert frontier.refusals == 1


def test_a_query_against_a_source_that_was_not_admitted_is_refused():
    frontier = _frontier()
    admitted, reason = frontier.admit(_query("invoice reconciliation problem", "other_source"))
    assert admitted is False
    assert "not admissible" in reason


def test_a_caller_cannot_choose_the_family_of_a_query():
    frontier = _frontier(
        policy=SaturationPolicy(required_families=(QueryFamily.severity,))
    )
    admitted, reason = frontier.admit(
        _unknown("invoice reconciliation", QueryFamily.severity)
    )
    assert admitted is False
    assert "not the family" in reason
    assert frontier.family_counts == {}


def test_a_forged_family_never_reaches_the_coverage_counters():
    frontier = _frontier(
        policy=SaturationPolicy(required_families=(QueryFamily.severity,))
    )
    forged = _unknown("invoice reconciliation", QueryFamily.severity)
    admitted, _reason = frontier.admit(forged)
    assert admitted is False
    # The query was never admitted, so its execution is refused and no
    # counter moves.
    with pytest.raises(ValueError) as excinfo:
        frontier.record_execution(
            forged,
            run_id=RUN,
            status=DiscoveryStatus.success,
            result_count=1,
            result_ids=("r1",),
            timestamp=FROZEN,
        )
    assert forged.query_id in str(excinfo.value)
    assert frontier.family_counts == {}
    assert frontier.budget.queries == 0
    assert REASON_FAMILY_COVERAGE not in frontier.saturation().reasons


def test_a_rendering_with_a_line_break_in_the_subject_is_still_a_rendering():
    frontier = _frontier(
        policy=SaturationPolicy(required_families=(QueryFamily.problem,))
    )
    # "alpha\nbeta problem" is a rendering of the problem template, so the
    # family is the code's to give, whatever the subject carries.
    rendered = _forged("alpha\nbeta problem", "problem-0", QueryFamily.problem)
    admitted, reason = frontier.admit(rendered)
    assert admitted is True, reason
    record = _run(frontier, rendered, result_ids=("r1",))
    assert record.family is QueryFamily.problem


def test_a_query_that_claims_a_template_it_was_not_rendered_from_is_refused():
    frontier = _frontier(
        policy=SaturationPolicy(required_families=(QueryFamily.severity,))
    )
    # The severity family words a query as "how bad is {subject}", so this
    # text is not a rendering of the template it names.
    forged = _forged(
        "invoice reconciliation", "severity-0", QueryFamily.severity
    )
    admitted, reason = frontier.admit(forged)
    assert admitted is False
    assert "not a rendering" in reason
    assert frontier.family_counts == {}
    assert REASON_FAMILY_COVERAGE not in frontier.saturation().reasons


def test_an_execution_of_a_query_that_claims_a_template_it_was_not_rendered_from_is_refused():
    frontier = _frontier(
        policy=SaturationPolicy(required_families=(QueryFamily.severity,))
    )
    forged = _forged(
        "invoice reconciliation", "severity-0", QueryFamily.severity
    )
    with pytest.raises(ValueError) as excinfo:
        _run(frontier, forged, result_ids=("r1",))
    assert forged.query_id in str(excinfo.value)
    # The family rule is the one that fires, not the admission gate.
    assert "cannot be determined" in str(excinfo.value)
    assert frontier.family_counts == {}
    assert frontier.budget.queries == 0


def test_the_queries_the_code_generates_are_admitted_under_their_own_family():
    frontier = _frontier(
        policy=SaturationPolicy(
            required_families=(QueryFamily.problem, QueryFamily.competitor)
        )
    )
    generated = generate_queries(
        "invoice reconciliation", [QueryFamily.problem], "hn_algolia", now=FROZEN
    )[0]
    derived = generate_derived_queries(_problem(), "hn_algolia", now=FROZEN)[0]
    # The derived query renders the action and the object of the problem,
    # not its seed.
    assert derived.seed == _problem().normalized_key
    assert derived.text != derived.seed
    for query in (generated, derived):
        assert frontier.admit(query)[0] is True
        record = _run(frontier, query, result_ids=(f"r-{query.query_id}",))
        assert record.family is query.family
    assert frontier.family_counts == {"problem": 1, "competitor": 1}


def test_a_query_of_unknown_origin_takes_the_marker_family():
    frontier = _frontier()
    query, admitted, reason = frontier.propose(
        "we use a spreadsheet to reconcile invoices", "hn_algolia"
    )
    assert query.family is QueryFamily.workaround
    assert query.assignment_reason == "marker:spreadsheet"
    assert query.assigned_by == "worker"
    assert admitted is True


def test_the_budget_stops_admission():
    frontier = _frontier(budget=DiscoveryBudget(max_queries_per_run=1))
    first = _admit(frontier, _query("alpha problem"))
    _run(frontier, first)
    admitted, reason = frontier.admit(_query("beta problem"))
    assert admitted is False
    assert "per_run" in reason


def test_the_limit_binds_on_admission_and_not_after_the_fact():
    frontier = _frontier(budget=DiscoveryBudget(max_queries_per_source=2))
    assert frontier.admit(_query("alpha problem"))[0] is True
    assert frontier.admit(_query("beta problem"))[0] is True
    admitted, reason = frontier.admit(_query("gamma problem"))
    assert admitted is False
    assert "per_source" in reason
    # Nothing ran yet: the limit held at the moment of admission.
    assert frontier.budget.queries == 0


def test_the_family_limit_binds_on_admission():
    frontier = _frontier(budget=DiscoveryBudget(max_queries_per_family=1))
    assert frontier.admit(_query("alpha problem"))[0] is True
    admitted, reason = frontier.admit(_query("beta problem"))
    assert admitted is False
    assert "per_family" in reason


def test_the_run_limit_binds_at_admission_across_sources_and_families():
    frontier = _frontier(
        budget=DiscoveryBudget(
            max_queries_per_run=3,
            max_queries_per_source=10,
            max_queries_per_family=10,
        )
    )
    assert frontier.admit(_query("alpha problem", "hn_algolia"))[0] is True
    assert (
        frontier.admit(
            _query("beta severity", "hn_algolia", QueryFamily.severity)
        )[0]
        is True
    )
    assert frontier.admit(_query("gamma problem", "github_issues"))[0] is True
    admitted, reason = frontier.admit(
        _query("delta workflow", "github_issues", QueryFamily.workflow_pain)
    )
    assert admitted is False
    assert "per_run" in reason
    # Nothing ran yet: the limit held at the moment of admission.
    assert frontier.budget.queries == 0


def test_saturation_names_family_coverage():
    frontier = _frontier(
        policy=SaturationPolicy(
            required_families=(QueryFamily.problem, QueryFamily.severity),
            queries_per_family=3,
        )
    )
    for index in range(3):
        _run(
            frontier,
            _admit(frontier, _query(f"alpha problem {index}", family=QueryFamily.problem)),
            result_ids=(f"p{index}",),
        )
    assert REASON_FAMILY_COVERAGE not in frontier.saturation().reasons
    for index in range(3):
        _run(
            frontier,
            _admit(
                frontier,
                _query(f"beta severity {index}", family=QueryFamily.severity),
            ),
            result_ids=(f"s{index}",),
        )
    verdict = frontier.saturation()
    assert REASON_FAMILY_COVERAGE in verdict.reasons
    assert verdict.saturated is True


def test_a_family_is_covered_only_after_its_queries_per_family():
    frontier = _frontier(
        policy=SaturationPolicy(
            required_families=(QueryFamily.problem,), queries_per_family=3
        )
    )
    for index in range(2):
        _run(
            frontier,
            _admit(frontier, _query(f"alpha problem {index}")),
            result_ids=(f"p{index}",),
        )
    assert REASON_FAMILY_COVERAGE not in frontier.saturation().reasons


def test_saturation_names_source_coverage():
    frontier = _frontier(
        policy=SaturationPolicy(required_families=(QueryFamily.problem,))
    )
    _run(
        frontier,
        _admit(
            frontier,
            _query("alpha problem", "hn_algolia", QueryFamily.severity),
        ),
    )
    assert REASON_SOURCE_COVERAGE not in frontier.saturation().reasons
    _run(
        frontier,
        _admit(
            frontier,
            _query("beta problem", "github_issues", QueryFamily.severity),
        ),
    )
    verdict = frontier.saturation()
    assert REASON_SOURCE_COVERAGE in verdict.reasons


def test_saturation_names_budget_exhausted():
    frontier = _frontier(
        budget=DiscoveryBudget(max_queries_per_run=1),
        policy=SaturationPolicy(
            required_families=(QueryFamily.problem, QueryFamily.severity)
        ),
    )
    _run(frontier, _admit(frontier, _query("alpha problem")))
    verdict = frontier.saturation()
    assert REASON_BUDGET_EXHAUSTED in verdict.reasons


def test_a_frontier_with_room_and_nothing_pending_is_not_exhausted():
    frontier = _frontier()
    assert REASON_BUDGET_EXHAUSTED not in frontier.saturation().reasons


def test_budget_exhausted_is_not_reported_while_a_pending_query_is_admissible():
    frontier = _frontier(
        budget=DiscoveryBudget(max_queries_per_family=1),
        policy=SaturationPolicy(required_families=(QueryFamily.problem,)),
    )
    first = _admit(frontier, _query("alpha problem"))
    _run(frontier, first)
    pending = _admit(frontier, _query("beta severity", family=QueryFamily.severity))
    verdict = frontier.saturation()
    assert REASON_BUDGET_EXHAUSTED not in verdict.reasons
    assert pending.query_id in [query.query_id for query in frontier.pending]


def test_saturation_names_diminishing_novelty():
    frontier = _frontier(
        policy=SaturationPolicy(
            min_novelty=0.2,
            max_consecutive_low_yield_batches=2,
            required_families=(QueryFamily.problem,),
        )
    )
    _run(frontier, _admit(frontier, _query("alpha problem")), result_ids=("r1",))
    assert REASON_DIMINISHING_NOVELTY not in frontier.saturation().reasons
    _run(frontier, _admit(frontier, _query("beta problem")), result_ids=("r1",))
    assert REASON_DIMINISHING_NOVELTY not in frontier.saturation().reasons
    _run(frontier, _admit(frontier, _query("gamma problem")), result_ids=("r1",))
    verdict = frontier.saturation()
    assert REASON_DIMINISHING_NOVELTY in verdict.reasons


def test_saturation_names_repeated_results_on_the_rate_of_the_run():
    frontier = _frontier(
        policy=SaturationPolicy(
            max_duplicate_rate=0.5,
            required_families=(QueryFamily.problem,),
        )
    )
    _run(
        frontier,
        _admit(frontier, _query("alpha problem")),
        result_count=2,
        result_ids=("r1", "r2"),
    )
    _run(
        frontier,
        _admit(frontier, _query("beta problem")),
        result_count=2,
        result_ids=("r1", "r2"),
    )
    _run(
        frontier,
        _admit(frontier, _query("gamma problem")),
        result_count=2,
        result_ids=("r1", "r2"),
    )
    verdict = frontier.saturation()
    assert REASON_REPEATED_RESULTS in verdict.reasons
    # The reason is decided on the rate the verdict reports.
    assert verdict.signals["duplicate_rate"] == 2 / 3


def test_one_unlucky_query_does_not_trip_the_repeated_results_reason():
    frontier = _frontier(
        policy=SaturationPolicy(
            max_duplicate_rate=0.5,
            required_families=(QueryFamily.problem,),
        )
    )
    _run(
        frontier,
        _admit(frontier, _query("alpha problem")),
        result_count=4,
        result_ids=("r1", "r2", "r3", "r4"),
    )
    _run(
        frontier,
        _admit(frontier, _query("beta problem")),
        result_count=4,
        result_ids=("r1", "r2", "r3", "r4"),
    )
    verdict = frontier.saturation()
    assert verdict.signals["last_duplicate_rate"] == 1.0
    assert verdict.signals["duplicate_rate"] == 0.5
    assert REASON_REPEATED_RESULTS not in verdict.reasons


def test_a_failed_search_leaves_the_frontier_blocked_and_not_saturated():
    frontier = _frontier(
        policy=SaturationPolicy(
            required_families=(QueryFamily.problem,), queries_per_family=3
        )
    )
    for index in range(3):
        _run(
            frontier,
            _admit(frontier, _query(f"alpha problem {index}")),
            result_ids=(f"p{index}",),
        )
    assert REASON_FAMILY_COVERAGE in frontier.saturation().reasons
    record = _run(
        frontier,
        _admit(frontier, _query("beta problem")),
        status=DiscoveryStatus.failure,
        result_count=0,
        result_ids=(),
    )
    assert record.status is DiscoveryStatus.failure
    verdict = frontier.saturation()
    assert verdict.blocked is True
    assert verdict.saturated is False
    assert "consecutive search failures" in verdict.blocked_reason


def test_a_failed_search_does_not_count_towards_family_coverage():
    frontier = _frontier(
        policy=SaturationPolicy(required_families=(QueryFamily.problem,))
    )
    _run(
        frontier,
        _admit(frontier, _query("alpha problem")),
        status=DiscoveryStatus.failure,
        result_count=0,
        result_ids=(),
    )
    assert frontier.family_counts.get("problem", 0) == 0
    assert REASON_FAMILY_COVERAGE not in frontier.saturation().reasons


def test_a_failed_search_does_not_advance_the_low_yield_counter():
    frontier = _frontier(
        policy=SaturationPolicy(
            max_consecutive_low_yield_batches=1,
            required_families=(QueryFamily.problem,),
        )
    )
    _run(
        frontier,
        _admit(frontier, _query("alpha problem")),
        status=DiscoveryStatus.failure,
        result_count=0,
        result_ids=(),
    )
    assert REASON_DIMINISHING_NOVELTY not in frontier.saturation().reasons


def test_a_refused_query_counts_towards_no_family_and_no_source():
    frontier = _frontier(
        policy=SaturationPolicy(required_families=(QueryFamily.problem,))
    )
    query = _admit(frontier, _query("alpha problem"))
    record = frontier.record_execution(
        query,
        run_id=RUN,
        status=DiscoveryStatus.duplicate,
        result_count=0,
        result_ids=(),
        timestamp=FROZEN,
    )
    assert record.status is DiscoveryStatus.duplicate
    assert record.to_tool_call_provenance() is None
    assert frontier.family_counts == {}
    assert frontier.budget.queries == 0
    verdict = frontier.saturation()
    assert REASON_FAMILY_COVERAGE not in verdict.reasons
    assert REASON_SOURCE_COVERAGE not in verdict.reasons
    assert verdict.saturated is False


def test_a_refusal_does_not_block_the_frontier():
    frontier = _frontier(
        policy=SaturationPolicy(required_families=(QueryFamily.problem,))
    )
    query = _admit(frontier, _query("alpha problem"))
    frontier.record_execution(
        query,
        run_id=RUN,
        status=DiscoveryStatus.duplicate,
        result_count=0,
        result_ids=(),
        timestamp=FROZEN,
    )
    verdict = frontier.saturation()
    assert verdict.blocked is False
    assert verdict.signals["consecutive_failures"] == 0.0


def test_a_refusal_does_not_clear_the_failure_counter():
    frontier = _frontier(
        policy=SaturationPolicy(required_families=(QueryFamily.problem,))
    )
    failed = _admit(frontier, _query("alpha problem"))
    frontier.record_execution(
        failed,
        run_id=RUN,
        status=DiscoveryStatus.failure,
        result_count=0,
        result_ids=(),
        timestamp=FROZEN,
    )
    refused = _admit(frontier, _query("beta problem"))
    frontier.record_execution(
        refused,
        run_id=RUN,
        status=DiscoveryStatus.over_budget,
        result_count=0,
        result_ids=(),
        timestamp=FROZEN,
    )
    verdict = frontier.saturation()
    assert verdict.blocked is True
    assert verdict.saturated is False


def test_an_empty_result_is_a_real_outcome():
    frontier = _frontier()
    record = _run(
        frontier,
        _admit(frontier, _query("alpha problem")),
        status=DiscoveryStatus.empty,
        result_count=0,
        result_ids=(),
    )
    assert record.status is DiscoveryStatus.empty
    assert record.result_count == 0
    assert record.novelty == 0.0
    assert record.duplicate_rate == 0.0
    verdict = frontier.saturation()
    assert verdict.blocked is False
    assert verdict.saturated is False


def test_the_zero_result_outcome_is_recorded_only_when_every_required_family_ran():
    frontier = _frontier(
        policy=SaturationPolicy(
            required_families=(QueryFamily.problem, QueryFamily.severity)
        )
    )
    _run(
        frontier,
        _admit(frontier, _query("alpha problem", family=QueryFamily.problem)),
        status=DiscoveryStatus.empty,
        result_count=0,
        result_ids=(),
    )
    outcome = frontier.zero_result_outcome()
    assert outcome.recorded is False
    assert outcome.completed_query_families == ["problem"]
    _run(
        frontier,
        _admit(frontier, _query("beta severity", family=QueryFamily.severity)),
        status=DiscoveryStatus.empty,
        result_count=0,
        result_ids=(),
    )
    outcome = frontier.zero_result_outcome()
    assert outcome.recorded is True
    assert outcome.completed_query_families == ["problem", "severity"]
    assert outcome.required_query_families == ["problem", "severity"]


def test_a_failed_family_keeps_the_zero_result_unrecorded():
    frontier = _frontier(
        policy=SaturationPolicy(
            required_families=(QueryFamily.problem, QueryFamily.severity)
        )
    )
    _run(
        frontier,
        _admit(frontier, _query("alpha problem", family=QueryFamily.problem)),
        status=DiscoveryStatus.empty,
        result_count=0,
        result_ids=(),
    )
    _run(
        frontier,
        _admit(frontier, _query("beta severity", family=QueryFamily.severity)),
        status=DiscoveryStatus.failure,
        result_count=0,
        result_ids=(),
    )
    outcome = frontier.zero_result_outcome()
    assert outcome.recorded is False
    assert outcome.completed_query_families == ["problem"]


def test_a_zero_result_is_not_recorded_after_an_earlier_failure():
    frontier = _frontier(
        policy=SaturationPolicy(required_families=(QueryFamily.problem,))
    )
    _run(
        frontier,
        _admit(frontier, _query("alpha problem")),
        status=DiscoveryStatus.failure,
        result_count=0,
        result_ids=(),
    )
    _run(
        frontier,
        _admit(frontier, _query("beta problem")),
        status=DiscoveryStatus.empty,
        result_count=0,
        result_ids=(),
    )
    # The frontier is not blocked any more and every required family ran,
    # but a search of the run failed, and the house gate refuses a zero
    # when any search call in the stage did not succeed.
    assert frontier.saturation().blocked is False
    assert frontier.zero_result_outcome().recorded is False


def test_the_total_failure_count_survives_a_checkpoint():
    frontier = _frontier(
        policy=SaturationPolicy(required_families=(QueryFamily.problem,))
    )
    _run(
        frontier,
        _admit(frontier, _query("alpha problem")),
        status=DiscoveryStatus.failure,
        result_count=0,
        result_ids=(),
    )
    restored = QueryFrontier.from_state(frontier.checkpoint_state())
    assert restored.saturation().signals["unsuccessful_searches"] == 1.0
    # The failure is part of the run, not of the session that wrote the
    # checkpoint, so the restored frontier refuses the zero as well.
    _run(
        restored,
        _admit(restored, _query("beta problem")),
        status=DiscoveryStatus.empty,
        result_count=0,
        result_ids=(),
    )
    assert restored.zero_result_outcome().recorded is False


def test_a_later_search_with_results_clears_the_zero_result_outcome():
    frontier = _frontier(
        policy=SaturationPolicy(required_families=(QueryFamily.problem,))
    )
    _run(
        frontier,
        _admit(frontier, _query("alpha problem")),
        status=DiscoveryStatus.empty,
        result_count=0,
        result_ids=(),
    )
    assert frontier.zero_result_outcome().recorded is True
    _run(
        frontier,
        _admit(frontier, _query("beta problem")),
        result_count=3,
        result_ids=("r1", "r2", "r3"),
    )
    assert frontier.zero_result_outcome().recorded is False


def test_the_novelty_arithmetic_holds():
    frontier = _frontier()
    record = _run(
        frontier,
        _admit(frontier, _query("alpha problem")),
        result_count=4,
        result_ids=("r1", "r2", "r3", "r4"),
    )
    assert record.novelty == 1.0
    assert record.duplicate_rate == 0.0
    record = _run(
        frontier,
        _admit(frontier, _query("beta problem")),
        result_count=4,
        result_ids=("r1", "r2", "r5", "r6"),
    )
    assert record.novelty == 0.5
    assert record.duplicate_rate == 0.5
    assert record.novelty + record.duplicate_rate == 1.0


def test_an_identity_repeated_inside_one_result_set_counts_as_a_repeat():
    frontier = _frontier()
    record = _run(
        frontier,
        _admit(frontier, _query("alpha problem")),
        result_count=2,
        result_ids=("r1", "r1"),
    )
    assert record.novelty == 0.5
    assert record.duplicate_rate == 0.5
    assert record.novelty * record.result_count == 1


def test_a_result_count_without_identities_is_refused():
    frontier = _frontier()
    query = _admit(frontier, _query("alpha problem"))
    with pytest.raises(ValueError) as excinfo:
        _run(frontier, query, result_count=3, result_ids=())
    assert "result identities" in str(excinfo.value)


def test_a_result_count_that_does_not_match_the_identities_is_refused():
    frontier = _frontier()
    query = _admit(frontier, _query("alpha problem"))
    with pytest.raises(ValueError) as excinfo:
        _run(frontier, query, result_count=1, result_ids=("r1", "r2"))
    assert "result identities" in str(excinfo.value)
    with pytest.raises(ValueError):
        _run(frontier, query, result_count=5, result_ids=("r1",))


def test_a_query_that_already_ran_is_refused():
    frontier = _frontier()
    query = _admit(frontier, _query("alpha problem"))
    _run(frontier, query)
    with pytest.raises(ValueError) as excinfo:
        _run(frontier, query)
    assert "already ran" in str(excinfo.value)


def test_an_execution_of_a_query_that_was_never_admitted_is_refused():
    frontier = _frontier(source_ids=["hn_algolia"])
    # The query is well formed, but the frontier never admitted it.
    query = _query("invoice reconciliation problem")
    with pytest.raises(ValueError) as excinfo:
        _run(frontier, query, result_ids=("r1",))
    assert query.query_id in str(excinfo.value)
    assert "never admitted" in str(excinfo.value)
    # Nothing moved: not the family coverage, not the searched sources, not
    # the budget, and no result identity was kept.
    assert frontier.family_counts == {}
    assert frontier.budget.queries == 0
    assert frontier.seen_results == frozenset()
    verdict = frontier.saturation()
    assert REASON_FAMILY_COVERAGE not in verdict.reasons
    assert REASON_SOURCE_COVERAGE not in verdict.reasons


@pytest.mark.parametrize(
    "status", [DiscoveryStatus.duplicate, DiscoveryStatus.over_budget]
)
def test_a_refusal_is_recordable_for_a_query_that_was_never_admitted(
    status: DiscoveryStatus,
):
    frontier = _frontier(source_ids=["hn_algolia"])
    query = _query("invoice reconciliation problem")
    record = frontier.record_execution(
        query,
        run_id=RUN,
        status=status,
        result_count=0,
        result_ids=(),
        timestamp=FROZEN,
    )
    # A refusal describes a query that was never admitted, so it is the one
    # execution that does not need an admission.
    assert record.status is status
    assert record.to_tool_call_provenance() is None
    assert frontier.family_counts == {}
    assert frontier.budget.queries == 0
    assert REASON_SOURCE_COVERAGE not in frontier.saturation().reasons


def test_a_plain_string_status_is_accepted():
    frontier = _frontier()
    query = _admit(frontier, _query("alpha problem"))
    record = frontier.record_execution(
        query,
        run_id=RUN,
        status="success",
        result_count=1,
        result_ids=("r1",),
        timestamp=FROZEN,
    )
    assert record.status is DiscoveryStatus.success
    assert frontier.executed == {query.query_id: "success"}


def test_the_priority_order_is_stable():
    frontier = _frontier()
    first = _admit(frontier, _query("alpha problem", family=QueryFamily.problem))
    second = _admit(frontier, _query("beta problem", family=QueryFamily.problem))
    _admit(frontier, _query("gamma problem", family=QueryFamily.workflow_pain))
    _admit(frontier, _query("delta problem", family=QueryFamily.severity))
    order = [query.query_id for query in frontier.next_queries(4)]
    assert order == [query.query_id for query in frontier.next_queries(4)]
    assert order[:2] == sorted([first.query_id, second.query_id])
    assert order[2].startswith("q_")
    families = [query.family for query in frontier.next_queries(4)]
    assert families[2:] == [QueryFamily.workflow_pain, QueryFamily.severity]


def test_the_priority_counts_the_source_weight():
    frontier = _frontier()
    # One query has already run against hn_algolia, so that source is worth
    # less than the one that has not been searched yet.
    _run(
        frontier,
        _admit(frontier, _query("delta problem", "hn_algolia", QueryFamily.severity)),
    )
    used = _admit(frontier, _query("alpha problem", "hn_algolia"))
    fresh = _admit(frontier, _query("gamma problem", "github_issues"))
    # The priority value itself, not the order that falls out of it: the
    # family weight is 1.0 and the novelty estimate is 1.0 for both, so
    # the source weight is the only thing that tells them apart.
    assert frontier.priority(used) == pytest.approx(0.5 * 1.0 + 0.3 * 0.5 + 0.2 * 1.0)
    assert frontier.priority(fresh) == pytest.approx(0.5 * 1.0 + 0.3 * 1.0 + 0.2 * 1.0)
    order = [query.query_id for query in frontier.next_queries(2)]
    assert order == [fresh.query_id, used.query_id]


def test_the_priority_counts_the_novelty_estimate():
    frontier = _frontier()
    # The problem family has run once and came back empty, so it is
    # expected to bring nothing new; the workflow family never ran.
    _run(
        frontier,
        _admit(frontier, _query("delta problem")),
        status=DiscoveryStatus.empty,
        result_count=0,
        result_ids=(),
    )
    problem = _admit(frontier, _query("alpha problem"))
    workflow = _admit(
        frontier, _query("gamma problem", family=QueryFamily.workflow_pain)
    )
    order = [query.query_id for query in frontier.next_queries(2)]
    assert order == [workflow.query_id, problem.query_id]


def test_the_next_queries_limit_is_honoured():
    frontier = _frontier()
    _admit(frontier, _query("alpha problem"))
    _admit(frontier, _query("beta problem"))
    assert len(frontier.next_queries(1)) == 1
    assert frontier.next_queries(0) == []


def test_expand_generates_the_code_owned_families():
    frontier = _frontier(
        policy=SaturationPolicy(
            required_families=(QueryFamily.problem, QueryFamily.severity)
        )
    )
    queries = frontier.expand(2)
    assert [query.family for query in queries] == [
        QueryFamily.problem,
        QueryFamily.severity,
    ]
    for query in queries:
        assert "invoice reconciliation" in query.text
        assert query.template_id == f"{query.family.value}-0"
        assert query.assignment_reason == f"template:{query.template_id}"
    assert len({query.text for query in queries}) == 2


def test_expand_gives_every_required_family_its_queries():
    frontier = _frontier(
        policy=SaturationPolicy(
            required_families=(QueryFamily.problem, QueryFamily.severity)
        )
    )
    queries = frontier.expand(4)
    assert [query.family for query in queries] == [
        QueryFamily.problem,
        QueryFamily.severity,
        QueryFamily.problem,
        QueryFamily.severity,
    ]
    assert len({query.text for query in queries}) == 4


def test_expand_stops_when_every_required_family_is_covered():
    frontier = _frontier(
        policy=SaturationPolicy(
            required_families=(QueryFamily.problem, QueryFamily.severity),
            queries_per_family=2,
        )
    )
    assert len(frontier.expand(4)) == 4
    assert frontier.expand(2) == []


def test_the_expansion_cursor_survives_a_checkpoint():
    frontier = _frontier(
        policy=SaturationPolicy(required_families=(QueryFamily.problem,))
    )
    first = frontier.expand(1)
    assert frontier.refusals == 0
    restored = QueryFrontier.from_state(frontier.checkpoint_state())
    second = restored.expand(1)
    # The restored frontier expands without refusing anything: the cursor
    # carried over, so the next variant is a new one and not a duplicate.
    assert restored.refusals == 0
    assert second[0].family is first[0].family
    assert second[0].text != first[0].text


def test_a_raw_objective_cannot_name_a_derived_family():
    frontier = _frontier(
        policy=SaturationPolicy(required_families=(QueryFamily.competitor,))
    )
    query, admitted, reason = frontier.propose(
        "alternatives to invoice reconciliation", "hn_algolia"
    )
    # The marker table still reads the text, but the family needs a
    # normalized problem, and there is none.
    assert query.family is QueryFamily.competitor
    assert admitted is False
    assert "normalized problem" in reason
    assert frontier.family_counts == {}


def test_an_execution_of_a_derived_family_from_a_raw_objective_is_refused():
    frontier = _frontier(
        policy=SaturationPolicy(required_families=(QueryFamily.competitor,))
    )
    query, admitted, _reason = frontier.propose(
        "alternatives to invoice reconciliation", "hn_algolia"
    )
    assert admitted is False
    with pytest.raises(ValueError) as excinfo:
        _run(frontier, query, result_ids=("r1",))
    assert query.query_id in str(excinfo.value)
    assert frontier.family_counts == {}
    assert frontier.budget.queries == 0


def test_the_expansion_never_renders_a_derived_family():
    frontier = _frontier(
        policy=SaturationPolicy(
            required_families=(QueryFamily.problem, QueryFamily.competitor)
        )
    )
    queries = frontier.expand(3)
    assert len(queries) == 3
    assert {query.family for query in queries} == {QueryFamily.problem}


def test_a_multi_line_seed_is_normalised_and_still_expands():
    frontier = QueryFrontier(
        seed="invoice\nreconciliation",
        source_ids=["hn_algolia"],
        policy=SaturationPolicy(required_families=(QueryFamily.problem,)),
        clock=lambda: FROZEN,
    )
    assert frontier.seed == "invoice reconciliation"
    queries = frontier.expand(2)
    assert len(queries) == 2
    assert frontier.refusals == 0
    assert all("invoice reconciliation" in query.text for query in queries)


def test_an_empty_seed_is_refused():
    with pytest.raises(ValueError) as excinfo:
        QueryFrontier(seed="   ", source_ids=["hn_algolia"], clock=lambda: FROZEN)
    assert "seed" in str(excinfo.value)


def test_a_policy_that_wants_more_variants_than_the_templates_is_refused():
    with pytest.raises(ValidationError) as excinfo:
        SaturationPolicy(
            required_families=(QueryFamily.problem,), queries_per_family=5
        )
    assert "templates" in str(excinfo.value)


def test_the_expansion_names_the_family_it_could_not_expand():
    frontier = _frontier(
        budget=DiscoveryBudget(max_queries_per_run=1),
        policy=SaturationPolicy(required_families=(QueryFamily.problem,)),
    )
    assert len(frontier.expand(1)) == 1
    # The run limit is spent, so the next variant cannot be admitted.
    assert frontier.expand(1) == []
    assert "problem" in frontier.expansion_stop_reason


def test_the_expansion_says_when_every_required_family_is_covered():
    frontier = _frontier(
        policy=SaturationPolicy(
            required_families=(QueryFamily.problem,), queries_per_family=1
        )
    )
    assert len(frontier.expand(2)) == 1
    # The reason names the state the expansion stopped in, so a stale
    # reason cannot pass this test.
    assert frontier.expansion_stop_reason == (
        "no required family can be expanded any further"
    )


def test_a_later_expansion_clears_the_stop_reason():
    frontier = _frontier(
        policy=SaturationPolicy(
            required_families=(QueryFamily.problem,), queries_per_family=3
        )
    )
    first = frontier.expand(3)
    assert len(first) == 3
    assert frontier.expand(1) == []
    assert frontier.expansion_stop_reason
    # A failure is not a search towards the coverage, so it frees a slot
    # and the expansion can go on.
    _run(
        frontier,
        first[0],
        status=DiscoveryStatus.failure,
        result_count=0,
        result_ids=(),
    )
    assert len(frontier.expand(1)) == 1
    # The reason describes the last expansion, and this one produced a
    # query.
    assert frontier.expansion_stop_reason == ""


def test_a_search_that_costs_more_than_the_limit_is_reported():
    frontier = _frontier(budget=DiscoveryBudget(max_cost_per_run=1.0))
    query = _admit(frontier, _query("alpha problem"))
    record = _run(frontier, query, cost=100.0)
    # The spend stays recorded: the call already happened.
    assert frontier.budget.cost == 100.0
    # The record comes back too: a search that was paid for must be
    # persistable, so the frontier keeps the overrun instead of raising it.
    assert record.status is DiscoveryStatus.success
    assert record.cost == 100.0


def test_the_overrun_is_reported_as_the_house_budget_exceeded():
    frontier = _frontier(budget=DiscoveryBudget(max_cost_per_run=1.0))
    query = _admit(frontier, _query("alpha problem"))
    _run(frontier, query, cost=100.0)
    overrun = frontier.budget_overrun
    assert isinstance(overrun, BudgetExceeded)
    assert overrun.scope == "per_cost"
    # A caller that only reads the verdict still sees the overrun.
    assert frontier.saturation().signals["budget_overrun"] == 1.0
    # And a run that never went over reports no overrun.
    clean = _frontier()
    _run(clean, _admit(clean, _query("alpha problem")))
    assert clean.budget_overrun is None
    assert clean.saturation().signals["budget_overrun"] == 0.0


def test_a_restored_frontier_still_reports_the_budget_overrun():
    frontier = _frontier(budget=DiscoveryBudget(max_cost_per_run=1.0))
    query = _admit(frontier, _query("alpha problem"))
    _run(frontier, query, cost=100.0)
    restored = QueryFrontier.from_state(frontier.checkpoint_state())
    # The exception of the session is not in the checkpoint, so it is gone
    # after the restore.
    assert restored.budget_overrun is None
    # The spend is in the checkpoint, and the run is still over its limit,
    # so the signal says so: it is derived from the budget, not from the
    # exception.
    assert restored.budget.cost == 100.0
    assert restored.saturation().signals["budget_overrun"] == 1.0


def test_a_budget_overrun_does_not_skip_a_counter():
    frontier = _frontier(budget=DiscoveryBudget(max_cost_per_run=1.0))
    query = _admit(frontier, _query("alpha problem"))
    _run(frontier, query, cost=100.0, result_ids=("r1",))
    # The bookkeeping comes before the budget, so the raise from the budget
    # cannot skip any of it.
    assert frontier.family_counts == {"problem": 1}
    assert frontier.seen_results == frozenset({"r1"})
    assert frontier.executed == {query.query_id: "success"}
    assert frontier.pending == []


def test_a_restored_blocked_frontier_records_no_zero_result():
    frontier = _frontier(
        policy=SaturationPolicy(required_families=(QueryFamily.problem,))
    )
    _run(
        frontier,
        _admit(frontier, _query("alpha problem")),
        status=DiscoveryStatus.empty,
        result_count=0,
        result_ids=(),
    )
    _run(
        frontier,
        _admit(frontier, _query("beta problem")),
        status=DiscoveryStatus.failure,
        result_count=0,
        result_ids=(),
    )
    # A checkpoint written before the frontier kept a count of the searches
    # that did not succeed carries the consecutive count and nothing else.
    state = frontier.checkpoint_state()
    counters = dict(state["counters"])
    counters.pop("unsuccessful_searches")
    state["counters"] = counters

    restored = QueryFrontier.from_state(state)

    assert restored.saturation().blocked is True
    # A blocked frontier records no zero result, whatever the total count is.
    assert restored.zero_result_outcome().recorded is False


def test_a_broken_search_that_overruns_the_budget_still_blocks_the_frontier():
    frontier = _frontier(budget=DiscoveryBudget(max_cost_per_run=1.0))
    query = _admit(frontier, _query("alpha problem"))
    _run(
        frontier,
        query,
        status=DiscoveryStatus.failure,
        result_count=0,
        result_ids=(),
        cost=100.0,
    )
    verdict = frontier.saturation()
    # A broken search tool is never saturation, however much it cost.
    assert verdict.blocked is True
    assert verdict.saturated is False
    assert verdict.signals["consecutive_failures"] == 1.0
    assert verdict.signals["budget_overrun"] == 1.0


def test_a_budget_that_fails_does_not_skip_a_counter():
    class _BrokenBudget(DiscoveryBudget):
        """A budget whose ledger fails for a reason the frontier does not absorb."""

        def record(self, source_id, family, cost=0.0):
            raise RuntimeError("the ledger is down")

    frontier = _frontier(budget=_BrokenBudget())
    query = _admit(frontier, _query("alpha problem"))
    with pytest.raises(RuntimeError):
        _run(frontier, query, result_ids=("r1",))
    # The bookkeeping comes before the budget, so a raise from the budget
    # does not skip a counter.
    assert frontier.family_counts == {"problem": 1}
    assert frontier.seen_results == frozenset({"r1"})

    failed = _frontier(budget=_BrokenBudget())
    query = _admit(failed, _query("alpha problem"))
    with pytest.raises(RuntimeError):
        _run(
            failed,
            query,
            status=DiscoveryStatus.failure,
            result_count=0,
            result_ids=(),
        )
    # The failure is counted before the budget as well, so a broken tool
    # still blocks the frontier.
    assert failed.saturation().blocked is True


def test_the_identities_of_an_overrunning_search_are_not_new_the_next_time():
    frontier = _frontier(budget=DiscoveryBudget(max_cost_per_run=1.0))
    first = _admit(frontier, _query("alpha problem"))
    second = _admit(frontier, _query("beta problem"))
    _run(frontier, first, cost=100.0, result_ids=("r1",))
    record = _run(frontier, second, cost=0.0, result_ids=("r1",))
    # The identity was already seen, so it is not new: the overrun did not
    # cost the frontier its memory of what it has seen.
    assert record.novelty == 0.0
    assert record.duplicate_rate == 1.0


def test_a_blocked_frontier_does_not_record_a_zero_result():
    frontier = _frontier(
        policy=SaturationPolicy(
            required_families=(QueryFamily.problem, QueryFamily.severity)
        )
    )
    _run(
        frontier,
        _admit(frontier, _query("alpha problem", family=QueryFamily.problem)),
        status=DiscoveryStatus.empty,
        result_count=0,
        result_ids=(),
    )
    _run(
        frontier,
        _admit(frontier, _query("beta severity", family=QueryFamily.severity)),
        status=DiscoveryStatus.empty,
        result_count=0,
        result_ids=(),
    )
    assert frontier.zero_result_outcome().recorded is True
    # A broken tool means the empty result is not a zero.
    _run(
        frontier,
        _admit(frontier, _query("gamma problem", family=QueryFamily.problem)),
        status=DiscoveryStatus.failure,
        result_count=0,
        result_ids=(),
    )
    assert frontier.saturation().blocked is True
    assert frontier.zero_result_outcome().recorded is False


def test_a_policy_with_no_required_family_records_no_zero():
    frontier = _frontier(policy=SaturationPolicy(required_families=()))
    _run(
        frontier,
        _admit(frontier, _query("alpha problem")),
        status=DiscoveryStatus.empty,
        result_count=0,
        result_ids=(),
    )
    outcome = frontier.zero_result_outcome()
    assert outcome.recorded is False
    assert outcome.required_query_families == []


def test_a_forged_checkpoint_does_not_smuggle_a_query_past_the_source_gate():
    frontier = _frontier(source_ids=["hn_algolia"])
    # The query is well formed, but the frontier was never given the source
    # it names, so it never passed the source gate.
    smuggled = _query("invoice reconciliation problem", "other_source")
    restored = QueryFrontier.from_state(_forged_state(frontier, smuggled))
    # The state is data, not proof: the query is not pending.
    assert smuggled.query_id not in [query.query_id for query in restored.pending]
    with pytest.raises(ValueError) as excinfo:
        _run(restored, smuggled, result_ids=("r1",))
    assert "not admissible" in str(excinfo.value)
    # Nothing moved: not the family coverage, not the searched sources, not
    # the budget, and no result identity was kept.
    assert restored.family_counts == {}
    assert restored.budget.queries == 0
    assert restored.seen_results == frozenset()
    verdict = restored.saturation()
    assert REASON_FAMILY_COVERAGE not in verdict.reasons
    assert REASON_SOURCE_COVERAGE not in verdict.reasons


def test_a_forged_checkpoint_does_not_smuggle_a_query_with_an_unknown_family():
    frontier = _frontier()
    # The query names a template it was not rendered from, so the code cannot
    # work out its family.
    forged = _forged("invoice reconciliation", "severity-0", QueryFamily.severity)
    restored = QueryFrontier.from_state(_forged_state(frontier, forged))
    assert forged.query_id not in [query.query_id for query in restored.pending]


def test_a_legitimate_pending_query_survives_the_restore():
    frontier = _frontier()
    pending = _admit(frontier, _query("alpha problem"))
    restored = QueryFrontier.from_state(frontier.checkpoint_state())
    assert [query.query_id for query in restored.pending] == [pending.query_id]
    # And it still runs: the restore did not take the admission away.
    record = _run(restored, pending, result_ids=("r1",))
    assert record.status is DiscoveryStatus.success
    assert restored.family_counts == {"problem": 1}


def test_a_frontier_that_admits_runs_and_admits_again_never_goes_over_the_budget():
    frontier = _frontier(budget=DiscoveryBudget(max_queries_per_run=2))
    _run(frontier, _admit(frontier, _query("alpha problem")))
    _run(frontier, _admit(frontier, _query("beta problem")))
    assert frontier.budget.queries == 2
    # The run limit is spent, so the next query is refused at admission.
    admitted, reason = frontier.admit(_query("gamma problem"))
    assert admitted is False
    assert "per_run" in reason


def test_a_checkpoint_that_holds_more_queries_than_the_budget_drops_the_rest():
    frontier = _frontier(budget=DiscoveryBudget(max_queries_per_run=1))
    admitted = _admit(frontier, _query("alpha problem"))
    # The state is data, so its pending list can name more queries than the
    # budget admits. The extra one passes the source gate and the family
    # re-derivation, so only the budget can turn it away.
    extra = _query("beta problem")
    restored = QueryFrontier.from_state(
        _state_with_pending(frontier, [admitted, extra])
    )
    assert [query.query_id for query in restored.pending] == [admitted.query_id]
    # And the query the budget turned away is not runnable either.
    with pytest.raises(ValueError) as excinfo:
        _run(restored, extra, result_ids=("r1",))
    assert "never admitted" in str(excinfo.value)


def test_a_legitimate_checkpoint_restores_exactly_its_pending_queries():
    frontier = _frontier(
        budget=DiscoveryBudget(
            max_queries_per_run=4,
            max_queries_per_source=2,
            max_queries_per_family=2,
            # Two searches of 0.1 cross this, so the run is over its spend
            # by the time the checkpoint is written.
            max_cost_per_run=0.15,
        )
    )
    first = _admit(frontier, _query("alpha problem"))
    second = _admit(frontier, _query("beta severity", family=QueryFamily.severity))
    third = _admit(frontier, _query("gamma problem", "github_issues"))
    fourth = _admit(frontier, _query("delta severity", "github_issues", QueryFamily.severity))
    _run(frontier, first)
    _run(frontier, second)
    # The run is over its spend, and the queries it committed to are still
    # the run's to run: a search that goes over the cost limit is reported
    # as an overrun, not as a query that may not start.
    assert frontier.budget.cost > frontier.budget.max_cost_per_run
    pending_before = {query.query_id for query in frontier.pending}
    assert pending_before == {third.query_id, fourth.query_id}
    # The run committed to four queries, which is what the run limit
    # allows, so the restore has to approve the same four.
    restored = QueryFrontier.from_state(frontier.checkpoint_state())
    # No query is dropped and none is added.
    assert {query.query_id for query in restored.pending} == pending_before
    assert len(restored.pending) == len(pending_before)


def test_the_same_text_against_another_source_is_a_different_search():
    frontier = _frontier()
    first = _admit(frontier, _query("invoice reconciliation problem"))
    other = _query("invoice reconciliation problem", "github_issues")
    # The query id is source|text, so the two are different searches.
    assert other.query_id != first.query_id
    admitted, reason = frontier.admit(other)
    # The duplicate check is scoped to the source: the same wording against
    # another source is another search, and it is admitted.
    assert admitted is True, reason
    assert frontier.refusals == 0


def test_the_same_text_against_the_same_source_is_a_duplicate():
    frontier = _frontier()
    _admit(frontier, _query("invoice reconciliation problem"))
    admitted, reason = frontier.admit(_query("invoice reconciliation problem"))
    assert admitted is False
    assert "duplicate" in reason
    assert frontier.refusals == 1


def test_the_expansion_spreads_over_the_admissible_sources():
    frontier = _frontier(
        policy=SaturationPolicy(
            required_families=(QueryFamily.problem,), queries_per_family=2
        )
    )
    queries = frontier.expand(2)
    # The source choice counts the admitted queries, not only the ones that
    # have run, so a batch that has not run yet still spreads.
    assert {query.source_id for query in queries} == {
        "hn_algolia",
        "github_issues",
    }
    for query in queries:
        _run(frontier, query, result_ids=(f"r-{query.query_id}",))
    # Both sources were searched, so the source coverage fires.
    verdict = frontier.saturation()
    assert REASON_SOURCE_COVERAGE in verdict.reasons


def test_a_forged_checkpoint_does_not_smuggle_a_near_duplicate_query():
    frontier = _frontier(policy=SaturationPolicy(near_duplicate_threshold=0.5))
    _admit(frontier, _query("invoice reconciliation for accountants"))
    # The wording is a near duplicate of the query the frontier holds
    # against the same source, and the admission would refuse it.
    smuggled = _query("invoice reconciliation for bookkeepers")
    restored = QueryFrontier.from_state(_forged_state(frontier, smuggled))
    # The restore runs the same duplicate check the admission runs, so the
    # smuggled query is not pending.
    assert smuggled.query_id not in [query.query_id for query in restored.pending]


def test_a_forged_checkpoint_does_not_smuggle_a_query_with_a_forged_family():
    frontier = _frontier()
    # The query names the problem template, so the code owns the problem
    # family, but it declares the severity family.
    liar = Query(
        query_id=query_id("invoice reconciliation problem", "hn_algolia"),
        text="invoice reconciliation problem",
        normalized=normalize_query("invoice reconciliation problem"),
        family=QueryFamily.severity,
        source_id="hn_algolia",
        template_id="problem-0",
        created_at=FROZEN,
        assignment_reason="template:problem-0",
    )
    restored = QueryFrontier.from_state(_forged_state(frontier, liar))
    # The family the code derives is not the family the state declares, so
    # the query is not pending.
    assert liar.query_id not in [query.query_id for query in restored.pending]


def test_budget_exhausted_when_no_pending_query_is_still_admissible():
    frontier = _frontier(budget=DiscoveryBudget(max_cost_per_run=1.0))
    first = _admit(frontier, _query("alpha problem"))
    pending = _admit(frontier, _query("beta problem"))
    # The search crosses the cost limit, so the run is over its spend.
    _run(frontier, first, cost=100.0, result_ids=("r1",))
    # The query is still pending, but the budget will not admit it any more.
    assert pending.query_id in [query.query_id for query in frontier.pending]
    verdict = frontier.saturation()
    assert REASON_BUDGET_EXHAUSTED in verdict.reasons
    assert verdict.saturated is True


def test_a_partial_search_keeps_the_zero_result_unrecorded():
    frontier = _frontier(
        policy=SaturationPolicy(required_families=(QueryFamily.problem,))
    )
    records = [
        _run(
            frontier,
            _admit(frontier, _query("alpha problem")),
            status=DiscoveryStatus.empty,
            result_count=0,
            result_ids=(),
        ),
        _run(
            frontier,
            _admit(frontier, _query("beta problem")),
            status=DiscoveryStatus.partial,
            result_count=0,
            result_ids=(),
        ),
    ]
    # A partial search is a completed search, so the family ran.
    assert frontier.family_counts == {"problem": 2}
    # It is not a successful one, so the house gate refuses a zero that
    # describes it, and the frontier does not record one either.
    assert frontier.saturation().blocked is False
    assert frontier.zero_result_outcome().recorded is False
    execution = StageExecution(
        stage="discovery",
        status=StageStatus.running,
        tool_calls=[record.to_tool_call_provenance() for record in records],
        zero_result=frontier.zero_result_outcome(),
    )
    accepted, detail = explicit_zero_outcome(execution)
    assert accepted is False
    assert "no zero-result outcome was recorded" in detail
    # And a zero that claimed the families anyway would be refused too: a
    # partial call is not a successful one.
    claimed = ZeroResultOutcome(
        recorded=True,
        required_query_families=["problem"],
        completed_query_families=["problem"],
        note="claimed",
    )
    accepted, detail = explicit_zero_outcome(
        execution.model_copy(update={"zero_result": claimed})
    )
    assert accepted is False
    assert "did not succeed" in detail


def test_the_admitted_cost_and_the_execution_parameters_reach_the_record():
    frontier = _frontier(budget=DiscoveryBudget(max_cost_per_run=1.0))
    # The cost of an admission is checked against the budget.
    admitted, reason = frontier.admit(_query("alpha problem"), cost=0.5)
    assert admitted is True, reason
    refused, reason = frontier.admit(_query("beta problem"), cost=2.0)
    assert refused is False
    assert "per_cost" in reason
    query = _admit(frontier, _query("gamma problem"))
    record = frontier.record_execution(
        query,
        run_id=RUN,
        status=DiscoveryStatus.partial,
        result_count=0,
        result_ids=(),
        useful_count=0,
        cost=0.5,
        tool="search_alt",
        agent="discovery_alt",
        stage="discovery_alt",
        result_ref="raw/8f14e45f",
        error="the search was cut short",
        timestamp=FROZEN,
    )
    assert record.status is DiscoveryStatus.partial
    assert record.cost == 0.5
    assert record.tool == "search_alt"
    assert record.agent == "discovery_alt"
    assert record.stage == "discovery_alt"
    assert record.result_ref == "raw/8f14e45f"
    assert record.error == "the search was cut short"

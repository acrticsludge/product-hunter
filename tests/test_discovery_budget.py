from __future__ import annotations

import math

import pytest

from product_hunter.discovery.budget import DiscoveryBudget
from product_hunter.discovery.families import QueryFamily
from product_hunter.research.budget import (
    BudgetExceeded,
    BudgetSpec,
    BudgetTracker,
    NonFiniteAmount,
)


def _budget(**overrides) -> DiscoveryBudget:
    return DiscoveryBudget(**overrides)


def test_a_query_within_the_limits_is_allowed():
    budget = _budget(
        max_queries_per_run=2,
        max_queries_per_source=2,
        max_queries_per_family=2,
    )
    decision = budget.check("hn_algolia", QueryFamily.problem, 0.1)
    assert decision.allowed is True
    assert decision.scope == "none"


def test_the_per_run_limit_refuses():
    budget = _budget(max_queries_per_run=1)
    budget.record("hn_algolia", QueryFamily.problem, 0.1)
    decision = budget.check("hn_algolia", QueryFamily.problem, 0.1)
    assert decision.allowed is False
    assert decision.scope == "per_run"


def test_the_per_source_limit_refuses():
    budget = _budget(max_queries_per_source=1)
    budget.record("hn_algolia", QueryFamily.problem, 0.1)
    decision = budget.check("hn_algolia", QueryFamily.severity, 0.1)
    assert decision.allowed is False
    assert decision.scope == "per_source"
    assert budget.check("github_issues", QueryFamily.severity, 0.1).allowed is True


def test_the_per_family_limit_refuses():
    budget = _budget(max_queries_per_family=1)
    budget.record("hn_algolia", QueryFamily.problem, 0.1)
    decision = budget.check("github_issues", QueryFamily.problem, 0.1)
    assert decision.allowed is False
    assert decision.scope == "per_family"


def test_the_cost_limit_refuses():
    budget = _budget(max_cost_per_run=1.0)
    decision = budget.check("hn_algolia", QueryFamily.problem, 1.5)
    assert decision.allowed is False
    assert decision.scope == "per_cost"


def test_the_refusal_names_the_scope_and_the_numbers():
    budget = _budget(max_queries_per_run=1)
    budget.record("hn_algolia", QueryFamily.problem, 0.1)
    decision = budget.check("hn_algolia", QueryFamily.problem, 0.1)
    assert "per_run" in decision.reason
    assert decision.limit == 1.0
    assert decision.spent == 1.0
    assert decision.requested == 1.0


def test_a_supplied_contract_tracker_still_binds():
    tracker = BudgetTracker(
        BudgetSpec(version="test-v1", per_run={"search_queries": 1.0})
    )
    budget = _budget(tracker=tracker)
    assert budget.check("hn_algolia", QueryFamily.problem, 0.0).allowed is True
    budget.record("hn_algolia", QueryFamily.problem, 0.0)
    decision = budget.check("hn_algolia", QueryFamily.problem, 0.0)
    assert decision.allowed is False
    assert decision.dimension == "search_queries"


def test_recording_counts_the_queries_per_source_and_per_family():
    budget = _budget()
    budget.record("hn_algolia", QueryFamily.problem, 0.25)
    budget.record("hn_algolia", QueryFamily.problem, 0.25)
    budget.record("github_issues", QueryFamily.severity, 0.5)
    assert budget.queries == 3
    assert budget.cost == 1.0
    assert budget.queries_by_source == {"hn_algolia": 2, "github_issues": 1}
    assert budget.queries_by_family == {"problem": 2, "severity": 1}


def test_the_pending_queries_bind_the_limits():
    budget = _budget(
        max_queries_per_run=2,
        max_queries_per_source=2,
        max_queries_per_family=2,
    )
    assert budget.check("hn_algolia", QueryFamily.problem, 0.0).allowed is True
    assert budget.check("hn_algolia", QueryFamily.problem, 0.0, pending_total=1).allowed is True
    assert budget.check("hn_algolia", QueryFamily.problem, 0.0, pending_total=2).allowed is False
    assert budget.check("hn_algolia", QueryFamily.problem, 0.0, pending_source=2).allowed is False
    assert budget.check("hn_algolia", QueryFamily.problem, 0.0, pending_family=2).allowed is False
    # The counters stay the executed ones: nothing was counted twice.
    assert budget.queries == 0


def test_a_non_finite_cost_is_refused():
    budget = _budget(max_cost_per_run=1.0)
    decision = budget.check("hn_algolia", QueryFamily.problem, float("nan"))
    assert decision.allowed is False
    assert "not a finite number" in decision.reason
    with pytest.raises(NonFiniteAmount):
        budget.record("hn_algolia", QueryFamily.problem, float("inf"))
    assert math.isfinite(budget.cost)
    assert budget.cost == 0.0


def test_a_negative_cost_is_refused():
    budget = _budget(max_cost_per_run=1.0)
    decision = budget.check("hn_algolia", QueryFamily.problem, -1.0)
    assert decision.allowed is False
    assert "negative" in decision.reason
    with pytest.raises(ValueError):
        budget.record("hn_algolia", QueryFamily.problem, -1.0)
    assert budget.cost == 0.0


def test_recording_over_the_cost_limit_records_the_spend_and_raises():
    budget = _budget(max_cost_per_run=1.0)
    with pytest.raises(BudgetExceeded) as excinfo:
        budget.record("hn_algolia", QueryFamily.problem, 5.0)
    assert excinfo.value.dimension == "discovery_cost"
    assert excinfo.value.scope == "per_cost"
    # The spend stays recorded: the call already happened, and the limit is
    # measured from the recorded total.
    assert budget.cost == 5.0
    assert budget.queries == 1


def test_recording_within_the_cost_limit_raises_nothing():
    budget = _budget(max_cost_per_run=1.0)
    budget.record("hn_algolia", QueryFamily.problem, 0.5)
    budget.record("hn_algolia", QueryFamily.problem, 0.5)
    assert budget.cost == 1.0


@pytest.mark.parametrize(
    ("limits", "source_id", "family", "scope"),
    [
        ({"max_queries_per_run": 1}, "hn_algolia", QueryFamily.problem, "per_run"),
        (
            {"max_queries_per_source": 1},
            "hn_algolia",
            QueryFamily.problem,
            "per_source",
        ),
        (
            {"max_queries_per_family": 1},
            "hn_algolia",
            QueryFamily.problem,
            "per_family",
        ),
    ],
)
def test_the_budget_raises_when_the_query_count_goes_over_its_limit(
    limits, source_id, family, scope
):
    budget = _budget(**limits)
    budget.record(source_id, family, 0.1)
    with pytest.raises(BudgetExceeded) as excinfo:
        budget.record(source_id, family, 0.1)
    assert excinfo.value.dimension == "discovery_queries"
    assert excinfo.value.scope == scope
    # The count stays recorded: the call already happened, and the limit is
    # measured from the recorded total.
    assert budget.queries == 2


def test_the_cost_overrun_is_its_own_case_and_not_the_count_overrun():
    budget = _budget(max_queries_per_run=1, max_cost_per_run=1.0)
    with pytest.raises(BudgetExceeded) as excinfo:
        budget.record("hn_algolia", QueryFamily.problem, 5.0)
    # The query count is within its limit, so the spend is what is over.
    assert excinfo.value.dimension == "discovery_cost"
    assert excinfo.value.scope == "per_cost"
    assert budget.queries == 1


def test_exceeded_names_the_limits_the_run_is_over():
    budget = _budget(
        max_queries_per_run=1,
        max_queries_per_source=1,
        max_cost_per_run=1.0,
    )
    # A run inside its limits is over nothing.
    assert budget.exceeded() == []
    with pytest.raises(BudgetExceeded):
        budget.record("hn_algolia", QueryFamily.problem, 5.0)
    # The spend is over its limit, so the run is over that limit and not
    # over the query counts yet.
    assert budget.exceeded() == ["per_cost"]
    # The counters are in the checkpoint, so the answer survives a restore.
    assert DiscoveryBudget.from_state(budget.state()).exceeded() == ["per_cost"]


def test_the_tracker_can_be_handed_back_at_restore():
    tracker = BudgetTracker(
        BudgetSpec(version="test-v1", per_run={"search_queries": 1.0})
    )
    budget = _budget(tracker=tracker)
    budget.record("hn_algolia", QueryFamily.problem, 0.0)
    restored = DiscoveryBudget.from_state(budget.state(), tracker=tracker)
    assert restored.tracker is tracker
    assert restored.queries == 1
    assert restored.check("hn_algolia", QueryFamily.problem, 0.0).allowed is False

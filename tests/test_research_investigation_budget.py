from __future__ import annotations

import pytest

from product_hunter.research.budget import (
    BudgetDimension,
    BudgetExceeded,
    BudgetSpec,
    BudgetTracker,
)
from product_hunter.research.investigation.budget import InvestigationBudget

# The areas the investigation searches. A research area is a string enum, so
# the area and its name are the same value.
AREA = "competitor"
OTHER_AREA = "counter_evidence"


def _tracker(**overrides) -> BudgetTracker:
    data = {"version": "test-v1"}
    data.update(overrides)
    return BudgetTracker(BudgetSpec(**data))


def _budget(per_area: dict[str, dict[str, float]] | None = None, **tracker_overrides):
    return InvestigationBudget(_tracker(**tracker_overrides), per_area)


def test_a_call_inside_the_per_area_cap_is_allowed():
    budget = _budget({AREA: {"search_queries": 4.0}}, per_run={"search_queries": 100.0})

    decision = budget.check(AREA, BudgetDimension.search_queries, 2.0)

    assert decision.allowed is True
    assert decision.dimension == "search_queries"
    assert decision.limit == 4.0
    assert decision.spent == 0.0
    assert decision.scope == "per_area"


def test_a_call_over_the_per_area_cap_is_denied_before_it_starts():
    budget = _budget({AREA: {"search_queries": 4.0}}, per_run={"search_queries": 100.0})
    budget.record(AREA, "search_queries", 3.0)

    decision = budget.check(AREA, "search_queries", 2.0)

    assert decision.allowed is False
    assert decision.scope == "per_area"
    assert decision.limit == 4.0
    assert decision.spent == 3.0
    assert decision.requested == 2.0

    with pytest.raises(BudgetExceeded) as excinfo:
        budget.guard(AREA, "search_queries", 2.0)

    assert excinfo.value.scope == "per_area"
    assert excinfo.value.limit == 4.0


def test_the_stricter_of_the_per_run_and_the_per_area_cap_wins():
    budget = _budget({AREA: {"search_queries": 4.0}}, per_run={"search_queries": 10.0})
    budget.record(AREA, "search_queries", 3.0)

    decision = budget.check(AREA, "search_queries", 2.0)

    assert decision.allowed is False
    assert decision.scope == "per_area"
    assert decision.limit == 4.0


def test_the_stricter_per_run_limit_wins_when_it_is_the_smaller_one():
    budget = _budget({AREA: {"search_queries": 10.0}}, per_run={"search_queries": 4.0})
    budget.record(AREA, "search_queries", 3.0)

    decision = budget.check(AREA, "search_queries", 2.0)

    assert decision.allowed is False
    assert decision.scope == "per_run"
    assert decision.limit == 4.0


def test_a_per_area_cap_does_not_leak_into_another_area():
    budget = _budget({AREA: {"search_queries": 4.0}}, per_run={"search_queries": 100.0})
    budget.record(AREA, "search_queries", 3.0)

    decision = budget.check(OTHER_AREA, "search_queries", 2.0)

    assert decision.allowed is True
    assert budget.spent(OTHER_AREA, "search_queries") == 0.0
    assert budget.spent(AREA, "search_queries") == 3.0


def test_record_raises_past_the_cap():
    budget = _budget({AREA: {"search_queries": 4.0}}, per_run={"search_queries": 100.0})
    budget.record(AREA, "search_queries", 3.0)

    with pytest.raises(BudgetExceeded) as excinfo:
        budget.record(AREA, "search_queries", 2.0)

    assert excinfo.value.scope == "per_area"
    assert excinfo.value.limit == 4.0


def test_a_non_positive_per_area_cap_is_refused():
    with pytest.raises(ValueError) as excinfo:
        _budget({AREA: {"search_queries": 0.0}})
    assert "must be positive" in str(excinfo.value)

    with pytest.raises(ValueError) as excinfo:
        _budget({AREA: {"search_queries": -1.0}})
    assert "must be positive" in str(excinfo.value)


def test_a_non_finite_per_area_cap_is_refused():
    with pytest.raises(ValueError) as excinfo:
        _budget({AREA: {"search_queries": float("nan")}})
    assert "finite" in str(excinfo.value)


def test_the_exceeded_names_the_dimension_over_its_cap():
    budget = _budget({AREA: {"search_queries": 4.0}}, per_run={"search_queries": 100.0})

    with pytest.raises(BudgetExceeded):
        budget.record(AREA, "search_queries", 5.0)

    # The spend stays recorded: the call already happened.
    assert budget.exceeded() == ["search_queries"]


def test_the_tracker_enforces_a_per_stage_limit():
    base = BudgetSpec(
        version="test-v1",
        per_run={"search_queries": 100.0},
        per_stage={AREA: {"search_queries": 4.0}},
    )
    tracker = BudgetTracker(base)

    tracker.record("search_queries", 3.0, stage=AREA)
    decision = tracker.check("search_queries", 2.0, stage=AREA)

    assert decision.allowed is False
    assert decision.scope == "per_stage"
    assert decision.limit == 4.0

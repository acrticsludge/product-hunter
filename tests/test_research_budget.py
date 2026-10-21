from __future__ import annotations

import pytest

from product_hunter.research.budget import (
    BudgetDimension,
    BudgetExceeded,
    BudgetSpec,
    BudgetTracker,
    NonFiniteAmount,
)


def _spec(**overrides) -> BudgetSpec:
    data = {"version": "test-v1"}
    data.update(overrides)
    return BudgetSpec(**data)


def _tracker(**overrides) -> BudgetTracker:
    return BudgetTracker(_spec(**overrides))


def test_call_over_the_per_run_limit_is_denied():
    tracker = _tracker(per_run={"search_queries": 10.0})
    tracker.record(BudgetDimension.search_queries, 8.0, stage="discovery")

    decision = tracker.check("search_queries", 5.0, stage="discovery")

    assert decision.allowed is False
    assert decision.dimension == "search_queries"
    assert decision.limit == 10.0
    assert decision.spent == 8.0
    assert decision.requested == 5.0
    assert decision.scope == "per_run"


def test_call_inside_the_limit_is_allowed():
    tracker = _tracker(per_run={"search_queries": 10.0})
    tracker.record("search_queries", 3.0, stage="discovery")

    decision = tracker.check("search_queries", 5.0, stage="discovery")

    assert decision.allowed is True
    assert decision.limit == 10.0
    assert decision.spent == 3.0


def test_a_dimension_without_a_limit_is_allowed():
    tracker = _tracker(per_run={"search_queries": 10.0})

    decision = tracker.check("fetch_requests", 500.0, stage="discovery")

    assert decision.allowed is True
    assert decision.limit is None


def test_the_stricter_of_per_run_and_per_stage_wins():
    tracker = _tracker(
        per_run={"search_queries": 10.0},
        per_stage={"discovery": {"search_queries": 4.0}},
    )
    tracker.record("search_queries", 3.0, stage="discovery")

    decision = tracker.check("search_queries", 2.0, stage="discovery")

    assert decision.allowed is False
    assert decision.scope == "per_stage"
    assert decision.limit == 4.0
    assert decision.spent == 3.0


def test_the_per_stage_limit_does_not_leak_into_another_stage():
    tracker = _tracker(
        per_run={"search_queries": 10.0},
        per_stage={"discovery": {"search_queries": 4.0}},
    )
    tracker.record("search_queries", 3.0, stage="discovery")

    decision = tracker.check("search_queries", 2.0, stage="validation")

    assert decision.allowed is True
    assert decision.scope == "per_run"


def test_a_per_source_limit_blocks_that_source():
    tracker = _tracker(per_source={"hn_algolia": 5.0})
    tracker.record("source_requests_per_source", 4.0, source_id="hn_algolia")

    blocked = tracker.check("source_requests_per_source", 2.0, source_id="hn_algolia")
    other = tracker.check("source_requests_per_source", 2.0, source_id="github_issues")

    assert blocked.allowed is False
    assert blocked.scope == "per_source"
    assert blocked.limit == 5.0
    assert other.allowed is True


def test_record_raises_past_the_limit():
    tracker = _tracker(per_run={"search_queries": 10.0})
    tracker.record("search_queries", 8.0, stage="discovery")

    with pytest.raises(BudgetExceeded) as excinfo:
        tracker.record("search_queries", 5.0, stage="discovery")

    assert excinfo.value.dimension == "search_queries"
    assert excinfo.value.limit == 10.0
    assert excinfo.value.scope == "per_run"


def test_guard_raises_before_the_call_starts():
    tracker = _tracker(per_run={"search_queries": 10.0})
    tracker.record("search_queries", 10.0, stage="discovery")

    with pytest.raises(BudgetExceeded) as excinfo:
        tracker.guard("search_queries", 1.0, stage="discovery")

    assert excinfo.value.dimension == "search_queries"
    assert excinfo.value.requested == 1.0


def test_exceeded_names_the_dimension():
    tracker = _tracker(per_run={"search_queries": 10.0, "usd_cost": 5.0})
    with pytest.raises(BudgetExceeded):
        tracker.record("search_queries", 11.0, stage="discovery")
    tracker.record("usd_cost", 1.0, stage="discovery")

    assert tracker.exceeded() == ["search_queries"]


def test_exceeded_is_empty_while_the_run_is_inside_its_budget():
    tracker = _tracker(per_run={"search_queries": 10.0})
    tracker.record("search_queries", 10.0, stage="discovery")

    assert tracker.exceeded() == []


def test_spent_is_tracked_per_scope():
    tracker = _tracker(per_run={"search_queries": 100.0})
    tracker.record("search_queries", 3.0, stage="discovery")
    tracker.record("search_queries", 4.0, stage="validation")

    assert tracker.spent("search_queries") == 7.0
    assert tracker.spent("search_queries", stage="discovery") == 3.0
    assert tracker.spent("search_queries", stage="validation") == 4.0


def test_a_non_finite_budget_amount_is_refused():
    tracker = _tracker(per_run={"usd_cost": 5.0})

    decision = tracker.check("usd_cost", float("nan"))

    # A NaN amount makes every comparison against it false, so the dimension
    # would switch itself off. The spend is refused and not recorded.
    with pytest.raises(ValueError):
        tracker.record("usd_cost", float("nan"), stage="discovery")
    with pytest.raises(ValueError):
        tracker.guard("usd_cost", float("nan"), stage="discovery")

    assert decision.allowed is False
    assert decision.dimension == "usd_cost"
    # The reason is fixed and does not quote the value.
    assert decision.reason == tracker.check("usd_cost", float("inf")).reason
    assert "nan" not in decision.reason.lower()
    assert tracker.check("usd_cost", float("inf")).allowed is False
    # The ledger never saw the refused spend.
    assert tracker.spent("usd_cost") == 0.0
    assert tracker.spent("usd_cost", stage="discovery") == 0.0
    assert tracker.exceeded() == []


def test_a_non_finite_amount_raises_the_named_refusal():
    tracker = _tracker(per_run={"usd_cost": 5.0})

    with pytest.raises(NonFiniteAmount):
        tracker.record("usd_cost", float("nan"), stage="discovery")
    with pytest.raises(NonFiniteAmount):
        tracker.guard("usd_cost", float("inf"), stage="discovery")

    # The refusal is a ValueError, so a caller that already catches one
    # keeps catching it, and a caller that wants to tell a refused amount
    # from an overrun has a class of its own to catch.
    assert issubclass(NonFiniteAmount, ValueError)
    with pytest.raises(ValueError):
        tracker.guard("usd_cost", float("nan"), stage="discovery")

    # The ledger never saw the refused spend.
    assert tracker.spent("usd_cost") == 0.0
    assert tracker.spent("usd_cost", stage="discovery") == 0.0

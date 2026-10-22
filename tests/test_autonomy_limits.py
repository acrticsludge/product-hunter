"""The bounds the operation keeps itself inside.

The pacer, the concurrency limiter and the budget ledger each hold state that
has to survive a restart, so every test here reads the state back through a
fresh object over the same database.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from product_hunter.autonomy.budget import OperationBudget
from product_hunter.autonomy.limits import ConcurrencyLimiter, SourcePacer
from product_hunter.db import Store
from product_hunter.db.jobs import claim_job, enqueue_job
from product_hunter.research.budget import (
    BudgetDimension,
    BudgetExceeded,
    BudgetSpec,
)

NOW = datetime(2026, 10, 21, 6, 0, tzinfo=timezone.utc)


def _store() -> Store:
    return Store.open(":memory:")


def test_the_pacer_refuses_a_source_before_its_interval_and_allows_it_after() -> None:
    store = _store()
    try:
        pacer = SourcePacer(store.conn, {"hn_algolia": 30})
        assert pacer.seconds_until_allowed("hn_algolia", now=NOW) == 0
        pacer.mark_used("hn_algolia", now=NOW)
        assert pacer.seconds_until_allowed("hn_algolia", now=NOW) == 30
        assert (
            pacer.seconds_until_allowed(
                "hn_algolia", now=NOW + timedelta(seconds=29)
            )
            == 1
        )
        assert (
            pacer.seconds_until_allowed(
                "hn_algolia", now=NOW + timedelta(seconds=30)
            )
            == 0
        )
    finally:
        store.close()


def test_the_pacer_state_survives_a_new_pacer_over_the_same_database() -> None:
    store = _store()
    try:
        SourcePacer(store.conn, {"hn_algolia": 30}).mark_used(
            "hn_algolia", now=NOW
        )
        later = SourcePacer(store.conn, {"hn_algolia": 30})
        assert later.seconds_until_allowed("hn_algolia", now=NOW) == 30
        state = later.state("hn_algolia")
        assert state is not None
        assert state.use_count == 1
        assert state.last_used_at == NOW.isoformat()
        assert later.state("other") is None
    finally:
        store.close()


def test_the_concurrency_limiter_counts_the_leased_jobs_of_every_worker() -> None:
    store = _store()
    try:
        enqueue_job(
            store.conn,
            stage="COLLECT",
            idempotency_key="one",
            scheduled_for=NOW.isoformat(),
            now=NOW,
        )
        enqueue_job(
            store.conn,
            stage="COLLECT",
            idempotency_key="two",
            scheduled_for=NOW.isoformat(),
            now=NOW,
        )
        assert claim_job(store.conn, "worker-a", now=NOW) is not None
        assert claim_job(store.conn, "worker-b", now=NOW) is not None

        limiter = ConcurrencyLimiter(store.conn, 2)
        assert limiter.in_flight(now=NOW) == 2
        assert limiter.at_capacity(now=NOW) is True
        # The count is over the whole store, so a third worker does not make
        # room for a fourth job.
        assert ConcurrencyLimiter(store.conn, 3).at_capacity(now=NOW) is False
    finally:
        store.close()


def test_the_budget_ledger_refuses_the_call_that_would_break_a_limit() -> None:
    store = _store()
    try:
        spec = BudgetSpec(version="1", per_run={"fetch_requests": 2})
        budget = OperationBudget(store.conn, "op-1", spec)
        budget.record(BudgetDimension.fetch_requests, 1)
        assert budget.spent(BudgetDimension.fetch_requests) == 1
        assert budget.exhausted() == []

        decision = budget.check(BudgetDimension.fetch_requests, 2)
        assert decision.allowed is False
        assert decision.dimension == "fetch_requests"

        with pytest.raises(BudgetExceeded):
            budget.record(BudgetDimension.fetch_requests, 2)
        # The spend that did happen is written down before the refusal.
        assert budget.spent(BudgetDimension.fetch_requests) == 3
        assert budget.exhausted() == ["fetch_requests"]
    finally:
        store.close()


def test_the_budget_ledger_charges_the_wall_time_and_refuses_the_charge_that_breaks_it() -> None:
    store = _store()
    try:
        spec = BudgetSpec(version="1", per_run={"wall_time_seconds": 60})
        budget = OperationBudget(store.conn, "op-1", spec)
        budget.record(BudgetDimension.wall_time_seconds, 30)
        assert budget.spent(BudgetDimension.wall_time_seconds) == 30
        assert budget.exhausted() == []

        decision = budget.check(BudgetDimension.wall_time_seconds, 40)
        assert decision.allowed is False
        assert decision.dimension == "wall_time_seconds"

        with pytest.raises(BudgetExceeded):
            budget.record(BudgetDimension.wall_time_seconds, 40)
        # The spend that did happen is written down before the refusal.
        assert budget.spent(BudgetDimension.wall_time_seconds) == 70
        assert budget.exhausted() == ["wall_time_seconds"]

        # The charge survives a restart, so the operation does not get a
        # fresh allowance.
        later = OperationBudget(store.conn, "op-1", spec)
        assert later.spent(BudgetDimension.wall_time_seconds) == 70
        assert later.exhausted() == ["wall_time_seconds"]
    finally:
        store.close()


def test_the_scope_rows_of_one_spend_are_written_together() -> None:
    store = _store()
    try:
        spec = BudgetSpec(version="1", per_run={"fetch_requests": 10})
        budget = OperationBudget(store.conn, "op-1", spec)
        # The source row refuses the write, so the rows before it must not
        # land either.
        store.conn.execute(
            "CREATE TRIGGER refuse_source BEFORE INSERT ON operation_budget "
            "WHEN NEW.scope LIKE 'source:%' "
            "BEGIN SELECT RAISE(ABORT, 'the source row is refused'); END"
        )
        with pytest.raises(sqlite3.IntegrityError):
            budget.record(
                BudgetDimension.fetch_requests,
                1,
                stage="COLLECT",
                source_id="hn_algolia",
            )
        rows = store.conn.execute("SELECT scope FROM operation_budget").fetchall()
        assert rows == []
    finally:
        store.close()


def test_an_over_limit_spend_is_restored_from_the_ledger() -> None:
    store = _store()
    try:
        spec = BudgetSpec(version="1", per_run={"fetch_requests": 5})
        first = OperationBudget(store.conn, "op-1", spec)
        with pytest.raises(BudgetExceeded):
            first.record(BudgetDimension.fetch_requests, 8)
        # The spend is over the limit, and it is in the ledger.
        assert first.spent(BudgetDimension.fetch_requests) == 8
        assert first.exhausted() == ["fetch_requests"]

        # A new tracker over the same database picks the spend up, so a
        # crash cannot buy the operation a fresh allowance.
        later = OperationBudget(store.conn, "op-1", spec)
        assert later.spent(BudgetDimension.fetch_requests) == 8
        assert later.exhausted() == ["fetch_requests"]
    finally:
        store.close()


def test_the_budget_ledger_survives_a_new_tracker_over_the_same_database() -> None:
    store = _store()
    try:
        spec = BudgetSpec(
            version="1",
            per_run={"fetch_requests": 10},
            per_stage={"COLLECT": {"fetch_requests": 8}},
            per_source={"hn_algolia": 4},
        )
        first = OperationBudget(store.conn, "op-1", spec)
        first.record(BudgetDimension.fetch_requests, 2, stage="COLLECT")
        first.record(
            BudgetDimension.fetch_requests,
            3,
            stage="COLLECT",
            source_id="hn_algolia",
        )

        later = OperationBudget(store.conn, "op-1", spec)
        assert later.spent(BudgetDimension.fetch_requests) == 5
        assert later.spent(BudgetDimension.fetch_requests, stage="COLLECT") == 5
        assert (
            later.spent(
                BudgetDimension.fetch_requests, source_id="hn_algolia"
            )
            == 3
        )
        assert later.exhausted() == []

        # The ledger of one operation is not the ledger of another.
        other = OperationBudget(store.conn, "op-2", spec)
        assert other.spent(BudgetDimension.fetch_requests) == 0
    finally:
        store.close()

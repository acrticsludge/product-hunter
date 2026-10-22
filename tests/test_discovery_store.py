from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from product_hunter.db import Store
from product_hunter.discovery.budget import DiscoveryBudget
from product_hunter.discovery.families import QueryFamily
from product_hunter.discovery.frontier import QueryFrontier, SaturationPolicy
from product_hunter.discovery.queries import Query, normalize_query, query_id
from product_hunter.discovery.records import (
    DiscoveryRecord,
    DiscoveryStatus,
    record_id,
)
from product_hunter.discovery.store import (
    EVENT_TYPE,
    event_id,
    list_records,
    load_frontier,
    save_frontier,
    write_record,
)
from product_hunter.research.budget import BudgetSpec, BudgetTracker
from product_hunter.research.evidence import ToolCallStatus

FROZEN = datetime(2026, 10, 21, 12, 0, 0, tzinfo=timezone.utc)
LATER = datetime(2026, 10, 21, 12, 5, 0, tzinfo=timezone.utc)
RUN = "run-1"


def _store_with_run(run_id: str = RUN) -> Store:
    store = Store.open(":memory:")
    store.conn.execute(
        """
        INSERT INTO research_runs (
            run_id, public_id, objective, original_request, status,
            created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run_id,
            run_id,
            "test",
            "",
            "running",
            FROZEN.isoformat(),
            FROZEN.isoformat(),
        ),
    )
    return store


def _query(text: str = "invoice reconciliation problem") -> Query:
    return Query(
        query_id=query_id(text, "hn_algolia"),
        text=text,
        normalized=normalize_query(text),
        family=QueryFamily.problem,
        source_id="hn_algolia",
        created_at=FROZEN,
        assignment_reason="template:problem-0",
    )


def _record(
    query: Query,
    timestamp: datetime = FROZEN,
    cost: float = 0.25,
    **overrides,
) -> DiscoveryRecord:
    data = {
        "run_id": RUN,
        "stage": "discovery",
        "tool": "search",
        "agent": "discovery",
        "query_id": query.query_id,
        "query": query.text,
        "family": query.family,
        "source_id": query.source_id,
        "timestamp": timestamp,
        "result_count": 1,
        "useful_count": 1,
        "novelty": 1.0,
        "duplicate_rate": 0.0,
        "cost": cost,
        "status": DiscoveryStatus.success,
    }
    data.update(overrides)
    return DiscoveryRecord(**data)


def _frontier() -> QueryFrontier:
    return QueryFrontier(
        seed="invoice reconciliation",
        source_ids=["hn_algolia"],
        policy=SaturationPolicy(required_families=(QueryFamily.problem,)),
        clock=lambda: FROZEN,
    )


def test_a_record_writes_an_event_and_a_spend_row():
    store = _store_with_run()
    try:
        record = _record(_query())
        identifier = write_record(store.conn, record)
        assert identifier.startswith("evt_")
        event = store.conn.execute(
            "SELECT event_type, payload_json FROM research_events WHERE event_id = ?",
            (identifier,),
        ).fetchone()
        assert event["event_type"] == EVENT_TYPE
        assert record.record_id in event["payload_json"]
        spend = store.conn.execute(
            "SELECT units, unit_type, cost_micros FROM spend_ledger WHERE run_id = ?",
            (RUN,),
        ).fetchone()
        assert spend["units"] == 1
        assert spend["unit_type"] == "search_queries"
        assert spend["cost_micros"] == 250000
    finally:
        store.close()


def test_the_records_read_back_oldest_first():
    store = _store_with_run()
    try:
        write_record(store.conn, _record(_query("alpha problem"), timestamp=LATER))
        write_record(store.conn, _record(_query("beta problem"), timestamp=FROZEN))
        records = list_records(store.conn, RUN)
        assert [record.query for record in records] == [
            "beta problem",
            "alpha problem",
        ]
        assert records[0].record_id == _record(_query("beta problem")).record_id
    finally:
        store.close()


def test_writing_the_same_record_twice_is_a_no_op():
    store = _store_with_run()
    try:
        record = _record(_query())
        first = write_record(store.conn, record)
        second = write_record(store.conn, record)
        assert first == second
        count = store.conn.execute(
            "SELECT count(*) FROM research_events WHERE run_id = ?", (RUN,)
        ).fetchone()[0]
        assert count == 1
    finally:
        store.close()


def test_the_event_and_the_spend_are_written_in_one_transaction():
    store = _store_with_run()
    try:
        store.conn.execute(
            "CREATE TRIGGER refuse_spend BEFORE INSERT ON spend_ledger "
            "BEGIN SELECT RAISE(ABORT, 'no spend'); END;"
        )
        with pytest.raises(sqlite3.IntegrityError):
            write_record(store.conn, _record(_query()))
        events = store.conn.execute(
            "SELECT count(*) FROM research_events WHERE run_id = ?", (RUN,)
        ).fetchone()[0]
        assert events == 0
    finally:
        store.close()


def test_a_replay_repairs_a_missing_spend_row():
    store = _store_with_run()
    try:
        record = _record(_query())
        write_record(store.conn, record)
        store.conn.execute("DELETE FROM spend_ledger")
        write_record(store.conn, record)
        spend = store.conn.execute(
            "SELECT count(*) FROM spend_ledger WHERE run_id = ?", (RUN,)
        ).fetchone()[0]
        assert spend == 1
    finally:
        store.close()


def test_the_replay_repairs_the_spend_inside_a_callers_transaction():
    store = _store_with_run()
    try:
        record = _record(_query())
        write_record(store.conn, record)
        store.conn.execute("DELETE FROM spend_ledger")
        store.conn.execute("BEGIN IMMEDIATE")
        write_record(store.conn, record)
        # The repair belongs to the caller's transaction, so it is only
        # there once the caller commits.
        assert store.conn.in_transaction is True
        store.conn.execute("COMMIT")
        spend = store.conn.execute(
            "SELECT count(*) FROM spend_ledger WHERE run_id = ?", (RUN,)
        ).fetchone()[0]
        assert spend == 1
    finally:
        store.close()


def test_a_failed_write_undoes_itself_and_leaves_the_callers_transaction_alone():
    store = _store_with_run()
    try:
        store.conn.execute(
            "CREATE TRIGGER refuse_spend BEFORE INSERT ON spend_ledger "
            "BEGIN SELECT RAISE(ABORT, 'no spend'); END;"
        )
        store.conn.execute("BEGIN IMMEDIATE")
        with pytest.raises(sqlite3.IntegrityError):
            write_record(store.conn, _record(_query()))
        # The caller's transaction is still the caller's: the write undid
        # itself and took nothing else with it.
        assert store.conn.in_transaction is True
        store.conn.execute("COMMIT")
        events = store.conn.execute(
            "SELECT count(*) FROM research_events WHERE run_id = ?", (RUN,)
        ).fetchone()[0]
        assert events == 0
        spend = store.conn.execute(
            "SELECT count(*) FROM spend_ledger WHERE run_id = ?", (RUN,)
        ).fetchone()[0]
        assert spend == 0
    finally:
        store.close()


def test_the_write_joins_a_callers_transaction():
    store = _store_with_run()
    try:
        store.conn.execute("BEGIN IMMEDIATE")
        identifier = write_record(store.conn, _record(_query()))
        # The caller still owns the transaction: the write joined it and
        # left the commit to the caller.
        assert store.conn.in_transaction is True
        store.conn.execute("COMMIT")
        assert identifier.startswith("evt_")
        events = store.conn.execute(
            "SELECT count(*) FROM research_events WHERE run_id = ?", (RUN,)
        ).fetchone()[0]
        assert events == 1
        spend = store.conn.execute(
            "SELECT count(*) FROM spend_ledger WHERE run_id = ?", (RUN,)
        ).fetchone()[0]
        assert spend == 1
    finally:
        store.close()


def test_the_frontier_checkpoints_and_reloads():
    store = _store_with_run()
    try:
        frontier = _frontier()
        ran = _query("alpha problem")
        frontier.admit(ran)
        frontier.record_execution(
            ran,
            run_id=RUN,
            status=DiscoveryStatus.success,
            result_count=1,
            result_ids=["r1"],
            timestamp=FROZEN,
        )
        pending = _query("beta problem")
        frontier.admit(pending)
        save_frontier(store.conn, RUN, frontier)

        restored = load_frontier(store.conn, RUN)

        assert restored is not None
        assert restored.seed == frontier.seed
        assert [query.query_id for query in restored.pending] == [pending.query_id]
        assert restored.seen_results == frozenset({"r1"})
        assert restored.family_counts == {"problem": 1}
        assert restored.budget.queries == 1
    finally:
        store.close()


def test_a_restored_frontier_refuses_to_run_a_query_it_already_ran():
    store = _store_with_run()
    try:
        frontier = _frontier()
        ran = _query("alpha problem")
        frontier.admit(ran)
        frontier.record_execution(
            ran,
            run_id=RUN,
            status=DiscoveryStatus.success,
            result_count=1,
            result_ids=["r1"],
            timestamp=FROZEN,
        )
        save_frontier(store.conn, RUN, frontier)

        restored = load_frontier(store.conn, RUN)

        assert restored is not None
        with pytest.raises(ValueError) as excinfo:
            restored.record_execution(
                ran,
                run_id=RUN,
                status=DiscoveryStatus.success,
                result_count=1,
                result_ids=["r2"],
                timestamp=FROZEN,
            )
        assert "already ran" in str(excinfo.value)
    finally:
        store.close()


def test_a_missing_checkpoint_reads_back_as_nothing():
    store = _store_with_run()
    try:
        assert load_frontier(store.conn, RUN) is None
    finally:
        store.close()


def test_a_second_save_writes_the_next_checkpoint_version():
    store = _store_with_run()
    try:
        frontier = _frontier()
        save_frontier(store.conn, RUN, frontier)
        pending = _query("beta problem")
        frontier.admit(pending)
        save_frontier(store.conn, RUN, frontier)

        restored = load_frontier(store.conn, RUN)

        assert restored is not None
        assert [query.query_id for query in restored.pending] == [pending.query_id]
    finally:
        store.close()


def test_the_checkpoint_version_advances_by_one_per_save():
    store = _store_with_run()
    try:
        frontier = _frontier()
        assert frontier.checkpoint_version == 1
        first = save_frontier(store.conn, RUN, frontier)
        assert first.checkpoint_version == 1
        assert frontier.checkpoint_version == 2
        frontier.admit(_query("alpha problem"))
        second = save_frontier(store.conn, RUN, frontier)
        assert second.checkpoint_version == 2
        assert frontier.checkpoint_version == 3
        # An explicit version is written as it was given, and the counter
        # still advances, so the next automatic save lands on a free one.
        frontier.admit(_query("beta problem"))
        third = save_frontier(store.conn, RUN, frontier, version=7)
        assert third.checkpoint_version == 7
        assert frontier.checkpoint_version == 8
        frontier.admit(_query("gamma problem"))
        fourth = save_frontier(store.conn, RUN, frontier)
        assert fourth.checkpoint_version == 8
        assert frontier.checkpoint_version == 9
        # A restore continues after the version the state was written at.
        restored = load_frontier(store.conn, RUN)
        assert restored is not None
        assert restored.checkpoint_version == 9
    finally:
        store.close()


def test_an_explicit_checkpoint_version_leaves_the_next_write_on_a_free_version():
    store = _store_with_run()
    try:
        frontier = _frontier()
        save_frontier(store.conn, RUN, frontier)
        frontier.admit(_query("alpha problem"))
        save_frontier(store.conn, RUN, frontier)
        frontier.admit(_query("beta problem"))
        save_frontier(store.conn, RUN, frontier, version=7)
        # The state records the version the row was written at, so a
        # restore continues after that row.
        state = json.loads(
            store.conn.execute(
                "SELECT state_json FROM research_checkpoints "
                "WHERE checkpoint_version = 7"
            ).fetchone()["state_json"]
        )
        assert state["checkpoint_version"] == 7
        restored = load_frontier(store.conn, RUN, version=7)
        assert restored is not None
        assert restored.checkpoint_version == 8
        # So the next write lands on a version that holds nothing yet, and
        # it is the newest one, so a restore finds it.
        fourth = save_frontier(store.conn, RUN, restored)
        assert fourth.checkpoint_version == 8
        latest = load_frontier(store.conn, RUN)
        assert latest is not None
        assert latest.checkpoint_version == 9
        assert sorted(query.text for query in latest.pending) == [
            "alpha problem",
            "beta problem",
        ]
    finally:
        store.close()


def test_a_restore_of_an_older_version_writes_past_the_newest_one():
    store = _store_with_run()
    try:
        frontier = _frontier()
        save_frontier(store.conn, RUN, frontier)
        frontier.admit(_query("alpha problem"))
        save_frontier(store.conn, RUN, frontier)
        restored = load_frontier(store.conn, RUN, version=1)
        assert restored is not None
        # The next write lands on the version after the newest checkpoint,
        # so it does not write over the state that is already there.
        assert restored.checkpoint_version == 3
        third = save_frontier(store.conn, RUN, restored)
        assert third.checkpoint_version == 3
        versions = [
            row["checkpoint_version"]
            for row in store.conn.execute(
                "SELECT checkpoint_version FROM research_checkpoints "
                "ORDER BY checkpoint_version"
            )
        ]
        assert versions == [1, 2, 3]
    finally:
        store.close()


def test_a_restore_keeps_the_spend_the_run_already_made():
    store = _store_with_run()
    try:
        frontier = _frontier()
        for text in ("alpha problem", "beta problem"):
            query = _query(text)
            frontier.admit(query)
            frontier.record_execution(
                query,
                run_id=RUN,
                status=DiscoveryStatus.success,
                result_count=1,
                result_ids=[f"r-{text}"],
                cost=0.5,
                timestamp=FROZEN,
            )
        save_frontier(store.conn, RUN, frontier)

        restored = load_frontier(store.conn, RUN)

        assert restored is not None
        assert restored.budget.queries == 2
        assert restored.budget.cost == 1.0
    finally:
        store.close()


def test_a_restore_does_not_take_a_budget():
    # A caller supplied budget would replace the checkpointed one and lose
    # the spend the run already made, so the parameter is gone.
    store = _store_with_run()
    try:
        with pytest.raises(TypeError):
            load_frontier(store.conn, RUN, budget=DiscoveryBudget())
    finally:
        store.close()


def test_a_restored_frontier_still_binds_the_contract_tracker():
    store = _store_with_run()
    try:
        tracker = BudgetTracker(
            BudgetSpec(version="test-v1", per_run={"search_queries": 1.0})
        )
        frontier = QueryFrontier(
            seed="invoice reconciliation",
            source_ids=["hn_algolia"],
            budget=DiscoveryBudget(tracker=tracker),
            clock=lambda: FROZEN,
        )
        ran = _query("alpha problem")
        frontier.admit(ran)
        frontier.record_execution(
            ran,
            run_id=RUN,
            status=DiscoveryStatus.success,
            result_count=1,
            result_ids=["r1"],
            timestamp=FROZEN,
        )
        save_frontier(store.conn, RUN, frontier)

        restored = load_frontier(store.conn, RUN, tracker=tracker)

        assert restored is not None
        assert restored.budget.tracker is tracker
        admitted, reason = restored.admit(_query("beta problem"))
        assert admitted is False
        assert "per_run" in reason
        decision = restored.budget.check("hn_algolia", QueryFamily.problem, 0.0)
        assert decision.allowed is False
        assert decision.dimension == "search_queries"
    finally:
        store.close()


def test_the_record_id_is_the_golden_value_of_its_basis():
    query = _query()
    assert record_id(RUN, query.query_id, FROZEN, DiscoveryStatus.success) == (
        "dq_e706b299dc2103f6"
    )
    assert _record(query).record_id == "dq_e706b299dc2103f6"
    assert _record(query, run_id="run-2").record_id != "dq_e706b299dc2103f6"


def test_a_record_with_a_hand_written_id_is_refused():
    # Two searches that share a hand-written id would write two events and
    # one spend row, and the spend would be under-recorded.
    with pytest.raises(ValidationError) as excinfo:
        DiscoveryRecord(
            record_id="dq_0000000000000000",
            run_id=RUN,
            stage="discovery",
            tool="search",
            agent="discovery",
            query_id="q_0123456789abcdef",
            query="alpha problem",
            family=QueryFamily.problem,
            source_id="hn_algolia",
            timestamp=FROZEN,
            result_count=1,
            useful_count=1,
            novelty=1.0,
            duplicate_rate=0.0,
            cost=0.25,
            status=DiscoveryStatus.success,
        )
    assert "record_id must be" in str(excinfo.value)


def test_the_status_is_part_of_the_record_id():
    query = _query()
    assert (
        record_id(RUN, query.query_id, FROZEN, "success")
        != record_id(RUN, query.query_id, FROZEN, "empty")
    )


def test_the_event_id_is_the_golden_value_of_its_basis():
    record = _record(_query())
    assert event_id(record) == "evt_56fee2fb1257ca42"


def test_a_success_with_no_results_is_refused():
    with pytest.raises(ValidationError) as excinfo:
        _record(_query(), result_count=0, useful_count=0)
    assert "needs a result count above zero" in str(excinfo.value)


def test_an_empty_with_results_is_refused():
    with pytest.raises(ValidationError) as excinfo:
        _record(_query(), status=DiscoveryStatus.empty)
    assert "needs a result count of zero" in str(excinfo.value)


def test_a_failure_with_results_is_refused():
    with pytest.raises(ValidationError) as excinfo:
        _record(_query(), status=DiscoveryStatus.failure)
    assert "needs a result count of zero" in str(excinfo.value)


def test_a_useful_count_above_the_result_count_is_refused():
    with pytest.raises(ValidationError) as excinfo:
        _record(_query(), result_count=1, useful_count=2)
    assert "useful_count must not exceed result_count" in str(excinfo.value)


def test_a_novelty_that_does_not_add_up_is_refused():
    with pytest.raises(ValidationError) as excinfo:
        _record(_query(), novelty=0.4, duplicate_rate=0.4)
    assert "novelty and duplicate_rate must sum to one" in str(excinfo.value)


def test_a_novelty_on_an_empty_result_is_refused():
    with pytest.raises(ValidationError) as excinfo:
        _record(
            _query(),
            status=DiscoveryStatus.empty,
            result_count=0,
            useful_count=0,
            novelty=0.5,
            duplicate_rate=0.5,
        )
    assert "must be zero when no result came back" in str(excinfo.value)


def test_a_naive_timestamp_is_refused():
    with pytest.raises(ValidationError) as excinfo:
        _record(_query(), timestamp=datetime(2026, 10, 21, 12, 0, 0))
    assert "timestamp must be timezone-aware" in str(excinfo.value)


def test_a_result_ref_with_whitespace_is_refused():
    with pytest.raises(ValidationError) as excinfo:
        _record(_query(), result_ref="raw/8f14e45f a summary of the results")
    assert "result_ref must not contain whitespace" in str(excinfo.value)


def test_a_result_ref_with_an_invalid_character_is_refused():
    with pytest.raises(ValidationError) as excinfo:
        _record(_query(), result_ref="summary(results)")
    assert "result_ref must match" in str(excinfo.value)
    assert _record(_query(), result_ref="raw/8f14e45f").result_ref == "raw/8f14e45f"


def test_a_non_finite_cost_is_refused():
    with pytest.raises(ValidationError) as excinfo:
        _record(_query(), cost=float("inf"))
    assert "cost must be a finite number" in str(excinfo.value)


def test_an_empty_search_becomes_a_successful_tool_call():
    record = _record(
        _query(),
        status=DiscoveryStatus.empty,
        result_count=0,
        useful_count=0,
        novelty=0.0,
        duplicate_rate=0.0,
    )
    provenance = record.to_tool_call_provenance()
    assert provenance is not None
    assert provenance.status is ToolCallStatus.success
    assert provenance.query_family == "problem"
    assert provenance.query_or_operation == record.query


def test_a_duplicate_record_becomes_no_tool_call():
    record = _record(
        _query(),
        status=DiscoveryStatus.duplicate,
        result_count=0,
        useful_count=0,
        novelty=0.0,
        duplicate_rate=0.0,
    )
    assert record.to_tool_call_provenance() is None

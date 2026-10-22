"""The operation engine: what one cycle does and what it ends in.

Every test names the claim it holds, so a failure says which one broke. The
clock is a fixed one, so a cycle does not depend on the wall clock.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from product_hunter.autonomy.engine import (
    DEFAULT_OPERATION_ID,
    OperationEngine,
    OperationStep,
)
from product_hunter.autonomy.states import (
    BlockedError,
    OperationState,
    PermanentError,
    classify_failure,
)
from product_hunter.config import AutonomyConfig, OperationBudgetLimits, load_autonomy_config
from product_hunter.db import Store
from product_hunter.db.jobs import claim_job, enqueue_job, get_job
from product_hunter.db.scheduler import Priority, Schedule
from product_hunter.research.budget import BudgetDimension, BudgetExceeded
from product_hunter.runtime.identity import WorkerIdentity

NOW = datetime(2026, 10, 21, 12, 0, tzinfo=timezone.utc)


class _Clock:
    """A clock that stands still."""

    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


class _MovingClock:
    """A clock the test moves by hand."""

    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        """Move the clock forward."""
        self.now = self.now + timedelta(seconds=seconds)


def _identity() -> WorkerIdentity:
    return WorkerIdentity(
        worker_id="worker-test-1",
        hostname="test-host",
        pid=4242,
        started_at="2026-10-21T00:00:00+00:00",
    )


def _config(**overrides: object) -> AutonomyConfig:
    """The shipped config, with what a test changes."""
    updates: dict[str, object] = {"heartbeat_seconds": 0.0}
    updates.update(overrides)
    return load_autonomy_config().model_copy(update=updates)


def _engine(
    store: Store,
    *,
    handlers: dict | None = None,
    schedules: list[Schedule] | None = None,
    config: AutonomyConfig | None = None,
    clock=None,
) -> OperationEngine:
    return OperationEngine(
        store.conn,
        _identity(),
        handlers if handlers is not None else {},
        config=config if config is not None else _config(),
        schedules=schedules if schedules is not None else [],
        clock=clock if clock is not None else _Clock(NOW),
    )


def _job(
    store: Store, key: str, *, priority: int, payload: dict | None = None
) -> str:
    """A queued job that is due now."""
    job = enqueue_job(
        store.conn,
        stage="COLLECT",
        idempotency_key=key,
        payload=payload if payload is not None else {},
        priority=priority,
        scheduled_for=NOW.isoformat(),
        now=NOW,
    )
    return job.id


def _operation_row(store: Store) -> dict:
    row = store.conn.execute(
        "SELECT * FROM operation_runs WHERE operation_id = ?",
        (DEFAULT_OPERATION_ID,),
    ).fetchone()
    assert row is not None
    return dict(row)


def test_a_tick_creates_the_jobs_of_the_beats_whose_moment_has_passed() -> None:
    store = Store.open(":memory:")
    try:
        engine = _engine(
            store,
            schedules=[
                Schedule(
                    name="hourly",
                    stage="PROCESS",
                    interval_seconds=3600,
                    catchup_limit=2,
                )
            ],
        )
        step = engine.run_once()
        assert step.state is OperationState.running
        rows = store.conn.execute(
            "SELECT stage, payload_json FROM jobs ORDER BY scheduled_for"
        ).fetchall()
        assert len(rows) == 2
        assert {row["stage"] for row in rows} == {"PROCESS"}
    finally:
        store.close()


def test_a_beat_that_passed_while_nothing_ran_is_counted_as_missed() -> None:
    store = Store.open(":memory:")
    try:
        engine = _engine(
            store,
            schedules=[
                Schedule(
                    name="hourly",
                    stage="PROCESS",
                    interval_seconds=3600,
                    catchup_limit=2,
                )
            ],
        )
        step = engine.run_once()
        # The occurrence an hour ago came while nothing was running, and the
        # one at this instant did not.
        assert step.missed_occurrences == 1
        assert _operation_row(store)["missed_occurrences"] == 1
    finally:
        store.close()


def test_a_p0_job_is_claimed_before_a_p3_job() -> None:
    store = Store.open(":memory:")
    try:
        ran: list[str] = []

        def handle(context):
            ran.append(context.job.id)
            return {"ok": True}

        low = _job(store, "p3", priority=int(Priority.P3))
        high = _job(store, "p0", priority=int(Priority.P0))
        engine = _engine(store, handlers={"COLLECT": handle})
        step = engine.run_once()
        assert step.claimed == 1
        assert ran == [high]
        assert get_job(store.conn, low).status == "queued"
    finally:
        store.close()


def test_a_low_band_job_is_not_starved() -> None:
    store = Store.open(":memory:")
    try:
        ran: list[str] = []

        def handle(context):
            ran.append(context.job.id)
            return {"ok": True}

        # The low band job has waited long enough to reach the full boost, so
        # it outranks the newer job of the higher band.
        waited = _job(store, "old", priority=int(Priority.P4))
        store.conn.execute(
            "UPDATE jobs SET scheduled_for = ? WHERE id = ?",
            ((NOW - timedelta(seconds=16000)).isoformat(), waited),
        )
        newer = _job(store, "new", priority=int(Priority.P3))
        engine = _engine(store, handlers={"COLLECT": handle})
        step = engine.run_once()
        assert step.claimed == 1
        assert ran == [waited]
        assert get_job(store.conn, newer).status == "queued"
    finally:
        store.close()


def test_the_operation_stops_with_budget_exhausted_when_the_budget_is_over() -> None:
    store = Store.open(":memory:")
    try:
        config = _config(
            budget=OperationBudgetLimits(per_operation={"fetch_requests": 2})
        )
        engine = _engine(store, config=config)
        with pytest.raises(BudgetExceeded):
            engine.budget.record(BudgetDimension.fetch_requests, 3)

        step = engine.run_once()
        assert step.state is OperationState.budget_exhausted
        assert "fetch_requests" in step.reason
        # The budget running out is a completion and not a failure.
        assert step.failed == 0
        assert step.claimed == 0
        row = _operation_row(store)
        assert row["state"] == "budget_exhausted"
        assert row["jobs_failed"] == 0
    finally:
        store.close()


def test_the_operation_ends_budget_exhausted_once_the_wall_time_is_spent() -> None:
    store = Store.open(":memory:")
    try:
        clock = _MovingClock(NOW)
        config = _config(
            budget=OperationBudgetLimits(per_operation={"wall_time_seconds": 10})
        )
        engine = _engine(store, config=config, clock=clock)
        clock.advance(20)
        step = engine.run_once()
        assert step.state is OperationState.budget_exhausted
        # The reason names the dimension that ran out.
        assert step.reason == "wall_time_seconds"
        row = _operation_row(store)
        assert row["state"] == "budget_exhausted"
        assert row["jobs_failed"] == 0
        # The spend is in the ledger, so a new engine picks it up.
        later = _engine(store, config=config, clock=clock)
        assert later.budget.spent(BudgetDimension.wall_time_seconds) == 20
    finally:
        store.close()


def test_a_permanent_failure_ends_the_operation() -> None:
    store = Store.open(":memory:")
    try:
        def handle(_context):
            raise PermanentError("the source is gone for good")

        _job(store, "failing", priority=int(Priority.P3))
        engine = _engine(store, handlers={"COLLECT": handle})
        step = engine.run_once()
        assert step.state is OperationState.permanent_failure
        assert step.failed == 1
        row = _operation_row(store)
        assert row["state"] == "permanent_failure"
        assert row["jobs_failed"] == 1
        assert row["last_failure"] == "permanent_failure"
    finally:
        store.close()


def test_a_retryable_failure_leaves_the_operation_running() -> None:
    store = Store.open(":memory:")
    try:
        def handle(_context):
            raise RuntimeError("boom")

        _job(store, "failing", priority=int(Priority.P3))
        engine = _engine(store, handlers={"COLLECT": handle})
        step = engine.run_once()
        # A failure that may be tried again is not an end.
        assert step.state is OperationState.running
        assert step.failed == 1
        row = _operation_row(store)
        assert row["state"] == "running"
        assert row["jobs_failed"] == 1
        assert row["last_failure"] == "retryable_failure"
    finally:
        store.close()


def test_the_state_of_the_operation_and_the_event_that_says_so_are_written_together() -> None:
    store = Store.open(":memory:")
    try:
        engine = _engine(store)
        engine.start()
        engine.request_stop()
        # The log refuses the write, so the state must not land without it.
        store.conn.execute("DROP TABLE operation_events")
        with pytest.raises(sqlite3.OperationalError):
            engine.run_once()
        assert _operation_row(store)["state"] == "running"
    finally:
        store.close()


def test_an_unexpected_fault_ends_the_operation_with_a_state_an_operator_can_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = Store.open(":memory:")
    try:
        engine = _engine(store, config=_config(poll_seconds=0.01))

        def broken():
            raise RuntimeError("boom")

        monkeypatch.setattr(engine, "run_once", broken)
        engine.run_forever()

        row = _operation_row(store)
        assert row["state"] == "permanent_failure"
        assert "boom" in row["reason"]
        events = store.conn.execute(
            "SELECT event_type FROM operation_events"
        ).fetchall()
        errors = [row for row in events if row["event_type"] == "operation_error"]
        assert len(errors) == 5
    finally:
        store.close()


def test_a_good_cycle_resets_the_count_of_the_faults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = Store.open(":memory:")
    try:
        engine = _engine(
            store,
            config=_config(poll_seconds=0.01, max_consecutive_errors=3),
        )
        calls = {"n": 0}

        def flaky():
            calls["n"] += 1
            if calls["n"] == 5:
                engine.request_stop()
                return OperationStep()
            if calls["n"] in (1, 2, 4):
                raise RuntimeError("boom")
            return OperationStep()

        monkeypatch.setattr(engine, "run_once", flaky)
        engine.run_forever()

        # The faults never follow each other without a good cycle in
        # between, so the operation is still running when it is stopped.
        assert _operation_row(store)["state"] == "paused"
    finally:
        store.close()


def test_the_health_row_carries_the_last_error() -> None:
    store = Store.open(":memory:")
    try:
        def handle(_context):
            raise RuntimeError("upstream refused api_key=sk-live-1234567890")

        _job(store, "failing", priority=int(Priority.P3))
        engine = _engine(store, handlers={"COLLECT": handle})
        engine.run_once()

        row = store.conn.execute(
            "SELECT last_error, jobs_failed FROM worker_health"
        ).fetchone()
        assert row is not None
        assert row["jobs_failed"] == 1
        assert row["last_error"] is not None
        # The text is masked the way the job row masks it.
        assert "sk-live-1234567890" not in row["last_error"]
        assert "[redacted]" in row["last_error"]
    finally:
        store.close()


def test_a_message_that_merely_names_a_failure_is_not_read_as_one() -> None:
    # The marker is read with its colon, so only the text the operation
    # wrote itself is a marker.
    assert classify_failure(
        "RuntimeError: the source is permanent_failure in name only"
    ) is OperationState.retryable_failure
    assert classify_failure(
        "RuntimeError: the gate said blocked_failure in passing"
    ) is OperationState.retryable_failure
    assert classify_failure(str(PermanentError("the source is gone"))) is (
        OperationState.permanent_failure
    )
    assert classify_failure(str(PermanentError())) is OperationState.permanent_failure
    assert classify_failure(str(BlockedError("the gate refused"))) is (
        OperationState.blocked
    )


def test_a_job_whose_stage_has_no_handler_is_deferred_and_keeps_its_attempt() -> None:
    store = Store.open(":memory:")
    try:
        ran: list[str] = []

        def handle(context):
            ran.append(context.job.id)
            return {"ok": True}

        job_id = _job(store, "unhandled", priority=int(Priority.P3))
        engine = _engine(store, handlers={"BRIEF": handle})
        step = engine.run_once()
        # The job is waiting for a handler, so it is counted and reported.
        assert step.blocked == 1
        assert step.claimed == 0
        assert step.failed == 0
        assert step.deferred == 0
        assert step.state is OperationState.running
        assert ran == []

        stored = get_job(store.conn, job_id)
        assert stored is not None
        assert stored.status == "queued"
        assert stored.attempts == 0
        assert stored.last_error is None
        assert stored.scheduled_for == (NOW + timedelta(seconds=300)).isoformat()
        assert _operation_row(store)["jobs_blocked"] == 1

        events = store.conn.execute(
            "SELECT event_type, detail_json FROM operation_events"
        ).fetchall()
        blocked = [row for row in events if row["event_type"] == "job_blocked"]
        assert len(blocked) == 1
        assert "COLLECT" in blocked[0]["detail_json"]
    finally:
        store.close()


def test_the_operation_ends_paused_when_a_stop_is_requested() -> None:
    store = Store.open(":memory:")
    try:
        engine = _engine(store)
        engine.request_stop()
        step = engine.run_once()
        assert step.state is OperationState.paused
        assert _operation_row(store)["state"] == "paused"
    finally:
        store.close()


def test_the_engine_refuses_to_claim_when_the_leased_jobs_reached_the_limit() -> None:
    store = Store.open(":memory:")
    try:
        ran: list[str] = []

        def handle(context):
            ran.append(context.job.id)
            return {"ok": True}

        held = _job(store, "held", priority=int(Priority.P3))
        assert claim_job(store.conn, "other-worker", now=NOW) is not None
        waiting = _job(store, "waiting", priority=int(Priority.P3))

        engine = _engine(
            store,
            handlers={"COLLECT": handle},
            config=_config(max_concurrent_jobs=1),
        )
        step = engine.run_once()
        assert step.claimed == 0
        assert ran == []
        assert get_job(store.conn, waiting).status == "queued"
        assert get_job(store.conn, held).status == "leased"
    finally:
        store.close()


def test_the_status_command_reports_the_operation_and_fails_on_a_failed_state(
    tmp_path, capsys
) -> None:
    from product_hunter.cli import autonomy_status_command

    db_path = tmp_path / "product_hunter.db"
    store = Store.open(db_path)
    store.close()

    assert autonomy_status_command(db_path=db_path, repo_root=tmp_path) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["operation"]["state"] == "running"
    assert "queue_depth" in payload
    assert "dead_letters" in payload
    assert "budget" in payload
    assert "workers" in payload

    store = Store.open(db_path)
    try:
        store.conn.execute(
            "INSERT INTO operation_runs (operation_id, mode, state, started_at, "
            "updated_at) VALUES (?, 'continuous', 'permanent_failure', ?, ?)",
            (DEFAULT_OPERATION_ID, NOW.isoformat(), NOW.isoformat()),
        )
    finally:
        store.close()
    assert autonomy_status_command(db_path=db_path, repo_root=tmp_path) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["operation"]["state"] == "permanent_failure"


def test_a_job_for_a_source_that_is_not_due_is_deferred_and_keeps_its_attempt() -> None:
    store = Store.open(":memory:")
    try:
        ran: list[str] = []

        def handle(context):
            ran.append(context.job.id)
            return {"ok": True}

        engine = _engine(store, handlers={"COLLECT": handle})
        engine.pacer.mark_used("hn_algolia", now=NOW)
        job_id = _job(
            store,
            "paced",
            priority=int(Priority.P3),
            payload={"source_id": "hn_algolia"},
        )

        step = engine.run_once()
        assert step.deferred == 1
        assert step.failed == 0
        assert step.claimed == 0
        assert ran == []
        stored = get_job(store.conn, job_id)
        assert stored is not None
        assert stored.status == "queued"
        assert stored.attempts == 0
        assert stored.last_error is None
        assert stored.scheduled_for == (NOW + timedelta(seconds=30)).isoformat()
    finally:
        store.close()


def test_a_terminal_finish_that_raises_is_swallowed_and_the_process_ends(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A terminal ``_finish`` that cannot write the store must not propagate.

    The operation_error event already holds the cause, so the process ends
    cleanly even though the state column stays non-terminal.
    """
    store = Store.open(":memory:")
    try:
        engine = _engine(
            store, config=_config(poll_seconds=0.01, max_consecutive_errors=1)
        )

        def broken():
            raise RuntimeError("boom")

        monkeypatch.setattr(engine, "run_once", broken)

        def failing_finish(state, reason, step):
            raise sqlite3.Error("the store will not take the write")

        monkeypatch.setattr(engine, "_finish", failing_finish)

        # The fault path must end the process without raising.
        engine.run_forever()

        # The cause is in the event log, written before the terminal write.
        events = store.conn.execute(
            "SELECT event_type, detail_json FROM operation_events"
        ).fetchall()
        errors = [row for row in events if row["event_type"] == "operation_error"]
        assert len(errors) == 1
        assert "boom" in errors[0]["detail_json"]

        # The terminal write failed, so the row is not permanent_failure;
        # the exit is clean and the cause is in the event log.
        assert _operation_row(store)["state"] != "permanent_failure"
    finally:
        store.close()


def test_a_joined_transaction_failure_rolls_back_only_the_helper() -> None:
    """A helper that fails inside a joined transaction undoes only its own work.

    The caller's transaction stays open and the caller's own write remains;
    the caller's commit or rollback decides the fate of everything else.
    """
    from product_hunter.db.connection import transaction

    store = Store.open(":memory:")
    try:
        conn = store.conn
        conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("INSERT INTO t (v) VALUES ('caller')")
        try:
            with transaction(conn):
                conn.execute("INSERT INTO t (v) VALUES ('helper')")
                raise RuntimeError("helper blew up")
        except RuntimeError:
            pass
        rows = conn.execute("SELECT v FROM t ORDER BY id").fetchall()
        assert [row["v"] for row in rows] == ["caller"]
        # The caller's transaction is still open: the caller decides.
        assert conn.in_transaction is True
        conn.execute("ROLLBACK")
    finally:
        store.close()

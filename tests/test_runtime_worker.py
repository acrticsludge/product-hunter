from __future__ import annotations

import io
import json
import os
import signal
import sqlite3
import sys
import threading
import types
from datetime import datetime

import pytest

from product_hunter.db import Store
from product_hunter.db.connection import connect
from product_hunter.db.jobs import claim_job, enqueue_job, get_job
from product_hunter.runtime import __main__ as runtime_entry
from product_hunter.runtime import worker as worker_module
from product_hunter.runtime.__main__ import main
from product_hunter.runtime.events import JsonlEventSink, WorkerEvent
from product_hunter.runtime.identity import WorkerIdentity
from product_hunter.runtime.lock import SingleInstanceLock
from product_hunter.runtime.worker import (
    RetryPolicy,
    WorkerRuntime,
    install_shutdown_handlers,
)


def _identity() -> WorkerIdentity:
    return WorkerIdentity(
        worker_id="worker-test-1",
        hostname="test-host",
        pid=4242,
        started_at="2026-01-01T00:00:00+00:00",
    )


class _RecordingSink:
    """A sink that keeps the events and wakes a waiter on a chosen name."""

    def __init__(self) -> None:
        self.events: list[WorkerEvent] = []
        self.renewed = threading.Event()
        self.renewal_failed = threading.Event()

    def emit(self, event: WorkerEvent) -> None:
        self.events.append(event)
        if event.event == "lease_renewed":
            self.renewed.set()
        if event.event == "lease_renewal_failed":
            self.renewal_failed.set()

    def names(self) -> list[str]:
        return [event.event for event in self.events]

    def lines(self) -> list[dict]:
        return [json.loads(event.to_json()) for event in self.events]


def _runtime(store: Store, handlers: dict, **options) -> tuple[WorkerRuntime, io.StringIO]:
    stream = io.StringIO()
    settings = {"events": JsonlEventSink(stream), "heartbeat_seconds": 0}
    settings.update(options)
    runtime = WorkerRuntime(store.conn, _identity(), handlers, **settings)
    return runtime, stream


def _names(stream: io.StringIO) -> list[str]:
    return [json.loads(line)["event"] for line in stream.getvalue().splitlines() if line]


def _events(stream: io.StringIO) -> list[dict]:
    return [json.loads(line) for line in stream.getvalue().splitlines() if line]


def test_a_worker_id_names_the_host_the_process_and_a_token() -> None:
    identity = WorkerIdentity.create()
    token = identity.worker_id.rsplit("-", 1)[1]
    assert identity.worker_id.startswith("worker-")
    assert len(token) == 8
    assert all(character in "0123456789abcdef" for character in token)
    assert str(identity.pid) in identity.worker_id
    assert identity.hostname
    assert datetime.fromisoformat(identity.started_at)
    assert identity.to_dict()["worker_id"] == identity.worker_id


def test_the_hostname_is_reduced_to_safe_characters() -> None:
    identity = WorkerIdentity.create()
    assert all(
        character.isalnum() or character in "_-" for character in identity.hostname
    )


def test_two_identities_never_share_a_worker_id() -> None:
    assert WorkerIdentity.create().worker_id != WorkerIdentity.create().worker_id


def test_a_successful_job_writes_once_and_succeeds() -> None:
    store = Store.open(":memory:")
    try:
        store.conn.execute("CREATE TABLE test_writes (id INTEGER PRIMARY KEY, value TEXT)")
        job = enqueue_job(store.conn, stage="WRITE", idempotency_key="write-1")

        def handler(context):
            with context.transaction() as conn:
                conn.execute("INSERT INTO test_writes (value) VALUES (?)", ("ok",))
            return {"ok": True}

        runtime, stream = _runtime(store, {"WRITE": handler})
        outcome = runtime.run_once()
        assert outcome is not None
        assert outcome.status == "succeeded"
        assert outcome.result == {"ok": True}
        assert store.conn.execute("SELECT count(*) FROM test_writes").fetchone()[0] == 1
        current = get_job(store.conn, job.id)
        assert current is not None and current.status == "succeeded"
        assert "job_succeeded" in _names(stream)
    finally:
        store.close()


def test_a_failing_handler_requeues_the_job_and_reports_it() -> None:
    store = Store.open(":memory:")
    try:
        job = enqueue_job(
            store.conn, stage="FAIL", idempotency_key="fail-1", max_attempts=3
        )

        def handler(_context):
            raise RuntimeError("boom")

        runtime, stream = _runtime(
            store, {"FAIL": handler}, retry_policy=RetryPolicy(base_seconds=0.0)
        )
        outcome = runtime.run_once()
        assert outcome is not None and outcome.status == "failed"
        current = get_job(store.conn, job.id)
        assert current is not None
        assert current.status == "queued"
        assert current.last_error == "RuntimeError: boom"
        assert "job_failed" in _names(stream)
    finally:
        store.close()


def test_a_failed_attempt_is_retried_until_it_succeeds() -> None:
    store = Store.open(":memory:")
    try:
        job = enqueue_job(
            store.conn, stage="RETRY", idempotency_key="retry-1", max_attempts=3
        )
        attempts: list[int] = []

        def handler(context):
            attempts.append(context.job.attempts)
            if len(attempts) == 1:
                raise RuntimeError("first attempt fails")
            return {"attempt": context.job.attempts}

        runtime, _ = _runtime(
            store, {"RETRY": handler}, retry_policy=RetryPolicy(base_seconds=0.0)
        )
        first = runtime.run_once()
        second = runtime.run_once()
        assert first is not None and first.status == "failed"
        assert second is not None and second.status == "succeeded"
        assert attempts == [1, 2]
        current = get_job(store.conn, job.id)
        assert current is not None
        assert current.attempts == 2
        assert current.status == "succeeded"
    finally:
        store.close()


def test_a_job_at_the_attempt_ceiling_is_reported_dead() -> None:
    store = Store.open(":memory:")
    try:
        job = enqueue_job(
            store.conn, stage="FAIL", idempotency_key="dead-1", max_attempts=1
        )

        def handler(_context):
            raise RuntimeError("boom")

        runtime, stream = _runtime(
            store, {"FAIL": handler}, retry_policy=RetryPolicy(base_seconds=0.0)
        )
        outcome = runtime.run_once()
        assert outcome is not None and outcome.status == "failed"
        current = get_job(store.conn, job.id)
        assert current is not None and current.status == "dead"
        names = _names(stream)
        assert "job_failed" in names
        assert "job_dead" in names
    finally:
        store.close()


def test_a_handler_failure_masks_a_labelled_secret() -> None:
    store = Store.open(":memory:")
    try:
        job = enqueue_job(store.conn, stage="FAIL", idempotency_key="secret-1")

        def handler(_context):
            raise RuntimeError(
                "upstream refused api_key=sk-live-1234567890 "
                "and bearer: eyJhbGciOiJIUzI1NiJ9"
            )

        runtime, stream = _runtime(store, {"FAIL": handler})
        outcome = runtime.run_once()
        assert outcome is not None and outcome.status == "failed"
        current = get_job(store.conn, job.id)
        assert current is not None and current.last_error is not None
        assert "sk-live-1234567890" not in current.last_error
        assert "eyJhbGciOiJIUzI1NiJ9" not in current.last_error
        assert "[redacted]" in current.last_error
        assert "RuntimeError" in current.last_error
        failed = [event for event in _events(stream) if event["event"] == "job_failed"]
        assert len(failed) == 1
        assert "sk-live-1234567890" not in failed[0]["error"]
        assert "eyJhbGciOiJIUzI1NiJ9" not in failed[0]["error"]
        assert "[redacted]" in failed[0]["error"]
        assert "RuntimeError" in failed[0]["error"]
    finally:
        store.close()


def test_a_long_handler_failure_is_cut_to_the_bound() -> None:
    store = Store.open(":memory:")
    try:
        job = enqueue_job(store.conn, stage="FAIL", idempotency_key="long-1")

        def handler(_context):
            raise RuntimeError("x" * 500)

        runtime, _ = _runtime(store, {"FAIL": handler})
        outcome = runtime.run_once()
        assert outcome is not None and outcome.status == "failed"
        current = get_job(store.conn, job.id)
        assert current is not None and current.last_error is not None
        assert current.last_error.startswith("RuntimeError: ")
        message = current.last_error[len("RuntimeError: ") :]
        # 300 characters of message plus the ellipsis.
        assert len(message) == 303
        assert message.endswith("...")
    finally:
        store.close()


def test_the_lease_lost_message_survives_the_mask() -> None:
    store = Store.open(":memory:")
    try:
        job = enqueue_job(store.conn, stage="STEAL", idempotency_key="mask-1")

        def handler(context):
            context.conn.execute(
                "UPDATE jobs SET worker_id = 'thief', lease_epoch = lease_epoch + 1 "
                "WHERE id = ?",
                (context.job.id,),
            )
            with context.transaction() as conn:
                conn.execute("SELECT 1")
            return {"ok": True}

        runtime, stream = _runtime(store, {"STEAL": handler})
        outcome = runtime.run_once()
        assert outcome is not None and outcome.status == "lease_lost"
        lost = [event for event in _events(stream) if event["event"] == "job_lease_lost"]
        assert len(lost) == 1
        error = lost[0]["error"]
        assert error.startswith("LeaseLost: ")
        assert "[redacted]" not in error
        assert job.id in error
        assert "worker-test-1" in error
        assert "epoch 1" in error
    finally:
        store.close()


def test_a_stage_without_a_handler_is_recorded_as_a_failure() -> None:
    store = Store.open(":memory:")
    try:
        job = enqueue_job(store.conn, stage="NO_HANDLER", idempotency_key="missing-1")
        runtime, stream = _runtime(store, {"OTHER": lambda _context: {"ok": True}})
        outcome = runtime.run_once()
        assert outcome is not None and outcome.status == "failed"
        current = get_job(store.conn, job.id)
        assert current is not None
        assert current.status == "queued"
        assert current.last_error == "no handler for stage 'NO_HANDLER'"
        assert "handler_missing" in _names(stream)
    finally:
        store.close()


def test_an_expired_lease_is_recovered_and_the_job_runs() -> None:
    store = Store.open(":memory:")
    try:
        store.conn.execute("CREATE TABLE test_writes (id INTEGER PRIMARY KEY, value TEXT)")
        job = enqueue_job(store.conn, stage="WRITE", idempotency_key="recover-1")
        assert claim_job(store.conn, "worker-a", lease_seconds=1) is not None
        store.conn.execute(
            "UPDATE jobs SET lease_expires_at = '2000-01-01T00:00:00+00:00' "
            "WHERE id = ?",
            (job.id,),
        )

        def handler(context):
            with context.transaction() as conn:
                conn.execute("INSERT INTO test_writes (value) VALUES (?)", ("recovered",))
            return {"ok": True}

        runtime, _ = _runtime(store, {"WRITE": handler})
        outcome = runtime.run_once()
        assert outcome is not None and outcome.status == "succeeded"
        assert store.conn.execute("SELECT count(*) FROM test_writes").fetchone()[0] == 1
        attempts = store.conn.execute(
            "SELECT status FROM stage_attempts WHERE job_id = ? ORDER BY attempt",
            (job.id,),
        ).fetchall()
        assert [row["status"] for row in attempts] == ["lease_lost", "succeeded"]
    finally:
        store.close()


def test_recover_requeues_an_expired_lease_and_reports_the_count() -> None:
    store = Store.open(":memory:")
    try:
        job = enqueue_job(store.conn, stage="WRITE", idempotency_key="recover-2")
        assert claim_job(store.conn, "worker-a", lease_seconds=1) is not None
        store.conn.execute(
            "UPDATE jobs SET lease_expires_at = '2000-01-01T00:00:00+00:00' "
            "WHERE id = ?",
            (job.id,),
        )
        runtime, stream = _runtime(store, {})
        assert runtime.recover() == 1
        current = get_job(store.conn, job.id)
        assert current is not None and current.status == "queued"
        recovered = [event for event in _events(stream) if event["event"] == "lease_recovered"]
        assert recovered and recovered[0]["count"] == 1
    finally:
        store.close()


def test_a_stolen_lease_rejects_the_write() -> None:
    store = Store.open(":memory:")
    try:
        store.conn.execute("CREATE TABLE test_writes (id INTEGER PRIMARY KEY, value TEXT)")
        job = enqueue_job(store.conn, stage="STEAL", idempotency_key="steal-1")

        def handler(context):
            context.conn.execute(
                "UPDATE jobs SET worker_id = 'thief', lease_epoch = lease_epoch + 1 "
                "WHERE id = ?",
                (context.job.id,),
            )
            with context.transaction() as conn:
                conn.execute("INSERT INTO test_writes (value) VALUES (?)", ("nope",))
            return {"ok": True}

        runtime, stream = _runtime(store, {"STEAL": handler})
        outcome = runtime.run_once()
        assert outcome is not None
        assert outcome.status == "lease_lost"
        assert store.conn.execute("SELECT count(*) FROM test_writes").fetchone()[0] == 0
        current = get_job(store.conn, job.id)
        assert current is not None
        assert current.status == "leased"
        assert current.worker_id == "thief"
        assert current.lease_epoch == 2
        names = _names(stream)
        assert "job_lease_lost" in names
        assert "job_succeeded" not in names
    finally:
        store.close()


def test_a_stale_worker_cannot_mark_a_job_succeeded() -> None:
    store = Store.open(":memory:")
    try:
        job = enqueue_job(store.conn, stage="STEAL", idempotency_key="steal-2")

        def handler(context):
            context.conn.execute(
                "UPDATE jobs SET worker_id = 'thief', lease_epoch = lease_epoch + 1 "
                "WHERE id = ?",
                (context.job.id,),
            )
            return {"ok": True}

        runtime, stream = _runtime(store, {"STEAL": handler})
        outcome = runtime.run_once()
        assert outcome is not None
        assert outcome.status == "lease_lost"
        current = get_job(store.conn, job.id)
        assert current is not None
        assert current.status == "leased"
        assert current.worker_id == "thief"
        assert current.completed_at is None
        assert current.result is None
        assert "job_succeeded" not in _names(stream)
    finally:
        store.close()


def test_the_heartbeat_renews_the_lease_of_a_running_job(tmp_path) -> None:
    store = Store.open(tmp_path / "worker.db")
    try:
        enqueue_job(store.conn, stage="SLOW", idempotency_key="renew-1")
        sink = _RecordingSink()

        def handler(_context):
            assert sink.renewed.wait(5.0)
            return {"ok": True}

        runtime = WorkerRuntime(
            store.conn,
            _identity(),
            {"SLOW": handler},
            heartbeat_seconds=0.02,
            lease_seconds=60,
            events=sink,
        )
        outcome = runtime.run_once()
        assert outcome is not None and outcome.status == "succeeded"
        assert "lease_renewed" in sink.names()
        assert "lease_renewal_failed" not in sink.names()
    finally:
        store.close()


def test_a_lease_loss_stops_the_runtime_and_writes_nothing(tmp_path) -> None:
    store = Store.open(tmp_path / "worker.db")
    thief = connect(tmp_path / "worker.db")
    try:
        store.conn.execute("CREATE TABLE test_writes (id INTEGER PRIMARY KEY, value TEXT)")
        job = enqueue_job(store.conn, stage="SLOW", idempotency_key="slow-1")
        sink = _RecordingSink()

        def handler(context):
            # Another worker takes the lease over while this handler runs.
            thief.execute(
                "UPDATE jobs SET worker_id = 'thief', lease_epoch = lease_epoch + 1 "
                "WHERE id = ?",
                (context.job.id,),
            )
            assert sink.renewal_failed.wait(5.0)
            return {"ok": True}

        runtime = WorkerRuntime(
            store.conn,
            _identity(),
            {"SLOW": handler},
            heartbeat_seconds=0.02,
            lease_seconds=60,
            events=sink,
        )
        outcome = runtime.run_once()
        assert outcome is not None
        assert outcome.status == "lease_lost"
        names = sink.names()
        assert "lease_renewal_failed" in names
        assert "job_lease_lost" in names
        assert "job_succeeded" not in names
        assert store.conn.execute("SELECT count(*) FROM test_writes").fetchone()[0] == 0
        current = get_job(store.conn, job.id)
        assert current is not None
        assert current.status == "leased"
        assert current.worker_id == "thief"
        assert current.completed_at is None
        assert current.result is None
        assert current.last_error is None
    finally:
        thief.close()
        store.close()


def test_a_handler_that_leaves_the_runtime_stops_the_heartbeat(tmp_path) -> None:
    store = Store.open(tmp_path / "worker.db")
    try:
        enqueue_job(store.conn, stage="SLOW", idempotency_key="leave-1")
        sink = _RecordingSink()

        def handler(_context):
            assert sink.renewed.wait(5.0)
            raise SystemExit(3)

        runtime = WorkerRuntime(
            store.conn,
            _identity(),
            {"SLOW": handler},
            heartbeat_seconds=0.02,
            lease_seconds=60,
            events=sink,
        )
        with pytest.raises(SystemExit):
            runtime.run_once()
        assert "lease_renewed" in sink.names()
        alive = [
            thread.name
            for thread in threading.enumerate()
            if thread.name.startswith("heartbeat-")
        ]
        assert alive == []
    finally:
        store.close()


def test_a_stop_event_ends_the_loop_after_the_running_job() -> None:
    store = Store.open(":memory:")
    try:
        store.conn.execute("CREATE TABLE test_writes (id INTEGER PRIMARY KEY, value TEXT)")
        first = enqueue_job(store.conn, stage="WRITE", idempotency_key="stop-1")
        second = enqueue_job(store.conn, stage="WRITE", idempotency_key="stop-2")
        stop = threading.Event()

        def handler(context):
            with context.transaction() as conn:
                conn.execute("INSERT INTO test_writes (value) VALUES (?)", (context.job.id,))
            stop.set()
            return {"ok": True}

        runtime, _ = _runtime(store, {"WRITE": handler}, poll_seconds=0.01)
        runtime.run_forever(stop)
        assert store.conn.execute("SELECT count(*) FROM test_writes").fetchone()[0] == 1
        assert get_job(store.conn, first.id).status == "succeeded"
        assert get_job(store.conn, second.id).status == "queued"
    finally:
        store.close()


def test_a_stop_request_ends_the_loop_and_lets_the_job_finish() -> None:
    store = Store.open(":memory:")
    try:
        store.conn.execute("CREATE TABLE test_writes (id INTEGER PRIMARY KEY, value TEXT)")
        first = enqueue_job(store.conn, stage="WRITE", idempotency_key="stop-3")
        second = enqueue_job(store.conn, stage="WRITE", idempotency_key="stop-4")
        holder: list[WorkerRuntime] = []

        def handler(context):
            with context.transaction() as conn:
                conn.execute("INSERT INTO test_writes (value) VALUES (?)", (context.job.id,))
            holder[0].request_stop()
            return {"ok": True}

        runtime, stream = _runtime(store, {"WRITE": handler}, poll_seconds=0.01)
        holder.append(runtime)
        runtime.run_forever()
        assert store.conn.execute("SELECT count(*) FROM test_writes").fetchone()[0] == 1
        assert get_job(store.conn, first.id).status == "succeeded"
        assert get_job(store.conn, second.id).status == "queued"
        names = _names(stream)
        assert "shutdown_requested" in names
        assert "worker_stopped" in names
    finally:
        store.close()


def test_one_idempotency_key_means_one_job_and_one_row() -> None:
    store = Store.open(":memory:")
    try:
        store.conn.execute("CREATE TABLE test_rows (id TEXT PRIMARY KEY)")
        job = enqueue_job(store.conn, stage="DUP", idempotency_key="dup-1")
        again = enqueue_job(store.conn, stage="DUP", idempotency_key="dup-1")
        assert again.id == job.id
        seen: list[str] = []

        def handler(context):
            seen.append(context.job.idempotency_key)
            with context.transaction() as conn:
                conn.execute(
                    "INSERT OR IGNORE INTO test_rows (id) VALUES (?)",
                    (context.job.idempotency_key,),
                )
            return {"ok": True}

        runtime, _ = _runtime(store, {"DUP": handler})
        first = runtime.run_once()
        assert first is not None and first.status == "succeeded"
        # A re-execution of the same job converges on the same row.
        store.conn.execute("UPDATE jobs SET status = 'queued' WHERE id = ?", (job.id,))
        second = runtime.run_once()
        assert second is not None and second.status == "succeeded"
        assert seen == ["dup-1", "dup-1"]
        assert store.conn.execute("SELECT count(*) FROM test_rows").fetchone()[0] == 1
        assert store.conn.execute("SELECT count(*) FROM jobs").fetchone()[0] == 1
    finally:
        store.close()


def test_a_runtime_without_handlers_claims_nothing() -> None:
    store = Store.open(":memory:")
    try:
        job = enqueue_job(store.conn, stage="WRITE", idempotency_key="idle-1")
        runtime, stream = _runtime(store, {})
        assert runtime.run_once() is None
        current = get_job(store.conn, job.id)
        assert current is not None
        assert current.status == "queued"
        assert current.attempts == 0
        assert current.worker_id is None
        assert _names(stream) == []
    finally:
        store.close()


def test_an_idle_runtime_says_that_it_has_no_handlers() -> None:
    store = Store.open(":memory:")
    try:
        runtime, stream = _runtime(store, {})
        runtime.request_stop()
        runtime.run_forever()
        names = _names(stream)
        assert "worker_started" in names
        assert "no_handlers" in names
        assert "worker_stopped" in names
    finally:
        store.close()


def test_every_event_line_is_valid_json_with_the_worker_id() -> None:
    store = Store.open(":memory:")
    try:
        job = enqueue_job(store.conn, stage="WRITE", idempotency_key="events-1")

        def handler(_context):
            return {"ok": True}

        runtime, stream = _runtime(store, {"WRITE": handler})
        runtime.run_once()
        events = _events(stream)
        assert events
        for event in events:
            assert event["event"]
            assert event["worker_id"] == "worker-test-1"
            assert event["ts"]
            assert datetime.fromisoformat(event["ts"])
        claimed = [event for event in events if event["event"] == "job_claimed"]
        assert len(claimed) == 1
        assert claimed[0]["job_id"] == job.id
        assert claimed[0]["stage"] == "WRITE"
        assert claimed[0]["attempt"] == 1
        assert claimed[0]["lease_epoch"] == 1
    finally:
        store.close()


def test_the_retry_policy_grows_and_caps_the_backoff() -> None:
    policy = RetryPolicy(base_seconds=5.0, multiplier=2.0, max_seconds=30.0)
    assert policy.delay_for(1) == 5
    assert policy.delay_for(2) == 10
    assert policy.delay_for(3) == 20
    assert policy.delay_for(4) == 30
    assert policy.delay_for(9) == 30


def test_the_runtime_refuses_impossible_settings() -> None:
    store = Store.open(":memory:")
    try:
        with pytest.raises(ValueError):
            WorkerRuntime(store.conn, _identity(), {}, lease_seconds=0)
        with pytest.raises(ValueError):
            WorkerRuntime(store.conn, _identity(), {}, heartbeat_seconds=-1)
        with pytest.raises(ValueError):
            WorkerRuntime(store.conn, _identity(), {}, poll_seconds=0)
    finally:
        store.close()


def test_a_heartbeat_on_an_in_memory_database_is_refused() -> None:
    store = Store.open(":memory:")
    try:
        # The heartbeat needs a second connection, and an in-memory database
        # has no file to open one against.
        with pytest.raises(ValueError):
            WorkerRuntime(store.conn, _identity(), {}, heartbeat_seconds=1)
    finally:
        store.close()


def test_the_runtime_refuses_a_heartbeat_that_cannot_renew_in_time(tmp_path) -> None:
    store = Store.open(tmp_path / "worker.db")
    try:
        with pytest.raises(ValueError) as excinfo:
            WorkerRuntime(
                store.conn, _identity(), {}, lease_seconds=10, heartbeat_seconds=10
            )
        message = str(excinfo.value)
        assert "heartbeat_seconds" in message
        assert "lease_seconds" in message
        assert "10" in message
        with pytest.raises(ValueError):
            WorkerRuntime(
                store.conn, _identity(), {}, lease_seconds=10, heartbeat_seconds=30
            )
        # A heartbeat that renews in time is accepted.
        runtime = WorkerRuntime(
            store.conn, _identity(), {}, lease_seconds=10, heartbeat_seconds=5
        )
        assert runtime.heartbeat_seconds == 5
        # No heartbeat at all stays allowed.
        WorkerRuntime(store.conn, _identity(), {}, lease_seconds=10, heartbeat_seconds=0)
    finally:
        store.close()


def test_the_signal_handlers_route_to_the_stop_request() -> None:
    store = Store.open(":memory:")
    try:
        runtime, _ = _runtime(store, {})
        previous = signal.getsignal(signal.SIGINT)
        uninstall = install_shutdown_handlers(runtime)
        try:
            for sig in (signal.SIGINT, signal.SIGTERM):
                handler = signal.getsignal(sig)
                assert callable(handler)
                handler(sig, None)
            assert runtime.stop_event.is_set()
        finally:
            uninstall()
        assert signal.getsignal(signal.SIGINT) is previous
    finally:
        store.close()


def test_the_handlers_cannot_be_installed_outside_the_main_thread() -> None:
    store = Store.open(":memory:")
    try:
        runtime, _ = _runtime(store, {})
        installed: list[object] = []

        def install() -> None:
            installed.append(install_shutdown_handlers(runtime))

        thread = threading.Thread(target=install)
        thread.start()
        thread.join()
        assert len(installed) == 1
        uninstall = installed[0]
        assert callable(uninstall)
        uninstall()
        assert not runtime.stop_event.is_set()
    finally:
        store.close()


def test_the_entry_point_starts_and_gives_the_lock_back(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("PH_WORKER_LOCK", str(tmp_path / "worker.lock"))
    monkeypatch.setenv("PH_DB_PATH", str(tmp_path / "worker.db"))
    monkeypatch.setattr(WorkerRuntime, "run_forever", lambda self: None)
    assert main() == 0
    assert (tmp_path / "worker.db").exists()
    later = SingleInstanceLock(tmp_path / "worker.lock")
    try:
        assert later.acquire() is True
    finally:
        later.release()


def test_the_entry_point_refuses_to_start_beside_a_running_worker(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setenv("PH_WORKER_LOCK", str(tmp_path / "worker.lock"))
    monkeypatch.setenv("PH_DB_PATH", str(tmp_path / "worker.db"))
    holder = SingleInstanceLock(tmp_path / "worker.lock")
    assert holder.acquire() is True
    try:
        assert main() == 2
    finally:
        holder.release()


def test_the_heartbeat_survives_a_busy_database(tmp_path, monkeypatch) -> None:
    store = Store.open(tmp_path / "worker.db")
    try:
        job = enqueue_job(store.conn, stage="SLOW", idempotency_key="busy-1")
        sink = _RecordingSink()
        real_renew = worker_module.renew_lease
        attempts = {"n": 0}

        def flaky_renew(*args, **kwargs):
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise sqlite3.OperationalError("database is locked")
            return real_renew(*args, **kwargs)

        monkeypatch.setattr(worker_module, "renew_lease", flaky_renew)

        def handler(_context):
            assert sink.renewal_failed.wait(5.0)
            return {"ok": True}

        runtime = WorkerRuntime(
            store.conn,
            _identity(),
            {"SLOW": handler},
            heartbeat_seconds=0.02,
            lease_seconds=60,
            events=sink,
        )
        outcome = runtime.run_once()
        assert outcome is not None
        assert outcome.status == "succeeded"
        assert "lease_renewal_failed" in sink.names()
        assert "job_lease_lost" not in sink.names()
        current = get_job(store.conn, job.id)
        assert current is not None and current.status == "succeeded"
    finally:
        store.close()


def test_the_outcome_write_is_retried_while_the_file_is_busy(monkeypatch) -> None:
    store = Store.open(":memory:")
    try:
        job = enqueue_job(store.conn, stage="WRITE", idempotency_key="busy-2")
        real_complete = worker_module.complete_job
        attempts = {"n": 0}

        def flaky_complete(*args, **kwargs):
            attempts["n"] += 1
            if attempts["n"] <= 2:
                raise sqlite3.OperationalError("database is locked")
            return real_complete(*args, **kwargs)

        monkeypatch.setattr(worker_module, "complete_job", flaky_complete)

        def handler(_context):
            return {"ok": True}

        runtime, stream = _runtime(store, {"WRITE": handler})
        outcome = runtime.run_once()
        assert outcome is not None
        assert outcome.status == "succeeded"
        assert attempts["n"] == 3
        current = get_job(store.conn, job.id)
        assert current is not None and current.status == "succeeded"
        names = _names(stream)
        assert "job_lease_lost" not in names
        assert "job_outcome_failed" not in names
    finally:
        store.close()


def test_a_failed_outcome_write_leaves_the_job_alone(monkeypatch) -> None:
    store = Store.open(":memory:")
    try:
        job = enqueue_job(store.conn, stage="WRITE", idempotency_key="busy-3")

        def busy_complete(*args, **kwargs):
            raise sqlite3.OperationalError("database is locked")

        monkeypatch.setattr(worker_module, "complete_job", busy_complete)

        def handler(_context):
            return {"ok": True}

        runtime, stream = _runtime(store, {"WRITE": handler})
        outcome = runtime.run_once()
        assert outcome is not None
        assert outcome.status == "lease_lost"
        names = _names(stream)
        assert "job_outcome_failed" in names
        assert "job_succeeded" not in names
        current = get_job(store.conn, job.id)
        assert current is not None
        assert current.status == "leased"
    finally:
        store.close()


def test_a_failed_failure_write_leaves_the_job_alone(monkeypatch) -> None:
    store = Store.open(":memory:")
    try:
        job = enqueue_job(store.conn, stage="FAIL", idempotency_key="busy-4")

        def busy_fail(*args, **kwargs):
            raise sqlite3.OperationalError("database is locked")

        monkeypatch.setattr(worker_module, "fail_job", busy_fail)

        def handler(_context):
            raise RuntimeError("boom")

        runtime, stream = _runtime(store, {"FAIL": handler})
        outcome = runtime.run_once()
        assert outcome is not None
        assert outcome.status == "lease_lost"
        names = _names(stream)
        assert "job_outcome_failed" in names
        assert "job_failed" not in names
        current = get_job(store.conn, job.id)
        assert current is not None
        assert current.status == "leased"
    finally:
        store.close()


def test_the_loop_survives_a_database_error(monkeypatch) -> None:
    store = Store.open(":memory:")
    try:
        runtime, stream = _runtime(
            store, {"WRITE": lambda _context: {"ok": True}}, poll_seconds=0.01
        )
        real_run_once = runtime.run_once
        calls = {"n": 0}

        def flaky_run_once():
            calls["n"] += 1
            if calls["n"] == 1:
                raise sqlite3.OperationalError("database is locked")
            if calls["n"] == 2:
                runtime.request_stop()
            return real_run_once()

        monkeypatch.setattr(runtime, "run_once", flaky_run_once)
        runtime.run_forever()
        names = _names(stream)
        assert "worker_error" in names
        assert "worker_stopped" in names
    finally:
        store.close()


def test_the_mask_covers_a_bearer_token() -> None:
    store = Store.open(":memory:")
    try:
        job = enqueue_job(store.conn, stage="FAIL", idempotency_key="bearer-1")

        def handler(_context):
            raise RuntimeError(
                "the call failed with Authorization: Bearer eyJhbGciOiJIUzI1NiJ9"
            )

        runtime, stream = _runtime(store, {"FAIL": handler})
        outcome = runtime.run_once()
        assert outcome is not None and outcome.status == "failed"
        current = get_job(store.conn, job.id)
        assert current is not None and current.last_error is not None
        assert "eyJhbGciOiJIUzI1NiJ9" not in current.last_error
        assert "RuntimeError" in current.last_error
        failed = [event for event in _events(stream) if event["event"] == "job_failed"]
        assert len(failed) == 1
        assert "eyJhbGciOiJIUzI1NiJ9" not in failed[0]["error"]
    finally:
        store.close()


def test_the_entry_point_reads_the_handlers_from_the_environment(
    monkeypatch, tmp_path
) -> None:
    module = types.ModuleType("fake_handlers")
    module.build = lambda: {"WRITE": lambda _context: {"ok": True}}
    monkeypatch.setitem(sys.modules, "fake_handlers", module)
    monkeypatch.setenv("PH_WORKER_HANDLERS", "fake_handlers:build")
    monkeypatch.setenv("PH_WORKER_LOCK", str(tmp_path / "worker.lock"))
    monkeypatch.setenv("PH_DB_PATH", str(tmp_path / "worker.db"))
    built: list[WorkerRuntime] = []

    class _RecordingRuntime(WorkerRuntime):
        def __init__(self, *args, **kwargs) -> None:
            super().__init__(*args, **kwargs)
            built.append(self)

        def run_forever(self, stop_event=None) -> None:
            return None

    monkeypatch.setattr(runtime_entry, "WorkerRuntime", _RecordingRuntime)
    assert main() == 0
    assert len(built) == 1
    handlers = built[0].handlers
    assert "WRITE" in handlers
    assert callable(handlers["WRITE"])


def test_the_entry_point_refuses_a_broken_handler_spec(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("PH_WORKER_HANDLERS", "not-a-module-and-attribute")
    monkeypatch.setenv("PH_WORKER_LOCK", str(tmp_path / "worker.lock"))
    monkeypatch.setenv("PH_DB_PATH", str(tmp_path / "worker.db"))
    assert main() == 2


def test_the_watchdog_ends_a_shutdown_that_overruns_the_grace_period(
    monkeypatch,
) -> None:
    store = Store.open(":memory:")
    try:
        runtime, stream = _runtime(store, {})
        runtime.request_stop()
        exits: list[int] = []
        monkeypatch.setattr(runtime_entry, "GRACE_SECONDS", 0.01)
        monkeypatch.setattr(os, "_exit", lambda code: exits.append(code))
        runtime_entry.watch_shutdown(runtime, threading.Event(), JsonlEventSink(stream))
        assert exits == [3]
        assert "shutdown_timeout" in _names(stream)
    finally:
        store.close()


def test_the_watchdog_leaves_a_worker_that_finishes_in_time(monkeypatch) -> None:
    store = Store.open(":memory:")
    try:
        runtime, stream = _runtime(store, {})
        runtime.request_stop()
        finished = threading.Event()
        finished.set()
        exits: list[int] = []
        monkeypatch.setattr(runtime_entry, "GRACE_SECONDS", 0.01)
        monkeypatch.setattr(os, "_exit", lambda code: exits.append(code))
        runtime_entry.watch_shutdown(runtime, finished, JsonlEventSink(stream))
        assert exits == []
        assert "shutdown_timeout" not in _names(stream)
    finally:
        store.close()

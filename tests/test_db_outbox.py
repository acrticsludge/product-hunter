from __future__ import annotations

from datetime import timedelta

import pytest

from product_hunter.db import Store
from product_hunter.db.jobs import LeaseLost
from product_hunter.db.outbox import (
    OutboxConflict,
    claim_projection,
    complete_projection,
    enqueue_projection,
    fail_projection,
    get_projection_task,
    list_projection_tasks,
    process_outbox,
)
from product_hunter.fetch import utcnow

NOW = utcnow()


def _store() -> Store:
    return Store.open(":memory:")


def _task(store: Store, key: str = "projection-1", **kwargs):
    return enqueue_projection(
        store.conn,
        target=kwargs.pop("target", "obsidian"),
        entity_type=kwargs.pop("entity_type", "run"),
        entity_id=kwargs.pop("entity_id", "run-1"),
        idempotency_key=key,
        payload=kwargs.pop("payload", {"run_id": "run-1"}),
        now=NOW,
        **kwargs,
    )


def test_enqueue_is_idempotent_and_refuses_a_changed_payload() -> None:
    store = _store()
    try:
        first = _task(store)
        second = _task(store)
        assert first.task_id == second.task_id
        assert store.conn.execute("SELECT count(*) FROM projection_tasks").fetchone()[0] == 1
        with pytest.raises(OutboxConflict):
            _task(store, payload={"run_id": "run-2"})
    finally:
        store.close()


def test_claim_and_complete_with_the_current_lease() -> None:
    store = _store()
    try:
        task = _task(store)
        claimed = claim_projection(store.conn, "worker-a", lease_seconds=60, now=NOW)
        assert claimed is not None
        assert claimed.task_id == task.task_id
        assert claimed.status == "processing"
        assert claimed.worker_id == "worker-a"
        assert claimed.attempts == 1
        assert claimed.lease_epoch == 1
        completed = complete_projection(
            store.conn,
            claimed,
            worker_id="worker-a",
            lease_epoch=1,
            now=NOW + timedelta(seconds=1),
        )
        assert completed.status == "done"
        assert completed.completed_at is not None
        assert claim_projection(store.conn, "worker-b", now=NOW) is None
    finally:
        store.close()


def test_an_expired_projection_lease_is_reclaimed_and_fenced() -> None:
    store = _store()
    try:
        task = _task(store)
        first = claim_projection(store.conn, "worker-a", lease_seconds=1, now=NOW)
        assert first is not None
        store.conn.execute(
            "UPDATE projection_tasks SET lease_expires_at = ? WHERE task_id = ?",
            ((NOW - timedelta(seconds=1)).isoformat(), task.task_id),
        )
        with pytest.raises(LeaseLost):
            complete_projection(
                store.conn, first, worker_id="worker-a", lease_epoch=1, now=NOW
            )
        with pytest.raises(LeaseLost):
            fail_projection(
                store.conn,
                first,
                worker_id="worker-a",
                lease_epoch=1,
                error="too late",
                now=NOW,
            )
        second = claim_projection(store.conn, "worker-b", lease_seconds=60, now=NOW)
        assert second is not None
        assert second.lease_epoch == 2
        assert second.attempts == 2
        with pytest.raises(LeaseLost):
            complete_projection(
                store.conn, first, worker_id="worker-a", lease_epoch=1, now=NOW
            )
        completed = complete_projection(
            store.conn, second, worker_id="worker-b", lease_epoch=2, now=NOW
        )
        assert completed.status == "done"
    finally:
        store.close()


def test_failure_retries_with_backoff_then_fails_at_the_ceiling() -> None:
    store = _store()
    try:
        task = _task(store, max_attempts=2)
        first = claim_projection(store.conn, "worker-a", lease_seconds=60, now=NOW)
        assert first is not None
        pending = fail_projection(
            store.conn,
            first,
            worker_id="worker-a",
            lease_epoch=1,
            error="downstream down",
            backoff_seconds=10,
            now=NOW,
        )
        assert pending.status == "pending"
        assert pending.attempts == 1
        assert claim_projection(store.conn, "worker-b", now=NOW) is None
        second = claim_projection(
            store.conn, "worker-b", now=NOW + timedelta(seconds=11)
        )
        assert second is not None
        failed = fail_projection(
            store.conn,
            second,
            worker_id="worker-b",
            lease_epoch=2,
            error="downstream still down",
            now=NOW + timedelta(seconds=12),
        )
        assert failed.status == "failed"
        assert failed.completed_at is not None
        assert get_projection_task(store.conn, task.task_id).status == "failed"
    finally:
        store.close()


def test_process_outbox_runs_each_handler_once() -> None:
    store = _store()
    try:
        _task(store, key="one", entity_id="run-1")
        _task(store, key="two", entity_id="run-2", payload={"run_id": "run-2"})
        seen: list[str] = []

        def handler(task):
            seen.append(task.entity_id)

        processed = process_outbox(
            store.conn, {"obsidian": handler}, "worker-1", max_tasks=10, now=NOW
        )
        assert processed == 2
        assert sorted(seen) == ["run-1", "run-2"]
        assert len(list_projection_tasks(store.conn, status="done")) == 2
        assert process_outbox(store.conn, {"obsidian": handler}, "worker-1", now=NOW) == 0
    finally:
        store.close()


def test_a_projector_failure_is_recorded_and_retried() -> None:
    store = _store()
    try:
        _task(store)
        calls = {"n": 0}

        def handler(_task):
            calls["n"] += 1
            raise RuntimeError("projection failed")

        processed = process_outbox(
            store.conn, {"obsidian": handler}, "worker-1", max_tasks=1, now=NOW
        )
        assert processed == 1
        assert calls["n"] == 1
        task = list_projection_tasks(store.conn)[0]
        assert task.status == "pending"
        assert task.attempts == 1
        assert task.last_error is not None and "RuntimeError" in task.last_error
    finally:
        store.close()

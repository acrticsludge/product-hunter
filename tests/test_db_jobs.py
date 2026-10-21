from __future__ import annotations

from datetime import timedelta

import pytest

from product_hunter.db import Store
from product_hunter.db.jobs import (
    JobConflict,
    JobContext,
    LeaseLost,
    claim_job,
    complete_job,
    enqueue_job,
    fail_job,
    get_job,
    list_jobs,
    recover_expired_leases,
    renew_lease,
)
from product_hunter.fetch import utcnow

NOW = utcnow()


def _store() -> Store:
    return Store.open(":memory:")


def _job(store: Store, **kwargs):
    return enqueue_job(
        store.conn,
        stage=kwargs.pop("stage", "EXTRACT"),
        idempotency_key=kwargs.pop("idempotency_key", "job-key-1"),
        payload=kwargs.pop("payload", {"document": "doc-1"}),
        now=NOW,
        **kwargs,
    )


def test_enqueue_is_idempotent_and_refuses_a_changed_payload() -> None:
    store = _store()
    try:
        first = _job(store)
        second = _job(store)
        assert first.id == second.id
        assert first.status == "queued"
        assert store.conn.execute("SELECT count(*) FROM jobs").fetchone()[0] == 1
        with pytest.raises(JobConflict):
            _job(store, payload={"document": "doc-2"})
    finally:
        store.close()


def test_claim_renew_and_complete_with_the_current_lease() -> None:
    store = _store()
    try:
        job = _job(store)
        claimed = claim_job(store.conn, "worker-a", lease_seconds=60, now=NOW)
        assert claimed is not None
        assert claimed.id == job.id
        assert claimed.status == "leased"
        assert claimed.worker_id == "worker-a"
        assert claimed.attempts == 1
        assert claimed.lease_epoch == 1

        renewed = renew_lease(
            store.conn,
            claimed,
            worker_id="worker-a",
            lease_epoch=1,
            lease_seconds=60,
            now=NOW + timedelta(seconds=10),
        )
        assert renewed > claimed.lease_expires_at

        completed = complete_job(
            store.conn,
            claimed,
            worker_id="worker-a",
            lease_epoch=1,
            result={"ok": True},
            now=NOW + timedelta(seconds=20),
        )
        assert completed.status == "succeeded"
        assert completed.result == {"ok": True}
        assert completed.worker_id is None
        assert claim_job(store.conn, "worker-b", now=NOW + timedelta(seconds=30)) is None

        attempts = store.conn.execute(
            "SELECT status, worker_id, lease_epoch FROM stage_attempts ORDER BY attempt"
        ).fetchall()
        assert len(attempts) == 1
        assert attempts[0]["status"] == "succeeded"
        assert attempts[0]["worker_id"] == "worker-a"
        assert attempts[0]["lease_epoch"] == 1
    finally:
        store.close()


def test_expired_lease_is_reclaimed_and_the_old_worker_cannot_write() -> None:
    store = _store()
    try:
        store.conn.execute("CREATE TABLE test_writes (id INTEGER PRIMARY KEY, value TEXT)")
        job = _job(store)
        first = claim_job(store.conn, "worker-a", lease_seconds=1, now=NOW)
        assert first is not None
        store.conn.execute(
            "UPDATE jobs SET lease_expires_at = ? WHERE id = ?",
            ((NOW - timedelta(seconds=1)).isoformat(), job.id),
        )

        second = claim_job(store.conn, "worker-b", lease_seconds=60, now=NOW)
        assert second is not None
        assert second.lease_epoch == 2
        assert second.worker_id == "worker-b"
        assert second.attempts == 2

        with pytest.raises(LeaseLost):
            complete_job(
                store.conn,
                first,
                worker_id="worker-a",
                lease_epoch=1,
                result={"stale": True},
                now=NOW,
            )

        old_context = JobContext(store.conn, first, worker_id="worker-a")
        with pytest.raises(LeaseLost):
            with old_context.transaction():
                store.conn.execute(
                    "INSERT INTO test_writes (value) VALUES (?)", ("stale",)
                )
        assert store.conn.execute("SELECT count(*) FROM test_writes").fetchone()[0] == 0

        completed = complete_job(
            store.conn,
            second,
            worker_id="worker-b",
            lease_epoch=2,
            result={"fresh": True},
            now=NOW,
        )
        assert completed.status == "succeeded"
        assert completed.result == {"fresh": True}

        statuses = [
            row["status"]
            for row in store.conn.execute(
                "SELECT status FROM stage_attempts ORDER BY attempt"
            )
        ]
        assert statuses == ["lease_lost", "succeeded"]
    finally:
        store.close()


def test_failure_retries_then_stops_at_the_attempt_ceiling() -> None:
    store = _store()
    try:
        job = _job(store, max_attempts=2)
        first = claim_job(store.conn, "worker-a", lease_seconds=60, now=NOW)
        assert first is not None
        failed = fail_job(
            store.conn,
            first,
            worker_id="worker-a",
            lease_epoch=1,
            error="boom",
            backoff_seconds=10,
            now=NOW,
        )
        assert failed.status == "queued"
        assert failed.attempts == 1
        assert failed.last_error == "boom"
        assert claim_job(store.conn, "worker-b", now=NOW) is None

        second = claim_job(
            store.conn,
            "worker-b",
            lease_seconds=60,
            now=NOW + timedelta(seconds=11),
        )
        assert second is not None
        assert second.lease_epoch == 2
        dead = fail_job(
            store.conn,
            second,
            worker_id="worker-b",
            lease_epoch=2,
            error="boom again",
            now=NOW + timedelta(seconds=12),
        )
        assert dead.status == "dead"
        assert dead.completed_at is not None
        assert store.conn.execute("SELECT count(*) FROM jobs").fetchone()[0] == 1
    finally:
        store.close()


def test_recovery_marks_a_job_dead_when_the_attempt_ceiling_is_reached() -> None:
    store = _store()
    try:
        job = _job(store, max_attempts=1)
        claimed = claim_job(store.conn, "worker-a", lease_seconds=1, now=NOW)
        assert claimed is not None
        store.conn.execute(
            "UPDATE jobs SET lease_expires_at = ? WHERE id = ?",
            ((NOW - timedelta(seconds=1)).isoformat(), job.id),
        )
        assert recover_expired_leases(store.conn, now=NOW) == 1
        recovered = get_job(store.conn, job.id)
        assert recovered is not None
        assert recovered.status == "dead"
        assert recovered.last_error == "lease_expired"
        attempt = store.conn.execute(
            "SELECT status FROM stage_attempts WHERE job_id = ?", (job.id,)
        ).fetchone()
        assert attempt["status"] == "lease_lost"
    finally:
        store.close()


def test_priority_and_scheduled_time_choose_the_next_job() -> None:
    store = _store()
    try:
        _job(store, idempotency_key="low", priority=0)
        high = _job(store, idempotency_key="high", priority=10)
        future = _job(
            store,
            idempotency_key="future",
            priority=99,
            scheduled_for=(NOW + timedelta(seconds=60)).isoformat(),
        )
        first = claim_job(store.conn, "worker-a", now=NOW)
        assert first is not None
        assert first.id == high.id
        second = claim_job(store.conn, "worker-b", now=NOW)
        assert second is None or second.id != future.id
        later = claim_job(
            store.conn, "worker-c", now=NOW + timedelta(seconds=61)
        )
        assert later is not None
        assert later.id == future.id
    finally:
        store.close()


def test_a_succeeded_job_is_not_replayed_by_the_same_key() -> None:
    store = _store()
    try:
        job = _job(store)
        claimed = claim_job(store.conn, "worker-a", now=NOW)
        assert claimed is not None
        complete_job(
            store.conn,
            claimed,
            worker_id="worker-a",
            lease_epoch=1,
            result={"ok": True},
            now=NOW,
        )
        again = _job(store)
        assert again.id == job.id
        assert again.status == "succeeded"
        assert len(list_jobs(store.conn)) == 1
    finally:
        store.close()


def test_a_stale_worker_cannot_complete_or_fail() -> None:
    store = _store()
    try:
        _job(store)
        first = claim_job(store.conn, "worker-a", lease_seconds=1, now=NOW)
        assert first is not None
        store.conn.execute(
            "UPDATE jobs SET lease_expires_at = ? WHERE id = ?",
            ((NOW - timedelta(seconds=1)).isoformat(), first.id),
        )
        with pytest.raises(LeaseLost):
            complete_job(
                store.conn,
                first,
                worker_id="worker-a",
                lease_epoch=1,
                result={"too": "late"},
                now=NOW,
            )
        second = claim_job(store.conn, "worker-b", lease_seconds=60, now=NOW)
        assert second is not None
        with pytest.raises(LeaseLost):
            fail_job(
                store.conn,
                first,
                worker_id="worker-a",
                lease_epoch=1,
                error="stale",
                now=NOW,
            )
        with pytest.raises(LeaseLost):
            renew_lease(
                store.conn,
                first,
                worker_id="worker-a",
                lease_epoch=1,
                now=NOW,
            )
    finally:
        store.close()

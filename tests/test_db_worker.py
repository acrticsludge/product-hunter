from __future__ import annotations

from product_hunter.db import Store
from product_hunter.db.jobs import claim_job, enqueue_job, get_job
from product_hunter.db.worker import Worker


def _store() -> Store:
    return Store.open(":memory:")


def test_a_worker_runs_a_handler_and_writes_once() -> None:
    store = _store()
    try:
        store.conn.execute("CREATE TABLE test_writes (id INTEGER PRIMARY KEY, value TEXT)")
        job = enqueue_job(
            store.conn, stage="WRITE", idempotency_key="write-1", payload={"n": 1}
        )

        def handler(context):
            with context.transaction() as conn:
                conn.execute("INSERT INTO test_writes (value) VALUES (?)", ("ok",))
            return {"ok": True}

        worker = Worker(store.conn, "worker-1", {"WRITE": handler})
        outcome = worker.run_once()
        assert outcome is not None
        assert outcome.status == "succeeded"
        assert outcome.result == {"ok": True}
        assert store.conn.execute("SELECT count(*) FROM test_writes").fetchone()[0] == 1
        assert get_job(store.conn, job.id).status == "succeeded"
    finally:
        store.close()


def test_a_failing_handler_retries_then_dies() -> None:
    store = _store()
    try:
        job = enqueue_job(
            store.conn,
            stage="FAIL",
            idempotency_key="fail-1",
            max_attempts=2,
        )

        def handler(_context):
            raise RuntimeError("boom")

        worker = Worker(store.conn, "worker-1", {"FAIL": handler}, backoff_seconds=0)
        first = worker.run_once()
        assert first is not None and first.status == "failed"
        assert get_job(store.conn, job.id).status == "queued"
        second = worker.run_once()
        assert second is not None and second.status == "failed"
        final = get_job(store.conn, job.id)
        assert final.status == "dead"
        assert final.last_error is not None and "RuntimeError" in final.last_error
    finally:
        store.close()


def test_a_stolen_lease_makes_completion_fail_closed() -> None:
    store = _store()
    try:
        job = enqueue_job(
            store.conn, stage="STEAL", idempotency_key="steal-1"
        )

        def handler(context):
            context.conn.execute(
                "UPDATE jobs SET worker_id = 'thief', lease_epoch = lease_epoch + 1 "
                "WHERE id = ?",
                (context.job.id,),
            )
            return {"ok": True}

        worker = Worker(store.conn, "worker-1", {"STEAL": handler})
        outcome = worker.run_once()
        assert outcome is not None
        assert outcome.status == "lease_lost"
        current = get_job(store.conn, job.id)
        assert current is not None
        assert current.status == "leased"
        assert current.worker_id == "thief"
        assert current.lease_epoch == 2
    finally:
        store.close()


def test_recovery_after_an_expired_lease_writes_once() -> None:
    store = _store()
    try:
        store.conn.execute("CREATE TABLE test_writes (id INTEGER PRIMARY KEY, value TEXT)")
        job = enqueue_job(
            store.conn, stage="WRITE", idempotency_key="recover-1"
        )
        first = claim_job(store.conn, "worker-a", lease_seconds=1)
        assert first is not None
        store.conn.execute(
            "UPDATE jobs SET lease_expires_at = '2000-01-01T00:00:00+00:00' "
            "WHERE id = ?",
            (job.id,),
        )

        def handler(context):
            with context.transaction() as conn:
                conn.execute("INSERT INTO test_writes (value) VALUES (?)", ("recovered",))
            return {"ok": True}

        worker = Worker(store.conn, "worker-b", {"WRITE": handler})
        outcome = worker.run_once()
        assert outcome is not None and outcome.status == "succeeded"
        assert store.conn.execute("SELECT count(*) FROM test_writes").fetchone()[0] == 1
        current = get_job(store.conn, job.id)
        assert current is not None and current.status == "succeeded"
        attempts = store.conn.execute(
            "SELECT status FROM stage_attempts WHERE job_id = ? ORDER BY attempt",
            (job.id,),
        ).fetchall()
        assert [row["status"] for row in attempts] == ["lease_lost", "succeeded"]
    finally:
        store.close()


def test_a_missing_handler_is_recorded_as_a_failure() -> None:
    store = _store()
    try:
        job = enqueue_job(
            store.conn, stage="NO_HANDLER", idempotency_key="missing-1"
        )
        worker = Worker(store.conn, "worker-1", {})
        outcome = worker.run_once()
        assert outcome is not None and outcome.status == "failed"
        current = get_job(store.conn, job.id)
        assert current is not None
        assert current.status == "queued"
        assert current.last_error is not None
        assert "no handler" in current.last_error
    finally:
        store.close()

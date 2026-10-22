"""The crashes a worker has to come back from.

Every test names the invariant it holds, so a failure says which claim broke.
The crash is a child process that ends in ``os._exit(9)``, so nothing is
cleaned up on the way out and the store is left exactly as the dead process
left it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from conftest import crash_child, open_store
from product_hunter.autonomy.crash import recover, resume_point
from product_hunter.db.checkpoints import save_checkpoint
from product_hunter.db.jobs import LeaseLost, claim_job, complete_job, get_job, list_jobs
from product_hunter.db.outbox import enqueue_projection, list_projection_tasks
from product_hunter.fetch import FetchRecord
from product_hunter.runtime.lock import SingleInstanceLock

EXPIRED = "2000-01-01T00:00:00+00:00"


def _fetch_record() -> FetchRecord:
    """The capture a fetch leaves behind."""
    return FetchRecord(
        fetch_id="fetch-crash-1",
        source_id="hn_algolia",
        requested_url="https://hn.algolia.com/api/v1/search?query=invoice",
        final_url="https://hn.algolia.com/api/v1/search?query=invoice",
        status=200,
        content_type="application/json",
        raw_sha256="a" * 64,
        raw_path="data/raw/hn_algolia/a.json",
        bytes=128,
        fetched_at="2026-10-21T00:00:00+00:00",
        terms_status="permitted",
        allowed_use=["discovery", "retrieval", "analysis", "storage"],
        review_basis="fixture",
        user_agent="ProductHunter/test",
        latency_ms=5,
        retry_count=0,
    )


def _insert_source(store) -> None:
    store.conn.execute(
        "INSERT INTO sources (source_id, source_type, terms_status) "
        "VALUES ('hn_algolia', 'community', 'permitted')"
    )


def test_a_worker_killed_during_a_job_leaves_the_job_leased(tmp_path: Path) -> None:
    """The job of a worker that died mid-job is leased, and the recovery
    requeues it."""
    db_path = tmp_path / "ph.db"
    store = open_store(db_path)
    store.close()

    child = crash_child(
        db_path,
        """
        from product_hunter.db import Store
        from product_hunter.db.jobs import claim_job, enqueue_job
        store = Store.open(sys.argv[1])
        enqueue_job(store.conn, stage="WRITE", idempotency_key="crash-1")
        claim_job(store.conn, "worker-dead")
        os._exit(9)
        """,
    )
    assert child.returncode == 9, child.stderr

    store = open_store(db_path)
    try:
        job = list_jobs(store.conn)[0]
        assert job.status == "leased"
        assert job.worker_id == "worker-dead"

        # The lease of a dead worker expires, and the recovery requeues the
        # job rather than calling it done.
        store.conn.execute(
            "UPDATE jobs SET lease_expires_at = ? WHERE id = ?", (EXPIRED, job.id)
        )
        report = recover(store.conn)
        assert report.leases_recovered == 1
        assert report.interrupted_jobs == 1
        recovered = get_job(store.conn, job.id)
        assert recovered is not None
        assert recovered.status == "queued"
        assert recovered.last_error == "lease_expired"
    finally:
        store.close()


def test_a_worker_killed_after_a_fetch_does_not_fetch_the_same_url_twice(
    tmp_path: Path,
) -> None:
    """The capture of a worker that died after the fetch is in the store, so
    the retry does not fetch the same URL again."""
    db_path = tmp_path / "ph.db"
    store = open_store(db_path)
    _insert_source(store)
    store.close()

    child = crash_child(
        db_path,
        """
        from product_hunter.db import Store
        from product_hunter.fetch import FetchRecord
        store = Store.open(sys.argv[1])
        store.import_fetch(FetchRecord(
            fetch_id="fetch-crash-1",
            source_id="hn_algolia",
            requested_url="https://hn.algolia.com/api/v1/search?query=invoice",
            final_url="https://hn.algolia.com/api/v1/search?query=invoice",
            status=200,
            content_type="application/json",
            raw_sha256="a" * 64,
            raw_path="data/raw/hn_algolia/a.json",
            bytes=128,
            fetched_at="2026-10-21T00:00:00+00:00",
            terms_status="permitted",
            allowed_use=["discovery", "retrieval", "analysis", "storage"],
            review_basis="fixture",
            user_agent="ProductHunter/test",
            latency_ms=5,
            retry_count=0,
        ))
        os._exit(9)
        """,
    )
    assert child.returncode == 9, child.stderr

    store = open_store(db_path)
    try:
        # The retry fetches the same URL, so it lands on the same capture.
        # The store says the row is already there.
        assert store.import_fetch(_fetch_record()) is False
        count = store.conn.execute("SELECT count(*) FROM fetches").fetchone()[0]
        assert count == 1
    finally:
        store.close()


def test_a_worker_killed_during_a_transaction_leaves_no_partial_write(
    tmp_path: Path,
) -> None:
    """A write of a transaction that was open when the process died is gone."""
    db_path = tmp_path / "ph.db"
    store = open_store(db_path)
    store.conn.execute("CREATE TABLE crash_writes (id INTEGER PRIMARY KEY, value TEXT)")
    store.close()

    child = crash_child(
        db_path,
        """
        from product_hunter.db.connection import connect
        conn = connect(sys.argv[1])
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("INSERT INTO crash_writes (value) VALUES ('never')")
        os._exit(9)
        """,
    )
    assert child.returncode == 9, child.stderr

    store = open_store(db_path)
    try:
        count = store.conn.execute("SELECT count(*) FROM crash_writes").fetchone()[0]
        assert count == 0
        assert store.integrity_check() == "ok"
    finally:
        store.close()


def test_a_worker_killed_after_a_commit_leaves_the_job_done(tmp_path: Path) -> None:
    """A job whose commit landed before the process died stays done, and the
    recovery does not run it again."""
    db_path = tmp_path / "ph.db"
    store = open_store(db_path)
    store.close()

    child = crash_child(
        db_path,
        """
        from product_hunter.db import Store
        from product_hunter.db.jobs import claim_job, complete_job, enqueue_job
        store = Store.open(sys.argv[1])
        enqueue_job(store.conn, stage="WRITE", idempotency_key="crash-commit")
        claimed = claim_job(store.conn, "worker-dead")
        complete_job(
            store.conn,
            claimed,
            worker_id="worker-dead",
            lease_epoch=claimed.lease_epoch,
            result={"ok": True},
        )
        os._exit(9)
        """,
    )
    assert child.returncode == 9, child.stderr

    store = open_store(db_path)
    try:
        job = list_jobs(store.conn)[0]
        assert job.status == "succeeded"
        report = recover(store.conn)
        assert report.leases_recovered == 0
        assert report.requeued == 0
        assert get_job(store.conn, job.id).status == "succeeded"
    finally:
        store.close()


def test_a_worker_killed_before_the_projection_leaves_the_task_pending(
    tmp_path: Path,
) -> None:
    """A projection task of a worker that died before the projection is still
    pending, so the projection is built again."""
    db_path = tmp_path / "ph.db"
    store = open_store(db_path)
    store.close()

    child = crash_child(
        db_path,
        """
        from product_hunter.db import Store
        from product_hunter.db.outbox import enqueue_projection
        store = Store.open(sys.argv[1])
        enqueue_projection(
            store.conn,
            target="obsidian",
            entity_type="run",
            entity_id="run-crash",
            idempotency_key="projection:crash-1",
            payload={"run_id": "run-crash"},
        )
        os._exit(9)
        """,
    )
    assert child.returncode == 9, child.stderr

    store = open_store(db_path)
    try:
        tasks = list_projection_tasks(store.conn)
        assert len(tasks) == 1
        assert tasks[0].status == "pending"
        report = recover(store.conn)
        assert report.projections_recovered == 0
        assert list_projection_tasks(store.conn)[0].status == "pending"
    finally:
        store.close()


def test_an_expired_lease_is_requeued_and_its_attempt_is_lost(tmp_path: Path) -> None:
    """The attempt of an expired lease is recorded as lost, never as done."""
    db_path = tmp_path / "ph.db"
    store = open_store(db_path)
    try:
        from product_hunter.db.jobs import enqueue_job

        job = enqueue_job(store.conn, stage="WRITE", idempotency_key="expired-1")
        assert claim_job(store.conn, "worker-dead", lease_seconds=1) is not None
        store.conn.execute(
            "UPDATE jobs SET lease_expires_at = ? WHERE id = ?", (EXPIRED, job.id)
        )

        report = recover(store.conn)
        assert report.leases_recovered == 1
        recovered = get_job(store.conn, job.id)
        assert recovered is not None
        assert recovered.status == "queued"
        attempt = store.conn.execute(
            "SELECT status, error FROM stage_attempts WHERE job_id = ?", (job.id,)
        ).fetchone()
        assert attempt["status"] == "lease_lost"
        assert attempt["error"] == "lease_expired"
    finally:
        store.close()


def test_a_second_worker_is_refused_the_lock(tmp_path: Path) -> None:
    """A second process is refused the single instance lock while the first
    one holds it."""
    lock_file = tmp_path / "worker.lock"
    holder = SingleInstanceLock(lock_file)
    assert holder.acquire() is True
    try:
        child = crash_child(
            tmp_path / "ph.db",
            """
            from product_hunter.runtime.lock import SingleInstanceLock
            lock = SingleInstanceLock(sys.argv[2])
            print("acquired" if lock.acquire() else "refused", flush=True)
            os._exit(9)
            """,
            extra_args=[str(lock_file)],
        )
        assert child.returncode == 9, child.stderr
        assert "refused" in child.stdout
    finally:
        holder.release()


def test_a_stale_worker_cannot_complete_a_job_another_worker_took(
    tmp_path: Path,
) -> None:
    """A worker whose lease was taken over cannot write the job it lost."""
    db_path = tmp_path / "ph.db"
    store = open_store(db_path)
    store.close()

    child = crash_child(
        db_path,
        """
        from product_hunter.db import Store
        from product_hunter.db.jobs import claim_job, enqueue_job
        store = Store.open(sys.argv[1])
        enqueue_job(store.conn, stage="WRITE", idempotency_key="crash-stale")
        claim_job(store.conn, "worker-dead")
        os._exit(9)
        """,
    )
    assert child.returncode == 9, child.stderr

    store = open_store(db_path)
    try:
        job = list_jobs(store.conn)[0]
        assert job.status == "leased"
        store.conn.execute(
            "UPDATE jobs SET lease_expires_at = ? WHERE id = ?", (EXPIRED, job.id)
        )
        taken = claim_job(store.conn, "worker-fresh")
        assert taken is not None
        assert taken.lease_epoch == 2

        with pytest.raises(LeaseLost):
            complete_job(
                store.conn,
                job,
                worker_id="worker-dead",
                lease_epoch=job.lease_epoch,
                result={"stale": True},
            )
        current = get_job(store.conn, job.id)
        assert current is not None
        assert current.status == "leased"
        assert current.worker_id == "worker-fresh"
    finally:
        store.close()


def test_the_resume_point_is_the_newest_checkpoint_that_parses(tmp_path: Path) -> None:
    """The resume point skips a checkpoint that was edited outside the worker."""
    db_path = tmp_path / "ph.db"
    store = open_store(db_path)
    try:
        store.conn.execute(
            "INSERT INTO research_runs (run_id, public_id, objective, "
            "original_request) VALUES ('run-crash', 'run-crash', 'test', '')"
        )
        save_checkpoint(
            store.conn,
            run_id="run-crash",
            stage="DISCOVERY",
            state={"cursor": 1},
            checkpoint_version=1,
        )
        save_checkpoint(
            store.conn,
            run_id="run-crash",
            stage="DISCOVERY",
            state={"cursor": 2},
            checkpoint_version=2,
        )
        store.conn.execute(
            "UPDATE research_checkpoints SET state_json = ? "
            "WHERE run_id = ? AND checkpoint_version = 2",
            ("{not json", "run-crash"),
        )
        point = resume_point(store.conn, run_id="run-crash", stage="DISCOVERY")
        assert point is not None
        assert point.checkpoint_version == 1
        assert point.state == {"cursor": 1}
    finally:
        store.close()

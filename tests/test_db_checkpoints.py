from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from product_hunter.db import Store
from product_hunter.db.checkpoints import (
    CheckpointConflict,
    CheckpointError,
    list_checkpoints,
    load_checkpoint,
    save_checkpoint,
)
from product_hunter.db.jobs import (
    Job,
    claim_job,
    complete_job,
    enqueue_job,
)
from product_hunter.fetch import utcnow

NOW = utcnow()

LEASE_REFUSED = "the lease is not yours or has expired"
NO_JOB = "there is no job for this run and stage"
HALF_FENCE = "a fenced checkpoint needs both worker_id and lease_epoch"


def _store_with_run(run_id: str = "run-1") -> Store:
    store = Store.open(":memory:")
    store.conn.execute(
        """
        INSERT INTO research_runs (
            run_id, public_id, objective, original_request, status,
            created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (run_id, run_id, "test", "", "running", NOW.isoformat(), NOW.isoformat()),
    )
    return store


def _leased_job(
    store: Store,
    *,
    run_id: str = "run-1",
    stage: str = "DISCOVERY",
    worker_id: str = "worker-a",
    # leases.assert_live_lease reads the store's wall clock and cannot be
    # handed another one, so a one-minute lease dies if the suite takes a
    # minute to reach the test. The expired-lease cases pass their own length.
    lease_seconds: int = 3600,
    now: datetime = NOW,
) -> Job:
    """A job for a run and stage, leased to one worker."""
    enqueue_job(
        store.conn,
        stage=stage,
        idempotency_key=f"job:{run_id}:{stage}",
        run_id=run_id,
        now=now,
    )
    claimed = claim_job(store.conn, worker_id, lease_seconds=lease_seconds, now=now)
    assert claimed is not None
    return claimed


def test_save_and_load_a_checkpoint() -> None:
    store = _store_with_run()
    try:
        saved = save_checkpoint(
            store.conn,
            run_id="run-1",
            stage="DISCOVERY",
            step="query-1",
            state={"cursor": 3, "pending": ["a", "b"]},
            input_refs=["input.json"],
            output_refs=["out.json"],
            now=NOW,
        )
        loaded = load_checkpoint(
            store.conn, run_id="run-1", stage="DISCOVERY", step="query-1"
        )
        assert loaded is not None
        assert loaded.checkpoint_id == saved.checkpoint_id
        assert loaded.state == {"cursor": 3, "pending": ["a", "b"]}
        assert loaded.input_refs == ["input.json"]
        assert loaded.output_refs == ["out.json"]
        assert loaded.completed_at is not None
    finally:
        store.close()


def test_re_saving_the_same_state_is_idempotent() -> None:
    store = _store_with_run()
    try:
        first = save_checkpoint(
            store.conn, run_id="run-1", stage="DISCOVERY", state={"n": 1}, now=NOW
        )
        second = save_checkpoint(
            store.conn, run_id="run-1", stage="DISCOVERY", state={"n": 1}, now=NOW
        )
        assert first.checkpoint_id == second.checkpoint_id
        assert len(list_checkpoints(store.conn, run_id="run-1")) == 1
    finally:
        store.close()


def test_changed_state_under_the_same_version_is_refused() -> None:
    store = _store_with_run()
    try:
        save_checkpoint(
            store.conn, run_id="run-1", stage="DISCOVERY", state={"n": 1}, now=NOW
        )
        with pytest.raises(CheckpointConflict):
            save_checkpoint(
                store.conn,
                run_id="run-1",
                stage="DISCOVERY",
                state={"n": 2},
                now=NOW,
            )
    finally:
        store.close()


def test_a_new_version_becomes_the_latest_checkpoint() -> None:
    store = _store_with_run()
    try:
        save_checkpoint(
            store.conn,
            run_id="run-1",
            stage="DISCOVERY",
            state={"n": 1},
            checkpoint_version=1,
            now=NOW,
        )
        save_checkpoint(
            store.conn,
            run_id="run-1",
            stage="DISCOVERY",
            state={"n": 2},
            checkpoint_version=2,
            now=NOW,
        )
        latest = load_checkpoint(store.conn, run_id="run-1", stage="DISCOVERY")
        assert latest is not None
        assert latest.checkpoint_version == 2
        assert latest.state == {"n": 2}
        assert len(list_checkpoints(store.conn, run_id="run-1")) == 2
    finally:
        store.close()


def test_changed_refs_under_the_same_version_are_refused() -> None:
    store = _store_with_run()
    try:
        save_checkpoint(
            store.conn,
            run_id="run-1",
            stage="DISCOVERY",
            state={"n": 1},
            input_refs=["old-input"],
            now=NOW,
        )
        with pytest.raises(CheckpointConflict):
            save_checkpoint(
                store.conn,
                run_id="run-1",
                stage="DISCOVERY",
                state={"n": 1},
                input_refs=["new-input"],
                now=NOW,
            )
    finally:
        store.close()


def test_a_missing_run_is_refused() -> None:
    store = Store.open(":memory:")
    try:
        with pytest.raises(CheckpointError):
            save_checkpoint(
                store.conn, run_id="missing", stage="DISCOVERY", state={}
            )
    finally:
        store.close()


def test_an_invalid_version_is_refused() -> None:
    store = _store_with_run()
    try:
        with pytest.raises(CheckpointError):
            save_checkpoint(
                store.conn,
                run_id="run-1",
                stage="DISCOVERY",
                state={},
                checkpoint_version=0,
            )
    finally:
        store.close()


def test_an_unfenced_save_needs_no_job_and_stays_idempotent() -> None:
    store = _store_with_run()
    try:
        first = save_checkpoint(
            store.conn, run_id="run-1", stage="DISCOVERY", state={"n": 1}, now=NOW
        )
        second = save_checkpoint(
            store.conn, run_id="run-1", stage="DISCOVERY", state={"n": 1}, now=NOW
        )
        assert first.checkpoint_id == second.checkpoint_id
        assert len(list_checkpoints(store.conn, run_id="run-1")) == 1
        assert store.conn.execute("SELECT count(*) FROM jobs").fetchone()[0] == 0
    finally:
        store.close()


def test_a_checkpoint_with_only_a_worker_id_is_refused() -> None:
    store = _store_with_run()
    try:
        with pytest.raises(CheckpointError) as refused:
            save_checkpoint(
                store.conn,
                run_id="run-1",
                stage="DISCOVERY",
                state={},
                now=NOW,
                worker_id="worker-a",
            )
        assert str(refused.value) == HALF_FENCE
    finally:
        store.close()


def test_a_checkpoint_with_only_a_lease_epoch_is_refused() -> None:
    store = _store_with_run()
    try:
        with pytest.raises(CheckpointError) as refused:
            save_checkpoint(
                store.conn,
                run_id="run-1",
                stage="DISCOVERY",
                state={},
                now=NOW,
                lease_epoch=1,
            )
        assert str(refused.value) == HALF_FENCE
    finally:
        store.close()


def test_a_fenced_save_by_the_live_lease_holder_succeeds() -> None:
    store = _store_with_run()
    try:
        job = _leased_job(store)
        saved = save_checkpoint(
            store.conn,
            run_id="run-1",
            stage="DISCOVERY",
            state={"n": 1},
            now=NOW,
            worker_id="worker-a",
            lease_epoch=job.lease_epoch,
        )
        loaded = load_checkpoint(store.conn, run_id="run-1", stage="DISCOVERY")
        assert loaded is not None
        assert loaded.checkpoint_id == saved.checkpoint_id
        assert loaded.state == {"n": 1}
    finally:
        store.close()


def test_a_fenced_re_save_of_the_same_state_is_idempotent() -> None:
    store = _store_with_run()
    try:
        job = _leased_job(store)
        first = save_checkpoint(
            store.conn,
            run_id="run-1",
            stage="DISCOVERY",
            state={"n": 1},
            now=NOW,
            worker_id="worker-a",
            lease_epoch=job.lease_epoch,
        )
        second = save_checkpoint(
            store.conn,
            run_id="run-1",
            stage="DISCOVERY",
            state={"n": 1},
            now=NOW,
            worker_id="worker-a",
            lease_epoch=job.lease_epoch,
        )
        assert first.checkpoint_id == second.checkpoint_id
        assert len(list_checkpoints(store.conn, run_id="run-1")) == 1
    finally:
        store.close()


def test_a_fenced_save_by_another_worker_is_refused() -> None:
    store = _store_with_run()
    try:
        job = _leased_job(store, worker_id="worker-a")
        with pytest.raises(CheckpointError) as refused:
            save_checkpoint(
                store.conn,
                run_id="run-1",
                stage="DISCOVERY",
                state={"n": 1},
                now=NOW,
                worker_id="worker-b",
                lease_epoch=job.lease_epoch,
            )
        assert str(refused.value) == LEASE_REFUSED
        assert list_checkpoints(store.conn, run_id="run-1") == []
    finally:
        store.close()


def test_a_fenced_save_at_a_stale_epoch_is_refused() -> None:
    store = _store_with_run()
    try:
        job = _leased_job(store, worker_id="worker-a")
        with pytest.raises(CheckpointError) as refused:
            save_checkpoint(
                store.conn,
                run_id="run-1",
                stage="DISCOVERY",
                state={"n": 1},
                now=NOW,
                worker_id="worker-a",
                lease_epoch=job.lease_epoch + 1,
            )
        assert str(refused.value) == LEASE_REFUSED
        assert list_checkpoints(store.conn, run_id="run-1") == []
    finally:
        store.close()


def test_a_fenced_save_after_the_lease_expired_is_refused() -> None:
    store = _store_with_run()
    try:
        job = _leased_job(store, worker_id="worker-a", lease_seconds=1)
        # The lease expires in the row, not in the caller's clock: the fence
        # reads the store's clock, so the write is refused.
        store.conn.execute(
            "UPDATE jobs SET lease_expires_at = ? WHERE id = ?",
            ((NOW - timedelta(seconds=1)).isoformat(), job.id),
        )
        with pytest.raises(CheckpointError) as refused:
            save_checkpoint(
                store.conn,
                run_id="run-1",
                stage="DISCOVERY",
                state={"n": 1},
                now=NOW,
                worker_id="worker-a",
                lease_epoch=job.lease_epoch,
            )
        assert str(refused.value) == LEASE_REFUSED
        assert list_checkpoints(store.conn, run_id="run-1") == []
    finally:
        store.close()


def test_a_fenced_save_with_an_old_now_cannot_outlive_the_lease() -> None:
    store = _store_with_run()
    try:
        # The lease was taken an hour ago and lasted a minute, so it expired
        # long before this write. The caller still writes with the instant it
        # took the lease: the caller's clock says the lease is live, and only
        # the store's clock says it is not.
        taken_at = NOW - timedelta(hours=1)
        job = _leased_job(store, worker_id="worker-a", lease_seconds=60, now=taken_at)
        assert job.lease_expires_at is not None
        assert datetime.fromisoformat(job.lease_expires_at) < NOW
        with pytest.raises(CheckpointError) as refused:
            save_checkpoint(
                store.conn,
                run_id="run-1",
                stage="DISCOVERY",
                state={"n": 1},
                now=taken_at,
                worker_id="worker-a",
                lease_epoch=job.lease_epoch,
            )
        assert str(refused.value) == LEASE_REFUSED
        assert list_checkpoints(store.conn, run_id="run-1") == []
    finally:
        store.close()


def test_a_fenced_save_without_a_job_is_refused() -> None:
    store = _store_with_run()
    try:
        with pytest.raises(CheckpointError) as refused:
            save_checkpoint(
                store.conn,
                run_id="run-1",
                stage="DISCOVERY",
                state={"n": 1},
                now=NOW,
                worker_id="worker-a",
                lease_epoch=1,
            )
        assert str(refused.value) == NO_JOB
        assert list_checkpoints(store.conn, run_id="run-1") == []
    finally:
        store.close()


def test_a_fenced_save_after_the_lease_completed_is_refused() -> None:
    store = _store_with_run()
    try:
        job = _leased_job(store, worker_id="worker-a")
        complete_job(
            store.conn,
            job,
            worker_id="worker-a",
            lease_epoch=job.lease_epoch,
            result={"ok": True},
            now=NOW,
        )
        with pytest.raises(CheckpointError) as refused:
            save_checkpoint(
                store.conn,
                run_id="run-1",
                stage="DISCOVERY",
                state={"n": 1},
                now=NOW,
                worker_id="worker-a",
                lease_epoch=job.lease_epoch,
            )
        assert str(refused.value) == LEASE_REFUSED
        assert list_checkpoints(store.conn, run_id="run-1") == []
    finally:
        store.close()


def test_a_stale_worker_cannot_write_over_a_checkpoint_a_live_worker_wrote() -> None:
    store = _store_with_run()
    try:
        # The lease is judged against the store's clock, so the job is leased
        # with a lease that is live now and expired afterwards, by the row.
        live = utcnow()
        job = _leased_job(
            store, worker_id="worker-a", lease_seconds=60, now=live
        )
        save_checkpoint(
            store.conn,
            run_id="run-1",
            stage="DISCOVERY",
            state={"n": 1},
            now=live,
            worker_id="worker-a",
            lease_epoch=job.lease_epoch,
        )
        store.conn.execute(
            "UPDATE jobs SET lease_expires_at = ? WHERE id = ?",
            ((live - timedelta(seconds=1)).isoformat(), job.id),
        )
        second = claim_job(store.conn, "worker-b", lease_seconds=60, now=utcnow())
        assert second is not None
        assert second.lease_epoch == job.lease_epoch + 1

        with pytest.raises(CheckpointConflict):
            save_checkpoint(
                store.conn,
                run_id="run-1",
                stage="DISCOVERY",
                state={"n": 2},
                now=utcnow(),
                worker_id="worker-b",
                lease_epoch=second.lease_epoch,
            )

        with pytest.raises(CheckpointError) as refused:
            save_checkpoint(
                store.conn,
                run_id="run-1",
                stage="DISCOVERY",
                state={"n": 3},
                now=utcnow(),
                worker_id="worker-a",
                lease_epoch=job.lease_epoch,
            )
        assert str(refused.value) == LEASE_REFUSED
        loaded = load_checkpoint(store.conn, run_id="run-1", stage="DISCOVERY")
        assert loaded is not None
        assert loaded.state == {"n": 1}
    finally:
        store.close()


def test_the_refusal_messages_are_fixed_and_differ_from_each_other() -> None:
    store = _store_with_run()
    try:
        job = _leased_job(store, worker_id="worker-a")
        with pytest.raises(CheckpointError) as lease_error:
            save_checkpoint(
                store.conn,
                run_id="run-1",
                stage="DISCOVERY",
                state={},
                now=NOW,
                worker_id="worker-b",
                lease_epoch=job.lease_epoch,
            )
        with pytest.raises(CheckpointError) as no_job_error:
            save_checkpoint(
                store.conn,
                run_id="run-1",
                stage="PLAN",
                state={},
                now=NOW,
                worker_id="worker-a",
                lease_epoch=job.lease_epoch,
            )
        messages = (str(lease_error.value), str(no_job_error.value))
        assert messages[0] == LEASE_REFUSED
        assert messages[1] == NO_JOB
        assert messages[0] != messages[1]
        for message in messages:
            for value in (job.id, "worker-a", "worker-b", "run-1", "DISCOVERY"):
                assert value not in message
    finally:
        store.close()

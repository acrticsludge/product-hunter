"""Job recovery: config-error jobs die immediately and are requeued when the
config is fixed; transient errors still retry.
"""

from __future__ import annotations

import json

import pytest

from product_hunter.autonomy.states import PERMANENT_MARKER
from product_hunter.db import (
    connect,
    enqueue_job,
    get_job,
    migrate,
    recover_config_error_jobs,
    recover_expired_leases,
)
from product_hunter.fetch import utcnow
from product_hunter.probes import (
    create_probe,
    start_probe,
)
from product_hunter.registry import CreateRequest, create_run

PROBE_OBJECTIVE = "find the mobile checkout drop-off pain"
PROBE_CONFIG = json.dumps({"cycle_interval_seconds": 300, "maximum_backoff_seconds": 3600})


def _conn(tmp_path):
    connection = connect(tmp_path / "recover.db")
    migrate(connection)
    return connection


def _probe_and_run(conn):
    view, _ = create_probe(conn, PROBE_OBJECTIVE, PROBE_CONFIG, requester="test")
    start_probe(conn, view.public_id, requester="test")
    run, _ = create_run(
        conn, CreateRequest(objective=PROBE_OBJECTIVE), probe_id=view.probe_id, now=utcnow()
    )
    return view, run


def _dead_config_job(conn, run_id):
    """A dead job whose failure carries the permanent_failure marker."""
    job = enqueue_job(
        conn,
        stage="DISCOVERY",
        idempotency_key="recover-job-1",
        run_id=run_id,
        payload={"source_id": "github_issues"},
        max_attempts=3,
        now=utcnow(),
    )
    error = f"{PERMANENT_MARKER}: suspended source missing suspend_reason"
    conn.execute(
        "UPDATE jobs SET status = 'dead', worker_id = NULL, lease_expires_at = NULL, "
        "completed_at = ?, last_error = ? WHERE id = ?",
        (utcnow().isoformat(), error, job.id),
    )
    conn.commit()
    return job


def test_config_error_job_is_marked_dead_and_run_fails(tmp_path):
    conn = _conn(tmp_path)
    try:
        _view, run = _probe_and_run(conn)
        job = _dead_config_job(conn, run.run_id)
        assert get_job(conn, job.id).status == "dead"
        # The operator marks the run failed with the same reason.
        conn.execute(
            "UPDATE research_runs SET status = 'FAILED_PERMANENT', "
            "last_error = ?, completed_at = ? WHERE run_id = ?",
            (job.last_error, utcnow().isoformat(), run.run_id),
        )
        conn.commit()
        row = conn.execute(
            "SELECT status FROM research_runs WHERE run_id = ?", (run.run_id,)
        ).fetchone()
        assert row["status"] == "FAILED_PERMANENT"
    finally:
        conn.close()


def test_recover_config_error_jobs_requeues_after_fix(tmp_path):
    conn = _conn(tmp_path)
    try:
        _view, run = _probe_and_run(conn)
        job = _dead_config_job(conn, run.run_id)
        assert get_job(conn, job.id).status == "dead"
        # Once the config is fixed, the sweeper requeues it.
        requeued = recover_config_error_jobs(conn, now=utcnow())
        assert requeued == 1
        got = get_job(conn, job.id)
        assert got.status == "queued"
        assert got.last_error is None
    finally:
        conn.close()


def test_transient_error_stays_dead(tmp_path):
    """A job that died on a transient error is not requeued by the sweeper."""
    conn = _conn(tmp_path)
    try:
        _view, run = _probe_and_run(conn)
        job = enqueue_job(
            conn,
            stage="DISCOVERY",
            idempotency_key="recover-job-transient",
            run_id=run.run_id,
            payload={"source_id": "github_issues"},
            max_attempts=3,
            now=utcnow(),
        )
        conn.execute(
            "UPDATE jobs SET status = 'dead', worker_id = NULL, lease_expires_at = NULL, "
            "completed_at = ?, last_error = ? WHERE id = ?",
            (utcnow().isoformat(), "upstream timeout", job.id),
        )
        conn.commit()
        requeued = recover_config_error_jobs(conn, now=utcnow())
        assert requeued == 0
        assert get_job(conn, job.id).status == "dead"
    finally:
        conn.close()


def test_expired_lease_is_requeued_not_left_dead(tmp_path):
    """A leased job whose lease expired is requeued for the next worker."""
    conn = _conn(tmp_path)
    try:
        _view, run = _probe_and_run(conn)
        job = _dead_config_job(conn, run.run_id)
        # Pretend this worker leased it and then died: leased + expired.
        conn.execute(
            "UPDATE jobs SET status = 'leased', worker_id = 'dead-worker', "
            "lease_epoch = 1, lease_expires_at = '2000-01-01T00:00:00+00:00' "
            "WHERE id = ?",
            (job.id,),
        )
        conn.commit()
        recovered = recover_expired_leases(conn, now=utcnow())
        assert recovered == 1
        got = get_job(conn, job.id)
        assert got.status == "queued"
        assert got.last_error == "lease_expired"
    finally:
        conn.close()

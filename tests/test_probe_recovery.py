"""Probe crash/restart recovery.

A worker crashes while holding a lease (its heartbeat stops, the lease
expires). After restart, recovery requeues the expired lease, the probe
stays RUNNING with exactly one open cycle, and no duplicate cycle/run is
created. No manual DB repair is performed during the test.
"""

from __future__ import annotations

import sqlite3

import pytest

from product_hunter.db import connect, migrate, utc_now
from product_hunter.db.jobs import claim_job, enqueue_job, recover_expired_leases
from product_hunter.probes import (
    ProbeError,
    create_probe,
    record_probe_cycle,
    recover_probes,
    start_probe,
)
from product_hunter.registry import CreateRequest, create_run
from product_hunter.runtime.identity import WorkerIdentity
from product_hunter.autonomy.engine import OperationEngine
from product_hunter.config import load_autonomy_config


PROBE_OBJECTIVE = "recovery smoke: recurring deployment pain"


def _setup(conn):
    view, _ = create_probe(
        conn, PROBE_OBJECTIVE,
        '{"cycle_interval_seconds": 60, "maximum_backoff_seconds": 300}',
        requester="recovery-test",
    )
    start_probe(conn, view.public_id, requester="recovery-test")
    conn.execute(
        "UPDATE probes SET next_cycle_at = ? WHERE probe_id = ?",
        (utc_now().isoformat(), view.probe_id),
    )
    conn.commit()
    return view


def _engine_once(conn, probe_id):
    ident = WorkerIdentity.create()
    config = load_autonomy_config()
    engine = OperationEngine(conn, ident, {}, config=config)
    engine._tick_probes(utc_now())
    conn.commit()


def test_expired_lease_recovers_without_duplicate_cycle(tmp_path):
    db = tmp_path / "recovery.db"
    conn = connect(db)
    try:
        migrate(conn)
        view = _setup(conn)
        probe_id = view.probe_id

        # Tick 1: create the research run + DISCOVERY job.
        _engine_once(conn, probe_id)
        run_row = conn.execute(
            "SELECT run_id FROM research_runs WHERE probe_id = ? "
            "ORDER BY created_at DESC LIMIT 1", (probe_id,)
        ).fetchone()
        run_id = run_row["run_id"]
        enqueue_job(
            conn, stage="DISCOVERY",
            idempotency_key=f"probe:{probe_id}:{run_id}",
            run_id=run_id, priority=0,
            payload={"objective": PROBE_OBJECTIVE, "probe_id": probe_id},
            now=utc_now(),
        )
        conn.commit()

        # Tick 2: claim the job (worker leases it, epoch 1).
        claim_job(conn, "worker-crash-victim", now=utc_now())
        conn.commit()
        job = conn.execute(
            "SELECT id, status, lease_epoch, lease_expires_at, worker_id "
            "FROM jobs WHERE run_id = ?", (run_id,)
        ).fetchone()
        assert job["status"] == "leased"
        assert job["worker_id"] == "worker-crash-victim"
        assert job["lease_epoch"] == 1

        # Simulate the worker crashing: its lease expires and it never
        # renews. Set lease_expires_at to the past (no manual repair).
        conn.execute(
            "UPDATE jobs SET lease_expires_at = ? WHERE id = ?",
            ("2020-01-01T00:00:00+00:00", job["id"]),
        )
        conn.commit()

        # Restart: the engine's recovery pass runs (recover_expired_leases is
        # part of every engine tick via _run_recovery).
        recover_expired_leases(conn, now=utc_now())
        conn.commit()

        # Recovery requeued the expired lease.
        job2 = conn.execute(
            "SELECT status, worker_id, lease_expires_at, last_error, lease_epoch "
            "FROM jobs WHERE id = ?", (job["id"],)
        ).fetchone()
        assert job2["status"] == "queued"
        assert job2["worker_id"] is None
        assert job2["lease_expires_at"] is None
        assert job2["last_error"] == "lease_expired"

        # Probe remains RUNNING with exactly one open cycle.
        probe = conn.execute(
            "SELECT status, current_run_id FROM probes WHERE probe_id = ?",
            (probe_id,),
        ).fetchone()
        assert probe["status"] == "RUNNING"
        assert probe["current_run_id"] == run_id

        open_runs = conn.execute(
            "SELECT count(*) FROM research_runs "
            "WHERE probe_id = ? AND probe_cycle_closed_at IS NULL",
            (probe_id,),
        ).fetchone()[0]
        assert open_runs == 1

    finally:
        conn.close()


def test_recover_probes_resolves_pause_requested_without_live_lease(tmp_path):
    """PAUSE_REQUESTED probe whose worker is gone resolves to PAUSED,
    closing its interrupted cycle without counting it."""
    db = tmp_path / "recovery2.db"
    conn = connect(db)
    try:
        migrate(conn)
        view = _setup(conn)
        probe_id = view.probe_id

        # Create an open run for the probe and lease a job (live lease).
        _engine_once(conn, probe_id)
        run_row = conn.execute(
            "SELECT run_id FROM research_runs WHERE probe_id = ? "
            "ORDER BY created_at DESC LIMIT 1", (probe_id,)
        ).fetchone()
        run_id = run_row["run_id"]
        enqueue_job(
            conn, stage="DISCOVERY",
            idempotency_key=f"recover:{probe_id}:{run_id}",
            run_id=run_id, priority=0,
            payload={"objective": PROBE_OBJECTIVE, "probe_id": probe_id},
            now=utc_now(),
        )
        conn.commit()
        claim_job(conn, "worker-pause-victim", now=utc_now())
        conn.commit()

        # Operator requests pause while a live lease exists -> PAUSE_REQUESTED
        # (the cycle stays open because the worker still holds the lease).
        from product_hunter.probes import pause_probe
        pause_probe(conn, probe_id, requester="operator")
        conn.commit()
        probe = conn.execute(
            "SELECT status FROM probes WHERE probe_id = ?", (probe_id,)
        ).fetchone()
        assert probe["status"] == "PAUSE_REQUESTED"

        # The worker is gone: its lease expires (simulating a crash), so
        # recover_probes no longer sees a live lease.
        conn.execute(
            "UPDATE jobs SET lease_expires_at = ? WHERE run_id = ? AND status = 'leased'",
            ("2020-01-01T00:00:00+00:00", run_id),
        )
        conn.commit()

        # Recover: resolves to PAUSED and closes the interrupted cycle
        # without counting it.
        changed = recover_probes(conn)
        assert view.public_id in changed
        probe = conn.execute(
            "SELECT status, current_run_id FROM probes WHERE probe_id = ?",
            (probe_id,),
        ).fetchone()
        assert probe["status"] == "PAUSED"
        assert probe["current_run_id"] is None
        closed = conn.execute(
            "SELECT probe_cycle_closed_at FROM research_runs WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        assert closed["probe_cycle_closed_at"] is not None
        # No cycle counted.
        probe2 = conn.execute(
            "SELECT total_cycles FROM probes WHERE probe_id = ?", (probe_id,)
        ).fetchone()
        assert probe2["total_cycles"] == 0

    finally:
        conn.close()


def test_record_probe_cycle_rejects_invalid_outcome(tmp_path):
    db = tmp_path / "outcome.db"
    conn = connect(db)
    try:
        migrate(conn)
        view, _ = create_probe(
            conn, PROBE_OBJECTIVE, '{}', requester="recovery-test"
        )
        with pytest.raises(ProbeError):
            record_probe_cycle(conn, view.probe_id, "run_whatever", "weird")
    finally:
        conn.close()
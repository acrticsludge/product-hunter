"""The probe watchdog: a silent probe is named, not waited on.

Each test names the stall it asserts, so a failure says which observation
broke. The clock is a fixed one: the watchdog judges ages, not wall time.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from product_hunter.db import claim_job, connect, migrate
from product_hunter.probe_watchdog import (
    STALLED_EVENT,
    StallThresholds,
    audit_probes,
    last_stalls,
    report_stalls,
)
from product_hunter.probes import create_probe, start_probe
from product_hunter.registry import CreateRequest, create_run

NOW = datetime(2026, 10, 26, 12, 0, tzinfo=timezone.utc)


@pytest.fixture()
def conn(tmp_path):
    connection = connect(tmp_path / "watchdog.db")
    migrate(connection)
    try:
        yield connection
    finally:
        connection.close()


def _started_probe(conn):
    view, _ = create_probe(
        conn,
        "find the checkout drop-off pain",
        json.dumps({"cycle_interval_seconds": 60, "maximum_backoff_seconds": 300}),
        requester="test",
    )
    return start_probe(conn, view.public_id, requester="test")


def _run(conn, probe_id, *, heartbeat=NOW):
    run, _ = create_run(
        conn,
        CreateRequest(objective="probe cycle"),
        probe_id=probe_id,
        now=heartbeat,
    )
    conn.execute(
        "UPDATE research_runs SET last_heartbeat = ? WHERE run_id = ?",
        (heartbeat.isoformat(), run.run_id),
    )
    conn.commit()
    return run.run_id


def _leased_job(conn, run_id, *, lease_seconds=60):
    # create_run already enqueued the DISCOVERY job of this run.
    claimed = claim_job(conn, "worker-test", lease_seconds=lease_seconds, now=NOW)
    assert claimed is not None and claimed.run_id == run_id
    assert claimed.stage == "DISCOVERY"
    conn.commit()
    return claimed


def test_a_due_probe_with_no_run_is_reported_overdue(conn):
    view = _started_probe(conn)
    conn.execute(
        "UPDATE probes SET next_cycle_at = ? WHERE probe_id = ?",
        ((NOW - timedelta(seconds=400)).isoformat(), view.probe_id),
    )
    conn.commit()

    stalls = audit_probes(conn, now=NOW)

    assert [stall.kind for stall in stalls] == ["cycle_overdue"]
    assert stalls[0].seconds >= 400
    assert stalls[0].public_id == view.public_id


def test_a_run_with_no_worker_and_a_stale_heartbeat_is_stalled(conn):
    view = _started_probe(conn)
    run_id = _run(conn, view.probe_id, heartbeat=NOW - timedelta(seconds=1000))

    stalls = audit_probes(conn, now=NOW)

    assert [stall.kind for stall in stalls] == ["run_stalled"]
    assert stalls[0].seconds >= 1000
    assert run_id in stalls[0].detail


def test_a_healthy_run_is_not_a_stall(conn):
    view = _started_probe(conn)
    _run(conn, view.probe_id, heartbeat=NOW)

    assert audit_probes(conn, now=NOW) == []


def test_a_closed_cycle_is_not_a_stall(conn):
    """A recorded cycle leaves a QUEUED run behind; that run is finished work.

    Treating it as a live run would report a healthy probe as stalled as soon
    as its backoff outlasted the heartbeat threshold.
    """
    view = _started_probe(conn)
    run_id = _run(conn, view.probe_id, heartbeat=NOW - timedelta(seconds=1000))
    conn.execute(
        "UPDATE research_runs SET probe_cycle_closed_at = ? WHERE run_id = ?",
        (NOW.isoformat(), run_id),
    )
    conn.execute(
        "UPDATE probes SET next_cycle_at = ? WHERE probe_id = ?",
        ((NOW + timedelta(seconds=60)).isoformat(), view.probe_id),
    )
    conn.commit()

    assert audit_probes(conn, now=NOW) == []


def test_a_closed_cycle_that_is_overdue_is_reported_overdue(conn):
    view = _started_probe(conn)
    run_id = _run(conn, view.probe_id, heartbeat=NOW - timedelta(seconds=1000))
    conn.execute(
        "UPDATE research_runs SET probe_cycle_closed_at = ? WHERE run_id = ?",
        (NOW.isoformat(), run_id),
    )
    conn.execute(
        "UPDATE probes SET next_cycle_at = ? WHERE probe_id = ?",
        ((NOW - timedelta(seconds=400)).isoformat(), view.probe_id),
    )
    conn.commit()

    stalls = audit_probes(conn, now=NOW)

    assert [stall.kind for stall in stalls] == ["cycle_overdue"]


def test_an_expired_lease_is_reported_before_the_heartbeat(conn):
    view = _started_probe(conn)
    run_id = _run(conn, view.probe_id, heartbeat=NOW - timedelta(seconds=1000))
    _leased_job(conn, run_id)
    conn.execute(
        "UPDATE jobs SET lease_expires_at = ? WHERE run_id = ?",
        ((NOW - timedelta(seconds=30)).isoformat(), run_id),
    )
    conn.commit()

    stalls = audit_probes(conn, now=NOW)

    assert [stall.kind for stall in stalls] == ["lease_expired"]
    assert stalls[0].seconds >= 30


def test_a_lease_that_recovery_requeues_is_not_a_stall(conn):
    """Recovery runs before the watchdog, so a restart is not a stall.

    The lease of a crashed worker expires while the engine restarts; recovery
    requeues it in the same cycle. Reporting it would page an operator for a
    probe that is already moving again.
    """
    from product_hunter.db.jobs import recover_expired_leases

    view = _started_probe(conn)
    run_id = _run(conn, view.probe_id, heartbeat=NOW - timedelta(seconds=30))
    _leased_job(conn, run_id)
    conn.execute(
        "UPDATE jobs SET lease_expires_at = ? WHERE run_id = ?",
        ((NOW - timedelta(seconds=5)).isoformat(), run_id),
    )
    conn.commit()

    assert recover_expired_leases(conn, now=NOW) == 1
    assert audit_probes(conn, now=NOW) == []


def test_a_live_lease_that_never_advances_is_overtime(conn):
    view = _started_probe(conn)
    run_id = _run(conn, view.probe_id, heartbeat=NOW - timedelta(seconds=4000))
    _leased_job(conn, run_id, lease_seconds=7200)

    stalls = audit_probes(conn, now=NOW)

    assert [stall.kind for stall in stalls] == ["run_overtime"]
    assert "abnormally long" in stalls[0].detail


def test_a_paused_probe_is_not_audited(conn):
    view = _started_probe(conn)
    conn.execute(
        "UPDATE probes SET status = 'PAUSED', next_cycle_at = ? WHERE probe_id = ?",
        ((NOW - timedelta(seconds=4000)).isoformat(), view.probe_id),
    )
    conn.commit()

    assert audit_probes(conn, now=NOW) == []


def test_report_stalls_writes_one_event_per_window(conn):
    view = _started_probe(conn)
    conn.execute(
        "UPDATE probes SET next_cycle_at = ? WHERE probe_id = ?",
        ((NOW - timedelta(seconds=400)).isoformat(), view.probe_id),
    )
    conn.commit()

    first = report_stalls(conn, now=NOW)
    twice = report_stalls(conn, now=NOW + timedelta(seconds=10))
    later = report_stalls(conn, now=NOW + timedelta(seconds=1000))

    assert [stall.kind for stall in first] == ["cycle_overdue"]
    assert twice == []
    assert [stall.kind for stall in later] == ["cycle_overdue"]
    rows = conn.execute(
        "SELECT event_type, kind, detail_json FROM probe_events WHERE probe_id = ?",
        (view.probe_id,),
    ).fetchall()
    assert len(rows) == 2
    assert rows[0]["event_type"] == STALLED_EVENT
    assert json.loads(rows[0]["detail_json"])["kind"] == "cycle_overdue"


def test_last_stalls_returns_the_newest_first(conn):
    view = _started_probe(conn)
    conn.execute(
        "UPDATE probes SET next_cycle_at = ? WHERE probe_id = ?",
        ((NOW - timedelta(seconds=400)).isoformat(), view.probe_id),
    )
    conn.commit()
    report_stalls(conn, now=NOW)

    recorded = last_stalls(conn, view.probe_id)

    assert recorded[0]["kind"] == "cycle_overdue"
    assert recorded[0]["created_at"] == NOW.isoformat()


def test_custom_thresholds_shorten_the_patience(conn):
    view = _started_probe(conn)
    conn.execute(
        "UPDATE probes SET next_cycle_at = ? WHERE probe_id = ?",
        ((NOW - timedelta(seconds=30)).isoformat(), view.probe_id),
    )
    conn.commit()

    assert audit_probes(conn, now=NOW) == []
    tight = StallThresholds(cycle_overdue_grace_seconds=10)
    assert [s.kind for s in audit_probes(conn, thresholds=tight, now=NOW)] == [
        "cycle_overdue"
    ]

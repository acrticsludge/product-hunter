"""Probe circuit breaker: K consecutive failed/blocked cycles auto-pause the
probe and record paused_reason; a success resets the counter.
"""

from __future__ import annotations

import json

import pytest

from product_hunter.db import connect, migrate
from product_hunter.fetch import utcnow
from product_hunter.probes import (
    CIRCUIT_BREAKER_THRESHOLD,
    create_probe,
    record_probe_cycle,
    start_probe,
)

PROBE_OBJECTIVE = "find the mobile checkout drop-off pain"
PROBE_CONFIG = json.dumps({"cycle_interval_seconds": 300, "maximum_backoff_seconds": 3600})


def _conn(tmp_path):
    connection = connect(tmp_path / "circuit.db")
    migrate(connection)
    return connection


def _running_probe(conn):
    view, _ = create_probe(conn, PROBE_OBJECTIVE, PROBE_CONFIG, requester="test")
    return start_probe(conn, view.public_id, requester="test")


def _run_for_cycle(conn, probe_id):
    """Create an open cycle run for the probe so a cycle can be recorded."""
    from product_hunter.registry import CreateRequest, create_run

    run, _ = create_run(
        conn, CreateRequest(objective=PROBE_OBJECTIVE), probe_id=probe_id, now=utcnow()
    )
    return run.run_id


def test_consecutive_failures_auto_pause_at_threshold(conn):
    view = _running_probe(conn)
    assert view.status == "RUNNING"
    for _ in range(CIRCUIT_BREAKER_THRESHOLD):
        run_id = _run_for_cycle(conn, view.probe_id)
        view = record_probe_cycle(conn, view.probe_id, run_id, "failed")
    assert view.status == "PAUSED"
    assert view.paused_reason is not None
    assert "consecutive" in view.paused_reason
    assert view.consecutive_failures >= CIRCUIT_BREAKER_THRESHOLD


def test_blocked_cycles_also_trip_the_breaker(conn):
    view = _running_probe(conn)
    for _ in range(CIRCUIT_BREAKER_THRESHOLD):
        run_id = _run_for_cycle(conn, view.probe_id)
        view = record_probe_cycle(conn, view.probe_id, run_id, "blocked")
    assert view.status == "PAUSED"
    assert view.paused_reason is not None


def test_success_resets_the_consecutive_counter(conn):
    view = _running_probe(conn)
    # Two failures advance the counter but do not yet trip it.
    for _ in range(CIRCUIT_BREAKER_THRESHOLD - 1):
        run_id = _run_for_cycle(conn, view.probe_id)
        view = record_probe_cycle(conn, view.probe_id, run_id, "failed")
    assert view.consecutive_failures == CIRCUIT_BREAKER_THRESHOLD - 1
    assert view.status == "RUNNING"
    # A success resets the counter to zero.
    run_id = _run_for_cycle(conn, view.probe_id)
    view = record_probe_cycle(conn, view.probe_id, run_id, "success")
    assert view.consecutive_failures == 0
    assert view.status == "RUNNING"


def test_mixed_outcomes_count_only_failures_and_blocked(conn):
    view = _running_probe(conn)
    run_id = _run_for_cycle(conn, view.probe_id)
    view = record_probe_cycle(conn, view.probe_id, run_id, "success")
    assert view.consecutive_failures == 0
    run_id = _run_for_cycle(conn, view.probe_id)
    view = record_probe_cycle(conn, view.probe_id, run_id, "failed")
    assert view.consecutive_failures == 1
    assert view.status == "RUNNING"


@pytest.fixture
def conn(tmp_path):
    return _conn(tmp_path)

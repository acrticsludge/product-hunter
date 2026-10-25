"""Probe lifecycle, scheduling, accumulation, pause/restart and recovery.

Self contained on purpose: each test builds its own migrated database in a
temporary file, mirroring tests/test_registry.py.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

import pytest

from product_hunter.autonomy.engine import OperationEngine
from product_hunter.config import load_autonomy_config
from product_hunter.db import connect, enqueue_job, list_jobs, migrate
from product_hunter.fetch import utcnow
from product_hunter.probes import (
    InvalidTransition,
    UnknownProbe,
    create_probe,
    get_active_run,
    get_probe,
    list_probes,
    pause_probe,
    record_probe_cycle,
    recover_probes,
    resume_probe,
    start_probe,
    stop_probe,
)
from product_hunter.registry import CreateRequest, create_run
from product_hunter.runtime.identity import WorkerIdentity

PROBE_OBJECTIVE = "find the mobile checkout drop-off pain"
PROBE_CONFIG = json.dumps(
    {"cycle_interval_seconds": 300, "maximum_backoff_seconds": 3600}
)


@pytest.fixture
def conn(tmp_path):
    """A migrated database in a temporary file (schema version 15 incl. probes)."""
    connection = connect(tmp_path / "probe.db")
    migrate(connection)
    try:
        yield connection
    finally:
        connection.close()


def _started_probe(conn, objective=PROBE_OBJECTIVE, config=PROBE_CONFIG):
    view, _ = create_probe(conn, objective, config, requester="test")
    return start_probe(conn, view.public_id, requester="test")


def test_create_probe_returns_view_and_dedups_by_objective(conn):
    view, created = create_probe(conn, PROBE_OBJECTIVE, PROBE_CONFIG, requester="a")
    assert created is False
    assert view.status == "CREATED"
    assert view.public_id.startswith("PRB-")
    assert view.next_cycle_at is None
    view2, reused = create_probe(conn, PROBE_OBJECTIVE, PROBE_CONFIG, requester="b")
    assert reused is True
    assert view2.public_id == view.public_id


def test_get_and_list_probes(conn):
    create_probe(conn, "alpha objective", PROBE_CONFIG, requester="a")
    create_probe(conn, "beta objective", PROBE_CONFIG, requester="b")
    probes = list_probes(conn)
    assert len(probes) == 2
    assert get_probe(conn, probes[0].public_id).objective == probes[0].objective
    with pytest.raises(UnknownProbe):
        get_probe(conn, "PRB-99999999-0001")


def test_start_pause_resume_stop_transitions(conn):
    view = _started_probe(conn)
    assert view.status == "RUNNING"
    assert view.started_at is not None

    paused = pause_probe(conn, view.public_id, requester="a")
    assert paused.status == "PAUSED"
    assert paused.paused_at is not None

    resumed = resume_probe(conn, view.public_id, requester="a")
    assert resumed.status == "RUNNING"

    stopped = stop_probe(conn, view.public_id, requester="a")
    assert stopped.status == "STOPPED"
    assert stopped.stopped_at is not None


def test_start_refuses_running_or_stopped(conn):
    view = _started_probe(conn)
    with pytest.raises(InvalidTransition):
        start_probe(conn, view.public_id)
    stopped = stop_probe(conn, view.public_id)
    with pytest.raises(InvalidTransition):
        start_probe(conn, stopped.public_id)


def test_record_probe_cycle_counts_and_backoff(conn):
    view = _started_probe(conn)
    run_id = "run_synthetic"
    first = record_probe_cycle(conn, view.probe_id, run_id, "success")
    assert first.total_cycles == 1
    assert first.successful_cycles == 1
    assert first.backoff_multiplier == 1
    assert first.last_cycle_at is not None
    assert first.next_cycle_at is not None

    second = record_probe_cycle(conn, view.probe_id, run_id, "blocked")
    assert second.total_cycles == 2
    assert second.blocked_cycles == 1
    assert second.backoff_multiplier == 2
    # Backoff doubles the interval (300s -> ~600s); wall-clock drift is allowed.
    t1 = datetime.fromisoformat(first.next_cycle_at)
    t2 = datetime.fromisoformat(second.next_cycle_at)
    delta = (t2 - t1).total_seconds()
    assert 299 <= delta <= 301, delta


def test_record_probe_cycle_resets_backoff_on_success(conn):
    view = _started_probe(conn)
    run_id = "run_synthetic"
    record_probe_cycle(conn, view.probe_id, run_id, "blocked")
    assert get_probe(conn, view.public_id).backoff_multiplier == 2
    record_probe_cycle(conn, view.probe_id, run_id, "success")
    assert get_probe(conn, view.public_id).backoff_multiplier == 1


def test_probe_tick_starts_run_and_job(conn):
    view = _started_probe(conn)
    now = utcnow()
    conn.execute(
        "UPDATE probes SET next_cycle_at = ? WHERE probe_id = ?",
        (now.isoformat(), view.probe_id),
    )
    conn.commit()

    config = load_autonomy_config()
    ident = WorkerIdentity.create()
    engine = OperationEngine(conn, ident, {}, config=config)
    started = engine._tick_probes(now)

    assert started == 1
    runs = conn.execute(
        "SELECT run_id, public_id, probe_id, status FROM research_runs WHERE probe_id = ?",
        (view.probe_id,),
    ).fetchall()
    assert len(runs) == 1
    assert runs[0]["probe_id"] == view.probe_id
    assert runs[0]["status"] == "QUEUED"
    jobs = list_jobs(conn, status="queued")
    probe_jobs = [j for j in jobs if j.run_id == runs[0]["run_id"]]
    assert len(probe_jobs) == 1
    assert probe_jobs[0].stage == "DISCOVERY"
    payload = probe_jobs[0].payload
    assert payload["probe_id"] == view.probe_id
    probe = get_probe(conn, view.public_id)
    assert probe.current_run_id == runs[0]["run_id"]


def test_probe_tick_respects_one_active_run(conn):
    view = _started_probe(conn)
    now = utcnow()
    conn.execute(
        "UPDATE probes SET next_cycle_at = ? WHERE probe_id = ?",
        (now.isoformat(), view.probe_id),
    )
    conn.commit()
    # Create a live (QUEUED) run for the probe manually.
    run, _ = create_run(conn, CreateRequest(objective="holding run"), probe_id=view.probe_id, now=now)
    enqueue_job(
        conn,
        stage="DISCOVERY",
        idempotency_key=f"probe:{view.probe_id}:{run.run_id}",
        run_id=run.run_id,
        priority=0,
        payload={"objective": PROBE_OBJECTIVE, "probe_id": view.probe_id},
        now=now,
    )
    conn.commit()

    config = load_autonomy_config()
    ident = WorkerIdentity.create()
    engine = OperationEngine(conn, ident, {}, config=config)
    started = engine._tick_probes(now)
    assert started == 0  # active run blocks a new cycle


def test_paused_probe_starts_no_new_cycle(conn):
    view = _started_probe(conn)
    pause_probe(conn, view.public_id)  # genuinely PAUSED
    now = utcnow()
    conn.execute(
        "UPDATE probes SET next_cycle_at = ? WHERE probe_id = ?",
        (now.isoformat(), view.probe_id),
    )
    conn.commit()
    # tick on a paused probe must not start a run (status != RUNNING)
    config = load_autonomy_config()
    ident = WorkerIdentity.create()
    engine = OperationEngine(conn, ident, {}, config=config)
    started = engine._tick_probes(now)
    assert started == 0


def test_recover_probes_resolves_stale_requests(conn):
    view = _started_probe(conn)
    now = utcnow()
    conn.execute(
        "UPDATE probes SET status = 'PAUSE_REQUESTED' WHERE probe_id = ?",
        (view.probe_id,),
    )
    conn.commit()
    recovered = recover_probes(conn)
    assert view.public_id in recovered
    assert get_probe(conn, view.public_id).status == "PAUSED"


def test_concurrency_one_active_run_per_probe(conn):
    view = _started_probe(conn)
    now = utcnow()
    run, _ = create_run(conn, CreateRequest(objective="first"), probe_id=view.probe_id, now=now)
    enqueue_job(
        conn,
        stage="DISCOVERY",
        idempotency_key=f"probe:{view.probe_id}:{run.run_id}",
        run_id=run.run_id,
        priority=0,
        payload={"objective": PROBE_OBJECTIVE, "probe_id": view.probe_id},
        now=now,
    )
    conn.commit()
    # A second QUEUED run for the same probe is refused by the partial index.
    with pytest.raises(sqlite3.IntegrityError):
        run2, _ = create_run(
            conn, CreateRequest(objective="second"), probe_id=view.probe_id, now=now
        )
        enqueue_job(
            conn,
            stage="DISCOVERY",
            idempotency_key=f"probe:{view.probe_id}:{run2.run_id}",
            run_id=run2.run_id,
            priority=0,
            payload={"objective": PROBE_OBJECTIVE, "probe_id": view.probe_id},
            now=now,
        )
        conn.commit()


def test_probe_report_builds_with_active_run(conn):
    view = _started_probe(conn)
    now = utcnow()
    conn.execute(
        "UPDATE probes SET next_cycle_at = ? WHERE probe_id = ?",
        (now.isoformat(), view.probe_id),
    )
    conn.commit()
    config = load_autonomy_config()
    ident = WorkerIdentity.create()
    engine = OperationEngine(conn, ident, {}, config=config)
    engine._tick_probes(now)

    probed = get_probe(conn, view.public_id)
    assert probed.active_run is not None
    assert probed.active_run["current_stage"] == "PLAN"
    assert probed.active_run["job_stages"][0]["stage"] == "DISCOVERY"
    assert probed.active_run["tool_calls_spent"] == 0


def test_probe_full_e2e_flow(conn, monkeypatch, tmp_path):
    """End to end: create, start, cycle, pause, resume, report + Obsidian journal."""
    import os
    import product_hunter.cli as cli

    # Probe CLI commands read PH_DB_PATH; point them at this test's database.
    db_file = tmp_path / "e2e.db"
    monkeypatch.setenv("PH_DB_PATH", str(db_file))
    # Journal must not pollute the real vault: point it at a temp dir.
    journal_root = tmp_path / "vault"
    monkeypatch.setattr(cli, "_journal_root", lambda: journal_root)

    # Re-open on the PH_DB_PATH file so the CLI and this test share one store.
    from product_hunter.db import connect, migrate
    test_conn = connect(db_file)
    migrate(test_conn)
    try:
        view, _ = create_probe(test_conn, "e2e checkout pain", PROBE_CONFIG, requester="t")
        start_probe(test_conn, view.public_id, requester="t")
        assert get_probe(test_conn, view.public_id).status == "RUNNING"

        now = utcnow()
        test_conn.execute(
            "UPDATE probes SET next_cycle_at = ? WHERE probe_id = ?",
            (now.isoformat(), view.probe_id),
        )
        test_conn.commit()
        config = load_autonomy_config()
        ident = WorkerIdentity.create()
        engine = OperationEngine(test_conn, ident, {}, config=config)

        # First tick starts a run and its DISCOVERY job.
        assert engine._tick_probes(now) == 1
        run_row = test_conn.execute(
            "SELECT run_id, status FROM research_runs WHERE probe_id = ?",
            (view.probe_id,),
        ).fetchone()
        assert run_row is not None
        assert run_row["status"] == "QUEUED"

        # The worker finishes the run; record the completed cycle.
        test_conn.execute(
            "UPDATE research_runs SET status = 'COMPLETED' WHERE run_id = ?",
            (run_row["run_id"],),
        )
        test_conn.commit()
        record_probe_cycle(test_conn, view.probe_id, run_row["run_id"], "success")
        assert get_probe(test_conn, view.public_id).successful_cycles == 1

        # Advance the cadence and tick again: a second cycle starts.
        test_conn.execute(
            "UPDATE probes SET next_cycle_at = ? WHERE probe_id = ?",
            (utcnow().isoformat(), view.probe_id),
        )
        test_conn.commit()
        assert engine._tick_probes(utcnow()) == 1

        # Pause: no new cycle while paused.
        pause_probe(test_conn, view.public_id, requester="t")
        assert engine._tick_probes(utcnow()) == 0
        assert get_probe(test_conn, view.public_id).status == "PAUSED"
        # The paused run's jobs are cancelled; mark it terminal so a resume can
        # start a fresh cycle.
        test_conn.execute(
            "UPDATE research_runs SET status = 'COMPLETED' WHERE probe_id = ? "
            "AND status = 'QUEUED'",
            (view.probe_id,),
        )
        test_conn.commit()

        # Resume: a cycle starts again once the cadence is due.
        resume_probe(test_conn, view.public_id, requester="t")
        test_conn.execute(
            "UPDATE probes SET next_cycle_at = ? WHERE probe_id = ?",
            (utcnow().isoformat(), view.probe_id),
        )
        test_conn.commit()
        assert engine._tick_probes(utcnow()) == 1
        assert get_probe(test_conn, view.public_id).status == "RUNNING"

        # Report builds and writes the Obsidian journal note.
        assert cli.probe_report_command(public_id=view.public_id) == 0
        notes = list(journal_root.rglob("*.md"))
        assert len(notes) == 1
        assert notes[0].parent.parent.name == view.public_id

        # A second identical report writes nothing new (idempotent).
        assert cli.probe_report_command(public_id=view.public_id) == 0
        assert len(list(journal_root.rglob("*.md"))) == 1
    finally:
        test_conn.close()

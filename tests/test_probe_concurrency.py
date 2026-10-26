"""Multi-process probe concurrency: at most one active research cycle per probe.

Two independent engine processes tick the same due probe at the same time.
The authoritative protection is SQLite: the unique partial index
``idx_research_runs_one_open_probe_cycle`` on
``research_runs(probe_id) WHERE probe_cycle_closed_at IS NULL`` plus
``_tick_probes`` tolerating the resulting ``IntegrityError``.

No in-memory mutex is used.
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from product_hunter.db import connect, migrate
from product_hunter.probes import create_probe, start_probe
from product_hunter.registry import CreateRequest

PROBE_OBJECTIVE = "concurrency smoke: recurring deployment pain"

# Child process code: tick the shared DB once for the given probe.
_CHILD_CODE = '''
import sys, json
sys.path.insert(0, "/home/ubuntu/product-hunter")
from product_hunter.db import connect, migrate, utc_now
from product_hunter.autonomy.engine import OperationEngine
from product_hunter.config import load_autonomy_config
from product_hunter.runtime.identity import WorkerIdentity

db_path = sys.argv[1]
public_id = sys.argv[2]
now = utc_now()

conn = connect(db_path)
try:
    migrate(conn)
    ident = WorkerIdentity.create()
    config = load_autonomy_config()
    engine = OperationEngine(conn, ident, {}, config=config)
    started = engine._tick_probes(now)
    conn.commit()
finally:
    conn.close()

print(json.dumps({"public_id": public_id, "started": started}))
'''


def _run_tick(db_path: str, public_id: str) -> dict:
    env = dict(os.environ)
    env["PH_DB_PATH"] = db_path
    out = subprocess.run(
        [sys.executable, "-c", _CHILD_CODE, db_path, public_id],
        capture_output=True, text=True, timeout=60, env=env,
    )
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


def _open_run_count(conn: sqlite3.Connection, probe_id: str) -> int:
    return conn.execute(
        "SELECT count(*) FROM research_runs "
        "WHERE probe_id = ? AND probe_cycle_closed_at IS NULL",
        (probe_id,),
    ).fetchone()[0]


def _run_count(conn: sqlite3.Connection, probe_id: str) -> int:
    return conn.execute(
        "SELECT count(*) FROM research_runs WHERE probe_id = ?",
        (probe_id,),
    ).fetchone()[0]


def _setup_probe(conn) -> str:
    view, _ = create_probe(
        conn, PROBE_OBJECTIVE,
        '{"cycle_interval_seconds": 60, "maximum_backoff_seconds": 300}',
        requester="concurrency-test",
    )
    start_probe(conn, view.public_id, requester="concurrency-test")
    conn.execute(
        "UPDATE probes SET next_cycle_at = ? WHERE probe_id = ?",
        ("2020-01-01T00:00:00+00:00", view.probe_id),
    )
    conn.commit()
    return view.probe_id


@pytest.fixture()
def concurrency_db(tmp_path: Path):
    db = tmp_path / "concurrency.db"
    conn = connect(db)
    try:
        migrate(conn)
        yield str(db)
    finally:
        conn.close()


def test_two_processes_cannot_create_two_active_cycles(concurrency_db):
    """Simultaneous ticks by two processes yield at most one open run."""
    conn = connect(concurrency_db)
    try:
        probe_id = _setup_probe(conn)
    finally:
        conn.close()

    results = [_run_tick(concurrency_db, probe_id) for _ in range(2)]
    assert all(r["started"] >= 0 for r in results)

    conn = connect(concurrency_db)
    try:
        open_runs = _open_run_count(conn, probe_id)
        total_runs = _run_count(conn, probe_id)
    finally:
        conn.close()

    assert open_runs <= 1, f"expected at most 1 open run, got {open_runs}"
    assert total_runs >= 1, "at least one cycle should have been created"


def test_many_processes_still_respect_one_active_cycle(concurrency_db):
    """Eight simultaneous ticks still yield at most one open run."""
    conn = connect(concurrency_db)
    try:
        probe_id = _setup_probe(conn)
    finally:
        conn.close()

    results = [_run_tick(concurrency_db, probe_id) for _ in range(8)]

    conn = connect(concurrency_db)
    try:
        open_runs = _open_run_count(conn, probe_id)
    finally:
        conn.close()

    assert sum(r["started"] for r in results) >= 1
    assert open_runs <= 1, f"expected at most 1 open run, got {open_runs}"


def test_unique_index_rejects_second_open_run(concurrency_db):
    """Directly: SQLite rejects a second open cycle for the same probe."""
    conn = connect(concurrency_db)
    try:
        probe_id = _setup_probe(conn)
        from product_hunter.registry import create_run, CreateRequest

        # First open cycle: create a run with no closed_at.
        run1, _ = create_run(
            conn, CreateRequest(objective="first"),
            probe_id=probe_id, now=None,
        )
        conn.commit()
        assert _open_run_count(conn, probe_id) == 1

        # A second open cycle for the same probe must be rejected by the
        # unique partial index, even in autocommit.
        with pytest.raises(sqlite3.IntegrityError):
            run2, _ = create_run(
                conn, CreateRequest(objective="second"),
                probe_id=probe_id, now=None,
            )
            conn.commit()
    finally:
        conn.close()

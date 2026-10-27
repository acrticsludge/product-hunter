"""Unit tests for the TUI bridge subprocess.

Each test builds its own migrated database in a temporary file, mirroring
tests/test_probe_lifecycle.py.
"""

from __future__ import annotations

import hashlib
import json
import os

import pytest

from product_hunter.db import connect, migrate
from product_hunter.tui_bridge import build_snapshot, redact, run_command


@pytest.fixture
def conn(tmp_path):
    """A migrated database in a temporary file."""
    connection = connect(tmp_path / "tui.db")
    migrate(connection)
    try:
        yield connection
    finally:
        connection.close()


def test_snapshot_has_required_keys(conn):
    snapshot = build_snapshot(conn)
    assert "db_status" in snapshot
    assert "runs" in snapshot
    assert "probes" in snapshot
    assert "autonomy" in snapshot
    assert "sources" in snapshot
    assert "hash" in snapshot

    db_status = snapshot["db_status"]
    assert "schema_version" in db_status
    assert "tables" in db_status
    assert "jobs" in db_status
    assert "outbox_pending" in db_status
    assert "projection_tasks" in db_status

    jobs = db_status["jobs"]
    assert "queued" in jobs
    assert "running" in jobs
    assert "failed" in jobs
    assert "expired" in jobs

    assert isinstance(snapshot["runs"], list)
    assert isinstance(snapshot["probes"], list)


def test_snapshot_hash_stable(conn):
    first = build_snapshot(conn)
    second = build_snapshot(conn)
    assert first["hash"] == second["hash"]

    # The hash is sha256 of the canonical JSON of everything except ``hash``.
    payload = {k: v for k, v in first.items() if k != "hash"}
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    expected = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    assert first["hash"] == expected


def test_redact():
    os.environ["PH_TEST_API_KEY"] = "supersecret-value-123"
    os.environ["PH_NORMAL_VAR"] = "supersecret-value-123"
    try:
        out = redact("token=supersecret-value-123 and normal=supersecret-value-123")
        assert "supersecret-value-123" not in out
        assert out.count("***") == 2
    finally:
        del os.environ["PH_TEST_API_KEY"]
        del os.environ["PH_NORMAL_VAR"]


def test_cmd_execution():
    result = run_command("req-1", ["echo", "hi"])
    assert result["req_id"] == "req-1"
    assert result["ok"] is True
    assert result["stdout"].strip() == "hi"
    assert result["exit_code"] == 0


def test_cmd_execution_failure():
    result = run_command("req-2", ["false"])
    assert result["ok"] is False
    assert result["exit_code"] != 0

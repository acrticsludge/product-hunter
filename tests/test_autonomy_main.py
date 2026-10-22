"""The operation entry point and the shared transaction helper.

These tests cover the re-harden findings that touch the entry point
(clean refusal on a malformed config) and the single ``transaction`` copy
shared by engine, budget and evidence.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
import yaml

from product_hunter.autonomy import budget, engine as engine_module
from product_hunter.autonomy.__main__ import EXIT_ERROR, main
from product_hunter.config import load_autonomy_config
from product_hunter.db.connection import transaction
import product_hunter.evidence as evidence_module
from product_hunter.sources import SourceRegistry


def test_malformed_sources_yaml_is_refused_by_name(tmp_path: Path) -> None:
    """A malformed sources.yaml is refused as a ValueError naming the file."""
    path = tmp_path / "sources.yaml"
    path.write_text("sources: [\n  unclosed", encoding="utf-8")
    with pytest.raises(ValueError, match="sources"):
        SourceRegistry.from_yaml(path)


def test_malformed_autonomy_yaml_is_refused_by_name(tmp_path: Path) -> None:
    """A malformed autonomy.yaml is refused as a ValueError naming the file."""
    path = tmp_path / "autonomy.yaml"
    path.write_text("budget: [\n  unclosed", encoding="utf-8")
    with pytest.raises(ValueError, match="autonomy"):
        load_autonomy_config(path)


def test_the_entry_point_refuses_a_bad_config_cleanly(
    monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    """A malformed config makes ``main`` exit non-zero with JSON, no traceback."""

    def boom(*args, **kwargs):
        raise ValueError("the autonomy config at config/autonomy.yaml is not valid YAML")

    monkeypatch.setattr(
        "product_hunter.autonomy.__main__.load_autonomy_config", boom
    )

    code = main([])
    assert code == EXIT_ERROR
    err = capsys.readouterr().err
    assert "ok" in err and "false" in err
    assert "Traceback" not in err and "traceback" not in err


def test_engine_budget_and_evidence_share_one_transaction() -> None:
    """Engine, budget and evidence all use the single ``transaction`` from db.connection.

    Finding 3: the three copies diverged, so the helper now has one home and
    every module imports it from there. The cycle cannot form because
    db.connection sits below both engine and budget.
    """
    assert engine_module.transaction is transaction
    assert budget.transaction is transaction
    assert evidence_module.transaction is transaction


def test_a_joined_transaction_failure_rolls_back_only_the_helper() -> None:
    """The shared helper's savepoint branch never destroys the caller's writes."""
    store_conn = sqlite3.connect(":memory:", isolation_level=None)
    try:
        store_conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
        store_conn.execute("BEGIN IMMEDIATE")
        store_conn.execute("INSERT INTO t (v) VALUES ('caller')")
        try:
            with transaction(store_conn):
                store_conn.execute("INSERT INTO t (v) VALUES ('helper')")
                raise RuntimeError("helper blew up")
        except RuntimeError:
            pass
        rows = store_conn.execute("SELECT v FROM t ORDER BY id").fetchall()
        assert [row[0] for row in rows] == ["caller"]
        assert store_conn.in_transaction is True
        store_conn.execute("ROLLBACK")
    finally:
        store_conn.close()

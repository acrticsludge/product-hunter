"""The trace of one run: the four sources, the chain fields and the gaps."""

from __future__ import annotations

import json

import pytest
from test_observability_metrics import populated_store

from product_hunter.cli import main, observe_trace_command
from product_hunter.db import connect, migrate
from product_hunter.observability.trace import (
    MODEL_EXECUTION_GAP,
    STAGE_EXECUTION_GAP,
    collect_trace,
)


def test_the_four_kinds_are_read(tmp_path) -> None:
    conn = populated_store(tmp_path)
    report = collect_trace(conn, run_id="run-1")

    assert report.counts["tool_call"] == 2
    assert report.counts["model_execution"] == 2
    assert report.counts["stage_execution"] == 1
    assert report.counts["event"] == 4
    assert report.counts["total"] == 9
    assert {entry.kind for entry in report.entries} == {
        "tool_call",
        "model_execution",
        "stage_execution",
        "event",
    }


def test_the_chain_of_a_tool_call_is_filled(tmp_path) -> None:
    conn = populated_store(tmp_path)
    report = collect_trace(conn, run_id="run-1")

    entry = next(item for item in report.entries if item.kind == "tool_call")
    assert entry.run_id == "run-1"
    assert entry.stage == "PAIN_EXTRACTION"
    assert entry.agent == "extractor"
    assert entry.tool == "hn_search"
    assert entry.purpose == "find pains"
    assert entry.result_ref == "ref-1"
    assert entry.status == "success"
    assert entry.duration_ms == 120
    assert entry.attempt == 1
    assert entry.occurred_at == "2026-10-20T10:00:00+00:00"


def test_the_chain_of_a_model_execution_is_filled(tmp_path) -> None:
    conn = populated_store(tmp_path)
    report = collect_trace(conn, run_id="run-1")

    entry = next(item for item in report.entries if item.kind == "model_execution")
    assert entry.stage == "PAIN_EXTRACTION"
    assert entry.tool == "deepseek-v4.1-flash"
    assert entry.purpose == "extraction"
    assert entry.status == "ok"
    assert entry.duration_ms == 250
    assert entry.attempt is None


def test_the_chain_of_a_stage_execution_is_filled(tmp_path) -> None:
    conn = populated_store(tmp_path)
    report = collect_trace(conn, run_id="run-1")

    entry = next(item for item in report.entries if item.kind == "stage_execution")
    assert entry.stage == "PAIN_EXTRACTION"
    assert entry.tool == "PAIN_EXTRACTION"
    assert entry.purpose == "PAIN_EXTRACTION"
    assert entry.status == "succeeded"
    assert entry.attempt == 1


def test_the_chain_of_an_event_is_filled(tmp_path) -> None:
    conn = populated_store(tmp_path)
    report = collect_trace(conn, run_id="run-1")

    entry = next(
        item
        for item in report.entries
        if item.kind == "event" and item.purpose == "DISCOVERY_QUERY_EXECUTED"
    )
    assert entry.stage == "DISCOVERY"
    assert entry.agent == "discovery"
    assert entry.tool == "search"
    assert entry.result_ref == "dr-1"
    assert entry.status == "recorded"
    assert entry.duration_ms is None


def test_the_two_gaps_are_named(tmp_path) -> None:
    conn = populated_store(tmp_path)
    report = collect_trace(conn, run_id="run-1")

    assert MODEL_EXECUTION_GAP in report.gaps
    assert STAGE_EXECUTION_GAP in report.gaps


def test_the_order_is_stable(tmp_path) -> None:
    conn = populated_store(tmp_path)

    first = collect_trace(conn, run_id="run-1")
    second = collect_trace(conn, run_id="run-1")

    assert [entry.kind for entry in first.entries] == [
        "event",
        "model_execution",
        "stage_execution",
        "tool_call",
        "event",
        "event",
        "tool_call",
        "model_execution",
        "event",
    ]
    assert first.model_dump_json() == second.model_dump_json()


def test_a_stage_filter_keeps_the_entries_of_that_stage(tmp_path) -> None:
    conn = populated_store(tmp_path)
    report = collect_trace(conn, run_id="run-1", stage="PAIN_EXTRACTION")

    assert report.counts["total"] == 6
    assert report.counts["event"] == 1
    assert {entry.stage for entry in report.entries} == {"PAIN_EXTRACTION"}

    discovery = collect_trace(conn, run_id="run-1", stage="DISCOVERY")
    assert discovery.counts["total"] == 3
    assert discovery.counts["tool_call"] == 0


def test_a_run_without_entries_holds_no_chain(tmp_path) -> None:
    conn = populated_store(tmp_path)
    report = collect_trace(conn, run_id="run-missing")

    assert report.entries == []
    assert report.counts["total"] == 0
    assert MODEL_EXECUTION_GAP in report.gaps
    assert STAGE_EXECUTION_GAP in report.gaps


def test_a_run_id_that_looks_like_sql_is_bound_and_not_run(tmp_path) -> None:
    conn = populated_store(tmp_path)

    report = collect_trace(conn, run_id="'; DROP TABLE tool_calls; --")

    assert report.entries == []
    assert conn.execute("SELECT count(*) FROM tool_calls").fetchone()[0] == 2


def test_the_read_side_never_writes(tmp_path) -> None:
    conn = populated_store(tmp_path)
    before = conn.execute("SELECT count(*) FROM research_events").fetchone()[0]

    collect_trace(conn, run_id="run-1")

    after = conn.execute("SELECT count(*) FROM research_events").fetchone()[0]
    assert after == before
    assert not conn.in_transaction


def test_the_command_prints_the_chain_as_json(tmp_path, capsys) -> None:
    db_path = tmp_path / "product_hunter.db"
    conn = connect(db_path)
    migrate(conn)
    conn.close()

    assert (
        observe_trace_command(
            run_id="run-1",
            db_path=db_path,
            out=tmp_path / "trace.md",
            repo_root=tmp_path,
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["run_id"] == "run-1"
    assert payload["counts"]["total"] == 0
    assert (tmp_path / "trace.md").is_file()


def test_the_trace_command_needs_a_run() -> None:
    with pytest.raises(SystemExit):
        main(["observe", "trace"])

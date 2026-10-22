"""The markdown rendering of the observability reports."""

from __future__ import annotations

import json

from test_observability_metrics import populated_store

from product_hunter.db import connect, migrate
from product_hunter.observability.metrics import collect_metrics
from product_hunter.observability.report import (
    NOT_MEASURED,
    render_metrics_markdown,
    render_trace_markdown,
    write_metrics_report,
    write_trace_report,
)
from product_hunter.observability.trace import collect_trace


def test_a_measured_metric_prints_its_value(tmp_path) -> None:
    conn = populated_store(tmp_path)
    report = collect_metrics(conn, run_id="run-1")

    text = render_metrics_markdown(report)

    assert "| `tool_call.total` | 2 | count | run |  |" in text
    assert "| `tool_call.failure_rate` | 0.500 | rate | run |  |" in text
    assert "| `run.status` | COMPLETED | text | run |  |" in text


def test_an_unmeasured_metric_prints_not_yet_measured_and_its_reason(
    tmp_path,
) -> None:
    conn = connect(":memory:")
    migrate(conn)
    report = collect_metrics(conn)

    text = render_metrics_markdown(report)

    assert NOT_MEASURED in text
    assert (
        "| `tool_call.failure_rate` | NOT YET MEASURED | rate | database | "
        "no denominator |" in text
    )
    assert "- `tool_call.failure_rate`: no denominator" in text
    assert "- `run.status`: no run was selected" in text


def test_the_report_names_every_family_and_what_it_could_not_measure(
    tmp_path,
) -> None:
    conn = populated_store(tmp_path)
    report = collect_metrics(conn, run_id="run-1")

    text = render_metrics_markdown(report)

    for group in report.groups:
        assert f"## {group}" in text
    assert "## What was not measured" in text
    assert "## The counts behind the numbers" in text
    # The comparison families hold nothing, and the report says so.
    assert "- `regression.last_verdict`: no comparison has been recorded" in text


def test_the_trace_report_names_the_gaps(tmp_path) -> None:
    conn = populated_store(tmp_path)
    report = collect_trace(conn, run_id="run-1")

    text = render_trace_markdown(report)

    assert "# Trace report" in text
    assert "| tool_call | 2 |" in text
    assert "- a model execution carries no agent and no result reference" in text
    assert "- a stage execution carries no result reference" in text
    assert "extractor" in text


def test_the_two_reads_produce_the_same_json(tmp_path) -> None:
    conn = populated_store(tmp_path)

    first = collect_metrics(conn, run_id="run-1")
    second = collect_metrics(conn, run_id="run-1")

    assert first.model_dump_json() == second.model_dump_json()
    assert json.dumps(first.model_dump(mode="json")) == json.dumps(
        second.model_dump(mode="json")
    )


def test_the_reports_are_written_to_a_file(tmp_path) -> None:
    conn = populated_store(tmp_path)
    metrics = collect_metrics(conn, run_id="run-1")
    trace = collect_trace(conn, run_id="run-1")

    target = tmp_path / "reports" / "metrics.md"
    write_metrics_report(metrics, target)
    trace_target = tmp_path / "reports" / "trace.md"
    write_trace_report(trace, trace_target)

    assert target.is_file()
    assert "NOT YET MEASURED" in target.read_text(encoding="utf-8")
    assert trace_target.is_file()
    assert "Trace report" in trace_target.read_text(encoding="utf-8")


def test_a_report_never_prints_a_zero_for_a_metric_without_a_denominator(
    tmp_path,
) -> None:
    conn = connect(":memory:")
    migrate(conn)
    report = collect_metrics(conn)

    text = render_metrics_markdown(report)

    for line in text.splitlines():
        if not line.startswith("| `") or "rate" not in line:
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if cells[1] == NOT_MEASURED:
            assert cells[0].startswith("`")
        else:
            assert cells[1] != "0.000000"

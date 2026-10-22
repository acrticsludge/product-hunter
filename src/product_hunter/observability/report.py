"""The markdown rendering of the observability reports.

This is the only module of the package that produces text. Every other
module produces data, and the data is what the text is made of: a metric
that could not be measured is shown as ``NOT YET MEASURED`` with the reason
beside it, so a reader never has to guess whether a missing number is a zero.

A report names what it measured, what it could not measure, and why. The
counts behind a number travel with it, so a reader can check the arithmetic
without running the query again.
"""

from __future__ import annotations

from pathlib import Path

from product_hunter.observability.metrics import Metric, MetricReport, MetricStatus
from product_hunter.observability.trace import TraceReport

# What a metric that could not be measured prints as.
NOT_MEASURED = "NOT YET MEASURED"


def render_metrics_markdown(report: MetricReport) -> str:
    """Render the metric report as markdown."""
    lines: list[str] = []
    lines.append("# Metrics report")
    lines.append("")
    lines.append(
        f"The report was read at schema version {report.schema_version}, "
        f"with the measurement contract {report.observability_version}."
    )
    lines.append("")
    if report.run_id is None:
        lines.append(
            "No run was named, so the run-scoped families were read over the "
            "whole store."
        )
    else:
        lines.append(f"The report covers the run {report.run_id}.")
    lines.append("")
    for note in report.notes:
        lines.append(note)
        lines.append("")
    lines.extend(_group_sections(report))
    lines.extend(_not_measured(report))
    lines.extend(_counts_behind(report))
    lines.append("")
    return "\n".join(lines)


def write_metrics_report(report: MetricReport, path: Path) -> None:
    """Write the metric report, creating the parent directories."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        render_metrics_markdown(report), encoding="utf-8", newline="\n"
    )


def render_trace_markdown(report: TraceReport) -> str:
    """Render the trace report as markdown."""
    lines: list[str] = []
    lines.append("# Trace report")
    lines.append("")
    lines.append(
        f"The trace of the run {report.run_id} holds {report.counts.get('total', 0)} "
        f"entries, read with the contract {report.observability_version}."
    )
    lines.append("")
    lines.append("## Counts")
    lines.append("")
    lines.append("| Kind | Entries |")
    lines.append("| --- | --- |")
    for kind, count in report.counts.items():
        lines.append(f"| {kind} | {count} |")
    lines.append("")
    lines.append("## The chain")
    lines.append("")
    if not report.entries:
        lines.append("The run left no entry behind in the four tables.")
        lines.append("")
    else:
        lines.append(
            "| Moment | Kind | Stage | Agent | Tool | Purpose | Result | "
            "Status | Duration (ms) | Attempt |"
        )
        lines.append("| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |")
        for entry in report.entries:
            lines.append(
                f"| {_moment(entry.occurred_at)} | {entry.kind} | "
                f"{_text(entry.stage)} | {_text(entry.agent)} | {entry.tool} | "
                f"{_text(entry.purpose)} | {_text(entry.result_ref)} | "
                f"{entry.status} | {_number(entry.duration_ms)} | "
                f"{_number(entry.attempt)} |"
            )
        lines.append("")
    lines.append("## Gaps")
    lines.append("")
    if report.gaps:
        for gap in report.gaps:
            lines.append(f"- {gap}")
    else:
        lines.append("The chain holds every link.")
    lines.append("")
    return "\n".join(lines)


def write_trace_report(report: TraceReport, path: Path) -> None:
    """Write the trace report, creating the parent directories."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_trace_markdown(report), encoding="utf-8", newline="\n")


def _group_sections(report: MetricReport) -> list[str]:
    """One section per family, in the order the report lists them."""
    lines: list[str] = []
    for group in report.groups:
        metrics = [metric for metric in report.metrics if metric.group == group]
        lines.append(f"## {group}")
        lines.append("")
        if not metrics:
            lines.append("This family was not read.")
            lines.append("")
            continue
        lines.append("| Metric | Value | Unit | Scope | Note |")
        lines.append("| --- | --- | --- | --- | --- |")
        for metric in metrics:
            lines.append(
                f"| `{metric.name}` | {_value(metric)} | {metric.unit} | "
                f"{metric.scope} | {_note(metric)} |"
            )
        lines.append("")
    return lines


def _not_measured(report: MetricReport) -> list[str]:
    """The numbers that could not be measured, and why."""
    lines = ["## What was not measured", ""]
    missing = [
        metric
        for metric in report.metrics
        if metric.status is not MetricStatus.measured
    ]
    if not missing:
        lines.append("Every metric of this report was measured.")
        lines.append("")
        return lines
    for metric in missing:
        lines.append(f"- `{metric.name}`: {metric.reason or 'the reason was not given'}")
    lines.append("")
    return lines


def _counts_behind(report: MetricReport) -> list[str]:
    """The counts each number was built from."""
    lines = ["## The counts behind the numbers", ""]
    written = False
    for metric in report.metrics:
        if metric.status is not MetricStatus.measured or not metric.detail:
            continue
        parts = [
            f"{key} {metric.detail[key]}" for key in sorted(metric.detail, key=str)
        ]
        if not parts:
            continue
        written = True
        lines.append(f"- `{metric.name}`: {', '.join(parts)}")
    if not written:
        lines.append("No number of this report was built from a count.")
    lines.append("")
    return lines


def _value(metric: Metric) -> str:
    """What a metric prints as its value."""
    if metric.status is not MetricStatus.measured:
        return NOT_MEASURED
    if metric.unit == "text":
        return str(metric.detail.get("text", ""))
    return _number(metric.value)


def _note(metric: Metric) -> str:
    """The note that follows a value."""
    if metric.status is MetricStatus.measured:
        return ""
    return metric.reason or "the reason was not given"


def _text(value: str | None) -> str:
    """A text field, or a dash when the row left it empty."""
    return "-" if value is None or value == "" else str(value)


def _number(value: float | None) -> str:
    """A number as it is printed."""
    if value is None:
        return "-"
    if isinstance(value, int):
        return str(value)
    return f"{float(value):.3f}"


def _moment(value: str) -> str:
    """A moment as it is printed."""
    return value if value else "-"

"""The benchmark report and the label status rendering.

This is the only module that produces text. Every other module produces data.
A metric with no human label is shown as ``n/a`` and marked ``blocked``, so a
report that measured quote fidelity alone never reads like a quality
benchmark.
"""

from __future__ import annotations

from pathlib import Path

from product_hunter.eval.cases import LabelStatus
from product_hunter.eval.run import (
    ComparisonReport,
    EvalRun,
    TaskResult,
    ThresholdCheck,
    task_value_rules,
)


def render_benchmark_markdown(
    run: EvalRun, comparison: ComparisonReport | None = None
) -> str:
    """Render the benchmark report as markdown."""
    lines: list[str] = []
    lines.append("# Benchmark report")
    lines.append("")
    lines.extend(_identity(run))
    lines.append("")
    lines.append("## Tasks")
    lines.append("")
    lines.append(
        "The task value of a metric is built from its case values. Pooled means "
        "the metric is computed over every case of the task together, mean and "
        "sum are over the case values."
    )
    lines.append("")
    if not run.tasks:
        lines.append("The dataset holds no case, so nothing was measured.")
        lines.append("")
    for task in run.tasks:
        lines.extend(_task(task, run))
    lines.extend(_not_measured(run))
    if comparison is not None:
        lines.extend(_comparison(comparison))
    lines.append("")
    return "\n".join(lines)


def render_label_status(status: LabelStatus) -> str:
    """Render the label status of a dataset as markdown."""
    lines: list[str] = []
    lines.append("# Label status")
    lines.append("")
    lines.append(
        f"The dataset {status.dataset_version} holds {status.total_cases} cases, "
        f"of which {status.total_labeled} carry a human label. A task with no "
        "labelled case has no human answer to score against, so its quality "
        "metrics stay empty. quote_fidelity is checked against the source text "
        "and needs no label."
    )
    lines.append("")
    lines.append("| Task | Cases | Labelled | Unlabelled | Blocked |")
    lines.append("| --- | --- | --- | --- | --- |")
    for task in status.tasks:
        lines.append(
            f"| {task.task.value} | {task.cases} | {task.labeled} | "
            f"{task.unlabeled} | {'yes' if task.blocked else 'no'} |"
        )
    lines.append("")
    return "\n".join(lines)


def write_benchmark_report(
    run: EvalRun, path: Path, comparison: ComparisonReport | None = None
) -> None:
    """Write the markdown report, creating the parent directories."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        render_benchmark_markdown(run, comparison), encoding="utf-8", newline="\n"
    )


def _identity(run: EvalRun) -> list[str]:
    lines = ["| Field | Value |", "| --- | --- |"]
    for name, value in (
        ("Run", run.run_id),
        ("Provider", run.provider),
        ("Model", run.model),
        ("Model version", run.model_version),
        ("Model catalog", run.model_catalog),
        ("Prompt version", run.prompt_version),
        ("Evaluator version", run.evaluator_version),
        ("Dataset version", run.dataset_version),
        ("Verdict", _verdict(run.passed)),
    ):
        lines.append(f"| {name} | {_text(value)} |")
    return lines


def _task(task: TaskResult, run: EvalRun) -> list[str]:
    lines = [f"### {task.task.value}", ""]
    if task.blocked:
        lines.append(f"Blocked: {task.blocked_reason}.")
        lines.append("")
    lines.append(
        f"Cases: {task.cases}. Scored: {task.scored}. No label: {task.unlabeled}. "
        f"Errors: {task.errored}. Prompt version: {_text(task.prompt_version)}."
    )
    lines.append("")
    lines.append("| Metric | Value | Task value | Threshold |")
    lines.append("| --- | --- | --- | --- |")
    checks = {
        check.metric: check
        for check in run.thresholds.checks
        if check.task == task.task.value
    }
    rules = task_value_rules(task.case_results)
    for name, value in task.metrics.items():
        lines.append(
            f"| {name} | {_number(value)} | {rules.get(name, 'sum')} | "
            f"{_status(checks.get(name))} |"
        )
    lines.append("")
    return lines


def _not_measured(run: EvalRun) -> list[str]:
    blocked = [task.task.value for task in run.tasks if task.blocked]
    unscored = [
        task.task.value for task in run.tasks if not task.blocked and task.scored == 0
    ]
    partly = [
        f"{task.task.value} ({task.scored} of {task.cases} cases scored)"
        for task in run.tasks
        if 0 < task.scored < task.cases
    ]
    measured = [
        task.task.value
        for task in run.tasks
        if task.scored == task.cases and task.cases
    ]
    lines = ["## What was not measured", ""]
    if blocked:
        lines.append(
            "These tasks carried no human label, so their quality metrics are "
            f"empty and no number was measured for them: {', '.join(blocked)}."
        )
    for name in unscored:
        lines.append(f"For {name} no case of this task could be scored.")
    if partly:
        lines.append(f"Only part of these tasks was measured: {', '.join(partly)}.")
    if not blocked and not unscored and not partly:
        lines.append("Every task of the dataset was measured.")
    if measured:
        lines.append("")
        lines.append(f"Measured: {', '.join(measured)}.")
    lines.append("")
    return lines


def _comparison(comparison: ComparisonReport) -> list[str]:
    lines = ["## Comparison", ""]
    lines.append(
        f"Baseline {comparison.baseline_run_id} "
        f"({comparison.baseline_provider}/{comparison.baseline_model}) against "
        f"candidate {comparison.candidate_run_id} "
        f"({comparison.candidate_provider}/{comparison.candidate_model})."
    )
    lines.append("")
    lines.append(f"Verdict: {comparison.verdict}.")
    lines.append("")
    if comparison.metric_deltas:
        lines.append("| Task | Metric | Baseline | Candidate | Delta | Status |")
        lines.append("| --- | --- | --- | --- | --- | --- |")
        for delta in comparison.metric_deltas:
            lines.append(
                f"| {delta.task} | {delta.metric} | {_number(delta.baseline)} | "
                f"{_number(delta.candidate)} | {_number(delta.delta)} | "
                f"{delta.status} |"
            )
        lines.append("")
    if comparison.case_flips:
        lines.append("| Task | Case | Baseline | Candidate | Change |")
        lines.append("| --- | --- | --- | --- | --- |")
        for flip in comparison.case_flips:
            lines.append(
                f"| {flip.task} | {flip.case_id} | {_verdict(flip.baseline)} | "
                f"{_verdict(flip.candidate)} | {flip.kind} |"
            )
        lines.append("")
    for note in comparison.notes:
        lines.append(note)
        lines.append("")
    return lines


def _verdict(value: bool | None) -> str:
    if value is None:
        return "not measured"
    return "pass" if value else "fail"


def _text(value: str | None) -> str:
    return "n/a" if value is None else str(value)


def _number(value: float | int | None) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, int):
        return str(value)
    return f"{float(value):.3f}"


def _status(check: ThresholdCheck | None) -> str:
    if check is None:
        return "not checked"
    if check.status == "blocked":
        return f"blocked ({check.reason})"
    return f"{check.status} (min {check.threshold})"

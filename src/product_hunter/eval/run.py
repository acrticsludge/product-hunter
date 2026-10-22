"""The model identity, the run record, the thresholds, and the comparison.

The result of a run carries no timestamp and no random value, so the same
dataset and the same outputs produce byte-identical JSON. A result file can
be committed and diffed.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from product_hunter.config import repo_root
from product_hunter.eval.cases import (
    EVALUATOR_VERSION,
    LABEL_FREE_TASKS,
    MAX_EVAL_FILE_BYTES,
    EvalCase,
    EvalDataset,
    EvalError,
    LabelStatus,
    TaskName,
    brief_value,
    check_file_size,
    dataset_cases_for,
    label_status,
    short_error,
)
from product_hunter.eval.tasks import CaseScore, family_metrics, score_case

# The three reasons a metric can be null, so a reader can tell them apart.
BLOCKED_NO_LABEL = "the task has no human label"
BLOCKED_UNREADABLE = "no case of this task could be scored"
BLOCKED_NO_DENOMINATOR = "the metric has no denominator in this task"

# The metrics a fall is a regression. Everything not named here is a count or
# a property of the label, and a count never moves the verdict.
_HIGHER_IS_BETTER = frozenset(
    {
        "precision",
        "recall",
        "f1",
        "accuracy",
        "exact_rate",
        "within_one_rate",
        "verbatim_rate",
        "key_agreement",
        "domain_accuracy",
        "actor_agreement",
        "action_agreement",
        "object_agreement",
        "consequence_agreement",
        "potential_exact_rate",
        "confidence_exact_rate",
        "potential_within_rate",
        "confidence_within_rate",
    }
)
_LOWER_IS_BETTER = frozenset(
    {
        "false_accept_rate",
        "false_reject_rate",
        "mean_absolute_error",
        "potential_mean_absolute_error",
        "confidence_mean_absolute_error",
    }
)


class ThresholdCheck(BaseModel):
    """One metric against the lowest value that passes."""

    model_config = ConfigDict(extra="forbid")

    task: str
    metric: str
    threshold: float
    value: float | None
    status: str
    reason: str | None = None


class ThresholdReport(BaseModel):
    """The result of checking a run against the threshold file."""

    model_config = ConfigDict(extra="forbid")

    checks: list[ThresholdCheck] = Field(default_factory=list)
    passed: bool | None = None
    failed: list[str] = Field(default_factory=list)
    blocked: list[str] = Field(default_factory=list)


class Thresholds(BaseModel):
    """The threshold file: a floor per metric, per task."""

    model_config = ConfigDict(extra="forbid")

    version: int = 1
    # A negative tolerance would hide every regression, so it is refused.
    tolerance: float = Field(default=0.0, ge=0)
    defaults: dict[str, float] = Field(default_factory=dict)
    tasks: dict[TaskName, dict[str, float]] = Field(default_factory=dict)


class ModelIdentity(BaseModel):
    """Where the answers came from."""

    model_config = ConfigDict(extra="forbid")

    provider: str
    model: str
    model_version: str | None = None
    model_catalog: str | None = None
    prompt_version: str | None = None
    prompt_versions: dict[TaskName, str] = Field(default_factory=dict)

    def prompt_for(self, task: TaskName | str) -> str | None:
        """The prompt version that answered a task, or ``None``."""
        name = task.value if isinstance(task, TaskName) else str(task)
        try:
            key = TaskName(name)
        except ValueError:
            return None
        if key in self.prompt_versions:
            return self.prompt_versions[key]
        return self.prompt_version


class RunSpec(ModelIdentity):
    """The input of a run: which model, which dataset, which outputs."""

    run_id: str
    dataset_version: str | None = None
    outputs: str


class ModelOutput(BaseModel):
    """One answer of the model, for one case."""

    model_config = ConfigDict(extra="forbid")

    case_id: str
    output: dict[str, Any]
    prompt_version: str | None = None


class TaskResult(BaseModel):
    """What one task family produced in one run."""

    model_config = ConfigDict(extra="forbid")

    task: TaskName
    prompt_version: str | None = None
    cases: int
    scored: int
    unlabeled: int
    errored: int
    metrics: dict[str, float | int | None]
    blocked: bool
    blocked_reason: str | None = None
    case_results: list[CaseScore] = Field(default_factory=list)


class EvalRun(BaseModel):
    """The record of one run."""

    model_config = ConfigDict(extra="forbid")

    run_id: str
    evaluator_version: str = EVALUATOR_VERSION
    dataset_version: str
    provider: str
    model: str
    model_version: str | None = None
    model_catalog: str | None = None
    prompt_version: str | None = None
    prompt_versions: dict[str, str] = Field(default_factory=dict)
    tasks: list[TaskResult] = Field(default_factory=list)
    label_status: LabelStatus
    thresholds: ThresholdReport = Field(default_factory=ThresholdReport)
    passed: bool | None = None
    warnings: list[str] = Field(default_factory=list)


class MetricDelta(BaseModel):
    """One metric, in two runs, and the distance between them.

    The delta is the signed change, the candidate minus the baseline. The
    status reads that change against the direction the metric is better in, so
    a count, which says nothing about the quality of the model, is shown with
    its numbers and marked ``not_comparable``.
    """

    model_config = ConfigDict(extra="forbid")

    task: str
    metric: str
    baseline: float | None
    candidate: float | None
    delta: float | None
    status: str


class CaseFlip(BaseModel):
    """One case that passed in one run and did not pass in the other."""

    model_config = ConfigDict(extra="forbid")

    task: str
    case_id: str
    baseline: bool | None
    candidate: bool | None
    kind: str


class ComparisonReport(BaseModel):
    """Two runs, side by side."""

    model_config = ConfigDict(extra="forbid")

    baseline_run_id: str
    candidate_run_id: str
    baseline_model: str
    candidate_model: str
    baseline_provider: str
    candidate_provider: str
    dataset_version_match: bool
    metric_deltas: list[MetricDelta] = Field(default_factory=list)
    case_flips: list[CaseFlip] = Field(default_factory=list)
    regressions: int = 0
    improvements: int = 0
    verdict: str
    notes: list[str] = Field(default_factory=list)


# --- the run ----------------------------------------------------------------


def run_dataset(
    dataset: EvalDataset, spec: RunSpec, thresholds: Thresholds
) -> EvalRun:
    """Score every case of the dataset against the outputs the spec names."""
    if (
        spec.dataset_version is not None
        and spec.dataset_version != dataset.dataset_version
    ):
        raise EvalError(
            f"the run spec names the dataset version "
            f"{brief_value(spec.dataset_version)} but the dataset is "
            f"{brief_value(dataset.dataset_version)}"
        )

    outputs_path = _resolve(spec.outputs)
    outputs = read_outputs(outputs_path)
    by_case: dict[str, ModelOutput] = {}
    for item in outputs:
        if item.case_id in by_case:
            raise EvalError(
                f"{outputs_path}: the case {brief_value(item.case_id)} has more "
                "than one output"
            )
        by_case[item.case_id] = item
    known = {case.case_id for case in dataset.cases}
    for case_id in sorted(set(by_case) - known):
        raise EvalError(
            f"{outputs_path}: the output names the case {brief_value(case_id)}, "
            "which the dataset does not hold"
        )

    results: list[TaskResult] = []
    warnings: list[str] = []
    for task in TaskName:
        cases = dataset_cases_for(dataset, task)
        if not cases:
            continue
        names = family_metrics(task)
        case_results: list[CaseScore] = []
        for case in cases:
            item = by_case.get(case.case_id)
            if item is None:
                case_results.append(
                    CaseScore(
                        case_id=case.case_id,
                        task=task,
                        passed=False,
                        metrics={name: None for name in names},
                        detail={},
                        error=(
                            f"{outputs_path}: the case "
                            f"{brief_value(case.case_id)} has no output"
                        ),
                    )
                )
                continue
            score = score_case(case, item.output)
            if score.error is not None:
                score = score.model_copy(
                    update={"error": f"{outputs_path}: {score.error}"}
                )
            case_results.append(score)

        unlabeled = sum(
            1
            for case in cases
            if case.expected is None and task not in LABEL_FREE_TASKS
        )
        errored = sum(1 for result in case_results if result.error is not None)
        # A case is scored when it carries a metric: no error, and a verdict
        # that is not the "nothing to check" one.
        scored = sum(
            1
            for result in case_results
            if result.error is None and result.passed is not None
        )
        blocked = unlabeled == len(cases)
        prompt_version = _resolved_prompt_version(spec, task, cases, by_case)
        results.append(
            TaskResult(
                task=task,
                prompt_version=prompt_version,
                cases=len(cases),
                scored=scored,
                unlabeled=unlabeled,
                errored=errored,
                metrics=_aggregate(case_results),
                blocked=blocked,
                blocked_reason=(
                    "no case in this task carries a human label" if blocked else None
                ),
                case_results=case_results,
            )
        )
        if prompt_version is None:
            warnings.append(f"the task {task.value} has no prompt version")
        if errored == len(cases):
            warnings.append(f"no case of the task {task.value} could be scored")

    run = EvalRun(
        run_id=spec.run_id,
        evaluator_version=EVALUATOR_VERSION,
        dataset_version=dataset.dataset_version,
        provider=spec.provider,
        model=spec.model,
        model_version=spec.model_version,
        model_catalog=spec.model_catalog,
        prompt_version=spec.prompt_version,
        prompt_versions={
            task.value: version for task, version in spec.prompt_versions.items()
        },
        tasks=results,
        label_status=label_status(dataset),
        warnings=warnings,
    )
    report = check_thresholds(run, thresholds)
    run.thresholds = report
    run.passed = report.passed
    for check in report.checks:
        if check.status == "blocked":
            run.warnings.append(
                f"the threshold {check.task}.{check.metric} was not checked: "
                f"{check.reason}"
            )
    return run


def check_thresholds(run: EvalRun, thresholds: Thresholds) -> ThresholdReport:
    """Check a run against the threshold file.

    Only a metric in ``_HIGHER_IS_BETTER`` may gate: it is a label-derived rate
    that is better when higher, which is the only kind of number a floor says
    something about. A limit on a count the model produced, or on a
    lower-is-better rate a floor would read backwards, is not evaluated and
    never makes a run pass. A metric that is ``None`` because the task is
    blocked is reported as blocked and does not fail the run. A run with no
    evaluable threshold has not passed; it has not been measured.
    """
    checks: list[ThresholdCheck] = []
    failed: list[str] = []
    blocked: list[str] = []
    for task in run.tasks:
        # The limits are merged by metric name, so a task entry wins over the
        # default even when the two spell the metric differently.
        limits: dict[str, float] = {}
        for key, threshold in thresholds.defaults.items():
            limits[_metric_name(key)] = threshold
        for key, threshold in thresholds.tasks.get(task.task, {}).items():
            limits[_metric_name(key)] = threshold
        for metric in sorted(limits):
            if metric not in task.metrics:
                continue
            # A count the model produced, or a rate where lower is better, is
            # not a quality number. A floor on it would read a number the model
            # made as a verdict, so it is never evaluated.
            if metric not in _HIGHER_IS_BETTER:
                continue
            threshold = limits[metric]
            value = task.metrics[metric]
            reason: str | None = None
            if value is None:
                status = "blocked"
                # A null metric means one of three things, and the reader has
                # to be told which.
                if task.blocked:
                    reason = BLOCKED_NO_LABEL
                elif task.cases and task.errored == task.cases:
                    reason = BLOCKED_UNREADABLE
                else:
                    reason = BLOCKED_NO_DENOMINATOR
            elif value >= threshold:
                status = "pass"
            else:
                status = "fail"
            checks.append(
                ThresholdCheck(
                    task=task.task.value,
                    metric=metric,
                    threshold=threshold,
                    value=value,
                    status=status,
                    reason=reason,
                )
            )
            if status == "fail":
                failed.append(f"{task.task.value}.{metric}")
            elif status == "blocked":
                blocked.append(f"{task.task.value}.{metric}")

    evaluated = [check for check in checks if check.status in ("pass", "fail")]
    passed = None if not evaluated else not failed
    return ThresholdReport(
        checks=checks, passed=passed, failed=failed, blocked=blocked
    )


# --- the comparison ---------------------------------------------------------


def compare_runs(
    baseline: EvalRun, candidate: EvalRun, thresholds: Thresholds
) -> ComparisonReport:
    """Compare two runs and say whether the candidate regressed.

    Two runs are comparable only when they scored the same dataset version
    under the same evaluator. Across different datasets, or under different
    evaluators, the deltas mean nothing, so no delta is reported.

    The two rules are independent. A run that differs in both is still not
    comparable, and both notes are written.
    """
    dataset_match = baseline.dataset_version == candidate.dataset_version
    evaluator_match = baseline.evaluator_version == candidate.evaluator_version
    report = ComparisonReport(
        baseline_run_id=baseline.run_id,
        candidate_run_id=candidate.run_id,
        baseline_model=baseline.model,
        candidate_model=candidate.model,
        baseline_provider=baseline.provider,
        candidate_provider=candidate.provider,
        dataset_version_match=dataset_match,
        verdict=(
            "no_change" if dataset_match and evaluator_match else "not_comparable"
        ),
    )
    if not dataset_match:
        report.notes.append(
            f"the baseline scored the dataset {baseline.dataset_version!r} and the "
            f"candidate scored {candidate.dataset_version!r}; the deltas would "
            "mean nothing"
        )
    if not evaluator_match:
        report.notes.append(
            f"the baseline was scored by the evaluator "
            f"{baseline.evaluator_version!r} and the candidate by "
            f"{candidate.evaluator_version!r}; the metric set changed, so the "
            "deltas would mean nothing"
        )
    if not dataset_match or not evaluator_match:
        return report

    tolerance = thresholds.tolerance
    baseline_tasks = {result.task: result for result in baseline.tasks}
    candidate_tasks = {result.task: result for result in candidate.tasks}
    deltas: list[MetricDelta] = []
    for task in TaskName:
        before = baseline_tasks.get(task)
        after = candidate_tasks.get(task)
        if before is None and after is None:
            continue
        names = list(before.metrics) if before is not None else []
        for name in after.metrics if after is not None else {}:
            if name not in names:
                names.append(name)
        for name in names:
            old = before.metrics.get(name) if before is not None else None
            new = after.metrics.get(name) if after is not None else None
            if old is None or new is None:
                deltas.append(
                    MetricDelta(
                        task=task.value,
                        metric=name,
                        baseline=old,
                        candidate=new,
                        delta=None,
                        status="not_comparable",
                    )
                )
                continue
            delta = new - old
            if name in _HIGHER_IS_BETTER:
                status = _movement(delta, tolerance)
            elif name in _LOWER_IS_BETTER:
                status = _movement(-delta, tolerance)
            else:
                # A count says nothing about whether the model got better.
                status = "not_comparable"
            deltas.append(
                MetricDelta(
                    task=task.value,
                    metric=name,
                    baseline=old,
                    candidate=new,
                    delta=delta,
                    status=status,
                )
            )

    flips: list[CaseFlip] = []
    for task in TaskName:
        before = baseline_tasks.get(task)
        after = candidate_tasks.get(task)
        if before is None or after is None:
            continue
        before_cases = {result.case_id: result for result in before.case_results}
        after_cases = {result.case_id: result for result in after.case_results}
        for case_id in sorted(set(before_cases) & set(after_cases)):
            old = before_cases[case_id].passed
            new = after_cases[case_id].passed
            if old is True and new is not True:
                flips.append(
                    CaseFlip(
                        task=task.value,
                        case_id=case_id,
                        baseline=old,
                        candidate=new,
                        kind="regressed",
                    )
                )
            elif new is True and old is not True:
                flips.append(
                    CaseFlip(
                        task=task.value,
                        case_id=case_id,
                        baseline=old,
                        candidate=new,
                        kind="improved",
                    )
                )

    # The counters hold the regressed and improved metrics as well as the
    # regressed and improved cases, so the number a reader sees matches the
    # verdict.
    regressions = sum(1 for delta in deltas if delta.status == "regressed") + sum(
        1 for flip in flips if flip.kind == "regressed"
    )
    improvements = sum(1 for delta in deltas if delta.status == "improved") + sum(
        1 for flip in flips if flip.kind == "improved"
    )
    if regressions:
        verdict = "regression"
    elif improvements:
        verdict = "improvement"
    else:
        verdict = "no_change"

    for task in TaskName:
        if task not in baseline_tasks and task not in candidate_tasks:
            continue
        old = _prompt_for(baseline, task)
        new = _prompt_for(candidate, task)
        if old is None or new is None:
            report.notes.append(
                f"the prompt version of the task {task.value} is unknown in one "
                "of the two runs"
            )
        elif old != new:
            report.notes.append(
                f"the prompt version of the task {task.value} changed from "
                f"{old!r} to {new!r}"
            )

    report.metric_deltas = deltas
    report.case_flips = flips
    report.regressions = regressions
    report.improvements = improvements
    report.verdict = verdict
    return report


# --- the files --------------------------------------------------------------


def load_thresholds(
    path: Path, *, limit: int = MAX_EVAL_FILE_BYTES
) -> Thresholds:
    """Read the threshold file, or refuse it."""
    path = Path(path)
    if not path.is_file():
        raise EvalError(f"{path}: the threshold file does not exist")
    check_file_size(path, limit)
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8-sig"))
    except yaml.YAMLError as exc:
        raise EvalError(f"{path}: the file is not valid YAML: {exc}") from exc
    except OSError as exc:
        raise EvalError(f"{path}: the file cannot be read: {exc}") from exc
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise EvalError(f"{path}: the threshold file is not a mapping")
    try:
        thresholds = Thresholds.model_validate(raw)
    except ValidationError as exc:
        raise EvalError(
            f"{path}: the threshold file is not valid: {short_error(exc)}"
        ) from exc
    known = _known_metrics()
    for key in thresholds.defaults:
        metric = _metric_name(key)
        if metric not in known:
            raise EvalError(
                f"{path}: the threshold {brief_value(key)} names the metric "
                f"{brief_value(metric)}, which no task family produces"
            )
        _check_gateable(path, key, metric)
    for task, block in thresholds.tasks.items():
        produced = set(family_metrics(task))
        for key in block:
            metric = _metric_name(key)
            if metric not in produced:
                raise EvalError(
                    f"{path}: the threshold {brief_value(key)} of the task "
                    f"{task.value} names the metric {brief_value(metric)}, which "
                    f"the task {task.value} does not produce"
                )
            _check_gateable(path, key, metric)
    return thresholds


def _check_gateable(path: Path, key: str, metric: str) -> None:
    """Refuse a threshold on a metric that is better when it is lower."""
    if metric not in _HIGHER_IS_BETTER:
        raise EvalError(
            f"{path}: the threshold {brief_value(key)} names the metric "
            f"{brief_value(metric)}, which is not gateable: a threshold may "
            "only set a floor on a rate that is better when it is higher, and a "
            "count the model produced is not a gate"
        )


def read_outputs(
    path: Path, *, limit: int = MAX_EVAL_FILE_BYTES
) -> list[ModelOutput]:
    """Read the model's answers, one JSON object per line."""
    path = Path(path)
    if not path.is_file():
        raise EvalError(f"{path}: the outputs file does not exist")
    check_file_size(path, limit)
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise EvalError(f"{path}: the file cannot be read: {exc}") from exc
    items: list[ModelOutput] = []
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError as exc:
            raise EvalError(f"{path}: line {number} is not valid JSON: {exc}") from exc
        except RecursionError as exc:
            raise EvalError(
                f"{path}: line {number} is nested too deeply to parse: {exc}"
            ) from exc
        if not isinstance(raw, dict):
            raise EvalError(f"{path}: line {number} is not a JSON object")
        try:
            items.append(ModelOutput.model_validate(raw))
        except ValidationError as exc:
            raise EvalError(
                f"{path}: line {number} is not a valid output: {short_error(exc)}"
            ) from exc
    return items


def load_run_spec(path: Path, *, limit: int = MAX_EVAL_FILE_BYTES) -> RunSpec:
    """Read a run spec file.

    The spec is a bench input like the others, so it is read here, where the
    size cap lives.
    """
    path = Path(path)
    if not path.is_file():
        raise EvalError(f"{path}: the run spec file does not exist")
    check_file_size(path, limit)
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise EvalError(f"{path}: the file is not valid JSON: {exc}") from exc
    except RecursionError as exc:
        raise EvalError(
            f"{path}: the file is nested too deeply to parse: {exc}"
        ) from exc
    except OSError as exc:
        raise EvalError(f"{path}: the file cannot be read: {exc}") from exc
    try:
        return RunSpec.model_validate(raw)
    except ValidationError as exc:
        raise EvalError(
            f"{path}: the file is not a valid run spec: {short_error(exc)}"
        ) from exc


def load_run(path: Path, *, limit: int = MAX_EVAL_FILE_BYTES) -> EvalRun:
    """Read a run result file."""
    path = Path(path)
    if not path.is_file():
        raise EvalError(f"{path}: the run file does not exist")
    check_file_size(path, limit)
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise EvalError(f"{path}: the file is not valid JSON: {exc}") from exc
    except RecursionError as exc:
        raise EvalError(
            f"{path}: the file is nested too deeply to parse: {exc}"
        ) from exc
    except OSError as exc:
        raise EvalError(f"{path}: the file cannot be read: {exc}") from exc
    try:
        return EvalRun.model_validate(raw)
    except ValidationError as exc:
        raise EvalError(
            f"{path}: the file is not a valid run result: {short_error(exc)}"
        ) from exc


def load_comparison(
    path: Path, *, limit: int = MAX_EVAL_FILE_BYTES
) -> ComparisonReport:
    """Read a comparison result file."""
    path = Path(path)
    if not path.is_file():
        raise EvalError(f"{path}: the comparison file does not exist")
    check_file_size(path, limit)
    try:
        raw = path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise EvalError(f"{path}: the file cannot be read: {exc}") from exc
    try:
        return ComparisonReport.model_validate_json(raw)
    except ValidationError as exc:
        raise EvalError(
            f"{path}: the file is not a valid comparison result: {short_error(exc)}"
        ) from exc


def write_run(path: Path, run: EvalRun) -> None:
    """Write a run result as JSON, creating the parent directories."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        run.model_dump_json(indent=2) + "\n", encoding="utf-8", newline="\n"
    )


def write_comparison(path: Path, comparison: ComparisonReport) -> None:
    """Write a comparison result as JSON, creating the parent directories."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        comparison.model_dump_json(indent=2) + "\n", encoding="utf-8", newline="\n"
    )


# --- the helpers ------------------------------------------------------------


def _metric_name(key: str) -> str:
    """The metric a threshold key names, with the ``min_`` prefix dropped."""
    return key[len("min_") :] if key.startswith("min_") else key


def _known_metrics() -> set[str]:
    """Every metric name any family produces."""
    names: set[str] = set()
    for task in TaskName:
        names.update(family_metrics(task))
    return names


def _movement(delta: float, tolerance: float) -> str:
    """The status of a change, read against the direction the metric is good in."""
    if delta > tolerance:
        return "improved"
    if delta < -tolerance:
        return "regressed"
    return "unchanged"


def task_value_rules(case_results: list[CaseScore]) -> dict[str, str]:
    """How each metric of a family reaches its task value.

    The report shows this, because a pooled number and the mean of the case
    values answer different questions. The rule comes from the case values
    themselves, the same way the value does, so the two cannot disagree.
    """
    names = _metric_names(case_results)
    rules: dict[str, str] = {}
    for name in names:
        rules[name] = _task_value_rule(case_results, name)
    return rules


def _task_value_rule(case_results: list[CaseScore], name: str) -> str:
    """How one metric reaches its task value: pooled, mean, or sum.

    A rate is a rate and a count is a count, and the two are told apart by
    what the case value is: every family writes a rate as a number with a
    fraction and a count as a whole number.
    """
    values = [
        result.metrics.get(name)
        for result in case_results
        if result.metrics.get(name) is not None
    ]
    if not values:
        return "not measured"
    if name == "mean_absolute_error":
        return "pooled"
    if name in _pooled_names(set(_metric_names(case_results))):
        return "pooled"
    return "mean" if isinstance(values[0], float) else "sum"


def _resolve(path: str | Path) -> Path:
    """A relative path is resolved against the repository root."""
    candidate = Path(path)
    return candidate if candidate.is_absolute() else repo_root() / candidate


def _metric_names(case_results: list[CaseScore]) -> list[str]:
    """The metric names of a family, in the order the family writes them."""
    names: list[str] = []
    for result in case_results:
        for name in result.metrics:
            if name not in names:
                names.append(name)
    return names


def _aggregate(case_results: list[CaseScore]) -> dict[str, float | int | None]:
    """The task value of each metric.

    A rate is pooled when the family exposes the counts behind it: the
    detection and grouping families pool their precision, recall and f1 from
    the summed counts, the agreement family pools its rates from the summed
    ``n``, and the classification families pool their accuracy through ``n``.
    Every other rate is the mean of the case values, and every count is their
    sum.
    """
    metrics: dict[str, float | int | None] = {}
    for name in _metric_names(case_results):
        values = [
            result.metrics.get(name)
            for result in case_results
            if result.metrics.get(name) is not None
        ]
        if not values:
            metrics[name] = None
            continue
        rule = _task_value_rule(case_results, name)
        if rule == "pooled":
            if name == "mean_absolute_error":
                metrics[name] = _pooled_error(case_results)
            else:
                metrics[name] = _pooled_rate(case_results, name)
        elif rule == "mean":
            metrics[name] = sum(values) / len(values)
        else:
            metrics[name] = sum(values)
    return metrics


def _pooled_names(names: set[str]) -> set[str]:
    """The metrics of a family whose task value is pooled over the cases.

    The family tells which rule applies by the counts it exposes.
    """
    if {"true_positive", "false_positive", "false_negative"} <= names:
        return {"precision", "recall", "f1"}
    if {"n", "exact", "within_one"} <= names:
        return {"exact_rate", "within_one_rate"}
    if {"n", "accuracy"} <= names:
        return {"accuracy"}
    return set()


def _pooled_rate(case_results: list[CaseScore], name: str) -> float | None:
    """The pooled value of a rate, from the counts the family exposes."""
    if name in ("precision", "recall", "f1"):
        return _pooled_detection(case_results, name)
    if name == "accuracy":
        return _pooled_accuracy(case_results)
    right = "exact" if name == "exact_rate" else "within_one"
    return _pooled_count(case_results, right)


def _pooled_detection(case_results: list[CaseScore], name: str) -> float | None:
    """The pooled precision, recall or f1 of a detection or grouping family.

    A value is empty only when there was nothing on either side to compare. A
    zero denominator otherwise gives ``0.0``, the same rule the case values
    follow, so a gate on the precision can fail a model that found nothing.
    """
    hits = _sum_of(case_results, "true_positive")
    wrong = _sum_of(case_results, "false_positive")
    missed = _sum_of(case_results, "false_negative")
    if hits + wrong + missed == 0:
        return None
    if name == "precision":
        return hits / (hits + wrong) if hits + wrong else 0.0
    if name == "recall":
        return hits / (hits + missed) if hits + missed else 0.0
    denominator = 2 * hits + wrong + missed
    return 2 * hits / denominator


def _pooled_accuracy(case_results: list[CaseScore]) -> float | None:
    """The pooled accuracy: the right answers over the items compared."""
    total = 0
    right = 0
    for result in case_results:
        count = result.metrics.get("n")
        accuracy = result.metrics.get("accuracy")
        if not isinstance(count, int) or accuracy is None:
            continue
        total += count
        # The right answers are whole items, so the share comes back to the
        # count it came from.
        right += round(count * accuracy)
    return right / total if total else None


def _pooled_count(case_results: list[CaseScore], right: str) -> float | None:
    """The pooled rate of a family that counts its right answers."""
    total = _sum_of(case_results, "n")
    answers = _sum_of(case_results, right)
    return answers / total if total else None


def _pooled_error(case_results: list[CaseScore]) -> float | None:
    """The mean absolute error over every item compared, not over the cases."""
    total = 0
    distance = 0.0
    for result in case_results:
        value = result.metrics.get("mean_absolute_error")
        if value is None:
            continue
        count = result.metrics.get("n")
        count = count if isinstance(count, int) else 0
        distance += float(value) * count
        total += count
    return distance / total if total else None


def _sum_of(case_results: list[CaseScore], name: str) -> int:
    """The sum of one count over the cases that carry it."""
    total = 0
    for result in case_results:
        value = result.metrics.get(name)
        if isinstance(value, int):
            total += value
    return total


def _prompt_for(run: EvalRun, task: TaskName) -> str | None:
    """The prompt version a task was answered with, as the run resolved it."""
    for result in run.tasks:
        if result.task is task:
            return result.prompt_version
    return run.prompt_versions.get(task.value, run.prompt_version)


def _resolved_prompt_version(
    spec: RunSpec,
    task: TaskName,
    cases: list[EvalCase],
    by_case: dict[str, ModelOutput],
) -> str | None:
    """The prompt version of a task, from the outputs first and the spec after.

    An output line may name the prompt that produced it, and that name wins
    over the run default.
    """
    versions: list[str] = []
    for case in cases:
        item = by_case.get(case.case_id)
        if item is not None and item.prompt_version is not None:
            versions.append(item.prompt_version)
    if not versions:
        return spec.prompt_for(task)
    if len(set(versions)) > 1:
        raise EvalError(
            f"the outputs of the task {task.value} name more than one prompt "
            f"version: {', '.join(sorted(set(versions)))}"
        )
    return versions[0]

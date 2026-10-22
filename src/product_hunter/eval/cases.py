"""The benchmark cases, the dataset that holds them, and the label status.

A case pairs an input with an optional human label. The label is what makes a
quality number possible: a case with no label still runs, but every quality
metric it could produce stays ``None`` and its task is reported as blocked.
Nothing here ever writes a label.
"""

from __future__ import annotations

import json
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError

# The version of the scoring code. A run records the value it was scored with,
# so a number can always be traced to the code that produced it.
#
# It moved to eval-v2 when the entity resolution and scoring consistency
# families joined the benchmark. The metric set changed with them, so a v1
# number and a v2 number are not the same measurement.
EVALUATOR_VERSION = "eval-v2"


class TaskName(str, Enum):
    """The task families a benchmark case can belong to."""

    pain_extraction = "pain_extraction"
    false_positive_rejection = "false_positive_rejection"
    quote_fidelity = "quote_fidelity"
    severity = "severity"
    normalization = "normalization"
    duplicate_detection = "duplicate_detection"
    author_independence = "author_independence"
    clustering = "clustering"
    competitor_identification = "competitor_identification"
    counter_evidence_classification = "counter_evidence_classification"
    entity_resolution = "entity_resolution"
    scoring_consistency = "scoring_consistency"


class EvalError(Exception):
    """A dataset, an output file, or a threshold file cannot be used as given."""


class EvalCase(BaseModel):
    """One benchmark case: an input, and the human label when there is one."""

    model_config = ConfigDict(extra="forbid")

    case_id: str
    task: TaskName
    case_version: int = 1
    input: dict[str, Any]
    expected: dict[str, Any] | None = None


class EvalDataset(BaseModel):
    """A versioned set of benchmark cases."""

    model_config = ConfigDict(extra="forbid")

    dataset_version: str
    created_at: str | None = None
    description: str = ""
    cases: list[EvalCase]


class TaskLabelStatus(BaseModel):
    """How much of one task family a human has labelled."""

    model_config = ConfigDict(extra="forbid")

    task: TaskName
    cases: int
    labeled: int
    unlabeled: int
    blocked: bool


class LabelStatus(BaseModel):
    """The labelling state of a whole dataset."""

    model_config = ConfigDict(extra="forbid")

    dataset_version: str
    total_cases: int
    total_labeled: int
    tasks: list[TaskLabelStatus]


# The task names the loader accepts, kept as a plain set so the error message
# can name the offending value.
_TASK_VALUES = frozenset(task.value for task in TaskName)

# The families that need no human label. They are checked against the source
# text, so they are measured whatever the dataset holds, and they are never
# blocked. The run result and the label status both use this set, so they
# cannot disagree about it.
LABEL_FREE_TASKS = frozenset({TaskName.quote_fidelity})

# The largest file an eval reader pulls into memory. A dataset of a few
# thousand labelled cases is a few megabytes, so 32 MiB is generous and still
# bounded.
MAX_EVAL_FILE_BYTES = 32 * 1024 * 1024

# The longest a validation error may be before it is cut. Pydantic embeds the
# offending value, and that value can be a whole file.
MAX_ERROR_CHARS = 1000

# The longest a value echoed into an error message may be. A value that comes
# out of a file or out of a model is data, not a log line.
MAX_VALUE_CHARS = 120


def check_file_size(path: Path, limit: int = MAX_EVAL_FILE_BYTES) -> None:
    """Refuse a file over the limit, before it is read into memory.

    The size is asked for rather than the content, so an oversized file costs
    nothing to refuse.
    """
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise EvalError(f"{path}: the file cannot be read: {exc}") from exc
    if size > limit:
        raise EvalError(
            f"{path}: the file is {size} bytes, over the {limit} byte limit"
        )


def short_error(exc: Exception, limit: int = MAX_ERROR_CHARS) -> str:
    """The message of a validation error, cut to a length a log can hold."""
    text = str(exc)
    if len(text) <= limit:
        return text
    return text[:limit] + f"... (cut at {limit} characters)"


def brief_value(value: object, limit: int = MAX_VALUE_CHARS) -> str:
    """A value as it goes into an error message, cut to a short length.

    The value is named, because naming it is how the operator finds the
    mistake, but it is cut, because it came out of a file or out of a model.

    This never raises. Python refuses to print an integer of more than 4300
    digits, so a value it will not print is named as too large instead.
    """
    try:
        text = repr(value)
    except ValueError:
        return f"... (too large to print, over {limit} characters)"
    if len(text) <= limit:
        return text
    return text[:limit] + f"... (cut at {limit} characters)"


def load_dataset(path: Path, *, limit: int = MAX_EVAL_FILE_BYTES) -> EvalDataset:
    """Read and validate a dataset file.

    A bad file is refused, never guessed at: the error names the file and the
    problem. A file over the size limit is refused before it is read.
    """
    path = Path(path)
    if not path.is_file():
        raise EvalError(f"{path}: the dataset file does not exist")
    check_file_size(path, limit)
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise EvalError(f"{path}: the file is not valid JSON: {exc}") from exc
    except RecursionError as exc:
        raise EvalError(f"{path}: the file is nested too deeply to parse: {exc}") from exc
    except OSError as exc:
        raise EvalError(f"{path}: the file cannot be read: {exc}") from exc

    if not isinstance(raw, dict):
        raise EvalError(f"{path}: the dataset is not a JSON object")
    if "dataset_version" not in raw:
        raise EvalError(f"{path}: the dataset has no dataset_version")
    cases = raw.get("cases")
    if not isinstance(cases, list):
        raise EvalError(f"{path}: the dataset has no cases list")

    for index, case in enumerate(cases):
        if not isinstance(case, dict):
            raise EvalError(f"{path}: case {index} is not a JSON object")
        task = case.get("task")
        if task not in _TASK_VALUES:
            raise EvalError(
                f"{path}: case {index} names the unknown task {brief_value(task)}"
            )
        if not isinstance(case.get("input"), dict):
            raise EvalError(f"{path}: case {index} has no input object")

    try:
        dataset = EvalDataset.model_validate(raw)
    except ValidationError as exc:
        raise EvalError(
            f"{path}: the dataset is not valid: {short_error(exc)}"
        ) from exc

    seen: set[str] = set()
    for case in dataset.cases:
        if case.case_id in seen:
            raise EvalError(
                f"{path}: the case id {brief_value(case.case_id)} appears more "
                "than once"
            )
        seen.add(case.case_id)
    return dataset


def label_status(dataset: EvalDataset) -> LabelStatus:
    """Count the labelled cases per task and say which tasks are blocked.

    Every task of the enum is reported, including the ones with no case at
    all, so the status shows which families have no data yet. A task is blocked
    when it has no labelled case and the family needs a label.
    """
    tasks: list[TaskLabelStatus] = []
    for task in TaskName:
        cases = dataset_cases_for(dataset, task)
        labeled = sum(1 for case in cases if case.expected is not None)
        tasks.append(
            TaskLabelStatus(
                task=task,
                cases=len(cases),
                labeled=labeled,
                unlabeled=len(cases) - labeled,
                blocked=labeled == 0 and task not in LABEL_FREE_TASKS,
            )
        )
    return LabelStatus(
        dataset_version=dataset.dataset_version,
        total_cases=len(dataset.cases),
        total_labeled=sum(status.labeled for status in tasks),
        tasks=tasks,
    )


def dataset_cases_for(dataset: EvalDataset, task: TaskName | str) -> list[EvalCase]:
    """The cases of one task, in the order the dataset lists them."""
    try:
        wanted = TaskName(task)
    except ValueError as exc:
        raise EvalError(f"the unknown task {task!r}") from exc
    return [case for case in dataset.cases if case.task is wanted]

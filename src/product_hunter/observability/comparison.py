"""The durable record of a model comparison.

The benchmark scores a run and compares two runs. This module keeps the
record of that comparison in the store, so the answer to "did the model
change make the quality worse" survives the session that produced it.

The record names the model, the provider, the model version and the prompt
version of the run that was scored. No model is named in this code: every
one of those fields is read off the run object that was handed in.

The write is an upsert on the comparison id, the way the model routing store
writes an execution: the same comparison written twice leaves one row, and
the second write repairs a row the first one left behind.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from product_hunter.eval.run import ComparisonReport, EvalRun
from product_hunter.fetch import utcnow

# The prefix every comparison identifier carries.
COMPARISON_ID_PREFIX = "cmp_"


class ComparisonError(Exception):
    """Raised when a comparison cannot be written or read."""


class ComparisonRecord(BaseModel):
    """One comparison, as it is held in the store."""

    model_config = ConfigDict(extra="forbid")

    comparison_id: str
    dataset_version: str
    evaluator_version: str
    baseline_run_id: str
    baseline_provider: str
    baseline_model: str
    baseline_model_version: str | None
    baseline_prompt_version: str | None
    candidate_run_id: str
    candidate_provider: str
    candidate_model: str
    candidate_model_version: str | None
    candidate_prompt_version: str | None
    verdict: str
    comparable: bool
    regressions: int
    improvements: int
    tasks: list[dict[str, Any]] = Field(default_factory=list)
    created_at: str


def record_comparison(
    conn: sqlite3.Connection,
    baseline: EvalRun,
    candidate: EvalRun,
    comparison: ComparisonReport,
    *,
    now: datetime | None = None,
) -> str:
    """Write one comparison to the store and return its identifier.

    The model, the provider, the model version and the prompt version are
    read off the two runs, and the evaluator version off the run itself, so
    the record says what was scored without this module knowing any model.

    A store that does not hold the table refuses the write. The comparison is
    not dropped without a word: a caller that asked for it to be kept is
    told that it was not.
    """
    if not _has_table(conn, "model_comparisons"):
        raise ComparisonError(
            "the model_comparisons table is not in the schema; run the "
            "migration before recording a comparison"
        )
    created_at = (now or utcnow()).isoformat()
    comparison_id = _comparison_id(
        baseline.run_id, candidate.run_id, created_at
    )
    tasks = _tasks(baseline, candidate, comparison)
    payload = (
        comparison_id,
        baseline.dataset_version,
        baseline.evaluator_version,
        baseline.run_id,
        baseline.provider,
        baseline.model,
        baseline.model_version,
        baseline.prompt_version,
        candidate.run_id,
        candidate.provider,
        candidate.model,
        candidate.model_version,
        candidate.prompt_version,
        comparison.verdict,
        int(comparison.verdict != "not_comparable"),
        int(comparison.regressions),
        int(comparison.improvements),
        json.dumps(tasks, sort_keys=True),
        created_at,
    )
    conn.execute(
        """
        INSERT INTO model_comparisons (
            comparison_id, dataset_version, evaluator_version,
            baseline_run_id, baseline_provider, baseline_model,
            baseline_model_version, baseline_prompt_version,
            candidate_run_id, candidate_provider, candidate_model,
            candidate_model_version, candidate_prompt_version,
            verdict, comparable, regressions, improvements, tasks_json,
            created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(comparison_id) DO UPDATE SET
            dataset_version = excluded.dataset_version,
            evaluator_version = excluded.evaluator_version,
            baseline_run_id = excluded.baseline_run_id,
            baseline_provider = excluded.baseline_provider,
            baseline_model = excluded.baseline_model,
            baseline_model_version = excluded.baseline_model_version,
            baseline_prompt_version = excluded.baseline_prompt_version,
            candidate_run_id = excluded.candidate_run_id,
            candidate_provider = excluded.candidate_provider,
            candidate_model = excluded.candidate_model,
            candidate_model_version = excluded.candidate_model_version,
            candidate_prompt_version = excluded.candidate_prompt_version,
            verdict = excluded.verdict,
            comparable = excluded.comparable,
            regressions = excluded.regressions,
            improvements = excluded.improvements,
            tasks_json = excluded.tasks_json,
            created_at = excluded.created_at
        """,
        payload,
    )
    return comparison_id


def list_comparisons(conn: sqlite3.Connection) -> list[ComparisonRecord]:
    """Every comparison the store holds, oldest first."""
    if not _has_table(conn, "model_comparisons"):
        raise ComparisonError(
            "the model_comparisons table is not in the schema; there is no "
            "comparison to read"
        )
    rows = conn.execute(
        "SELECT comparison_id, dataset_version, evaluator_version, "
        "baseline_run_id, baseline_provider, baseline_model, "
        "baseline_model_version, baseline_prompt_version, "
        "candidate_run_id, candidate_provider, candidate_model, "
        "candidate_model_version, candidate_prompt_version, verdict, "
        "comparable, regressions, improvements, tasks_json, created_at "
        "FROM model_comparisons ORDER BY created_at, comparison_id"
    ).fetchall()
    return [_row_to_record(row) for row in rows]


def _comparison_id(
    baseline_run_id: str, candidate_run_id: str, created_at: str
) -> str:
    """The identifier of a comparison.

    The basis is the two runs and the moment the comparison was made, so
    the same three produce the same identifier, and two comparisons of the
    same two runs at different moments are two rows.
    """
    basis = f"{baseline_run_id}|{candidate_run_id}|{created_at}"
    return COMPARISON_ID_PREFIX + hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16]


def _tasks(
    baseline: EvalRun, candidate: EvalRun, comparison: ComparisonReport
) -> list[dict[str, Any]]:
    """The task families the two runs covered, and what each one measured.

    The entry of a task names the prompt version each run answered it with,
    how many cases it held, how many of them were scored, how many carry a
    human label, whether the task was blocked for want of one, and what each
    of its metrics did between the two runs.
    """
    names = sorted(set(_task_names(baseline)) | set(_task_names(candidate)))
    entries: list[dict[str, Any]] = []
    for name in names:
        before = _task_result(baseline, name)
        after = _task_result(candidate, name)
        cases = (before.cases if before else 0) or (after.cases if after else 0)
        scored = (before.scored if before else 0) or (after.scored if after else 0)
        labelled = _labelled(baseline, candidate, name)
        blocked = bool(after.blocked if after else before.blocked if before else False)
        entries.append(
            {
                "task": name,
                "baseline_prompt_version": _prompt_for(baseline, name),
                "candidate_prompt_version": _prompt_for(candidate, name),
                "cases": cases,
                "scored": scored,
                "labelled": labelled,
                "blocked": blocked,
                "metrics": [
                    {
                        "metric": delta.metric,
                        "baseline": delta.baseline,
                        "candidate": delta.candidate,
                        "delta": delta.delta,
                        "status": delta.status,
                    }
                    for delta in comparison.metric_deltas
                    if delta.task == name
                ],
            }
        )
    return entries


def _task_names(run: EvalRun) -> list[str]:
    """The names of the task families a run scored."""
    return [_task_name(result.task) for result in run.tasks]


def _task_name(task: Any) -> str:
    """The name of a task family, whether it is an enum or a plain string."""
    return str(getattr(task, "value", task))


def _task_result(run: EvalRun, name: str) -> Any:
    """The result of one task family, when the run holds it."""
    for result in run.tasks:
        if _task_name(result.task) == name:
            return result
    return None


def _labelled(baseline: EvalRun, candidate: EvalRun, name: str) -> int:
    """How many cases of a task carry a human label.

    The label status of the run is the authority. It counts a case as
    labelled only when the case carries an ``expected`` block, so a family
    that needs no label holds zero labelled cases: quote fidelity is scored
    against the source text, and its cases carry no human answer.

    The two runs are asked in turn, and the larger number wins, because a
    label added between the two runs is a label the task holds.
    """
    counts = [
        int(status.labeled)
        for run in (baseline, candidate)
        for status in run.label_status.tasks
        if status.task.value == name
    ]
    return max(counts) if counts else 0


def _prompt_for(run: EvalRun, name: str) -> str | None:
    """The prompt version a run answered a task with."""
    result = _task_result(run, name)
    if result is not None and result.prompt_version is not None:
        return result.prompt_version
    return run.prompt_versions.get(name, run.prompt_version)


def _row_to_record(row: sqlite3.Row) -> ComparisonRecord:
    """A comparison row, read back into the shape it was written from."""
    tasks = _read_tasks(row["tasks_json"])
    return ComparisonRecord(
        comparison_id=str(row["comparison_id"]),
        dataset_version=str(row["dataset_version"]),
        evaluator_version=str(row["evaluator_version"]),
        baseline_run_id=str(row["baseline_run_id"]),
        baseline_provider=str(row["baseline_provider"]),
        baseline_model=str(row["baseline_model"]),
        baseline_model_version=row["baseline_model_version"],
        baseline_prompt_version=row["baseline_prompt_version"],
        candidate_run_id=str(row["candidate_run_id"]),
        candidate_provider=str(row["candidate_provider"]),
        candidate_model=str(row["candidate_model"]),
        candidate_model_version=row["candidate_model_version"],
        candidate_prompt_version=row["candidate_prompt_version"],
        verdict=str(row["verdict"]),
        comparable=bool(row["comparable"]),
        regressions=int(row["regressions"] or 0),
        improvements=int(row["improvements"] or 0),
        tasks=tasks,
        created_at=str(row["created_at"]),
    )


def _read_tasks(payload: Any) -> list[dict[str, Any]]:
    """The task entries of a row, read back as they were written."""
    if not isinstance(payload, str) or not payload:
        return []
    try:
        value = json.loads(payload)
    except (json.JSONDecodeError, RecursionError):
        return []
    if not isinstance(value, list):
        return []
    return [entry for entry in value if isinstance(entry, dict)]


def _has_table(conn: sqlite3.Connection, name: str) -> bool:
    """Whether the store holds a table under that name."""
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)
    ).fetchone()
    return row is not None

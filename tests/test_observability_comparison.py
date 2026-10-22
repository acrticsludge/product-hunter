"""The record of a model comparison in the store."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from test_observability_metrics import version_seven_store

from product_hunter.cli import bench_compare_command
from product_hunter.db import connect, migrate
from product_hunter.eval.cases import LabelStatus, TaskLabelStatus, TaskName
from product_hunter.eval.run import (
    ComparisonReport,
    EvalRun,
    MetricDelta,
    TaskResult,
    write_run,
)
from product_hunter.observability.comparison import (
    ComparisonError,
    list_comparisons,
    record_comparison,
)

NOW = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)


def _run(run_id: str, model: str, prompt: str) -> EvalRun:
    """A run of the benchmark: one task, blocked for want of a label."""
    task = TaskResult(
        task=TaskName.pain_extraction,
        prompt_version=prompt,
        cases=12,
        scored=0,
        unlabeled=12,
        errored=0,
        metrics={"precision": None, "recall": None},
        blocked=True,
        blocked_reason="no case in this task carries a human label",
    )
    return EvalRun(
        run_id=run_id,
        evaluator_version="eval-v1",
        dataset_version="ds-v1",
        provider="opencode-go",
        model=model,
        model_version="2026-09",
        prompt_version=prompt,
        tasks=[task],
        label_status=LabelStatus(
            dataset_version="ds-v1",
            total_cases=12,
            total_labeled=0,
            tasks=[
                TaskLabelStatus(
                    task=TaskName.pain_extraction,
                    cases=12,
                    labeled=0,
                    unlabeled=12,
                    blocked=True,
                )
            ],
        ),
    )


def _comparison() -> ComparisonReport:
    """A comparison of two runs that could not be compared."""
    return ComparisonReport(
        baseline_run_id="base-1",
        candidate_run_id="cand-1",
        baseline_model="model-a",
        candidate_model="model-b",
        baseline_provider="opencode-go",
        candidate_provider="opencode-go",
        dataset_version_match=True,
        verdict="no_change",
        metric_deltas=[
            MetricDelta(
                task="pain_extraction",
                metric="precision",
                baseline=None,
                candidate=None,
                delta=None,
                status="not_comparable",
            )
        ],
    )


def _label_free_run(run_id: str, model: str, prompt: str) -> EvalRun:
    """A run of a family that needs no label: every case was scored.

    Quote fidelity is checked against the source text, so its cases carry no
    human answer and the run reports no unlabelled case. The number of
    labelled cases is therefore zero, not the number of cases.
    """
    task = TaskResult(
        task=TaskName.quote_fidelity,
        prompt_version=prompt,
        cases=3,
        scored=3,
        unlabeled=0,
        errored=0,
        metrics={"verbatim_rate": 1.0},
        blocked=False,
    )
    return EvalRun(
        run_id=run_id,
        evaluator_version="eval-v2",
        dataset_version="ds-v2",
        provider="opencode-go",
        model=model,
        model_version="2026-10",
        prompt_version=prompt,
        tasks=[task],
        label_status=LabelStatus(
            dataset_version="ds-v2",
            total_cases=3,
            total_labeled=0,
            tasks=[
                TaskLabelStatus(
                    task=TaskName.quote_fidelity,
                    cases=3,
                    labeled=0,
                    unlabeled=3,
                    blocked=False,
                )
            ],
        ),
    )


def _store():
    conn = connect(":memory:")
    migrate(conn)
    return conn


def test_the_row_is_written_and_read_back_with_every_field_intact() -> None:
    conn = _store()

    comparison_id = record_comparison(
        conn, _run("base-1", "model-a", "extract-v3"), _run("cand-1", "model-b", "extract-v4"), _comparison(), now=NOW
    )

    assert comparison_id.startswith("cmp_")
    assert len(comparison_id) == len("cmp_") + 16
    records = list_comparisons(conn)
    assert len(records) == 1
    record = records[0]
    assert record.comparison_id == comparison_id
    assert record.dataset_version == "ds-v1"
    assert record.evaluator_version == "eval-v1"
    assert record.baseline_run_id == "base-1"
    assert record.baseline_provider == "opencode-go"
    assert record.baseline_model == "model-a"
    assert record.baseline_model_version == "2026-09"
    assert record.baseline_prompt_version == "extract-v3"
    assert record.candidate_run_id == "cand-1"
    assert record.candidate_provider == "opencode-go"
    assert record.candidate_model == "model-b"
    assert record.candidate_model_version == "2026-09"
    assert record.candidate_prompt_version == "extract-v4"
    assert record.verdict == "no_change"
    assert record.comparable is True
    assert record.regressions == 0
    assert record.improvements == 0
    assert record.created_at == NOW.isoformat()


def test_a_family_that_needs_no_label_records_no_labelled_case() -> None:
    conn = _store()

    record_comparison(
        conn,
        _label_free_run("base-1", "model-a", "quote-v1"),
        _label_free_run("cand-1", "model-b", "quote-v1"),
        _comparison(),
        now=NOW,
    )

    task = list_comparisons(conn)[0].tasks[0]
    assert task["task"] == "quote_fidelity"
    assert task["cases"] == 3
    assert task["scored"] == 3
    # Every case was scored, and not one of them carries a human label.
    assert task["labelled"] == 0


def test_the_tasks_are_written_with_the_prompt_versions_and_the_metrics() -> None:
    conn = _store()

    record_comparison(
        conn,
        _run("base-1", "model-a", "extract-v3"),
        _run("cand-1", "model-b", "extract-v4"),
        _comparison(),
        now=NOW,
    )

    task = list_comparisons(conn)[0].tasks[0]
    assert task["task"] == "pain_extraction"
    assert task["baseline_prompt_version"] == "extract-v3"
    assert task["candidate_prompt_version"] == "extract-v4"
    assert task["cases"] == 12
    assert task["scored"] == 0
    assert task["labelled"] == 0
    assert task["blocked"] is True
    assert task["metrics"] == [
        {
            "metric": "precision",
            "baseline": None,
            "candidate": None,
            "delta": None,
            "status": "not_comparable",
        }
    ]


def test_the_same_comparison_written_twice_upserts() -> None:
    conn = _store()
    baseline = _run("base-1", "model-a", "extract-v3")
    candidate = _run("cand-1", "model-b", "extract-v4")

    first = record_comparison(conn, baseline, candidate, _comparison(), now=NOW)
    second = record_comparison(conn, baseline, candidate, _comparison(), now=NOW)

    assert first == second
    records = list_comparisons(conn)
    assert len(records) == 1
    assert records[0].comparison_id == first


def test_a_comparison_against_a_database_without_the_table_is_refused(
    tmp_path,
) -> None:
    conn = version_seven_store(tmp_path)

    with pytest.raises(ComparisonError):
        record_comparison(
            conn,
            _run("base-1", "model-a", "extract-v3"),
            _run("cand-1", "model-b", "extract-v4"),
            _comparison(),
            now=NOW,
        )

    assert conn.execute("SELECT count(*) FROM sqlite_master").fetchone()[0] > 0
    with pytest.raises(ComparisonError):
        list_comparisons(conn)


def test_the_comparison_id_is_derived_from_the_two_runs_and_the_moment() -> None:
    conn = _store()

    first = record_comparison(
        conn, _run("base-1", "model-a", "extract-v3"), _run("cand-1", "model-b", "extract-v4"), _comparison(), now=NOW
    )
    later = record_comparison(
        conn,
        _run("base-1", "model-a", "extract-v3"),
        _run("cand-1", "model-b", "extract-v4"),
        _comparison(),
        now=datetime(2026, 10, 20, 13, 0, tzinfo=UTC),
    )

    assert first != later
    assert len(list_comparisons(conn)) == 2


def test_the_bench_compare_command_records_the_comparison(tmp_path, capsys) -> None:
    write_run(tmp_path / "base.json", _run("base-1", "model-a", "extract-v3"))
    write_run(tmp_path / "cand.json", _run("cand-1", "model-b", "extract-v4"))

    code = bench_compare_command(
        tmp_path / "base.json",
        tmp_path / "cand.json",
        tmp_path / "comparison.json",
        db_path=tmp_path / "product_hunter.db",
        repo_root=tmp_path,
    )

    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["comparison_id"].startswith("cmp_")
    conn = connect(tmp_path / "product_hunter.db")
    records = list_comparisons(conn)
    assert len(records) == 1
    assert records[0].baseline_run_id == "base-1"
    assert records[0].candidate_run_id == "cand-1"


def test_the_bench_compare_command_without_a_database_writes_no_row(
    tmp_path, capsys
) -> None:
    write_run(tmp_path / "base.json", _run("base-1", "model-a", "extract-v3"))
    write_run(tmp_path / "cand.json", _run("cand-1", "model-b", "extract-v4"))

    code = bench_compare_command(
        tmp_path / "base.json",
        tmp_path / "cand.json",
        tmp_path / "comparison.json",
        repo_root=tmp_path,
    )

    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert "comparison_id" not in payload
    assert not (tmp_path / "product_hunter.db").exists()

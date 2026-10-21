from __future__ import annotations

import json
from pathlib import Path

from product_hunter.cli import (
    bench_compare_command,
    bench_labels_command,
    bench_report_command,
    bench_run_command,
    build_parser,
    eval_command,
    main,
    review_sheet_command,
)
from product_hunter.eval import MAX_EVAL_FILE_BYTES
from product_hunter.evaluation import COLUMNS

TEXT = (
    "Every month I export the invoices to CSV and paste the totals into a "
    "spreadsheet by hand. It takes me a whole Saturday."
)
QUOTE = "paste the totals into a spreadsheet by hand"
PARAPHRASE = "the monthly close takes a whole weekend"


def write_dataset(root: Path) -> Path:
    document = {
        "dataset_version": "ds-v1",
        "created_at": "2026-10-21",
        "description": "No human labels yet.",
        "cases": [
            {
                "case_id": "quote-1",
                "task": "quote_fidelity",
                "case_version": 1,
                "input": {"text": TEXT},
                "expected": None,
            },
            {
                "case_id": "pain-1",
                "task": "pain_extraction",
                "case_version": 1,
                "input": {"text": TEXT, "doc_id": "doc_1"},
                "expected": {"pain_points": [{"quote": QUOTE}]},
            },
        ],
    }
    path = root / "eval" / "datasets" / "ds-v1.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, indent=2), encoding="utf-8")
    return path


def write_spec(root: Path, outputs: Path, run_id: str = "run-1") -> Path:
    spec = {
        "run_id": run_id,
        "provider": "opencode-go",
        "model": "deepseek-v4.1-flash",
        "model_version": None,
        "model_catalog": None,
        "prompt_version": "extract-v3",
        "prompt_versions": {},
        "dataset_version": "ds-v1",
        "outputs": str(outputs),
    }
    path = root / f"{run_id}-spec.json"
    path.write_text(json.dumps(spec, indent=2), encoding="utf-8")
    return path


def write_outputs(root: Path, items: list[dict]) -> Path:
    path = root / "outputs.jsonl"
    path.write_text(
        "\n".join(json.dumps(item) for item in items) + "\n", encoding="utf-8"
    )
    return path


def write_thresholds(root: Path, text: str) -> Path:
    path = root / "eval" / "thresholds.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def good_outputs() -> list[dict]:
    return [
        {"case_id": "quote-1", "output": {"quote": QUOTE}},
        {
            "case_id": "pain-1",
            "output": {"candidates": [{"quote": QUOTE, "validity": "genuine"}]},
        },
    ]


def test_bench_labels_prints_the_label_status(tmp_path, capsys):
    dataset = write_dataset(tmp_path)

    code = bench_labels_command(dataset, repo_root=tmp_path)

    assert code == 0
    status = json.loads(capsys.readouterr().out)
    assert status["dataset_version"] == "ds-v1"
    assert status["total_cases"] == 2
    assert status["total_labeled"] == 1
    by_task = {entry["task"]: entry for entry in status["tasks"]}
    assert by_task["quote_fidelity"]["cases"] == 1
    assert by_task["quote_fidelity"]["labeled"] == 0
    assert by_task["pain_extraction"]["labeled"] == 1
    assert by_task["pain_extraction"]["blocked"] is False


def test_bench_labels_writes_the_table(tmp_path, capsys):
    dataset = write_dataset(tmp_path)
    out = tmp_path / "labels.md"

    code = bench_labels_command(dataset, out, repo_root=tmp_path)

    assert code == 0
    capsys.readouterr()
    assert out.is_file()
    written = out.read_text(encoding="utf-8")
    assert "# Label status" in written
    assert "| pain_extraction | 1 | 1 | 0 | no |" in written


def test_bench_run_scores_a_dataset_and_writes_the_result(tmp_path, capsys):
    dataset = write_dataset(tmp_path)
    outputs = write_outputs(tmp_path, good_outputs())
    spec = write_spec(tmp_path, outputs)
    out = tmp_path / "run.json"

    code = bench_run_command(dataset, spec, out, repo_root=tmp_path)

    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["run_id"] == "run-1"
    assert payload["dataset_version"] == "ds-v1"
    assert payload["evaluator_version"] == "eval-v1"
    assert payload["provider"] == "opencode-go"
    assert payload["model"] == "deepseek-v4.1-flash"
    assert out.is_file()
    written = json.loads(out.read_text(encoding="utf-8"))
    assert written["run_id"] == "run-1"
    tasks = {task["task"]: task for task in written["tasks"]}
    assert tasks["quote_fidelity"]["metrics"]["verbatim_rate"] == 1.0
    assert tasks["pain_extraction"]["metrics"]["precision"] == 1.0


def test_bench_run_reports_that_no_threshold_was_checked(tmp_path, capsys):
    """The threshold file is optional, and its absence is said out loud."""
    dataset = write_dataset(tmp_path)
    outputs = write_outputs(tmp_path, good_outputs())
    spec = write_spec(tmp_path, outputs)
    out = tmp_path / "run.json"

    code = bench_run_command(dataset, spec, out, repo_root=tmp_path)

    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["passed"] is None
    assert payload["reason"] == "no threshold could be evaluated"
    assert any("no threshold file" in warning for warning in payload["warnings"])


def test_bench_run_exits_one_when_a_threshold_fails(tmp_path, capsys):
    dataset = write_dataset(tmp_path)
    outputs = write_outputs(tmp_path, good_outputs())
    spec = write_spec(tmp_path, outputs)
    write_thresholds(tmp_path, "defaults:\n  min_verbatim_rate: 1.01\n")
    out = tmp_path / "run.json"

    code = bench_run_command(dataset, spec, out, repo_root=tmp_path)

    assert code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["passed"] is False
    assert "quote_fidelity.verbatim_rate" in payload["reason"]
    assert payload["thresholds"]["failed"] == ["quote_fidelity.verbatim_rate"]


def test_bench_run_passes_a_threshold_the_model_meets(tmp_path, capsys):
    dataset = write_dataset(tmp_path)
    outputs = write_outputs(tmp_path, good_outputs())
    spec = write_spec(tmp_path, outputs)
    write_thresholds(tmp_path, "defaults:\n  min_verbatim_rate: 0.99\n")
    out = tmp_path / "run.json"

    code = bench_run_command(dataset, spec, out, repo_root=tmp_path)

    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["passed"] is True
    assert payload["reason"] == "the thresholds passed"


def test_bench_run_with_a_broken_spec_exits_one(tmp_path, capsys):
    """The spec is read by the package, so a broken one is refused there."""
    dataset = write_dataset(tmp_path)
    outputs = write_outputs(tmp_path, good_outputs())
    spec = tmp_path / "spec.json"
    spec.write_text('{"run_id": "run-1"\n', encoding="utf-8")
    out = tmp_path / "run.json"

    code = bench_run_command(dataset, spec, out, repo_root=tmp_path)

    assert code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert "spec.json" in payload["error"]


def test_bench_run_with_an_oversized_spec_exits_one(tmp_path, capsys):
    """A spec file over the cap is refused before it is read into memory."""
    dataset = write_dataset(tmp_path)
    outputs = write_outputs(tmp_path, good_outputs())
    spec = tmp_path / "spec.json"
    with spec.open("wb") as handle:
        handle.truncate(MAX_EVAL_FILE_BYTES + 1)
    out = tmp_path / "run.json"

    code = bench_run_command(dataset, spec, out, repo_root=tmp_path)

    assert code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert "byte limit" in payload["error"]


def test_bench_run_refuses_a_threshold_path_that_is_not_a_file(tmp_path, capsys):
    """A threshold path that exists but is unusable is loud, not silently read
    as an absent file that disables every gate."""
    dataset = write_dataset(tmp_path)
    outputs = write_outputs(tmp_path, good_outputs())
    spec = write_spec(tmp_path, outputs)
    (tmp_path / "eval" / "thresholds.yaml").mkdir(parents=True)
    out = tmp_path / "run.json"

    code = bench_run_command(dataset, spec, out, repo_root=tmp_path)

    assert code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False


def test_bench_compare_with_an_odd_run_file_exits_one_with_a_json_error(
    tmp_path, capsys
):
    """A run file that loads but breaks the comparison is an error, not a crash."""
    dataset = write_dataset(tmp_path)
    outputs = write_outputs(tmp_path, good_outputs())
    spec = write_spec(tmp_path, outputs)
    run = tmp_path / "run.json"
    assert bench_run_command(dataset, spec, run, repo_root=tmp_path) == 0
    capsys.readouterr()

    odd = json.loads(run.read_text(encoding="utf-8"))
    odd["tasks"][0]["metrics"]["true_positive"] = 10**3999
    odd_path = tmp_path / "odd.json"
    odd_path.write_text(json.dumps(odd), encoding="utf-8")

    code = bench_compare_command(
        run, odd_path, tmp_path / "comparison.json", repo_root=tmp_path
    )

    assert code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert "error" in payload


def test_bench_compare_exits_one_on_a_regression(tmp_path, capsys):
    dataset = write_dataset(tmp_path)
    baseline_outputs = write_outputs(tmp_path, good_outputs())
    baseline_spec = write_spec(tmp_path, baseline_outputs, run_id="baseline")
    baseline = tmp_path / "baseline.json"
    assert bench_run_command(dataset, baseline_spec, baseline, repo_root=tmp_path) == 0
    capsys.readouterr()

    candidate_outputs = write_outputs(
        tmp_path,
        [
            {"case_id": "quote-1", "output": {"quote": PARAPHRASE}},
            good_outputs()[1],
        ],
    )
    candidate_spec = write_spec(tmp_path, candidate_outputs, run_id="candidate")
    candidate = tmp_path / "candidate.json"
    assert (
        bench_run_command(dataset, candidate_spec, candidate, repo_root=tmp_path) == 0
    )
    capsys.readouterr()

    out = tmp_path / "comparison.json"
    code = bench_compare_command(baseline, candidate, out, repo_root=tmp_path)

    assert code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["verdict"] == "regression"
    assert payload["baseline_run_id"] == "baseline"
    assert payload["candidate_run_id"] == "candidate"
    assert payload["regressions"] >= 1
    assert out.is_file()
    written = json.loads(out.read_text(encoding="utf-8"))
    assert written["verdict"] == "regression"


def test_bench_compare_reports_no_change_between_two_equal_runs(tmp_path, capsys):
    dataset = write_dataset(tmp_path)
    outputs = write_outputs(tmp_path, good_outputs())
    spec = write_spec(tmp_path, outputs)
    baseline = tmp_path / "baseline.json"
    candidate = tmp_path / "candidate.json"
    assert bench_run_command(dataset, spec, baseline, repo_root=tmp_path) == 0
    assert bench_run_command(dataset, spec, candidate, repo_root=tmp_path) == 0
    capsys.readouterr()

    code = bench_compare_command(
        baseline, candidate, tmp_path / "comparison.json", repo_root=tmp_path
    )

    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["verdict"] == "no_change"
    assert payload["regressions"] == 0


def test_bench_compare_refuses_a_threshold_path_that_is_not_a_file(tmp_path, capsys):
    dataset = write_dataset(tmp_path)
    outputs = write_outputs(tmp_path, good_outputs())
    spec = write_spec(tmp_path, outputs)
    baseline = tmp_path / "baseline.json"
    candidate = tmp_path / "candidate.json"
    assert bench_run_command(dataset, spec, baseline, repo_root=tmp_path) == 0
    assert bench_run_command(dataset, spec, candidate, repo_root=tmp_path) == 0
    capsys.readouterr()
    (tmp_path / "eval" / "thresholds.yaml").mkdir(parents=True)

    code = bench_compare_command(
        baseline, candidate, tmp_path / "comparison.json", repo_root=tmp_path
    )

    assert code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False


def test_bench_report_writes_the_markdown(tmp_path, capsys):
    dataset = write_dataset(tmp_path)
    outputs = write_outputs(tmp_path, good_outputs())
    spec = write_spec(tmp_path, outputs)
    run = tmp_path / "run.json"
    assert bench_run_command(dataset, spec, run, repo_root=tmp_path) == 0
    capsys.readouterr()

    out = tmp_path / "report.md"
    code = bench_report_command(run, out, repo_root=tmp_path)

    assert code == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["ok"] is True
    assert summary["run_id"] == "run-1"
    assert summary["out"] == str(out)
    assert summary["verdict"] == "not measured"
    assert summary["comparison"] is None
    assert out.is_file()
    written = out.read_text(encoding="utf-8")
    assert "# Benchmark report" in written
    assert "opencode-go" in written
    assert "eval-v1" in written
    assert "quote_fidelity" in written


def test_bench_report_reads_a_comparison(tmp_path, capsys):
    dataset = write_dataset(tmp_path)
    outputs = write_outputs(tmp_path, good_outputs())
    spec = write_spec(tmp_path, outputs)
    baseline = tmp_path / "baseline.json"
    candidate = tmp_path / "candidate.json"
    assert bench_run_command(dataset, spec, baseline, repo_root=tmp_path) == 0
    assert bench_run_command(dataset, spec, candidate, repo_root=tmp_path) == 0
    comparison = tmp_path / "comparison.json"
    assert (
        bench_compare_command(
            baseline, candidate, comparison, repo_root=tmp_path
        )
        == 0
    )
    capsys.readouterr()

    code = bench_report_command(
        baseline, tmp_path / "report.md", comparison, repo_root=tmp_path
    )

    assert code == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["ok"] is True
    assert summary["comparison"]["verdict"] == "no_change"
    written = (tmp_path / "report.md").read_text(encoding="utf-8")
    assert "## Comparison" in written
    assert "no_change" in written


def test_bench_run_without_a_dataset_exits_one(tmp_path, capsys):
    outputs = write_outputs(tmp_path, good_outputs())
    spec = write_spec(tmp_path, outputs)

    code = bench_run_command(
        tmp_path / "absent.json", spec, tmp_path / "run.json", repo_root=tmp_path
    )

    assert code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert "absent.json" in payload["error"]


def test_bench_run_with_a_broken_spec_exits_one(tmp_path, capsys):
    dataset = write_dataset(tmp_path)
    outputs = write_outputs(tmp_path, good_outputs())
    spec = tmp_path / "spec.json"
    spec.write_text('{"run_id": "run-1"}\n', encoding="utf-8")

    code = bench_run_command(dataset, spec, tmp_path / "run.json", repo_root=tmp_path)

    assert code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False


def test_bench_labels_without_a_dataset_exits_one(tmp_path, capsys):
    code = bench_labels_command(tmp_path / "absent.json", repo_root=tmp_path)

    assert code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert "absent.json" in payload["error"]


def test_bench_report_without_a_run_exits_one(tmp_path, capsys):
    code = bench_report_command(
        tmp_path / "absent.json", tmp_path / "report.md", repo_root=tmp_path
    )

    assert code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False


def test_the_bench_group_is_in_the_parser():
    parser = build_parser()

    args = parser.parse_args(
        ["bench", "run", "--cases", "c.json", "--spec", "s.json", "--out", "o.json"]
    )

    assert args.command == "bench"
    assert args.bench_command == "run"
    assert args.cases == "c.json"
    assert args.spec == "s.json"
    assert args.out == "o.json"
    assert args.thresholds is None
    report = parser.parse_args(
        ["bench", "report", "--run", "r.json", "--out", "o.md"]
    )
    assert report.out == "o.md"
    assert report.compare is None


def test_main_dispatches_the_bench_group(tmp_path, capsys):
    dataset = write_dataset(tmp_path)

    code = main(["bench", "labels", "--cases", str(dataset)])

    assert code == 0
    status = json.loads(capsys.readouterr().out)
    assert status["dataset_version"] == "ds-v1"


def test_ph_eval_still_scores_a_filled_sheet(tmp_path, capsys):
    sheet = tmp_path / "sheet.csv"
    with sheet.open("w", encoding="utf-8", newline="") as handle:
        handle.write(",".join(COLUMNS) + "\n")
        handle.write(
            ",".join(
                [
                    "cand_1",
                    "pp_cand_1",
                    "doc_1",
                    "hn_algolia",
                    "the team pastes the totals by hand",
                    "10",
                    "46",
                    "exact",
                    "true",
                    "genuine",
                    "time_cost",
                    "3",
                    "monthly",
                    "0.9",
                    "finance",
                    "finance:paste-totals-by-hand:totals",
                    "yes",
                    "3",
                    "monthly",
                    "yes",
                    "finance:paste-totals-by-hand:totals",
                    "",
                ]
            )
            + "\n"
        )

    code = eval_command(sheet, tmp_path / "report.md", repo_root=tmp_path)

    assert code == 0
    report = json.loads(capsys.readouterr().out)
    assert report["total"] == 1
    assert report["labeled"] == 1
    assert report["quote_ok_rate"] == 1.0


def test_ph_review_sheet_without_a_run_still_exits_one(tmp_path, capsys):
    code = review_sheet_command(
        "no-such-run", tmp_path / "sheet.csv", repo_root=tmp_path
    )

    assert code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False

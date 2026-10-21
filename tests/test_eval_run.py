from __future__ import annotations

import json
from pathlib import Path

import pytest

from product_hunter.config import repo_root
from product_hunter.eval.cases import (
    MAX_EVAL_FILE_BYTES,
    EvalCase,
    EvalDataset,
    EvalError,
    TaskName,
    label_status,
    load_dataset,
)
from product_hunter.eval.report import (
    render_benchmark_markdown,
    render_label_status,
    write_benchmark_report,
)
from product_hunter.eval.run import (
    BLOCKED_NO_DENOMINATOR,
    BLOCKED_NO_LABEL,
    BLOCKED_UNREADABLE,
    RunSpec,
    Thresholds,
    check_thresholds,
    compare_runs,
    load_comparison,
    load_run,
    load_run_spec,
    load_thresholds,
    read_outputs,
    run_dataset,
    write_comparison,
    write_run,
)
from product_hunter.eval.tasks import family_metrics

TEXT = (
    "Every month I export the invoices to CSV and paste the totals into a "
    "spreadsheet by hand. It takes me a whole Saturday."
)
QUOTE = "paste the totals into a spreadsheet by hand"
PARAPHRASE = "the monthly close takes a whole weekend"


def quote_case() -> EvalCase:
    return EvalCase(
        case_id="quote-1", task="quote_fidelity", input={"text": TEXT}, expected=None
    )


def pain_case(
    labeled: bool = True, case_id: str = "pain-1", quote: str = QUOTE
) -> EvalCase:
    expected = {"pain_points": [{"quote": quote}]} if labeled else None
    return EvalCase(
        case_id=case_id,
        task="pain_extraction",
        input={"text": TEXT, "doc_id": "d1"},
        expected=expected,
    )


def rejection_case() -> EvalCase:
    return EvalCase(
        case_id="fp-1",
        task="false_positive_rejection",
        input={"text": TEXT, "span": "It takes me a whole Saturday."},
        expected={"is_pain": False},
    )


def competitor_case() -> EvalCase:
    return EvalCase(
        case_id="comp-1",
        task="competitor_identification",
        input={
            "product": "Ledger",
            "candidates": ["Ledger Pro", "QuickBooks", "Wave", "FreshBooks"],
        },
        expected={"competitors": ["QuickBooks"], "not_competitors": ["Wave"]},
    )


def counter_evidence_case(
    case_id: str, items: int, labels: list[str]
) -> EvalCase:
    return EvalCase(
        case_id=case_id,
        task="counter_evidence_classification",
        input={
            "claim": "the tool saves a day a week",
            "items": [
                {"item_id": f"{case_id}-e{index}", "text": "a piece of evidence"}
                for index in range(items)
            ],
        },
        expected={"labels": labels},
    )


def clustering_case() -> EvalCase:
    return EvalCase(
        case_id="cluster-1",
        task="clustering",
        input={"items": [{"item_id": "i1"}, {"item_id": "i2"}, {"item_id": "i3"}]},
        expected={"groups": [["i1", "i2", "i3"]]},
    )


NORMALIZATION_FIELDS = {
    "domain": "finance",
    "actor": "the bookkeeping team",
    "action": "paste invoice totals by hand",
    "object": "invoice totals",
    "consequence": "a Saturday of manual work every month",
}


def normalization_case(case_id: str = "norm-1", **changes: str) -> EvalCase:
    fields = {**NORMALIZATION_FIELDS, **changes}
    return EvalCase(
        case_id=case_id,
        task="normalization",
        input={"text": TEXT, "quote": QUOTE},
        expected=dict(fields),
    )


def make_dataset(cases: list[EvalCase], version: str = "ds-v1") -> EvalDataset:
    return EvalDataset(
        dataset_version=version, description="the test dataset", cases=cases
    )


def write_outputs(path: Path, items: list[dict]) -> Path:
    path.write_text(
        "\n".join(json.dumps(item) for item in items) + "\n", encoding="utf-8"
    )
    return path


def make_spec(outputs: Path, **changes: object) -> RunSpec:
    values: dict[str, object] = {
        "run_id": "ds-v1-deepseek-2026-10-21",
        "provider": "opencode-go",
        "model": "deepseek-v4.1-flash",
        "model_version": None,
        "model_catalog": None,
        "prompt_version": "extract-v3",
        "prompt_versions": {"clustering": "cluster-v1"},
        "dataset_version": "ds-v1",
        "outputs": str(outputs),
    }
    values.update(changes)
    return RunSpec(**values)


def write_spec_file(path: Path, outputs: Path, **changes: object) -> Path:
    """A run spec on disk, for the loader to read."""
    spec = make_spec(outputs, **changes)
    path.write_text(spec.model_dump_json(indent=2), encoding="utf-8")
    return path


def good_outputs() -> list[dict]:
    return [
        {"case_id": "quote-1", "output": {"quote": QUOTE}},
        {
            "case_id": "pain-1",
            "output": {"candidates": [{"quote": QUOTE, "validity": "genuine"}]},
        },
    ]


def make_run(tmp_path: Path, cases: list[EvalCase], items: list[dict], **spec_changes):
    known = {case.case_id for case in cases}
    outputs = write_outputs(
        tmp_path / "outputs.jsonl",
        [item for item in items if item["case_id"] in known],
    )
    dataset = make_dataset(cases)
    spec = make_spec(outputs, **spec_changes)
    return run_dataset(dataset, spec, Thresholds())


# --- the run record ---------------------------------------------------------


def test_the_run_record_carries_the_model_identity_and_the_versions(tmp_path):
    run = make_run(tmp_path, [quote_case(), pain_case()], good_outputs())

    assert run.run_id == "ds-v1-deepseek-2026-10-21"
    assert run.provider == "opencode-go"
    assert run.model == "deepseek-v4.1-flash"
    assert run.model_version is None
    assert run.model_catalog is None
    assert run.prompt_version == "extract-v3"
    assert run.evaluator_version == "eval-v1"
    assert run.dataset_version == "ds-v1"
    assert run.prompt_versions == {"clustering": "cluster-v1"}


def test_a_run_over_a_dataset_with_no_labels_blocks_every_task_that_needs_one(tmp_path):
    run = make_run(tmp_path, [quote_case(), pain_case(labeled=False)], good_outputs())

    tasks = {result.task: result for result in run.tasks}
    assert tasks[TaskName.pain_extraction].blocked is True
    assert tasks[TaskName.pain_extraction].blocked_reason
    assert tasks[TaskName.pain_extraction].metrics["precision"] is None
    assert tasks[TaskName.pain_extraction].unlabeled == 1
    assert tasks[TaskName.pain_extraction].scored == 0
    # Quote fidelity needs no label, so it is measured all the same.
    assert tasks[TaskName.quote_fidelity].blocked is False
    assert tasks[TaskName.quote_fidelity].metrics["verbatim_rate"] == 1.0
    assert tasks[TaskName.quote_fidelity].scored == 1


def test_the_result_has_no_timestamp_and_is_byte_identical_across_two_runs(tmp_path):
    first = make_run(tmp_path, [quote_case(), pain_case()], good_outputs())
    second = make_run(tmp_path, [quote_case(), pain_case()], good_outputs())

    assert first.model_dump_json(indent=2) == second.model_dump_json(indent=2)
    assert "timestamp" not in first.model_dump_json()
    assert "created_at" not in first.model_dump_json()


def test_a_run_result_round_trips_through_the_model(tmp_path):
    run = make_run(tmp_path, [quote_case(), pain_case()], good_outputs())

    path = tmp_path / "run.json"
    path.write_text(run.model_dump_json(indent=2), encoding="utf-8")
    loaded = load_run(path)

    assert loaded.model_dump_json() == run.model_dump_json()


def test_the_run_records_the_version_of_every_case_it_scored(tmp_path):
    run = make_run(tmp_path, [quote_case(), pain_case()], good_outputs())

    for result in run.tasks:
        for score in result.case_results:
            assert score.detail["case_version"] == 1


def test_an_output_for_an_unknown_case_is_refused(tmp_path):
    outputs = write_outputs(
        tmp_path / "outputs.jsonl",
        good_outputs() + [{"case_id": "nope-1", "output": {"quote": QUOTE}}],
    )

    with pytest.raises(EvalError) as excinfo:
        run_dataset(
            make_dataset([quote_case()]), make_spec(outputs), Thresholds()
        )

    assert "nope-1" in str(excinfo.value)
    assert "outputs.jsonl" in str(excinfo.value)


def test_a_case_with_no_output_is_a_case_error(tmp_path):
    run = make_run(tmp_path, [quote_case(), pain_case()], [good_outputs()[0]])

    tasks = {result.task: result for result in run.tasks}
    assert tasks[TaskName.pain_extraction].errored == 1
    errored = tasks[TaskName.pain_extraction].case_results[0]
    assert errored.error is not None
    assert "no output" in errored.error
    assert errored.passed is False


def test_unlabelled_cases_with_no_output_score_nothing(tmp_path):
    """A case with no label and no output is in both counts, so scored is
    counted from the case results and never goes negative."""
    outputs = write_outputs(tmp_path / "outputs.jsonl", [])
    cases = [
        pain_case(labeled=False, case_id=f"pain-{index}") for index in range(3)
    ]

    run = run_dataset(make_dataset(cases), make_spec(outputs), Thresholds())

    task = run.tasks[0]
    assert task.cases == 3
    assert task.scored == 0
    assert task.unlabeled == 3
    assert task.errored == 3
    assert task.scored >= 0


def test_a_run_spec_that_names_another_dataset_version_is_refused(tmp_path):
    outputs = write_outputs(tmp_path / "outputs.jsonl", good_outputs())

    with pytest.raises(EvalError):
        run_dataset(
            make_dataset([quote_case()], version="ds-v2"),
            make_spec(outputs, dataset_version="ds-v1"),
            Thresholds(),
        )


def test_a_task_with_no_prompt_version_is_named_in_the_warnings(tmp_path):
    run = make_run(tmp_path, [quote_case()], good_outputs(), prompt_version=None)

    assert run.prompt_version is None
    assert run.prompt_versions == {"clustering": "cluster-v1"}
    assert any("prompt version" in warning for warning in run.warnings)
    assert {result.prompt_version for result in run.tasks} == {None}


def test_an_output_line_may_name_its_own_prompt_version(tmp_path):
    run = make_run(
        tmp_path,
        [quote_case()],
        [
            {
                "case_id": "quote-1",
                "output": {"quote": QUOTE},
                "prompt_version": "quote-v2",
            }
        ],
    )

    tasks = {result.task: result for result in run.tasks}
    assert tasks[TaskName.quote_fidelity].prompt_version == "quote-v2"


# --- the thresholds ---------------------------------------------------------


def test_a_threshold_below_the_value_passes_and_one_above_it_fails(tmp_path):
    outputs = write_outputs(tmp_path / "outputs.jsonl", good_outputs())
    dataset = make_dataset([quote_case(), pain_case()])
    spec = make_spec(outputs)

    passing = run_dataset(
        dataset,
        spec,
        Thresholds(tasks={TaskName.quote_fidelity: {"min_verbatim_rate": 0.9}}),
    )
    failing = run_dataset(
        dataset,
        spec,
        Thresholds(tasks={TaskName.quote_fidelity: {"min_verbatim_rate": 1.01}}),
    )

    assert passing.passed is True
    assert passing.thresholds.passed is True
    assert failing.passed is False
    assert failing.thresholds.failed == ["quote_fidelity.verbatim_rate"]


def test_a_blocked_threshold_does_not_fail_the_run(tmp_path):
    outputs = write_outputs(tmp_path / "outputs.jsonl", good_outputs())
    dataset = make_dataset([quote_case(), pain_case(labeled=False)])
    spec = make_spec(outputs)

    run = run_dataset(
        dataset,
        spec,
        Thresholds(tasks={TaskName.pain_extraction: {"min_precision": 0.8}}),
    )

    check = run.thresholds.checks[0]
    assert check.status == "blocked"
    assert check.value is None
    assert run.thresholds.blocked == ["pain_extraction.precision"]
    assert run.thresholds.failed == []
    # Nothing could be evaluated, so the run has not been measured.
    assert run.passed is None


def test_a_run_with_no_evaluable_threshold_reports_no_pass(tmp_path):
    run = make_run(tmp_path, [quote_case()], good_outputs())

    assert run.thresholds.checks == []
    assert run.passed is None


def test_the_default_threshold_applies_to_a_task_that_does_not_name_it(tmp_path):
    outputs = write_outputs(tmp_path / "outputs.jsonl", good_outputs())
    dataset = make_dataset([quote_case(), pain_case()])
    spec = make_spec(outputs)

    run = run_dataset(dataset, spec, Thresholds(defaults={"min_verbatim_rate": 0.9}))

    check = run.thresholds.checks[0]
    assert check.task == "quote_fidelity"
    assert check.metric == "verbatim_rate"
    assert check.threshold == 0.9
    assert check.status == "pass"
    assert run.passed is True


def test_a_task_entry_overrides_the_default(tmp_path):
    outputs = write_outputs(tmp_path / "outputs.jsonl", good_outputs())
    dataset = make_dataset([quote_case(), pain_case()])
    spec = make_spec(outputs)

    run = run_dataset(
        dataset,
        spec,
        Thresholds(
            defaults={"min_verbatim_rate": 0.9},
            tasks={TaskName.quote_fidelity: {"min_verbatim_rate": 0.5}},
        ),
    )

    assert run.thresholds.checks[0].threshold == 0.5


def test_a_task_entry_overrides_the_default_even_when_they_spell_it_differently(
    tmp_path,
):
    outputs = write_outputs(tmp_path / "outputs.jsonl", good_outputs())
    dataset = make_dataset([quote_case(), pain_case()])
    spec = make_spec(outputs)

    run = run_dataset(
        dataset,
        spec,
        Thresholds(
            defaults={"verbatim_rate": 0.9},
            tasks={TaskName.quote_fidelity: {"min_verbatim_rate": 0.5}},
        ),
    )

    assert len(run.thresholds.checks) == 1
    assert run.thresholds.checks[0].threshold == 0.5


def test_the_threshold_file_drives_the_verdict(tmp_path):
    path = tmp_path / "thresholds.yaml"
    path.write_text(
        "version: 1\n"
        "defaults:\n"
        "  min_verbatim_rate: 0.99\n"
        "tasks:\n"
        "  quote_fidelity:\n"
        "    min_exact_rate: 0.5\n",
        encoding="utf-8",
    )
    thresholds = load_thresholds(path)

    assert thresholds.version == 1
    assert thresholds.defaults == {"min_verbatim_rate": 0.99}
    assert thresholds.tasks[TaskName.quote_fidelity] == {"min_exact_rate": 0.5}


def test_a_threshold_file_that_names_an_unknown_task_is_refused(tmp_path):
    path = tmp_path / "thresholds.yaml"
    path.write_text("tasks:\n  vibes:\n    min_precision: 0.5\n", encoding="utf-8")

    with pytest.raises(EvalError) as excinfo:
        load_thresholds(path)

    assert "thresholds.yaml" in str(excinfo.value)


def test_a_threshold_file_naming_a_metric_no_family_produces_is_refused(tmp_path):
    path = tmp_path / "thresholds.yaml"
    path.write_text(
        "defaults:\n  min_quote_fidelity_rate: 0.99\n", encoding="utf-8"
    )

    with pytest.raises(EvalError) as excinfo:
        load_thresholds(path)

    message = str(excinfo.value)
    assert "thresholds.yaml" in message
    assert "min_quote_fidelity_rate" in message
    assert "quote_fidelity_rate" in message


def test_a_threshold_file_naming_a_count_is_refused(tmp_path):
    """A count the model produced is not a gate, so the file is refused."""
    path = tmp_path / "thresholds.yaml"
    path.write_text(
        "tasks:\n  clustering:\n    min_group_count: 3\n", encoding="utf-8"
    )

    with pytest.raises(EvalError) as excinfo:
        load_thresholds(path)

    message = str(excinfo.value)
    assert "thresholds.yaml" in message
    assert "min_group_count" in message
    assert "group_count" in message


def test_a_threshold_file_naming_a_lower_is_better_rate_is_refused(tmp_path):
    """A floor reads a lower-is-better rate backwards, so it is not a gate."""
    path = tmp_path / "thresholds.yaml"
    path.write_text(
        "tasks:\n  false_positive_rejection:\n    min_false_accept_rate: 0.5\n",
        encoding="utf-8",
    )

    with pytest.raises(EvalError) as excinfo:
        load_thresholds(path)

    assert "false_accept_rate" in str(excinfo.value)


def test_a_threshold_file_naming_a_real_metric_for_an_absent_task_is_accepted(
    tmp_path,
):
    path = tmp_path / "thresholds.yaml"
    path.write_text("tasks:\n  clustering:\n    min_f1: 0.7\n", encoding="utf-8")

    thresholds = load_thresholds(path)

    assert thresholds.tasks[TaskName.clustering] == {"min_f1": 0.7}


def test_a_task_scoped_threshold_naming_a_metric_of_another_task_is_refused(
    tmp_path,
):
    """A task that cannot produce the metric can never check it."""
    path = tmp_path / "thresholds.yaml"
    path.write_text(
        "tasks:\n  pain_extraction:\n    min_verbatim_rate: 0.99\n", encoding="utf-8"
    )

    with pytest.raises(EvalError) as excinfo:
        load_thresholds(path)

    message = str(excinfo.value)
    assert "thresholds.yaml" in message
    assert "pain_extraction" in message
    assert "min_verbatim_rate" in message
    assert "verbatim_rate" in message


def test_a_defaults_key_naming_a_metric_of_some_family_is_still_accepted(tmp_path):
    """The defaults block applies to every task that has the metric."""
    path = tmp_path / "thresholds.yaml"
    path.write_text("defaults:\n  min_verbatim_rate: 0.99\n", encoding="utf-8")

    thresholds = load_thresholds(path)

    assert thresholds.defaults == {"min_verbatim_rate": 0.99}


def test_a_missing_threshold_file_is_refused(tmp_path):
    with pytest.raises(EvalError):
        load_thresholds(tmp_path / "absent.yaml")


def test_a_threshold_file_with_a_negative_tolerance_is_refused(tmp_path):
    """A negative tolerance hides every regression, so the file is refused."""
    path = tmp_path / "thresholds.yaml"
    path.write_text("tolerance: -0.1\n", encoding="utf-8")

    with pytest.raises(EvalError) as excinfo:
        load_thresholds(path)

    message = str(excinfo.value)
    assert "thresholds.yaml" in message
    assert "tolerance" in message


def test_a_threshold_file_with_a_tolerance_is_accepted(tmp_path):
    path = tmp_path / "thresholds.yaml"
    path.write_text("tolerance: 0.05\n", encoding="utf-8")

    thresholds = load_thresholds(path)

    assert thresholds.tolerance == 0.05


def test_a_blocked_threshold_check_carries_its_reason(tmp_path):
    """A null metric is blocked, and the two reasons are told apart."""
    unlabeled = make_run(
        tmp_path,
        [pain_case(labeled=False)],
        [{"case_id": "pain-1", "output": {"candidates": []}}],
    )
    limits = Thresholds(tasks={TaskName.pain_extraction: {"min_precision": 0.8}})

    report = check_thresholds(unlabeled, limits)

    check = report.checks[0]
    assert check.status == "blocked"
    assert check.reason == BLOCKED_NO_LABEL


def test_a_metric_with_no_denominator_is_blocked_for_its_own_reason(tmp_path):
    """The task has a label; the metric has nothing to divide by."""
    case = pain_case()
    case.expected = {"pain_points": []}
    run = make_run(
        tmp_path, [case], [{"case_id": "pain-1", "output": {"candidates": []}}]
    )
    limits = Thresholds(tasks={TaskName.pain_extraction: {"min_precision": 0.8}})

    report = check_thresholds(run, limits)

    check = report.checks[0]
    assert check.status == "blocked"
    assert check.reason == BLOCKED_NO_DENOMINATOR
    assert check.reason != BLOCKED_NO_LABEL


def test_a_min_precision_threshold_fails_a_model_that_found_nothing(tmp_path):
    """The gate has to be able to fail the model that extracted nothing."""
    outputs = write_outputs(
        tmp_path / "outputs.jsonl",
        [{"case_id": "pain-1", "output": {"candidates": []}}],
    )
    spec = write_spec_file(tmp_path / "spec.json", outputs)
    limits = Thresholds(tasks={TaskName.pain_extraction: {"min_precision": 0.8}})

    run = run_dataset(make_dataset([pain_case()]), load_run_spec(spec), limits)

    task = run.tasks[0]
    assert task.metrics["precision"] == 0.0
    assert task.metrics["recall"] == 0.0
    assert task.metrics["f1"] == 0.0
    check = run.thresholds.checks[0]
    assert check.status == "fail"
    assert check.value == 0.0
    assert run.passed is False
    assert run.thresholds.failed == ["pain_extraction.precision"]


def test_a_task_whose_every_case_errored_reports_the_third_reason(tmp_path):
    """No output at all is not a missing denominator."""
    outputs = write_outputs(tmp_path / "outputs.jsonl", [])
    spec = write_spec_file(tmp_path / "spec.json", outputs)
    limits = Thresholds(tasks={TaskName.pain_extraction: {"min_precision": 0.8}})

    run = run_dataset(make_dataset([pain_case()]), load_run_spec(spec), limits)

    check = run.thresholds.checks[0]
    assert check.status == "blocked"
    assert check.reason == BLOCKED_UNREADABLE
    assert check.reason != BLOCKED_NO_DENOMINATOR
    assert any(BLOCKED_UNREADABLE in warning for warning in run.warnings)


def test_the_report_shows_no_number_beside_a_zero_for_the_same_case(tmp_path):
    """A model that found nothing has a precision, and it is zero."""
    outputs = write_outputs(
        tmp_path / "outputs.jsonl",
        [{"case_id": "pain-1", "output": {"candidates": []}}],
    )
    spec = write_spec_file(tmp_path / "spec.json", outputs)
    run = run_dataset(
        make_dataset([pain_case()]), load_run_spec(spec), Thresholds()
    )

    markdown = render_benchmark_markdown(run)

    assert "| precision | 0.000 |" in markdown
    assert "| precision | n/a |" not in markdown
    assert "| recall | 0.000 |" in markdown
    assert "| f1 | 0.000 |" in markdown


def test_the_markdown_report_carries_the_blocked_reason(tmp_path):
    """A reader of the report learns why a metric was blocked."""
    outputs = write_outputs(
        tmp_path / "outputs.jsonl",
        [{"case_id": "pain-1", "output": {"candidates": []}}],
    )
    spec = write_spec_file(tmp_path / "spec.json", outputs)
    limits = Thresholds(tasks={TaskName.pain_extraction: {"min_precision": 0.8}})
    run = run_dataset(
        make_dataset([pain_case(labeled=False)]), load_run_spec(spec), limits)
    markdown = render_benchmark_markdown(run)

    assert "blocked (" in markdown
    assert BLOCKED_NO_LABEL in markdown


def test_the_run_warnings_name_the_blocked_metric(tmp_path):
    """A blocked threshold is not silently excused."""
    outputs = write_outputs(
        tmp_path / "outputs.jsonl",
        [{"case_id": "pain-1", "output": {"candidates": []}}],
    )
    cases = [pain_case(labeled=False)]
    spec = write_spec_file(tmp_path / "spec.json", outputs)
    limits = Thresholds(tasks={TaskName.pain_extraction: {"min_precision": 0.8}})

    run = run_dataset(make_dataset(cases), load_run_spec(spec), limits)

    assert run.passed is None
    assert any(
        "pain_extraction.precision" in warning
        and BLOCKED_NO_LABEL in warning
        for warning in run.warnings
    )


def test_a_threshold_on_a_count_the_model_produced_never_passes(tmp_path):
    """The defect: a wrong clustering that emitted three singletons passed on
    ``min_group_count``. A count the model produced is not a quality number, so
    it is not a gate, and the run stays unmeasured.
    """
    run = make_run(
        tmp_path,
        [clustering_case()],
        [{"case_id": "cluster-1", "output": {"groups": [["i1"], ["i2"], ["i3"]]}}],
    )

    report = check_thresholds(
        run, Thresholds(tasks={TaskName.clustering: {"min_group_count": 3}})
    )

    assert report.checks == []
    assert report.passed is None
    assert report.failed == []
    assert report.blocked == []
    # The count the model produced is exactly what the floor would have read.
    assert run.tasks[0].metrics["group_count"] == 3


def test_a_threshold_on_a_lower_is_better_rate_is_not_evaluated(tmp_path):
    """A floor on ``false_accept_rate`` would read a false accept as a pass."""
    run = make_run(
        tmp_path,
        [rejection_case()],
        [{"case_id": "fp-1", "output": {"is_pain": True, "reason": "it is slow"}}],
    )

    report = check_thresholds(
        run,
        Thresholds(
            tasks={TaskName.false_positive_rejection: {"min_false_accept_rate": 0.5}}
        ),
    )

    assert report.checks == []
    assert report.passed is None
    assert run.tasks[0].metrics["false_accept_rate"] == 1.0


# --- the comparison ---------------------------------------------------------


def test_comparing_two_runs_reports_the_metric_deltas(tmp_path):
    baseline = make_run(tmp_path, [quote_case(), pain_case()], good_outputs())
    candidate = make_run(
        tmp_path,
        [quote_case(), pain_case()],
        [
            {"case_id": "quote-1", "output": {"quote": PARAPHRASE}},
            {
                "case_id": "pain-1",
                "output": {"candidates": [{"quote": QUOTE, "validity": "genuine"}]},
            },
        ],
    )

    report = compare_runs(baseline, candidate, Thresholds())

    assert report.dataset_version_match is True
    assert report.baseline_run_id == baseline.run_id
    assert report.candidate_run_id == candidate.run_id
    assert report.baseline_model == baseline.model
    assert report.candidate_model == candidate.model
    deltas = {delta.metric: delta for delta in report.metric_deltas}
    assert deltas["verbatim_rate"].baseline == 1.0
    assert deltas["verbatim_rate"].candidate == 0.0
    assert deltas["verbatim_rate"].delta == -1.0
    assert deltas["verbatim_rate"].status == "regressed"


def test_a_metric_that_fell_is_a_regression(tmp_path):
    baseline = make_run(tmp_path, [quote_case()], good_outputs())
    candidate = make_run(
        tmp_path,
        [quote_case()],
        [{"case_id": "quote-1", "output": {"quote": PARAPHRASE}}],
    )

    report = compare_runs(baseline, candidate, Thresholds())

    assert report.verdict == "regression"
    # The count holds the regressed metrics and the regressed case.
    assert report.regressions == 3
    assert report.improvements == 0


def test_the_task_value_of_precision_is_pooled_over_the_cases(tmp_path):
    """A perfect prediction, a case with no prediction, and a wrong one."""
    outputs = write_outputs(
        tmp_path / "outputs.jsonl",
        [
            {
                "case_id": "pain-1",
                "output": {"candidates": [{"quote": QUOTE, "validity": "genuine"}]},
            },
            {"case_id": "pain-2", "output": {"candidates": []}},
            {
                "case_id": "pain-3",
                "output": {
                    "candidates": [
                        {"quote": "the report is late", "validity": "genuine"},
                        {"quote": "the invoice is wrong", "validity": "genuine"},
                    ]
                },
            },
        ],
    )
    cases = [
        pain_case(case_id="pain-1", quote=QUOTE),
        pain_case(case_id="pain-2", quote="the report is late"),
        pain_case(case_id="pain-3", quote="the invoice is wrong"),
    ]

    run = run_dataset(make_dataset(cases), make_spec(outputs), Thresholds())

    metrics = run.tasks[0].metrics
    # Pooled: two hits, one false positive, one miss. The mean of the case
    # values would have been 0.75 for precision and f1, and 0.5 for recall.
    assert metrics["true_positive"] == 2
    assert metrics["false_positive"] == 1
    assert metrics["false_negative"] == 1
    assert metrics["precision"] == pytest.approx(2 / 3)
    assert metrics["recall"] == pytest.approx(2 / 3)
    assert metrics["f1"] == pytest.approx(2 / 3)


def test_the_task_value_of_the_accuracy_of_a_classification_family_is_pooled(tmp_path):
    """Three items judged right and one judged wrong pool to three in four."""
    outputs = write_outputs(
        tmp_path / "outputs.jsonl",
        [
            {
                "case_id": "ce-1",
                "output": {"labels": ["supports", "refutes", "neutral"]},
            },
            {"case_id": "ce-2", "output": {"labels": ["refutes"]}},
        ],
    )
    cases = [
        counter_evidence_case(
            case_id="ce-1", items=3, labels=["supports", "refutes", "neutral"]
        ),
        counter_evidence_case(case_id="ce-2", items=1, labels=["supports"]),
    ]

    run = run_dataset(make_dataset(cases), make_spec(outputs), Thresholds())

    metrics = run.tasks[0].metrics
    assert metrics["n"] == 4
    # The mean of the case values would have been 0.5.
    assert metrics["accuracy"] == pytest.approx(0.75)
    # The classification family keeps the macro-over-labels meaning, so its
    # precision is the mean of the case values.
    assert metrics["precision"] == pytest.approx(0.5)


def test_a_drop_in_the_false_accept_rate_is_an_improvement(tmp_path):
    baseline = make_run(
        tmp_path,
        [rejection_case()],
        [{"case_id": "fp-1", "output": {"is_pain": True, "reason": "it is slow"}}],
    )
    candidate = make_run(
        tmp_path,
        [rejection_case()],
        [{"case_id": "fp-1", "output": {"is_pain": False, "reason": "it is fine"}}],
    )

    report = compare_runs(baseline, candidate, Thresholds())

    deltas = {delta.metric: delta for delta in report.metric_deltas}
    assert deltas["false_accept_rate"].baseline == 1.0
    assert deltas["false_accept_rate"].candidate == 0.0
    assert deltas["false_accept_rate"].status == "improved"
    assert report.verdict == "improvement"
    assert report.regressions == 0


def test_a_drop_in_precision_is_a_regression(tmp_path):
    baseline = make_run(tmp_path, [pain_case()], good_outputs())
    candidate = make_run(
        tmp_path,
        [pain_case()],
        [
            {
                "case_id": "pain-1",
                "output": {
                    "candidates": [
                        {"quote": QUOTE, "validity": "genuine"},
                        {"quote": "the report is late", "validity": "genuine"},
                    ]
                },
            }
        ],
    )

    report = compare_runs(baseline, candidate, Thresholds())

    deltas = {delta.metric: delta for delta in report.metric_deltas}
    assert deltas["precision"].baseline == 1.0
    assert deltas["precision"].candidate == 0.5
    assert deltas["precision"].status == "regressed"
    assert report.verdict == "regression"


def test_a_count_is_not_compared_and_does_not_move_the_verdict(tmp_path):
    """A candidate nobody judged is counted, and it changes no rate."""
    baseline = make_run(
        tmp_path,
        [competitor_case()],
        [{"case_id": "comp-1", "output": {"competitors": ["QuickBooks"]}}],
    )
    candidate = make_run(
        tmp_path,
        [competitor_case()],
        [
            {
                "case_id": "comp-1",
                "output": {"competitors": ["QuickBooks", "FreshBooks"]},
            }
        ],
    )

    report = compare_runs(baseline, candidate, Thresholds())

    deltas = {delta.metric: delta for delta in report.metric_deltas}
    assert deltas["unjudged_predictions"].baseline == 0.0
    assert deltas["unjudged_predictions"].candidate == 1.0
    assert deltas["unjudged_predictions"].delta == 1.0
    assert deltas["unjudged_predictions"].status == "not_comparable"
    assert deltas["precision"].status == "unchanged"
    assert report.verdict == "no_change"
    assert report.regressions == 0
    assert report.improvements == 0


def test_regressions_count_a_regressed_metric_and_a_regressed_case(tmp_path):
    baseline = make_run(tmp_path, [quote_case()], good_outputs())
    candidate = make_run(
        tmp_path,
        [quote_case()],
        [{"case_id": "quote-1", "output": {"quote": PARAPHRASE}}],
    )

    report = compare_runs(baseline, candidate, Thresholds())

    regressed = [delta for delta in report.metric_deltas if delta.status == "regressed"]
    flips = [flip for flip in report.case_flips if flip.kind == "regressed"]
    assert [delta.metric for delta in regressed] == ["verbatim_rate", "exact_rate"]
    assert len(flips) == 1
    assert report.regressions == len(regressed) + len(flips)


def test_a_case_that_flipped_from_pass_to_fail_is_a_case_flip(tmp_path):
    baseline = make_run(tmp_path, [quote_case()], good_outputs())
    candidate = make_run(
        tmp_path,
        [quote_case()],
        [{"case_id": "quote-1", "output": {"quote": PARAPHRASE}}],
    )

    report = compare_runs(baseline, candidate, Thresholds())

    assert len(report.case_flips) == 1
    flip = report.case_flips[0]
    assert flip.task == "quote_fidelity"
    assert flip.case_id == "quote-1"
    assert flip.baseline is True
    assert flip.candidate is False
    assert flip.kind == "regressed"


def test_two_identical_runs_report_no_change(tmp_path):
    baseline = make_run(tmp_path, [quote_case()], good_outputs())
    candidate = make_run(tmp_path, [quote_case()], good_outputs())

    report = compare_runs(baseline, candidate, Thresholds())

    assert report.verdict == "no_change"
    assert report.regressions == 0
    assert report.improvements == 0
    assert report.case_flips == []


def test_comparing_across_dataset_versions_is_not_comparable_and_reports_no_delta(
    tmp_path,
):
    baseline = make_run(tmp_path, [quote_case()], good_outputs())
    candidate = make_run(tmp_path, [quote_case()], good_outputs())
    candidate = candidate.model_copy(update={"dataset_version": "ds-v2"})

    report = compare_runs(baseline, candidate, Thresholds())

    assert report.verdict == "not_comparable"
    assert report.dataset_version_match is False
    assert report.metric_deltas == []
    assert report.case_flips == []
    assert report.baseline_run_id == baseline.run_id
    assert report.candidate_run_id == candidate.run_id
    assert report.notes


def test_a_changed_prompt_version_is_named_in_the_comparison(tmp_path):
    baseline = make_run(tmp_path, [quote_case()], good_outputs())
    candidate = make_run(
        tmp_path, [quote_case()], good_outputs(), prompt_version="extract-v4"
    )

    report = compare_runs(baseline, candidate, Thresholds())

    assert any("prompt version" in note for note in report.notes)


# --- the reports ------------------------------------------------------------


def test_the_report_names_the_model_under_test(tmp_path):
    run = make_run(tmp_path, [quote_case(), pain_case()], good_outputs())

    markdown = render_benchmark_markdown(run)

    assert "# Benchmark report" in markdown
    assert "opencode-go" in markdown
    assert "deepseek-v4.1-flash" in markdown
    assert "eval-v1" in markdown
    assert "ds-v1" in markdown
    assert "extract-v3" in markdown


def test_the_report_shows_a_metric_table_per_task(tmp_path):
    run = make_run(tmp_path, [quote_case(), pain_case()], good_outputs())

    markdown = render_benchmark_markdown(run)

    assert "### quote_fidelity" in markdown
    assert "| verbatim_rate | 1.000 |" in markdown
    assert "### pain_extraction" in markdown


def test_the_report_says_which_rule_each_metric_follows(tmp_path):
    run = make_run(tmp_path, [quote_case(), pain_case()], good_outputs())

    markdown = render_benchmark_markdown(run)

    # The quote fidelity family has no count behind its rates, so they are the
    # mean of the case values. The detection family pools its rates.
    assert "| verbatim_rate | 1.000 | mean |" in markdown
    assert "| precision | 1.000 | pooled |" in markdown
    assert "| true_positive | 1 | sum |" in markdown
    assert "Pooled means the metric is computed over every case" in markdown


def test_the_report_shows_a_blocked_metric_as_not_measured(tmp_path):
    run = make_run(tmp_path, [quote_case(), pain_case(labeled=False)], good_outputs())

    markdown = render_benchmark_markdown(run)

    assert "Blocked: no case in this task carries a human label." in markdown
    assert "| precision | n/a |" in markdown
    assert "n/a" in markdown


def test_the_report_says_what_was_not_measured(tmp_path):
    run = make_run(tmp_path, [quote_case(), pain_case(labeled=False)], good_outputs())

    markdown = render_benchmark_markdown(run)

    assert "## What was not measured" in markdown
    assert "pain_extraction" in markdown.split("## What was not measured")[1]


def test_the_report_names_a_task_whose_every_case_errored_as_unmeasured(tmp_path):
    """A task with no scored case has no number, so it is named as unmeasured
    rather than reported as measured above a table of ``n/a`` values."""
    run = make_run(
        tmp_path,
        [pain_case()],
        [{"case_id": "pain-1", "output": {"candidates": "not a list"}}],
    )

    markdown = render_benchmark_markdown(run)

    section = markdown.split("## What was not measured")[1]
    assert "pain_extraction" in section
    assert "no case of this task could be scored" in section
    assert "Every task of the dataset was measured." not in markdown


def test_the_report_does_not_claim_a_task_was_measured_when_there_is_none(
    tmp_path,
):
    """A dataset with no case has no task, so no task was measured."""
    run = make_run(tmp_path, [], [])

    markdown = render_benchmark_markdown(run)

    assert run.tasks == []
    section = markdown.split("## What was not measured")[1]
    assert "Every task of the dataset was measured." not in markdown
    assert "The dataset holds no task, so nothing was measured." in section


# --- the task values --------------------------------------------------------


def test_the_classification_f1_of_a_half_right_set_is_below_a_fully_right_one(
    tmp_path,
):
    """A model that got a case wrong must not read as a perfect score."""
    right = make_run(
        tmp_path,
        [
            counter_evidence_case("ce-1", 1, ["supports"]),
            counter_evidence_case("ce-2", 1, ["supports"]),
        ],
        [
            {"case_id": "ce-1", "output": {"labels": ["supports"]}},
            {"case_id": "ce-2", "output": {"labels": ["supports"]}},
        ],
    )
    half = make_run(
        tmp_path,
        [
            counter_evidence_case("ce-1", 1, ["supports"]),
            counter_evidence_case("ce-2", 1, ["supports"]),
        ],
        [
            {"case_id": "ce-1", "output": {"labels": ["supports"]}},
            {"case_id": "ce-2", "output": {"labels": ["refutes"]}},
        ],
    )

    right_f1 = right.tasks[0].metrics["f1"]
    half_f1 = half.tasks[0].metrics["f1"]

    assert right_f1 == 1.0
    assert half_f1 == 0.5
    assert half_f1 < right_f1


def test_a_min_f1_gate_on_a_classification_family_fails_on_a_wrong_case(tmp_path):
    """A model that answered half the cases wrong must not pass the gate."""
    outputs = write_outputs(
        tmp_path / "outputs.jsonl",
        [
            {"case_id": "ce-1", "output": {"labels": ["supports"]}},
            {"case_id": "ce-2", "output": {"labels": ["refutes"]}},
        ],
    )
    cases = [
        counter_evidence_case("ce-1", 1, ["supports"]),
        counter_evidence_case("ce-2", 1, ["supports"]),
    ]
    spec = write_spec_file(tmp_path / "spec.json", outputs)
    thresholds = Thresholds(tasks={"counter_evidence_classification": {"min_f1": 0.99}})

    run = run_dataset(make_dataset(cases), load_run_spec(spec), thresholds)

    assert run.tasks[0].metrics["f1"] == 0.5
    assert run.passed is False
    assert run.thresholds.failed == ["counter_evidence_classification.f1"]


def test_a_case_where_the_model_found_nothing_is_scored_not_a_gap(tmp_path):
    """The model extracted nothing, so the case is a failure, not a gap."""
    outputs = write_outputs(
        tmp_path / "outputs.jsonl",
        [{"case_id": "pain-1", "output": {"candidates": []}}],
    )
    spec = write_spec_file(tmp_path / "spec.json", outputs)

    run = run_dataset(
        make_dataset([pain_case()]), load_run_spec(spec), Thresholds()
    )

    task = run.tasks[0]
    assert task.scored == 1
    assert task.errored == 0
    assert task.case_results[0].passed is False
    assert task.case_results[0].metrics["f1"] == 0.0


def test_the_four_per_field_agreement_rates_are_averaged(tmp_path):
    """A rate is a rate: the task value is the mean, never the sum."""
    outputs = write_outputs(
        tmp_path / "outputs.jsonl",
        [
            {
                "case_id": "norm-1",
                "output": {
                    "domain": "finance",
                    "actor": "the bookkeeping team",
                    "action": "paste invoice totals by hand",
                    "object": "invoice totals",
                    "consequence": "a Saturday of manual work every month",
                },
            },
            {
                "case_id": "norm-2",
                "output": {
                    "domain": "finance",
                    "actor": "someone else",
                    "action": "paste invoice totals by hand",
                    "object": "invoice totals",
                    "consequence": "a Saturday of manual work every month",
                },
            },
        ],
    )
    cases = [
        normalization_case("norm-1"),
        normalization_case("norm-2"),
    ]
    spec = write_spec_file(tmp_path / "spec.json", outputs)

    run = run_dataset(make_dataset(cases), load_run_spec(spec), Thresholds())

    metrics = run.tasks[0].metrics
    assert metrics["actor_agreement"] == 0.5
    assert metrics["action_agreement"] == 1.0
    assert metrics["object_agreement"] == 1.0
    assert metrics["consequence_agreement"] == 1.0
    assert metrics["domain_accuracy"] == 1.0
    assert metrics["key_agreement"] == 1.0


def test_the_report_shows_the_mean_for_a_per_field_agreement_rate(tmp_path):
    outputs = write_outputs(
        tmp_path / "outputs.jsonl",
        [
            {
                "case_id": "norm-1",
                "output": {
                    "domain": "finance",
                    "actor": "the bookkeeping team",
                    "action": "paste invoice totals by hand",
                    "object": "invoice totals",
                    "consequence": "a Saturday of manual work every month",
                },
            }
        ],
    )
    spec = write_spec_file(tmp_path / "spec.json", outputs)
    run = run_dataset(
        make_dataset([normalization_case("norm-1")]),
        load_run_spec(spec),
        Thresholds(),
    )

    markdown = render_benchmark_markdown(run)

    assert "| actor_agreement | 1.000 | mean |" in markdown
    assert "| domain_accuracy | 1.000 | mean |" in markdown


def test_the_report_shows_the_comparison_when_there_is_one(tmp_path):
    baseline = make_run(tmp_path, [quote_case()], good_outputs())
    candidate = make_run(
        tmp_path,
        [quote_case()],
        [{"case_id": "quote-1", "output": {"quote": PARAPHRASE}}],
    )
    comparison = compare_runs(baseline, candidate, Thresholds())

    markdown = render_benchmark_markdown(candidate, comparison)

    assert "## Comparison" in markdown
    assert "regression" in markdown
    assert "quote-1" in markdown


def test_the_label_status_report_counts_the_cases(tmp_path):
    run = make_run(tmp_path, [quote_case(), pain_case()], good_outputs())

    markdown = render_label_status(run.label_status)

    assert "# Label status" in markdown
    assert "| Task | Cases | Labelled | Unlabelled | Blocked |" in markdown
    assert "| pain_extraction | 1 | 1 | 0 | no |" in markdown
    # The quote fidelity family needs no label, so it is never blocked.
    assert "| quote_fidelity | 1 | 0 | 1 | no |" in markdown


def test_the_report_is_written_to_a_file(tmp_path):
    run = make_run(tmp_path, [quote_case()], good_outputs())
    path = tmp_path / "out" / "report.md"

    write_benchmark_report(run, path)

    assert path.is_file()
    assert "# Benchmark report" in path.read_text(encoding="utf-8")


# --- the files --------------------------------------------------------------


def test_read_outputs_reads_one_object_per_line(tmp_path):
    path = write_outputs(tmp_path / "outputs.jsonl", good_outputs())

    items = read_outputs(path)

    assert [item.case_id for item in items] == ["quote-1", "pain-1"]


def test_read_outputs_refuses_a_broken_line(tmp_path):
    path = tmp_path / "outputs.jsonl"
    path.write_text('{"case_id": "quote-1"\n', encoding="utf-8")

    with pytest.raises(EvalError) as excinfo:
        read_outputs(path)

    assert "outputs.jsonl" in str(excinfo.value)
    assert "line 1" in str(excinfo.value)


def test_read_outputs_refuses_a_deeply_nested_line(tmp_path):
    path = tmp_path / "outputs.jsonl"
    path.write_text("[" * 50000 + "]" * 50000 + "\n", encoding="utf-8")

    with pytest.raises(EvalError) as excinfo:
        read_outputs(path)

    assert "outputs.jsonl" in str(excinfo.value)


def test_check_thresholds_can_be_called_on_its_own(tmp_path):
    run = make_run(tmp_path, [quote_case()], good_outputs())

    report = check_thresholds(run, Thresholds(defaults={"min_verbatim_rate": 0.5}))

    assert report.passed is True
    assert report.checks[0].metric == "verbatim_rate"


def test_the_shipped_threshold_file_is_accepted():
    """Every key of the shipped file must name a metric a family produces."""
    path = repo_root() / "eval" / "thresholds.yaml"

    thresholds = load_thresholds(path)

    known: set[str] = set()
    for task in TaskName:
        known.update(family_metrics(task))
    keys = list(thresholds.defaults)
    for block in thresholds.tasks.values():
        keys.extend(block)
    assert keys
    for key in keys:
        metric = key[len("min_") :] if key.startswith("min_") else key
        assert metric in known, key


def test_the_shipped_dataset_carries_no_label():
    """No label is ever written by the code, so the shipped dataset has none."""
    path = repo_root() / "eval" / "datasets" / "ds-v1.json"

    dataset = load_dataset(path)

    assert dataset.dataset_version == "ds-v1"
    assert dataset.cases
    for case in dataset.cases:
        assert case.expected is None, case.case_id
    assert label_status(dataset).total_labeled == 0


def test_a_run_round_trips_through_its_file(tmp_path):
    run = make_run(tmp_path, [quote_case()], good_outputs())
    path = tmp_path / "run.json"

    write_run(path, run)
    loaded = load_run(path)

    assert loaded.model_dump_json() == run.model_dump_json()


def test_a_comparison_round_trips_through_its_file(tmp_path):
    baseline = make_run(tmp_path, [quote_case()], good_outputs())
    candidate = make_run(tmp_path, [quote_case()], good_outputs())
    comparison = compare_runs(baseline, candidate, Thresholds())
    path = tmp_path / "comparison.json"

    write_comparison(path, comparison)
    loaded = load_comparison(path)

    assert loaded.model_dump_json() == comparison.model_dump_json()


def test_the_label_status_and_the_run_result_agree_about_quote_fidelity(tmp_path):
    """One artifact, one answer: the family that needs no label is not blocked."""
    run = make_run(tmp_path, [quote_case()], good_outputs())

    status = {entry.task: entry for entry in run.label_status.tasks}
    results = {result.task: result for result in run.tasks}

    assert status[TaskName.quote_fidelity].labeled == 0
    assert status[TaskName.quote_fidelity].blocked is False
    assert results[TaskName.quote_fidelity].blocked is False


def test_the_comparison_notes_a_task_whose_prompt_version_is_unknown(tmp_path):
    """Two runs whose prompt is unknown must not look clean."""
    baseline = make_run(
        tmp_path, [quote_case()], good_outputs(), prompt_version=None
    )
    candidate = make_run(
        tmp_path, [quote_case()], good_outputs(), prompt_version=None
    )

    report = compare_runs(baseline, candidate, Thresholds())

    assert any(
        "prompt version" in note and "unknown" in note for note in report.notes
    )


def test_the_report_separates_a_fully_measured_task_from_a_partly_measured_one(
    tmp_path,
):
    """A task with one case of two scored is not a task that was measured."""
    outputs = write_outputs(
        tmp_path / "outputs.jsonl",
        [
            {
                "case_id": "pain-1",
                "output": {"candidates": [{"quote": QUOTE, "validity": "genuine"}]},
            },
            {"case_id": "pain-2", "output": {"candidates": "not a list"}},
        ],
    )
    cases = [
        pain_case(case_id="pain-1"),
        pain_case(case_id="pain-2", quote="the report is late"),
    ]
    spec = write_spec_file(tmp_path / "spec.json", outputs)

    run = run_dataset(make_dataset(cases), load_run_spec(spec), Thresholds())

    markdown = render_benchmark_markdown(run)

    assert "Only part of these tasks was measured" in markdown
    assert "pain_extraction (1 of 2 cases scored)" in markdown
    assert "Every task of the dataset was measured." not in markdown


def test_load_comparison_refuses_a_broken_file(tmp_path):
    path = tmp_path / "comparison.json"
    path.write_text("not json", encoding="utf-8")

    with pytest.raises(EvalError) as excinfo:
        load_comparison(path)

    assert "comparison.json" in str(excinfo.value)


def test_load_comparison_refuses_a_missing_file(tmp_path):
    with pytest.raises(EvalError):
        load_comparison(tmp_path / "absent.json")


# --- the size cap -----------------------------------------------------------


def test_the_outputs_file_over_the_cap_is_refused(tmp_path):
    path = write_outputs(tmp_path / "outputs.jsonl", good_outputs())

    with pytest.raises(EvalError) as excinfo:
        read_outputs(path, limit=10)

    message = str(excinfo.value)
    assert "outputs.jsonl" in message
    assert "byte limit" in message
    assert "10" in message


def test_the_threshold_file_over_the_cap_is_refused(tmp_path):
    path = tmp_path / "thresholds.yaml"
    path.write_text("defaults:\n  min_verbatim_rate: 0.99\n", encoding="utf-8")

    with pytest.raises(EvalError) as excinfo:
        load_thresholds(path, limit=10)

    message = str(excinfo.value)
    assert "thresholds.yaml" in message
    assert "byte limit" in message
    assert "10" in message


def test_a_run_spec_file_over_the_cap_is_refused(tmp_path):
    path = write_spec_file(tmp_path / "spec.json", tmp_path / "outputs.jsonl")

    with pytest.raises(EvalError) as excinfo:
        load_run_spec(path, limit=10)

    message = str(excinfo.value)
    assert "spec.json" in message
    assert "byte limit" in message
    assert "10" in message


def test_a_run_spec_that_is_not_valid_json_is_refused(tmp_path):
    path = tmp_path / "spec.json"
    path.write_text('{"run_id": "run-1"\n', encoding="utf-8")

    with pytest.raises(EvalError) as excinfo:
        load_run_spec(path)

    message = str(excinfo.value)
    assert "spec.json" in message
    assert "JSON" in message


def test_a_run_spec_that_is_not_a_run_spec_is_refused(tmp_path):
    path = tmp_path / "spec.json"
    path.write_text(json.dumps({"run_id": "run-1"}), encoding="utf-8")

    with pytest.raises(EvalError) as excinfo:
        load_run_spec(path)

    message = str(excinfo.value)
    assert "spec.json" in message
    assert "run spec" in message
    assert len(message) < 2000


def test_a_valid_run_spec_loads_and_round_trips(tmp_path):
    path = write_spec_file(tmp_path / "spec.json", tmp_path / "outputs.jsonl")

    spec = load_run_spec(path)

    assert spec.run_id == "ds-v1-deepseek-2026-10-21"
    assert spec.provider == "opencode-go"
    assert spec.model == "deepseek-v4.1-flash"
    assert spec.dataset_version == "ds-v1"
    assert RunSpec.model_validate_json(path.read_text(encoding="utf-8")) == spec


def test_a_dataset_version_mismatch_gives_a_short_message(tmp_path):
    outputs = write_outputs(tmp_path / "outputs.jsonl", good_outputs())
    spec = write_spec_file(
        tmp_path / "spec.json", outputs, dataset_version="ds-v2"
    )
    loaded = load_run_spec(spec)

    with pytest.raises(EvalError) as excinfo:
        run_dataset(
            make_dataset([quote_case()], version="ds-v1"), loaded, Thresholds()
        )

    message = str(excinfo.value)
    assert len(message) < 1000
    assert "ds-v2" in message
    assert "ds-v1" in message


def test_a_long_dataset_version_mismatch_gives_a_short_message(tmp_path):
    """The version comes out of a file, so the message must be cut."""
    outputs = write_outputs(tmp_path / "outputs.jsonl", good_outputs())
    long_version = "ds-v2" + "x" * 50_000
    spec = write_spec_file(
        tmp_path / "spec.json", outputs, dataset_version=long_version
    )
    loaded = load_run_spec(spec)

    with pytest.raises(EvalError) as excinfo:
        run_dataset(
            make_dataset([quote_case()], version="ds-v1"), loaded, Thresholds()
        )

    message = str(excinfo.value)
    assert len(message) < 1000
    assert "cut at" in message


def test_a_run_result_file_over_the_cap_is_refused(tmp_path):
    path = tmp_path / "run.json"
    path.write_text(json.dumps({"run_id": "run-1"}), encoding="utf-8")

    with pytest.raises(EvalError) as excinfo:
        load_run(path, limit=10)

    message = str(excinfo.value)
    assert "run.json" in message
    assert "byte limit" in message
    assert "10" in message


def test_a_comparison_file_over_the_cap_is_refused(tmp_path):
    path = tmp_path / "comparison.json"
    path.write_text(json.dumps({"verdict": "no_change"}), encoding="utf-8")

    with pytest.raises(EvalError) as excinfo:
        load_comparison(path, limit=10)

    message = str(excinfo.value)
    assert "comparison.json" in message
    assert "byte limit" in message
    assert "10" in message


def test_a_run_result_just_under_the_cap_is_accepted(tmp_path):
    run = make_run(tmp_path, [quote_case()], good_outputs())
    path = tmp_path / "run.json"
    write_run(path, run)

    loaded = load_run(path, limit=path.stat().st_size)

    assert loaded.run_id == run.run_id


def test_a_run_result_over_the_default_cap_is_refused(tmp_path):
    """The shipped cap is 32 MiB, and a file over it never reaches the parser."""
    path = tmp_path / "big.json"
    with path.open("wb") as handle:
        handle.truncate(MAX_EVAL_FILE_BYTES + 1)

    with pytest.raises(EvalError) as excinfo:
        load_run(path)

    assert f"{MAX_EVAL_FILE_BYTES} byte limit" in str(excinfo.value)


# --- the byte order mark ----------------------------------------------------


def test_a_file_with_a_byte_order_mark_loads(tmp_path):
    """A file an operator made by hand on Windows carries a BOM."""
    bom = "\ufeff"
    dataset = tmp_path / "ds-v1.json"
    dataset.write_text(
        bom
        + json.dumps(
            {
                "dataset_version": "ds-v1",
                "cases": [
                    {
                        "case_id": "quote-1",
                        "task": "quote_fidelity",
                        "input": {"text": TEXT},
                        "expected": None,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    assert load_dataset(dataset).dataset_version == "ds-v1"

    outputs = tmp_path / "outputs.jsonl"
    outputs.write_text(
        bom + json.dumps({"case_id": "quote-1", "output": {"quote": QUOTE}}) + "\n",
        encoding="utf-8",
    )
    assert read_outputs(outputs)[0].case_id == "quote-1"

    spec = tmp_path / "spec.json"
    spec.write_text(
        bom
        + json.dumps(
            {
                "run_id": "run-1",
                "provider": "opencode-go",
                "model": "deepseek-v4.1-flash",
                "outputs": str(outputs),
            }
        ),
        encoding="utf-8",
    )
    assert load_run_spec(spec).run_id == "run-1"

    thresholds = tmp_path / "thresholds.yaml"
    thresholds.write_text(
        bom + "defaults:\n  min_verbatim_rate: 0.99\n", encoding="utf-8"
    )
    assert load_thresholds(thresholds).defaults == {"min_verbatim_rate": 0.99}

    run = make_run(tmp_path, [quote_case()], good_outputs())
    run_path = tmp_path / "run.json"
    write_run(run_path, run)
    run_path.write_text(bom + run_path.read_text(encoding="utf-8"), encoding="utf-8")
    assert load_run(run_path).run_id == run.run_id

    comparison_path = tmp_path / "comparison.json"
    write_comparison(comparison_path, compare_runs(run, run, Thresholds()))
    comparison_path.write_text(
        bom + comparison_path.read_text(encoding="utf-8"), encoding="utf-8"
    )
    assert load_comparison(comparison_path).verdict == "no_change"


# --- the capped messages ----------------------------------------------------


def test_an_output_naming_a_very_long_case_id_is_refused_with_a_short_message(
    tmp_path,
):
    """A case id comes out of the outputs file, so the message must be cut."""
    long_id = "c" * 50_000
    outputs = write_outputs(
        tmp_path / "outputs.jsonl",
        [{"case_id": long_id, "output": {"quote": QUOTE}}],
    )

    with pytest.raises(EvalError) as excinfo:
        run_dataset(make_dataset([quote_case()]), make_spec(outputs), Thresholds())

    message = str(excinfo.value)
    assert len(message) < 1000
    assert "cut at" in message


def test_a_dataset_case_with_a_very_long_id_and_no_output_is_a_short_message(
    tmp_path,
):
    """A case id comes out of the dataset, so the message must be cut."""
    long_id = "c" * 50_000
    case = EvalCase(
        case_id=long_id, task="quote_fidelity", input={"text": TEXT}, expected=None
    )
    outputs = write_outputs(tmp_path / "outputs.jsonl", [])

    run = run_dataset(make_dataset([case]), make_spec(outputs), Thresholds())

    error = run.tasks[0].case_results[0].error
    assert error is not None
    assert len(error) < 1000
    assert "cut at" in error


def test_a_threshold_naming_a_very_long_metric_is_refused_with_a_short_message(
    tmp_path,
):
    """A threshold key comes out of the threshold file, so it must be cut."""
    path = tmp_path / "thresholds.yaml"
    # The YAML scanner reads a line of about a kilobyte, so this is the
    # longest key a threshold file can hold.
    path.write_text(f"defaults:\n  min_{'m' * 900}: 0.5\n", encoding="utf-8")

    with pytest.raises(EvalError) as excinfo:
        load_thresholds(path)

    message = str(excinfo.value)
    assert len(message) < 1000
    assert "cut at" in message


def test_a_threshold_key_the_yaml_scanner_cannot_hold_is_refused_with_a_short_message(
    tmp_path,
):
    """A key over the scanner's line limit is refused before it is read."""
    path = tmp_path / "thresholds.yaml"
    path.write_text(f"defaults:\n  min_{'m' * 50_000}: 0.5\n", encoding="utf-8")

    with pytest.raises(EvalError) as excinfo:
        load_thresholds(path)

    message = str(excinfo.value)
    assert len(message) < 1000

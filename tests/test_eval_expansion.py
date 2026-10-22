"""The evaluation expansion: two new families, a new dataset, a new version.

The rules these tests hold the change to:

- the new families load and are blocked, because no case carries a human label
- the entity resolution scorer reads a grouping and the scoring consistency
  scorer reads two scores, and the tolerance comes from the case
- two runs scored by different evaluators are not comparable
- the same dataset and the same answers produce the same bytes
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from product_hunter.config import repo_root
from product_hunter.eval.cases import (
    EVALUATOR_VERSION,
    EvalCase,
    EvalDataset,
    EvalError,
    TaskName,
    label_status,
    load_dataset,
)
from product_hunter.eval.run import (
    EvalRun,
    RunSpec,
    Thresholds,
    compare_runs,
    load_thresholds,
    run_dataset,
)
from product_hunter.eval.tasks import (
    ScoreError,
    family_metrics,
    score_case,
    score_entity_resolution,
    score_scoring_consistency,
)

DS_V1 = repo_root() / "eval" / "datasets" / "ds-v1.json"
DS_V2 = repo_root() / "eval" / "datasets" / "ds-v2.json"
THRESHOLDS = repo_root() / "eval" / "thresholds.yaml"

TEXT = (
    "Every month I export the invoices to CSV and paste the totals into a "
    "spreadsheet by hand. It takes me a whole Saturday."
)
QUOTE = "paste the totals into a spreadsheet by hand"

# The accounts of one case of the entity resolution family. The first two come
# from one source and the third from another, so the case can hold the
# question the family asks: are any of these the same person?
ACCOUNTS = [
    {
        "account_id": "au_1",
        "handle": "agumonkey",
        "display_name": "agumonkey",
        "source_id": "hn_algolia",
    },
    {
        "account_id": "au_2",
        "handle": "sama",
        "display_name": "sama",
        "source_id": "hn_algolia",
    },
    {
        "account_id": "au_3",
        "handle": "hoodmane",
        "display_name": "hoodmane",
        "source_id": "github_issues",
    },
]


def entity_case(case_id: str = "er-1") -> EvalCase:
    return EvalCase(
        case_id=case_id,
        task="entity_resolution",
        input={"accounts": ACCOUNTS},
        expected={"groups": [["au_1", "au_2"], ["au_3"]]},
    )


def scoring_case(case_id: str = "sc-1", tolerance: int = 10) -> EvalCase:
    return EvalCase(
        case_id=case_id,
        task="scoring_consistency",
        input={
            "opportunity": "the export covers one account at a time",
            "tolerance": tolerance,
        },
        expected={"potential": 70, "confidence": 60},
    )


def quote_case() -> EvalCase:
    return EvalCase(
        case_id="quote-1", task="quote_fidelity", input={"text": TEXT}, expected=None
    )


def write_outputs(path: Path, items: list[dict]) -> Path:
    path.write_text(
        "\n".join(json.dumps(item) for item in items) + "\n", encoding="utf-8"
    )
    return path


def make_spec(outputs: Path, dataset_version: str = "ds-v2") -> RunSpec:
    return RunSpec(
        run_id="ds-v2-run-2026-10-22",
        provider="opencode-go",
        model="deepseek-v4.1-flash",
        prompt_version="extract-v3",
        dataset_version=dataset_version,
        outputs=str(outputs),
    )


def make_run(tmp_path: Path) -> EvalRun:
    """One run over one dataset, with the answers of one quote case."""
    outputs = write_outputs(
        tmp_path / "outputs.jsonl", [{"case_id": "quote-1", "output": {"quote": QUOTE}}]
    )
    dataset = EvalDataset(
        dataset_version="ds-v2", description="the test dataset", cases=[quote_case()]
    )
    return run_dataset(dataset, make_spec(outputs), Thresholds())


def well_formed_output(case: EvalCase) -> dict:
    """One answer in the shape the family of the case defines."""
    task = case.task
    if task is TaskName.quote_fidelity:
        return {"quote": " ".join(case.input["text"].split()[:6])}
    if task is TaskName.pain_extraction:
        return {
            "candidates": [
                {"quote": "the totals are pasted by hand", "validity": "genuine"}
            ]
        }
    if task is TaskName.false_positive_rejection:
        return {"is_pain": True, "reason": "the text describes the work by hand"}
    if task is TaskName.severity:
        return {"severity": 3}
    if task is TaskName.normalization:
        return {
            "domain": "finance",
            "actor": "the bookkeeping team",
            "action": "paste invoice totals by hand",
            "object": "invoice totals",
            "consequence": "a Saturday of manual work every month",
        }
    if task is TaskName.duplicate_detection:
        return {
            "groups": [[document["doc_id"] for document in case.input["documents"]]]
        }
    if task is TaskName.author_independence:
        return {
            "groups": [[mention["mention_id"] for mention in case.input["mentions"]]]
        }
    if task is TaskName.clustering:
        return {"groups": [[item["item_id"] for item in case.input["items"]]]}
    if task is TaskName.competitor_identification:
        return {"competitors": []}
    if task is TaskName.counter_evidence_classification:
        return {"labels": ["supports"]}
    if task is TaskName.entity_resolution:
        return {
            "groups": [[account["account_id"] for account in case.input["accounts"]]]
        }
    if task is TaskName.scoring_consistency:
        return {"potential": 60, "confidence": 50}
    raise AssertionError(f"the family {task.value} has no answer shape")


# --- the dataset -------------------------------------------------------------


def test_the_new_dataset_loads_and_holds_the_seed_cases_and_the_new_ones():
    first = load_dataset(DS_V1)
    second = load_dataset(DS_V2)

    assert second.dataset_version == "ds-v2"
    assert second.created_at
    assert second.description
    # Three seed cases for each of the two new families.
    assert len(second.cases) == len(first.cases) + 6


def test_the_seed_cases_of_the_first_dataset_are_in_the_second_one_unchanged():
    first = load_dataset(DS_V1)
    second = load_dataset(DS_V2)
    kept = {case.case_id: case for case in second.cases}

    for case in first.cases:
        assert case.case_id in kept
        other = kept[case.case_id]
        assert other.case_version == case.case_version
        assert other.task is case.task
        assert other.input == case.input
        assert other.expected == case.expected


def test_the_first_dataset_is_untouched():
    """A dataset is a versioned artefact, so the old one stays readable."""
    dataset = load_dataset(DS_V1)

    assert dataset.dataset_version == "ds-v1"
    assert len(dataset.cases) == 30


def test_no_case_of_the_new_dataset_carries_a_label():
    dataset = load_dataset(DS_V2)

    for case in dataset.cases:
        assert case.expected is None, case.case_id
    assert label_status(dataset).total_labeled == 0


def test_the_seed_cases_of_the_new_families_hold_real_captured_data():
    """The accounts are the ones the capture recorded, under the ids the
    store derives from the source and the handle."""
    dataset = load_dataset(DS_V2)
    accounts = [
        account
        for case in dataset.cases
        if case.task is TaskName.entity_resolution
        for account in case.input["accounts"]
    ]
    handles = {account["handle"] for account in accounts}
    sources = {account["source_id"] for account in accounts}

    assert handles == {"agumonkey", "hoodmane", "sama", "dmon", "pg"}
    assert sources == {"hn_algolia", "github_issues"}
    assert all(account["account_id"].startswith("au_") for account in accounts)


def test_the_scoring_consistency_cases_carry_a_tolerance():
    dataset = load_dataset(DS_V2)
    cases = [
        case for case in dataset.cases if case.task is TaskName.scoring_consistency
    ]

    assert cases
    for case in cases:
        assert case.input["tolerance"] == 10
        assert case.input["opportunity"]


def test_the_label_status_reports_the_two_new_families_as_blocked():
    dataset = load_dataset(DS_V2)

    status = label_status(dataset)
    by_task = {entry.task: entry for entry in status.tasks}

    for task in (TaskName.entity_resolution, TaskName.scoring_consistency):
        assert by_task[task].cases == 3
        assert by_task[task].labeled == 0
        assert by_task[task].unlabeled == 3
        assert by_task[task].blocked is True


# --- the run over the new dataset --------------------------------------------


def test_every_family_but_quote_fidelity_is_blocked_and_measures_nothing(
    tmp_path,
):
    dataset = load_dataset(DS_V2)
    items = [
        {"case_id": case.case_id, "output": well_formed_output(case)}
        for case in dataset.cases
    ]
    outputs = write_outputs(tmp_path / "outputs.jsonl", items)

    run = run_dataset(dataset, make_spec(outputs), load_thresholds(THRESHOLDS))

    tasks = {result.task: result for result in run.tasks}
    for task in TaskName:
        result = tasks[task]
        if task is TaskName.quote_fidelity:
            assert result.blocked is False
            assert result.metrics["verbatim_rate"] == 1.0
            continue
        assert result.blocked is True, task
        assert result.blocked_reason == "no case in this task carries a human label"
        assert all(value is None for value in result.metrics.values()), task


def test_no_case_of_the_new_dataset_errors_against_a_well_formed_answer(tmp_path):
    dataset = load_dataset(DS_V2)
    items = [
        {"case_id": case.case_id, "output": well_formed_output(case)}
        for case in dataset.cases
    ]
    outputs = write_outputs(tmp_path / "outputs.jsonl", items)

    run = run_dataset(dataset, make_spec(outputs), load_thresholds(THRESHOLDS))

    for result in run.tasks:
        for score in result.case_results:
            assert score.error is None, score.case_id


def test_the_new_families_produce_the_metrics_they_are_registered_with():
    assert family_metrics(TaskName.entity_resolution) == (
        "true_positive",
        "false_positive",
        "false_negative",
        "precision",
        "recall",
        "f1",
        "group_count",
        "expected_group_count",
    )
    assert family_metrics(TaskName.scoring_consistency) == (
        "n",
        "potential_exact_rate",
        "confidence_exact_rate",
        "potential_within_rate",
        "confidence_within_rate",
        "potential_mean_absolute_error",
        "confidence_mean_absolute_error",
    )


# --- the entity resolution scorer --------------------------------------------


def test_a_perfect_grouping_scores_one():
    case = entity_case()

    metrics = score_entity_resolution(case, {"groups": [["au_1", "au_2"], ["au_3"]]})

    assert metrics["true_positive"] == 1
    assert metrics["false_positive"] == 0
    assert metrics["false_negative"] == 0
    assert metrics["precision"] == 1.0
    assert metrics["recall"] == 1.0
    assert metrics["f1"] == 1.0
    assert metrics["group_count"] == 2
    assert metrics["expected_group_count"] == 2


def test_a_wrong_grouping_scores_zero():
    """The label says the first two accounts are one person, and the model
    split them, so the model found nothing of what was there."""
    case = entity_case()

    metrics = score_entity_resolution(case, {"groups": [["au_1"], ["au_2"], ["au_3"]]})

    assert metrics["true_positive"] == 0
    assert metrics["false_negative"] == 1
    assert metrics["precision"] == 0.0
    assert metrics["recall"] == 0.0
    assert metrics["f1"] == 0.0


def test_a_group_naming_an_account_the_case_does_not_hold_is_a_case_error():
    case = entity_case()

    with pytest.raises(ScoreError) as excinfo:
        score_entity_resolution(case, {"groups": [["au_1", "au_9"]]})

    assert "au_9" in str(excinfo.value)


def test_a_malformed_grouping_is_recorded_as_a_case_error():
    case = entity_case()

    score = score_case(case, {"groups": [["au_9"]]})

    assert score.passed is False
    assert score.error is not None
    assert "au_9" in score.error
    assert all(value is None for value in score.metrics.values())


# --- the scoring consistency scorer ------------------------------------------


def test_a_score_inside_the_tolerance_counts():
    case = scoring_case(tolerance=10)

    metrics = score_scoring_consistency(case, {"potential": 75, "confidence": 65})

    assert metrics["n"] == 1
    assert metrics["potential_exact_rate"] == 0.0
    assert metrics["confidence_exact_rate"] == 0.0
    assert metrics["potential_within_rate"] == 1.0
    assert metrics["confidence_within_rate"] == 1.0
    assert metrics["potential_mean_absolute_error"] == 5.0
    assert metrics["confidence_mean_absolute_error"] == 5.0


def test_a_score_outside_the_tolerance_does_not_count():
    case = scoring_case(tolerance=10)

    metrics = score_scoring_consistency(case, {"potential": 90, "confidence": 30})

    assert metrics["potential_within_rate"] == 0.0
    assert metrics["confidence_within_rate"] == 0.0
    assert metrics["potential_mean_absolute_error"] == 20.0
    assert metrics["confidence_mean_absolute_error"] == 30.0


def test_the_tolerance_lives_in_the_case():
    """The same two scores agree under one tolerance and not under another."""
    output = {"potential": 90, "confidence": 35}

    wide = score_scoring_consistency(scoring_case(tolerance=25), output)
    narrow = score_scoring_consistency(scoring_case(tolerance=10), output)

    assert wide["potential_within_rate"] == 1.0
    assert wide["confidence_within_rate"] == 1.0
    assert narrow["potential_within_rate"] == 0.0
    assert narrow["confidence_within_rate"] == 0.0


def test_a_score_outside_the_scale_is_a_case_error():
    case = scoring_case()

    with pytest.raises(ScoreError) as excinfo:
        score_scoring_consistency(case, {"potential": 140, "confidence": 60})

    assert "140" in str(excinfo.value)


# --- the two version rules ----------------------------------------------------


def test_the_evaluator_version_moved():
    assert EVALUATOR_VERSION == "eval-v2"


def test_two_runs_scored_by_different_evaluators_are_not_comparable(tmp_path):
    baseline = make_run(tmp_path)
    candidate = baseline.model_copy(update={"evaluator_version": "eval-v1"})

    report = compare_runs(baseline, candidate, Thresholds())

    assert report.verdict == "not_comparable"
    assert report.metric_deltas == []
    assert report.case_flips == []
    assert any("eval-v1" in note and "eval-v2" in note for note in report.notes)


def test_two_runs_over_different_datasets_are_still_not_comparable(tmp_path):
    baseline = make_run(tmp_path)
    candidate = baseline.model_copy(update={"dataset_version": "ds-v1"})

    report = compare_runs(baseline, candidate, Thresholds())

    assert report.verdict == "not_comparable"
    assert report.dataset_version_match is False
    assert report.metric_deltas == []


def test_a_run_that_differs_in_both_versions_names_both(tmp_path):
    baseline = make_run(tmp_path)
    candidate = baseline.model_copy(
        update={"dataset_version": "ds-v1", "evaluator_version": "eval-v1"}
    )

    report = compare_runs(baseline, candidate, Thresholds())

    assert report.verdict == "not_comparable"
    assert report.metric_deltas == []
    assert len(report.notes) == 2


def test_the_same_dataset_and_the_same_answers_produce_the_same_bytes(tmp_path):
    first = make_run(tmp_path)
    second = make_run(tmp_path)

    assert first.model_dump_json(indent=2) == second.model_dump_json(indent=2)
    assert first.evaluator_version == "eval-v2"


# --- the thresholds ----------------------------------------------------------


def test_the_shipped_threshold_file_carries_the_new_floors():
    thresholds = load_thresholds(THRESHOLDS)

    assert thresholds.tasks[TaskName.entity_resolution] == {"min_f1": 0.80}
    assert thresholds.tasks[TaskName.scoring_consistency] == {
        "min_potential_within_rate": 0.70,
        "min_confidence_within_rate": 0.70,
    }


def test_a_floor_on_a_lower_is_better_metric_of_the_new_family_is_refused(
    tmp_path,
):
    """A floor reads a lower-is-better error backwards, so it is not a gate."""
    path = tmp_path / "thresholds.yaml"
    path.write_text(
        "tasks:\n  scoring_consistency:\n    min_potential_mean_absolute_error: 5\n",
        encoding="utf-8",
    )

    with pytest.raises(EvalError) as excinfo:
        load_thresholds(path)

    message = str(excinfo.value)
    assert "thresholds.yaml" in message
    assert "potential_mean_absolute_error" in message


def test_a_floor_on_a_count_of_the_new_family_is_refused(tmp_path):
    path = tmp_path / "thresholds.yaml"
    path.write_text(
        "tasks:\n  entity_resolution:\n    min_group_count: 2\n", encoding="utf-8"
    )

    with pytest.raises(EvalError) as excinfo:
        load_thresholds(path)

    assert "group_count" in str(excinfo.value)

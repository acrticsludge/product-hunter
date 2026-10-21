from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from product_hunter.config import repo_root
from product_hunter.eval.cases import (
    EVALUATOR_VERSION,
    MAX_EVAL_FILE_BYTES,
    EvalCase,
    EvalDataset,
    EvalError,
    LabelStatus,
    TaskLabelStatus,
    TaskName,
    brief_value,
    dataset_cases_for,
    label_status,
    load_dataset,
)

DATASET = {
    "dataset_version": "ds-v1",
    "created_at": "2026-10-21",
    "description": "Seed cases. No human labels yet.",
    "cases": [
        {
            "case_id": "quote-hn-0001",
            "task": "quote_fidelity",
            "case_version": 1,
            "input": {"text": "the team pastes the totals by hand"},
            "expected": None,
        },
        {
            "case_id": "pain-hn-0001",
            "task": "pain_extraction",
            "input": {"text": "the team pastes the totals by hand", "doc_id": "d1"},
            "expected": {"pain_points": [{"quote": "pastes the totals by hand"}]},
        },
    ],
}


def write_json(path: Path, document: object) -> Path:
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def test_a_valid_dataset_loads_and_keeps_its_version(tmp_path):
    path = write_json(tmp_path / "ds-v1.json", DATASET)

    dataset = load_dataset(path)

    assert isinstance(dataset, EvalDataset)
    assert dataset.dataset_version == "ds-v1"
    assert dataset.description == "Seed cases. No human labels yet."
    assert len(dataset.cases) == 2
    assert dataset.cases[0].case_id == "quote-hn-0001"
    assert dataset.cases[0].case_version == 1


def test_the_evaluator_version_is_a_constant():
    assert EVALUATOR_VERSION == "eval-v1"


def test_a_missing_file_raises(tmp_path):
    with pytest.raises(EvalError) as excinfo:
        load_dataset(tmp_path / "absent.json")

    assert "absent.json" in str(excinfo.value)


def test_a_json_syntax_error_raises_and_names_the_file(tmp_path):
    path = tmp_path / "broken.json"
    path.write_text('{"dataset_version": "ds-v1",', encoding="utf-8")

    with pytest.raises(EvalError) as excinfo:
        load_dataset(path)

    assert "broken.json" in str(excinfo.value)
    assert "JSON" in str(excinfo.value)


def test_a_deeply_nested_dataset_is_refused(tmp_path):
    path = tmp_path / "deep.json"
    path.write_text("[" * 50000 + "]" * 50000, encoding="utf-8")

    with pytest.raises(EvalError) as excinfo:
        load_dataset(path)

    assert "deep.json" in str(excinfo.value)


def test_a_document_that_is_not_an_object_raises(tmp_path):
    path = write_json(tmp_path / "list.json", [DATASET])

    with pytest.raises(EvalError) as excinfo:
        load_dataset(path)

    assert "list.json" in str(excinfo.value)


def test_a_dataset_without_a_version_raises(tmp_path):
    document = {
        key: value for key, value in DATASET.items() if key != "dataset_version"
    }
    path = write_json(tmp_path / "no-version.json", document)

    with pytest.raises(EvalError) as excinfo:
        load_dataset(path)

    assert "dataset_version" in str(excinfo.value)


def test_a_dataset_without_a_cases_list_raises(tmp_path):
    document = {key: value for key, value in DATASET.items() if key != "cases"}
    path = write_json(tmp_path / "no-cases.json", document)

    with pytest.raises(EvalError) as excinfo:
        load_dataset(path)

    assert "cases" in str(excinfo.value)


def test_a_duplicate_case_id_raises(tmp_path):
    document = json.loads(json.dumps(DATASET))
    document["cases"].append(dict(document["cases"][0]))
    path = write_json(tmp_path / "duplicate.json", document)

    with pytest.raises(EvalError) as excinfo:
        load_dataset(path)

    assert "quote-hn-0001" in str(excinfo.value)


def test_an_unknown_task_raises(tmp_path):
    document = json.loads(json.dumps(DATASET))
    document["cases"][0]["task"] = "clustering_by_vibes"
    path = write_json(tmp_path / "unknown-task.json", document)

    with pytest.raises(EvalError) as excinfo:
        load_dataset(path)

    assert "clustering_by_vibes" in str(excinfo.value)


def test_a_case_without_an_input_object_raises(tmp_path):
    document = json.loads(json.dumps(DATASET))
    document["cases"][0]["input"] = "just a string"
    path = write_json(tmp_path / "bad-input.json", document)

    with pytest.raises(EvalError) as excinfo:
        load_dataset(path)

    assert "input" in str(excinfo.value)


def test_an_unknown_dataset_field_raises(tmp_path):
    document = json.loads(json.dumps(DATASET))
    document["cases"][0]["expected_v2"] = {"pain_points": []}
    path = write_json(tmp_path / "extra-field.json", document)

    with pytest.raises(EvalError) as excinfo:
        load_dataset(path)

    assert "extra-field.json" in str(excinfo.value)


def test_a_case_with_a_missing_expected_block_loads_as_unlabeled(tmp_path):
    document = json.loads(json.dumps(DATASET))
    del document["cases"][1]["expected"]
    path = write_json(tmp_path / "unlabeled.json", document)

    dataset = load_dataset(path)

    assert dataset.cases[1].expected is None
    assert dataset.cases[0].expected is None


def test_the_label_status_counts_the_labeled_cases(tmp_path):
    path = write_json(tmp_path / "ds-v1.json", DATASET)

    status = label_status(load_dataset(path))

    assert isinstance(status, LabelStatus)
    assert status.dataset_version == "ds-v1"
    assert status.total_cases == 2
    assert status.total_labeled == 1
    by_task = {entry.task: entry for entry in status.tasks}
    assert by_task[TaskName.pain_extraction].labeled == 1
    assert by_task[TaskName.pain_extraction].unlabeled == 0
    assert by_task[TaskName.pain_extraction].blocked is False
    assert by_task[TaskName.quote_fidelity].labeled == 0
    assert by_task[TaskName.quote_fidelity].unlabeled == 1


def test_a_task_with_no_labeled_case_is_blocked(tmp_path):
    path = write_json(tmp_path / "ds-v1.json", DATASET)

    status = label_status(load_dataset(path))

    by_task = {entry.task: entry for entry in status.tasks}
    # The quote fidelity family needs no label, so it is never blocked.
    assert by_task[TaskName.quote_fidelity].blocked is False
    assert by_task[TaskName.severity].blocked is True


def test_a_task_with_no_case_at_all_is_reported_with_a_count_of_zero(tmp_path):
    path = write_json(tmp_path / "ds-v1.json", DATASET)

    status = label_status(load_dataset(path))

    by_task = {entry.task: entry for entry in status.tasks}
    assert by_task[TaskName.clustering].cases == 0
    assert by_task[TaskName.clustering].labeled == 0
    assert by_task[TaskName.clustering].unlabeled == 0
    assert by_task[TaskName.clustering].blocked is True
    # Every family is reported, not only the ones the dataset holds.
    assert len(status.tasks) == len(TaskName)


def test_the_label_status_is_stable_across_the_order_of_the_tasks(tmp_path):
    document = json.loads(json.dumps(DATASET))
    document["cases"].reverse()
    path = write_json(tmp_path / "reversed.json", document)

    status = label_status(load_dataset(path))

    assert [entry.task for entry in status.tasks] == list(TaskName)
    assert status.total_cases == 2
    assert status.total_labeled == 1


def test_dataset_cases_for_returns_the_cases_of_one_task(tmp_path):
    path = write_json(tmp_path / "ds-v1.json", DATASET)
    dataset = load_dataset(path)

    cases = dataset_cases_for(dataset, TaskName.quote_fidelity)

    assert [case.case_id for case in cases] == ["quote-hn-0001"]


def test_dataset_cases_for_refuses_an_unknown_task(tmp_path):
    path = write_json(tmp_path / "ds-v1.json", DATASET)
    dataset = load_dataset(path)

    with pytest.raises(EvalError):
        dataset_cases_for(dataset, "clustering_by_vibes")


def test_a_case_refuses_an_unknown_field():
    with pytest.raises(ValidationError):
        EvalCase(
            case_id="c1",
            task="quote_fidelity",
            input={},
            case_verison=1,
        )


def test_a_task_label_status_refuses_an_unknown_field():
    with pytest.raises(ValidationError):
        TaskLabelStatus(
            task="quote_fidelity",
            cases=1,
            labeled=0,
            unlabeled=1,
            blocked=True,
            extra=1,
        )


def test_no_dataset_in_the_repository_carries_a_label():
    """No label is ever written by the code, so a shipped dataset has none."""
    directory = repo_root() / "eval" / "datasets"
    files = sorted(directory.glob("*.json")) if directory.is_dir() else []
    if not files:
        pytest.skip("the seed dataset is not in the repository yet")
    for path in files:
        dataset = load_dataset(path)
        for case in dataset.cases:
            assert case.expected is None, f"{path}: {case.case_id} carries a label"


# --- the size cap -----------------------------------------------------------


def test_a_dataset_file_over_the_cap_is_refused(tmp_path):
    path = tmp_path / "ds-v1.json"
    path.write_text(json.dumps(DATASET), encoding="utf-8")

    with pytest.raises(EvalError) as excinfo:
        load_dataset(path, limit=10)

    message = str(excinfo.value)
    assert "ds-v1.json" in message
    assert "byte limit" in message
    assert "10" in message


def test_a_dataset_just_under_the_cap_is_accepted(tmp_path):
    path = tmp_path / "ds-v1.json"
    path.write_text(json.dumps(DATASET), encoding="utf-8")
    size = path.stat().st_size

    dataset = load_dataset(path, limit=size)

    assert dataset.dataset_version == "ds-v1"


def test_a_dataset_over_the_default_cap_is_refused(tmp_path):
    """The shipped cap is 32 MiB, and a file over it never reaches the parser."""
    path = tmp_path / "big.json"
    with path.open("wb") as handle:
        handle.truncate(MAX_EVAL_FILE_BYTES + 1)

    with pytest.raises(EvalError) as excinfo:
        load_dataset(path)

    assert f"{MAX_EVAL_FILE_BYTES} byte limit" in str(excinfo.value)


def test_a_validation_error_carrying_a_large_value_produces_a_short_message(tmp_path):
    """An unknown field name is echoed by pydantic, so the message must be cut."""
    document = json.loads(json.dumps(DATASET))
    document["cases"][0]["z" * 200_000] = 1
    path = tmp_path / "ds-v1.json"
    path.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(EvalError) as excinfo:
        load_dataset(path)

    message = str(excinfo.value)
    assert len(message) < 2000
    assert "cut at" in message


# --- the value cap ----------------------------------------------------------


def test_brief_value_never_raises_on_an_integer_python_cannot_print():
    """Python refuses to print an integer of more than 4300 digits."""
    text = brief_value(10**5000)

    assert len(text) < 200
    assert "too large to print" in text


def test_brief_value_still_cuts_a_normal_string():
    text = brief_value("x" * 500)

    assert len(text) < 200
    assert "cut at" in text


def test_brief_value_leaves_a_normal_value_alone():
    assert brief_value(42) == "42"
    assert brief_value("pain") == "'pain'"

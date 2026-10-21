from __future__ import annotations

import pytest
from pydantic import ValidationError

from product_hunter.eval import tasks
from product_hunter.eval.cases import EvalCase, TaskName
from product_hunter.eval.tasks import (
    CaseScore,
    ScoreError,
    agreement_metrics,
    classification_metrics,
    detection_metrics,
    pairwise_metrics,
    score_case,
    score_author_independence,
    score_clustering,
    score_competitor_identification,
    score_counter_evidence_classification,
    score_duplicate_detection,
    score_false_positive_rejection,
    score_normalization,
    score_pain_extraction,
    score_quote_fidelity,
    score_severity,
)

TEXT = (
    "Every month I export the invoices to CSV and paste the totals into a "
    "spreadsheet by hand. It takes me a whole Saturday."
)
QUOTE = "paste the totals into a spreadsheet by hand"


def make_case(task: str, case_input: dict, expected: dict | None) -> EvalCase:
    return EvalCase(case_id=f"{task}-1", task=task, input=case_input, expected=expected)


# The known input and the known output of every family, over which the exact
# metric values are asserted.
def family_case(task: TaskName) -> tuple[EvalCase, dict]:
    if task is TaskName.pain_extraction:
        case = make_case(
            "pain_extraction",
            {
                "text": "the team’s totals are pasted by hand every month",
                "doc_id": "d1",
            },
            {"pain_points": [{"quote": "the team’s totals are pasted by hand"}]},
        )
        return case, {
            "candidates": [
                {
                    "quote": "the team's totals are pasted by hand",
                    "validity": "genuine",
                },
                {"quote": "the report is late", "validity": "not_pain"},
            ]
        }
    if task is TaskName.false_positive_rejection:
        case = make_case(
            "false_positive_rejection",
            {"text": TEXT, "span": "It takes me a whole Saturday."},
            {"is_pain": False},
        )
        return case, {
            "is_pain": True,
            "reason": "the text says the export takes a whole Saturday",
        }
    if task is TaskName.quote_fidelity:
        case = make_case("quote_fidelity", {"text": TEXT}, None)
        return case, {"quote": QUOTE}
    if task is TaskName.severity:
        case = make_case(
            "severity",
            {"text": TEXT, "quote": QUOTE},
            {"severity": 3},
        )
        return case, {"severity": 4}
    if task is TaskName.normalization:
        fields = {
            "domain": "finance",
            "actor": "the bookkeeping team",
            "action": "paste invoice totals by hand",
            "object": "invoice totals",
            "consequence": "a Saturday of manual work every month",
        }
        return make_case(
            "normalization", {"text": TEXT, "quote": QUOTE}, dict(fields)
        ), dict(fields)
    if task is TaskName.duplicate_detection:
        case = make_case(
            "duplicate_detection",
            {
                "documents": [
                    {"doc_id": "d1", "text_sha256": "a" * 64},
                    {"doc_id": "d2", "text_sha256": "b" * 64},
                    {"doc_id": "d3", "text_sha256": "a" * 64},
                ]
            },
            {"groups": [["d1", "d3"], ["d2"]]},
        )
        return case, {"groups": [["d1", "d3"], ["d2"]]}
    if task is TaskName.author_independence:
        case = make_case(
            "author_independence",
            {
                "mentions": [
                    {"mention_id": "m1", "author_key": "a1"},
                    {"mention_id": "m2", "author_key": "a1"},
                    {"mention_id": "m3", "author_key": "a2"},
                ],
                "propagation_edges": [{"from": "m1", "to": "m2"}],
            },
            {"groups": [["m1", "m2"], ["m3"]]},
        )
        return case, {"groups": [["m1", "m2"], ["m3"]]}
    if task is TaskName.clustering:
        case = make_case(
            "clustering",
            {
                "items": [
                    {"item_id": "i1", "text": "the export is slow"},
                    {"item_id": "i2", "text": "the export takes an hour"},
                    {"item_id": "i3", "text": "the invoice template is wrong"},
                ]
            },
            {"groups": [["i1", "i2"], ["i3"]]},
        )
        return case, {"groups": [["i1", "i2", "i3"]]}
    if task is TaskName.competitor_identification:
        case = make_case(
            "competitor_identification",
            {
                "product": "Ledger",
                "candidates": ["Ledger Pro", "QuickBooks", "Wave", "FreshBooks"],
            },
            {"competitors": ["QuickBooks"], "not_competitors": ["Wave"]},
        )
        return case, {"competitors": ["QuickBooks", "FreshBooks"]}
    case = make_case(
        "counter_evidence_classification",
        {
            "claim": "the tool saves a day a week",
            "items": [
                {"item_id": "e1", "text": "I stopped doing the monthly close by hand"},
                {"item_id": "e2", "text": "the tool costs more than my accountant"},
                {"item_id": "e3", "text": "the tool was released in 2019"},
            ],
        },
        {"labels": ["supports", "refutes", "neutral"]},
    )
    return case, {"labels": ["supports", "refutes", "neutral"]}


# --- the ten families -------------------------------------------------------


def test_pain_extraction_scores_the_genuine_quotes():
    case, output = family_case(TaskName.pain_extraction)

    metrics = score_pain_extraction(case, output)

    # The curly quote folds to the same canonical string, and the candidate
    # the model did not call genuine is not a prediction.
    assert metrics["true_positive"] == 1
    assert metrics["false_positive"] == 0
    assert metrics["false_negative"] == 0
    assert metrics["precision"] == 1.0
    assert metrics["recall"] == 1.0
    assert metrics["f1"] == 1.0
    assert metrics["unjudged"] == 0


def test_pain_extraction_counts_a_missed_pain_point():
    case, output = family_case(TaskName.pain_extraction)
    case.expected = {
        "pain_points": [
            {"quote": "the team’s totals are pasted by hand"},
            {"quote": "the report is late"},
        ]
    }

    metrics = score_pain_extraction(case, output)

    assert metrics["true_positive"] == 1
    assert metrics["false_negative"] == 1
    assert metrics["recall"] == 0.5
    assert metrics["f1"] == pytest.approx(2 / 3)


def test_false_positive_rejection_scores_the_judgement():
    case, output = family_case(TaskName.false_positive_rejection)

    metrics = score_false_positive_rejection(case, output)

    assert metrics["n"] == 1
    assert metrics["accuracy"] == 0.0
    assert metrics["precision"] == 0.0
    assert metrics["recall"] == 0.0
    assert metrics["false_accept_rate"] == 1.0
    assert metrics["false_reject_rate"] is None


def test_quote_fidelity_scores_the_quote_against_the_text():
    case, output = family_case(TaskName.quote_fidelity)

    metrics = score_quote_fidelity(case, output)

    assert metrics["verbatim_rate"] == 1.0
    assert metrics["exact_rate"] == 1.0
    assert metrics["canonical_rate"] == 0.0
    assert metrics["failures"] == 0


def test_quote_fidelity_fails_a_paraphrase():
    case, output = family_case(TaskName.quote_fidelity)
    output = {"quote": "the monthly close takes a whole weekend"}

    metrics = score_quote_fidelity(case, output)

    assert metrics["verbatim_rate"] == 0.0
    assert metrics["exact_rate"] == 0.0
    assert metrics["canonical_rate"] == 0.0
    assert metrics["failures"] == 1


def test_severity_scores_the_agreement():
    case, output = family_case(TaskName.severity)

    metrics = score_severity(case, output)

    assert metrics["n"] == 1
    assert metrics["exact"] == 0
    assert metrics["exact_rate"] == 0.0
    assert metrics["within_one"] == 1
    assert metrics["within_one_rate"] == 1.0
    assert metrics["mean_absolute_error"] == 1.0


def test_normalization_scores_the_key():
    case, output = family_case(TaskName.normalization)

    metrics = score_normalization(case, output)

    assert metrics["domain_accuracy"] == 1.0
    assert metrics["key_agreement"] == 1.0
    assert metrics["actor_agreement"] == 1.0
    assert metrics["action_agreement"] == 1.0
    assert metrics["object_agreement"] == 1.0
    assert metrics["consequence_agreement"] == 1.0


def test_normalization_sees_a_wrong_domain():
    case, output = family_case(TaskName.normalization)
    output["domain"] = "operations"

    metrics = score_normalization(case, output)

    assert metrics["domain_accuracy"] == 0.0
    assert metrics["key_agreement"] == 0.0


def test_duplicate_detection_scores_the_groups():
    case, output = family_case(TaskName.duplicate_detection)

    metrics = score_duplicate_detection(case, output)

    assert metrics["predicted_pairs"] == 1
    assert metrics["expected_pairs"] == 1
    assert metrics["true_positive"] == 1
    assert metrics["false_positive"] == 0
    assert metrics["false_negative"] == 0
    assert metrics["f1"] == 1.0
    assert metrics["group_count"] == 2
    assert metrics["expected_group_count"] == 2


def test_author_independence_counts_the_independent_authors():
    case, output = family_case(TaskName.author_independence)

    metrics = score_author_independence(case, output)

    assert metrics["true_positive"] == 1
    assert metrics["f1"] == 1.0
    assert metrics["independent_author_count"] == 2
    assert metrics["expected_author_count"] == 2


def test_clustering_scores_the_groups():
    case, output = family_case(TaskName.clustering)

    metrics = score_clustering(case, output)

    assert metrics["predicted_pairs"] == 3
    assert metrics["expected_pairs"] == 1
    assert metrics["true_positive"] == 1
    assert metrics["false_positive"] == 2
    assert metrics["false_negative"] == 0
    assert metrics["precision"] == pytest.approx(1 / 3)
    assert metrics["recall"] == 1.0
    assert metrics["f1"] == 0.5
    assert metrics["group_count"] == 1
    assert metrics["expected_group_count"] == 2


def test_competitor_identification_keeps_unjudged_predictions_out_of_precision():
    case, output = family_case(TaskName.competitor_identification)

    metrics = score_competitor_identification(case, output)

    assert metrics["true_positive"] == 1
    assert metrics["false_positive"] == 0
    assert metrics["false_negative"] == 0
    assert metrics["precision"] == 1.0
    assert metrics["recall"] == 1.0
    assert metrics["unjudged_predictions"] == 1


def test_counter_evidence_classification_scores_the_labels():
    case, output = family_case(TaskName.counter_evidence_classification)

    metrics = score_counter_evidence_classification(case, output)

    assert metrics["n"] == 3
    assert metrics["accuracy"] == 1.0
    assert metrics["precision"] == 1.0
    assert metrics["recall"] == 1.0
    assert metrics["f1"] == 1.0


def test_counter_evidence_classification_reports_a_confusion_matrix():
    case, output = family_case(TaskName.counter_evidence_classification)
    output = {"labels": ["supports", "supports", "neutral"]}

    metrics = score_counter_evidence_classification(case, output)

    assert metrics["accuracy"] == pytest.approx(2 / 3)
    # The label the model got wrong contributes 0.0 to the macro, so the
    # macro is 0.5 and not the 0.75 of the labels that happened to be right.
    assert metrics["precision"] == pytest.approx(0.5)
    assert metrics["recall"] == pytest.approx(2 / 3)
    assert metrics["f1"] == pytest.approx(5 / 9)
    assert metrics["confusion"] == {
        "supports": {"supports": 1},
        "refutes": {"supports": 1},
        "neutral": {"neutral": 1},
    }


# --- the primitives ---------------------------------------------------------


def test_classification_metrics_are_empty_when_there_was_nothing_to_compare():
    metrics = classification_metrics([], [], ["pain", "not_pain"])

    assert metrics["n"] == 0
    assert metrics["accuracy"] is None
    assert metrics["precision"] is None
    assert metrics["recall"] is None
    assert metrics["f1"] is None


def test_detection_metrics_are_empty_when_there_was_nothing_to_compare():
    metrics = detection_metrics([], [], [])

    assert metrics["true_positive"] == 0
    assert metrics["false_positive"] == 0
    assert metrics["false_negative"] == 0
    assert metrics["precision"] is None
    assert metrics["recall"] is None
    assert metrics["f1"] is None


def test_pairwise_metrics_count_the_pairs_inside_a_group():
    metrics = pairwise_metrics([["a", "b", "c"]], [["a", "b"], ["c"]])

    assert metrics["predicted_pairs"] == 3
    assert metrics["expected_pairs"] == 1
    assert metrics["true_positive"] == 1
    assert metrics["false_positive"] == 2
    assert metrics["false_negative"] == 0
    assert metrics["group_count"] == 1
    assert metrics["expected_group_count"] == 2


def test_agreement_metrics_count_the_gap():
    metrics = agreement_metrics([3, 2], [2, 2])

    assert metrics["n"] == 2
    assert metrics["exact"] == 1
    assert metrics["exact_rate"] == 0.5
    assert metrics["within_one"] == 2
    assert metrics["within_one_rate"] == 1.0
    assert metrics["mean_absolute_error"] == 0.5


# --- the dispatcher ---------------------------------------------------------


def test_a_case_with_no_label_yields_none_metrics_and_no_pass():
    case, output = family_case(TaskName.pain_extraction)
    case.expected = None

    score = score_case(case, output)

    assert isinstance(score, CaseScore)
    assert score.passed is None
    assert score.error is None
    assert score.metrics
    assert all(value is None for value in score.metrics.values())


def test_a_case_with_no_label_on_quote_fidelity_still_yields_a_metric():
    case, output = family_case(TaskName.quote_fidelity)

    score = score_case(case, output)

    assert score.passed is True
    assert score.metrics["verbatim_rate"] == 1.0
    assert score.metrics["failures"] == 0


def test_a_malformed_output_raises_a_score_error():
    case, _ = family_case(TaskName.pain_extraction)

    with pytest.raises(ScoreError):
        score_pain_extraction(case, {"candidates": "not a list"})


def test_a_malformed_output_is_recorded_as_a_case_error():
    case, _ = family_case(TaskName.pain_extraction)

    score = score_case(
        case, {"candidates": [{"quote": "a quote", "validity": "great"}]}
    )

    assert score.passed is False
    assert score.error is not None
    assert "great" in score.error
    assert all(value is None for value in score.metrics.values())


def test_a_domain_outside_the_controlled_values_is_a_case_error():
    case, output = family_case(TaskName.normalization)
    output["domain"] = "finance-plus"

    score = score_case(case, output)

    assert score.passed is False
    assert score.error is not None
    assert "finance-plus" in score.error


def test_a_label_outside_the_three_is_a_case_error():
    case, output = family_case(TaskName.counter_evidence_classification)
    output = {"labels": ["supports", "refutes", "maybe"]}

    score = score_case(case, output)

    assert score.passed is False
    assert score.error is not None
    assert "maybe" in score.error


def test_a_group_naming_an_unknown_item_is_a_case_error():
    case, output = family_case(TaskName.clustering)
    output = {"groups": [["i1", "i9"]]}

    score = score_case(case, output)

    assert score.passed is False
    assert score.error is not None
    assert "i9" in score.error


def test_an_unknown_task_name_is_impossible_by_construction():
    with pytest.raises(ValidationError):
        EvalCase(case_id="c1", task="clustering_by_vibes", input={})


def test_the_same_case_and_output_scored_twice_gives_the_same_result():
    case, output = family_case(TaskName.pain_extraction)

    first = score_case(case, output)
    second = score_case(case, output)

    assert first.model_dump_json() == second.model_dump_json()


def test_each_family_produces_exactly_its_registered_metrics():
    for task in TaskName:
        case, output = family_case(task)
        score = score_case(case, output)
        assert set(score.metrics) == set(tasks._FAMILIES[task].metrics), task


def test_a_task_error_echoing_a_long_model_value_produces_a_short_message():
    """A model output is data, not a log line, so the message stays short."""
    case = EvalCase(
        case_id="fp-1",
        task="false_positive_rejection",
        input={"text": TEXT, "span": "the export is slow"},
        expected={"is_pain": False},
    )
    long_value = "x" * 20_000

    score = score_case(
        case, {"is_pain": long_value, "reason": "the text complains"}
    )

    assert score.error is not None
    assert len(score.error) < 400
    assert "cut at" in score.error


# --- the zero division convention -------------------------------------------


def test_the_classification_f1_of_a_wrong_answer_is_zero():
    """A label the model got wrong scores 0.0, so the macro cannot hide it."""
    metrics = classification_metrics(["pain"], ["not_pain"], ("pain", "not_pain"))

    assert metrics["accuracy"] == 0.0
    assert metrics["precision"] == 0.0
    assert metrics["recall"] == 0.0
    assert metrics["f1"] == 0.0


def test_a_label_nobody_used_stays_out_of_the_macro():
    metrics = classification_metrics(
        ["pain"], ["pain"], ("pain", "not_pain", "other")
    )

    # The label nobody used keeps its empty value in the breakdown.
    assert metrics["per_label"]["not_pain"]["precision"] is None
    assert metrics["per_label"]["other"]["f1"] is None
    # And the macro runs over the label the comparison touched.
    assert metrics["precision"] == 1.0
    assert metrics["recall"] == 1.0
    assert metrics["f1"] == 1.0


def test_the_detection_precision_and_recall_of_a_model_that_found_nothing_are_zero():
    """The comparison had something to compare and the model matched none."""
    metrics = detection_metrics([], ["a"])

    assert metrics["true_positive"] == 0
    assert metrics["false_negative"] == 1
    # A zero denominator gives 0.0, the same rule the classification
    # primitive follows, so a gate on the precision can fail this model.
    assert metrics["precision"] == 0.0
    assert metrics["recall"] == 0.0
    assert metrics["f1"] == 0.0


def test_the_detection_f1_is_empty_when_there_was_nothing_on_either_side():
    metrics = detection_metrics([], [])

    assert metrics["precision"] is None
    assert metrics["recall"] is None
    assert metrics["f1"] is None


def test_the_pairwise_shares_follow_the_same_rule():
    """The grouping family agrees with the detection family."""
    metrics = pairwise_metrics([["a"]], [])

    assert metrics["true_positive"] == 0
    assert metrics["false_positive"] == 0
    assert metrics["false_negative"] == 0
    assert metrics["precision"] is None
    assert metrics["recall"] is None
    assert metrics["f1"] is None

    wrong = pairwise_metrics([], [["a", "b"]])

    assert wrong["precision"] == 0.0
    assert wrong["recall"] == 0.0
    assert wrong["f1"] == 0.0


def test_a_case_where_the_model_found_nothing_is_a_failure_not_a_gap():
    case, _ = family_case(TaskName.pain_extraction)

    score = score_case(case, {"candidates": []})

    assert score.metrics["f1"] == 0.0
    assert score.passed is False
    assert score.error is None


# --- the pair cap -----------------------------------------------------------


def test_a_grouping_case_over_the_pair_cap_is_refused():
    """A group of a thousand items holds half a million pairs."""
    members = [f"item-{index}" for index in range(448)]

    with pytest.raises(ScoreError) as excinfo:
        pairwise_metrics([members], [[members[0]]], limit=100_000)

    assert "pair limit" in str(excinfo.value)
    assert "100000" in str(excinfo.value)


def test_the_pair_cap_counts_each_side_separately():
    """The limit means what it says: pairs on one side."""
    members = [f"item-{index}" for index in range(448)]

    with pytest.raises(ScoreError) as excinfo:
        pairwise_metrics([members], [[members[0]]], limit=100_000)

    message = str(excinfo.value)
    assert "predicted groups" in message
    assert "100128" in message
    assert "128" in message
    # The expected side was under the limit, so it is not named.
    assert "expected groups" not in message


def test_a_case_over_the_cap_on_the_expected_side_only_is_refused():
    members = [f"item-{index}" for index in range(448)]

    with pytest.raises(ScoreError) as excinfo:
        pairwise_metrics([[members[0]]], [members], limit=100_000)

    message = str(excinfo.value)
    assert "expected groups" in message
    assert "predicted groups" not in message


def test_a_grouping_case_at_a_normal_size_is_still_scored():
    metrics = pairwise_metrics(
        [["a", "b", "c"]], [["a", "b"], ["c"]], limit=100_000
    )

    assert metrics["true_positive"] == 1
    assert metrics["f1"] == pytest.approx(0.5)


def test_a_case_over_the_pair_cap_is_refused_by_name():
    case, output = family_case(TaskName.clustering)
    items = [f"item-{index}" for index in range(448)]
    case.input = {"items": [{"item_id": item, "text": "a pain"} for item in items]}
    case.expected = {"groups": [[items[0]]]}

    score = score_case(case, {"groups": [items]})

    assert score.passed is False
    assert score.error is not None
    assert "pair limit" in score.error
    assert case.case_id in score.error


def test_a_model_that_named_only_unjudged_candidates_has_nothing_to_compare():
    """A candidate no human judged is counted, and there is nothing to compare."""
    case = EvalCase(
        case_id="comp-1",
        task="competitor_identification",
        input={
            "product": "Ledger",
            "candidates": ["Ledger Pro", "QuickBooks", "Wave", "FreshBooks"],
        },
        expected={"competitors": [], "not_competitors": ["Wave"]},
    )

    metrics = score_competitor_identification(case, {"competitors": ["FreshBooks"]})

    assert metrics["true_positive"] == 0
    assert metrics["false_positive"] == 0
    assert metrics["false_negative"] == 0
    assert metrics["precision"] is None
    assert metrics["recall"] is None
    assert metrics["f1"] is None
    assert metrics["unjudged_predictions"] == 1


def test_the_severity_message_follows_the_same_cap():
    """A severity is an integer, and an integer can be very long."""
    case = EvalCase(
        case_id="sev-1",
        task="severity",
        input={"text": TEXT, "quote": QUOTE},
        expected={"severity": 3},
    )

    score = score_case(case, {"severity": 10**1000})

    assert score.error is not None
    assert len(score.error) < 400
    assert "cut at" in score.error


def test_a_severity_python_cannot_print_still_gives_a_short_message():
    """Python refuses to print an integer of more than 4300 digits.

    The guard belongs to the value cap, so the case error survives.
    """
    case = EvalCase(
        case_id="sev-1",
        task="severity",
        input={"text": TEXT, "quote": QUOTE},
        expected={"severity": 3},
    )

    score = score_case(case, {"severity": 10**5000})

    assert score.error is not None
    assert len(score.error) < 400
    assert "too large to print" in score.error

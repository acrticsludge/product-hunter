"""The twelve task families, the four metric primitives, and the case scorer.

Every scorer is a pure function of a case and one model output: no clock, no
randomness, no network. The same input always produces the same numbers, so a
run result can be diffed.

A quality metric needs a human label. A case that carries no label is still
run, but the metrics that would need one stay ``None`` and the case does not
pass. ``quote_fidelity`` is the exception the spec makes: it is checked
against the source text, so it is always computable.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict

from product_hunter.eval.cases import EvalCase, TaskName, brief_value
from product_hunter.extraction import PainValidity
from product_hunter.normalization import (
    DEFAULT_NORMALIZATION_VERSION,
    Domain,
    NormalizationError,
    normalize_fields,
)
from product_hunter.quote import QuoteError, canonicalize, locate_quote

# The two answers the false-positive family allows.
PAIN_LABELS = ("pain", "not_pain")
# The three answers the counter-evidence family allows.
COUNTER_EVIDENCE_LABELS = ("supports", "refutes", "neutral")
# The five controlled fields of a normalized pain point.
NORMALIZATION_FIELDS = ("domain", "actor", "action", "object", "consequence")

# The most pairs one case may hold. A group of a thousand items holds half a
# million pairs, which is far past any labelling a person would do, so the
# case is refused before the pairs are built.
MAX_GROUP_PAIRS = 100_000


class ScoreError(Exception):
    """The output does not match the shape the family defines."""


class CaseScore(BaseModel):
    """The result of scoring one case against one model output."""

    model_config = ConfigDict(extra="forbid")

    case_id: str
    task: TaskName
    passed: bool | None
    metrics: dict[str, float | int | None]
    detail: dict[str, Any]
    error: str | None = None


# --- the metric primitives -------------------------------------------------


def classification_metrics(
    predicted: list[str], expected: list[str], labels: Iterable[str]
) -> dict[str, Any]:
    """Compare two lists of labels over a fixed label set.

    The lists are paired in order, which is how every family that uses this
    primitive reports: one answer per item, in the order of the items. A label
    the model used that the human did not still counts against it, so the
    macro averages run over the labels the comparison touched.

    A touched label the model got wrong scores ``0.0``, not an empty value: a
    macro average that dropped the wrong labels would report a perfect score
    for a model that got the case wrong. A label nobody used keeps its empty
    value in the breakdown and stays out of the macro.
    """
    order = list(dict.fromkeys(labels))
    for label in sorted(set(expected) | set(predicted)):
        if label not in order:
            order.append(label)

    true_positive: Counter[str] = Counter()
    false_positive: Counter[str] = Counter()
    false_negative: Counter[str] = Counter()
    confusion: dict[str, dict[str, int]] = {}
    for expected_label, predicted_label in zip(expected, predicted):
        if expected_label == predicted_label:
            true_positive[expected_label] += 1
        else:
            false_negative[expected_label] += 1
            false_positive[predicted_label] += 1
        row = confusion.setdefault(expected_label, {})
        row[predicted_label] = row.get(predicted_label, 0) + 1
    for expected_label in expected[len(predicted) :]:
        false_negative[expected_label] += 1
    for predicted_label in predicted[len(expected) :]:
        false_positive[predicted_label] += 1

    per_label: dict[str, dict[str, float | int | None]] = {}
    for label in order:
        hits = true_positive[label]
        wrong = false_positive[label]
        missed = false_negative[label]
        touched = bool(hits + wrong + missed)
        if touched:
            precision = hits / (hits + wrong) if hits + wrong else 0.0
            recall = hits / (hits + missed) if hits + missed else 0.0
            f1 = _f1(precision, recall)
        else:
            precision = None
            recall = None
            f1 = None
        per_label[label] = {
            "support": hits + missed,
            "predicted": hits + wrong,
            "true_positive": hits,
            "false_positive": wrong,
            "false_negative": missed,
            "precision": precision,
            "recall": recall,
            "f1": f1,
        }

    touched_labels = [
        entry for entry in per_label.values() if entry["support"] or entry["predicted"]
    ]
    return {
        "n": len(expected),
        "accuracy": _ratio(sum(true_positive.values()), len(expected)),
        "precision": _mean([entry["precision"] for entry in touched_labels]),
        "recall": _mean([entry["recall"] for entry in touched_labels]),
        "f1": _mean([entry["f1"] for entry in touched_labels]),
        "per_label": per_label,
        "confusion": confusion,
    }


def detection_metrics(
    predicted: Iterable[str], expected: Iterable[str], unjudged: Iterable[str] = ()
) -> dict[str, Any]:
    """Compare two sets of identifiers.

    ``unjudged`` names the predictions no human ruled on. They are counted and
    kept out of precision: a prediction nobody judged is not a wrong answer.

    A value is empty only when there was nothing on either side to compare. A
    zero denominator otherwise gives ``0.0``: a model that found nothing
    against a label that found something matched none of what was there.
    """
    predicted_set = set(predicted)
    expected_set = set(expected)
    unjudged_set = set(unjudged)
    true_positive = len(predicted_set & expected_set)
    false_positive = len((predicted_set - unjudged_set) - expected_set)
    false_negative = len(expected_set - predicted_set)
    precision, recall, f1 = _shares_of_counts(
        true_positive, false_positive, false_negative
    )
    return {
        "true_positive": true_positive,
        "false_positive": false_positive,
        "false_negative": false_negative,
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "unjudged": len(predicted_set & unjudged_set),
    }


def pairwise_metrics(
    predicted_groups: list[list[str]],
    expected_groups: list[list[str]],
    *,
    limit: int = MAX_GROUP_PAIRS,
) -> dict[str, Any]:
    """Compare two groupings of items.

    Every unordered pair of items inside a group is a positive pair, so a
    grouping is right when it puts the same items together as the label does.

    A group of ``n`` items holds ``n(n-1)/2`` pairs, so a case whose groups
    hold more pairs than the limit is refused before the pairs are built. Each
    side is counted against the limit on its own, so the limit means what it
    says.
    """
    predicted_count = _pair_count(predicted_groups)
    expected_count = _pair_count(expected_groups)
    if predicted_count > limit:
        raise ScoreError(
            f"the predicted groups hold {predicted_count} pairs, "
            f"{predicted_count - limit} over the {limit} pair limit"
        )
    if expected_count > limit:
        raise ScoreError(
            f"the expected groups hold {expected_count} pairs, "
            f"{expected_count - limit} over the {limit} pair limit"
        )
    predicted_pairs = _pairs(predicted_groups)
    expected_pairs = _pairs(expected_groups)
    true_positive = len(predicted_pairs & expected_pairs)
    false_positive = len(predicted_pairs - expected_pairs)
    false_negative = len(expected_pairs - predicted_pairs)
    precision, recall, f1 = _shares_of_counts(
        true_positive, false_positive, false_negative
    )
    return {
        "predicted_pairs": len(predicted_pairs),
        "expected_pairs": len(expected_pairs),
        "true_positive": true_positive,
        "false_positive": false_positive,
        "false_negative": false_negative,
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "group_count": len(predicted_groups),
        "expected_group_count": len(expected_groups),
    }


def agreement_metrics(predicted: list[int], expected: list[int]) -> dict[str, Any]:
    """Compare two lists of integers."""
    total = len(expected)
    exact = 0
    within_one = 0
    distance = 0
    for expected_value, predicted_value in zip(expected, predicted):
        gap = abs(expected_value - predicted_value)
        distance += gap
        if gap == 0:
            exact += 1
        if gap <= 1:
            within_one += 1
    return {
        "n": total,
        "exact": exact,
        "exact_rate": _ratio(exact, total),
        "within_one": within_one,
        "within_one_rate": _ratio(within_one, total),
        "mean_absolute_error": distance / total if total else None,
    }


# --- the twelve families ----------------------------------------------------


def score_pain_extraction(case: EvalCase, output: dict[str, Any]) -> dict[str, Any]:
    """Score the genuine quotes a model found against the labelled pain points.

    A predicted candidate matches a labelled pain point when the two quotes
    fold to the same canonical string.
    """
    predicted: list[str] = []
    candidates = _list_field(output, "candidates", "output")
    for index, candidate in enumerate(candidates):
        if not isinstance(candidate, dict):
            raise ScoreError(f"candidate {index} of the output is not an object")
        quote = _text_field(candidate, "quote", f"candidate {index} of the output")
        validity = _text_field(
            candidate, "validity", f"candidate {index} of the output"
        )
        try:
            kind = PainValidity(validity)
        except ValueError as exc:
            raise ScoreError(
                f"candidate {index} of the output has the unknown validity "
                f"{brief_value(validity)}"
            ) from exc
        if kind is PainValidity.genuine:
            predicted.append(canonicalize(quote))

    expected: list[str] = []
    points = _list_field(_expected(case), "pain_points", "expected block")
    for index, point in enumerate(points):
        if not isinstance(point, dict):
            raise ScoreError(
                f"pain point {index} of the expected block is not an object"
            )
        quote = _text_field(point, "quote", f"pain point {index} of the expected block")
        expected.append(canonicalize(quote))
    return detection_metrics(predicted, expected)


def score_false_positive_rejection(
    case: EvalCase, output: dict[str, Any]
) -> dict[str, Any]:
    """Score one span the model was asked to judge."""
    # The reason is part of the shape the family defines, but no metric reads it.
    _text_field(output, "reason", "output")
    predicted = _pain_label(_required(output, "is_pain", "output"), "output")
    expected = _pain_label(
        _required(_expected(case), "is_pain", "expected block"), "expected block"
    )
    metrics = classification_metrics([predicted], [expected], PAIN_LABELS)
    metrics["false_accept_rate"] = _ratio(
        1 if (predicted == "pain" and expected == "not_pain") else 0,
        1 if expected == "not_pain" else 0,
    )
    metrics["false_reject_rate"] = _ratio(
        1 if (predicted == "not_pain" and expected == "pain") else 0,
        1 if expected == "pain" else 0,
    )
    return metrics


def score_quote_fidelity(case: EvalCase, output: dict[str, Any]) -> dict[str, Any]:
    """Check the model's quote against the source text.

    The check needs no label: the text is the reference.
    """
    text = _text_field(case.input, "text", "case input")
    quote = _text_field(output, "quote", "output")
    try:
        match = locate_quote(text, quote)
    except QuoteError as exc:
        return {
            "verbatim_rate": 0.0,
            "exact_rate": 0.0,
            "canonical_rate": 0.0,
            "failures": 1,
            "failure_reason": exc.detail or exc.reason_code,
        }
    exact = match.match_mode == "exact"
    return {
        "verbatim_rate": 1.0,
        "exact_rate": 1.0 if exact else 0.0,
        "canonical_rate": 0.0 if exact else 1.0,
        "failures": 0,
        "match_mode": match.match_mode,
    }


def score_severity(case: EvalCase, output: dict[str, Any]) -> dict[str, Any]:
    """Compare the severity the model gave with the one a human gave."""
    predicted = _severity(output, "output")
    expected = _severity(_expected(case), "expected block")
    return agreement_metrics([predicted], [expected])


def score_normalization(case: EvalCase, output: dict[str, Any]) -> dict[str, Any]:
    """Compare the key the model builds with the key the human's fields build.

    Both keys come from the production rule, so the benchmark measures the
    key the pipeline uses and not a copy of it.
    """
    predicted_fields = _normalization_fields(output, "output")
    expected_fields = _normalization_fields(_expected(case), "expected block")
    predicted_key = _normalized_key(predicted_fields, "output")
    expected_key = _normalized_key(expected_fields, "expected block")
    metrics: dict[str, float | int | None] = {
        "domain_accuracy": 1.0
        if predicted_fields["domain"] == expected_fields["domain"]
        else 0.0,
        "key_agreement": 1.0 if predicted_key == expected_key else 0.0,
    }
    for name in ("actor", "action", "object", "consequence"):
        metrics[f"{name}_agreement"] = (
            1.0
            if predicted_fields[name].strip() == expected_fields[name].strip()
            else 0.0
        )
    return metrics


def score_duplicate_detection(case: EvalCase, output: dict[str, Any]) -> dict[str, Any]:
    """Compare the duplicate groups with the labelled ones."""
    known = _document_ids(case)
    groups = _groups(output, "output")
    expected_groups = _groups(_expected(case), "expected block")
    _check_members(groups, known, "output")
    _check_members(expected_groups, known, "expected block")
    return _pairwise(case, groups, expected_groups)


def score_author_independence(case: EvalCase, output: dict[str, Any]) -> dict[str, Any]:
    """Compare the author groups and count the independent authors.

    The count is a property of the groups, computed here. It is never a number
    the model supplies.
    """
    known = _mention_ids(case)
    groups = _groups(output, "output")
    expected_groups = _groups(_expected(case), "expected block")
    _check_members(groups, known, "output")
    _check_members(expected_groups, known, "expected block")
    metrics = _pairwise(case, groups, expected_groups)
    metrics["independent_author_count"] = len(groups)
    metrics["expected_author_count"] = len(expected_groups)
    return metrics


def score_clustering(case: EvalCase, output: dict[str, Any]) -> dict[str, Any]:
    """Compare the clusters with the labelled ones."""
    known = _item_ids(case)
    groups = _groups(output, "output")
    expected_groups = _groups(_expected(case), "expected block")
    _check_members(groups, known, "output")
    _check_members(expected_groups, known, "expected block")
    return _pairwise(case, groups, expected_groups)


def score_entity_resolution(case: EvalCase, output: dict[str, Any]) -> dict[str, Any]:
    """Compare the identity groups with the labelled ones.

    The family answers whether two accounts are the same person. The
    independence family groups mentions into authors; this one groups the
    accounts themselves, which is the step the identity resolver takes.

    An account the case does not hold is a case error, the way it is in the
    other three grouping families.
    """
    known = _account_ids(case)
    groups = _groups(output, "output")
    expected_groups = _groups(_expected(case), "expected block")
    _check_members(groups, known, "output")
    _check_members(expected_groups, known, "expected block")
    return _pairwise(case, groups, expected_groups)


def _pairwise(
    case: EvalCase, groups: list[list[str]], expected_groups: list[list[str]]
) -> dict[str, Any]:
    """The pairwise metrics of one case, with the case named in a refusal."""
    try:
        return pairwise_metrics(groups, expected_groups)
    except ScoreError as exc:
        raise ScoreError(
            f"the case {brief_value(case.case_id)}: {exc}"
        ) from exc


def score_competitor_identification(
    case: EvalCase, output: dict[str, Any]
) -> dict[str, Any]:
    """Compare the competitors the model named with the ones a human judged.

    A prediction naming a candidate no human judged is counted and kept out of
    precision. It is not silently a false positive.
    """
    candidates = _candidate_names(case)
    competitors = _names(output, "competitors", "output")
    expected_block = _expected(case)
    expected = _names(expected_block, "competitors", "expected block")
    judged = set(expected) | set(
        _names(expected_block, "not_competitors", "expected block")
    )
    unjudged = {
        name for name in competitors if name in candidates and name not in judged
    }
    metrics = detection_metrics(competitors, expected, unjudged)
    metrics.pop("unjudged", None)
    metrics["unjudged_predictions"] = len(unjudged)
    return metrics


def score_counter_evidence_classification(
    case: EvalCase, output: dict[str, Any]
) -> dict[str, Any]:
    """Classify each piece of evidence against the label a human gave."""
    items = _list_field(case.input, "items", "case input")
    labels = _labels(output, "output")
    if len(labels) != len(items):
        raise ScoreError(
            f"the output labels {len(labels)} items but the case holds {len(items)}"
        )
    expected_labels = _labels(_expected(case), "expected block")
    if len(expected_labels) != len(items):
        raise ScoreError(
            f"the expected block labels {len(expected_labels)} items "
            f"but the case holds {len(items)}"
        )
    return classification_metrics(labels, expected_labels, COUNTER_EVIDENCE_LABELS)


def score_scoring_consistency(
    case: EvalCase, output: dict[str, Any]
) -> dict[str, Any]:
    """Compare the two scores with the ones a human gave.

    The tolerance is the case's own: how many points apart two scores may be
    and still count as the same answer. A scorer that fixed the tolerance in
    the code would make the number a property of the scorer instead of a
    property of the case.

    A score outside 0 to 100 is a case error, the way an unknown label is in
    the classification families.
    """
    tolerance = _tolerance(case)
    # The opportunity is the text the model reads. The score does not depend
    # on it, but a case that does not hold one is malformed.
    _text_field(case.input, "opportunity", "case input")
    expected = _expected(case)
    potential_gap = abs(
        _score_number(output, "potential", "output")
        - _score_number(expected, "potential", "expected block")
    )
    confidence_gap = abs(
        _score_number(output, "confidence", "output")
        - _score_number(expected, "confidence", "expected block")
    )
    return {
        "n": 1,
        "potential_exact_rate": 1.0 if potential_gap == 0 else 0.0,
        "confidence_exact_rate": 1.0 if confidence_gap == 0 else 0.0,
        "potential_within_rate": 1.0 if potential_gap <= tolerance else 0.0,
        "confidence_within_rate": 1.0 if confidence_gap <= tolerance else 0.0,
        "potential_mean_absolute_error": potential_gap,
        "confidence_mean_absolute_error": confidence_gap,
    }


# --- the dispatcher ---------------------------------------------------------


@dataclass(frozen=True)
class _Family:
    """One task family: the metrics it produces and the scorer behind them."""

    metrics: tuple[str, ...]
    primary: str
    scorer: Callable[[EvalCase, dict[str, Any]], dict[str, Any]]


_FAMILIES: dict[TaskName, _Family] = {
    TaskName.pain_extraction: _Family(
        metrics=(
            "true_positive",
            "false_positive",
            "false_negative",
            "precision",
            "recall",
            "f1",
            "unjudged",
        ),
        primary="f1",
        scorer=score_pain_extraction,
    ),
    TaskName.false_positive_rejection: _Family(
        metrics=(
            "n",
            "accuracy",
            "precision",
            "recall",
            "f1",
            "false_accept_rate",
            "false_reject_rate",
        ),
        primary="accuracy",
        scorer=score_false_positive_rejection,
    ),
    TaskName.quote_fidelity: _Family(
        metrics=("verbatim_rate", "exact_rate", "canonical_rate", "failures"),
        primary="verbatim_rate",
        scorer=score_quote_fidelity,
    ),
    TaskName.severity: _Family(
        metrics=(
            "n",
            "exact",
            "exact_rate",
            "within_one",
            "within_one_rate",
            "mean_absolute_error",
        ),
        primary="exact_rate",
        scorer=score_severity,
    ),
    TaskName.normalization: _Family(
        metrics=(
            "domain_accuracy",
            "key_agreement",
            "actor_agreement",
            "action_agreement",
            "object_agreement",
            "consequence_agreement",
        ),
        primary="key_agreement",
        scorer=score_normalization,
    ),
    TaskName.duplicate_detection: _Family(
        metrics=(
            "predicted_pairs",
            "expected_pairs",
            "true_positive",
            "false_positive",
            "false_negative",
            "precision",
            "recall",
            "f1",
            "group_count",
            "expected_group_count",
        ),
        primary="f1",
        scorer=score_duplicate_detection,
    ),
    TaskName.author_independence: _Family(
        metrics=(
            "predicted_pairs",
            "expected_pairs",
            "true_positive",
            "false_positive",
            "false_negative",
            "precision",
            "recall",
            "f1",
            "group_count",
            "expected_group_count",
            "independent_author_count",
            "expected_author_count",
        ),
        primary="f1",
        scorer=score_author_independence,
    ),
    TaskName.clustering: _Family(
        metrics=(
            "predicted_pairs",
            "expected_pairs",
            "true_positive",
            "false_positive",
            "false_negative",
            "precision",
            "recall",
            "f1",
            "group_count",
            "expected_group_count",
        ),
        primary="f1",
        scorer=score_clustering,
    ),
    TaskName.competitor_identification: _Family(
        metrics=(
            "true_positive",
            "false_positive",
            "false_negative",
            "precision",
            "recall",
            "f1",
            "unjudged_predictions",
        ),
        primary="f1",
        scorer=score_competitor_identification,
    ),
    TaskName.counter_evidence_classification: _Family(
        metrics=("n", "accuracy", "precision", "recall", "f1"),
        primary="accuracy",
        scorer=score_counter_evidence_classification,
    ),
    TaskName.entity_resolution: _Family(
        metrics=(
            "true_positive",
            "false_positive",
            "false_negative",
            "precision",
            "recall",
            "f1",
            "group_count",
            "expected_group_count",
        ),
        primary="f1",
        scorer=score_entity_resolution,
    ),
    TaskName.scoring_consistency: _Family(
        metrics=(
            "n",
            "potential_exact_rate",
            "confidence_exact_rate",
            "potential_within_rate",
            "confidence_within_rate",
            "potential_mean_absolute_error",
            "confidence_mean_absolute_error",
        ),
        primary="potential_within_rate",
        scorer=score_scoring_consistency,
    ),
}


def family_metrics(task: TaskName) -> tuple[str, ...]:
    """The metrics one task family produces, in a fixed order."""
    return _FAMILIES[TaskName(task)].metrics


def score_case(case: EvalCase, output: dict[str, Any]) -> CaseScore:
    """Score one case against one model output.

    A case with no label is measured only where the metric needs no label. An
    output the scorer cannot read is a case error, and a case with an error
    never counts as a pass.
    """
    family = _FAMILIES[case.task]
    # The case version travels with the score, so a run records which version
    # of every case it scored.
    detail: dict[str, Any] = {"case_version": case.case_version}
    if case.expected is None and case.task is not TaskName.quote_fidelity:
        detail["blocked"] = "the case carries no human label"
        return CaseScore(
            case_id=case.case_id,
            task=case.task,
            passed=None,
            metrics={name: None for name in family.metrics},
            detail=detail,
        )
    try:
        raw = family.scorer(case, output)
    except ScoreError as exc:
        return CaseScore(
            case_id=case.case_id,
            task=case.task,
            passed=False,
            metrics={name: None for name in family.metrics},
            detail=detail,
            error=str(exc),
        )
    metrics = {name: raw.get(name) for name in family.metrics}
    detail.update(
        {key: value for key, value in raw.items() if key not in family.metrics}
    )
    primary = metrics.get(family.primary)
    passed = None if primary is None else primary == 1.0
    return CaseScore(
        case_id=case.case_id,
        task=case.task,
        passed=passed,
        metrics=metrics,
        detail=detail,
    )


# --- the small helpers ------------------------------------------------------


def _ratio(numerator: int, denominator: int) -> float | None:
    """A share, or ``None`` when there is nothing to divide by."""
    return numerator / denominator if denominator else None


def _mean(values: list[float]) -> float | None:
    """The mean of the values that exist, or ``None`` when there are none."""
    return sum(values) / len(values) if values else None


def _f1(precision: float | None, recall: float | None) -> float | None:
    """The harmonic mean of two shares, or ``None`` when one of them is."""
    if precision is None or recall is None:
        return None
    if precision + recall == 0:
        return 0.0
    return 2 * precision * recall / (precision + recall)


def _f1_of_counts(
    true_positive: int, false_positive: int, false_negative: int
) -> float | None:
    """The f1 of a comparison, from its counts.

    A model that matched none of the comparison scores ``0.0`` when the
    comparison had something to compare. It is empty only when there was
    nothing on either side, so there was nothing to compare.
    """
    denominator = 2 * true_positive + false_positive + false_negative
    return 2 * true_positive / denominator if denominator else None


def _shares_of_counts(
    true_positive: int, false_positive: int, false_negative: int
) -> tuple[float | None, float | None, float | None]:
    """The precision, the recall and the f1 of a comparison.

    All three are empty only when there was nothing on either side to compare.
    A zero denominator otherwise gives ``0.0``, the same rule the
    classification primitive follows, so the primitives agree.
    """
    if true_positive + false_positive + false_negative == 0:
        return None, None, None
    precision = (
        true_positive / (true_positive + false_positive)
        if true_positive + false_positive
        else 0.0
    )
    recall = (
        true_positive / (true_positive + false_negative)
        if true_positive + false_negative
        else 0.0
    )
    return precision, recall, _f1_of_counts(
        true_positive, false_positive, false_negative
    )


def _pair_count(groups: list[list[str]]) -> int:
    """How many pairs the groups hold, without building them."""
    return sum(len(set(group)) * (len(set(group)) - 1) // 2 for group in groups)


def _pairs(groups: list[list[str]]) -> set[tuple[str, str]]:
    """The unordered pairs of items that share a group."""
    pairs: set[tuple[str, str]] = set()
    for group in groups:
        members = sorted(set(group))
        for index, first in enumerate(members):
            for second in members[index + 1 :]:
                pairs.add((first, second))
    return pairs


def _expected(case: EvalCase) -> dict[str, Any]:
    """The human label of a case, or a refusal when there is none."""
    if case.expected is None:
        raise ScoreError("the case carries no human label")
    return case.expected


def _required(mapping: dict[str, Any], name: str, where: str) -> Any:
    if name not in mapping:
        raise ScoreError(f"the {where} has no {name!r}")
    return mapping[name]


def _text_field(mapping: dict[str, Any], name: str, where: str) -> str:
    value = _required(mapping, name, where)
    if not isinstance(value, str):
        raise ScoreError(f"the {where} has a {name!r} that is not text")
    return value


def _list_field(mapping: dict[str, Any], name: str, where: str) -> list[Any]:
    value = _required(mapping, name, where)
    if not isinstance(value, list):
        raise ScoreError(f"the {where} has a {name!r} that is not a list")
    return value


def _pain_label(value: Any, where: str) -> str:
    """Read an ``is_pain`` answer as one of the two labels."""
    if isinstance(value, bool):
        return "pain" if value else "not_pain"
    if value in PAIN_LABELS:
        return str(value)
    raise ScoreError(
        f"the {where} has the unknown is_pain value {brief_value(value)}"
    )


def _severity(mapping: dict[str, Any], where: str) -> int:
    value = _required(mapping, "severity", where)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ScoreError(f"the {where} has a 'severity' that is not an integer")
    if not 0 <= value <= 5:
        raise ScoreError(
            f"the {where} has a severity of {brief_value(value)}, expected 0 to 5"
        )
    return value


def _score_number(mapping: dict[str, Any], name: str, where: str) -> float:
    """Read one score: a number from 0 to 100."""
    value = _required(mapping, name, where)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ScoreError(f"the {where} has a {name!r} that is not a number")
    if not 0 <= value <= 100:
        raise ScoreError(
            f"the {where} has a {name!r} of {brief_value(value)}, expected 0 to 100"
        )
    return float(value)


def _tolerance(case: EvalCase) -> float:
    """How many points apart two scores may be and still count as the same."""
    value = _required(case.input, "tolerance", "case input")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ScoreError("the case input has a 'tolerance' that is not a number")
    if value < 0:
        raise ScoreError(
            f"the case input has a tolerance of {brief_value(value)}, which is "
            "negative"
        )
    return float(value)


def _normalization_fields(mapping: dict[str, Any], where: str) -> dict[str, str]:
    return {name: _text_field(mapping, name, where) for name in NORMALIZATION_FIELDS}


def _normalized_key(fields: dict[str, str], where: str) -> str:
    """Build the key the production rule builds, or refuse the fields."""
    try:
        domain = Domain(fields["domain"])
    except ValueError as exc:
        raise ScoreError(
            f"the {where} names the domain {brief_value(fields['domain'])}, "
            "which is not one of the controlled values"
        ) from exc
    try:
        problem = normalize_fields(
            domain=domain,
            actor=fields["actor"],
            action=fields["action"],
            object_=fields["object"],
            consequence=fields["consequence"],
            normalization_version=DEFAULT_NORMALIZATION_VERSION,
        )
    except NormalizationError as exc:
        raise ScoreError(f"the {where} cannot be normalized: {exc.detail}") from exc
    return problem.normalized_key


def _document_ids(case: EvalCase) -> set[str]:
    ids: set[str] = set()
    documents = _list_field(case.input, "documents", "case input")
    for index, document in enumerate(documents):
        if not isinstance(document, dict):
            raise ScoreError(f"document {index} of the case input is not an object")
        ids.add(_text_field(document, "doc_id", f"document {index} of the case input"))
    return ids


def _mention_ids(case: EvalCase) -> set[str]:
    ids: set[str] = set()
    mentions = _list_field(case.input, "mentions", "case input")
    for index, mention in enumerate(mentions):
        if not isinstance(mention, dict):
            raise ScoreError(f"mention {index} of the case input is not an object")
        identifier = mention.get("mention_id") or mention.get("id")
        if not isinstance(identifier, str) or not identifier:
            raise ScoreError(f"mention {index} of the case input has no mention id")
        ids.add(identifier)
    return ids


def _item_ids(case: EvalCase) -> set[str]:
    ids: set[str] = set()
    items = _list_field(case.input, "items", "case input")
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            raise ScoreError(f"item {index} of the case input is not an object")
        ids.add(_text_field(item, "item_id", f"item {index} of the case input"))
    return ids


def _account_ids(case: EvalCase) -> set[str]:
    """The account ids the case holds."""
    ids: set[str] = set()
    accounts = _list_field(case.input, "accounts", "case input")
    for index, account in enumerate(accounts):
        if not isinstance(account, dict):
            raise ScoreError(f"account {index} of the case input is not an object")
        ids.add(
            _text_field(account, "account_id", f"account {index} of the case input")
        )
    return ids


def _candidate_names(case: EvalCase) -> set[str]:
    names: set[str] = set()
    candidates = _list_field(case.input, "candidates", "case input")
    for index, candidate in enumerate(candidates):
        if isinstance(candidate, str):
            names.add(candidate)
        elif isinstance(candidate, dict) and isinstance(candidate.get("name"), str):
            names.add(candidate["name"])
        else:
            raise ScoreError(f"candidate {index} of the case input has no name")
    return names


def _groups(mapping: dict[str, Any], where: str) -> list[list[str]]:
    groups: list[list[str]] = []
    for index, group in enumerate(_list_field(mapping, "groups", where)):
        if not isinstance(group, list):
            raise ScoreError(f"group {index} of the {where} is not a list")
        members: list[str] = []
        for member in group:
            if not isinstance(member, str):
                raise ScoreError(
                    f"group {index} of the {where} holds a member that is not text"
                )
            members.append(member)
        groups.append(members)
    return groups


def _names(mapping: dict[str, Any], name: str, where: str) -> list[str]:
    names: list[str] = []
    for index, value in enumerate(_list_field(mapping, name, where)):
        if not isinstance(value, str):
            raise ScoreError(f"entry {index} of the {where} {name!r} is not text")
        names.append(value)
    return names


def _labels(mapping: dict[str, Any], where: str) -> list[str]:
    labels: list[str] = []
    for index, label in enumerate(_list_field(mapping, "labels", where)):
        if not isinstance(label, str) or label not in COUNTER_EVIDENCE_LABELS:
            raise ScoreError(
                f"label {index} of the {where} is {brief_value(label)}, expected "
                f"one of {', '.join(COUNTER_EVIDENCE_LABELS)}"
            )
        labels.append(label)
    return labels


def _check_members(groups: list[list[str]], known: set[str], where: str) -> None:
    """Refuse a group that names something the case does not hold."""
    for index, group in enumerate(groups):
        for member in group:
            if member not in known:
                raise ScoreError(
                    f"group {index} of the {where} names {brief_value(member)}, "
                    "which the case does not hold"
                )

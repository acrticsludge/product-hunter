from __future__ import annotations

import pytest
from pydantic import ValidationError

from product_hunter.research.evidence import ToolCallStatus
from product_hunter.research.investigation.counter_evidence import (
    CounterEvidenceItem,
    CounterEvidenceKind,
    CounterEvidenceResult,
    CounterEvidenceStatus,
    assess_counter_evidence,
)
from product_hunter.research.investigation.coverage import (
    CoverageRequirements,
    SearchAttempt,
)

REQUIRED = ["counter_not_worth", "counter_complaints"]


def _attempt(family: str, query: str, **overrides) -> SearchAttempt:
    data = {
        "family": family,
        "query": query,
        "status": ToolCallStatus.success,
        "document_count": 1,
        "domains": ["one.example"],
    }
    data.update(overrides)
    return SearchAttempt(**data)


def _requirements(**overrides) -> CoverageRequirements:
    data = {"min_queries": 2, "min_documents": 1, "min_domains": 2}
    data.update(overrides)
    return CoverageRequirements(**data)


def _complete_attempts() -> list[SearchAttempt]:
    return [
        _attempt("counter_not_worth", "spreadsheet pain not worth"),
        _attempt(
            "counter_complaints",
            "spreadsheet pain complaints",
            domains=["two.example"],
        ),
    ]


def _item(**overrides) -> CounterEvidenceItem:
    data = {
        "item_id": "ce-1",
        "kind": CounterEvidenceKind.existing_solution,
        "summary": "a spreadsheet suite already does this",
    }
    data.update(overrides)
    return CounterEvidenceItem(**data)


def test_items_found_gives_found():
    result = assess_counter_evidence(
        [_item()], _complete_attempts(), REQUIRED, _requirements()
    )

    assert result.status is CounterEvidenceStatus.found
    assert len(result.items) == 1


def test_no_items_with_a_complete_coverage_gives_none_found():
    result = assess_counter_evidence([], _complete_attempts(), REQUIRED, _requirements())

    assert result.status is CounterEvidenceStatus.none_found
    assert result.coverage.status.value == "complete"


def test_no_items_with_an_incomplete_coverage_is_incomplete():
    attempts = [_attempt("counter_not_worth", "spreadsheet pain not worth")]

    result = assess_counter_evidence([], attempts, REQUIRED, _requirements())

    # "Incomplete" is the mission's UNKNOWN, never "no counter-evidence exists".
    assert result.status is CounterEvidenceStatus.incomplete
    assert result.status is not CounterEvidenceStatus.none_found
    assert result.reasons


def test_a_failed_search_makes_the_result_incomplete():
    attempts = [
        _attempt("counter_not_worth", "spreadsheet pain not worth"),
        _attempt(
            "counter_complaints",
            "spreadsheet pain complaints",
            status=ToolCallStatus.failure,
            error="upstream refused",
            domains=["two.example"],
        ),
    ]

    result = assess_counter_evidence([], attempts, REQUIRED, _requirements())

    assert result.status is CounterEvidenceStatus.incomplete
    assert result.coverage.tool_failures == 1


def test_the_required_families_are_recorded():
    result = assess_counter_evidence([], _complete_attempts(), REQUIRED, _requirements())

    assert result.required_families == REQUIRED
    assert result.coverage.required_families == REQUIRED


def test_none_found_is_refused_on_an_incomplete_coverage():
    coverage = assess_counter_evidence(
        [], [_attempt("counter_not_worth", "spreadsheet pain not worth")], REQUIRED, _requirements()
    ).coverage

    with pytest.raises(ValidationError) as excinfo:
        CounterEvidenceResult(
            status=CounterEvidenceStatus.none_found,
            items=[],
            coverage=coverage,
            required_families=REQUIRED,
        )

    assert "complete coverage" in str(excinfo.value)


def test_the_strength_is_bounded():
    with pytest.raises(ValidationError) as excinfo:
        _item(strength=1.5)
    assert "strength" in str(excinfo.value)


def test_the_reasons_name_the_counts_and_not_the_queries():
    attempts = [
        _attempt("counter_not_worth", "spreadsheet pain not worth"),
        _attempt(
            "counter_complaints",
            "spreadsheet pain complaints",
            status=ToolCallStatus.failure,
            error="upstream refused",
        ),
    ]

    result = assess_counter_evidence([_item()], attempts, REQUIRED, _requirements())

    assert result.reasons
    for reason in result.reasons:
        assert "spreadsheet" not in reason
        assert "http" not in reason.lower()

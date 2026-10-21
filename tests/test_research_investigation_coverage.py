from __future__ import annotations

import pytest
from pydantic import ValidationError

from product_hunter.research.evidence import ToolCallStatus
from product_hunter.research.investigation.coverage import (
    CoverageRequirements,
    CoverageStatus,
    SearchAttempt,
    assess_coverage,
)

REQUIRED = ["competitor_alternative", "counter_not_worth"]


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
        _attempt("competitor_alternative", "spreadsheet pain alternative"),
        _attempt(
            "counter_not_worth",
            "spreadsheet pain not worth",
            domains=[" Two.Example "],
        ),
    ]


def test_a_covered_search_is_complete():
    coverage = assess_coverage(REQUIRED, _complete_attempts(), _requirements())

    assert coverage.status is CoverageStatus.complete
    assert coverage.reasons == []
    assert coverage.completed_families == ["competitor_alternative", "counter_not_worth"]
    assert coverage.missing_families == []
    assert coverage.failed_families == []
    assert coverage.tool_failures == 0
    assert coverage.distinct_queries == 2
    assert coverage.document_count == 2


def test_a_family_that_was_never_attempted_is_incomplete():
    attempts = [_attempt("competitor_alternative", "spreadsheet pain alternative")]

    coverage = assess_coverage(REQUIRED, attempts, _requirements())

    assert coverage.status is CoverageStatus.incomplete
    assert coverage.missing_families == ["counter_not_worth"]
    assert coverage.completed_families == ["competitor_alternative"]
    assert any("never attempted" in reason for reason in coverage.reasons)


def test_a_family_without_a_successful_attempt_is_incomplete():
    attempts = [
        _attempt("competitor_alternative", "spreadsheet pain alternative"),
        _attempt(
            "counter_not_worth",
            "spreadsheet pain not worth",
            status=ToolCallStatus.failure,
            error="upstream refused",
        ),
    ]

    coverage = assess_coverage(REQUIRED, attempts, _requirements())

    assert coverage.status is CoverageStatus.incomplete
    assert coverage.failed_families == ["counter_not_worth"]
    assert coverage.completed_families == ["competitor_alternative"]
    assert coverage.tool_failures == 1
    assert any("no successful attempt" in reason for reason in coverage.reasons)


def test_a_failed_attempt_stays_a_failure_after_a_successful_retry():
    attempts = [
        _attempt("competitor_alternative", "spreadsheet pain alternative"),
        _attempt(
            "counter_not_worth",
            "spreadsheet pain not worth",
            status=ToolCallStatus.timeout,
        ),
        _attempt(
            "counter_not_worth",
            "spreadsheet pain not worth",
            domains=["two.example"],
        ),
    ]

    coverage = assess_coverage(REQUIRED, attempts, _requirements())

    # The retry succeeded, so the family completed, but the flaky search
    # cannot support a "nothing exists" conclusion.
    assert coverage.completed_families == ["competitor_alternative", "counter_not_worth"]
    assert coverage.failed_families == []
    assert coverage.tool_failures == 1
    assert coverage.status is CoverageStatus.incomplete
    assert any("did not succeed" in reason for reason in coverage.reasons)


def test_a_coverage_below_the_query_floor_is_incomplete():
    attempts = [
        _attempt("competitor_alternative", "spreadsheet pain alternative"),
        _attempt("counter_not_worth", "spreadsheet pain not worth", domains=["two.example"]),
    ]

    coverage = assess_coverage(REQUIRED, attempts, _requirements(min_queries=3))

    assert coverage.status is CoverageStatus.incomplete
    assert coverage.distinct_queries == 2
    assert any("distinct successful queries" in reason for reason in coverage.reasons)


def test_a_coverage_below_the_document_floor_is_incomplete():
    attempts = [
        _attempt(
            "competitor_alternative", "spreadsheet pain alternative", document_count=0
        ),
        _attempt(
            "counter_not_worth",
            "spreadsheet pain not worth",
            document_count=0,
            domains=["two.example"],
        ),
    ]

    coverage = assess_coverage(REQUIRED, attempts, _requirements(min_documents=1))

    assert coverage.status is CoverageStatus.incomplete
    assert coverage.document_count == 0
    assert any("documents examined" in reason for reason in coverage.reasons)


def test_a_coverage_below_the_domain_floor_is_incomplete():
    attempts = [
        _attempt("competitor_alternative", "spreadsheet pain alternative"),
        _attempt("counter_not_worth", "spreadsheet pain not worth"),
    ]

    coverage = assess_coverage(REQUIRED, attempts, _requirements(min_domains=2))

    assert coverage.status is CoverageStatus.incomplete
    assert coverage.distinct_domains == ["one.example"]
    assert any("distinct source domains" in reason for reason in coverage.reasons)


def test_the_reasons_never_quote_a_query_a_url_or_a_domain():
    attempts = [
        _attempt(
            "competitor_alternative",
            "spreadsheet pain alternative",
            domains=["Secret.example"],
        ),
        _attempt(
            "counter_not_worth",
            "spreadsheet pain not worth",
            status=ToolCallStatus.failure,
            error="upstream refused",
        ),
    ]

    coverage = assess_coverage(REQUIRED, attempts, _requirements(min_queries=9))

    assert coverage.status is CoverageStatus.incomplete
    assert coverage.reasons
    for reason in coverage.reasons:
        assert "spreadsheet" not in reason
        assert "secret.example" not in reason.lower()
        assert "http" not in reason.lower()


def test_the_distinct_domains_are_lowercased_and_trimmed():
    attempts = [
        _attempt("competitor_alternative", "spreadsheet pain alternative", domains=[" One.Example "]),
        _attempt(
            "counter_not_worth",
            "spreadsheet pain not worth",
            domains=["one.example", "TWO.EXAMPLE"],
        ),
    ]

    coverage = assess_coverage(REQUIRED, attempts, _requirements(min_domains=2))

    assert coverage.distinct_domains == ["one.example", "two.example"]


def test_a_negative_document_count_is_refused():
    with pytest.raises(ValidationError) as excinfo:
        _attempt("competitor_alternative", "spreadsheet pain alternative", document_count=-1)

    assert "document_count" in str(excinfo.value)


def test_a_negative_floor_is_refused():
    with pytest.raises(ValidationError) as excinfo:
        CoverageRequirements(min_queries=-1)

    assert "min_queries" in str(excinfo.value)

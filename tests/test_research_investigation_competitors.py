from __future__ import annotations

import pytest
from pydantic import ValidationError

from product_hunter.research.evidence import ToolCallStatus
from product_hunter.research.investigation.competitors import (
    Competitor,
    CompetitorAnalysis,
    CompetitorAnalysisStatus,
    CompetitorConfidence,
    analyse_competitors,
)
from product_hunter.research.investigation.coverage import (
    CoverageRequirements,
    ResearchCoverage,
    SearchAttempt,
    assess_coverage,
)

REQUIRED = ["competitor_alternative", "competitor_existing_solution"]


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


def _complete_coverage() -> ResearchCoverage:
    attempts = [
        _attempt("competitor_alternative", "spreadsheet pain alternative"),
        _attempt(
            "competitor_existing_solution",
            "spreadsheet pain existing solution",
            domains=["two.example"],
        ),
    ]
    return assess_coverage(REQUIRED, attempts, _requirements())


def _incomplete_coverage() -> ResearchCoverage:
    attempts = [_attempt("competitor_alternative", "spreadsheet pain alternative")]
    return assess_coverage(REQUIRED, attempts, _requirements())


def _competitor(**overrides) -> Competitor:
    data = {
        "competitor_id": "comp-1",
        "name": "Sheetwise",
        "confidence": CompetitorConfidence.high,
    }
    data.update(overrides)
    return Competitor(**data)


def test_competitors_found_gives_the_highest_confidence():
    competitors = [
        _competitor(competitor_id="comp-1", confidence=CompetitorConfidence.low),
        _competitor(competitor_id="comp-2", confidence=CompetitorConfidence.high),
        _competitor(competitor_id="comp-3", confidence=CompetitorConfidence.medium),
    ]

    analysis = analyse_competitors(competitors, _complete_coverage(), REQUIRED)

    assert analysis.status is CompetitorAnalysisStatus.competitors_found
    assert analysis.confidence is CompetitorConfidence.high
    assert len(analysis.competitors) == 3


def test_no_competitors_on_a_complete_coverage_is_no_competitors_found():
    analysis = analyse_competitors([], _complete_coverage(), REQUIRED)

    assert analysis.status is CompetitorAnalysisStatus.no_competitors_found
    assert analysis.confidence is CompetitorConfidence.medium
    assert analysis.unknowns


def test_no_competitors_on_a_complete_coverage_is_never_high_confidence():
    # A complete search of a narrow slice is not proof of absence, so the
    # confidence is capped at medium.
    with pytest.raises(ValidationError) as excinfo:
        CompetitorAnalysis(
            status=CompetitorAnalysisStatus.no_competitors_found,
            competitors=[],
            confidence=CompetitorConfidence.high,
            coverage=_complete_coverage(),
        )

    assert "high confidence" in str(excinfo.value)


def test_no_competitors_on_an_incomplete_coverage_is_unknown():
    analysis = analyse_competitors([], _incomplete_coverage(), REQUIRED)

    assert analysis.status is CompetitorAnalysisStatus.unknown
    assert analysis.confidence is CompetitorConfidence.unknown


def test_an_incomplete_coverage_never_reaches_no_competitors_found():
    with pytest.raises(ValidationError) as excinfo:
        CompetitorAnalysis(
            status=CompetitorAnalysisStatus.no_competitors_found,
            competitors=[],
            confidence=CompetitorConfidence.medium,
            coverage=_incomplete_coverage(),
        )

    assert "complete coverage" in str(excinfo.value)


def test_the_unknowns_are_the_union_of_the_competitors_unknowns():
    competitors = [
        _competitor(
            competitor_id="comp-1",
            confidence=CompetitorConfidence.low,
            unknowns=["the pricing is not published"],
        ),
        _competitor(
            competitor_id="comp-2",
            confidence=CompetitorConfidence.low,
            unknowns=["the customer segment is unknown", "the pricing is not published"],
        ),
    ]

    analysis = analyse_competitors(competitors, _complete_coverage(), REQUIRED)

    assert analysis.unknowns == [
        "the pricing is not published",
        "the customer segment is unknown",
    ]


def test_a_competitor_with_an_unknown_confidence_must_say_what_is_unknown():
    with pytest.raises(ValidationError) as excinfo:
        _competitor(confidence=CompetitorConfidence.unknown)

    assert "must name what is unknown" in str(excinfo.value)

    competitor = _competitor(
        confidence=CompetitorConfidence.unknown,
        unknowns=["the pricing is not published"],
    )
    assert competitor.unknowns == ["the pricing is not published"]


def test_an_unknown_analysis_carries_an_unknown_confidence():
    with pytest.raises(ValidationError) as excinfo:
        CompetitorAnalysis(
            status=CompetitorAnalysisStatus.unknown,
            competitors=[],
            confidence=CompetitorConfidence.medium,
            coverage=_incomplete_coverage(),
        )

    assert "unknown confidence" in str(excinfo.value)


def test_the_coverage_reasons_reach_the_unknowns():
    coverage = assess_coverage(
        REQUIRED,
        [_attempt("competitor_alternative", "spreadsheet pain alternative")],
        _requirements(),
    )

    analysis = analyse_competitors([], coverage, REQUIRED)

    assert analysis.status is CompetitorAnalysisStatus.unknown
    assert coverage.reasons
    for reason in coverage.reasons:
        assert reason in analysis.unknowns

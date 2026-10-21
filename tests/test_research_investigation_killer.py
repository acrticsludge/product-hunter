from __future__ import annotations

import pytest
from pydantic import ValidationError

from product_hunter.research.evidence import ToolCallStatus
from product_hunter.research.investigation.coverage import SearchAttempt
from product_hunter.research.investigation.startup_killer import (
    DimensionAssessment,
    DimensionStatus,
    FalsificationDimension,
    StartupKillerReport,
    StartupKillerVerdict,
    derive_killer_verdict,
)

FAMILY = "killer_problem_existence"


def _attempt(**overrides) -> SearchAttempt:
    data = {
        "family": FAMILY,
        "query": "spreadsheet pain is this a real problem",
        "status": ToolCallStatus.success,
        "document_count": 1,
        # Two sources: a dimension that read one document from one source has
        # not survived anything.
        "domains": ["one.example", "two.example"],
        "tool_call_id": "call-1",
    }
    data.update(overrides)
    return SearchAttempt(**data)


def _assessment(**overrides) -> DimensionAssessment:
    data = {
        "dimension": FalsificationDimension.problem_existence,
        "status": DimensionStatus.survived,
        "attempts": [_attempt()],
        "evidence_refs": ["call-1"],
        "reason": "the attempt to kill this dimension did not succeed",
    }
    data.update(overrides)
    return DimensionAssessment(**data)


def _report(assessments: list[DimensionAssessment], **overrides) -> StartupKillerReport:
    data = {
        "verdict": derive_killer_verdict(assessments),
        "assessments": assessments,
        "dimensions_covered": len({a.dimension for a in assessments}),
    }
    data.update(overrides)
    return StartupKillerReport(**data)


def _all_survived() -> list[DimensionAssessment]:
    return [
        DimensionAssessment(
            dimension=dimension,
            status=DimensionStatus.survived,
            attempts=[
                _attempt(
                    family=f"killer_{dimension.value}",
                    query=f"spreadsheet pain {dimension.value}",
                    tool_call_id=f"call-{dimension.value}",
                )
            ],
            evidence_refs=[f"call-{dimension.value}"],
            reason="the attempt to kill this dimension did not succeed",
        )
        for dimension in FalsificationDimension
    ]


def test_a_dimension_with_no_attempts_is_inconclusive():
    assessment = _assessment(status=DimensionStatus.inconclusive, attempts=[])

    assert assessment.status is DimensionStatus.inconclusive


def test_survived_without_an_attempt_is_refused():
    with pytest.raises(ValidationError) as excinfo:
        _assessment(attempts=[])

    assert "not survived anything" in str(excinfo.value)


def test_a_dimension_with_an_attempt_and_an_incomplete_coverage_is_inconclusive():
    assessment = _assessment(
        status=DimensionStatus.inconclusive,
        attempts=[_attempt(status=ToolCallStatus.failure, tool_call_id="call-1")],
    )

    assert assessment.status is DimensionStatus.inconclusive


def test_survived_on_an_incomplete_coverage_is_refused():
    with pytest.raises(ValidationError) as excinfo:
        _assessment(attempts=[_attempt(status=ToolCallStatus.failure)])

    assert "complete coverage" in str(excinfo.value)


def test_a_dimension_with_an_attempt_and_a_complete_coverage_can_be_survived():
    assessment = _assessment()

    assert assessment.status is DimensionStatus.survived


def test_a_dimension_on_one_source_is_refused_as_survived():
    with pytest.raises(ValidationError) as excinfo:
        _assessment(attempts=[_attempt(domains=["one.example"])])

    assert "complete coverage" in str(excinfo.value)


def test_a_dimension_on_two_sources_can_be_survived():
    assessment = _assessment()

    assert assessment.status is DimensionStatus.survived


def test_the_assessment_records_the_bar_it_was_judged_against():
    assessment = _assessment()

    assert assessment.requirements.min_domains == 2
    assert assessment.requirements.min_documents == 1


def test_a_falsified_dimension_needs_a_reason():
    with pytest.raises(ValidationError) as excinfo:
        _assessment(status=DimensionStatus.falsified, reason="")

    assert "name the disproof" in str(excinfo.value)


def test_a_falsified_dimension_gives_the_report_a_falsified_verdict():
    assessment = _assessment(
        status=DimensionStatus.falsified,
        reason="the search for this dimension returned a disproof",
    )

    report = _report([assessment])

    assert report.verdict is StartupKillerVerdict.falsified


def test_a_report_that_covers_eight_of_ten_dimensions_is_inconclusive():
    assessments = _all_survived()[:8]

    report = _report(assessments)

    assert report.dimensions_covered == 8
    assert report.dimensions_required == 10
    assert report.verdict is StartupKillerVerdict.inconclusive


def test_a_report_that_covers_every_dimension_survived():
    report = _report(_all_survived())

    assert report.dimensions_covered == 10
    assert report.verdict is StartupKillerVerdict.survived


def test_one_inconclusive_dimension_makes_the_report_inconclusive():
    assessments = _all_survived()
    assessments[3] = DimensionAssessment(
        dimension=FalsificationDimension.severity,
        status=DimensionStatus.inconclusive,
        attempts=[],
        reason="the dimension was not attacked",
    )

    report = _report(assessments)

    assert report.verdict is StartupKillerVerdict.inconclusive


def test_the_report_has_no_validated_verdict():
    assert "validated" not in StartupKillerVerdict.__members__
    assert StartupKillerVerdict.survived.value == "survived"


def test_the_verdict_must_match_the_assessments():
    with pytest.raises(ValidationError) as excinfo:
        StartupKillerReport(
            verdict=StartupKillerVerdict.survived,
            assessments=_all_survived()[:8],
            dimensions_covered=8,
        )

    assert "verdict does not match" in str(excinfo.value)

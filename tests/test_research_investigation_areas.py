from __future__ import annotations

import pytest
from pydantic import ValidationError

from product_hunter.research.evidence import ToolCallStatus
from product_hunter.research.investigation.areas import (
    AreaFinding,
    AreaResearch,
    ResearchArea,
    WtpTier,
)
from product_hunter.research.investigation.coverage import (
    CoverageRequirements,
    SearchAttempt,
    assess_coverage,
)
from product_hunter.research.investigation.queries import area_templates

REQUIRED = ["market_validation"]


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
    data = {"min_queries": 1, "min_documents": 1, "min_domains": 1}
    data.update(overrides)
    return CoverageRequirements(**data)


def _coverage(attempts: list[SearchAttempt]):
    return assess_coverage(REQUIRED, attempts, _requirements())


def _complete_coverage():
    return _coverage([_attempt("market_validation", "spreadsheet pain market demand")])


def _incomplete_coverage():
    return _coverage(
        [
            _attempt(
                "market_validation",
                "spreadsheet pain market demand",
                status=ToolCallStatus.failure,
                error="upstream refused",
            )
        ]
    )


def _finding(**overrides) -> AreaFinding:
    data = {
        "finding_id": "finding-1",
        "area": ResearchArea.market_validation,
        "summary": "a vendor already sells this",
    }
    data.update(overrides)
    return AreaFinding(**data)


def test_an_area_with_a_complete_coverage_and_no_findings_is_complete_and_empty():
    research = AreaResearch(
        area=ResearchArea.market_validation,
        findings=[],
        coverage=_complete_coverage(),
        complete=True,
    )

    # The research ran and found nothing, which is a finding in itself.
    assert research.complete is True
    assert research.findings == []


def test_an_area_with_findings_and_an_incomplete_coverage_is_not_complete():
    coverage = _incomplete_coverage()

    research = AreaResearch(
        area=ResearchArea.market_validation,
        findings=[_finding()],
        coverage=coverage,
        complete=False,
        reasons=list(coverage.reasons),
    )

    assert research.complete is False
    assert research.reasons


def test_an_area_that_claims_completeness_on_a_thin_coverage_is_refused():
    with pytest.raises(ValidationError) as excinfo:
        AreaResearch(
            area=ResearchArea.market_validation,
            findings=[_finding()],
            coverage=_incomplete_coverage(),
            complete=True,
        )

    assert "complete only when its coverage is complete" in str(excinfo.value)


def test_the_wtp_tier_is_refused_on_a_non_wtp_area():
    with pytest.raises(ValidationError) as excinfo:
        AreaResearch(
            area=ResearchArea.market_validation,
            findings=[],
            coverage=_complete_coverage(),
            complete=True,
            wtp_tier=WtpTier.w3,
        )

    assert "willingness-to-pay tier" in str(excinfo.value)


def test_the_wtp_tier_is_allowed_on_the_wtp_area():
    research = AreaResearch(
        area=ResearchArea.willingness_to_pay,
        findings=[],
        coverage=_complete_coverage(),
        complete=True,
        wtp_tier=WtpTier.w3,
    )

    assert research.wtp_tier is WtpTier.w3


def test_a_finding_is_filed_under_the_area_it_names():
    with pytest.raises(ValidationError) as excinfo:
        AreaResearch(
            area=ResearchArea.market_validation,
            findings=[_finding(area=ResearchArea.distribution)],
            coverage=_complete_coverage(),
            complete=True,
        )

    assert "filed under the area it names" in str(excinfo.value)


def test_a_zero_floor_is_refused():
    # A floor of zero is not a floor: it switches the check off without
    # saying so.
    for field in ("min_queries", "min_documents", "min_domains"):
        with pytest.raises(ValidationError) as excinfo:
            CoverageRequirements(**{field: 0})
        assert field in str(excinfo.value)


def test_a_finding_with_a_tier_outside_the_wtp_area_is_refused():
    with pytest.raises(ValidationError) as excinfo:
        _finding(wtp_tier=WtpTier.w3)

    assert "willingness-to-pay tier" in str(excinfo.value)


def test_a_wtp_finding_may_carry_a_tier():
    finding = _finding(area=ResearchArea.willingness_to_pay, wtp_tier=WtpTier.w4)

    assert finding.wtp_tier is WtpTier.w4


def test_the_area_templates_cover_every_area():
    for area in ResearchArea:
        templates = area_templates(area)

        assert templates, f"the {area.value} area has no templates"
        for template in templates:
            assert "{problem}" in template.template

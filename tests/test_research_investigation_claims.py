from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from product_hunter.research.evidence import ClaimKind
from product_hunter.research.investigation.claims import (
    ClaimEvidence,
    ClaimStatus,
    InvestigationClaim,
    derive_claim_status,
)
from product_hunter.research.investigation.provenance import InvestigationProvenance

FROZEN = datetime(2026, 10, 21, 12, 0, 0, tzinfo=timezone.utc)
NAIVE = datetime(2026, 10, 21, 12, 0, 0)


def _provenance(*refs: str) -> InvestigationProvenance:
    return InvestigationProvenance(
        run_id="run-1",
        stage="investigation",
        agent="researcher",
        tool_call_ids=list(refs),
        recorded_at=FROZEN,
    )


def _evidence(ref: str = "call-1") -> ClaimEvidence:
    return ClaimEvidence(ref=ref, summary="the search returned a competitor")


def _claim(**overrides) -> InvestigationClaim:
    data = {
        "claim_id": "claim-1",
        "kind": ClaimKind.competitors_exist,
        "statement": "a competitor ships this",
        "stage": "investigation",
    }
    data.update(overrides)
    return InvestigationClaim(**data)


def test_derive_claim_status_reads_the_evidence():
    assert derive_claim_status([], [], False) is ClaimStatus.unknown
    assert derive_claim_status([_evidence()], [], False) is ClaimStatus.supported
    assert derive_claim_status([_evidence()], [_evidence("call-2")], False) is (
        ClaimStatus.contradicted
    )
    assert derive_claim_status([_evidence()], [_evidence("call-2")], True) is (
        ClaimStatus.supported
    )


def test_a_claim_with_no_evidence_is_unknown():
    claim = _claim()

    assert claim.status is ClaimStatus.unknown
    assert claim.supporting_evidence == []
    assert claim.evidence_refs == []


def test_a_claim_with_no_evidence_cannot_be_constructed_as_supported():
    with pytest.raises(ValidationError) as excinfo:
        _claim(status=ClaimStatus.supported)

    assert "does not match the derived status" in str(excinfo.value)


def test_a_supported_claim_needs_a_provenance_that_carries_evidence():
    with pytest.raises(ValidationError) as excinfo:
        _claim(
            status=ClaimStatus.supported,
            supporting_evidence=[_evidence()],
            provenance=_provenance(),
        )

    assert "provenance that carries evidence" in str(excinfo.value)


def test_a_claim_whose_evidence_is_not_in_the_provenance_is_refused():
    with pytest.raises(ValidationError) as excinfo:
        _claim(
            status=ClaimStatus.supported,
            supporting_evidence=[_evidence("call-unknown")],
            provenance=_provenance("call-1"),
        )

    assert "provenance cannot resolve" in str(excinfo.value)


def test_contradicting_evidence_without_a_reconciliation_is_contradicted():
    claim = _claim(
        status=ClaimStatus.contradicted,
        supporting_evidence=[_evidence("call-1")],
        contradicting_evidence=[_evidence("call-2")],
        provenance=_provenance("call-1", "call-2"),
    )

    assert claim.status is ClaimStatus.contradicted


def test_a_derived_contradiction_may_be_softened_to_inconclusive():
    claim = _claim(
        status=ClaimStatus.inconclusive,
        supporting_evidence=[_evidence("call-1")],
        contradicting_evidence=[_evidence("call-2")],
        provenance=_provenance("call-1", "call-2"),
    )

    assert claim.status is ClaimStatus.inconclusive


def test_nothing_else_may_be_softened():
    # An unknown claim may not be softened to inconclusive.
    with pytest.raises(ValidationError) as excinfo:
        _claim(status=ClaimStatus.inconclusive)
    assert "does not match the derived status" in str(excinfo.value)

    # A supported claim may not be softened to inconclusive either.
    with pytest.raises(ValidationError) as excinfo:
        _claim(
            status=ClaimStatus.inconclusive,
            supporting_evidence=[_evidence("call-1")],
            provenance=_provenance("call-1"),
        )
    assert "does not match the derived status" in str(excinfo.value)


def test_a_supported_claim_cannot_be_asserted_against_a_contradiction():
    with pytest.raises(ValidationError) as excinfo:
        _claim(
            status=ClaimStatus.supported,
            supporting_evidence=[_evidence("call-1")],
            contradicting_evidence=[_evidence("call-2")],
            provenance=_provenance("call-1", "call-2"),
        )

    assert "does not match the derived status" in str(excinfo.value)


def test_a_reconciled_contradiction_is_supported():
    claim = _claim(
        status=ClaimStatus.supported,
        supporting_evidence=[_evidence("call-1")],
        contradicting_evidence=[_evidence("call-2")],
        provenance=_provenance("call-1", "call-2"),
        reconciliation="the counter example call-2 was out of date",
    )

    assert claim.status is ClaimStatus.supported


def test_a_reconciliation_that_names_no_evidence_does_not_reconcile():
    # A sentence that names no evidence is an opinion, not a resolution, and a
    # placeholder padded into a sentence is still a placeholder.
    for text in ("n/a", "none", "n/a n/a n/a n/a", "the counter example was out of date"):
        with pytest.raises(ValidationError) as excinfo:
            _claim(
                status=ClaimStatus.supported,
                supporting_evidence=[_evidence("call-1")],
                contradicting_evidence=[_evidence("call-2")],
                provenance=_provenance("call-1", "call-2"),
                reconciliation=text,
            )

        assert "does not match the derived status" in str(excinfo.value)


def test_the_evidence_refs_are_filled_from_the_supporting_evidence():
    claim = _claim(
        status=ClaimStatus.supported,
        supporting_evidence=[_evidence("call-1"), _evidence("call-2")],
        provenance=_provenance("call-1", "call-2"),
    )

    assert claim.evidence_refs == ["call-1", "call-2"]


def test_the_confidence_is_bounded():
    with pytest.raises(ValidationError) as excinfo:
        _claim(confidence=1.5)
    assert "confidence" in str(excinfo.value)

    with pytest.raises(ValidationError) as excinfo:
        _claim(confidence=-0.1)
    assert "confidence" in str(excinfo.value)

    assert _claim(confidence=0.5).confidence == 0.5


def test_the_kind_accepts_the_investigation_kinds():
    for kind in (
        "competitors_exist",
        "existing_solution_exists",
        "market_validated",
        "willingness_to_pay",
        "distribution_accessible",
        "problem_exists",
        "problem_recurs",
        "problem_is_severe",
        "independent_population_exists",
        "market_is_large",
        "buildable",
        "timing_is_right",
    ):
        assert _claim(kind=kind).kind is ClaimKind(kind)


def test_the_evidence_weight_is_bounded():
    with pytest.raises(ValidationError) as excinfo:
        ClaimEvidence(ref="call-1", summary="a finding", weight=1.1)
    assert "weight" in str(excinfo.value)


def test_a_naive_provenance_timestamp_is_refused():
    with pytest.raises(ValidationError) as excinfo:
        InvestigationProvenance(
            run_id="run-1",
            stage="investigation",
            agent="researcher",
            recorded_at=NAIVE,
        )

    assert "recorded_at must be timezone-aware" in str(excinfo.value)


def test_the_provenance_resolves_the_refs_it_knows():
    provenance = _provenance("call-1")

    assert provenance.known_refs() == {"call-1"}
    assert provenance.covers("call-1") is True
    assert provenance.covers("call-2") is False
    assert provenance.has_evidence is True
    assert _provenance().has_evidence is False

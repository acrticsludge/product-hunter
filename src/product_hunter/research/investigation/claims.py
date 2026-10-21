"""The claims an investigation makes, and the rule that keeps them honest.

A claim is a conclusion with its evidence attached. The status is derived
from that evidence, not asserted: a claim with no evidence cannot be
supported, and a claim whose evidence the provenance cannot resolve is
refused at construction.
"""

from __future__ import annotations

from collections.abc import Sequence
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from product_hunter.research.evidence import ResearchClaim
from product_hunter.research.investigation.provenance import InvestigationProvenance


class ClaimStatus(str, Enum):
    supported = "supported"
    contradicted = "contradicted"
    inconclusive = "inconclusive"
    unknown = "unknown"


class ClaimEvidence(BaseModel):
    """One piece of evidence behind a claim."""

    model_config = ConfigDict(extra="forbid")

    ref: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    source_url: str | None = None
    tool_call_id: str | None = None
    query_family: str | None = None
    weight: float = Field(default=1.0, ge=0.0, le=1.0)


def derive_claim_status(
    supporting: Sequence[ClaimEvidence],
    contradicting: Sequence[ClaimEvidence],
    reconciled: bool,
) -> ClaimStatus:
    """Derive the status the evidence supports."""
    if not supporting or sum(item.weight for item in supporting) <= 0:
        # Evidence that counts for nothing is not evidence.
        return ClaimStatus.unknown
    if contradicting and not reconciled:
        return ClaimStatus.contradicted
    return ClaimStatus.supported


# A reconciliation has to name the contradiction it addresses. Naming the
# evidence is what makes it a reconciliation: a placeholder names nothing,
# and a sentence that names no evidence is an opinion, not a resolution.
def _reconciles(reconciliation: str, contradicting: Sequence[ClaimEvidence]) -> bool:
    """Report whether the reconciliation names the contradiction it addresses."""
    text = reconciliation.strip()
    if not text:
        return False
    return any(item.ref in text for item in contradicting)


class InvestigationClaim(ResearchClaim):
    """A wave 1 claim with the investigation's evidence attached."""

    status: ClaimStatus = ClaimStatus.unknown
    supporting_evidence: list[ClaimEvidence] = Field(default_factory=list)
    contradicting_evidence: list[ClaimEvidence] = Field(default_factory=list)
    provenance: InvestigationProvenance | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    reconciliation: str = ""

    @model_validator(mode="after")
    def _check_invariants(self) -> InvestigationClaim:
        reconciled = _reconciles(self.reconciliation, self.contradicting_evidence)
        derived = derive_claim_status(
            self.supporting_evidence, self.contradicting_evidence, reconciled
        )
        softened = derived is ClaimStatus.contradicted and self.status is ClaimStatus.inconclusive
        if self.status is not derived and not softened:
            # A claim may not be softer than its evidence, and it may not be
            # stronger either: "supported" against an unknown or a
            # contradiction is the failure this wave exists to prevent.
            raise ValueError(
                f"the status {self.status.value!r} does not match the derived "
                f"status {derived.value!r}"
            )
        if self.status is ClaimStatus.supported and (
            self.provenance is None or not self.provenance.has_evidence
        ):
            raise ValueError("a supported claim needs a provenance that carries evidence")
        if self.provenance is not None:
            known = self.provenance.known_refs()
            for item in (*self.supporting_evidence, *self.contradicting_evidence):
                if item.ref not in known:
                    raise ValueError("the claim carries evidence the provenance cannot resolve")
        if not self.evidence_refs:
            # The wave 1 predicate reads evidence_refs, so the claim carries
            # its own evidence there.
            self.evidence_refs = [item.ref for item in self.supporting_evidence]
        return self

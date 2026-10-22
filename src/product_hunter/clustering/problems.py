"""The pain points the wave reads, and the canonical form it compares.

The canonical form is derived from the controlled fields, not from the prose.
Two records that describe one problem in different words reach the same
fingerprint, and the prose is left in the canonical text for the embedder to
read, never in the fingerprint.
"""

from __future__ import annotations

import hashlib

from pydantic import BaseModel, ConfigDict, Field

from product_hunter.normalization import Domain

# The separator between the parts of a digest. It cannot occur in a field.
_SEPARATOR = "\x1f"


class PainPoint(BaseModel):
    """One accepted pain point, as Phase 1 stored it.

    A pain point whose accepted flag is false is not an input, and a pain
    point without a normalized key is not an input either: it never went
    through normalisation, so it has no versioned identity to compare.
    """

    model_config = ConfigDict(extra="forbid")

    pain_id: str = Field(min_length=1)
    doc_id: str = Field(min_length=1)
    source_id: str = ""
    span_id: str = ""
    quote: str = ""
    domain: Domain
    actor: str = Field(min_length=1)
    action: str = Field(min_length=1)
    object: str = Field(min_length=1)
    consequence: str = Field(min_length=1)
    pain_statement: str = Field(min_length=1)
    normalized_key: str = Field(min_length=1)
    normalization_version: str = Field(min_length=1)
    severity: int = Field(default=0, ge=0, le=5)
    frequency: str = ""


class CanonicalProblem(BaseModel):
    """The model-independent form of one pain point.

    This is what the wave embeds and what it compares. The fingerprint is
    the exact-identity signal: two pain points with the same fingerprint name
    the same actor, the same task, the same object and the same cost.
    """

    model_config = ConfigDict(extra="forbid")

    pain_id: str = Field(min_length=1)
    doc_id: str = Field(min_length=1)
    source_id: str = ""
    span_id: str = ""
    quote: str = ""
    domain: Domain
    actor: str = Field(min_length=1)
    action: str = Field(min_length=1)
    object: str = Field(min_length=1)
    consequence: str = Field(min_length=1)
    pain_statement: str = Field(min_length=1)
    normalized_key: str = Field(min_length=1)
    normalization_version: str = Field(min_length=1)
    canonical_text: str = Field(min_length=1)
    fingerprint: str = Field(min_length=1)


def fingerprint(pain: PainPoint) -> str:
    """The digest of the normalisation version and the five controlled fields.

    The pain statement is not in it, so two records that describe the same
    problem with different prose share a fingerprint. The fields are compared
    the way the key compares them, case aside: a record that says ``Ops`` and
    a record that says ``ops`` name the same actor.
    """
    parts = (
        pain.normalization_version,
        pain.domain.value,
        pain.actor,
        pain.action,
        pain.object,
        pain.consequence,
    )
    basis = _SEPARATOR.join(part.strip().lower() for part in parts)
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()


def canonical_text(pain: PainPoint) -> str:
    """The rendering the wave embeds.

    The fields come in a fixed order with one label each, so the same pain
    point always renders to the same string and two renderings can be
    compared without knowing where one field ends.
    """
    return "\n".join(
        (
            f"domain: {pain.domain.value}",
            f"actor: {pain.actor}",
            f"action: {pain.action}",
            f"object: {pain.object}",
            f"consequence: {pain.consequence}",
            f"pain_statement: {pain.pain_statement}",
        )
    )


def canonicalize(pain: PainPoint) -> CanonicalProblem:
    """Build the canonical form of one pain point."""
    return CanonicalProblem(
        pain_id=pain.pain_id,
        doc_id=pain.doc_id,
        source_id=pain.source_id,
        span_id=pain.span_id,
        quote=pain.quote,
        domain=pain.domain,
        actor=pain.actor,
        action=pain.action,
        object=pain.object,
        consequence=pain.consequence,
        pain_statement=pain.pain_statement,
        normalized_key=pain.normalized_key,
        normalization_version=pain.normalization_version,
        canonical_text=canonical_text(pain),
        fingerprint=fingerprint(pain),
    )

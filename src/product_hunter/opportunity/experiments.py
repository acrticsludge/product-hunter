"""The validation experiment: what would settle the thesis, and what was measured.

An experiment is a record, not a result. The observed result starts absent,
and the conclusion is computed from it, so no run can assert a conclusion it
did not earn.
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field, field_validator

from product_hunter.opportunity.signals import WtpTier

if TYPE_CHECKING:  # pragma: no cover - the import is for the type only
    from product_hunter.opportunity.policy import ScoringPolicy


class Comparator(str, Enum):
    """The comparison a threshold makes."""

    at_least = "at_least"
    at_most = "at_most"
    above = "above"
    below = "below"


class ThresholdUnit(str, Enum):
    """What the number of a threshold counts.

    A threshold of five means five of something, and the unit says what: a
    count of people is not a count of signups, and neither is an amount of
    money.
    """

    people = "people"
    signups = "signups"
    count = "count"
    percent = "percent"
    money_usd = "money_usd"


class Threshold(BaseModel):
    """The comparison that would settle the hypothesis.

    The unit says what the number counts, so a comparison against a count of
    people is never read as a comparison against an amount of money. The
    value is finite: a broken threshold settles nothing.
    """

    model_config = ConfigDict(extra="forbid")

    comparator: Comparator
    value: float = Field(allow_inf_nan=False)
    unit: ThresholdUnit


class ObservedResult(BaseModel):
    """The measurement, recorded after the method ran.

    A result carries who took it, when, and the evidence behind it. A result
    without that record cannot be stored, so no experiment ships with a
    measurement it did not record. The value is finite: a broken measurement
    settles nothing, and it may not refute a thesis either.
    """

    model_config = ConfigDict(extra="forbid")

    value: float = Field(allow_inf_nan=False)
    sample_size: int = Field(ge=0)
    taken_at: datetime
    taken_by: str = Field(min_length=1)
    evidence_ids: list[str] = Field(min_length=1)

    @field_validator("taken_at")
    @classmethod
    def _taken_at_is_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
            raise ValueError("taken_at must be timezone-aware")
        return value


class Conclusion(str, Enum):
    """What the observed result settled."""

    pending = "pending"
    supported = "supported"
    refuted = "refuted"
    inconclusive = "inconclusive"


class ValidationExperiment(BaseModel):
    """What the run believes, how it would be tested, and what was measured.

    The conclusion is not a field. It is computed from the observed result by
    ``conclude``, so it cannot be asserted.
    """

    model_config = ConfigDict(extra="forbid")

    experiment_id: str = Field(min_length=1)
    hypothesis: str = Field(min_length=1)
    method: str = Field(min_length=1)
    success_threshold: Threshold
    observed_result: ObservedResult | None = None


def conclude(experiment: ValidationExperiment, policy: ScoringPolicy) -> Conclusion:
    """Compute the conclusion of an experiment from its observed result.

    With no observed result the experiment is pending. A result whose sample
    size falls under the policy minimum is inconclusive whatever its value
    says, because a measurement of nothing settles nothing.
    """
    result = experiment.observed_result
    if result is None:
        return Conclusion.pending
    if result.sample_size < policy.experiment.min_sample_size:
        return Conclusion.inconclusive

    threshold = experiment.success_threshold
    value = result.value
    if threshold.comparator is Comparator.at_least:
        settled = value >= threshold.value
    elif threshold.comparator is Comparator.at_most:
        settled = value <= threshold.value
    elif threshold.comparator is Comparator.above:
        settled = value > threshold.value
    else:
        settled = value < threshold.value
    return Conclusion.supported if settled else Conclusion.refuted


def recommend_experiment(
    opportunity_id: str,
    *,
    wtp_tier: WtpTier,
    confidence: float,
    independent_people: int,
    policy: ScoringPolicy,
) -> ValidationExperiment:
    """Recommend the experiment an opportunity should run next.

    The tier picks the template, and the confidence and the independent
    population pick between the validation template and the commercial one:
    a thin evidence base needs more evidence, a thick one needs a price test.
    The recommendation never carries an observed result, because nothing has
    been run.
    """
    templates = policy.experiment.templates[wtp_tier]
    if (
        confidence < policy.experiment.low_confidence_threshold
        or independent_people < policy.experiment.min_independent_people
    ):
        template = templates.validation
        kind = "validation"
    else:
        template = templates.commercial
        kind = "commercial"

    # The basis carries the template's own content, so a template that
    # changes its method moves the id with the record it identifies.
    basis = "|".join(
        (
            opportunity_id,
            wtp_tier.value,
            kind,
            template.hypothesis,
            template.method,
            template.threshold.comparator.value,
            f"{template.threshold.value}",
            template.threshold.unit.value,
        )
    )
    experiment_id = "exp_" + hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16]
    return ValidationExperiment(
        experiment_id=experiment_id,
        hypothesis=template.hypothesis,
        method=template.method,
        success_threshold=template.threshold,
    )

"""The scoring policy: the weights, the factor tables, and the thresholds.

Every number the scorer reads comes from the versioned policy file, so two
snapshots are only comparable when their policy versions match. The loader
refuses a policy that cannot be scored with: weights that do not sum to one,
a factor table that names a value the enums do not have, or a negative
threshold.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import Enum
from pathlib import Path
from typing import Annotated

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from product_hunter.config import repo_root
from product_hunter.extraction import Frequency
from product_hunter.opportunity.experiments import Comparator, Threshold, ThresholdUnit
from product_hunter.opportunity.signals import (
    CompetitionDirection,
    SourceReliability,
    WtpSignalKind,
    WtpTier,
)

POLICY_NAME = Path("config") / "scoring_policy.yaml"

# The six components of each score. A policy that names a different set is
# refused, because a weight the scorer cannot read is a weight that does
# nothing.
POTENTIAL_COMPONENTS: tuple[str, ...] = (
    "severity",
    "frequency",
    "wtp",
    "market",
    "competition",
    "buildability",
)
CONFIDENCE_COMPONENTS: tuple[str, ...] = (
    "independent_people",
    "community_diversity",
    "source_reliability",
    "temporal_persistence",
    "counter_evidence",
    "extraction_agreement",
)

# The weights are shares of a score, so they sum to one. Floating point
# addition of the shipped weights lands a hair off one, hence the tolerance.
WEIGHT_TOLERANCE = 1e-6

# A factor is a share of a component, so it sits between zero and one.
Factor = Annotated[float, Field(ge=0, le=1)]


class PolicyError(Exception):
    """The scoring policy is invalid and cannot be used."""

    def __init__(self, errors: list[str]) -> None:
        self.errors = list(errors)
        super().__init__("; ".join(self.errors) or "the scoring policy is invalid")


class WtpFactors(BaseModel):
    """The factor of every tier, and the ceiling of every signal kind."""

    model_config = ConfigDict(extra="forbid")

    tiers: dict[WtpTier, Factor]
    signal_caps: dict[WtpSignalKind, WtpTier]


class NichePolicy(BaseModel):
    """When a small market may still be the right product."""

    model_config = ConfigDict(extra="forbid")

    factor_ceiling: Factor
    factor_floor: Factor
    min_severity: int = Field(ge=0, le=5)
    min_wtp_tier: WtpTier

    @model_validator(mode="after")
    def _floor_lifts(self) -> NichePolicy:
        if self.factor_floor < self.factor_ceiling:
            raise ValueError(
                "the niche factor_floor must not be below the factor_ceiling: "
                "the override lifts the market factor, it does not lower it"
            )
        return self


class MarketPolicy(BaseModel):
    """The market ceiling of the logarithmic mapping, and the niche override."""

    model_config = ConfigDict(extra="forbid")

    ceiling_usd: float = Field(gt=0)
    niche: NichePolicy


class BuildabilityPolicy(BaseModel):
    """What one blocker takes out of the buildability factor."""

    model_config = ConfigDict(extra="forbid")

    blocker_penalty: Factor


class IdentityPolicy(BaseModel):
    """The identity confidence an author needs to count as independent."""

    model_config = ConfigDict(extra="forbid")

    min_confidence: Factor


class ConfidencePolicy(BaseModel):
    """The targets the confidence components are measured against.

    The agreement minimum counts the extractors, not the extractions: ten
    extractions from one model are not agreement across models. The range
    fraction is a fraction of the 0-100 scale, not of the score, so the
    widest range it can produce is a quarter of the scale.
    """

    model_config = ConfigDict(extra="forbid")

    target_independent_people: int = Field(ge=1)
    target_communities: int = Field(ge=1)
    target_months: int = Field(ge=1)
    target_span_days: int = Field(ge=1)
    agreement_min_extractors: int = Field(ge=1)
    # The floor is a floor: a floor of zero is no floor at all, and would
    # hand the caller the whole counter-evidence weight.
    min_counter_evidence_families: int = Field(ge=1)
    range_max_fraction: Factor
    uncertain_share_weight: Factor
    disagreement_share_weight: Factor


class ConcentrationPolicy(BaseModel):
    """The Herfindahl index that is neutral, and the worst penalty."""

    model_config = ConfigDict(extra="forbid")

    neutral_index: float = Field(ge=0, lt=1)
    max_penalty: Factor


class LifecyclePolicy(BaseModel):
    """What a state change requires, and when evidence goes stale."""

    model_config = ConfigDict(extra="forbid")

    min_independent_people: int = Field(ge=0)
    min_confidence: float = Field(ge=0, le=100)
    staleness_days: int = Field(ge=0)


class ExperimentTemplate(BaseModel):
    """One recommended experiment: the belief, the test, and the threshold.

    The sample size is the number of observations the method proposes. It is
    a number in the policy rather than a number buried in the prose, so the
    loader can check that the threshold is reachable in that sample.
    """

    model_config = ConfigDict(extra="forbid")

    hypothesis: str = Field(min_length=1)
    method: str = Field(min_length=1)
    sample_size: int = Field(ge=1)
    threshold: Threshold


class ExperimentTemplates(BaseModel):
    """The two recommendations a tier can carry."""

    model_config = ConfigDict(extra="forbid")

    validation: ExperimentTemplate
    commercial: ExperimentTemplate


class ExperimentPolicy(BaseModel):
    """What an experiment needs before it settles anything."""

    model_config = ConfigDict(extra="forbid")

    min_sample_size: int = Field(ge=1)
    low_confidence_threshold: float = Field(ge=0, le=100)
    min_independent_people: int = Field(ge=0)
    templates: dict[WtpTier, ExperimentTemplates]


class ScoringPolicy(BaseModel):
    """The whole policy: weights, factor tables, thresholds, and requirements."""

    model_config = ConfigDict(extra="forbid")

    policy_version: str = Field(min_length=1)
    potential_weights: dict[str, float]
    confidence_weights: dict[str, float]
    frequency_factors: dict[Frequency, Factor]
    wtp_factors: WtpFactors
    competition_factors: dict[CompetitionDirection, Factor]
    competition_absent_factor_cap: Factor
    market: MarketPolicy
    buildability: BuildabilityPolicy
    identity: IdentityPolicy
    confidence: ConfidencePolicy
    concentration: ConcentrationPolicy
    source_reliability_weights: dict[SourceReliability, Factor]
    lifecycle: LifecyclePolicy
    experiment: ExperimentPolicy


def _weight_errors(name: str, weights: Mapping[str, float], components: tuple[str, ...]) -> list[str]:
    """Return one error per problem in a weight map."""
    errors: list[str] = []
    missing = sorted(set(components) - set(weights))
    if missing:
        errors.append(f"{name} is missing the weights: {', '.join(missing)}")
    extra = sorted(set(weights) - set(components))
    if extra:
        errors.append(f"{name} names the unknown components: {', '.join(extra)}")
    for component, weight in sorted(weights.items()):
        if not 0.0 <= weight <= 1.0:
            errors.append(f"{name} weight for {component!r} must be between 0 and 1")
    if not missing and not extra:
        total = sum(weights.values())
        if abs(total - 1.0) > WEIGHT_TOLERANCE:
            errors.append(f"{name} sums to {total}, not to one")
    return errors


def _coverage_errors(name: str, table: Mapping, members: type[Enum]) -> list[str]:
    """Return one error per enum member the table does not carry."""
    missing = sorted(member.value for member in members if member not in table)
    if missing:
        return [f"{name} is missing the members: {', '.join(missing)}"]
    return []


def validate_policy(policy: ScoringPolicy) -> list[str]:
    """Return one error per problem in the policy; an empty list means valid."""
    errors: list[str] = []
    errors.extend(
        _weight_errors("potential_weights", policy.potential_weights, POTENTIAL_COMPONENTS)
    )
    errors.extend(
        _weight_errors("confidence_weights", policy.confidence_weights, CONFIDENCE_COMPONENTS)
    )
    errors.extend(_coverage_errors("frequency_factors", policy.frequency_factors, Frequency))
    errors.extend(
        _coverage_errors("competition_factors", policy.competition_factors, CompetitionDirection)
    )
    errors.extend(
        _coverage_errors(
            "source_reliability_weights", policy.source_reliability_weights, SourceReliability
        )
    )
    errors.extend(_coverage_errors("wtp_factors.tiers", policy.wtp_factors.tiers, WtpTier))
    errors.extend(
        _coverage_errors("wtp_factors.signal_caps", policy.wtp_factors.signal_caps, WtpSignalKind)
    )
    errors.extend(
        _coverage_errors("experiment.templates", policy.experiment.templates, WtpTier)
    )

    range_total = (
        policy.confidence.uncertain_share_weight
        + policy.confidence.disagreement_share_weight
    )
    if abs(range_total - 1.0) > WEIGHT_TOLERANCE:
        errors.append(
            "the confidence range weights sum to "
            f"{range_total}, not to one"
        )
    errors.extend(_template_errors(policy))
    return errors


# The threshold units that count something in the sample. An amount of money
# is not a count, and neither is a rate: a percent is a share of the sample,
# not a number of things in it.
_COUNT_UNITS = frozenset(
    {
        ThresholdUnit.people,
        ThresholdUnit.signups,
        ThresholdUnit.count,
    }
)


def _template_errors(policy: ScoringPolicy) -> list[str]:
    """Return one error per experiment template that cannot settle anything.

    A method that proposes a sample below the policy minimum settles nothing,
    and a threshold that counts more than the sample holds can only ever be
    refuted.
    """
    errors: list[str] = []
    minimum = policy.experiment.min_sample_size
    for tier, templates in sorted(
        policy.experiment.templates.items(), key=lambda item: item[0].value
    ):
        for name, template in (
            ("validation", templates.validation),
            ("commercial", templates.commercial),
        ):
            if template.sample_size < minimum:
                errors.append(
                    f"the {name} template for tier {tier.value} proposes a sample "
                    f"of {template.sample_size}, below the minimum of {minimum}"
                )
            threshold = template.threshold
            if (
                threshold.comparator is Comparator.at_least
                and threshold.unit in _COUNT_UNITS
                and threshold.value > template.sample_size
            ):
                errors.append(
                    f"the {name} template for tier {tier.value} settles on "
                    f"{threshold.value} {threshold.unit.value}, which its sample "
                    f"of {template.sample_size} cannot reach"
                )
    return errors


def require_valid_policy(policy: ScoringPolicy) -> ScoringPolicy:
    """Return the policy, or raise PolicyError carrying every error."""
    errors = validate_policy(policy)
    if errors:
        raise PolicyError(errors)
    return policy


def load_scoring_policy(path: str | Path | None = None) -> ScoringPolicy:
    """Load the scoring policy from YAML and refuse an unusable one."""
    resolved = Path(path) if path is not None else repo_root() / POLICY_NAME
    try:
        text = resolved.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        # A file that is not UTF-8 text is a broken policy, not a crash.
        raise PolicyError([f"{resolved} is not UTF-8 text: {exc}"]) from exc
    except OSError as exc:
        # A file that cannot be read is a broken policy, not a crash: a
        # missing file and an unreadable one both fail closed.
        raise PolicyError([f"{resolved} cannot be read: {exc}"]) from exc
    try:
        raw = yaml.safe_load(text) or {}
    except yaml.YAMLError as exc:
        # A file that is not YAML at all is a broken policy, not a crash.
        raise PolicyError([f"{resolved} is not valid YAML: {exc}"]) from exc
    if isinstance(raw, dict) and "scoring_policy" in raw:
        raw = raw["scoring_policy"]
    try:
        policy = ScoringPolicy.model_validate(raw)
    except ValidationError as exc:
        raise PolicyError(_validation_messages(exc)) from exc
    return require_valid_policy(policy)


def _validation_messages(exc: ValidationError) -> list[str]:
    """Flatten a pydantic error into one message per problem."""
    messages: list[str] = []
    for error in exc.errors():
        location = ".".join(str(part) for part in error["loc"])
        messages.append(f"{location}: {error['msg']}" if location else error["msg"])
    return messages

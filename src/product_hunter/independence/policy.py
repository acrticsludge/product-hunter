"""The independence policy: every threshold the analysis uses, and the check
that the configuration is one the analysis may run on.

Nothing here is a constant in code. A policy that cannot be loaded is not a
default policy: the loader refuses it and names the rule it broke.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from product_hunter.config import repo_root

POLICY_NAME = Path("config") / "independence.yaml"

# A merge needs one of these. The resolver caps a pair with no strong signal
# at the suspect threshold, so a weak signal can never merge two accounts.
STRONG_SIGNALS = ("identity_key", "same_platform_handle")

# A pair with only these fired is at most a suspect, never a merge.
WEAK_SIGNALS = (
    "cross_linking",
    "model_signal",
    "signature_reuse",
    "community_overlap",
    "display_name",
)

# A negative signal caps the score and marks the pair conflicted.
NEGATIVE_SIGNALS = ("identity_key_conflict", "explicit_disavow")


class PolicyError(Exception):
    """The policy is invalid and the analysis may not run on it."""

    def __init__(self, errors: list[str]) -> None:
        self.errors = list(errors)
        super().__init__("; ".join(self.errors) or "the independence policy is invalid")


class IdentityPolicy(BaseModel):
    """How two accounts are compared, and when a pair is one person."""

    model_config = ConfigDict(extra="forbid")

    standalone_handle_confidence: float
    identity_key_bonus: float
    profile_url_bonus: float
    confident_threshold: float
    merge_threshold: float
    suspect_threshold: float
    conflict_score_cap: float
    cross_source_name_weight: float
    signal_weights: dict[str, float]
    negative_signal_weights: dict[str, float]


class PropagationPolicy(BaseModel):
    """When two mentions are the same content, and when one quotes the other."""

    model_config = ConfigDict(extra="forbid")

    repost_similarity_threshold: float
    syndication_similarity_threshold: float
    quote_min_overlap: float
    min_comparable_chars: int
    max_text_chars: int
    max_scope_mentions: int


class ConcentrationPolicy(BaseModel):
    """The limits the evidence must stay inside."""

    model_config = ConfigDict(extra="forbid")

    max_single_source_share: float
    max_single_community_share: float
    max_single_author_cluster_share: float
    max_single_propagation_chain_share: float
    min_independent_authors: int
    min_independent_communities: int


class IndependencePolicy(BaseModel):
    """The whole policy: the identity, propagation, and concentration rules."""

    model_config = ConfigDict(extra="forbid")

    version: str = Field(min_length=1)
    identity: IdentityPolicy
    propagation: PropagationPolicy
    concentration: ConcentrationPolicy


def validate_policy(policy: IndependencePolicy) -> list[str]:
    """Return one error per problem in the policy; an empty list means valid."""
    errors: list[str] = []
    identity = policy.identity

    for name in _IDENTITY_SCALARS:
        value = getattr(identity, name)
        if not 0.0 <= value <= 1.0:
            errors.append(f"identity.{name} must be in [0, 1], found {value}")

    for name, value in sorted(identity.signal_weights.items()):
        if not 0.0 <= value <= 1.0:
            errors.append(
                f"identity.signal_weights.{name} must be in [0, 1], found {value}"
            )
    for name, value in sorted(identity.negative_signal_weights.items()):
        if not 0.0 <= value <= 1.0:
            errors.append(
                f"identity.negative_signal_weights.{name} must be in [0, 1], found {value}"
            )

    errors.extend(_signal_set_errors(identity))

    if not identity.merge_threshold > identity.suspect_threshold:
        errors.append(
            "merge_threshold must be above suspect_threshold: "
            f"found merge {identity.merge_threshold} and suspect {identity.suspect_threshold}"
        )
    # The suspect threshold scores a pair and the confident threshold scores a
    # group, so they are not on one scale and are not ordered against each
    # other. What has to hold is that a group confident enough to be a person
    # is not a stricter bar than the bar for calling two accounts one person.
    if identity.confident_threshold > identity.merge_threshold:
        errors.append(
            "confident_threshold must not be above merge_threshold: "
            f"found confident {identity.confident_threshold} and "
            f"merge {identity.merge_threshold}"
        )

    model_weight = identity.signal_weights.get("model_signal", 0.0)
    if model_weight >= identity.merge_threshold:
        errors.append(
            "model_signal may not reach merge_threshold on its own: the model may not merge"
        )
    for name in WEAK_SIGNALS:
        weight = identity.signal_weights.get(name, 0.0)
        if weight >= identity.merge_threshold:
            errors.append(
                f"weak signal {name!r} may not reach merge_threshold on its own"
            )
    # The resolver caps a pair with no strong signal at the suspect threshold,
    # so a weak-only score can never reach the merge threshold while the
    # thresholds are ordered. The rule is stated here on its own, so a
    # configuration whose weak ceiling reaches the merge threshold is refused
    # even if the cap in the resolver were to change.
    weak_total = sum(identity.signal_weights.get(name, 0.0) for name in WEAK_SIGNALS)
    weak_ceiling = min(weak_total, identity.suspect_threshold)
    if weak_ceiling >= identity.merge_threshold:
        errors.append(
            "weak signals alone may not merge: their score must stay below merge_threshold"
        )

    display_weight = identity.signal_weights.get("display_name", 0.0)
    if display_weight >= identity.suspect_threshold:
        errors.append(
            "display_name may not reach suspect_threshold: a name match alone creates no edge"
        )

    propagation = policy.propagation
    for name in _SIMILARITY_FIELDS:
        value = getattr(propagation, name)
        if not 0.0 < value <= 1.0:
            errors.append(f"propagation.{name} must be in (0, 1], found {value}")
    if propagation.min_comparable_chars < 0:
        errors.append("propagation.min_comparable_chars must not be negative")
    if propagation.max_text_chars <= 0:
        errors.append("propagation.max_text_chars must be positive")
    if propagation.max_scope_mentions <= 0:
        errors.append("propagation.max_scope_mentions must be positive")

    concentration = policy.concentration
    for name in _SHARE_FIELDS:
        value = getattr(concentration, name)
        if not 0.0 < value <= 1.0:
            errors.append(f"concentration.{name} must be in (0, 1], found {value}")
    for name in _MINIMUM_FIELDS:
        value = getattr(concentration, name)
        if value < 0:
            errors.append(f"concentration.{name} must not be negative")

    return errors


def require_valid_policy(policy: IndependencePolicy) -> IndependencePolicy:
    """Return the policy, or raise PolicyError carrying every error."""
    errors = validate_policy(policy)
    if errors:
        raise PolicyError(errors)
    return policy


def load_independence_policy(path: str | Path | None = None) -> IndependencePolicy:
    """Load the policy from YAML and refuse an invalid one."""
    resolved = Path(path) if path is not None else repo_root() / POLICY_NAME
    try:
        raw = yaml.safe_load(resolved.read_text(encoding="utf-8")) or {}
    except OSError as exc:
        raise PolicyError([f"{resolved} could not be read: {exc}"]) from exc
    except yaml.YAMLError as exc:
        raise PolicyError([f"{resolved} is not valid YAML: {exc}"]) from exc
    try:
        policy = IndependencePolicy.model_validate(raw)
    except ValidationError as exc:
        raise PolicyError(_validation_messages(exc)) from exc
    return require_valid_policy(policy)


_IDENTITY_SCALARS = (
    "standalone_handle_confidence",
    "identity_key_bonus",
    "profile_url_bonus",
    "confident_threshold",
    "merge_threshold",
    "suspect_threshold",
    "conflict_score_cap",
    "cross_source_name_weight",
)

_SIMILARITY_FIELDS = (
    "repost_similarity_threshold",
    "syndication_similarity_threshold",
    "quote_min_overlap",
)

_SHARE_FIELDS = (
    "max_single_source_share",
    "max_single_community_share",
    "max_single_author_cluster_share",
    "max_single_propagation_chain_share",
)

_MINIMUM_FIELDS = ("min_independent_authors", "min_independent_communities")


def _signal_set_errors(identity: IdentityPolicy) -> list[str]:
    """The policy must weight every signal the resolver reads, and no other."""
    errors: list[str] = []
    known = set(STRONG_SIGNALS) | set(WEAK_SIGNALS)
    missing = sorted(known - set(identity.signal_weights))
    if missing:
        errors.append("identity.signal_weights is missing: " + ", ".join(missing))
    unknown = sorted(set(identity.signal_weights) - known)
    if unknown:
        errors.append("identity.signal_weights names an unknown signal: " + ", ".join(unknown))
    missing_negative = sorted(set(NEGATIVE_SIGNALS) - set(identity.negative_signal_weights))
    if missing_negative:
        errors.append(
            "identity.negative_signal_weights is missing: " + ", ".join(missing_negative)
        )
    unknown_negative = sorted(set(identity.negative_signal_weights) - set(NEGATIVE_SIGNALS))
    if unknown_negative:
        errors.append(
            "identity.negative_signal_weights names an unknown signal: "
            + ", ".join(unknown_negative)
        )
    return errors


def _validation_messages(exc: ValidationError) -> list[str]:
    """Flatten a pydantic error into one message per problem."""
    messages: list[str] = []
    for error in exc.errors():
        location = ".".join(str(part) for part in error["loc"])
        messages.append(f"{location}: {error['msg']}" if location else error["msg"])
    return messages

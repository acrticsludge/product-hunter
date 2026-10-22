"""The policy layer: what may be done to a source, and how much it carries.

This module owns the registry edits that change a source's status, the
reliability tier the registry derives for a source, and the health status the
collection layer's observed failures produce.

Nothing here writes the registry file. Every edit returns a new entry, and
the operator decides whether to paste it.

The tier vocabulary belongs to the scoring layer: this module imports it and
adds no second one.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import Enum

from product_hunter.collection.state import SourceHealth
from product_hunter.fetch import utcnow
from product_hunter.opportunity.policy import load_scoring_policy
from product_hunter.opportunity.signals import (
    SourceReliability as SourceReliabilityTier,
)
from product_hunter.sources import Collector, SourceConfig, SourceStatus, TermsStatus

# The failure rate a source may carry and still be trusted, per tier.
TIER_A_FAILURE_RATE = 0.05
TIER_B_FAILURE_RATE = 0.20
TIER_C_FAILURE_RATE = 0.50

# The failure rate at which a source is healthy, degraded, or failing.
HEALTHY_FAILURE_RATE = 0.10
DEGRADED_FAILURE_RATE = 0.25
FAILING_FAILURE_RATE = 0.50

# The consecutive failures that quarantine a source.
DEFAULT_CONSECUTIVE_FAILURE_LIMIT = 5


class PolicyRefused(Exception):
    """A registry change that was refused, and the reason it was refused."""

    def __init__(self, reason_code: str, detail: str = "") -> None:
        self.reason_code = reason_code
        super().__init__(detail or reason_code)


def retire_source(source: SourceConfig, *, reason: str, today: date) -> SourceConfig:
    """Take a source out of rotation for good.

    A source that is already retired is refused, so the second retirement
    cannot overwrite the first reason.
    """
    if source.status is SourceStatus.retired:
        raise PolicyRefused(
            "already_retired", f"source {source.source_id!r} is already retired"
        )
    if not reason:
        raise PolicyRefused("missing_reason", "a retirement needs a reason")
    return source.model_copy(
        update={
            "status": SourceStatus.retired,
            "retired_at": today,
            "retire_reason": reason,
            # A retired source was never suspended, so the suspension fields
            # are cleared rather than left to name a day that did not happen.
            "suspended_at": None,
            "suspend_reason": "",
        }
    )


def suspend_source(source: SourceConfig, *, reason: str, today: date) -> SourceConfig:
    """Take a source out of rotation for now.

    A source that is already suspended is refused, so the reason of the
    first suspension is kept.
    """
    if source.status is SourceStatus.suspended:
        raise PolicyRefused(
            "already_suspended", f"source {source.source_id!r} is already suspended"
        )
    if not reason:
        raise PolicyRefused("missing_reason", "a suspension needs a reason")
    return source.model_copy(
        update={
            "status": SourceStatus.suspended,
            "suspended_at": today,
            "suspend_reason": reason,
        }
    )


def resume_source(source: SourceConfig, *, today: date) -> SourceConfig:
    """Put a suspended source back into rotation.

    A retired source is refused: retirement is for good. A source whose
    review expired while it was suspended is refused too, because a stale
    source needs a new review and not a resume. The source comes back active
    only when its terms permit a collection, and otherwise it comes back
    with its review still open.
    """
    if source.status is SourceStatus.retired:
        raise PolicyRefused(
            "retired_source", f"source {source.source_id!r} is retired"
        )
    if (
        source.review_expires_at is not None
        and source.review_expires_at < today
    ):
        raise PolicyRefused(
            "review_expired",
            f"source {source.source_id!r} needs a new review before it comes back",
        )
    active = source.terms_status in (TermsStatus.reviewed, TermsStatus.permitted)
    return source.model_copy(
        update={
            "status": SourceStatus.active if active else SourceStatus.pending_review,
            "suspended_at": None,
            "suspend_reason": "",
        }
    )


def revalidate_source(
    source: SourceConfig,
    *,
    reviewer: str,
    reviewed_at: date,
    expires_at: date,
) -> SourceConfig:
    """Renew the review of a source.

    A source whose terms status would not permit a collection is refused: a
    review cannot make a source collectible that the terms do not allow.
    """
    if source.terms_status not in (TermsStatus.reviewed, TermsStatus.permitted):
        raise PolicyRefused(
            "terms_not_permitted",
            f"source {source.source_id!r} terms_status is {source.terms_status.value!r}",
        )
    if not reviewer:
        raise PolicyRefused("missing_reviewer", "a revalidation names its reviewer")
    if expires_at < reviewed_at:
        raise PolicyRefused(
            "invalid_expiry", "the review expires before it was made"
        )
    updates: dict[str, object] = {
        "reviewed_at": reviewed_at,
        "review_expires_at": expires_at,
        "last_reviewed_by": reviewer,
    }
    # A source that was taken out of rotation on purpose stays out of it: a
    # revalidation renews the review, it does not undo a decision.
    if source.status in (
        SourceStatus.candidate,
        SourceStatus.pending_review,
        SourceStatus.stale,
        SourceStatus.active,
    ):
        updates["status"] = SourceStatus.active
    return source.model_copy(update=updates)


def reliability_weight(tier: SourceReliabilityTier) -> float:
    """The weight the scoring policy gives a tier."""
    return float(load_scoring_policy().source_reliability_weights[tier])


@dataclass(frozen=True)
class SourceReliability:
    """The tier a source carries, the score behind it, and the basis for it.

    A tier without a basis is not a tier, so the basis names the terms
    status, the collector, the request cap, and the failure rate when there
    were attempts to measure it.
    """

    tier: SourceReliabilityTier
    score: float
    basis: tuple[str, ...]
    observed_failure_rate: float | None
    observed_attempts: int


def derive_reliability(
    source: SourceConfig,
    health: SourceHealth | None = None,
    *,
    today: date | None = None,
) -> SourceReliability:
    """The tier the registry derives for a source.

    The tier is derived, never set. It follows the terms status, the
    collector, the review, and the failure rate the collection layer
    observed.
    """
    moment = today if today is not None else utcnow().date()
    attempts = health.attempts if health is not None else 0
    failures = sum(health.failures.values()) if health is not None else 0
    rate = (failures / attempts) if attempts else None
    measured = rate if rate is not None else 0.0
    terms = source.terms_status
    collector = source.collector
    if (
        terms in (TermsStatus.unknown, TermsStatus.restricted, TermsStatus.prohibited)
        or source.effective_status(moment) is not SourceStatus.active
    ):
        tier = SourceReliabilityTier.unrated
    elif (
        terms is TermsStatus.permitted
        and collector in (Collector.api, Collector.rss)
        and measured <= TIER_A_FAILURE_RATE
    ):
        tier = SourceReliabilityTier.tier_a
    elif (
        terms in (TermsStatus.reviewed, TermsStatus.permitted)
        and collector in (Collector.api, Collector.rss, Collector.http)
        and measured <= TIER_B_FAILURE_RATE
    ):
        tier = SourceReliabilityTier.tier_b
    elif (
        terms in (TermsStatus.reviewed, TermsStatus.permitted)
        and measured <= TIER_C_FAILURE_RATE
    ):
        tier = SourceReliabilityTier.tier_c
    else:
        tier = SourceReliabilityTier.tier_d

    basis = [
        f"terms_status={terms.value}",
        f"collector={collector.value}",
        f"request_cap={source.rate_limit.max_requests_per_run}",
    ]
    if rate is not None:
        basis.append(f"failure_rate={rate:.4f}")
    return SourceReliability(
        tier=tier,
        score=reliability_weight(tier),
        basis=tuple(basis),
        observed_failure_rate=rate,
        observed_attempts=attempts,
    )


class HealthStatus(str, Enum):
    """How a source is doing, as the collection layer observed it."""

    unknown = "unknown"
    healthy = "healthy"
    degraded = "degraded"
    failing = "failing"
    quarantined = "quarantined"


def health_status(
    source: SourceConfig,
    health: SourceHealth | None = None,
    *,
    consecutive_failure_limit: int = DEFAULT_CONSECUTIVE_FAILURE_LIMIT,
    today: date | None = None,
) -> HealthStatus:
    """The health of a source, from the attempts the collection layer recorded.

    A source that is not in rotation is quarantined whatever its history
    says, because it is not being collected and so it is not being trusted
    either. A source whose review expired reads as not in rotation, so a
    stale source is quarantined too. The rest of the status follows the
    observed failure rate.
    """
    moment = today if today is not None else utcnow().date()
    if source.effective_status(moment) is not SourceStatus.active:
        return HealthStatus.quarantined
    attempts = health.attempts if health is not None else 0
    if attempts <= 0:
        return HealthStatus.unknown
    failures = sum(health.failures.values())
    consecutive = health.consecutive_failures if health is not None else 0
    if consecutive >= consecutive_failure_limit:
        return HealthStatus.quarantined
    rate = failures / attempts
    if rate <= HEALTHY_FAILURE_RATE:
        return HealthStatus.healthy
    if rate <= DEGRADED_FAILURE_RATE:
        return HealthStatus.degraded
    if rate <= FAILING_FAILURE_RATE:
        return HealthStatus.failing
    return HealthStatus.quarantined

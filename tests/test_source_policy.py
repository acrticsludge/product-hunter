"""The policy layer: the registry edits, the reliability tier, the health.

The tests build their own registry, because the rules are about a source
that is broken in one particular way, and the shipped file is not broken.
"""

from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError

from product_hunter.collection.state import SourceHealth
from product_hunter.opportunity.signals import SourceReliability
from product_hunter.safety import UrlBlocked, admit_url
from product_hunter.source_policy import (
    HealthStatus,
    PolicyRefused,
    derive_reliability,
    health_status,
    resume_source,
    retire_source,
    revalidate_source,
    suspend_source,
)
from product_hunter.sources import (
    AllowedUse,
    Collector,
    RateLimit,
    RobotsPolicy,
    SourceConfig,
    SourceRegistry,
    SourceStatus,
    TermsStatus,
)

TODAY = date(2026, 10, 22)
FUTURE = date(2099, 4, 20)
USES = {AllowedUse.retrieval, AllowedUse.storage}


def _rate(**overrides) -> RateLimit:
    data = {
        "min_interval_seconds": 1.0,
        "max_requests_per_run": 100,
        "max_retries": 2,
        "backoff_base_seconds": 1.0,
        "backoff_max_seconds": 30.0,
    }
    data.update(overrides)
    return RateLimit(**data)


def _source(**overrides) -> SourceConfig:
    data = {
        "source_id": "hn_algolia",
        "source_type": "community",
        "status": SourceStatus.active,
        "terms_status": TermsStatus.permitted,
        "allowed_use": [
            AllowedUse.discovery,
            AllowedUse.retrieval,
            AllowedUse.analysis,
            AllowedUse.storage,
        ],
        "reviewed_at": date(2026, 10, 20),
        "review_expires_at": FUTURE,
        "review_basis": "reviewed",
        "robots_or_api_notes": "notes",
        "collector": Collector.api,
        "rate_limit": _rate(),
        "domains": ["hn.algolia.com"],
        "schemes": ["https"],
        "robots": RobotsPolicy.skip,
        "enabled": True,
    }
    data.update(overrides)
    if "status" in overrides and "enabled" not in overrides:
        data["enabled"] = overrides["status"] is SourceStatus.active
    return SourceConfig(**data)


def _registry(source: SourceConfig) -> SourceRegistry:
    return SourceRegistry({source.source_id: source})


def test_a_source_with_an_incomplete_policy_is_refused():
    source = _source(review_basis="")
    decision = _registry(source).admission("hn_algolia", USES)
    assert decision.admitted is False
    assert decision.reason_code == "incomplete_policy"
    assert "review_basis" in decision.gaps
    assert "review_basis" in decision.detail


def test_unknown_terms_are_refused():
    source = _source(terms_status=TermsStatus.unknown, reviewed_at=None)
    decision = _registry(source).admission("hn_algolia", USES)
    assert decision.admitted is False
    assert decision.reason_code == "terms_not_permitted"


def test_a_disabled_source_is_refused():
    source = _source(status=SourceStatus.pending_review)
    decision = _registry(source).admission("hn_algolia", USES)
    assert decision.admitted is False
    assert decision.reason_code == "source_disabled"
    assert "pending_review" in decision.detail


def test_a_domain_outside_the_allowlist_is_refused():
    source = _source()
    with pytest.raises(UrlBlocked):
        admit_url(
            "https://example.com/post",
            source,
            resolver=lambda host: ["93.184.216.34"],
        )


def test_the_rate_limit_policy_is_required_and_bounded():
    with pytest.raises(ValidationError):
        _source(rate_limit=None)
    with pytest.raises(ValidationError):
        _rate(max_requests_per_run=0)
    with pytest.raises(ValidationError):
        _rate(max_requests_per_community=0)


def test_a_retired_source_is_refused():
    source = retire_source(
        _source(), reason="the terms changed", today=TODAY
    )
    assert source.status is SourceStatus.retired
    assert source.retired_at == TODAY
    assert source.retire_reason == "the terms changed"
    decision = _registry(source).admission("hn_algolia", USES)
    assert decision.admitted is False
    assert decision.reason_code == "source_disabled"
    assert "retired" in decision.detail


def test_a_retired_source_cannot_be_retired_again():
    source = retire_source(_source(), reason="the terms changed", today=TODAY)
    with pytest.raises(PolicyRefused) as excinfo:
        retire_source(source, reason="again", today=TODAY)
    assert excinfo.value.reason_code == "already_retired"

def test_a_suspended_source_is_refused():
    source = suspend_source(_source(), reason="a failure spike", today=TODAY)
    assert source.status is SourceStatus.suspended
    assert source.suspended_at == TODAY
    assert source.suspend_reason == "a failure spike"
    decision = _registry(source).admission("hn_algolia", USES)
    assert decision.admitted is False
    assert decision.reason_code == "source_disabled"


def test_a_suspended_source_cannot_be_suspended_again():
    source = suspend_source(_source(), reason="a failure spike", today=TODAY)
    with pytest.raises(PolicyRefused) as excinfo:
        suspend_source(source, reason="again", today=TODAY)
    assert excinfo.value.reason_code == "already_suspended"


def test_a_resumed_source_comes_back_only_when_the_terms_permit_it():
    source = suspend_source(_source(), reason="a failure spike", today=TODAY)
    resumed = resume_source(source, today=TODAY)
    assert resumed.status is SourceStatus.active
    assert resumed.suspended_at is None
    assert resumed.suspend_reason == ""
    unknown = resume_source(
        suspend_source(
            _source(terms_status=TermsStatus.unknown, reviewed_at=None),
            reason="a failure spike",
            today=TODAY,
        ),
        today=TODAY,
    )
    assert unknown.status is SourceStatus.pending_review


def test_a_retired_source_cannot_be_resumed():
    source = retire_source(_source(), reason="the terms changed", today=TODAY)
    with pytest.raises(PolicyRefused) as excinfo:
        resume_source(source, today=TODAY)
    assert excinfo.value.reason_code == "retired_source"


def test_an_expired_review_is_refused_until_it_is_renewed():
    source = _source(review_expires_at=date(2020, 1, 1))
    decision = _registry(source).admission("hn_algolia", USES, today=TODAY)
    assert decision.admitted is False
    assert decision.reason_code == "review_expired"
    renewed = revalidate_source(
        source,
        reviewer="operator",
        reviewed_at=TODAY,
        expires_at=FUTURE,
    )
    assert renewed.reviewed_at == TODAY
    assert renewed.last_reviewed_by == "operator"
    assert renewed.review_expires_at == FUTURE
    assert _registry(renewed).admission("hn_algolia", USES, today=TODAY).admitted


def test_a_revalidation_refuses_terms_that_do_not_permit_a_collection():
    source = _source(terms_status=TermsStatus.restricted, reviewed_at=None)
    with pytest.raises(PolicyRefused) as excinfo:
        revalidate_source(
            source,
            reviewer="operator",
            reviewed_at=TODAY,
            expires_at=FUTURE,
        )
    assert excinfo.value.reason_code == "terms_not_permitted"


def test_a_source_edit_does_not_change_the_source_it_was_given():
    source = _source()
    retired = retire_source(source, reason="the terms changed", today=TODAY)
    assert retired.status is SourceStatus.retired
    assert source.status is SourceStatus.active
    assert source.retired_at is None


def test_the_reliability_tier_names_its_basis():
    reliability = derive_reliability(_source())
    assert reliability.tier is SourceReliability.tier_a
    assert reliability.score == 1.0
    assert any("terms_status=permitted" in item for item in reliability.basis)
    assert any("collector=api" in item for item in reliability.basis)
    assert any("request_cap=100" in item for item in reliability.basis)
    assert reliability.observed_failure_rate is None
    assert reliability.observed_attempts == 0


def test_the_reliability_tier_follows_the_terms_and_the_failures():
    health = SourceHealth(
        source_id="hn_algolia",
        attempts=10,
        failures={"http_error": 1},
    )
    reliability = derive_reliability(_source(), health)
    assert reliability.tier is SourceReliability.tier_b
    assert reliability.observed_failure_rate == pytest.approx(0.1)
    assert reliability.observed_attempts == 10
    assert any("failure_rate=0.1000" in item for item in reliability.basis)
    unknown = derive_reliability(
        _source(terms_status=TermsStatus.unknown, reviewed_at=None), health
    )
    assert unknown.tier is SourceReliability.unrated


def test_the_health_status_follows_the_observed_failures():
    source = _source()
    assert health_status(source) is HealthStatus.unknown
    healthy = SourceHealth(source_id="hn_algolia", attempts=10, failures={})
    assert health_status(source, healthy) is HealthStatus.healthy
    degraded = SourceHealth(
        source_id="hn_algolia", attempts=10, failures={"http_error": 2}
    )
    assert health_status(source, degraded) is HealthStatus.degraded
    failing = SourceHealth(
        source_id="hn_algolia", attempts=10, failures={"http_error": 4}
    )
    assert health_status(source, failing) is HealthStatus.failing
    quarantined = SourceHealth(
        source_id="hn_algolia",
        attempts=10,
        failures={"http_error": 6},
        consecutive_failures=5,
    )
    assert health_status(source, quarantined) is HealthStatus.quarantined
    # A failure spike that has not reached the consecutive limit is failing,
    # not quarantined: the limit is what turns a spike into a quarantine.
    spike = SourceHealth(
        source_id="hn_algolia",
        attempts=10,
        failures={"http_error": 4},
        consecutive_failures=5,
    )
    assert health_status(source, spike) is HealthStatus.quarantined
    assert (
        health_status(source, spike, consecutive_failure_limit=6)
        is HealthStatus.failing
    )


def test_a_source_that_is_not_in_rotation_is_quarantined():
    source = suspend_source(_source(), reason="a failure spike", today=TODAY)
    healthy = SourceHealth(source_id="hn_algolia", attempts=10, failures={})
    assert health_status(source, healthy) is HealthStatus.quarantined


def test_a_source_whose_review_expired_is_quarantined():
    source = _source(review_expires_at=date(2020, 1, 1))
    healthy = SourceHealth(source_id="hn_algolia", attempts=10, failures={})
    assert health_status(source, healthy, today=TODAY) is HealthStatus.quarantined

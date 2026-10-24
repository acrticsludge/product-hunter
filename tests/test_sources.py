from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from product_hunter.sources import (
    POLICY_FIELDS,
    AllowedUse,
    Collector,
    RateLimit,
    RobotsPolicy,
    SourceBlocked,
    SourceConfig,
    SourceRegistry,
    SourceStatus,
    SourceType,
    TermsStatus,
    load_registry,
)

USES = {AllowedUse.discovery, AllowedUse.retrieval, AllowedUse.analysis, AllowedUse.storage}


def _rate(**overrides) -> dict:
    data = {
        "min_interval_seconds": 1.0,
        "max_requests_per_run": 10,
        "max_retries": 2,
        "backoff_base_seconds": 1.0,
        "backoff_max_seconds": 30.0,
    }
    data.update(overrides)
    return data


def _source(source_id: str = "hn_algolia", **overrides) -> dict:
    data = {
        "source_id": source_id,
        "source_type": "community",
        "terms_status": "permitted",
        "allowed_use": ["discovery", "retrieval", "analysis", "storage"],
        "reviewed_at": date(2026, 10, 20),
        "review_basis": "reviewed",
        "robots_or_api_notes": "notes",
        "collector": "api",
        "rate_limit": _rate(),
        "domains": ["hn.algolia.com"],
        "schemes": ["https"],
        "robots": "skip",
        "enabled": True,
    }
    data.update(overrides)
    return data


def _write(tmp_path, sources) -> Path:
    path = tmp_path / "sources.yaml"
    path.write_text(yaml.safe_dump({"version": 1, "sources": sources}), encoding="utf-8")
    return path


def _entry(**overrides) -> dict:
    data = _source()
    data.update(overrides)
    if "status" in overrides and "enabled" not in overrides:
        data["enabled"] = overrides["status"] == "active"
    return data


def _config(**overrides) -> SourceConfig:
    data = {
        "source_id": "hn_algolia",
        "terms_status": TermsStatus.permitted,
        "allowed_use": [AllowedUse.discovery, AllowedUse.retrieval, AllowedUse.analysis, AllowedUse.storage],
        "reviewed_at": date(2026, 10, 20),
        "review_basis": "reviewed",
        "robots_or_api_notes": "notes",
        "collector": Collector.api,
        "rate_limit": RateLimit(**_rate()),
        "domains": ["hn.algolia.com"],
        "schemes": ["https"],
        "robots": RobotsPolicy.skip,
        "enabled": True,
    }
    data.update(overrides)
    if "status" in overrides and "enabled" not in overrides:
        data["enabled"] = overrides["status"] is SourceStatus.active
    return SourceConfig(**data)


def test_real_registry_has_exactly_the_reviewed_ids():
    ids = {source.source_id for source in load_registry().list()}
    assert ids == {"hn_algolia", "github_issues", "blog_http", "dev_to"}
    assert "reddit" not in ids


def test_blog_is_disabled_and_unknown():
    blog = load_registry().get("blog_http")
    assert blog.enabled is False
    assert blog.terms_status is TermsStatus.unknown


def test_require_passes_for_enabled_sources():
    registry = load_registry()
    assert registry.require("hn_algolia", USES).source_id == "hn_algolia"
    assert registry.require("github_issues", USES).source_id == "github_issues"


def test_require_unknown_source_fails():
    with pytest.raises(SourceBlocked):
        load_registry().require("reddit", {AllowedUse.retrieval})


def test_require_disabled_source_fails():
    with pytest.raises(SourceBlocked):
        load_registry().require("blog_http", {AllowedUse.retrieval})


def test_require_rejects_unpermitted_terms():
    registry = SourceRegistry(
        {"blocked": _config(source_id="blocked", terms_status=TermsStatus.prohibited)}
    )
    with pytest.raises(SourceBlocked):
        registry.require("blocked", {AllowedUse.retrieval})


def test_require_rejects_missing_use():
    registry = SourceRegistry(
        {"partial": _config(source_id="partial", allowed_use=[AllowedUse.discovery])}
    )
    with pytest.raises(SourceBlocked):
        registry.require("partial", {AllowedUse.retrieval})


def test_require_rejects_source_without_domains():
    registry = SourceRegistry({"nodomain": _config(source_id="nodomain", domains=[])})
    with pytest.raises(SourceBlocked):
        registry.require("nodomain", {AllowedUse.retrieval})


def test_duplicate_ids_fail_validation(tmp_path):
    duplicate = _source("dup")
    with pytest.raises(ValidationError):
        SourceRegistry.from_yaml(_write(tmp_path, [duplicate, duplicate]))


@pytest.mark.parametrize(
    "domain",
    ["203.0.113.5", "::1", "LOCALHOST", "foo.local", "foo.internal", "not a host"],
)
def test_invalid_domains_fail_validation(tmp_path, domain):
    with pytest.raises(ValidationError):
        SourceRegistry.from_yaml(_write(tmp_path, [_source("bad", domains=[domain])]))


def test_enabled_unknown_terms_fail_validation(tmp_path):
    with pytest.raises(ValidationError):
        SourceRegistry.from_yaml(
            _write(tmp_path, [_source("bad", terms_status="unknown", reviewed_at=None)])
        )


def test_none_must_be_alone(tmp_path):
    with pytest.raises(ValidationError):
        SourceRegistry.from_yaml(
            _write(
                tmp_path,
                [
                    _source(
                        "bad",
                        terms_status="unknown",
                        allowed_use=["none", "retrieval"],
                        reviewed_at=None,
                        domains=[],
                        enabled=False,
                    )
                ],
            )
        )


def test_reviewed_requires_reviewed_at(tmp_path):
    with pytest.raises(ValidationError):
        SourceRegistry.from_yaml(_write(tmp_path, [_source("bad", reviewed_at=None)]))


def test_prohibited_cannot_be_enabled(tmp_path):
    with pytest.raises(ValidationError):
        SourceRegistry.from_yaml(
            _write(tmp_path, [_source("bad", terms_status="prohibited")])
        )


def test_non_positive_request_cap_fails_validation(tmp_path):
    with pytest.raises(ValidationError):
        SourceRegistry.from_yaml(
            _write(tmp_path, [_source("bad", rate_limit=_rate(max_requests_per_run=0))])
        )


def test_the_enabled_key_is_derived_from_the_status(tmp_path):
    registry = SourceRegistry.from_yaml(
        _write(
            tmp_path,
            [
                _entry(
                    status="pending_review",
                    terms_status="unknown",
                    reviewed_at=None,
                    allowed_use=["none"],
                    domains=[],
                )
            ],
        )
    )
    source = registry.get("hn_algolia")
    assert source.status is SourceStatus.pending_review
    assert source.enabled is False


def test_a_file_that_contradicts_itself_is_refused(tmp_path):
    entry = _source()
    entry["status"] = "pending_review"
    with pytest.raises(ValidationError):
        SourceRegistry.from_yaml(_write(tmp_path, [entry]))


def test_an_expired_review_reads_as_stale():
    source = _config(review_expires_at=date(2020, 1, 1))
    assert source.effective_status(date(2026, 10, 22)) is SourceStatus.stale
    assert source.effective_status(date(2019, 1, 1)) is SourceStatus.active


def test_the_policy_gaps_name_every_missing_field():
    assert _config().policy_gaps() == []
    gaps = _config(
        terms_status=TermsStatus.unknown,
        reviewed_at=None,
        review_basis="",
        robots_or_api_notes="",
        allowed_use=[AllowedUse.discovery],
        domains=[],
    ).policy_gaps()
    assert set(gaps) == {
        "terms_status",
        "allowed_use",
        "reviewed_at",
        "review_basis",
        "robots_or_api_notes",
        "domains",
    }
    assert set(gaps) <= set(POLICY_FIELDS)


def test_the_admission_decision_carries_the_reason_code():
    registry = SourceRegistry({"hn_algolia": _config()})
    assert registry.admission("hn_algolia", USES).admitted is True
    assert registry.admission("reddit", USES).reason_code == "unknown_source"
    disabled = SourceRegistry(
        {"hn_algolia": _config(status=SourceStatus.pending_review)}
    )
    assert disabled.admission("hn_algolia", USES).reason_code == "source_disabled"
    stale = SourceRegistry(
        {"hn_algolia": _config(review_expires_at=date(2020, 1, 1))}
    )
    assert (
        stale.admission("hn_algolia", USES, today=date(2026, 10, 22)).reason_code
        == "review_expired"
    )
    unknown = SourceRegistry(
        {"hn_algolia": _config(terms_status=TermsStatus.unknown, reviewed_at=None)}
    )
    assert unknown.admission("hn_algolia", USES).reason_code == "terms_not_permitted"
    prohibited = SourceRegistry(
        {"hn_algolia": _config(terms_status=TermsStatus.prohibited)}
    )
    assert prohibited.admission("hn_algolia", USES).reason_code == "terms_not_permitted"
    no_use = SourceRegistry({"hn_algolia": _config(allowed_use=[AllowedUse.none])})
    assert no_use.admission("hn_algolia", USES).reason_code == "use_not_allowed"
    partial = SourceRegistry(
        {"hn_algolia": _config(allowed_use=[AllowedUse.discovery])}
    )
    assert partial.admission("hn_algolia", USES).reason_code == "missing_use"
    no_domains = SourceRegistry({"hn_algolia": _config(domains=[])})
    assert no_domains.admission("hn_algolia", USES).reason_code == "no_domains"
    incomplete = SourceRegistry({"hn_algolia": _config(review_basis="")})
    decision = incomplete.admission("hn_algolia", USES)
    assert decision.reason_code == "incomplete_policy"
    assert "review_basis" in decision.gaps
    over_http = SourceRegistry(
        {"hn_algolia": _config(collector=Collector.http, robots=RobotsPolicy.skip)}
    )
    assert over_http.admission("hn_algolia", USES).reason_code == "robots_policy_missing"


def test_a_retired_source_needs_its_date_and_reason(tmp_path):
    with pytest.raises(ValidationError):
        SourceRegistry.from_yaml(_write(tmp_path, [_entry(status="retired")]))
    registry = SourceRegistry.from_yaml(
        _write(
            tmp_path,
            [
                _entry(
                    status="retired",
                    retired_at=date(2026, 10, 22),
                    retire_reason="the terms changed",
                )
            ],
        )
    )
    assert registry.get("hn_algolia").status is SourceStatus.retired


def test_a_suspended_source_needs_its_date_and_reason(tmp_path):
    with pytest.raises(ValidationError):
        SourceRegistry.from_yaml(_write(tmp_path, [_entry(status="suspended")]))
    registry = SourceRegistry.from_yaml(
        _write(
            tmp_path,
            [
                _entry(
                    status="suspended",
                    suspended_at=date(2026, 10, 22),
                    suspend_reason="a failure spike",
                )
            ],
        )
    )
    assert registry.get("hn_algolia").status is SourceStatus.suspended


def test_the_shipped_registry_names_its_lifecycle():
    registry = load_registry()
    hn = registry.get("hn_algolia")
    assert hn.status is SourceStatus.active
    assert hn.enabled is True
    assert hn.source_type is SourceType.community
    assert hn.review_expires_at is not None
    assert hn.review_expires_at > hn.reviewed_at
    assert hn.metadata.name
    assert hn.query_capabilities.families
    assert hn.collection.endpoint_path
    github = registry.get("github_issues")
    assert github.status is SourceStatus.active
    assert github.source_type is SourceType.code_host
    blog = registry.get("blog_http")
    assert blog.status is SourceStatus.pending_review
    assert blog.enabled is False

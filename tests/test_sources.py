from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from product_hunter.sources import (
    AllowedUse,
    Collector,
    RateLimit,
    RobotsPolicy,
    SourceBlocked,
    SourceConfig,
    SourceRegistry,
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


def _config(**overrides) -> SourceConfig:
    data = {
        "source_id": "hn_algolia",
        "terms_status": TermsStatus.permitted,
        "allowed_use": [AllowedUse.discovery, AllowedUse.retrieval, AllowedUse.analysis, AllowedUse.storage],
        "reviewed_at": date(2026, 10, 20),
        "collector": Collector.api,
        "rate_limit": RateLimit(**_rate()),
        "domains": ["hn.algolia.com"],
        "schemes": ["https"],
        "robots": RobotsPolicy.skip,
        "enabled": True,
    }
    data.update(overrides)
    return SourceConfig(**data)


def test_real_registry_has_exactly_three_ids():
    ids = {source.source_id for source in load_registry().list()}
    assert ids == {"hn_algolia", "github_issues", "blog_http"}
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

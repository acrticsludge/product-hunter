"""Preflight gate: no run or probe starts on invalid config.

Self contained: each test exercises the preflight checks against real config
files, breaking and restoring them so the repo config is never left dirty.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from product_hunter.config import repo_root
from product_hunter.preflight import (
    PreflightFailure,
    PreflightReport,
    auto_repair_sources,
    load_and_validate_sources,
    preflight_run,
    preflight_with_repair,
    validate_extraction_yaml,
    validate_model_routing_yaml,
)

CONFIG = repo_root() / "config"
SOURCES = CONFIG / "sources.yaml"
EXTRACTION = CONFIG / "extraction.yaml"
ROUTING = CONFIG / "model_routing.yaml"

# A minimal valid source entry: every required field except the ones a
# suspended/retired source must also carry. Only the status-specific fields
# are toggled in the tests below.
_VALID_SOURCE = """
  - source_id: github_issues
    source_type: code_host
    status: {status}
    {extra}
    terms_status: permitted
    allowed_use: [discovery, retrieval, analysis, storage]
    reviewed_at: 2026-10-20
    review_expires_at: 2027-04-20
    last_reviewed_by: operator
    review_basis: "Public GitHub REST API for public repositories."
    robots_or_api_notes: "API access at https://api.github.com."
    collector: api
    rate_limit_policy:
      min_interval_seconds: 0.25
      max_requests_per_run: 50
      max_retries: 2
      backoff_base_seconds: 1.0
      backoff_max_seconds: 30.0
    domains: [api.github.com]
    schemes: [https]
    robots: skip
    enabled: false
    metadata:
      name: GitHub Issues
      homepage: https://github.com
      platform: github
"""

_SOURCES_TEMPLATE = "version: 1\nsources:\n" + _VALID_SOURCE


def _write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8", newline="\n")


def _restore(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8", newline="\n")


@pytest.fixture
def backup_config():
    """Back up the real config files so tests can break and restore them."""
    copies = {
        name: (CONFIG / name).read_text(encoding="utf-8")
        for name in ("sources.yaml", "extraction.yaml", "model_routing.yaml")
    }
    try:
        yield copies
    finally:
        for name, text in copies.items():
            _restore(CONFIG / name, text)


def test_valid_config_passes_preflight(backup_config):
    report = preflight_run()
    assert report.ok is True
    assert all(check.ok for check in report.checks)


def test_preflight_returns_structured_failures(backup_config):
    report = preflight_run()
    assert isinstance(report, PreflightReport)
    names = {check.name for check in report.checks}
    assert names == {"sources", "extraction", "model_routing"}


def test_suspended_source_missing_fields_fails_preflight(backup_config):
    text = _SOURCES_TEMPLATE.format(
        status="suspended", extra=""  # no suspended_at / suspend_reason
    )
    _write(SOURCES, text)
    with pytest.raises(PreflightFailure) as exc:
        load_and_validate_sources()
    message = str(exc.value)
    assert "suspended" in message.lower()
    assert "suspend_reason" in message or "suspended_at" in message


def test_retired_source_missing_fields_fails_preflight(backup_config):
    text = _SOURCES_TEMPLATE.format(status="retired", extra="")
    _write(SOURCES, text)
    with pytest.raises(PreflightFailure) as exc:
        load_and_validate_sources()
    message = str(exc.value)
    assert "retired" in message.lower()
    assert "retire_reason" in message or "retired_at" in message


def test_malformed_extraction_yaml_fails_preflight(backup_config):
    _write(EXTRACTION, "version: 1\n  bad: : indentation:\n")
    with pytest.raises(PreflightFailure) as exc:
        validate_extraction_yaml()
    assert "extraction" in str(exc.value).lower()


def test_malformed_routing_yaml_fails_preflight(backup_config):
    _write(ROUTING, "roles:\n  extraction:\n    - pattern:\n")
    with pytest.raises(PreflightFailure) as exc:
        validate_model_routing_yaml()
    assert "model_routing" in str(exc.value).lower()


def test_preflight_run_fails_on_bad_sources(backup_config):
    text = _SOURCES_TEMPLATE.format(status="suspended", extra="")
    _write(SOURCES, text)
    report = preflight_run()
    assert report.ok is False
    assert report.failures[0].name == "sources"
    assert report.failures[0].error is not None


def test_preflight_failure_is_fixed_text_no_ids_leaked(backup_config):
    text = _SOURCES_TEMPLATE.format(status="suspended", extra="")
    _write(SOURCES, text)
    report = preflight_run()
    text_out = json.dumps(
        {
            "ok": False,
            "error": report.failures[0].error,
            "code": "preflight_failure",
        }
    )
    assert "run_id" not in text_out
    assert "job" not in text_out.lower()
    assert "worker" not in text_out.lower()


def test_suspended_source_with_fields_passes(backup_config):
    extra = "suspended_at: 2026-10-27\n    suspend_reason: \"Disabled for the AI-native probe.\""
    text = _SOURCES_TEMPLATE.format(status="suspended", extra=extra)
    _write(SOURCES, text)
    registry = load_and_validate_sources()
    assert registry.get("github_issues") is not None


def test_retired_source_with_fields_passes(backup_config):
    extra = "retired_at: 2026-10-27\n    retire_reason: \"Replaced by HN source.\""
    text = _SOURCES_TEMPLATE.format(status="retired", extra=extra)
    _write(SOURCES, text)
    registry = load_and_validate_sources()
    assert registry.get("github_issues") is not None


def test_auto_repair_fills_missing_suspended_fields(backup_config):
    text = _SOURCES_TEMPLATE.format(status="suspended", extra="")
    _write(SOURCES, text)
    changes = auto_repair_sources(dry_run=False)
    fields = {change["field"] for change in changes}
    assert "suspended_at" in fields
    assert "suspend_reason" in fields
    assert len(changes) == 2
    # Re-validation must now pass.
    registry = load_and_validate_sources()
    assert registry.get("github_issues") is not None


def test_auto_repair_fills_missing_retired_fields(backup_config):
    text = _SOURCES_TEMPLATE.format(status="retired", extra="")
    _write(SOURCES, text)
    changes = auto_repair_sources(dry_run=False)
    fields = {change["field"] for change in changes}
    assert "retired_at" in fields
    assert "retire_reason" in fields
    registry = load_and_validate_sources()
    assert registry.get("github_issues") is not None


def test_auto_repair_dry_run_does_not_write(backup_config):
    text = _SOURCES_TEMPLATE.format(status="suspended", extra="")
    _write(SOURCES, text)
    before = SOURCES.read_text(encoding="utf-8")
    changes = auto_repair_sources(dry_run=True)
    assert len(changes) == 2
    assert SOURCES.read_text(encoding="utf-8") == before


def test_auto_repair_no_changes_when_valid(backup_config):
    changes = auto_repair_sources(dry_run=False)
    assert changes == []


def test_preflight_with_repair_reports_and_repairs(backup_config):
    text = _SOURCES_TEMPLATE.format(status="suspended", extra="")
    _write(SOURCES, text)
    report, changes = preflight_with_repair()
    assert report.ok is True
    assert len(changes) == 2

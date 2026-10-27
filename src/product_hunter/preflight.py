"""The preflight gate: validate configuration before any run or probe starts.

A preflight check parses every config file the worker trusts and refuses to
start when any of them is invalid, printing the exact file, source, field and
reason. No run is created and no backoff is consumed when preflight fails.

The check is idempotent and safe to repeat: it is meant to run at worker
startup, before ``ph probe start`` and before ``research create``, and again
whenever a config file's mtime changes.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from product_hunter.config import (
    ROUTING_CONFIG_NAME,
    load_config,
    load_routing_config,
    repo_root,
)
from product_hunter.sources import SOURCES_NAME, SourceRegistry

CONFIG_FILES = (SOURCES_NAME, Path("config") / "extraction.yaml", ROUTING_CONFIG_NAME)


class PreflightFailure(Exception):
    """The configuration is invalid; no run may start."""

    code = "preflight_failure"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


@dataclass(frozen=True)
class PreflightCheck:
    """The result of one preflight check."""

    name: str
    file: str
    ok: bool
    error: str | None = None


@dataclass(frozen=True)
class PreflightReport:
    """The result of a full preflight run."""

    ok: bool
    checks: tuple[PreflightCheck, ...] = ()

    @property
    def failures(self) -> tuple[PreflightCheck, ...]:
        return tuple(check for check in self.checks if not check.ok)


def _read_yaml(path: Path) -> Any:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except OSError as exc:
        raise PreflightFailure(
            f"config file {path} cannot be read: {exc}"
        ) from exc
    except yaml.YAMLError as exc:
        raise PreflightFailure(
            f"config file {path} is not valid YAML: {exc}"
        ) from exc
    return raw


def load_and_validate_sources(path: Path = SOURCES_NAME) -> SourceRegistry:
    """Parse ``sources.yaml`` and validate every source's status-specific
    required fields.

    A ``suspended`` source needs ``suspended_at`` plus ``suspend_reason``; a
    ``retired`` source needs ``retired_at`` plus ``retire_reason``. This is the
    check that catches the github_issues misconfiguration before a single job
    dies.
    """
    try:
        registry = SourceRegistry.from_yaml(path)
    except ValidationError as exc:
        raise PreflightFailure(_pydantic_message(path, exc)) from exc
    except ValueError as exc:
        raise PreflightFailure(str(exc)) from exc
    return registry


def _pydantic_message(path: Path, exc: ValidationError) -> str:
    parts: list[str] = []
    for error in exc.errors():
        loc = " -> ".join(str(part) for part in error.get("loc", ()))
        msg = error.get("msg", "")
        parts.append(f"{loc}: {msg}")
    detail = "; ".join(parts) if parts else str(exc)
    return f"config file {path} failed validation: {detail}"


def validate_extraction_yaml(path: Path | None = None) -> Any:
    """Load and validate ``config/extraction.yaml``."""
    resolved = path if path is not None else repo_root() / Path("config") / "extraction.yaml"
    try:
        return load_config(resolved)
    except PreflightFailure:
        raise
    except (ValidationError, ValueError, OSError, yaml.YAMLError) as exc:
        raise PreflightFailure(
            f"config file {resolved} failed validation: {exc}"
        ) from exc


def validate_model_routing_yaml(path: Path | None = None) -> Any:
    """Load and validate ``config/model_routing.yaml``."""
    resolved = path if path is not None else ROUTING_CONFIG_NAME
    if not resolved.is_absolute():
        resolved = repo_root() / resolved
    try:
        return load_routing_config(resolved)
    except PreflightFailure:
        raise
    except (ValidationError, ValueError, OSError, yaml.YAMLError) as exc:
        raise PreflightFailure(
            f"config file {resolved} failed validation: {exc}"
        ) from exc


def preflight_run(*, dry_run: bool = False) -> PreflightReport:
    """Run every preflight check and return the report.

    Raises :class:`PreflightFailure` on the first invalid config so the caller
    (worker startup or a CLI command) refuses to start and prints the exact
    field and reason. ``dry_run`` is accepted for symmetry with the safe
    self-repair guard; this gate itself never writes.
    """
    checks: list[PreflightCheck] = []

    # Sources: the registry file is the only source of truth.
    try:
        load_and_validate_sources()
        checks.append(PreflightCheck("sources", str(SOURCES_NAME), True))
    except PreflightFailure as exc:
        checks.append(PreflightCheck("sources", str(SOURCES_NAME), False, exc.message))
        return PreflightReport(ok=False, checks=tuple(checks))

    # Extraction config.
    try:
        validate_extraction_yaml()
        checks.append(PreflightCheck("extraction", "config/extraction.yaml", True))
    except PreflightFailure as exc:
        checks.append(
            PreflightCheck("extraction", "config/extraction.yaml", False, exc.message)
        )
        return PreflightReport(ok=False, checks=tuple(checks))

    # Model routing config.
    try:
        validate_model_routing_yaml()
        checks.append(PreflightCheck("model_routing", str(ROUTING_CONFIG_NAME), True))
    except PreflightFailure as exc:
        checks.append(
            PreflightCheck(
                "model_routing", str(ROUTING_CONFIG_NAME), False, exc.message
            )
        )
        return PreflightReport(ok=False, checks=tuple(checks))

    return PreflightReport(ok=True, checks=tuple(checks))


def preflight_failure_json(exc: PreflightFailure) -> str:
    """Render a preflight failure as the fixed-text JSON the CLI prints.

    The text names the file and reason and leaks no job or worker ids.
    """
    return json.dumps(
        {
            "ok": False,
            "error": exc.message,
            "code": exc.code,
        }
    )


def _write_yaml(path: Path, data: Any) -> None:
    """Write ``data`` back to ``path`` as YAML without reordering keys."""
    path.write_text(
        yaml.safe_dump(data, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
        newline="\n",
    )


def auto_repair_sources(
    path: Path = SOURCES_NAME, *, dry_run: bool = False
) -> list[dict[str, object]]:
    """Auto-fill missing status-required fields in ``sources.yaml``.

    A ``suspended`` source missing ``suspended_at``/``suspend_reason`` gets a
    timestamp and a generated reason; a ``retired`` source missing
    ``retired_at``/``retire_reason`` is treated the same way. This is the safe
    self-repair that directly prevents the github_issues failure: a misconfigured
    source is repaired instead of killing every discovery job.

    Returns the list of changes made (empty when nothing needed repair).
    Never writes when ``dry_run`` is True, so an operator can preview first.
    The caller records each change as an audit event.
    """
    raw = _read_yaml(path)
    sources = raw.get("sources") or []
    if not isinstance(sources, list):
        return []
    changes: list[dict[str, object]] = []
    dirty = False
    today = datetime.now(timezone.utc).date().isoformat()
    for source in sources:
        if not isinstance(source, dict):
            continue
        status = source.get("status")
        source_id = source.get("source_id")
        if status == "suspended":
            if not source.get("suspended_at"):
                source["suspended_at"] = today
                dirty = True
                changes.append(
                    {
                        "source": source_id,
                        "field": "suspended_at",
                        "value": today,
                        "repair": "auto-filled missing suspended_at",
                    }
                )
            if not source.get("suspend_reason"):
                reason = f"auto-suspended: missing fields repaired at {today}"
                source["suspend_reason"] = reason
                dirty = True
                changes.append(
                    {
                        "source": source_id,
                        "field": "suspend_reason",
                        "value": reason,
                        "repair": "auto-filled missing suspend_reason",
                    }
                )
        elif status == "retired":
            if not source.get("retired_at"):
                source["retired_at"] = today
                dirty = True
                changes.append(
                    {
                        "source": source_id,
                        "field": "retired_at",
                        "value": today,
                        "repair": "auto-filled missing retired_at",
                    }
                )
            if not source.get("retire_reason"):
                reason = f"auto-retired: missing fields repaired at {today}"
                source["retire_reason"] = reason
                dirty = True
                changes.append(
                    {
                        "source": source_id,
                        "field": "retire_reason",
                        "value": reason,
                        "repair": "auto-filled missing retire_reason",
                    }
                )
    if dirty and not dry_run:
        _write_yaml(path, raw)
    return changes


def preflight_with_repair(
    *, dry_run: bool = False
) -> tuple[PreflightReport, list[dict[str, object]]]:
    """Run preflight, then auto-repair sources when it failed on sources only.

    Returns ``(report, changes)``. When preflight fails because a source is
    missing status-required fields, the missing fields are auto-filled (unless
    dry_run) and preflight is re-run to confirm the repair. All other
    preflight failures are reported as-is with no repair attempted.
    """
    report = preflight_run(dry_run=dry_run)
    if report.ok:
        return report, []
    source_failure = next(
        (check for check in report.checks if check.name == "sources"), None
    )
    if source_failure is None:
        return report, []
    changes = auto_repair_sources(dry_run=dry_run)
    if not changes:
        return report, []
    if dry_run:
        return report, changes
    return preflight_run(), changes


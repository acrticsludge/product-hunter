from __future__ import annotations

import ipaddress
import re
from datetime import date
from enum import Enum
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from product_hunter.config import repo_root

SOURCES_NAME = Path("config") / "sources.yaml"

# A hostname label is 1-63 chars, alphanumeric with interior hyphens.
_LABEL = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
_HOSTNAME_RE = re.compile(rf"{_LABEL}(?:\.{_LABEL})*\Z")
_RESERVED_SUFFIXES = (".local", ".internal")


class TermsStatus(str, Enum):
    unknown = "unknown"
    reviewed = "reviewed"
    permitted = "permitted"
    restricted = "restricted"
    prohibited = "prohibited"


class AllowedUse(str, Enum):
    discovery = "discovery"
    retrieval = "retrieval"
    analysis = "analysis"
    storage = "storage"
    none = "none"


class Collector(str, Enum):
    api = "api"
    rss = "rss"
    http = "http"
    browser = "browser"
    managed = "managed"


class RobotsPolicy(str, Enum):
    required = "required"
    skip = "skip"


class SourceBlocked(Exception):
    """Raised when a source may not be used for the requested operation."""

    def __init__(self, reason_code: str, detail: str = "") -> None:
        self.reason_code = reason_code
        super().__init__(detail or reason_code)


class RateLimit(BaseModel):
    min_interval_seconds: float = Field(gt=0)
    max_requests_per_run: int = Field(gt=0)
    max_retries: int = Field(ge=0)
    backoff_base_seconds: float = Field(gt=0)
    backoff_max_seconds: float = Field(gt=0)


class SourceConfig(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    source_id: str = Field(min_length=1)
    source_type: str = ""
    terms_status: TermsStatus
    allowed_use: list[AllowedUse] = Field(default_factory=list)
    reviewed_at: date | None = None
    review_basis: str = ""
    robots_or_api_notes: str = ""
    collector: Collector
    rate_limit: RateLimit = Field(alias="rate_limit_policy")
    domains: list[str] = Field(default_factory=list)
    schemes: list[str] = Field(default_factory=lambda: ["https"])
    robots: RobotsPolicy = RobotsPolicy.required
    enabled: bool = True
    timeout_seconds: float = 30.0
    max_redirects: int = 5
    max_bytes: int = 2097152
    user_agent: str = "ProductHunter/0.1 (Phase 1; local operator)"


class SourcesFile(BaseModel):
    version: int = Field(gt=0)
    sources: list[SourceConfig]

    @model_validator(mode="after")
    def _check_invariants(self) -> SourcesFile:
        seen: set[str] = set()
        for source in self.sources:
            if source.source_id in seen:
                raise ValueError(f"duplicate source_id: {source.source_id!r}")
            seen.add(source.source_id)

            uses = set(source.allowed_use)
            if AllowedUse.none in uses and len(uses) > 1:
                raise ValueError(f"{source.source_id!r}: 'none' must be the only allowed use")

            if (
                source.terms_status in (TermsStatus.reviewed, TermsStatus.permitted)
                and source.reviewed_at is None
            ):
                raise ValueError(
                    f"{source.source_id!r}: {source.terms_status.value} requires reviewed_at"
                )

            if source.terms_status is TermsStatus.prohibited and source.enabled:
                raise ValueError(f"{source.source_id!r}: a prohibited source cannot be enabled")

            if source.enabled:
                if source.terms_status not in (TermsStatus.reviewed, TermsStatus.permitted):
                    raise ValueError(
                        f"{source.source_id!r}: enabled source requires reviewed or permitted terms"
                    )
                if AllowedUse.retrieval not in uses or AllowedUse.storage not in uses:
                    raise ValueError(
                        f"{source.source_id!r}: enabled source requires retrieval and storage"
                    )
                if not source.domains:
                    raise ValueError(f"{source.source_id!r}: enabled source requires a domain")

            for domain in source.domains:
                if not _is_valid_domain(domain):
                    raise ValueError(f"{source.source_id!r}: invalid domain {domain!r}")
        return self


def _is_valid_domain(domain: str) -> bool:
    """An exact lowercase hostname: not an IP literal, not localhost, .local, .internal."""
    if not domain or domain != domain.lower() or len(domain) > 253:
        return False
    if not _HOSTNAME_RE.match(domain):
        return False
    try:
        ipaddress.ip_address(domain)
    except ValueError:
        pass
    else:
        return False
    if domain == "localhost" or domain.endswith(_RESERVED_SUFFIXES):
        return False
    return True


class SourceRegistry:
    def __init__(self, sources: dict[str, SourceConfig]) -> None:
        self._sources = dict(sources)

    @classmethod
    def from_yaml(cls, path: str | Path) -> SourceRegistry:
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        parsed = SourcesFile.model_validate(raw)
        return cls({source.source_id: source for source in parsed.sources})

    def get(self, source_id: str) -> SourceConfig:
        try:
            return self._sources[source_id]
        except KeyError:
            raise SourceBlocked("unknown_source", f"unknown source: {source_id!r}") from None

    def require(self, source_id: str, uses: set[AllowedUse]) -> SourceConfig:
        """Return the source, or fail closed if it may not serve the requested uses."""
        source = self.get(source_id)
        if not source.enabled:
            raise SourceBlocked("source_disabled", f"source {source_id!r} is disabled")
        if source.terms_status not in (TermsStatus.reviewed, TermsStatus.permitted):
            raise SourceBlocked(
                "terms_not_permitted",
                f"source {source_id!r} terms_status is {source.terms_status.value!r}",
            )
        if AllowedUse.none in source.allowed_use:
            raise SourceBlocked("use_not_allowed", f"source {source_id!r} allows no use")
        missing = set(uses) - set(source.allowed_use)
        if missing:
            raise SourceBlocked(
                "missing_use",
                f"source {source_id!r} does not allow: {sorted(u.value for u in missing)}",
            )
        if not source.domains:
            raise SourceBlocked("no_domains", f"source {source_id!r} declares no domains")
        return source

    def list(self) -> list[SourceConfig]:
        return list(self._sources.values())


def load_registry(path: str | Path | None = None) -> SourceRegistry:
    resolved = Path(path) if path is not None else repo_root() / SOURCES_NAME
    return SourceRegistry.from_yaml(resolved)

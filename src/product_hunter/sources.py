"""The source registry: the reviewed entries that say what may be collected.

This module owns the shape of a registry entry, the lifecycle of its status,
and the gate that decides whether a source may serve a requested use.

The registry file is the only source of truth. Nothing here writes it, and a
source that was merely discovered never becomes an entry by being found.
"""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

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
    crawler = "crawler"
    browser = "browser"
    managed = "managed"


class RobotsPolicy(str, Enum):
    required = "required"
    skip = "skip"


class SourceType(str, Enum):
    """What kind of place a source is."""

    community = "community"
    forum = "forum"
    code_host = "code_host"
    publication = "publication"
    review_site = "review_site"
    support = "support"
    mailing_list = "mailing_list"
    aggregator = "aggregator"
    other = "other"


class SourceStatus(str, Enum):
    """Where a source sits in its lifecycle."""

    candidate = "candidate"
    pending_review = "pending_review"
    active = "active"
    stale = "stale"
    suspended = "suspended"
    retired = "retired"


# The policy fields an active source has to carry. The collector, the rate
# limit and the robots policy are required fields of an entry, so they are
# present by construction and are never named as a gap.
POLICY_FIELDS: tuple[str, ...] = (
    "terms_status",
    "allowed_use",
    "reviewed_at",
    "review_basis",
    "robots_or_api_notes",
    "collector",
    "rate_limit_policy",
    "domains",
    "robots",
)


class SourceBlocked(Exception):
    """Raised when a source may not be used for the requested operation."""

    def __init__(self, reason_code: str, detail: str = "") -> None:
        self.reason_code = reason_code
        super().__init__(detail or reason_code)


@dataclass(frozen=True)
class AdmissionDecision:
    """The answer to one admission question, and the reason it says no."""

    source_id: str
    admitted: bool
    reason_code: str
    detail: str
    gaps: list[str] = field(default_factory=list)


class RateLimit(BaseModel):
    min_interval_seconds: float = Field(gt=0)
    max_requests_per_run: int = Field(gt=0)
    max_retries: int = Field(ge=0)
    backoff_base_seconds: float = Field(gt=0)
    backoff_max_seconds: float = Field(gt=0)
    # The per-community cap keeps one busy community inside a source from
    # spending the whole allowance of that source.
    max_requests_per_community: int | None = Field(default=None, gt=0)


class SourceMetadata(BaseModel):
    """What a source is, for a report to read. It is never permission."""

    model_config = ConfigDict(extra="forbid")

    name: str = ""
    homepage: str = ""
    platform: str = ""
    language: str = ""
    topics: list[str] = Field(default_factory=list)
    independence_group: str = ""


class QueryCapabilities(BaseModel):
    """What the search of a source can do.

    A source that cannot serve a family says so here, so the frontier does
    not send that family to it.
    """

    model_config = ConfigDict(extra="forbid")

    families: list[str] = Field(default_factory=list)
    full_text: bool = False
    max_query_length: int = Field(default=0, ge=0)
    supports_filters: bool = False
    pagination: str = ""


class SourceCollection(BaseModel):
    """The source-specific collection configuration."""

    model_config = ConfigDict(extra="forbid")

    endpoint_path: str = ""
    query_param: str = ""
    content_type: str = ""
    max_results_per_query: int = Field(default=0, ge=0)
    max_communities_per_run: int = Field(default=0, ge=0)
    max_items_per_community: int = Field(default=0, ge=0)


class SourceConfig(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    source_id: str = Field(min_length=1)
    source_type: SourceType = SourceType.other
    status: SourceStatus = SourceStatus.pending_review
    terms_status: TermsStatus
    allowed_use: list[AllowedUse] = Field(default_factory=list)
    reviewed_at: date | None = None
    review_expires_at: date | None = None
    last_reviewed_by: str = ""
    review_basis: str = ""
    robots_or_api_notes: str = ""
    collector: Collector
    # The rungs below the preferred one that may be tried when it cannot
    # deliver. The list is empty by default, and a source with an empty
    # list has exactly one candidate: a refusal never escalates and a
    # tool failure only escalates when the source says where to.
    fallbacks: list[Collector] = Field(default_factory=list)
    rate_limit: RateLimit = Field(alias="rate_limit_policy")
    domains: list[str] = Field(default_factory=list)
    schemes: list[str] = Field(default_factory=lambda: ["https"])
    robots: RobotsPolicy = RobotsPolicy.required
    metadata: SourceMetadata = Field(default_factory=SourceMetadata)
    query_capabilities: QueryCapabilities = Field(default_factory=QueryCapabilities)
    collection: SourceCollection = Field(default_factory=SourceCollection)
    suspended_at: date | None = None
    suspend_reason: str = ""
    retired_at: date | None = None
    retire_reason: str = ""
    timeout_seconds: float = 30.0
    max_redirects: int = 5
    max_bytes: int = 2097152
    user_agent: str = "ProductHunter/0.1 (Phase 1; local operator)"

    @model_validator(mode="before")
    @classmethod
    def _consume_enabled(cls, data: Any) -> Any:
        """Turn the old ``enabled`` key into a status, and refuse a file that
        says both and disagrees.

        The status is the only switch now. A file that still says ``enabled``
        keeps working, and a file that says ``enabled: true`` next to a
        status that is not ``active`` is refused instead of meaning two
        different things.
        """
        if not isinstance(data, dict) or "enabled" not in data:
            return data
        values = dict(data)
        enabled = bool(values.pop("enabled"))
        status = values.get("status")
        if status is None:
            values["status"] = (
                SourceStatus.active.value
                if enabled
                else SourceStatus.pending_review.value
            )
            return values
        declared = status if isinstance(status, SourceStatus) else SourceStatus(status)
        if enabled is not (declared is SourceStatus.active):
            raise ValueError(
                "enabled and status disagree: enabled is true exactly when the "
                "status is 'active'"
            )
        return values

    @computed_field
    @property
    def enabled(self) -> bool:
        """Whether the source is in rotation. The status decides it."""
        return self.status is SourceStatus.active

    def effective_status(self, today: date) -> SourceStatus:
        """The status the source reads as on the given day.

        An active source whose review has expired reads as stale, because
        nobody renewed it. Every other status reads as itself.
        """
        expired = (
            self.status is SourceStatus.active
            and self.review_expires_at is not None
            and self.review_expires_at < today
        )
        return SourceStatus.stale if expired else self.status

    def policy_gaps(self) -> list[str]:
        """The policy fields this source does not carry.

        The list is empty when the source carries the whole policy that an
        active source needs.
        """
        gaps: list[str] = []
        if self.terms_status not in (TermsStatus.reviewed, TermsStatus.permitted):
            gaps.append("terms_status")
        if (
            AllowedUse.retrieval not in self.allowed_use
            or AllowedUse.storage not in self.allowed_use
        ):
            gaps.append("allowed_use")
        if self.reviewed_at is None:
            gaps.append("reviewed_at")
        if not self.review_basis:
            gaps.append("review_basis")
        if not self.robots_or_api_notes:
            gaps.append("robots_or_api_notes")
        if not self.domains:
            gaps.append("domains")
        return gaps


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

            # A source that was taken out of rotation says why and when, so a
            # reader never has to guess whether the removal was deliberate.
            if source.status is SourceStatus.retired and (
                source.retired_at is None or not source.retire_reason
            ):
                raise ValueError(
                    f"{source.source_id!r}: a retired source needs retired_at and retire_reason"
                )
            if source.status is SourceStatus.suspended and (
                source.suspended_at is None or not source.suspend_reason
            ):
                raise ValueError(
                    f"{source.source_id!r}: a suspended source needs suspended_at and suspend_reason"
                )

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


def _utc_today() -> date:
    """Today in UTC, the clock the rest of the repository reads."""
    return datetime.now(timezone.utc).date()


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

    def admission(
        self,
        source_id: str,
        uses: set[AllowedUse],
        *,
        today: date | None = None,
    ) -> AdmissionDecision:
        """Decide whether the source may serve the requested uses.

        The checks run in one order, and the first one that refuses is the
        reason: a source that was switched off is not also reported as a
        source with a broken policy.
        """
        moment = today if today is not None else _utc_today()
        source = self._sources.get(source_id)
        if source is None:
            return AdmissionDecision(
                source_id,
                False,
                "unknown_source",
                f"unknown source: {source_id!r}",
            )
        status = source.effective_status(moment)
        if status is SourceStatus.stale:
            return AdmissionDecision(
                source_id,
                False,
                "review_expired",
                f"source {source_id!r} review expired on {source.review_expires_at}",
            )
        if status is not SourceStatus.active:
            return AdmissionDecision(
                source_id,
                False,
                "source_disabled",
                f"source {source_id!r} is {status.value!r}",
            )
        if source.terms_status not in (TermsStatus.reviewed, TermsStatus.permitted):
            return AdmissionDecision(
                source_id,
                False,
                "terms_not_permitted",
                f"source {source_id!r} terms_status is {source.terms_status.value!r}",
            )
        if AllowedUse.none in source.allowed_use:
            return AdmissionDecision(
                source_id,
                False,
                "use_not_allowed",
                f"source {source_id!r} allows no use",
            )
        missing = set(uses) - set(source.allowed_use)
        if missing:
            return AdmissionDecision(
                source_id,
                False,
                "missing_use",
                f"source {source_id!r} does not allow: {sorted(u.value for u in missing)}",
            )
        if not source.domains:
            return AdmissionDecision(
                source_id,
                False,
                "no_domains",
                f"source {source_id!r} declares no domains",
            )
        gaps = source.policy_gaps()
        if gaps:
            return AdmissionDecision(
                source_id,
                False,
                "incomplete_policy",
                f"source {source_id!r} policy is incomplete: {', '.join(gaps)}",
                gaps,
            )
        if source.collector is Collector.http and source.robots is not RobotsPolicy.required:
            return AdmissionDecision(
                source_id,
                False,
                "robots_policy_missing",
                f"source {source_id!r} collects over HTTP without the robots policy",
            )
        return AdmissionDecision(source_id, True, "", "")

    def require(
        self,
        source_id: str,
        uses: set[AllowedUse],
        *,
        today: date | None = None,
    ) -> SourceConfig:
        """Return the source, or fail closed if it may not serve the requested uses."""
        decision = self.admission(source_id, uses, today=today)
        if not decision.admitted:
            raise SourceBlocked(decision.reason_code, decision.detail)
        return self._sources[decision.source_id]

    def list(self) -> list[SourceConfig]:
        return list(self._sources.values())


def load_registry(path: str | Path | None = None) -> SourceRegistry:
    resolved = Path(path) if path is not None else repo_root() / SOURCES_NAME
    return SourceRegistry.from_yaml(resolved)

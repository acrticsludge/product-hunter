"""What every collection adapter shares: the result, the policy, the gate.

The four adapters differ in one thing: how the bytes are taken. Everything
else is the same for all of them, and it lives here so it cannot drift
between them.

The rules are the shared fetch path's rules. The terms gate, the
allowed-use gate, the domain allowlist, the scheme and port rules, the
private-address rule, the robots check, the per-source budget, the byte
ceiling, the raw store and the content hash are applied by
``product_hunter.fetch`` and ``product_hunter.safety``, and this module
reuses them rather than keeping a second copy.

The result carries hashes, paths, status codes and reason codes. It never
carries page text: the body stays in the raw store, addressed by its
hash, and nothing in this layer turns content into a command, a URL or a
tool argument.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

from pydantic import BaseModel, Field

from product_hunter.fetch import (
    Fetcher,
    FetchRecord,
    RequestBudget,
    RobotsBlocked,
    TooLarge,
    Validators,
    fetch_id,
    utcnow,
)
from product_hunter.raw_store import RawStore
from product_hunter.safety import (
    Admission,
    UrlBlocked,
    admit_redirect,
    admit_url,
    resolve_public_ips,
)
from product_hunter.sources import (
    AllowedUse,
    SourceConfig,
    SourceRegistry,
)

if TYPE_CHECKING:
    from product_hunter.collection.orchestrator import CollectionTarget

# The most network requests one capture may make. A page and its assets
# are a few dozen requests; a thousand of them would be a crawl, and a
# crawl belongs to the crawler rung.
MAX_RESOURCES = 50

# The reasons that mean the operator's policy refused the request. A
# refusal ends the ladder: a more expensive tool does not get to overrule
# a decision that was made about the source.
POLICY_REASON_CODES = frozenset(
    {
        "unknown_source",
        "source_disabled",
        "terms_not_permitted",
        "use_not_allowed",
        "missing_use",
        "no_domains",
        "unsafe_url",
        "robots_blocked",
        "too_large",
        "too_many_redirects",
    }
)


class Attempt(BaseModel):
    """One tool the ladder tried, and what it did."""

    tool: str
    outcome: str
    reason_code: str = ""
    detail: str = ""


class CollectionResult(BaseModel):
    """What one adapter produced for one target.

    The field names match ``FetchRecord`` where the two overlap, so a line
    in the fetch log reads the same whichever rung took the capture, and
    the run importer reads it without knowing the rung.
    """

    source_id: str
    requested_url: str
    final_url: str
    status: int | None = None
    outcome: str
    tool: str
    rung: str = ""
    fetch_id: str = ""
    content_type: str = ""
    raw_sha256: str | None = None
    raw_path: str | None = None
    bytes: int = 0
    fetched_at: str = ""
    etag: str | None = None
    last_modified: str | None = None
    not_modified: bool = False
    terms_status: str = ""
    allowed_use: list[str] = Field(default_factory=list)
    review_basis: str = ""
    user_agent: str = ""
    latency_ms: int = 0
    retry_count: int = 0
    reason_code: str = ""
    detail: str = ""
    metadata: dict[str, str] = Field(default_factory=dict)
    attempts: list[Attempt] = Field(default_factory=list)

    @classmethod
    def from_fetch_record(cls, record: FetchRecord, *, tool: str) -> CollectionResult:
        """The result the shared fetch path produced."""
        return cls(
            source_id=record.source_id,
            requested_url=record.requested_url,
            final_url=record.final_url,
            status=record.status,
            outcome="not_modified" if record.not_modified else "fetched",
            tool=tool,
            fetch_id=record.fetch_id,
            content_type=record.content_type,
            raw_sha256=record.raw_sha256,
            raw_path=record.raw_path,
            bytes=record.bytes,
            fetched_at=record.fetched_at,
            etag=record.etag,
            last_modified=record.last_modified,
            not_modified=record.not_modified,
            terms_status=record.terms_status,
            allowed_use=list(record.allowed_use),
            review_basis=record.review_basis,
            user_agent=record.user_agent,
            latency_ms=record.latency_ms,
            retry_count=record.retry_count,
        )

    def fetch_record(self) -> FetchRecord:
        """The capture in the shape the store already knows."""
        return FetchRecord(
            fetch_id=self.fetch_id
            or fetch_id(self.source_id, self.final_url, self.raw_sha256),
            source_id=self.source_id,
            requested_url=self.requested_url,
            final_url=self.final_url,
            status=self.status if self.status is not None else 0,
            content_type=self.content_type,
            raw_sha256=self.raw_sha256,
            raw_path=self.raw_path,
            bytes=self.bytes,
            fetched_at=self.fetched_at,
            etag=self.etag,
            last_modified=self.last_modified,
            not_modified=self.not_modified,
            terms_status=self.terms_status,
            allowed_use=list(self.allowed_use),
            review_basis=self.review_basis,
            user_agent=self.user_agent,
            latency_ms=self.latency_ms,
            retry_count=self.retry_count,
        )


@dataclass(frozen=True)
class CollectPolicy:
    """The limits a tool must honour while it takes a page.

    The byte ceiling and the resource ceiling are here so a tool cannot
    be handed a page and no limit. ``allow_downloads`` is false because
    the spec says downloads stay off unless a collector contract asks for
    them, and no collector contract does.
    """

    max_bytes: int
    max_resources: int
    timeout_seconds: float
    max_redirects: int
    user_agent: str
    allow_downloads: bool = False


@dataclass(frozen=True)
class CollectRequest:
    """Everything a tool is allowed to do for one target."""

    url: str
    source: SourceConfig
    policy: CollectPolicy
    gate: UrlGate


@dataclass(frozen=True)
class Capture:
    """What a tool brought back."""

    content: bytes
    content_type: str
    status: int
    final_url: str
    metadata: dict[str, str] = field(default_factory=dict)


class Driver(Protocol):
    """How one tool takes a page.

    The driver is the only part that knows the dependency. It is handed
    the admitted URL and the gate, and it returns the bytes.
    """

    name: str

    def take(self, request: CollectRequest) -> Capture: ...


class ToolError(Exception):
    """The tool could not take the page."""

    reason_code = "tool_failed"
    retryable = False

    def __init__(self, detail: str = "", *, retry_after: float | None = None) -> None:
        self.detail = detail
        self.retry_after = retry_after
        super().__init__(detail or self.reason_code)


class ToolUnavailable(ToolError):
    """The tool is not installed, or it has no credential."""

    reason_code = "collector_unavailable"


class ToolTimeout(ToolError):
    """The tool ran out of time."""

    reason_code = "timeout"
    retryable = True


class ToolNetworkError(ToolError):
    """The tool could not reach the page."""

    reason_code = "network_error"
    retryable = True


class ToolEmptyResult(ToolError):
    """The tool reached the page and it had nothing in it."""

    reason_code = "empty_body"


class ToolHttpError(ToolError):
    """The page or the service answered with an error status."""

    reason_code = "http_error"
    retryable = True

    def __init__(
        self,
        status: int,
        detail: str = "",
        *,
        retry_after: float | None = None,
    ) -> None:
        self.status = status
        super().__init__(detail or f"HTTP {status}", retry_after=retry_after)


class UrlGate:
    """Admits every URL a tool is about to request.

    A browser follows redirects and loads sub-resources on its own, so the
    tool cannot be trusted to stay on the source's hosts. The gate is what
    stops it: every request goes through ``admit`` and every redirect hop
    goes through ``redirect``, and both run the same rules the shared
    fetch path runs.
    """

    def __init__(
        self,
        source: SourceConfig,
        policy: CollectPolicy,
        *,
        resolver=resolve_public_ips,
    ) -> None:
        self._source = source
        self._policy = policy
        self._resolver = resolver
        self._hops = 0
        self._resources = 0

    def admit(self, url: str) -> Admission:
        """The URL may be requested."""
        if self._resources >= self._policy.max_resources:
            raise UrlBlocked(
                f"exceeded {self._policy.max_resources} requests for "
                f"{self._source.source_id!r}"
            )
        self._resources += 1
        return admit_url(url, self._source, resolver=self._resolver)

    def redirect(self, current: str, location: str) -> Admission:
        """The redirect hop may be followed."""
        if self._hops >= self._policy.max_redirects:
            raise UrlBlocked(
                f"exceeded {self._policy.max_redirects} redirects fetching {current!r}"
            )
        self._hops += 1
        if self._resources >= self._policy.max_resources:
            raise UrlBlocked(
                f"exceeded {self._policy.max_resources} requests for "
                f"{self._source.source_id!r}"
            )
        self._resources += 1
        return admit_redirect(current, location, self._source, resolver=self._resolver)

    @property
    def resources(self) -> int:
        """The requests the gate has admitted."""
        return self._resources


class AdapterBase:
    """The policy every tool shares.

    The sequence is the same for all of them: the source is required, the
    robots answer is checked, the URL is admitted, a budget slot is taken,
    the page is taken, the byte ceiling is checked, the bytes are stored
    and the result is described.
    """

    name: str = ""
    # What is missing when the adapter cannot serve its rung. It is shown
    # in the refusal, so the operator is told what to set up.
    unavailable_detail: str = ""

    def __init__(
        self,
        registry: SourceRegistry,
        raw_store: RawStore,
        budget: RequestBudget,
        fetcher: Fetcher,
        *,
        driver: Driver | None = None,
        resolver=resolve_public_ips,
        clock=time.monotonic,
        sleeper=time.sleep,
        now=utcnow,
        max_concurrency: int = 1,
    ) -> None:
        self._registry = registry
        self._store = raw_store
        self._budget = budget
        self._fetcher = fetcher
        self._driver = driver
        self._resolver = resolver
        self._clock = clock
        self._sleeper = sleeper
        self._now = now
        # The semaphore bounds how many calls may be in flight at once. It
        # is taken before the budget slot, so the source's minimum gap is
        # waited out inside the bound and two calls cannot leave together.
        self._slots = threading.BoundedSemaphore(max(1, max_concurrency))

    # --- the interface -----------------------------------------------------

    @property
    def available(self) -> bool:
        """Whether the tool behind this adapter is installed.

        An adapter without a driver cannot take a page, and the ladder
        refuses it by name rather than pretending the rung does not exist.
        """
        return self._driver is not None

    def collect(
        self, target: CollectionTarget, *, validators: Validators | None = None
    ) -> CollectionResult:
        """Take one target and describe what came back."""
        if self._driver is None:
            raise ToolUnavailable(
                self.unavailable_detail or f"the {self.name!r} tool is not installed"
            )
        source = self._require_source(target.source_id)
        if not self._fetcher.robots_allows(source, target.url):
            raise RobotsBlocked(f"robots.txt disallows {target.url}")
        policy = self._policy(source)
        gate = UrlGate(source, policy, resolver=self._resolver)
        admitted = gate.admit(target.url)
        request = CollectRequest(
            url=admitted.url, source=source, policy=policy, gate=gate
        )
        capture, retries, latency_ms = self._take(request)
        return self._describe(target, source, capture, retries, latency_ms)

    # --- the shared steps --------------------------------------------------

    def _require_source(self, source_id: str) -> SourceConfig:
        """The source, or a refusal when it may not serve a retrieval."""
        return self._registry.require(
            source_id, {AllowedUse.retrieval, AllowedUse.storage}
        )

    def _policy(self, source: SourceConfig) -> CollectPolicy:
        return CollectPolicy(
            max_bytes=source.max_bytes,
            max_resources=MAX_RESOURCES,
            timeout_seconds=source.timeout_seconds,
            max_redirects=source.max_redirects,
            user_agent=source.user_agent,
        )

    def _take(
        self, request: CollectRequest
    ) -> tuple[Capture, int, int]:
        """Take the page, with the source's retry ladder around it.

        A retry is a request. It waits the source's minimum gap and it
        counts against the per-source cap, the same way the shared fetch
        path treats it.
        """
        source = request.source
        retries = 0
        started = self._clock()
        while True:
            try:
                with self._slots:
                    self._budget.acquire(source)
                    capture = self._take_once(request)
                break
            except ToolError as exc:
                if not exc.retryable or retries >= source.rate_limit.max_retries:
                    raise
                self._sleeper(self._delay(source, retries, exc))
                retries += 1
        latency_ms = int((self._clock() - started) * 1000)
        return capture, retries, latency_ms

    def _take_once(self, request: CollectRequest) -> Capture:
        assert self._driver is not None  # guarded by collect()
        return self._driver.take(request)

    def _delay(self, source: SourceConfig, retries: int, exc: ToolError) -> float:
        """The wait before the next attempt.

        A service that says when to come back is believed, and the wait
        is still capped by the source's own ceiling.
        """
        wait = exc.retry_after
        if wait is None:
            wait = source.rate_limit.backoff_base_seconds * (2**retries)
        return max(0.0, min(wait, source.rate_limit.backoff_max_seconds))

    def _describe(
        self,
        target: CollectionTarget,
        source: SourceConfig,
        capture: Capture,
        retries: int,
        latency_ms: int,
    ) -> CollectionResult:
        """Store the bytes and describe the capture.

        The body is stored as bytes and addressed by its hash. It is never
        parsed here, so page text cannot become anything but an artifact.
        """
        if not capture.content:
            raise ToolEmptyResult(f"the {self.name!r} tool returned no content")
        if len(capture.content) > source.max_bytes:
            raise TooLarge(
                f"the capture is {len(capture.content)} bytes, over the "
                f"{source.max_bytes} byte limit"
            )
        raw = self._store.put(source.source_id, capture.content, capture.content_type)
        return CollectionResult(
            source_id=source.source_id,
            requested_url=target.url,
            final_url=capture.final_url,
            status=capture.status,
            outcome="fetched",
            tool=self.name,
            fetch_id=fetch_id(source.source_id, capture.final_url, raw.sha256),
            content_type=capture.content_type,
            raw_sha256=raw.sha256,
            raw_path=raw.path,
            bytes=raw.bytes,
            fetched_at=self._now().isoformat(),
            terms_status=source.terms_status.value,
            allowed_use=[use.value for use in source.allowed_use],
            review_basis=source.review_basis,
            user_agent=source.user_agent,
            latency_ms=latency_ms,
            retry_count=retries,
            metadata=dict(capture.metadata),
        )

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, Field

from product_hunter.raw_store import RawStore
from product_hunter.safety import (
    UrlBlocked,
    admit_redirect,
    admit_url,
    parse_robots,
    resolve_public_ips,
)
from product_hunter.sources import (
    AllowedUse,
    RobotsPolicy,
    SourceBlocked,
    SourceRegistry,
)

_REDIRECT_STATUS = {301, 302, 303, 307, 308}
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _content_type(response: httpx.Response) -> str:
    raw = response.headers.get("content-type")
    if not raw:
        return ""
    return raw.split(";", 1)[0].strip().lower()


@dataclass(frozen=True)
class Validators:
    etag: str | None = None
    last_modified: str | None = None


class FetchRecord(BaseModel):
    fetch_id: str
    source_id: str
    requested_url: str
    final_url: str
    status: int
    content_type: str
    content_encoding: str | None = None
    raw_sha256: str | None = None
    raw_path: str | None = None
    bytes: int = 0
    fetched_at: str
    etag: str | None = None
    last_modified: str | None = None
    redirect_chain: list[str] = Field(default_factory=list)
    robots_checked: bool = False
    terms_status: str = ""
    allowed_use: list[str] = Field(default_factory=list)
    review_basis: str = ""
    user_agent: str = ""
    latency_ms: int = 0
    retry_count: int = 0
    not_modified: bool = False


class FetchRejection(BaseModel):
    rejection_id: str
    source_id: str
    url: str
    reason_code: str
    detail: str
    created_at: str
    terms_status: str | None = None
    allowed_use: list[str] | None = None
    review_basis: str | None = None


class JsonlLog:
    """Append one JSON object per line, creating parent directories as needed."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)

    def append(self, model: BaseModel) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as handle:
            handle.write(model.model_dump_json() + "\n")


class FetchError(Exception):
    reason_code = "fetch_failed"

    def __init__(self, detail: str = "") -> None:
        self.detail = detail
        super().__init__(detail or self.reason_code)


class RobotsBlocked(FetchError):
    reason_code = "robots_blocked"


class BudgetExceeded(FetchError):
    reason_code = "budget_exceeded"


class HttpError(FetchError):
    reason_code = "http_error"

    def __init__(self, status: int, detail: str = "") -> None:
        self.status = status
        super().__init__(detail or f"HTTP {status}")


class TooLarge(FetchError):
    reason_code = "too_large"


class TooManyRedirects(FetchError):
    reason_code = "too_many_redirects"


class EmptyBody(FetchError):
    reason_code = "empty_body"


class NetworkError(FetchError):
    reason_code = "network_error"


class Fetcher:
    """Fetch one admitted URL per source, recording provenance and every failure."""

    def __init__(
        self,
        registry: SourceRegistry,
        store: RawStore,
        log: JsonlLog,
        rejection_log: JsonlLog,
        client: httpx.Client,
        *,
        clock=time.monotonic,
        sleeper=time.sleep,
        resolver=resolve_public_ips,
        now=utcnow,
    ) -> None:
        self._registry = registry
        self._store = store
        self._log = log
        self._rejection_log = rejection_log
        self._client = client
        self._clock = clock
        self._sleeper = sleeper
        self._resolver = resolver
        self._now = now
        self._rate: dict[str, dict[str, float]] = {}
        self._robots: dict[str, str | bool] = {}

    def fetch(
        self, source_id: str, url: str, validators: Validators | None = None
    ) -> FetchRecord:
        try:
            return self._fetch(source_id, url, validators)
        except (FetchError, SourceBlocked, UrlBlocked) as exc:
            self._reject(source_id, url, exc)
            raise

    def _fetch(
        self, source_id: str, url: str, validators: Validators | None
    ) -> FetchRecord:
        source = self._registry.require(
            source_id, {AllowedUse.retrieval, AllowedUse.storage}
        )
        admitted = admit_url(url, source, resolver=self._resolver)
        robots_checked = source.robots is RobotsPolicy.required
        self._check_robots(source, admitted.url)

        headers = self._headers(source, validators)
        current = admitted.url
        redirect_chain: list[str] = []
        redirects = 0

        # shortcut: admit_url resolves the host before httpx connects, so a DNS
        # rebind between the two is not caught; pin the resolved IP in the
        # transport in Phase 4/6.
        while True:
            response, latency_ms, retry_count = self._send(source, current, headers)
            if response.status_code not in _REDIRECT_STATUS:
                break
            location = response.headers.get("location")
            if not location:
                raise HttpError(
                    response.status_code, f"HTTP {response.status_code} without Location"
                )
            if redirects >= source.max_redirects:
                raise TooManyRedirects(
                    f"exceeded {source.max_redirects} redirects fetching {url!r}"
                )
            redirects += 1
            next_admission = admit_redirect(
                current, location, source, resolver=self._resolver
            )
            redirect_chain.append(next_admission.url)
            self._check_robots(source, next_admission.url)
            current = next_admission.url

        status = response.status_code
        content_type = _content_type(response)
        etag = response.headers.get("etag") or (
            validators.etag if validators else None
        )
        last_modified = response.headers.get("last-modified") or (
            validators.last_modified if validators else None
        )
        content_encoding = response.headers.get("content-encoding")

        raw_sha256: str | None = None
        raw_path: str | None = None
        num_bytes = 0
        not_modified = False

        if status == 304:
            not_modified = True
        elif status == 200:
            content = response.content
            if not content:
                raise EmptyBody("response body is empty")
            if len(content) > source.max_bytes:
                raise TooLarge(
                    f"response body is {len(content)} bytes, over the "
                    f"{source.max_bytes} byte limit"
                )
            raw = self._store.put(source_id, content, content_type)
            raw_sha256 = raw.sha256
            raw_path = raw.path
            num_bytes = len(content)
        else:
            raise HttpError(status, f"HTTP {status}")

        record = FetchRecord(
            fetch_id=self._fetch_id(source_id, current, raw_sha256, validators),
            source_id=source_id,
            requested_url=url,
            final_url=current,
            status=status,
            content_type=content_type,
            content_encoding=content_encoding,
            raw_sha256=raw_sha256,
            raw_path=raw_path,
            bytes=num_bytes,
            fetched_at=self._now().isoformat(),
            etag=etag,
            last_modified=last_modified,
            redirect_chain=redirect_chain,
            robots_checked=robots_checked,
            terms_status=source.terms_status.value,
            allowed_use=[use.value for use in source.allowed_use],
            review_basis=source.review_basis,
            user_agent=source.user_agent,
            latency_ms=latency_ms,
            retry_count=retry_count,
            not_modified=not_modified,
        )
        self._log.append(record)
        return record

    def _send(
        self, source, url: str, headers: dict[str, str]
    ) -> tuple[httpx.Response, int, int]:
        retries = 0
        while True:
            # Every attempt waits the per-source interval and counts against the
            # per-run cap; retries are requests too.
            self._acquire(source)
            started = self._clock()
            try:
                response = self._client.get(
                    url,
                    headers=headers,
                    timeout=source.timeout_seconds,
                    follow_redirects=False,
                )
            except httpx.TransportError as exc:
                if retries >= source.rate_limit.max_retries:
                    raise NetworkError(f"{type(exc).__name__}: {exc}") from exc
                self._sleeper(self._delay(source, retries, None))
                retries += 1
                continue
            latency_ms = int((self._clock() - started) * 1000)
            if response.status_code not in _RETRYABLE_STATUS:
                return response, latency_ms, retries
            if retries >= source.rate_limit.max_retries:
                raise HttpError(response.status_code, f"HTTP {response.status_code}")
            self._sleeper(self._delay(source, retries, response))
            retries += 1

    def _delay(self, source, retries: int, response: httpx.Response | None) -> float:
        retry_after = self._retry_after(response) if response is not None else None
        if retry_after is None:
            retry_after = source.rate_limit.backoff_base_seconds * (2**retries)
        return min(retry_after, source.rate_limit.backoff_max_seconds)

    @staticmethod
    def _retry_after(response: httpx.Response) -> float | None:
        value = response.headers.get("retry-after")
        if not value:
            return None
        value = value.strip()
        try:
            return max(0.0, float(value))
        except ValueError:
            pass
        try:
            when = parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return None
        if when is None:
            return None
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        return max(0.0, (when - datetime.now(timezone.utc)).total_seconds())

    def _acquire(self, source) -> None:
        state = self._rate.setdefault(source.source_id, {"next_at": 0.0, "count": 0})
        cap = source.rate_limit.max_requests_per_run
        if state["count"] >= cap:
            raise BudgetExceeded(
                f"source {source.source_id!r} exceeded its per-run cap of {cap} requests"
            )
        state["count"] += 1
        now = self._clock()
        wait = state["next_at"] - now
        if wait > 0:
            self._sleeper(wait)
            now = self._clock()
        state["next_at"] = now + source.rate_limit.min_interval_seconds

    def _check_robots(self, source, page_url: str) -> None:
        if source.robots is not RobotsPolicy.required:
            return
        parsed = urlsplit(page_url)
        origin = f"{parsed.scheme}://{parsed.hostname}"
        if origin not in self._robots:
            self._robots[origin] = self._load_robots(source, page_url)
        policy = self._robots[origin]
        if policy is False:
            raise RobotsBlocked(f"robots.txt is unavailable for {origin}")
        allowed = (
            True if policy is True else parse_robots(policy, page_url, source.user_agent)
        )
        if not allowed:
            raise RobotsBlocked(f"robots.txt disallows {page_url}")

    def _load_robots(self, source, page_url: str) -> str | bool:
        parsed = urlsplit(page_url)
        robots_url = f"{parsed.scheme}://{parsed.hostname}/robots.txt"
        # robots.txt is fetched over the network, so it gets the same URL
        # admission as any page.
        admitted = admit_url(robots_url, source, resolver=self._resolver)
        self._acquire(source)
        try:
            response = self._client.get(
                admitted.url,
                headers={
                    "User-Agent": source.user_agent,
                    "Accept-Encoding": "identity",
                },
                timeout=source.timeout_seconds,
                follow_redirects=False,
            )
        except httpx.TransportError:
            return False
        if response.status_code == 200:
            return response.text
        if response.status_code == 404:
            return True
        return False

    @staticmethod
    def _headers(source, validators: Validators | None) -> dict[str, str]:
        headers = {"User-Agent": source.user_agent, "Accept-Encoding": "identity"}
        if validators is not None:
            if validators.etag:
                headers["If-None-Match"] = validators.etag
            if validators.last_modified:
                headers["If-Modified-Since"] = validators.last_modified
        return headers

    @staticmethod
    def _fetch_id(
        source_id: str,
        final_url: str,
        raw_sha256: str | None,
        validators: Validators | None,
    ) -> str:
        if raw_sha256:
            basis = f"{source_id}|{final_url}|{raw_sha256}"
        else:
            etag = validators.etag if validators else None
            last_modified = validators.last_modified if validators else None
            basis = f"{source_id}|{final_url}|{etag or ''}|{last_modified or ''}"
        return "fetch_" + hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16]

    def _reject(self, source_id: str, url: str, exc: Exception) -> None:
        # An unknown source has no terms to report, so the provenance stays None.
        try:
            source = self._registry.get(source_id)
        except SourceBlocked:
            source = None
        rejection = FetchRejection(
            rejection_id="rej_"
            + hashlib.sha256(f"{source_id}|{url}".encode("utf-8")).hexdigest()[:16],
            source_id=source_id,
            url=url,
            reason_code=getattr(exc, "reason_code", "fetch_failed"),
            detail=str(exc),
            created_at=self._now().isoformat(),
            terms_status=source.terms_status.value if source else None,
            allowed_use=[use.value for use in source.allowed_use] if source else None,
            review_basis=source.review_basis if source else None,
        )
        self._rejection_log.append(rejection)

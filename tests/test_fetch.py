from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timezone

import httpx
import pytest

from product_hunter.fetch import (
    BudgetExceeded,
    EmptyBody,
    Fetcher,
    FetchRecord,
    HttpError,
    JsonlLog,
    NetworkError,
    RobotsBlocked,
    TooLarge,
    TooManyRedirects,
    Validators,
)
from product_hunter.raw_store import RawStore
from product_hunter.safety import UrlBlocked, admit_url
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

PUBLIC = "93.184.216.34"
FROZEN = datetime(2026, 10, 20, 12, 0, 0, tzinfo=timezone.utc)


def make_source(**overrides) -> SourceConfig:
    data = {
        "source_id": "hn_algolia",
        "source_type": "community",
        "terms_status": TermsStatus.permitted,
        "allowed_use": [
            AllowedUse.discovery,
            AllowedUse.retrieval,
            AllowedUse.analysis,
            AllowedUse.storage,
        ],
        "reviewed_at": date(2026, 10, 20),
        "review_basis": "reviewed basis",
        "robots_or_api_notes": "notes",
        "collector": Collector.api,
        "rate_limit": RateLimit(
            min_interval_seconds=1.0,
            max_requests_per_run=100,
            max_retries=2,
            backoff_base_seconds=1.0,
            backoff_max_seconds=30.0,
        ),
        "domains": ["hn.algolia.com", "news.ycombinator.com"],
        "schemes": ["https"],
        "robots": RobotsPolicy.skip,
        "enabled": True,
        "max_redirects": 5,
        "max_bytes": 1_000_000,
    }
    data.update(overrides)
    return SourceConfig(**data)


def make_rate(**overrides) -> RateLimit:
    data = {
        "min_interval_seconds": 1.0,
        "max_requests_per_run": 100,
        "max_retries": 2,
        "backoff_base_seconds": 1.0,
        "backoff_max_seconds": 30.0,
    }
    data.update(overrides)
    return RateLimit(**data)


class FakeClock:
    """A monotonic clock that jumps by ``step`` on every read, so interval waits vanish."""

    def __init__(self, start: float = 0.0, step: float = 1000.0) -> None:
        self._t = start
        self._step = step

    def __call__(self) -> float:
        value = self._t
        self._t += self._step
        return value


class FakeSleeper:
    def __init__(self) -> None:
        self.calls: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


class RecordingClient:
    """Wraps an httpx client and records the url and kwargs of every ``get``."""

    def __init__(self, inner: httpx.Client) -> None:
        self._inner = inner
        self.calls: list[tuple[str, dict]] = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self._inner.get(url, **kwargs)


class Harness:
    def __init__(self, tmp_path, source, handler, clock=None, client=None, resolver=None) -> None:
        self.tmp_path = tmp_path
        self.source = source
        self.registry = SourceRegistry({source.source_id: source})
        self.store = RawStore(tmp_path)
        self.log_path = tmp_path / "fetches.jsonl"
        self.rejection_path = tmp_path / "rejections.jsonl"
        self.sleeper = FakeSleeper()
        self.client = (
            client
            if client is not None
            else httpx.Client(
                transport=httpx.MockTransport(handler), follow_redirects=False
            )
        )
        self.fetcher = Fetcher(
            self.registry,
            self.store,
            JsonlLog(self.log_path),
            JsonlLog(self.rejection_path),
            self.client,
            clock=clock or FakeClock(),
            sleeper=self.sleeper,
            resolver=resolver if resolver is not None else (lambda host: [PUBLIC]),
            now=lambda: FROZEN,
        )

    def records(self) -> list[dict]:
        if not self.log_path.exists():
            return []
        return [json.loads(line) for line in self.log_path.read_text("utf-8").splitlines()]

    def rejections(self) -> list[dict]:
        if not self.rejection_path.exists():
            return []
        return [
            json.loads(line) for line in self.rejection_path.read_text("utf-8").splitlines()
        ]


def body_200(content: bytes, content_type: str = "application/json") -> httpx.Response:
    return httpx.Response(200, content=content, headers={"content-type": content_type})


def test_200_records_provenance_and_content_addressed_path(tmp_path):
    payload = {"hits": [{"objectID": "1", "comment_text": "spreadsheet pain"}]}
    body = json.dumps(payload).encode("utf-8")
    sha = hashlib.sha256(body).hexdigest()
    seen: dict[str, dict] = {}

    def handler(request):
        seen["headers"] = dict(request.headers)
        return body_200(body, "application/json; charset=utf-8")

    harness = Harness(tmp_path, make_source(), handler)
    url = "https://hn.algolia.com/api/v1/search?query=spreadsheet"
    record = harness.fetcher.fetch("hn_algolia", url)

    assert isinstance(record, FetchRecord)
    assert record.status == 200
    assert record.source_id == "hn_algolia"
    assert record.requested_url == url
    assert record.final_url == url
    assert record.content_type == "application/json"
    assert record.raw_sha256 == sha
    assert record.bytes == len(body)
    assert record.raw_path == f"data/raw/hn_algolia/{sha}.json"
    assert (tmp_path / record.raw_path).read_bytes() == body
    assert record.terms_status == "permitted"
    assert record.allowed_use == ["discovery", "retrieval", "analysis", "storage"]
    assert record.review_basis == "reviewed basis"
    assert record.user_agent == harness.source.user_agent
    assert record.not_modified is False
    assert record.retry_count == 0
    assert record.robots_checked is False
    assert record.redirect_chain == []
    assert record.fetched_at == FROZEN.isoformat()
    assert seen["headers"]["accept-encoding"] == "identity"
    assert seen["headers"]["user-agent"] == harness.source.user_agent
    assert len(harness.records()) == 1
    assert harness.rejections() == []


def test_304_records_not_modified_without_raw_write(tmp_path):
    def handler(request):
        assert request.headers["if-none-match"] == '"abc"'
        return httpx.Response(304, headers={"etag": '"abc"'})

    harness = Harness(tmp_path, make_source(), handler)
    record = harness.fetcher.fetch(
        "hn_algolia", "https://hn.algolia.com/api/v1/search", validators=Validators(etag='"abc"')
    )

    assert record.status == 304
    assert record.not_modified is True
    assert record.raw_sha256 is None
    assert record.raw_path is None
    assert record.bytes == 0
    assert record.etag == '"abc"'
    assert not (tmp_path / "data" / "raw" / "hn_algolia").exists()
    assert len(harness.records()) == 1


def test_429_honors_retry_after_then_succeeds(tmp_path):
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"retry-after": "5"})
        return body_200(b"ok", "text/plain")

    harness = Harness(tmp_path, make_source(), handler)
    record = harness.fetcher.fetch("hn_algolia", "https://hn.algolia.com/x")

    assert record.status == 200
    assert record.retry_count == 1
    assert calls["n"] == 2
    assert harness.sleeper.calls == [5.0]


def test_500_exhaustion_raises_and_records_rejection(tmp_path):
    def handler(request):
        return httpx.Response(500)

    harness = Harness(tmp_path, make_source(), handler)
    with pytest.raises(HttpError) as excinfo:
        harness.fetcher.fetch("hn_algolia", "https://hn.algolia.com/x")

    assert excinfo.value.status == 500
    assert excinfo.value.reason_code == "http_error"
    assert harness.sleeper.calls == [1.0, 2.0]
    rejections = harness.rejections()
    assert len(rejections) == 1
    assert rejections[0]["reason_code"] == "http_error"
    assert rejections[0]["source_id"] == "hn_algolia"
    assert harness.records() == []


def test_404_does_not_retry(tmp_path):
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(404)

    harness = Harness(tmp_path, make_source(), handler)
    with pytest.raises(HttpError) as excinfo:
        harness.fetcher.fetch("hn_algolia", "https://hn.algolia.com/missing")

    assert excinfo.value.status == 404
    assert calls["n"] == 1
    assert harness.sleeper.calls == []
    assert harness.rejections()[0]["reason_code"] == "http_error"


def test_transport_error_retries_then_raises_network_error(tmp_path):
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        raise httpx.ConnectError("boom", request=request)

    harness = Harness(tmp_path, make_source(rate_limit=make_rate(max_retries=1)), handler)
    with pytest.raises(NetworkError):
        harness.fetcher.fetch("hn_algolia", "https://hn.algolia.com/x")

    assert calls["n"] == 2
    assert harness.sleeper.calls == [1.0]
    assert harness.rejections()[0]["reason_code"] == "network_error"


def test_same_host_redirect_passes(tmp_path):
    def handler(request):
        if request.url.path == "/a":
            return httpx.Response(302, headers={"location": "/b"})
        return body_200(b"done", "text/plain")

    harness = Harness(tmp_path, make_source(), handler)
    record = harness.fetcher.fetch("hn_algolia", "https://hn.algolia.com/a")

    assert record.status == 200
    assert record.final_url == "https://hn.algolia.com/b"
    assert record.redirect_chain == ["https://hn.algolia.com/b"]
    assert record.raw_sha256 == hashlib.sha256(b"done").hexdigest()


def test_off_allowlist_redirect_blocks(tmp_path):
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(302, headers={"location": "https://evil.example.com/b"})

    harness = Harness(tmp_path, make_source(), handler)
    with pytest.raises(UrlBlocked):
        harness.fetcher.fetch("hn_algolia", "https://hn.algolia.com/a")

    assert calls["n"] == 1
    assert harness.rejections()[0]["reason_code"] == "unsafe_url"


def test_https_to_http_redirect_blocks(tmp_path):
    def handler(request):
        return httpx.Response(302, headers={"location": "http://hn.algolia.com/b"})

    harness = Harness(tmp_path, make_source(schemes=["http", "https"]), handler)
    with pytest.raises(UrlBlocked):
        harness.fetcher.fetch("hn_algolia", "https://hn.algolia.com/a")

    assert harness.rejections()[0]["reason_code"] == "unsafe_url"


def test_redirect_loop_blocks(tmp_path):
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(302, headers={"location": "/loop"})

    harness = Harness(tmp_path, make_source(max_redirects=2), handler)
    with pytest.raises(TooManyRedirects):
        harness.fetcher.fetch("hn_algolia", "https://hn.algolia.com/loop")

    assert calls["n"] == 3
    assert harness.rejections()[0]["reason_code"] == "too_many_redirects"


def test_robots_deny_blocks_before_page_request(tmp_path):
    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(200, content=b"User-agent: *\nDisallow: /\n")
        raise AssertionError(f"page requested after robots deny: {request.url}")

    harness = Harness(tmp_path, make_source(robots=RobotsPolicy.required), handler)
    with pytest.raises(RobotsBlocked):
        harness.fetcher.fetch("hn_algolia", "https://hn.algolia.com/private")

    assert harness.rejections()[0]["reason_code"] == "robots_blocked"


def test_robots_404_allows_page(tmp_path):
    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return body_200(b"body", "text/plain")

    harness = Harness(tmp_path, make_source(robots=RobotsPolicy.required), handler)
    record = harness.fetcher.fetch("hn_algolia", "https://hn.algolia.com/page")

    assert record.status == 200
    assert record.robots_checked is True


def test_robots_cached_per_origin_still_checks_each_page(tmp_path):
    robots_calls = {"n": 0}

    def handler(request):
        if request.url.path == "/robots.txt":
            robots_calls["n"] += 1
            return httpx.Response(200, content=b"User-agent: *\nDisallow: /private\n")
        return body_200(b"ok", "text/plain")

    harness = Harness(tmp_path, make_source(robots=RobotsPolicy.required), handler)
    assert harness.fetcher.fetch("hn_algolia", "https://hn.algolia.com/public").status == 200
    with pytest.raises(RobotsBlocked):
        harness.fetcher.fetch("hn_algolia", "https://hn.algolia.com/private/x")

    assert robots_calls["n"] == 1


def test_requests_never_follow_redirects_and_robots_url_is_admitted(tmp_path):
    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(200, content=b"User-agent: *\nAllow: /\n")
        return body_200(b"page", "text/plain")

    # The injected client would follow redirects on its own; the fetcher has to
    # override that on every request it makes, including robots.txt.
    inner = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)
    recorder = RecordingClient(inner)
    resolved: list[str] = []

    def resolver(host):
        resolved.append(host)
        return [PUBLIC]

    harness = Harness(
        tmp_path,
        make_source(robots=RobotsPolicy.required),
        handler,
        client=recorder,
        resolver=resolver,
    )

    harness.fetcher.fetch("hn_algolia", "https://hn.algolia.com/page")

    assert len(recorder.calls) == 2
    robots_call = next(c for c in recorder.calls if c[0].endswith("/robots.txt"))
    page_call = next(c for c in recorder.calls if c[0] == "https://hn.algolia.com/page")

    assert robots_call[0] == "https://hn.algolia.com/robots.txt"
    assert robots_call[1]["follow_redirects"] is False
    assert page_call[1]["follow_redirects"] is False
    # The robots URL went through the same URL admission as the page, so it
    # was resolved and allowlisted in its own right.
    assert resolved == ["hn.algolia.com", "hn.algolia.com"]


def test_rate_interval_sleeps_between_requests(tmp_path):
    def handler(request):
        return body_200(b"x", "text/plain")

    harness = Harness(tmp_path, make_source(), handler, clock=FakeClock(start=0.0, step=0.0))
    harness.fetcher.fetch("hn_algolia", "https://hn.algolia.com/a")
    harness.fetcher.fetch("hn_algolia", "https://hn.algolia.com/b")

    assert harness.sleeper.calls == [1.0]


def test_retry_waits_both_the_interval_and_the_backoff(tmp_path):
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(500)
        return body_200(b"ok", "text/plain")

    harness = Harness(
        tmp_path,
        make_source(
            rate_limit=make_rate(min_interval_seconds=0.5, backoff_base_seconds=1.0)
        ),
        handler,
        clock=FakeClock(start=0.0, step=0.0),
    )
    record = harness.fetcher.fetch("hn_algolia", "https://hn.algolia.com/x")

    assert record.status == 200
    assert record.retry_count == 1
    assert calls["n"] == 2
    # Two waits, not one: the backoff before the retry and the per-source
    # minimum interval before the retry attempt goes out.
    assert len(harness.sleeper.calls) == 2
    assert 1.0 in harness.sleeper.calls
    assert 0.5 in harness.sleeper.calls


def test_per_run_cap_raises_budget_exceeded(tmp_path):
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return body_200(b"x", "text/plain")

    harness = Harness(
        tmp_path, make_source(rate_limit=make_rate(max_requests_per_run=1)), handler
    )
    harness.fetcher.fetch("hn_algolia", "https://hn.algolia.com/a")
    with pytest.raises(BudgetExceeded):
        harness.fetcher.fetch("hn_algolia", "https://hn.algolia.com/b")

    assert calls["n"] == 1
    assert harness.rejections()[0]["reason_code"] == "budget_exceeded"


def test_disabled_source_raises_and_records_rejection(tmp_path):
    def handler(request):
        raise AssertionError("no request should be made for a disabled source")

    harness = Harness(tmp_path, make_source(enabled=False), handler)
    with pytest.raises(SourceBlocked):
        harness.fetcher.fetch("hn_algolia", "https://hn.algolia.com/a")

    rejections = harness.rejections()
    assert len(rejections) == 1
    assert rejections[0]["reason_code"] == "source_disabled"
    assert harness.records() == []


def test_disabled_known_source_rejection_carries_provenance(tmp_path):
    def handler(request):
        raise AssertionError("no request should be made for a disabled source")

    source = load_registry().get("blog_http")
    harness = Harness(tmp_path, source, handler)
    with pytest.raises(SourceBlocked):
        harness.fetcher.fetch("blog_http", "https://example.com/a")

    rejection = harness.rejections()[0]
    assert rejection["reason_code"] == "source_disabled"
    assert rejection["terms_status"] == "unknown"
    assert rejection["allowed_use"] == ["none"]
    assert rejection["review_basis"] == source.review_basis
    assert rejection["review_basis"]


def test_unknown_source_rejection_has_no_provenance(tmp_path):
    def handler(request):
        raise AssertionError("no request should be made for an unknown source")

    harness = Harness(tmp_path, make_source(), handler)
    with pytest.raises(SourceBlocked):
        harness.fetcher.fetch("missing_source", "https://hn.algolia.com/a")

    rejection = harness.rejections()[0]
    assert rejection["reason_code"] == "unknown_source"
    assert rejection["terms_status"] is None
    assert rejection["allowed_use"] is None
    assert rejection["review_basis"] is None


def test_oversized_body_blocks(tmp_path):
    def handler(request):
        return body_200(b"x" * 100, "text/plain")

    harness = Harness(tmp_path, make_source(max_bytes=10), handler)
    with pytest.raises(TooLarge):
        harness.fetcher.fetch("hn_algolia", "https://hn.algolia.com/big")

    assert harness.rejections()[0]["reason_code"] == "too_large"
    assert not (tmp_path / "data" / "raw" / "hn_algolia").exists()


def test_empty_body_blocks(tmp_path):
    def handler(request):
        return body_200(b"", "text/plain")

    harness = Harness(tmp_path, make_source(), handler)
    with pytest.raises(EmptyBody):
        harness.fetcher.fetch("hn_algolia", "https://hn.algolia.com/empty")

    assert harness.rejections()[0]["reason_code"] == "empty_body"


def test_identical_bodies_dedupe_to_one_file(tmp_path):
    body = b'{"same": true}'

    def handler(request):
        return body_200(body)

    harness = Harness(tmp_path, make_source(), handler)
    first = harness.fetcher.fetch("hn_algolia", "https://hn.algolia.com/a")
    second = harness.fetcher.fetch("hn_algolia", "https://hn.algolia.com/b")

    assert first.raw_path == second.raw_path
    assert first.raw_sha256 == second.raw_sha256
    assert first.fetch_id != second.fetch_id
    files = list((tmp_path / "data" / "raw" / "hn_algolia").iterdir())
    assert len(files) == 1


def test_fetch_id_is_deterministic(tmp_path):
    def handler(request):
        return body_200(b"stable", "text/plain")

    harness = Harness(tmp_path, make_source(), handler)
    first = harness.fetcher.fetch("hn_algolia", "https://hn.algolia.com/a")
    second = harness.fetcher.fetch("hn_algolia", "https://hn.algolia.com/a")

    assert first.fetch_id == second.fetch_id


def test_raw_store_rejects_empty_and_unsafe_source_id(tmp_path):
    store = RawStore(tmp_path)
    with pytest.raises(ValueError):
        store.put("hn_algolia", b"", "text/plain")
    with pytest.raises(ValueError):
        store.put("Bad/Id", b"x", "text/plain")
    with pytest.raises(ValueError):
        store.put("UPPER", b"x", "text/plain")


@pytest.mark.parametrize(
    ("content_type", "extension"),
    [
        ("application/json", ".json"),
        ("text/html; charset=utf-8", ".html"),
        ("text/plain", ".txt"),
        ("application/xml", ".xml"),
        ("application/octet-stream", ".bin"),
    ],
)
def test_raw_store_extension_by_content_type(tmp_path, content_type, extension):
    obj = RawStore(tmp_path).put("src", b"data", content_type)
    assert obj.path.endswith(extension)
    assert obj.path.startswith("data/raw/src/")


def test_scheme_port_pairing_blocks():
    source = make_source(schemes=["http", "https"])
    with pytest.raises(UrlBlocked):
        admit_url("https://hn.algolia.com:80/x", source, resolver=lambda host: [PUBLIC])
    with pytest.raises(UrlBlocked):
        admit_url("http://hn.algolia.com:443/x", source, resolver=lambda host: [PUBLIC])
    admitted = admit_url("https://hn.algolia.com:443/x", source, resolver=lambda host: [PUBLIC])
    assert admitted.url == "https://hn.algolia.com/x"


def test_cli_sources_lists_registry(capsys):
    from product_hunter.cli import sources_command

    assert sources_command() == 0
    payload = json.loads(capsys.readouterr().out)
    ids = {row["source_id"] for row in payload}
    assert ids == {"hn_algolia", "github_issues", "blog_http"}
    row = next(r for r in payload if r["source_id"] == "hn_algolia")
    assert row["terms_status"] == "permitted"
    assert row["enabled"] is True
    assert row["domains"] == ["hn.algolia.com", "news.ycombinator.com"]
    assert set(row["allowed_use"]) == {"discovery", "retrieval", "analysis", "storage"}


def test_cli_fetch_prints_record(tmp_path, monkeypatch, capsys):
    from product_hunter import cli

    body = b'{"ok": true}'

    def handler(request):
        return body_200(body)

    source = make_source()
    registry = SourceRegistry({source.source_id: source})
    client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False)
    monkeypatch.setattr(cli, "repo_root", lambda: tmp_path)

    code = cli.fetch_command(
        "hn_algolia",
        "https://hn.algolia.com/api",
        registry=registry,
        store=RawStore(tmp_path),
        client=client,
    )

    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == 200
    assert payload["source_id"] == "hn_algolia"
    assert payload["raw_sha256"] == hashlib.sha256(body).hexdigest()


def test_cli_fetch_reports_block(tmp_path, monkeypatch, capsys):
    from product_hunter import cli

    source = make_source(enabled=False)
    registry = SourceRegistry({source.source_id: source})
    client = httpx.Client(
        transport=httpx.MockTransport(lambda request: body_200(b"x", "text/plain")),
        follow_redirects=False,
    )
    monkeypatch.setattr(cli, "repo_root", lambda: tmp_path)

    code = cli.fetch_command(
        "hn_algolia",
        "https://hn.algolia.com/api",
        registry=registry,
        store=RawStore(tmp_path),
        client=client,
    )

    assert code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["reason"] == "source_disabled"

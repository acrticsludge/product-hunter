"""The behaviour of the advanced collection ladder.

The house style holds: a fake tool, a fake resolver, a fake clock, and a
temporary store. No live service is contacted and no live success is
claimed. The libraries the real drivers need are not installed in this
environment, so the drivers are exercised against their protocol, and the
ones that are exercised against a library are exercised against a fake
one.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date

import httpx
import pytest

from product_hunter.collection import (
    CollectionOrchestrator,
    CollectionTarget,
    CollectorUnavailable,
    Ladder,
    build_adapters,
)
from product_hunter.collection.adapters.base import (
    Capture,
    CollectionResult,
    CollectRequest,
    ToolEmptyResult,
    ToolHttpError,
    ToolNetworkError,
    ToolTimeout,
)
from product_hunter.db import Store
from product_hunter.fetch import JsonlLog
from product_hunter.raw_store import RawStore
from product_hunter.research.contract import ResearchContract, load_research_contract
from product_hunter.sources import (
    AllowedUse,
    RateLimit,
    RobotsPolicy,
    SourceConfig,
    SourceRegistry,
    TermsStatus,
)
from product_hunter.sources import (
    Collector as SourceCollector,
)

PUBLIC = "93.184.216.34"
URL = "https://hn.algolia.com/api/v1/search"


class FakeClock:
    """A clock that jumps on every read, so the interval waits vanish."""

    def __init__(self) -> None:
        self._t = 0.0
        self._step = 1.0

    def __call__(self) -> float:
        value = self._t
        self._t += self._step
        return value


class FakeSleeper:
    def __init__(self) -> None:
        self.calls: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


class FakeDriver:
    """A tool that answers with what the test tells it to."""

    def __init__(
        self,
        name: str = "fake",
        *,
        content: bytes = b"<html>ok</html>",
        content_type: str = "text/html",
        status: int = 200,
        error: Exception | None = None,
        metadata: dict[str, str] | None = None,
    ) -> None:
        self.name = name
        self.content = content
        self.content_type = content_type
        self.status = status
        self.error = error
        self.metadata = metadata or {}
        self.requests: list[CollectRequest] = []

    def take(self, request: CollectRequest) -> Capture:
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        return Capture(
            content=self.content,
            content_type=self.content_type,
            status=self.status,
            final_url=request.url,
            metadata=dict(self.metadata),
        )


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
        "collector": SourceCollector.api,
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


def tight_contract(limit: int) -> ResearchContract:
    """The shipped contract with the fetch budgets pulled down to ``limit``."""
    base = load_research_contract()
    budgets = base.budgets.model_copy(
        update={
            "per_run": {**base.budgets.per_run, "fetch_requests": limit},
            "per_stage": {
                **base.budgets.per_stage,
                "evidence_collection": {"fetch_requests": limit},
            },
            "per_source": {**base.budgets.per_source, "hn_algolia": limit},
        }
    )
    return base.model_copy(update={"budgets": budgets})


class Harness:
    """A collection orchestrator over a mock transport and a temporary store."""

    def __init__(
        self,
        tmp_path,
        source,
        *,
        tools: dict | None = None,
        handler=None,
        clock=None,
        resolver=None,
        contract=None,
    ) -> None:
        self.tmp_path = tmp_path
        self.source = source
        self.registry = SourceRegistry({source.source_id: source})
        self.store = Store.open(tmp_path / "product_hunter.db")
        self.raw_store = RawStore(tmp_path)
        self.sleeper = FakeSleeper()
        self.calls: list[str] = []
        self.events: list[dict[str, str]] = []

        def recording(request: httpx.Request) -> httpx.Response:
            self.calls.append(str(request.url))
            if handler is None:
                raise AssertionError("no request should be made")
            return handler(request)

        self.client = httpx.Client(
            transport=httpx.MockTransport(recording), follow_redirects=False
        )
        self.orchestrator = CollectionOrchestrator(
            self.registry,
            self.store,
            client=self.client,
            raw_store=self.raw_store,
            log=JsonlLog(self.tmp_path / "fetches.jsonl"),
            rejection_log=JsonlLog(self.tmp_path / "rejections.jsonl"),
            clock=clock or FakeClock(),
            sleeper=self.sleeper,
            resolver=resolver if resolver is not None else (lambda host: [PUBLIC]),
            contract=contract,
            events=self.events.append,
            tools=tools if tools is not None else {},
        )

    def create_run(self, run_id: str = "run-1") -> None:
        self.store.conn.execute(
            "INSERT INTO research_runs (run_id, public_id, objective, "
            "original_request) VALUES (?, ?, '', '')",
            (run_id, run_id),
        )

    def items(self, run_id: str = "run-1"):
        from product_hunter.collection.state import CollectionState

        return CollectionState(self.store.conn).item(
            run_id, self.source.source_id, URL
        )

    def fetch_log(self) -> list[dict]:
        import json

        path = self.tmp_path / "fetches.jsonl"
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text("utf-8").splitlines()]


def target(url: str = URL, source_id: str = "hn_algolia") -> CollectionTarget:
    return CollectionTarget(source_id=source_id, url=url)


def body_200(content: bytes, content_type: str = "application/json") -> httpx.Response:
    return httpx.Response(200, content=content, headers={"content-type": content_type})


# --- the routing -----------------------------------------------------------


def test_the_api_rung_is_served_by_the_http_tool(tmp_path):
    driver = FakeDriver("crawl4ai")

    def handler(request):
        return body_200(b'{"hits": []}')

    harness = Harness(
        tmp_path,
        make_source(collector=SourceCollector.api),
        tools={"crawl4ai": driver},
        handler=handler,
    )
    harness.create_run()
    outcome = harness.orchestrator.collect("run-1", target())

    assert outcome.outcome == "fetched"
    assert outcome.rung == "api"
    # The API rung is a plain HTTP request, so the browser tool was not
    # used and the shared path made the request.
    assert driver.requests == []
    assert harness.calls == [URL]


def test_the_crawler_rung_is_served_by_the_crawler_tool(tmp_path):
    driver = FakeDriver("crawl4ai")

    harness = Harness(
        tmp_path,
        make_source(collector=SourceCollector.crawler),
        tools={"crawl4ai": driver},
    )
    harness.create_run()
    outcome = harness.orchestrator.collect("run-1", target())

    assert outcome.outcome == "fetched"
    assert outcome.rung == "crawler"
    assert len(driver.requests) == 1
    assert driver.requests[0].url == URL


def test_the_browser_rung_is_served_by_the_browser_tool(tmp_path):
    driver = FakeDriver("playwright")

    harness = Harness(
        tmp_path,
        make_source(collector=SourceCollector.browser),
        tools={"playwright": driver},
    )
    harness.create_run()
    outcome = harness.orchestrator.collect("run-1", target())

    assert outcome.outcome == "fetched"
    assert outcome.rung == "browser"
    assert len(driver.requests) == 1


def test_the_managed_rung_is_served_by_the_managed_tool(tmp_path):
    driver = FakeDriver("apify")

    harness = Harness(
        tmp_path,
        make_source(collector=SourceCollector.managed),
        tools={"apify": driver},
    )
    harness.create_run()
    outcome = harness.orchestrator.collect("run-1", target())

    assert outcome.outcome == "fetched"
    assert outcome.rung == "managed"
    assert len(driver.requests) == 1


def test_the_rung_order_is_the_sources_own(tmp_path):
    source = make_source(
        collector=SourceCollector.crawler,
        fallbacks=[SourceCollector.browser, SourceCollector.managed],
    )
    from product_hunter.collection.adapters.routing import rung_order

    assert rung_order(source) == [
        SourceCollector.crawler,
        SourceCollector.browser,
        SourceCollector.managed,
    ]


def test_a_fallback_that_repeats_the_preferred_rung_is_dropped(tmp_path):
    from product_hunter.collection.adapters.routing import rung_order

    source = make_source(
        collector=SourceCollector.crawler,
        fallbacks=[SourceCollector.crawler, SourceCollector.browser],
    )

    assert rung_order(source) == [SourceCollector.crawler, SourceCollector.browser]


def test_a_source_with_no_fallbacks_has_one_candidate(tmp_path):
    from product_hunter.collection.adapters.routing import rung_order

    source = make_source(collector=SourceCollector.browser)

    assert rung_order(source) == [SourceCollector.browser]


# --- the fallback ----------------------------------------------------------


def test_a_tool_failure_escalates_to_the_declared_fallback(tmp_path):
    crawler = FakeDriver("crawl4ai", error=ToolTimeout("the crawler timed out"))
    browser = FakeDriver("playwright", content=b"<html>rendered</html>")

    harness = Harness(
        tmp_path,
        make_source(
            collector=SourceCollector.crawler,
            fallbacks=[SourceCollector.browser],
        ),
        tools={"crawl4ai": crawler, "playwright": browser},
    )
    harness.create_run()
    outcome = harness.orchestrator.collect("run-1", target())

    assert outcome.outcome == "fetched"
    assert outcome.rung == "browser"
    # The cheap tool was tried once and then retried twice, the number of
    # retries the source allows, before the ladder moved on.
    assert len(crawler.requests) == 3
    assert len(browser.requests) == 1


def test_the_result_records_the_tools_that_were_tried(tmp_path):
    crawler = FakeDriver("crawl4ai", error=ToolTimeout("the crawler timed out"))
    browser = FakeDriver("playwright")

    harness = Harness(
        tmp_path,
        make_source(
            collector=SourceCollector.crawler,
            fallbacks=[SourceCollector.browser],
        ),
        tools={"crawl4ai": crawler, "playwright": browser},
    )
    harness.create_run()
    outcome = harness.orchestrator.collect("run-1", target())

    assert outcome.outcome == "fetched"
    item = harness.items()
    assert item is not None
    assert item.rung == "browser"
    assert item.reason_code == ""


def test_a_tool_failure_without_a_fallback_is_recorded_as_a_failure(tmp_path):
    crawler = FakeDriver("crawl4ai", error=ToolTimeout("the crawler timed out"))

    harness = Harness(
        tmp_path,
        make_source(collector=SourceCollector.crawler),
        tools={"crawl4ai": crawler},
    )
    harness.create_run()
    outcome = harness.orchestrator.collect("run-1", target())

    assert outcome.outcome == "failed"
    assert outcome.reason_code == "timeout"
    assert "crawl4ai" in outcome.detail


def test_an_unavailable_tool_is_skipped_and_the_next_candidate_runs(tmp_path):
    browser = FakeDriver("playwright")

    harness = Harness(
        tmp_path,
        make_source(
            collector=SourceCollector.crawler,
            fallbacks=[SourceCollector.browser],
        ),
        tools={"crawl4ai": None, "playwright": browser},
    )
    harness.create_run()
    outcome = harness.orchestrator.collect("run-1", target())

    assert outcome.outcome == "fetched"
    assert outcome.rung == "browser"
    assert len(browser.requests) == 1


def test_a_rung_with_no_tool_at_all_is_refused_by_name(tmp_path):
    harness = Harness(
        tmp_path,
        make_source(collector=SourceCollector.browser),
        tools={},
    )
    harness.create_run()
    outcome = harness.orchestrator.collect("run-1", target())

    assert outcome.outcome == "skipped"
    assert outcome.reason_code == "collector_unavailable"
    assert "playwright" in outcome.detail


def test_the_ladder_refuses_a_rung_it_cannot_serve():
    with pytest.raises(CollectorUnavailable) as excinfo:
        Ladder().collector_for(make_source(collector=SourceCollector.managed))

    assert excinfo.value.rung == "managed"
    assert excinfo.value.dependency == "apify"


# --- the result ------------------------------------------------------------


def test_the_result_carries_the_capture_and_its_provenance(tmp_path):
    body = b"<html>rendered</html>"
    digest = hashlib.sha256(body).hexdigest()
    driver = FakeDriver("playwright", content=body, content_type="text/html")

    harness = Harness(
        tmp_path,
        make_source(collector=SourceCollector.browser),
        tools={"playwright": driver},
    )
    harness.create_run()
    outcome = harness.orchestrator.collect("run-1", target())

    assert outcome.outcome == "fetched"
    assert outcome.status == 200
    assert outcome.bytes == len(body)
    assert outcome.raw_sha256 == digest
    assert outcome.raw_path == f"data/raw/hn_algolia/{digest}.html"
    assert outcome.latency_ms >= 0
    assert outcome.retry_count == 0


def test_the_result_names_the_tool_that_took_the_capture(tmp_path):
    driver = FakeDriver("playwright", metadata={"browser": "chromium"})

    harness = Harness(
        tmp_path,
        make_source(collector=SourceCollector.browser),
        tools={"playwright": driver},
    )
    harness.create_run()

    result = harness.orchestrator._ladder.collect(
        harness.source, target(), validators=None
    )

    assert isinstance(result, CollectionResult)
    assert result.tool == "playwright"
    assert result.rung == "browser"
    assert result.metadata == {"browser": "chromium"}
    assert result.terms_status == "permitted"
    assert result.allowed_use == ["discovery", "retrieval", "analysis", "storage"]
    assert result.review_basis == "reviewed basis"
    assert result.user_agent == "ProductHunter/0.1 (Phase 1; local operator)"


def test_the_capture_is_stored_content_addressed(tmp_path):
    body = b"<html>rendered</html>"
    digest = hashlib.sha256(body).hexdigest()
    driver = FakeDriver("playwright", content=body)

    harness = Harness(
        tmp_path,
        make_source(collector=SourceCollector.browser),
        tools={"playwright": driver},
    )
    harness.create_run()
    harness.orchestrator.collect("run-1", target())

    stored = tmp_path / "data" / "raw" / "hn_algolia" / f"{digest}.html"
    assert stored.read_bytes() == body


def test_a_capture_taken_by_a_tool_is_written_to_the_fetch_log(tmp_path):
    driver = FakeDriver("playwright")

    harness = Harness(
        tmp_path,
        make_source(collector=SourceCollector.browser),
        tools={"playwright": driver},
    )
    harness.create_run()
    harness.orchestrator.collect("run-1", target())

    lines = harness.fetch_log()
    assert len(lines) == 1
    assert lines[0]["tool"] == "playwright"
    assert lines[0]["rung"] == "browser"
    # The line parses as a fetch record, so the run importer reads a
    # capture taken by a tool the same way it reads a plain one.
    from product_hunter.fetch import FetchRecord

    parsed = FetchRecord.model_validate_json(
        json.dumps(lines[0], sort_keys=True)
    )
    assert parsed.source_id == "hn_algolia"
    assert parsed.status == 200


def test_a_plain_http_capture_is_logged_by_the_shared_path(tmp_path):
    def handler(request):
        return body_200(b'{"hits": []}')

    harness = Harness(
        tmp_path,
        make_source(collector=SourceCollector.api),
        tools={},
        handler=handler,
    )
    harness.create_run()
    harness.orchestrator.collect("run-1", target())

    lines = harness.fetch_log()
    assert len(lines) == 1
    # The shared path logs a fetch record, and it carries no tool field,
    # so the ladder did not log a second line.
    assert "tool" not in lines[0]


def test_a_capture_over_the_byte_ceiling_is_refused(tmp_path):
    driver = FakeDriver("playwright", content=b"x" * 2000)

    harness = Harness(
        tmp_path,
        make_source(collector=SourceCollector.browser, max_bytes=1000),
        tools={"playwright": driver},
    )
    harness.create_run()
    outcome = harness.orchestrator.collect("run-1", target())

    assert outcome.outcome == "rejected"
    assert outcome.reason_code == "too_large"


def test_an_empty_capture_is_a_failure(tmp_path):
    driver = FakeDriver("playwright", content=b"")

    harness = Harness(
        tmp_path,
        make_source(collector=SourceCollector.browser),
        tools={"playwright": driver},
    )
    harness.create_run()
    outcome = harness.orchestrator.collect("run-1", target())

    assert outcome.outcome == "failed"
    assert outcome.reason_code == "empty_body"


def test_an_http_error_from_the_tool_is_a_failure(tmp_path):
    driver = FakeDriver("playwright", error=ToolHttpError(503, "the page is down"))

    harness = Harness(
        tmp_path,
        make_source(collector=SourceCollector.browser),
        tools={"playwright": driver},
    )
    harness.create_run()
    outcome = harness.orchestrator.collect("run-1", target())

    assert outcome.outcome == "failed"
    assert outcome.reason_code == "http_error"


# --- the retries and the budgets -------------------------------------------


def test_a_retryable_failure_is_retried_up_to_the_source_limit(tmp_path):
    driver = FakeDriver("playwright", error=ToolNetworkError("the socket died"))

    harness = Harness(
        tmp_path,
        make_source(
            collector=SourceCollector.browser,
            rate_limit=RateLimit(
                min_interval_seconds=1.0,
                max_requests_per_run=100,
                max_retries=2,
                backoff_base_seconds=1.0,
                backoff_max_seconds=30.0,
            ),
        ),
        tools={"playwright": driver},
    )
    harness.create_run()
    outcome = harness.orchestrator.collect("run-1", target())

    assert outcome.outcome == "failed"
    assert outcome.reason_code == "network_error"
    # The first attempt and the two retries.
    assert len(driver.requests) == 3


def test_a_retry_waits_before_the_next_attempt(tmp_path):
    driver = FakeDriver("playwright", error=ToolNetworkError("the socket died"))

    harness = Harness(
        tmp_path,
        make_source(collector=SourceCollector.browser),
        tools={"playwright": driver},
    )
    harness.create_run()
    harness.orchestrator.collect("run-1", target())

    # The first wait is the backoff, the second is twice the backoff.
    assert harness.sleeper.calls[:2] == [1.0, 2.0]


def test_a_retry_waits_for_as_long_as_the_service_asks(tmp_path):
    driver = FakeDriver(
        "playwright",
        error=ToolNetworkError("slow down", retry_after=7.0),
    )

    harness = Harness(
        tmp_path,
        make_source(collector=SourceCollector.browser),
        tools={"playwright": driver},
    )
    harness.create_run()
    harness.orchestrator.collect("run-1", target())

    assert harness.sleeper.calls[0] == 7.0


def test_a_retry_is_capped_by_the_sources_own_ceiling(tmp_path):
    driver = FakeDriver(
        "playwright",
        error=ToolNetworkError("slow down", retry_after=900.0),
    )

    harness = Harness(
        tmp_path,
        make_source(collector=SourceCollector.browser),
        tools={"playwright": driver},
    )
    harness.create_run()
    harness.orchestrator.collect("run-1", target())

    assert harness.sleeper.calls[0] == 30.0


def test_a_failure_that_is_not_retryable_is_not_retried(tmp_path):
    driver = FakeDriver("playwright", error=ToolEmptyResult("nothing there"))

    harness = Harness(
        tmp_path,
        make_source(collector=SourceCollector.browser),
        tools={"playwright": driver},
    )
    harness.create_run()
    outcome = harness.orchestrator.collect("run-1", target())

    assert outcome.outcome == "failed"
    assert outcome.reason_code == "empty_body"
    assert len(driver.requests) == 1


def test_the_per_source_cap_refuses_the_request(tmp_path):
    driver = FakeDriver("playwright")

    harness = Harness(
        tmp_path,
        make_source(
            collector=SourceCollector.browser,
            rate_limit=RateLimit(
                min_interval_seconds=1.0,
                max_requests_per_run=1,
                max_retries=0,
                backoff_base_seconds=1.0,
                backoff_max_seconds=30.0,
            ),
        ),
        tools={"playwright": driver},
    )
    harness.create_run()
    first = harness.orchestrator.collect("run-1", target("https://hn.algolia.com/a"))
    second = harness.orchestrator.collect("run-1", target("https://hn.algolia.com/b"))

    assert first.outcome == "fetched"
    assert second.outcome == "rejected"
    assert second.reason_code == "budget_exceeded"
    assert len(driver.requests) == 1


def test_the_contract_budget_is_charged_per_tool_attempt(tmp_path):
    crawler = FakeDriver("crawl4ai", error=ToolTimeout("the crawler timed out"))
    browser = FakeDriver("playwright")

    harness = Harness(
        tmp_path,
        make_source(
            collector=SourceCollector.crawler,
            fallbacks=[SourceCollector.browser],
        ),
        tools={"crawl4ai": crawler, "playwright": browser},
        contract=tight_contract(1),
    )
    harness.create_run()
    outcome = harness.orchestrator.collect("run-1", target())

    assert outcome.outcome == "fetched"
    # Two tools ran and the contract only had room for one fetch, so the
    # run went over and the overrun was reported.
    over = [event for event in harness.events if event["event"] == "budget_overrun"]
    assert over


def test_a_rung_that_never_ran_is_not_charged(tmp_path):
    """A tool that is not installed is not a fetch the run spent."""
    browser = FakeDriver("playwright")

    harness = Harness(
        tmp_path,
        make_source(
            collector=SourceCollector.crawler,
            fallbacks=[SourceCollector.browser],
        ),
        # The crawler is not installed, so only the browser ran.
        tools={"crawl4ai": None, "playwright": browser},
        contract=tight_contract(1),
    )
    harness.create_run()
    outcome = harness.orchestrator.collect("run-1", target())

    assert outcome.outcome == "fetched"
    over = [event for event in harness.events if event["event"] == "budget_overrun"]
    assert over == []


def test_a_fallback_that_failed_is_charged_for_both_tools(tmp_path):
    """A ladder that ran two tools and delivered nothing is charged twice."""
    crawler = FakeDriver("crawl4ai", error=ToolTimeout("the crawler timed out"))
    browser = FakeDriver("playwright", error=ToolTimeout("the browser timed out"))

    harness = Harness(
        tmp_path,
        make_source(
            collector=SourceCollector.crawler,
            fallbacks=[SourceCollector.browser],
        ),
        tools={"crawl4ai": crawler, "playwright": browser},
        contract=tight_contract(1),
    )
    harness.create_run()
    outcome = harness.orchestrator.collect("run-1", target())

    assert outcome.outcome == "failed"
    over = [event for event in harness.events if event["event"] == "budget_overrun"]
    assert over


def test_the_source_minimum_gap_is_waited_out(tmp_path):
    driver = FakeDriver("playwright")

    harness = Harness(
        tmp_path,
        make_source(
            collector=SourceCollector.browser,
            rate_limit=RateLimit(
                min_interval_seconds=10.0,
                max_requests_per_run=100,
                max_retries=2,
                backoff_base_seconds=1.0,
                backoff_max_seconds=30.0,
            ),
        ),
        tools={"playwright": driver},
    )
    harness.create_run()
    harness.orchestrator.collect("run-1", target("https://hn.algolia.com/a"))
    harness.orchestrator.collect("run-1", target("https://hn.algolia.com/b"))

    # One wait, for the gap between the two collections, and no longer
    # than the gap itself.
    assert len(harness.sleeper.calls) == 1
    assert 0 < harness.sleeper.calls[0] <= 10.0


# --- the source policy -----------------------------------------------------


def test_a_source_that_may_not_be_retrieved_is_refused_before_the_tool_runs(
    tmp_path,
):
    driver = FakeDriver("playwright")
    source = make_source(
        collector=SourceCollector.browser,
        terms_status=TermsStatus.reviewed,
        allowed_use=[AllowedUse.discovery],
    )

    harness = Harness(
        tmp_path,
        source,
        tools={"playwright": driver},
    )
    harness.create_run()
    outcome = harness.orchestrator.collect("run-1", target())

    assert outcome.outcome == "skipped"
    assert outcome.reason_code == "missing_use"
    assert driver.requests == []


def test_a_disabled_source_is_refused_before_the_tool_runs(tmp_path):
    driver = FakeDriver("playwright")

    harness = Harness(
        tmp_path,
        make_source(collector=SourceCollector.browser, enabled=False),
        tools={"playwright": driver},
    )
    harness.create_run()
    outcome = harness.orchestrator.collect("run-1", target())

    assert outcome.outcome == "skipped"
    assert outcome.reason_code == "source_disabled"
    assert driver.requests == []


def test_a_robots_refusal_ends_the_ladder(tmp_path):
    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(200, content=b"User-agent: *\nDisallow: /")
        raise AssertionError("the page must not be requested")

    crawler = FakeDriver("crawl4ai")
    browser = FakeDriver("playwright")

    harness = Harness(
        tmp_path,
        make_source(
            collector=SourceCollector.crawler,
            fallbacks=[SourceCollector.browser],
            robots=RobotsPolicy.required,
        ),
        tools={"crawl4ai": crawler, "playwright": browser},
        handler=handler,
    )
    harness.create_run()
    outcome = harness.orchestrator.collect("run-1", target())

    assert outcome.outcome == "rejected"
    assert outcome.reason_code == "robots_blocked"
    # A refusal is the operator's decision, so the ladder stopped and the
    # browser was not tried.
    assert crawler.requests == []
    assert browser.requests == []


def test_a_url_outside_the_allowlist_is_refused_before_the_tool_runs(tmp_path):
    driver = FakeDriver("playwright")

    harness = Harness(
        tmp_path,
        make_source(collector=SourceCollector.browser),
        tools={"playwright": driver},
    )
    harness.create_run()
    outcome = harness.orchestrator.collect(
        "run-1", target("https://elsewhere.example.com/page")
    )

    assert outcome.outcome == "rejected"
    assert outcome.reason_code == "unsafe_url"
    assert driver.requests == []


def test_an_unknown_source_is_refused_before_the_tool_runs(tmp_path):
    driver = FakeDriver("playwright")

    harness = Harness(
        tmp_path,
        make_source(collector=SourceCollector.browser),
        tools={"playwright": driver},
    )
    harness.create_run()
    outcome = harness.orchestrator.collect("run-1", target(source_id="nope"))

    assert outcome.outcome == "skipped"
    assert outcome.reason_code == "unknown_source"
    assert driver.requests == []


# --- the wiring ------------------------------------------------------------


def test_the_managed_rung_is_refused_when_the_credential_is_missing(
    tmp_path, monkeypatch
):
    from product_hunter.collection.adapters.adapters import apify_credentials

    monkeypatch.delenv("APIFY_TOKEN", raising=False)
    monkeypatch.delenv("APIFY_ACTOR_ID", raising=False)

    harness = Harness(
        tmp_path,
        make_source(collector=SourceCollector.managed),
        tools={"apify": None},
    )
    harness.create_run()
    outcome = harness.orchestrator.collect("run-1", target())

    assert apify_credentials() == ("", "")
    assert outcome.outcome == "skipped"
    assert outcome.reason_code == "collector_unavailable"
    assert "APIFY_TOKEN" in outcome.detail


def test_the_managed_rung_is_available_with_a_credential(monkeypatch):
    from product_hunter.collection.adapters.adapters import (
        apify_credentials,
        apify_driver,
    )

    monkeypatch.setenv("APIFY_TOKEN", "a-token")
    monkeypatch.setenv("APIFY_ACTOR_ID", "apify/website-content-crawler")

    assert apify_credentials() == ("a-token", "apify/website-content-crawler")
    assert apify_driver() is not None


def test_the_environment_answers_which_tools_it_has():
    from product_hunter.collection.adapters.adapters import available_drivers

    drivers = available_drivers()

    assert set(drivers) == {"crawl4ai", "playwright", "apify"}


def test_the_adapters_are_built_without_a_driver_when_none_is_given(tmp_path):
    source = make_source(collector=SourceCollector.browser)
    registry = SourceRegistry({source.source_id: source})
    raw_store = RawStore(tmp_path)
    from product_hunter.fetch import Fetcher, RequestBudget

    client = httpx.Client(transport=httpx.MockTransport(lambda r: body_200(b"")))
    fetcher = Fetcher(
        registry,
        raw_store,
        JsonlLog(tmp_path / "fetches.jsonl"),
        JsonlLog(tmp_path / "rejections.jsonl"),
        client,
        resolver=lambda host: [PUBLIC],
    )

    adapters = build_adapters(
        registry, raw_store, RequestBudget(), fetcher, drivers={}
    )

    assert adapters["playwright"].available is False
    assert adapters["crawl4ai"].available is False
    assert adapters["apify"].available is False


def test_the_adapters_are_built_with_a_driver_when_one_is_given(tmp_path):
    source = make_source(collector=SourceCollector.browser)
    registry = SourceRegistry({source.source_id: source})
    raw_store = RawStore(tmp_path)
    from product_hunter.fetch import Fetcher, RequestBudget

    client = httpx.Client(transport=httpx.MockTransport(lambda r: body_200(b"")))
    fetcher = Fetcher(
        registry,
        raw_store,
        JsonlLog(tmp_path / "fetches.jsonl"),
        JsonlLog(tmp_path / "rejections.jsonl"),
        client,
        resolver=lambda host: [PUBLIC],
    )

    adapters = build_adapters(
        registry,
        raw_store,
        RequestBudget(),
        fetcher,
        drivers={"playwright": FakeDriver("playwright")},
    )

    assert adapters["playwright"].available is True
    assert adapters["crawl4ai"].available is False

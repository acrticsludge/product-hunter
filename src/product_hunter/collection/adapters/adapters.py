"""The four adapters and the drivers that take the bytes.

The HTTP adapter is the plain one: it hands the target to the shared fetch
path and describes what came back. The other three take a page with a
tool that is not a plain HTTP request, and each of them holds the source
policy around the tool.

The drivers are the only part that knows a dependency, and each of them
imports its library lazily. An adapter whose driver is missing is
unavailable, and an unavailable adapter is refused by name. That is the
behaviour the project has today, and it does not change until the
dependency is installed.

A driver is handed an admitted URL and a gate, and it returns the bytes.
It is never handed a token, a cookie or a page-supplied instruction, and
the capture it returns carries no text beyond the page it took.
"""

from __future__ import annotations

import asyncio
import json
import os
from concurrent.futures import ThreadPoolExecutor

from product_hunter.collection.adapters.base import (
    AdapterBase,
    Capture,
    CollectionResult,
    CollectRequest,
    Driver,
    ToolEmptyResult,
    ToolError,
    ToolHttpError,
    ToolNetworkError,
    ToolTimeout,
    ToolUnavailable,
)
from product_hunter.fetch import Fetcher, Validators
from product_hunter.safety import UrlBlocked
from product_hunter.sources import SourceRegistry


class HttpAdapter:
    """The rung that fetches a target over HTTP through the shared path.

    The shared path owns the admission, the budget, the redirect rules and
    the robots check, so this adapter does not repeat them: it hands the
    target to the fetcher and describes what came back.
    """

    name = "http"
    available = True

    def __init__(self, fetcher: Fetcher) -> None:
        self._fetcher = fetcher

    def collect(
        self, target, *, validators: Validators | None = None
    ) -> CollectionResult:
        record = self._fetcher.fetch(
            target.source_id, target.url, validators=validators
        )
        return CollectionResult.from_fetch_record(record, tool=self.name)


class Crawl4aiAdapter(AdapterBase):
    """The rung that takes a page with the local crawler."""

    name = "crawl4ai"


class PlaywrightAdapter(AdapterBase):
    """The rung that takes a page with a browser."""

    name = "playwright"


class ApifyAdapter(AdapterBase):
    """The rung that takes a page with a managed actor.

    The token and the actor are configuration. A source cannot name an
    actor, and a page cannot ask for one, so the managed rung can only do
    what the operator set up.
    """

    name = "apify"
    unavailable_detail = (
        "the managed rung needs APIFY_TOKEN and APIFY_ACTOR_ID in the environment"
    )


# --- the drivers ------------------------------------------------------------


class Crawl4aiDriver:
    """Takes a page with the local crawler.

    The crawler runs a browser of its own, so the browser is configured
    the way the spec asks: headless, no profile on disk, no cookies, no
    downloads, and a bounded page timeout.

    A route handler installed through the crawler's own hook admits every
    request through the gate. Without it the crawler could reach a host
    the source does not declare, so a crawler that cannot be gated is
    refused rather than run.
    """

    name = "crawl4ai"

    def take(self, request: CollectRequest) -> Capture:
        return _run_sync(self._take(request))

    async def _take(self, request: CollectRequest) -> Capture:
        try:
            from crawl4ai import (
                AsyncWebCrawler,
                BrowserConfig,
                CacheMode,
                CrawlerRunConfig,
            )
        except ImportError as exc:
            raise ToolUnavailable("crawl4ai is not installed") from exc

        blocked: list[str] = []
        gate = request.gate
        # The gate is only in place once the hook has installed the route
        # handler. A crawler that ignores the hook would fetch from any
        # host, so the crawl is refused rather than trusted.
        gated = False

        async def on_page_context_created(page=None, context=None, **kwargs) -> None:
            nonlocal gated
            resolved = (
                context
                if context is not None
                else getattr(kwargs.get("ctx"), "context", None)
            )
            if resolved is None:
                raise ToolUnavailable(
                    "the local crawler did not give a page context to gate"
                )

            async def route_filter(route) -> None:
                incoming = route.request
                try:
                    previous = incoming.redirected_from
                    if previous is not None:
                        gate.redirect(previous.url, incoming.url)
                    else:
                        gate.admit(incoming.url)
                except UrlBlocked:
                    blocked.append(incoming.url)
                    await route.abort()
                    return
                await route.continue_()

            await resolved.route("**/*", route_filter)
            gated = True

        browser_config = BrowserConfig(
            headless=True,
            verbose=False,
            use_persistent_context=False,
            user_data_dir=None,
            accept_downloads=False,
            user_agent=request.policy.user_agent,
        )
        run_config = CrawlerRunConfig(
            cache_mode=CacheMode.BYPASS,
            page_timeout=int(request.policy.timeout_seconds * 1000),
            wait_until="domcontentloaded",
            screenshot=False,
            pdf=False,
            capture_mhtml=False,
            scan_full_page=False,
        )
        crawler = AsyncWebCrawler(config=browser_config)
        async with crawler:
            crawler.crawler_strategy.set_hook(
                "on_page_context_created", on_page_context_created
            )
            result = await crawler.arun(url=request.url, config=run_config)

        status = int(getattr(result, "status_code", 0) or 0)
        if not getattr(result, "success", False):
            raise ToolHttpError(status, f"the crawl answered {status}")
        if not gated:
            # The crawl worked, but nothing checked the requests it made,
            # so what came back cannot be trusted to the source policy.
            raise ToolUnavailable(
                "the local crawler did not install the request gate, so its "
                "requests were not checked against the source policy"
            )
        text = _markdown(result)
        if not text:
            raise ToolEmptyResult("the crawl produced no text")
        return Capture(
            content=text.encode("utf-8"),
            content_type="text/markdown",
            status=status or 200,
            final_url=request.url,
            metadata={
                "crawler_status": str(status),
                "blocked_resources": str(len(blocked)),
            },
        )


class PlaywrightDriver:
    """Takes a page with a throwaway browser context.

    The context is created for the capture and thrown away with it: no
    storage state, no permissions, no credentials, no service worker, and
    downloads off. The route handler admits every request through the
    gate, so the browser cannot reach a host the source does not declare.

    The browser is started and stopped inside the capture, so a failed
    capture cannot leave a browser running.
    """

    name = "playwright"

    def take(self, request: CollectRequest) -> Capture:
        try:
            from playwright.sync_api import Error as PlaywrightError
            from playwright.sync_api import TimeoutError as PlaywrightTimeout
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise ToolUnavailable("playwright is not installed") from exc

        gate = request.gate
        blocked: list[str] = []

        def handle(route) -> None:
            incoming = route.request
            try:
                previous = incoming.redirected_from
                if previous is not None:
                    gate.redirect(previous.url, incoming.url)
                else:
                    gate.admit(incoming.url)
            except UrlBlocked:
                blocked.append(incoming.url)
                route.abort()
                return
            route.continue_()

        playwright = sync_playwright().start()
        browser = None
        context = None
        try:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(
                user_agent=request.policy.user_agent,
                accept_downloads=False,
                permissions=[],
                ignore_https_errors=False,
                bypass_csp=False,
                service_workers="block",
            )
            context.route("**/*", handle)
            page = context.new_page()
            response = page.goto(
                request.url,
                timeout=int(request.policy.timeout_seconds * 1000),
                wait_until="domcontentloaded",
            )
            content = page.content()
            status = response.status if response is not None else 200
            final_url = page.url
        except PlaywrightTimeout as exc:
            raise ToolTimeout(f"the browser timed out: {exc}") from exc
        except PlaywrightError as exc:
            raise ToolNetworkError(f"the browser could not take the page: {exc}") from exc
        finally:
            # The context is closed before the browser, and both are closed
            # even when the capture failed, so no browser is left running.
            for closeable in (context, browser):
                if closeable is None:
                    continue
                try:
                    closeable.close()
                except Exception:  # noqa: BLE001 - cleanup must not mask the failure
                    pass
            try:
                playwright.stop()
            except Exception:  # noqa: BLE001 - cleanup must not mask the failure
                pass

        if not content:
            raise ToolEmptyResult("the browser produced no content")
        return Capture(
            content=content.encode("utf-8"),
            content_type="text/html",
            status=status,
            final_url=final_url,
            metadata={
                "blocked_resources": str(len(blocked)),
                "browser": "chromium",
            },
        )


class ApifyDriver:
    """Takes a page with a managed actor.

    The actor and the token are configuration. The token is never written
    into the capture's metadata and never left in an error detail.
    """

    name = "apify"

    def __init__(self, token: str, actor_id: str) -> None:
        self._token = token
        self._actor_id = actor_id

    def take(self, request: CollectRequest) -> Capture:
        try:
            from apify_client import ApifyClient
            from datetime import timedelta
        except ImportError as exc:
            raise ToolUnavailable("the Apify client is not installed") from exc

        run_input = {
            "startUrls": [{"url": request.url}],
            "maxItems": 1,
        }
        timeout = timedelta(seconds=int(request.policy.timeout_seconds))
        try:
            client = ApifyClient(token=self._token)
            run = client.actor(self._actor_id).call(
                run_input=run_input,
                run_timeout=timeout,
                wait_duration=timeout,
            )
            items = client.dataset(_dataset_id(run)).list_items()
        except ToolError:
            raise
        except Exception as exc:  # noqa: BLE001 - the client's errors are many kinds
            raise ToolNetworkError(self._redact(f"{type(exc).__name__}: {exc}")) from exc

        if isinstance(items, dict):
            rows = items.get("items")
        else:
            rows = getattr(items, "items", None)
        if not rows:
            raise ToolEmptyResult("the managed run produced no items")
        text = _item_text(rows[0])
        if not text:
            raise ToolEmptyResult("the managed run produced no content")
        return Capture(
            content=text.encode("utf-8"),
            content_type="text/markdown",
            status=200,
            final_url=request.url,
            metadata={"actor": self._actor_id, "items": str(len(rows))},
        )

    def _redact(self, text: str) -> str:
        if self._token and self._token in text:
            return text.replace(self._token, "<token>")
        return text


# --- the helpers ------------------------------------------------------------


def _run_sync(coroutine):
    """Run a coroutine from a synchronous caller.

    The collection layer is synchronous and the local crawler is not. A
    caller that is already inside an event loop gets the coroutine run on
    a worker thread with a loop of its own.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coroutine)
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coroutine).result()


def _markdown(result) -> str:
    """The text the crawler produced.

    The crawler answers with a markdown object in some versions and with
    a string in others, so both are read.
    """
    markdown = getattr(result, "markdown", None)
    if isinstance(markdown, str):
        return markdown
    for attribute in ("raw_markdown", "fit_markdown"):
        value = getattr(markdown, attribute, None)
        if value:
            return str(value)
    return ""


def _dataset_id(run) -> str:
    """The dataset the run wrote to.

    The client answers with a mapping in some versions and with an object
    in others, so both are read.
    """
    if isinstance(run, dict):
        for key in ("defaultDatasetId", "default_dataset_id"):
            value = run.get(key)
            if value:
                return str(value)
    for key in ("default_dataset_id", "defaultDatasetId"):
        value = getattr(run, key, None)
        if value:
            return str(value)
    raise ToolError("the managed run reported no dataset")


def _item_text(item) -> str:
    """The text of one dataset item.

    The actors differ in what they put in an item, so the common keys are
    read in order and an item with none of them is serialised as JSON.
    """
    if isinstance(item, str):
        return item
    if not isinstance(item, dict):
        return ""
    for key in ("markdown", "text", "html", "body", "content"):
        value = item.get(key)
        if isinstance(value, str) and value:
            return value
    return json.dumps(item, sort_keys=True)


# --- the factories ----------------------------------------------------------


def crawl4ai_driver() -> Driver | None:
    """The local crawler driver, or None when the library is not installed."""
    try:
        import crawl4ai  # noqa: F401
    except ImportError:
        return None
    return Crawl4aiDriver()


def playwright_driver() -> Driver | None:
    """The browser driver, or None when the library is not installed."""
    try:
        import playwright.sync_api  # noqa: F401
    except ImportError:
        return None
    return PlaywrightDriver()


def apify_credentials() -> tuple[str, str]:
    """The managed rung's token and actor, read from the environment.

    Neither is ever read from the source registry, and neither is ever
    taken from a page.
    """
    token = os.environ.get("APIFY_TOKEN", "").strip()
    actor = os.environ.get("APIFY_ACTOR_ID", "").strip()
    return token, actor


def apify_driver() -> Driver | None:
    """The managed driver, or None when the credential is not configured."""
    token, actor = apify_credentials()
    if not token or not actor:
        return None
    return ApifyDriver(token, actor)


def available_drivers() -> dict[str, Driver | None]:
    """The drivers this environment can serve.

    A driver whose library is not installed, or whose credential is not
    configured, is None. The rung is then refused by name.
    """
    return {
        "crawl4ai": crawl4ai_driver(),
        "playwright": playwright_driver(),
        "apify": apify_driver(),
    }


def build_adapters(
    registry: SourceRegistry,
    raw_store,
    budget,
    fetcher: Fetcher,
    *,
    drivers: dict[str, Driver | None] | None = None,
    **kwargs,
) -> dict[str, AdapterBase]:
    """The adapters, keyed by tool name.

    The drivers are given, not found. A mapping of ``None`` means the
    environment is asked which tools it has; an empty mapping means no
    tool beyond HTTP is served, and every other rung is refused by name.
    """
    resolved = available_drivers() if drivers is None else dict(drivers)
    adapters: dict[str, AdapterBase] = {}
    for name, adapter in (
        ("crawl4ai", Crawl4aiAdapter),
        ("playwright", PlaywrightAdapter),
        ("apify", ApifyAdapter),
    ):
        adapters[name] = adapter(
            registry, raw_store, budget, fetcher, driver=resolved.get(name), **kwargs
        )
    return adapters

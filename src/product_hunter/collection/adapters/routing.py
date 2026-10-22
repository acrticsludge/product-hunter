"""The rung table and the order the tools are tried in.

The order is a pure function of the source's configuration. The source's
``collector`` names the rung, and the source's ``fallbacks`` name the
rungs below it that may be tried when the first one cannot deliver.

Nothing else changes the order. A model that wants a browser for a page
the registry says is plain HTTP does not get one, because the model is
not in the routing path.
"""

from __future__ import annotations

from product_hunter.collection.adapters.base import (
    Attempt,
    CollectionResult,
    ToolError,
)
from product_hunter.sources import Collector, SourceConfig

# The tool that serves each rung. An API and a feed are both an HTTP
# request, so they share the HTTP adapter.
RUNG_TOOLS: dict[Collector, str] = {
    Collector.api: "http",
    Collector.rss: "http",
    Collector.http: "http",
    Collector.crawler: "crawl4ai",
    Collector.browser: "playwright",
    Collector.managed: "apify",
}

# The dependency a rung needs. It is named in the refusal when the rung
# cannot be served, so the operator is told what is missing.
RUNG_DEPENDENCIES: dict[Collector, str] = {
    Collector.crawler: "crawl4ai",
    Collector.browser: "playwright",
    Collector.managed: "apify",
}


class LadderExhausted(ToolError):
    """Every tool the source allows was tried, and none delivered.

    The reason code is the last tool's own, so the caller still knows
    whether it was a timeout, a transport error or a refusal.
    """

    def __init__(
        self, detail: str, attempts: list[Attempt], reason_code: str
    ) -> None:
        super().__init__(detail)
        self.attempts = attempts
        self.reason_code = reason_code


def rung_order(source: SourceConfig) -> list[Collector]:
    """The rungs that may serve the source, cheapest first.

    A fallback that repeats the preferred rung is dropped, so the list is
    the source's order without repeats.
    """
    order: list[Collector] = [source.collector]
    for fallback in source.fallbacks:
        if fallback not in order:
            order.append(fallback)
    return order


def tool_for(rung: Collector) -> str:
    """The tool that serves the rung."""
    return RUNG_TOOLS[rung]


def dependency_for(rung: Collector) -> str:
    """The dependency the rung needs."""
    return RUNG_DEPENDENCIES.get(rung, "the collector")


def summarize(attempts: list[Attempt]) -> str:
    """The attempts, as one bounded line.

    The line names the tool and the reason and stops there. It carries no
    page text, because no page text is ever in an attempt.
    """
    parts = [
        f"{attempt.tool}: {attempt.reason_code or attempt.outcome}: {attempt.detail}"
        for attempt in attempts
    ]
    return "; ".join(parts)[:1000]


__all__ = [
    "RUNG_DEPENDENCIES",
    "RUNG_TOOLS",
    "Attempt",
    "CollectionResult",
    "LadderExhausted",
    "dependency_for",
    "rung_order",
    "summarize",
    "tool_for",
]

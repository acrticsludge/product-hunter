"""The collection ladder: the rung that fetches a target, and the seam for more.

The ladder maps a source's ``collector`` value onto a rung. The order of
preference is an API or a feed, then direct HTTP, then a local crawler, then
browser automation, then a managed scraper.

The three active rungs share one implementation, because a feed is an HTTP GET
and the parsing of a feed is a document concern that belongs to the next
stage.

A rung that is not registered is refused, not approximated. A page that needed
a browser would be fetched incompletely over plain HTTP, and the operator
would not know the page was incomplete.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from product_hunter.fetch import Fetcher, FetchRecord, Validators

if TYPE_CHECKING:
    from product_hunter.collection.orchestrator import CollectionTarget

# The rungs the shared fetch path serves.
_HTTP_RUNGS = ("api", "rss", "http")

# The rung and the dependency that would serve it. None of them is installed,
# so the rung is refused with the name of what is missing.
_UNAVAILABLE = {
    "crawler": "crawl4ai",
    "browser": "playwright",
    "managed": "apify",
}


class CollectorUnavailable(Exception):
    """The rung the source needs is not installed."""

    def __init__(self, rung: str, dependency: str) -> None:
        self.rung = rung
        self.dependency = dependency
        super().__init__(
            f"the {rung!r} rung needs {dependency!r}, which is not installed"
        )


class Collector(Protocol):
    """What a rung does with a target."""

    name: str

    def collect(
        self, target: CollectionTarget, *, validators: Validators | None = None
    ) -> FetchRecord: ...


class HttpCollector:
    """The rung that fetches a target over HTTP through the shared path."""

    def __init__(self, fetcher: Fetcher, name: str = "http") -> None:
        self._fetcher = fetcher
        self.name = name

    def collect(
        self, target: CollectionTarget, *, validators: Validators | None = None
    ) -> FetchRecord:
        return self._fetcher.fetch(
            target.source_id, target.url, validators=validators
        )


class Ladder:
    """The rungs a source may be collected with."""

    def __init__(
        self,
        fetcher: Fetcher | None = None,
        *,
        collectors: dict[str, Collector] | None = None,
    ) -> None:
        if collectors is not None:
            self._collectors = dict(collectors)
        elif fetcher is not None:
            self._collectors = {
                rung: HttpCollector(fetcher, rung) for rung in _HTTP_RUNGS
            }
        else:
            self._collectors = {}

    def collector_for(self, source) -> Collector:
        """The rung that serves the source, or a refusal naming what is missing."""
        rung = source.collector.value
        collector = self._collectors.get(rung)
        if collector is not None:
            return collector
        raise CollectorUnavailable(rung, _UNAVAILABLE.get(rung, "the collector"))

"""The collection ladder: the rung that takes a target, and the seam for more.

The ladder maps a source's ``collector`` value onto a rung, and the
source's ``fallbacks`` onto the rungs below it that may be tried when the
first one cannot deliver.

The order of preference is the spec's: an API or a feed, then direct HTTP,
then a local crawler, then browser automation, then a managed scraper.

The order is a pure function of the source's configuration, so the
routing is deterministic and a model is not in it. A policy refusal ends
the ladder, because a refusal is the operator's decision about the
source and a more expensive tool does not get to overrule it. A tool
failure does not end the ladder, when the source said where to go next.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from product_hunter.collection.adapters.adapters import HttpAdapter
from product_hunter.collection.adapters.base import (
    POLICY_REASON_CODES,
    Attempt,
    CollectionResult,
    ToolError,
)
from product_hunter.collection.adapters.routing import (
    LadderExhausted,
    dependency_for,
    rung_order,
    summarize,
    tool_for,
)
from product_hunter.fetch import (
    Fetcher,
    FetchError,
    Validators,
    bounded_detail,
)
from product_hunter.safety import UrlBlocked
from product_hunter.sources import SourceBlocked, SourceConfig

if TYPE_CHECKING:
    from product_hunter.collection.orchestrator import CollectionTarget


class CollectorUnavailable(Exception):
    """The rung the source needs is not installed."""

    def __init__(self, rung: str, dependency: str, detail: str = "") -> None:
        self.rung = rung
        self.dependency = dependency
        self.detail = detail
        if detail:
            super().__init__(f"the {rung!r} rung is unavailable: {detail}")
        else:
            super().__init__(
                f"the {rung!r} rung needs {dependency!r}, which is not installed"
            )


class Collector(Protocol):
    """What a rung does with a target."""

    name: str
    available: bool

    def collect(
        self, target: CollectionTarget, *, validators: Validators | None = None
    ) -> CollectionResult: ...


class Ladder:
    """The rungs a source may be collected with, and the order they are tried.

    The rungs are keyed by the tool that serves them, so an API, a feed
    and a plain page all share the one HTTP adapter.
    """

    def __init__(
        self,
        fetcher: Fetcher | None = None,
        *,
        adapters: dict[str, Collector] | None = None,
    ) -> None:
        self._adapters: dict[str, Collector] = {}
        if fetcher is not None:
            self._adapters["http"] = HttpAdapter(fetcher)
        if adapters:
            self._adapters.update(adapters)

    def collector_for(self, source: SourceConfig) -> Collector:
        """The rung that serves the source, or a refusal naming what is missing."""
        adapter = self._adapters.get(tool_for(source.collector))
        if adapter is not None and adapter.available:
            return adapter
        raise CollectorUnavailable(
            source.collector.value, dependency_for(source.collector)
        )

    def collect(
        self,
        source: SourceConfig,
        target: CollectionTarget,
        *,
        validators: Validators | None = None,
    ) -> CollectionResult:
        """Take the target with the cheapest tool that can deliver it.

        The candidates are the source's rung and the source's fallbacks, in
        that order. The first one that delivers wins, and the tools that
        did not are in the result's attempts, so an escalation is visible
        in the record.
        """
        attempts: list[Attempt] = []
        ran = False
        for rung in rung_order(source):
            tool = tool_for(rung)
            adapter = self._adapters.get(tool)
            if adapter is None or not adapter.available:
                attempts.append(
                    Attempt(
                        tool=tool,
                        outcome="unavailable",
                        reason_code="collector_unavailable",
                        detail=_missing(adapter, rung),
                    )
                )
                continue
            ran = True
            try:
                result = adapter.collect(target, validators=validators)
            except (FetchError, SourceBlocked, UrlBlocked) as exc:
                reason = getattr(exc, "reason_code", "fetch_failed")
                attempts.append(
                    Attempt(
                        tool=adapter.name,
                        outcome="refused" if reason in POLICY_REASON_CODES else "failed",
                        reason_code=reason,
                        detail=bounded_detail(str(exc)),
                    )
                )
                if reason in POLICY_REASON_CODES:
                    # A refusal is the operator's decision about the
                    # source, so it ends the ladder. A more expensive tool
                    # does not get to overrule it.
                    raise
                continue
            except ToolError as exc:
                attempts.append(
                    Attempt(
                        tool=adapter.name,
                        outcome="failed",
                        reason_code=exc.reason_code,
                        detail=bounded_detail(str(exc)),
                    )
                )
                continue
            return result.model_copy(
                update={
                    "rung": rung.value,
                    "attempts": attempts
                    + [Attempt(tool=adapter.name, outcome="ok")],
                }
            )
        if not ran:
            # Nothing could even be attempted: the rung the source asked
            # for is not installed.
            adapter = self._adapters.get(tool_for(source.collector))
            raise CollectorUnavailable(
                source.collector.value,
                dependency_for(source.collector),
                _detail(adapter),
            )
        raise LadderExhausted(
            summarize(attempts), attempts, _last_reason(attempts)
        )


def _detail(adapter) -> str:
    """What the adapter says it is missing, when it says anything."""
    return getattr(adapter, "unavailable_detail", "") or ""


def _missing(adapter, rung) -> str:
    """What the rung is missing, for the refusal."""
    return _detail(adapter) or dependency_for(rung)


def _last_reason(attempts: list[Attempt]) -> str:
    """The reason the ladder stopped on.

    A tool that is not installed says nothing about the page, so the last
    reason that was about the page is the one that counts.
    """
    for attempt in reversed(attempts):
        if attempt.reason_code != "collector_unavailable":
            return attempt.reason_code
    return "collector_unavailable"

"""The OpenCode discovery surface and the refresher that keeps it fresh.

Two documented surfaces are used, in order: the model route
(``opencode api get /api/model``), which carries the status and the enabled
flag, and the model list (``opencode models``), which carries neither. Both go
through the same argv runner as the generation path: an argv list, no shell, no
raw HTTP, and no provider key.

The refresher re-discovers when the cached catalogue has aged past its
interval. A failed discovery keeps the previous catalogue: discovery failure
is never fatal to a running pipeline. The failure is remembered until the
interval elapses, so a broken server costs one attempt per interval.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from datetime import datetime
from typing import Protocol, runtime_checkable

from pydantic import ValidationError

from product_hunter.llm.base import ProviderError
from product_hunter.llm.catalog import (
    CatalogDiff,
    CatalogError,
    MalformedCatalogError,
    ModelCatalog,
    parse_model_catalog,
    parse_model_list,
    utcnow,
)
from product_hunter.llm.opencode import (
    Runner,
    ServerInfo,
    _resolve_cli,
    run_opencode_cli,
)

ROUTE_SOURCE = "opencode api get /api/model"
LIST_SOURCE = "opencode models"
INFO_SOURCE = "opencode api get /api/info"


class DiscoveryError(CatalogError):
    """Every documented discovery surface failed."""


@runtime_checkable
class ModelDiscovery(Protocol):
    """Something that can produce the current model catalogue."""

    def discover(self) -> ModelCatalog:
        ...


@runtime_checkable
class CatalogSource(Protocol):
    """Something that hands out a catalogue, refreshed on demand."""

    def catalog(self, *, force: bool = False) -> ModelCatalog:
        ...


class OpenCodeModelDiscovery:
    """Discover the models OpenCode offers, over the documented CLI."""

    name = "opencode"

    def __init__(
        self,
        *,
        timeout_seconds: int = 30,
        runner: Runner = subprocess.run,
    ) -> None:
        self._timeout_seconds = timeout_seconds
        self._runner = runner

    def discover(self) -> ModelCatalog:
        """The current catalogue.

        The model route is the primary surface. The model list is the
        fallback, and every entry it yields is ``unknown`` because it reports
        no status. If both surfaces fail, the failure is refused by name.
        """
        cli = _resolve_cli()
        try:
            raw = run_opencode_cli(
                [cli, "api", "get", "/api/model"],
                timeout_seconds=self._timeout_seconds,
                runner=self._runner,
                label=ROUTE_SOURCE,
            )
            catalog = parse_model_catalog(raw, source=ROUTE_SOURCE)
        except (ProviderError, MalformedCatalogError) as route_error:
            try:
                listed = run_opencode_cli(
                    [cli, "models"],
                    timeout_seconds=self._timeout_seconds,
                    runner=self._runner,
                    label=LIST_SOURCE,
                )
                catalog = parse_model_list(listed, source=LIST_SOURCE)
            except (ProviderError, MalformedCatalogError) as list_error:
                raise DiscoveryError(
                    f"model discovery failed: {route_error}; "
                    f"the model list failed too: {list_error}"
                ) from list_error
        version = self._server_version(cli)
        if version is not None:
            catalog = catalog.model_copy(update={"server_version": version})
        return catalog

    def _server_version(self, cli: str) -> str | None:
        """The OpenCode server version, or None when the server will not say."""
        try:
            raw = run_opencode_cli(
                [cli, "api", "get", "/api/info"],
                timeout_seconds=self._timeout_seconds,
                runner=self._runner,
                label=INFO_SOURCE,
            )
            return ServerInfo.model_validate_json(raw).version
        except (ProviderError, ValidationError):
            return None


class CatalogRefresher:
    """Hold the catalogue and re-discover it when it ages out."""

    def __init__(
        self,
        discovery: ModelDiscovery,
        *,
        refresh_interval_seconds: int = 900,
        now: Callable[[], datetime] = utcnow,
    ) -> None:
        self._discovery = discovery
        self._refresh_interval_seconds = refresh_interval_seconds
        self._now = now
        self._catalog: ModelCatalog | None = None
        self._last_error: str | None = None
        self._failure: Exception | None = None
        self._failed_at: datetime | None = None

    @property
    def last_error(self) -> str | None:
        """The message of the last failed discovery, if any."""
        return self._last_error

    def catalog(self, *, force: bool = False) -> ModelCatalog:
        """The catalogue, re-discovered when it is older than the interval.

        A failed discovery keeps the previous catalogue and records the error.
        It raises only when there is no catalogue at all. The failure is
        remembered until the interval elapses, so a broken server costs one
        discovery per interval instead of one per call. A forced refresh
        always tries again.
        """
        if force:
            return self.refresh()[0]
        if self._catalog is None:
            self._reraise_failure()
            return self.refresh()[0]
        if self._expired(self._catalog):
            if self._failure_is_fresh():
                # The last discovery failed: keep the catalogue we have rather
                # than pay for another attempt on every call.
                return self._catalog
            return self.refresh()[0]
        return self._catalog

    def refresh(self) -> tuple[ModelCatalog, CatalogDiff]:
        """Discover again and report what changed against the previous catalogue."""
        try:
            catalog = self._discovery.discover()
        except (DiscoveryError, ProviderError, MalformedCatalogError) as exc:
            self._last_error = f"{type(exc).__name__}: {exc}"
            self._failure = exc
            self._failed_at = self._now()
            if self._catalog is None:
                raise
            return self._catalog, CatalogDiff()
        previous = self._catalog
        self._catalog = catalog
        self._last_error = None
        self._failure = None
        self._failed_at = None
        if previous is None:
            return catalog, CatalogDiff()
        return catalog, previous.diff(catalog)

    def _expired(self, catalog: ModelCatalog) -> bool:
        """True when the catalogue is older than the refresh interval."""
        age = (self._now() - catalog.discovered_at).total_seconds()
        return age >= self._refresh_interval_seconds

    def _failure_is_fresh(self) -> bool:
        """True when the last failure is younger than the refresh interval."""
        if self._failure is None or self._failed_at is None:
            return False
        age = (self._now() - self._failed_at).total_seconds()
        return age < self._refresh_interval_seconds

    def _reraise_failure(self) -> None:
        """Raise the remembered failure again, when it is still fresh."""
        failure = self._failure
        if failure is not None and self._failure_is_fresh():
            raise type(failure)(str(failure)) from failure

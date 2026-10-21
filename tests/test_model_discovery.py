from __future__ import annotations

import json
import subprocess
from datetime import timedelta

import pytest

from product_hunter.llm.catalog import (
    Availability,
    ModelCatalog,
    parse_model_catalog,
    utcnow,
)
from product_hunter.llm.discovery import (
    CatalogRefresher,
    DiscoveryError,
    OpenCodeModelDiscovery,
)

ROUTE = json.dumps(
    {
        "location": {"directory": "."},
        "data": [
            {
                "id": "alpha-flash",
                "modelID": "alpha-flash",
                "providerID": "opencode-go",
                "status": "active",
                "enabled": True,
                "name": "Alpha Flash",
            }
        ],
    }
)
INFO = json.dumps({"version": "2.1.2", "pid": 31052})
LIST = "opencode-go/alpha-flash\nopencode-go/beta-flash\n"


def completed(stdout: str, returncode: int = 0, stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


class FakeRunner:
    """Replays one result per documented surface, and records every call."""

    def __init__(
        self,
        *,
        route: subprocess.CompletedProcess | None = None,
        models: subprocess.CompletedProcess | None = None,
        info: subprocess.CompletedProcess | None = None,
    ) -> None:
        self._route = route if route is not None else completed(ROUTE)
        self._models = models if models is not None else completed(LIST)
        self._info = info if info is not None else completed(INFO)
        self.calls: list[tuple[list[str], dict]] = []

    def __call__(self, argv, **kwargs):
        self.calls.append((list(argv), dict(kwargs)))
        if "/api/model" in argv:
            return self._route
        if "/api/info" in argv:
            return self._info
        return self._models


class FakeDiscovery:
    """Hands out the catalogues it was given, one per call."""

    def __init__(self, *catalogs: ModelCatalog) -> None:
        self._catalogs = list(catalogs)
        self.calls = 0

    def discover(self) -> ModelCatalog:
        self.calls += 1
        if len(self._catalogs) == 1:
            return self._catalogs[0]
        return self._catalogs.pop(0)


def catalog_with(*model_ids: str, status: str = "active") -> ModelCatalog:
    return parse_model_catalog(
        json.dumps(
            {
                "data": [
                    {
                        "id": model_id,
                        "modelID": model_id,
                        "providerID": "opencode-go",
                        "status": status,
                        "enabled": True,
                    }
                    for model_id in model_ids
                ]
            }
        ),
        source="test",
    )


def test_discovery_parses_the_model_route() -> None:
    runner = FakeRunner()
    discovery = OpenCodeModelDiscovery(runner=runner)

    catalog = discovery.discover()

    assert [entry.identity.key for entry in catalog.entries] == [
        "opencode-go/alpha-flash"
    ]
    assert catalog.entries[0].availability is Availability.available
    assert catalog.server_version == "2.1.2"
    route_call = runner.calls[0]
    assert route_call[0][-3:] == ["api", "get", "/api/model"]
    assert route_call[1]["shell"] is False
    assert isinstance(route_call[0], list)


def test_discovery_falls_back_to_the_model_list() -> None:
    runner = FakeRunner(route=completed("boom", returncode=1, stderr="no route"))
    discovery = OpenCodeModelDiscovery(runner=runner)

    catalog = discovery.discover()

    assert [entry.identity.key for entry in catalog.entries] == [
        "opencode-go/alpha-flash",
        "opencode-go/beta-flash",
    ]
    assert all(entry.availability is Availability.unknown for entry in catalog.entries)
    assert catalog.source == "opencode models"
    assert [call[0][-1] for call in runner.calls] == [
        "/api/model",
        "models",
        "/api/info",
    ]


def test_discovery_rejects_a_malformed_payload() -> None:
    runner = FakeRunner(
        route=completed("not json"),
        models=completed("not a pair", returncode=1),
    )
    discovery = OpenCodeModelDiscovery(runner=runner)

    with pytest.raises(DiscoveryError):
        discovery.discover()


def test_a_malformed_route_falls_back_to_the_model_list() -> None:
    runner = FakeRunner(route=completed(json.dumps({"data": [{"id": "x"}]})))
    discovery = OpenCodeModelDiscovery(runner=runner)

    catalog = discovery.discover()

    assert catalog.source == "opencode models"
    assert [entry.identity.key for entry in catalog.entries] == [
        "opencode-go/alpha-flash",
        "opencode-go/beta-flash",
    ]


def test_refresh_reports_added_and_removed() -> None:
    first = catalog_with("alpha-flash")
    second = catalog_with("alpha-flash", "beta-flash")
    refresher = CatalogRefresher(FakeDiscovery(first, second))
    refresher.catalog()

    catalog, diff = refresher.refresh()

    assert [identity.key for identity in diff.added] == ["opencode-go/beta-flash"]
    assert diff.removed == []
    assert catalog.catalog_version == second.catalog_version


def test_refresh_keeps_the_previous_catalog_on_failure() -> None:
    class Failing:
        def __init__(self) -> None:
            self.calls = 0

        def discover(self) -> ModelCatalog:
            self.calls += 1
            if self.calls == 1:
                return catalog_with("alpha-flash")
            raise DiscoveryError("the server is down")

    refresher = CatalogRefresher(Failing())
    first = refresher.catalog()

    catalog, diff = refresher.refresh()

    assert catalog is first
    assert diff.added == []
    assert refresher.last_error is not None
    assert "the server is down" in refresher.last_error


def test_a_failed_discovery_with_no_catalog_raises() -> None:
    class Failing:
        def discover(self) -> ModelCatalog:
            raise DiscoveryError("the server is down")

    refresher = CatalogRefresher(Failing())

    with pytest.raises(DiscoveryError):
        refresher.catalog()


def test_refresh_happens_after_the_interval() -> None:
    catalog = catalog_with("alpha-flash")
    now = utcnow()
    discovery = FakeDiscovery(catalog)
    refresher = CatalogRefresher(
        discovery,
        refresh_interval_seconds=900,
        now=lambda: now,
    )

    first = refresher.catalog()
    assert discovery.calls == 1

    same = refresher.catalog()
    assert same is first
    assert discovery.calls == 1

    now = now + timedelta(seconds=901)
    refresher.catalog()
    assert discovery.calls == 2


def test_a_forced_refresh_re_discovers() -> None:
    discovery = FakeDiscovery(catalog_with("alpha-flash"), catalog_with("beta-flash"))
    refresher = CatalogRefresher(discovery)

    refresher.catalog()
    catalog = refresher.catalog(force=True)

    assert discovery.calls == 2
    assert [entry.identity.model_id for entry in catalog.entries] == ["beta-flash"]

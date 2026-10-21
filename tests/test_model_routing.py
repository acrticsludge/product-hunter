from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from product_hunter.cli import models_command
from product_hunter.config import (
    CatalogSettings,
    RoutingConfig,
    load_config,
    load_routing_config,
)
from product_hunter.db import (
    connect,
    list_executions,
    list_inventory,
    migrate,
    record_catalog,
    record_execution,
    record_model_executions,
    record_selection,
)
from product_hunter.llm import build_generator_for_selection
from product_hunter.llm.base import (
    GenerationRequest,
    GenerationResponse,
    ModelCallFailed,
    ProviderError,
    TextGenerator,
)
from product_hunter.llm.catalog import (
    Availability,
    CatalogError,
    ModelCatalog,
    ModelIdentity,
    parse_model_catalog,
)
from product_hunter.llm.discovery import CatalogRefresher, DiscoveryError
from product_hunter.llm.routing import (
    MAX_FALLBACK_ATTEMPTS,
    FallbackGenerator,
    ModelExecution,
    ModelRole,
    ModelRouter,
    ModelSelection,
    NoModelAvailable,
    RouteCandidate,
    RoutingError,
    SelectionSource,
    UnknownRole,
)

CONFIG = load_config()
DEFAULT = ModelIdentity(provider_id=CONFIG.provider_id, model_id=CONFIG.model_id)


def stamp(day: int) -> datetime:
    """A moment in October 2026, so two catalogues can be ordered against
    each other without depending on the clock."""
    return datetime(2026, 10, day, tzinfo=UTC)


def fixture_catalog(
    *model_ids: str,
    cost: float = 0.0,
    costs: dict[str, float] | None = None,
    include_default: bool = True,
    discovered_at: datetime | None = None,
) -> ModelCatalog:
    """A catalogue holding the given model ids, plus the configured default.

    The default model is the most expensive one, so it never wins a pattern
    on price and the chain it terminates is easy to read.
    """
    ids = list(dict.fromkeys([*model_ids, DEFAULT.model_id] if include_default else model_ids))
    prices = costs or {}
    catalog = parse_model_catalog(
        json.dumps(
            {
                "data": [
                    {
                        "modelID": model_id,
                        "providerID": DEFAULT.provider_id,
                        "status": "active",
                        "enabled": True,
                        "cost": [
                            {
                                "input": 10.0
                                if model_id == DEFAULT.model_id
                                else prices.get(model_id, cost),
                                "output": 0.0,
                            }
                        ],
                    }
                    for model_id in ids
                ]
            }
        ),
        source="test",
    )
    if discovered_at is not None:
        catalog = catalog.model_copy(update={"discovered_at": discovered_at})
    return catalog


class FakeCatalogSource:
    """Hands out one catalogue, and counts the forced refreshes."""

    def __init__(self, catalog: ModelCatalog) -> None:
        self.catalog_value = catalog
        self.forced = 0

    def catalog(self, *, force: bool = False) -> ModelCatalog:
        if force:
            self.forced += 1
        return self.catalog_value


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


def routing_config(roles: dict[ModelRole, list[RouteCandidate]]) -> RoutingConfig:
    """A routing config that binds every role.

    The config refuses a role that is not bound, so the roles a test does
    not care about are bound to a provider that does not exist.
    """
    bound = {role: list(candidates) for role, candidates in roles.items()}
    for role in ModelRole:
        bound.setdefault(role, [pattern("other-provider/*")])
    return RoutingConfig(
        version=1,
        catalog=CatalogSettings(
            refresh_interval_seconds=900, discovery_timeout_seconds=30
        ),
        roles=bound,
    )


def router_for(
    roles: dict[ModelRole, list[RouteCandidate]],
    catalog: ModelCatalog,
    *,
    source: FakeCatalogSource | None = None,
) -> ModelRouter:
    return ModelRouter(
        routing_config(roles),
        default=DEFAULT,
        catalog_source=source if source is not None else FakeCatalogSource(catalog),
    )


def pattern(value: str) -> RouteCandidate:
    return RouteCandidate(kind="pattern", value=value)


def model(value: str) -> RouteCandidate:
    return RouteCandidate(kind="model", value=value)


class ScriptedGenerator:
    """A generator that answers with one response, or fails on demand."""

    name = "fake"

    def __init__(self, model_id: str, *, fails: bool = False) -> None:
        self.model_id = model_id
        self.fails = fails
        self.calls = 0

    def generate(self, request: GenerationRequest) -> GenerationResponse:
        self.calls += 1
        if self.fails:
            raise ProviderError(f"{self.model_id} is down")
        return GenerationResponse(
            text='{"candidates": []}',
            provider=DEFAULT.provider_id,
            model=self.model_id,
            extractor_version="phase1-0.3",
            input_chars=len(request.prompt),
            latency_ms=5,
        )

    def check(self) -> dict[str, object]:
        if self.fails:
            raise ProviderError(f"{self.model_id} is not in the registry")
        return {"ok": True}


def build_for(failing: set[str]):
    """A builder of a generator that fails for the models named."""

    def build(selection: ModelSelection) -> TextGenerator:
        return ScriptedGenerator(
            selection.identity.model_id,
            fails=selection.identity.model_id in failing,
        )

    return build


# The model the shipped role file must pick for a role: the cheapest flash
# model, except for the roles that ask for a deepseek model first. The
# extraction role asks for it first too, so it takes the cheapest deepseek
# match, which today is the model Phase 1 measured its evidence quality on.
EXPECTED_MODEL = {
    ModelRole.extraction: DEFAULT.model_id,
    ModelRole.classification: "alpha-flash",
    ModelRole.normalization: "alpha-flash",
    ModelRole.deduplication: "alpha-flash",
    ModelRole.entity_resolution: "alpha-flash",
    ModelRole.clustering: "alpha-flash",
    ModelRole.validation: "alpha-flash",
    ModelRole.investigation: DEFAULT.model_id,
    ModelRole.competitor_research: DEFAULT.model_id,
    ModelRole.counter_evidence: DEFAULT.model_id,
    ModelRole.scoring: DEFAULT.model_id,
    ModelRole.reporting: "alpha-flash",
    ModelRole.synthesis: DEFAULT.model_id,
}


def test_every_role_resolves() -> None:
    routing = load_routing_config()
    catalog = fixture_catalog("alpha-flash", "beta-flash")
    router = ModelRouter(
        routing,
        default=DEFAULT,
        catalog_source=FakeCatalogSource(catalog),
    )

    for role in ModelRole:
        selection = router.select(role)
        assert selection.role is role
        assert selection.identity.model_id == EXPECTED_MODEL[role]
        assert selection.selection_source is SelectionSource.pattern
        assert selection.catalog_version == catalog.catalog_version


def test_the_shipped_routing_config_names_no_model() -> None:
    routing = load_routing_config()

    assert routing.version == 1
    assert set(routing.roles) == set(ModelRole)
    for candidates in routing.roles.values():
        for candidate in candidates:
            assert candidate.kind == "pattern"


def test_the_extraction_chain_leads_with_the_deepseek_family() -> None:
    """The extraction role asks for the deepseek family before the flash ones."""
    routing = load_routing_config()

    chain = routing.roles[ModelRole.extraction]

    assert [candidate.value for candidate in chain] == [
        "opencode-go/deepseek*",
        "opencode-go/*-flash*",
        "opencode-go/*",
    ]


def test_a_routing_config_without_a_role_is_refused() -> None:
    """A role the operator deleted is a config error, not a silent downgrade."""
    roles = {
        role: [pattern(f"{DEFAULT.provider_id}/*")] for role in ModelRole
    }
    del roles[ModelRole.synthesis]

    with pytest.raises(ValueError) as excinfo:
        RoutingConfig(
            version=1,
            catalog=CatalogSettings(
                refresh_interval_seconds=900, discovery_timeout_seconds=30
            ),
            roles=roles,
        )

    assert "synthesis" in str(excinfo.value)


def test_a_malformed_routing_file_is_a_value_error(tmp_path) -> None:
    """A routing file the parser cannot read is a bad config, not a crash."""
    path = tmp_path / "model_routing.yaml"
    path.write_text("roles: [unclosed\n", encoding="utf-8")

    with pytest.raises(ValueError) as excinfo:
        load_routing_config(path)

    message = str(excinfo.value)
    assert "model_routing.yaml" in message
    assert "expected" in message


def test_a_malformed_extraction_file_is_a_value_error(tmp_path) -> None:
    """The extraction config loader refuses a broken file the same way."""
    path = tmp_path / "extraction.yaml"
    path.write_text("version: 1\nlimits: {max_documents: 0\n", encoding="utf-8")

    with pytest.raises(ValueError) as excinfo:
        load_config(path)

    message = str(excinfo.value)
    assert "extraction.yaml" in message
    assert "expected" in message


def test_a_generator_for_a_selection_is_bound_to_the_selected_model(monkeypatch) -> None:
    """The generator runs the model the router chose, not the pinned one."""
    catalog = fixture_catalog("alpha-flash")
    router = router_for(
        {ModelRole.extraction: [pattern(f"{DEFAULT.provider_id}/*-flash")]},
        catalog,
    )
    built: list[tuple[str, str]] = []

    def factory(config):
        built.append((config.provider_id, config.model_id))
        return object()

    monkeypatch.setattr("product_hunter.llm.build_generator", factory)
    selection = router.select(ModelRole.extraction)

    generator = build_generator_for_selection(CONFIG, selection)

    assert generator is not None
    assert selection.identity.model_id == "alpha-flash"
    assert built == [(DEFAULT.provider_id, "alpha-flash")]
    assert (CONFIG.provider_id, CONFIG.model_id) != built[0]


def test_an_unknown_role_is_refused() -> None:
    router = router_for(
        {ModelRole.extraction: [pattern(f"{DEFAULT.provider_id}/*")]},
        fixture_catalog("alpha-flash"),
    )

    with pytest.raises(UnknownRole):
        router.select("not-a-role")
    with pytest.raises(UnknownRole):
        router.chain("not-a-role")


def test_a_disabled_model_is_not_selected() -> None:
    catalog = parse_model_catalog(
        json.dumps(
            {
                "data": [
                    {
                        "modelID": "off-flash",
                        "providerID": DEFAULT.provider_id,
                        "status": "active",
                        "enabled": False,
                    }
                ]
            }
        ),
        source="test",
    )
    router = router_for(
        {ModelRole.extraction: [model(f"{DEFAULT.provider_id}/off-flash")]},
        catalog,
    )

    with pytest.raises(NoModelAvailable):
        router.select(ModelRole.extraction)


def test_a_retired_model_is_not_selected() -> None:
    catalog = parse_model_catalog(
        json.dumps(
            {
                "data": [
                    {
                        "modelID": "old-flash",
                        "providerID": DEFAULT.provider_id,
                        "status": "deprecated",
                        "enabled": True,
                    }
                ]
            }
        ),
        source="test",
    )
    router = router_for(
        {ModelRole.extraction: [model(f"{DEFAULT.provider_id}/old-flash")]},
        catalog,
    )

    with pytest.raises(NoModelAvailable):
        router.select(ModelRole.extraction)


def test_a_missing_model_does_not_raise() -> None:
    catalog = fixture_catalog("alpha-flash")
    router = router_for(
        {
            ModelRole.extraction: [
                model(f"{DEFAULT.provider_id}/vanished"),
                pattern(f"{DEFAULT.provider_id}/*-flash"),
            ]
        },
        catalog,
    )

    selection = router.select(ModelRole.extraction)

    assert selection.identity.model_id == "alpha-flash"
    assert selection.fallback_index == 0


def test_a_new_model_is_selected_without_a_code_change() -> None:
    roles = {ModelRole.extraction: [pattern(f"{DEFAULT.provider_id}/*-fast")]}
    routing = routing_config(roles)
    discovery = FakeDiscovery(
        fixture_catalog("alpha-fast", costs={"alpha-fast": 1.0}),
        fixture_catalog(
            "alpha-fast",
            "gamma-fast",
            costs={"alpha-fast": 1.0, "gamma-fast": 0.0},
        ),
    )
    refresher = CatalogRefresher(discovery)
    router = ModelRouter(routing, default=DEFAULT, catalog_source=refresher)

    first = router.select(ModelRole.extraction)
    assert first.identity.model_id == "alpha-fast"

    refresher.catalog(force=True)
    after = router.select(ModelRole.extraction)

    assert after.identity.model_id == "gamma-fast"


def test_fallback_moves_to_the_next_candidate() -> None:
    catalog = fixture_catalog("alpha-fast", "beta-fast")
    router = router_for(
        {ModelRole.extraction: [pattern(f"{DEFAULT.provider_id}/*-fast")]},
        catalog,
    )
    primary = router.select(ModelRole.extraction)

    fallback = router.select(
        ModelRole.extraction, exclude=[primary.identity]
    )

    assert fallback.identity.model_id == "beta-fast"
    assert fallback.fallback_index == 1
    assert fallback.is_fallback is True


def test_a_failed_call_falls_through_to_the_next_candidate() -> None:
    """A model that fails at call time is retried with the next candidate."""
    catalog = fixture_catalog("alpha-fast", "beta-fast")
    router = router_for(
        {ModelRole.extraction: [pattern(f"{DEFAULT.provider_id}/*-fast")]},
        catalog,
    )
    generator = FallbackGenerator(
        router, ModelRole.extraction, build_for({"alpha-fast"})
    )

    response = generator.generate(GenerationRequest(prompt="find the pain"))

    assert response.model == "beta-fast"
    assert [attempt.ok for attempt in generator.attempts] == [False, True]
    failed = generator.attempts[0]
    assert failed.selection.identity.model_id == "alpha-fast"
    assert failed.error is not None
    assert "alpha-fast is down" in failed.error
    # The call that answered is the fallback it was.
    assert generator.attempts[1].selection.fallback_index == 1
    assert generator.attempts[1].selection.is_fallback is True


def test_a_failed_candidate_is_not_tried_again() -> None:
    """A candidate that failed is excluded from the calls that follow."""
    catalog = fixture_catalog("alpha-fast", "beta-fast")
    router = router_for(
        {ModelRole.extraction: [pattern(f"{DEFAULT.provider_id}/*-fast")]},
        catalog,
    )
    generator = FallbackGenerator(
        router, ModelRole.extraction, build_for({"alpha-fast"})
    )

    generator.generate(GenerationRequest(prompt="first"))
    generator.generate(GenerationRequest(prompt="second"))

    assert [attempt.selection.identity.model_id for attempt in generator.attempts] == [
        "alpha-fast",
        "beta-fast",
        "beta-fast",
    ]


def test_the_number_of_candidates_one_call_may_try_is_bounded() -> None:
    """A provider that is down costs a timeout per attempt, so the chain ends."""
    catalog = fixture_catalog(
        "alpha-fast", "beta-fast", "gamma-fast", "delta-fast"
    )
    router = router_for(
        {ModelRole.extraction: [pattern(f"{DEFAULT.provider_id}/*-fast")]},
        catalog,
    )
    generator = FallbackGenerator(
        router, ModelRole.extraction, build_for({"alpha-fast", "beta-fast", "gamma-fast", "delta-fast", DEFAULT.model_id})
    )

    with pytest.raises(ProviderError):
        generator.generate(GenerationRequest(prompt="find the pain"))

    assert len(generator.attempts) == MAX_FALLBACK_ATTEMPTS


def test_the_exhausted_chain_raises_the_last_provider_error() -> None:
    """The reason the run stopped is the reason the last model gave."""
    catalog = fixture_catalog("alpha-fast", "beta-fast", include_default=False)
    router = router_for(
        {ModelRole.extraction: [pattern(f"{DEFAULT.provider_id}/*-fast")]},
        catalog,
    )
    generator = FallbackGenerator(
        router, ModelRole.extraction, build_for({"alpha-fast", "beta-fast"})
    )

    with pytest.raises(ProviderError) as excinfo:
        generator.generate(GenerationRequest(prompt="find the pain"))

    assert "beta-fast is down" in str(excinfo.value)


def test_the_exhausted_chain_names_the_model_that_failed() -> None:
    """The error names the candidate that failed, not the one the run began on."""
    catalog = fixture_catalog("alpha-fast", "beta-fast", include_default=False)
    router = router_for(
        {ModelRole.extraction: [pattern(f"{DEFAULT.provider_id}/*-fast")]},
        catalog,
    )
    generator = FallbackGenerator(
        router, ModelRole.extraction, build_for({"alpha-fast", "beta-fast"})
    )

    with pytest.raises(ModelCallFailed) as excinfo:
        generator.generate(GenerationRequest(prompt="find the pain"))

    error = excinfo.value
    assert error.model_id == "beta-fast"
    assert error.provider_id == DEFAULT.provider_id
    assert "beta-fast is down" in str(error)


def test_the_next_call_fails_with_the_reason_the_models_were_down() -> None:
    """The chain is exhausted for the whole run, and so is the reason."""
    catalog = fixture_catalog("alpha-fast", "beta-fast", include_default=False)
    router = router_for(
        {ModelRole.extraction: [pattern(f"{DEFAULT.provider_id}/*-fast")]},
        catalog,
    )
    generator = FallbackGenerator(
        router, ModelRole.extraction, build_for({"alpha-fast", "beta-fast"})
    )

    with pytest.raises(ProviderError) as first:
        generator.generate(GenerationRequest(prompt="first"))
    with pytest.raises(ProviderError) as second:
        generator.generate(GenerationRequest(prompt="second"))

    assert "beta-fast is down" in str(first.value)
    assert str(second.value) == str(first.value)
    # The reason the models were down, and not "no available model", which is
    # what the exhausted chain would say on its own.
    assert "no available model" not in str(second.value)


def test_a_candidate_refused_at_the_check_is_a_failed_attempt() -> None:
    """A refused candidate is recorded, so the run says what was skipped."""
    catalog = fixture_catalog("alpha-fast", "beta-fast")
    router = router_for(
        {ModelRole.extraction: [pattern(f"{DEFAULT.provider_id}/*-fast")]},
        catalog,
    )
    generator = FallbackGenerator(
        router, ModelRole.extraction, build_for({"alpha-fast"})
    )

    result = generator.check()

    assert result == {"ok": True}
    # The refused candidate is an attempt, and it is a failed one.
    assert [attempt.ok for attempt in generator.attempts] == [False]
    refused = generator.attempts[0]
    assert refused.selection.identity.model_id == "alpha-fast"
    assert refused.error is not None
    assert "alpha-fast is not in the registry" in refused.error


def test_a_candidate_that_passes_the_check_is_not_an_attempt() -> None:
    """A check that passes is not a generation, so it is not recorded."""
    catalog = fixture_catalog("alpha-fast", "beta-fast")
    router = router_for(
        {ModelRole.extraction: [pattern(f"{DEFAULT.provider_id}/*-fast")]},
        catalog,
    )
    generator = FallbackGenerator(router, ModelRole.extraction, build_for(set()))

    generator.check()

    assert generator.attempts == []


def test_check_falls_through_to_the_next_candidate() -> None:
    """A model the provider refuses is not the end of the run."""
    catalog = fixture_catalog("alpha-fast", "beta-fast")
    router = router_for(
        {ModelRole.extraction: [pattern(f"{DEFAULT.provider_id}/*-fast")]},
        catalog,
    )
    generator = FallbackGenerator(
        router, ModelRole.extraction, build_for({"alpha-fast"})
    )

    result = generator.check()

    assert result == {"ok": True}
    # And the refused model is not the one that answers the call.
    response = generator.generate(GenerationRequest(prompt="find the pain"))
    assert response.model == "beta-fast"


def test_the_cheapest_model_comes_first() -> None:
    catalog = fixture_catalog(
        "alpha-fast", "beta-fast", costs={"alpha-fast": 1.0, "beta-fast": 0.0}
    )
    router = router_for(
        {ModelRole.extraction: [pattern(f"{DEFAULT.provider_id}/*-fast")]},
        catalog,
    )

    chain = router.chain(ModelRole.extraction)

    assert [selection.identity.model_id for selection in chain] == [
        "beta-fast",
        "alpha-fast",
        DEFAULT.model_id,
    ]
    assert chain[0].selection_source is SelectionSource.pattern
    assert chain[-1].selection_source is SelectionSource.default


def test_the_chain_order_does_not_depend_on_the_server_order() -> None:
    def catalogue(provider_ids: tuple[str, ...]) -> ModelCatalog:
        return parse_model_catalog(
            json.dumps(
                {
                    "data": [
                        {
                            "modelID": "shared-flash",
                            "providerID": provider_id,
                            "status": "active",
                            "enabled": True,
                        }
                        for provider_id in provider_ids
                    ]
                }
            ),
            source="test",
        )

    zeta_first = router_for(
        {ModelRole.extraction: [pattern("*/shared-flash")]},
        catalogue(("zeta", "alpha")),
    )
    alpha_first = router_for(
        {ModelRole.extraction: [pattern("*/shared-flash")]},
        catalogue(("alpha", "zeta")),
    )

    assert [s.identity.key for s in zeta_first.chain(ModelRole.extraction)] == [
        s.identity.key for s in alpha_first.chain(ModelRole.extraction)
    ]
    assert [s.identity.key for s in zeta_first.chain(ModelRole.extraction)] == [
        "alpha/shared-flash",
        "zeta/shared-flash",
    ]


def test_the_default_terminates_the_chain() -> None:
    catalog = fixture_catalog("alpha-flash")
    router = router_for(
        {ModelRole.extraction: [pattern("other-provider/*")]},
        catalog,
    )

    chain = router.chain(ModelRole.extraction)

    assert len(chain) == 1
    assert chain[0].identity == DEFAULT
    assert chain[0].selection_source is SelectionSource.default


def test_an_empty_catalog_leaves_the_role_without_a_model() -> None:
    catalog = parse_model_catalog(json.dumps({"data": []}), source="test")
    router = router_for(
        {ModelRole.extraction: [pattern(f"{DEFAULT.provider_id}/*")]},
        catalog,
    )

    with pytest.raises(NoModelAvailable):
        router.select(ModelRole.extraction)


def test_a_role_that_matches_nothing_triggers_one_refresh() -> None:
    catalog = fixture_catalog("alpha-fast", include_default=False)
    source = FakeCatalogSource(catalog)
    router = router_for(
        {ModelRole.extraction: [pattern("other-provider/*")]},
        catalog,
        source=source,
    )

    with pytest.raises(NoModelAvailable):
        router.select(ModelRole.extraction)

    assert source.forced == 1


def test_a_routing_refusal_is_a_provider_error() -> None:
    assert issubclass(RoutingError, ProviderError)

    router = router_for(
        {ModelRole.extraction: [pattern("other-provider/*")]},
        fixture_catalog("alpha-flash", include_default=False),
    )

    caught: Exception | None = None
    try:
        router.select(ModelRole.extraction)
    except ProviderError as exc:
        caught = exc

    assert isinstance(caught, NoModelAvailable)


def test_a_discovery_failure_is_visible_in_the_refusal() -> None:
    class FailingSource:
        """A catalogue source whose discovery is broken."""

        def catalog(self, *, force: bool = False) -> ModelCatalog:
            raise CatalogError("the opencode server is down")

    router = ModelRouter(
        routing_config({ModelRole.extraction: [pattern(f"{DEFAULT.provider_id}/*")]}),
        default=DEFAULT,
        catalog_source=FailingSource(),
    )

    with pytest.raises(NoModelAvailable) as excinfo:
        router.select(ModelRole.extraction)

    message = str(excinfo.value)
    assert "discovery failed" in message
    assert "the opencode server is down" in message


def test_a_failed_discovery_is_not_discovered_again_by_the_router() -> None:
    class FailingSource:
        """A catalogue source whose discovery always fails, counting the calls."""

        def __init__(self) -> None:
            self.calls = 0

        def catalog(self, *, force: bool = False) -> ModelCatalog:
            self.calls += 1
            raise CatalogError("the opencode server is down")

    source = FailingSource()
    router = ModelRouter(
        routing_config({ModelRole.extraction: [pattern(f"{DEFAULT.provider_id}/*")]}),
        default=DEFAULT,
        catalog_source=source,
    )

    with pytest.raises(NoModelAvailable):
        router.select(ModelRole.extraction)

    assert source.calls == 1


def test_a_recovered_discovery_does_not_leave_the_old_failure_behind() -> None:
    class FlakySource:
        """A catalogue source whose discovery fails once, then works."""

        def __init__(self, catalog: ModelCatalog) -> None:
            self.catalog_value = catalog
            self.calls = 0

        def catalog(self, *, force: bool = False) -> ModelCatalog:
            self.calls += 1
            if self.calls == 1:
                raise CatalogError("the opencode server is down")
            return self.catalog_value

    router = ModelRouter(
        routing_config({ModelRole.extraction: [pattern("other-provider/*")]}),
        default=DEFAULT,
        catalog_source=FlakySource(
            fixture_catalog("alpha-flash", include_default=False)
        ),
    )

    with pytest.raises(NoModelAvailable) as broken:
        router.select(ModelRole.extraction)
    assert "discovery failed" in str(broken.value)

    with pytest.raises(NoModelAvailable) as recovered:
        router.select(ModelRole.extraction)

    message = str(recovered.value)
    assert "no available model" in message
    assert "discovery failed" not in message


def test_a_failed_discovery_is_not_retried_within_the_interval() -> None:
    class FailingDiscovery:
        """A discovery that always fails, counting the attempts."""

        def __init__(self) -> None:
            self.calls = 0

        def discover(self) -> ModelCatalog:
            self.calls += 1
            raise DiscoveryError("model discovery failed: the server is down")

    discovery = FailingDiscovery()
    refresher = CatalogRefresher(discovery)
    router = ModelRouter(
        routing_config({ModelRole.extraction: [pattern(f"{DEFAULT.provider_id}/*")]}),
        default=DEFAULT,
        catalog_source=refresher,
    )

    for _ in range(2):
        with pytest.raises(NoModelAvailable):
            router.select(ModelRole.extraction)

    assert discovery.calls == 1

    with pytest.raises(DiscoveryError):
        refresher.catalog(force=True)

    assert discovery.calls == 2


def test_the_inventory_keeps_a_vanished_model() -> None:
    conn = connect(":memory:")
    try:
        migrate(conn)
        first = fixture_catalog("alpha-flash", discovered_at=stamp(1))
        second = fixture_catalog(discovered_at=stamp(8))

        record_catalog(conn, first)
        record_catalog(conn, second)

        rows = list_inventory(conn)
        vanished = next(
            row for row in rows if row.model_id == "alpha-flash"
        )
        assert vanished.availability == "missing"
        # The stamps are the catalogue's own, not the moment of the import.
        assert vanished.retired_at == second.discovered_at.isoformat()
        assert vanished.first_seen_at == first.discovered_at.isoformat()
        assert vanished.last_seen_at == first.discovered_at.isoformat()
        # The model that stayed in the catalogue is alive, and it keeps the
        # day it was first seen.
        alive = next(row for row in rows if row.model_id == DEFAULT.model_id)
        assert alive.availability == "available"
        assert alive.retired_at is None
        assert alive.first_seen_at == first.discovered_at.isoformat()
        assert alive.last_seen_at == second.discovered_at.isoformat()
        assert len(rows) == 2
    finally:
        conn.close()


def test_an_older_catalogue_does_not_retire_a_newer_model() -> None:
    """A catalogue imported out of order knows nothing about a newer row."""
    conn = connect(":memory:")
    try:
        migrate(conn)
        newer = fixture_catalog("alpha-flash", discovered_at=stamp(8))
        older = fixture_catalog(discovered_at=stamp(1))

        record_catalog(conn, newer)
        record_catalog(conn, older)

        rows = {row.model_id: row for row in list_inventory(conn)}
        flash = rows["alpha-flash"]
        # The model the newer catalogue held is still available.
        assert flash.availability == "available"
        assert flash.retired_at is None
        # And its stamps were not moved backwards by the older catalogue.
        assert flash.first_seen_at == newer.discovered_at.isoformat()
        assert flash.last_seen_at == newer.discovered_at.isoformat()
    finally:
        conn.close()


def test_the_same_catalogue_twice_is_a_no_op() -> None:
    """The same catalogue imported twice retires nothing twice."""
    conn = connect(":memory:")
    try:
        migrate(conn)
        first = fixture_catalog("alpha-flash", discovered_at=stamp(1))
        second = fixture_catalog(discovered_at=stamp(8))

        record_catalog(conn, first)
        record_catalog(conn, second)
        record_catalog(conn, second)

        rows = {row.model_id: row for row in list_inventory(conn)}
        assert len(rows) == 2
        flash = rows["alpha-flash"]
        assert flash.availability == "missing"
        # The model is retired once, and the second import keeps that stamp.
        assert flash.retired_at == second.discovered_at.isoformat()
        assert flash.last_seen_at == first.discovered_at.isoformat()
        catalogs = conn.execute("SELECT count(*) FROM model_catalogs").fetchone()
        assert catalogs is not None
        assert catalogs[0] == 2
    finally:
        conn.close()


def test_the_catalogue_round_trips_through_json() -> None:
    """The catalogue a run records is the catalogue the import reads back."""
    catalog = fixture_catalog("alpha-flash")

    text = catalog.model_dump_json()

    assert ModelCatalog.model_validate_json(text) == catalog


def test_the_execution_row_carries_the_provenance() -> None:
    conn = connect(":memory:")
    try:
        migrate(conn)
        execution = ModelExecution(
            execution_id="exec-1",
            run_id="run-1",
            stage="extraction",
            role=ModelRole.extraction,
            provider_id=DEFAULT.provider_id,
            model_id="alpha-flash",
            selection_source=SelectionSource.pattern,
            fallback_index=1,
            is_fallback=True,
            catalog_version="sha256:abc",
            prompt_version="extract-v3",
            config_version="routing-v1-abc",
            latency_ms=1234,
            status="ok",
            error=None,
            created_at="2026-01-01T00:00:00+00:00",
        )

        record_execution(conn, execution)
        rows = list_executions(conn, run_id="run-1", role="extraction")

        assert len(rows) == 1
        row = rows[0]
        assert row.execution_id == "exec-1"
        assert row.run_id == "run-1"
        assert row.stage == "extraction"
        assert row.role == "extraction"
        assert row.provider_id == DEFAULT.provider_id
        assert row.model_id == "alpha-flash"
        assert row.selection_source == "pattern"
        assert row.fallback_index == 1
        assert row.is_fallback is True
        assert row.catalog_version == "sha256:abc"
        assert row.prompt_version == "extract-v3"
        assert row.config_version == "routing-v1-abc"
        assert row.latency_ms == 1234
        assert row.status == "ok"
        assert row.error is None
        assert row.created_at == "2026-01-01T00:00:00+00:00"
    finally:
        conn.close()


def test_the_package_exports_the_execution_writer() -> None:
    """The writer comes from the package, and it writes every call of a run."""
    conn = connect(":memory:")
    try:
        migrate(conn)
        executions = [
            ModelExecution(
                execution_id=f"exec-{number}",
                run_id="run-2",
                stage="extraction",
                role=ModelRole.extraction,
                provider_id=DEFAULT.provider_id,
                model_id="alpha-flash",
                selection_source=SelectionSource.pattern,
                fallback_index=0,
                is_fallback=False,
                catalog_version="sha256:abc",
                prompt_version="extract-v3",
                config_version="routing-v1-abc",
                latency_ms=number,
                status="ok",
                error=None,
                created_at="2026-01-01T00:00:00+00:00",
            )
            for number in (1, 2)
        ]

        assert record_model_executions(conn, executions) == 2
        rows = list_executions(conn, run_id="run-2")

        assert [row.execution_id for row in rows] == ["exec-1", "exec-2"]
        assert [row.model_id for row in rows] == ["alpha-flash", "alpha-flash"]
    finally:
        conn.close()


def test_a_long_error_is_cut_in_the_audit_row() -> None:
    conn = connect(":memory:")
    try:
        migrate(conn)
        execution = ModelExecution(
            execution_id="exec-long",
            run_id="run-1",
            stage="extraction",
            role=ModelRole.extraction,
            provider_id=DEFAULT.provider_id,
            model_id="alpha-flash",
            selection_source=SelectionSource.pattern,
            fallback_index=0,
            is_fallback=False,
            catalog_version="sha256:abc",
            prompt_version="extract-v3",
            config_version="routing-v1-abc",
            latency_ms=42,
            status="error",
            error="the provider exploded: " + "stderr " * 2000,
            created_at="2026-01-01T00:00:00+00:00",
        )

        record_execution(conn, execution)
        rows = list_executions(conn, run_id="run-1")

        assert len(rows) == 1
        stored = rows[0].error
        assert stored is not None
        assert stored.startswith("the provider exploded: ")
        assert len(stored) == 2000
        assert len(execution.error) > 2000
    finally:
        conn.close()


def test_the_selection_is_persisted() -> None:
    conn = connect(":memory:")
    try:
        migrate(conn)
        catalog = fixture_catalog("alpha-flash")
        router = router_for(
            {ModelRole.extraction: [pattern(f"{DEFAULT.provider_id}/*-flash")]},
            catalog,
        )
        selection = router.select(ModelRole.extraction)

        record_selection(
            conn, selection, config_version="routing-v1-abc"
        )
        row = conn.execute(
            "SELECT * FROM model_role_bindings WHERE role = ? AND position = ?",
            ("extraction", 0),
        ).fetchone()

        assert row is not None
        assert row["provider_id"] == DEFAULT.provider_id
        assert row["model_id"] == "alpha-flash"
        assert row["selection_source"] == "pattern"
        assert row["catalog_version"] == catalog.catalog_version
        assert row["config_version"] == "routing-v1-abc"
        assert row["selected_at"] is not None
    finally:
        conn.close()


def test_a_write_function_commits_only_the_transaction_it_owns() -> None:
    """A write inside a caller's transaction leaves that transaction open."""
    conn = connect(":memory:")
    try:
        migrate(conn)
        catalog = fixture_catalog("alpha-flash")
        router = router_for(
            {ModelRole.extraction: [pattern(f"{DEFAULT.provider_id}/*-flash")]},
            catalog,
        )
        selection = router.select(ModelRole.extraction)
        execution = ModelExecution(
            execution_id="exec-1",
            run_id="run-1",
            stage="extraction",
            role=ModelRole.extraction,
            provider_id=DEFAULT.provider_id,
            model_id="alpha-flash",
            selection_source=SelectionSource.pattern,
            fallback_index=0,
            is_fallback=False,
            catalog_version=catalog.catalog_version,
            prompt_version="extract-v3",
            config_version="routing-v1-abc",
            latency_ms=1,
            status="ok",
            error=None,
            created_at="2026-01-01T00:00:00+00:00",
        )

        # A free connection: the write owns the transaction and commits it.
        record_catalog(conn, catalog)
        assert conn.in_transaction is False
        record_selection(conn, selection, config_version="routing-v1-abc")
        assert conn.in_transaction is False
        record_execution(conn, execution)
        assert conn.in_transaction is False
        record_model_executions(conn, [execution])
        assert conn.in_transaction is False

        # A busy connection: the write joins the transaction and leaves it
        # to the caller.
        conn.execute("BEGIN IMMEDIATE")
        record_catalog(conn, catalog)
        record_selection(conn, selection, config_version="routing-v1-abc")
        record_execution(conn, execution)
        record_model_executions(conn, [execution])
        assert conn.in_transaction is True
        conn.execute("COMMIT")
        assert conn.in_transaction is False
    finally:
        conn.close()


def test_the_models_command_prints_the_catalog(capsys) -> None:
    catalog = fixture_catalog("alpha-flash")

    code = models_command(
        config=CONFIG,
        routing_config=load_routing_config(),
        discovery=FakeDiscovery(catalog),
    )

    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload["ok"] is True
    assert payload["catalog_version"] == catalog.catalog_version
    assert payload["model_count"] == len(catalog.entries)
    assert payload["models"][0]["provider_id"] == DEFAULT.provider_id


def test_the_models_command_prints_the_release_time(capsys) -> None:
    catalog = parse_model_catalog(
        json.dumps(
            {
                "data": [
                    {
                        "modelID": "alpha-flash",
                        "providerID": DEFAULT.provider_id,
                        "status": "active",
                        "enabled": True,
                        "time": {"released": 1767225600000},
                    }
                ]
            }
        ),
        source="test",
    )

    code = models_command(
        config=CONFIG,
        routing_config=load_routing_config(),
        discovery=FakeDiscovery(catalog),
    )

    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload["models"][0]["released_at"] == "2026-01-01T00:00:00+00:00"


def test_the_models_command_prints_the_role_chain(capsys) -> None:
    catalog = fixture_catalog("alpha-flash")

    code = models_command(
        role="extraction",
        config=CONFIG,
        routing_config=load_routing_config(),
        discovery=FakeDiscovery(catalog),
    )

    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload["role"] == "extraction"
    # The extraction chain leads with the deepseek family, so the model the
    # shipped file picks is the cheapest deepseek match, not the cheapest
    # flash.
    assert payload["selected"]["model_id"] == DEFAULT.model_id
    assert payload["selected"]["selection_source"] == "pattern"
    assert payload["chain"][0]["model_id"] == DEFAULT.model_id


def test_the_models_command_prints_the_server_version_for_a_role(capsys) -> None:
    """The role payload carries the server version the catalogue reported."""
    catalog = parse_model_catalog(
        json.dumps(
            {
                "data": [
                    {
                        "modelID": "alpha-flash",
                        "providerID": DEFAULT.provider_id,
                        "status": "active",
                        "enabled": True,
                    }
                ]
            }
        ),
        source="test",
        server_version="2.1.2",
    )

    code = models_command(
        role="extraction",
        config=CONFIG,
        routing_config=load_routing_config(),
        discovery=FakeDiscovery(catalog),
    )

    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload["server_version"] == "2.1.2"
    assert payload["catalog_version"] == catalog.catalog_version


def test_the_models_command_refuses_an_unknown_role(capsys) -> None:
    code = models_command(
        role="not-a-role",
        config=CONFIG,
        routing_config=load_routing_config(),
        discovery=FakeDiscovery(fixture_catalog("alpha-flash")),
    )

    payload = json.loads(capsys.readouterr().out)
    assert code == 1
    assert payload["ok"] is False
    assert "not-a-role" in payload["error"]


def test_a_model_that_is_not_available_is_never_in_a_chain() -> None:
    catalog = parse_model_catalog(
        json.dumps(
            {
                "data": [
                    {
                        "modelID": "off-flash",
                        "providerID": DEFAULT.provider_id,
                        "status": "active",
                        "enabled": False,
                    }
                ]
            }
        ),
        source="test",
    )
    router = router_for(
        {ModelRole.extraction: [pattern(f"{DEFAULT.provider_id}/*-flash")]},
        catalog,
    )

    assert router.chain(ModelRole.extraction) == []
    assert catalog.entries[0].availability is Availability.disabled

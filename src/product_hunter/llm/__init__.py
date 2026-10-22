from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from product_hunter.llm.base import ProviderUnavailable, TextGenerator
from product_hunter.llm.catalog import ModelIdentity
from product_hunter.llm.decision import (
    DecisionProvider,
    DecisionRouter,
    TextDecisionProvider,
)
from product_hunter.llm.discovery import (
    CatalogRefresher,
    ModelDiscovery,
    OpenCodeModelDiscovery,
)
from product_hunter.llm.opencode import OpenCodeGenerateProvider
from product_hunter.llm.routing import ModelRole, ModelRouter, ModelSelection

if TYPE_CHECKING:
    from product_hunter.config import (
        DecisionRoutingConfig,
        ExtractionConfig,
        RoutingConfig,
    )
    from product_hunter.llm.subq import SubQConfig

_PROVIDERS = {
    "opencode_generate": OpenCodeGenerateProvider,
}

# The decision providers the chain may name.
_DECISION_PROVIDERS = ("subq", "text")


def build_generator(config: ExtractionConfig) -> TextGenerator:
    """Return the text generator for config.provider. Phase 2 extends this map."""
    try:
        builder = _PROVIDERS[config.provider]
    except KeyError:
        raise ProviderUnavailable(
            f"unsupported provider {config.provider!r}; "
            f"available: {', '.join(sorted(_PROVIDERS))}"
        ) from None
    return builder(config)


def build_router(
    routing: RoutingConfig,
    extraction: ExtractionConfig,
    *,
    discovery: ModelDiscovery | None = None,
) -> ModelRouter:
    """Return the router for the configured roles, over the live catalogue.

    The default model of the extraction config terminates every chain, and the
    catalogue is re-discovered when it ages past the configured interval.
    """
    source = (
        discovery
        if discovery is not None
        else OpenCodeModelDiscovery(
            timeout_seconds=routing.discovery_timeout_seconds
        )
    )
    refresher = CatalogRefresher(
        source, refresh_interval_seconds=routing.refresh_interval_seconds
    )
    default = ModelIdentity(
        provider_id=extraction.provider_id, model_id=extraction.model_id
    )
    return ModelRouter(routing, default=default, catalog_source=refresher)


def build_generator_for_selection(
    config: ExtractionConfig, selection: ModelSelection
) -> TextGenerator:
    """The generator that runs the model a selection names.

    The model is bound to the generator by copying the extraction config with
    the selected provider and model, so the provider runs the model the
    router chose.
    """
    bound = config.model_copy(
        update={
            "provider_id": selection.identity.provider_id,
            "model_id": selection.identity.model_id,
        }
    )
    return build_generator(bound)


def build_decision_router(
    config: DecisionRoutingConfig,
    *,
    subq: SubQConfig | None = None,
    generator: TextGenerator | None = None,
    generator_for_role: Callable[[ModelRole], TextGenerator] | None = None,
) -> DecisionRouter:
    """Return the decision router for the configured chain.

    The text provider is the fallback, so it needs a generator. The caller
    either hands one in for every text entry, or hands in a resolver that
    builds one for the model role the entry names, through the model routing
    that already exists. The SubQ provider needs no credential to be built:
    without one it reports itself disabled and the chain moves on, and the
    module is imported only when the chain actually names it.
    """
    providers: list[DecisionProvider] = []
    for entry in config.providers:
        if not entry.enabled:
            continue
        if entry.name == "subq":
            from product_hunter.llm.subq import SubQConfig, SubQProvider

            providers.append(
                SubQProvider(subq if subq is not None else SubQConfig())
            )
        elif entry.name == "text":
            chosen = generator
            if chosen is None and generator_for_role is not None:
                if entry.model_role is None:
                    raise ProviderUnavailable(
                        "the text decision provider needs a model_role to "
                        "resolve a generator"
                    )
                chosen = generator_for_role(entry.model_role)
            if chosen is None:
                raise ProviderUnavailable(
                    "the text decision provider needs a text generator"
                )
            providers.append(
                TextDecisionProvider(
                    chosen,
                    role=entry.model_role.value if entry.model_role else None,
                )
            )
        else:
            raise ProviderUnavailable(
                f"unsupported decision provider {entry.name!r}; "
                f"available: {', '.join(sorted(_DECISION_PROVIDERS))}"
            )
    return DecisionRouter(providers)

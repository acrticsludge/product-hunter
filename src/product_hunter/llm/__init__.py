from __future__ import annotations

from typing import TYPE_CHECKING

from product_hunter.llm.base import ProviderUnavailable, TextGenerator
from product_hunter.llm.catalog import ModelIdentity
from product_hunter.llm.discovery import (
    CatalogRefresher,
    ModelDiscovery,
    OpenCodeModelDiscovery,
)
from product_hunter.llm.opencode import OpenCodeGenerateProvider
from product_hunter.llm.routing import ModelRouter, ModelSelection

if TYPE_CHECKING:
    from product_hunter.config import ExtractionConfig, RoutingConfig

_PROVIDERS = {
    "opencode_generate": OpenCodeGenerateProvider,
}


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

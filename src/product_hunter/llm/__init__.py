from __future__ import annotations

from product_hunter.config import ExtractionConfig
from product_hunter.llm.base import ProviderUnavailable, TextGenerator
from product_hunter.llm.opencode import OpenCodeGenerateProvider

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

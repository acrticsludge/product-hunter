"""The adapter layer: the tools that take a page, and the policy around them.

The four adapters differ in one thing: how the bytes are taken. Everything
else is the same for all of them, and it lives in ``base.py`` so it cannot
drift between them.

The result, the policy and the gate are in ``base``. The adapters and
their drivers are in ``adapters``. The rung table and the order the tools
are tried in are in ``routing``.
"""

from product_hunter.collection.adapters.adapters import (
    ApifyAdapter,
    ApifyDriver,
    Crawl4aiAdapter,
    Crawl4aiDriver,
    HttpAdapter,
    PlaywrightAdapter,
    PlaywrightDriver,
    apify_credentials,
    apify_driver,
    available_drivers,
    build_adapters,
    crawl4ai_driver,
    playwright_driver,
)
from product_hunter.collection.adapters.base import (
    MAX_RESOURCES,
    POLICY_REASON_CODES,
    AdapterBase,
    Attempt,
    Capture,
    CollectionResult,
    CollectPolicy,
    CollectRequest,
    Driver,
    ToolEmptyResult,
    ToolError,
    ToolHttpError,
    ToolNetworkError,
    ToolTimeout,
    ToolUnavailable,
    UrlGate,
)
from product_hunter.collection.adapters.routing import (
    RUNG_DEPENDENCIES,
    RUNG_TOOLS,
    LadderExhausted,
    dependency_for,
    rung_order,
    summarize,
    tool_for,
)

__all__ = [
    "MAX_RESOURCES",
    "POLICY_REASON_CODES",
    "RUNG_DEPENDENCIES",
    "RUNG_TOOLS",
    "AdapterBase",
    "ApifyAdapter",
    "ApifyDriver",
    "Attempt",
    "Capture",
    "CollectPolicy",
    "CollectRequest",
    "CollectionResult",
    "Crawl4aiAdapter",
    "Crawl4aiDriver",
    "Driver",
    "HttpAdapter",
    "LadderExhausted",
    "PlaywrightAdapter",
    "PlaywrightDriver",
    "ToolEmptyResult",
    "ToolError",
    "ToolHttpError",
    "ToolNetworkError",
    "ToolTimeout",
    "ToolUnavailable",
    "UrlGate",
    "apify_credentials",
    "apify_driver",
    "available_drivers",
    "build_adapters",
    "crawl4ai_driver",
    "dependency_for",
    "playwright_driver",
    "rung_order",
    "summarize",
    "tool_for",
]

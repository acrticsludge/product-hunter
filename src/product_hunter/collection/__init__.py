"""The source collection layer: targets in, raw captures with provenance out.

Collection turns a list of targets into raw captures with provenance. It
fetches what it is told to fetch, under the source policy, and records what
came back. It holds no research reasoning: no scoring, no clustering, no
judging of whether a page is worth reading.

The shared fetch path is the only way to make a plain HTTP request, so the
terms gate, the allowed-use gate, the domain allowlist, the rate limits,
the SSRF protection and robots are enforced there and are not repeated
here. The tools that are not plain HTTP requests hold the same policy
around themselves in ``collection.adapters``.
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
    available_drivers,
    build_adapters,
)
from product_hunter.collection.adapters.base import (
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
    tool_for,
)
from product_hunter.collection.ladder import (
    Collector,
    CollectorUnavailable,
    Ladder,
)
from product_hunter.collection.orchestrator import (
    COLLECT_STAGE,
    FAILED_REASONS,
    STAGE,
    TERMINAL_REASONS,
    CollectionOrchestrator,
    CollectionOutcome,
    CollectionPlan,
    CollectionPlanner,
    CollectionTarget,
    PlanRefusal,
    enqueue_collection_jobs,
    make_collection_handler,
)
from product_hunter.collection.state import (
    CollectionItem,
    CollectionState,
    RunState,
    SourceHealth,
    SourceState,
    source_health,
)

__all__ = [
    "COLLECT_STAGE",
    "FAILED_REASONS",
    "RUNG_DEPENDENCIES",
    "RUNG_TOOLS",
    "STAGE",
    "TERMINAL_REASONS",
    "ApifyAdapter",
    "ApifyDriver",
    "Attempt",
    "Capture",
    "CollectPolicy",
    "CollectRequest",
    "CollectionItem",
    "CollectionOrchestrator",
    "CollectionOutcome",
    "CollectionPlan",
    "CollectionPlanner",
    "CollectionResult",
    "CollectionState",
    "CollectionTarget",
    "Collector",
    "CollectorUnavailable",
    "Crawl4aiAdapter",
    "Crawl4aiDriver",
    "Driver",
    "HttpAdapter",
    "Ladder",
    "LadderExhausted",
    "PlanRefusal",
    "PlaywrightAdapter",
    "PlaywrightDriver",
    "RunState",
    "SourceHealth",
    "SourceState",
    "ToolEmptyResult",
    "ToolError",
    "ToolHttpError",
    "ToolNetworkError",
    "ToolTimeout",
    "ToolUnavailable",
    "UrlGate",
    "apify_credentials",
    "available_drivers",
    "build_adapters",
    "dependency_for",
    "enqueue_collection_jobs",
    "make_collection_handler",
    "rung_order",
    "source_health",
    "tool_for",
]

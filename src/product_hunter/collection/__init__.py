"""The source collection layer: targets in, raw captures with provenance out.

Collection turns a list of targets into raw captures with provenance. It
fetches what it is told to fetch, under the source policy, and records what
came back. It holds no research reasoning: no scoring, no clustering, no
judging of whether a page is worth reading.

The shared fetch path is the only way to the network, so the terms gate, the
allowed-use gate, the domain allowlist, the rate limits, the SSRF protection
and robots are enforced there and are not repeated here.
"""

from product_hunter.collection.ladder import (
    Collector,
    CollectorUnavailable,
    HttpCollector,
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
    "STAGE",
    "TERMINAL_REASONS",
    "CollectionItem",
    "CollectionOrchestrator",
    "CollectionOutcome",
    "CollectionPlan",
    "CollectionPlanner",
    "CollectionState",
    "CollectionTarget",
    "Collector",
    "CollectorUnavailable",
    "HttpCollector",
    "Ladder",
    "PlanRefusal",
    "RunState",
    "SourceHealth",
    "SourceState",
    "enqueue_collection_jobs",
    "make_collection_handler",
    "source_health",
]

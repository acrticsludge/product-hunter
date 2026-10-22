"""The discovery engine: the code that decides what to search.

Discovery proposes queries and sources. It does not fetch them, does not
admit a source, and does not call a search endpoint: the collection engine
runs the searches this one proposes.

The strategy lives in this package and in no other place. The query
families, the templates and the marker table are module constants, so the
same objective produces the same frontier in every run. The saturation
thresholds are the policy's own defaults rather than values read at
runtime, so a caller that supplies a policy decides when a run stops.

Import the package for the public surface: the families, the query type,
the record, the budget, the frontier and the persistence helpers.
"""

from __future__ import annotations

from product_hunter.discovery.budget import (
    DISCOVERY_STAGE,
    DiscoveryBudget,
)
from product_hunter.discovery.families import (
    DEFAULT_FAMILY,
    DERIVED_FAMILIES,
    FAMILY_MARKERS,
    FAMILY_SPECS,
    MANDATORY_FAMILIES,
    FamilySpec,
    QueryFamily,
    assign_family,
)
from product_hunter.discovery.frontier import (
    REASON_BUDGET_EXHAUSTED,
    REASON_DIMINISHING_NOVELTY,
    REASON_FAMILY_COVERAGE,
    REASON_REPEATED_RESULTS,
    REASON_SOURCE_COVERAGE,
    QueryFrontier,
    SaturationPolicy,
    SaturationVerdict,
)
from product_hunter.discovery.queries import (
    CandidateSignals,
    Query,
    SourceCandidate,
    generate_derived_queries,
    generate_queries,
    is_duplicate,
    normalize_query,
    query_id,
)
from product_hunter.discovery.records import (
    DiscoveryRecord,
    DiscoveryStatus,
    record_id,
)
from product_hunter.discovery.store import (
    EVENT_TYPE,
    list_records,
    load_frontier,
    save_frontier,
    write_record,
)

__all__ = [
    "DEFAULT_FAMILY",
    "DERIVED_FAMILIES",
    "DISCOVERY_STAGE",
    "EVENT_TYPE",
    "FAMILY_MARKERS",
    "FAMILY_SPECS",
    "MANDATORY_FAMILIES",
    "REASON_BUDGET_EXHAUSTED",
    "REASON_DIMINISHING_NOVELTY",
    "REASON_FAMILY_COVERAGE",
    "REASON_REPEATED_RESULTS",
    "REASON_SOURCE_COVERAGE",
    "CandidateSignals",
    "DiscoveryBudget",
    "DiscoveryRecord",
    "DiscoveryStatus",
    "FamilySpec",
    "Query",
    "QueryFamily",
    "QueryFrontier",
    "SaturationPolicy",
    "SaturationVerdict",
    "SourceCandidate",
    "assign_family",
    "generate_derived_queries",
    "generate_queries",
    "is_duplicate",
    "list_records",
    "load_frontier",
    "normalize_query",
    "query_id",
    "record_id",
    "save_frontier",
    "write_record",
]

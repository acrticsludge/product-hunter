"""The named analytical queries: one function per question the mission names.

Every function reads a view and returns a list of dictionaries, ordered so
two calls on the same data return the same list. The functions and the
dispatcher share one table of SQL, so a query asked for by name and a query
asked for by function answer the same thing.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, NamedTuple

if TYPE_CHECKING:  # the import would close a cycle at runtime
    from product_hunter.analytics.ingest import Analytics


class QuerySpec(NamedTuple):
    """A named query: the view it reads and the SQL it runs."""

    view: str
    sql: str


QUERIES: dict[str, QuerySpec] = {
    "growing_pain_points": QuerySpec(
        view="v_pain_point_trend",
        sql="""
        WITH bounds AS (
            SELECT date_trunc('month', MAX(month)) - ($months - 1) * INTERVAL 1 MONTH
                AS first_month
            FROM v_pain_point_trend
        )
        SELECT t.normalized_key,
               SUM(t.mention_count) AS mentions,
               SUM(t.mention_change) AS mention_change,
               MAX(t.distinct_authors) AS distinct_authors,
               MAX(t.distinct_communities) AS distinct_communities,
               COUNT(*) AS months_observed
        FROM v_pain_point_trend t
        CROSS JOIN bounds b
        WHERE t.month >= b.first_month
        GROUP BY t.normalized_key
        HAVING SUM(t.mention_change) > 0
        ORDER BY mention_change DESC, t.normalized_key
        LIMIT $limit
        """,
    ),
    "independent_evidence_communities": QuerySpec(
        view="v_community_evidence",
        sql="""
        SELECT community_id, community_name, source_id, mention_count,
               canonical_documents, distinct_authors, independent_authors,
               uncertain_authors, distinct_sources, first_sighting, last_sighting
        FROM v_community_evidence
        ORDER BY independent_authors DESC, mention_count DESC, community_id
        LIMIT $limit
        """,
    ),
    "source_useful_yield": QuerySpec(
        view="v_source_yield",
        sql="""
        SELECT source_id, source_type, fetch_count, document_count, evidence_count,
               accepted_pain_points, useful_evidence_yield, distinct_authors,
               distinct_communities, first_sighting, last_sighting
        FROM v_source_yield
        ORDER BY useful_evidence_yield DESC, evidence_count DESC, source_id
        LIMIT $limit
        """,
    ),
    "saturated_query_families": QuerySpec(
        view="v_query_family_performance",
        sql="""
        SELECT family, searches, results, useful, mean_novelty,
               mean_duplicate_rate, mean_useful_count, total_cost,
               useful_per_dollar, saturated
        FROM v_query_family_performance
        WHERE saturated
        ORDER BY mean_novelty, family
        LIMIT $limit
        """,
    ),
    "opportunity_movement": QuerySpec(
        view="v_opportunity_evidence_trend",
        sql="""
        WITH bounds AS (
            SELECT date_trunc('month', MAX(month)) - ($months - 1) * INTERVAL 1 MONTH
                AS first_month
            FROM v_opportunity_evidence_trend
        )
        SELECT t.cluster_id,
               SUM(t.mention_count) AS mentions,
               SUM(t.mention_change) AS mention_change,
               COUNT(*) AS months_observed
        FROM v_opportunity_evidence_trend t
        CROSS JOIN bounds b
        WHERE t.month >= b.first_month
        GROUP BY t.cluster_id
        ORDER BY mention_change DESC, t.cluster_id
        LIMIT $limit
        """,
    ),
    "concentration_hotspots": QuerySpec(
        view="v_source_concentration",
        sql="""
        SELECT normalized_key, source_count, largest_source_share, top_three_share,
               herfindahl_index, concentrated, top_source_id
        FROM v_source_concentration
        WHERE concentrated
        ORDER BY largest_source_share DESC, normalized_key
        LIMIT $limit
        """,
    ),
    "run_budget_consumers": QuerySpec(
        view="v_run_budget",
        sql="""
        SELECT run_id, MAX(run_units) AS units, MAX(run_cost_micros) AS cost_micros,
               MAX(total_cost_micros) AS total_cost_micros,
               MAX(cost_share) AS cost_share
        FROM v_run_budget
        GROUP BY run_id
        ORDER BY cost_micros DESC, run_id
        LIMIT $limit
        """,
    ),
}


def growing_pain_points(
    store: "Analytics", *, months: int = 3, limit: int = 50
) -> list[dict]:
    """Which pain points are growing over the last months."""
    return store.query("growing_pain_points", months=months, limit=limit)


def independent_evidence_communities(
    store: "Analytics", *, limit: int = 50
) -> list[dict]:
    """Which communities produce the most independent evidence."""
    return store.query("independent_evidence_communities", limit=limit)


def source_useful_yield(store: "Analytics", *, limit: int = 50) -> list[dict]:
    """Which sources have the highest useful-evidence yield."""
    return store.query("source_useful_yield", limit=limit)


def saturated_query_families(store: "Analytics", *, limit: int = 50) -> list[dict]:
    """Which query families are saturated."""
    return store.query("saturated_query_families", limit=limit)


def opportunity_movement(
    store: "Analytics", *, months: int = 3, limit: int = 50
) -> list[dict]:
    """Which opportunities are gaining or losing evidence."""
    return store.query("opportunity_movement", months=months, limit=limit)


def concentration_hotspots(store: "Analytics", *, limit: int = 50) -> list[dict]:
    """Where is the evidence overly concentrated."""
    return store.query("concentration_hotspots", limit=limit)


def run_budget_consumers(store: "Analytics", *, limit: int = 50) -> list[dict]:
    """Which research runs consume the most budget."""
    return store.query("run_budget_consumers", limit=limit)

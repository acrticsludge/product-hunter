"""The analytical views: the questions the derived state answers.

Every view is plain SQL over the mirrored tables, and every view names the
columns it selects, so the extra hash column of a mirrored table never
leaks into an answer. The views are created by the ingestion and dropped and
recreated by a rebuild.

DuckDB binds a view when it is created, so a view over a table that is not
there cannot be made at all. A view is therefore created only when every
table it names is present, and a view that is skipped is reported by name
rather than faked with an empty result.
"""

from __future__ import annotations

from dataclasses import dataclass

import duckdb

from product_hunter.collection.state import COMPLETED_OUTCOMES, FAILURE_OUTCOMES
from product_hunter.discovery.frontier import SaturationPolicy
from product_hunter.discovery.store import EVENT_TYPE
from product_hunter.research.evidence import ToolCallStatus

# The bands the discovery frontier policy uses, read from its defaults so the
# view and the policy cannot drift apart.
_POLICY = SaturationPolicy()
SATURATION_MIN_NOVELTY = _POLICY.min_novelty
SATURATION_MAX_DUPLICATE_RATE = _POLICY.max_duplicate_rate

# The persistence classes are this layer's own classification of how long a
# pain point has been seen. Nothing else in the repository reads them, and
# they are not a policy of the product.
PERSISTENCE_SINGLE = "single_sighting"
PERSISTENCE_EMERGING = "emerging"
PERSISTENCE_PERSISTENT = "persistent"
PERSISTENCE_DORMANT = "dormant"

# A pain point seen in one month is a single sighting. One seen in three
# months or more has persisted. One not seen for six months of the data is
# dormant.
PERSISTENT_FROM_MONTHS = 3
DORMANT_AFTER_MONTHS = 6

# The tables the evidence views read. They are named once, because a view
# over the evidence is only created when all of them are there.
_EVIDENCE_TABLES = (
    "authors",
    "author_links",
    "communities",
    "documents",
    "evidence",
    "pain_points",
    "sources",
)

_COMPLETED = ", ".join(f"'{outcome}'" for outcome in COMPLETED_OUTCOMES)
_FAILED = ", ".join(f"'{outcome}'" for outcome in FAILURE_OUTCOMES)
_SUCCESS_STATUS = ToolCallStatus.success.value


@dataclass(frozen=True)
class ViewReport:
    """What happened to one view.

    A view that was skipped carries the reason: the table it needs is not
    there.
    """

    name: str
    created: bool
    reason: str = ""


# Every view, in the order they are created. A view over another view comes
# after the view it reads.
VIEW_DDL: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    (
        "v_best_author",
        """
        CREATE OR REPLACE VIEW v_best_author AS
        SELECT doc_id, author_id
        FROM (
            SELECT doc_id, author_id,
                   row_number() OVER (PARTITION BY doc_id
                                      ORDER BY confidence DESC, link_id ASC) AS link_rank
            FROM author_links
        )
        WHERE link_rank = 1
        """,
        ("author_links",),
    ),
    (
        "v_evidence_mentions",
        """
        CREATE OR REPLACE VIEW v_evidence_mentions AS
        SELECT e.span_id, e.doc_id, e.quote,
               e.live_status AS span_live_status,
               e.strength, e.strength_score, e.reliability, e.reliability_score,
               e.observation_count, e.created_at,
               d.source_id, d.community_id, d.published_at, d.text_sha256,
               d.live_status AS document_live_status,
               c.name AS community_name,
               a.author_id, a.identity_confidence, a.deletion_status,
               s.source_type, s.terms_status,
               p.pain_id, p.normalized_key, p.domain, p.actor, p.action,
               p."object", p.consequence, p.severity, p.frequency, p.accepted
        FROM evidence e
        JOIN documents d ON d.doc_id = e.doc_id
        LEFT JOIN communities c ON c.community_id = d.community_id
        LEFT JOIN v_best_author ba ON ba.doc_id = d.doc_id
        LEFT JOIN authors a ON a.author_id = ba.author_id
        LEFT JOIN pain_points p ON p.span_id = e.span_id
        LEFT JOIN sources s ON s.source_id = d.source_id
        """,
        _EVIDENCE_TABLES,
    ),
    (
        "v_pain_point_evidence",
        """
        CREATE OR REPLACE VIEW v_pain_point_evidence AS
        SELECT m.pain_id, m.normalized_key, m.domain, m.severity, m.frequency,
               m.accepted,
               COUNT(*) AS mention_count,
               COUNT(DISTINCT m.text_sha256) AS canonical_documents,
               COUNT(DISTINCT m.author_id) AS distinct_authors,
               COUNT(DISTINCT m.community_id) AS distinct_communities,
               COUNT(DISTINCT m.source_id) AS distinct_sources,
               COUNT(DISTINCT CASE WHEN m.identity_confidence >= 0.5
                                   THEN m.author_id END) AS independent_authors,
               COUNT(DISTINCT CASE WHEN m.identity_confidence < 0.5
                                   THEN m.author_id END) AS uncertain_authors,
               COALESCE(s.largest_source_share, 0.0) AS largest_source_share
        FROM v_evidence_mentions m
        LEFT JOIN (
            SELECT pain_id,
                   MAX(source_mentions) * 1.0 / SUM(source_mentions)
                       AS largest_source_share
            FROM (
                SELECT pain_id, source_id, COUNT(*) AS source_mentions
                FROM v_evidence_mentions
                GROUP BY pain_id, source_id
            ) per_source
            GROUP BY pain_id
        ) s ON s.pain_id = m.pain_id
        GROUP BY m.pain_id, m.normalized_key, m.domain, m.severity, m.frequency,
                 m.accepted, s.largest_source_share
        """,
        _EVIDENCE_TABLES,
    ),
    (
        "v_pain_point_trend",
        """
        CREATE OR REPLACE VIEW v_pain_point_trend AS
        SELECT normalized_key, month, mention_count, distinct_authors,
               distinct_communities,
               mention_count - LAG(mention_count) OVER (
                   PARTITION BY normalized_key ORDER BY month
               ) AS mention_change
        FROM (
            SELECT normalized_key,
                   date_trunc('month', created_at) AS month,
                   COUNT(*) AS mention_count,
                   COUNT(DISTINCT author_id) AS distinct_authors,
                   COUNT(DISTINCT community_id) AS distinct_communities
            FROM v_evidence_mentions
            WHERE normalized_key IS NOT NULL
            GROUP BY normalized_key, date_trunc('month', created_at)
        ) monthly
        """,
        _EVIDENCE_TABLES,
    ),
    (
        "v_source_yield",
        """
        CREATE OR REPLACE VIEW v_source_yield AS
        SELECT s.source_id, s.source_type,
               COALESCE(f.fetch_count, 0) AS fetch_count,
               COUNT(DISTINCT m.text_sha256) AS document_count,
               COUNT(m.span_id) AS evidence_count,
               COUNT(DISTINCT CASE WHEN m.accepted THEN m.pain_id END)
                   AS accepted_pain_points,
               CASE WHEN COUNT(DISTINCT m.text_sha256) = 0 THEN 0.0
                    ELSE COUNT(DISTINCT CASE WHEN m.accepted THEN m.pain_id END)
                         * 1.0 / COUNT(DISTINCT m.text_sha256)
               END AS useful_evidence_yield,
               COUNT(DISTINCT m.author_id) AS distinct_authors,
               COUNT(DISTINCT m.community_id) AS distinct_communities,
               MIN(m.created_at) AS first_sighting,
               MAX(m.created_at) AS last_sighting
        FROM sources s
        LEFT JOIN v_evidence_mentions m ON m.source_id = s.source_id
        LEFT JOIN (
            SELECT source_id, COUNT(*) AS fetch_count
            FROM fetches
            GROUP BY source_id
        ) f ON f.source_id = s.source_id
        GROUP BY s.source_id, s.source_type, f.fetch_count
        """,
        (*_EVIDENCE_TABLES, "fetches"),
    ),
    (
        "v_community_evidence",
        """
        CREATE OR REPLACE VIEW v_community_evidence AS
        SELECT m.community_id,
               MAX(m.community_name) AS community_name,
               MAX(m.source_id) AS source_id,
               COUNT(*) AS mention_count,
               COUNT(DISTINCT m.text_sha256) AS canonical_documents,
               COUNT(DISTINCT m.author_id) AS distinct_authors,
               COUNT(DISTINCT CASE WHEN m.identity_confidence >= 0.5
                                   THEN m.author_id END) AS independent_authors,
               COUNT(DISTINCT CASE WHEN m.identity_confidence < 0.5
                                   THEN m.author_id END) AS uncertain_authors,
               COUNT(DISTINCT m.source_id) AS distinct_sources,
               MIN(m.created_at) AS first_sighting,
               MAX(m.created_at) AS last_sighting
        FROM v_evidence_mentions m
        WHERE m.community_id IS NOT NULL
        GROUP BY m.community_id
        """,
        _EVIDENCE_TABLES,
    ),
    (
        "v_independence_summary",
        """
        CREATE OR REPLACE VIEW v_independence_summary AS
        WITH mentions AS (
            SELECT * FROM v_evidence_mentions WHERE normalized_key IS NOT NULL
        ),
        per_pain AS (
            SELECT normalized_key,
                   COUNT(*) AS raw_mentions,
                   COUNT(DISTINCT text_sha256) AS canonical_observations,
                   COUNT(DISTINCT CASE WHEN identity_confidence >= 0.5
                                       THEN author_id END) AS independent_authors,
                   COUNT(DISTINCT CASE WHEN identity_confidence < 0.5
                                       THEN author_id END) AS uncertain_authors,
                   COUNT(DISTINCT CASE WHEN identity_confidence >= 0.5
                                       THEN community_id END)
                       AS independent_communities,
                   SUM(CASE WHEN pe.to_doc_id IS NULL THEN 1 ELSE 0 END)
                       AS propagation_adjusted_evidence
            FROM mentions
            LEFT JOIN (SELECT DISTINCT to_doc_id FROM propagation_edges) pe
                   ON pe.to_doc_id = mentions.doc_id
            GROUP BY normalized_key
        ),
        source_counts AS (
            SELECT normalized_key, source_id, COUNT(*) AS c
            FROM mentions GROUP BY normalized_key, source_id
        ),
        community_counts AS (
            SELECT normalized_key, community_id, COUNT(*) AS c
            FROM mentions GROUP BY normalized_key, community_id
        ),
        cluster_counts AS (
            -- The grouping the independence package uses: a mention counts
            -- against the author's identity group, and an author without a
            -- group counts against its own account. A mention with neither
            -- counts against nobody and is left out.
            SELECT m.normalized_key,
                   CASE WHEN a.cluster_id IS NOT NULL THEN a.cluster_id
                        ELSE 'author:' || m.author_id END AS group_key,
                   COUNT(*) AS c
            FROM mentions m
            LEFT JOIN authors a ON a.author_id = m.author_id
            WHERE a.cluster_id IS NOT NULL OR m.author_id IS NOT NULL
            GROUP BY m.normalized_key,
                     CASE WHEN a.cluster_id IS NOT NULL THEN a.cluster_id
                          ELSE 'author:' || m.author_id END
        ),
        chain_counts AS (
            SELECT m.normalized_key, COUNT(DISTINCT pe.from_doc_id) AS c
            FROM mentions m
            JOIN propagation_edges pe ON pe.from_doc_id = m.doc_id
            GROUP BY m.normalized_key
        ),
        concentrations AS (
            SELECT p.normalized_key,
                   COALESCE((SELECT MAX(c) FROM source_counts s
                             WHERE s.normalized_key = p.normalized_key), 0)
                       AS max_source,
                   COALESCE((SELECT MAX(c) FROM community_counts c
                             WHERE c.normalized_key = p.normalized_key), 0)
                       AS max_community,
                   COALESCE((SELECT MAX(c) FROM cluster_counts k
                             WHERE k.normalized_key = p.normalized_key), 0)
                       AS max_cluster
            FROM per_pain p
        )
        SELECT p.normalized_key,
               p.raw_mentions,
               p.canonical_observations,
               p.independent_authors,
               p.uncertain_authors,
               p.independent_communities,
               p.propagation_adjusted_evidence,
               COALESCE(ch.c, 0) AS propagation_chain_count,
               c.max_source * 1.0 / p.raw_mentions AS source_concentration,
               c.max_community * 1.0 / p.raw_mentions AS community_concentration,
               c.max_cluster * 1.0 / p.raw_mentions AS author_cluster_concentration
        FROM per_pain p
        JOIN concentrations c ON c.normalized_key = p.normalized_key
        LEFT JOIN chain_counts ch ON ch.normalized_key = p.normalized_key
        """,
        (*_EVIDENCE_TABLES, "propagation_edges"),
    ),
    (
        "v_source_concentration",
        """
        CREATE OR REPLACE VIEW v_source_concentration AS
        SELECT normalized_key,
               COUNT(*) AS source_count,
               MAX(share) AS largest_source_share,
               SUM(CASE WHEN rank <= 3 THEN share ELSE 0 END) AS top_three_share,
               SUM(share * share) AS herfindahl_index,
               MAX(share) > 0.5 AS concentrated,
               FIRST(source_id ORDER BY source_mentions DESC, source_id) AS top_source_id
        FROM (
            SELECT normalized_key, source_id, source_mentions, share,
                   row_number() OVER (PARTITION BY normalized_key
                                      ORDER BY source_mentions DESC, source_id)
                       AS rank
            FROM (
                SELECT normalized_key, source_id, COUNT(*) AS source_mentions,
                       COUNT(*) * 1.0
                           / SUM(COUNT(*)) OVER (PARTITION BY normalized_key) AS share
                FROM v_evidence_mentions
                WHERE normalized_key IS NOT NULL
                GROUP BY normalized_key, source_id
            ) per_source
        ) ranked
        GROUP BY normalized_key
        """,
        _EVIDENCE_TABLES,
    ),
    (
        "v_opportunity_evidence_trend",
        """
        CREATE OR REPLACE VIEW v_opportunity_evidence_trend AS
        SELECT cluster_id, month, mention_count,
               mention_count - LAG(mention_count) OVER (
                   PARTITION BY cluster_id ORDER BY month
               ) AS mention_change
        FROM (
            SELECT cm.cluster_id,
                   date_trunc('month', m.created_at) AS month,
                   COUNT(*) AS mention_count
            FROM cluster_members cm
            JOIN v_evidence_mentions m ON m.pain_id = cm.pain_id
            WHERE cm.valid_to IS NULL
            GROUP BY cm.cluster_id, date_trunc('month', m.created_at)
        ) monthly
        """,
        (*_EVIDENCE_TABLES, "cluster_members"),
    ),
    (
        "v_score_history",
        """
        CREATE OR REPLACE VIEW v_score_history AS
        SELECT opportunity_id, score_id, potential, confidence, created_at,
               potential - LAG(potential) OVER (
                   PARTITION BY opportunity_id ORDER BY created_at, score_id
               ) AS potential_change,
               confidence - LAG(confidence) OVER (
                   PARTITION BY opportunity_id ORDER BY created_at, score_id
               ) AS confidence_change
        FROM opportunity_scores
        """,
        ("opportunity_scores",),
    ),
    (
        "v_run_analytics",
        """
        CREATE OR REPLACE VIEW v_run_analytics AS
        SELECT r.run_id, r.status, r.created_at, r.started_at, r.completed_at,
               CASE WHEN r.started_at IS NULL OR r.completed_at IS NULL THEN NULL
                    ELSE date_diff('second', r.started_at, r.completed_at)
               END AS wall_time_seconds,
               COALESCE(t.tool_calls, 0) AS tool_calls,
               COALESCE(t.distinct_tools, 0) AS distinct_tools,
               COALESCE(t.failed_tool_calls, 0) AS failed_tool_calls,
               COALESCE(g.gate_verdicts, 0) AS gate_verdicts,
               COALESCE(g.passed_gates, 0) AS passed_gates
        FROM research_runs r
        LEFT JOIN (
            SELECT run_id,
                   COUNT(*) AS tool_calls,
                   COUNT(DISTINCT tool) AS distinct_tools,
                   COUNT(*) FILTER (WHERE status <> '""" + _SUCCESS_STATUS + """')
                       AS failed_tool_calls
            FROM tool_calls
            GROUP BY run_id
        ) t ON t.run_id = r.run_id
        LEFT JOIN (
            SELECT run_id,
                   COUNT(*) AS gate_verdicts,
                   COUNT(*) FILTER (WHERE passed = 1) AS passed_gates
            FROM stage_gate_results
            GROUP BY run_id
        ) g ON g.run_id = r.run_id
        """,
        ("research_runs", "tool_calls", "stage_gate_results"),
    ),
    (
        "v_run_budget",
        """
        CREATE OR REPLACE VIEW v_run_budget AS
        WITH per_tool AS (
            SELECT run_id, tool, provider, model,
                   SUM(units) AS units, SUM(cost_micros) AS cost_micros
            FROM spend_ledger
            GROUP BY run_id, tool, provider, model
        ),
        joined AS (
            SELECT r.run_id,
                   COALESCE(p.tool, '') AS tool,
                   COALESCE(p.provider, '') AS provider,
                   COALESCE(p.model, '') AS model,
                   COALESCE(p.units, 0) AS units,
                   COALESCE(p.cost_micros, 0) AS cost_micros
            FROM research_runs r
            LEFT JOIN per_tool p ON p.run_id = r.run_id
        ),
        per_run AS (
            SELECT run_id,
                   SUM(units) AS run_units,
                   SUM(cost_micros) AS run_cost_micros
            FROM joined GROUP BY run_id
        ),
        totals AS (
            SELECT SUM(run_cost_micros) AS total_cost_micros FROM per_run
        )
        SELECT j.run_id, j.tool, j.provider, j.model, j.units, j.cost_micros,
               p.run_units, p.run_cost_micros, t.total_cost_micros,
               p.run_cost_micros * 1.0 / NULLIF(t.total_cost_micros, 0)
                   AS cost_share
        FROM joined j
        JOIN per_run p ON p.run_id = j.run_id
        CROSS JOIN totals t
        """,
        ("research_runs", "spend_ledger"),
    ),
    (
        "v_collection_health",
        """
        CREATE OR REPLACE VIEW v_collection_health AS
        SELECT source_id,
               COUNT(*) AS attempts,
               COUNT(DISTINCT outcome) AS distinct_outcomes,
               COUNT(*) FILTER (WHERE outcome IN (""" + _COMPLETED + """)) AS successes,
               COUNT(*) FILTER (WHERE outcome IN (""" + _FAILED + """)) AS failures,
               COUNT(*) FILTER (WHERE outcome IN (""" + _COMPLETED + """)) * 1.0
                   / COUNT(*) AS success_rate,
               AVG(latency_ms) FILTER (WHERE outcome IN (""" + _COMPLETED + """))
                   AS mean_latency_ms,
               MAX(latency_ms) FILTER (WHERE outcome IN (""" + _COMPLETED + """))
                   AS max_latency_ms,
               SUM(bytes) AS total_bytes,
               COUNT(*) FILTER (WHERE outcome = 'not_modified') AS not_modified,
               COUNT(DISTINCT NULLIF(reason_code, '')) AS distinct_reason_codes
        FROM collection_items
        GROUP BY source_id
        """,
        ("collection_items",),
    ),
    (
        "v_discovery_yield",
        """
        CREATE OR REPLACE VIEW v_discovery_yield AS
        SELECT event_id, run_id, created_at,
               json_extract_string(payload_json, '$.record_id') AS record_id,
               json_extract_string(payload_json, '$.family') AS family,
               json_extract_string(payload_json, '$.query') AS query,
               json_extract_string(payload_json, '$.status') AS status,
               CAST(json_extract_string(payload_json, '$.result_count') AS BIGINT)
                   AS result_count,
               CAST(json_extract_string(payload_json, '$.useful_count') AS BIGINT)
                   AS useful_count,
               CAST(json_extract_string(payload_json, '$.novelty') AS DOUBLE)
                   AS novelty,
               CAST(json_extract_string(payload_json, '$.duplicate_rate') AS DOUBLE)
                   AS duplicate_rate,
               CAST(json_extract_string(payload_json, '$.cost') AS DOUBLE) AS cost,
               CASE WHEN CAST(json_extract_string(payload_json, '$.cost') AS DOUBLE) > 0
                    THEN CAST(json_extract_string(payload_json, '$.useful_count') AS BIGINT)
                         * 1.0
                         / CAST(json_extract_string(payload_json, '$.cost') AS DOUBLE)
                    ELSE 0.0
               END AS useful_per_dollar
        FROM research_events
        WHERE event_type = '""" + EVENT_TYPE + """'
        """,
        ("research_events",),
    ),
    (
        "v_query_family_performance",
        """
        CREATE OR REPLACE VIEW v_query_family_performance AS
        SELECT family,
               COUNT(*) AS searches,
               SUM(result_count) AS results,
               SUM(useful_count) AS useful,
               AVG(novelty) AS mean_novelty,
               AVG(duplicate_rate) AS mean_duplicate_rate,
               AVG(useful_count) AS mean_useful_count,
               SUM(cost) AS total_cost,
               CASE WHEN SUM(cost) > 0 THEN SUM(useful_count) * 1.0 / SUM(cost)
                    ELSE 0.0 END AS useful_per_dollar,
               AVG(novelty) < """ + str(SATURATION_MIN_NOVELTY) + """
                   OR AVG(duplicate_rate) > """ + str(SATURATION_MAX_DUPLICATE_RATE) + """
                   AS saturated
        FROM v_discovery_yield
        GROUP BY family
        """,
        ("research_events",),
    ),
    (
        "v_temporal_persistence",
        """
        CREATE OR REPLACE VIEW v_temporal_persistence AS
        WITH sightings AS (
            SELECT normalized_key,
                   MIN(created_at) AS first_sighting,
                   MAX(created_at) AS last_sighting,
                   COUNT(DISTINCT date_trunc('month', created_at)) AS active_months
            FROM v_evidence_mentions
            WHERE normalized_key IS NOT NULL
            GROUP BY normalized_key
        ),
        horizon AS (SELECT MAX(last_sighting) AS newest FROM sightings)
        SELECT s.normalized_key,
               s.first_sighting,
               s.last_sighting,
               date_diff('day', s.first_sighting, s.last_sighting) AS span_days,
               s.active_months,
               date_diff('month', s.last_sighting, h.newest)
                   AS months_since_last_sighting,
               CASE WHEN s.active_months = 1 THEN '""" + PERSISTENCE_SINGLE + """'
                    WHEN date_diff('month', s.last_sighting, h.newest) > """
                   + str(DORMANT_AFTER_MONTHS) + """ THEN '"""
                   + PERSISTENCE_DORMANT + """'
                    WHEN s.active_months >= """ + str(PERSISTENT_FROM_MONTHS) + """
                         THEN '""" + PERSISTENCE_PERSISTENT + """'
                    ELSE '""" + PERSISTENCE_EMERGING + """'
               END AS persistence_class
        FROM sightings s
        CROSS JOIN horizon h
        """,
        _EVIDENCE_TABLES,
    ),
)


def create_views(
    conn: duckdb.DuckDBPyConnection,
    available: frozenset[str] | set[str],
) -> tuple[ViewReport, ...]:
    """Create the views whose tables are all there, and report every view.

    A view whose table is missing is skipped and reported by name: a caller
    that needs it learns which table is absent instead of reading an empty
    answer.
    """
    reports: list[ViewReport] = []
    for name, sql, required in VIEW_DDL:
        missing = sorted(table for table in required if table not in available)
        if missing:
            reports.append(
                ViewReport(
                    name=name,
                    created=False,
                    reason="missing table: " + ", ".join(missing),
                )
            )
            continue
        conn.execute(sql)
        reports.append(ViewReport(name=name, created=True))
    return tuple(reports)


def required_tables(view: str) -> tuple[str, ...]:
    """The tables a view names, or nothing when the view is not known here."""
    for name, _sql, required in VIEW_DDL:
        if name == view:
            return required
    return ()

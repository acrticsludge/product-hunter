"""The metrics of the observability engine.

The database these tests read is built with plain inserts, so the numbers
under test are the numbers the tables hold and not the numbers a pipeline
happened to write on the day.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from product_hunter.cli import observe_metrics_command
from product_hunter.db import connect, current_version, migrate
from product_hunter.db.migrations import MIGRATIONS_DIR
from product_hunter.discovery.families import QueryFamily
from product_hunter.discovery.records import DiscoveryRecord, DiscoveryStatus
from product_hunter.observability.metrics import (
    Metric,
    MetricReport,
    MetricStatus,
    collect_metrics,
)

NOW = "2026-10-20T10:00:00+00:00"


def populated_store(tmp_path: Path) -> sqlite3.Connection:
    """A store with one run and the rows of every table the metrics read."""
    conn = connect(":memory:")
    migrate(conn)
    _write_rows(conn)
    return conn


def version_seven_store(tmp_path: Path) -> sqlite3.Connection:
    """A store at the schema version that has no model comparison table."""
    target = tmp_path / "migrations"
    target.mkdir(parents=True, exist_ok=True)
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        if int(path.name[:4]) <= 7:
            shutil.copy(path, target / path.name)
    conn = connect(":memory:")
    migrate(conn, migrations_dir=target)
    return conn


def metric(report: MetricReport, name: str) -> Metric:
    """The metric of that name, or a failure that says it is not there."""
    for item in report.metrics:
        if item.name == name:
            return item
    raise AssertionError(f"the report holds no metric named {name!r}")


def _write_rows(conn: sqlite3.Connection) -> None:
    """Write one run and the rows of every table the metrics read."""
    _sources(conn)
    _run(conn)
    _tool_calls(conn)
    _stages(conn)
    _events(conn)
    _model_calls(conn)
    _spend(conn)
    _corpus(conn)
    _collection(conn)
    _jobs(conn)
    _independence(conn)
    _clusters(conn)


def _sources(conn: sqlite3.Connection) -> None:
    conn.execute(
        "INSERT INTO sources (source_id, source_type, terms_status) "
        "VALUES ('hn_algolia', 'community', 'permitted')"
    )
    conn.execute(
        "INSERT INTO sources (source_id, source_type, terms_status) "
        "VALUES ('reddit', 'community', 'permitted')"
    )
    conn.execute(
        "INSERT INTO fetches (fetch_id, source_id, requested_url, status, "
        "latency_ms, fetched_at, retry_count) "
        "VALUES ('fetch-1', 'hn_algolia', 'https://example.com/1', 200, 100, ?, 0)",
        (NOW,),
    )
    conn.execute(
        "INSERT INTO fetches (fetch_id, source_id, requested_url, status, "
        "latency_ms, fetched_at, retry_count) "
        "VALUES ('fetch-2', 'hn_algolia', 'https://example.com/2', 500, 300, ?, 1)",
        (NOW,),
    )
    conn.execute(
        "INSERT INTO fetches (fetch_id, source_id, requested_url, status, "
        "latency_ms, fetched_at, retry_count) "
        "VALUES ('fetch-3', 'reddit', 'https://example.com/3', 200, 200, ?, 0)",
        (NOW,),
    )


def _run(conn: sqlite3.Connection) -> None:
    conn.execute(
        "INSERT INTO research_runs (run_id, public_id, objective, "
        "original_request, status, current_stage, created_at, started_at, "
        "completed_at, progress_percent, contract_version, model_policy_version) "
        "VALUES ('run-1', 'pub-1', 'find the pains', 'find the pains', "
        "'COMPLETED', 'PAIN_EXTRACTION', '2026-10-20T09:00:00+00:00', ?, ?, 100, "
        "'phase2-v1', 'policy-1')",
        (NOW, "2026-10-20T10:30:00+00:00"),
    )


def _tool_calls(conn: sqlite3.Connection) -> None:
    conn.execute(
        "INSERT INTO tool_calls (tool_call_id, run_id, stage, agent, tool, "
        "purpose, query_or_operation, result_ref, result_count, status, "
        "duration_ms, occurred_at, attempt) "
        "VALUES ('tc-1', 'run-1', 'PAIN_EXTRACTION', 'extractor', 'hn_search', "
        "'find pains', 'invoice', 'ref-1', 3, 'success', 120, ?, 1)",
        (NOW,),
    )
    conn.execute(
        "INSERT INTO tool_calls (tool_call_id, run_id, stage, agent, tool, "
        "purpose, query_or_operation, result_ref, result_count, status, "
        "duration_ms, occurred_at, attempt) "
        "VALUES ('tc-2', 'run-1', 'PAIN_EXTRACTION', 'extractor', 'hn_search', "
        "'find pains', 'invoice', NULL, 0, 'failure', 200, ?, 2)",
        ("2026-10-20T10:11:00+00:00",),
    )


def _stages(conn: sqlite3.Connection) -> None:
    conn.execute(
        "INSERT INTO research_stage_executions (execution_id, run_id, stage, "
        "status, attempt, started_at, finished_at) "
        "VALUES ('se-1', 'run-1', 'PAIN_EXTRACTION', 'succeeded', 1, ?, ?)",
        (NOW, "2026-10-20T10:20:00+00:00"),
    )
    conn.execute(
        "INSERT INTO stage_gate_results (gate_result_id, run_id, stage, passed, "
        "violations_json, metrics_json, checked_at) "
        "VALUES ('g-1', 'run-1', 'PAIN_EXTRACTION', 1, '[]', '{}', ?)",
        (NOW,),
    )
    conn.execute(
        "INSERT INTO stage_gate_results (gate_result_id, run_id, stage, passed, "
        "violations_json, metrics_json, checked_at) "
        "VALUES ('g-2', 'run-1', NULL, 0, '[{\"code\": \"too few\"}]', '{}', ?)",
        ("2026-10-20T10:31:00+00:00",),
    )


def _events(conn: sqlite3.Connection) -> None:
    first = DiscoveryRecord(
        run_id="run-1",
        stage="DISCOVERY",
        tool="search",
        agent="discovery",
        query_id="q_" + "0" * 16,
        query="invoice pain",
        family=QueryFamily.problem,
        source_id="hn_algolia",
        timestamp=datetime(2026, 10, 20, 10, 5, tzinfo=UTC),
        result_count=10,
        useful_count=4,
        novelty=0.6,
        duplicate_rate=0.4,
        cost=0.01,
        status=DiscoveryStatus.success,
        result_ref="dr-1",
    )
    second = DiscoveryRecord(
        run_id="run-1",
        stage="DISCOVERY",
        tool="search",
        agent="discovery",
        query_id="q_" + "1" * 16,
        query="invoice app",
        family=QueryFamily.competitor,
        source_id="reddit",
        timestamp=datetime(2026, 10, 20, 10, 6, tzinfo=UTC),
        result_count=5,
        useful_count=5,
        novelty=1.0,
        duplicate_rate=0.0,
        cost=0.02,
        status=DiscoveryStatus.success,
        result_ref="dr-2",
    )
    conn.execute(
        "INSERT INTO research_events (event_id, run_id, event_type, stage, tool, "
        "agent, payload_ref, payload_json, created_at) "
        "VALUES ('evt-1', 'run-1', 'DISCOVERY_QUERY_EXECUTED', 'DISCOVERY', "
        "'search', 'discovery', 'dr-1', ?, ?)",
        (first.model_dump_json(), NOW),
    )
    conn.execute(
        "INSERT INTO research_events (event_id, run_id, event_type, stage, tool, "
        "agent, payload_ref, payload_json, created_at) "
        "VALUES ('evt-2', 'run-1', 'DISCOVERY_QUERY_EXECUTED', 'DISCOVERY', "
        "'search', 'discovery', 'dr-2', ?, ?)",
        (second.model_dump_json(), "2026-10-20T10:06:00+00:00"),
    )
    conn.execute(
        "INSERT INTO research_events (event_id, run_id, event_type, stage, tool, "
        "agent, payload_ref, payload_json, created_at) "
        "VALUES ('evt-3', 'run-1', 'DISCOVERY_QUERY_EXECUTED', 'DISCOVERY', "
        "'search', 'discovery', NULL, 'not json at all', ?)",
        ("2026-10-20T10:07:00+00:00",),
    )
    conn.execute(
        "INSERT INTO research_events (event_id, run_id, event_type, stage, tool, "
        "agent, payload_ref, payload_json, created_at) "
        "VALUES ('evt-4', 'run-1', 'STAGE_COMPLETED', 'PAIN_EXTRACTION', "
        "'gate', 'researcher', 'ref-1', NULL, ?)",
        ("2026-10-20T10:21:00+00:00",),
    )


def _model_calls(conn: sqlite3.Connection) -> None:
    conn.execute(
        "INSERT INTO model_executions (execution_id, run_id, stage, role, "
        "provider_id, model_id, selection_source, fallback_index, is_fallback, "
        "prompt_version, latency_ms, status, created_at) "
        "VALUES ('me-1', 'run-1', 'PAIN_EXTRACTION', 'extraction', 'opencode-go', "
        "'deepseek-v4.1-flash', 'default', 0, 0, 'extract-v3', 250, 'ok', ?)",
        (NOW,),
    )
    conn.execute(
        "INSERT INTO model_executions (execution_id, run_id, stage, role, "
        "provider_id, model_id, selection_source, fallback_index, is_fallback, "
        "prompt_version, latency_ms, status, created_at) "
        "VALUES ('me-2', 'run-1', 'PAIN_EXTRACTION', 'extraction', 'opencode-go', "
        "'gpt-5', 'fallback', 1, 1, 'extract-v3', 400, 'error', ?)",
        ("2026-10-20T10:13:00+00:00",),
    )


def _spend(conn: sqlite3.Connection) -> None:
    conn.execute(
        "INSERT INTO spend_ledger (entry_id, run_id, stage, tool, provider, model, "
        "units, unit_type, cost_micros, created_at) "
        "VALUES ('spn-1', 'run-1', 'DISCOVERY', 'search', NULL, NULL, 1, "
        "'search_queries', 1200, ?)",
        (NOW,),
    )
    conn.execute(
        "INSERT INTO spend_ledger (entry_id, run_id, stage, tool, provider, model, "
        "units, unit_type, cost_micros, created_at) "
        "VALUES ('spn-2', 'run-1', 'PAIN_EXTRACTION', NULL, 'opencode-go', "
        "'deepseek-v4.1-flash', 3, 'llm_calls', 5000, ?)",
        (NOW,),
    )


def _corpus(conn: sqlite3.Connection) -> None:
    for index, (doc_id, fetch_id, source_id, digest) in enumerate(
        (
            ("doc-1", "fetch-1", "hn_algolia", "aaa"),
            ("doc-2", "fetch-2", "hn_algolia", "aaa"),
            ("doc-3", "fetch-3", "reddit", "bbb"),
        )
    ):
        conn.execute(
            "INSERT INTO documents (doc_id, fetch_id, source_id, url, "
            "text_sha256, text_path, raw_path, raw_sha256, parser_version) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'phase1-0.3')",
            (
                doc_id,
                fetch_id,
                source_id,
                f"https://example.com/{index}",
                digest,
                f"text/{doc_id}.txt",
                f"raw/{doc_id}.json",
                f"raw-{doc_id}",
            ),
        )
    conn.execute(
        "INSERT INTO evidence (span_id, doc_id, quote, char_start, char_end) "
        "VALUES ('span-1', 'doc-1', 'the first quote', 0, 14)"
    )
    conn.execute(
        "INSERT INTO evidence (span_id, doc_id, quote, char_start, char_end) "
        "VALUES ('span-2', 'doc-2', 'the second quote', 0, 15)"
    )
    conn.execute(
        "INSERT INTO evidence (span_id, doc_id, quote, char_start, char_end) "
        "VALUES ('span-3', 'doc-3', 'the third quote', 0, 14)"
    )
    conn.execute(
        "INSERT INTO pain_points (pain_id, span_id, doc_id, severity, frequency, "
        "accepted, normalized_key) "
        "VALUES ('pp-1', 'span-1', 'doc-1', 4, 3, 1, 'key-a')"
    )
    conn.execute(
        "INSERT INTO pain_points (pain_id, span_id, doc_id, severity, frequency, "
        "accepted, normalized_key) "
        "VALUES ('pp-2', 'span-2', 'doc-2', 0, 0, 0, 'key-a')"
    )
    conn.execute(
        "INSERT INTO pain_points (pain_id, span_id, doc_id, severity, frequency, "
        "accepted, normalized_key) "
        "VALUES ('pp-3', 'span-3', 'doc-3', 2, 1, 0, 'key-b')"
    )


def _collection(conn: sqlite3.Connection) -> None:
    conn.execute(
        "INSERT INTO collection_items (run_id, source_id, url, attempt_no, "
        "outcome, reason_code, attempted_at) "
        "VALUES ('run-1', 'hn_algolia', 'https://example.com/1', 1, 'fetched', "
        "'ok', ?)",
        (NOW,),
    )
    conn.execute(
        "INSERT INTO collection_items (run_id, source_id, url, attempt_no, "
        "outcome, reason_code, attempted_at) "
        "VALUES ('run-1', 'reddit', 'https://example.com/2', 1, 'skipped', "
        "'robots_disallowed', ?)",
        (NOW,),
    )


def _jobs(conn: sqlite3.Connection) -> None:
    conn.execute(
        "INSERT INTO jobs (id, run_id, stage, step, status, attempts, "
        "max_attempts, idempotency_key) "
        "VALUES ('job-1', 'run-1', 'PAIN_EXTRACTION', 'extract', 'failed', 2, "
        "3, 'key-1')"
    )
    conn.execute(
        "INSERT INTO jobs (id, run_id, stage, step, status, attempts, "
        "max_attempts, idempotency_key) "
        "VALUES ('job-2', 'run-1', 'PAIN_EXTRACTION', 'gate', 'dead', 3, 3, 'key-2')"
    )
    conn.execute(
        "INSERT INTO stage_attempts (attempt_id, job_id, run_id, stage, attempt, "
        "status, started_at, finished_at) "
        "VALUES ('sa-1', 'job-1', 'run-1', 'PAIN_EXTRACTION', 1, 'lease_lost', ?, ?)",
        (NOW, "2026-10-20T10:01:00+00:00"),
    )


def _independence(conn: sqlite3.Connection) -> None:
    conn.execute(
        "INSERT INTO authors (author_id, source_id, handle) "
        "VALUES ('auth-1', 'hn_algolia', 'alice')"
    )
    conn.execute(
        "INSERT INTO authors (author_id, source_id, handle) "
        "VALUES ('auth-2', 'hn_algolia', 'bob')"
    )
    metrics = {
        "independent_author_count": 2,
        "uncertain_author_count": 1,
        "independent_community_count": 1,
        "source_concentration": 0.5,
    }
    conn.execute(
        "INSERT INTO independence_reports (report_id, scope_id, policy_version, "
        "metrics_json, violations_json, satisfied, created_at) "
        "VALUES ('ir-1', 'run-1', 'independence-v1', ?, '[]', 1, ?)",
        (json.dumps(metrics, sort_keys=True), NOW),
    )


def _clusters(conn: sqlite3.Connection) -> None:
    conn.execute(
        "INSERT INTO clustering_runs (run_id, algorithm_version, config_version, "
        "prompt_version, space_id, created_at) "
        "VALUES ('cl-1', 'alg-v1', 'cfg-v1', 'cluster-v1', 'space-1', ?)",
        (NOW,),
    )
    for cluster_id, status, members in (
        ("cluster-1", "active", 2),
        ("cluster-2", "active", 1),
        ("cluster-3", "merged", 2),
    ):
        conn.execute(
            "INSERT INTO clusters (cluster_id, version, status, member_count, "
            "run_id, created_at, updated_at) "
            "VALUES (?, 1, ?, ?, 'cl-1', ?, ?)",
            (cluster_id, status, members, NOW, NOW),
        )
    for member_id, cluster_id, pain_id in (
        ("cm-1", "cluster-1", "pp-1"),
        ("cm-2", "cluster-1", "pp-2"),
        ("cm-3", "cluster-2", "pp-3"),
    ):
        conn.execute(
            "INSERT INTO cluster_members (member_id, cluster_id, pain_id, "
            "valid_from, basis) VALUES (?, ?, ?, ?, 'exact')",
            (member_id, cluster_id, pain_id, NOW),
        )
    conn.execute(
        "INSERT INTO cluster_decisions (decision_id, run_id, kind, relation, "
        "pain_a, pain_b, cluster_id, decision, created_at) "
        "VALUES ('cd-1', 'cl-1', 'adjudication', 'same_problem', 'pp-1', 'pp-2', "
        "'cluster-1', 'same_problem', ?)",
        (NOW,),
    )


def test_a_populated_database_produces_the_expected_values(tmp_path) -> None:
    conn = populated_store(tmp_path)
    report = collect_metrics(conn, run_id="run-1")

    assert report.schema_version == 16
    assert report.run_id == "run-1"
    assert len(report.groups) == 20

    assert metric(report, "run.status").detail["text"] == "COMPLETED"
    assert metric(report, "run.duration_seconds").value == 1800.0
    assert metric(report, "run.progress_percent").value == 100

    assert metric(report, "stage.stages").value == 1
    assert metric(report, "stage.PAIN_EXTRACTION.status").detail["text"] == "succeeded"
    assert metric(report, "stage.PAIN_EXTRACTION.attempts").value == 1
    assert metric(report, "stage.PAIN_EXTRACTION.duration_ms").value == 1200000.0
    assert metric(report, "stage.PAIN_EXTRACTION.tool_calls").value == 2
    assert metric(report, "stage.PAIN_EXTRACTION.failed_tool_calls").value == 1
    assert metric(report, "stage.PAIN_EXTRACTION.gate_passed").value == 1
    assert metric(report, "stage.PAIN_EXTRACTION.gate_passed").detail["passed"] is True
    assert metric(report, "stage.PAIN_EXTRACTION.violations").value == 0

    assert metric(report, "tool_call.total").value == 2
    assert metric(report, "tool_call.failed").value == 1
    assert metric(report, "tool_call.failure_rate").value == 0.5
    assert metric(report, "tool_call.result_count_total").value == 3
    assert metric(report, "tool_call.duration_mean_ms").value == 160.0
    assert metric(report, "tool_call.duration_p50_ms").value == 120
    assert metric(report, "tool_call.duration_max_ms").value == 200

    assert metric(report, "source_health.sources").value == 2
    assert metric(report, "source_health.hn_algolia.fetches").value == 2
    assert metric(report, "source_health.hn_algolia.failures").value == 1
    assert metric(report, "source_health.hn_algolia.success_rate").value == 0.5
    assert metric(report, "source_health.hn_algolia.latency_p50_ms").value == 100
    assert metric(report, "source_health.collection_items").value == 2

    assert metric(report, "discovery_yield.queries_executed").value == 3
    assert metric(report, "discovery_yield.records").value == 2
    assert metric(report, "discovery_yield.unreadable_records").value == 1
    assert metric(report, "discovery_yield.novel_records").value == 2
    assert metric(report, "discovery_yield.duplicate_yield").value == 0.266667
    assert metric(report, "discovery_yield.source_yield").value == 1.0
    assert metric(report, "discovery_yield.family.problem.queries").value == 1
    assert metric(report, "discovery_yield.family.competitor.duplicate_yield").value == 0.0

    assert metric(report, "evidence_yield.documents").value == 3
    assert metric(report, "evidence_yield.evidence_spans").value == 3
    assert metric(report, "evidence_yield.pain_points").value == 3
    assert metric(report, "evidence_yield.accepted_pain_points").value == 1
    assert metric(report, "evidence_yield.accepted_rate").value == 0.333333
    assert metric(report, "evidence_yield.evidence_per_document").value == 1.0

    assert metric(report, "duplicate_rate.duplicate_text_rate").value == 0.666667
    assert metric(report, "duplicate_rate.duplicate_key_rate").value == 0.666667
    assert metric(report, "duplicate_rate.duplicate_spans").value == 0

    assert metric(report, "independence_rate.authors").value == 2
    assert metric(report, "independence_rate.independent_authors").value == 2
    assert metric(report, "independence_rate.independent_author_rate").value == 1.0
    assert metric(report, "independence_rate.uncertain_authors").value == 1
    assert metric(report, "independence_rate.source_concentration").value == 0.5

    assert metric(report, "cluster_quality.clustering_runs").value == 1
    assert metric(report, "cluster_quality.clusters").value == 2
    assert metric(report, "cluster_quality.mean_cluster_size").value == 1.5
    assert metric(report, "cluster_quality.singletons").value == 1
    assert metric(report, "cluster_quality.singleton_rate").value == 0.5
    assert metric(report, "cluster_quality.merge_decisions").value == 1

    assert metric(report, "completion.runs_total").value == 1
    assert metric(report, "completion.runs_completed").value == 1
    assert metric(report, "completion.completion_rate").value == 1.0
    assert metric(report, "completion.stages_total").value == 1
    assert metric(report, "completion.stage_pass_rate").value == 1.0
    assert metric(report, "completion.gate_pass_rate").value == 0.0

    assert metric(report, "budget.entries").value == 2
    assert metric(report, "budget.cost_micros_total").value == 6200
    assert metric(report, "budget.cost_by_stage.DISCOVERY").value == 1200
    assert metric(report, "budget.cost_by_provider.opencode-go").value == 5000
    assert metric(report, "budget.units_by_type.search_queries").value == 1

    assert metric(report, "model_usage.models").value == 2
    assert metric(report, "model_usage.deepseek-v4.1-flash.calls").value == 1
    assert metric(report, "model_usage.gpt-5.fallback_calls").value == 1
    assert metric(report, "model_usage.gpt-5.latency_mean_ms").value == 400.0

    assert metric(report, "provider_usage.providers").value == 1
    assert metric(report, "provider_usage.opencode-go.calls").value == 2
    assert metric(report, "provider_usage.opencode-go.models").value == 2
    assert metric(report, "provider_usage.opencode-go.failures").value == 1

    assert metric(report, "latency.tool_call_ms_mean").value == 160.0
    assert metric(report, "latency.model_execution_ms_p50").value == 250
    assert metric(report, "latency.fetch_ms_max").value == 300
    assert metric(report, "latency.stage_ms_mean").value == 1200000.0

    assert metric(report, "cost_accounting.cost_micros_total").value == 6200
    assert metric(report, "cost_accounting.cost_per_tool_call").value == 3100.0
    assert metric(report, "cost_accounting.cost_per_accepted_pain_point").value == 6200.0


def test_the_token_metrics_are_not_measured_while_no_spend_row_carries_one(
    tmp_path,
) -> None:
    conn = populated_store(tmp_path)
    report = collect_metrics(conn, run_id="run-1")

    for name in ("cost_accounting.input_tokens", "cost_accounting.output_tokens"):
        found = metric(report, name)
        assert found.status is MetricStatus.not_measured
        assert found.value is None
        assert found.reason == "no spend row carries a token unit"


def test_the_token_metrics_are_measured_once_a_spend_row_carries_one(
    tmp_path,
) -> None:
    conn = populated_store(tmp_path)
    conn.execute(
        "INSERT INTO spend_ledger (entry_id, run_id, stage, provider, model, "
        "units, unit_type, cost_micros, created_at) "
        "VALUES ('spn-3', 'run-1', 'PAIN_EXTRACTION', 'opencode-go', "
        "'deepseek-v4.1-flash', 900, 'llm_input_tokens', 10, ?)",
        (NOW,),
    )
    report = collect_metrics(conn, run_id="run-1")

    assert metric(report, "cost_accounting.input_tokens").value == 900
    assert metric(report, "cost_accounting.output_tokens").status is (
        MetricStatus.not_measured
    )


def test_an_empty_database_measures_what_it_can_and_names_what_it_cannot(
    tmp_path,
) -> None:
    conn = connect(":memory:")
    migrate(conn)
    report = collect_metrics(conn)

    assert len(report.groups) == 20
    assert len(report.metrics) > 0
    # A count over a table that exists is a measurement, and a zero is a
    # legitimate count.
    assert metric(report, "tool_call.total").value == 0
    assert metric(report, "tool_call.total").status is MetricStatus.measured
    # A rate with no denominator is not measured, and it says why.
    failure = metric(report, "tool_call.failure_rate")
    assert failure.status is MetricStatus.not_measured
    assert failure.value is None
    assert failure.reason == "no denominator"
    # A run that was not named is not measured either.
    assert metric(report, "run.status").status is MetricStatus.not_measured
    assert metric(report, "run.status").reason == "no run was selected"


def test_a_rate_with_no_denominator_is_never_zero(tmp_path) -> None:
    conn = connect(":memory:")
    migrate(conn)
    report = collect_metrics(conn)

    for item in report.metrics:
        if item.unit != "rate":
            continue
        if item.status is MetricStatus.measured:
            continue
        assert item.value is None
        assert item.reason


def test_a_database_at_schema_version_seven_does_not_raise(tmp_path) -> None:
    conn = version_seven_store(tmp_path)
    assert current_version(conn) == 7

    report = collect_metrics(conn)

    assert report.schema_version == 7
    for group in ("regression", "model_comparison", "evaluation_coverage", "evaluation_status"):
        found = [item for item in report.metrics if item.group == group]
        assert found, f"the family {group} produced no metric at all"
        for item in found:
            assert item.status is MetricStatus.not_measured
            assert item.value is None
            assert "model_comparisons" in (item.reason or "")


def test_a_run_that_does_not_exist_is_not_measured(tmp_path) -> None:
    conn = populated_store(tmp_path)
    report = collect_metrics(conn, run_id="run-missing")

    for item in report.metrics:
        if item.group != "run":
            continue
        assert item.status is MetricStatus.not_measured
        assert item.value is None
        assert "run-missing" in (item.reason or "")


def test_the_report_says_which_scope_each_metric_was_read_at(tmp_path) -> None:
    conn = populated_store(tmp_path)
    report = collect_metrics(conn, run_id="run-1")

    assert metric(report, "run.status").scope == "run"
    assert metric(report, "tool_call.total").scope == "run"
    assert metric(report, "evidence_yield.documents").scope == "corpus"
    assert metric(report, "latency.fetch_ms_mean").scope == "corpus"
    assert metric(report, "model_comparison.comparisons").scope == "database"


def test_the_report_notes_the_missing_comparison_table(tmp_path) -> None:
    conn = version_seven_store(tmp_path)
    report = collect_metrics(conn)

    assert any("model_comparisons" in note for note in report.notes)


def test_a_provider_named_by_both_sides_is_one_metric_with_the_summed_count(
    tmp_path,
) -> None:
    conn = populated_store(tmp_path)
    _two_comparisons_over_one_provider(conn)
    report = collect_metrics(conn)

    providers = [
        item for item in report.metrics if item.name.startswith("model_comparison.provider.")
    ]
    assert len(providers) == 1, [item.name for item in providers]
    assert providers[0].value == 2


def _two_comparisons_over_one_provider(conn: sqlite3.Connection) -> None:
    """Write two comparisons whose baseline and candidate share a provider."""
    for index in (1, 2):
        conn.execute(
            """
            INSERT INTO model_comparisons (
                comparison_id, dataset_version, evaluator_version,
                baseline_run_id, baseline_provider, baseline_model,
                candidate_run_id, candidate_provider, candidate_model,
                verdict, comparable, regressions, improvements,
                tasks_json, created_at
            ) VALUES (?, 'ds-v2', 'eval-v2', ?, 'one-provider', 'model-a', ?,
                      'one-provider', 'model-b', 'regression', 1, 1, 0, '[]', ?)
            """,
            (f"cmp_{index}", f"base-{index}", f"cand-{index}", f"2026-10-2{index}"),
        )
    conn.commit()


def test_a_failed_run_counts_its_failures(tmp_path) -> None:
    conn = populated_store(tmp_path)
    report = collect_metrics(conn, run_id="run-1")

    assert metric(report, "failure_retry.tool_call_failures").value == 1
    assert metric(report, "failure_retry.model_execution_failures").value == 1
    assert metric(report, "failure_retry.job_failures").value == 1
    assert metric(report, "failure_retry.dead_jobs").value == 1
    assert metric(report, "failure_retry.failure_rate").value == 0.5


def test_a_retry_is_counted(tmp_path) -> None:
    conn = populated_store(tmp_path)
    report = collect_metrics(conn, run_id="run-1")

    assert metric(report, "failure_retry.tool_call_retries").value == 1
    assert metric(report, "failure_retry.job_retries").value == 2
    assert metric(report, "failure_retry.lease_lost_attempts").value == 1
    assert metric(report, "tool_call.retried").value == 1


def test_a_run_id_that_looks_like_sql_is_bound_and_not_run(tmp_path) -> None:
    conn = populated_store(tmp_path)

    report = collect_metrics(conn, run_id="'; DROP TABLE tool_calls; --")

    assert metric(report, "run.status").status is MetricStatus.not_measured
    assert conn.execute("SELECT count(*) FROM tool_calls").fetchone()[0] == 2


def test_two_reads_of_the_same_database_agree(tmp_path) -> None:
    conn = populated_store(tmp_path)

    first = collect_metrics(conn, run_id="run-1")
    second = collect_metrics(conn, run_id="run-1")

    assert first.model_dump_json() == second.model_dump_json()


def test_the_read_side_never_writes(tmp_path) -> None:
    conn = populated_store(tmp_path)
    before = conn.execute("SELECT count(*) FROM tool_calls").fetchone()[0]

    collect_metrics(conn, run_id="run-1")

    after = conn.execute("SELECT count(*) FROM tool_calls").fetchone()[0]
    assert after == before
    assert not conn.in_transaction


def test_the_command_prints_the_report_as_json(tmp_path, capsys) -> None:
    db_path = tmp_path / "product_hunter.db"
    conn = connect(db_path)
    migrate(conn)
    _write_rows(conn)
    conn.close()

    assert (
        observe_metrics_command(
            db_path=db_path, out=tmp_path / "metrics.md", repo_root=tmp_path
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert len(payload["groups"]) == 20
    assert payload["schema_version"] == 16
    assert (tmp_path / "metrics.md").is_file()


def test_the_command_refuses_a_database_that_does_not_exist(tmp_path, capsys) -> None:
    assert (
        observe_metrics_command(db_path=tmp_path / "missing.db", repo_root=tmp_path)
        == 1
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert "database does not exist" in payload["error"]
    assert not (tmp_path / "missing.db").exists()

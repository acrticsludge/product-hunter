"""The metric families of the observability engine.

This module owns the numbers and the state each number is in. It reads the
store and never writes to it, and it never raises because a table is missing
or empty. A number that could not be computed comes back with a null value
and a reason that says what was missing, so a reader can tell a zero from an
unknown without guessing.

Three states, not two. ``measured`` means the number was read from rows that
exist. ``not_measured`` means there was nothing to measure.
``not_applicable`` means the metric does not apply to the scope it was asked
about. A rate whose denominator is zero is never ``0``: it is
``not_measured`` with the reason ``no denominator``.

Nothing here reads the clock or draws a random number, so two reads of the
same database produce the same report, byte for byte.
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

# The version of the measurement contract this module implements. A report
# carries it, so a reader knows which set of rules produced the numbers.
OBSERVABILITY_VERSION = "observability-v1"

# The twenty metric families, in the order the report prints them.
GROUPS: tuple[str, ...] = (
    "run",
    "stage",
    "tool_call",
    "source_health",
    "discovery_yield",
    "evidence_yield",
    "duplicate_rate",
    "independence_rate",
    "cluster_quality",
    "completion",
    "failure_retry",
    "budget",
    "model_usage",
    "provider_usage",
    "latency",
    "cost_accounting",
    "regression",
    "model_comparison",
    "evaluation_coverage",
    "evaluation_status",
)

# The scope a metric was read at.
SCOPE_RUN = "run"
SCOPE_CORPUS = "corpus"
SCOPE_DATABASE = "database"

# The reason a rate with nothing to divide by is not measured.
NO_DENOMINATOR = "no denominator"

# The unit types the spend ledger may carry for a model's tokens.
INPUT_TOKEN_UNIT = "llm_input_tokens"
OUTPUT_TOKEN_UNIT = "llm_output_tokens"

# The tool call statuses that mean the call did not do its work. A partial
# result is not a failure: the tool answered, and said so. SQLite cannot bind
# a list to IN, so the statuses are bound as parameters at each site, using
# one placeholder per status.
FAILED_TOOL_STATUSES = ("failure", "timeout")


class MetricStatus(str, Enum):
    """The state a number is in."""

    measured = "measured"
    not_measured = "not_measured"
    not_applicable = "not_applicable"


class Metric(BaseModel):
    """One number, the state it is in, and the counts behind it.

    A metric that is not measured holds a null value and a reason. A metric
    that is measured and is not a number holds its text in ``detail``, because
    a status, a version and a stage name are facts rather than quantities.
    """

    model_config = ConfigDict(extra="forbid")

    name: str
    group: str
    scope: str
    value: float | int | None
    unit: str
    status: MetricStatus
    reason: str | None = None
    detail: dict[str, Any] = Field(default_factory=dict)


class MetricReport(BaseModel):
    """Every metric family, read off the store in one pass."""

    model_config = ConfigDict(extra="forbid")

    observability_version: str = OBSERVABILITY_VERSION
    run_id: str | None = None
    schema_version: int
    groups: list[str]
    metrics: list[Metric]
    notes: list[str] = Field(default_factory=list)


@dataclass(frozen=True)
class _Scope:
    """What a read is scoped to: one run, or the whole store.

    ``missing`` holds the reason a named run is not in the store, so every
    run-scoped family says the same thing about it.
    """

    run_id: str | None = None
    missing: str | None = None

    @property
    def label(self) -> str:
        """The scope a run-scoped metric of this read carries."""
        return SCOPE_RUN if self.run_id is not None else SCOPE_DATABASE


def collect_metrics(
    conn: sqlite3.Connection, *, run_id: str | None = None
) -> MetricReport:
    """Read the twenty metric families off the store.

    The read never writes, and it never raises because a table is missing or
    empty: a family that could not be measured says so in the reason of its
    metrics. The report holds no timestamp and no random value, so two reads
    of the same database produce the same report.
    """
    scope = _scope_for(conn, run_id)
    metrics: list[Metric] = []
    for family in _FAMILIES:
        metrics.extend(family(conn, scope))
    return MetricReport(
        run_id=run_id,
        schema_version=_schema_version(conn),
        groups=list(GROUPS),
        metrics=metrics,
        notes=_notes(conn, scope),
    )


# --- the families -----------------------------------------------------------


def _run_family(conn: sqlite3.Connection, scope: _Scope) -> list[Metric]:
    """The state of one run: where it is, and how far it got."""
    group = "run"
    label = SCOPE_RUN
    if scope.run_id is None:
        return _unmeasured_row(group, label, _RUN_FIELDS, "no run was selected")
    if not _has_table(conn, "research_runs"):
        return _unmeasured_row(
            group,
            label,
            _RUN_FIELDS,
            "the research_runs table is not in the schema",
        )
    row = conn.execute(
        "SELECT status, started_at, completed_at, progress_percent, "
        "contract_version, model_policy_version, current_stage "
        "FROM research_runs WHERE run_id = ?",
        (scope.run_id,),
    ).fetchone()
    if row is None:
        return _unmeasured_row(group, label, _RUN_FIELDS, scope.missing or "the run is not in the store")
    metrics: list[Metric] = []
    for field, unit in _RUN_FIELDS:
        name = f"{group}.{field}"
        if field == "duration_seconds":
            metrics.append(_run_duration(name, group, label, row))
            continue
        if field == "progress_percent":
            metrics.append(
                _measured(
                    name,
                    group,
                    label,
                    int(row["progress_percent"] or 0),
                    unit,
                    {"progress_percent": int(row["progress_percent"] or 0)},
                )
            )
            continue
        metrics.append(
            _text_metric(
                name,
                group,
                label,
                row[field],
                missing=f"the run leaves {field} null",
            )
        )
    return metrics


def _run_duration(name: str, group: str, label: str, row: sqlite3.Row) -> Metric:
    """How long the run took, from the two stamps it carries."""
    started = _parse_stamp(row["started_at"])
    completed = _parse_stamp(row["completed_at"])
    if row["started_at"] is None or started is None:
        return _unmeasured(
            name, group, label, "the run leaves started_at null", unit="seconds"
        )
    if row["completed_at"] is None or completed is None:
        return _unmeasured(
            name, group, label, "the run leaves completed_at null", unit="seconds"
        )
    seconds = (completed - started).total_seconds()
    if seconds < 0:
        return _unmeasured(
            name,
            group,
            label,
            "the run's completed_at is earlier than its started_at",
            unit="seconds",
        )
    return _measured(
        name,
        group,
        label,
        round(seconds, 3),
        "seconds",
        {"started_at": row["started_at"], "completed_at": row["completed_at"]},
    )


def _stage_family(conn: sqlite3.Connection, scope: _Scope) -> list[Metric]:
    """Every stage of the run: what it did, and whether the gate let it past."""
    group = "stage"
    label = scope.label
    if not _has_table(conn, "research_stage_executions"):
        return [
            _unmeasured(
                f"{group}.stages",
                group,
                label,
                "the research_stage_executions table is not in the schema",
            )
        ]
    if scope.missing is not None:
        return [_unmeasured(f"{group}.stages", group, label, scope.missing)]
    rows = conn.execute(
        "SELECT stage, status, attempt, started_at, finished_at "
        "FROM research_stage_executions "
        "WHERE (:run_id IS NULL OR run_id = :run_id) "
        "ORDER BY stage, attempt",
        {"run_id": scope.run_id},
    ).fetchall()
    stages = sorted({str(row["stage"]) for row in rows})
    metrics = [
        _measured(
            f"{group}.stages",
            group,
            label,
            len(stages),
            "count",
            {"stages": stages, "stage_executions": len(rows)},
        )
    ]
    for stage in stages:
        metrics.extend(_stage_metrics(conn, scope, stage, rows))
    return metrics


def _stage_metrics(
    conn: sqlite3.Connection,
    scope: _Scope,
    stage: str,
    rows: list[sqlite3.Row],
) -> list[Metric]:
    """The metrics of one stage, from the rows that stage wrote."""
    group = "stage"
    label = scope.label
    part = _name_part(stage)
    own = [row for row in rows if row["stage"] == stage]
    latest = own[-1]
    metrics = [
        _text_metric(
            f"{group}.{part}.status",
            group,
            label,
            latest["status"],
            missing=f"the stage execution of {stage} leaves status null",
        ),
        _measured(
            f"{group}.{part}.attempts",
            group,
            label,
            len(own),
            "count",
            {"stage": stage, "latest_attempt": int(latest["attempt"] or 1)},
        ),
    ]
    metrics.append(_stage_duration(f"{group}.{part}.duration_ms", group, label, own))
    metrics.extend(
        _stage_tool_calls(conn, scope, f"{group}.{part}", group, label, stage)
    )
    gate = _stage_gate(conn, scope, stage)
    metrics.append(gate[0])
    metrics.append(gate[1])
    return metrics


def _stage_duration(
    name: str, group: str, label: str, own: list[sqlite3.Row]
) -> Metric:
    """How long the deciding attempt of the stage took."""
    latest = own[-1]
    started = _parse_stamp(latest["started_at"])
    completed = _parse_stamp(latest["finished_at"])
    if latest["started_at"] is None or started is None:
        return _unmeasured(
            name, group, label, "the stage execution leaves started_at null", unit="milliseconds"
        )
    if latest["finished_at"] is None or completed is None:
        return _unmeasured(
            name, group, label, "the stage execution leaves finished_at null", unit="milliseconds"
        )
    millis = (completed - started).total_seconds() * 1000.0
    if millis < 0:
        return _unmeasured(
            name,
            group,
            label,
            "the stage execution's finished_at is earlier than its started_at",
            unit="milliseconds",
        )
    return _measured(
        name,
        group,
        label,
        round(millis, 3),
        "milliseconds",
        {"attempts": len(own), "latest_attempt": int(latest["attempt"] or 1)},
    )


def _stage_tool_calls(
    conn: sqlite3.Connection,
    scope: _Scope,
    name: str,
    group: str,
    label: str,
    stage: str,
) -> list[Metric]:
    """The tool calls of one stage, and the ones that failed."""
    if not _has_table(conn, "tool_calls"):
        reason = "the tool_calls table is not in the schema"
        return [
            _unmeasured(f"{name}.tool_calls", group, label, reason),
            _unmeasured(f"{name}.failed_tool_calls", group, label, reason),
        ]
    params = {"stage": stage, "run_id": scope.run_id, "fs0": "failure", "fs1": "timeout"}
    calls = int(
        _scalar(
            conn,
            "SELECT count(*) FROM tool_calls WHERE stage = :stage "
            "AND (:run_id IS NULL OR run_id = :run_id)",
            params,
        )
        or 0
    )
    failed = int(
        _scalar(
            conn,
            "SELECT count(*) FROM tool_calls "
            "WHERE stage = :stage "
            "AND status IN (:fs0, :fs1) "
            "AND (:run_id IS NULL OR run_id = :run_id)",
            params,
        )
        or 0
    )
    return [
        _measured(
            f"{name}.tool_calls", group, label, calls, "count", {"stage": stage}
        ),
        _measured(
            f"{name}.failed_tool_calls",
            group,
            label,
            failed,
            "count",
            {"stage": stage, "calls": calls},
        ),
    ]


def _stage_gate(
    conn: sqlite3.Connection, scope: _Scope, stage: str
) -> tuple[Metric, Metric]:
    """The gate verdict of one stage: whether it passed, and what it broke."""
    group = "stage"
    label = scope.label
    part = _name_part(stage)
    if not _has_table(conn, "stage_gate_results"):
        reason = "the stage_gate_results table is not in the schema"
        return (
            _unmeasured(f"{group}.{part}.gate_passed", group, label, reason),
            _unmeasured(f"{group}.{part}.violations", group, label, reason),
        )
    row = conn.execute(
        "SELECT passed, violations_json FROM stage_gate_results "
        "WHERE stage = :stage AND (:run_id IS NULL OR run_id = :run_id) "
        "ORDER BY checked_at DESC, gate_result_id DESC LIMIT 1",
        {"stage": stage, "run_id": scope.run_id},
    ).fetchone()
    if row is None:
        reason = f"no gate verdict exists for the stage {stage}"
        return (
            _unmeasured(f"{group}.{part}.gate_passed", group, label, reason),
            _unmeasured(f"{group}.{part}.violations", group, label, reason),
        )
    violations = _read_json_list(row["violations_json"])
    if violations is None:
        reason = "the gate verdict carries no readable violations_json"
        return (
            _unmeasured(f"{group}.{part}.gate_passed", group, label, reason),
            _unmeasured(f"{group}.{part}.violations", group, label, reason),
        )
    return (
        _measured(
            f"{group}.{part}.gate_passed",
            group,
            label,
            int(bool(row["passed"])),
            "boolean",
            {"stage": stage, "passed": bool(row["passed"])},
        ),
        _measured(
            f"{group}.{part}.violations",
            group,
            label,
            len(violations),
            "count",
            {"stage": stage, "violations": violations},
        ),
    )


def _tool_call_family(conn: sqlite3.Connection, scope: _Scope) -> list[Metric]:
    """The tool calls of the scope: how many there were, and how many failed."""
    group = "tool_call"
    label = scope.label
    if not _has_table(conn, "tool_calls"):
        return [
            _unmeasured(
                f"{group}.{name}",
                group,
                label,
                "the tool_calls table is not in the schema",
            )
            for name in (
                "total",
                "failed",
                "failure_rate",
                "retried",
                "result_count_total",
                "duration_mean_ms",
                "duration_p50_ms",
                "duration_max_ms",
            )
        ]
    if scope.missing is not None:
        return [
            _unmeasured(f"{group}.{name}", group, label, scope.missing)
            for name in (
                "total",
                "failed",
                "failure_rate",
                "retried",
                "result_count_total",
                "duration_mean_ms",
                "duration_p50_ms",
                "duration_max_ms",
            )
        ]
    params = {"run_id": scope.run_id, "fs0": "failure", "fs1": "timeout"}
    total = int(_scalar(conn, "SELECT count(*) FROM tool_calls WHERE (:run_id IS NULL OR run_id = :run_id)", params) or 0)
    failed = int(
        _scalar(
            conn,
            "SELECT count(*) FROM tool_calls WHERE status IN (:fs0, :fs1) "
            "AND (:run_id IS NULL OR run_id = :run_id)",
            params,
        )
        or 0
    )
    retried = int(
        _scalar(
            conn,
            "SELECT count(*) FROM tool_calls WHERE attempt > 1 "
            "AND (:run_id IS NULL OR run_id = :run_id)",
            params,
        )
        or 0
    )
    results = int(
        _scalar(
            conn,
            "SELECT coalesce(sum(result_count), 0) FROM tool_calls "
            "WHERE (:run_id IS NULL OR run_id = :run_id)",
            params,
        )
        or 0
    )
    metrics = [
        _measured(f"{group}.total", group, label, total, "count"),
        _measured(f"{group}.failed", group, label, failed, "count"),
        _rate(
            f"{group}.failure_rate",
            group,
            label,
            failed,
            total,
            detail={"failed": failed, "total": total},
        ),
        _measured(f"{group}.retried", group, label, retried, "count"),
        _measured(
            f"{group}.result_count_total", group, label, results, "count"
        ),
    ]
    metrics.extend(
        _latency_metrics(
            conn, group, label, "tool_calls", "duration_ms", params, "duration", "_ms"
        )
    )
    rows = conn.execute(
        "SELECT tool, count(*) AS total, "
        "sum(CASE WHEN status IN (:fs0, :fs1) THEN 1 ELSE 0 END) AS failed "
        "FROM tool_calls WHERE (:run_id IS NULL OR run_id = :run_id) "
        "GROUP BY tool ORDER BY tool",
        params,
    ).fetchall()
    for row in rows:
        part = _name_part(row["tool"])
        tool_total = int(row["total"] or 0)
        tool_failed = int(row["failed"] or 0)
        metrics.append(
            _measured(
                f"{group}.{part}.total",
                group,
                label,
                tool_total,
                "count",
                {"tool": row["tool"]},
            )
        )
        metrics.append(
            _measured(
                f"{group}.{part}.failed",
                group,
                label,
                tool_failed,
                "count",
                {"tool": row["tool"]},
            )
        )
        metrics.append(
            _rate(
                f"{group}.{part}.failure_rate",
                group,
                label,
                tool_failed,
                tool_total,
                detail={"tool": row["tool"], "failed": tool_failed, "total": tool_total},
            )
        )
    return metrics


def _source_health_family(conn: sqlite3.Connection, scope: _Scope) -> list[Metric]:
    """Every source the store knows: what it answered, and what it cost."""
    group = "source_health"
    label = SCOPE_CORPUS
    if not _has_table(conn, "fetches") or not _has_table(conn, "sources"):
        reason = "the fetches or sources table is not in the schema"
        return [_unmeasured(f"{group}.sources", group, label, reason)]
    sources = int(_scalar(conn, "SELECT count(*) FROM sources") or 0)
    metrics = [
        _measured(f"{group}.sources", group, label, sources, "count")
    ]
    rows = conn.execute(
        "SELECT source_id, count(*) AS fetches, "
        "sum(CASE WHEN status IS NULL OR status < 200 OR status >= 300 "
        "THEN 1 ELSE 0 END) AS failures, "
        "max(fetched_at) AS last_seen_at, "
        "count(latency_ms) AS with_latency, "
        "coalesce(avg(latency_ms), 0) AS mean_latency "
        "FROM fetches GROUP BY source_id ORDER BY source_id"
    ).fetchall()
    medians = _grouped_medians(
        conn,
        "SELECT source_id, latency_ms FROM ( "
        "SELECT source_id, latency_ms, "
        "row_number() OVER (PARTITION BY source_id ORDER BY latency_ms) AS rn, "
        "count(*) OVER (PARTITION BY source_id) AS n "
        "FROM fetches WHERE latency_ms IS NOT NULL) "
        "WHERE rn = (n + 1) / 2",
    )
    by_source = {str(row["source_id"]): row["latency_ms"] for row in medians}
    for row in rows:
        part = _name_part(row["source_id"])
        fetches = int(row["fetches"] or 0)
        failures = int(row["failures"] or 0)
        metrics.append(
            _measured(
                f"{group}.{part}.fetches",
                group,
                label,
                fetches,
                "count",
                {"source_id": row["source_id"]},
            )
        )
        metrics.append(
            _measured(
                f"{group}.{part}.failures",
                group,
                label,
                failures,
                "count",
                {"source_id": row["source_id"]},
            )
        )
        metrics.append(
            _rate(
                f"{group}.{part}.success_rate",
                group,
                label,
                fetches - failures,
                fetches,
                detail={"source_id": row["source_id"], "fetches": fetches, "failures": failures},
            )
        )
        metrics.append(
            _text_metric(
                f"{group}.{part}.last_seen_at",
                group,
                label,
                row["last_seen_at"],
                missing=f"no fetch of the source {row['source_id']} carries a fetched_at",
            )
        )
        with_latency = int(row["with_latency"] or 0)
        if with_latency == 0:
            reason = f"every fetch of the source {row['source_id']} leaves latency_ms null"
        else:
            reason = ""
        metrics.append(
            _measured(
                f"{group}.{part}.latency_mean_ms",
                group,
                label,
                round(float(row["mean_latency"]), 3),
                "milliseconds",
                {"source_id": row["source_id"], "measured_fetches": with_latency},
            )
            if with_latency
            else _unmeasured(
                f"{group}.{part}.latency_mean_ms",
                group,
                label,
                reason,
                unit="milliseconds",
            )
        )
        median = by_source.get(str(row["source_id"]))
        metrics.append(
            _measured(
                f"{group}.{part}.latency_p50_ms",
                group,
                label,
                int(median),
                "milliseconds",
                {"source_id": row["source_id"], "measured_fetches": with_latency},
            )
            if median is not None
            else _unmeasured(
                f"{group}.{part}.latency_p50_ms",
                group,
                label,
                reason or NO_DENOMINATOR,
                unit="milliseconds",
            )
        )
    metrics.extend(_collection_outcomes(conn, scope, group))
    return metrics


def _collection_outcomes(
    conn: sqlite3.Connection, scope: _Scope, group: str
) -> list[Metric]:
    """What the collection of each source ended in, by its reason code."""
    label = scope.label
    if not _has_table(conn, "collection_items"):
        return [
            _unmeasured(
                f"{group}.collection_items",
                group,
                label,
                "the collection_items table is not in the schema",
            )
        ]
    if scope.missing is not None:
        return [
            _unmeasured(
                f"{group}.collection_items", group, label, scope.missing
            )
        ]
    rows = conn.execute(
        "SELECT source_id, reason_code, count(*) AS n FROM collection_items "
        "WHERE (:run_id IS NULL OR run_id = :run_id) "
        "GROUP BY source_id, reason_code ORDER BY source_id, reason_code",
        {"run_id": scope.run_id},
    ).fetchall()
    metrics = [
        _measured(
            f"{group}.collection_items",
            group,
            label,
            sum(int(row["n"] or 0) for row in rows),
            "count",
            {"by_reason_code": {str(row["reason_code"]): int(row["n"] or 0) for row in rows}},
        )
    ]
    for row in rows:
        part = _name_part(row["source_id"])
        code = _name_part(row["reason_code"])
        metrics.append(
            _measured(
                f"{group}.{part}.collection_outcome.{code}",
                group,
                label,
                int(row["n"] or 0),
                "count",
                {"source_id": row["source_id"], "reason_code": row["reason_code"]},
            )
        )
    return metrics


def _discovery_yield_family(conn: sqlite3.Connection, scope: _Scope) -> list[Metric]:
    """What the discovery queries found: new, repeated, and from where."""
    group = "discovery_yield"
    label = scope.label
    names = (
        "queries_executed",
        "records",
        "unreadable_records",
        "novel_records",
        "duplicate_yield",
        "source_yield",
        "independent_author_yield",
    )
    if not _has_table(conn, "research_events"):
        return [
            _unmeasured(
                f"{group}.{name}",
                group,
                label,
                "the research_events table is not in the schema",
            )
            for name in names
        ]
    if scope.missing is not None:
        return [
            _unmeasured(f"{group}.{name}", group, label, scope.missing)
            for name in names
        ]
    rows = conn.execute(
        "SELECT event_id, payload_json FROM research_events "
        "WHERE event_type = 'DISCOVERY_QUERY_EXECUTED' "
        "AND (:run_id IS NULL OR run_id = :run_id) "
        "ORDER BY created_at, event_id",
        {"run_id": scope.run_id},
    ).fetchall()
    records: list[dict[str, Any]] = []
    unreadable: list[str] = []
    for row in rows:
        payload = _read_json_object(row["payload_json"])
        if payload is None:
            unreadable.append(str(row["event_id"]))
            continue
        records.append(payload)
    results = sum(int(item.get("result_count") or 0) for item in records)
    novel = sum(_novel_results(item) for item in records)
    duplicate = max(results - novel, 0)
    sources = {str(item.get("source_id")) for item in records if item.get("source_id")}
    sources_with_results = {
        str(item.get("source_id"))
        for item in records
        if item.get("source_id") and int(item.get("result_count") or 0) > 0
    }
    metrics = [
        _measured(
            f"{group}.queries_executed",
            group,
            label,
            len(rows),
            "count",
            {"unreadable": unreadable},
        ),
        _measured(
            f"{group}.records",
            group,
            label,
            len(records),
            "count",
            {"unreadable": unreadable},
        ),
        _measured(
            f"{group}.unreadable_records",
            group,
            label,
            len(unreadable),
            "count",
            {"unreadable": unreadable},
        ),
        _measured(
            f"{group}.novel_records",
            group,
            label,
            sum(1 for item in records if _novel_results(item) > 0),
            "count",
            {"results": results, "novel_results": novel},
        ),
        _rate(
            f"{group}.duplicate_yield",
            group,
            label,
            duplicate,
            results,
            detail={"duplicate_results": duplicate, "results": results},
        ),
        _rate(
            f"{group}.source_yield",
            group,
            label,
            len(sources_with_results),
            len(sources),
            detail={
                "sources": sorted(sources),
                "sources_with_results": sorted(sources_with_results),
            },
        ),
        # A discovery record names the source a search went to and the
        # results it brought. It says nothing about who wrote them, so the
        # author yield of a discovery run cannot be read from it.
        _unmeasured(
            f"{group}.independent_author_yield",
            group,
            label,
            "the discovery record carries no author field",
        ),
    ]
    metrics.extend(_discovery_families(records, group, label))
    return metrics


def _discovery_families(
    records: list[dict[str, Any]], group: str, label: str
) -> list[Metric]:
    """What each query family brought back."""
    metrics: list[Metric] = []
    families = sorted({str(item.get("family")) for item in records if item.get("family")})
    for family in families:
        part = _name_part(family)
        own = [item for item in records if item.get("family") == family]
        results = sum(int(item.get("result_count") or 0) for item in own)
        novel = sum(_novel_results(item) for item in own)
        metrics.append(
            _measured(
                f"{group}.family.{part}.queries",
                group,
                label,
                len(own),
                "count",
                {"family": family},
            )
        )
        metrics.append(
            _measured(
                f"{group}.family.{part}.novel_records",
                group,
                label,
                sum(1 for item in own if _novel_results(item) > 0),
                "count",
                {"family": family, "results": results, "novel_results": novel},
            )
        )
        metrics.append(
            _rate(
                f"{group}.family.{part}.duplicate_yield",
                group,
                label,
                max(results - novel, 0),
                results,
                detail={"family": family, "results": results},
            )
        )
    return metrics


def _novel_results(record: dict[str, Any]) -> int:
    """How many results of a discovery record were new.

    The record stores the novelty as the share of its results that had not
    been seen before, and the count of its results beside it, so the count
    of the new ones is the product of the two. The product is a whole number
    of results, and it is rounded to one because that is what it counts.
    """
    results = int(record.get("result_count") or 0)
    if results <= 0:
        return 0
    novelty = record.get("novelty")
    if not isinstance(novelty, (int, float)):
        return 0
    return max(min(round(float(novelty) * results), results), 0)


def _evidence_yield_family(conn: sqlite3.Connection, scope: _Scope) -> list[Metric]:
    """What the corpus holds: documents, spans, pain points, and the accepted ones."""
    group = "evidence_yield"
    label = SCOPE_CORPUS
    for table in ("documents", "evidence", "pain_points"):
        if not _has_table(conn, table):
            return [
                _unmeasured(
                    f"{group}.{name}",
                    group,
                    label,
                    f"the {table} table is not in the schema",
                )
                for name in (
                    "documents",
                    "evidence_spans",
                    "pain_points",
                    "accepted_pain_points",
                    "accepted_rate",
                    "evidence_per_document",
                )
            ]
    documents = int(_scalar(conn, "SELECT count(*) FROM documents") or 0)
    spans = int(_scalar(conn, "SELECT count(*) FROM evidence") or 0)
    pains = int(_scalar(conn, "SELECT count(*) FROM pain_points") or 0)
    accepted = int(
        _scalar(conn, "SELECT count(*) FROM pain_points WHERE accepted = 1") or 0
    )
    return [
        _measured(f"{group}.documents", group, label, documents, "count"),
        _measured(f"{group}.evidence_spans", group, label, spans, "count"),
        _measured(f"{group}.pain_points", group, label, pains, "count"),
        _measured(f"{group}.accepted_pain_points", group, label, accepted, "count"),
        _rate(
            f"{group}.accepted_rate",
            group,
            label,
            accepted,
            pains,
            detail={"accepted": accepted, "pain_points": pains},
        ),
        _rate(
            f"{group}.evidence_per_document",
            group,
            label,
            spans,
            documents,
            detail={"evidence_spans": spans, "documents": documents},
        ),
    ]


def _duplicate_rate_family(conn: sqlite3.Connection, scope: _Scope) -> list[Metric]:
    """How much of the corpus is the same text twice."""
    group = "duplicate_rate"
    label = SCOPE_CORPUS
    if not _has_table(conn, "documents") or not _has_table(conn, "pain_points"):
        return [
            _unmeasured(
                f"{group}.{name}",
                group,
                label,
                "the documents or pain_points table is not in the schema",
            )
            for name in ("duplicate_text_rate", "duplicate_key_rate", "duplicate_spans")
        ]
    documents = int(_scalar(conn, "SELECT count(*) FROM documents") or 0)
    repeated_text = int(
        _scalar(
            conn,
            "SELECT coalesce(sum(n), 0) FROM ("
            "SELECT count(*) AS n FROM documents GROUP BY text_sha256 "
            "HAVING count(*) > 1)",
        )
        or 0
    )
    keyed = int(
        _scalar(
            conn,
            "SELECT count(*) FROM pain_points WHERE normalized_key IS NOT NULL",
        )
        or 0
    )
    repeated_key = int(
        _scalar(
            conn,
            "SELECT coalesce(sum(n), 0) FROM ("
            "SELECT count(*) AS n FROM pain_points WHERE normalized_key IS NOT NULL "
            "GROUP BY normalized_key HAVING count(*) > 1)",
        )
        or 0
    )
    spans = 0
    if _has_table(conn, "propagation_edges"):
        spans = int(_scalar(conn, "SELECT count(*) FROM propagation_edges") or 0)
    return [
        _rate(
            f"{group}.duplicate_text_rate",
            group,
            label,
            repeated_text,
            documents,
            detail={"repeated_documents": repeated_text, "documents": documents},
        ),
        _rate(
            f"{group}.duplicate_key_rate",
            group,
            label,
            repeated_key,
            keyed,
            detail={"repeated_pain_points": repeated_key, "keyed_pain_points": keyed},
        ),
        _measured(f"{group}.duplicate_spans", group, label, spans, "count"),
    ]


def _independence_rate_family(conn: sqlite3.Connection, scope: _Scope) -> list[Metric]:
    """How many of the people behind the corpus are independent of each other."""
    group = "independence_rate"
    label = SCOPE_CORPUS
    if not _has_table(conn, "authors"):
        return [
            _unmeasured(
                f"{group}.{name}",
                group,
                label,
                "the authors table is not in the schema",
            )
            for name in (
                "authors",
                "independent_authors",
                "independent_author_rate",
                "independent_communities",
                "uncertain_authors",
                "source_concentration",
            )
        ]
    authors = int(_scalar(conn, "SELECT count(*) FROM authors") or 0)
    metrics = [_measured(f"{group}.authors", group, label, authors, "count")]
    report = _independence_report(conn, scope)
    if report is None:
        reason = "no independence report exists"
        for name in (
            "independent_authors",
            "independent_author_rate",
            "independent_communities",
            "uncertain_authors",
            "source_concentration",
        ):
            metrics.append(_unmeasured(f"{group}.{name}", group, label, reason))
        return metrics
    metrics.append(
        _report_number(
            f"{group}.independent_authors",
            group,
            label,
            report,
            "independent_author_count",
            "count",
        )
    )
    independent = _report_number_value(report, "independent_author_count")
    metrics.append(
        _rate(
            f"{group}.independent_author_rate",
            group,
            label,
            independent or 0,
            authors,
            detail={"independent_authors": independent, "authors": authors},
        )
        if independent is not None
        else _unmeasured(
            f"{group}.independent_author_rate",
            group,
            label,
            "the independence report carries no independent_author_count",
        )
    )
    metrics.append(
        _report_number(
            f"{group}.independent_communities",
            group,
            label,
            report,
            "independent_community_count",
            "count",
        )
    )
    metrics.append(
        _report_number(
            f"{group}.uncertain_authors",
            group,
            label,
            report,
            "uncertain_author_count",
            "count",
        )
    )
    metrics.append(
        _report_number(
            f"{group}.source_concentration",
            group,
            label,
            report,
            "source_concentration",
            "rate",
        )
    )
    return metrics


def _independence_report(
    conn: sqlite3.Connection, scope: _Scope
) -> dict[str, Any] | None:
    """The metrics of the independence report that covers the scope."""
    if not _has_table(conn, "independence_reports"):
        return None
    if scope.run_id is None:
        row = conn.execute(
            "SELECT metrics_json FROM independence_reports "
            "ORDER BY created_at DESC, report_id DESC LIMIT 1"
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT metrics_json FROM independence_reports WHERE scope_id = :scope_id "
            "ORDER BY created_at DESC, report_id DESC LIMIT 1",
            {"scope_id": scope.run_id},
        ).fetchone()
    if row is None:
        return None
    return _read_json_object(row["metrics_json"])


def _report_number(
    name: str,
    group: str,
    label: str,
    report: dict[str, Any],
    field: str,
    unit: str,
) -> Metric:
    """One number of an independence report, or the reason it is not there."""
    value = _report_number_value(report, field)
    if value is None:
        return _unmeasured(
            name, group, label, f"the independence report carries no {field}", unit=unit
        )
    return _measured(name, group, label, value, unit, {field: value})


def _report_number_value(report: dict[str, Any], field: str) -> float | None:
    """One number of a report, when it is there and it is a number."""
    value = report.get(field)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value


def _cluster_quality_family(conn: sqlite3.Connection, scope: _Scope) -> list[Metric]:
    """The clusters of the corpus, and the ones that stand alone."""
    group = "cluster_quality"
    label = SCOPE_CORPUS
    for table in ("clustering_runs", "clusters", "cluster_members"):
        if not _has_table(conn, table):
            return [
                _unmeasured(
                    f"{group}.{name}",
                    group,
                    label,
                    f"the {table} table is not in the schema",
                )
                for name in (
                    "clustering_runs",
                    "decisions",
                    "clusters",
                    "mean_cluster_size",
                    "singletons",
                    "singleton_rate",
                    "merge_decisions",
                    "split_decisions",
                )
            ]
    runs = int(_scalar(conn, "SELECT count(*) FROM clustering_runs") or 0)
    decisions = 0
    if _has_table(conn, "cluster_decisions"):
        decisions = int(_scalar(conn, "SELECT count(*) FROM cluster_decisions") or 0)
    active = int(
        _scalar(conn, "SELECT count(*) FROM clusters WHERE status = 'active'") or 0
    )
    merged = int(
        _scalar(conn, "SELECT count(*) FROM clusters WHERE status = 'merged'") or 0
    )
    split = int(
        _scalar(conn, "SELECT count(*) FROM clusters WHERE status = 'split'") or 0
    )
    members = int(
        _scalar(
            conn,
            "SELECT count(*) FROM cluster_members WHERE valid_to IS NULL",
        )
        or 0
    )
    singletons = int(
        _scalar(
            conn,
            "SELECT count(*) FROM ("
            "SELECT cluster_id FROM cluster_members WHERE valid_to IS NULL "
            "GROUP BY cluster_id HAVING count(*) = 1)",
        )
        or 0
    )
    return [
        _measured(f"{group}.clustering_runs", group, label, runs, "count"),
        _measured(f"{group}.decisions", group, label, decisions, "count"),
        _measured(
            f"{group}.clusters",
            group,
            label,
            active,
            "count",
            {"merged": merged, "split": split},
        ),
        _rate(
            f"{group}.mean_cluster_size",
            group,
            label,
            members,
            active,
            detail={"members": members, "clusters": active},
        ),
        _measured(f"{group}.singletons", group, label, singletons, "count"),
        _rate(
            f"{group}.singleton_rate",
            group,
            label,
            singletons,
            active,
            detail={"singletons": singletons, "clusters": active},
        ),
        _measured(f"{group}.merge_decisions", group, label, merged, "count"),
        _measured(f"{group}.split_decisions", group, label, split, "count"),
    ]


def _completion_family(conn: sqlite3.Connection, scope: _Scope) -> list[Metric]:
    """How much of the work finished, stage by stage."""
    group = "completion"
    label = scope.label
    if not _has_table(conn, "research_runs"):
        return [
            _unmeasured(
                f"{group}.{name}",
                group,
                label,
                "the research_runs table is not in the schema",
            )
            for name in (
                "runs_total",
                "runs_completed",
                "completion_rate",
                "stages_total",
                "stages_passed",
                "stage_pass_rate",
                "gate_pass_rate",
            )
        ]
    if scope.missing is not None:
        return [
            _unmeasured(f"{group}.{name}", group, label, scope.missing)
            for name in (
                "runs_total",
                "runs_completed",
                "completion_rate",
                "stages_total",
                "stages_passed",
                "stage_pass_rate",
                "gate_pass_rate",
            )
        ]
    runs_total = int(
        _scalar(
            conn,
            "SELECT count(*) FROM research_runs "
            "WHERE (:run_id IS NULL OR run_id = :run_id)",
            {"run_id": scope.run_id},
        )
        or 0
    )
    runs_completed = int(
        _scalar(
            conn,
            "SELECT count(*) FROM research_runs WHERE status = 'COMPLETED' "
            "AND (:run_id IS NULL OR run_id = :run_id)",
            {"run_id": scope.run_id},
        )
        or 0
    )
    metrics = [
        _measured(f"{group}.runs_total", group, label, runs_total, "count"),
        _measured(f"{group}.runs_completed", group, label, runs_completed, "count"),
        _rate(
            f"{group}.completion_rate",
            group,
            label,
            runs_completed,
            runs_total,
            detail={"completed": runs_completed, "runs": runs_total},
        ),
    ]
    if not _has_table(conn, "stage_gate_results"):
        reason = "the stage_gate_results table is not in the schema"
        for name in (
            "stages_total",
            "stages_passed",
            "stage_pass_rate",
            "gate_pass_rate",
        ):
            metrics.append(_unmeasured(f"{group}.{name}", group, label, reason))
        return metrics
    stages_total = int(
        _scalar(
            conn,
            "SELECT count(*) FROM stage_gate_results WHERE stage IS NOT NULL "
            "AND (:run_id IS NULL OR run_id = :run_id)",
            {"run_id": scope.run_id},
        )
        or 0
    )
    stages_passed = int(
        _scalar(
            conn,
            "SELECT count(*) FROM stage_gate_results WHERE stage IS NOT NULL "
            "AND passed = 1 AND (:run_id IS NULL OR run_id = :run_id)",
            {"run_id": scope.run_id},
        )
        or 0
    )
    gates_total = int(
        _scalar(
            conn,
            "SELECT count(*) FROM stage_gate_results WHERE stage IS NULL "
            "AND (:run_id IS NULL OR run_id = :run_id)",
            {"run_id": scope.run_id},
        )
        or 0
    )
    gates_passed = int(
        _scalar(
            conn,
            "SELECT count(*) FROM stage_gate_results WHERE stage IS NULL "
            "AND passed = 1 AND (:run_id IS NULL OR run_id = :run_id)",
            {"run_id": scope.run_id},
        )
        or 0
    )
    metrics.append(_measured(f"{group}.stages_total", group, label, stages_total, "count"))
    metrics.append(_measured(f"{group}.stages_passed", group, label, stages_passed, "count"))
    metrics.append(
        _rate(
            f"{group}.stage_pass_rate",
            group,
            label,
            stages_passed,
            stages_total,
            detail={"passed": stages_passed, "stages": stages_total},
        )
    )
    metrics.append(
        _rate(
            f"{group}.gate_pass_rate",
            group,
            label,
            gates_passed,
            gates_total,
            detail={"passed": gates_passed, "gates": gates_total},
        )
    )
    return metrics


def _failure_retry_family(conn: sqlite3.Connection, scope: _Scope) -> list[Metric]:
    """What failed, and what was tried again."""
    group = "failure_retry"
    label = scope.label
    names = (
        "tool_call_failures",
        "model_execution_failures",
        "job_failures",
        "dead_jobs",
        "tool_call_retries",
        "job_retries",
        "lease_lost_attempts",
        "failure_rate",
    )
    if not _has_table(conn, "tool_calls"):
        return [_unmeasured(f"{group}.{name}", group, label, "the tool_calls table is not in the schema") for name in names]
    if scope.missing is not None:
        return [_unmeasured(f"{group}.{name}", group, label, scope.missing) for name in names]
    params = {"run_id": scope.run_id, "fs0": "failure", "fs1": "timeout"}
    tool_failures = int(
        _scalar(
            conn,
            "SELECT count(*) FROM tool_calls WHERE status IN (:fs0, :fs1) "
            "AND (:run_id IS NULL OR run_id = :run_id)",
            params,
        )
        or 0
    )
    tool_calls = int(
        _scalar(
            conn,
            "SELECT count(*) FROM tool_calls WHERE (:run_id IS NULL OR run_id = :run_id)",
            params,
        )
        or 0
    )
    tool_retries = int(
        _scalar(
            conn,
            "SELECT count(*) FROM tool_calls WHERE attempt > 1 "
            "AND (:run_id IS NULL OR run_id = :run_id)",
            params,
        )
        or 0
    )
    model_failures = 0
    model_calls = 0
    if _has_table(conn, "model_executions"):
        model_failures = int(
            _scalar(
                conn,
                "SELECT count(*) FROM model_executions WHERE status <> 'ok' "
                "AND (:run_id IS NULL OR run_id = :run_id)",
                params,
            )
            or 0
        )
        model_calls = int(
            _scalar(
                conn,
                "SELECT count(*) FROM model_executions "
                "WHERE (:run_id IS NULL OR run_id = :run_id)",
                params,
            )
            or 0
        )
    job_failures = 0
    dead_jobs = 0
    job_retries = 0
    jobs = 0
    if _has_table(conn, "jobs"):
        job_failures = int(
            _scalar(
                conn,
                "SELECT count(*) FROM jobs WHERE status = 'failed' "
                "AND (:run_id IS NULL OR run_id = :run_id)",
                params,
            )
            or 0
        )
        dead_jobs = int(
            _scalar(
                conn,
                "SELECT count(*) FROM jobs WHERE status = 'dead' "
                "AND (:run_id IS NULL OR run_id = :run_id)",
                params,
            )
            or 0
        )
        job_retries = int(
            _scalar(
                conn,
                "SELECT count(*) FROM jobs WHERE attempts > 1 "
                "AND (:run_id IS NULL OR run_id = :run_id)",
                params,
            )
            or 0
        )
        jobs = int(
            _scalar(
                conn,
                "SELECT count(*) FROM jobs WHERE (:run_id IS NULL OR run_id = :run_id)",
                params,
            )
            or 0
        )
    lease_lost = 0
    if _has_table(conn, "stage_attempts"):
        lease_lost = int(
            _scalar(
                conn,
                "SELECT count(*) FROM stage_attempts WHERE status = 'lease_lost' "
                "AND (:run_id IS NULL OR run_id = :run_id)",
                params,
            )
            or 0
        )
    failures = tool_failures + model_failures + job_failures
    attempts = tool_calls + model_calls + jobs
    return [
        _measured(f"{group}.tool_call_failures", group, label, tool_failures, "count"),
        _measured(
            f"{group}.model_execution_failures", group, label, model_failures, "count"
        ),
        _measured(f"{group}.job_failures", group, label, job_failures, "count"),
        _measured(f"{group}.dead_jobs", group, label, dead_jobs, "count"),
        _measured(f"{group}.tool_call_retries", group, label, tool_retries, "count"),
        _measured(f"{group}.job_retries", group, label, job_retries, "count"),
        _measured(f"{group}.lease_lost_attempts", group, label, lease_lost, "count"),
        _rate(
            f"{group}.failure_rate",
            group,
            label,
            failures,
            attempts,
            detail={"failures": failures, "attempts": attempts},
        ),
    ]


def _budget_family(conn: sqlite3.Connection, scope: _Scope) -> list[Metric]:
    """The spend: how much, and where it went."""
    group = "budget"
    label = scope.label
    names = (
        "entries",
        "units_by_type",
        "cost_micros_total",
        "cost_by_stage",
        "cost_by_provider",
        "cost_by_unit_type",
    )
    if not _has_table(conn, "spend_ledger"):
        return [
            _unmeasured(
                f"{group}.{name}",
                group,
                label,
                "the spend_ledger table is not in the schema",
            )
            for name in names
        ]
    if scope.missing is not None:
        return [_unmeasured(f"{group}.{name}", group, label, scope.missing) for name in names]
    params = {"run_id": scope.run_id, "fs0": "failure", "fs1": "timeout"}
    entries = int(
        _scalar(
            conn,
            "SELECT count(*) FROM spend_ledger WHERE (:run_id IS NULL OR run_id = :run_id)",
            params,
        )
        or 0
    )
    total = int(
        _scalar(
            conn,
            "SELECT coalesce(sum(cost_micros), 0) FROM spend_ledger "
            "WHERE (:run_id IS NULL OR run_id = :run_id)",
            params,
        )
        or 0
    )
    metrics = [
        _measured(f"{group}.entries", group, label, entries, "count"),
        _measured(f"{group}.cost_micros_total", group, label, total, "micros"),
    ]
    for row in _spend_groups(conn, "unit_type", params):
        part = _name_part(row["key"])
        metrics.append(
            _measured(
                f"{group}.units_by_type.{part}",
                group,
                label,
                int(row["units"] or 0),
                "count",
                {"unit_type": row["key"]},
            )
        )
        metrics.append(
            _measured(
                f"{group}.cost_by_unit_type.{part}",
                group,
                label,
                int(row["cost"] or 0),
                "micros",
                {"unit_type": row["key"]},
            )
        )
    for row in _spend_groups(conn, "stage", params):
        part = _name_part(row["key"])
        metrics.append(
            _measured(
                f"{group}.cost_by_stage.{part}",
                group,
                label,
                int(row["cost"] or 0),
                "micros",
                {"stage": row["key"]},
            )
        )
    for row in _spend_groups(conn, "provider", params):
        part = _name_part(row["key"])
        metrics.append(
            _measured(
                f"{group}.cost_by_provider.{part}",
                group,
                label,
                int(row["cost"] or 0),
                "micros",
                {"provider": row["key"]},
            )
        )
    return metrics


def _spend_groups(
    conn: sqlite3.Connection, column: str, params: dict[str, Any]
) -> list[sqlite3.Row]:
    """The spend of the scope, grouped by one of its columns.

    The column is a name this module writes down, never one that comes from
    outside, and it cannot be a bound parameter in SQLite, so the three
    statements are spelled out where they are used.
    """
    if column == "unit_type":
        sql = (
            "SELECT coalesce(unit_type, 'unknown') AS key, "
            "coalesce(sum(units), 0) AS units, coalesce(sum(cost_micros), 0) AS cost "
            "FROM spend_ledger WHERE (:run_id IS NULL OR run_id = :run_id) "
            "GROUP BY key ORDER BY key"
        )
    elif column == "stage":
        sql = (
            "SELECT coalesce(stage, 'unknown') AS key, "
            "coalesce(sum(cost_micros), 0) AS cost "
            "FROM spend_ledger WHERE (:run_id IS NULL OR run_id = :run_id) "
            "GROUP BY key ORDER BY key"
        )
    else:
        sql = (
            "SELECT coalesce(provider, 'unknown') AS key, "
            "coalesce(sum(cost_micros), 0) AS cost "
            "FROM spend_ledger WHERE (:run_id IS NULL OR run_id = :run_id) "
            "GROUP BY key ORDER BY key"
        )
    return conn.execute(sql, params).fetchall()


def _model_usage_family(conn: sqlite3.Connection, scope: _Scope) -> list[Metric]:
    """What each model was asked, and what it failed at."""
    group = "model_usage"
    label = scope.label
    if not _has_table(conn, "model_executions"):
        return [
            _unmeasured(
                f"{group}.models",
                group,
                label,
                "the model_executions table is not in the schema",
            )
        ]
    if scope.missing is not None:
        return [_unmeasured(f"{group}.models", group, label, scope.missing)]
    params = {"run_id": scope.run_id, "fs0": "failure", "fs1": "timeout"}
    rows = conn.execute(
        "SELECT model_id, count(*) AS calls, "
        "sum(CASE WHEN status <> 'ok' THEN 1 ELSE 0 END) AS failures, "
        "sum(CASE WHEN is_fallback = 1 THEN 1 ELSE 0 END) AS fallbacks, "
        "count(latency_ms) AS with_latency, "
        "coalesce(avg(latency_ms), 0) AS mean_latency "
        "FROM model_executions WHERE (:run_id IS NULL OR run_id = :run_id) "
        "GROUP BY model_id ORDER BY model_id",
        params,
    ).fetchall()
    metrics = [
        _measured(f"{group}.models", group, label, len(rows), "count")
    ]
    medians = _grouped_medians(
        conn,
        "SELECT model_id, latency_ms FROM ( "
        "SELECT model_id, latency_ms, "
        "row_number() OVER (PARTITION BY model_id ORDER BY latency_ms) AS rn, "
        "count(*) OVER (PARTITION BY model_id) AS n "
        "FROM model_executions WHERE latency_ms IS NOT NULL "
        "AND (:run_id IS NULL OR run_id = :run_id)) "
        "WHERE rn = (n + 1) / 2",
        params,
    )
    by_model = {str(row["model_id"]): row["latency_ms"] for row in medians}
    for row in rows:
        part = _name_part(row["model_id"])
        with_latency = int(row["with_latency"] or 0)
        metrics.append(
            _measured(
                f"{group}.{part}.calls",
                group,
                label,
                int(row["calls"] or 0),
                "count",
                {"model_id": row["model_id"]},
            )
        )
        metrics.append(
            _measured(
                f"{group}.{part}.failures",
                group,
                label,
                int(row["failures"] or 0),
                "count",
                {"model_id": row["model_id"]},
            )
        )
        metrics.append(
            _measured(
                f"{group}.{part}.fallback_calls",
                group,
                label,
                int(row["fallbacks"] or 0),
                "count",
                {"model_id": row["model_id"]},
            )
        )
        reason = (
            f"every model execution of {row['model_id']} leaves latency_ms null"
            if with_latency == 0
            else ""
        )
        metrics.append(
            _measured(
                f"{group}.{part}.latency_mean_ms",
                group,
                label,
                round(float(row["mean_latency"]), 3),
                "milliseconds",
                {"model_id": row["model_id"], "measured_calls": with_latency},
            )
            if with_latency
            else _unmeasured(
                f"{group}.{part}.latency_mean_ms",
                group,
                label,
                reason,
                unit="milliseconds",
            )
        )
        median = by_model.get(str(row["model_id"]))
        metrics.append(
            _measured(
                f"{group}.{part}.latency_p50_ms",
                group,
                label,
                int(median),
                "milliseconds",
                {"model_id": row["model_id"], "measured_calls": with_latency},
            )
            if median is not None
            else _unmeasured(
                f"{group}.{part}.latency_p50_ms",
                group,
                label,
                reason or NO_DENOMINATOR,
                unit="milliseconds",
            )
        )
    return metrics


def _provider_usage_family(conn: sqlite3.Connection, scope: _Scope) -> list[Metric]:
    """What each provider was asked, and how many models it answered with."""
    group = "provider_usage"
    label = scope.label
    if not _has_table(conn, "model_executions"):
        return [
            _unmeasured(
                f"{group}.providers",
                group,
                label,
                "the model_executions table is not in the schema",
            )
        ]
    if scope.missing is not None:
        return [_unmeasured(f"{group}.providers", group, label, scope.missing)]
    rows = conn.execute(
        "SELECT provider_id, count(*) AS calls, count(DISTINCT model_id) AS models, "
        "sum(CASE WHEN status <> 'ok' THEN 1 ELSE 0 END) AS failures "
        "FROM model_executions WHERE (:run_id IS NULL OR run_id = :run_id) "
        "GROUP BY provider_id ORDER BY provider_id",
        {"run_id": scope.run_id},
    ).fetchall()
    metrics = [_measured(f"{group}.providers", group, label, len(rows), "count")]
    for row in rows:
        part = _name_part(row["provider_id"])
        metrics.append(
            _measured(
                f"{group}.{part}.calls",
                group,
                label,
                int(row["calls"] or 0),
                "count",
                {"provider_id": row["provider_id"]},
            )
        )
        metrics.append(
            _measured(
                f"{group}.{part}.models",
                group,
                label,
                int(row["models"] or 0),
                "count",
                {"provider_id": row["provider_id"]},
            )
        )
        metrics.append(
            _measured(
                f"{group}.{part}.failures",
                group,
                label,
                int(row["failures"] or 0),
                "count",
                {"provider_id": row["provider_id"]},
            )
        )
    return metrics


def _latency_family(conn: sqlite3.Connection, scope: _Scope) -> list[Metric]:
    """How long the calls, the fetches and the stages took."""
    group = "latency"
    metrics: list[Metric] = []
    metrics.extend(
        _latency_metrics(
            conn,
            group,
            scope.label,
            "tool_calls",
            "duration_ms",
            {"run_id": scope.run_id},
            "tool_call_ms",
        )
    )
    metrics.extend(
        _latency_metrics(
            conn,
            group,
            scope.label,
            "model_executions",
            "latency_ms",
            {"run_id": scope.run_id},
            "model_execution_ms",
        )
    )
    metrics.extend(
        _latency_metrics(
            conn, group, SCOPE_CORPUS, "fetches", "latency_ms", None, "fetch_ms"
        )
    )
    metrics.extend(_stage_latency(conn, scope, group))
    return metrics


def _latency_metrics(
    conn: sqlite3.Connection,
    group: str,
    label: str,
    table: str,
    column: str,
    params: dict[str, Any] | None,
    prefix: str,
    suffix: str = "",
) -> list[Metric]:
    """The mean, the median and the maximum of one duration column.

    The table and the column are named by the caller and are constants of
    this module, so they are written into the statement: SQLite cannot bind
    a name. Every value that can vary is a bound parameter.
    """
    name = f"{group}.{prefix}"
    if not _has_table(conn, table):
        reason = f"the {table} table is not in the schema"
        return [
            _unmeasured(
                f"{name}_{part}{suffix}", group, label, reason, unit="milliseconds"
            )
            for part in ("mean", "p50", "max")
        ]
    scope = "" if table == "fetches" else " AND (:run_id IS NULL OR run_id = :run_id)"
    where = f"WHERE {column} IS NOT NULL{scope}"
    values = params or {}
    total = int(
        _scalar(conn, f"SELECT count(*) FROM {table} {where}", values) or 0
    )
    if total == 0:
        reason = f"no row of {table} carries a {column}"
        return [
            _unmeasured(
                f"{name}_{part}{suffix}", group, label, reason, unit="milliseconds"
            )
            for part in ("mean", "p50", "max")
        ]
    mean = float(_scalar(conn, f"SELECT avg({column}) FROM {table} {where}", values) or 0.0)
    maximum = int(_scalar(conn, f"SELECT max({column}) FROM {table} {where}", values) or 0)
    median = _scalar(
        conn,
        f"SELECT {column} FROM (SELECT {column}, "
        f"row_number() OVER (ORDER BY {column}) AS rn, count(*) OVER () AS n "
        f"FROM {table} {where}) WHERE rn = (n + 1) / 2",
        values,
    )
    detail = {"rows": total, "table": table, "column": column}
    return [
        _measured(
            f"{name}_mean{suffix}", group, label, round(mean, 3), "milliseconds", detail
        ),
        _measured(
            f"{name}_p50{suffix}", group, label, int(median), "milliseconds", detail
        ),
        _measured(
            f"{name}_max{suffix}", group, label, maximum, "milliseconds", detail
        ),
    ]


def _stage_latency(
    conn: sqlite3.Connection, scope: _Scope, group: str
) -> list[Metric]:
    """How long the stages took, from the two stamps each one carries."""
    label = scope.label
    prefix = f"{group}.stage_ms"
    if not _has_table(conn, "research_stage_executions"):
        reason = "the research_stage_executions table is not in the schema"
        return [
            _unmeasured(f"{prefix}_{part}", group, label, reason, unit="milliseconds")
            for part in ("mean", "max")
        ]
    rows = conn.execute(
        "SELECT started_at, finished_at FROM research_stage_executions "
        "WHERE (:run_id IS NULL OR run_id = :run_id) "
        "AND started_at IS NOT NULL AND finished_at IS NOT NULL",
        {"run_id": scope.run_id},
    ).fetchall()
    durations: list[float] = []
    for row in rows:
        started = _parse_stamp(row["started_at"])
        completed = _parse_stamp(row["finished_at"])
        if started is None or completed is None:
            continue
        millis = (completed - started).total_seconds() * 1000.0
        if millis >= 0:
            durations.append(millis)
    if not durations:
        reason = (
            "no stage execution carries both a started_at and a finished_at"
            if not rows
            else "every stage execution leaves started_at or finished_at null"
        )
        return [
            _unmeasured(f"{prefix}_{part}", group, label, reason, unit="milliseconds")
            for part in ("mean", "max")
        ]
    durations.sort()
    middle = len(durations) // 2
    median = (
        durations[middle]
        if len(durations) % 2
        else (durations[middle - 1] + durations[middle]) / 2.0
    )
    detail = {"stage_executions": len(durations)}
    return [
        _measured(
            f"{prefix}_mean",
            group,
            label,
            round(sum(durations) / len(durations), 3),
            "milliseconds",
            detail,
        ),
        _measured(
            f"{prefix}_p50",
            group,
            label,
            round(median, 3),
            "milliseconds",
            detail,
        ),
        _measured(
            f"{prefix}_max", group, label, round(durations[-1], 3), "milliseconds", detail
        ),
    ]


def _cost_accounting_family(conn: sqlite3.Connection, scope: _Scope) -> list[Metric]:
    """What the work cost, and what a unit of it cost."""
    group = "cost_accounting"
    label = scope.label
    names = (
        "cost_micros_total",
        "cost_per_tool_call",
        "cost_per_accepted_pain_point",
        "input_tokens",
        "output_tokens",
    )
    if not _has_table(conn, "spend_ledger"):
        return [
            _unmeasured(
                f"{group}.{name}",
                group,
                label,
                "the spend_ledger table is not in the schema",
            )
            for name in names
        ]
    if scope.missing is not None:
        return [_unmeasured(f"{group}.{name}", group, label, scope.missing) for name in names]
    params = {"run_id": scope.run_id, "fs0": "failure", "fs1": "timeout"}
    total = int(
        _scalar(
            conn,
            "SELECT coalesce(sum(cost_micros), 0) FROM spend_ledger "
            "WHERE (:run_id IS NULL OR run_id = :run_id)",
            params,
        )
        or 0
    )
    tool_calls = 0
    if _has_table(conn, "tool_calls"):
        tool_calls = int(
            _scalar(
                conn,
                "SELECT count(*) FROM tool_calls "
                "WHERE (:run_id IS NULL OR run_id = :run_id)",
                params,
            )
            or 0
        )
    accepted = 0
    if _has_table(conn, "pain_points"):
        accepted = int(
            _scalar(conn, "SELECT count(*) FROM pain_points WHERE accepted = 1") or 0
        )
    metrics = [
        _measured(f"{group}.cost_micros_total", group, label, total, "micros"),
        _rate(
            f"{group}.cost_per_tool_call",
            group,
            label,
            total,
            tool_calls,
            unit="micros",
            detail={"cost_micros": total, "tool_calls": tool_calls},
        ),
    ]
    # The pain points of the corpus are not tied to a run, so the cost of one
    # accepted pain point is a number about the whole corpus. Its numerator is
    # the whole ledger for the same reason.
    corpus_cost = int(
        _scalar(conn, "SELECT coalesce(sum(cost_micros), 0) FROM spend_ledger") or 0
    )
    metrics.append(
        _rate(
            f"{group}.cost_per_accepted_pain_point",
            group,
            SCOPE_CORPUS,
            corpus_cost,
            accepted,
            unit="micros",
            detail={"cost_micros": corpus_cost, "accepted_pain_points": accepted},
        )
    )
    metrics.append(_token_metric(conn, group, label, "input_tokens", INPUT_TOKEN_UNIT))
    metrics.append(_token_metric(conn, group, label, "output_tokens", OUTPUT_TOKEN_UNIT))
    return metrics


def _token_metric(
    conn: sqlite3.Connection, group: str, label: str, name: str, unit_type: str
) -> Metric:
    """The tokens a spend row carries, when one carries them.

    No writer puts a token row in the ledger yet, so the number is not
    measured until one does. The unit type is already in the schema, so the
    migration is not what is missing.
    """
    rows = int(
        _scalar(
            conn,
            "SELECT count(*) FROM spend_ledger WHERE unit_type = :unit_type",
            {"unit_type": unit_type},
        )
        or 0
    )
    if rows == 0:
        return _unmeasured(
            f"{group}.{name}",
            group,
            label,
            "no spend row carries a token unit",
            unit="tokens",
            detail={"unit_type": unit_type},
        )
    tokens = int(
        _scalar(
            conn,
            "SELECT coalesce(sum(units), 0) FROM spend_ledger WHERE unit_type = :unit_type",
            {"unit_type": unit_type},
        )
        or 0
    )
    return _measured(
        f"{group}.{name}", group, label, tokens, "tokens", {"rows": rows}
    )


def _regression_family(conn: sqlite3.Connection, scope: _Scope) -> list[Metric]:
    """What the last comparison said, and what it found."""
    group = "regression"
    label = SCOPE_DATABASE
    names = (
        "comparisons_recorded",
        "last_verdict",
        "regressions",
        "improvements",
        "last_comparison",
    )
    if not _has_table(conn, "model_comparisons"):
        return [
            _unmeasured(
                f"{group}.{name}",
                group,
                label,
                "the model_comparisons table is not in the schema",
            )
            for name in names
        ]
    comparisons = int(_scalar(conn, "SELECT count(*) FROM model_comparisons") or 0)
    metrics = [
        _measured(f"{group}.comparisons_recorded", group, label, comparisons, "count")
    ]
    last = _last_comparison(conn)
    if last is None:
        reason = "no comparison has been recorded"
        for name in ("last_verdict", "regressions", "improvements", "last_comparison"):
            metrics.append(_unmeasured(f"{group}.{name}", group, label, reason))
        return metrics
    metrics.append(
        _text_metric(
            f"{group}.last_verdict",
            group,
            label,
            last["verdict"],
            missing="the last comparison carries no verdict",
        )
    )
    metrics.append(
        _measured(
            f"{group}.regressions", group, label, int(last["regressions"] or 0), "count"
        )
    )
    metrics.append(
        _measured(
            f"{group}.improvements", group, label, int(last["improvements"] or 0), "count"
        )
    )
    metrics.append(
        _text_metric(
            f"{group}.last_comparison",
            group,
            label,
            last["comparison_id"],
            missing="the last comparison carries no identifier",
        )
    )
    tasks = _read_json_list(last["tasks_json"]) or []
    for task in tasks:
        if not isinstance(task, dict):
            continue
        name = _name_part(task.get("task"))
        for metric in task.get("metrics") or []:
            if not isinstance(metric, dict):
                continue
            part = _name_part(metric.get("metric"))
            delta = metric.get("delta")
            metric_name = f"{group}.delta.{name}.{part}"
            if not isinstance(delta, (int, float)) or isinstance(delta, bool):
                metrics.append(
                    _unmeasured(
                        metric_name,
                        group,
                        label,
                        f"the comparison holds no delta for {metric.get('metric')}",
                        unit="rate",
                        detail={
                            "baseline": metric.get("baseline"),
                            "candidate": metric.get("candidate"),
                            "status": metric.get("status"),
                        },
                    )
                )
                continue
            metrics.append(
                _measured(
                    metric_name,
                    group,
                    label,
                    float(delta),
                    "rate",
                    {
                        "baseline": metric.get("baseline"),
                        "candidate": metric.get("candidate"),
                        "status": metric.get("status"),
                    },
                )
            )
    return metrics


def _model_comparison_family(conn: sqlite3.Connection, scope: _Scope) -> list[Metric]:
    """The comparisons the store holds, counted by what they say."""
    group = "model_comparison"
    label = SCOPE_DATABASE
    if not _has_table(conn, "model_comparisons"):
        return [
            _unmeasured(
                f"{group}.comparisons",
                group,
                label,
                "the model_comparisons table is not in the schema",
            )
        ]
    comparisons = int(_scalar(conn, "SELECT count(*) FROM model_comparisons") or 0)
    metrics = [
        _measured(f"{group}.comparisons", group, label, comparisons, "count")
    ]
    for row in conn.execute(
        "SELECT verdict, count(*) AS n FROM model_comparisons GROUP BY verdict "
        "ORDER BY verdict"
    ).fetchall():
        part = _name_part(row["verdict"])
        metrics.append(
            _measured(
                f"{group}.verdict.{part}",
                group,
                label,
                int(row["n"] or 0),
                "count",
                {"verdict": row["verdict"]},
            )
        )
    # A provider, a model and a dataset are named twice per comparison, once
    # for the baseline and once for the candidate. The count is the number of
    # comparisons that name the key, so a comparison between two models of the
    # same provider counts once. One metric per key: two metrics with the
    # same name would split one count in two and a reader indexing by name
    # would see the count twice.
    for prefix, columns in (
        ("provider", ("baseline_provider", "candidate_provider")),
        ("model", ("baseline_model", "candidate_model")),
        ("dataset", ("dataset_version",)),
    ):
        selects = " UNION ALL ".join(
            f"SELECT comparison_id, {column} AS key FROM model_comparisons"
            for column in columns
        )
        counts: dict[str, int] = {}
        raw: dict[str, Any] = {}
        for row in conn.execute(
            f"SELECT key, count(DISTINCT comparison_id) AS n FROM ({selects}) "
            "GROUP BY key ORDER BY key"
        ).fetchall():
            key = str(row["key"])
            counts[key] = int(row["n"] or 0)
            raw[key] = row["key"]
        for key in sorted(counts):
            part = _name_part(key)
            metrics.append(
                _measured(
                    f"{group}.{prefix}.{part}",
                    group,
                    label,
                    counts[key],
                    "count",
                    {prefix: raw[key]},
                )
            )
    return metrics


def _evaluation_coverage_family(conn: sqlite3.Connection, scope: _Scope) -> list[Metric]:
    """How much of the benchmark each task of the last comparison covered."""
    group = "evaluation_coverage"
    label = SCOPE_DATABASE
    if not _has_table(conn, "model_comparisons"):
        return [
            _unmeasured(
                f"{group}.tasks",
                group,
                label,
                "the model_comparisons table is not in the schema",
            )
        ]
    last = _last_comparison(conn)
    if last is None:
        reason = "no comparison has been recorded"
        return [_unmeasured(f"{group}.tasks", group, label, reason)]
    tasks = _read_json_list(last["tasks_json"]) or []
    entries = [task for task in tasks if isinstance(task, dict)]
    metrics = [
        _measured(f"{group}.tasks", group, label, len(entries), "count")
    ]
    for task in entries:
        name = _name_part(task.get("task"))
        metrics.append(
            _measured(
                f"{group}.{name}.cases",
                group,
                label,
                int(task.get("cases") or 0),
                "count",
                {"task": task.get("task")},
            )
        )
        metrics.append(
            _measured(
                f"{group}.{name}.labelled",
                group,
                label,
                int(task.get("labelled") or 0),
                "count",
                {"task": task.get("task")},
            )
        )
        metrics.append(
            _measured(
                f"{group}.{name}.scored",
                group,
                label,
                int(task.get("scored") or 0),
                "count",
                {"task": task.get("task")},
            )
        )
        metrics.append(
            _measured(
                f"{group}.{name}.blocked",
                group,
                label,
                int(bool(task.get("blocked"))),
                "boolean",
                {"task": task.get("task"), "blocked": bool(task.get("blocked"))},
            )
        )
    return metrics


def _evaluation_status_family(conn: sqlite3.Connection, scope: _Scope) -> list[Metric]:
    """Which versions the store has seen, and what is still blocked."""
    group = "evaluation_status"
    label = SCOPE_DATABASE
    names = (
        "dataset_versions",
        "evaluator_versions",
        "blocked_tasks",
        "last_comparison",
    )
    if not _has_table(conn, "model_comparisons"):
        return [
            _unmeasured(
                f"{group}.{name}",
                group,
                label,
                "the model_comparisons table is not in the schema",
            )
            for name in names
        ]
    datasets = [
        str(row["dataset_version"])
        for row in conn.execute(
            "SELECT DISTINCT dataset_version FROM model_comparisons "
            "ORDER BY dataset_version"
        ).fetchall()
    ]
    evaluators = [
        str(row["evaluator_version"])
        for row in conn.execute(
            "SELECT DISTINCT evaluator_version FROM model_comparisons "
            "ORDER BY evaluator_version"
        ).fetchall()
    ]
    metrics = [
        _measured(
            f"{group}.dataset_versions",
            group,
            label,
            len(datasets),
            "count",
            {"versions": datasets},
        ),
        _measured(
            f"{group}.evaluator_versions",
            group,
            label,
            len(evaluators),
            "count",
            {"versions": evaluators},
        ),
    ]
    last = _last_comparison(conn)
    if last is None:
        reason = "no comparison has been recorded"
        metrics.append(_unmeasured(f"{group}.blocked_tasks", group, label, reason))
        metrics.append(_unmeasured(f"{group}.last_comparison", group, label, reason))
        return metrics
    tasks = [task for task in (_read_json_list(last["tasks_json"]) or []) if isinstance(task, dict)]
    metrics.append(
        _measured(
            f"{group}.blocked_tasks",
            group,
            label,
            sum(1 for task in tasks if task.get("blocked")),
            "count",
            {"tasks": [str(task.get("task")) for task in tasks]},
        )
    )
    metrics.append(
        _text_metric(
            f"{group}.last_comparison",
            group,
            label,
            last["comparison_id"],
            missing="the last comparison carries no identifier",
        )
    )
    return metrics


# --- the helpers ------------------------------------------------------------


def _scope_for(conn: sqlite3.Connection, run_id: str | None) -> _Scope:
    """The scope of the read, and the reason a named run is not in the store."""
    if run_id is None:
        return _Scope()
    if not _has_table(conn, "research_runs"):
        return _Scope(run_id=run_id, missing="the research_runs table is not in the schema")
    row = conn.execute(
        "SELECT 1 FROM research_runs WHERE run_id = ?", (run_id,)
    ).fetchone()
    if row is None:
        return _Scope(run_id=run_id, missing=f"the run {run_id} does not exist")
    return _Scope(run_id=run_id)


def _notes(conn: sqlite3.Connection, scope: _Scope) -> list[str]:
    """The notes a reader needs before the numbers."""
    notes: list[str] = []
    if scope.missing is not None:
        notes.append(scope.missing)
    if not _has_table(conn, "model_comparisons"):
        notes.append(
            "the store holds no model_comparisons table, so the comparison "
            "families were not measured"
        )
    return notes


def _schema_version(conn: sqlite3.Connection) -> int:
    """The schema version the store carries."""
    row = conn.execute("PRAGMA user_version").fetchone()
    return int(row[0]) if row is not None else 0


def _has_table(conn: sqlite3.Connection, name: str) -> bool:
    """Whether the store holds a table under that name."""
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)
    ).fetchone()
    return row is not None


def _scalar(
    conn: sqlite3.Connection, sql: str, params: dict[str, Any] | None = None
) -> Any:
    """The first column of the first row, or None."""
    row = conn.execute(sql, params or {}).fetchone()
    return None if row is None else row[0]


def _grouped_medians(
    conn: sqlite3.Connection, sql: str, params: dict[str, Any] | None = None
) -> list[sqlite3.Row]:
    """The median row of every group of a windowed query."""
    return conn.execute(sql, params or {}).fetchall()


def _measured(
    name: str,
    group: str,
    scope: str,
    value: float,
    unit: str,
    detail: dict[str, Any] | None = None,
) -> Metric:
    """A metric that was measured."""
    return Metric(
        name=name,
        group=group,
        scope=scope,
        value=value,
        unit=unit,
        status=MetricStatus.measured,
        detail=detail or {},
    )


def _unmeasured(
    name: str,
    group: str,
    scope: str,
    reason: str,
    unit: str = "count",
    detail: dict[str, Any] | None = None,
) -> Metric:
    """A metric that could not be measured, and the reason why."""
    return Metric(
        name=name,
        group=group,
        scope=scope,
        value=None,
        unit=unit,
        status=MetricStatus.not_measured,
        reason=reason,
        detail=detail or {},
    )


def _text_metric(
    name: str,
    group: str,
    scope: str,
    value: Any,
    *,
    missing: str,
) -> Metric:
    """A fact that is not a number: its text travels in the detail."""
    if value is None or value == "":
        return _unmeasured(name, group, scope, missing, unit="text")
    return Metric(
        name=name,
        group=group,
        scope=scope,
        value=None,
        unit="text",
        status=MetricStatus.measured,
        detail={"text": str(value)},
    )


def _rate(
    name: str,
    group: str,
    scope: str,
    numerator: float,
    denominator: float,
    *,
    unit: str = "rate",
    detail: dict[str, Any] | None = None,
) -> Metric:
    """A rate, or the reason it could not be computed.

    A rate with a denominator of zero is never a zero. It is not measured,
    and the reason says there was no denominator.
    """
    if not denominator:
        return _unmeasured(name, group, scope, NO_DENOMINATOR, unit=unit, detail=detail)
    return _measured(
        name,
        group,
        scope,
        round(float(numerator) / float(denominator), 6),
        unit,
        detail,
    )


def _unmeasured_row(
    group: str, scope: str, fields: tuple[tuple[str, str], ...], reason: str
) -> list[Metric]:
    """One unmeasured metric per field of a family."""
    return [
        _unmeasured(f"{group}.{field}", group, scope, reason, unit=unit)
        for field, unit in fields
    ]


def _name_part(value: Any) -> str:
    """A part of a metric name: the case kept, and nothing a name cannot hold."""
    if value is None:
        return "unknown"
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value).strip())
    return cleaned or "unknown"


def _parse_stamp(value: Any) -> datetime | None:
    """A timestamp read from a row, or None when it cannot be read."""
    if not isinstance(value, str) or not value:
        return None
    try:
        stamp = datetime.fromisoformat(value)
    except ValueError:
        return None
    return stamp if stamp.tzinfo is not None else stamp.replace(tzinfo=UTC)


def _read_json_object(payload: Any) -> dict[str, Any] | None:
    """A JSON object read from a row, or None when it cannot be read."""
    if not isinstance(payload, str) or not payload:
        return None
    try:
        value = json.loads(payload)
    except (json.JSONDecodeError, RecursionError):
        return None
    return value if isinstance(value, dict) else None


def _read_json_list(payload: Any) -> list[Any] | None:
    """A JSON list read from a row, or None when it cannot be read."""
    if not isinstance(payload, str) or not payload:
        return None
    try:
        value = json.loads(payload)
    except (json.JSONDecodeError, RecursionError):
        return None
    return value if isinstance(value, list) else None


def _last_comparison(conn: sqlite3.Connection) -> sqlite3.Row | None:
    """The most recent comparison the store holds."""
    return conn.execute(
        "SELECT comparison_id, dataset_version, evaluator_version, verdict, "
        "regressions, improvements, tasks_json, created_at "
        "FROM model_comparisons ORDER BY created_at DESC, comparison_id DESC LIMIT 1"
    ).fetchone()


# The fields of the run family, and the unit each one is read in.
_RUN_FIELDS: tuple[tuple[str, str], ...] = (
    ("status", "text"),
    ("started_at", "text"),
    ("completed_at", "text"),
    ("duration_seconds", "seconds"),
    ("progress_percent", "percent"),
    ("contract_version", "text"),
    ("model_policy_version", "text"),
    ("current_stage", "text"),
)

# The twenty families, in the order the report prints them.
_FAMILIES = (
    _run_family,
    _stage_family,
    _tool_call_family,
    _source_health_family,
    _discovery_yield_family,
    _evidence_yield_family,
    _duplicate_rate_family,
    _independence_rate_family,
    _cluster_quality_family,
    _completion_family,
    _failure_retry_family,
    _budget_family,
    _model_usage_family,
    _provider_usage_family,
    _latency_family,
    _cost_accounting_family,
    _regression_family,
    _model_comparison_family,
    _evaluation_coverage_family,
    _evaluation_status_family,
)

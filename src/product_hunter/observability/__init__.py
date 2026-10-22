"""The observability engine: the metrics, the trace and the comparison record.

This package reads the authoritative store and reports what it finds. It
never writes to the store from the read side, and it never raises because a
table is missing or empty: a number that could not be measured comes back
with a null value and a reason.

The four modules divide the work between them. ``metrics`` owns the twenty
metric families and the three states a number can be in. ``trace`` owns the
union of the four tables that hold the chain of a run. ``comparison`` owns
the ``model_comparisons`` table, the one thing the store could not already
hold. ``report`` owns the markdown rendering of the first two.
"""

from __future__ import annotations

from product_hunter.observability.comparison import (
    ComparisonError,
    ComparisonRecord,
    list_comparisons,
    record_comparison,
)
from product_hunter.observability.metrics import (
    GROUPS,
    OBSERVABILITY_VERSION,
    Metric,
    MetricReport,
    MetricStatus,
    collect_metrics,
)
from product_hunter.observability.report import (
    render_metrics_markdown,
    render_trace_markdown,
    write_metrics_report,
    write_trace_report,
)
from product_hunter.observability.trace import (
    TraceEntry,
    TraceReport,
    collect_trace,
)

__all__ = [
    "GROUPS",
    "OBSERVABILITY_VERSION",
    "ComparisonError",
    "ComparisonRecord",
    "Metric",
    "MetricReport",
    "MetricStatus",
    "TraceEntry",
    "TraceReport",
    "collect_metrics",
    "collect_trace",
    "list_comparisons",
    "record_comparison",
    "render_metrics_markdown",
    "render_trace_markdown",
    "write_metrics_report",
    "write_trace_report",
]

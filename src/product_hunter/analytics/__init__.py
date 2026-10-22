"""The analytical layer: a derived copy of the database and the questions it answers.

SQLite stays the authoritative store. This package mirrors it into DuckDB,
builds the analytical views over the mirror, and answers the named questions
the mirror can answer. The derived state is disposable: it can be deleted
and rebuilt, and the rebuild is deterministic.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

from product_hunter.analytics.ingest import (
    Analytics,
    AnalyticsError,
    IngestReport,
    ReconcileReport,
    TableDrift,
    TableReport,
)
from product_hunter.analytics.queries import QUERIES
from product_hunter.analytics.tables import TableSpec, discover, row_hash, row_key
from product_hunter.analytics.views import VIEW_DDL, ViewReport
from product_hunter.config import ENV_FILE, repo_root

__all__ = [
    "Analytics",
    "AnalyticsError",
    "IngestReport",
    "QUERIES",
    "ReconcileReport",
    "TableDrift",
    "TableReport",
    "TableSpec",
    "VIEW_DDL",
    "ViewReport",
    "analytics_path",
    "discover",
    "row_hash",
    "row_key",
]

# Where the derived state lives when the environment does not say.
DEFAULT_ANALYTICS_NAME = Path("db") / "product_hunter.duckdb"


def analytics_path(root: Path | None = None) -> Path:
    """The DuckDB path: ``PH_ANALYTICS_PATH`` when set, otherwise the default.

    The path follows the pattern of ``config.database_path``, so the derived
    state sits next to the database it is derived from.
    """
    base = Path(root) if root is not None else repo_root()
    load_dotenv(base / ENV_FILE, override=False)
    raw = os.environ.get("PH_ANALYTICS_PATH")
    if raw:
        path = Path(raw).expanduser()
        if not path.is_absolute():
            path = base / path
        return path.resolve()
    return (base / DEFAULT_ANALYTICS_NAME).resolve()

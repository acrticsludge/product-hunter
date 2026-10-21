"""The model routing state: catalogues, inventory, bindings and executions.

The inventory is append-only in spirit. A model that leaves the catalogue
keeps its row, moves to ``missing`` and gets a ``retired_at``, so a model id
that ran last month is still resolvable next month. This module takes plain
objects in and hands plain objects back.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from product_hunter.fetch import utcnow
from product_hunter.llm.catalog import ModelCatalog
from product_hunter.llm.routing import ModelExecution, ModelSelection

MAX_ERROR_CHARS = 2000


@dataclass(frozen=True)
class CatalogRecord:
    """One catalogue snapshot that was seen."""

    catalog_version: str
    source: str
    server_version: str | None
    model_count: int
    discovered_at: str


@dataclass(frozen=True)
class InventoryRow:
    """One model the catalogue reported, now or in the past."""

    provider_id: str
    model_id: str
    name: str | None
    family: str | None
    status: str | None
    enabled: bool | None
    availability: str
    source: str | None
    first_seen_at: str
    last_seen_at: str
    retired_at: str | None
    catalog_version: str | None


@dataclass(frozen=True)
class ExecutionRow:
    """One model call, with the provenance that explains it."""

    execution_id: str
    run_id: str | None
    stage: str | None
    role: str
    provider_id: str
    model_id: str
    selection_source: str
    fallback_index: int
    is_fallback: bool
    catalog_version: str | None
    prompt_version: str | None
    config_version: str | None
    latency_ms: int
    status: str
    error: str | None
    created_at: str


def record_catalog(
    conn: sqlite3.Connection,
    catalog: ModelCatalog,
    *,
    now: datetime | None = None,
) -> CatalogRecord:
    """Write a catalogue snapshot and refresh the inventory, in one transaction.

    Every model the catalogue holds is marked alive again and stamped with the
    time it was seen. Every model the catalogue no longer holds is retired:
    its row survives, its availability becomes ``missing`` and it gets a
    ``retired_at``.
    """
    now_iso = (now or utcnow()).isoformat()
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            """
            INSERT INTO model_catalogs (
                catalog_version, source, server_version, model_count, discovered_at
            ) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(catalog_version) DO UPDATE SET
                source = excluded.source,
                server_version = excluded.server_version,
                model_count = excluded.model_count,
                discovered_at = excluded.discovered_at
            """,
            (
                catalog.catalog_version,
                catalog.source,
                catalog.server_version,
                len(catalog.entries),
                catalog.discovered_at.isoformat(),
            ),
        )
        conn.execute(
            """
            UPDATE model_inventory
            SET availability = 'missing', retired_at = ?
            WHERE retired_at IS NULL
            """,
            (now_iso,),
        )
        for entry in catalog.entries:
            conn.execute(
                """
                INSERT INTO model_inventory (
                    provider_id, model_id, name, family, status, enabled,
                    availability, source, first_seen_at, last_seen_at,
                    retired_at, catalog_version
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?)
                ON CONFLICT(provider_id, model_id) DO UPDATE SET
                    name = excluded.name,
                    family = excluded.family,
                    status = excluded.status,
                    enabled = excluded.enabled,
                    availability = excluded.availability,
                    source = excluded.source,
                    last_seen_at = excluded.last_seen_at,
                    retired_at = NULL,
                    catalog_version = excluded.catalog_version
                """,
                (
                    entry.identity.provider_id,
                    entry.identity.model_id,
                    entry.name,
                    entry.family,
                    entry.status,
                    None if entry.enabled is None else int(entry.enabled),
                    entry.availability.value,
                    entry.source,
                    now_iso,
                    now_iso,
                    catalog.catalog_version,
                ),
            )
        conn.execute("COMMIT")
    except BaseException:
        _rollback(conn)
        raise
    return CatalogRecord(
        catalog_version=catalog.catalog_version,
        source=catalog.source,
        server_version=catalog.server_version,
        model_count=len(catalog.entries),
        discovered_at=catalog.discovered_at.isoformat(),
    )


def record_selection(
    conn: sqlite3.Connection,
    selection: ModelSelection,
    *,
    config_version: str,
    now: datetime | None = None,
) -> None:
    """Write the model a role resolved to, at its position in the chain."""
    conn.execute(
        """
        INSERT INTO model_role_bindings (
            role, position, provider_id, model_id, selection_source,
            catalog_version, config_version, selected_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(role, position) DO UPDATE SET
            provider_id = excluded.provider_id,
            model_id = excluded.model_id,
            selection_source = excluded.selection_source,
            catalog_version = excluded.catalog_version,
            config_version = excluded.config_version,
            selected_at = excluded.selected_at
        """,
        (
            selection.role.value,
            selection.fallback_index,
            selection.identity.provider_id,
            selection.identity.model_id,
            selection.selection_source.value,
            selection.catalog_version,
            config_version,
            (now or utcnow()).isoformat(),
        ),
    )


def record_execution(conn: sqlite3.Connection, execution: ModelExecution) -> None:
    """Write one model call and the provenance that explains it.

    The write is an upsert on the execution id: a run imported twice leaves
    one row per call, and the second import repairs a row the first one left
    behind. The error text is cut to ``MAX_ERROR_CHARS``: the row keeps the
    beginning of the message, not a page of stderr.
    """
    conn.execute(
        """
        INSERT INTO model_executions (
            execution_id, run_id, stage, role, provider_id, model_id,
            selection_source, fallback_index, is_fallback, catalog_version,
            prompt_version, config_version, latency_ms, status, error, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(execution_id) DO UPDATE SET
            run_id = excluded.run_id,
            stage = excluded.stage,
            role = excluded.role,
            provider_id = excluded.provider_id,
            model_id = excluded.model_id,
            selection_source = excluded.selection_source,
            fallback_index = excluded.fallback_index,
            is_fallback = excluded.is_fallback,
            catalog_version = excluded.catalog_version,
            prompt_version = excluded.prompt_version,
            config_version = excluded.config_version,
            latency_ms = excluded.latency_ms,
            status = excluded.status,
            error = excluded.error,
            created_at = excluded.created_at
        """,
        (
            execution.execution_id,
            execution.run_id,
            execution.stage,
            execution.role.value,
            execution.provider_id,
            execution.model_id,
            execution.selection_source.value,
            execution.fallback_index,
            int(execution.is_fallback),
            execution.catalog_version,
            execution.prompt_version,
            execution.config_version,
            execution.latency_ms,
            execution.status,
            _bounded_error(execution.error),
            execution.created_at,
        ),
    )


def record_model_executions(
    conn: sqlite3.Connection, executions: Iterable[ModelExecution]
) -> int:
    """Write every model call of a run, in one transaction.

    The calls of a run are all there or none of them are. The run's role
    binding is a separate write: the import of the run itself is another
    transaction, so the two happen in sequence and not in one. The rows are
    derivable from the run artifacts, so a run whose binding write failed is
    recoverable by importing it again.
    """
    rows = list(executions)
    try:
        conn.execute("BEGIN IMMEDIATE")
        for execution in rows:
            record_execution(conn, execution)
        conn.execute("COMMIT")
    except BaseException:
        _rollback(conn)
        raise
    return len(rows)


def list_inventory(
    conn: sqlite3.Connection,
    *,
    availability: str | None = None,
) -> list[InventoryRow]:
    """Every model the inventory holds, oldest first."""
    if availability is None:
        rows = conn.execute(
            "SELECT * FROM model_inventory ORDER BY provider_id, model_id"
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM model_inventory WHERE availability = ? "
            "ORDER BY provider_id, model_id",
            (availability,),
        ).fetchall()
    return [_row_to_inventory(row) for row in rows]


def list_executions(
    conn: sqlite3.Connection,
    *,
    run_id: str | None = None,
    role: str | None = None,
) -> list[ExecutionRow]:
    """The recorded model calls, oldest first."""
    sql = "SELECT * FROM model_executions"
    clauses: list[str] = []
    params: list[object] = []
    if run_id is not None:
        clauses.append("run_id = ?")
        params.append(run_id)
    if role is not None:
        clauses.append("role = ?")
        params.append(role)
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY created_at, execution_id"
    rows = conn.execute(sql, params).fetchall()
    return [_row_to_execution(row) for row in rows]


def _row_to_inventory(row: sqlite3.Row) -> InventoryRow:
    enabled = row["enabled"]
    return InventoryRow(
        provider_id=row["provider_id"],
        model_id=row["model_id"],
        name=row["name"],
        family=row["family"],
        status=row["status"],
        enabled=None if enabled is None else bool(enabled),
        availability=row["availability"],
        source=row["source"],
        first_seen_at=row["first_seen_at"],
        last_seen_at=row["last_seen_at"],
        retired_at=row["retired_at"],
        catalog_version=row["catalog_version"],
    )


def _row_to_execution(row: sqlite3.Row) -> ExecutionRow:
    return ExecutionRow(
        execution_id=row["execution_id"],
        run_id=row["run_id"],
        stage=row["stage"],
        role=row["role"],
        provider_id=row["provider_id"],
        model_id=row["model_id"],
        selection_source=row["selection_source"],
        fallback_index=int(row["fallback_index"]),
        is_fallback=bool(row["is_fallback"]),
        catalog_version=row["catalog_version"],
        prompt_version=row["prompt_version"],
        config_version=row["config_version"],
        latency_ms=int(row["latency_ms"]),
        status=row["status"],
        error=row["error"],
        created_at=row["created_at"],
    )


def _bounded_error(error: str | None) -> str | None:
    """The error text, cut to the length the audit row holds."""
    return None if error is None else error[:MAX_ERROR_CHARS]


def _rollback(conn: sqlite3.Connection) -> None:
    try:
        conn.execute("ROLLBACK")
    except sqlite3.Error:
        pass

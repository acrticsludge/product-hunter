"""The model routing state: catalogues, inventory, bindings and executions.

The inventory is append-only in spirit. A model that leaves the catalogue
keeps its row, moves to ``missing`` and gets a ``retired_at``, so a model id
that ran last month is still resolvable next month. The inventory only moves
forward: a catalogue older than a row knows nothing about that row. This
module takes plain objects in and hands plain objects back.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone

from product_hunter.fetch import utcnow
from product_hunter.llm.catalog import ModelCatalog, ModelEntry
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


@dataclass(frozen=True)
class RunProvenance:
    """The routing state one run recorded, to be written with the run."""

    catalog: ModelCatalog | None = None
    selections: tuple[ModelSelection, ...] = ()
    executions: tuple[ModelExecution, ...] = ()


@contextmanager
def _owned_transaction(conn: sqlite3.Connection) -> Iterator[None]:
    """The transaction a write owns, or the caller's own.

    A connection that is already in a transaction keeps it: the write joins
    the caller's transaction and leaves the commit to the caller. A
    connection that is free gets one, and the write commits it, or rolls it
    back when the write fails.
    """
    owns = not conn.in_transaction
    try:
        if owns:
            conn.execute("BEGIN IMMEDIATE")
        yield
        if owns:
            conn.execute("COMMIT")
    except BaseException:
        if owns:
            _rollback(conn)
        raise


def record_catalog(
    conn: sqlite3.Connection,
    catalog: ModelCatalog,
) -> CatalogRecord:
    """Write a catalogue snapshot and refresh the inventory, in one transaction.

    The inventory only moves forward. Every stamp comes from the catalogue's
    ``discovered_at``, which says when the model was seen, and not from the
    import time, which says when the row was written. A model the catalogue
    holds is alive again; a model it does not hold is retired, but only when
    the row was last seen before the catalogue was discovered. A catalogue
    older than a row knows nothing about that row, so it neither retires it
    nor moves its stamps backwards.

    A connection that is already in a transaction keeps it: the catalogue is
    written inside the caller's transaction and is committed with it.
    """
    discovered_at = _aware(catalog.discovered_at)
    discovered_iso = discovered_at.isoformat()
    with _owned_transaction(conn):
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
                discovered_iso,
            ),
        )
        _retire_absent_models(conn, catalog, discovered_at, discovered_iso)
        for entry in catalog.entries:
            _record_entry(conn, entry, catalog, discovered_at, discovered_iso)
    return CatalogRecord(
        catalog_version=catalog.catalog_version,
        source=catalog.source,
        server_version=catalog.server_version,
        model_count=len(catalog.entries),
        discovered_at=discovered_iso,
    )


def _retire_absent_models(
    conn: sqlite3.Connection,
    catalog: ModelCatalog,
    discovered_at: datetime,
    discovered_iso: str,
) -> None:
    """Retire the models the catalogue does not hold, and only the old ones.

    A row is retired when the catalogue does not mention it and the row was
    last seen before the catalogue was discovered. A row a newer catalogue
    saw stays alive: the older catalogue knows nothing about it. A row that
    is already retired keeps its first ``retired_at``.
    """
    held = {
        (entry.identity.provider_id, entry.identity.model_id)
        for entry in catalog.entries
    }
    rows = conn.execute(
        "SELECT provider_id, model_id, last_seen_at FROM model_inventory "
        "WHERE retired_at IS NULL"
    ).fetchall()
    for row in rows:
        if (row["provider_id"], row["model_id"]) in held:
            continue
        seen = _seen_at(row["last_seen_at"])
        if seen is None or seen >= discovered_at:
            # The row is newer than the catalogue, or its stamp cannot be
            # read: the catalogue knows nothing about it.
            continue
        conn.execute(
            "UPDATE model_inventory SET availability = 'missing', retired_at = ? "
            "WHERE provider_id = ? AND model_id = ?",
            (discovered_iso, row["provider_id"], row["model_id"]),
        )


def _record_entry(
    conn: sqlite3.Connection,
    entry: ModelEntry,
    catalog: ModelCatalog,
    discovered_at: datetime,
    discovered_iso: str,
) -> None:
    """Write one model of the catalogue, without moving the row backwards.

    ``first_seen_at`` keeps its meaning: the insert sets it and no update
    overwrites it, so a model that leaves the catalogue and comes back keeps
    the moment it was first seen.
    """
    row = conn.execute(
        "SELECT last_seen_at FROM model_inventory "
        "WHERE provider_id = ? AND model_id = ?",
        (entry.identity.provider_id, entry.identity.model_id),
    ).fetchone()
    if row is not None:
        seen = _seen_at(row["last_seen_at"])
        if seen is None or seen > discovered_at:
            # A catalogue older than the row knows nothing about it, so the
            # row keeps what the newer catalogue said.
            return
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
            discovered_iso,
            discovered_iso,
            catalog.catalog_version,
        ),
    )


def record_selection(
    conn: sqlite3.Connection,
    selection: ModelSelection,
    *,
    config_version: str,
    now: datetime | None = None,
) -> None:
    """Write the model a role resolved to, at its position in the chain.

    A connection that is already in a transaction keeps it, so the binding
    can be written with the run it belongs to.
    """
    with _owned_transaction(conn):
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

    A connection that is already in a transaction keeps it, so the call can
    be written with the run it belongs to.
    """
    with _owned_transaction(conn):
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

    The calls of a run are all there or none of them are. A connection that
    is already in a transaction keeps it, so the calls can be written with
    the run they belong to.
    """
    rows = list(executions)
    with _owned_transaction(conn):
        for execution in rows:
            record_execution(conn, execution)
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


def _aware(moment: datetime) -> datetime:
    """A moment with a timezone, so it can be compared with another one."""
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment


def _seen_at(stamp: str) -> datetime | None:
    """The moment a row was last seen, or None when the stamp cannot be read.

    A stamp without a timezone cannot be compared with a catalogue's, so it
    is treated as unreadable.
    """
    try:
        seen = datetime.fromisoformat(stamp)
    except (TypeError, ValueError):
        return None
    return seen if seen.tzinfo is not None else None


def _rollback(conn: sqlite3.Connection) -> None:
    try:
        conn.execute("ROLLBACK")
    except sqlite3.Error:
        pass

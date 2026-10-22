"""The operation's spend, kept in the database so a crash buys no more.

The budget model is the one the research layer already has: the same
dimensions, the same limits, the same decisions, the same refusal. This
module puts a ledger under it, so the spend of an operation survives the
process that made it. A laptop that dies at eighty percent of its budget
comes back at eighty percent, not at zero.
"""

from __future__ import annotations

import math
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager

from product_hunter.config import AutonomyConfig
from product_hunter.db.connection import rollback, transaction, utc_now
from product_hunter.research.budget import (
    BudgetDecision,
    BudgetDimension,
    BudgetExceeded,
    BudgetSpec,
    BudgetTracker,
)

OPERATION_SCOPE = "operation"
STAGE_SCOPE_PREFIX = "stage:"
SOURCE_SCOPE_PREFIX = "source:"


def build_budget_spec(config: AutonomyConfig) -> BudgetSpec:
    """The operation budget of the config, in the budget model.

    The contract's scopes are the operation, a stage and a source, and the
    budget model calls them per run, per stage and per source.
    """
    budget = config.budget
    per_source: dict[str, float] = {}
    for source_id, limits in budget.per_source.items():
        limit = limits.get(BudgetDimension.source_requests_per_source.value)
        if limit is not None:
            per_source[source_id] = float(limit)
    return BudgetSpec(
        version=str(config.version),
        per_run={name: float(value) for name, value in budget.per_operation.items()},
        per_stage={
            stage: {name: float(value) for name, value in limits.items()}
            for stage, limits in budget.per_stage.items()
        },
        per_source=per_source,
    )


class OperationBudget:
    """The budget of one operation, backed by the operation_budget table."""

    def __init__(
        self, conn: sqlite3.Connection, operation_id: str, spec: BudgetSpec
    ) -> None:
        self.conn = conn
        self.operation_id = operation_id
        self.spec = spec
        self._tracker = BudgetTracker(spec)
        self._load()

    def check(
        self,
        dimension: str | BudgetDimension,
        quantity: float,
        *,
        stage: str | None = None,
        source_id: str | None = None,
    ) -> BudgetDecision:
        """Whether a call of this quantity may start.

        The decision is the one the budget model gives, with the same fields
        and the same reasons, so a caller that knows the model knows this.
        """
        return self._tracker.check(
            dimension, quantity, stage=stage, source_id=source_id
        )

    def record(
        self,
        dimension: str | BudgetDimension,
        quantity: float,
        *,
        stage: str | None = None,
        source_id: str | None = None,
    ) -> None:
        """Record the spend, then refuse the call that went over the limit.

        The spend stays written when the refusal comes, because the call
        already happened. The ledger is right either way.
        """
        try:
            self._tracker.record(
                dimension, quantity, stage=stage, source_id=source_id
            )
        except BudgetExceeded:
            self._persist(dimension, stage=stage, source_id=source_id)
            raise
        self._persist(dimension, stage=stage, source_id=source_id)

    def spent(
        self,
        dimension: str | BudgetDimension,
        *,
        stage: str | None = None,
        source_id: str | None = None,
    ) -> float:
        """The spend in one scope: the source, the stage, or the operation."""
        return self._tracker.spent(dimension, stage=stage, source_id=source_id)

    def exhausted(self) -> list[str]:
        """The dimensions that are over their limit."""
        return self._tracker.exceeded_against(self.spec)

    def _persist(
        self,
        dimension: str | BudgetDimension,
        *,
        stage: str | None,
        source_id: str | None,
    ) -> None:
        """Write the spend of the operation and of the scopes it names.

        The rows of one spend are written in one transaction, so a spend
        that touches three scopes cannot land half written.
        """
        name = _dimension_name(dimension)
        now_iso = utc_now().isoformat()
        rows: list[tuple[str, str, float]] = [
            (OPERATION_SCOPE, name, self._tracker.spent(name))
        ]
        if stage is not None:
            rows.append(
                (
                    f"{STAGE_SCOPE_PREFIX}{stage}",
                    name,
                    self._tracker.spent(name, stage=stage),
                )
            )
        if source_id is not None:
            rows.append(
                (
                    f"{SOURCE_SCOPE_PREFIX}{source_id}",
                    name,
                    self._tracker.spent(name, source_id=source_id),
                )
            )
        with transaction(self.conn):
            for scope, dim, spent in rows:
                self.conn.execute(
                    """
                    INSERT INTO operation_budget (
                        operation_id, scope, dimension, spent, updated_at
                    ) VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(operation_id, scope, dimension) DO UPDATE SET
                        spent = excluded.spent, updated_at = excluded.updated_at
                    """,
                    (self.operation_id, scope, dim, float(spent), now_iso),
                )

    def _load(self) -> None:
        """Rebuild the tracker from the ledger.

        The breakdown rows are replayed first, because the tracker counts a
        spend in the scope it names and in the operation beside it. The
        operation row then corrects the total to the one that was written
        down, so a spend that named a stage and a source is counted once.
        """
        rows = self.conn.execute(
            "SELECT scope, dimension, spent FROM operation_budget "
            "WHERE operation_id = ?",
            (self.operation_id,),
        ).fetchall()
        totals: dict[str, float] = {}
        breakdowns: list[tuple[str, str, float]] = []
        for row in rows:
            scope = str(row["scope"])
            dimension = str(row["dimension"])
            amount = float(row["spent"])
            if not math.isfinite(amount):
                # An amount that is not a number is not a spend, so it is left
                # out rather than poisoning every total.
                continue
            if scope == OPERATION_SCOPE:
                totals[dimension] = amount
                continue
            breakdowns.append((scope, dimension, amount))
        for scope, dimension, amount in breakdowns:
            stage, source_id = _split_scope(scope)
            self._replay(dimension, amount, stage=stage, source_id=source_id)
        for dimension, amount in totals.items():
            correction = amount - self._tracker.spent(dimension)
            if correction:
                self._replay(dimension, correction)

    def _replay(
        self,
        dimension: str,
        amount: float,
        *,
        stage: str | None = None,
        source_id: str | None = None,
    ) -> None:
        """Put a spend back into the tracker, overrun and all.

        A spend that is over its limit is refused by the tracker, and the
        refusal is the state the operation is already in. The spend is
        written before the refusal, so it is kept either way.
        """
        try:
            self._tracker.record(dimension, amount, stage=stage, source_id=source_id)
        except BudgetExceeded:
            pass


# The transaction helper moved to product_hunter.db.connection. The budget ledger
# imports it there: the engine imports this module, so importing from the engine
# would close a cycle, but the database module sits below both.


def _split_scope(scope: str) -> tuple[str | None, str | None]:
    """The stage or the source a scope name stands for."""
    if scope.startswith(STAGE_SCOPE_PREFIX):
        return scope[len(STAGE_SCOPE_PREFIX) :], None
    if scope.startswith(SOURCE_SCOPE_PREFIX):
        return None, scope[len(SOURCE_SCOPE_PREFIX) :]
    return None, None


def _dimension_name(dimension: str | BudgetDimension) -> str:
    """The plain name of a dimension."""
    if isinstance(dimension, BudgetDimension):
        return dimension.value
    return str(dimension)

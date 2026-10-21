"""The investigation's budget: the wave 1 tracker with per-area caps.

The tracker is scoped by the investigation stage, so the contract's per-stage
limit for the investigation binds against the investigation as a whole and the
per-run limit binds across everything. The per-area caps are this module's own,
kept on a second ledger, because the tracker has one stage scope per call and
the areas are a second scope. The run total is recorded once: the tracker's run
ledger is keyed by dimension alone. A cap that is not positive is refused: it
would switch the dimension off.
"""

from __future__ import annotations

import math

from product_hunter.research.budget import (
    BudgetDecision,
    BudgetDimension,
    BudgetExceeded,
    BudgetTracker,
    NonFiniteAmount,
)

# The scope a per-area cap is reported under.
PER_AREA_SCOPE = "per_area"

# The stage scope the tracker's spend is recorded under, so the contract's
# per-stage limit for the investigation binds against the investigation.
INVESTIGATION_STAGE = "investigation"


def _dimension_name(dimension: str | BudgetDimension) -> str:
    """Return the plain string name of a dimension."""
    if isinstance(dimension, BudgetDimension):
        return dimension.value
    return str(dimension)


def _area_name(area: str) -> str:
    """Return the plain name of a research area."""
    return str(getattr(area, "value", area))


def _cap_value(limit: float) -> float:
    """Return a usable per-area cap, refusing one that would switch it off."""
    cap = float(limit)
    if not math.isfinite(cap):
        # A non-finite cap makes every comparison against it false, so the
        # area would run without a limit while claiming to have one.
        raise ValueError("a per-area cap must be a finite number")
    if cap <= 0:
        raise ValueError("a per-area cap must be positive")
    return cap


class InvestigationBudget:
    """The wave 1 tracker with the investigation's per-area caps on top.

    The tracker is scoped by the investigation stage, so the contract's
    per-stage limit for the investigation binds against the investigation as a
    whole. The per-area caps are kept here, because the tracker has one stage
    scope per call and the areas are a second scope. The run total is
    recorded once: the tracker's run ledger is keyed by dimension alone.
    """

    def __init__(
        self,
        tracker: BudgetTracker,
        per_area: dict[str, dict[str, float]] | None = None,
        *,
        stage: str = INVESTIGATION_STAGE,
    ) -> None:
        self.tracker = tracker
        self.stage = stage
        self.per_area: dict[str, dict[str, float]] = {}
        self._caps: dict[tuple[str, str], float] = {}
        self._area_spend: dict[tuple[str, str], float] = {}
        for area, limits in (per_area or {}).items():
            stage_name = _area_name(area)
            for dimension, limit in limits.items():
                name = _dimension_name(dimension)
                cap = _cap_value(limit)
                self.per_area.setdefault(stage_name, {})[name] = cap
                self._caps[(stage_name, name)] = cap

    def check(
        self, area: str, dimension: str | BudgetDimension, quantity: float
    ) -> BudgetDecision:
        """Report whether a call of this quantity may start in this area."""
        name = _dimension_name(dimension)
        area_name = _area_name(area)
        tracker_decision = self.tracker.check(name, quantity, stage=self.stage)
        limit = self._caps.get((area_name, name))
        if limit is None:
            return tracker_decision

        spent = self._area_spend.get((area_name, name), 0.0)
        area_exceeded = spent + quantity > limit
        area_decision = BudgetDecision(
            allowed=not area_exceeded,
            dimension=name,
            limit=limit,
            spent=spent,
            requested=quantity,
            scope=PER_AREA_SCOPE,
            reason=(
                f"{spent} + {quantity} would exceed the {PER_AREA_SCOPE} limit of {limit}"
                if area_exceeded
                else "within budget"
            ),
        )
        if not tracker_decision.allowed:
            # The stricter of the run limit and the area cap wins.
            if area_decision.allowed or (tracker_decision.limit or 0.0) < limit:
                return tracker_decision
            return area_decision
        if not area_decision.allowed:
            return area_decision
        if tracker_decision.limit is not None and tracker_decision.limit < limit:
            return tracker_decision
        return area_decision

    def guard(
        self, area: str, dimension: str | BudgetDimension, quantity: float
    ) -> None:
        """Raise when the call would exceed a hard limit."""
        decision = self.check(area, dimension, quantity)
        if decision.allowed:
            return
        if decision.limit is None:
            # A refused amount is not an overrun, so it is not reported as one.
            raise NonFiniteAmount(decision.reason)
        raise BudgetExceeded(
            decision.dimension,
            decision.limit,
            decision.spent,
            decision.requested,
            decision.scope,
        )

    def record(
        self, area: str, dimension: str | BudgetDimension, quantity: float
    ) -> None:
        """Record the spend in this area, then raise if it left the cap behind."""
        name = _dimension_name(dimension)
        area_name = _area_name(area)
        amount = float(quantity)
        if not math.isfinite(amount):
            raise NonFiniteAmount("the amount is not a finite number")
        # The tracker records the spend once, under the investigation stage.
        self.tracker.record(name, amount, stage=self.stage)
        key = (area_name, name)
        self._area_spend[key] = self._area_spend.get(key, 0.0) + amount
        limit = self._caps.get(key)
        if limit is None:
            return
        if self._area_spend[key] > limit:
            raise BudgetExceeded(name, limit, self._area_spend[key], amount, PER_AREA_SCOPE)

    def spent(self, area: str, dimension: str | BudgetDimension) -> float:
        """Return what this area has spent on this dimension."""
        return self._area_spend.get((_area_name(area), _dimension_name(dimension)), 0.0)

    def exceeded(self) -> list[str]:
        """Return the names of the dimensions that are over a limit."""
        names = set(self.tracker.exceeded())
        for (area_name, name), limit in self._caps.items():
            if self._area_spend.get((area_name, name), 0.0) > limit:
                names.add(name)
        return sorted(names)

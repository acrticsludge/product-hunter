"""Budget dimensions, limits, and the tracker that blocks the next call.

The rule is literal: a call that would exceed a hard limit is not started.
The tracker never calls a tool and never spends anything; it answers whether
a call may start and records what was spent.
"""

from __future__ import annotations

import math
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator


class BudgetDimension(str, Enum):
    wall_time_seconds = "wall_time_seconds"
    tool_calls_total = "tool_calls_total"
    search_queries = "search_queries"
    fetch_requests = "fetch_requests"
    browser_minutes = "browser_minutes"
    crawl_items = "crawl_items"
    llm_input_tokens = "llm_input_tokens"
    llm_output_tokens = "llm_output_tokens"
    model_calls = "model_calls"
    subq_calls = "subq_calls"
    embedding_items = "embedding_items"
    usd_cost = "usd_cost"
    source_requests_per_source = "source_requests_per_source"
    saturation_threshold = "saturation_threshold"


BUDGET_DIMENSION_NAMES = frozenset(dimension.value for dimension in BudgetDimension)

# The per-source limit is the source's own request cap.
_SOURCE_DIMENSION = BudgetDimension.source_requests_per_source.value

# A non-finite amount makes every comparison against it false, so a NaN spend
# would switch the dimension off. The reason is the same for every such value
# and never quotes it.
_NON_FINITE_AMOUNT_REASON = "the amount is not a finite number"


def _dimension_name(dimension: str | BudgetDimension) -> str:
    """Return the plain string name of a dimension."""
    if isinstance(dimension, BudgetDimension):
        return dimension.value
    return str(dimension)


def _normalize_keys(value: object) -> object:
    """Rewrite dimension keys to their plain names, keeping unknown names as they are."""
    if not isinstance(value, dict):
        return value
    return {_dimension_name(key): item for key, item in value.items()}


class BudgetSpec(BaseModel):
    """The hard limits and soft fractions a run is held to.

    Limits are plain numbers here. The contract validation is what rejects a
    non-positive limit, so a bad limit is a contract error and not a crash.
    """

    model_config = ConfigDict(extra="forbid")

    version: str = Field(min_length=1)
    per_run: dict[str, float] = Field(default_factory=dict)
    per_stage: dict[str, dict[str, float]] = Field(default_factory=dict)
    per_source: dict[str, float] = Field(default_factory=dict)
    soft_thresholds: dict[str, float] = Field(default_factory=dict)

    @field_validator("per_run", "soft_thresholds", mode="before")
    @classmethod
    def _flat_dimension_keys(cls, value: object) -> object:
        return _normalize_keys(value)

    @field_validator("per_stage", mode="before")
    @classmethod
    def _stage_dimension_keys(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        return {stage: _normalize_keys(limits) for stage, limits in value.items()}


class BudgetDecision(BaseModel):
    """The answer to "may this call start?"."""

    model_config = ConfigDict(extra="forbid")

    allowed: bool
    dimension: str
    limit: float | None = None
    spent: float = 0.0
    requested: float = 0.0
    scope: str = "none"
    reason: str = ""


class BudgetExceeded(Exception):
    """A call would exceed a hard limit and must not start."""

    def __init__(
        self,
        dimension: str,
        limit: float,
        spent: float,
        requested: float,
        scope: str = "per_run",
    ) -> None:
        self.dimension = dimension
        self.limit = limit
        self.spent = spent
        self.requested = requested
        self.scope = scope
        super().__init__(
            f"{dimension} budget exceeded ({scope}): "
            f"{spent} + {requested} > {limit}"
        )


class NonFiniteAmount(ValueError):
    """A non-finite amount is not a spend, so it is refused.

    A refused amount is not a budget overrun, so it does not raise
    BudgetExceeded: a caller that guards a call would miss it. It is a
    ValueError, so a caller that already catches one keeps catching it, and
    this class gives a caller that wants to tell the two refusals apart
    something to catch.
    """


class BudgetTracker:
    """Tracks spend against a budget spec and refuses the call that would break it."""

    def __init__(self, spec: BudgetSpec) -> None:
        self.spec = spec
        self._run: dict[str, float] = {}
        self._stage: dict[tuple[str, str], float] = {}
        self._source: dict[tuple[str, str], float] = {}

    def check(
        self,
        dimension: str | BudgetDimension,
        quantity: float,
        stage: str | None = None,
        source_id: str | None = None,
    ) -> BudgetDecision:
        """Report whether a call of this quantity may start."""
        name = _dimension_name(dimension)
        amount = float(quantity)
        if not math.isfinite(amount):
            # The amount is refused, and the value is not carried into the
            # decision: a non-finite quantity has no meaningful request.
            return BudgetDecision(
                allowed=False,
                dimension=name,
                reason=_NON_FINITE_AMOUNT_REASON,
            )
        scopes = self._applicable_scopes(name, stage, source_id)
        if not scopes:
            return BudgetDecision(
                allowed=True,
                dimension=name,
                requested=amount,
                reason="no limit applies to this dimension",
            )
        strictest = min(scopes, key=lambda scope: scope[1])
        for scope, limit, spent in scopes:
            if spent + amount > limit:
                return BudgetDecision(
                    allowed=False,
                    dimension=name,
                    limit=limit,
                    spent=spent,
                    requested=amount,
                    scope=scope,
                    reason=(
                        f"{spent} + {amount} would exceed the {scope} limit of {limit}"
                    ),
                )
        return BudgetDecision(
            allowed=True,
            dimension=name,
            limit=strictest[1],
            spent=strictest[2],
            requested=amount,
            scope=strictest[0],
            reason="within budget",
        )

    def guard(
        self,
        dimension: str | BudgetDimension,
        quantity: float,
        stage: str | None = None,
        source_id: str | None = None,
    ) -> None:
        """Raise when the call would exceed a hard limit."""
        amount = float(quantity)
        if not math.isfinite(amount):
            # The refusal is not a budget overrun, so it is not reported as
            # one: a non-finite quantity is not a number of calls.
            raise NonFiniteAmount(_NON_FINITE_AMOUNT_REASON)
        decision = self.check(dimension, amount, stage=stage, source_id=source_id)
        if not decision.allowed:
            raise BudgetExceeded(
                decision.dimension,
                decision.limit or 0.0,
                decision.spent,
                decision.requested,
                decision.scope,
            )

    def record(
        self,
        dimension: str | BudgetDimension,
        quantity: float,
        stage: str | None = None,
        source_id: str | None = None,
    ) -> None:
        """Record spend, then raise if the spend left the budget behind.

        The spend stays recorded: the call already happened, and the gate
        reports the overrun from the recorded total. A non-finite amount is
        the one spend that is refused and not recorded.
        """
        name = _dimension_name(dimension)
        amount = float(quantity)
        if not math.isfinite(amount):
            raise NonFiniteAmount(_NON_FINITE_AMOUNT_REASON)
        self._run[name] = self._run.get(name, 0.0) + amount
        if stage is not None:
            key = (stage, name)
            self._stage[key] = self._stage.get(key, 0.0) + amount
        if source_id is not None:
            key = (source_id, name)
            self._source[key] = self._source.get(key, 0.0) + amount
        for scope, limit, spent in self._applicable_scopes(name, stage, source_id):
            if spent > limit:
                raise BudgetExceeded(name, limit, spent, amount, scope)

    def spent(
        self,
        dimension: str | BudgetDimension,
        stage: str | None = None,
        source_id: str | None = None,
    ) -> float:
        """Return the spend in one scope: the source, the stage, or the run."""
        name = _dimension_name(dimension)
        if source_id is not None:
            return self._source.get((source_id, name), 0.0)
        if stage is not None:
            return self._stage.get((stage, name), 0.0)
        return self._run.get(name, 0.0)

    def measured(
        self, dimension: str | BudgetDimension, stage: str | None = None
    ) -> bool:
        """Return whether a record exists for that (stage, dimension).

        Presence is the measurement claim: a stage that records a dimension,
        even at zero, has measured it. Absence is the gap, and zero is a
        measured zero, not an unmeasured one.
        """
        name = _dimension_name(dimension)
        if stage is not None:
            return (stage, name) in self._stage
        return name in self._run

    def exceeded(self) -> list[str]:
        """Return the names of the dimensions that are over their limit."""
        return self.exceeded_against(self.spec)

    def exceeded_against(self, spec: BudgetSpec) -> list[str]:
        """Return the names of the dimensions that are over the given limits.

        The contract's budgets are the authority for the limit and the tracker
        is the ledger for the spend, so the gate measures the overrun against
        the contract and not against the tracker's own spec. A tracker built
        from a looser spec cannot widen the budget the contract declares.
        """
        names: set[str] = set()
        for name, limit in spec.per_run.items():
            if self._run.get(name, 0.0) > limit:
                names.add(name)
        for stage, limits in spec.per_stage.items():
            for name, limit in limits.items():
                if self._stage.get((stage, name), 0.0) > limit:
                    names.add(name)
        for source_id, limit in spec.per_source.items():
            spent = self._source.get((source_id, _SOURCE_DIMENSION), 0.0)
            if spent > limit:
                names.add(_SOURCE_DIMENSION)
        return sorted(names)

    def _applicable_scopes(
        self, name: str, stage: str | None, source_id: str | None
    ) -> list[tuple[str, float, float]]:
        """Return the (scope, limit, spend) triples that apply to a call."""
        scopes: list[tuple[str, float, float]] = []
        if name in self.spec.per_run:
            scopes.append(("per_run", self.spec.per_run[name], self._run.get(name, 0.0)))
        stage_limits = self.spec.per_stage.get(stage or "")
        if stage_limits and name in stage_limits:
            scopes.append(
                ("per_stage", stage_limits[name], self._stage.get((stage or "", name), 0.0))
            )
        if source_id is not None and name == _SOURCE_DIMENSION and source_id in self.spec.per_source:
            scopes.append(
                (
                    "per_source",
                    self.spec.per_source[source_id],
                    self._source.get((source_id, name), 0.0),
                )
            )
        return scopes

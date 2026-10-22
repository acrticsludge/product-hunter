"""The discovery budget: query counts per run, per source and per family.

The budget is a limit and a stopping signal. It counts the queries a run
may issue against one source, one family, and the run as a whole, and it
counts the spend. When a research contract tracker is supplied, the tracker
is consulted as well, so the contract's limits still bind: this budget does
not replace it.

This module never calls a tool and never spends anything on its own. It
answers whether a query may start and counts what was spent. A search that
goes over a limit, in queries or in spend, is counted and then reported as
an overrun, because the call already happened.
"""

from __future__ import annotations

import math

from product_hunter.discovery.families import QueryFamily
from product_hunter.research.budget import (
    BudgetDecision,
    BudgetExceeded,
    BudgetTracker,
    NonFiniteAmount,
)

# The dimension names the discovery budget reports under. They are its own,
# and they are not the contract's dimensions: the contract's tracker reports
# under the contract's names.
DIMENSION_QUERIES = "discovery_queries"
DIMENSION_COST = "discovery_cost"

# The stage the discovery queries run in, which is the stage the contract
# tracker is consulted for.
DISCOVERY_STAGE = "discovery"

# The default limits. They are module constants: a budget is code, not a
# file that is read at runtime.
DEFAULT_MAX_QUERIES_PER_RUN = 40
DEFAULT_MAX_QUERIES_PER_SOURCE = 20
DEFAULT_MAX_QUERIES_PER_FAMILY = 8
DEFAULT_MAX_COST_PER_RUN = 5.0

# The reason a non-finite amount is refused. The research budget refuses the
# same amount with the same reason: every comparison against a non-finite
# value is false, so the dimension would switch itself off.
NON_FINITE_AMOUNT_REASON = "the amount is not a finite number"

# The reason a negative amount is refused. A spend is not a credit.
NEGATIVE_AMOUNT_REASON = "the amount must not be negative"


class DiscoveryBudget:
    """The query counts and the spend a discovery run may use."""

    def __init__(
        self,
        *,
        max_queries_per_run: int = DEFAULT_MAX_QUERIES_PER_RUN,
        max_queries_per_source: int = DEFAULT_MAX_QUERIES_PER_SOURCE,
        max_queries_per_family: int = DEFAULT_MAX_QUERIES_PER_FAMILY,
        max_cost_per_run: float = DEFAULT_MAX_COST_PER_RUN,
        tracker: BudgetTracker | None = None,
    ) -> None:
        self.max_queries_per_run = max_queries_per_run
        self.max_queries_per_source = max_queries_per_source
        self.max_queries_per_family = max_queries_per_family
        self.max_cost_per_run = max_cost_per_run
        self.tracker = tracker
        self._queries = 0
        self._cost = 0.0
        self._queries_by_source: dict[str, int] = {}
        self._queries_by_family: dict[str, int] = {}

    @property
    def queries(self) -> int:
        """The number of queries the run has issued."""
        return self._queries

    @property
    def cost(self) -> float:
        """The spend of the run, in dollars."""
        return self._cost

    @property
    def queries_by_source(self) -> dict[str, int]:
        """The number of queries issued against each source."""
        return dict(self._queries_by_source)

    @property
    def queries_by_family(self) -> dict[str, int]:
        """The number of queries issued in each family."""
        return dict(self._queries_by_family)

    def check(
        self,
        source_id: str,
        family: QueryFamily,
        cost: float = 0.0,
        *,
        pending_total: int = 0,
        pending_source: int = 0,
        pending_family: int = 0,
    ) -> BudgetDecision:
        """Report whether a query of this source, family and cost may start.

        The decision names the scope that refused: ``per_run``,
        ``per_source``, ``per_family`` or ``per_cost``.

        The pending counts are the queries the frontier has admitted and not
        yet run. They are added to the spend, because an admitted query is
        one the run is committed to: a limit that only bites after the query
        has run is no limit at all.
        """
        amount = float(cost)
        if not math.isfinite(amount):
            # The amount is refused, and it is not carried into the
            # decision: a non-finite amount has no meaningful request.
            return BudgetDecision(
                allowed=False,
                dimension=DIMENSION_COST,
                scope="per_cost",
                reason=NON_FINITE_AMOUNT_REASON,
            )
        if amount < 0.0:
            return BudgetDecision(
                allowed=False,
                dimension=DIMENSION_COST,
                scope="per_cost",
                reason=NEGATIVE_AMOUNT_REASON,
            )
        if self._queries + pending_total >= self.max_queries_per_run:
            return self._refusal(
                DIMENSION_QUERIES,
                "per_run",
                float(self.max_queries_per_run),
                float(self._queries + pending_total),
                1.0,
            )
        source_queries = self._queries_by_source.get(source_id, 0)
        if source_queries + pending_source >= self.max_queries_per_source:
            return self._refusal(
                DIMENSION_QUERIES,
                "per_source",
                float(self.max_queries_per_source),
                float(source_queries + pending_source),
                1.0,
            )
        family_queries = self._queries_by_family.get(family.value, 0)
        if family_queries + pending_family >= self.max_queries_per_family:
            return self._refusal(
                DIMENSION_QUERIES,
                "per_family",
                float(self.max_queries_per_family),
                float(family_queries + pending_family),
                1.0,
            )
        if self._cost + amount > self.max_cost_per_run:
            return self._refusal(
                DIMENSION_COST,
                "per_cost",
                float(self.max_cost_per_run),
                self._cost,
                amount,
            )
        if self.tracker is not None:
            decision = self.tracker.check(
                "search_queries",
                1.0 + pending_total,
                stage=DISCOVERY_STAGE,
                source_id=source_id,
            )
            if not decision.allowed:
                return decision
        return BudgetDecision(
            allowed=True,
            dimension=DIMENSION_QUERIES,
            limit=float(self.max_queries_per_run),
            spent=float(self._queries),
            requested=1.0,
            scope="none",
            reason="within budget",
        )

    def record(
        self,
        source_id: str,
        family: QueryFamily,
        cost: float = 0.0,
    ) -> None:
        """Count a query and the spend it made.

        A non-finite or negative amount is refused and not recorded: it is
        not a spend, and recording it would switch the cost limit off.

        The spend is recorded and then the overrun is reported, the way the
        research budget reports it: the call already happened, so the limit
        is measured from the recorded total. Every limit is a limit, so the
        query counts are reported the same way the spend is: the run, the
        source and the family count, and the cost. The overrun is the house
        ``BudgetExceeded``, so a caller that already catches one keeps
        catching it.
        """
        amount = float(cost)
        if not math.isfinite(amount):
            raise NonFiniteAmount(NON_FINITE_AMOUNT_REASON)
        if amount < 0.0:
            raise ValueError(NEGATIVE_AMOUNT_REASON)
        self._queries += 1
        self._queries_by_source[source_id] = (
            self._queries_by_source.get(source_id, 0) + 1
        )
        self._queries_by_family[family.value] = (
            self._queries_by_family.get(family.value, 0) + 1
        )
        self._cost += amount
        if self.tracker is not None:
            self.tracker.record(
                "search_queries",
                1.0,
                stage=DISCOVERY_STAGE,
                source_id=source_id,
            )
        source_queries = self._queries_by_source[source_id]
        family_queries = self._queries_by_family[family.value]
        if self._queries > self.max_queries_per_run:
            raise BudgetExceeded(
                DIMENSION_QUERIES,
                float(self.max_queries_per_run),
                float(self._queries),
                1.0,
                "per_run",
            )
        if source_queries > self.max_queries_per_source:
            raise BudgetExceeded(
                DIMENSION_QUERIES,
                float(self.max_queries_per_source),
                float(source_queries),
                1.0,
                "per_source",
            )
        if family_queries > self.max_queries_per_family:
            raise BudgetExceeded(
                DIMENSION_QUERIES,
                float(self.max_queries_per_family),
                float(family_queries),
                1.0,
                "per_family",
            )
        if self._cost > self.max_cost_per_run:
            raise BudgetExceeded(
                DIMENSION_COST,
                self.max_cost_per_run,
                self._cost,
                amount,
                "per_cost",
            )

    def exceeded(self) -> list[str]:
        """Return the names of the limits the run is over.

        The names are the scopes of the limits, the same names the house
        ``BudgetExceeded`` carries: ``per_run``, ``per_source``,
        ``per_family`` and ``per_cost``. The list is empty while the run is
        within every limit, and it is measured from the recorded totals, so
        it says whether the run is over and not whether a call was refused
        in this session.

        The contract tracker is not measured here: it is the contract's
        ledger, and the contract's gate reports an overrun against the
        contract's own limits.
        """
        names: set[str] = set()
        if self._queries > self.max_queries_per_run:
            names.add("per_run")
        if self._cost > self.max_cost_per_run:
            names.add("per_cost")
        if any(
            count > self.max_queries_per_source
            for count in self._queries_by_source.values()
        ):
            names.add("per_source")
        if any(
            count > self.max_queries_per_family
            for count in self._queries_by_family.values()
        ):
            names.add("per_family")
        return sorted(names)

    def state(self) -> dict[str, object]:
        """Return the JSON-ready state of the budget, for a checkpoint."""
        return {
            "max_queries_per_run": self.max_queries_per_run,
            "max_queries_per_source": self.max_queries_per_source,
            "max_queries_per_family": self.max_queries_per_family,
            "max_cost_per_run": self.max_cost_per_run,
            "queries": self._queries,
            "cost": self._cost,
            "queries_by_source": dict(self._queries_by_source),
            "queries_by_family": dict(self._queries_by_family),
        }

    @classmethod
    def from_state(
        cls,
        state: dict[str, object],
        *,
        tracker: BudgetTracker | None = None,
    ) -> DiscoveryBudget:
        """Rebuild a budget from the state a checkpoint holds.

        The contract tracker is not part of the state: a tracker is a ledger
        that lives in the run, not a number that can be written into a
        checkpoint. The caller hands it back, so the contract's limits still
        bind after a resume.
        """
        budget = cls(
            max_queries_per_run=int(state.get("max_queries_per_run", DEFAULT_MAX_QUERIES_PER_RUN)),
            max_queries_per_source=int(
                state.get("max_queries_per_source", DEFAULT_MAX_QUERIES_PER_SOURCE)
            ),
            max_queries_per_family=int(
                state.get("max_queries_per_family", DEFAULT_MAX_QUERIES_PER_FAMILY)
            ),
            max_cost_per_run=float(
                state.get("max_cost_per_run", DEFAULT_MAX_COST_PER_RUN)
            ),
            tracker=tracker,
        )
        budget._queries = int(state.get("queries", 0))
        budget._cost = float(state.get("cost", 0.0))
        budget._queries_by_source = {
            str(key): int(value)
            for key, value in dict(state.get("queries_by_source", {})).items()
        }
        budget._queries_by_family = {
            str(key): int(value)
            for key, value in dict(state.get("queries_by_family", {})).items()
        }
        return budget

    def _refusal(
        self,
        dimension: str,
        scope: str,
        limit: float,
        spent: float,
        requested: float,
    ) -> BudgetDecision:
        """Build the decision that refuses a query."""
        return BudgetDecision(
            allowed=False,
            dimension=dimension,
            limit=limit,
            spent=spent,
            requested=requested,
            scope=scope,
            reason=(
                f"{spent} + {requested} would exceed the {scope} limit of {limit}"
            ),
        )

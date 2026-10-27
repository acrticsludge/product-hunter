"""The query frontier: the searches a run may still make, and their yield.

The frontier holds the pending queries, the result identities it has already
seen, and the counters that measure what came back. It computes the novelty
of a result set, in one place, and it decides when the run has enough.

The rule that shapes this module: a broken search tool is not saturation. A
failure never counts towards family coverage, never advances the low-yield
counter, and leaves the frontier blocked. A blocked frontier never reports
saturation.

This module never calls a search endpoint. It is handed result identities
and it reasons about them.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator

from product_hunter.discovery.budget import (
    DIMENSION_COST,
    DISCOVERY_STAGE,
    DiscoveryBudget,
)
from product_hunter.discovery.families import (
    DERIVED_FAMILIES,
    FAMILY_SPECS,
    MANDATORY_FAMILIES,
    SUBJECT_SLOT,
    QueryFamily,
    assign_family,
)
from product_hunter.discovery.queries import (
    ASSIGNED_BY_WORKER,
    Query,
    generate_queries,
    is_duplicate,
    normalize_query,
    query_id,
)
from product_hunter.discovery.records import DiscoveryRecord, DiscoveryStatus
from product_hunter.fetch import utcnow
from product_hunter.research.budget import BudgetExceeded, BudgetTracker
from product_hunter.research.evidence import ZeroResultOutcome

# The five reasons a frontier stops for.
REASON_FAMILY_COVERAGE = "family_coverage"
REASON_SOURCE_COVERAGE = "source_coverage"
REASON_BUDGET_EXHAUSTED = "budget_exhausted"
REASON_DIMINISHING_NOVELTY = "diminishing_novelty"
REASON_REPEATED_RESULTS = "repeated_results"


def _family_of(query: Query) -> QueryFamily | None:
    """Return the family the code owns for a query, or nothing.

    A query that carries a template id comes from that template, so it
    takes the family of the template, and only when its text really is a
    rendering of that template: a caller that names a template it did not
    render does not get to pick the family. A query without a template id
    is of unknown origin, so it takes the family the marker table gives
    its text, and never a family that needs a normalized problem: a raw
    objective cannot name a derived family.

    The function fails closed: a family it cannot work out is nothing, and
    the caller refuses the query.
    """
    if not query.template_id:
        family = assign_family(query.text)[0]
        if FAMILY_SPECS[family].requires_normalized_problem:
            return None
        return family
    family_name, _, index_text = query.template_id.rpartition("-")
    try:
        family = QueryFamily(family_name)
        index = int(index_text)
    except ValueError:
        return None
    templates = FAMILY_SPECS[family].templates
    if not 0 <= index < len(templates):
        return None
    pattern = re.escape(templates[index]).replace(re.escape(SUBJECT_SLOT), ".+?")
    # The subject slot matches across a line break, so a subject that
    # carries one is still a rendering of the template.
    if re.fullmatch(pattern, query.text, re.DOTALL) is None:
        return None
    return family


class SaturationPolicy(BaseModel):
    """The thresholds that decide when the frontier has enough."""

    model_config = ConfigDict(extra="forbid")

    min_novelty: float = Field(default=0.1, ge=0.0, le=1.0)
    max_consecutive_low_yield_batches: int = Field(default=3, ge=1)
    max_duplicate_rate: float = Field(default=0.9, ge=0.0, le=1.0)
    required_families: tuple[QueryFamily, ...] = MANDATORY_FAMILIES
    queries_per_family: int = Field(default=4, ge=1)
    near_duplicate_threshold: float = Field(default=0.9, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _queries_per_family_fits_the_templates(self) -> SaturationPolicy:
        """Refuse a policy that asks for more variants than the templates hold.

        A family that holds fewer templates than the policy asks for can
        never reach its own coverage, and the frontier would stall with no
        reason. The policy is refused instead.
        """
        if not self.required_families:
            return self
        fewest = min(
            len(FAMILY_SPECS[family].templates) for family in self.required_families
        )
        if self.queries_per_family > fewest:
            raise ValueError(
                f"queries_per_family must not exceed the {fewest} templates a "
                f"required family holds"
            )
        return self


class SaturationVerdict(BaseModel):
    """What the frontier decided about itself."""

    model_config = ConfigDict(extra="forbid")

    saturated: bool
    reasons: list[str] = Field(default_factory=list)
    blocked: bool = False
    blocked_reason: str = ""
    signals: dict[str, float] = Field(default_factory=dict)


class QueryFrontier:
    """The persistent set of searches, the yield they produced, and the verdict."""

    def __init__(
        self,
        *,
        seed: str,
        source_ids: Sequence[str],
        policy: SaturationPolicy | None = None,
        budget: DiscoveryBudget | None = None,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self.seed = " ".join(seed.split())
        if not self.seed:
            raise ValueError("the seed must not be empty")
        self.source_ids = list(source_ids)
        self.policy = policy if policy is not None else SaturationPolicy()
        self.budget = budget if budget is not None else DiscoveryBudget()
        self.clock = clock
        # The version the next checkpoint write uses. It advances after a
        # successful save, so a changed state never lands on a version that
        # already holds another state.
        self.checkpoint_version = 1
        self._queries: dict[str, Query] = {}
        self._pending: dict[str, Query] = {}
        self._executed: dict[str, str] = {}
        self._seen_results: set[str] = set()
        self._family_counts: dict[str, int] = {}
        self._family_novelty: dict[str, list[float]] = {}
        self._source_runs: dict[str, int] = {}
        self._searched_sources: set[str] = set()
        self._consecutive_failures = 0
        # The searches of the whole run that did not succeed, beside the
        # consecutive failures. The house gate refuses a zero-result
        # outcome when any search call in the stage did not succeed, and a
        # partial call is not a successful one, so this count is the one the
        # zero-result outcome reads.
        self._unsuccessful_searches = 0
        self._consecutive_low_yield = 0
        self._refusals = 0
        self._last_novelty = 0.0
        self._last_duplicate_rate = 0.0
        self._results_seen = 0
        self._duplicate_results = 0
        self._saw_empty = False
        # The cursor of each family over its own templates. It is part of
        # the state, so a resume continues where the frontier left off.
        self._cursors: dict[str, int] = {}
        # Why the last expansion produced fewer queries than it was asked
        # for. The caller needs to tell "more to do" from "done".
        self._expansion_stop_reason = ""
        # The budget overrun the run went over, if any. The frontier keeps
        # it, because a search that was paid for must leave a record
        # behind, and the caller has to be able to see the overrun.
        self._budget_overrun: BudgetExceeded | None = None

    @property
    def pending(self) -> list[Query]:
        """The queries that may still run, in priority order."""
        return self.next_queries(len(self._pending))

    @property
    def expansion_stop_reason(self) -> str:
        """Why the last expansion stopped, empty when it never stopped."""
        return self._expansion_stop_reason

    @property
    def budget_overrun(self) -> BudgetExceeded | None:
        """The budget overrun this session went over, or nothing.

        The overrun is the house ``BudgetExceeded``, kept rather than
        raised: the search already happened, so the record of it must not
        be lost, and the overrun is reported here instead.

        It says what happened in this session. Whether the run is over its
        limits is a different fact, and the budget answers it from its own
        recorded state: the verdict's ``budget_overrun`` signal is that
        answer, so it survives a checkpoint.
        """
        return self._budget_overrun

    @property
    def refusals(self) -> int:
        """The number of queries the frontier refused."""
        return self._refusals

    @property
    def executed(self) -> dict[str, str]:
        """The status of every query that ran, by query id."""
        return dict(self._executed)

    @property
    def seen_results(self) -> frozenset[str]:
        """The result identities the frontier has already seen."""
        return frozenset(self._seen_results)

    @property
    def family_counts(self) -> dict[str, int]:
        """The number of successful searches per family."""
        return dict(self._family_counts)

    def propose(
        self,
        text: str,
        source_id: str,
        *,
        now: datetime | None = None,
    ) -> tuple[Query, bool, str]:
        """Build a query from text that arrived from outside, and admit it.

        The family of a query of unknown origin comes from the marker table,
        never from the proposer. The decision and the reason come back with
        the query.
        """
        family, reason = assign_family(text)
        query = Query(
            query_id=query_id(text, source_id),
            text=text,
            normalized=normalize_query(text),
            family=family,
            source_id=source_id,
            seed=self.seed,
            assigned_by=ASSIGNED_BY_WORKER,
            assignment_reason=reason,
            depth=0,
            created_at=now or self.clock(),
        )
        admitted, refusal = self.admit(query)
        return query, admitted, refusal

    def expand(self, count: int = 1) -> list[Query]:
        """Grow the frontier along the code-owned families.

        The caller says how many queries it wants; the frontier says which
        family they come from. Each required family has a cursor over its
        own templates, and the next variant of the least covered family is
        rendered for the seed and admitted.

        The families, their templates and their order live in
        ``discovery.families``, so the caller never picks a family and the
        same seed always produces the same frontier. A family that needs a
        normalized problem is never expanded from the raw seed: the
        frontier has no normalized problem, so the caller adds those
        queries itself.

        When the expansion cannot go on, the reason is left in
        ``expansion_stop_reason``. The reason describes the last expansion
        and nothing else, so it is cleared before this one runs: a stale
        answer would tell the caller it is done when it is not.
        """
        self._expansion_stop_reason = ""
        expanded: list[Query] = []
        for _ in range(max(0, count)):
            query = self._expand_one()
            if query is None:
                break
            expanded.append(query)
        if len(expanded) < max(0, count):
            self._expansion_stop_reason = self._expansion_stop_text()
        return expanded

    def admit(self, query: Query, cost: float = 0.0) -> tuple[bool, str]:
        """Admit a query to the frontier, or refuse it and say why.

        A query whose family does not match the code that owns it is
        refused: a query of unknown origin takes the family the marker
        table gives its text, and never a family that needs a normalized
        problem, and a query of a template takes the family that template
        belongs to, and only when its text really is a rendering of that
        template. A query that duplicates one the frontier already holds
        against the same source is refused, a query against a source the
        caller did not admit is refused, and a query the budget will not
        admit is refused. Every refusal is counted, so the duplicate rate
        of the frontier is measurable.
        """
        if query.source_id not in self.source_ids:
            self._refusals += 1
            return False, f"source {query.source_id!r} is not admissible"
        family = _family_of(query)
        if family is None:
            self._refusals += 1
            if query.template_id:
                return False, (
                    f"query {query.query_id} is not a rendering of template "
                    f"{query.template_id}"
                )
            return False, (
                f"query {query.query_id} names a family that needs a "
                f"normalized problem"
            )
        if family != query.family:
            self._refusals += 1
            return False, (
                f"family {query.family.value!r} is not the family of "
                f"{query.template_id or 'the query text'}"
            )
        duplicate = self._duplicate_of(query)
        if duplicate is not None:
            self._refusals += 1
            if duplicate.normalized == query.normalized:
                return False, f"duplicate of query {duplicate.query_id}"
            return False, f"near duplicate of query {duplicate.query_id}"
        decision = self.budget.check(
            query.source_id,
            query.family,
            cost,
            pending_total=len(self._pending),
            pending_source=self._pending_of(source_id=query.source_id),
            pending_family=self._pending_of(family=query.family),
        )
        if not decision.allowed:
            self._refusals += 1
            return False, decision.reason
        self._queries[query.query_id] = query
        self._pending[query.query_id] = query
        return True, "admitted"

    def record_execution(
        self,
        query: Query,
        *,
        run_id: str,
        status: DiscoveryStatus | str,
        result_count: int,
        result_ids: Sequence[str] = (),
        useful_count: int = 0,
        cost: float = 0.0,
        tool: str = "search",
        agent: str = "discovery",
        stage: str = DISCOVERY_STAGE,
        result_ref: str = "",
        error: str = "",
        timestamp: datetime | None = None,
    ) -> DiscoveryRecord:
        """Record one search and return the record it produced.

        The novelty is computed here, against the result identities the
        frontier has already seen, so one implementation owns the
        arithmetic. A result count that does not match the number of
        result identities is refused, because the novelty of a result set
        that cannot be identified is not knowable.

        A refused query is not a search: a query the frontier refused as a
        duplicate, or that the budget would not admit, never ran a tool.
        It counts for nothing.

        A query that represents a real search must have been admitted: an
        execution of a query the frontier never admitted is refused, because
        that query never went through the source gate, the budget check or
        the duplicate check. The two refusal statuses are the exception,
        because a refusal describes a query that was not admitted.

        The source gate is re-checked here for every execution. A search is
        only recorded for a source the frontier was given, whatever else
        put the query into the frontier.

        The family the frontier counts is the family the code owns, so a
        caller that hands over a query with a forged family cannot inflate
        that family's coverage. A query whose family cannot be worked out
        at all is refused, rather than counted under the family the
        caller claimed.

        The record is never lost. The budget's overrun is raised after the
        search, because the cost of a search is not known before it runs,
        and the frontier keeps that overrun instead of letting it escape:
        a search that was paid for must be persistable, and the frontier's
        own counters must not be skipped by the raise. The overrun is
        reported through ``budget_overrun``, and the verdict's
        ``budget_overrun`` signal reports whether the run is over one of
        the budget's limits.
        """
        if query.query_id in self._executed:
            raise ValueError(f"query {query.query_id} already ran")
        status = DiscoveryStatus(status)
        if query.source_id not in self.source_ids:
            # A search is only recorded for a source the frontier was given.
            # This is the backstop for any path that puts a query into the
            # pending set without a gate, including a future one nobody has
            # thought of yet.
            raise ValueError(
                f"source {query.source_id!r} is not admissible for query "
                f"{query.query_id}"
            )
        family = _family_of(query)
        if family is None:
            # The family cannot be worked out, so the execution is refused
            # rather than counted under the family the caller claimed.
            raise ValueError(
                f"the family of query {query.query_id} cannot be determined"
            )
        if (
            status not in (DiscoveryStatus.duplicate, DiscoveryStatus.over_budget)
            and query.query_id not in self._pending
        ):
            # A search that represents a real run must have been admitted:
            # a query the frontier never admitted never went through the
            # source gate, the budget check or the duplicate check. The two
            # refusal statuses are the exception, because a refusal
            # describes a query that was not admitted.
            raise ValueError(f"query {query.query_id} was never admitted")
        identities = list(result_ids)
        if result_count != len(identities):
            raise ValueError(
                "the result count must equal the number of result identities"
            )
        novel = len(set(identities) - self._seen_results)
        if identities:
            novelty = novel / len(identities)
            duplicate_rate = 1.0 - novelty
        else:
            # A search that returned nothing carries no novelty and no
            # duplicate rate: there was nothing to be new or to repeat.
            novelty = 0.0
            duplicate_rate = 0.0
        record = DiscoveryRecord(
            run_id=run_id,
            stage=stage,
            tool=tool,
            agent=agent,
            query_id=query.query_id,
            query=query.text,
            family=family,
            source_id=query.source_id,
            timestamp=timestamp or self.clock(),
            result_count=result_count,
            useful_count=useful_count,
            novelty=novelty,
            duplicate_rate=duplicate_rate,
            cost=cost,
            status=status,
            result_ref=result_ref,
            error=error,
        )
        self._executed[query.query_id] = status.value
        self._queries.setdefault(query.query_id, query)
        self._pending.pop(query.query_id, None)
        if status in (DiscoveryStatus.failure, DiscoveryStatus.timeout):
            # A broken tool says nothing about the query: it does not count
            # towards family coverage, it does not advance the low-yield
            # counter, and it leaves the frontier blocked. The tool was
            # called, so the search cost a query. The failure is counted
            # before the budget, so a budget overrun cannot skip it: a
            # broken search tool is never saturation.
            self._consecutive_failures += 1
            self._unsuccessful_searches += 1
            self._count_search(query, family, cost)
            return record
        if status in (DiscoveryStatus.duplicate, DiscoveryStatus.over_budget):
            # A refusal is not a search and not a failure: no tool ran, so
            # it costs nothing, it counts for no coverage, and it neither
            # advances nor clears the failure counter.
            return record
        # The bookkeeping comes first and the budget comes last. The cost of
        # a search is not known before the search runs, so the budget is
        # only measured afterwards, and a raise from it must not skip a
        # counter: the coverage, the result identities and the failure
        # counter are the frontier's own account of what happened.
        self._seen_results |= set(identities)
        self._results_seen += len(identities)
        self._duplicate_results += len(identities) - novel
        self._last_novelty = novelty
        self._last_duplicate_rate = duplicate_rate
        self._consecutive_failures = 0
        self._searched_sources.add(query.source_id)
        self._family_counts[family.value] = (
            self._family_counts.get(family.value, 0) + 1
        )
        self._family_novelty.setdefault(family.value, []).append(novelty)
        if status is DiscoveryStatus.partial:
            # A partial search is a completed search: the tool ran and it
            # counts towards the coverage. It is not a successful one
            # though, so the zero-result outcome cannot describe the run
            # while it happened.
            self._unsuccessful_searches += 1
        if identities:
            # A search that came back with results is not a zero result,
            # whatever an earlier search found.
            self._saw_empty = False
        elif status is DiscoveryStatus.empty:
            self._saw_empty = True
        if novelty < self.policy.min_novelty:
            self._consecutive_low_yield += 1
        else:
            self._consecutive_low_yield = 0
        self._count_search(query, family, cost)
        return record

    def next_queries(self, limit: int) -> list[Query]:
        """Return the pending queries in priority order.

        The priority is the family weight, the source weight and the
        novelty estimate, and ties break on the query id, so the order is
        stable.
        """
        if limit <= 0:
            return []
        ordered = sorted(
            self._pending.values(),
            key=lambda query: (-self.priority(query), query.query_id),
        )
        return ordered[:limit]

    def saturation(self) -> SaturationVerdict:
        """Return the verdict: whether the frontier has enough, and why.

        A blocked frontier never reports saturation, whatever else it
        measured.

        The ``budget_overrun`` signal is the budget's own answer to whether
        the run is over one of its limits, measured from the recorded
        totals. It is not the kept exception of this session, so it
        survives a checkpoint.
        """
        reasons: list[str] = []
        required = list(self.policy.required_families)
        covered = [
            family.value
            for family in required
            if self._family_counts.get(family.value, 0)
            >= self.policy.queries_per_family
        ]
        if required and len(covered) == len(required):
            reasons.append(REASON_FAMILY_COVERAGE)
        if self.source_ids and all(
            source in self._searched_sources for source in self.source_ids
        ):
            reasons.append(REASON_SOURCE_COVERAGE)
        if self._budget_exhausted():
            reasons.append(REASON_BUDGET_EXHAUSTED)
        if self._consecutive_low_yield >= self.policy.max_consecutive_low_yield_batches:
            reasons.append(REASON_DIMINISHING_NOVELTY)
        if self._duplicate_rate() > self.policy.max_duplicate_rate:
            reasons.append(REASON_REPEATED_RESULTS)
        blocked = self._consecutive_failures > 0
        blocked_reason = ""
        if blocked:
            blocked_reason = f"{self._consecutive_failures} consecutive search failures"
        return SaturationVerdict(
            saturated=bool(reasons) and not blocked,
            reasons=reasons,
            blocked=blocked,
            blocked_reason=blocked_reason,
            signals={
                "families_covered": float(len(covered)),
                "families_required": float(len(required)),
                "queries": float(self.budget.queries),
                "cost": self.budget.cost,
                "last_novelty": self._last_novelty,
                "last_duplicate_rate": self._last_duplicate_rate,
                "duplicate_rate": self._duplicate_rate(),
                "consecutive_low_yield": float(self._consecutive_low_yield),
                "consecutive_failures": float(self._consecutive_failures),
                "unsuccessful_searches": float(self._unsuccessful_searches),
                "refusals": float(self._refusals),
                "seen_results": float(len(self._seen_results)),
                "budget_overrun": 1.0 if self.budget.exceeded() else 0.0,
            },
        )

    def zero_result_outcome(self) -> ZeroResultOutcome:
        """Return the zero-result outcome of the run.

        It is recorded only when a search came back empty, every required
        family ran, the frontier is not blocked, and no search of the run
        failed: the house gate refuses a zero when any search call in the
        stage did not succeed, because "a search call did not succeed, so
        its result is not a zero". A partial search is not a successful
        one, so it counts here too. A policy with no required family
        records nothing, because nothing was covered.
        """
        required = [family.value for family in self.policy.required_families]
        completed = [
            family.value
            for family in self.policy.required_families
            if self._family_counts.get(family.value, 0) >= 1
        ]
        blocked = self._consecutive_failures > 0
        if (
            self._saw_empty
            and required
            and not blocked
            and self._unsuccessful_searches == 0
            and len(completed) == len(required)
        ):
            return ZeroResultOutcome(
                recorded=True,
                required_query_families=required,
                completed_query_families=completed,
                note="every required family ran and a search came back empty",
            )
        return ZeroResultOutcome(
            recorded=False,
            required_query_families=required,
            completed_query_families=completed,
            note=(
                "a zero is only recorded when the frontier is not blocked, "
                "every search of the run succeeded, and every required "
                "family ran"
            ),
        )

    def checkpoint_state(self, version: int | None = None) -> dict[str, object]:
        """Return the JSON-ready state of the frontier.

        The state records the checkpoint version it is written at, so a
        restore continues after the row it came from. A caller that writes
        the state at another version passes it in.
        """
        return {
            "seed": self.seed,
            "source_ids": list(self.source_ids),
            "policy": self.policy.model_dump(mode="json"),
            "budget": self.budget.state(),
            "checkpoint_version": (
                self.checkpoint_version if version is None else version
            ),
            "queries": {
                query_id_: query.model_dump(mode="json")
                for query_id_, query in self._queries.items()
            },
            "pending": sorted(self._pending),
            "executed": dict(self._executed),
            "seen_results": sorted(self._seen_results),
            "counters": {
                "family_counts": dict(self._family_counts),
                "family_novelty": {
                    family: list(novelties)
                    for family, novelties in self._family_novelty.items()
                },
                "source_runs": dict(self._source_runs),
                "searched_sources": sorted(self._searched_sources),
                "consecutive_failures": self._consecutive_failures,
                "unsuccessful_searches": self._unsuccessful_searches,
                "consecutive_low_yield": self._consecutive_low_yield,
                "refusals": self._refusals,
                "last_novelty": self._last_novelty,
                "last_duplicate_rate": self._last_duplicate_rate,
                "results_seen": self._results_seen,
                "duplicate_results": self._duplicate_results,
                "saw_empty": self._saw_empty,
                "family_cursors": dict(self._cursors),
            },
        }

    @classmethod
    def from_state(
        cls,
        state: dict[str, object],
        *,
        clock: Callable[[], datetime] = utcnow,
        tracker: BudgetTracker | None = None,
    ) -> QueryFrontier:
        """Rebuild a frontier from the state a checkpoint holds.

        The contract tracker is not part of the state: a tracker is a ledger
        that lives in the run. The caller hands the tracker back, so the
        contract's limits still bind after a resume. The budget's own
        counters come from the checkpoint, so a resume never forgets what
        the run already spent.

        The state is data, not proof. A pending query is restored only when
        it passes the source gate, the family re-derivation, the duplicate
        check and the budget check again, so a forged state cannot smuggle
        a query past the gates it never passed.
        """
        policy = SaturationPolicy.model_validate(dict(state.get("policy", {})))
        budget = DiscoveryBudget.from_state(
            dict(state.get("budget", {})), tracker=tracker
        )
        frontier = cls(
            seed=str(state.get("seed", "")),
            source_ids=[str(source) for source in list(state.get("source_ids", []))],
            policy=policy,
            budget=budget,
            clock=clock,
        )
        # The state records the version it was written under, so the next
        # write lands on the version after it.
        frontier.checkpoint_version = int(state.get("checkpoint_version", 0)) + 1
        for query_key, payload in dict(state.get("queries", {})).items():
            query = Query.model_validate(payload)
            frontier._queries[query_key] = query
        for query_key in list(state.get("pending", [])):
            query = frontier._queries.get(query_key)
            if query is None:
                continue
            # A state is data and data is not proof. A restored query passes
            # the same gates it passed when it was admitted, or it is not
            # pending: a query the caller may not search against is not
            # pending, a query whose family the code cannot work out is not
            # pending, a query that duplicates one the frontier already
            # holds against the same source is not pending, and a query the
            # budget will not admit is not pending.
            if query.source_id not in frontier.source_ids:
                continue
            family = _family_of(query)
            if family is None or family != query.family:
                continue
            if frontier._duplicate_of(query, ignore_id=query.query_id) is not None:
                continue
            # The budget's admission check takes the pending counts, because
            # an admitted query is a query the run is committed to. The
            # restore passes the counts it has restored so far, which are
            # the counts the admission itself saw: the query is not in the
            # pending set yet. The executed count and the pending count of a
            # scope add up to the number of queries the run committed to, so
            # the totals the restore approves are the totals the run already
            # approved, and no legitimate query is dropped.
            decision = frontier.budget.check(
                query.source_id,
                family,
                0.0,
                pending_total=len(frontier._pending),
                pending_source=frontier._pending_of(source_id=query.source_id),
                pending_family=frontier._pending_of(family=family),
            )
            # A refusal about the cost does not take the query out of the
            # pending set. The cost of a search is not known before the
            # search runs, so the cost limit is reported as an overrun when
            # the search is recorded, and a query that was admitted is one
            # the run is committed to. The query counts are the limits the
            # admission bounded, and they are what a restore honours.
            if not decision.allowed and decision.dimension != DIMENSION_COST:
                continue
            frontier._pending[query_key] = query
        frontier._executed = {
            str(key): str(value)
            for key, value in dict(state.get("executed", {})).items()
        }
        frontier._seen_results = {
            str(result) for result in list(state.get("seen_results", []))
        }
        counters = dict(state.get("counters", {}))
        frontier._family_counts = {
            str(key): int(value)
            for key, value in dict(counters.get("family_counts", {})).items()
        }
        frontier._family_novelty = {
            str(family): [float(value) for value in novelties]
            for family, novelties in dict(counters.get("family_novelty", {})).items()
        }
        frontier._source_runs = {
            str(key): int(value)
            for key, value in dict(counters.get("source_runs", {})).items()
        }
        frontier._searched_sources = {
            str(source) for source in list(counters.get("searched_sources", []))
        }
        frontier._consecutive_failures = int(counters.get("consecutive_failures", 0))
        frontier._unsuccessful_searches = int(
            counters.get("unsuccessful_searches", 0)
        )
        frontier._consecutive_low_yield = int(
            counters.get("consecutive_low_yield", 0)
        )
        frontier._refusals = int(counters.get("refusals", 0))
        frontier._last_novelty = float(counters.get("last_novelty", 0.0))
        frontier._last_duplicate_rate = float(
            counters.get("last_duplicate_rate", 0.0)
        )
        frontier._results_seen = int(counters.get("results_seen", 0))
        frontier._duplicate_results = int(counters.get("duplicate_results", 0))
        frontier._saw_empty = bool(counters.get("saw_empty", False))
        frontier._cursors = {
            str(family): int(index)
            for family, index in dict(counters.get("family_cursors", {})).items()
        }
        return frontier

    def priority(self, query: Query) -> float:
        """Return the priority of a pending query.

        The priority is the family weight, the source weight and the
        novelty estimate, and ties break on the query id, so the order is
        stable.
        """
        family_weight = FAMILY_SPECS[query.family].priority_weight
        source_weight = 1.0 / (1.0 + self._source_runs.get(query.source_id, 0))
        return (
            0.5 * family_weight
            + 0.3 * source_weight
            + 0.2 * self._novelty_estimate(query.family)
        )

    def _novelty_estimate(self, family: QueryFamily) -> float:
        """Return the novelty a query of this family is expected to bring.

        A family that never ran is assumed to bring something new, because
        nothing is known about it yet.
        """
        novelties = self._family_novelty.get(family.value, [])
        if not novelties:
            return 1.0
        return sum(novelties) / len(novelties)

    def _duplicate_rate(self) -> float:
        """Return the share of the results seen so far that were repeats."""
        if not self._results_seen:
            return 0.0
        return self._duplicate_results / self._results_seen

    def _duplicate_of(
        self, query: Query, *, ignore_id: str | None = None
    ) -> Query | None:
        """Return the query this one duplicates, or nothing.

        The check is scoped to the source. The same text against the same
        source is the same search, and the same text against another
        source is another search, so the frontier can hold one query per
        wording per source and can search every admissible source.

        An equal text is a duplicate as well, so one comparison covers
        both. ``ignore_id`` names the query the frontier is checking, so a
        restore does not find the query against itself.
        """
        duplicate: Query | None = None
        for existing in self._queries.values():
            if ignore_id is not None and existing.query_id == ignore_id:
                continue
            if existing.source_id != query.source_id:
                continue
            if duplicate is None and is_duplicate(
                existing.text, query.text, self.policy.near_duplicate_threshold
            ):
                duplicate = existing
        return duplicate

    def _budget_exhausted(self) -> bool:
        """Report whether the budget refuses every pending query.

        A frontier that has nothing pending is exhausted when the run-level
        limit is spent, so a caller can tell "stopped" from "has more to
        do". A pending query that the budget still admits means the
        frontier has room, whatever the budget says about the others.
        """
        pending = self.next_queries(len(self._pending))
        if not pending:
            return self.budget.queries >= self.budget.max_queries_per_run
        for query in pending:
            if self.budget.check(query.source_id, query.family, 0.0).allowed:
                return False
        return True

    def _count_search(self, query: Query, family: QueryFamily, cost: float) -> None:
        """Count a search against its source and against the budget.

        The budget's own ``record`` raises the house ``BudgetExceeded``
        when the run is over one of its limits. The frontier keeps that
        exception rather than letting it escape: the call already
        happened, so the record of it must not be lost, and the overrun
        is reported through ``budget_overrun`` instead. The budget still
        raises for a caller that uses it on its own.
        """
        self._source_runs[query.source_id] = (
            self._source_runs.get(query.source_id, 0) + 1
        )
        try:
            self.budget.record(query.source_id, family, cost)
        except BudgetExceeded as overrun:
            self._budget_overrun = overrun

    def _pending_of(
        self,
        *,
        source_id: str | None = None,
        family: QueryFamily | None = None,
    ) -> int:
        """Return the number of pending queries of a source or a family."""
        return sum(
            1
            for query in self._pending.values()
            if (source_id is None or query.source_id == source_id)
            and (family is None or query.family is family)
        )

    def _searches_of(self, family: QueryFamily) -> int:
        """Return the searches a family holds: the ones that ran and the pending ones."""
        return self._family_counts.get(family.value, 0) + self._pending_of(family=family)

    def _expand_one(self) -> Query | None:
        """Admit the next variant of the least covered family, or nothing."""
        for family in self._expansion_order():
            query = self._next_variant(family)
            if query is not None:
                return query
        return None

    def _expansion_order(self) -> list[QueryFamily]:
        """Return the required families that still need searches, least covered first.

        A family that needs a normalized problem is never expanded from the
        raw seed: the frontier has no normalized problem, so the caller
        adds those queries itself.
        """
        return sorted(
            (
                family
                for family in self.policy.required_families
                if family not in DERIVED_FAMILIES
                and self._searches_of(family) < self.policy.queries_per_family
            ),
            key=self._searches_of,
        )

    def _expansion_stop_text(self) -> str:
        """Return why the expansion could not go on."""
        order = self._expansion_order()
        if not order:
            return "no required family can be expanded any further"
        return (
            "no variant of "
            + ", ".join(family.value for family in order)
            + " could be admitted"
        )

    def _next_variant(self, family: QueryFamily) -> Query | None:
        """Render and admit the next variant of a family, or nothing."""
        source_id = self._least_used_source()
        if source_id is None:
            return None
        candidates = generate_queries(
            self.seed, [family], source_id, now=self.clock()
        )
        cursor = self._cursors.get(family.value, 0)
        for offset in range(len(candidates)):
            index = (cursor + offset) % len(candidates)
            query = candidates[index]
            admitted, _reason = self.admit(query)
            if admitted:
                self._cursors[family.value] = (index + 1) % len(candidates)
                return query
        return None

    def _least_used_source(self) -> str | None:
        """Return the admissible source the frontier has admitted the fewest queries against.

        The count is the admitted queries, not the executed ones: a batch
        of admissions that has not run yet is already a load on that
        source, so the frontier spreads over the sources it was given.
        """
        if not self.source_ids:
            return None
        admitted: dict[str, int] = {}
        for query in self._queries.values():
            admitted[query.source_id] = admitted.get(query.source_id, 0) + 1
        return min(
            self.source_ids,
            key=lambda source: admitted.get(source, 0),
        )

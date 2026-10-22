"""Planning, execution and checkpoints for a collection run.

The orchestrator owns the shared fetch path, the ladder and the contract's
budget tracker. It holds no client of its own: the fetcher is the only way to
the network, and every rule about what may be fetched lives inside it.

The collection layer holds no research reasoning. It fetches what it is told
to fetch and records what came back.
"""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Callable, Iterable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta

import httpx

from product_hunter.collection.adapters.adapters import build_adapters
from product_hunter.collection.adapters.base import (
    CollectionResult,
    Driver,
    ToolError,
)
from product_hunter.collection.ladder import CollectorUnavailable, Ladder
from product_hunter.collection.state import CollectionItem, CollectionState, SourceState
from product_hunter.db.checkpoints import save_checkpoint
from product_hunter.db.jobs import Job, JobContext, enqueue_job
from product_hunter.db.store import Store
from product_hunter.fetch import (
    Fetcher,
    FetchError,
    JsonlLog,
    RequestBudget,
    Validators,
    bounded_detail,
    utcnow,
)
from product_hunter.raw_store import RawStore
from product_hunter.research.budget import (
    BudgetDimension,
    BudgetExceeded,
    BudgetTracker,
)
from product_hunter.research.contract import ResearchContract, load_research_contract
from product_hunter.safety import UrlBlocked, admit_url, resolve_public_ips
from product_hunter.sources import (
    AllowedUse,
    SourceBlocked,
    SourceConfig,
    SourceRegistry,
)

# The research contract's collection stage. Its budgets are the contract's.
STAGE = "evidence_collection"

# The worker stage the collection handler answers to.
COLLECT_STAGE = "COLLECT"

# The reasons a recorded attempt is not tried again. The same outcome can be
# permanent or transient, so terminality is decided by the reason and not by
# the outcome: an http error may clear, a robots refusal will not.
TERMINAL_REASONS = frozenset(
    {
        "",
        "unknown_source",
        "source_disabled",
        "review_expired",
        "terms_not_permitted",
        "use_not_allowed",
        "missing_use",
        "no_domains",
        "robots_blocked",
        "unsafe_url",
        "too_large",
        "too_many_redirects",
        "empty_body",
        # The rung will not be installed during the run, so the answer
        # cannot change.
        "collector_unavailable",
    }
)

# The reasons that mean the request did not complete, rather than being
# refused before it went out. An empty body is one of them: the request
# went out and the page answered with nothing.
FAILED_REASONS = frozenset({"http_error", "network_error", "timeout", "empty_body"})

# The reasons that mean no capture was produced. The contract's fetch
# budget pays for the captures, so an attempt with one of these reasons is
# recorded and not charged as a fetch. A refused redirect hop is one of
# them: it produces no capture, and the hop's request was made, so it
# still counts against the request counter.
NO_CAPTURE_REASONS = frozenset(
    {
        "budget_exceeded",
        "unsafe_url",
        "robots_blocked",
        "unknown_source",
        "source_disabled",
        "review_expired",
        "terms_not_permitted",
        "use_not_allowed",
        "missing_use",
        "no_domains",
        "collector_unavailable",
    }
)

EventSink = Callable[[Mapping[str, str]], None]
"""Where the collection layer hands the events it cannot record itself.

The layer does not log. It hands a structured event to the caller, and the
caller decides what to do with it, so the observability policy stays with the
application layer. An event carries identifiers the worker owns. It never
carries a URL, a page title or any other text that came from the network.
"""


def _bounded_detail(exc: Exception, *, secret: str = "", limit: int = 200) -> str:
    """The error text, bounded, with the network text taken out of it.

    The detail is what a caller would log. The URL came from the network, so
    it is replaced before the detail leaves this module, and the text is
    bounded so a long error cannot ride along in it.
    """
    text = f"{type(exc).__name__}: {exc}"
    if secret:
        text = text.replace(secret, "<url>")
    if len(text) > limit:
        text = text[:limit] + "..."
    return text


@dataclass(frozen=True)
class CollectionTarget:
    """What discovery asked for: a source and the URL under it."""

    source_id: str
    url: str


@dataclass(frozen=True)
class PlanRefusal:
    """A target the planner left out, and the reason it left it out."""

    target: CollectionTarget
    reason_code: str
    detail: str


@dataclass(frozen=True)
class CollectionPlan:
    """The batch to collect now, and what was left out.

    The scope is the unit the caps are counted in. Every job of one batch
    shares one scope.
    """

    scope: str
    batch: tuple[CollectionTarget, ...]
    refusals: tuple[PlanRefusal, ...]


@dataclass(frozen=True)
class CollectionOutcome:
    """What one target produced.

    The outcome carries hashes, paths, status codes and reason codes. It
    never carries page text: the body stays in the raw store, addressed by
    hash, and nothing here turns content into a command, a URL or an
    argument.
    """

    scope: str
    source_id: str
    url: str
    outcome: str
    reason_code: str = ""
    detail: str = ""
    rung: str = ""
    fetch_id: str | None = None
    status: int | None = None
    bytes: int = 0
    raw_sha256: str | None = None
    raw_path: str | None = None
    latency_ms: int = 0
    retry_count: int = 0


class CollectionPlanner:
    """Decides what may be collected now, and what may not."""

    def __init__(
        self,
        registry: SourceRegistry,
        state: CollectionState,
        tracker: BudgetTracker,
        budget: RequestBudget,
        *,
        resolver=resolve_public_ips,
    ) -> None:
        self._registry = registry
        self._state = state
        self._tracker = tracker
        self._budget = budget
        self._resolver = resolver

    def plan(
        self,
        scope: str,
        targets: Iterable[CollectionTarget],
        now: float | None = None,
    ) -> CollectionPlan:
        """The batch to collect now, and the targets left out with their reasons.

        The scope is the unit the caps are counted in, and every job of one
        batch shares it.

        A source whose minimum gap has not elapsed is refused with
        ``rate_limited``: a plan says what may be collected now, and a target
        that would have to wait is not one of them.
        ``CollectionOrchestrator.collect`` waits the gap out instead of
        refusing it, because a target handed to it directly is a request to
        collect that target now. Both behaviours are intended.
        """
        moment = self._budget.now() if now is None else now
        batch: list[CollectionTarget] = []
        refusals: list[PlanRefusal] = []
        for target in targets:
            refusal = self._refusal(scope, target, moment)
            if refusal is None:
                batch.append(target)
            else:
                refusals.append(refusal)
        return CollectionPlan(
            scope=scope,
            batch=tuple(_rotate(batch)),
            refusals=tuple(refusals),
        )

    def due_sources(self, now: float | None = None) -> list[str]:
        """The sources that may be collected from right now.

        A source whose gap has elapsed but whose terms do not permit a
        collection is not one of them, so the list is filtered through the
        same terms gate the plan uses.
        """
        moment = self._budget.now() if now is None else now
        return [
            source.source_id
            for source in self._registry.list()
            if self._budget.ready_at(source.source_id) <= moment
            and self._require(source.source_id)[0] is not None
        ]

    def source_for(
        self, target: CollectionTarget
    ) -> tuple[SourceConfig | None, PlanRefusal | None]:
        """The source, or the refusal that keeps it from being collected."""
        source, reason_code, detail = self._require(target.source_id)
        if source is None:
            return None, PlanRefusal(target, reason_code, detail)
        return source, None

    def _require(
        self, source_id: str
    ) -> tuple[SourceConfig | None, str, str]:
        """The source, or the reason and the detail that keep it out."""
        try:
            source = self._registry.require(
                source_id, {AllowedUse.retrieval, AllowedUse.storage}
            )
        except SourceBlocked as exc:
            return None, exc.reason_code, str(exc)
        return source, "", ""

    def budget_refusal(self, target: CollectionTarget) -> PlanRefusal | None:
        """The refusal the contract's budget gives, if it gives one."""
        decision = self._tracker.check(
            BudgetDimension.fetch_requests, 1, stage=STAGE
        )
        if not decision.allowed:
            return PlanRefusal(target, "budget_exceeded", decision.reason)
        decision = self._tracker.check(
            BudgetDimension.source_requests_per_source,
            1,
            stage=STAGE,
            source_id=target.source_id,
        )
        if not decision.allowed:
            return PlanRefusal(target, "budget_exceeded", decision.reason)
        return None

    def duplicate_item(
        self, scope: str, target: CollectionTarget
    ) -> CollectionItem | None:
        """The latest attempt that keeps this target from being fetched again."""
        item = self._state.item(scope, target.source_id, target.url)
        if item is None or item.reason_code not in TERMINAL_REASONS:
            return None
        return item

    def _refusal(
        self, scope: str, target: CollectionTarget, now: float
    ) -> PlanRefusal | None:
        source, refusal = self.source_for(target)
        if source is None:
            return refusal
        refusal = self._url_refusal(target, source)
        if refusal is not None:
            return refusal
        if self.duplicate_item(scope, target) is not None:
            return PlanRefusal(
                target, "duplicate", "the target was already collected in this run"
            )
        refusal = self.budget_refusal(target)
        if refusal is not None:
            return refusal
        if self._budget.ready_at(target.source_id) > now:
            return PlanRefusal(
                target,
                "rate_limited",
                "the source's minimum gap has not elapsed",
            )
        return None

    def _url_refusal(
        self, target: CollectionTarget, source: SourceConfig
    ) -> PlanRefusal | None:
        # The shared path decides what may be fetched, so the planner asks it
        # rather than keeping a second copy of the rule. The reason is the
        # shared path's own, so the two paths speak the same language.
        try:
            admit_url(target.url, source, resolver=self._resolver)
        except UrlBlocked as exc:
            return PlanRefusal(target, "unsafe_url", str(exc))
        return None


class CollectionOrchestrator:
    """Executes a collection plan and records what came back."""

    def __init__(
        self,
        registry: SourceRegistry,
        store: Store,
        *,
        client: httpx.Client,
        raw_store: RawStore,
        log: JsonlLog,
        rejection_log: JsonlLog,
        resolver=resolve_public_ips,
        clock=time.monotonic,
        sleeper=time.sleep,
        now=utcnow,
        contract: ResearchContract | None = None,
        events: EventSink | None = None,
        tools: dict[str, Driver | None] | None = None,
    ) -> None:
        self._registry = registry
        self._store = store
        self._conn = store.conn
        self._now = now
        self._resolver = resolver
        self._events = events
        self._log = log
        self._state = CollectionState(self._conn)
        self._contract = contract or load_research_contract()
        self._tracker = BudgetTracker(self._contract.budgets)
        self._budget = RequestBudget(
            clock=clock,
            wall_clock=now,
            sleeper=sleeper,
            stored_count=self._stored_request_count,
            on_change=self._persist_counts,
        )
        self._fetcher = Fetcher(
            registry,
            raw_store,
            log,
            rejection_log,
            client,
            clock=clock,
            sleeper=sleeper,
            resolver=resolver,
            now=now,
            budget=self._budget,
        )
        self._ladder = Ladder(
            self._fetcher,
            adapters=build_adapters(
                registry,
                raw_store,
                self._budget,
                self._fetcher,
                # The tools are injected rather than found. A browser in an
                # unattended worker is a deliberate decision, so the rung
                # is refused by name until a caller hands the driver over.
                drivers=tools if tools is not None else {},
                clock=clock,
                sleeper=sleeper,
                now=now,
                resolver=resolver,
            ),
        )
        self._planner = self._make_planner()
        self._bound_scope: str | None = None
        # The collection tables carry a foreign key to sources, so the registry
        # has to be in the store before a target may be collected.
        store.sync_registry(registry)

    def _make_planner(self) -> CollectionPlanner:
        return CollectionPlanner(
            self._registry,
            self._state,
            self._tracker,
            self._budget,
            resolver=self._resolver,
        )

    # --- the run -----------------------------------------------------------

    def start_run(self, scope: str) -> None:
        """Bind the orchestrator to a scope and restore what the scope spent.

        The scope is the unit the caps are counted in: the run id of a
        research run, or the batch scope of a cycle trigger. Every job of
        one batch shares it, so the caps are counted once per batch and not
        once per job.

        A scope that comes back after a crash picks its counters up where it
        left them, so a crash cannot buy a fresh allowance, and it waits out
        the gap its last request left behind.
        """
        if self._bound_scope == scope:
            return
        self._bound_scope = scope
        self._tracker = BudgetTracker(self._contract.budgets)
        self._planner = self._make_planner()
        self._budget.restore(
            self._state.request_counts(scope), self._gap_floors(scope)
        )
        self._replay_tracker(scope)

    def plan(
        self,
        scope: str,
        targets: Iterable[CollectionTarget],
        now: float | None = None,
    ) -> CollectionPlan:
        """The batch to collect now, and the targets left out with their reasons."""
        self.start_run(scope)
        return self._planner.plan(scope, targets, now)

    def due_sources(self, now: float | None = None) -> list[str]:
        """The sources that may be collected from right now."""
        return self._planner.due_sources(now)

    def collect(self, scope: str, target: CollectionTarget) -> CollectionOutcome:
        """Collect one target and record what came back.

        The scope is the unit the caps are counted in, and every job of one
        batch shares it.

        The source's minimum gap is waited out in the shared path instead of
        being refused, because a target handed to ``collect`` is a request to
        collect that target now. ``CollectionPlanner.plan`` refuses a source
        whose gap has not elapsed, because a plan says what may be collected
        now. Both behaviours are intended.
        """
        self.start_run(scope)
        source, refusal = self._planner.source_for(target)
        if refusal is not None:
            return self._record(
                scope,
                target,
                outcome="skipped",
                reason_code=refusal.reason_code,
                detail=refusal.detail,
            )
        # A target that was already collected costs nothing, so the duplicate
        # is recognised before the budget is consulted.
        duplicate = self._planner.duplicate_item(scope, target)
        if duplicate is not None:
            return _outcome_from_item(scope, duplicate)
        budget_refusal = self._planner.budget_refusal(target)
        if budget_refusal is not None:
            return self._record(
                scope,
                target,
                outcome="skipped",
                reason_code=budget_refusal.reason_code,
                detail=budget_refusal.detail,
            )
        state = self._state.source_state(target.source_id, target.url)
        validators = _validators(state)
        try:
            result = self._ladder.collect(source, target, validators=validators)
        except CollectorUnavailable as exc:
            return self._record(
                scope,
                target,
                outcome="skipped",
                reason_code="collector_unavailable",
                detail=str(exc),
                rung=source.collector.value,
                source=source,
            )
        except (FetchError, SourceBlocked, UrlBlocked, ToolError) as exc:
            reason = getattr(exc, "reason_code", "fetch_failed")
            outcome = _outcome_for(reason)
            return self._record(
                scope,
                target,
                outcome=outcome,
                reason_code=reason,
                detail=str(exc),
                rung=source.collector.value,
                source=source,
                # Every tool that ran is a fetch the run spent, and a rung
                # that was refused because its tool is missing never ran.
                charges=_charges(getattr(exc, "attempts", [])),
                # A refusal that stopped the request before it went out is
                # an attempt, not a fetch: the contract's fetch budget pays
                # for the requests that were sent.
                attempted=reason not in NO_CAPTURE_REASONS,
            )
        except Exception as exc:
            # The collection layer's promise is that every attempt leaves a
            # record, so an unexpected failure is recorded rather than
            # raised: a worker that dies on one bad target loses the rest of
            # the batch. The class name is in the detail, the detail is
            # bounded and the URL is taken out of it, so a page cannot ride
            # along in it. KeyboardInterrupt and SystemExit are not
            # Exception and keep propagating.
            return self._record(
                scope,
                target,
                outcome="failed",
                reason_code="fetch_failed",
                detail=_bounded_detail(exc, secret=target.url),
                rung=source.collector.value,
                source=source,
                attempted=True,
            )
        return self._record(
            scope,
            target,
            outcome="not_modified" if result.not_modified else "fetched",
            reason_code="",
            detail="",
            rung=result.rung or source.collector.value,
            source=source,
            result=result,
            attempted=True,
            charges=_charges(result.attempts),
            cursor=state.cursor if state is not None else None,
            previous_sha256=state.content_sha256 if state is not None else None,
        )

    def run(self, plan: CollectionPlan) -> list[CollectionOutcome]:
        """Record the refusals, collect the batch, and return the outcomes."""
        outcomes: list[CollectionOutcome] = []
        for refusal in plan.refusals:
            if refusal.reason_code == "duplicate":
                # The attempt that made it a duplicate is the record; a
                # refusal row would replace it and lose the capture.
                duplicate = self._planner.duplicate_item(plan.scope, refusal.target)
                if duplicate is not None:
                    outcomes.append(_outcome_from_item(plan.scope, duplicate))
                    continue
            outcomes.append(
                self._record(
                    plan.scope,
                    refusal.target,
                    outcome="skipped",
                    reason_code=refusal.reason_code,
                    detail=refusal.detail,
                )
            )
        for target in plan.batch:
            outcomes.append(self.collect(plan.scope, target))
        return outcomes

    # --- the writes --------------------------------------------------------

    def _record(
        self,
        scope: str,
        target: CollectionTarget,
        *,
        outcome: str,
        reason_code: str,
        detail: str,
        rung: str = "",
        source: SourceConfig | None = None,
        result: CollectionResult | None = None,
        attempted: bool = False,
        charges: int = 1,
        cursor: str | None = None,
        previous_sha256: str | None = None,
    ) -> CollectionOutcome:
        at = self._now().isoformat()
        if attempted:
            # A fallback that ran two tools spent two fetches, and the
            # contract's caps are counted per tool attempt. A rung that
            # was refused because its tool is not installed never ran, so
            # it is not charged.
            self._charge(target.source_id, count=max(1, charges))
        provenance = source if source is not None else self._known_source(target.source_id)
        record = result.fetch_record() if result is not None else None
        with self._transaction():
            # The collection tables carry a foreign key to sources, so a
            # source the registry does not know has nowhere to be recorded.
            # The refusal is still returned to the caller.
            if provenance is not None:
                if attempted:
                    # A failed attempt is a fetch the run spent, so the
                    # ledger says so. A skip is not a fetch, and a duplicate
                    # never reaches this point.
                    self._state.add_fetch(scope, target.source_id, at=at)
                if record is not None:
                    self._store.import_fetch(record)
                    self._state.save_source_state(
                        SourceState(
                            source_id=target.source_id,
                            url=target.url,
                            cursor=cursor,
                            etag=record.etag,
                            last_modified=record.last_modified,
                            last_status=record.status,
                            last_fetch_id=record.fetch_id,
                            last_fetched_at=record.fetched_at,
                            # A 304 stores no body, so the hash of the last
                            # stored body is the one that is still true.
                            content_sha256=record.raw_sha256 or previous_sha256,
                            updated_at=at,
                        )
                    )
                self._state.save_item(
                    CollectionItem(
                        run_id=scope,
                        source_id=target.source_id,
                        url=target.url,
                        outcome=outcome,
                        reason_code=reason_code,
                        detail=bounded_detail(detail),
                        rung=rung,
                        terms_status=provenance.terms_status.value,
                        allowed_use=[use.value for use in provenance.allowed_use],
                        review_basis=provenance.review_basis,
                        fetch_id=record.fetch_id if record is not None else None,
                        status=record.status if record is not None else None,
                        bytes=record.bytes if record is not None else 0,
                        raw_sha256=record.raw_sha256 if record is not None else None,
                        raw_path=record.raw_path if record is not None else None,
                        latency_ms=record.latency_ms if record is not None else 0,
                        retry_count=record.retry_count if record is not None else 0,
                        attempted_at=at,
                    )
                )
        # The attempt is the fact and the checkpoint is progress metadata, so
        # the checkpoint is written after the attempt is committed: a broken
        # checkpoint must not roll the attempt back.
        self._write_checkpoint(scope, target)
        if result is not None and result.tool != "http":
            # The shared path logs its own captures. A capture taken by
            # another tool is logged here, so the fetch log holds every
            # capture and the line still parses as a fetch record. The
            # write is outside the transaction: a broken log must not roll
            # the attempt back either.
            self._log.append(result)
        return CollectionOutcome(
            scope=scope,
            source_id=target.source_id,
            url=target.url,
            outcome=outcome,
            reason_code=reason_code,
            detail=bounded_detail(detail),
            rung=rung,
            fetch_id=record.fetch_id if record is not None else None,
            status=record.status if record is not None else None,
            bytes=record.bytes if record is not None else 0,
            raw_sha256=record.raw_sha256 if record is not None else None,
            raw_path=record.raw_path if record is not None else None,
            latency_ms=record.latency_ms if record is not None else 0,
            retry_count=record.retry_count if record is not None else 0,
        )

    def _charge(self, source_id: str, *, count: int = 1) -> None:
        """Charge one fetch to the contract's budget.

        The tracker records the spend before it reports an overrun, so the
        ledger is right either way and the overrun is not raised over a
        capture that already happened. The overrun is logged instead, because
        the run has gone over its contract and nothing else would say so.

        The count is the number of tool attempts the collection spent, so a
        fallback that ran two tools is charged two fetches.
        """
        for dimension in (
            BudgetDimension.fetch_requests,
            BudgetDimension.source_requests_per_source,
        ):
            try:
                self._tracker.record(
                    dimension, count, stage=STAGE, source_id=source_id
                )
            except BudgetExceeded as exc:
                # The spend is recorded before the tracker refuses it, so the
                # ledger is right and the overrun is not raised over a
                # capture that already happened. The overrun is reported
                # here, because nothing else would say the run went over.
                self._log_overrun(exc, self._bound_scope)

    def _replay_tracker(self, scope: str) -> None:
        """Rebuild the contract's tracker from what the scope already spent."""
        for state in self._state.run_states(scope):
            if state.fetch_count <= 0:
                continue
            # Each dimension is replayed on its own, so one overrun does not
            # cost the other dimension its spend. The stage is the one the
            # live path charges, so the two paths read the same.
            for dimension in (
                BudgetDimension.fetch_requests,
                BudgetDimension.source_requests_per_source,
            ):
                try:
                    self._tracker.record(
                        dimension,
                        state.fetch_count,
                        stage=STAGE,
                        source_id=state.source_id,
                    )
                except BudgetExceeded as exc:
                    # The spend is recorded before the tracker refuses it, so
                    # the ledger is right and the refusal only reports the
                    # overrun.
                    self._log_overrun(exc, scope)

    def _emit(self, event: str, **fields: str) -> None:
        """Hand an event to the caller, when the caller wants events.

        The library does not log, and an event never carries a URL or any
        other text that came from the network. The caller owns the
        observability policy, so the event is handed over as data.

        shortcut: a caller that passes no sink hears nothing, so a budget
        overrun and a broken checkpoint are silent; the worker wires a sink
        that logs the events when it runs the COLLECT stage. Until then the
        spend is still recorded in the collection ledger, so only the report
        of it is missing.
        """
        if self._events is None:
            return
        self._events({"event": event, **fields})

    def _log_overrun(self, exc: BudgetExceeded, scope: str | None) -> None:
        """Say that the scope went over its contract, and by how much."""
        self._emit(
            "budget_overrun",
            dimension=exc.dimension,
            budget_scope=exc.scope,
            limit=str(exc.limit),
            spent=str(exc.spent),
            requested=str(exc.requested),
            run=scope or "",
        )

    def _gap_floors(self, scope: str) -> dict[str, datetime]:
        """The wall-clock floor per source: the last request plus the gap.

        The floor is what a restarted process has to wait out, so the gap a
        run left behind is not skipped.
        """
        floors: dict[str, datetime] = {}
        for state in self._state.run_states(scope):
            if not state.last_request_at:
                continue
            source, _, _ = self._planner._require(state.source_id)
            if source is None:
                continue
            last = datetime.fromisoformat(state.last_request_at)
            floors[state.source_id] = last + timedelta(
                seconds=source.rate_limit.min_interval_seconds
            )
        return floors

    def _persist_counts(self, source_id: str) -> None:
        """Add one request to the scope's ledger.

        The ledger is incremented by one, the way a fetch is, so a worker
        that wrote its own total could not overwrite what another worker
        spent.
        """
        scope = self._bound_scope
        if scope is None:
            return
        self._state.add_request(scope, source_id, at=self._now().isoformat())

    def _stored_request_count(self, source_id: str) -> int:
        """The requests the scope has spent against the source so far."""
        scope = self._bound_scope
        if scope is None:
            return 0
        return self._state.run_state(scope, source_id).request_count

    def _known_source(self, source_id: str) -> SourceConfig | None:
        """The source as the registry knows it, even when it may not be used."""
        try:
            return self._registry.get(source_id)
        except SourceBlocked:
            return None

    def _write_checkpoint(self, scope: str, target: CollectionTarget) -> None:
        """Name the step and the target in the scope's checkpoint.

        A checkpoint needs a run in the store, so one is written only when
        the scope names a run that exists. The state names the target and
        nothing else, so a replay writes the same checkpoint and not a
        conflicting one.

        The attempt is the fact and the checkpoint is progress metadata, so a
        checkpoint that cannot be written is reported and not raised: the
        attempt is already recorded, and failing the collection now would
        throw that away.
        """
        try:
            if not self._run_exists(scope):
                return
            save_checkpoint(
                self._conn,
                run_id=scope,
                stage=COLLECT_STAGE,
                step=f"{target.source_id} {target.url}",
                state={"source_id": target.source_id, "url": target.url},
                checkpoint_version=1,
            )
        except Exception as exc:
            # The event names the source, the scope and the error class. It
            # does not name the URL: the URL came from the network, and the
            # caller decides what may be logged. The message is the error
            # class and its text, bounded, because a database error carries
            # the values that failed and one of them is the URL.
            self._emit(
                "checkpoint_write_failed",
                source_id=target.source_id,
                scope=scope,
                error=type(exc).__name__,
                detail=_bounded_detail(exc, secret=target.url),
            )

    def _run_exists(self, run_id: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM research_runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        return row is not None

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        """One transaction for the state, the run ledger, the item and the capture.

        A worker handler may already own a transaction, and a transaction
        inside a transaction is an error, so the writes join the one that is
        already open.
        """
        if self._conn.in_transaction:
            yield self._conn
            return
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            yield self._conn
        except BaseException:
            self._conn.execute("ROLLBACK")
            raise
        self._conn.execute("COMMIT")


def make_collection_handler(orchestrator: CollectionOrchestrator):
    """The ``COLLECT`` stage handler for the existing worker.

    The handler serves a *target* job: a payload that names a source and a
    URL to collect. The scope of the collection is the unit the caps are
    counted in, and it comes from the job's run or from the payload's scope.
    Every job of one batch carries the same scope, so the caps are counted
    once per batch and not once per job. A job with neither is refused: the
    caps would be counted per job and the caps would multiply.

    The shipped ``source_collection`` schedule does not produce a target
    job. It produces a cycle trigger, a payload that names a schedule and an
    occurrence and no target, and the targets of a cycle come from
    discovery, which is a different wave. Such a job is refused with a
    message that names what is missing. The collection layer does not choose
    a target on its own: the registry declares domains, not paths, and
    discovery owns the target choice.
    """

    def handle(context: JobContext) -> dict[str, object]:
        payload = context.job.payload
        source_id = str(payload.get("source_id") or "")
        url = str(payload.get("url") or "")
        if not source_id or not url:
            raise ValueError(
                "a collection job needs a source_id and a url in its payload"
            )
        scope = str(payload.get("scope") or context.job.run_id or "")
        if not scope:
            raise ValueError(
                "a collection job needs a run id or a scope, so the caps are "
                "counted once for the batch and not once per job"
            )
        outcome = orchestrator.collect(scope, CollectionTarget(source_id, url))
        return {
            "source_id": outcome.source_id,
            "url": outcome.url,
            "outcome": outcome.outcome,
            "reason_code": outcome.reason_code,
            "fetch_id": outcome.fetch_id,
            "status": outcome.status,
            "bytes": outcome.bytes,
        }

    return handle


def enqueue_collection_jobs(
    conn: sqlite3.Connection,
    plan: CollectionPlan,
    *,
    run_id: str | None = None,
    scope: str | None = None,
) -> list[Job]:
    """Enqueue one ``COLLECT`` job per target in the plan's batch.

    The scope is the unit the caps are counted in, and every job of one
    batch carries it, so the caps are counted once per batch and not once
    per job. The scope defaults to the run, and a call with neither a run
    nor a scope is refused here, so the caller finds out at enqueue time.

    The idempotency key is derived from the scope, the source and the URL,
    so the same plan enqueued twice produces the same jobs and the second
    enqueue returns the rows the first one made.

    A job's ``run_id`` is set only when the caller names a run, because the
    queue holds a foreign key to ``research_runs``.
    """
    resolved = scope or run_id or plan.scope
    if not resolved:
        raise ValueError(
            "a collection job needs a run id or a scope, so the caps are "
            "counted once for the batch and not once per job"
        )
    jobs: list[Job] = []
    for target in plan.batch:
        jobs.append(
            enqueue_job(
                conn,
                stage=COLLECT_STAGE,
                idempotency_key=(
                    f"collect:{resolved}:{target.source_id}:{target.url}"
                ),
                payload={
                    "source_id": target.source_id,
                    "url": target.url,
                    "scope": resolved,
                },
                run_id=run_id,
            )
        )
    return jobs


def _rotate(targets: list[CollectionTarget]) -> list[CollectionTarget]:
    """Interleave the targets so one source cannot push the others out."""
    buckets: dict[str, list[CollectionTarget]] = {}
    order: list[str] = []
    for target in targets:
        if target.source_id not in buckets:
            buckets[target.source_id] = []
            order.append(target.source_id)
        buckets[target.source_id].append(target)
    rotated: list[CollectionTarget] = []
    while len(rotated) < len(targets):
        for source_id in order:
            bucket = buckets[source_id]
            if bucket:
                rotated.append(bucket.pop(0))
    return rotated


def _outcome_for(reason: str) -> str:
    """The outcome a reason code means.

    A tool that is not installed is a skip, not a failure: nothing was
    tried and nothing was spent. A reason that means the request did not
    complete is a failure. Everything else was refused before it went
    out.
    """
    if reason == "collector_unavailable":
        return "skipped"
    return "failed" if reason in FAILED_REASONS else "rejected"


def _charges(attempts) -> int:
    """The tool attempts that actually ran.

    A rung that was refused because its tool is not installed never ran,
    so it is not a fetch the run spent. Every tool that did run is one
    fetch, whatever it produced.
    """
    return sum(1 for attempt in attempts if attempt.outcome != "unavailable")


def _validators(state: SourceState | None) -> Validators | None:
    """The validators the server handed out last time, if it handed out any."""
    if state is None:
        return None
    if not state.etag and not state.last_modified:
        return None
    return Validators(etag=state.etag, last_modified=state.last_modified)


def _outcome_from_item(scope: str, item: CollectionItem) -> CollectionOutcome:
    """The outcome of a target that was already collected in this scope.

    The reason and the detail are the ones the attempt was recorded with, so
    the returned outcome does not drop what the record says.
    """
    return CollectionOutcome(
        scope=scope,
        source_id=item.source_id,
        url=item.url,
        outcome="duplicate",
        reason_code=item.reason_code,
        detail=item.detail,
        rung=item.rung,
        fetch_id=item.fetch_id,
        status=item.status,
        bytes=item.bytes,
        raw_sha256=item.raw_sha256,
        raw_path=item.raw_path,
        latency_ms=item.latency_ms,
        retry_count=item.retry_count,
    )

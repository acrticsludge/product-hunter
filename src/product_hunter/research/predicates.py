"""The metric and predicate registries: named, deterministic checks over a stage.

A metric counts something in one stage execution. A predicate passes or fails
with a reason. Both are pure functions: they never call a tool, never read a
file, and never look at the clock.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from urllib.parse import urlparse

from product_hunter.research.evidence import (
    ArtifactRecord,
    ResearchClaim,
    StageExecution,
    ToolCallProvenance,
    ToolCallStatus,
)
from product_hunter.research.query_plan import (
    MANDATORY_FAMILIES_BY_STAGE,
    covered_families,
    has_visible_text,
    is_known_family,
    uncovered_mandatory_families,
)

# Artifact kinds the metrics and predicates read.
KIND_DOCUMENT = "document"
KIND_CANDIDATE_URL = "candidate_url"
KIND_AUTHOR = "author"
KIND_COMMUNITY = "community"
KIND_EVIDENCE_SPAN = "evidence_span"
KIND_COUNTER_EVIDENCE = "counter_evidence"
KIND_CLUSTER_NEIGHBORS_CHECKED = "cluster_neighbors_checked"
KIND_NEIGHBOR_CHECK = "neighbor_check"
KIND_DUPLICATE_REVIEW = "duplicate_review"

# Tool names the metrics distinguish.
TOOL_SEARCH = "search"
TOOL_FETCH = "fetch"


@dataclass(frozen=True)
class PredicateContext:
    """Everything a predicate may look at: the run, the stage, the claims."""

    run_id: str
    execution: StageExecution
    claims: tuple[ResearchClaim, ...] = ()


MetricFn = Callable[[StageExecution], int]
PredicateFn = Callable[[PredicateContext], tuple[bool, str]]


def stage_calls(execution: StageExecution) -> list[ToolCallProvenance]:
    """The calls the execution's own stage made.

    A call that names another stage is not this stage's evidence: it cannot
    count towards a metric, satisfy a required tool, or stand in for a search
    the stage did not make. The rule lives here so the metrics, the
    predicates, and the gate share one implementation of it.
    """
    return [call for call in execution.tool_calls if call.stage == execution.stage]


def _successful(calls: Sequence[ToolCallProvenance], tool: str) -> list[ToolCallProvenance]:
    return [
        call
        for call in calls
        if call.tool == tool and call.status is ToolCallStatus.success
    ]


def _artifacts(execution: StageExecution, kind: str):
    return [artifact for artifact in execution.artifacts if artifact.kind == kind]


def _sum_items(execution: StageExecution, kind: str) -> int:
    return sum(artifact.item_count for artifact in _counted_artifacts(execution, kind))


def _counted_artifacts(execution: StageExecution, kind: str) -> list[ArtifactRecord]:
    """The artifacts of a kind a metric or a quality check is allowed to count.

    A metric counts an artifact that is valid, belongs to the stage being
    judged, and traces to a successful tool call of this stage, so an
    artifact the gate rejected does not count towards a threshold, and
    neither does one filed under another stage. The quality checks that read
    an artifact use the same helper, so the stage rule has one
    implementation.

    The rule is stricter than the gate's: the gate checks traceability only
    when the stage declares required tools, the metric always checks it. A
    stage that declares no required tools can hold artifacts the gate accepts
    and the metric refuses, while a traceable artifact in such a stage does
    count. That is deliberate: the metric fails closed.
    """
    return [
        artifact
        for artifact in _artifacts(execution, kind)
        if artifact.valid
        and artifact.stage == execution.stage
        and traces_to_successful_call(execution, artifact)
    ]


def _host_of(url: str) -> str | None:
    """Return the lowercase host of a URL, or None when there is no host."""
    try:
        host = urlparse(url).hostname
    except ValueError:
        # A URL the parser cannot read has no host: a truncated IPv6 literal
        # or a stray bracket. It contributes no domain, and the gate carries
        # on to return a verdict.
        return None
    if not host:
        return None
    # The host is trimmed before it is lowered: a trailing space would make
    # one source look like several.
    return host.strip().lower()


# --- metrics ---------------------------------------------------------------


def distinct_query_set(execution: StageExecution) -> set[str]:
    """The distinct non-empty queries among the successful search calls."""
    # The query is trimmed before it goes into the set: trailing whitespace
    # would make one query look like several. A query with no visible
    # character is no query at all, and it is left out.
    queries = {
        call.query_or_operation.strip()
        for call in _successful(stage_calls(execution), TOOL_SEARCH)
        if has_visible_text(call.query_or_operation)
    }
    queries.discard("")
    return queries


def distinct_queries(execution: StageExecution) -> int:
    """Distinct non-empty queries among the successful search calls."""
    return len(distinct_query_set(execution))


def query_family_set(execution: StageExecution) -> set[str]:
    """The distinct query families among the successful search calls.

    The plan owns the one implementation of the rule, so the metric and the
    mandatory family check cannot drift apart.
    """
    return set(covered_families(execution))


def query_families(execution: StageExecution) -> int:
    """Distinct query families among the successful search calls."""
    return len(query_family_set(execution))


def source_domain_set(execution: StageExecution) -> set[str]:
    """Distinct observed hosts in successful searches and fetches."""
    calls = stage_calls(execution)
    hosts = {
        host
        for call in _successful(calls, TOOL_SEARCH)
        for host in call.source_domains
    }
    for call in _successful(calls, TOOL_FETCH):
        host = _host_of(call.query_or_operation)
        if host:
            hosts.add(host)
    return hosts


def source_domains(execution: StageExecution) -> int:
    """Distinct observed hosts among successful search and fetch calls."""
    return len(source_domain_set(execution))


def new_documents(execution: StageExecution) -> int:
    """Documents produced by the stage."""
    return _sum_items(execution, KIND_DOCUMENT)


def candidate_urls(execution: StageExecution) -> int:
    """Candidate urls produced by the stage."""
    return _sum_items(execution, KIND_CANDIDATE_URL)


def independent_authors(execution: StageExecution) -> int:
    """Authors produced by the stage."""
    return _sum_items(execution, KIND_AUTHOR)


def independent_communities(execution: StageExecution) -> int:
    """Communities produced by the stage."""
    return _sum_items(execution, KIND_COMMUNITY)


def evidence_spans(execution: StageExecution) -> int:
    """Evidence spans produced by the stage."""
    return _sum_items(execution, KIND_EVIDENCE_SPAN)


def counter_evidence_items(execution: StageExecution) -> int:
    """Counter-evidence items produced by the stage."""
    return _sum_items(execution, KIND_COUNTER_EVIDENCE)


def neighbors_checked_per_cluster(execution: StageExecution) -> int:
    """The smallest neighbour count over the cluster checks, zero when there are none."""
    counts = [
        artifact.item_count
        for artifact in _counted_artifacts(execution, KIND_CLUSTER_NEIGHBORS_CHECKED)
    ]
    return min(counts) if counts else 0


def tool_calls(execution: StageExecution) -> int:
    """Number of tool calls the stage made."""
    return len(execution.tool_calls)


def successful_tool_calls(execution: StageExecution) -> int:
    """Number of tool calls the stage made that succeeded."""
    return sum(1 for call in execution.tool_calls if call.status is ToolCallStatus.success)


METRICS: dict[str, MetricFn] = {
    "distinct_queries": distinct_queries,
    "query_families": query_families,
    "source_domains": source_domains,
    "new_documents": new_documents,
    "candidate_urls": candidate_urls,
    "independent_authors": independent_authors,
    "independent_communities": independent_communities,
    "evidence_spans": evidence_spans,
    "counter_evidence_items": counter_evidence_items,
    "neighbors_checked_per_cluster": neighbors_checked_per_cluster,
    "tool_calls": tool_calls,
    "successful_tool_calls": successful_tool_calls,
}

METRIC_NAMES = frozenset(METRICS)

# The metrics that count distinct values rather than items. A run-level value
# of such a metric is the distinct count over the union of the run's
# executions, not the sum of their counts, which would count the same host,
# query, or family once per stage.
DISTINCT_METRIC_SETS: dict[str, Callable[[StageExecution], set[str]]] = {
    "distinct_queries": distinct_query_set,
    "query_families": query_family_set,
    "source_domains": source_domain_set,
}

# The metrics that are a minimum over what they count rather than a total. A
# run-level value of such a metric is the smallest of the run's per-stage
# values, not their sum: the smallest cluster check of the run is the
# smallest one any stage found, and a sum would report a number the run never
# had.
MINIMUM_METRICS: frozenset[str] = frozenset({"neighbors_checked_per_cluster"})

# The artifact kind each artifact-counting metric reads. The metric functions
# read the same KIND_* constants, so the mapping and the functions cannot
# drift. The tool-based metrics are absent: they count tool calls, not
# artifacts.
METRIC_ARTIFACT_KINDS: dict[str, str] = {
    "new_documents": KIND_DOCUMENT,
    "candidate_urls": KIND_CANDIDATE_URL,
    "independent_authors": KIND_AUTHOR,
    "independent_communities": KIND_COMMUNITY,
    "evidence_spans": KIND_EVIDENCE_SPAN,
    "counter_evidence_items": KIND_COUNTER_EVIDENCE,
    "neighbors_checked_per_cluster": KIND_CLUSTER_NEIGHBORS_CHECKED,
}


def metric_value(name: str, execution: StageExecution) -> int:
    """Return the value of a named metric over one stage execution."""
    return METRICS[name](execution)


# --- shared provenance helpers --------------------------------------------


def mismatched_calls(context: PredicateContext) -> list[ToolCallProvenance]:
    """Return the calls bound to another run or another stage."""
    return [
        call
        for call in context.execution.tool_calls
        if call.run_id != context.run_id or call.stage != context.execution.stage
    ]


def incomplete_calls(context: PredicateContext) -> list[ToolCallProvenance]:
    """Return the successful calls whose provenance is not complete.

    A successful call without a ``tool_call_id`` cannot be pointed at by an
    artifact. A call loaded from SQLite also needs its referenced payload in
    the durable result store; in-memory fixtures leave that fact unknown.
    """
    return [
        call
        for call in context.execution.tool_calls
        if call.status is ToolCallStatus.success
        and (
            not call.tool_call_id
            or call.result_is_durable is False
        )
    ]


def traces_to_successful_call(
    execution: StageExecution, artifact: ArtifactRecord
) -> bool:
    """Report whether the artifact points at a successful call of this stage.

    The gate and the metrics share this rule, so an artifact the gate refuses
    is not counted by a metric either.
    """
    handle = artifact.produced_by_tool_call_id
    if not handle:
        return False
    return any(
        call.tool_call_id == handle
        and call.stage == execution.stage
        and call.status is ToolCallStatus.success
        for call in execution.tool_calls
    )


def explicit_zero_outcome(execution: StageExecution) -> tuple[bool, str]:
    """Report whether the stage recorded a legitimate zero-result outcome.

    A zero counts only when it names at least one required query family, every
    required query family completed, every completed family was searched
    successfully, and no search call failed: an unsearched or failed query is
    not a zero.
    """
    outcome = execution.zero_result
    if not outcome.recorded:
        return False, "no zero-result outcome was recorded"
    if not outcome.required_query_families:
        # A zero that names no family searched nothing.
        return False, "the zero-result outcome names no required query family"
    missing = [
        family
        for family in outcome.required_query_families
        if family not in outcome.completed_query_families
    ]
    if missing:
        # The family names are free text from the evidence: report the count.
        return False, f"the zero-result outcome is missing {len(missing)} query families"
    searched = {
        call.query_family
        for call in _successful(stage_calls(execution), TOOL_SEARCH)
        if call.query_family
    }
    unsearched = [
        family for family in outcome.completed_query_families if family not in searched
    ]
    if unsearched:
        # The family names are free text from the evidence: report the count.
        return False, (
            f"the zero-result outcome claims {len(unsearched)} query families "
            "that were never searched"
        )
    failed = [
        call
        for call in stage_calls(execution)
        if call.tool == TOOL_SEARCH and call.status is not ToolCallStatus.success
    ]
    if failed:
        return False, "a search call did not succeed, so its result is not a zero"
    return True, "a zero-result outcome completed every required query family"


# --- predicates ------------------------------------------------------------


def non_empty_queries(context: PredicateContext) -> tuple[bool, str]:
    """True when a successful search call carries a non-empty query.

    The detail never quotes the query: a query can be a URL with credentials
    in it, and the detail ends up in a persisted violation.
    """
    for call in _successful(stage_calls(context.execution), TOOL_SEARCH):
        # The emptiness test is the shared one, so the predicate, the
        # distinct_queries metric and the covering test answer the same
        # question about a query that holds no visible character.
        if has_visible_text(call.query_or_operation):
            if call.tool_call_id:
                return True, f"search call {call.tool_call_id} carries a query"
            return True, "a search call carries a query"
    return False, "no successful search call carries a query"


def source_diversity(context: PredicateContext) -> tuple[bool, str]:
    """True when at least two distinct source domains were fetched."""
    count = source_domains(context.execution)
    if count >= 2:
        return True, f"{count} distinct source domains"
    return False, f"only {count} distinct source domain"


def nearest_neighbor_checked(context: PredicateContext) -> tuple[bool, str]:
    """True when a valid neighbour-check artifact of this stage traces to a successful call."""
    for artifact in _counted_artifacts(context.execution, KIND_NEIGHBOR_CHECK):
        return True, f"neighbour check {artifact.artifact_id} is valid"
    return False, "no valid neighbor_check artifact"


def duplicate_candidates_reviewed(context: PredicateContext) -> tuple[bool, str]:
    """True when a valid duplicate-review artifact of this stage traces to a successful call."""
    for artifact in _counted_artifacts(context.execution, KIND_DUPLICATE_REVIEW):
        return True, f"duplicate review {artifact.artifact_id} is valid"
    return False, "no valid duplicate_review artifact"


def counter_evidence_present_or_explicit_zero(context: PredicateContext) -> tuple[bool, str]:
    """True when counter-evidence exists, or when a real zero was recorded."""
    items = counter_evidence_items(context.execution)
    if items >= 1:
        return True, f"{items} counter-evidence items"
    ok, detail = explicit_zero_outcome(context.execution)
    return ok, detail


def provenance_complete(context: PredicateContext) -> tuple[bool, str]:
    """True when every successful call carries complete, correctly bound provenance."""
    incomplete = incomplete_calls(context)
    if incomplete:
        return False, f"{len(incomplete)} successful tool calls carry no tool_call_id"
    mismatched = mismatched_calls(context)
    if mismatched:
        return False, f"{len(mismatched)} tool calls belong to another run or stage"
    return True, "every successful tool call carries complete provenance"


def artifacts_valid(context: PredicateContext) -> tuple[bool, str]:
    """True when every artifact of the stage is valid."""
    invalid = [artifact for artifact in context.execution.artifacts if not artifact.valid]
    if invalid:
        return False, f"{len(invalid)} artifacts are invalid"
    return True, "every artifact is valid"


def claim_evidence_links(context: PredicateContext) -> tuple[bool, str]:
    """True when every claim of the run carries at least one evidence reference."""
    unlinked = [claim for claim in context.claims if not claim.evidence_refs]
    if unlinked:
        return False, f"{len(unlinked)} claims carry no evidence reference"
    return True, "every claim carries an evidence reference"


def mandatory_query_families(context: PredicateContext) -> tuple[bool, str]:
    """True when the stage searched every query family it has to search.

    A family is covered by a search that carries a query: a search with a
    whitespace-only query covers nothing, so it does not count towards the
    families the stage had to search.

    The missing families are named: they are the worker's own constants, not
    text from the evidence. A stage that has no mandatory family passes.
    """
    stage = context.execution.stage
    required = MANDATORY_FAMILIES_BY_STAGE.get(stage, ())
    if not required:
        return True, "the stage has no mandatory query family"
    missing = uncovered_mandatory_families(context.execution, stage)
    if missing:
        return False, (
            f"the mandatory query families were not searched: {', '.join(missing)}"
        )
    return True, f"every mandatory query family was searched ({len(required)} families)"


def known_query_families(context: PredicateContext) -> tuple[bool, str]:
    """True when every search call of the stage names a family the worker owns.

    A search with no family at all fails: an unassigned search is not a
    worker-assigned query. The family names come from the tool calls, so the
    reason reports counts and never the names themselves.
    """
    calls = [call for call in stage_calls(context.execution) if call.tool == TOOL_SEARCH]
    if not calls:
        return False, "the stage made no search call"
    unnamed = [call for call in calls if not (call.query_family or "").strip()]
    if unnamed:
        return False, f"{len(unnamed)} search calls carry no query family"
    unknown = [call for call in calls if not is_known_family(call.query_family or "")]
    if unknown:
        return False, (
            f"{len(unknown)} search calls name a query family the worker does not own"
        )
    return True, f"{len(calls)} search calls name a worker-owned query family"


PREDICATES: dict[str, PredicateFn] = {
    "non_empty_queries": non_empty_queries,
    "source_diversity": source_diversity,
    "nearest_neighbor_checked": nearest_neighbor_checked,
    "duplicate_candidates_reviewed": duplicate_candidates_reviewed,
    "counter_evidence_present_or_explicit_zero": counter_evidence_present_or_explicit_zero,
    "provenance_complete": provenance_complete,
    "artifacts_valid": artifacts_valid,
    "claim_evidence_links": claim_evidence_links,
    "mandatory_query_families": mandatory_query_families,
    "known_query_families": known_query_families,
}

# The artifact kind each artifact-reading predicate reads. The predicate
# functions read the same KIND_* constants, so the mapping and the functions
# cannot drift. The absent ones read tool calls (non_empty_queries,
# source_diversity, provenance_complete, mandatory_query_families,
# known_query_families), every artifact kind (artifacts_valid), or the run's
# claims (claim_evidence_links), so none of them names a single kind.
PREDICATE_ARTIFACT_KINDS: dict[str, str] = {
    "nearest_neighbor_checked": KIND_NEIGHBOR_CHECK,
    "duplicate_candidates_reviewed": KIND_DUPLICATE_REVIEW,
    "counter_evidence_present_or_explicit_zero": KIND_COUNTER_EVIDENCE,
}

PREDICATE_NAMES = frozenset(PREDICATES)

"""The coverage rule: when a search that found nothing really found nothing.

A zero is reachable only when every required family was attempted and
completed, no attempt failed, and the query, document and domain floors were
met. Everything else is incomplete, and an incomplete never reads as "nothing
exists".
"""

from __future__ import annotations

from collections.abc import Sequence
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from product_hunter.research.evidence import ToolCallStatus


class CoverageStatus(str, Enum):
    complete = "complete"
    incomplete = "incomplete"


class SearchAttempt(BaseModel):
    """One search: the family it served and what came back."""

    model_config = ConfigDict(extra="forbid")

    family: str = Field(min_length=1)
    query: str
    status: ToolCallStatus
    document_count: int = Field(default=0, ge=0)
    domains: list[str] = Field(default_factory=list)
    tool_call_id: str = ""
    error: str | None = None


class CoverageRequirements(BaseModel):
    """The floors a search has to clear before its zero counts.

    A floor of zero is not a floor: it switches the check off without saying
    so, which is the wave 1 lesson about a non-positive limit. Every floor is
    positive.
    """

    model_config = ConfigDict(extra="forbid")

    min_queries: int = Field(default=6, ge=1)
    min_documents: int = Field(default=1, ge=1)
    min_domains: int = Field(default=2, ge=1)
    allow_empty_results: bool = False


class ResearchCoverage(BaseModel):
    """What the search covered, and whether that coverage is complete."""

    model_config = ConfigDict(extra="forbid")

    required_families: list[str]
    attempted_families: list[str]
    completed_families: list[str]
    failed_families: list[str]
    missing_families: list[str]
    distinct_queries: int = Field(default=0, ge=0)
    document_count: int = Field(default=0, ge=0)
    distinct_domains: list[str]
    tool_failures: int = Field(default=0, ge=0)
    status: CoverageStatus
    reasons: list[str]


def _family_name(family: str) -> str:
    """Return the plain name of a family."""
    return family.strip()


def _domain_name(domain: str) -> str:
    """Return the plain name of a source domain."""
    return domain.strip().lower()


def assess_coverage(
    required_families: Sequence[str],
    attempts: Sequence[SearchAttempt],
    requirements: CoverageRequirements,
) -> ResearchCoverage:
    """Judge whether the search covered enough to support a zero."""
    required = [name for name in (_family_name(f) for f in required_families) if name]

    attempted: list[str] = []
    completed: list[str] = []
    queries: set[str] = set()
    documents = 0
    domains: set[str] = set()
    failures = 0

    for attempt in attempts:
        family = _family_name(attempt.family)
        if family and family not in attempted:
            attempted.append(family)
        if attempt.status is not ToolCallStatus.success:
            # A failed call is counted even when a later attempt of the same
            # family succeeds: a flaky search cannot support a zero.
            failures += 1
            continue
        if family and family not in completed:
            completed.append(family)
        query = attempt.query.strip()
        if query:
            queries.add(query)
        documents += attempt.document_count
        for domain in attempt.domains:
            name = _domain_name(domain)
            if name:
                domains.add(name)

    failed = [family for family in attempted if family not in completed]
    missing = [family for family in required if family not in attempted]
    uncovered = [family for family in required if family not in completed]

    # The reasons carry counts and fixed phrases only: a reason ends up in a
    # persisted violation, and a query or a URL can carry a token.
    reasons: list[str] = []
    if missing:
        reasons.append(f"{len(missing)} required query families were never attempted")
    if uncovered:
        reasons.append(f"{len(uncovered)} required query families had no successful attempt")
    if failures:
        reasons.append(f"{failures} tool calls did not succeed")
    if len(queries) < requirements.min_queries:
        reasons.append(
            f"{len(queries)} distinct successful queries, {requirements.min_queries} required"
        )
    if not requirements.allow_empty_results and documents < requirements.min_documents:
        reasons.append(f"{documents} documents examined, {requirements.min_documents} required")
    if not requirements.allow_empty_results and len(domains) < requirements.min_domains:
        reasons.append(
            f"{len(domains)} distinct source domains, {requirements.min_domains} required"
        )

    complete = (
        not missing
        and not uncovered
        and failures == 0
        and len(queries) >= requirements.min_queries
        and (
            requirements.allow_empty_results
            or (
                documents >= requirements.min_documents
                and len(domains) >= requirements.min_domains
            )
        )
    )

    return ResearchCoverage(
        required_families=required,
        attempted_families=attempted,
        completed_families=completed,
        failed_families=failed,
        missing_families=missing,
        distinct_queries=len(queries),
        document_count=documents,
        distinct_domains=sorted(domains),
        tool_failures=failures,
        status=CoverageStatus.complete if complete else CoverageStatus.incomplete,
        reasons=reasons,
    )

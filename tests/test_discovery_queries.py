from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from product_hunter.discovery.families import DERIVED_FAMILIES, QueryFamily
from product_hunter.discovery.queries import (
    Query,
    SourceCandidate,
    generate_derived_queries,
    generate_queries,
    is_duplicate,
    normalize_query,
    query_id,
)
from product_hunter.normalization import Domain, NormalizedProblem, normalize_fields

FROZEN = datetime(2026, 10, 21, 12, 0, 0, tzinfo=timezone.utc)
NAIVE = datetime(2026, 10, 21, 12, 0, 0)


def _query(text: str, source_id: str = "hn_algolia") -> Query:
    return Query(
        query_id=query_id(text, source_id),
        text=text,
        normalized=normalize_query(text),
        family=QueryFamily.problem,
        source_id=source_id,
        created_at=FROZEN,
        assignment_reason="template:problem-0",
    )


def _problem() -> NormalizedProblem:
    return normalize_fields(
        domain=Domain.operations,
        actor="finance lead",
        action="reconcile invoices",
        object_="spreadsheet",
        consequence="a lost afternoon",
        normalization_version="norm-v1",
    )


def test_the_query_id_is_the_golden_value_of_its_basis():
    identifier = query_id("invoice reconciliation", "hn_algolia")
    assert identifier == "q_6dad596aaa8664d9"
    assert identifier.startswith("q_")
    assert len(identifier) == 18


def test_a_different_source_gives_a_different_id():
    assert query_id("invoice reconciliation", "hn_algolia") != query_id(
        "invoice reconciliation", "github_issues"
    )


def test_the_id_is_taken_over_the_normalized_text():
    assert query_id("Invoice, Reconciliation!", "hn_algolia") == query_id(
        "invoice reconciliation", "hn_algolia"
    )


def test_normalization_folds_case_and_punctuation():
    assert normalize_query("Invoice, Reconciliation!") == "invoice reconciliation"
    assert normalize_query("  invoice   reconciliation ") == "invoice reconciliation"


def test_an_equal_text_is_a_duplicate():
    assert is_duplicate("Invoice Reconciliation", "invoice reconciliation") is True
    assert is_duplicate("invoice reconciliation", "invoice reconciliation") is True


def test_a_reworded_text_above_the_threshold_is_a_duplicate():
    left = "invoice reconciliation for accountants"
    right = "invoice reconciliation for bookkeepers"
    assert is_duplicate(left, right, 0.6) is True
    assert is_duplicate(left, right, 0.7) is False


def test_a_different_text_is_not_a_duplicate():
    assert is_duplicate("invoice reconciliation", "kubernetes ingress") is False


def test_a_threshold_of_zero_still_refuses_an_empty_token_set():
    # An empty text has no tokens, so there is nothing to compare and
    # nothing to be a near duplicate of, whatever the threshold is.
    assert is_duplicate("", "invoice reconciliation", 0.0) is False
    assert is_duplicate("invoice reconciliation", "", 0.0) is False


def test_generation_assigns_the_family_of_the_template():
    queries = generate_queries(
        "invoice reconciliation",
        [QueryFamily.problem, QueryFamily.workaround],
        "hn_algolia",
        now=FROZEN,
    )
    assert len(queries) == 8
    for query in queries:
        assert query.family in (QueryFamily.problem, QueryFamily.workaround)
        assert query.source_id == "hn_algolia"
        assert query.assigned_by == "worker"
        assert query.assignment_reason == f"template:{query.template_id}"
        assert query.query_id == query_id(query.text, "hn_algolia")
        assert query.seed == "invoice reconciliation"
    assert [query.template_id for query in queries[:4]] == [
        f"problem-{index}" for index in range(4)
    ]


def test_derived_generation_returns_only_the_two_derived_families():
    problem = _problem()
    queries = generate_derived_queries(problem, "hn_algolia", now=FROZEN)
    assert {query.family for query in queries} == set(DERIVED_FAMILIES)
    assert all(query.seed == problem.normalized_key for query in queries)
    assert all("reconcile invoices spreadsheet" in query.text for query in queries)


def test_the_derived_subject_is_rendered_on_one_line():
    problem = normalize_fields(
        domain=Domain.operations,
        actor="finance lead",
        action="reconcile\ninvoices",
        object_="spreadsheet",
        consequence="a lost afternoon",
        normalization_version="norm-v1",
    )
    queries = generate_derived_queries(problem, "hn_algolia", now=FROZEN)
    assert queries
    for query in queries:
        assert "\n" not in query.text
        assert "reconcile invoices spreadsheet" in query.text


def test_a_source_candidate_starts_as_a_proposal():
    candidate = SourceCandidate(
        candidate_id="cand_1",
        source_id="new_forum",
        domain="forum.example",
        discovered_by="q_0123456789abcdef",
        discovered_at=FROZEN,
    )
    assert candidate.status == "proposed"
    assert candidate.terms_status == "unknown"
    assert candidate.allowed_use == []


def test_a_source_candidate_refuses_to_be_built_as_anything_but_a_proposal():
    with pytest.raises(ValidationError):
        SourceCandidate(
            candidate_id="cand_1",
            source_id="new_forum",
            discovered_by="q_0123456789abcdef",
            discovered_at=FROZEN,
            status="approved",
        )
    with pytest.raises(ValidationError):
        SourceCandidate(
            candidate_id="cand_1",
            source_id="new_forum",
            discovered_by="q_0123456789abcdef",
            discovered_at=FROZEN,
            terms_status="permitted",
        )
    with pytest.raises(ValidationError):
        SourceCandidate(
            candidate_id="cand_1",
            source_id="new_forum",
            discovered_by="q_0123456789abcdef",
            discovered_at=FROZEN,
            allowed_use=["discovery"],
        )


def test_a_query_refuses_a_foreign_assignment():
    with pytest.raises(ValidationError) as excinfo:
        Query(
            query_id=query_id("x", "s"),
            text="x",
            normalized="x",
            family=QueryFamily.problem,
            source_id="s",
            assigned_by="model",
            created_at=FROZEN,
            assignment_reason="template:problem-0",
        )
    assert "assigned_by must be 'worker'" in str(excinfo.value)


def test_a_query_refuses_a_normalized_text_that_is_not_the_normalized_form():
    with pytest.raises(ValidationError) as excinfo:
        Query(
            query_id=query_id("x", "s"),
            text="x",
            normalized="y",
            family=QueryFamily.problem,
            source_id="s",
            created_at=FROZEN,
            assignment_reason="template:problem-0",
        )
    assert "normalized must be the normalized form" in str(excinfo.value)


def test_a_query_refuses_an_id_that_does_not_match_its_text():
    with pytest.raises(ValidationError) as excinfo:
        Query(
            query_id="q_0123456789abcdef",
            text="x",
            normalized="x",
            family=QueryFamily.problem,
            source_id="s",
            created_at=FROZEN,
            assignment_reason="template:problem-0",
        )
    assert "query_id must be" in str(excinfo.value)


def test_a_query_refuses_a_naive_timestamp():
    with pytest.raises(ValidationError) as excinfo:
        Query(
            query_id=query_id("x", "s"),
            text="x",
            normalized="x",
            family=QueryFamily.problem,
            source_id="s",
            created_at=NAIVE,
            assignment_reason="template:problem-0",
        )
    assert "created_at must be timezone-aware" in str(excinfo.value)

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from product_hunter.research.contract import (
    load_research_contract,
    validate_contract,
)
from product_hunter.research.evidence import (
    StageExecution,
    StageStatus,
    ToolCallProvenance,
    ToolCallStatus,
)
from product_hunter.research.gate import ViolationCode, evaluate_stage
from product_hunter.research.predicates import (
    PREDICATES,
    PredicateContext,
    distinct_queries,
)
from product_hunter.research.query_plan import (
    MANDATORY_FAMILIES_BY_STAGE,
    QUERY_FAMILIES,
    QUERY_TEMPLATES,
    QUERY_VARIANTS,
    QueryTemplate,
    covered_families,
    is_known_family,
    known_families,
    plan_for_stage,
    render_query,
    uncovered_mandatory_families,
)

FROZEN = datetime(2026, 10, 21, 12, 0, 0, tzinfo=timezone.utc)

# The ten initial query families of section 7A, in the spec's order. The set
# is spelled out here so a rename of a family is a deliberate act.
SPEC_FAMILIES = {
    "direct_pain",
    "manual_workflow",
    "workaround",
    "alternative_to",
    "failed_solution",
    "feature_request",
    "switching_pain",
    "cost_consequence",
    "role_workflow",
    "trigger_incident",
}

# The eight controlled variants of section 7A.
SPEC_VARIANTS = {
    "problem_wording",
    "synonym_domain_wording",
    "workaround_wording",
    "alternative_competitor_wording",
    "failed_solution_wording",
    "consequence_cost_wording",
    "role_industry_wording",
    "source_community_wording",
}


def _call(
    stage: str = "discovery",
    *,
    family: str | None = "direct_pain",
    status: ToolCallStatus = ToolCallStatus.success,
    tool: str = "search",
    tool_call_id: str | None = "call-1",
    query: str = "a query",
) -> ToolCallProvenance:
    data = {
        "run_id": "run-1",
        "stage": stage,
        "agent": "researcher",
        "tool": tool,
        "purpose": "collect evidence",
        "timestamp": FROZEN,
        "query_or_operation": query,
        "result_ref": f"raw/{stage}",
        "result_count": 3,
        "status": status,
        "tool_call_id": tool_call_id,
    }
    if family is not None:
        data["query_family"] = family
    return ToolCallProvenance(**data)


def _execution(
    stage: str = "discovery", calls: list[ToolCallProvenance] | None = None
) -> StageExecution:
    return StageExecution(
        stage=stage,
        status=StageStatus.succeeded,
        started_at=FROZEN,
        finished_at=FROZEN,
        tool_calls=list(calls) if calls is not None else [_call(stage)],
    )


def _context(execution: StageExecution) -> PredicateContext:
    return PredicateContext(run_id="run-1", execution=execution, claims=())


def test_the_worker_owns_the_ten_query_families_of_the_spec():
    assert known_families() == SPEC_FAMILIES
    assert len(known_families()) == 10
    assert set(QUERY_FAMILIES) == SPEC_FAMILIES


def test_a_family_name_the_worker_does_not_own_is_not_known():
    assert is_known_family("direct_pain") is True
    assert is_known_family("direct_pain ") is True
    assert is_known_family("problem") is False
    assert is_known_family("") is False


def test_the_templates_cover_every_family_and_every_variant():
    families = {template.family for template in QUERY_TEMPLATES}
    variants = {template.variant for template in QUERY_TEMPLATES}

    assert families == SPEC_FAMILIES
    assert variants == SPEC_VARIANTS
    # The variants are spread over the families: no family carries them all.
    for family in SPEC_FAMILIES:
        owned = [
            template for template in QUERY_TEMPLATES if template.family == family
        ]
        assert owned
        assert len({template.variant for template in owned}) < len(SPEC_VARIANTS)


def test_every_variant_is_spread_over_more_than_one_family():
    for variant in SPEC_VARIANTS:
        families = {
            template.family for template in QUERY_TEMPLATES if template.variant == variant
        }
        assert len(families) >= 2, variant


def test_the_mandatory_templates_hold_the_negative_and_the_alternative_to_family():
    mandatory = {
        template.family for template in QUERY_TEMPLATES if template.mandatory
    }

    assert "failed_solution" in mandatory
    assert "alternative_to" in mandatory


def test_the_plan_of_a_stage_is_the_same_every_time_it_is_asked_for():
    first = plan_for_stage("validation")
    second = plan_for_stage("validation")

    assert first == second
    assert isinstance(first, tuple)


def test_a_stage_with_no_mandatory_family_has_no_plan():
    assert plan_for_stage("normalization") == ()
    assert plan_for_stage("clustering") == ()
    assert plan_for_stage("scoring") == ()
    # A stage the worker knows nothing about has no plan either.
    assert plan_for_stage("ghost") == ()


def test_the_plan_of_a_searching_stage_holds_its_mandatory_families():
    plan = plan_for_stage("counter_evidence")

    families = {template.family for template in plan}
    for family in MANDATORY_FAMILIES_BY_STAGE["counter_evidence"]:
        assert family in families
    # The mandatory templates come first, then the ones it may search.
    assert all(template.mandatory for template in plan[:6])


def test_a_rendered_query_carries_the_topic_and_stays_on_one_line():
    template = QUERY_TEMPLATES[0]
    rendered = render_query(template.template, "invoice chasing")

    assert "invoice chasing" in rendered
    assert "{topic}" not in rendered
    assert rendered.strip()
    assert "\n" not in rendered
    assert template.render("invoice chasing") == rendered


def test_a_query_without_a_topic_is_refused():
    with pytest.raises(ValueError):
        render_query(QUERY_TEMPLATES[0].template, "   ")

    with pytest.raises(ValueError):
        render_query(QUERY_TEMPLATES[0].template, "")


def test_a_topic_that_breaks_the_query_over_two_lines_is_refused():
    with pytest.raises(ValueError):
        render_query(QUERY_TEMPLATES[0].template, "invoices\nand receipts")


def test_only_the_successful_search_calls_of_the_stage_cover_a_family():
    execution = _execution(
        "discovery",
        calls=[
            _call("discovery", family="direct_pain", tool_call_id="call-1"),
            _call(
                "discovery",
                family="failed_solution",
                tool_call_id="call-2",
                status=ToolCallStatus.failure,
            ),
            _call("other", family="alternative_to", tool_call_id="call-3"),
            _call(
                "discovery",
                family="workaround",
                tool_call_id="call-4",
                tool="fetch",
                query="https://example.com/a",
            ),
        ],
    )

    assert covered_families(execution) == {"direct_pain"}


def test_the_families_a_stage_missed_are_named():
    execution = _execution(
        "validation",
        calls=[
            _call("validation", family="direct_pain", tool_call_id="call-1"),
            _call("validation", family="alternative_to", tool_call_id="call-2"),
        ],
    )

    missing = uncovered_mandatory_families(execution, "validation")

    assert missing == ("failed_solution",)
    assert set(missing) <= set(MANDATORY_FAMILIES_BY_STAGE["validation"])


def test_the_mandatory_family_check_passes_when_the_stage_searched_them():
    execution = _execution(
        "validation",
        calls=[
            _call("validation", family="direct_pain", tool_call_id="call-1"),
            _call("validation", family="failed_solution", tool_call_id="call-2"),
            _call("validation", family="alternative_to", tool_call_id="call-3"),
        ],
    )

    passed, reason = PREDICATES["mandatory_query_families"](_context(execution))

    assert passed is True
    assert "mandatory query family" in reason


def test_the_mandatory_family_check_fails_and_names_the_missing_family():
    execution = _execution(
        "validation",
        calls=[
            _call("validation", family="direct_pain", tool_call_id="call-1"),
            _call("validation", family="alternative_to", tool_call_id="call-2"),
        ],
    )

    passed, reason = PREDICATES["mandatory_query_families"](_context(execution))

    assert passed is False
    assert "failed_solution" in reason


def test_a_whitespace_only_search_covers_no_family():
    execution = _execution(
        "validation",
        calls=[
            _call(
                "validation",
                family="direct_pain",
                tool_call_id="call-1",
                query="   ",
            ),
        ],
    )

    # A search with no query is not a search, so it covers no family.
    assert covered_families(execution) == frozenset()
    assert uncovered_mandatory_families(execution, "validation") == (
        "direct_pain",
        "failed_solution",
        "alternative_to",
    )

    passed, reason = PREDICATES["mandatory_query_families"](_context(execution))

    assert passed is False
    assert "direct_pain" in reason


def test_a_search_with_no_visible_character_covers_no_family():
    execution = _execution(
        "validation",
        calls=[
            _call(
                "validation",
                family="direct_pain",
                tool_call_id="call-1",
                query="\u200b",
            ),
        ],
    )

    # A zero-width space is not whitespace, so the query survives the trim
    # and carries nothing. The covering test, the predicate and the metric
    # answer the same question about it.
    assert covered_families(execution) == frozenset()
    assert uncovered_mandatory_families(execution, "validation") == (
        "direct_pain",
        "failed_solution",
        "alternative_to",
    )
    assert distinct_queries(execution) == 0

    passed, reason = PREDICATES["non_empty_queries"](_context(execution))

    assert passed is False
    assert "no successful search call carries a query" in reason


def test_the_validation_stage_fails_on_four_whitespace_only_searches():
    contract = load_research_contract()
    execution = _execution(
        "validation",
        calls=[
            _call(
                "validation",
                family="direct_pain",
                tool_call_id="call-1",
                query="   ",
            ),
            _call(
                "validation",
                family="failed_solution",
                tool_call_id="call-2",
                query="   ",
            ),
            _call(
                "validation",
                family="alternative_to",
                tool_call_id="call-3",
                query="   ",
            ),
            _call(
                "validation",
                family="workaround",
                tool_call_id="call-4",
                query="   ",
            ),
        ],
    )

    result = evaluate_stage(contract, "run-1", "validation", execution)

    failed = {
        violation.detail.get("check")
        for violation in result.violations
        if violation.code is ViolationCode.quality_check_failed
    }
    assert "non_empty_queries" in failed
    assert "mandatory_query_families" in failed
    assert result.passed is False


def test_the_mandatory_family_check_passes_for_a_stage_without_mandatory_families():
    execution = _execution("normalization", calls=[])

    passed, reason = PREDICATES["mandatory_query_families"](_context(execution))

    assert passed is True
    assert "no mandatory query family" in reason


def test_a_search_without_a_query_family_is_not_a_worker_assigned_query():
    execution = _execution(
        "discovery",
        calls=[
            _call("discovery", family="direct_pain", tool_call_id="call-1"),
            _call("discovery", family=None, tool_call_id="call-2"),
        ],
    )

    passed, reason = PREDICATES["known_query_families"](_context(execution))

    assert passed is False
    assert "1 search calls carry no query family" in reason


def test_a_search_that_names_an_invented_family_fails_without_quoting_it():
    execution = _execution(
        "discovery",
        calls=[
            _call("discovery", family="direct_pain", tool_call_id="call-1"),
            _call("discovery", family="my_own_bucket", tool_call_id="call-2"),
        ],
    )

    passed, reason = PREDICATES["known_query_families"](_context(execution))

    assert passed is False
    # The family name is text from the tool call, so the reason carries the
    # count and never the name.
    assert "my_own_bucket" not in reason
    assert "does not own" in reason


def test_searches_that_name_worker_owned_families_pass():
    execution = _execution(
        "discovery",
        calls=[
            _call("discovery", family="direct_pain", tool_call_id="call-1"),
            _call("discovery", family="failed_solution", tool_call_id="call-2"),
        ],
    )

    passed, reason = PREDICATES["known_query_families"](_context(execution))

    assert passed is True
    assert "worker-owned" in reason


@pytest.mark.parametrize(
    "family",
    [
        "competitor_alternative",
        "counter_not_worth",
        "killer_market_size",
    ],
)
def test_investigation_query_families_are_worker_owned(family):
    assert is_known_family(family)


def test_the_shipped_contract_still_validates():
    assert validate_contract(load_research_contract()) == []


def test_the_shipped_contract_names_the_new_checks_the_registry_knows():
    contract = load_research_contract()
    names = {check for spec in contract.stages.values() for check in spec.quality_checks}

    assert "mandatory_query_families" in names
    assert "known_query_families" in names
    assert names <= set(PREDICATES)


def test_the_shipped_contract_searches_the_stages_that_declare_a_mandatory_family():
    contract = load_research_contract()

    for stage, spec in contract.stages.items():
        searches = "search" in spec.required_tools
        if searches:
            assert MANDATORY_FAMILIES_BY_STAGE[stage]
            assert "mandatory_query_families" in spec.quality_checks
            assert "known_query_families" in spec.quality_checks
        else:
            assert MANDATORY_FAMILIES_BY_STAGE[stage] == ()
            assert "mandatory_query_families" not in spec.quality_checks
            assert "known_query_families" not in spec.quality_checks


def test_a_template_is_a_frozen_record_of_its_family_and_variant():
    template = QueryTemplate(
        family="direct_pain",
        variant="problem_wording",
        template="{topic} is a pain",
        mandatory=True,
    )

    assert template.family == "direct_pain"
    assert template.variant == "problem_wording"
    assert template.mandatory is True
    assert template.render("invoices") == "invoices is a pain"

from __future__ import annotations

import pytest
from pydantic import ValidationError

from product_hunter.research.investigation.queries import (
    PLACEHOLDER,
    QueryFamily,
    QueryTemplate,
    area_templates,
    build_queries,
    competitor_templates,
    counter_evidence_templates,
    mandatory_templates,
    render,
    startup_killer_templates,
)

# The mission's query families, verbatim.
COMPETITOR_FAMILIES = {
    "competitor_alternative": "{problem} alternative",
    "competitor_existing_solution": "{problem} existing solution",
    "competitor_software": "{problem} software",
    "competitor_pricing": "{problem} pricing",
}

COUNTER_FAMILIES = {
    "counter_not_worth": "{problem} not worth",
    "counter_complaints": "{problem} complaints",
    "counter_workaround": "{problem} workaround",
}

# The areas the investigation searches. A research area is a string enum, so
# the area and its name are the same value.
AREA_NAMES = [
    "competitor",
    "existing_solution",
    "alternative",
    "workaround",
    "market_validation",
    "willingness_to_pay",
    "distribution",
    "counter_evidence",
    "startup_killer",
]


def test_the_four_competitor_families_are_present_verbatim():
    templates = competitor_templates()

    assert len(templates) == 4
    assert {template.family.value: template.template for template in templates} == (
        COMPETITOR_FAMILIES
    )


def test_the_three_counter_families_are_present_verbatim():
    templates = counter_evidence_templates()

    assert len(templates) == 3
    assert {template.family.value: template.template for template in templates} == (
        COUNTER_FAMILIES
    )


def test_mandatory_templates_is_the_union_of_the_two():
    mandatory = mandatory_templates()

    assert len(mandatory) == 7
    assert [template.family for template in mandatory] == [
        *(template.family for template in competitor_templates()),
        *(template.family for template in counter_evidence_templates()),
    ]


def test_every_template_carries_the_placeholder():
    for template in (*mandatory_templates(), *startup_killer_templates()):
        assert PLACEHOLDER in template.template
    for name in AREA_NAMES:
        for template in area_templates(name):
            assert PLACEHOLDER in template.template


def test_a_template_without_the_placeholder_is_refused():
    with pytest.raises(ValidationError) as excinfo:
        QueryTemplate(
            family=QueryFamily.competitor_software,
            template="spreadsheet pain software",
            purpose="find the software that competes with this",
        )

    assert "placeholder" in str(excinfo.value)


def test_render_refuses_an_empty_problem():
    template = competitor_templates()[0]

    with pytest.raises(ValueError) as excinfo:
        render(template, "   ")

    assert "must not be empty" in str(excinfo.value)


def test_render_replaces_the_placeholder_with_the_trimmed_problem():
    template = next(
        t for t in competitor_templates() if t.family is QueryFamily.competitor_alternative
    )

    query = render(template, "  spreadsheet pain  ")

    assert query.text == "spreadsheet pain alternative"
    assert query.family is QueryFamily.competitor_alternative
    assert query.template == "{problem} alternative"
    assert query.purpose == template.purpose


def test_build_queries_renders_one_query_per_template_in_order():
    templates = competitor_templates()

    queries = build_queries("spreadsheet pain", templates)

    assert [query.family for query in queries] == [template.family for template in templates]
    assert [query.text for query in queries] == [
        "spreadsheet pain alternative",
        "spreadsheet pain existing solution",
        "spreadsheet pain software",
        "spreadsheet pain pricing",
    ]


def test_the_area_templates_are_not_empty():
    for name in AREA_NAMES:
        assert area_templates(name)


def test_an_area_without_templates_is_refused():
    with pytest.raises(ValueError) as excinfo:
        area_templates("no_such_area")

    assert "no templates" in str(excinfo.value)


def test_the_startup_killer_covers_every_falsification_dimension():
    templates = startup_killer_templates()

    assert len(templates) == 10
    assert len({template.family for template in templates}) == 10

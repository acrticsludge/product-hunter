from __future__ import annotations

from product_hunter.discovery.families import (
    DEFAULT_FAMILY,
    DERIVED_FAMILIES,
    FAMILY_MARKERS,
    FAMILY_SPECS,
    MANDATORY_FAMILIES,
    QueryFamily,
    assign_family,
)


def test_every_family_has_a_spec():
    assert set(FAMILY_SPECS) == set(QueryFamily)
    for family, spec in FAMILY_SPECS.items():
        assert spec.family is family
        assert spec.description


def test_the_mandatory_and_derived_sets_are_disjoint_and_cover_every_family():
    assert set(MANDATORY_FAMILIES).isdisjoint(DERIVED_FAMILIES)
    assert set(MANDATORY_FAMILIES) | set(DERIVED_FAMILIES) == set(QueryFamily)
    assert len(MANDATORY_FAMILIES) == 6
    assert len(DERIVED_FAMILIES) == 2


def test_only_the_derived_families_need_a_normalized_problem():
    for family, spec in FAMILY_SPECS.items():
        assert spec.mandatory is (family in MANDATORY_FAMILIES)
        assert spec.requires_normalized_problem is (family in DERIVED_FAMILIES)


def test_every_template_carries_the_subject_slot():
    for spec in FAMILY_SPECS.values():
        assert len(spec.templates) == 4
        for template in spec.templates:
            assert template.count("{subject}") == 1


def test_every_family_has_a_marker():
    marked = {family for family, _markers in FAMILY_MARKERS}
    assert marked == set(QueryFamily)


def test_every_marker_is_case_folded():
    # The table is matched against case-folded text, so a marker carrying a
    # capital letter would never match.
    for _family, markers in FAMILY_MARKERS:
        for marker in markers:
            assert marker == marker.casefold()


def test_a_marker_text_maps_to_its_family():
    cases = {
        "we work around it with a spreadsheet": QueryFamily.workaround,
        "is this a real problem at all": QueryFamily.counter_evidence,
        "competitors for invoice reconciliation": QueryFamily.competitor,
        "the tool currently used for this": QueryFamily.existing_solution,
        "how bad is it really": QueryFamily.severity,
        "the manual step in the workflow": QueryFamily.workflow_pain,
        "another way to phrase this": QueryFamily.alternative_wording,
        "a plain problem statement": QueryFamily.problem,
    }
    for text, family in cases.items():
        assigned, reason = assign_family(text)
        assert assigned is family
        assert reason.startswith("marker:")


def test_an_unknown_text_falls_back_to_the_default_family():
    assigned, reason = assign_family("zzzz qqqq")
    assert assigned is DEFAULT_FAMILY
    assert reason == "default"


def test_the_same_text_always_maps_to_the_same_family():
    text = "the spreadsheet we use for this"
    assert assign_family(text) == assign_family(text)
    assert assign_family(text.upper()) == assign_family(text)

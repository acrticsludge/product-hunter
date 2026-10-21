from __future__ import annotations

import pytest
from pydantic import ValidationError

from product_hunter.normalization import (
    DEFAULT_NORMALIZATION_VERSION,
    Domain,
    NormalizationError,
    NormalizedProblem,
    normalize_fields,
    slugify,
)


def build(**overrides) -> NormalizedProblem:
    """One normalized problem, with the parts a test wants to change."""
    fields = {
        "domain": Domain.operations,
        "actor": "the ops team",
        "action": "paste invoice totals by hand",
        "object_": "invoice totals",
        "consequence": "a Saturday of manual work every month",
        "normalization_version": DEFAULT_NORMALIZATION_VERSION,
    }
    fields.update(overrides)
    return normalize_fields(**fields)


# --- slugify ---------------------------------------------------------------


def test_slugify_lowercases_and_trims():
    assert slugify("  Paste Totals  ") == "paste-totals"


def test_slugify_replaces_punctuation_and_underscores():
    assert slugify("paste, totals; by hand!") == "paste-totals-by-hand"
    assert slugify("paste_totals") == "paste-totals"


def test_slugify_drops_accents():
    assert slugify("Reconciliation des Paiements") == "reconciliation-des-paiements"


def test_slugify_collapses_whitespace_and_dashes():
    assert slugify("paste   totals\tby\nhand") == "paste-totals-by-hand"
    assert slugify("--paste--totals--") == "paste-totals"


def test_slugify_truncates_to_the_limit():
    slug = slugify("a" * 100, max_length=10)

    assert slug == "a" * 10
    assert len(slug) == 10


def test_slugify_strips_the_dash_a_truncation_leaves_behind():
    assert slugify("aaaa-b", max_length=5) == "aaaa"


@pytest.mark.parametrize("value", ["", "   ", "---", "!!!"])
def test_slugify_rejects_a_value_with_nothing_left(value):
    with pytest.raises(NormalizationError) as excinfo:
        slugify(value)

    assert excinfo.value.reason_code == "normalization_invalid"
    assert excinfo.value.detail


# --- the normalized key ----------------------------------------------------


def test_the_key_is_the_same_for_case_and_punctuation_variants():
    first = build(action="Paste invoice totals by hand", object_="Invoice totals")
    second = build(action="paste  invoice, totals by hand", object_="invoice totals")

    assert first.normalized_key == second.normalized_key
    assert first.normalized_key == (
        "operations:paste-invoice-totals-by-hand:invoice-totals"
    )


def test_a_different_domain_or_action_gives_a_different_key():
    base = build()

    assert build(domain=Domain.finance).normalized_key != base.normalized_key
    assert build(action="export invoice totals by hand").normalized_key != (
        base.normalized_key
    )
    assert build(object_="credit notes").normalized_key != base.normalized_key


def test_the_key_stays_within_its_length_bound():
    problem = build(action="a" * 120, object_="b" * 120)

    assert len(problem.normalized_key) <= 200


# --- normalize_fields ------------------------------------------------------


def test_normalize_fields_trims_the_parts():
    problem = build(actor="  the ops team  ", action=" paste invoice totals by hand ")

    assert problem.actor == "the ops team"
    assert problem.action == "paste invoice totals by hand"


@pytest.mark.parametrize("field", ["actor", "action", "object_", "consequence"])
def test_normalize_fields_rejects_an_empty_part(field):
    with pytest.raises(NormalizationError) as excinfo:
        build(**{field: "   "})

    assert excinfo.value.reason_code == "normalization_invalid"
    assert field.rstrip("_") in excinfo.value.detail


@pytest.mark.parametrize("field", ["action", "object_"])
def test_normalize_fields_rejects_a_part_with_no_slug(field):
    with pytest.raises(NormalizationError) as excinfo:
        build(**{field: "---"})

    assert excinfo.value.reason_code == "normalization_invalid"
    assert field.rstrip("_") in excinfo.value.detail


@pytest.mark.parametrize(
    "field,limit",
    [("actor", 120), ("action", 120), ("object_", 120), ("consequence", 200)],
)
def test_normalize_fields_rejects_a_part_over_the_limit(field, limit):
    with pytest.raises(NormalizationError) as excinfo:
        build(**{field: "x" * (limit + 1)})

    assert excinfo.value.reason_code == "normalization_invalid"
    assert field.rstrip("_") in excinfo.value.detail


def test_normalize_fields_records_the_version():
    problem = build(normalization_version="norm-v2")

    assert problem.normalization_version == "norm-v2"


def test_normalize_fields_rejects_an_empty_version():
    with pytest.raises(NormalizationError) as excinfo:
        build(normalization_version="  ")

    assert excinfo.value.reason_code == "normalization_invalid"


def test_the_default_version_is_norm_v1():
    assert DEFAULT_NORMALIZATION_VERSION == "norm-v1"


def test_the_normalized_problem_forbids_extra_fields():
    with pytest.raises(ValidationError):
        NormalizedProblem.model_validate(
            {
                "domain": "operations",
                "actor": "the ops team",
                "action": "paste invoice totals by hand",
                "object": "invoice totals",
                "consequence": "a Saturday of manual work every month",
                "normalized_key": "operations:paste:invoice-totals",
                "normalization_version": "norm-v1",
                "extra": "not allowed",
            }
        )

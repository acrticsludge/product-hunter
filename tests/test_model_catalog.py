from __future__ import annotations

import json

import pytest

from product_hunter.llm.catalog import (
    Availability,
    MalformedCatalogError,
    ModelIdentity,
    compute_catalog_version,
    derive_availability,
    parse_model_catalog,
    parse_model_list,
)

ROUTE_ENTRY = {
    "id": "minimax-m3.1-flash-free",
    "modelID": "minimax-m3.1-flash-free",
    "providerID": "opencode-go",
    "family": "minimax",
    "name": "MiniMax M3.1 Flash Free",
    "status": "active",
    "enabled": True,
    "limit": {"context": 1000000, "output": 524288},
    "cost": [{"input": 0, "output": 0, "cache": {"read": 0, "write": 0}}],
    "time": {"released": 1792368000000},
    "capabilities": {"reasoning": True},
}


def route(*entries: dict) -> str:
    return json.dumps({"location": {"directory": "."}, "data": list(entries)})


def test_the_route_payload_becomes_a_catalog() -> None:
    catalog = parse_model_catalog(route(ROUTE_ENTRY), source="test")

    assert catalog.source == "test"
    assert len(catalog.entries) == 1
    entry = catalog.entries[0]
    assert entry.identity.key == "opencode-go/minimax-m3.1-flash-free"
    assert entry.name == "MiniMax M3.1 Flash Free"
    assert entry.family == "minimax"
    assert entry.status == "active"
    assert entry.enabled is True
    assert entry.availability is Availability.available
    assert entry.context_limit == 1000000
    assert entry.output_limit == 524288
    assert entry.cost_input == 0.0
    assert entry.cost_output == 0.0
    assert entry.released_at is not None
    assert entry.released_at.year == 2026
    assert entry.source == "test"


def test_an_extreme_release_timestamp_leaves_the_entry_intact() -> None:
    entry = {**ROUTE_ENTRY, "time": {"released": 1e30}}

    catalog = parse_model_catalog(route(entry), source="test")

    assert catalog.entries[0].released_at is None
    assert catalog.entries[0].availability is Availability.available
    assert catalog.entries[0].identity.key == "opencode-go/minimax-m3.1-flash-free"


def test_a_bare_list_is_accepted() -> None:
    catalog = parse_model_catalog(json.dumps([ROUTE_ENTRY]), source="test")

    assert len(catalog.entries) == 1


def test_a_malformed_catalog_is_refused() -> None:
    with pytest.raises(MalformedCatalogError):
        parse_model_catalog("not json", source="test")
    with pytest.raises(MalformedCatalogError):
        parse_model_catalog(json.dumps({"data": {"a": 1}}), source="test")
    with pytest.raises(MalformedCatalogError):
        parse_model_catalog(json.dumps("a string"), source="test")
    with pytest.raises(MalformedCatalogError):
        parse_model_catalog(json.dumps({"data": ["not an object"]}), source="test")


def test_a_deeply_nested_payload_is_refused() -> None:
    payload = "[" * 20000 + "]" * 20000

    with pytest.raises(MalformedCatalogError) as excinfo:
        parse_model_catalog(payload, source="test")

    assert "nested too deeply" in str(excinfo.value)


def test_an_entry_without_a_provider_id_is_refused() -> None:
    entry = {key: value for key, value in ROUTE_ENTRY.items() if key != "providerID"}

    with pytest.raises(MalformedCatalogError):
        parse_model_catalog(route(entry), source="test")


def test_an_entry_without_a_model_id_is_refused() -> None:
    entry = {
        key: value
        for key, value in ROUTE_ENTRY.items()
        if key not in ("providerID", "modelID", "id")
    }

    with pytest.raises(MalformedCatalogError):
        parse_model_catalog(route(entry), source="test")


def test_an_empty_catalog_is_valid() -> None:
    catalog = parse_model_catalog(route(), source="test")

    assert catalog.entries == []
    assert catalog.available() == []
    assert catalog.catalog_version.startswith("sha256:")


def test_the_model_list_parses_to_unknown_entries() -> None:
    catalog = parse_model_list(
        "opencode-go/minimax-m3.1-flash-free\nnvidia/deepseek-ai/deepseek-v4.1-flash\n",
        source="test",
    )

    assert [entry.identity.key for entry in catalog.entries] == [
        "opencode-go/minimax-m3.1-flash-free",
        "nvidia/deepseek-ai/deepseek-v4.1-flash",
    ]
    assert all(entry.availability is Availability.unknown for entry in catalog.entries)
    assert catalog.available() == []


def test_an_empty_model_list_is_valid() -> None:
    catalog = parse_model_list("", source="test")

    assert catalog.entries == []


def test_a_model_list_line_that_is_not_a_pair_is_refused() -> None:
    with pytest.raises(MalformedCatalogError):
        parse_model_list("opencode-go\n", source="test")
    with pytest.raises(MalformedCatalogError):
        parse_model_list("opencode-go/\n", source="test")


def test_identity_separates_providers() -> None:
    other = {**ROUTE_ENTRY, "providerID": "tokenharbor"}
    catalog = parse_model_catalog(route(ROUTE_ENTRY, other), source="test")

    keys = [entry.identity.key for entry in catalog.entries]
    assert keys == [
        "opencode-go/minimax-m3.1-flash-free",
        "tokenharbor/minimax-m3.1-flash-free",
    ]
    assert catalog.entries[0].identity.model_id == catalog.entries[1].identity.model_id
    assert catalog.by_key("tokenharbor/minimax-m3.1-flash-free") is not None
    assert catalog.by_key("minimax-m3.1-flash-free") is None


def test_availability_is_derived_from_what_the_source_reported() -> None:
    assert derive_availability("active", True) is Availability.available
    assert derive_availability("active", False) is Availability.disabled
    assert derive_availability("error", True) is Availability.retired
    assert derive_availability(None, None) is Availability.unknown
    assert derive_availability("active", None) is Availability.unknown


def test_a_disabled_model_is_not_available() -> None:
    entry = {**ROUTE_ENTRY, "enabled": False}
    catalog = parse_model_catalog(route(entry), source="test")

    assert catalog.entries[0].availability is Availability.disabled
    assert catalog.available() == []


def test_a_retired_model_is_not_available() -> None:
    entry = {**ROUTE_ENTRY, "status": "deprecated"}
    catalog = parse_model_catalog(route(entry), source="test")

    assert catalog.entries[0].availability is Availability.retired
    assert catalog.available() == []


def test_the_catalog_version_is_stable_for_an_unchanged_catalog() -> None:
    first = parse_model_catalog(route(ROUTE_ENTRY), source="test")
    second = parse_model_catalog(route(ROUTE_ENTRY), source="other")

    assert first.catalog_version == second.catalog_version
    assert first.catalog_version == compute_catalog_version(first.entries)


def test_the_catalog_version_moves_when_a_model_moves() -> None:
    first = parse_model_catalog(route(ROUTE_ENTRY), source="test")
    second = parse_model_catalog(
        route(ROUTE_ENTRY, {**ROUTE_ENTRY, "modelID": "other", "id": "other"}),
        source="test",
    )

    assert first.catalog_version != second.catalog_version


def test_the_diff_names_what_changed() -> None:
    before = parse_model_catalog(
        route(ROUTE_ENTRY, {**ROUTE_ENTRY, "modelID": "gone", "id": "gone"}),
        source="test",
    )
    after = parse_model_catalog(
        route(
            {**ROUTE_ENTRY, "status": "deprecated"},
            {**ROUTE_ENTRY, "modelID": "new", "id": "new"},
            {**ROUTE_ENTRY, "modelID": "renamed", "id": "renamed", "name": "Other"},
        ),
        source="test",
    )

    diff = before.diff(after)

    assert [identity.key for identity in diff.added] == [
        "opencode-go/new",
        "opencode-go/renamed",
    ]
    assert [identity.key for identity in diff.removed] == ["opencode-go/gone"]
    assert [identity.key for identity in diff.changed] == [
        "opencode-go/minimax-m3.1-flash-free"
    ]


def test_an_identity_key_is_the_provider_and_the_model() -> None:
    identity = ModelIdentity(provider_id="opencode-go", model_id="alpha")

    assert identity.key == "opencode-go/alpha"
    assert identity == ModelIdentity(provider_id="opencode-go", model_id="alpha")

"""The community registry: the places the system reads inside a source."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from product_hunter.communities import (
    CommunityConfig,
    CommunityError,
    CommunityRegistry,
    load_communities,
    normalize_name,
)
from product_hunter.sources import SourceConfig, SourceRegistry, load_registry


def _community(community_id: str, **overrides) -> CommunityConfig:
    data = {
        "community_id": community_id,
        "source_id": "hn_algolia",
        "name": "Ask HN",
        "host": "news.ycombinator.com",
        "url": "https://news.ycombinator.com/ask",
        "platform": "hacker_news",
        "topics": ["advice"],
        "independence_group": "hacker_news",
        "duplicate_of": None,
        "status": "active",
        "activity": "high",
    }
    data.update(overrides)
    return CommunityConfig(**data)


def _registry(*communities: CommunityConfig) -> CommunityRegistry:
    return CommunityRegistry(list(communities))


def _source_registry(*source_ids: str) -> SourceRegistry:
    sources = {
        source_id: SourceConfig(
            source_id=source_id,
            terms_status="permitted",
            allowed_use=["discovery", "retrieval", "storage"],
            reviewed_at="2026-10-20",
            collector="api",
            rate_limit={
                "min_interval_seconds": 1.0,
                "max_requests_per_run": 10,
                "max_retries": 1,
                "backoff_base_seconds": 1.0,
                "backoff_max_seconds": 30.0,
            },
            domains=["example.com"],
            robots="skip",
        )
        for source_id in source_ids
    }
    return SourceRegistry(sources)


def test_duplicate_communities_resolve_to_one_canonical():
    registry = _registry(
        _community("ask_hn"),
        _community("ask-hn", name="ask hn"),
        _community("ask_hn_alias", name="Ask HN!", duplicate_of="ask_hn"),
    )
    assert registry.canonical("ask_hn") == "ask_hn"
    assert registry.canonical("ask-hn") == "ask_hn"
    assert registry.canonical("ask_hn_alias") == "ask_hn"
    groups = registry.duplicates()
    assert ("ask_hn", "ask-hn", "ask_hn_alias") in groups


def test_a_community_that_is_not_a_duplicate_resolves_to_itself():
    registry = _registry(_community("ask_hn"), _community("other", name="Other"))
    assert registry.canonical("ask_hn") == "ask_hn"
    assert registry.duplicates() == []


def test_a_community_of_an_unregistered_source_is_refused():
    registry = _registry(_community("ask_hn", source_id="reddit"))
    with pytest.raises(CommunityError) as excinfo:
        registry.validate_against(_source_registry("hn_algolia"))
    assert excinfo.value.reason_code == "unknown_source"


def test_a_duplicate_that_names_no_community_is_refused():
    registry = _registry(_community("ask_hn", duplicate_of="nowhere"))
    with pytest.raises(CommunityError) as excinfo:
        registry.validate_against(_source_registry("hn_algolia"))
    assert excinfo.value.reason_code == "unknown_duplicate"


def test_the_community_registry_names_its_topics_and_groups():
    registry = load_communities()
    communities = registry.list()
    assert communities
    for community in communities:
        assert community.topics
        assert community.independence_group
        assert community.activity
    groups = registry.independence_groups()
    assert set(groups) == {community.community_id for community in communities}
    assert len(set(groups.values())) == len(groups)


def test_the_shipped_community_registry_holds_the_communities_of_the_shipped_sources():
    registry = load_communities()
    registry.validate_against(load_registry())
    assert {community.community_id for community in registry.for_source("hn_algolia")} == {
        "ask_hn"
    }
    assert {
        community.community_id for community in registry.for_source("github_issues")
    } == {"github_issues"}


def test_a_community_file_naming_an_unknown_source_is_refused(tmp_path: Path):
    path = tmp_path / "communities.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "version": 1,
                "communities": [
                    {"community_id": "ask_hn", "source_id": "reddit"},
                ],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(CommunityError) as excinfo:
        load_communities(path, registry=_source_registry("hn_algolia"))
    assert excinfo.value.reason_code == "unknown_source"


def test_the_same_community_file_is_accepted_when_the_registry_holds_the_source(
    tmp_path: Path,
):
    path = tmp_path / "communities.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "version": 1,
                "communities": [
                    {"community_id": "ask_hn", "source_id": "reddit"},
                ],
            }
        ),
        encoding="utf-8",
    )
    registry = load_communities(path, registry=_source_registry("reddit"))
    assert [community.community_id for community in registry.list()] == ["ask_hn"]


def test_a_community_file_with_a_duplicate_id_is_refused(tmp_path: Path):
    path = tmp_path / "communities.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "version": 1,
                "communities": [
                    {"community_id": "ask_hn", "source_id": "hn_algolia"},
                    {"community_id": "ask_hn", "source_id": "hn_algolia"},
                ],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValidationError):
        CommunityRegistry.from_yaml(path)


def test_a_community_cannot_be_a_duplicate_of_itself():
    with pytest.raises(ValidationError):
        _community("ask_hn", duplicate_of="ask_hn")


def test_the_normalized_name_drops_punctuation_and_case():
    assert normalize_name("Ask, HN!") == "ask hn"
    assert normalize_name("  ask   hn ") == "ask hn"

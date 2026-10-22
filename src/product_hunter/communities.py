"""The community registry: the places the system reads inside a source.

This module owns the shape of a community entry, the rule that decides when
two entries are the same place, and the check that keeps the community file
from introducing a source.

A community whose source is not in the source registry is refused, so this
file can never widen what may be collected.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from product_hunter.config import repo_root
from product_hunter.sources import SourceRegistry, load_registry

COMMUNITIES_NAME = Path("config") / "communities.yaml"

# Everything that is not a word character and not whitespace is dropped from
# a community name before two names are compared.
_NOT_WORD = re.compile(r"[^\w\s]")


class CommunityError(Exception):
    """The community registry is invalid and cannot be used."""

    def __init__(self, reason_code: str, detail: str = "") -> None:
        self.reason_code = reason_code
        super().__init__(detail or reason_code)


class CommunityConfig(BaseModel):
    """One community of one source."""

    model_config = ConfigDict(extra="forbid")

    community_id: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    name: str = ""
    host: str = ""
    url: str = ""
    platform: str = ""
    topics: list[str] = Field(default_factory=list)
    independence_group: str = ""
    duplicate_of: str | None = None
    status: str = "active"
    activity: str = ""

    @model_validator(mode="after")
    def _not_its_own_duplicate(self) -> CommunityConfig:
        if self.duplicate_of == self.community_id:
            raise ValueError("a community cannot be a duplicate of itself")
        return self


class CommunitiesFile(BaseModel):
    """The community file: a version and the communities it holds."""

    model_config = ConfigDict(extra="forbid")

    version: int = Field(gt=0)
    communities: list[CommunityConfig] = Field(default_factory=list)

    @model_validator(mode="after")
    def _unique_ids(self) -> CommunitiesFile:
        seen: set[str] = set()
        for community in self.communities:
            if community.community_id in seen:
                raise ValueError(f"duplicate community_id: {community.community_id!r}")
            seen.add(community.community_id)
        return self


def normalize_name(name: str) -> str:
    """The comparable form of a community name.

    The name is case-folded, everything that is not a word character or
    whitespace is dropped, and the whitespace runs collapse into one space.
    """
    return " ".join(_NOT_WORD.sub("", name.casefold()).split())


class CommunityRegistry:
    """The communities the system knows about."""

    def __init__(self, communities: list[CommunityConfig]) -> None:
        self._communities = list(communities)

    @classmethod
    def from_yaml(cls, path: str | Path) -> CommunityRegistry:
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        parsed = CommunitiesFile.model_validate(raw)
        return cls(parsed.communities)

    def get(self, community_id: str) -> CommunityConfig:
        for community in self._communities:
            if community.community_id == community_id:
                return community
        raise CommunityError(
            "unknown_community", f"unknown community: {community_id!r}"
        )

    def list(self) -> list[CommunityConfig]:
        return list(self._communities)

    def for_source(self, source_id: str) -> list[CommunityConfig]:
        """The communities of one source."""
        return [
            community
            for community in self._communities
            if community.source_id == source_id
        ]

    def duplicates(self) -> list[tuple[str, ...]]:
        """The groups of communities that are the same place.

        Each group holds the canonical community first, and the communities
        that resolve to it after it. A community that is not a duplicate is
        not a group.
        """
        groups: dict[str, list[str]] = {}
        for community in self._communities:
            canonical = self.canonical(community.community_id)
            groups.setdefault(canonical, []).append(community.community_id)
        return [tuple(members) for members in groups.values() if len(members) > 1]

    def canonical(self, community_id: str) -> str:
        """The community a duplicate resolves to.

        A community that is not a duplicate resolves to itself.
        """
        self.get(community_id)
        parents = self._parents()
        seen = {community_id}
        current = community_id
        while True:
            parent = parents.get(current)
            if parent is None or parent in seen:
                return current
            seen.add(parent)
            current = parent

    def independence_groups(self) -> dict[str, str]:
        """The independence group of every community.

        A community without a group is a group of its own, so two communities
        without one are not held to be the same origin.
        """
        return {
            community.community_id: community.independence_group
            or community.community_id
            for community in self._communities
        }

    def validate_against(self, registry: SourceRegistry) -> None:
        """Refuse a community the source registry does not know.

        The check is what keeps this file from introducing a source: a
        community whose source is not in the registry names something the
        operator never reviewed.
        """
        known_sources = {source.source_id for source in registry.list()}
        known_communities = {c.community_id for c in self._communities}
        for community in self._communities:
            if community.source_id not in known_sources:
                raise CommunityError(
                    "unknown_source",
                    f"community {community.community_id!r} names source "
                    f"{community.source_id!r}, which is not in the source registry",
                )
            if community.duplicate_of and community.duplicate_of not in known_communities:
                raise CommunityError(
                    "unknown_duplicate",
                    f"community {community.community_id!r} names the duplicate "
                    f"{community.duplicate_of!r}, which is not a community",
                )

    def _parents(self) -> dict[str, str]:
        """The community each duplicate resolves to, one step.

        An explicit ``duplicate_of`` wins, and two communities with the same
        normalized name and the same host are the same place. The canonical
        one of such a pair is the one that is not itself a duplicate of
        another community.
        """
        parents: dict[str, str] = {}
        for community in self._communities:
            if community.duplicate_of:
                parents[community.community_id] = community.duplicate_of
        groups: dict[tuple[str, str], list[CommunityConfig]] = {}
        for community in self._communities:
            key = (normalize_name(community.name), community.host.lower())
            groups.setdefault(key, []).append(community)
        for members in groups.values():
            if len(members) < 2:
                continue
            canonical = next(
                (member for member in members if not member.duplicate_of), members[0]
            )
            for member in members:
                if (
                    member.community_id != canonical.community_id
                    and member.community_id not in parents
                ):
                    parents[member.community_id] = canonical.community_id
        return parents


def load_communities(
    path: str | Path | None = None, *, registry: SourceRegistry | None = None
) -> CommunityRegistry:
    """Load the community registry and refuse a community of an unknown source.

    The check is what keeps this file from introducing a source: a community
    whose source is not in the registry names something nobody reviewed.
    """
    resolved = Path(path) if path is not None else repo_root() / COMMUNITIES_NAME
    parsed = CommunityRegistry.from_yaml(resolved)
    parsed.validate_against(registry if registry is not None else load_registry())
    return parsed

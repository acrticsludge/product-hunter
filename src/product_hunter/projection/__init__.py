"""The read-only projection of the authoritative state into an Obsidian vault.

SQLite stays the source of truth. The vault is a projection of it: it can be
deleted and rebuilt at any time, and nothing in it is ever read back into
the database.
"""

from __future__ import annotations

from product_hunter.projection.contract import CANONICAL_DDL, CONTRACT_VERSION
from product_hunter.projection.engine import ProjectResult, ProjectionError, Projector
from product_hunter.projection.models import (
    CommunityRef,
    EvidenceRef,
    EvidenceSummary,
    Experiment,
    MarketReport,
    Opportunity,
    ResearchRequest,
    Snapshot,
    SourceRef,
)
from product_hunter.projection.sanitize import (
    content_hash,
    neutralize,
    safe_url,
    slug,
    yaml_scalar,
)
from product_hunter.projection.source import SqliteProjectionSource
from product_hunter.projection.vault import VaultLayout

__all__ = [
    "CANONICAL_DDL",
    "CONTRACT_VERSION",
    "CommunityRef",
    "EvidenceRef",
    "EvidenceSummary",
    "Experiment",
    "MarketReport",
    "Opportunity",
    "ProjectResult",
    "ProjectionError",
    "Projector",
    "ResearchRequest",
    "Snapshot",
    "SourceRef",
    "SqliteProjectionSource",
    "VaultLayout",
    "content_hash",
    "neutralize",
    "safe_url",
    "slug",
    "yaml_scalar",
]

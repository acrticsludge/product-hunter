"""Which model runs which pipeline job.

A role owns an ordered chain of candidates: an explicit ``provider/model`` or a
glob matched against the live catalogue. The router walks the chain and takes
the first candidate the catalogue reports as available. A model that has
disappeared is skipped, never fatal, and the selection records where it came
from, so a run can be explained afterwards. This module knows nothing about
SQLite.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from enum import Enum
from fnmatch import fnmatchcase
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field, model_validator

from product_hunter.llm.base import ProviderError
from product_hunter.llm.catalog import (
    Availability,
    CatalogError,
    ModelCatalog,
    ModelEntry,
    ModelIdentity,
    utcnow,
)
from product_hunter.llm.discovery import CatalogSource

if TYPE_CHECKING:
    from product_hunter.config import RoutingConfig


class RoutingError(ProviderError):
    """A routing operation was refused."""


class UnknownRole(RoutingError):
    """The role is not one of the pipeline roles."""


class NoModelAvailable(RoutingError):
    """No candidate of the role's chain is available."""


class ModelRole(str, Enum):
    """The pipeline jobs a model can be bound to."""

    extraction = "extraction"
    classification = "classification"
    normalization = "normalization"
    deduplication = "deduplication"
    entity_resolution = "entity_resolution"
    clustering = "clustering"
    validation = "validation"
    investigation = "investigation"
    competitor_research = "competitor_research"
    counter_evidence = "counter_evidence"
    scoring = "scoring"
    reporting = "reporting"
    synthesis = "synthesis"


class SelectionSource(str, Enum):
    """Where a selected model came from."""

    config = "config"
    pattern = "pattern"
    default = "default"


class RouteCandidate(BaseModel):
    """One candidate of a chain: an explicit model or a glob over the catalogue."""

    kind: Literal["model", "pattern"]
    value: str = Field(min_length=1)

    @model_validator(mode="before")
    @classmethod
    def _from_mapping(cls, data: object) -> object:
        """Accept the file shape ``{"pattern": "opencode-go/*"}``."""
        if isinstance(data, dict) and "kind" not in data:
            if len(data) != 1:
                raise ValueError(
                    "a candidate needs exactly one of 'model' or 'pattern'"
                )
            (kind, value), = data.items()
            if kind not in ("model", "pattern"):
                raise ValueError(f"unknown candidate kind {kind!r}")
            if not isinstance(value, str) or not value.strip():
                raise ValueError("a candidate needs a non-empty value")
            if kind == "model" and "/" not in value.strip("/"):
                raise ValueError(
                    f"a model candidate must be 'provider/model', got {value!r}"
                )
            return {"kind": kind, "value": value.strip()}
        return data


class ModelSelection(BaseModel):
    """The model a role resolved to, and where the choice came from."""

    role: ModelRole
    identity: ModelIdentity
    selection_source: SelectionSource
    fallback_index: int
    is_fallback: bool
    catalog_version: str
    resolved_at: datetime


class ModelExecution(BaseModel):
    """One model call, with the provenance that explains it."""

    execution_id: str
    run_id: str | None = None
    stage: str | None = None
    role: ModelRole
    provider_id: str
    model_id: str
    selection_source: SelectionSource
    fallback_index: int = 0
    is_fallback: bool = False
    catalog_version: str
    prompt_version: str | None = None
    config_version: str
    latency_ms: int = 0
    status: str
    error: str | None = None
    created_at: str


class ModelRouter:
    """Resolves a role to a model against the live catalogue."""

    def __init__(
        self,
        config: RoutingConfig,
        *,
        default: ModelIdentity,
        catalog_source: CatalogSource,
    ) -> None:
        self.config = config
        self.default = default
        self.catalog_source = catalog_source
        self.last_error: str | None = None

    def chain(self, role: ModelRole | str) -> list[ModelSelection]:
        """The ordered, available candidates of a role.

        A model that is gone from the catalogue is skipped, and a role that
        matches nothing triggers one re-discovery before it gives up. A failed
        discovery is not a miss: it is not re-discovered here.
        """
        resolved = _role(role)
        catalog = self._catalog()
        if catalog is None:
            return []
        selections = self._expand(catalog, resolved)
        if not selections:
            refreshed = self._refresh()
            if refreshed is not None:
                selections = self._expand(refreshed, resolved)
        return selections

    def select(
        self,
        role: ModelRole | str,
        *,
        exclude: Iterable[ModelIdentity] = (),
    ) -> ModelSelection:
        """The first available candidate of a role, skipping excluded models.

        An excluded model is one that failed at call time, so the next
        candidate is returned with its own position in the chain, which makes
        the attempt a fallback.
        """
        resolved = _role(role)
        excluded = {identity.key for identity in exclude}
        for selection in self.chain(resolved):
            if selection.identity.key not in excluded:
                return selection
        raise NoModelAvailable(self._no_model_message(resolved))

    def _expand(self, catalog: ModelCatalog, role: ModelRole) -> list[ModelSelection]:
        """The available candidates of a role, in chain order."""
        selections: list[ModelSelection] = []
        seen: set[str] = set()
        for candidate in self.config.roles.get(role, []):
            if candidate.kind == "model":
                identity = _identity(candidate.value)
                entry = catalog.by_key(identity.key)
                if entry is None or entry.availability is not Availability.available:
                    continue
                source = SelectionSource.config
                matches = [entry]
            else:
                source = SelectionSource.pattern
                matches = _match(catalog, candidate.value)
            for entry in matches:
                if entry.identity.key in seen:
                    continue
                seen.add(entry.identity.key)
                selections.append(
                    _selection(role, entry.identity, source, len(selections), catalog)
                )
        default = catalog.by_key(self.default.key)
        if (
            default is not None
            and default.availability is Availability.available
            and self.default.key not in seen
        ):
            selections.append(
                _selection(
                    role, self.default, SelectionSource.default, len(selections), catalog
                )
            )
        return selections

    def _catalog(self) -> ModelCatalog | None:
        """The live catalogue, or None when the discovery failed."""
        try:
            catalog = self.catalog_source.catalog()
        except (CatalogError, ProviderError) as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            return None
        self.last_error = None
        return catalog

    def _refresh(self) -> ModelCatalog | None:
        """One forced re-discovery, or None when it fails."""
        try:
            catalog = self.catalog_source.catalog(force=True)
        except (CatalogError, ProviderError) as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            return None
        self.last_error = None
        return catalog

    def _no_model_message(self, role: ModelRole) -> str:
        """Why there was no model, with the discovery failure when there was one.

        An operator must be able to tell "no model exists" apart from
        "discovery is broken", so the last discovery failure is named.
        """
        message = f"no available model for role {role.value!r}"
        failure = self.last_error or self._source_error()
        if failure:
            message = f"{message}; discovery failed: {failure}"
        return message

    def _source_error(self) -> str | None:
        """The catalogue source's last discovery failure, when it keeps one."""
        error = getattr(self.catalog_source, "last_error", None)
        return error if isinstance(error, str) else None


def _role(role: ModelRole | str) -> ModelRole:
    """The role, refused by name when it is not one of them."""
    if isinstance(role, ModelRole):
        return role
    try:
        return ModelRole(role)
    except ValueError as exc:
        raise UnknownRole(f"unknown role {role!r}") from exc


def _identity(value: str) -> ModelIdentity:
    """The identity of an explicit ``provider/model`` candidate."""
    provider_id, separator, model_id = value.partition("/")
    if not separator or not provider_id or not model_id:
        raise RoutingError(f"a model candidate must be 'provider/model', got {value!r}")
    return ModelIdentity(provider_id=provider_id, model_id=model_id)


def _match(catalog: ModelCatalog, pattern: str) -> list[ModelEntry]:
    """The available entries matching a glob, cheapest first, then by model id."""
    matched = [
        entry
        for entry in catalog.entries
        if entry.availability is Availability.available
        and fnmatchcase(entry.identity.key, pattern)
    ]
    return sorted(
        matched,
        key=lambda entry: (
            _cost_rank(entry),
            entry.identity.model_id,
            entry.identity.provider_id,
        ),
    )


def _cost_rank(entry: ModelEntry) -> tuple[int, float]:
    """Cost ascending, with an unknown cost last."""
    cost = entry.cost_input if entry.cost_input is not None else entry.cost_output
    if cost is None:
        return (1, 0.0)
    return (0, cost)


def _selection(
    role: ModelRole,
    identity: ModelIdentity,
    source: SelectionSource,
    index: int,
    catalog: ModelCatalog,
) -> ModelSelection:
    """One selection of a chain, with its position and the catalogue it used."""
    return ModelSelection(
        role=role,
        identity=identity,
        selection_source=source,
        fallback_index=index,
        is_fallback=index > 0,
        catalog_version=catalog.catalog_version,
        resolved_at=utcnow(),
    )

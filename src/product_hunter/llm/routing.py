"""Which model runs which pipeline job.

A role owns an ordered chain of candidates: an explicit ``provider/model`` or a
glob matched against the live catalogue. The router walks the chain and takes
the first candidate the catalogue reports as available. A model that has
disappeared is skipped, never fatal, and the selection records where it came
from, so a run can be explained afterwards. This module knows nothing about
SQLite.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable
from datetime import datetime, timedelta
from enum import Enum
from fnmatch import fnmatchcase
from typing import TYPE_CHECKING, Literal, TypeVar

from pydantic import BaseModel, Field, model_validator

from product_hunter.llm.base import (
    GenerationRequest,
    GenerationResponse,
    ModelCallFailed,
    ProviderError,
    TextGenerator,
)
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

_T = TypeVar("_T")


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


class GenerationAttempt(BaseModel):
    """One provider call, with what came of it."""

    selection: ModelSelection
    latency_ms: int = 0
    ok: bool
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


# The primary candidate plus two fallbacks. The free tier catalogue holds 29
# models and a provider that is down costs the configured timeout per
# attempt, 120 s today, so one chunk can cost 3 x 120 s and an unbounded
# chain would hang it for the whole catalogue.
MAX_FALLBACK_ATTEMPTS = 3


class FallbackGenerator:
    """A text generator that falls through a role's chain when a call fails.

    The router owns the chain; the generator asks it for the next candidate
    with the failed ones excluded, and retries the same request. Every
    provider call is recorded, so a run can say what it tried.
    """

    name = "fallback"

    def __init__(
        self,
        router: ModelRouter,
        role: ModelRole | str,
        build: Callable[[ModelSelection], TextGenerator],
    ) -> None:
        self.router = router
        self.role = _role(role)
        self.build = build
        self.attempts: list[GenerationAttempt] = []
        self._failed: list[ModelIdentity] = []
        self._last_at: datetime | None = None
        self._last_failure: ProviderError | None = None

    def generate(self, request: GenerationRequest) -> GenerationResponse:
        """The answer of the first candidate of the chain that answers."""

        def call(
            selection: ModelSelection, generator: TextGenerator
        ) -> GenerationResponse:
            started = time.monotonic()
            try:
                response = generator.generate(request)
            except ProviderError as exc:
                self._failed_attempt(selection, started, exc)
                raise
            self.attempts.append(
                GenerationAttempt(
                    selection=selection,
                    latency_ms=response.latency_ms,
                    ok=True,
                    created_at=self._stamp(),
                )
            )
            return response

        return self._walk(call)

    def check(self) -> dict[str, object]:
        """The check of the first candidate of the chain the provider accepts.

        A model that left the catalogue between the discovery and the check
        is refused here, and the next candidate is tried instead. A refused
        candidate is recorded as a failed attempt, so the run says which
        candidate was skipped and why. A candidate that passes the check is
        not an attempt: it is not a generation.
        """

        def probe(
            selection: ModelSelection, generator: TextGenerator
        ) -> dict[str, object]:
            started = time.monotonic()
            try:
                return generator.check()
            except ProviderError as exc:
                self._failed_attempt(selection, started, exc)
                raise

        return self._walk(probe)

    def _failed_attempt(
        self, selection: ModelSelection, started: float, error: ProviderError
    ) -> None:
        """One failed attempt: the candidate that failed it and why."""
        self.attempts.append(
            GenerationAttempt(
                selection=selection,
                latency_ms=_elapsed_ms(started),
                ok=False,
                error=str(error),
                created_at=self._stamp(),
            )
        )

    def _stamp(self) -> str:
        """The moment of the call, after the moment of the call before it.

        The clock is coarse, so two calls in the same millisecond would take
        the same stamp and the execution log would lose their order.
        """
        now = utcnow()
        if self._last_at is not None and now <= self._last_at:
            now = self._last_at + timedelta(microseconds=1)
        self._last_at = now
        return now.isoformat()

    def _walk(self, action: Callable[[ModelSelection, TextGenerator], _T]) -> _T:
        """Run one action against the candidates of the chain, in order.

        A candidate that fails is excluded and the next one is tried. The
        last failure is raised when the chain is exhausted, so the reason the
        run stopped is the reason the last model gave. The failure is
        remembered for the whole generator, so a later chunk fails with that
        reason and not with "no model available".
        """
        for _ in range(MAX_FALLBACK_ATTEMPTS):
            try:
                selection = self.router.select(self.role, exclude=self._failed)
            except ProviderError as exc:
                if self._last_failure is not None:
                    raise self._last_failure from exc
                raise
            try:
                return action(selection, self.build(selection))
            except ProviderError as exc:
                # The error names the candidate that failed, so the record of
                # the failure names the model that made it.
                self._last_failure = _call_failed(selection, exc)
                self._failed.append(selection.identity)
        if self._last_failure is not None:
            raise self._last_failure
        raise RoutingError(f"no candidate to try for role {self.role.value!r}")


def _call_failed(selection: ModelSelection, error: ProviderError) -> ModelCallFailed:
    """The error of a failed call, carrying the candidate that failed it."""
    return ModelCallFailed(
        str(error),
        provider_id=selection.identity.provider_id,
        model_id=selection.identity.model_id,
    )


def _elapsed_ms(started: float) -> int:
    """The milliseconds a call took."""
    return round((time.monotonic() - started) * 1000)


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

"""The model catalogue: what the discovery surface offers, and what may run.

A model is identified by the pair (provider id, model id): the same model id on
two providers is two different models. Availability is derived from what the
source reported, never assumed, and only an ``available`` entry may be handed
to the pipeline. A malformed payload is refused by name; an empty catalogue is
valid. This module knows nothing about OpenCode, roles or SQLite.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


def utcnow() -> datetime:
    """The current UTC time.

    The project clock lives in product_hunter.fetch, which cannot be imported
    here: the fetch chain reaches back into the config, which imports this
    package.
    """
    return datetime.now(timezone.utc)


class CatalogError(Exception):
    """A catalogue operation was refused."""


class MalformedCatalogError(CatalogError):
    """The payload is not a model catalogue."""


class Availability(str, Enum):
    """Whether a model may be selected. Only ``available`` may."""

    available = "available"
    disabled = "disabled"
    retired = "retired"
    unknown = "unknown"
    missing = "missing"


class ModelIdentity(BaseModel):
    """A provider and a model id. The model id alone is not an identity."""

    model_config = ConfigDict(frozen=True)

    provider_id: str = Field(min_length=1)
    model_id: str = Field(min_length=1)

    @property
    def key(self) -> str:
        """The ``provider/model`` key the catalogue and the store use."""
        return f"{self.provider_id}/{self.model_id}"


class ModelEntry(BaseModel):
    """One model the source reported, with the availability derived from it."""

    identity: ModelIdentity
    name: str | None = None
    family: str | None = None
    status: str | None = None
    enabled: bool | None = None
    availability: Availability
    context_limit: int | None = None
    output_limit: int | None = None
    cost_input: float | None = None
    cost_output: float | None = None
    released_at: datetime | None = None
    source: str


class ModelCatalog(BaseModel):
    """One immutable snapshot of the models a source offered."""

    catalog_version: str
    source: str
    server_version: str | None = None
    discovered_at: datetime
    entries: list[ModelEntry]

    def by_key(self, key: str) -> ModelEntry | None:
        """The entry with this ``provider/model`` key, or None."""
        for entry in self.entries:
            if entry.identity.key == key:
                return entry
        return None

    def available(self) -> list[ModelEntry]:
        """Only the entries the source confirmed as usable."""
        return [e for e in self.entries if e.availability is Availability.available]

    def diff(self, other: ModelCatalog) -> CatalogDiff:
        """What changed between this catalogue and a later one."""
        before = {entry.identity.key: entry for entry in self.entries}
        after = {entry.identity.key: entry for entry in other.entries}
        return CatalogDiff(
            added=[after[key].identity for key in sorted(after) if key not in before],
            removed=[before[key].identity for key in sorted(before) if key not in after],
            changed=[
                after[key].identity
                for key in sorted(after)
                if key in before and _state(before[key]) != _state(after[key])
            ],
        )


class CatalogDiff(BaseModel):
    """The models a refresh added, removed, or changed."""

    added: list[ModelIdentity] = Field(default_factory=list)
    removed: list[ModelIdentity] = Field(default_factory=list)
    changed: list[ModelIdentity] = Field(default_factory=list)


def derive_availability(status: str | None, enabled: bool | None) -> Availability:
    """The availability a reported status and flag mean.

    A model is only ``available`` when the source said the status is active
    and the model is enabled. Anything the source did not confirm stays
    ``unknown`` and is therefore not selectable.
    """
    if enabled is False:
        return Availability.disabled
    if status is None:
        return Availability.unknown
    if status != "active":
        return Availability.retired
    if enabled is True:
        return Availability.available
    return Availability.unknown


def compute_catalog_version(entries: Iterable[ModelEntry]) -> str:
    """A content digest over the sorted ``provider/model|status|enabled|name`` lines."""
    lines = sorted(
        "|".join(
            (
                entry.identity.key,
                entry.status or "",
                "" if entry.enabled is None else ("true" if entry.enabled else "false"),
                entry.name or "",
            )
        )
        for entry in entries
    )
    digest = hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def parse_model_catalog(
    payload: str,
    *,
    source: str,
    server_version: str | None = None,
) -> ModelCatalog:
    """Parse the model route body into a catalogue.

    The body is ``{"data": [...]}`` or a bare list. A body that is not JSON, is
    not an object or a list, or holds an entry without a usable provider or
    model id is refused with ``MalformedCatalogError``.
    """
    try:
        document = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise MalformedCatalogError(f"{source}: payload is not JSON: {exc}") from exc
    except RecursionError as exc:
        raise MalformedCatalogError(
            f"{source}: payload is nested too deeply to parse: {exc}"
        ) from exc
    if isinstance(document, dict):
        rows = document.get("data")
    elif isinstance(document, list):
        rows = document
    else:
        raise MalformedCatalogError(f"{source}: payload is not an object or a list")
    if not isinstance(rows, list):
        raise MalformedCatalogError(f"{source}: payload has no 'data' array")
    entries = [_entry_from_route(row, source=source) for row in rows]
    return ModelCatalog(
        catalog_version=compute_catalog_version(entries),
        source=source,
        server_version=server_version,
        discovered_at=utcnow(),
        entries=entries,
    )


def parse_model_list(payload: str, *, source: str) -> ModelCatalog:
    """Parse the model list output: one ``provider/model`` per line.

    The list reports no status, so every entry it yields is ``unknown`` and
    therefore not selectable. A line that is not a provider/model pair is
    refused.
    """
    entries: list[ModelEntry] = []
    for number, line in enumerate(payload.splitlines(), start=1):
        text = line.strip()
        if not text:
            continue
        provider_id, separator, model_id = text.partition("/")
        if not separator or not provider_id or not model_id:
            raise MalformedCatalogError(
                f"{source}: line {number} is not a provider/model pair: {text!r}"
            )
        entries.append(
            ModelEntry(
                identity=ModelIdentity(provider_id=provider_id, model_id=model_id),
                availability=Availability.unknown,
                source=source,
            )
        )
    return ModelCatalog(
        catalog_version=compute_catalog_version(entries),
        source=source,
        discovered_at=utcnow(),
        entries=entries,
    )


def _entry_from_route(row: Any, *, source: str) -> ModelEntry:
    """One entry of the model route, with its availability derived."""
    if not isinstance(row, dict):
        raise MalformedCatalogError(f"{source}: catalogue entry is not an object")
    provider_id = _text(row.get("providerID"))
    model_id = _text(row.get("modelID")) or _text(row.get("id"))
    if not provider_id:
        raise MalformedCatalogError(f"{source}: catalogue entry has no provider id")
    if not model_id:
        raise MalformedCatalogError(
            f"{source}: catalogue entry for provider {provider_id!r} has no model id"
        )
    status = _text(row.get("status"))
    enabled = row.get("enabled")
    if not isinstance(enabled, bool):
        enabled = None
    limit = row.get("limit")
    context_limit = output_limit = None
    if isinstance(limit, dict):
        context_limit = _int(limit.get("context"))
        output_limit = _int(limit.get("output"))
    cost_input = cost_output = None
    cost = row.get("cost")
    if isinstance(cost, list) and cost and isinstance(cost[0], dict):
        cost_input = _float(cost[0].get("input"))
        cost_output = _float(cost[0].get("output"))
    released_at = None
    timing = row.get("time")
    if isinstance(timing, dict):
        released = timing.get("released")
        if isinstance(released, (int, float)) and not isinstance(released, bool):
            try:
                released_at = datetime.fromtimestamp(released / 1000, tz=timezone.utc)
            except (OverflowError, OSError, ValueError):
                # A timestamp outside the representable range is not a reason
                # to refuse the whole catalogue: the model is still there.
                released_at = None
    return ModelEntry(
        identity=ModelIdentity(provider_id=provider_id, model_id=model_id),
        name=_text(row.get("name")),
        family=_text(row.get("family")),
        status=status,
        enabled=enabled,
        availability=derive_availability(status, enabled),
        context_limit=context_limit,
        output_limit=output_limit,
        cost_input=cost_input,
        cost_output=cost_output,
        released_at=released_at,
        source=source,
    )


def _state(entry: ModelEntry) -> tuple[str | None, bool | None, str | None]:
    """The part of an entry a catalogue version and a diff look at."""
    return (entry.status, entry.enabled, entry.name)


def _text(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _float(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)

"""Turn a model's wording into a versioned key for the same pain point.

The model names a pain point in its own words. Two sources can describe the
same problem with different spelling, so the wording is slugged into a
``domain:action:object`` key. The key is versioned: a change to the slug
rules changes the meaning of the key, and the version travels with the record
so an old key is never silently read as a new one.
"""

from __future__ import annotations

import re
import unicodedata
from enum import Enum

from pydantic import BaseModel, ConfigDict

# The version that ships with this code. Records carry the version they were
# built with, so a key is only ever compared with keys of the same version.
DEFAULT_NORMALIZATION_VERSION = "norm-v1"

# The length caps on the controlled fields. They match the schema in
# extraction.py; a field over its cap is a model error, not a truncation.
MAX_ACTOR = 120
MAX_ACTION = 120
MAX_OBJECT = 120
MAX_CONSEQUENCE = 200
MAX_KEY = 200
MAX_SLUG = 48

# Every run of characters that is not a lowercase ASCII letter or a digit
# becomes one dash.
_NOT_SLUG = re.compile(r"[^a-z0-9]+")


class NormalizationError(Exception):
    """The candidate cannot be normalized into a usable key."""

    reason_code = "normalization_invalid"

    def __init__(self, detail: str = "") -> None:
        self.detail = detail
        super().__init__(detail or self.reason_code)


class Domain(str, Enum):
    """The area of work a pain point sits in."""

    software = "software"
    data = "data"
    operations = "operations"
    finance = "finance"
    sales = "sales"
    marketing = "marketing"
    support = "support"
    hr = "hr"
    legal = "legal"
    security = "security"
    research = "research"
    education = "education"
    healthcare = "healthcare"
    ecommerce = "ecommerce"
    other = "other"


def slugify(value: str, *, max_length: int = MAX_SLUG) -> str:
    """Reduce a phrase to the lowercase, dash-separated form used in keys."""
    text = unicodedata.normalize("NFKC", value).strip().lower()
    # NFKD splits the accents off; dropping everything non-ASCII then leaves
    # the plain letter behind it.
    text = unicodedata.normalize("NFKD", text)
    text = "".join(character for character in text if ord(character) < 128)
    text = _NOT_SLUG.sub("-", text).strip("-")
    if len(text) > max_length:
        text = text[:max_length].rstrip("-")
    if not text:
        raise NormalizationError(f"{value!r} has no characters that survive slugging")
    return text


class NormalizedProblem(BaseModel):
    """The controlled fields of one pain point, plus the key they make."""

    model_config = ConfigDict(extra="forbid")

    domain: Domain
    actor: str
    action: str
    object: str
    consequence: str
    normalized_key: str
    normalization_version: str


def normalize_fields(
    *,
    domain: Domain,
    actor: str,
    action: str,
    object_: str,
    consequence: str,
    normalization_version: str,
) -> NormalizedProblem:
    """Check the controlled fields and build the key that groups them."""
    version = normalization_version.strip()
    if not version:
        raise NormalizationError("normalization_version is empty")

    actor = actor.strip()
    action = action.strip()
    object_ = object_.strip()
    consequence = consequence.strip()

    for name, value, limit in (
        ("actor", actor, MAX_ACTOR),
        ("action", action, MAX_ACTION),
        ("object", object_, MAX_OBJECT),
        ("consequence", consequence, MAX_CONSEQUENCE),
    ):
        if not value:
            raise NormalizationError(f"{name} is empty")
        if len(value) > limit:
            raise NormalizationError(f"{name} is longer than {limit} characters")

    key = f"{domain.value}:{_slug(action, 'action')}:{_slug(object_, 'object')}"
    if len(key) > MAX_KEY:
        raise NormalizationError(f"the normalized key is longer than {MAX_KEY} characters")

    return NormalizedProblem(
        domain=domain,
        actor=actor,
        action=action,
        object=object_,
        consequence=consequence,
        normalized_key=key,
        normalization_version=version,
    )


def _slug(value: str, name: str) -> str:
    """Slug one field, naming the field when the slug comes out empty."""
    try:
        return slugify(value)
    except NormalizationError as exc:
        raise NormalizationError(f"{name} has no slug characters: {exc.detail}") from exc

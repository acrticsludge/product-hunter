"""The query type, its identifiers, and the two ways a query is produced.

A query is one search the frontier may run. This module owns the shape of
that query, the identifier it carries, the normalization that decides whether
two queries are the same, and the generation of a query from a code-owned
template.

This module never calls a model and never calls a search endpoint. The
wording of a query comes from a template in ``discovery.families``.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from product_hunter.discovery.families import (
    DERIVED_FAMILIES,
    FAMILY_SPECS,
    QueryFamily,
    SUBJECT_SLOT,
)
from product_hunter.fetch import utcnow
from product_hunter.normalization import NormalizedProblem

# The identifier prefix of a query. The id is a prefix and sixteen hex
# characters, the pattern the repository uses everywhere.
QUERY_ID_PREFIX = "q_"

# A query id is this shape, and a query that carries another shape was built
# by something other than this module.
_QUERY_ID_RE = re.compile(r"^q_[0-9a-f]{16}$")

# Everything that is not a word character and not whitespace is dropped from
# a query before it is compared or identified.
_NOT_WORD = re.compile(r"[^\w\s]")

# The default share of tokens two queries must share before the second one
# counts as a near duplicate of the first.
DEFAULT_NEAR_DUPLICATE_THRESHOLD = 0.9

# The worker is the only thing that may assign a family. The field is
# refused for any other value, so a record cannot claim a model chose it.
ASSIGNED_BY_WORKER = "worker"


def normalize_query(text: str) -> str:
    """Return the comparable form of a query text.

    The text is case-folded, everything that is not a word character or
    whitespace is dropped, and the whitespace runs collapse into one space.
    """
    return " ".join(_NOT_WORD.sub("", text.casefold()).split())


def query_id(text: str, source_id: str) -> str:
    """Return the identifier of a query text against a source.

    The identifier is the prefix and the first sixteen hex characters of the
    SHA-256 of the source id and the normalized query text, joined by a
    pipe. The same query against the same source is the same id in every
    run, so a frontier can refuse to re-issue a query it already ran.
    """
    basis = f"{source_id}|{normalize_query(text)}"
    return QUERY_ID_PREFIX + hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16]


def is_duplicate(
    left: str,
    right: str,
    threshold: float = DEFAULT_NEAR_DUPLICATE_THRESHOLD,
) -> bool:
    """Report whether two query texts are the same query.

    Equal normalized text is a duplicate. Otherwise the token sets are
    compared by Jaccard similarity, and a similarity at or above the
    threshold is a duplicate.
    """
    left_normalized = normalize_query(left)
    right_normalized = normalize_query(right)
    if left_normalized == right_normalized:
        return True
    left_tokens = set(left_normalized.split())
    right_tokens = set(right_normalized.split())
    if not left_tokens or not right_tokens:
        return False
    union = left_tokens | right_tokens
    similarity = len(left_tokens & right_tokens) / len(union)
    return similarity >= threshold


class Query(BaseModel):
    """One search the frontier may run, with the family the worker gave it."""

    model_config = ConfigDict(extra="forbid")

    query_id: str
    text: str = Field(min_length=1)
    normalized: str
    family: QueryFamily
    source_id: str = Field(min_length=1)
    seed: str = ""
    parent_query_id: str | None = None
    template_id: str = ""
    assigned_by: str = ASSIGNED_BY_WORKER
    assignment_reason: str = Field(min_length=1)
    depth: int = Field(default=0, ge=0)
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def _created_at_is_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
            raise ValueError("created_at must be timezone-aware")
        return value

    @field_validator("parent_query_id")
    @classmethod
    def _parent_is_a_query_id(cls, value: str | None) -> str | None:
        if value is not None and not _QUERY_ID_RE.match(value):
            raise ValueError("parent_query_id must be a query id")
        return value

    @model_validator(mode="after")
    def _check_invariants(self) -> Query:
        if self.assigned_by != ASSIGNED_BY_WORKER:
            raise ValueError("assigned_by must be 'worker'")
        expected_normalized = normalize_query(self.text)
        if self.normalized != expected_normalized:
            raise ValueError(
                "normalized must be the normalized form of the query text"
            )
        expected_id = query_id(self.text, self.source_id)
        if self.query_id != expected_id:
            raise ValueError(f"query_id must be {expected_id} for this text and source")
        return self


class SourceCandidate(BaseModel):
    """A source discovery proposed, and not yet admitted.

    A candidate is a proposal until a human reviews it, so the model refuses
    to be built with another status, another terms status, or an allowed
    use. Discovery proposes a source; it never admits one.

    The fields of a candidate are a proposal and never admission authority:
    no consumer may read them as the permission to use a source. A source
    is admitted by the source registry, and a run may only search a source
    the frontier was given.
    """

    model_config = ConfigDict(extra="forbid")

    candidate_id: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    domain: str = ""
    community: str = ""
    source_type: str = ""
    discovered_by: str = Field(min_length=1)
    discovered_at: datetime
    status: Literal["proposed"] = "proposed"
    terms_status: Literal["unknown"] = "unknown"
    allowed_use: list[str] = Field(default_factory=list)

    @field_validator("discovered_at")
    @classmethod
    def _discovered_at_is_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
            raise ValueError("discovered_at must be timezone-aware")
        return value

    @field_validator("allowed_use")
    @classmethod
    def _allowed_use_is_empty(cls, value: list[str]) -> list[str]:
        if value:
            raise ValueError("a source candidate carries no allowed use")
        return value


def generate_queries(
    seed: str,
    families: list[QueryFamily],
    source_id: str,
    *,
    now: datetime | None = None,
    depth: int = 0,
    parent_query_id: str | None = None,
) -> list[Query]:
    """Render the templates of each family with the seed as the subject.

    The family of a generated query is the family of the template that
    generated it, and the template id travels with the query.
    """
    moment = now or utcnow()
    queries: list[Query] = []
    for family in families:
        for index, template in enumerate(FAMILY_SPECS[family].templates):
            # The slot is substituted rather than formatted, so a subject
            # that carries a brace of its own cannot break the render.
            text = template.replace(SUBJECT_SLOT, seed).strip()
            template_id = f"{family.value}-{index}"
            queries.append(
                Query(
                    query_id=query_id(text, source_id),
                    text=text,
                    normalized=normalize_query(text),
                    family=family,
                    source_id=source_id,
                    seed=seed,
                    parent_query_id=parent_query_id,
                    template_id=template_id,
                    assigned_by=ASSIGNED_BY_WORKER,
                    assignment_reason=f"template:{template_id}",
                    depth=depth,
                    created_at=moment,
                )
            )
    return queries


def generate_derived_queries(
    problem: NormalizedProblem,
    source_id: str,
    *,
    now: datetime | None = None,
    depth: int = 1,
    parent_query_id: str | None = None,
) -> list[Query]:
    """Render the derived families from a normalized problem.

    The subject is the action and the object of the problem, and the seed is
    the normalized key. Only the competitor and the counter-evidence family
    can be written this way: neither can be phrased from a raw objective.

    The subject is normalised to one line, so a problem whose action or
    object carries a line break still renders a query that is a rendering
    of its template.
    """
    subject = " ".join(f"{problem.action} {problem.object}".split())
    queries = generate_queries(
        subject,
        list(DERIVED_FAMILIES),
        source_id,
        now=now,
        depth=depth,
        parent_query_id=parent_query_id,
    )
    return [
        query.model_copy(update={"seed": problem.normalized_key}) for query in queries
    ]

"""The vector space, the embedder, and the cosine between two vectors.

Two vectors may only be compared when they come from the same space. The
space id is the guard: it is a digest of the model that made the vectors, so
a change of embedding model is visible instead of blended.
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, model_validator

from product_hunter.config import ClusteringConfig, ClusteringError

# The separator between the parts of the space digest.
_SEPARATOR = "\x1f"


class MixedVectorSpaceError(ClusteringError):
    """Two vector spaces were compared, or a result crossed spaces."""

    reason_code = "mixed_vector_space"


class EmbeddingUnavailable(ClusteringError):
    """The embedding package is not installed."""

    reason_code = "embedding_unavailable"


class VectorSpace(BaseModel):
    """The identity of an embedding model.

    The space id is a digest of the four fields below, and the model refuses
    a space id that does not match them: the id is the guard, so it cannot
    be written by hand to make two spaces look like one.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    model_version: str = Field(min_length=1)
    dimensions: int = Field(gt=0)
    space_id: str = Field(min_length=1)

    @classmethod
    def of(
        cls,
        *,
        provider: str,
        model: str,
        model_version: str,
        dimensions: int,
    ) -> VectorSpace:
        """The space of one embedding model."""
        return cls(
            provider=provider,
            model=model,
            model_version=model_version,
            dimensions=dimensions,
            space_id=space_id(provider, model, model_version, dimensions),
        )

    @model_validator(mode="after")
    def _space_id_matches(self) -> VectorSpace:
        expected = space_id(
            self.provider, self.model, self.model_version, self.dimensions
        )
        if self.space_id != expected:
            raise ValueError("the space id does not match the space fields")
        return self


def space_id(
    provider: str, model: str, model_version: str, dimensions: int
) -> str:
    """The digest that names one vector space."""
    basis = _SEPARATOR.join(
        (provider.strip(), model.strip(), model_version.strip(), str(dimensions))
    )
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()


@runtime_checkable
class Embedder(Protocol):
    """What the wave needs from an embedding model."""

    @property
    def space(self) -> VectorSpace:
        """The space the vectors of this embedder live in."""
        ...

    def embed(self, texts: Sequence[str]) -> list[tuple[float, ...]]:
        """One vector per text, in the order the texts were given."""
        ...


class SentenceTransformerEmbedder:
    """The embedder over a local sentence-transformers model.

    The package is not a dependency of this repository and is imported when
    the embedder is used, not when it is built. The model name comes from
    the config: this class never names a model.
    """

    def __init__(self, space: VectorSpace) -> None:
        self._space = space
        self._model = None

    @classmethod
    def from_config(cls, config: ClusteringConfig) -> SentenceTransformerEmbedder:
        """The embedder of the model the config names."""
        embedding = config.embedding
        return cls(
            VectorSpace.of(
                provider=embedding.provider,
                model=embedding.model,
                model_version=embedding.model_version,
                dimensions=embedding.dimensions,
            )
        )

    @property
    def space(self) -> VectorSpace:
        """The space the vectors of this embedder live in."""
        return self._space

    def embed(self, texts: Sequence[str]) -> list[tuple[float, ...]]:
        """One vector per text, in the order the texts were given."""
        model = self._load()
        vectors = model.encode(list(texts))
        return [tuple(float(value) for value in vector) for vector in vectors]

    def _load(self):
        """The loaded model, imported on first use."""
        if self._model is not None:
            return self._model
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            raise EmbeddingUnavailable(
                "the sentence-transformers package is not installed"
            ) from exc
        self._model = SentenceTransformer(self._space.model)
        return self._model


def cosine_similarity(
    left: Sequence[float], right: Sequence[float]
) -> float:
    """The cosine of the angle between two vectors of one space."""
    if len(left) != len(right):
        raise MixedVectorSpaceError(
            f"vectors of {len(left)} and {len(right)} dimensions are not "
            "of the same space"
        )
    dot = math.fsum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(math.fsum(value * value for value in left))
    right_norm = math.sqrt(math.fsum(value * value for value in right))
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    return dot / (left_norm * right_norm)


def require_same_space(left: str, right: str) -> None:
    """Refuse a comparison of two spaces, naming both of them.

    The two arguments are space ids: a space id is the identity of a
    vector space, and the caller that holds one usually holds no more than
    the id.
    """
    if left != right:
        raise MixedVectorSpaceError(
            f"the space {left} and the space {right} are not the same space "
            "and cannot be compared"
        )


def centroid(vectors: Sequence[Sequence[float]]) -> tuple[float, ...]:
    """The mean of the vectors of a cluster."""
    if not vectors:
        return ()
    length = len(vectors[0])
    if any(len(vector) != length for vector in vectors):
        raise MixedVectorSpaceError(
            "the vectors of one cluster are not of the same space"
        )
    return tuple(
        math.fsum(vector[index] for vector in vectors) / len(vectors)
        for index in range(length)
    )

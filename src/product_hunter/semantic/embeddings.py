"""The embedder the config names, and the batch embed with its guard.

The embedder is a provider: the index asks it for vectors and for what it
is, and the provider behind it is the one the config names. The model is
never named in this module, and the package that loads it is imported when
the provider is used.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from product_hunter.clustering.similarity import Embedder, MixedVectorSpaceError
from product_hunter.config import SemanticConfig
from product_hunter.semantic.providers import _is_degenerate
from product_hunter.semantic.providers import build_provider


def build_embedder(
    config: SemanticConfig, *, root: Path | None = None
) -> Embedder:
    """The embedder the config names.

    The name stays, because the index calls it. What it hands back is a
    provider, which is an embedder plus the diagnostics and the release.
    """
    return build_provider(config, root=root)


def embed_all(embedder: Embedder, texts: Sequence[str]) -> list[tuple[float, ...]]:
    """One vector per text, each checked against the embedder's space.

    A vector of the wrong width means the model under the index changed, and
    a collection must never hold two spaces.
    """
    vectors = embedder.embed(texts)
    width = embedder.space.dimensions
    for vector in vectors:
        if len(vector) != width:
            raise MixedVectorSpaceError(
                f"a vector of {len(vector)} dimensions came back from a space "
                f"of {width} dimensions"
            )
        if _is_degenerate(vector):
            raise MixedVectorSpaceError(
                "a degenerate vector (zero, constant, or non-finite) "
                "came back from the embedder"
            )
    return [tuple(float(value) for value in vector) for vector in vectors]

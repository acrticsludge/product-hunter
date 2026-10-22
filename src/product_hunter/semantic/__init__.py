"""The semantic retrieval layer: the index that answers a question in words.

The package is a projection of the authoritative store, never a second
source of truth. The vector space identity and the embedder live in the
clustering package, and this package imports them, so the repository holds
one notion of a vector space.

The provider is the seam: the index asks a provider for vectors and for what
it is, and never reaches for a model library of its own.
"""

from __future__ import annotations

from product_hunter.semantic.embeddings import build_embedder, embed_all
from product_hunter.semantic.index import (
    EmbeddingSpaceMismatch,
    IndexSyncFailed,
    SearchHit,
    SemanticIndex,
    SyncReport,
)
from product_hunter.semantic.providers import (
    DenseEncoder,
    DeviceUnavailable,
    EmbeddingProvider,
    LocalBGEM3Provider,
    ModelAcquisitionFailed,
    ModelCacheCorrupt,
    ModelLoadFailed,
    ProviderError,
    SentenceTransformerProvider,
    UnknownProvider,
    build_provider,
)
from product_hunter.semantic.qdrant import (
    QdrantAdapter,
    QdrantConfigError,
    QdrantError,
    QdrantRequestFailed,
    QdrantUnavailable,
)
from product_hunter.semantic.source import Entity, SemanticSource
from product_hunter.semantic.vectors import (
    ENTITY_CLUSTER,
    ENTITY_EVIDENCE,
    ENTITY_PAIN_POINT,
    ENTITY_TYPES,
    SearchFilter,
    VectorPayload,
    vector_id,
)

__all__ = [
    "ENTITY_CLUSTER",
    "ENTITY_EVIDENCE",
    "ENTITY_PAIN_POINT",
    "ENTITY_TYPES",
    "DenseEncoder",
    "DeviceUnavailable",
    "EmbeddingProvider",
    "EmbeddingSpaceMismatch",
    "Entity",
    "IndexSyncFailed",
    "LocalBGEM3Provider",
    "ModelAcquisitionFailed",
    "ModelCacheCorrupt",
    "ModelLoadFailed",
    "ProviderError",
    "QdrantAdapter",
    "QdrantConfigError",
    "QdrantError",
    "QdrantRequestFailed",
    "QdrantUnavailable",
    "SearchFilter",
    "SearchHit",
    "SemanticIndex",
    "SemanticSource",
    "SentenceTransformerProvider",
    "SyncReport",
    "UnknownProvider",
    "VectorPayload",
    "build_embedder",
    "build_provider",
    "embed_all",
    "vector_id",
]

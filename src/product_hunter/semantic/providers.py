"""The embedding providers: the model, the weights and the device.

A provider owns one vector space. It keeps its model loaded, keeps its
weights in a local cache, and can say what it is. The index talks to the
protocol and never to a model library, so a different model is a different
provider rather than a branch inside the index.

The configuration is the only thing that names the model. A page, a post or
a model's own answer never decides what gets downloaded, and the identifier
is refused before anything is fetched if it is not an org/name pair.
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from product_hunter.clustering.similarity import (
    EmbeddingUnavailable,
    SentenceTransformerEmbedder,
    VectorSpace,
)
from product_hunter.config import EMBEDDING_DEVICES, SemanticConfig, repo_root

EventSink = Callable[[Mapping[str, str]], None]
"""Where the provider hands over the events it cannot record itself.

The provider does not log. It hands a structured event to the caller, and the
caller decides what to do with it, so the observability policy stays with the
application layer. An event carries the model, the device and the file names
of the cache. It never carries the message of an exception, because a path or
a token in a message would ride along into a log line.
"""

Downloader = Callable[..., str]
"""(repo_id, *, cache_dir, revision, local_only) -> the path of the snapshot."""

ModelFactory = Callable[..., object]
"""(path, *, device, normalize) -> the loaded model."""

# A model identifier is an org/name pair of plain identifier characters.
# A scheme, a second slash, a dot-dot or a leading dot is refused.
_MODEL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*$")


@runtime_checkable
class EmbeddingProvider(Protocol):
    """What the index needs from an embedding model.

    The index depends on this, never on a model library. A provider owns one
    vector space, loads its model at most once, and can say what it is.
    """

    @property
    def space(self) -> VectorSpace:
        """The space the vectors of this provider live in."""
        ...

    def embed(self, texts: Sequence[str]) -> list[tuple[float, ...]]:
        """One vector per text, in the order the texts were given."""
        ...

    def diagnostics(self) -> dict[str, object]:
        """What an operator needs to know about this provider."""
        ...

    def release(self) -> None:
        """Let go of whatever the provider holds."""
        ...


class ProviderError(Exception):
    """An embedding provider could not do its work."""

    reason_code = "embedding_provider_error"

    def __init__(self, detail: str = "") -> None:
        self.detail = detail
        super().__init__(detail or self.reason_code)


class ModelAcquisitionFailed(ProviderError):
    """The weights of the model could not be fetched."""

    reason_code = "embedding_model_acquisition_failed"


class ModelCacheCorrupt(ProviderError):
    """The cache holds a snapshot that cannot be loaded."""

    reason_code = "embedding_model_cache_corrupt"


class ModelLoadFailed(ProviderError):
    """The model could not be loaded."""

    reason_code = "embedding_model_load_failed"


class DeviceUnavailable(ProviderError):
    """The device the config asked for is not there."""

    reason_code = "embedding_device_unavailable"


class UnknownProvider(ProviderError):
    """The config named a provider that does not exist."""

    reason_code = "embedding_provider_unknown"


def _is_degenerate(vector: object) -> bool:
    """A vector carries no information when it is all zero, all equal, or
    contains a non-finite value. None of those may be accepted as evidence.
    """
    try:
        values = [float(value) for value in vector]  # type: ignore[not-iterable]
    except (TypeError, ValueError):
        return True
    if not values:
        return True
    if any(not math.isfinite(value) for value in values):
        return True
    if all(value == 0.0 for value in values):
        return True
    if all(value == values[0] for value in values):
        return True
    return False


def _checked_model_id(model: str) -> str:
    """The model identifier, refused when it is not an org/name pair.

    The check is the guard that keeps a scraped url or a model's answer from
    choosing what gets downloaded: only the configuration names a model, and
    only a plain identifier reaches the model distribution.
    """
    if not _MODEL_ID.match(model) or ".." in model:
        raise ProviderError(
            f"the model identifier {model!r} is not an org/name pair of plain "
            "identifier characters"
        )
    return model


class LocalBGEM3Provider:
    """BGE-M3, run in this process, with the weights fetched once and kept.

    The model is loaded at most once and reused for every batch. The weights
    come from the model distribution the configuration names, and the
    configuration is the only thing that can name it: a page, a post or a
    model's answer never decides what gets downloaded.
    """

    def __init__(
        self,
        space: VectorSpace,
        *,
        model: str,
        cache_dir: str,
        device: str = "auto",
        batch_size: int = 16,
        normalize: bool = True,
        revision: str = "",
        downloader: Downloader | None = None,
        model_factory: ModelFactory | None = None,
        cuda_available: Callable[[], bool] | None = None,
        events: EventSink | None = None,
        required_files: Sequence[str] = ("config.json", "model.safetensors"),
    ) -> None:
        self._space = space
        self._model_name = _checked_model_id(model)
        self._cache_dir = str(cache_dir)
        self._device_requested = device
        self._batch_size = max(1, int(batch_size))
        self._normalize = bool(normalize)
        self._revision = revision
        self._downloader = downloader if downloader is not None else _default_downloader
        self._model_factory = (
            model_factory if model_factory is not None else _default_model_factory
        )
        self._cuda_available = (
            cuda_available if cuda_available is not None else _default_cuda_available
        )
        self._events = events
        self._required_files = tuple(required_files)
        self._model: Any | None = None
        self._device = ""
        self._fallback = ""

    @property
    def space(self) -> VectorSpace:
        """The space the vectors of this provider live in."""
        return self._space

    def embed(self, texts: Sequence[str]) -> list[tuple[float, ...]]:
        """One vector per text, embedded in batches of the configured size.

        The model is loaded on the first call and reused by every later one.
        """
        model = self._ensure_loaded()
        wanted = list(texts)
        vectors: list[tuple[float, ...]] = []
        for start in range(0, len(wanted), self._batch_size):
            chunk = wanted[start : start + self._batch_size]
            encoded = model.encode(chunk, batch_size=self._batch_size)
            if len(encoded) != len(chunk):
                raise ProviderError(
                    f"the model returned {len(encoded)} vectors for "
                    f"{len(chunk)} texts"
                )
            for vector in encoded:
                if _is_degenerate(vector):
                    raise ProviderError(
                        "the model produced a degenerate vector "
                        "(zero, constant, or non-finite)"
                    )
            vectors.extend(
                tuple(float(value) for value in vector) for vector in encoded
            )
        return vectors

    def diagnostics(self) -> dict[str, object]:
        """What an operator needs to know about this provider.

        The answer comes without loading the model: the cache is asked about
        the weights, and a download is never triggered here.
        """
        return {
            "provider": self._space.provider,
            "model": self._model_name,
            "revision": self._revision,
            "revision_pinned": bool(self._revision),
            "cache_dir": self._cache_dir,
            "cached": self._is_cached(),
            "device": self._device,
            "device_requested": self._device_requested,
            "cuda_available": self._cuda_available(),
            "dimensions": self._space.dimensions,
            "loaded": self._model is not None,
            "space_id": self._space.space_id,
            "normalize": self._normalize,
            "batch_size": self._batch_size,
            "fallback": self._fallback,
        }

    def release(self) -> None:
        """Let go of whatever the provider holds.

        The model is dropped and the memory it holds goes with it. A later
        embed loads the model again.
        """
        self._model = None
        self._device = ""
        self._fallback = ""

    # --- the lifecycle ------------------------------------------------------

    def _ensure_loaded(self) -> Any:
        """The model, loaded once and kept."""
        if self._model is not None:
            return self._model
        device = self._resolve_device()
        path = self._resolve_weights()
        model, device = self._load(path, device)
        self._model = model
        self._device = device
        return model

    def _resolve_device(self) -> str:
        """The device the model runs on.

        An explicit cuda that is not there is a refusal: the operator asked
        for the gpu and a quiet fall back to the cpu would hide it.
        """
        preference = self._device_requested
        if preference == "cpu":
            return "cpu"
        if preference == "cuda":
            if not self._cuda_available():
                raise DeviceUnavailable("CUDA was required and is not available")
            return "cuda"
        if preference == "auto":
            return "cuda" if self._cuda_available() else "cpu"
        raise ProviderError(
            f"the device preference {preference!r} is not one of "
            + ", ".join(EMBEDDING_DEVICES)
        )

    def _resolve_weights(self) -> str:
        """Where the weights are, fetched once and kept in the cache."""
        path = self._cached_path()
        if path:
            self._check_snapshot(path)
            self._emit(
                "model_cache_hit",
                model=self._model_name,
                path=path,
                revision=self._revision,
            )
            return path
        self._emit(
            "model_download_started",
            model=self._model_name,
            cache_dir=self._cache_dir,
            revision=self._revision,
        )
        try:
            path = self._downloader(
                self._model_name,
                cache_dir=self._cache_dir,
                revision=self._revision,
                local_only=False,
            )
        except EmbeddingUnavailable:
            raise
        except Exception as exc:
            raise ModelAcquisitionFailed(
                f"the weights of {self._model_name} could not be fetched: "
                f"{type(exc).__name__}"
            ) from exc
        self._check_snapshot(path)
        self._emit(
            "model_download_completed",
            model=self._model_name,
            path=path,
            revision=self._revision,
        )
        return path

    def _cached_path(self) -> str:
        """The cached snapshot, or nothing when the cache holds no model."""
        try:
            path = self._downloader(
                self._model_name,
                cache_dir=self._cache_dir,
                revision=self._revision,
                local_only=True,
            )
        except EmbeddingUnavailable:
            raise
        except Exception:
            return ""
        return str(path or "")

    def _check_snapshot(self, path: str) -> None:
        """The snapshot must hold every file the model needs.

        A corrupt cache is a fact an operator has to see, so the provider
        refuses it instead of fetching the weights again behind its back.

        BGE-M3 ships either ``model.safetensors`` or ``pytorch_model.bin``
        depending on the revision, so the two are treated as alternatives:
        at least one of them must be present and non-empty.
        """
        weight_names = {"model.safetensors", "pytorch_model.bin"}
        other_files = [name for name in self._required_files if name not in weight_names]
        for name in other_files:
            candidate = Path(path) / name
            if not candidate.is_file():
                raise ModelCacheCorrupt(
                    f"the model cache at {path} is missing {name}"
                )
            if candidate.stat().st_size == 0:
                raise ModelCacheCorrupt(
                    f"the model cache at {path} holds an empty {name}"
                )
        # The model ships either format depending on the revision, so at
        # least one of them must be present and non-empty.
        present = [
            Path(path) / name for name in weight_names
            if (Path(path) / name).is_file()
        ]
        if not present:
            raise ModelCacheCorrupt(
                f"the model cache at {path} is missing one of "
                + ", ".join(sorted(weight_names))
            )
        if any(candidate.stat().st_size == 0 for candidate in present):
            raise ModelCacheCorrupt(
                f"the model cache at {path} holds an empty weight file"
            )

    def _load(self, path: str, device: str) -> tuple[Any, str]:
        """The model on the device, with the fall back when it is allowed."""
        try:
            return self._load_on(path, device), device
        except EmbeddingUnavailable:
            raise
        except Exception as exc:
            if device == "cuda" and self._device_requested == "auto":
                self._emit(
                    "device_fallback",
                    model=self._model_name,
                    from_device="cuda",
                    to_device="cpu",
                    error=type(exc).__name__,
                )
                try:
                    model = self._load_on(path, "cpu")
                except Exception as cpu_exc:
                    raise ModelLoadFailed(
                        "the model could not be loaded on the cpu after the "
                        f"cuda load failed: {type(cpu_exc).__name__}"
                    ) from cpu_exc
                self._fallback = (
                    f"the cuda load failed with {type(exc).__name__}, "
                    "the model ran on the cpu"
                )
                return model, "cpu"
            if device == "cuda":
                raise DeviceUnavailable(
                    f"the model could not be loaded on cuda: {type(exc).__name__}"
                ) from exc
            raise ModelLoadFailed(
                f"the model could not be loaded on {device}: {type(exc).__name__}"
            ) from exc

    def _load_on(self, path: str, device: str) -> Any:
        """One load attempt, with the events around it."""
        self._emit(
            "model_load_started",
            model=self._model_name,
            device=device,
            path=path,
        )
        try:
            model = self._model_factory(
                path, device=device, normalize=self._normalize
            )
        except Exception as exc:
            self._emit(
                "model_load_failed",
                model=self._model_name,
                device=device,
                error=type(exc).__name__,
            )
            raise
        self._emit(
            "model_loaded",
            model=self._model_name,
            device=device,
            dimensions=str(self._space.dimensions),
        )
        return model

    def _is_cached(self) -> bool:
        """Whether the weights are in the cache. The model is not loaded."""
        try:
            path = self._downloader(
                self._model_name,
                cache_dir=self._cache_dir,
                revision=self._revision,
                local_only=True,
            )
        except Exception:
            return False
        return bool(path)

    def _emit(self, event: str, **fields: str) -> None:
        """Hand an event to the caller, when the caller wants events.

        The provider does not log, and the events never carry the message of
        an exception: only the class name of the failure travels.

        shortcut: a caller that passes no sink hears nothing, so a download
        and a fall back are silent; the index wires a sink that records the
        events when it runs.
        """
        if self._events is None:
            return
        self._events({"event": event, **fields})


class SentenceTransformerProvider:
    """The clustering wave's embedder behind the provider interface.

    It keeps the previous behaviour reachable and shows that the
    abstraction is replaceable: the index never learns which one it holds.
    """

    def __init__(
        self,
        space: VectorSpace,
        *,
        embedder: SentenceTransformerEmbedder | None = None,
    ) -> None:
        self._space = space
        self._embedder = (
            embedder if embedder is not None else SentenceTransformerEmbedder(space)
        )
        self._loaded = False

    @property
    def space(self) -> VectorSpace:
        """The space the vectors of this provider live in."""
        return self._space

    def embed(self, texts: Sequence[str]) -> list[tuple[float, ...]]:
        """One vector per text, in the order the texts were given."""
        vectors = self._embedder.embed(texts)
        self._loaded = True
        return vectors

    def diagnostics(self) -> dict[str, object]:
        """What an operator needs to know about this provider."""
        return {
            "provider": self._space.provider,
            "model": self._space.model,
            "revision": "",
            "revision_pinned": False,
            "cache_dir": "",
            "cached": False,
            "device": "cpu",
            "device_requested": "auto",
            "cuda_available": False,
            "dimensions": self._space.dimensions,
            "loaded": self._loaded,
            "space_id": self._space.space_id,
            "normalize": False,
            "batch_size": None,
            "fallback": "",
        }

    def release(self) -> None:
        """Let go of whatever the provider holds.

        The sentence-transformers model lives in the embedder of the
        clustering wave, which has no release of its own, so the provider
        drops the embedder and the next embed builds a new one.
        """
        self._embedder = SentenceTransformerEmbedder(self._space)
        self._loaded = False


def _space_of(config: SemanticConfig) -> VectorSpace:
    """The space the config names."""
    embedding = config.embedding
    return VectorSpace.of(
        provider=embedding.provider,
        model=embedding.model,
        model_version=config.space_model_version,
        dimensions=embedding.dimensions,
    )


def _cache_dir(cache_dir: str, root: Path | None) -> str:
    """The cache directory, resolved against the root when it is relative."""
    if not cache_dir:
        return ""
    path = Path(cache_dir).expanduser()
    if path.is_absolute():
        return str(path)
    return str((root if root is not None else repo_root()) / path)


def _build_local_bge_m3(
    config: SemanticConfig, *, root: Path | None = None
) -> EmbeddingProvider:
    """The BGE-M3 provider the config names."""
    embedding = config.embedding
    return LocalBGEM3Provider(
        _space_of(config),
        model=embedding.model,
        cache_dir=_cache_dir(embedding.cache_dir, root),
        device=embedding.device,
        batch_size=embedding.batch_size,
        normalize=embedding.normalize,
        revision=embedding.revision,
    )


def _build_sentence_transformers(
    config: SemanticConfig, *, root: Path | None = None
) -> EmbeddingProvider:
    """The provider of the model the clustering wave names."""
    return SentenceTransformerProvider(_space_of(config))


_PROVIDERS: dict[str, Callable[..., EmbeddingProvider]] = {
    "bge-m3-local": _build_local_bge_m3,
    "sentence-transformers": _build_sentence_transformers,
}


def build_provider(
    config: SemanticConfig, *, root: Path | None = None
) -> EmbeddingProvider:
    """The provider the config names."""
    name = config.embedding.provider
    builder = _PROVIDERS.get(name)
    if builder is None:
        raise UnknownProvider(
            f"the provider {name!r} is not known; the providers are: "
            + ", ".join(sorted(_PROVIDERS))
        )
    return builder(config, root=root)


class DenseEncoder:
    """One dense vector per text, from a model that returns a mapping.

    The BGE-M3 model answers with a mapping of the three kinds of vector it
    can produce. The index wants the dense ones, so the mapping is unwrapped
    here and the provider never learns what the model library returns.
    """

    def __init__(self, model: Any) -> None:
        self._model = model

    def encode(self, texts: Sequence[str], batch_size: int = 16) -> list[Any]:
        """One dense vector per text, in the order the texts were given."""
        result = self._model.encode(list(texts), batch_size=batch_size)
        if isinstance(result, Mapping):
            dense = result.get("dense_vecs")
            if dense is None:
                raise ProviderError("the model returned no dense vectors")
            return list(dense)
        return list(result)


def _default_downloader(
    repo_id: str, *, cache_dir: str, revision: str, local_only: bool
) -> str:
    """The weights from the model distribution, or the cached copy."""
    try:
        from huggingface_hub import snapshot_download
    except ImportError as exc:
        raise EmbeddingUnavailable(
            "the huggingface_hub package is not installed; install the "
            "'embeddings' extra to fetch a model"
        ) from exc
    return snapshot_download(
        repo_id=repo_id,
        cache_dir=cache_dir or None,
        revision=revision or None,
        local_files_only=local_only,
    )


def _default_model_factory(path: str, *, device: str, normalize: bool) -> object:
    """The BGE-M3 model, loaded from a local directory."""
    try:
        from FlagEmbedding import BGEM3FlagModel
    except ImportError as exc:
        raise EmbeddingUnavailable(
            "the flagembedding package is not installed; install the "
            "'embeddings' extra to run the local BGE-M3 provider"
        ) from exc
    return DenseEncoder(
        BGEM3FlagModel(
            path,
            use_fp16=(device == "cuda"),
            devices=device,
            normalize_embeddings=normalize,
        )
    )


def _default_cuda_available() -> bool:
    """Whether CUDA is there. A machine without torch is a cpu machine."""
    try:
        import torch
    except ImportError:
        return False
    return bool(torch.cuda.is_available())

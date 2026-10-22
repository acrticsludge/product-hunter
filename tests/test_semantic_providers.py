"""The embedding providers: the model, the weights, the device, the space.

No test in this file reaches the network, loads torch or fetches a weight.
The downloader and the model factory are arguments, so the provider is
driven by fakes that say what they stand for. The one test that could reach
the model distribution is marked, and it is skipped unless the model package
is installed.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

import pytest
import yaml

from product_hunter.clustering.similarity import MixedVectorSpaceError, VectorSpace
from product_hunter.config import SemanticConfig, load_semantic_config, repo_root
from product_hunter.semantic.embeddings import embed_all
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

SHIPPED_CONFIG = repo_root() / "config" / "semantic.yaml"

# The files a snapshot of the model must hold.
SNAPSHOT_FILES = {"config.json": b"{}", "model.safetensors": b"weights"}


def _config(**embedding: object) -> SemanticConfig:
    """The shipped config, with the embedding block changed."""
    data = yaml.safe_load(SHIPPED_CONFIG.read_text(encoding="utf-8"))
    data["embedding"].update(embedding)
    return SemanticConfig.model_validate(data)


def _vector(text: str, width: int) -> list[float]:
    """A vector that depends on the text and on nothing else."""
    seed = sum(ord(character) for character in text) or 1
    return [float((seed + index) % 97) for index in range(width)]


def _snapshot(path: Path, files: Mapping[str, bytes] | None = None) -> Path:
    """A snapshot of a model on disk."""
    path.mkdir(parents=True, exist_ok=True)
    for name, content in (SNAPSHOT_FILES if files is None else files).items():
        (path / name).write_bytes(content)
    return path


class _Downloader:
    """A downloader that hands out a snapshot that is already on disk."""

    def __init__(
        self,
        snapshot: Path,
        *,
        cached: bool = True,
        failure: Exception | None = None,
    ) -> None:
        self.snapshot = snapshot
        self.cached = cached
        self.failure = failure
        self.calls: list[dict[str, object]] = []

    def __call__(
        self, repo_id: str, *, cache_dir: str, revision: str, local_only: bool
    ) -> str:
        self.calls.append(
            {
                "repo_id": repo_id,
                "cache_dir": cache_dir,
                "revision": revision,
                "local_only": local_only,
            }
        )
        if local_only:
            if not self.cached:
                raise FileNotFoundError("the cache holds no snapshot")
            return str(self.snapshot)
        if self.failure is not None:
            raise self.failure
        return str(self.snapshot)


class _Model:
    """A model that turns a text into a vector."""

    def __init__(self, width: int, *, short: bool = False) -> None:
        self.width = width
        self.short = short
        self.batches: list[list[str]] = []

    def encode(self, texts, batch_size=None):
        texts = list(texts)
        self.batches.append(texts)
        wanted = texts[:-1] if self.short else texts
        return [_vector(text, self.width) for text in wanted]


class _Factory:
    """A factory that hands out models and remembers what it was asked for."""

    def __init__(
        self,
        width: int,
        *,
        failures: Mapping[str, Exception] | None = None,
        short: bool = False,
    ) -> None:
        self.width = width
        self.failures = dict(failures or {})
        self.short = short
        self.calls: list[dict[str, object]] = []
        self.models: list[_Model] = []

    def __call__(self, path: str, *, device: str, normalize: bool) -> object:
        self.calls.append({"path": path, "device": device, "normalize": normalize})
        failure = self.failures.get(device)
        if failure is not None:
            raise failure
        model = _Model(self.width, short=self.short)
        self.models.append(model)
        return model


class _Events:
    """The events the provider hands over."""

    def __init__(self) -> None:
        self.records: list[dict[str, str]] = []

    def __call__(self, event: Mapping[str, str]) -> None:
        self.records.append(dict(event))

    def names(self) -> list[str]:
        return [record["event"] for record in self.records]

    def of(self, name: str) -> list[dict[str, str]]:
        return [record for record in self.records if record["event"] == name]


def _build(
    tmp_path: Path,
    *,
    config: SemanticConfig | None = None,
    cached: bool = True,
    download_failure: Exception | None = None,
    load_failures: Mapping[str, Exception] | None = None,
    cuda: bool = False,
    width: int | None = None,
    short: bool = False,
    files: Mapping[str, bytes] | None = None,
) -> tuple[LocalBGEM3Provider, _Downloader, _Factory, _Events]:
    """A provider with the fakes behind it."""
    settings = config or _config()
    snapshot = _snapshot(tmp_path / "snapshot", files)
    downloader = _Downloader(snapshot, cached=cached, failure=download_failure)
    factory = _Factory(
        width if width is not None else settings.embedding.dimensions,
        failures=load_failures,
        short=short,
    )
    events = _Events()
    provider = LocalBGEM3Provider(
        VectorSpace.of(
            provider=settings.embedding.provider,
            model=settings.embedding.model,
            model_version=settings.space_model_version,
            dimensions=settings.embedding.dimensions,
        ),
        model=settings.embedding.model,
        cache_dir=str(tmp_path / "cache"),
        device=settings.embedding.device,
        batch_size=settings.embedding.batch_size,
        normalize=settings.embedding.normalize,
        revision=settings.embedding.revision,
        downloader=downloader,
        model_factory=factory,
        cuda_available=lambda: cuda,
        events=events,
    )
    return provider, downloader, factory, events


# --- the configuration ------------------------------------------------------


def test_the_shipped_config_names_the_bge_m3_provider():
    config = load_semantic_config()
    assert config.embedding.provider == "bge-m3-local"
    assert config.embedding.model == "BAAI/bge-m3"
    assert config.embedding.dimensions == 1024
    assert config.embedding.device == "auto"
    assert config.embedding.batch_size == 16
    assert config.embedding.cache_dir == "data/models"


def test_the_provider_resolves_the_cache_dir_against_the_root(tmp_path):
    provider = build_provider(load_semantic_config(), root=tmp_path)
    diagnostics = provider.diagnostics()
    assert diagnostics["cache_dir"] == str(tmp_path / "data" / "models")
    assert diagnostics["provider"] == "bge-m3-local"
    assert diagnostics["model"] == "BAAI/bge-m3"


# --- the abstraction --------------------------------------------------------


def test_build_provider_returns_a_provider():
    provider = build_provider(load_semantic_config())
    assert isinstance(provider, EmbeddingProvider)
    assert isinstance(provider, LocalBGEM3Provider)


def test_another_provider_name_gives_another_class():
    bge = build_provider(_config())
    other = build_provider(_config(provider="sentence-transformers"))
    assert isinstance(other, EmbeddingProvider)
    assert isinstance(other, SentenceTransformerProvider)
    assert type(other) is not type(bge)


def test_an_unknown_provider_is_refused():
    with pytest.raises(UnknownProvider) as caught:
        build_provider(_config(provider="no-such-provider"))
    assert "no-such-provider" in str(caught.value)
    assert "bge-m3-local" in str(caught.value)


# --- the weights ------------------------------------------------------------


def test_a_cached_model_is_not_fetched_again(tmp_path):
    provider, downloader, _, events = _build(tmp_path)

    provider.embed(["one"])

    assert [call["local_only"] for call in downloader.calls] == [True]
    assert "model_cache_hit" in events.names()
    assert "model_download_started" not in events.names()


def test_a_missing_model_is_fetched_once(tmp_path):
    provider, downloader, factory, events = _build(tmp_path, cached=False)

    provider.embed(["one"])

    names = events.names()
    assert names.index("model_download_started") < names.index(
        "model_download_completed"
    )
    assert "model_cache_hit" not in names
    assert factory.calls[0]["path"] == str(tmp_path / "snapshot")


def test_a_failed_acquisition_is_reported(tmp_path):
    provider, _, factory, _ = _build(
        tmp_path, cached=False, download_failure=RuntimeError("the host is down")
    )

    with pytest.raises(ModelAcquisitionFailed) as caught:
        provider.embed(["one"])

    assert "BAAI/bge-m3" in str(caught.value)
    assert "RuntimeError" in str(caught.value)
    assert factory.calls == []


def test_a_missing_file_in_the_cache_is_refused(tmp_path):
    provider, downloader, _, _ = _build(tmp_path, files={"config.json": b"{}"})

    with pytest.raises(ModelCacheCorrupt) as caught:
        provider.embed(["one"])

    assert "model.safetensors" in str(caught.value)
    assert str(tmp_path / "snapshot") in str(caught.value)
    assert [call["local_only"] for call in downloader.calls] == [True]


def test_an_empty_file_in_the_cache_is_refused(tmp_path):
    provider, downloader, _, _ = _build(
        tmp_path, files={"config.json": b"", "model.safetensors": b"weights"}
    )

    with pytest.raises(ModelCacheCorrupt) as caught:
        provider.embed(["one"])

    assert "config.json" in str(caught.value)
    assert [call["local_only"] for call in downloader.calls] == [True]


def test_a_corrupt_cache_is_not_fetched_again(tmp_path):
    provider, downloader, _, events = _build(tmp_path, files={"config.json": b""})

    with pytest.raises(ModelCacheCorrupt):
        provider.embed(["one"])

    assert [call["local_only"] for call in downloader.calls] == [True]
    assert "model_download_started" not in events.names()


# --- the model identifier ---------------------------------------------------


@pytest.mark.parametrize(
    "model",
    [
        "https://example.com/a-model",
        "org/name/extra",
        "org/../name",
        "org/..",
        ".org/name",
    ],
)
def test_the_model_identifier_is_checked(tmp_path, model):
    downloader = _Downloader(tmp_path / "snapshot")

    with pytest.raises(ProviderError):
        LocalBGEM3Provider(
            VectorSpace.of(
                provider="bge-m3-local",
                model=model,
                model_version="1",
                dimensions=4,
            ),
            model=model,
            cache_dir=str(tmp_path / "cache"),
            downloader=downloader,
        )

    assert downloader.calls == []


# --- the device -------------------------------------------------------------


def test_a_failed_load_is_reported_and_refused(tmp_path):
    provider, _, _, events = _build(
        tmp_path, load_failures={"cpu": RuntimeError("the model is broken")}
    )

    with pytest.raises(ModelLoadFailed):
        provider.embed(["one"])

    assert "model_load_failed" in events.names()
    assert events.of("model_load_failed")[0]["error"] == "RuntimeError"


def test_an_auto_device_falls_back_to_the_cpu(tmp_path):
    provider, _, factory, events = _build(
        tmp_path, cuda=True, load_failures={"cuda": RuntimeError("no gpu")}
    )

    vectors = provider.embed(["one"])

    assert len(vectors) == 1
    assert provider.diagnostics()["device"] == "cpu"
    assert "device_fallback" in events.names()
    assert events.of("device_fallback")[0]["from_device"] == "cuda"
    assert events.of("device_fallback")[0]["to_device"] == "cpu"
    assert [call["device"] for call in factory.calls] == ["cuda", "cpu"]


def test_an_auto_device_picks_cuda_when_it_is_there(tmp_path):
    provider, _, factory, _ = _build(tmp_path, cuda=True)

    provider.embed(["one"])

    assert [call["device"] for call in factory.calls] == ["cuda"]
    assert provider.diagnostics()["device"] == "cuda"


def test_a_required_cuda_that_is_missing_is_refused(tmp_path):
    provider, _, factory, _ = _build(
        tmp_path, cuda=False, config=_config(device="cuda")
    )

    with pytest.raises(DeviceUnavailable):
        provider.embed(["one"])

    assert factory.calls == []


def test_a_required_cuda_that_fails_to_load_is_refused(tmp_path):
    provider, _, factory, events = _build(
        tmp_path,
        cuda=True,
        config=_config(device="cuda"),
        load_failures={"cuda": RuntimeError("no gpu")},
    )

    with pytest.raises(DeviceUnavailable):
        provider.embed(["one"])

    assert [call["device"] for call in factory.calls] == ["cuda"]
    assert "device_fallback" not in events.names()


# --- the embedding ----------------------------------------------------------


def test_the_texts_are_embedded_in_batches(tmp_path):
    provider, _, factory, _ = _build(tmp_path, config=_config(batch_size=8))

    vectors = provider.embed([f"text {index}" for index in range(25)])

    assert len(vectors) == 25
    model = factory.models[0]
    assert [len(batch) for batch in model.batches] == [8, 8, 8, 1]


def test_the_model_is_loaded_once(tmp_path):
    provider, _, factory, _ = _build(tmp_path)

    provider.embed(["one"])
    provider.embed(["two"])

    assert len(factory.calls) == 1


def test_a_released_provider_loads_the_model_again(tmp_path):
    provider, _, factory, _ = _build(tmp_path)

    provider.embed(["one"])
    provider.release()

    assert provider.diagnostics()["loaded"] is False
    assert provider.diagnostics()["device"] == ""

    provider.embed(["two"])

    assert provider.diagnostics()["loaded"] is True
    assert len(factory.calls) == 2


def test_the_same_text_gives_the_same_vector(tmp_path):
    provider, _, factory, _ = _build(tmp_path)

    first = provider.embed(["the same text"])[0]
    second = provider.embed(["the same text"])[0]

    assert first == second
    assert len(factory.calls) == 1


def test_a_vector_of_the_wrong_width_is_refused(tmp_path):
    provider, _, _, _ = _build(tmp_path, width=8)

    with pytest.raises(MixedVectorSpaceError):
        embed_all(provider, ["one"])


def test_a_chunk_of_the_wrong_count_is_refused(tmp_path):
    provider, _, _, _ = _build(tmp_path, short=True)

    with pytest.raises(ProviderError):
        provider.embed(["one", "two"])


# --- the vector space -------------------------------------------------------


def test_a_pinned_revision_changes_the_space():
    unpinned = _config()
    pinned = _config(revision="9f8a7b")

    assert unpinned.space_model_version == "1"
    assert pinned.space_model_version == "1@9f8a7b"
    assert (
        build_provider(unpinned).space.space_id
        != build_provider(pinned).space.space_id
    )
    assert build_provider(pinned).space.model_version == "1@9f8a7b"


def test_another_model_changes_the_space():
    first = build_provider(_config()).space
    second = build_provider(_config(model="BAAI/bge-small")).space
    assert first.space_id != second.space_id


# --- the diagnostics --------------------------------------------------------


def test_the_diagnostics_carry_the_provider_metadata(tmp_path):
    provider, _, _, _ = _build(tmp_path)
    diagnostics = provider.diagnostics()

    assert diagnostics["provider"] == "bge-m3-local"
    assert diagnostics["model"] == "BAAI/bge-m3"
    assert diagnostics["revision"] == ""
    assert diagnostics["revision_pinned"] is False
    assert diagnostics["cache_dir"] == str(tmp_path / "cache")
    assert diagnostics["device"] == ""
    assert diagnostics["device_requested"] == "auto"
    assert diagnostics["cuda_available"] is False
    assert diagnostics["dimensions"] == 1024
    assert diagnostics["loaded"] is False
    assert diagnostics["space_id"]
    assert diagnostics["normalize"] is True
    assert diagnostics["batch_size"] == 16


def test_the_diagnostics_report_a_pinned_revision(tmp_path):
    provider, _, _, _ = _build(tmp_path, config=_config(revision="9f8a7b"))
    diagnostics = provider.diagnostics()
    assert diagnostics["revision"] == "9f8a7b"
    assert diagnostics["revision_pinned"] is True


def test_the_diagnostics_never_download(tmp_path):
    provider, downloader, factory, _ = _build(tmp_path, cached=False)

    diagnostics = provider.diagnostics()

    assert diagnostics["cached"] is False
    assert [call["local_only"] for call in downloader.calls] == [True]
    assert factory.calls == []


# --- the real model ---------------------------------------------------------


class _MappingModel:
    """A model that answers with a mapping of the kinds of vector it has."""

    def __init__(self) -> None:
        self.batches: list[list[str]] = []

    def encode(self, texts, batch_size=None):
        texts = list(texts)
        self.batches.append(texts)
        return {
            "dense_vecs": [_vector(text, 3) for text in texts],
            "lexical_weights": None,
            "colbert_vecs": None,
        }


def test_the_dense_adapter_unwraps_the_mapping():
    model = _MappingModel()
    encoder = DenseEncoder(model)

    vectors = encoder.encode(["one", "two"], batch_size=2)

    assert len(vectors) == 2
    assert vectors[0] == _vector("one", 3)
    assert model.batches == [["one", "two"]]


def test_the_dense_adapter_refuses_a_model_without_dense_vectors():
    class _Empty:
        def encode(self, texts, batch_size=None):
            return {"dense_vecs": None}

    with pytest.raises(ProviderError):
        DenseEncoder(_Empty()).encode(["one"])


@pytest.mark.network
def test_the_real_bge_m3_embeds_a_sentence(tmp_path) -> None:
    """The real model, when the package that runs it is installed.

    This is the only test in the file that could reach the model
    distribution, and it is skipped when the package is not installed.
    """
    pytest.importorskip("FlagEmbedding")

    provider = LocalBGEM3Provider(
        VectorSpace.of(
            provider="bge-m3-local",
            model="BAAI/bge-m3",
            model_version="1",
            dimensions=1024,
        ),
        model="BAAI/bge-m3",
        cache_dir=str(tmp_path / "models"),
    )

    vectors = provider.embed(["a product hunter needs one place for the evidence"])

    assert len(vectors) == 1
    assert len(vectors[0]) == 1024

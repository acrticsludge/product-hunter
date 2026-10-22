from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import TYPE_CHECKING

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from product_hunter.llm.routing import ModelRole, RouteCandidate

if TYPE_CHECKING:
    # The SubQ module is optional: importing it here would load it for
    # every caller of the config, so the type is imported for the checker
    # only and the loader imports it when it is asked for.
    from product_hunter.llm.subq import SubQConfig

CONFIG_NAME = Path("config") / "extraction.yaml"
ROUTING_CONFIG_NAME = Path("config") / "model_routing.yaml"
DECISION_ROUTING_CONFIG_NAME = Path("config") / "decision_routing.yaml"
CLUSTERING_CONFIG_NAME = Path("config") / "clustering.yaml"
ENV_FILE = ".env"
DEFAULT_DB_NAME = Path("db") / "product_hunter.db"

# Config field -> PH_ environment override. Empty values are ignored.
_ENV_OVERRIDES = {
    "provider": "PH_PROVIDER",
    "provider_id": "PH_PROVIDER_ID",
    "model_id": "PH_MODEL_ID",
    "extractor_version": "PH_EXTRACTOR_VERSION",
    "prompt_version": "PH_PROMPT_VERSION",
    "normalization_version": "PH_NORMALIZATION_VERSION",
    "max_input_chars": "PH_MAX_INPUT_CHARS",
    "timeout_seconds": "PH_TIMEOUT_SECONDS",
}
_INT_LIMITS = ("max_input_chars", "timeout_seconds")

# The SubQ settings, and the environment name that overrides each of them.
# The key is one of the three names SubQ documents, so the same key works
# for the loop tooling and for this repository.
_SUBQ_ENV_OVERRIDES = {
    "api_key": "SUBQ_API_KEY",
    "base_url": "SUBQ_BASE_URL",
    "model": "SUBQ_MODEL",
    "timeout_seconds": "PH_SUBQ_TIMEOUT_SECONDS",
    "max_retries": "PH_SUBQ_MAX_RETRIES",
    "backoff_base_seconds": "PH_SUBQ_BACKOFF_SECONDS",
    "max_calls": "PH_SUBQ_MAX_CALLS",
    "max_input_tokens": "PH_SUBQ_MAX_INPUT_TOKENS",
    "max_usd": "PH_SUBQ_MAX_USD",
    "max_batch_items": "PH_SUBQ_MAX_BATCH_ITEMS",
    "usd_per_mtok_input": "PH_SUBQ_USD_PER_MTOK_INPUT",
    "failure_threshold": "PH_SUBQ_FAILURE_THRESHOLD",
    "cooldown_seconds": "PH_SUBQ_COOLDOWN_SECONDS",
    "max_cache_entries": "PH_SUBQ_MAX_CACHE_ENTRIES",
}
_SUBQ_TEXT_FIELDS = ("api_key", "base_url", "model")
_SUBQ_INT_FIELDS = (
    "max_retries",
    "max_calls",
    "max_input_tokens",
    "max_batch_items",
    "failure_threshold",
    "max_cache_entries",
)


class Limits(BaseModel):
    max_documents: int = Field(gt=0)
    max_calls: int = Field(gt=0)
    max_input_chars: int = Field(gt=0)
    timeout_seconds: int = Field(gt=0)
    min_quote_chars: int = Field(gt=0)
    max_quote_chars: int = Field(gt=0)
    chunk_chars: int = Field(gt=0)
    chunk_overlap_chars: int = Field(ge=0)
    max_candidates_per_chunk: int = Field(ge=1, le=10)

    @model_validator(mode="after")
    def _check_invariants(self) -> Limits:
        if self.min_quote_chars >= self.max_quote_chars:
            raise ValueError(
                f"min_quote_chars ({self.min_quote_chars}) must be less than "
                f"max_quote_chars ({self.max_quote_chars})"
            )
        if self.chunk_overlap_chars >= self.chunk_chars:
            raise ValueError(
                f"chunk_overlap_chars ({self.chunk_overlap_chars}) must be less than "
                f"chunk_chars ({self.chunk_chars})"
            )
        return self


class ExtractionConfig(BaseModel):
    version: int = Field(gt=0)
    provider: str = Field(min_length=1)
    provider_id: str = Field(min_length=1)
    model_id: str = Field(min_length=1)
    extractor_version: str = Field(min_length=1)
    prompt_version: str = Field(min_length=1)
    normalization_version: str = Field(min_length=1)
    limits: Limits


class CatalogSettings(BaseModel):
    """How the catalogue is discovered and how long it stays fresh."""

    refresh_interval_seconds: int = Field(gt=0)
    discovery_timeout_seconds: int = Field(gt=0)


class RoutingConfig(BaseModel):
    """The role bindings, written as patterns so a new model needs no edit."""

    version: int = Field(gt=0)
    catalog: CatalogSettings
    roles: dict[ModelRole, list[RouteCandidate]]

    @model_validator(mode="after")
    def _every_role_is_bound(self) -> RoutingConfig:
        """Refuse a file that leaves a role unbound.

        A role the operator deleted from the file would resolve to an empty
        chain and quietly run on the default model. That is a config error,
        not a silent downgrade, so the file is refused by name.
        """
        missing = sorted(role.value for role in ModelRole if role not in self.roles)
        if missing:
            raise ValueError(
                "the routing config must bind every role; missing: "
                + ", ".join(missing)
            )
        return self

    @property
    def refresh_interval_seconds(self) -> int:
        """How long a catalogue stays fresh."""
        return self.catalog.refresh_interval_seconds

    @property
    def discovery_timeout_seconds(self) -> int:
        """How long a discovery call may take."""
        return self.catalog.discovery_timeout_seconds

    @property
    def config_version(self) -> str:
        """The routing version plus a digest of the bindings, so a change shows."""
        lines = sorted(
            f"{role.value}|{candidate.kind}|{candidate.value}"
            for role, candidates in self.roles.items()
            for candidate in candidates
        )
        digest = hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()[:12]
        return f"routing-v{self.version}-{digest}"


class DecisionProviderConfig(BaseModel):
    """One provider of the decision chain.

    The order of the list in the file is the order of preference. An
    operator who does not want a provider removes it from the list or sets
    ``enabled`` to false, and the next provider takes the work.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    enabled: bool = True
    model_role: ModelRole | None = None


class DecisionRoutingConfig(BaseModel):
    """The chain of decision providers."""

    model_config = ConfigDict(extra="forbid")

    version: int = Field(gt=0)
    providers: list[DecisionProviderConfig]

    @model_validator(mode="after")
    def _check_chain(self) -> DecisionRoutingConfig:
        """Refuse a chain that names a provider twice or leaves one unbound.

        A provider named twice would be asked twice, and the second answer
        would be paid for and thrown away. The text provider resolves a
        model role, and a chain that names it without a role would leave the
        fallback without a model.
        """
        names = [provider.name for provider in self.providers]
        if len(set(names)) != len(names):
            raise ValueError("the decision routing config names a provider twice")
        for provider in self.providers:
            if provider.name == "text" and provider.model_role is None:
                raise ValueError("the text decision provider needs a model_role")
        return self


class ClusteringError(Exception):
    """Base for clustering failures; every subclass carries a reason code.

    The base lives here because the clustering package imports this module.
    A base defined in the package would close a cycle: the loader raises it,
    and the loader is here.
    """

    reason_code = "clustering_error"

    def __init__(self, detail: str = "") -> None:
        self.detail = detail
        super().__init__(detail or self.reason_code)


class ClusteringConfigError(ClusteringError, ValueError):
    """The clustering config file cannot be used."""

    reason_code = "clustering_config_invalid"


class EmbeddingConfig(BaseModel):
    """The embedding model the wave embeds with."""

    model_config = ConfigDict(extra="forbid")

    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    model_version: str = Field(min_length=1)
    dimensions: int = Field(gt=0)


class SimilarityConfig(BaseModel):
    """The bands a similarity score is read against."""

    model_config = ConfigDict(extra="forbid")

    merge_threshold: float = Field(ge=0, le=1)
    ambiguous_threshold: float = Field(ge=0, le=1)
    domain_mismatch_penalty: float = Field(ge=0, le=1)
    max_candidates: int = Field(ge=1)
    min_neighbors_checked: int = Field(ge=1)


class AdjudicationConfig(BaseModel):
    """The adjudicator the wave calls when a score lands between the bands."""

    model_config = ConfigDict(extra="forbid")

    role: str = Field(min_length=1)
    enabled: bool = True
    max_calls: int = Field(ge=0)


class ClusteringConfig(BaseModel):
    """The operator-tunable values of the clustering wave.

    No model and no threshold is written in the code. The model comes from
    the embedding block, the thresholds from the similarity block, and the
    adjudication role names the routing entry the caller wires.
    """

    model_config = ConfigDict(extra="forbid")

    version: int = Field(gt=0)
    algorithm_version: str = Field(min_length=1)
    prompt_version: str = Field(min_length=1)
    embedding: EmbeddingConfig
    similarity: SimilarityConfig
    adjudication: AdjudicationConfig

    @model_validator(mode="after")
    def _check_bands(self) -> ClusteringConfig:
        """Refuse bands that cannot be read.

        The ambiguous band sits between the merge band and the noise. A file
        that puts the two thresholds on top of each other has no ambiguous
        band, and a file that puts the ambiguous one above the merge one
        would send every pair to the adjudicator.
        """
        if self.similarity.ambiguous_threshold >= self.similarity.merge_threshold:
            raise ValueError(
                "ambiguous_threshold must be below merge_threshold, got "
                f"{self.similarity.ambiguous_threshold} and "
                f"{self.similarity.merge_threshold}"
            )
        return self

    @property
    def config_version(self) -> str:
        """The version plus a digest of the values, so a change shows."""
        embedding = self.embedding
        similarity = self.similarity
        adjudication = self.adjudication
        lines = sorted(
            [
                f"algorithm_version|{self.algorithm_version}",
                f"prompt_version|{self.prompt_version}",
                "embedding|"
                f"{embedding.provider}|{embedding.model}|"
                f"{embedding.model_version}|{embedding.dimensions}",
                "similarity|"
                f"{similarity.merge_threshold}|{similarity.ambiguous_threshold}|"
                f"{similarity.domain_mismatch_penalty}|{similarity.max_candidates}|"
                f"{similarity.min_neighbors_checked}",
                f"adjudication|{adjudication.role}|{adjudication.enabled}|"
                f"{adjudication.max_calls}",
            ]
        )
        digest = hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()[:12]
        return f"clustering-v{self.version}-{digest}"


def repo_root() -> Path:
    """Return the repository root by walking up from this file to pyproject.toml."""
    for parent in Path(__file__).resolve().parents:
        if (parent / "pyproject.toml").is_file():
            return parent
    raise RuntimeError("repository root not found: no pyproject.toml above the package")


def database_path(root: Path | None = None) -> Path:
    """The SQLite path: ``PH_DB_PATH`` when set, otherwise ``db/product_hunter.db``."""
    base = Path(root) if root is not None else repo_root()
    load_dotenv(base / ENV_FILE, override=False)
    raw = os.environ.get("PH_DB_PATH")
    if raw:
        path = Path(raw).expanduser()
        return path if path.is_absolute() else base / path
    return base / DEFAULT_DB_NAME


def load_config(path: Path | None = None) -> ExtractionConfig:
    """Load config/extraction.yaml, then apply PH_ environment overrides."""
    config_path = path if path is not None else repo_root() / CONFIG_NAME
    load_dotenv(repo_root() / ENV_FILE, override=False)

    raw = _load_yaml(config_path)
    return ExtractionConfig.model_validate(_apply_env(raw))


def _load_yaml(path: Path) -> dict:
    """The parsed YAML file, or a ValueError naming the file.

    A file the parser cannot read is a bad config, and a bad config reaches
    the caller as a ValueError, never as a parser traceback.
    """
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ValueError(f"{path}: {exc}") from exc


def _apply_env(raw: dict) -> dict:
    data = dict(raw)
    limits = dict(data.get("limits") or {})
    for field_name, env_name in _ENV_OVERRIDES.items():
        value = os.environ.get(env_name)
        if not value:
            continue
        if field_name in _INT_LIMITS:
            limits[field_name] = int(value)
        else:
            data[field_name] = value
    if limits:
        data["limits"] = limits
    return data


def load_routing_config(path: Path | None = None) -> RoutingConfig:
    """Load config/model_routing.yaml.

    The file names no model: every candidate is a pattern, so a model that
    appears later is picked up with no edit. A file that names an unknown role
    or an unusable candidate is refused.
    """
    config_path = path if path is not None else repo_root() / ROUTING_CONFIG_NAME
    raw = _load_yaml(config_path)
    return RoutingConfig.model_validate(raw)


def load_decision_routing_config(path: Path | None = None) -> DecisionRoutingConfig:
    """Load config/decision_routing.yaml, the chain of decision providers."""
    config_path = (
        path if path is not None else repo_root() / DECISION_ROUTING_CONFIG_NAME
    )
    raw = _load_yaml(config_path)
    return DecisionRoutingConfig.model_validate(raw)


def load_subq_config() -> SubQConfig:
    """Load the SubQ settings from the environment.

    The key is read from the environment and nowhere else, and it stays out
    of every record this repository writes. An empty value is ignored, so
    the built-in default applies.
    """
    # Imported here so the pipeline never loads the SubQ module unless it
    # asks for this config.
    from product_hunter.llm.subq import SubQConfig

    load_dotenv(repo_root() / ENV_FILE, override=False)
    values: dict[str, object] = {}
    for field_name, env_name in _SUBQ_ENV_OVERRIDES.items():
        raw = os.environ.get(env_name)
        if not raw:
            continue
        values[field_name] = _subq_value(field_name, raw)
    return SubQConfig.model_validate(values)


def _subq_value(field_name: str, raw: str) -> object:
    """The value of one environment override, in the type the field takes."""
    if field_name in _SUBQ_TEXT_FIELDS:
        return raw
    if field_name in _SUBQ_INT_FIELDS:
        return int(raw)
    return float(raw)


def load_clustering_config(path: Path | None = None) -> ClusteringConfig:
    """Load config/clustering.yaml and refuse a file that cannot be used.

    A missing file, a file the parser cannot read and a file whose values
    do not hold are all the same refusal to the caller: a bad config never
    reaches the engine as a half-read config.
    """
    config_path = path if path is not None else repo_root() / CLUSTERING_CONFIG_NAME
    try:
        raw = _load_yaml(config_path)
    except (OSError, ValueError) as exc:
        raise ClusteringConfigError(
            f"the clustering config at {config_path} cannot be read: {exc}"
        ) from exc
    try:
        return ClusteringConfig.model_validate(raw)
    except ValidationError as exc:
        raise ClusteringConfigError(
            f"the clustering config at {config_path} is not usable: "
            f"{_first_config_error(exc)}"
        ) from exc


def _first_config_error(exc: ValidationError) -> str:
    """The first refusal of a config file, naming the field it names."""
    errors = exc.errors()
    if not errors:
        return "the file does not match the clustering config schema"
    first = errors[0]
    location = ".".join(str(part) for part in first.get("loc", ()))
    return f"{location}: {first.get('msg', 'invalid value')}"

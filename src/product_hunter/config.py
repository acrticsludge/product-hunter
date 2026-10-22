from __future__ import annotations

import hashlib
import os
from pathlib import Path

import yaml
from dotenv import load_dotenv
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from product_hunter.llm.routing import ModelRole, RouteCandidate

CONFIG_NAME = Path("config") / "extraction.yaml"
ROUTING_CONFIG_NAME = Path("config") / "model_routing.yaml"
CLUSTERING_CONFIG_NAME = Path("config") / "clustering.yaml"
AUTONOMY_CONFIG_NAME = Path("config") / "autonomy.yaml"
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


class PriorityAging(BaseModel):
    """How fast a queued job's priority grows while it waits.

    The queue ages a job that has been due and unclaimed, so a low band
    cannot starve behind a steady stream of high band work.
    """

    model_config = ConfigDict(extra="forbid")

    interval_seconds: int = Field(ge=0)
    max_boost: int = Field(ge=0)


class OperationBudgetLimits(BaseModel):
    """The budget of one operation, in the scopes the contract names.

    The scopes are the operation itself, a stage and a source. A dimension the
    budget model does not know is refused by name, because a limit that is
    never checked is a limit nobody has.
    """

    model_config = ConfigDict(extra="forbid")

    per_operation: dict[str, float] = Field(default_factory=dict)
    per_stage: dict[str, dict[str, float]] = Field(default_factory=dict)
    per_source: dict[str, dict[str, float]] = Field(default_factory=dict)

    @field_validator("per_operation")
    @classmethod
    def _operation_dimensions(cls, value: dict[str, float]) -> dict[str, float]:
        for name in value:
            _check_dimension(name)
        return value

    @field_validator("per_stage")
    @classmethod
    def _stage_dimensions(
        cls, value: dict[str, dict[str, float]]
    ) -> dict[str, dict[str, float]]:
        for limits in value.values():
            for name in limits:
                _check_dimension(name)
        return value

    @field_validator("per_source")
    @classmethod
    def _source_dimensions(
        cls, value: dict[str, dict[str, float]]
    ) -> dict[str, dict[str, float]]:
        from product_hunter.research.budget import BudgetDimension

        for limits in value.values():
            for name in limits:
                _check_dimension(name)
                if name != BudgetDimension.source_requests_per_source.value:
                    # The per source scope of the budget model binds one
                    # dimension only, so a limit on another one there would
                    # never be checked.
                    raise ValueError(
                        f"the per source limit names {name!r}; the per source "
                        "scope binds "
                        f"{BudgetDimension.source_requests_per_source.value!r} only"
                    )
        return value


class SourcePacingSettings(BaseModel):
    """The interval each source may be contacted at.

    A source that is not due is put back to the queue with a later time, so
    the interval is what keeps a source from being fetched too often.
    """

    model_config = ConfigDict(extra="forbid")

    default_min_interval_seconds: int = Field(default=0, ge=0)
    per_source: dict[str, int] = Field(default_factory=dict)


class AutonomyConfig(BaseModel):
    """The operation layer's settings.

    The timings, the concurrency limit, the priority aging, the budget and the
    source pacing all live in the file, so an operator changes how the
    operation runs without touching the code.
    """

    model_config = ConfigDict(extra="forbid")

    version: int = Field(gt=0)
    poll_seconds: float = Field(gt=0)
    lease_seconds: int = Field(gt=0)
    heartbeat_seconds: float = Field(gt=0)
    scheduler_interval_seconds: float = Field(gt=0)
    recovery_interval_seconds: float = Field(gt=0)
    max_concurrent_jobs: int = Field(gt=0)
    # How many cycles may fault in a row before the operation ends with a
    # permanent failure. One fault is a hiccup; a run of them is a broken
    # operation, and it ends where an operator can read it.
    max_consecutive_errors: int = Field(default=5, ge=1)
    unhandled_stage_delay_seconds: int = Field(default=300, ge=0)
    priority_aging: PriorityAging
    budget: OperationBudgetLimits
    source_pacing: SourcePacingSettings


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
            f"{_first_config_error(exc, 'clustering config')}"
        ) from exc


def load_autonomy_config(path: Path | None = None) -> AutonomyConfig:
    """Load config/autonomy.yaml and refuse a file that cannot be used.

    A missing file, a file the parser cannot read and a file whose values do
    not hold are one refusal to the caller: a bad config never reaches the
    engine as a half-read config.
    """
    config_path = path if path is not None else repo_root() / AUTONOMY_CONFIG_NAME
    try:
        raw = _load_yaml(config_path)
    except (OSError, ValueError) as exc:
        raise ValueError(
            f"the autonomy config at {config_path} cannot be read: {exc}"
        ) from exc
    try:
        return AutonomyConfig.model_validate(raw)
    except ValidationError as exc:
        raise ValueError(
            f"the autonomy config at {config_path} is not usable: "
            f"{_first_config_error(exc, 'autonomy config')}"
        ) from exc


def _check_dimension(name: str) -> None:
    """A budget dimension the model does not know is refused by name.

    The budget model is imported here and not at the top of the file: the
    research package imports this module, so a module level import would be a
    cycle.
    """
    from product_hunter.research.budget import BUDGET_DIMENSION_NAMES

    if name not in BUDGET_DIMENSION_NAMES:
        raise ValueError(f"unknown budget dimension {name!r}")


def _first_config_error(exc: ValidationError, label: str) -> str:
    """The first refusal of a config file, naming the field it names.

    The label says which schema the file was measured against, so a reader
    knows which file to fix.
    """
    errors = exc.errors()
    if not errors:
        return f"the file does not match the {label} schema"
    first = errors[0]
    location = ".".join(str(part) for part in first.get("loc", ()))
    return f"{location}: {first.get('msg', 'invalid value')}"

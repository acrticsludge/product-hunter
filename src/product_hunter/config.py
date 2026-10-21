from __future__ import annotations

import os
from pathlib import Path

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field, model_validator

CONFIG_NAME = Path("config") / "extraction.yaml"
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

    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    return ExtractionConfig.model_validate(_apply_env(raw))


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

from __future__ import annotations

import json
import shutil
import subprocess
import time
from typing import Any, Callable

from pydantic import BaseModel, ConfigDict, TypeAdapter, ValidationError

from product_hunter.config import ExtractionConfig
from product_hunter.llm.base import (
    GenerationRequest,
    GenerationResponse,
    InputTooLarge,
    ProviderError,
    ProviderUnavailable,
)

Runner = Callable[..., subprocess.CompletedProcess[str]]

# Windows CreateProcess caps the command line near 32k chars; keep the JSON payload well under it.
MAX_COMMAND_LINE_CHARS = 30_000


class GenerateData(BaseModel):
    model_config = ConfigDict(strict=True)

    text: str


class GenerateEnvelope(BaseModel):
    model_config = ConfigDict(strict=True)

    data: GenerateData


class ModelEntry(BaseModel):
    model_config = ConfigDict(strict=True)

    modelID: str
    providerID: str
    status: str | None = None
    enabled: bool | None = None


class ModelEnvelope(BaseModel):
    model_config = ConfigDict(strict=True)

    data: list[ModelEntry]


# /api/model returns {"data": [...]} on the current server; accept a bare list too.
_MODELS = TypeAdapter(ModelEnvelope | list[ModelEntry])


class ServerInfo(BaseModel):
    model_config = ConfigDict(strict=True)

    version: str


def _resolve_cli() -> str:
    """Prefer the .cmd shim; never run a PowerShell shim."""
    for candidate in ("opencode.cmd", "opencode"):
        path = shutil.which(candidate)
        if path is None:
            continue
        if path.lower().endswith(".ps1"):
            raise ProviderError(
                f"refusing to run OpenCode through a PowerShell shim: {path}. "
                "Install the .cmd shim or put it first on PATH."
            )
        return path
    raise ProviderUnavailable("opencode CLI not found on PATH")


class OpenCodeGenerateProvider:
    """Phase 1 text generator over the OpenCode V2 one-shot route."""

    name = "opencode_generate"

    def __init__(self, config: ExtractionConfig, runner: Runner = subprocess.run) -> None:
        self._config = config
        self._runner = runner
        self._cli: str | None = None

    @property
    def cli(self) -> str:
        if self._cli is None:
            self._cli = _resolve_cli()
        return self._cli

    def _run(self, argv: list[str]) -> subprocess.CompletedProcess[str]:
        return self._runner(
            argv,
            capture_output=True,
            text=True,
            shell=False,
            timeout=self._config.limits.timeout_seconds,
        )

    def _call(self, argv: list[str]) -> str:
        try:
            proc = self._run(argv)
        except subprocess.TimeoutExpired as exc:
            raise ProviderError(
                f"opencode api timed out after {self._config.limits.timeout_seconds}s"
            ) from exc
        except OSError as exc:
            raise ProviderUnavailable(f"could not run opencode CLI: {exc}") from exc
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "").strip().splitlines()
            message = detail[-1] if detail else f"exit code {proc.returncode}"
            raise ProviderError(f"opencode api failed: {message}")
        return proc.stdout

    @staticmethod
    def _parse(raw: str, validator: Callable[[str], Any]) -> Any:
        try:
            return validator(raw)
        except ValidationError as exc:
            first = exc.errors()[0]["msg"] if exc.errors() else "invalid response"
            raise ProviderError(f"invalid OpenCode JSON response: {first}") from exc

    def _get_info(self) -> ServerInfo:
        raw = self._call([self.cli, "api", "get", "/api/info"])
        return self._parse(raw, ServerInfo.model_validate_json)

    def _get_models(self) -> list[ModelEntry]:
        raw = self._call([self.cli, "api", "get", "/api/model"])
        parsed = self._parse(raw, _MODELS.validate_json)
        return parsed.data if isinstance(parsed, ModelEnvelope) else parsed

    def check(self) -> dict[str, Any]:
        """Verify the server reports the configured provider and model as usable."""
        info = self._get_info()
        models = self._get_models()
        match = next(
            (
                entry
                for entry in models
                if entry.providerID == self._config.provider_id
                and entry.modelID == self._config.model_id
            ),
            None,
        )
        if match is None:
            raise ProviderUnavailable(
                f"model {self._config.provider_id}/{self._config.model_id} is not in the registry"
            )
        if match.status != "active":
            raise ProviderUnavailable(f"model status is {match.status!r}, expected 'active'")
        if match.enabled is not True:
            raise ProviderUnavailable("model is disabled")
        return {
            "version": info.version,
            "providerID": match.providerID,
            "modelID": match.modelID,
            "status": match.status,
            "enabled": match.enabled,
        }

    def generate(self, request: GenerationRequest) -> GenerationResponse:
        input_chars = len(request.prompt)
        if input_chars > self._config.limits.max_input_chars:
            raise InputTooLarge(
                f"prompt is {input_chars} chars, limit is "
                f"{self._config.limits.max_input_chars}"
            )
        body = {
            "prompt": request.prompt,
            "model": {
                "providerID": self._config.provider_id,
                "id": self._config.model_id,
            },
        }
        serialized = json.dumps(body, ensure_ascii=False)
        if len(serialized) > MAX_COMMAND_LINE_CHARS:
            raise InputTooLarge(
                f"request body is {len(serialized)} chars, command-line limit is "
                f"{MAX_COMMAND_LINE_CHARS}"
            )
        argv = [
            self.cli,
            "api",
            "post",
            "/api/experimental/generate",
            "--data",
            serialized,
        ]
        started = time.monotonic()
        raw = self._call(argv)
        latency_ms = int(round((time.monotonic() - started) * 1000))
        envelope = self._parse(raw, GenerateEnvelope.model_validate_json)
        text = envelope.data.text.strip()
        if not text:
            raise ProviderError("OpenCode returned empty text")
        return GenerationResponse(
            text=text,
            provider=self._config.provider_id,
            model=self._config.model_id,
            extractor_version=self._config.extractor_version,
            input_chars=input_chars,
            latency_ms=latency_ms,
        )

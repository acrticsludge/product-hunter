from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from pydantic import BaseModel, ConfigDict, TypeAdapter, ValidationError

from product_hunter.llm.base import (
    GenerationRequest,
    GenerationResponse,
    InputTooLarge,
    ProviderError,
    ProviderUnavailable,
)

if TYPE_CHECKING:
    from product_hunter.config import ExtractionConfig

Runner = Callable[..., subprocess.CompletedProcess[str]]

# Windows CreateProcess caps the command line near 32k chars; keep the JSON payload well under it.
MAX_COMMAND_LINE_CHARS = 30_000

# The real binary inside the global npm install. The .cmd shim next to it is
# run by cmd.exe, which re-parses the command line and mangles a prompt.
_NPM_CLI_RELATIVE = Path("node_modules") / "@opencode" / "cli" / "bin" / "opencode.exe"

# A shim cmd.exe re-parses. Good for a plain `api get`, never for a prompt.
_SHIM_SUFFIXES = (".cmd", ".bat")


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


def _nested_npm_exe() -> str | None:
    """The real opencode.exe inside the global npm install, when it is there."""
    roots: list[Path] = []
    appdata = os.environ.get("APPDATA")
    if appdata:
        roots.append(Path(appdata) / "npm")
    for shim in ("opencode.cmd", "opencode"):
        found = shutil.which(shim)
        if found:
            roots.append(Path(found).parent)
    for root in roots:
        candidate = root / _NPM_CLI_RELATIVE
        if candidate.is_file():
            return str(candidate)
    return None


def _cli_candidates() -> Iterator[str]:
    """Every OpenCode CLI path to try, best first."""
    on_path = shutil.which("opencode.exe")
    if on_path:
        yield on_path
    nested = _nested_npm_exe()
    if nested:
        yield nested
    fallback = shutil.which("opencode")
    if fallback:
        yield fallback


def _resolve_cli() -> str:
    """Resolve the OpenCode CLI, preferring the real .exe over a cmd shim.

    A .cmd or .bat shim is run by cmd.exe, which re-parses the command line,
    so it is only good for a plain `api get` call. generate() refuses one.
    """
    for candidate in _cli_candidates():
        if candidate.lower().endswith(".ps1"):
            raise ProviderError(
                f"refusing to run OpenCode through a PowerShell shim: {candidate}. "
                "Install the .exe or put it first on PATH."
            )
        return candidate
    raise ProviderUnavailable("opencode CLI not found on PATH")


def run_opencode_cli(
    argv: list[str],
    *,
    timeout_seconds: int,
    runner: Runner = subprocess.run,
    label: str = "opencode api",
) -> str:
    """Run the OpenCode CLI and return its stdout.

    The command is always an argv list, never a command string, and the shell
    is never used. A timeout, a missing binary, and a non-zero exit are each
    refused by name.
    """
    try:
        proc = runner(
            argv,
            capture_output=True,
            text=True,
            shell=False,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        raise ProviderError(
            f"{label} timed out after {timeout_seconds}s"
        ) from exc
    except OSError as exc:
        raise ProviderUnavailable(f"could not run opencode CLI: {exc}") from exc
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip().splitlines()
        message = detail[-1] if detail else f"exit code {proc.returncode}"
        raise ProviderError(f"{label} failed: {message}")
    return proc.stdout


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

    def _call(self, argv: list[str]) -> str:
        return run_opencode_cli(
            argv,
            timeout_seconds=self._config.limits.timeout_seconds,
            runner=self._runner,
        )

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
        cli = self.cli
        if cli.lower().endswith(_SHIM_SUFFIXES):
            raise ProviderUnavailable(
                f"refusing to send a prompt through the {Path(cli).name} shim: "
                "cmd.exe re-parses the command line. Install the opencode.exe "
                "binary and put it on PATH."
            )
        argv = [
            cli,
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

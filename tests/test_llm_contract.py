from __future__ import annotations

import json
import subprocess

import pytest

from product_hunter.cli import doctor
from product_hunter.config import load_config
from product_hunter.llm.base import (
    GenerationRequest,
    GenerationResponse,
    InputTooLarge,
    ProviderError,
)
from product_hunter.llm.opencode import OpenCodeGenerateProvider


@pytest.fixture
def config():
    return load_config()


class FakeRunner:
    """Records every call and replays queued CompletedProcess results."""

    def __init__(self, *results):
        self._results = list(results)
        self.calls: list[tuple[list[str], dict]] = []

    def __call__(self, argv, **kwargs):
        self.calls.append((list(argv), dict(kwargs)))
        if not self._results:
            raise AssertionError("runner invoked more times than scripted")
        result = self._results.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result


def completed(stdout: str, returncode: int = 0, stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


def registry(*models: dict) -> str:
    return json.dumps({"location": {"directory": "."}, "data": list(models)})


MODEL = {
    "id": "deepseek-v4.1-flash",
    "modelID": "deepseek-v4.1-flash",
    "providerID": "opencode-go",
    "status": "active",
    "enabled": True,
}
INFO = json.dumps({"version": "2.1.0"})


def test_generate_parses_envelope_and_records_metadata(config):
    runner = FakeRunner(completed('{"data":{"text":"pong"}}'))
    provider = OpenCodeGenerateProvider(config, runner=runner)

    response = provider.generate(GenerationRequest(prompt="hi"))

    assert isinstance(response, GenerationResponse)
    assert response.text == "pong"
    assert response.provider == "opencode-go"
    assert response.model == "deepseek-v4.1-flash"
    assert response.extractor_version == "phase1-0.1"
    assert response.input_chars == len("hi")
    assert response.latency_ms >= 0


def test_generate_rejects_invalid_json(config):
    runner = FakeRunner(completed("not json"))
    provider = OpenCodeGenerateProvider(config, runner=runner)
    with pytest.raises(ProviderError):
        provider.generate(GenerationRequest(prompt="hi"))


def test_generate_rejects_missing_text(config):
    runner = FakeRunner(completed('{"data":{}}'))
    provider = OpenCodeGenerateProvider(config, runner=runner)
    with pytest.raises(ProviderError):
        provider.generate(GenerationRequest(prompt="hi"))


def test_generate_rejects_empty_text(config):
    runner = FakeRunner(completed('{"data":{"text":"   "}}'))
    provider = OpenCodeGenerateProvider(config, runner=runner)
    with pytest.raises(ProviderError):
        provider.generate(GenerationRequest(prompt="hi"))


def test_generate_rejects_nonzero_exit(config):
    runner = FakeRunner(completed("boom", returncode=1, stderr="HTTP 400 Bad Request"))
    provider = OpenCodeGenerateProvider(config, runner=runner)
    with pytest.raises(ProviderError):
        provider.generate(GenerationRequest(prompt="hi"))


def test_generate_rejects_oversized_prompt_before_calling_runner(config):
    runner = FakeRunner()
    provider = OpenCodeGenerateProvider(config, runner=runner)
    prompt = "x" * (config.limits.max_input_chars + 1)

    with pytest.raises(InputTooLarge):
        provider.generate(GenerationRequest(prompt=prompt))

    assert runner.calls == []


def test_generate_rejects_body_over_command_line_budget(config):
    """The body cap fires even when the prompt is under max_input_chars."""
    wide = config.model_copy(
        update={"limits": config.limits.model_copy(update={"max_input_chars": 40_000})}
    )
    runner = FakeRunner()
    provider = OpenCodeGenerateProvider(wide, runner=runner)
    prompt = "x" * 35_000
    assert len(prompt) <= wide.limits.max_input_chars

    with pytest.raises(InputTooLarge):
        provider.generate(GenerationRequest(prompt=prompt))

    assert runner.calls == []


def test_generate_rejects_non_string_text(config):
    runner = FakeRunner(completed('{"data":{"text":123}}'))
    provider = OpenCodeGenerateProvider(config, runner=runner)
    with pytest.raises(ProviderError):
        provider.generate(GenerationRequest(prompt="hi"))


def test_generate_uses_argv_list_without_shell(config):
    runner = FakeRunner(completed('{"data":{"text":"ok"}}'))
    provider = OpenCodeGenerateProvider(config, runner=runner)

    provider.generate(GenerationRequest(prompt="hi"))

    argv, kwargs = runner.calls[0]
    assert isinstance(argv, list)
    assert all(isinstance(part, str) for part in argv)
    assert kwargs["shell"] is False
    assert argv[:2] == [provider.cli, "api"]
    assert argv[2] == "post"
    assert argv[3] == "/api/experimental/generate"
    body = json.loads(argv[argv.index("--data") + 1])
    assert body == {"prompt": "hi", "model": {"providerID": "opencode-go", "id": "deepseek-v4.1-flash"}}


def test_doctor_accepts_active_enabled_model(config, capsys):
    runner = FakeRunner(completed(INFO), completed(registry(MODEL)))
    provider = OpenCodeGenerateProvider(config, runner=runner)

    assert doctor(config, provider=provider) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload == {
        "ok": True,
        "version": "2.1.0",
        "providerID": "opencode-go",
        "modelID": "deepseek-v4.1-flash",
        "status": "active",
        "enabled": True,
    }


def test_doctor_rejects_disabled_model(config, capsys):
    runner = FakeRunner(completed(INFO), completed(registry({**MODEL, "enabled": False})))
    provider = OpenCodeGenerateProvider(config, runner=runner)

    assert doctor(config, provider=provider) == 1

    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False


def test_doctor_reports_config_load_failure(capsys, monkeypatch):
    def missing():
        raise FileNotFoundError("config/extraction.yaml")

    monkeypatch.setattr("product_hunter.cli.load_config", missing)

    assert doctor() == 1

    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["error"] == "FileNotFoundError: config/extraction.yaml"

from __future__ import annotations

import pytest
from pydantic import ValidationError

from product_hunter import __version__
from product_hunter.cli import main
from product_hunter.config import load_config


def test_package_version():
    assert __version__ == "0.1.0"


def test_cli_help_exits_zero(capsys):
    with pytest.raises(SystemExit) as excinfo:
        main(["--help"])
    assert excinfo.value.code == 0
    assert "doctor" in capsys.readouterr().out


def test_cli_version(capsys):
    assert main(["version"]) == 0
    assert capsys.readouterr().out.strip() == "0.1.0"


def test_config_loads_defaults():
    config = load_config()
    assert config.provider == "opencode_generate"
    assert config.provider_id == "opencode-go"
    assert config.model_id == "deepseek-v4.1-flash"
    assert config.extractor_version == "phase1-0.1"
    assert config.prompt_version == "extract-v1"
    assert config.limits.max_documents == 150
    assert config.limits.max_calls == 300
    assert config.limits.max_input_chars == 12000
    assert config.limits.timeout_seconds == 120


def test_config_rejects_non_positive_limit(tmp_path):
    bad = tmp_path / "extraction.yaml"
    bad.write_text(
        "\n".join(
            [
                "version: 1",
                "provider: opencode_generate",
                "provider_id: opencode-go",
                "model_id: deepseek-v4.1-flash",
                "extractor_version: phase1-0.1",
                "prompt_version: extract-v1",
                "limits:",
                "  max_documents: 150",
                "  max_calls: 300",
                "  max_input_chars: 0",
                "  timeout_seconds: 120",
                "",
            ]
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValidationError):
        load_config(bad)

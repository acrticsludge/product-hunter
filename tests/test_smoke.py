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
    assert config.extractor_version == "phase1-0.2"
    assert config.prompt_version == "extract-v2"
    assert config.limits.max_documents == 150
    assert config.limits.max_calls == 300
    assert config.limits.max_input_chars == 12000
    assert config.limits.timeout_seconds == 120
    assert config.limits.min_quote_chars == 20
    assert config.limits.max_quote_chars == 2000
    assert config.limits.chunk_chars == 8000
    assert config.limits.chunk_overlap_chars == 400
    assert config.limits.max_candidates_per_chunk == 3


def test_config_rejects_non_positive_limit(tmp_path):
    bad = tmp_path / "extraction.yaml"
    bad.write_text(
        "\n".join(
            [
                "version: 1",
                "provider: opencode_generate",
                "provider_id: opencode-go",
                "model_id: deepseek-v4.1-flash",
                "extractor_version: phase1-0.2",
                "prompt_version: extract-v2",
                "limits:",
                "  max_documents: 150",
                "  max_calls: 300",
                "  max_input_chars: 0",
                "  timeout_seconds: 120",
                "  min_quote_chars: 20",
                "  max_quote_chars: 2000",
                "  chunk_chars: 8000",
                "  chunk_overlap_chars: 400",
                "  max_candidates_per_chunk: 3",
                "",
            ]
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValidationError):
        load_config(bad)


def test_config_rejects_quote_bounds_that_cross(tmp_path):
    bad = tmp_path / "extraction.yaml"
    bad.write_text(
        "\n".join(
            [
                "version: 1",
                "provider: opencode_generate",
                "provider_id: opencode-go",
                "model_id: deepseek-v4.1-flash",
                "extractor_version: phase1-0.2",
                "prompt_version: extract-v2",
                "limits:",
                "  max_documents: 150",
                "  max_calls: 300",
                "  max_input_chars: 12000",
                "  timeout_seconds: 120",
                "  min_quote_chars: 2000",
                "  max_quote_chars: 20",
                "  chunk_chars: 8000",
                "  chunk_overlap_chars: 400",
                "  max_candidates_per_chunk: 3",
                "",
            ]
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValidationError):
        load_config(bad)


def test_config_rejects_overlap_that_is_not_smaller_than_the_chunk(tmp_path):
    bad = tmp_path / "extraction.yaml"
    bad.write_text(
        "\n".join(
            [
                "version: 1",
                "provider: opencode_generate",
                "provider_id: opencode-go",
                "model_id: deepseek-v4.1-flash",
                "extractor_version: phase1-0.2",
                "prompt_version: extract-v2",
                "limits:",
                "  max_documents: 150",
                "  max_calls: 300",
                "  max_input_chars: 12000",
                "  timeout_seconds: 120",
                "  min_quote_chars: 20",
                "  max_quote_chars: 2000",
                "  chunk_chars: 8000",
                "  chunk_overlap_chars: 8000",
                "  max_candidates_per_chunk: 3",
                "",
            ]
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValidationError):
        load_config(bad)


def test_config_rejects_out_of_range_candidate_cap(tmp_path):
    bad = tmp_path / "extraction.yaml"
    bad.write_text(
        "\n".join(
            [
                "version: 1",
                "provider: opencode_generate",
                "provider_id: opencode-go",
                "model_id: deepseek-v4.1-flash",
                "extractor_version: phase1-0.2",
                "prompt_version: extract-v2",
                "limits:",
                "  max_documents: 150",
                "  max_calls: 300",
                "  max_input_chars: 12000",
                "  timeout_seconds: 120",
                "  min_quote_chars: 20",
                "  max_quote_chars: 2000",
                "  chunk_chars: 8000",
                "  chunk_overlap_chars: 400",
                "  max_candidates_per_chunk: 11",
                "",
            ]
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValidationError):
        load_config(bad)

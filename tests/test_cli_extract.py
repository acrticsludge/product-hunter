from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

from product_hunter.cli import (
    eval_command,
    extract_command,
    report_command,
    review_sheet_command,
)
from product_hunter.config import load_config
from product_hunter.evaluation import COLUMNS
from product_hunter.fetch import FetchRecord
from product_hunter.llm.base import (
    GenerationRequest,
    GenerationResponse,
    ProviderUnavailable,
    TextGenerator,
)

FIXTURE = Path(__file__).parent / "fixtures" / "hn_search.json"
BLOG_FIXTURE = Path(__file__).parent / "fixtures" / "blog.html"

# The one fixture hit that carries both quotes: a cost the team pays by hand,
# and a complaint about a missing feature with no cost behind it.
GENUINE_QUOTE = (
    "The PDF it produces is unreadable, so I retype every number into the "
    "ledger by hand and hope I do not miss a row."
)
NOT_PAIN_QUOTE = 'Our bookkeeping tool "does not export" anything.'

ANSWER = json.dumps(
    {
        "candidates": [
            {
                "quote": GENUINE_QUOTE,
                "pain_validity": "genuine",
                "reason": "recurring_manual_work",
                "pain_statement": "the team retypes every ledger number by hand",
                "severity": 3,
                "frequency": "weekly",
                "confidence": 0.8,
                "rationale": "the text says the PDF is unreadable and every number is retyped by hand",
                "domain": "finance",
                "actor": "the bookkeeper",
                "action": "retype ledger numbers by hand",
                "object": "ledger numbers",
                "consequence": "a row that can be missed in the ledger",
            },
            {
                "quote": NOT_PAIN_QUOTE,
                "pain_validity": "not_pain",
                "reason": "feature_request",
                "pain_statement": "the export tool is missing an export feature",
                "severity": 0,
                "frequency": "unknown",
                "confidence": 0.6,
                "rationale": "the text states a missing feature with no cost behind it",
            },
        ]
    }
)


class FakeGenerator:
    """A TextGenerator that answers every call with the same two candidates."""

    name = "fake"

    def __init__(self, answer: str = ANSWER) -> None:
        self._answer = answer
        self.prompts: list[str] = []

    def generate(self, request: GenerationRequest) -> GenerationResponse:
        self.prompts.append(request.prompt)
        return GenerationResponse(
            text=self._answer,
            provider="fake-provider",
            model="fake-model",
            extractor_version="phase1-0.3",
            input_chars=len(request.prompt),
            latency_ms=3,
        )

    def check(self) -> dict[str, object]:
        return {"ok": True}


class UnavailableGenerator(FakeGenerator):
    """A generator whose provider check fails, so no call may be made."""

    def check(self) -> dict[str, object]:
        raise ProviderUnavailable("opencode CLI not found on PATH")


def seed_repo(root: Path) -> str:
    """Store the HN fixture as a raw capture and record the fetch that made it."""
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    payload["hits"] = [hit for hit in payload["hits"] if hit["objectID"] == "9000005"]
    raw = json.dumps(payload).encode("utf-8")
    digest = hashlib.sha256(raw).hexdigest()
    raw_path = Path("data") / "raw" / "hn_algolia" / f"{digest}.json"
    target = root / raw_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(raw)
    record = FetchRecord(
        fetch_id="fetch_test",
        source_id="hn_algolia",
        requested_url="https://hn.algolia.com/api/v1/search?query=invoice",
        final_url="https://hn.algolia.com/api/v1/search?query=invoice",
        status=200,
        content_type="application/json",
        raw_sha256=digest,
        raw_path=raw_path.as_posix(),
        bytes=len(raw),
        fetched_at="2026-10-20T09:00:00+00:00",
    )
    log = root / "data" / "raw" / "fetches.jsonl"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(record.model_dump_json() + "\n", encoding="utf-8")
    return digest


def seed_blog_repo(root: Path) -> str:
    """Store the blog fixture as a raw capture of a source that may not be analyzed."""
    raw = BLOG_FIXTURE.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    raw_path = Path("data") / "raw" / "blog_http" / f"{digest}.html"
    target = root / raw_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(raw)
    record = FetchRecord(
        fetch_id="fetch_blog",
        source_id="blog_http",
        requested_url="https://blog.example.com/bookkeeping",
        final_url="https://blog.example.com/bookkeeping",
        status=200,
        content_type="text/html",
        raw_sha256=digest,
        raw_path=raw_path.as_posix(),
        bytes=len(raw),
        fetched_at="2026-10-20T09:00:00+00:00",
    )
    log = root / "data" / "raw" / "fetches.jsonl"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(record.model_dump_json() + "\n", encoding="utf-8")
    return digest


def run_extract(root: Path, run_id: str = "test-run") -> int:
    """Extract the seeded capture with a fake provider."""
    generator = FakeGenerator()
    assert isinstance(generator, TextGenerator)
    return extract_command(
        limit=1,
        repo_root=root,
        config=load_config(),
        generator=generator,
        run_id=run_id,
    )


def read_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_extract_command_writes_the_run(tmp_path, capsys):
    digest = seed_repo(tmp_path)
    config = load_config()

    code = run_extract(tmp_path)

    assert code == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["run_id"] == "test-run"
    assert summary["documents"] == 1
    assert summary["chunks"] == 1
    assert summary["calls"] == 1
    assert summary["candidates"] == 2
    assert summary["accepted"] == 1
    assert summary["rejected"] == 0
    assert summary["output_dir"] == str(tmp_path / "data" / "processed" / "test-run")

    out = tmp_path / "data" / "processed" / "test-run"
    for name in (
        "documents.jsonl",
        "candidates.jsonl",
        "pain_points.jsonl",
        "calls.jsonl",
        "rejects.jsonl",
        "manifest.json",
    ):
        assert (out / name).is_file()

    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["run_id"] == "test-run"
    assert manifest["documents"] == 1
    assert manifest["chunks"] == 1
    assert manifest["calls"] == 1
    assert manifest["candidates"] == 2
    assert manifest["accepted"] == 1
    assert manifest["rejected"] == 0
    assert manifest["failures"] == 0
    assert manifest["source_ids"] == ["hn_algolia"]
    assert manifest["provider"] == config.provider_id
    assert manifest["model"] == config.model_id
    assert manifest["extractor_version"] == config.extractor_version
    assert manifest["prompt_version"] == config.prompt_version
    assert manifest["normalization_version"] == config.normalization_version
    assert manifest["limits"]["max_candidates_per_chunk"] == config.limits.max_candidates_per_chunk
    assert manifest["started_at"] <= manifest["completed_at"]

    candidates = read_jsonl(out / "candidates.jsonl")
    assert len(candidates) == 2
    accepted = [candidate for candidate in candidates if candidate["accepted"]]
    assert len(accepted) == 1
    genuine = accepted[0]
    assert genuine["quote"] == GENUINE_QUOTE
    assert genuine["provider"] == "fake-provider"
    assert genuine["model"] == "fake-model"
    assert genuine["extractor_version"] == config.extractor_version
    assert genuine["prompt_version"] == config.prompt_version
    assert genuine["raw_sha256"] == digest
    assert genuine["raw_path"].startswith("data/raw/hn_algolia/")
    assert genuine["source_id"] == "hn_algolia"
    assert genuine["pain_id"].startswith("pp_")
    assert genuine["char_end"] > genuine["char_start"]

    rejected = [candidate for candidate in candidates if not candidate["accepted"]]
    assert len(rejected) == 1
    assert rejected[0]["pain_validity"] == "not_pain"
    assert rejected[0]["pain_id"] is None

    assert len(read_jsonl(out / "pain_points.jsonl")) == 1
    assert read_jsonl(out / "rejects.jsonl") == []
    calls = read_jsonl(out / "calls.jsonl")
    assert len(calls) == 1
    assert calls[0]["status"] == "ok"
    assert calls[0]["accepted_count"] == 1


def test_report_command_reports_the_run(tmp_path, capsys):
    seed_repo(tmp_path)
    run_extract(tmp_path)
    capsys.readouterr()

    out = tmp_path / "report.md"
    code = report_command("test-run", out, repo_root=tmp_path)

    assert code == 0
    report = json.loads(capsys.readouterr().out)
    assert report["run_id"] == "test-run"
    assert report["normalization_version"] == load_config().normalization_version
    assert report["documents"] == 1
    assert report["chunks"] == 1
    assert report["calls"] == 1
    assert report["candidates"] == 2
    assert report["accepted"] == 1
    assert report["rejected"] == 0
    assert report["failures"] == 0
    assert report["quote_exact"] + report["quote_canonical"] == 2
    assert report["normalization_keys"] == 1
    assert report["duplicate_text_hashes"] == 0
    assert report["source_counts"] == {
        "hn_algolia": {"documents": 1, "candidates": 2, "accepted": 1}
    }
    assert out.is_file()
    written = out.read_text(encoding="utf-8")
    assert "# Run report" in written
    assert "## Limitations" in written


def test_report_command_without_a_run_exits_one(tmp_path, capsys):
    code = report_command("no-such-run", repo_root=tmp_path)

    assert code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False


def test_report_command_refuses_an_unsafe_run_id(tmp_path, capsys):
    code = report_command("../escape", repo_root=tmp_path)

    assert code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert "invalid run id" in payload["error"]


def test_review_sheet_command_writes_a_blank_sheet(tmp_path, capsys):
    seed_repo(tmp_path)
    run_extract(tmp_path)
    capsys.readouterr()

    out = tmp_path / "review.csv"
    code = review_sheet_command("test-run", out, repo_root=tmp_path)

    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["run_id"] == "test-run"
    assert payload["rows"] == 2
    assert payload["path"] == str(out)

    with out.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 2
    assert [row["model_validity"] for row in rows] == ["genuine", "not_pain"]
    for row in rows:
        assert row["human_valid_pain"] == ""
        assert row["human_severity"] == ""
        assert row["human_frequency"] == ""
        assert row["notes"] == ""
        assert row["verbatim_ok"] == "true"
        assert row["quote"]


def test_review_sheet_command_without_a_run_exits_one(tmp_path, capsys):
    code = review_sheet_command("no-such-run", tmp_path / "review.csv", repo_root=tmp_path)

    assert code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False


def test_review_sheet_command_refuses_malformed_candidates(tmp_path, capsys):
    run_dir = tmp_path / "data" / "processed" / "broken-run"
    run_dir.mkdir(parents=True)
    (run_dir / "candidates.jsonl").write_text('{"candidate_id": "cand_1"}\n', encoding="utf-8")

    code = review_sheet_command("broken-run", tmp_path / "review.csv", repo_root=tmp_path)

    assert code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False


def test_eval_command_scores_a_filled_sheet(tmp_path, capsys):
    seed_repo(tmp_path)
    run_extract(tmp_path)
    sheet = tmp_path / "sheet.csv"
    review_sheet_command("test-run", sheet, repo_root=tmp_path)
    capsys.readouterr()

    with sheet.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    for row, verdict, severity in zip(rows, ("yes", "no"), ("3", "")):
        row["human_valid_pain"] = verdict
        row["human_severity"] = severity
        row["human_frequency"] = "weekly" if verdict == "yes" else ""
    labels = tmp_path / "labels.csv"
    with labels.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(COLUMNS))
        writer.writeheader()
        writer.writerows(rows)

    out = tmp_path / "report.md"
    code = eval_command(labels, out, repo_root=tmp_path)

    assert code == 0
    report = json.loads(capsys.readouterr().out)
    assert report["total"] == 2
    assert report["labeled"] == 2
    assert report["coverage"] == 1.0
    assert report["quote_exact"] == 2
    assert report["quote_failed"] == 0
    assert report["model_genuine"] == 1
    assert report["true_positive"] == 1
    assert report["false_positive"] == 0
    assert report["false_negative"] == 0
    assert report["precision"] == 1.0
    assert report["recall"] == 1.0
    assert report["severity_n"] == 1
    assert report["severity_exact"] == 1
    assert out.is_file()
    assert "# Extraction evaluation" in out.read_text(encoding="utf-8")


def test_eval_command_refuses_malformed_labels(tmp_path, capsys):
    labels = tmp_path / "labels.csv"
    labels.write_text("candidate_id,doc_id\ncand_1,doc_1\n", encoding="utf-8")

    code = eval_command(labels, tmp_path / "report.md", repo_root=tmp_path)

    assert code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False


def test_extract_command_without_fetches_exits_one(tmp_path, capsys):
    code = extract_command(
        limit=1,
        repo_root=tmp_path,
        config=load_config(),
        generator=FakeGenerator(),
        run_id="empty-run",
    )

    assert code == 1
    summary = json.loads(capsys.readouterr().out)
    assert summary["run_id"] == "empty-run"
    assert summary["documents"] == 0
    assert summary["calls"] == 0
    assert "no fetches" in summary["error"]


def test_extract_command_rejects_a_hash_mismatch(tmp_path, capsys):
    digest = seed_repo(tmp_path)
    (tmp_path / "data" / "raw" / "hn_algolia" / f"{digest}.json").write_bytes(b"{}")

    code = run_extract(tmp_path, run_id="bad-run")

    assert code == 1
    summary = json.loads(capsys.readouterr().out)
    assert summary["documents"] == 0
    assert summary["calls"] == 0
    rejects = read_jsonl(tmp_path / "data" / "processed" / "bad-run" / "rejects.jsonl")
    assert len(rejects) == 1
    assert rejects[0]["reason_code"] == "raw_hash_mismatch"
    assert rejects[0]["doc_id"] is None
    assert rejects[0]["fetch_id"] == "fetch_test"
    assert rejects[0]["source_id"] == "hn_algolia"
    assert rejects[0]["extractor_version"] == "phase1-0.3"
    assert rejects[0]["prompt_version"] == "extract-v3"


def test_extract_command_refuses_an_unsafe_run_id(tmp_path, capsys):
    code = extract_command(
        limit=1,
        repo_root=tmp_path,
        config=load_config(),
        generator=FakeGenerator(),
        run_id="../escape",
    )

    assert code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert "invalid run id" in payload["error"]
    assert not (tmp_path / "data" / "escape").exists()
    assert not (tmp_path / "escape").exists()


def test_extract_command_fails_closed_when_the_provider_check_fails(tmp_path, capsys):
    """The provider is verified first, so a dead provider costs no model call."""
    seed_repo(tmp_path)
    generator = UnavailableGenerator()

    code = extract_command(
        limit=1,
        repo_root=tmp_path,
        config=load_config(),
        generator=generator,
        run_id="unavailable-run",
    )

    assert code == 1
    summary = json.loads(capsys.readouterr().out)
    assert summary["run_id"] == "unavailable-run"
    assert summary["documents"] == 1
    assert summary["calls"] == 0
    assert summary["candidates"] == 0
    assert summary["rejected"] == 1
    assert "provider check failed" in summary["error"]
    assert generator.prompts == []

    out = tmp_path / "data" / "processed" / "unavailable-run"
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["calls"] == 0
    assert manifest["candidates"] == 0
    assert manifest["rejected"] == 1
    assert read_jsonl(out / "candidates.jsonl") == []
    # The dead provider leaves a record of its own.
    rejects = read_jsonl(out / "rejects.jsonl")
    assert len(rejects) == 1
    assert rejects[0]["reason_code"] == "provider_check_failed"
    assert rejects[0]["doc_id"] is None
    assert rejects[0]["fetch_id"] is None
    assert rejects[0]["source_id"] is None
    assert "opencode CLI not found on PATH" in rejects[0]["detail"]
    assert rejects[0]["provider"] == load_config().provider_id
    assert rejects[0]["model"] == load_config().model_id
    assert rejects[0]["extractor_version"] == "phase1-0.3"
    assert rejects[0]["prompt_version"] == "extract-v3"
    assert rejects[0]["created_at"]


def test_extract_command_rejects_a_source_without_analysis_use(tmp_path, capsys):
    """blog_http is disabled and allows no use, so its capture is never read."""
    seed_blog_repo(tmp_path)
    generator = FakeGenerator()

    code = extract_command(
        limit=1,
        repo_root=tmp_path,
        config=load_config(),
        generator=generator,
        run_id="blocked-run",
    )

    assert code == 1
    summary = json.loads(capsys.readouterr().out)
    assert summary["documents"] == 0
    assert summary["chunks"] == 0
    assert summary["calls"] == 0
    assert summary["candidates"] == 0
    assert summary["rejected"] == 1
    assert generator.prompts == []
    out = tmp_path / "data" / "processed" / "blocked-run"
    assert read_jsonl(out / "documents.jsonl") == []
    rejects = read_jsonl(out / "rejects.jsonl")
    assert len(rejects) == 1
    assert rejects[0]["reason_code"] == "source_disabled"
    assert rejects[0]["doc_id"] is None
    assert rejects[0]["fetch_id"] == "fetch_blog"
    assert rejects[0]["source_id"] == "blog_http"
    assert rejects[0]["extractor_version"] == "phase1-0.3"
    assert rejects[0]["prompt_version"] == "extract-v3"

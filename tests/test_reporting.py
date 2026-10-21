from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import BaseModel

from product_hunter.documents import Document
from product_hunter.extraction import (
    ExtractedCandidate,
    ExtractionCall,
    ExtractionReject,
)
from product_hunter.normalization import Domain, normalize_fields
from product_hunter.reporting import (
    ReportError,
    build_run_report,
    render_markdown,
    write_report,
)

# Two documents that hold the same text, so the run has one duplicate.
SHARED_TEXT_SHA = "a" * 64


def make_document(doc_id: str, source_id: str) -> Document:
    return Document(
        doc_id=doc_id,
        fetch_id=f"fetch_{doc_id}",
        source_id=source_id,
        url=f"https://example.invalid/{doc_id}",
        text="the team pastes the totals by hand every month",
        text_sha256=SHARED_TEXT_SHA,
        text_path=f"data/processed/text/{SHARED_TEXT_SHA}.txt",
        raw_path=f"data/raw/{source_id}/raw.json",
        raw_sha256="b" * 64,
    )


def make_candidate(
    candidate_id: str,
    doc_id: str,
    source_id: str,
    *,
    match_mode: str = "exact",
    accepted: bool = True,
    action: str = "paste the totals by hand",
) -> ExtractedCandidate:
    """A stored candidate, the way the extractor writes it.

    An accepted candidate carries the five normalized fields and the key they
    make, so the record is checked against itself when it is read back.
    """
    quote = f"the team pastes the totals by hand every month ({candidate_id})"
    if accepted:
        normalized = normalize_fields(
            domain=Domain.finance,
            actor="the bookkeeping team",
            action=action,
            object_="the invoice totals",
            consequence="a Saturday of manual work every month",
            normalization_version="norm-v1",
        )
        domain = normalized.domain.value
        actor = normalized.actor
        object_ = normalized.object
        consequence = normalized.consequence
        normalized_key = normalized.normalized_key
    else:
        domain = actor = object_ = consequence = None
        normalized_key = None
    return ExtractedCandidate(
        candidate_id=candidate_id,
        pain_id=f"pp_{candidate_id}" if accepted else None,
        doc_id=doc_id,
        fetch_id=f"fetch_{doc_id}",
        source_id=source_id,
        url=f"https://example.invalid/{doc_id}",
        span_id=f"ev_{candidate_id}",
        quote=quote,
        char_start=10,
        char_end=10 + len(quote),
        match_mode=match_mode,
        model_quote=quote,
        occurrence=1,
        pain_validity="genuine" if accepted else "not_pain",
        reason="time_cost" if accepted else "informational",
        pain_statement="the team pastes the totals by hand",
        severity=3 if accepted else 0,
        frequency="monthly",
        confidence=0.9,
        rationale="the text says the totals are pasted by hand",
        domain=domain,
        actor=actor,
        action=action if accepted else None,
        object=object_,
        consequence=consequence,
        normalized_key=normalized_key,
        normalization_version="norm-v1",
        accepted=accepted,
        provider="fake-provider",
        model="fake-model",
        extractor_version="phase1-0.3",
        prompt_version="extract-v3",
        raw_path=f"data/raw/{source_id}/raw.json",
        raw_sha256="b" * 64,
        text_sha256=SHARED_TEXT_SHA,
        created_at="2026-10-20T10:01:00+00:00",
    )


def make_call(call_id: str, doc_id: str, *, status: str = "ok") -> ExtractionCall:
    return ExtractionCall(
        call_id=call_id,
        doc_id=doc_id,
        chunk_index=0,
        provider="fake-provider",
        model="fake-model",
        extractor_version="phase1-0.3",
        prompt_version="extract-v3",
        input_chars=1200,
        latency_ms=40,
        candidate_count=2,
        accepted_count=1,
        rejected_count=1,
        status=status,
        created_at="2026-10-20T10:01:00+00:00",
    )


def make_reject(reject_id: str, reason_code: str) -> ExtractionReject:
    return ExtractionReject(
        reject_id=reject_id,
        doc_id="doc_1",
        fetch_id="fetch_doc_1",
        source_id="hn_algolia",
        reason_code=reason_code,
        detail="a detail",
        extractor_version="phase1-0.3",
        prompt_version="extract-v3",
        created_at="2026-10-20T10:01:00+00:00",
    )


def write_jsonl(path: Path, records: list[BaseModel]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for record in records:
            handle.write(record.model_dump_json() + "\n")


def make_run_dir(tmp_path: Path, manifest: dict | None = None) -> Path:
    """A run directory with two documents, four candidates, and the rest."""
    run_dir = tmp_path / "data" / "processed" / "run-1"
    run_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "run_id": "run-1",
        "provider": "opencode-go",
        "model": "deepseek-v4.1-flash",
        "extractor_version": "phase1-0.3",
        "prompt_version": "extract-v3",
        "normalization_version": "norm-v1",
        "started_at": "2026-10-20T10:00:00+00:00",
        "completed_at": "2026-10-20T10:05:00+00:00",
        "chunks": 5,
        # The manifest counts are deliberately wrong: the report must count
        # the artifacts, not the manifest.
        "documents": 99,
        "calls": 99,
        "candidates": 99,
        "accepted": 99,
        "rejected": 99,
    }
    if manifest is not None:
        payload.update(manifest)
    (run_dir / "manifest.json").write_text(
        json.dumps(payload, indent=2) + "\n", encoding="utf-8"
    )
    write_jsonl(
        run_dir / "documents.jsonl",
        [
            make_document("doc_1", "hn_algolia"),
            make_document("doc_2", "github_issues"),
        ],
    )
    write_jsonl(
        run_dir / "candidates.jsonl",
        [
            make_candidate("cand_1", "doc_1", "hn_algolia"),
            make_candidate(
                "cand_2",
                "doc_1",
                "hn_algolia",
                match_mode="canonical",
                action="copy the totals by hand",
            ),
            make_candidate(
                "cand_3",
                "doc_2",
                "github_issues",
                accepted=False,
            ),
            make_candidate(
                "cand_4",
                "doc_2",
                "github_issues",
                accepted=False,
            ),
        ],
    )
    write_jsonl(
        run_dir / "rejects.jsonl",
        [
            make_reject("rej_1", "quote_not_found"),
            make_reject("rej_2", "quote_not_found"),
            make_reject("rej_3", "provider_error"),
        ],
    )
    write_jsonl(
        run_dir / "calls.jsonl",
        [
            make_call("call_1", "doc_1"),
            make_call("call_2", "doc_2"),
            make_call("call_3", "doc_2", status="provider_error"),
        ],
    )
    return run_dir


# --- the report ------------------------------------------------------------


def test_build_run_report_counts_the_artifacts(tmp_path):
    run_dir = make_run_dir(tmp_path)

    report = build_run_report(run_dir)

    assert report.run_id == "run-1"
    assert report.provider == "opencode-go"
    assert report.model == "deepseek-v4.1-flash"
    assert report.extractor_version == "phase1-0.3"
    assert report.prompt_version == "extract-v3"
    assert report.normalization_version == "norm-v1"
    assert report.started_at == "2026-10-20T10:00:00+00:00"
    assert report.completed_at == "2026-10-20T10:05:00+00:00"
    # The manifest lies about the counts, and the report ignores it.
    assert report.documents == 2
    assert report.chunks == 5
    assert report.calls == 3
    assert report.candidates == 4
    assert report.accepted == 2
    assert report.rejected == 3
    assert report.failures == 1


def test_build_run_report_counts_the_quotes_and_the_keys(tmp_path):
    run_dir = make_run_dir(tmp_path)

    report = build_run_report(run_dir)

    assert report.quote_exact == 3
    assert report.quote_canonical == 1
    # Two candidates carry a key, and they are two different keys.
    assert report.normalization_keys == 2
    # Two documents hold the same text.
    assert report.duplicate_text_hashes == 1


def test_build_run_report_splits_the_sources(tmp_path):
    run_dir = make_run_dir(tmp_path)

    report = build_run_report(run_dir)

    assert report.source_counts == {
        "github_issues": {"documents": 1, "candidates": 2, "accepted": 0},
        "hn_algolia": {"documents": 1, "candidates": 2, "accepted": 2},
    }


def test_build_run_report_counts_the_rejections(tmp_path):
    run_dir = make_run_dir(tmp_path)

    report = build_run_report(run_dir)

    assert report.rejection_counts == {"provider_error": 1, "quote_not_found": 2}


def test_build_run_report_without_a_manifest_raises(tmp_path):
    run_dir = make_run_dir(tmp_path)
    (run_dir / "manifest.json").unlink()

    with pytest.raises(ReportError) as excinfo:
        build_run_report(run_dir)

    assert "manifest" in str(excinfo.value)


def test_build_run_report_with_a_malformed_line_raises(tmp_path):
    run_dir = make_run_dir(tmp_path)
    with (run_dir / "candidates.jsonl").open("a", encoding="utf-8") as handle:
        handle.write('{"candidate_id": "cand_5"}\n')

    with pytest.raises(ReportError) as excinfo:
        build_run_report(run_dir)

    message = str(excinfo.value)
    assert "candidates.jsonl" in message
    assert "line 5" in message


def test_build_run_report_with_a_broken_json_line_raises(tmp_path):
    run_dir = make_run_dir(tmp_path)
    with (run_dir / "calls.jsonl").open("a", encoding="utf-8") as handle:
        handle.write("not json at all\n")

    with pytest.raises(ReportError) as excinfo:
        build_run_report(run_dir)

    message = str(excinfo.value)
    assert "calls.jsonl" in message
    assert "line 4" in message


def test_build_run_report_with_a_missing_artifact_raises(tmp_path):
    run_dir = make_run_dir(tmp_path)
    (run_dir / "calls.jsonl").unlink()

    with pytest.raises(ReportError) as excinfo:
        build_run_report(run_dir)

    assert "calls.jsonl" in str(excinfo.value)


def test_an_older_manifest_without_a_normalization_version(tmp_path):
    run_dir = make_run_dir(tmp_path)
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    del manifest["normalization_version"]
    (run_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    report = build_run_report(run_dir)

    assert report.normalization_version == ""


# --- the markdown report ---------------------------------------------------


def test_render_markdown_carries_the_run_and_its_counts(tmp_path):
    report = build_run_report(make_run_dir(tmp_path))

    text = render_markdown(report)

    assert "# Run report" in text
    assert "run-1" in text
    assert "opencode-go" in text
    assert "deepseek-v4.1-flash" in text
    assert "2 documents in 5 chunks over 3 calls" in text
    assert "## Counts" in text
    assert "| Documents | 2 |" in text
    assert "| Chunks | 5 |" in text
    assert "| Calls | 3 |" in text
    assert "| Failed calls | 1 |" in text
    assert "| Candidates | 4 |" in text
    assert "| Accepted | 2 |" in text
    assert "| Rejected | 3 |" in text
    # The quote fidelity.
    assert "## Quote fidelity" in text
    assert "| exact | 3 |" in text
    assert "| canonical | 1 |" in text
    # The rejection histogram.
    assert "## Rejections" in text
    assert "| quote_not_found | 2 |" in text
    assert "| provider_error | 1 |" in text
    # The source table.
    assert "## Sources" in text
    assert "| hn_algolia | 1 | 2 | 2 |" in text
    assert "| github_issues | 1 | 2 | 0 |" in text
    # The normalization.
    assert "## Normalization" in text
    assert "| Distinct normalized keys | 2 |" in text
    assert "| Duplicate document texts | 1 |" in text
    # The limitation.
    assert "## Limitations" in text
    assert "does not measure" in text
    assert "model quality" in text


def test_render_markdown_handles_a_run_without_rejections(tmp_path):
    run_dir = make_run_dir(tmp_path)
    (run_dir / "rejects.jsonl").write_text("", encoding="utf-8")

    report = build_run_report(run_dir)
    text = render_markdown(report)

    assert "No rejection was recorded." in text
    assert report.rejection_counts == {}


def test_write_report_creates_the_parent_and_writes_the_markdown(tmp_path):
    report = build_run_report(make_run_dir(tmp_path))
    path = tmp_path / "reports" / "run-1.md"

    write_report(report, path)

    assert path.is_file()
    assert path.read_text(encoding="utf-8") == render_markdown(report)
    assert "## Limitations" in path.read_text(encoding="utf-8")

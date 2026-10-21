"""Compute the report of one extraction run from the artifacts it wrote.

The report counts what the run produced: documents, calls, candidates,
rejections, and the normalized keys behind them. The counts come from the
artifacts, not from the manifest, so a manifest that overstates a run cannot
quiet the numbers. The chunk count is the one exception: the run directory
holds no chunk list, so it is read from the manifest. The manifest is the run's
metadata.

Nothing here measures how good the model is. That judgement comes from a
human-labelled review sheet, which ``ph eval`` scores.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError

from product_hunter.documents import Document
from product_hunter.extraction import (
    ExtractedCandidate,
    ExtractionCall,
    ExtractionReject,
)

# The files one run directory holds.
MANIFEST = "manifest.json"
DOCUMENTS = "documents.jsonl"
CANDIDATES = "candidates.jsonl"
REJECTS = "rejects.jsonl"
CALLS = "calls.jsonl"

LIMITATION = (
    "This report measures the artifacts of one run: what was written, how the "
    "quotes matched, and how the rejections broke down. It does not measure "
    "model quality. That comes from a human-labelled review sheet, scored by "
    "ph eval."
)


class ReportError(Exception):
    """A run report could not be built from the run directory."""


class RunReport(BaseModel):
    """The numbers one run directory produces."""

    run_id: str
    provider: str
    model: str
    extractor_version: str
    prompt_version: str
    normalization_version: str
    started_at: str
    completed_at: str
    documents: int
    chunks: int
    calls: int
    candidates: int
    accepted: int
    rejected: int
    failures: int
    quote_exact: int
    quote_canonical: int
    rejection_counts: dict[str, int] = Field(default_factory=dict)
    source_counts: dict[str, dict[str, int]] = Field(default_factory=dict)
    duplicate_text_hashes: int = 0
    normalization_keys: int = 0


def build_run_report(run_dir: Path) -> RunReport:
    """Build the report of one run directory.

    The manifest must be there: without it there is no run to report on. Every
    count except the chunk count comes from the artifacts, so a manifest that
    disagrees with the artifacts does not change the counts. The chunk count is
    read from the manifest, because the run directory holds no chunk list.
    """
    run_dir = Path(run_dir)
    manifest = _read_manifest(run_dir / MANIFEST)
    documents = _read_records(run_dir / DOCUMENTS, Document)
    candidates = _read_records(run_dir / CANDIDATES, ExtractedCandidate)
    rejects = _read_records(run_dir / REJECTS, ExtractionReject)
    calls = _read_records(run_dir / CALLS, ExtractionCall)

    return RunReport(
        run_id=_text(manifest.get("run_id")),
        provider=_text(manifest.get("provider")),
        model=_text(manifest.get("model")),
        extractor_version=_text(manifest.get("extractor_version")),
        prompt_version=_text(manifest.get("prompt_version")),
        # A run made before the normalization version was recorded still
        # reports; the version is simply unknown.
        normalization_version=_text(manifest.get("normalization_version")),
        started_at=_text(manifest.get("started_at")),
        completed_at=_text(manifest.get("completed_at")),
        documents=len(documents),
        chunks=_number(manifest.get("chunks")),
        calls=len(calls),
        candidates=len(candidates),
        accepted=sum(1 for candidate in candidates if candidate.accepted),
        rejected=len(rejects),
        failures=sum(1 for call in calls if call.status != "ok"),
        quote_exact=sum(
            1 for candidate in candidates if candidate.match_mode == "exact"
        ),
        quote_canonical=sum(
            1 for candidate in candidates if candidate.match_mode == "canonical"
        ),
        rejection_counts=dict(
            sorted(Counter(reject.reason_code for reject in rejects).items())
        ),
        source_counts=_source_counts(documents, candidates),
        duplicate_text_hashes=len(documents)
        - len({document.text_sha256 for document in documents}),
        normalization_keys=len(
            {
                candidate.normalized_key
                for candidate in candidates
                if candidate.normalized_key
            }
        ),
    )


def _source_counts(
    documents: list[Document], candidates: list[ExtractedCandidate]
) -> dict[str, dict[str, int]]:
    """The documents, candidates and accepted candidates of each source."""
    document_counts = Counter(document.source_id for document in documents)
    candidate_counts = Counter(candidate.source_id for candidate in candidates)
    accepted_counts = Counter(
        candidate.source_id for candidate in candidates if candidate.accepted
    )
    return {
        source_id: {
            "documents": document_counts.get(source_id, 0),
            "candidates": candidate_counts.get(source_id, 0),
            "accepted": accepted_counts.get(source_id, 0),
        }
        for source_id in sorted(set(document_counts) | set(candidate_counts))
    }


def _read_manifest(path: Path) -> dict:
    """Read the manifest of a run, or refuse to report on it."""
    if not path.is_file():
        raise ReportError(f"run manifest is missing: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ReportError(f"{path}: not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ReportError(f"{path}: the manifest is not a JSON object")
    return payload


def _read_records(path: Path, model: type[BaseModel]) -> list[BaseModel]:
    """Read one JSONL artifact, or refuse the run.

    A broken line is reported with the file and the line number, so the run
    that produced it can be found.
    """
    if not path.is_file():
        raise ReportError(f"run artifact is missing: {path}")
    records: list[BaseModel] = []
    for number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not line.strip():
            continue
        try:
            records.append(model.model_validate_json(line))
        except ValidationError as exc:
            raise ReportError(
                f"{path}: line {number} is not a valid record: {exc}"
            ) from exc
    return records


def _text(value: object) -> str:
    """A manifest value as text, with nothing standing in for it."""
    return "" if value is None else str(value)


def _number(value: object) -> int:
    """A manifest count as a number, with nothing standing in for it."""
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0


# --- the markdown report ---------------------------------------------------


def render_markdown(report: RunReport) -> str:
    """Render the run report as markdown."""
    lines: list[str] = []
    lines.append("# Run report")
    lines.append("")
    lines.append(_summary(report))
    lines.append("")
    lines.append("## Counts")
    lines.append("")
    lines.append("| Metric | Value |")
    lines.append("| --- | --- |")
    for name, value in (
        ("Documents", report.documents),
        ("Chunks", report.chunks),
        ("Calls", report.calls),
        ("Failed calls", report.failures),
        ("Candidates", report.candidates),
        ("Accepted", report.accepted),
        ("Rejected", report.rejected),
    ):
        lines.append(f"| {name} | {value} |")
    lines.append("")
    lines.append("## Quote fidelity")
    lines.append("")
    lines.append("| Match mode | Count |")
    lines.append("| --- | --- |")
    lines.append(f"| exact | {report.quote_exact} |")
    lines.append(f"| canonical | {report.quote_canonical} |")
    lines.append("")
    lines.append("## Rejections")
    lines.append("")
    if report.rejection_counts:
        lines.append("| Reason | Count |")
        lines.append("| --- | --- |")
        for reason, count in report.rejection_counts.items():
            lines.append(f"| {reason} | {count} |")
    else:
        lines.append("No rejection was recorded.")
    lines.append("")
    lines.append("## Sources")
    lines.append("")
    if report.source_counts:
        lines.append("| Source | Documents | Candidates | Accepted |")
        lines.append("| --- | --- | --- | --- |")
        for source_id, counts in report.source_counts.items():
            lines.append(
                f"| {source_id} | {counts['documents']} | {counts['candidates']} | "
                f"{counts['accepted']} |"
            )
    else:
        lines.append("No source produced a document.")
    lines.append("")
    lines.append("## Normalization")
    lines.append("")
    lines.append("| Metric | Value |")
    lines.append("| --- | --- |")
    lines.append(f"| Distinct normalized keys | {report.normalization_keys} |")
    lines.append(f"| Duplicate document texts | {report.duplicate_text_hashes} |")
    lines.append("")
    lines.append("## Limitations")
    lines.append("")
    lines.append(LIMITATION)
    lines.append("")
    return "\n".join(lines)


def write_report(report: RunReport, path: Path) -> None:
    """Write the markdown report, creating the parent directories."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_markdown(report), encoding="utf-8", newline="\n")


def _summary(report: RunReport) -> str:
    """One paragraph on what the run did."""
    normalization = (
        f"normalization {report.normalization_version}"
        if report.normalization_version
        else "no recorded normalization version"
    )
    failures = (
        "none failed" if report.failures == 0 else f"{report.failures} failed"
    )
    return (
        f"Run {report.run_id} on provider {report.provider} with model "
        f"{report.model}. Extractor {report.extractor_version}, prompt "
        f"{report.prompt_version}, {normalization}. It read "
        f"{_count(report.documents, 'document', 'documents')} in "
        f"{_count(report.chunks, 'chunk', 'chunks')} over "
        f"{_count(report.calls, 'call', 'calls')}, of which {failures}. "
        f"{report.accepted} of {report.candidates} candidates were accepted and "
        f"{report.rejected} were rejected."
    )


def _count(count: int, singular: str, plural: str) -> str:
    """A count in the right number."""
    return f"{count} {singular if count == 1 else plural}"

from __future__ import annotations

import argparse
import hashlib
import json
import re
import secrets
from collections.abc import Iterable
from pathlib import Path

import httpx
from pydantic import BaseModel, ValidationError

from product_hunter import __version__
from product_hunter.config import ExtractionConfig, load_config, repo_root
from product_hunter.documents import (
    Document,
    DocumentChunk,
    DocumentError,
    load_fetch_documents,
)
from product_hunter.evaluation import (
    evaluate,
    read_review_sheet,
    write_report,
    write_review_sheet,
)
from product_hunter.extraction import (
    ExtractedCandidate,
    ExtractionCall,
    ExtractionReject,
    extract_documents,
)
from product_hunter.fetch import (
    Fetcher,
    FetchError,
    FetchRecord,
    JsonlLog,
    utcnow,
)
from product_hunter.llm import build_generator
from product_hunter.llm.base import ProviderError, TextGenerator
from product_hunter.raw_store import RawStore
from product_hunter.reporting import (
    ReportError,
    build_run_report,
    write_report as write_run_report,
)
from product_hunter.safety import UrlBlocked
from product_hunter.sources import AllowedUse, SourceBlocked, load_registry

FETCH_LOG = Path("data") / "raw" / "fetches.jsonl"
REJECTION_LOG = Path("data") / "raw" / "rejections.jsonl"
PROCESSED_DIR = Path("data") / "processed"

# A run id is a plain name. Path separators and a parent link are refused so
# a run id can never name a directory outside the processed tree.
RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]+$")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ph",
        description="Product Hunter: evidence-driven pain point research.",
    )
    commands = parser.add_subparsers(dest="command")
    commands.add_parser("doctor", help="check the configured provider and model")
    commands.add_parser("version", help="print the package version")
    commands.add_parser("sources", help="list configured sources as JSON")
    fetch_parser = commands.add_parser("fetch", help="fetch one URL and record provenance")
    fetch_parser.add_argument("source_id")
    fetch_parser.add_argument("url")
    extract_parser = commands.add_parser(
        "extract", help="extract pain-point candidates from the stored fetches"
    )
    extract_parser.add_argument("--limit", type=int, default=None)
    extract_parser.add_argument("--source", dest="source_id", default=None)
    extract_parser.add_argument("--run-id", dest="run_id", default=None)
    sheet_parser = commands.add_parser(
        "review-sheet", help="write a review sheet for one run"
    )
    sheet_parser.add_argument("--run", dest="run_id", required=True)
    sheet_parser.add_argument("--out", dest="out", required=True)
    sheet_parser.add_argument("--limit", type=int, default=100)
    eval_parser = commands.add_parser("eval", help="score a filled review sheet")
    eval_parser.add_argument("--labels", dest="labels", required=True)
    eval_parser.add_argument("--out", dest="out", default=None)
    eval_parser.add_argument("--quote-failures", dest="quote_failures", type=int, default=0)
    report_parser = commands.add_parser("report", help="report the metrics of one run")
    report_parser.add_argument("--run", dest="run_id", required=True)
    report_parser.add_argument("--out", dest="out", default=None)
    return parser


def doctor(
    config: ExtractionConfig | None = None,
    provider: TextGenerator | None = None,
) -> int:
    """Run the provider capability check. Returns 0 on success, 1 on failure."""
    try:
        config = config if config is not None else load_config()
        provider = provider if provider is not None else build_generator(config)
        info = provider.check()
    except ProviderError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 1
    except Exception as exc:
        print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}))
        return 1
    print(json.dumps({"ok": True, **info}))
    return 0


def sources_command(registry=None) -> int:
    """Print the configured source list as a JSON array. Returns 0."""
    registry = registry if registry is not None else load_registry()
    payload = [
        {
            "source_id": source.source_id,
            "terms_status": source.terms_status.value,
            "enabled": source.enabled,
            "allowed_use": [use.value for use in source.allowed_use],
            "domains": list(source.domains),
        }
        for source in registry.list()
    ]
    print(json.dumps(payload))
    return 0


def fetch_command(
    source_id: str,
    url: str,
    *,
    registry=None,
    store=None,
    client=None,
    now=utcnow,
) -> int:
    """Fetch one admitted URL and print the FetchRecord. Returns 0, or 1 on failure."""
    registry = registry if registry is not None else load_registry()
    root = repo_root()
    store = store if store is not None else RawStore(root)
    if client is None:
        with httpx.Client(follow_redirects=False) as owned_client:
            return _run_fetch(registry, store, root, owned_client, now, source_id, url)
    return _run_fetch(registry, store, root, client, now, source_id, url)


def _run_fetch(registry, store, root, client, now, source_id: str, url: str) -> int:
    fetcher = Fetcher(
        registry,
        store,
        JsonlLog(root / FETCH_LOG),
        JsonlLog(root / REJECTION_LOG),
        client,
        now=now,
    )
    try:
        record = fetcher.fetch(source_id, url)
    except (FetchError, SourceBlocked, UrlBlocked) as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "reason": getattr(exc, "reason_code", "fetch_failed"),
                    "error": str(exc),
                }
            )
        )
        return 1
    print(record.model_dump_json())
    return 0


def extract_command(
    limit: int | None = None,
    source_id: str | None = None,
    run_id: str | None = None,
    *,
    repo_root: Path | None = None,
    config: ExtractionConfig | None = None,
    generator: TextGenerator | None = None,
) -> int:
    """Extract pain-point candidates from the stored fetches.

    Returns 0 when at least one model call succeeded. Returns 1 when there
    was nothing to extract, when no document could be built, or when every
    call failed.
    """
    run_id = run_id if run_id is not None else _default_run_id()
    if not _valid_run_id(run_id):
        print(json.dumps({"ok": False, "error": f"invalid run id {run_id!r}"}))
        return 1
    root = _root(repo_root)
    try:
        config = config if config is not None else load_config()
    except (OSError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": f"config: {exc}"}))
        return 1
    try:
        records = _read_fetch_records(root / FETCH_LOG)
    except ValueError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 1
    try:
        registry = load_registry()
    except (OSError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": f"sources: {exc}"}))
        return 1

    selected = _select_fetches(records, source_id=source_id, limit=limit)
    started = utcnow().isoformat()

    documents: list[Document] = []
    rejects: list[ExtractionReject] = []
    for record in selected:
        # Analysis is its own use: a source may allow retrieval and storage
        # and still not allow the text to be read by the model.
        try:
            registry.require(record.source_id, {AllowedUse.analysis})
        except SourceBlocked as exc:
            rejects.append(_source_reject(config, record, exc))
            continue
        try:
            documents.extend(load_fetch_documents(record, root))
        except DocumentError as exc:
            rejects.append(_document_reject(config, record, exc))

    error: str | None = None
    try:
        generator = generator if generator is not None else build_generator(config)
    except ProviderError as exc:
        error = str(exc)
        generator = None

    if generator is not None:
        # The provider is verified before any document text is sent. A dead or
        # mismatched provider must fail here, not after a model call.
        try:
            generator.check()
        except ProviderError as exc:
            error = f"provider check failed: {exc}"
            # The run still leaves a record, so a dead provider is never a
            # silent failure.
            rejects.append(_provider_reject(config, error))
            generator = None

    calls: list[ExtractionCall] = []
    candidates: list[ExtractedCandidate] = []
    chunks: list[DocumentChunk] = []
    if generator is not None and documents:
        batch = extract_documents(documents, generator, config)
        calls = batch.calls
        candidates = batch.candidates
        chunks = batch.chunks
        documents = batch.documents
        rejects.extend(batch.rejects)

    accepted = sum(1 for candidate in candidates if candidate.accepted)
    succeeded = sum(1 for call in calls if call.status == "ok")
    if error is None and succeeded == 0:
        if not selected:
            error = "no fetches to extract"
        elif not documents:
            error = "no document could be built from the selected fetches"
        else:
            error = "no extraction call succeeded"

    out_dir = root / PROCESSED_DIR / run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    _write_jsonl(out_dir / "documents.jsonl", documents)
    _write_jsonl(out_dir / "candidates.jsonl", candidates)
    _write_jsonl(out_dir / "pain_points.jsonl", [c for c in candidates if c.accepted])
    _write_jsonl(out_dir / "calls.jsonl", calls)
    _write_jsonl(out_dir / "rejects.jsonl", rejects)
    manifest = {
        "run_id": run_id,
        "started_at": started,
        "completed_at": utcnow().isoformat(),
        "provider": config.provider_id,
        "model": config.model_id,
        "extractor_version": config.extractor_version,
        "prompt_version": config.prompt_version,
        "normalization_version": config.normalization_version,
        "limits": config.limits.model_dump(),
        "documents": len(documents),
        "chunks": len(chunks),
        "calls": len(calls),
        "candidates": len(candidates),
        "accepted": accepted,
        "rejected": len(rejects),
        "failures": len(calls) - succeeded,
        "source_ids": sorted({record.source_id for record in selected}),
    }
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )

    summary = {
        "run_id": run_id,
        "documents": len(documents),
        "chunks": len(chunks),
        "calls": len(calls),
        "candidates": len(candidates),
        "accepted": accepted,
        "rejected": len(rejects),
        "output_dir": str(out_dir),
    }
    if error is not None:
        summary["error"] = error
    print(json.dumps(summary))
    return 0 if succeeded else 1


def review_sheet_command(
    run_id: str,
    out: Path,
    *,
    limit: int = 100,
    repo_root: Path | None = None,
) -> int:
    """Write a review sheet for one run. Returns 0, or 1 on a missing or broken run."""
    if not _valid_run_id(run_id):
        print(json.dumps({"ok": False, "error": f"invalid run id {run_id!r}"}))
        return 1
    root = _root(repo_root)
    source = root / PROCESSED_DIR / run_id / "candidates.jsonl"
    if not source.is_file():
        print(
            json.dumps(
                {"ok": False, "error": f"run {run_id!r} has no candidates file"}
            )
        )
        return 1
    try:
        candidates = _read_candidates(source)
    except ValueError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 1
    target = _resolve_path(out, root)
    try:
        rows = write_review_sheet(candidates, target, limit=limit)
    except OSError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 1
    print(
        json.dumps(
            {"ok": True, "run_id": run_id, "rows": rows, "path": str(target)}
        )
    )
    return 0


def eval_command(
    labels: Path,
    out: Path | None = None,
    *,
    quote_failures: int = 0,
    repo_root: Path | None = None,
) -> int:
    """Score a filled review sheet. Returns 0, or 1 on a malformed sheet."""
    root = _root(repo_root)
    try:
        rows = read_review_sheet(_resolve_path(labels, root))
    except (OSError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 1
    report = evaluate(rows, quote_failures=quote_failures)
    if out is not None:
        try:
            write_report(report, _resolve_path(out, root))
        except OSError as exc:
            print(json.dumps({"ok": False, "error": str(exc)}))
            return 1
    print(json.dumps(report.model_dump()))
    return 0


def report_command(
    run_id: str,
    out: Path | None = None,
    repo_root: Path | None = None,
) -> int:
    """Report the metrics of one run. Returns 0, or 1 on a broken run."""
    if not _valid_run_id(run_id):
        print(json.dumps({"ok": False, "error": f"invalid run id {run_id!r}"}))
        return 1
    root = _root(repo_root)
    try:
        report = build_run_report(root / PROCESSED_DIR / run_id)
    except (ReportError, OSError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 1
    if out is not None:
        try:
            write_run_report(report, _resolve_path(out, root))
        except OSError as exc:
            print(json.dumps({"ok": False, "error": str(exc)}))
            return 1
    print(json.dumps(report.model_dump()))
    return 0


def _root(root: Path | None) -> Path:
    """The repository root to work in: the injected one, or the real one."""
    return Path(root) if root is not None else repo_root()


def _resolve_path(path: Path, root: Path) -> Path:
    """A path from the command line, resolved against the repository root."""
    path = Path(path)
    return path if path.is_absolute() else root / path


def _valid_run_id(run_id: str) -> bool:
    """A run id must be a plain name: no separator, no parent link."""
    return bool(RUN_ID_PATTERN.match(run_id)) and ".." not in run_id


def _default_run_id() -> str:
    """A run id from the UTC time and four random hex characters."""
    return f"ph-{utcnow().strftime('%Y%m%dT%H%M%SZ')}-{secrets.token_hex(2)}"


def _read_fetch_records(path: Path) -> list[FetchRecord]:
    """Read the fetch log, one FetchRecord per line."""
    if not path.is_file():
        return []
    records: list[FetchRecord] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            records.append(FetchRecord.model_validate_json(line))
        except ValidationError as exc:
            raise ValueError(
                f"{path}: line {number} is not a fetch record: {exc}"
            ) from exc
    return records


def _select_fetches(
    records: Iterable[FetchRecord], *, source_id: str | None, limit: int | None
) -> list[FetchRecord]:
    """The fetches to extract: a 200 with stored bytes that was not a 304.

    The same URL can be fetched more than once, so only the first record of
    each fetch id is used. A run over the whole log must not pay for the same
    document twice.
    """
    if limit is not None and limit <= 0:
        return []
    selected: list[FetchRecord] = []
    seen: set[str] = set()
    for record in records:
        if record.status != 200 or not record.raw_path or record.not_modified:
            continue
        if source_id is not None and record.source_id != source_id:
            continue
        if record.fetch_id in seen:
            continue
        seen.add(record.fetch_id)
        selected.append(record)
        if limit is not None and len(selected) >= limit:
            break
    return selected


def _read_candidates(path: Path) -> list[ExtractedCandidate]:
    """Read the candidates of one run, one ExtractedCandidate per line."""
    candidates: list[ExtractedCandidate] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            candidates.append(ExtractedCandidate.model_validate_json(line))
        except ValidationError as exc:
            raise ValueError(
                f"{path}: line {number} is not a candidate: {exc}"
            ) from exc
    return candidates


def _document_reject(
    config: ExtractionConfig, record: FetchRecord, exc: DocumentError
) -> ExtractionReject:
    """The rejection record of a capture that could not become a document."""
    basis = f"{record.fetch_id}|{exc.reason_code}|{exc.detail}"
    return ExtractionReject(
        reject_id="rej_" + hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16],
        doc_id=None,
        fetch_id=record.fetch_id,
        source_id=record.source_id,
        reason_code=exc.reason_code,
        detail=exc.detail,
        extractor_version=config.extractor_version,
        prompt_version=config.prompt_version,
        created_at=utcnow().isoformat(),
    )


def _source_reject(
    config: ExtractionConfig, record: FetchRecord, exc: SourceBlocked
) -> ExtractionReject:
    """The rejection record of a capture whose source may not be analyzed."""
    basis = f"{record.fetch_id}|{exc.reason_code}|{exc}"
    return ExtractionReject(
        reject_id="rej_" + hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16],
        doc_id=None,
        fetch_id=record.fetch_id,
        source_id=record.source_id,
        reason_code=exc.reason_code,
        detail=str(exc),
        extractor_version=config.extractor_version,
        prompt_version=config.prompt_version,
        created_at=utcnow().isoformat(),
    )


def _provider_reject(config: ExtractionConfig, detail: str) -> ExtractionReject:
    """The rejection record of a run that could not reach the provider."""
    basis = f"provider_check_failed|{detail}"
    return ExtractionReject(
        reject_id="rej_" + hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16],
        doc_id=None,
        fetch_id=None,
        source_id=None,
        reason_code="provider_check_failed",
        detail=detail,
        provider=config.provider_id,
        model=config.model_id,
        extractor_version=config.extractor_version,
        prompt_version=config.prompt_version,
        created_at=utcnow().isoformat(),
    )


def _write_jsonl(path: Path, records: Iterable[BaseModel]) -> None:
    """Write one JSON object per line."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for record in records:
            handle.write(record.model_dump_json() + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "version":
        print(__version__)
        return 0
    if args.command == "doctor":
        return doctor()
    if args.command == "sources":
        return sources_command()
    if args.command == "fetch":
        return fetch_command(args.source_id, args.url)
    if args.command == "extract":
        return extract_command(
            limit=args.limit, source_id=args.source_id, run_id=args.run_id
        )
    if args.command == "review-sheet":
        return review_sheet_command(args.run_id, Path(args.out), limit=args.limit)
    if args.command == "eval":
        return eval_command(
            Path(args.labels),
            Path(args.out) if args.out else None,
            quote_failures=args.quote_failures,
        )
    if args.command == "report":
        return report_command(args.run_id, Path(args.out) if args.out else None)
    parser.print_help()
    return 0

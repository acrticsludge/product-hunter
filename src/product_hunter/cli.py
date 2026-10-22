from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import secrets
from collections.abc import Iterable
from dataclasses import asdict
from datetime import date
from pathlib import Path

import httpx
from pydantic import BaseModel, ValidationError

from product_hunter import __version__
from product_hunter.config import (
    ExtractionConfig,
    RoutingConfig,
    database_path,
    load_config,
    load_routing_config,
    repo_root,
)
from product_hunter.db import (
    Store,
    connect,
    current_version,
    list_jobs,
    list_projection_tasks,
    process_outbox,
    recover_expired_leases,
    recover_projection_leases,
)
from product_hunter.db.model_routing import RunProvenance
from product_hunter.documents import (
    Document,
    DocumentChunk,
    DocumentError,
    load_fetch_documents,
)
from product_hunter.eval import (
    EvalError,
    Thresholds,
    compare_runs,
    label_status,
    load_comparison,
    load_dataset,
    load_run,
    load_run_spec,
    load_thresholds,
    render_label_status,
    run_dataset,
    write_benchmark_report,
    write_comparison,
    write_run,
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
from product_hunter.llm import (
    build_generator,
    build_generator_for_selection,
    build_router,
)
from product_hunter.llm.base import ProviderError, TextGenerator
from product_hunter.llm.catalog import (
    CatalogError,
    ModelCatalog,
    ModelEntry,
    ModelIdentity,
)
from product_hunter.llm.discovery import ModelDiscovery
from product_hunter.llm.routing import (
    FallbackGenerator,
    GenerationAttempt,
    ModelExecution,
    ModelRole,
    ModelRouter,
    ModelSelection,
    SelectionSource,
)
from product_hunter.observability import (
    ComparisonError,
    collect_metrics,
    collect_trace,
    record_comparison,
    write_metrics_report,
    write_trace_report,
)
from product_hunter.projection import Projector, SqliteProjectionSource
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
# Where the vault lives when no path is given. The data directory is
# ignored by git, so the generated notes never reach the repository.
VAULT_DIR = Path("data") / "vault" / "Product-Hunter-Vault"
# The threshold file the benchmark falls back to when none is named.
BENCH_THRESHOLDS = Path("eval") / "thresholds.yaml"

# A run id is a plain name. Path separators and a parent link are refused so
# a run id can never name a directory outside the processed tree.
RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]+$")

# The pipeline stage an extraction call belongs to. The store names the same
# stage for the run it imports.
PAIN_EXTRACTION_STAGE = "PAIN_EXTRACTION"

# The call statuses that mean the provider answered. The call record of such
# a call belongs to the attempt that answered it.
ANSWERED_CALL_STATUSES = ("ok", "invalid_json", "schema_invalid")


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
    db_parser = commands.add_parser("db", help="authoritative SQLite store")
    db_commands = db_parser.add_subparsers(dest="db_command", required=True)
    db_init = db_commands.add_parser("init", help="create and migrate the database")
    db_init.add_argument("--path", dest="db_path", default=None)
    db_status = db_commands.add_parser("status", help="show schema version and counts")
    db_status.add_argument("--path", dest="db_path", default=None)
    db_import = db_commands.add_parser(
        "import-run", help="import one processed extraction run"
    )
    db_import.add_argument("--run", dest="run_id", required=True)
    db_import.add_argument("--fetch-log", dest="fetch_log", default=None)
    db_import.add_argument("--path", dest="db_path", default=None)
    db_recover = db_commands.add_parser(
        "recover", help="requeue expired jobs and projections"
    )
    db_recover.add_argument("--path", dest="db_path", default=None)
    db_jobs = db_commands.add_parser("jobs", help="list jobs as JSON")
    db_jobs.add_argument("--status", default=None)
    db_jobs.add_argument("--path", dest="db_path", default=None)
    db_outbox = db_commands.add_parser("outbox", help="list projection tasks as JSON")
    db_outbox.add_argument("--status", default=None)
    db_outbox.add_argument("--path", dest="db_path", default=None)
    db_check = db_commands.add_parser("check", help="run SQLite integrity checks")
    db_check.add_argument("--path", dest="db_path", default=None)
    project_parser = commands.add_parser(
        "project", help="build the Obsidian projection of the store"
    )
    project_parser.add_argument("--db", dest="db_path", default=None)
    project_parser.add_argument("--vault", dest="vault", default=None)
    project_parser.add_argument(
        "--outbox", action="store_true", help="drain the pending obsidian tasks first"
    )
    project_parser.add_argument(
        "--as-of", dest="as_of", default=None, help="the reference date, YYYY-MM-DD"
    )
    research_parser = commands.add_parser("research", help="control research runs")
    research_parser.add_argument("action")
    research_parser.add_argument("target", nargs="?", default=None)
    research_parser.add_argument("--objective", default=None)
    research_parser.add_argument("--chat-id", dest="chat_id", default=None)
    research_parser.add_argument("--limit", type=int, default=50)
    models_parser = commands.add_parser(
        "models", help="list the model catalog as JSON"
    )
    models_parser.add_argument(
        "--refresh", action="store_true", help="re-discover the catalog first"
    )
    models_parser.add_argument(
        "--role", default=None, help="print the resolved chain of one role"
    )
    bench_parser = commands.add_parser(
        "bench", help="score and compare benchmark runs"
    )
    bench_commands = bench_parser.add_subparsers(dest="bench_command", required=True)
    bench_run = bench_commands.add_parser(
        "run", help="score one model's outputs against a dataset"
    )
    bench_run.add_argument("--cases", dest="cases", required=True)
    bench_run.add_argument("--spec", dest="spec", required=True)
    bench_run.add_argument("--out", dest="out", required=True)
    bench_run.add_argument("--thresholds", dest="thresholds", default=None)
    bench_compare = bench_commands.add_parser(
        "compare", help="compare two run results and detect regressions"
    )
    bench_compare.add_argument("--baseline", dest="baseline", required=True)
    bench_compare.add_argument("--candidate", dest="candidate", required=True)
    bench_compare.add_argument("--out", dest="out", required=True)
    bench_compare.add_argument("--thresholds", dest="thresholds", default=None)
    bench_compare.add_argument(
        "--db",
        dest="db_path",
        default=None,
        help="also record the comparison in the store; creates and migrates the database when it is absent",
    )
    bench_report = bench_commands.add_parser(
        "report", help="write the benchmark report of one run"
    )
    bench_report.add_argument("--run", dest="run_id", required=True)
    bench_report.add_argument("--out", dest="out", required=True)
    bench_report.add_argument("--compare", dest="compare", default=None)
    bench_labels = bench_commands.add_parser(
        "labels", help="print the label status of a dataset"
    )
    bench_labels.add_argument("--cases", dest="cases", required=True)
    bench_labels.add_argument("--out", dest="out", default=None)
    observe_parser = commands.add_parser(
        "observe", help="read the metrics and the trace of the store"
    )
    observe_commands = observe_parser.add_subparsers(
        dest="observe_command", required=True
    )
    observe_metrics = observe_commands.add_parser(
        "metrics", help="read the twenty metric families as JSON"
    )
    observe_metrics.add_argument("--run", dest="run_id", default=None)
    observe_metrics.add_argument("--db", dest="db_path", default=None)
    observe_metrics.add_argument("--out", dest="out", default=None)
    observe_trace = observe_commands.add_parser(
        "trace", help="read the chain of one run as JSON"
    )
    observe_trace.add_argument("--run", dest="run_id", required=True)
    observe_trace.add_argument("--stage", dest="stage", default=None)
    observe_trace.add_argument("--db", dest="db_path", default=None)
    observe_trace.add_argument("--out", dest="out", default=None)
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


def models_command(
    *,
    refresh: bool = False,
    role: str | None = None,
    config: ExtractionConfig | None = None,
    routing_config: RoutingConfig | None = None,
    discovery: ModelDiscovery | None = None,
) -> int:
    """Print the model catalog, or the resolved chain of one role, as JSON.

    Returns 0 when the catalog was printed, or when the role resolved to a
    model. Returns 1 when the catalog could not be discovered or the role has
    no available model.
    """
    try:
        config = config if config is not None else load_config()
        routing_config = (
            routing_config if routing_config is not None else load_routing_config()
        )
    except Exception as exc:
        print(json.dumps({"ok": False, "error": f"config: {exc}"}))
        return 1
    try:
        router = build_router(routing_config, config, discovery=discovery)
        catalog = router.catalog_source.catalog(force=refresh)
    except (CatalogError, ProviderError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 1
    if role is None:
        print(
            json.dumps(
                {
                    "ok": True,
                    "catalog_version": catalog.catalog_version,
                    "source": catalog.source,
                    "server_version": catalog.server_version,
                    "discovered_at": catalog.discovered_at.isoformat(),
                    "model_count": len(catalog.entries),
                    "models": [_model_payload(entry) for entry in catalog.entries],
                }
            )
        )
        return 0
    try:
        resolved = ModelRole(role)
    except ValueError:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": f"unknown role {role!r}",
                    "roles": [known.value for known in ModelRole],
                }
            )
        )
        return 1
    chain = router.chain(resolved)
    payload = {
        "ok": bool(chain),
        "role": resolved.value,
        "catalog_version": catalog.catalog_version,
        "server_version": catalog.server_version,
        "chain": [_selection_payload(selection) for selection in chain],
        "selected": _selection_payload(chain[0]) if chain else None,
    }
    if not chain:
        payload["error"] = f"no available model for role {resolved.value!r}"
    print(json.dumps(payload))
    return 0 if chain else 1


def _model_payload(entry: ModelEntry) -> dict[str, object]:
    """One model of the catalog, with its availability."""
    return {
        "provider_id": entry.identity.provider_id,
        "model_id": entry.identity.model_id,
        "name": entry.name,
        "family": entry.family,
        "status": entry.status,
        "enabled": entry.enabled,
        "availability": entry.availability.value,
        "context_limit": entry.context_limit,
        "output_limit": entry.output_limit,
        "cost_input": entry.cost_input,
        "cost_output": entry.cost_output,
        "released_at": entry.released_at.isoformat() if entry.released_at else None,
    }


def _selection_payload(selection: ModelSelection) -> dict[str, object]:
    """One resolved candidate of a role chain."""
    return {
        "provider_id": selection.identity.provider_id,
        "model_id": selection.identity.model_id,
        "selection_source": selection.selection_source.value,
        "fallback_index": selection.fallback_index,
        "is_fallback": selection.is_fallback,
    }


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
    routing_config: RoutingConfig | None = None,
    discovery: ModelDiscovery | None = None,
) -> int:
    """Extract pain-point candidates from the stored fetches.

    The model is a runtime decision: the router resolves the extraction role
    against the live catalogue and the generator runs the model it selected.
    A router that cannot answer never stops the run: the model pinned in the
    extraction config runs it, and the manifest and a rejection record say
    why. A generator the caller supplies is used as it is, and the router is
    not consulted at all.

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
    except Exception as exc:
        print(json.dumps({"ok": False, "error": f"config: {exc}"}))
        return 1
    try:
        records = _read_fetch_records(root / FETCH_LOG)
    except ValueError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 1
    try:
        registry = load_registry()
    except Exception as exc:
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
    selection: ModelSelection | None = None
    catalog: ModelCatalog | None = None
    routing_failure: str | None = None
    routing_version = ""
    fallback: FallbackGenerator | None = None
    if generator is None:
        # The model is a runtime decision: the router resolves the extraction
        # role against the live catalogue, and a call that fails is retried
        # with the next candidate of the chain.
        try:
            routing = (
                routing_config
                if routing_config is not None
                else load_routing_config()
            )
            routing_version = routing.config_version
            router = build_router(routing, config, discovery=discovery)
            selection = router.select(ModelRole.extraction)
            fallback = FallbackGenerator(
                router,
                ModelRole.extraction,
                lambda chosen: build_generator_for_selection(config, chosen),
            )
            generator = fallback
            catalog = _router_catalog(router, selection)
        except (OSError, ValueError, ProviderError) as exc:
            # A broken router must never stop an extraction run.
            routing_failure = f"{type(exc).__name__}: {exc}"
        if generator is None:
            # The router could not answer: the model pinned in the extraction
            # config runs the extraction.
            try:
                generator = build_generator(config)
            except ProviderError as exc:
                error = str(exc)
                generator = None

    if selection is None and routing_failure is not None:
        # The router could not answer, so the run happened on the model pinned
        # in the extraction config, and that is the model its records name.
        selection = _default_selection(config)

    # The generator is bound to the model the router selected, and the call
    # records are written from the config the extraction reads, so both name
    # the model the run is bound to. A call that fails is attributed to the
    # model that failed, and the error carries it.
    run_config = _bound_config(config, selection)

    if generator is not None:
        # The provider is verified before any document text is sent. A dead or
        # mismatched provider must fail here, not after a model call.
        try:
            generator.check()
        except ProviderError as exc:
            error = f"provider check failed: {exc}"
            # The run still leaves a record, so a dead provider is never a
            # silent failure.
            rejects.append(
                _run_reject(config, error, selection, "provider_check_failed")
            )
            generator = None

    if routing_failure is not None:
        # The run says why the router could not choose a model.
        rejects.append(
            _run_reject(config, routing_failure, selection, "routing_failed")
        )

    calls: list[ExtractionCall] = []
    candidates: list[ExtractedCandidate] = []
    chunks: list[DocumentChunk] = []
    if generator is not None and documents:
        batch = extract_documents(documents, generator, run_config)
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
    # The manifest names the model the router chose, not the model pinned in
    # the config, and not the model that happened to answer the last call.
    # The top-level model is the policy of the run: it is what the router
    # decided, and it does not change with provider luck. A run whose router
    # failed ran the pinned model, and its selection is the pinned model.
    # Which model made which call is in the execution rows.
    provider_id, model_id = _model_pair(config, selection)
    manifest = {
        "run_id": run_id,
        "started_at": started,
        "completed_at": utcnow().isoformat(),
        "provider": provider_id,
        "model": model_id,
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
    if selection is not None:
        # The selection survives the run, so the model that ran it can be
        # explained afterwards.
        manifest["model_routing"] = _routing_block(
            selection, config_version=routing_version, reason=routing_failure
        )
        if fallback is not None:
            # One row per provider call: a call that failed is a row of its
            # own, and the call that answered is the one the chunk records.
            rows = _attempt_executions(
                run_id,
                fallback.attempts,
                calls,
                config_version=routing_version,
                prompt_version=config.prompt_version,
            )
        else:
            rows = _model_executions(run_id, calls, selection, routing_version)
        _write_jsonl(out_dir / "model_executions.jsonl", rows)
    if catalog is not None:
        # The catalogue the run decided against, so the import can rebuild the
        # model inventory from it. A run whose router failed has no catalogue
        # to record, and an empty one would be a lie.
        (out_dir / "model_catalog.json").write_text(
            catalog.model_dump_json() + "\n", encoding="utf-8"
        )
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
    except Exception as exc:
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


def bench_run_command(
    cases: Path,
    spec: Path,
    out: Path,
    thresholds: Path | None = None,
    *,
    repo_root: Path | None = None,
) -> int:
    """Score one model's outputs against a dataset.

    Returns 1 when a threshold failed, or when a file could not be read. It
    returns 0 when the thresholds passed and when nothing could be measured,
    because a run with no evaluable threshold has not failed.
    """
    root = _root(repo_root)
    threshold_path = (
        _resolve_path(thresholds, root)
        if thresholds is not None
        else root / BENCH_THRESHOLDS
    )
    try:
        dataset = load_dataset(_resolve_path(cases, root))
        run_spec = load_run_spec(_resolve_path(spec, root))
        # A missing threshold file is not a failure: the run is scored and the
        # result says that no threshold was checked.
        # exists(), not is_file(): a path that is present but not a readable
        # file must be loud, not silently read as "no threshold file".
        measured = threshold_path.exists()
        limits = load_thresholds(threshold_path) if measured else Thresholds()
    except (OSError, ValueError, EvalError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 1
    try:
        run = run_dataset(dataset, run_spec, limits)
    except (OSError, ValueError, EvalError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 1
    if not measured:
        run.warnings.append(
            f"no threshold file at {threshold_path}; no threshold was checked"
        )
    try:
        write_run(_resolve_path(out, root), run)
    except OSError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 1
    payload = run.model_dump()
    payload["ok"] = run.passed is not False
    payload["reason"] = _run_reason(run)
    print(json.dumps(payload))
    return 1 if run.passed is False else 0


def bench_compare_command(
    baseline: Path,
    candidate: Path,
    out: Path,
    thresholds: Path | None = None,
    *,
    db_path: str | Path | None = None,
    repo_root: Path | None = None,
) -> int:
    """Compare two run results and report a regression.

    Returns 1 when the verdict is a regression, and 0 otherwise.

    A database path adds one thing: the comparison is written to the store
    as well, so the answer survives the session that produced it. Without a
    database path the command does exactly what it did before. When the database
    does not exist, it is created and migrated to the current schema first, so
    the --db flag works on a fresh machine.
    """
    root = _root(repo_root)
    threshold_path = (
        _resolve_path(thresholds, root)
        if thresholds is not None
        else root / BENCH_THRESHOLDS
    )
    try:
        before = load_run(_resolve_path(baseline, root))
        after = load_run(_resolve_path(candidate, root))
        # exists(), not is_file(): a path that is present but not a readable
        # file must be loud, not silently read as "no threshold file".
        measured = threshold_path.exists()
        limits = load_thresholds(threshold_path) if measured else Thresholds()
        comparison = compare_runs(before, after, limits)
    except (OSError, ValueError, EvalError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 1
    try:
        write_comparison(_resolve_path(out, root), comparison)
    except OSError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 1
    comparison_id = None
    if db_path is not None:
        try:
            store = Store.open(_db_path(db_path, root))
            try:
                comparison_id = record_comparison(
                    store.conn, before, after, comparison
                )
            finally:
                store.close()
        except (OSError, ValueError, EvalError, ComparisonError) as exc:
            print(json.dumps({"ok": False, "error": str(exc)}))
            return 1
    payload = comparison.model_dump()
    payload["ok"] = comparison.verdict != "regression"
    payload["reason"] = _compare_reason(comparison)
    if comparison_id is not None:
        payload["comparison_id"] = comparison_id
    print(json.dumps(payload))
    return 1 if comparison.verdict == "regression" else 0


def bench_report_command(
    run_id: Path,
    out: Path,
    compare: Path | None = None,
    *,
    repo_root: Path | None = None,
) -> int:
    """Write the benchmark report of one run. Returns 0, or 1 on a bad file."""
    root = _root(repo_root)
    try:
        run = load_run(_resolve_path(run_id, root))
        comparison = (
            load_comparison(_resolve_path(compare, root))
            if compare is not None
            else None
        )
    except (OSError, ValueError, EvalError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 1
    target = _resolve_path(out, root)
    try:
        write_benchmark_report(run, target, comparison)
    except OSError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 1
    print(
        json.dumps(
            {
                "ok": True,
                "run_id": run.run_id,
                "out": str(target),
                "verdict": _verdict(run.passed),
                "comparison": comparison.model_dump() if comparison else None,
            }
        )
    )
    return 0


def bench_labels_command(
    cases: Path,
    out: Path | None = None,
    *,
    repo_root: Path | None = None,
) -> int:
    """Print the label status of a dataset. Returns 0, or 1 on a bad file."""
    root = _root(repo_root)
    try:
        dataset = load_dataset(_resolve_path(cases, root))
    except (OSError, ValueError, EvalError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 1
    status = label_status(dataset)
    if out is not None:
        try:
            target = _resolve_path(out, root)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(
                render_label_status(status), encoding="utf-8", newline="\n"
            )
        except OSError as exc:
            print(json.dumps({"ok": False, "error": str(exc)}))
            return 1
    print(json.dumps(status.model_dump()))
    return 0


def observe_metrics_command(
    *,
    run_id: str | None = None,
    db_path: str | Path | None = None,
    out: str | Path | None = None,
    repo_root: Path | None = None,
) -> int:
    """Read the metric families of the store. Returns 0, or 1 on failure.

    The database has to exist: this command reads a store, it never creates
    one. The report is printed as JSON and, when a path is given, written as
    markdown beside it.
    """
    root = _root(repo_root)
    path = _db_path(db_path, root)
    if not _require_db(path):
        return 1
    try:
        conn = connect(path, read_only=True)
        try:
            report = collect_metrics(conn, run_id=run_id)
        finally:
            conn.close()
    except (OSError, sqlite3.Error, ValueError) as exc:
        print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}))
        return 1
    if out is not None:
        try:
            write_metrics_report(report, _resolve_path(Path(out), root))
        except OSError as exc:
            print(json.dumps({"ok": False, "error": str(exc)}))
            return 1
    payload = report.model_dump(mode="json")
    payload["ok"] = True
    print(json.dumps(payload))
    return 0


def observe_trace_command(
    *,
    run_id: str,
    stage: str | None = None,
    db_path: str | Path | None = None,
    out: str | Path | None = None,
    repo_root: Path | None = None,
) -> int:
    """Read the chain of one run. Returns 0, or 1 on failure.

    The database has to exist: this command reads a store, it never creates
    one. The trace is printed as JSON and, when a path is given, written as
    markdown beside it.
    """
    root = _root(repo_root)
    path = _db_path(db_path, root)
    if not _require_db(path):
        return 1
    try:
        conn = connect(path, read_only=True)
        try:
            report = collect_trace(conn, run_id=run_id, stage=stage)
        finally:
            conn.close()
    except (OSError, sqlite3.Error, ValueError) as exc:
        print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}))
        return 1
    if out is not None:
        try:
            write_trace_report(report, _resolve_path(Path(out), root))
        except OSError as exc:
            print(json.dumps({"ok": False, "error": str(exc)}))
            return 1
    payload = report.model_dump(mode="json")
    payload["ok"] = True
    print(json.dumps(payload))
    return 0


def _run_reason(run) -> str:
    """Why a run passed, failed, or measured nothing."""
    if run.passed is True:
        return "the thresholds passed"
    if run.passed is False:
        failed = ", ".join(run.thresholds.failed)
        return f"the thresholds failed: {failed}" if failed else "the thresholds failed"
    return "no threshold could be evaluated"


def _compare_reason(comparison) -> str:
    """Why a comparison says what it says, in one plain sentence."""
    if comparison.verdict == "not_comparable":
        return "the two runs are not comparable"
    if comparison.verdict == "regression":
        names = _moved_metrics(comparison, "regressed")
        if not names:
            return "the candidate regressed"
        return f"the candidate regressed on {', '.join(names)}"
    if comparison.verdict == "improvement":
        names = _moved_metrics(comparison, "improved")
        if not names:
            return "the candidate improved"
        return f"the candidate improved on {', '.join(names)}"
    return "nothing changed"


def _moved_metrics(comparison, status: str) -> list[str]:
    """The task and metric of every metric that moved the way it was asked."""
    return [
        f"{delta.task}.{delta.metric}"
        for delta in comparison.metric_deltas
        if delta.status == status
    ]


def _verdict(passed: bool | None) -> str:
    """The verdict of a run: pass, fail, or not measured."""
    if passed is None:
        return "not measured"
    return "pass" if passed else "fail"


def _run_bench_command(args: argparse.Namespace) -> int:
    if args.bench_command == "run":
        return bench_run_command(
            Path(args.cases),
            Path(args.spec),
            Path(args.out),
            Path(args.thresholds) if args.thresholds else None,
        )
    if args.bench_command == "compare":
        return bench_compare_command(
            Path(args.baseline),
            Path(args.candidate),
            Path(args.out),
            Path(args.thresholds) if args.thresholds else None,
            db_path=args.db_path,
        )
    if args.bench_command == "report":
        return bench_report_command(
            Path(args.run_id),
            Path(args.out),
            Path(args.compare) if args.compare else None,
        )
    if args.bench_command == "labels":
        return bench_labels_command(
            Path(args.cases), Path(args.out) if args.out else None
        )
    return 1


def _run_observe_command(args: argparse.Namespace) -> int:
    if args.observe_command == "metrics":
        return observe_metrics_command(
            run_id=args.run_id,
            db_path=args.db_path,
            out=args.out,
        )
    if args.observe_command == "trace":
        return observe_trace_command(
            run_id=args.run_id,
            stage=args.stage,
            db_path=args.db_path,
            out=args.out,
        )
    return 1


def _schema_tables(conn) -> list[str]:
    """Every table in the schema, in name order.

    The list is read from the database rather than written down here, so a
    migration that adds a table shows up in ``ph db status`` without anyone
    editing this file. A table name that is not a plain identifier is
    skipped: the name is interpolated into the count query below.
    """
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' "
        "AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()
    return [
        row["name"]
        for row in rows
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", row["name"] or "")
    ]


def db_init_command(
    *, db_path: str | Path | None = None, repo_root: Path | None = None
) -> int:
    """Create and migrate the authoritative database. Returns 0 or 1."""
    root = _root(repo_root)
    path = _db_path(db_path, root)
    try:
        store = Store.open(path)
        try:
            version = current_version(store.conn)
        finally:
            store.close()
    except Exception as exc:
        print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}))
        return 1
    print(json.dumps({"ok": True, "path": str(path), "version": version}))
    return 0


def db_status_command(
    *, db_path: str | Path | None = None, repo_root: Path | None = None
) -> int:
    """Show schema version and row counts without creating the database."""
    root = _root(repo_root)
    path = _db_path(db_path, root)
    if not path.is_file():
        print(json.dumps({"ok": False, "error": f"database does not exist: {path}"}))
        return 1
    try:
        conn = connect(path, read_only=True)
        try:
            version = current_version(conn)
            if version == 0:
                print(
                    json.dumps(
                        {"ok": False, "error": "database is not migrated", "path": str(path)}
                    )
                )
                return 1
            counts = {
                table: int(
                    conn.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0]
                )
                for table in _schema_tables(conn)
            }
            jobs = {
                row["status"]: int(row["n"])
                for row in conn.execute(
                    "SELECT status, count(*) AS n FROM jobs GROUP BY status"
                )
            }
            projections = {
                row["status"]: int(row["n"])
                for row in conn.execute(
                    "SELECT status, count(*) AS n FROM projection_tasks GROUP BY status"
                )
            }
        finally:
            conn.close()
    except Exception as exc:
        print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}))
        return 1
    print(
        json.dumps(
            {
                "ok": True,
                "path": str(path),
                "version": version,
                "tables": counts,
                "jobs": jobs,
                "projections": projections,
            }
        )
    )
    return 0


def db_import_run_command(
    run_id: str,
    *,
    fetch_log: str | Path | None = None,
    db_path: str | Path | None = None,
    repo_root: Path | None = None,
    registry=None,
) -> int:
    """Import one processed run into SQLite. Returns 0 or 1.

    The run's model provenance is imported with it, in the same transaction:
    the model calls, the model the role resolved to, and the catalogue the
    run decided against. A provenance file that cannot be read refuses the
    import: the run does not land without the provenance it recorded, and
    the message names the file to fix.
    """
    if not _valid_run_id(run_id):
        print(json.dumps({"ok": False, "error": f"invalid run id {run_id!r}"}))
        return 1
    root = _root(repo_root)
    path = _db_path(db_path, root)
    if not _require_db(path):
        return 1
    run_dir = root / PROCESSED_DIR / run_id
    fetch_path = (
        _resolve_path(fetch_log, root) if fetch_log is not None else root / FETCH_LOG
    )
    try:
        registry = registry if registry is not None else load_registry()
        try:
            provenance = _run_provenance(run_dir)
        except Exception as exc:
            # The provenance is refused on its own: the run is not imported,
            # because a run without the provenance it recorded is a lie. The
            # message names the file and what to do about it.
            print(
                json.dumps(
                    {
                        "ok": False,
                        "error": f"provenance: {exc}; the run was not imported. "
                        "Fix the file or remove it, then import the run again.",
                    }
                )
            )
            return 1
        store = Store.open(path)
        try:
            summary = store.import_run(
                run_dir,
                fetch_log=fetch_path,
                registry=registry,
                provenance=provenance,
            )
        finally:
            store.close()
    except Exception as exc:
        print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}))
        return 1
    print(json.dumps({"ok": True, **asdict(summary)}))
    return 0


def db_recover_command(
    *, db_path: str | Path | None = None, repo_root: Path | None = None
) -> int:
    """Requeue expired job and projection leases. Returns 0 or 1."""
    root = _root(repo_root)
    path = _db_path(db_path, root)
    if not _require_db(path):
        return 1
    try:
        store = Store.open(path)
        try:
            jobs = recover_expired_leases(store.conn)
            projections = recover_projection_leases(store.conn)
        finally:
            store.close()
    except Exception as exc:
        print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}))
        return 1
    print(
        json.dumps(
            {
                "ok": True,
                "jobs_recovered": jobs,
                "projections_recovered": projections,
            }
        )
    )
    return 0


def db_jobs_command(
    *,
    status: str | None = None,
    db_path: str | Path | None = None,
    repo_root: Path | None = None,
) -> int:
    root = _root(repo_root)
    path = _db_path(db_path, root)
    if not _require_db(path):
        return 1
    try:
        store = Store.open(path)
        try:
            jobs = list_jobs(store.conn, status=status)
        finally:
            store.close()
    except Exception as exc:
        print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}))
        return 1
    print(json.dumps([asdict(job) for job in jobs]))
    return 0


def db_outbox_command(
    *,
    status: str | None = None,
    db_path: str | Path | None = None,
    repo_root: Path | None = None,
) -> int:
    root = _root(repo_root)
    path = _db_path(db_path, root)
    if not _require_db(path):
        return 1
    try:
        store = Store.open(path)
        try:
            tasks = list_projection_tasks(store.conn, status=status)
        finally:
            store.close()
    except Exception as exc:
        print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}))
        return 1
    print(json.dumps([asdict(task) for task in tasks]))
    return 0


def db_check_command(
    *, db_path: str | Path | None = None, repo_root: Path | None = None
) -> int:
    root = _root(repo_root)
    path = _db_path(db_path, root)
    if not _require_db(path):
        return 1
    try:
        store = Store.open(path)
        try:
            integrity = store.integrity_check()
            violations = [dict(row) for row in store.foreign_key_violations()]
        finally:
            store.close()
    except Exception as exc:
        print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}))
        return 1
    ok = integrity == "ok" and not violations
    print(
        json.dumps(
            {
                "ok": ok,
                "integrity": integrity,
                "foreign_key_violations": violations,
            }
        )
    )
    return 0 if ok else 1


def _run_db_command(args: argparse.Namespace) -> int:
    if args.db_command == "init":
        return db_init_command(db_path=args.db_path)
    if args.db_command == "status":
        return db_status_command(db_path=args.db_path)
    if args.db_command == "import-run":
        return db_import_run_command(
            args.run_id, fetch_log=args.fetch_log, db_path=args.db_path
        )
    if args.db_command == "recover":
        return db_recover_command(db_path=args.db_path)
    if args.db_command == "jobs":
        return db_jobs_command(status=args.status, db_path=args.db_path)
    if args.db_command == "outbox":
        return db_outbox_command(status=args.status, db_path=args.db_path)
    if args.db_command == "check":
        return db_check_command(db_path=args.db_path)
    return 1


def project_command(
    *,
    db_path: str | Path | None = None,
    vault: str | Path | None = None,
    outbox: bool = False,
    as_of: str | None = None,
    repo_root: Path | None = None,
) -> int:
    """Build the Obsidian vault from the store. Returns 0, or 1 on failure.

    The vault is a projection of the whole store, so the command rebuilds it
    rather than patching it. A run that changes nothing writes nothing.
    """
    root = _root(repo_root)
    path = _db_path(db_path, root)
    if not _require_db(path):
        return 1
    if not _valid_as_of(as_of):
        print(
            json.dumps(
                {"ok": False, "error": f"the reference date is not a date: {as_of!r}"}
            )
        )
        return 1
    vault_root = (
        _resolve_path(Path(vault), root) if vault is not None else root / VAULT_DIR
    )
    processed = 0
    try:
        # The outbox owns the task bookkeeping and needs a connection it can
        # write with. The projection reads through its own read-only
        # connection, so it can never write to the database.
        work = connect(path) if outbox else None
        try:
            conn = connect(path, read_only=True)
            try:
                projector = Projector(vault_root, source=SqliteProjectionSource(conn))
                projector.as_of = as_of
                if work is not None:
                    processed = _drain_outbox(work, projector)
                result = projector.last_result
                if result is None:
                    # No task was pending, so the drain built nothing and the
                    # vault still has to be built.
                    result = projector.project()
            finally:
                conn.close()
        finally:
            if work is not None:
                work.close()
    except Exception as exc:
        print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}))
        return 1
    print(
        json.dumps(
            {
                "ok": True,
                "vault": str(vault_root),
                "written": len(result.written),
                "unchanged": len(result.unchanged),
                "removed": len(result.removed),
                "processed": processed,
            }
        )
    )
    return 0


def _drain_outbox(conn, projector: Projector) -> int:
    """Run the pending obsidian tasks through the outbox.

    The outbox owns the task bookkeeping: a projection that raises leaves
    the task pending and schedules the retry. The vault is built once for
    the whole drain, not once per task: it is a projection of the whole
    state, and the state does not change while the outbox runs.
    """
    built = False

    def handle(task) -> None:
        """Build the vault for the first task of the drain, and leave the rest."""
        nonlocal built
        if built:
            return
        projector.handle_projection_task(task)
        built = True

    return process_outbox(
        conn,
        {"obsidian": handle},
        f"ph-project-{os.getpid()}",
        target="obsidian",
        max_tasks=100,
    )


def _require_db(path: Path) -> bool:
    if path.is_file():
        return True
    print(json.dumps({"ok": False, "error": f"database does not exist: {path}"}))
    return False


def _db_path(value: str | Path | None, root: Path) -> Path:
    if value is None:
        return database_path(root)
    path = Path(value).expanduser()
    return path if path.is_absolute() else root / path


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


def _valid_as_of(value: str | None) -> bool:
    """A reference date is a plain date, or nothing at all."""
    if value is None:
        return True
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


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


def _read_model_executions(path: Path) -> list[ModelExecution]:
    """Read the model calls of one run, one ModelExecution per line.

    A run without the file has no model calls to import.
    """
    if not path.is_file():
        return []
    executions: list[ModelExecution] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            executions.append(ModelExecution.model_validate_json(line))
        except ValidationError as exc:
            raise ValueError(
                f"{path}: line {number} is not a model execution: {exc}"
            ) from exc
    return executions


def _run_provenance(run_dir: Path) -> RunProvenance:
    """The routing state one run recorded, to be imported with the run.

    A run without the files has no routing state, and nothing extra is
    written for it.
    """
    block = _read_routing_block(run_dir / "manifest.json")
    selection = _selection_from_block(block) if block else None
    return RunProvenance(
        catalog=_read_model_catalog(run_dir / "model_catalog.json"),
        selections=(selection,) if selection is not None else (),
        executions=tuple(_read_model_executions(run_dir / "model_executions.jsonl")),
    )


def _read_routing_block(path: Path) -> dict[str, object] | None:
    """The model routing block of a run manifest, or None when it has none."""
    if not path.is_file():
        return None
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(manifest, dict):
        return None
    block = manifest.get("model_routing")
    return block if isinstance(block, dict) else None


def _read_model_catalog(path: Path) -> ModelCatalog | None:
    """The catalogue a run recorded, or None when the run has none.

    A run without the file was written before the router recorded a
    catalogue, or it ran on a generator a caller supplied.
    """
    if not path.is_file():
        return None
    try:
        return ModelCatalog.model_validate_json(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ValueError(f"{path}: not a model catalog: {exc}") from exc


def _selection_from_block(block: dict[str, object]) -> ModelSelection | None:
    """The selection a routing block records, or None when it is unusable."""
    try:
        return ModelSelection(
            role=ModelRole(block["role"]),
            identity=ModelIdentity(
                provider_id=block["provider_id"], model_id=block["model_id"]
            ),
            selection_source=SelectionSource(block["selection_source"]),
            fallback_index=int(block["fallback_index"]),  # type: ignore[arg-type]
            is_fallback=bool(block["is_fallback"]),
            catalog_version=str(block["catalog_version"] or ""),
            resolved_at=block["resolved_at"],  # type: ignore[arg-type]
        )
    except (KeyError, TypeError, ValueError):
        return None


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


def _model_pair(
    config: ExtractionConfig, selection: ModelSelection | None
) -> tuple[str, str]:
    """The provider and the model of a run: the one selected, or the pinned one.

    A run whose router answered ran the model the router selected. A run
    whose router failed ran the model pinned in the extraction config.
    """
    if selection is None:
        return config.provider_id, config.model_id
    return selection.identity.provider_id, selection.identity.model_id


def _bound_config(
    config: ExtractionConfig, selection: ModelSelection | None
) -> ExtractionConfig:
    """The config that names the model the run is bound to.

    The generator is built from a copy of the config that carries the
    selected provider and model, so the extraction reads that copy too. A
    call that answered names the model that answered it, and a call that
    failed names the model that failed it, which the error carries.
    """
    if selection is None:
        return config
    return config.model_copy(
        update={
            "provider_id": selection.identity.provider_id,
            "model_id": selection.identity.model_id,
        }
    )


def _run_reject(
    config: ExtractionConfig,
    detail: str,
    selection: ModelSelection | None,
    reason_code: str,
) -> ExtractionReject:
    """The rejection record of a run that made no model call.

    The record says why the run did not use a model: the provider could not
    be reached, or the router could not choose one. It names the model that
    ran: the one the router selected, or the one pinned in the config when
    the router could not answer.
    """
    provider, model = _model_pair(config, selection)
    basis = f"{reason_code}|{detail}"
    return ExtractionReject(
        reject_id="rej_" + hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16],
        doc_id=None,
        fetch_id=None,
        source_id=None,
        reason_code=reason_code,
        detail=detail,
        provider=provider,
        model=model,
        extractor_version=config.extractor_version,
        prompt_version=config.prompt_version,
        created_at=utcnow().isoformat(),
    )


def _router_catalog(
    router: ModelRouter, selection: ModelSelection
) -> ModelCatalog | None:
    """The catalogue the router decided against, or None when there is none.

    The router holds one catalogue and the selection names its version, so a
    catalogue that was re-discovered in between is not the one the decision
    was made against and is not recorded.
    """
    try:
        catalog = router.catalog_source.catalog()
    except (CatalogError, ProviderError):
        return None
    if catalog.catalog_version != selection.catalog_version:
        return None
    return catalog


def _default_selection(config: ExtractionConfig) -> ModelSelection:
    """The selection of the model pinned in the extraction config.

    It is the record of a run whose router could not answer: no catalogue was
    consulted, so the model is the default one and the reason is kept beside
    it.
    """
    return ModelSelection(
        role=ModelRole.extraction,
        identity=ModelIdentity(
            provider_id=config.provider_id, model_id=config.model_id
        ),
        selection_source=SelectionSource.default,
        fallback_index=0,
        is_fallback=False,
        catalog_version="",
        resolved_at=utcnow(),
    )


def _routing_block(
    selection: ModelSelection,
    *,
    config_version: str,
    reason: str | None = None,
) -> dict[str, object]:
    """The manifest block that keeps the selection of a run."""
    block: dict[str, object] = {
        "role": selection.role.value,
        "provider_id": selection.identity.provider_id,
        "model_id": selection.identity.model_id,
        "selection_source": selection.selection_source.value,
        "fallback_index": selection.fallback_index,
        "is_fallback": selection.is_fallback,
        "catalog_version": selection.catalog_version,
        "config_version": config_version,
        "resolved_at": selection.resolved_at.isoformat(),
    }
    if reason is not None:
        block["reason"] = reason
    return block


def _model_executions(
    run_id: str,
    calls: list[ExtractionCall],
    selection: ModelSelection,
    config_version: str,
) -> list[ModelExecution]:
    """One execution row per model call, with the provenance of the run."""
    return [
        ModelExecution(
            execution_id=_execution_id(run_id, call.call_id),
            run_id=run_id,
            stage=PAIN_EXTRACTION_STAGE,
            role=selection.role,
            provider_id=call.provider,
            model_id=call.model,
            selection_source=selection.selection_source,
            fallback_index=selection.fallback_index,
            is_fallback=selection.is_fallback,
            catalog_version=selection.catalog_version,
            prompt_version=call.prompt_version,
            config_version=config_version,
            latency_ms=call.latency_ms,
            status="ok" if call.status == "ok" else "error",
            error=_call_error(call),
            created_at=call.created_at,
        )
        for call in calls
    ]


def _attempt_executions(
    run_id: str,
    attempts: list[GenerationAttempt],
    calls: list[ExtractionCall],
    *,
    config_version: str,
    prompt_version: str,
) -> list[ModelExecution]:
    """One execution row per provider call of the run.

    A call that did not answer is a row of its own, with the reason it
    failed, and so is a candidate the provider refused at the check. The
    call that answered carries the outcome of the chunk it answered, because
    the extraction, not the provider, decides whether an answer is usable.
    Every row names the candidate that made the call, so a run that only
    succeeded on the second candidate says so.
    """
    answered = [call for call in calls if call.status in ANSWERED_CALL_STATUSES]
    rows: list[ModelExecution] = []
    position = 0
    for index, attempt in enumerate(attempts):
        if attempt.ok:
            call = answered[position] if position < len(answered) else None
            position += 1
            status = "ok" if call is None or call.status == "ok" else "error"
            error = None if call is None else _call_error(call)
        else:
            status = "error"
            error = attempt.error
        rows.append(
            ModelExecution(
                execution_id=_attempt_execution_id(run_id, index, attempt),
                run_id=run_id,
                stage=PAIN_EXTRACTION_STAGE,
                role=attempt.selection.role,
                provider_id=attempt.selection.identity.provider_id,
                model_id=attempt.selection.identity.model_id,
                selection_source=attempt.selection.selection_source,
                fallback_index=attempt.selection.fallback_index,
                is_fallback=attempt.selection.is_fallback,
                catalog_version=attempt.selection.catalog_version,
                prompt_version=prompt_version,
                config_version=config_version,
                latency_ms=attempt.latency_ms,
                status=status,
                error=error,
                created_at=attempt.created_at,
            )
        )
    return rows


def _attempt_execution_id(
    run_id: str, position: int, attempt: GenerationAttempt
) -> str:
    """The id of one execution: the run, the attempt's place, and the model."""
    identity = attempt.selection.identity
    basis = f"{run_id}|{position}|{identity.provider_id}|{identity.model_id}"
    return "exec_" + hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16]


def _execution_id(run_id: str, call_id: str) -> str:
    """The id of one execution: the run and the call it belongs to."""
    basis = f"{run_id}|{call_id}"
    return "exec_" + hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16]


def _call_error(call: ExtractionCall) -> str | None:
    """The failure of a call: its status code and its message.

    A call that succeeded has no error.
    """
    if call.status == "ok":
        return None
    if call.error:
        return f"{call.status}: {call.error}"
    return call.status


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
    if args.command == "models":
        return models_command(refresh=args.refresh, role=args.role)
    if args.command == "bench":
        return _run_bench_command(args)
    if args.command == "db":
        return _run_db_command(args)
    if args.command == "observe":
        return _run_observe_command(args)
    if args.command == "project":
        return project_command(
            db_path=args.db_path,
            vault=args.vault,
            outbox=args.outbox,
            as_of=args.as_of,
        )
    if args.command == "research":
        # The import stays inside the branch, so this module loads the same
        # way when the registry is absent.
        from product_hunter.registry import main as registry_main

        argv = ["research", args.action]
        if args.target is not None:
            argv.append(args.target)
        if args.objective is not None:
            argv += ["--objective", args.objective]
        if args.chat_id is not None:
            argv += ["--chat-id", args.chat_id]
        argv += ["--limit", str(args.limit)]
        return registry_main(argv)
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

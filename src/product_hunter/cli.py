from __future__ import annotations

import argparse
import json
from pathlib import Path

import httpx

from product_hunter import __version__
from product_hunter.config import ExtractionConfig, load_config, repo_root
from product_hunter.fetch import Fetcher, FetchError, JsonlLog, utcnow
from product_hunter.llm import build_generator
from product_hunter.llm.base import ProviderError, TextGenerator
from product_hunter.raw_store import RawStore
from product_hunter.safety import UrlBlocked
from product_hunter.sources import SourceBlocked, load_registry

FETCH_LOG = Path("data") / "raw" / "fetches.jsonl"
REJECTION_LOG = Path("data") / "raw" / "rejections.jsonl"


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
    parser.print_help()
    return 0

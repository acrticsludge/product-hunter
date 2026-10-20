from __future__ import annotations

import argparse
import json

from product_hunter import __version__
from product_hunter.config import ExtractionConfig, load_config
from product_hunter.llm import build_generator
from product_hunter.llm.base import ProviderError, TextGenerator


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ph",
        description="Product Hunter: evidence-driven pain point research.",
    )
    commands = parser.add_subparsers(dest="command")
    commands.add_parser("doctor", help="check the configured provider and model")
    commands.add_parser("version", help="print the package version")
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


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "version":
        print(__version__)
        return 0
    if args.command == "doctor":
        return doctor()
    parser.print_help()
    return 0

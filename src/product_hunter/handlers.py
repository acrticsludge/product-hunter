"""Worker handlers for the Product Hunter pipeline.

This module is the glue the autonomy engine needs: it turns the library
functions into the callables the worker claims jobs for. Each handler
receives a job payload and either completes the work or raises, so the
worker can record the failure and retry.
"""

from __future__ import annotations

import json
from typing import Any


def _noop_handler(payload: dict[str, Any]) -> None:
    """A handler that records the job as done without doing work.

    This exists so the supervised worker can be started and exercised end to
    end. Real handlers are added per stage as the pipeline is wired.
    """
    return None


def build_handlers() -> dict[str, Any]:
    """The handler mapping the worker uses."""
    return {
        "DISCOVERY": _noop_handler,
        "COLLECTION": _noop_handler,
        "NORMALIZATION": _noop_handler,
        "DEDUPLICATION": _noop_handler,
        "ENTITY_RESOLUTION": _noop_handler,
        "PAIN_EXTRACTION": _noop_handler,
        "EMBEDDING": _noop_handler,
        "CLUSTERING": _noop_handler,
        "INDEPENDENCE_ANALYSIS": _noop_handler,
        "VALIDATION": _noop_handler,
        "COMPETITOR_RESEARCH": _noop_handler,
        "COUNTER_EVIDENCE": _noop_handler,
        "SCORING": _noop_handler,
        "REPORTING": _noop_handler,
        "OBSIDIAN_SYNC": _noop_handler,
    }
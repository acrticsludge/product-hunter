"""Production integration point for the research contract's promotion gate.

This module is the single place a run's result is promoted. It delegates to
``require_promotable``, which is fail-closed: an invalid or incomplete run
raises ``GateBlocked`` instead of returning a verdict the caller could
ignore. Later sessions wire this into the terminal stage of the pipeline;
for now it is available as the authoritative entry point.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from product_hunter.db.research_gate import GateBlocked, require_promotable
from product_hunter.research.contract import ResearchContract, load_research_contract


def promote_run(
    conn: sqlite3.Connection,
    run_id: str,
    *,
    contract: ResearchContract | None = None,
    contract_path: str | Path | None = None,
    worker_id: str | None = None,
    lease_epoch: int | None = None,
) -> None:
    """Promote a run's result, or raise ``GateBlocked`` if it may not be.

    The worker is the safety authority: a run that does not pass the gate is
    never promoted, and the exception carries the violation codes.
    """
    resolved = contract if contract is not None else load_research_contract(contract_path)
    require_promotable(
        conn,
        resolved,
        run_id,
        worker_id=worker_id,
        lease_epoch=lease_epoch,
    )


__all__ = ["GateBlocked", "promote_run"]

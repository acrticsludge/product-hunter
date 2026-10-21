"""Durable, versioned research checkpoints.

A checkpoint is written at a stage boundary, not during an LLM token stream.
Re-saving the same version and state is idempotent; changing state under an
existing version is a conflict, so a resume never silently reads a different
state under the same version.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime

from product_hunter.fetch import utcnow


class CheckpointError(Exception):
    """A checkpoint operation was refused."""


class CheckpointConflict(CheckpointError):
    """A checkpoint version already holds different state."""


@dataclass(frozen=True)
class Checkpoint:
    checkpoint_id: str
    run_id: str
    stage: str
    step: str | None
    checkpoint_version: int
    state: dict[str, object]
    state_ref: str | None
    input_refs: list[str]
    output_refs: list[str]
    created_at: str
    completed_at: str | None


def save_checkpoint(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    stage: str,
    state: dict[str, object],
    step: str | None = None,
    checkpoint_version: int = 1,
    state_ref: str | None = None,
    input_refs: list[str] | None = None,
    output_refs: list[str] | None = None,
    now: datetime | None = None,
) -> Checkpoint:
    if not run_id.strip():
        raise CheckpointError("a checkpoint needs a run id")
    if not stage.strip():
        raise CheckpointError("a checkpoint needs a stage")
    if checkpoint_version < 1:
        raise CheckpointError("checkpoint_version must be at least 1")
    run = conn.execute(
        "SELECT 1 FROM research_runs WHERE run_id = ?", (run_id,)
    ).fetchone()
    if run is None:
        raise CheckpointError(f"run {run_id!r} is not in the store")
    state_json = json.dumps(state, sort_keys=True)
    input_json = json.dumps(input_refs or [], sort_keys=True)
    output_json = json.dumps(output_refs or [], sort_keys=True)
    checkpoint_id = _hash_id(
        "ckpt_", f"{run_id}|{stage}|{step or ''}|{checkpoint_version}"
    )
    existing = conn.execute(
        "SELECT state_json, state_ref, input_refs, output_refs "
        "FROM research_checkpoints WHERE checkpoint_id = ?",
        (checkpoint_id,),
    ).fetchone()
    if existing is not None:
        if (
            existing["state_json"] != state_json
            or existing["state_ref"] != state_ref
            or existing["input_refs"] != input_json
            or existing["output_refs"] != output_json
        ):
            raise CheckpointConflict(
                f"checkpoint {checkpoint_id!r} already holds different content"
            )
        loaded = load_checkpoint(
            conn,
            run_id=run_id,
            stage=stage,
            step=step,
            version=checkpoint_version,
        )
        if loaded is None:  # pragma: no cover
            raise CheckpointError("checkpoint disappeared")
        return loaded
    now_iso = (now or utcnow()).isoformat()
    conn.execute(
        """
        INSERT INTO research_checkpoints (
            checkpoint_id, run_id, stage, step, checkpoint_version,
            state_ref, input_refs, output_refs, state_json, created_at,
            completed_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            checkpoint_id,
            run_id,
            stage,
            step or "",
            checkpoint_version,
            state_ref,
            input_json,
            output_json,
            state_json,
            now_iso,
            now_iso,
        ),
    )
    return Checkpoint(
        checkpoint_id=checkpoint_id,
        run_id=run_id,
        stage=stage,
        step=step,
        checkpoint_version=checkpoint_version,
        state=state,
        state_ref=state_ref,
        input_refs=list(input_refs or []),
        output_refs=list(output_refs or []),
        created_at=now_iso,
        completed_at=now_iso,
    )


def load_checkpoint(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    stage: str,
    step: str | None = None,
    version: int | None = None,
) -> Checkpoint | None:
    sql = (
        "SELECT * FROM research_checkpoints WHERE run_id = ? AND stage = ?"
    )
    params: list[object] = [run_id, stage]
    if step is not None:
        sql += " AND step = ?"
        params.append(step)
    if version is not None:
        sql += " AND checkpoint_version = ?"
        params.append(version)
    sql += " ORDER BY checkpoint_version DESC, created_at DESC LIMIT 1"
    row = conn.execute(sql, params).fetchone()
    return _row_to_checkpoint(row) if row is not None else None


def list_checkpoints(
    conn: sqlite3.Connection, *, run_id: str
) -> list[Checkpoint]:
    rows = conn.execute(
        "SELECT * FROM research_checkpoints WHERE run_id = ? "
        "ORDER BY stage, step, checkpoint_version",
        (run_id,),
    ).fetchall()
    return [_row_to_checkpoint(row) for row in rows]


def _row_to_checkpoint(row: sqlite3.Row) -> Checkpoint:
    try:
        state = json.loads(row["state_json"] or "{}")
    except json.JSONDecodeError as exc:
        raise CheckpointError(
            f"checkpoint {row['checkpoint_id']!r} has invalid state_json"
        ) from exc
    return Checkpoint(
        checkpoint_id=row["checkpoint_id"],
        run_id=row["run_id"],
        stage=row["stage"],
        step=row["step"],
        checkpoint_version=int(row["checkpoint_version"]),
        state=state,
        state_ref=row["state_ref"],
        input_refs=json.loads(row["input_refs"] or "[]"),
        output_refs=json.loads(row["output_refs"] or "[]"),
        created_at=row["created_at"],
        completed_at=row["completed_at"],
    )


def _hash_id(prefix: str, basis: str) -> str:
    return prefix + hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16]

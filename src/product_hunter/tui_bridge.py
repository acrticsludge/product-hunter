"""Bridge subprocess streaming NDJSON to the Bun TUI.

A long-lived Python subprocess that holds one read-only SQLite connection,
polls tables every 250ms, and emits ``snapshot`` NDJSON lines only when the
content hash changes. It accepts command requests on stdin (``{"type":"cmd",
"req_id", "argv"}``), executes them via argv-only ``subprocess.run`` (no
shell), and emits ``result`` lines. A background thread tails log files in
``logs/`` and ``data/processed/`` and emits ``log`` lines tagged by entity.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import signal
import sqlite3
import subprocess
import sys
import threading
import time
from pathlib import Path

from product_hunter.db import (
    connect,
    current_version,
    list_jobs,
)
from product_hunter.config import database_path, repo_root
from product_hunter.probes import list_probes
from product_hunter.registry import list_runs

# The repository root: parent of src/product_hunter.
PROJECT_ROOT = Path(__file__).resolve().parents[2]

POLL_INTERVAL = 0.25
LOG_INTERVAL = 0.5
CMD_TIMEOUT = 300
RUN_LIMIT = 200

# Environment variable names whose values are secrets.
_SECRET_ENV_RE = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD)", re.IGNORECASE)

# Entity tagging patterns.
_ENTITY_PATTERNS = (
    (re.compile(r"run_[a-f0-9]+"), "run"),
    (re.compile(r"PRB-\d{8}-\d{4}"), "probe"),
    (re.compile(r"prb_[a-f0-9]+"), "probe"),
)

# Log level inference (order matters: most specific first).
_LEVEL_PATTERNS = (
    (re.compile(r"\bERR\b|\bERROR\b", re.IGNORECASE), "ERR"),
    (re.compile(r"\bWARN\b", re.IGNORECASE), "WARN"),
    (re.compile(r"\bDEBUG\b", re.IGNORECASE), "DEBUG"),
    (re.compile(r"\bINFO\b", re.IGNORECASE), "INFO"),
)


def _emit(obj: dict) -> None:
    """Write one NDJSON line to stdout and flush."""
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def _secret_values() -> list[str]:
    """The secret env var values to redact, longest first."""
    values: list[str] = []
    for key, value in os.environ.items():
        if key and _SECRET_ENV_RE.search(key) and value:
            values.append(value)
    values.sort(key=len, reverse=True)
    unique: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value not in seen:
            seen.add(value)
            unique.append(value)
    return unique


def redact(text: str) -> str:
    """Replace occurrences of secret env var values with ``***``."""
    for value in _secret_values():
        text = text.replace(value, "***")
    return text


def _table_counts(conn: sqlite3.Connection) -> dict[str, int]:
    """Row count per table, best-effort; -1 when a table cannot be read."""
    counts: dict[str, int] = {}
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
        for row in rows:
            name = row["name"]
            if not name or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
                continue
            try:
                counts[name] = int(
                    conn.execute(f'SELECT count(*) FROM "{name}"').fetchone()[0]
                )
            except sqlite3.Error:
                counts[name] = -1
    except sqlite3.Error:
        pass
    return counts


def _now_iso() -> str:
    from product_hunter.db.connection import utc_now

    return utc_now().isoformat()


def _job_status_counts(conn: sqlite3.Connection) -> dict[str, int]:
    """Job counts by bucket: queued, running (live lease), failed, expired."""
    counts = {"queued": 0, "running": 0, "failed": 0, "expired": 0}
    try:
        now_iso = _now_iso()
        rows = conn.execute(
            "SELECT status, count(*) AS n FROM jobs GROUP BY status"
        ).fetchall()
        for row in rows:
            status = (row["status"] or "").lower()
            n = int(row["n"])
            if status == "queued":
                counts["queued"] = n
            elif status == "leased":
                live = int(
                    conn.execute(
                        "SELECT count(*) FROM jobs WHERE status = 'leased' "
                        "AND lease_expires_at IS NOT NULL "
                        "AND lease_expires_at > ?",
                        (now_iso,),
                    ).fetchone()[0]
                )
                counts["running"] = live
                counts["expired"] = n - live
            elif status in ("failed", "dead"):
                counts["failed"] += n
    except sqlite3.Error:
        pass
    return counts


def _autonomy(conn: sqlite3.Connection) -> dict[str, object]:
    """Autonomy status, best-effort from the autonomy module or the db."""
    result: dict[str, object] = {
        "worker_active": False,
        "running_runs": 0,
        "stalled_runs": 0,
    }
    try:
        from product_hunter.autonomy.engine import (
            DEFAULT_OPERATION_ID,
            operation_status,
        )

        payload = operation_status(conn, operation_id=DEFAULT_OPERATION_ID)
        if isinstance(payload, dict):
            result["worker_active"] = bool(payload.get("worker_active"))
    except Exception:
        pass
    try:
        now_iso = _now_iso()
        row = conn.execute(
            "SELECT count(*) FROM jobs WHERE status = 'leased' "
            "AND lease_expires_at IS NOT NULL "
            "AND lease_expires_at > ?",
            (now_iso,),
        ).fetchone()
        running = int(row[0]) if row else 0
        result["running_runs"] = running
        if running:
            result["worker_active"] = True
        expired = int(
            conn.execute(
                "SELECT count(*) FROM jobs WHERE status = 'leased' "
                "AND lease_expires_at IS NOT NULL "
                "AND lease_expires_at <= ?",
                (now_iso,),
            ).fetchone()[0]
        )
        result["stalled_runs"] = expired
    except sqlite3.Error:
        pass
    return result


def _sources(conn: sqlite3.Connection) -> list[dict[str, object]]:
    """Source list, best-effort when the sources table exists."""
    sources: list[dict[str, object]] = []
    try:
        rows = conn.execute(
            "SELECT source_id, source_type, enabled FROM sources ORDER BY source_id"
        ).fetchall()
        for row in rows:
            sources.append(
                {
                    "source_id": row["source_id"],
                    "name": row["source_id"],
                    "type": row["source_type"],
                    "status": "enabled" if row["enabled"] else "disabled",
                }
            )
    except sqlite3.Error:
        pass
    return sources


def build_snapshot(conn: sqlite3.Connection) -> dict:
    """Build one snapshot dict over the read-only store.

    Keys: db_status, runs, probes, autonomy, sources, hash. The hash is the
    sha256 of the canonical JSON of everything except ``hash`` itself.
    """
    data: dict[str, object] = {}

    # --- db_status ------------------------------------------------------
    try:
        schema_version = current_version(conn)
    except sqlite3.Error:
        schema_version = 0
    jobs = _job_status_counts(conn)
    try:
        outbox_pending = int(
            conn.execute(
                "SELECT count(*) FROM projection_tasks WHERE status = 'pending'"
            ).fetchone()[0]
        )
        projection_tasks = int(
            conn.execute("SELECT count(*) FROM projection_tasks").fetchone()[0]
        )
    except sqlite3.Error:
        outbox_pending = 0
        projection_tasks = 0
    data["db_status"] = {
        "schema_version": schema_version,
        "tables": _table_counts(conn),
        "jobs": jobs,
        "outbox_pending": outbox_pending,
        "projection_tasks": projection_tasks,
    }

    # --- runs -----------------------------------------------------------
    run_list: list[dict[str, object]] = []
    try:
        for view in list_runs(conn, limit=RUN_LIMIT):
            run_list.append(
                {
                    "id": view.run_id,
                    "objective": view.objective,
                    "status": view.status,
                    "current_stage": view.current_stage,
                    "current_step": view.current_step,
                    "progress_percent": view.progress_percent,
                    "resume_count": view.resume_count,
                    "updated_at": view.updated_at,
                    "probe_id": None,
                    "worker_active": view.worker_active,
                }
            )
    except sqlite3.Error:
        pass
    data["runs"] = run_list

    # --- probes ---------------------------------------------------------
    probe_list: list[dict[str, object]] = []
    try:
        for view in list_probes(conn):
            probe_list.append(
                {
                    "public_id": view.public_id,
                    "probe_id": view.probe_id,
                    "status": view.status,
                    "objective": view.objective,
                    "total_cycles": view.total_cycles,
                    "successful_cycles": view.successful_cycles,
                    "failed_cycles": view.failed_cycles,
                    "blocked_cycles": view.blocked_cycles,
                    "backoff_multiplier": view.backoff_multiplier,
                    "next_cycle_at": view.next_cycle_at,
                    "current_run_id": view.current_run_id,
                    "worker_active": view.worker_active,
                }
            )
    except sqlite3.Error:
        pass
    data["probes"] = probe_list

    # --- jobs -----------------------------------------------------------
    job_list: list[dict[str, object]] = []
    try:
        for job in list_jobs(conn):
            job_list.append(
                {
                    "id": job.id,
                    "run_id": getattr(job, "run_id", None),
                    "stage": getattr(job, "stage", None),
                    "status": job.status,
                    "worker_id": getattr(job, "worker_id", None),
                    "lease_expires_at": getattr(job, "lease_expires_at", None),
                    "created_at": getattr(job, "created_at", None),
                    "updated_at": getattr(job, "updated_at", None),
                }
            )
    except Exception:
        pass
    data["jobs"] = job_list

    # --- autonomy -------------------------------------------------------
    data["autonomy"] = _autonomy(conn)

    # --- sources --------------------------------------------------------
    data["sources"] = _sources(conn)

    # --- hash -----------------------------------------------------------
    payload = {k: v for k, v in data.items() if k != "hash"}
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    data["hash"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return data


def run_command(req_id: str, argv: list[str]) -> dict:
    """Execute one argv-only command and return its result dict."""
    if not isinstance(argv, list) or not argv or not all(isinstance(x, str) for x in argv):
        return {"req_id": req_id, "ok": False, "stdout": "", "stderr": redact("invalid argv"), "exit_code": -1}
    try:
        completed = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            cwd=str(PROJECT_ROOT),
            timeout=CMD_TIMEOUT,
        )
        return {
            "req_id": req_id,
            "ok": completed.returncode == 0,
            "stdout": redact(completed.stdout or ""),
            "stderr": redact(completed.stderr or ""),
            "exit_code": int(completed.returncode),
        }
    except subprocess.TimeoutExpired:
        return {
            "req_id": req_id,
            "ok": False,
            "stdout": "",
            "stderr": redact("command timed out"),
            "exit_code": -1,
        }
    except Exception as exc:  # pragma: no cover - defensive
        return {
            "req_id": req_id,
            "ok": False,
            "stdout": "",
            "stderr": redact(f"{type(exc).__name__}: {exc}"),
            "exit_code": -1,
        }


def _stdin_reader(stop_event: threading.Event) -> None:
    """Read JSON command lines from stdin and execute them."""
    for line in sys.stdin:
        if stop_event.is_set():
            break
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(req, dict) or req.get("type") != "cmd":
            continue
        req_id = req.get("req_id")
        argv = req.get("argv")
        if not req_id or not isinstance(argv, list):
            continue
        _emit({"type": "result", **run_command(req_id, argv)})


def _infer_level(line: str) -> str:
    for pattern, level in _LEVEL_PATTERNS:
        if pattern.search(line):
            return level
    return "INFO"


def _tag_entity(line: str) -> dict[str, object]:
    for pattern, kind in _ENTITY_PATTERNS:
        match = pattern.search(line)
        if match:
            return {"kind": kind, "id": match.group(0)}
    return {"kind": "system", "id": None}


def _log_tailer(stop_event: threading.Event) -> None:
    """Tail *.log and *.txt files under logs/ and data/processed/."""
    directories = [PROJECT_ROOT / "logs", PROJECT_ROOT / "data" / "processed"]
    offsets: dict[str, int] = {}
    while not stop_event.is_set():
        stop_event.wait(LOG_INTERVAL)
        if stop_event.is_set():
            break
        for directory in directories:
            if not directory.is_dir():
                continue
            for path in directory.rglob("*"):
                if not path.is_file():
                    continue
                if path.suffix not in (".log", ".txt"):
                    continue
                key = str(path)
                try:
                    size = path.stat().st_size
                except OSError:
                    continue
                offset = offsets.setdefault(key, 0)
                if offset > size:
                    offset = 0
                    offsets[key] = 0
                try:
                    with path.open("r", encoding="utf-8", errors="replace") as handle:
                        handle.seek(offset)
                        new_bytes = handle.read()
                        offsets[key] = handle.tell()
                except OSError:
                    continue
                if not new_bytes:
                    continue
                for text in new_bytes.splitlines():
                    if not text.strip():
                        continue
                    _emit(
                        {
                            "type": "log",
                            "level": _infer_level(text),
                            "ts": _now_iso(),
                            "entity": _tag_entity(text),
                            "line": text,
                        }
                    )


def _poll_loop(conn: sqlite3.Connection, stop_event: threading.Event) -> None:
    """Poll the store every 250ms; emit snapshot when the hash changes."""
    last_hash: str | None = None
    while not stop_event.is_set():
        stop_event.wait(POLL_INTERVAL)
        if stop_event.is_set():
            break
        try:
            snapshot = build_snapshot(conn)
        except Exception:
            continue
        h = snapshot.get("hash")
        if h is not None and h != last_hash:
            last_hash = h
            _emit({"type": "snapshot", "hash": h, "data": snapshot})


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ph tui-bridge")
    parser.add_argument(
        "--db",
        default=None,
        help="SQLite path (default: PH_DB_PATH env, then config default)",
    )
    args = parser.parse_args(argv)

    db_path = (
        args.db
        or os.environ.get("PH_DB_PATH")
        or str(database_path(repo_root()))
    )

    stop_event = threading.Event()

    def _handle_signal(signum, frame):  # noqa: ARG001
        stop_event.set()

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    try:
        conn = connect(db_path, read_only=True)
    except Exception as exc:  # pragma: no cover - defensive
        _emit({"type": "error", "message": f"cannot open db: {exc}"})
        return 1

    # Emit an initial snapshot so the TUI has something to render immediately.
    try:
        snapshot = build_snapshot(conn)
        _emit({"type": "snapshot", "hash": snapshot["hash"], "data": snapshot})
    except Exception as exc:
        _emit({"type": "error", "message": f"snapshot failed: {exc}"})

    stdin_thread = threading.Thread(
        target=_stdin_reader, args=(stop_event,), daemon=True
    )
    log_thread = threading.Thread(
        target=_log_tailer, args=(stop_event,), daemon=True
    )
    stdin_thread.start()
    log_thread.start()

    _poll_loop(conn, stop_event)

    stdin_thread.join(timeout=5)
    log_thread.join(timeout=5)
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

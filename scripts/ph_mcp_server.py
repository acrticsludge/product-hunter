#!/usr/bin/env python3
"""Minimal MCP server (stdio transport) wrapping the `ph` CLI.

Exposes the `product_hunter` tool so opencode agents on any machine (including
remote VMs without the built-in tool) can control Product Hunter research runs,
probes, and diagnostics.

Protocol: JSON-RPC 2.0 over newline-delimited stdio (MCP 2024-11-05).
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

_script_dir = Path(__file__).resolve().parent
PROJECT_DIR = os.environ.get("PH_PROJECT_DIR", str(_script_dir.parent))


def _find_ph() -> str:
    env_bin = os.environ.get("PH_BIN")
    if env_bin:
        return env_bin
    found = shutil.which("ph")
    if found:
        return found
    for candidate in [
        Path(PROJECT_DIR) / ".venv" / "bin" / "ph",
        Path(PROJECT_DIR) / ".venv" / "Scripts" / "ph.exe",
        Path(PROJECT_DIR) / ".venv" / "Scripts" / "ph",
    ]:
        if candidate.is_file():
            return str(candidate)
    return "ph"


PH_BIN = _find_ph()


def _run_ph(argv: list[str]) -> str:
    """Run a ph CLI command and return its stdout (or stderr on failure)."""
    try:
        proc = subprocess.run(
            [PH_BIN, *argv],
            capture_output=True,
            text=True,
            cwd=PROJECT_DIR,
            timeout=300,
        )
    except FileNotFoundError:
        return "error: ph CLI not found on PATH"
    except subprocess.TimeoutExpired:
        return "error: ph command timed out after 300s"
    out = (proc.stdout or "").strip()
    err = (proc.stderr or "").strip()
    if proc.returncode != 0:
        return f"error: {err or out or f'exit code {proc.returncode}'}"
    return out or err or "(no output)"


def _run_system(argv: list[str]) -> str:
    """Run a system command (e.g., systemctl) and return its output."""
    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except FileNotFoundError:
        return f"error: {argv[0]} not found"
    except subprocess.TimeoutExpired:
        return "error: command timed out after 30s"
    out = (proc.stdout or "").strip()
    err = (proc.stderr or "").strip()
    if proc.returncode != 0:
        return f"error: {err or out or f'exit code {proc.returncode}'}"
    return out or err or "(no output)"


def _handle_list_tools() -> dict:
    return {
        "tools": [
            {
                "name": "product_hunter",
                "description": (
                    "Control Product Hunter research runs, probes, and diagnostics: "
                    "create/list/status/continue/resume/pause/retry/cancel/log/doctor/recover "
                    "research runs; create/list/status/start/pause/resume/stop/report probes; "
                    "check store health, list requests, db status, autonomy status, "
                    "start/stop worker, list sources, index status."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "action": {
                            "type": "string",
                            "enum": [
                                # Research runs
                                "create", "list", "status", "continue", "resume",
                                "pause", "retry", "cancel", "log", "doctor",
                                "recover", "store_health", "list_requests",
                                # Probes (24/7 on-call)
                                "probe_create", "probe_list", "probe_status",
                                "probe_start", "probe_pause", "probe_resume",
                                "probe_stop", "probe_report",
                                # Worker control
                                "worker_start", "worker_stop", "worker_status",
                                # Diagnostics
                                "db_status", "autonomy_status", "sources_list",
                                "index_status", "observe_metrics", "observe_trace",
                                "models_list", "project_build",
                            ],
                            "description": "The action to perform.",
                        },
                        "research_id": {
                            "type": "string",
                            "description": "Research run ID (RH-YYYYMMDD-NNNN).",
                        },
                        "probe_id": {
                            "type": "string",
                            "description": "Probe ID (PRB-YYYYMMDD-NNNN).",
                        },
                        "objective": {
                            "type": "string",
                            "description": "Research objective (for create/probe_create).",
                        },
                        "interval": {
                            "type": "integer",
                            "description": "Probe cycle interval in seconds (default 300).",
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Log entry limit (default 50).",
                        },
                        "run_id": {
                            "type": "string",
                            "description": "Run ID for observe_trace.",
                        },
                    },
                    "required": ["action"],
                },
            }
        ]
    }


def _handle_call_tool(params: dict) -> dict:
    args = params.get("arguments", {})
    action = args.get("action", "")
    rid = args.get("research_id", "")
    pid = args.get("probe_id", "") or args.get("research_id", "")
    objective = args.get("objective", "")
    interval = args.get("interval", 300)
    limit = args.get("limit", 50)
    run_id = args.get("run_id", "")

    # --- Research runs ---
    if action == "create":
        argv = ["research", "create"]
        if objective:
            argv += ["--objective", objective]
    elif action in ("list", "list_requests"):
        argv = ["research", "list"]
    elif action == "status":
        argv = ["research", "status", rid] if rid else ["research", "list"]
    elif action == "continue":
        argv = ["research", "continue"] + ([rid] if rid else [])
    elif action == "resume":
        argv = ["research", "resume", rid] if rid else ["research", "list"]
    elif action == "pause":
        argv = ["research", "pause", rid] if rid else ["research", "list"]
    elif action == "retry":
        argv = ["research", "retry", rid] if rid else ["research", "list"]
    elif action == "cancel":
        argv = ["research", "cancel", rid] if rid else ["research", "list"]
    elif action == "log":
        argv = ["research", "log", rid, "--limit", str(limit)] if rid else ["research", "list"]
    elif action in ("doctor", "store_health"):
        argv = ["doctor"]
    elif action == "recover":
        argv = ["research", "recover"] + ([rid] if rid else [])

    # --- Probes (24/7 on-call) ---
    elif action == "probe_create":
        argv = ["probe", "create", "--objective", objective]
        if interval:
            argv += ["--interval", str(interval)]
    elif action == "probe_list":
        argv = ["probe", "list"]
    elif action == "probe_status":
        argv = ["probe", "status", pid] if pid else ["probe", "list"]
    elif action == "probe_start":
        argv = ["probe", "start", pid] if pid else ["probe", "list"]
    elif action == "probe_pause":
        argv = ["probe", "pause", pid] if pid else ["probe", "list"]
    elif action == "probe_resume":
        argv = ["probe", "resume", pid] if pid else ["probe", "list"]
    elif action == "probe_stop":
        argv = ["probe", "stop", pid] if pid else ["probe", "list"]
    elif action == "probe_report":
        argv = ["probe", "report", pid] if pid else ["probe", "list"]

    # --- Worker control ---
    elif action == "worker_start":
        return {"content": [{"type": "text", "text": _run_system(["systemctl", "--user", "start", "ph-agent.service"])}]}
    elif action == "worker_stop":
        return {"content": [{"type": "text", "text": _run_system(["systemctl", "--user", "stop", "ph-agent.service"])}]}
    elif action == "worker_status":
        status = _run_system(["systemctl", "--user", "is-active", "ph-agent.service"])
        doctor = _run_ph(["doctor"])
        return {"content": [{"type": "text", "text": f"service: {status}\n{doctor}"}]}

    # --- Diagnostics ---
    elif action == "db_status":
        argv = ["db", "status"]
    elif action == "autonomy_status":
        argv = ["autonomy", "status"]
    elif action == "sources_list":
        argv = ["sources"]
    elif action == "index_status":
        argv = ["index", "status"]
    elif action == "index_search":
        argv = ["index", "search"] + ([objective] if objective else [])
    elif action == "observe_metrics":
        argv = ["observe", "metrics"]
    elif action == "observe_trace":
        argv = ["observe", "trace", "--run", run_id] if run_id else ["observe", "metrics"]
    elif action == "models_list":
        argv = ["models"]
    elif action == "project_build":
        argv = ["project"]

    else:
        return {"content": [{"type": "text", "text": f"unknown action: {action}"}], "isError": True}

    text = _run_ph(argv)
    return {"content": [{"type": "text", "text": text}]}


def main() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            continue

        method = req.get("method", "")
        req_id = req.get("id")

        if method == "initialize":
            result = {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "product-hunter-mcp", "version": "1.1.0"},
            }
        elif method == "notifications/initialized":
            continue
        elif method == "tools/list":
            result = _handle_list_tools()
        elif method == "tools/call":
            result = _handle_call_tool(req.get("params", {}))
        else:
            if req_id is not None:
                resp = {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32601, "message": "Method not found"}}
                sys.stdout.write(json.dumps(resp) + "\n")
                sys.stdout.flush()
            continue

        if req_id is not None:
            resp = {"jsonrpc": "2.0", "id": req_id, "result": result}
            sys.stdout.write(json.dumps(resp) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Minimal MCP server (stdio transport) wrapping the `ph` CLI.

Exposes the `product_hunter` tool so opencode agents on any machine (including
remote VMs without the built-in tool) can control Product Hunter research runs.

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


def _handle_list_tools() -> dict:
    return {
        "tools": [
            {
                "name": "product_hunter",
                "description": (
                    "Control Product Hunter research runs: create, list, inspect, "
                    "continue, resume, pause, retry, cancel, read logs, check store "
                    "health, and recover requests."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "action": {
                            "type": "string",
                            "enum": [
                                "create", "list", "status", "continue", "resume",
                                "pause", "retry", "cancel", "log", "doctor",
                                "recover", "store_health", "list_requests",
                            ],
                            "description": "The action to perform.",
                        },
                        "research_id": {
                            "type": "string",
                            "description": "Research run ID (required for most actions).",
                        },
                        "objective": {
                            "type": "string",
                            "description": "Research objective (for create).",
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Log entry limit (default 50).",
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
    objective = args.get("objective", "")
    limit = args.get("limit", 50)

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
                "serverInfo": {"name": "product-hunter-mcp", "version": "1.0.0"},
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
EOF
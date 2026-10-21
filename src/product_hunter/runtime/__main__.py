"""The process entry point for the supervised worker.

The handler factory, the database path and the lock path come from the
environment, so the same command starts the worker everywhere. The process
takes the single instance lock, runs the worker until a signal arrives, and
leaves a handler that overruns the grace period to the lease: the job is
delayed, never corrupted.
"""

from __future__ import annotations

import importlib
import json
import os
import sys
import threading
from pathlib import Path

from product_hunter.config import database_path, repo_root
from product_hunter.db import Store
from product_hunter.fetch import utcnow
from product_hunter.runtime.events import JsonlEventSink, WorkerEvent
from product_hunter.runtime.identity import WorkerIdentity
from product_hunter.runtime.lock import SingleInstanceLock
from product_hunter.runtime.worker import Handler, WorkerRuntime, install_shutdown_handlers

LOCK_NAME = Path("runtime") / "worker.lock"
GRACE_SECONDS = 30.0

EXIT_OK = 0
EXIT_ERROR = 2
EXIT_TIMEOUT = 3


def main() -> int:
    """Run one supervised worker until it is asked to stop."""
    root = repo_root()
    identity = WorkerIdentity.create()
    sink = JsonlEventSink(sys.stderr)
    try:
        handlers = _load_handlers(os.environ.get("PH_WORKER_HANDLERS"))
    except (ImportError, AttributeError, TypeError, ValueError) as exc:
        print(
            json.dumps({"ok": False, "error": f"handlers: {exc}"}),
            file=sys.stderr,
        )
        return EXIT_ERROR
    lock = SingleInstanceLock(_lock_path(root), identity=identity)
    if not lock.acquire():
        sink.emit(
            WorkerEvent(
                event="lock_denied",
                worker_id=identity.worker_id,
                ts=utcnow().isoformat(),
            )
        )
        return EXIT_ERROR
    sink.emit(
        WorkerEvent(
            event="lock_acquired",
            worker_id=identity.worker_id,
            ts=utcnow().isoformat(),
        )
    )
    store = Store.open(database_path(root))
    runtime = WorkerRuntime(store.conn, identity, handlers, events=sink)
    uninstall = install_shutdown_handlers(runtime)
    finished = threading.Event()
    watchdog = threading.Thread(
        target=watch_shutdown, args=(runtime, finished, sink), daemon=True
    )
    watchdog.start()
    try:
        runtime.run_forever()
    finally:
        finished.set()
        uninstall()
        store.close()
        lock.release()
    return EXIT_OK


def watch_shutdown(
    runtime: WorkerRuntime, finished: threading.Event, sink: JsonlEventSink
) -> None:
    """End the process when a handler overruns the grace period.

    The process leaves the write ahead log to SQLite's own crash recovery, and
    the abandoned job is recovered when its lease expires.
    """
    runtime.stop_event.wait()
    if finished.wait(GRACE_SECONDS):
        return
    sink.emit(
        WorkerEvent(
            event="shutdown_timeout",
            worker_id=runtime.identity.worker_id,
            ts=runtime.clock().isoformat(),
        )
    )
    os._exit(EXIT_TIMEOUT)


def _load_handlers(spec: str | None) -> dict[str, Handler]:
    """The handler mapping named by PH_WORKER_HANDLERS as module:attribute."""
    if not spec or not spec.strip():
        return {}
    module_name, _, attribute = spec.strip().partition(":")
    if not module_name or not attribute:
        raise ValueError("PH_WORKER_HANDLERS must be 'module:attribute'")
    module = importlib.import_module(module_name)
    factory = getattr(module, attribute)
    return dict(factory())


def _lock_path(root: Path) -> Path:
    """The lock file: PH_WORKER_LOCK, else runtime/worker.lock under the root."""
    raw = os.environ.get("PH_WORKER_LOCK")
    if raw and raw.strip():
        path = Path(raw.strip()).expanduser()
        return path if path.is_absolute() else root / path
    return root / LOCK_NAME


if __name__ == "__main__":
    sys.exit(main())

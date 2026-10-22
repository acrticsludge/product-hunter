"""The process entry point for the operation layer.

The entry point takes the same single instance lock the supervised worker
takes, so the worker and the operation cannot both run against one database.
The handlers come from the same environment variable the worker reads, and the
upkeep handler is always registered, so a process with no other handler still
does the upkeep.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from product_hunter.autonomy.engine import OperationEngine
from product_hunter.autonomy.maintenance import (
    MAINTENANCE_STAGE,
    make_maintenance_handler,
)
from product_hunter.config import database_path, load_autonomy_config, repo_root
from product_hunter.db import Store
from product_hunter.runtime.events import JsonlEventSink, WorkerEvent
from product_hunter.runtime.identity import WorkerIdentity
from product_hunter.runtime.lock import SingleInstanceLock, lock_path
from product_hunter.runtime.worker import install_shutdown_handlers, load_handlers
from product_hunter.fetch import utcnow

EXIT_OK = 0
EXIT_ERROR = 2


def main(argv: list[str] | None = None) -> int:
    """Run the operation until it is asked to stop, or for one cycle."""
    parser = argparse.ArgumentParser(
        prog="python -m product_hunter.autonomy",
        description="Run the operation layer against the store.",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="run one cycle, print it as JSON and stop",
    )
    args = parser.parse_args(argv)

    root = repo_root()
    identity = WorkerIdentity.create()
    sink = JsonlEventSink(sys.stderr)
    try:
        handlers = load_handlers(os.environ.get("PH_WORKER_HANDLERS"))
    except (ImportError, AttributeError, TypeError, ValueError) as exc:
        print(
            json.dumps({"ok": False, "error": f"handlers: {exc}"}),
            file=sys.stderr,
        )
        return EXIT_ERROR
    try:
        config = load_autonomy_config()
    except (OSError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": f"config: {exc}"}), file=sys.stderr)
        return EXIT_ERROR

    lock = SingleInstanceLock(lock_path(root), identity=identity)
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
    try:
        # The upkeep handler is always there, so a process with no other
        # handler still keeps the store tidy.
        handlers[MAINTENANCE_STAGE] = make_maintenance_handler(store.conn)
        try:
            engine = OperationEngine(
                store.conn, identity, handlers, config=config, events=sink
            )
        except (OSError, ValueError) as exc:
            # The engine reads the cadence file, and a file that cannot be
            # read is a configuration fault. It gets a clean refusal and not
            # a traceback.
            print(
                json.dumps({"ok": False, "error": f"cadence: {exc}"}),
                file=sys.stderr,
            )
            return EXIT_ERROR
        if args.once:
            step = engine.run_once()
            print(json.dumps(step.to_dict(), sort_keys=True))
            return EXIT_OK
        uninstall = install_shutdown_handlers(engine.runtime)
        try:
            engine.run_forever()
        except Exception as exc:
            print(
                json.dumps({"ok": False, "error": f"run: {exc}"}), file=sys.stderr
            )
            return EXIT_ERROR
        finally:
            uninstall()
    finally:
        store.close()
        lock.release()
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())

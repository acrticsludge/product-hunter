"""The supervised worker runtime.

The runtime sits on top of the authoritative SQLite job queue and gives the
process the behaviour the database layer does not have: lease renewal, a
graceful stop, structured events, a worker identity and a cross process single
instance lock.
"""

from __future__ import annotations

from product_hunter.runtime.events import EventSink, JsonlEventSink, WorkerEvent
from product_hunter.runtime.identity import WorkerIdentity
from product_hunter.runtime.lock import LockError, SingleInstanceLock
from product_hunter.runtime.worker import (
    Handler,
    RetryPolicy,
    WorkerRuntime,
    install_shutdown_handlers,
)

__all__ = [
    "EventSink",
    "Handler",
    "JsonlEventSink",
    "LockError",
    "RetryPolicy",
    "SingleInstanceLock",
    "WorkerEvent",
    "WorkerIdentity",
    "WorkerRuntime",
    "install_shutdown_handlers",
]

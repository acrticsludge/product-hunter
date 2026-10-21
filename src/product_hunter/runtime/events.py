"""Structured worker events, one JSON object per line.

The runtime reports every transition it makes as an event. An event names the
worker, the job and the lease epoch it happened under, so a line can be traced
back to the process that wrote it. Fields that carry no value are left out of
the JSON, so a line only holds what the event actually knows.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Protocol, TextIO


@dataclass(frozen=True)
class WorkerEvent:
    """One thing the worker did, at one point in time."""

    event: str
    worker_id: str
    ts: str
    job_id: str | None = None
    stage: str | None = None
    attempt: int | None = None
    lease_epoch: int | None = None
    status: str | None = None
    duration_ms: int | None = None
    error: str | None = None
    count: int | None = None

    def to_dict(self) -> dict[str, object]:
        """The event as plain data, without the fields that are unset."""
        data: dict[str, object] = {
            "event": self.event,
            "worker_id": self.worker_id,
            "ts": self.ts,
        }
        for name in (
            "job_id",
            "stage",
            "attempt",
            "lease_epoch",
            "status",
            "duration_ms",
            "error",
            "count",
        ):
            value = getattr(self, name)
            if value is not None:
                data[name] = value
        return data

    def to_json(self) -> str:
        """The event as one JSON object, without a trailing newline."""
        return json.dumps(self.to_dict(), sort_keys=True)


class EventSink(Protocol):
    """Where a worker event goes."""

    def emit(self, event: WorkerEvent) -> None:
        """Record one event."""


class JsonlEventSink:
    """A sink that writes one flushed JSON line per event."""

    def __init__(self, stream: TextIO) -> None:
        self.stream = stream

    def emit(self, event: WorkerEvent) -> None:
        self.stream.write(event.to_json() + "\n")
        self.stream.flush()

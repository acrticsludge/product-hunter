"""Stable identity for one worker process.

The identity is created once per process and never changes, so a job row that
names a worker can be traced back to a host and a pid. The random token keeps
two processes on the same host apart.
"""

from __future__ import annotations

import os
import re
import secrets
import socket
from dataclasses import dataclass
from datetime import datetime

from product_hunter.fetch import utcnow

_UNSAFE = re.compile(r"[^A-Za-z0-9_-]")


@dataclass(frozen=True)
class WorkerIdentity:
    """Who this worker is: an id, a host, a pid and a start time."""

    worker_id: str
    hostname: str
    pid: int
    started_at: str

    @classmethod
    def create(
        cls, *, prefix: str = "worker", now: datetime | None = None
    ) -> "WorkerIdentity":
        """Build an identity for the running process."""
        started = now if now is not None else utcnow()
        hostname = _safe_hostname(socket.gethostname())
        pid = os.getpid()
        worker_id = f"{prefix}-{hostname}-{pid}-{secrets.token_hex(4)}"
        return cls(
            worker_id=worker_id,
            hostname=hostname,
            pid=pid,
            started_at=started.isoformat(),
        )

    def to_dict(self) -> dict[str, object]:
        """The identity as plain data, for an event or a log line."""
        return {
            "worker_id": self.worker_id,
            "hostname": self.hostname,
            "pid": self.pid,
            "started_at": self.started_at,
        }


def _safe_hostname(value: str) -> str:
    """A hostname reduced to characters that are safe inside a worker id."""
    cleaned = _UNSAFE.sub("-", value.strip())
    return cleaned or "unknown-host"

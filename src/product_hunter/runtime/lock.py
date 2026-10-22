"""A cross process single instance lock for one supervised worker.

The lock is an operating system file lock, so it dies with the process that
holds it. A second worker is refused while the lock file stays the same file,
which holds as long as the file is not deleted or replaced under a worker
that holds it. The lock file names the last holder for diagnosis; the
operating system lock, not the file content, says who holds it now.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import IO

from product_hunter.runtime.identity import WorkerIdentity

if sys.platform == "win32":
    import msvcrt
else:
    import fcntl

# The lock file under the repository root, when the environment names none.
LOCK_NAME = Path("runtime") / "worker.lock"


def lock_path(root: Path) -> Path:
    """The lock file: PH_WORKER_LOCK, else runtime/worker.lock under the root.

    Both entry points take the same path, so the supervised worker and the
    operation cannot run against one database at the same time.
    """
    raw = os.environ.get("PH_WORKER_LOCK")
    if raw and raw.strip():
        path = Path(raw.strip()).expanduser()
        return path if path.is_absolute() else root / path
    return root / LOCK_NAME


class LockError(Exception):
    """The lock could not be taken."""


class SingleInstanceLock:
    """An operating system file lock that never blocks."""

    def __init__(
        self, path: str | Path, *, identity: WorkerIdentity | None = None
    ) -> None:
        self.path = Path(path)
        self.identity = identity if identity is not None else WorkerIdentity.create()
        self._handle: IO[bytes] | None = None

    def acquire(self) -> bool:
        """Take the lock. Returns False when another holder has it."""
        if self._handle is not None:
            return True
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(self.path, "a+b")
        try:
            handle.seek(0)
            _lock(handle)
        except OSError:
            handle.close()
            return False
        self._handle = handle
        self._write_holder()
        return True

    def release(self) -> None:
        """Give the lock back. Safe to call more than once."""
        handle = self._handle
        self._handle = None
        if handle is None:
            return
        try:
            handle.seek(0)
            _unlock(handle)
        except OSError:
            pass
        finally:
            handle.close()

    def __enter__(self) -> "SingleInstanceLock":
        if not self.acquire():
            raise LockError(f"another worker holds the lock at {self.path}")
        return self

    def __exit__(self, *exc: object) -> None:
        self.release()

    def _write_holder(self) -> None:
        handle = self._handle
        if handle is None:  # pragma: no cover - only called while held
            return
        # The previous holder left a record; the live holder replaces it.
        handle.truncate(0)
        handle.write(
            f"worker_id={self.identity.worker_id}\n"
            f"pid={self.identity.pid}\n"
            f"started_at={self.identity.started_at}\n".encode("utf-8")
        )
        handle.flush()


def _lock(handle: IO[bytes]) -> None:
    """Take an exclusive lock on the first byte of the file."""
    if sys.platform == "win32":
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock(handle: IO[bytes]) -> None:
    """Release the lock taken by ``_lock``."""
    if sys.platform == "win32":
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

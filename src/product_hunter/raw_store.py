from __future__ import annotations

import hashlib
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

_SOURCE_ID_RE = re.compile(r"[a-z0-9_]+\Z")

_EXTENSION_BY_CONTENT_TYPE = {
    "application/json": ".json",
    "text/html": ".html",
    "text/plain": ".txt",
    "application/xml": ".xml",
    "text/xml": ".xml",
}


@dataclass(frozen=True)
class RawObject:
    sha256: str
    path: str
    bytes: int
    content_type: str


def extension_for(content_type: str) -> str:
    mime = (content_type or "").split(";", 1)[0].strip().lower()
    return _EXTENSION_BY_CONTENT_TYPE.get(mime, ".bin")


class RawStore:
    """Content-addressed, immutable raw capture store under ``data/raw``."""

    def __init__(self, repo_root: str | Path) -> None:
        self._root = Path(repo_root)

    def put(self, source_id: str, content: bytes, content_type: str) -> RawObject:
        if not _SOURCE_ID_RE.match(source_id):
            raise ValueError(f"unsafe source_id: {source_id!r}")
        if not content:
            raise ValueError("refusing to store empty content")

        sha256 = hashlib.sha256(content).hexdigest()
        relative = Path("data") / "raw" / source_id / f"{sha256}{extension_for(content_type)}"
        target = self._root / relative
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            handle_fd, temp_path = tempfile.mkstemp(dir=str(target.parent), prefix=".tmp-")
            try:
                with os.fdopen(handle_fd, "wb") as handle:
                    handle.write(content)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temp_path, target)
            except BaseException:
                try:
                    os.unlink(temp_path)
                except OSError:
                    pass
                raise
        return RawObject(
            sha256=sha256,
            path=relative.as_posix(),
            bytes=len(content),
            content_type=content_type,
        )

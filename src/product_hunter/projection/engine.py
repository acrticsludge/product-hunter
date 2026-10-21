"""Write the projection: compare, write what changed, prune what is gone.

The engine is the only module that writes, and it writes only under the
vault root. A note is written when its bytes differ, so a run over unchanged
state touches nothing. The manifest at the vault root records what was
written, and a note the snapshot no longer holds is removed.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from product_hunter.db.outbox import ProjectionTask
from product_hunter.projection.contract import CONTRACT_VERSION
from product_hunter.projection.render import (
    SOURCE_MARKER,
    RenderedNote,
    render_frontmatter,
    render_snapshot,
)
from product_hunter.projection.sanitize import content_hash
from product_hunter.projection.source import SqliteProjectionSource
from product_hunter.projection.vault import VaultLayout

# The file at the vault root that records what the projection wrote.
MANIFEST_NAME = ".product-hunter-projection.json"


class ProjectionError(Exception):
    """The projection could not be written."""


@dataclass(frozen=True)
class ProjectResult:
    """What one projection run did."""

    written: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    paths: list[str] = field(default_factory=list)


class Projector:
    """Build the vault from the authoritative state."""

    def __init__(
        self, vault_root: str | Path, *, source: SqliteProjectionSource
    ) -> None:
        self.layout = VaultLayout(vault_root)
        self.source = source
        self.as_of: str | None = None
        self.last_result: ProjectResult | None = None

    def project(self, as_of: str | None = None) -> ProjectResult:
        """Rebuild the vault from the state the database holds."""
        snapshot = self.source.snapshot(as_of=as_of or self.as_of)
        notes = render_snapshot(snapshot, self.layout)
        # The seven top-level folders are part of the layout, so they are
        # there whether the state fills them or not.
        self.layout.create_folders()
        written: list[str] = []
        unchanged: list[str] = []
        for note in notes:
            text = _note_text(note)
            path = self._path(note.path)
            note_bytes = text.encode("utf-8", errors="replace")
            if path.is_file() and path.read_bytes() == note_bytes:
                unchanged.append(note.path)
                continue
            _write(path, text)
            written.append(note.path)
        removed = self._prune({note.path for note in notes})
        self._write_manifest(notes)
        result = ProjectResult(
            written=written,
            unchanged=unchanged,
            removed=removed,
            paths=sorted(note.path for note in notes),
        )
        self.last_result = result
        return result

    def handle_projection_task(self, task: ProjectionTask) -> None:
        """Rebuild the vault for one outbox task.

        The vault is a projection of the whole state, so a partial rebuild
        would be wrong. The rebuild is idempotent, so the cost of rebuilding
        is the cost of comparing bytes. The task payload says nothing about
        the vault, so it is not read.
        """
        self.project()

    # --- the files on disk -------------------------------------------------

    def _path(self, relative: str) -> Path:
        try:
            return self.layout.resolve(relative)
        except ValueError as exc:
            raise ProjectionError(str(exc)) from exc

    def _prune(self, current: set[str]) -> list[str]:
        """Remove the notes the snapshot no longer holds."""
        removed: list[str] = []
        for relative in self._known_notes():
            if relative in current:
                continue
            try:
                path = self._path(relative)
            except ProjectionError:
                # A path the vault will not hold is left alone: a stray file
                # must not stop the projection.
                continue
            if path.is_file():
                path.unlink()
                removed.append(relative)
        return sorted(removed)

    def _known_notes(self) -> list[str]:
        """The notes this projection wrote before.

        The manifest names them. When the manifest is missing, or when it
        holds no notes of its own, the engine finds its own notes by the
        marker in their frontmatter.
        """
        manifest = self._read_manifest()
        if manifest is not None:
            notes = manifest.get("notes")
            if isinstance(notes, dict) and notes:
                return sorted(str(key) for key in notes)
        return self._scan_notes()

    def _read_manifest(self) -> dict[str, object] | None:
        path = self.layout.root / MANIFEST_NAME
        if not path.is_file():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(payload, dict):
            return None
        return payload

    def _scan_notes(self) -> list[str]:
        """The notes the projection wrote, found by their frontmatter."""
        root = self.layout.root
        if not root.is_dir():
            return []
        found: list[str] = []
        for path in sorted(root.rglob("*.md")):
            if not path.is_file():
                continue
            if _has_marker(path):
                found.append(path.relative_to(root).as_posix())
        return found

    def _write_manifest(self, notes: list[RenderedNote]) -> None:
        """Record what the vault holds, so the next run can prune it."""
        payload = {
            "contract": CONTRACT_VERSION,
            "notes": {
                note.path: {
                    "id": note.id,
                    "kind": note.kind,
                    "hash": content_hash(_note_text(note)),
                }
                for note in notes
            },
        }
        text = json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
        path = self.layout.root / MANIFEST_NAME
        if path.is_file() and path.read_text(encoding="utf-8") == text:
            return
        _write(path, text)


def _note_text(note: RenderedNote) -> str:
    """The whole note: the frontmatter, then the body."""
    fields: dict[str, object] = {
        "id": note.id,
        "kind": note.kind,
        "contract": CONTRACT_VERSION,
        "source": SOURCE_MARKER,
    }
    fields.update(note.fields)
    fields["content_hash"] = content_hash(note.body)
    return render_frontmatter(fields) + "\n" + note.body


def _has_marker(path: Path) -> bool:
    """Whether a file carries the marker of a projected note.

    The frontmatter is parsed and the source key is compared exactly. A note
    that merely mentions the marker somewhere in its frontmatter is not the
    projection's note, and the prune leaves it alone. A frontmatter that
    cannot be parsed at all is not the projection's note either: a deeply
    nested one takes the parser past the recursion limit, and the parse
    failure must not stop the run that writes the manifest.
    """
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    if not text.startswith("---"):
        return False
    end = text.find("\n---", 3)
    block = text[3:end] if end != -1 else text[3:]
    try:
        fields = yaml.safe_load(block)
    except (yaml.YAMLError, RecursionError):
        return False
    if not isinstance(fields, dict):
        return False
    return fields.get("source") == SOURCE_MARKER


def _write(path: Path, text: str) -> None:
    """Write a file through a temporary file in the same directory.

    A reader never sees half a note, and a write that fails leaves the
    previous note where it was. A character that cannot be encoded is
    replaced rather than fatal, so a malformed value cannot fail the same
    row forever.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(
        dir=str(path.parent), prefix=".ph-", suffix=".tmp"
    )
    try:
        with os.fdopen(
            handle, "w", encoding="utf-8", errors="replace", newline="\n"
        ) as stream:
            stream.write(text)
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise

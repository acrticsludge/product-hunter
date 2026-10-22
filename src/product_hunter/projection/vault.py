"""The vault layout: which folder a note lands in, and what the file is called.

The layout owns the seven top-level folders, the six score bands and the five
archive reasons. Every path it returns stays inside the vault root, because
a note name is built from data a page could have written.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from product_hunter.projection.sanitize import slug

DASHBOARD = "00-Dashboard"
IDEAS = "01-Ideas"
RESEARCH = "02-Research"
DAILY_REPORTS = "03-Daily-Reports"
EXPERIMENTS = "04-Experiments"
RESEARCH_REQUESTS = "05-Research-Requests"
ARCHIVE = "99-Archive"

# The folder of the dashboard that holds the index of the daily briefs.
DAILY_BRIEFS = "Daily Briefs"

# The seven top-level folders of the vault. They are fixed: a folder with
# nothing in it is still a folder of the vault.
TOP_LEVEL_FOLDERS = (
    DASHBOARD,
    IDEAS,
    RESEARCH,
    DAILY_REPORTS,
    EXPERIMENTS,
    RESEARCH_REQUESTS,
    ARCHIVE,
)

# The score bands, from the highest to the lowest. An opportunity with no
# recorded potential goes to the unscored band rather than to a band it does
# not belong to.
BAND_EXCEPTIONAL = "🔥 90-100 Exceptional"
BAND_STRONG = "🟢 80-89 Strong"
BAND_INTERESTING = "🟡 70-79 Interesting"
BAND_WEAK = "⚪ 50-69 Weak Evidence"
BAND_LOW = "🔴 00-49 Low Potential"
BAND_UNSCORED = "⚫ Unscored"

# The lifecycles that file an opportunity away instead of under a band.
ARCHIVE_REASONS: dict[str, str] = {
    "ALREADY_SOLVED": "Already-Solved",
    "REJECTED": "Rejected",
    "INSUFFICIENT_EVIDENCE": "Insufficient-Evidence",
    "STALE": "Stale",
    "DEAD": "Dead",
}

# The folders an experiment can be filed under.
EXPERIMENT_FOLDERS: dict[str, str] = {
    "active": "Active",
    "running": "Active",
    "in_progress": "Active",
    "completed": "Completed",
    "complete": "Completed",
    "succeeded": "Completed",
    "success": "Completed",
    "failed": "Failed",
    "failure": "Failed",
    "dead": "Failed",
    "cancelled": "Failed",
}

_SAFE_COMPONENT = re.compile(r"^[A-Za-z0-9._-]{1,120}$")


def band_for(potential: float | None) -> str:
    """The band a potential falls in."""
    if potential is None:
        return BAND_UNSCORED
    if potential >= 90:
        return BAND_EXCEPTIONAL
    if potential >= 80:
        return BAND_STRONG
    if potential >= 70:
        return BAND_INTERESTING
    if potential >= 50:
        return BAND_WEAK
    return BAND_LOW


def archive_reason(lifecycle: str) -> str | None:
    """The archive folder a lifecycle belongs in, or None when it stays."""
    return ARCHIVE_REASONS.get(lifecycle.strip().upper())


def experiment_folder(status: str) -> str:
    """The folder an experiment status belongs in.

    An experiment that is neither finished nor failed is still open.
    """
    return EXPERIMENT_FOLDERS.get(status.strip().lower(), "Active")


class VaultLayout:
    """The folders and file names of one vault."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    # --- the note files ---------------------------------------------------

    def opportunity_path(
        self,
        *,
        title: str,
        opportunity_id: str,
        potential: float | None,
        lifecycle: str,
    ) -> str:
        """Where an opportunity note goes: a band, or an archive folder."""
        name = _note_name(title, opportunity_id)
        reason = archive_reason(lifecycle)
        if reason is not None:
            return f"{ARCHIVE}/{reason}/{name}.md"
        return f"{IDEAS}/{band_for(potential)}/{name}.md"

    def source_path(self, source_id: str) -> str:
        return f"{RESEARCH}/Sources/{_component(source_id)}.md"

    def community_path(self, community_id: str) -> str:
        return f"{RESEARCH}/Communities/{_component(community_id)}.md"

    def market_path(self, market: str) -> str:
        return f"{RESEARCH}/Markets/{_component(market)}.md"

    def trends_path(self) -> str:
        return f"{RESEARCH}/Trends/README.md"

    def daily_path(self, day: str) -> str:
        return f"{DAILY_REPORTS}/{_component(day)}.md"

    def daily_briefs_path(self) -> str:
        """Where the index of the daily briefs goes."""
        return f"{DASHBOARD}/{DAILY_BRIEFS}/README.md"

    def dashboard_path(self, name: str) -> str:
        return f"{DASHBOARD}/{_folder_name(name)}.md"

    def experiment_path(self, experiment_id: str, status: str) -> str:
        folder = experiment_folder(status)
        return f"{EXPERIMENTS}/{folder}/{_component(experiment_id)}.md"

    def request_path(self, request_id: str) -> str:
        return f"{RESEARCH_REQUESTS}/{_component(request_id)}.md"

    # --- the folders on disk -----------------------------------------------

    def create_folders(self) -> None:
        """The seven top-level folders, created on every run.

        A folder that holds nothing yet is still a folder of the vault, and
        a reader expects to find it there.
        """
        for name in TOP_LEVEL_FOLDERS:
            (self.root / name).mkdir(parents=True, exist_ok=True)

    # --- the paths on disk ------------------------------------------------

    def resolve(self, relative: str) -> Path:
        """The file a relative note path names, checked against the root.

        A note path is built from data, so it is checked before anything is
        written: a path that would leave the vault is refused.
        """
        target = (self.root / relative).resolve()
        root = self.root.resolve()
        if target != root and root not in target.parents:
            raise ValueError(f"the note path leaves the vault root: {relative}")
        return target


def _note_name(title: str, opportunity_id: str) -> str:
    """The file name of an opportunity note: a slug and eight hex characters.

    The slug keeps the vault readable and the hash keeps two opportunities
    with the same title apart.
    """
    digest = sha_prefix(opportunity_id)
    return f"{slug(title)}--{digest}"


def sha_prefix(value: str, length: int = 8) -> str:
    """The first hex characters of the sha256 of a value."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:length]


def _component(value: str) -> str:
    """A file name component that cannot name a directory.

    A value that cannot be a file name gets a slug and a hash, so two
    values that slugify to the same name cannot share one file.
    """
    if _SAFE_COMPONENT.match(value) and value not in {".", ".."}:
        return value
    return f"{slug(value)}--{sha_prefix(value)}"


def _folder_name(value: str) -> str:
    """The name of a file the layout itself names."""
    if not value or value in {".", ".."} or "/" in value or "\\" in value:
        return "untitled"
    return value

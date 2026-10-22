"""The cadence file, read into the schedules the queue runs on.

The beats of ``config/cadence.yaml`` are the operation's timetable, and the
file is the only place the intervals live. A file that cannot be read is a
configuration fault: the refusal names the file, and nothing falls back to
numbers written in the code.

This module sits above ``product_hunter.db.scheduler`` and imports it at the
top, so the scheduler's own reader of the file is a lazy import. The
dependency stays one way.
"""

from __future__ import annotations

from enum import Enum
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from product_hunter.autonomy.maintenance import MAINTENANCE_STAGE
from product_hunter.config import repo_root
from product_hunter.db.scheduler import Priority, Schedule, ScheduleError

CADENCE_NAME = Path("config") / "cadence.yaml"

# The stage the upkeep runs on. The beat names how often the upkeep happens,
# and the operation layer runs it on that interval, so the beat is not turned
# into a queued job as well: the same work would then happen twice.
UPKEEP_STAGE = MAINTENANCE_STAGE


class CadenceError(ScheduleError, ValueError):
    """The cadence file cannot be used."""


class CadenceKind(str, Enum):
    """How hot a beat is.

    Every kind is the same thing under the hood: a fixed UTC grid with an
    offset. The kind is there so an operator can read the file and see which
    beats are hot, and so a primitive that is not a plain interval has
    somewhere to live later.
    """

    frequent = "frequent"
    periodic = "periodic"
    daily = "daily"


class CadenceBeat(BaseModel):
    """One beat of the timetable."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    kind: CadenceKind
    stage: str = Field(min_length=1)
    interval_seconds: int = Field(gt=0)
    offset_seconds: int = Field(default=0, ge=0)
    priority: str = "P3"
    catchup_limit: int | None = Field(default=None, ge=1)
    max_attempts: int = Field(default=3, ge=1)
    enabled: bool = True


class CadenceConfig(BaseModel):
    """The whole file."""

    model_config = ConfigDict(extra="forbid")

    version: int = Field(gt=0)
    cadence: list[CadenceBeat]


def load_cadence(path: Path | None = None) -> CadenceConfig:
    """The cadence file, read and checked.

    The default path is the one under the repository root, so every process
    reads the same file.
    """
    config_path = path if path is not None else repo_root() / CADENCE_NAME
    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except OSError as exc:
        raise CadenceError(
            f"the cadence file {config_path} cannot be read: {exc}"
        ) from exc
    except yaml.YAMLError as exc:
        raise CadenceError(
            f"the cadence file {config_path} is not valid YAML: {exc}"
        ) from exc
    try:
        return CadenceConfig.model_validate(raw)
    except ValidationError as exc:
        raise CadenceError(
            f"the cadence file {config_path} is not usable: {_first_error(exc)}"
        ) from exc


def load_cadence_schedules(path: Path | None = None) -> list[Schedule]:
    """The work beats of the cadence file, as schedules, in file order.

    The upkeep beat is left out on purpose. The operation layer runs the
    upkeep itself on that beat's interval, so queueing it as well would run
    the same pass twice. The beat stays in the file, because the interval it
    names is where the upkeep runs.
    """
    config = load_cadence(path)
    return [
        _to_schedule(beat)
        for beat in config.cadence
        if beat.stage != UPKEEP_STAGE
    ]


def upkeep_beat(config: CadenceConfig) -> CadenceBeat | None:
    """The beat the upkeep runs on, whether it is enabled or not.

    A caller can tell a disabled beat from a missing one: the first says the
    operator turned the upkeep off, the second says the file does not name it
    at all.
    """
    for beat in config.cadence:
        if beat.stage == UPKEEP_STAGE:
            return beat
    return None


def _to_schedule(beat: CadenceBeat) -> Schedule:
    """One beat as a schedule the queue can run."""
    try:
        priority = int(Priority[beat.priority])
    except KeyError:
        raise CadenceError(
            f"the beat {beat.name!r} names the priority band {beat.priority!r}, "
            f"which is not one of: {', '.join(band.name for band in Priority)}"
        ) from None
    return Schedule(
        name=beat.name,
        stage=beat.stage,
        interval_seconds=beat.interval_seconds,
        offset_seconds=beat.offset_seconds,
        priority=priority,
        catchup_limit=beat.catchup_limit,
        max_attempts=beat.max_attempts,
        enabled=beat.enabled,
    )


def _first_error(exc: ValidationError) -> str:
    """The first refusal of the file, naming the field it names."""
    errors = exc.errors()
    if not errors:
        return "the file does not match the cadence schema"
    first = errors[0]
    location = ".".join(str(part) for part in first.get("loc", ()))
    return f"{location}: {first.get('msg', 'invalid value')}"

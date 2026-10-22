"""The cadence file and the schedules it turns into.

Every test here names what must hold, so a failure says which claim broke.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

from product_hunter.autonomy import cadence as cadence_module
from product_hunter.autonomy.cadence import (
    CADENCE_NAME,
    UPKEEP_STAGE,
    CadenceError,
    load_cadence,
    load_cadence_schedules,
    upkeep_beat,
)
from product_hunter.autonomy.engine import OperationEngine
from product_hunter.config import (
    AutonomyConfig,
    OperationBudgetLimits,
    load_autonomy_config,
    repo_root,
)
from product_hunter.db import Store
from product_hunter.db.checkpoints import save_checkpoint
from product_hunter.db.scheduler import Priority, ScheduleError, default_schedules
from product_hunter.runtime.identity import WorkerIdentity

CADENCE_FILE = repo_root() / CADENCE_NAME
NOW = datetime(2026, 10, 21, 12, 0, tzinfo=timezone.utc)


class _MovingClock:
    """A clock the test moves by hand."""

    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        """Move the clock forward."""
        self.now = self.now + timedelta(seconds=seconds)


def _identity() -> WorkerIdentity:
    return WorkerIdentity(
        worker_id="worker-test-1",
        hostname="test-host",
        pid=4242,
        started_at="2026-10-21T00:00:00+00:00",
    )


def _config() -> AutonomyConfig:
    """The shipped config, with a budget the upkeep test cannot exhaust."""
    return load_autonomy_config().model_copy(
        update={
            "heartbeat_seconds": 0.0,
            "budget": OperationBudgetLimits(
                per_operation={"wall_time_seconds": 100000}
            ),
        }
    )


def _finished_run_with_old_checkpoint(store: Store, *, step: str) -> None:
    """A finished run with a checkpoint older than the retention."""
    store.conn.execute(
        "INSERT OR IGNORE INTO research_runs (run_id, public_id, objective, "
        "original_request, completed_at) "
        "VALUES ('run-upkeep', 'run-upkeep', 'test', '', ?)",
        (NOW.isoformat(),),
    )
    save_checkpoint(
        store.conn,
        run_id="run-upkeep",
        stage="DISCOVERY",
        step=step,
        state={"cursor": 1},
        checkpoint_version=1,
        now=NOW - timedelta(days=30),
    )


def _checkpoint_count(store: Store) -> int:
    """The number of checkpoints the store holds."""
    return int(
        store.conn.execute("SELECT count(*) FROM research_checkpoints").fetchone()[0]
    )


def _beat(**overrides: object) -> dict[str, object]:
    """One beat of the file, with the fields a test changes."""
    beat: dict[str, object] = {
        "name": "sample",
        "kind": "periodic",
        "stage": "PROCESS",
        "interval_seconds": 3600,
        "offset_seconds": 0,
        "priority": "P2",
        "catchup_limit": 2,
        "max_attempts": 3,
        "enabled": True,
    }
    beat.update(overrides)
    return beat


def _write(tmp_path: Path, data: dict[str, object]) -> Path:
    """A cadence file holding the given content."""
    path = tmp_path / "cadence.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path


def test_the_cadence_file_parses_into_the_beats_it_declares() -> None:
    declared = yaml.safe_load(CADENCE_FILE.read_text(encoding="utf-8"))
    config = load_cadence()
    assert config.version == declared["version"]
    assert len(config.cadence) == len(declared["cadence"])
    for beat, raw in zip(config.cadence, declared["cadence"]):
        assert beat.name == raw["name"]
        assert beat.kind.value == raw["kind"]
        assert beat.stage == raw["stage"]
        assert beat.interval_seconds == raw["interval_seconds"]
        assert beat.offset_seconds == raw["offset_seconds"]
        assert beat.priority == raw["priority"]
        assert beat.catchup_limit == raw["catchup_limit"]
        assert beat.max_attempts == raw["max_attempts"]
        assert beat.enabled == raw["enabled"]


def test_the_file_numbers_are_the_numbers_the_schedules_use() -> None:
    config = load_cadence()
    schedules = {schedule.name: schedule for schedule in load_cadence_schedules()}
    for beat in config.cadence:
        if beat.stage == UPKEEP_STAGE:
            continue
        schedule = schedules[beat.name]
        assert schedule.stage == beat.stage
        assert schedule.interval_seconds == beat.interval_seconds
        assert schedule.offset_seconds == beat.offset_seconds
        assert schedule.priority == int(Priority[beat.priority])
        assert schedule.catchup_limit == beat.catchup_limit
        assert schedule.max_attempts == beat.max_attempts


def test_the_work_beats_of_the_file_are_the_queued_schedules_in_file_order() -> None:
    config = load_cadence()
    work = [beat for beat in config.cadence if beat.stage != UPKEEP_STAGE]
    schedules = default_schedules()
    assert [schedule.name for schedule in schedules] == [
        beat.name for beat in work
    ]


def test_the_upkeep_beat_is_not_one_of_the_queued_schedules() -> None:
    beat = upkeep_beat(load_cadence())
    assert beat is not None
    assert beat.stage == UPKEEP_STAGE
    stages = [schedule.stage for schedule in default_schedules()]
    assert UPKEEP_STAGE not in stages


def test_the_file_still_declares_the_upkeep_beat_enabled() -> None:
    beat = upkeep_beat(load_cadence())
    assert beat is not None
    assert beat.enabled is True
    assert beat.interval_seconds == 3600


def test_an_unknown_priority_band_is_refused_by_name(tmp_path: Path) -> None:
    path = _write(tmp_path, {"version": 1, "cadence": [_beat(priority="P9")]})
    with pytest.raises(CadenceError) as refused:
        load_cadence_schedules(path)
    assert "P9" in str(refused.value)


def test_a_beat_without_a_positive_interval_is_refused(tmp_path: Path) -> None:
    path = _write(
        tmp_path, {"version": 1, "cadence": [_beat(interval_seconds=0)]}
    )
    with pytest.raises(CadenceError) as refused:
        load_cadence(path)
    assert "interval_seconds" in str(refused.value)


def test_the_engine_runs_the_upkeep_on_the_interval_the_beat_declares() -> None:
    store = Store.open(":memory:")
    try:
        clock = _MovingClock(NOW)
        engine = OperationEngine(
            store.conn, _identity(), {}, config=_config(), clock=clock
        )
        # The upkeep beat of the file is an hour, so the upkeep runs on the
        # first cycle and then waits for the hour to pass.
        _finished_run_with_old_checkpoint(store, step="first")
        engine.run_once()
        assert _checkpoint_count(store) == 0

        _finished_run_with_old_checkpoint(store, step="second")
        clock.advance(60)
        engine.run_once()
        assert _checkpoint_count(store) == 1

        clock.advance(3600)
        engine.run_once()
        assert _checkpoint_count(store) == 0
    finally:
        store.close()


def test_the_entry_point_refuses_a_broken_cadence_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    from product_hunter.autonomy import __main__ as entry

    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "cadence.yaml").write_text(
        "version: 1\n"
        "cadence:\n"
        "  - name: broken\n"
        "    kind: periodic\n"
        "    stage: PROCESS\n"
        "    interval_seconds: 0\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(cadence_module, "repo_root", lambda: tmp_path)
    monkeypatch.setenv("PH_DB_PATH", str(tmp_path / "ph.db"))
    monkeypatch.setenv("PH_WORKER_LOCK", str(tmp_path / "worker.lock"))

    # A configuration fault is a clean refusal and not a traceback.
    assert entry.main(["--once"]) == 2
    error = capsys.readouterr().err
    payload = json.loads(error.strip().splitlines()[-1])
    assert payload["ok"] is False
    assert payload["error"].startswith("cadence: ")
    assert "cadence.yaml" in payload["error"]


def test_a_missing_cadence_file_is_refused_by_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    missing = tmp_path / "nope.yaml"
    with pytest.raises(ScheduleError) as refused:
        load_cadence_schedules(missing)
    assert "nope.yaml" in str(refused.value)

    # The file under the root is gone, so the default schedules are refused
    # as well. Nothing falls back to numbers written in the code.
    monkeypatch.setattr(cadence_module, "repo_root", lambda: tmp_path)
    with pytest.raises(ScheduleError) as refused:
        default_schedules()
    assert "cadence.yaml" in str(refused.value)

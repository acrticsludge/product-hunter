from __future__ import annotations

import pytest

from product_hunter.db import Store
from product_hunter.db.checkpoints import (
    CheckpointConflict,
    CheckpointError,
    list_checkpoints,
    load_checkpoint,
    save_checkpoint,
)
from product_hunter.fetch import utcnow

NOW = utcnow()


def _store_with_run(run_id: str = "run-1") -> Store:
    store = Store.open(":memory:")
    store.conn.execute(
        """
        INSERT INTO research_runs (
            run_id, public_id, objective, original_request, status,
            created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (run_id, run_id, "test", "", "running", NOW.isoformat(), NOW.isoformat()),
    )
    return store


def test_save_and_load_a_checkpoint() -> None:
    store = _store_with_run()
    try:
        saved = save_checkpoint(
            store.conn,
            run_id="run-1",
            stage="DISCOVERY",
            step="query-1",
            state={"cursor": 3, "pending": ["a", "b"]},
            input_refs=["input.json"],
            output_refs=["out.json"],
            now=NOW,
        )
        loaded = load_checkpoint(
            store.conn, run_id="run-1", stage="DISCOVERY", step="query-1"
        )
        assert loaded is not None
        assert loaded.checkpoint_id == saved.checkpoint_id
        assert loaded.state == {"cursor": 3, "pending": ["a", "b"]}
        assert loaded.input_refs == ["input.json"]
        assert loaded.output_refs == ["out.json"]
        assert loaded.completed_at is not None
    finally:
        store.close()


def test_re_saving_the_same_state_is_idempotent() -> None:
    store = _store_with_run()
    try:
        first = save_checkpoint(
            store.conn, run_id="run-1", stage="DISCOVERY", state={"n": 1}, now=NOW
        )
        second = save_checkpoint(
            store.conn, run_id="run-1", stage="DISCOVERY", state={"n": 1}, now=NOW
        )
        assert first.checkpoint_id == second.checkpoint_id
        assert len(list_checkpoints(store.conn, run_id="run-1")) == 1
    finally:
        store.close()


def test_changed_state_under_the_same_version_is_refused() -> None:
    store = _store_with_run()
    try:
        save_checkpoint(
            store.conn, run_id="run-1", stage="DISCOVERY", state={"n": 1}, now=NOW
        )
        with pytest.raises(CheckpointConflict):
            save_checkpoint(
                store.conn,
                run_id="run-1",
                stage="DISCOVERY",
                state={"n": 2},
                now=NOW,
            )
    finally:
        store.close()


def test_a_new_version_becomes_the_latest_checkpoint() -> None:
    store = _store_with_run()
    try:
        save_checkpoint(
            store.conn,
            run_id="run-1",
            stage="DISCOVERY",
            state={"n": 1},
            checkpoint_version=1,
            now=NOW,
        )
        save_checkpoint(
            store.conn,
            run_id="run-1",
            stage="DISCOVERY",
            state={"n": 2},
            checkpoint_version=2,
            now=NOW,
        )
        latest = load_checkpoint(store.conn, run_id="run-1", stage="DISCOVERY")
        assert latest is not None
        assert latest.checkpoint_version == 2
        assert latest.state == {"n": 2}
        assert len(list_checkpoints(store.conn, run_id="run-1")) == 2
    finally:
        store.close()


def test_changed_refs_under_the_same_version_are_refused() -> None:
    store = _store_with_run()
    try:
        save_checkpoint(
            store.conn,
            run_id="run-1",
            stage="DISCOVERY",
            state={"n": 1},
            input_refs=["old-input"],
            now=NOW,
        )
        with pytest.raises(CheckpointConflict):
            save_checkpoint(
                store.conn,
                run_id="run-1",
                stage="DISCOVERY",
                state={"n": 1},
                input_refs=["new-input"],
                now=NOW,
            )
    finally:
        store.close()


def test_a_missing_run_is_refused() -> None:
    store = Store.open(":memory:")
    try:
        with pytest.raises(CheckpointError):
            save_checkpoint(
                store.conn, run_id="missing", stage="DISCOVERY", state={}
            )
    finally:
        store.close()


def test_an_invalid_version_is_refused() -> None:
    store = _store_with_run()
    try:
        with pytest.raises(CheckpointError):
            save_checkpoint(
                store.conn,
                run_id="run-1",
                stage="DISCOVERY",
                state={},
                checkpoint_version=0,
            )
    finally:
        store.close()

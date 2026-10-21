from __future__ import annotations

import pytest

from product_hunter.runtime.identity import WorkerIdentity
from product_hunter.runtime.lock import LockError, SingleInstanceLock


def _identity() -> WorkerIdentity:
    return WorkerIdentity(
        worker_id="worker-test-1",
        hostname="test-host",
        pid=4242,
        started_at="2026-01-01T00:00:00+00:00",
    )


def test_a_second_lock_on_the_same_path_is_denied(tmp_path) -> None:
    path = tmp_path / "worker.lock"
    first = SingleInstanceLock(path)
    second = SingleInstanceLock(path)
    try:
        assert first.acquire() is True
        assert second.acquire() is False
    finally:
        first.release()
        second.release()


def test_the_lock_is_free_again_after_a_release(tmp_path) -> None:
    path = tmp_path / "worker.lock"
    first = SingleInstanceLock(path)
    second = SingleInstanceLock(path)
    try:
        assert first.acquire() is True
        first.release()
        assert second.acquire() is True
    finally:
        second.release()


def test_the_lock_file_names_the_holder(tmp_path) -> None:
    path = tmp_path / "worker.lock"
    identity = _identity()
    lock = SingleInstanceLock(path, identity=identity)
    assert lock.acquire() is True
    lock.release()
    text = path.read_text(encoding="utf-8")
    assert f"worker_id={identity.worker_id}" in text
    assert f"pid={identity.pid}" in text
    assert f"started_at={identity.started_at}" in text


def test_the_context_manager_refuses_a_locked_path(tmp_path) -> None:
    path = tmp_path / "worker.lock"
    holder = SingleInstanceLock(path)
    assert holder.acquire() is True
    try:
        with pytest.raises(LockError):
            with SingleInstanceLock(path):
                pass
    finally:
        holder.release()


def test_the_context_manager_takes_and_gives_the_lock(tmp_path) -> None:
    path = tmp_path / "worker.lock"
    with SingleInstanceLock(path) as lock:
        assert lock.acquire() is True
    again = SingleInstanceLock(path)
    try:
        assert again.acquire() is True
    finally:
        again.release()


def test_releasing_a_lock_that_was_never_taken_is_safe(tmp_path) -> None:
    lock = SingleInstanceLock(tmp_path / "worker.lock")
    lock.release()
    assert lock.acquire() is True
    lock.release()
    lock.release()

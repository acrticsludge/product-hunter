"""Crash and restart behaviour of the Phase 2 store and worker layer.

Every test names the invariant it holds, so a failure says which claim broke.
Every crash test uses a file-backed database: WAL and the ``-wal`` sidecar only
exist on disk, and an in-memory database cannot outlive the process that made
it. A killed child is the interruption. Lease expiry is driven by writing
``lease_expires_at`` into the past, never by waiting on the wall clock.

What these tests prove is process-crash durability. They do not prove
power-loss durability: ``synchronous = NORMAL`` lets a host power loss discard
recently committed transactions.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path

import pytest

from conftest import _build_run, crash_child, open_store
from product_hunter.db import StoreError
from product_hunter.db.checkpoints import (
    CheckpointError,
    list_checkpoints,
    load_checkpoint,
    load_valid_checkpoint,
    save_checkpoint,
)
from product_hunter.db.jobs import (
    JobContext,
    LeaseLost,
    claim_job,
    complete_job,
    enqueue_job,
    fail_job,
    get_job,
    list_jobs,
    renew_lease,
)
from product_hunter.db.outbox import (
    claim_projection,
    enqueue_projection,
    list_projection_tasks,
    process_outbox,
    recover_projection_leases,
)
from product_hunter.db.worker import Worker
from product_hunter.sources import load_registry

EXPIRED = "2000-01-01T00:00:00+00:00"


def _insert_run(conn: sqlite3.Connection, run_id: str) -> None:
    """The one row a checkpoint needs before it can be saved."""
    conn.execute(
        "INSERT INTO research_runs (run_id, public_id, objective, "
        "original_request, status) VALUES (?, ?, 'test', '', 'running')",
        (run_id, run_id),
    )


def _insert_document(conn: sqlite3.Connection) -> None:
    """One source, one fetch, one document: enough for an evidence row."""
    conn.execute(
        "INSERT INTO sources (source_id, source_type, terms_status) "
        "VALUES ('src-crash', 'web', 'permitted')"
    )
    conn.execute(
        "INSERT INTO fetches (fetch_id, source_id, requested_url) "
        "VALUES ('fetch-crash', 'src-crash', 'https://example.invalid/x')"
    )
    conn.execute(
        """
        INSERT INTO documents (
            doc_id, fetch_id, source_id, url, text_sha256, text_path,
            raw_path, raw_sha256, parser_version
        ) VALUES ('doc-crash-1', 'fetch-crash', 'src-crash',
                  'https://example.invalid/x', ?, 'text.md', 'raw.json', ?, 'v1')
        """,
        ("a" * 64, "b" * 64),
    )


def _counts(conn: sqlite3.Connection, tables: tuple[str, ...]) -> dict[str, int]:
    return {
        table: conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        for table in tables
    }


def _record_count(path: Path) -> int:
    """The number of records in a JSONL artifact."""
    return sum(
        1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    )


def _drop_last_record(path: Path) -> None:
    """Cut the last record off a JSONL artifact, the way a lost tail does."""
    lines = [
        line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    path.write_text("".join(line + "\n" for line in lines[:-1]), encoding="utf-8")


def _accepted_count(path: Path) -> int:
    """The number of accepted candidates in a candidates file."""
    return sum(
        1
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and json.loads(line).get("accepted")
    )


def test_a_committed_transaction_survives_a_process_kill(tmp_path: Path) -> None:
    """V1: a transaction committed before a process kill is visible after restart."""
    db_path = tmp_path / "ph.db"
    store = open_store(db_path)
    store.conn.execute("CREATE TABLE crash_writes (id INTEGER PRIMARY KEY, value TEXT)")
    store.close()

    child = crash_child(
        db_path,
        """
        from product_hunter.db.connection import connect
        conn = connect(sys.argv[1])
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("INSERT INTO crash_writes (value) VALUES ('committed')")
        conn.execute("COMMIT")
        os._exit(9)
        """,
    )
    assert child.returncode == 9, child.stderr

    store = open_store(db_path)
    try:
        rows = store.conn.execute("SELECT value FROM crash_writes").fetchall()
        assert [row["value"] for row in rows] == ["committed"]
        assert store.integrity_check() == "ok"
    finally:
        store.close()


def test_an_open_transaction_leaves_nothing_behind(tmp_path: Path) -> None:
    """V2: a transaction open during a process kill leaves no trace."""
    db_path = tmp_path / "ph.db"
    store = open_store(db_path)
    store.conn.execute("CREATE TABLE crash_writes (id INTEGER PRIMARY KEY, value TEXT)")
    store.close()

    child = crash_child(
        db_path,
        """
        from product_hunter.db.connection import connect
        conn = connect(sys.argv[1])
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("INSERT INTO crash_writes (value) VALUES ('never')")
        os._exit(9)
        """,
    )
    assert child.returncode == 9, child.stderr

    store = open_store(db_path)
    try:
        count = store.conn.execute("SELECT count(*) FROM crash_writes").fetchone()[0]
        assert count == 0
        assert store.integrity_check() == "ok"
    finally:
        store.close()


def test_a_job_whose_worker_vanished_is_recoverable(tmp_path: Path) -> None:
    """V3: a job whose worker vanished is recoverable, never half-applied."""
    db_path = tmp_path / "ph.db"
    child = crash_child(
        db_path,
        """
        from product_hunter.db import Store
        from product_hunter.db.jobs import claim_job, enqueue_job
        store = Store.open(sys.argv[1])
        enqueue_job(store.conn, stage="WRITE", idempotency_key="crash-job-1")
        claim_job(store.conn, "worker-dead")
        os._exit(9)
        """,
    )
    assert child.returncode == 9, child.stderr

    store = open_store(db_path)
    try:
        jobs = list_jobs(store.conn)
        assert len(jobs) == 1
        job = jobs[0]
        assert job.status == "leased"
        assert job.worker_id == "worker-dead"
        assert job.lease_epoch == 1

        store.conn.execute(
            "UPDATE jobs SET lease_expires_at = ? WHERE id = ?", (EXPIRED, job.id)
        )
        store.conn.execute("CREATE TABLE crash_writes (id INTEGER PRIMARY KEY, value TEXT)")

        def handler(context):
            with context.transaction() as conn:
                conn.execute("INSERT INTO crash_writes (value) VALUES ('recovered')")
            return {"ok": True}

        outcome = Worker(store.conn, "worker-2", {"WRITE": handler}).run_once()
        assert outcome is not None
        assert outcome.status == "succeeded"
        current = get_job(store.conn, job.id)
        assert current is not None
        assert current.status == "succeeded"
        statuses = [
            row["status"]
            for row in store.conn.execute(
                "SELECT status FROM stage_attempts WHERE job_id = ? ORDER BY attempt",
                (job.id,),
            )
        ]
        assert statuses == ["lease_lost", "succeeded"]
        count = store.conn.execute("SELECT count(*) FROM crash_writes").fetchone()[0]
        assert count == 1
    finally:
        store.close()


def test_a_worker_whose_lease_expired_cannot_write(tmp_path: Path) -> None:
    """V4: a worker whose lease expired cannot write anything."""
    db_path = tmp_path / "ph.db"
    store = open_store(db_path)
    try:
        store.conn.execute("CREATE TABLE crash_writes (id INTEGER PRIMARY KEY, value TEXT)")
        job = enqueue_job(store.conn, stage="WRITE", idempotency_key="expired-1")
        claimed = claim_job(store.conn, "worker-a")
        assert claimed is not None
        store.conn.execute(
            "UPDATE jobs SET lease_expires_at = ? WHERE id = ?", (EXPIRED, job.id)
        )

        context = JobContext(store.conn, claimed, worker_id="worker-a")
        with pytest.raises(LeaseLost):
            with context.transaction() as conn:
                conn.execute("INSERT INTO crash_writes (value) VALUES ('stale')")
        count = store.conn.execute("SELECT count(*) FROM crash_writes").fetchone()[0]
        assert count == 0
    finally:
        store.close()


def test_a_superseded_epoch_cannot_complete_fail_renew_or_write(tmp_path: Path) -> None:
    """V5: a worker holding a superseded epoch cannot complete, fail, renew, or write."""
    db_path = tmp_path / "ph.db"
    store = open_store(db_path)
    try:
        store.conn.execute("CREATE TABLE crash_writes (id INTEGER PRIMARY KEY, value TEXT)")
        job = enqueue_job(store.conn, stage="WRITE", idempotency_key="superseded-1")
        first = claim_job(store.conn, "worker-a")
        assert first is not None
        assert first.lease_epoch == 1
        store.conn.execute(
            "UPDATE jobs SET lease_expires_at = ? WHERE id = ?", (EXPIRED, job.id)
        )
        second = claim_job(store.conn, "worker-b")
        assert second is not None
        assert second.lease_epoch == 2

        with pytest.raises(LeaseLost):
            complete_job(
                store.conn, first, worker_id="worker-a", lease_epoch=1, result={"stale": True}
            )
        with pytest.raises(LeaseLost):
            fail_job(store.conn, first, worker_id="worker-a", lease_epoch=1, error="stale")
        with pytest.raises(LeaseLost):
            renew_lease(store.conn, first, worker_id="worker-a", lease_epoch=1)

        context = JobContext(store.conn, first, worker_id="worker-a")
        with pytest.raises(LeaseLost):
            with context.transaction() as conn:
                conn.execute("INSERT INTO crash_writes (value) VALUES ('stale')")
        count = store.conn.execute("SELECT count(*) FROM crash_writes").fetchone()[0]
        assert count == 0
    finally:
        store.close()


def test_two_workers_racing_for_one_job_produce_one_claim(tmp_path: Path) -> None:
    """V6: two workers racing for one job produce exactly one claim."""
    db_path = tmp_path / "ph.db"
    store = open_store(db_path)
    try:
        enqueue_job(store.conn, stage="WRITE", idempotency_key="race-1")
    finally:
        store.close()

    barrier = threading.Barrier(2)
    claims: list[object] = []
    errors: list[BaseException] = []
    lock = threading.Lock()

    def claim(worker_id: str) -> None:
        local = open_store(db_path)
        try:
            barrier.wait(timeout=30)
            result = claim_job(local.conn, worker_id)
        except BaseException as exc:  # pragma: no cover - reported below
            with lock:
                errors.append(exc)
            return
        finally:
            local.close()
        with lock:
            claims.append(result)

    threads = [
        threading.Thread(target=claim, args=(worker_id,))
        for worker_id in ("worker-a", "worker-b")
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert errors == []
    assert len(claims) == 2
    assert sum(1 for result in claims if result is not None) == 1

    store = open_store(db_path)
    try:
        count = store.conn.execute("SELECT count(*) FROM jobs").fetchone()[0]
        assert count == 1
    finally:
        store.close()


def test_a_rollback_inside_a_fenced_transaction_discards_every_write(
    tmp_path: Path,
) -> None:
    """V7: a rollback inside a fenced transaction discards every write in it."""
    db_path = tmp_path / "ph.db"
    store = open_store(db_path)
    try:
        store.conn.execute("CREATE TABLE crash_writes (id INTEGER PRIMARY KEY, value TEXT)")
        job = enqueue_job(store.conn, stage="WRITE", idempotency_key="rollback-1")

        def handler(context):
            with context.transaction() as conn:
                conn.execute("INSERT INTO crash_writes (value) VALUES ('one')")
                conn.execute("INSERT INTO crash_writes (value) VALUES ('two')")
                raise RuntimeError("boom")

        outcome = Worker(store.conn, "worker-1", {"WRITE": handler}).run_once()
        assert outcome is not None
        assert outcome.status == "failed"
        current = get_job(store.conn, job.id)
        assert current is not None
        assert current.status == "queued"
        count = store.conn.execute("SELECT count(*) FROM crash_writes").fetchone()[0]
        assert count == 0
    finally:
        store.close()


def test_a_checkpoint_survives_a_restart(tmp_path: Path) -> None:
    """V8: a checkpoint written before a crash is readable after restart."""
    db_path = tmp_path / "ph.db"
    store = open_store(db_path)
    try:
        _insert_run(store.conn, "run-crash")
        saved = save_checkpoint(
            store.conn,
            run_id="run-crash",
            stage="DISCOVERY",
            step="query-1",
            state={"cursor": 3},
            checkpoint_version=1,
        )
    finally:
        store.close()

    store = open_store(db_path)
    try:
        loaded = load_checkpoint(
            store.conn, run_id="run-crash", stage="DISCOVERY", step="query-1"
        )
        assert loaded is not None
        assert loaded.checkpoint_id == saved.checkpoint_id
        assert loaded.checkpoint_version == 1
        assert loaded.state == {"cursor": 3}
    finally:
        store.close()


def test_a_corrupt_checkpoint_does_not_stop_recovery(tmp_path: Path) -> None:
    """V9: a corrupt checkpoint does not stop recovery."""
    db_path = tmp_path / "ph.db"
    store = open_store(db_path)
    try:
        _insert_run(store.conn, "run-crash")
        save_checkpoint(
            store.conn,
            run_id="run-crash",
            stage="DISCOVERY",
            state={"n": 1},
            checkpoint_version=1,
        )
        save_checkpoint(
            store.conn,
            run_id="run-crash",
            stage="DISCOVERY",
            state={"n": 2},
            checkpoint_version=2,
        )
        store.conn.execute(
            "UPDATE research_checkpoints SET state_json = ? "
            "WHERE run_id = ? AND checkpoint_version = 2",
            ("{not json", "run-crash"),
        )

        with pytest.raises(CheckpointError):
            load_checkpoint(store.conn, run_id="run-crash", stage="DISCOVERY")

        valid = load_valid_checkpoint(
            store.conn, run_id="run-crash", stage="DISCOVERY"
        )
        assert valid is not None
        assert valid.checkpoint_version == 1
        assert valid.state == {"n": 1}

        listed = list_checkpoints(store.conn, run_id="run-crash", skip_invalid=True)
        assert [row.checkpoint_version for row in listed] == [1]
        with pytest.raises(CheckpointError):
            list_checkpoints(store.conn, run_id="run-crash")
    finally:
        store.close()


def test_an_outbox_task_lands_with_the_evidence_or_with_neither(tmp_path: Path) -> None:
    """V10: an outbox task is committed with the evidence it projects, or with neither."""
    db_path = tmp_path / "ph.db"
    store = open_store(db_path)
    try:
        _insert_document(store.conn)
    finally:
        store.close()

    killed = crash_child(
        db_path,
        """
        from product_hunter.db import Store
        from product_hunter.db.jobs import JobContext, claim_job, enqueue_job
        from product_hunter.db.outbox import enqueue_projection
        store = Store.open(sys.argv[1])
        enqueue_job(store.conn, stage="PROJECT", idempotency_key="outbox-crash-1")
        claimed = claim_job(store.conn, "worker-crash")
        context = JobContext(store.conn, claimed, worker_id="worker-crash")
        with context.transaction() as conn:
            conn.execute(
                "INSERT INTO evidence (span_id, doc_id, quote, char_start, char_end) "
                "VALUES ('ev-crash-1', 'doc-crash-1', 'a quote', 0, 0)"
            )
            enqueue_projection(
                conn,
                target="crash-target",
                entity_type="run",
                entity_id="run-crash",
                idempotency_key="projection:crash-1",
                payload={"run_id": "run-crash"},
            )
            os._exit(9)
        """,
    )
    assert killed.returncode == 9, killed.stderr

    store = open_store(db_path)
    try:
        count = store.conn.execute("SELECT count(*) FROM evidence").fetchone()[0]
        assert count == 0
        count = store.conn.execute("SELECT count(*) FROM projection_tasks").fetchone()[0]
        assert count == 0
    finally:
        store.close()

    committed = crash_child(
        db_path,
        """
        from product_hunter.db import Store
        from product_hunter.db.jobs import JobContext, claim_job, enqueue_job
        from product_hunter.db.outbox import enqueue_projection
        store = Store.open(sys.argv[1])
        enqueue_job(store.conn, stage="PROJECT", idempotency_key="outbox-crash-2")
        claimed = claim_job(store.conn, "worker-crash")
        context = JobContext(store.conn, claimed, worker_id="worker-crash")
        with context.transaction() as conn:
            conn.execute(
                "INSERT INTO evidence (span_id, doc_id, quote, char_start, char_end) "
                "VALUES ('ev-crash-2', 'doc-crash-1', 'a quote', 0, 0)"
            )
            enqueue_projection(
                conn,
                target="crash-target",
                entity_type="run",
                entity_id="run-crash",
                idempotency_key="projection:crash-2",
                payload={"run_id": "run-crash"},
            )
        os._exit(9)
        """,
    )
    assert committed.returncode == 9, committed.stderr

    store = open_store(db_path)
    try:
        count = store.conn.execute("SELECT count(*) FROM evidence").fetchone()[0]
        assert count == 1
        tasks = list_projection_tasks(store.conn)
        assert len(tasks) == 1
        assert tasks[0].status == "pending"

        projected: list[str] = []
        processed = process_outbox(
            store.conn,
            {"crash-target": lambda task: projected.append(task.task_id)},
            "worker-restart",
        )
        assert processed == 1
        assert projected == [tasks[0].task_id]
        assert list_projection_tasks(store.conn)[0].status == "done"
    finally:
        store.close()


def test_a_replayed_import_creates_no_duplicate_rows(tmp_path: Path) -> None:
    """V11: replaying an interrupted import creates no duplicate authoritative rows."""
    run_dir, fetch_log, _documents, accepted = _build_run(tmp_path)
    db_path = tmp_path / "ph.db"
    tables = (
        "documents",
        "evidence",
        "pain_points",
        "research_events",
        "research_artifacts",
        "projection_tasks",
    )

    store = open_store(db_path)
    try:
        store.import_run(run_dir, fetch_log=fetch_log, registry=load_registry())
    finally:
        store.close()

    store = open_store(db_path)
    try:
        before = _counts(store.conn, tables)
        store.import_run(run_dir, fetch_log=fetch_log, registry=load_registry())
        assert _counts(store.conn, tables) == before
        pain_ids = [
            row["pain_id"] for row in store.conn.execute("SELECT pain_id FROM pain_points")
        ]
        assert pain_ids == [accepted.pain_id]
    finally:
        store.close()


@pytest.mark.parametrize(
    ("manifest_key", "artifact", "truncate_file"),
    [
        ("documents", "documents.jsonl", True),
        ("candidates", "candidates.jsonl", True),
        ("calls", "calls.jsonl", True),
        ("accepted", "candidates.jsonl", False),
        ("rejected", "rejects.jsonl", True),
    ],
)
def test_a_truncated_run_directory_is_refused(
    tmp_path: Path,
    manifest_key: str,
    artifact: str,
    truncate_file: bool,
) -> None:
    """V12: a truncated run directory is refused rather than imported."""
    run_dir, fetch_log, _documents, _accepted = _build_run(tmp_path)
    counts = {
        "documents": _record_count(run_dir / "documents.jsonl"),
        "candidates": _record_count(run_dir / "candidates.jsonl"),
        "calls": _record_count(run_dir / "calls.jsonl"),
        "accepted": _accepted_count(run_dir / "candidates.jsonl"),
        "rejected": _record_count(run_dir / "rejects.jsonl"),
    }
    if truncate_file:
        _drop_last_record(run_dir / artifact)
    else:
        counts[manifest_key] += 1
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    manifest.update(counts)
    (run_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    db_path = tmp_path / "ph.db"
    store = open_store(db_path)
    try:
        with pytest.raises(StoreError) as excinfo:
            store.import_run(run_dir, fetch_log=fetch_log, registry=load_registry())
        message = str(excinfo.value)
        assert f"'{artifact}'" in message
        assert f"{manifest_key}=" in message
        count = store.conn.execute("SELECT count(*) FROM documents").fetchone()[0]
        assert count == 0
        assert not store.conn.in_transaction
    finally:
        store.close()


@pytest.mark.parametrize(
    "kill_when",
    [
        'stmt.strip().upper().startswith("INSERT INTO PROJECTION_TASKS")',
        'stmt.strip().upper() == "COMMIT"',
    ],
    ids=["projection_insert", "commit"],
)
def test_the_store_rolls_the_projection_task_back_with_the_evidence(
    tmp_path: Path,
    kill_when: str,
) -> None:
    """V10: the store enqueues the projection task inside the evidence's transaction.

    The child dies inside the import transaction, at the projection insert or
    just before the commit. Either way the store must come back with no
    document, no evidence and no projection task: a projection task enqueued
    after the commit would leave the evidence committed and the task missing.
    """
    run_dir, fetch_log, _documents, _accepted = _build_run(tmp_path)
    db_path = tmp_path / "ph.db"
    child = crash_child(
        db_path,
        f"""
        from product_hunter.db import Store
        from product_hunter.sources import load_registry
        store = Store.open(sys.argv[1])
        store.conn.set_trace_callback(
            lambda stmt: os._exit(9) if {kill_when} else None
        )
        store.import_run(sys.argv[2], fetch_log=sys.argv[3], registry=load_registry())
        os._exit(9)
        """,
        extra_args=[str(run_dir), str(fetch_log)],
    )
    assert child.returncode == 9, child.stderr

    store = open_store(db_path)
    try:
        count = store.conn.execute("SELECT count(*) FROM documents").fetchone()[0]
        assert count == 0
        count = store.conn.execute("SELECT count(*) FROM evidence").fetchone()[0]
        assert count == 0
        count = store.conn.execute("SELECT count(*) FROM projection_tasks").fetchone()[0]
        assert count == 0
        assert store.integrity_check() == "ok"
    finally:
        store.close()


def test_a_handler_that_raises_after_its_own_commit_is_retried_without_duplicating(
    tmp_path: Path,
) -> None:
    """I-C: a handler that commits and then raises is retried without a duplicate write."""
    db_path = tmp_path / "ph.db"
    store = open_store(db_path)
    try:
        store.conn.execute(
            "CREATE TABLE crash_writes (id INTEGER PRIMARY KEY, value TEXT)"
        )
        job = enqueue_job(
            store.conn, stage="WRITE", idempotency_key="handler-raises-1"
        )
        calls = 0

        def handler(context):
            nonlocal calls
            calls += 1
            with context.transaction() as conn:
                conn.execute(
                    "INSERT OR IGNORE INTO crash_writes (id, value) "
                    "VALUES (1, 'written')"
                )
            if calls == 1:
                raise RuntimeError("boom")
            return {"ok": True}

        worker = Worker(store.conn, "worker-1", {"WRITE": handler}, backoff_seconds=0)
        first = worker.run_once()
        assert first is not None
        assert first.status == "failed"
        current = get_job(store.conn, job.id)
        assert current is not None
        assert current.status == "queued"

        second = worker.run_once()
        assert second is not None
        assert second.status == "succeeded"
        statuses = [
            row["status"]
            for row in store.conn.execute(
                "SELECT status FROM stage_attempts WHERE job_id = ? ORDER BY attempt",
                (job.id,),
            )
        ]
        assert statuses == ["failed", "succeeded"]
        assert calls == 2
        count = store.conn.execute("SELECT count(*) FROM crash_writes").fetchone()[0]
        assert count == 1
    finally:
        store.close()


def test_a_projection_task_abandoned_with_an_expired_lease_converges_to_one_row(
    tmp_path: Path,
) -> None:
    """I-D: an abandoned projection task is recovered and converges to one row."""
    db_path = tmp_path / "ph.db"
    store = open_store(db_path)
    try:
        store.conn.execute(
            "CREATE TABLE crash_effects (id TEXT PRIMARY KEY, value TEXT)"
        )
        task = enqueue_projection(
            store.conn,
            target="crash-target",
            entity_type="run",
            entity_id="run-crash",
            idempotency_key="projection:crash-1",
            payload={"run_id": "run-crash"},
        )
        claimed = claim_projection(store.conn, "worker-crash")
        assert claimed is not None
        # the downstream effect happens, then the worker dies before it completes
        store.conn.execute(
            "INSERT OR IGNORE INTO crash_effects (id, value) VALUES (?, ?)",
            (claimed.idempotency_key, "written"),
        )
        store.conn.execute(
            "UPDATE projection_tasks SET lease_expires_at = ? WHERE task_id = ?",
            (EXPIRED, claimed.task_id),
        )
        assert recover_projection_leases(store.conn) == 1

        keys: list[str] = []

        def handler(projection):
            keys.append(projection.idempotency_key)
            store.conn.execute(
                "INSERT OR IGNORE INTO crash_effects (id, value) VALUES (?, ?)",
                (projection.idempotency_key, "written"),
            )

        processed = process_outbox(
            store.conn, {"crash-target": handler}, "worker-restart"
        )
        assert processed == 1
        assert keys == [claimed.idempotency_key]
        assert claimed.idempotency_key == task.idempotency_key
        count = store.conn.execute("SELECT count(*) FROM crash_effects").fetchone()[0]
        assert count == 1
        assert len(list_projection_tasks(store.conn)) == 1
        assert list_projection_tasks(store.conn)[0].status == "done"
    finally:
        store.close()


def test_a_crash_child_that_hangs_fails_the_test_instead_of_hanging_it(
    tmp_path: Path,
) -> None:
    """A crash child that hangs fails the test; it never hangs the suite."""
    with pytest.raises(pytest.fail.Exception):
        crash_child(
            tmp_path / "ph.db",
            """
            import time
            time.sleep(2)
            """,
            timeout=1,
        )

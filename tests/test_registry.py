"""The state layer: run states, transitions, formatting, and the dispatcher.

Self contained on purpose. ``tests/conftest.py`` belongs to another change in
this tree, so this file builds its own database and its own fixtures.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import timedelta

import pytest

from product_hunter.db import (
    connect,
    enqueue_job,
    get_job,
    list_checkpoints,
    list_jobs,
    migrate,
)
from product_hunter.fetch import utcnow
from product_hunter.registry import (
    CreateRequest,
    InvalidRequest,
    InvalidTransition,
    UnknownResearch,
    cancel_run,
    continue_run,
    create_run,
    eligible_runs,
    execute,
    get_run,
    list_runs,
    main,
    pause_run,
    render_run,
    render_runs,
    resume_run,
    retry_run,
    run_log,
)

PUBLIC_ID = re.compile(r"^RH-\d{8}-\d{4}$")


@pytest.fixture
def conn(tmp_path):
    """A migrated database in a temporary file."""
    connection = connect(tmp_path / "registry.db")
    migrate(connection)
    try:
        yield connection
    finally:
        connection.close()


def _create(conn, objective="Find the WhatsApp inventory pain", **kwargs):
    run, _reused = create_run(conn, CreateRequest(objective=objective, **kwargs))
    return run


def _set_status(conn, public_id, status, *, updated_at=None):
    conn.execute(
        "UPDATE research_runs SET status = ?, updated_at = COALESCE(?, updated_at) "
        "WHERE public_id = ?",
        (status, updated_at, public_id),
    )


def _lease(conn, run_id, *, live=True):
    """Write a job lease by hand. The worker itself is not running here."""
    moment = utcnow()
    expires = moment + (timedelta(seconds=60) if live else -timedelta(seconds=60))
    job_id = "job_" + hashlib.sha256(f"{run_id}|{live}".encode()).hexdigest()[:16]
    conn.execute(
        "INSERT INTO jobs (id, run_id, stage, step, payload_json, priority, status, "
        "attempts, max_attempts, idempotency_key, worker_id, lease_epoch, "
        "lease_expires_at, created_at, scheduled_for) "
        "VALUES (?, ?, 'DISCOVERY', NULL, '{}', 0, 'leased', 1, 3, ?, 'worker-a', 1, ?, ?, ?)",
        (
            job_id,
            run_id,
            f"lease:{run_id}|{live}",
            expires.isoformat(),
            moment.isoformat(),
            moment.isoformat(),
        ),
    )
    return job_id


def _event_types(conn, run_id):
    rows = conn.execute(
        "SELECT event_type FROM research_events WHERE run_id = ? ORDER BY created_at, event_id",
        (run_id,),
    ).fetchall()
    return [row["event_type"] for row in rows]


def _event(conn, run_id, event_type):
    """The last event of that type on the run."""
    rows = conn.execute(
        "SELECT * FROM research_events WHERE run_id = ? AND event_type = ? "
        "ORDER BY created_at, event_id",
        (run_id, event_type),
    ).fetchall()
    assert rows, f"no {event_type} event on {run_id}"
    return dict(rows[-1])


# --- create ---------------------------------------------------------------


def test_create_writes_the_run_the_checkpoint_the_event_and_the_job(conn):
    view = _create(conn)

    assert PUBLIC_ID.match(view.public_id)
    assert view.status == "QUEUED"
    assert view.current_stage == "PLAN"
    assert view.resume_count == 0
    assert view.progress_percent == 0
    assert view.worker_active is False

    row = conn.execute(
        "SELECT * FROM research_runs WHERE public_id = ?", (view.public_id,)
    ).fetchone()
    assert row["run_id"] == view.run_id
    assert row["status"] == "QUEUED"
    assert row["contract_version"] == "phase2-v1"
    assert row["chat_id"] is None

    checkpoints = list_checkpoints(conn, run_id=view.run_id)
    assert [(c.stage, c.step) for c in checkpoints] == [("PLAN", "research_requested")]

    assert _event_types(conn, view.run_id) == ["RESEARCH_STARTED"]

    jobs = list_jobs(conn)
    assert len(jobs) == 1
    assert jobs[0].status == "queued"
    assert jobs[0].stage == "DISCOVERY"
    assert jobs[0].idempotency_key == f"research_start:{view.run_id}"
    assert jobs[0].run_id == view.run_id


def test_create_with_a_live_objective_returns_the_same_run(conn):
    first = _create(conn, "Compare   CRM tools")
    second = _create(conn, "compare crm tools")

    assert second.public_id == first.public_id
    assert conn.execute("SELECT count(*) FROM research_runs").fetchone()[0] == 1


def test_create_with_a_terminal_run_creates_a_new_run(conn):
    first = _create(conn, "Compare CRM tools")
    _set_status(conn, first.public_id, "COMPLETED")

    second = _create(conn, "Compare CRM tools")

    assert second.public_id != first.public_id
    assert conn.execute("SELECT count(*) FROM research_runs").fetchone()[0] == 2


# --- list and status ------------------------------------------------------


def test_list_orders_running_first_then_paused_then_the_rest(conn):
    running = _create(conn, "Running topic")
    paused = _create(conn, "Paused topic")
    recent = _create(conn, "Recent topic")
    older = _create(conn, "Older topic")

    _set_status(conn, running.public_id, "RUNNING", updated_at="2026-10-21T12:00:00+00:00")
    _set_status(conn, paused.public_id, "PAUSED", updated_at="2026-10-21T11:00:00+00:00")
    _set_status(conn, recent.public_id, "QUEUED", updated_at="2026-10-21T10:00:00+00:00")
    _set_status(conn, older.public_id, "QUEUED", updated_at="2026-10-21T09:00:00+00:00")

    views = list_runs(conn)

    assert [v.public_id for v in views] == [
        running.public_id,
        paused.public_id,
        recent.public_id,
        older.public_id,
    ]


def test_status_reports_the_run_and_its_counts(conn):
    view = _create(conn)

    result = execute(conn, "status", research_id=view.public_id)

    assert result.ok is True
    assert result.run is not None
    assert result.run.public_id == view.public_id
    assert result.run.job_count == 1
    assert result.run.checkpoint_count == 1
    assert result.run.event_count == 1
    assert result.run.artifact_count == 0


def test_status_of_an_unknown_id_is_unknown_research(conn):
    known = _create(conn)

    result = execute(conn, "status", research_id="RH-20261021-9999")

    assert result.ok is False
    assert result.code == "unknown_research"
    assert "RH-20261021-9999" in result.message
    assert known.public_id in result.message

    with pytest.raises(UnknownResearch):
        get_run(conn, "RH-20261021-9999")


# --- continue -------------------------------------------------------------


def test_continue_queues_the_run_and_raises_the_resume_count(conn):
    view = _create(conn)
    _set_status(conn, view.public_id, "FAILED_RETRYABLE")
    # The work of the run is gone, so the continue has to queue it again.
    conn.execute("UPDATE jobs SET status = 'dead' WHERE run_id = ?", (view.run_id,))

    result = execute(conn, "continue", research_id=view.public_id)

    assert result.ok is True
    assert result.run is not None
    assert result.run.status == "QUEUED"
    assert result.run.resume_count == 1

    keys = [job.idempotency_key for job in list_jobs(conn)]
    assert f"research_continue:{view.run_id}:1" in keys
    continuation = [job for job in list_jobs(conn) if job.stage == "DISCOVERY"]
    assert len(continuation) == 2
    assert "RESEARCH_RESUMED" in _event_types(conn, view.run_id)


def test_continue_reuses_the_pending_job_instead_of_queueing_a_second(conn):
    view = _create(conn)
    job = list_jobs(conn)[0]

    result = execute(conn, "continue", research_id=view.public_id)

    assert result.ok is True
    assert result.run is not None
    assert result.run.resume_count == 1
    jobs = list_jobs(conn)
    assert len(jobs) == 1
    assert jobs[0].id == job.id
    assert jobs[0].status == "queued"
    assert jobs[0].lease_epoch == 1
    assert "RESEARCH_RESUMED" in _event_types(conn, view.run_id)


def test_continue_requeues_the_job_whose_lease_expired_instead_of_adding_one(conn):
    view = _create(conn)
    job = list_jobs(conn)[0]
    expired = (utcnow() - timedelta(seconds=60)).isoformat()
    conn.execute(
        "UPDATE jobs SET status = 'leased', worker_id = 'worker-a', "
        "lease_epoch = 1, lease_expires_at = ? WHERE id = ?",
        (expired, job.id),
    )

    result = execute(conn, "continue", research_id=view.public_id)

    assert result.ok is True
    assert result.run is not None
    assert result.run.resume_count == 1
    jobs = list_jobs(conn)
    # One job, the same one: the expired lease is pending work of this run.
    assert len(jobs) == 1
    assert jobs[0].id == job.id
    assert jobs[0].status == "queued"
    assert jobs[0].lease_epoch == 2
    assert jobs[0].worker_id is None
    assert jobs[0].lease_expires_at is None
    assert get_run(conn, view.public_id).job_count == 1


def test_continue_on_a_terminal_run_is_refused(conn):
    view = _create(conn)
    _set_status(conn, view.public_id, "COMPLETED")

    with pytest.raises(InvalidTransition):
        continue_run(conn, view.public_id)

    result = execute(conn, "continue", research_id=view.public_id)
    assert result.ok is False
    assert result.code == "invalid_transition"


def test_continue_on_a_run_with_a_live_lease_is_refused(conn):
    view = _create(conn)
    _lease(conn, view.run_id)

    with pytest.raises(InvalidTransition):
        continue_run(conn, view.public_id)

    result = execute(conn, "continue", research_id=view.public_id)
    assert result.ok is False
    assert result.code == "invalid_transition"


def test_bare_continue_with_one_eligible_run_continues_it(conn):
    view = _create(conn)

    result = execute(conn, "continue")

    assert result.ok is True
    assert result.needs_selection is False
    assert result.run is not None
    assert result.run.public_id == view.public_id
    assert result.run.resume_count == 1


def test_bare_continue_with_two_eligible_runs_asks_and_never_guesses(conn):
    first = _create(conn, "First topic")
    second = _create(conn, "Second topic")

    result = execute(conn, "continue")

    assert result.ok is True
    assert result.needs_selection is True
    assert result.run is None
    assert {view.public_id for view in result.runs} == {
        first.public_id,
        second.public_id,
    }
    assert len(result.runs) == 2


def test_bare_continue_with_no_eligible_run_reports_no_candidates(conn):
    view = _create(conn)
    _set_status(conn, view.public_id, "COMPLETED")

    result = execute(conn, "continue")

    assert result.ok is True
    assert result.needs_selection is False
    assert result.run is None
    assert result.runs == []


def test_bare_continue_says_no_run_is_eligible_and_not_that_there_is_none(conn):
    view = _create(conn)
    _set_status(conn, view.public_id, "COMPLETED")

    result = execute(conn, "continue")

    assert "no research runs" not in result.text
    assert "no research run is eligible to continue" in result.text


def test_a_cancel_requested_run_is_not_eligible_for_a_bare_continue(conn):
    view = _create(conn)
    _set_status(conn, view.public_id, "CANCEL_REQUESTED")

    assert eligible_runs(conn) == []

    result = execute(conn, "continue")

    assert result.ok is True
    assert result.run is None
    assert result.runs == []
    assert "no research run is eligible to continue" in result.text
    # The cancel still stands: the run was not queued again.
    assert get_run(conn, view.public_id).status == "CANCEL_REQUESTED"


def test_a_named_continue_on_a_cancel_requested_run_is_refused(conn):
    view = _create(conn)
    _set_status(conn, view.public_id, "CANCEL_REQUESTED")

    result = execute(conn, "continue", research_id=view.public_id)

    assert result.ok is False
    assert result.code == "invalid_transition"
    assert "CANCEL_REQUESTED" in result.message
    assert "worker" in result.message
    # The cancel still stands: the run was not queued again.
    assert get_run(conn, view.public_id).status == "CANCEL_REQUESTED"
    assert get_run(conn, view.public_id).job_count == 1
    refusal = _event(conn, view.run_id, "RESEARCH_REQUEST_REFUSED")
    payload = json.loads(refusal["payload_json"])
    assert payload["action"] == "continue"
    assert payload["code"] == "invalid_transition"


def test_a_pause_requested_run_is_not_eligible_for_a_bare_continue(conn):
    view = _create(conn)
    _set_status(conn, view.public_id, "PAUSE_REQUESTED")

    assert eligible_runs(conn) == []

    result = execute(conn, "continue")

    assert result.ok is True
    assert result.run is None
    assert get_run(conn, view.public_id).status == "PAUSE_REQUESTED"


def test_the_status_text_says_a_cancel_awaits_the_worker(conn):
    view = _create(conn)
    _set_status(conn, view.public_id, "CANCEL_REQUESTED")

    result = execute(conn, "status", research_id=view.public_id)

    assert result.ok is True
    assert "cancel requested" in result.text
    assert "worker" in result.text


def test_eligible_runs_excludes_terminal_failed_and_leased_runs(conn):
    eligible = _create(conn, "Eligible topic")
    terminal = _create(conn, "Terminal topic")
    failed = _create(conn, "Failed topic")
    leased = _create(conn, "Leased topic")
    _set_status(conn, terminal.public_id, "CANCELLED")
    _set_status(conn, failed.public_id, "FAILED_PERMANENT")
    _lease(conn, leased.run_id)

    views = eligible_runs(conn)

    assert [view.public_id for view in views] == [eligible.public_id]


# --- pause, resume, retry, cancel -----------------------------------------


def test_pause_without_a_worker_stops_the_run(conn):
    view = _create(conn)

    result = execute(conn, "pause", research_id=view.public_id)

    assert result.run is not None
    assert result.run.status == "PAUSED"
    assert "RESEARCH_PAUSED" in _event_types(conn, view.run_id)


def test_pause_with_a_live_lease_only_records_the_request(conn):
    view = _create(conn)
    _lease(conn, view.run_id)

    result = execute(conn, "pause", research_id=view.public_id)

    assert result.run is not None
    assert result.run.status == "PAUSE_REQUESTED"
    assert "RESEARCH_PAUSE_REQUESTED" in _event_types(conn, view.run_id)


def test_resume_from_paused_queues_the_run_again(conn):
    view = _create(conn)
    execute(conn, "pause", research_id=view.public_id)

    result = execute(conn, "resume", research_id=view.public_id)

    assert result.run is not None
    assert result.run.status == "QUEUED"
    assert result.run.resume_count == 1
    assert "RESEARCH_RESUMED" in _event_types(conn, view.run_id)


def test_resume_from_a_run_that_is_not_paused_is_refused(conn):
    view = _create(conn)

    with pytest.raises(InvalidTransition):
        resume_run(conn, view.public_id)

    result = execute(conn, "resume", research_id=view.public_id)
    assert result.ok is False
    assert result.code == "invalid_transition"


def test_resume_from_a_pause_requested_run_queues_the_run_again(conn):
    view = _create(conn)
    _set_status(conn, view.public_id, "PAUSE_REQUESTED")

    result = execute(conn, "resume", research_id=view.public_id)

    assert result.ok is True
    assert result.run is not None
    assert result.run.status == "QUEUED"
    assert result.run.resume_count == 1
    assert "RESEARCH_RESUMED" in _event_types(conn, view.run_id)


def test_resume_on_a_run_with_a_live_lease_is_refused(conn):
    view = _create(conn)
    job = list_jobs(conn)[0]
    _set_status(conn, view.public_id, "PAUSE_REQUESTED")
    live = (utcnow() + timedelta(seconds=60)).isoformat()
    conn.execute(
        "UPDATE jobs SET status = 'leased', worker_id = 'worker-a', "
        "lease_epoch = 1, lease_expires_at = ? WHERE id = ?",
        (live, job.id),
    )

    with pytest.raises(InvalidTransition):
        resume_run(conn, view.public_id)

    result = execute(conn, "resume", research_id=view.public_id)
    assert result.ok is False
    assert result.code == "invalid_transition"
    # The refused transaction rolled back: the run stands as it was.
    assert get_run(conn, view.public_id).status == "PAUSE_REQUESTED"
    assert get_run(conn, view.public_id).resume_count == 0
    # One job, still leased: no second job was queued behind the lease.
    jobs = list_jobs(conn)
    assert len(jobs) == 1
    assert jobs[0].id == job.id
    assert jobs[0].status == "leased"
    assert get_run(conn, view.public_id).job_count == 1
    refusal = _event(conn, view.run_id, "RESEARCH_REQUEST_REFUSED")
    payload = json.loads(refusal["payload_json"])
    assert payload["action"] == "resume"
    assert payload["code"] == "invalid_transition"


def test_resume_requeues_the_job_whose_lease_expired_instead_of_adding_one(conn):
    view = _create(conn)
    job = list_jobs(conn)[0]
    _set_status(conn, view.public_id, "PAUSE_REQUESTED")
    expired = (utcnow() - timedelta(seconds=60)).isoformat()
    conn.execute(
        "UPDATE jobs SET status = 'leased', worker_id = 'worker-a', "
        "lease_epoch = 1, lease_expires_at = ? WHERE id = ?",
        (expired, job.id),
    )

    result = execute(conn, "resume", research_id=view.public_id)

    assert result.ok is True
    assert result.run is not None
    assert result.run.resume_count == 1
    jobs = list_jobs(conn)
    # One job, the same one: the expired lease is pending work of this run.
    assert len(jobs) == 1
    assert jobs[0].id == job.id
    assert jobs[0].status == "queued"
    assert jobs[0].lease_epoch == 2
    assert jobs[0].worker_id is None
    assert jobs[0].lease_expires_at is None
    assert get_run(conn, view.public_id).job_count == 1
    assert "RESEARCH_RESUMED" in _event_types(conn, view.run_id)


def test_retry_requeues_the_failed_job_and_raises_the_lease_epoch(conn):
    view = _create(conn)
    job = list_jobs(conn)[0]
    conn.execute(
        "UPDATE jobs SET status = 'failed', attempts = 2, worker_id = 'worker-a', "
        "lease_epoch = 3, lease_expires_at = ? WHERE id = ?",
        (utcnow().isoformat(), job.id),
    )
    _set_status(conn, view.public_id, "FAILED_RETRYABLE")

    result = execute(conn, "retry", research_id=view.public_id)

    assert result.run is not None
    assert result.run.status == "QUEUED"
    requeued = get_job(conn, job.id)
    assert requeued.status == "queued"
    assert requeued.attempts == 0
    assert requeued.lease_epoch == 4
    assert requeued.worker_id is None
    assert requeued.lease_expires_at is None
    assert "RESEARCH_RETRIED" in _event_types(conn, view.run_id)


def test_retry_requeues_a_dead_job(conn):
    view = _create(conn)
    job = list_jobs(conn)[0]
    conn.execute(
        "UPDATE jobs SET status = 'dead', attempts = 3, worker_id = 'worker-a', "
        "lease_epoch = 2, lease_expires_at = ? WHERE id = ?",
        (utcnow().isoformat(), job.id),
    )
    _set_status(conn, view.public_id, "FAILED_RETRYABLE")

    result = execute(conn, "retry", research_id=view.public_id)

    assert result.ok is True
    assert result.run is not None
    assert result.run.status == "QUEUED"
    requeued = get_job(conn, job.id)
    assert requeued.status == "queued"
    assert requeued.lease_epoch == 3
    assert requeued.worker_id is None
    assert requeued.lease_expires_at is None


def test_cancel_cancels_the_run_and_its_queued_jobs_and_keeps_the_history(conn):
    view = _create(conn)

    result = execute(conn, "cancel", research_id=view.public_id)

    assert result.run is not None
    assert result.run.status == "CANCELLED"
    assert [job.status for job in list_jobs(conn)] == ["cancelled"]
    assert _event_types(conn, view.run_id)
    assert list_checkpoints(conn, run_id=view.run_id)


def test_cancel_with_a_live_lease_only_records_the_request(conn):
    view = _create(conn)
    _lease(conn, view.run_id)

    result = execute(conn, "cancel", research_id=view.public_id)

    assert result.run is not None
    assert result.run.status == "CANCEL_REQUESTED"
    assert "RESEARCH_CANCEL_REQUESTED" in _event_types(conn, view.run_id)


# --- log, validation, formatting ------------------------------------------


def test_log_reports_the_events_the_checkpoints_and_the_jobs(conn):
    view = _create(conn)

    payload = run_log(conn, view.public_id)

    assert payload["run"].public_id == view.public_id
    assert [event["event_type"] for event in payload["events"]] == ["RESEARCH_STARTED"]
    assert [checkpoint["stage"] for checkpoint in payload["checkpoints"]] == ["PLAN"]
    assert [job["idempotency_key"] for job in payload["jobs"]] == [
        f"research_start:{view.run_id}"
    ]
    assert payload["attempts"] == []


def test_the_log_holds_the_jobs_of_that_run_only_and_bounds_them(conn):
    first = _create(conn, "First topic")
    _create(conn, "Second topic")
    for index in range(3):
        enqueue_job(
            conn,
            stage="DISCOVERY",
            idempotency_key=f"extra:{first.run_id}:{index}",
            run_id=first.run_id,
        )

    payload = run_log(conn, first.public_id, limit=2)

    assert len(payload["jobs"]) == 2
    assert {job["run_id"] for job in payload["jobs"]} == {first.run_id}


@pytest.mark.parametrize("bad_limit", [0, 501])
def test_the_list_limit_outside_the_bounds_is_refused(conn, bad_limit):
    _create(conn)

    result = execute(conn, "list", limit=bad_limit)

    assert result.ok is False
    assert result.code == "invalid_request"
    assert str(bad_limit) in result.message


@pytest.mark.parametrize("research_id", [None, "RH-20261021-0001"])
def test_an_unknown_action_names_the_action_and_not_the_id(conn, research_id):
    result = execute(conn, "bogus", research_id=research_id)

    assert result.ok is False
    assert result.code == "invalid_request"
    assert "bogus" in result.message
    assert "research_id" not in result.message


@pytest.mark.parametrize("bad_id", ["", "RH 2026", "a/b", "x" * 200])
def test_an_id_with_a_slash_or_a_space_is_refused(conn, bad_id):
    result = execute(conn, "status", research_id=bad_id)

    assert result.ok is False
    assert result.code == "invalid_request"
    assert bad_id[:20] in result.message


def test_the_run_block_names_the_state_the_progress_and_the_worker(conn):
    view = _create(conn)

    text = render_run(view)

    assert view.public_id in text
    assert "state: QUEUED" in text
    assert "progress: 0%" in text
    assert "worker: not running" in text

    _lease(conn, view.run_id)
    assert "worker: running" in render_run(get_run(conn, view.public_id))


def test_the_list_block_names_every_run(conn):
    view = _create(conn)

    text = render_runs(list_runs(conn))

    assert view.public_id in text
    assert "QUEUED" in text


# --- the requester and the refusal trace -----------------------------------


def test_a_transition_event_names_the_requester_and_the_stage(conn):
    view = _create(conn, "Requester topic", chat_id="session-1")

    started = _event(conn, view.run_id, "RESEARCH_STARTED")
    assert started["agent"] == "session-1"
    assert started["stage"] == "PLAN"

    execute(conn, "pause", research_id=view.public_id, chat_id="session-2")

    paused = _event(conn, view.run_id, "RESEARCH_PAUSED")
    assert paused["agent"] == "session-2"
    assert paused["stage"] == "PLAN"


def test_a_refused_command_leaves_a_durable_refusal_event(conn):
    view = _create(conn)
    _set_status(conn, view.public_id, "COMPLETED")

    result = execute(conn, "continue", research_id=view.public_id, chat_id="session-9")

    assert result.ok is False
    assert result.code == "invalid_transition"
    refusal = _event(conn, view.run_id, "RESEARCH_REQUEST_REFUSED")
    assert refusal["agent"] == "session-9"
    assert refusal["created_at"]
    payload = json.loads(refusal["payload_json"])
    assert payload["action"] == "continue"
    assert payload["reason"] == result.message
    # The refused transaction rolled back: the run is as it was.
    assert get_run(conn, view.public_id).status == "COMPLETED"


def test_a_refusal_against_an_unknown_id_writes_no_event(conn):
    result = execute(conn, "status", research_id="RH-20261021-9999", chat_id="session-9")

    assert result.ok is False
    assert result.code == "unknown_research"
    assert conn.execute("SELECT count(*) FROM research_events").fetchone()[0] == 0


# --- the command line entry point -----------------------------------------


def test_main_prints_one_json_object_and_returns_zero(tmp_path, capsys):
    path = tmp_path / "registry.db"
    connection = connect(path)
    migrate(connection)
    try:
        create_run(connection, CreateRequest(objective="Track the billing pain"))
    finally:
        connection.close()

    code = main(["research", "list"], db_path=path)

    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload["ok"] is True
    assert payload["action"] == "list"
    assert payload["runs"][0]["public_id"].startswith("RH-")


def test_main_reports_a_missing_database_without_creating_one(tmp_path, capsys):
    path = tmp_path / "absent.db"

    code = main(["list"], db_path=path)

    payload = json.loads(capsys.readouterr().out)
    assert code == 1
    assert payload["ok"] is False
    assert payload["code"] == "database_missing"
    assert not path.exists()


def test_main_reports_a_missing_database_without_leaking_the_path(tmp_path, capsys):
    path = tmp_path / "absent.db"

    code = main(["list"], db_path=path)

    payload = json.loads(capsys.readouterr().out)
    assert code == 1
    assert payload["code"] == "database_missing"
    assert str(path) not in payload["message"]
    assert str(path) not in payload["text"]
    assert "ph db init" in payload["message"]


def test_main_returns_one_for_a_failed_action(tmp_path, capsys):
    path = tmp_path / "registry.db"
    connection = connect(path)
    migrate(connection)
    connection.close()

    code = main(["status", "RH-20261021-9999"], db_path=path)

    payload = json.loads(capsys.readouterr().out)
    assert code == 1
    assert payload["ok"] is False
    assert payload["code"] == "unknown_research"

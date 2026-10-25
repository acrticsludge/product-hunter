"""The state layer: run states, transitions, formatting, and the dispatcher.

Self contained on purpose. ``tests/conftest.py`` belongs to another change in
this tree, so this file builds its own database and its own fixtures.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from datetime import timedelta

import pytest

import product_hunter.registry as registry
from product_hunter.db import (
    connect,
    claim_job,
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
    doctor,
    eligible_runs,
    execute,
    get_run,
    list_runs,
    main,
    pause_run,
    recover,
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


def test_the_create_text_of_a_reused_run_says_it_was_reused(conn):
    first = _create(conn, "Compare CRM tools")

    result = execute(conn, "create", objective="compare crm tools")

    assert result.ok is True
    assert result.run is not None
    assert result.run.public_id == first.public_id
    # The model reads the text, so the text opens with what happened.
    first_line = result.text.splitlines()[0]
    assert first.public_id in first_line
    assert "already holds this objective" in first_line
    assert "reused" in first_line
    assert "created" not in first_line
    # The run block still follows the first line.
    assert render_run(result.run) in result.text


def test_the_create_text_of_a_new_run_says_it_was_created(conn):
    result = execute(conn, "create", objective="Track the billing pain")

    assert result.ok is True
    assert result.run is not None
    first_line = result.text.splitlines()[0]
    assert "created" in first_line
    assert result.run.public_id in first_line
    # The run block still follows the first line.
    assert render_run(result.run) in result.text


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


def test_the_list_orders_a_tie_by_the_public_id(conn):
    first = _create(conn, "First topic")
    second = _create(conn, "Second topic")
    moment = "2026-10-21T12:00:00+00:00"
    _set_status(conn, first.public_id, "QUEUED", updated_at=moment)
    _set_status(conn, second.public_id, "QUEUED", updated_at=moment)
    # The id of the first run now sorts after the id of the second one, so
    # the tie is broken by the id and not by the order the rows came in.
    renamed = second.public_id[:-4] + "9999"
    conn.execute(
        "UPDATE research_runs SET public_id = ? WHERE run_id = ?",
        (renamed, first.run_id),
    )

    views = list_runs(conn)

    assert [view.public_id for view in views] == [second.public_id, renamed]


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


def test_bare_continue_with_two_eligible_runs_tells_the_model_to_ask(conn):
    first = _create(conn, "First topic")
    second = _create(conn, "Second topic")

    result = execute(conn, "continue")

    assert result.ok is True
    assert result.needs_selection is True
    # The model reads the text, so the text has to say what to do.
    assert "ask the user" in result.text
    assert "never pick one on the user's behalf" in result.text.lower()
    # The candidates stay in the text, so the model can offer them.
    assert first.public_id in result.text
    assert second.public_id in result.text
    # The instruction comes before the list.
    assert result.text.index("ask the user") < result.text.index(first.public_id)


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


def test_a_named_continue_on_a_pause_requested_run_is_refused(conn):
    view = _create(conn)
    _set_status(conn, view.public_id, "PAUSE_REQUESTED")

    result = execute(conn, "continue", research_id=view.public_id)

    assert result.ok is False
    assert result.code == "invalid_transition"
    assert "PAUSE_REQUESTED" in result.message
    assert "worker" in result.message
    # The pause request still stands: the run was not queued again.
    assert get_run(conn, view.public_id).status == "PAUSE_REQUESTED"
    assert get_run(conn, view.public_id).resume_count == 0
    refusal = _event(conn, view.run_id, "RESEARCH_REQUEST_REFUSED")
    payload = json.loads(refusal["payload_json"])
    assert payload["action"] == "continue"
    assert payload["code"] == "invalid_transition"


def test_a_named_continue_on_a_malformed_state_is_refused(conn):
    view = _create(conn)
    _set_status(conn, view.public_id, "GARBAGE")

    result = execute(conn, "continue", research_id=view.public_id)

    assert result.ok is False
    assert result.code == "invalid_transition"
    assert "GARBAGE" in result.message
    assert "repair" in result.message
    # The corrupt row was not handed to a worker as work.
    assert get_run(conn, view.public_id).status == "GARBAGE"
    assert get_run(conn, view.public_id).resume_count == 0


def test_a_resume_on_a_malformed_state_is_refused(conn):
    view = _create(conn)
    _set_status(conn, view.public_id, "GARBAGE")

    result = execute(conn, "resume", research_id=view.public_id)

    assert result.ok is False
    assert result.code == "invalid_transition"
    assert "GARBAGE" in result.message
    assert "repair" in result.message
    assert get_run(conn, view.public_id).status == "GARBAGE"


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


def test_a_pause_on_a_cancel_requested_run_is_refused(conn):
    view = _create(conn)
    _set_status(conn, view.public_id, "CANCEL_REQUESTED")

    result = execute(conn, "pause", research_id=view.public_id)

    assert result.ok is False
    assert result.code == "invalid_transition"
    assert "CANCEL_REQUESTED" in result.message
    # The cancel survives: the pause did not overwrite it.
    assert get_run(conn, view.public_id).status == "CANCEL_REQUESTED"
    refusal = _event(conn, view.run_id, "RESEARCH_REQUEST_REFUSED")
    payload = json.loads(refusal["payload_json"])
    assert payload["action"] == "pause"


def test_a_pause_without_a_worker_cancels_the_queued_job(conn):
    view = _create(conn)
    job = list_jobs(conn)[0]

    result = execute(conn, "pause", research_id=view.public_id)

    assert result.run is not None
    assert result.run.status == "PAUSED"
    # The pending work is stopped, so a worker does not pick the run up.
    assert get_job(conn, job.id).status == "cancelled"


def test_a_pause_with_a_live_lease_leaves_the_job_alone(conn):
    view = _create(conn)
    job = list_jobs(conn)[0]
    _lease(conn, view.run_id)

    execute(conn, "pause", research_id=view.public_id)

    # The worker is on the job: it finishes the step it is on.
    assert get_job(conn, job.id).status == "queued"


def test_a_resume_of_a_paused_run_queues_a_fresh_job(conn):
    view = _create(conn)
    execute(conn, "pause", research_id=view.public_id)

    result = execute(conn, "resume", research_id=view.public_id)

    assert result.ok is True
    assert result.run is not None
    assert result.run.status == "QUEUED"
    jobs = list_jobs(conn)
    # The cancelled job of the pause stays, and the resume queues a new one.
    assert len(jobs) == 2
    assert sorted(job.status for job in jobs) == ["cancelled", "queued"]
    assert "RESEARCH_RESUMED" in _event_types(conn, view.run_id)


def test_a_resume_on_a_cancel_requested_run_is_refused(conn):
    view = _create(conn)
    _set_status(conn, view.public_id, "CANCEL_REQUESTED")

    result = execute(conn, "resume", research_id=view.public_id)

    assert result.ok is False
    assert result.code == "invalid_transition"
    assert "CANCEL_REQUESTED" in result.message
    # The cancel survives: the resume did not queue the run again.
    assert get_run(conn, view.public_id).status == "CANCEL_REQUESTED"
    assert get_run(conn, view.public_id).resume_count == 0


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


def test_retry_clears_stage_attempts_so_reclaim_does_not_conflict(conn):
    """A retried job must clear its old stage_attempts rows so the next claim
    can insert a fresh attempt row without a UNIQUE(job_id, attempt)
    conflict."""
    view = _create(conn)
    job = list_jobs(conn)[0]
    conn.execute(
        "UPDATE jobs SET status = 'dead', attempts = 3, worker_id = 'worker-a', "
        "lease_epoch = 2, lease_expires_at = ? WHERE id = ?",
        (utcnow().isoformat(), job.id),
    )
    # Old attempt rows from the previous run.
    for attempt in (1, 2, 3):
        conn.execute(
            """
            INSERT INTO stage_attempts (
                attempt_id, job_id, run_id, stage, attempt, worker_id,
                lease_epoch, status, started_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "att_" + hashlib.sha256(f"{job.id}|{attempt}".encode()).hexdigest()[:16],
                job.id,
                view.run_id,
                job.stage,
                attempt,
                "worker-a",
                2,
                "running",
                utcnow().isoformat(),
            ),
        )
    conn.commit()
    _set_status(conn, view.public_id, "FAILED_RETRYABLE")

    result = execute(conn, "retry", research_id=view.public_id)
    assert result.ok is True

    # Old stage_attempts rows are cleared.
    remaining = conn.execute(
        "SELECT count(*) FROM stage_attempts WHERE job_id = ?", (job.id,)
    ).fetchone()[0]
    assert remaining == 0, f"expected 0 stage_attempts rows, got {remaining}"

    # The job can be re-claimed and a new attempt row inserted without a
    # UNIQUE(job_id, attempt) conflict.
    claimed = claim_job(
        conn,
        "worker-b",
        lease_seconds=60,
        now=utcnow(),
    )
    assert claimed is not None
    assert claimed.id == job.id


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


def test_a_secret_in_the_refused_value_never_reaches_the_message(conn):
    # The offending value is shown, except where it names a secret.
    result = execute(conn, "status", research_id="sk-abc123/")

    assert result.ok is False
    assert result.code == "invalid_request"
    assert "sk-abc123" not in result.message
    assert "[redacted]" in result.message


def test_a_secret_in_the_internal_error_never_reaches_the_message(conn, monkeypatch):
    def broken(_conn, _research_id):
        raise RuntimeError("the store fell over with api_key=sk-internal-secret")

    monkeypatch.setattr(registry, "get_run", broken)

    result = execute(conn, "status", research_id="RH-20261021-0001")

    assert result.ok is False
    assert result.code == "internal_error"
    assert "sk-internal-secret" not in result.message
    assert "[redacted]" in result.message


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


# --- the enriched run ------------------------------------------------------


def test_a_corrupt_checkpoint_does_not_break_the_status_command(conn):
    view = _create(conn)
    conn.execute(
        "INSERT INTO research_checkpoints (checkpoint_id, run_id, stage, step, "
        "checkpoint_version, state_json, created_at) "
        "VALUES ('cp_broken', ?, 'PLAN', 'broken', 99, 'not json', ?)",
        (view.run_id, utcnow().isoformat()),
    )

    result = execute(conn, "status", research_id=view.public_id)

    assert result.ok is True
    assert result.run is not None
    # The good checkpoint is still the newest one the interface can read.
    assert result.run.latest_checkpoint.startswith("PLAN/research_requested v1")


def test_a_corrupt_checkpoint_does_not_break_the_log_command(conn):
    view = _create(conn)
    conn.execute(
        "INSERT INTO research_checkpoints (checkpoint_id, run_id, stage, step, "
        "checkpoint_version, state_json, created_at) "
        "VALUES ('cp_broken', ?, 'PLAN', 'broken', 99, 'not json', ?)",
        (view.run_id, utcnow().isoformat()),
    )

    result = execute(conn, "log", research_id=view.public_id)

    assert result.ok is True
    assert result.run is not None
    payload = run_log(conn, view.public_id)
    assert [checkpoint["stage"] for checkpoint in payload["checkpoints"]] == ["PLAN"]


def test_the_status_block_names_the_budget_the_checkpoint_and_the_activity(conn):
    view = _create(conn)

    result = execute(conn, "status", research_id=view.public_id)

    assert result.ok is True
    assert "budget:" in result.text
    assert "tool_calls_total" in result.text
    assert "checkpoint: PLAN/research_requested v1" in result.text
    assert "last activity:" in result.text
    assert result.run is not None
    assert result.run.budget
    assert result.run.latest_checkpoint.startswith("PLAN/research_requested v1")
    # The run never sent a heartbeat, so the last activity is the update.
    assert result.run.last_activity == result.run.updated_at


def test_the_status_block_names_the_blockers_and_the_errors(conn):
    view = _create(conn)
    conn.execute(
        "UPDATE research_runs SET last_error = ? WHERE run_id = ?",
        ("boom token=sk-run-secret", view.run_id),
    )
    conn.execute(
        "UPDATE jobs SET status = 'failed', last_error = ? WHERE run_id = ?",
        ("boom api_key=sk-job-secret", view.run_id),
    )

    result = execute(conn, "status", research_id=view.public_id)

    assert "blockers:" in result.text
    assert "errors:" in result.text
    assert result.run is not None
    assert "boom token=[redacted]" in result.run.errors
    assert "boom api_key=[redacted]" in result.run.errors


def test_a_secret_in_the_run_error_never_reaches_the_status_text(conn):
    view = _create(conn)
    conn.execute(
        "UPDATE research_runs SET last_error = ? WHERE run_id = ?",
        ("boom token=sk-run-secret", view.run_id),
    )

    result = execute(conn, "status", research_id=view.public_id)

    assert "sk-run-secret" not in result.text
    assert "[redacted]" in result.text


def test_a_secret_in_a_job_error_never_reaches_the_status_text(conn):
    view = _create(conn)
    conn.execute(
        "UPDATE jobs SET status = 'failed', last_error = ? WHERE run_id = ?",
        ("boom Authorization: Bearer sk-job-secret", view.run_id),
    )

    result = execute(conn, "status", research_id=view.public_id)

    assert "sk-job-secret" not in result.text
    assert "[redacted]" in result.text


def test_the_run_error_in_the_view_is_masked(conn):
    view = _create(conn)
    conn.execute(
        "UPDATE research_runs SET last_error = ? WHERE run_id = ?",
        ("boom token=sk-run-secret", view.run_id),
    )

    result = execute(conn, "status", research_id=view.public_id)

    # The view is what the plugin hands to the model beside the text, so the
    # raw error must not be in it.
    assert result.run is not None
    assert result.run.last_error is not None
    assert "sk-run-secret" not in result.run.last_error
    assert "[redacted]" in result.run.last_error


def test_a_json_secret_in_the_run_error_is_masked(conn):
    view = _create(conn)
    conn.execute(
        "UPDATE research_runs SET last_error = ? WHERE run_id = ?",
        ('{"token": "abc"}', view.run_id),
    )

    result = execute(conn, "status", research_id=view.public_id)

    assert result.run is not None
    assert result.run.last_error is not None
    assert "abc" not in result.run.last_error
    assert "[redacted]" in result.run.last_error


def test_a_bare_credential_in_the_run_error_is_masked(conn):
    view = _create(conn)
    conn.execute(
        "UPDATE research_runs SET last_error = ? WHERE run_id = ?",
        ("the upstream refused sk-abc123", view.run_id),
    )

    result = execute(conn, "status", research_id=view.public_id)

    assert result.run is not None
    assert result.run.last_error is not None
    assert "sk-abc123" not in result.run.last_error
    assert "[redacted]" in result.run.last_error


def test_the_blockers_name_the_worker_that_holds_the_run(conn):
    view = _create(conn)
    _lease(conn, view.run_id)

    result = execute(conn, "status", research_id=view.public_id)

    assert "a worker holds the run" in result.text


def test_the_blockers_name_an_expired_lease_of_a_running_run(conn):
    view = _create(conn)
    _lease(conn, view.run_id, live=False)
    _set_status(conn, view.public_id, "RUNNING")

    result = execute(conn, "status", research_id=view.public_id)

    assert "the worker lease expired while the state still says RUNNING" in result.text


def test_the_blockers_name_a_run_whose_worker_is_gone(conn):
    view = _create(conn)

    result = execute(conn, "status", research_id=view.public_id)

    # The database can prove that no worker holds a lease on any run.
    assert "no worker holds a lease on any run" in result.text


def test_the_list_block_marks_the_runs_that_can_be_continued(conn):
    continuable = _create(conn, "Continuable topic")
    leased = _create(conn, "Leased topic")
    _lease(conn, leased.run_id)

    text = render_runs(list_runs(conn))

    lines = {line.split()[0]: line for line in text.splitlines()[1:]}
    assert "continuable" in lines[continuable.public_id]
    assert "continuable" not in lines[leased.public_id]


# --- the retry rule --------------------------------------------------------


def test_a_retry_of_a_permanently_failed_run_is_refused(conn):
    view = _create(conn)
    _set_status(conn, view.public_id, "FAILED_PERMANENT")

    result = execute(conn, "retry", research_id=view.public_id)

    assert result.ok is False
    assert result.code == "invalid_transition"
    assert "new research request" in result.message
    # The refusal rolled back: the run is as it was.
    assert get_run(conn, view.public_id).status == "FAILED_PERMANENT"
    refusal = _event(conn, view.run_id, "RESEARCH_REQUEST_REFUSED")
    payload = json.loads(refusal["payload_json"])
    assert payload["action"] == "retry"


def test_a_continue_of_a_permanently_failed_run_asks_for_a_new_request(conn):
    view = _create(conn)
    _set_status(conn, view.public_id, "FAILED_PERMANENT")

    result = execute(conn, "continue", research_id=view.public_id)

    assert result.ok is False
    assert result.code == "invalid_transition"
    assert "new research request" in result.message


def test_a_retry_of_a_retryable_failure_still_works(conn):
    view = _create(conn)
    job = list_jobs(conn)[0]
    conn.execute(
        "UPDATE jobs SET status = 'failed', attempts = 2 WHERE id = ?", (job.id,)
    )
    _set_status(conn, view.public_id, "FAILED_RETRYABLE")

    result = execute(conn, "retry", research_id=view.public_id)

    assert result.ok is True
    assert result.run is not None
    assert result.run.status == "QUEUED"
    assert get_job(conn, job.id).status == "queued"


def test_a_retry_on_a_run_with_a_live_lease_is_refused(conn):
    view = _create(conn)
    job = list_jobs(conn)[0]
    conn.execute(
        "UPDATE jobs SET status = 'leased', worker_id = 'worker-a', "
        "lease_epoch = 1, lease_expires_at = ? WHERE id = ?",
        ((utcnow() + timedelta(seconds=60)).isoformat(), job.id),
    )
    _set_status(conn, view.public_id, "BLOCKED_TOOL")

    result = execute(conn, "retry", research_id=view.public_id)

    assert result.ok is False
    assert result.code == "invalid_transition"
    assert "live worker lease" in result.message
    # The refusal rolled back: the worker still holds the run.
    assert get_run(conn, view.public_id).status == "BLOCKED_TOOL"
    assert get_job(conn, job.id).status == "leased"
    refusal = _event(conn, view.run_id, "RESEARCH_REQUEST_REFUSED")
    payload = json.loads(refusal["payload_json"])
    assert payload["action"] == "retry"


# --- the malformed state ---------------------------------------------------


def test_a_malformed_state_is_reported_and_is_not_eligible_to_continue(conn):
    view = _create(conn)
    _set_status(conn, view.public_id, "GARBAGE")

    result = execute(conn, "status", research_id=view.public_id)

    assert result.ok is True
    assert "state: GARBAGE" in result.text
    assert "the stored state is not one the interface knows" in result.text
    # A corrupt row is never handed to a worker as work.
    assert eligible_runs(conn) == []
    assert "continuable" not in render_runs(list_runs(conn))


def test_the_doctor_reports_a_malformed_state(conn):
    view = _create(conn)
    _set_status(conn, view.public_id, "GARBAGE")

    report = doctor(conn)

    assert report["malformed_states"] == [
        {"public_id": view.public_id, "status": "GARBAGE"}
    ]


# --- the doctor ------------------------------------------------------------


def test_the_doctor_reports_the_contract_version_and_the_runs(conn):
    _create(conn, "First topic")
    _create(conn, "Second topic")

    report = doctor(conn)

    assert report["contract_version"] == "phase4-v1"
    assert report["runs_by_state"] == {"QUEUED": 2}
    assert report["live_leases"] == 0
    assert report["expired_leases"] == 0
    assert report["queued_jobs"] == 2


def test_the_doctor_text_names_the_contract_and_the_runs(conn):
    _create(conn)

    result = execute(conn, "doctor")

    assert result.ok is True
    assert "contract version: phase4-v1" in result.text
    assert "runs by state: QUEUED 1" in result.text
    assert "outstanding requests: none" in result.text
    assert "malformed states: none" in result.text


def test_the_doctor_counts_the_live_and_the_expired_leases(conn):
    live = _create(conn, "Live topic")
    expired = _create(conn, "Expired topic")
    _lease(conn, live.run_id)
    _lease(conn, expired.run_id, live=False)

    report = doctor(conn)

    assert report["live_leases"] == 1
    assert report["expired_leases"] == 1


# --- the stale requests and the recover ------------------------------------


def test_the_doctor_reports_a_stale_pause_request(conn):
    view = _create(conn)
    _set_status(conn, view.public_id, "PAUSE_REQUESTED")

    report = doctor(conn)

    assert report["outstanding_requests"] == [
        {"public_id": view.public_id, "status": "PAUSE_REQUESTED"}
    ]


def test_a_cancel_request_whose_worker_is_gone_is_stale(conn):
    view = _create(conn)
    job_id = _lease(conn, view.run_id)
    execute(conn, "cancel", research_id=view.public_id)

    # The worker still holds the run, so the request is not stale.
    assert doctor(conn)["outstanding_requests"] == []

    conn.execute(
        "UPDATE jobs SET lease_expires_at = ? WHERE id = ?",
        ((utcnow() - timedelta(seconds=60)).isoformat(), job_id),
    )

    assert doctor(conn)["outstanding_requests"] == [
        {"public_id": view.public_id, "status": "CANCEL_REQUESTED"}
    ]


def test_the_recover_resolves_a_stale_pause_request(conn):
    view = _create(conn)
    _set_status(conn, view.public_id, "PAUSE_REQUESTED")

    result = execute(conn, "recover")

    assert result.ok is True
    assert "recovered 1 run(s)" in result.text
    assert get_run(conn, view.public_id).status == "PAUSED"
    event = _event(conn, view.run_id, "RESEARCH_RECOVERED")
    payload = json.loads(event["payload_json"])
    assert payload["from"] == "PAUSE_REQUESTED"
    assert payload["to"] == "PAUSED"


def test_the_recover_finds_the_run_the_doctor_reports(conn):
    view = _create(conn)
    # The stored state is not the one the interface writes, but it names the
    # same request.
    _set_status(conn, view.public_id, "pause_requested")

    report = doctor(conn)

    assert report["outstanding_requests"] == [
        {"public_id": view.public_id, "status": "PAUSE_REQUESTED"}
    ]

    result = execute(conn, "recover")

    assert result.ok is True
    assert "recovered 1 run(s)" in result.text
    assert get_run(conn, view.public_id).status == "PAUSED"


def test_the_recover_resolves_a_stale_cancel_request_and_cancels_its_jobs(conn):
    view = _create(conn)
    job_id = _lease(conn, view.run_id)
    execute(conn, "cancel", research_id=view.public_id)
    conn.execute(
        "UPDATE jobs SET lease_expires_at = ? WHERE id = ?",
        ((utcnow() - timedelta(seconds=60)).isoformat(), job_id),
    )

    result = execute(conn, "recover")

    assert result.ok is True
    assert get_run(conn, view.public_id).status == "CANCELLED"
    # The pending work of a cancelled run is cancelled with it.
    assert {job.status for job in list_jobs(conn)} == {"cancelled"}
    event = _event(conn, view.run_id, "RESEARCH_RECOVERED")
    payload = json.loads(event["payload_json"])
    assert payload["from"] == "CANCEL_REQUESTED"
    assert payload["to"] == "CANCELLED"


def test_the_recover_with_a_research_id_touches_only_that_run(conn):
    first = _create(conn, "First topic")
    second = _create(conn, "Second topic")
    _set_status(conn, first.public_id, "PAUSE_REQUESTED")
    _set_status(conn, second.public_id, "PAUSE_REQUESTED")

    result = execute(conn, "recover", research_id=first.public_id)

    assert result.ok is True
    assert get_run(conn, first.public_id).status == "PAUSED"
    assert get_run(conn, second.public_id).status == "PAUSE_REQUESTED"


def test_the_recover_leaves_a_request_the_worker_still_holds(conn):
    view = _create(conn)
    _lease(conn, view.run_id)
    execute(conn, "pause", research_id=view.public_id)

    result = execute(conn, "recover")

    assert result.ok is True
    assert "recovered 0 run(s)" in result.text
    assert get_run(conn, view.public_id).status == "PAUSE_REQUESTED"


def test_the_recover_requeues_the_job_whose_lease_expired(conn):
    view = _create(conn)
    job = list_jobs(conn)[0]
    conn.execute(
        "UPDATE jobs SET status = 'leased', worker_id = 'worker-a', "
        "lease_epoch = 1, lease_expires_at = ? WHERE id = ?",
        ((utcnow() - timedelta(seconds=60)).isoformat(), job.id),
    )

    result = execute(conn, "recover")

    assert "requeued 1 job(s)" in result.text
    assert get_job(conn, job.id).status == "queued"


def test_the_recover_of_an_unknown_id_is_unknown_research(conn):
    result = execute(conn, "recover", research_id="RH-20261021-9999")

    assert result.ok is False
    assert result.code == "unknown_research"


def test_the_doctor_writes_nothing(conn):
    view = _create(conn)
    before = conn.execute("SELECT count(*) FROM research_events").fetchone()[0]

    execute(conn, "doctor")

    after = conn.execute("SELECT count(*) FROM research_events").fetchone()[0]
    assert after == before
    assert get_run(conn, view.public_id).status == "QUEUED"


# --- the write path --------------------------------------------------------


def test_a_write_joins_the_transaction_that_is_already_open(conn):
    view = _create(conn)
    conn.execute("BEGIN IMMEDIATE")
    try:
        result = execute(conn, "continue", research_id=view.public_id)
        assert result.ok is True
        assert result.run is not None
    finally:
        conn.execute("ROLLBACK")
    # The command did not commit the transaction it joined, so the rollback
    # undid the whole write.
    assert get_run(conn, view.public_id).resume_count == 0


def test_a_write_against_a_locked_file_waits_instead_of_failing(tmp_path):
    path = tmp_path / "registry.db"
    setup = connect(path)
    migrate(setup)
    view = _create(setup, "First topic")
    setup.close()

    locked = threading.Event()

    def hold_the_file():
        writer = connect(path)
        try:
            # The write has to fail fast, so that the retry of the registry
            # is what waits the other writer out.
            writer.execute("PRAGMA busy_timeout = 0")
            writer.execute("BEGIN IMMEDIATE")
            writer.execute(
                "UPDATE jobs SET scheduled_for = scheduled_for WHERE run_id = ?",
                (view.run_id,),
            )
            locked.set()
            time.sleep(0.15)
            writer.execute("COMMIT")
        finally:
            writer.close()

    holder = threading.Thread(target=hold_the_file)
    holder.start()
    assert locked.wait(30)
    conn = connect(path)
    try:
        conn.execute("PRAGMA busy_timeout = 0")
        result = execute(conn, "continue", research_id=view.public_id)
    finally:
        holder.join()
        conn.close()

    assert result.ok is True
    assert result.run is not None
    assert result.run.status == "QUEUED"
    assert result.run.resume_count == 1


# --- two commands at once --------------------------------------------------


def test_two_commands_at_once_leave_the_database_consistent(tmp_path):
    path = tmp_path / "registry.db"
    setup = connect(path)
    migrate(setup)
    view = _create(setup, "First topic")
    setup.close()

    results: dict[str, object] = {}

    def create():
        # A connection belongs to the thread that opened it, so each command
        # opens its own on the same file.
        connection = connect(path)
        try:
            results["create"] = execute(
                connection, "create", objective="Second topic"
            )
        finally:
            connection.close()

    def continue_the_run():
        connection = connect(path)
        try:
            results["continue"] = execute(
                connection, "continue", research_id=view.public_id
            )
        finally:
            connection.close()

    threads = [
        threading.Thread(target=create),
        threading.Thread(target=continue_the_run),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert results["create"].ok is True
    assert results["continue"].ok is True

    check = connect(path)
    try:
        assert len(list_runs(check)) == 2
        continued = get_run(check, view.public_id)
        assert continued.status == "QUEUED"
        assert continued.resume_count == 1
        # The pending job was reused, so the run holds one job and not two.
        assert continued.job_count == 1
    finally:
        check.close()

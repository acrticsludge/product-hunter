from __future__ import annotations

import hashlib
import logging
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from product_hunter.db import Store
from product_hunter.db import scheduler as scheduler_module
from product_hunter.db import worker as worker_module
from product_hunter.db.jobs import (
    claim_job,
    complete_job,
    enqueue_job,
    fail_job,
    get_job,
)
from product_hunter.db.scheduler import (
    BlockedSchedule,
    ConflictedSchedule,
    DroppedSchedule,
    Priority,
    Schedule,
    ScheduleError,
    ScheduleTick,
    Scheduler,
    default_schedules,
)
from product_hunter.db.worker import Worker
from product_hunter.fetch import utcnow

NOW = datetime(2026, 10, 21, 6, 0, tzinfo=timezone.utc)
# The daily cadences fire at 02:00 and 07:00 UTC. A noon tick catches both of
# them on the same day, inside one 24 hour catch-up window.
CADENCE_NOW = datetime(2026, 10, 21, 12, 0, tzinfo=timezone.utc)
# A daily grid is anchored at the epoch, so it fires at 00:00 UTC. The offset
# moves it to 06:00, which puts NOW itself on the grid.
DAILY = Schedule(
    name="daily", stage="COLLECT", interval_seconds=86400, offset_seconds=21600
)


def _store() -> Store:
    return Store.open(":memory:")


def _source(store: Store, source_id: str, terms_status: str, enabled: int) -> None:
    store.conn.execute(
        "INSERT INTO sources (source_id, source_type, terms_status, enabled) "
        "VALUES (?, ?, ?, ?)",
        (source_id, "forum", terms_status, enabled),
    )


def _job_id(key: str) -> str:
    """The id the queue derives from an idempotency key."""
    return "job_" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def _warnings(
    caplog: pytest.LogCaptureFixture, logger_name: str
) -> list[logging.LogRecord]:
    """The WARNING records one module logged, and nothing else."""
    return [
        record
        for record in caplog.records
        if record.name == logger_name and record.levelno == logging.WARNING
    ]


def test_priority_bands_order_the_queue_and_claim_agrees() -> None:
    store = _store()
    try:
        scheduler = Scheduler(store.conn, [])
        for band in Priority:
            enqueue_job(
                store.conn,
                stage="COLLECT",
                idempotency_key=f"band-{band.name}",
                payload={},
                priority=int(band),
                scheduled_for=NOW.isoformat(),
                now=NOW,
            )
        plan = scheduler.plan(now=NOW)
        assert [job.priority for job in plan] == [50, 40, 30, 20, 10]
        assert [job.priority for job in plan] == [
            int(Priority.P0),
            int(Priority.P1),
            int(Priority.P2),
            int(Priority.P3),
            int(Priority.P4),
        ]
        claimed = claim_job(store.conn, "worker-a", now=NOW)
        assert claimed is not None
        assert claimed.id == plan[0].id
    finally:
        store.close()


def test_overdue_jobs_are_reported_and_a_future_job_is_not() -> None:
    store = _store()
    try:
        scheduler = Scheduler(store.conn, [])
        past = enqueue_job(
            store.conn,
            stage="COLLECT",
            idempotency_key="past",
            payload={},
            scheduled_for=(NOW - timedelta(seconds=1)).isoformat(),
            now=NOW,
        )
        due = enqueue_job(
            store.conn,
            stage="COLLECT",
            idempotency_key="due",
            payload={},
            scheduled_for=NOW.isoformat(),
            now=NOW,
        )
        future = enqueue_job(
            store.conn,
            stage="COLLECT",
            idempotency_key="future",
            payload={},
            scheduled_for=(NOW + timedelta(seconds=60)).isoformat(),
            now=NOW,
        )
        assert [job.id for job in scheduler.overdue(now=NOW)] == [past.id]
        assert [job.id for job in scheduler.overdue(now=NOW, limit=1)] == [past.id]
        assert [job.id for job in scheduler.plan(now=NOW)] == [past.id, due.id]
        assert future.id not in [job.id for job in scheduler.plan(now=NOW)]
    finally:
        store.close()


def test_the_backoff_runs_30_60_120_and_stops_at_the_cap() -> None:
    store = _store()
    try:
        scheduler = Scheduler(store.conn, [])
        assert scheduler.backoff_seconds(1) == 30
        assert scheduler.backoff_seconds(2) == 60
        assert scheduler.backoff_seconds(3) == 120
        assert scheduler.backoff_seconds(4) == 240
        assert scheduler.backoff_seconds(8) == 3600
        assert scheduler.backoff_seconds(0) == 30
        assert scheduler.backoff_seconds(-5) == 30
    finally:
        store.close()


def test_a_failed_job_is_requeued_with_the_computed_delay() -> None:
    store = _store()
    try:
        scheduler = Scheduler(store.conn, [])
        delays: list[int] = []

        def backoff(job):
            delays.append(scheduler.backoff_seconds(job.attempts))
            return delays[-1]

        def boom(context):
            raise RuntimeError("boom")

        job = enqueue_job(
            store.conn,
            stage="COLLECT",
            idempotency_key="failing",
            payload={},
            scheduled_for="2020-01-01T00:00:00+00:00",
            now=NOW,
        )
        worker = Worker(store.conn, "worker-a", {"COLLECT": boom}, backoff=backoff)
        before = utcnow()
        outcome = worker.run_once()
        after = utcnow()
        assert outcome is not None
        assert outcome.status == "failed"
        assert delays == [30]
        failed = get_job(store.conn, job.id)
        assert failed is not None
        assert failed.status == "queued"
        assert failed.attempts == 1
        scheduled = datetime.fromisoformat(failed.scheduled_for)
        assert before + timedelta(seconds=30) <= scheduled
        assert scheduled <= after + timedelta(seconds=30)

        other = enqueue_job(
            store.conn,
            stage="BRIEF",
            idempotency_key="no-handler",
            payload={},
            scheduled_for="2020-01-01T00:00:00+00:00",
            now=NOW,
        )
        no_handler = Worker(store.conn, "worker-a", {}, backoff=backoff)
        before = utcnow()
        no_handler.run_once()
        after = utcnow()
        assert delays == [30, 30]
        missed = get_job(store.conn, other.id)
        assert missed is not None
        assert missed.status == "queued"
        scheduled = datetime.fromisoformat(missed.scheduled_for)
        assert before + timedelta(seconds=30) <= scheduled
        assert scheduled <= after + timedelta(seconds=30)
    finally:
        store.close()


def test_two_ticks_at_the_same_instant_create_one_job() -> None:
    store = _store()
    try:
        scheduler = Scheduler(store.conn, [DAILY])
        first = scheduler.tick(now=NOW)
        assert isinstance(first, ScheduleTick)
        assert first.now == NOW.isoformat()
        assert len(first.created) == 1
        assert first.existing == ()
        assert first.blocked == ()

        second = scheduler.tick(now=NOW)
        assert second.created == ()
        assert len(second.existing) == 1
        assert second.existing[0].id == first.created[0].id
        assert store.conn.execute("SELECT count(*) FROM jobs").fetchone()[0] == 1
    finally:
        store.close()


def test_a_second_scheduler_over_the_same_database_creates_nothing() -> None:
    store = _store()
    try:
        first = Scheduler(store.conn, [DAILY]).tick(now=NOW)
        assert len(first.created) == 1

        restarted = Scheduler(store.conn, [DAILY]).tick(now=NOW)
        assert restarted.created == ()
        assert len(restarted.existing) == 1
        assert store.conn.execute("SELECT count(*) FROM jobs").fetchone()[0] == 1
    finally:
        store.close()


def test_blocked_and_disabled_schedules_create_no_job() -> None:
    store = _store()
    try:
        _source(store, "src-off", "reviewed", 0)
        _source(store, "src-terms", "unknown", 1)
        schedules = [
            Schedule(
                name="missing",
                stage="COLLECT",
                interval_seconds=86400,
                offset_seconds=21600,
                source_id="nope",
            ),
            Schedule(
                name="off",
                stage="COLLECT",
                interval_seconds=86400,
                offset_seconds=21600,
                source_id="src-off",
            ),
            Schedule(
                name="terms",
                stage="COLLECT",
                interval_seconds=86400,
                offset_seconds=21600,
                source_id="src-terms",
            ),
            Schedule(
                name="paused",
                stage="COLLECT",
                interval_seconds=86400,
                offset_seconds=21600,
                enabled=False,
            ),
        ]
        tick = Scheduler(store.conn, schedules).tick(now=NOW)
        assert tick.created == ()
        assert tick.existing == ()
        assert tick.blocked == (
            BlockedSchedule("missing", "unknown_source", NOW.isoformat()),
            BlockedSchedule("off", "source_disabled", NOW.isoformat()),
            BlockedSchedule("terms", "terms_not_permitted", NOW.isoformat()),
        )
        assert store.conn.execute("SELECT count(*) FROM jobs").fetchone()[0] == 0
    finally:
        store.close()


def test_a_scheduled_job_carries_the_schedule_contents() -> None:
    store = _store()
    try:
        store.conn.execute(
            "INSERT INTO research_runs (run_id, public_id, objective, original_request) "
            "VALUES (?, ?, ?, ?)",
            ("run-1", "run-1", "find pains", "find pains"),
        )
        schedule = Schedule(
            name="collect",
            stage="COLLECT",
            interval_seconds=86400,
            offset_seconds=21600,
            priority=int(Priority.P3),
            payload={"source": "hn_algolia"},
            max_attempts=5,
            run_id="run-1",
        )
        tick = Scheduler(store.conn, [schedule]).tick(now=NOW)
        assert len(tick.created) == 1
        job = tick.created[0]
        assert job.stage == "COLLECT"
        assert job.priority == int(Priority.P3)
        assert job.max_attempts == 5
        assert job.run_id == "run-1"
        assert job.status == "queued"
        assert job.scheduled_for == NOW.isoformat()
        assert job.idempotency_key == f"schedule:collect:{NOW.isoformat()}"
        assert job.payload == {
            "source": "hn_algolia",
            "schedule": "collect",
            "occurrence": NOW.isoformat(),
        }

        with pytest.raises(ScheduleError):
            Scheduler(
                store.conn,
                [
                    Schedule(
                        name="ghost",
                        stage="COLLECT",
                        interval_seconds=86400,
                        offset_seconds=21600,
                        run_id="nope",
                    )
                ],
            ).tick(now=NOW)
    finally:
        store.close()


def test_a_scheduled_job_runs_from_queued_to_succeeded() -> None:
    store = _store()
    try:
        tick = Scheduler(store.conn, [DAILY]).tick(now=NOW)
        job = tick.created[0]
        assert job.status == "queued"

        claimed = claim_job(store.conn, "worker-a", lease_seconds=60, now=NOW)
        assert claimed is not None
        assert claimed.id == job.id
        assert claimed.status == "leased"
        assert claimed.attempts == 1
        assert claimed.worker_id == "worker-a"

        completed = complete_job(
            store.conn,
            claimed,
            worker_id="worker-a",
            lease_epoch=claimed.lease_epoch,
            result={"ok": True},
            now=NOW,
        )
        assert completed.status == "succeeded"
        assert completed.result == {"ok": True}
    finally:
        store.close()


def test_a_dead_job_can_be_retried_and_the_dead_row_stays() -> None:
    store = _store()
    try:
        schedule = Schedule(
            name="daily",
            stage="COLLECT",
            interval_seconds=86400,
            offset_seconds=21600,
            max_attempts=1,
        )
        scheduler = Scheduler(store.conn, [schedule])
        scheduler.tick(now=NOW).created[0]

        claimed = claim_job(store.conn, "worker-a", lease_seconds=60, now=NOW)
        assert claimed is not None
        dead = fail_job(
            store.conn,
            claimed,
            worker_id="worker-a",
            lease_epoch=claimed.lease_epoch,
            error="boom",
            backoff_seconds=0,
            now=NOW,
        )
        assert dead.status == "dead"

        retry = scheduler.schedule_retry(dead, now=NOW, reason="manual")
        assert retry.id != dead.id
        assert retry.status == "queued"
        assert retry.stage == "COLLECT"
        assert retry.priority == dead.priority
        assert retry.max_attempts == dead.max_attempts
        assert retry.idempotency_key == f"retry:{dead.id}:{dead.attempts + 1}"
        assert retry.scheduled_for == (
            NOW + timedelta(seconds=scheduler.backoff_seconds(dead.attempts + 1))
        ).isoformat()
        assert retry.payload["retry_of"] == dead.id
        assert retry.payload["retry_attempt"] == dead.attempts + 1
        # The reason describes the request, not the work, so it is not in the
        # payload the key identifies. It is logged instead.
        assert "reason" not in retry.payload

        again = scheduler.schedule_retry(dead, now=NOW, reason="manual")
        assert again.id == retry.id
        kept = get_job(store.conn, dead.id)
        assert kept is not None
        assert kept.status == "dead"
    finally:
        store.close()


def test_a_queued_job_can_be_promoted_and_the_plan_order_changes() -> None:
    store = _store()
    try:
        scheduler = Scheduler(store.conn, [])
        low = enqueue_job(
            store.conn,
            stage="COLLECT",
            idempotency_key="low",
            payload={},
            priority=10,
            scheduled_for=NOW.isoformat(),
            now=NOW,
        )
        high = enqueue_job(
            store.conn,
            stage="COLLECT",
            idempotency_key="high",
            payload={},
            priority=20,
            scheduled_for=(NOW - timedelta(seconds=10)).isoformat(),
            now=NOW,
        )
        assert [job.id for job in scheduler.plan(now=NOW)] == [high.id, low.id]

        promoted = scheduler.promote(low.id, priority=int(Priority.P0))
        assert promoted.priority == int(Priority.P0)
        assert [job.id for job in scheduler.plan(now=NOW)] == [low.id, high.id]

        with pytest.raises(ScheduleError):
            scheduler.promote("job_missing")
        claimed = claim_job(store.conn, "worker-a", now=NOW)
        assert claimed is not None
        with pytest.raises(ScheduleError):
            scheduler.promote(claimed.id)
    finally:
        store.close()


def test_the_cadence_definitions_land_on_the_grid() -> None:
    store = _store()
    try:
        by_stage = {schedule.stage: schedule for schedule in default_schedules()}
        assert by_stage["COLLECT"].interval_seconds == 900
        assert by_stage["PROCESS"].interval_seconds == 3600
        assert by_stage["CLUSTER"].interval_seconds == 21600
        assert by_stage["INVESTIGATE"].offset_seconds == 7200
        assert by_stage["BRIEF"].offset_seconds == 25200
        assert by_stage["INVESTIGATE"].priority == int(Priority.P1)
        assert by_stage["BRIEF"].priority == int(Priority.P2)

        def occurrences(stage: str) -> list[datetime]:
            # The default cadence caps its catch-up at two rounds. The grid
            # itself is checked without that cap.
            schedule = replace(by_stage[stage], catchup_limit=None)
            tick = Scheduler(store.conn, [schedule]).tick(now=CADENCE_NOW)
            return [
                datetime.fromisoformat(job.payload["occurrence"])
                for job in tick.created
            ]

        collect = occurrences("COLLECT")
        assert len(collect) == 96
        assert all(job.minute % 15 == 0 and job.second == 0 for job in collect)
        assert collect[-1] == CADENCE_NOW

        process = occurrences("PROCESS")
        assert len(process) == 24
        assert all(job.minute == 0 and job.second == 0 for job in process)

        cluster = occurrences("CLUSTER")
        assert len(cluster) == 4
        assert all(job.hour % 6 == 0 and job.minute == 0 for job in cluster)

        investigate = occurrences("INVESTIGATE")
        assert investigate == [datetime(2026, 10, 21, 2, 0, tzinfo=timezone.utc)]

        brief = occurrences("BRIEF")
        assert brief == [datetime(2026, 10, 21, 7, 0, tzinfo=timezone.utc)]
    finally:
        store.close()


def test_the_catch_up_window_bounds_the_backlog() -> None:
    store = _store()
    try:
        hourly = Schedule(name="hourly", stage="PROCESS", interval_seconds=3600)
        capped = Schedule(
            name="hourly-capped",
            stage="PROCESS",
            interval_seconds=3600,
            catchup_limit=3,
        )
        full = Scheduler(store.conn, [hourly]).tick(now=NOW)
        assert len(full.created) == 24
        assert full.created[0].payload["occurrence"] == "2026-10-20T07:00:00+00:00"
        assert full.created[-1].payload["occurrence"] == NOW.isoformat()

        limited = Scheduler(store.conn, [capped]).tick(now=NOW)
        assert [job.payload["occurrence"] for job in limited.created] == [
            "2026-10-21T04:00:00+00:00",
            "2026-10-21T05:00:00+00:00",
            "2026-10-21T06:00:00+00:00",
        ]
    finally:
        store.close()


def test_an_invalid_schedule_or_a_duplicate_name_is_refused() -> None:
    store = _store()
    try:
        with pytest.raises(ScheduleError):
            Schedule(name="  ", stage="COLLECT", interval_seconds=60)
        with pytest.raises(ScheduleError):
            Schedule(name="x", stage=" ", interval_seconds=60)
        with pytest.raises(ScheduleError):
            Schedule(name="x", stage="COLLECT", interval_seconds=0)
        with pytest.raises(ScheduleError):
            Schedule(name="x", stage="COLLECT", interval_seconds=60, offset_seconds=-1)
        with pytest.raises(ScheduleError):
            Schedule(name="x", stage="COLLECT", interval_seconds=60, max_attempts=0)
        with pytest.raises(ScheduleError):
            Schedule(
                name="x", stage="COLLECT", interval_seconds=60, catchup_limit=0
            )
        with pytest.raises(ScheduleError):
            Scheduler(store.conn, [DAILY, DAILY])
    finally:
        store.close()


def test_the_cap_bounds_one_tick_and_reports_what_it_dropped() -> None:
    store = _store()
    try:
        schedule = Schedule(name="fast", stage="COLLECT", interval_seconds=1)
        tick = Scheduler(store.conn, [schedule], max_occurrences=10).tick(now=NOW)
        assert len(tick.created) == 10
        # The cap keeps the newest occurrences.
        assert tick.created[0].payload["occurrence"] == (
            NOW - timedelta(seconds=9)
        ).isoformat()
        assert tick.created[-1].payload["occurrence"] == NOW.isoformat()
        # The window holds 86,400 grid points, so 86,390 of them were dropped.
        assert tick.dropped == (
            DroppedSchedule(
                "fast", 86_390, (NOW - timedelta(seconds=86_399)).isoformat()
            ),
        )
    finally:
        store.close()


def test_the_catchup_limit_lowers_the_cap_and_names_the_oldest_drop() -> None:
    store = _store()
    try:
        capped = Schedule(
            name="hourly-capped",
            stage="PROCESS",
            interval_seconds=3600,
            catchup_limit=3,
        )
        tick = Scheduler(store.conn, [capped]).tick(now=NOW)
        assert len(tick.created) == 3
        assert tick.dropped == (
            DroppedSchedule("hourly-capped", 21, "2026-10-20T07:00:00+00:00"),
        )

        # A catch-up limit above the cap changes nothing.
        wide = Schedule(
            name="wide",
            stage="PROCESS",
            interval_seconds=3600,
            catchup_limit=500,
        )
        limited = Scheduler(store.conn, [wide], max_occurrences=10).tick(now=NOW)
        assert len(limited.created) == 10
        assert limited.dropped == (
            DroppedSchedule("wide", 14, "2026-10-20T07:00:00+00:00"),
        )
    finally:
        store.close()


def test_a_bare_window_or_a_bare_cap_is_refused() -> None:
    store = _store()
    try:
        with pytest.raises(ScheduleError):
            Scheduler(store.conn, [], lookback_seconds=0)
        with pytest.raises(ScheduleError):
            Scheduler(store.conn, [], lookback_seconds=-1)
        with pytest.raises(ScheduleError):
            Scheduler(store.conn, [], max_occurrences=0)
        with pytest.raises(ScheduleError):
            Scheduler(store.conn, [], max_occurrences=-1)
    finally:
        store.close()


def test_an_oversized_payload_is_refused() -> None:
    store = _store()
    try:
        with pytest.raises(ScheduleError):
            Schedule(
                name="big",
                stage="COLLECT",
                interval_seconds=60,
                payload={"blob": "x" * 70_000},
            )
        # A payload just under the limit is accepted.
        Schedule(
            name="ok",
            stage="COLLECT",
            interval_seconds=60,
            payload={"blob": "x" * 65_000},
        )
    finally:
        store.close()


def test_a_backoff_hook_cannot_return_an_immediate_requeue() -> None:
    for delay in (0, -30):
        store = _store()
        try:
            def boom(context):
                raise RuntimeError("boom")

            job = enqueue_job(
                store.conn,
                stage="COLLECT",
                idempotency_key="immediate",
                payload={},
                scheduled_for="2020-01-01T00:00:00+00:00",
                now=NOW,
            )
            worker = Worker(
                store.conn,
                "worker-a",
                {"COLLECT": boom},
                backoff=lambda job, d=delay: d,
            )
            before = utcnow()
            outcome = worker.run_once()
            after = utcnow()
            assert outcome is not None
            assert outcome.status == "failed"
            failed = get_job(store.conn, job.id)
            assert failed is not None
            assert failed.status == "queued"
            scheduled = datetime.fromisoformat(failed.scheduled_for)
            assert before + timedelta(seconds=1) <= scheduled
            assert scheduled <= after + timedelta(seconds=1)
            # The job waits, so it is not claimable the moment it failed.
            assert claim_job(store.conn, "worker-b", now=before) is None
        finally:
            store.close()


def test_a_backoff_hook_that_raises_falls_back_to_the_flat_delay() -> None:
    store = _store()
    try:
        def boom(context):
            raise RuntimeError("boom")

        def broken(job):
            raise ValueError("no delay for you")

        job = enqueue_job(
            store.conn,
            stage="COLLECT",
            idempotency_key="broken-hook",
            payload={},
            scheduled_for="2020-01-01T00:00:00+00:00",
            now=NOW,
        )
        worker = Worker(
            store.conn,
            "worker-a",
            {"COLLECT": boom},
            backoff_seconds=7,
            backoff=broken,
        )
        before = utcnow()
        outcome = worker.run_once()
        after = utcnow()
        assert outcome is not None
        assert outcome.status == "failed"
        failed = get_job(store.conn, job.id)
        assert failed is not None
        assert failed.status == "queued"
        scheduled = datetime.fromisoformat(failed.scheduled_for)
        assert before + timedelta(seconds=7) <= scheduled
        assert scheduled <= after + timedelta(seconds=7)
    finally:
        store.close()


def test_a_cap_above_the_ceiling_is_refused() -> None:
    store = _store()
    try:
        with pytest.raises(ScheduleError) as refused:
            Scheduler(store.conn, [], max_occurrences=10_001)
        message = str(refused.value)
        assert "10001" in message
        assert "10000" in message
        # The ceiling itself is allowed.
        Scheduler(store.conn, [], max_occurrences=10_000)
    finally:
        store.close()


def test_a_blocked_schedule_is_logged_as_a_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    store = _store()
    try:
        _source(store, "src-off", "reviewed", 0)
        schedule = Schedule(
            name="off",
            stage="COLLECT",
            interval_seconds=86400,
            offset_seconds=21600,
            source_id="src-off",
        )
        with caplog.at_level(logging.WARNING, logger="product_hunter.db.scheduler"):
            tick = Scheduler(store.conn, [schedule]).tick(now=NOW)
        records = _warnings(caplog, "product_hunter.db.scheduler")
        assert len(records) == 1
        message = records[0].getMessage()
        assert "off" in message
        assert "source_disabled" in message
        # The log does not replace the report.
        assert tick.blocked == (
            BlockedSchedule("off", "source_disabled", NOW.isoformat()),
        )
    finally:
        store.close()


def test_a_truncated_schedule_is_logged_as_a_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    store = _store()
    try:
        schedule = Schedule(name="fast", stage="COLLECT", interval_seconds=1)
        with caplog.at_level(logging.WARNING, logger="product_hunter.db.scheduler"):
            tick = Scheduler(store.conn, [schedule], max_occurrences=10).tick(now=NOW)
        records = _warnings(caplog, "product_hunter.db.scheduler")
        assert len(records) == 1
        message = records[0].getMessage()
        assert "fast" in message
        assert "86390" in message
        assert "2026-10-20T06:00:01+00:00" in message
        # The log does not replace the report.
        assert tick.dropped == (
            DroppedSchedule("fast", 86_390, "2026-10-20T06:00:01+00:00"),
        )
    finally:
        store.close()


def test_a_broken_backoff_hook_is_logged_and_the_job_is_requeued(
    caplog: pytest.LogCaptureFixture,
) -> None:
    store = _store()
    try:
        def boom(context):
            raise RuntimeError("boom")

        def broken(job):
            raise ValueError("no delay for you")

        job = enqueue_job(
            store.conn,
            stage="COLLECT",
            idempotency_key="logged-hook",
            payload={},
            scheduled_for="2020-01-01T00:00:00+00:00",
            now=NOW,
        )
        worker = Worker(
            store.conn,
            "worker-a",
            {"COLLECT": boom},
            backoff_seconds=7,
            backoff=broken,
        )
        with caplog.at_level(logging.WARNING, logger="product_hunter.db.worker"):
            outcome = worker.run_once()
        records = _warnings(caplog, "product_hunter.db.worker")
        assert len(records) == 1
        assert job.id in records[0].getMessage()
        assert outcome is not None
        assert outcome.status == "failed"
        failed = get_job(store.conn, job.id)
        assert failed is not None
        assert failed.status == "queued"
    finally:
        store.close()


def test_a_changed_schedule_is_reported_as_a_conflict_and_the_tick_continues(
    caplog: pytest.LogCaptureFixture,
) -> None:
    store = _store()
    try:
        first = Schedule(
            name="daily",
            stage="COLLECT",
            interval_seconds=86400,
            offset_seconds=21600,
        )
        tick = Scheduler(store.conn, [first]).tick(now=NOW)
        assert len(tick.created) == 1

        changed = Schedule(
            name="daily",
            stage="PROCESS",
            interval_seconds=86400,
            offset_seconds=21600,
            payload={"source": "hn"},
        )
        other = Schedule(
            name="other",
            stage="BRIEF",
            interval_seconds=86400,
            offset_seconds=21600,
        )
        with caplog.at_level(logging.WARNING, logger="product_hunter.db.scheduler"):
            second = Scheduler(store.conn, [changed, other]).tick(now=NOW)
        # The conflict does not stop the tick.
        assert len(second.conflicts) == 1
        conflict = second.conflicts[0]
        assert second.conflicts == (
            ConflictedSchedule("daily", NOW.isoformat(), conflict.detail),
        )
        assert "different content" in conflict.detail
        # The other schedule in the same scheduler still got its job.
        assert [job.payload["schedule"] for job in second.created] == ["other"]
        # The stored job still holds the old content.
        stored = get_job(store.conn, tick.created[0].id)
        assert stored is not None
        assert stored.stage == "COLLECT"
        # The conflict is logged, naming the schedule and the occurrence.
        records = _warnings(caplog, "product_hunter.db.scheduler")
        assert len(records) == 1
        message = records[0].getMessage()
        assert "daily" in message
        assert NOW.isoformat() in message
    finally:
        store.close()


def test_a_schedule_with_a_priority_that_is_not_an_integer_is_refused() -> None:
    store = _store()
    try:
        for bad in ("30", 30.0, True, None):
            with pytest.raises(ScheduleError):
                Schedule(
                    name="x", stage="COLLECT", interval_seconds=60, priority=bad
                )
        # An enum member is an int, and it is stored as a plain int.
        schedule = Schedule(
            name="x", stage="COLLECT", interval_seconds=60, priority=Priority.P0
        )
        assert schedule.priority == int(Priority.P0)
        assert type(schedule.priority) is int
    finally:
        store.close()


def test_promote_refuses_a_priority_that_is_not_an_integer() -> None:
    store = _store()
    try:
        scheduler = Scheduler(store.conn, [])
        job = enqueue_job(
            store.conn,
            stage="COLLECT",
            idempotency_key="promoted",
            payload={},
            priority=10,
            scheduled_for=NOW.isoformat(),
            now=NOW,
        )
        with pytest.raises(ScheduleError):
            scheduler.promote(job.id, priority="50")
        kept = get_job(store.conn, job.id)
        assert kept is not None
        assert kept.priority == 10
    finally:
        store.close()


def test_a_repeated_retry_with_a_different_reason_returns_the_same_job(
    caplog: pytest.LogCaptureFixture,
) -> None:
    store = _store()
    try:
        schedule = Schedule(
            name="daily",
            stage="COLLECT",
            interval_seconds=86400,
            offset_seconds=21600,
            max_attempts=1,
        )
        scheduler = Scheduler(store.conn, [schedule])
        scheduler.tick(now=NOW)
        claimed = claim_job(store.conn, "worker-a", lease_seconds=60, now=NOW)
        assert claimed is not None
        dead = fail_job(
            store.conn,
            claimed,
            worker_id="worker-a",
            lease_epoch=claimed.lease_epoch,
            error="boom",
            backoff_seconds=0,
            now=NOW,
        )
        assert dead.status == "dead"

        with caplog.at_level(logging.INFO, logger="product_hunter.db.scheduler"):
            first = scheduler.schedule_retry(dead, now=NOW, reason="network")
            second = scheduler.schedule_retry(dead, now=NOW, reason="timeout")
        assert first.id == second.id
        # The reason is recorded in the log, not in the payload.
        assert "network" in caplog.text
        assert "timeout" in caplog.text
        assert "reason" not in first.payload
        # The dead job and one retry, no second retry.
        assert store.conn.execute("SELECT count(*) FROM jobs").fetchone()[0] == 2
    finally:
        store.close()


def test_a_retry_of_a_job_that_is_not_dead_is_refused() -> None:
    store = _store()
    try:
        scheduler = Scheduler(store.conn, [])
        job = enqueue_job(
            store.conn,
            stage="COLLECT",
            idempotency_key="queued",
            payload={},
            scheduled_for=NOW.isoformat(),
            now=NOW,
        )
        assert job.status == "queued"
        with pytest.raises(ScheduleError) as refused:
            scheduler.schedule_retry(job, now=NOW, reason="manual")
        assert "queued" in str(refused.value)
        assert store.conn.execute("SELECT count(*) FROM jobs").fetchone()[0] == 1
    finally:
        store.close()


def test_the_plan_and_the_queue_order_two_equal_jobs_the_same_way() -> None:
    store = _store()
    try:
        scheduler = Scheduler(store.conn, [])
        keys = ["equal-a", "equal-b"]
        # The two jobs are equal in every visible field, so only the id can
        # order them. They go in with the larger id first, so the table's own
        # order and the id order disagree and the tiebreak is what decides.
        for key in sorted(keys, key=_job_id, reverse=True):
            enqueue_job(
                store.conn,
                stage="COLLECT",
                idempotency_key=key,
                payload={},
                priority=20,
                scheduled_for=NOW.isoformat(),
                now=NOW,
            )
        plan = scheduler.plan(now=NOW)
        assert len(plan) == 2
        claimed = claim_job(store.conn, "worker-a", now=NOW)
        assert claimed is not None
        claimed_again = claim_job(store.conn, "worker-b", now=NOW)
        assert claimed_again is not None
        assert [job.id for job in plan] == [claimed.id, claimed_again.id]
        assert [claimed.id, claimed_again.id] == sorted(
            [claimed.id, claimed_again.id]
        )
    finally:
        store.close()


def test_the_first_tick_on_a_fresh_database_queues_eight_jobs() -> None:
    store = _store()
    try:
        tick = Scheduler(store.conn, default_schedules()).tick(now=CADENCE_NOW)
        assert len(tick.created) == 8
        per_schedule: dict[str, int] = {}
        for job in tick.created:
            name = str(job.payload["schedule"])
            per_schedule[name] = per_schedule.get(name, 0) + 1
        assert len(per_schedule) == 5
        assert max(per_schedule.values()) <= 2
    finally:
        store.close()


def test_the_plan_and_the_queue_agree_on_a_naive_now() -> None:
    store = _store()
    try:
        scheduler = Scheduler(store.conn, [])
        enqueue_job(
            store.conn,
            stage="COLLECT",
            idempotency_key="naive",
            payload={},
            scheduled_for=NOW.isoformat(),
            now=NOW,
        )
        naive = datetime(2026, 10, 21, 6, 0)
        plan = scheduler.plan(now=naive)
        assert len(plan) == 1
        claimed = claim_job(store.conn, "worker-a", now=naive)
        assert claimed is not None
        assert claimed.id == plan[0].id
    finally:
        store.close()


def test_a_missing_run_aborts_the_tick_before_any_work() -> None:
    store = _store()
    try:
        good = Schedule(
            name="good", stage="COLLECT", interval_seconds=86400, offset_seconds=21600
        )
        bad = Schedule(
            name="bad",
            stage="COLLECT",
            interval_seconds=86400,
            offset_seconds=21600,
            run_id="nope",
        )
        with pytest.raises(ScheduleError) as refused:
            Scheduler(store.conn, [good, bad]).tick(now=NOW)
        assert "bad" in str(refused.value)
        # The good schedule wrote nothing: the tick is all or nothing.
        assert store.conn.execute("SELECT count(*) FROM jobs").fetchone()[0] == 0
    finally:
        store.close()


def test_a_payload_that_cannot_be_serialised_is_refused() -> None:
    store = _store()
    try:
        with pytest.raises(ScheduleError):
            Schedule(
                name="x",
                stage="COLLECT",
                interval_seconds=60,
                payload={"blob": object()},
            )
    finally:
        store.close()


def test_a_window_that_holds_no_grid_point_holds_no_occurrence() -> None:
    store = _store()
    try:
        schedule = Schedule(
            name="daily", stage="COLLECT", interval_seconds=86400, offset_seconds=21600
        )
        early = datetime(1970, 1, 1, 0, 0, 1, tzinfo=timezone.utc)
        tick = Scheduler(store.conn, [schedule]).tick(now=early)
        assert tick.created == ()
        assert tick.existing == ()
        assert tick.blocked == ()
        assert store.conn.execute("SELECT count(*) FROM jobs").fetchone()[0] == 0
    finally:
        store.close()


def test_a_naive_now_is_read_as_utc() -> None:
    store = _store()
    try:
        schedule = Schedule(
            name="daily", stage="COLLECT", interval_seconds=86400, offset_seconds=21600
        )
        naive = datetime(2026, 10, 21, 6, 0)
        tick = Scheduler(store.conn, [schedule]).tick(now=naive)
        assert len(tick.created) == 1
        assert tick.now == NOW.isoformat()
        assert tick.created[0].scheduled_for == NOW.isoformat()
    finally:
        store.close()


def test_the_backoff_policy_is_fixed_and_not_configurable() -> None:
    store = _store()
    try:
        scheduler = Scheduler(store.conn, [])
        assert scheduler.backoff_seconds(1) == 30
        with pytest.raises(TypeError):
            Scheduler(store.conn, [], backoff_base_seconds=10)
    finally:
        store.close()


def test_a_tick_that_raises_leaves_the_jobs_table_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _store()
    try:
        schedules = [
            Schedule(
                name="a", stage="COLLECT", interval_seconds=86400, offset_seconds=21600
            ),
            Schedule(
                name="b", stage="PROCESS", interval_seconds=86400, offset_seconds=21600
            ),
        ]
        real = scheduler_module.enqueue_job
        keys: list[str] = []

        def flaky(conn, **kwargs):
            keys.append(kwargs["idempotency_key"])
            if len(keys) == 2:
                raise RuntimeError("boom")
            return real(conn, **kwargs)

        monkeypatch.setattr(scheduler_module, "enqueue_job", flaky)
        with pytest.raises(RuntimeError):
            Scheduler(store.conn, schedules).tick(now=NOW)
        # The first occurrence was written and then rolled back with the rest.
        assert len(keys) == 2
        assert store.conn.execute("SELECT count(*) FROM jobs").fetchone()[0] == 0
        assert not store.conn.in_transaction
    finally:
        store.close()


def test_a_tick_inside_an_open_transaction_joins_it() -> None:
    store = _store()
    try:
        store.conn.execute("BEGIN IMMEDIATE")
        tick = Scheduler(store.conn, [DAILY]).tick(now=NOW)
        assert len(tick.created) == 1
        # The tick did not commit: the caller's transaction is still open.
        assert store.conn.in_transaction
        store.conn.execute("ROLLBACK")
        # The job went with the transaction the caller rolled back.
        assert store.conn.execute("SELECT count(*) FROM jobs").fetchone()[0] == 0
    finally:
        store.close()


def test_a_priority_outside_the_signed_64_bit_range_is_refused() -> None:
    store = _store()
    try:
        for too_big in (2**63, 10**30):
            with pytest.raises(ScheduleError):
                Schedule(
                    name="x", stage="COLLECT", interval_seconds=60, priority=too_big
                )
        with pytest.raises(ScheduleError):
            Schedule(
                name="x", stage="COLLECT", interval_seconds=60, priority=-(2**63) - 1
            )
        # The ends of the range are allowed.
        for edge in (2**63 - 1, -(2**63)):
            Schedule(name="x", stage="COLLECT", interval_seconds=60, priority=edge)
        # The same range applies to a promotion.
        scheduler = Scheduler(store.conn, [])
        job = enqueue_job(
            store.conn,
            stage="COLLECT",
            idempotency_key="wide",
            payload={},
            priority=10,
            scheduled_for=NOW.isoformat(),
            now=NOW,
        )
        with pytest.raises(ScheduleError):
            scheduler.promote(job.id, priority=2**63)
        kept = get_job(store.conn, job.id)
        assert kept is not None
        assert kept.priority == 10
    finally:
        store.close()


def test_a_span_timedelta_cannot_hold_is_refused() -> None:
    store = _store()
    try:
        with pytest.raises(ScheduleError):
            Schedule(name="x", stage="COLLECT", interval_seconds=10**18)
        with pytest.raises(ScheduleError):
            Schedule(
                name="x", stage="COLLECT", interval_seconds=60, offset_seconds=10**18
            )
    finally:
        store.close()


def test_a_lookback_timedelta_cannot_hold_is_refused() -> None:
    store = _store()
    try:
        with pytest.raises(ScheduleError):
            Scheduler(store.conn, [], lookback_seconds=10**18)
    finally:
        store.close()


def test_the_schedule_keeps_a_copy_of_the_payload() -> None:
    payload = {"source": "hn"}
    schedule = Schedule(name="x", stage="COLLECT", interval_seconds=60, payload=payload)
    payload["source"] = "other"
    payload["added"] = True
    assert schedule.payload == {"source": "hn"}


def test_the_schedule_keeps_a_deep_copy_of_the_payload() -> None:
    payload = {"source": "hn", "filters": {"tags": ["a"]}}
    schedule = Schedule(name="x", stage="COLLECT", interval_seconds=60, payload=payload)
    # A nested value the caller mutates must not reach the schedule.
    payload["filters"]["tags"].append("b")
    payload["filters"]["since"] = 2026
    assert schedule.payload == {"source": "hn", "filters": {"tags": ["a"]}}
    assert schedule.payload["filters"] is not payload["filters"]


def test_a_failed_tick_inside_an_open_transaction_leaves_the_table_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _store()
    try:
        schedules = [
            Schedule(
                name="a", stage="COLLECT", interval_seconds=86400, offset_seconds=21600
            ),
            Schedule(
                name="b", stage="PROCESS", interval_seconds=86400, offset_seconds=21600
            ),
        ]
        real = scheduler_module.enqueue_job
        keys: list[str] = []

        def flaky(conn, **kwargs):
            keys.append(kwargs["idempotency_key"])
            if len(keys) == 2:
                raise RuntimeError("boom")
            return real(conn, **kwargs)

        monkeypatch.setattr(scheduler_module, "enqueue_job", flaky)
        store.conn.execute("BEGIN IMMEDIATE")
        with pytest.raises(RuntimeError):
            Scheduler(store.conn, schedules).tick(now=NOW)
        # The first occurrence was written and then undone by the savepoint.
        assert len(keys) == 2
        assert store.conn.execute("SELECT count(*) FROM jobs").fetchone()[0] == 0
        # The caller's transaction is still open, and still usable.
        assert store.conn.in_transaction
        monkeypatch.setattr(scheduler_module, "enqueue_job", real)
        tick = Scheduler(store.conn, [DAILY]).tick(now=NOW)
        assert len(tick.created) == 1
        store.conn.execute("COMMIT")
        assert store.conn.execute("SELECT count(*) FROM jobs").fetchone()[0] == 1
    finally:
        store.close()


def test_a_successful_tick_in_an_open_transaction_leaves_the_job_to_commit() -> None:
    store = _store()
    try:
        store.conn.execute("BEGIN IMMEDIATE")
        tick = Scheduler(store.conn, [DAILY]).tick(now=NOW)
        assert len(tick.created) == 1
        # The tick did not commit: the caller's transaction is still open.
        assert store.conn.in_transaction
        store.conn.execute("COMMIT")
        # The caller's commit is what made the job durable.
        assert store.conn.execute("SELECT count(*) FROM jobs").fetchone()[0] == 1
        assert not store.conn.in_transaction
    finally:
        store.close()


def test_a_span_a_datetime_cannot_express_is_refused() -> None:
    store = _store()
    try:
        with pytest.raises(ScheduleError):
            Schedule(
                name="x", stage="COLLECT", interval_seconds=60, offset_seconds=10**12
            )
        with pytest.raises(ScheduleError):
            Scheduler(store.conn, [], lookback_seconds=10**12)
        with pytest.raises(ScheduleError):
            Schedule(
                name="x", stage="COLLECT", interval_seconds=60, max_attempts=10**30
            )
        # A span inside the datetime range but outside the grid is refused by
        # the tick, not by a raw overflow.
        wide = Schedule(
            name="wide",
            stage="COLLECT",
            interval_seconds=60,
            offset_seconds=300_000_000_000,
        )
        with pytest.raises(ScheduleError):
            Scheduler(store.conn, [wide]).tick(now=NOW)
        with pytest.raises(ScheduleError):
            Scheduler(
                store.conn, [DAILY], lookback_seconds=300_000_000_000
            ).tick(now=NOW)
    finally:
        store.close()


def test_a_changed_priority_is_reported_as_a_conflict(
    caplog: pytest.LogCaptureFixture,
) -> None:
    store = _store()
    try:
        first = Schedule(
            name="daily",
            stage="COLLECT",
            interval_seconds=86400,
            offset_seconds=21600,
            priority=int(Priority.P3),
        )
        tick = Scheduler(store.conn, [first]).tick(now=NOW)
        assert len(tick.created) == 1

        changed = replace(first, priority=int(Priority.P0))
        with caplog.at_level(logging.WARNING, logger="product_hunter.db.scheduler"):
            second = Scheduler(store.conn, [changed]).tick(now=NOW)
        # The tick does not raise.
        assert len(second.conflicts) == 1
        conflict = second.conflicts[0]
        assert conflict.name == "daily"
        assert conflict.occurrence == NOW.isoformat()
        assert "priority" in conflict.detail
        # The stored job is not the job the schedule describes, so it is
        # neither created nor reported as existing.
        assert second.created == ()
        assert second.existing == ()
        stored = get_job(store.conn, tick.created[0].id)
        assert stored is not None
        assert stored.priority == int(Priority.P3)
        # The conflict is logged, naming the schedule and the difference.
        records = _warnings(caplog, "product_hunter.db.scheduler")
        assert len(records) == 1
        message = records[0].getMessage()
        assert "daily" in message
        assert "priority" in message
    finally:
        store.close()


def test_a_job_the_queue_requeued_is_not_reported_as_a_conflict() -> None:
    store = _store()
    try:
        schedule = Schedule(
            name="daily",
            stage="COLLECT",
            interval_seconds=86400,
            offset_seconds=21600,
            max_attempts=3,
        )
        scheduler = Scheduler(store.conn, [schedule])
        tick = scheduler.tick(now=NOW)
        assert len(tick.created) == 1

        claimed = claim_job(store.conn, "worker-a", lease_seconds=60, now=NOW)
        assert claimed is not None
        failed = fail_job(
            store.conn,
            claimed,
            worker_id="worker-a",
            lease_epoch=claimed.lease_epoch,
            error="boom",
            backoff_seconds=30,
            now=NOW,
        )
        # The queue moved the scheduled time, not the schedule.
        assert failed.scheduled_for == (NOW + timedelta(seconds=30)).isoformat()

        # The tick does not raise, and the moved time is not a conflict.
        second = scheduler.tick(now=NOW)
        assert second.conflicts == ()
        assert len(second.existing) == 1
        assert second.existing[0].id == tick.created[0].id
    finally:
        store.close()


def test_a_promoted_job_is_not_reported_as_a_conflict() -> None:
    store = _store()
    try:
        schedule = Schedule(
            name="daily",
            stage="COLLECT",
            interval_seconds=86400,
            offset_seconds=21600,
            priority=int(Priority.P3),
        )
        scheduler = Scheduler(store.conn, [schedule])
        tick = scheduler.tick(now=NOW)
        assert len(tick.created) == 1

        promoted = scheduler.promote(tick.created[0].id, priority=int(Priority.P0))
        assert promoted.priority == int(Priority.P0)

        # A job above the schedule's priority was promoted on purpose.
        second = scheduler.tick(now=NOW)
        assert second.conflicts == ()
        assert len(second.existing) == 1
        assert second.existing[0].priority == int(Priority.P0)
    finally:
        store.close()


def test_a_changed_attempt_ceiling_is_reported_as_a_conflict() -> None:
    store = _store()
    try:
        first = Schedule(
            name="daily",
            stage="COLLECT",
            interval_seconds=86400,
            offset_seconds=21600,
            max_attempts=3,
        )
        tick = Scheduler(store.conn, [first]).tick(now=NOW)
        assert len(tick.created) == 1

        changed = replace(first, max_attempts=5)
        second = Scheduler(store.conn, [changed]).tick(now=NOW)
        assert len(second.conflicts) == 1
        conflict = second.conflicts[0]
        assert conflict.name == "daily"
        assert conflict.occurrence == NOW.isoformat()
        assert "max_attempts" in conflict.detail
        # The stored job is not the job the schedule describes.
        assert second.created == ()
        assert second.existing == ()
        stored = get_job(store.conn, tick.created[0].id)
        assert stored is not None
        assert stored.max_attempts == 3
    finally:
        store.close()


def test_a_schedule_pointed_at_another_run_is_reported_as_a_conflict() -> None:
    store = _store()
    try:
        for run_id in ("run-1", "run-2"):
            store.conn.execute(
                "INSERT INTO research_runs (run_id, public_id, objective, "
                "original_request) VALUES (?, ?, ?, ?)",
                (run_id, run_id, "find pains", "find pains"),
            )
        first = Schedule(
            name="daily",
            stage="COLLECT",
            interval_seconds=86400,
            offset_seconds=21600,
            run_id="run-1",
        )
        tick = Scheduler(store.conn, [first]).tick(now=NOW)
        assert len(tick.created) == 1

        changed = replace(first, run_id="run-2")
        second = Scheduler(store.conn, [changed]).tick(now=NOW)
        assert len(second.conflicts) == 1
        conflict = second.conflicts[0]
        assert conflict.name == "daily"
        assert "run_id" in conflict.detail
        assert second.existing == ()
        # The stored job books the work against the old run.
        stored = get_job(store.conn, tick.created[0].id)
        assert stored is not None
        assert stored.run_id == "run-1"
    finally:
        store.close()


def test_the_backoff_caps_an_enormous_attempt() -> None:
    store = _store()
    try:
        scheduler = Scheduler(store.conn, [])
        # The exponent is capped, so the answer arrives at once.
        assert scheduler.backoff_seconds(10**18) == 3600
    finally:
        store.close()


def test_promote_refuses_a_demotion() -> None:
    store = _store()
    try:
        scheduler = Scheduler(store.conn, [])
        job = enqueue_job(
            store.conn,
            stage="COLLECT",
            idempotency_key="demoted",
            payload={},
            priority=20,
            scheduled_for=NOW.isoformat(),
            now=NOW,
        )
        with pytest.raises(ScheduleError) as refused:
            scheduler.promote(job.id, priority=10)
        # The error names the priority the job has and the one refused.
        message = str(refused.value)
        assert "20" in message
        assert "10" in message
        # The stored job keeps its priority.
        kept = get_job(store.conn, job.id)
        assert kept is not None
        assert kept.priority == 20
        # The same priority is not a demotion: the job stays where it is.
        same = scheduler.promote(job.id, priority=20)
        assert same.priority == 20
    finally:
        store.close()


def test_a_refused_demotion_leaves_the_next_tick_free_of_conflicts() -> None:
    store = _store()
    try:
        schedule = Schedule(
            name="daily",
            stage="COLLECT",
            interval_seconds=86400,
            offset_seconds=21600,
            priority=int(Priority.P3),
        )
        scheduler = Scheduler(store.conn, [schedule])
        tick = scheduler.tick(now=NOW)
        assert len(tick.created) == 1

        with pytest.raises(ScheduleError):
            scheduler.promote(tick.created[0].id, priority=int(Priority.P4))

        # The job still holds the priority the schedule says, so the tick
        # reports no conflict.
        second = scheduler.tick(now=NOW)
        assert second.conflicts == ()
        assert len(second.existing) == 1
        assert second.existing[0].priority == int(Priority.P3)
    finally:
        store.close()


def test_a_numeric_field_that_is_not_an_integer_is_refused() -> None:
    store = _store()
    try:
        with pytest.raises(ScheduleError):
            Schedule(
                name="x", stage="COLLECT", interval_seconds=60, max_attempts=2.5
            )
        with pytest.raises(ScheduleError):
            Schedule(
                name="x", stage="COLLECT", interval_seconds=60, catchup_limit=2.5
            )
        with pytest.raises(ScheduleError):
            Schedule(name="x", stage="COLLECT", interval_seconds=2.5)
        with pytest.raises(ScheduleError):
            Schedule(
                name="x", stage="COLLECT", interval_seconds=60, offset_seconds=1.5
            )
        # A bool is an int in Python, and it is not a number here.
        with pytest.raises(ScheduleError):
            Schedule(
                name="x", stage="COLLECT", interval_seconds=60, max_attempts=True
            )
        with pytest.raises(ScheduleError):
            Scheduler(store.conn, [], lookback_seconds=1.5)
        with pytest.raises(ScheduleError):
            Scheduler(store.conn, [], max_occurrences=1.5)
    finally:
        store.close()


def test_the_stored_attempt_ceiling_is_an_integer() -> None:
    store = _store()
    try:
        schedule = Schedule(
            name="daily",
            stage="COLLECT",
            interval_seconds=86400,
            offset_seconds=21600,
            max_attempts=4,
        )
        tick = Scheduler(store.conn, [schedule]).tick(now=NOW)
        assert len(tick.created) == 1
        job = tick.created[0]
        assert job.max_attempts == 4
        assert type(job.max_attempts) is int
        # The table holds an integer, not a number SQLite would read back as
        # another value.
        stored = store.conn.execute(
            "SELECT typeof(max_attempts) FROM jobs WHERE id = ?", (job.id,)
        ).fetchone()
        assert stored[0] == "integer"
    finally:
        store.close()


def test_a_backoff_hook_that_returns_an_enormous_delay_is_capped() -> None:
    store = _store()
    try:
        def boom(context):
            raise RuntimeError("boom")

        job = enqueue_job(
            store.conn,
            stage="COLLECT",
            idempotency_key="huge-hook",
            payload={},
            scheduled_for="2020-01-01T00:00:00+00:00",
            now=NOW,
        )
        worker = Worker(
            store.conn,
            "worker-a",
            {"COLLECT": boom},
            backoff=lambda failed_job: 10**18,
        )
        before = utcnow()
        outcome = worker.run_once()
        after = utcnow()
        # The delay is capped, so the requeue does not overflow the worker.
        assert outcome is not None
        assert outcome.status == "failed"
        failed = get_job(store.conn, job.id)
        assert failed is not None
        assert failed.status == "queued"
        scheduled = datetime.fromisoformat(failed.scheduled_for)
        longest = timedelta(seconds=worker_module._MAX_BACKOFF_SECONDS)
        assert before + longest <= scheduled
        assert scheduled <= after + longest
    finally:
        store.close()


def test_promote_refuses_a_job_leased_in_between() -> None:
    store = _store()
    try:
        scheduler = Scheduler(store.conn, [])
        job = enqueue_job(
            store.conn,
            stage="COLLECT",
            idempotency_key="leased-mid-promote",
            payload={},
            priority=20,
            scheduled_for=NOW.isoformat(),
            now=NOW,
        )

        # A worker leases the job between the read and the write, so the
        # statement trace is the only place that race can be staged.
        def lease_it(statement: str) -> None:
            if statement.startswith("UPDATE jobs SET priority"):
                store.conn.execute(
                    "UPDATE jobs SET status = 'leased', worker_id = 'worker-a', "
                    "lease_epoch = lease_epoch + 1, attempts = attempts + 1 "
                    "WHERE id = ?",
                    (job.id,),
                )

        store.conn.set_trace_callback(lease_it)
        try:
            with pytest.raises(ScheduleError) as refused:
                scheduler.promote(job.id, priority=int(Priority.P0))
        finally:
            store.conn.set_trace_callback(None)
        # The error says the job is no longer queued.
        assert "queued" in str(refused.value)
        stored = get_job(store.conn, job.id)
        assert stored is not None
        assert stored.status == "leased"
        # A leased job keeps the priority it had.
        assert stored.priority == 20
    finally:
        store.close()


def test_a_worker_with_an_enormous_flat_delay_does_not_raise() -> None:
    store = _store()
    try:
        def boom(context):
            raise RuntimeError("boom")

        job = enqueue_job(
            store.conn,
            stage="COLLECT",
            idempotency_key="huge-flat-delay",
            payload={},
            scheduled_for="2020-01-01T00:00:00+00:00",
            now=NOW,
        )
        worker = Worker(
            store.conn, "worker-a", {"COLLECT": boom}, backoff_seconds=10**18
        )
        before = utcnow()
        outcome = worker.run_once()
        after = utcnow()
        # The flat delay is capped, so the requeue does not overflow the worker.
        assert outcome is not None
        assert outcome.status == "failed"
        failed = get_job(store.conn, job.id)
        assert failed is not None
        assert failed.status == "queued"
        scheduled = datetime.fromisoformat(failed.scheduled_for)
        longest = timedelta(seconds=worker_module._MAX_BACKOFF_SECONDS)
        assert before + longest <= scheduled
        assert scheduled <= after + longest
    finally:
        store.close()


def test_a_disabled_schedule_with_a_missing_run_does_not_abort_the_tick() -> None:
    store = _store()
    try:
        good = Schedule(
            name="good", stage="COLLECT", interval_seconds=86400, offset_seconds=21600
        )
        paused = Schedule(
            name="paused",
            stage="COLLECT",
            interval_seconds=86400,
            offset_seconds=21600,
            run_id="nope",
            enabled=False,
        )
        tick = Scheduler(store.conn, [good, paused]).tick(now=NOW)
        # The paused schedule is inert, so it cannot veto the good one.
        assert len(tick.created) == 1
        assert tick.created[0].payload["schedule"] == "good"
    finally:
        store.close()


def test_a_fractional_attempt_is_refused() -> None:
    store = _store()
    try:
        scheduler = Scheduler(store.conn, [])
        with pytest.raises(ScheduleError):
            scheduler.backoff_seconds(2.5)
        # An integer attempt still returns the ladder.
        assert scheduler.backoff_seconds(1) == 30
        assert scheduler.backoff_seconds(3) == 120
    finally:
        store.close()


def test_the_fallback_delay_is_logged_after_the_clamp(
    caplog: pytest.LogCaptureFixture,
) -> None:
    store = _store()
    try:
        def boom(context):
            raise RuntimeError("boom")

        def broken(job):
            raise ValueError("no delay for you")

        job = enqueue_job(
            store.conn,
            stage="COLLECT",
            idempotency_key="clamped-hook",
            payload={},
            scheduled_for="2020-01-01T00:00:00+00:00",
            now=NOW,
        )
        worker = Worker(
            store.conn,
            "worker-a",
            {"COLLECT": boom},
            backoff_seconds=0,
            backoff=broken,
        )
        before = utcnow()
        with caplog.at_level(logging.WARNING, logger="product_hunter.db.worker"):
            outcome = worker.run_once()
        after = utcnow()
        records = _warnings(caplog, "product_hunter.db.worker")
        assert len(records) == 1
        message = records[0].getMessage()
        # The log names the delay that was applied, not the flat delay the
        # worker was configured with.
        assert job.id in message
        assert "using 1 seconds" in message
        assert "using 0 seconds" not in message
        # The job waits the second the log names.
        assert outcome is not None
        assert outcome.status == "failed"
        failed = get_job(store.conn, job.id)
        assert failed is not None
        assert failed.status == "queued"
        scheduled = datetime.fromisoformat(failed.scheduled_for)
        assert before + timedelta(seconds=1) <= scheduled
        assert scheduled <= after + timedelta(seconds=1)
    finally:
        store.close()


def test_promote_refuses_a_job_whose_priority_moved_under_it() -> None:
    store = _store()
    try:
        scheduler = Scheduler(store.conn, [])
        job = enqueue_job(
            store.conn,
            stage="COLLECT",
            idempotency_key="promoted-mid-promote",
            payload={},
            priority=20,
            scheduled_for=NOW.isoformat(),
            now=NOW,
        )

        # A second promotion lands between the read and the write, so the
        # job now sits above the priority this call asked for. The flag
        # keeps the statement the callback runs out of its own way.
        inside = False

        def promote_it(statement: str) -> None:
            nonlocal inside
            if statement.startswith("UPDATE jobs SET priority") and not inside:
                inside = True
                store.conn.execute(
                    "UPDATE jobs SET priority = ? WHERE id = ?",
                    (int(Priority.P0), job.id),
                )
                inside = False

        store.conn.set_trace_callback(promote_it)
        try:
            with pytest.raises(ScheduleError) as refused:
                scheduler.promote(job.id, priority=30)
        finally:
            store.conn.set_trace_callback(None)
        # The error says the job is no longer queued.
        assert "queued" in str(refused.value)
        # The job keeps the higher priority the other promotion gave it.
        stored = get_job(store.conn, job.id)
        assert stored is not None
        assert stored.priority == int(Priority.P0)
    finally:
        store.close()

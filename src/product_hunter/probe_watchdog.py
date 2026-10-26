"""The probe watchdog: is the unattended thing actually moving?

A probe never sets ``research_runs.status`` to COMPLETED, and a stalled probe
looks exactly like a quiet one from the outside. This module reads the
execution truth instead: the probe cadence, the run heartbeat and the job
lease. A silence that lasts too long becomes a named ``PROBE_STALLED`` event
and a line in ``ph probe status`` / ``ph probe report``, so the operator or the
AI sees the stall instead of waiting for a cycle that will never start.

The watchdog observes only. It never pauses, retries or promotes anything.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone

from product_hunter.db.connection import transaction, utc_now

TERMINAL_RUN_STATUSES = ("COMPLETED", "CANCELLED", "FAILED_PERMANENT")

STALLED_EVENT = "PROBE_STALLED"


@dataclass(frozen=True)
class StallThresholds:
    """How long a silence is normal before it is a stall."""

    # A run that holds no lease and has not beaten for this long is dead.
    heartbeat_stall_seconds: int = 900
    # A run that holds a live lease but has not advanced for this long is
    # running abnormally long; the worker is alive, the stage is not finishing.
    stage_stall_seconds: int = 3600
    # The probe was due this long ago and still owns no run: the engine is not
    # ticking it.
    cycle_overdue_grace_seconds: int = 300
    # One event per kind per probe per window, so a long stall is not spam.
    report_interval_seconds: int = 900


def thresholds_for(settings: object | None) -> StallThresholds:
    """The thresholds from the autonomy config, with safe defaults.

    Accepts a ``ProbeWatchdogSettings`` (validated at config load) or any
    object exposing the four attributes. Validation errors surface at config
    load, not here, because ``load_autonomy_config`` raises ``ValueError``
    before the watchdog ever ticks.
    """
    if settings is None:
        return StallThresholds()
    data: dict[str, object]
    dumper = getattr(settings, "model_dump", None)
    if callable(dumper):
        try:
            data = dumper()
        except (TypeError, ValueError):
            data = {}
    else:
        data = {}
    if not data:
        data = {
            "heartbeat_stall_seconds": getattr(
                settings, "heartbeat_stall_seconds", 900
            ),
            "stage_stall_seconds": getattr(settings, "stage_stall_seconds", 3600),
            "cycle_overdue_grace_seconds": getattr(
                settings, "cycle_overdue_grace_seconds", 300
            ),
            "report_interval_seconds": getattr(
                settings, "report_interval_seconds", 900
            ),
        }
    return StallThresholds(
        heartbeat_stall_seconds=int(data.get("heartbeat_stall_seconds", 900)),
        stage_stall_seconds=int(data.get("stage_stall_seconds", 3600)),
        cycle_overdue_grace_seconds=int(
            data.get("cycle_overdue_grace_seconds", 300)
        ),
        report_interval_seconds=int(data.get("report_interval_seconds", 900)),
    )


@dataclass(frozen=True)
class Stall:
    """One observed reason a probe is not making progress."""

    probe_id: str
    public_id: str
    kind: str
    seconds: int
    detail: str

    def to_dict(self) -> dict[str, object]:
        return {
            "probe_id": self.probe_id,
            "public_id": self.public_id,
            "kind": self.kind,
            "seconds": self.seconds,
            "detail": self.detail,
        }


def audit_probes(
    conn: sqlite3.Connection,
    *,
    thresholds: StallThresholds | None = None,
    now: datetime | None = None,
    recovery_grace_seconds: float = 0.0,
) -> list[Stall]:
    """Every stall of every RUNNING probe, most severe first per probe.

    A run is live only while its cycle is open. Run.status cannot answer that:
    probe runs stay QUEUED after their result is recorded, so a closed run is
    finished work, not a silent worker.
    """
    limits = thresholds or StallThresholds()
    moment = utc_now(now)
    rows = conn.execute(
        "SELECT probe_id, public_id, next_cycle_at FROM probes "
        "WHERE status = 'RUNNING' ORDER BY public_id"
    ).fetchall()
    stalls: list[Stall] = []
    for row in rows:
        stalls.extend(
            _audit_probe(conn, row, limits, moment, recovery_grace_seconds)
        )
    return stalls


def _audit_probe(
    conn: sqlite3.Connection,
    probe: sqlite3.Row,
    limits: StallThresholds,
    now: datetime,
    recovery_grace_seconds: float = 0.0,
) -> list[Stall]:
    probe_id = probe["probe_id"]
    public_id = probe["public_id"]
    placeholders = ", ".join("?" for _ in TERMINAL_RUN_STATUSES)
    run = conn.execute(
        "SELECT run_id, status, last_heartbeat, created_at FROM research_runs "
        f"WHERE probe_id = ? AND probe_cycle_closed_at IS NULL "
        f"AND UPPER(TRIM(status)) NOT IN ({placeholders}) "
        "ORDER BY created_at DESC LIMIT 1",
        (probe_id, *TERMINAL_RUN_STATUSES),
    ).fetchone()

    if run is None:
        overdue = _seconds_since(probe["next_cycle_at"], now)
        if overdue is None or overdue <= limits.cycle_overdue_grace_seconds:
            return []
        return [
            Stall(
                probe_id=probe_id,
                public_id=public_id,
                kind="cycle_overdue",
                seconds=overdue,
                detail=(
                    f"the probe was due {overdue}s ago and owns no run; "
                    "the engine is not ticking it"
                ),
            )
        ]

    run_id = run["run_id"]
    live = conn.execute(
        "SELECT 1 FROM jobs WHERE run_id = ? AND status = 'leased' "
        "AND lease_expires_at IS NOT NULL AND lease_expires_at > ? LIMIT 1",
        (run_id, now.isoformat()),
    ).fetchone()
    if live is None:
        expired = conn.execute(
            "SELECT MIN(lease_expires_at) FROM jobs WHERE run_id = ? "
            "AND status = 'leased' AND lease_expires_at <= ?",
            (run_id, now.isoformat()),
        ).fetchone()
        if expired is not None and expired[0] is not None:
            age = _seconds_since(expired[0], now) or 0
            # Recovery runs on its own interval; a lease that just expired has
            # not yet had a chance to be reclaimed. Only name it once recovery
            # has had that chance, so a healthy restart is not a false alarm.
            if age < recovery_grace_seconds:
                return []
            return [
                Stall(
                    probe_id=probe_id,
                    public_id=public_id,
                    kind="lease_expired",
                    seconds=age,
                    detail=(
                        f"run {run_id} holds a lease that expired {age}s ago; "
                        "recovery is not reclaiming it"
                    ),
                )
            ]
        age = _seconds_since(run["last_heartbeat"] or run["created_at"], now)
        if age is not None and age > limits.heartbeat_stall_seconds:
            return [
                Stall(
                    probe_id=probe_id,
                    public_id=public_id,
                    kind="run_stalled",
                    seconds=age,
                    detail=(
                        f"run {run_id} has no worker and no heartbeat for {age}s; "
                        "the execution stopped without ending"
                    ),
                )
            ]
        return []

    # The worker holds a live lease. Only a long run is abnormal here: the
    # heartbeat moves at job boundaries, not inside a handler.
    age = _seconds_since(run["last_heartbeat"] or run["created_at"], now)
    if age is not None and age > limits.stage_stall_seconds:
        return [
            Stall(
                probe_id=probe_id,
                public_id=public_id,
                kind="run_overtime",
                seconds=age,
                detail=(
                    f"run {run_id} has run {age}s without advancing a stage; "
                    "the worker is alive but the execution is abnormally long"
                ),
            )
        ]
    return []


def report_stalls(
    conn: sqlite3.Connection,
    *,
    thresholds: StallThresholds | None = None,
    now: datetime | None = None,
    recovery_grace_seconds: float = 0.0,
) -> list[Stall]:
    """Audit, then record the stalls not already reported in this window."""
    limits = thresholds or StallThresholds()
    moment = utc_now(now)
    reported: list[Stall] = []
    window_start = moment.timestamp() - limits.report_interval_seconds
    for stall in audit_probes(
        conn, thresholds=limits, now=moment,
        recovery_grace_seconds=recovery_grace_seconds,
    ):
        recent = conn.execute(
            "SELECT 1 FROM probe_events WHERE probe_id = ? AND event_type = ? "
            "AND kind = ? AND created_at >= ? LIMIT 1",
            (
                stall.probe_id,
                STALLED_EVENT,
                stall.kind,
                datetime.fromtimestamp(window_start, timezone.utc).isoformat(),
            ),
        ).fetchone()
        if recent is not None:
            continue
        event_id = "pev_" + hashlib.sha256(
            f"{stall.probe_id}|{stall.kind}|{moment.isoformat()}".encode("utf-8")
        ).hexdigest()[:16]
        with transaction(conn):
            conn.execute(
                "INSERT OR IGNORE INTO probe_events "
                "(event_id, probe_id, event_type, kind, detail_json, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    event_id,
                    stall.probe_id,
                    STALLED_EVENT,
                    stall.kind,
                    _detail_json(stall),
                    moment.isoformat(),
                ),
            )
        reported.append(stall)
    return reported


def last_stalls(
    conn: sqlite3.Connection, probe_id: str, *, limit: int = 5
) -> list[dict[str, object]]:
    """The recorded stall events of one probe, newest first."""
    rows = conn.execute(
        "SELECT event_type, kind, detail_json, created_at FROM probe_events "
        "WHERE probe_id = ? AND event_type = ? "
        "ORDER BY created_at DESC LIMIT ?",
        (probe_id, STALLED_EVENT, limit),
    ).fetchall()
    return [
        {
            "event_type": row["event_type"],
            "kind": row["kind"],
            "detail": row["detail_json"],
            "created_at": row["created_at"],
        }
        for row in rows
    ]


def _detail_json(stall: Stall) -> str:
    return json.dumps(
        {"kind": stall.kind, "seconds": stall.seconds, "detail": stall.detail},
        sort_keys=True,
    )


def _seconds_since(stamp: object, now: datetime) -> int | None:
    """Whole seconds from a stored timestamp to now, or None if unusable."""
    if not stamp:
        return None
    try:
        then = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    except ValueError:
        return None
    if then.tzinfo is None:
        then = then.replace(tzinfo=timezone.utc)
    return max(0, int((now - then).total_seconds()))

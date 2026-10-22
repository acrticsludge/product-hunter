"""The bounds that keep the operation inside its share.

The pacer holds a source to the interval it may be contacted at, and the
concurrency limiter counts the jobs in flight across every process. Both keep
their state in the database, so a restart hands nobody a fresh allowance.
"""

from __future__ import annotations

import math
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta

from product_hunter.db.connection import utc_now


@dataclass(frozen=True)
class SourcePacing:
    """What the pacer knows about one source."""

    source_id: str
    next_allowed_at: str | None
    last_used_at: str | None
    use_count: int
    updated_at: str

    def to_dict(self) -> dict[str, object]:
        """The pacing row as plain data."""
        return {
            "source_id": self.source_id,
            "next_allowed_at": self.next_allowed_at,
            "last_used_at": self.last_used_at,
            "use_count": self.use_count,
            "updated_at": self.updated_at,
        }


class SourcePacer:
    """Keeps every source to the interval it may be contacted at.

    A job whose payload names a source that is not due yet is put back to the
    queue with a later time. It is not a failure and it does not burn an
    attempt.
    """

    def __init__(
        self,
        conn: sqlite3.Connection,
        intervals: Mapping[str, int],
        default_interval_seconds: int = 0,
    ) -> None:
        if default_interval_seconds < 0:
            raise ValueError("the default interval must not be negative")
        self.conn = conn
        self.intervals = {str(key): int(value) for key, value in intervals.items()}
        self.default_interval_seconds = int(default_interval_seconds)

    def seconds_until_allowed(
        self, source_id: str, *, now: datetime | None = None
    ) -> int:
        """The seconds to wait before the source may be used again.

        Zero means the source may be used now.
        """
        state = self.state(source_id)
        if state is None or not state.next_allowed_at:
            return 0
        allowed = datetime.fromisoformat(state.next_allowed_at)
        now_dt = utc_now(now)
        if allowed <= now_dt:
            return 0
        return max(1, math.ceil((allowed - now_dt).total_seconds()))

    def mark_used(self, source_id: str, *, now: datetime | None = None) -> None:
        """Record that the source was used, and when it may be used again."""
        now_dt = utc_now(now)
        now_iso = now_dt.isoformat()
        interval = self.intervals.get(source_id, self.default_interval_seconds)
        next_allowed = (now_dt + timedelta(seconds=interval)).isoformat()
        self.conn.execute(
            """
            INSERT INTO source_pacing (
                source_id, next_allowed_at, last_used_at, use_count, updated_at
            ) VALUES (?, ?, ?, 1, ?)
            ON CONFLICT(source_id) DO UPDATE SET
                next_allowed_at = excluded.next_allowed_at,
                last_used_at = excluded.last_used_at,
                use_count = source_pacing.use_count + 1,
                updated_at = excluded.updated_at
            """,
            (source_id, next_allowed, now_iso, now_iso),
        )

    def state(self, source_id: str) -> SourcePacing | None:
        """What the pacer knows about the source, or None when it has no row."""
        row = self.conn.execute(
            "SELECT * FROM source_pacing WHERE source_id = ?", (source_id,)
        ).fetchone()
        if row is None:
            return None
        return SourcePacing(
            source_id=str(row["source_id"]),
            next_allowed_at=row["next_allowed_at"],
            last_used_at=row["last_used_at"],
            use_count=int(row["use_count"]),
            updated_at=str(row["updated_at"]),
        )


class ConcurrencyLimiter:
    """Counts the jobs in flight, over every process that shares the store."""

    def __init__(self, conn: sqlite3.Connection, limit: int) -> None:
        if limit < 1:
            raise ValueError("the concurrency limit must be at least 1")
        self.conn = conn
        self.limit = int(limit)

    def in_flight(self, *, now: datetime | None = None) -> int:
        """The jobs that are leased and whose lease has not expired.

        The count is the same for every process, because the rows are the
        same, and that is what makes the limit global rather than per
        process. A lease that has already expired is not in flight: its job
        is waiting for the recovery pass.
        """
        now_iso = utc_now(now).isoformat()
        row = self.conn.execute(
            "SELECT count(*) FROM jobs WHERE status = 'leased' "
            "AND (lease_expires_at IS NULL OR lease_expires_at > ?)",
            (now_iso,),
        ).fetchone()
        return int(row[0])

    def at_capacity(self, *, now: datetime | None = None) -> bool:
        """Whether the limit has been reached."""
        return self.in_flight(now=now) >= self.limit

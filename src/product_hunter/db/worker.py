"""Supervised job worker with fenced handlers.

The worker claims a job, calls the handler with a ``JobContext``, and records
the outcome only while its lease is still current. A handler that loses its
lease stops writing; a crash leaves the job recoverable rather than half
applied.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable
from threading import Event

from product_hunter.db.jobs import (
    Job,
    JobContext,
    JobOutcome,
    LeaseLost,
    claim_job,
    complete_job,
    fail_job,
    recover_expired_leases,
)

Handler = Callable[[JobContext], dict[str, object] | None]
Backoff = Callable[[Job], int]

logger = logging.getLogger(__name__)

# A retry delay longer than a day is a bug in the backoff hook, and the
# worker does not park a job for longer than that.
_MAX_BACKOFF_SECONDS = 86_400


class Worker:
    def __init__(
        self,
        conn: sqlite3.Connection,
        worker_id: str,
        handlers: dict[str, Handler],
        *,
        lease_seconds: int = 60,
        backoff_seconds: int = 5,
        backoff: Backoff | None = None,
    ) -> None:
        if not worker_id.strip():
            raise ValueError("a worker needs an id")
        if lease_seconds < 1:
            raise ValueError("lease_seconds must be at least 1")
        self.conn = conn
        self.worker_id = worker_id
        self.handlers = dict(handlers)
        self.lease_seconds = lease_seconds
        self.backoff_seconds = backoff_seconds
        self.backoff = backoff

    def _delay(self, job: Job) -> int:
        """The requeue delay for a failed job: the hook's, or the flat one.

        The hook may not return a delay that requeues the job straight back
        into the queue, and it may not raise: a broken hook falls back to
        the flat delay rather than killing the worker. Every delay is
        capped, so a huge one cannot overflow the requeue into a crash.
        """
        if self.backoff is None:
            # The flat delay is the caller's own value: it keeps the delay it
            # was configured with, so a worker that asks for no delay retries
            # at once. The cap still applies, so a huge one cannot overflow
            # the requeue.
            return min(self.backoff_seconds, _MAX_BACKOFF_SECONDS)
        try:
            delay = int(self.backoff(job))
        except Exception as exc:
            # The log names the delay that was applied, not the one the
            # worker was configured with, so the clamp is visible in the
            # one line that explains the fallback.
            delay = min(max(1, self.backoff_seconds), _MAX_BACKOFF_SECONDS)
            logger.warning(
                "the backoff hook failed for job %s (%s); using %s seconds",
                job.id,
                exc,
                delay,
            )
        return min(max(1, delay), _MAX_BACKOFF_SECONDS)

    def recover(self) -> int:
        return recover_expired_leases(self.conn)

    def run_once(self) -> JobOutcome | None:
        job = claim_job(
            self.conn, self.worker_id, lease_seconds=self.lease_seconds
        )
        if job is None:
            return None
        handler = self.handlers.get(job.stage)
        if handler is None:
            try:
                fail_job(
                    self.conn,
                    job,
                    worker_id=self.worker_id,
                    lease_epoch=job.lease_epoch,
                    error=f"no handler for stage {job.stage!r}",
                    backoff_seconds=self._delay(job),
                )
            except LeaseLost:
                return JobOutcome(job.id, "lease_lost")
            return JobOutcome(job.id, "failed", error="no handler")

        context = JobContext(
            self.conn,
            job,
            worker_id=self.worker_id,
            lease_seconds=self.lease_seconds,
        )
        try:
            result = handler(context)
            if result is not None and not isinstance(result, dict):
                raise TypeError("a job handler must return a dict or None")
        except LeaseLost:
            return JobOutcome(job.id, "lease_lost")
        except Exception as exc:
            try:
                failed = fail_job(
                    self.conn,
                    job,
                    worker_id=self.worker_id,
                    lease_epoch=job.lease_epoch,
                    error=f"{type(exc).__name__}: {exc}",
                    backoff_seconds=self._delay(job),
                )
            except LeaseLost:
                return JobOutcome(job.id, "lease_lost")
            return JobOutcome(job.id, "failed", error=failed.last_error)
        try:
            completed = complete_job(
                self.conn,
                job,
                worker_id=self.worker_id,
                lease_epoch=job.lease_epoch,
                result=result,
            )
        except LeaseLost:
            return JobOutcome(job.id, "lease_lost")
        return JobOutcome(job.id, "succeeded", result=completed.result)

    def run_forever(
        self,
        *,
        stop_event: Event | None = None,
        poll_seconds: float = 1.0,
    ) -> None:
        if poll_seconds <= 0:
            raise ValueError("poll_seconds must be positive")
        stop = stop_event if stop_event is not None else Event()
        self.recover()
        while not stop.is_set():
            outcome = self.run_once()
            if outcome is None:
                stop.wait(poll_seconds)

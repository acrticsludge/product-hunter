"""The lease fence a worker's write has to pass.

A worker that lost its lease must not write over a newer worker's state, so
every write it makes carries the worker id and the lease epoch it holds. The
fence is all or nothing: a write with only one of the two is a programming
error and is refused, and a write with neither is the control plane rather
than a leased worker.

``product_hunter.db.checkpoints`` grew its own copy of this check when the
checkpoint writes were fenced. It is the same check with the same refusal
messages, so it lives here and the checkpoint writes and the research gate use
it too.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager

from product_hunter.db.connection import rollback as _rollback
from product_hunter.db.connection import utc_now

# Operator-facing refusals. They are fixed text: a message that repeats a job
# id, a worker id, a run id or a stage hands an untrusted value to whoever
# reads the log.
_HALF_FENCE = "a fenced write needs both worker_id and lease_epoch"
_LEASE_REFUSED = "the lease is not yours or has expired"
_NO_JOB = "there is no job for this run and stage"


class LeaseRefused(Exception):
    """A fenced write was refused: the caller does not hold a live lease."""


def assert_live_lease(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    stage: str,
    worker_id: str,
    lease_epoch: int,
) -> None:
    """Refuse a fenced write whose lease is not live.

    One statement answers both questions a refusal needs: whether the caller
    holds a live lease on a job for this run and stage, and whether such a
    job exists at all. The two faults are separate because they are separate:
    a run and stage with no job is a missing job, a job leased to somebody
    else is a lost lease.

    The comparison is against the store's clock and there is no way to pass
    another one. A caller that could name the instant would decide for itself
    whether its lease was still live, and a stale worker would keep its lease
    alive by asking nicely.
    """
    stamp = utc_now().isoformat()
    row = conn.execute(
        """
        SELECT
            EXISTS (
                SELECT 1 FROM jobs
                WHERE run_id = ? AND stage = ? AND status = 'leased'
                  AND worker_id = ? AND lease_epoch = ?
                  AND lease_expires_at > ?
            ) AS lease_held,
            EXISTS (
                SELECT 1 FROM jobs WHERE run_id = ? AND stage = ?
            ) AS job_exists
        """,
        (run_id, stage, worker_id, lease_epoch, stamp, run_id, stage),
    ).fetchone()
    if row["lease_held"]:
        return
    raise LeaseRefused(_LEASE_REFUSED if row["job_exists"] else _NO_JOB)


def assert_fence_pair(
    worker_id: str | None, lease_epoch: int | None
) -> None:
    """Refuse a half fence.

    A fence is the worker id and the lease epoch together. One without the
    other is a programming error, not a write, and a caller that only
    checks the shape of the fence learns about it before it writes.
    """
    if (worker_id is None) != (lease_epoch is None):
        raise LeaseRefused(_HALF_FENCE)


@contextmanager
def fenced_write(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    stage: str,
    worker_id: str | None = None,
    lease_epoch: int | None = None,
) -> Iterator[sqlite3.Connection]:
    """Run one write under the lease fence, in a single transaction.

    The lease check and the write must not be separable, so they share one
    transaction. A caller that owns a transaction keeps it and commits it
    itself; a caller with no transaction gets one here, opened and committed
    here.
    """
    assert_fence_pair(worker_id, lease_epoch)
    owns_transaction = not conn.in_transaction
    if owns_transaction:
        conn.execute("BEGIN IMMEDIATE")
    try:
        if worker_id is not None and lease_epoch is not None:
            assert_live_lease(
                conn,
                run_id=run_id,
                stage=stage,
                worker_id=worker_id,
                lease_epoch=lease_epoch,
            )
        yield conn
    except BaseException:
        if owns_transaction:
            _rollback(conn)
        raise
    if owns_transaction:
        conn.execute("COMMIT")

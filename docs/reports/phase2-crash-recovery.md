# Phase 2 crash-recovery hardening — run report

Run: `phase2: harden crash recovery`
Date: 2026-10-21
Scope: the Phase 2 SQLite store, job queue, outbox and checkpoint writer
Risk: declared `medium`, floor `medium` (adapter). No auth, schema, payment, secret or infrastructure file was touched.

## What this run did

Every database test in this repo ran against `:memory:`. An in-memory database
dies with the process, so no test had ever watched a worker vanish, a lease
expire, a transaction tear in half, or a queue row outlive the process that
created it. The fencing code looked correct on paper and nothing had
challenged it.

This run wrote that missing suite and fixed what it broke. The claim it
supports:

```text
WORK -> INTERRUPTION -> PERSISTED STATE -> RECOVERY -> SAFE RESUME
```

## The boundary of the claim

`PRAGMA synchronous = NORMAL` is set in `db/connection.py`. In WAL mode SQLite
does not fsync on every commit, so a host power loss can discard recently
committed transactions.

**This run proves process-crash durability. It does not prove power-loss
durability, and nothing here may be read as claiming it.** A killed process
cannot tear a committed transaction and cannot leave a partial one visible.
That is a different guarantee, and it is the one the tests establish.

## Invariants and the test that holds each

All 21 cases live in `tests/test_db_crash_recovery.py` and pass. Every crash
test uses a file-backed database, because WAL and the `-wal` sidecar only exist
on disk. The interruption is a real killed process: the `crash_child` helper
spawns a child that ends in `os._exit(9)`, which skips every cleanup path.

| Invariant | Test |
|---|---|
| A committed transaction survives a process kill | `test_a_committed_transaction_survives_a_process_kill` |
| A transaction open during a process kill leaves no trace | `test_an_open_transaction_leaves_nothing_behind` |
| A job whose worker vanished is recoverable | `test_a_job_whose_worker_vanished_is_recoverable` |
| A worker whose lease expired cannot write | `test_a_worker_whose_lease_expired_cannot_write` |
| A superseded epoch cannot complete, fail, renew or write | `test_a_superseded_epoch_cannot_complete_fail_renew_or_write` |
| Two workers racing for one job produce one claim | `test_two_workers_racing_for_one_job_produce_one_claim` |
| A fenced transaction rolls back every write in it | `test_a_rollback_inside_a_fenced_transaction_discards_every_write` |
| A checkpoint survives a restart | `test_a_checkpoint_survives_a_restart` |
| A corrupt checkpoint does not stop recovery | `test_a_corrupt_checkpoint_does_not_stop_recovery` |
| An outbox task lands with the evidence or with neither | `test_an_outbox_task_lands_with_the_evidence_or_with_neither` |
| A replayed import creates no duplicate rows | `test_a_replayed_import_creates_no_duplicate_rows` |
| A truncated run directory is refused | `test_a_truncated_run_directory_is_refused` (5 cases) |
| The projection task rides the evidence's transaction | `test_the_store_rolls_the_projection_task_back_with_the_evidence` (2 cases) |
| A handler that raises after its own commit is retried without duplicating | `test_a_handler_that_raises_after_its_own_commit_is_retried_without_duplicating` |
| A projection abandoned with an expired lease converges to one row | `test_a_projection_task_abandoned_with_an_expired_lease_converges_to_one_row` |
| A crash child that hangs fails the test rather than hanging it | `test_a_crash_child_that_hangs_fails_the_test_instead_of_hanging_it` |

## The three fixes

**F1 — a truncated run directory is refused.** `Store.import_run` now compares
the manifest's declared record counts against the observed counts and raises
`StoreError` on any disagreement, before the import transaction opens. The
mapping comes from `cli.py`: `documents` and `candidates` and `calls` and
`rejected` each name their own file, and `accepted` is a count derived from
`candidates.jsonl`. Before this, a file truncated at a line boundary parsed
cleanly, hashed cleanly, and imported as authoritative.

**F2 — a corrupt checkpoint no longer stops recovery.** `checkpoints.py` gained
`load_valid_checkpoint`, which returns the newest row whose state parses, and
`list_checkpoints` gained `skip_invalid`. `load_checkpoint` stays strict and
still raises `CheckpointError`: a caller that asked for a specific version
still gets told the truth.

**F3 — the outbox has one writer.** `Store._enqueue_projection` calls
`outbox.enqueue_projection` instead of carrying its own INSERT. The two paths
used to agree only because both derived the task id from the same key with the
same hash basis; if they ever drifted, a replayed import would create a
second projection task for the same run.

## What the review round changed

The adversarial review found no blocking defect but found that the evidence
was weaker than the claim, and one real bug in F1.

F1 originally compared the manifest's `accepted` against `pain_points.jsonl`, a
file `import_run` never reads. The reviewer demonstrated both directions: with
that file deleted the count went unchecked, and with a stale extra line in it
a correct import was refused. The check now compares `accepted` against the
accepted-candidate count in `candidates.jsonl`, which is what the CLI writes
and what the import actually uses.

Four evidence gaps were closed as well. The truncated-run-directory test is
now parameterised over the whole mapping, so a one-character typo in any
mapping entry fails a test. The projection row is now pinned on
`entity_version`, `payload_json` and `max_attempts`. A new test kills
`Store.import_run` inside its import transaction, at two points, and shows
that the projection task and the evidence are absent together, which is the
invariant `store.py`'s own docstring claims. The two interruption kinds the
spec listed but no test simulated, a handler that raises after its own commit
and a projection abandoned with an expired lease, now have tests.

A second review round went further. It found that the new store test could
not fail for the reason it was written: the child died at the `COMMIT`, so the
evidence was never committed, and the test could not tell "the projection task
rides the evidence's transaction" from "the projection task is enqueued later".
Moving the enqueue past the commit left every test green. The test now kills
the child at the projection INSERT as well as at the COMMIT, and the review
confirmed in a scratch copy that the enqueue-after-commit mutation now fails
it. Two smaller findings came out of the same round: the F1 refusal message
said an artifact "holds N records" when the count for `accepted` is the number
of accepted candidates, which is the line an operator reads when a legitimate
run is refused, and the count guard carried a dead `None` branch left over
from the previous fix.

## Gate evidence

Full suite in this worktree, which other loop runs also share:

```
747 passed in 10.77s
```

The same change alone, in a clean worktree at base commit `2039d4b` plus only
this run's five files:

```
485 passed in 9.53s
```

That second number is the one that matters for this commit. 464 of those tests
are the pre-existing suite at base commit `2039d4b`, unchanged, and 21 are
this run's.

```
uv run pytest tests/test_db_crash_recovery.py
21 passed in 4.80s
```

## Changed files

| File | Change |
|---|---|
| `tests/test_db_crash_recovery.py` | new, 695 lines, 21 cases |
| `tests/conftest.py` | new, 229 lines: the six fixture builders moved verbatim from `test_db_store.py`, plus `open_store` and `crash_child` |
| `tests/test_db_store.py` | +14 / -199: the builders moved out, three assertions added, no assertion weakened |
| `src/product_hunter/db/store.py` | +57 / -28: F1 and F3 |
| `src/product_hunter/db/checkpoints.py` | +72 / -14: F2, plus a `LIMIT 1` restored on the strict load path |
| `docs/reasonix/specs/phase2-crash-recovery.md` | new, 209 lines |
| `docs/reasonix/plans/phase2-crash-recovery.md` | new, 163 lines |
| `docs/reasonix/prompts/phase2-crash-recovery-implement.md` | new, the implement node's brief |
| `docs/reports/phase2-crash-recovery.md` | this file |

## Not proven, and known limits

The queue is at-least-once. A crash between the handler's commit and
`complete_job` re-runs the handler, because those are two transactions. Closing
that window would mean changing the handler contract, which this run was told
not to do. The defence against duplicate authoritative mutations lives one level
down, in the deterministic ids of `Store`: replaying an import produces the
same ids and inserts no second row, which
`test_a_replayed_import_creates_no_duplicate_rows` shows end to end. A handler
that writes outside `Store` with non-deterministic ids can still double-apply.

`load_valid_checkpoint` and `skip_invalid` have no production caller yet, so a
corrupt checkpoint still stops `registry.run_log`. This is recorded as a
`shortcut:` comment in `checkpoints.py` and is a follow-up, not a fix.

`load_valid_checkpoint` is not re-exported from `product_hunter.db`. That
module is mid-change in a concurrent run, and committing it here would have
pulled another run's work into this commit. Callers reach into
`product_hunter.db.checkpoints`, which is where the test imports it from. Two
lines in `db/__init__.py` close the gap when that file settles.

`pain_points.jsonl` is not completeness-checked at import time, because the
import never reads it. A divergent sidecar is caught later, by the artifact
hash comparison on replay. Recorded as a `shortcut:` comment in `store.py`.

A refused import and a skipped checkpoint leave no queryable audit trail. The
codebase has no logging at all, and an audit table would need a schema
migration, which raises the risk floor. The CLI's error output is the only
observable surface today.

## Residual risk

Nothing here has been tested against a host power loss, a hardware fault, a
network filesystem, or a filesystem that loses the tail of a file. The last of
those is the actual trigger for the truncation that F1 now catches, so F1 is
tested against a simulated truncation rather than a real one.

`os._exit(9)` is self-inflicted, but it skips every cleanup path, which is the
property that matters to SQLite. No externally delivered `SIGKILL` was tested.

The crash helper runs a Python source string, so a value spliced into that
string would be a program nobody wrote. The helper's docstring states the
contract and every call site passes a literal. The child inherits the parent
environment, and the child's stderr is echoed into an assertion message when
the return code is wrong. A future test that prints the environment would leak
it into test output.

## Worktree note

Other loop runs share this worktree and were editing it throughout. Three of
them committed while this run was working, so the base moved from `205a4ba` to
`2039d4b`. The committed change is exactly the ten files listed above, staged
by explicit path. `db/worker.py`, `db/__init__.py`,
`db/migrations/0002_model_routing.sql`, `config/model_routing.yaml`,
`src/product_hunter/research/`, `src/product_hunter/eval/` and the `docs/`
tree restructure belong to those runs and are not part of this commit.

The base commit moving is why the isolated run above matters. It re-checks
this change against the tree as it stands now, with none of those other runs'
files in the picture.

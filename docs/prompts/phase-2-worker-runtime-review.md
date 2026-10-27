# Prompt — adversarial review of the worker runtime

Node: `loop-free-review-1`
Stage: 7 REVIEW
Rubric: `ponytail-review` (correct, safe, load, tested, fast, lean)

## What to review

The new package `src/product_hunter/runtime/` and its two test files, against
the base commit `205a4ba`:

- `src/product_hunter/runtime/__init__.py`
- `src/product_hunter/runtime/identity.py`
- `src/product_hunter/runtime/events.py`
- `src/product_hunter/runtime/lock.py`
- `src/product_hunter/runtime/worker.py`
- `src/product_hunter/runtime/__main__.py`
- `tests/test_runtime_worker.py`
- `tests/test_runtime_lock.py`

Read the spec the change was built against,
`docs/reasonix/specs/phase-2-worker-runtime.md`, and the handoff
`docs/reasonix/handoff/phase-2-worker-runtime-handoff.md`.

The change is not committed yet. Read the files in the working tree.

## The rules the change must hold

1. SQLite is the only authority. No second queue, no second database, no
   in-memory queue state, no chat state.
2. The database layer is read-only. Nothing under `src/product_hunter/db/**`
   or `db/migrations/**` may be changed, and the runtime must not reach
   around its public API.
3. A worker may only write a job outcome while it holds the lease, at the
   right epoch. A stale worker must not be able to complete or fail a job.
4. A lost lease must produce no further write for that job.
5. A handler failure is retried until the attempt ceiling, and a dead job
   stays dead.
6. An expired lease is recovered as work, never as a success.
7. The same idempotency key means one job, and a re-execution converges.
8. A stop request ends the claim loop and lets the running handler finish.
9. No handler registered means nothing is claimed.
10. No new dependency, standard library only.

## How to review

Try to prove the change wrong. Read the code, not only the diff, and read the
code it calls in `src/product_hunter/db/jobs.py` so you can judge the fence
rather than assume it.

Ask yourself, concretely:

- What input, ordering or timing makes a job get written twice, lost, or
  marked succeeded without a handler running?
- Can the heartbeat thread and the outcome write touch the same row at the
  same time? Trace the order in `run_once`.
- What happens when the handler raises `BaseException` that is not
  `Exception`, when the handler returns something that is not a dict, when the
  database is busy, when the lease expires between the handler finishing and
  the completion, when the process is killed between the claim and the
  completion?
- Does any test assert something weaker than the behaviour it names? A test
  that passes for the wrong reason is a finding.
- Is any test timing dependent, or dependent on the order pytest happens to
  run the files in?
- Is there code here that the database layer already provides, or that the
  specification does not ask for? Over engineering is a finding, and so is a
  missing test for non trivial logic.
- Does the documentation match the code? A promise the code does not keep is
  a finding.

## Rules

- You are the reviewer. You report. You do not edit, create, move or delete
  any file, and you do not change a test to make it pass.
- Do not propose a change to `src/product_hunter/db/**` or
  `db/migrations/**`.
- Do not repeat a finding the hardening pass already made and closed. The
  closed items were: the unmasked exception message (now masked by
  `_safe_error`), the watchdog exit without closing the connection (the
  connection cannot be closed from another thread, so the hard exit stays),
  and the missing `heartbeat_seconds` against `lease_seconds` check (now
  refused). Judge whether those closures are correct; do not reopen them
  without new evidence.
- A finding needs a name, the file and the line, why it bites, and the
  smallest fix. Rank them must fix, should fix, nice to have.
- Do not manufacture findings. If the change is sound, say so.

## Report back

Use this shape:

- `Verdict:` ship, or ship with fixes, or do not ship, with one line of
  reason.
- The ranked findings, each with file and line.
- `Lean: -N lines possible` with the number you can actually evidence, and
  which lines.
- What you checked and found sound, one line each.
- Anything you could not check, and why.

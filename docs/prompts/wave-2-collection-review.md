# Review prompt: Wave 2 source collection layer

You are the adversarial reviewer for Wave 2 Session 3 of Product Hunter.

## Where you work

- Worktree: `C:\Anubhav\Web Dev Projects\product-hunter\.worktrees\wave2-collection-engine`
- Branch: `wave2/collection-engine`
- Read every file. Edit nothing, create nothing, delete nothing, commit nothing.

## What to read

1. Load the `ponytail-review` skill and use its rubric.
2. `docs/reasonix/specs/wave-2-collection.md` — the design.
3. `docs/reasonix/plans/wave-2-collection.md` — the module contracts.
4. The diff: `git diff 3d7bc68` and the new files under
   `src/product_hunter/collection/`, `db/migrations/0003_collection.sql`,
   `tests/test_collection.py`, `tests/test_collection_security.py`,
   `tests/test_db_migrations.py`.
5. The code the new code depends on: `src/product_hunter/fetch.py`,
   `src/product_hunter/safety.py`, `src/product_hunter/sources.py`,
   `src/product_hunter/db/jobs.py`, `src/product_hunter/db/checkpoints.py`,
   `src/product_hunter/db/store.py`, `src/product_hunter/research/budget.py`.

## Your job

Try to prove the change wrong. The questions worth asking:

- Does the collection layer really have no way to reach the network except
  through the `Fetcher`? Look for a client, a transport, a session or a
  subprocess.
- Is the terms, allowed-use, domain-allowlist, rate-limit, SSRF and robots
  policy enforced once, in the shared path, or is any of it re-implemented
  where it can drift?
- Can the per-source request cap, the contract's per-run cap, the per-stage
  cap or the per-source cap be exceeded, including across a restart, through
  retries, through `robots.txt` fetches, or through redirect hops?
- Is the idempotency claim true? Collect the same target twice, restart
  mid-batch, replay a plan: does anything get fetched twice, charged twice
  or lost?
- Does the state survive a crash? Which write is the last one, and what
  happens if the process dies between two writes?
- Do the tests prove what the spec claims, or do they prove something
  weaker? A test that asserts the mock's behaviour instead of the layer's is
  a weak test.
- Is any of this unnecessary? A table, a field, a parameter or a method with
  one caller and no purpose is a cost. The `Lean:` line in your verdict must
  name the lines that could go, with evidence.

## The rubric

The six passes of `ponytail-review`, in order: correct, safe, load, tested,
fast, lean. Every finding is explained in plain English. Rank them must,
should, nice.

## The output

```
Verdict: ship | changes-requested
Lean: -N lines possible

Must
- file:line — what is wrong — what to do

Should
- ...

Nice
- ...

Checked and clean
- <what you checked and found sound>
```

A clean verdict is a valid verdict. Do not manufacture findings to look
thorough, and do not report taste. If you find nothing wrong, say so and
name what you checked.

For the `Lean:` line, count only lines you can point at. If nothing can go,
write `Lean: 0 lines possible`.

## The fix round after this review

Your first pass returned `changes-requested` with three must-fix findings,
five should-fix findings and two nice findings. Here is what was done.

1. **A job with no run got its own allowance.** The scope is now the unit
   the caps are counted in. `enqueue_collection_jobs` resolves it (scope,
   then the run id, then the plan's scope) and refuses a call with none of
   them; the payload carries it; the handler reads the job's run or the
   payload's scope and refuses a job with neither, with no per-job fallback.
2. **The handler's docstring claimed the wrong thing.** It now says the
   handler serves a target job, that the shipped `source_collection`
   schedule produces a cycle trigger whose targets come from discovery, that
   the collection layer does not choose a target, and that a job with
   neither a run nor a scope is refused. The refusal messages name what is
   missing.
3. **The second attempt overwrote the first.** `collection_items` has an
   `attempt_no` and its key is `(run_id, source_id, url, attempt_no)`. One
   row per attempt. The duplicate check reads the latest attempt. The health
   report counts the rows. The spec's section 5 was updated.
4. **The per-source check read a different scope than the charge.** The
   check passes `stage=STAGE` now, the stage the charge and the replay use.
5. **The minimum gap was not restored after a restart.** `RequestBudget`
   learned a wall-clock floor per source, `restore` takes it, `start_run`
   computes it from the run ledger's `last_request_at` plus the source's
   `min_interval_seconds`, and the budget waits it out.
6. **`due_sources` named uncollectable sources.** It is filtered through the
   same terms gate the planner uses.
7. **The idempotency-across-a-crash claim had no test.** There is one now: a
   fresh orchestrator over the same store returns `duplicate` and makes no
   second request.
8. **One condition, two reason codes.** The planner uses the shared path's
   `unsafe_url` now. One existing test expectation was updated to match; the
   assertion is unchanged in strength.
9. **Two truncate helpers, two limits.** One helper and one limit in
   `fetch.py`, imported by the collection layer. The limit is 1000 for both.
10. **The plan did not mention the fetch.py change.** It does now.

Judge the result, not the description. Look for anything the first pass
missed and for anything the fixes broke. Say plainly when a finding is
closed and when it is not.

## The second fix round after the second review

Your second pass said `ship` and left eight items open. Here is what was
done.

1. **The request ledger held the writing process's own total.** The ledger
   is now incremented by one per request, atomically, the way `add_fetch`
   does. `RequestBudget` grew an optional `stored_count` hook and checks the
   cap against the larger of its own count and the stored one, so two
   workers on one scope cannot both spend the whole allowance. The
   counter-publishing callback now names the source it was for.
2. **The handler and the enqueue disagreed about which scope wins.** The
   handler reads the payload's scope first, the same order the enqueue uses.
3. **The spec named a reason code the code never produces.** The spec says
   `unsafe_url` now.
4. **Dead code.** Deleted: the `idx_collection_items_target` index,
   `LADDER_ORDER` and its two export lines, `attempt_count` and everything
   that read it, `RequestBudget.counts()`, the test that counted
   `BudgetTracker` constructions, and the test that asserted the absence of
   two methods. The `crawler` entry in the ladder's uninstalled-rung table
   stays: that rung is the seam the spec names, and the refusal has to say
   which dependency is missing.

Judge the result once more. Say plainly what is closed and what is not, and
whether anything is still open that would stop a ship.

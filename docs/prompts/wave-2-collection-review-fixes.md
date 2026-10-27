# Review fix round: Wave 2 source collection layer

You are the implementation node for Wave 2 Session 3 of Product Hunter, in the
fix round after the adversarial review.

## Where you work

- Worktree: `C:\Anubhav\Web Dev Projects\product-hunter\.worktrees\wave2-collection-engine`
- Branch: `wave2/collection-engine`
- Run every command in that worktree.

The review said `changes-requested` with three must-fix findings and five
should-fix findings. All of them are below. Fix each one and leave a test
that fails without the fix.

## Must 1. A job with no run gets its own allowance, so the caps multiply

`make_collection_handler` scopes a job with no run by its own job id. The
budget ledger and the contract tracker are keyed by that scope, so every job
starts with a fresh allowance. Two `COLLECT` jobs with no run and a source
whose `max_requests_per_run` is 1 let two requests out, and the store holds
two `collection_run_state` rows.

The caps must be counted once per batch, not once per job.

- `enqueue_collection_jobs(conn, plan, *, run_id=None, scope=None)`: the
  scope defaults to the run id, and when there is neither a run nor a scope
  the call raises at enqueue time, so the caller finds out immediately.
  The job payload carries the scope.
- The handler reads the scope from `context.job.run_id` or the payload's
  scope. When it has neither, it refuses the job with a message that says
  what is missing. **There is no per-job fallback.**
- The orchestrator's `plan`, `collect` and `start_run` take the scope, and
  the ledger and the tracker are keyed by it.
- Say in the docstrings what the scope is: the unit in which the caps are
  counted. Every job of one batch shares one scope.

A test: two jobs with the same scope share one allowance, a job with neither
a run nor a scope is refused, and the caps are not multiplied.

## Must 2. The handler's docstring claims the wrong thing

The handler raises `ValueError("a collection job needs a source_id and a
url")` for a payload with no target, and the only producer of `COLLECT`
jobs in `src` is the `source_collection` schedule, whose payload is
`{"schedule": ..., "occurrence": ...}` with no source and no URL. Every
scheduled job fails.

Fix the docstring: it must say that this handler serves a target job, and
that the shipped `source_collection` schedule creates a cycle trigger whose
targets come from discovery, which is a different wave. The refusal message
must name what is missing.

Do not invent targets. The collection layer may not choose a URL on its own:
the registry declares domains, not paths, and discovery owns the target
choice. Do not add a URL field to the registry and do not touch
`config/sources.yaml`.

This behaviour is not a regression: before this wave there was no `COLLECT`
handler at all, so those jobs failed too.

## Must 3. The second attempt overwrites the first, so the failure history is lost

`CollectionState.save_item` upserts on `(run_id, source_id, url)`. A target
that failed with `network_error` and then succeeded leaves one row, and the
health report shows `attempts=1, failures={}`. The spec promises that every
attempt has a row with its outcome, reason and detail, and that the health
report counts the attempts and the failures.

- Give `collection_items` an `attempt_no` column and make the primary key
  `(run_id, source_id, url, attempt_no)`.
- `save_item` writes a new row per attempt, with the attempt number one
  above the last one for that target.
- The duplicate check reads the **latest** attempt for the target. A target
  whose latest attempt is terminal is not fetched again.
- The health report counts the rows, so the attempts and the failures are
  right.
- Update `docs/reasonix/specs/wave-2-collection.md` section 5: one row per
  attempt, the key is `(run_id, source_id, url, attempt_no)`, the target is
  the idempotency key and the latest attempt decides.

`db/migrations/0003_collection.sql` is new in this wave and has not shipped,
so change the migration itself. `tests/test_db_migrations.py` already
asserts the table names and the schema version, and neither changes.

A test: a target that fails and then succeeds leaves two rows, and the
health report shows the failure.

## Should 1. The per-source budget check reads a different scope than the charge

`CollectionPlanner.budget_refusal` checks `source_requests_per_source`
without `stage=STAGE`, while `_charge` and `_replay_tracker` both pass it.
With a per-stage limit on that dimension, both fetches went out and the
overrun was only logged. Pass the stage in the check too.

## Should 2. The minimum gap is not restored after a restart

The gap lives only in `RequestBudget._next_at`. `restore` puts back the
counters but not the gate, so a restarted process asks the source again
immediately. `collection_run_state.last_request_at` is written for exactly
this and nothing reads it.

- `RequestBudget` learns a wall-clock floor per source: the instant before
  which the source may not be asked. `restore` takes it.
- `CollectionOrchestrator.start_run` computes the floor from the run ledger:
  the source's `last_request_at` plus the source's `min_interval_seconds`.
- The budget waits out the floor before the request, the same way it waits
  out the in-process gap.

Mind the two clocks: `last_request_at` is wall time and `_next_at` is
monotonic, so the restored floor is a wall-clock instant and the wait is
computed against the wall clock.

A test: after a restart, the next request to that source waits out the
remaining gap.

## Should 3. `due_sources` names sources that may not be collected at all

It walks `registry.list()` and reports any source whose gap has elapsed, so
a disabled source with unknown terms and no domains comes back as
collectable. The spec calls this "who may be collected from right now". Filter
it through the same terms gate the planner uses.

## Should 4. The idempotency claim across a crash has no test

The spec says the idempotency holds across a crash, because the item row is
committed before the next target starts. Every duplicate test reuses the same
orchestrator. Add one that restarts between the fetch and the replay: a fresh
orchestrator over the same store returns `duplicate` and makes no second
request.

## Should 5. One condition, two reason codes

The planner records `not_allowed` for a URL the shared path would refuse with
`unsafe_url`. Only `unsafe_url` is terminal, so a target the planner refused
is re-checked and re-recorded on every plan.

Use the shared path's code, `unsafe_url`, in the planner as well, so the two
paths speak the same language and the terminal set already covers it. The
detail still says what was wrong.

## Nice 1. The truncate helper exists twice, with two limits

`fetch.py` has one and the collection layer has another, at 1000 and 300
characters. One helper, one limit. Put it in `fetch.py` and have the
collection layer import it.

## Nice 2. The plan does not mention the fetch.py change

`docs/reasonix/plans/wave-2-collection.md` says `fetch.py` only gains
`RequestBudget`. It also gains the bounded rejection detail. Say so in the
plan.

## Rules that still bind you

- The Fetcher is the only path to the network. Do not re-implement terms,
  allowed use, the domain allowlist, rate limits, SSRF protection or robots.
- `tests/test_fetch.py` must stay byte-for-byte unchanged. Do not weaken,
  skip or edit any test to make it pass.
- No new dependency, no CLI command, no clustering, scoring, Obsidian or
  Phase 7 work.
- Fetched content is data: no function may turn content into a command, a
  URL or a tool argument.
- Do not touch `config/sources.yaml`, `src/product_hunter/sources.py`,
  `src/product_hunter/db/scheduler.py` or `src/product_hunter/safety.py`.
  If a fix needs one of them, stop and say so instead of editing it.
- Stay inside the declared radius: `src/product_hunter/fetch.py`,
  `src/product_hunter/collection/*.py`,
  `db/migrations/0003_collection.sql`, `tests/test_collection.py`,
  `tests/test_collection_security.py`, `tests/test_db_migrations.py`, and the
  `docs/reasonix` paths already listed.
- Do not commit.

## Acceptance

```
uv run pytest
```

must be green, with the new tests present and the old ones unchanged.

When you are done, report: the files changed with line counts, the verbatim
gate line, one line per finding saying how it was handled and which test
covers it, and anything you could not check.

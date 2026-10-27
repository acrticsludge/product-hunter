# Review fix round 2: Wave 2 source collection layer

You are the implementation node for Wave 2 Session 3 of Product Hunter, in the
second fix round after the adversarial review.

## Where you work

- Worktree: `C:\Anubhav\Web Dev Projects\product-hunter\.worktrees\wave2-collection-engine`
- Branch: `wave2/collection-engine`
- Run every command in that worktree.

The second review said `ship` and left eight items open. Two are correctness
and six are dead code. All of them are below.

## 1. The request ledger holds the writing process's own total

`CollectionOrchestrator._persist_counts` writes the process's own counter
total as the ledger's value, and the difference is computed in Python. Two
workers on one scope each spend the full allowance, and the ledger then
records fewer requests than were made: with a cap of 1 and two workers, two
requests went out and the ledger said `request_count` 1.

Two changes, both in the collection layer and the budget:

- The ledger is incremented by one per request, atomically, the way
  `add_fetch` already does. A worker that writes its own total overwrites
  what another worker spent.
- The cap is checked against the stored count, not only against the
  worker's own memory, so two workers on one scope cannot both spend the
  whole allowance.

`RequestBudget` needs one more optional hook: a callable that returns the
stored request count for a source. The budget takes the larger of its own
count and the stored count before it checks the cap. The existing behaviour
for a budget with no such hook must not change, and `tests/test_fetch.py`
must stay byte-for-byte unchanged.

The hook that publishes the counter to the ledger needs to say which source
the request was for, so the ledger can add one to that source. Change the
shape of that callback; it is new in this wave, so nothing else depends on
it.

A test: two workers on one scope, a cap of 1, and only one request goes out.

## 2. The handler and the enqueue disagree about which scope wins

`enqueue_collection_jobs` takes the explicit scope first and the run id
second. The handler takes the run id first and the payload's scope second.
A job enqueued with `run_id="run-1"` and `scope="batch-1"` is keyed as
`batch-1` at enqueue and recorded under `run-1` when it runs.

Read the payload's scope first in the handler, so both paths agree.

A test: a job enqueued with a run id and a scope is recorded under the
scope.

## 3. The spec names a reason code the code never produces

`docs/reasonix/specs/wave-2-collection.md` still says `not_allowed` where the
planner now uses the shared path's `unsafe_url`. Change the spec.

## 4. Dead code, to delete

- `db/migrations/0003_collection.sql`: the `idx_collection_items_target` index
  repeats the primary key's own columns, so every attempt row is written to
  two identical indexes. Drop the index. The primary key already indexes
  `(run_id, source_id, url, attempt_no)`.
- `LADDER_ORDER` in `ladder.py` and its two export lines in `__init__.py`:
  nothing reads it, and a constant that nothing reads can go stale when a
  rung is added. The order of preference stays in the module docstring and
  in the spec.
- `attempt_count`: the column in `collection_run_state`, the field on
  `RunState`, the `add_attempt` method and the mapping that reads it. The
  checkpoint uses the target as its step, so the counter has no reader. Drop
  it, and drop the test that only asserts it exists.
- `RequestBudget.counts()`: no caller. Drop it.
- The test that counts `BudgetTracker` constructions: it asserts an internal
  detail. The behaviour it names, that one scope's spend is not charged to
  the next, already has its own test. Drop it.
- The test that asserts the absence of two methods: that is a lint, not a
  behaviour. Drop it.

Keep the `crawler` entry in the ladder's uninstalled-rung table. The rung is
the seam the spec names for the local crawler, and the refusal has to say
which dependency is missing.

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
gate line, one line per item saying how it was handled and which test covers
it, and anything you could not check.

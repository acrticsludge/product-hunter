# Implement prompt: Wave 2 source collection layer

You are the implementation node for Wave 2 Session 3 of Product Hunter.

## Where you work

- Worktree: `C:\Anubhav\Web Dev Projects\product-hunter\.worktrees\wave2-collection-engine`
- Branch: `wave2/collection-engine`
- Run every command in that worktree. Do not touch the parent checkout.

## Read first, in this order

1. `docs/reasonix/specs/wave-2-collection.md` — the design.
2. `docs/reasonix/plans/wave-2-collection.md` — the module contracts, the
   schema, the outcome vocabulary, the test list.
3. `docs/reasonix/handoffs/wave-2-collection-handoff.md` — the repository map
   and the rules that bind the work.
4. `src/product_hunter/fetch.py`, `src/product_hunter/safety.py`,
   `src/product_hunter/sources.py`, `src/product_hunter/raw_store.py`.
5. `src/product_hunter/db/store.py` (the `Store` facade),
   `src/product_hunter/db/checkpoints.py`, `src/product_hunter/db/jobs.py`,
   `src/product_hunter/db/worker.py`.
6. `src/product_hunter/research/budget.py`,
   `src/product_hunter/research/contract.py`.
7. `tests/test_fetch.py` — the house test style.

## What to build

Exactly the files the plan names, and nothing else:

| file | change |
|---|---|
| `src/product_hunter/fetch.py` | add `RequestBudget`; `Fetcher` takes one and delegates to it |
| `src/product_hunter/collection/__init__.py` | new, the public surface |
| `src/product_hunter/collection/state.py` | new, the durable state and the health report |
| `src/product_hunter/collection/ladder.py` | new, the collector interface and the ladder |
| `src/product_hunter/collection/orchestrator.py` | new, planning, execution, checkpoints |
| `db/migrations/0003_collection.sql` | new, the three tables |
| `tests/test_collection.py` | new |
| `tests/test_collection_security.py` | new |

Do not add a dependency. Do not add a CLI command. Do not build clustering,
scoring, opportunity research, Obsidian, or any Phase 7 integration.

## The rules that matter most

- The shared fetch path (`product_hunter.fetch.Fetcher`) is the only way to
  the network. The collection layer holds no client it can use for anything
  but the `Fetcher`. Terms, allowed use, the domain allowlist, rate limits,
  SSRF protection and robots are enforced there, and the collection layer
  must not re-implement any of them.
- The per-source request counter and the interval gate move out of
  `Fetcher._rate` into the new `RequestBudget` class. Keep the order the old
  `_acquire` had, and keep the `Fetcher`'s existing `clock` and `sleeper`
  defaults, so a `Fetcher` built without a budget behaves exactly as before.
  The existing tests in `tests/test_fetch.py` must still pass unchanged.
- The contract's per-run and per-source fetch caps use
  `research.budget.BudgetTracker` with the shipped
  `config/research_contract.yaml`. Rebuild the tracker at start-up by
  replaying the run's recorded counters through `record()`. Do not write a
  second budget mechanism.
- The collection layer writes the `fetches` row through the existing
  `Store.import_fetch`, and it syncs the registry with `Store.sync_registry`
  so the `sources` row exists. Do not re-implement that insert.
- Fetched content is data. The collection outcome carries hashes, paths,
  status codes and headers. It never carries page text, and no function in
  the layer turns content into a command, a URL or a tool argument.
- The collection layer holds no research reasoning. It fetches what it is
  told to fetch and records what came back.
- Write one small test for every non-trivial branch. The plan lists the
  behaviour tests and the security tests. Cover all of them.
- Never change a test to make it pass.

## Fix round, after the orchestrator's read of the diff

Five defects. Fix each one and leave a test that fails without the fix.

1. **The durable budget ledger under-counts (must fix).**
   `CollectionOrchestrator._charge` charges the contract's tracker on every
   attempt, but `CollectionState.add_fetch` only runs when the capture
   succeeded. `_replay_tracker` rebuilds the tracker from `fetch_count`, so a
   run that restarts after a failed fetch gets its failed attempt back as
   budget. Count every attempt as a fetch: a failed attempt is a fetch the
   run spent, and the ledger has to say so. A skip is not a fetch.

2. **The duplicate check runs after the budget check.**
   `CollectionPlanner._refusal` checks the recorded item before the budget;
   `CollectionOrchestrator.collect` does the opposite. A target that was
   already collected costs nothing, so it must be recognised before the
   budget is consulted. After the fix, collecting a target that was already
   collected returns the `duplicate` outcome even when the budget is spent.

3. **The handler refuses a job with no run.**
   `make_collection_handler` raises when `context.job.run_id` is empty, but
   the shipped `source_collection` schedule creates jobs with no run, so every
   scheduled collection job would fail. Give the job its own scope when it
   has no run, and say so in the docstring.

4. **Nothing creates the fetch jobs.**
   The mission asks for fetch jobs, and only the handler exists. Add a
   function that enqueues one `COLLECT` job per target, with an idempotency
   key derived from the run, the source and the URL, so the same plan
   enqueued twice produces the same jobs. Reuse `db.jobs.enqueue_job`.

5. **Small cleanups.**
   `CollectionOrchestrator.__init__` builds the `BudgetTracker` twice. And
   `CollectionState.fetch_count` and `CollectionState.items_for_run` have no
   caller: delete them, or keep them only if a test needs them.

## Acceptance

```
uv run pytest
```

must be green, with the new tests present and the old ones unchanged.

When the suite is green, report:

- the files you changed, with the line counts,
- the gate line, verbatim,
- anything you skipped and why,
- anything you could not check.

Do not commit. The orchestrator commits after the gates pass.

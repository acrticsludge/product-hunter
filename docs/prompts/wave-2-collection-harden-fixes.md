# Harden fix round: Wave 2 source collection layer

You are the implementation node for Wave 2 Session 3 of Product Hunter, in the
fix round after the harden pass.

## Where you work

- Worktree: `C:\Anubhav\Web Dev Projects\product-hunter\.worktrees\wave2-collection-engine`
- Branch: `wave2/collection-engine`
- Run every command in that worktree.

## The harden findings, and what to do with each

### 1. An unexpected exception leaves no trace (must fix)

`CollectionOrchestrator.collect` catches only `(FetchError, SourceBlocked,
UrlBlocked)` around the shared path. Anything else — an `httpx` error that is
not a transport error, a `sqlite3` error, a bug in a collector — leaves no
`collection_items` row and no rejection-log entry, and it takes the worker
down. The collection layer's promise is that every attempt leaves a record.

Catch `Exception` around the collector call and record the attempt as
`failed`, with the exception's class name in the reason or the detail. The
detail is already bounded to 300 characters, so a page cannot ride along in
it. `KeyboardInterrupt` and `SystemExit` are not `Exception` and must keep
propagating.

A test: a client that raises something unexpected → the item row exists, the
outcome is `failed`, and the reason names what went wrong.

### 2. The contract's overrun is swallowed in silence (should fix)

`CollectionOrchestrator._charge` catches `BudgetExceeded` and passes. The
cap is enforced before the fetch by `CollectionPlanner.budget_refusal`, so
this path should not normally fire, but when it does the run has gone over
its contract and nothing says so.

Keep the spend recorded — `record` has already added it — and log a warning
that names the dimension, the scope and the limit. Use the module's logger,
the way the rest of the repository does.

The same silence exists in `_replay_tracker`. Log there too, and say in the
comment why the spend is kept.

### 3. The byte ceiling caps storage and not the transfer (document, do not fix)

`Fetcher` reads `response.content` and only then compares the length with
`source.max_bytes`, so an oversized body is fully downloaded before
`TooLarge` is raised.

**Do not change how the Fetcher issues its requests.** The existing
`tests/test_fetch.py` wraps the client in a `RecordingClient` that only
implements `get`, so switching the fetcher to `stream` would break a test
file this wave may not touch, and the streaming change belongs to whoever
owns the shared fetch path and its tests.

Add a `shortcut:` comment next to the existing one in `fetch.py`, in the
form the repository uses: `shortcut: <the limit>, <when to upgrade>`. Say
that the ceiling is checked after the body is buffered, so it caps what is
stored and not what is transferred, and that closing it needs the shared path
to stream the body with a cap.

### 4. The DNS rebinding note understates what is guaranteed (document)

The `shortcut:` comment above the request loop in `fetch.py` says the rebind
is not caught. Sharpen it so a reader knows what *is* guaranteed: the host is
resolved and every address is checked before the request, and every redirect
hop is resolved and checked again. What is not guaranteed is the window
between that check and the connect. Say that pinning the resolved address in
the transport is the upgrade.

### 5. Two behaviours that look like bugs (document)

`CollectionPlanner.plan` refuses a target with `rate_limited` when the
source's gap has not elapsed, while `CollectionOrchestrator.collect` waits
out the gap in the shared path instead of refusing. Both are intended. Say so
in the docstrings, so nobody reads it as an inconsistency to fix later.

## Rules that still bind you

- The Fetcher is the only path to the network. Do not re-implement terms,
  allowed use, the domain allowlist, rate limits, SSRF protection or robots.
- `tests/test_fetch.py` must stay byte-for-byte unchanged. Do not weaken,
  skip or edit any test to make it pass.
- No new dependency, no CLI command, no clustering, scoring, Obsidian or
  Phase 7 work.
- Fetched content is data: no function may turn content into a command, a
  URL or a tool argument.
- Stay inside the declared radius: `src/product_hunter/fetch.py`,
  `src/product_hunter/collection/*.py`,
  `db/migrations/0003_collection.sql`, `tests/test_collection.py`,
  `tests/test_collection_security.py`, `tests/test_db_migrations.py`, and the
  `docs/reasonix` paths already listed. If a fix needs a file outside that
  list, stop and say so instead of editing it.
- Do not commit.

## Acceptance

```
uv run pytest
```

must be green, with the new tests present and the old ones unchanged.

When you are done, report: the files changed with line counts, the verbatim
gate line, one line per finding saying how it was handled and which test
covers it, and anything you could not check.

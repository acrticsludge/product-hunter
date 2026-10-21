# Harden fix round 2: Wave 2 source collection layer

You are the implementation node for Wave 2 Session 3 of Product Hunter, in the
second fix round after the harden pass.

## Where you work

- Worktree: `C:\Anubhav\Web Dev Projects\product-hunter\.worktrees\wave2-collection-engine`
- Branch: `wave2/collection-engine`
- Run every command in that worktree.

## The re-harden findings, and what to do with each

### 1. A checkpoint failure erases the attempt record (should fix)

`CollectionOrchestrator._record` calls `_write_checkpoint` inside the
transaction. `save_checkpoint` can raise, and then the whole transaction rolls
back: the item row, the run ledger and the source state all disappear, and
the attempt leaves no trace. The attempt is the fact; the checkpoint is
progress metadata.

Write the checkpoint after the transaction has committed, and log a
checkpoint failure instead of letting it roll the attempt back or fail a
collection that already happened. The attempt record must survive a broken
checkpoint.

A test: make the checkpoint write fail, then check that the item row and the
ledger are still there.

### 2. The rejection log's detail is unbounded (should fix)

`Fetcher._reject` writes `detail=str(exc)` with no bound, while the
collection layer bounds its own detail to 300 characters. The exception
messages are short today, but the log is the one place where an unbounded
string from an unexpected failure would land.

Apply the same kind of bound to the rejection log's detail in `fetch.py`.
Keep the bound generous enough that a real reason is not truncated into
meaninglessness.

### 3. An unavailable collector is retried forever (nice)

`TERMINAL_REASONS` does not hold `collector_unavailable`, so a target whose
source needs a rung that is not installed is attempted again on every pass.
The rung will not be installed during the run, so the answer cannot change.

Add `collector_unavailable` to `TERMINAL_REASONS`.

While you are there: `_outcome_from_item` builds the `duplicate` outcome with
an empty `reason_code` and an empty `detail`, so the reason a target was
recorded is lost from the returned outcome. Carry the item's own reason and
detail through. The item row already keeps them; the returned outcome should
not drop them.

### 4. The replay tracker and the live tracker disagree on the stage (nice)

`_charge` records both dimensions with `stage=STAGE`. `_replay_tracker`
replays `source_requests_per_source` with `stage=None`. The contract has no
per-stage limit for that dimension, so nothing breaks today, but the two
paths should read the same. Use the same stage in both.

### 5. The DNS rebinding window is accepted, not fixed (decision, no code)

The re-harden rated the rebinding window between the admission check and the
connect as a must. This wave does not fix it, and the decision is recorded
here so nobody re-opens it by accident.

Closing it means pinning the resolved address in the transport, so httpx
connects to the address that was checked instead of resolving again. That
changes how the shared fetch path issues its requests, and `tests/test_fetch.py`
wraps the client in a `RecordingClient` that only implements `get`; that test
file is out of this wave's reach, and the change would have to land with the
tests that own the shared path. A wrong pinning implementation is worse than
the window it closes, because it touches TLS and redirect resolution.

The guarantee that is in place stays in place: the host is resolved and every
address is checked before the request, and every redirect hop is resolved and
checked again. The `shortcut:` comment in `fetch.py` says exactly that, and
names the pinning as the upgrade.

Do not change the transport. Do not change how the Fetcher issues its
requests.

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

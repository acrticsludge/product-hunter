# Review fix round 3: Wave 2 source collection layer

You are the implementation node for Wave 2 Session 3 of Product Hunter, in the
last fix round after the third review.

## Where you work

- Worktree: `C:\Anubhav\Web Dev Projects\product-hunter\.worktrees\wave2-collection-engine`
- Branch: `wave2/collection-engine`
- Run every command in that worktree.

The third review said `ship`, with `Lean: 0 lines possible`, and left two
items it described as non-blocking. Close both.

## 1. A request the per-source cap refused is charged to the contract as a fetch

When the shared path refuses a request because the source's per-run request
cap is spent, no request went out, but the attempt is charged to the
contract's `fetch_requests` and to the run's fetch counter. One HTTP call
left the run with `fetch_count` 2.

The contract's fetch budget pays for fetches, not for refusals. A refusal
that stopped the request before it went out must not be charged as a fetch.

- The attempt is still recorded: the item row keeps the outcome and the
  reason, so the refusal stays visible.
- The contract tracker and the run's fetch counter are not charged for it.

Note that the request counter is already right: the budget refuses before it
counts the request. Do not change that.

A test: a source whose per-run request cap is spent shows one fetch in the
run's ledger, and the refused attempt is still in the item rows with its
reason.

## 2. The cap check and the increment are not one transaction

`RequestBudget` reads the stored count, checks the cap against it and then
writes the new count. Two workers that read at the same instant can both pass
and spend one request more than the cap. The overshoot is bounded by the
number of workers on the scope, and it is in the politeness cap rather than
in a policy gate.

Do not restructure the budget around a transaction. Say the limit in the
code, in the `shortcut:` form the repository uses: the read and the
increment are not one transaction, so N workers on one scope can overshoot
the cap by at most N-1 requests, and the fix is a transactional reserve in
the store.

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

must be green.

When you are done, report: the files changed with line counts, the verbatim
gate line, one line per item saying how it was handled and which test covers
it, and anything you could not check.

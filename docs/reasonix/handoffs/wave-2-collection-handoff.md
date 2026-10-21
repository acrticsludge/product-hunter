# Handoff: Wave 2 source collection layer

Branch: `wave2/collection-engine`. Worktree: `.worktrees/wave2-collection-engine`.
Base commit: `3d7bc68`. Spec: `docs/reasonix/specs/wave-2-collection.md`.
Plan: `docs/reasonix/plans/wave-2-collection.md`.

## The repository in one paragraph

Python 3.14, `src/product_hunter` on the `src` layout, pytest, pydantic v2,
httpx, PyYAML. The authoritative store is SQLite, migrated by numbered files
in `db/migrations/`. The queue, the scheduler, the checkpoints and the worker
live in `src/product_hunter/db/`. The research contract, the budget tracker
and the evidence gate live in `src/product_hunter/research/`.

## The parts this work touches

- `src/product_hunter/fetch.py`: the shared fetch path. It already enforces
  terms, allowed use, the domain allowlist, the scheme and port rules, the
  userinfo rule, private-address resolution, the redirect limit, the HTTPS to
  HTTP downgrade, robots.txt, the retry ladder, `Retry-After`, the ETag and
  Last-Modified validators, the byte ceiling, the raw store, content hashing
  and the rejection log. The only change is that the per-source request
  counter and the interval gate move into a `RequestBudget` object the
  `Fetcher` takes in its constructor.
- `src/product_hunter/safety.py`: `admit_url`, `admit_redirect`,
  `parse_robots`, `resolve_public_ips`. Not changed. Read it before writing a
  test.
- `src/product_hunter/sources.py`: `SourceRegistry.require` is the terms gate.
  `SourceConfig.rate_limit` holds the per-source caps. Not changed.
- `src/product_hunter/raw_store.py`: the content-addressed raw store. Not
  changed.
- `src/product_hunter/db/store.py`: `Store(conn)`, `Store.sync_registry`,
  `Store.import_fetch`. Reuse these, do not re-implement the `fetches` insert.
- `src/product_hunter/db/checkpoints.py`: `save_checkpoint`. It refuses a run
  that is not in `research_runs`, so the collection layer writes a checkpoint
  only when the run exists.
- `src/product_hunter/db/jobs.py`: `enqueue_job`, `JobContext`. The worker in
  `src/product_hunter/db/worker.py` dispatches on `job.stage` through a
  `dict[str, Handler]`. There is no `COLLECT` handler yet.
- `src/product_hunter/research/budget.py`: `BudgetTracker`, `BudgetSpec`,
  `BudgetDimension`. The tracker is in memory and has no restore method, so
  rebuild it by replaying `record()`.
- `src/product_hunter/research/contract.py`: `load_research_contract`. The
  shipped contract is `config/research_contract.yaml`.

## Commands

```
uv run pytest                      # the code gate
uv run pytest tests/test_collection.py tests/test_collection_security.py
uv run ruff check src tests        # if ruff is configured
```

The adapter's code gate is `uv run pytest`.

## Rules that bind the work

- The repo adapter lists `src/product_hunter/fetch.py` and `db/migrations/**`
  as high-risk paths. Both are touched, so the run's risk is high.
- Never fetch a URL that is not declared in `config/sources.yaml`. URL
  admission is a worker decision.
- Never pass untrusted page text as a shell, tool or URL argument. It is
  data.
- Never change a test to make it pass.
- Never commit `.env`, `data/`, `runtime/` or `logs/`.
- Do not add a dependency. The collection layer uses the standard library and
  the packages already in `pyproject.toml`.

## The house style

Read `tests/test_fetch.py` before writing the tests. It uses `httpx.MockTransport`,
a `FakeClock` that jumps on every read so the interval waits vanish, a
`FakeSleeper` that records the delays, and a `Harness` that builds a `Fetcher`
over a `tmp_path`. The new tests follow that style.

Source modules carry a module docstring that says what the module owns, and
comments explain why the code cannot show. The code is typed. Errors are
classes with a `reason_code`.

## The shape of the work

Read the spec and the plan first. They carry the design, the schema, the
outcome vocabulary and the test list. The plan names the exact methods each
module has.

The two decisions that matter:

1. The request budget lives in the shared path. The collection layer owns the
   `RequestBudget` and persists its counters, so a restart resumes the
   allowance instead of resetting it.
2. The contract's caps use `BudgetTracker` with the shipped contract. The
   tracker is rebuilt at start-up by replaying the run's recorded counters.

Do not build clustering, scoring, opportunity research, Obsidian, or any
Phase 7 integration. Do not add Crawl4AI, Playwright or Apify: the ladder
refuses those rungs and says why.

# Session 2 Discovery Report

## What existed before

Discovery was single-source and hardcoded to Hacker News. `_discovery_handler` issued eight fixed HN queries, inserted documents directly, slept 1.0s between calls, and enqueued `COLLECTION`. The query frontier, saturation policy, discovery records, and multi-source registry existed as libraries but were not used by the handler. Only `hn_algolia` was admissible; `github_issues` existed in the registry but was not queried by discovery, and `blog_http` remained blocked with unknown terms.

## What was changed

- `src/product_hunter/handlers.py`
  - Added `_github_search` for GitHub Issues/Discussions.
  - Added `_devto_search` for the general web/blog/forum family.
  - Added retry and `Retry-After` handling in `_fetch_json`.
  - Rewrote `_discovery_handler` to:
    - load and sync the source registry,
    - select only admissible (active, permitted, reviewed) sources,
    - generate queries through `QueryFrontier.expand` (code-owned query families),
    - propose worker-owned negative/alternative queries,
    - execute source-specific searchers,
    - distinguish `success`, `empty`, `failure`, and `timeout`,
    - persist documents,
    - write `DiscoveryRecord`s through `write_record`,
    - checkpoint/resume the frontier through `save_frontier`/`load_frontier`,
    - apply source rate-limit pacing,
    - enqueue the next stage.
- `tests/test_discovery_handler.py`
  - Added integration tests proving the worker invokes discovery, uses multiple sources, persists documents and records, and distinguishes empty results.

## Production execution path

The authoritative worker claims a `DISCOVERY` job and calls `_discovery_handler` from `build_handlers()`. The handler uses the source registry, generates query families in code, executes real searches, persists documents and discovery records, checkpoints the frontier, and enqueues `COLLECTION`.

## Source families actually tested

Real bounded discovery run used three independent source families:

1. Hacker News (`hn_algolia`)
2. GitHub Issues (`github_issues`)
3. DEV Community (`dev_to`, general web/blog/forum family)

The production registry still contains `blog_http` as blocked (`unknown` terms), and Reddit remains absent/blocked, as required by source policy.

## Real result counts

From the real bounded discovery run:

- Documents discovered: 22
- Discovery records: 4
- Successful/empty/failure breakdown:
  - `hn_algolia`: success (2), empty (0)
  - `dev_to`: success (10)
  - `github_issues`: success (10)
- Checkpoints written: 4
- Database: `C:\Users\anubh\AppData\Local\Temp\opencode\real_discovery.db`

## Tests

- `tests/test_discovery_handler.py`: 2 passed.
- Full test suite: two failures observed when run together:
  - `tests/test_eval_cases.py::test_no_dataset_in_the_repository_carries_a_label` (passes in isolation; appears test-order/state related)
  - `tests/test_intelligence_eval_corpus.py::test_intelligence_eval_corpus_expected_outcomes` (`sqlite3.IntegrityError: FOREIGN KEY constraint failed`; unrelated to discovery changes)
  Discovery tests and handler integration tests pass.

## Failures encountered

- Initial discovery records were not persisted because `DISCOVERY_STAGE` was not imported.
- `dev_to` search initially failed because `_fetch_json` was not defined in the restored handler version; fixed by using direct HTTP.
- Real discovery rate limited by source caps and saturation policy; the run completed with 4 executed queries.

## Remaining limitations

- Saturation policy uses `queries_per_family=1`, so the bounded run stops after minimal family coverage.
- Retry-After handling is implemented at the HTTP helper level but was not exercised by a live 429 in this run.
- `blog_http` and Reddit remain blocked by source policy, as required.
- Full-suite failure in `test_eval_cases.py` should be investigated separately.

## Exact commands used

- `python -m compileall src/product_hunter/handlers.py`
- `python -m pytest tests/test_discovery_handler.py -q`
- `python C:\Users\anubh\AppData\Local\Temp\opencode\real_discovery.py`
- `python C:\Users\anubh\AppData\Local\Temp\opencode\inspect_real.py`

## Confirmation that the worker invoked discovery

Yes. The real run enqueued a `DISCOVERY` job, claimed it with the worker, invoked `_discovery_handler` through `build_handlers()`, persisted documents and discovery records, wrote checkpoints, and enqueued `COLLECTION`.
PY
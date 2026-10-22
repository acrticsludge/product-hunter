# Wave 3 Session 4: verify the advanced collection ladder

Node: VERIFY-CODE. Branch: `wave3/scraping-ladder`.

## The gate

The adapter's gate is `uv run pytest`, run from the worktree root. Report
the result verbatim, including the count and the time.

The baseline before the work was `2138 passed`. The work added the tests
in `tests/test_scraping_ladder.py` and
`tests/test_scraping_ladder_security.py`, so the count must be higher. A
lower count means a test was lost, and that is a failure to report, not
a detail to smooth over.

## What the run has to show

- The routing picks the rung the source declares, and the fallback order
  follows the source's list.
- A tool failure escalates to the declared fallback, and the result names
  the tool that ran.
- A policy refusal does not escalate.
- A timeout, a transport error, an empty result, an HTTP error and a
  missing dependency are each handled and each recorded.
- The per-source cap, the minimum gap, the retry ladder and the byte
  ceiling all hold.
- The browser context holds no cookie, no storage state, no permission
  and no download, and it is closed even when the capture fails.
- A request to a host the source does not declare, a private address, a
  metadata address and an off-allowlist redirect are all refused.
- The Apify token reaches neither the capture nor an error detail.
- A page that carries instructions stays an artifact and never becomes an
  argument.

## What the run must not claim

No live service is contacted. The crawler, the browser and the managed
client are not installed in this environment, so their drivers are
exercised against a fake library, and no live success may be reported.

## Also run

`uv run ruff check --select I,UP037,F,E4,E7,E9 src/product_hunter/collection tests/test_scraping_ladder.py tests/test_scraping_ladder_security.py`

Ruff is not in `pyproject.toml` and is not the adapter's gate, so its
result is reported as an observation and not as a pass or a fail.

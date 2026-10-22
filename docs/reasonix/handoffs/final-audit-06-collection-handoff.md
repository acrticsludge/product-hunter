# Handoff — final audit probe 06

RUN_ID: final-audit-06
Date: 2026-10-22
Mode: read-only. No source file was modified.

## Worktrees (discovered, not assumed)

`git worktree list` and `git branch -a` on
`C:\Anubhav\Web Dev Projects\product-hunter` returned eight Wave 3 branches
across three parent directories. Two worktrees sit outside the repository
directory entirely.

| Branch | Worktree path | HEAD |
|---|---|---|
| `wave3/scraping-ladder` | `C:\Anubhav\Web Dev Projects\product-hunter-wt\wave3-scraping-ladder` | 5f66d46 |
| `wave3/source-expansion` | `C:\Anubhav\Web Dev Projects\product-hunter-w3c` | a9540ee |
| `wave3/autonomous-operation` | `C:\Anubhav\Web Dev Projects\product-hunter-w3a` | 1efc328 |
| `wave3/duckdb-analytics` | `C:\Anubhav\Web Dev Projects\product-hunter-wt\wave3-analytics` | 3474e58 |
| `wave3/observability-evaluation` | `C:\Anubhav\Web Dev Projects\product-hunter-wt\wave3-observability` | 317e7f2 |
| `wave3/opencode-control-plane` | `C:\Anubhav\Web Dev Projects\product-hunter\.worktrees\wave3-opencode-control-plane` | 5e45c63 |
| `wave3/qdrant-semantic` | `C:\Users\anubh\AppData\Local\Temp\opencode\wt-wave3-qdrant` | 357dd5e |
| `wave3/subq-integration` | `C:\Users\anubh\.local\share\opencode\worktree\44767c\wave3\subq-integration` | 172a74f |

Main is `00a6ca3` on `main`.

## Which branches touch the collection surface

Two, out of eight:

- **`wave3/scraping-ladder`** — `collection/adapters/{base,adapters,routing}.py`
  (new, 1124 lines), `collection/ladder.py` (+219), `collection/orchestrator.py`
  (+113), `fetch.py` (+50), `sources.py` (+6), `config/sources.yaml` (0),
  `tests/test_scraping_ladder.py` (1025), `tests/test_scraping_ladder_security.py` (832).
- **`wave3/source-expansion`** — `sources.py` (+314), `source_policy.py` (new,
  290), `source_expansion.py` (new, 749), `communities.py` (new, 225),
  `config/sources.yaml` (+56), `config/communities.yaml` (new, 39),
  `db/migrations/0008_source_expansion.sql` (new, 44), `collection/orchestrator.py` (+2).

The other six leave `collection/`, `discovery/`, `fetch.py`, `safety.py` and
`config/sources.yaml` byte-identical to `main`. `collection/state.py` is
byte-identical in all nine trees.

## Shared path on main (the rules every rung inherits)

- `safety.admit_url` (safety.py:73-106) — scheme allowlist, no userinfo, host
  must be in `source.domains`, no IP-literal host, port must be 80/443 and
  match the scheme, host must resolve to public addresses only.
- `safety.admit_redirect` (safety.py:109-115) — re-admits every hop and
  blocks an https→http downgrade.
- `safety.is_public_ip` (safety.py:31-49) — rejects loopback, private,
  link-local, multicast, reserved, unspecified, CGNAT `100.64.0.0/10`,
  IPv4-mapped IPv6, IPv6 ULA, and 6to4/Teredo addresses embedding a private
  IPv4. Requires `is_global`.
- `fetch.Fetcher._fetch` (fetch.py:307-410) — terms gate, allowed-use gate,
  robots check, per-hop re-admission, byte ceiling, 304 handling, ETag and
  Last-Modified capture.
- `fetch.RequestBudget.acquire` (fetch.py:198-234) — per-source
  `max_requests_per_run`, `min_interval_seconds` gap, wall-clock floor
  restored from the ledger after a restart.
- `sources.SourceRegistry.require` — terms, allowed use, domains, enabled.

## The ladder as built (scraping-ladder branch)

- `routing.RUNG_TOOLS` (routing.py:23-30) maps api/rss/http → `"http"`,
  crawler → `"crawl4ai"`, browser → `"playwright"`, managed → `"apify"`.
- `routing.rung_order` (routing.py:56-66) returns `[source.collector] +
  source.fallbacks`, de-duplicating rungs, not tools.
- `ladder.Ladder.collect` (ladder.py:102-178) tries each rung in order. A
  reason in `POLICY_REASON_CODES` (base.py:64-77) ends the ladder. A tool
  failure escalates. An unavailable tool is skipped and recorded.
- `base.AdapterBase.collect` (base.py:390-408) — terms gate, robots check on
  the entry URL, `UrlGate.admit` on the entry URL, then the tool.
- `base.UrlGate` (base.py:283-334) — every browser or crawler request goes
  through `admit` or `redirect`; `MAX_RESOURCES = 50` per capture.
- `adapters.PlaywrightDriver` (adapters.py:210-302) — fresh context per
  capture, `accept_downloads=False`, `permissions=[]`,
  `service_workers="block"`, route handler gates every request, browser and
  context closed in `finally`.
- `adapters.ApifyDriver` (adapters.py:305-363) — token and actor from the
  environment only, `maxItems: 1`, run timeout from the source, token
  redacted in error text.

## Budgets, saturation, health (unchanged by every Wave 3 branch)

- Discovery caps: 40 queries per run, 20 per source, 8 per family, $5 per
  run (`discovery/budget.py:39-42`). Persisted through the checkpoint
  round-trip.
- `QueryFrontier.saturation()` (frontier.py:541) returns a verdict with five
  reasons. No production caller consults it.
- `state.source_health()` (state.py:270) computes the failure histogram and
  `consecutive_failures`. `source_policy.health_status()`
  (source_policy.py:258) turns that into `quarantined`. No production caller
  consults it.
- `DiscoveryStatus` separates `empty` from `failure` and `timeout`
  (`records.py:39-48`), and `frontier.record_execution` treats them
  differently (frontier.py:475-524).

## Gates

| Tree | Command | Result |
|---|---|---|
| main | `uv run pytest` | `2138 passed in 42.24s` |
| `wave3/scraping-ladder` | `uv run pytest` | `2216 passed in 43.07s` |
| `wave3/source-expansion` | `uv run pytest` | `2191 passed in 42.48s` |

## Cross-branch conflict to carry forward

`src/product_hunter/sources.py` is modified by both collection branches with
incompatible admission logic. The scraping-ladder branch adds
`Collector.crawler` and the `fallbacks` field. The source-expansion branch
replaces `enabled: bool` with `status: SourceStatus`, adds `effective_status`
and `policy_gaps`, and rewrites `require()` into `admission()` with five
further gates. A textual merge cannot hold both.

## Migration-number collision

Three branches claim `0008`: `0008_source_expansion.sql` (source-expansion),
`0008_observability.sql` (observability-evaluation), `0008_autonomy.sql`
(autonomous-operation). `main`'s last migration is `0007_independence.sql`.

## Note on the docs root

`docs/resonix/` does not exist in any tree. The repository root is
`docs/reasonix/`. The report is written to `docs/reasonix/reports/`, the same
call the wave-3 qdrant report made for the same reason.

## Empirical checks run (read-only, in memory)

These turned code-reading claims into executed evidence. No file was
written; the scripts lived in the temp directory.

**C-1, the registry accepts a backwards ladder.** In the scraping-ladder
tree, a `SourceConfig` with `collector: managed` and
`fallbacks: [http, api, rss, browser, crawler]` validates without error.
`rung_order` returns `['managed', 'http', 'api', 'rss', 'browser',
'crawler']` and the tools tried are `['apify', 'http', 'http', 'http',
'playwright', 'crawl4ai']`. The ladder starts on the paid managed rung and
then runs backwards, and the registry says nothing.

**C-2, the HTTP tool runs twice.** With the natural
`collector: rss, fallbacks: [http, crawler, browser, managed]`, the rung
order is `[rss, http, crawler, browser, managed]` and the tools are
`['http', 'http', 'crawl4ai', 'playwright', 'apify']`.

**C-3, the enum collision is real.** In the source-expansion tree,
`Collector` has members `api, rss, http, browser, managed` and no
`crawler`. `Collector.crawler` raises
`AttributeError: type object 'Collector' has no attribute 'crawler'`,
which is exactly what `routing.py:27` does.

**Private addresses.** Python's `ipaddress` reports `is_private=True` for
6to4 and Teredo addresses that embed a private IPv4, so `is_public_ip`
rejects them before the `is_global` test is reached. `100.64.0.1` is
neither private nor global, and the explicit CGNAT check catches it.

## The architecture document corroborates two findings

`docs/reasonix/ARCHITECTURE.md:352-361` describes the collection layer and
states the canonical ladder:

> "It owns how to fetch: the collector ladder from an official API through a
> feed, direct HTTP, a local crawler, browser automation, to a managed
> scraper, and the refusal to run a rung that is not installed."

That is the specification C-1's validator has to implement. The same
paragraph ends with "Nothing calls it yet.", which is C-4 stated in the
project's own words.

## Citation check

Every `# path:line` citation in the report's code blocks was resolved
against the tree it names. 24 of 24 resolve to an existing file and sit in
range. Six paths exist in more than one tree
(`sources.py`, `collection/ladder.py`, `collection/orchestrator.py`,
`fetch.py`); each of those citations was checked against all three trees
and the quoted line matches the scraping-ladder tree, which is the branch
each finding's header names.

## Baseline movement during the probe

`main` moved from `00a6ca3` to `69e7e11` while the probe ran. Both new
commits (`a22d18d`, `69e7e11`) add the wave-3 final-audit-05 report and
change no source, so every line number in this probe still holds. All eight
Wave 3 branch heads are unchanged from the table above.

## Convention seen in a sibling probe

The nova-memory entry for final audit probe 04 records that it left its
report untracked because nine sibling probe reports are untracked in the
same tree. The tree holds untracked reports for probes 01, 02, 03, 04, 07,
08, 09 and 10. A commit of this probe's seven files must use explicit
paths and must not stage the siblings.

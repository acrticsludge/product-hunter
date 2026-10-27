# Wave 2 Session 3: Source Collection Layer

> **Integrated as `0004_collection.sql`.** The migration named below was
> written as `0003_collection.sql` and was renumbered by the Wave 2 final
> integration. See
> [wave-2-final-integration.md](../reports/wave-2-final-integration.md).

Status: spec, accepted for implementation.
Contract version: `wave2-collection-v1`.

## 1. What this layer is

Collection turns a list of targets into raw captures with provenance. It sits
between discovery and document extraction:

```
DISCOVERY -> COLLECTION -> DOCUMENT -> EVIDENCE
```

A target is a `(source_id, url)` pair. Discovery decides what to look for.
Collection asks the network for it and records what came back. Document
extraction turns the stored bytes into structured records. Evidence is a later
stage entirely.

## 2. What this layer is not

Collection contains no research reasoning. It does not score, cluster, extract
pain points, judge whether a page is worth reading, or decide that an
opportunity exists. It fetches what it is told to fetch, under the source
policy, and it records the result.

The following stay out of this wave: clustering, scoring, opportunity
research, Obsidian projection, and every Phase 7 integration.

## 3. The invariant that shapes the design

Source policy is enforced in one shared path, and no collector can reach the
network outside it.

The shared path is `product_hunter.fetch.Fetcher`. It already enforces the
terms gate, the allowed-use gate, the domain allowlist, the scheme and port
rules, the userinfo rule, the IP-literal rule, private-address resolution, the
redirect limit, the HTTPS to HTTP downgrade rule, robots.txt, the retry ladder,
`Retry-After`, the ETag and Last-Modified validators, the byte ceiling, the
raw store, content hashing, and the rejection log.

The collection layer therefore owns no HTTP client of its own. It builds the
`Fetcher` and calls it. Everything a collector could otherwise skip is a
property of the object it has to go through, not a rule it has to remember.

Two things the shared path does not yet own move into it:

- The per-source request counter and the interval gate become a
  `RequestBudget` object the collection layer owns, so the counters survive a
  process restart and two collectors in the same run share one counter.
- The per-run and per-stage fetch caps come from the research contract and are
  enforced with the existing `BudgetTracker`, so the collection layer and the
  research gate read the same ledger.

## 4. Module layout

```
src/product_hunter/collection/
    __init__.py     the public surface
    state.py        the durable state: source state, run state, items, health
    ladder.py       the collection ladder and the collector interface
    orchestrator.py planning, budget, execution, checkpoints, provenance
```

`src/product_hunter/fetch.py` gains one class, `RequestBudget`, and one
optional constructor argument on `Fetcher`.

## 5. The data model

Migration `0003_collection.sql` adds three tables.

### `collection_source_state`

One row per `(source_id, url)`, across every run.

| column | meaning |
|---|---|
| `source_id`, `url` | the target the row describes |
| `cursor` | the source's own pagination cursor |
| `etag`, `last_modified` | the validators the server last handed out |
| `last_status` | the HTTP status of the last attempt |
| `last_fetch_id`, `last_fetched_at` | the last capture and when it happened |
| `content_sha256` | the hash of the last stored body |
| `updated_at` | when the row last changed |

The validators outlive a run on purpose. A later run sends them back and the
server answers `304` when nothing changed, so unchanged content is never
downloaded twice.

### `collection_run_state`

One row per `(run_id, source_id)`.

| column | meaning |
|---|---|
| `request_count` | HTTP requests spent against that source in that run |
| `fetch_count` | logical fetches spent against that source in that run |
| `last_request_at` | when the source was last asked for anything |

This is the durable budget ledger. A run that crashes and restarts picks its
counters up where it left them, so a crash cannot buy a fresh allowance.

The `run_id` holds the *scope*: the unit the caps are counted in. It is the
run id of a research run, or the batch scope of a cycle trigger, and every
job of one batch shares one scope, so the caps are counted once per batch
and not once per job.

### `collection_items`

One row per `(run_id, source_id, url, attempt_no)`. Every attempt has its
own row, so a failure is not erased by the retry that follows it.

| column | meaning |
|---|---|
| `attempt_no` | the attempt's number for that target, counting from one |
| `outcome` | `fetched`, `not_modified`, `rejected`, `failed`, `skipped`, `duplicate` |
| `reason_code` | the failure's reason, or empty |
| `detail` | the human-readable detail of the failure |
| `fetch_id`, `status`, `bytes`, `raw_sha256`, `raw_path` | the capture |
| `latency_ms`, `retry_count` | what the attempt cost |
| `attempted_at` | when the attempt happened |

Every attempt lands here, successful or not. The table is the failure
provenance and the idempotency record at the same time: the target is the
idempotency key, and the latest attempt of a target decides whether it is
collected again.

## 6. Budgets

Four caps apply to a collection, and each one is checked before a request
starts.

| cap | source of truth | enforced by |
|---|---|---|
| per-source requests per run | `rate_limit_policy.max_requests_per_run` in the source registry | `RequestBudget` |
| per-source minimum gap | `rate_limit_policy.min_interval_seconds` | `RequestBudget` |
| per-run fetches | `budgets.per_run.fetch_requests` in the research contract | `BudgetTracker` |
| per-stage fetches | `budgets.per_stage.evidence_collection.fetch_requests` | `BudgetTracker` |
| per-source fetches | `budgets.per_source.<source_id>` | `BudgetTracker` |

The contract dimensions used are `fetch_requests` and
`source_requests_per_source`, the names the research gate already reads.

The `BudgetTracker` is rebuilt at start-up by replaying the run's stored
counters through `record()`. No new budget mechanism is introduced.

## 7. The collection ladder

The order of preference is the spec's: API or feed, then direct HTTP, then a
local crawler, then browser automation, then a managed scraper.

`ladder.py` maps a source's `collector` value onto a rung:

| `collector` | rung | production status |
|---|---|---|
| `api` | API over HTTP | active |
| `rss` | feed over HTTP | active |
| `http` | direct HTTP | active |
| `browser` | Playwright | refused, the dependency is not installed |
| `managed` | Apify | refused, the dependency is not installed |

The three active rungs share one implementation. A feed is an HTTP GET; the
parsing of a feed is a document concern and stays out of this layer.

The `browser` and `managed` rungs raise `CollectorUnavailable` with the name of
the missing dependency. They do not fall back to a different rung, because a
silent fallback would fetch a page a browser was supposed to render, and the
operator would not know the page was incomplete.

The `Collector` protocol is the seam a future crawler, browser or managed
collector plugs into. Nothing else in the layer knows which rung ran.

## 8. Scheduling and planning

`CollectionPlanner.plan()` turns a list of targets into the batch to collect
now, and reports what it left out and why.

A target is left out when:

- its source is disabled, or its terms are not `reviewed` or `permitted`
  (`terms_not_permitted`),
- its source asked for no domain, or the domain is not on the allowlist
  (`unsafe_url`),
- the target was already collected in this run (`duplicate`),
- the run's fetch budget is spent (`budget_exceeded`),
- the source's minimum gap has not elapsed (`rate_limited`).

The batch is ordered so that the sources rotate. One source with fifty targets
does not push the other sources out of the batch.

`CollectionPlanner.due_sources()` lists the sources whose minimum gap has
elapsed, which is what a caller asks when it wants to know who may be
collected from right now.

## 9. Execution, idempotency and recovery

`CollectionOrchestrator.collect()` executes one target.

1. The planner's verdict is checked. A refused target is recorded as
   `skipped` with its reason, and no request is made.
2. The budget is checked. An exhausted budget is recorded as `skipped` with
   `budget_exceeded`, and no request is made.
3. The stored validators for the target are read and handed to the shared
   path, which sends them as `If-None-Match` and `If-Modified-Since`.
4. The shared path runs. Every failure is recorded by the shared path in the
   rejection log, and by the collection layer in `collection_items`.
5. The source state, the run state, and the item row are written in one
   transaction.
6. A checkpoint is written for the run, naming the step and the target.

A target already collected in the same run is not fetched again. The item row
is returned with the outcome `duplicate` and the original `fetch_id`. This is
the idempotency guarantee, and it holds across a crash, because the item row
is committed before the next target starts.

A crash between two targets loses nothing. The completed targets have item
rows, the budget counters are committed, and a replay skips them.

## 10. Failure provenance

Every attempt has a row in `collection_items` with an outcome, a reason code
and a detail. The reason codes are the shared path's own: `robots_blocked`,
`budget_exceeded`, `http_error`, `too_large`, `too_many_redirects`,
`empty_body`, `network_error`, `unsafe_url`, and the registry's
`terms_not_permitted`, `source_disabled`, `missing_use`, `use_not_allowed`,
`no_domains`, `unknown_source`.

The row also carries the terms status, the allowed uses and the review basis
of the source, so a reader can see why a capture was allowed without opening
the registry.

## 11. Source health

`source_health()` reports, per source, across every run: the attempts, the
outcomes, the failures grouped by reason code, the last success, the last
failure, the consecutive failures, and the mean latency.

The numbers are derived from `collection_items` and `collection_source_state`.
No health table is kept, so the report cannot drift from the records it
summarises.

A source with consecutive failures is a source the scheduler should slow down.
That decision belongs to the caller; the layer reports the number.

## 12. Security invariants

The collection layer adds no network path of its own, so the shared path's
rules hold for every target. The tests cover them explicitly.

| threat | rule |
|---|---|
| localhost and loopback | the address is not public, so the host is refused |
| private and carrier-grade NAT addresses | the same rule |
| a redirect into a private address | every hop is admitted again |
| HTTPS to HTTP downgrade | refused |
| a URL with userinfo | refused |
| `file:`, `data:`, `javascript:` and other schemes | refused by the scheme allowlist |
| an IP-literal host | refused |
| DNS rebinding | the host is resolved and checked before the request, and every redirect resolves again |
| a redirect loop | the redirect limit stops it |
| a body that carries instructions | the body is stored as bytes and hashed. The layer parses nothing and evaluates nothing, so page text can never become a tool argument, a shell command or a URL |

The last row is the one that needs a structural answer rather than a rule. The
collection outcome carries hashes, paths, status codes and headers. It never
carries page text. There is no function in the layer that takes content and
returns a command, a URL or a tool call.

## 13. What the tests must show

Rate limiting, retry budgets, `429`, `500`, `502`, `503`, `504`,
`Retry-After`, the ETag round trip, the duplicate fetch, the terms rejection,
the robots rejection, the redirect rejection, crash recovery, and the
idempotent replay.

The security tests cover the table in section 12.

## 14. Integration assumptions

- The worker registers a `COLLECT` stage handler. The scheduler's
  `source_collection` schedule already creates `COLLECT` jobs, and no handler
  for that stage exists yet, so this layer supplies the missing half.
- A collection job's payload is a target: `source_id` and `url`. The run id
  comes from the job's `run_id`.
- The research contract's `evidence_collection` stage is the collection
  stage. The contract already names `fetch` as one of its tools.
- Discovery owns the choice of targets. Collection does not invent URLs.
- The `sources` table is already in the schema, and the registry is already
  the authority on terms, domains and rate limits. The collection layer reads
  both and enforces nothing twice.

## 15. Out of scope

Clustering, scoring, opportunity research, Obsidian, and every Phase 7
integration. The `browser` and `managed` ladder rungs are interfaces without
implementations, and they stay that way until the dependency is a deliberate
decision.

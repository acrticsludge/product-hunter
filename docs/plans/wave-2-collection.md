# Plan: Wave 2 Source Collection Layer

Spec: `docs/reasonix/specs/wave-2-collection.md`.
Branch: `wave2/collection-engine`. Worktree: `.worktrees/wave2-collection-engine`.

## 1. The two decisions that shape the work

### The request budget belongs to the shared path

The `Fetcher` keeps its per-source request counter in `self._rate`, a plain
dict on the instance. A collection run owns one `Fetcher`, so the counter works
inside a process. It does not work across a restart: a crashed run comes back
with an empty dict and a fresh allowance.

The counter moves into a `RequestBudget` object in `fetch.py`. The `Fetcher`
takes one in its constructor and calls `budget.acquire(source)` where it
called `self._acquire(source)`. The collection layer owns the `RequestBudget`,
persists its counters after every attempt, and restores them when it starts.

The alternative was a second counter in the collection layer that refuses a
target before it calls the `Fetcher`. It drifts, because the shared path also
spends requests on `robots.txt` and on redirect hops, and the collector cannot
see those. A budget that counts what it does not control is not a budget.

### The contract's caps use the contract's tracker

`research.budget.BudgetTracker` already enforces per-run, per-stage and
per-source limits, and the research gate reads the same dimension names. The
collection layer uses it with the shipped `config/research_contract.yaml`.

The tracker is in memory, so the collection layer rebuilds it at start-up by
replaying the run's recorded items through `record()`. The dimension names are
`fetch_requests` (with the stage `evidence_collection`) and
`source_requests_per_source` (with the source).

## 2. Files

| file | change |
|---|---|
| `src/product_hunter/fetch.py` | add `RequestBudget` and the bounded detail helper; `Fetcher` takes a budget and delegates to it |
| `src/product_hunter/collection/__init__.py` | new, the public surface |
| `src/product_hunter/collection/state.py` | new, the durable state and the health report |
| `src/product_hunter/collection/ladder.py` | new, the collector interface and the ladder |
| `src/product_hunter/collection/orchestrator.py` | new, planning, execution, checkpoints |
| `db/migrations/0003_collection.sql` | new, three tables |
| `tests/test_collection.py` | new, the behaviour tests |
| `tests/test_collection_security.py` | new, the security tests |

`src/product_hunter/sources.py` is not touched. The registry's `Collector`
enum has no Crawl4AI value, and the ladder keeps that rung as a name it
refuses until a source can ask for it.

## 3. The migration

```sql
CREATE TABLE collection_source_state (
    source_id       TEXT NOT NULL REFERENCES sources(source_id),
    url             TEXT NOT NULL,
    cursor          TEXT,
    etag            TEXT,
    last_modified   TEXT,
    last_status     INTEGER,
    last_fetch_id   TEXT,
    last_fetched_at TEXT,
    content_sha256  TEXT,
    updated_at      TEXT NOT NULL,
    PRIMARY KEY (source_id, url)
);

CREATE TABLE collection_run_state (
    run_id          TEXT NOT NULL,
    source_id       TEXT NOT NULL REFERENCES sources(source_id),
    request_count   INTEGER NOT NULL DEFAULT 0,
    fetch_count     INTEGER NOT NULL DEFAULT 0,
    last_request_at TEXT,
    PRIMARY KEY (run_id, source_id)
);

CREATE TABLE collection_items (
    run_id       TEXT NOT NULL,
    source_id    TEXT NOT NULL REFERENCES sources(source_id),
    url          TEXT NOT NULL,
    attempt_no   INTEGER NOT NULL DEFAULT 1,
    outcome      TEXT NOT NULL,
    reason_code  TEXT NOT NULL DEFAULT '',
    detail       TEXT NOT NULL DEFAULT '',
    rung         TEXT NOT NULL DEFAULT '',
    fetch_id     TEXT,
    status       INTEGER,
    bytes        INTEGER NOT NULL DEFAULT 0,
    raw_sha256   TEXT,
    raw_path     TEXT,
    latency_ms   INTEGER NOT NULL DEFAULT 0,
    retry_count  INTEGER NOT NULL DEFAULT 0,
    attempted_at TEXT NOT NULL,
    PRIMARY KEY (run_id, source_id, url, attempt_no)
);
```

Two indexes: `collection_items(source_id, attempted_at)` for the health report
and `collection_run_state(run_id)` for the run ledger.

The `sources` foreign key is the same one the `fetches` table already carries,
so a source that was never synced into the store cannot be collected from.

## 4. `fetch.py`

```python
class RequestBudget:
    """The per-source request counters and the interval gate for one run."""

    def __init__(self, *, counts=None, floors=None, clock=time.monotonic,
                 wall_clock=utcnow, sleeper=time.sleep, stored_count=None,
                 on_change=None) -> None: ...

    def acquire(self, source) -> None:
        """Take one request slot for the source, or refuse the request."""
```

`acquire` keeps the order the old `_acquire` had: check the cap, count the
request, wait out the interval. The `Fetcher` keeps its own `clock` and
`sleeper` defaults, so a `Fetcher` built without a budget behaves exactly as
it does today.

`on_change` is called with the source the request was for, after every
request. The collection layer wires it to the writer that adds one to the
scope's ledger, so a crash cannot hand the run a fresh allowance.

`stored_count` is a callable that returns the requests the scope has already
spent against a source. The budget checks the cap against the larger of its
own count and the stored one, so two workers on one scope cannot both spend
the allowance. A budget without that hook counts exactly as it always did.

The read of the stored count and the increment of the ledger are not one
transaction, so N workers on one scope can overshoot the cap by at most N-1
requests. The fix is a transactional reserve in the store.

`fetch.py` also gains the bound on a failure detail. The rejection log and
the collection layer's rows share one helper and one limit, so neither
carries an unbounded string.

The budget also learns a *floor* per source: the wall-clock instant before
which the source may not be asked. `restore` takes it, so a restarted
process waits out the gap its last request left behind instead of asking
the source again at once. The floor is wall time and the in-process gap is
monotonic, so the floor is waited out against the wall clock.

## 5. `collection/state.py`

Four dataclasses and one class.

- `SourceState`: the validators, the cursor and the last outcome of one
  `(source_id, url)`.
- `RunState`: the counters of one `(scope, source_id)`.
- `CollectionItem`: one attempt at one target in one scope.
- `SourceHealth`: the report for one source.

`CollectionState` wraps the connection and owns the reads and the writes:
`source_state`, `save_source_state`, `run_state`, `run_states`,
`add_request`, `add_fetch`, `item`, `next_attempt_no`, `save_item`,
`request_counts`.

`item` reads the latest attempt of a target, and `save_item` writes one row
per attempt with the attempt number one above the last, so a retry does not
erase the attempt it follows.

`source_health(conn)` derives the report from `collection_items` and
`collection_source_state`. No health table is kept, so the report cannot
drift from the records.

## 6. `collection/ladder.py`

```python
class CollectorUnavailable(Exception):
    """The rung the source needs is not installed."""

class Collector(Protocol):
    name: str
    def collect(self, target, *, validators=None) -> FetchRecord: ...

class HttpCollector:
    """The rung that fetches a target over HTTP through the shared path."""

class Ladder:
    def collector_for(self, source) -> Collector: ...
```

The default ladder registers the `HttpCollector` for `api`, `rss` and `http`.
The `crawler`, `browser` and `managed` rungs are not registered, so
`collector_for` raises `CollectorUnavailable` naming the rung. There is no
silent fallback to a cheaper rung: a page that needed a browser would be
fetched incompletely and the operator would not know.

## 7. `collection/orchestrator.py`

```python
@dataclass(frozen=True)
class CollectionTarget:
    source_id: str
    url: str

@dataclass(frozen=True)
class PlanRefusal:
    target: CollectionTarget
    reason_code: str
    detail: str

@dataclass(frozen=True)
class CollectionPlan:
    scope: str
    batch: tuple[CollectionTarget, ...]
    refusals: tuple[PlanRefusal, ...]

@dataclass(frozen=True)
class CollectionOutcome:
    scope: str
    source_id: str
    url: str
    outcome: str
    reason_code: str
    detail: str
    rung: str
    fetch_id: str | None
    status: int | None
    bytes: int
    raw_sha256: str | None
    raw_path: str | None
    latency_ms: int
    retry_count: int
```

`CollectionOrchestrator` builds the `Fetcher`, the `Ladder` and the
`BudgetTracker`, and owns them. It never holds a client it can use for
anything but the `Fetcher`.

The scope is the unit the caps are counted in: the run id of a research run,
or the batch scope of a cycle trigger. Every job of one batch shares one
scope, so the caps are counted once per batch and not once per job. The
ledger and the tracker are keyed by the scope.

The methods:

- `plan(scope, targets, now)` returns the batch and the refusals. The batch
  rotates the sources, so one source with many targets cannot push the others
  out.
- `collect(scope, target)` executes one target and returns its outcome.
- `run(plan)` records the refusals, collects the batch, and returns the
  outcomes.
- `due_sources(now)` lists the sources that may be collected from right now:
  the ones whose interval has elapsed and whose terms permit a collection.

`make_collection_handler(orchestrator)` returns a `COLLECT` stage handler for
the existing worker. It serves a target job: a payload that names a source
and a URL. The scope comes from the job's run or from the payload's scope,
and a job with neither is refused, because the caps would be counted per
job. The shipped `source_collection` schedule produces a cycle trigger, a
payload with no target; the targets of a cycle come from discovery, which is
a different wave.

`enqueue_collection_jobs(conn, plan, *, run_id=None, scope=None)` enqueues
one `COLLECT` job per target of the batch. The idempotency key is derived
from the scope, the source and the URL, and the payload carries the scope.

### Outcomes

| outcome | meaning | terminal |
|---|---|---|
| `fetched` | the body was captured | yes |
| `not_modified` | the server answered `304` | yes |
| `rejected` | the shared path refused the request | depends on the reason |
| `failed` | the request did not complete | no |
| `skipped` | the planner did not attempt it | depends on the reason |

Terminality is decided by the reason code, not the outcome, because the same
outcome can be permanent or transient:

```python
TERMINAL_REASONS = frozenset({
    "", "unknown_source", "source_disabled", "terms_not_permitted",
    "use_not_allowed", "missing_use", "no_domains", "robots_blocked",
    "unsafe_url", "too_large", "too_many_redirects", "empty_body",
    "collector_unavailable",
})
```

`http_error`, `network_error`, `budget_exceeded` and `rate_limited` are not
terminal. A target whose latest recorded attempt is terminal is a duplicate
and is not fetched again.

### The order of one `collect()`

1. The source is resolved through the registry. A source that is unknown,
   disabled or not permitted is recorded as `skipped` with its reason, and no
   request is made.
2. The latest attempt at the target is read. A terminal attempt returns the
   `duplicate` outcome without a request, before the budget is consulted.
3. The budget is checked. A spent budget is recorded as `skipped` with
   `budget_exceeded`, and no request is made.
4. The stored validators are read and handed to the shared path, which sends
   them as `If-None-Match` and `If-Modified-Since`.
5. The shared path runs. A failure is recorded in the rejection log by the
   shared path, and in the item row by the collection layer.
6. The source state, the run state, the item row and the `fetches` row are
   written, in one transaction.
7. A checkpoint is written for the scope, after the attempt is committed, so
   a broken checkpoint cannot erase the attempt.

## 8. The tests

`tests/test_collection.py` covers: the rate limit, the retry budget, `429`,
`500`, `502`, `503`, `504`, `Retry-After`, the ETag round trip, the duplicate
fetch, the terms rejection, the robots rejection, the redirect rejection, the
crash and recovery, and the idempotent replay. It also covers the scope that
one batch of jobs shares, the attempt history a retry leaves behind, the gap
a restarted process waits out, and the idempotency across a crash.

`tests/test_collection_security.py` covers: localhost, a private address, a
redirect into a private address, the HTTPS to HTTP downgrade, a URL with
userinfo, an unsupported scheme, an IP-literal host, the DNS rebinding
resolver, a malicious redirect, and a body that carries instructions.

The tests use `httpx.MockTransport`, a `RawStore` in `tmp_path`, and a
resolver that returns one public address, in the style `tests/test_fetch.py`
already uses.

## 9. What is deliberately not built

No clustering, no scoring, no opportunity research, no Obsidian, no Phase 7
integration, and no Crawl4AI, Playwright or Apify dependency. The ladder
refuses the rungs it cannot serve instead of pretending to serve them.

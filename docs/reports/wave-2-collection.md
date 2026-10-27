# Wave 2 Session 3: the source collection layer

> **Integrated as `0004_collection.sql`.** This session wrote the migration as
> `0003_collection.sql`, because its worktree forked before the Wave 1
> research-gate migration took the number. The Wave 2 final integration
> renumbered it. See
> [wave-2-final-integration.md](wave-2-final-integration.md).

Branch: `wave2/collection-engine`. Worktree: `.worktrees/wave2-collection-engine`.
Implementation commit: `5489559dd5b0d54822d1e0c3311c457a81947cbf`.
Base commit: `3d7bc68`. Risk: high. Code gate: `uv run pytest`.

Spec: [specs/wave-2-collection.md](../specs/wave-2-collection.md).
Plan: [plans/wave-2-collection.md](../plans/wave-2-collection.md).
Handoff: [handoffs/wave-2-collection-handoff.md](../handoffs/wave-2-collection-handoff.md).

The mission asked for the report under a misspelled docs root.
The repository's docs root is `docs/reasonix`, so the file is here.

## What landed

A collection layer that turns a list of `(source_id, url)` targets into raw
captures with provenance, plus the changes to the shared fetch path that let
one run own its budget.

```
src/product_hunter/collection/__init__.py      61 lines   the public surface
src/product_hunter/collection/ladder.py        73 lines   the rungs and the seam
src/product_hunter/collection/state.py        320 lines   the durable state
src/product_hunter/collection/orchestrator.py 841 lines   planning, budgets, execution
db/migrations/0003_collection.sql              64 lines   three tables
tests/test_collection.py                    1228 lines   68 tests
tests/test_collection_security.py             273 lines   15 tests
src/product_hunter/fetch.py                  +156 lines   RequestBudget and two shortcuts
```

`fetch.py` grew one class and lost the inline counter it replaced. A `Fetcher`
built without a budget behaves exactly as before, and `tests/test_fetch.py` is
byte-for-byte unchanged against `3d7bc68`.

### The invariant the design rests on

`product_hunter.fetch.Fetcher` is the only path to the network. The collection
layer receives an `httpx.Client` and hands it straight to the `Fetcher`; it
never calls it. The terms gate, the allowed-use gate, the domain allowlist, the
scheme and port rules, the userinfo rule, private-address resolution, the
redirect limit, the HTTPS to HTTP downgrade, robots.txt, the retry ladder,
`Retry-After`, the ETag and Last-Modified validators, the byte ceiling, the raw
store, content hashing and the rejection log all live in that one object.

A collector cannot reach the network around a policy check, because it holds
nothing but the fetcher.

### The state

Three tables. `collection_source_state` holds one row per `(source_id, url)`:
the cursor, the ETag, the Last-Modified validator, the last status, the last
fetch and the hash of the last stored body. The validators outlive a run on
purpose, so a later run sends them back and the server answers `304` instead
of sending the same body twice.

`collection_run_state` holds one row per `(run_id, source_id)`: the HTTP
requests spent, the fetches spent, and when the source was last asked. It is
the durable ledger, so a run that crashes picks its counters up where it left
them.

`collection_items` holds one row per attempt, keyed
`(run_id, source_id, url, attempt_no)`. Every attempt leaves a row with its
outcome, its reason code, its detail, the source's terms status, allowed uses
and review basis, and the capture's hash, path, status, size, latency and
retry count. The target is the idempotency key; the latest attempt decides
whether it is collected again.

### The budgets

Five caps, each checked before a request goes out.

| cap | where it comes from | who enforces it |
|---|---|---|
| per-source requests per run | the source's `max_requests_per_run` | `RequestBudget` in the shared path |
| per-source minimum gap | the source's `min_interval_seconds` | `RequestBudget`, restored from the ledger after a restart |
| per-run fetches | the contract's `budgets.per_run.fetch_requests` | `BudgetTracker` |
| per-stage fetches | the contract's `budgets.per_stage.evidence_collection.fetch_requests` | `BudgetTracker` |
| per-source fetches | the contract's `budgets.per_source` | `BudgetTracker` |

The contract caps use the shipped `config/research_contract.yaml` and the
existing `research.budget.BudgetTracker`, so the research gate reads the same
ledger the collector writes. The tracker is rebuilt at start-up by replaying
the run's stored counters, which is why a failed attempt counts as a fetch: the
ledger and the tracker have to agree, or a crash hands the run its failures
back as budget.

### The scope

The scope is the unit the caps are counted in. It is the research run's id
when the collection belongs to a run, and a caller-named scope otherwise.
Every job of one batch shares one scope, and a job with neither a run nor a
scope is refused. This was the review's first must-fix: a job with no run used
to be scoped by its own id, which multiplied the caps by the number of jobs.

### The ladder

The order of preference is the spec's: an API or a feed, then direct HTTP, then
a local crawler, then browser automation, then a managed scraper. The `api`,
`rss` and `http` rungs share one implementation, because a feed is an HTTP GET
and parsing a feed belongs to the document stage. The `crawler`, `browser` and
`managed` rungs are not registered: `collector_for` raises
`CollectorUnavailable` naming the missing dependency. There is no silent
fallback to a cheaper rung, because a page that needed a browser would be
fetched incompletely and the operator would not know.

No crawler, browser or managed-scraper dependency was added.

## The gate

```
1063 passed in 22.03s
```

The baseline before this work was 980. The 83 new tests are the 68 collected
from `tests/test_collection.py` and the 15 from
`tests/test_collection_security.py`.

The verify node ran the adapter's own gate wrapper and got the same answer:

```
1045 passed in 20.74s   (at the time of that run)
[loopgate] PASS  exit 0    in 0m22s
```

## The security checks

`tests/test_collection_security.py` drives the whole layer, not a helper in
isolation. Each case refuses before a request goes out.

| threat | what the test does |
|---|---|
| localhost | a source whose allowlist holds `localhost`; nothing is requested |
| loopback behind a name | the resolver returns `127.0.0.1`; nothing is requested |
| private address behind a name | the resolver returns `10.0.0.5`; nothing is requested |
| redirect into a private address | the first hop answers `302` to a private host; the hop is refused |
| HTTPS to HTTP downgrade | the redirect points at `http://`; refused |
| userinfo in the URL | `https://user:pass@host/…`; refused |
| unsupported scheme | `file:`, `data:`, `javascript:`; refused |
| an IP-literal host | `https://93.184.216.34/…`; refused |
| DNS rebinding | the resolver is called before the request, and again on every hop |
| a malicious redirect | a redirect off the allowlist, and a redirect loop |
| a body that carries instructions | the body is stored as bytes and hashed; the outcome carries no page text |

The injection case is the one that needs a structural answer rather than a
rule. The collection outcome carries hashes, paths, status codes, headers and
reason codes. It never carries page text, and no function in the layer takes
content and returns a command, a URL or a tool argument. The test asserts that
the instruction-bearing body survives as a raw artifact and that nothing
downstream of it is executable.

## What the review changed

The adversarial review ran three times. The first pass said `changes-requested`
with three must-fix findings, five should-fix and two nice. The second said
`ship` and left eight items open. The third said `ship` with
`Lean: 0 lines possible`.

The findings that mattered, and what happened to them:

- A job with no run was scoped by its own job id, so the caps multiplied by
  the number of jobs. The scope is now explicit and shared by a batch.
- A second attempt overwrote the first, so a failure that a retry followed
  vanished from the health report. The item table now keeps one row per
  attempt.
- An unexpected exception left no trace at all. Every attempt is now recorded.
- A checkpoint failure rolled back the attempt record. The checkpoint is
  written after the commit and a broken one is reported to the caller, not
  raised.
- The contract's overrun was swallowed in silence. It is reported to the
  caller now, with the dimension, the scope and the limit.
- The minimum gap was not restored after a restart, so a restarted process
  asked the source again at once. The floor comes back with the ledger now.
- The per-source budget check read a different scope than the charge. Both
  read the stage now.
- `due_sources` named sources that may not be collected at all. It goes
  through the terms gate now.
- A request the per-source cap refused was charged to the contract as a fetch
  it never spent. A refusal that produced no capture is recorded and not
  charged.

The harden pass ran three times as well: four findings, then six, then a clean
sweep with nothing open.

## The open items

Three, and they are named rather than buried.

**The `COLLECT` handler is not registered.** `make_collection_handler` builds
one and a test runs it against the real `Worker`, but the runtime worker's
handler map does not include it. Nothing runs a collection yet.

**The shipped `source_collection` schedule cannot drive this layer.** It
enqueues a cycle trigger whose payload carries no source and no URL, and the
handler refuses a job with no target. The targets of a cycle come from
discovery, and this layer does not choose them: the registry declares domains,
not paths, and inventing a URL here would break the adapter's rule that a URL
must be declared in `config/sources.yaml`. Before this wave the same jobs died
with "no handler for stage 'COLLECT'", so this is not a regression, but it is
the top item for the wave that owns target production.

**The migration number `0003` is contested, and this branch is one of the
claimants.** This branch was cut from `3d7bc68`, where `0003` was free, and
took it with `0003_collection.sql`. Main has since moved and now holds
`0003_research_gate.sql`, and `wave2/clustering`, `wave2/evidence-pipeline`
and `wave2/independence` each carry their own `0003_*.sql` for the same
reason. `db/migrations.py` refuses a duplicate number and a gap, so the
integrator has to renumber the four into one contiguous sequence and re-apply
the `user_version` assertions in `tests/test_db_migrations.py` after each
rename. No statement inside any of the four files depends on the number, and
each branch is self-consistent on its own. The architecture document records
the same thing.

## The shortcut ledger

Three `shortcut:` markers, all in `fetch.py`, all in the shared path rather
than in the new code.

1. The byte ceiling is checked after the body is buffered, so it caps what is
   stored and not what is transferred. Closing it means streaming the body with
   a cap, which changes how the fetcher issues its requests, and
   `tests/test_fetch.py` wraps the client in a `RecordingClient` that only
   implements `get`. That test file belongs to the shared path's owner, not to
   this wave.
2. The host is resolved and every address is checked before the request, and
   every redirect hop is resolved and checked again, but the window between
   that check and the connect is open. Closing it means pinning the resolved
   address in the transport, which is a change to TLS handling and redirect
   resolution.
3. The stored request count is read and the ledger is incremented later
   without one transaction, so N workers on one scope can overshoot the cap by
   at most N-1 requests. Closing it means reserving the slot in the store in
   one transaction.

The first two were both raised as must-fix by the harden pass and both were
closed by decision rather than by code. The reasoning is the same: a wrong
implementation of either is worse than the limit it closes, and both land in
the shared fetch path with the tests that own it.

## The integration assumptions

- **Discovery owns the targets.** The collection layer is given a list of
  `(source_id, url)` and fetches them. It never invents one.
- **The research contract's `evidence_collection` stage is the collection
  stage.** The contract already names `fetch` among its tools, and the caps
  come from there.
- **`research_runs` is the research registry, and the job queue is the
  execution spine.** A `COLLECT` job's payload is a target, and its scope
  comes from the job's run or the payload.
- **The authoritative `fetches` table is still written by the import path.**
  The collection layer writes the capture through `Store.import_fetch`, and
  the raw bytes through the content-addressed `RawStore`. It does not write
  `documents`, and it does not parse.
- **The `sources` table is the registry's mirror.** The orchestrator syncs the
  registry into it on construction, because the collection tables carry a
  foreign key to it and the scheduler reads it.

## What was skipped and what was not checked

Skipped on purpose: clustering, scoring, opportunity research, Obsidian, and
every Phase 7 integration. The `crawler`, `browser` and `managed` ladder rungs
are interfaces without implementations. No CLI command was added, no
dependency was added, and no URL was added to the source registry.

Not checked:

- No live network. Every test drives `httpx.MockTransport`, and the public-IP
  rule is exercised through the real `safety.resolve_public_ips` with a fake
  `getaddrinfo`.
- The two-worker case is tested sequentially, not with two OS processes
  against one SQLite file.
- The shipped `source_collection` schedule was never run end to end, because
  `db/scheduler.py` is outside this wave's radius.
- `ruff check --select I src tests` fails on nine files that predate this
  wave. The new files are clean under `--select I,UP037,F,E4,E7,E9`. Ruff is
  not in `pyproject.toml` and the adapter's gate is pytest.

## The report to the operator

The collection layer is in, the gates are green, and the shared fetch path is
still the only way to the network. Two things are left for the waves that
follow: registering the handler, and the target production that turns a cycle
trigger into jobs. The three shortcuts above are the open exposure, and the
first two are in code that predates this wave.

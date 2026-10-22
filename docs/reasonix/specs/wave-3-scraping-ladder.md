# Wave 3 Session 4: the advanced collection ladder

Status: spec, accepted for implementation.
Contract version: `wave3-scraping-ladder-v1`.

## 1. What this layer is

The Wave 2 collection layer fetches a target over HTTP through one shared
path. That path is the whole of the network reach the project had: an API
call, a feed, a plain page. A source that needs a rendered page, a local
crawl, or a managed scraper had no way in, and the ladder's browser and
managed rungs raised `CollectorUnavailable`.

This wave gives those rungs an adapter, and it gives the ladder a
deterministic way to choose between them.

```
API / RSS
  -> direct HTTP
  -> Crawl4AI
  -> Playwright
  -> Apify
```

The order is the spec's order of preference, and it is the order the
routing follows. The expensive rung is never chosen because it is
available. It is chosen when the source's configuration says so, or when
the rung below it failed and the source declared a fallback.

## 2. What this layer is not

It holds no research reasoning. It does not choose targets, judge pages,
score anything, or decide that a page is worth reading. It takes a target,
takes it with the cheapest tool the source's policy allows, and describes
what came back.

The shared fetch path stays the only place a plain HTTP request is made.
This layer adds tools that are not plain HTTP requests, and it holds each
of them to the same source policy.

## 3. The invariant that shapes the design

Source policy is enforced in one place per tool, and the tool that runs is
decided by the source's configuration, never by a model.

Two rules follow from that, and everything else in this spec is a
consequence of them.

**A tool never reaches the network outside the policy.** Every adapter
runs the same sequence: the terms gate, the allowed-use gate, the domain
allowlist, the scheme and port rules, the private-address rule, the
robots check, the per-source budget, the byte ceiling, the raw store and
the content hash. The HTTP adapter gets all of that from the shared fetch
path. The browser, crawler and managed adapters get it from the adapter
base in `adapters/base.py`, which reuses `safety.admit_url`,
`safety.admit_redirect` and `fetch.RequestBudget` rather than keeping a
second copy of any of them.

**The ladder is a pure function of the source's configuration.** The
source's `collector` value names the preferred rung. The source's
`fallbacks` list names the rungs below it that may be tried if the
preferred one cannot deliver. Nothing else changes the order. A model
that wants a browser for a page the registry says is plain HTTP does not
get one, because the model is not in the routing path.

## 4. Module layout

```
src/product_hunter/collection/
    adapters/
        __init__.py     the public surface of the adapter layer
        base.py         the result, the policy, the gate, the adapter base
        adapters.py     the four adapters and their drivers
        routing.py      the deterministic rung order and the escalation
    ladder.py           the ladder: a rung, an order, a result
    orchestrator.py     planning, budgets, execution, checkpoints
```

`src/product_hunter/sources.py` gains two things: the `crawler` value on
the `Collector` enum, and the `fallbacks` list on `SourceConfig`. The
enum needed the value because the ladder already had a `crawler` rung and
no way for a source to ask for it.

`src/product_hunter/fetch.py` gains one public method, `robots_allows`.
The browser, crawler and managed adapters need the robots answer, and the
robots machinery already lived in the shared path.

## 5. The result

Every adapter returns the same object. It is `CollectionResult`, and it
carries what the mission asks for:

| field | meaning |
|---|---|
| `source_id` | the source the capture belongs to |
| `requested_url`, `final_url` | the URL asked for, and the one that answered |
| `status` | the status the tool reported |
| `outcome` | `fetched`, `not_modified`, `failed` or `rejected` |
| `tool` | the adapter that produced it |
| `rung` | the rung that served it, which is the source's `collector` value unless a fallback served it |
| `content_type` | the type of the stored bytes |
| `raw_sha256` | the hash of the stored bytes |
| `raw_path` | where the bytes are, in the raw store |
| `bytes` | the size of the stored bytes |
| `fetched_at` | when the capture was taken |
| `etag`, `last_modified`, `not_modified` | the validators the server handed out |
| `terms_status`, `allowed_use`, `review_basis`, `user_agent` | the provenance of the source |
| `latency_ms`, `retry_count` | what the attempt cost |
| `reason_code`, `detail` | the failure, when there is one |
| `metadata` | the tool's own facts: the actor, the artifact, the blocked resources |
| `attempts` | every tool that was tried, and why each one stopped |

`CollectionResult` is a pydantic model, so it serialises to the fetch log
with the same field names the `FetchRecord` uses. The two overlap on
purpose: a reader of `fetches.jsonl` should not have to know which rung
took a capture.

The result carries no page text. The body stays in the raw store,
addressed by its hash. There is no function in this layer that takes
content and returns a command, a URL or a tool argument.

`tool` and `rung` answer two different questions. `tool` is what ran:
the adapter. `rung` is what the source asked for: the collector value,
or the fallback that served it. The collection layer's `rung` column
holds the rung, and the tool is in the fetch log line.

An `outcome` of `failed` means the request went out and did not
complete. That covers a timeout, a transport error, an HTTP error and an
empty body. An `outcome` of `rejected` means the request was refused
before it went out. An `outcome` of `skipped` means no request was made
at all, which is what an uninstalled tool is.

## 6. The adapters

Four adapters, one interface:

```python
class Adapter(Protocol):
    name: str

    def collect(
        self, target: CollectionTarget, *, validators: Validators | None = None
    ) -> CollectionResult: ...
```

| adapter | tool | what it takes |
|---|---|---|
| `HttpAdapter` | `http` | a page over the shared fetch path |
| `Crawl4aiAdapter` | `crawl4ai` | a rendered page, as markdown, from a local crawler |
| `PlaywrightAdapter` | `playwright` | a rendered page, as HTML, from a throwaway browser context |
| `ApifyAdapter` | `apify` | a page from a managed actor |

Each adapter owns its policy and hands the bytes to a driver. The driver
is the thin part, and it is the only part that knows the dependency.
`Crawl4aiDriver`, `PlaywrightDriver` and `ApifyDriver` import their
library lazily, so the module loads without any of them installed.

The drivers are injected. A driver that is not supplied makes its adapter
unavailable, and an unavailable adapter is refused with the name of the
dependency, the way the Wave 2 ladder refused the browser and managed
rungs. That is the behaviour the environment has today, and it does not
change until the dependency is installed.

### The drivers

A driver is handed a `CollectRequest` and returns a `Capture`:

```python
@dataclass(frozen=True)
class CollectRequest:
    url: str                 # the admitted URL
    source: SourceConfig
    policy: CollectPolicy    # the limits the tool must honour
    gate: UrlGate            # re-admits every URL the tool is about to request

@dataclass(frozen=True)
class Capture:
    content: bytes
    content_type: str
    status: int
    final_url: str
    metadata: dict[str, str]
```

`CollectPolicy` carries the byte ceiling, the resource ceiling, the
navigation timeout, the redirect limit, the user agent, and the fact that
downloads are off.

`UrlGate` is the object that keeps a browser honest. A browser follows
redirects and loads sub-resources on its own, so the gate is what stops
it. Every URL the tool is about to request goes through `gate.admit`,
which runs the same `admit_url` the shared path runs, and every redirect
hop goes through `gate.redirect`, which counts the hop and runs
`admit_redirect`. A refused URL raises `UrlBlocked` and the tool aborts.

The local crawler is the one tool that needs the gate installed rather
than handed over, because the crawler brings its own browser. The gate
goes in through the crawler's own page-context hook, and the crawl is
refused when the hook did not install it. A crawler that cannot be gated
is not run, because it would fetch from any host.

The managed actor fetches the page from Apify's own cloud, not from this
machine. The URL handed to the actor is admitted here, and the actor's
own requests are not, because this repository cannot see them. That is
why the actor id is configuration and not a source field: the operator
chooses the actor, and the operator is the one who decides what it is
allowed to reach.

## 7. The routing

The ladder turns a source into an ordered list of adapters, cheapest
first.

| source `collector` | rung | adapter |
|---|---|---|
| `api` | API over HTTP | `HttpAdapter` |
| `rss` | feed over HTTP | `HttpAdapter` |
| `http` | direct HTTP | `HttpAdapter` |
| `crawler` | local crawler | `Crawl4aiAdapter` |
| `browser` | browser automation | `PlaywrightAdapter` |
| `managed` | managed scraper | `ApifyAdapter` |

The source's `fallbacks` list follows the preferred rung, in the order the
source wrote it. The list is empty by default, and a source with an empty
list has exactly one candidate.

The ladder walks the candidates in order and stops at the first one that
delivers a capture. The result's `attempts` list records every tool that
was tried, so an escalation is visible in the record and not hidden
inside it.

### What may escalate and what may not

A **policy refusal never escalates.** A URL that is not admitted, a
private address, a robots refusal, a source whose terms do not permit
retrieval, a disabled source: these are the operator's decisions, and a
more expensive tool does not get to overrule them. A refusal ends the
ladder.

A **tool failure may escalate**, because the tool failed to deliver the
page and the source declared that a tool below it may try. The failure
has to be the tool's own: a timeout, a transport error, an empty result,
or a tool that is not installed. An HTTP error from the site is a tool
failure too, and a source that declares no fallback still gets one.

The ladder never falls back silently. The tool that ran is in the
result, the tools that did not are in `attempts`, and the item row's
`rung` column names the rung that served the target.

## 8. Security

| threat | rule |
|---|---|
| SSRF through a browser | every URL the tool requests goes through `gate.admit` |
| a redirect into a private address | every hop goes through `gate.redirect`, which re-resolves and re-checks |
| private and carrier-grade NAT addresses | `admit_url` resolves and refuses, the same rule the shared path applies |
| an IP-literal host | refused by `admit_url` |
| a URL with userinfo | refused by `admit_url` |
| `file:`, `data:`, `javascript:` and other schemes | refused by the scheme allowlist |
| a DNS rebind | the host is resolved and checked before the request, and again on every hop |
| a browser that keeps a profile | the context is created fresh, with no storage state |
| a browser that holds cookies | a fresh context has no cookies, and the adapter never writes one |
| a browser that downloads | downloads are off in the policy and the driver refuses a download |
| a browser that reaches a local file | `file:` is not in the scheme allowlist |
| a page that carries instructions | the body is stored as bytes and hashed, and the result carries no page text |
| a credential in a log or a result | the token is redacted from every detail, and never enters `metadata` |
| an actor that a page chose | the actor id is configuration, and a source cannot name one |
| a tool that runs arbitrary page script | the browser runs with no permissions, no extensions, and a bounded navigation time |
| a crawler that fetches ungated | the crawl is refused when the gate could not be installed |

A page cannot cause a tool call. The only thing a page can do is produce
bytes, and the bytes are stored, hashed and left there.

## 9. Rate limits and budgets

Every adapter, including the HTTP one, sits behind the same caps.

| cap | where it comes from | who enforces it |
|---|---|---|
| per-source requests per run | `rate_limit_policy.max_requests_per_run` | `RequestBudget` |
| per-source minimum gap | `rate_limit_policy.min_interval_seconds` | `RequestBudget` |
| per-run fetches | the contract's `budgets.per_run.fetch_requests` | `BudgetTracker` |
| per-stage fetches | the contract's per-stage cap | `BudgetTracker` |
| per-source fetches | the contract's `budgets.per_source` | `BudgetTracker` |
| retries | `rate_limit_policy.max_retries` | the adapter's retry ladder |
| concurrency | the adapter's `max_concurrency` | a semaphore per adapter |

A retry is a request. It waits the source's minimum gap and it counts
against the per-source cap, the same way the shared path treats it.

The contract's caps are charged per tool attempt, so a fallback that runs
two tools is charged two fetches. A rung that was refused because its
tool is not installed never ran, so it is not charged. The shared fetch
path is charged the same way it always was.

The two counters answer different questions and both are right for their
own. `RequestBudget` counts the requests that really went out, including
the retries inside one tool. The contract's `fetch_requests` counts the
tool attempts, because a fetch is a capture the run tried to take.

There is no IP rotation and no rate-limit bypass anywhere in this layer.
A source that refuses a request is recorded as a refusal.

## 10. Credentials

The managed rung needs an Apify token. The token is read from the
environment, never from the source registry and never from a page.

`.env.example` gains two empty values, `APIFY_TOKEN` and `APIFY_ACTOR_ID`.
The actor id is configuration for the same reason the token is: an actor
that a page could name would be an arbitrary tool call.

The token is redacted from every error detail and never enters
`metadata`. The managed adapter is unavailable without a token, and an
unavailable adapter is refused by name.

No other rung needs a credential. Crawl4AI and Playwright run locally.

## 11. What the tests must show

The routing picks the rung the source declares, and the fallback order
follows the source's list. A failure escalates to the next candidate and
the result says which tool ran. A policy refusal does not escalate. A
timeout, a transport error, an empty result and a missing dependency are
each handled and each recorded. The per-source cap, the minimum gap, the
retry ladder and the concurrency limit all hold.

The security tests cover the table in section 8, and they drive the
adapters the way the rest of the project does: with a fake tool, a fake
resolver and a fake clock. No live service is contacted and no live
success is claimed. The dependencies are not installed in this
environment, so the drivers were exercised against their protocol and
not against the libraries.

## 12. Out of scope

A new source in `config/sources.yaml`. The registry belongs to the wave
that expands the sources, and a source added here would be a URL this
wave has no right to declare.

The `COLLECT` handler registration and the target production that the
Wave 2 report left open. Both belong to the wave that runs a collection.

A live run against Crawl4AI, Playwright or Apify. The credentials are not
here, and the libraries are not installed.

An asset-host allowlist for the browser. A page that loads its assets
from a host the source does not declare has those assets blocked, and the
count of blocked resources is in the result's `metadata`. A source that
needs a CDN needs a configuration field for it, and that field is a
follow-up, not a guess.

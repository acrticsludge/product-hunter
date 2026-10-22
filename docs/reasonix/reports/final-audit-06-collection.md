# Final audit probe 06: source discovery, registry, adapters and the scraping ladder

RUN_ID: final-audit-06
Date: 2026-10-22
Mode: read-only. No source file was modified by this probe.
Scope: the collection surface of the eight Wave 3 branches.

---

## 1. Verdict

The ladder exists and the shape of it is right. The rung table, the refusal
set and the source-driven ordering are all in place, and a model is nowhere
in the routing path. The shared fetch path on `main` is sound, and the
adapters reuse it rather than reimplementing it.

Three things stop this from shipping as it stands.

**The canonical order is a docstring, not a rule.** Nothing validates the
`fallbacks` list. A source config can jump from a feed straight to a paid
managed actor, or run backwards from a browser to plain HTTP, and the
registry will accept it without a word. The escalation is config-shaped, and
the config is unvalidated.

**The two collection branches rewrite the same file with incompatible
admission logic.** `src/product_hunter/sources.py` gains
`Collector.crawler` and `fallbacks` in one branch, and gains `status`,
`effective_status` and `policy_gaps` in the other. Whichever version wins
the merge, something load-bearing is lost: the ladder loses its escalation
field, or the registry loses its review-expiry and status gates.

**The browser and crawler rungs do not inherit the robots rule, and the
managed rung does not inherit the gate at all.** robots.txt is consulted for
the entry URL only. A browser may then load `/admin` or `/search?q=` on the
same host with the path-scoped rules never consulted. The Apify driver hands
a URL to a third-party actor with no domain allowlist in the actor input, so
the actor's own navigation is invisible to every rule in this repository.

This finding is corroborated by the project's own architecture document,
which describes the collection layer and ends with the same observation:

> "**The collection layer.** `IMPLEMENTED` (`src/product_hunter/collection/`,
> `src/product_hunter/fetch.py`). It owns how to fetch: the collector ladder
> from an official API through a feed, direct HTTP, a local crawler, browser
> automation, to a managed scraper, and the refusal to run a rung that is not
> installed. [...] `RequestBudget` in `fetch.py` holds the per-source interval
> and the per-run cap. Nothing calls it yet."
> (`docs/reasonix/ARCHITECTURE.md:352-361`)

Two further findings change what "escalation control" means in practice: the
collection layer has no production caller in any of the nine trees, and
source health and discovery saturation are both computed and then never
consulted.

A fifth one appears only in the merged tree, and it is the one that most
directly answers the mission's question. Three of the reason codes the
source-expansion registry's new `admission()` gate can return are missing
from the ladder's refusal sets. A source whose review has expired, whose
policy is incomplete, or whose HTTP collector has no robots policy is
therefore not treated as a refusal: the ladder escalates past it, and the
run is charged a fetch for a request that never left the process. That
breakage is invisible in either branch alone and unavoidable in the merge.

Count: 4 critical, 5 high, 11 medium, 8 low. The confirmed-OK register in
section 8 lists 18 controls that hold.

---

## 2. What was audited

### 2.1 The eight worktrees, discovered

`git worktree list` and `git branch -a` returned eight Wave 3 branches across
three parent directories. Two of the worktrees sit outside the repository
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

### 2.2 Which branches touch the collection surface

Two of the eight.

`wave3/scraping-ladder` adds `collection/adapters/{base,adapters,routing}.py`
(1124 lines), rewrites `collection/ladder.py` (+219) and
`collection/orchestrator.py` (+113), and adds 50 lines to `fetch.py` and 6 to
`sources.py`. It ships 1857 lines of tests across
`tests/test_scraping_ladder.py` and `tests/test_scraping_ladder_security.py`.

`wave3/source-expansion` adds `source_policy.py` (290), `source_expansion.py`
(749) and `communities.py` (225), rewrites `sources.py` (+314), and edits
`config/sources.yaml` (+56) and `config/communities.yaml` (new, 39).

The other six leave `collection/`, `discovery/`, `fetch.py`, `safety.py` and
`config/sources.yaml` byte-identical to `main`. `collection/state.py` is
byte-identical in all nine trees.

### 2.3 Gates, verbatim

| Tree | Command | Result |
|---|---|---|
| main | `uv run pytest` | `2138 passed in 42.24s` |
| `wave3/scraping-ladder` | `uv run pytest` | `2216 passed in 43.07s` |
| `wave3/source-expansion` | `uv run pytest` | `2191 passed in 42.48s` |

All three pass. The adapter's gate is `uv run pytest`, satisfied on the tree
this report is written from.

### 2.4 Method

The shared path (`fetch.py`, `safety.py`) was read first and the rules were
written down before the ladder was read, so the ladder was checked against
the rules rather than against its own docstrings. Each rung was then traced
for admission, robots, budget, validators, redirects, byte ceiling and
isolation. All nine trees were swept for network clients outside the shared
path. An independent fresh-context reviewer was dispatched with the claim
under test and ten specific attacks; its results are reconciled below and
marked where it disagreed with the direct reading.

Three claims were then checked by running the code rather than by reading
it, in the branch worktrees and in memory only: the registry's acceptance of
a backwards ladder (C-1), the ladder's resolution of a natural
configuration to two HTTP attempts (C-2), and the source-expansion
registry's `Collector` enum against the ladder's routing table (C-3). Every
`path:line` citation in this report's code blocks was resolved against the
tree it names; all 24 resolve to an existing file and sit in range.

### 2.5 Citation convention

A `path:line` in a code block is relative to the branch the finding names
in its header. Six paths exist in more than one tree (`sources.py`,
`collection/ladder.py`, `collection/orchestrator.py`, `fetch.py`), and each
of those citations was checked against all three trees; the quoted line
matches the branch the finding names. A citation that crosses trees carries
the branch as a prefix, for example
`wave3/source-expansion: src/product_hunter/sources.py`.

---

## 3. The intended ladder against the built ladder

Intended: API/RSS → direct HTTP → Crawl4AI → Playwright → Apify.

Built, in `wave3/scraping-ladder`:

```python
# src/product_hunter/collection/adapters/routing.py:23-30
RUNG_TOOLS: dict[Collector, str] = {
    Collector.api: "http",
    Collector.rss: "http",
    Collector.http: "http",
    Collector.crawler: "crawl4ai",
    Collector.browser: "playwright",
    Collector.managed: "apify",
}
```

```python
# src/product_hunter/collection/adapters/routing.py:56-66
def rung_order(source: SourceConfig) -> list[Collector]:
    order: list[Collector] = [source.collector]
    for fallback in source.fallbacks:
        if fallback not in order:
            order.append(fallback)
    return order
```

The mapping matches the intent. The ordering is the source's
`collector` followed by the source's `fallbacks`, in the order the operator
wrote them, and nothing else. `Ladder.collect`
(`collection/ladder.py:102-178`) walks that list, and two policies govern it:

- A reason in `POLICY_REASON_CODES` (`adapters/base.py:64-77`) ends the
  ladder. `unknown_source`, `terms_not_permitted`, `use_not_allowed`,
  `robots_blocked`, `unsafe_url`, `too_large` and `too_many_redirects` are
  all refusals, and a refusal raises out of the loop rather than trying the
  next rung.
- A tool failure does not end the ladder, so the next configured rung is
  tried.

That is the right shape: escalation is driven by configuration, and a model
has no route into it. The gap is that the configuration is not checked
against the ladder it is supposed to express. The order appears in three
docstrings (`ladder.py:6-8`, `routing.py:1-10`) and in the branch report, and
nowhere in code.

`config/sources.yaml` in the scraping-ladder branch declares no `fallbacks`
at all, so no shipped source can escalate. The escalation machinery is
unexercised by the shipped configuration.

---

## 4. Findings

| # | Severity | Finding | Branch | Primary file |
|---|---|---|---|---|
| C-1 | Critical | Canonical ladder order is never validated | scraping-ladder | `src/product_hunter/sources.py` |
| C-2 | Critical | `api`/`rss`/`http` share one tool, so a fallback re-runs the same adapter | scraping-ladder | `collection/adapters/routing.py` |
| C-3 | Critical | Two branches rewrite `sources.py` with incompatible admission logic | both | `src/product_hunter/sources.py` |
| C-4 | Critical | The collection layer has no production caller in any tree | all | `collection/orchestrator.py` |
| H-1 | High | robots.txt is checked for the entry URL only on the browser and crawler rungs | scraping-ladder | `collection/adapters/base.py` |
| H-2 | High | One browser capture can make 50 requests and costs one budget slot | scraping-ladder | `collection/adapters/base.py` |
| H-3 | High | The managed rung runs outside the local gate with no domain allowlist | scraping-ladder | `collection/adapters/adapters.py` |
| H-4 | High | The Crawl4AI gate is verified after the crawl has already run | scraping-ladder | `collection/adapters/adapters.py` |
| H-5 | High | Three admission reason codes are missing from the ladder's refusal sets, so a policy refusal escalates | merged | `collection/orchestrator.py`, `adapters/base.py` |
| M-1 | Medium | ETag and Last-Modified are dropped on every rung above HTTP | scraping-ladder | `collection/adapters/base.py` |
| M-2 | Medium | A failed attempt records the source's preferred rung, not the rung that failed | scraping-ladder | `collection/orchestrator.py` |
| M-3 | Medium | Source health is computed and never enforced | source-expansion | `source_policy.py` |
| M-4 | Medium | Discovery saturation is computed and never consulted | all | `discovery/frontier.py` |
| M-5 | Medium | The contract budget's check-then-act is not atomic | scraping-ladder | `collection/orchestrator.py` |
| M-6 | Medium | The source-discovery pass cannot propose a genuinely new source | source-expansion | `source_expansion.py` |
| M-7 | Medium | A community names a host outside its source's domain allowlist | source-expansion | `config/communities.yaml` |
| M-8 | Medium | A misspelled `fallbacks` key is silently ignored | scraping-ladder | `src/product_hunter/sources.py` |
| M-9 | Medium | Three branches claim migration number 0008 | three | `db/migrations/` |
| M-10 | Medium | Only the HTTP rung uses `Fetcher.fetch`, so the rejection log and the redirect chain stop at the HTTP rung | scraping-ladder | `collection/adapters/base.py` |
| M-11 | Medium | The three rung dependencies are undeclared, so a clean install has no rung above HTTP | scraping-ladder | `pyproject.toml` |
| L-1 | Low | The DNS-rebinding gap is acknowledged for HTTP but not for the browsers | scraping-ladder | `collection/adapters/adapters.py` |
| L-2 | Low | The robots.txt cache never expires | both | `src/product_hunter/fetch.py` |
| L-3 | Low | Every HTTP error is retryable, including 403 and 404 | scraping-ladder | `collection/adapters/base.py` |
| L-4 | Low | Blocked sub-resource URLs are counted but not persisted | scraping-ladder | `collection/adapters/adapters.py` |
| L-5 | Low | The database `sources` table has no `status` column | source-expansion | `db/store.py` |
| L-6 | Low | Two provider clients reach the network outside `admit_url` | subq, qdrant | `llm/subq.py`, `semantic/providers.py` |
| L-7 | Low | The ladder branch adds public API to `fetch.py` that `main` does not have | scraping-ladder | `src/product_hunter/fetch.py` |
| L-8 | Low | Neither collection branch updates the collection section of the architecture document | both | `docs/reasonix/ARCHITECTURE.md` |

---

## 5. Critical findings

### C-1. The canonical ladder order is never validated

**Severity:** Critical
**Branch:** `wave3/scraping-ladder`
**Files:** `src/product_hunter/sources.py:99-137`,
`src/product_hunter/collection/adapters/routing.py:56-66`

`SourceConfig` gained a `fallbacks` field:

```python
# src/product_hunter/sources.py:79-83
    # The rungs below the preferred one that may be tried when it cannot
    # deliver. The list is empty by default, and a source with an empty
    # list has exactly one candidate: a refusal never escalates and a
    # tool failure only escalates when the source says where to.
    fallbacks: list[Collector] = Field(default_factory=list)
```

`SourcesFile._check_invariants` (sources.py:99-137) checks duplicate ids, the
`none` use, `reviewed_at` against the terms status, prohibited sources, the
enabled-source gates and the domain shape. It says nothing about
`fallbacks`.

**Consequence.** The order of the ladder is whatever a config file says. A
source declared as `collector: rss, fallbacks: [managed]` skips direct HTTP,
the crawler and the browser and goes straight to a paid managed actor. A
source declared as `collector: browser, fallbacks: [http]` escalates
backwards to a cheaper tool. Neither is refused, and neither is logged as
unusual. Because the managed rung is the only one with a monetary cost and
the only one that leaves the local gate, this is the difference between a
config typo costing a few cents and a config typo costing an unbounded
number of actor runs.

The project's own architecture document states the order, so there is a
specification for the validator to implement:

> "It owns how to fetch: the collector ladder from an official API through a
> feed, direct HTTP, a local crawler, browser automation, to a managed
> scraper, and the refusal to run a rung that is not installed."
> (`docs/reasonix/ARCHITECTURE.md:353-355`)

This was confirmed by running the registry rather than by reading it. A
`SourceConfig` with `collector: managed` and
`fallbacks: [http, api, rss, browser, crawler]` validates without error in
the scraping-ladder tree, and:

```
rung_order  -> ['managed', 'http', 'api', 'rss', 'browser', 'crawler']
tools tried -> ['apify', 'http', 'http', 'http', 'playwright', 'crawl4ai']
```

The ladder starts on the paid managed rung, runs backwards to plain HTTP,
then jumps forward again to Playwright and Crawl4AI. The registry accepted
it without a word.

**Mandatory fix.** Add a validator to `SourcesFile._check_invariants` that
pins the fallback list to the canonical cost order
(api, rss, http, crawler, browser, managed), rejects a fallback that is
cheaper than the preferred rung, and rejects a fallback that repeats the
preferred rung's tool. The check belongs in the registry, so an invalid
registry file fails to load rather than failing at fetch time.

### C-2. `api`, `rss` and `http` share one tool, so a fallback re-runs the same adapter

**Severity:** Critical
**Branch:** `wave3/scraping-ladder`
**Files:** `src/product_hunter/collection/adapters/routing.py:23-30, 56-66`,
`src/product_hunter/collection/ladder.py:118-134`

`RUNG_TOOLS` maps three collectors onto the single tool `"http"`.
`rung_order` de-duplicates rungs, not tools:

```python
# src/product_hunter/collection/adapters/routing.py:62-66
    order: list[Collector] = [source.collector]
    for fallback in source.fallbacks:
        if fallback not in order:
            order.append(fallback)
    return order
```

`Collector.rss` and `Collector.http` are different enum members, so both
survive the de-duplication. The function's own docstring promises the
opposite of what it does:

```python
# src/product_hunter/collection/adapters/routing.py:56-61
def rung_order(source: SourceConfig) -> list[Collector]:
    """The rungs that may serve the source, cheapest first.

    A fallback that repeats the preferred rung is dropped, so the list is
    the source's order without repeats.
    """
```

The repeat it drops is a repeated `Collector`. A repeated tool is not
dropped. `Ladder.collect` then resolves each to a tool and calls the
adapter:

```python
# src/product_hunter/collection/ladder.py:118-133
        for rung in rung_order(source):
            tool = tool_for(rung)
            adapter = self._adapters.get(tool)
            if adapter is None or not adapter.available:
                ...
                continue
            ran = True
            try:
                result = adapter.collect(target, validators=validators)
```

**Consequence.** The natural configuration for this ladder is
`collector: rss, fallbacks: [http, crawler, browser, managed]`: a feed first,
a plain page as the first fallback. That config produces the rung list
`[rss, http, crawler, browser, managed]`. When the feed fetch fails, the
ladder calls the same `HttpAdapter` a second time at the `http` rung. The
page is fetched twice, two budget slots are spent, the source's minimum
interval is waited out twice, and the attempts record shows the same tool
twice under two rung names. Nothing reports the duplication.

Confirmed by running the ladder's own routing function in the
scraping-ladder tree, with the configuration an operator would most likely
write:

```
collector: rss, fallbacks: [http, crawler, browser, managed]
rungs  -> ['rss', 'http', 'crawler', 'browser', 'managed']
tools  -> ['http', 'http', 'crawl4ai', 'playwright', 'apify']
```

**Mandatory fix.** De-duplicate by tool in `rung_order`, so the rung list
holds each tool once, and record the rung name of the rung that actually
ran. Add a test that a config of the shape above produces one HTTP attempt.

### C-3. Two branches rewrite `sources.py` with incompatible admission logic

**Severity:** Critical
**Branches:** `wave3/scraping-ladder` and `wave3/source-expansion`
**File:** `src/product_hunter/sources.py`

Both branches modify the same file, from `main`, with different models of
what a source is.

The scraping-ladder branch adds the ladder's vocabulary:

```python
# wave3/scraping-ladder: src/product_hunter/sources.py:39-44
class Collector(str, Enum):
    api = "api"
    rss = "rss"
    http = "http"
    crawler = "crawler"
    browser = "browser"
    managed = "managed"
```

The source-expansion branch replaces the switch entirely:

```python
# wave3/source-expansion: src/product_hunter/sources.py
    source_type: SourceType = SourceType.other
    status: SourceStatus = SourceStatus.pending_review
    terms_status: TermsStatus
    ...
    review_expires_at: date | None = None
    last_reviewed_by: str = ""
```

and rewrites the gate:

```python
# wave3/source-expansion: src/product_hunter/sources.py (admission)
        status = source.effective_status(moment)
        if status is SourceStatus.stale:
            return AdmissionDecision(source_id, False, "review_expired", ...)
        if status is not SourceStatus.active:
            return AdmissionDecision(source_id, False, "source_disabled", ...)
        if source.terms_status not in (TermsStatus.reviewed, TermsStatus.permitted):
            return AdmissionDecision(source_id, False, "terms_not_permitted", ...)
        ...
        gaps = source.policy_gaps()
        if gaps:
            return AdmissionDecision(source_id, False, "incomplete_policy", ...)
        if source.collector is Collector.http and source.robots is not RobotsPolicy.required:
            return AdmissionDecision(source_id, False, "robots_policy_missing", ...)
```

**Consequence.** A textual merge cannot hold both, and the reconciliation
needed is larger than adding a field. Four things collide.

The enum. The scraping-ladder branch adds `Collector.crawler`; the
source-expansion branch's enum has `api, rss, http, browser, managed` and no
`crawler`. `routing.py:27` indexes `Collector.crawler`, so importing the
ladder's routing module against the source-expansion registry fails at
import time. Confirmed by running it against the source-expansion tree:

```
W2 Collector members: ['api', 'rss', 'http', 'browser', 'managed']
AttributeError: type object 'Collector' has no attribute 'crawler'
```

The `enabled` field. The scraping-ladder branch has a settable
`enabled: bool = True` (sources.py:88). The source-expansion branch replaces
it with a computed read-only property derived from `status`, plus a
`model_validator(mode="before")` that consumes the `enabled` key from the
file and refuses a file where `enabled` and `status` disagree
(sources.py:205-240). A merge cannot keep a settable field and a computed
property of the same name.

The gate. The source-expansion branch's `require()` delegates to
`admission()`, which applies eight gates. The scraping-ladder branch's
`require()` applies five: it has no `effective_status` check, so it admits a
source whose review has expired, and it has no `policy_gaps` check, so it
admits a source with no `review_basis` or no `robots_or_api_notes`. It also
has no `robots_policy_missing` rule, so it admits an HTTP source whose
robots policy is `skip`.

The vocabulary. `Collector.crawler` and `fallbacks` exist only in the
scraping-ladder branch, and the source-expansion branch's `config/sources.yaml`
carries the new `status`, `review_expires_at`, `last_reviewed_by`,
`metadata`, `query_capabilities` and `collection` blocks that the
scraping-ladder branch's `SourceConfig` would silently ignore, because
`SourceConfig` does not set `extra="forbid"` (M-8).

**Consequence.** Take source-expansion's file and the ladder loses
`fallbacks` and `Collector.crawler`: there is no escalation, and a
`collector: crawler` value in any registry file fails to parse. Take
scraping-ladder's file and the registry loses `status`,
`effective_status` and `policy_gaps`: a source whose review has expired, or
which is suspended or retired, is still admitted, because `enabled` is the
only switch left. The two branches' `require()` implementations already
disagree on that point today, and an integrator who picks the wrong side
loses a fail-closed gate without any test failing.

**Mandatory fix.** Rebase rather than merge. Take source-expansion's
`sources.py` as the base, then extend its `Collector` enum with `crawler`,
add the `fallbacks` field to its `SourceConfig`, keep its computed `enabled`
and its `admission()` gate set, and add C-1's validator on top. Then confirm
that the merged `require()` keeps all eight of source-expansion's gates and
that `fallbacks` survives. Add one test per gate.

### C-4. The collection layer has no production caller in any tree

**Severity:** Critical
**Branches:** all nine trees
**File:** `src/product_hunter/collection/orchestrator.py`

Across all nine trees, `CollectionOrchestrator(`, `make_collection_handler`,
`enqueue_collection_jobs` and `COLLECT_STAGE` appear only inside
`collection/orchestrator.py` and in the re-export list of
`collection/__init__.py`. The CLI never builds an orchestrator. The worker
runtime never registers a `COLLECT` handler.

The ladder's own default makes the point sharper:

```python
# src/product_hunter/collection/orchestrator.py:391-407
        self._ladder = Ladder(
            self._fetcher,
            adapters=build_adapters(
                registry,
                raw_store,
                self._budget,
                self._fetcher,
                # The tools are injected rather than found. A browser in an
                # unattended worker is a deliberate decision, so the rung
                # is refused by name until a caller hands the driver over.
                drivers=tools if tools is not None else {},
                ...
```

`drivers={}` means `build_adapters` constructs the crawl4ai, playwright and
apify adapters with `driver=None`, so `AdapterBase.available` is False and
each of those rungs is refused by name.

**Consequence.** Two things are true at once. The collection layer, and the
whole Wave 3 scraping ladder with it, ships unwired: no scheduled job, no
CLI command and no worker stage reaches it. And the three escalated rungs
are default-off even if it were wired, because no caller supplies a driver.
The comment calls the injection "a deliberate decision", so the intent is
documented, but the effect is that the ladder is a library with no
production path.

This finding also sets the urgency of the others. H-1 through H-4 are latent
rather than live today, because nothing drives the browser or the managed
rung. They become live the moment the layer is wired.

**Mandatory fix.** Wire the orchestrator into the worker's `COLLECT` stage
and add a CLI command that constructs one. Decide explicitly whether
`tools` should default to `available_drivers()` or stay empty, and write
the decision down. Fix H-1 through H-4 before the wiring lands, not after.

---

## 6. High findings

### H-1. robots.txt is checked for the entry URL only on the browser and crawler rungs

**Severity:** High
**Branch:** `wave3/scraping-ladder`
**Files:** `collection/adapters/base.py:390-408, 283-334`,
`collection/adapters/adapters.py:144-156, 235-247`,
`src/product_hunter/safety.py:73-106`

`AdapterBase.collect` performs one robots check, for the entry URL:

```python
# src/product_hunter/collection/adapters/base.py:398-403
        source = self._require_source(target.source_id)
        if not self._fetcher.robots_allows(source, target.url):
            raise RobotsBlocked(f"robots.txt disallows {target.url}")
        policy = self._policy(source)
        gate = UrlGate(source, policy, resolver=self._resolver)
        admitted = gate.admit(target.url)
```

`UrlGate.admit` and `UrlGate.redirect` call `admit_url` and
`admit_redirect`. Neither consults robots:

```python
# src/product_hunter/safety.py:73-106 (admit_url) - no robots consult
def admit_url(url: str, source: SourceConfig, resolver=resolve_public_ips) -> Admission:
    parsed = urlsplit(url)
    scheme = parsed.scheme.lower()
    if scheme not in {s.lower() for s in source.schemes}:
        raise UrlBlocked(...)
    ...
    if host not in source.domains:
        raise UrlBlocked(...)
```

The route handlers admit every browser request through that gate and
nothing more:

```python
# src/product_hunter/collection/adapters/adapters.py:235-247
        def handle(route) -> None:
            incoming = route.request
            try:
                previous = incoming.redirected_from
                if previous is not None:
                    gate.redirect(previous.url, incoming.url)
                else:
                    gate.admit(incoming.url)
            except UrlBlocked:
                blocked.append(incoming.url)
                route.abort()
                return
            route.continue_()
```

The HTTP rung is correct. `Fetcher._fetch` re-checks robots on every
redirect hop (fetch.py:349) and the robots fetch itself is admitted and
budgeted (fetch.py:492-493).

**Consequence.** robots.txt rules are path-scoped. A source whose robots
disallows `/admin` or `/search` is collected over the browser, the browser
requests those paths as sub-resources, and the path rule is never
consulted. The only check that ever ran was for the entry URL. The capture
is then stored and attributed to a source whose robots compliance was never
established for the paths that produced the bytes.

**Mandatory fix.** Consult robots per requested URL inside `UrlGate`, using
the same `Fetcher.robots_allows` the entry check uses, and abort any
request whose path is disallowed. Cache the robots answer per origin, the
way the HTTP rung does. If per-request robots is judged too costly for
sub-resources, then refuse the browser and crawler rungs for any source
whose `robots` policy is `required`, and say so in the rung's contract.

### H-2. One browser capture can make 50 requests and costs one budget slot

**Severity:** High
**Branch:** `wave3/scraping-ladder`
**Files:** `collection/adapters/base.py:59, 306-334, 427-451`,
`src/product_hunter/fetch.py:198-234`

```python
# src/product_hunter/collection/adapters/base.py:56-59
# The most network requests one capture may make. A page and its assets
# are a few dozen requests; a thousand of them would be a crawl, and a
# crawl belongs to the crawler rung.
MAX_RESOURCES = 50
```

The gate counts its own requests:

```python
# src/product_hunter/collection/adapters/base.py:306-314
    def admit(self, url: str) -> Admission:
        if self._resources >= self._policy.max_resources:
            raise UrlBlocked(...)
        self._resources += 1
        return admit_url(url, self._source, resolver=self._resolver)
```

The budget is taken once per tool attempt:

```python
# src/product_hunter/collection/adapters/base.py:439-444
            try:
                with self._slots:
                    self._budget.acquire(source)
                    capture = self._take_once(request)
                break
```

`RequestBudget.acquire` (fetch.py:198-234) is what enforces
`max_requests_per_run` and `min_interval_seconds`.

**Consequence.** A single Playwright capture may issue up to 50 requests to
the source and consume one of the source's per-run request slots and one
minimum interval. A source configured with `max_requests_per_run: 20` and
`min_interval_seconds: 2.0` can therefore receive 1000 requests in what the
registry records as 20. The source's declared rate limit is not what the
target actually receives, which is the thing a rate limit is for.

The cap is also per attempt rather than per ladder run. `AdapterBase.collect`
builds a fresh gate for each call:

```python
# src/product_hunter/collection/adapters/base.py:401-402
        policy = self._policy(source)
        gate = UrlGate(source, policy, resolver=self._resolver)
```

The gate's resource counter and hop counter start at zero, so a ladder that
tries the browser and then the managed actor gets 50 resources on each, 100
in total, for one target.

**Mandatory fix.** Charge the budget per admitted request rather than per
tool attempt. The gate is the only place that knows how many requests were
made, so it is the place the charge belongs. Give `UrlGate` a reference to
the `RequestBudget` and a per-source counter, and have `admit` and
`redirect` take a slot before the request goes out, the way
`RequestBudget.acquire` does for the HTTP rung. The per-source cap then
reflects real traffic, and the per-source interval is a floor between
individual requests rather than between captures. Share one gate across the
ladder's rungs, or move the resource cap out of the gate and into the
orchestrator, so the cap covers the target rather than the attempt.

### H-3. The managed rung runs outside the local gate with no domain allowlist

**Severity:** High
**Branch:** `wave3/scraping-ladder`
**File:** `collection/adapters/adapters.py:305-363`

```python
# src/product_hunter/collection/adapters/adapters.py:325-336
        run_input = {
            "startUrls": [{"url": request.url}],
            "maxItems": 1,
        }
        timeout = timedelta(seconds=int(request.policy.timeout_seconds))
        try:
            client = ApifyClient(token=self._token)
            run = client.actor(self._actor_id).call(
                run_input=run_input,
                run_timeout=timeout,
                wait_duration=timeout,
            )
            items = client.dataset(_dataset_id(run)).list_items()
```

The actor input carries no `allowedDomains` and no `blockedDomains`. The run
executes on Apify's infrastructure, where the local `UrlGate` does not
exist. The entry URL was admitted locally, and the robots check covered the
entry URL. What the actor does after that is invisible to every rule in
this repository: it may follow redirects, load sub-resources, or crawl links
to hosts the source does not declare.

**Consequence.** The capture is stored and attributed to a source whose
`terms_status`, `allowed_use` and robots policy were verified against a URL
the actor may not have stayed on. The `terms_status` and `allowed_use` gates
in the registry are the operator's statement about a site, and the managed
rung delegates the fetching to a third party without a way to hold that
delegation to the statement.

The credit side of this rung is in better shape. The token and the actor
come from the environment and never from the registry or a page
(adapters.py:456-472). `maxItems: 1` and the run timeout give a per-run
ceiling. The token is redacted in error text (adapters.py:360-363).

**Mandatory fix.** Add `allowedDomains` to the run input, derived from
`source.domains`, so the actor is confined to the hosts the source declares.
Refuse the managed rung for any source whose `robots` policy is `required`
unless the actor's fetch behaviour is documented and constrained, since
robots is path-scoped and the actor's paths are not observable. Record the
actor's own request count and spend against the source's budget.

### H-4. The Crawl4AI gate is verified after the crawl has already run

**Severity:** High
**Branch:** `wave3/scraping-ladder`
**File:** `collection/adapters/adapters.py:111-207`

```python
# src/product_hunter/collection/adapters/adapters.py:178-194
        crawler = AsyncWebCrawler(config=browser_config)
        async with crawler:
            crawler.crawler_strategy.set_hook(
                "on_page_context_created", on_page_context_created
            )
            result = await crawler.arun(url=request.url, config=run_config)

        status = int(getattr(result, "status_code", 0) or 0)
        if not getattr(result, "success", False):
            raise ToolHttpError(status, f"the crawl answered {status}")
        if not gated:
            # The crawl worked, but nothing checked the requests it made,
            # so what came back cannot be trusted to the source policy.
            raise ToolUnavailable(
                "the local crawler did not install the request gate, so its "
                "requests were not checked against the source policy"
            )
```

`gated` is set to True inside the `on_page_context_created` hook. The hook
is installed on line 181 and the crawl runs on line 183. The check on line
188 happens after.

**Consequence.** The failure mode is fail-late. If the hook never fires,
the crawl has already made its network requests against hosts the source
does not declare, and the code then discards the result. The refusal
protects the capture, not the traffic. The comment describes the check as
though it protected the crawl, and it does not.

**Mandatory fix.** Refuse to call `arun` unless the route handler is
already installed, and install it through a driver-level hook that runs
before the first navigation rather than through a page-context hook that
runs during it. If crawl4ai cannot offer a pre-navigation hook, the rung
should be refused rather than run and checked afterwards.

### H-5. Three admission reason codes are missing from the ladder's refusal sets

**Severity:** High
**Branches:** merged state of `wave3/scraping-ladder` and `wave3/source-expansion`
**Files:** `collection/orchestrator.py:66-84, 96-109, 528`,
`collection/adapters/base.py:64-77`, `collection/ladder.py:134-149`,
`src/product_hunter/sources.py` (source-expansion `admission()`)

The source-expansion branch's `admission()` returns nine reason codes:
`unknown_source`, `review_expired`, `source_disabled`, `terms_not_permitted`,
`use_not_allowed`, `missing_use`, `no_domains`, `incomplete_policy` and
`robots_policy_missing`.

The scraping-ladder branch has three sets of reason codes, and none of them
lists the last three of those. Checked against the source:

```
unknown_source         in TERMINAL    in NO_CAPTURE
review_expired         NOT terminal   NOT no-capture
source_disabled        in TERMINAL    in NO_CAPTURE
terms_not_permitted    in TERMINAL    in NO_CAPTURE
use_not_allowed        in TERMINAL    in NO_CAPTURE
missing_use            in TERMINAL    in NO_CAPTURE
no_domains             in TERMINAL    in NO_CAPTURE
incomplete_policy      NOT terminal   NOT no-capture
robots_policy_missing  NOT terminal   NOT no-capture
```

`POLICY_REASON_CODES` (adapters/base.py:64-77) also omits all three.

Three things follow in the merged tree.

**A policy refusal escalates the ladder.** `AdapterBase._require_source`
calls `self._registry.require(...)`, which on the merged registry raises
`SourceBlocked` carrying the decision's reason code. The ladder catches it:

```python
# src/product_hunter/collection/ladder.py:134-149
            except (FetchError, SourceBlocked, UrlBlocked) as exc:
                reason = getattr(exc, "reason_code", "fetch_failed")
                attempts.append(
                    Attempt(
                        tool=adapter.name,
                        outcome="refused" if reason in POLICY_REASON_CODES else "failed",
                        reason_code=reason,
                        detail=bounded_detail(str(exc)),
                    )
                )
                if reason in POLICY_REASON_CODES:
                    # A refusal is the operator's decision about the
                    # source, so it ends the ladder. A more expensive tool
                    # does not get to overrule it.
                    raise
                continue
```

`review_expired` is not in `POLICY_REASON_CODES`, so the refusal does not
end the ladder. The next rung runs. A source whose review expired is
fetched by the browser, and if the browser is configured as a fallback of
the managed rung, a paid actor is asked to fetch it as well. The comment
above the `raise` states the intent this code fails to carry out.

**The refusal is not terminal, so it is re-planned every cycle.**
`CollectionPlanner.duplicate_item` (orchestrator.py:296-303) returns the
recorded attempt only when its reason is in `TERMINAL_REASONS`. These three
are not, so the planner treats the target as never attempted and puts it
back in the next batch.

**The refusal is billed as a fetch.** The exception path in `collect()`
passes:

```python
# src/product_hunter/collection/orchestrator.py:528
                attempted=reason not in NO_CAPTURE_REASONS,
```

For these three reasons that is True, so `_charge` records the attempt
against the contract's fetch budget and `collection_run_state` counts a
fetch that never went out. A refusal that costs money and a refusal that
consumes budget are the same event in the ledger.

**Consequence.** The single most direct answer to the mission's question
sits here. Escalation is controlled in either branch alone and uncontrolled
in the merge. A source that a person stopped reviewing, or whose policy is
incomplete, is not stopped: it is escalated, retried and charged.

**Mandatory fix.** Add `review_expired`, `incomplete_policy` and
`robots_policy_missing` to `POLICY_REASON_CODES`, to `TERMINAL_REASONS` and
to `NO_CAPTURE_REASONS`. Better, derive the three sets from one table of
reason codes so that a new admission code cannot be added without being
classified. Add a test that each reason code the registry can return is
either terminal or explicitly retryable, and that each policy reason ends
the ladder.

---

## 7. Medium and low findings

### M-1. ETag and Last-Modified are dropped on every rung above HTTP

`AdapterBase.collect` accepts `validators` and never uses them:

```python
# src/product_hunter/collection/adapters/base.py:390-408
    def collect(
        self, target: CollectionTarget, *, validators: Validators | None = None
    ) -> CollectionResult:
        ...
        request = CollectRequest(
            url=admitted.url, source=source, policy=policy, gate=gate
        )
```

`CollectRequest` has no validators field, so the crawl4ai, playwright and
apify drivers make unconditional requests. The HTTP rung does send the
conditional headers (fetch.py:554-562) and handles the 304 (fetch.py:367-368).
Three of five rungs re-download a page that has not changed, and the origin
sees more traffic than the declared limits imply. Either carry `validators`
into `CollectRequest` and set the headers on the driver's request, or record
in the rung's contract that the escalated rungs do not revalidate.

### M-2. A failed attempt records the source's preferred rung

The success path is right:

```python
# src/product_hunter/collection/orchestrator.py:554
            rung=result.rung or source.collector.value,
```

with `result.rung` set by the ladder (ladder.py:160-164). Every failure
path passes the preferred rung instead:

```python
# src/product_hunter/collection/orchestrator.py:508, 520, 544
                rung=source.collector.value,
```

A record of a ladder exhaustion says the source's preferred rung, so an
auditor cannot tell whether HTTP failed or the browser failed. Carry the
last attempted rung on `LadderExhausted` and on the individual exceptions,
and persist that.

### M-3. Source health is computed and never enforced

`collection/state.py:270 source_health()` builds the failure histogram,
`consecutive_failures` (state.py:305-310) and `mean_latency_ms`. The
source-expansion branch turns that into a status:

```python
# src/product_hunter/source_policy.py:281-282
    if consecutive >= consecutive_failure_limit:
        return HealthStatus.quarantined
```

`DEFAULT_CONSECUTIVE_FAILURE_LIMIT` is 5 (source_policy.py:39). Nothing in
`src/` calls `health_status()`. `suspend_source` and `retire_source`
(source_policy.py:50-93) are pure functions that return a new
`SourceConfig`; their result reaches the registry only if a person pastes
it into the YAML. A source that fails every request keeps its full per-run
budget until an operator notices. Have the collection planner consult
`health_status()` and refuse a quarantined source, or state plainly in the
specification that quarantine is advisory.

### M-4. Discovery saturation is computed and never consulted

`QueryFrontier.saturation()` (discovery/frontier.py:541) returns a
`SaturationVerdict` with five reasons: family coverage, source coverage,
budget exhausted, diminishing novelty and repeated results, plus a `blocked`
flag for consecutive failures. No caller in `src/` invokes it. Only tests
do. A discovery run therefore stops when the hard query cap is reached
(40 per run, `discovery/budget.py:39`) and not when the frontier is
saturated. Poll `saturation()` in the discovery loop and stop on
`saturated`.

### M-5. The contract budget's check-then-act is not atomic

`CollectionPlanner.budget_refusal` checks:

```python
# src/product_hunter/collection/orchestrator.py:281-293
        decision = self._tracker.check(
            BudgetDimension.fetch_requests, 1, stage=STAGE
        )
        if not decision.allowed:
            return PlanRefusal(target, "budget_exceeded", decision.reason)
```

and the spend is recorded later, in `_charge` (orchestrator.py:692-716).
`RequestBudget` has a `stored_count` hook that stops two workers spending
the same source allowance (fetch.py:202-205), and `BudgetTracker` has no
equivalent. Two workers on one scope can both pass the check and both
spend. Reserve the budget slot in the same transaction that records it, the
way `RequestBudget` documents for its own counter at fetch.py:206-210.

### M-6. The source-discovery pass cannot propose a genuinely new source

```python
# src/product_hunter/source_expansion.py:257-269
    known_hosts = {
        domain.lower() for source in registry.list() for domain in source.domains
    }
    if communities is not None:
        known_hosts |= {
            community.host.lower() for community in communities.list() if community.host
        }
    fetched = _fetched_hosts(conn)
    ...
    for (host, community), documents in sorted(_samples(conn).items()):
        if not host or host in known_hosts or host not in fetched:
            continue
```

Every host in the `fetches` table was fetched through `admit_url`, which
requires the host to be in some source's `domains` (safety.py:85-86). Those
hosts are exactly the `known_hosts`. The only candidates the pass can
produce are hosts whose source was removed from the registry afterwards.
The loop is closed by construction, and the name overstates what it does.
Rename it, or give it a genuine discovery input such as a link graph, and
say which hosts are in scope.

### M-7. A community names a host outside its source's domain allowlist

`config/communities.yaml` declares:

```yaml
  - community_id: github_issues
    source_id: github_issues
    name: GitHub Issues
    host: github.com
    url: https://github.com/issues
```

while the source `github_issues` declares `domains: [api.github.com]`.
`communities.py:160 validate_against` checks that the `source_id` exists in
the registry, not that the host is allowed. Any future fetch built from
`community.url` is refused by `admit_url`, which is fail-closed and
correct, but the refusal happens at fetch time rather than at load time.
Check the community's host against its source's domains in
`validate_against`.

### M-8. A misspelled `fallbacks` key is silently ignored

`SourceConfig` uses `ConfigDict(populate_by_name=True)` and pydantic's
default `extra="ignore"`. The new nested models in the source-expansion
branch (`SourceMetadata`, `QueryCapabilities`, `SourceCollection`) each use
`extra="forbid"`, but `SourceConfig` does not. A registry file with
`fallback:` or `fallbacks: [broweser]` loads without an error, and the
source silently collects on one rung with no escalation and no warning. Set
`extra="forbid"` on `SourceConfig`.

### M-9. Three branches claim migration number 0008

| Branch | File |
|---|---|
| `wave3/source-expansion` | `db/migrations/0008_source_expansion.sql` |
| `wave3/observability-evaluation` | `db/migrations/0008_observability.sql` |
| `wave3/autonomous-operation` | `db/migrations/0008_autonomy.sql` |

`main`'s last migration is `0007_independence.sql`. The migration runner
applies files in name order, so two of the three will be skipped or
misapplied depending on which lands first. Renumber to 0008, 0009 and 0010
at integration and record the mapping in the integrator's notes.

### M-10. Only the HTTP rung uses `Fetcher.fetch`

**Severity:** Medium
**Branch:** `wave3/scraping-ladder`
**Files:** `collection/adapters/base.py:390-408`,
`collection/adapters/adapters.py:58-64`, `src/product_hunter/fetch.py:329, 573-591`

The HTTP adapter goes through the shared fetch path:

```python
# src/product_hunter/collection/adapters/adapters.py:58-64
    def collect(
        self, target, *, validators: Validators | None = None
    ) -> CollectionResult:
        record = self._fetcher.fetch(
            target.source_id, target.url, validators=validators
        )
        return CollectionResult.from_fetch_record(record, tool=self.name)
```

The other three do not. `AdapterBase.collect` calls the driver directly:

```python
# src/product_hunter/collection/adapters/base.py:404-408
        request = CollectRequest(
            url=admitted.url, source=source, policy=policy, gate=gate
        )
        capture, retries, latency_ms = self._take(request)
```

The gates are not lost. The terms and allowed-use gate is applied by
`AdapterBase._require_source` (base.py:412-416) and the robots gate by
`self._fetcher.robots_allows` (base.py:399), so admission is enforced on
every rung.

Two things in `Fetcher.fetch` are lost on the other three rungs. The
rejection log is one of them. `Fetcher.fetch` wraps `_fetch` and writes a
`FetchRejection` row for every failure:

```python
# src/product_hunter/fetch.py:327-329
        try:
            return self._fetch(source_id, url, validators)
        except (FetchError, SourceBlocked, UrlBlocked) as exc:
            self._reject(source_id, url, exc)
```

`_reject` is the only writer of that log (fetch.py:573-591), and the
orchestrator holds the log without writing to it (orchestrator.py:353, 383).
So a refused policy decision on the browser or crawler rung leaves a
`CollectionItem` and no `FetchRejection` row. The two logs disagree about
what was refused.

The redirect chain is the other. `redirect_chain` is a field of `FetchRecord`
only (fetch.py:110), populated in `Fetcher._fetch` (fetch.py:344-373).
`CollectionResult` has no such field, so `CollectionResult.fetch_record()`
(base.py:151-174) produces a record with an empty chain. A capture that
arrived after three redirects records none of them.

**Consequence.** The rejection log and the redirect trail are complete for
the HTTP rung and incomplete above it. An auditor reading the logs cannot
tell from the rejection log that the browser rung refused a target.

**Mandatory fix.** Write the `FetchRejection` row from the orchestrator's
failure path rather than from inside `Fetcher.fetch`, so every rung reports
its refusals the same way. Add a `redirect_chain` field to `CollectionResult`
and populate it from the gate's admitted hops.

### M-11. The three rung dependencies are undeclared, so a clean install has no rung above HTTP

**Severity:** Medium
**Branch:** `wave3/scraping-ladder`
**File:** `pyproject.toml`

The branch's `pyproject.toml` is byte-identical to `main`'s. Its dependency
list holds `pydantic`, `httpx`, `beautifulsoup4`, `lxml`, `PyYAML` and
`python-dotenv`, and none of `crawl4ai`, `playwright` or `apify-client`.
The branch adds three drivers that import exactly those three packages.

The `.env.example` does carry `APIFY_TOKEN` and `APIFY_ACTOR_ID`, so the
managed rung is documented as configurable while the package that would use
the credential is not a declared dependency.

The three packages happen to be present in this machine's environment, and
`available_drivers()` returns live `Crawl4aiDriver` and `PlaywrightDriver`
here. That is a property of the machine, not of the repository. A checkout
built from `pyproject.toml` alone has none of the three, `available_drivers()`
returns three `None` values, and every rung above HTTP is refused by name.

**Consequence.** The escalation machinery is unreachable in any environment
built from the project's declared dependencies. That is the same end state
C-4 describes from the other direction, and it means the ladder cannot be
exercised by whoever picks the branch up.

**Mandatory fix.** Declare the three dependencies in `pyproject.toml`, or
declare them in an optional dependency group that the collection worker
installs, and say in the branch report which of the three the operator is
expected to install. The drivers already refuse by name when a library is
missing (adapters.py:438-453), so the declaration is the only thing
missing.

### L-1. The DNS-rebinding gap is acknowledged for HTTP but not for the browsers

`fetch.py:326-330` carries a `shortcut:` comment stating that the window
between the resolve and the connect is not closed, and that pinning the
resolved address in the transport would close it. The browser and crawler
drivers carry no such note, and the situation is worse for them: the browser
resolves in its own process, so the pinning mitigation is not available at
all. The domain allowlist is the primary control, so this is defence in
depth. Carry the same `shortcut:` note, and say what closes it.

### L-2. The robots.txt cache never expires

`Fetcher._robots` (fetch.py:296) is keyed by origin and lives for the life
of the `Fetcher` instance. A long-lived worker keeps a stale policy for the
life of the process. Bound the cache by time.

### L-3. Every HTTP error is retryable on the adapter path, including 403 and 404

`ToolHttpError` sets `retryable = True` unconditionally
(adapters/base.py:266-280), so a 404 or a 403 is retried
`source.rate_limit.max_retries` times, spending budget slots on a status
that will not change.

This applies to the adapter path only. The shared fetch path retries
`{429, 500, 502, 503, 504}` and transport errors and nothing else
(fetch.py:32, 412-446), so a 403 or a 404 from the HTTP rung is not
retried there. The adapter path is also not wired into production
(C-4), so this is latent. Make 4xx other than 429 non-retryable in
`ToolHttpError`.

### L-4. Blocked sub-resource URLs are counted but not persisted

Both browser drivers collect the URLs they blocked into a list
(adapters.py:233, 299) and publish only the count, as
`metadata["blocked_resources"]`. The refusal trail for sub-resource blocks
is lost. Persist the count and the hosts, not the full URLs, so the reason
a page came back incomplete is still auditable.

### L-5. The database `sources` table has no `status` column

`db/store.py:144 _upsert_source` writes `enabled` as an integer and
nothing else. `0008_source_expansion.sql` adds `source_candidates` and
`source_reviews` and does not alter `sources`. The distinction between
pending_review, suspended, retired and stale is lost in the database; the
enabled bit is all that survives. Add a `status` column, or state in the
schema that the database mirrors only the enabled bit.

### L-6. Two provider clients reach the network outside `admit_url`

`wave3/subq-integration` `llm/subq.py:264` owns an `httpx.Client` and
posts to `config.decisions_url`. `wave3/qdrant-semantic`
`semantic/providers.py:578-595` calls `snapshot_download(repo_id=...)`.

Neither is a collection fetch, and neither URL is chosen by a model. The
SubQ base URL is operator configuration, validated to require https except
for loopback (subq.py:119-152), and the model id is regex-validated
(providers.py:45, 114). They are recorded here because the adapter's
forbidden rule reads "Never fetch a URL that is not declared in
config/sources.yaml", and these two do not appear in that file. The rule
needs a written carve-out for operator-configured provider endpoints, or the
two providers need to be named in the adapter.

### L-7. The ladder branch adds public API to `fetch.py` that `main` does not have

The branch adds a module-level `fetch_id()` (fetch.py:58-84) and a public
`Fetcher.robots_allows()` method (fetch.py:496-511), and moves the private
`_fetch_id` onto the new function (fetch.py:568-574). The additions are
correct and the private method delegates to the public one. The drift is
worth recording because `main` does not have them, so an integrator merging
the other seven branches first will see a public API appear from nowhere.
Record the addition in the integration notes.

### L-8. Neither collection branch updates the collection section of the architecture document

`docs/reasonix/ARCHITECTURE.md` has a paragraph on the collection layer at
lines 352-361, and it names the ladder, the reason vocabulary and the
budget. Four of the eight Wave 3 branches edit that file
(`duckdb-analytics`, `observability-evaluation`, `qdrant-semantic`,
`subq-integration`). The two branches that change the collection layer
itself, `scraping-ladder` and `source-expansion`, do not.

The paragraph will therefore survive the merge describing a collection layer
that no longer exists in that form: one ladder with four rungs served by the
shared fetch path, no adapters, no `fallbacks`, and a registry with a
boolean `enabled` switch. It already says "Nothing calls it yet", which
C-4 confirms and which the merge does not change.

Update the paragraph as part of the integration, and fold in the canonical
order that C-1's validator enforces.

---

## 8. Confirmed-OK register

These were checked and hold. They are listed so the report says what the code
does and not only what it fails to do.

| Control | Where | Result |
|---|---|---|
| Terms status gate | `sources.py` `require` / `admission`; `fetch.py:310-312` | Unknown, restricted and prohibited sources are refused. |
| Allowed-use gate | `sources.py` `require`; `fetch.py:310-312` | `none` and a missing `retrieval` or `storage` are refused. |
| Domain allowlist | `safety.py:85-86` | A host outside `source.domains` is refused. |
| Scheme, port and userinfo rules | `safety.py:76-96` | Non-allowed scheme, userinfo, IP-literal host and nonstandard port are all refused. |
| Private-address blocking | `safety.py:31-49` | Loopback, private, link-local, multicast, reserved, unspecified, CGNAT `100.64.0.0/10`, IPv4-mapped IPv6, IPv6 ULA, and 6to4 and Teredo addresses embedding a private IPv4 are all rejected. Verified empirically with `uv run python -c`. |
| Redirect handling, HTTP rung | `safety.py:109-115`; `fetch.py:340-350` | Every hop re-admitted, https to http downgrade blocked, hop cap enforced, robots re-checked per hop. |
| Retries | `fetch.py:412-466`; `adapters/base.py:427-451` | Retryable statuses `{429, 500, 502, 503, 504}` plus transport errors, exponential backoff, `Retry-After` honoured and capped, and every attempt counted against the per-run cap. |
| Rate limiting | `fetch.py:198-234` | `min_interval_seconds` enforced per source, and the wall-clock floor is restored from the ledger so a crash cannot buy a fresh allowance. |
| Caching, HTTP rung | `fetch.py:354-360, 367-368`; `collection/state.py` | ETag and Last-Modified stored in `collection_source_state`, replayed as conditional headers, and a 304 stored with no body. |
| Browser isolation | `adapters.py:249-289` (Playwright), `adapters.py:161-168` (Crawl4AI) | Playwright: a fresh `browser.new_context()` per capture, `accept_downloads=False`, `permissions=[]`, `service_workers="block"`, no storage state, and browser and context closed in `finally`. Crawl4AI: `use_persistent_context=False`, `user_data_dir=None`, `accept_downloads=False`. |
| Download restrictions | `adapters.py:166, 256` | `accept_downloads=False` on both the browser and the crawler. No path writes a download to disk. |
| Managed-rung credentials | `adapters.py:456-472, 360-363` | Token and actor come from the environment only, never from the registry or a page, and the token is redacted in error text. |
| Unknown sources cannot enter collection | `source_expansion.py:8, 311, 329-351, 739-749`; `communities.py:160-177`; `db/store.py:137-142` | The expansion module never writes `config/sources.yaml`. An approval refuses `terms_status` unknown or prohibited, the entry must validate through `SourcesFile`, and the operator pastes the text. A community whose source is not in the registry is refused. `sync_registry` only upserts the YAML registry's sources. |
| Failed versus empty | `discovery/records.py:39-48, 151-163`; `discovery/frontier.py:475-524` | `DiscoveryStatus` separates `empty` from `failure` and `timeout`; result-count consistency is validated; the frontier accounts for them differently. |
| Query budgets | `discovery/budget.py:39-42, 290-308`; `discovery/store.py:166, 199` | 40 per run, 20 per source, 8 per family and $5 per run, persisted through the checkpoint round-trip. |
| Source budgets | `collection/orchestrator.py:692-741` | The contract tracker covers `fetch_requests` and `source_requests_per_source`, and the scope ledger is replayed on restart. |
| No collection bypass | all nine trees | The only network call in the collection path outside `Fetcher` and `UrlGate` is the Apify actor invocation, which is H-3. The SubQ and huggingface_hub clients are provider and model-download paths, recorded as L-6. The autonomy engine reads `source_id` from the job payload and never builds a URL. |
| No registry name collision | `wave3/opencode-control-plane` | That branch's `src/product_hunter/registry.py` is the run-state and CLI registry, not the source registry. It does not collide with `sources.py`. |

---

## 9. Mandatory integration fixes

Ordered. The first five are the gating set.

1. **Rebase `src/product_hunter/sources.py`** onto source-expansion's version.
   Extend its `Collector` enum with `crawler`, add the `fallbacks` field to
   its `SourceConfig`, keep its computed `enabled` and its eight-gate
   `admission()`, and add the ladder-order validator from item 2. Confirm
   that the merged `require()` keeps all eight gates and that `fallbacks`
   survives. One test per gate. (C-1, C-3)
2. **Add the ladder-order validator.** The `fallbacks` list must follow the
   canonical cost order, may not repeat the preferred rung's tool, and may
   not name a cheaper rung. De-duplicate `rung_order` by tool, which is what
   its own docstring already promises. One test per rule. (C-1, C-2)
3. **Classify every reason code the registry can return.** Add
   `review_expired`, `incomplete_policy` and `robots_policy_missing` to
   `POLICY_REASON_CODES`, `TERMINAL_REASONS` and `NO_CAPTURE_REASONS`, and
   derive the three sets from one table so a new code cannot be added
   unclassified. Add a test that each code the registry returns is either
   terminal or explicitly retryable, and that each policy reason ends the
   ladder. (H-5)
4. **Fix the robots gap on the browser and crawler rungs.** Consult robots
   per requested URL inside `UrlGate`, or refuse those rungs for sources
   whose robots policy is `required`. (H-1)
5. **Charge the budget per admitted request**, not per tool attempt, so the
   per-source cap reflects real traffic. (H-2)

6. **Add `allowedDomains` to the Apify run input**, derived from
   `source.domains`, and record the actor's request count and spend against
   the source's budget. (H-3)
7. **Refuse to run the Crawl4AI crawl until the route handler is
   installed**, using a pre-navigation hook. (H-4)
8. **Wire the collection layer into the worker and the CLI**, and decide
   whether `tools` defaults to `available_drivers()` or stays empty.
   Fixes 3 to 7 land before this does. (C-4)
9. **Declare the three rung dependencies** in `pyproject.toml`, or in an
   optional group the collection worker installs, and say which of the three
   the operator is expected to install. (M-11)
10. **Carry the last attempted rung on failures** and persist it. (M-2)
11. **Enforce source health in the collection planner**, or record in the
    specification that quarantine is advisory. (M-3)
12. **Poll `saturation()` in the discovery loop** and stop on `saturated`.
    (M-4)
13. **Make the contract budget reservation atomic**, in one transaction with
    the record. (M-5)
14. **Set `extra="forbid"` on `SourceConfig`.** (M-8)
15. **Renumber the three `0008` migrations** to 0008, 0009 and 0010, and
    record the mapping. (M-9)
16. **Check the community host against its source's domains** in
    `validate_against`. (M-7)
17. **Carry the validators into `CollectRequest`**, or state in each escalated
    rung's contract that it does not revalidate. (M-1)
18. **Rename or re-scope the source-discovery pass**, and state which hosts
    it may propose. (M-6)
19. **Write the `FetchRejection` row from the orchestrator's failure path**,
    so every rung reports its refusals the same way, and add
    `redirect_chain` to `CollectionResult`. (M-10)

The low findings do not block the merge, and each carries its own remedy in
the finding itself. (L-1, L-2, L-3, L-4, L-5, L-6, L-7, L-8)

Items 1 to 5 are the gating set. Items 6 to 9 should land before the layer
is wired. The rest can follow.

---

## 10. Not checked, and the limits of this probe

- No code was executed against a live source, a live actor or a real
  browser. The browser and crawler drivers were read, not run. The
  behaviour of `crawl4ai`'s hook contract and of the Apify actor's own
  navigation is inferred from the source, not observed.
- The registry, the routing table and the driver discovery were executed in
  memory, with no network traffic and nothing written. That confirms C-1,
  C-2, C-3 and M-11. It does not confirm H-1, H-3 or H-4, which depend on
  what a browser or an actor does with a request, and those three rest on
  the source alone.
- The `wave3/source-expansion` and `wave3/scraping-ladder` suites were run
  and pass. The other six branches' suites were not run; they were read for
  network clients and for changes to the collection surface, and both
  checks came back clean.
- The `opencode-control-plane` branch's `registry.py` was read far enough to
  establish that it is the run-state registry and not the source registry.
  Its behaviour was not otherwise reviewed.
- The `qdrant-semantic` and `subq-integration` providers were read only
  where they create a network client. Their own logic was not audited.
- The database schema in `db/migrations/` was checked for numbering
  collisions only. The column-level fit of the three `0008` migrations was
  not assessed.
- Whether the collection layer's absence of a production caller is
  deliberate (a later wave wires it) or a gap is not determinable from the
  branches alone. The finding is written as observed: no caller exists
  today.
- This probe did not read the `docs/reasonix/specs/` of the Wave 3 branches
  to check whether the ladder order was specified more strictly than it was
  implemented. The implementation was checked against the mission text.

## 11. What the stage 7 review changed

An adversarial reviewer with fresh context was given this report and the
trees it audits, and was asked to prove it wrong on nine axes: fabricated
findings, wrong severity, missed findings, false confirmed-OK claims,
incomplete work, the bypass question, the gate evidence, the mandatory
fixes, and the prose.

The reviewer confirmed all 23 findings the report held at that point
against the source, confirmed the bypass sweep, and re-ran the three gates
independently. The pass counts matched exactly (2138, 2216, 2191) and only
the wall-clock times differed.

It also found five things this report had to change, and they are corrected
above.

- The confirmed-OK count in section 1 said 12. The table in section 8 has
  18 rows.
- The browser isolation row cited `use_persistent_context=False` and
  `user_data_dir=None` against the Playwright driver. Both belong to the
  Crawl4AI driver.
- L-3 was worded as though the shared fetch path retried a 403 or a 404. It
  does not. The retryable set is `{429, 500, 502, 503, 504}` and transport
  errors. The finding applies to the adapter path only, which is also not
  wired, so it is latent.
- The report missed that only the HTTP rung calls `Fetcher.fetch`, so the
  rejection log and the redirect chain stop at the HTTP rung. That is now
  M-10.
- C-3 was under-specified about what a merge has to reconcile: the enum,
  the `enabled` field and the gate count, not just two added fields.

The reviewer also observed that `rung_order`'s docstring promises to drop a
repeated rung while the code drops a repeated enum member and not a
repeated tool. That quote is now in C-2.

One disagreement was resolved in the report's favour. The reviewer read the
adapter rungs as bypassing `Fetcher.fetch`'s terms and allowed-use gates.
They do not: `AdapterBase._require_source` applies the registry gate and
`robots_allows` applies the robots gate on every rung. What the other three
rungs miss is the rejection row and the redirect chain, which is what M-10
says.

## 12. A second pass, after the first review

The first review passed the report. A second pass then ran three checks the
first pass had not, and they produced three more findings and a correction
to the report's own claims.

The three checks were the reason-code cross-reference between the two
collection branches, the dependency declaration in `pyproject.toml`, and a
read of the sibling probe 01 architecture report that is sitting untracked
in the same tree.

The reason-code cross-reference produced H-5, the most consequential
finding in the report, and it is the one that bears directly on the
mission's question. Nine reason codes can come out of the source-expansion
registry's `admission()`. Three of them appear in none of the ladder's
three classification sets. A source whose review expired, whose policy is
incomplete, or whose HTTP collector has no robots policy is therefore
escalated rather than refused, re-planned rather than remembered, and
charged to the fetch budget as though a request had gone out. Neither
branch shows this. The merge produces it every time.

The dependency check produced M-11: the branch that adds three drivers
declares none of their packages, so a clean install has no rung above
HTTP.

The sibling report produced the corroboration for C-1 and C-4, and one
correction. `docs/reasonix/ARCHITECTURE.md:352-361` states the canonical
ladder order in the project's own words and ends with "Nothing calls it
yet", so C-1's validator has a specification to implement and C-4 is
confirmed by the repository's own documentation. The correction is to
section 1: the report had said the shared fetch path was reused by the
adapters, which is true of the terms gate, the allowed-use gate, the
robots gate, the domain allowlist and the private-address rules, and not
true of the rejection log or the redirect chain, which is what M-10 says.

This probe also found, by reading the sibling report, that three
independent probes have now reported the same migration-number collision:
probe 01 as F-01, this probe as M-9, and probe 04 as F-7 in its
nova-memory entry.

# Wave 3 Session 4: the advanced collection ladder

Branch: `wave3/scraping-ladder`. Worktree:
`C:\Anubhav\Web Dev Projects\product-hunter-wt\wave3-scraping-ladder`.
Implementation commit: `f3659b5`. Base commit: `00a6ca3`. Risk: high.
Code gate: `uv run pytest`.

Spec: [specs/wave-3-scraping-ladder.md](../specs/wave-3-scraping-ladder.md).
Plan: [plans/wave-3-scraping-ladder.md](../plans/wave-3-scraping-ladder.md).
Handoff: [handoffs/wave-3-scraping-ladder-handoff.md](../handoffs/wave-3-scraping-ladder-handoff.md).

The mission asked for the report at `docs/resonix/reports/wave-3-scraping-ladder.md`.
The repository's docs root is `docs/reasonix`, so the file is here.

## What landed

The collection ladder had rungs it could not serve. `browser` and
`managed` were refused by name, and there was no local crawler rung at
all. This adds the four adapters the spec asks for and a deterministic way
to choose between them.

```
src/product_hunter/collection/adapters/__init__.py    88 lines   the adapter surface
src/product_hunter/collection/adapters/base.py       429 lines   the result, the gate, the base
src/product_hunter/collection/adapters/adapters.py    421 lines   the four adapters, three drivers
src/product_hunter/collection/adapters/routing.py     80 lines   the rung table, the order
src/product_hunter/collection/ladder.py              172 lines   the candidates, the escalation
src/product_hunter/collection/orchestrator.py        942 lines   consumes the result
src/product_hunter/collection/__init__.py            125 lines   the public surface
src/product_hunter/fetch.py                          +50 lines   robots_allows, a shared fetch_id
src/product_hunter/sources.py                          +6 lines  the crawler value, the fallbacks
tests/test_scraping_ladder.py                       810 lines   41 tests
tests/test_scraping_ladder_security.py              614 lines   37 tests
```

### The invariant

Two rules, and everything else follows from them.

**Source policy is enforced in one place per tool.** The HTTP adapter
hands the target to the shared fetch path and repeats nothing. The other
three hold the policy themselves, and they reuse `safety.admit_url`,
`safety.admit_redirect` and `fetch.RequestBudget` rather than keeping a
second copy of any of it. The terms gate, the allowed-use gate, the
domain allowlist, the scheme and port rules, the private-address rule,
the robots check, the per-source budget, the byte ceiling, the raw store
and the content hash are the same rules for all four.

**The tool is chosen by the source's configuration.** The source's
`collector` names the rung. The source's `fallbacks` name the rungs below
it that may be tried when the first one cannot deliver. Nothing else
changes the order, so a model is not in the routing path and a page the
registry says is plain HTTP does not get a browser.

### The adapters

| adapter | tool | what it takes |
|---|---|---|
| `HttpAdapter` | `http` | a page over the shared fetch path |
| `Crawl4aiAdapter` | `crawl4ai` | a rendered page as markdown, from a local crawler |
| `PlaywrightAdapter` | `playwright` | a rendered page as HTML, from a throwaway browser context |
| `ApifyAdapter` | `apify` | a page from a managed actor |

Each one returns the same `CollectionResult`. The field names match
`FetchRecord` where the two overlap, so a line in the fetch log reads the
same whichever rung took the capture, and `Store.import_run` reads it
without knowing the rung. On top of the shared fields the result carries
`tool`, `rung`, `outcome`, `reason_code`, `detail`, `metadata` and
`attempts`.

The result carries no page text. The body stays in the raw store,
addressed by its hash.

### The routing

| source `collector` | rung | tool |
|---|---|---|
| `api` | API over HTTP | `http` |
| `rss` | feed over HTTP | `http` |
| `http` | direct HTTP | `http` |
| `crawler` | local crawler | `crawl4ai` |
| `browser` | browser automation | `playwright` |
| `managed` | managed scraper | `apify` |

The fallback order is the source's `fallbacks` list, in the order the
source wrote it. The list is empty by default, so a source that says
nothing about falling back has exactly one candidate.

A policy refusal ends the ladder. A URL that is not admitted, a private
address, a robots refusal, a source whose terms do not permit retrieval,
a disabled source: these are the operator's decisions and a more
expensive tool does not get to overrule them.

A tool failure escalates, because the tool failed to deliver and the
source said where to go next. The failure has to be the tool's own: a
timeout, a transport error, an HTTP error, an empty result, or a tool
that is not installed.

The escalation is never silent. The tool that ran is in the result, the
tools that did not are in `attempts`, and the item row's `rung` column
names the rung that served the target.

## The gate

```
2216 passed in 44.83s
```

The baseline before this work was 2138. The 78 new tests are the 41 from
`tests/test_scraping_ladder.py` and the 37 from
`tests/test_scraping_ladder_security.py`.

Ruff is not in `pyproject.toml` and is not the adapter's gate. The new
files are clean under `--select I,UP037,F,E4,E7,E9`.

## The security findings

The security tests drive the adapters, not a helper in isolation. Each
case refuses before a request goes out.

| threat | what the test does |
|---|---|
| a host outside the allowlist | the gate refuses it |
| a private, loopback or metadata address behind a name | the real resolver with a fake `getaddrinfo` refuses it |
| an IP-literal host | refused |
| userinfo in the URL | refused |
| `file:`, `data:`, `javascript:`, `ftp:`, `gopher:` | each refused |
| a nonstandard port | refused |
| a redirect into a private address | refused |
| an HTTPS to HTTP downgrade | refused |
| a redirect loop | the redirect limit stops it |
| too many requests | the resource ceiling stops it |
| a browser request to an undeclared host | the route handler aborts it |
| a browser request to a metadata address | the route handler aborts it |
| a browser redirect off the allowlist | the route handler aborts it |
| a crawler that ignored the gate | the crawl is refused |
| the browser holding state | the context is created with no storage state, no permissions, no credentials and no service worker |
| the browser downloading | `accept_downloads` is off |
| a browser left running | the context and the browser are closed even when the capture fails |
| the Apify token in a capture | it is not there |
| the Apify token in an error | it is redacted to `<token>` |
| a page that carries instructions | the body is stored and hashed, and the result carries no page text |

The last row needs a structural answer rather than a rule. The tool is
handed a URL, a policy and a gate, and nothing else. There is no function
in the layer that takes content and returns a command, a URL or a tool
argument.

## What the review changed

The adversarial review ran once and said `changes requested`. Four
must-fix findings and five should-fix findings came out of it, and all
of them were closed.

The two that mattered most were about the budget. The contract's caps
were charged from the length of the attempts list, which counted a rung
whose tool was not installed as if it had run, and the failure path
charged one fetch whatever had actually run. A source with three
declared fallbacks could spend four tool attempts for one charged fetch.
The charge is now the number of attempts that ran, on both paths, and
two tests pin it.

The third was that the local crawler could run ungated. The gate goes
into the crawler through its own page-context hook, and nothing checked
that the hook had fired, so a crawler that ignored it would have loaded
assets from any host, from this machine, with no allowlist and no
redirect check. The crawl is now refused when the gate could not be
installed. The fake crawler in the test fires the hook and shows an
undeclared host being aborted, so the test says something about the gate
and not only about the wiring.

The fourth was that the same test asserted the hook's name and nothing
else, so replacing the route handler's body with a plain continue would
have passed it.

The should-fix findings were the outcome vocabulary, the unbounded
detail on the unexpected-exception path, the `rung` and `tool` names
overlapping in the spec, and the managed rung's remote fetches. The
first three are closed in the code and the spec. The fourth is a limit
and is written down below.

## The credential blockers

The managed rung needs two values, and both are empty in `.env.example`:

```
APIFY_TOKEN=
APIFY_ACTOR_ID=
```

The token is read from the environment and never from the source
registry. It is redacted from every error detail and never enters the
capture's metadata. The actor id is configuration for the same reason
the token is: an actor a page could name would be an arbitrary tool
call.

No credential was invented and no secret was committed. The two values
are placeholders, and the managed rung is refused by name until they are
set.

Crawl4AI and Playwright need no credential. They run locally.

## The open items

Five, and they are named rather than buried.

**The libraries are not installed here.** `crawl4ai`, `playwright` and
`apify_client` are not in this environment. The drivers are exercised
against their protocol, and the three that talk to a library are
exercised against a fake library that records what it was asked to do. The
hook API and the route wiring were written against the documented
interfaces, read through Context7, and they were not exercised against
the real libraries.

**The drivers are injected, not found.** `CollectionOrchestrator` takes a
`tools` mapping and defaults to none, so every rung below HTTP is refused
until a caller hands the driver over. `available_drivers()` answers which
tools the environment has. A browser in an unattended worker is a
deliberate decision, so it is not switched on by an import.

**A page that loads its assets from a host the source does not declare
has those assets blocked.** The count of blocked resources is in the
result's metadata. A source that needs a CDN needs a configuration field
for it, and that field is a follow-up rather than a guess.

**The managed actor's own fetches are not gated by this repository.** The
URL handed to the actor is admitted here. The actor fetches from Apify's
cloud, and this repository cannot see what it requests there. That is why
the actor id is operator configuration.

**The failure path keeps the escalation history as one flattened line** in
the item row's detail. There is no column for the attempt list, and
adding one needs a migration, and the migration numbers are contested in
this repository.

## What was skipped and what was not checked

Skipped on purpose: a source in `config/sources.yaml`, because the
registry belongs to the wave that expands the sources and a URL added
here would be one this wave has no right to declare. The `COLLECT`
handler registration and the target production that the Wave 2 report
left open, because both belong to the wave that runs a collection. A
migration, because the migration numbers are contested. A live run
against Crawl4AI, Playwright or Apify, because the credentials are not
here and the libraries are not installed.

Not checked: the real `crawl4ai`, `playwright` and `apify-client` APIs,
so the hook and route wiring is unverified against the libraries. The
`uv_build` packaging of the new subpackage. Two workers against one
SQLite file, because the two-worker case is still tested sequentially.
The managed client's `call` signature is taken from its documentation, so
the `timeout_secs` and `wait_secs` arguments are the one place where an
installed client could disagree with the driver.

## The report to the operator

The ladder has its rungs now, and the rung is the source's decision
rather than a model's. The browser and the crawler put a gate in front
of every request they make on their own, and the crawler refuses to run
when it cannot be gated. The budget is charged for the tools that
actually ran, on the success path and the failure path alike.

Nothing was contacted. No service was called and no success is claimed
that did not happen in a test against a fake. The next wave that wants a
browser in the worker needs to install the library and hand the driver
over, and the next wave that wants a managed source needs the two
environment values.

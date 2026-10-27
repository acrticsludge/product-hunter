# Prompt — final audit 06, review

RUN_ID: final-audit-06

Role: adversarial reviewer, fresh context. Read-only. Try to prove the
collection path is wrong.

## Claim under test

"The collection path enforces one shared admission path, and escalation up
the ladder API/RSS → HTTP → Crawl4AI → Playwright → Apify is controlled by
source configuration, not by a model."

## Trees

- `C:\Anubhav\Web Dev Projects\product-hunter-wt\wave3-scraping-ladder`
- `C:\Anubhav\Web Dev Projects\product-hunter` (main)
- `C:\Anubhav\Web Dev Projects\product-hunter-w3c`

## Attacks to attempt

(a) A source config that makes the ladder skip rungs or run backwards, such as
    `collector: rss, fallbacks: [managed, browser, crawler, http]`. Is there
    any validator on the `fallbacks` order in `SourcesFile._check_invariants`?

(b) `Collector.api`, `Collector.rss` and `Collector.http` all map to the tool
    `"http"` in `routing.py RUNG_TOOLS`. With `collector: rss, fallbacks:
    [http, browser]`, does `rung_order()` produce `[rss, http, browser]` and
    does the ladder then invoke the same `HttpAdapter` twice? Trace
    `Ladder.collect` exactly.

(c) `admit_url()` does not consult robots.txt. `AdapterBase.collect` calls
    `fetcher.robots_allows(source, target.url)` once, for the entry URL. The
    Playwright and Crawl4AI route handlers admit sub-resources through
    `UrlGate.admit()` only. So: can a browser fetch `/admin` or `/search?q=`
    on the same host when robots.txt disallows it? Quote the code.

(d) The Crawl4AI driver sets `gated = True` inside the route hook but checks
    `if not gated` after `crawler.arun()` has already run. Does the crawl's
    network traffic happen before the gate check? What is the practical
    consequence?

(e) The Apify driver sends only `{"startUrls": [{"url": request.url}],
    "maxItems": 1}` to a third-party actor. Is there a domain allowlist in
    that input? What can the actor fetch that the local `UrlGate` never sees?
    Is there a cost ceiling?

(f) One Playwright capture can make up to `MAX_RESOURCES` (50) HTTP requests.
    How many of them increment `RequestBudget._counts` for the source?

(g) `Validators` (ETag, Last-Modified) reach the HTTP rung. Do they reach
    crawl4ai, playwright and apify? Trace where `validators` goes in
    `AdapterBase.collect` and `CollectRequest`.

(h) Does the persisted `CollectionItem.rung` record the rung that actually
    ran, or the source's preferred rung? Trace `result.model_copy(update=
    {"rung": ...})` into `orchestrator._record` → `CollectionItem(rung=...)`.

(i) Any network call in any tree that avoids `Fetcher` / `admit_url` /
    `UrlGate`? Check the CLI, the worker runtime, the autonomy engine, and
    wave3/source-expansion's `source_expansion.py` and `communities.py`.

(j) Can a source with `terms_status` unknown, restricted or prohibited, or
    `allowed_use: [none]`, or a status that is not active, still be
    collected? Check `SourceRegistry.require` in both trees. Note that
    wave3/source-expansion and wave3/scraping-ladder both modify
    `src/product_hunter/sources.py`: does either keep the other's admission
    checks?

## Output

A numbered list of attacks. For each: the claim broken or CONFIRMED, the
file:line, the quoted code, the concrete consequence. Then the confirmed
breakages ranked by severity, and the ones that must be fixed before
integration. Quote real code. No edits.

# Wave 3 Session 4: the advanced collection ladder

Spec: [specs/wave-3-scraping-ladder.md](../specs/wave-3-scraping-ladder.md).

Branch: `wave3/scraping-ladder`. Base commit: `00a6ca3`. Risk: high.

## 1. The work, in order

1. **`sources.py`.** Add `crawler` to the `Collector` enum and `fallbacks`
   to `SourceConfig`. Two additions, both defaulted, so
   `config/sources.yaml` is untouched and every existing source parses.
2. **`fetch.py`.** Add the public `robots_allows(source, url)`. It calls
   the private `_check_robots` that already exists and answers with a
   bool, so the robots machinery is not copied.
3. **`collection/adapters/base.py`.** `CollectionResult`, `Attempt`,
   `CollectPolicy`, `CollectRequest`, `Capture`, `UrlGate`, `ToolError`
   and the `AdapterBase` that holds the policy every tool shares.
4. **`collection/adapters/adapters.py`.** `HttpAdapter`,
   `Crawl4aiAdapter`, `PlaywrightAdapter`, `ApifyAdapter`, and the three
   drivers that import their libraries lazily.
5. **`collection/adapters/routing.py`.** The rung table, the candidate
   order, and the escalation rule.
6. **`collection/ladder.py`.** The ladder over the new result. The
   `collector_for` refusal stays, because the Wave 2 tests depend on it.
7. **`collection/orchestrator.py`.** Consume `CollectionResult` instead
   of `FetchRecord`, charge the contract per tool attempt, and record the
   tool that ran.
8. **`.env.example`.** The two empty Apify values.
9. **The tests.** `tests/test_scraping_ladder.py` for the behaviour and
   `tests/test_scraping_ladder_security.py` for the threats.

## 2. The shape of the code

`CollectionResult` is a pydantic model whose field names match
`FetchRecord` where the two overlap, so the fetch log reads the same
whichever rung took the capture. It adds `tool`, `outcome`,
`reason_code`, `detail`, `metadata` and `attempts`.

`AdapterBase.collect` is the template every tool runs:

```
require the source      -> terms, allowed use, enabled, domains
robots                   -> the shared path's answer
admit the URL           -> scheme, port, userinfo, allowlist, public IP
take a budget slot      -> per-source cap, minimum gap
take the page           -> the driver, with the gate and the policy
check the byte ceiling
store the bytes         -> the raw store, content-addressed
describe the result
```

The HTTP adapter is the exception. It hands the target to the shared
fetch path, which already does every one of those steps, and it describes
what came back. It does not repeat the admission, because the shared path
resolves the host again and a second resolution is a second answer.

## 3. What the drivers do

`PlaywrightDriver` creates a fresh context with no storage state, no
permissions and downloads off, installs a route handler that admits every
request through the gate and refuses a download, navigates with a bounded
timeout, and closes the context in a `finally` block. The browser is
stopped the same way.

`Crawl4aiDriver` runs the local crawler with a headless browser
configuration, no user data directory, downloads off and a bounded page
timeout, and takes the markdown the crawler produced. The crawler runs
inside the same gate.

`ApifyDriver` calls the managed actor with the admitted URL and reads the
first dataset item back. The token comes from the environment, the actor
id comes from the configuration, and the driver never sees a page-supplied
actor.

None of the three is installed here, so all three are exercised against
their protocol in the tests and against nothing else.

## 4. The risks, and what closes them

| risk | what closes it |
|---|---|
| a browser reaching a host the source does not declare | the gate admits the source's declared domains only |
| a browser keeping a profile or a cookie | a fresh context, and the adapter never writes state |
| a page causing a tool call | the result carries no page text, and nothing in the layer turns content into an argument |
| a token in a log | the token is redacted from every detail and never enters `metadata` |
| the ladder escalating past a policy refusal | the refusal ends the ladder |
| the contract's caps being spent twice | the charge is per tool attempt, and the attempt count comes from the result |
| the shared path changing under a new adapter | the HTTP adapter reuses `Fetcher` unchanged, and `tests/test_fetch.py` is untouched |

## 5. The gate

`uv run pytest`, the adapter's own gate. The baseline before this work is
recorded before the first edit and compared after.

Ruff is not in `pyproject.toml` and is not the adapter's gate. The new
files are checked with `ruff check --select I,UP037,F,E4,E7,E9` if ruff
is on the machine, and the result is reported either way.

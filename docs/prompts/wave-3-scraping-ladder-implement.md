# Wave 3 Session 4: implement the advanced collection ladder

Node: IMPLEMENT. Branch: `wave3/scraping-ladder`. Base: `00a6ca3`.
Risk: high (the shared fetch path and the source registry are in the radius).

## What to read first

- `docs/reasonix/specs/wave-3-scraping-ladder.md`
- `docs/reasonix/specs/Product-Hunter-Full-Spec.md`, sections 6, 6A, 8, 37
- `docs/reasonix/specs/wave-2-collection.md`
- `src/product_hunter/fetch.py`, `src/product_hunter/safety.py`,
  `src/product_hunter/sources.py`, `src/product_hunter/collection/`

## What to build

Four adapters behind one result contract, and a deterministic rung order.

```
src/product_hunter/collection/adapters/
    base.py       CollectionResult, Attempt, CollectPolicy, CollectRequest,
                  Capture, UrlGate, AdapterBase, the ToolError family
    adapters.py   HttpAdapter, Crawl4aiAdapter, PlaywrightAdapter,
                  ApifyAdapter, and the three drivers
    routing.py    the rung table, rung_order, LadderExhausted
```

`ladder.py` walks the candidates. `orchestrator.py` consumes the result.

## The rules that shape it

1. The shared fetch path stays the only path for a plain HTTP request. The
   HTTP adapter wraps `Fetcher` and does not repeat its admission.
2. The other three adapters hold the same policy themselves: the terms
   gate, the allowed-use gate, the domain allowlist, the scheme and port
   rules, the private-address rule, the robots check, the per-source
   budget, the byte ceiling, the raw store and the content hash. Reuse
   `safety.admit_url`, `safety.admit_redirect` and `fetch.RequestBudget`.
3. The tool is chosen by the source's configuration and by nothing else.
   The source's `collector` names the rung and the source's `fallbacks`
   name the rungs below it. No model is in the routing path.
4. A policy refusal ends the ladder. A tool failure escalates only to a
   rung the source declared.
5. Scraped content is data. It is stored, hashed and left there. Nothing
   in the layer turns content into a command, a URL or a tool argument.
6. No IP rotation, no rate-limit bypass, no invented credentials.

## The gate

`uv run pytest`. The baseline before the work is 2138 passed.

## What not to do

- Do not add a source to `config/sources.yaml`. Another wave owns that.
- Do not add a migration. The migration numbers are contested.
- Do not install a crawler, a browser or the Apify client. The adapters
  import their library lazily and are refused by name when it is absent.
- Do not write a live-success test. There are no credentials here.

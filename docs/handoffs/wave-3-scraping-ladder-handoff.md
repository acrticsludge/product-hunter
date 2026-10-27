# Wave 3 Session 4 handoff: the advanced collection ladder

Branch: `wave3/scraping-ladder`. Base: `00a6ca3`. Risk: high.

## What this wave added

Four adapters behind one result contract, and a deterministic rung order
that comes from the source's configuration and from nothing else.

```
src/product_hunter/collection/adapters/base.py       the result, the policy,
                                                      the gate, the adapter base
src/product_hunter/collection/adapters/adapters.py    the four adapters and
                                                      the three drivers
src/product_hunter/collection/adapters/routing.py     the rung table
src/product_hunter/collection/ladder.py               the candidate order and
                                                      the escalation
src/product_hunter/collection/orchestrator.py         consumes the result
src/product_hunter/fetch.py                           robots_allows, fetch_id
src/product_hunter/sources.py                         Collector.crawler,
                                                      SourceConfig.fallbacks
```

## The invariant

Source policy is enforced in one place per tool, and the tool is decided
by the source's configuration.

- The HTTP adapter goes through the shared fetch path, so every rule the
  shared path holds holds for it.
- The other three hold the same policy themselves, reusing
  `safety.admit_url`, `safety.admit_redirect` and `fetch.RequestBudget`.
- The source's `collector` names the rung and the source's `fallbacks`
  name the rungs below it. A model is not in the routing path.
- A policy refusal ends the ladder. A tool failure escalates only to a
  rung the source declared.

## The result

`CollectionResult` is a pydantic model whose field names match
`FetchRecord` where the two overlap, so a line in the fetch log reads the
same whichever rung took the capture and `Store.import_run` reads it
without knowing the rung. It adds `tool`, `rung`, `outcome`,
`reason_code`, `detail`, `metadata` and `attempts`.

The result carries no page text. The body stays in the raw store,
addressed by its hash.

## What the next wave needs to know

- The drivers are injected, not found. `CollectionOrchestrator` takes a
  `tools` mapping and defaults to none, so every rung below HTTP is
  refused by name until a caller hands the driver over. `available_drivers()`
  answers which tools the environment has.
- The three libraries are not installed here. The drivers are exercised
  against their protocol, and the three that talk to a library are
  exercised against a fake one.
- The managed rung needs `APIFY_TOKEN` and `APIFY_ACTOR_ID`. Both are
  empty in `.env.example` and neither is invented.
- A page that loads its assets from a host the source does not declare
  has those assets blocked, and the count of blocked resources is in the
  result's metadata. A source that needs a CDN needs a configuration
  field for it, and that field is a follow-up.
- The managed actor's own fetches are not gated by this repository. The
  URL sent to the actor is admitted; what the actor does with it is the
  operator's actor configuration, which is why the actor id is
  configuration and not a source field.

## The review round

The adversarial review ran once and said `changes requested`. Four must-fix
findings and five should-fix findings came out of it, and all of them were
closed.

The two that mattered most were about the budget. The contract's caps were
charged from `len(result.attempts)`, which counted a rung whose tool was
not installed as if it had run, and the failure path charged one fetch
whatever had actually run. A source with three declared fallbacks could
spend four tool attempts for one charged fetch. The charge is now the
number of attempts that ran, on both the success path and the failure path,
and two tests pin it.

The third was that the local crawler could run ungated. The gate goes into
the crawler through its own page-context hook, and nothing checked that the
hook had fired, so a crawler that ignored the hook would have loaded
assets from any host, from this machine, with no allowlist and no redirect
check. The crawl is now refused when the gate could not be installed, and
the fake crawler in the test fires the hook and shows an undeclared host
being aborted, so the test says something about the gate and not only
about the wiring.

The fourth was that the same crawler test asserted the hook's name and
nothing else, so replacing the route handler's body with a plain continue
would have passed it.

## The open items

1. The `COLLECT` handler is still not registered in the runtime worker,
   and the shipped `source_collection` schedule still cannot drive this
   layer. Both were open at the end of Wave 2 and neither is fixed here.
2. The concurrency bound is per adapter and the shared `RequestBudget` is
   not thread-safe. With the default bound of one there is no
   interleaving; a caller that raises the bound needs a thread-safe
   budget first.
3. A page that loads its assets from a host the source does not declare
   has those assets blocked, and the count of blocked resources is in the
   result's metadata. A source that needs a CDN needs a configuration
   field for it, and that field is a follow-up.
4. The managed actor's own fetches are not gated by this repository. The
   URL sent to the actor is admitted; what the actor does with it is the
   operator's actor configuration, which is why the actor id is
   configuration and not a source field.
5. The failure path keeps the escalation history as a flattened line in
   the item row's detail. There is no column for the attempt list, and
   adding one needs a migration, and the migration numbers are contested.

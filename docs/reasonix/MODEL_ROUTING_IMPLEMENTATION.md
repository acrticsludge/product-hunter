# Model routing implementation

The routing implementation exists. `IMPLEMENTED`
(`src/product_hunter/llm/catalog.py`, `src/product_hunter/llm/discovery.py`,
`src/product_hunter/llm/routing.py`, `src/product_hunter/db/model_routing.py`,
`config/model_routing.yaml`). What does not exist yet is a generation path
wired to the router: `OpenCodeGenerateProvider` in
`src/product_hunter/llm/opencode.py` still calls the model pinned in
`config/extraction.yaml`.

**Baseline.** This document describes the working tree at RUN_ID
`arch-docs-20261021-4f9c2b71`, not the last commit. HEAD is `205a4ba`, and the
routing modules are in the tree and were still uncommitted when this was
written. They are `IMPLEMENTED` like any other code.
[ARCHITECTURE.md](ARCHITECTURE.md) explains the shape of the system.
[RESEARCH_CONTRACT.md](RESEARCH_CONTRACT.md) explains what the code enforces. The
full specification is
[Product-Hunter-Full-Spec.md](specs/Product-Hunter-Full-Spec.md).

## Claim labels

Every factual claim here carries one of four labels.

| Label | Meaning |
|---|---|
| `IMPLEMENTED` | Code exists in this branch. The claim names the file. |
| `PLANNED` | A specification exists and is approved, but no code does. The claim names the spec. |
| `BLOCKED` | Cannot proceed until a precondition is met. The claim names the precondition. |
| `OPTIONAL` | Deliberately excluded from the core, per spec §38. |

The labels apply per claim, not per section.

## The routing principles (spec §3)

Spec §3 says not to use one expensive model for everything. It names four roles,
and the implementation preserves them:

- a fast or free model for extraction, classification, summarization, tagging,
  and filtering;
- SubQ for high-volume structured decisions, as a decision and processing layer
  rather than the primary chat model;
- a reasoning model for deep investigation, competitor analysis, ambiguous
  clustering, counter-evidence, market reasoning, and the final opportunity
  thesis;
- the model the user selects in OpenCode's model selector, which stays
  independent of the pipeline's routing.

SubQ is `PLANNED` in the sense that spec §3 names it as a decision layer, while
the approved `docs/reasonix/specs/model-routing.md` §9 lists it as a non-goal:
not implemented, not stubbed. The empty `SUBQ_API_KEY=` in `.env.example` stays
empty until the Phase 7 SubQ gate, and nothing reads it today.

## The identity rule

A model is `provider/model`. `ModelIdentity` in
`src/product_hunter/llm/catalog.py` is a frozen pair of `provider_id` and
`model_id`, and `.key` is `provider/model`. The same model id on two providers
is two different models, so `opencode-go/deepseek-v4.1-flash` and
`nvidia/deepseek-ai/deepseek-v4.1-flash` are different models. A model id alone
is not an identity. The catalogue, the inventory, and the execution log all key
on the pair.

## The catalogue

`src/product_hunter/llm/discovery.py` holds the two discovery surfaces.
`opencode api get /api/model` is primary, `opencode models` is the fallback, and
`opencode api get /api/info` supplies the server version. All three are argv
lists, never a command string. `CatalogRefresher` re-discovers when the cached
catalogue is older than `refresh_interval_seconds`, 900 by default. A failed
refresh keeps the previous catalogue and the failure is remembered until the
interval elapses, so a broken server costs one discovery attempt per interval
rather than one per selection. A discovery failure is never fatal to a running
pipeline.

`src/product_hunter/llm/catalog.py` is data and parsing. `Availability` is an
enum of five values: `available`, `disabled`, `retired`, `unknown`, `missing`.
`derive_availability(status, enabled)` maps a catalogue entry to one of them,
and only `active` plus `enabled` is `available`. `unknown` is deliberately not
selectable: the fallback CLI list reports no status, and a model whose usability
was never confirmed must not reach the pipeline.

`compute_catalog_version(entries)` is a SHA-256 over the sorted
`provider/model|status|enabled|name` lines, prefixed `sha256:`. That digest is
what a caller records alongside a selection, so two runs over the same catalogue
carry the same `catalog_version`.

`parse_model_catalog()` reads the route payload, with `providerID`, `modelID` or
`id`, `status`, `enabled`, `limit`, `cost`, and `time.released`.
`parse_model_list()` reads the plain CLI list and forces every entry to
`unknown`.

## The fallback rule

`src/product_hunter/llm/routing.py` holds the resolution. `ModelRole` is an
enum of thirteen roles: `extraction`, `classification`, `normalization`,
`deduplication`, `entity_resolution`, `clustering`, `validation`,
`investigation`, `competitor_research`, `counter_evidence`, `scoring`,
`reporting`, and `synthesis`. `RouteCandidate` is either an explicit
`provider/model` or a glob pattern over the live catalogue. `ModelRouter`
expands the chain, takes the first available candidate, falls through the chain
when one fails, and raises `NoModelAvailable` when nothing is available.
`UnknownRole` is raised for a role that is not in the enum.

`ModelSelection` carries `role`, `identity`, `selection_source`,
`fallback_index`, `is_fallback`, `catalog_version`, and `resolved_at`.
`SelectionSource` is `config`, `pattern`, or `default`, and `is_fallback` is
true for any index above zero.

`config/model_routing.yaml` is version 1. Every role is a list of patterns, and
no model id appears in it. The cheap roles get `opencode-go/*-flash*` then
`opencode-go/*`; the reasoning roles get `opencode-go/deepseek*` then
`opencode-go/*`. `config.RoutingConfig.config_version` is
`routing-v{version}-{sha256[:12]}`.

## The persistence

`src/product_hunter/db/model_routing.py` writes the four tables from
`db/migrations/0002_model_routing.sql`: `model_catalogs`, `model_inventory`,
`model_role_bindings`, and `model_executions`.

`record_catalog()` upserts `model_catalogs`, marks every `model_inventory` row
that was not retired as `missing` with a `retired_at`, and upserts each entry
back to life. A model that leaves the catalogue keeps its row; retired rows are
never deleted. `record_selection()` upserts `model_role_bindings` on
`(role, position)`. `record_execution()` inserts one `model_executions` row per
call, with `provider_id`, `model_id`, `role`, `selection_source`,
`fallback_index`, `is_fallback`, `catalog_version`, `prompt_version`,
`config_version`, `latency_ms`, `status`, and `error`. `list_inventory()` and
`list_executions()` read them back.

## The command surface

`IMPLEMENTED` (`src/product_hunter/cli.py`, `src/product_hunter/llm/__init__.py`).
`ph models` prints the catalogue as JSON, with `catalog_version`, `source`,
`server_version`, `discovered_at`, `model_count`, and the model list. With
`--role` it prints the resolved chain for that role instead. `--refresh` forces
a rediscovery. `build_router()` in `src/product_hunter/llm/__init__.py` is the
constructor the command uses.

## The honest limits

OpenCode publishes no event when the model set changes. The CLI is a
request and response client, and the server exposes no catalogue-change feed
the CLI surfaces, so there is no instant notification to build on. The
catalogue is polled instead: it is re-discovered when it is older than
`refresh_interval_seconds`, once more when a role matches nothing available,
and on demand with `ph models --refresh`. A model that disappears stays
selectable until the next refresh, and a model that appears is picked up at
the next refresh. Neither needs a source edit, and neither is instant. That
delay is the price of the only mechanism the current OpenCode surface offers.

The generation path does not use the router. `OpenCodeGenerateProvider` still
calls the model pinned in `config/extraction.yaml`, so an extraction run today
does not consult a role chain. The router exists and `ph models` uses it, and
that is the whole difference between a router that exists and a router that
routes the pipeline.

A catalogue is a snapshot, not a guarantee. A model that is `available` in the
catalogue can still fail at call time, and the catalogue is refreshed on an
interval, on a miss, and on demand. The selected model can change between two
runs, because the role configuration names patterns and not model ids, so a new
matching model is picked up with no edit. That is the point of the design and it
is also why the choice is not stable.

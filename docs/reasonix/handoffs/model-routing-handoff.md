# Handoff — dynamic model routing

**Date:** 2026-10-21
**Branch:** `main`, base commit `351d962`
**Spec:** `docs/reasonix/specs/model-routing.md`
**Plan:** `docs/reasonix/plans/model-routing-implementation-plan.md`
**Implementation doc:** `docs/reasonix/MODEL_ROUTING_IMPLEMENTATION.md`
**Decisions:** `docs/reasonix/decisions/2026-10-21-dynamic-model-routing.md`

---

## What exists now

A model catalogue, a discovery surface, thirteen role chains with fallback, and
the persistence of catalogues, the model inventory, role selections and
per-call execution provenance.

| Piece | File |
|---|---|
| catalogue, identity, availability, parsing, diff | `src/product_hunter/llm/catalog.py` |
| discovery over the OpenCode CLI, TTL refresher | `src/product_hunter/llm/discovery.py` |
| roles, chains, selector, selection and execution records | `src/product_hunter/llm/routing.py` |
| shared argv runner for the OpenCode CLI | `src/product_hunter/llm/opencode.py` |
| router construction | `src/product_hunter/llm/__init__.py` |
| routing config loader | `src/product_hunter/config.py` |
| `ph models` command | `src/product_hunter/cli.py` |
| persistence | `src/product_hunter/db/model_routing.py` |
| schema | `db/migrations/0002_model_routing.sql` |
| role bindings, patterns only | `config/model_routing.yaml` |

## What does not exist yet

- No benchmarking. No SubQ integration. Both are named as non-goals in the
  spec.

## What the generation path does now

`ph extract` resolves the `extraction` role through the router, binds the
selected model to the generator, and writes the run's
`model_executions.jsonl` plus a `model_routing` block in the manifest. A
router that cannot answer never stops a run: the model pinned in
`config/extraction.yaml` runs it, and the manifest and a `routing_failed`
rejection say why. `ph db import-run` writes the calls into
`model_executions` and the selection into `model_role_bindings`.

The run also records the catalogue the router decided against, in
`model_catalog.json`. The import reads it back and fills the model inventory
with it, so a model that leaves the catalogue keeps its row and gets a
`retired_at`. The inventory only moves forward: a catalogue imported out of
order cannot retire a model a newer catalogue saw, because the stamps come
from the catalogue's `discovered_at` and not from the import time.

A generator a caller supplies to `extract_command` is used as it is, and the
router is not consulted at all, so that run records no routing.

## How to use it

```
ph models                       # the whole catalogue as JSON
ph models --refresh             # re-discover first
ph models --role extraction     # the resolved chain for one role
```

```python
from product_hunter.config import load_config, load_routing_config
from product_hunter.llm import build_router

router = build_router(load_routing_config(), load_config())
selection = router.select("extraction")
# selection.identity, selection.selection_source, selection.catalog_version
```

The router API can exclude a model that failed at call time, so a caller
that wants to try the next candidate can:

```python
selection = router.select("extraction", exclude=[failed_identity])
```

That is the router API, not the extraction path: `ph extract` never calls
`select(..., exclude=...)`. A routed model that fails at call time fails its
chunk into a rejection, and the chunk is not retried with another model.

## The four properties, and where each one lives

| Property | Code | Test |
|---|---|---|
| a new model is usable with no source edit | pattern expansion over the live catalogue, `routing.py` | `test_a_new_model_is_selected_without_a_code_change` |
| a vanished model does not crash the pipeline | availability filter plus chain fallthrough, `routing.py` | `test_a_missing_model_does_not_raise` |
| historical model ids survive | `record_catalog` retires rows and never deletes them, `db/model_routing.py` | `test_the_inventory_keeps_a_vanished_model` |
| every execution is attributable | `ModelExecution` and `model_executions` | `test_the_execution_row_carries_the_provenance` |

## Watch out for

- The catalogue is polled. OpenCode publishes no model-change event, so a
  model that disappears stays selectable until the next refresh. The limit is
  stated in `docs/reasonix/MODEL_ROUTING_IMPLEMENTATION.md`.
- `config/model_routing.yaml` names no model on purpose. Do not add one to a
  source file.
- A role that is missing from the routing file is a config error, not a
  fallback: `RoutingConfig` refuses a file that does not bind all thirteen
  roles.
- The generation path and the discovery path share one argv runner. Never add
  `shell=True`, and never send a prompt through a `.cmd` shim.

## Verification

```
uv run pytest
```

The routing-specific tests are `tests/test_model_catalog.py`,
`tests/test_model_discovery.py` and `tests/test_model_routing.py`.
`tests/test_llm_contract.py` covers the generation bridge and must pass
unchanged.

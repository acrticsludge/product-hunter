# Dispatch prompt — Stage 4 IMPLEMENT (dynamic model routing)

**Node:** `loop-free-implement-1`
**Repo:** `C:\Anubhav\Web Dev Projects\product-hunter`
**Gate:** `uv run pytest` must be green.

---

## Read first

- `docs/resonix/specs/model-routing.md` — the specification.
- `docs/resonix/plans/model-routing-implementation-plan.md` — the plan. Follow it.
- `src/product_hunter/llm/base.py`, `src/product_hunter/llm/opencode.py` — the existing seam.
- `src/product_hunter/config.py`, `src/product_hunter/db/jobs.py`, `db/migrations/0001_initial.sql` — the house style.

## What to build

Ten tasks, in the plan's order. Nothing outside this list.

1. `src/product_hunter/llm/catalog.py` — `Availability`, `ModelIdentity`, `ModelEntry`,
   `ModelCatalog`, `CatalogError`, `MalformedCatalogError`, `parse_model_catalog`,
   `parse_model_list`, and a `CatalogDiff` (added / removed / changed).
2. `src/product_hunter/llm/opencode.py` — add `run_opencode_cli(argv, *, timeout_seconds,
   runner=subprocess.run, label="opencode api")` and make `OpenCodeGenerateProvider._call`
   use it. **The existing error messages and the class behaviour must not change.**
   `tests/test_llm_contract.py` must pass with no edits to that file.
3. `src/product_hunter/llm/discovery.py` — `ModelDiscovery` protocol,
   `OpenCodeModelDiscovery`, `CatalogRefresher`.
4. `src/product_hunter/llm/routing.py` — `ModelRole`, `SelectionSource`, `RouteCandidate`,
   `RoleRoute`, `ModelSelection`, `ModelExecution`, `NoModelAvailable`, `ModelRouter`.
5. `src/product_hunter/config.py` — `RoutingConfig`, `CatalogSettings`, `load_routing_config`.
6. `config/model_routing.yaml` — the file exactly as in the plan.
7. `db/migrations/0002_model_routing.sql` — the four tables exactly as in the plan.
8. `src/product_hunter/db/model_routing.py` — `record_catalog`, `record_selection`,
   `record_execution`, `list_inventory`, `list_executions`. Export the public ones from
   `src/product_hunter/db/__init__.py`.
9. `src/product_hunter/cli.py` — `ph models [--refresh] [--role ROLE]`, JSON output.
10. Tests — `tests/test_model_catalog.py`, `tests/test_model_discovery.py`,
    `tests/test_model_routing.py`, covering the plan's test table.

## Verified facts about this machine — use them, do not re-derive them

- `opencode v2.1.2`. The binary is
  `C:\Users\anubh\AppData\Roaming\npm\node_modules\@opencode\cli\bin\opencode.exe`.
  `shutil.which("opencode")` returns the `.CMD` shim, which is fine for a plain
  `api get` and forbidden for a prompt. `_resolve_cli()` already prefers the `.exe`.
- `opencode api get /api/model` returns
  `{"location": {...}, "data": [ {...}, ... ]}` with 624 entries. Each entry looks like:

  ```json
  {"id":"minimax-m3.1-flash-free","modelID":"minimax-m3.1-flash-free",
   "providerID":"opencode-go","family":"minimax","name":"MiniMax M3.1 Flash Free",
   "status":"active","enabled":true,"limit":{"context":1000000,"output":524288},
   "cost":[{"input":0,"output":0,"cache":{"read":0,"write":0}}],
   "time":{"released":1792368000000},"capabilities":{...},"variants":[...]}
  ```

  `time.released` is epoch milliseconds. `cost` is a list; take the first element's
  `input` and `output`. Entries also carry keys the catalog does not model
  (`package`, `settings`, `headers`, `body`, `compatibility`) — ignore them.
- `opencode api get /api/info` returns `{"version":"2.1.2","pid":31052,...}`.
- `opencode models` prints one `provider/model` per line, 624 lines, no status.
- 29 of the 624 are on `opencode-go`.

## Hard rules

- **Never** call the OpenCode server over raw HTTP. Use the `opencode api` CLI.
- **Never** use `shell=True` or a command string. Always an argv list.
- **Never** add a provider API key. Discovery rides the OpenCode auth store.
- **Never** add a dependency. `pydantic`, `PyYAML`, `httpx` and the stdlib are what
  you have.
- **Never** hard-code a model id in Python. Model ids live in `config/model_routing.yaml`
  as patterns, or nowhere.
- **Never** change a test to make it pass. The single exception, already approved and
  already recorded in the plan: `tests/test_db_migrations.py` asserts the schema
  version, which moves from 1 to 2 when `0002` is added. Change `1` to `2` in the two
  version assertions and add the four new table names to `EXPECTED_TABLES`. Keep the
  assertions exactly as strict; do not weaken anything else.
- **Never** touch a file outside this list:
  `src/product_hunter/llm/{catalog,discovery,routing,opencode,__init__}.py`,
  `src/product_hunter/{config,cli}.py`, `src/product_hunter/db/{model_routing,__init__}.py`,
  `db/migrations/0002_model_routing.sql`, `config/model_routing.yaml`,
  `tests/test_model_{catalog,discovery,routing}.py`, `tests/test_db_migrations.py`,
  `README.md`, `MODEL_ROUTING_IMPLEMENTATION.md`.
  `config/extraction.yaml` is **not** in the list. Leave it alone.
- `MODEL_ROUTING_IMPLEMENTATION.md` and the `README.md` section are written in Stage 8
  by a different node. **Do not** create them. Only Stage 4 through Stage 7 code,
  config, migration, and tests.

## Style

- Match the house style: `from __future__ import annotations`, pydantic models with
  explicit field constraints, `snake_case`, a module docstring that says what the
  module decides, and a docstring on every public function that says what it refuses.
- Fail closed with a named exception. No bare `except Exception` on the happy path.
- Ponytail: reuse what exists, then the stdlib, then an installed dependency, before
  writing anything new. The smallest change that fully solves the task. No
  abstraction nobody asked for, no config knob nobody asked for, no factory for one
  implementation. Every branch, parser and non-trivial piece of logic gets a test.
- Do not leave a `TODO`, a `NotImplementedError`, a `pass` stub, or a disabled test.
- If something in the plan is impossible as written, do the closest thing that keeps
  the requirement, and say so in your final message.

## Definition of done

- `uv run pytest` is green.
- The nine test cases the task asked for are covered: discovery, refresh, unavailable
  model, fallback, role selection, historical model preservation, malformed catalog,
  empty catalog, provider/model identity.
- A new model appearing in a refreshed catalogue is selectable with no source change.
- A model disappearing is skipped, never fatal, and its inventory row survives.
- `tests/test_llm_contract.py` passes unchanged.

## Final message

Report, in one screen: what you built per file, the gate line, anything in the plan
you changed and why, and anything you did not do.

# Dynamic Model Routing — Implementation Plan

**Status:** approved. The spec is `docs/resonix/specs/model-routing.md`.
**Date:** 2026-10-21
**Risk:** high. `db/migrations/**` is a high-risk path in `.loop/adapter.json`, so stages 2, 6 and 7 are not skippable.
**Gate:** `uv run pytest`.

---

## 1. Shape of the work

Four new pieces and one migration:

| Piece | File | Job |
|---|---|---|
| Catalog | `src/product_hunter/llm/catalog.py` | identity, availability, entries, catalog, parsing, diff |
| Discovery | `src/product_hunter/llm/discovery.py` | the OpenCode discovery surface, the TTL refresher |
| Routing | `src/product_hunter/llm/routing.py` | roles, the chain, the selector, the execution record |
| Persistence | `src/product_hunter/db/model_routing.py` + `db/migrations/0002_model_routing.sql` | catalog snapshots, inventory, selections, executions |

Plus a thin surface: `config/model_routing.yaml`, a `ph models` command, and a shared
argv runner in `opencode.py` so discovery and generation use one CLI seam.

### 1.1 Dependency direction

```
config.py ──> routing.py ──> discovery.py ──> catalog.py
                                │
opencode.py (CLI seam) ─────────┘

db/model_routing.py  ──> catalog.py, routing.py   (plain data in, plain data out)
```

`catalog.py` knows nothing about OpenCode, SQLite, or roles. `discovery.py` knows
about OpenCode but not about roles. `routing.py` knows about roles and the catalog but
not about SQLite. The persistence module takes plain objects and returns plain
objects. Every layer is testable without a subprocess or a database.

### 1.2 The persistence fork, settled

Two shapes were on the table: a new migration with typed tables, or JSON payloads in
the existing `research_events` table.

SubQ Snap was asked to weigh it (`noul` 0.976 for the new migration; one call, 295
input tokens, `subq-snap-preview`). The evidence agrees: the routing state is queried
by run, by role, and by model id; the inventory needs `first_seen_at`, `last_seen_at`
and `retired_at`, which a JSON event payload cannot carry; and Phase 2 already
established the one-table-per-concern pattern.

**Decision: new additive migration `0002_model_routing.sql` with four typed tables.**

The consequence is that `PRAGMA user_version` moves from 1 to 2, and
`tests/test_db_migrations.py` asserts the version. Those assertions change from 1 to
2 and `EXPECTED_TABLES` gains the four new names. The assertions keep their strength;
the number they pin moved because the schema moved. This is recorded here, in the
commit message, and in the run report rather than done quietly.

---

## 2. Data model

### 2.1 `catalog.py`

```python
class Availability(str, Enum):
    available = "available"
    disabled = "disabled"
    retired = "retired"
    unknown = "unknown"
    missing = "missing"


class ModelIdentity(BaseModel):        # frozen, hashable
    provider_id: str = Field(min_length=1)
    model_id: str = Field(min_length=1)

    @property
    def key(self) -> str:  # "provider/model"
        ...


class ModelEntry(BaseModel):
    identity: ModelIdentity
    name: str | None = None
    family: str | None = None
    status: str | None = None
    enabled: bool | None = None
    availability: Availability
    context_limit: int | None = None
    output_limit: int | None = None
    cost_input: float | None = None
    cost_output: float | None = None
    released_at: datetime | None = None
    source: str


class ModelCatalog(BaseModel):
    catalog_version: str
    source: str
    server_version: str | None = None
    discovered_at: datetime
    entries: list[ModelEntry]

    def by_key(self, key: str) -> ModelEntry | None: ...
    def available(self) -> list[ModelEntry]: ...
```

`catalog_version` is `sha256` over the sorted `provider/model|status|enabled|name`
lines, prefixed `sha256:`. Same models, same version; one model added or renamed,
different version.

Parsing:

```python
def parse_model_catalog(payload: str, *, source: str, server_version: str | None = None) -> ModelCatalog
def parse_model_list(payload: str, *, source: str) -> ModelCatalog
```

`parse_model_catalog` takes the `/api/model` body: `{"data":[...]}` or a bare list.
`parse_model_list` takes the `opencode models` output: one `provider/model` per line.
Both raise `MalformedCatalogError` on bad JSON, a wrong shape, an entry that is not an
object, or an entry without a usable `providerID`/`modelID`.

An empty model list is **not** malformed. It is a valid, empty catalog.

Availability is derived, not reported:

- `enabled is False` → `disabled`
- `status` present and not `active` → `retired`
- `status` absent (the plain list) → `unknown`
- `status == "active"` and `enabled is True` → `available`

Only `available` is selectable.

### 2.2 `discovery.py`

```python
class ModelDiscovery(Protocol):
    def discover(self) -> ModelCatalog: ...


class OpenCodeModelDiscovery:
    """Discover models through the documented OpenCode CLI."""
    name = "opencode"

    def __init__(self, *, timeout_seconds: int = 30, runner=subprocess.run): ...
    def discover(self) -> ModelCatalog: ...
```

Two documented surfaces, in order:

1. `opencode api get /api/model` — the server route. Primary. Carries status and
   enabled.
2. `opencode models` — the CLI list. Fallback, used only when the route fails. No
   status, so every entry it produces is `unknown` and therefore not selectable.

Both go through the same argv runner as the generation path: an argv list, `shell=False`,
never a command string, never raw HTTP.

```python
class CatalogDiff(BaseModel):
    added: list[ModelIdentity]
    removed: list[ModelIdentity]
    changed: list[ModelIdentity]


class CatalogRefresher:
    def __init__(self, discovery, *, refresh_interval_seconds: int = 900, now=utcnow): ...
    def catalog(self, *, force: bool = False) -> ModelCatalog: ...
    def refresh(self) -> tuple[ModelCatalog, CatalogDiff]: ...
    @property
    def last_error(self) -> str | None: ...
```

`catalog()` re-discovers when the cached catalog is older than the interval. A failed
discovery keeps the previous catalog and records the error in `last_error`; it raises
only when there is no catalog at all.

### 2.3 `routing.py`

```python
class ModelRole(str, Enum):
    extraction, classification, normalization, deduplication,
    entity_resolution, clustering, validation, investigation,
    competitor_research, counter_evidence, scoring, reporting, synthesis


class SelectionSource(str, Enum):
    config = "config"      # an explicit provider/model binding
    pattern = "pattern"    # matched the live catalog
    default = "default"    # the global default from config/extraction.yaml


class RouteCandidate(BaseModel):
    kind: Literal["model", "pattern"]
    value: str


class ModelSelection(BaseModel):
    role: ModelRole
    identity: ModelIdentity
    selection_source: SelectionSource
    fallback_index: int
    is_fallback: bool
    catalog_version: str
    resolved_at: datetime


class ModelExecution(BaseModel):
    execution_id: str
    run_id: str | None
    stage: str | None
    role: ModelRole
    provider_id: str
    model_id: str
    selection_source: SelectionSource
    fallback_index: int
    is_fallback: bool
    catalog_version: str
    prompt_version: str | None
    config_version: str
    latency_ms: int
    status: str          # "ok" | "error"
    error: str | None
    created_at: str
```

`RoleRoute` was dropped in review: `RoutingConfig.roles` is already a validated
map of role to candidates, and the one-use wrapper checked nothing new.

The router:

```python
class ModelRouter:
    def __init__(self, config: RoutingConfig, *, default: ModelIdentity, catalog_source): ...
    def select(self, role: ModelRole, *, exclude: Iterable[ModelIdentity] = ()) -> ModelSelection: ...
    def chain(self, role: ModelRole) -> list[ModelSelection]: ...
```

`chain()` expands the role's candidates against the live catalog and returns the
ordered, available selections. `select()` returns the first one, or raises
`NoModelAvailable` naming the role.

Ordering inside a pattern: pattern position, then cost ascending with unknown cost
last, then model id, then provider id. The last key was added in review: without
it, two providers offering the same model id at the same price tied, and the
order followed the server's listing order. The result is deterministic and does
not depend on the order the server happened to list the models in.

### 2.4 `config.py`

```python
class RoutingConfig(BaseModel):
    version: int
    refresh_interval_seconds: int
    discovery_timeout_seconds: int
    roles: dict[ModelRole, list[RouteCandidate]]

def load_routing_config(path: Path | None = None) -> RoutingConfig
```

`config/model_routing.yaml`:

```yaml
version: 1
catalog:
  refresh_interval_seconds: 900
  discovery_timeout_seconds: 30
roles:
  extraction:
    - pattern: "opencode-go/*-flash*"
    - pattern: "opencode-go/*"
  classification:
    - pattern: "opencode-go/*-flash*"
    - pattern: "opencode-go/*"
  normalization:
    - pattern: "opencode-go/*-flash*"
    - pattern: "opencode-go/*"
  deduplication:
    - pattern: "opencode-go/*-flash*"
    - pattern: "opencode-go/*"
  entity_resolution:
    - pattern: "opencode-go/*"
  clustering:
    - pattern: "opencode-go/*"
  validation:
    - pattern: "opencode-go/*"
  investigation:
    - pattern: "opencode-go/deepseek*"
    - pattern: "opencode-go/*"
  competitor_research:
    - pattern: "opencode-go/deepseek*"
    - pattern: "opencode-go/*"
  counter_evidence:
    - pattern: "opencode-go/deepseek*"
    - pattern: "opencode-go/*"
  scoring:
    - pattern: "opencode-go/deepseek*"
    - pattern: "opencode-go/*"
  reporting:
    - pattern: "opencode-go/*"
  synthesis:
    - pattern: "opencode-go/deepseek*"
    - pattern: "opencode-go/*"
```

Not one model id in the file. The fast roles take a flash model, the reasoning roles
take a deep model, and both patterns pick up whatever the catalogue holds at the
time. A new `opencode-go/mimo-v3-flash` is picked up by the extraction role with no
edit anywhere.

`config/extraction.yaml` is not touched. Its `provider_id` and `model_id` remain the
global default that terminates every chain.

### 2.5 The migration

`db/migrations/0002_model_routing.sql`, additive only:

```sql
CREATE TABLE model_catalogs (
    catalog_version TEXT PRIMARY KEY NOT NULL,
    source          TEXT NOT NULL,
    server_version  TEXT,
    model_count     INTEGER NOT NULL DEFAULT 0,
    discovered_at   TEXT NOT NULL
);

CREATE TABLE model_inventory (
    provider_id     TEXT NOT NULL,
    model_id        TEXT NOT NULL,
    name            TEXT,
    family          TEXT,
    status          TEXT,
    enabled         INTEGER,
    availability    TEXT NOT NULL,
    source          TEXT,
    first_seen_at   TEXT NOT NULL,
    last_seen_at    TEXT NOT NULL,
    retired_at      TEXT,
    catalog_version TEXT,
    PRIMARY KEY (provider_id, model_id)
);

CREATE TABLE model_role_bindings (
    role             TEXT NOT NULL,
    position         INTEGER NOT NULL,
    provider_id      TEXT NOT NULL,
    model_id         TEXT NOT NULL,
    selection_source TEXT NOT NULL,
    catalog_version  TEXT,
    config_version   TEXT NOT NULL,
    selected_at      TEXT NOT NULL,
    PRIMARY KEY (role, position)
);

CREATE TABLE model_executions (
    execution_id     TEXT PRIMARY KEY NOT NULL,
    run_id           TEXT,
    stage            TEXT,
    role             TEXT NOT NULL,
    provider_id      TEXT NOT NULL,
    model_id         TEXT NOT NULL,
    selection_source TEXT NOT NULL,
    fallback_index   INTEGER NOT NULL DEFAULT 0,
    is_fallback      INTEGER NOT NULL DEFAULT 0,
    catalog_version  TEXT,
    prompt_version   TEXT,
    config_version   TEXT,
    latency_ms       INTEGER NOT NULL DEFAULT 0,
    status           TEXT NOT NULL,
    error            TEXT,
    created_at       TEXT NOT NULL
);
```

Plus indexes on `model_executions(run_id, created_at)` and
`model_executions(role, created_at)`.

`model_inventory` rows are never deleted. A model that leaves the catalogue keeps its
row, gets `retired_at` set, and moves to `availability = 'missing'`. That is how a
model id from last month is still resolvable.

### 2.6 `db/model_routing.py`

```python
def record_catalog(conn, catalog: ModelCatalog, *, now=None) -> CatalogRecord
def record_selection(conn, selection: ModelSelection, *, config_version: str, now=None) -> None
def record_execution(conn, execution: ModelExecution) -> None
def list_inventory(conn, *, availability: str | None = None) -> list[InventoryRow]
def list_executions(conn, *, run_id: str | None = None, role: str | None = None) -> list[ExecutionRow]
```

`record_catalog` upserts the catalog row, upserts every entry, and retires the
inventory rows that are no longer in the catalogue. It is one transaction.

---

## 3. The shared CLI seam

`opencode.py` grows one function, and the generation provider uses it:

```python
def run_opencode_cli(argv: list[str], *, timeout_seconds: int, runner=subprocess.run, label: str = "opencode api") -> str
```

`OpenCodeGenerateProvider._call` becomes a call to it. The error messages are
unchanged, so `tests/test_llm_contract.py` passes without edits, including the
monkeypatch on `product_hunter.llm.opencode._resolve_cli` and the refusal of a
`.cmd` shim for prompts.

`discovery.py` imports `run_opencode_cli` and `_resolve_cli` from `opencode.py` and
uses them for the route and the list. Both calls are argv lists with `shell=False`.

---

## 4. The command surface

```
ph models [--refresh] [--role ROLE]
```

- No flag: the current catalogue as JSON, with the catalog version, the source, the
  server version, and every model with its availability.
- `--refresh`: force one re-discovery before printing.
- `--role ROLE`: print the resolved chain for that role, the selected model, and
  where the selection came from.

Output is JSON, like every other command in this CLI.

---

## 5. Task list

| # | Task | Files |
|---|---|---|
| T1 | catalog types, parsing, availability, diff | `llm/catalog.py` |
| T2 | shared argv runner | `llm/opencode.py` |
| T3 | discovery + refresher | `llm/discovery.py` |
| T4 | roles, chain, router, execution record | `llm/routing.py` |
| T5 | routing config loader + YAML | `config.py`, `config/model_routing.yaml` |
| T6 | migration + persistence functions | `db/migrations/0002_model_routing.sql`, `db/model_routing.py`, `db/__init__.py` |
| T7 | `ph models` | `cli.py` |
| T8 | tests | `tests/test_model_catalog.py`, `tests/test_model_discovery.py`, `tests/test_model_routing.py` |
| T9 | version-assertion update | `tests/test_db_migrations.py` |
| T10 | docs | `MODEL_ROUTING_IMPLEMENTATION.md`, `README.md` |

`llm/__init__.py` gains `build_router` next to `build_generator`. `build_generator`
is untouched.

---

## 6. Test plan

| Test | What it pins |
|---|---|
| `test_discovery_parses_the_model_route` | the route payload becomes a catalog with the right identities |
| `test_discovery_falls_back_to_the_model_list` | the route fails, the list is used, entries are `unknown` |
| `test_discovery_rejects_a_malformed_payload` | bad JSON and a missing provider id are refused |
| `test_empty_catalog_is_valid` | an empty catalogue parses; selection raises `NoModelAvailable` |
| `test_refresh_reports_added_and_removed` | the diff names what changed |
| `test_refresh_keeps_the_previous_catalog_on_failure` | a failed discovery does not wipe the catalogue |
| `test_refresh_happens_after_the_interval` | the TTL forces a re-discovery |
| `test_a_disabled_model_is_not_selected` | `disabled` is not selectable |
| `test_a_retired_model_is_not_selected` | `retired` is not selectable |
| `test_a_missing_model_does_not_raise` | a model gone from the catalogue is skipped, not fatal |
| `test_a_new_model_is_selected_without_a_code_change` | a refreshed catalogue adds a model and the role picks it up |
| `test_fallback_moves_to_the_next_candidate` | the chain falls through and the selection is flagged as a fallback |
| `test_every_role_resolves` | all thirteen roles resolve against a fixture catalogue |
| `test_an_unknown_role_is_refused` | a bad role raises |
| `test_identity_separates_providers` | the same model id on two providers is two models |
| `test_the_inventory_keeps_a_vanished_model` | the row survives with `retired_at` set |
| `test_the_execution_row_carries_the_provenance` | every required field is stored |
| `test_the_selection_is_persisted` | the role binding is written and readable |
| `test_the_generation_bridge_still_works` | the existing contract test passes unchanged |

---

## 7. Out of scope

SubQ. Benchmarking. Per-role cost accounting. Any change to the extraction prompt,
the quote rules, the OpenCode generation route, or `config/extraction.yaml`.

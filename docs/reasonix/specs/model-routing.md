# Dynamic Model Routing — Specification

**Status:** approved for implementation.
**Date:** 2026-10-21
**Source of truth:** `docs/reasonix/specs/Product-Hunter-Full-Spec.md` §3 (Model Strategy), §38A Phase 6 (model routing), §34 (Observability).
**Lane:** backend / research infrastructure. No UI, so no `DESIGN.md` is required.
**Builds on:** Phase 1 extraction seam (`src/product_hunter/llm/`), Phase 2 evidence core (`db/migrations/0001_initial.sql`).

---

## 1. What this change proves

Phase 1 pinned one model in `config/extraction.yaml`. That is fine for a hand-proved
extraction pipeline and wrong for a system that runs unattended: free-model
availability changes without notice, and a run that assumed a model existed either
crashes or silently produces nothing.

This change makes the model a runtime decision. The system discovers which models
OpenCode actually offers, records their availability, binds each pipeline role to a
chain of candidates, and writes down which model ran which piece of work.

The proof is not "it selects a model". The proof is the four properties below.

| # | Property | How it is proved |
|---|---|---|
| P1 | A model that becomes available is usable without a source-code edit | catalog refresh, then a pattern-matched role picks the new model |
| P2 | A model that disappears does not crash the pipeline | the role falls through its chain; a vanished model is recorded, not deleted |
| P3 | Historical model ids survive | `model_inventory` rows are retired, never removed |
| P4 | Every model execution is attributable | `model_executions` carries provider, model, role, source, fallback, catalog version, prompt/config version, latency, outcome, run, stage |

---

## 2. Constraints

From the task and `.loop/adapter.json`, all binding:

- No permanent model list in source. A model appearing or disappearing is a catalog
  event, not a code change.
- `DeepSeek V4.1 Flash` is not the architecture. It is one candidate among many.
- The OpenCode generation bridge stays compatible. `OpenCodeGenerateProvider`,
  `ph doctor`, and `ph extract` keep their current behaviour and signatures.
- Discovery uses a documented OpenCode surface. No invented API, no raw HTTP to the
  OpenCode server, no `shell=True`, no command string.
- No provider API keys are added. Discovery rides the OpenCode auth store.
- No SubQ integration. No benchmarking. Only the interfaces and tests those need.

---

## 3. The discovery surface, verified

Checked on this machine against `opencode v2.1.2`, not assumed.

| Surface | Command | Result | Verdict |
|---|---|---|---|
| Server route | `opencode api get /api/model` | 492 KB JSON, 624 entries, ~240 ms | primary |
| Server info | `opencode api get /api/info` | `{"version":"2.1.2","pid":31052,...}` | catalog version source |
| CLI list | `opencode models` | 624 lines of `provider/model`, ~200 ms | fallback |

The route and the CLI list report the same 624 models. The route is preferred because
each entry carries `status` and `enabled`, which the plain list does not:

```json
{"id":"minimax-m3.1-flash-free","modelID":"minimax-m3.1-flash-free",
 "providerID":"opencode-go","family":"minimax","name":"MiniMax M3.1 Flash Free",
 "status":"active","enabled":true,"limit":{"context":1000000,"output":524288},
 "cost":[{"input":0,"output":0,"cache":{"read":0,"write":0}}],
 "time":{"released":1792368000000},"capabilities":{...},"variants":[...]}
```

Twenty-nine of the 624 are on `opencode-go`, the free tier this project runs on. The
model ids differ per provider: `opencode-go/deepseek-v4.1-flash`,
`nvidia/deepseek-ai/deepseek-v4.1-flash`, and `tokenharbor/deepseek-v4.1-flash` are
three different models. A model id alone is not an identity.

### 3.1 There is no instant model event

OpenCode exposes no documented event for "the model set changed". The CLI is a
request/response client; the server publishes no catalog-change feed the CLI
surfaces. Polling is the only supported mechanism.

The safe refresh therefore has three parts, and the limitation is stated in the
implementation document rather than hidden:

1. **TTL refresh** — a cached catalog older than `refresh_interval_seconds` is
   re-discovered on next use.
2. **Refresh on miss** — a role whose chain matches nothing available triggers one
   immediate re-discovery before it gives up.
3. **Refresh on demand** — `ph models --refresh` forces one.

A failed refresh keeps the previous catalog. Discovery failure is never fatal to a
running pipeline.

---

## 4. Domain model

### 4.1 Identity

`ModelIdentity` is `(provider_id, model_id)`. Its key is `provider/model`. Two
providers may offer the same model id; those are two models, and the catalog, the
inventory, and the execution log all key on the pair.

### 4.2 Availability

| State | Meaning | Selectable |
|---|---|---|
| `available` | present, `status == "active"`, `enabled == true` | yes |
| `disabled` | present, `enabled == false` | no |
| `retired` | present, `status` is not `active` | no |
| `unknown` | present, the source reported no status | no |
| `missing` | known from an earlier catalog, gone from this one | no |

Only `available` is selectable. `unknown` is deliberately not selectable: the
fallback CLI list reports no status, and a model whose usability was never confirmed
must not be handed to the pipeline.

### 4.3 Catalog

A `ModelCatalog` is one immutable snapshot: the entries, the source that produced
them, the OpenCode server version, the discovery time, and a `catalog_version`.

`catalog_version` is a content digest over the sorted `provider/model|status|enabled|
name` lines. It is stable for an unchanged catalog and changes the moment the model
set changes, which is what makes it useful as provenance.

---

## 5. Roles and selection

### 5.1 Roles

Thirteen roles, taken from §3 and §5 of the full spec:

`extraction`, `classification`, `normalization`, `deduplication`, `entity_resolution`,
`clustering`, `validation`, `investigation`, `competitor_research`,
`counter_evidence`, `scoring`, `reporting`, `synthesis`.

A role is a pipeline job, not a model. Two roles may resolve to the same model; the
role is what the execution log records.

### 5.2 The chain

Each role owns an ordered chain of candidates. A candidate is one of:

- **explicit** — a `provider/model` pair, an operator's deliberate choice.
- **pattern** — a glob over the live catalog, e.g. `opencode-go/*-flash*`. A pattern
  is what makes P1 work: a new model that matches is selected with no edit anywhere.

The default provider/model from `config/extraction.yaml` terminates every chain as
the last resort.

### 5.3 Resolution

1. Expand the chain. Explicit candidates contribute their identity. Pattern candidates
   contribute every available catalog entry that matches, ordered by pattern position,
   then by cost ascending (unknown cost last), then by model id.
2. Walk the expansion in order and take the first available candidate.
3. If nothing is available, take the default identity if it is available.
4. If the default is unavailable too, raise `NoModelAvailable`. Fail closed, with a
   message naming the role. Never crash.

The result is a `ModelSelection`:

| Field | Meaning |
|---|---|
| `role` | the pipeline role |
| `identity` | the chosen `provider/model` |
| `selection_source` | `config` (explicit binding), `pattern` (matched the live catalog), `default` (the global default) |
| `fallback_index` | position in the resolved chain; 0 is the primary |
| `is_fallback` | true for any index above 0 |
| `catalog_version` | the catalog the choice was made against |
| `resolved_at` | when the choice was made |

### 5.4 Runtime fallback

A model that is available in the catalog can still fail at call time. The router
accepts an exclusion set, so a caller that got a provider error can ask for the next
candidate in the chain and record the attempt as a fallback. The failed identity stays
in the execution log.

---

## 6. Provenance and persistence

`model_executions` records one row per model call:

`execution_id`, `run_id`, `stage`, `role`, `provider_id`, `model_id`,
`selection_source`, `fallback_index`, `is_fallback`, `catalog_version`,
`prompt_version`, `config_version`, `latency_ms`, `status`, `error`, `created_at`.

Three more tables carry the state:

| Table | Holds | Key |
|---|---|---|
| `model_catalogs` | each catalog snapshot that was seen | `catalog_version` |
| `model_inventory` | every model ever seen, with first/last seen and retirement | `(provider_id, model_id)` |
| `model_role_bindings` | the selection made for a role | `(role, position)` |

`model_inventory` rows are never deleted. A model that leaves the catalog gets
`retired_at` set and `availability` moved to `missing`. This is P3: the id that ran
last month's extraction is still resolvable next month.

The migration is `db/migrations/0002_model_routing.sql`. `db/migrations/**` is a
high-risk path in `.loop/adapter.json`, so the risk floor for this change is high
and stages 2, 6, and 7 are not skippable.

---

## 7. Configuration

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
  investigation:
    - pattern: "opencode-go/deepseek*"
    - pattern: "opencode-go/*"
```

No model id is written in Python. The YAML is the only place an operator names a
model, and a pattern needs no edit when the catalogue gains a matching model.

The role file carries a `version`, and the effective `config_version` recorded on
every execution is that version plus a digest of the role bindings, so a routing
change is visible in the log.

---

## 8. Acceptance

| # | Requirement | Test |
|---|---|---|
| A1 | discovery | route payload parses into a catalog; the CLI list is the fallback |
| A2 | refresh | TTL forces a re-discovery; the diff reports added, removed, changed |
| A3 | unavailable model | a disabled or retired model is not selected |
| A4 | fallback | an unavailable primary hands the role to the next candidate, flagged as a fallback |
| A5 | role selection | every one of the thirteen roles resolves; an unknown role is refused |
| A6 | historical model preservation | a model that disappears keeps its inventory row |
| A7 | malformed catalog | bad JSON and a missing provider id are refused |
| A8 | empty catalog | an empty catalogue is valid; selection raises `NoModelAvailable` |
| A9 | provider/model identity | the same model id on two providers is two models |
| A10 | generation bridge compatibility | the existing `tests/test_llm_contract.py` still passes unchanged |

---

## 9. Non-goals

- SubQ. Not implemented, not stubbed.
- Benchmarking. No scoring of model quality, only the interface a later phase uses.
- Per-role cost accounting. `spend_ledger` stays as it is.
- Changing the OpenCode generation route, the extraction prompt, or the quote
  verification rules.
- Rewriting `config/extraction.yaml`. Phase 1 behaviour is untouched.

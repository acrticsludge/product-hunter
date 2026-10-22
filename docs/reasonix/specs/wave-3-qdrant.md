# Wave 3 spec: the semantic retrieval layer (Qdrant)

**Run ID:** wave-3-qdrant-2026-10-22
**Branch:** `wave3/qdrant-semantic`
**Base:** `00a6ca3` (the Wave 2 integration, `edd557e` plus the review fixes)
**Risk:** declared `medium`, adapter floor `medium`
**Adapter:** Product Hunter, 8 forbidden rules in force
**Specialization:** backend. No user interface is touched.

## 1. The goal

Product Hunter keeps one authoritative store, SQLite. Everything else is
derived. This wave adds the derived semantic index: the place where a
question in words is answered with the evidence that means the same thing.

Six capabilities depend on it:

- semantic evidence retrieval
- similar pain-point discovery
- duplicate and candidate discovery
- cluster assistance
- investigation context retrieval
- historical opportunity comparison

The index is a projection. It never becomes a second source of truth, and it
never decides anything the SQLite store has already decided.

## 2. What the repository already has

The Wave 2 tree already holds most of what this wave needs, and reusing it
shapes the design.

| What exists | Where | How this wave uses it |
|---|---|---|
| A vector space identity | `clustering/similarity.py`: `VectorSpace`, `space_id()`, `Embedder` | The index reuses it. There is one notion of a vector space in the repository. |
| A local embedder | `clustering/similarity.py`: `SentenceTransformerEmbedder` | The index embeds with it. The model is named in config, never in code. |
| A canonical text form for a pain point | `clustering/problems.py`: `canonical_text()` | The index embeds the same text the clustering wave compares. |
| A transactional outbox | `db/outbox.py`: `enqueue_projection`, `process_outbox` | The index is fed by a `target="qdrant"` task in the same transaction as the evidence. |
| A projection pattern | `projection/` | The index is written the way the Obsidian vault is written: read the whole state, write what changed, remove what is gone. |
| A Qdrant MCP server | `opencode.json` (`COLLECTION_NAME=product_hunter_evidence`) | The index writes into a collection the agent's MCP server can read. |

What does not exist: the `qdrant-client` package, a reachable Qdrant server,
and the `sentence-transformers` package. The adapter therefore speaks the
Qdrant REST API over `httpx`, which is already a dependency, and the tests
drive it through an `httpx` transport seam. No live Qdrant connection is
fabricated anywhere in this wave.

## 3. The rules this wave holds to

### 3.1 SQLite is authoritative

A vector is derived work. The authoritative row is written first, in its own
transaction, together with the projection task that will index it. A Qdrant
failure never rolls back committed evidence, and it never corrupts a row.

### 3.2 Two vector spaces are never blended

The space id is a digest of the embedding provider, model, model version and
dimension count. It is written into the collection metadata, into every
payload, and into every search filter.

When the configured model changes, the space id changes with it. The index
then refuses to write: it raises `EmbeddingSpaceMismatch`, naming both spaces
and the model that produced each. The operator runs a rebuild, which drops
the collection, recreates it under the new space, and re-embeds everything.
There is no path in this wave that writes a vector of one space into a
collection that holds another.

### 3.3 The index is a projection

The index is rebuilt from SQLite, not patched in place. A rebuild is
idempotent, and a partial index is a normal state rather than a broken one:
a run that dies half way leaves the vectors it wrote and the task still
pending, and the retry converges.

### 3.4 No secret is read, written, printed or logged

The Qdrant API key is read from the environment variable the config names.
The config holds the variable's name, never its value. The key travels in
the `api-key` header and appears in no log line, no payload, no error
message and no report.

## 4. The shape of the code

A new package, `src/product_hunter/semantic/`, beside the other wave
packages.

| Module | Holds |
|---|---|
| `vectors.py` | The entity types, the deterministic point id, the payload model, the search filter. |
| `embeddings.py` | The embedder the config names, and the batch embed with its dimension guard. |
| `qdrant.py` | The adapter: the HTTP transport, the collection lifecycle, the point operations. |
| `source.py` | The reader: the authoritative rows as indexable entities. |
| `index.py` | The index: sync, rebuild, search, and the outbox handler. |

`config/semantic.yaml` holds the operator-tunable values. The root
`config.py` gains `SemanticConfig` and `load_semantic_config()`, beside the
existing `ClusteringConfig` and `load_clustering_config()`.

The CLI gains `ph index`, with `sync`, `rebuild`, `search` and `status`
subcommands, beside the existing `ph project`.

## 5. The adapter

`QdrantAdapter` speaks the Qdrant REST API. It holds no product state: the
collection name, the vector size and the distance are arguments, and the
space belongs to the collection metadata.

| Method | Call |
|---|---|
| `list_collections()` | `GET /collections` |
| `collection_info(name)` | `GET /collections/{name}` |
| `create_collection(name, size, distance, metadata)` | `PUT /collections/{name}` |
| `delete_collection(name)` | `DELETE /collections/{name}` |
| `create_payload_index(name, field)` | `PUT /collections/{name}/index?wait=true` |
| `upsert(name, points)` | `PUT /collections/{name}/points?wait=true` |
| `delete_points(name, ids)` | `POST /collections/{name}/points/delete?wait=true` |
| `search(name, vector, limit, filter)` | `POST /collections/{name}/points/query` |
| `scroll(name, limit, filter, with_vector)` | `POST /collections/{name}/points/scroll` |
| `count(name, filter)` | `POST /collections/{name}/points/count` |
| `get_point(name, id)` | `GET /collections/{name}/points/{id}` |

The search call falls back to `POST /collections/{name}/points/search` when
the server answers 404, because the current endpoint arrived in Qdrant 1.10
and the older one still answers on every server since 1.0.

### 5.1 Failure handling

| Condition | Outcome |
|---|---|
| Connection refused, DNS failure, timeout | `QdrantUnavailable`, after the configured attempts |
| 429, 500, 502, 503, 504 | retried with exponential backoff, then `QdrantRequestFailed` |
| 400, 401, 403, 404 on a write | `QdrantRequestFailed`, no retry |
| 409 on collection create | the collection already exists, which is not an error here |

A 401 and a 403 are refusals, not faults. Retrying them wastes the budget and
hides a wrong key behind a timeout.

### 5.2 The retry budget

`max_attempts` and `backoff_seconds` come from the config. The backoff is
`backoff_seconds * 2 ** attempt`, capped at 60 seconds. The adapter holds no
clock of its own: the sleep function is an argument, so a test drives the
retry without waiting.

## 6. What a vector is

### 6.1 The entity types

| Type | The entity | The text that is embedded |
|---|---|---|
| `evidence` | one evidence span | the quote |
| `pain_point` | one accepted pain point | `canonical_text()` of the pain point, the same text the clustering wave compares |
| `cluster` | one active cluster | the centroid of the member pain-point vectors already in the index |

### 6.2 The point id

```
vector_id(entity_type, entity_id) = uuid(sha256(entity_type + "\x1f" + entity_id))
```

The id is a digest of the entity type and the entity id alone. The content
hash is not in it, because the point is the entity and the content is what
the payload says about the entity. So a re-index of a changed entity lands on
the same point and replaces it, instead of leaving the old vector beside the
new one.

The id is a UUID because Qdrant accepts a UUID or an unsigned integer, and a
UUID keeps the id stable across processes and languages.

### 6.3 The payload

Every point carries these fields. Nothing else is stored, and no field holds
a secret.

| Field | Why it is there |
|---|---|
| `entity_id` | Which entity the vector is |
| `entity_type` | Which kind of entity it is |
| `content_hash` | Which content was embedded, so a change is visible |
| `embedding_provider` | Which provider made the vector |
| `embedding_model` | Which model made the vector |
| `embedding_model_version` | Which version of that model |
| `space_id` | Which vector space, so a mixed collection cannot answer a query |
| `dimensions` | How wide the vector is |
| `index_version` | Which index wrote the point |
| `created_at` | When the entity was created, taken from the authoritative row |
| `updated_at` | When the entity was last updated, taken from the authoritative row |
| `source_id` | The source the entity came from, for filtering |
| `community_id` | The community the entity came from, for filtering |
| `domain` | The domain of the pain point, for filtering |
| `cluster_id` | The cluster the entity belongs to, for filtering |
| `live_status` | Whether the evidence is still live, for filtering |

`created_at` and `updated_at` are the authoritative row's own timestamps, not
the moment the index wrote the point. A wall-clock stamp in the payload would
make the index non-deterministic, and a derived index that cannot be rebuilt
into the same bytes is harder to reason about than one that can.

The payload indexes Qdrant holds are on `entity_type`, `source_id`,
`community_id`, `domain`, `cluster_id`, `live_status` and `space_id`.

## 7. The index

### 7.1 Preparing the collection

`ensure_ready()` creates the collection when it is missing, with the vector
size and the distance from the config, and the space id in the collection
metadata. When the collection is already there, the metadata is read back and
compared with the current space.

- The space id differs: raise `EmbeddingSpaceMismatch`.
- The vector size differs: raise `EmbeddingSpaceMismatch`.

Both cases say what has to happen next, which is a rebuild.

### 7.2 Syncing

`sync()` reconciles the index with the authoritative state.

1. Read the entities to index: the live evidence spans, the accepted pain
   points, the active clusters. A caller may name a subset, which is how a
   projection task indexes only what its run produced.
2. For each entity, read the payload the index already holds.
3. An entity whose payload is already the same is skipped. The comparison is
   on the whole payload except `updated_at`, so a cluster that gained a member
   is re-indexed even though its vector did not change.
4. The rest are embedded in batches and upserted.
5. The points whose entity is gone, or whose entity is no longer live, are
   deleted. This is the delete synchronisation: a document that closed takes
   its evidence vectors with it.

The report says how many were indexed, how many were unchanged, how many were
removed, and which entities failed.

### 7.3 Rebuilding

`rebuild()` deletes the collection, recreates it under the current space, and
syncs everything. It is the answer to a space mismatch, and the way to
recover from an index that was written by hand or by an older wave.

### 7.4 Searching

`search()` embeds the query, then searches the collection with a filter that
always carries the current space id, plus whatever the caller asked for.

| Filter | Field |
|---|---|
| `entity_type` | `entity_type` |
| `source_id` | `source_id` |
| `community_id` | `community_id` |
| `domain` | `domain` |
| `cluster_id` | `cluster_id` |
| `live_status` | `live_status` |
| `since`, `until` | a range on `created_at` |

Each filter is a `must` clause, so they narrow rather than widen. The score
threshold is a separate argument, because a score is not a filter.

### 7.5 The projection task

The store enqueues a `target="qdrant"` task in the same transaction as the
evidence it imported. The payload names the run and the entities the run
produced.

```json
{"run_id": "run-test", "evidence": ["ev_..."], "pain_points": ["pp_..."]}
```

The handler reads the payload and syncs exactly those entities. A task whose
entities are already indexed costs one read per entity and writes nothing.
A Qdrant failure leaves the task pending, and the outbox schedules the
retry, exactly as the Obsidian projection does.

The idempotency key is `semantic_index:{run_id}:{content_digest}`, where the
digest covers the entity ids. A replay of the same run produces the same
key, so the task is not enqueued twice.

## 8. The configuration

`config/semantic.yaml`:

```yaml
version: 1
index_version: semantic-v1
collection: product_hunter_evidence
url: http://localhost:6333
api_key_env: QDRANT_API_KEY
embedding:
  provider: local
  model: sentence-transformers/all-MiniLM-L6-v2
  model_version: "1"
  dimensions: 384
distance: Cosine
timeout_seconds: 30
max_attempts: 4
backoff_seconds: 1
batch_size: 64
```

The collection name matches the one the Qdrant MCP server in `opencode.json`
is pointed at, so the agent can read the index the code writes.

`url` and `collection` are overridable by `PH_QDRANT_URL` and
`PH_QDRANT_COLLECTION`. The API key is read from the environment variable
`api_key_env` names. The URL is checked before use: the scheme has to be
`http` or `https`, and userinfo is refused.

The embedding block names the same model the clustering config names, and it
is a separate block on purpose. The two waves move independently, and a
divergence between them is loud: each guards its own space, and a cluster
vector in one space is never compared with a pain point vector in another.

## 9. The CLI

```
ph index sync     [--db PATH] [--json]
ph index rebuild  [--db PATH] [--json]
ph index search   --text TEXT [--limit N] [--source ID] [--community ID]
                  [--entity-type TYPE] [--domain NAME] [--cluster ID]
                  [--since DATE] [--until DATE] [--db PATH] [--json]
ph index status   [--db PATH] [--json]
```

`sync` reconciles, `rebuild` replaces the collection, `search` answers a
question in words, and `status` reports the space, the collection, the point
count and the entities the store holds. Every command prints one JSON object
and returns 0 or 1, the way the other commands in this repository do.

## 10. The tests

| Case | What it proves |
|---|---|
| The point id is deterministic | The same entity always lands on the same point |
| Two entity types with one id differ | The entity type is part of the id |
| An unchanged entity is skipped | A second sync writes nothing |
| The same entity indexed twice writes one point | The upsert is idempotent |
| A changed entity replaces its point | The old vector does not survive |
| A changed cluster is re-indexed | The whole payload is compared, not only the hash |
| A deleted entity loses its point | The delete synchronisation works |
| A closed document takes its evidence with it | Liveness is honoured |
| A model change raises `EmbeddingSpaceMismatch` | Two spaces are never blended |
| A rebuild clears the mismatch | The reindex path works |
| A refused connection raises `QdrantUnavailable` | A failure is not a success |
| A 500 is retried and then succeeds | The retry works |
| A 401 is not retried | A refusal is not a fault |
| A filter narrows the result | The filtering works |
| A filter on the space id excludes another space | The guard holds under search |
| A partial index converges on the next run | The recovery works |
| A projection task indexes only its entities | The task integration works |
| A failed projection task stays pending | The outbox owns the retry |
| The API key is in no payload, error or log | The secret rule holds |

## 11. What this wave does not do

- No scoring change.
- No discovery change.
- No Obsidian change.
- No scheduler change.
- No schema migration. The outbox table already carries a target.
- No new runtime dependency. The adapter uses `httpx`, which is already
  installed.
- No live Qdrant call, and no claim that one succeeded.

## 12. The known limits

1. **The embedding model never ran here.** `sentence-transformers` is not
   installed, so the tests inject a deterministic embedder through the
   `Embedder` protocol the clustering wave already defines. The production
   path is the same call with a different object behind it.
2. **The Qdrant server never ran here.** The tests drive the adapter through
   an `httpx` transport that answers with the real wire format. No test
   fabricates a successful live connection, and the report says so.
3. **The semantic layer imports the clustering layer.** The vector space
   identity and the embedder live in `clustering/similarity.py`, and the
   semantic package imports them. One notion of a vector space is worth more
   than a clean dependency direction, but the coupling is real and a later
   wave may want to move the shared primitives into their own module.
4. **The embedding block is declared twice.** The clustering config and the
   semantic config each name the model. The space guard makes a divergence
   loud rather than silent.
5. **The outbox task carries the entity ids in its payload.** A run with many
   candidates makes a large payload. The alternative, one task per entity,
   makes many rows; the payload was chosen because the task count stays at
   one per run.
6. **The cluster vector is the centroid of the members already in the
   index.** A cluster whose members are not indexed has no vector. That is
   the partial-index state, and the next sync fills it in.

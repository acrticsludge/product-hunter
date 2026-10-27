# Wave 3 plan: the semantic retrieval layer (Qdrant)

**Run ID:** wave-3-qdrant-2026-10-22
**Branch:** `wave3/qdrant-semantic`
**Spec:** [../specs/wave-3-qdrant.md](../specs/wave-3-qdrant.md)

## 1. The order of the work

The order follows the dependency, not the excitement. Nothing is written
before the thing it depends on can be read, and nothing is tested before it
exists.

| # | Task | Produces | Needs |
|---|---|---|---|
| 1 | The configuration | `config/semantic.yaml`, `SemanticConfig`, `load_semantic_config()` | nothing |
| 2 | The vector identity | `semantic/vectors.py` | 1 |
| 3 | The embedder | `semantic/embeddings.py` | 1 |
| 4 | The adapter | `semantic/qdrant.py` | 1 |
| 5 | The reader | `semantic/source.py` | 2 |
| 6 | The index | `semantic/index.py` | 2, 3, 4, 5 |
| 7 | The write path | the store's qdrant projection task | 6 |
| 8 | The command | `ph index` | 6, 7 |
| 9 | The tests | three test files | 1 to 8 |

## 2. The tasks in detail

### Task 1: the configuration

`config/semantic.yaml` holds the values the operator can change. The root
`config.py` gains a `SemanticConfig` model and a `load_semantic_config()`
loader, beside the clustering pair, and reuses `EmbeddingConfig` for the
embedding block so the two waves agree on the shape of a model identity.

The loader refuses a file it cannot use, the way `load_clustering_config()`
does. `PH_QDRANT_URL` and `PH_QDRANT_COLLECTION` override the file.

**Done when:** a valid file loads, a missing file raises the config error, an
unknown key is refused, and the URL is checked for its scheme and for
userinfo.

### Task 2: the vector identity

`semantic/vectors.py` holds the entity types, the point id, the payload model
and the search filter.

The point id is a UUID built from the digest of the entity type and the
entity id. The payload model is a pydantic model with `extra="forbid"`, so a
field that is not part of the contract cannot be written by accident.

**Done when:** the same entity always yields the same id, two entity types
yield different ids, the payload validates, and an unknown entity type is
refused.

### Task 3: the embedder

`semantic/embeddings.py` builds the embedder the config names, using
`SentenceTransformerEmbedder` and `VectorSpace` from
`clustering/similarity.py`. The batch embed checks the width of every vector
against the space and raises `MixedVectorSpaceError` when one does not fit,
which is what a model that changed under the index would look like.

**Done when:** the embedder's space is the configured space, a vector of the
wrong width is refused, and a missing `sentence-transformers` package raises
`EmbeddingUnavailable` instead of an import error at module load.

### Task 4: the adapter

`semantic/qdrant.py` speaks the Qdrant REST API over `httpx`. The transport
is an argument, so the tests drive the real wire format without a server.

The retry rule is in the spec: a transport failure and a 5xx are retried, a
4xx is not. The API key travels in the `api-key` header and appears in no
error message.

**Done when:** every method in the spec's table is implemented, a refused
connection raises `QdrantUnavailable`, a 500 is retried and then succeeds, a
401 is not retried, and the key is in no error string.

### Task 5: the reader

`semantic/source.py` reads the authoritative rows as indexable entities. Each
entity carries its text, its content hash, the payload fields that come from
the database, and the timestamps the database holds.

The pain point is read into the clustering wave's `PainPoint` model, so the
canonical text that gets embedded is the same text the clustering wave
compares.

**Done when:** a live evidence span, an accepted pain point and an active
cluster each read into an entity, a closed document's evidence reads as not
live, and a missing entity reads as `None`.

### Task 6: the index

`semantic/index.py` holds the reconciliation, the rebuild, the search and the
outbox handler.

`ensure_ready()` creates the collection or reads it back and compares the
space. `sync()` reconciles. `rebuild()` replaces. `search()` answers.

**Done when:** the space mismatch raises, the rebuild clears it, an unchanged
entity is skipped, a changed one replaces its point, a deleted one loses its
point, a filter narrows the result, and a partial index converges.

### Task 7: the write path

`db/store.py` enqueues a `target="qdrant"` task in the transaction that
imports the run. The payload names the run and the entities the run produced,
and the idempotency key covers the entity ids, so a replay does not enqueue
the same work twice.

**Done when:** the task is written in the same transaction as the evidence, a
replay does not add a second task, and the task names the right entities.

One existing assertion in `tests/test_db_store.py` changes: the count of
projection tasks goes from one to two. The assertion is extended, not
weakened: it now also names the qdrant task and checks its shape.

### Task 8: the command

`cli.py` gains `ph index` with `sync`, `rebuild`, `search` and `status`.
Every command prints one JSON object and returns 0 or 1.

**Done when:** each subcommand runs against a store and against an index
that answers.

### Task 9: the tests

Three files: `tests/test_semantic_vectors.py`,
`tests/test_semantic_qdrant.py`, `tests/test_semantic_index.py`. The matrix
is in section 10 of the spec.

**Done when:** every case in the matrix has a test, and the whole suite is
green.

## 3. What the implementer must not do

- No new runtime dependency. The adapter uses `httpx`.
- No schema migration. The outbox table already carries a target.
- No secret in a source file, a payload, a log line or a report.
- No live Qdrant call, and no test that pretends a live call succeeded.
- No change to an existing assertion except the one the spec names, and that
  change extends the assertion.
- No scoring, discovery, Obsidian or scheduler change.

## 4. The order of the gates

1. The unit tests of the new code.
2. The whole suite, which must be green before the review reads the diff.
3. The review reads the diff against `00a6ca3` and the connected code.

## 5. The risks carried into the review

1. The embedder and the Qdrant server are both absent from this environment,
   so the review has to judge the seam and not a live run.
2. The semantic package imports the clustering package. The coupling is
   deliberate and documented, and the review may disagree.
3. The embedding block is declared in two config files.
4. The outbox payload carries the entity ids, so it grows with the run.

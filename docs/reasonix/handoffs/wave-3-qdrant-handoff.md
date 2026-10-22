# Handoff: the semantic retrieval layer (Qdrant)

**Run ID:** wave-3-qdrant-2026-10-22
**Branch:** `wave3/qdrant-semantic`
**Worktree:** `C:\Users\anubh\AppData\Local\Temp\opencode\wt-wave3-qdrant`
**Base:** `00a6ca3`
**Spec:** [../specs/wave-3-qdrant.md](../specs/wave-3-qdrant.md)
**Plan:** [../plans/wave-3-qdrant.md](../plans/wave-3-qdrant.md)

## 1. The repository map a node needs

| Path | Holds |
|---|---|
| `src/product_hunter/config.py` | The pydantic config models and their loaders. `ClusteringConfig`, `EmbeddingConfig`, `load_clustering_config()` are the pattern a new config follows. |
| `src/product_hunter/clustering/similarity.py` | `VectorSpace`, `space_id()`, the `Embedder` protocol, `SentenceTransformerEmbedder`, `cosine_similarity()`, `centroid()`, `MixedVectorSpaceError`, `EmbeddingUnavailable`. The semantic package reuses all of it. |
| `src/product_hunter/clustering/problems.py` | `PainPoint`, `canonical_text()`, `fingerprint()`. The pain point's embedded text is `canonical_text()`. |
| `src/product_hunter/db/outbox.py` | `enqueue_projection()`, `process_outbox()`, `ProjectionTask`. The index is fed through this. |
| `src/product_hunter/db/store.py` | `Store.import_run()` writes the evidence and the projection tasks in one transaction. |
| `src/product_hunter/projection/` | The projection pattern: read the state, write what changed, remove what is gone. `projection/engine.py` is the shape to copy. |
| `src/product_hunter/cli.py` | The command surface. `project_command()` is the shape to copy for `ph index`. |
| `db/migrations/*.sql` | The schema. `evidence`, `pain_points`, `clusters`, `cluster_members`, `documents`, `communities`, `sources`, `projection_tasks`. |
| `tests/conftest.py` | The shared fixtures. `open_store()`, `evidence_store()` build a store with the registry synced. |
| `config/clustering.yaml` | The clustering config. Its `embedding:` block is the shape a new embedding block follows. |
| `opencode.json` | The Qdrant MCP server, pointed at `COLLECTION_NAME=product_hunter_evidence`. |

The gate is `uv run pytest` from the worktree root. It must be green.

## 2. The decisions that are already made

Do not reopen these. They are in the spec, and a SubQ Snap call at each of
stages 0, 1 and 3 confirmed them.

1. **One collection**, named by the config, default `product_hunter_evidence`,
   matching the MCP server's collection so the agent can read what the code
   writes.
2. **The space id is the guard.** It is written into the collection metadata,
   into every payload, and into every search filter. A model change makes the
   collection metadata disagree with the configured space, so the index
   refuses to write and demands a rebuild.
3. **The point id is `uuid(sha256(entity_type + "\x1f" + entity_id))`.** The
   content hash is deliberately not in it, so a changed entity replaces its
   point instead of leaving the old vector beside the new one.
4. **The adapter speaks the Qdrant REST API over `httpx`.** `qdrant-client`
   is not installed and no new runtime dependency is allowed. The transport
   is an argument, so the tests drive the real wire format.
5. **The embedder is the clustering wave's** `SentenceTransformerEmbedder`,
   built from the clustering wave's `VectorSpace`. There is one notion of a
   vector space in this repository.
6. **One outbox task per run**, `target="qdrant"`, payload naming the run
   and the entities the run produced, so the handler indexes only those.
7. **The timestamps in the payload are the authoritative row's own**, not the
   moment the index wrote the point. A wall-clock stamp would make the index
   impossible to rebuild into the same bytes.

## 3. What is not available in this environment

- `qdrant-client` is not installed.
- `sentence-transformers` is not installed.
- No Qdrant server is reachable.

So the tests inject a deterministic embedder through the `Embedder` protocol
and drive the adapter through an `httpx` transport that answers with the real
wire format. No test may pretend a live Qdrant connection succeeded, and no
report may claim one did.

## 4. The forbidden rules that are in force

- Never add a provider secret or a Qdrant credential to the repository. The
  config holds the *name* of the environment variable, never its value.
- Never use `shell=True` or a command string for a subprocess.
- Never change a test to make it pass. The one exception in this run is
  named in the plan: the projection-task count in `tests/test_db_store.py`
  goes from one to two, and the change extends the assertion by naming the
  new task and checking its shape. Nothing is weakened.
- Never weaken an evidence assertion, and never accept evidence without a
  verbatim source span.
- Never commit `.env`, `data/`, `runtime/` or `logs/`.

## 5. The blast radius

These files, and nothing outside them:

```
src/product_hunter/semantic/__init__.py
src/product_hunter/semantic/vectors.py
src/product_hunter/semantic/embeddings.py
src/product_hunter/semantic/qdrant.py
src/product_hunter/semantic/source.py
src/product_hunter/semantic/index.py
src/product_hunter/config.py
config/semantic.yaml
src/product_hunter/db/store.py
src/product_hunter/cli.py
tests/test_semantic_vectors.py
tests/test_semantic_qdrant.py
tests/test_semantic_index.py
tests/test_db_store.py
.env.example
docs/reasonix/specs/wave-3-qdrant.md
docs/reasonix/plans/wave-3-qdrant.md
docs/reasonix/handoffs/wave-3-qdrant-handoff.md
docs/reasonix/reports/wave-3-qdrant.md
docs/reasonix/ARCHITECTURE.md
```

A file outside this list is not to be edited. If a fix needs one, say so in
the report rather than editing it.

## 6. The build order

The plan splits the work into layers so that a defect is found close to where
it was made.

| Node | Delivers |
|---|---|
| A | the config, the vector identity, the embedder, the REST adapter, and their tests |
| B | the authoritative reader, the index, the outbox write path, the command, and their tests |

Node A is proved against the wire format before node B is written.

## 7. The state a node leaves behind

- The gate is green: `uv run pytest` from the worktree root.
- The files that were touched are inside the blast radius.
- The new code follows the house style: module docstrings that say what the
  module is for, pydantic models with `extra="forbid"`, type hints, and
  errors that carry a `reason_code`.
- The prose in the docstrings and the comments says what the code cannot
  show, in one line, and does not restate the code.

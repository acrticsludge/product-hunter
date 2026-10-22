# Wave 3 report: the semantic retrieval layer (Qdrant)

**Run ID:** wave-3-qdrant-2026-10-22
**Branch:** `wave3/qdrant-semantic`
**Base:** `00a6ca3` (the Wave 2 integration, plus its review fixes)
**Commit:** `769d260f09f58dc05f103873d6b235aa6bb12c47`
**Risk:** declared `medium`, adapter floor `medium`
**Adapter:** Product Hunter, 8 forbidden rules in force
**Specialization:** backend. No user interface is touched.
**Spec:** [../specs/wave-3-qdrant.md](../specs/wave-3-qdrant.md)
**Plan:** [../plans/wave-3-qdrant.md](../plans/wave-3-qdrant.md)
**Handoff:** [../handoffs/wave-3-qdrant-handoff.md](../handoffs/wave-3-qdrant-handoff.md)

## 1. The goal

SQLite is the authoritative store. This wave adds the derived semantic index
that answers a question in words with the evidence that means the same thing:
semantic evidence retrieval, similar pain-point discovery, duplicate and
candidate discovery, cluster assistance, investigation context retrieval and
historical opportunity comparison. Qdrant is a projection, never a second
source of truth, and it never decides anything SQLite has already decided.

## 2. What was built

A new package, `src/product_hunter/semantic/`, beside the other wave
packages, plus the configuration, the write path and the command.

| File | Lines | Holds |
|---|---|---|
| `src/product_hunter/semantic/__init__.py` | 83 | the public re-exports |
| `src/product_hunter/semantic/vectors.py` | 127 | the entity types, the point id, the payload and the search filter |
| `src/product_hunter/semantic/embeddings.py` | 44 | `build_embedder` and `embed_all` with the width guard |
| `src/product_hunter/semantic/providers.py` | 622 | the provider protocol, the BGE-M3 provider, the sentence-transformers provider, the registry |
| `src/product_hunter/semantic/qdrant.py` | 380 | the REST adapter over `httpx` |
| `src/product_hunter/semantic/source.py` | 346 | the authoritative reader |
| `src/product_hunter/semantic/index.py` | 522 | the index: sync, rebuild, search, the outbox handler |
| `src/product_hunter/config.py` | 447 | `SemanticConfig`, `load_semantic_config`, `SemanticConfigError` |
| `config/semantic.yaml` | 19 | the shipped default |
| `src/product_hunter/db/store.py` | 1276 | the qdrant projection task in `import_run` |
| `src/product_hunter/cli.py` | 1840 | `ph index`, the extended `ph doctor` |
| `.env.example` | 20 | the three Qdrant names |
| `pyproject.toml` | 35 | the optional `embeddings` extra |
| `uv.lock` | +1788 | the lock for the extra |
| `docs/reasonix/ARCHITECTURE.md` | 711 | the new "semantic index" section |
| `tests/test_semantic_vectors.py` | 361 | 30 tests |
| `tests/test_semantic_qdrant.py` | 721 | 27 tests against an in-memory fake Qdrant |
| `tests/test_semantic_providers.py` | 595 | 35 tests, no network, no torch |
| `tests/test_semantic_index.py` | 689 | 22 tests |
| `tests/test_db_store.py` | 138 | the second projection task |

## 3. The embedding provider

The default provider is **BAAI BGE-M3, run locally**. A clean machine
fetches the weights on first use, caches them under `data/models`, loads the
model once and reuses it for every batch. On a warm machine the cache is
reused and no download happens. The device preference is `auto`, which picks
CUDA when it is available and falls back to the CPU, and a CPU-only machine
works without a code change.

The provider is behind a protocol, `EmbeddingProvider`, with a registry that
also holds a sentence-transformers provider, so the abstraction is real and
the default is replaceable. Nothing outside the provider imports a model
library: `providers.py` imports `torch`, `huggingface_hub` and
`flagembedding` lazily, inside the functions that need them, so importing
`product_hunter.semantic` never drags the ML stack in.

The configuration carries the provider, the model, the model version, the
revision, the dimensions, the cache directory, the device, the batch size and
the normalization flag, and the Qdrant collection is created from the
dimensions the provider reports. A change of model or revision changes the
space id, and the index refuses to write until a rebuild.

## 4. The gate

```
uv run pytest
2259 passed, 1 skipped in 40.75s      (exit 0)
```

The one skip is the `network`-marked integration test, which is gated by
`pytest.importorskip("FlagEmbedding")` and does not run here because the
`embeddings` extra is not installed. The default environment does not
contain torch, flagembedding or huggingface_hub; they are recorded in
`uv.lock` behind `marker = "extra == 'embeddings'"`.

## 5. The hardening pass

Two findings, both fixed.

1. **Must.** `src/product_hunter/semantic/index.py:408`: the sync report
   carried the exception message, and `ModelCacheCorrupt`'s message holds a
   filesystem path. The error string now carries the exception class name
   only. The same change was applied to the status report for consistency.
2. **Nice.** `src/product_hunter/semantic/index.py:346`: the status report
   carried the message as well. Fixed the same way.

The pass checked and found clean: no secret anywhere, no shell execution, no
SQL injection, no path traversal, no SSRF, no fake vectors, no zero or random
vectors, no heavy import at module load, no migration, no schema change, and
the adapter scrubs the API key from every message.

## 6. The review

The adversarial review used the ponytail-review six-pass rubric and returned
`fix-then-ship` with one Should fix:

- `src/product_hunter/semantic/index.py:270`: `handle_projection_task` called
  `self.sync(wanted or None)`, so a task naming no entities became a full
  re-index and prune. It now syncs exactly the named entities, and a task with
  no payload syncs everything. The change is one block and the gate is still
  green.

The review also confirmed the space guard, the point id, the centroid clusters,
the full-sync-only prune, the whole-payload comparison, the batched and
single-load embed, the width guard, the same-transaction idempotent outbox
task, the named-entity projection handler, the read-only store connection,
the doctor that never loads the model, the model-id rejection, the API-key
scrubbing, the absence of heavy module-load imports, the optional extra, the
behaviour-level tests, and the absence of a second vector-space identity,
centroid, outbox or fake Qdrant.

## 7. The assumptions and limits

1. **The embedding model never ran here.** `flagembedding`, `torch` and
   `huggingface_hub` are not installed, so the tests inject a deterministic
   embedder through the `EmbeddingProvider` protocol and the `LocalBGEM3Provider`
   is exercised through its injectable downloader, model factory and
   `cuda_available` callable. The production path is the same code with a
   different object behind it. `FlagEmbedding`'s `encode()` returns a mapping
   with a `dense_vecs` key rather than a list; the default model factory
   returns a `DenseEncoder` that unwraps it, so the provider's contract
   holds against the documented API. The integration test that uses the real
   model is marked `network` and is skipped here.
2. **No Qdrant server was ever reached.** The adapter is driven through an
   `httpx` transport that answers with the real wire format, modelled from
   the Qdrant API reference. No test fabricates a successful live connection,
   and no report claims one.
3. **The semantic layer imports the clustering layer.** The vector space
   identity, the embedder protocol and the centroid live in
   `clustering/similarity.py`, and the semantic package imports them. One
   notion of a vector space is worth more than a clean dependency direction,
   but the coupling is real and a later wave may want to move the shared
   primitives into their own module.
4. **The embedding block is declared twice.** The clustering config and the
   semantic config each name a model. The space guard makes a divergence loud
   rather than silent.
5. **The outbox task carries the entity ids in its payload.** A run with
   many candidates makes a large payload. One task per entity was the
   alternative and was rejected because the task count stays at one per run.
6. **The cluster vector is the centroid of the members already in the
   index.** A cluster whose members are not indexed has no vector. That is
   the partial-index state, and the next sync fills it in.
7. **The `network` marker is documentation only.** There is no
   `pytest_collection_modifyitems` hook that skips it, so it runs when
   `FlagEmbedding` is installed. The test guards itself with
   `pytest.importorskip("FlagEmbedding")`, so it does no harm here, but a
   reviewer who wants the skip to be unconditional should add the hook.
8. **The mission asked for `docs/resonix/reports/wave-3-qdrant.md`.** The
   repository's directory is `docs/reasonix/`, so the report was written
   there. The path in the mission is a typo.

## 8. Debt ledger

One `shortcut:` marker is in the wave's code.

- `src/product_hunter/semantic/providers.py`, in `LocalBGEM3Provider._emit`:
  a caller that passes no event sink hears nothing, so a download, a load
  failure or a device fallback is silent. The worker wires a sink. The house
  format is `shortcut: <the limit>, <when to upgrade>`.

## 9. What was not checked

- A live Qdrant server. The wire format is modelled from the API reference.
- A live BGE-M3 model. The provider is exercised through its injectable
  downloader, model factory and `cuda_available` callable.
- The CLI success paths (`ph index sync`, `rebuild`, `search`, `status`) were
  exercised through the tests, not through the command line. The failure path
  (no Qdrant) was smoke-tested by hand.
- The full `ph project --outbox` flow with a qdrant task. The handler was
  tested directly.
- `ph doctor` was tested through the helper that builds the semantic block,
  not through the command line.
- A cluster row in a real database. The cluster path was tested by inserting
  a cluster and its members into an in-memory store.

## 10. The next step

The wave is ready to merge. The open pieces it leaves for the next wave are
the same ones the Wave 2 report named, plus one: the `EmbeddingProvider`
protocol and the BGE-M3 provider are in place, but the worker that calls
`build_provider` and feeds the index still has to be wired, the same way the
Obsidian projection's worker handler is wired.

```
GOAL: implement the semantic retrieval layer for Product Hunter, with BAAI
      BGE-M3 as the default local embedding provider
TIER: free (models used per node in order)
RISK: declared=medium floor=medium (backend adapter, no schema, no secret)
ADAPTER: Product Hunter [repo] 8 forbidden rules in force
STAGES: 11/11 complete, skipped: none. Stage 5b visual was not required; the
  adapter sets visualRequired to never and no UI file is in the radius.
GATES: uv run pytest -> 2259 passed, 1 skipped, exit 0
CHANGES: 20 files, +4900/-18 lines (approx), one commit on wave3/qdrant-semantic
HARDEN: two findings, both fixed. The sync report no longer carries an
  exception message, and the status report does not either.
REVIEW: fix-then-ship, one Should fix, applied. The space guard, the point
  id, the centroid clusters, the full-sync-only prune, the whole-payload
  comparison, the batched and single-load embed, the width guard, the
  same-transaction idempotent outbox task, the named-entity projection
  handler, the read-only store connection, the doctor that never loads the
  model, the model-id rejection, the API-key scrubbing, the absence of heavy
  module-load imports, the optional extra, the behaviour-level tests and the
  absence of a second vector-space identity, centroid, outbox or fake Qdrant
  were all checked and found clean.
WAIVERS: none
STOPPED BECAUSE: all pass and review ship
NEXT: merge wave3/qdrant-semantic into the integration branch; the worker
  handler that calls build_provider and feeds the index is the next wave's work
```

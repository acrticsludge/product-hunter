# Wave 2, Session 4: problem clustering, implementation plan

**Spec:** [../specs/wave-2-clustering.md](../specs/wave-2-clustering.md)
**Date:** 2026-10-21
**Branch:** `wave2/clustering`
**Code gate:** `uv run pytest`

## 1. Layout

The wave adds one package and one module in the existing `db` layer.

```text
src/product_hunter/clustering/
  __init__.py        exports
  problems.py        PainPoint, CanonicalProblem, fingerprint, canonical text
  similarity.py      VectorSpace, Embedder, SentenceTransformerEmbedder, cosine
  clusters.py        Cluster, Membership, ClusterDecision, NeighborCheck, statuses
  adjudication.py    Adjudicator protocol, LlmAdjudicator, prompt, strict parsing
  engine.py          ClusteringEngine, ClusteringResult, the decision pipeline
src/product_hunter/db/clustering.py   ClusterStore, run and result records
src/product_hunter/config.py          ClusteringConfig, load_clustering_config
config/clustering.yaml                thresholds, embedding block, algorithm version
db/migrations/0003_clustering.sql     six tables
```

The dependency runs one way. `clustering` imports from `config`, `llm` and `normalization`. `db/clustering.py` imports from `clustering`. The engine imports nothing from `db` and reads no clock.

## 2. Order of work

Each step leaves the suite green. A step is done when its tests pass and the whole suite still passes.

**Step 1, the data.** `problems.py`, `similarity.py`, `clusters.py`, the config loader, `config/clustering.yaml`. Records, the vector space identity, the cosine helper, the statuses, the membership and decision shapes. `tests/test_clustering_core.py` covers the fingerprint, the canonical text, the space id, the mixed-space refusal, the config validator, and the status and lineage enums.

**Step 2, the migration and the store.** `db/migrations/0003_clustering.sql` and `src/product_hunter/db/clustering.py`. The six tables, the run row, the apply, the idempotency, the membership interval rules, the merge and the split, the evidence lineage, the space refusal. `tests/test_clustering_store.py` covers it, including a migration test that walks the shipped migration files the way `tests/test_db_migrations.py` does.

**Step 3, the adjudicator.** `adjudication.py`. The prompt, the strict parse, the provenance on the answer, the failure path. A stub generator in the test file answers with a scripted JSON body and with a broken body.

**Step 4, the engine.** `engine.py`. The pipeline of the spec's section 6, the bands, the held pain points, the neighbour checks, the duplicate decisions, the deterministic ordering.

**Step 5, the behaviour matrix.** `tests/test_clustering_engine.py`. Every row of the spec's test matrix, with a scripted embedder and a scripted adjudicator.

**Step 6, the wiring.** The `__init__.py` exports, and the helper that turns a result into `ArtifactRecord` values of the kinds the research contract's clustering stage declares.

## 3. Public names

The names the rest of the repository will use.

From `product_hunter.clustering`:

- `PainPoint`, `CanonicalProblem`, `canonicalize`
- `VectorSpace`, `Embedder`, `SentenceTransformerEmbedder`, `cosine_similarity`, `MixedVectorSpaceError`, `EmbeddingUnavailable`
- `Cluster`, `ClusterStatus`, `ClusterProfile`, `Membership`, `ClusterDecision`, `NeighborCheck`, `ClusteringResult`, `ClusteringEngine`
- `Adjudicator`, `Adjudication`, `LlmAdjudicator`, `AdjudicationError`
- `ClusteringError`, `ClusteringConfigError`

From `product_hunter.db.clustering`:

- `ClusterStore`, `ClusteringRun`, `ClusteringRunRecord`, `ApplySummary`, `EvidenceLineageRow`

From `product_hunter.config`:

- `ClusteringConfig`, `EmbeddingConfig`, `SimilarityConfig`, `AdjudicationConfig`, `load_clustering_config`

## 4. The scripted doubles

The tests inject two doubles, both defined in the test files and neither in the package.

`ScriptedEmbedder` answers a vector per canonical text from a table the test writes. A text that is not in the table gets a deterministic fallback vector, so an unexpected comparison fails a test loudly instead of silently scoring zero.

`ScriptedAdjudicator` answers a decision per pair of pain ids. A pair that is not in the table gets the default the test sets, so the ambiguous path is reachable without a special case.

Both doubles are the reason no test needs a network, a model or an embedding package.

## 5. The gates

| Gate | Command | Expected |
|---|---|---|
| code | `uv run pytest` | green, exit code 0 |
| clustering package | `uv run pytest tests/test_clustering_core.py tests/test_clustering_engine.py tests/test_clustering_store.py` | green |
| migrations | `uv run pytest tests/test_db_migrations.py` | green, and the new file is applied by the runner |

The code gate is the adapter's gate: `uv run pytest`. It runs the whole suite, so a change to `config.py` or to the migration set is covered by the existing tests as well as the new ones.

## 6. Known limits, recorded as they are written

The wave takes two limits and names them in the code with a `shortcut:` comment, in the form the repository already uses.

1. The engine scores every pain point against every other one, which is quadratic in the number of pain points. It is fine for the corpus sizes Phase 1 produces and it is the reason the wave needs no vector database. The comment says what to do when it stops being fine: a nearest-neighbour index, which is the Qdrant path the spec describes.
2. The wave ships no CLI command and no worker handler. The package is a library, like the research package, and it has no caller yet. The comment says where the caller goes.

## 7. What the implementer must not do

- No dependency is added. `sentence-transformers` is imported lazily and is not in `pyproject.toml`.
- No model name is written in the code. The model comes from `config/clustering.yaml`.
- No threshold is written in the code. The thresholds come from the config.
- No change to `config/model_routing.yaml`, `config/research_contract.yaml` or `config/sources.yaml`.
- No change to the extraction, fetch, safety, quote, documents, registry or research code.
- No count of mentions, authors, communities or people anywhere in the package.
- No keyword-only merge path, and no wording-only split path.
- No deletion of a cluster row, a membership row, a version row or a lineage edge.
- No network call in a test, and no live model call in the package.

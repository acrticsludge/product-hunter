# Handoff: Wave 2, Session 4, problem clustering

**Date:** 2026-10-21
**Branch:** `wave2/clustering`
**Spec:** [../specs/wave-2-clustering.md](../specs/wave-2-clustering.md)
**Plan:** [../plans/wave-2-clustering.md](../plans/wave-2-clustering.md)

## 1. What the next node has to build

The problem clustering engine for Product Hunter. It decides which pieces of evidence describe the same underlying problem, and it keeps the history of the clusters it makes. The full contract is in the spec. This file is the map of the code that already exists, so the next node reads what it needs and nothing more.

## 2. The repo map

Entry points and commands live in `src/product_hunter/cli.py`. The package is `src/product_hunter/`. Tests live in `tests/`, one file per module, and the gate is `uv run pytest`.

What the wave builds on:

| File | What it gives the wave |
|---|---|
| `src/product_hunter/normalization.py` | `Domain`, `slugify`, `NormalizedProblem`, `normalize_fields`, `DEFAULT_NORMALIZATION_VERSION`. The Phase 1 key is `domain:action:object`, built by `normalize_fields`. The wave reads `pain_points.normalized_key` and `pain_points.normalization_version` and does not rebuild the key. |
| `src/product_hunter/extraction.py` | The `pain_points` rows the wave reads. `ExtractedCandidate` carries `pain_id`, `doc_id`, `span_id`, `quote`, the five controlled fields, `normalized_key`, `normalization_version` and the model provenance. |
| `src/product_hunter/llm/base.py` | `GenerationRequest`, `GenerationResponse` (with `provider`, `model`, `extractor_version` and `provider_version`), `TextGenerator`, `ProviderError`, `ModelCallFailed`. `provider_version` is where the model version comes from. |
| `src/product_hunter/llm/routing.py` | `ModelRole` with a `clustering` member, `ModelRouter`, `ModelSelection`, `ModelExecution`, `FallbackGenerator`. The wave uses `ModelRole.clustering` and changes no routing file. |
| `src/product_hunter/config.py` | The house config pattern: a pydantic model per file, a `load_*_config()` that reads YAML and validates, `repo_root()`, and `ROUTING_CONFIG_NAME` as the pattern for a new config path. |
| `src/product_hunter/db/migrations.py` | The migration runner. Files are `NNNN_name.sql`, contiguous from 0001, split on `;`, full-line `--` comments dropped. A migration that breaks those rules fails the suite. |
| `src/product_hunter/db/model_routing.py` | The closest analogue for the wave's store: a module that owns the persistence of a domain concept, takes a `sqlite3.Connection`, and uses `_owned_transaction` so a write joins the caller's transaction. |
| `src/product_hunter/db/store.py` | The import pattern. `import_run` is one transaction, the outbox write joins it, and a changed value under the same deterministic id is a `StoreConflict` rather than an overwrite. |
| `src/product_hunter/research/evidence.py` | `ArtifactRecord`, `ToolCallProvenance`, `StageExecution`. The wave's helper builds `ArtifactRecord` values of the kinds the research contract's clustering stage declares. |
| `src/product_hunter/research/predicates.py` | The artifact kinds: `KIND_CLUSTER_NEIGHBORS_CHECKED = "cluster_neighbors_checked"`, `KIND_NEIGHBOR_CHECK = "neighbor_check"`, `KIND_DUPLICATE_REVIEW = "duplicate_review"`. The metric `neighbors_checked_per_cluster` reads the first kind and takes the smallest count. |
| `config/research_contract.yaml` | The clustering stage: required tools `qdrant`, required outputs `cluster_assignments`, `similarity_checks`, `cluster_neighbors_checked`, `neighbor_check`, `duplicate_review`, quality checks `nearest_neighbor_checked` and `duplicate_candidates_reviewed`, threshold `min_neighbors_checked_per_cluster: 5`. |
| `db/migrations/0001_initial.sql` | The `pain_points`, `evidence` and `evidence_pain_links` tables the wave reads. `pain_points` has `span_id`, `doc_id`, `domain`, `actor`, `action`, `object`, `consequence`, `pain_statement`, `normalized_key`, `normalization_version`, `accepted`. `evidence` has `span_id`, `doc_id`, `quote`, `char_start`, `char_end`. |
| `db/migrations/0002_model_routing.sql` | The model routing tables. The wave writes none of them. |

The wave adds `db/migrations/0003_clustering.sql`. No other wave has taken 0003 yet.

## 3. Conventions the new code follows

- Pydantic models with `ConfigDict(extra="forbid")` for records that carry data in and out.
- Every exception carries a `reason_code`, and the message names the problem in one line.
- An error detail carries ids, counts and fixed phrases. It never carries the raw text of a quote or of a model answer.
- Deterministic ids: a prefix, then 16 hex characters of a SHA-256. The repository's own ids are `span_id` from `doc_id|char_start|char_end|quote`, `pain_id` from `span_id|extractor_version`, `member_id` will follow the same shape.
- Timestamps are ISO-8601 text.
- A limit that is known and deferred gets a `shortcut:` comment saying the limit and what to do when it stops being true.
- Docstrings say what the module refuses, not what it does. The house voice is plain and short.

## 4. The rules that decide the work

1. Membership is decided on meaning. Shared words never decide a merge.
2. Membership is not split because the wording changed.
3. No count of mentions, authors, communities or people appears anywhere in the package.
4. History is never rewritten. A merge, a split or a re-cluster leaves the old rows alone.
5. Vectors from two different embedding models are never compared.
6. Every model call is recorded with provider, model, model version, prompt version and config version.
7. No model is named in the code. The role comes from the routing layer.

## 5. The state the wave leaves behind

- The code is on the branch `wave2/clustering`, in the worktree `.worktrees/wave2-clustering`. The repository root held another wave's uncommitted work, so the wave was moved into its own worktree and the root was restored to the other wave's state.
- The suite is green at 1087 tests. The clustering package's own run is 107 tests and the migration run is 13.
- The clustering code exists: `src/product_hunter/clustering/` with six modules, `src/product_hunter/db/clustering.py`, `config/clustering.yaml`, the `ClusteringConfig` models and the loader in `src/product_hunter/config.py`, and `db/migrations/0003_clustering.sql` with six tables.
- `sentence-transformers` and `numpy` are not installed, and the wave added no dependency. The embedding adapter imports `sentence-transformers` lazily and the tests inject a scripted embedder.
- The global handoff file at `%TEMP%\loop-handoff\handoff.md` holds the handoff of an unrelated project. It was not merged into this one.
- The migration number `0003` is taken on this branch. `main` now carries `db/migrations/0003_research_gate.sql`, committed at `59e29fa`, and the collection wave has `db/migrations/0003_collection.sql`. When this branch merges against `main`, its file has to be renumbered to `0004_clustering.sql` and the schema version assertion in `tests/test_db_migrations.py` re-applied from 3 to 4. The name is the only thing that changes, and this branch is self-consistent on its own.

## 6. The radius the wave touched

The wave may touch these files and nothing else:

```text
src/product_hunter/clustering/__init__.py
src/product_hunter/clustering/problems.py
src/product_hunter/clustering/similarity.py
src/product_hunter/clustering/clusters.py
src/product_hunter/clustering/adjudication.py
src/product_hunter/clustering/engine.py
src/product_hunter/db/clustering.py
src/product_hunter/config.py
config/clustering.yaml
db/migrations/0003_clustering.sql
tests/test_clustering_core.py
tests/test_clustering_engine.py
tests/test_clustering_store.py
tests/test_db_migrations.py
docs/reasonix/specs/wave-2-clustering.md
docs/reasonix/plans/wave-2-clustering.md
docs/reasonix/prompts/wave-2-clustering-*.md
docs/reasonix/handoffs/wave-2-clustering-handoff.md
docs/reasonix/reports/wave-2-clustering.md
docs/reasonix/reports/wave-2-clustering-harden-2.md
docs/reasonix/reports/wave-2-clustering-review.md
docs/reasonix/reports/wave-2-clustering-review-2.md
docs/reasonix/reports/wave-2-clustering-review-3.md
docs/reasonix/decisions/2026-10-21-cluster-identity-and-lineage.md
docs/reasonix/ARCHITECTURE.md
```

`config.py` is the only existing source file the wave edited, and the edit is the addition of the clustering config models and the loader. `tests/test_db_migrations.py` is the only existing test file the wave edited, and the edit is the schema version assertion, because the wave adds a migration.

## 7. What the next wave inherits

- The engine is a pure function of its inputs. It reads no clock and no database. The store owns every write, and the store owns the durable cluster id.
- The decision note `docs/reasonix/decisions/2026-10-21-cluster-identity-and-lineage.md` holds the five conventions a later wave must not break: the store owns the id, a merge and a split create new ids, a membership is an interval, two vector spaces are never compared, and a decision is scoped to its run.
- The scoring wave reads cluster ids and versions. The guarantee it leans on is that a score attached to a cluster stays attached to that cluster, and that a successor carries a lineage edge to its predecessor.
- The independence wave reads the decisions with relation `repost` and writes the `propagation_edges` rows. The clustering wave does not write that table, and it produces no count of mentions, authors, communities or people.
- The research contract's `clustering` stage expects the outputs `cluster_assignments`, `similarity_checks`, `cluster_neighbors_checked`, `neighbor_check` and `duplicate_review`. The engine's result carries the matching records, and `clustering_artifacts()` turns them into `ArtifactRecord` values of those kinds. The helper takes the tool call handle, because the gate counts an artifact only when it traces to a successful call of the stage.
- The model routing layer is unchanged. `ModelRole.clustering` exists and `config/model_routing.yaml` binds it. The wave names no model and writes no threshold in code.

## 8. The debts the wave leaves

Three `shortcut:` comments name the known limits:

- `src/product_hunter/clustering/engine.py`: the run is quadratic in the corpus. The upgrade is a nearest-neighbour index, which is the Qdrant path the spec describes.
- `src/product_hunter/clustering/__init__.py`: the package ships no command line and no worker handler. The caller is the research stage that runs the engine and applies the result to the store.
- `src/product_hunter/db/clustering.py`: a merge or a split falls back to the predecessor's label when the anchor's pain point row is gone. The upgrade is to read the pain point from the store, once a wave prunes the pain points a cluster points at.

Three things were never exercised: the real embedding path, because `sentence-transformers` is not installed; the adjudicator against a live model, because only a scripted double ran; and the store against a real run, because nothing calls it yet.

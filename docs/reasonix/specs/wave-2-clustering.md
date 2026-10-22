# Wave 2, Session 4: problem clustering

> **Integrated as `0006_clustering.sql`.** The migration named below was
> written as `0003_clustering.sql` and was renumbered by the Wave 2 final
> integration. See
> [wave-2-final-integration.md](../reports/wave-2-final-integration.md).

**Status:** approved for implementation
**Date:** 2026-10-21
**Branch:** `wave2/clustering`
**Risk:** high. The radius touches `db/migrations/**`, which the adapter lists as a high-risk path, so stages 2, 6 and 7 cannot be skipped.
**Adapter:** `Product Hunter` (`.loop/adapter.json`), code gate `uv run pytest`.

## 1. What this wave builds

One job: decide which pieces of evidence describe the same underlying problem.

The wave turns accepted pain points into clusters. A cluster is a set of pain points that a person could recognise as the same complaint. The wave also keeps the history of those clusters, so a later report can say what a cluster meant when a score was attached to it.

The wave does not judge whether a problem is a good opportunity. Scoring, competitor research, counter-evidence, market sizing and Obsidian projection are other waves and are out of scope here.

## 2. Rules the code must hold

These are the constraints the wave is judged against. Each one is a test.

1. A cluster's membership is decided on meaning, not on shared words. Two pain points that use the same words in different domains are not the same problem.
2. A cluster's membership is not split because the wording changed. Two pain points that say the same thing in different words are the same problem.
3. The wave never counts mentions, authors, communities or people. Raw mention count is not evidence of independent people, and independence is a separate analysis (`wave2/independence`).
4. A historical cluster keeps its identity and its meaning. A merge, a split or a re-cluster never rewrites what an older cluster meant.
5. The wave never compares vectors from two different embedding models. A vector space change is refused, not blended.
6. Every model call is recorded with its provider, model, model version, prompt version and config version.
7. The model is never hard-coded. The role comes from the model routing layer.

## 3. Inputs

The wave reads accepted pain points and their evidence spans from SQLite.

A `PainPoint` carries:

| Field | Source | Note |
|---|---|---|
| `pain_id` | `pain_points.pain_id` | stable id from Phase 1 |
| `doc_id`, `source_id`, `span_id` | `pain_points` | provenance of the pain point |
| `quote` | `evidence.quote` | the verbatim span, used for repost detection |
| `domain` | `pain_points.domain` | controlled field, one of `normalization.Domain` |
| `actor`, `action`, `object`, `consequence` | `pain_points` | controlled fields |
| `pain_statement` | `pain_points.pain_statement` | the model's prose summary |
| `normalized_key` | `pain_points.normalized_key` | the Phase 1 key |
| `normalization_version` | `pain_points.normalization_version` | the version the key was built with |
| `severity`, `frequency` | `pain_points` | carried, never used to decide identity |

A pain point whose `accepted` flag is false is not an input. A pain point with no `normalized_key` is not an input either: it never went through Phase 1 normalisation, so it has no versioned identity to compare.

## 4. The canonical problem

`CanonicalProblem` is the model-independent form of one pain point. It is what the wave embeds, and it is the identity the wave compares.

Derived from the controlled fields, never from the prose alone:

- `canonical_text`: a deterministic rendering of domain, actor, action, object, consequence and pain statement, in that fixed order, with one label per field. The same pain point always renders to the same string.
- `fingerprint`: the SHA-256 of the normalisation version plus the five controlled fields, domain, actor, action, object and consequence. The pain statement is not in it, so two records that describe the same problem with different prose share a fingerprint.
- `normalized_key`: the Phase 1 key, carried through.

The fingerprint is an exact-identity signal, not a similarity signal. Two pain points with the same fingerprint name the same actor, the same task, the same object and the same cost. Two pain points with different fingerprints may still be the same problem, and that is what similarity and adjudication are for.

## 5. The vector space

`VectorSpace` is the identity of an embedding model:

| Field | Meaning |
|---|---|
| `provider` | who serves the model, `local` for a local model |
| `model` | the model name, from config |
| `model_version` | the version of that model, from config |
| `dimensions` | the length of the vectors it returns |
| `space_id` | SHA-256 over the four fields above |

`space_id` is the guard. Two vectors may only be compared when they come from the same space. A comparison across spaces raises `MixedVectorSpaceError`, naming both space ids. The store refuses to apply a result whose space differs from the space the stored clusters were built in, so an embedding model change cannot silently blend two spaces.

The `Embedder` protocol is:

```python
class Embedder(Protocol):
    @property
    def space(self) -> VectorSpace: ...
    def embed(self, texts: Sequence[str]) -> list[tuple[float, ...]]: ...
```

`SentenceTransformerEmbedder` implements it with a lazy import of `sentence-transformers`. The package is not a dependency of this repository. When the package is missing the adapter raises `EmbeddingUnavailable` with a message that names the package. The model name comes from `config/clustering.yaml`, never from the code.

The wave ships no other embedder. A lexical embedder would merge on shared words, which rule 1 forbids, so there is no keyword fallback.

## 6. The decision pipeline

`ClusteringEngine.cluster(pain_points)` is a pure function of its inputs. It touches no database and no clock. The store owns persistence, the caller owns the clock.

The pipeline, in order:

1. **Sort** the pain points by `pain_id`. The result of a run must not depend on the order the caller happened to supply.
2. **Canonicalise** each pain point into a `CanonicalProblem` and embed every `canonical_text` in one call. The space of the returned vectors must equal the embedder's space, or the run stops.
3. **Candidate generation** for each pain point, in sorted order. A candidate cluster is any cluster that already holds a pain point whose similarity to this pain point is at or above the ambiguous threshold, plus every cluster that already holds a pain point with the same fingerprint. At most `max_candidates` candidates are kept, the strongest first.
4. **Scoring** a candidate. The score is the highest cosine similarity between the pain point's vector and the vectors of the candidate cluster's members. Two adjustments apply:
   - A member with an identical fingerprint raises the score to the merge threshold, and the basis becomes `identical_normalized_fields`.
   - A member whose domain differs from the pain point's domain has the domain penalty subtracted from the score. The penalty is configurable. The basis records that the penalty applied.
   The centroid of the cluster is also scored. The centroid is the mean of the member vectors, and a cluster with one member has the member's vector as its centroid.
5. **Banding** the score against the configured thresholds.
   - At or above `merge_threshold`: assign to the strongest candidate. The basis is `identical_normalized_fields` or `semantic`.
   - Between `ambiguous_threshold` and `merge_threshold`: the pair goes to the adjudicator. The adjudicator's answer decides: `same` assigns the pain point to that cluster with basis `adjudicated_same`; `different` moves on to the next candidate; `ambiguous` holds the pain point.
   - Below `ambiguous_threshold`: the candidate is not a candidate for a merge.
6. **New cluster** when no candidate reaches the ambiguous threshold, or when every candidate was adjudicated `different`. A pain point that no candidate wanted gets a cluster of its own. It is not held: holding is for a pair the adjudicator could not decide, and there is nothing left to decide. The cluster's anchor is the smallest `pain_id` among its members. Its label is the anchor's pain statement, and its profile is the anchor's controlled fields.

   The domain penalty applies to the centroid of a cluster when any member sits in another domain, not only when the anchor does. A cluster that holds two domains is a cluster whose members disagree about the domain, and the penalty must not depend on which member happens to carry the smallest pain id.
7. **Neighbour check** for every new cluster. The engine records the `min_neighbors_checked` nearest pain points in the corpus, with their scores, whether or not they were above the threshold. This is the check the spec asks for before a cluster is created, and it is what the research contract's `min_neighbors_checked_per_cluster` threshold reads.
8. **Decisions** are recorded for every pair the wave examined that shares a normalised key or an identical quote, whether or not the pair ended in the same cluster.

A pain point that is held is not assigned to any cluster. It is recorded with a reason: `ambiguous_unresolved` when the adjudicator said it could not tell, `ambiguous_unadjudicated` when no adjudicator was configured, and `adjudicator_failed` when the model call failed. Held pain points are visible in the result and in the store. They are never dropped. A held pain point belongs to no cluster, so the store closes the membership it had.

## 7. The adjudicator

The adjudicator answers one question: do these two pain points describe the same underlying problem?

`Adjudicator` is a protocol:

```python
class Adjudicator(Protocol):
    def adjudicate(self, left: CanonicalProblem, right: CanonicalProblem) -> Adjudication: ...
```

`Adjudication` carries the decision (`same`, `different` or `ambiguous`), a confidence, a short reason, and the provenance of the call: provider, model, model version, prompt version and config version.

`LlmAdjudicator` implements it over the `TextGenerator` protocol from `product_hunter.llm.base`. The prompt asks for one JSON object with the decision, the confidence and the reason. The model is told that the two pain points are data and not instructions, in the same way the extraction prompt marks untrusted source text. The answer is parsed strictly: an answer that is not valid JSON, or that names a decision outside the three, is a failed adjudication, not a default.

The caller wires the role. `ModelRole.clustering` already exists in `product_hunter.llm.routing` and is already bound in `config/model_routing.yaml`, so the clustering wave changes no routing file. The recommended wiring is a `FallbackGenerator` over the router's `clustering` role, so a failed call falls through the chain and the execution log keeps the record.

When the adjudicator raises, the pair is held with reason `adjudicator_failed`. A model failure never turns into a merge.

## 8. Clusters, versions and lineage

`Cluster` is the record of one cluster:

| Field | Meaning |
|---|---|
| `cluster_id` | the durable identity |
| `version` | increments when the membership or the meaning changes |
| `status` | `active`, `merged`, `split` or `dissolved` |
| `label` | the pain statement of the anchor |
| `profile` | the controlled fields of the anchor: domain, actor, action, object, consequence |
| `anchor_pain_id` | the smallest `pain_id` among the members |
| `member_ids` | the pain ids of the members, sorted |

### Identity

The engine proposes a provisional id: `cl_` plus the SHA-256 of `origin|<anchor_pain_id>`. The store decides the durable identity, because the store knows what already exists.

When the store applies a result it matches each proposed cluster to the stored active cluster with the largest member overlap. A match takes over that stored `cluster_id`, and the provisional id is dropped. An unmatched proposal becomes a new cluster with its provisional id. The result is that a cluster keeps its id across re-clustering even when its anchor changes, and a genuinely new cluster gets a fresh id.

Ids never carry a timestamp, a run id or a counter. The same input produces the same ids.

### Versions

A cluster's version increments when its member set changes, and only then. A re-run that produces the same membership changes nothing: no version bump, no new membership rows, no new snapshot.

A cluster's version also moves when its status changes, because a status is part of what the cluster means. A predecessor that is merged and a parent that is split get a new version row with the new status, so the version a current row carries always names a snapshot that agrees with it. The rule that a predecessor keeps its versions means its history stays: the old version rows are never removed and the old membership rows are never rewritten.

Every version is snapshotted in `cluster_versions`: the cluster id, the version, the status, the label, the profile, the member ids, the run that produced it and the config version. The snapshot is immutable. The `clusters` table holds the current row for convenience, and the snapshots hold the history.

### Membership validity

`cluster_members` rows carry `valid_from` and `valid_to`. A row with no `valid_to` is the current membership. A membership that ends gets its `valid_to` set; it is never deleted.

Two invariants hold over the membership intervals:

- A pain point has at most one open membership. A pain point is in one cluster at a time.
- The intervals of one pain point never overlap.

The store refuses a write that breaks either one.

### Merge

`merge_clusters(left, right)` produces a successor:

- The successor is a new cluster. Its id is `cl_` plus the SHA-256 of `merge|<smaller id>|<larger id>|<anchor>`.
- Both predecessors get `status = merged` and `merged_into = <successor>`, and a new version row with that status.
- A lineage edge of relation `merged_into` is written for each predecessor.
- The predecessors' membership rows are closed. The successor gets open rows for the union of the members.
- The predecessors keep their rows and their version history. A score attached to a predecessor stays attached to that predecessor.
- The merge runs in one vector space. The space of the rows being touched has to be the space of the run, or the merge is refused.

### Split

`split_cluster(parent, groups)` produces children:

- The groups must cover the parent's members exactly. A group that names a pain point the parent never held, or a member the groups leave out, is refused before anything is written. A split never drops a pain point and never adopts a stranger.
- Each child is a new cluster. Its id is `cl_` plus the SHA-256 of `split|<parent id>|<anchor>`.
- The parent gets `status = split` and a new version row with that status.
- A lineage edge of relation `split_from` is written for each child, pointing at the parent.
- The parent's membership rows are closed. The children get open rows for their own members.
- The split runs in one vector space, on the same rule as the merge.

### Lineage

`cluster_lineage` holds one edge per relation: `merged_into` and `split_from`. The edges are what let a reader walk from an old cluster to its successor without the successor having overwritten anything.

### Evidence lineage

The evidence behind a cluster is reachable from the cluster's members: `cluster_members` to `pain_points` to `evidence_pain_links` to `evidence`. `ClusterStore.evidence_lineage(cluster_id)` returns one row per evidence span, with the pain id, the document id, the quote and the validity interval of the membership that carried it.

## 9. Duplicate detection

The wave flags duplicate candidates. It does not decide opportunity value, and it does not write the propagation graph, which belongs to the independence wave.

A pair is a duplicate candidate when the two pain points share a normalised key, or when their quotes are the same after lowercasing and collapsing whitespace.

| Case | Relation | Decision |
|---|---|---|
| Same fingerprint | `duplicate` | `duplicate`. The two are the same problem by construction. |
| Same quote, different fingerprint, different document | `repost` | `ambiguous` until adjudicated, then `duplicate` or `different`. |
| Same quote, different fingerprint, same document | `repeat_wording` | `ambiguous` until adjudicated. |
| Same normalised key, different fingerprint | `related` | `ambiguous` until adjudicated. |

The independence wave can read the decisions with relation `repost` and write the `propagation_edges` rows. The clustering wave does not write that table.

## 10. Persistence

One migration, `db/migrations/0006_clustering.sql`, adds six tables. The
migration runner splits a statement where `sqlite3.complete_statement` says
one ends, so a trigger body keeps the semicolon it needs, and it drops
full-line `--` comments.

`clustering_runs`

| Column | Meaning |
|---|---|
| `run_id` | primary key |
| `algorithm_version` | the version of the clustering algorithm |
| `config_version` | the version of `config/clustering.yaml` |
| `prompt_version` | the version of the adjudication prompt |
| `provider`, `model`, `model_version` | the model that adjudicated, from the router |
| `embedding_provider`, `embedding_model`, `embedding_model_version` | the embedding model |
| `space_id` | the vector space the run was built in |
| `params_json` | the thresholds and the limits the run used |
| `created_at` | ISO-8601 |

`clusters`

`cluster_id` primary key, `version`, `status`, `label`, `domain`, `actor`, `action`, `object`, `consequence`, `anchor_pain_id`, `member_count`, `merged_into`, `split_from`, `space_id`, `config_version`, `run_id`, `created_at`, `updated_at`.

`cluster_versions`

`cluster_id` and `version` primary key, `status`, `label`, the five profile fields, `member_ids_json`, `member_count`, `run_id`, `config_version`, `space_id`, `created_at`. Rows are never updated.

`cluster_members`

`member_id` primary key, `cluster_id`, `pain_id`, `valid_from`, `valid_to`, `assignment_confidence`, `basis`, `run_id`. Unique on `(cluster_id, pain_id, valid_from)`. Indexes on `pain_id` and on `(cluster_id, valid_to)`.

`cluster_lineage`

`edge_id` primary key, `from_cluster_id`, `to_cluster_id`, `relation`, `reason`, `run_id`, `created_at`. Unique on `(from_cluster_id, to_cluster_id, relation)`.

`cluster_decisions`

`decision_id` primary key, `run_id`, `kind` (`duplicate`, `ambiguous`, `adjudication`), `relation`, `pain_a`, `pain_b`, `cluster_id`, `decision`, `score`, `confidence`, `basis`, `detail`, `provider`, `model`, `model_version`, `prompt_version`, `created_at`.

The row id is scoped to the run. Two runs that reach the same decision about the same pair write two rows, so the second run's model call leaves a record of its own and does not overwrite the first run's. The `cluster_id` names the durable cluster the decision was about, so a reader can join the decision to the cluster the store allocated. The `confidence` is the confidence of the adjudication behind the decision, and the `detail` is the fixed phrase that explains a held pain point. Neither the adjudicator's free-text reason nor the text sent to the adjudicator is written: a persisted detail never carries the raw text of a model answer, and the canonical text of a pain point is a pure function of its row in `pain_points`.

`ClusterStore` lives in `src/product_hunter/db/clustering.py`, beside `db/store.py` and `db/model_routing.py`, so every SQLite write in the repository stays inside the `db` layer. The domain package `src/product_hunter/clustering/` holds no database code, and the engine imports nothing from `db`. The dependency runs one way, the way `db/store.py` already depends on `extraction.py`.

`ClusterStore` owns every write. Its methods:

- `record_run(run)` writes the run row.
- `apply_result(result, run)` writes the clusters, the memberships, the versions, the lineage and the decisions. It is idempotent: applying the same result twice changes nothing. It refuses a result whose `space_id` differs from the stored space.
- `current_memberships()` returns the open memberships.
- `cluster(cluster_id)`, `cluster_versions(cluster_id)`, `lineage(cluster_id)` read the history back.
- `evidence_lineage(cluster_id)` walks the memberships to the evidence spans.
- `embedding_spaces()` returns the distinct spaces the stored clusters were built in, so an operator can see a drift.

The store never deletes a cluster row, a membership row, a version row or a lineage edge. A closed membership is a membership with a `valid_to`.

## 11. Configuration

`config/clustering.yaml` holds the operator-tunable values. The loader follows the house pattern in `product_hunter.config`: `load_clustering_config()` reads the file, validates it and returns a `ClusteringConfig`.

```yaml
version: 1
algorithm_version: cluster-v1
prompt_version: cluster-prompt-v1
embedding:
  provider: local
  model: sentence-transformers/all-MiniLM-L6-v2
  model_version: "1"
  dimensions: 384
similarity:
  merge_threshold: 0.86
  ambiguous_threshold: 0.72
  domain_mismatch_penalty: 0.15
  max_candidates: 5
  min_neighbors_checked: 5
adjudication:
  role: clustering
  enabled: true
  max_calls: 50
```

The validator refuses a file whose `ambiguous_threshold` is at or above its `merge_threshold`, whose thresholds are outside `[0, 1]`, whose `min_neighbors_checked` is below 1, or whose embedding block names no model.

## 12. Errors

Every error in the package carries a `reason_code`.

| Error | Reason code | When |
|---|---|---|
| `ClusteringError` | `clustering_error` | base class |
| `EmbeddingUnavailable` | `embedding_unavailable` | the embedding package is missing |
| `MixedVectorSpaceError` | `mixed_vector_space` | two spaces are compared, or a result crosses spaces |
| `AdjudicationError` | `adjudication_failed` | the model answer cannot be read |
| `ClusteringConfigError` | `clustering_config_invalid` | the config file is not usable |

A detail string carries ids, counts, thresholds and fixed phrases. It never carries the raw text of a quote or a model answer, on the rule the research package already follows: a persisted detail is read by people and by tools, and the text in it came from an untrusted source.

## 13. The test matrix

Three test files: `tests/test_clustering_core.py`, `tests/test_clustering_engine.py` and `tests/test_clustering_store.py`.

The engine tests use a scripted embedder and a scripted adjudicator. Neither calls a network.

| Case | Expected |
|---|---|
| Obvious duplicates: same controlled fields, two documents | one cluster, two members, basis `identical_normalized_fields` |
| Same wording, different problems: same action and object, different domain and actor | two clusters. The domain penalty keeps them out of the merge band |
| Same problem, different wording: different controlled words, same meaning | one cluster, reached through the adjudicator |
| Repost: same quote, two documents, different fingerprint | one duplicate decision with relation `repost`; the pair is adjudicated |
| Ambiguous: the adjudicator answers `ambiguous` | the pain point is held with reason `ambiguous_unresolved` and belongs to no cluster |
| Adjudicator failure | the pain point is held with reason `adjudicator_failed`; nothing merges |
| No adjudicator, ambiguous score | the pain point is held with reason `ambiguous_unadjudicated` |
| Merge: two clusters that are one problem | a new successor id, both predecessors `merged`, a lineage edge each, the predecessors' rows closed |
| Split: one cluster holding two problems | two children with new ids, `split_from` set, the parent's rows closed |
| Re-run: the same input twice | the same cluster ids, the same members, no version bump, and the second write changes nothing |
| Deterministic ids | the same input gives the same ids; a different input gives different ids; no id carries a timestamp |
| Model change: the adjudication model changes | the run row carries the new provider, model and model version; the membership is unchanged, so no version bump |
| Embedding model change | the space id changes, and applying the new result to the stored clusters raises `MixedVectorSpaceError` |
| Cluster version change: a member leaves | the version increments, a new snapshot row appears, the old snapshot is unchanged |
| Membership validity | a pain point has one open membership, and its intervals never overlap |
| No keyword merge | two pain points that share every word but sit in different domains do not merge |
| No keyword split | two pain points with different wording for the same problem stay together |
| No counts | the result carries no mention, author or person count |
| The domain penalty does not follow the anchor | a cluster that holds two domains gives the same score in either id order |
| A merge in another vector space | refused with `MixedVectorSpaceError`, and nothing is written |
| A split in another vector space | refused with `MixedVectorSpaceError`, and nothing is written |
| A split that leaves a member out | refused before any write, and the parent keeps its members |
| A split that names a stranger | refused before any write |
| The durable id survives a renamed proposal | the cluster keeps its id, its version does not move, and one cluster row exists |
| A pain point that moved | its old membership is closed, its new one is open, and it is named once |
| A vector of the wrong length | the run stops with `MixedVectorSpaceError` |
| An exhausted adjudication budget | the pain point is held with reason `ambiguous_unadjudicated`, and the adjudicator is not called |
| Two runs that reach the same decision | two rows, one per run, each with its own provider and confidence |
| The same run applied twice | one decision row, and nothing else changes |
| A held pain point | it has no open membership, its cluster's version moves, and a snapshot records the change |
| The real embedder without the package | `EmbeddingUnavailable`, and the message names the package |
| A cluster that names a member it does not assign | refused with `ClusterStoreError` |
| A merged predecessor | its version moves and its new snapshot says `merged` |
| A split parent | its version moves and its new snapshot says `split` |
| A cluster that lost its last member | its status is `dissolved` |
| A pain point that is both assigned and held | refused with `ClusterStoreError` |
| The closed count | it covers the rows closed by a move and by a hold |

The store tests cover the migration, the round trip, the idempotent apply, the history after a merge and a split, the evidence lineage, and the refusal to apply a result from another space.

## 14. Integration points

- The research contract's `clustering` stage declares the outputs `cluster_assignments`, `similarity_checks`, `cluster_neighbors_checked`, `neighbor_check` and `duplicate_review`, the quality checks `nearest_neighbor_checked` and `duplicate_candidates_reviewed`, and the threshold `min_neighbors_checked_per_cluster: 5`. The engine's result carries the matching records, and a helper turns them into `ArtifactRecord` values of those kinds. The helper takes the tool call handle, because the gate only counts an artifact that traces to a successful call of the stage.
- The `pain_points` and `evidence` tables feed the engine. The store reads them.
- `wave2/independence` reads the decisions with relation `repost` and writes `propagation_edges`. The clustering wave does not write that table.
- The scoring wave reads cluster ids and versions. The guarantee it relies on is that a score attached to a cluster stays attached to that cluster, and that a successor carries a lineage edge to its predecessor.
- The model routing layer is unchanged. `ModelRole.clustering` exists and `config/model_routing.yaml` binds it.

## 15. Assumptions

These were decided here, and the reviewer may overturn any of them.

1. The deliverable path in the mission reads `docs/resonix/reports/wave-2-clustering.md`. The repository's directory is `docs/reasonix`, and every other report in it lives under `docs/reasonix/reports`. The report goes to `docs/reasonix/reports/wave-2-clustering.md`.
2. The migration number is `0003`. The architecture document reserved `0003` for the wave that persists tool calls, and that file did not exist when this wave was planned. The clustering wave took `0003` because migrations must be contiguous. `main` now carries `db/migrations/0003_research_gate.sql`, committed at `59e29fa`, so this branch's file has to be renumbered to `0004_clustering.sql` when the branch merges against `main`, and the schema version assertion in `tests/test_db_migrations.py` has to be re-applied from 3 to 4 after the rename. The file name is the only thing that changes.
3. The embedding backend is `sentence-transformers`, as the spec names. The package is not a dependency of this repository, so the adapter imports it lazily and the tests inject a scripted embedder. No dependency is added by this wave.
4. A pain point in a different domain is not merged with one in another domain on strength alone. The domain penalty is a configurable number, not a rule, because one underlying problem can be filed under two domains by two different extractors.
5. The wave exposes no command line. It is a library, like the research package, and it has no caller yet.

## 16. What the wave does not do

- Opportunity scoring, confidence, severity weighting of clusters.
- Competitor research, alternatives, counter-evidence.
- Market sizing, trends, frequency analysis.
- Obsidian projection, DuckDB analytics, Qdrant client code.
- Any count of independent people, authors or communities.
- Any change to `config/model_routing.yaml`, `config/research_contract.yaml` or `config/sources.yaml`.
- Any change to the extraction, fetch, safety, quote or documents code.

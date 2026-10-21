# Phase 4 Wave 2: problem clustering engine, run report

**RUN_ID:** wave2-clustering-20261021
**Node:** REPORT (stage 10)
**Date:** 2026-10-21
**Commit:** 0d9170e, "Phase 4 wave 2: problem clustering engine"
**Risk:** declared high, floor high (db/migrations/** is an adapter high-risk path)

## 1. What the wave delivered

The problem clustering engine. It decides which pieces of evidence describe the same underlying problem, and it keeps the history of those clusters. It does not decide whether a problem is a good opportunity, and it produces no count of mentions, authors, communities or people. The code lives in `src/product_hunter/clustering/` with six modules (the pain point records, the vector space, the cluster shapes, the adjudicator, the engine and the exports), in `src/product_hunter/db/clustering.py`, in the config file `config/clustering.yaml` with its loader in `src/product_hunter/config.py`, in the migration `db/migrations/0003_clustering.sql` with six tables, and in three test files.

## 2. Gate evidence

The code gate from `.loop/adapter.json`:

```text
uv run pytest
1087 passed in 14.49s
exit code 0

uv run pytest tests/test_clustering_core.py tests/test_clustering_engine.py tests/test_clustering_store.py
107 passed in 0.91s
exit code 0

uv run pytest tests/test_db_migrations.py
13 passed in 0.59s
exit code 0
```

The suite was 980 tests before the wave.

## 3. The two security passes

The first pass returned `fix first` with three must-fix items and three should-fix items. The must-fix items: the migration declared no foreign keys, the status column was not constrained, and there was no database backstop for one open membership per pain point. The should-fix items: the adjudicator's confidence was computed and thrown away, the reason a pain point was held was dropped, and `params_json` was written with `default=str`. A fix round closed all six and the second pass returned `ship`. The two verdicts are `fix first` and `ship`.

One thing the first pass asked for was declined on purpose: the adjudicator's free-text reason and the text sent to the adjudicator are not persisted, because the spec says a persisted detail never carries the raw text of a model answer, and the input is reconstructible from the two pain ids a decision row already names.

## 4. The three review passes

The first review returned `fix 1, 2 and 3 first` with three must-fix items, eight should-fix items, three nice-to-have items and five lean items. The three must-fix items: the domain penalty depended on which member had the smallest pain id, so the same two problems clustered differently depending on an id; a merge or a split could run in another vector space; a split could drop a member or adopt a stranger. A fix round closed all nineteen, the spec was amended where the contract was worded both ways, the re-review returned `Verdict: Ship.` with one should-fix and four nice-to-have items, a last round closed those five, and the final confirmation returned `Verdict: Ship.` with no new findings. The three verdicts are `fix 1, 2 and 3 first`, `Verdict: Ship.` and `Verdict: Ship.`.

The review proved three of its own gaps by removing code and watching the suite stay green, and the fix rounds added twenty-two tests that go red when the rule is taken out.

## 5. The model-routing assumptions

The wave names no model and no threshold in code. The adjudication model comes from the existing routing layer: `ModelRole.clustering` in `src/product_hunter/llm/routing.py`, already bound in `config/model_routing.yaml`, which the wave does not change. The embedding model comes from `config/clustering.yaml`, and `sentence-transformers` is imported lazily and is not a dependency of the repository. Every call records the provider, the model, the model version, the prompt version and the config version. A vector space is identified by a digest of the provider, the model, the model version and the dimension count, and two spaces are never compared: the comparison, the apply, the merge and the split all refuse.

## 6. The integration points

The research contract's `clustering` stage declares the outputs `cluster_assignments`, `similarity_checks`, `cluster_neighbors_checked`, `neighbor_check` and `duplicate_review`, the quality checks `nearest_neighbor_checked` and `duplicate_candidates_reviewed`, and the threshold `min_neighbors_checked_per_cluster: 5`; the engine's result carries the matching records and `clustering_artifacts()` turns them into `ArtifactRecord` values of those kinds. The `pain_points` and `evidence` tables feed the engine. The independence wave reads the decisions with relation `repost` and writes the `propagation_edges` rows; this wave does not write that table. The scoring wave reads cluster ids and versions and leans on the guarantee that a score attached to a cluster stays attached to that cluster.

## 7. The file map

```text
   191 total   176 non-blank  src/product_hunter/clustering/__init__.py
   132 total   108 non-blank  src/product_hunter/clustering/problems.py
   196 total   156 non-blank  src/product_hunter/clustering/similarity.py
   241 total   171 non-blank  src/product_hunter/clustering/clusters.py
   182 total   138 non-blank  src/product_hunter/clustering/adjudication.py
   666 total   601 non-blank  src/product_hunter/clustering/engine.py
  1422 total  1317 non-blank  src/product_hunter/db/clustering.py
    28 total    24 non-blank  config/clustering.yaml
   133 total   122 non-blank  db/migrations/0003_clustering.sql
   454 total   354 non-blank  tests/test_clustering_core.py
   665 total   578 non-blank  tests/test_clustering_engine.py
  1107 total   967 non-blank  tests/test_clustering_store.py
  5417 total  4712 non-blank  the twelve files together
```

The three edited files: `src/product_hunter/config.py`, 149 lines added, the clustering config models and the loader; `tests/test_db_migrations.py`, 16 lines changed, the schema version assertion, because the wave adds a migration; and `docs/reasonix/ARCHITECTURE.md`, 79 lines changed, the table count, the clustering engine section and the hazards.

## 8. The debts

The three `shortcut:` markers, quoted: the engine is quadratic in the corpus and the upgrade is a nearest-neighbour index, which is the Qdrant path; the package ships no command line and no worker handler, and the caller is the research stage; a merge or a split falls back to the predecessor's label when the anchor's pain point row is gone, and the upgrade is to read the pain point from the store once a wave prunes pain points.

## 9. The residuals

The real embedding path never ran, because `sentence-transformers` is not installed; the adjudicator never ran against a live model, only against a scripted double; the store has no caller, so no real run has ever written a cluster; the engine's quadratic cost is taken on trust from the `shortcut:` comment; the migration number `0003` collides with the tool-call wave's uncommitted `0003_research_gate.sql`, and one of the two files has to be renumbered before the branches merge, with the schema version assertion re-applied after the rename.

The repository root held another wave's uncommitted work while this wave ran, so the wave was moved into its own worktree at `.worktrees/wave2-clustering` and the root was left exactly as it was found. The branch is `wave2/clustering`; nothing was pushed and no other wave's branch was merged.

## 10. What was not checked

The real embedding path, the live adjudicator, the store against a real run, the store against a file-backed database, under concurrency, or against a database that predates the constraints, and the other waves' code against this store.

## 11. The tiers and the nodes

The run used the free tier. The nodes, in order: `loop-free-implement-1` for the build and for the three fix rounds, `loop-free-harden` twice, `loop-free-review-1` three times, and `loop-free-verify-code-1` and `loop-free-verify-code-2`, both of which failed with provider errors, so the orchestrator ran the code gate itself and recorded it. The stage 5 visual gate does not apply: no UI file changed, and the adapter says `visualRequired: never`.

```text
GOAL: decide which pieces of evidence describe the same underlying problem, and keep the history of the clusters
TIER: free
RISK: declared=high floor=high (db/migrations/** is an adapter high-risk path)
ADAPTER: Product Hunter [repo]
STAGES: 11/11 complete, skipped: 5b visual, no UI file changed and the adapter says visualRequired: never
GATES: uv run pytest -> 1087 passed, exit 0; clustering package -> 107 passed, exit 0; migrations -> 13 passed, exit 0
CHANGES: 30 new files, 3 edited files, 5417 lines of code, config, migration and tests
HARDEN: fix first, then ship
REVIEW: fix 1, 2 and 3 first, then Verdict: Ship., then Verdict: Ship.
WAIVERS: none
STOPPED BECAUSE: all pass and review ship
NEXT: merge with the other wave branches, after one of the two 0003 migrations is renumbered
```

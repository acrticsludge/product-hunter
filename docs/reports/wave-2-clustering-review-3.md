# Wave 2 clustering - third review

**Date:** 2026-10-21
**Node:** `loop-free-review-1`, after the short fix round
**Worktree:** `C:\Anubhav\Web Dev Projects\product-hunter\.worktrees\wave2-clustering`
**Branch:** `wave2/clustering`
**Gate:** `uv run pytest` gives 1087 passed, exit 0.

## What this change does

The wave adds a clustering engine that groups pain points by meaning, and a SQLite store that keeps the clusters, their members, their version history and the lineage between merged and split clusters. This round closed the one should-fix and the four nice-to-have items of the second review.

## The five items of the second review

1. **A cluster that lost its last member stays active - CLOSED.** `db/clustering.py:310-334` computes the status in the loop that gives a new version to a cluster that lost a member to a hold, writes it on the row and writes it on the snapshot. My own case: cl-1 holds pp-1 and pp-2, the next run holds both, and the row then reads status `dissolved`, version 2, member_count 0, with the snapshot saying `dissolved`. The new test `test_a_cluster_that_lost_its_last_member_is_dissolved` (`tests/test_clustering_store.py:847`) goes red when the `dissolved` branch is taken out. The status is inside the schema check (`db/migrations/0003_clustering.sql:36-37`) and the enum (`src/product_hunter/clustering/clusters.py:27`). It opened nothing: a dissolved cluster is not in `_active_cluster_ids()` (`db/clustering.py:1370-1375`), so no proposal can take over its id, and `merge_clusters` and `split_cluster` both refuse a cluster that is not active.
2. **The `decision_id` docstring described the old rule - CLOSED.** `src/product_hunter/clustering/clusters.py:233-238` now says that the function's id carries the content and that the store scopes the row id to the run.
3. **The migration comment named the provisional cluster id - CLOSED.** `db/migrations/0003_clustering.sql:13-16` now says the decision names the durable cluster the store allocated, and still names the two columns that carry no reference.
4. **A result that both holds and assigns a pain point - CLOSED.** `_assignments_by_pain` (`db/clustering.py:416-422`) refuses it, and it refuses before the transaction opens (`:266`), so nothing is written. The new test `test_a_pain_point_that_is_both_assigned_and_held_is_refused` (`tests/test_clustering_store.py:870`) goes red when the guard is taken out, and it asserts that the store holds no cluster row. The engine cannot produce that input: a held placement returns before the assignment is appended (`src/product_hunter/clustering/engine.py:198-199`), so the guard refuses only a malformed result.
5. **The test matrix did not name the new cases - CLOSED.** Section 13 of the spec now lists 37 rows, the original 18 plus 19 that name every case the two fix rounds added, ending with "A cluster that lost its last member | its status is `dissolved`" and "A pain point that is both assigned and held | refused with `ClusterStoreError`".

Five of five closed. The round opened nothing that a test does not catch.

## New findings

None.

## Verdict

`Verdict: Ship.`

The wave is sound. The engine decides membership on meaning, the store keeps the history, the vector space guard holds on every write path, and every rule the contract names has a test that goes red when the rule is taken out.

## Lean

No lean findings. The write functions are one, the export list is the plan's, and nothing in this round added a line that was not needed.

## Not checked

The real embedding path, because `sentence-transformers` is not installed. The adjudicator against a live model; only the scripted double ran. The behaviour under the corpus size Phase 1 will really produce; the quadratic limit is taken on trust from the `shortcut:` comment. The other waves' code against this store; the clustering package has no caller.

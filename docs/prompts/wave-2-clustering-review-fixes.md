# Dispatch: stage 7 fix round, wave 2 clustering

**Node:** `loop-free-implement-1` (same session as the build)
**Date:** 2026-10-21
**Worktree:** `C:\Anubhav\Web Dev Projects\product-hunter\.worktrees\wave2-clustering`
**Branch:** `wave2/clustering`

## Brief

The review returned `fix 1, 2 and 3 first`, with three must-fix items, eight should-fix items, three nice-to-have items and a lean pass. The spec has been amended where the review showed it was worded both ways. Close everything.

Work in the worktree above. Do not work in the repository root. Do not commit, do not push.

## The three must-fix items

**1. The domain penalty depends on which member has the smallest pain id** (`src/product_hunter/clustering/engine.py`, `_score`)

The centroid is penalised when the *anchor's* domain differs, so a cluster holding two domains flips the penalty with the id order. Change it to penalise the centroid when **any** member's domain differs from the pain point's domain. The review verified the change: both id orders then agree, and the suite stays green.

**2. A merge or a split can run in another vector space** (`src/product_hunter/db/clustering.py`, `merge_clusters` and `split_cluster`)

Both write `run.space_id` into the rows they touch and neither checks it against the space the touched rows were built in. The refusal lives only in `apply_result`. Read the `space_id` of the rows being touched and raise `MixedVectorSpaceError` when it differs from the run's space. `similarity.require_same_space` is the helper for this, and it is currently unused: use it.

**3. A split may drop a member and adopt a stranger** (`src/product_hunter/db/clustering.py`, `split_cluster`)

`split_cluster(parent, groups)` never checks that the groups cover the parent. Before any write, check that the union of the groups is exactly the parent's open members, and raise `ClusterStoreError` otherwise. A split never drops a pain point and never gives a child a member the parent never held.

## The eight should-fix items

**4. The durable id rule has no test** (`_match_clusters`). Add a store test: apply a result with cluster `cl-1` holding pp-1, then apply a result with cluster `cl-2` holding pp-1. Assert the durable id is still `cl-1`, the version did not move, and there is one cluster row. The reviewer proved this test was missing by patching the store to always keep the provisional id and watching the suite stay green.

**5. The "a pain point moved" path has no test** (`_close_moved`). Add a store test: apply `cl-1 = {pp-1}`, then `cl-2 = {pp-1}`. Assert pp-1's row in cl-1 is closed, cl-2 has an open one, and `current_memberships()` names pp-1 once. The reviewer proved it was missing by removing the function.

**6. The engine's space guard and the adjudication budget have no test.** Two tests: an embedder that returns a vector of the wrong length raises `MixedVectorSpaceError`; a config with `max_calls: 0` holds the pain point with reason `ambiguous_unadjudicated`. The reviewer proved both were missing by removing the checks.

**7. A second run's decisions are dropped** (`decision_id` carries no run id). The row id must be scoped to the run, so two runs that reach the same decision write two rows and the second run's model call leaves a record of its own. The same run applied twice still writes one row, so the idempotency holds. The store knows the run id, so the store is the right place to build the row id.

**8. A held pain point keeps its old membership.** The store says both that the pain point is in a cluster and that it was held, and the cluster's version never moves. The spec now says a held pain point belongs to no cluster, so close the membership of the held pain points and let the version move. A snapshot then records the change.

**9. The real embedder has no test.** One test: when `sentence-transformers` is not installed, `SentenceTransformerEmbedder(space).embed(["x"])` raises `EmbeddingUnavailable` and the message names the package. Use `pytest.importorskip` so the test skips on a machine that has the package, and assert the refusal on a machine that does not.

**10. The store does not check that a cluster's members match its assignments.** A result whose cluster lists pp-1 and pp-2 but assigns only pp-1 writes `member_count 2` and a snapshot of two members while only pp-1 has an open membership. In `_assignments_by_pain`, check that each cluster's `member_ids` equals the pain ids assigned to it, and raise `ClusterStoreError` otherwise.

**11. A merged or split cluster keeps its old version number.** After a merge the current row says `merged` at version 1 while the snapshot at version 1 says `active`. The spec now says a version moves when the status changes, because a status is part of what the cluster means. Write a new version row for a predecessor or a parent whose status changed, and update the current row's version to match. The old version rows stay: the history is kept, the number is not.

## The three nice-to-have items

**12. Every candidate is scored twice.** Have `_candidates` return the score it already computed and let `_place` use it.

**13. The decision's `cluster_id` is never written.** The column and the field are dead weight. Set the field where there is a cluster, and have the store write the *durable* cluster id from its mapping, so the stored row joins to the cluster the store allocated.

**14. The closed-membership count under-reports.** `ApplySummary.memberships_closed` counts the rows closed by `_close_members_of` and not the rows closed by `_close_moved`. Count both.

## The lean pass

**15.** `db/clustering.py` holds two near-copies of the same write: `_write_snapshot` and `_write_version`. Merge them into one function with a status argument.

**16.** `similarity.require_same_space` is exported and tested but nothing calls it. It is used by the must-fix item 2, so it stays.

**17.** `clusters.HeldReason.held_after_different` is never produced, because a pain point that no candidate wanted gets a cluster of its own. The spec has been amended to say so. Delete the member.

**18.** `engine._score` takes a `best_member` parameter that is dead: the function recomputes the best member and the branch that reads the parameter cannot be reached. Drop the parameter and its two call sites.

**19.** `src/product_hunter/clustering/__init__.py` exports about thirty names more than the plan's public list, and the store imports the submodules directly, so the extra names serve the tests only. Export the plan's list, and have the tests import what they need from the submodules.

## The tests

Every fix gets a test. The review proved three of the gaps by removing code and watching the suite stay green, so the new tests have to fail when the fix is removed. Do not weaken or delete an assertion. A fixture that was incomplete is fine to complete.

## The gate

`uv run pytest` green with exit code 0, and so must the clustering package's own run and `tests/test_db_migrations.py`.

## The radius

`src/product_hunter/clustering/engine.py`, `src/product_hunter/clustering/clusters.py`, `src/product_hunter/clustering/similarity.py`, `src/product_hunter/clustering/__init__.py`, `src/product_hunter/db/clustering.py`, `tests/test_clustering_core.py`, `tests/test_clustering_engine.py`, `tests/test_clustering_store.py`. The migration and `src/product_hunter/config.py` are not in this round's scope unless a change above needs them, and if it does, say so in the report.

## What to report back

- Every change, file by file, with the line count.
- The three gate runs, verbatim, with their exit codes.
- The new tests, one line each, and which fix each one pins.
- The lean total: the lines removed and the lines added.
- Anything you found while fixing that the review did not name.
- What you did not check.

# Wave 2 clustering - review report

**Date:** 2026-10-21
**Node:** `loop-free-review-1`
**Worktree:** `C:\Anubhav\Web Dev Projects\product-hunter\.worktrees\wave2-clustering`
**Branch:** `wave2/clustering`
**Spec:** `docs/reasonix/specs/wave-2-clustering.md`
**Files in radius:** the new clustering package, `src/product_hunter/db/clustering.py`, the clustering config, the `0003` migration, the three clustering test files, and the two edited files.

## What this change does

The wave adds a clustering engine. It reads accepted pain points, embeds them, and groups the ones that describe the same problem into clusters, with a model adjudicator for the pairs that land between two thresholds. A new SQLite store keeps the clusters, their members, their version history and the lineage between merged and split clusters, so a score attached to a cluster stays attached to what that cluster meant at the time. The package is a library and has no caller yet.

## Must fix

### 1. The domain penalty depends on which member has the smallest pain id

`src/product_hunter/clustering/engine.py:385`

- **What this is:** `_score` subtracts the domain penalty from the cluster centroid when the *anchor's* domain differs from the pain point's domain. The anchor is the member with the smallest pain id.
- **Problem:** in a cluster whose members sit in two domains, the centroid's penalty flips with the id order. The docstring at `engine.py:364-367` says "a penalty cannot be walked around through the centroid". It can. Case: the cluster holds pp-1 (finance) and pp-2 (software), and a new pain point pp-3 (finance) whose vector is closest to pp-2. With pp-1 as the anchor the centroid is not penalised, the score is 0.8988, and pp-3 merges silently with basis `semantic`, even though pp-2's own penalised score is 0.8281, under the 0.86 merge band. Swap the ids so pp-2 is the anchor and the same two members and the same third pain point give two clusters, because the centroid is then penalised and the pair goes to the adjudicator. The same two problems, a different answer, decided by an id.
- **Fix:** penalise the centroid when any member's domain differs: `center_penalized = any(m.domain != problem.domain for m in cluster.members)`. I ran that change against the suite: 85 passed, and both id orders now agree.
- **If we skip it:** the same two complaints cluster differently depending on an id, and the merge band is entered without the penalty the operator configured.

### 2. A merge can be run in another vector space

`src/product_hunter/db/clustering.py:861` and `:966`

- **What this is:** `merge_clusters` and `split_cluster` write `run.space_id` into the successor's row and the child's row. Neither checks it against the space the clusters they touch were built in. The refusal lives only in `apply_result` (`_refuse_another_space`, `db/clustering.py:326`).
- **Problem:** case: cl-1 and cl-2 are built in space A. `merge_clusters("cl-1", "cl-2", run=run_in_space_B)` succeeds. The successor's row and its snapshot say space B while its members' vectors are in space A, and `embedding_spaces()` now returns two spaces. After that the guard in `apply_result` refuses nothing, because the old space is still in the list. Rule 5 and spec section 5 say a space change is refused, not blended.
- **Fix:** in `merge_clusters` and `split_cluster`, read the `space_id` of the rows being touched and raise `MixedVectorSpaceError` when it differs from `run.space_id`.
- **If we skip it:** one merge puts two spaces in the store, and the guard that was meant to stop it stops working.

### 3. A split may drop a member and adopt a stranger

`src/product_hunter/db/clustering.py:984` and `:1000`

- **What this is:** `split_cluster(parent, groups)` closes every open membership of the parent and then opens the children's rows. It never checks that the groups cover the parent, and never checks that a group names a member of the parent.
- **Problem:** case: cl-1 holds pp-1 and pp-2. `split_cluster("cl-1", [["pp-1"]])` succeeds. pp-2 now has no open membership in any cluster and no row says where it went. The parent's row still says member_count 2, and its last snapshot still says `[pp-1, pp-2]` with status active. And `split_cluster("cl-1", [["pp-1"], ["pp-9"]])` gives the second child a member the parent never had, so its `split_from` edge points at a parent that never held it. A dropped member is also the way one cluster id can later be handed out twice: the dropped pain point is in no active cluster, so a later proposal anchored on it can take the id of a cluster that is still there.
- **Fix:** before the writes, check that the union of the groups is exactly the parent's members, and raise `ClusterStoreError` otherwise.
- **If we skip it:** a split loses a pain point with no trace, and the lineage can claim a child came from a parent that never held its members.

## Should fix

### 4. The durable id rule has no test

`src/product_hunter/db/clustering.py:366`

- **What this is:** `_match_clusters` gives a proposal the stored active cluster it overlaps most, so a cluster keeps its id when its anchor changes. That is the headline of spec section 8.
- **Problem:** I patched `_match_clusters` to always keep the provisional id, and the whole clustering suite still passed (85 tests). Every store test applies results whose cluster id is the same string in both runs, so no test ever sees a proposal take over a stored id. The rule is unpinned.
- **Fix:** add a store test: apply a result with cluster `cl-1` holding pp-1, then apply a result with cluster `cl-2` holding pp-1, and assert the durable id is still `cl-1`, the version did not move, and there is one cluster row.
- **If we skip it:** the store can start handing out a new id on every re-cluster, and the scoring wave's ids stop pointing at the same thing.

### 5. The "a pain point moved" path has no test

`src/product_hunter/db/clustering.py:399`

- **What this is:** `_close_moved` closes the old membership of a pain point that joined another cluster.
- **Problem:** I removed `_close_moved` and the suite still passed. The existing test keeps the pain point in the same cluster across runs, so the move never happens and the unique index on the open rows never fires.
- **Fix:** add a store test: apply `cl-1 = {pp-1}`, then `cl-2 = {pp-1}`; assert pp-1's row in cl-1 is closed, cl-2 has an open one, and `current_memberships()` names pp-1 once.
- **If we skip it:** the one rule that keeps a pain point in one cluster at a time is the part with no test.

### 6. The engine's space guard and the adjudication budget have no test

`src/product_hunter/clustering/engine.py:542` and `:422`

- **What this is:** `_embed` refuses vectors whose length differs from the space's dimensions, and `_adjudicate` stops after `max_calls`.
- **Problem:** I removed the dimension check and the suite passed. I removed the budget check and the suite passed. The rule-5 guard that runs in production is unpinned, and so is the budget that decides whether a pair is held.
- **Fix:** two small tests: an embedder that returns a vector of the wrong length must raise `MixedVectorSpaceError`; a config with `max_calls: 0` must hold the pain point with reason `ambiguous_unadjudicated`.
- **If we skip it:** a bad embedder or an exhausted budget changes the result with nothing to notice.

### 7. A second run's decisions are dropped

`src/product_hunter/clustering/clusters.py:227` and `src/product_hunter/db/clustering.py:591`

- **What this is:** `decision_id` is a digest of the kind, the relation, the two pain ids, the decision and the basis. It carries no run id.
- **Problem:** case: run-1 adjudicates pp-1 and pp-2 as `same` with provider prov-1 and confidence 0.75. Run-2 reaches the same decision with provider prov-2 and confidence 0.99. The row is written once: `decisions_written` for run-2 is 0, and the stored row still says prov-1 and 0.75. The second run's model call leaves no row at all, which is rule 6.
- **Fix:** put the run id in the decision digest. The same run applied twice still writes one row, so the idempotency holds.
- **If we skip it:** the decision history shows the first run's model and confidence for ever, and the independence wave reading the repost decisions misses every later run.

### 8. A held pain point keeps its old membership

`src/product_hunter/db/clustering.py:399` and `:629`

- **What this is:** `apply_result` closes the memberships of the pain points in the result's assignments. A held pain point has no assignment, so its old open membership is left alone, and the store also writes a held row for it.
- **Problem:** case: cl-1 holds pp-1 and pp-2. The next run holds both and clusters only pp-3. The store then says cl-1 is active with member_count 2 and both memberships open, and also says pp-1 and pp-2 are held. A reader that asks "which clusters is pp-1 in?" gets cl-1. A reader that asks "was pp-1 held?" gets a held row. Both are in the database and they disagree, and cl-1's version never moves, so no snapshot records the change.
- **Fix:** close the membership of the held pain points too and let the version move, or keep the membership and stop writing a held row for a pain point that is still a member. Pick one and let the store say it.
- **If we skip it:** the scoring wave reads clusters the run never confirmed, and the run and the store tell two stories.

### 9. The real embedder has no test at all

`src/product_hunter/clustering/similarity.py:103`

- **What this is:** `SentenceTransformerEmbedder` is the only embedder the wave ships, and the only path that runs in production.
- **Problem:** no test touches it. `sentence-transformers` is not installed, so the path an operator takes first is "the package is missing", and nothing pins that refusal. `EmbeddingUnavailable` is only checked for carrying a reason code.
- **Fix:** one test: `SentenceTransformerEmbedder(space).embed(["x"])` must raise `EmbeddingUnavailable`, and the message must name the package.
- **If we skip it:** the first real run fails with a stack trace instead of the error the wave promised.

### 10. The store does not check that a cluster's members match its assignments

`src/product_hunter/db/clustering.py:342` and `:504`

- **What this is:** the members the store opens come from the assignments, while the member count and the snapshot come from `cluster.member_ids`.
- **Problem:** case: a result whose cluster lists pp-1 and pp-2 but assigns only pp-1. The store writes member_count 2, the snapshot says `["pp-1","pp-2"]`, and only pp-1 has an open membership. The history says the cluster had two members; the current state has one.
- **Fix:** in `_assignments_by_pain`, also check that each cluster's `member_ids` equals the pain ids assigned to it, and raise `ClusterStoreError` otherwise.
- **If we skip it:** a snapshot can describe a membership that was never made.

### 11. A merged or split cluster keeps its old version number

`src/product_hunter/db/clustering.py:941` and `:1005`

- **What this is:** a merge sets the predecessor's status to merged and a split sets the parent's status to split in the `clusters` row, with no new version row.
- **Problem:** after a merge, `store.cluster("cl-1").status` is merged and `store.cluster("cl-1").version` is 1, but `store.cluster_versions("cl-1")[-1].status` is active. The number the current row carries names a snapshot that says something else. Spec section 8 says a version moves when the membership changes, and also says the predecessors keep their versions, so the contract is worded both ways here, but the two answers must not disagree.
- **Fix:** write a version row for the new status, or leave the version number on the row alone when only the status changes.
- **If we skip it:** a reader that rebuilds a cluster at its current version gets the wrong status.

## Nice to have

### 12. Every candidate is scored twice

`src/product_hunter/clustering/engine.py:237` and `:348`

- **What this is:** `_candidates` calls `_score` for every cluster to sort them, and `_place` calls `_score` again for the same cluster.
- **Problem:** the cosine work is doubled on the hot path. The quadratic limit is already recorded in the `shortcut:` comment; this is the part that is cheap to drop.
- **Fix:** have `_candidates` return the score it already computed and let `_place` use it.
- **If we skip it:** nothing breaks, the run is just slower than it needs to be.

### 13. The decision's `cluster_id` is never written

`src/product_hunter/clustering/clusters.py:145` and `src/product_hunter/clustering/engine.py:580`

- **What this is:** `ClusterDecision.cluster_id` and the `cluster_id` column of `cluster_decisions`.
- **Problem:** `_decision` never sets it, so every decision row stores NULL. The column and the field are dead weight.
- **Fix:** set it to the candidate's cluster id where there is one, or drop the field and the column.
- **If we skip it:** a reader of the decisions table has a column that is always empty.

### 14. The closed-membership count under-reports

`src/product_hunter/db/clustering.py:271` and `:298`

- **What this is:** `ApplySummary.memberships_closed` only counts the rows closed by `_close_members_of`.
- **Problem:** the rows closed by `_close_moved` are not counted, so a run that moved a pain point between clusters reports fewer closures than it made.
- **Fix:** count the rows `_close_moved` closes and add them to the summary.
- **If we skip it:** the summary is wrong, nothing else is.

## Lean

- `db/clustering.py:1107` `_write_snapshot` is a near-copy of `_write_version` at `:553`. Both write a `cluster_versions` row and have to change together. One function with a status argument: about 15 lines.
- `similarity.py:170` `require_same_space` is exported and tested, but nothing in the package calls it. The guard that runs is the dimension check in `_embed`. Delete it, or use it where two spaces are compared: 8 lines.
- `clusters.py:53` `held_after_different` is never produced; the docstring at `:44-48` says so, the engine gives the pain point a cluster of its own instead. Delete the member, or produce it: 7 lines.
- `engine.py:355` the `best_member` parameter of `_score` is dead. The function recomputes the best member and the only branch that reads the parameter cannot be reached. Drop it and the two call sites: 3 lines.
- `clustering/__init__.py:202` the export list holds about 30 names more than the plan's public list, and the store imports the submodules directly, so the extra names serve the tests only. Export the plan's list: about 30 lines.

## Verdict

`fix 1, 2 and 3 first`

## Not checked

The real embedding path, because `sentence-transformers` is not installed. The adjudicator against a live model; only the scripted double ran. The behaviour under the corpus size Phase 1 will really produce; the quadratic limit is taken on trust from the `shortcut:` comment. The other waves' code against this store.

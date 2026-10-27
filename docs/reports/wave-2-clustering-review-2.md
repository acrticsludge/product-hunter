# Wave 2 clustering - second review

**Date:** 2026-10-21
**Node:** `loop-free-review-1`, second pass after the fix round
**Worktree:** `C:\Anubhav\Web Dev Projects\product-hunter\.worktrees\wave2-clustering`
**Branch:** `wave2/clustering`
**Spec:** `docs/reasonix/specs/wave-2-clustering.md` (amended in five places by the fix round)
**Gate:** `uv run pytest` gives 1085 passed, exit 0. The clustering package's own run gives 105 passed.

## What this change does

Nothing moved since the first review in scope: the wave adds a clustering engine that groups pain points by meaning, and a SQLite store that keeps the clusters, their members, their version history and the lineage between merged and split clusters. What moved is the fix round, which closed the three must-fix items, the eight should-fix items, the three nice-to-have items and the five lean items of the first review, and amended the spec where the contract was worded both ways.

## The nineteen findings of the first review

Checked against the code, not against the fix round's report. "The old rule fails" means I put the pre-fix behaviour back at runtime and the named test went red.

### Must fix

1. **The centroid penalty followed the anchor - CLOSED.** `engine.py:406-408` penalises the centroid when any member sits in another domain, and the docstring at `:380-387` says so. My own reproduction (the same two members, both id orders) now returns the same clustering and the same score. The new test `test_the_domain_penalty_does_not_depend_on_the_anchor` (`tests/test_clustering_engine.py:262`) goes red under the old anchor-based rule: the two orders come out 1.0 and 0.974 instead of one value.
2. **A merge ran in another vector space - CLOSED.** `db/clustering.py:999` and `:1122` call `_require_space` (`:1308-1322`), which compares the `space_id` of the rows being touched with `run.space_id` through `require_same_space`. A merge in space B is refused with `MixedVectorSpaceError`, nothing is written, and `embedding_spaces()` still holds one space. Both new tests go red when the check is removed.
3. **A split dropped a member or adopted a stranger - CLOSED.** `db/clustering.py:1123-1134` refuses before the transaction opens when the groups leave a member out or name one the parent never held. Both of my cases are refused with `ClusterStoreError` and the parent keeps both members. Both new tests go red when the check is removed.

### Should fix

4. **The durable id rule had no test - CLOSED.** `test_the_durable_id_is_kept_when_the_proposal_is_renamed` (`tests/test_clustering_store.py:787`). It goes red when `_match_clusters` is replaced by a pass-through.
5. **The path that closes a moved membership had no test - CLOSED.** `test_a_pain_point_that_moved_leaves_one_open_membership` (`:804`). It goes red when `_close_moved` is a no-op, and the unique index fires.
6. **The engine's dimension check and the call budget had no test - CLOSED.** `test_a_vector_of_another_length_stops_the_run` (`tests/test_clustering_engine.py:330`) and `test_a_budget_of_no_calls_holds_the_pain_point` (`:340`). Each goes red when its rule is removed.
7. **A second run's decisions were dropped - CLOSED.** `_scoped_decision_id` (`db/clustering.py:1383-1390`) puts the run id in the row id. My case: two runs that reach the same decision write two rows, each with its own provider and confidence. The new test goes red when the scoping is removed, and `test_the_same_run_applied_twice_writes_one_decision_row` keeps the apply idempotent.
8. **A held pain point kept its membership - CLOSED.** `_close_held` (`db/clustering.py:467-489`) closes the membership, and the clusters that lose a member this way get a new version and a snapshot (the loop at `:308-327`). Both new tests go red when the path is removed. One residue is left, in the findings below.
9. **The real embedder had no test - CLOSED.** `tests/test_clustering_core.py:195` calls `SentenceTransformerEmbedder.embed` and expects `EmbeddingUnavailable` naming the package.
10. **A cluster could name a member it did not assign - CLOSED.** `_assignments_by_pain` (`db/clustering.py:395-406`) refuses it. The new test goes red when the check is removed.
11. **A merged or split cluster kept its old version number - CLOSED.** The merge writes version+1 and a snapshot with status `merged` (`db/clustering.py:1060-1077`); the split does the same for the parent (`:1158-1174`). Both new tests go red when the version move is removed.

### Nice to have

12. **Every candidate was scored twice - CLOSED.** `_Candidate` (`engine.py:147-155`) carries the score, and `_place` uses it (`:246-249`).
13. **The decision row never carried a cluster id - CLOSED.** The store maps the provisional id to the durable one (`db/clustering.py:702-704`), and `test_the_decision_row_carries_the_durable_cluster_id` checks it. Two descriptions of the old behaviour were left behind, in the findings below.
14. **The closed count under-reported - CLOSED.** `closed + moved + held_closed` (`db/clustering.py:335`), with `test_the_closed_count_covers_a_row_that_moved`.

### Lean

15. **The two near-copy write functions - CLOSED.** `_write_snapshot` is gone; only `_write_version` (`db/clustering.py:641`) is left.
16. **`require_same_space` was dead code - CLOSED.** It takes two space ids now (`similarity.py:170-181`) and the store calls it (`db/clustering.py:1322`).
17. **`HeldReason.held_after_different` was never produced - CLOSED.** The enum holds three reasons (`clusters.py:38-51`), and the spec's section 6 names three.
18. **The dead `_score` parameter - CLOSED.** `_score` takes three parameters now (`engine.py:374-379`).
19. **The export list was wider than the plan's - CLOSED.** `__init__.py:167-191` holds the plan's 23 names and nothing more. The tests import from the submodules, and nothing in `src` imports the package root, so no name that is still needed went missing.

Nineteen of nineteen closed. The fix round opened nothing that a test does not catch.

## Must fix

None.

## Should fix

### 1. A cluster that lost its last member stays active
`src/product_hunter/db/clustering.py:308`

- **What this is:** the loop that gives a new version to a cluster that lost a member to a held pain point. It sets `version`, `member_count` and `updated_at`, and leaves the status alone.
- **Problem:** case: cl-1 holds pp-1 and pp-2. The next run holds both, so the store closes both memberships and the loop runs. The row then reads status `active`, member_count 0, anchor pp-1, and the label of pp-1, a pain point that is now in no cluster. `store.cluster("cl-1")` reports an active cluster with no members, and the scoring wave reads cluster ids and versions. The status that fits is `dissolved`, which the enum (`clusters.py:27`) and the schema check name, and which nothing in the repository ever writes.
- **Fix:** in that loop, set `status = 'dissolved'` when the remaining member list is empty, and write the snapshot with that status.
- **If we skip it:** a cluster that means nothing stays active for ever, and the one status that would say so is never used.

## Nice to have

### 2. The `decision_id` docstring describes the rule the fix round removed
`src/product_hunter/clustering/clusters.py:233-236`

- **What this is:** the docstring says "The id carries the content and not the run, so writing the same decision twice leaves one row: the second write changes nothing."
- **Problem:** the store now scopes the row id to the run (`db/clustering.py:1383-1390`), so two runs that reach the same decision write two rows. The function still carries no run, so the sentence is true of the function and false of the row. A reader of the function would draw the wrong conclusion about the table.
- **Fix:** say that the function's id carries the content, and that the store scopes it to the run.
- **If we skip it:** nothing breaks, the next reader of the decision table is misled about how it is keyed.

### 3. The migration comment still says a decision names the provisional cluster id
`db/migrations/0003_clustering.sql:13-15`

- **What this is:** the header comment says "a decision names the engine's provisional cluster id and a pair of pain points".
- **Problem:** the store writes the durable cluster id now (`db/clustering.py:702-704`), and the spec's section 10 says the same. The comment is the one place left that says the old thing, and it is the place a reader goes to understand the two columns that carry no foreign key.
- **Fix:** change "the engine's provisional cluster id" to "the durable cluster the decision was about" in that comment.
- **If we skip it:** the schema and the spec and the code disagree on what one column holds.

### 4. The test matrix does not name the cases the amended rules now require
`docs/reasonix/specs/wave-2-clustering.md:318-339`

- **What this is:** section 13 lists the cases the wave is judged against.
- **Problem:** the fix round added nine behaviours to the contract (the centroid penalty, the held pain point leaving its cluster, the version moving on a status change, the split coverage, the merge and the split in one space, the run-scoped decision row, the durable id rule, the members against the assignments, the closed count) and nine tests, but the matrix names none of them. A reader auditing the wave against section 13 does not learn that a merge in another space must be refused.
- **Fix:** add the nine rows to the matrix.
- **If we skip it:** the contract and its test list drift apart, and the next wave cannot tell which of the new rules are meant to hold.

### 5. A result that both holds and assigns a pain point leaves the member count wrong
`src/product_hunter/db/clustering.py:370-407`

- **What this is:** the store refuses a result that puts a pain point in two clusters, and now also one whose cluster names a member it does not assign. It does not look at `result.held`.
- **Problem:** case: a result whose cluster holds pp-1 and whose `held` list also names pp-1. `_close_held` closes pp-1's membership, the main loop sees the stored members equal the wanted members and skips the cluster, so nothing is reopened. The row then reads member_count 1 and version 1 with no open membership, pp-1 has a held row and an assignment, and the version never moved. The engine cannot produce this, but the store refuses every other malformed result it can see.
- **Fix:** in `_assignments_by_pain`, raise `ClusterStoreError` when a pain point is both assigned and held.
- **If we skip it:** a malformed result leaves a row that counts a member the store does not have.

## Verdict

`Verdict: Ship.`

The three must-fix items are closed in the code and pinned by tests that fail when the old rule goes back in. Nothing in the fix round opened a hole the tests miss. The should-fix item is a one-line change in the loop that already exists; the nice-to-have items are two stale sentences and two small gaps.

## Lean

No lean findings left. The five of the first round are closed: one write function, `require_same_space` in use, three held reasons, three parameters on `_score`, and the plan's export list.

## Not checked

The real embedding path, because `sentence-transformers` is not installed; the refusal is pinned by a test, the encode path is not. The adjudicator against a live model; only the scripted double ran. The behaviour under the corpus size Phase 1 will really produce; the quadratic limit is taken on trust from the `shortcut:` comment. The other waves' code against this store. The clustering package has no caller, so nothing outside the tests exercises the trimmed export list.

# Dispatch: stage 7 final fix round, wave 2 clustering

**Node:** `loop-free-implement-1` (same session as the build)
**Date:** 2026-10-21
**Worktree:** `C:\Anubhav\Web Dev Projects\product-hunter\.worktrees\wave2-clustering`
**Branch:** `wave2/clustering`

## Brief

The re-review returned `Verdict: Ship`. Nineteen of nineteen findings from the first review are closed. Five small items are left. Close them, run the gates, and stop.

Work in the worktree above. Do not work in the repository root. Do not commit, do not push.

## The one should-fix item

**A cluster that lost its last member stays active** (`src/product_hunter/db/clustering.py`, the loop that gives a new version to a cluster that lost a member to a held pain point, around line 308).

Case: cl-1 holds pp-1 and pp-2. The next run holds both, so the store closes both memberships and the loop runs. The row then reads status `active`, member_count 0, and the anchor and label of pp-1, a pain point that is now in no cluster. The status that fits is `dissolved`. The enum and the schema check already name it, and nothing in the repository ever writes it.

Fix: in that loop, set the status to `dissolved` when the remaining member list is empty, and write the snapshot with that status. Add a test: a cluster that loses its last member to a hold reads `dissolved`, and its snapshot says `dissolved`.

## The four nice-to-have items

**The `decision_id` docstring describes the rule the fix round removed** (`src/product_hunter/clustering/clusters.py`, around line 233). The docstring says the id carries the content and not the run, "so writing the same decision twice leaves one row". The store now scopes the row id to the run, so two runs that reach the same decision write two rows. Say that the function's id carries the content and that the store scopes it to the run. A comment, nothing more.

**The migration comment still says a decision names the provisional cluster id** (`db/migrations/0003_clustering.sql`, the header, around lines 13 to 15). The store writes the durable cluster id now, and the spec's section 10 says the same. Change the sentence so the schema, the spec and the code agree.

**A result that both holds and assigns a pain point leaves the member count wrong** (`src/product_hunter/db/clustering.py`, `_assignments_by_pain`). The store refuses a result that puts a pain point in two clusters, and one whose cluster names a member it does not assign. It does not look at `result.held`. A result whose cluster holds pp-1 and whose `held` list also names pp-1 leaves a row that counts a member the store does not have. Fix: in `_assignments_by_pain`, raise `ClusterStoreError` when a pain point is both assigned and held. Add a test.

**The spec's test matrix did not name the new cases.** The orchestrator has amended `docs/reasonix/specs/wave-2-clustering.md` section 13 with the twenty-two rows the fix rounds added. You do not need to touch the spec. Read the amended section 13 before you start, so the tests you write match the matrix.

## The gate

`uv run pytest` green with exit code 0, and so must the clustering package's own run and `tests/test_db_migrations.py`.

## The radius

`src/product_hunter/db/clustering.py`, `src/product_hunter/clustering/clusters.py`, `db/migrations/0003_clustering.sql`, `tests/test_clustering_store.py`. The spec is already amended; leave it alone.

## What to report back

- Every change, file by file, with the line count.
- The three gate runs, verbatim, with their exit codes.
- The new tests, one line each, and which rule each one pins.
- Anything you found while fixing that the review did not name.
- What you did not check.

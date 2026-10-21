# Dispatch: stage 6 fix round, wave 2 clustering

**Node:** `loop-free-implement-1` (same session as the build)
**Date:** 2026-10-21
**Worktree:** `C:\Anubhav\Web Dev Projects\product-hunter\.worktrees\wave2-clustering`
**Branch:** `wave2/clustering`

## Brief

The harden pass returned `fix first` with three must-fix items in the migration and three should-fix items in the store. Close them. The migration has not shipped, so the columns and the constraints go into `db/migrations/0003_clustering.sql` itself. Do not add a `0004` migration.

## The three must-fix items

**1. The migration declares no foreign keys.** `PRAGMA foreign_keys = ON` is set at connection time, but a column without a `FOREIGN KEY` clause is not enforced. Add the references:

- `cluster_members.cluster_id` references `clusters(cluster_id)`
- `cluster_members.pain_id` references `pain_points(pain_id)`
- `cluster_versions.cluster_id` references `clusters(cluster_id)`
- `cluster_lineage.from_cluster_id` references `clusters(cluster_id)`
- `cluster_lineage.to_cluster_id` references `clusters(cluster_id)`
- `cluster_decisions.run_id` references `clustering_runs(run_id)`
- `clusters.merged_into` references `clusters(cluster_id)`
- `clusters.split_from` references `clusters(cluster_id)`

Two references are deliberately left out, and the reason is in the report you write back:

- `cluster_decisions.cluster_id` holds the engine's provisional cluster id, not the durable id the store allocates. A reference to `clusters(cluster_id)` would reject every decision row. Leave the column free.
- `cluster_decisions.pain_a` and `pain_b` are left free for the same reason as `cluster_members.pain_id` is not: a decision is a record about two pain points and must be writable even when one of them has left the store.

**2. The status column is not constrained.** `clusters.status` and `cluster_versions.status` are `TEXT NOT NULL` with no check. Add `CHECK (status IN ('active','merged','split','dissolved'))` to both, which is the four values of `ClusterStatus` in `src/product_hunter/clustering/clusters.py`.

**3. There is no database backstop for one open membership per pain point.** The spec's rule is that a pain point is in one cluster at a time. It is enforced in Python only. Add:

```sql
CREATE UNIQUE INDEX idx_cluster_members_one_open ON cluster_members(pain_id) WHERE valid_to IS NULL;
```

This will change the write order in `merge_clusters` and `split_cluster`: the predecessor's memberships have to be closed before the successor's are opened, or the insert hits the index. Fix the order in the store, not the index.

## The three should-fix items

**4. The adjudicator's confidence is computed and thrown away.** Add a `confidence REAL` column to `cluster_decisions` and write the confidence of the adjudication behind the decision. The held rows get a null confidence, because a held pain point has no answer.

**5. The reason a pain point was held is dropped.** Add a `detail TEXT` column to `cluster_decisions` and write `held.detail` into it. The detail is a fixed phrase or an exception type name, never raw untrusted text.

**6. `params_json` is written with `default=str`.** `json.dumps(run.params, sort_keys=True, default=str)` turns anything it cannot serialise into a string instead of failing. Drop the `default=str` so a bad params object fails loudly at the write.

## One thing the harden pass asked for that this wave will not do

The harden pass asked for the adjudicator's free-text reason to be persisted, and for the text sent to the adjudicator to be recorded. Neither will be done, and the reason is the spec's own rule in section 12: a persisted detail never carries the raw text of a quote or of a model answer. The adjudicator's reason is part of the model answer. The text sent to the adjudicator is already reconstructible: a decision row names `pain_a` and `pain_b`, and the canonical text of a pain point is a pure function of the row in `pain_points`. Record neither. Say so in the report you write back.

## The tests

- Every new constraint gets a test: a membership that would open a second open row for one pain point is refused; a cluster row with a bad status is refused; a membership that names a pain point the store does not hold is refused; a lineage edge that names a cluster that does not exist is refused.
- If a test that writes a membership does not seed the pain point into `pain_points` first, seed it. That is a fixture that was incomplete, not an assertion to weaken. Do not change an assertion.
- The idempotent apply, the merge, the split, the evidence lineage and the space refusal keep passing.

## The gate

`uv run pytest` must be green, exit code 0, and so must the clustering package's own run and `tests/test_db_migrations.py`.

## The radius

Only the files listed in the handoff's section 6 may be edited, and the two that this round will touch are `db/migrations/0003_clustering.sql` and `src/product_hunter/db/clustering.py`, plus the test files `tests/test_clustering_store.py` and, if a test needs it, `tests/test_db_migrations.py`.

## What to report back

- Every change, file by file, with the line count.
- The three gate runs, verbatim, with their exit codes.
- The write order you changed in `merge_clusters` and `split_cluster`, and why the old order broke the new index.
- Which tests you added for the new constraints.
- Anything you found while fixing that the harden pass did not name.
- What you did not check.

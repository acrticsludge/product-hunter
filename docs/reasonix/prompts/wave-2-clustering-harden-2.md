# Dispatch: stage 6 re-harden, wave 2 clustering

**Node:** `loop-free-harden`
**Date:** 2026-10-21
**Worktree:** `C:\Anubhav\Web Dev Projects\product-hunter\.worktrees\wave2-clustering`
**Branch:** `wave2/clustering`

## Brief

The first harden pass returned `fix first` with three must-fix items in the migration and three should-fix items in the store. A fix round has closed them. Re-run the pass over the wave and say whether the findings are closed and whether the fixes opened anything new.

Work in the worktree above. Do not run anything in the repository root: that tree holds another wave's work.

You do not edit code. You report.

## What the fix round changed

- `db/migrations/0003_clustering.sql` gained eight foreign key references, two `CHECK (status IN ('active','merged','split','dissolved'))` constraints, the partial unique index `idx_cluster_members_one_open ON cluster_members(pain_id) WHERE valid_to IS NULL`, and the `confidence REAL` and `detail TEXT` columns on `cluster_decisions`.
- `src/product_hunter/db/clustering.py` writes the two new columns, the held detail, and lost `default=str` from the params serialisation.
- `split_cluster` now closes the parent's memberships before opening the children's, because the new partial index counts the parent's open rows.
- `ClusterDecision` gained a `confidence` field, and the engine puts the adjudicator's confidence on the decision record.

## What the fix round deliberately did not do, and why

The first pass asked for the adjudicator's free-text reason and for the text sent to the adjudicator to be persisted. Neither is persisted. The spec's section 12 says a persisted detail never carries the raw text of a quote or of a model answer, and the adjudicator's reason is part of the model answer. The input is reconstructible without storing it: a decision row names `pain_a` and `pain_b`, and the canonical text of a pain point is a pure function of its row in `pain_points`.

The first pass also asked for a foreign key on `cluster_decisions.cluster_id`. There is none, because that column holds the engine's provisional cluster id and not the durable id the store allocates.

Judge these two decisions on their merits. Say plainly if you disagree.

## What to look at now

- Are the foreign keys correct and complete, and does the write order in every path satisfy them? Read `apply_result`, `merge_clusters` and `split_cluster` and check the order of the writes against the constraints.
- Does the partial unique index hold everywhere, and is there a path that closes a membership twice?
- Do the new columns break the idempotent apply, the re-run, the version bump, the merge, the split, the evidence lineage or the space refusal?
- Is the new `confidence` value bounded and does it reach the row?
- Is the `detail` value ever raw untrusted text?
- Is anything else in the wave still open, that the first pass did not name?

## What to report

- For each of the six findings from the first pass: closed, partly closed, or still open, with the evidence.
- Any new finding the fixes opened, with the file, the line, the class of the problem and the fix.
- Anything else that is still open in the wave.
- A verdict: `ship`, `fix first`, or `block`, with the reason in one line.

Say plainly when you find nothing. Do not manufacture findings, and do not report a style preference as a gap.

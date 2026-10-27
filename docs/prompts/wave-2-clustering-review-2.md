# Dispatch: stage 7 re-review, wave 2 clustering

**Node:** `loop-free-review-1` (same session as the first review)
**Date:** 2026-10-21
**Worktree:** `C:\Anubhav\Web Dev Projects\product-hunter\.worktrees\wave2-clustering`
**Branch:** `wave2/clustering`

## Brief

You reviewed this wave and returned `fix 1, 2 and 3 first`, with three must-fix items, eight should-fix items, three nice-to-have items and five lean findings. A fix round has closed all nineteen. Check that each one is closed, that the fixes did not open anything new, and that the wave is now sound.

Work in the worktree above. You change no code. You produce a report.

## What the fix round changed

Must fix:

1. The centroid is penalised when any member's domain differs, not only the anchor's.
2. `merge_clusters` and `split_cluster` read the `space_id` of the rows they touch and refuse another space with `MixedVectorSpaceError`. `require_same_space` now takes two space ids and is used there.
3. `split_cluster` checks that the union of the groups is exactly the parent's open members, and refuses before any write.

Should fix, each with a new test: the durable id rule, the path that closes the membership of a pain point that moved, the engine's dimension check, the adjudication call budget, the run-scoped decision row id, the held pain point losing its membership, the real embedder's refusal, the check that a cluster's members match its assignments, and the version moving when the status changes.

Nice to have: the candidates are scored once, the decision row carries the durable cluster id, the closed count covers the moved and the held rows.

Lean: the two near-copy write functions are one, the never-produced `HeldReason.held_after_different` is gone, the dead `_score` parameter is gone, and the package's export list is the plan's list.

The spec was amended where your review showed it was worded both ways: the held-reason vocabulary, the rule that a version moves when the status changes, the rule that a split's groups must cover the parent, the rule that a merge and a split run in one vector space, and the run-scoped decision row.

## What to do

- For each of your nineteen findings: closed, partly closed, or still open, with the evidence. Check the code, not the report.
- Look for what the fixes opened. Pay attention to the changed write order in the store, to the held pain point path, to the run-scoped decision id, and to the trimmed export list: the tests now import from the submodules, so a name that is no longer exported may still be needed.
- Read the diff of the fix round against what you reviewed and look for anything the round changed that no finding asked for.
- Say whether the wave is sound now.

## What to report

The same format as your first report: what this change does, the findings in the three groups, the `Verdict:` line, the `Lean:` line and the `Not checked:` line. A finding needs a concrete case. No case, no finding.

Do not manufacture findings to show the round was thorough, and do not repeat a finding that is closed.

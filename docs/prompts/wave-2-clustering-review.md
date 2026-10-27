# Dispatch: stage 7 REVIEW, wave 2 clustering

**Node:** `loop-free-review-1`
**Date:** 2026-10-21
**Worktree:** `C:\Anubhav\Web Dev Projects\product-hunter\.worktrees\wave2-clustering`
**Branch:** `wave2/clustering`

## Brief

Review the wave 2 clustering engine the way the senior developer who will be paged when it breaks would. Your job is to try to prove the change wrong, not to praise it.

Work in the worktree above. Do not run anything in the repository root: that tree holds another wave's work.

You change no code. You produce a report.

## The rubric

Use the `ponytail-review` skill. Read it before you start. Its order of importance is correct, safe, holds under load, tested, fast, lean. Its output format is the one to follow: findings in three groups, `Must fix`, `Should fix`, `Nice to have`, each finding with what this is, the problem, the fix and what happens if it is skipped, a `Verdict:` line, a `Lean: -<N> lines possible.` line when lean findings exist, and a `Not checked:` line.

## What the change is

The engine decides which pieces of evidence describe the same underlying problem. It never decides whether a problem is a good opportunity, and it never counts mentions, authors, communities or people.

New files:

```text
src/product_hunter/clustering/__init__.py
src/product_hunter/clustering/problems.py
src/product_hunter/clustering/similarity.py
src/product_hunter/clustering/clusters.py
src/product_hunter/clustering/adjudication.py
src/product_hunter/clustering/engine.py
src/product_hunter/db/clustering.py
config/clustering.yaml
db/migrations/0003_clustering.sql
tests/test_clustering_core.py
tests/test_clustering_engine.py
tests/test_clustering_store.py
```

Edited files: `src/product_hunter/config.py` (the clustering config models and the loader) and `tests/test_db_migrations.py` (the schema version assertion, because the wave adds a migration).

The new files are untracked, so `git diff` shows only the two edits. Read the new files themselves.

## The contract

`docs/reasonix/specs/wave-2-clustering.md` is the contract. Section 2 holds the seven rules, section 6 the decision pipeline, section 8 the cluster identity, versioning and lineage rules, section 10 the schema, section 12 the error taxonomy and section 13 the test matrix.

`docs/reasonix/plans/wave-2-clustering.md` holds the layout, the public names and the two named limits.

`docs/reasonix/handoffs/wave-2-clustering-handoff.md` holds the map of the code that already exists.

## The state of the work

- The code gate passes: `uv run pytest` gives 1065 passed, exit 0. The clustering package's own run gives 85 passed. The migration run gives 13 passed.
- The harden pass ran twice. The first returned `fix first` with three must-fix items in the migration and three should-fix items in the store. The fix round closed all six, and the second pass returned `ship`. Its report is in `docs/reasonix/reports/wave-2-clustering-harden-2.md`.
- The `sentence-transformers` package is not installed, so the real embedding path has never run. The tests inject a scripted embedder and a scripted adjudicator.

## What to check hardest

- **The decision logic.** Does the engine ever merge two pain points that are not the same problem, or split two that are? The two rules that matter: a shared word never merges, a different wording never splits. Try to construct the input that breaks each.
- **The history.** A merge, a split and a re-cluster must never rewrite what an older cluster meant. Look for a path that mutates a closed row, drops a version, or reinterprets an old membership.
- **The identity.** The store owns the durable cluster id, the engine proposes a provisional one. Look for the path where a cluster loses its id, or where two clusters end up with the same one.
- **The vector space guard.** Can two spaces be compared anywhere? Can a result from another space be applied?
- **The store's writes.** Is any write able to lose history, double-close a membership, or leave a pain point in two clusters?
- **The tests.** Do they actually pin the behaviour, or do they restate the implementation? A test that would still pass if the rule were broken is a missing test.
- **The lean pass.** What in this wave should not exist? The `shortcut:` comments name two known limits and those are decisions, not findings. Look for other code that should not be there, for a helper the repository already has, and for a near-copy that has to change with another.

## What to report

The ponytail-review report, in full, in the skill's format. Every finding needs a concrete case: the input or situation that leads to the wrong result. No case, no finding.

Finish with the `Verdict:` line, the `Lean:` line and the `Not checked:` line.

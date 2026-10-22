# Dispatch: stage 9 SHIP and stage 10 REPORT, wave 2 clustering

**Node:** `loop-free-scribe`
**Date:** 2026-10-21
**Worktree:** `C:\Anubhav\Web Dev Projects\product-hunter\.worktrees\wave2-clustering`
**Branch:** `wave2/clustering`

## Brief

Two jobs, in this order: commit the wave, then write the report and commit the
report. You write no code. Do not change a source file, a test or a config.

Work in the worktree above. Do not run anything in the repository root
`C:\Anubhav\Web Dev Projects\product-hunter`: that tree holds another wave's
work. Do not push. Do not merge.

## Job 1, the commit

Stage exactly these paths. Use explicit paths; never `git add -A` and never
`git add .`.

```text
src/product_hunter/clustering/__init__.py
src/product_hunter/clustering/problems.py
src/product_hunter/clustering/similarity.py
src/product_hunter/clustering/clusters.py
src/product_hunter/clustering/adjudication.py
src/product_hunter/clustering/engine.py
src/product_hunter/db/clustering.py
src/product_hunter/config.py
config/clustering.yaml
db/migrations/0003_clustering.sql
tests/test_clustering_core.py
tests/test_clustering_engine.py
tests/test_clustering_store.py
tests/test_db_migrations.py
docs/reasonix/ARCHITECTURE.md
docs/reasonix/decisions/2026-10-21-cluster-identity-and-lineage.md
docs/reasonix/handoffs/wave-2-clustering-handoff.md
docs/reasonix/plans/wave-2-clustering.md
docs/reasonix/prompts/wave-2-clustering-implement.md
docs/reasonix/prompts/wave-2-clustering-verify.md
docs/reasonix/prompts/wave-2-clustering-harden.md
docs/reasonix/prompts/wave-2-clustering-harden-fixes.md
docs/reasonix/prompts/wave-2-clustering-harden-2.md
docs/reasonix/prompts/wave-2-clustering-review.md
docs/reasonix/prompts/wave-2-clustering-review-fixes.md
docs/reasonix/prompts/wave-2-clustering-review-2.md
docs/reasonix/prompts/wave-2-clustering-review-3.md
docs/reasonix/reports/wave-2-clustering-harden-2.md
docs/reasonix/reports/wave-2-clustering-review.md
docs/reasonix/reports/wave-2-clustering-review-2.md
docs/reasonix/reports/wave-2-clustering-review-3.md
docs/reasonix/specs/wave-2-clustering.md
```

The commit message, in the house style, one subject line and a short body:

```text
Phase 4 wave 2: problem clustering engine

Groups pain points by meaning, keeps the history of the clusters it makes,
and records the model provenance of every decision. The engine is a pure
function of its inputs; the store owns the durable cluster id and every
write. A merge and a split create new ids, the predecessors keep their
rows, and a lineage edge points at the successor.
```

Commit with `git commit` and the staged paths. Do not use `--amend` and do not
use `--no-verify`. Report the commit hash.

## Job 2, the report

Write `docs/reasonix/reports/wave-2-clustering.md`, then commit it as a second
commit. The report names the first commit's hash, so the report cannot be in
the first commit.

Follow the shape of `docs/reasonix/reports/phase-4-run-report.md`: a title with
the wave, the node and the date; then numbered sections; then a closing section
on what was not checked. Plain English, short sentences. No filler, no praise.

The report has to answer these, with these facts, and it must not contradict
them:

**The header.** RUN_ID `wave2-clustering-20261021`, node REPORT (stage 10), the
date, the commit hash from job 1, the risk: declared high, floor high, because
the radius touches `db/migrations/**`, which the adapter lists as a high-risk
path. The adapter is `Product Hunter`, from `.loop/adapter.json`.

**Section 1, what the wave delivered.** The problem clustering engine. It
decides which pieces of evidence describe the same underlying problem, and it
keeps the history of those clusters. It does not decide whether a problem is a
good opportunity, and it produces no count of mentions, authors, communities or
people. The code lives in `src/product_hunter/clustering/` (six modules: the
pain point records, the vector space, the cluster shapes, the adjudicator, the
engine and the exports), `src/product_hunter/db/clustering.py`, the config file
`config/clustering.yaml` with its loader in `src/product_hunter/config.py`, the
migration `db/migrations/0003_clustering.sql` with six tables, and three test
files.

**Section 2, the gates.** These three lines verbatim, with the exit codes:

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

**Section 3, the two security passes.** The first pass returned `fix first`
with three must-fix items and three should-fix items: the migration declared no
foreign keys, the status column was not constrained, there was no database
backstop for one open membership per pain point, the adjudicator's confidence
was computed and thrown away, the reason a pain point was held was dropped, and
`params_json` was written with `default=str`. A fix round closed all six and
the second pass returned `ship`. Quote the two verdicts and the six items.

One decision the first pass asked for was declined on purpose, and the reason
belongs in the report: the adjudicator's free-text reason and the text sent to
the adjudicator are not persisted, because the spec says a persisted detail
never carries the raw text of a model answer, and the input is reconstructible
from the two pain ids a decision row already names.

**Section 4, the three review passes.** The first review returned `fix 1, 2 and
3 first` with three must-fix items, eight should-fix items, three nice-to-have
items and five lean items. The three must-fix items: the domain penalty
depended on which member had the smallest pain id, so the same two problems
clustered differently depending on an id; a merge or a split could run in
another vector space; a split could drop a member or adopt a stranger. A fix
round closed all nineteen, the spec was amended where the contract was worded
both ways, the re-review returned `Ship` with one should-fix and four
nice-to-have items, a last round closed those five, and the final confirmation
returned `Verdict: Ship` with no new findings. Quote the verdicts. The review
proved three of its own gaps by removing code and watching the suite stay green,
and the fix round added twenty-two tests that go red when the rule is taken
out.

**Section 5, the model-routing assumptions.** The wave names no model and no
threshold in code. The adjudication model comes from the existing routing
layer: `ModelRole.clustering` in `src/product_hunter/llm/routing.py`, already
bound in `config/model_routing.yaml`, which the wave does not change. The
embedding model comes from `config/clustering.yaml`, and `sentence-transformers`
is imported lazily and is not a dependency of the repository. Every call
records the provider, the model, the model version, the prompt version and the
config version. A vector space is identified by a digest of the provider, the
model, the model version and the dimension count, and two spaces are never
compared: the comparison, the apply, the merge and the split all refuse.

**Section 6, the integration points.** The research contract's `clustering`
stage declares the outputs `cluster_assignments`, `similarity_checks`,
`cluster_neighbors_checked`, `neighbor_check` and `duplicate_review`, the
quality checks `nearest_neighbor_checked` and `duplicate_candidates_reviewed`,
and the threshold `min_neighbors_checked_per_cluster: 5`; the engine's result
carries the matching records and `clustering_artifacts()` turns them into
`ArtifactRecord` values of those kinds. The `pain_points` and `evidence` tables
feed the engine. The independence wave reads the decisions with relation
`repost` and writes the `propagation_edges` rows; this wave does not write that
table. The scoring wave reads cluster ids and versions and leans on the
guarantee that a score attached to a cluster stays attached to that cluster.

**Section 7, the file map.** A table of the files and their line counts:

```text
   176  src/product_hunter/clustering/__init__.py
   108  src/product_hunter/clustering/problems.py
   156  src/product_hunter/clustering/similarity.py
   171  src/product_hunter/clustering/clusters.py
   138  src/product_hunter/clustering/adjudication.py
   601  src/product_hunter/clustering/engine.py
  1317  src/product_hunter/db/clustering.py
    24  config/clustering.yaml
   122  db/migrations/0003_clustering.sql
   354  tests/test_clustering_core.py
   578  tests/test_clustering_engine.py
   967  tests/test_clustering_store.py
```

and the three edited files: `src/product_hunter/config.py` (149 lines added,
the clustering config models and the loader), `tests/test_db_migrations.py` (16
lines, the schema version assertion, because the wave adds a migration) and
`docs/reasonix/ARCHITECTURE.md` (79 lines changed, the table count, the
clustering engine section and the hazards).

**Section 8, the debts.** The three `shortcut:` markers, quoted: the engine is
quadratic in the corpus and the upgrade is a nearest-neighbour index, which is
the Qdrant path; the package ships no command line and no worker handler, and
the caller is the research stage; a merge or a split falls back to the
predecessor's label when the anchor's pain point row is gone, and the upgrade
is to read the pain point from the store once a wave prunes pain points.

**Section 9, the residuals.** The real embedding path never ran, because
`sentence-transformers` is not installed; the adjudicator never ran against a
live model, only against a scripted double; the store has no caller, so no real
run has ever written a cluster; the engine's quadratic cost is taken on trust
from the `shortcut:` comment; the migration number `0003` collides with the
tool-call wave's uncommitted `0003_research_gate.sql`, and one of the two files
has to be renumbered before the branches merge, with the schema version
assertion re-applied after the rename.

**Section 10, what was not checked.** The real embedding path, the live
adjudicator, the store against a real run, the store against a file-backed
database, under concurrency, or against a database that predates the
constraints, and the other waves' code against this store.

**Section 11, the tiers and the nodes.** The run used the free tier. The
nodes, in order: `loop-free-implement-1` for the build and for the three fix
rounds, `loop-free-harden` twice, `loop-free-review-1` three times, and
`loop-free-verify-code-1` and `loop-free-verify-code-2`, both of which failed
with provider errors, so the orchestrator ran the code gate itself and recorded
it. The stage 5 visual gate does not apply: no UI file changed, and the
adapter says `visualRequired: never`.

**The closing block.** End the report with this block, filled in:

```text
GOAL: decide which pieces of evidence describe the same underlying problem, and keep the history of the clusters
TIER: free
RISK: declared=high floor=high (db/migrations/** is an adapter high-risk path)
ADAPTER: Product Hunter [repo]
STAGES: 11/11 complete, skipped: 5b visual, no UI file changed and the adapter says visualRequired: never
GATES: uv run pytest -> 1087 passed, exit 0; clustering package -> 107 passed, exit 0; migrations -> 13 passed, exit 0
CHANGES: 13 new files, 3 edited files, 4712 lines of source and tests
HARDEN: fix first, then ship
REVIEW: fix 1, 2 and 3 first, then Ship, then Verdict: Ship
WAIVERS: none
STOPPED BECAUSE: all pass and review ship
NEXT: ready to commit
```

## Job 2, the second commit

Stage `docs/reasonix/reports/wave-2-clustering.md` on its own and commit it
with the subject line:

```text
docs: add the wave 2 clustering run report
```

Report both commit hashes.

## What to report back

- Both commit hashes.
- The list of paths in each commit, with `git show --stat` output.
- The report file's line count.
- Anything in the facts above that you could not write as given, and why.

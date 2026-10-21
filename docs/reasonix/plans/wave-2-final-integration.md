# Wave 2 final integration — plan

**Spec:** [../specs/wave-2-final-integration.md](../specs/wave-2-final-integration.md)
**Branch of work:** `integration/wave2`, created from `main` at `59e29fa`
**Landing commit on `main`:** `wave2: integrate intelligence pipeline`

A SubQ Snap decision at stage 3 chose the side-branch topology at 0.98
confidence. The work happens on `integration/wave2`, each session branch lands
there with `--no-ff` so every session commit stays individually visible, and
the reconciled tree is then squashed onto `main` as one commit. Nothing is
rewritten and no session commit is lost.

## 1. Merge order

The order is not the conceptual pipeline order. It is chosen to keep the
conflict surface small at each step.

| # | Branch | Why here | Expected conflicts |
|---|---|---|---|
| 1 | `wave2/obsidian-reporting` | `main` is its ancestor, so this is a fast-forward with no conflict at all. It also carries the Wave 1 commits in its history, so it is the one branch that already knows about `db/leases.py` and `research/query_plan.py`. | none |
| 2 | `wave2/evidence-pipeline` | First of the seven that fork from `3d7bc68`. It changes the write semantics of `pain_points` and the migration statement splitter, so it must land before anything else writes against the new schema. | `db/__init__.py`, `db/migrations.py`, `db/store.py`, `tests/test_db_migrations.py`, `ARCHITECTURE.md` |
| 3 | `wave2/collection-engine` | Touches `fetch.py`, which nothing else touches. Lands before the branches that read collection state. | `tests/test_db_migrations.py`, `ARCHITECTURE.md` |
| 4 | `wave2/discovery-engine` | A self-contained package. Lands wherever there is room; here it is between the two schema-heavy branches. | `ARCHITECTURE.md` |
| 5 | `wave2/clustering` | Reads `pain_points`, so it lands after the evidence migration. | `tests/test_db_migrations.py`, `ARCHITECTURE.md` |
| 6 | `wave2/independence` | Alters `authors`, `documents` and `propagation_edges`, and it is the second claimant of `documents.live_status`. It lands after the evidence migration so the column owner is already decided. | `db/__init__.py`, `tests/test_db_migrations.py`, `ARCHITECTURE.md` |
| 7 | `wave2/scoring-validation` | A self-contained package with no migration. | `ARCHITECTURE.md` |
| 8 | `wave2/investigation` | Reads the Wave 1 gate. Lands last so it can be reconciled against the gate that is actually in the tree. | `research/__init__.py` |

## 2. The conflict resolutions

### `docs/reasonix/ARCHITECTURE.md`

Taken from the current integrated text at every merge. Each branch's edit
describes one subsystem in a document that will describe eight, so any of
them is wrong the moment the next branch lands. The branches' own subsystem
descriptions are recovered from the branch diffs and folded into one rewrite
at stage 8, against the code that is actually there.

### `src/product_hunter/db/__init__.py`

Both the evidence branch and the independence branch add public re-exports.
Both sets are kept. The file is a re-export surface, so the only question is
whether two names collide, and they do not.

### `src/product_hunter/db/migrations.py`

The evidence branch replaces the naive semicolon splitter with
`sqlite3.complete_statement`, because its triggers contain semicolons inside a
body and the naive splitter would cut a trigger in half. Wave 1 also touched
this file. The evidence version is kept, and it is required for the triggers
to apply at all.

### `src/product_hunter/db/store.py`

The evidence branch adds the document-version import path and the liveness
reconciliation. Wave 1 added the transactional outbox and the run import. Both
are kept, and the order of the two edits inside the file is preserved.

### `src/product_hunter/research/__init__.py`

Wave 1 added the gate re-exports and the investigation branch added the
investigation re-exports. Both are kept.

### `src/product_hunter/cli.py`

Wave 1 added the `ph db status` table listing and the reporting branch added
`ph project`. Both are kept.

### `tests/test_db_migrations.py`

Four branches each moved a `migrate(conn) == 2` assertion and added a case for
their own migration. The integrated file asserts the final version, 7, and
carries a case per migration, so every migration is covered by a test that
knows its own number.

## 3. The migration reconciliation

Renumbered after the merges, so the renames and the duplicate-column removal
land in one place:

```
0003_research_gate.sql      unchanged
0003_collection.sql         -> 0004_collection.sql
0003_evidence_pipeline.sql  -> 0005_evidence_pipeline.sql
0003_clustering.sql        -> 0006_clustering.sql
0003_independence.sql      -> 0007_independence.sql
```

`documents.live_status` is owned by the evidence migration. The
`ALTER TABLE documents ADD COLUMN live_status` line is removed from the
independence migration, and a comment says which migration owns it and why.

The `shortcut:` marker in `db/migrations.py` about the semicolon splitter is
removed, because the splitter no longer splits on a semicolon.

## 4. Test plan

After each merge: the tests of the branch that just landed, plus the shared
files that merge touched.

```
uv run pytest tests/test_db_migrations.py
uv run pytest tests/test_evidence_pipeline.py
uv run pytest tests/test_collection.py tests/test_collection_security.py
uv run pytest tests/test_discovery_*.py
uv run pytest tests/test_clustering_*.py
uv run pytest tests/test_independence_*.py
uv run pytest tests/test_opportunity_*.py
uv run pytest tests/test_research_investigation_*.py
uv run pytest tests/test_projection_*.py
```

After the last merge and the renumbering: the whole suite, then the whole
suite again from the committed state.

## 5. What is deliberately not done

- No Phase 7 integration. No SubQ Snap, no Apify, no Crawl4AI, no large-scale
  Playwright, no Langfuse, no Redis or Temporal, no Obsidian two-way sync.
- No model is hard-coded and no provider secret is added.
- No test is changed to make a merge pass. A failing assertion is the signal.
- No branch is deleted until its commits are confirmed reachable from the
  integrated tree.

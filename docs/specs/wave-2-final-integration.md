# Wave 2 final integration — spec

**Run ID:** wave-2-final-integration-2026-10-22
**Base:** `main` at `59e29fa` (holds the Wave 1 integration commit `984c278`)
**Fork point of the eight session branches:** `3d7bc68`
**Risk:** declared `high`, floor `medium`. The adapter lists `db/migrations/**`,
`config/sources.yaml`, `src/product_hunter/safety.py` and
`src/product_hunter/fetch.py` as high-risk paths, and this run touches the
first and the last. Stages 2, 6 and 7 are therefore not skippable.
**Specialization:** backend. No markup, style or component file is in the
declared radius, so the visual gate is not required.

## 1. The goal

Eight Wave 2 sessions ran in isolation, each on its own branch and worktree.
They produced eight disjoint Python packages and four SQLite migrations that
collide on the number `0003`. This run reconciles them into one tree on `main`,
with one migration sequence, one authoritative implementation per
responsibility, a green suite, a security pass, and documentation that matches
the code.

## 2. What the repository actually held before this run

`main` was at `59e29fa` and carried the Wave 1 integration: the research
contract and tool gate, the fenced checkpoint write, the worker-owned query
plan, and the shared clock and rollback helpers. The suite was green at 1085
tests.

Seven of the eight Wave 2 branches fork from `3d7bc68`, the commit before the
Wave 1 integration. The eighth, `wave2/obsidian-reporting`, was already merged
against `main` and carries the Wave 1 commits in its history.

That is the central fact of the integration. The seven branches were written
against a tree that had no `db/leases.py`, no `db/research_gate.py`, no
`research/query_plan.py`, and an unfenced checkpoint write. Merging them is
therefore not a matter of adding packages to a tree: it is a matter of
re-basing eight bodies of work onto a tree that had already moved.

## 3. The eight branches

| Branch | Head | Delivers | Migration claimed |
|---|---|---|---|
| `wave2/evidence-pipeline` | `2c9eb8f` | Evidence chain: document versions, span observations, lineage, derived strength and reliability, and three triggers that make a pain point require a verified evidence span | `0003_evidence_pipeline.sql` |
| `wave2/discovery-engine` | `cc4cf7e` | Discovery engine and query frontier: eight query families, controlled variants, novelty and saturation, discovery budgets | none |
| `wave2/collection-engine` | `11724d7` | Collection layer: the collector ladder, per-source run state, attempt ledger, and a `RequestBudget` inside `fetch.py` | `0003_collection.sql` |
| `wave2/clustering` | `e425e72` | Problem clustering: normalisation, similarity, candidate generation, assignment, merge and split, cluster lineage and versions | `0003_clustering.sql` |
| `wave2/independence` | `51e7dec` | Independence graph: author and community identity resolution, propagation detection, deterministic analytics, concentration checks | `0003_independence.sql` |
| `wave2/scoring-validation` | `2e47038` | Opportunity scoring: Potential and Confidence on separate scales, WTP tiers, market ranges, lifecycle transitions, content-addressed snapshots | none |
| `wave2/investigation` | `da5f201` | Investigation engine: competitor search, counter-evidence, coverage, budgets, checkpoints, the startup-killer pass, and a bridge into the Wave 1 gate | none |
| `wave2/obsidian-reporting` | `b40144a` | Reporting and the Obsidian projection: sanitising, deterministic rendering, the vault layout, an outbox handler, and the `ph project` command | none |

The eight packages are disjoint. There is no package implemented twice, and no
module name collides.

## 4. Conflicts, by category

### 4.1 The migration number

Five files claim `0003`: the Wave 1 `0003_research_gate.sql` on `main`, and the
four above. The loader refuses a duplicate number and refuses a gap, so an
unreconciled merge fails loudly at `db init`. This is a known break, recorded
in the Wave 1 report, and this run closes it.

Resolution, confirmed by a SubQ Snap decision at 0.91 confidence: the
migrations take the spec section 10 pipeline order.

```
0001 initial
0002 model_routing
0003 research_gate          (Wave 1, unchanged)
0004 collection             (renamed from 0003_collection.sql)
0005 evidence_pipeline      (renamed from 0003_evidence_pipeline.sql)
0006 clustering             (renamed from 0003_clustering.sql)
0007 independence           (renamed from 0003_independence.sql)
```

No foreign key between the four points at a table another of the four creates.
Every reference resolves into `0001`, so the order is a readability choice and
not a constraint. It is still the order the pipeline runs in.

### 4.2 The duplicated column

The evidence migration and the independence migration both add
`documents.live_status TEXT NOT NULL DEFAULT 'live'`. The definitions are
identical, so one of them owns it and the other stops adding it. The evidence
migration owns it, because it is the migration that models the document
lifecycle, and the independence migration keeps its own `documents.deleted_at`.

### 4.3 The shared files

| File | Touched by | Nature of the conflict |
|---|---|---|
| `src/product_hunter/db/__init__.py` | evidence, independence | Both add public re-exports. Textual, resolvable by keeping both sets. |
| `tests/test_db_migrations.py` | evidence, collection, clustering, independence | Each branch moved a `migrate(conn) == 2` assertion and added a case for its own migration. The integrated file asserts the final count and carries a case per migration. |
| `docs/reasonix/ARCHITECTURE.md` | seven branches | Each branch described its own subsystem in the same document. Every one of those descriptions is partial once the next branch lands. Resolved by taking the current integrated text during each merge and rewriting the document once, at the end, against the code. |
| `src/product_hunter/db/migrations.py` | evidence, and Wave 1 | The evidence branch replaced the naive `;` splitter with `sqlite3.complete_statement`, because its triggers contain semicolons inside a body. Wave 1 had also touched this file. The splitter change is required and is kept. |
| `src/product_hunter/db/store.py` | evidence, and Wave 1 | The evidence branch added the document version import path and the liveness reconciliation. Wave 1 had added the outbox and the run import. Both are kept. |
| `src/product_hunter/research/__init__.py` | investigation, and Wave 1 | Wave 1 added the gate re-exports; the investigation branch added the investigation re-exports. Both are kept. |
| `src/product_hunter/cli.py` | obsidian, and Wave 1 | Wave 1 added the `ph db status` tables listing; obsidian added `ph project`. Both are kept. |

### 4.4 The files only one branch touched, which still matter

`src/product_hunter/fetch.py` is touched by the collection branch alone, and it
is an adapter high-risk path. The branch added a request budget and three
labelled `shortcut:` markers. The markers are reviewed in the security pass and
are kept, with the reasons they state.

`src/product_hunter/config.py` is touched by the clustering branch alone, which
added the clustering configuration loader. `src/product_hunter/documents.py` and
`src/product_hunter/extraction.py` are touched by the evidence branch alone.
`src/product_hunter/research/evidence.py` is touched by the investigation
branch alone, which added twelve `ClaimKind` members.

## 5. Rules for resolving a duplicate implementation

Where two branches solve the same problem, the choice is made in this order:

1. `Product-Hunter-Full-Spec.md`
2. the architecture that already exists
3. correctness
4. security
5. deterministic behaviour
6. testability
7. maintainability

The order of the first branch to be merged is not a criterion.

The one duplication found inside a branch is five copies of a `_hash_id`
helper in the independence package. That is intra-branch and is a lean finding
for the review pass, not a merge decision.

## 6. What this run must not do

- No Phase 7 integration is pulled in: no SubQ Snap, no Apify, no Crawl4AI, no
  large-scale Playwright, no Langfuse, no Redis or Temporal, no Obsidian
  two-way sync.
- No runtime model is hard-coded. The model catalogue and the role chains in
  `config/model_routing.yaml` stay the single place a model is chosen.
- No provider secret is added. `DEEPSEEK_API_KEY` is forbidden by the adapter
  and stays absent.
- No test is weakened, skipped or deleted to make a merge pass.
- No branch is deleted before its commits are confirmed integrated.

## 7. Acceptance criteria

1. `main` holds the integrated tree in one commit titled
   `wave2: integrate intelligence pipeline`, on top of `59e29fa`.
2. `uv run pytest` passes from the committed state.
3. `db/migrations/` holds a contiguous `0001` to `0007` with no duplicate and
   no gap, and `db init` applies all seven.
4. Every Wave 2 branch commit is reachable, and every Wave 2 package is
   importable and exercised by its own tests.
5. The security pass records the findings it fixed and the ones it left.
6. `docs/reasonix/ARCHITECTURE.md` and
   `docs/reasonix/reports/wave-2-final-integration.md` describe the integrated
   tree, and the spec audit says honestly what is done, partial, deferred and
   not built.

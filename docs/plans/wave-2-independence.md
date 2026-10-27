# Wave 2 Session 5: implementation plan

**Spec:** [`../specs/wave-2-independence.md`](../specs/wave-2-independence.md)
**Branch:** `wave2/independence`
**Worktree:** `C:\Users\anubh\.local\share\opencode\worktree\44767c\wave2-independence`

## 1. The shape

Eight new files, one migration, one config, four test files.

```
src/product_hunter/independence/
  __init__.py        public exports
  policy.py          IndependencePolicy + loader + validation
  records.py         every pydantic record
  identity.py        author resolution, community resolution, author clusters
  propagation.py     repost / crosspost / quote / syndication detection
  graph.py           the independence graph
  analytics.py       metrics + concentration check
src/product_hunter/db/
  independence.py    IndependenceStore: read the state, write the relationships
db/migrations/
  0003_independence.sql
config/
  independence.yaml
tests/
  test_independence_identity.py
  test_independence_propagation.py
  test_independence_analytics.py
  test_independence_store.py
```

The dependency order is one way: `policy` and `records` have no internal
dependency; `identity` and `propagation` depend on those two; `graph` depends
on `identity` and `propagation`; `analytics` depends on `graph`; the store
depends on `records` and the database.

## 2. What already exists and gets reused

- `product_hunter.db.connection.connect` for the pragmas and the row factory.
- `product_hunter.db.migrations.migrate` for the migration runner. The new
  migration is `0003`, the next free contiguous number.
- `product_hunter.config.repo_root` for the config path and the text files.
- `pydantic` for every record, `yaml` for the policy file, `hashlib` for the
  deterministic ids, `difflib` for the verbatim block, `sqlite3` through the
  existing connection helper.
- Nothing new is added to `pyproject.toml`. The standard library covers
  similarity, hashing, and the graph walk.

## 3. Build order

1. `policy.py` and `records.py`, with the policy validator.
2. `identity.py`: normalisation, the signal set, the score, the caps, the
   edges, the author confidence, the clusters, the community links.
3. `propagation.py`: the token set, the longest verbatim block, the four
   relations, the direction.
4. `graph.py`: the canonical content groups, the chains, the graph object.
5. `analytics.py`: the metrics, the concentration check.
6. `db/independence.py` and the migration.
7. The tests.

## 4. Decisions taken at ARCHITECT

- **One mention is one document.** Recorded in the spec §3.1 and confirmed by
  the SubQ snap call (document 0.65 against span 0.35, confidence 0.31). The
  low confidence is recorded in the run report as a judgement call, not a
  settled fact.
- **The graph is data.** No clock, no file, no query inside the analysis
  package. Every timestamp is injected by the caller. That is what makes the
  numbers reproducible.
- **Merge requires a strong signal.** The code caps the score of a pair with
  no strong signal at `suspect_threshold`, so a merge is unreachable from
  weak signals whatever the configuration says. The policy validator refuses a
  configuration that would let it happen.
- **A suspect group is one uncertain person, not two.** The groups are
  connected components over merge and suspect edges together. A suspect pair
  is counted once, in the uncertain column.
- **The concentration base is the raw mention count**, for all four shares, so
  the numbers are comparable and a report can put them in one table.
- **The propagation similarity is text-only.** No Qdrant, so a paraphrase reads
  as a separate canonical content. The error is under-counting propagation,
  not over-counting independence.

## 5. The migration

`0003_independence.sql` extends `authors`, `documents`, and
`propagation_edges` with `ALTER TABLE`, and adds five tables.

`ALTER TABLE ADD COLUMN` has no `IF NOT EXISTS` in SQLite. The migration
runner applies each file once, keyed on `PRAGMA user_version`, so the columns
are added once. The migration test applies the migration to a fresh database
and to a version-2 database, and asserts the version lands on 3.

`research/persistence.py` names a planned `0003_research_gate.sql`. This wave
takes `0003` because the runner demands contiguous numbers and `0003` is the
next free one. The wave that persists tool calls must take `0004` or later.
This is recorded in the run report and in `ARCHITECTURE.md`.

## 6. What the tests prove

The matrix is the spec §12 table. The tests build small in-memory graphs
directly, without a database, except for `test_independence_store.py`, which
uses a temporary file and the real migration.

The test for the model boundary is the important one: a caller-supplied
`model_signal` between two authors, and the assertion that the pair never
reaches `same-person-merged` and never enters the independent-author count.

## 7. Risks

| Risk | Handling |
|---|---|
| A free model writes code that does not compile | the code gate is the arbiter; the implementer may not change a test to pass |
| The policy drifts from the research contract | a test reads `config/research_contract.yaml` and asserts the two minima agree |
| The identity key leaks raw | the store hashes it; a test asserts the stored value is not the raw key |
| The migration collides with another wave | `0003` is taken, and the run report says so |

## 8. Out of scope for this wave

Scoring, market sizing, competitor analysis, Obsidian, Qdrant, DuckDB
materialisation, and the CLI. The package is a library, like the rest of
`src/product_hunter/research/`.

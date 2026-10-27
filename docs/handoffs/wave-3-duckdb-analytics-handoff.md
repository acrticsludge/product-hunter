# Handoff: wave 3 session 2, the DuckDB analytical layer

**Run ID:** `wave3-duckdb-analytics-2026-10-22`
**Branch:** `wave3/duckdb-analytics`
**Worktree:** `C:\Anubhav\Web Dev Projects\product-hunter-wt\wave3-analytics`
**Base commit:** `00a6ca3` (main, the Wave 2 integration)

## What this session builds

The analytical/query layer over the Product Hunter database. DuckDB holds a
derived copy of the SQLite state and the SQL views that answer the
analytical questions. SQLite stays the authoritative transactional store.

**The rule that shapes everything: SQLite wins.** The analytics layer opens
SQLite read-only, so it has no write path to the authoritative store at all.
DuckDB is derived state: it can be thrown away and rebuilt, and the rebuild
is deterministic.

## Where the code is

| Area | Path |
|---|---|
| Package | `src/product_hunter/analytics/` |
| Tests | `tests/test_analytics_ingest.py`, `tests/test_analytics_views.py` |
| Spec | `docs/reasonix/specs/wave-3-duckdb-analytics.md` |
| Plan | `docs/reasonix/plans/wave-3-duckdb-analytics.md` |
| Report | `docs/reasonix/reports/wave-3-duckdb.md` |

## The repository map a later session needs

### The pieces this layer reuses

- `src/product_hunter/db/connection.py`: `connect(db_path, *, read_only=False)`.
  The pragmas, the row factory, and the read-only mode. `read_only=True`
  opens an existing file with a `file:...?mode=ro` URI and raises
  `FileNotFoundError` when the file is not there. This is the read path.
- `src/product_hunter/db/migrations.py`: `migrate(conn)`. Numbered files in
  `db/migrations/`, one transaction each, `PRAGMA user_version` as the only
  version record. The test fixture migrates a temporary file.
- `src/product_hunter/config.py`: `database_path(root=None)`. The SQLite path,
  `PH_DB_PATH` when set, otherwise `db/product_hunter.db`.
- `src/product_hunter/projection/contract.py`: `CANONICAL_DDL`. The DDL of
  the canonical opportunity, score, claim, evidence, experiment and request
  tables. **No migration creates those tables yet.** The projection's reader
  degrades when a table is absent, and this layer does the same: the score
  history view reads `opportunity_scores` and is skipped while the table is
  absent. The test fixture applies `CANONICAL_DDL` to its temporary database
  so the score history has a table to read.
- `src/product_hunter/projection/source.py`: `select_columns(conn, table)`
  reads a table through the columns it actually has, and returns nothing for
  a table that is not there. The same tolerance is wanted here.
- `src/product_hunter/discovery/store.py`: the discovery records live in
  `research_events.payload_json` with a constant event type. Read the
  constant from the module rather than repeating the literal.
- `src/product_hunter/discovery/frontier.py`: `SaturationPolicy`, with
  `min_novelty=0.1` and `max_duplicate_rate=0.9`. The query-family view uses
  the same two bands.
- `src/product_hunter/independence/analytics.py`: the shape of the
  independence counters: raw mentions, canonical content count, independent
  author count, uncertain author count, independent community count, the
  propagation-adjusted count and the four concentrations. The
  `v_independence_summary` view produces the same counters over the mirrored
  tables.
- `tests/conftest.py`: `NOW = "2026-10-21T00:00:00+00:00"` and the fixture
  helpers. The analytics tests build their own fixture, because they need a
  known shape rather than a parsed document.

### The tables this layer mirrors

Every user table in SQLite. The list is discovered at runtime, not
hand-written, so a table a later wave adds is mirrored without an edit here.
The schema is in `db/migrations/0001_initial.sql` through
`db/migrations/0007_independence.sql`:

```text
0001 initial        sources, fetches, communities, documents, authors,
                    author_links, evidence, pain_points, evidence_pain_links,
                    propagation_edges, research_runs, research_artifacts,
                    research_events, research_checkpoints, jobs,
                    stage_attempts, projection_tasks, spend_ledger,
                    human_decisions
0002 model routing  model_catalogs, model_inventory, model_role_bindings,
                    model_executions
0003 research gate  tool_calls, research_stage_executions,
                    research_stage_artifacts, research_claims,
                    stage_gate_results
0004 collection     collection_source_state, collection_run_state,
                    collection_items
0005 evidence       document_versions, evidence_observations,
                    evidence_lineage (plus columns on documents, evidence,
                    evidence_pain_links)
0006 clustering     clustering_runs, clusters, cluster_versions,
                    cluster_members, cluster_lineage, cluster_decisions
0007 independence   author_identity_links, community_links, author_clusters,
                    author_cluster_members, independence_reports (plus columns
                    on authors and propagation_edges)
```

The tables that do **not** exist yet, and that the views read by name:
`opportunities`, `opportunity_scores`, `opportunity_claims`,
`opportunity_evidence`, `experiments`, `research_requests`. They arrive with
a later wave. The views over them are skipped until they are there.

### The DuckDB facts that were measured, not remembered

These were checked against `duckdb==2.0.0` in this repository's virtual
environment. Do not re-derive them, and do not assume the opposite.

| Fact | Why it matters |
|---|---|
| `INSERT OR REPLACE INTO t VALUES (...)` works | The upsert of a mirrored row. |
| `ON CONFLICT (pk) DO UPDATE SET` works | The alternative upsert. |
| `DELETE FROM t WHERE NOT EXISTS (SELECT 1 FROM src s WHERE s.pk = t.pk)` works | The delete-missing pass. |
| A **view over a missing table fails at creation** with `CatalogException` | The views must be created only when their source tables exist. |
| Named parameters are `$name`. `:name` is a **syntax error** | The query parameters are bound, never interpolated. |
| `json_extract_string(payload, '$.field')` reads a JSON string out of a VARCHAR column | The discovery records live in `research_events.payload_json`. |
| `date_trunc('month', ts)` works on a `TIMESTAMP` column | The month buckets of the trend views. |
| `SET TimeZone='UTC'` on the connection | A naive `TIMESTAMP` holds the UTC wall clock the SQLite string carried. The cast of `'2026-10-22T12:00:00.000Z'` to `TIMESTAMP` is the same under any session zone, but the setting makes it explicit. |
| `row_number() OVER (PARTITION BY ... ORDER BY ...)` works | The best-author-link pick, which stops a document fanning out into several authors. |
| A DuckDB transaction rolls back a `CREATE TABLE` | The per-table transaction that keeps the derived state half-free. |
| `object` is a reserved word in DuckDB | Quote it as `"object"`. |

## The decisions already made

Do not re-open them.

1. **The ingestion path reads through the stdlib `sqlite3` module**, not
   through DuckDB's SQLite extension. A SubQ Snap decision at stage 3 chose
   this at 0.98 confidence. The reason: the extension may need a network
   install at runtime, and it would give DuckDB a write path to the
   authoritative store. The invariant is structural, not a flag.
2. **The schema is discovered at runtime.** A hand-written column list
   drifts when a later wave adds a table.
3. **One side computes the row hash.** DuckDB returns a `TIMESTAMP` as a
   `datetime` and SQLite returns the same column as a string, so a hash
   computed on each side would not agree. The hash is computed on the SQLite
   side and stored in the mirrored row as `_ph_hash`.
4. **There is no migration.** The derived state needs no SQLite table, and
   `db/migrations/**` is an adapter high-risk path this run does not need.
5. **There is no CLI command.** The mission asks for a library and a report.
   `src/product_hunter/cli.py` is 68 KB and every other wave may be editing
   it.

## What the implementer must not do

- Do not modify any file outside the declared radius.
- Do not touch `db/migrations/`, `src/product_hunter/cli.py`,
  `src/product_hunter/config.py`, or any existing module outside
  `src/product_hunter/analytics/`.
- Do not change a test to make it pass. Weakening, skipping or deleting an
  assertion is a hard stop.
- Do not add a dependency other than `duckdb`, which is already added.
- Do not add a `DEEPSEEK_API_KEY` or any provider secret.
- Do not fetch a URL that is not declared in `config/sources.yaml`.
- Do not commit. The orchestrator commits.

## The gate

```text
uv run pytest
```

The baseline is `2138 passed` on `00a6ca3`. The whole suite must pass, not
only the two new files.

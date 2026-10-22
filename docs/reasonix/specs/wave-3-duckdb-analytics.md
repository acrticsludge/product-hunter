# Spec: the DuckDB analytical layer (Wave 3, Session 2)

**Run ID:** `wave3-duckdb-analytics-2026-10-22`
**Branch:** `wave3/duckdb-analytics`
**Base:** `00a6ca3` (main, the Wave 2 integration)
**Risk:** declared `medium`, floor `medium` (adapter `floorRisk: medium`).
**Adapter:** Product Hunter, 8 forbidden rules in force.

## 1. What this session builds

An analytical/query layer over the Product Hunter database. DuckDB holds a
derived copy of the SQLite state and the SQL views that answer the analytical
questions. SQLite stays the authoritative transactional store; DuckDB never
becomes a second source of truth.

Nothing else changes. The scoring logic, the discovery engine, the scraping
ladder and the Obsidian projection are not touched.

## 2. The rule that shapes everything

> SQLite wins.

The consequence is not a convention, it is a structure:

- The analytics layer opens SQLite **read-only**, through the existing
  `product_hunter.db.connection.connect(path, read_only=True)`. It has no
  write path to SQLite at all, so it cannot make SQLite wrong.
- DuckDB is written **only** by the ingestion, and every row it holds carries
  the hash of the SQLite row it came from. A DuckDB row that cannot be traced
  to a SQLite row is drift.
- The derived state can be thrown away and rebuilt. `rebuild()` does that from
  the authoritative source, and the rebuild is deterministic: the same SQLite
  state produces the same DuckDB state, row for row.

The DuckDB file is derived state. It is not committed (`.gitignore` already
excludes `*.duckdb`), and it is not read by any existing write path.

## 3. Where the data comes from

The mirrored tables are discovered from the SQLite schema itself rather than
hand-listed, so a table that a later wave adds is mirrored without an edit
here, and a table that a later wave changes shape does not break this layer.

| What | Rule |
|---|---|
| Which tables | Every user table in `sqlite_master`. `sqlite_%` names are skipped. |
| Which columns | `PRAGMA table_info`. The declared type maps to a DuckDB type: `INTEGER` to `BIGINT`, `REAL` to `DOUBLE`, `TEXT` to `VARCHAR`, `BLOB` to `BLOB`, anything else to `VARCHAR`. |
| Which columns are timestamps | The name ends in `_at`, or is `valid_from` or `valid_to`. Those become DuckDB `TIMESTAMP`. |
| Which column is the key | The `PRAGMA table_info` primary key, in key order. A table with no declared key uses `rowid`. |
| Which column is the watermark | The first present of `updated_at`, `last_updated`, `last_seen_at`, `created_at`, `recorded_at`, `observed_at`, `occurred_at`, `detected_at`, `checked_at`, `attempted_at`, `started_at`, `last_request_at`, `last_fetched_at`, `valid_from`, `published_at`, `fetched_at`, `selected_at`, `discovered_at`, `completed_at`, `deleted_at`, `superseded_at`. A table with none is re-read in full. |

A table that is missing from SQLite is skipped, not an error. That is what
makes `opportunities`, `opportunity_scores`, `opportunity_claims`,
`opportunity_evidence`, `experiments` and `research_requests` work today
(the projection's read contract names them, but no migration creates them yet)
and keeps working when a later wave creates them.

### 3.1 The row hash

Every mirrored DuckDB table carries one extra column, `_ph_hash`. It holds
the SHA-256 of the canonical JSON of the SQLite row's values. `null` is
`null`, bytes are hex, and the key order is the column order, so the hash is
stable across runs and across processes.

The hash is what makes the refresh incremental and the reconciliation exact:

- A refresh writes a row only when its hash changed or the key is new.
- A reconciliation compares the SQLite hash with the stored `_ph_hash` and
  names every added, removed and changed key.

The hash is computed on the SQLite side only. DuckDB returns a `TIMESTAMP`
column as a `datetime` and SQLite returns the same column as a string, so a
hash computed on each side would not agree. One side computes it; the other
side stores it.

## 4. The operations

| Operation | What it does | Cost |
|---|---|---|
| `snapshot()` | Reads every table in full and writes the derived state. | O(n) read, O(n) write. |
| `refresh()` | Reads the rows newer than the stored watermark, writes the rows whose hash changed, and removes the rows SQLite no longer has. | O(changed) write, O(n) key read. |
| `rebuild()` | Drops the derived state and runs `snapshot()` again. | O(n) read, O(n) write. |
| `reconcile()` | Compares SQLite and DuckDB row by row and reports the drift: added keys, removed keys, changed keys, per table. | O(n) read, no write. |

`refresh()` is incremental in the write path, and it is honest about the read:
the key set of every table is read, because a row deleted in SQLite cannot be
noticed by a filter on a timestamp. A table with no watermark column is
re-read in full, so "incremental where practical" is literal.

`reconcile()` is the exact check. It is the operation that makes "SQLite wins"
enforceable rather than merely intended: the drift it names is repaired by
`rebuild()`, and the repair is deterministic.

### 4.1 Failure and recovery

- Each table is ingested inside a DuckDB transaction. A failure part-way
  through a refresh leaves the mirrored table and its stored state as they
  were, so the derived state is never half a table.
- A refresh that fails leaves the previous derived state readable. The next
  refresh or a rebuild converges it.
- A missing SQLite file is refused with a clear error before anything is
  written.
- A DuckDB file that cannot be opened is refused with the underlying error.
  The derived state is never silently replaced by an empty one.

## 5. The analytical views

The views are plain SQL over the mirrored tables. They are created by the
ingestion, dropped and recreated by a rebuild, and are the only thing a
caller needs to know to ask a question.

| View | Question it answers |
|---|---|
| `v_evidence_mentions` | One row per evidence span, with its document, source, community, author and pain point. The base of the evidence analytics. |
| `v_pain_point_evidence` | Per pain point: mentions, canonical documents, distinct authors, distinct communities, distinct sources, the independent counts, the source concentration. |
| `v_pain_point_trend` | Per pain point per month: mentions, distinct authors, and the change against the previous month. Which pain points are growing. |
| `v_source_yield` | Per source: fetches, documents, evidence, accepted pain points, the useful-evidence yield, the distinct authors and communities, the failure rate. Which sources have the highest useful-evidence yield. |
| `v_community_evidence` | Per community: mentions, canonical documents, distinct authors, independent authors, uncertain authors, the source spread. Which communities produce the most independent evidence. |
| `v_independence_summary` | Per pain point: raw mentions, canonical observations, independent authors, uncertain authors, independent communities, the propagation-adjusted count, the source, community and author-cluster concentration. The counters the spec's independence section names. |
| `v_source_concentration` | Per pain point: the number of sources, the largest single-source share, the top-three share, the Herfindahl index, and whether the evidence is concentrated. Where is evidence overly concentrated. |
| `v_opportunity_evidence_trend` | Per opportunity, per month: the evidence behind it and the change against the previous month. Which opportunities are gaining or losing evidence. |
| `v_score_history` | Per opportunity: the potential and the confidence of every score, with the change against the previous score. The score history. |
| `v_run_analytics` | Per research run: the status, the wall time, the tool calls, the distinct tools, the failures, the gate verdicts. |
| `v_run_budget` | Per research run: the units, the cost, the share of the total, and the split by tool, provider and model. Which research runs consume the most budget. |
| `v_collection_health` | Per source: the attempts, the outcomes by reason code, the success rate, the latency, the bytes, the not-modified count. |
| `v_discovery_yield` | Per discovery record: the family, the novelty, the duplicate rate, the useful count, the cost and the useful-per-dollar yield. |
| `v_query_family_performance` | Per query family: the searches, the results, the useful results, the mean novelty, the mean duplicate rate and the saturation verdict. Which query families are saturated. |
| `v_temporal_persistence` | Per pain point: the first sighting, the last sighting, the span in days, the active months, the months since the last sighting and the persistence class. |

### 5.1 The scopes

Two scopes carry the analysis:

- **The pain point** is the group of pain points that share a
  `normalized_key`. It is the scope the independence and the concentration
  views use, because it is the unit the evidence counts belong to.
- **The opportunity** is the cluster. The `clusters` table and its
  `cluster_members` exist today; the canonical `opportunities` table does not.
  When a later wave creates it, the score history reads `opportunity_scores`
  and the opportunity views read `opportunities`; until then they read the
  cluster. Both are read by name, so a table that arrives is picked up
  without an edit here.

The score history is read from `opportunity_scores`, the table the
projection's read contract already names. It is empty until that table
exists, and an empty history is reported as no rows, not as a score of zero.

## 6. The named queries

`queries.py` holds one function per question the mission names. Each one
reads a view and returns a list of dictionaries, ordered so the answer is
stable.

| Function | Question |
|---|---|
| `growing_pain_points` | Which pain points are growing? |
| `independent_evidence_communities` | Which communities produce the most independent evidence? |
| `source_useful_yield` | Which sources have the highest useful-evidence yield? |
| `saturated_query_families` | Which query families are saturated? |
| `opportunity_movement` | Which opportunities are gaining or losing evidence? |
| `concentration_hotspots` | Where is evidence overly concentrated? |
| `run_budget_consumers` | Which research runs consume the most budget? |

## 7. The public surface

```python
from product_hunter.analytics import Analytics, analytics_path

store = Analytics.open(analytics_path(), database_path())
store.snapshot()          # build the derived state
store.refresh()           # bring it up to date
rows = store.query("growing_pain_points", months=3)
```

`Analytics` is a context manager. `Analytics.open()` takes the DuckDB path
and the SQLite path, and it opens SQLite read-only.

The default DuckDB path is `db/product_hunter.duckdb`, overridable with
`PH_ANALYTICS_PATH`. The SQLite path is the existing `database_path()`.

## 8. What is not built

| Not built | Why |
|---|---|
| A migration | The derived state needs no SQLite table. The adapter lists `db/migrations/**` as a high-risk path and this run does not need to touch it. |
| A CLI command | The mission asks for a library and a report, not a command. `ph` is 68 KB and every other wave may be editing it. |
| The `opportunities` table | It is listed as a blocker for the next wave, not for this one. The views read it when it exists. |
| A writer for `propagation_edges` | The wave 2 report lists it as an open gap. The views read the table; they do not invent the rows. |
| Scoring, discovery, scraping, Obsidian | The mission says so. |

## 9. The tests

| Test | What it holds |
|---|---|
| Snapshot creation | The derived state holds every table, with the right row count and the right hash. |
| Incremental refresh | A new row lands; an unchanged row is not rewritten. |
| Rebuild | The derived state after a rebuild equals the state after a snapshot. |
| Duplicate ingestion | The same row ingested twice leaves one row. |
| Changed source | A changed row is re-read and re-written; the hash moves with it. |
| Deleted source | A row deleted in SQLite is gone from the derived state. |
| Score history | The score rows become a history with the change against the previous score. |
| Time-based queries | A growing pain point is found by the month buckets, and a stale one is not. |
| Concentration queries | The concentration view names the source that holds the evidence. |
| Failure and recovery | A failed refresh leaves the previous state readable, and a rebuild converges it. |
| Reconciliation | A hand-made drift is named by `reconcile()` and repaired by `rebuild()`. |

## 10. The risks

1. **The watermark may not cover every mutation.** A writer that changes a
   row without moving its `updated_at` is not seen by the incremental read.
   `reconcile()` catches it; `refresh()` alone does not. The tables whose
   writers bump a watermark are listed in the report.
2. **The read is O(n) even when the write is incremental.** Removing a
   deleted row needs the key set. The report says so rather than claiming a
   cost the code does not have.
3. **The Python round trip is the ingestion cost.** DuckDB can attach a
   SQLite file directly, which is faster, but that would give DuckDB a write
   path to the authoritative store. The stdlib read path is the deliberate
   trade.
4. **A mirrored table is a copy of a table whose shape may change.** A column
   added in a later migration is picked up on the next snapshot, and the
   rebuild recreates the DuckDB table to match.
5. **The views read tables that do not exist yet.** `opportunity_scores` and
   the opportunity tables are read by name and are absent today. DuckDB binds
   a view when it is created, so a view over a table that is not there cannot
   be created. It is skipped, the skip is reported, and a query that needs
   the view says which table is missing. It never returns an empty list to
   hide a missing table and never invents a row.

## 11. A defect this run found, and the radius it widened

The implementer reported that the new tests could not be made to pass
together with `tests/test_db_checkpoints.py`, and that the cause was a
wall-clock budget in that file rather than anything in the new code.

The cause is confirmed. `tests/test_db_checkpoints.py` takes its clock from
a module-level `NOW = utcnow()`, which is captured when the module is
imported, that is during collection. The `_leased_job` fixture claims the
lease with that timestamp and the default `lease_seconds=60`, so the lease
expires sixty seconds after collection. The fence in
`product_hunter.db.leases.assert_live_lease` compares the stored expiry
against the store's own wall clock and there is no way to pass another one,
which is deliberate: a stale worker must not be able to keep its lease alive
by naming an instant.

So the success-path checkpoint tests hold a lease that expires sixty seconds
after the suite starts collecting. On the base commit the suite reached that
file with roughly forty-five seconds to spare. The analytics tests add about
thirty seconds in front of it, because `test_analytics_*` sorts before
`test_db_checkpoints.py`. The margin drops to roughly twenty seconds on this
machine, and the implementer measured the suite failing outright when the
new tests took sixty-eight seconds.

Trimming the new tests until they fit inside another file's sixty-second
window would hide the defect and leave it to the next wave. The defect is in
the shared fixture, and the fix is one line: the default lease of the
success-path fixture has to outlive a test run.

The radius therefore widens by one file, `tests/test_db_checkpoints.py`, and
by nothing else. No assertion changes. The two cases that test an expired
lease pass `lease_seconds` explicitly and are not touched by the default.
The fix is the removal of a wall-clock dependency from a test fixture, not a
loosening of a test.

The blast radius declared at stage 0 widens to:

```text
pyproject.toml
uv.lock
src/product_hunter/analytics/__init__.py
src/product_hunter/analytics/tables.py
src/product_hunter/analytics/ingest.py
src/product_hunter/analytics/views.py
src/product_hunter/analytics/queries.py
tests/test_analytics_ingest.py
tests/test_analytics_views.py
tests/test_db_checkpoints.py
docs/reasonix/specs/wave-3-duckdb-analytics.md
docs/reasonix/plans/wave-3-duckdb-analytics.md
docs/reasonix/prompts/wave-3-duckdb-analytics-implement.md
docs/reasonix/handoffs/wave-3-duckdb-analytics-handoff.md
docs/reasonix/reports/wave-3-duckdb.md
docs/reasonix/ARCHITECTURE.md
```


# Wave 3: DuckDB analytical layer, run report

**RUN_ID:** wave3-duckdb-analytics-2026-10-22
**Node:** REPORT (stage 10)
**Date:** 2026-10-22
**Branch:** `wave3/duckdb-analytics`, base `00a6ca3` (main, the Wave 2 integration)
**Commit:** `92281a21e40908ec39868a229025eb8b3b3015e3`, "wave3: add the DuckDB analytical layer"
**Risk:** declared `medium`, floor `medium` (the adapter's floorRisk is medium; the run adds a runtime dependency and a new package, but changes no schema, no auth path, no secret path, no payment path and no existing write path)

```text
GOAL: add a DuckDB analytical layer over the Product Hunter SQLite database
TIER: free (models used per node in order)
  stage 0-3   orchestrator: longcat-2.5-preview-free
  stage 4     implement: longcat-2.5-preview-free, then step-5-preview-free
  stage 5     verify-code: longcat-2.5-preview-free
  stage 6     harden: step-5-preview-free, twice
  stage 7     review: step-5-preview-free, twice
  stage 8-10  orchestrator: longcat-2.5-preview-free, report node: longcat-2.5-preview-free
RISK: declared=medium floor=medium (runtime dependency and new package; no schema, auth, secret, payment or write path changed)
ADAPTER: Product Hunter [repo] 8 forbidden rules in force
STAGES: 11/11 complete, skipped: 5b visual, no UI file changed and the adapter sets visualRequired to never
GATES: uv run pytest -> 2170 passed in 72.99s, exit 0
CHANGES: 15 files changed, 3857 insertions, 1 deletion
HARDEN: first pass 6 Should and 1 Nice, no Must; second pass clean, all seven closed
REVIEW: first pass fix-then-ship, 1 Must, 2 Should, 1 Nice; second pass ship, no new findings
WAIVERS: none
STOPPED BECAUSE: all pass and review ship
NEXT: the wave 3 integration is the next step
```

## 1. What the wave delivered

A DuckDB analytical layer over the Product Hunter SQLite database. SQLite stays the authoritative transactional store; DuckDB is derived analytical state. Nothing reads the layer yet: no worker, no CLI command and no schedule drives it.

Files and line counts:

```text
   64  src/product_hunter/analytics/__init__.py
  780  src/product_hunter/analytics/ingest.py
  166  src/product_hunter/analytics/queries.py
  289  src/product_hunter/analytics/tables.py
  589  src/product_hunter/analytics/views.py
  300  tests/test_analytics_ingest.py
  522  tests/test_analytics_views.py
```

The design:

- The layer opens SQLite read-only through `product_hunter.db.connection.connect(path, read_only=True)`. It has no write path to the authoritative store at all. That is structural, not a convention.
- The mirrored tables are discovered from the SQLite schema at runtime (`PRAGMA table_info`), so a table a later wave adds is mirrored with no edit here.
- Every mirrored row carries `_ph_hash`, the SHA-256 of the canonical JSON of the SQLite row. The hash is computed on the SQLite side only, because DuckDB returns a `TIMESTAMP` as a `datetime` and SQLite returns the same column as a string, so a hash computed on each side would not agree.
- Four operations: `snapshot()` reads every table in full; `refresh()` reads the rows newer than a stored watermark, writes only the rows whose hash changed, and removes the rows the database no longer holds; `rebuild()` drops the derived state and snapshots again; `reconcile()` compares both sides row by row and names the added, removed and changed keys per table. SQLite wins on any disagreement.
- The public surface: `Analytics` (a context manager with `open`, `snapshot`, `refresh`, `rebuild`, `reconcile`, `views`, `sql`, `query`), `AnalyticsError`, `IngestReport`, `TableReport`, `ReconcileReport`, `TableDrift`, `ViewReport`, `TableSpec`, `QUERIES`, `VIEW_DDL`, `analytics_path`, `discover`, `row_hash`, `row_key`.
- The DuckDB file is `db/product_hunter.duckdb`, overridable with `PH_ANALYTICS_PATH`. It is gitignored (`*.duckdb`) and it is not committed.

## 2. Gate evidence

```text
uv run pytest
2170 passed in 72.99s (0:01:12)
exit code 0
```

The base commit `00a6ca3` was `2138 passed`, so 32 tests are new. The verify node observed the gate independently of the implement node and reported `2168 passed in 55.52s`, exit 0, 73 test files, no failures and no skips, at the point before the review fixes added two more tests. The final observed state is `2170 passed`, exit 0.

## 3. The two harden passes

The first pass returned 6 Should and 1 Nice, no Must. The findings were: no identifier charset validation on table and column names taken from SQLite's catalog; errors raised but never logged; the shared DuckDB connection is not thread-safe and nothing said so; `PH_ANALYTICS_PATH` not resolved, so `..` could escape the intended directory; `rebuild()` dropped the derived state outside a transaction; the stale-table cleanup and the view creation ran outside a transaction; and the state table's name was interpolated unquoted in three places. All seven were fixed. The second pass returned clean: all seven verified closed, no new defects, no removable code.

## 4. The two review passes

The first review ran the six passes (correct, safe, load, tested, fast, lean) and returned `fix-then-ship` with 1 Must, 2 Should and 1 Nice.

- **Must**: the mirror declared the key columns NOT NULL, and SQLite lets a key column hold null while DuckDB refuses a null in any key column, so a row with a null key aborted the whole ingest. The fix detects the case up front from the SQLite schema (the `notnull` flag from `PRAGMA table_info` and the declared type, with an INTEGER primary key exempt because it is the rowid) and refuses that table: it is reported with the mode `refused` and a reason, logged as a warning, left out of the view creation, and skipped, while every other table still lands. `reconcile()` shows the refused table as drift.
- **Should**: `v_independence_summary` counted the author-cluster concentration by the author's identity group, so every author without a group collapsed into one bucket. The independence package counts such a mention against that author's own account. The view now groups the same way the package does, and a mention with neither a group nor an author counts against nobody.
- **Should**: the test named "the SQLite file is never written" only compared the file's bytes, which shows the file did not change but does not show the layer's connection cannot write. It now writes through the very connection the layer holds and asserts SQLite refuses the write.
- **Nice**: the mirror compared DuckDB's reported type names to the wanted ones exactly, so a future DuckDB that spelled a type differently would drop and rewrite every mirrored table on every snapshot. The comparison now drops the width and the precision and treats a type outside the set the layer writes as no difference.

The second review verified all four closed, found no new defect and returned `ship`. Two new tests were added for the first two findings and both were mutation-checked: with the fix reverted, the refused-table test fails with the reviewer's exact error, and the author-group test fails with `assert 1.0 == 0.5`.

## 5. The wall-clock defect the wave found in a shared file

`tests/test_db_checkpoints.py` captured `NOW = utcnow()` at import, that is during collection, and the `_leased_job` fixture claimed its lease with that timestamp and a default of 60 seconds. The fence in `product_hunter.db.leases.assert_live_lease` compares the stored expiry against the store's own wall clock and there is no way to pass another clock, which is deliberate. So the success-path checkpoint tests held a lease that died sixty seconds into the suite. The base commit reached that file with roughly forty-five seconds of margin; the new test files sort in front of it and added about thirty seconds, which left roughly twenty and made the suite fail outright on a slower machine. The fix is one line: the default lease of the success-path fixture is now 3600 seconds, with a comment saying why. The two cases that test an expired lease pass `lease_seconds` explicitly and are untouched. No assertion changed. The radius was widened by that one file and the spec records it.

## 6. The schemas

The wave adds no SQLite table and no migration. The SQLite schema is unchanged. The DuckDB side holds one mirrored table per SQLite table, the same column names, the declared type mapped (INTEGER to BIGINT, REAL to DOUBLE, TEXT to VARCHAR, BLOB to BLOB), a column whose name ends in `_at` (or is `valid_from`/`valid_to`) mirrored as TIMESTAMP, plus the one extra `_ph_hash` column. The derived state keeps its own bookkeeping in one table, `analytics_state(table_name, watermark, row_count, ingested_at)`.

Six canonical tables are read by name and arrive with a later wave: `opportunities`, `opportunity_scores`, `opportunity_claims`, `opportunity_evidence`, `experiments`, `research_requests`.

## 7. The sixteen views

| View | What it answers |
|---|---|
| `v_best_author` | best author per document |
| `v_evidence_mentions` | evidence mentions |
| `v_pain_point_evidence` | pain point to evidence |
| `v_pain_point_trend` | which pain points are growing |
| `v_source_yield` | source useful-evidence yield |
| `v_community_evidence` | communities producing independent evidence |
| `v_independence_summary` | independence summary |
| `v_source_concentration` | where evidence is concentrated |
| `v_opportunity_evidence_trend` | opportunities gaining or losing evidence |
| `v_score_history` | score history (needs `opportunity_scores`) |
| `v_run_analytics` | run analytics |
| `v_run_budget` | research run budget consumers |
| `v_collection_health` | collection health |
| `v_discovery_yield` | discovery yield |
| `v_query_family_performance` | saturated query families |
| `v_temporal_persistence` | temporal persistence |

A view over a table that does not exist yet is skipped and reported, never faked. DuckDB binds a view when it creates it, so the check comes first. `v_score_history` is the one that needs the later-wave tables, and it is skipped on a database that does not have `opportunity_scores`.

## 8. The seven named queries

`growing_pain_points`, `independent_evidence_communities`, `source_useful_yield`, `saturated_query_families`, `opportunity_movement`, `concentration_hotspots`, `run_budget_consumers`. They share one SQL table with the `Analytics.query` dispatcher, and a query whose view is missing is refused by name rather than answered with an empty list.

## 9. The design decisions

Four SubQ Snap calls, 0 errors, 0 cache hits, 461 + 570 + 1195 input tokens across the three that reported usage.

- Stage 0, risk lane: the risk floor is `medium` at 0.95 confidence, and the specialization is `backend` at 0.97.
- Stage 3, the ingestion path: the plan that discovers the schema at runtime and reads through the standard library `sqlite3` module, keeping SQLite strictly read-only, was chosen at 0.98 confidence over the plan that hand-lists the schema and attaches SQLite into DuckDB with the sqlite extension. The reason is in the spec: the extension may need a network install at runtime, and it would give DuckDB a write path to the authoritative store, guarded only by a flag.
- Stage 7, the verdict severity: `should_fix` at 0.65, with `must_fix` at 0.11; the disposition was `ship_as_is` at 0.85. The review that followed had more information and returned `fix-then-ship`, which is what the wave did.

The four operations and the rest of the design are in section 1.

## 10. The integration risks, honestly

1. The incremental read is O(n) in the key set, because a row deleted in SQLite cannot be noticed by a filter on a timestamp. The write is O(changed).
2. A row that changes without moving its watermark column is not seen by `refresh()` alone. `reconcile()` finds it. The tables whose writers bump a watermark are the ones the refresh is useful for.
3. The Python round trip is the ingestion cost. DuckDB can attach a SQLite file directly and would be faster, but that would give DuckDB a write path to the authoritative store. The trade is deliberate.
4. DuckDB 2.0.0's `INSERT OR REPLACE` is slow on wide tables, measured at about five milliseconds per column per statement, so sixty columns cost about three hundred milliseconds. A plain insert plus a delete pass measured about thirty times faster and was not adopted, because the plan named the statement and the rewrite is the next wave's call. A snapshot of the full forty-seven-table schema takes about 1.4 seconds and a rebuild about 2.8.
5. A SQLite value that does not fit its declared column type fails that table, the transaction rolls back and the error names the table. There is no lossy fallback.
6. The watermark comparison is a string comparison on the ISO text SQLite holds. Mixing the `Z` and `+00:00` formats would order wrong; nothing in the repository mixes them today.
7. The layer was never run against the repository's real `db/product_hunter.db`, only against freshly migrated test databases. No concurrent writer was tested. The DuckDB file is not locked by the layer and the class is documented as not thread-safe.
8. The `opportunities` and `opportunity_scores` tables do not exist in any migration yet, so the score history and the opportunity views are exercised only against a test database that applies the projection contract's `CANONICAL_DDL`.
9. Nothing calls the layer: no worker, no CLI command and no schedule drives it. The DuckDB file is derived state that has to be refreshed by whoever wants it fresh.

## 11. Wave 2 context

The Wave 2 final integration report lists Qdrant and DuckDB as deferred: "Spec section 38A Phase 6 production adapters; the similarity layer takes an embedding identity and a space id instead." This wave is that adapter for DuckDB. It does not touch Qdrant, the crawler, the browser rungs or the managed scraper, and it does not touch the Obsidian projection.

## 12. What was not checked

The layer was never run against the repository's real `db/product_hunter.db`; no concurrent writer was tested; the DuckDB file is not locked by the layer; the `opportunities` and `opportunity_scores` tables do not exist in any migration yet, so the score history and opportunity views are exercised only against a test database; and nothing calls the layer, so no real run has ever refreshed it.

## 13. The blast radius and the widenings

Declared at stage 0 with 15 files. Widened twice with a recorded reason: once for `docs/reasonix/handoffs/wave-3-duckdb-analytics-handoff.md` (the house convention for a wave handoff) and once for `tests/test_db_checkpoints.py` (the wall-clock lease defect in section 5). The spec's section 11 carries both widenings.

## 14. The tiers and the nodes

The run used the free tier. Stages 2, 6 and 7 were run and not skipped. The stage 5 visual gate does not apply: no UI file changed, and the adapter says `visualRequired: never`.

The nodes, in order: the implement node built the package and the tests; the verify node observed the code gate independently of the implement node; two harden passes ran; two review passes ran; the report node wrote this file. The models per node are in the block at the top.

Two things about the sequence are worth recording, because they changed who did the work.

The implement node's provider was unavailable for the follow-up that fixed the wall-clock lease in `tests/test_db_checkpoints.py`. Two dispatches failed at the provider and the third, on the second rung, did the work.

The seven harden fixes were applied by the orchestrator rather than by an implement node. A dispatch for them returned without changing a file, and the fixes were then made directly, each one verified by the gate and then by the second harden pass. The two review fixes and the two new tests that cover them were applied the same way. The gate, the harden pass and the review pass all ran on the result, and the review's close-out verified the fixes against the code rather than against the orchestrator's account of them.

## 15. A correction to the mission brief

The mission brief asked for the report at `docs/resonix/reports/wave-3-duckdb.md`. The repository's directory is `docs/reasonix/`, and every other report is there. The report is written to `docs/reasonix/reports/wave-3-duckdb.md`.

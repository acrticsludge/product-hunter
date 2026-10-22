# Plan: the DuckDB analytical layer (Wave 3, Session 2)

**Run ID:** `wave3-duckdb-analytics-2026-10-22`
**Branch:** `wave3/duckdb-analytics`
**Spec:** [../specs/wave-3-duckdb-analytics.md](../specs/wave-3-duckdb-analytics.md)

## 1. The files

| File | What it holds |
|---|---|
| `src/product_hunter/analytics/__init__.py` | The public surface: `Analytics`, `AnalyticsError`, `analytics_path`, the report dataclasses. |
| `src/product_hunter/analytics/tables.py` | The mirrored-table spec: schema discovery, the type mapping, the timestamp rule, the watermark choice, the row hash, the row key. |
| `src/product_hunter/analytics/ingest.py` | The `Analytics` class: open, snapshot, refresh, rebuild, reconcile, and the raw read path. |
| `src/product_hunter/analytics/views.py` | The view DDL and the function that creates the views whose source tables exist. |
| `src/product_hunter/analytics/queries.py` | The named analytical queries. |
| `tests/test_analytics_ingest.py` | Snapshot, refresh, rebuild, duplicate, changed, deleted, reconcile, failure and recovery. |
| `tests/test_analytics_views.py` | The views and the named queries: score history, time-based queries, concentration queries. |
| `pyproject.toml`, `uv.lock` | `duckdb` added as a runtime dependency. Already done. |

Nothing else changes. No migration, no CLI, no change to an existing module.

## 2. The dependency

`duckdb>=2.0,<3` is in `project.dependencies`. It is already added to
`pyproject.toml` and `uv.lock` in this branch; the implementer does not need to
touch either.

## 3. `tables.py`

### 3.1 The dataclasses

```python
@dataclass(frozen=True)
class ColumnSpec:
    name: str
    duckdb_type: str        # BIGINT | DOUBLE | VARCHAR | BLOB | TIMESTAMP
    is_timestamp: bool

@dataclass(frozen=True)
class TableSpec:
    name: str
    columns: tuple[ColumnSpec, ...]
    primary_key: tuple[str, ...]

    @property
    def hash_column(self) -> str          # "_ph_hash"
    @property
    def watermark(self) -> str | None     # from the priority list
    def column_names(self) -> tuple[str, ...]
    def create_ddl(self) -> str
    def insert_sql(self) -> str           # INSERT OR REPLACE, all columns
```

`create_ddl()` produces:

```sql
CREATE TABLE IF NOT EXISTS "sources" (
    "source_id" VARCHAR NOT NULL,
    "source_type" VARCHAR NOT NULL,
    ...
    "_ph_hash" VARCHAR NOT NULL,
    PRIMARY KEY ("source_id")
);
```

The primary key columns are `NOT NULL`. A column whose SQLite declaration
allows NULL stays nullable.

### 3.2 The rules

```python
TIMESTAMP_SUFFIX = "_at"
EXTRA_TIMESTAMP_COLUMNS = frozenset({"valid_from", "valid_to"})
WATERMARK_PRIORITY = (
    "updated_at", "last_updated", "last_seen_at", "created_at",
    "recorded_at", "observed_at", "occurred_at", "detected_at",
    "checked_at", "attempted_at", "started_at", "last_request_at",
    "last_fetched_at", "valid_from", "published_at", "fetched_at",
    "selected_at", "discovered_at", "completed_at", "deleted_at",
    "superseded_at",
)
```

- `is_timestamp_column(name)` → `name.endswith("_at")` or the name is in
  `EXTRA_TIMESTAMP_COLUMNS`.
- `duckdb_type(declared)` → `INTEGER`/`INT` → `BIGINT`, `REAL`/`FLOAT`/`DOUBLE`
  → `DOUBLE`, `BLOB` → `BLOB`, `TEXT`/`VARCHAR`/`CHAR` → `VARCHAR`, anything
  else → `VARCHAR`.
- `discover(conn)` → reads `sqlite_master` for `type='table'` and a name that
  does not start with `sqlite_`, then `PRAGMA table_info` for each. The
  primary key is the columns whose `pk` is non-zero, in `pk` order. A table
  with no primary key uses `rowid` as a single-column key.

### 3.3 The hash and the key

```python
def row_hash(values: Sequence[object]) -> str:
    """The SHA-256 of the canonical JSON of the row's values."""
```

The canonical form: `json.dumps` of a list where `None` is `null`, `bytes`
are `{"__bytes__": "<hex>"}`, `int`/`float`/`str` are themselves. `sort_keys`
does not apply because the order is the column order. The digest is the
hex SHA-256 of that string.

```python
def row_key(spec: TableSpec, values: Sequence[object]) -> str:
    """The key of the row: the JSON list of its primary key values."""
```

`json.dumps([...])` of the primary key values, in key order. A composite key
is therefore one string, and the key is stable across runs.

## 4. `ingest.py`

### 4.1 The reports

```python
@dataclass(frozen=True)
class TableReport:
    name: str
    mode: str            # "snapshot" | "incremental" | "skipped"
    rows_read: int
    rows_written: int
    rows_removed: int
    watermark: str | None

@dataclass(frozen=True)
class IngestReport:
    started_at: str
    tables: tuple[TableReport, ...]
    def summary(self) -> str
    @property
    def rows_written(self) -> int

@dataclass(frozen=True)
class TableDrift:
    name: str
    sqlite_rows: int
    duckdb_rows: int
    added: tuple[str, ...]     # in DuckDB, not in SQLite
    removed: tuple[str, ...]   # in SQLite, not in DuckDB
    changed: tuple[str, ...]   # in both, the hash differs

@dataclass(frozen=True)
class ReconcileReport:
    tables: tuple[TableDrift, ...]
    @property
    def in_sync(self) -> bool
    def drifted_tables(self) -> tuple[str, ...]
```

### 4.2 The state table

The derived state holds one table of its own:

```sql
CREATE TABLE IF NOT EXISTS analytics_state (
    table_name  VARCHAR PRIMARY KEY,
    watermark   VARCHAR,
    row_count   BIGINT NOT NULL DEFAULT 0,
    ingested_at VARCHAR NOT NULL
);
```

The watermark is stored as the ISO-8601 string SQLite holds, so the comparison
is a string comparison on the same format the writers use.

### 4.3 The class

```python
class Analytics:
    def __init__(self, duck: duckdb.DuckDBPyConnection, sqlite: sqlite3.Connection) -> None

    @classmethod
    def open(cls, duckdb_path: str | Path, sqlite_path: str | Path) -> Analytics

    def close(self) -> None
    def __enter__(self) -> Analytics
    def __exit__(self, *exc: object) -> None

    def snapshot(self) -> IngestReport
    def refresh(self) -> IngestReport
    def rebuild(self) -> IngestReport
    def reconcile(self) -> ReconcileReport
    def views(self) -> tuple[str, ...]
    def sql(self, statement: str, parameters: Sequence[object] = ()) -> list[dict]
    def query(self, name: str, **parameters: object) -> list[dict]
```

`open()`:

- The DuckDB path's parent directory is created. The file is created when it
  does not exist.
- The DuckDB connection runs `SET TimeZone='UTC'`, so a `TIMESTAMP` column
  holds the UTC wall clock the SQLite string carried.
- The SQLite connection is `product_hunter.db.connection.connect(path,
  read_only=True)`. A missing file raises `FileNotFoundError` from that
  helper; `open()` re-raises it as `AnalyticsError` with the path in the
  message. The analytics layer never opens SQLite for writing.
- The two paths must not be the same file. Compare the resolved paths and
  raise `AnalyticsError` when they match.

`sql()` returns a list of dictionaries, with the rows converted to plain
Python values. It runs a read-only statement; the statement is the caller's.

### 4.4 `snapshot()`

For every discovered table:

1. Create the DuckDB table when it does not exist. When it exists with a
   different shape, drop it and create it again, because the SQLite schema
   is the authority.
2. Read every row from SQLite: `SELECT <columns> FROM "<table>"`.
3. For every row, compute the hash and the key. Insert with `INSERT OR
   REPLACE INTO`.
4. Delete the rows of the DuckDB table whose key is not in the SQLite table.
   Read the SQLite key set with `SELECT <pk> FROM "<table>"` and delete with
   a correlated `NOT EXISTS` against a temporary table of the keys.
5. Store the watermark (the largest watermark value read, as a string) and the
   row count in `analytics_state`.

Each table is done inside its own DuckDB transaction, so a failure leaves that
table as it was. The whole snapshot is one transaction per table, not one per
database: a failure in one table must not undo the tables that already
succeeded, because the report says which tables landed.

### 4.5 `refresh()`

For every discovered table:

1. Read the stored watermark from `analytics_state`.
2. If the table has a watermark column and a stored watermark: read
   `SELECT <columns> FROM "<table>" WHERE "<watermark>" > ?`. A row whose
   watermark column is NULL is read too (`OR "<watermark>" IS NULL`), because
   a row that never got a timestamp still has to be mirrored.
   Otherwise read every row.
3. For every row read, compute the hash. Read the stored hash for that key
   from the DuckDB table. Write the row only when the key is new or the hash
   differs. The row count that is written is the number of rows written, not
   the number read.
4. Delete the rows whose key is gone from SQLite, the same way the snapshot
   does.
5. Update the watermark to the largest watermark value seen.

`refresh()` on a table that has never been ingested behaves like a snapshot
of that table: with no stored watermark, the whole table is read.

### 4.6 `rebuild()`

Drop every mirrored table, drop `analytics_state`, drop the views, and run
`snapshot()`. The result is a derived state built from the authoritative
source alone.

### 4.7 `reconcile()`

For every table that exists in both databases:

- Read `(key, hash)` from SQLite: the same hash function, over the same
  values.
- Read `(key, _ph_hash)` from DuckDB.
- Compare the two key sets and the hashes. Report the added keys, the removed
  keys and the changed keys.

`in_sync` is true when no table reports drift. A table that exists in SQLite
and not in DuckDB is drift: every added key.

`reconcile()` writes nothing.

### 4.8 The failure and recovery rules

- Every table is ingested inside a DuckDB transaction. A failure rolls that
  table back and leaves the state row as it was.
- A failure inside `snapshot()` or `refresh()` propagates as
  `AnalyticsError`, and the tables that already landed stay landed. The
  derived state is never half a table.
- The derived state is never emptied to recover from an error. The repair is
  `rebuild()`.

## 5. `views.py`

```python
VIEW_DDL: tuple[tuple[str, str, tuple[str, ...]], ...]
```

Each entry is `(view_name, sql, required_tables)`. `create_views(conn,
available)` creates a view when every table it names is in `available`, and
skips it otherwise. A skipped view is reported, never faked.

DuckDB binds a view at creation, so a view over a table that does not exist
cannot be created. That is why the required tables are checked first.

### 5.1 The views

Every view names the columns it selects explicitly. A mirrored table carries
the extra `_ph_hash` column, and a view never selects it.

**`v_evidence_mentions`** — one row per live evidence span.

```sql
SELECT e.span_id, e.doc_id, e.quote, e.live_status AS span_live_status,
       e.strength, e.strength_score, e.reliability, e.reliability_score,
       e.observation_count, e.created_at,
       d.source_id, d.community_id, d.published_at, d.text_sha256,
       d.live_status AS document_live_status,
       c.name AS community_name,
       a.author_id, a.identity_confidence, a.deletion_status,
       s.source_type, s.terms_status,
       p.pain_id, p.normalized_key, p.domain, p.actor, p.action,
       p."object", p.consequence, p.severity, p.frequency, p.accepted
FROM evidence e
JOIN documents d ON d.doc_id = e.doc_id
LEFT JOIN communities c ON c.community_id = d.community_id
LEFT JOIN v_best_author ba ON ba.doc_id = d.doc_id
LEFT JOIN authors a ON a.author_id = ba.author_id
LEFT JOIN pain_points p ON p.span_id = e.span_id
LEFT JOIN sources s ON s.source_id = d.source_id
```

`object` is a reserved word in DuckDB, so it is quoted as `"object"`.

**`v_best_author`** — the best author link per document. A document can have
more than one author link, and a fan-out would multiply the evidence rows, so
the best link (the highest confidence, then the lowest `link_id`) is chosen
with a window function:

```sql
SELECT doc_id, author_id FROM (
    SELECT doc_id, author_id,
           row_number() OVER (PARTITION BY doc_id
                              ORDER BY confidence DESC, link_id ASC) AS rank
    FROM author_links
) WHERE rank = 1
```

`v_best_author` is a view of its own and is created before
`v_evidence_mentions`.

**`v_pain_point_evidence`** — per pain point: `pain_id`,
`normalized_key`, `domain`, `severity`, `frequency`, `accepted`, the mention
count, the canonical document count, the distinct author count, the distinct
community count, the distinct source count, the independent author count (the
authors whose `identity_confidence >= 0.5`), the uncertain author count (the
authors below it), and the largest single-source share.

The author is read through `v_best_author`, so a document contributes one
author.

**`v_pain_point_trend`** — per `normalized_key` per month: the month (the
first of the month, a `TIMESTAMP`), the mention count, the distinct author
count, the distinct community count, and the change in mentions against the
previous month (`LAG` over the window ordered by month).

The month comes from `date_trunc('month', e.created_at)`. `created_at` is a
`TIMESTAMP` in the mirrored table.

**`v_source_yield`** — per source: the source id and type, the fetch count,
the document count, the evidence count, the accepted pain point count, the
useful-evidence yield (the accepted pain point count over the document count,
zero when there is no document), the distinct author count, the distinct
community count, and the first and last sighting.

**`v_community_evidence`** — per community: the community id and name, the
source id, the mention count, the canonical document count, the distinct
author count, the independent author count, the uncertain author count, the
distinct source count and the first and last sighting.

**`v_independence_summary`** — per `normalized_key`: the raw mention count,
the canonical observation count (the distinct `text_sha256`), the independent
author count, the uncertain author count, the independent community count, the
propagation-adjusted count (the mentions whose document is not the `to_doc_id`
of a propagation edge), the propagation chain count, the source concentration,
the community concentration and the author-cluster concentration.

The three concentrations are the largest single holder's share of the raw
mentions, computed with a window function over the counts, so they match the
shape the independence package computes.

**`v_source_concentration`** — per `normalized_key`: the source count, the
largest single-source share, the top-three share, the Herfindahl index (the
sum of the squared shares) and a `concentrated` flag (the largest share above
`0.5`).

**`v_opportunity_evidence_trend`** — per `cluster_id` per month: the mention
count and the change against the previous month. The cluster is the
opportunity scope; the members come from `cluster_members` where
`valid_to IS NULL`, joined to the pain points and their evidence.

**`v_score_history`** — per `opportunity_id`: the `score_id`, the potential,
the confidence, the `created_at`, and the change in potential and confidence
against the previous score of the same opportunity (`LAG` over the window
ordered by `created_at`, then `score_id`).

This view needs the `opportunity_scores` table, which no migration creates
yet. It is skipped while the table is absent.

**`v_run_analytics`** — per `research_runs.run_id`: the status, the created,
started and completed times, the wall time in seconds (the difference between
the completed and the started time, null when either is absent), the tool call
count, the distinct tool count, the failed tool call count, the gate verdict
count and the passed gate count.

**`v_run_budget`** — per `research_runs.run_id`: the spend units, the cost in
micros, the share of the total cost of every run, and the split by tool,
provider and model. The split is three more columns built from
`spend_ledger`, or three companion views. The simplest shape that answers the
question: one row per run and per tool, with the run totals repeated.

**`v_collection_health`** — per source: the attempt count, the distinct
outcome count, the success count, the failure count, the success rate, the
mean and the maximum latency, the total bytes, the not-modified count and the
distinct reason codes.

**`v_discovery_yield`** — per discovery record: the record id, the run id,
the family, the query, the status, the result count, the useful count, the
novelty, the duplicate rate, the cost, and the useful-per-dollar yield.

The discovery records live in `research_events.payload_json`, a JSON string.
Read them with DuckDB's JSON functions:

```sql
SELECT event_id, run_id, created_at,
       json_extract_string(payload_json, '$.family')        AS family,
       json_extract_string(payload_json, '$.query')         AS query,
       json_extract_string(payload_json, '$.status')        AS status,
       CAST(json_extract_string(payload_json, '$.result_count') AS BIGINT) AS result_count,
       CAST(json_extract_string(payload_json, '$.useful_count') AS BIGINT) AS useful_count,
       CAST(json_extract_string(payload_json, '$.novelty') AS DOUBLE)      AS novelty,
       CAST(json_extract_string(payload_json, '$.duplicate_rate') AS DOUBLE) AS duplicate_rate,
       CAST(json_extract_string(payload_json, '$.cost') AS DOUBLE)          AS cost
FROM research_events
WHERE event_type = 'discovery'
```

The `event_type` is the constant the discovery store writes. Read it from
`product_hunter.discovery.store` rather than repeating the literal.

**`v_query_family_performance`** — per family: the search count, the result
count, the useful count, the mean novelty, the mean duplicate rate, the mean
useful count, the total cost, the useful-per-dollar yield, and the saturation
verdict.

The verdict: a family is `saturated` when its mean novelty is below `0.1` or
its mean duplicate rate is above `0.9`, the same bands the discovery frontier
policy uses (`config`/the `SaturationPolicy` defaults). The thresholds are
named constants in the module, and the comment says where they come from.

**`v_temporal_persistence`** — per `normalized_key`: the first sighting, the
last sighting, the span in days, the number of active months, the months
since the last sighting, and the persistence class (`single_sighting`,
`emerging`, `persistent`, `dormant`).

The class bands are named constants with a comment that says they are the
analytics layer's own classification and not a policy of the product.

## 6. `queries.py`

One function per question the mission names. Each one runs a `SELECT` against
a view and returns a list of dictionaries.

```python
def growing_pain_points(store, *, months: int = 3, limit: int = 50) -> list[dict]
def independent_evidence_communities(store, *, limit: int = 50) -> list[dict]
def source_useful_yield(store, *, limit: int = 50) -> list[dict]
def saturated_query_families(store, *, limit: int = 50) -> list[dict]
def opportunity_movement(store, *, months: int = 3, limit: int = 50) -> list[dict]
def concentration_hotspots(store, *, limit: int = 50) -> list[dict]
def run_budget_consumers(store, *, limit: int = 50) -> list[dict]
```

`store.query(name, **parameters)` dispatches to the same SQL, so the named
functions and the dispatcher share one table of SQL.

The parameters are bound, never interpolated into the SQL text. DuckDB's
Python API takes named parameters as `$name`; `:name` is a syntax error
there.

A view that does not exist raises `AnalyticsError` naming the view and the
table that is missing. It never returns an empty list to hide a missing
table, and it never invents a row.

The ordering of every result is fixed, so two calls on the same data return
the same list.

## 7. `__init__.py`

```python
from product_hunter.analytics.ingest import (
    Analytics, AnalyticsError, IngestReport, ReconcileReport, TableDrift, TableReport,
)
from product_hunter.analytics.queries import QUERIES
from product_hunter.analytics.tables import TableSpec, discover, row_hash, row_key

__all__ = [...]
```

`analytics_path(root=None)` follows the house pattern of
`product_hunter.config.database_path`: `PH_ANALYTICS_PATH` when set, otherwise
`db/product_hunter.duckdb`. It lives in the analytics package, not in
`config.py`, because `config.py` is outside this run's declared radius.

## 8. The tests

### 8.1 `tests/test_analytics_ingest.py`

The fixture builds a real SQLite database: `migrate()` on a temporary file,
then rows written with plain SQL.

| Test | What it holds |
|---|---|
| `test_snapshot_mirrors_every_table` | Every SQLite table has a DuckDB table with the same row count. |
| `test_snapshot_stores_the_row_hash` | The `_ph_hash` of a row is the SHA-256 of its canonical form. |
| `test_refresh_reads_only_the_new_rows` | After a refresh, a new row is present and the report says how many rows were written. |
| `test_duplicate_ingestion_is_a_no_op` | Two snapshots of the same data leave one row per key. |
| `test_changed_source_is_rewritten` | A changed row has the new value and the new hash after a refresh. |
| `test_deleted_source_is_removed` | A row deleted in SQLite is gone from DuckDB after a refresh. |
| `test_rebuild_matches_the_snapshot` | The derived state after a rebuild equals the state after a snapshot, row for row and hash for hash. |
| `test_reconcile_names_the_drift` | A row changed behind the analytics layer's back is named by `reconcile()`. |
| `test_reconcile_is_in_sync_after_a_snapshot` | A fresh snapshot reconciles clean. |
| `test_rebuild_repairs_the_drift` | After a rebuild the drift is gone. |
| `test_a_failed_refresh_leaves_the_state_readable` | A refresh that raises leaves the previous derived state intact. |
| `test_the_sqlite_file_is_never_written` | The SQLite file's hash is the same before and after a snapshot. |
| `test_a_missing_sqlite_file_is_refused` | `open()` on a missing path raises `AnalyticsError`. |

The failure test uses a table whose insertion raises: a row whose value does
not fit the DuckDB column, or a table that is dropped from SQLite between the
discovery and the read. The simplest reliable way is to make the analytics
layer read a table that was dropped after the discovery: the read raises, the
transaction rolls back, and the tables that landed before it are still there.

### 8.2 `tests/test_analytics_views.py`

The fixture builds a SQLite database with a known shape: two sources, three
communities, documents, authors, evidence spans, pain points, a cluster, two
score rows, a research run with tool calls and spend, a collection attempt and
two discovery records.

`opportunity_scores` is created in the fixture with the DDL the projection's
read contract already names (`product_hunter.projection.contract.CANONICAL_DDL`),
so the score history view has a table to read. The fixture applies that DDL to
the temporary database.

| Test | What it holds |
|---|---|
| `test_the_views_are_created` | Every view whose tables exist is created. |
| `test_the_score_history_reads_the_scores` | Two score rows become two history rows with the change against the previous score. |
| `test_a_pain_point_that_is_growing_is_found` | The pain point with the rising monthly count is in the trend view and in `growing_pain_points`. |
| `test_a_stale_pain_point_is_not_reported_as_growing` | A pain point whose counts fall is not reported as growing. |
| `test_the_concentration_view_names_the_source` | The source that holds the evidence has the largest share. |
| `test_the_independence_counters_are_counted` | The independent, uncertain and community counts match the fixture. |
| `test_the_query_family_view_marks_the_saturated_family` | The family with the low novelty is marked saturated. |
| `test_the_run_budget_view_ranks_the_runs` | The run with the larger spend is first. |
| `test_the_collection_health_view_counts_the_outcomes` | The success and the failure are counted. |
| `test_the_temporal_persistence_view_classifies` | A single sighting is `single_sighting` and a spread one is `persistent`. |

## 9. The order of the work

1. `tables.py` — the spec, the discovery, the hash, the key.
2. `ingest.py` — the class, `snapshot`, `refresh`, `rebuild`, `reconcile`.
3. `views.py` — the DDL and the creation.
4. `queries.py` — the named queries.
5. `__init__.py` — the public surface.
6. `tests/test_analytics_ingest.py`.
7. `tests/test_analytics_views.py`.
8. `uv run pytest` — the whole suite, not only the new files.

## 10. The done test

```text
uv run pytest            -> the whole suite passes, 2138 plus the new tests
uv run pytest tests/test_analytics_ingest.py tests/test_analytics_views.py
```

No test is skipped, weakened or deleted. No assertion is loosened to make a
test pass.

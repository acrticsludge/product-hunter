# Implement prompt: the DuckDB analytical layer (Wave 3, Session 2)

**Run ID:** `wave3-duckdb-analytics-2026-10-22`
**Branch:** `wave3/duckdb-analytics`
**Model:** `opencode-go/longcat-2.5-preview-free`, then `opencode-go/step-5-preview-free`
for the follow-up, then `opencode-go/nvidia/nemotron-3.5-super-vl` for the harden
fixes.

The contract is in the spec, the file-by-file plan is in the plan, and the
repository map is in the handoff. This file records what the implementer was
asked, so the next wave can repeat the run.

## What to read, in this order

1. `docs/reasonix/specs/wave-3-duckdb-analytics.md` — the contract.
2. `docs/reasonix/plans/wave-3-duckdb-analytics.md` — the module layout, the
   dataclasses, the view SQL, the query names, the test names.
3. `docs/reasonix/handoffs/wave-3-duckdb-analytics-handoff.md` — the repository
   map, the measured DuckDB facts, the decisions already taken.

## The files to write

```text
src/product_hunter/analytics/__init__.py
src/product_hunter/analytics/tables.py
src/product_hunter/analytics/ingest.py
src/product_hunter/analytics/views.py
src/product_hunter/analytics/queries.py
tests/test_analytics_ingest.py
tests/test_analytics_views.py
```

`pyproject.toml` and `uv.lock` carry `duckdb>=2.0,<3`. Nothing else changes.

## The rules that shape the work

- SQLite is authoritative. Open it read-only through
  `product_hunter.db.connection.connect(path, read_only=True)`. The layer must
  have no write path to the authoritative store at all.
- Mirror the schema at runtime, do not list the tables. A table a later wave
  adds is then mirrored with no edit here.
- Compute the row hash on the SQLite side only, and store it in the mirrored
  row as `_ph_hash`. DuckDB hands a `TIMESTAMP` back as a `datetime` and SQLite
  hands the same column back as a string, so a hash computed on each side
  would not agree.
- SQLite wins. `reconcile()` names the drift, `rebuild()` repairs it, and the
  rebuild is deterministic.
- A view over a table that does not exist yet is skipped and reported, never
  faked. DuckDB binds a view when it creates it, so the check has to come
  first.
- No migration, no CLI command, no change to an existing module, no dependency
  other than `duckdb`.
- No test is skipped, weakened or deleted. No assertion is loosened to make a
  test pass.

## The measured DuckDB facts

Checked against `duckdb==2.0.0` in this repository's virtual environment.
Do not re-derive them, and do not assume the opposite.

| Fact | Why it matters |
|---|---|
| `INSERT OR REPLACE INTO` works | The upsert of a mirrored row. |
| `DELETE FROM t WHERE NOT EXISTS (SELECT 1 FROM src s WHERE s.pk = t.pk)` works | The delete-missing pass. |
| A view over a missing table fails at creation | The views are created only when their tables exist. |
| Named parameters are `$name`; `:name` is a syntax error | The query parameters are bound. |
| `json_extract_string(payload, '$.field')` reads a JSON string out of a VARCHAR | The discovery records live in `research_events.payload_json`. |
| `date_trunc('month', ts)` works on a `TIMESTAMP` | The month buckets of the trend views. |
| `SET TimeZone='UTC'` | A naive `TIMESTAMP` holds the UTC wall clock. |
| `object` is a reserved word | Quote it as `"object"`. |
| DuckDB enforces NOT NULL on every PRIMARY KEY column | A SQLite key column that can hold null cannot be mirrored; the table is refused, not half mirrored. |

## The gate

```text
uv run pytest
```

The baseline on `00a6ca3` was `2138 passed`. The whole suite must pass, not
only the two new files.

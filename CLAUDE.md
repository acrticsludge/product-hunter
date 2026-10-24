# Working rules for this repo

## Speed: use every core

This machine has 24 logical CPUs. Long Python work must use them.

- Tests: always `uv run pytest -n auto` (pytest-xdist is a dev dependency).
  Locally that is `.venv\Scripts\python.exe -m pytest -q -n auto`.
  Drop `-n auto` only for a single test, or when a failure needs serial
  reproduction, and say why in the report.
- Any other CPU-bound Python batch (imports, re-indexing, backfills): split it
  across processes (`concurrent.futures.ProcessPoolExecutor`,
  `max_workers=os.cpu_count()`), not threads, unless the work is I/O-bound.
- Do not run two full-suite pytest runs at once; xdist already saturates the
  cores and a second run only makes both slower.

## Product Hunter specifics

- Python lives in `.venv`; the adapter gate is `uv run pytest`.
- SQLite is authoritative. Qdrant, DuckDB and Obsidian are derived projections.
- Never weaken a test, a quote check, or a source-policy rule to go green.
- Never hard-code a model id in business logic; route through the model router.

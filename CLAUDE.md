# Product Hunter — Working Rules

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
- The model router config is `config/model_routing.yaml`. Heavy reasoning uses
  `nvidia/nvidia/nemotron-3.5-super-vl-120b-a12b`; cheap gather tasks use
  `opencode-go/beam-free`. Never bypass the router.
- Sources are defined in `config/sources.yaml`. A source must be reviewed and
  approved before it is enabled. Suspended sources need `suspended_at` and
  `suspend_reason` fields.

## Project overview

Product Hunter is an evidence-driven pain-point research tool. It discovers
recurring problems from online communities, clusters them, scores them, and
produces ranked product opportunities with evidence trails.

Key entry point: `ph` (defined in `pyproject.toml` as `product_hunter.cli:main`).

## Architecture

- **SQLite** (`db/product_hunter.db`) is the authoritative store.
- **Qdrant** (vector search) is a derived projection for semantic search.
- **DuckDB** (`db/product_hunter.duckdb`) is a derived analytics projection.
- **Obsidian vault** (`data/vault/`) is a derived projection for human review.
- **Sources** are defined in `config/sources.yaml` and synced to the store.

## Documentation structure

```
docs/
  ARCHITECTURE.md              # System architecture and design decisions
  RESEARCH_CONTRACT.md         # Research stage contracts and validation
  MODEL_ROUTING_IMPLEMENTATION.md  # Model routing design
  spec-compliance.md           # Spec compliance tracking
  TOOLING.md                   # Development tooling notes
  decisions/                   # Architecture Decision Records (ADRs)
  handoffs/                    # Session handoff documents
  plans/                       # Implementation plans
  prompts/                     # Agent prompts and task definitions
  reports/                     # Research reports and audit reports
  specs/                       # Technical specifications
```

## Key directories

- `src/product_hunter/` — application code
  - `cli.py` — CLI entry point (`ph` command)
  - `handlers.py` — worker stage handlers (DISCOVERY, COLLECTION, etc.)
  - `autonomy/` — autonomy engine (operation layer, cadence, health, maintenance)
  - `runtime/` — supervised worker runtime (claim, lease, dispatch)
  - `db/` — SQLite store, migrations, jobs, model routing
  - `research/` — research run lifecycle, stages, evidence, investigation
  - `llm/` — LLM provider, catalog, routing, opencode integration
  - `semantic/` — Qdrant vector search integration
  - `probes.py` — 24/7 on-call research probe lifecycle
  - `preflight.py` — pre-run validation checks
- `config/` — YAML configuration (autonomy, cadence, sources, model_routing, etc.)
- `db/migrations/` — SQL migration files (v1-v17)
- `tests/` — pytest test suite
- `scripts/` — utility scripts (including ph_mcp_server.py)
- `.opencode/` — opencode agent definitions, commands, skills

## Running tests

```bash
uv run pytest -n auto                    # full suite
uv run pytest tests/test_foo.py -q       # single test file
uv run pytest -k "test_name" -q          # single test
```

## Database

- Authoritative: `db/product_hunter.db` (SQLite)
- Migrations: `db/migrations/0001_*.sql` through `0017_*.sql`
- Reset: `uv run ph db init` (creates fresh DB with all migrations)
- Status: `uv run ph db status`

## 24/7 operation

- Worker: `uv run python -m product_hunter.autonomy` (or via systemd `ph-agent.service`)
- Probes: `uv run ph probe create/list/status/start/pause/resume/stop/report`
- Qdrant: Docker container `qdrant` (port 6333, persistent storage)
- OpenCode: tmux session `opencode` (auto-starts on boot)
# Final Audit 01 — Wave 3 Architecture and Dependency Integrity

**Probe:** Final Audit Probe #1 (read-only)
**Date:** 2026-10-22
**Base:** `main` @ `00a6ca3` ("fix: close the second review round")
**Scope:** all 8 Wave 3 branches, the Wave 1/2 baseline they fork from, the spec, and each branch's own run report
**Mutation:** none. No branch was checked out, merged, committed, or reconfigured. The repository is unchanged.

---

## 1. Executive verdict

**Conditional go. The eight implementations can coexist as one system, but not through a merge.**

The good news first, because it is unusual and it matters: the branches are *module-level independent*. Not one of the 8 branches imports a new module introduced by another branch. Every new package (`autonomy/`, `analytics/`, `observability/`, `semantic/`, `collection/adapters/`, `llm/decision.py`, `llm/subq.py`, `communities.py`, `source_policy.py`, `source_expansion.py`) reaches back only into the Wave 1/2 tree. A static AST sweep of all nine trees — baseline plus 8 branches — found **zero eager import cycles**; every cycle that a naive import-graph walk reports is a `TYPE_CHECKING` or function-level import. The branches also agree on the hard architectural rules: SQLite stays authoritative, the outbox is the projection mechanism, there is one job queue, one scheduler, one single-instance lock. No branch forks the queue, the worker, the model router, or the source registry's write path.

The problem is one level up. All eight branches grew their own opinion about the *same shared surfaces*, and the shared surfaces are oversubscribed:

- **Three branches claim migration number `0008`.** The migration runner refuses a duplicate before it applies anything, so an un-renumbered merge breaks every `Store.open()` path in the product.
- **Three branches set the same test constant to the same value**, `8`, when the merged schema will be `10`. This is a guaranteed code-gate failure, not a risk.
- **Four branches rewrite `src/product_hunter/cli.py`** (1710 lines on main) inside the same `build_parser()` function and the same `main()` dispatch chain.
- **Three branches rewrite `src/product_hunter/config.py`**, and two of them changed the *signature* of the same private helper in opposite directions.
- **Four of the eight waves ship code that is wired to nothing**: the SubQ decision provider, the Qdrant projection handler, the DuckDB analytical layer, and the scraping ladder's driver injection.
- **Two branches independently solved the same two problems** — SQLite busy-retry and secret redaction — and moved the solutions into different files.

So the honest verdict is: the *architecture* is sound and the *integration surface* is not. The final integration agent is not doing a merge; it is doing a merge plus a design reconciliation of the six shared surfaces named in section 9. Two of the three blockers are deterministic (they will fail on the first `ph db init` and the first `uv run pytest`), and the third is structural: **four features exist but are unreachable** — the SubQ decision provider, the Qdrant projection handler, the DuckDB analytical layer, and the scraping ladder's browser/crawler/managed rungs.

One more thing the integrator must carry: **two of the eight branches have no adversarial review.** `wave3/autonomous-operation` skipped stage 7 because both free review models were unavailable, and was committed with the gate waived. `wave3/source-expansion`'s report contradicts itself about whether it was reviewed at all — see F-26. Treat both branches as green-but-unreviewed. The same caution applies to the branch reports as a class: the autonomy report misstates 12 file sizes, the qdrant report misstates 11 of 19 file sizes and claims CLI coverage that does not exist, and the duckdb report names six "later-wave tables read by name" when only one is.

---

## 2. Findings

Severity: **CRITICAL** = blocks the merge, deterministic. **HIGH** = the merged tree is broken or a feature is unreachable. **MEDIUM** = real defect or spec gap, survivable. **LOW** = hygiene, doc, or consistency.

### F-01 — CRITICAL — Three branches claim migration number `0008`

| Branch | File | New tables |
|---|---|---|
| `wave3/autonomous-operation` | `db/migrations/0008_autonomy.sql` | `worker_health`, `operation_runs`, `operation_events`, `operation_budget`, `source_pacing` |
| `wave3/source-expansion` | `db/migrations/0008_source_expansion.sql` | `source_candidates`, `source_reviews` |
| `wave3/observability-evaluation` | `db/migrations/0008_observability.sql` | `model_comparisons` |

`src/product_hunter/db/migrations.py` discovers migrations by parsing the leading integer out of the filename (`_MIGRATION_RE`, line 11) and:

- raises `MigrationError("duplicate migration number 8: ...")` at lines 54–57, and
- raises `MigrationError("gap in migration numbers between ...")` at lines 64–68.

`_discover()` runs at the top of `migrate()` (line 29), **before** anything is applied. So the failure is not partial: on a merged tree with two `0008` files, `ph db init`, `ph db status`, `ph db recover`, `ph db jobs`, `ph db outbox`, `ph db check`, `ph bench compare --db`, and `python -m product_hunter.autonomy` all raise immediately. Only the read-only commands (`ph observe metrics`, `ph observe trace`, `ph autonomy status`) survive, because they use `connect(path, read_only=True)` and never call `migrate()`.

The three files are **table-disjoint**, so renumbering is safe and no SQL needs editing. The `wave3/observability-evaluation` branch already knew this — `db/migrations/0008_observability.sql:16-21` says so in its own header:

> "Another wave 3 branch may also have taken the number; the loader refuses a duplicate and refuses a gap, so the integrator renumbers rather than merging two files under one number."

`db/migrations.py` itself is byte-identical on `main` and on the observability branch. The runner is not the problem; the filenames are.

**Fix:** renumber to `0008` / `0009` / `0010`, then fix F-02.

---

### F-02 — CRITICAL — Three branches set the schema-version constant to `8`; the merged value is `10`

| File | `main` | autonomy | source-expansion | observability | **merged truth** |
|---|---|---|---|---|---|
| `tests/test_db_migrations.py:19` | `EXPECTED_VERSION = 7` | `= 8` | `= 8` | `= 8` | **`= 10`** |
| `tests/test_cli_db_status.py:17` | `assert payload["version"] == 7` | `== 8` | `== 8` | `== 8` | **`== 10`** |

This is a guaranteed `uv run pytest` failure. Merging any two of the three branches produces a test that asserts `8` while `PRAGMA user_version` is `9` or `10`. It is the most likely first failure the integration agent will hit, and it will look like a merge bug rather than a merge conflict.

The `EXPECTED_TABLES` set in the same file is the good news: each branch adds its own table names to the same set literal, so those merge additively. The table count goes **45 → 53**.

**Fix:** set both constants to `10` after renumbering, and merge the three table-name additions into one set.

---

### F-03 — HIGH — The Qdrant branch writes outbox tasks that nothing consumes

This is the most consequential architectural gap in Wave 3.

`src/product_hunter/db/store.py` on `wave3/qdrant-semantic` adds `_enqueue_index_projection()`, which calls the **existing** `enqueue_projection()` from `product_hunter.db.outbox` with `target="qdrant"`, `entity_type="run"`, and a deterministic `idempotency_key` of `semantic_index:{run_id}:{sha256(entity_ids)}`. It runs inside the same `BEGIN IMMEDIATE` as the manifest import. That is exactly right per spec §15A.

The consumer side does not exist:

- The only outbox drain in the whole tree is `src/product_hunter/cli.py:1349` → `process_outbox(conn, {"obsidian": handle}, f"ph-project-{os.getpid()}", target="obsidian", max_tasks=100)`. It filters by `target`, so `qdrant` tasks are never claimed.
- `src/product_hunter/runtime/worker.py` never touches the outbox — a grep for `outbox|projection|process_outbox` across `src/product_hunter/runtime/*.py` returns nothing. The supervised worker does not drain projections at all.
- `src/product_hunter/semantic/index.py` `SemanticIndex.sync()` reconciles the index by reading the store directly. It never reads `projection_tasks`.

Consequences:

1. Every imported run leaves a `pending` `qdrant` row in `projection_tasks` that nothing will ever claim. `ph db outbox` shows a permanently growing backlog that is not a backlog — it is a set of orphaned rows.
2. `projection_tasks` grows without bound.
3. If any future drain is ever written with `target=None` (the default), `process_outbox` will claim these tasks, find no handler for `target='qdrant'`, and **fail them** with `no handler for target 'qdrant'` (see `db/outbox.py`, `process_outbox`'s `handler is None` branch). That converts a silent leak into a visible failure of an unrelated feature.

The branch's own report is honest about it — `docs/reasonix/reports/wave-3-qdrant.md:215-219` and `:246-247`:

> "the worker that calls `build_provider` and feeds the index still has to be wired, the same way the Obsidian projection's worker handler is wired."

**Fix:** register a `qdrant` handler in the projection handler map and drive it from the worker, or drop `target="qdrant"` from the enqueue and make `index sync` the only path. Do not leave it half-wired.

---

### F-04 — HIGH — Four of the eight waves ship code that is wired to nothing

**SubQ.** `src/product_hunter/llm/decision.py` (900 lines) and `src/product_hunter/llm/subq.py` (1126 lines) provide a complete decision-provider chain, and `build_decision_router()` is exported from `src/product_hunter/llm/__init__.py`. No production code calls it. The branch's report says it plainly (`docs/reasonix/reports/wave-3-subq.md:268-270`):

> "No caller of the provider exists in the tree, so it gates nothing and persists nothing. A table for it arrives with the wave that owns the migration."

Spec DoD #10 ("It can use SubQ for high-volume structured decisions where appropriate") is **not** satisfied by the merged tree. The branch also notes that §38A asks for a SubQ-vs-cheap-model benchmark before SubQ is promoted into the default path, and that benchmark was not run.

**Qdrant.** See F-03.

**Autonomy.** `src/product_hunter/autonomy/__main__.py` registers exactly one handler — the upkeep handler — and loads any others from the `PH_WORKER_HANDLERS` environment variable via `runtime/worker.py:load_handlers()`. No shipped handler module exists for the stages the cadence names (`COLLECT`, `PROCESS`, `CLUSTER`, `INVESTIGATE`, `BRIEF`). The branch's report (`wave-3-autonomous-operation.md:308-310`):

> "The collection handler is not wired into the shipped entry point, so a real run with handlers named still needs the operator to set `PH_WORKER_HANDLERS`."

So `python -m product_hunter.autonomy` runs a scheduler that enqueues the five §42 beats, then defers every one of them because no handler claims the stage. The 24/7 loop is structurally present and operationally inert.

**Scraping ladder.** The browser, crawler and managed rungs have the same problem: `CollectionOrchestrator` takes a `tools` mapping that nothing ever passes, so the ladder is always built with no drivers. That is F-27, and it is listed here because it belongs to the same class of defect — code that is complete, tested, and unreachable.

**Fix:** decide per feature whether the integration wave wires it or the branch is merged as a dormant capability with that stated in the ARCHITECTURE.md. What must not happen is merging it as if it were live.

---

### F-05 — HIGH — `config.py`: `_first_config_error` signature conflict (merges cleanly, fails at runtime)

- `wave3/autonomous-operation` changes `src/product_hunter/config.py`: `def _first_config_error(exc: ValidationError) -> str:` becomes `def _first_config_error(exc: ValidationError, label: str) -> str:`, and updates the one existing call site in `load_clustering_config()` to `_first_config_error(exc, "clustering config")`.
- `wave3/qdrant-semantic` keeps the one-argument form and adds a **new** call site in `load_semantic_config()`: `f"{_first_config_error(exc)}"`.

Git merges these two files without a conflict (different regions). The merged code raises `TypeError: _first_config_error() missing 1 required positional argument: 'label'` the first time `config/semantic.yaml` fails to validate — i.e. on a typo in a config file, at the worst possible moment.

**Fix:** give `label` a default (`label: str = "config"`) or update the qdrant call site. The default is cheaper and prevents the next branch from repeating the break.

---

### F-06 — HIGH — `db/connection.py`: two branches append to the same tail, and solved the same problem twice

- `wave3/autonomous-operation` appends `transaction()` — a savepoint-aware context manager — to the end of `src/product_hunter/db/connection.py`, with `from contextlib import contextmanager` and `from typing import Iterator` placed **after** the last function definition instead of at the top of the file.
- `wave3/opencode-control-plane` appends `is_busy()` and `with_busy_retry()` to the **same end of the same file**, and rewrites the file's top import block to match.

Both appends land on the same EOF lines → guaranteed textual conflict. Both also add import blocks to the same top region → a second conflict.

Semantically the two are complementary but inconsistent: `transaction()` issues a bare `BEGIN IMMEDIATE` with no busy handling, while `with_busy_retry()` exists precisely to wait out a writer holding the file. The merged code must compose them, or the autonomy engine's transactions will fail with `database is locked` under exactly the concurrency the autonomy engine creates (it has a `max_concurrent_jobs` of 4 and a heartbeat thread).

A related behavioural change rides along: `src/product_hunter/evidence.py` on the autonomy branch deletes its local `_transaction()` context manager and switches five call sites to the shared `transaction()`. The local one did not use savepoints; the shared one does. A nested failure now rolls back to a savepoint instead of propagating out of the caller's transaction. That is an improvement, but it is a behaviour change to the evidence write path and belongs in the integration notes.

**Fix:** hoist both import blocks to the top of `db/connection.py`, place `transaction`, `is_busy`, `with_busy_retry` as siblings, and wrap `transaction()`'s `BEGIN IMMEDIATE` in `with_busy_retry()`.

---

### F-07 — HIGH — `runtime/worker.py` is rewritten by two branches with opposite intents

- **autonomy** adds `import importlib`, adds `aging_seconds` / `aging_cap` to `WorkerRuntime.__init__` and `run_once`, and inserts a new `load_handlers(spec)` at the end of the file.
- **opencode-control-plane** deletes `_BUSY_ATTEMPTS`, `_BUSY_DELAY_SECONDS`, `_with_busy_retry`, `_is_busy`, `_redact`, `_LABELLED_SECRET` and `_MAX_ERROR_CHARS` from this file, moves the busy-retry logic to `db/connection.py` and the redaction logic to a new `product_hunter/redaction.py`, and rewrites the import block.

Autonomy's `load_handlers` insertion point (`@@ -441,6 +452,24 @@`) is exactly where opencode-control-plane deletes the redaction and busy-retry helpers. Both also touch `WorkerRuntime.__init__`. Two conflict hunks, one of them in a region that is being rewritten rather than extended.

**Fix:** resolve in this order — take opencode-control-plane's removals and the new `product_hunter/redaction.py`, then re-apply autonomy's `aging_seconds` / `aging_cap` parameters and `load_handlers` on top of the resulting file. Do not try to interleave the two diffs.

---

### F-08 — HIGH — Private-symbol coupling across branch boundaries

Two places where one branch depends on a *private* symbol of another:

1. `src/product_hunter/autonomy/engine.py:55` — `from product_hunter.runtime.worker import Handler, WorkerRuntime, _safe_error`
2. `src/product_hunter/autonomy/health.py:20` — `from product_hunter.runtime.worker import _safe_error`

`wave3/opencode-control-plane` replaces `_safe_error`'s body with `return redaction.safe_error(exc)`. The name survives, so the import keeps resolving — but the whole point of the control-plane branch was to move that rule to one place, and the control plane's own report item 4 asks for masking "at the source too". Keeping a private cross-package import of the old location defeats that and leaves a landmine for the next branch that deletes the wrapper.

A related smell, in the same family: `src/product_hunter/autonomy/engine.py:48` imports `utcnow` from `product_hunter.fetch` — the HTTP fetch module — while line 39 imports `utc_now` from `product_hunter.db.connection`. Both are clocks; `db/connection.py:utc_now` wraps `fetch.utcnow`. The same import appears in `src/product_hunter/observability/comparison.py:27`. And `src/product_hunter/db/connection.py:11` itself imports `from product_hunter.fetch import utcnow` — so the **core** database module depends on the HTTP module for a clock, and `wave3/scraping-ladder` rewrites `fetch.py`.

**Fix:** use `db.connection.utc_now` everywhere, and point the autonomy branch at `product_hunter.redaction.safe_error` instead of `runtime.worker._safe_error`.

---

### F-09 — HIGH — `sources.py` is rewritten by one branch while two others depend on its symbols

`wave3/source-expansion` rewrites `src/product_hunter/sources.py` (+294 / −13):

- `SourceConfig.source_type` changes from `str` to a `SourceType` enum (`community`, `forum`, `code_host`, `publication`, `review_site`, `support`, `mailing_list`, `aggregator`, `other`).
- `SourceConfig.enabled` changes from a **pydantic field** to a **derived property** computed from a new `status: SourceStatus` field.
- `SourceRegistry.require()` gains a new `admission()` path that enforces `effective_status()` and `policy_gaps()`.

Two other branches depend on that module:

- `wave3/scraping-ladder`: `collection/adapters/base.py` imports `AllowedUse, SourceConfig, SourceRegistry`; `collection/adapters/routing.py` imports `Collector, SourceConfig`; `collection/ladder.py` imports `SourceBlocked, SourceConfig`.
- `wave3/autonomous-operation`: `autonomy/engine.py:57` imports `load_registry` from `product_hunter.sources`.

All the symbols survive the rewrite (verified individually), so the import surface is intact. The risk is behavioural, not lexical:

- `enabled` becomes a **`@computed_field`** (`sources.py:236`), so `model_dump()` *does* still emit it — but it is now a derived output rather than an input, and the value written to the `sources` table comes from `status` rather than from the file. The `_consume_enabled` before-validator (`sources.py:207`) turns the legacy `enabled` key into a `status` and **refuses** a file where the two disagree. A hand-edited YAML that sets `enabled: false` with `status: active` is now a load error, not a silent override.
- `source_type` as an enum means `str(source.source_type)` renders as `SourceType.community`, not `community`, on Python 3.12+. `db/store.py` was correctly patched to `source.source_type.value` by the source-expansion branch, but **no other branch was patched**, because no other branch touched `source_type`.
- `require()` now fails closed on an expired review. That is the intent, and `collection/orchestrator.py` correctly gained the `review_expired` terminal reason in both `TERMINAL_REASONS` and `NO_CAPTURE_REASONS` — but the failure mode now applies to code the source-expansion branch never ran (`fetch.py:310` and `collection/orchestrator.py:265` both call `registry.require(...)`).

**Fix:** merge `sources.py` first, then re-run the other two branches' tests against the merged `sources.py`. The `enabled` field→property change needs an explicit `model_dump()` check.

---

### F-10 — MEDIUM/HIGH — `cli.py` is rewritten by four branches

`src/product_hunter/cli.py` is 1710 lines on `main` and is edited by four branches:

| Branch | Size | Adds |
|---|---|---|
| `wave3/autonomous-operation` | +57 | `ph autonomy status` |
| `wave3/source-expansion` | +104 | `ph source-candidates list\|discover` |
| `wave3/observability-evaluation` | +169 | `ph observe metrics\|trace`, plus `--db` on the existing `ph bench compare` |
| `wave3/qdrant-semantic` | +304 | `ph index sync\|rebuild\|search\|status`, plus a rewritten `doctor()` |

**No argparse name collides** — `autonomy`, `source-candidates`, `observe`, `index` are all new. But all four insert into the same `build_parser()` function (~40 lines apart) and all four add imports to the same top block, and all four add an `if args.command == "..."` branch to the same `main()` dispatch chain. That is two four-way conflict hunks minimum, plus the import block.

Two further issues in the same file:

1. **The observability branch widens six `except (OSError, ValueError)` clauses to `except Exception`** in **pre-existing** code paths: `models_command` (line 334), `extract_command` (lines 491 and 501), `eval_command` (line 742), `db_import_run_command` (line 1257), and `_read_model_catalog` (line 1673). A `TypeError`, `KeyError`, `AttributeError` or `sqlite3.Error` that previously surfaced as a traceback is now swallowed and reported as `{"ok": false, "error": ...}` with exit code 1. This is the most invasive change in that branch and its report does not mention it. The right fix is to add the specific new exception types the feature introduces, not `Exception`.
2. **The `--db` vs `--path` flag spelling is inconsistent** across branches for the same `dest="db_path"`: autonomy uses `--path` (matching the `db` group), observability and qdrant use `--db` (matching the `project` group). Cosmetic, but an operator will hit it.

**Fix:** pick one spelling for the database path flag across the four new command groups, and revert the six widened `except` clauses to explicit tuples.

---

### F-11 — MEDIUM/HIGH — Two embedding models, two config files, one shared pydantic class

| File | `embedding:` block |
|---|---|
| `config/clustering.yaml` (Wave 2) | `provider: local`, `model: sentence-transformers/all-MiniLM-L6-v2`, `dimensions: 384` |
| `config/semantic.yaml` (Wave 3, qdrant) | `provider: bge-m3-local`, `model: BAAI/bge-m3`, `dimensions: 1024` |

Both use the same `EmbeddingConfig` class in `src/product_hunter/config.py` — the qdrant branch extended it in place (adding `revision`, `cache_dir`, `device`, `batch_size`, `normalize` and a `_check_device` validator) rather than forking it, which is the right call. But there are now two files that name an embedding model, two vector spaces of different widths in one repository, and two different `provider:` vocabularies (`local` vs `bge-m3-local`) for what is conceptually the same role.

**`sentence-transformers` is not declared anywhere in `pyproject.toml`.** It is not in `dependencies` and it is not in the new `embeddings` extra. `config/clustering.yaml` names a `sentence-transformers` model, and `clustering/similarity.py:144` imports it lazily — so the Wave 2 clustering wave has had an undeclared dependency all along, and the qdrant branch's new extra (`flagembedding`, `huggingface-hub`, `torch`) does not close the gap. Spec §36 lists `sentence-transformers` as a core library.

**Fix:** declare `sentence-transformers` (or document the clustering wave as requiring the `embeddings` extra), and state in `ARCHITECTURE.md` which space the clustering wave uses and which the index uses.

---

### F-12 — MEDIUM/HIGH — `pyproject.toml` / `uv.lock`: two branches, one unmergeable lockfile

- `wave3/duckdb-analytics` adds `duckdb>=2.0.0` to **hard** `project.dependencies` (+1 line). The lock resolves it to `duckdb 2.0.0` (uploaded 2026-10-21, sdist plus 8 wheels including `win_amd64`).
- `wave3/qdrant-semantic` adds an `[project.optional-dependencies] embeddings` extra (+7 lines): `flagembedding>=1.3.0,<2`, `huggingface-hub>=0.23,<1`, `torch>=2.2`.

The `uv.lock` delta is the problem: duckdb adds **17 lines**, qdrant adds **1788 lines** — the full torch/CUDA resolution graph (`accelerate`, `cuda-bindings`, `cuda-pathfinder`, `cuda-toolkit`, `datasets`, `ir-datasets`, `multiprocess`, `numpy`, `nvidia-cublas`, and more). `uv.lock` records the resolution for **all** extras, so after the merge every `uv sync` resolves the CUDA dependency graph even for a user who never installs the extra.

Two lockfiles cannot be merged. The lock must be regenerated once, after both `pyproject.toml` edits land. `torch>=2.2` is unbounded and the project requires `python>=3.14`; the current lock pins a working resolution, but a re-lock on a different index could fail.

Heavy imports are correctly lazy: `semantic/providers.py:600` imports `FlagEmbedding` inside `_default_model_factory`, `providers.py:619` imports `torch` inside `_default_cuda_available`, `providers.py:583` imports `huggingface_hub` inside `_default_downloader`. `analytics/ingest.py:21` and `analytics/views.py:18` import `duckdb` at module level — acceptable because duckdb is a hard dependency, but see F-13.

**Fix:** land both `pyproject.toml` edits, then run one `uv lock` / `uv sync` to regenerate. Do not attempt to merge the two lockfile diffs.

---

### F-13 — MEDIUM — The DuckDB analytics layer has no entry point

`src/product_hunter/analytics/` is 1554 lines across `__init__.py`, `ingest.py`, `queries.py`, `tables.py`, `views.py`. `git grep analytics -- src/product_hunter/cli.py` on `wave3/duckdb-analytics` returns **nothing**. The layer is reachable only by `import product_hunter.analytics` from a REPL or a script. Meanwhile `duckdb` is a hard dependency that every install pays for.

Spec §16 calls DuckDB a "required verification and analytics layer" and §38A puts it in a later phase, so shipping it unwired is defensible — but it must be stated, not implied. The branch's own report says (§10, "The integration risks, honestly") that it "does not touch the Obsidian projection" and lists the wave as the DuckDB adapter; it does not say the layer is unreachable.

The DuckDB sync is also not driven by the outbox. `analytics/ingest.py:33` reads authoritative SQLite through `product_hunter.db.connection.connect` — correct per §15A ("DuckDB reads authoritative SQLite state") — but nothing reconciles DuckDB back, and no `projection_tasks` row records that DuckDB is behind. A silent drift between SQLite and DuckDB is currently undetectable.

**Fix:** add a `ph analytics sync` command and a `ph analytics <query>` command, and either enqueue a `duckdb` projection task or document that DuckDB is a pull-only analytical surface.

---

### F-14 — MEDIUM — Two routing abstractions and two provider registries

| | Text generation | Structured decisions |
|---|---|---|
| Router | `src/product_hunter/llm/routing.py` → `ModelRouter`, `ModelSelection`, `ModelRole` | `src/product_hunter/llm/decision.py` → `DecisionProvider`, `DecisionRouter`, `TextDecisionProvider` (new, subq) |
| Config | `config/model_routing.yaml` | `config/decision_routing.yaml` (new) |
| Registry | `llm/__init__.py:_PROVIDERS` | `llm/__init__.py:_DECISION_PROVIDERS = ("subq", "text")` |
| Persisted | `db/model_routing.py` | nothing |

The `text` decision provider resolves its model role through the **existing** `ModelRouter` (`build_decision_router(..., generator_for_role=...)`), so the two are compatible and the subq branch reused rather than forked. That is good.

What is duplicated is the vocabulary and the plumbing: two routing config files, two provider registries, two provider-name vocabularies, and the provider name `"text"` is written in **three** places — `llm/__init__.py:_DECISION_PROVIDERS`, `config.py:DecisionRoutingConfig._check_chain`, and `config/decision_routing.yaml`. Adding a third decision provider means editing all three.

**Fix:** make `config/decision_routing.yaml` the single source for provider names, and have `llm/__init__.py` validate against it rather than carrying its own tuple.

---

### F-15 — MEDIUM — `research_runs` progress is never written by any branch

Spec §43 and DoD #28 require the interface to show current stage, progress, last activity, evidence count and error state for each research run.

`research_runs.current_stage`, `current_step`, `progress_percent` and `last_heartbeat` are set **only** by the `INSERT` in `src/product_hunter/db/store.py:1007-1011`, from the values the create request carried. There is **no** `UPDATE research_runs SET current_stage ...` anywhere in `main` or in any of the eight branches.

`wave3/opencode-control-plane` lists this as its integration requirement #1:

> "The worker must write the run's progress. Nothing in the repository writes `research_runs.current_stage`, `current_step`, `progress_percent` or `last_heartbeat` after the create, so the status shows the values the request carried. ... the change to the code is the worker's."

`wave3/autonomous-operation` owns the worker and **does not** satisfy it — its `current_stage` references are to the new `worker_health` table, not `research_runs`. The two branches do not meet.

The same applies to requirements #2 and #3 of the control plane: the autonomy worker never reads `research_runs.status`, so a pause issued against a live worker is not honoured, and a paused or cancelled run's work is not prevented from being requeued.

**Fix:** this is the single most valuable piece of glue the integration wave can add, because it closes three of the control plane's six stated integration requirements at once.

---

### F-16 — MEDIUM — The OpenCode plugin tool result contract changed from `output` to `content`

`wave3/opencode-control-plane` modifies `.opencode/plugins/product-hunter.ts`:

- removes `output: { type: "string" }` from the tool schema, and
- changes every `return { output: ... }` to `return { content: ... }`.

No other branch touches `.opencode/`, so there is no merge conflict. But this is a **breaking change to a shared interface**: the OpenCode runtime and any consumer of the `product_hunter` tool must accept `content` instead of `output`. The branch's own test (`tests/test_plugin_control_plane.py`) checks the action list, the argument vector, the equals form and the absence of a shell — it does **not** assert the result key, and `main`'s `tests/test_plugin_project_root.py` does not assert it either. **No test will catch a regression here.**

**Fix:** verify the `content` key against the installed OpenCode version before shipping, and add a test that asserts the tool's declared output shape.

---

### F-17 — MEDIUM — `Collector.crawler` is outside the spec's vocabulary, and `fallbacks` is a new registry field

Spec §7 fixes the collector vocabulary: `collector: api | rss | http | browser | managed`.

`wave3/scraping-ladder` adds `crawler = "crawler"` to the `Collector` enum in `src/product_hunter/sources.py:42` and adds `SourceConfig.fallbacks: list[Collector]` — the escalation ladder. The `sources` table's `collector` column is plain `TEXT` with no CHECK constraint, so it persists cleanly, and the enum addition is purely additive so existing YAML still loads.

But the vocabulary is now wider than the spec's, and the spec file is not updated to match. `fallbacks` is a genuinely new registry concept not in §7's required field list, and it is only meaningful alongside the new `collection/adapters/routing.py`.

**Fix:** update spec §7 to include `crawler` and the `fallbacks` field, or map `crawler` onto an existing value.

---

### F-18 — MEDIUM — The registry's new review state is not persisted

`wave3/source-expansion` adds `status`, `review_expires_at`, `last_reviewed_by`, `metadata`, `query_capabilities` and `collection` to `SourceConfig`. `db/migrations/0008_source_expansion.sql` creates only `source_candidates` and `source_reviews` — it adds **no** `ALTER TABLE sources`. The `sources` table therefore keeps only the derived `enabled` integer; every other new field is file-only.

Two consequences:

- The registry's review state cannot be queried from the database, only re-derived from the YAML. `source_reviews` holds the decision *history*; the *current* state is not in the DB.
- `config/sources.yaml` changed `source_type: code host` → `code_host`. `db/store.py:_upsert_source` is an upsert on `source_id`, so no stale duplicate row survives — but any existing development database synced before the change still holds `code host` until the next `sync_registry`.

**Fix:** either add the columns to the `sources` table in the renumbered migration, or state plainly in `ARCHITECTURE.md` that the reviewed registry is a file and the database holds only the derived flag.

---

### F-19 — MEDIUM — `default_schedules()` now depends on a file, and creates a two-way package dependency

`wave3/autonomous-operation` replaces the body of `src/product_hunter/db/scheduler.py:default_schedules()` — five hard-coded `Schedule(...)` literals — with:

```python
from product_hunter.autonomy.cadence import load_cadence_schedules
return load_cadence_schedules()
```

The docstring claims this "keeps the dependency one way". It does not. At package level the dependency is **two-way**:

```
product_hunter.db.scheduler  →  product_hunter.autonomy.cadence  →  product_hunter.autonomy.maintenance  →  product_hunter.db.scheduler
```

It resolves today only because the `db.scheduler → autonomy.cadence` edge is a function-level import. Any future top-level import of `autonomy.cadence` in `db/scheduler.py` turns this into a hard import failure. The cycle is real; only the *call* direction is one-way.

Second consequence: `default_schedules()` now raises `CadenceError` when `config/cadence.yaml` is missing or malformed, where it previously always returned a list. Every caller must now handle a configuration fault. `config/cadence.yaml` is a new file the branch adds, so it ships with the branch — but a user who deletes it gets a stack trace, not a default.

**Fix:** move the cadence reader out of `product_hunter.autonomy` into a neutral module (or into `db/scheduler.py` itself) so the dependency is genuinely one-way, and give `default_schedules()` a documented fallback.

---

### F-20 — MEDIUM — The autonomy branch has no adversarial review, and its report's file inventory is wrong

`docs/reasonix/reports/wave-3-autonomous-operation.md:303-307`:

> "The independent adversarial review (stage 7) did not run, because both free review models were unavailable. The harden pass served as the security and observability check and returned a clean verdict."

The branch was committed with the gate waived (report lines 142-153, 181-186). It is green but **unreviewed**, and the integrator should carry that risk explicitly.

The same report's "New files" table (lines 51-76) lists 22 files while claiming 26, omits `tests/test_autonomy_main.py` entirely, and **12 of its 17 line counts are wrong** — for example `engine.py` is claimed at 663 lines and is 757; `tests/test_autonomy_cadence.py` is claimed at 117 and is 263. The observability report is materially more accurate (its 20 metric families, 12 task families, 36 dataset cases and every source line count check out exactly), but its test-line figure is low by 359 lines and its test inventory omits six modified test files.

**Fix:** re-review the autonomy branch, or at minimum re-read its diff with the report's numbers discarded.

---

### F-21 — LOW/MEDIUM — The docs root is `reasonix`, not `resonix`

Every path in the repository, all 8 branches, and the 194 tracked files under `docs/` on `main` use `docs/reasonix/`. No branch contains a `docs/resonix/` path. The adapter's `docsRoot` is `docs/`, so both spellings are "inside" the declared root, but only one matches the tree.

`wave3/source-expansion` hit this exact collision and recorded it (`docs/reasonix/reports/wave-3-source-expansion.md:24-27`):

> "The deliverable path in the task (`docs/resonix/reports/...`) does not exist in this repository; the docs root is `docs/reasonix/`. This report is written there."

This report was written to the path the task named, `docs/resonix/reports/final-audit-01-architecture.md`, and was subsequently moved to `docs/reasonix/reports/` — the correct root, and the same resolution the source-expansion branch chose. The sibling audit reports (`final-audit-02` … `final-audit-10`) also live under `docs/reasonix/reports/`.

**Fix:** none needed for this report. If the docs root is ever renamed, do it in one deliberate commit across all 198 files; do not leave a second spelling behind.

---

### F-22 — LOW/MEDIUM — `docs/reasonix/ARCHITECTURE.md` is edited by four branches and by none of the other four

Edited by `wave3/duckdb-analytics` (+21), `wave3/observability-evaluation` (+55), `wave3/subq-integration` (+18) and `wave3/qdrant-semantic` (48 lines of path rewrites). Not edited by `wave3/autonomous-operation`, `wave3/source-expansion`, `wave3/scraping-ladder` or `wave3/opencode-control-plane` — and the source-expansion report explicitly leaves it as a PRODUCTIONIZE task (lines 199-202).

The observability branch already rewrote the section title from "The forty-five tables" to "forty-six tables" and "eight migrations". The merged tree has **53 tables across 10 migrations**.

**Fix:** one deliberate rewrite of `ARCHITECTURE.md` after the merge, not four partial ones.

---

### F-23 — LOW/MEDIUM — `config/sources.yaml` is an adapter high-risk path edited by an agent

`.loop/adapter.json` lists `config/sources.yaml` under `highRiskPaths`. `wave3/source-expansion` rewrote it (+55 / −1), changing `source_type: code host` → `code_host` and adding `status`, `review_expires_at`, `last_reviewed_by`, `metadata`, `query_capabilities` and `collection` to each entry. `config/communities.yaml` is new.

The registry still holds exactly three sources and no source was newly enabled — the branch's report states this and it checks out. A human should read the diff before it lands; the branch asked for the same.

---

### F-24 — LOW — `.env.example` is edited by three branches

`wave3/scraping-ladder` (+11: `APIFY_TOKEN`, `APIFY_ACTOR_ID`), `wave3/subq-integration` (17 lines: `SUBQ_BASE_URL`, `SUBQ_MODEL`, and eleven `PH_SUBQ_*` overrides), `wave3/qdrant-semantic` (+5: `PH_QDRANT_URL`, `PH_QDRANT_COLLECTION`, `QDRANT_API_KEY`). The keys are disjoint, so the merge is a hand-edit of one file rather than a conflict. `.env` is a high-risk path per the adapter.

---

### F-25 — LOW — The eight worktrees live in five different places

| Location | Worktree | Branch |
|---|---|---|
| `product-hunter/.worktrees/wave3-opencode-control-plane` | inside the repo | `wave3/opencode-control-plane` |
| `product-hunter-w3a` | sibling | `wave3/autonomous-operation` |
| `product-hunter-w3c` | sibling | `wave3/source-expansion` |
| `product-hunter-wt/wave3-{analytics,observability,scraping-ladder}` | sibling dir | 3 branches |
| `C:\Users\anubh\.local\share\opencode\worktree\44767c\wave3\subq-integration` | outside the repo | `wave3/subq-integration` |
| `%TEMP%\opencode\wt-wave3-qdrant` | outside the repo | `wave3/qdrant-semantic` |

All eight are registered in `.git/worktrees`, so `git worktree list` sees them, but a clone elsewhere will not, and `git worktree prune` will drop the two outside the repo root. All eight branches fork from the same `00a6ca3`, so there is no rebase debt.

**No source file depends on a worktree path.** A `git grep` for `C:\Users\anubh` and `C:\Anubhav` across `*.py`, `*.ts`, `*.yaml` and `*.toml` on every branch returns **no** hits in code — every hit is inside `docs/`, in handoff prose. All 8 branches' test suites resolve their fixtures through `product_hunter.config.repo_root()` or `product_hunter.db.migrations.MIGRATIONS_DIR`, both of which point at the running checkout. **There are no worktree-only imports.**

---

### F-26 — MEDIUM — The source-expansion report contradicts itself about whether it was reviewed

`docs/reasonix/reports/wave-3-source-expansion.md` says both of the following:

- Lines 5–8: *"Stopped at stage 7 (REVIEW). The review node could not be dispatched on either rung of the free model chain, so **no review verdict exists and no commit was made.** The working tree on `wave3/source-expansion` holds all of the changes at the base commit `00a6ca3`."*
- Lines 62–65: *"7 REVIEW: complete on the third attempt (provider recovered after the model config update). Verdict: `ship`, no Must findings."* … *"9 SHIP: complete (commit `bd33b9ad9c53b9b5fd0be697414f933979521d00`)."*

The branch has **three** commits (`bd33b9a`, `f7a8641`, `a9540ee`); the report lists two and omits the third. Lines 235–238 then repeat the two hashes, so the "no commit was made" statement is false on its own document's evidence.

This matters for the integration because the report's "Stage 7" verdict is the evidence the integrator would rely on when deciding whether the branch is safe to merge. The code is present and the tests exist; what is in doubt is only the provenance of the verdict.

**Fix:** resolve the contradiction by re-running stage 7 on the branch, and correct the report. The same branch's other report claims also need checking — the file's own line counts for `source_policy.py` (claims 237, actual 290) and `test_source_policy.py` (claims 247, actual 300) match neither the total nor the non-blank count.

---

### F-27 — HIGH — The scraping ladder's new rungs are unreachable

`wave3/scraping-ladder` adds the browser, crawler and managed rungs — the whole point of the branch — by giving `CollectionOrchestrator.__init__` a `tools: dict[str, Driver | None]` parameter that defaults to none, and building the ladder with `build_adapters(...)`.

**No caller anywhere in the repository passes `tools=`.** A grep for `tools=` across `src/` and `tests/` on that branch returns nothing. The shipped orchestrator therefore always builds an adapter set with no drivers, and every source whose `collector` is `crawler`, `browser` or `managed` is refused with `collector_unavailable`.

The branch's own report acknowledges the injection design (`docs/reasonix/reports/wave-3-scraping-ladder.md:221-225` — "The drivers are injected, not found"), but frames it as a design choice rather than as the fact that the feature is inert. The report also contains a direct self-contradiction: line 208–210 claims the drivers "were installed and the two local drivers were run end to end against `https://hn.algolia.com`", while line 265–266 says "Nothing was contacted. No service was called and no success is claimed that did not happen in a test against a fake." Both cannot be true.

**Fix:** wire the drivers into the shipped orchestrator, or merge the branch as a dormant capability and say so in `ARCHITECTURE.md`. Same decision as F-03 and F-04.

---

### F-28 — MEDIUM/HIGH — The scraping ladder introduces a second fetch pipeline and a second result shape

`src/product_hunter/collection/adapters/base.py` `AdapterBase` re-implements the fetch sequence that `src/product_hunter/fetch.py` `Fetcher._fetch` already owns: the robots check (via the new `Fetcher.robots_allows`), URL admission, redirect admission, the retry ladder, the byte ceiling, the raw-store write, and the fetch-id derivation. It reuses `safety.admit_url` / `admit_redirect` and `fetch.RequestBudget`, so the *rules* are shared, but the *sequence* is written twice.

It also introduces a second result shape: `CollectionResult` (`adapters/base.py:89`) mirrors `fetch.FetchRecord` field for field, with `CollectionResult.from_fetch_record()` and `CollectionResult.fetch_record()` as two converters, and `fetch.py` still constructs `FetchRecord` independently.

And a second protocol: `collection/ladder.py::Collector` (Protocol) **and** `collection/adapters/base.py::Driver` (Protocol), plus `HttpAdapter` as a third spelling of main's deleted `HttpCollector`.

**Fix:** make `AdapterBase` delegate to `Fetcher` for the shared steps rather than re-implementing them, and make `CollectionResult` a view over `FetchRecord` rather than a parallel type.

---

### F-29 — MEDIUM — Three runtime dependencies of the new rungs are undeclared

`crawl4ai`, `playwright` and `apify-client` are imported by `collection/adapters/adapters.py` (lines 116–121, 226–228, 320–321, 441, 449) but **none of them appears in `pyproject.toml`** — not in `dependencies`, not in an extra. Spec §8 lists Crawl4AI, Playwright and Apify as part of the collection stack, so these are requirements the project has, declared nowhere.

The imports are correctly lazy (inside the driver methods), so a missing library degrades to `ToolUnavailable` rather than an import crash. But there is no way for an operator to install the rungs except by reading the source.

**Fix:** declare them as an optional extra (`[project.optional-dependencies] collection`), and document which rungs it enables.

---

### F-30 — HIGH — Two new admission reason codes are missing from the orchestrator's terminal and no-capture sets

`wave3/source-expansion` adds three new refusal reason codes to `SourceRegistry.admission()`: `review_expired` (`sources.py:405`), `incomplete_policy` (`sources.py:449`) and `robots_policy_missing` (`sources.py:457`).

`collection/orchestrator.py` on that branch adds only `review_expired`, to `TERMINAL_REASONS` (line 66) and `NO_CAPTURE_REASONS` (line 98). **`incomplete_policy` and `robots_policy_missing` are in neither set.** Two consequences follow directly from the orchestrator's own code:

- `orchestrator.py:502` — `attempted=reason not in NO_CAPTURE_REASONS`. A source refused for `incomplete_policy` or `robots_policy_missing` counts as an **attempted fetch** even though no request left the process. The source's request budget is charged for a request that never happened.
- `orchestrator.py:296` — `if item is None or item.reason_code not in TERMINAL_REASONS`. The refusal is **not terminal**, so the same source is re-attempted on every run, forever, until its budget runs out.

The merged tree needs all four new codes in both sets: `review_expired`, `incomplete_policy`, `robots_policy_missing`, and — from the scraping-ladder — `collector_unavailable`.

**Fix:** add the two missing codes to `TERMINAL_REASONS` and `NO_CAPTURE_REASONS`, and reconcile with the scraping-ladder's `collector_unavailable` in the same edit.

---

### F-31 — MEDIUM — Two `SourceReliability` classes and two `derive_reliability` functions in one package

- `src/product_hunter/opportunity/signals.py:43` — `class SourceReliability(str, Enum)` (the existing reliability tier).
- `src/product_hunter/source_policy.py:173` — `class SourceReliability` (a new dataclass, a different concept).

`source_policy.py:23-25` imports the enum aliased as `SourceReliabilityTier` precisely because the name is taken — so `from product_hunter.source_policy import SourceReliability` and `from product_hunter.opportunity.signals import SourceReliability` return two different things with the same name.

There is also a second `derive_reliability`: `src/product_hunter/evidence.py:143` `derive_reliability(conn, source_id) -> tuple[str, float]` and `src/product_hunter/source_policy.py:188` `derive_reliability(source, health, *, today) -> SourceReliability`. Different signatures, same name, same package.

**Fix:** rename the new dataclass to something unambiguous (`SourceReliabilityAssessment`) and rename or remove the second `derive_reliability`.

---

### F-32 — MEDIUM — The `code host` → `code_host` vocabulary change leaves an agent-facing file stale

`wave3/source-expansion` changes `config/sources.yaml` to `source_type: code_host` and adds the `SourceType` enum, and its report states (line 226): *"The source type vocabulary changed: `code host` became `code_host`. Nothing reads the old spelling."*

That is not true. Two files still carry the old spelling on that branch:

- `.opencode/skills/source-discovery/SKILL.md:15` — `| source_type | community, code host, publication |`. This is an **agent-facing instruction file**; an agent that reads it will look for `code host` in a registry that now requires `code_host`.
- `docs/reasonix/plans/phase-1-implementation-plan.md:157` — `| github_issues | code host | …`.

**Fix:** update both, or make the loader accept the legacy spelling during a deprecation window.

---

### F-33 — MEDIUM — `ARCHITECTURE.md` will contain a direct factual contradiction after the merge

Three statements in the same file, from three different branches, cannot all be true:

- `wave3/qdrant-semantic` (`ARCHITECTURE.md:706`): `| Qdrant projection | §14 | IMPLEMENTED as a library … DuckDB stays PLANNED |`
- `wave3/duckdb-analytics` (`ARCHITECTURE.md:386`): `**The DuckDB analytical layer.** IMPLEMENTED`
- `wave3/duckdb-analytics` (`ARCHITECTURE.md:689`): `| Qdrant and DuckDB projections | §14, §16 | PLANNED, full spec only, no wave spec |`

The observability branch also rewrites the heading from "The forty-five tables … across seven migrations" to "forty-six tables … across eight migrations", which is already wrong for the merged tree (see F-22: 53 tables, 10 migrations).

**Fix:** one deliberate rewrite of the whole file after the merge, not four partial ones.

---

### F-34 — MEDIUM — SubQ spend is never persisted, and the provider re-implements the retry ladder

`wave3/subq-integration` adds a complete spend accounting surface — `SubQSpend` (`llm/subq.py:224`), `SpendTracker` (Protocol, `llm/decision.py:340`), `DIMENSION_CALLS` / `DIMENSION_TOKENS` / `DIMENSION_USD` (`llm/subq.py:60-62`) — and it all stays **in memory**. The `spend_ledger` table exists (`db/migrations/0001_initial.sql`) and the `SpendTracker` protocol exists specifically to avoid an import cycle, but nothing writes a SubQ row to `spend_ledger`. Spec §34 requires token/cost usage tracking; the SubQ path records nothing durable.

Two duplications in the same branch:

- `llm/subq.py:67` defines its own `_RETRYABLE_STATUS` and `llm/subq.py:1072` its own `_retry_after`, mirroring `fetch.py:32` and `fetch.py:449`.
- `llm/decision.py:646` `_parse_answer` and `:672` `_strip_fence` re-implement the JSON-extraction-from-model-text pattern that `extraction.py` already owns. The branch's own report (lines 274–276) notes the same marker-escape pattern still exists in `extraction.py` and `clustering/adjudication.py` and was not fixed there.

**Fix:** route SubQ spend into `spend_ledger` when the provider is wired (F-04), and reuse the existing retry and JSON-extraction helpers rather than writing a third copy.

---

### F-35 — MEDIUM — The autonomy branch deletes checkpoint history, and its report does not say so

`src/product_hunter/autonomy/maintenance.py:141`:

```sql
DELETE FROM research_checkpoints WHERE created_at < ? AND run_id IN (
    SELECT run_id FROM research_runs WHERE completed_at IS NOT NULL)
```

The module docstring at line 5 says "It never deletes a job row" — technically true, but the branch **does** delete checkpoint history for every completed run, and the branch's report does not mention it anywhere. Checkpoints are the resume path (`autonomy/crash.py` uses `load_valid_checkpoint`), and spec §38B covers retention, backup and drift. A retention policy introduced silently in a wave whose report claims it only "keeps the store tidy" is a change the integrator must approve deliberately.

**Fix:** decide the retention window explicitly, record it in the report and in `ARCHITECTURE.md`, and make it configurable rather than hard-coded.

---

### F-36 — MEDIUM — Lease recovery runs twice per cycle, and the repository now has three worker loops

`recover_expired_leases` is called from **two** places inside the same new package: `autonomy/crash.py:52` and `autonomy/maintenance.py:58`. The engine calls `crash.recover()` on `recovery_interval_seconds` **and** `maintenance.run_maintenance()` on the upkeep interval, so the same recovery runs twice through two code paths, in two different orders, with two different checkpoint-validation behaviours.

Separately, the number of supervising loops in the repository is now three:

| Loop | Location | Status |
|---|---|---|
| `db/worker.py::Worker.run_forever` | `db/worker.py:149` | pre-existing on main; **no production caller** |
| `runtime/worker.py::WorkerRuntime.run_forever` | `runtime/worker.py:230` | the live one |
| `autonomy/engine.py::OperationEngine.run_forever` | new in the autonomy branch | wraps `WorkerRuntime.run_once` |

The spec §42 says "one supervised worker process with a single-instance lock". The *process* rule holds — `autonomy/__main__.py` and `runtime/__main__.py` both take `SingleInstanceLock(lock_path(root))`, so only one can run. But there are three loops and two recovery paths inside one package.

**Fix:** have the engine call one recovery path, and delete or explicitly deprecate `db/worker.py::Worker`, which has no caller.

---

### F-37 — LOW — Three smaller defects worth fixing in the same pass

1. **`import re` becomes dead** in `wave3/opencode-control-plane`'s `runtime/worker.py`. The branch removes `_LABELLED_SECRET` and `_redact` (both the only users of `re` in that file) but keeps `import re` at line 13.
2. **`AutonomyConfig` has no `config_version` property**, while `RoutingConfig` (`config.py:110-119`) and `ClusteringConfig` (`config.py:210-232`) both do. The `operation_runs` table has no config-version column either, so **a changed `config/autonomy.yaml` is not recorded anywhere** — the operation cannot be tied to the settings it ran under. Related: `_ENV_OVERRIDES` is not extended, so no autonomy setting is env-overridable, unlike `ExtractionConfig`.
3. **`cli.py` imports the autonomy engine at module level** on the autonomy branch (lines 18–19). `ph --help` now pulls `autonomy.engine → autonomy.budget → research.budget → research.contract → config → llm.routing → llm.discovery → llm.opencode`. The generic CLI now has a hard import dependency on an optional wave, and an import error anywhere in that tree breaks every `ph` command.

---

### F-38 — MEDIUM — The control plane's "every refusal leaves a trace" claim is false

`wave3/opencode-control-plane`'s report claims *"Every refusal leaves a trace. `RESEARCH_REQUEST_REFUSED` records the action, the code and the reason, in its own transaction, after the refused transaction has rolled back."*

`registry.py:_record_refusal` returns immediately when `action == "create"` or when no `research_id` was supplied. So a refusal on `create`, on `list`, on `doctor`, and on **any action that carried no research id** leaves no event. A separate `except UnknownResearch: return` path means a refusal naming a run that does not exist also leaves no trace.

The same report also claims *"the run id matches `^[A-Za-z0-9._-]+$`"*, but that pattern is `RUN_ID_PATTERN` in `cli.py:121` — the registry resolves ids by SQL lookup (`_resolve_row`), so the pattern is not enforced on the registry's own path, and the plugin does not validate it either. The run id is passed as `--research-id=value` on an argv list, so this is not a shell injection; the claim is simply overstated.

**Fix:** either widen `_record_refusal` to cover the no-id actions, or narrow the report's claim.

---

### F-39 — HIGH — `load_semantic_config()` never loads `.env`, so the Qdrant settings in `.env` are silently ignored

`src/product_hunter/config.py` on `wave3/qdrant-semantic` adds `load_semantic_config()` (lines 403–424). It reads the YAML, applies the env overrides through `_apply_semantic_env(raw)`, and validates. It never calls `load_dotenv`.

Main's other loaders do: `database_path()` calls `load_dotenv(base / ENV_FILE, override=False)` at line 246 and `load_config()` calls it at line 257. `load_subq_config()` on the SubQ branch also calls it explicitly, for the same reason.

The consequence is silent. `PH_QDRANT_URL`, `PH_QDRANT_COLLECTION` and `QDRANT_API_KEY` (all listed in the branch's `.env.example` addition) are read from `os.environ` only. If nothing else has loaded `.env` first, they are ignored and the defaults in `config/semantic.yaml` are used instead:

- `ph doctor` → `_semantic_diagnostics()` → `load_semantic_config()`. **`ph doctor` never loads `.env` at all**, so the doctor always reports the file's defaults.
- `ph index … --db PATH` → `_db_path()` short-circuits when the path is given, so `database_path()` — and therefore `load_dotenv` — is never called.

An operator who sets `QDRANT_API_KEY` in `.env` gets a silent connection failure against the wrong URL, with no error naming the cause.

**Fix:** call `load_dotenv(repo_root() / ENV_FILE, override=False)` at the top of `load_semantic_config()`, exactly as `load_config()` and `load_subq_config()` do.

---

### F-40 — HIGH — Two incompatible vector spaces target the same Qdrant collection

`opencode.json` (on `main`, modified by **no** Wave 3 branch) configures the Qdrant MCP server:

```json
"QDRANT_URL": "http://localhost:6333",
"COLLECTION_NAME": "product_hunter_evidence",
"EMBEDDING_MODEL": "sentence-transformers/all-MiniLM-L6-v2"
```

`config/semantic.yaml` on `wave3/qdrant-semantic` configures the index:

```yaml
collection: product_hunter_evidence
embedding:
  model: BAAI/bge-m3
  dimensions: 1024
```

**One collection name, two embedding models, two vector widths.** The MCP server will create the collection at 384 dimensions with the MiniLM space; `SemanticIndex.ensure_ready()` reads the collection's `metadata.space_id` and vector size and **refuses to write on mismatch**, raising `EmbeddingSpaceMismatch` (`semantic/index.py:175`). Whichever path runs first wins and the other errors — and the error names a space mismatch, not a configuration conflict.

This is the operational half of F-11: the repository now has two embedding models configured against the same collection name, and the spec §14 rule ("Changing the embedding model creates a re-embedding job rather than silently mixing vector spaces") is not enforced anywhere across the two.

**Fix:** give the index its own collection name (`product_hunter_semantic`) or update `opencode.json` to the same model. One collection, one vector space.

---

### F-41 — MEDIUM/HIGH — The DuckDB layer and the DuckDB MCP server share one file, and the layer drops tables it did not create

`opencode.json` also configures a DuckDB MCP server:

```json
"command": ["uvx", "--python", "3.13", "--with", "mcp<2", "mcp-server-duckdb@1.1.0", "--db-path", "db/product_hunter.duckdb"]
```

`analytics/__init__.py` defaults the analytics file to the same path (`db/product_hunter.duckdb`). Two problems follow:

1. **DuckDB takes a single-writer file lock.** The MCP server is a long-lived `uvx` process holding that file; the analytics layer opening the same file contends with it. The branch's own report (§10.7) admits the layer does not lock the file.
2. **`rebuild()` drops tables it did not create.** `analytics/ingest.py:653` `_drop_stale_tables(specs)` issues `DROP TABLE "{name}"` (line 664) for every DuckDB table that is not in the SQLite mirror, and `rebuild()` calls it with `drop_stale=True` (line 215). A table the MCP server created in that file is destroyed by a `ph analytics rebuild`.

**Fix:** point the analytics layer at its own file (`db/product_hunter.analytics.duckdb`), or make `_drop_stale_tables` only drop tables the layer itself created — it should track its own table names, not infer them from SQLite.

---

### F-42 — MEDIUM — The DuckDB views re-implement metrics that already exist in Python

`analytics/views.py` re-derives metrics that `main` already computes in Python:

| View | Python counterpart on main |
|---|---|
| `v_independence_summary` (views.py:226-309) | `independence/analytics.py::_metrics` (main L103-169) |
| `v_query_family_performance` (views.py:499-519) | `discovery/frontier.py::SaturationPolicy` (main L94-124) |
| `v_discovery_yield` (views.py:470-497) | the discovery-yield fields already persisted in `discovery/store.py`'s event payload |
| `v_run_budget` (views.py:408-445) | `research/budget.py::BudgetTracker` |

Two implementations of one metric, kept in sync by hand. The branch's own report (§4) records that the first version of `v_independence_summary` **disagreed** with the Python package and had to be hand-corrected to match its grouping — that is the cost of the duplication, already paid once.

Two aggravating details: the saturation thresholds are taken from the **class defaults** of `SaturationPolicy()` (views.py:28-29), not from an operator config, so a changed policy default silently changes the view; and the views hard-code table and column names for 20+ SQLite tables, so any schema change to those tables breaks `create_views()` inside the ingestion transaction.

**Fix:** decide which side owns each metric. Where Python already owns it, have the view read the persisted value rather than recomputing it in SQL. Where SQL owns it, delete the Python version.

---

### F-43 — MEDIUM — The semantic reader re-implements `db/clustering.py` with raw SQL

`semantic/source.py` issues its own SQL against `evidence`, `documents`, `pain_points`, `clusters`, `cluster_members` and `evidence_pain_links` (lines 123, 130, 135, 156, 194, 238, 275, 284) instead of using the readers `main` already has. Two of them are direct re-implementations:

- `semantic/source.py::_pain_points` (L190-232) re-implements `db/clustering.py::ClusterStore.pain_points()` (main L933-962) — the same `FROM pain_points p JOIN evidence e JOIN documents d WHERE p.accepted = 1 AND p.normalized_key IS NOT NULL` shape.
- `semantic/source.py::_maps` (L267) re-implements `ClusterStore.current_memberships()` (main L794).

This is the "raw SQL bypasses the store facade" pattern the rest of the codebase avoids: `db/store.py` is described as "a thin authoritative-store facade", and the convention is that a feature module reads through it.

**Fix:** have `semantic/source.py` call `ClusterStore` rather than re-deriving the joins. The reads stay read-only, which is correct; only the duplication needs to go.

---

### F-44 — MEDIUM — A second embedding protocol, and an annotation that does not match its return type

`semantic/providers.py:48` defines `EmbeddingProvider` as a Protocol — a **second** embedding abstraction beside `clustering/similarity.py::Embedder` (main L90). `semantic/embeddings.py:19` then declares:

```python
def build_embedder(config: SemanticConfig, *, root: Path | None = None) -> Embedder:
```

and returns a `build_provider(config, root=root)` — that is, an `EmbeddingProvider`, which is the **wider** type, not an `Embedder`. The annotation is wrong, and the mismatch is what lets the wider protocol grow unchecked.

The branch does reuse main well in places: `VectorSpace`, `centroid`, `EmbeddingUnavailable` and `MixedVectorSpaceError` all come from `clustering/similarity.py`, and `SentenceTransformerProvider` wraps main's `SentenceTransformerEmbedder` rather than reimplementing it. The problem is the extra protocol on top.

**Fix:** drop `EmbeddingProvider` and extend `Embedder` with the diagnostics and release methods the index actually needs, or make `Embedder` a supertype of `EmbeddingProvider` and say so.

---

### F-45 — MEDIUM — The whole `ph index` CLI surface is untested, and `--json` is registered and never read

No test calls `main(["index", …])`, `index_command()`, `_run_index_action()` or `_semantic_diagnostics()`. The four new subcommands and the doctor's new semantic block are exercised only through the underlying `SemanticIndex` methods.

The qdrant branch's report (§10, lines 203-205) states the opposite — *"The CLI success paths (`ph index sync`, `rebuild`, `search`, `status`) were exercised **through the tests**, not through the command line."* — and separately (§10, lines 208-209) that *"`ph doctor` was tested through the helper that builds the semantic block"*. Neither is true.

The same report's file inventory is stale on **11 of its 19 rows** (`semantic/__init__.py` 83 vs 86, `semantic/qdrant.py` 380 vs 397, `semantic/source.py` 346 vs 376, `semantic/index.py` 522 vs 554, `db/store.py` 1276 vs 1317, `cli.py` 1840 vs 2169, `.env.example` 20 vs 21, `ARCHITECTURE.md` 711 vs 731, `test_semantic_qdrant.py` 721 vs 736, `test_semantic_index.py` 689 vs 709, `test_db_store.py` 138 vs 258), and the commit message's "114 new tests" is 116 test functions.

Separately, `--json` is registered on all four `index` subparsers (cli.py:215, 222, 238, 245) and never read — the command always prints JSON.

**Fix:** add CLI-level tests for the four subcommands, remove the dead `--json` flag or honour it, and correct the report.

---

### F-46 — LOW/MEDIUM — The DuckDB path is configured outside `config.py`, and `PH_ANALYTICS_PATH` is undocumented

`analytics/__init__.py:50-64` `analytics_path()` re-implements the `PH_*`-env-var-with-`.env`-override pattern that lives in `config.py` (`database_path()`, lines 243-251). The new env var is read in exactly one place — but that one place is a **new** place, outside the house config module, which breaks the convention that `config.py` owns path resolution.

`PH_ANALYTICS_PATH` is also **not in `.env.example`** on any branch — the DuckDB branch adds no `.env.example` hunk at all, and the three branches that do edit that file add only their own keys.

**Fix:** move the path resolution into `config.py` next to `database_path()`, and add `PH_ANALYTICS_PATH=` to `.env.example`.

---

### F-47 — LOW — Small defects worth folding into the same pass

1. `analytics/ingest.py` places three imports (`product_hunter.analytics.views`, `product_hunter.db.connection`, `product_hunter.fetch`) **after** the executable `logger = logging.getLogger(__name__)` line — `E402` under ruff's default rules. Ruff is not a declared dev dependency, so nothing catches it.
2. `analytics/ingest.py:61` carries a dead comment marker: `# (AnalyticsError moved to tables.py)`.
3. `tests/test_db_checkpoints.py` (DuckDB branch only) changes the `_leased_job` fixture default from `lease_seconds=60` to `3600`. That is a **workaround, not a fix**: the underlying wall-clock fragility — a 60-second lease expiring mid-suite — remains for every other test that uses a short lease, and the DuckDB branch's own ~30 s of extra test time is what pushed it over the edge.
4. `semantic/source.py` reaches into raw column names; `semantic/index.py:22` imports the concrete `ProjectionTask` dataclass (public, but a wire type).
5. `tests/test_semantic_index.py:17` imports the underscore-private `_build_run` from `tests/conftest.py`, and `:37` imports `FakeQdrant` from a **sibling test module** that exists only on that branch. `tests/` has no `__init__.py`, so it resolves via pytest's rootdir path insertion — it works, but `test_semantic_index.py` is not runnable in isolation and it is a merge hazard if another branch adds a file with the same name.

---

## 3. Severity summary

| Severity | Count | IDs |
|---|---|---|
| CRITICAL | 2 | F-01, F-02 |
| HIGH | 13 | F-03 … F-10, F-27, F-30, F-39, F-40 |
| MEDIUM/HIGH | 4 | F-11, F-12, F-28, F-41 |
| MEDIUM | 19 | F-13 … F-20, F-22, F-23, F-26, F-29, F-31, F-32, F-33, F-34, F-35, F-36, F-38, F-42, F-43, F-44, F-45 |
| LOW/MEDIUM | 2 | F-21, F-46 |
| LOW | 4 | F-24, F-25, F-37, F-47 |

Forty-seven findings, of which two are deterministic blockers and thirteen are HIGH.

---

## 4. Exact files and components involved

**Migration and schema**
- `db/migrations/0008_autonomy.sql` (autonomy) · `0008_source_expansion.sql` (source-expansion) · `0008_observability.sql` (observability)
- `src/product_hunter/db/migrations.py:11,29,54-57,64-68` — `_MIGRATION_RE`, `migrate()`, duplicate and gap refusal
- `tests/test_db_migrations.py:19` — `EXPECTED_VERSION`; `EXPECTED_TABLES` (45 → 53)
- `tests/test_cli_db_status.py:17` — `assert payload["version"] == N`

**Shared Python modules touched by 2+ branches**

| File | Branches | Conflict |
|---|---|---|
| `src/product_hunter/cli.py` | autonomy, source-expansion, observability, qdrant | 4-way, 2 hunks + import block |
| `src/product_hunter/config.py` | autonomy, subq, qdrant | 3-way + F-05 semantic break |
| `src/product_hunter/sources.py` | autonomy, source-expansion, scraping-ladder | 3-way, F-09 |
| `src/product_hunter/db/connection.py` | autonomy, opencode-control-plane | EOF append + import block, F-06 |
| `src/product_hunter/runtime/worker.py` | autonomy, opencode-control-plane | 2 hunks, F-07 |
| `src/product_hunter/db/store.py` | source-expansion, qdrant | different regions — clean |
| `src/product_hunter/collection/orchestrator.py` | source-expansion, scraping-ladder | different regions — likely clean |
| `src/product_hunter/db/scheduler.py` | autonomy | F-19 |
| `src/product_hunter/db/jobs.py` | autonomy | clean — `claim_job` widened, defaults preserve behaviour |
| `src/product_hunter/evidence.py` | autonomy | clean — local `_transaction` removed |
| `src/product_hunter/runtime/__main__.py` | autonomy | clean — `_load_handlers`/`_lock_path` moved out |
| `src/product_hunter/runtime/lock.py` | autonomy | clean — gains `lock_path()` |
| `src/product_hunter/registry.py` | opencode-control-plane | clean — gains `doctor`/`recover` actions |
| `src/product_hunter/llm/{__init__,base}.py` | subq | clean |
| `src/product_hunter/eval/*` | observability | clean |
| `.env.example` | scraping-ladder, subq, qdrant | disjoint keys |
| `pyproject.toml` / `uv.lock` | duckdb, qdrant | lock must be regenerated |
| `docs/reasonix/ARCHITECTURE.md` | duckdb, observability, subq, qdrant | 4-way |

**New modules by branch**

| Branch | New packages / modules | Lines |
|---|---|---|
| autonomous-operation | `autonomy/` (9 files), `config/autonomy.yaml`, `config/cadence.yaml` | ~1900 + 92 |
| source-expansion | `communities.py`, `source_policy.py`, `source_expansion.py`, `config/communities.yaml` | ~1264 |
| duckdb-analytics | `analytics/` (5 files) | 1554 |
| observability-evaluation | `observability/` (5 files), `eval/datasets/ds-v2.json` | ~3332 + 835 |
| scraping-ladder | `collection/adapters/` (4 files) | 1216 |
| subq-integration | `llm/decision.py`, `llm/subq.py`, `config/decision_routing.yaml` | 2026 |
| qdrant-semantic | `semantic/` (6 files), `config/semantic.yaml` | 2178 |
| opencode-control-plane | `redaction.py`, 2 `.opencode/commands/*.md` | ~200 + plugin |

---

## 5. Cross-worktree conflicts

1. **Migration numbering** — three branches, one number. F-01.
2. **Schema-version constant** — three branches, one wrong value. F-02.
3. **`_first_config_error` signature** — autonomy takes two args, qdrant calls with one. Merges clean, fails at runtime. F-05.
4. **`db/connection.py` EOF** — autonomy appends `transaction()`, opencode appends `is_busy()`/`with_busy_retry()`; both add import blocks. F-06.
5. **`runtime/worker.py`** — autonomy inserts `load_handlers` exactly where opencode deletes the redaction/busy helpers; both touch `WorkerRuntime.__init__`. F-07.
6. **`sources.py`** — source-expansion changes `source_type` to an enum and `enabled` to a property; scraping-ladder adds `Collector.crawler` and `fallbacks` to the same class bodies; autonomy wraps `from_yaml` in the same file. F-09.
7. **`cli.py` `build_parser()` and `main()` dispatch** — four branches insert into the same two functions. F-10.
8. **`config.py` top block** — autonomy adds `field_validator` + `AUTONOMY_CONFIG_NAME`; qdrant adds `SEMANTIC_CONFIG_NAME` + `EMBEDDING_DEVICES`; subq adds `TYPE_CHECKING` + `DECISION_ROUTING_CONFIG_NAME`. Three-way.
9. **`config.py` env-override tables** — qdrant's `_SEMANTIC_ENV_OVERRIDES` and subq's `_SUBQ_ENV_OVERRIDES` are inserted at the same anchor (after `_INT_LIMITS`). Two-way.
10. **`config.py` model anchor** — autonomy's `AutonomyConfig` and qdrant's `SemanticConfig` are both inserted after `ClusteringConfig`. Two-way.
11. **`uv.lock`** — duckdb +17, qdrant +1788. Not mergeable; regenerate. F-12.
12. **`docs/reasonix/ARCHITECTURE.md`** — four branches, including a table-count rewrite that will be wrong again. F-22.
13. **`tests/test_db_migrations.py` and `tests/test_cli_db_status.py`** — three branches each add table names to the same set literal and each set the version to 8. F-02.
14. **Cross-branch semantic coupling** — `autonomy/engine.py:48` and `observability/comparison.py:27` import `utcnow` from `product_hunter.fetch`, and `db/connection.py:11` does too, while scraping-ladder rewrites `fetch.py`. `llm/subq.py:35` and `llm/decision.py:28` import `utcnow` from `product_hunter.llm.catalog` — a **third** home for the same helper. `autonomy/engine.py:55` and `autonomy/health.py:20` import the private `runtime.worker._safe_error`, which opencode-control-plane is rewriting. F-08.
15. **`collection/orchestrator.py` reason-code sets** — source-expansion adds `review_expired` to both `TERMINAL_REASONS` and `NO_CAPTURE_REASONS`; scraping-ladder adds `collector_unavailable` to `NO_CAPTURE_REASONS` and `timeout` / `empty_body` to `FAILED_REASONS`. Both branches edit `NO_CAPTURE_REASONS` at the same anchor, and the merged set must also carry source-expansion's two *omitted* codes `incomplete_policy` and `robots_policy_missing`. F-30.
16. **Config-level coupling** — `config/autonomy.yaml` names `hn_algolia` in `budget.per_source` and `source_pacing.per_source`; `config/sources.yaml` is rewritten by source-expansion. If `hn_algolia` is dropped or renamed, the autonomy config degrades silently (the pacer falls back to the default interval; nothing errors).
17. **Stale vocabulary in an agent-facing file** — `.opencode/skills/source-discovery/SKILL.md:15` still teaches `community, code host, publication` after the enum changed to `code_host`. F-32.

**No cross-worktree conflicts in module-level imports.** No branch imports another branch's new module.

---

## 6. Missing integration points

1. **A projection handler for `target="qdrant"`.** The task is enqueued; nothing consumes it. F-03.
2. **A `duckdb` entry point** — no CLI command reaches `product_hunter.analytics`. F-13.
3. **A caller for the SubQ decision router.** `build_decision_router()` has no production call site. F-04.
4. **Stage handlers for the autonomy operation.** `PH_WORKER_HANDLERS` is read but no handler module ships for `COLLECT` / `PROCESS` / `CLUSTER` / `INVESTIGATE` / `BRIEF`. F-04.
4b. **A caller for the scraping ladder's `tools=` injection point.** Without one, the browser, crawler and managed rungs never run. F-27.
4c. **A driver for `target="duckdb"`** — or an explicit statement that DuckDB is a pull-only analytical surface with no reconciliation signal. F-13.
5. **A worker that writes `research_runs` progress.** Closes control-plane requirements 1, 2 and 3. F-15.
6. **A `db → qdrant` and `db → duckdb` reconciliation signal.** Neither downstream store can report that it is behind.
7. **A `ph` command for the operation loop's own state beyond `autonomy status`** — no way to pause, resume, or inspect the budget ledger.
8. **A test that asserts the OpenCode plugin's result key.** F-16.
9. **An `ARCHITECTURE.md` that describes the merged system.** F-22.

---

## 7. Recommended fixes

**Before any merge (mechanical, deterministic)**
1. Renumber the three migrations to `0008` / `0009` / `0010`. Pick the order; the files are table-disjoint.
2. Set `EXPECTED_VERSION = 10` and `assert payload["version"] == 10`, and merge the eight new table names into `EXPECTED_TABLES`.
3. Re-run `uv lock` once, after both `pyproject.toml` edits are in place. Do not merge lockfile diffs.
4. Revert the six widened `except (OSError, ValueError)` → `except Exception` clauses in `cli.py` to explicit tuples.
5. Pick one spelling for the database-path flag (`--db` or `--path`) across the four new command groups.

**During the merge (semantic, needs a decision)**
6. Give `_first_config_error` a default label, or update the qdrant call site. Do not leave a two-arg/one-arg split.
7. Hoist both import blocks to the top of `db/connection.py`; compose `transaction()` with `with_busy_retry()`.
8. Resolve `runtime/worker.py` in the order given in F-07, not by interleaving diffs.
9. Merge `sources.py` first, then re-run the scraping-ladder and autonomy suites against it. Check `SourceConfig.model_dump()` for the lost `enabled` key.
10. Replace `runtime.worker._safe_error` with `product_hunter.redaction.safe_error` in the autonomy branch's two imports, and replace `fetch.utcnow` with `db.connection.utc_now` in the three places that use it.
11. Move the cadence reader out of `product_hunter.autonomy` so the `db.scheduler ↔ autonomy` dependency is genuinely one-way, and give `default_schedules()` a fallback.
12. Declare `sentence-transformers` in `pyproject.toml`, and state in `ARCHITECTURE.md` which vector space the clustering wave uses and which the index uses.

**After the merge (closing the gaps)**
13. Register a `qdrant` projection handler, or stop enqueuing `target="qdrant"` tasks. Do not leave it half-wired.
14. Add `ph analytics sync` and a query command, and enqueue a `duckdb` projection task or document DuckDB as pull-only.
15. Decide the fate of the SubQ decision router: wire it into a call site, or merge it as a dormant capability and say so in `ARCHITECTURE.md`.
16. Write the stage handlers the autonomy operation needs, or document that the operation currently defers every scheduled beat.
17. Add the worker-side write of `research_runs.current_stage` / `current_step` / `progress_percent` / `last_heartbeat`, and the pause/cancel check before a claim.
18. Rewrite `docs/reasonix/ARCHITECTURE.md` once: 53 tables, 10 migrations, five new module trees, four new command groups.
19. Move this report to `docs/reasonix/reports/` (F-21).

---

## 8. Spec requirements that remain unsatisfied after the merge

Measured against `docs/reasonix/specs/Product-Hunter-Full-Spec.md` §44 Definition of Done and the relevant sections.

| # | Requirement | Status after merge |
|---|---|---|
| 10 | "It can use SubQ for high-volume structured decisions where appropriate" | **UNSATISFIED.** No caller of the decision router exists. F-04. §38A also asks for a SubQ-vs-cheap-model benchmark before promotion; it was not run. |
| 16 | "It can track opportunity history and cluster lineage" | **AT RISK.** The qdrant branch introduces a second vector space (BGE-M3, 1024 dims) alongside the clustering wave's (MiniLM, 384 dims), and the index is not wired to the cluster path. §14 requires Qdrant checks "before a new problem cluster is created or a serious opportunity is promoted" — that check is not wired. F-03. |
| 34 | "The system records spend, tool provenance, evidence provenance, and checkpoint history" | **PARTIALLY UNSATISFIED.** SubQ spend is tracked in memory only and never reaches `spend_ledger` (F-34). Separately, the autonomy branch deletes `research_checkpoints` rows for completed runs without the retention window being specified or reported (F-35). |
| 20 | "It can run scheduled jobs without manual intervention" | **UNSATISFIED.** The cadence schedules exist and the scheduler enqueues them, but no stage handler ships, so every scheduled beat is deferred. F-04. |
| 20b | spec §8 — "Use the cheapest appropriate collection mechanism … Crawl4AI … Playwright MCP … Apify" | **UNSATISFIED.** The rungs exist as code but no caller passes `tools=`, so `crawler`, `browser` and `managed` sources are always refused with `collector_unavailable`. F-27, F-29. |
| 21 | "It can recover from failures, crashes, shutdowns, and stale workers" | **PARTIALLY UNSATISFIED.** The autonomy branch builds the recovery machinery, but it is unreviewed (F-20) and unwired. |
| 23 | "Cross-store projections converge through a transactional outbox" | **HALF SATISFIED.** The Qdrant write side is correct and atomic; the consume side does not exist, so it never converges. F-03. DuckDB has no outbox coupling at all. F-13. |
| 28 | "`/list-research` and cross-chat continuation operate from persistent state" | **PARTIALLY UNSATISFIED.** The control plane reads persistent state correctly, but `research_runs.current_stage`, `current_step`, `progress_percent` and `last_heartbeat` are never written, and a pause is not honoured by a live worker. F-15. |
| 31 | "The implementation phases in Section 38A have passed their stated acceptance criteria" | **NOT DEMONSTRABLE.** §38A puts DuckDB and Qdrant in later phases; the DuckDB layer has no entry point (F-13) and the Qdrant projection has no consumer (F-03). |
| 32 | "Source legality status, retention, backup/restore, parser-drift canaries, and budget enforcement are operationally verified" | **PARTIALLY UNSATISFIED.** The source-expansion branch adds a real legality and review-status model — but the review state is file-only and not queryable from the database (F-18), and `config/sources.yaml` was edited by an agent on a high-risk path without operational verification (F-23). |

**Satisfied and holding after the merge** — the following were re-verified and are *not* at risk:

- §14 Qdrant consistency: every vector stores source/entity ID, model, version, content hash and creation time; the space id is checked before upsert.
- §15 SQLite is the authoritative store: **no branch writes to SQLite from a downstream store.** DuckDB writes only to DuckDB; Qdrant writes only to Qdrant; Obsidian is untouched. Verified by grep across all eight branches.
- §15A transactional outbox: the Qdrant enqueue happens in the same SQLite transaction as the import, with a deterministic idempotency key. The write side is correct; only the consumer is missing (F-03).
- §15A DuckDB consistency: DuckDB reads authoritative SQLite state through `db.connection.connect` and never writes back.
- §15 "events is not a second generic event store": the observability branch adds `model_comparisons`, a distinct entity table, and explains in the migration header why `research_events` cannot hold it (it requires a `research_runs` foreign key). Not a violation.
- §17 one persistent SQLite job queue: **no branch forks the queue.** Autonomy extends `db/jobs.py:claim_job` with priority aging (off by default, byte-identical ORDER BY when off) and adds `peek_job` / `defer_queued_job`.
- §18 one supervised worker with a single-instance lock: **no branch forks the lock.** `autonomy/__main__.py` and `runtime/__main__.py` both take `SingleInstanceLock(lock_path(root))`, and `lock_path()` was relocated into `runtime/lock.py` precisely so they share it. They cannot run concurrently.
- §18 the SQLite job queue is the scheduler of record: the autonomy branch *extends* `db/scheduler.py` and reads the same `jobs` table. It does add a second long-running loop beside `WorkerRuntime.run_forever` (F-19) — the queues are one, the loops are two.
- §19 event system: three event surfaces now exist — `runtime/events.py` JSONL, the `research_events` table, and the new `operation_events` table. `operation_events` records operation transitions rather than domain events, so it is a third sink rather than a duplicate store.
- §36 Python stack: `pydantic`, `httpx`, `beautifulsoup4`, `lxml`, `PyYAML`, `python-dotenv` all declared. `sentence-transformers` is named by `config/clustering.yaml` but **not declared** (F-11). `duckdb` is a hard dependency, justified by §16. `torch` / `flagembedding` / `huggingface-hub` are an optional extra, correctly lazy-imported.
- §37 secrets outside committed source: `.env.example` carries names only. `SUBQ_API_KEY` is read from the environment and never written to a record. `QDRANT_API_KEY` is read through the `api_key_env` indirection, never stored in a config.
- §40 architectural ownership: OpenCode owns the interface, the worker owns execution, SQLite owns state, Qdrant/DuckDB/Obsidian are projections. No branch inverts this.

---

## 9. What the final integration agent MUST resolve

1. **Renumber the three `0008` migrations to `0008` / `0009` / `0010`** and set both schema-version constants to `10`. Without this, `ph db init` and `uv run pytest` both fail on the first try. F-01, F-02.
2. **Reconcile `config.py` as one file**, not three diffs. Three branches, one import block, one env-override anchor, one model anchor, and one helper whose signature two branches changed in opposite directions. F-05, F-08.
3. **Resolve `db/connection.py` and `runtime/worker.py` deliberately.** Two branches moved the busy-retry and redaction logic out of `runtime/worker.py` while the other was inserting into the same region. Take the moves, then re-apply the aging parameters and `load_handlers` on top. F-06, F-07.
4. **Merge `sources.py` first and re-validate the other two branches against it.** `source_type` became an enum, `enabled` became a property, and `require()` now fails closed on an expired review — a behaviour change that reaches `fetch.py` and `collection/orchestrator.py`, which the source-expansion branch never ran. F-09.
5. **Rebuild `cli.py` from the four branches' pieces** rather than resolving the diff: four `add_parser` blocks in one function, four dispatch branches in one chain, six widened `except` clauses to revert, one flag spelling to standardise. F-10.
6. **Regenerate `uv.lock` once** after both `pyproject.toml` edits land, and decide whether the torch/CUDA resolution graph belongs in a lockfile every user resolves. F-12.
7. **Wire the four inert features, or declare them inert.** The Qdrant projection handler, the DuckDB entry point, the SubQ decision router, and the scraping ladder's driver injection (`tools=`). A wave that is merged but unreachable is not a wave that is done. F-03, F-04, F-13, F-27.
8. **Add the worker-side `research_runs` progress write and the pause/cancel check.** This is the cheapest single piece of glue available: it closes three of the control plane's six stated integration requirements, which no branch satisfies on its own. F-15.
9. **Replace the private cross-branch imports** — `runtime.worker._safe_error` → `product_hunter.redaction.safe_error`; `fetch.utcnow` → `db.connection.utc_now` in the three places that use it. F-08.
10. **Break the `db.scheduler ↔ autonomy` package cycle** and give `default_schedules()` a fallback so a missing `config/cadence.yaml` is a message, not a stack trace. F-19.
11. **Reconcile the reason-code sets in `collection/orchestrator.py`.** The merged `TERMINAL_REASONS` and `NO_CAPTURE_REASONS` need `review_expired`, `incomplete_policy`, `robots_policy_missing` and `collector_unavailable`. Without the first two of those the system charges budget for requests it never makes and retries a refused source forever. F-30.
12. **Declare the collection drivers** (`crawl4ai`, `playwright`, `apify-client`) as an optional extra, and name the SubQ spend ledger's destination. F-29, F-34.
13. **Re-review the autonomy branch and resolve the source-expansion report's self-contradiction.** The autonomy branch has no stage-7 verdict and its report's file inventory is wrong in 12 line counts and one missing file. The source-expansion report says both "no review verdict exists and no commit was made" and "Verdict: ship" in the same document. F-20, F-26.
14. **Rewrite `docs/reasonix/ARCHITECTURE.md` once** — 53 tables, 10 migrations, five new module trees, four new command groups, and the F-03/F-04/F-13/F-27 wiring decisions stated plainly. The merged file would otherwise carry three mutually contradictory statements about DuckDB. F-22, F-33.
15. **Move this report** from `docs/resonix/reports/` to `docs/reasonix/reports/`, or rename the docs root once across the tree. F-21.
16. **Have a human read the `config/sources.yaml` diff** before it lands. It is an adapter high-risk path, it was edited by an agent, and it changes the source-type vocabulary — while `.opencode/skills/source-discovery/SKILL.md` still teaches agents the old spelling. F-23, F-32.
17. **Decide the checkpoint retention policy** the autonomy branch silently applies, and record it. F-35.
18. **Give the Qdrant index its own collection, and make the DuckDB layer own its own file.** Today the index and the Qdrant MCP server target the same collection with two different embedding models, and the analytics layer and the DuckDB MCP server open the same file, where a rebuild drops tables the layer did not create. F-40, F-41.
19. **Make `load_semantic_config()` load `.env`**, and add `PH_ANALYTICS_PATH` to `.env.example` and move its resolution into `config.py`. F-39, F-46.
20. **Add the missing CLI-level tests** for `ph index` and `ph analytics`, and remove the dead `--json` flag. F-45, F-13.

---

## 10. Method, and what was not checked

**Method.** `git worktree list` and `git branch -a` to locate the eight worktrees; `git diff --name-only`/`--stat main...<branch>` for the change surface; `git show <branch>:<path>` for file contents on branches without checkout; `git grep` across all branches for symbol survival and import edges; an AST-based import-graph sweep over all nine trees (eager vs. `TYPE_CHECKING`/function-level); direct reads of the spec, all eight branch reports, `.loop/adapter.json`, `pyproject.toml`, `uv.lock`, `db/migrations/*.sql`, `src/product_hunter/db/{migrations,connection,jobs,scheduler,store,outbox}.py`, `src/product_hunter/{config,cli,sources,fetch,registry}.py`, `src/product_hunter/runtime/*`, `src/product_hunter/llm/*`, `src/product_hunter/clustering/similarity.py`, and `config/*.yaml`.

**Repository state.** Unchanged. No branch was checked out, merged, committed, or reconfigured; no worktree was created or removed; no file in the repository was written except this report. The two helper scripts used for the import sweep were written to the temp directory outside the repo.

**Not checked.**
- **No test suite was executed and no Python was imported.** Every "N passed" figure in the eight reports is therefore unverified. The gate verdicts reported by the branches are taken on trust.
- The `julianday()`-based priority-aging SQL in `db/jobs.py:_due_order` was read but not run against a real SQLite file.
- The budget-dimension names in `config/autonomy.yaml` were not cross-checked one by one against `research.budget.BudgetDimension`.
- The 1788-line `uv.lock` delta for the torch/CUDA graph was not resolved locally; only the package names and versions were read out of the diff.
- The `.opencode/plugins/product-hunter.ts` `output` → `content` change was not checked against a running OpenCode instance.
- The four branch reports that were still being written by their own sessions at audit time were read as found; their claims were checked against code, not against a later revision.
- Whether `product_hunter.sources.SourceConfig.model_dump()` still carries `enabled` after the field→property change was reasoned from the diff, not executed.

**Risk the user must carry.** Two of the eight branches (autonomy, source-expansion) carry a commit that was made with the review gate waived, and the autonomy branch's own report misstates twelve file sizes. The merged tree will not be trustworthy on the strength of the branch reports alone; it needs the integration agent's own verification, starting with the two deterministic blockers.

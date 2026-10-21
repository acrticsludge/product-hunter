# Handoff: architecture and operations documentation wave

**RUN_ID:** arch-docs-20261021-4f9c2b71
**Date:** 2026-10-21
**Risk:** declared medium, floor medium. No code, config, or schema file is
touched. `.loop/adapter.json` lists `src/product_hunter/safety.py`,
`src/product_hunter/fetch.py`, `config/sources.yaml`, `db/migrations/**`, and
`.env` as high-risk paths; this wave reads them and writes none of them.
**Lane:** documentation.

## 1. Baseline

| Item | Value |
|---|---|
| Branch | `main` |
| HEAD | `205a4ba` "Phase 2 step 1: authoritative SQLite evidence core" |
| Baseline for these documents | the working tree at this RUN_ID, not HEAD alone |
| Remote | `github.com/acrticsludge/product-hunter.git`. Do not push. |

**Read this before writing anything.** Several parallel waves have written
code into the working tree, and it is not committed yet. The tree also holds
untracked spec, plan, and prompt files from those waves. Leave them alone, do
not stage them, and do not edit them.

The modules listed in section 3.13 exist in the tree and are `IMPLEMENTED`.
Some were still uncommitted when these documents were written, and their own
waves own the commit. Say so once, in a baseline note, and then treat them as
`IMPLEMENTED` like any other code. Do not hedge on every claim.

The Phase 1 and Phase 2 step 1 facts in sections 3.1 to 3.12 were read at
`205a4ba` and re-verified against the working tree. Where a later wave
changed a file, section 3.13 says so.

The code gate `uv run pytest` is green over the whole tree.

## 2. Repo map

| Path | What it is |
|---|---|
| `src/product_hunter/config.py` | `ExtractionConfig`, `Limits`, `RoutingConfig`, `CatalogSettings`, `repo_root()`, `load_config()`, `load_routing_config()`, `database_path()`, `PH_` env overrides |
| `src/product_hunter/sources.py` | `TermsStatus`, `AllowedUse`, `Collector`, `RobotsPolicy`, `RateLimit`, `SourceConfig`, `SourceRegistry`, `SourceBlocked` |
| `src/product_hunter/safety.py` | `is_public_ip()`, `resolve_public_ips()`, `admit_url()`, `admit_redirect()`, `UrlBlocked` |
| `src/product_hunter/fetch.py` | `Fetcher`, `FetchRecord`, `FetchRejection`, the fetch error hierarchy, robots, retries, rate limits |
| `src/product_hunter/raw_store.py` | `RawStore.put()`, `RawObject` |
| `src/product_hunter/documents.py` | `Document`, `DocumentChunk`, `parse_documents()`, `chunk_documents()`, `PARSER_VERSION = "phase1-0.2"` |
| `src/product_hunter/extraction.py` | `Candidate`, `PainValidity`, `PainReason`, `Frequency`, `ExtractionOutput`, `extract_documents()`, the prompt |
| `src/product_hunter/normalization.py` | `Domain` (15 values), `slugify()`, `normalize_fields()`, `NormalizedProblem`, `norm-v1` |
| `src/product_hunter/quote.py` | quote location and matching modes |
| `src/product_hunter/evaluation.py` | review-sheet columns, `evaluate()`, `EvalReport`, `render_markdown()` |
| `src/product_hunter/reporting.py` | `build_run_report()`, `RunReport` |
| `src/product_hunter/llm/base.py` | `TextGenerator` protocol, `GenerationRequest`, `GenerationResponse`, `ProviderError` |
| `src/product_hunter/llm/opencode.py` | `OpenCodeGenerateProvider`, `run_opencode_cli()`, CLI resolution |
| `src/product_hunter/llm/catalog.py` | `ModelCatalog`, `ModelEntry`, `ModelIdentity`, `Availability`, `parse_model_catalog()` |
| `src/product_hunter/llm/discovery.py` | `OpenCodeModelDiscovery`, `CatalogRefresher` |
| `src/product_hunter/llm/routing.py` | `ModelRouter`, `ModelRole`, `ModelSelection`, `RouteCandidate` |
| `src/product_hunter/db/connection.py` | `connect()`, pragmas, read-only URI mode |
| `src/product_hunter/db/migrations.py` | `migrate()`, `current_version()`, `MigrationError` |
| `src/product_hunter/db/store.py` | `Store`, `import_run()`, `import_fetch()`, `import_document()`, `import_candidate()`, `sync_registry()`, `integrity_check()` |
| `src/product_hunter/db/jobs.py` | `enqueue_job()`, `claim_job()`, `renew_lease()`, `complete_job()`, `fail_job()`, `recover_expired_leases()`, `JobContext`, `JobOutcome`, `LeaseLost` |
| `src/product_hunter/db/checkpoints.py` | `save_checkpoint()`, `load_checkpoint()`, `load_valid_checkpoint()`, `list_checkpoints()`, `Checkpoint`, `CheckpointConflict` |
| `src/product_hunter/db/outbox.py` | `enqueue_projection()`, `claim_projection()`, `process_outbox()` |
| `src/product_hunter/db/worker.py` | `Worker.run_once()`, `Worker.run_forever()`, `Worker.recover()`, the `Backoff` hook |
| `src/product_hunter/db/model_routing.py` | `record_catalog()`, `record_selection()`, `record_execution()`, `list_inventory()`, `list_executions()` |
| `src/product_hunter/db/scheduler.py` | `Scheduler`, `Schedule`, `Priority`, `default_schedules()` |
| `src/product_hunter/runtime/` | `WorkerRuntime`, `SingleInstanceLock`, `WorkerIdentity`, `WorkerEvent`, `install_shutdown_handlers` |
| `src/product_hunter/research/` | `contract.py`, `gate.py`, `budget.py`, `evidence.py`, `predicates.py`, `persistence.py` |
| `src/product_hunter/eval/` | `cases.py`, `tasks.py`, `run.py`, `report.py` |
| `db/migrations/0001_initial.sql` | The base schema, nineteen tables |
| `db/migrations/0002_model_routing.sql` | The model routing tables, four of them |
| `config/sources.yaml` | `hn_algolia`, `github_issues` enabled; `blog_http` disabled |
| `config/extraction.yaml` | Provider, model, versions, limits |
| `config/model_routing.yaml` | The thirteen role chains |
| `config/research_contract.yaml` | The baseline research contract |
| `docs/reasonix/specs/Product-Hunter-Full-Spec.md` | The full specification. The terminology source. |

Commands: `uv run pytest` is the code gate. `uv run ph --help` lists the CLI.

## 3. Facts the documents may assert

Everything in this section was read in the tree at this RUN_ID. Cite the file
when a document makes a claim from this list.

### 3.1 SQLite as authority

- Path: `config.database_path()` reads `PH_DB_PATH` from `.env` (via
  `python-dotenv`, `override=False`). A relative path resolves against the
  repository root. The default is `db/product_hunter.db` under the repository
  root. `*.db` is in `.gitignore`.
- Connection: `sqlite3.connect(isolation_level=None, check_same_thread=True)`,
  `row_factory = sqlite3.Row`. `isolation_level=None` means the code manages
  `BEGIN IMMEDIATE` / `COMMIT` / `ROLLBACK` itself.
- Pragmas: `foreign_keys = ON`, `busy_timeout = 5000`,
  `synchronous = NORMAL`, and `journal_mode = WAL` for file databases only.
  `:memory:` does not switch to WAL.
- Read-only opens use a `file:...?mode=ro` URI, never create the file, and
  raise `FileNotFoundError` when the database is missing.
- `check_same_thread=True` means a connection belongs to the thread that
  opened it. A worker thread needs its own connection. The worker runtime
  opens a second connection for its heartbeat, and refuses to construct on an
  in-memory database with a heartbeat enabled.
- Migrations: `PRAGMA user_version` is the only version record. There is no
  migration-history table and no checksum, so drift is not detected. Files in
  `db/migrations/` must be named `NNNN_name.sql`, must start at 1, and must be
  contiguous. A gap or a duplicate number raises `MigrationError`. Each
  migration runs inside `BEGIN IMMEDIATE` ... `COMMIT` and sets
  `PRAGMA user_version`. `migrate()` is idempotent. The runner splits SQL on
  `;`, so a semicolon inside a string literal is not supported.
- Two migrations exist. `migrate()` returns 2 on a current database.

### 3.2 The twenty-three tables

`db/migrations/0001_initial.sql`, nineteen tables: `sources`, `fetches`,
`communities`, `documents`, `authors`, `author_links`, `evidence`,
`pain_points`, `evidence_pain_links`, `propagation_edges`, `research_runs`,
`research_artifacts`, `research_events`, `research_checkpoints`, `jobs`,
`stage_attempts`, `projection_tasks`, `spend_ledger`, `human_decisions`.

`db/migrations/0002_model_routing.sql`, four tables:
`model_catalogs`, `model_inventory`, `model_role_bindings`,
`model_executions`.

Three of the nineteen have no writer anywhere in `src/`:
`propagation_edges`, `spend_ledger`, and `human_decisions`. The tables exist
so a later phase can use them; today they stay empty. Say that plainly.

### 3.3 Jobs, leases, fencing

- Job status is constrained to `queued`, `leased`, `succeeded`, `failed`,
  `dead`, `cancelled`. The code sets `queued`, `leased`, `succeeded`, and
  `dead`. `failed` and `cancelled` are never written by the jobs module.
- `idempotency_key` is unique. The job id is `job_` plus the first 16 hex
  characters of the SHA-256 of the key, so the same key is the same job.
  `enqueue_job()` returns the existing row when the key is present and raises
  `JobConflict` when the stage or payload differ.
- Claiming selects `status = 'queued' AND scheduled_for <= now`, ordered by
  `priority DESC, scheduled_for ASC, created_at ASC`, then sets
  `status = 'leased'`, `lease_epoch = lease_epoch + 1`,
  `lease_expires_at`, and `attempts = attempts + 1`. A `stage_attempts` row
  is inserted with status `running`. The default lease is 60 seconds.
- The fencing predicate, identical in `renew_lease`, `complete_job`, and
  `fail_job`:

```sql
WHERE id = ? AND status = 'leased'
  AND worker_id = ? AND lease_epoch = ?
  AND lease_expires_at > ?
```

  Zero rows updated raises `LeaseLost`. A worker whose lease was taken over
  cannot write.
- `recover_expired_leases()` finds rows where `lease_expires_at <= now`. A
  job whose `attempts >= max_attempts` becomes `dead`. Otherwise it returns
  to `queued` with `scheduled_for = now`, and the `stage_attempts` row is
  marked `lease_lost` with error `lease_expired`.
- `fail_job()` requeues with `scheduled_for = now + backoff_seconds`, default
  5 seconds, and marks the job `dead` when `attempts >= max_attempts`.
  `db.worker.Worker` now takes a `backoff` hook, so the requeue delay can
  come from the job rather than a flat number.
- `JobContext.assert_lease()` re-reads the row and refuses a lease that is
  not `leased`, not owned by this worker, not at this epoch, or expired.
  `JobContext.transaction()` asserts the lease before and after the write,
  inside `BEGIN IMMEDIATE`.

### 3.4 Checkpoints

- `research_checkpoints` holds `run_id`, `stage`, `step`, `checkpoint_version`,
  `state_ref`, `input_refs`, `output_refs`, `state_json`, and timestamps. It
  is unique on `(run_id, stage, step, checkpoint_version)`.
- `step` is `NOT NULL` in the schema. The code stores an empty string when
  the caller passes no step.
- The checkpoint id is `ckpt_` plus 16 hex characters of the SHA-256 of
  `run_id|stage|step|version`.
- Saving the same `(run, stage, step, version)` again with different state
  raises `CheckpointConflict`. Saving it with identical state returns the
  existing checkpoint. That is what makes a checkpoint idempotent.
- `load_checkpoint()` orders by `checkpoint_version DESC, created_at DESC`,
  so the latest version wins. `load_valid_checkpoint()` skips rows whose
  `state_json` cannot be parsed, and `list_checkpoints()` takes a
  `skip_invalid` flag.
- A checkpoint stores an arbitrary state dictionary. It does not interpret
  pending items, source cursors, budget state, or a lease epoch. The spec's
  §41.5 list is a design intent, and the code implements the container, not
  the contents.
- Nothing in the codebase calls `load_checkpoint()` to resume work. The
  resume orchestration does not exist.

### 3.5 The transactional outbox

- `projection_tasks` holds `target`, `entity_type`, `entity_id`,
  `entity_version`, `idempotency_key` (unique), `status`, `attempts`,
  `max_attempts`, `last_error`, `available_at`, lease fields, and `payload`.
- Status is `pending`, `processing`, `done`, `failed`.
- `Store.import_run()` calls `enqueue_projection()` inside the same
  `BEGIN IMMEDIATE` that writes the evidence. The authoritative row and the
  projection task commit together, which is the point of the outbox. The
  earlier direct Obsidian insert is gone.
- `import_run()` also refuses a run whose manifest counts disagree with the
  artifact line counts, so a truncated or edited artifact set fails closed
  instead of importing half a run.
- `process_outbox(conn, handlers, worker_id, ...)` claims up to `max_tasks`
  (default 10) and dispatches by `task.target` to a handler. A missing
  handler fails the task with `no handler for target ...`. An exception fails
  it with `<ExceptionType>: <message>`. `LeaseLost` stops the batch.
- There is no Obsidian handler, no Qdrant handler, and no CLI command that
  runs the outbox. `ph db outbox` only lists the tasks.
- A crash after the SQLite commit leaves the task `pending`, and a later
  `process_outbox()` picks it up. A crash during the projection leaves the
  task `processing` until its lease expires, then it returns to `pending`, or
  to `failed` when the attempt ceiling is reached.

### 3.6 The worker model

Two layers exist.

`db.worker.Worker(conn, worker_id, handlers, lease_seconds=60,
backoff_seconds=5)` is a library class. `run_once()` claims one job,
dispatches by `job.stage`, and returns a `JobOutcome` or `None`.
`run_forever()` loops with `poll_seconds=1.0` and an optional `stop_event`.
`recover()` calls `recover_expired_leases()`. A handler is
`Callable[[JobContext], dict | None]`. A stage with no handler fails the job
with `no handler for stage '<stage>'`. It does not process projections, does
not advance `research_runs` state, does not heartbeat on its own, and has no
single-instance lock.

`src/product_hunter/runtime/` is the process layer.
`runtime.worker.WorkerRuntime` claims a job, hands it to the caller, renews
the lease from a background thread on its own connection, records the
outcome, and shuts down gracefully. `runtime.lock.SingleInstanceLock` takes
an OS file lock, `msvcrt.locking` on Windows and `fcntl.flock` elsewhere, so
the lock dies with the process. `runtime.identity.WorkerIdentity` builds a
`worker-<hostname>-<pid>-<8 hex>` id from `secrets.token_hex(4)`.
`runtime.events` defines the stable event names: `worker_started`,
`worker_stopped`, `lease_recovered`, `job_claimed`, `job_succeeded`,
`job_failed`, `job_dead`, `job_lease_lost`, `lease_renewed`,
`lease_renewal_failed`, `handler_missing`, `shutdown_requested`,
`shutdown_timeout`, `lock_acquired`, `lock_denied`, `no_handlers`.
`install_shutdown_handlers()` routes `SIGINT` and `SIGTERM` to
`request_stop()`.

**Two hazards to record.** First, `.gitignore` contains `runtime/`, which
matches `src/product_hunter/runtime/`. The worker runtime package is
invisible to `git status` and would not be committed by a plain `git add`.
Second, the runtime package exists on disk but is not in the committed tree at
`205a4ba`.

### 3.7 Evidence provenance

The chain, from bytes to an accepted record:

```text
raw bytes  ->  data/raw/<source_id>/<sha256><ext>     (RawStore, atomic write)
           ->  FetchRecord                            (fetch log, data/raw/fetches.jsonl)
           ->  Document                               (text_sha256, parser_version)
           ->  DocumentChunk
           ->  evidence span                          (quote, char range, match_mode)
           ->  pain_points row                        (accepted = true)
```

- Identifiers are deterministic. `span_id` is `ev_` plus 16 hex of the
  SHA-256 of `doc_id|char_start|char_end|quote`. `candidate_id` adds the
  extractor version. `pain_id` is `pp_` plus 16 hex of the SHA-256 of
  `span_id|extractor_version`. The same span and the same extractor version
  always produce the same id, so a replay reproduces them.
- A fetch is recorded whether it succeeds or fails. A failed fetch writes a
  `FetchRejection` to `data/raw/rejections.jsonl` with a `reason_code`.
- The rejection codes: `unknown_source`, `source_disabled`,
  `terms_not_permitted`, `use_not_allowed`, `missing_use`, `no_domains`,
  `unsafe_url`, `robots_blocked`, `budget_exceeded`, `http_error`,
  `too_large`, `too_many_redirects`, `empty_body`, `network_error`,
  `fetch_failed`.
- Extraction rejections use a different vocabulary: `budget_exceeded`,
  `prompt_too_large`, `provider_error`, `invalid_json`, `schema_invalid`,
  `quote_too_short`, `quote_too_long`, `quote_not_found`,
  `quote_outside_chunk`, `normalization_invalid`, and `provider_check_failed`.
- `Store.import_run()` writes a run with `status = 'completed'` and
  `current_stage = 'PAIN_EXTRACTION'`. There is no run lifecycle. The run
  state machine from spec §41.2 is not implemented.

### 3.8 Source safety

- `config/sources.yaml` is version 1 with three sources. `hn_algolia` and
  `github_issues` are `terms_status: permitted`, `reviewed_at: 2026-10-20`,
  and enabled, with `allowed_use: [discovery, retrieval, analysis, storage]`.
  `blog_http` is `terms_status: unknown`, `allowed_use: [none]`, no domains,
  and `enabled: false`. Reddit is deliberately absent while its terms are
  unknown.
- Admission fails closed. `SourceRegistry.require()` raises `SourceBlocked`
  with one of the codes above for an unknown source, a disabled source, a
  source whose terms are not `reviewed` or `permitted`, a use that is not
  allowed, a missing required use, or a source with no domains.
- The registry validates at load time: a `prohibited` source cannot be
  enabled, an enabled source needs `terms_status` of `reviewed` or
  `permitted`, needs `retrieval` and `storage` in `allowed_use`, and needs at
  least one domain. A domain must be a hostname, not an IP literal, and not
  `localhost`, `.local`, or `.internal`.
- URL admission in `safety.py`: the scheme must be in the source's `schemes`,
  userinfo is refused, the host must be in the source's `domains`, the port
  must be 80 or 443 and match the scheme, and the host must resolve to a
  public IP. `is_public_ip()` refuses loopback, private, link-local,
  multicast, reserved, unspecified, and CGNAT `100.64.0.0/10` addresses, and
  requires `is_global`. An IP literal as the host is refused.
- Every redirect is re-admitted, and a downgrade from HTTPS to HTTP is
  refused.
- Robots: `hn_algolia` and `github_issues` set `robots: skip` because they are
  API calls. `blog_http` sets `robots: required`. When robots are required
  and the file cannot be fetched, the request is blocked.
- Rate limits come from the source: `min_interval_seconds`,
  `max_requests_per_run`, `max_retries`, `backoff_base_seconds`,
  `backoff_max_seconds`. Exceeding `max_requests_per_run` raises
  `BudgetExceeded` with the code `budget_exceeded`.
- Retries cover transport errors and the status codes 429, 500, 502, 503, and
  504. The delay honours `Retry-After` and otherwise doubles from
  `backoff_base_seconds` up to `backoff_max_seconds`.
- The scheduler applies the same fail-closed rule before it enqueues work for
  a source, and refuses a schedule for a source that is unknown, disabled, or
  whose terms are not permitted.

### 3.9 Credentials

- The repository holds no provider key. `.env` is gitignored, and
  `.env.example` holds names only, with a comment saying not to define
  `DEEPSEEK_API_KEY` or any other provider secret.
- `.env.example` now also carries an empty `SUBQ_API_KEY=` with a comment
  that it stays empty until the Phase 7 SubQ gate and that the pipeline does
  not read it today.
- The LLM provider is `OpenCodeGenerateProvider`. It resolves the `opencode`
  executable, then calls it as an argv list with `shell=False`:
  `opencode api post /api/experimental/generate --data <json>`. It reads
  `/api/info` and `/api/model` for `check()`. Auth is discovered by the
  OpenCode CLI and its auth store, so the repository needs no key.
- `run_opencode_cli(argv, *, timeout_seconds, runner, label)` is now the
  shared call path for generation and for model discovery. It refuses a
  timeout, an `OSError`, and a nonzero exit by name.
- The JSON body carries `prompt` and
  `model: {providerID, id}`. The provider refuses a body over 30,000
  characters. The timeout is `config.limits.timeout_seconds`, 120 seconds by
  default. The provider does not retry.
- `check()` requires the configured provider and model to be present with
  `status == "active"` and `enabled == true`.

### 3.10 The CLI

```text
ph doctor                              check the provider and model, print JSON
ph version                             print the package version, 0.1.0
ph sources                             list the source registry as JSON
ph fetch <source_id> <url>             fetch one admitted URL, print the record
ph extract [--limit N] [--source ID] [--run-id ID]
ph review-sheet --run RUN_ID --out PATH [--limit 100]
ph eval --labels PATH [--out PATH] [--quote-failures N]
ph report --run RUN_ID [--out PATH]
ph models [--refresh] [--role ROLE]     show the model catalogue or a role chain
ph db init    [--path P]
ph db status  [--path P]
ph db import-run --run RUN_ID [--fetch-log P] [--path P]
ph db recover [--path P]
ph db jobs    [--status S] [--path P]
ph db outbox  [--status S] [--path P]
ph db check   [--path P]
```

`ph models` prints the catalogue as JSON, with `catalog_version`, `source`,
`server_version`, `discovered_at`, `model_count`, and the model list. With
`--role` it prints the resolved chain for that role instead. `--refresh`
forces a rediscovery.

There is no `ph worker`, no `ph research`, no `ph contract`, no `ph gate`,
no `ph scheduler`, and no `ph continue`. The `ph eval` command scores the
Phase 1 review sheet; the `src/product_hunter/eval/` benchmark package has no
CLI.

### 3.11 What is not implemented

State this in the documents, with the spec section that requires it.

| Spec requirement | Section | State |
|---|---|---|
| Research run lifecycle and transitions | §41.2 | not implemented |
| `continue` and cross-chat continuity | §41.6, §41.8 | not implemented |
| Tool-call and gate-result persistence in SQLite | §6A, §34 | not implemented; `research/persistence.py` holds protocols only |
| `spend_ledger` writes and global spend caps | §22 | not implemented; the table exists and stays empty |
| Opportunity states and scoring | §20, §23 | not implemented |
| Qdrant, DuckDB, and Obsidian projections | §14, §16, §27 | not implemented |
| Independent-author and propagation analysis | §12 | not implemented; `propagation_edges` exists and stays empty |
| Backup, restore, retention, drift canaries | §38B | not implemented |
| Human approval gates | §37 | not implemented; `human_decisions` exists and stays empty |
| MCP server or client | §6 | not implemented, no MCP code in the repository |
| Obsidian projection handler for the outbox | §15A, §27 | not implemented |

### 3.12 Roles this documentation gives to systems outside the code

- **OpenCode** is the interaction surface: the operator runs `ph` through it,
  it holds the user's model selection, and it can host skills. It is not the
  authority for research state. The repository's `.opencode/skills/` holds
  ten skills and no agents, commands, or plugins:
  `entity-resolution`, `evidence-provenance`, `evidence-validation`,
  `frequency-analysis`, `pain-point-extraction`,
  `pain-point-normalization`, `problem-clustering`, `source-discovery`,
  `source-independence`, `source-reliability`. The repository README still
  says five, which is stale.
- **MCP** is specified as an OpenCode integration surface for interactive
  exploration. It is not the authoritative execution mechanism for
  unattended runs. No MCP code exists in this repository. An untracked
  `opencode.json` in the working tree declares Qdrant and DuckDB MCP
  servers; it is not committed and is not part of the product.
- **Human approval.** The specification divides actions into automatic and
  approval-required. The automatic list: read public web, search, analyse,
  write the research database, write Obsidian through the projection worker,
  update research notes through controlled writers. The approval list:
  destructive shell commands, Git push, external account actions, external
  posting, irreversible operations. The code implements none of the approval
  list today, because the code has no external-account, posting, or Git
  write path. The one human input the code does require is the filled
  evaluation sheet, and that is offline labelling rather than an approval
  gate.

### 3.13 The parallel wave code in the working tree

All of this exists in the tree and is `IMPLEMENTED`.

#### Model routing

`src/product_hunter/llm/catalog.py` is pure data and parsing.
`ModelIdentity` is a frozen pair of `provider_id` and `model_id`, and
`.key` is `provider/model`. `Availability` is an enum of `available`,
`disabled`, `retired`, `unknown`, `missing`.
`derive_availability(status, enabled)` maps a catalogue entry to one of them,
and only `active` plus `enabled` is `available`. `unknown` is deliberately
not selectable: the fallback CLI list reports no status, and a model whose
usability was never confirmed must not reach the pipeline.
`compute_catalog_version(entries)` is a SHA-256 over the sorted
`provider/model|status|enabled|name` lines, prefixed `sha256:`.
`parse_model_catalog()` reads the route payload, with `providerID`,
`modelID` or `id`, `status`, `enabled`, `limit`, `cost`, and `time.released`.
`parse_model_list()` reads the plain CLI list and forces every entry to
`unknown`.

`src/product_hunter/llm/discovery.py` holds the two surfaces:
`opencode api get /api/model` is primary, `opencode models` is the fallback,
and `opencode api get /api/info` supplies the server version. All three are
argv lists, never a command string. `CatalogRefresher` re-discovers when the
cached catalogue is older than `refresh_interval_seconds`, 900 by default. A
failed refresh keeps the previous catalogue and only raises when there is no
catalogue at all, so a discovery failure is never fatal to a running
pipeline.

`src/product_hunter/llm/routing.py` holds the resolution.
`ModelRole` is an enum of thirteen roles: `extraction`, `classification`,
`normalization`, `deduplication`, `entity_resolution`, `clustering`,
`validation`, `investigation`, `competitor_research`, `counter_evidence`,
`scoring`, `reporting`, `synthesis`. `RouteCandidate` is either an explicit
`provider/model` or a glob pattern over the live catalogue. `ModelRouter`
expands the chain, takes the first available candidate, falls through the
chain when one fails, and raises `NoModelAvailable` when nothing is
available. `UnknownRole` is raised for a role that is not in the enum.
`ModelSelection` carries `role`, `identity`, `selection_source`,
`fallback_index`, `is_fallback`, `catalog_version`, and `resolved_at`.
`SelectionSource` is `config`, `pattern`, or `default`. `is_fallback` is true
for any index above zero.

`src/product_hunter/db/model_routing.py` writes the four tables.
`record_catalog()` upserts `model_catalogs`, marks every
`model_inventory` row that was not retired as `missing` with a `retired_at`,
and upserts each entry back to life, so a model that leaves the catalogue
keeps its row. `record_selection()` upserts `model_role_bindings` on
`(role, position)`. `record_execution()` inserts one `model_executions` row
per call, with `provider_id`, `model_id`, `role`, `selection_source`,
`fallback_index`, `is_fallback`, `catalog_version`, `prompt_version`,
`config_version`, `latency_ms`, `status`, and `error`.
`list_inventory()` and `list_executions()` read them back.

`config/model_routing.yaml` is version 1. Every role is a list of patterns,
and no model id appears in it. The cheap roles get `opencode-go/*-flash*`
then `opencode-go/*`; the reasoning roles get `opencode-go/deepseek*` then
`opencode-go/*`. `config.RoutingConfig.config_version` is
`routing-v{version}-{sha256[:12]}`.

**The important limit.** The generation path is not wired to the router.
`OpenCodeGenerateProvider` still uses the pinned `provider_id` and `model_id`
from `config/extraction.yaml`. `build_router()` exists and the `ph models`
command uses it, so the router is reachable, but an extraction run still
calls the configured model directly. Say this plainly; it is the difference
between a router that exists and a router that routes the pipeline.

#### Research contract and tool gate

`src/product_hunter/research/` holds six modules and nothing outside the
package imports it. There is no CLI command, no worker handler, and no
caller. The code is complete and reachable, but it does not gate anything
yet.

`evidence.py` holds `ToolCallStatus` (`success`, `failure`, `timeout`,
`partial`), `StageStatus`, `ClaimKind` (`no_strong_competitor`,
`no_counter_evidence_found`, `opportunity_exists`), `ToolCallProvenance`,
`ArtifactRecord`, `ZeroResultOutcome`, `StageExecution`, `ResearchClaim`, and
`RunEvidence`. `result_ref` must match `^[A-Za-z0-9._:/-]+$`, so a
hand-written summary cannot be passed off as the original tool result, and
`timestamp` must be timezone-aware.

`predicates.py` holds twelve metrics and eight predicates, with the
`PredicateContext` of `run_id`, `execution`, and `claims` as the input. A
metric that cannot be computed returns zero, and a missing artifact is never
read as a satisfied threshold.

`budget.py` holds `BudgetDimension` with fifteen values, `BudgetSpec`,
`BudgetDecision`, `BudgetExceeded`, and `BudgetTracker`. `check()` returns a
decision, `guard()` raises before the call, `record()` records and raises
past the limit. The rule from the spec is literal: a call that would exceed
a hard limit is not started.

`contract.py` holds `ResearchContract` with the nine parts, `StageContract`,
`EvidenceThreshold`, `HardRequirements`, `StoppingPolicy`, `RetryPolicy`,
`PromotionRequirements`, `validate_contract()` which returns a list of
errors, and `require_valid_contract()` which raises `ContractError`.
`load_research_contract()` reads `config/research_contract.yaml`.

`gate.py` holds `ViolationCode` with fourteen codes: `stage_missing`,
`stage_failed`, `stage_timed_out`, `budget_exceeded`, `artifact_invalid`,
`provenance_missing`, `evidence_threshold_unmet`, `promotion_blocked`,
`artifact_missing`, `tool_missing`, `quality_check_failed`,
`provenance_mismatch`, `contract_invalid`, `claim_evidence_missing`.
`evaluate_stage()`, `evaluate_run()`, and `evaluate_promotion()` return
results and never raise for a failed gate. Only an invalid contract yields
`contract_invalid`.

`persistence.py` holds the `ToolCallStore` and `GateResultStore` protocols
and no SQLite. Its docstring names a planned migration
`db/migrations/0002_research_gate.sql`, and that number is already taken by
`0002_model_routing.sql`. The next research-gate migration has to be 0003.
Record the conflict rather than papering over it.

`config/research_contract.yaml` is `contract_version: phase4-v1` with eight
required stages, per-stage tools, outputs, quality checks, and gate keys, all
hard requirements on, thresholds for five gate areas, a budget with twelve
per-run dimensions and per-source limits, and stopping, retry, and promotion
policies. It loads and validates.

#### Scheduler

`src/product_hunter/db/scheduler.py` holds `Priority` with five bands
(`P0` 50, `P1` 40, `P2` 30, `P3` 20, `P4` 10), `Schedule`, `Scheduler`,
`ScheduleTick`, `BlockedSchedule`, and `default_schedules()`.
`Scheduler.tick()` enqueues through `enqueue_job()` with an idempotency key
of `schedule:{name}:{occurrence}`, so a second tick at the same instant
creates nothing and a second scheduler over the same database creates
nothing. `default_schedules()` holds five schedules: `source_collection`
every 900 seconds at P3, `evidence_processing` every 3600 at P2,
`reclustering` every 21600 at P3, `deep_investigation` every 86400 at P1 with
a 7200 second offset, and `morning_brief` every 86400 at P2 with a 25200
second offset. The module docstring says it plainly: nothing runs it yet.
There is no CLI command and no entry point. The single-instance lock lives in
the runtime package, not here.

#### The evaluation benchmark package

`src/product_hunter/eval/` is a benchmark harness, separate from the Phase 1
review sheet. `cases.py` holds `TaskName` with ten tasks and
`EVALUATOR_VERSION = "eval-v1"`. `tasks.py` holds one scorer per task and
`score_case()`, which returns `passed = None` for an unlabelled case that is
not the quote-fidelity task. `run.py` holds `run_dataset()`,
`check_thresholds()`, `compare_runs()`, and the run record types.
`report.py` renders the benchmark Markdown. No CLI command reaches it, and
`ph eval` still scores the Phase 1 review sheet.

#### The ten project skills

`.opencode/skills/` holds `entity-resolution`, `evidence-provenance`,
`evidence-validation`, `frequency-analysis`, `pain-point-extraction`,
`pain-point-normalization`, `problem-clustering`, `source-discovery`,
`source-independence`, `source-reliability`. The repository README still
claims five, and this wave does not fix that line, but the documentation
should say ten.

## 4. Writing rules for the implement nodes

- Plain prose. No marketing register. The repository's `README.md` and
  `docs/reasonix/reports/phase-1-step-4.md` are the voice reference, not a
  corporate template.
- Vary sentence length. Do not chain three short sentences. Do not default to
  lists of three.
- One em dash per document at most. Use commas, colons, and parentheses.
- Avoid: delve, tapestry, landscape, testament, vibrant, pivotal, crucial,
  intricate, meticulous, bolster, garner, underscore, interplay, multifaceted,
  nuanced, foster, leverage as a verb, utilize, commence, facilitate,
  encompass, paramount, groundbreaking, seamless, robust, comprehensive,
  harness, showcase, highlight, emphasise, enhance, remarkable, profound, in
  essence, synergy, at its core, in conclusion, overall, moving forward, and
  the openers additionally, furthermore, moreover, notably, importantly,
  indeed, interestingly.
- "Pain point" is a domain term in this repository, from the specification.
  Keep it. It is not the banned filler usage.
- Never invent a number. If a count is not in this handoff or in the code,
  it does not go in the document.
- Name the file, table, command, or function behind every claim about
  behaviour.
- Use the four labels from the specification for this wave: `IMPLEMENTED`,
  `PLANNED`, `BLOCKED`, `OPTIONAL`, with a legend at the top of each document.
- One baseline note per document, near the top, saying which tree state it
  describes. Do not repeat it per claim.

## 5. SubQ Snap, stage 0

One call, model `subq-snap-preview`, 729 tokens, 0 errors.

| Question | Answer | Probability | Confidence |
|---|---|---|---|
| risk | `medium` | 0.720 | 0.580 |
| lane | `none` | 0.915 | 0.872 |
| overclaim risk | yes | 0.978 | n/a |
| status vocabulary | four labels | 0.561 | 0.341 |

The overclaim answer is the one that matters: 0.978 that a documentation wave
like this could easily state features that do not exist. The four-label
vocabulary is the cheapest way to keep that number low.

## 6. Still unverified

- The `spend_ledger`, `human_decisions`, and `propagation_edges` tables were
  confirmed empty of writers by reading `src/`, not by running the code.
- `ph models` was not run against a live OpenCode server. The discovery
  behaviour is read from code.
- No live research run has been executed against the current schema, and no
  research stage has been gated by the gate engine, because nothing calls it.
- The Phase 1 acceptance gate, 100 manually inspected evidence spans, is
  still open. `docs/reasonix/reports/phase-1-step-4.md` records that no
  precision, recall, or normalization rate exists for a real corpus.
- The working tree moves while this wave runs. Re-check the tree before
  committing.

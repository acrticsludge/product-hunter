# Product Hunter architecture

**Baseline.** This document describes the working tree, not the last commit. The
fourth stage-8 pass of phase 4 was written on 2026-10-21, when HEAD was
`80a98a4` and the model router, the research contract and gate engine, the
scheduler, the worker runtime, and the evaluation benchmark package were in the
tree and still uncommitted. HEAD is now `3d7bc68`, and the discovery package in
`src/product_hunter/discovery/` has been added on top of it. That package is in
the tree and still uncommitted, and it is `IMPLEMENTED` like any other code.
The full specification is
[Product-Hunter-Full-Spec.md](specs/Product-Hunter-Full-Spec.md).

## Claim labels

Every factual claim here carries one of four labels.

| Label | Meaning |
|---|---|
| `IMPLEMENTED` | Code exists in this branch. The claim names the file. |
| `PLANNED` | A specification exists and is approved, but no code does. The claim names the spec. |
| `BLOCKED` | Cannot proceed until a precondition is met. The claim names the precondition. |
| `OPTIONAL` | Deliberately excluded from the core, per spec §38. |

The labels apply per claim, not per section. One section can hold an
`IMPLEMENTED` sentence and a `PLANNED` one.

## Who owns what

Four parts own the system, and only one of them owns the truth.

**OpenCode** is the interaction surface. The operator runs `ph` through it, it
holds the user's model selection, and it hosts the ten project skills under
`.opencode/skills/`. It is not the authority for research state.

**The worker** owns execution. Jobs are claimed, leased, executed, and completed
by worker code, not by a chat session. `IMPLEMENTED`
(`src/product_hunter/db/worker.py`).

**SQLite** owns state. Every authoritative record lives in one database file.
`IMPLEMENTED` (`src/product_hunter/db/connection.py`).

**The filesystem** is a mirror. `data/` holds raw bytes and run artifacts that
are inspectable and recoverable, but it does not decide what is true. Spec §33
calls these files durable, content-addressed mirrors, not authority.

MCP is not on that list because it is not code in this repository.

## SQLite is the single source of truth

`IMPLEMENTED` (`src/product_hunter/db/connection.py`,
`src/product_hunter/db/migrations.py`). `config.database_path()` in
`src/product_hunter/config.py` reads `PH_DB_PATH` from `.env` through
`python-dotenv` with `override=False`. A relative path resolves against the
repository root, and the default is `db/product_hunter.db` under that root.
`*.db` is in `.gitignore`.

The connection opens with `sqlite3.connect(isolation_level=None,
check_same_thread=True)` and `row_factory = sqlite3.Row`. `isolation_level=None`
means the code manages `BEGIN IMMEDIATE`, `COMMIT`, and `ROLLBACK` itself. The
pragmas are `foreign_keys = ON`, `busy_timeout = 5000`, `synchronous = NORMAL`,
and `journal_mode = WAL` for file databases only; a `:memory:` database does not
switch to WAL. Read-only opens use a `file:...?mode=ro` URI, never create the
file, and raise `FileNotFoundError` when the database is missing.

`check_same_thread=True` means a connection belongs to the thread that opened
it, so a worker thread needs its own connection. The worker runtime opens a
second connection for its heartbeat.

Migrations use `PRAGMA user_version` as the only version record. There is no
migration-history table and no checksum, so drift is not detected. Files in
`db/migrations/` must be named `NNNN_name.sql`, must start at 1, and must be
contiguous; a gap or a duplicate number raises `MigrationError`. Each migration
runs inside `BEGIN IMMEDIATE` and `COMMIT`, then sets `PRAGMA user_version`.
`migrate()` is idempotent and returns 2 on a current database. The runner splits
SQL on `;`, so a semicolon inside a string literal is not supported.

## The twenty-three tables

`IMPLEMENTED` (`db/migrations/0001_initial.sql`,
`db/migrations/0002_model_routing.sql`). The schema holds twenty-three tables:
nineteen from the base migration and four from the model routing migration.

| Group | Tables |
|---|---|
| Collection and provenance | `sources`, `fetches`, `communities`, `documents`, `authors`, `author_links` |
| Analysis | `evidence`, `pain_points`, `evidence_pain_links` |
| Research registry | `research_runs`, `research_artifacts`, `research_events`, `research_checkpoints` |
| Execution | `jobs`, `stage_attempts`, `projection_tasks` |
| Model routing | `model_catalogs`, `model_inventory`, `model_role_bindings`, `model_executions` |
| Spend | `spend_ledger` |
| Reserved, no writer | `propagation_edges`, `human_decisions` |

Two tables have no writer anywhere in `src/`: `propagation_edges` and
`human_decisions`. They exist so a later phase can use them, and today they
stay empty. The independent-author and propagation analysis from spec §12 and
the approval records from spec §37 are the work that would fill them.

`spend_ledger` has a writer now. `discovery/store.py` writes one row per
discovery record, with the query count in `units`, the unit type
`search_queries`, and the cost of the search in `cost_micros`. Those rows hold
the spend of a discovery run, and no other stage writes the table.

## Runtime artifacts

`IMPLEMENTED` (`src/product_hunter/raw_store.py`, `src/product_hunter/fetch.py`).
Raw bytes live under `data/raw/<source_id>/<sha256><ext>`, written atomically
by `RawStore.put()`. Every fetch writes a `FetchRecord` to
`data/raw/fetches.jsonl`. Every blocked or failed fetch writes a `FetchRejection`
to `data/raw/rejections.jsonl` with a `reason_code`. A run writes its artifacts
under `data/processed/<run_id>/`.

`data/` is gitignored and `.loop/adapter.json` forbids committing it. Spec §33
calls the runtime files durable, content-addressed mirrors: useful for recovery,
debugging, and inspection, but not the authority. SQLite is.

## The execution machinery

Two layers exist. `IMPLEMENTED` (`src/product_hunter/db/worker.py`,
`src/product_hunter/db/jobs.py`, `src/product_hunter/runtime/`).

### The library worker

`db.worker.Worker(conn, worker_id, handlers, lease_seconds=60,
backoff_seconds=5)` is a library class. `run_once()` claims one job, dispatches
by `job.stage`, and returns a `JobOutcome` or `None`. `run_forever()` loops with
`poll_seconds=1.0` and an optional `stop_event`. `recover()` calls
`recover_expired_leases()`. A handler is `Callable[[JobContext], dict | None]`,
and a stage with no handler fails the job with `no handler for stage '<stage>'`.
It does not process projections, does not advance `research_runs` state, does
not heartbeat on its own, and has no single-instance lock.

### The process layer

`runtime/worker.WorkerRuntime` claims a job, hands it to a caller, renews the
lease from a background thread on its own connection, records the outcome, and
shuts down gracefully. `runtime/lock.SingleInstanceLock` takes an OS file lock,
`msvcrt.locking` on Windows and `fcntl.flock` elsewhere, so the lock dies with
the process. `runtime/identity.WorkerIdentity` builds a
`worker-<hostname>-<pid>-<8 hex>` id from `secrets.token_hex(4)`.
`runtime/worker.py` and `runtime/__main__.py` emit events named `worker_started`,
`worker_stopped`, `job_claimed`, `job_succeeded`, `job_failed`, `job_dead`,
`handler_missing`, `no_handlers`, `lease_recovered`, `lease_renewed`,
`lease_renewal_failed`, `shutdown_requested`, `shutdown_timeout`,
`lock_acquired`, and `lock_denied`. `runtime/__main__.py` is the entry point,
and `install_shutdown_handlers()` routes `SIGINT` and `SIGTERM` to
`request_stop()`. A handler that overruns the grace period, 30 seconds, is left
to the lease, so the job is delayed rather than corrupted.

### Leases and fencing

`IMPLEMENTED` (`src/product_hunter/db/jobs.py`). Job status is constrained to
`queued`, `leased`, `succeeded`, `failed`, `dead`, and `cancelled`. The code
writes `queued`, `leased`, `succeeded`, and `dead`; `failed` and `cancelled` are
never written by the jobs module.

`idempotency_key` is unique. The job id is `job_` plus the first 16 hex
characters of the SHA-256 of the key, so the same key is the same job.
`enqueue_job()` returns the existing row when the key is present and raises
`JobConflict` when the stage or payload differ.

Claiming selects `status = 'queued' AND scheduled_for <= now`, ordered by
`priority DESC, scheduled_for ASC, created_at ASC`, then sets
`status = 'leased'`, `lease_epoch = lease_epoch + 1`, `lease_expires_at`, and
`attempts = attempts + 1`. A `stage_attempts` row is inserted with status
`running`. The default lease is 60 seconds.

The fencing predicate is identical in `renew_lease`, `complete_job`, and
`fail_job`:

```sql
WHERE id = ? AND status = 'leased'
  AND worker_id = ? AND lease_epoch = ?
  AND lease_expires_at > ?
```

Zero rows updated raises `LeaseLost`, so a worker whose lease was taken over
cannot write. `recover_expired_leases()` finds rows where
`lease_expires_at <= now`. A job whose `attempts >= max_attempts` becomes
`dead`; otherwise it returns to `queued` with `scheduled_for = now`, and its
`stage_attempts` row is marked `lease_lost` with error `lease_expired`.
`fail_job()` requeues with `scheduled_for = now + backoff_seconds`, default 5
seconds, and marks the job `dead` when `attempts >= max_attempts`.
`db.worker.Worker` takes a `backoff` hook, so the requeue delay can come from the
job rather than a flat number. `JobContext.assert_lease()` re-reads the row and
refuses a lease that is not `leased`, not owned by this worker, not at this
epoch, or expired. `JobContext.transaction()` asserts the lease before and after
the write, inside `BEGIN IMMEDIATE`.

### Checkpoints

`IMPLEMENTED` (`src/product_hunter/db/checkpoints.py`). `research_checkpoints`
holds `run_id`, `stage`, `step`, `checkpoint_version`, `state_ref`,
`input_refs`, `output_refs`, `state_json`, and timestamps. It is unique on
`(run_id, stage, step, checkpoint_version)`, and `step` is `NOT NULL` in the
schema while the code stores an empty string when the caller passes no step.

The checkpoint id is `ckpt_` plus 16 hex characters of the SHA-256 of
`run_id|stage|step|version`. Saving the same `(run, stage, step, version)` again
with different state raises `CheckpointConflict`; saving it with identical state
returns the existing checkpoint, which is what makes a checkpoint idempotent.
`load_checkpoint()` orders by `checkpoint_version DESC, created_at DESC`, so the
latest version wins. `load_valid_checkpoint()` skips rows whose `state_json`
cannot be parsed, and `list_checkpoints()` takes a `skip_invalid` flag.

A checkpoint stores an arbitrary state dictionary. It does not interpret pending
items, source cursors, budget state, or a lease epoch. Spec §41.5 lists those
fields as design intent, and the code in `db/checkpoints.py` still implements
the container and not the contents. The discovery package writes part of that
intent into a state of its own: its pending queries, its family cursors and
its budget counters, and it reads them back through the same container.
Nothing resumes a research run from a checkpoint, so the resume orchestration
does not exist.

### The transactional outbox

`IMPLEMENTED` (`src/product_hunter/db/outbox.py`,
`src/product_hunter/db/store.py`). `projection_tasks` holds `target`,
`entity_type`, `entity_id`, `entity_version`, `idempotency_key` (unique),
`status`, `attempts`, `max_attempts`, `last_error`, `available_at`, lease
fields, and `payload`. Status is `pending`, `processing`, `done`, or `failed`.

`enqueue_projection()` is called inside the same `BEGIN IMMEDIATE` that writes
the evidence, in `Store.import_run()`. The authoritative row and the projection
task commit together, and that is the point of the outbox. `import_run()` also
refuses a run whose manifest counts disagree with the artifact line counts, so a
truncated or edited artifact set fails closed instead of importing half a run.

`process_outbox(conn, handlers, worker_id, ...)` claims up to `max_tasks`
(default 10) and dispatches by `task.target` to a handler. A missing handler
fails the task with `no handler for target ...`; an exception fails it with
`<ExceptionType>: <message>`. `LeaseLost` stops the batch. There is no Obsidian
handler, no Qdrant handler, and no CLI command that runs the outbox; `ph db
outbox` only lists the tasks.

A crash after the SQLite commit leaves the task `pending`, and a later
`process_outbox()` picks it up. A crash during the projection leaves the task
`processing` until its lease expires, then it returns to `pending`, or to
`failed` when the attempt ceiling is reached.

Three success words differ, and they stay different. A job that finishes is
`succeeded`. A projection task that finishes is `done`. A research run that
finishes is `completed`.

## Subsystems that exist but are not on the pipeline

Each of these is `IMPLEMENTED` and reachable, and each has one thing that does
not call it yet.

**The model router and its catalogue.** `IMPLEMENTED`
(`src/product_hunter/llm/catalog.py`, `src/product_hunter/llm/discovery.py`,
`src/product_hunter/llm/routing.py`, `src/product_hunter/db/model_routing.py`,
`config/model_routing.yaml`). The router resolves a role to a `provider/model`
identity through an ordered chain of candidates and takes the first available
one. `ph models` uses it. What does not use it is the generation path:
`OpenCodeGenerateProvider` in `src/product_hunter/llm/opencode.py` still calls the
model pinned in `config/extraction.yaml`. The router exists and routes the `ph
models` command; an extraction run does not go through it. That distinction is
the whole story and it is told in
[MODEL_ROUTING_IMPLEMENTATION.md](MODEL_ROUTING_IMPLEMENTATION.md).

**The research contract and gate engine.** `IMPLEMENTED`
(`src/product_hunter/research/`, `config/research_contract.yaml`). The contract
is a typed schema, the gate turns evidence into a pass or fail decision with
stable `ViolationCode` values, and the budget refuses a call that would exceed a
hard limit. A gate verdict measures an overrun against the limits the contract
declares, so a tracker built from a looser budget cannot widen them. An
artifact counts towards a threshold only when it is valid, belongs to the stage
being judged, and traces to a successful call of that stage, and the same rule
governs the two artifact-reading quality predicates. A call filed under another
stage is not that stage's evidence either: it counts towards no threshold and
satisfies no required tool, while the same call is still reported as a
provenance mismatch when the contract requires provenance. At run level, a
metric that is a minimum over what it counts takes the smallest of the run's
stage values rather than their sum, and a refused non-finite budget amount
raises a named `NonFiniteAmount` rather than a bare `ValueError`. A contract
that names a threshold or a quality check on an artifact kind its own stage
does not declare is refused. Three of the six hard-requirement flags are
enforced through the stage gate only, so with
`promotion.require_all_stages_passed: false` they are not enforced at
promotion time, and the shipped contract sets the flag to true. What does not
call it is anything: nothing outside the package imports it, no CLI command
calls it, and there is no worker handler, so the gate is complete and reachable
as a library and gates nothing yet. What the code enforces about research
quality is in [RESEARCH_CONTRACT.md](RESEARCH_CONTRACT.md).

**The discovery engine.** `IMPLEMENTED` (`src/product_hunter/discovery/`). The
package decides what to search. It is separate from collection, which decides
how a source is fetched, and it never calls a search endpoint: there is no HTTP
client, no CLI command and no worker handler. `families.py` holds the eight
query families, their templates and the marker table, and the strategy is
worker-side code, so those are module constants rather than values read at
runtime: the wording of a query comes from a template and never from a
paraphrase, and the same seed produces the same query text in every run.
`queries.py` holds the `Query` type, the identifiers, the normalization, the
duplicate check and the two generation paths, `records.py` holds the
`DiscoveryRecord` and its seven-value status vocabulary, `budget.py` holds the
`DiscoveryBudget` with its limits and its counters, and `frontier.py` holds
the `QueryFrontier`, the `SaturationPolicy` and the `SaturationVerdict`, where
the saturation thresholds are the policy's own defaults rather than values
read at runtime. A query's family is assigned by the template that generated
it, or by the marker table for a query of unknown origin, and nothing that runs
at request time gets to pick one. The two derived families, `competitor` and
`counter_evidence`, need a normalized problem, so they cannot be reached from
a raw objective. The record's statuses are `success`, `empty`, `failure`,
`timeout`, `partial`, `duplicate` and `over_budget`, and a search that ran and
found nothing is `empty` while a search whose tool broke is `failure`. The
frontier stops for one of five reasons, `family_coverage`, `source_coverage`,
`budget_exhausted`, `diminishing_novelty` and `repeated_results`, and a broken
search tool is never saturation. The budget consults the research contract's
tracker when one is supplied, so the contract's limits still bind. `store.py`
persists a query execution into `research_events`, the cost of the search into
`spend_ledger` and the frontier state into `research_checkpoints`, so the
package adds no migration and changes no table. The identifiers are derived in
the house pattern: `q_` for a query, `dq_` for a record, `evt_` for the event,
`spn_` for the spend row, each followed by sixteen hex characters of a
SHA-256. A discovery record converts into a
`research.evidence.ToolCallProvenance`, so the research gate can read a
discovery run without knowing this package exists. What does not call it is
anything: nothing outside the package imports it, so the engine is complete and
reachable as a library and gates nothing yet. The limit worth naming is the
source rule. A source the registry admits is the only source a run may search,
and a source the discovery finds is a proposal until a human reviews it. The
reasoning is in [wave-2-discovery.md](specs/wave-2-discovery.md).

**The scheduler.** `IMPLEMENTED` (`src/product_hunter/db/scheduler.py`).
`Scheduler.tick()` enqueues through `enqueue_job()` with an idempotency key of
`schedule:{name}:{occurrence}`, and `default_schedules()` holds five schedules.
The module docstring says it plainly: nothing runs it yet. There is no CLI
command and no entry point.

**The evaluation benchmark package.** `IMPLEMENTED`
(`src/product_hunter/eval/`). `cases.py` holds `TaskName` with ten tasks and
`EVALUATOR_VERSION = "eval-v1"`; `tasks.py` holds one scorer per task and
`score_case()`; `run.py` holds `run_dataset()`, `check_thresholds()`, and
`compare_runs()`; `report.py` renders the benchmark Markdown. The `ph bench`
group reaches it: `bench run` scores one model's answers against a versioned
dataset, `bench compare` compares two results and detects regressions,
`bench report` writes the benchmark report, and `bench labels` reports how
much of a dataset a human has labelled. `ph eval` still scores the Phase 1
review sheet, which is a different thing.

## Evidence provenance

`IMPLEMENTED` (the chain through `src/product_hunter/raw_store.py`,
`src/product_hunter/fetch.py`, `src/product_hunter/documents.py`,
`src/product_hunter/extraction.py`, and `src/product_hunter/normalization.py`).
The chain from bytes to an accepted record:

```text
raw bytes  ->  data/raw/<source_id>/<sha256><ext>   (RawStore, atomic write)
           ->  FetchRecord                          (data/raw/fetches.jsonl)
           ->  Document                             (text_sha256, parser_version)
           ->  DocumentChunk
           ->  evidence span                        (quote, char range, match_mode)
           ->  pain_points row                      (accepted = true)
```

Identifiers are deterministic. `span_id` is `ev_` plus 16 hex characters of the
SHA-256 of `doc_id|char_start|char_end|quote`. `candidate_id` adds the extractor
version. `pain_id` is `pp_` plus 16 hex characters of the SHA-256 of
`span_id|extractor_version`. The same span and the same extractor version always
produce the same id, so a replay reproduces them.

A fetch is recorded whether it succeeds or fails. A failed fetch carries one of
these `reason_code` values: `unknown_source`, `source_disabled`,
`terms_not_permitted`, `use_not_allowed`, `missing_use`, `no_domains`,
`unsafe_url`, `robots_blocked`, `budget_exceeded`, `http_error`, `too_large`,
`too_many_redirects`, `empty_body`, `network_error`, or `fetch_failed`. The
extraction side uses a different vocabulary, described in
[RESEARCH_CONTRACT.md](RESEARCH_CONTRACT.md).

`Store.import_run()` writes a run with `status = 'completed'` and
`current_stage = 'PAIN_EXTRACTION'`. There is no run lifecycle. The run state
machine from spec §41.2 is not implemented, and neither is `continue` from spec
§41.6 or cross-chat continuity from spec §41.8.

## Roles that are not code

**OpenCode.** It is the interaction surface. The operator runs `ph` through it,
it holds the user's model selection, and it hosts the ten project skills under
`.opencode/skills/`. It is not the authority for research state. The repository
README still says five skills; that line is stale and this wave does not fix it.

**MCP.** Spec §6 specifies MCP as an OpenCode integration surface for interactive
exploration, not as the authoritative execution mechanism for unattended runs.
There is no MCP code in this repository. An untracked `opencode.json` in the
working tree declares Qdrant and DuckDB MCP servers; it is not committed and is
not part of the product.

**Human approval.** Spec §37 divides actions into automatic and
approval-required. The automatic list is read public web, search, analyse, write
the research database, write Obsidian through the projection worker, and update
research notes through controlled writers. The approval list is destructive
shell commands, Git push, external account actions, external posting, and
irreversible operations. The code implements none of the approval list, and
that list is `BLOCKED` on a precondition the repository does not meet: there is
no external-account, posting, or Git write path to gate. The one human input the
code requires is the filled evaluation sheet, and that is offline labelling
rather than an approval gate.

## Hazards

One is live and one is closed. Both are worth recording because an operator
will hit them.

**`.gitignore` hides the worker runtime package.** `.gitignore` contains
`runtime/`, which also matches `src/product_hunter/runtime/`. The worker
runtime package is invisible to `git status`, and a plain `git add` would not
commit it. The package exists on disk but is not in the committed tree.

**The research-gate migration number was taken, and is now reserved.**
`research/persistence.py` names a planned migration
`db/migrations/0003_research_gate.sql`. The name was `0002` until
`0002_model_routing.sql` took that number, and the docstring and the wave spec
now say `0003`. The file does not exist and `0003` is not taken. The wave that
persists tool calls has to use `0003` or a later free number.

## What is not implemented

The full specification describes an eight-phase system. These requirements are
named there and have no code in this branch.

| Requirement | Section | State |
|---|---|---|
| Research run lifecycle and transitions | §41.2 | `PLANNED`, full spec only, no wave spec |
| `continue` and cross-chat continuity | §41.6, §41.8 | `PLANNED`, full spec only, no wave spec |
| Tool-call and gate-result persistence in SQLite | §6A, §34 | `PLANNED`; `research/persistence.py` holds protocols only and names `0003_research_gate.sql` |
| Global spend caps | §22 | `PLANNED`; every budget in the code is bounded inside one run, and no cap spans a day or a rolling window |
| Opportunity states and scoring | §20, §23 | `PLANNED`, full spec only, no wave spec |
| Qdrant, DuckDB, and Obsidian projections | §14, §16, §27 | `PLANNED`, full spec only, no wave spec |
| Independent-author and propagation analysis | §12 | `PLANNED`; the `propagation_edges` table exists and stays empty |
| Backup, restore, retention, drift canaries | §38B | `PLANNED`, full spec only, no wave spec |
| Human approval gates | §37 | `BLOCKED` on an external-account, posting, or Git write path that does not exist |
| MCP server or client | §6 | `PLANNED`; no MCP code exists |
| Obsidian projection handler for the outbox | §15A, §27 | `PLANNED` |

Model selection and its honest status are in
[MODEL_ROUTING_IMPLEMENTATION.md](MODEL_ROUTING_IMPLEMENTATION.md). What the code
enforces about research quality is in
[RESEARCH_CONTRACT.md](RESEARCH_CONTRACT.md).

## Deliberately absent infrastructure

Spec §38 lists components that are `OPTIONAL` and deliberately excluded from
the core: LangChain, LangGraph, LangSmith, Redis, BullMQ, Temporal, LiteLLM,
OpenTelemetry, and Prometheus. The spec's reason is that OpenCode handles the
interaction surface, the worker owns orchestration, SQLite handles the initial
queue and state, and more infrastructure should arrive only when the current
architecture cannot meet a real requirement. The spec calls this a design
decision, not an unfinished system.

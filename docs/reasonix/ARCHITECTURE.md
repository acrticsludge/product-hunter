# Product Hunter architecture

**Baseline.** This document describes the working tree. It was rewritten on
2026-10-22, after the Wave 2 final integration, when the tree held the eight
Wave 2 packages: the evidence pipeline, the discovery engine, the collection
layer, the clustering engine, the independence analysis, the opportunity
scoring, the investigation engine, and the Obsidian projection. The model
router, the research contract and gate engine, the scheduler, the worker
runtime, and the evaluation benchmark package are still here. They are
`IMPLEMENTED` like any other code. The full specification is
[Product-Hunter-Full-Spec.md](specs/Product-Hunter-Full-Spec.md).

The integration report for that pass is
[wave-2-final-integration.md](reports/wave-2-final-integration.md).

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
`migrate()` is idempotent and returns the version on a current database. The
runner splits a statement where `sqlite3.complete_statement` says one ends, so
a trigger body keeps the semicolon it needs and a semicolon inside a string
literal does not cut a statement in half.

## The forty-five tables

`IMPLEMENTED` (`db/migrations/0001_initial.sql` through
`db/migrations/0007_independence.sql`). The schema holds forty-five tables
across seven migrations.

| Migration | Number | Tables |
|---|---|---|
| `0001_initial.sql` | 1 | `sources`, `fetches`, `communities`, `documents`, `authors`, `author_links`, `evidence`, `pain_points`, `evidence_pain_links`, `propagation_edges`, `research_runs`, `research_artifacts`, `research_events`, `research_checkpoints`, `jobs`, `stage_attempts`, `projection_tasks`, `spend_ledger`, `human_decisions` |
| `0002_model_routing.sql` | 2 | `model_catalogs`, `model_inventory`, `model_role_bindings`, `model_executions` |
| `0003_research_gate.sql` | 3 | `tool_calls`, `research_stage_executions`, `research_stage_artifacts`, `research_claims`, `stage_gate_results` |
| `0004_collection.sql` | 4 | `collection_source_state`, `collection_run_state`, `collection_items` |
| `0005_evidence_pipeline.sql` | 5 | `document_versions`, `evidence_observations`, `evidence_lineage` |
| `0006_clustering.sql` | 6 | `clustering_runs`, `clusters`, `cluster_versions`, `cluster_members`, `cluster_lineage`, `cluster_decisions` |
| `0007_independence.sql` | 7 | `author_identity_links`, `community_links`, `author_clusters`, `author_cluster_members`, `independence_reports` |

The numbering is the pipeline order of spec §10: collect, then evidence, then
cluster, then independence. The four Wave 2 migrations were each written as
`0003_*` in a worktree that forked before the research gate took the number,
so the integration renumbered them. The migration loader refuses a duplicate
number and refuses a gap, so an unreconciled merge fails at `db init` rather
than silently.

| Group | Tables |
|---|---|
| Collection and provenance | `sources`, `fetches`, `communities`, `documents`, `authors`, `author_links` |
| Analysis | `evidence`, `pain_points`, `evidence_pain_links` |
| Evidence lifecycle | `document_versions`, `evidence_observations`, `evidence_lineage` |
| Collection state | `collection_source_state`, `collection_run_state`, `collection_items` |
| Clustering | `clustering_runs`, `clusters`, `cluster_versions`, `cluster_members`, `cluster_lineage`, `cluster_decisions` |
| Independence | `author_identity_links`, `community_links`, `author_clusters`, `author_cluster_members`, `independence_reports` |
| Research registry | `research_runs`, `research_artifacts`, `research_events`, `research_checkpoints` |
| Execution | `jobs`, `stage_attempts`, `projection_tasks` |
| Model routing | `model_catalogs`, `model_inventory`, `model_role_bindings`, `model_executions` |
| Research gate | `tool_calls`, `research_stage_executions`, `research_stage_artifacts`, `research_claims`, `stage_gate_results` |
| Reserved, no writer | `propagation_edges`, `human_decisions` |

Two tables still have no writer anywhere in `src/`: `propagation_edges` and
`human_decisions`. `propagation_edges` gained its columns in the independence
migration but still holds no row, because nothing writes a propagation edge
yet. `human_decisions` is the approval record of spec §37, and there is no
external-account or posting path for it to record.

`spend_ledger` now has a writer: `src/product_hunter/discovery/store.py` writes
one row per discovery record. That is the only writer, so the budget
dimensions measured from it are still narrow.

`research_stage_artifacts` is not `research_artifacts`. The first holds the
files of an extraction run, the second holds the stage outputs the research gate
judges. They are different things and they are not merged.

`documents.live_status` is added by the evidence migration. The independence
migration wanted the same column with the same definition, and it is left to
the evidence migration to add it.

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
fields as design intent; the code implements the container, not the contents.
Nothing in the codebase calls `load_checkpoint()` to resume work, so the resume
orchestration does not exist.

A checkpoint written by a leased worker is fenced. `save_checkpoint()` takes
optional `worker_id` and `lease_epoch`; a write with only one of the two is
refused, and a write with both is refused unless the caller holds a live lease
on a job for that run and stage. The lease is compared against the store's
clock, not against a timestamp the caller supplies, so a caller cannot make an
expired lease look live. A write with neither is the control plane, not a
leased worker, and is unfenced.

The check and the insert share one transaction. A caller that already owns a
transaction keeps it and commits it; a caller with none gets one from
`save_checkpoint()`. The fence and the write therefore cannot be separated by
another writer.

The check itself lives in `src/product_hunter/db/leases.py` and is shared. The
research gate's five write paths use the same one.

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
`<ExceptionType>: <message>`. `LeaseLost` stops the batch. The Obsidian target has a handler (`src/product_hunter/projection/engine.py`)
and `ph project --outbox` runs the outbox. The Qdrant target now has a handler
too (`src/product_hunter/semantic/index.py`, `ph index sync`); `ph db outbox`
still lists every task.

A crash after the SQLite commit leaves the task `pending`, and a later
`process_outbox()` picks it up. A crash during the projection leaves the task
`processing` until its lease expires, then it returns to `pending`, or to
`failed` when the attempt ceiling is reached.

Three success words differ, and they stay different. A job that finishes is
`succeeded`. A projection task that finishes is `done`. A research run that
finishes is `completed`.

### The Obsidian projection

`IMPLEMENTED` (`src/product_hunter/projection/`, `ph project`). The vault is a
projection of the database, never a second copy of the truth. Nothing reads a
note back, and a note a human edited is overwritten by the next projection
without a flag saying so; two-way synchronisation is not built.

The layer is read-only against SQLite and writes only under the vault root.
A projection is deterministic (no wall-clock time reaches a note, the
reference date is the newest timestamp in the data), idempotent (a note is
written only when its bytes differ, so a second run touches no file and
changes no file time), and regenerable (the vault can be deleted and rebuilt
from the database, and a note whose opportunity is gone is removed).

`projection_tasks` is how the work arrives. `Store.import_run()` enqueues a
task with the target `obsidian` in the same transaction that writes the
evidence, and `Projector.handle_projection_task()` rebuilds the vault from
that task. A failure lets the exception out, so the outbox records it and
retries with a backoff; the database is never touched by the projection, so a
failed projection cannot roll anything back.

`src/product_hunter/projection/contract.py` holds the DDL of the tables the
projection reads when they exist (`opportunities`, `opportunity_scores`,
`opportunity_evidence`, `opportunity_claims`, `experiments`,
`research_requests`) and the column map for reading them. The reader asks
`PRAGMA table_info` for the columns a table has and selects only those, so a
table with a different shape degrades the projection instead of crashing it.
The `opportunities` table does not exist in this branch, so the reader falls
back to the pain points and the evidence, and the report fields that only a
scored opportunity can fill read `not recorded` until the table lands.

Scraped text is data. `src/product_hunter/projection/sanitize.py` escapes
HTML, wikilinks, embeds, Templater expressions, Dataview queries, comments,
headings, table rows, blockquotes, code fences and frontmatter delimiters,
and blocks every URL whose scheme is not `http` or `https`. A value the
database does not hold reads `not recorded`, never a zero.

### The semantic index

`IMPLEMENTED` (`src/product_hunter/semantic/`, `ph index`). The index is a
projection of the database, not a second copy of the truth. SQLite stays
authoritative, and the index is rebuilt from it the way the Obsidian vault is:
read the whole state, write what changed, remove what is gone.

The layer is read-only against SQLite and writes only to the Qdrant collection
named in `config/semantic.yaml` (`product_hunter_evidence` by default, the
same name the Qdrant MCP server in `opencode.json` is pointed at). A vector
is derived work: the authoritative row is written first, in its own
transaction, together with the projection task that will index it, and a
Qdrant failure never rolls back committed evidence.

The space id is the guard. It is a digest of the embedding provider, model,
model version and dimensions, and it is written into the collection metadata,
into every payload and into every search filter. The configured model is
BAAI/bge-m3 running locally, 1024 dimensions, with the weights fetched on
first use and kept in `data/models`. When the configured model changes, the
space id changes with it, `ensure_ready()` raises `EmbeddingSpaceMismatch`,
and the operator runs `ph index rebuild`, which drops the collection,
recreates it under the new space and re-embeds everything. There is no path
in this wave that writes a vector of one space into a collection that holds
another.

`projection_tasks` carries a `target="qdrant"` row as well as the obsidian
one. `Store.import_run()` enqueues it in the same transaction that writes
the evidence, and `SemanticIndex.handle_projection_task()` indexes only the
entities the task names. A failure lets the exception out, so the outbox
records it and retries with a backoff; the database is never touched by the
index.

`ph index sync` reconciles, `ph index rebuild` replaces the collection,
`ph index search` answers a question in words, and `ph index status` reports
the space, the collection and the counts. `ph doctor` reports the provider,
the model, the cache, the device, the dimensions and the collection, and
answers on a machine with no Qdrant and no ML stack.

## The Wave 2 subsystems

Eight packages arrived with the Wave 2 sessions. They are listed in the order
of the pipeline of spec §10, and each one says what calls it.

**The evidence pipeline.** `IMPLEMENTED` (`src/product_hunter/evidence.py`,
`src/product_hunter/db/evidence.py`). It owns the chain from a document to a
span: a document carries a content version, a span carries an observation
history and a lineage, and the derived strength and reliability carry the
version of the rule that produced them, so a later rule cannot silently
rewrite an old row. `Store.import_document_version()` moves a document to its
next content version and leaves the one it leaves behind in
`document_versions`. Nothing calls it yet.

**The discovery engine.** `IMPLEMENTED` (`src/product_hunter/discovery/`). It
owns what to search: the ten query families of spec §7A, the controlled
variants, the novelty and saturation bookkeeping, the per-run discovery
budgets, and the persistent frontier. A frontier query stores its parent
query, its family, its novelty yield, its duplicate yield, its source yield,
its independent-author yield, and its last-seen time. `discovery/store.py`
writes a discovery record as a tool-call-shaped row and a `spend_ledger`
entry. Nothing calls it yet.

**The collection layer.** `IMPLEMENTED` (`src/product_hunter/collection/`,
`src/product_hunter/fetch.py`). It owns how to fetch: the collector ladder
from an official API through a feed, direct HTTP, a local crawler, browser
automation, to a managed scraper, and the refusal to run a rung that is not
installed. `collection_items` holds one row per attempt, so a failure is not
erased by the retry that follows it. The reason vocabulary separates a
terminal refusal (`robots_blocked`, `terms_not_permitted`, `unsafe_url`) from a
failure (`http_error`, `network_error`) and from a capture that produced
nothing. `RequestBudget` in `fetch.py` holds the per-source interval and the
per-run cap. Nothing calls it yet.

**The clustering engine.** `IMPLEMENTED` (`src/product_hunter/clustering/`,
`src/product_hunter/db/clustering.py`). It answers one question: do these
pieces of evidence describe the same underlying problem? It does not answer
whether the problem is a good startup. It normalises, compares, generates
candidates, assigns, merges and splits, and records every decision with the
model that made it and the version of the prompt. The history is never
rewritten: a membership is closed with a `valid_to` and never deleted, and a
lineage edge points from the old cluster to the new one. The nearest-neighbour
comparison is a full scan of the corpus today, which is recorded as a
`shortcut:` marker. Nothing calls it yet.

**The independence analysis.** `IMPLEMENTED`
(`src/product_hunter/independence/`, `src/product_hunter/db/independence.py`).
It answers how many independent people a body of evidence really holds. The
identity resolver resolves authors and communities, the propagation detector
finds reposts, quotes, cross-posts and syndication, and the analytics layer
counts raw mentions, canonical observations, independent authors, uncertain
authors, independent communities, propagation-adjusted evidence and source
concentration. A model may supply a signal; the policy refuses a
configuration that would let a weak or model-supplied signal reach a merge
threshold, so the count is computed by code and not declared by a model.
Nothing writes a propagation edge yet, and nothing calls the package.

**The DuckDB analytical layer.** `IMPLEMENTED`
(`src/product_hunter/analytics/`). SQLite stays the authoritative transactional
store; DuckDB is a derived analytical surface, rebuilt from SQLite on demand.
The layer opens SQLite read-only through `product_hunter.db.connection.connect`,
so it has no write path to the authoritative store at all. The mirrored tables
are discovered from the SQLite schema (`PRAGMA table_info`), and every mirrored
row carries a `_ph_hash`, the SHA-256 of its canonical form, computed on the
SQLite side only. `snapshot()`, `refresh()`, `rebuild()` and `reconcile()` are
the four operations; SQLite wins on any disagreement, and `reconcile()` names
the added, removed and changed keys per table. The views answer the analytical
questions: evidence mentions, pain point evidence and trend, source yield,
community evidence, independence summary, source concentration, opportunity
evidence trend, score history, run analytics and run budget, collection health,
discovery yield, query family performance, temporal persistence. A view over a
table that does not exist yet (the canonical `opportunity_scores`,
`opportunities`, `opportunity_claims`, `opportunity_evidence`, `experiments`
and `research_requests` tables arrive with a later wave) is skipped and
reported, not faked. The DuckDB file is `db/product_hunter.duckdb` and is not
committed. Nothing calls the package yet, and no worker or CLI command drives
it.

**The opportunity scoring.** `IMPLEMENTED` (`src/product_hunter/opportunity/`).
Potential and Confidence are two separate scales and never share a term.
Potential considers severity, frequency, the willingness-to-pay tier, the
reachable market, the competitive gap, buildability and recurrence.
Confidence considers independent authors discounted for identity uncertainty,
community diversity, the source-reliability mix, temporal persistence, the
propagation-adjusted count, extraction agreement and the counter-evidence
coverage. The willingness-to-pay tiers are `W0` to `W4`, and a niche override
is recorded with its reason rather than silently applied. Every score is a
content-addressed snapshot that names the policy version and the input
snapshot ids. Nothing calls it yet.

**The investigation engine.** `IMPLEMENTED`
(`src/product_hunter/research/investigation/`). It researches an opportunity
instead of only scoring it: competitor search, counter-evidence, coverage,
the investigation budget, the checkpoints, and the startup-killer pass. The
query families come from code-side templates, not from the model. A zero
result is only reachable on a complete coverage, so an incomplete search reads
as incomplete rather than as "no competitor exists". The engine bridges into
the research gate through `investigation_stage_execution()`. It has no
persistence: the checkpoint is a value, and storing it arrives with the wave
that owns the migration.

**The Obsidian projection.** Described above. `ph project` builds the vault
and `ph project --outbox` drains the outbox through it.

### The seam each of them waits for

Seven of the eight are libraries with a store and no caller. The projection
is the exception: it has a CLI command and an outbox handler. The missing
piece is a stage handler that claims a job, runs one stage, records its tool
calls and artifacts in the gate's tables, and advances the run. The gate can
already refuse a run; nothing yet asks it to.

## Subsystems that exist but are not on the pipeline

Each of these is `IMPLEMENTED` and reachable, and each has one thing that does
not call it yet.

**The model router and its catalogue.** `IMPLEMENTED`
(`src/product_hunter/llm/catalog.py`, `src/product_hunter/llm/discovery.py`,
`src/product_hunter/llm/routing.py`, `src/product_hunter/db/model_routing.py`,
`config/model_routing.yaml`). The router resolves a role to a `provider/model`
identity through an ordered chain of candidates and takes the first available
one. It routes two things: the `ph models` command, and the extraction run.
`ph extract` resolves the `extraction` role against the live catalogue, binds
the selected model to the provider, and records the selection, the catalogue
version, and the fallback index in the run manifest. A router that cannot
answer falls back to the model pinned in `config/extraction.yaml`, and the
manifest says which one ran. No model is named in the business logic: the
chains are patterns in `config/model_routing.yaml`, resolved against a
catalogue discovered from OpenCode at runtime. The full story is in
[MODEL_ROUTING_IMPLEMENTATION.md](MODEL_ROUTING_IMPLEMENTATION.md).

All thirteen roles of spec §3 exist as `ModelRole` members and as configured
chains in `config/model_routing.yaml`: `extraction`, `classification`,
`normalization`, `deduplication`, `entity_resolution`, `clustering`,
`validation`, `investigation`, `competitor_research`, `counter_evidence`,
`scoring`, `reporting`, `synthesis`. Only `extraction` is bound to a live
call path. The Wave 2 engines take a resolved model identity as an input and
record it, which is the seam a caller resolves the role through: the
clustering adjudicator takes `provider`, `model`, `model_version` and
`prompt_version` and writes them into every decision row, and the similarity
layer takes the embedding model's identity and derives the vector space id
from it. No Wave 2 package resolves a role itself, and none of them names a
model.

**The research contract and gate engine.** `IMPLEMENTED`
(`src/product_hunter/research/`, `config/research_contract.yaml`). The contract
is a typed schema, the gate turns evidence into a pass or fail decision with
stable `ViolationCode` values, and the budget refuses a call that would exceed a
hard limit. A gate verdict measures an overrun against the limits the contract
declares, so a tracker built from a looser budget cannot widen them. An
artifact counts towards a threshold only when it is valid, belongs to the stage
being judged, and traces to a successful call of that stage, and the same rule
governes the two artifact-reading quality predicates. A call filed under another
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
promotion time, and the shipped contract sets the flag to true.

The gate has a home now. `db/migrations/0003_research_gate.sql` holds the
evidence the gate judges — `tool_calls`, `research_stage_executions`,
`research_stage_artifacts`, `research_claims` — and the verdicts it produced,
`stage_gate_results`. `src/product_hunter/db/research_gate.py` writes and reads
them, rebuilds a `RunEvidence` from the stored rows, and exposes
`require_promotable()`, which raises `GateBlocked` when a run may not be
promoted. The budget is measured from the stored rows, so an over-budget run is
refused. A verdict carries the fingerprint of the contract it was judged under,
and a verdict judged under a different contract is not promoted.

What does not call it is still the pipeline: no worker handler records a tool
call or an artifact, no CLI command evaluates a run, and there is no promotion
path to refuse. The gate is enforceable and enforced in a library, and the
caller that makes it bite is the next wave's work. What the code enforces
about research quality is in [RESEARCH_CONTRACT.md](RESEARCH_CONTRACT.md).

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

**The SubQ Snap decision provider.** `IMPLEMENTED`
(`src/product_hunter/llm/decision.py`, `src/product_hunter/llm/subq.py`,
`config/decision_routing.yaml`). It answers structured questions about text:
a yes/no gate, a label out of a set, a number on a scale. `decision.py` owns
the shapes, the health states, the provenance and the chain;
`build_decision_router()` reads `config/decision_routing.yaml` and puts the
providers in the order the file lists. The first provider that answers wins, a
provider that is disabled or unavailable is skipped, and a provider that
cannot answer hands the work to the next one, so the text model is the
fallback and SubQ is never a dependency. `subq.py` owns the HTTP client, with
the timeout, the retry, the rate limit, the request hash and a bounded answer
cache, and the provider, with the capability report, the circuit, the spend
ledger and the budget. The credential is read from the environment, held in a
`SecretStr`, and never reaches a record. The Decisions API decides over one
state and has no batch endpoint, so a batch is one request per item and the
capability report says so. Nothing calls it yet, and the benchmark gate of
spec §38A is what would promote it.

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

### What the Wave 2 packages added to the chain

The evidence pipeline extends the chain with three things the earlier tree did
not have.

A document carries a `content_version`, so a re-capture of the same URL is a
new version of the same document rather than a conflict. The version it
leaves behind stays in `document_versions`, and the row keeps the first and
last time it was seen.

A span carries an observation history. The same span seen again in another
version of the document is another observation of the same span, written to
`evidence_observations` with the extractor, the model, the prompt version and
the quote that was seen. `evidence_lineage` records every lifecycle event: a
document event, a span event, a rejection.

The derived facts carry their rule version. The strength and the reliability
of a span are written with the version of the rule that produced them, so a
later rule cannot silently rewrite an old row, and a recomputation is an
explicit act.

The database enforces the weakest link of the chain. `0005_evidence_pipeline.sql`
installs three triggers: a pain point cannot be inserted without an evidence
span that has a non-empty quote and a possible character range, and an
evidence link cannot point at a pain point that a different span backs. The
check is in the schema, so it holds for every writer, including one that does
not know the rule.

The clustering layer extends the chain with the cluster: a membership that
closes is closed with a `valid_to` and never deleted, a version row holds one
snapshot of a cluster, and a lineage edge points from a merged or split
cluster to its successor. The opportunity layer extends it with the score: a
snapshot is content-addressed, names the policy version it was computed
under, and names the input snapshot ids it was computed from.

The independence layer extends it with the author: an author carries an
identity key, an identity confidence, the basis of that confidence, a
deletion status and a first and last seen date. The identity key is a hash.
No raw handle is stored in it.

## Roles that are not code

**OpenCode.** It is the interaction surface. The operator runs `ph` through it,
it holds the user's model selection, and it hosts the ten project skills under
`.opencode/skills/`. It is not the authority for research state. The README
lists the same ten skills.

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

Two are closed, two are closed by the Wave 2 integration, and one is a
standing limit.

**`.gitignore` hides the worker runtime package.** `CLOSED`. `.gitignore` holds
`/runtime/`, and the leading slash anchors the pattern to the repository root,
so it matches the top-level runtime directory and not
`src/product_hunter/runtime/`. All six files of the worker runtime package are
tracked, and `git check-ignore src/product_hunter/runtime/worker.py` reports
nothing. An earlier revision of this document recorded this as a live hazard on
the strength of the pattern text alone; the pattern is anchored and the package
is in the committed tree.

**The research-gate migration number was taken, and is now reserved.**
`CLOSED`. `research/persistence.py` named a planned migration
`db/migrations/0003_research_gate.sql`. The name was `0002` until
`0002_model_routing.sql` took that number, and the docstring and the wave spec
now say `0003`. The file exists and holds the five tables the gate needs, and
`0003` is taken by it.

**Four Wave 2 migrations claimed the number `0003`.** `CLOSED`. Each of the
collection, evidence, clustering and independence sessions wrote a `0003_*`
migration in its own worktree, which forked before the research gate took the
number. The migration loader refuses a duplicate number and refuses a gap, so
the break failed loudly at `db init` rather than silently. The integration
renumbered them to `0004`, `0005`, `0006` and `0007` in the pipeline order of
spec §10, and the one column two of them both wanted,
`documents.live_status`, has one owner.

**The migration runner split a statement on a semicolon.** `CLOSED`. The
runner cut the SQL on `;`, so a trigger body was cut in half and a semicolon
inside a string literal ended a statement early. The evidence migration needs
triggers, so the runner now splits where `sqlite3.complete_statement` says a
statement ends.

**The gate has a home but no caller.** `OPEN`. The evidence, the verdicts, the
budget measurement, and the fail-closed `require_promotable()` all exist and
are tested. Nothing in the running system calls them yet: no worker handler
records a tool call or an artifact, no CLI command evaluates a run, and there
is no promotion path to refuse. The gate is enforceable and enforced in a
library, and the caller that makes it bite is the next wave's work.

**Seven of the eight Wave 2 packages have no caller.** `OPEN`. The evidence
pipeline, the discovery engine, the collection layer, the clustering engine,
the independence analysis, the opportunity scoring and the investigation engine
are complete, tested libraries, and nothing in the running system calls them.
The projection is the exception. The same missing piece causes both hazards:
a stage handler that claims a job, runs one stage, records its tool calls and
artifacts in the gate's tables, and advances the run.

## What is not implemented

The full specification describes an eight-phase system. These requirements are
named there and have no code in this branch.

| Requirement | Section | State |
|---|---|---|
| Research run lifecycle and transitions | §41.2 | `PLANNED`, full spec only, no wave spec |
| `continue` and cross-chat continuity | §41.6, §41.8 | `PLANNED`, full spec only, no wave spec |
| `spend_ledger` writes and global spend caps | §22 | `PARTIAL`; the discovery store writes one row per discovery record, and the other budget dimensions still have no writer, so the gate reports them as unmeasured rather than as zero |
| Opportunity states and scoring | §20, §23 | `IMPLEMENTED` as a library (`src/product_hunter/opportunity/`); the `opportunities` table and the lifecycle in the database are `PLANNED`, and no caller |
| Qdrant projection | §14 | `IMPLEMENTED` as a library (`src/product_hunter/semantic/`); `ph index sync`, `rebuild`, `search`, `status` drive it, and the store enqueues a `target="qdrant"` task per imported run. `ph doctor` reports its state. DuckDB stays `PLANNED` |
| Obsidian projection | §27 | `IMPLEMENTED` (`src/product_hunter/projection/`); two-way interaction is `PLANNED` |
| Independent-author and propagation analysis | §12 | `IMPLEMENTED` as a library (`src/product_hunter/independence/`); `propagation_edges` still has no writer, and the analysis has no caller |
| Embeddings and a vector store | §14 | `PARTIAL`; the similarity layer takes an embedding identity and a space id, the local embedding model is imported lazily and was never run, and the vector store is `PLANNED` |
| Backup, restore, retention, drift canaries | §38B | `PLANNED`, full spec only, no wave spec |
| Human approval gates | §37 | `BLOCKED` on an external-account, posting, or Git write path that does not exist |
| MCP server or client | §6 | `PLANNED`; no MCP code exists |
| Obsidian projection handler for the outbox | §15A, §27 | `IMPLEMENTED` (`src/product_hunter/projection/engine.py`) |
| A persistent query frontier with novelty and saturation | §7A | `IMPLEMENTED` (`src/product_hunter/discovery/`); it has no caller |
| A stage handler that records tool calls and artifacts | §6A | `PLANNED`; the stores and the fence exist, and no handler writes to them yet |
| The scheduler has no entry point | §18 | `IMPLEMENTED` as a library; nothing runs `Scheduler.tick()` and no CLI command calls it |

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

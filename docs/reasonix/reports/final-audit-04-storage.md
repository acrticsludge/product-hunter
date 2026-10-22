# Final audit 04: the storage architecture

**Run ID:** final-audit-04-storage-2026-10-22
**Probe:** Final Audit Probe #4. **Branch:** `main`. **Base:** `00a6ca3`
**Risk:** declared `medium`, floor `medium`. The adapter floor is `medium` and
nothing in the radius is a high-risk path: the only file touched is a report
under `docs/`.
**Adapter:** Product Hunter, 8 forbidden rules in force
**Spec:** [../specs/wave-2-obsidian-reporting.md](../specs/wave-2-obsidian-reporting.md),
the projection contract this audit checks. The two Wave 3 layer specs live on
their own branches, `wave-3-qdrant.md` and `wave-3-duckdb-analytics.md`.

```text
GOAL: audit the four-layer storage architecture (SQLite authoritative, Qdrant
      semantic index, DuckDB analytical layer, Obsidian downstream view) across
      main and the eight Wave 3 branches, and report what is actually built
TIER: free (models used per node in order)
  stage 0-3   orchestrator: minimax-m3.1-flash-free
  stage 4     orchestrator: minimax-m3.1-flash-free. The report is the
              deliverable; no implement node was needed for one document.
  stage 5     verify-code: the orchestrator ran the adapter's gate command
              directly rather than dispatching a node for a single command.
  stage 6     harden: step-5-preview-free
  stage 7     review: step-5-preview-free, verdict SHIP
  stage 8-10  orchestrator: minimax-m3.1-flash-free
RISK: declared=medium, adapter floor=medium (docs only; no high-risk path)
ADAPTER: Product Hunter [repo] 8 forbidden rules in force
STAGES: 11/11 complete, skipped: 3 (1 DEFINE, 2 ARCHITECT, 3 PLAN). A read-only
  audit with one deliverable path and an explicit check list has no open decision
  to grill, no code to design and no plan to write. Stage 5b visual was not
  required; the adapter sets visualRequired to never and no UI file is in the radius.
GATES: uv run pytest -> 2138 passed in 42.79s, exit 0
CHANGES: 1 file, docs/reasonix/reports/final-audit-04-storage.md, 952 lines. No
  code touched.
HARDEN: verdict `object` on the storage architecture, with Must-fix findings on
  unbounded growth and on observability. The node took five dispatches to land:
  one provider HTTP 429, two cancelled by the environment, one cross-rung
  attempt on the other model family that failed because that model was
  unavailable. The orchestrator ran the same pass directly in the meantime, and
  the two agree on every point. 9 findings: H1 the outbox stores unredacted
  handler error text, H2 nothing ever deletes a projection task, a job or a
  spend-ledger row, H3 the raw and text stores have no retention, H4 the DuckDB
  mirror keeps its state only inside the DuckDB file, H5 operation_events is
  never pruned, H6 worker_health accumulates a row per process start, H7 a
  DuckDB mirror failure leaves no durable record, H8 lease recovery leaves only
  a counter, H9 a clustering embedding failure may leave no durable record.
  Clean: credential handling, path and identifier safety, URL admission,
  bounded caches, the authority boundary.
REVIEW: SHIP. 0 Must-fix. 3 Should-fix, all applied: the F12 isoformat citation
  was repointed to the outbox write, the F1 line range was corrected, and the
  F12 available_at and lease_expires_at comparisons were split apart. 4
  Nice-to-have, all applied: this header block, a spec link, the Lean line and a
  trimmed path note. Lean: 0 lines possible. The deliverable is a new document,
  not a diff to shrink.
WAIVERS: one. The report is left uncommitted in the working tree. The brief said
  READ-ONLY and do not modify anything, and every probe report is untracked in
  this same tree, so committing one of them mid-flight would race the others.
  Every commit condition was otherwise met: the gate is green, the review
  returned ship, the path is inside the declared radius, the risk floor is
  medium and the radius was declared at stage 0. The integrator should commit
  the probe reports together.
STOPPED BECAUSE: all pass and review ship
NEXT: the integrator renumbers the three 0008 migrations, adds a qdrant outbox
  drain, gives the autonomous engine a projection stage and pages the Qdrant
  scroll. Fixes 1 to 5 are the merge blockers; the rest can follow.
```

**Path note.** The brief asked for `docs/resonix/reports/final-audit-04-storage.md`.
`resonix` is the spelling this tree was renamed away from; all nine trees
inspected here carry `docs/reasonix`, and `7a32a04` cleaned the misspelling out
on purpose. This report lands at `docs/reasonix/reports/final-audit-04-storage.md`,
the only docs root that exists.

---

## 1. The trees audited

`git worktree list` and `git branch -a` gave all eight. The original paths are not
obvious: two live outside the project folder, one under a local opencode worktree
root, one under the system temp directory.

| Branch | Worktree path | Head |
|---|---|---|
| `wave3/opencode-control-plane` | `<repo>/.worktrees/wave3-opencode-control-plane` | `5e45c63` |
| `wave3/autonomous-operation` | `C:/Anubhav/Web Dev Projects/product-hunter-w3a` | `1efc328` |
| `wave3/source-expansion` | `C:/Anubhav/Web Dev Projects/product-hunter-w3c` | `a9540ee` |
| `wave3/duckdb-analytics` | `<repo>-wt/wave3-analytics` | `3474e58` |
| `wave3/observability-evaluation` | `<repo>-wt/wave3-observability` | `317e7f2` |
| `wave3/scraping-ladder` | `<repo>-wt/wave3-scraping-ladder` | `5f66d46` |
| `wave3/subq-integration` | `~/.local/share/opencode/worktree/44767c/wave3/subq-integration` | `172a74f` |
| `wave3/qdrant-semantic` | `%LOCALAPPDATA%/Temp/opencode/wt-wave3-qdrant` | `357dd5e` |

`main` is `00a6ca3`. All eight branches are unmerged; `main` is the merge base for
every one of them, and each branch forked from the same base, which is where the
migration-number collision in F7 comes from.

Five branches touch storage. `qdrant-semantic` adds the semantic layer and
`duckdb-analytics` adds the analytical layer. `autonomous-operation`,
`opencode-control-plane`, `source-expansion` and `observability-evaluation` each
add one migration, and the first two also change `db/connection.py` or
`db/jobs.py`. `scraping-ladder` and `subq-integration` leave the storage layer
alone.

## 2. Architecture verdict

**The four-layer architecture is real and the authority boundary holds in code.**
SQLite is the only store with a write path. Qdrant, DuckDB and the Obsidian vault
are all derived, and all three open SQLite through a connection that is
read-only at the driver level, so they have no write path to the authority even by
accident.

The chain is implemented as described:

- **SQLite → authoritative.** Seven migrations (`db/migrations/0001..0007`),
  a store that owns the write transactions, a transactional outbox, deterministic
  ids, content hashes, fenced leases and durable checkpoints.
- **Qdrant → derived semantic index.** `wave3/qdrant-semantic`, package
  `src/product_hunter/semantic/`. Reads SQLite read-only, writes vectors whose
  point id is a digest of the entity, and refuses to write into a collection that
  holds another vector space.
- **DuckDB → derived analytical layer.** `wave3/duckdb-analytics`, package
  `src/product_hunter/analytics/`. Mirrors the schema rather than listing it,
  carries a per-row hash computed on the SQLite side, and can be dropped and
  rebuilt deterministically.
- **Obsidian → downstream view.** Package `src/product_hunter/projection/`, on
  `main` since the wave 2 reporting wave. Reads SQLite read-only, writes only
  under the vault root, and writes a note only when its bytes differ.

**The shape is sound. The wiring is not finished.** Two of the four layers are
never refreshed by the product itself (F1, F2), one of them silently prunes only
part of its contents past a hard cap (F4), and the three derived layers do not
agree on what a schema mismatch should do (F3).

### 2.1 SQLite, the authority

| Concern | Where | Verdict |
|---|---|---|
| Migration loader | `src/product_hunter/db/migrations.py:23-69` | Fails closed on a duplicate number, a gap, and a database newer than the code |
| Migration atomicity | `migrations.py:72-87` | One `BEGIN IMMEDIATE` per file, `PRAGMA user_version` inside it |
| Write connection | `db/connection.py:37-59` | `isolation_level=None`, WAL, `foreign_keys=ON`, `busy_timeout=5000` |
| Read-only connection | `db/connection.py:49-53` | `file:...?mode=ro` URI; the file is never created |
| Import transaction | `db/store.py:869-953` | One transaction for the run, its rows, its provenance and its projection tasks |
| Deterministic ids | `db/store.py:1267-1268`, `db/checkpoints.py:310-311` | `prefix + sha256(basis)[:16]` |
| Content hashes | `documents.py:90`, `documents.py:306`, `db/store.py:1058` | sha256 over raw bytes, text and artifact bytes |
| Idempotent imports | `db/store.py:153`, `db/store.py:556`, `db/store.py:665` | `ON CONFLICT DO UPDATE` and `INSERT OR IGNORE` |
| Fenced leases | `db/outbox.py:163-215`, `db/jobs.py:_owns` | worker id plus a monotonic `lease_epoch` |
| Checkpoints | `db/checkpoints.py:55-193` | Lease-fenced, versioned, same version with different state is a conflict |

The outbox is the mechanism that keeps the layers honest, and it is implemented
properly: `enqueue_projection` derives the task id from the idempotency key
(`db/outbox.py:75`), returns the existing task for the same key, and raises
`OutboxConflict` when the same key carries different content
(`db/outbox.py:76-87`). A claim bumps the lease epoch; completion and failure
both require the worker id, the epoch and an unexpired lease to match
(`db/outbox.py:194-205`, `db/outbox.py:251-270`). A downstream failure cannot
roll back committed evidence, because the projection runs in its own transaction
after the authority has already committed.

### 2.2 Qdrant, the derived semantic index

`wave3/qdrant-semantic` adds `src/product_hunter/semantic/` (six modules, 2900
lines) and `config/semantic.yaml`.

The guard that matters is `SemanticIndex.ensure_ready`
(`semantic/index.py:152-185`): it reads the collection's own metadata and raises
`EmbeddingSpaceMismatch` when the held `space_id` or the vector width differs
from the configured space. A collection holding another space is never written to.
The answer it gives is a rebuild, and `rebuild()` (`semantic/index.py:187-192`)
deletes the collection and syncs again. `VectorSpace` computes `space_id` from
provider, model, model version and dimensions
(`clustering/similarity.py:66,79-85`), and `VectorSpace` validates its own id
(`similarity.py:70-76`), so a hand-edited space id is refused.

Point identity is a digest of the entity, not of its content
(`semantic/vectors.py:36-52`): `uuid(sha256(entity_type + "\x1f" + entity_id))`.
A changed entity lands on the same point and replaces it. The payload carries the
full model and version metadata (`semantic/vectors.py:55-76`):
`embedding_provider`, `embedding_model`, `embedding_model_version`, `space_id`,
`dimensions`, `index_version`, `content_hash`, `created_at`, `updated_at`.
The collection metadata carries the same space identity
(`semantic/index.py:509-517`).

The sync reconciles rather than appends. A point whose payload is unchanged is
skipped (`semantic/index.py:240-242`, `semantic/vectors.py:82-93`), a full sync
prunes what the store no longer holds (`semantic/index.py:471-486`), and a named
subset never prunes (`semantic/index.py:196-202`, `245-246`).

**BGE-M3.** The provider is `LocalBGEM3Provider`
(`semantic/providers.py:129-222`). The model identifier is validated as an
org/name pair before anything is fetched (`providers.py:114-126`), so a scraped
URL or a model's own answer cannot choose what gets downloaded. The model is
loaded once and reused (`providers.py:236-245`), the device falls back from an
explicit `cuda` only by refusal rather than silently (`providers.py:247-261`),
and `embed_all` rejects a vector of the wrong width before it reaches the
adapter (`semantic/embeddings.py:30-44`). The adapter is a plain `httpx` REST
client with a bounded retry budget on 429 and 5xx
(`semantic/qdrant.py:266-300`) and scrubs the API key out of every message
(`qdrant.py:321-325`). Model quality is out of scope here; the storage contract
around it holds.

### 2.3 DuckDB, the derived analytical layer

`wave3/duckdb-analytics` adds `src/product_hunter/analytics/` (four modules) and
two test files. It adds no CLI command and no outbox task.

`Analytics.open` (`analytics/ingest.py:168-194`) opens SQLite with
`read_only=True`, refuses a DuckDB path that resolves to the SQLite path, and
refuses a missing database before anything is written. The DuckDB connection is
the only writable one, and the layer writes nothing but its own tables.

The mirror is schema-driven (`analytics/tables.py:185-200`): the table list comes
from `sqlite_master`, so a table a later wave adds is mirrored without an edit.
A table whose key can hold null is refused rather than half mirrored
(`tables.py:261-281`). The row hash is computed on the SQLite side and stored in
the mirrored row as `_ph_hash` (`tables.py:203-211`, `ingest.py:525-543`), because
SQLite hands a timestamp back as a string and DuckDB hands the same column back as
a datetime, so a hash computed on each side would not agree. That reasoning is
correct and is written down in the module.

Each table is written in its own transaction (`ingest.py:353-424`). A schema
change drops and recreates the mirrored table, because the database schema is the
authority (`ingest.py:459-487`). A view is created only when every table it
names is present (`analytics/views.py:8-11`), and a query whose view is missing
is refused by name with the missing table listed (`ingest.py:708-728`).

`reconcile()` (`ingest.py:307-314`, `426-457`) is the drift detector: it compares
the key set and the row hash on both sides and reports `added`, `removed` and
`changed` per table. `rebuild()` (`ingest.py:284-305`) drops everything and
re-snapshots, and the rebuild is deterministic because the hash is.

### 2.4 The Obsidian vault, a downstream view

Package `src/product_hunter/projection/`, six modules, on `main`.

The engine is the only writer and it writes only under the vault root
(`projection/engine.py:1-7`). A note is written only when its bytes differ
(`engine.py:69-77`), so a run over unchanged state touches nothing. Writes go
through a temporary file in the same directory and `os.replace`
(`engine.py:223-246`), so a reader never sees half a note. The manifest at the
vault root records what was written, and a note the snapshot no longer holds is
removed (`engine.py:107-136`); when the manifest is missing, the engine finds its
own notes by the frontmatter marker (`engine.py:150-161`, `196-220`). A path
that would leave the vault is refused (`projection/vault.py:169-179`).

The projection reads SQLite through a connection the CLI opens read-only
(`cli.py:1297`); the only writable connection in that command is the one the
outbox uses for its own bookkeeping (`cli.py:1295`).

### 2.5 What the branches already said

Three of the findings below are work the branches documented as open, rather
than defects they concealed. The distinction matters for the integrator, so it
is recorded here rather than left for the reader to work out.

- The Qdrant branch named the missing index worker wiring as the next wave's
  work (`wave-3-qdrant.md:215-219`) and listed the end-to-end outbox flow with
  a qdrant task as not checked (`wave-3-qdrant.md:206-207`). That is F1.
- The DuckDB branch stated twice that nothing drives the layer, and named it an
  integration risk (`wave-3-duckdb.md:33`, `:138`). That is fix 12.
- The observability branch recorded the migration-number collision before it
  could happen and named the resolution: "the integrator renumbers rather than
  merging two files under one number" (`db/migrations/0008_observability.sql:17-20`).
  That is F7.

The remaining findings, F2 through F6 and F8 through F12, are not described
anywhere in the branch reports. Neither are the nine stage 6 findings in
section 7.

## 3. The brief's checks, answered

| Check | Result | Evidence |
|---|---|---|
| SQLite remains authoritative | **Pass** | The only write path. Qdrant and DuckDB both open it `read_only=True`; the projection does too |
| Qdrant cannot become authoritative | **Pass** | `semantic/source.py` reads SQLite and writes nothing; the `semantic/` package issues no SQL write of any kind against SQLite, and the one `DELETE` in it is a Qdrant REST call (`semantic/qdrant.py:135`); the CLI opens SQLite read-only (`wave3/qdrant-semantic` `cli.py:1527`) |
| DuckDB cannot become authoritative | **Pass** | `analytics/ingest.py:183` opens SQLite read-only; the layer writes only DuckDB tables; same-file paths are refused (`ingest.py:178-181`) |
| Projections can be rebuilt | **Pass** | `ph project` rebuilds the vault; `ph index rebuild` drops and re-syncs the collection (`semantic/index.py:187-192`); `Analytics.rebuild()` drops and re-snapshots (`ingest.py:284-305`) |
| Failed projections remain retryable | **Partial** | Retried with backoff up to `max_attempts=5`; after that the task is terminal and nothing requeues it. See F6 |
| Partial projection does not corrupt authoritative state | **Pass** | The projection writes only to the vault; a failure surfaces as a `fail_projection` row in SQLite, never as a change to the evidence |
| Duplicate projection is safe | **Pass** | `enqueue_projection` returns the existing task for the same key and raises `OutboxConflict` on changed content (`outbox.py:76-87`); the vault write is byte-compared; the DuckDB write is `INSERT OR REPLACE` keyed on the primary key |
| Stale projections are detectable | **Partial** | Detectable for the Obsidian vault (outbox status) and for DuckDB (`reconcile()`). Not detectable for Qdrant, and the Qdrant prune is capped. See F1, F4, F5 |
| Vector-space changes are handled safely | **Pass** | `ensure_ready` raises `EmbeddingSpaceMismatch` before any write; the answer is a rebuild; the width is checked again at embed time (`embeddings.py:36-44`) |
| Schema mismatches fail closed | **Fail** | DuckDB and Qdrant fail closed; the Obsidian projection fails open by design. See F3 |
| Transactions and outbox semantics are preserved | **Pass** | The enqueue sits inside the import transaction (`db/store.py:940`, committed at `db/store.py:941`); claim, complete and fail are epoch-fenced; the migration loader refuses duplicates and gaps |

## 4. Consistency failures

### F1 (High): the `qdrant` projection task is enqueued and never drained

`wave3/qdrant-semantic`, `src/product_hunter/db/store.py:1150-1179`. The import
transaction calls `_enqueue_index_projection` at `store.py:952-954`, which writes a
projection task with `target="qdrant"`, a key of
`semantic_index:{run_id}:{digest}` over the entity ids, and a payload naming the
spans and pain points the run produced. The mechanism is right and it is inside
the authoritative transaction, which is where it belongs.

Nothing ever executes it. The only drain in the tree is
`wave3/qdrant-semantic` `cli.py:1611-1635`, and it registers one handler,
`{"obsidian": handle}`, and filters on `target="obsidian"`. There is no
`ph index --outbox`. The single place that drains a `qdrant` task is a test:
`tests/test_semantic_index.py:626` calls `process_outbox` with
`{"qdrant": index.handle_projection_task}` by hand.

Consequence: every `ph db import-run` leaves a `pending` task with
`target='qdrant'` in the authoritative store. The semantic index moves forward
only when a person runs `ph index sync`. `ph db status` reports the backlog
(`cli.py:1049-1054`), so the drift is visible to an operator who knows to look,
but nothing surfaces it.

There is a second-order hazard. `process_outbox` looks the handler up by target
and, when it finds none, calls `fail_projection` with
`"no handler for target ..."` (`db/outbox.py:347-360`). Any future drain that
passes no target filter would claim the `qdrant` tasks and burn them through the
attempt ceiling, marking them `failed` for good. The current drain filters by
target, so this is latent rather than live.

The branch's own report says as much, which matters for how the integrator
should read this. `wave-3-qdrant.md:206-207` lists "The full `ph project
--outbox` flow with a qdrant task" under what was not checked, and lines 215-219
name the missing wiring as the next wave's work: "the worker that calls
`build_provider` and feeds the index still has to be wired, the same way the
Obsidian projection's worker handler is wired." This is deferred integration
work the branch documented, not a defect it hid. The Wave 3 integration is where
it has to be closed.

### F2 (High): the autonomous operation engine never executes a projection

`wave3/autonomous-operation`. The engine recovers projection leases
(`autonomy/maintenance.py:59`, `autonomy/crash.py:53`, both calling
`recover_projection_leases`) but never runs one. `config/cadence.yaml` schedules
`COLLECT`, `PROCESS`, `CLUSTER`, `INVESTIGATE`, `BRIEF` and `MAINTENANCE`; there
is no projection stage. `src/product_hunter/runtime/worker.py` dispatches on
`job.stage` and nothing handles the outbox.

The effect is that the recovery path keeps a task alive and nothing consumes it.
In an autonomous run, `projection_tasks` accumulates `pending` rows, the Obsidian
vault never refreshes, and the `qdrant` tasks from F1 never run. Every derived
layer in this architecture is refreshed today by a human running a CLI command.
That is the single largest gap between the architecture as designed and the
architecture as it runs.

The branch's own report describes the engine's scope as recovery, not
execution. `wave-3-autonomous-operation.md:261-266` lists the startup sequence
as "recover the expired job leases, recover the expired projection leases, count
the interrupted jobs, validate the latest checkpoints, count the dead letters,
and report what was found". Its test table row for
`test_a_worker_killed_before_the_projection_leaves_the_task_pending` reads "the
outbox task stays pending; the projection is built again"
(`wave-3-autonomous-operation.md:255`). The test only asserts that the task is
still `pending` (`tests/test_autonomy_recovery.py:251`); the projection is built
again because an operator later runs `ph project`, not because the engine built
it. The wording is worth correcting at the source so the next reader does not
read it as engine behaviour.

### F3 (Medium): the Obsidian projection fails open on a schema mismatch, and the other two layers fail closed

`src/product_hunter/projection/contract.py:236-256`. `select_columns` returns an
empty list for a table that is not there, and swallows `sqlite3.Error` from the
query. `projection/source.py:1-10` states the policy in the module docstring: a
table that arrives with a different shape "degrades the projection instead of
breaking it". The behaviour is deliberate and it is pinned by a test,
`tests/test_projection_source.py:597`
(`test_a_table_with_a_different_shape_degrades_the_projection`).

The other two derived layers take the opposite line. DuckDB refuses a table it
cannot mirror faithfully (`analytics/tables.py:261-281`) and refuses a query
whose view is missing, naming the table it needs
(`analytics/ingest.py:708-728`). Qdrant refuses a collection holding another
vector space (`semantic/index.py:171-181`).

A migration that renames a column, or a database restored from a backup that
predates a wave, therefore produces a silently smaller Obsidian vault with no
error, no exit code and no marker anywhere. The authoritative rows are untouched,
which is the part that matters most, but the failure is invisible. A `strict`
mode that raises on a missing required table or column, plus a note in the vault
manifest naming whatever was degraded, would align the three layers without
changing the derived-only contract.

### F4 (Medium): the Qdrant prune is capped at 10,000 points and the adapter cannot page

`semantic/index.py:56` sets `_SCROLL_LIMIT = 10_000` and `_prune`
(`semantic/index.py:471-486`) scrolls the collection with that limit. The
adapter's `scroll` (`semantic/qdrant.py:206-227`) sends only
`{"limit": limit, ...}`; it sends no offset and does not follow
`next_page_token`. The comment at `semantic/index.py:54-56` says so plainly:
"The adapter does not page, so the limit has to cover the collection."

Once the collection holds more than 10,000 points, a full sync prunes only the
points it can see. Stale points beyond that window are neither removed nor
reported, and the index keeps serving entities the store no longer holds. This
is the one place in the architecture where a derived store drifts permanently
from the authority with no signal. The `_member_vectors` scroll is safe, because
its filter matches at most as many points as its limit
(`semantic/index.py:435-459`).

### F5 (Medium): a cluster that cannot be indexed is an error but not a failure, so the task is marked done

`semantic/index.py:418-433`. When a cluster has no indexed member to take a
centroid from, `_write_clusters` appends a message to `counts.errors` and
continues. It does not increment `counts.failed`. `handle_projection_task` raises
`IndexSyncFailed` only `if report.failed` (`semantic/index.py:275-278`).

So the outbox task completes as `done` while that cluster was never written. The
message survives in the sync report, and a later full sync will retry the cluster
because its point is absent, so no data is lost. What is wrong is the record: the
outbox says the projection succeeded when it did not. The neighbouring paths get
this right. A payload the model refuses increments `counts.failed`
(`semantic/index.py:233-239`), and an entity that cannot be embedded increments it
too (`semantic/index.py:408-414`).

The same shape appears in the Obsidian projection. A row the model refuses is
dropped with no channel to report it: `projection/source.py` has no skip list at
all, and a value it cannot read becomes `None` and is rendered as "not recorded"
(`projection/source.py:840-861`). The semantic layer has exactly the mechanism
the projection lacks, in `SemanticSource.take_skipped()`
(`semantic/source.py:146-150`), and the index collects it into the report
(`semantic/index.py:206`).

### F6 (Medium): a projection task that exhausts its attempts is terminal, and nothing requeues it

`db/outbox.py:249-250`. When `attempts` reaches `max_attempts`, `fail_projection`
sets the status to `failed`. From that point:

- `claim_projection` only claims `status = 'pending'` (`db/outbox.py:152`).
- `recover_projection_leases` only touches `status = 'processing'`
  (`db/outbox.py:296-297`).
- `enqueue_projection` returns the existing task for a known key without
  resetting it (`db/outbox.py:76-87`).
- `ph db recover` calls `recover_projection_leases` and nothing else
  (`cli.py:1149`).

So a `failed` task stays `failed` forever, and re-importing the same run finds
the same idempotency key and enqueues nothing. The projection output is still
recoverable by hand, because `ph project` rebuilds the vault whether or not a
task is pending, and `ph index rebuild` replaces the collection. What is missing
is a requeue for the task record, so the operator's view of what is outstanding
is wrong in the one case where something actually went wrong.

### F7 (Medium): three Wave 3 branches ship a migration numbered 0008

- `wave3/autonomous-operation` → `db/migrations/0008_autonomy.sql`
- `wave3/observability-evaluation` → `db/migrations/0008_observability.sql`
- `wave3/source-expansion` → `db/migrations/0008_source_expansion.sql`

`db/migrations.py:42-69` refuses a duplicate number and refuses a gap, so the
merge of any two of these fails closed with a `MigrationError` and no data is
corrupted. The `0008_observability.sql` header records the risk in advance:
"Another wave 3 branch may also have taken the number; the loader refuses a
duplicate and refuses a gap, so the integrator renumbers rather than merging two
files under one number." The behaviour is right; the integration work is still
outstanding and it is a hard blocker on the merge.

### F8 (Medium): two branches add competing transaction helpers to the same file

`wave3/autonomous-operation` adds a `transaction()` context manager to
`src/product_hunter/db/connection.py`: a savepoint when the caller already owns a
transaction, its own `BEGIN IMMEDIATE` otherwise.
`wave3/opencode-control-plane` adds `with_busy_retry()` and `is_busy()` to the
same file: three attempts with a 0.25 second delay when another writer holds the
file.

Both solve the same problem and they overlap. Merging them is a textual conflict
in one file with two policies for the same condition, and the order of nesting
changes the behaviour. One of the two should be adopted and the other dropped.

### F9 (Low): the authoritative store keeps no record of the semantic index's state

There is no table in SQLite that says which vector space the semantic index
currently holds, or when it was last synced. The `space_id` lives only in the
Qdrant collection metadata and in each payload.

The pattern already exists one wave earlier. `db/migrations/0006_clustering.sql:26-29`
puts `embedding_provider`, `embedding_model`, `embedding_model_version` and
`space_id` on `clustering_runs`, and `clusters.space_id` at line 49. The DuckDB
layer keeps the same idea in its own `analytics_state` table, with a watermark
and a row count per table (`analytics/ingest.py:51-58`).

Consequence: if the Qdrant collection is lost, SQLite cannot say what space was
indexed, and only `config/semantic.yaml` can. The `ensure_ready` guard still
catches a mismatch, so this is a recoverability gap rather than a correctness
one.

### F10 (Low): a closed document removes its evidence from the index but leaves its pain points and clusters

`semantic/source.py:351-361`. `_live_status` propagates a closed document's
status to its spans, and the index deletes the span's point
(`semantic/index.py:222-230`). The pain-point and cluster readers
(`semantic/source.py:190-232` and `234-265`) join `documents` but never look at
`d.live_status`, so a pain point derived from a closed document stays indexed,
and a cluster holding that pain point keeps its centroid.

The index therefore applies two opposite rules to the same retraction. Whether a
pain point should outlive its document is a product decision, but nothing records
the decision, and the two readers disagree by accident rather than by design.

### F11 (Low): `ph db check` does not verify that the raw blobs are still on disk

The raw capture store is content-addressed and immutable, and it writes atomically
(`raw_store.py:40-69`). The extracted text is written the same way
(`documents.py:304-320`). SQLite holds the path and the hash, not the bytes
(`db/store.py:208-236`).

`db_check_command` runs `PRAGMA integrity_check` and `PRAGMA foreign_key_check`
and nothing else (`cli.py:1213-1239`). A blob that was deleted, truncated, or
never written leaves the database perfectly consistent. "Rebuild everything from
SQLite" is not possible for the raw layer, and no command notices when the
referenced files have gone.

### F12 (Low): two timestamp formats share a column

`db/migrations/0001_initial.sql:281` defaults `created_at` to
`strftime('%Y-%m-%dT%H:%M:%fZ','now')`, which is UTC with a `Z`. The Python code
writes an aware UTC `datetime` and serialises it with `isoformat()`, which ends in
`+00:00`. The write that matters is the outbox's `available_at`
(`db/outbox.py:88`, `:110`), where `now_dt = utc_now(now)` and
`available_at or now_dt.isoformat()`. `utc_now` is defined in
`db/connection.py:12-22` and returns a `datetime`; `utcnow`, which it calls, is
`src/product_hunter/fetch.py:40` and is `datetime.now(timezone.utc)`.

The two formats order differently as strings, because `Z` sorts after `+`.

The outbox compares timestamps as strings, so the format has to be consistent.
`available_at` is compared in `claim_projection` (`db/outbox.py:152`), and
`lease_expires_at` is compared in `recover_projection_leases`
(`db/outbox.py:296-297`). Both sides of each comparison are written by Python
today, so the ordering is consistent as it stands. `available_at` is always
written explicitly (`db/outbox.py:110`) and never falls back to the SQL default,
which is what keeps the two formats out of the same column. The risk is that a
future writer omits the column and the default slips in.

## 5. Transaction failures

No transaction failure corrupts authoritative state. Every write path reviewed
either holds its own transaction or joins the one it was given.

| Failure | Behaviour | Where |
|---|---|---|
| A migration file fails | The schema and `user_version` are left exactly as they were | `db/migrations.py:72-87` |
| The run import fails on an integrity error | The whole import rolls back and the caller gets a `StoreConflict` | `db/store.py:942-952` |
| The import fails on anything else | The whole import rolls back | `db/store.py:950-952` |
| A projection handler raises | The outbox records the failure and the task goes back to `pending` with a backoff; the evidence stays committed | `db/outbox.py:362-375`, `db/outbox.py:222-284` |
| A projection lease expires | The task returns to `pending`, or to `failed` at the ceiling | `db/outbox.py:287-322` |
| A worker lost its lease | `complete_projection` and `fail_projection` both refuse with `LeaseLost` | `db/outbox.py:194-211`, `db/outbox.py:243-248` |
| A checkpoint is written under a stale lease | The write is refused by the fence | `db/checkpoints.py:101-115` |
| A checkpoint is re-saved with different state under the same version | `CheckpointConflict` | `db/checkpoints.py:132-142` |
| A DuckDB table fails to mirror | That table's transaction rolls back; the tables that already landed keep their rows | `analytics/ingest.py:364-367`, `408-411` |
| The Qdrant collection holds another space | The sync refuses before any write | `semantic/index.py:171-181` |

Two transaction behaviours are worth naming because they are design choices
rather than defects, and a reader of the code will want to know them.

**The DuckDB snapshot aborts on the first failing table.**
`analytics/ingest.py:253` builds the report with
`reports.extend(per_table(spec) for spec in mirrorable)`. The generator raises
on the first failure, so the remaining tables are never mirrored and the caller
receives an exception rather than a partial `IngestReport`. The per-table
transaction does its job, and the already-mirrored tables keep their data, but
the report of what was and was not mirrored is lost. The refused-table path
alongside it (`ingest.py:235-252`) is handled gracefully and reported by name,
so the two failure kinds are treated differently. `reconcile()` recovers the
picture, but only an operator who knows to run it.

**The projection reads and the outbox writes on two connections.** In
`project_command` (`cli.py:1446-1460`) the projection reads through a
read-only connection and the outbox bookkeeping runs on a separate writable one.
That split is what makes the projection structurally incapable of writing to the
authority, and it is the right design.

## 6. Projection failures

| Failure | Detected | Retryable | Authoritative state |
|---|---|---|---|
| The vault root is not writable | `Projector.project` raises, the outbox records it | Yes, until the attempt ceiling, then terminal (F6) | Untouched |
| A note path would leave the vault | `VaultLayout.resolve` refuses the path | The task fails, the other notes still land | Untouched |
| A note holds text that would act in Obsidian | `projection/sanitize.py` escapes it, the hostile URL scheme is replaced | Not a failure | Untouched |
| The vault is deleted after the task completed | Nothing. The task stays `done` and the vault is not rebuilt | The operator runs `ph project` | Untouched |
| A table the projection reads has changed shape | Nothing. The projection degrades and the vault is smaller (F3) | Not applicable | Untouched |
| A row the projection model refuses | Nothing. The field renders as "not recorded" (F5) | Not applicable | Untouched |
| A Qdrant point cannot be embedded | The index counts it as failed and the task is retried | Yes | Untouched |
| A Qdrant cluster has no indexed member | The error is in the report, the task is marked done (F5) | Only by a later full sync | Untouched |
| A DuckDB table cannot be mirrored | `mirror_problem` refuses the table by name | The next run tries again | Untouched |
| A DuckDB view is missing | The query is refused and the missing table is named | Not applicable | Untouched |
| A Qdrant collection holds another space | `EmbeddingSpaceMismatch`, and the answer is a rebuild | The rebuild is the fix | Untouched |
| A Qdrant point is stale past the 10,000 point window | Nothing (F4) | Not detected | Untouched |

## 7. Security and observability findings

The stage 6 node took five dispatches to land. One died on a provider HTTP 429,
two were cancelled by the environment, and the cross-rung attempt on the other
model family failed because that model was unavailable. The fifth ran and
returned a verdict of `object` on the storage architecture, with Must-fix
findings on unbounded growth and on observability.

The orchestrator also performed the same pass directly while the dispatches
were failing, and read the storage layer, the shared runtime, the vault, the
raw store and the URL admission guard. The two passes agree on every point, and
the findings below are the union. Each one has been re-checked against the code
before it was written down.

### H1 (Medium): the outbox stores unredacted handler exception text

`db/outbox.py:371` writes `error=f"{type(exc).__name__}: {exc}"` into
`projection_tasks.last_error`. The text goes in verbatim, with no masking and no
length cap, and `ph db outbox` prints it (`cli.py:1190-1210`).

The runtime worker's equivalent path does the opposite, on purpose.
`runtime/worker.py:52-54` states the rule: a handler can raise an exception
whose message carries page text, model output or a credential, so the failure
text is masked and cut before it reaches the job row or the event stream. The
implementation is `_safe_error` (`runtime/worker.py:473-489`), which masks
labelled secrets and truncates at 300 characters.

The outbox has no such policy, and it is the table an operator reads when a
projection is failing. The two handlers in play narrow the exposure. The
semantic branch already reduces its own provider messages to the exception type
(`semantic/providers.py:295-297`), so what the outbox would carry there is a
model name. The vault projection is the live case, and there the message is an
OS error carrying a filesystem path. A path, not a credential, but it lands in
the authoritative store and on stdout.

The fix is one line: move `_safe_error` into a shared module and call it from
`process_outbox` at `db/outbox.py:371`.

### H2 (Medium): nothing ever deletes a projection task, a job or a spend-ledger row

There is no `DELETE FROM projection_tasks` and no `DELETE FROM jobs` anywhere
under `src/product_hunter/db/`, and the CLI has no purge, vacuum or retention
command (`cli.py:138-198`).

Two consequences follow. The tables that carry the operational history grow for
as long as the database lives. And `list_projection_tasks` has no `LIMIT`
(`db/outbox.py:391-404`), so `ph db outbox` with no `--status` serialises every
row of the table to JSON. The command an operator would reach for to inspect
the outbox is the command that grows without bound.

A `ph db purge --before <iso>` that removes `done` projection tasks and terminal
jobs older than a cutoff, plus a `--limit` on the listing, closes it.

### H3 (Low): the raw capture store and the extracted-text store have no retention

`raw_store.py:40-69` writes content-addressed blobs and never removes one.
`documents.py:304-324` does the same for extracted text. Both live under
`data/`, which the adapter's forbidden rules keep out of git, so the growth is
invisible to `git status` and unbounded on disk. The harden pass reached the
same conclusion independently and noted that the path handling is sound: the
`source_id` is validated and the file name is a sha256, so there is no
traversal risk, only a capacity one.

The `fetches` table already knows when each capture happened, so a retention
sweep keyed on that column is the natural fix, and the autonomy branch's
upkeep pass is where a sweep belongs.

### H4 (Low): the DuckDB mirror keeps its own state only inside the DuckDB file

`analytics_state` holds the watermark and the row count per table
(`analytics/ingest.py:51-58`), and it is written to DuckDB, not to SQLite
(`analytics/ingest.py:606-614`). The authoritative store therefore cannot
answer "when was the analytical layer last refreshed?", and a lost DuckDB file
takes the watermark history with it. `reconcile()` detects drift but reports
no age. This is the same gap as F9, seen from the observability side: the
projected layers should record their own state somewhere the authority can see.

### H5 (Medium): `operation_events` is never pruned

`db/migrations/0008_autonomy.sql:52-58` declares the table with no retention
column and no TTL. `autonomy/engine.py:543-563` appends one row per transition
and nothing removes one. `autonomy/maintenance.py:48-79` is the upkeep pass, and
it recovers leases, sweeps workers, retries dead letters and prunes
checkpoints, but it never touches `operation_events`. The only `DELETE` in the
whole autonomy package is the checkpoint prune at `maintenance.py:141`.

The rate follows from the config. `config/autonomy.yaml:2` sets
`poll_seconds: 1.0`, and an active cycle writes `operation_started`,
`job_completed`, `job_failed`, `job_blocked` and `lease_recovered` events
continuously. There is no ceiling, so the table and every query against it grow
for as long as the operation runs.

The fix follows the pattern already in the file: a retention pass in
`run_maintenance` that mirrors `_prune_checkpoints` at `maintenance.py:131-145`,
deleting `operation_events` older than a configurable retention.

### H6 (Low): `worker_health` accumulates one row per process start

`runtime/identity.py:39` mints the worker id as
`f"{prefix}-{hostname}-{pid}-{secrets.token_hex(4)}"`, so every process start
is a new id. `worker_id` is the primary key of the table
(`db/migrations/0008_autonomy.sql:15-30`), so every restart inserts another
row. `sweep_stale_workers` only sets the status
(`autonomy/health.py:203-222`), an `UPDATE` that never deletes, so the rows
accumulate instead of being reclaimed.

The fix is to delete the stale rows past a retention in the same sweep, rather
than only flipping the status.

### H7 (Medium): a DuckDB mirror failure leaves no durable record

`analytics/ingest.py:366` and `:410` log the failure and re-raise it.
`analytics_state` is written only on success (`analytics/ingest.py:606-614`),
so a table that never mirrored has no row at all. And the per-table generator
at `analytics/ingest.py:253` aborts the whole run on the first failure, so no
`IngestReport` is returned either.

The consequence is that the only trace of a failed mirror is a `logger.error`
line, which dies with the process. A later operator cannot tell from the store
which table failed or why. This is the durability half of the finding in
section 5: the transaction semantics are right, and the record of the failure
does not survive.

The fix is to catch inside the per-table loop and return a
`TableReport(mode="failed", reason=...)` rather than aborting, and to persist a
failure row so the failure outlives the process.

### H8 (Medium): lease recovery and maintenance leave only a counter

`autonomy/engine.py:309-320` calls `recover(...)` and keeps only
`report.leases_recovered`, as a counter on the operation row. The rest of the
`CrashRecoveryReport` is discarded: `projections_recovered`,
`interrupted_jobs`, `checkpoints_validated`, `checkpoints_skipped`, `requeued`,
`dead_letters` and `workers_marked_stale` are all dropped on the floor.
`autonomy/engine.py:322-331` is the same shape: it calls `run_maintenance(...)`
and throws the `MaintenanceReport` away without recording it.

So when a worker dies and its leases are recovered, the durable record says
"1 lease recovered" and nothing else. Which job, which worker, which
projections, which checkpoints were skipped: none of it survives. A crash
cannot be diagnosed from the store alone.

The fix is a structured `operation_event` carrying the report fields, written
in the same transaction as the counter.

### H9 (Low, conditional): a clustering embedding failure may leave no durable record

`clustering/engine.py:188-192` calls `self._embed(...)` with no `try/except`,
and `_embed` (`clustering/engine.py:566-589`) propagates the embedder's error
and raises `MixedVectorSpaceError`. If clustering always runs inside a worker
job, the job's `job_failed` event is durable and the gap closes. If it is
invoked directly anywhere, a failed embedding leaves no trace: no event, no run
row, nothing.

This one is conditional on the call path, which is why it is Low. The fix is to
wrap `_embed` and record the failure against the clustering run, or to confirm
that clustering is always worker-wrapped.

### Clean on this pass

- **Credential handling.** The Qdrant api key is read once from the environment
  (`wave3/qdrant-semantic` `cli.py:405`), never written to a config file, and
  scrubbed from every message the adapter builds (`semantic/qdrant.py:321-325`).
  `.env.example` carries an empty placeholder. The runtime worker masks and
  truncates before an error reaches the job row or the event stream
  (`runtime/worker.py:52-59`, `:473-489`). The observability branch writes only
  model metadata and metric summaries (`observability/comparison.py:112-143`).
  The harden pass found the same, and more: the control-plane branch factors
  the masking into `redaction.py:18-49`, which the worker's `_safe_error` calls
  (`wave3-opencode-control-plane` `runtime/worker.py:467-474`); the SubQ
  client keeps its key in a `SecretStr` with `repr=False`
  (`llm/subq.py:94`), bounds its service error detail at 500 characters
  (`llm/subq.py:78`, `:1118-1125`) and refuses a URL carrying userinfo
  (`llm/subq.py:147-152`); the Apify driver strips its token from error detail
  (`collection/adapters/adapters.py:360-363`); and the autonomy branch records
  its cycle faults through the same masking (`autonomy/engine.py:248`,
  `autonomy/health.py:164-173`). The one caveat is the one `_safe_error` itself
  names: the mask matches *labelled* secrets, so a bare token with no label in
  front of it passes through.
- **Path and identifier safety.** `VaultLayout.resolve` refuses a path that
  leaves the vault root (`projection/vault.py:169-179`), and `_component`
  sanitises every file-name part (`vault.py:197-205`). The raw store validates
  `source_id` against `^[a-z0-9_]+$` (`raw_store.py:10`, `:41-42`), and the
  extracted-text file name is a content digest, so no user string reaches a
  path (`documents.py:304-308`). The DuckDB layer validates every table and
  column name before interpolating it into DDL or an INSERT
  (`analytics/tables.py:174-182`, `:196-199`, `:229-233`). The migration number
  is an int parsed from a filename (`db/migrations.py:47-52`). The harden pass
  checked the branch-added code for the same and found the only two dynamic
  identifiers to be whitelisted against a fixed dict
  (`autonomy/engine.py:523-530`, `autonomy/health.py:150-153`), with
  everything else parameterised, and confirmed that every subprocess call
  passes an argv list with `shell=False`
  (`llm/opencode.py:135-141`).
- **URL admission.** `safety.py` refuses a scheme outside the source allowlist,
  URL userinfo, an IP-literal host, a host outside the allowlist, a
  nonstandard port, and a host that resolves to a non-public address, and it
  refuses a redirect from HTTPS to HTTP (`safety.py:73-115`).
- **Bounded caches.** The SubQ response cache is in memory and bounded at 256
  entries with LRU eviction (`llm/subq.py:106`, `:404-410`), and
  `source_pacing`, `operation_budget` and `operation_runs` are upserted per
  source, scope and operation rather than appended, so they do not grow.
- **The authority boundary.** Confirmed by the stage 7 node and not re-checked
  here.

## 8. Recommended integration fixes

Ordered by what has to be true before the branches can be merged and the layers
can be trusted.

**1. Renumber the three 0008 migrations.** Pick one to keep the number and give
the other two the next numbers, then land the renumber as its own commit on each
branch. The loader already refuses the collision, so this is mechanical. F7.

**2. Merge the two `db/connection.py` transaction helpers into one.** The
savepoint-aware `transaction()` and the `with_busy_retry()` overlap. Take one
policy for a busy writer and one for a nested transaction, and implement both in
a single place rather than in two branches. F8.

**3. Add a `qdrant` drain.** The task is already enqueued in the right
transaction. What is missing is a path that claims it: a `ph index --outbox`
flag that drains `target="qdrant"` with `SemanticIndex.handle_projection_task`,
mirroring the obsidian drain at `cli.py:1611-1635`. While doing it, filter by
target in every drain, so a task of an unknown target is never claimed and
burned. F1, and the hazard in `db/outbox.py:347-360`.

**4. Give the autonomous engine a projection stage.** Add a `PROJECT` stage to
`config/cadence.yaml`, a handler in the runtime worker that drains the outbox,
and make the maintenance beat run the drain rather than only recovering leases.
Without this, the derived layers are refreshed only by a person at a terminal. F2.

**5. Page the Qdrant scroll.** Either add offset and `next_page_token` handling
to `QdrantAdapter.scroll`, or make the prune loop until the adapter reports no
more points and raise when the collection is larger than the cap. The current
silent cap is the one way a derived store can drift from the authority with no
signal at all. F4.

**6. Align the failure posture of the three derived layers.** Add a strict mode
to the Obsidian projection that raises when a table in `COLUMN_MAP` is missing or
a required column has moved, and make the vault manifest name whatever was
degraded. DuckDB and Qdrant already refuse; the projection is the one that
degrades. F3, F5.

**7. Count a skipped cluster as a failure.** In `semantic/index.py:418-433`,
increment `counts.failed` alongside `counts.errors` when a cluster has no
indexed member, so `handle_projection_task` raises and the outbox keeps the task
retryable. F5.

**8. Add a requeue for failed projection tasks.** A `ph db outbox --requeue`
that moves `failed` tasks back to `pending` with a fresh attempt budget, and make
`enqueue_projection` reset a `failed` task for the same key rather than return
it unchanged. F6.

**9. Record the semantic index's state in SQLite.** A `semantic_index_state`
table holding the space id, the model, the model version, the index version and
the last sync time, written in the same transaction as the sync. The clustering
wave already does this on `clustering_runs`
(`db/migrations/0006_clustering.sql:26-29`) and the DuckDB layer does it in
`analytics_state`. F9.

**10. Decide and record the retraction rule for the semantic index.** Either a
closed document takes its pain points and clusters with it, or it does not. Make
the two readers agree, and write the rule down where the readers can see it.
F10.

**11. Make the DuckDB snapshot report partial failures, durably.** Catch a
per-table failure, record it in the `TableReport` with its reason, and carry on
with the next table, the way the refused-table path already does. Keep raising
at the end if any table failed, so the exit code still tells the truth. Persist
the failure as well as returning it, in a failure row or a failed flag on
`analytics_state`, so a mirror that failed yesterday is still visible today
without the log file that recorded it. F, transaction section above, and H7.

**12. Expose the DuckDB layer.** There is no CLI command for it, and the branch
says so itself: `wave-3-duckdb.md:33` records "Nothing reads the layer yet: no
worker, no CLI command and no schedule drives it", and line 138 repeats it as an
integration risk. The fix here is not a discovery but a closing of known
deferred work. `ph analytics snapshot`, `ph analytics refresh`, `ph analytics
reconcile` and `ph analytics rebuild` would give the layer an operator surface
and make `reconcile()` reachable, which is today the only way to detect that the
derived state has drifted.

**13. Extend `ph db check` to the raw layer.** Walk the `raw_path` and
`text_path` columns and report the files that are missing or whose hash no
longer matches the stored one. F11.

**14. Normalise the timestamp format.** Pick one of `Z` or `+00:00` and use it
in both the SQL defaults and the Python writes, so the string comparison in the
outbox can never see two formats in one column. F12.

**15. Redact the outbox's error text.** Move the runtime worker's `_safe_error`
into a shared module and call it at `db/outbox.py:371`, so a handler's
exception message is masked and truncated before it lands in
`projection_tasks.last_error` and on stdout. H1.

**16. Give the operational tables a retention path.** A `ph db purge --before
<iso>` that removes `done` projection tasks and terminal jobs older than a
cutoff, a `--limit` on `list_projection_tasks` and on `ph db outbox`, and a
sweep for the raw and text stores keyed on the `fetches` table. H2, H3.

**17. Prune `operation_events` in the upkeep pass.** Add a retention sweep to
`run_maintenance` that mirrors `_prune_checkpoints` at
`autonomy/maintenance.py:131-145`, deleting `operation_events` older than a
configurable retention. H5.

**18. Reclaim `worker_health` rows instead of only marking them.** Delete the
rows past a retention in `sweep_stale_workers`, rather than only setting
`status = 'stale'`. A process start mints a new worker id every time, so the
table grows by one row per restart for as long as the database lives. H6.

**19. Write a recovery event, not only a counter.** Record the full
`CrashRecoveryReport` and `MaintenanceReport` as a structured `operation_event`
in the same transaction as the counter, so a crash can be diagnosed from the
store. H8.

**20. Make a clustering embedding failure durable.** Either wrap `_embed` and
record the failure against the clustering run, or confirm that clustering is
always invoked through a worker job whose `job_failed` event is durable. H9.

## 9. What was skipped, and what was not checked

**Skipped.**

- No test was run. Every claim in this report comes from reading the code, the
  migrations, the branch diffs and the existing test files. Where a test pins a
  behaviour, the test is named; where the behaviour is untested, the report says
  so.
- No Qdrant server, no DuckDB file, no vault and no live database were
  exercised. The DuckDB and Qdrant claims about transaction behaviour come from
  the code paths, not from a run.
- The BGE-M3 model was not loaded and no embedding was produced. The audit
  looked at the storage contract around the model, which is what was asked for,
  and made no judgement about the vectors.
- The other three Wave 3 branches were read for storage impact only:
  `scraping-ladder` and `subq-integration` do not touch the storage layer, and
  `source-expansion` and `observability-evaluation` were checked for migrations
  and for `db/` writes only. The harden pass read the branch-added code for
  credential handling, identifier safety and growth, which is deeper than the
  storage-impact read the other three branches got here.
- **The stage 6 node took five dispatches to land**, and the orchestrator ran
  the same pass directly in the meantime, so the security and observability
  findings in section 7 are the union of two passes that agree. The gap is
  coverage rather than independence: the branch code read most deeply is the
  autonomy branch's own budget, health and crash handling, and a fifth
  independent pass would have looked at it with fresh context.
- The harden verdict is `object` on the storage architecture, not on this
  report. The report is the instrument, and the architecture is the subject.

**Not checked.**

- Whether the Qdrant and DuckDB layers will hold up at the volumes the design
  implies. The 10,000 point prune cap (F4) is a code fact; whether the
  collection reaches that size in practice is a workload question that was not
  investigated.
- The `wave3/opencode-control-plane` rewrite of `registry.py` (675 lines changed)
  was reviewed only for its effect on `db/connection.py`. Whether it writes
  authoritative state outside the store was not audited.
- The autonomy engine's own crash and recovery behaviour was read for its
  interaction with the outbox. Its budget, cadence and lease handling were not
  audited.
- Multi-process concurrency was reasoned about from the code, not observed. The
  `BEGIN IMMEDIATE` plus epoch fencing looked correct on reading, but no
  concurrent run was performed.
- The two `shortcut:` markers found in the storage layer were noted, not
  resolved: `db/checkpoints.py:210` records that `load_valid_checkpoint` and
  `skip_invalid` have no production caller yet, and the wave 3 reports carry
  their own.

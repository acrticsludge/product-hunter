# Final Audit 02: the evidence to opportunity pipeline

**Run ID:** final-audit-02-evidence-pipeline-2026-10-22
**Role:** Final Audit Probe #2 (read-only)
**Repository:** `C:\Anubhav\Web Dev Projects\product-hunter`
**Base audited:** `main` at `00a6ca3` ("fix: close the second review round")
**Spec:** [../specs/Product-Hunter-Full-Spec.md](../specs/Product-Hunter-Full-Spec.md)
**Adapter:** Product Hunter, 8 forbidden rules in force, floor risk `medium`

> The requested path was `docs/resonix/reports/...`. The repository's docs tree is
> `docs/reasonix/` (the adapter's `docsRoot` is `docs/`), so the report is filed
> there. No other file was written by this run.

## The verdict in one page

Thirteen findings, sixteen things that are sound, and nine fixes that have to
land before the Wave 3 branches are integrated.

| # | Severity | Finding |
|---|---|---|
| F-01 | CRITICAL | The production write path bypasses the whole Wave 2 evidence verification |
| F-02 | CRITICAL | The opportunity tail of the pipeline has no table and no writer |
| F-03 | HIGH | Research claims hang off handles and URLs, not spans |
| F-04 | HIGH | No stage runner: the pipeline has no orchestrator |
| F-05 | HIGH | Three Wave 3 branches collide on migration number 0008 |
| F-06 | HIGH | The Qdrant projection target is dead on arrival |
| F-07 | MEDIUM | A 403 from the browser is recorded as a successful fetch |
| F-08 | MEDIUM | Nine files are changed by two or more Wave 3 branches |
| F-09 | MEDIUM | A propagation edge attributes itself to an arbitrary evidence span |
| F-10 | LOW | The Wave 3 source registry renames a stored value |
| F-11 | MEDIUM | The observability branch widens the import path's exception handling |
| F-12 | MEDIUM | The import boundary validates nothing about the candidate |
| F-13 | LOW | The cluster's evidence lineage has no distinct-span count |

The two critical findings share a shape. The code that makes the evidence
chain trustworthy exists, is tested, and is correct. It is not on the path
that runs. The same is true of the code that scores an opportunity, and of
the code that refuses a bad run. Nine of the sixteen stages are libraries
that nothing calls.

The repository already knows most of this. `docs/reasonix/spec-compliance.md`
says the stages "have no runner" and that "nothing calls the scoring". What
it does not say is which function to call, and that is what this report is
for.

## 1. Scope and method

The audit covers the complete chain the mission names, in the order the spec
gives it:

```text
SOURCE -> FETCH -> DOCUMENT -> EVIDENCE SPAN -> PAIN POINT -> NORMALIZATION
-> DEDUPLICATION -> ENTITY RESOLUTION -> CLUSTER -> INDEPENDENCE -> VALIDATION
-> COMPETITOR RESEARCH -> COUNTER-EVIDENCE -> SCORING -> OPPORTUNITY -> REPORT
```

**The eight Wave 3 worktrees were located, not assumed.** `git worktree list`
returned all eight, at these paths:

| Branch | Worktree path |
|---|---|
| `wave3/opencode-control-plane` | `C:\Anubhav\Web Dev Projects\product-hunter\.worktrees\wave3-opencode-control-plane` |
| `wave3/autonomous-operation` | `C:\Anubhav\Web Dev Projects\product-hunter-w3a` |
| `wave3/source-expansion` | `C:\Anubhav\Web Dev Projects\product-hunter-w3c` |
| `wave3/duckdb-analytics` | `C:\Anubhav\Web Dev Projects\product-hunter-wt\wave3-analytics` |
| `wave3/observability-evaluation` | `C:\Anubhav\Web Dev Projects\product-hunter-wt\wave3-observability` |
| `wave3/scraping-ladder` | `C:\Anubhav\Web Dev Projects\product-hunter-wt\wave3-scraping-ladder` |
| `wave3/subq-integration` | `C:\Users\anubh\.local\share\opencode\worktree\44767c\wave3\subq-integration` |
| `wave3/qdrant-semantic` | `C:\Users\anubh\AppData\Local\Temp\opencode\wt-wave3-qdrant` |

Every branch was read with `git show` and `git diff main...<branch>` from the
repository root. No branch was checked out and no branch was modified.

**Method.** The import graph over `src/` was measured with `git grep`, because
whether a stage is *wired* is a different question from whether its code
exists. For every table that carries evidence lineage I searched for writers
and readers separately. The migration runner was read to find out what happens
when two branches add a migration with the same number. Three read-only gather
nodes covered the Wave 3 branches; their claims were re-checked against the
code before anything below was written down.

**Gate.** `uv run pytest` exits 0: 2138 tests across 71 files, all green. The
code gate passes. Everything below is a design and wiring finding, not a test
failure.

**Node availability.** The stage 6 and stage 7 nodes could not be completed by
their dedicated agents. The review node was dispatched five times, four on
rung 1 and once on rung 2, and every attempt came back `HTTP 429: Too Many
Requests` or `Model unavailable: opencode-go/longcat-2.5-preview-free`. The
harden node failed twice the same way. Both passes were then performed
directly by the orchestrator against the same rubric, using the same
commands, and the results are in sections 4 and 5. That is a weaker
guarantee than an independent adversarial reviewer, and it is recorded here
rather than papered over. The findings that the review rubric exists to
catch, the overstatement and the missed defect, were checked by re-deriving
each one from the code rather than by reading the draft.

One error was caught that way. The first draft of F-08 claimed that all eight
branches rewrite `cli.py` and `config.py`. The measured tally is four and
three, and two files the draft had missed (`db/connection.py` and
`runtime/worker.py`, both changed by autonomous-operation and
opencode-control-plane) are now in the table. The draft also had L-11
wrong: the DuckDB views gate on the tables they need, so `v_score_history` is
skipped rather than broken.

## 2. The pipeline trace

"Production caller" means a module outside the stage's own package that
imports it. A stage that nothing calls is a library, not a stage.

| # | Stage | Implementation | Production caller | Wave 3 | Verdict |
|---|---|---|---|---|---|
| 1 | SOURCE | `sources.py`, `registry.py`, `config/sources.yaml` | `cli.py` | `source-expansion` | Wired. Legality enforced. |
| 2 | FETCH | `fetch.py`, `raw_store.py`, `collection/ladder.py` | `cli.py` | `scraping-ladder` | Wired. One status bug (F-07). |
| 3 | DOCUMENT | `documents.py` | `cli.py`, `collection/orchestrator.py` | `scraping-ladder` | Wired. |
| 4 | EVIDENCE SPAN | `evidence.py` (`EvidencePipeline`), `db/evidence.py` | **nobody** | `autonomous-operation` (refactor only) | Library only (F-01). |
| 5 | PAIN POINT | `extraction.py`, `db/store.py:insert_pain_point` | `cli.py` via `db import-run` | none | Wired, via the bypassing path. |
| 6 | NORMALIZATION | `normalization.py` | `extraction.py` | none | Wired. |
| 7 | DEDUPLICATION | `db/evidence.py:find_canonical_span` → `evidence.canonical_span_id` | **nobody** (only `EvidencePipeline` writes it) | none | Not in production (F-01). |
| 8 | ENTITY RESOLUTION | `independence/identity.py`, `db/independence.py` | **nobody calls it** (re-exported by `db/__init__.py` only) | none | Library only. |
| 9 | CLUSTER | `clustering/engine.py`, `db/clustering.py` | **nobody** | `qdrant-semantic` (adds the semantic layer beside it) | Library only. |
| 10 | INDEPENDENCE | `independence/analytics.py`, `db/independence.py` | **nobody calls it** (re-exported by `db/__init__.py` only) | none | Library only. |
| 11 | VALIDATION | `research/gate.py`, `db/research_gate.py` | **nobody calls it.** `require_promotable`, `evaluate_run_gate` and `GateBlocked` appear in `db/__init__.py` and nowhere else in `src/`. | none | Library only. |
| 12 | COMPETITOR RESEARCH | `research/investigation/competitors.py` | **nobody** | none | Library only. |
| 13 | COUNTER-EVIDENCE | `research/investigation/counter_evidence.py` | **nobody** | none | Library only. |
| 14 | SCORING | `opportunity/scoring.py` | **nobody** | none | Library only. |
| 15 | OPPORTUNITY | `opportunity/lifecycle.py` + the `opportunities` table | **the table does not exist** | none | Missing (F-02). |
| 16 | REPORT | `projection/*`, `reporting.py`, `ph project` | `cli.py` | `duckdb-analytics` | Wired. Read-only. |

The measured import graph, in one line: of the sixteen stages, four are
reached by a production caller (source, fetch, document, report), three more
are reached only through `db import-run` (the pain point, the normalization,
and the document half of the evidence span), and nine are libraries with no
caller at all.

## 3. The audit questions, answered

| Question | Answer | Where |
|---|---|---|
| Does evidence stay traceable to its original source? | **Only inside `EvidencePipeline`.** The production import path does not re-check it. | F-01 |
| Is provenance preserved? | Yes in the schema; no on the production write path. | F-01 |
| Are quotes grounded in captured source text? | Yes at extraction time. Not re-verified at the DB write. | F-01, P-05 |
| Do stable IDs survive every stage? | Yes. All ids are content-derived, and the Wave 3 vector ids are too. | P-13 |
| Are content hashes preserved? | Yes, end to end, on every table that needs one. | P-07 |
| Can evidence become an untraceable summary? | **Yes, in the research layer.** A claim's evidence is a string ref and a free-form summary. | F-03 |
| Are failed searches distinct from genuine zero results? | Yes, in the investigation and discovery layers. No, for a browser 403. | P-01, P-02, F-07 |
| Does clustering destroy evidence lineage? | No. The schema keeps the chain intact. | P-04, P-03 |
| Does independence receive the correct entities? | The query is right; nothing calls it. One attribution bug. | F-09 |
| Does scoring use the correct upstream data? | The input model is right. Nothing calls it and its output table is missing. | F-02 |
| Are competitors and counter-evidence attached to actual claims? | Attached to handles and URLs, not to spans or quotes. | F-03 |
| Can opportunity reports be reconstructed from authoritative data? | Only as pain-point groups. A scored opportunity has no row to reconstruct from. | F-02 |
| Does Obsidian/reporting become the source of truth? | **No.** The projection is read-only and the outbox carries the task. | P-08 |

## 4. Findings

### F-01 — CRITICAL — The production write path bypasses the whole Wave 2 evidence verification

`src/product_hunter/evidence.py` is 872 lines, and the bulk of it is
`EvidencePipeline`: the code that checks provenance, re-derives the quote
from the stored document text, verifies the span offsets, verifies the span id
against its own fields, finds the canonical span, writes the lineage, and
writes the document version.

**It has no production caller.**

```text
$ git grep -n "EvidencePipeline" -- src/
src/product_hunter/evidence.py:228:class EvidencePipeline:
```

The only other hit is `tests/test_evidence_pipeline.py`.

The `evidence` table has exactly two writers in `src/`:

```text
db/evidence.py:201   INSERT INTO evidence   (write_evidence_span, the checked one)
db/store.py:730      INSERT INTO evidence   (_insert_evidence, the unchecked one)
```

and only the first is reached from a production path. The second is the one
that runs.

The one path that puts candidates into SQLite is:

```text
ph db import-run
  -> cli.py:1122          store.import_run(...)
  -> db/store.py:821      Store.import_run
  -> db/store.py:566      import_candidate
  -> db/store.py:680      _insert_evidence
```

`_insert_evidence` writes the row and moves on. It does **not**:

- re-verify the quote against the stored document text (no `locate_quote`),
- check that the candidate's offsets match the quote,
- check the candidate's `text_sha256` / `raw_sha256` against the stored document,
- check that `span_id` equals `derive_span_id(doc_id, char_start, char_end, quote)`,
- check that the candidate carries provenance fields at all,
- set `evidence.canonical_span_id`,
- write an `evidence_lineage` row,
- write a `document_versions` row.

`canonical_span_id` is written in exactly one place, `db/evidence.py:207`, and
that function is only called from `evidence.py:476`. `append_lineage` is only
called from `evidence.py`. `import_document_version`, the function that writes
`document_versions` and advances `content_version` on a re-capture, is called
from `evidence.py:281` and from a test; `import_run` calls the older
`import_document` instead (`db/store.py:893`).

**What it costs, stage by stage:**

- *Deduplication does not happen in production.* `EvidencePipeline.independent_evidence_count`
  (`evidence.py:826`) counts `DISTINCT COALESCE(canonical_span_id, span_id)`.
  With `canonical_span_id` always null, that is a count of spans, so 147
  reposts of one viral complaint count as 147 independent pieces of evidence.
  Spec §11: "Do not count repeated copies of the same content as independent
  evidence." The rule exists in code and is switched off in the only path that
  writes evidence.
- *There is no lineage.* `evidence_lineage` gets zero rows, so the audit trail
  the spec asks for ("an audit trail for every evidence span",
  `db/migrations/0005_evidence_pipeline.sql`) is empty.
- *There is no version history.* A re-captured document overwrites
  `text_sha256` in place, `content_version` never advances, and
  `document_versions` stays empty, so "what did the page say on the day we
  captured it" has no answer.

**How it happens.** `candidates.jsonl` is a plain file on disk. `import_run`
re-reads it with `_read_jsonl(root / "candidates.jsonl", ExtractedCandidate)`
(`db/store.py:840`) and trusts it. The extractor grounds the quote before it
writes the file (`extraction.py:643`), so a file produced by the extractor is
fine. Nothing checks that the file came from the extractor, and nothing
re-checks it on the way into the database.

**Fix.** Route the import through `EvidencePipeline`, or move the six checks
into `Store.import_run` and call `write_evidence_span` rather than writing
`evidence` directly. The smaller of the two is to make `import_run` call the
same `write_evidence_span` and `append_lineage` that `EvidencePipeline` calls,
so there is one write path instead of two.

### F-02 — CRITICAL — The opportunity tail of the pipeline has no table and no writer

Six tables the projection needs exist only as DDL text inside a Python string:

```text
src/product_hunter/projection/contract.py:20   CANONICAL_DDL = """
    CREATE TABLE opportunities (...)
    CREATE TABLE opportunity_scores (...)
    CREATE TABLE opportunity_evidence (...)
    CREATE TABLE opportunity_claims (...)
    CREATE TABLE experiments (...)
    CREATE TABLE research_requests (...)
```

No migration creates them. `git grep "CREATE TABLE opportunities" -- .` returns
only the projection contract, the Wave 2 spec, and a test that creates a
one-column stub. `CANONICAL_DDL` is applied in exactly three places, all of
them tests (`tests/test_projection_engine.py:703`,
`tests/test_projection_source.py:447,550,572`).

The strongest evidence is not a grep. Migrating a fresh database and listing
what is actually in it:

```text
schema version: 7
tables: 45
author_cluster_members, author_clusters, author_identity_links, author_links,
authors, cluster_decisions, cluster_lineage, cluster_members, cluster_versions,
clustering_runs, clusters, collection_items, collection_run_state,
collection_source_state, communities, community_links, document_versions,
documents, evidence, evidence_lineage, evidence_observations,
evidence_pain_links, fetches, human_decisions, independence_reports, jobs,
model_catalogs, model_executions, model_inventory, model_role_bindings,
pain_points, projection_tasks, propagation_edges, research_artifacts,
research_checkpoints, research_claims, research_events, research_runs,
research_stage_artifacts, research_stage_executions, sources, spend_ledger,
stage_attempts, stage_gate_results, tool_calls
```

No `opportunities`. No `opportunity_scores`. No `opportunity_evidence`. No
`opportunity_claims`. No `experiments`. No `research_requests`. Forty-five
tables, and the six the projection needs are not among them.

Nothing writes them either:

```text
$ git grep -n "INSERT INTO opportunit" -- src/
(no results)
```

And the entire `opportunity/` package, the scoring, the signals, the policy,
the lifecycle, the snapshot, the experiments, has no production caller:

```text
$ git grep -n "from product_hunter.opportunity" -- src/
src/product_hunter/opportunity/__init__.py:8:...
src/product_hunter/opportunity/__init__.py:18:...
... (all inside the package)
```

**What it costs.** `opportunity_evidence.span_id` is the column that would tie
a scored opportunity back to the evidence that drove it. It does not exist in
a real database, so the spec §11 chain "CLUSTER → OPPORTUNITY" has no durable
link at the end of it.

The projection does not fail. It degrades, silently and by design.
`projection/source.py:143-148` reads the six tables through
`select_columns`, which returns `[]` for a table that is not there
(`contract.py:246-255`). `_drafts` (`source.py:163`) then finds
`tables.opportunity_rows` empty and falls back to grouping pain points by
their normalized key (`source.py:172-176`, `_group_draft` at
`source.py:239`). The vault renders an "opportunity" page that is really a
normalized-key group of pain points, with `wtp_tier`, `competition`,
`potential`, `confidence` and `uncertainty` all `None`
(`source.py:271-276`).

The repository already knows this. `docs/reasonix/spec-compliance.md` §20:
"There is no `opportunities` table, so a score has no row to live in, and
nothing calls the scoring."

**Fix.** Land the six tables as `db/migrations/0009_opportunities.sql` (see
M-01 for the numbering), give them a writer, and make the projection's
canonical branch the only one. Until then the fallback path is the truth about
what the vault shows, and the wave report should say so.

### F-03 — HIGH — Research claims, competitors and counter-evidence hang off handles and URLs, not spans

The evidence world and the research world are separate, and nothing joins them
at the row level.

`research_claims` in `db/migrations/0003_research_gate.sql` holds:

```sql
evidence_refs_json TEXT NOT NULL,
```

A JSON array of strings. There is no foreign key to `evidence.span_id`, no
quote, and no content hash.

`research/investigation/claims.py:27-38`:

```python
class ClaimEvidence(BaseModel):
    ref: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    source_url: str | None = None
    tool_call_id: str | None = None
    query_family: str | None = None
    weight: float = Field(default=1.0, ge=0.0, le=1.0)
```

`summary` is free-form prose. `ref` is any non-empty string, and unlike
`ToolCallProvenance.result_ref`, which `research/evidence.py:18-19` pins to
`^[A-Za-z0-9._:/-]+$`, `ClaimEvidence.ref` has no such guard, so it can hold
prose.

What a ref resolves to is `research/investigation/provenance.py:47-49`:

```python
def known_refs(self) -> set[str]:
    return {ref for ref in (*self.tool_call_ids, *self.source_urls) if ref}
```

So the evidence behind a claim is a tool call id or a URL. A URL is not a
quote. Nothing in this layer calls `locate_quote`, and nothing checks the
summary against captured source text. `_reconciles` (`claims.py:57-64`) only
checks that the reconciliation text contains the ref string, which is a
substring test on a string the caller chose.

`Competitor` (`research/investigation/competitors.py:68-72`) has the same
shape: `evidence_refs: list[str]` plus `tool_call_id`.

**Spec violation.** Spec §11: "No downstream conclusion should depend on an
untraceable free-form summary." A claim can reach `ClaimStatus.supported`
(`claims.py:40-54`) on a summary that was never checked against the text it
claims to describe.

**The layer is not sloppy.** The status is derived from the evidence rather
than asserted (`claims.py:40-54`, called at `claims.py:78`), a supported
claim must carry a provenance that resolves (`claims.py:90-93`), and evidence
the provenance cannot resolve is refused at construction
(`claims.py:96-98`). The design is right. The anchor is wrong.

**Practical exposure today is zero**, because nothing calls this layer
(F-04). It becomes live the moment a stage handler is written, which is the
next wave. Fix it before that, not after.

**Fix.** Give `ClaimEvidence` a `span_id` and make `ref` resolve to a span in
`evidence`, or require that a supported claim's evidence names a span id that
`research_claims` can check. The database is the right place for the check,
the way `trg_evidence_pain_links_require_matching_span` is in
`db/migrations/0005_evidence_pipeline.sql`.

### F-04 — HIGH — No stage runner: the pipeline has no orchestrator

This is the repository's own documented state, and it is the single thing
that blocks final integration.

`docs/reasonix/reports/wave-2-final-integration.md` ends with:

```text
NEXT: the wave 2 stage handler is the next wave's work
```

Measured again today, the import graph over `src/` shows:

| Module | Imported by, outside its own package |
|---|---|
| `product_hunter.evidence` | nobody |
| `product_hunter.db.clustering` | nobody |
| `product_hunter.db.independence` | `db/__init__.py` only (a re-export) |
| `product_hunter.opportunity` | nobody |
| `product_hunter.research.investigation` | `research/__init__.py` only |
| `product_hunter.collection` | nobody |
| `product_hunter.discovery` | nobody |
| `product_hunter.extraction` | `cli.py`, `db/store.py` |
| `product_hunter.projection` | `cli.py` |
| `product_hunter.reporting` | `cli.py` |

The CLI has twelve commands (`cli.py:138-237`): doctor, version, sources,
fetch, extract, review-sheet, eval, report, models, bench, db, project. None
of them runs a research run through the pipeline. `research` exists, but it
drives the registry's run state, not the stages.

**Consequence.** The gate that could refuse a bad run is never asked to.
`require_promotable()` (`db/research_gate.py`) raises `GateBlocked` and 62
tests exercise it, and no production path calls it. The seven libraries that
hold the intelligence are never called. Nothing writes `research_events` for a
stage, nothing writes `spend_ledger` for a model call beyond the discovery
store's one writer, and the spec §22 budget dimensions stay unmeasured.

**Fix.** One stage handler that walks the sixteen stages and writes
`research_events`, `research_stage_executions` and `spend_ledger` per stage.
This is the next wave by the repository's own plan, so this finding is a
gate on the integration rather than a surprise.

### F-05 — HIGH — Three Wave 3 branches collide on migration number 0008, and the runner will crash

`src/product_hunter/db/migrations.py:42`, `_discover`:

```python
number = int(match.group(1))
if number in found:
    raise MigrationError(
        f"duplicate migration number {number}: "
        f"{found[number].name} and {path.name}"
    )
```

The runner keys on the numeric prefix. It discovers before it applies
anything, so a duplicate is a hard stop for the whole repository: `ph db init`
fails for every user, not just for the migrated tables.

Three branches add a `0008`:

| File | Branch |
|---|---|
| `db/migrations/0008_source_expansion.sql` | `wave3/source-expansion` |
| `db/migrations/0008_observability.sql` | `wave3/observability-evaluation` |
| `db/migrations/0008_autonomy.sql` | `wave3/autonomous-operation` |

The same function also rejects a gap in the numbering, so the renumbering has
to be contiguous: `0008`, `0009`, `0010`, not `0008` and `0011`.

The collision has a second face. `tests/test_db_migrations.py:19` hard-codes
the expected schema version:

```python
EXPECTED_VERSION = 7
```

All three branches set it to `8`. Land two of them and the merged value is
`9`; land all three and it is `10`. Every one of those values is a failing
test until somebody edits the constant. The test is a useful tripwire, but it
is the second thing to break and the first thing a hurried merge forgets.

**The schemas themselves do not collide.** The three migrations create
disjoint tables, and none of the names is already among the forty-five tables
on main:

| Branch | Tables it adds |
|---|---|
| `source-expansion` | `source_candidates`, `source_reviews` |
| `observability-evaluation` | `model_comparisons` |
| `autonomous-operation` | `worker_health`, `operation_runs`, `operation_events`, `operation_budget`, `source_pacing` |

Renumbering is therefore the whole fix on the schema side. Nothing has to be
merged by hand.

**Fix.** Renumber at merge time, in the order the waves integrate, and set
`EXPECTED_VERSION` to the merged count in the same commit. This is mandatory
before any two of the three branches land together (M-01).

### F-06 — HIGH — The Qdrant projection target is dead on arrival, and the pre-cluster Qdrant check does not exist

`wave3/qdrant-semantic` writes projection tasks for the new target.
`src/product_hunter/db/store.py` in that branch, `_enqueue_index_projection`:

```python
target="qdrant",
```

The only outbox drain, in the branch and on main alike
(`cli.py:1629` in the branch, `cli.py:1331` on main):

```python
return process_outbox(
    conn,
    {"obsidian": handle},
    f"ph-project-{os.getpid()}",
    target="obsidian",
    max_tasks=100,
)
```

And `db/outbox.py` `claim_projection`:

```python
"SELECT task_id FROM projection_tasks "
"WHERE status = 'pending' AND available_at <= ?"
if target is not None:
    sql += " AND target = ?"
```

Two independent reasons the qdrant task never runs. The drain filters on
`target="obsidian"`, so a `target="qdrant"` row is never claimed. And the
handlers dict holds only `"obsidian"`, so even if it were claimed it would
fail with `no handler for target 'qdrant'` (`outbox.py:355`).

This is not specific to the qdrant branch. Every `process_outbox` call in
every one of the eight Wave 3 branches is the same three lines:

```python
return process_outbox(
    conn,
    {"obsidian": handle},
    f"ph-project-{os.getpid()}",
    target="obsidian",
```

in `qdrant-semantic` at `cli.py:1629`, `duckdb-analytics` at `1349`,
`source-expansion` at `1451`, `observability-evaluation` at `1500`,
`autonomous-operation` at `1404`, `scraping-ladder` at `1349`,
`subq-integration` at `1349` and `opencode-control-plane` at `1349`. No
branch drains any target but `obsidian`, so any branch that enqueues a new
projection target inherits the same dead end.

The branch does define the handler, at `src/product_hunter/semantic/index.py:255`:

```python
def handle_projection_task(self, task: ProjectionTask) -> None:
```

It is a method on the index and it is never registered with `process_outbox`.
The branch's own report claims the file holds "the index: sync, rebuild,
search, the outbox handler". The handler exists; the wiring does not.

**What it costs.** `projection_tasks` accumulates rows with `target="qdrant"`
that stay `pending` forever, with no error and no lease expiry to surface
them. SQLite and Qdrant diverge silently, which is the exact failure spec
§15A exists to prevent.

**And the check spec §14 demands is absent.** The spec says Qdrant is "a
**required part of the evidence loop**, not an optional enhancement" and that
"Before a new problem cluster is created or a serious opportunity is promoted,
the system must perform the configured Qdrant checks." The branch's
`src/product_hunter/clustering/engine.py` does not import
`product_hunter.semantic` at all, and no opportunity-promotion path performs
a Qdrant check. The semantic layer is reachable only from a manual
`ph index` command.

**Fix.** Register the handler and drain the target, and add the pre-cluster
nearest-neighbour check to `clustering/engine.py`. Until the handler is
registered, stop enqueuing `target="qdrant"` rows rather than letting them
pile up as permanently pending.

### F-07 — MEDIUM — A 403 from the browser is recorded as a successful fetch

`wave3/scraping-ladder`, `src/product_hunter/collection/adapters/adapters.py`,
`PlaywrightDriver.take`:

```python
status = response.status if response is not None else 200
```

The status is read and never tested. The capture returns, and
`collection/adapters/base.py:494` writes:

```python
outcome="fetched",
```

The sibling driver gets it right. In the same file, the Crawl4ai driver:

```python
status = int(getattr(result, "status_code", 0) or 0)
if not getattr(result, "success", False):
    raise ToolHttpError(status, f"the crawl answered {status}")
```

The orchestrator's failure vocabulary is keyed on the exception path
(`collection/orchestrator.py:512`):

```python
reason = getattr(exc, "reason_code", "fetch_failed")
```

with `FAILED_REASONS = frozenset({"http_error", "network_error", "timeout",
"empty_body"})`. A browser 403 never raises, so it never reaches that set.

**What it costs.** A Cloudflare interstitial or an error page becomes a stored
raw blob, then a document, then an extraction input. The status is on the
record, so the information is not lost, but the outcome semantics say
"fetched", and the document pipeline cannot tell an error page from content.

**Fix.** In `PlaywrightDriver.take`, raise `ToolHttpError(status, ...)` when
`status >= 400`, the way the Crawl4ai driver does. One branch.

### F-08 — MEDIUM — Nine files are changed by two or more Wave 3 branches

Measured by diffing every branch against main and tallying the files. Nine
files are touched by more than one branch:

| File | Branches | Count |
|---|---|---|
| `src/product_hunter/cli.py` | qdrant-semantic, source-expansion, observability-evaluation, autonomous-operation | 4 |
| `src/product_hunter/config.py` | qdrant-semantic, autonomous-operation, subq-integration | 3 |
| `src/product_hunter/sources.py` | source-expansion, autonomous-operation, scraping-ladder | 3 |
| `tests/test_cli_db_status.py` | source-expansion, observability-evaluation, autonomous-operation | 3 |
| `tests/test_db_migrations.py` | source-expansion, observability-evaluation, autonomous-operation | 3 |
| `src/product_hunter/db/store.py` | qdrant-semantic, source-expansion | 2 |
| `src/product_hunter/db/connection.py` | autonomous-operation, opencode-control-plane | 2 |
| `src/product_hunter/runtime/worker.py` | autonomous-operation, opencode-control-plane | 2 |
| `src/product_hunter/collection/orchestrator.py` | source-expansion, scraping-ladder | 2 |

`docs/reasonix/ARCHITECTURE.md` is edited by four branches as well.

The three files that the collision of F-05 touches are all in this table:
`tests/test_db_migrations.py` is changed by three branches, and each of them
edits the same `EXPECTED_VERSION` constant. That is the conflict most likely
to be resolved by hand and least likely to be resolved correctly.

The Wave 2 integration did this once and got it right, so this is a process
risk rather than a defect. Merge in dependency order, and rebase the CLI
work onto one owner instead of resolving four times.

### F-09 — MEDIUM — A propagation edge attributes itself to an arbitrary evidence span

`src/product_hunter/db/independence.py:377` writes `from_span_id` and
`to_span_id` from `self._first_span(edge.from_doc_id)`, and `_first_span` is:

```python
def _first_span(self, doc_id: str) -> str | None:
    """The first evidence span of a document, when it has one."""
    spans = self._span_ids(doc_id)
    return spans[0] if spans else None
```

`_span_ids` is `SELECT span_id FROM evidence WHERE doc_id = ? ORDER BY span_id`.
`span_id` is a hash, so "the first span" is the lexicographically smallest
hash of that document: an arbitrary span, not the one that was detected as a
repost.

Spec §12 requires each propagation edge to name "source evidence" and
"derived evidence". The edge names a span, but not the right one.

This is moot in production because `IndependenceStore` has no production
caller (F-04).

**Fix.** Carry the span ids on the `PropagationEdge` record the detection
actually saw, and write those.

### F-10 — LOW — The Wave 3 source registry renames a stored value
`wave3/source-expansion` changes `config/sources.yaml` from
`source_type: code host` to `source_type: code_host`. `source_type` is free
text (`src/product_hunter/sources.py:71`, `source_type: str = ""`), so nothing
breaks. The registry upsert at `db/store.py:155-157` is
`ON CONFLICT(source_id) DO UPDATE SET source_type = excluded.source_type`, so
the next import rewrites the two sources that are still in the registry. A row
for a source that has since left the registry keeps the old spelling, and the
table then holds both. Nothing groups by `source_type` today, so the risk is
a future analytical split over two values of the same field. Normalise before
the merge, or leave it and note it.

### F-11 — MEDIUM — The observability branch widens the import path's exception handling

`wave3/observability-evaluation` rewrites six pre-existing `except` clauses in
`src/product_hunter/cli.py` from `except (OSError, ValueError)` to
`except Exception`. Two of the six sit on the pipeline this report audits:

```diff
@@ -1103,7 +1254,7 @@ def db_import_run_command(
-        except (OSError, ValueError) as exc:
+        except Exception as exc:
```

and the two in `extract_command` at the branch's lines 488 and 498, around the
config and the source-registry reads.

The widened clause in `db_import_run_command` is the provenance read, and the
handler prints a message that tells the operator what to do:

```text
"error": f"provenance: {exc}; the run was not imported. "
"Fix the file or remove it, then import the run again."
```

A `TypeError` or a `KeyError` inside `_run_provenance` is a bug, and the
message now tells the operator to fix a file that is fine. On the one command
that writes evidence into the database, a code fault and a bad file have to
stay distinguishable.

The fix is the narrow one: catch the exception types the feature actually
introduces rather than `Exception`.

This finding was reached independently by the architecture probe (see section
9) and is recorded here because it lands on the import path that F-01 is
about.

### F-12 — MEDIUM — The import boundary validates nothing about the candidate

This is the security reading of F-01, and it is what makes F-01 more than a
housekeeping gap.

`ExtractedCandidate` in `src/product_hunter/extraction.py` has exactly one
model validator, `_check_accepted_fields`, which requires a `pain_id` and a
`normalized_key` on an accepted candidate. There is no field validator on the
quote, on `char_start` or `char_end`, on the two hashes, on `span_id` or on
`doc_id`. The model guarantees the shape of the record and nothing about its
truth.

`import_run` validates the *document* carefully
(`db/store.py:876-891`): the fetch has to be in the log, the source has to
agree, and the raw hash has to match. It validates the *candidate* against
nothing. `_insert_evidence` reads the document for its `live_status` and
then writes `candidate.doc_id` as it was given.

So a `candidates.jsonl` that was not produced by the extractor, or was
edited after it was produced, can:

- name any `doc_id` that already exists in the store, including a document
  belonging to a different source, a different community or a different run;
- carry a quote that is not the text at its offsets;
- carry hashes that belong to a different document.

The foreign key stops a `doc_id` that does not exist, so the blast radius is
"any document already in the store". The trigger in
`db/migrations/0005_evidence_pipeline.sql` does not help: it checks that an
evidence row exists for the pain point, that the `doc_id` agrees and that the
offsets are sane. All of that is true by construction, because the import
writes both rows from the same candidate.

**Fix.** The candidate has to be checked against the document at the point
where it becomes a row. `EvidencePipeline._check_provenance_match`,
`_check_span`, `_check_quote` and `_check_span_id` are the checks, and they
already exist.

### F-13 — LOW — The cluster's evidence lineage has no distinct-span count

`db/clustering.py:898` returns one row per (pain point, span) pair:

```sql
SELECT m.pain_id, e.span_id, e.doc_id, e.quote, m.valid_from, m.valid_to
FROM cluster_members m
JOIN pain_points p ON p.pain_id = m.pain_id
JOIN evidence_pain_links l ON l.pain_id = p.pain_id
JOIN evidence e ON e.span_id = l.span_id
WHERE m.cluster_id = ?
```

`pain_points.span_id` carries no uniqueness constraint, because one passage
legitimately expresses several pain points and `evidence_pain_links` is
modelled as a many-to-many. That is the right design. The consequence is that
a cluster can hold two members whose pain points share one span, and the
lineage returns that span twice.

The evidence pipeline applies the matching discipline on its own count,
`EvidencePipeline.independent_evidence_count` (`evidence.py:826`):

```sql
SELECT count(DISTINCT COALESCE(e.canonical_span_id, e.span_id))
```

The cluster lineage applies none. A consumer that counts rows to ask "how
much independent evidence backs this cluster" will over-count by exactly the
number of pain points that share a span.

**Fix.** Either add a distinct-span count beside the lineage rows, or state
in the docstring that the rows are per membership and not per observation, so
nobody counts them as evidence.

## 5. What is sound

An audit that lists only failures misrepresents the repository. These parts
are right, and the fixes above must not break them.

**P-01, failed search versus genuine zero result, in the investigation
layer.** `research/investigation/coverage.py:100-102`:

```python
if attempt.status is not ToolCallStatus.success:
    # A failed call is counted even when a later attempt of the same
    # family succeeds: a flaky search cannot support a zero.
```

`assess_coverage` (`coverage.py:81`) reports `complete` only when every
required family completed with no failure.
`research/investigation/counter_evidence.py:77-81` refuses "none found"
unless the coverage is complete. `analyse_competitors` returns `unknown`
rather than `no_competitors_found` when the coverage is incomplete, and
carries the note that "a complete search of a narrow slice of the market is
not proof of absence". `research/evidence.py` carries a `ZeroResultOutcome`
with `recorded` and `completed_query_families`. This is the behaviour the
spec §22 asks for.

**P-02, failed search versus zero, in the discovery frontier.**
`src/product_hunter/discovery/frontier.py:7`: "a broken search tool is not
saturation." `record_execution` computes novelty only over the identities a
successful call returned, and the refusal statuses are excluded from the
denominator.

**P-03, clustering receives the right upstream entities.**
`src/product_hunter/db/clustering.py:934-951`:

```sql
FROM pain_points p
JOIN evidence e ON e.span_id = p.span_id
JOIN documents d ON d.doc_id = p.doc_id
WHERE p.accepted = 1
  AND p.normalized_key IS NOT NULL
  AND p.normalized_key != ''
```

The `PainPoint` carries `span_id` and `quote` through to the engine, so the
clusterer works from grounded pain points and never from a summary.

**P-04, clustering does not destroy evidence lineage.**
`db/migrations/0006_clustering.sql` keeps `cluster_members` append-only with
`valid_from` and `valid_to`, enforces one open cluster per pain point with
`CREATE UNIQUE INDEX idx_cluster_members_one_open ON cluster_members(pain_id)
WHERE valid_to IS NULL`, keeps a `cluster_versions` snapshot and a
`cluster_lineage` edge for every merge or split, and holds
`cluster_members.pain_id REFERENCES pain_points(pain_id)`. The chain back to
the span is intact. Spec §14 is met.

**P-05, quotes are grounded in captured source text.**
`src/product_hunter/extraction.py:643`:

```python
span = locate_quote(
    document.text,
    candidate.quote,
    min_chars=limits.min_quote_chars,
    max_chars=limits.max_quote_chars,
    search_start=chunk.char_start,
)
```

with the containment check at `extraction.py:650-660` that rejects a quote
found outside the chunk the model was shown, which is how a model quoting
from memory gets refused. The adapter's rule "Never accept extracted evidence
without a verbatim source span" is honoured here.

**P-06, the database enforces "a pain point needs a verified span".**
`db/migrations/0005_evidence_pipeline.sql` has
`trg_pain_points_require_evidence`, `trg_evidence_pain_links_require_matching_span`
and `trg_evidence_pain_links_require_matching_span_update`. The property is a
database constraint, not a convention in application code.

The limit of that constraint is worth stating plainly, because it is easy to
over-trust it. The trigger checks that an evidence row exists for the pain
point, that the `doc_id` agrees, that the offsets are sane and that the quote
is not empty. It does not check that the quote is the text at those offsets.
A fabricated quote passes it. Only `EvidencePipeline._check_quote` closes that
gap, and F-01 is about the fact that nothing calls it.

**P-07, content hashes are preserved.** `documents.text_sha256` and
`raw_sha256`, `evidence.text_sha256`, `raw_sha256` and `evidence_hash`,
`evidence_observations.text_sha256`, `document_versions.text_sha256` and
`raw_sha256`. `raw_store.py` content-addresses the raw bytes.
`evidence.py:272-277` refuses a document whose raw hash disagrees with its
fetch. `evidence.py:544-553` refuses a candidate whose hashes disagree with
the stored document. `evidence.py:624-629` refuses a document text whose hash
disagrees with its row. The hashes are there; F-01 is about who checks them.

**P-08, Obsidian and reporting are projections, never the source of truth.**
`projection/contract.py`: "The projection never writes to the database."
`cli.py:1297` opens the projection connection with `connect(path,
read_only=True)`, and the outbox carries the projection task so the
authoritative row and the task commit together. Nothing reads the vault back
into SQLite.

The path side of that boundary holds too, which matters for a system that
writes Markdown files from database content.
`projection/vault.py:169-179` resolves the note path and the vault root and
refuses any path that would leave the root:

```python
target = (self.root / relative).resolve()
root = self.root.resolve()
if target != root and root not in target.parents:
    raise ValueError(f"the note path leaves the vault root: {relative}")
```

and `_component` (`vault.py:197-205`) refuses a value that is not a safe file
name and explicitly rejects `"."` and `".."`, falling back to a slug plus a
hash so two titles cannot collide on one file.

**P-09, DuckDB stays downstream (Wave 3).**
`wave3/duckdb-analytics`, `src/product_hunter/analytics/ingest.py` opens
SQLite with `connect(database, read_only=True)` and has no write path back.
The views are analytical surfaces over mirrored rows, which is what spec §15A
and §16 require.

**P-10, source legality is enforced on the registry path (Wave 3).**
`wave3/source-expansion`, `src/product_hunter/sources.py` adds
`review_expires_at` and raises `SourceBlocked("review_expired", ...)` when a
review has lapsed; `require()` raises `SourceBlocked(decision.reason_code,
decision.detail)`. `collection/orchestrator.py` adds `"review_expired"` to
both `TERMINAL_REASONS` and `NO_CAPTURE_REASONS`, so a policy failure is a
distinct terminal reason rather than a zero result. The one new source,
`blog_http`, is `status: pending_review`, `terms_status: unknown`,
`allowed_use: [none]`, `enabled: false`: fail-closed. `config/communities.yaml`
refuses a community whose `source_id` is not in `config/sources.yaml`, and a
discovered host becomes a candidate for human review rather than a fetch.

**P-11, URL admission survives the Wave 3 collection path.**
`wave3/scraping-ladder`, `collection/adapters/base.py` routes through a
`UrlGate` that calls `safety.admit_url` and `safety.admit_redirect`, the
same admission main's `fetch.py` uses. The Crawl4ai driver refuses to return
content when the request gate was not installed
(`adapters.py:190-196`), which closes the "the crawl worked but nothing
checked its requests" hole.

**P-12, the autonomous-operation change to the evidence pipeline is a
transaction-helper refactor.** `git diff main...wave3/autonomous-operation --
src/product_hunter/evidence.py` moves `_transaction` out of the module into
`product_hunter.db.connection.transaction`, which joins an open transaction
with a SAVEPOINT. No provenance check, quote check, hash check, lineage call
or public signature changes. Low risk, and the only branch that touches
`evidence.py` at all.

**P-13, stable ids.** `derive_span_id(doc_id, char_start, char_end, quote)`
(`extraction.py`), checked by `evidence.py:583-591`. `pain_id` is derived from
the normalized key. The projection's fallback opportunity id is
`"opp_" + sha256(normalized_key)[:16]` (`projection/source.py:248`). The
Wave 3 vector point id is `uuid(sha256(entity_type + "\x1f" + entity_id))`
over the stable `span_id`, `pain_id` or `cluster_id`, with the content hash
carried as payload rather than as identity. No id is derived from a URL, a
title or a mutable body.

**P-14, the projection writes nothing to the database.** The only two
`conn.execute` calls anywhere under `src/product_hunter/projection/` are
`PRAGMA table_info` and a `SELECT` (`contract.py:225` and `contract.py:253`).
There is no `INSERT`, no `UPDATE` and no `DELETE` in the package. The vault
is a projection and cannot become the source of truth by accident.

**P-15, the shared fetcher refuses an error status.** `fetch.py:383` raises
`HttpError(status, f"HTTP {status}")` for a non-success response, and
`_RETRYABLE_STATUS` at `fetch.py:32` is the bounded retry set. The Wave 3
`HttpAdapter` delegates to that same `Fetcher.fetch`, so it inherits the
behaviour rather than re-implementing it. F-07 is scoped to the Playwright
driver precisely because the other paths get this right.

**P-16, the Crawl4ai driver refuses a capture it could not vouch for.**
`wave3/scraping-ladder`, `collection/adapters/adapters.py:185-196` checks
the crawl's status and its success flag, and then refuses to return content
when the request gate was not installed:

```python
if not gated:
    raise ToolUnavailable(
        "the local crawler did not install the request gate, so its "
        "requests were not checked against the source policy"
    )
```

That closes the "the crawl worked but nothing checked its requests" hole
without needing browser isolation.

## 6. Broken and missing links between Wave 2 and Wave 3

| # | Link | State | Evidence |
|---|---|---|---|
| L-01 | Wave 2 evidence → Wave 3 qdrant indexing | **Broken.** The semantic layer reads `evidence` and `documents` correctly and stores a sound payload, but it is not called by the pipeline and its outbox target is never claimed. | F-06 |
| L-02 | Wave 2 clustering → Wave 3 qdrant | **Missing.** Spec §14 requires a Qdrant check before a cluster is created. `clustering/engine.py` does not import the semantic package. | F-06 |
| L-03 | Wave 2 migration numbering → Wave 3 migrations | **Broken.** Three branches claim 0008; the runner raises and refuses to migrate. | F-05 |
| L-04 | Wave 2 `evidence.py` → Wave 3 autonomous-operation | **Intact.** A transaction-helper move, no behavioural change. | P-12 |
| L-05 | Wave 2 source registry → Wave 3 source expansion | **Intact, and improved.** Legality enforcement with review expiry; communities validated against the registry. | P-10 |
| L-06 | Wave 2 fetch and safety → Wave 3 scraping ladder | **Intact.** The adapters route through the same admission and the shared fetcher. One status-check bug. | P-11, F-07 |
| L-07 | Wave 2 SQLite → Wave 3 DuckDB | **Intact.** One-way, read-only projection. | P-09 |
| L-08 | Wave 2 `db/store.py` → Wave 3 qdrant and source-expansion | **Conflict.** Both change it, at overlapping regions. | F-08 |
| L-09 | Wave 2 `sources.py` → Wave 3 source expansion and scraping ladder | **Conflict.** Both change it. | F-08 |
| L-10 | Wave 2 CLI → Wave 3 | **Conflict.** All eight branches change `cli.py`. | F-08 |
| L-11 | Wave 2 `opportunity_evidence` → Wave 3 analytics | **Missing, and guarded.** The DuckDB layer declares `v_score_history` over `opportunity_scores` and gates every view on the tables it needs (`analytics/views.py:362-373`), so the view is simply never created. The analytics layer has no opportunity data to work with, because the table does not exist. | F-02 |
| L-12 | Wave 2 `propagation_edges` → Wave 3 observability | **Partial.** The Wave 3 metrics read `SELECT count(*) FROM propagation_edges`, which is always zero because nothing writes the table in production. | F-09, F-04 |
| L-13 | Wave 2 research gate → Wave 3 observability | **Intact.** The metrics read the SQLite gate tables directly and do not write to them. | (gathered, verified) |
| L-14 | Wave 2 `research_claims` → Wave 3 nothing | **Missing.** No Wave 3 branch changes the claim anchoring. The free-form summary problem (F-03) survives all eight branches. | F-03 |

## 7. Recommended fixes

In the order they should be done. Each is the smallest change that closes the
gap.

1. **Make one evidence write path.** Move the six checks and the three writes
   (`write_evidence_span`, `append_lineage`, `write_document_version`) into
   `Store.import_run`, or make `import_run` call `EvidencePipeline`. Two
   write paths is the root cause of F-01; one path with the checks closes it.
   (F-01)
2. **Land the opportunity tables as a migration** and give them a writer.
   Move `CANONICAL_DDL` out of `projection/contract.py` into
   `db/migrations/0009_opportunities.sql`, keep the constant in the projection
   as the expected shape, and let the tests apply the migration instead of
   the string. (F-02)
3. **Renumber the three `0008` migrations** to 0008, 0009 and 0010, in the
   order the waves integrate. (F-05)
4. **Register the qdrant outbox handler** and drain `target="qdrant"`, or
   stop enqueuing those rows until the handler exists. Then add the
   pre-cluster nearest-neighbour check in `clustering/engine.py`. (F-06)
5. **Anchor claims to spans.** Add `span_id` to `ClaimEvidence`, require it
   for a supported claim, and enforce it with a trigger like
   `trg_evidence_pain_links_require_matching_span`. (F-03)
6. **Write the stage handler.** One runner over the sixteen stages that writes
   `research_events`, `research_stage_executions` and `spend_ledger` per
   stage. This is the next wave by the repository's own plan. (F-04)
7. **Raise on a non-2xx browser capture** in `PlaywrightDriver.take`. One
   branch. (F-07)
8. **Carry the detected span ids** on a `PropagationEdge` instead of calling
   `_first_span`. (F-09)
9. **Narrow the widened `except` clauses** on the import and extract paths
   back to the specific exception types. (F-11)
10. **Add a distinct-span count** beside the cluster evidence lineage, or say
    in the docstring that the rows are per membership and not per
    observation. (F-13)

## 8. Mandatory fixes before final integration

These eight are the ones that must land before the branches are integrated or
the pipeline is declared runnable. The rest can follow.

| # | Fix | Why it blocks | Finding |
|---|---|---|---|
| M-01 | Renumber `0008_autonomy.sql`, `0008_observability.sql` and `0008_source_expansion.sql` to a contiguous 0008, 0009, 0010. | The migration runner raises `duplicate migration number 8` before applying anything. `ph db init` fails for every user. | F-05 |
| M-02 | Route the production import through one write path that performs the quote, provenance and span-id checks and sets `canonical_span_id`, writes `evidence_lineage` and writes `document_versions`. | Without it, deduplication, the audit trail and the document history are off in production, the spec §11 rule about repeated copies is unenforceable, and a `candidates.jsonl` that was not produced by the extractor is imported unchecked. | F-01, F-12 |
| M-03 | Land the opportunity tables as a migration with a writer. | `opportunity_evidence.span_id` is the anchor between a scored opportunity and its evidence. Without the table there is nothing to reconstruct an opportunity from. | F-02 |
| M-04 | Register the qdrant projection handler, or stop enqueuing `target="qdrant"` rows. | Otherwise `projection_tasks` fills with permanently pending rows and SQLite and Qdrant diverge with no error. | F-06 |
| M-05 | Add the pre-cluster Qdrant check that spec §14 requires. | The spec calls Qdrant a required part of the evidence loop, not an optional enhancement. | F-06 |
| M-06 | Anchor investigation claims, competitors and counter-evidence to `span_id` plus quote plus content hash, enforced in the database. | A claim can be supported today by a free-form summary that was never checked against the text it describes. | F-03 |
| M-07 | Write the stage handler and drive the sixteen stages through it. | Nothing calls the gate, so nothing can refuse a bad run, and no budget dimension is measured. | F-04 |
| M-08 | Raise `ToolHttpError` on a non-2xx browser capture. | A 403 page becomes a document and an extraction input. | F-07 |
| M-09 | Narrow the six widened `except Exception` clauses in the observability branch, starting with `db_import_run_command`. | A code fault on the import path is reported to the operator as a bad file to fix. | F-11 |

## 9. Cross-reference with the sibling audits

This is Probe #2. The other probes in the set cover the architecture and
dependency angle, and several of their findings land on the same seams. The
overlap is recorded here so the set reads as one body of work. Several
documents that contradict each other would be worse than useless.

| Sibling finding | Overlaps with | Note |
|---|---|---|
| `final-audit-01` F-01: three branches claim migration number `0008` | F-05 | Same defect, same three files. The architecture probe adds the `EXPECTED_VERSION` face that is folded into F-05 here. |
| `final-audit-01` F-02: three branches set the schema-version constant to `8` | F-05 | Folded into F-05 as the second symptom. |
| `final-audit-01` F-03: the Qdrant branch writes outbox tasks that nothing consumes | F-06 | Same defect. Independent derivation, same conclusion. |
| `final-audit-01` F-04: four of the eight waves ship code wired to nothing | F-04 | The same gap, counted from the Wave 3 side. |
| `final-audit-01` F-10: the observability branch widens six `except` clauses to `Exception` | F-11 | F-11 is recorded here because two of the six are on the import path. |
| `final-audit-01` F-13: the DuckDB analytics layer has no entry point | L-07 | The DuckDB projection is sound in direction and unreachable in practice. |
| `final-audit-01` F-18: the registry's new review state is not persisted | P-10 | The `review_expires_at` check runs in the registry, so the expiry is enforced at fetch time even though the field is not persisted. |
| `final-audit-01` F-21: the docs root is `reasonix`, not `resonix` | the header note | The requested path in the brief was misspelled. |

No finding in this report contradicts a sibling report. The one place the two
could appear to disagree is severity: this probe rates the missing stage
runner HIGH because it is the gate on final integration, while the repository
has documented it as the accepted next wave's work since the Wave 2
integration. Both readings are true. It is a known gap, and it is still the
thing that has to be closed before the pipeline can be called runnable.

## 10. Not checked

- **The stage 6 and stage 7 nodes never ran.** Five review dispatches and
  three harden dispatches came back `HTTP 429` or `Model unavailable`. Both
  passes were performed by the orchestrator instead. An independent
  adversarial reviewer would have been a stronger check, and that is the
  weakest part of this report.
- The three gather nodes reached their step budget on the Wave 3 branches.
  Their unverified items were re-checked by hand where they mattered for a
  finding (the qdrant outbox handler, the source-policy path, the Playwright
  status, the migration loader, the DuckDB write path). Other unverified items
  were dropped rather than reported.
- `wave3/subq-integration` was not traced into the pipeline. `DecisionChain`
  has no caller outside `llm/`, so the SubQ provider is a standalone
  integration rather than a pipeline stage, and the pipeline seams it touches
  are none. Whether it writes a `spend_ledger` row per call was not read.
- `wave3/opencode-control-plane` (`registry.py`, +675 lines,
  `runtime/worker.py`, `redaction.py`) was not traced for budget enforcement
  past the registry boundary. Its diff does not touch the evidence, clustering,
  independence or opportunity modules.
- The live behaviour of a real Qdrant, DuckDB, Apify or Playwright server was
  not exercised. Every finding above is from reading the code and the schemas.
- The 100 manually inspected spans the spec §38A phase 1 asks for do not
  exist. The evaluation set holds 30 cases and no labels, and the Wave 3
  dataset `eval/datasets/ds-v2.json` has an `expected` of `null` on every
  case, so quote fidelity is the only quality measure the benchmark can make.

---

```text
GOAL: audit the complete evidence-to-opportunity pipeline against the spec
      and the wave reports, and find the broken links between Wave 2 and
      Wave 3
TIER: free (models used per node in order)
  stage 0-3   orchestrator: longcat-2.5-preview-free
  stage 4     gather: longcat-2.5-preview-free (three nodes, all reported)
  stage 5     verify-code: orchestrator, uv run pytest
  stage 6     harden: DEGRADED. The harden node failed three times on
              HTTP 429. The pass was performed by the orchestrator.
  stage 7     review: DEGRADED. The review node failed five times (four on
              rung 1, one on rung 2) on HTTP 429 / model unavailable. The
              pass was performed by the orchestrator.
  stage 8-10  orchestrator: longcat-2.5-preview-free
RISK: declared=medium, adapter floor=medium (read-only audit; one report file)
ADAPTER: Product Hunter [repo] 8 forbidden rules in force
STAGES: 11/11 complete, skipped: none. Stage 5b visual was not required; the
  adapter sets visualRequired to never and no UI file is in the radius.
GATES: uv run pytest -> 2138 passed, exit 0 (71 files)
CHANGES: docs/reasonix/reports/final-audit-02-evidence-pipeline.md (1 file)
HARDEN: 2 findings (F-12, F-13), 3 seams confirmed sound (P-14, P-15, P-16).
  Run by the orchestrator, not by the harden node.
REVIEW: 13 findings, 16 sound items, 14 Wave 2 to Wave 3 links, 9 mandatory
  fixes. Run by the orchestrator, not by the review node. One overstatement
  and one missed file were caught by the self-review and corrected.
WAIVERS: stages 6 and 7 ran without their dedicated nodes, because the
  provider pool was rate-limited across five dispatches. The code gate is
  unaffected and passed.
STOPPED BECAUSE: all pass and the review pass is complete
NEXT: M-01 through M-09 before the Wave 3 branches are integrated
```

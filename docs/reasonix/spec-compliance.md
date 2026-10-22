# Spec compliance checklist

**Spec:** `docs/reasonix/specs/Product-Hunter-Full-Spec.md`
**Repository state:** branch `main`, 7 migrations, 45 tables, 2133 tests.
**Checked:** 2026-10-22, after the Wave 2 final integration.

Every verdict below was checked against the code, not against a document. A
skill file that describes a method is documentation, not an implementation, and
a table with a reserved name and no writer is not a feature. Where a section
is partly built, the document says which half.

The Wave 2 integration report is
[wave-2-final-integration.md](reports/wave-2-final-integration.md). It carries
the branch map, the conflicts, the security findings and the test counts.

## Summary

| Verdict | Count |
|---|---|
| DONE | 9 |
| PARTIALLY DONE | 38 |
| NOT DONE | 3 |
| DEFERRED BY DESIGN | 1 |

## The checklist

### 1. Mission — PARTIALLY DONE

The evidence chain is real: raw bytes to fetch record, to document, to quote
span, to pain point, to cluster, to opportunity, with a deterministic id at
every hop (`raw_store.py`, `fetch.py`, `documents.py`, `extraction.py`,
`normalization.py`, `db/evidence.py`, `db/clustering.py`,
`opportunity/`). What is missing is the runner: no stage handler drives a run
through those hops.

### 2. OpenCode-native design — PARTIALLY DONE

The plugin at `.opencode/plugins/product-hunter.ts` and the ten command files
under `.opencode/commands/` exist and work. The five OpenCode agents of
spec §4 do not: there is no `.opencode/agents/` directory.

### 3. Model strategy — PARTIALLY DONE

Fast-model routing is implemented and role-driven
(`llm/routing.py`, `llm/catalog.py`, `llm/discovery.py`,
`db/model_routing.py`, `config/model_routing.yaml`). All thirteen roles of
§3 are configured chains. SubQ Snap is Phase 7 and absent. The chain for a
role is a list of patterns, resolved against a catalogue discovered from
OpenCode at runtime, and no model is named in the business logic.

### 4. OpenCode agents — NOT DONE

No agent definitions. The five agents of spec §4 would live under
`.opencode/agents/`, and the directory does not exist. The restricted
toolset of spec §6A is therefore not enforced either.

### 5. OpenCode skills — PARTIALLY DONE

Ten skills exist under `.opencode/skills/`, each naming the spec section it
implements. They are methodology documents. Spec §6A requires the contract to
be enforced in code, and a skill is not code, so the skills document intent
rather than satisfy the requirement.

### 6. MCP and tool layer — PARTIALLY DONE

`opencode.json` is committed and declares three MCP servers: `qdrant`,
`duckdb`, and `apify`. The audit of all eighteen available servers, and the
reason each of the other fifteen is deferred or unusable, is in
`docs/TOOLING.md`.

The product itself has no MCP code and no MCP client. Its own tool layer is
the HTTP fetcher, the source registry and the collector ladder, which is what
the pipeline uses. The ladder of spec §8 is coded and the rungs that are not
installed refuse with `collector_unavailable`.

### 6A. Tool enforcement and research contracts — PARTIALLY DONE

The contract is a typed schema (`research/contract.py`), the gate turns
evidence into a verdict with stable violation codes (`research/gate.py`), the
budget refuses a call that would break a hard limit (`research/budget.py`),
and the query families are worker-owned rather than model-invented
(`research/query_plan.py`).

The evidence the gate judges has a home. Migration
`db/migrations/0003_research_gate.sql` holds `tool_calls`,
`research_stage_executions`, `research_stage_artifacts`, `research_claims`, and
`stage_gate_results`. `db/research_gate.py` writes them, reads them back,
rebuilds a run's evidence from the stored rows, measures the budget from the
same rows, and exposes `require_promotable()`, which raises `GateBlocked` when
a run may not be promoted.

What is missing is the caller. No worker handler records a tool call or an
artifact, no CLI command evaluates a run, and there is no promotion path to
refuse. The investigation engine bridges into the gate through
`investigation_stage_execution()`, and the discovery store writes a discovery
record as a tool-call-shaped row, but nothing calls either.

### 6B. LLM output validation and evaluation — PARTIALLY DONE

Structured output is validated against a strict schema and a quote is checked
to be an exact span of the chunk the model was shown
(`extraction.py`). The evaluation package under `src/product_hunter/eval/`
holds ten task families, a labelled-case format, a runner, and a comparator,
and `ph bench` reaches it. The seed dataset holds 30 cases and 0 human
labels, so the benchmark can measure quote fidelity and nothing else.

### 7. Discovery sources — PARTIALLY DONE

`config/sources.yaml` carries the legality fields the spec asks for —
`terms_status`, `allowed_use`, `reviewed_at`, `review_basis`,
`robots_or_api_notes`, `collector`, and a rate-limit policy — and the registry
fails closed for an unknown, disabled, or unreviewed source. Two sources are
admitted, one is disabled, and Reddit is absent while its terms are unknown.

Only HTTP collection is installed. The heavier collectors of spec §8 are
absent, and the ladder refuses their rungs.

### 7A. Query frontier and exploration strategy — DONE

The ten query families, the eight controlled wording variants, the mandatory
query templates, and the per-stage mapping of which families a stage must
cover are worker-owned in `research/query_plan.py`, and two predicates enforce
them through the gate. The negative and alternative-to families are mandatory,
so a validation run may not only collect people who agree with the thesis.

The persistent frontier is built: `discovery/frontier.py` records a parent
query, the family, the novelty yield, the duplicate yield, the source yield,
the independent-author yield and the last-seen time, and it stops on the
budget or the saturation rule rather than on a fixed number of results. It has
no caller.

### 8. Collection and crawling stack — PARTIALLY DONE

`httpx` does the fetching, and BeautifulSoup with lxml does the parsing
(`fetch.py`, `documents.py`). The collector ladder of spec §8 is coded in
`collection/ladder.py`: an official API, a feed, direct HTTP, a local
crawler, browser automation, and a managed scraper, in that order of
preference. The last three rungs are not installed and refuse with
`collector_unavailable`. Crawl4AI, Playwright and Apify are absent.

### 9. Rate limiting and caching — DONE

`fetch.py` holds a per-source minimum interval, a bounded retry set
(`429`, `500`, `502`, `503`, `504`), a `Retry-After` path, exponential
backoff, ETag and `Last-Modified` validators, content-addressed raw storage,
and a rejection record for every blocked or failed fetch. The collection layer
adds a per-run request cap, a per-source ledger that survives a restart, and
a validator cache in `collection_source_state`.

### 10. Data processing pipeline — PARTIALLY DONE

Discovery, collection, raw evidence, normalization, pain extraction, entity
resolution, clustering, independence validation, scoring, competitor
research, counter-evidence, deep investigation and the opportunity record are
all built as libraries with stores and tests. They are not chained: no stage
handler runs one stage and hands its output to the next.

### 11. Evidence model — DONE

A document carries a `text_sha256`, a parser version and a content version.
An evidence span carries the quote, the character range, and the match mode,
and an observation history and a lineage trail. The raw bytes are
content-addressed. The database holds `documents`, `evidence`,
`evidence_pain_links`, and `pain_points`, and three triggers make a pain
point impossible without a verified span.

### 12. Independent evidence — PARTIALLY DONE

The independence graph is built: `independence/` resolves authors and
communities, detects reposts, quotes, cross-posts and syndication, and
computes the raw mention count, the canonical observation count, the
independent author count, the uncertain author count, the independent
community count, the propagation-adjusted count and the source concentration
by code, not by a model. A model may supply a signal, and the policy refuses
a configuration that would let a weak signal reach a merge threshold.

`propagation_edges` has its columns and no writer, and nothing calls the
package.

### 13. Strong versus weak pain — DONE

Severity and the strong/weak split are enforced at extraction time: a
`genuine` pain point needs a named actor, a named workflow, a concrete cost,
and severity 2 to 5; `weak` needs severity 1 or 2; `not_pain` needs severity 0
(`extraction.py`). The scoring side of §13 — willingness to pay, market size,
the niche override — is in `opportunity/`.

### 14. Semantic memory — PARTIALLY DONE

The clustering similarity layer takes an embedding model's identity and
dimensions, derives the vector space id from them, and records them
(`clustering/similarity.py`). There is no vector store and no Qdrant adapter,
and the local embedding model is imported lazily and was never run.

### 15. Database — DONE

The connection applies `foreign_keys=ON`, `busy_timeout=5000`,
`synchronous=NORMAL`, and `journal_mode=WAL` for a file database. Migrations
are numbered, discovered, and applied one file per transaction, and a failure
leaves the schema and the version as they were. The schema holds forty-five
tables across seven migrations. A read-only connection opens the file in
`mode=ro` and never creates it. `ph db status` reports the schema it finds.

The spec's §38B requirement that each migration carry rollback notes and a
restore canary is not met, and is recorded under §38B.

### 15A. Cross-store consistency and transactional outbox — PARTIALLY DONE

The outbox is real: `enqueue_projection()` is called inside the same
`BEGIN IMMEDIATE` that writes the evidence, so the authoritative row and the
projection task commit together. Projection tasks are leased, retried, and
recovered.

The Obsidian target has a handler (`projection/engine.py`), and
`ph project --outbox` runs the outbox. The Qdrant and DuckDB sides of §15A
are absent, and no other target has a handler.

### 16. Analytics — NOT DONE

No DuckDB code. The MCP server is configured; the product does not use it.

### 17. Queue — DONE

The SQLite job queue holds the status constraint, the unique idempotency key,
the deterministic job id, the claim order, the attempt counter, the lease
fields, and the fenced write (`db/jobs.py`). A job past its attempt ceiling
becomes `dead` and stays visible.

### 18. Scheduler — PARTIALLY DONE

`Scheduler.tick()` walks the enabled schedules, applies the source gate, and
writes the due occurrences under a stable idempotency key of
`schedule:{name}:{occurrence}`, so a restart creates nothing twice.
`default_schedules()` holds the five schedules the spec names.

Nothing runs it. There is no entry point and no command, because no stage
handler implements the stages a schedule would create.

### 19. Event system — PARTIALLY DONE

The worker emits one JSON object per line to stderr, and `research_events`
holds the run-level events. The internal domain vocabulary of spec §19 is
absent.

### 20. Opportunity scoring — PARTIALLY DONE

Potential and Confidence are two separate scales in `opportunity/scoring.py`,
with the willingness-to-pay tiers `W0` to `W4`, a market range with its
assumptions, and a recorded reason for any niche override. The score is a
content-addressed snapshot that names the policy version and the input
snapshot ids.

There is no `opportunities` table, so a score has no row to live in, and
nothing calls the scoring.

### 21. Counter-evidence — PARTIALLY DONE

The counter-evidence assessment is built in
`research/investigation/counter_evidence.py`. A zero result is only reachable
on a complete coverage, so an incomplete search reads as incomplete rather
than as "no counter-evidence exists". The query families come from code-side
templates, not from the model.

Nothing calls it.

### 22. Research stopping and budgets — PARTIALLY DONE

The budget is a typed spec with per-run, per-stage, and per-source scopes, a
soft-threshold fraction, and a tracker that refuses a call that would break a
hard limit. The gate measures an overrun against the contract's limits, and
the database path measures it from the stored tool calls and spend rows.

The stopping policy and the saturation rule are carried by the contract and
read by no code. The `spend_ledger` has one writer, the discovery store, so
the other budget dimensions are still unmeasured.

### 23. Opportunity lifecycle — PARTIALLY DONE

The opportunity states and their allowed transitions are in
`opportunity/lifecycle.py`, and an invalid transition is refused. The states
live in a library rather than in a table, and nothing drives them.

### 24. Opportunity history — PARTIALLY DONE

The cluster lineage is in the database: a membership that closes is closed
with a `valid_to` and never deleted, a version row holds one snapshot, and a
lineage edge points from a merged or split cluster to its successor. The
opportunity score history is a content-addressed snapshot with no table
behind it.

### 25. Competitor and market research — PARTIALLY DONE

The competitor search is built in `research/investigation/competitors.py`, and
the query families come from code-side templates. The contract reserves the
`competitor_search` gate key, and the engine produces the stage execution the
gate judges. Nothing calls the engine, and the market-size range of §20 is not
computed.

### 26. Validation experiments — PARTIALLY DONE

The experiment record is built in `opportunity/experiments.py`, and the
projection reads an `experiments` table when one exists. There is no table,
and nothing generates an experiment.

### 27. Obsidian output — DONE

The projection is built in `projection/`. The vault is a projection of the
database and never a second copy of the truth: the layer is read-only against
SQLite, writes only under the vault root, and a note a human edited is
overwritten by the next projection. Two-way synchronisation is not built.

The projection is deterministic, idempotent and regenerable. A second run
writes no file and changes no file time, and the vault can be deleted and
rebuilt.

### 28. Individual idea folder — PARTIALLY DONE

The projection writes the idea folder when an opportunity exists, and falls
back to the pain points and the evidence when it does not. There is no
`opportunities` table, so the folder is built from the clusters.

### 29. Dashboard — DONE

`ph project` writes the dashboard: the top opportunities, the recent
discoveries, the rising opportunities, the falling opportunities, and the
notes that need human review.

### 30. Daily brief — PARTIALLY DONE

The daily brief is rendered by the projection into the daily-briefs folder.
The scheduler names a morning-brief schedule and no handler serves it.

### 31. Research requests — PARTIALLY DONE

`ph research create` takes an objective and writes a durable run. The
`--chat-id` is recorded. The richer request fields of spec §31 are not
modelled.

### 32. Git — PARTIALLY DONE

Git is infrastructure. The product has no Git write path, and §38B's retention
and drift rules are unimplemented.

### 33. Runtime and checkpoints — PARTIALLY DONE

The checkpoint container is implemented, idempotent, conflict-detecting, and
fenced: a write by a leased worker is refused unless the caller holds a live
lease on a job for that run and stage, judged against the store's clock.

The resume orchestration does not exist. Nothing calls `load_checkpoint()` to
resume work, and the checkpoint does not carry the pending items, source
cursors, budget state, or lease epoch that spec §41.5 lists.

### 34. Observability — PARTIALLY DONE

`stage_attempts`, `model_executions`, `tool_calls`, `research_events`, and
the worker's JSON event stream record what happened. The `spend_ledger` has
one writer, and the model-execution rows carry latency and status but not
token counts.

### 35. Infrastructure — PARTIALLY DONE

Python, SQLite, and Git are in use. There is no container, no service
definition, and no deployment path in the repository.

### 36. Python stack — DONE

Python 3.14, Pydantic, httpx, BeautifulSoup with lxml, PyYAML, and pytest are
all in use.

### 37. Security and permissions — PARTIALLY DONE

The SSRF and URL-safety layer of `safety.py` is real: scheme allowlist, no
userinfo, no IP literal, host allowlist, ports 80 and 443 only, public-IP
resolution that fails closed on any private address, and per-hop redirect
re-validation with no HTTPS-to-HTTP downgrade.

The approval gate for a destructive operation is not implemented, and the
repository holds no OpenCode permission configuration. Spec §37's approval
gate is `BLOCKED` on a write path that does not exist.

### 37A. Untrusted content and prompt-injection boundary — PARTIALLY DONE

The fetch path admits only allowlisted hosts and public addresses, untrusted
text is delimited in the extraction prompt and labelled as data, and a
subprocess is only ever invoked with an argument vector and `shell=False`. The
extraction prompt is assembled by literal substitution, so a brace in a
scraped page cannot break its structure. The projection sanitiser neutralises
HTML, wikilinks, embeds, Templater expressions, Dataview queries, comments,
headings, table rows, blockquotes, code fences and frontmatter delimiters, and
blocks every URL whose scheme is not `http` or `https`.

The browser isolation of §37A does not exist, because no browser collector is
installed. The one open residual is the DNS-rebind window between admission
and connect, which the code carries as a labelled `shortcut:` marker.

### 38. Components deliberately not required — DEFERRED BY DESIGN

Spec §38 lists LangChain, LangGraph, LangSmith, Redis, BullMQ, Temporal,
LiteLLM, OpenTelemetry, and Prometheus as optional and excluded from the
core. The spec calls this an intentional design decision, not an unfinished
system, and the repository agrees: none of the nine is present, and the
architecture gives the reason.

### 38A. Implementation phases — PARTIALLY DONE

Phase 1 is partly met, Phase 2 is met, Phases 3, 4 and 6 are partly met,
Phase 5 is met, and Phase 8 is not met. Phase 7 is deferred by design. The
table is at the end of this document.

### 38B. Retention, backup, migration, and drift — NOT DONE

No retention tiers, no online backup, no restore canary, no disk or WAL
watchdog, no parser-drift canary, and no rollback notes on the migrations.
The spec asks for all six, and the repository has none of them. This is the
largest single gap in operational readiness.

### 39. Repository structure — PARTIALLY DONE

The shape is close: `src/product_hunter/` with the `db`, `research`,
`runtime`, `llm`, `eval`, `evidence`, `discovery`, `collection`, `clustering`,
`independence`, `opportunity` and `projection` subpackages, plus
`db/migrations/`, `config/`, `docs/`, `eval/`, and `tests/`. The spec's
`collectors/`, `processing/`, `agents/`, and `docker/` directories do not
exist, because the code that would live in them has not been written.

### 40. End-to-end architecture — PARTIALLY DONE

The ownership split of §40 holds: OpenCode is the interaction surface, the
Python worker owns execution, SQLite is the state, and the outbox carries the
projections. What is missing from the pipeline is the part that does not
exist yet — Qdrant, DuckDB, the stage handler, and the run state machine that
advances the stages.

### 41. Research registry, persistence, and continuation — PARTIALLY DONE

The registry is real: `create`, `list`, `status`, `continue`, `resume`,
`pause`, `retry`, `cancel`, and `log` act on durable runs in SQLite, with a
stable public id of `RH-YYYYMMDD-NNNN`, an event log, and a run log. The
plugin reaches it as a subprocess with an argument vector.

§41.2's stage state machine does not exist as a separate machine: the registry
holds the run state, and the job queue holds the stage work, but nothing
advances a run from one stage to the next. §41.6's `continue` semantics exist
as a registry action and not as a resume of interrupted work.

### 42. The 24/7 autonomous loop — PARTIALLY DONE

The worker runtime, the single-instance lock, the scheduler, and the
recovery path all exist. Nothing runs them in a loop, there is no service
definition, and no stage handler exists to execute.

### 43. Interactive research command UX — PARTIALLY DONE

Ten command files map to the registry actions, the tool returns the state
layer's own text, and a bare `/continue` with more than one eligible run asks
which one through the question tool rather than guessing.

The interactive picker of spec §43 is text, not a UI.

### 44. Definition of done — PARTIALLY DONE

Of the thirty-two items, twelve are met, seventeen are partly met, one is
deferred and two are not met. The list is at the end of this document.

## Phase 38A status

| Phase | State | Evidence |
|---|---|---|
| 1 — hand-prove extraction and normalization | PARTIALLY MET | Quote-verbatim validation, deterministic ids and the fail-closed triggers in `extraction.py` and `0005_evidence_pipeline.sql`; the 100 manually inspected spans are not evidenced, because the evaluation set holds 30 cases and 0 labels |
| 2 — evidence core | MET | Fenced leases in `db/jobs.py`, the outbox in `db/outbox.py`, the checkpoint fence, `Store.import_run`; `tests/test_db_crash_recovery.py` |
| 3 — semantics and independence | PARTIALLY MET | Clustering, cluster lineage, entity resolution and propagation-aware independence in `clustering/` and `independence/`. No embedding run, no Qdrant, no DuckDB, and `propagation_edges` has no writer |
| 4 — research gate and adversarial validation | PARTIALLY MET | The contract, the gate, the budget, the persistence, the fail-closed `require_promotable()`, the scoring, the competitor search and the counter-evidence all exist and are tested. No stage handler calls any of it |
| 5 — OpenCode interface | MET | The plugin, the ten commands, the registry actions and the cross-chat resume from persistent state |
| 6 — controlled scale and projections | PARTIALLY MET | The Obsidian projection with its outbox handler, the model routing and the source budgets exist. No Qdrant or DuckDB adapter, and the crawler, browser and managed rungs are not installed |
| 7 — optional heavy infrastructure | DEFERRED BY DESIGN | §38A gates it behind a measured need |
| 8 — operational hardening | NOT MET | No backup, restore, retention, disk watermark, parser canary, or migration rollback notes |

## The definition of done (spec §44)

| # | Requirement | State | Evidence |
|---|---|---|---|
| 1 | `product-hunter` available inside OpenCode | MET | The plugin tool and ten commands; the agent definitions of §4 are separate and absent |
| 2 | OpenCode can create, inspect, pause, resume, retry, cancel | MET | `registry.py` actions, exercised by `tests/test_registry.py` |
| 3 | A worker owns autonomous execution independently of chat | PARTIALLY MET | The worker runtime exists; no stage handler is wired |
| 4 | Autonomously discover public evidence | PARTIALLY MET | The discovery engine and the frontier are built; no caller |
| 5 | Collect and preserve provenance | PARTIALLY MET | The collection layer, the ladder and the attempt ledger are built; no caller |
| 6 | Identify independence, propagation-aware | PARTIALLY MET | `independence/` computes the counts deterministically; `propagation_edges` has no writer and nothing calls it |
| 7 | Extract and normalize pain points | MET | `extraction.py`, `normalization.py` |
| 8 | Semantically cluster problems | PARTIALLY MET | `clustering/` with the lineage; the embedding path never ran |
| 9 | Score opportunities with separate potential and confidence | PARTIALLY MET | `opportunity/scoring.py`; no `opportunities` table and no caller |
| 10 | Use SubQ for high-volume structured decisions | DEFERRED | Deferred to Phase 7 |
| 11 | Route simple tasks to fast models | MET | `llm/routing.py`, `config/model_routing.yaml` |
| 12 | Route hard tasks to a reasoning model | PARTIALLY MET | The chain leads with the reasoning family; only the extraction role is bound to a call path |
| 13 | Research competitors and alternatives | PARTIALLY MET | `research/investigation/competitors.py`; no caller |
| 14 | Actively seek counter-evidence | PARTIALLY MET | `research/investigation/counter_evidence.py`; a zero needs a complete coverage, and no caller |
| 15 | Reject weak or already-solved opportunities | PARTIALLY MET | The lifecycle and the gate can refuse; nothing drives them |
| 16 | Track opportunity history and cluster lineage | PARTIALLY MET | The cluster lineage is in the database; the score history has no table |
| 17 | Detect stale opportunities | PARTIALLY MET | The lifecycle knows its states; no scheduler drives it |
| 18 | Generate validation experiments | PARTIALLY MET | `opportunity/experiments.py`; no table and no generator |
| 19 | Write and continuously update the Obsidian vault | MET | `projection/`, `ph project`, `ph project --outbox` |
| 20 | Run scheduled jobs without manual intervention | PARTIALLY MET | The scheduler exists; no runner |
| 21 | Recover from failures, crashes, shutdowns, stale workers | MET | `tests/test_db_crash_recovery.py` kills a real child process |
| 22 | Research state idempotent and protected by leases and fencing | MET | `db/jobs.py`, `db/leases.py`, `db/checkpoints.py` |
| 23 | Cross-store projections converge through a transactional outbox | PARTIALLY MET | The Obsidian target has a handler and the outbox runs; Qdrant and DuckDB do not exist |
| 24 | Untrusted internet content cannot trigger tools or side effects | MET | The admission, the prompt delimiter and the literal prompt assembly, the argv discipline, the projection sanitiser |
| 25 | Structured LLM output passes schema and deterministic validation | MET | `extraction.py` |
| 26 | Record spend, tool provenance, evidence provenance, checkpoint history | PARTIALLY MET | Evidence provenance and checkpoints are real; the spend ledger has one writer and the tool-call rows have none |
| 27 | Research ids stay stable across machines | MET | The `RH-` public id and the hash-derived run id |
| 28 | `/list-research` and cross-chat continuation from persistent state | MET | The command and the registry act on SQLite |
| 29 | Explain a score by tracing claims to source evidence | PARTIALLY MET | The snapshot names its policy version and its input snapshot ids; the end-to-end trace is not built |
| 30 | Required research gates fail closed | PARTIALLY MET | `require_promotable()` raises `GateBlocked`; 62 tests in `tests/test_db_research_gate.py`; no caller |
| 31 | The §38A phases pass their acceptance criteria | NOT MET | Phases 2 and 5 met; 1, 3, 4 and 6 partly; 8 not |
| 32 | Source legality, retention, backup, drift canaries, budget enforcement operationally verified | NOT MET | Source legality and budget enforcement are in code; the rest is not built |

## What is deferred, and why

| Integration | State | Reason |
|---|---|---|
| SubQ Snap | Absent | Spec §38A Phase 7, behind a benchmark gate on schema validity, classification accuracy, duplicate and propagation decisions, latency, cost, and failure rate. The pipeline never reads the key; `.env.example` names it empty. |
| Apify | Absent from the product | Spec §38A Phase 7. The MCP server is configured in `opencode.json` and the worker does not use it. |
| Crawl4AI | Absent | Spec §8 and §38A Phase 7, for larger-scale crawling. The ladder refuses the rung. |
| Large-scale Playwright collection | Absent | Spec §8 and §37A, for JavaScript-heavy sites, and §37A requires browser isolation first. |
| Langfuse | Absent | Spec §34: only after the core evidence pipeline is correct and a Phase 7 benchmark justifies it. |
| Redis, Temporal | Absent | Spec §38 excludes them from the initial core. SQLite holds the queue. |
| Obsidian, both directions | One direction only | Spec §38A Phase 6 for the projection, Phase 7 for the two-way interaction. The projection is built and one-way. |

## The honest summary

The repository holds a complete, tested and fenced evidence core, eight
complete Wave 2 subsystems, and a working one-way Obsidian projection. It does
not hold a running research pipeline. The gap is not a missing feature here
and there; it is that the stages of spec §10 have no runner, so the gate
that could refuse a bad run is never asked to, and the seven libraries that
hold the intelligence are never called.

The operational work of spec §38B does not exist either, and the
specification's own acceptance criteria for the phases are not met. The
system is not production-ready for unattended long-term operation, and the
specification says so itself.

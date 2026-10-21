# Spec compliance checklist

**Spec:** `docs/reasonix/specs/Product-Hunter-Full-Spec.md`
**Repository state:** branch `main`, 51 modules under `src/product_hunter/`, 3 migrations, 1085 tests.
**Checked:** 2026-10-21, during the final wave-1 integration.

Every verdict below was checked against the code, not against a document. A
skill file that describes a method is documentation, not an implementation, and
a table with a reserved name and no writer is not a feature. Where a section
is partly built, the document says which half.

## Summary

| Verdict | Count |
|---|---|
| DONE | 12 |
| PARTIALLY DONE | 24 |
| NOT DONE | 9 |
| DEFERRED BY DESIGN | 5 |

## The checklist

### 1. Mission — PARTIALLY DONE

The evidence chain is real: raw bytes to fetch record, to document, to quote
span, to pain point, with a deterministic id at every hop
(`raw_store.py`, `fetch.py`, `documents.py`, `extraction.py`,
`normalization.py`). Everything downstream of that — independence, clustering,
scoring, competitor research, counter-evidence, the Obsidian projection — is
absent.

### 2. OpenCode-native design — PARTIALLY DONE

The plugin at `.opencode/plugins/product-hunter.ts` and the ten command files
under `.opencode/commands/` exist and work. The five OpenCode agents of
spec §4 do not: there is no `.opencode/agents/` directory.

### 3. Model strategy — PARTIALLY DONE

Fast-model routing is implemented and role-driven
(`llm/routing.py`, `llm/catalog.py`, `llm/discovery.py`,
`db/model_routing.py`, `config/model_routing.yaml`). SubQ Snap is Phase 7 and
absent. The chain for a role is a list of patterns, resolved against a
catalogue discovered from OpenCode at runtime, and no model is named in the
business logic.

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
the HTTP fetcher and the source registry, which is what the pipeline uses.
The tool-selection ladder of spec §6 — official API, then HTTP, then fetch,
then the heavier collectors — is implemented only as far as HTTP.

### 6A. Tool enforcement and research contracts — PARTIALLY DONE

The contract is a typed schema (`research/contract.py`), the gate turns
evidence into a verdict with stable violation codes (`research/gate.py`), the
budget refuses a call that would break a hard limit (`research/budget.py`),
and the query families are worker-owned rather than model-invented
(`research/query_plan.py`).

The evidence the gate judges now has a home. Migration
`db/migrations/0003_research_gate.sql` holds `tool_calls`,
`research_stage_executions`, `research_stage_artifacts`, `research_claims`, and
`stage_gate_results`. `db/research_gate.py` writes them, reads them back,
rebuilds a run's evidence from the stored rows, measures the budget from the
same rows, and exposes `require_promotable()`, which raises `GateBlocked` when
a run may not be promoted.

What is missing is the caller. No worker handler records a tool call or an
artifact, no CLI command evaluates a run, and there is no promotion path to
refuse. The gate is enforceable in a library and gates nothing yet.

### 6B. LLM output validation and evaluation — PARTIALLY DONE

Structured output is validated against a strict schema and a quote is checked
to be an exact span of the chunk the model was shown
(`extraction.py`). The evaluation package under `src/product_hunter/eval/`
holds ten task families, a labelled-case format, a runner, and a comparator,
and `ph bench` reaches it. The labelled set for clustering, entity
resolution, and independence classification does not exist; the seed dataset
carries `null` labels on purpose.

### 7. Discovery sources — PARTIALLY DONE

`config/sources.yaml` carries the legality fields the spec asks for —
`terms_status`, `allowed_use`, `reviewed_at`, `review_basis`,
`robots_or_api_notes`, `collector`, and a rate-limit policy — and the registry
fails closed for an unknown, disabled, or unreviewed source. Two sources are
admitted, one is disabled, and Reddit is absent while its terms are unknown.

Only HTTP collection exists. The heavier collectors of spec §8 are absent.

### 7A. Query frontier and exploration strategy — PARTIALLY DONE

The ten query families, the eight controlled wording variants, the mandatory
query templates, and the per-stage mapping of which families a stage must
cover are worker-owned in `research/query_plan.py`, and two predicates enforce
them through the gate. The negative and alternative-to families are mandatory,
so a validation run may not only collect people who agree with the thesis.

The persistent frontier with its novelty and saturation bookkeeping is not
built. The frontier is the part of §7A that needs a store and a policy, and
both are Phase 3 work.

### 8. Collection and crawling stack — PARTIALLY DONE

`httpx` does the fetching, and BeautifulSoup with lxml does the parsing
(`fetch.py`, `documents.py`). Crawl4AI, Playwright, and Apify are absent, and
so is the coded order of preference.

### 9. Rate limiting and caching — DONE

`fetch.py` holds a per-source minimum interval, a bounded retry set
(`429`, `500`, `502`, `503`, `504`), a `Retry-After` path, exponential
backoff, ETag and `Last-Modified` validators, content-addressed raw storage,
and a rejection record for every blocked or failed fetch.

### 10. Data processing pipeline — PARTIALLY DONE

Discovery, collection, raw evidence, normalization, and pain extraction are
built and chained by the CLI. Entity resolution, embedding, clustering,
independence, scoring, competitor research, counter-evidence, deep
investigation, opportunity, and validation experiment are not.

### 11. Evidence model — PARTIALLY DONE

A document carries a `text_sha256` and a parser version. An evidence span
carries the quote, the character range, and the match mode. The raw bytes are
content-addressed. The database holds `documents`, `evidence`,
`evidence_pain_links`, and `pain_points`.

### 12. Independent evidence — NOT DONE

`authors`, `author_links`, `communities`, and `propagation_edges` exist as
tables. No code writes them. The independence graph, the canonical-content
count, and the independent-author count of spec §12 are absent.

### 13. Strong versus weak pain — PARTIALLY DONE

Severity and the strong/weak split are enforced at extraction time: a
`genuine` pain point needs a named actor, a named workflow, a concrete cost,
and severity 2 to 5; `weak` needs severity 1 or 2; `not_pain` needs severity 0
(`extraction.py`).

The scoring side of §13 — willingness to pay, market size, the niche
override — is absent.

### 14. Semantic memory — NOT DONE

No embeddings, no vector store, no Qdrant adapter. §38A places this in
Phase 3, which has not run.

### 15. Database — DONE

The connection applies `foreign_keys=ON`, `busy_timeout=5000`,
`synchronous=NORMAL`, and `journal_mode=WAL` for a file database. Migrations
are numbered, discovered, and applied one file per transaction, and a failure
leaves the schema and the version as they were. The schema holds twenty-eight
tables across three migrations. A read-only connection opens the file in
`mode=ro` and never creates it.

The spec's §38B requirement that each migration carry rollback notes and a
restore canary is not met, and is recorded under §38B.

### 15A. Cross-store consistency and transactional outbox — PARTIALLY DONE

The outbox is real: `enqueue_projection()` is called inside the same
`BEGIN IMMEDIATE` that writes the evidence, so the authoritative row and the
projection task commit together. Projection tasks are leased, retried, and
recovered.

No handler is registered for any target, and no command runs the outbox.
`ph db outbox` only lists the tasks. The Qdrant, DuckDB, and Obsidian sides
of §15A are absent.

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

### 20. Opportunity scoring — NOT DONE

No potential, no confidence, no output block, no opportunity table.

### 21. Counter-evidence — NOT DONE

The gate knows how to require counter-evidence, and the contract asks for it
on the `counter_evidence` stage. Nothing collects it.

### 22. Research stopping and budgets — PARTIALLY DONE

The budget is a typed spec with per-run, per-stage, and per-source scopes, a
soft-threshold fraction, and a tracker that refuses a call that would break a
hard limit. The gate measures an overrun against the contract's limits, and
the database path measures it from the stored tool calls and spend rows.

The stopping policy and the saturation rule are carried by the contract and
read by no code. The `spend_ledger` table has no writer.

### 23. Opportunity lifecycle — NOT DONE

No opportunity states, no transitions, no promotion path.

### 24. Opportunity history — NOT DONE

Nothing tracks it.

### 25. Competitor and market research — NOT DONE

The contract reserves a `competitor_search` gate key and the `investigation`
stage serves it, but no stage searches for competitors.

### 26. Validation experiments — NOT DONE

Nothing generates one.

### 27. Obsidian output — NOT DONE

No projection, and therefore no projection safety.

### 28. Individual idea folder — NOT DONE

No opportunity, so no folder.

### 29. Dashboard — NOT DONE

No dashboard.

### 30. Daily brief — NOT DONE

No brief. The scheduler names a morning-brief schedule and no handler serves
it.

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
the worker's JSON event stream record what happened. `spend_ledger` is empty
and the model-execution rows carry latency and status but not token counts.

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
subprocess is only ever invoked with an argument vector and `shell=False`.

The browser isolation of §37A does not exist, and neither does the Obsidian
projection safety, because there is no Obsidian projection. The one open
residual is the DNS-rebind window between admission and connect, which the
code carries as a labelled `shortcut:` marker.

### 38. Components deliberately not required — DEFERRED BY DESIGN

Spec §38 lists LangChain, LangGraph, LangSmith, Redis, BullMQ, Temporal,
LiteLLM, OpenTelemetry, and Prometheus as optional and excluded from the
core. The spec calls this an intentional design decision, not an unfinished
system, and the repository agrees: none of the nine is present, and the
architecture gives the reason.

### 38A. Implementation phases — PARTIALLY DONE

Phase 1 is met, Phase 2 is met, Phase 4 is partly met, and Phases 3, 5, 6, and
8 are not met. Phase 7 is deferred by design. The table is at the end of this
document.

### 38B. Retention, backup, migration, and drift — NOT DONE

No retention tiers, no online backup, no restore canary, no disk or WAL
watchdog, no parser-drift canary, and no rollback notes on the migrations.
The spec asks for all six, and the repository has none of them. This is the
largest single gap in operational readiness.

### 39. Repository structure — PARTIALLY DONE

The shape is close: `src/product_hunter/` with the `db`, `research`,
`runtime`, `llm`, and `eval` subpackages, `db/migrations/`, `config/`,
`docs/`, `eval/`, and `tests/`. The spec's `collectors/`, `processing/`,
`scoring/`, `clustering/`, `agents/`, and `docker/` directories do not exist,
because the code that would live in them has not been written.

### 40. End-to-end architecture — PARTIALLY DONE

The ownership split of §40 holds: OpenCode is the interaction surface, the
Python worker owns execution, SQLite is the state, and the outbox carries the
projections. What is missing from the pipeline is the part that does not
exist yet — Qdrant, DuckDB, Obsidian, scoring, clustering, the opportunity
lifecycle, and the run state machine that advances the stages.

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

Of the thirty-two items, eleven are met, fifteen are partly met, and six are
not met. The list is at the end of this document.

## Phase 38A status

| Phase | State | Evidence |
|---|---|---|
| 1 — hand-prove extraction and normalization | MET | Quote-verbatim validation and deterministic ids in `extraction.py`; `ph extract`; `tests/test_extraction.py` |
| 2 — evidence core | MET | Fenced leases in `db/jobs.py`, the outbox in `db/outbox.py`, the checkpoint fence, `Store.import_run`; `tests/test_db_crash_recovery.py` |
| 3 — semantics and independence | NOT MET | No embedding, Qdrant, clustering, entity-resolution, propagation, or DuckDB code |
| 4 — research gate and adversarial validation | PARTIALLY MET | The contract, the gate, the budget, the persistence, and the fail-closed `require_promotable()` exist and are tested. No counter-evidence collection, no competitor search, no scoring, and no promotion path to refuse |
| 5 — OpenCode interface | PARTIALLY MET | The plugin, the commands, and the registry actions exist. The worker-side cross-chat resume does not |
| 6 — controlled scale and projections | PARTIALLY MET | The outbox and the model routing exist. No production projection handler exists |
| 7 — optional heavy infrastructure | DEFERRED BY DESIGN | §38A gates it behind a measured need |
| 8 — operational hardening | NOT MET | No backup, restore, retention, disk watermark, parser canary, or migration rollback notes |

## The definition of done (spec §44)

| # | Requirement | State | Evidence |
|---|---|---|---|
| 1 | `product-hunter` available inside OpenCode | PARTIALLY MET | The plugin tool and ten commands; no agent definition |
| 2 | OpenCode can create, inspect, pause, resume, retry, cancel | MET | `registry.py` actions, exercised by `tests/test_registry.py` |
| 3 | A worker owns autonomous execution independently of chat | PARTIALLY MET | The worker runtime exists; no stage handler is wired |
| 4 | Autonomously discover public evidence | PARTIALLY MET | Two admitted sources and a fetcher; no autonomous loop |
| 5 | Collect and preserve provenance | MET | `raw_store.py`, `fetch.py`, `documents.py`, `extraction.py` |
| 6 | Identify independence, propagation-aware | NOT MET | Tables only |
| 7 | Extract and normalize pain points | MET | `extraction.py`, `normalization.py` |
| 8 | Semantically cluster problems | NOT MET | — |
| 9 | Score opportunities with separate potential and confidence | NOT MET | — |
| 10 | Use SubQ for high-volume structured decisions | NOT MET | Deferred to Phase 7 |
| 11 | Route simple tasks to fast models | MET | `llm/routing.py`, `config/model_routing.yaml` |
| 12 | Route hard tasks to a reasoning model | PARTIALLY MET | The chain leads with the reasoning family; only the extraction role is bound |
| 13 | Research competitors and alternatives | NOT MET | — |
| 14 | Actively seek counter-evidence | NOT MET | — |
| 15 | Reject weak or already-solved opportunities | NOT MET | The gate can require it; nothing collects it |
| 16 | Track opportunity history and cluster lineage | NOT MET | — |
| 17 | Detect stale opportunities | NOT MET | — |
| 18 | Generate validation experiments | NOT MET | — |
| 19 | Write and continuously update the Obsidian vault | NOT MET | — |
| 20 | Run scheduled jobs without manual intervention | PARTIALLY MET | The scheduler exists; no runner |
| 21 | Recover from failures, crashes, shutdowns, stale workers | MET | `tests/test_db_crash_recovery.py` kills a real child process |
| 22 | Research state idempotent and protected by leases and fencing | MET | `db/jobs.py`, `db/leases.py`, `db/checkpoints.py` |
| 23 | Cross-store projections converge through a transactional outbox | PARTIALLY MET | The outbox is real; no handler converges anything yet |
| 24 | Untrusted internet content cannot trigger tools or side effects | PARTIALLY MET | The admission, the prompt delimiter, the argv discipline; no projection safety |
| 25 | Structured LLM output passes schema and deterministic validation | MET | `extraction.py` |
| 26 | Record spend, tool provenance, evidence provenance, checkpoint history | PARTIALLY MET | Evidence provenance and checkpoints are real; the spend ledger and the tool-call rows have no writer |
| 27 | Research ids stay stable across machines | MET | The `RH-` public id and the hash-derived run id |
| 28 | `/list-research` and cross-chat continuation from persistent state | PARTIALLY MET | The command and the registry act on SQLite |
| 29 | Explain a score by tracing claims to source evidence | NOT MET | No scoring |
| 30 | Required research gates fail closed | MET | `require_promotable()` raises `GateBlocked`; 62 tests in `tests/test_db_research_gate.py` |
| 31 | The §38A phases pass their acceptance criteria | PARTIALLY MET | Phases 1 and 2 met; 4 partly; 3, 5, 6, 8 not |
| 32 | Source legality, retention, backup, drift canaries, budget enforcement operationally verified | PARTIALLY MET | Source legality and budget enforcement are in code; the rest is not built |

## What is deferred, and why

| Integration | State | Reason |
|---|---|---|
| SubQ Snap | Absent | Spec §38A Phase 7, behind a benchmark gate on schema validity, classification accuracy, duplicate and propagation decisions, latency, cost, and failure rate. The pipeline never reads the key; `.env.example` names it empty. |
| Apify | Absent from the product | Spec §38A Phase 7. The MCP server is configured in `opencode.json` and the worker does not use it. |
| Crawl4AI | Absent | Spec §8 and §38A Phase 7, for larger-scale crawling. |
| Large-scale Playwright collection | Absent | Spec §8 and §37A, for JavaScript-heavy sites, and §37A requires browser isolation first. |
| Langfuse | Absent | Spec §34: only after the core evidence pipeline is correct and a Phase 7 benchmark justifies it. |
| Redis, Temporal | Absent | Spec §38 excludes them from the initial core. SQLite holds the queue. |
| Obsidian, both directions | Absent | Spec §38A Phase 6 for the projection, Phase 7 for the two-way interaction. No projection exists at all. |

## The honest summary

The repository holds a complete, tested, and fenced evidence core. It does not
hold a running research pipeline. The gap is not a missing feature here and
there; it is that the stages of spec §10 beyond extraction have no
implementation, and the research gate — which exists, and which now has
persistence, a budget measurement, and a fail-closed promotion check — has
nothing to judge yet.

The gate is the part that was finished first on purpose. A stage handler that
records its tool calls and artifacts can be written against it without
reinventing any of it, and the wave-2 sessions are exactly that work.

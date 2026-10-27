# Plan: architecture and operations documentation wave

**RUN_ID:** arch-docs-20261021-4f9c2b71
**Date:** 2026-10-21
**Definition:** `docs/reasonix/specs/architecture-documentation-spec.md`
**Handoff:** `docs/reasonix/handoffs/architecture-documentation-handoff.md`

---

## 1. Mapping implemented reality onto the document set

The full specification has 44 sections. The documents in this wave do not
repeat it. Each one takes the slice an operator or a reviewer needs, and each
slice is cut along the line between what the code does and what the
specification only intends.

| Spec section | Lands in | Cut |
|---|---|---|
| §15, §15A, §17, §18, §41.3 | ARCHITECTURE | SQLite as the only authority; the nineteen tables; the queue |
| §33, §41.4, §41.5, §41.9 | ARCHITECTURE, RECOVERY | leases, fencing, checkpoints, crash recovery |
| §15A | ARCHITECTURE | the transactional outbox and why it commits together |
| §11, §6B | ARCHITECTURE | the provenance chain from raw bytes to an accepted record |
| §34 | OPERATIONS | what is recorded where |
| §9, §22 | OPERATIONS | rate limits, request budgets, the provider call budget |
| §7, §7A, §8, §37A | SECURITY | the source registry, URL admission, the untrusted-content boundary |
| §37, §6A | SECURITY | credentials, the forbidden rules, the human approval boundary |
| §6A, §6B, §22, §38A Phase 4 | RESEARCH_CONTRACT | what the code enforces today, and what a gate still has to do |
| §3, §34 | MODEL_ROUTING_IMPLEMENTATION | routing principles, identity, fallback, and the honest status |
| §2, §6, §40 | ARCHITECTURE | the role of OpenCode, the role of MCP, and the ownership split |
| §41.2, §41.6, §41.8 | ARCHITECTURE, RECOVERY | the run registry that does not exist yet |

The specification's own §38, the list of components deliberately not required,
is the source for the `OPTIONAL` label: LangChain, LangGraph, LangSmith,
Redis, BullMQ, Temporal, LiteLLM, OpenTelemetry, Prometheus. Qdrant, DuckDB,
and Obsidian are not in that list, so they are `PLANNED`, not `OPTIONAL`.

## 2. The judgement call in this wave

`MODEL_ROUTING_IMPLEMENTATION.md` is conditional in the task: it is to be
written "if the model-router work is already visible in the branch".

The work is visible. `docs/reasonix/specs/model-routing.md` exists and is
approved, and the implementation landed in the working tree while this wave
was running: `src/product_hunter/llm/routing.py`, `llm/catalog.py`,
`llm/discovery.py`, `src/product_hunter/db/model_routing.py`,
`db/migrations/0002_model_routing.sql`, `config/model_routing.yaml`, and a
`ph models` command. The document is written, and it describes what the code
does.

The same is true of `RESEARCH_CONTRACT.md`. The name could be read as
claiming the §6A contract exists. It does, in code: `src/product_hunter/research/`
holds the contract schema, the gate engine, the budget model, and the
provenance records. The honest limits are narrower than "does not exist" and
wider than "it works": the code is complete and reachable as a library, and
nothing calls it, so it gates nothing yet. The tool-call and gate-result
persistence is protocols only, and its docstring names
`db/migrations/0002_research_gate.sql`, a number that
`0002_model_routing.sql` already took. The next one has to be 0003.

## 3. Baseline drift during the run

The tree moved while this wave was running. Three sibling waves committed
nothing but wrote code, specifications, and plans into the working tree, and
the first three documents were written against the older baseline of
`205a4ba`. They were wrong the moment they landed: they said the research
gate and the model router had no code.

The fix was to re-baseline the handoff to the working tree, note the drift in
the plan, and rewrite the three documents. Each document now carries one
baseline note saying which tree state it describes, and the handoff carries
the new facts in a section of their own.

The lesson for the commit is in the same place: the tree is moving, so the
commit stages explicit paths and nothing else, and the verification stage
re-reads the tree before it signs off.

## 4. Execution order

| Step | Node | Produces |
|---|---|---|
| 1 | `loop-free-implement-1` | `ARCHITECTURE.md`, `RESEARCH_CONTRACT.md`, `MODEL_ROUTING_IMPLEMENTATION.md`, against `205a4ba` |
| 2 | orchestrator | the handoff is re-baselined to the working tree after the sibling waves land |
| 3 | `loop-free-implement-1` | the same three documents, rewritten against the working tree |
| 4 | `loop-free-implement-1` | `OPERATIONS.md`, `RECOVERY.md`, `SECURITY.md` |
| 5 | orchestrator | `README.md` section linking the six documents, and `docs/reasonix/check_docs.py` |
| 6 | `loop-free-verify-code-1` | the code gate, `uv run pytest`, plus the link check |
| 7 | `loop-free-harden` | the security and observability pass over the new prose |
| 8 | `loop-free-review-1` | the adversarial diff review |
| 9 | orchestrator | the report and the commit |

Two implement dispatches rather than one, because each document has to be
right about a different slice of the system, and a single node writing six
documents will drift on the fourth.

## 5. The link check

The specification requires documentation and link consistency checks where
they are available. There is no doc-link linter in this repository, and
adding one is out of scope for a documentation wave. The check runs as part of
stage 5 with the standard library:

- every relative link in the six new documents and the README section
  resolves to a file that exists in the tree
- every path mentioned in backticks in the six new documents exists in the
  tree
- every `ph` command mentioned exists in the CLI
- every table name mentioned exists in `db/migrations/0001_initial.sql` or
  `db/migrations/0002_model_routing.sql`
- the four labels appear in each of the six documents, with a legend

The check is `docs/reasonix/check_docs.py`. It stays in the repository because
the next wave will need it, and because a doc-link check that is only ever
run once tends to rot.

## 6. What this wave does not do

- It does not write the code for any of the `PLANNED` items.
- It does not edit the sibling wave specifications, plans, or code.
- It does not change `README.md` beyond one linking section.
- It does not commit any sibling wave's code, even though some of it is
  uncommitted in the same tree.
- It does not push.

## 7. Risks

| Risk | Handling |
|---|---|
| A document describes a planned feature as if it shipped | the four labels, and the review stage checks each `PLANNED` claim against the absence of the module |
| The six documents contradict each other | the handoff is the single fact sheet, and both implement dispatches read it |
| The tree moves again before the commit | the verification stage re-reads the tree, and the commit stages explicit paths |
| The documents grow into a second copy of the specification | the outlines in the definition fix the content, and the review checks for overlap |
| A factual claim is wrong | every claim names a file, table, command, or function, so the reviewer can check it |

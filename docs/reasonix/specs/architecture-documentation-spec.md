# Documentation consistency wave: architecture and operations

**Status:** defined. Approved for implementation.
**Date:** 2026-10-21
**RUN_ID:** arch-docs-20261021-4f9c2b71
**Source of truth:** `docs/reasonix/specs/Product-Hunter-Full-Spec.md`
**Lane:** documentation only. No runtime code, no config, no schema change.

---

## 1. What this wave delivers

The repository has six documents that describe the system. None of them exist
yet. The README describes the Phase 1 extraction chain in prose; the full
specification describes an eight-phase system that is one and a half phases
along. An operator reading either one today cannot answer basic questions:
where the authoritative state lives, what runs unattended, what a failed run
leaves behind, which URLs may be fetched, and where a provider key comes from.

This wave writes those documents against the code that exists now, at commit
`205a4ba`.

| Document | Answers |
|---|---|
| `docs/reasonix/ARCHITECTURE.md` | How the system is put together and which part owns what |
| `docs/reasonix/OPERATIONS.md` | How to run it, day to day |
| `docs/reasonix/RECOVERY.md` | What to do when it breaks |
| `docs/reasonix/SECURITY.md` | What the trust boundary is |
| `docs/reasonix/RESEARCH_CONTRACT.md` | What the code enforces today about research quality |
| `docs/reasonix/MODEL_ROUTING_IMPLEMENTATION.md` | How models get chosen, and how far that has got |

## 2. The constraint that shapes every document

**Do not claim features that do not exist.**

The system is being built by several waves at once, and some of their work is
still in the working tree rather than in a commit. A reader who cannot tell
shipped code from an approved plan will plan work against a system that does
not run. Every claim in every document therefore carries one of four labels.

The baseline for these documents is the working tree at the time of writing,
not the last commit. Each document says so once, near the top.

| Label | Meaning |
|---|---|
| `IMPLEMENTED` | Code exists in this branch and is reachable from the CLI or the library API. Name the file. |
| `PLANNED` | A specification exists and is approved, but no code does. Name the spec. |
| `BLOCKED` | Cannot proceed until a precondition is met. Name the precondition. |
| `OPTIONAL` | Deliberately excluded from the core, per spec §38. May be added later if measured need justifies it. |

The labels are applied per claim, not per section. A single section can hold
an `IMPLEMENTED` paragraph and a `PLANNED` paragraph.

## 3. Status vocabulary

The full specification's terms are preserved exactly. The vocabulary in use
already: `research_run`, `Research Contract`, `Tool Gate`, `stage`,
`checkpoint`, `lease_epoch`, `idempotency_key`, `projection_tasks`,
`evidence span`, `pain point`, `normalized_key`, `terms_status`,
`allowed_use`, `counter-evidence`, `Potential`, `Confidence`.

Three words in the codebase differ from each other and must not be smoothed
over: a job that finishes is `succeeded`, a projection task that finishes is
`done`, and a research run that finishes is `completed`.

## 4. Document outlines

### ARCHITECTURE.md

- The layers and who owns what: OpenCode, the worker, SQLite, the filesystem.
- SQLite as the single source of truth. Where the file lives, how it is
  opened, what the pragmas are.
- The nineteen tables and what each one is for.
- Runtime artifacts: what lives in `data/`, what is gitignored, what is a
  durable mirror rather than authority.
- The worker model: claim, lease, execute, complete. What `db.worker.Worker`
  does today, and what the separate `product_hunter.runtime` package is
  specified to add.
- Leases and fencing. The exact SQL predicate.
- Checkpoints. What a checkpoint holds, how versions work, what conflicts.
- The transactional outbox. Why the projection task and the evidence row
  commit together, and what happens if the process dies between.
- Evidence provenance. The chain from raw bytes to an accepted pain point.
- OpenCode's role, MCP's role, and the human approval boundary.

### OPERATIONS.md

- The command surface, with what each command does and what it writes.
- Bringing the database up: init, migrate, status, check.
- The normal loop: fetch, extract, review, evaluate, import, report.
- Migrations, and what the migration runner does and does not detect.
- Rate limits, request budgets, and the provider call budget.
- Observability: what is recorded where, and where to look.
- A troubleshooting table keyed on the error text an operator actually sees.

### RECOVERY.md

- Failure modes and their signatures.
- Expired leases, `LeaseLost`, a killed worker, a killed process mid-run.
- The `db recover` command, and what it does and does not recover.
- Outbox convergence.
- Checkpoint resume, and the fact that nothing calls it yet.
- Restore and backup: the state today is that a backup is a file copy, and
  the online-backup canary from spec §38B is `PLANNED`.

### SECURITY.md

- The trust boundary, and the rule that untrusted content is data.
- Source safety: `terms_status`, `allowed_use`, the fail-closed admission
  codes, and the fact that URL admission is a worker decision.
- SSRF and URL safety: the admission rules and the redirect revalidation.
- Credentials: where they live, why the repo holds none, what the OpenCode
  auth store provides, and the rule that no provider key is added.
- The adapter's forbidden rules, verbatim in effect.
- Human approval boundaries: what needs a person, and what the code does
  today versus what the specification requires.

### RESEARCH_CONTRACT.md

- What a Research Contract is, per spec §6A.
- What the code enforces today: the extraction contract, schema validation,
  quote-verbatim verification, the rejection codes, the fail-closed
  behaviour, and the research contract and gate engine that now exist in
  `src/product_hunter/research/`. The gate engine is complete and reachable
  as a library, and nothing calls it yet, so it gates nothing.
- What is `PLANNED`: persisting tool calls and gate results in SQLite, and
  wiring the gate into a stage.
- The baseline thresholds from spec §6A, marked `PLANNED` with their source.
- The rule that a gate validates results and derivation, not activity.

### MODEL_ROUTING_IMPLEMENTATION.md

- The routing principles from spec §3, preserved: fast model for extraction
  and classification, SubQ for high-volume structured decisions, a reasoning
  model for investigation, and the user's interactive model staying
  independent.
- The identity rule: a model is `provider/model`, and a model id alone is
  not an identity.
- The fallback rule: a chain of candidates, and the fail-closed behaviour
  when nothing is available.
- The status: what exists today is a router, a catalogue, a discovery
  surface, a role configuration, a persistence layer, and a `ph models`
  command, and the generation path does not use the router yet.

## 5. Constraints

- Terminology from the full specification is preserved. Do not rename
  `lease_epoch`, do not write "job lease token", do not call a projection
  task a "message".
- No document repeats another. `ARCHITECTURE.md` explains the shape;
  `OPERATIONS.md` explains the commands; `RECOVERY.md` explains the
  failures. A reader should be able to read one and not need the other two
  for its own question.
- No document grows past the point where it stops being readable. ARCHITECTURE
  is the largest; SECURITY and MODEL_ROUTING are the smallest.
- Prose follows the repo's existing voice: plain, specific, willing to state
  a limit. The existing `README.md` and `docs/reasonix/reports/` are the
  reference, not a corporate template.
- `README.md` gets one short section linking the new documents. It does not
  get a summary of them.

## 6. Acceptance

| # | Criterion | How it is checked |
|---|---|---|
| A1 | Every factual claim about code names a file, a table, a command, or a function that exists | review reads the diff against the tree |
| A2 | No document claims a feature that has no code | review checks each `PLANNED` claim against the absence of the module |
| A3 | The four labels are used and defined in every document | review checks each legend |
| A4 | The three different success words are documented accurately | review checks `succeeded`, `done`, `completed` |
| A5 | No document duplicates another | review checks overlap |
| A6 | Every relative link in the new documents resolves | the link check in stage 5 |
| A7 | `uv run pytest` still passes | the code gate in stage 5 |
| A8 | The committed diff touches only the declared radius | stage 8 |

## 7. Out of scope

- Code changes of any kind.
- The `MODEL_ROUTING_IMPLEMENTATION.md` document describing an implementation
  that does not exist. The document states the routing principles and the
  implementation status, and it says plainly at the top that the routing
  implementation is not in this branch.
- Editing the sibling wave specifications, which are other agents' files.
- Adding a `docs/reasonix/README.md` index. The README at the repo root links
  the documents instead.

# Dispatch prompt: architecture and operations documentation

**RUN_ID:** arch-docs-20261021-4f9c2b71
**Node:** `loop-free-implement-1`
**Definition:** `docs/reasonix/specs/architecture-documentation-spec.md`
**Plan:** `docs/reasonix/plans/architecture-documentation-plan.md`
**Handoff, and the only fact sheet:** `docs/reasonix/handoffs/architecture-documentation-handoff.md`

---

## Read first, in this order

1. `docs/reasonix/handoffs/architecture-documentation-handoff.md`, all of it.
   It carries the verified facts about the code, the exact identifiers, the
   four-label vocabulary, the writing rules, and the list of what is NOT
   implemented. Use it rather than re-deriving the code, and use it rather
   than your own recollection of this project.
2. `docs/reasonix/prompts/architecture-docs-implement.md`, this file.
3. `docs/reasonix/specs/architecture-documentation-spec.md`, section 4, which
   fixes the outline of each document.
4. `README.md` and `docs/reasonix/reports/phase-1-step-4.md` for the voice.
   Plain, specific, willing to state a limit. The phase-1 report says what it
   does not do, and that is the register to match.

## The baseline

The documents describe the **working tree**, not the last commit. HEAD is
`205a4ba`, and several parallel waves have written code into the tree that is
not committed yet. Those modules are `IMPLEMENTED` like any other code.

Each document carries one baseline note near the top, saying which tree state
it describes and that some modules were still uncommitted when it was
written. Say it once per document. Do not hedge on every claim.

## The rule that overrides everything else

**Do not claim features that do not exist.**

Every factual claim carries one of four labels, defined in the definition and
in the handoff:

- `IMPLEMENTED` — code exists in the tree. Name the file.
- `PLANNED` — a specification exists and is approved, no code does. Name the spec.
- `BLOCKED` — cannot proceed until a precondition is met. Name the precondition.
- `OPTIONAL` — deliberately excluded from the core, per spec §38.

Put the legend near the top of every document. Apply the labels per claim,
not per section.

The trap in this wave is the opposite one. Model routing, the research gate,
the scheduler, and the worker runtime all exist now, so describing them as
`PLANNED` would be as wrong as claiming something that does not exist. Get
the label right in both directions.

## Writing rules

- Plain prose, no marketing register, no "robust", "seamless", "leverage",
  "delve", "comprehensive", "crucial", "pivotal", "in conclusion", "at its
  core", "additionally", "furthermore", "moreover", "notably", "importantly",
  "indeed", "overall", "moving forward", "unlock", "elevate", "streamline",
  "supercharge", "bridge the gap".
- "Pain point" is a domain term here and stays. The banned usage is the
  marketing phrase, not the noun.
- Vary sentence length. Never chain three short sentences in a row. Do not
  default to a list of three. One em dash per document at most.
- Never invent a number. If a count or identifier is not in the handoff or in
  the code, it does not go in the document.
- Every claim about behaviour names a file, table, command, or function.
  "The worker fences writes" is weak. "`renew_lease` refuses a row whose
  `lease_epoch` no longer matches" is checkable.
- Use the code's own identifiers: `lease_epoch`, `idempotency_key`,
  `projection_tasks`, `research_checkpoints`, `stage_attempts`,
  `terms_status`, `allowed_use`, `normalized_key`, `pain_id`, `span_id`,
  `catalog_version`, `selection_source`, `ViolationCode`, `BudgetTracker`.
- The three success words are different and must stay different: a job that
  finishes is `succeeded`, a projection task that finishes is `done`, a
  research run that finishes is `completed`.
- Do not repeat the full specification. Link to it by section number instead.
- Do not repeat another document in this wave. ARCHITECTURE explains the
  shape, OPERATIONS explains the commands, RECOVERY explains the failures.
- Relative links between the new documents use paths relative to
  `docs/reasonix/`, for example `[ARCHITECTURE.md](ARCHITECTURE.md)`. A link
  to the specification is
  `[Product-Hunter-Full-Spec.md](specs/Product-Hunter-Full-Spec.md)`.

## Document-specific instructions

### ARCHITECTURE.md

Lead with the ownership split, because that is the question an operator has
first: OpenCode is the interaction surface, the worker owns execution, SQLite
owns state, the filesystem is a mirror.

Then: the layers. SQLite as the single source of truth, with the path, the
pragmas, the connection rules, and the two migrations. The twenty-three
tables, grouped by what they are for, with the three that have no writer
named as such. Runtime artifacts: what lives under `data/`, what is
gitignored, and the rule from spec §33 that runtime files are durable
content-addressed mirrors rather than authority.

Then the execution machinery, which now has two layers. The library worker
`db.worker.Worker` and the process layer `src/product_hunter/runtime/`,
which claims a job, hands it to a caller, renews the lease from a background
thread on its own connection, and takes a single-instance file lock. Leases
and fencing with the exact SQL predicate. The checkpoint container and its
conflict rule, including `load_valid_checkpoint()`. The transactional outbox
with the reason the evidence row and the projection task commit together.

Then the subsystems that exist but are not yet on the pipeline: the model
router and its catalogue, the research contract and gate engine, the
scheduler, and the evaluation benchmark package. For each, say what it does
and, in one line, what does not call it yet. The distinction that matters
most: the router exists and `ph models` uses it, but the generation path
still calls the model pinned in `config/extraction.yaml`.

Then the provenance chain from raw bytes to an accepted pain point, with the
deterministic identifiers.

Close with the three roles that are not code: OpenCode, MCP, and the human
approval boundary. Say plainly that MCP has no code in this repository and
that the approval list from spec §37 has no implementation, because the code
has no external-account, posting, or Git write path.

Two hazards are worth recording, because an operator will hit them:

- `.gitignore` contains `runtime/`, which also matches
  `src/product_hunter/runtime/`. The worker runtime package is invisible to
  `git status` and a plain `git add` would not commit it.
- `research/persistence.py` names a planned migration
  `db/migrations/0002_research_gate.sql`, and `0002` is already taken by
  `0002_model_routing.sql`. The next research-gate migration has to be
  `0003`.

### RESEARCH_CONTRACT.md

The spine is that a contract is enforced in code, not in a prompt. Two
halves, and the first half is the larger one now.

The `IMPLEMENTED` half, in `src/product_hunter/research/`. The contract
schema and its nine parts, `validate_contract()` and the fail-closed
`ContractError`, the gate engine with its fourteen `ViolationCode` values,
the twelve metrics and eight predicates, the budget model where a call that
would exceed a hard limit is not started, the tool-call provenance records
where `result_ref` cannot contain whitespace so a hand-written summary cannot
be passed off as the original tool result, and `config/research_contract.yaml`
with `contract_version: phase4-v1` and eight required stages.

The honest limit, stated plainly: nothing outside the package imports it.
There is no CLI command, no worker handler, and no caller, so the gate is
complete and reachable as a library and gates nothing yet. The persistence
is protocols only.

The extraction contract, which was the first enforcement in this system and
still the only place the pipeline refuses a model claim: the strict pydantic
schema, the pain-validity rubric, the quote-verbatim rule, the fail-closed
rejection codes, the determinism of the identifiers, and the normalized key
with its version.

The `PLANNED` half: persisting tool calls and gate results in SQLite, and
wiring the gate into a stage so it decides something.

The rule that ties the halves together: a gate validates results and
derivation, not activity. A tool-call log is not evidence that validation
happened.

Include the baseline thresholds from spec §6A, labelled `PLANNED`, with the
source. Do not restate the whole threshold block; give the shape and point at
the section.

### MODEL_ROUTING_IMPLEMENTATION.md

The implementation exists. Say so in the first line, and say what the
baseline is.

The principles from spec §3, preserved: a fast model for extraction,
classification, summarization, tagging and filtering; SubQ for high-volume
structured decisions, as a decision layer and not the chat model; a reasoning
model for investigation, counter-evidence and the final thesis; and the user's
interactive model in OpenCode staying independent of the pipeline's routing.

The identity rule, which the implementation gets right and is worth stating:
a model is `provider/model`. The same model id on two providers is two
different models. A model id alone is not an identity.

The catalogue: how it is discovered, with the route as the primary surface
and the CLI list as the fallback, why the route is preferred, what
`catalog_version` is a digest of, and the five availability states with the
rule that only `available` is selectable.

The fallback rule: a role owns an ordered chain of candidates, explicit
identities and catalogue patterns, and the pipeline takes the first available
one. When nothing is available the failure is closed and named
(`NoModelAvailable`), never a crash. A role that is not in the thirteen is
refused (`UnknownRole`).

The persistence: the four tables, the rule that `model_inventory` rows are
retired and never deleted, and the one `model_executions` row per call with
its provider, model, role, source, fallback, catalogue version, prompt and
config version, latency, and outcome.

The honest limits, and they are the most useful part of the document:

- The generation path does not use the router. `OpenCodeGenerateProvider`
  still calls the model pinned in `config/extraction.yaml`.
- A catalogue is a snapshot, not a guarantee. A model that is available can
  still fail at call time, and the catalogue is refreshed on an interval, on
  a miss, and on demand.
- The role configuration names patterns, not model ids, so a new matching
  model is picked up with no edit. That is the point of the design, and it is
  also why the selected model can change between two runs.

### OPERATIONS.md

The command surface, each command with what it does and what it writes. This
is the document an operator reaches for first, so it leads with the commands,
including `ph models` with its `--refresh` and `--role` flags.

Then the database lifecycle: `ph db init` creates and migrates, `ph db status`
reports the schema version and row counts, `ph db check` runs
`PRAGMA integrity_check` and `PRAGMA foreign_key_check`. What the migration
runner does, and the three things it does not do: no history table, no
checksum, no drift detection. The current schema version is 2.

Then the normal loop: fetch, extract, review-sheet, eval, report,
`ph db import-run`. Where each step writes its output. `import_run()` refuses
a run whose manifest counts disagree with the artifacts, which is worth
telling an operator because it turns a silent partial import into a loud
failure.

Then limits: the per-source rate limits and request budget from
`config/sources.yaml`, the extraction limits from `config/extraction.yaml`,
the research contract budgets from `config/research_contract.yaml`, and the
fact that there is no global spend cap and no `spend_ledger` writer.

Then observability: what is recorded where, and where to look. The fetch log,
the rejection log, the run directory, the database tables, the worker events.

Then the subsystems an operator can reach but that nothing drives yet: the
scheduler (`default_schedules()`, nothing runs it), the gate engine (no CLI),
the benchmark package (no CLI, and `ph eval` is a different thing, the
Phase 1 review sheet), and the runtime package (an entry point, no handler
registry wired).

Close with a troubleshooting table keyed on the error text an operator
actually sees, mapping each to its cause and its fix. Use the real reason
codes.

### RECOVERY.md

Failure modes and their signatures, in the order an operator meets them.

Expired lease: the job is `leased`, the lease expired, the worker died. The
recovery path is `ph db recover`, or the next `claim_job()`, which recovers
expired leases on every call.

`LeaseLost`: the lease was taken over. The write is refused. The stale worker
stops. Nothing is corrupted; the job was already requeued by the new owner.

A worker killed mid-handler: the authoritative write is either committed or
not, because the write happens inside a transaction that asserts the lease.
There is no half-written job.

A process killed after the SQLite commit but before the projection: the
projection task stays `pending` and a later `process_outbox()` picks it up.
The outbox is what makes this safe.

A process killed during a projection: the task stays `processing` until its
lease expires, then it returns to `pending`, or to `failed` when the attempt
ceiling is reached.

The worker's own recovery: `WorkerRuntime.recover()` calls
`recover_expired_leases()` and reports the count as `lease_recovered`, and
the runtime takes a single-instance file lock so a second supervisor is
denied rather than allowed to steal work. A stopped worker finishes the job
in flight and does not claim the next one.

The parts that do not exist yet, stated plainly: no resume orchestration
calls `load_checkpoint()`, so a checkpoint is a record and not yet a resume
mechanism. No run state machine, so `ph db recover` requeues jobs and
projection tasks and nothing else. No backup tooling, so the restore
procedure today is a file copy of the database, with the online-backup
canary from spec §38B marked `PLANNED`.

Add the manual recovery steps, with the exact commands, and what to check
afterwards with `ph db status` and `ph db check`.

### SECURITY.md

The trust boundary first. Untrusted content is data. It never becomes an
instruction, a tool argument, or a shell command.

Source safety: the registry fields, the fail-closed admission codes, the fact
that `blog_http` is disabled and Reddit is absent while its terms are
unknown, and the rule that URL admission is a worker decision and not a model
decision.

SSRF and URL safety: the admission rules, the public-IP requirement, the
redirect revalidation, the HTTPS-to-HTTP downgrade refusal, the port
restriction, the IP-literal-host refusal, and the robots behaviour. Name
`admit_url()` and `admit_redirect()`.

Credentials: the repository holds no provider key, `.env` is gitignored,
`.env.example` holds names only, the OpenCode auth store carries the auth,
the CLI is invoked as an argv list with `shell=False`, and the `opencode`
executable is resolved rather than assumed. The rule from the adapter: never
add a provider secret to this repository. The empty `SUBQ_API_KEY=` in
`.env.example` is a name for a later phase, and nothing reads it today.

The forbidden rules from `.loop/adapter.json`, restated in plain language.

The human approval boundary: the automatic list and the approval list from
spec §37, what the code implements today, and the honest statement that the
code implements none of the approval list, because the code has no
external-account, posting, or Git write path. The one human input the code
requires is the filled evaluation sheet, and that is offline labelling, not
an approval gate.

## Definition of done for the node

- Every document you wrote has the four-label legend near the top.
- Every behavioural claim names a file, table, command, or function.
- No document claims a feature without code, and no document calls a shipped
  feature planned.
- The six documents do not contradict each other. They share the handoff's
  facts.
- The prose passes the writing rules above.
- You changed nothing except the files you were asked to write. Do not touch
  `README.md`, the sibling wave specifications, or any source file.

# Final wave 1 integration report

**Run ID:** wave-1-integration-2026-10-21
**Branch:** `main`
**Integration commit:** `984c278a9fded749c079bba6c203eda250d51d3c`
**Tree:** `9789803c97151ebda8ef9a3afad6c68f06b6c410`
**Base:** `3d7bc68`
**Risk:** declared `high` (schema, security boundary, research contract)
**Adapter:** Product Hunter, 8 forbidden rules in force
**Date:** 2026-10-21

```text
GOAL: reconcile the Wave 0 and Wave 1 sessions into one coherent, tested,
      production-ready state on main
TIER: free (models used per node in order)
  stage 0-3   orchestrator: longcat-2.5-preview-free
  stage 4     loop-free-implement-1: longcat-2.5-preview-free
              loop-free-implement-2: step-5-preview-free (failed, expired key)
  stage 5     loop-free-verify-code-1: step-5-preview-free
  stage 6     loop-free-harden: step-5-preview-free
  stage 7     loop-free-review-1: step-5-preview-free (two rounds)
  stage 8-10  orchestrator: longcat-2.5-preview-free
RISK: declared=high floor=medium (schema, security boundary, research contract)
ADAPTER: Product Hunter [repo] 8 forbidden rules in force
STAGES: 11/11 complete, skipped: none. Stage 5b visual was not required; the
  adapter sets visualRequired to never and no UI file changed.
GATES: uv run pytest -> 1085 passed in 14.15s, exit 0
CHANGES: 33 files, 6167 insertions, 234 deletions, 1 commit
HARDEN: clean. One nice-to-have (a stale docstring), closed.
REVIEW: round 1, fix 2 first. 2 Must-fix, 6 Should-fix, 6 Nice-to-have.
  round 2, fix 1 first. 8 of 14 closed, 4 residuals, 1 new Must-fix.
  All 19 findings closed across two fix rounds. Lean: -8 lines, taken.
WAIVERS: none
STOPPED BECAUSE: all pass and review ship
NEXT: ready to commit (done) and the wave 2 seam is in place
```

## 1. What each session actually delivered

The two waves did not follow the original branch and worktree plan. The
repository was a single branch (`main`) with every wave committed on it, and
the working tree was shared.

| Session | Delivered | State found at integration |
|---|---|---|
| Phase 1, steps 1-4 | The source registry, the fetch safety layer, the strict pain extraction with quote-verbatim validation, the deterministic normalization, and the manual evaluation harness | Complete and tested. Kept as is. |
| Wave 0 tooling | The MCP audit, two MCP servers added, five skills, `docs/TOOLING.md` | Complete. No stale claim found. Kept as is. |
| Phase 2, evidence core | The SQLite schema, the store, the job queue with fenced leases, the checkpoints, the transactional outbox | Complete and tested. Kept as is. |
| Phase 2, crash recovery | The lease expiry recovery, the epoch fencing proof, the process-kill recovery tests | Complete. Kept as is. |
| Phase 2, model routing | The catalogue discovery, the role router, the chain fallback, the routing persistence, the routing CLI | Complete and configurable. Kept as is. |
| Phase 2, scheduler | `Scheduler.tick()`, the five default schedules, the source gate, the occurrence idempotency | Complete, and nothing runs it. Kept as is. |
| Phase 2, worker runtime | The supervised worker, the single-instance lock, the event stream, the heartbeat | Complete. Kept as is. |
| Phase 2, OpenCode interface | The plugin, the ten commands, the registry actions, the project-root fix | Complete. Kept as is. |
| Phase 2, evaluation infrastructure | The ten benchmark task families, the runner, the comparator, the report | Complete. Kept as is. |
| Phase 4, research contract | The contract schema, the budget, the predicates, the gate evaluation, the violation codes, the shipped contract YAML | Complete as a library, with **no persistence and no caller**. The persistence and the enforcement were added by this run. |

Two documentation claims were found to be false against the code and were
corrected: the README said the model router "does not drive extraction yet",
and the architecture document recorded a live hazard that `.gitignore` hides
`src/product_hunter/runtime/`. The pattern is `/runtime/`, anchored to the
repository root, and all six files of the package are tracked. The hazard was
an artefact of reading the pattern without the slash.

## 2. What was reconciled

### The gate had a library and nothing else

`src/product_hunter/research/` was a complete, well-tested implementation of
the Research Contract and the Tool Gate, and nothing outside the package
imported it. The package's own docstring said the migration was "a plan in a
docstring and the file does not exist".

Now:

- `db/migrations/0003_research_gate.sql` holds `tool_calls`,
  `research_stage_executions`, `research_stage_artifacts`, `research_claims`,
  and `stage_gate_results`.
- `src/product_hunter/db/research_gate.py` writes and reads them, rebuilds a
  run's evidence from the stored rows, measures the budget from the same
  rows, evaluates the stage, run, and promotion gates, and exposes
  `require_promotable()`, which raises `GateBlocked` when a run may not be
  promoted.

The module lives in `db/` rather than `research/` because `research/` is
deliberately a pure-data package that never touches a database, and `db/` is
where every other SQLite access lives. The dependency runs one way: `db`
imports `research`, never the reverse.

### The checkpoint was unfenced

Every job write was fenced by the lease epoch. `save_checkpoint()` was not, so
a worker that had lost its lease could still write a checkpoint over a newer
worker's state. It now takes an optional `worker_id` and `lease_epoch`, is
refused without a live lease on a job for that run and stage, and shares one
transaction with the write. The lease is judged against the store's clock, so
a caller cannot make an expired lease look live by passing an old timestamp.

### The query families were the model's to invent

The family name on a tool call was a free-form string, and the only check
counted distinct strings. A stage that ran four searches with four invented
names passed, and nothing required the negative or alternative-to families
that spec §7A calls mandatory.

`src/product_hunter/research/query_plan.py` now owns the ten families, the
eight controlled wording variants, the mandatory query templates, and the
per-stage mapping. Two new gate predicates enforce it, and the shipped
contract asks for both on every stage that searches.

### One implementation per responsibility

No session had produced a competing implementation of the same thing, so
nothing had to be deleted for duplication. The reconciliation was of the
*gaps* between the sessions rather than of conflicts between them. The one
place a second implementation did appear — the private `_now` and `_rollback`
helpers in `jobs.py`, `outbox.py`, `migrations.py` and `checkpoints.py` — was
collapsed into `db/connection.py`.

## 3. Conflicts encountered

### The shared working tree

A Wave 2 session checked out `wave2/clustering` in the main directory and
began writing `db/migrations/0004_clustering.sql`,
`src/product_hunter/clustering/`, `src/product_hunter/db/clustering.py`, and
`config/clustering.yaml` into it. The instruction was to stay on `main`.

`main` was free at `3d7bc68`, so this run created a dedicated worktree at
`product-hunter-main` on the `main` branch and moved the session into it.
Only this run's own files were copied across. The other session's files were
left exactly where they were, and the `main` branch pointer did not move
until the integration commit.

The consequence for the Wave 2 sessions: their branches still fork from
`3d7bc68` and will need a merge against `984c278`.

### Migration numbering

The gate's migration was planned as `0003` and the Wave 2 clustering session
took `0004` in the shared tree. In the integration worktree the numbering is
`0001`, `0002`, `0003` (the gate), and `0004` remains free for the clustering
migration. No conflict, but the two branches will need their migration files
reconciled at merge time.

### The review against the code

Two review rounds returned findings that the test suite could not see. Both
rounds are the reason this commit is coherent.

## 4. Important architectural decisions

**SQLite is the only authority.** The gate's evidence, its verdicts, and its
budget measurement all live in the database. There is no file-based store and
no second source of truth. A stage handler that wrote its evidence to a file
instead of the database would leave the gate judging nothing.

**The gate's data layer sits in `db/`, not `research/`.** The `research/`
package is a pure-data package: it never calls a tool, never reads a file,
and never looks at the clock. Keeping the SQLite authority out of it preserves
that rule and keeps the dependency one-way.

**The lease fence lives in one place.** `src/product_hunter/db/leases.py` owns
`assert_live_lease`, `fenced_write`, and `assert_fence_pair`. The checkpoint
writes and all five gate write paths use it. The fence is all-or-nothing: a
write with only one of `worker_id` and `lease_epoch` is a programming error
and is refused. A write with neither is the control plane.

**The lease is judged against the store's clock.** The check takes no
timestamp parameter. A caller that could name the instant would decide for
itself whether its lease was still live, and a stale worker would keep its
lease alive by asking nicely. The `now_iso` parameter existed during
development and was removed.

**The verdict is bound to the contract that judged it.** Every stored verdict
carries the fingerprint of its contract, and `evaluate_promotion()` refuses a
result judged under a different one. A mixed set is refused on read and on
write. Re-judging a run under a changed contract replaces the whole verdict
set atomically under the new fingerprint, so a contract change cannot brick a
run.

**The run-level verdict is stored.** `evaluate_run()` produces run-level
violations with no stage, and the promotion check reads the run-level floor
from exactly those violations. They are stored in a `stage = NULL` row and
merged back on read, so a verdict read from the database equals the verdict
that was computed.

**The query families are worker-owned.** A family name that arrives on a tool
call is data. The gate refuses a search that names a family the worker does
not own, and a search that names no family at all, and the reason reports a
count rather than quoting the string.

**The evidence round trip must be lossless.** A verdict computed in memory and
the same verdict read back from the database must be equal. This is what
caught two of the fail-opens.

**A missing `tool_call_id` stays missing.** The store mints a deterministic
row key, but it does not repair the missing provenance, so the gate still
reports `provenance_missing`. A caller that wants the check to pass publishes
the id.

## 5. Tests run and results

| Run | Result |
|---|---|
| `uv run pytest` before the run | 980 passed |
| `uv run pytest` after gap A | 1040 passed |
| `uv run pytest` after gap B | 1066 passed |
| `uv run pytest` after the round-1 fixes | 1066 passed |
| `uv run pytest` after the round-2 fixes | 1085 passed |
| `uv run pytest` from the committed state | **1085 passed in 14.15s, exit 0** |

Per-file counts for the new and changed files:

| File | Tests |
|---|---|
| `tests/test_db_research_gate.py` | 62 |
| `tests/test_research_query_plan.py` | 26 |
| `tests/test_db_checkpoints.py` | 20 |
| `tests/test_research_provenance.py` | 17 |

Every test file in the repository:

```
test_cli_bench 24        test_cli_extract 39      test_db_checkpoints 20
test_db_crash_recovery 21 test_db_jobs 8          test_db_migrations 14
test_db_outbox 6         test_db_research_gate 62 test_db_scheduler 64
test_db_store 9          test_db_worker 5         test_documents 23
test_eval_cases 29       test_eval_run 94         test_eval_tasks 43
test_evaluation 46       test_extraction 67       test_fetch 34
test_llm_contract 15     test_model_catalog 19    test_model_discovery 9
test_model_routing 47    test_normalization 28    test_plugin_project_root 2
test_quote 16            test_registry 53         test_reporting 12
test_research_budget 13  test_research_contract 38 test_research_gate 76
test_research_provenance 17 test_research_query_plan 26 test_runtime_lock 6
test_runtime_worker 42   test_safety 30           test_smoke 8
test_sources 20
```

### The two test mechanisms that were corrected

Two existing tests simulated a lease expiry by passing a future `now` to
`save_checkpoint()`. Once the lease moved to the store's clock, that
simulation expired nothing, so the tests were exercising the wrong seam. Both
now expire the lease **in the row** and save with a real timestamp. The
assertions are unchanged, and the change makes the tests stronger: they now
prove the real clock decides. The second node verified its own fix by
mutation — it re-introduced the caller-supplied timestamp, watched both lease
tests fail, and reverted it.

A third test, `test_a_call_without_an_id_is_given_a_deterministic_one`, asserted
the old behaviour, that the store repaired a missing id. It was renamed
`test_a_call_without_an_id_keeps_its_missing_provenance` and now asserts the
correct behaviour.

No assertion was weakened, skipped, or deleted anywhere in this commit.

### What is not exercised

The `network` marker is declared in `pyproject.toml` and **no test carries
it**, so nothing is actually skipped by that mechanism today. The description
overstates what happens.

Ruff is not a project dependency and there is no `[tool.ruff]` section, so
lint is **not configured as a gate**. The global ruff 0.16.4 reports 103
findings on the tree; they are style and static-analysis findings, not gate
failures, and no mypy or pyright configuration exists. This was reported by
the verify node and is not hidden here.

## 6. Security checks

The hardening pass returned clean. The checks that were run:

| Area | Result |
|---|---|
| Subprocess invocation | No `shell=True`, no command string, no `os.system`, no `eval`/`exec`, no `pickle` in any new or changed file. `subprocess.run(argv, shell=False)` is unchanged in `llm/opencode.py`. |
| The OpenCode server | Reached only through the `opencode api` CLI with an argument vector. No raw HTTP to the OpenCode server. |
| SSRF and URL admission | Unchanged and intact: scheme allowlist, no userinfo, no IP literal, host allowlist, ports 80 and 443 only, public-IP resolution that fails closed on any private address, per-hop redirect re-validation, no HTTPS-to-HTTP downgrade. |
| Prompt injection | Untrusted text is delimited in the extraction prompt and labelled as data. It never reaches a tool name, a JSON schema, a subprocess argument, or a URL. |
| The untrusted-text rule | The `GateBlocked` message and every violation detail carry ids, names, counts, and fixed phrases only. No scraped string, no artifact `invalid_reason`, no tool-call `error`. A test asserts the offending strings are absent. The two new predicates follow the same rule: the unknown-family reason reports a count and never quotes the family. |
| SQL injection | Every statement in the new code uses parameter binding. No f-string SQL, no `%` or `.format()` SQL. Table and column names are literals. |
| Filesystem | The new code writes no file. Every id is derived from a hash or an internal identifier, never from a scraped title, a URL, or a query string. |
| Destructive operations | No `DELETE`, `DROP`, or `TRUNCATE` in the gate's write path, except the one deliberate `DELETE` inside the atomic verdict-set replacement. No `rmtree`, no `git push`, no external post. |
| Credentials | No secret is tracked. `.env.example` holds names only, and `SUBQ_API_KEY` is empty. The only environment reads in `src/` are `APPDATA`, `PH_WORKER_HANDLERS`, `PH_WORKER_LOCK`, `PH_DB_PATH`, and the eight non-secret configuration overrides. No `DEEPSEEK_API_KEY` exists anywhere except as a prohibition in the documentation. |
| Observability | The house rule holds: no `logging` import in the library packages, no bare `except: pass`. |
| The lease fence | A stale epoch, the wrong worker, an expired lease, a completed job, a missing job, and a half-fence are each refused on all six write paths, with fixed messages that carry no job id, worker id, run id, or stage. |
| The clock | The lease check takes no timestamp parameter, so no caller can make an expired lease look live. |

### The one standing security residual

`fetch.py:191` carries a labelled `shortcut:` marker: `admit_url()` resolves
the host before httpx connects, and a DNS rebind between the two is not
caught. The marker is honest and states the fix and the phase. It is the only
known security gap in the repository, and it is a TOCTOU window rather than a
missing control.

Two smaller notes: the stored `raw_path` is trusted on read, so a tampered
database row could traverse — the write path is content-addressed and
validated, and the normal flow is safe. And the worker's error redaction
masks labelled secrets only.

## 7. Recovery checks

`tests/test_db_crash_recovery.py` (21 tests) is the proof, and it kills a real
child process rather than simulating it:

- a committed transaction survives a process kill
- an open transaction leaves nothing behind
- a job whose worker vanished is recoverable
- a worker whose lease expired cannot write
- a superseded epoch cannot complete, fail, renew, or write
- two workers racing for one job produce one claim
- a rollback inside a fenced transaction discards every write
- a checkpoint survives a restart
- a corrupt checkpoint does not stop recovery
- an outbox task lands with the evidence or with neither
- a replayed import creates no duplicate rows
- a truncated run directory is refused
- the store rolls the projection task back with the evidence
- a handler that raises after its own commit is retried without duplicating
- a projection task abandoned with an expired lease converges to one row
- a crash child that hangs fails the test instead of hanging it

Live checks run from the committed state:

```
PASS  a job is claimable
PASS  the claim carries a lease epoch
PASS  a second worker cannot claim a leased job
PASS  an expired lease is recovered  -> recovered=1
PASS  the recovered job is claimable again
PASS  the re-claim moves the lease epoch forward  -> epoch 1 -> 2
PASS  a run with no stage evidence is refused
PASS  the refusal message names only the run and the codes
PASS  the schema version is 3 after a reopen  -> version=3
PASS  the integrity check is ok  -> ok
```

The worker process was started and stopped: it acquires the instance lock,
emits `worker_started`, reports `no_handlers` because no stage handler
exists, and recovers expired leases.

## 8. Model-routing status

**Genuinely configurable. No model is named in the business logic.**

- `config/model_routing.yaml` holds a chain of *patterns* per role, and the
  catalogue is discovered from OpenCode at runtime. A grep of `src/` for any
  model name returns nothing.
- All thirteen roles of spec §3 are present as `ModelRole` members and as
  configured chains: `extraction`, `classification`, `normalization`,
  `deduplication`, `entity_resolution`, `clustering`, `validation`,
  `investigation`, `competitor_research`, `counter_evidence`, `scoring`,
  `reporting`, `synthesis`.
- The router now drives extraction. `ph extract` resolves the `extraction`
  role, binds the selected model to the provider, and records the selection,
  the catalogue version, and the fallback index in the run manifest. A router
  that cannot answer falls back to the model pinned in `config/extraction.yaml`
  and the manifest says which one ran. The README previously denied this.
- The fallbacks are configurable: the chain falls through at call time, and
  the fallback index is recorded.
- The provenance records the provider, the model, the selection source, the
  fallback index, the catalogue version, and the prompt version, in the run
  manifest and in the `model_executions` table.
- `DEEPSEEK_API_KEY` was not added. The repository holds no provider secret
  and reads the OpenCode auth store. The coding model that wrote these
  sessions is not the product's runtime model.
- The role chains are configured against the `opencode-go` provider, and the
  extraction chain leads with the reasoning family and falls through to a
  flash model and then to any free model. An operator who wants a different
  family moves the pattern; that is a one-line change.

**Not done:** only the `extraction` role is bound to a live call path. The
other twelve roles are resolvable and tested but no stage consumes them yet,
because the stages that would consume them are Wave 2 work.

## 9. Research Contract status

**Enforceable by code, and now enforced in a library. It still gates nothing
in the running system.**

What the code enforces:

| Requirement | Where | Enforced |
|---|---|---|
| Required stages | `research/contract.py`, `config/research_contract.yaml` | Yes. A contract naming a stage it does not define is refused. |
| Required capabilities and tools | `research/gate.py` | Yes. A stage whose tool never ran fails with `tool_missing`. |
| Required artifacts | `research/gate.py` | Yes. `artifact_missing`, and an invalid artifact fails. |
| Evidence thresholds | `research/gate.py` | Yes. A threshold on an artifact kind the stage does not declare is a contract error. |
| Deterministic quality predicates | `research/predicates.py` | Yes. Eight predicates, two of them new. |
| Budgets | `research/budget.py`, `db/research_gate.py` | Yes, and measured from the stored rows. |
| Stopping criteria | `research/contract.py` | Carried. Read by no code. |
| Retry policy | `research/contract.py` | Carried. Read by no code. |
| Promotion requirements | `research/gate.py` | Yes. `require_promotable()` raises `GateBlocked`. |
| Provenance | `research/gate.py` | Yes, and it survives the round trip. |
| Stage attempts | `db/migrations/0003_research_gate.sql` | Yes. A stage is judged on its latest attempt only. |
| Tool calls | `db/research_gate.py` | Yes. Stored, read back, and counted. |
| Counter-evidence | `research/gate.py`, the shipped contract | The gate requires it; nothing collects it. |
| Competitor research | The `competitor_search` gate key | The gate requires it; nothing searches for it. |
| Source independence | `research/predicates.py` | The gate can require independent authors and communities; the analysis is not built. |
| Concentration checks | — | Not built. |
| Non-trivial zero-result handling | `research/gate.py` | Yes. A recorded zero requires every required family to have been searched, and a failed or unsearched query never counts as zero. |
| Worker-assigned query families | `research/query_plan.py` | Yes. Ten families, eight controlled wordings, mandatory templates, two enforcing predicates. |

**The one gap:** no worker handler records a tool call or an artifact, no CLI
command evaluates a run, and there is no promotion path to refuse. The gate is
a complete, tested, fail-closed library with no caller. That is the seam the
Wave 2 sessions code against, and the handoff
(`docs/reasonix/handoffs/wave-1-integration-handoff.md`) documents the exact
call sequence.

## 10. Known limitations

1. **The gate has no caller.** The single most important limitation.
2. **Seven of the twelve shipped budget limits can never be exceeded.**
   `llm_input_tokens`, `llm_output_tokens`, `embedding_items`,
   `browser_minutes`, `crawl_items`, `subq_calls`, and `usd_cost` are measured
   only from `spend_ledger`, and `spend_ledger` has no writer. A run that
   spends tokens without recording them under-counts. The gate now *reports*
   the unmeasured dimensions per run instead of pretending they are zero, and
   the report is on the promotion verdict rather than a violation, so it does
   not change a pass.
3. **The fence is opt-in.** A write with neither `worker_id` nor
   `lease_epoch` is allowed, because the control plane has no lease. The code
   cannot tell a control-plane write from a worker that forgot the fence. The
   rule is documented and the handoff states that a worker must pass it.
4. **The DNS rebind window** between URL admission and connect, carried as a
   labelled `shortcut:` marker.
5. **The stored `raw_path` is trusted on read.** A tampered database row could
   traverse; the write path is content-addressed and validated.
6. **No migration rollback.** Spec §38B asks for rollback notes and a restore
   canary on every migration. There are none, and the migrations are
   forward-only.
7. **The `network` pytest marker is inert.** No test carries it.
8. **Lint and type checks are not configured.** Ruff and mypy are not project
   dependencies and have no configuration.
9. **Three of the six hard-requirement flags are enforced through the stage
   gate only.** The shipped contract sets `promotion.require_all_stages_passed:
   true`, so the shipped baseline is not exposed to it.
10. **`saturation_threshold` has no measurement source.** A contract that
    limits it makes the budget builder refuse. The shipped contract does not
    set it.
11. **The checkpoint container does not interpret its contents.** It stores
    pending items, source cursors, budget state, and a lease epoch as opaque
    state, and nothing resumes from it.

## 11. Deferred functionality

None of these is implemented, and none was pulled into the core:

| Integration | State | Reason |
|---|---|---|
| SubQ Snap | Absent | Spec §38A Phase 7, behind a benchmark gate. The pipeline never reads the key. |
| Apify | Absent from the product | Spec §38A Phase 7. The MCP server is configured in `opencode.json`; the worker does not use it. |
| Crawl4AI | Absent | Spec §8 and §38A Phase 7. |
| Large-scale Playwright collection | Absent | Spec §8 and §37A, which requires browser isolation first. |
| Langfuse | Absent | Spec §34, only after a Phase 7 benchmark. |
| Redis, Temporal, BullMQ | Absent | Spec §38 excludes them from the initial core. |
| Obsidian, either direction | Absent | Spec §38A Phase 6 for the projection, Phase 7 for the two-way interaction. No projection exists. |
| LangChain, LangGraph, LangSmith, LiteLLM, OpenTelemetry, Prometheus | Absent | Spec §38, deliberately excluded. |

The MCP audit and the reason each of the other fifteen available servers is
deferred or unusable is in `docs/TOOLING.md`.

## 12. Remaining blockers

Nothing blocks the commit. The following block the **next** phase:

1. **A stage handler that records tool calls and artifacts.** The gate, the
   persistence, the fence, the budget, and the query plan are in place. The
   handler is what makes them bite. This is the Wave 2 work.
2. **A `spend_ledger` writer.** Until it exists, seven of the twelve shipped
   budget limits are inert.
3. **A promotion path.** The gate can refuse; nothing is refused yet.
4. **The operational work of spec §38B** — backup, restore canary, retention
   tiers, disk and WAL watchdogs, parser-drift canaries, and migration
   rollback notes. This is the largest single gap in operational readiness,
   and it is the reason the system is **not** production-ready for unattended
   long-term operation.
5. **A migration to reconcile** with the Wave 2 branches, which fork from
   `3d7bc68` and will need a merge against `984c278`, including the
   `0004_clustering.sql` numbering.

## 13. Honest assessment

**The system is not production-ready, and the specification says so itself.**

Spec §44 lists thirty-two items. Eleven are met, fifteen are partly met, and
six are not met. Spec §38A defines eight phases: Phase 1 is met, Phase 2 is
met, Phase 4 is partly met, Phases 3, 5, 6, and 8 are not met, and Phase 7 is
deferred by design. Item 31 of §44 says the implementation phases must pass
their acceptance criteria before the system is considered production-ready,
and item 32 says the operational work must be verified before unattended
long-term operation. Neither condition is met.

What the repository has is a complete, tested, and fenced **evidence core**,
and a complete, tested, and fail-closed **research gate** that nothing calls
yet. The gap is not a scattering of missing features. The stages of spec §10
beyond extraction have no implementation, so there is no pipeline for the
gate to judge.

That is the honest state, and the Wave 2 sessions are exactly the work that
closes it. This run built the seam they need and made sure it fails closed.

## 14. The final commit

```
commit 984c278a9fded749c079bba6c203eda250d51d3c
tree   9789803c97151ebda8ef9a3afad6c68f06b6c410
parent 3d7bc68acd5e2265b349456313d3eb8c35976ff9
author Anubhav Rai <anubhavrai100@gmail.com>
date   Wed Oct 21 21:06:30 2026 +0530
subject phase2: integrate wave 1 implementation

 33 files changed, 6167 insertions(+), 234 deletions(-)
```

The working tree is clean at that commit. The full suite passes from the
committed state: **1085 passed, exit 0**. Nothing was pushed.

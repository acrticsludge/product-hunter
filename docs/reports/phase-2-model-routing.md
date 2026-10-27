# Run report — phase 2, dynamic model routing

**Date:** 2026-10-21
**Base commit:** `205a4ba`
**Spec:** `docs/reasonix/specs/model-routing.md`
**Plan:** `docs/reasonix/plans/model-routing-implementation-plan.md`
**Implementation doc:** `docs/reasonix/MODEL_ROUTING_IMPLEMENTATION.md`
**Handoff:** `docs/reasonix/handoffs/model-routing-handoff.md`
**Decisions:** `docs/reasonix/decisions/2026-10-21-dynamic-model-routing.md`

---

```
GOAL: make the model a runtime decision — discover what OpenCode offers, bind
      each pipeline role to a chain of candidates, and record which model ran
      which piece of work.

TIER: free. Nodes, in order: loop-free-implement-1, loop-free-verify-code-1,
      loop-free-harden, loop-free-implement-1 (harden fixes), loop-free-review-1,
      loop-free-implement-1 (review fixes). Models follow the free model table:
      implement-1 on longcat-2.5-preview-free, verify-code-1 on
      longcat-2.5-preview-free, harden on step-5-preview-free, review-1 on
      step-5-preview-free.

RISK: declared=high floor=high (schema migration; db/migrations/** is a
      high-risk path in .loop/adapter.json, so stages 2, 6 and 7 were
      non-skippable and all ran)

ADAPTER: Product Hunter [repo] — .loop/adapter.json, 8 forbidden rules in force,
      26 files declared at stage 0

STAGES: 11/11 complete. None skipped.

GATES:
  stage 5, full:      627 passed, 1 skipped in 5.02s   [loopgate] PASS exit 0
  stage 5, contract:  15 passed in 0.50s              [loopgate] PASS exit 0
  stage 5, routing:   58 passed in 0.18s              [loopgate] PASS exit 0
  after harden fixes: 695 passed in 8.11s
  after review fixes: 746 passed in 10.89s
  final, before commit: 746 passed in 9.33s

CHANGES: 26 files, 4359 insertions, 31 deletions
  src/product_hunter/llm/catalog.py                    298   new
  src/product_hunter/llm/discovery.py                  210   new
  src/product_hunter/llm/routing.py                    320   new
  src/product_hunter/db/model_routing.py               322   new
  db/migrations/0002_model_routing.sql                  63   new
  config/model_routing.yaml                             40   new
  src/product_hunter/llm/opencode.py                    65   shared argv runner
  src/product_hunter/llm/__init__.py                    40   build_router
  src/product_hunter/config.py                          52   RoutingConfig
  src/product_hunter/cli.py                            117   ph models
  src/product_hunter/db/__init__.py                     20   exports
  tests/test_model_catalog.py                          232   new
  tests/test_model_discovery.py                        238   new
  tests/test_model_routing.py                          770   new
  tests/test_db_migrations.py                           14   version 1 -> 2
  README.md                                              3   ph models bullet
  docs/reasonix/...                                  1280   spec, plan, 4 prompts,
                                                             handoff, decision note,
                                                             implementation doc

HARDEN: FIX FIRST. Two Must fix, three Should fix.
  Must 1 — json.loads raises an uncaught RecursionError on a deeply nested
           payload, which crashed discovery instead of refusing it.
           Fixed: RecursionError now raises MalformedCatalogError.
  Must 2 — datetime.fromtimestamp raises an uncaught OverflowError/OSError on
           an extreme released timestamp. Fixed: an out-of-range timestamp
           leaves released_at as None.
  Should 3 — RoutingError derived from Exception, not ProviderError, so a
           NoModelAvailable would escape the existing fail-closed handlers.
           Fixed: RoutingError now derives from ProviderError.
  Should 4 — a failed discovery left no observable trace, so "no model exists"
           and "discovery is broken" looked the same. Fixed: the refusal now
           names the discovery failure, and the failure is cleared on recovery.
  Should 5 — execution.error was persisted unbounded. Fixed: truncated to
           2000 characters at record time.
  No finding on: subprocess safety, SQL parameterisation, the inventory
  transaction, the additive migration, provenance integrity, or fail-closed
  behaviour on an empty catalogue, a missing CLI, and malformed JSON.

REVIEW: ship with should-fixes. No Must fix.
  Should 1 — a failed discovery was remembered forever, so a later refusal
           named a failure that had not happened. Fixed: cleared on success.
  Should 2 — one select() ran the whole discovery twice when discovery was
           broken, up to 120 s per selection and 26 minutes per run. Fixed:
           a failure is not a miss, so the router does not re-discover after
           one; the refresher remembers a failure until the interval elapses.
  Should 3 — the pattern ordering was not deterministic across providers, so
           the plan's determinism claim was false. Fixed: provider id added
           as the last sort key.
  Should 4 — test_every_role_resolves could not fail: it asserted the
           provider id, which every fixture entry already had. Fixed: it now
           asserts the model id and the selection source, and the reviewer
           proved the old version passed with every shipped pattern broken.
  Should 5 — the no-instant-events limitation was not written down, which the
           task asked for explicitly. Fixed: stated in the implementation doc.
  Nice   — RoleRoute validated nothing and had one use; removed.
          released_at was parsed and never read; now printed by ph models.
          ph models was missing from the README; added.
  Lean: -19 lines possible. RoleRoute (6) and the unused released_at path
  were taken; the rest was kept.

WAIVERS: none.

STOPPED BECAUSE: all pass and review ship.

NEXT: ready to commit — phase2: add dynamic model routing, not pushed.
```

---

## Notes for the next run

**The working tree was shared.** Other agents were working in the same checkout
at the same time. Their files appear in the same directories, and one of them
renamed the documentation tree to `docs/reasonix` and moved the reports to
`docs/reasonix/reports` mid-run. The commit therefore stages explicit paths only, and
three shared files — `src/product_hunter/cli.py`, `src/product_hunter/db/__init__.py`
and `README.md` — were staged as a rebuilt "HEAD plus the model-routing change",
so another agent's benchmark and research commands are not in this commit. Their
work stays in the working tree, unstaged.

**One test changed, and it was not a weakened test.** `db/migrations/0002_model_routing.sql`
is a new migration, so the schema version moved from 1 to 2. `tests/test_db_migrations.py`
pinned that version in two assertions; both now say 2, and `EXPECTED_TABLES` gained
the four new table names, which makes the table check stronger rather than looser.
No assertion was removed, skipped, or weakened. The reviewer checked this
specifically and agreed.

**SubQ was consulted once, at stage 3.** The fork was where the routing state
lives: a new migration with typed tables, or JSON payloads in the existing
`research_events` table. The answer was `noul` 0.976 for the new migration.
One call, 295 input tokens, 0 errors, model `subq-snap-preview`. No key was
printed, and nothing about SubQ reached the repository.

**What this change does not do.** SubQ is not integrated and no benchmarking is
implemented; both were named non-goals. The catalogue is polled, because
OpenCode publishes no model-change event, so a model that disappears stays
selectable until the next refresh; the implementation document states this in
full.

---

## Follow-up — the router drives the extraction path

A second commit, `phase2: route extraction through the model router`, closes
the gap this report named.

- `extract_command` resolves the `extraction` role through the router and
  builds a generator bound to the model it selected. A router that cannot
  answer — a missing or broken routing file, a failed discovery, no available
  candidate — never stops the run: the model pinned in
  `config/extraction.yaml` runs it, and the manifest and a rejection record
  say why.
- The run gains a `model_routing` manifest block and a
  `model_executions.jsonl` artifact, one row per model call, carrying
  provider, model id, role, selection source, fallback index, catalogue
  version, prompt and config version, latency, and outcome. `ph db import-run`
  writes those rows and the role binding into SQLite.
- The shipped `extraction` chain leads with `opencode-go/deepseek*`, so
  extraction keeps running on the model whose output Phase 1 measured. It is a
  pattern, not a model id: a new model on that family is picked up with no
  edit, and the chain falls through to any flash model and then any free model
  if the family disappears. The chain takes the *cheapest* match, so a price
  change could move it; the config comment and the implementation document
  say so.
- `config/model_routing.yaml` must now bind all thirteen roles, a malformed
  YAML file is a `ValueError` rather than an uncaught parser error, and
  re-importing a run is idempotent.

The adversarial review of this increment found one Must fix — a call that
failed at the provider was recorded under the pinned model rather than the
routed one — and three Should fix. All four are fixed, and the failure path is
now covered by a test that fails without the fix.

**Still not done.** The chain falls through at *selection* time. A model that
passes selection and then fails at call time fails its chunk into a rejection,
and that chunk is not retried with another model.

---

## Follow-up two — the chain falls through at call time

A third commit, `phase2: fall through the model chain when a call fails`,
closes the last gap.

- The extraction runs through a `FallbackGenerator`. When a candidate's call
  fails, the failed identity is excluded, the router is asked for the next
  candidate, and the same request is retried. The walk is bounded: the
  primary plus two fallbacks, because the free tier catalogue holds 29 models
  and a provider that is down costs the configured timeout per attempt.
- `model_executions.jsonl` is now built from the generator's attempts rather
  than from the extraction's call records, so one row is written per provider
  call, with the model that made it, its own fallback index, and its outcome.
- The manifest's `provider` and `model` name the router's primary selection —
  the policy that decided the run — so a transient provider failure cannot
  change `research_runs.model_policy_version` and make a re-import a
  conflict. The per-call truth stays in the execution rows.
- A call that failed names the model that failed it: the error carries the
  provider and the model, and the extraction names what the error carries.
- The run import is one transaction. The catalogue, the role selection and the
  executions are written inside the transaction `import_run` already owns.

The adversarial review of this increment found two Must fix: a call that failed
on a later candidate was attributed to the primary model, and the run's
identity had come to depend on which candidate happened to answer. Both are
fixed.

### The fallback, proved live

The next real extraction after the change failed on the first two candidates
and was carried by the third:

```
model_executions.jsonl
  deepseek-v4.1-flash      fallback_index 0  status error  HTTP 503 Service Unavailable
  deepseek-v4-pro          fallback_index 1  status error  HTTP 503 Service Unavailable
  minimax-m3.1-flash-free  fallback_index 2  status ok

calls.jsonl
  minimax-m3.1-flash-free  status ok
```

The call record names the model that answered, the execution rows name every
model that was tried, and the run finished instead of dying. The two DeepSeek
models on the free tier were returning 503 at that moment; the chain carried
the run. That is the whole feature working on its own, unprompted.

**Still not done.** A candidate refused at the preflight check is recorded as a
failed attempt, so the file explains the skip, but the run-level
`provider_check_failed` rejection names the primary — the run's policy —
rather than the candidate the check refused.

**Not checked.** The live OpenCode CLI path was exercised by hand at the start of
the run (624 models, server version 2.1.2), but the discovery fallback was
exercised only against fakes, because the route never failed on this machine.
The persistence functions were tested against an in-memory database, not a
file-backed one.

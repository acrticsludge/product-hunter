# Dispatch prompt — Stage 7 REVIEW (adversarial, fresh context)

**Node:** `loop-free-review-1`
**Repo:** `C:\Anubhav\Web Dev Projects\product-hunter`
**Mandate:** try to prove the change wrong. You cannot edit. You do not commit.

---

## What to review

The dynamic model router, against the spec `docs/resonix/specs/model-routing.md` and
the plan `docs/resonix/plans/model-routing-implementation-plan.md`.

Files in the change:

- `src/product_hunter/llm/catalog.py` (new) — identity, availability, catalogue
  snapshot, parsing of the OpenCode model route and of the `opencode models` list,
  catalogue diff, catalogue version digest.
- `src/product_hunter/llm/discovery.py` (new) — discovery over the documented OpenCode
  CLI, plus the TTL refresher.
- `src/product_hunter/llm/routing.py` (new) — thirteen roles, per-role chains,
  the selector, the selection record, the execution-provenance record.
- `src/product_hunter/db/model_routing.py` (new) — persistence of catalogues, the
  model inventory, role selections, executions.
- `db/migrations/0002_model_routing.sql` (new) — four additive tables.
- `config/model_routing.yaml` (new) — thirteen roles written as patterns.
- `src/product_hunter/llm/opencode.py` (modified) — the new shared
  `run_opencode_cli` argv runner; the generation provider now calls it.
- `src/product_hunter/llm/__init__.py` (modified) — `build_router`.
- `src/product_hunter/config.py` (modified) — `RoutingConfig`, `load_routing_config`.
- `src/product_hunter/cli.py` (modified) — the `ph models` command.
- `src/product_hunter/db/__init__.py` (modified) — exports.
- `tests/test_model_catalog.py`, `tests/test_model_discovery.py`,
  `tests/test_model_routing.py` (new), `tests/test_db_migrations.py` (modified).

The task this change answers:

- Do not hard-code a permanent model list.
- Do not hard-code DeepSeek V4.1 Flash as the final architecture.
- The OpenCode generation bridge must stay compatible.
- Discover the models available through OpenCode using the most reliable
  currently-supported OpenCode mechanism. Do not invent undocumented APIs.
- A model becoming available must NOT require source-code modification.
- A model disappearing must NOT crash the pipeline.
- Preserve historical model IDs.
- Record: provider, model ID, role, selection source, fallback, catalog version if
  available, prompt/config version, latency, success/failure, run/stage.
- If the mechanism cannot provide true instant events, implement the best safe
  refresh mechanism and clearly document the limitation.
- Do not add provider API keys. Do not implement SubQ integration. Do not implement
  model benchmarking beyond interfaces and tests.

## The review method

Read the diff against the base commit (`205a4ba`, `git diff 205a4ba -- <paths>`), then
read the connected code, not only the diff. Attack it in this order:

1. **Correctness.** Walk the role-selection path end to end: config load → chain
   expansion → pattern matching → availability filter → default termination →
   selection record. Find the input that makes it pick the wrong model, the wrong
   order, or raise where it should not. Is the pattern ordering deterministic and
   independent of the order the server listed the models in?
2. **The four properties.** Prove or disprove each: a new model becomes usable with
   no source edit; a vanished model does not crash; historical ids survive; every
   execution is attributable. For each, name the code path that makes it true, and
   say if a test actually pins it.
3. **Claim vs code.** The spec claims the discovery uses the most reliable supported
   OpenCode mechanism, that the fallback is used only when the primary fails, and
   that an empty catalogue is valid while a malformed one is refused. Check the code
   says what the spec says.
4. **Compatibility.** `tests/test_llm_contract.py` passes unchanged. Read it and the
   new `run_opencode_cli` and confirm the generation bridge really is unchanged in
   behaviour, not just in test outcome.
5. **Test quality.** For each of the nine required cases (discovery, refresh,
   unavailable model, fallback, role selection, historical model preservation,
   malformed catalog, empty catalog, provider/model identity), find the test and
   judge whether it would fail if the behaviour broke. Name any test that asserts
   nothing, or that passes for the wrong reason.
6. **Leanness.** The `ponytail-review` rubric: correct → safe → load → tested → fast
   → lean. Is every line needed? What could be deleted? Report
   `Lean: -N lines possible` with the specific deletions.
7. **The migration and the test edit.** `db/migrations/0002_model_routing.sql` is
   additive; `tests/test_db_migrations.py` changed two version assertions from 1 to
   2 and added four table names to `EXPECTED_TABLES`. Judge whether that is a
   legitimate fact update or a weakened test, and say so plainly.

## Known item, already judged by the implementer, confirm or refute

The router's `self.last_error` is set when discovery fails but is never cleared
after a later successful discovery, so a later "no model available" refusal can
name a stale failure. `src/product_hunter/llm/routing.py`, `ModelRouter._catalog`
and `ModelRouter._refresh`. Say whether this is a real defect, and give the
smallest correct fix.

## Reporting rules

- Quote the code. A finding without file, line and a quoted snippet is not a finding.
- Sort into **Must fix**, **Should fix**, **Nice to have**.
- Every finding needs a concrete failure: an input, a state, and what goes wrong.
- Say plainly when something has no finding. Do not manufacture findings.
- End with a `Verdict:` line — `ship`, `ship with should-fixes`, or `fix first` —
  and a `Lean:` line.
- This checkout is shared with other agents working right now. Do not edit, revert,
  stash, clean or reset anything. Do not run the full test suite; read the code.
  If you must run something, scope it to the routing test files.

## The rubric, in full

Load the `ponytail-review` skill and use its six-pass rubric and its
Must/Should/Nice finding format, including the `Verdict:` line and the
`Lean: -N lines possible` line.

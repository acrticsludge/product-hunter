# Handoff: Phase 4 Wave 1, research contract and tool gate foundation

**RUN_ID:** phase4-20261021-a307a42
**Session:** ses_ea307a420ffeJZAhl7OEYLfa9z
**Date:** 2026-10-21
**Risk:** declared medium, floor medium (no auth, payment, secret, or SQLite schema change)

## 1. Goal

Build the code-enforced Research Contract and Tool Gate so the worker, not an agent instruction, decides whether a research stage happened. Deliverable: contract schema, contract validation, gate engine, tool-call provenance, budget model, deterministic tests. No collectors, no scraping, no competitor or counter-evidence execution.

## 2. Git baseline (stage 0, read-only)

| Item | Value |
|---|---|
| Branch | `main` |
| HEAD | `205a4ba` "Phase 2 step 1: authoritative SQLite evidence core" |
| Tree | clean except untracked `mcp-debug-emergency.log` (not ours, leave it) |
| Remote | `github.com/acrticsludge/product-hunter.git` |

The last four commits are Phase 1 steps 1 to 4 and the Phase 2 evidence core. Nothing in this wave touches the database.

## 3. Repo map

| Path | What it is |
|---|---|
| `src/product_hunter/config.py` | `ExtractionConfig` pydantic model, `repo_root()`, `load_config()`, `PH_` env overrides |
| `src/product_hunter/sources.py` | `SourceConfig` pydantic model, `SourceRegistry`, YAML loading, `SourceBlocked` |
| `src/product_hunter/documents.py` | `Document` model and community/author helpers |
| `src/product_hunter/fetch.py` | `FetchRecord` provenance-shaped model, `utcnow()` helper |
| `src/product_hunter/llm/base.py` | `TextGenerator` protocol, `GenerationRequest`/`GenerationResponse` |
| `src/product_hunter/db/` | `Store`, `connect`, `migrate`, jobs, checkpoints, outbox, worker |
| `db/migrations/0001_initial.sql` | The authoritative schema. Not touched in this wave. |
| `config/extraction.yaml` | Extraction configuration |
| `config/sources.yaml` | Source registry with terms status and rate limits |
| `tests/` | 18 pytest files, one per module, `def test_...` style, plain asserts |
| `docs/reasonix/specs/` | The full product spec and this wave's spec |
| `docs/reasonix/plans/` | Per-phase implementation plans |
| `docs/reasonix/reports/` | Per-phase reports |

Commands:

```text
uv run pytest                 # the code gate, from .loop/adapter.json
uv run ph --help              # CLI entry point, product_hunter.cli:main
```

## 4. Conventions to follow

- Python 3.14, `from __future__ import annotations` at the top of every module.
- Pydantic v2 (`BaseModel`, `Field`, `model_validator`, `ConfigDict`). No dataclasses where a model carries validation.
- `Enum` for closed vocabularies, `str` mixin so values serialize.
- Errors are exceptions with a docstring, raised at the boundary, never swallowed.
- Tests are plain pytest functions with plain asserts. No classes, no fixtures beyond `tmp_path`.
- Docstrings say what the module is for and what it refuses to do, in two or three lines.
- Comments explain the why, one line each.

## 5. Design decisions already fixed

Read `docs/reasonix/specs/phase-4-research-contract.md` first. It holds the schema, the validation rules, the gate check order, the violation codes, the metric and predicate registries, the budget model, the persistence plan, and the test matrix.

| Decision | Value |
|---|---|
| Package | `src/product_hunter/research/` with `evidence`, `predicates`, `budget`, `contract`, `gate`, `persistence` |
| Import direction | `evidence` and `budget` are leaves; `predicates` depends on `evidence`; `contract` depends on `predicates` and `budget`; `gate` depends on all |
| Provenance field names | The spec's names: `query_or_operation`, `result_ref` |
| Gate result | Stable violation codes, sorted output, no exceptions for a failed gate |
| Contract loading | YAML at `config/research_contract.yaml`, same pattern as `config/sources.yaml` |
| Persistence | Protocols only, no SQLite change, planned migration `0003_research_gate.sql` documented in the spec |

## 6. Blast radius (declared at stage 0)

```text
docs/reasonix/specs/phase-4-research-contract.md
docs/reasonix/plans/phase-4-research-contract.md
docs/reasonix/prompts/phase-4-implement.md
docs/reasonix/prompts/phase-4-verify-code.md
docs/reasonix/prompts/phase-4-harden.md
docs/reasonix/prompts/phase-4-review.md
docs/reasonix/prompts/phase-4-report.md
docs/reasonix/handoffs/phase-4-research-contract-handoff.md
docs/reasonix/reports/phase-4-research-contract.md
src/product_hunter/research/__init__.py
src/product_hunter/research/budget.py
src/product_hunter/research/contract.py
src/product_hunter/research/evidence.py
src/product_hunter/research/gate.py
src/product_hunter/research/persistence.py
src/product_hunter/research/predicates.py
config/research_contract.yaml
tests/test_research_budget.py
tests/test_research_contract.py
tests/test_research_gate.py
tests/test_research_provenance.py
README.md
```

Editing anything else is a radius breach. Say so rather than widening it silently.

## 7. Adapter rules in force

From `.loop/adapter.json`. The ones that shape this wave:

- Never add a provider secret to this repo.
- Never fetch a URL that is not declared in `config/sources.yaml`.
- Never accept extracted evidence without a verbatim source span.
- Never change a test to make it pass.
- Never commit `.env`, `data/`, `runtime/`, or `logs/`.

The code gate is `uv run pytest`.

## 8. SubQ Snap, stage 0

One call, model `subq-snap-preview`, state hash `0fc7cc14d28a`, 692 input tokens, 0 errors, 0 cache hits.

| Question | Answer | Probability | Confidence |
|---|---|---|---|
| lane | `backend_research_infrastructure` | 0.986 | 0.979 |
| risk | `medium` | 0.974 | 0.962 |
| scope | `contract_and_gate_only` | 0.983 | 0.975 |
| design | `typed_schemas_plus_deterministic_predicates` | 0.984 | 0.977 |

The API shape needed discovery: a `choice` question carries `instructions` and a `criteria` mapping of option name to description, and the answer names the winning key. Two 422 probes and a shape probe were spent learning that.

## 9. Notes

`%TEMP%\loop-handoff\handoff.md` holds the handoff of an unrelated project (sql-excel-report-automation). It was read, found irrelevant to this repo, and not merged. The repo map above comes from direct inspection in stage 0.

## 10. Still unverified

- No live research run has exercised this gate. Every input is a test fixture.
- The baseline budget limits are starting values, not measured ones.
- The `promotion` gate key has no stage of its own; it is evaluated at run level.

---

## 11. Resume session

**RUN_ID:** phase4-20261021-resume-01
**Date:** 2026-10-21
**Risk:** declared medium, floor medium (adapter `.loop/adapter.json`)
**Task:** finish the wave. The implementation and its tests are done and green.
What remains is re-harden, review, the docs sync, a scoped commit, and the report.

### 11.1 What the prior session left

Session `ses_ea307a420ffeJZAhl7OEYLfa9z` delivered the research package, the
baseline contract, and 84 research tests. The code gate passed twice:
746 passed in 11.53s, then 746 passed in 8.94s. This session re-ran the same
gate and it is still green.

### 11.2 Repo state at resume

| Item | Value |
|---|---|
| Branch | `main` |
| HEAD | `610c880` "phase2: add dynamic model routing" |
| Tree | dirty and shared; other waves have uncommitted work in it |

The working tree holds other waves' work: `README.md`, `src/product_hunter/db/`,
`src/product_hunter/eval/`, `src/product_hunter/runtime/`,
`src/product_hunter/registry.py`, `src/product_hunter/db/scheduler.py`,
`eval/`, `.opencode/`, and most of `docs/`. None of it is this run's.

The phase-4 work is entirely new files, so a scoped commit stages them by
explicit path and leaves every other working-tree change alone. `README.md`
carries phase-2 content only and is not in this run's radius.

### 11.3 Path drift

This handoff and the phase-4 spec were written under `docs/reasonix/`. The
directory was renamed to `docs/reasonix/` afterwards. Every path below uses the
current name.

### 11.4 Drift found at stage 0

The spec is half-synced with the code. Four items, in the order stage 8 must
close them:

1. **Extraction booleans.** Spec section 10 says the extraction booleans become
   `hard_requirements.require_quote_verbatim` and `require_source_span`. Spec
   section 4.3, the shipped `config/research_contract.yaml`, and the code all say
   they are absent until the extraction wave. Section 10 is the one that is
   wrong. `HardRequirements` is `extra="forbid"`, so adding either field to the
   contract would raise `ContractError`.

2. **Artifact-kind vocabulary.** The metrics in spec section 6.1 read a fixed set
   of artifact kinds: `document`, `candidate_url`, `author`, `community`,
   `evidence_span`, `counter_evidence`, `cluster_neighbors_checked`, plus
   `neighbor_check` and `duplicate_review` for the predicates. The
   `required_outputs` in the shipped contract name a different vocabulary:
   `source_records`, `provenance`, `normalized_records`, `cluster_assignments`,
   `similarity_checks`, `independent_source_count`, `independent_person_count`,
   `community_count`, `frequency_analysis`, `trend_analysis`,
   `evidence_summary`, `opportunity_score`, `confidence`, `competitors`,
   `alternatives`. Only `counter_evidence` appears in both. Every threshold that
   counts artifacts therefore counts a kind the contract never asks for. The
   spec does not say which vocabulary is authoritative.

3. **Migration number.** Spec section 9 and the `research/persistence.py`
   docstring both name the planned migration `db/migrations/0002_research_gate.sql`.
   `0002` is taken by `0002_model_routing.sql`. `docs/reasonix/ARCHITECTURE.md`
   already records the collision.

4. **Gate behaviour beyond the spec.** Three places where the code is stricter
   or different from the spec text, each with a test that pins the behaviour:
   a stage that has not produced a result yet reports `stage_missing`; a budget
   overrun is attached to every stage, including one that failed or never ran;
   and promotion is blocked when the run is over budget, which spec section 7.4
   does not list.

### 11.5 Remaining stages

| Stage | Node | What it must settle |
|---|---|---|
| 6 HARDEN | `loop-free-harden` | Security and observability pass over the package. |
| 7 REVIEW | `loop-free-review-1` | Fresh-context adversarial verdict over the diff. |
| 8 PRODUCTIONIZE | orchestrator | Close the four drift items, write the report. |
| 9 SHIP | orchestrator | Re-run the gate, stage by explicit path, commit. |
| 10 REPORT | `loop-free-scribe` | Final run report. |

---

## 12. Close

**RUN_ID:** phase4-20261021-resume-01
**Date:** 2026-10-21
**Status:** the wave is finished.

### 12.1 The gate is green

```text
uv run pytest
980 passed in 13.31s
exit code 0
```

The research package on its own: 141 passed, exit code 0. The four files split
as 38 contract, 75 gate, 13 budget, and 15 provenance tests.

The review verdict is ship. The package went through one security and
observability pass, a re-harden pass, and a run of review rounds that returned
findings. The nine review rounds that the report names each produced a fix
round, and two later fix rounds came from re-checks of the round before them.
A further review pass was running in parallel with the fourth stage-8 pass
and did not report into it. Each pass returned a verdict, each accepted
finding was fixed, and each behavioural fix has a test that pins it. The
report at `docs/reasonix/reports/phase-4-research-contract.md` records what
each pass found, what was left on purpose, and what a reader must not trust
yet.

### 12.2 The drift is closed

The four items in section 11.4 are closed, and the code has moved further than
that list, so the spec was rewritten against the code rather than patched.

| Item | Where it landed |
|---|---|
| The extraction booleans | Section 10 of the spec now says they do not become hard requirement flags and arrive with the extraction wave. `HardRequirements` is `extra="forbid"`. |
| The artifact-kind vocabulary | Section 10 of the spec now says the shipped stages declare the kinds their own thresholds and quality checks read, and section 6.1 names the metric-to-kind mapping. |
| The migration number | `0003_research_gate.sql`, in the `persistence.py` docstring, in the spec, and in `docs/reasonix/ARCHITECTURE.md`. |
| The gate behaviour beyond the spec | Section 7.2 of the spec lists the check order the code runs, including the budget overrun on every stage. |

Fix round 10 moved the code again, and the second stage-8 pass brought the
documents back to it. Six behaviours changed, and each one is now written down
where a reader of the spec or the two package documents will meet it.

| Change | Where it landed |
|---|---|
| The run-level counter-evidence zero comes only from the stage that serves the `counter_evidence` gate key | Section 4.2 and section 7.3 of the spec, and the threshold paragraph of `docs/reasonix/RESEARCH_CONTRACT.md` |
| The run-level value of a distinct metric is the union of the per-stage sets | Section 6.1 and section 7.3 of the spec, and the metrics paragraph of `docs/reasonix/RESEARCH_CONTRACT.md` |
| The gate measures a budget overrun against the contract's budgets | Section 8 of the spec, section 7.4 of the spec, and the budget paragraph of `docs/reasonix/RESEARCH_CONTRACT.md` |
| An artifact of a required output kind must belong to the stage being judged | Section 5.2, section 7.1 and section 7.2 of the spec |
| The two timeout cases carry different messages | Section 7.2 of the spec and the test matrix in section 11 |
| The fingerprint is taken over a canonical form of the contract | Section 4.6 of the spec, new, and the contract paragraph of `docs/reasonix/RESEARCH_CONTRACT.md` |

Fix rounds 11 and 12 moved the code again, and the third stage-8 pass brought
the documents back to it. Rounds 11 and 12 closed the last of the
misfiled-artifact class: an artifact filed under a stage other than the one
being judged was counted towards the threshold of the stage being judged, and
it satisfied the two artifact-reading quality predicates as well. The counting
rule is one helper now, and both the metrics and the quality predicates go
through it.

| Change | Where it landed |
|---|---|
| A metric counts an artifact only when it is valid, belongs to the execution's stage, and traces to a successful call of that stage | Section 5.2, section 6.1 and section 6.2 of the spec, and the metrics paragraph of `docs/reasonix/RESEARCH_CONTRACT.md` |
| The two artifact-reading quality predicates go through the same helper the metrics use | Section 6.2 of the spec and the metrics paragraph of `docs/reasonix/RESEARCH_CONTRACT.md` |
| The validator refuses a stage whose threshold or quality check reads an artifact kind it does not declare | Section 4.5 of the spec, rules 9 and 14, and the contract paragraph of `docs/reasonix/RESEARCH_CONTRACT.md` |
| An invalid contract returns the run's violation and no fingerprint, and the run does not raise | Section 7.3 and section 7.4 of the spec, and the gate paragraph of `docs/reasonix/RESEARCH_CONTRACT.md` |
| A non-finite budget amount is refused before it reaches the ledger | Section 8 of the spec, and the budget paragraph of `docs/reasonix/RESEARCH_CONTRACT.md` |
| A stage that finished before it started cannot be shown to have finished inside its timeout | Section 7.2 of the spec and the test matrix in section 11 |
| Three hard-requirement flags are enforced through the stage gate only | Section 4.3, section 7.5 of the spec, new, the honest limit of `docs/reasonix/RESEARCH_CONTRACT.md`, and the research paragraph of `docs/reasonix/ARCHITECTURE.md` |

Fix round 13 moved the code again, and the fourth stage-8 pass brought the
documents back to it. Three behaviours changed, and each one is now written
down where a reader of the spec or of the two package documents will meet it.

| Change | Where it landed |
|---|---|
| A call filed under another stage counts towards no metric of the stage being judged and satisfies no required tool, while the same call still reports `provenance_mismatch` | Section 4.2, section 6.1, section 6.2 and section 7.2 of the spec, and the metrics paragraph of `docs/reasonix/RESEARCH_CONTRACT.md` |
| The run-level value of a minimum metric is the smallest of the run's stage values, and zero for a run with no executions | Section 6.1 and section 7.3 of the spec, the metrics paragraph of `docs/reasonix/RESEARCH_CONTRACT.md`, and the research paragraph of `docs/reasonix/ARCHITECTURE.md` |
| A refused non-finite budget amount raises the named class `NonFiniteAmount`, which subclasses `ValueError` | Section 8 of the spec, the budget paragraph of `docs/reasonix/RESEARCH_CONTRACT.md`, and the research paragraph of `docs/reasonix/ARCHITECTURE.md` |

### 12.3 The next wave

The next wave is the persistence wave. It writes the tool calls to SQLite.

It inherits:

- **The package.** `src/product_hunter/research/`, seven files. Fix rounds 11,
  12, and 13 changed five of them: `gate.py`, `predicates.py`, `budget.py`,
  `contract.py`, and `__init__.py`. `evidence.py` and `persistence.py` are as
  the wave wrote them, and `__init__.py` carries the exports, one of which
  round 13 added.
  `src/product_hunter/research/persistence.py` holds the two protocols it
  codes against: `ToolCallStore` with `append_tool_call` and
  `list_tool_calls`, and `GateResultStore` with `record_gate_result` and
  `list_gate_results`. Both are `runtime_checkable` `Protocol` classes and
  nothing in the product satisfies them yet.
- **The planned migration.** `db/migrations/0003_research_gate.sql`, with the
  two tables written out in the `persistence.py` docstring: `tool_calls` and
  `stage_gate_results`. The number is 0003 because 0002 is taken by
  `0002_model_routing.sql`. The `run_id` foreign key points at
  `research_runs(run_id)`, which `0001_initial.sql` already creates.
- **The shapes to write.** `ToolCallProvenance` has fourteen fields, ten
  required and four optional. `GateResult` carries the run id, the stage, the
  verdict, the violations, and the metrics. `RunGateResult` carries the
  contract fingerprint of the contract it was judged under, and
  `evaluate_promotion` refuses a result judged under another contract, so a
  stored result has to keep the fingerprint or the binding is lost. The
  fingerprint is a hash of a canonical form of the contract, so a stored
  fingerprint has to be recomputed the same way when it is checked. A run
  judged under an invalid contract reaches no verdict and carries no
  fingerprint, so an empty fingerprint in a stored row means either that the
  row was hand-built or that the contract was invalid at the time.
- **The handle.** `tool_call_id` is the primary key of the planned table and
  the handle an artifact points back to. The gate's derivation check depends
  on the stored value being the same handle the artifact carries, and so does
  the metric that counts the artifact.
- **The untrusted-text rule.** A violation message and its detail mapping hold
  ids, names, counts, and fixed phrases, and nothing else. The stage error
  text and the artifact invalid reason are not in them, on purpose: the
  security pass in this wave found both reaching a violation detail. If the
  persistence wave wants them, it reads them from the `StageExecution` and the
  `ArtifactRecord` and writes them under its own rules.
- **The residuals it inherits.** No live research run has exercised the gate.
  Every input in the 141 tests is a fixture. The budget limits in the shipped
  contract are starting values and not measured ones, and the threshold values
  have not been validated against a labelled corpus. A budget violation names
  the dimension that went over and not the stage that went over, because the
  tracker does not attribute the spend to a scope. The package has no caller,
  so nothing gates anything yet. The `promotion` gate key has no stage of its
  own and is evaluated at run level. Three hard-requirement flags,
  `require_provenance`, `require_qdrant_check`, and `require_duckdb_analysis`,
  are enforced through the stage gate only, so a contract that sets
  `promotion.require_all_stages_passed: false` does not enforce them at
  promotion time. The shipped contract sets the flag to true.

# Final Audit Probe #3 — Research Contract, Tool Enforcement, and Gate Semantics

**Audit date:** 2026-10-22
**Auditor:** Final Audit Probe #3 (read-only)
**Scope:** Research contract, tool enforcement, gates, stage attempts, provenance, budgets, failure semantics, and the path from a research run to a `PROMISING` or `VALIDATED` opportunity.
**Repo:** `product-hunter`, branch `main` @ `00a6ca3`, plus the 8 Wave 3 branches.

> **Path note.** The audit request named the output `docs/resonix/reports/final-audit-03-research-contract.md`. The repository's actual docs root is `docs/reasonix/` (correct spelling), and the `wave3/qdrant-semantic` branch contains commit `7a32a04 docs: drop the misspelled docs root from the documentation tree` specifically to remove the misspelling. This report is written to `docs/reasonix/reports/final-audit-03-research-contract.md` to match the repository. No `docs/resonix/` directory was created.

---

## 1. Method and boundaries

Read-only audit. No source file was modified. The only commands run were `git worktree list`, `git branch`, `git diff --stat`, `git log`, `Select-String`/`Get-ChildItem` source reads, and one `pytest` invocation (which writes only `.pytest_cache`). Evidence is cited as `path:line` against the worktree named in each section.

**Worktree discovery was done first, from git, rather than assumed.** `git worktree list` returned 8 Wave 3 worktrees. All 8 were audited:

| # | Worktree path | Branch | HEAD |
|---|---|---|---|
| 1 | `C:\Anubhav\Web Dev Projects\product-hunter\.worktrees\wave3-opencode-control-plane` | `wave3/opencode-control-plane` | `5e45c63` |
| 2 | `C:\Anubhav\Web Dev Projects\product-hunter-w3a` | `wave3/autonomous-operation` | `1efc328` |
| 3 | `C:\Anubhav\Web Dev Projects\product-hunter-w3c` | `wave3/source-expansion` | `a9540ee` |
| 4 | `C:\Anubhav\Web Dev Projects\product-hunter-wt\wave3-analytics` | `wave3/duckdb-analytics` | `3474e58` |
| 5 | `C:\Anubhav\Web Dev Projects\product-hunter-wt\wave3-observability` | `wave3/observability-evaluation` | `317e7f2` |
| 6 | `C:\Anubhav\Web Dev Projects\product-hunter-wt\wave3-scraping-ladder` | `wave3/scraping-ladder` | `5f66d46` |
| 7 | `C:\Users\anubh\.local\share\opencode\worktree\44767c\wave3\subq-integration` | `wave3/subq-integration` | `172a74f` |
| 8 | `C:\Users\anubh\AppData\Local\Temp\opencode\wt-wave3-qdrant` | `wave3/qdrant-semantic` | `357dd5e` |

All 8 branch from `main` @ `00a6ca3`. **None is merged.** Every Wave 3 branch therefore adds code on top of a baseline that already contains the research contract.

The gate test suites were run to establish that the findings below are about wiring, not about broken tests:

```
$ pytest tests/test_db_research_gate.py tests/test_research_gate.py tests/test_research_query_plan.py -q
164 passed
```

---

## 2. Headline finding

**The research contract is enforced by a well-built library that no execution path calls.**

`src/product_hunter/research/gate.py` implements the gate correctly and fail-closed. `src/product_hunter/db/research_gate.py` wraps it with SQLite persistence, lease fencing, and a `require_promotable()` primitive that raises `GateBlocked`. 164 tests cover it. The repository's own compliance document states the gap plainly:

`docs/reasonix/spec-compliance.md:92-94`
> What is missing is the caller. No worker handler records a tool call or an artifact, no CLI command evaluates a run, and there is no promotion path to refuse.

`docs/reasonix/spec-compliance.md:473`
> The contract, the gate, the budget, the persistence, the fail-closed `require_promotable()`, the scoring, the competitor search and the counter-evidence all exist and are tested. No stage handler calls any of it.

`docs/reasonix/spec-compliance.md:512`
> `require_promotable()` raises `GateBlocked`; 62 tests in `tests/test_db_research_gate.py`; no caller

This audit confirms that finding independently, and adds four things the compliance document does not say:

1. The `opportunity/lifecycle.py` state machine — the one carrying the `min_independent_people`, `min_confidence`, `counter_evidence_searched`, and `human_decision` requirements — is **also uncalled** (§4, F-02).
2. If the gate *were* wired up tomorrow, the `investigation` stage would **fail every run**, because the contract requires two artifact kinds the only producer of that stage never creates (§4, F-03). The gap is not a single missing `if`; the contract and the producer are structurally incompatible.
3. The shipped contract declares **no stage timeout**, so the timeout check is off everywhere (§4, F-05).
4. Two independent budget-measurement paths disagree, and the gate's path under-counts exactly the dimension a flaky source blows (§4, F-06).

**Answer to the audit's central question: yes.** A broken, partial, or adversarial research run can currently become `PROMISING`, and it can be `VALIDATED` by a single non-empty string. §6 walks the concrete paths.

---

## 3. Verdict table for the 12 required checks

| # | Check | Verdict | Where |
|---|---|---|---|
| 1 | failed tool ≠ zero result | **HOLDS** in the library; library uncalled | `research/predicates.py:360-366`, `research/investigation/coverage.py:100-104` |
| 2 | timeout ≠ completed stage | **DOES NOT HOLD** — no stage declares a timeout | `config/research_contract.yaml` (no `timeout_seconds`); `research/gate.py:290` |
| 3 | missing artifact ≠ successful stage | **HOLDS** in the library; library uncalled | `research/gate.py:369-384` |
| 4 | invalid schema ≠ accepted result | **WEAK** — `valid` is producer-asserted, never re-validated | `research/evidence.py:121`; `research/gate.py:399-413` |
| 5 | exceeded budget prevents additional calls | **PARTIAL** — blocking works at the call site; the gate's measurement under-counts | `research/budget.py:192-213` vs `db/research_gate.py:1197` |
| 6 | mandatory stages cannot be skipped | **DOES NOT HOLD** — no stage list is enforced anywhere in a run | `research/gate.py:526` (uncalled); `runtime/worker.py` (no stage contract) |
| 7 | provenance is required | **HOLDS** in the library; library uncalled | `research/gate.py:338-367` |
| 8 | evidence thresholds are enforced | **DOES NOT HOLD** — thresholds read by no production code | `config/research_contract.yaml:141-160`; `research/gate.py:466-499` |
| 9 | counter-evidence cannot be falsely marked complete | **HOLDS** at the investigation layer; the contract's counter-evidence *stage* is never evaluated | `research/investigation/counter_evidence.py:74-91` |
| 10 | competitor research cannot be silently skipped | **DOES NOT HOLD** — request-level families enforced, but no stage can satisfy the contract | `research/investigation/pipeline.py:257-287`; F-03 |
| 11 | concentration checks cannot be bypassed | **DOES NOT HOLD** — concentration is a score penalty, never a gate | `opportunity/scoring.py:671-677`; `opportunity/lifecycle.py:291-296` (uncalled) |
| 12 | lease/job failure cannot produce false completion | **PARTIAL** — the lease fence holds; job success does not check evidence | `db/jobs.py:249-257`; `runtime/worker.py:205-215` |

---

## 4. Findings

### F-01 — CRITICAL — The promotion gate has no production caller

`src/product_hunter/db/research_gate.py:791`
```python
def require_promotable(
    conn: sqlite3.Connection,
    contract: ResearchContract,
    run_id: str,
    *,
    worker_id: str | None = None,
    lease_epoch: int | None = None,
) -> MeasuredPromotion:
    """Return the promotion verdict of a run, or refuse it.

    This is the fail-closed primitive: the run is judged from the database,
    and a run the gate does not pass raises ``GateBlocked`` instead of
    returning a verdict the caller might ignore.
    """
```

Call-site sweep across all 9 worktrees (`src/` and `tests/` separated):

| Symbol | Production (`src/`) | Test only |
|---|---|---|
| `require_promotable` | 0 | `tests/test_db_research_gate.py:1476,1485,1545,1616,1630` |
| `evaluate_run_gate` | 0 | `tests/test_db_research_gate.py:1362,1432,1455,1463,1501,1558,1570` |
| `evaluate_stage_gate` | 0 | `tests/test_db_research_gate.py:1512,1541` |
| `evaluate_promotion_gate` | 0 | 0 |

The only `src/` matches are the definitions themselves and the internal calls between them (`db/research_gate.py:717,750,777,791`).

No CLI command evaluates a run. A grep of `src/product_hunter/cli.py` and `src/product_hunter/runtime/` for `research_gate` or `gate` returns nothing.

**Impact:** the refusal primitive exists and is correct. Nothing ever asks the question. A run that would be refused is never refused, because it is never judged.

**Note on the Wave 3 branches:** none of the 8 adds a caller. `wave3/opencode-control-plane` adds `registry.doctor()` / `registry.recover()` (state inspection and requeueing only) and an OpenCode plugin passthrough that spawns the registry CLI; neither evaluates a contract. `wave3/autonomous-operation` adds a job engine whose handlers are operator-supplied module paths (`runtime/worker.py` `load_handlers(spec)`) and never load or validate the research contract. `wave3/source-expansion` and `wave3/scraping-ladder` add source-registry and collection-ladder code that does not touch the gate tables.

---

### F-02 — CRITICAL — The opportunity state machine that gates `PROMISING` and `VALIDATED` is uncalled

`src/product_hunter/opportunity/lifecycle.py` is the rigorous gate:

```python
# lifecycle.py:273-297 — VERIFYING -> PROMISING
if (current is OpportunityState.VERIFYING and target is OpportunityState.PROMISING):
    score = context.confidence_score
    if score is None:
        return ("confidence_score_missing", ...)
    independent = score.independence.independent_author_count
    if independent < policy.lifecycle.min_independent_people:
        return ("insufficient_independent_people", ...)
    if score.score < policy.lifecycle.min_confidence:
        return ("insufficient_confidence", ...)
    return None

# lifecycle.py:299-308 — PROMISING -> HUMAN_REVIEW
if not context.counter_evidence_searched:
    return ("counter_evidence_missing", "no counter-evidence search ran, so there is nothing to review")

# lifecycle.py:310-319 — HUMAN_REVIEW -> VALIDATED
if not context.human_decision:
    return ("human_decision_missing", "no human decision was recorded")
```

With `config/scoring_policy.yaml:134-136`:
```yaml
lifecycle:
  min_independent_people: 10
  min_confidence: 55.0
  staleness_days: 90
```

A sweep for `TransitionContext(`, `apply_transition(`, `evaluate_transition(`, `human_decision=`, `counter_evidence_searched=` across `src/` in all 9 worktrees returns **only the definition sites in `opportunity/lifecycle.py` itself**. Zero callers.

The state machine actually in use is a different, weaker one: `src/product_hunter/research/investigation/opportunity.py`. It is reached only from `research/investigation/pipeline.py:1280` and `:1283`, and it gates on the *transition table alone*, with no evidence requirements:

```python
# investigation/opportunity.py:90-96
_OUTCOME_STATES: dict[InvestigationOutcome, OpportunityState] = {
    InvestigationOutcome.supported: OpportunityState.human_review,
    InvestigationOutcome.contradicted: OpportunityState.rejected,
    ...
}
```

**Impact:** `VERIFYING -> PROMISING` happens on the investigation's own coverage rule (F-04), with no independent-author count and no confidence floor. `HUMAN_REVIEW -> VALIDATED` happens when `HumanDecision.validate` is passed (`investigation/opportunity.py:123-129`) — a string on an enum, with nothing behind it.

---

### F-03 — CRITICAL — The `investigation` stage can never satisfy the contract it is judged against

`config/research_contract.yaml:114-129`:
```yaml
  investigation:
    required_tools: [search, qdrant, duckdb]
    required_outputs:
      - competitors
      - alternatives
      - counter_evidence
      - opportunity_score
      - confidence
      - candidate_url
    quality_checks: [mandatory_query_families, known_query_families]
    gate_key: competitor_search
```

The only producer of that stage is `research/investigation/pipeline.py`, via `investigation_stage_execution()` (line 1484). It emits exactly four artifact kinds:

```python
# pipeline.py:1466-1482
artifacts.append(_artifact(call, KIND_CANDIDATE_URL, len(urls), result.finished_at))
artifacts.append(_artifact(call, KIND_COMPETITORS, len(produced), result.finished_at))
...
artifacts.append(_artifact(call, KIND_COUNTER_EVIDENCE, len(counter_items), result.finished_at))
...
artifacts.append(_artifact(call, KIND_ALTERNATIVES, len(area_findings), result.finished_at))
```

The `KIND_*` constants available in the investigation package are `competitors`, `alternatives` (`pipeline.py:108-109`) plus `candidate_url` and `counter_evidence` imported from `research/predicates.py:102-103`. **`opportunity_score` and `confidence` are never produced.**

The gate requires every `required_outputs` kind to have an artifact in the stage:

```python
# research/gate.py:369-384
for kind in spec.required_outputs:
    kind_artifacts = [a for a in execution.artifacts if a.kind == kind]
    if not any(artifact.stage == stage for artifact in kind_artifacts):
        violations.append(Violation(code=ViolationCode.artifact_missing, ...))
```

**Second, independent defect in the same block:** the `counter_evidence` artifact is produced only when `counter_items` is non-empty (`pipeline.py:1471-1477`). An investigation that finds *no* counter-evidence — the desirable outcome — produces no `counter_evidence` artifact and therefore fails `required_outputs`, even though the contract's own threshold (`min_counter_evidence_items_or_explicit_zero: 1`) explicitly wants the zero accepted. The `explicit_zero_outcome` allowance lives in the *threshold* check and the *predicate*, but `required_outputs` has no zero exception.

**Third:** `required_tools: [search, qdrant, duckdb]` — the investigation stage's tool calls are all `TOOL = "search"` (`pipeline.py:129`). `qdrant` and `duckdb` are never called by that stage, so `tool_missing` fires for both (`research/gate.py:321-336`).

**Impact:** the `investigation` stage serves the `competitor_search` gate key. With `hard_requirements.require_competitor_search: true` and `promotion.require_competitor_gate: true`, every investigation run would be blocked on promotion. **Wiring the gate up as it stands would not start enforcing the contract; it would block all research.** This is a latent correctness defect that sits underneath the missing caller, and it is the single most important thing to fix before any wiring work.

---

### F-04 — CRITICAL — The investigation's coverage floor is far weaker than the contract's evidence thresholds

The contract's `validation` gate key (`config/research_contract.yaml:150-154`) demands:

```yaml
  validation:
    min_independent_authors: 10
    min_independent_communities: 3
    min_query_families: 4
    min_evidence_spans: 20
```

The investigation engine that actually decides "supported" uses a different, much weaker set of floors (`research/investigation/coverage.py:38-50`):

```python
class CoverageRequirements(BaseModel):
    """The floors a search has to clear before its zero counts.
    ...
    Every floor is positive.
    """
    min_queries: int = Field(default=6, ge=1)
    min_documents: int = Field(default=1, ge=1)
    min_domains: int = Field(default=2, ge=1)
```

`assess_coverage()` (`coverage.py:81-161`) measures distinct queries, document count, and distinct domains. It never counts independent authors, independent communities, or evidence spans. It is the sole determinant of the outcome:

```python
# pipeline.py:981-994
incomplete = [area for area in areas if not area_research[area.value].complete]
if incomplete or coverage.status is not CoverageStatus.complete:
    outcome = InvestigationOutcome.incomplete
elif (killer.verdict is StartupKillerVerdict.falsified
      or counter_result.status is CounterEvidenceStatus.found):
    outcome = InvestigationOutcome.contradicted
elif killer.verdict is StartupKillerVerdict.inconclusive:
    outcome = InvestigationOutcome.inconclusive
else:
    outcome = InvestigationOutcome.supported
```

**Impact:** one document from two domains across the mandatory families, with the startup killer not falsified and no counter-evidence found, is enough to reach `supported` → `PROMISING`. The contract's 10-independent-author and 20-evidence-span minimums are never consulted by anything.

---

### F-05 — HIGH — No stage declares a timeout, so the timeout check is off everywhere

`research/gate.py:289-317` implements a fail-closed timeout check, and it is well built:

```python
elapsed = _elapsed_seconds(execution)
if spec.timeout_seconds is not None and (elapsed is None or elapsed > spec.timeout_seconds):
    # A stage that cannot prove it finished inside its timeout is treated
    # as having overrun it: the gate fails closed.
    if elapsed is None:
        timeout_message = f"stage {stage!r} cannot be shown to have finished inside its timeout"
    else:
        timeout_message = f"stage {stage!r} ran longer than its timeout"
```

But `StageContract.timeout_seconds` defaults to `None` (`research/contract.py:71`), and **no stage in `config/research_contract.yaml` sets it.** A search of the file for `timeout_seconds` returns zero matches.

The check is therefore dead for every stage in the shipped contract. A stage that recorded `status=StageStatus.succeeded` after running for a day passes the timeout check, because `spec.timeout_seconds is None` short-circuits the whole comparison.

The `StageStatus.timed_out` enum value exists (`research/evidence.py:45`) and the gate handles it (`gate.py:155-160`), but nothing produces it and no contract declares a deadline.

**Answer to "timeout ≠ completed stage": it does not hold.** The status is producer-supplied (`StageExecution.status`, `research/evidence.py:142`) and there is no deadline for the gate to compare it against.

---

### F-06 — HIGH — The two budget measurement paths disagree, and the gate's path under-counts

**Runtime path** (authoritative for blocking a call) — `research/investigation/pipeline.py:645-660`:

```python
if budget is not None:
    decision = budget.check(step.area, BudgetDimension.search_queries, 1)
    if not decision.allowed:
        run.checkpoint = apply_step(run.checkpoint, _step(step, StepStatus.failed, error="the budget refused the call"))
        run.stopped = True
        break

outcome = executor.search(step.query, family=step.family)
if budget is not None:
    # The call happened, so the call is spent whether or not the
    # result is used.
    budget.record(step.area, BudgetDimension.search_queries, 1)
```

Every search attempt is spent, success or failure.

**Database path** (authoritative for the gate's verdict) — `db/research_gate.py:1191-1221`:

```python
for row in conn.execute(
    "SELECT stage, tool, status, source_id FROM tool_calls WHERE run_id = ?", (run_id,)
).fetchall():
    key = (row["stage"], row["source_id"])
    calls[key] += 1
    if row["status"] == "success":
        if row["tool"] == "search":
            searches[key] += 1
        elif row["tool"] == "fetch":
            fetches[key] += 1
```

`search_queries` and `fetch_requests` are counted **only for successful calls**. Failed, timed-out, and partial calls land in `tool_calls_total` but not in the dimension that actually limits search work.

The contract's search limits are `per_run.search_queries: 160` and per-stage `discovery: 40`, `evidence_collection: 40`, `validation: 20`, `counter_evidence: 20`, `investigation: 30` (`config/research_contract.yaml:164-202`). Against the DB measurement, a run that burns 200 failed searches measures **0** against `search_queries` and is caught only by `tool_calls_total: 500`.

The gate reads the permissive measurement: `build_budget_tracker` (`db/research_gate.py:602-635`) rebuilds the tracker from `_measure_run`, and `research/gate.py:172-196` reports `budget_exceeded` from `budget.exceeded_against(spec)`.

**Impact:** the dimension most likely to be blown by a flaky or hostile source is the one the gate measures most leniently. The per-stage `search_queries` limits are unenforceable against failing searches.

---

### F-07 — HIGH — Unmeasured budget dimensions fail open

`db/research_gate.py:602-635`:

```python
# A dimension that has a source and no rows for this run is not refused.
# It is measured as zero, and ``unmeasured_budget_dimensions`` names it:
# the run recorded no spend for it, which is a gap in what the caller
# recorded and not a measurement the gate can fail.
```

And `require_promotable` (`db/research_gate.py:815-817`) states the policy outright:

> The verdict carries the budget dimensions the run recorded no spend for. The gap never changes the verdict: a run whose stages passed is promoted, and the report says what was not measured.

The message including the gap is only added on the **failure** path (`db/research_gate.py:850-854`). A successful promotion returns `MeasuredPromotion` with `unmeasured_dimensions` populated and `promoted=True`.

The contract declares 13 `per_run` dimensions. The measurable set (`_MEASURED_DIMENSIONS`, `db/research_gate.py`) covers `wall_time_seconds`, `tool_calls_total`, `search_queries`, `fetch_requests`, `source_requests_per_source`, `model_calls`, `usd_cost`, plus the `spend_ledger` dimensions `llm_input_tokens`, `llm_output_tokens`, `embedding_items`, `subq_calls`, `browser_minutes`, `crawl_items`. A run that writes no `spend_ledger` rows measures zero on seven of them and still promotes.

This is deliberate and documented, so it is a design risk rather than a bug — but it is a fail-open on the budget dimension, and it compounds F-06.

---

### F-08 — HIGH — The contract's stopping and retry policies are read by no code

`research/contract.py:120-145`:

```python
class StoppingPolicy(BaseModel):
    min_independent_authors: int = Field(default=10, ge=0)
    min_independent_communities: int = Field(default=3, ge=0)
    min_validation_query_families: int = Field(default=4, ge=0)
    max_consecutive_low_yield_batches: int = Field(default=3, ge=0)
    saturation_threshold: int = Field(default=0, ge=0)

class RetryPolicy(BaseModel):
    max_retries: int = Field(default=2, ge=0)
    backoff_seconds: float = Field(default=5.0, gt=0)
    retry_on: list[str] = Field(default_factory=lambda: ["transient", "timeout"])
    give_up_after_seconds: int | None = Field(default=None, ge=0)
```

A sweep of `src/` for `max_consecutive_low_yield_batches`, `saturation_threshold`, `min_validation_query_families`, `retry_on`, `give_up_after_seconds`, `max_retries`, `contract.retry` finds:

- `max_consecutive_low_yield_batches` — `contract.py:128` (definition) and `discovery/frontier.py:100`, which is the frontier's **own separate policy**, not the contract's.
- `saturation_threshold` — `contract.py:129` and `research/budget.py:30` (a budget dimension name). No implementation.
- `min_validation_query_families` — `contract.py:127` only.
- `retry_on`, `give_up_after_seconds`, `max_retries`, `contract.retry` — `contract.py` only. The fingerprint at `contract.py:219` sorts `retry.retry_on`, so the values affect the contract hash and nothing else.

Retry in the running system is a separate mechanism: `db/jobs.py` `attempts`/`max_attempts` with `backoff_seconds` (`db/jobs.py:284,307`), and `runtime/worker.py:271` uses `self.retry_policy.delay_for(job.attempts)` — the runtime's own policy object, not the contract's.

The repository confirms this: `docs/reasonix/spec-compliance.md:273-274`
> The stopping policy and the saturation rule are carried by the contract and read by no code.

**Impact:** `retry_on: [transient, timeout]` has no subject, because no stage declares a timeout (F-05) and no code consults the contract's retry block.

---

### F-09 — MEDIUM — A job can be marked succeeded without any contract evidence

`runtime/worker.py:179-204`: the handler is invoked and its return value is type-checked (`runtime/worker.py:180-181`, "a job handler must return a dict or None"), then `complete_job` is called. The result dict is stored as `result_json` and never checked against the contract, an artifact set, or a gate verdict. A handler that catches its own exception internally and returns `None` completes the job as succeeded.

`db/jobs.py:234-257` — the lease fence is real and correct:

```python
UPDATE jobs SET status = 'succeeded', result_json = ?, finished_at = ?
WHERE id = ? AND worker_id = ? AND lease_epoch = ?
...
raise ...(f"job {job.id!r} is no longer leased to {worker_id!r} at epoch {lease_epoch}")
```

`db/leases.py:36` `assert_live_lease`, `:78` `assert_fence_pair`, `:92` `fenced_write` are implemented and used by `record_stage_execution` (`db/research_gate.py:438-444`) and `record_gate_result`.

**So the lease fence holds** — a worker that lost its lease cannot write a verdict over a newer worker (`runtime/worker.py:192-193` short-circuits to `_lease_lost` when the heartbeat is lost; `db/jobs.py:249-257` fences the write on `worker_id` and `lease_epoch`). **But a job can still be marked succeeded on a handler that swallowed its own failure.** In `wave3/autonomous-operation`, `runtime/worker.py` gains `load_handlers(spec)`, which loads an operator-supplied `module:attribute` handler and runs it with no contract validation and no gate call.

**Answer to "lease/job failure cannot produce false completion": partial.** No false lease claim; a false job-success claim is possible.

---

### F-10 — MEDIUM — Artifact validity is producer-asserted and never re-validated

`research/evidence.py:109-125`:

```python
class ArtifactRecord(BaseModel):
    ...
    content_hash: str | None = None
    schema_version: str | None = None
    valid: bool = True
    invalid_reason: str | None = None
```

`valid` defaults to `True` and is set by whoever constructs the record. Construction sites: `research/investigation/pipeline.py:1400-1411` (`valid=True` hard-coded) and `clustering/__init__.py:156` (not passed, so the default applies).

The gate reads the boolean and nothing else:

```python
# research/gate.py:399-413
for artifact in execution.artifacts:
    if artifact.kind not in spec.required_outputs:
        continue
    if not artifact.valid:
        violations.append(Violation(code=ViolationCode.artifact_invalid, ...))
```

Nothing re-validates the artifact payload against a schema. `content_hash` and `schema_version` are stored and never checked. A producer that emits a malformed artifact with `valid=True` passes.

The *traceability* half of the check is stronger: `traces_to_successful_call` (`research/predicates.py:305-321`) requires the artifact's `produced_by_tool_call_id` to match a successful call of the same stage, and `research/gate.py:414-430` enforces it whenever the stage declares required tools. And the metrics only count artifacts that are valid, stage-owned, and traceable (`research/predicates.py:85-107`).

**Answer to "invalid schema ≠ accepted result": weak.** Schema validity is asserted, not proven.

---

### F-11 — MEDIUM — The query-family label is not derived from the query text

`research/query_plan.py:79-108` defines the mandatory families; each searching stage must cover `direct_pain`, `failed_solution`, `alternative_to`.

```python
# research/predicates.py:448-467
def mandatory_query_families(context: PredicateContext) -> tuple[bool, str]:
    stage = context.execution.stage
    required = MANDATORY_FAMILIES_BY_STAGE.get(stage, ())
    if not required:
        return True, "the stage has no mandatory query family"
    missing = uncovered_mandatory_families(context.execution, stage)
    ...
```

`covered_families` (`query_plan.py:331-354`) counts a family as covered when a *successful* search call carries that `query_family` string and a query with visible text. `known_query_families` (`predicates.py:470-488`) requires every search call to name a family in `_KNOWN_FAMILIES`, closing the "any string works" gap. Both predicates are in the shipped contract's `quality_checks` for all five searching stages.

What remains: **the family is a label the producer attaches to the call** (`ToolCallProvenance.query_family`, `research/evidence.py:83`). The in-tree producers are honest — `discovery/records.py:210` uses `self.family.value` from a `QueryFamily` enum, and `research/investigation/pipeline.py:517` uses `step.family`, also an enum. But the gate reads the string from the DB row, so three calls with three different family labels and one identical query satisfy all three mandatory families.

**Impact:** low in practice given honest producers; real for any writer that goes straight to the `tool_calls` table.

---

### F-12 — MEDIUM — The investigation pipeline never increments the attempt number

`db/research_gate.py:668-707` implements a strict "latest attempt wins" rule:

```python
"""Rebuild the evidence of a run from what it wrote to the database.

A stage is judged on the evidence of its latest attempt and on nothing
else: a stage that re-ran and produced nothing is judged as a stage that
produced nothing, and the evidence of the attempt before it is not
handed to it.
"""
...
rows = conn.execute(
    "SELECT * FROM research_stage_executions WHERE run_id = ? ORDER BY stage, attempt", (run_id,)
).fetchall()
for row in rows:
    stage = row["stage"]
    attempt = int(row["attempt"])
    stages[stage] = _execution_from_row(row, calls.get((stage, attempt), []), artifacts.get((stage, attempt), []))
```

`record_stage_execution` upserts by `(run_id, stage, attempt)` with `execution_id = _hash_id("exec_", f"{run_id}|{execution.stage}|{row_attempt}")` (`db/research_gate.py:435-437`), so attempts cannot overwrite each other.

But `investigation_stage_execution` constructs the execution without an `attempt`:

```python
# research/investigation/pipeline.py:1484-1492
return StageExecution(
    stage=STAGE,
    status=StageStatus.succeeded,
    started_at=result.started_at,
    finished_at=result.finished_at,
    tool_calls=list(result.tool_calls),
    artifacts=artifacts,
    zero_result=_zero_result(result),
)
```

`StageExecution.attempt` defaults to `1` (`research/evidence.py:145`). **Every re-run of the investigation overwrites attempt 1** instead of creating attempt 2. The "latest attempt wins" rule is never exercised for the one stage that produces competitor research, and retry history is invisible to the gate.

---

### F-13 — MEDIUM — Concentration is a score penalty, never a gate

Two independent concentration systems exist:

`independence/analytics.py:26-99` — share-based violations (`min_independent_authors`, `min_independent_communities`, `max_single_source_share`, `max_single_community_share`, `max_single_author_cluster_share`, `max_single_propagation_chain_share`), returning a `ConcentrationVerdict` with violations.

`opportunity/scoring.py:671-677` — a Herfindahl penalty:
```python
def _concentration_penalty(concentration: float, policy: ScoringPolicy) -> float:
    neutral = policy.concentration.neutral_index
    maximum = policy.concentration.max_penalty
    if concentration <= neutral:
        return 0.0
    penalty = maximum * (concentration - neutral) / (1.0 - neutral)
```
with `config/scoring_policy.yaml:124-125`: `neutral_index: 0.34`, `max_penalty: 0.5`. The index itself is `max(community_concentration, source_concentration)` (`opportunity/signals.py:400-402`).

**Concentration lowers the confidence score. It never blocks anything.** The score is compared against `lifecycle.min_confidence: 55.0` — in `opportunity/lifecycle.py:291-296`, which F-02 shows is uncalled.

**Answer to "concentration checks cannot be bypassed": they can.** A maximally concentrated evidence set (one community, one source, index 1.0) incurs a 0.5 penalty on one confidence component and then faces no threshold at all.

---

### F-14 — LOW — The `promotion` gate key has no threshold block

`config/research_contract.yaml:141-160` defines `evidence_thresholds` for `discovery`, `competitor_search`, `validation`, `counter_evidence`, and `clustering`. There is no `promotion:` key.

`research/gate.py:604-610` therefore returns immediately:
```python
threshold = contract.evidence_thresholds.get(PROMOTION_GATE_KEY)
if threshold is None:
    return []
```

The whole run-level threshold path (`_run_metric_value`, `_counter_evidence_zero_recorded`, `_promotion_threshold_violations`) is dead under the shipped contract. The mechanism is sound and tested; it just has no configuration.

---

### F-15 — LOW — A competitor need not trace to a tool call

`research/investigation/competitors.py:70-73`:
```python
    # The call that found this competitor, so the wave 1 gate can trace the
    # artifact of that call back to it.
    tool_call_id: str | None = None
```

Optional, whereas artifacts are bound through `produced_by_tool_call_id` and checked by `traces_to_successful_call`. A competitor with no `tool_call_id` would not be reachable from the artifact chain. `_artifact` binds the artifact to `call.tool_call_id` for the whole batch (`pipeline.py:1408`), so the batch is traceable even when an individual competitor is not.

---

## 5. What is genuinely solid

The library layer is good work, and the findings above are about wiring and one contract/producer mismatch — not about careless code. Specifically:

**Failed tool ≠ zero result — holds.** `research/predicates.py:360-366`:
```python
failed = [call for call in stage_calls(execution)
          if call.tool == TOOL_SEARCH and call.status is not ToolCallStatus.success]
if failed:
    return False, "a search call did not succeed, so its result is not a zero"
```
And `research/investigation/coverage.py:100-104` counts a failure even when a later attempt of the same family succeeds ("a flaky search cannot support a zero"), with `failures == 0` required for `CoverageStatus.complete` (`coverage.py:140-147`).

**Counter-evidence cannot be falsely marked complete — holds.** `research/investigation/counter_evidence.py:74-91`:
```python
if self.status is CounterEvidenceStatus.none_found:
    if self.coverage.status is not CoverageStatus.complete:
        raise ValueError("none found is only reachable on a complete coverage")
    if self.items:
        raise ValueError("none found cannot carry counter-evidence items")
if self.status is CounterEvidenceStatus.found and not self.items:
    raise ValueError("found needs counter-evidence items")
if self.status is CounterEvidenceStatus.incomplete and self.items:
    raise ValueError("an incomplete result carries no items")
```

**"No competitor found" is not proof of absence — holds.** `research/investigation/competitors.py:109-124` refuses `no_competitors_found` on an incomplete coverage, refuses `high` confidence on a search that found nothing, and carries the unknown "a complete search of a narrow slice of the market is not proof of absence" (`competitors.py:184`).

**Mandatory competitor and counter families cannot be dropped — holds at the request boundary.** `research/investigation/pipeline.py:257-287`:
```python
missing = [family for family in _MANDATORY_COMPETITOR_FAMILIES if family not in self.competitor_families]
if missing:
    raise ValueError("the mandatory competitor query families may not be removed")
...
missing_areas = [area.value for area in _MANDATORY_AREAS if area not in self.areas]
if missing_areas:
    raise ValueError("the competitor and counter-evidence areas may not be dropped")
```

**Missing artifact ≠ successful stage — holds.** `research/gate.py:369-384` (cited in F-03).

**Provenance is required — holds.** `research/gate.py:338-367` emits `provenance_missing` for a successful call with no `tool_call_id` and `provenance_mismatch` for a call bound to another run or stage. `research/gate.py:260-280` rejects a misfiled execution outright. `research/predicates.py:283-321` shares the rule with the metrics. `db/research_gate.py:937` nulls `tool_call_id` when `id_is_declared` is false, converting an undeclared id into a violation rather than a silent pass. `research/investigation/provenance.py:47-53` (`covers()`) keeps a claim's evidence anchored to a ref the provenance knows.

**Budgets do block calls at the call site.** `research/budget.py:192-213` (`guard`) raises `BudgetExceeded` before the call; `research/investigation/pipeline.py:646-654` checks before every search and stops the run cleanly when refused.

**The contract itself fails closed.** `research/contract.py:234-414` rejects a contract with an unknown quality check, a stage that names a check whose artifact kind it does not declare, a threshold on a metric with no artifact source, a non-positive budget limit (with the NaN case handled explicitly at `contract.py:356-360`), a stage serving the reserved `promotion` key, and a hard requirement with no stage to satisfy it.

---

## 6. Can a broken run become `PROMISING` or `VALIDATED`?

Yes. Three concrete paths, each with the code that permits it.

### Path A — the shipped system, today

1. An investigation runs. `research/investigation/pipeline.py:1292` `investigate()` executes the plan against the injected executor.
2. Coverage is assessed with `min_queries=6, min_documents=1, min_domains=2` (`coverage.py:48-50`). One document across two domains clears the floor.
3. The startup killer is not falsified and no counter-evidence was found, so `outcome = InvestigationOutcome.supported` (`pipeline.py:994`).
4. `_recommended_state` (`pipeline.py:1275-1289`) calls `next_state_for_outcome(state, outcome)`. From `verifying`, `supported → human_review` is not an allowed edge, so it falls through to `_FALLBACK_STATES[supported] = (promising, verifying, insufficient_evidence)` (`pipeline.py:1251-1255`) and selects `promising`.
5. `InvestigationResult.recommended_state` is set to `promising`. The model validator at `pipeline.py:343-347` checks only that the recommendation follows from the outcome — a self-consistency check, not an evidence gate.
6. Nothing calls `require_promotable`. Nothing calls `evaluate_transition`. The run is `PROMISING`.

**No independent-author count. No confidence floor. No contract thresholds. No competitor gate. No counter-evidence stage verdict.**

### Path B — a partial or failed run

- A stage whose tool call failed: `DiscoveryStatus.failure → ToolCallStatus.failure` (`discovery/records.py:192`), so the investigation's `assess_coverage` counts a failure and the outcome is `incomplete` → `insufficient_evidence`. **This path is correctly blocked at the investigation layer.**
- A stage whose artifact is missing: `research/gate.py:369-384` would block it. **But the gate is not called**, so a stage that never produced its `opportunity_score` or `confidence` artifact reaches the same outcome as one that did.
- A run that overran its budget: `research/budget.py` blocks the next call, and `research/gate.py:172-196` would raise `budget_exceeded`. **The gate is not called**, and per F-06 the gate's own measurement would have under-counted the overrun anyway.

### Path C — to `VALIDATED`

`research/investigation/opportunity.py:123-129`:
```python
def apply_human_decision(state: OpportunityState, decision: HumanDecision) -> OpportunityState:
    """Move a state the way a human decision says it may move."""
    target = _DECISION_STATES[decision]
    if target not in allowed_transitions(state):
        raise InvalidTransition(state, target)
    return target
```

`HumanDecision.validate` → `OpportunityState.validated` (`investigation/opportunity.py:100`). The check is that the transition edge exists. There is no record of *who* decided, no signature, no link to the research evidence, and no requirement that the opportunity was ever `PROMISING` through the rigorous path.

The rigorous version exists and is unused — `opportunity/lifecycle.py:310-319` requires a non-empty `human_decision`, and `opportunity/lifecycle.py:273-296` requires the engine's own confidence score with 10 independent people and a score of at least 55.0. Neither runs.

---

## 7. Remediation order

Not implemented here — this is a read-only audit. The order below is what the evidence supports.

1. **Fix the contract/producer mismatch first (F-03).** Until `opportunity_score` and `confidence` artifacts exist, or the `investigation` stage's `required_outputs` and `required_tools` are corrected, wiring the gate up blocks every run. This must land before any caller.
2. **Reconcile the two budget measurement paths (F-06).** Make `_measure_run` count `search_queries` and `fetch_requests` on every recorded call, matching `pipeline.py:660`, or make the runtime count successes only. One rule, both paths.
3. **Add `timeout_seconds` to the stages that need one (F-05)**, or delete `StageStatus.timed_out` and the timeout branch so the contract does not advertise a deadline it does not set.
4. **Decide whether the contract's `stopping` and `retry` blocks are live or aspirational (F-08)** and either implement them or remove them. They currently change the contract fingerprint and nothing else.
5. **Wire the gate (F-01)**, then wire `opportunity/lifecycle.py` (F-02), and make `PROMISING` depend on the confidence score and `min_independent_people`, and `VALIDATED` on a recorded human decision with an identity.
6. **Raise the investigation coverage floor to the contract's thresholds (F-04)**, or state in the contract that the investigation's own floor is the binding one.
7. **Make `require_promotable` block on unmeasured budget dimensions (F-07)**, or move the disclosure from the failure message to the success path so a caller sees it.
8. **Increment `attempt` in `investigation_stage_execution` (F-12)** so retry history survives into the gate.
9. **Decide whether concentration should gate (F-13)** or only penalise, and record the decision.

---

## 8. What was skipped, and what was not checked

**Skipped (out of scope, read-only audit):**

- No code was changed, no fix was applied, no branch was created or merged.
- No live research run was executed. No tool was called against a real source. The findings are from source reading, greps, and the existing test suite.
- The `pytest` run covered `tests/test_db_research_gate.py`, `tests/test_research_gate.py`, and `tests/test_research_query_plan.py` only (164 passed). The full suite was not run.
- The `wave3/duckdb-analytics`, `wave3/observability-evaluation`, and `wave3/subq-integration` branches were mapped by file listing and diff stat only. Their diffs touch analytics, observability/eval, and LLM decision routing, none of which appear in the contract path, but their source was not read line by line.

**Not checked / could not be verified:**

- Whether `wave3/source-expansion`'s `communities.py` or `wave3/scraping-ladder`'s `collection/orchestrator.py` and `adapters/routing.py` write to the research gate tables. The gathered evidence found no `ToolCallProvenance` production in the files that were read; these three files were not read.
- Whether `RequestBudget.acquire()` in the scraping ladder blocks or merely waits on overrun. The call site was confirmed; the overrun semantics were not read.
- The runtime behaviour of `runtime/worker.py` under a real lease loss. The fence code path was read; it was not exercised.
- Whether an external writer (outside this repository) can insert rows into `tool_calls`, `research_stage_executions`, or `research_claims` directly. The gate trusts those rows for provenance and status; if another process writes them, F-10 and F-11 become exploitable rather than theoretical. No schema-level write restriction was found in `db/migrations/0003_research_gate.sql`, but no attempt was made to write to those tables from a second connection.
- The 20-minute timeout on `webfetch`/`websearch` tools was not exercised; this report cites only repository files.

**Risks the reader must carry:**

- Every "HOLDS" verdict in §3 and §5 is a statement about the library, not about the running system. The library is not on the execution path.
- The Wave 3 branches are unmerged and mutually independent. Any remediation must account for all 8, not just the branch it lands on.
- `docs/resonix/` was not created; see the path note at the top.

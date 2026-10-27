# Phase 4 Wave 1: Research Contract and Tool Gate Foundation

**Status:** implemented. The code in `src/product_hunter/research/` is the source of
truth for every behaviour below. Where an earlier draft of this document and the
code disagree, the code wins.
**Date:** 2026-10-21
**Source of truth for the product:** `docs/reasonix/specs/Product-Hunter-Full-Spec.md` section 6A (Tool Enforcement and Research Contracts), section 22 (Research Stopping and Budgets), section 38A Phase 4 (Research gate and adversarial validation).
**Lane:** backend research infrastructure. No UI, so no `DESIGN.md` is required.

---

## 1. What this wave delivers

The worker must enforce research requirements in code. Agent instructions are not enforcement.

This wave builds the enforcement substrate:

- a typed Research Contract schema with validation
- a gate engine that turns evidence into pass/fail decisions with stable violation codes
- tool-call provenance records
- a budget model that blocks the next expensive call
- deterministic tests for all of it

Nothing in this wave runs a research stage. There is no collector, no search, no scrape, no competitor or counter-evidence execution, and no scoring. The contract and the gate are the deliverable; the pipeline that feeds them comes later.

## 2. Non-goals

| Not in this wave | Why |
|---|---|
| Discovery or collection collectors | The gate is judged against evidence a collector produces. Building collectors first means the gate gets tested against fabricated evidence. |
| Scraping or browser automation | Same reason, plus it expands the security surface. |
| Competitor and counter-evidence execution | The stages exist in the contract schema and in the gate. Their execution arrives in a later wave. |
| SQLite migration for tool calls | The user asked not to redesign SQLite. The persistence interface is defined and the planned migration is documented in section 9. |
| Scoring, confidence, opportunity lifecycle | Later waves. |
| OpenCode plugin or permission wiring | The spec is explicit that OpenCode hooks are integration safeguards, not the hard gate. |

## 3. Module layout

New package `src/product_hunter/research/`:

| Module | Holds |
|---|---|
| `evidence.py` | Provenance, artifact records, stage execution, claims, run evidence. Pure data plus validation. |
| `predicates.py` | The metric and predicate registries. Each entry is a named function over a stage execution. |
| `budget.py` | Budget dimensions, budget spec, tracker, decisions. |
| `contract.py` | The contract schema, contract validation, YAML loading, the contract fingerprint. |
| `gate.py` | Violation codes, gate results, the stage/run/promotion evaluators. |
| `persistence.py` | Protocols for the future SQLite implementation, plus the planned migration in the docstring. |
| `__init__.py` | Public exports. |

Import direction is one way: `evidence` and `budget` know nothing about the contract; `predicates` depends on `evidence`; `contract` depends on `predicates` and `budget`; `gate` depends on all of them. No cycles.

## 4. Contract schema

`ResearchContract` is a pydantic model. Every field below is required to be present or to have a documented default.

```text
contract_version: str            # e.g. "phase4-v1"
required_stages: list[str]       # ordered, non-empty, no duplicates
stages: dict[str, StageContract]
hard_requirements: HardRequirements
evidence_thresholds: dict[str, EvidenceThreshold]   # keyed by gate key
budgets: BudgetSpec
stopping: StoppingPolicy
retry: RetryPolicy
promotion: PromotionRequirements
```

### 4.1 StageContract

```text
required_tools: list[str]        # tool capabilities, e.g. ["search", "qdrant"]
required_outputs: list[str]      # artifact kinds, e.g. ["source_records"]
quality_checks: list[str]        # predicate names from the predicate registry
gate_key: str | None = None      # resolved key = gate_key or the stage name
timeout_seconds: int | None = None   # 0 or more when set
```

`gate_key` exists because the spec's baseline thresholds are keyed by gate area, not by stage name. A stage that performs competitor search serves the `competitor_search` gate key. Two stages may not serve the same gate key, and no stage may serve the reserved `promotion` key (section 4.5, rules 5 and 6).

A list of tools, outputs, or checks may not hold an empty name. The model refuses it at construction.

### 4.2 EvidenceThreshold

Named integer minima. Every field is optional, and a threshold block that sets
none of them is a contract error (section 4.5, rule 11).

```text
min_distinct_queries
min_source_domains
min_new_documents
min_query_families
min_candidate_urls
min_independent_authors
min_independent_communities
min_evidence_spans
min_counter_evidence_items_or_explicit_zero
min_neighbors_checked_per_cluster
```

Every field is a non-negative integer. The model allows extra fields, so an unrecognised name is caught by validation (section 4.5, rule 12) rather than by the model.

`min_counter_evidence_items_or_explicit_zero` is the one threshold with special handling. It is satisfied by at least that many counter-evidence items, or by a recorded zero-result outcome in which every required query family completed. A failed or unsearched query never counts as zero.

The special handling is applied at the two levels where the field can be read, and the two levels do not read the same zero:

- At stage level, when the metric does not reach the minimum, the gate falls back to the zero the execution being judged recorded (`gate.py`, `_threshold_violations`).
- At run level, when the run's metric does not reach the minimum, the gate falls back to the zero recorded by the execution of the stage that serves the `counter_evidence` gate key, and to no other zero (`gate.py`, `_counter_evidence_zero_recorded`). A zero recorded by another stage says nothing about the counter-evidence minimum, and a contract in which no stage serves that key accepts no run-level zero at all.

The run-level check reads the threshold block of the reserved `promotion` gate key, so a run-level counter-evidence minimum exists only when that block sets this field. When the field sits in the `counter_evidence` block, the stage-level check reads the stage that serves the `counter_evidence` gate key and the run-level check reads that same stage, so one execution cannot pass the stage gate for that key and fail the run gate for it.

A recorded zero counts only when all four of these hold: the outcome names at least one required query family, every required family appears in the completed families, every completed family is carried by a successful search call the stage itself made, and no search call the stage made failed. The family names come from the evidence, so the reasons report how many families are missing or unsearched and never their names (`predicates.py`, `explicit_zero_outcome`).

### 4.3 HardRequirements

```text
require_provenance: bool = True
require_counter_evidence: bool = True
require_competitor_search: bool = True
require_qdrant_check: bool = True
require_duckdb_analysis: bool = True
require_claim_evidence_links: bool = True
```

The flags stay as they are. Every one of the six is read: `require_provenance` and `require_claim_evidence_links` by the gate, `require_counter_evidence` and `require_competitor_search` by both the gate and contract validation, `require_qdrant_check` and `require_duckdb_analysis` by contract validation. A flag that promises a check nobody performs is a lie in the contract, so a flag this wave cannot enforce does not go in the model.

The spec's extraction booleans (`quote_verbatim_required`, `source_span_required`) are absent and stay absent. No stage in this wave produces spans, so there is nothing to check them against. They arrive with the extraction wave, together with the stage that can enforce them. `HardRequirements` is `extra="forbid"`, so adding either field to the contract today raises `ContractError` rather than being ignored.

`require_qdrant_check` and `require_duckdb_analysis` are enforced at contract level (rules 28 and 29 below): a contract that promises a qdrant check needs a stage that requires the qdrant tool, and the stage-level `tool_missing` check then proves qdrant actually ran. `require_claim_evidence_links` and `promotion.require_claim_evidence_links` ask for the same rule from two places in the spec, so the rule applies when either of them is set.

Three of the six flags are enforced only through the stage gate: `require_provenance`, `require_qdrant_check`, and `require_duckdb_analysis`. The promotion check reads the stage result, so with `promotion.require_all_stages_passed: false` a stage that failed on missing provenance, or on a qdrant or duckdb tool that never ran, does not block promotion. The shipped contract sets the flag to true, so the shipped baseline is not exposed to it. Section 7.5 states this as a known limit of the promotion path, not as a promise.

### 4.4 StoppingPolicy, RetryPolicy, PromotionRequirements

```text
StoppingPolicy
  min_independent_authors: int = 10
  min_independent_communities: int = 3
  min_validation_query_families: int = 4
  max_consecutive_low_yield_batches: int = 3
  saturation_threshold: int = 0

RetryPolicy
  max_retries: int = 2
  backoff_seconds: float = 5.0
  retry_on: list[str] = ["transient", "timeout"]   # failure kinds worth retrying
  give_up_after_seconds: int | None = None
  # max_attempts == max_retries + 1

PromotionRequirements
  require_all_stages_passed: bool = True
  require_competitor_gate: bool = True
  require_counter_evidence_gate: bool = True
  require_claim_evidence_links: bool = True
  require_quality_predicates: bool = True
```

The stopping policy and the retry policy are carried by the contract and are read by no code in this wave. The gate does not decide when a run has enough evidence, and it does not retry. The promotion requirements are read by the gate (section 7.4).

### 4.5 Contract validation

`validate_contract(contract)` returns a list of human-readable errors and `ContractError` carries them. An empty list means valid. The checks run in this order, and each one refuses something specific.

1. `required_stages` is empty.
2. `required_stages` names a stage more than once.
3. A required stage has no `StageContract`.
4. A `StageContract` names a stage that is not required. A contract for a stage that can never run is a configuration bug, not a spare.
5. A stage resolves to the reserved `promotion` gate key. No stage serves it; it is evaluated at run level.
6. Two stages resolve to the same gate key.
7. A stage names a quality check that is not in the predicate registry.
8. A stage names `claim_evidence_links` in its `quality_checks`. It is a run-level predicate, and a stage has no claims for it to read. The run-level way to ask for it is `hard_requirements.require_claim_evidence_links` or `promotion.require_claim_evidence_links`.
9. A stage names a quality check that reads an artifact kind it does not declare in `required_outputs`. A check can only be satisfied by an artifact of that kind, so a stage that asks for it has to ask for the artifact too. The kinds the artifact-reading predicates read are named in `PREDICATE_ARTIFACT_KINDS`; a check that is absent from that mapping reads tool calls, every artifact kind, or the run's claims, and this rule does not fire for it. The rule reads every stage that has a contract of its own.
10. A threshold key is neither a gate key that some stage serves nor the reserved `promotion` key.
11. A threshold block sets no minimum.
12. A threshold names a metric that is not in `THRESHOLD_METRICS`.
13. A threshold field maps to a metric that is not in the metric registry.
14. A threshold names a metric that reads an artifact kind that the stage serving that gate key does not declare in `required_outputs`. The kinds the artifact-counting metrics read are named in `METRIC_ARTIFACT_KINDS`; a metric that is absent from that mapping counts tool calls, and this rule does not fire for it. The rule reads the stage that owns the gate key, so a threshold under the reserved `promotion` key, which no stage serves, is not covered by it, and neither is a threshold whose owning stage has no contract of its own, which rule 3 already reports.
15. A required stage declares no gating mechanism: no required tools, no required outputs, no quality checks, and no threshold block for the gate key it serves. A stage with none of those would pass on an empty result, so the contract is wrong.
16. A `per_run` budget names a dimension that does not exist.
17. A soft threshold names a dimension that does not exist.
18. A `per_stage` budget names a stage that is not required.
19. A `per_stage` budget names a dimension that does not exist.
20. A `per_run` limit is not greater than zero.
21. A `per_stage` limit is not greater than zero.
22. A `per_source` limit is not greater than zero.
23. A soft threshold is not in the half-open interval (0, 1].
24. `hard_requirements.require_counter_evidence` is set and no stage serves `counter_evidence`.
25. `hard_requirements.require_competitor_search` is set and no stage serves `competitor_search`.
26. `promotion.require_counter_evidence_gate` is set and no stage serves `counter_evidence`.
27. `promotion.require_competitor_gate` is set and no stage serves `competitor_search`.
28. `hard_requirements.require_qdrant_check` is set and no required stage lists `qdrant` in its required tools.
29. `hard_requirements.require_duckdb_analysis` is set and no required stage lists `duckdb` in its required tools.

Rules 20 to 22 use the form `not limit > 0` rather than `limit <= 0`. Every comparison against NaN is false, so a NaN limit would pass the check and switch the dimension off. NaN is not greater than zero, so it is refused. Rule 23 keeps its own interval and its own NaN-safe form.

Rules 9 and 14 are the same rule about two different readers of the contract. A threshold or a quality check that counts a kind of artifact can only be satisfied by an artifact of that kind, and the artifact has to be an output the stage declares. The shipped baseline satisfies both, and one test walks every threshold and every quality check of the shipped file to keep it that way.

A contract that breaks rule 15 is not a contract that wants a weak stage. It is a contract that promises a stage and describes nothing about it.

Validation fails closed. An invalid contract cannot be evaluated.

`load_research_contract(path)` reads the YAML at `config/research_contract.yaml` by default. A file that is not UTF-8 text, a file that is not valid YAML, and a file whose content does not validate all raise `ContractError` with one message per problem. A pydantic validation failure is flattened into one message per problem by `_validation_messages`.

### 4.6 The contract fingerprint

`contract_fingerprint(contract)` is the SHA-256 of the contract in a canonical form, and it binds a run result to the contract that judged it (section 7.3). The contract version is not enough, because two contracts can carry the same version and different thresholds.

The canonical form sorts the lists whose order carries no meaning: `required_stages`, each stage's `required_tools`, `required_outputs` and `quality_checks`, and the retry policy's `retry_on` (`contract.py`, `_canonical_payload`). Two contracts that produce identical verdicts therefore fingerprint the same. Nothing whose order carries meaning is sorted. The threshold blocks are mappings, and the JSON dump sorts their keys, so a threshold is a difference: a contract with a tighter minimum fingerprints differently.

## 5. Evidence and provenance

### 5.1 ToolCallProvenance

Every material tool execution records one of these.

```text
run_id: str
stage: str
agent: str
tool: str
purpose: str
timestamp: datetime          # timezone-aware
query_or_operation: str
result_ref: str
result_count: int >= 0
status: "success" | "failure" | "timeout" | "partial"
tool_call_id: str | None = None
query_family: str | None = None
duration_ms: int | None = None
error: str | None = None
```

The ten fields the spec lists are required. The last four are optional. The gate reads two of them: `query_family` feeds the query-family threshold, and `tool_call_id` is the handle an artifact points back to. `duration_ms` and `error` are carried as evidence and are read by no code in this wave.

Validation:

- Every required string field except `result_ref` is non-empty.
- `result_ref` is required when `status` is `success`. For other statuses it may be empty.
- `result_ref` contains no whitespace and matches `^[A-Za-z0-9._:/-]+$`. A hand-written summary contains spaces, so a summary cannot be passed off as the original tool result.
- `timestamp` must be timezone-aware. Naive timestamps are rejected.
- `result_count` is never negative.
- `tool_call_id`, `query_family`, and `error` are refused when they are present and empty.

### 5.2 ArtifactRecord

```text
artifact_id: str
stage: str
kind: str                # e.g. "source_records", "counter_evidence"
ref: str                 # durable, content-addressed reference
item_count: int >= 0
content_hash: str | None = None
schema_version: str | None = None
valid: bool = True
invalid_reason: str | None = None
produced_by_tool_call_id: str | None = None
created_at: datetime
```

`produced_by_tool_call_id` is what makes derivation checkable. When a stage declares required tools, every artifact of a required output kind must point at a successful tool call in that same stage. When the stage declares no required tools, the gate does not apply that check, and a valid artifact is accepted without a producing call.

The artifact's own `stage` is the stage it belongs to. An artifact of a required output kind filed under another stage is not an output of the stage being judged, and the gate reports `provenance_mismatch` for it (section 7.2). The same stage binding applies to the metrics and to the artifact-reading quality predicates, so a misfiled artifact neither counts towards a threshold nor satisfies a quality check (section 6.1).

### 5.3 ZeroResultOutcome

```text
recorded: bool = False
required_query_families: list[str] = []
completed_query_families: list[str] = []
note: str = ""
```

### 5.4 StageExecution

```text
stage: str
status: "not_started" | "running" | "succeeded" | "failed" | "timed_out"
attempt: int = 1
started_at: datetime | None = None
finished_at: datetime | None = None
tool_calls: list[ToolCallProvenance] = []
artifacts: list[ArtifactRecord] = []
zero_result: ZeroResultOutcome = ZeroResultOutcome()
error: str | None = None
```

`started_at` and `finished_at` are timezone-aware when present. A naive timestamp is refused at construction, and the message names the field that was refused. A naive timestamp cannot be compared with an aware one, so it would silently switch off the stage timeout check.

### 5.5 ResearchClaim and RunEvidence

```text
ResearchClaim
  claim_id: str
  kind: "no_strong_competitor" | "no_counter_evidence_found" | "opportunity_exists"
  statement: str
  stage: str
  evidence_refs: list[str] = []

RunEvidence
  run_id: str
  contract_version: str
  stages: dict[str, StageExecution]
  claims: list[ResearchClaim]
```

## 6. Metrics and predicates

Both live in `predicates.py` as named registries. A metric returns an integer. A predicate returns a boolean plus a detail string. There are twelve metrics and eight predicates.

### 6.1 Metrics

| Metric | Computation over one stage execution |
|---|---|
| `distinct_queries` | Distinct non-empty `query_or_operation` among successful `search` calls |
| `query_families` | Distinct `query_family` among successful `search` calls |
| `source_domains` | Distinct hosts among successful `fetch` calls |
| `new_documents` | Sum of `item_count` over counted artifacts of kind `document` |
| `candidate_urls` | Sum of `item_count` over counted artifacts of kind `candidate_url` |
| `independent_authors` | Sum of `item_count` over counted artifacts of kind `author` |
| `independent_communities` | Sum of `item_count` over counted artifacts of kind `community` |
| `evidence_spans` | Sum of `item_count` over counted artifacts of kind `evidence_span` |
| `counter_evidence_items` | Sum of `item_count` over counted artifacts of kind `counter_evidence` |
| `neighbors_checked_per_cluster` | Smallest `item_count` over counted artifacts of kind `cluster_neighbors_checked`, zero when there are none |
| `tool_calls` | Number of tool calls the execution holds |
| `successful_tool_calls` | Number of those calls with status `success` |

Three of the metrics count distinct values rather than items: `distinct_queries`, `query_families`, and `source_domains`. Each has a set-returning form beside it, `distinct_query_set`, `query_family_set`, and `source_domain_set`, and the count is the size of the set. `DISTINCT_METRIC_SETS` names the three, and it is what lets the run level take the union of the sets instead of the sum of the counts (section 7.3).

The three distinct metrics read the calls the execution's own stage made. A call that names another stage is not this stage's evidence, so it counts towards no query, no family, and no source domain, and it cannot stand in for a search the stage did not make. The rule lives in one helper, `stage_calls(execution)`, which the metrics, the predicates, and the gate share (section 6.2). The two tool-call metrics are the exception: they count every call the execution holds, whichever stage the call names, so they report the execution's activity rather than the stage's.

One metric is a minimum over what it counts rather than a total: `neighbors_checked_per_cluster` takes the smallest `item_count` over the cluster checks it counts, and zero when there are none. `MINIMUM_METRICS` names it, and it is what lets the run level take the smallest of the run's stage values instead of their sum (section 7.3).

The counting rule for artifacts is one rule, and it is stricter than the gate's:

- A metric counts an artifact only when the artifact is valid, belongs to the execution's stage, and traces to a successful tool call in that stage. An artifact the gate rejected does not count towards a threshold, and an artifact filed under another stage is not an artifact of the stage being judged.
- Tracing means the artifact names a `produced_by_tool_call_id`, and a tool call of that stage carries that handle and has status `success`.
- The gate applies its own derivation check only when the stage declares required tools. The metric always applies it. A stage that declares no required tools can hold artifacts the gate accepts and the metric refuses, and a traceable artifact in such a stage does count. Nothing ties an untraceable artifact to a tool call, so the metric fails closed.

The queries and the hosts are trimmed before they are counted. `distinct_queries` and `query_families` strip the string before it goes into the set and discard the result when it is empty, so one query with trailing whitespace is one query. `_host_of` trims the host before it lowercases it, so one host with trailing whitespace is one source.

A metric that cannot be computed from the stage returns zero. A missing artifact must never be read as a satisfied threshold.

`METRIC_ARTIFACT_KINDS` names the artifact kind each artifact-counting metric reads, and it is built from the same `KIND_*` constants the metric functions use, so the mapping and the functions cannot drift. The tool-based metrics are absent from it because they count tool calls rather than artifacts.

### 6.2 Predicates

| Predicate | True when |
|---|---|
| `non_empty_queries` | A successful `search` call the stage itself made carries a non-empty query |
| `source_diversity` | At least two distinct source domains |
| `nearest_neighbor_checked` | An artifact of kind `neighbor_check` is valid, belongs to the stage, and traces to a successful tool call in the stage |
| `duplicate_candidates_reviewed` | An artifact of kind `duplicate_review` is valid, belongs to the stage, and traces to a successful tool call in the stage |
| `counter_evidence_present_or_explicit_zero` | Items are present, or a zero-result outcome is recorded with every required query family completed and searched by the stage's own search calls |
| `provenance_complete` | Every successful tool call carries complete provenance bound to the same run and stage |
| `artifacts_valid` | Every artifact in the stage is valid |
| `claim_evidence_links` | Every claim in the run carries at least one evidence reference |

The two neighbour and duplicate predicates require a traceable artifact. Validity alone is not enough, because an artifact that no tool call produced is not evidence. They go through the same helper the metrics use, so an artifact filed under another stage cannot satisfy a quality check either, and the stage rule has one implementation. The counter-evidence predicate reads its item count from the `counter_evidence_items` metric, so the same rule applies to it.

The stage rule for calls is one helper, `stage_calls(execution)`, which yields the calls whose own `stage` field equals the execution's stage. It is used at every site that counts a call: the three distinct metric set functions, `non_empty_queries`, the set of searched families and the set of failed searches in `explicit_zero_outcome`, and the gate's `required_tools` check. A call that names another stage is none of this stage's evidence, so it satisfies no required tool and stands in for no search the stage did not make. The provenance check is unchanged: a foreign call still reports `provenance_mismatch` when `require_provenance` is set, so the gate names what it found instead of ignoring the call.

A recorded zero is accepted only when every claimed query family is carried by a successful search call the stage itself made. A family that was never searched is not a zero, and the reason reports the count of the unsearched families rather than their names.

An unknown predicate name is a contract error, never a silent pass.

`PREDICATE_ARTIFACT_KINDS` names the artifact kind each artifact-reading predicate reads. The predicates that are absent from it read tool calls (`non_empty_queries`, `source_diversity`, `provenance_complete`), every artifact kind (`artifacts_valid`), or the run's claims (`claim_evidence_links`), so none of them names a single kind.

## 7. Gate engine

### 7.1 Violation codes

The eight determinations the gate must make, plus the supporting codes they need:

```text
stage_missing             mandatory stage missing
stage_failed              stage failed
stage_timed_out           stage timed out
budget_exceeded           budget exceeded
artifact_invalid          artifact invalid
provenance_missing        provenance missing
evidence_threshold_unmet  evidence threshold unmet
promotion_blocked         promotion blocked

artifact_missing          required output kind has no artifact
tool_missing              required tool never ran successfully in the stage
quality_check_failed      a deterministic quality predicate failed
provenance_mismatch       provenance belongs to another run or stage
contract_invalid          the contract itself is invalid
claim_evidence_missing    a claim carries no evidence reference
```

`provenance_mismatch` covers three cases. A tool call bound to another run or another stage is a mismatch. An execution filed under a stage it does not belong to is a mismatch too, and the gate reports it next to the execution's own status so a misfiled failed stage still carries `stage_failed`. An artifact of a required output kind filed under another stage is a mismatch as well.

Every violation carries the code, the stage, a message, a detail mapping, and a blocking flag. Violations sort by stage, then code, then message, so two runs over the same evidence produce byte-identical output.

A violation message and its detail mapping hold ids, names, counts, and fixed phrases. They never hold raw untrusted text: a query, a URL, or any other free-text input from the evidence stays out of the message, because a later wave writes these messages to the database and a URL can carry a token. The stage error text and the artifact invalid reason stay on the evidence records.

### 7.2 Stage evaluation

`evaluate_stage(contract, run_id, stage, execution, budget=None) -> GateResult`

Checks run in this order:

1. The contract is valid. Otherwise one `contract_invalid` violation and the evaluation stops.
2. The stage has a contract. Otherwise `stage_missing`.
3. The execution exists. Otherwise `stage_missing`.
4. The execution belongs to the stage it is judged against. An execution whose own `stage` is a different stage is `provenance_mismatch`, and the execution's status violation is reported next to it.
5. The execution status is `failed` (`stage_failed`), `timed_out` (`stage_timed_out`), or `not_started` or `running` (`stage_missing`).
6. The stage declares a timeout and the elapsed time cannot be computed, or the elapsed time is past the timeout. Either way the result is `stage_timed_out`, because a stage that cannot prove it finished inside its timeout is treated as having overrun it. The elapsed time cannot be computed when a timestamp is missing or when the stage finished before it started, since a stage with a negative duration has no duration at all. A zero-length stage has a real duration of zero and is in time. The two cases carry different messages: a proven overrun says the stage ran longer than its timeout, and an elapsed time that cannot be computed says the stage cannot be shown to have finished inside its timeout.
7. Every required tool has at least one successful call in the stage (`tool_missing`). A call that names another stage is not this stage's evidence, so a tool that only ran in another stage reports `tool_missing` here.
8. Provenance is complete and bound to this run and stage, when `require_provenance` is set (`provenance_missing`, `provenance_mismatch`).
9. Every required output kind has at least one artifact that belongs to the stage being judged (`artifact_missing`). An artifact of a required kind filed under another stage is `provenance_mismatch`; when every artifact of that kind is misfiled, the kind reports `artifact_missing` as well, because a misfiled artifact does not satisfy the output requirement. Every artifact of a required kind is valid (`artifact_invalid`), and, when the stage declares required tools, traces to a successful tool call in the same stage (`artifact_invalid`).
10. Every quality predicate passes (`quality_check_failed`).
11. Every threshold for the stage's gate key is met (`evidence_threshold_unmet`).
12. The stage stayed inside its budget (`budget_exceeded`).

The budget violations are computed before the checks and are appended to every outcome the stage can return, so a stage that failed, timed out, or never ran carries the budget violation as well as its own. The overrun is a fact about the run, not about one stage result.

A stage passes when it has no blocking violation.

### 7.3 Run evaluation

`evaluate_run(contract, run_id, executions, budget=None) -> RunGateResult`

The run evaluates every required stage and merges the violations. A required stage that never ran is `stage_missing` at run level too, so a run cannot pass by skipping a stage. The result carries the fingerprint of the contract it was judged under.

An invalid contract stops the run before any stage is judged. The result holds one `contract_invalid` violation, no stage results, and no fingerprint, because no verdict was reached and there is nothing to bind. The run does not raise.

The threshold block of the reserved `promotion` gate key is evaluated at run level, and its violations carry no stage. A run-level violation is what the promotion check reads to see that the run did not meet its own floors.

The run-level thresholds count the stages the gate actually evaluated and the evidence the gate actually accepted. The executions the run is made of are built in one place (`gate.py`, `_run_executions`), and an execution contributes only when it exists, is not `None`, and belongs to the stage it is filed under. An execution for a stage the contract does not require, and a misfiled execution, contribute nothing. A `None` execution does not raise.

The run-level value of a metric is not always the sum of the per-stage values, and three shapes exist. `DISTINCT_METRIC_SETS` and `MINIMUM_METRICS` name them, and `gate.py`, `_run_metric_value`, applies them in that order.

The three distinct metrics, `distinct_queries`, `query_families`, and `source_domains`, count queries, families, and hosts, so the run-level value is the size of the union of the per-stage sets. The sum of their counts would count the same query, family, or host once per stage.

The minimum metrics, `neighbors_checked_per_cluster` at present, are a smallest count rather than a total, so the run-level value is the smallest of the run's per-stage values, and zero for a run with no executions. A sum would report a number the run never had: a run whose two stages each checked three neighbours per cluster did not check six neighbours in one cluster, and a run-level minimum on that metric could never fail.

Every other metric counts items, and the sum of the per-stage counts is the run's total.

The run-level counter-evidence minimum falls back to the zero recorded by the stage that serves the `counter_evidence` gate key, and to no other zero (section 4.2).

### 7.4 Promotion evaluation

`evaluate_promotion(contract, run_id, run_result, claims) -> PromotionResult`

The function takes no budget tracker. The budget verdict is read back from the run result, which is where `evaluate_run` wrote it.

Promotion is blocked when:

- the run result belongs to another run. The detail carries the two run ids.
- the run result was judged under another contract. The contract fingerprint is compared, and the detail carries the two fingerprints. An empty fingerprint carries no contract binding, so there is nothing to compare and nothing is blocked. Two results have one: a result that `evaluate_run` did not produce, and a result `evaluate_run` produced from an invalid contract, where no verdict was reached. A promotion against an invalid contract is blocked by `contract_invalid` before the fingerprint is read.
- any required stage is missing, failed, or timed out. A stage that did not pass for another reason blocks promotion only while `require_all_stages_passed` is set.
- the run did not meet its run-level thresholds. Only the run-level violations count here, so no fact is reported twice. The run-level counter-evidence minimum is the one scoped to the stage that serves the `counter_evidence` gate key (section 4.2).
- the `counter_evidence` gate is required, by the hard requirement or by the promotion flag, and did not pass.
- the `competitor_search` gate is required, by the hard requirement or by the promotion flag, and did not pass.
- a claim of kind `no_strong_competitor` exists while the competitor gate did not pass.
- a claim of kind `no_counter_evidence_found` exists while the counter-evidence gate did not pass.
- `require_claim_evidence_links` is set, by the hard requirement or by the promotion flag, and a claim carries no evidence reference. Each such claim also gets a `claim_evidence_missing` violation.
- `require_quality_predicates` is set and a stage failed a quality predicate.
- the run is over its budget. The reason is taken from the `budget_exceeded` violations already in the run result, one reason per dimension, sorted. Those violations were measured against the contract's budgets, so the run result carries the contract's limits and not the tracker's.

Each blocked promotion reports one `promotion_blocked` violation naming the reason.

The claims are read twice, so they are materialised into a tuple first. A one-shot iterator would be empty on the second read, and the claim-evidence rule would be silently satisfied.

### 7.5 What the promotion path does not enforce

This is a known limit of the promotion path, not a promise it makes.

Three of the six hard-requirement flags are enforced only through the stage gate. `require_provenance` is read by `evaluate_stage`, which reports `provenance_missing` and `provenance_mismatch`. `require_qdrant_check` and `require_duckdb_analysis` are read by contract validation, which requires a stage to declare the tool; the stage gate's `tool_missing` check is what proves the tool ran. `evaluate_promotion` reads none of the three.

The promotion check carries a stage failure forward only while `promotion.require_all_stages_passed` is set. A missing, failed, or timed-out stage blocks promotion whatever the flag says, and the counter-evidence gate, the competitor gate, the claim-evidence rule, and the quality-predicate rule are read directly by the promotion check. Everything else a stage gate found, including missing provenance and a qdrant or duckdb tool that never ran, reaches the promotion check only as a stage that did not pass.

With `promotion.require_all_stages_passed: false`, a run whose stage failed on provenance, or on a required tool that never ran, can therefore still be promoted. The shipped contract sets the flag to true, so the shipped baseline is not exposed to this. The run deliberately did not change it: closing the gap would mean reading the three flags at promotion time, and the review that found it recorded the finding without asking for a change.

## 8. Budgets

`BudgetDimension` covers the spec's dimension list: `wall_time_seconds`, `tool_calls_total`, `search_queries`, `fetch_requests`, `browser_minutes`, `crawl_items`, `llm_input_tokens`, `llm_output_tokens`, `model_calls`, `subq_calls`, `embedding_items`, `usd_cost`, `source_requests_per_source`, `saturation_threshold`.

```text
BudgetSpec
  version: str
  per_run: dict[str, float] = {}
  per_stage: dict[str, dict[str, float]] = {}
  per_source: dict[str, float] = {}
  soft_thresholds: dict[str, float] = {}   # fraction of the hard limit
```

The dimension keys are plain names. A `BudgetDimension` enum member is rewritten to its name before the spec is built, so a limit is written the same way whichever way the caller holds the dimension.

`BudgetTracker(spec)` exposes:

- `check(dimension, quantity, stage=None, source_id=None) -> BudgetDecision`: may this call start?
- `guard(dimension, quantity, stage=None, source_id=None)`: raises `BudgetExceeded` when the call would exceed a hard limit
- `record(dimension, quantity, stage=None, source_id=None)`: records spend, raises when it exceeds
- `spent(dimension, stage=None, source_id=None) -> float`
- `exceeded() -> list[str]`: dimension names over their limit, measured against the tracker's own spec
- `exceeded_against(spec) -> list[str]`: dimension names over the limits the given spec declares

The rule from the spec is literal: a call that would exceed a hard limit is not started. `check` returns `allowed=False` with the dimension, the limit, the spend so far, and the requested quantity. The per-stage limit applies on top of the per-run limit; the stricter of the two wins. A per-source limit applies to `source_requests_per_source` when a source id is given.

A budget limit must be greater than zero. The contract validation refuses a limit that is not, and a NaN limit is refused with it (section 4.5, rules 20 to 22). A `BudgetSpec` built by hand, outside the contract, does not refuse a non-positive limit. That is a deliberate boundary: the contract path is the trust boundary, and the tracker is safe when it is built from a contract.

A non-finite amount is refused as well, and the refusal happens before the amount reaches the ledger. Every comparison against a non-finite value is false, so an amount that got in would switch its dimension off. `check` denies it and returns a fixed reason that does not quote the value, and the decision carries no limit, no spend, and no requested quantity, because a non-finite amount has no meaningful request. `record` raises `NonFiniteAmount` and does not record the spend, so the ledger never saw it. `guard` raises `NonFiniteAmount` for the same reason.

`NonFiniteAmount` is a named class and a subclass of `ValueError`. The refusal is not a budget overrun, so it is not a `BudgetExceeded`: a NaN is not a number of calls, and a caller that catches `BudgetExceeded` around `guard` still sees the refusal. It is a `ValueError`, so a caller that already catches one keeps catching it, and a caller that wants to tell a refused amount from an overrun has a class of its own to catch. The package exports it next to `BudgetExceeded`.

The gate reports `budget_exceeded` when the tracker's recorded spend is over the limit the contract declares for a dimension. The contract's budgets are the authority for the limit and the tracker is the ledger for the spend, so the gate measures the overrun with `exceeded_against(contract.budgets)` and not with the tracker's own `exceeded()`. A tracker built from a looser spec cannot widen the budget the contract declares.

The violation names the dimension, not the scope that overran. `exceeded_against` returns dimension names, and the tracker does not attribute an overrun to a scope, so the gate cannot name a culprit stage. Naming the scope needs the tracker to change, and that is a later wave.

## 9. Persistence

The persistence wave shipped. `db/migrations/0003_research_gate.sql` exists, and
`research/persistence.py` defines the protocols that the SQLite implementation in
`product_hunter.db.research_gate` codes against:

```text
ToolCallStore
  append_tool_call(call: ToolCallProvenance, *, attempt: int, source_id: str | None = None) -> None
  list_tool_calls(run_id: str, *, stage: str | None = None) -> list[ToolCallProvenance]

GateResultStore
  record_gate_result(result: GateResult, *, contract_fingerprint: str) -> None
  list_gate_results(run_id: str) -> list[GateResult]
```

The migration creates the two tables the protocols name:

```sql
CREATE TABLE tool_calls (
    tool_call_id  TEXT PRIMARY KEY NOT NULL,
    run_id        TEXT NOT NULL REFERENCES research_runs(run_id),
    stage         TEXT NOT NULL,
    agent         TEXT NOT NULL,
    tool          TEXT NOT NULL,
    purpose       TEXT NOT NULL,
    query_or_operation TEXT NOT NULL,
    result_ref    TEXT,
    result_count  INTEGER NOT NULL DEFAULT 0,
    status        TEXT NOT NULL,
    query_family  TEXT,
    duration_ms   INTEGER,
    error         TEXT,
    occurred_at   TEXT NOT NULL,
    attempt       INTEGER NOT NULL DEFAULT 1,
    source_id     TEXT,
    id_is_declared INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE stage_gate_results (
    gate_result_id TEXT PRIMARY KEY NOT NULL,
    run_id         TEXT NOT NULL REFERENCES research_runs(run_id),
    stage          TEXT,
    passed         INTEGER NOT NULL,
    violations_json TEXT NOT NULL,
    metrics_json   TEXT NOT NULL,
    contract_fingerprint TEXT,
    checked_at     TEXT NOT NULL
);
```

and three more that hold the evidence the gate judges: `research_stage_executions`,
`research_stage_artifacts` and `research_claims`. The number is 0003 because 0002 is
taken by `0002_model_routing.sql`.

`stage_gate_results.stage` is nullable, and the row with no stage holds the verdict
of the run as a whole: the violations the gate found for the run and not for any
stage, which are the run-level thresholds. The gate result id of that row is minted
on its own prefix, so a stage cannot collide with it. `list_gate_results` returns
the per-stage verdicts, and `load_run_result` merges the run-level row back in, so
a verdict read from the database carries the same run-level violations as the
verdict that was computed.

The protocols changed twice. `record_gate_result` takes the fingerprint of the
contract the verdict was judged under, and it is required: a verdict with no
contract behind it is bound to nothing. `append_tool_call` takes the attempt of the
stage that made the call and the source it was made against, and the attempt is
required: a caller states the attempt of the call it files, because a call filed
under an attempt nobody named is filed under the wrong one, and the gate then
judges a re-run stage on the evidence of the attempt before it.

## 10. Shipped baseline

`config/research_contract.yaml` holds the baseline contract. The threshold values come from the spec's `baseline_gate_thresholds` block, and the stage requirements come from the spec's example contract.

The extraction booleans (`quote_verbatim_required`, `source_span_required`) do not become hard requirement flags. They are absent from `hard_requirements` and they arrive with the extraction stage, the wave that can enforce them. The promotion booleans become `promotion.require_claim_evidence_links`, `promotion.require_competitor_gate`, and `promotion.require_counter_evidence_gate`. A boolean in a numeric threshold block has no other home, and the gate reads it from the flag that owns it.

The shipped contract's stages declare the artifact kinds their own thresholds and quality checks read. `discovery` declares `document` for `min_new_documents`, `validation` declares `author`, `community`, and `evidence_span` for its three minima, `clustering` declares `cluster_neighbors_checked` for `min_neighbors_checked_per_cluster` and declares `neighbor_check` and `duplicate_review` for its two quality checks, `investigation` declares `candidate_url` for `min_candidate_urls`, and `counter_evidence` declares the kind its own threshold reads. That alignment is why the shipped baseline is satisfiable, and one test walks every threshold and every quality check to keep it that way.

Budget limits in the baseline are starting values, not measured ones. The spec's own numbers (10 independent authors, 3 communities, 4 validation query families, 3 low-yield batches, 2 retries) are used as they stand.

## 11. Test matrix

All tests are deterministic. No network, no clock dependency beyond injected timestamps, no filesystem beyond pytest's tmp path. The four files under `tests/test_research_*.py` hold 141 tests.

| Area | Cases |
|---|---|
| Contract | The shipped baseline loads and validates, and it declares the artifact kinds its own thresholds and quality checks read. A missing stage contract, a duplicate stage, an empty stage list, a stage contract for a stage that is not required, an unknown quality check, a stage naming the run-level check, a threshold key no stage serves, an unknown threshold metric, a threshold with no minimum, a threshold on an artifact kind the stage does not declare, a quality check on an artifact kind the stage does not declare, an unknown budget dimension, a non-positive limit, a NaN limit, a soft threshold outside (0, 1] or equal to zero, a per-stage budget naming a stage that is not required, a hard requirement with no serving stage, a qdrant requirement with no qdrant stage, a duckdb requirement with no duckdb stage, a required stage with no gating mechanism, a stage serving the reserved `promotion` key, two stages serving the same gate key, and a contract that can never promote are all errors. An invalid contract raises `ContractError` carrying every error. A file that is not UTF-8, a file with broken YAML, and a file whose content does not validate all raise `ContractError`. The fingerprint ignores the order of a list that carries no meaning, and a tighter threshold is a difference. |
| Provenance | Success without `result_ref` is rejected. A failure may carry an empty `result_ref`. A `result_ref` with spaces or with a quote is rejected. A naive timestamp is rejected. A negative `result_count` is rejected. An empty optional string is rejected. An unknown field is refused. A stage execution with naive `started_at` or `finished_at` is rejected and the message names the field. A call round-trips through its own dump. An artifact keeps the tool call it came from. A tool-call store round-trips the calls of a run. |
| Budget | A call over the per-run limit is denied. A call inside the limit is allowed. A dimension with no limit is allowed. The stricter of per-run and per-stage wins. A per-stage limit does not leak into another stage. A per-source limit blocks that source. `record` raises past the limit. `guard` raises before the call starts. `exceeded` names the dimension and is empty while the run is inside its budget. `spent` is tracked per scope. A non-finite amount is denied by `check`, refused by `record` and by `guard` under the named class `NonFiniteAmount`, and the ledger never saw it. The gate measures the overrun against the contract's budgets, so a tracker built from a looser spec cannot widen them. |
| Gate, per determination | A missing stage, a stage that never ran, a stage that has not produced a result, a failed stage, a timed-out stage, a stage that outran its timeout, a stage that cannot prove it finished inside its timeout, a stage that finished before it started, a budget overrun, an invalid artifact, an untraceable artifact, missing provenance, a call from another run, an execution filed under the wrong stage, an artifact filed under the wrong stage, an unmet threshold, a missing required output, a tool that never ran, a failed quality check, and an invalid contract each produce exactly their violation code. The three status violations carry pinned codes, messages, and details. A proven timeout overrun and an elapsed time that cannot be computed carry different messages. |
| Gate, derivation and counting | An artifact with no producing tool call is `artifact_invalid`. An artifact from another stage's tool call is `artifact_invalid`. An artifact may be untraceable when the stage declares no required tools, and the metric is zero for it anyway. A metric does not count an artifact the gate rejected. A misfiled artifact is not counted towards a threshold, and it does not satisfy an artifact-reading quality check. A call filed under another stage counts towards no metric of the stage being judged, and it cannot satisfy a required tool, while the same call still reports `provenance_mismatch` when the contract requires provenance. |
| Gate, counter-evidence | One counter-evidence item satisfies the threshold. A recorded zero with every family completed and searched satisfies it. A recorded zero with a missing family, with no family at all, with families that were never searched, or behind a failed search does not. The run-level threshold accepts the same recorded zero, and only from the stage that serves the `counter_evidence` gate key. |
| Gate, run level | The run-level thresholds count only the stages the gate evaluated, and only the executions the gate accepted. The run-level value of a distinct metric is the union of the per-stage sets, not the sum of their counts. The run-level value of a minimum metric is the smallest of the run's per-stage values, so a sum cannot meet a minimum the run never had. A `None` execution does not crash the run gate. A misfiled failed execution still reports the failure. An invalid contract returns the run's `contract_invalid` violation, no stage results, and no fingerprint, and the run does not raise. The run result carries the fingerprint of the contract it was judged under. |
| Gate, promotion | Promotion passes when every stage passes. It is blocked when the competitor stage is missing, when the counter-evidence stage failed, when a claim has no evidence reference, when a `no_strong_competitor` claim rides on a failed competitor gate, when the run did not meet its run-level thresholds, when the run is over budget, when the run result belongs to another run, and when the run result was judged under another contract. The claim-evidence rule fires on the hard flag and on the promotion flag, and so do the competitor and counter-evidence rules. `require_all_stages_passed: false` still promotes a run whose stage failed. A one-shot claims iterator gives the same answer as a list. |
| Gate, text hygiene | The stage error text and the artifact invalid reason never reach a violation message or detail. The `non_empty_queries` detail and a failed quality-check detail never carry the query text. A whitespace-only query is not a query. One host with trailing whitespace is one source, and one query with trailing whitespace is one query. A malformed fetch URL does not crash the gate. |
| Determinism | The same evidence produces the same violation list, in the same order, twice. Run violations sort by stage, then code. |

## 12. Acceptance criteria

| # | Criterion | Where it is proven |
|---|---|---|
| A1 | The contract schema carries all nine parts the spec names: stages, tools, artifacts, thresholds, predicates, budgets, stopping, retry, promotion | section 4, `tests/test_research_contract.py::test_the_shipped_baseline_loads_and_validates` |
| A2 | Contract validation rejects an inconsistent contract instead of accepting it | section 4.5, the contract errors tests in `tests/test_research_contract.py`, including `test_a_threshold_on_a_kind_the_stage_does_not_declare_is_a_contract_error` and `test_a_quality_check_on_a_kind_the_stage_does_not_declare_is_a_contract_error` |
| A3 | The gate determines all eight listed outcomes with stable codes | section 7.1, the per-determination tests in `tests/test_research_gate.py` |
| A4 | Tool-call provenance carries all ten listed fields and rejects a summary in place of a result reference | section 5.1, `tests/test_research_provenance.py` |
| A5 | A budget overrun blocks the next call, and the gate reports it against the limits the contract declares | section 8, `tests/test_research_budget.py` and the budget tests in `tests/test_research_gate.py`, including `test_a_non_finite_budget_amount_is_refused` and `test_a_non_finite_amount_raises_the_named_refusal` |
| A6 | Promotion is blocked when a mandatory stage failed, timed out, was missing, or produced invalid artifacts | section 7.4, the promotion tests in `tests/test_research_gate.py` |
| A7 | No collector, scrape, or discovery execution is implemented | the package holds no collector, scrape, or network module |
| A8 | SQLite carries the evidence and the verdicts of a run | section 9, `db/migrations/0003_research_gate.sql` and the store tests in `tests/test_db_research_gate.py` |
| A9 | A run result is bound to the contract that judged it, and promotion refuses a result judged under another contract | sections 4.6 and 7.4, `tests/test_research_gate.py::test_a_run_result_judged_under_another_contract_is_not_promoted` and `tests/test_research_contract.py::test_the_contract_fingerprint_ignores_a_list_order_that_changes_nothing` |

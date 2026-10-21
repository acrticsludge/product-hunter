# Research Contract

A Research Contract is defined in spec §6A of
[Product-Hunter-Full-Spec.md](specs/Product-Hunter-Full-Spec.md). This document
describes what the code enforces about research quality, and what the contract
still has to add. [ARCHITECTURE.md](ARCHITECTURE.md) explains the shape of the
system. [MODEL_ROUTING_IMPLEMENTATION.md](MODEL_ROUTING_IMPLEMENTATION.md)
explains model selection.

**Baseline.** This document describes the working tree, not the last commit. At
the fourth stage-8 pass of phase 4, on 2026-10-21, HEAD was `80a98a4`, and the
research package in `src/product_hunter/research/` is in the tree and is still
uncommitted. It is `IMPLEMENTED` like any other code.

## Claim labels

Every factual claim here carries one of four labels.

| Label | Meaning |
|---|---|
| `IMPLEMENTED` | Code exists in this branch. The claim names the file. |
| `PLANNED` | A specification exists and is approved, but no code does. The claim names the spec. |
| `BLOCKED` | Cannot proceed until a precondition is met. The claim names the precondition. |
| `OPTIONAL` | Deliberately excluded from the core, per spec §38. |

The labels apply per claim, not per section.

## The spine: a contract is enforced in code, not in a prompt

Spec §6A makes the point directly. Agent instructions alone are not
enforcement. A rule that lives only in a prompt is a request, not a contract.
Enforcement means the code refuses a result that does not meet the rule,
whatever the model said. Spec §6A pairs the Research Contract with a Tool Gate,
and the package below implements both.

There are now two halves. The research package in
`src/product_hunter/research/` is the larger one and is `IMPLEMENTED`. The
extraction contract is the older one and is still the only place the live
pipeline refuses a model claim.

## The research package (`IMPLEMENTED`)

`src/product_hunter/research/` holds six modules and an `__init__.py`. Nothing
outside the package imports it. There is no CLI command, no worker handler, and
no caller, so the code is complete and reachable as a library and gates nothing
yet.

**The contract schema.** `contract.py` holds `ResearchContract` with nine
fields: `contract_version`, `required_stages`, `stages`, `hard_requirements`,
`evidence_thresholds`, `budgets`, `stopping`, `retry`, and `promotion`. It holds
`StageContract`, `EvidenceThreshold`, `HardRequirements`, `StoppingPolicy`,
`RetryPolicy`, and `PromotionRequirements`. `validate_contract()` returns a list
of errors. `require_valid_contract()` raises `ContractError` carrying every
error. `load_research_contract()` reads `config/research_contract.yaml` and
raises `ContractError` when the file does not validate. A contract that names a
threshold or a quality check on an artifact kind its own stage does not declare
in `required_outputs` is refused, because such a stage could never satisfy the
check it asks for. `contract_fingerprint()`
returns a stable hash of the whole contract, and the contract version alone is
not enough, because two contracts can carry the same version and different
thresholds. The hash is taken over a canonical form of the contract in which
the lists whose order carries no meaning are sorted, so two contracts that
produce identical verdicts fingerprint the same.

**The gate engine.** `gate.py` holds `ViolationCode` with fourteen values:
`stage_missing`, `stage_failed`, `stage_timed_out`, `budget_exceeded`,
`artifact_invalid`, `provenance_missing`, `evidence_threshold_unmet`,
`promotion_blocked`, `artifact_missing`, `tool_missing`, `quality_check_failed`,
`provenance_mismatch`, `contract_invalid`, and `claim_evidence_missing`.
`evaluate_stage()`, `evaluate_run()`, and `evaluate_promotion()` return results
and never raise for a failed gate. Only an invalid contract yields
`contract_invalid`, and an invalid contract is returned as that violation
rather than raised. A run result carries the fingerprint of the contract it was
judged under, and promotion refuses a result that was judged under another
contract. A run judged under an invalid contract reaches no verdict and carries
no fingerprint.

**The metrics and predicates.** `predicates.py` holds twelve metrics and eight
predicates. Their input is `PredicateContext` of `run_id`, `execution`, and
`claims`. A metric that cannot be computed returns zero, and a missing artifact
is never read as a satisfied threshold. A metric counts an artifact only when
the artifact is valid, belongs to the stage being judged, and traces to a
successful tool call of that stage, and the two artifact-reading quality
predicates go through the same rule, so an artifact filed under another stage
neither counts towards a threshold nor satisfies a quality check. A call filed
under another stage is not that stage's evidence either. One helper,
`stage_calls`, yields the calls whose own stage field equals the execution's
stage, and the three distinct metric sets, `non_empty_queries`, the recorded
zero check, and the gate's required-tool check all read through it, so a
foreign call satisfies no threshold and no required tool. Three of the metrics,
`distinct_queries`, `query_families`, and `source_domains`, count distinct
values rather than items, and the run level takes the union of their sets
instead of the sum of their counts, so a host three stages fetched counts once.
One metric, `neighbors_checked_per_cluster`, is a minimum over what it counts,
and the run level takes the smallest of the run's stage values instead of their
sum, so a run-level minimum on it can fail.

**The budget model.** `budget.py` holds `BudgetDimension` with fourteen values,
`BudgetSpec`, `BudgetDecision`, `BudgetExceeded`, `NonFiniteAmount`, and
`BudgetTracker`. `check()` returns a decision, `guard()` raises before the
call, and `record()` records and raises past the limit. The rule from spec §22
is literal: a call that would exceed a hard limit is not started. The gate
measures an overrun against the budget the contract declares, so a tracker
built from a looser budget cannot widen the limit. A non-finite amount is
refused before it reaches the ledger: `check` denies it, `record` does not
record it, and `guard` raises, because every comparison against a non-finite
value is false and the dimension would switch itself off. `record` and `guard`
raise the named class `NonFiniteAmount`, which subclasses `ValueError`, so a
caller can tell a refused amount from an overrun, and a caller that already
catches `ValueError` keeps catching it. The violation names the dimension that
went over, not the stage that went over.

**Tool-call provenance.** `evidence.py` holds `ToolCallStatus` (`success`,
`failure`, `timeout`, `partial`), `StageStatus`, `ClaimKind`
(`no_strong_competitor`, `no_counter_evidence_found`, `opportunity_exists`),
`ToolCallProvenance`, `ArtifactRecord`, `ZeroResultOutcome`, `StageExecution`,
`ResearchClaim`, and `RunEvidence`. A `result_ref` must match
`^[A-Za-z0-9._:/-]+$`, so a hand-written summary cannot be passed off as the
original tool result, and a `timestamp` must be timezone-aware.

**The shipped contract.** `config/research_contract.yaml` is
`contract_version: phase4-v1` with eight required stages, per-stage tools,
outputs, quality checks, and gate keys, hard requirements on, thresholds for
five gate areas, a budget with twelve per-run dimensions and per-source limits,
and stopping, retry, and promotion policies. It loads and validates. Each
stage declares the artifact kinds its own thresholds and quality checks read,
so the baseline is satisfiable, and one test walks the file to keep it that
way.

**The persistence is protocols only.** `persistence.py` holds the
`ToolCallStore` and `GateResultStore` protocols and no SQLite. Its docstring
names a planned migration `db/migrations/0003_research_gate.sql`. The number is
0003 because `0002` is taken by `0002_model_routing.sql`. The file does not
exist.

## The extraction contract (`IMPLEMENTED`)

The extraction pipeline is the one place where the system currently refuses to
accept a model claim, and that refusal is the proof the pattern works. The
enforcement lives in `src/product_hunter/extraction.py`,
`src/product_hunter/quote.py`, `src/product_hunter/normalization.py`, and
`src/product_hunter/documents.py`.

**A strict schema.** The model returns candidates against a pydantic schema.
Pain validity is one of `genuine`, `weak`, or `not_pain`, and the schema holds
the model to it. A `genuine` candidate needs a named actor, a named workflow, a
concrete cost, a severity from 2 to 5, and a reason that carries a cost. A
`weak` candidate needs severity 1 or 2. A `not_pain` candidate needs severity 0.

**A quote-verbatim rule.** A candidate is stored only when its quote is an
exact span inside the chunk the model was shown. `quote.py` locates the quote
and records the match mode, and the span must trace back to the document, the
fetch, and the raw artifact.

**Fail-closed rejection codes.** A candidate that fails any check becomes a
rejection with a stable code in the run's `rejects.jsonl`. The extraction
rejection vocabulary is `budget_exceeded`, `prompt_too_large`, `provider_error`,
`invalid_json`, `schema_invalid`, `quote_too_short`, `quote_too_long`,
`quote_not_found`, `quote_outside_chunk`, `normalization_invalid`, and
`provider_check_failed`. A candidate that passes the quote check but cannot
build a normalized key is rejected with `normalization_invalid` rather than
stored without a key.

**Deterministic identifiers.** `span_id` is `ev_` plus 16 hex characters of the
SHA-256 of `doc_id|char_start|char_end|quote`. `candidate_id` adds the extractor
version. `pain_id` is `pp_` plus 16 hex characters of the SHA-256 of
`span_id|extractor_version`. The same span and the same extractor version always
produce the same ids.

**A normalized key with a version.** A `genuine` candidate carries five
controlled fields: `domain`, `actor`, `action`, `object`, and `consequence`.
The domain is one of 15 fixed values. `normalize_fields()` in
`src/product_hunter/normalization.py` builds a `normalized_key` of the form
`domain:slug(action):slug(object)`, capped at 200 characters, and it travels
with `normalization_version` (`norm-v1`). `pain_id` stays span-based, so the key
groups exact wording without forking a record. The stored record re-derives its
own key from its five fields and refuses a record whose `normalized_key` does
not match, so a tampered key cannot be stored or read back.

**The limit of the key.** The key is exact grouping only. Two phrases whose
slugs share their first 48 characters collide, so the key is a grouping, not a
unique identifier. Semantic clustering is a later phase. The 15 domain values
are fixed until a deliberate vocabulary change and a new normalization version.

## The honest limit

Nothing outside the research package imports it. No CLI command calls it, there
is no worker handler, and there is no caller, so the gate is complete and
reachable as a library and gates nothing yet. The persistence layer is
protocols only.

Three of the six hard-requirement flags are enforced through the stage gate
only. `require_provenance` is read by the stage check. `require_qdrant_check`
and `require_duckdb_analysis` are read by contract validation, which requires
a stage to declare the tool, and the stage's `tool_missing` check is what
proves the tool ran. The promotion check reads none of the three, and it
carries a stage failure forward only while `promotion.require_all_stages_passed`
is set. With that flag false, a run whose stage failed on missing provenance,
or on a qdrant or duckdb tool that never ran, can still be promoted. The
shipped contract sets the flag to true, so the shipped baseline is not exposed
to it. This is a known limit of the promotion path, and it is recorded as one.

## What is planned (`PLANNED`)

Two pieces remain. The first is persisting tool calls and gate results in
SQLite, which needs the migration that `persistence.py` names. The second is
wiring the gate into a stage so that it decides something, rather than being
reachable only from a caller that a human has to write.

## The baseline thresholds (`PLANNED`)

Spec §6A ships a `baseline_gate_thresholds` block. The thresholds are keyed by
gate area rather than by stage name, and each gate area carries named integer
minima. The areas are `discovery`, `competitor_search`, `validation`,
`counter_evidence`, `extraction`, `clustering`, and `promotion`. `extraction`
carries two booleans rather than numbers, `quote_verbatim_required` and
`source_span_required`, and `promotion` carries `require_claim_evidence_links`,
`require_competitor_gate`, and `require_counter_evidence_gate`.

One threshold has special handling. `min_counter_evidence_items_or_explicit_zero`
is satisfied by at least that many counter-evidence items, or by a recorded
zero-result outcome in which every required query family completed. A failed or
unsearched query never counts as zero. The stage-level check reads the zero the
stage being judged recorded. The run-level check reads the zero recorded by the
stage that serves the `counter_evidence` gate key and no other zero, so a zero
from another stage cannot satisfy the run-level minimum.

The shape comes from spec §6A, and the values now live in
`config/research_contract.yaml` (`IMPLEMENTED`), where the gate reads them. The
specification block itself is `PLANNED` in the sense that its numbers are a
design baseline and have not been validated against a labelled corpus.

## The rule that ties the halves together

A gate validates results and derivation, not activity. Spec §6A states it: a
tool-call log such as `search -> result_count=27` is not sufficient evidence
that validation happened. The gate checks that the required tool actually ran,
that the execution belongs to the current run and stage, that an artifact of a
required output kind belongs to the stage being judged, that a produced
artifact references the result, that the artifact was produced by the worker
from that result, that the schemas validate, that the thresholds are met, that
the provenance links are intact, that counter-evidence was considered where
required, and that the stage stayed inside its budget.

That is why the extraction contract is the enforcement that runs today and the
gate is not. The code refuses a candidate whose quote is not in the source. The
gate refuses a stage for skipping a tool, and it refuses a run whose result was
judged under another contract, but nothing calls either one on a live stage
yet.

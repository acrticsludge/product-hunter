# Phase 4 Wave 1 Implementation Plan: Research Contract and Tool Gate

**Status:** approved.
**Date:** 2026-10-21
**Design of record:** `docs/reasonix/specs/phase-4-research-contract.md`
**Handoff:** `docs/reasonix/handoffs/phase-4-research-contract-handoff.md`
**Lane:** backend. No UI, no `DESIGN.md`.

---

## 1. Build order

The modules depend on each other in one direction, so the order below compiles at every step.

| # | File | Depends on | Delivers |
|---|---|---|---|
| 1 | `src/product_hunter/research/evidence.py` | nothing | `ToolCallProvenance`, `ArtifactRecord`, `ZeroResultOutcome`, `StageExecution`, `ResearchClaim`, `RunEvidence`, and the status enums |
| 2 | `src/product_hunter/research/budget.py` | nothing | `BudgetDimension`, `BudgetSpec`, `BudgetDecision`, `BudgetTracker`, `BudgetExceeded` |
| 3 | `src/product_hunter/research/predicates.py` | `evidence` | the metric registry and the predicate registry, plus `METRIC_NAMES` and `PREDICATE_NAMES` |
| 4 | `src/product_hunter/research/contract.py` | `predicates`, `budget` | `StageContract`, `EvidenceThreshold`, `HardRequirements`, `StoppingPolicy`, `RetryPolicy`, `PromotionRequirements`, `ResearchContract`, `ContractError`, `validate_contract`, `load_research_contract` |
| 5 | `src/product_hunter/research/gate.py` | all of the above | `ViolationCode`, `Violation`, `GateResult`, `RunGateResult`, `PromotionResult`, `evaluate_stage`, `evaluate_run`, `evaluate_promotion` |
| 6 | `src/product_hunter/research/persistence.py` | `evidence`, `gate` | `ToolCallStore` and `GateResultStore` protocols, migration plan in the docstring |
| 7 | `src/product_hunter/research/__init__.py` | all | public exports |
| 8 | `config/research_contract.yaml` | `contract` | the shipped baseline |
| 9 | `tests/test_research_provenance.py` | 1 | provenance validation and round-trip |
| 10 | `tests/test_research_budget.py` | 2 | limit decisions, per-stage and per-source precedence |
| 11 | `tests/test_research_contract.py` | 3, 4, 8 | validation rules, baseline loads clean |
| 12 | `tests/test_research_gate.py` | 5 | one test per violation code, derivation checks, promotion rules, determinism |

## 2. Rules that the implementation must not break

1. A gate never raises for a failed gate. It returns a result with violations. Only an invalid contract raises.
2. A missing artifact reads as zero, never as a satisfied threshold.
3. Every violation carries a code from `ViolationCode`. Free-text status strings do not exist.
4. Violation order is stable: stage, then code, then message.
5. The gate reads the contract and the evidence. It never calls a tool, never touches the network, and never writes a file.
6. No stage execution, collector, or pipeline code lands in this wave.

## 3. Baseline contract

`config/research_contract.yaml` carries the spec's `baseline_gate_thresholds` values and the spec's example stage requirements. Two threshold blocks move to the flags that own them: the extraction booleans become `hard_requirements.require_quote_verbatim` and `require_source_span`, the promotion booleans become `promotion.require_claim_evidence_links`, `require_competitor_gate`, and `require_counter_evidence_gate`.

The baseline must pass `validate_contract` with zero errors. A test asserts that, so the shipped config cannot drift from the rules.

## 4. Test plan

The matrix lives in the spec, section 11. Two properties matter beyond the individual cases:

- **Determinism.** The same evidence produces the same violations in the same order, twice over. No wall-clock reads, no set iteration order, no dict ordering assumptions.
- **Fail closed.** Every negative case asserts a specific code, not just that something failed. A test that accepts any failure proves nothing.

## 5. Commands

```text
uv run pytest tests/test_research_contract.py -q
uv run pytest tests/test_research_gate.py -q
uv run pytest
```

The full suite is the gate. Existing tests must stay green, since this wave adds modules rather than changing them.

## 6. What the reviewer should look at

- Whether a stage can pass without a tool call producing its artifact. That is the whole point of the wave.
- Whether a missing artifact can be read as a satisfied threshold anywhere.
- Whether the counter-evidence zero rule accepts a failed search.
- Whether the promotion gate can be passed with a missing stage.
- Whether the budget check happens before a call starts, not after it finishes.
- Whether any new abstraction is doing work the spec did not ask for.

## 7. Out of scope

Discovery, collection, scraping, competitor research, counter-evidence execution, scoring, confidence, the SQLite migration for tool calls, the OpenCode plugin, and the CLI. The protocols in `persistence.py` name the shape of the SQLite wave that comes next, and nothing more.

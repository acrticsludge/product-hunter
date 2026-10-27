# Handoff: wave 2 session 6, opportunity scoring and validation

**Gathered:** 2026-10-21
**Source:** read-only gather node over the wave2/scoring-validation worktree
**Purpose:** the interop contract the implementer needs, so it does not
re-discover the repo's shapes.

## 1. `src/product_hunter/extraction.py`

Enums (str, Enum), string values identical to member names:

- `PainValidity`: `genuine`, `weak`, `not_pain`.
- `PainReason`: `recurring_manual_work`, `time_cost`, `money_cost`,
  `error_or_delay`, `compliance_or_risk`, `negative_opinion`, `question`,
  `feature_request`, `informational`, `incidental`, `other`.
- `Frequency`: `daily`, `weekly`, `monthly`, `occasional`, `one_off`, `unknown`.

`Frequency` is the enum the opportunity package should reuse for workflow
frequency. Do not define a second one.

`Candidate` is `ConfigDict(strict=True, extra="forbid")` with `quote`,
`pain_validity`, `reason`, `pain_statement` (1-500), `severity` (0-5),
`frequency`, `confidence` (0-1), `rationale` (1-500), and the optional
`domain`/`actor`/`action`/`object`/`consequence` fields.

The severity scale, from the extraction prompt:

```text
0 none. 1 minor or incidental. 2 occasional real cost.
3 recurring manual work with a meaningful time cost.
4 several hours a week, an expensive workaround, or a repeated operational failure.
5 daily hours, revenue loss, compliance or legal risk, or safety risk.
```

## 2. `src/product_hunter/normalization.py`

`DEFAULT_NORMALIZATION_VERSION = "norm-v1"`. `NormalizedProblem` holds
`domain`, `actor`, `action`, `object`, `consequence`, `normalized_key`,
`normalization_version`. `normalize_fields()` is keyword-only and raises
`NormalizationError` (reason code `normalization_invalid`).

## 3. `src/product_hunter/research/evidence.py`

`ToolCallProvenance`: run_id, stage, agent, tool, purpose, timestamp (tz-aware),
query_or_operation, result_ref, result_count, status, tool_call_id,
query_family, duration_ms, error.

`ArtifactRecord`: artifact_id, stage, kind, ref, item_count, content_hash,
schema_version, valid, invalid_reason, produced_by_tool_call_id, created_at.

`StageExecution`: stage, status, attempt, started_at, finished_at, tool_calls,
artifacts, zero_result, error.

`ResearchClaim`: claim_id, kind, statement, stage, evidence_refs.
`RunEvidence`: run_id, contract_version, stages, claims.

`ClaimKind`: `no_strong_competitor`, `no_counter_evidence_found`,
`opportunity_exists`.

## 4. `src/product_hunter/research/contract.py`

`CONTRACT_NAME = Path("config") / "research_contract.yaml"`.
`PROMOTION_GATE_KEY = "promotion"`. `ContractError(errors: list[str])` carries
`.errors`.

```text
validate_contract(contract) -> list[str]
require_valid_contract(contract) -> ResearchContract
load_research_contract(path: str | Path | None = None) -> ResearchContract
```

The loader resolves `repo_root() / CONTRACT_NAME` when path is None, unwraps a
top-level `research_contract` key when the file has one, and raises
`ContractError` with one message per problem.

## 5. `src/product_hunter/research/predicates.py`

Artifact kinds: `document`, `candidate_url`, `author`, `community`,
`evidence_span`, `counter_evidence`, `cluster_neighbors_checked`,
`neighbor_check`, `duplicate_review`.

Metric keys: `distinct_queries`, `query_families`, `source_domains`,
`new_documents`, `candidate_urls`, `independent_authors`,
`independent_communities`, `evidence_spans`, `counter_evidence_items`,
`neighbors_checked_per_cluster`, `tool_calls`, `successful_tool_calls`.

Predicate keys: `non_empty_queries`, `source_diversity`,
`nearest_neighbor_checked`, `duplicate_candidates_reviewed`,
`counter_evidence_present_or_explicit_zero`, `provenance_complete`,
`artifacts_valid`, `claim_evidence_links`.

## 6. `src/product_hunter/config.py`

`repo_root() -> Path` walks up until it finds `pyproject.toml`.
`load_config(path: Path | None = None) -> ExtractionConfig` reads
`repo_root() / CONFIG_NAME`, where `CONFIG_NAME = Path("config") / "extraction.yaml"`.
`ROUTING_CONFIG_NAME = Path("config") / "model_routing.yaml"`.

The house pattern for a YAML config: a module constant holding the relative
path, `yaml.safe_load(path.read_text(encoding="utf-8"))`, then
`Model.model_validate(raw)`.

## 7. YAML house style

`config/extraction.yaml` is flat with one nested `limits` block.
`config/model_routing.yaml` is flat with a nested `catalog` block and a
`roles` map. `config/research_contract.yaml` is flat with nested `stages`,
`hard_requirements`, `evidence_thresholds`, `budgets`, `stopping`, `retry`,
`promotion` blocks. A leading comment block explains where the numbers came
from.

## 8. `tests/conftest.py`

No `@pytest.fixture` functions. Module constants: `FIXTURES`,
`NOW = "2026-10-21T00:00:00+00:00"`, `EXTRACTOR = "phase1-0.3"`,
`PROMPT = "extract-v3"`, `NORMALIZATION = "norm-v1"`.

Helpers: `_fixture_fetch(tmp_path)`, `_candidate(document, *, quote, accepted)`,
`_reject(document)`, `_call(document)`, `_write_jsonl(path, rows)`,
`_build_run(tmp_path)`, `open_store(db_path)`, `crash_child(...)`.

House test style: plain `def test_*` functions, no `class Test*`, heavy
`@pytest.mark.parametrize`, fakes as helper classes, assert-based checks.
Test files are named `test_<module>.py`.

## 9. Database state

Twenty-three tables: `sources`, `fetches`, `communities`, `documents`,
`authors`, `author_links`, `evidence`, `pain_points`, `evidence_pain_links`,
`propagation_edges`, `research_runs`, `research_artifacts`, `research_events`,
`research_checkpoints`, `jobs`, `stage_attempts`, `projection_tasks`,
`spend_ledger`, `human_decisions`, `model_catalogs`, `model_inventory`,
`model_role_bindings`, `model_executions`.

`opportunities` DOES NOT EXIST. `clusters` DOES NOT EXIST.
`identity_confidence` DOES NOT EXIST. `author_identity` DOES NOT EXIST.

Partial pieces that do exist: `authors` and `author_links` (the link carries a
`confidence` column, defaulting to 1.0 for a declared link), `communities`,
`pain_points` (grouped by `normalized_key`), `propagation_edges` (no writer),
`evidence` and `evidence_pain_links`.

## 10. `docs/reasonix/ARCHITECTURE.md`

Claim labels: `IMPLEMENTED`, `PLANNED`, `BLOCKED`, `OPTIONAL`, applied per
claim rather than per section. The document already lists "Opportunity states
and scoring" (spec sections 20 and 23) as `PLANNED`, and that line needs
updating once this wave lands. There is no component-index convention in that
document.

## Integration gaps

Every input the scoring engine needs that the repo cannot yet supply:

- **Author identity confidence.** Only `author_links.confidence` exists, and
  it defaults to 1.0 for a declared link. A table or column holding the
  resolved cross-source identity confidence is missing.
- **Cluster membership.** No `clusters` table and no cluster-assignment
  writer. The Qdrant artifact kinds and `propagation_edges` are placeholders.
- **Competition results.** No table. The `competitor_search` and
  `investigation` gate keys exist in the contract with no persistence behind
  them.
- **Market estimates.** No table.
- **Opportunity scores and confidence.** No `opportunities` table, even though
  the contract's `scoring` stage declares `opportunity_score` and `confidence`
  as required outputs.
- **Tool-call and gate-result storage.** The planned migration
  `0003_research_gate.sql` has not shipped; `research/persistence.py` holds
  protocols only.

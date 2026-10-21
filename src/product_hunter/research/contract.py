"""The research contract: what a research run must do, and the check that it can.

An invalid contract cannot be evaluated. Validation fails closed and returns
one human-readable error per problem, so a broken contract is a report, not a
silent pass.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from product_hunter.config import repo_root
from product_hunter.research.budget import BUDGET_DIMENSION_NAMES, BudgetSpec
from product_hunter.research.predicates import (
    METRIC_ARTIFACT_KINDS,
    METRIC_NAMES,
    PREDICATE_ARTIFACT_KINDS,
    PREDICATE_NAMES,
)

CONTRACT_NAME = Path("config") / "research_contract.yaml"

# The gate key that is evaluated at run level; no stage serves it.
PROMOTION_GATE_KEY = "promotion"

# The counter-evidence minimum accepts items or a recorded zero, so it is
# checked by predicate and not by a plain metric comparison.
COUNTER_EVIDENCE_THRESHOLD = "min_counter_evidence_items_or_explicit_zero"

# The claim_evidence_links predicate reads the run's claims, so it is a
# run-level check: a stage has no claims for it to read.
RUN_LEVEL_QUALITY_CHECK = "claim_evidence_links"

# Every threshold field names the metric it counts.
THRESHOLD_METRICS: dict[str, str] = {
    "min_distinct_queries": "distinct_queries",
    "min_source_domains": "source_domains",
    "min_new_documents": "new_documents",
    "min_query_families": "query_families",
    "min_candidate_urls": "candidate_urls",
    "min_independent_authors": "independent_authors",
    "min_independent_communities": "independent_communities",
    "min_evidence_spans": "evidence_spans",
    COUNTER_EVIDENCE_THRESHOLD: "counter_evidence_items",
    "min_neighbors_checked_per_cluster": "neighbors_checked_per_cluster",
}


class ContractError(Exception):
    """The research contract is invalid and cannot be evaluated."""

    def __init__(self, errors: list[str]) -> None:
        self.errors = list(errors)
        super().__init__("; ".join(self.errors) or "the research contract is invalid")


class StageContract(BaseModel):
    """What one stage must use, produce, and prove."""

    model_config = ConfigDict(extra="forbid")

    required_tools: list[str]
    required_outputs: list[str]
    quality_checks: list[str]
    gate_key: str | None = None
    timeout_seconds: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _check_invariants(self) -> StageContract:
        for name, values in (
            ("required_tools", self.required_tools),
            ("required_outputs", self.required_outputs),
            ("quality_checks", self.quality_checks),
        ):
            if any(not value for value in values):
                raise ValueError(f"{name} must not hold an empty name")
        return self


class EvidenceThreshold(BaseModel):
    """Named integer minima for one gate key. Every field is optional."""

    model_config = ConfigDict(extra="allow")

    min_distinct_queries: int | None = Field(default=None, ge=0)
    min_source_domains: int | None = Field(default=None, ge=0)
    min_new_documents: int | None = Field(default=None, ge=0)
    min_query_families: int | None = Field(default=None, ge=0)
    min_candidate_urls: int | None = Field(default=None, ge=0)
    min_independent_authors: int | None = Field(default=None, ge=0)
    min_independent_communities: int | None = Field(default=None, ge=0)
    min_evidence_spans: int | None = Field(default=None, ge=0)
    min_counter_evidence_items_or_explicit_zero: int | None = Field(default=None, ge=0)
    min_neighbors_checked_per_cluster: int | None = Field(default=None, ge=0)


class HardRequirements(BaseModel):
    """The evidence the run may not exist without.

    The extraction booleans of the spec are not here: no stage in this wave
    can enforce a verbatim quote or a source span, so they arrive with the
    extraction stage.
    """

    model_config = ConfigDict(extra="forbid")

    require_provenance: bool = True
    require_counter_evidence: bool = True
    require_competitor_search: bool = True
    require_qdrant_check: bool = True
    require_duckdb_analysis: bool = True
    require_claim_evidence_links: bool = True


class StoppingPolicy(BaseModel):
    """When the run has enough evidence to stop expanding the frontier."""

    model_config = ConfigDict(extra="forbid")

    min_independent_authors: int = Field(default=10, ge=0)
    min_independent_communities: int = Field(default=3, ge=0)
    min_validation_query_families: int = Field(default=4, ge=0)
    max_consecutive_low_yield_batches: int = Field(default=3, ge=0)
    saturation_threshold: int = Field(default=0, ge=0)


class RetryPolicy(BaseModel):
    """How often a stage attempt is retried, and for what."""

    model_config = ConfigDict(extra="forbid")

    max_retries: int = Field(default=2, ge=0)
    backoff_seconds: float = Field(default=5.0, gt=0)
    retry_on: list[str] = Field(default_factory=lambda: ["transient", "timeout"])
    give_up_after_seconds: int | None = Field(default=None, ge=0)

    @property
    def max_attempts(self) -> int:
        """One attempt plus the retries."""
        return self.max_retries + 1


class PromotionRequirements(BaseModel):
    """What a run must have shown before its result may be promoted."""

    model_config = ConfigDict(extra="forbid")

    require_all_stages_passed: bool = True
    require_competitor_gate: bool = True
    require_counter_evidence_gate: bool = True
    require_claim_evidence_links: bool = True
    require_quality_predicates: bool = True


class ResearchContract(BaseModel):
    """The whole contract: stages, thresholds, budgets, stopping, retry, promotion."""

    model_config = ConfigDict(extra="forbid")

    contract_version: str = Field(min_length=1)
    required_stages: list[str]
    stages: dict[str, StageContract] = Field(default_factory=dict)
    hard_requirements: HardRequirements = Field(default_factory=HardRequirements)
    evidence_thresholds: dict[str, EvidenceThreshold] = Field(default_factory=dict)
    budgets: BudgetSpec
    stopping: StoppingPolicy = Field(default_factory=StoppingPolicy)
    retry: RetryPolicy = Field(default_factory=RetryPolicy)
    promotion: PromotionRequirements = Field(default_factory=PromotionRequirements)

    @model_validator(mode="after")
    def _check_invariants(self) -> ResearchContract:
        if any(not stage for stage in self.required_stages):
            raise ValueError("required_stages must not hold an empty stage name")
        return self


def resolved_gate_key(contract: ResearchContract, stage: str) -> str:
    """Return the gate key a stage serves: its own key, or the stage name."""
    spec = contract.stages.get(stage)
    if spec is None or not spec.gate_key:
        return stage
    return spec.gate_key


def served_gate_keys(contract: ResearchContract) -> dict[str, str]:
    """Map every gate key a stage serves to that stage."""
    return {
        resolved_gate_key(contract, stage): stage for stage in contract.required_stages
    }


def threshold_fields(threshold: EvidenceThreshold) -> dict[str, int]:
    """Return the minimums a threshold sets, extras included."""
    return {
        name: value
        for name, value in threshold.model_dump().items()
        if value is not None
    }


def _canonical_payload(contract: ResearchContract) -> dict[str, object]:
    """Return the contract in the canonical form the fingerprint hashes.

    The lists whose order carries no meaning are sorted, so two contracts
    that produce identical verdicts fingerprint the same. Nothing whose order
    carries meaning is sorted: the threshold blocks are mappings, and their
    order is already stable under the sorted dump.
    """
    payload = contract.model_dump(mode="json")
    payload["required_stages"] = sorted(payload["required_stages"])
    for stage in payload["stages"].values():
        for name in ("required_tools", "required_outputs", "quality_checks"):
            stage[name] = sorted(stage[name])
    payload["retry"]["retry_on"] = sorted(payload["retry"]["retry_on"])
    return payload


def contract_fingerprint(contract: ResearchContract) -> str:
    """Return a stable fingerprint of the whole contract.

    The contract version is not enough: two contracts can carry the same
    version and different thresholds. The fingerprint is taken over the entire
    contract, so a result can be bound to the contract that judged it.
    """
    payload = json.dumps(_canonical_payload(contract), sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def validate_contract(contract: ResearchContract) -> list[str]:
    """Return one error per problem in the contract; an empty list means valid."""
    errors: list[str] = []

    required = contract.required_stages
    if not required:
        errors.append("required_stages is empty")
    duplicates = sorted({stage for stage in required if required.count(stage) > 1})
    if duplicates:
        errors.append(f"required_stages has duplicates: {', '.join(duplicates)}")

    for stage in required:
        if stage not in contract.stages:
            errors.append(f"required stage {stage!r} has no stage contract")
    for stage in contract.stages:
        if stage not in required:
            errors.append(f"stage contract {stage!r} is not a required stage")

    served = served_gate_keys(contract)
    owners: dict[str, str] = {}
    for stage in required:
        key = resolved_gate_key(contract, stage)
        if key == PROMOTION_GATE_KEY:
            errors.append(
                f"stage {stage!r} may not serve the reserved gate key {key!r}: "
                "no stage serves it"
            )
        if key in owners:
            errors.append(
                f"stages {owners[key]!r} and {stage!r} resolve to the same gate key {key!r}"
            )
        else:
            owners[key] = stage

    for stage, spec in contract.stages.items():
        for check in spec.quality_checks:
            if check not in PREDICATE_NAMES:
                errors.append(f"stage {stage!r} names an unknown quality check {check!r}")
            elif check == RUN_LEVEL_QUALITY_CHECK:
                errors.append(
                    f"stage {stage!r} names the run-level quality check {check!r}; "
                    "ask for it with hard_requirements.require_claim_evidence_links "
                    "or promotion.require_claim_evidence_links"
                )
            else:
                # A check that reads an artifact kind can only be satisfied by
                # an artifact of that kind, so the stage has to declare the
                # kind among its outputs.
                kind = PREDICATE_ARTIFACT_KINDS.get(check)
                if kind is not None and kind not in spec.required_outputs:
                    errors.append(
                        f"stage {stage!r} names the quality check {check!r}, which "
                        f"reads the artifact kind {kind!r}, which it does not "
                        "declare in required_outputs"
                    )

    for key, threshold in contract.evidence_thresholds.items():
        if key not in served and key != PROMOTION_GATE_KEY:
            errors.append(f"threshold key {key!r} is not a gate key any stage serves")
        fields = threshold_fields(threshold)
        if not fields:
            errors.append(f"threshold {key!r} sets no minimum")
        # The stage that serves this gate key, if any: a threshold reads the
        # artifacts of the stage that owns the key, so the kind it names has
        # to be one that stage declares as an output. A stage with no
        # contract of its own is already an error, so it is skipped here.
        owner = served.get(key)
        owner_spec = contract.stages.get(owner) if owner is not None else None
        required_outputs = (
            owner_spec.required_outputs if owner_spec is not None else []
        )
        for name in fields:
            metric = THRESHOLD_METRICS.get(name)
            if metric is None:
                errors.append(f"threshold {key!r} names an unknown metric {name!r}")
            elif metric not in METRIC_NAMES:
                errors.append(
                    f"threshold {key!r} field {name!r} maps to the unknown metric {metric!r}"
                )
            else:
                kind = METRIC_ARTIFACT_KINDS.get(metric)
                if (
                    kind is not None
                    and owner_spec is not None
                    and kind not in required_outputs
                ):
                    errors.append(
                        f"stage {owner!r} sets a threshold on the artifact kind {kind!r}, "
                        "which it does not declare in required_outputs"
                    )

    for stage in required:
        spec = contract.stages.get(stage)
        if spec is None:
            continue
        key = resolved_gate_key(contract, stage)
        if (
            not spec.required_tools
            and not spec.required_outputs
            and not spec.quality_checks
            and key not in contract.evidence_thresholds
        ):
            errors.append(
                f"stage {stage!r} has no gating mechanism: it needs required tools, "
                "required outputs, quality checks, or a threshold block"
            )

    for name in contract.budgets.per_run:
        if name not in BUDGET_DIMENSION_NAMES:
            errors.append(f"per_run budget names the unknown dimension {name!r}")
    for name in contract.budgets.soft_thresholds:
        if name not in BUDGET_DIMENSION_NAMES:
            errors.append(f"soft threshold names the unknown dimension {name!r}")
    for stage, limits in contract.budgets.per_stage.items():
        if stage not in required:
            errors.append(f"per_stage budget names a stage that is not required: {stage!r}")
        for name in limits:
            if name not in BUDGET_DIMENSION_NAMES:
                errors.append(
                    f"per_stage budget for {stage!r} names the unknown dimension {name!r}"
                )

    for name, limit in contract.budgets.per_run.items():
        # Not "limit <= 0": every comparison against NaN is false, so a NaN
        # limit would pass the check and turn the dimension off.
        if not limit > 0:
            errors.append(f"per_run budget limit for {name!r} must be positive")
    for stage, limits in contract.budgets.per_stage.items():
        for name, limit in limits.items():
            if not limit > 0:
                errors.append(
                    f"per_stage budget limit for {stage!r} and {name!r} must be positive"
                )
    for name, limit in contract.budgets.per_source.items():
        if not limit > 0:
            errors.append(f"per_source budget limit for {name!r} must be positive")
    for name, fraction in contract.budgets.soft_thresholds.items():
        if not 0 < fraction <= 1:
            errors.append(f"soft threshold for {name!r} must be in (0, 1]")

    if contract.hard_requirements.require_counter_evidence and "counter_evidence" not in served:
        errors.append(
            "require_counter_evidence needs a stage that serves the 'counter_evidence' gate key"
        )
    if (
        contract.hard_requirements.require_competitor_search
        and "competitor_search" not in served
    ):
        errors.append(
            "require_competitor_search needs a stage that serves the 'competitor_search' gate key"
        )
    if (
        contract.promotion.require_counter_evidence_gate
        and "counter_evidence" not in served
    ):
        errors.append(
            "promotion.require_counter_evidence_gate needs a stage that serves "
            "the 'counter_evidence' gate key"
        )
    if (
        contract.promotion.require_competitor_gate
        and "competitor_search" not in served
    ):
        errors.append(
            "promotion.require_competitor_gate needs a stage that serves "
            "the 'competitor_search' gate key"
        )

    required_tools = {
        tool
        for stage in required
        for tool in (contract.stages[stage].required_tools if stage in contract.stages else [])
    }
    if contract.hard_requirements.require_qdrant_check and "qdrant" not in required_tools:
        errors.append("require_qdrant_check needs a stage that requires the 'qdrant' tool")
    if contract.hard_requirements.require_duckdb_analysis and "duckdb" not in required_tools:
        errors.append(
            "require_duckdb_analysis needs a stage that requires the 'duckdb' tool"
        )

    return errors


def require_valid_contract(contract: ResearchContract) -> ResearchContract:
    """Return the contract, or raise ContractError carrying every error."""
    errors = validate_contract(contract)
    if errors:
        raise ContractError(errors)
    return contract


def load_research_contract(path: str | Path | None = None) -> ResearchContract:
    """Load the research contract from YAML and refuse an invalid one."""
    resolved = Path(path) if path is not None else repo_root() / CONTRACT_NAME
    try:
        raw = yaml.safe_load(resolved.read_text(encoding="utf-8")) or {}
    except UnicodeDecodeError as exc:
        # A file that is not UTF-8 text is a broken contract, not a crash.
        raise ContractError([f"{resolved} is not UTF-8 text: {exc}"]) from exc
    except yaml.YAMLError as exc:
        # A file that is not YAML at all is a broken contract, not a crash.
        raise ContractError([f"{resolved} is not valid YAML: {exc}"]) from exc
    if isinstance(raw, dict) and "research_contract" in raw:
        raw = raw["research_contract"]
    try:
        contract = ResearchContract.model_validate(raw)
    except ValidationError as exc:
        raise ContractError(_validation_messages(exc)) from exc
    return require_valid_contract(contract)


def _validation_messages(exc: ValidationError) -> list[str]:
    """Flatten a pydantic error into one message per problem."""
    messages: list[str] = []
    for error in exc.errors():
        location = ".".join(str(part) for part in error["loc"])
        messages.append(f"{location}: {error['msg']}" if location else error["msg"])
    return messages

"""The gate: evidence in, pass or fail out, with stable violation codes.

The gate never raises for a failed gate. It reads the contract and the
evidence and returns violations; only an invalid contract is an error, and
only when the caller asks for it to be validated.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from product_hunter.research.budget import BudgetSpec, BudgetTracker
from product_hunter.research.contract import (
    COUNTER_EVIDENCE_THRESHOLD,
    PROMOTION_GATE_KEY,
    THRESHOLD_METRICS,
    EvidenceThreshold,
    ResearchContract,
    contract_fingerprint,
    resolved_gate_key,
    served_gate_keys,
    threshold_fields,
    validate_contract,
)
from product_hunter.research.evidence import (
    ResearchClaim,
    StageExecution,
    StageStatus,
    ToolCallStatus,
)
from product_hunter.research.predicates import (
    DISTINCT_METRIC_SETS,
    METRIC_NAMES,
    MINIMUM_METRICS,
    PREDICATES,
    PredicateContext,
    explicit_zero_outcome,
    incomplete_calls,
    metric_value,
    mismatched_calls,
    stage_calls,
    traces_to_successful_call,
)


class ViolationCode(str, Enum):
    stage_missing = "stage_missing"
    stage_failed = "stage_failed"
    stage_timed_out = "stage_timed_out"
    budget_exceeded = "budget_exceeded"
    budget_unmeasured = "budget_unmeasured"
    artifact_invalid = "artifact_invalid"
    provenance_missing = "provenance_missing"
    evidence_threshold_unmet = "evidence_threshold_unmet"
    promotion_blocked = "promotion_blocked"
    artifact_missing = "artifact_missing"
    tool_missing = "tool_missing"
    quality_check_failed = "quality_check_failed"
    provenance_mismatch = "provenance_mismatch"
    contract_invalid = "contract_invalid"
    claim_evidence_missing = "claim_evidence_missing"


class Violation(BaseModel):
    """One reason a stage, run, or promotion did not pass."""

    model_config = ConfigDict(extra="forbid")

    code: ViolationCode
    stage: str | None = None
    message: str
    detail: dict[str, object] = Field(default_factory=dict)
    blocking: bool = True


class GateResult(BaseModel):
    """The verdict for one stage."""

    model_config = ConfigDict(extra="forbid")

    run_id: str
    stage: str
    passed: bool
    violations: list[Violation] = Field(default_factory=list)
    metrics: dict[str, int] = Field(default_factory=dict)


class RunGateResult(BaseModel):
    """The verdict for a whole run: every stage, plus the merged violations."""

    model_config = ConfigDict(extra="forbid")

    run_id: str
    passed: bool
    stages: dict[str, GateResult] = Field(default_factory=dict)
    violations: list[Violation] = Field(default_factory=list)
    # The fingerprint of the contract this result was judged under. It is
    # empty when the result was not produced by evaluate_run, and when the
    # contract was invalid and no verdict was reached, and an empty
    # fingerprint is not compared with anything.
    contract_fingerprint: str = ""


class PromotionResult(BaseModel):
    """The verdict on promoting a run's result."""

    model_config = ConfigDict(extra="forbid")

    run_id: str
    promoted: bool
    violations: list[Violation] = Field(default_factory=list)


def _sort_key(violation: Violation) -> tuple[str, str, str]:
    return (violation.stage or "", violation.code.value, violation.message)


def _sorted(violations: list[Violation]) -> list[Violation]:
    return sorted(violations, key=_sort_key)


def _metrics_for(execution: StageExecution) -> dict[str, int]:
    return {name: metric_value(name, execution) for name in sorted(METRIC_NAMES)}


def _elapsed_seconds(execution: StageExecution) -> float | None:
    """Return how long the stage ran, when both timestamps are usable."""
    if execution.started_at is None or execution.finished_at is None:
        return None
    started = execution.started_at
    finished = execution.finished_at
    if started.tzinfo is None or finished.tzinfo is None:
        return None
    elapsed = (finished - started).total_seconds()
    if elapsed < 0:
        # A stage that finished before it started has no duration, so it
        # cannot be shown to have finished inside its timeout. A zero-length
        # stage is a real duration and is in time.
        return None
    return elapsed


def _status_violation(stage: str, execution: StageExecution) -> Violation | None:
    """Report the violation the execution's status carries, if any."""
    status = execution.status
    if status is StageStatus.failed:
        return Violation(
            code=ViolationCode.stage_failed,
            stage=stage,
            message=f"stage {stage!r} failed",
            detail={"stage": stage},
        )
    if status is StageStatus.timed_out:
        return Violation(
            code=ViolationCode.stage_timed_out,
            stage=stage,
            message=f"stage {stage!r} timed out",
            detail={"stage": stage},
        )
    if status in (StageStatus.not_started, StageStatus.running):
        return Violation(
            code=ViolationCode.stage_missing,
            stage=stage,
            message=f"stage {stage!r} has not produced a result",
            detail={"stage": stage, "status": status.value},
        )
    return None


def _budget_violations(
    stage: str,
    budget: BudgetTracker | None,
    spec: BudgetSpec,
    *,
    ran: bool,
) -> list[Violation]:
    """Report the dimensions the run is over its limit on, and the limited
    dimensions a ran stage never measured.

    The contract's budgets are the authority for the limit and the tracker is
    the ledger for the spend, so the overrun is measured against the contract
    and not against the tracker's own spec: a tracker built from a looser
    spec cannot widen the budget the contract declares.

    The overrun is a fact about the run, not about one stage result, so it is
    reported on every stage: one that failed, timed out, or never ran carries
    the budget violation as well as its own.

    A stage that ran must record every dimension the contract limits it on,
    including an explicit zero when it incurred none. A stage that ran and
    holds no record for a per-stage limited dimension is a gate failure, not
    a gap: one violation per unmeasured (stage, dimension), each blocking.
    Dimensions limited only per run (browser_minutes, crawl_items, subq_calls)
    are never checked here: the pipeline has no tool that incurs them, so no
    stage can declare them and failing on them would fail every honest run.
    A stage that never ran is not measured at all, which stage_missing
    already reports.
    """
    if budget is None:
        return []
    violations: list[Violation] = [
        Violation(
            code=ViolationCode.budget_exceeded,
            stage=stage,
            message=f"budget dimension {dimension!r} is over its limit",
            detail={"dimension": dimension, "stage": stage},
        )
        for dimension in budget.exceeded_against(spec)
    ]
    if ran:
        # A stage that ran must record every dimension the contract limits it
        # on, including an explicit zero when it incurred none. Dimensions
        # limited only per run (browser_minutes, crawl_items, subq_calls) are
        # never checked here: the pipeline has no tool that incurs them, so no
        # stage can declare them and failing on them would fail every honest
        # run.
        for dimension in spec.per_stage.get(stage, {}):
            if not budget.measured(dimension, stage):
                violations.append(
                    Violation(
                        code=ViolationCode.budget_unmeasured,
                        stage=stage,
                        message=(
                            f"stage {stage!r} ran but never measured budget "
                            f"dimension {dimension!r}"
                        ),
                        detail={"dimension": dimension, "stage": stage},
                    )
                )
    return violations


def evaluate_stage(
    contract: ResearchContract,
    run_id: str,
    stage: str,
    execution: StageExecution | None,
    budget: BudgetTracker | None = None,
) -> GateResult:
    """Judge one stage against the contract. A failed gate returns violations."""
    errors = validate_contract(contract)
    if errors:
        violation = Violation(
            code=ViolationCode.contract_invalid,
            stage=stage,
            message="the research contract is invalid",
            detail={"errors": errors},
        )
        return GateResult(
            run_id=run_id, stage=stage, passed=False, violations=[violation], metrics={}
        )

    budget_violations = _budget_violations(
        stage, budget, contract.budgets, ran=execution is not None
    )

    def _result(
        violations: list[Violation], metrics: dict[str, int] | None = None
    ) -> GateResult:
        ordered = _sorted(violations)
        return GateResult(
            run_id=run_id,
            stage=stage,
            passed=not any(violation.blocking for violation in ordered),
            violations=ordered,
            metrics=metrics if metrics is not None else {},
        )

    spec = contract.stages.get(stage)
    if spec is None:
        return _result(
            [
                Violation(
                    code=ViolationCode.stage_missing,
                    stage=stage,
                    message=f"stage {stage!r} has no contract",
                    detail={"stage": stage},
                ),
                *budget_violations,
            ]
        )

    if execution is None:
        return _result(
            [
                Violation(
                    code=ViolationCode.stage_missing,
                    stage=stage,
                    message=f"stage {stage!r} never ran",
                    detail={"stage": stage},
                ),
                *budget_violations,
            ]
        )

    if execution.stage != stage:
        # The execution is the root of the evidence tree: it has to belong to
        # the stage it is judged against. Only stage names reach the detail.
        # A misfiled execution is still the execution it is, so its status is
        # reported next to the mismatch: a failed stage cannot hide behind a
        # wrong name.
        violations = [
            Violation(
                code=ViolationCode.provenance_mismatch,
                stage=stage,
                message=(
                    f"the execution belongs to stage {execution.stage!r}, "
                    f"not to stage {stage!r}"
                ),
                detail={"stage": stage, "execution_stage": execution.stage},
            )
        ]
        status_violation = _status_violation(stage, execution)
        if status_violation is not None:
            violations.append(status_violation)
        return _result([*violations, *budget_violations])

    metrics = _metrics_for(execution)
    context = PredicateContext(run_id=run_id, execution=execution, claims=())

    status_violation = _status_violation(stage, execution)
    if status_violation is not None:
        return _result([status_violation, *budget_violations], metrics)

    elapsed = _elapsed_seconds(execution)
    if spec.timeout_seconds is not None and (
        elapsed is None or elapsed > spec.timeout_seconds
    ):
        # A stage that cannot prove it finished inside its timeout is treated
        # as having overrun it: the gate fails closed. A missing timestamp
        # must not switch the check off.
        if elapsed is None:
            timeout_message = (
                f"stage {stage!r} cannot be shown to have finished inside its timeout"
            )
        else:
            timeout_message = f"stage {stage!r} ran longer than its timeout"
        return _result(
            [
                Violation(
                    code=ViolationCode.stage_timed_out,
                    stage=stage,
                    message=timeout_message,
                    detail={
                        "stage": stage,
                        "timeout_seconds": spec.timeout_seconds,
                        "elapsed_seconds": elapsed,
                    },
                ),
                *budget_violations,
            ],
            metrics,
        )

    violations: list[Violation] = []

    for tool in spec.required_tools:
        # The tool has to have run in this stage: a call that names another
        # stage is not this stage's evidence.
        ran = any(
            call.tool == tool and call.status is ToolCallStatus.success
            for call in stage_calls(execution)
        )
        if not ran:
            violations.append(
                Violation(
                    code=ViolationCode.tool_missing,
                    stage=stage,
                    message=f"required tool {tool!r} never ran successfully in stage {stage!r}",
                    detail={"tool": tool, "stage": stage},
                )
            )

    if contract.hard_requirements.require_provenance:
        for call in incomplete_calls(context):
            violations.append(
                Violation(
                    code=ViolationCode.provenance_missing,
                    stage=stage,
                    message=(
                        f"tool call {call.tool!r} in stage {stage!r} lacks required provenance"
                    ),
                    detail={
                        "tool": call.tool,
                        "stage": stage,
                        "run_id": call.run_id,
                        "has_tool_call_id": bool(call.tool_call_id),
                        "result_is_durable": call.result_is_durable,
                    },
                )
            )
        for call in mismatched_calls(context):
            violations.append(
                Violation(
                    code=ViolationCode.provenance_mismatch,
                    stage=stage,
                    message=(
                        f"tool call {call.tool!r} belongs to run {call.run_id!r} "
                        f"stage {call.stage!r}"
                    ),
                    detail={
                        "tool": call.tool,
                        "call_run_id": call.run_id,
                        "call_stage": call.stage,
                        "run_id": run_id,
                        "stage": stage,
                    },
                )
            )

    for kind in spec.required_outputs:
        kind_artifacts = [
            artifact for artifact in execution.artifacts if artifact.kind == kind
        ]
        # An artifact of a required kind has to belong to the stage being
        # judged: one filed under another stage is not this stage's output.
        # Only stage names reach the detail.
        if not any(artifact.stage == stage for artifact in kind_artifacts):
            violations.append(
                Violation(
                    code=ViolationCode.artifact_missing,
                    stage=stage,
                    message=f"required output {kind!r} has no artifact in stage {stage!r}",
                    detail={"kind": kind, "stage": stage},
                )
            )
        for artifact in kind_artifacts:
            if artifact.stage != stage:
                violations.append(
                    Violation(
                        code=ViolationCode.provenance_mismatch,
                        stage=stage,
                        message=(
                            f"the artifact belongs to stage {artifact.stage!r}, "
                            f"not to stage {stage!r}"
                        ),
                        detail={"stage": stage, "artifact_stage": artifact.stage},
                    )
                )

    for artifact in execution.artifacts:
        if artifact.kind not in spec.required_outputs:
            continue
        if not artifact.valid:
            violations.append(
                Violation(
                    code=ViolationCode.artifact_invalid,
                    stage=stage,
                    message=f"artifact {artifact.artifact_id!r} is invalid",
                    detail={
                        "artifact_id": artifact.artifact_id,
                        "kind": artifact.kind,
                    },
                )
            )
        elif spec.required_tools and not traces_to_successful_call(execution, artifact):
            # Derivation is checked when the stage declares the tools it used.
            violations.append(
                Violation(
                    code=ViolationCode.artifact_invalid,
                    stage=stage,
                    message=(
                        f"artifact {artifact.artifact_id!r} does not trace to a successful "
                        f"tool call in stage {stage!r}"
                    ),
                    detail={
                        "artifact_id": artifact.artifact_id,
                        "kind": artifact.kind,
                        "produced_by_tool_call_id": artifact.produced_by_tool_call_id or "",
                    },
                )
            )

    for check in spec.quality_checks:
        predicate = PREDICATES.get(check)
        if predicate is None:
            # An unknown predicate name is a contract error and never a silent pass.
            violations.append(
                Violation(
                    code=ViolationCode.quality_check_failed,
                    stage=stage,
                    message=f"quality check {check!r} is not in the predicate registry",
                    detail={"check": check, "stage": stage, "reason": "unknown predicate"},
                )
            )
            continue
        passed, detail = predicate(context)
        if not passed:
            violations.append(
                Violation(
                    code=ViolationCode.quality_check_failed,
                    stage=stage,
                    message=f"quality check {check!r} failed in stage {stage!r}: {detail}",
                    detail={"check": check, "stage": stage, "reason": detail},
                )
            )

    gate_key = resolved_gate_key(contract, stage)
    threshold = contract.evidence_thresholds.get(gate_key)
    if threshold is not None:
        violations.extend(_threshold_violations(stage, gate_key, threshold, execution))

    violations.extend(budget_violations)

    return _result(violations, metrics)


def _threshold_violations(
    stage: str,
    gate_key: str,
    threshold: EvidenceThreshold,
    execution: StageExecution,
) -> list[Violation]:
    """Compare every minimum of a gate key against the stage metrics."""
    violations: list[Violation] = []
    for name, minimum in threshold_fields(threshold).items():
        metric = THRESHOLD_METRICS.get(name)
        if metric is None:
            continue
        value = metric_value(metric, execution)
        met = value >= minimum
        if name == COUNTER_EVIDENCE_THRESHOLD and not met:
            met = explicit_zero_outcome(execution)[0]
        if met:
            continue
        violations.append(
            Violation(
                code=ViolationCode.evidence_threshold_unmet,
                stage=stage,
                message=(
                    f"gate {gate_key!r} needs {minimum} {name} but the stage has {value}"
                ),
                detail={
                    "gate_key": gate_key,
                    "metric": metric,
                    "minimum": minimum,
                    "value": value,
                },
            )
        )
    return violations


def evaluate_run(
    contract: ResearchContract,
    run_id: str,
    executions: Mapping[str, StageExecution | None],
    budget: BudgetTracker | None = None,
) -> RunGateResult:
    """Judge every required stage of a run and merge the violations."""
    errors = validate_contract(contract)
    if errors:
        violation = Violation(
            code=ViolationCode.contract_invalid,
            stage=None,
            message="the research contract is invalid",
            detail={"errors": errors},
        )
        return RunGateResult(
            run_id=run_id,
            passed=False,
            stages={},
            violations=[violation],
        )

    results: dict[str, GateResult] = {}
    violations: list[Violation] = []
    for stage in contract.required_stages:
        result = evaluate_stage(contract, run_id, stage, executions.get(stage), budget)
        results[stage] = result
        violations.extend(result.violations)

    violations.extend(_promotion_threshold_violations(contract, executions))
    violations = _sorted(violations)
    passed = not any(violation.blocking for violation in violations)
    return RunGateResult(
        run_id=run_id,
        passed=passed,
        stages=results,
        violations=violations,
        contract_fingerprint=contract_fingerprint(contract),
    )


def _run_executions(
    contract: ResearchContract, executions: Mapping[str, StageExecution | None]
) -> list[StageExecution]:
    """The executions the run is made of.

    A stage contributes its evidence only when it ran, and only when the
    execution is filed under the stage it belongs to: the stage gate refuses a
    misfiled execution, so the run-level sum must not count it either.
    """
    found: list[StageExecution] = []
    for stage in contract.required_stages:
        execution = executions.get(stage)
        if execution is None or execution.stage != stage:
            continue
        found.append(execution)
    return found


def _run_metric_value(metric: str, executions: list[StageExecution]) -> int:
    """The run-level value of a metric over the run's executions.

    The distinct metrics count hosts, queries, and families, so the run-level
    value is the distinct count over the union of the executions. The sum of
    their counts would count the same host, query, or family once per stage.
    The minimum metrics are a smallest count rather than a total, so the
    run-level value is the smallest of the stage values, and zero for a run
    with no executions: the sum would report a number the run never had. The
    additive metrics count items, and their sum is the run's total.
    """
    set_fn = DISTINCT_METRIC_SETS.get(metric)
    if set_fn is not None:
        union: set[str] = set()
        for execution in executions:
            union |= set_fn(execution)
        return len(union)
    if metric in MINIMUM_METRICS:
        values = [metric_value(metric, execution) for execution in executions]
        return min(values) if values else 0
    return sum(metric_value(metric, execution) for execution in executions)


def _counter_evidence_zero_recorded(
    contract: ResearchContract, executions: Mapping[str, StageExecution | None]
) -> bool:
    """Report whether the counter-evidence stage recorded a legitimate zero.

    The stage-level rule is scoped to the execution being judged, so the
    run-level rule is scoped to the execution of the stage that serves the
    'counter_evidence' gate key. A zero recorded by another stage says
    nothing about the counter-evidence minimum, and no stage serving the key
    means no zero is accepted at all.
    """
    stage = served_gate_keys(contract).get("counter_evidence")
    if stage is None:
        return False
    execution = executions.get(stage)
    if execution is None or execution.stage != stage:
        return False
    return explicit_zero_outcome(execution)[0]


def _promotion_threshold_violations(
    contract: ResearchContract, executions: Mapping[str, StageExecution | None]
) -> list[Violation]:
    """Check the thresholds of the run-level promotion gate key."""
    threshold = contract.evidence_thresholds.get(PROMOTION_GATE_KEY)
    if threshold is None:
        return []
    run_executions = _run_executions(contract, executions)
    violations: list[Violation] = []
    for name, minimum in threshold_fields(threshold).items():
        metric = THRESHOLD_METRICS.get(name)
        if metric is None:
            continue
        # Only the stages the gate evaluated are part of the run, so only they
        # count towards the run-level minimum.
        value = _run_metric_value(metric, run_executions)
        if value >= minimum:
            continue
        if name == COUNTER_EVIDENCE_THRESHOLD and _counter_evidence_zero_recorded(
            contract, executions
        ):
            # The stage-level check accepts a recorded zero for this field, so
            # the run-level check accepts the same one, from the same stage.
            continue
        violations.append(
            Violation(
                code=ViolationCode.evidence_threshold_unmet,
                stage=None,
                message=(
                    f"gate {PROMOTION_GATE_KEY!r} needs {minimum} {name} but the run has {value}"
                ),
                detail={
                    "gate_key": PROMOTION_GATE_KEY,
                    "metric": metric,
                    "minimum": minimum,
                    "value": value,
                },
            )
        )
    return violations


def _gate_passed(
    contract: ResearchContract, run_result: RunGateResult, gate_key: str
) -> bool:
    """Report whether the stage serving a gate key passed."""
    stage = served_gate_keys(contract).get(gate_key)
    if stage is None:
        return False
    result = run_result.stages.get(stage)
    return result is not None and result.passed


def evaluate_promotion(
    contract: ResearchContract,
    run_id: str,
    run_result: RunGateResult,
    claims: Iterable[ResearchClaim],
) -> PromotionResult:
    """Judge whether a run's result may be promoted."""
    # The claims are read twice below, so they are materialised first: a
    # one-shot iterator would be empty on the second read and the
    # claim_evidence_links rule would be silently satisfied.
    claims = tuple(claims)
    errors = validate_contract(contract)
    if errors:
        violation = Violation(
            code=ViolationCode.contract_invalid,
            stage=None,
            message="the research contract is invalid",
            detail={"errors": errors},
        )
        return PromotionResult(run_id=run_id, promoted=False, violations=[violation])

    if run_result.run_id != run_id:
        # The result says nothing about this run: it belongs to another one.
        # The two ids are ids, not free text.
        reason = "the run result belongs to another run"
        violation = Violation(
            code=ViolationCode.promotion_blocked,
            stage=None,
            message=f"promotion is blocked: {reason}",
            detail={
                "reason": reason,
                "run_id": run_id,
                "result_run_id": run_result.run_id,
            },
        )
        return PromotionResult(run_id=run_id, promoted=False, violations=[violation])

    fingerprint = contract_fingerprint(contract)
    if run_result.contract_fingerprint and run_result.contract_fingerprint != fingerprint:
        # The result was judged under another contract, so every verdict it
        # carries belongs to that other contract. The fingerprints are hashes,
        # not free text.
        reason = "the run result was judged under another contract"
        violation = Violation(
            code=ViolationCode.promotion_blocked,
            stage=None,
            message=f"promotion is blocked: {reason}",
            detail={
                "reason": reason,
                "contract_fingerprint": fingerprint,
                "result_contract_fingerprint": run_result.contract_fingerprint,
            },
        )
        return PromotionResult(run_id=run_id, promoted=False, violations=[violation])

    violations: list[Violation] = []
    reasons: list[str] = []

    for stage in contract.required_stages:
        result = run_result.stages.get(stage)
        if result is None:
            reasons.append(f"required stage {stage!r} is missing")
            continue
        codes = [violation.code for violation in result.violations]
        if ViolationCode.stage_missing in codes:
            reasons.append(f"required stage {stage!r} is missing")
        elif ViolationCode.stage_failed in codes:
            reasons.append(f"required stage {stage!r} failed")
        elif ViolationCode.stage_timed_out in codes:
            reasons.append(f"required stage {stage!r} timed out")
        elif contract.promotion.require_all_stages_passed and not result.passed:
            reasons.append(f"required stage {stage!r} did not pass")

    # The run-level threshold block is the one that guards promotion itself,
    # so its violation blocks promotion too. Only the run-level violations
    # count here: the per-stage ones are handled by the loop above, and the
    # budget one is handled at the end, so no fact is reported twice.
    if any(
        violation.stage is None
        and violation.code is ViolationCode.evidence_threshold_unmet
        for violation in run_result.violations
    ):
        reasons.append("the run did not meet its run-level thresholds")

    competitor_passed = _gate_passed(contract, run_result, "competitor_search")
    counter_passed = _gate_passed(contract, run_result, "counter_evidence")

    if (
        contract.hard_requirements.require_counter_evidence
        or contract.promotion.require_counter_evidence_gate
    ) and not counter_passed:
        reasons.append("the counter_evidence gate did not pass")
    if (
        contract.hard_requirements.require_competitor_search
        or contract.promotion.require_competitor_gate
    ) and not competitor_passed:
        reasons.append("the competitor_search gate did not pass")

    claim_kinds = {claim.kind.value for claim in claims}
    if "no_strong_competitor" in claim_kinds and not competitor_passed:
        reasons.append("a no_strong_competitor claim rides on a failed competitor_search gate")
    if "no_counter_evidence_found" in claim_kinds and not counter_passed:
        reasons.append(
            "a no_counter_evidence_found claim rides on a failed counter_evidence gate"
        )

    if (
        contract.hard_requirements.require_claim_evidence_links
        or contract.promotion.require_claim_evidence_links
    ):
        context = PredicateContext(
            run_id=run_id,
            execution=StageExecution(stage="promotion", status=StageStatus.succeeded),
            claims=tuple(claims),
        )
        linked, detail = PREDICATES["claim_evidence_links"](context)
        if not linked:
            reasons.append(f"a claim carries no evidence reference: {detail}")
            for claim in claims:
                if not claim.evidence_refs:
                    violations.append(
                        Violation(
                            code=ViolationCode.claim_evidence_missing,
                            stage=claim.stage,
                            message=f"claim {claim.claim_id!r} carries no evidence reference",
                            detail={"claim_id": claim.claim_id, "kind": claim.kind.value},
                        )
                    )

    if contract.promotion.require_quality_predicates:
        for stage, result in run_result.stages.items():
            for violation in result.violations:
                if violation.code is ViolationCode.quality_check_failed:
                    reasons.append(
                        f"stage {stage!r} failed a quality predicate: {violation.detail.get('check', '')}"
                    )

    # The budget overrun is a fact about the run, and evaluate_run already
    # wrote it as a violation on every stage, so the fact is read back from
    # there rather than from a tracker the caller has to remember.
    dimensions = sorted(
        {
            str(violation.detail.get("dimension", ""))
            for violation in run_result.violations
            if violation.code is ViolationCode.budget_exceeded
        }
    )
    for dimension in dimensions:
        if not dimension:
            continue
        reasons.append(f"the run is over its {dimension!r} budget")

    for reason in reasons:
        violations.append(
            Violation(
                code=ViolationCode.promotion_blocked,
                stage=None,
                message=f"promotion is blocked: {reason}",
                detail={"reason": reason},
            )
        )

    violations = _sorted(violations)
    promoted = not any(violation.blocking for violation in violations)
    return PromotionResult(run_id=run_id, promoted=promoted, violations=violations)

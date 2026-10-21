from __future__ import annotations

import pytest

from product_hunter.research import (
    METRIC_ARTIFACT_KINDS,
    PREDICATE_ARTIFACT_KINDS,
    PREDICATE_NAMES,
)
from product_hunter.research.contract import (
    THRESHOLD_METRICS,
    ContractError,
    ResearchContract,
    contract_fingerprint,
    load_research_contract,
    require_valid_contract,
    served_gate_keys,
    threshold_fields,
    validate_contract,
)


def _contract(
    *,
    discovery: dict | None = None,
    counter_evidence: dict | None = None,
    **overrides,
) -> ResearchContract:
    hard = {
        "require_competitor_search": False,
        "require_qdrant_check": False,
        "require_duckdb_analysis": False,
    }
    hard.update(overrides.pop("hard_requirements", {}))
    stages = {
        "discovery": {
            "required_tools": ["search"],
            "required_outputs": ["source_records"],
            "quality_checks": ["non_empty_queries"],
            "gate_key": "discovery",
        },
        "counter_evidence": {
            "required_tools": ["search"],
            "required_outputs": ["counter_evidence"],
            "quality_checks": [],
            "gate_key": "counter_evidence",
        },
    }
    if discovery:
        stages["discovery"].update(discovery)
    if counter_evidence:
        stages["counter_evidence"].update(counter_evidence)
    data = {
        "contract_version": "test-v1",
        "required_stages": ["discovery", "counter_evidence"],
        "stages": stages,
        "evidence_thresholds": {
            "discovery": {"min_distinct_queries": 1},
            "counter_evidence": {"min_counter_evidence_items_or_explicit_zero": 1},
        },
        "budgets": {"version": "test-v1"},
        "hard_requirements": hard,
        # No stage of this contract serves the 'competitor_search' gate key, so
        # the promotion flag for it has to be off: a contract that demands a
        # gate no stage serves can never promote, and the validator says so.
        "promotion": {"require_competitor_gate": False},
    }
    data.update(overrides)
    return ResearchContract.model_validate(data)


def test_the_shipped_baseline_loads_and_validates():
    contract = load_research_contract()

    assert validate_contract(contract) == []
    assert contract.contract_version == "phase4-v1"
    assert contract.stages["investigation"].gate_key == "competitor_search"


def test_the_shipped_baseline_declares_the_kinds_its_thresholds_and_checks_read():
    contract = load_research_contract()
    served = served_gate_keys(contract)

    for gate_key, threshold in contract.evidence_thresholds.items():
        stage = served.get(gate_key)
        if stage is None:
            # The promotion gate key is served by no stage.
            continue
        required = contract.stages[stage].required_outputs
        for field in threshold_fields(threshold):
            metric = THRESHOLD_METRICS.get(field)
            if metric is None:
                continue
            kind = METRIC_ARTIFACT_KINDS.get(metric)
            if kind is None:
                # A tool-based metric counts tool calls, not artifacts.
                continue
            assert kind in required, (
                f"gate key {gate_key!r} threshold {field!r} reads artifact kind "
                f"{kind!r}, which stage {stage!r} does not declare in required_outputs"
            )

    for stage, spec in contract.stages.items():
        required = spec.required_outputs
        for check in spec.quality_checks:
            kind = PREDICATE_ARTIFACT_KINDS.get(check)
            if kind is None:
                # The predicate reads tool calls, run-level claims, or nothing.
                continue
            assert kind in required, (
                f"quality check {check!r} reads artifact kind {kind!r}, which "
                f"stage {stage!r} does not declare in required_outputs"
            )


def test_the_predicate_artifact_kinds_cover_every_artifact_reading_predicate():
    # The predicates the comment in predicates.py names as absent: the ones
    # that read tool calls, every artifact kind, or the run's claims. A new
    # artifact-reading predicate forces a decision here.
    absent = PREDICATE_NAMES - set(PREDICATE_ARTIFACT_KINDS)

    assert absent == {
        "non_empty_queries",
        "source_diversity",
        "provenance_complete",
        "artifacts_valid",
        "claim_evidence_links",
        "mandatory_query_families",
        "known_query_families",
    }


def test_a_missing_stage_contract_is_an_error():
    contract = _contract(required_stages=["discovery", "counter_evidence", "validation"])

    errors = validate_contract(contract)

    assert any("'validation' has no stage contract" in error for error in errors)


def test_a_duplicate_stage_is_an_error():
    contract = _contract(required_stages=["discovery", "discovery", "counter_evidence"])

    errors = validate_contract(contract)

    assert any("required_stages has duplicates: discovery" in error for error in errors)


def test_an_empty_required_stage_list_is_an_error():
    contract = _contract(required_stages=[])

    errors = validate_contract(contract)

    assert "required_stages is empty" in errors


def test_a_stage_contract_for_an_unknown_stage_is_an_error():
    stages = dict(_contract().stages)
    stages["ghost"] = stages["discovery"]

    errors = validate_contract(_contract(stages=stages))

    assert any("'ghost' is not a required stage" in error for error in errors)


def test_an_unknown_quality_check_is_an_error():
    stages = dict(_contract().stages)
    stages["discovery"] = stages["discovery"].model_copy(
        update={"quality_checks": ["not_a_predicate"]}
    )

    errors = validate_contract(_contract(stages=stages))

    assert any(
        "stage 'discovery' names an unknown quality check 'not_a_predicate'" in error
        for error in errors
    )


def test_a_stage_may_not_use_the_run_level_claim_evidence_check():
    contract = _contract(discovery={"quality_checks": ["claim_evidence_links"]})

    errors = validate_contract(contract)

    assert any(
        "stage 'discovery'" in error
        and "claim_evidence_links" in error
        and "run-level" in error
        for error in errors
    )


def test_a_threshold_key_without_a_serving_stage_is_an_error():
    contract = _contract(
        evidence_thresholds={
            "discovery": {"min_distinct_queries": 1},
            "counter_evidence": {"min_counter_evidence_items_or_explicit_zero": 1},
            "nowhere": {"min_distinct_queries": 1},
        }
    )

    errors = validate_contract(contract)

    assert any("threshold key 'nowhere' is not a gate key any stage serves" in error for error in errors)


def test_the_promotion_gate_key_may_carry_thresholds():
    contract = _contract(
        evidence_thresholds={
            "discovery": {"min_distinct_queries": 1},
            "counter_evidence": {"min_counter_evidence_items_or_explicit_zero": 1},
            "promotion": {"min_independent_authors": 10},
        }
    )

    assert validate_contract(contract) == []


def test_an_unknown_threshold_metric_is_an_error():
    contract = _contract(
        evidence_thresholds={
            "discovery": {"min_distinct_queries": 1, "min_not_a_metric": 3},
            "counter_evidence": {"min_counter_evidence_items_or_explicit_zero": 1},
        }
    )

    errors = validate_contract(contract)

    assert any(
        "threshold 'discovery' names an unknown metric 'min_not_a_metric'" in error
        for error in errors
    )


def test_a_threshold_with_no_minimum_is_an_error():
    contract = _contract(
        evidence_thresholds={
            "discovery": {},
            "counter_evidence": {"min_counter_evidence_items_or_explicit_zero": 1},
        }
    )

    errors = validate_contract(contract)

    assert "threshold 'discovery' sets no minimum" in errors


def test_an_unknown_budget_dimension_is_an_error():
    contract = _contract(budgets={"version": "test-v1", "per_run": {"not_a_dimension": 5.0}})

    errors = validate_contract(contract)

    assert any(
        "per_run budget names the unknown dimension 'not_a_dimension'" in error
        for error in errors
    )


def test_a_non_positive_limit_is_an_error():
    contract = _contract(budgets={"version": "test-v1", "per_run": {"search_queries": 0.0}})

    errors = validate_contract(contract)

    assert "per_run budget limit for 'search_queries' must be positive" in errors


def test_a_soft_threshold_outside_the_interval_is_an_error():
    contract = _contract(
        budgets={"version": "test-v1", "soft_thresholds": {"search_queries": 1.5}}
    )

    errors = validate_contract(contract)

    assert "soft threshold for 'search_queries' must be in (0, 1]" in errors


def test_a_zero_soft_threshold_is_an_error():
    contract = _contract(
        budgets={"version": "test-v1", "soft_thresholds": {"search_queries": 0.0}}
    )

    errors = validate_contract(contract)

    assert "soft threshold for 'search_queries' must be in (0, 1]" in errors


def test_a_per_stage_budget_must_name_a_required_stage():
    contract = _contract(
        budgets={"version": "test-v1", "per_stage": {"ghost": {"search_queries": 5.0}}}
    )

    errors = validate_contract(contract)

    assert any(
        "per_stage budget names a stage that is not required: 'ghost'" in error
        for error in errors
    )


def test_a_hard_requirement_without_a_serving_stage_is_an_error():
    contract = _contract(hard_requirements={"require_competitor_search": True})

    errors = validate_contract(contract)

    assert any(
        "require_competitor_search needs a stage that serves the 'competitor_search' gate key"
        in error
        for error in errors
    )


def test_a_qdrant_check_without_a_qdrant_stage_is_an_error():
    contract = _contract(hard_requirements={"require_qdrant_check": True})

    errors = validate_contract(contract)

    assert "require_qdrant_check needs a stage that requires the 'qdrant' tool" in errors


def test_a_qdrant_check_with_a_qdrant_stage_is_valid():
    contract = _contract(
        discovery={"required_tools": ["search", "qdrant"]},
        hard_requirements={"require_qdrant_check": True},
    )

    assert validate_contract(contract) == []


def test_a_duckdb_analysis_without_a_duckdb_stage_is_an_error():
    contract = _contract(hard_requirements={"require_duckdb_analysis": True})

    errors = validate_contract(contract)

    assert "require_duckdb_analysis needs a stage that requires the 'duckdb' tool" in errors


def test_a_duckdb_analysis_with_a_duckdb_stage_is_valid():
    contract = _contract(
        counter_evidence={"required_tools": ["search", "duckdb"]},
        hard_requirements={"require_duckdb_analysis": True},
    )

    assert validate_contract(contract) == []


def test_a_required_stage_with_no_gating_mechanism_is_an_error():
    contract = _contract(
        discovery={"required_tools": [], "required_outputs": [], "quality_checks": []},
        evidence_thresholds={
            "counter_evidence": {"min_counter_evidence_items_or_explicit_zero": 1}
        },
    )

    errors = validate_contract(contract)

    assert (
        "stage 'discovery' has no gating mechanism: it needs required tools, "
        "required outputs, quality checks, or a threshold block" in errors
    )


def test_a_stage_gated_only_by_a_threshold_block_is_valid():
    contract = _contract(
        discovery={"required_tools": [], "required_outputs": [], "quality_checks": []},
    )

    assert validate_contract(contract) == []


def test_the_extraction_booleans_come_with_the_extraction_stage():
    contract = load_research_contract()

    assert "require_quote_verbatim" not in contract.hard_requirements.model_dump()
    assert "require_source_span" not in contract.hard_requirements.model_dump()


def test_two_stages_may_not_serve_the_same_gate_key():
    stages = dict(_contract().stages)
    stages["counter_evidence"] = stages["counter_evidence"].model_copy(
        update={"gate_key": "discovery"}
    )

    errors = validate_contract(_contract(stages=stages))

    assert any(
        "resolve to the same gate key 'discovery'" in error for error in errors
    )


def test_an_invalid_contract_raises_contract_error_carrying_every_error():
    contract = ResearchContract.model_validate(
        {
            "contract_version": "test-v1",
            "required_stages": ["discovery", "ghost"],
            "stages": {
                "discovery": {
                    "required_tools": ["search"],
                    "required_outputs": ["source_records"],
                    "quality_checks": ["not_a_predicate"],
                    "gate_key": "discovery",
                },
                "extra": {
                    "required_tools": ["search"],
                    "required_outputs": ["source_records"],
                    "quality_checks": [],
                    "gate_key": "extra",
                },
            },
            "evidence_thresholds": {"discovery": {"min_distinct_queries": 1}},
            "budgets": {"version": "test-v1", "per_run": {"not_a_dimension": 5.0}},
            "hard_requirements": {
                "require_counter_evidence": False,
                "require_competitor_search": False,
                "require_qdrant_check": False,
                "require_duckdb_analysis": False,
            },
            # No stage of this contract serves the 'counter_evidence' or the
            # 'competitor_search' gate key, so the promotion flags for them
            # have to be off: the errors under test are the four below.
            "promotion": {
                "require_counter_evidence_gate": False,
                "require_competitor_gate": False,
            },
        }
    )

    with pytest.raises(ContractError) as excinfo:
        require_valid_contract(contract)

    errors = excinfo.value.errors
    assert len(errors) == 4
    assert any("'ghost' has no stage contract" in error for error in errors)
    assert any("'extra' is not a required stage" in error for error in errors)
    assert any("unknown quality check 'not_a_predicate'" in error for error in errors)
    assert any("unknown dimension 'not_a_dimension'" in error for error in errors)


def test_a_contract_that_can_never_promote_is_an_error():
    contract = _contract(
        hard_requirements={"require_competitor_search": False},
        promotion={"require_competitor_gate": True},
    )

    errors = validate_contract(contract)

    assert any(
        "promotion.require_competitor_gate" in error
        and "competitor_search" in error
        for error in errors
    )

    # The same rule covers the counter-evidence gate.
    counter = _contract(
        required_stages=["discovery"],
        stages={
            "discovery": {
                "required_tools": ["search"],
                "required_outputs": ["source_records"],
                "quality_checks": [],
                "gate_key": "discovery",
            }
        },
        hard_requirements={"require_counter_evidence": False},
        promotion={"require_counter_evidence_gate": True},
    )

    assert any(
        "promotion.require_counter_evidence_gate" in error
        and "counter_evidence" in error
        for error in validate_contract(counter)
    )


def test_a_stage_may_not_serve_the_promotion_gate_key():
    contract = _contract(discovery={"gate_key": "promotion"})

    errors = validate_contract(contract)

    assert any(
        "stage 'discovery'" in error and "promotion" in error for error in errors
    )


def test_a_broken_contract_file_raises_contract_error(tmp_path):
    path = tmp_path / "contract.yaml"
    path.write_text(
        "contract_version: test-v1\n"
        "required_stages: [discovery]\n"
        "stages:\n"
        "  discovery:\n"
        "    required_tools: search\n",
        encoding="utf-8",
    )

    with pytest.raises(ContractError) as excinfo:
        load_research_contract(path)

    assert excinfo.value.errors
    assert "stages.discovery.required_tools" in str(excinfo.value)


def test_an_inconsistent_contract_file_raises_contract_error(tmp_path):
    path = tmp_path / "contract.yaml"
    path.write_text(
        "contract_version: test-v1\n"
        "required_stages: [discovery]\n"
        "budgets:\n"
        "  version: test-v1\n",
        encoding="utf-8",
    )

    with pytest.raises(ContractError) as excinfo:
        load_research_contract(path)

    assert any(
        "required stage 'discovery' has no stage contract" in error
        for error in excinfo.value.errors
    )


def test_a_nan_budget_limit_is_a_contract_error(tmp_path):
    per_run = _contract(
        budgets={"version": "test-v1", "per_run": {"search_queries": float("nan")}}
    )

    errors = validate_contract(per_run)

    assert "per_run budget limit for 'search_queries' must be positive" in errors

    per_stage = _contract(
        budgets={
            "version": "test-v1",
            "per_stage": {"discovery": {"search_queries": float("nan")}},
        }
    )

    errors = validate_contract(per_stage)

    assert (
        "per_stage budget limit for 'discovery' and 'search_queries' must be positive"
        in errors
    )

    per_source = _contract(
        budgets={"version": "test-v1", "per_source": {"example.com": float("nan")}}
    )

    errors = validate_contract(per_source)

    assert "per_source budget limit for 'example.com' must be positive" in errors

    # The same limit written in a contract file is refused at load time.
    path = tmp_path / "contract.yaml"
    path.write_text(
        "contract_version: test-v1\n"
        "required_stages: [discovery]\n"
        "stages:\n"
        "  discovery:\n"
        "    required_tools: [search]\n"
        "    required_outputs: [source_records]\n"
        "    quality_checks: [non_empty_queries]\n"
        "hard_requirements:\n"
        "  require_counter_evidence: false\n"
        "  require_competitor_search: false\n"
        "  require_qdrant_check: false\n"
        "  require_duckdb_analysis: false\n"
        "promotion:\n"
        "  require_counter_evidence_gate: false\n"
        "  require_competitor_gate: false\n"
        "budgets:\n"
        "  version: test-v1\n"
        "  per_run:\n"
        "    search_queries: .nan\n",
        encoding="utf-8",
    )

    with pytest.raises(ContractError) as excinfo:
        load_research_contract(path)

    assert excinfo.value.errors == [
        "per_run budget limit for 'search_queries' must be positive"
    ]


def test_a_broken_contract_file_with_a_yaml_syntax_error_raises_contract_error(tmp_path):
    path = tmp_path / "contract.yaml"
    path.write_text(
        "contract_version: test-v1\n"
        "required_stages: [discovery]\n"
        "stages: {\n"
        "  discovery:\n",
        encoding="utf-8",
    )

    with pytest.raises(ContractError) as excinfo:
        load_research_contract(path)

    assert excinfo.value.errors
    assert "not valid YAML" in str(excinfo.value)


def test_a_contract_file_that_is_not_utf8_raises_contract_error(tmp_path):
    path = tmp_path / "contract.yaml"
    path.write_bytes(b"contract_version: test-v1\nkey: \xff\xfe\n")

    with pytest.raises(ContractError) as excinfo:
        load_research_contract(path)

    assert excinfo.value.errors
    assert "not UTF-8" in str(excinfo.value)


def test_the_contract_fingerprint_ignores_a_list_order_that_changes_nothing():
    stages = {
        "discovery": {
            "required_tools": ["search", "fetch"],
            "required_outputs": ["source_records", "document"],
            "quality_checks": ["non_empty_queries", "source_diversity"],
            "gate_key": "discovery",
        },
        "counter_evidence": {
            "required_tools": ["search"],
            "required_outputs": ["counter_evidence"],
            "quality_checks": [],
            "gate_key": "counter_evidence",
        },
    }
    reordered = {
        "discovery": {
            "required_tools": ["fetch", "search"],
            "required_outputs": ["document", "source_records"],
            "quality_checks": ["source_diversity", "non_empty_queries"],
            "gate_key": "discovery",
        },
        "counter_evidence": stages["counter_evidence"],
    }

    first = _contract(required_stages=["discovery", "counter_evidence"], stages=stages)
    second = _contract(
        required_stages=["counter_evidence", "discovery"], stages=reordered
    )

    # The two contracts produce identical verdicts, so they fingerprint the
    # same: the order of the lists that carry no meaning is not a difference.
    assert contract_fingerprint(first) == contract_fingerprint(second)

    # The retry policy's list of reasons is order-free too.
    retries = _contract(
        required_stages=["discovery", "counter_evidence"],
        stages=stages,
        retry={"retry_on": ["timeout", "transient"]},
    )
    retries_reordered = _contract(
        required_stages=["discovery", "counter_evidence"],
        stages=stages,
        retry={"retry_on": ["transient", "timeout"]},
    )

    assert contract_fingerprint(retries) == contract_fingerprint(retries_reordered)

    # A threshold is a difference: it changes the verdicts.
    tighter = _contract(
        required_stages=["discovery", "counter_evidence"],
        stages=stages,
        evidence_thresholds={
            "discovery": {"min_distinct_queries": 2},
            "counter_evidence": {"min_counter_evidence_items_or_explicit_zero": 1},
        },
    )

    assert contract_fingerprint(first) != contract_fingerprint(tighter)


def test_a_threshold_on_a_kind_the_stage_does_not_declare_is_a_contract_error():
    contract = _contract(
        evidence_thresholds={
            # The discovery stage declares 'source_records' as its output, and
            # this threshold reads the 'document' kind, which it never
            # declares.
            "discovery": {"min_new_documents": 1},
            "counter_evidence": {"min_counter_evidence_items_or_explicit_zero": 1},
        }
    )

    errors = validate_contract(contract)

    # The error names the stage and the kind it does not declare.
    assert any(
        "discovery" in error and "document" in error for error in errors
    ), errors
    # The threshold on a kind the stage does declare is accepted.
    assert validate_contract(_contract()) == []

    # A threshold on a stage that has no contract of its own is reported and
    # not raised: the rule never reads a stage that is not there.
    without_contract = _contract(
        required_stages=["discovery", "counter_evidence", "ghost"],
        evidence_thresholds={
            "discovery": {"min_distinct_queries": 1},
            "counter_evidence": {"min_counter_evidence_items_or_explicit_zero": 1},
            "ghost": {"min_new_documents": 1},
        },
    )

    errors = validate_contract(without_contract)

    assert any("ghost" in error for error in errors), errors


def test_a_quality_check_on_a_kind_the_stage_does_not_declare_is_a_contract_error():
    contract = _contract(
        discovery={
            # The discovery stage declares 'source_records' as its output, and
            # this quality check reads the 'neighbor_check' kind, which it
            # never declares.
            "quality_checks": ["nearest_neighbor_checked"],
            "required_outputs": ["source_records"],
        }
    )

    errors = validate_contract(contract)

    # The error names the stage, the check, and the kind it does not declare.
    assert any(
        "discovery" in error
        and "nearest_neighbor_checked" in error
        and "neighbor_check" in error
        for error in errors
    ), errors

    # A quality check on a kind the stage does declare is accepted.
    declared = _contract(
        discovery={
            "quality_checks": ["nearest_neighbor_checked"],
            "required_outputs": ["source_records", "neighbor_check"],
        }
    )

    assert validate_contract(declared) == []

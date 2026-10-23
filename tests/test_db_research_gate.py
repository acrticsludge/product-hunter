from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from product_hunter.db.connection import connect
from product_hunter.db.leases import LeaseRefused
from product_hunter.db.migrations import MIGRATIONS_DIR, current_version, migrate
from product_hunter.db.research_gate import (
    GateBlocked,
    GateConflict,
    SqliteGateResultStore,
    SqliteToolCallStore,
    build_budget_tracker,
    evaluate_promotion_gate,
    evaluate_run_gate,
    evaluate_stage_gate,
    load_run_evidence,
    load_run_gate_result,
    measure_run_spend,
    record_artifact,
    record_claim,
    record_stage_execution,
    require_promotable,
    unmeasured_budget_dimensions,
)
from product_hunter.research.contract import (
    ResearchContract,
    contract_fingerprint,
    load_research_contract,
    resolved_gate_key,
    threshold_fields,
)
from product_hunter.research.evidence import (
    ArtifactRecord,
    ClaimKind,
    ResearchClaim,
    StageExecution,
    StageStatus,
    ToolCallProvenance,
    ToolCallStatus,
    ZeroResultOutcome,
)
from product_hunter.research.gate import GateResult, evaluate_stage
from product_hunter.research.persistence import GateResultStore, ToolCallStore
from product_hunter.research.query_plan import (
    MANDATORY_FAMILIES_BY_STAGE,
    QUERY_FAMILIES,
)

FROZEN = datetime(2026, 10, 21, 12, 0, 0, tzinfo=timezone.utc)
RUN_ID = "run-1"


def _migration_count() -> int:
    """How many migration files the repository ships.

    The gate tests care that the gate tables exist, not what the schema
    version happens to be, so the count is read from the directory instead of
    pinned here and forgotten.
    """
    return len(list(MIGRATIONS_DIR.glob("*.sql")))

ARTIFACT_KIND = {
    "discovery": "source_records",
    "counter_evidence": "counter_evidence",
    "investigation": "competitors",
}

STAGES = {
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
    "investigation": {
        "required_tools": ["search"],
        "required_outputs": ["competitors"],
        "quality_checks": [],
        "gate_key": "competitor_search",
    },
}

THRESHOLDS = {
    "discovery": {"min_distinct_queries": 1},
    "counter_evidence": {"min_counter_evidence_items_or_explicit_zero": 1},
    "competitor_search": {"min_distinct_queries": 1},
}


@pytest.fixture()
def conn() -> sqlite3.Connection:
    connection = connect(":memory:")
    migrate(connection)
    try:
        yield connection
    finally:
        connection.close()


def _contract(**overrides) -> ResearchContract:
    data = {
        "contract_version": "test-v1",
        "required_stages": list(STAGES),
        "stages": {name: dict(values) for name, values in STAGES.items()},
        "evidence_thresholds": {
            key: dict(value) for key, value in THRESHOLDS.items()
        },
        "budgets": {"version": "test-v1"},
        "hard_requirements": {
            "require_qdrant_check": False,
            "require_duckdb_analysis": False,
        },
    }
    data.update(overrides)
    return ResearchContract.model_validate(data)


def _call(stage: str = "discovery", **overrides) -> ToolCallProvenance:
    data = {
        "run_id": RUN_ID,
        "stage": stage,
        "agent": "researcher",
        "tool": "search",
        "purpose": "collect evidence",
        "timestamp": FROZEN,
        "query_or_operation": f"{stage} query",
        "result_ref": f"raw/{stage}",
        "result_count": 3,
        "status": ToolCallStatus.success,
        "tool_call_id": f"{stage}-call-1",
        "query_family": "problem",
    }
    data.update(overrides)
    return ToolCallProvenance(**data)


def _artifact(stage: str = "discovery", **overrides) -> ArtifactRecord:
    data = {
        "artifact_id": f"{stage}-art-1",
        "stage": stage,
        "kind": ARTIFACT_KIND[stage],
        "ref": f"data/artifacts/run-1/{stage}.jsonl",
        "item_count": 1,
        "produced_by_tool_call_id": f"{stage}-call-1",
        "created_at": FROZEN,
    }
    data.update(overrides)
    return ArtifactRecord(**data)


def _execution(stage: str = "discovery", **overrides) -> StageExecution:
    data = {
        "stage": stage,
        "status": StageStatus.succeeded,
        "started_at": FROZEN,
        "finished_at": FROZEN + timedelta(seconds=30),
    }
    data.update(overrides)
    return StageExecution(**data)


def _claim(**overrides) -> ResearchClaim:
    data = {
        "claim_id": "claim-1",
        "kind": ClaimKind.opportunity_exists,
        "statement": "nobody serves this workflow yet",
        "stage": "investigation",
        "evidence_refs": ["raw/investigation"],
    }
    data.update(overrides)
    return ResearchClaim(**data)


def _insert_run(conn: sqlite3.Connection, run_id: str = RUN_ID) -> None:
    conn.execute(
        """
        INSERT INTO research_runs (
            run_id, public_id, objective, original_request, status,
            created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run_id,
            run_id,
            "test",
            "",
            "running",
            FROZEN.isoformat(),
            FROZEN.isoformat(),
        ),
    )


def _seed_run(
    conn: sqlite3.Connection,
    run_id: str = RUN_ID,
    *,
    invalid_stages: tuple[str, ...] = (),
    invalid_reason: str = "the artifact does not match its schema",
) -> None:
    """Write the evidence of a run that passes the gate."""
    _insert_run(conn, run_id)
    store = SqliteToolCallStore(conn)
    for stage in STAGES:
        call = _call(stage, run_id=run_id, result_is_durable=True)
        store.append_tool_call(
            call, attempt=1, result_payload={"results": [call.result_ref]}
        )
        artifact = _artifact(stage)
        if stage in invalid_stages:
            artifact = _artifact(stage, valid=False, invalid_reason=invalid_reason)
        record_artifact(conn, run_id=run_id, artifact=artifact, attempt=1)
        record_stage_execution(
            conn, run_id=run_id, execution=_execution(stage)
        )
    record_claim(conn, run_id=run_id, claim=_claim())


def test_the_migration_creates_the_gate_tables() -> None:
    connection = connect(":memory:")
    try:
        assert migrate(connection) == _migration_count()
        assert current_version(connection) == _migration_count()
        rows = connection.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
        assert {
            "tool_calls",
            "research_stage_executions",
            "research_stage_artifacts",
            "research_claims",
            "stage_gate_results",
        } <= {row[0] for row in rows}
    finally:
        connection.close()


def test_migrating_twice_is_a_noop() -> None:
    connection = connect(":memory:")
    try:
        assert migrate(connection) == _migration_count()
        assert migrate(connection) == _migration_count()
        assert current_version(connection) == _migration_count()
    finally:
        connection.close()


def test_the_stores_satisfy_the_research_protocols(conn: sqlite3.Connection) -> None:
    assert isinstance(SqliteToolCallStore(conn), ToolCallStore)
    assert isinstance(SqliteGateResultStore(conn), GateResultStore)


def test_a_tool_call_round_trips_through_the_database(
    conn: sqlite3.Connection,
) -> None:
    _insert_run(conn)
    call = _call(
        "discovery", duration_ms=120, query_family="problem",
        result_is_durable=True,
    )
    store = SqliteToolCallStore(conn)
    store.append_tool_call(call, attempt=1, result_payload={"items": ["doc-1"]})

    loaded = store.list_tool_calls(RUN_ID)

    assert loaded == [call]
    assert loaded[0].duration_ms == 120
    assert loaded[0].timestamp == FROZEN


def test_tool_result_payload_is_durable_and_cannot_be_rebound(
    conn: sqlite3.Connection,
) -> None:
    _insert_run(conn)
    store = SqliteToolCallStore(conn)
    call = _call("discovery")
    payload = {"items": ["doc-1", "doc-2"]}

    store.append_tool_call(call, attempt=1, result_payload=payload)
    store.append_tool_call(call, attempt=1, result_payload=payload)
    row = conn.execute(
        "SELECT result_ref, payload_json, payload_sha256 "
        "FROM research_tool_results WHERE tool_call_id = ?",
        (call.tool_call_id,),
    ).fetchone()

    assert row["result_ref"] == call.result_ref
    assert json.loads(row["payload_json"]) == payload
    assert row["payload_sha256"] == hashlib.sha256(
        row["payload_json"].encode("utf-8")
    ).hexdigest()
    assert store.list_tool_calls(RUN_ID)[0].result_is_durable is True
    with pytest.raises(ValueError, match="different content"):
        store.append_tool_call(
            call, attempt=1, result_payload={"items": ["replaced"]}
        )


def test_a_successful_call_without_a_durable_result_fails_the_gate(
    conn: sqlite3.Connection,
) -> None:
    _insert_run(conn)
    call = _call("discovery")
    store = SqliteToolCallStore(conn)
    store.append_tool_call(call, attempt=1)
    record_artifact(conn, run_id=RUN_ID, artifact=_artifact("discovery"), attempt=1)
    record_stage_execution(
        conn, run_id=RUN_ID, execution=_execution("discovery")
    )
    evidence = load_run_evidence(conn, run_id=RUN_ID, contract_version="test-v1")

    result = evaluate_stage(
        _contract(), RUN_ID, "discovery", evidence.stages["discovery"]
    )

    assert any(v.code.value == "provenance_missing" for v in result.violations)


def test_list_tool_calls_filters_by_stage(conn: sqlite3.Connection) -> None:
    _insert_run(conn)
    store = SqliteToolCallStore(conn)
    store.append_tool_call(_call("discovery"), attempt=1)
    store.append_tool_call(_call("counter_evidence"), attempt=1)

    assert len(store.list_tool_calls(RUN_ID)) == 2
    only = store.list_tool_calls(RUN_ID, stage="discovery")
    assert [call.stage for call in only] == ["discovery"]


def test_a_tool_call_for_a_run_that_does_not_exist_is_refused(
    conn: sqlite3.Connection,
) -> None:
    store = SqliteToolCallStore(conn)
    with pytest.raises(sqlite3.IntegrityError):
        store.append_tool_call(_call("discovery", run_id="missing-run"), attempt=1)


def test_a_call_without_an_id_keeps_its_missing_provenance(
    conn: sqlite3.Connection,
) -> None:
    _insert_run(conn)
    store = SqliteToolCallStore(conn)
    store.append_tool_call(_call("discovery", tool_call_id=None), attempt=1)

    loaded = store.list_tool_calls(RUN_ID)

    # The row still needs a primary key, so the id is minted for the row.
    row = conn.execute(
        "SELECT tool_call_id FROM tool_calls WHERE run_id = ?", (RUN_ID,)
    ).fetchone()
    assert row["tool_call_id"].startswith("tc_")
    # The minted id is not provenance: the caller declared none, so the gate
    # has to see a call with no id.
    assert len(loaded) == 1
    assert loaded[0].tool_call_id is None


def test_writing_the_same_call_twice_is_idempotent(conn: sqlite3.Connection) -> None:
    _insert_run(conn)
    store = SqliteToolCallStore(conn)
    call = _call("discovery", tool_call_id=None)
    store.append_tool_call(call, attempt=1)
    store.append_tool_call(call, attempt=1)

    assert len(store.list_tool_calls(RUN_ID)) == 1


def test_writing_a_tool_call_twice_with_different_content_is_refused(
    conn: sqlite3.Connection,
) -> None:
    _insert_run(conn)
    store = SqliteToolCallStore(conn)
    store.append_tool_call(_call("discovery"), attempt=1)

    with pytest.raises(ValueError):
        store.append_tool_call(_call("discovery", result_count=99), attempt=1)

    assert store.list_tool_calls(RUN_ID)[0].result_count == 3


def test_a_stage_round_trips_through_the_database(conn: sqlite3.Connection) -> None:
    _insert_run(conn)
    call = _call("discovery", result_is_durable=True)
    artifact = _artifact("discovery", content_hash="a" * 64, schema_version="v1")
    SqliteToolCallStore(conn).append_tool_call(
        call, attempt=1, result_payload={"items": ["doc-1"]}
    )
    record_artifact(conn, run_id=RUN_ID, artifact=artifact, attempt=1)
    record_stage_execution(conn, run_id=RUN_ID, execution=_execution("discovery"))

    evidence = load_run_evidence(
        conn, run_id=RUN_ID, contract_version="test-v1"
    )

    stored = evidence.stages["discovery"]
    assert stored.tool_calls == [call]
    assert stored.artifacts == [artifact]
    assert stored.artifacts[0].produced_by_tool_call_id == "discovery-call-1"
    assert stored.artifacts[0].content_hash == "a" * 64
    assert stored.artifacts[0].schema_version == "v1"


def test_a_stage_execution_round_trips_with_its_zero_result(
    conn: sqlite3.Connection,
) -> None:
    _insert_run(conn)
    execution = _execution(
        "discovery",
        zero_result=ZeroResultOutcome(
            recorded=True,
            required_query_families=["problem"],
            completed_query_families=["problem"],
            note="no competitor wrote this down",
        ),
    )
    record_stage_execution(conn, run_id=RUN_ID, execution=execution)

    evidence = load_run_evidence(
        conn, run_id=RUN_ID, contract_version="test-v1"
    )

    stored = evidence.stages["discovery"]
    assert stored.status is StageStatus.succeeded
    assert stored.attempt == 1
    assert stored.started_at == FROZEN
    assert stored.finished_at == FROZEN + timedelta(seconds=30)
    assert stored.zero_result.recorded is True
    assert stored.zero_result.required_query_families == ["problem"]
    assert stored.zero_result.completed_query_families == ["problem"]
    assert stored.zero_result.note == "no competitor wrote this down"


def test_recording_a_stage_execution_twice_keeps_one_row(
    conn: sqlite3.Connection,
) -> None:
    _insert_run(conn)
    record_stage_execution(conn, run_id=RUN_ID, execution=_execution("discovery"))
    record_stage_execution(
        conn,
        run_id=RUN_ID,
        execution=_execution("discovery", status=StageStatus.failed),
    )

    rows = conn.execute(
        "SELECT count(*) FROM research_stage_executions WHERE run_id = ?",
        (RUN_ID,),
    ).fetchall()
    evidence = load_run_evidence(
        conn, run_id=RUN_ID, contract_version="test-v1"
    )
    assert rows[0][0] == 1
    assert evidence.stages["discovery"].status is StageStatus.failed


def test_a_second_attempt_of_a_stage_is_kept_apart(
    conn: sqlite3.Connection,
) -> None:
    _insert_run(conn)
    record_stage_execution(conn, run_id=RUN_ID, execution=_execution("discovery"))
    record_stage_execution(
        conn,
        run_id=RUN_ID,
        execution=_execution("discovery", attempt=2, status=StageStatus.failed),
    )

    rows = conn.execute(
        "SELECT attempt FROM research_stage_executions WHERE run_id = ? "
        "ORDER BY attempt",
        (RUN_ID,),
    ).fetchall()
    evidence = load_run_evidence(
        conn, run_id=RUN_ID, contract_version="test-v1"
    )
    assert [row[0] for row in rows] == [1, 2]
    # The verdict judges where the stage stands now: its latest attempt.
    assert evidence.stages["discovery"].attempt == 2


def test_a_claim_round_trips_through_the_database(conn: sqlite3.Connection) -> None:
    _insert_run(conn)
    claim = _claim()
    record_claim(conn, run_id=RUN_ID, claim=claim)

    evidence = load_run_evidence(
        conn, run_id=RUN_ID, contract_version="test-v1"
    )

    assert evidence.claims == [claim]


def test_the_evidence_of_a_run_that_wrote_nothing_is_empty(
    conn: sqlite3.Connection,
) -> None:
    evidence = load_run_evidence(
        conn, run_id="run-empty", contract_version="test-v1"
    )

    assert evidence.stages == {}
    assert evidence.claims == []


def test_evaluate_stage_gate_keeps_the_verdict(conn: sqlite3.Connection) -> None:
    _seed_run(conn)
    contract = _contract()
    evidence = load_run_evidence(
        conn, run_id=RUN_ID, contract_version=contract.contract_version
    )

    result = evaluate_stage_gate(conn, contract, evidence, "discovery")

    stored = SqliteGateResultStore(conn).list_gate_results(RUN_ID)
    assert result.passed is True
    assert len(stored) == 1
    assert stored[0].stage == "discovery"
    assert stored[0].passed is True
    assert stored[0].metrics == result.metrics
    assert stored[0].violations == result.violations


def test_the_kept_verdict_carries_the_contract_fingerprint(
    conn: sqlite3.Connection,
) -> None:
    _seed_run(conn)
    contract = _contract()
    evidence = load_run_evidence(
        conn, run_id=RUN_ID, contract_version=contract.contract_version
    )
    evaluate_run_gate(conn, contract, evidence)

    stored = load_run_gate_result(conn, run_id=RUN_ID)

    assert stored.contract_fingerprint == contract_fingerprint(contract)
    assert set(stored.stages) == set(STAGES)


def test_promotion_accepts_a_verdict_read_back_from_the_database(
    conn: sqlite3.Connection,
) -> None:
    _seed_run(conn)
    contract = _contract()
    evidence = load_run_evidence(
        conn, run_id=RUN_ID, contract_version=contract.contract_version
    )
    evaluate_run_gate(conn, contract, evidence)

    stored = load_run_gate_result(conn, run_id=RUN_ID)
    promotion = evaluate_promotion_gate(conn, contract, stored)

    assert promotion.promoted is True
    assert promotion.violations == []


def test_promotion_refuses_a_verdict_judged_under_another_contract(
    conn: sqlite3.Connection,
) -> None:
    _seed_run(conn)
    contract = _contract()
    evidence = load_run_evidence(
        conn, run_id=RUN_ID, contract_version=contract.contract_version
    )
    evaluate_run_gate(conn, contract, evidence)
    stored = load_run_gate_result(conn, run_id=RUN_ID)

    other = _contract(
        evidence_thresholds={
            **THRESHOLDS,
            "discovery": {"min_distinct_queries": 2},
        }
    )
    promotion = evaluate_promotion_gate(conn, other, stored)

    assert promotion.promoted is False
    reasons = [
        violation.detail.get("reason") for violation in promotion.violations
    ]
    assert "the run result was judged under another contract" in reasons


def test_require_promotable_returns_the_verdict_of_a_passing_run(
    conn: sqlite3.Connection,
) -> None:
    _seed_run(conn)

    promotion = require_promotable(conn, _contract(), RUN_ID)

    assert promotion.promoted is True
    assert promotion.run_id == RUN_ID


def test_require_promotable_refuses_a_run_whose_stage_did_not_pass(
    conn: sqlite3.Connection,
) -> None:
    _seed_run(conn, invalid_stages=("discovery",))

    with pytest.raises(GateBlocked) as excinfo:
        require_promotable(conn, _contract(), RUN_ID)

    assert "artifact_invalid" in str(excinfo.value)
    assert "promotion_blocked" in str(excinfo.value)


def test_require_promotable_refuses_a_run_that_produced_nothing(
    conn: sqlite3.Connection,
) -> None:
    _insert_run(conn)

    with pytest.raises(GateBlocked) as excinfo:
        require_promotable(conn, _contract(), RUN_ID)

    assert "stage_missing" in str(excinfo.value)


def test_require_promotable_refuses_a_run_that_is_not_in_the_store(
    conn: sqlite3.Connection,
) -> None:
    with pytest.raises(GateBlocked) as excinfo:
        require_promotable(conn, _contract(), "run-missing")

    assert "stage_missing" in str(excinfo.value)
    assert "run-missing" in str(excinfo.value)


def test_the_blocked_message_carries_no_untrusted_text(
    conn: sqlite3.Connection,
) -> None:
    page_text = "the page says buy cheap widgets now"
    error_text = f"upstream refused: {page_text}"
    _seed_run(conn, invalid_stages=("discovery",), invalid_reason=page_text)
    SqliteToolCallStore(conn).append_tool_call(
        _call(
            "discovery",
            tool_call_id="discovery-call-2",
            status=ToolCallStatus.failure,
            result_ref="",
            error=error_text,
        ),
        attempt=1,
    )

    with pytest.raises(GateBlocked) as excinfo:
        require_promotable(conn, _contract(), RUN_ID)

    message = str(excinfo.value)
    assert page_text not in message
    assert error_text not in message
    assert "promotion_blocked" in message


# --- the shipped contract ---------------------------------------------------

# The quality checks that read the search calls of a stage. A stage that
# names one of them has to have searched, whatever its required tools say.
_SEARCH_CHECKS = frozenset(
    {"non_empty_queries", "mandatory_query_families", "known_query_families"}
)

# The item count every fixture artifact carries. It is above every minimum
# the shipped thresholds set, so the fixture satisfies a threshold the
# contract raises.
_FIXTURE_ITEM_COUNT = 100


def _stage_minimums(contract: ResearchContract, stage: str) -> dict[str, int]:
    threshold = contract.evidence_thresholds.get(
        resolved_gate_key(contract, stage)
    )
    return threshold_fields(threshold) if threshold is not None else {}


def _family_pool(stage: str) -> list[str]:
    """The query families a stage may search, the mandatory ones first."""
    mandatory = list(MANDATORY_FAMILIES_BY_STAGE.get(stage, ()))
    return mandatory + [
        family for family in QUERY_FAMILIES if family not in mandatory
    ]


def _shipped_calls(
    contract: ResearchContract, run_id: str, stage: str
) -> list[ToolCallProvenance]:
    """The tool calls a stage of the shipped contract needs."""
    spec = contract.stages[stage]
    minimums = _stage_minimums(contract, stage)
    pool = _family_pool(stage)
    searches = max(
        minimums.get("min_distinct_queries", 0),
        minimums.get("min_query_families", 0),
        len(MANDATORY_FAMILIES_BY_STAGE.get(stage, ())),
        1 if _needs_search(spec) else 0,
    )
    fetches = max(
        minimums.get("min_source_domains", 0),
        2 if "source_diversity" in spec.quality_checks else 0,
        1 if "fetch" in spec.required_tools else 0,
    )
    calls: list[ToolCallProvenance] = []
    for index in range(searches):
        calls.append(
            ToolCallProvenance(
                run_id=run_id,
                stage=stage,
                agent="researcher",
                tool="search",
                purpose="collect evidence",
                timestamp=FROZEN + timedelta(seconds=index + 1),
                query_or_operation=f"{stage} query {index}",
                result_ref=f"raw/{stage}/search-{index}",
                result_count=3,
                status=ToolCallStatus.success,
                tool_call_id=f"{stage}-search-{index:02d}",
                query_family=pool[index % len(pool)],
            )
        )
    for index in range(fetches):
        calls.append(
            ToolCallProvenance(
                run_id=run_id,
                stage=stage,
                agent="researcher",
                tool="fetch",
                purpose="collect evidence",
                timestamp=FROZEN + timedelta(seconds=100 + index),
                query_or_operation=f"https://source{index}.test/evidence",
                result_ref=f"raw/{stage}/fetch-{index}",
                result_count=1,
                status=ToolCallStatus.success,
                tool_call_id=f"{stage}-fetch-{index:02d}",
            )
        )
    for tool in spec.required_tools:
        if tool in ("search", "fetch"):
            continue
        calls.append(
            ToolCallProvenance(
                run_id=run_id,
                stage=stage,
                agent="researcher",
                tool=tool,
                purpose="collect evidence",
                timestamp=FROZEN + timedelta(seconds=200),
                query_or_operation=f"{stage} {tool} operation",
                result_ref=f"raw/{stage}/{tool}",
                result_count=1,
                status=ToolCallStatus.success,
                tool_call_id=f"{stage}-{tool}-00",
            )
        )
    return calls


def _needs_search(spec) -> bool:
    return "search" in spec.required_tools or bool(
        _SEARCH_CHECKS & set(spec.quality_checks)
    )


def _shipped_artifacts(
    contract: ResearchContract, run_id: str, stage: str, calls: list[ToolCallProvenance]
) -> list[ArtifactRecord]:
    """The artifacts a stage of the shipped contract has to produce."""
    if not calls:
        return []
    producer = calls[0].tool_call_id
    return [
        ArtifactRecord(
            artifact_id=f"{stage}-art-{kind}",
            stage=stage,
            kind=kind,
            ref=f"data/artifacts/{run_id}/{stage}-{kind}.jsonl",
            item_count=_FIXTURE_ITEM_COUNT,
            produced_by_tool_call_id=producer,
            created_at=FROZEN,
        )
        for kind in contract.stages[stage].required_outputs
    ]


def _seed_shipped_run(
    conn: sqlite3.Connection,
    run_id: str,
    contract: ResearchContract,
    *,
    fail_stage: str | None = None,
) -> None:
    """Write a run that satisfies the shipped contract."""
    _insert_run(conn, run_id)
    for stage in contract.required_stages:
        calls = _shipped_calls(contract, run_id, stage)
        for call in calls:
            SqliteToolCallStore(conn).append_tool_call(
                call, attempt=1, result_payload={"results": [call.result_ref]}
            )
        for artifact in _shipped_artifacts(contract, run_id, stage, calls):
            record_artifact(conn, run_id=run_id, artifact=artifact, attempt=1)
        status = (
            StageStatus.failed if stage == fail_stage else StageStatus.succeeded
        )
        record_stage_execution(
            conn,
            run_id=run_id,
            execution=_execution(stage, status=status),
        )
    record_claim(
        conn,
        run_id=run_id,
        claim=ResearchClaim(
            claim_id="claim-1",
            kind=ClaimKind.opportunity_exists,
            statement="nobody serves this workflow yet",
            stage=contract.required_stages[0],
            evidence_refs=["raw/discovery"],
        ),
    )


def test_the_shipped_contract_promotes_a_run_that_satisfies_it(
    conn: sqlite3.Connection,
) -> None:
    contract = load_research_contract()
    _seed_shipped_run(conn, "run-shipped", contract)

    promotion = require_promotable(conn, contract, "run-shipped")

    assert promotion.promoted is True
    assert promotion.violations == []


def test_the_shipped_contract_refuses_a_run_whose_stage_failed(
    conn: sqlite3.Connection,
) -> None:
    contract = load_research_contract()
    _seed_shipped_run(
        conn,
        "run-shipped-failed",
        contract,
        fail_stage=contract.required_stages[-1],
    )

    with pytest.raises(GateBlocked) as excinfo:
        require_promotable(conn, contract, "run-shipped-failed")

    assert "stage_failed" in str(excinfo.value)
    assert "promotion_blocked" in str(excinfo.value)


# --- the budget of a run ----------------------------------------------------


def test_a_run_over_its_search_budget_is_blocked(conn: sqlite3.Connection) -> None:
    _seed_run(conn)
    contract = _contract(
        budgets={"version": "test-v1", "per_run": {"search_queries": 2}}
    )

    with pytest.raises(GateBlocked) as excinfo:
        require_promotable(conn, contract, RUN_ID)

    assert "budget_exceeded" in str(excinfo.value)


def test_a_run_inside_its_search_budget_is_promoted(
    conn: sqlite3.Connection,
) -> None:
    _seed_run(conn)
    contract = _contract(
        budgets={"version": "test-v1", "per_run": {"search_queries": 10}}
    )

    promotion = require_promotable(conn, contract, RUN_ID)

    assert promotion.promoted is True


def test_a_run_over_its_stage_budget_is_blocked(conn: sqlite3.Connection) -> None:
    _seed_run(conn)
    SqliteToolCallStore(conn).append_tool_call(
        _call("discovery", tool_call_id="discovery-call-2"), attempt=1
    )
    contract = _contract(
        budgets={
            "version": "test-v1",
            "per_stage": {"discovery": {"search_queries": 1}},
        }
    )

    with pytest.raises(GateBlocked) as excinfo:
        require_promotable(conn, contract, RUN_ID)

    assert "budget_exceeded" in str(excinfo.value)


def test_a_run_over_its_source_budget_is_blocked(conn: sqlite3.Connection) -> None:
    _seed_run(conn)
    store = SqliteToolCallStore(conn)
    store.append_tool_call(
        _call("discovery", tool_call_id="discovery-call-2"),
        source_id="hn_algolia",
        attempt=1,
    )
    store.append_tool_call(
        _call("discovery", tool_call_id="discovery-call-3"),
        source_id="hn_algolia",
        attempt=1,
    )
    contract = _contract(
        budgets={"version": "test-v1", "per_source": {"hn_algolia": 1}}
    )

    with pytest.raises(GateBlocked) as excinfo:
        require_promotable(conn, contract, RUN_ID)

    assert "budget_exceeded" in str(excinfo.value)


def test_the_budget_tracker_measures_the_stored_rows(
    conn: sqlite3.Connection,
) -> None:
    _seed_run(conn)
    contract = _contract(
        budgets={"version": "test-v1", "per_run": {"search_queries": 10}}
    )

    tracker = build_budget_tracker(conn, contract, RUN_ID)

    assert tracker.spent("search_queries") == 3
    assert tracker.spent("tool_calls_total") == 3
    assert tracker.spent("search_queries", stage="discovery") == 1


def test_a_budget_dimension_with_no_measurement_source_is_refused(
    conn: sqlite3.Connection,
) -> None:
    _seed_run(conn)
    contract = _contract(
        budgets={"version": "test-v1", "per_run": {"saturation_threshold": 10}}
    )

    with pytest.raises(ValueError) as excinfo:
        build_budget_tracker(conn, contract, RUN_ID)

    assert "saturation_threshold" in str(excinfo.value)


# --- the attempts of a stage ------------------------------------------------


def test_the_round_trip_verdict_matches_the_in_memory_verdict(
    conn: sqlite3.Connection,
) -> None:
    contract = _contract()
    call = _call("discovery")
    artifact = _artifact("discovery")
    execution = _execution("discovery")
    in_memory = StageExecution(
        stage="discovery",
        status=StageStatus.succeeded,
        started_at=execution.started_at,
        finished_at=execution.finished_at,
        tool_calls=[call],
        artifacts=[artifact],
    )
    expected = evaluate_stage(contract, RUN_ID, "discovery", in_memory)

    _insert_run(conn)
    SqliteToolCallStore(conn).append_tool_call(
        call, attempt=1, result_payload={"documents": ["doc-1"]}
    )
    record_artifact(conn, run_id=RUN_ID, artifact=artifact, attempt=1)
    record_stage_execution(conn, run_id=RUN_ID, execution=execution)
    evidence = load_run_evidence(
        conn, run_id=RUN_ID, contract_version=contract.contract_version
    )

    stored = evaluate_stage(
        contract, RUN_ID, "discovery", evidence.stages["discovery"]
    )

    assert stored.passed == expected.passed
    assert stored.violations == expected.violations
    assert stored.metrics == expected.metrics


def test_a_second_attempt_keeps_its_own_evidence(conn: sqlite3.Connection) -> None:
    _insert_run(conn)
    store = SqliteToolCallStore(conn)
    store.append_tool_call(_call("discovery"), attempt=1)
    record_artifact(
        conn, run_id=RUN_ID, artifact=_artifact("discovery"), attempt=1
    )
    record_stage_execution(
        conn, run_id=RUN_ID, execution=_execution("discovery", attempt=1)
    )
    store.append_tool_call(
        _call("discovery", tool_call_id="discovery-call-2"), attempt=2
    )
    record_artifact(
        conn,
        run_id=RUN_ID,
        artifact=_artifact(
            "discovery",
            artifact_id="discovery-art-2",
            produced_by_tool_call_id="discovery-call-2",
        ),
        attempt=2,
    )
    record_stage_execution(
        conn, run_id=RUN_ID, execution=_execution("discovery", attempt=2)
    )

    evidence = load_run_evidence(
        conn, run_id=RUN_ID, contract_version="test-v1"
    )
    stage = evidence.stages["discovery"]

    assert stage.attempt == 2
    assert [call.tool_call_id for call in stage.tool_calls] == ["discovery-call-2"]
    assert [artifact.artifact_id for artifact in stage.artifacts] == [
        "discovery-art-2"
    ]


def test_a_second_attempt_that_produced_nothing_fails_the_stage(
    conn: sqlite3.Connection,
) -> None:
    _insert_run(conn)
    store = SqliteToolCallStore(conn)
    store.append_tool_call(_call("discovery"), attempt=1)
    record_artifact(
        conn, run_id=RUN_ID, artifact=_artifact("discovery"), attempt=1
    )
    record_stage_execution(
        conn, run_id=RUN_ID, execution=_execution("discovery", attempt=1)
    )
    record_stage_execution(
        conn, run_id=RUN_ID, execution=_execution("discovery", attempt=2)
    )
    contract = _contract()
    evidence = load_run_evidence(
        conn, run_id=RUN_ID, contract_version=contract.contract_version
    )
    stage = evidence.stages["discovery"]

    # The stage re-ran and produced nothing, so it is judged on nothing.
    assert stage.tool_calls == []
    assert stage.artifacts == []
    result = evaluate_stage_gate(conn, contract, evidence, "discovery")

    assert result.passed is False
    codes = {violation.code.value for violation in result.violations}
    assert "tool_missing" in codes
    assert "artifact_missing" in codes


def test_a_call_with_no_declared_id_still_fails_the_provenance_check(
    conn: sqlite3.Connection,
) -> None:
    _seed_run(conn)
    SqliteToolCallStore(conn).append_tool_call(
        _call("discovery", tool_call_id=None, query_or_operation="another query"),
        attempt=1,
    )
    contract = _contract()
    evidence = load_run_evidence(
        conn, run_id=RUN_ID, contract_version=contract.contract_version
    )

    result = evaluate_stage_gate(conn, contract, evidence, "discovery")

    codes = {violation.code.value for violation in result.violations}
    assert "provenance_missing" in codes


# --- the contract a verdict is bound to -------------------------------------


def test_the_gate_result_store_refuses_a_verdict_without_a_fingerprint(
    conn: sqlite3.Connection,
) -> None:
    _insert_run(conn)
    result = GateResult(run_id=RUN_ID, stage="discovery", passed=True)

    with pytest.raises(TypeError):
        SqliteGateResultStore(conn).record_gate_result(result)


def test_a_verdict_is_bound_to_the_contract_it_was_judged_under(
    conn: sqlite3.Connection,
) -> None:
    _seed_run(conn)
    contract = _contract()
    evidence = load_run_evidence(
        conn, run_id=RUN_ID, contract_version=contract.contract_version
    )
    evaluate_run_gate(conn, contract, evidence)

    stored = load_run_gate_result(conn, run_id=RUN_ID)

    assert stored.contract_fingerprint == contract_fingerprint(contract)
    other = _contract(
        evidence_thresholds={
            **THRESHOLDS,
            "discovery": {"min_distinct_queries": 2},
        }
    )
    promotion = evaluate_promotion_gate(conn, other, stored)
    assert promotion.promoted is False


def test_a_run_judged_under_two_contracts_is_refused(
    conn: sqlite3.Connection,
) -> None:
    _seed_run(conn)
    contract = _contract()
    evidence = load_run_evidence(
        conn, run_id=RUN_ID, contract_version=contract.contract_version
    )
    evaluate_run_gate(conn, contract, evidence)
    conn.execute(
        """
        INSERT INTO stage_gate_results (
            gate_result_id, run_id, stage, passed, violations_json,
            metrics_json, contract_fingerprint, checked_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "gate-mixed",
            RUN_ID,
            "investigation",
            1,
            "[]",
            "{}",
            "another-fingerprint",
            FROZEN.isoformat(),
        ),
    )

    with pytest.raises(GateBlocked):
        load_run_gate_result(conn, run_id=RUN_ID)


def test_a_stage_verdict_under_another_contract_is_refused(
    conn: sqlite3.Connection,
) -> None:
    _seed_run(conn)
    contract = _contract()
    evidence = load_run_evidence(
        conn, run_id=RUN_ID, contract_version=contract.contract_version
    )
    evaluate_run_gate(conn, contract, evidence)
    other = _contract(
        evidence_thresholds={
            **THRESHOLDS,
            "discovery": {"min_distinct_queries": 2},
        }
    )

    with pytest.raises(GateBlocked):
        evaluate_stage_gate(conn, other, evidence, "discovery")


# --- the lease fence --------------------------------------------------------


def _insert_job(
    conn: sqlite3.Connection,
    run_id: str,
    stage: str,
    *,
    worker_id: str = "worker-1",
    lease_epoch: int = 1,
    expires_at: str = "2999-01-01T00:00:00+00:00",
) -> None:
    conn.execute(
        """
        INSERT INTO jobs (
            id, run_id, stage, payload_json, priority, status, attempts,
            max_attempts, idempotency_key, worker_id, lease_epoch,
            lease_expires_at, created_at, scheduled_for
        ) VALUES (?, ?, ?, ?, 0, 'leased', 1, 3, ?, ?, ?, ?, ?, ?)
        """,
        (
            f"job-{run_id}-{stage}",
            run_id,
            stage,
            "{}",
            f"key-{run_id}-{stage}",
            worker_id,
            lease_epoch,
            expires_at,
            FROZEN.isoformat(),
            FROZEN.isoformat(),
        ),
    )


def test_the_holder_of_the_lease_can_write_the_evidence(
    conn: sqlite3.Connection,
) -> None:
    _insert_run(conn)
    _insert_job(conn, RUN_ID, "discovery")
    store = SqliteToolCallStore(conn)

    store.append_tool_call(
        _call("discovery"), worker_id="worker-1", lease_epoch=1, attempt=1
    )

    assert len(store.list_tool_calls(RUN_ID)) == 1


def test_a_stale_worker_cannot_write_the_evidence_of_a_run(
    conn: sqlite3.Connection,
) -> None:
    _insert_run(conn)
    _insert_job(conn, RUN_ID, "discovery")
    store = SqliteToolCallStore(conn)

    with pytest.raises(LeaseRefused):
        store.append_tool_call(
            _call("discovery"), worker_id="worker-1", lease_epoch=2, attempt=1
        )
    with pytest.raises(LeaseRefused):
        store.append_tool_call(
            _call("discovery"), worker_id="worker-2", lease_epoch=1, attempt=1
        )
    with pytest.raises(LeaseRefused):
        store.append_tool_call(_call("discovery"), worker_id="worker-1", attempt=1)
    with pytest.raises(LeaseRefused):
        record_artifact(
            conn,
            run_id=RUN_ID,
            artifact=_artifact("discovery"),
            worker_id="worker-1",
            lease_epoch=2,
            attempt=1,
        )
    with pytest.raises(LeaseRefused):
        record_stage_execution(
            conn,
            run_id=RUN_ID,
            execution=_execution("discovery"),
            worker_id="worker-2",
            lease_epoch=1,
        )
    with pytest.raises(LeaseRefused):
        record_claim(
            conn,
            run_id=RUN_ID,
            claim=_claim(),
            worker_id="worker-1",
            lease_epoch=2,
        )
    with pytest.raises(LeaseRefused):
        SqliteGateResultStore(conn).record_gate_result(
            GateResult(run_id=RUN_ID, stage="discovery", passed=True),
            contract_fingerprint="fingerprint",
            worker_id="worker-1",
            lease_epoch=2,
        )


def test_an_expired_lease_refuses_a_fenced_write(conn: sqlite3.Connection) -> None:
    _insert_run(conn)
    _insert_job(
        conn, RUN_ID, "discovery", expires_at="2000-01-01T00:00:00+00:00"
    )
    store = SqliteToolCallStore(conn)

    with pytest.raises(LeaseRefused):
        store.append_tool_call(
            _call("discovery"), worker_id="worker-1", lease_epoch=1, attempt=1
        )


def test_a_refused_fenced_write_leaves_nothing_behind(
    conn: sqlite3.Connection,
) -> None:
    _insert_run(conn)
    _insert_job(conn, RUN_ID, "discovery")
    store = SqliteToolCallStore(conn)

    with pytest.raises(LeaseRefused):
        store.append_tool_call(
            _call("discovery"), worker_id="worker-1", lease_epoch=2, attempt=1
        )

    assert store.list_tool_calls(RUN_ID) == []


# --- the owner of a row -----------------------------------------------------


def test_writing_an_artifact_under_another_run_is_refused(
    conn: sqlite3.Connection,
) -> None:
    _insert_run(conn)
    _insert_run(conn, "run-2")
    SqliteToolCallStore(conn).append_tool_call(_call("discovery"), attempt=1)
    record_artifact(conn, run_id=RUN_ID, artifact=_artifact("discovery"), attempt=1)

    with pytest.raises(GateConflict):
        record_artifact(
            conn, run_id="run-2", artifact=_artifact("discovery"), attempt=1
        )

    row = conn.execute(
        "SELECT run_id FROM research_stage_artifacts WHERE artifact_id = ?",
        ("discovery-art-1",),
    ).fetchone()
    assert row["run_id"] == RUN_ID


def test_writing_a_claim_under_another_run_is_refused(
    conn: sqlite3.Connection,
) -> None:
    _insert_run(conn)
    _insert_run(conn, "run-2")
    record_claim(conn, run_id=RUN_ID, claim=_claim())

    with pytest.raises(GateConflict):
        record_claim(conn, run_id="run-2", claim=_claim())

    row = conn.execute(
        "SELECT run_id FROM research_claims WHERE claim_id = ?", ("claim-1",)
    ).fetchone()
    assert row["run_id"] == RUN_ID


# --- the run-level verdict ----------------------------------------------------


def _run_level_contract(**overrides) -> ResearchContract:
    """A contract whose run-level floor the seeded run cannot meet.

    The run-level floor is the threshold of the reserved ``promotion`` gate
    key: no stage serves it, so it is judged for the run as a whole.
    """
    thresholds = {**THRESHOLDS, "promotion": {"min_distinct_queries": 10}}
    return _contract(evidence_thresholds=thresholds, **overrides)


def test_the_run_verdict_read_back_is_the_verdict_that_was_computed(
    conn: sqlite3.Connection,
) -> None:
    _seed_run(conn)
    contract = _contract()
    evidence = load_run_evidence(
        conn, run_id=RUN_ID, contract_version=contract.contract_version
    )

    computed = evaluate_run_gate(conn, contract, evidence)
    stored = load_run_gate_result(conn, run_id=RUN_ID)

    assert computed.passed is True
    assert stored.passed == computed.passed
    assert stored.violations == computed.violations
    assert stored.stages == computed.stages


def test_the_run_verdict_read_back_carries_the_run_level_violations(
    conn: sqlite3.Connection,
) -> None:
    _seed_run(conn)
    contract = _run_level_contract()
    evidence = load_run_evidence(
        conn, run_id=RUN_ID, contract_version=contract.contract_version
    )

    computed = evaluate_run_gate(conn, contract, evidence)
    stored = load_run_gate_result(conn, run_id=RUN_ID)

    # The floor of the run is what fails, and it belongs to the run and
    # not to any stage.
    run_level = [
        violation for violation in computed.violations if violation.stage is None
    ]
    assert [violation.code.value for violation in run_level] == [
        "evidence_threshold_unmet"
    ]
    assert computed.passed is False
    # The verdict read back is the verdict that was computed.
    assert stored.passed == computed.passed
    assert stored.violations == computed.violations


def test_the_run_level_floor_blocks_the_promotion_of_a_stored_verdict(
    conn: sqlite3.Connection,
) -> None:
    _seed_run(conn)
    contract = _run_level_contract()
    evidence = load_run_evidence(
        conn, run_id=RUN_ID, contract_version=contract.contract_version
    )
    evaluate_run_gate(conn, contract, evidence)

    stored = load_run_gate_result(conn, run_id=RUN_ID)
    promotion = evaluate_promotion_gate(conn, contract, stored)

    assert promotion.promoted is False
    reasons = [violation.detail.get("reason") for violation in promotion.violations]
    assert "the run did not meet its run-level thresholds" in reasons


def test_the_run_level_verdict_is_a_row_of_its_own(
    conn: sqlite3.Connection,
) -> None:
    _seed_run(conn)
    contract = _contract()
    evidence = load_run_evidence(
        conn, run_id=RUN_ID, contract_version=contract.contract_version
    )
    evaluate_run_gate(conn, contract, evidence)

    rows = conn.execute(
        "SELECT gate_result_id, stage, contract_fingerprint "
        "FROM stage_gate_results WHERE run_id = ?",
        (RUN_ID,),
    ).fetchall()
    run_level = [row for row in rows if row["stage"] is None]
    stages = [row for row in rows if row["stage"] is not None]

    assert len(run_level) == 1
    assert len(stages) == len(STAGES)
    # The run-level row is bound to the contract the stage rows are bound to.
    assert {row["contract_fingerprint"] for row in rows} == {
        contract_fingerprint(contract)
    }
    # A caller that expects a stage verdict gets the stage verdicts.
    listed = SqliteGateResultStore(conn).list_gate_results(RUN_ID)
    assert [result.stage for result in listed] == sorted(STAGES)


def test_a_stage_named_run_does_not_collide_with_the_run_level_row(
    conn: sqlite3.Connection,
) -> None:
    _insert_run(conn)
    contract = ResearchContract.model_validate(
        {
            "contract_version": "test-v1",
            "required_stages": ["run"],
            "stages": {
                "run": {
                    "required_tools": ["search"],
                    "required_outputs": ["source_records"],
                    "quality_checks": [],
                }
            },
            "budgets": {"version": "test-v1"},
            "hard_requirements": {
                "require_qdrant_check": False,
                "require_duckdb_analysis": False,
                "require_counter_evidence": False,
                "require_competitor_search": False,
                "require_claim_evidence_links": False,
            },
            "promotion": {
                "require_competitor_gate": False,
                "require_counter_evidence_gate": False,
            },
        }
    )
    SqliteToolCallStore(conn).append_tool_call(_call("run"), attempt=1)
    record_artifact(
        conn,
        run_id=RUN_ID,
        artifact=ArtifactRecord(
            artifact_id="run-art-1",
            stage="run",
            kind="source_records",
            ref="data/artifacts/run-1/run.jsonl",
            item_count=1,
            produced_by_tool_call_id="run-call-1",
            created_at=FROZEN,
        ),
        attempt=1,
    )
    record_stage_execution(conn, run_id=RUN_ID, execution=_execution("run"))
    evidence = load_run_evidence(
        conn, run_id=RUN_ID, contract_version=contract.contract_version
    )

    evaluate_run_gate(conn, contract, evidence)

    rows = conn.execute(
        "SELECT gate_result_id, stage FROM stage_gate_results WHERE run_id = ?",
        (RUN_ID,),
    ).fetchall()
    # The stage called "run" and the verdict of the run are two rows.
    assert len(rows) == 2
    assert {row["stage"] for row in rows} == {"run", None}
    assert len({row["gate_result_id"] for row in rows}) == 2


# --- the contract a run is judged under ---------------------------------------


def test_a_run_judged_under_a_changed_contract_is_judged_again(
    conn: sqlite3.Connection,
) -> None:
    _seed_run(conn)
    contract = _contract()
    evidence = load_run_evidence(
        conn, run_id=RUN_ID, contract_version=contract.contract_version
    )
    evaluate_run_gate(conn, contract, evidence)

    other = _contract(
        evidence_thresholds={
            **THRESHOLDS,
            "discovery": {"min_distinct_queries": 2},
        }
    )
    judged = evaluate_run_gate(conn, other, evidence)

    stored = load_run_gate_result(conn, run_id=RUN_ID)
    assert stored.contract_fingerprint == contract_fingerprint(other)
    assert stored.passed == judged.passed
    assert stored.violations == judged.violations
    assert stored.stages["discovery"].passed is False


def test_a_run_judged_under_a_changed_contract_is_promoted_again(
    conn: sqlite3.Connection,
) -> None:
    _seed_run(conn)
    require_promotable(conn, _contract(), RUN_ID)
    other = _contract(
        evidence_thresholds={
            **THRESHOLDS,
            "discovery": {"min_distinct_queries": 2},
        }
    )

    with pytest.raises(GateBlocked) as excinfo:
        require_promotable(conn, other, RUN_ID)

    # The run was judged again under the other contract, and it failed
    # because of the stage, not because it was bound to another contract.
    assert "another contract" not in str(excinfo.value)
    assert "evidence_threshold_unmet" in str(excinfo.value)


def test_a_refused_second_contract_leaves_the_stored_set_untouched(
    conn: sqlite3.Connection,
) -> None:
    _seed_run(conn)
    contract = _contract()
    evidence = load_run_evidence(
        conn, run_id=RUN_ID, contract_version=contract.contract_version
    )
    evaluate_run_gate(conn, contract, evidence)
    other = _contract(
        evidence_thresholds={
            **THRESHOLDS,
            "discovery": {"min_distinct_queries": 2},
        }
    )

    # One stage on its own would give the run two contracts, so it is
    # refused.
    with pytest.raises(GateBlocked):
        evaluate_stage_gate(conn, other, evidence, "discovery")

    stored = load_run_gate_result(conn, run_id=RUN_ID)
    assert stored.contract_fingerprint == contract_fingerprint(contract)
    assert stored.passed is True


# --- the lease fence on the verdict writes ------------------------------------


def test_a_stale_worker_cannot_write_the_verdicts_of_a_run(
    conn: sqlite3.Connection,
) -> None:
    _seed_run(conn)
    _insert_job(conn, RUN_ID, "discovery")
    contract = _contract()
    evidence = load_run_evidence(
        conn, run_id=RUN_ID, contract_version=contract.contract_version
    )

    with pytest.raises(LeaseRefused):
        evaluate_run_gate(
            conn, contract, evidence, worker_id="worker-1", lease_epoch=2
        )
    with pytest.raises(LeaseRefused):
        evaluate_run_gate(
            conn, contract, evidence, worker_id="worker-2", lease_epoch=1
        )
    with pytest.raises(LeaseRefused):
        evaluate_stage_gate(
            conn, contract, evidence, "discovery", worker_id="worker-2", lease_epoch=1
        )
    with pytest.raises(LeaseRefused):
        require_promotable(conn, contract, RUN_ID, worker_id="worker-1")
    with pytest.raises(LeaseRefused):
        require_promotable(conn, contract, RUN_ID, lease_epoch=1)


def test_a_refused_write_leaves_the_previous_verdicts_untouched(
    conn: sqlite3.Connection,
) -> None:
    _seed_run(conn)
    contract = _contract()
    evidence = load_run_evidence(
        conn, run_id=RUN_ID, contract_version=contract.contract_version
    )
    evaluate_run_gate(conn, contract, evidence)
    other = _contract(
        evidence_thresholds={
            **THRESHOLDS,
            "discovery": {"min_distinct_queries": 2},
        }
    )
    # The worker holds the lease of the first stage only, so the write of
    # the whole set is refused and the set the run carried stays.
    _insert_job(conn, RUN_ID, "discovery")

    with pytest.raises(LeaseRefused):
        evaluate_run_gate(
            conn, other, evidence, worker_id="worker-1", lease_epoch=1
        )

    stored = load_run_gate_result(conn, run_id=RUN_ID)
    assert stored.contract_fingerprint == contract_fingerprint(contract)
    assert stored.passed is True
    assert stored.violations == []


# --- the measurement of a run -------------------------------------------------


def test_the_dimensions_the_run_recorded_no_spend_for_are_named(
    conn: sqlite3.Connection,
) -> None:
    _seed_run(conn)
    contract = _contract(
        budgets={
            "version": "test-v1",
            "per_run": {"search_queries": 10, "usd_cost": 5.0},
        }
    )

    tracker = build_budget_tracker(conn, contract, RUN_ID)

    # The run recorded its searches, and it recorded no cost at all. The
    # cost was not measured, and the dimension says so.
    assert tracker.spent("search_queries") == 3
    assert tracker.spent("usd_cost") == 0.0
    assert unmeasured_budget_dimensions(conn, contract, RUN_ID) == ("usd_cost",)
    assert measure_run_spend(conn, RUN_ID)["search_queries"] == 3
    assert "usd_cost" not in measure_run_spend(conn, RUN_ID)


def test_the_promotion_verdict_carries_the_measurement_report(
    conn: sqlite3.Connection,
) -> None:
    _seed_run(conn)
    contract = _contract(
        budgets={
            "version": "test-v1",
            "per_run": {"search_queries": 10, "usd_cost": 5.0},
        }
    )

    promotion = require_promotable(conn, contract, RUN_ID)

    # The gap is reported and the verdict is the one the gate reached.
    assert promotion.promoted is True
    assert promotion.unmeasured_dimensions == ("usd_cost",)
    assert promotion.violations == []


def test_the_shipped_contract_reports_the_dimensions_it_never_recorded(
    conn: sqlite3.Connection,
) -> None:
    contract = load_research_contract()
    _seed_shipped_run(conn, "run-shipped-report", contract)

    promotion = require_promotable(conn, contract, "run-shipped-report")

    assert promotion.promoted is True
    # The ledger has no writer, so the dimensions it holds were never
    # recorded. The run is promoted anyway: the gap is not a failure.
    assert "llm_input_tokens" in promotion.unmeasured_dimensions
    assert "usd_cost" in promotion.unmeasured_dimensions


def test_the_model_calls_are_counted_from_the_execution_log(
    conn: sqlite3.Connection,
) -> None:
    _seed_run(conn)
    conn.execute(
        """
        INSERT INTO model_executions (
            execution_id, run_id, stage, role, provider_id, model_id,
            selection_source, latency_ms, status, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "model-execution-1",
            RUN_ID,
            "discovery",
            "researcher",
            "opencode",
            "model",
            "manual",
            10,
            "ok",
            FROZEN.isoformat(),
        ),
    )
    conn.execute(
        """
        INSERT INTO spend_ledger (
            entry_id, run_id, stage, tool, units, unit_type, cost_micros, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "ledger-entry-1",
            RUN_ID,
            "discovery",
            "llm",
            1,
            "model_calls",
            0,
            FROZEN.isoformat(),
        ),
    )
    contract = _contract(
        budgets={"version": "test-v1", "per_run": {"model_calls": 10}}
    )

    tracker = build_budget_tracker(conn, contract, RUN_ID)

    # The ledger holds the same call, and the two sources are not added
    # together: one call is one call.
    assert tracker.spent("model_calls") == 1


# --- the attempt of a tool call -----------------------------------------------


def test_a_call_replayed_under_a_new_attempt_is_a_second_row(
    conn: sqlite3.Connection,
) -> None:
    _insert_run(conn)
    store = SqliteToolCallStore(conn)
    call = _call("discovery", tool_call_id=None)

    store.append_tool_call(call, attempt=1)
    store.append_tool_call(call, attempt=2)

    assert len(store.list_tool_calls(RUN_ID)) == 2


def test_a_call_without_an_attempt_is_refused(conn: sqlite3.Connection) -> None:
    _insert_run(conn)
    store = SqliteToolCallStore(conn)

    with pytest.raises(TypeError):
        store.append_tool_call(_call("discovery"))

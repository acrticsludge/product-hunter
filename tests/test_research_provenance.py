from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from product_hunter.research.evidence import (
    ArtifactRecord,
    ResearchClaim,
    StageExecution,
    StageStatus,
    ToolCallProvenance,
    ToolCallStatus,
    ZeroResultOutcome,
)
from product_hunter.research.persistence import ToolCallStore

FROZEN = datetime(2026, 10, 21, 12, 0, 0, tzinfo=timezone.utc)
NAIVE = datetime(2026, 10, 21, 12, 0, 0)


def _call(**overrides) -> ToolCallProvenance:
    data = {
        "run_id": "run-1",
        "stage": "discovery",
        "agent": "researcher",
        "tool": "search",
        "purpose": "find pain point evidence",
        "timestamp": FROZEN,
        "query_or_operation": "spreadsheet pain",
        "result_ref": "raw/8f14e45f",
        "result_count": 27,
        "status": ToolCallStatus.success,
        "tool_call_id": "call-1",
        "query_family": "problem",
    }
    data.update(overrides)
    return ToolCallProvenance(**data)


class MemoryToolCallStore:
    """The in-memory store the SQLite wave will replace."""

    def __init__(self) -> None:
        self.calls: list[ToolCallProvenance] = []

    def append_tool_call(
        self,
        call: ToolCallProvenance,
        *,
        attempt: int,
        source_id: str | None = None,
    ) -> None:
        self.calls.append(call)

    def list_tool_calls(
        self, run_id: str, *, stage: str | None = None
    ) -> list[ToolCallProvenance]:
        return [
            call
            for call in self.calls
            if call.run_id == run_id and (stage is None or call.stage == stage)
        ]


def test_success_without_result_ref_is_rejected():
    with pytest.raises(ValidationError) as excinfo:
        _call(result_ref="")
    assert "result_ref" in str(excinfo.value)
    assert "result_ref is required" in str(excinfo.value)


def test_failure_may_carry_an_empty_result_ref():
    call = _call(status=ToolCallStatus.failure, result_ref="", error="upstream refused")
    assert call.result_ref == ""
    assert call.status is ToolCallStatus.failure


def test_result_ref_with_spaces_is_rejected():
    with pytest.raises(ValidationError) as excinfo:
        _call(result_ref="raw/8f14e45f a summary of the results")
    assert "result_ref must not contain whitespace" in str(excinfo.value)


def test_result_ref_with_a_quote_is_rejected():
    with pytest.raises(ValidationError) as excinfo:
        _call(result_ref='raw/8f14e45f"')
    assert "result_ref must match" in str(excinfo.value)


def test_naive_timestamp_is_rejected():
    with pytest.raises(ValidationError) as excinfo:
        _call(timestamp=NAIVE)
    assert "timestamp must be timezone-aware" in str(excinfo.value)


def test_negative_result_count_is_rejected():
    with pytest.raises(ValidationError) as excinfo:
        _call(result_count=-1)
    assert "result_count" in str(excinfo.value)


def test_empty_optional_string_is_rejected():
    with pytest.raises(ValidationError) as excinfo:
        _call(tool_call_id="")
    assert "tool_call_id must not be empty" in str(excinfo.value)


def test_a_summary_field_cannot_accompany_the_record():
    with pytest.raises(ValidationError) as excinfo:
        _call(summary="found 27 results about spreadsheet pain")
    assert "summary" in str(excinfo.value)


def test_a_valid_call_round_trips():
    call = _call()
    restored = ToolCallProvenance.model_validate(call.model_dump())
    assert restored == call
    assert restored.timestamp == FROZEN
    assert restored.tool_call_id == "call-1"


def test_artifact_keeps_the_tool_call_it_came_from():
    artifact = ArtifactRecord(
        artifact_id="art-1",
        stage="discovery",
        kind="source_records",
        ref="data/artifacts/run-1/source_records.jsonl",
        item_count=12,
        produced_by_tool_call_id="call-1",
        created_at=FROZEN,
    )
    assert artifact.produced_by_tool_call_id == "call-1"
    assert artifact.item_count == 12
    assert artifact.valid is True


def test_artifact_with_a_negative_item_count_is_rejected():
    with pytest.raises(ValidationError) as excinfo:
        ArtifactRecord(
            artifact_id="art-1",
            stage="discovery",
            kind="source_records",
            ref="data/artifacts/run-1/source_records.jsonl",
            item_count=-1,
            created_at=FROZEN,
        )
    assert "item_count" in str(excinfo.value)


def test_stage_execution_holds_its_calls_and_artifacts():
    execution = StageExecution(
        stage="discovery",
        status=StageStatus.succeeded,
        started_at=FROZEN,
        finished_at=FROZEN,
        tool_calls=[_call()],
        zero_result=ZeroResultOutcome(recorded=True, required_query_families=["problem"]),
    )
    assert execution.tool_calls[0].tool_call_id == "call-1"
    assert execution.zero_result.recorded is True
    assert execution.attempt == 1
    assert execution.artifacts == []


def test_a_stage_with_naive_timestamps_is_rejected():
    with pytest.raises(ValidationError) as excinfo:
        StageExecution(
            stage="discovery",
            status=StageStatus.succeeded,
            started_at=NAIVE,
            finished_at=NAIVE,
        )

    assert "started_at must be timezone-aware" in str(excinfo.value)
    assert "finished_at must be timezone-aware" in str(excinfo.value)


def test_claim_without_evidence_refs_is_allowed_at_construction():
    claim = ResearchClaim(
        claim_id="claim-1",
        kind="no_strong_competitor",
        statement="no competitor ships this",
        stage="investigation",
    )
    assert claim.evidence_refs == []


def test_tool_call_store_round_trips_calls():
    store = MemoryToolCallStore()
    assert isinstance(store, ToolCallStore)
    store.append_tool_call(_call(), attempt=1)
    store.append_tool_call(_call(stage="validation", tool_call_id="call-2"), attempt=1)
    store.append_tool_call(_call(run_id="run-2", tool_call_id="call-3"), attempt=1)

    every = store.list_tool_calls("run-1")
    assert [call.tool_call_id for call in every] == ["call-1", "call-2"]
    only = store.list_tool_calls("run-1", stage="validation")
    assert [call.tool_call_id for call in only] == ["call-2"]


def test_the_store_is_called_through_the_protocol_signature():
    store: ToolCallStore = MemoryToolCallStore()

    # The call is made through the protocol's own signature. The isinstance
    # check above only proves the names of the methods, so a drift between
    # the protocol and the implementation is caught here.
    store.append_tool_call(_call(), attempt=2, source_id="hn_algolia")

    every = store.list_tool_calls("run-1")
    assert [call.tool_call_id for call in every] == ["call-1"]


def test_the_attempt_of_a_call_is_required():
    store = MemoryToolCallStore()

    with pytest.raises(TypeError):
        store.append_tool_call(_call())

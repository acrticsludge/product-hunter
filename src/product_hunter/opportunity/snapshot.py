"""The stored record of one score: the snapshot, its provenance, and its id.

The snapshot id is a content hash of everything the record holds that can
vary: the canonical inputs, the policy, the reference time, and the ids of
the input snapshots the record names. The policy goes in by its body, so an
unbumped policy edit cannot serve a stale id; the reference time goes in as
the same instant in UTC; the input ids are sorted, so their order cannot
leak into the id. The same id names the same record, and a different score
has a different id.

The id is deliberately coarser than the score it names: it moves for any
change to the policy body, including one that cannot move the score.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Sequence
from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, Field, model_validator

from product_hunter.opportunity.policy import ScoringPolicy
from product_hunter.opportunity.scoring import (
    ComponentScore,
    NicheOverride,
    ScoreProvenance,
    ScoringError,
    score_confidence,
    score_potential,
)
from product_hunter.opportunity.signals import (
    CompetitionDirection,
    ConfidenceInputs,
    IndependenceSummary,
    MarketEstimate,
    PotentialInputs,
    TrendSignal,
    WtpTier,
    compute_trend,
)


class ScoreSnapshot(BaseModel):
    """One scored opportunity, with everything the score was read from."""

    model_config = ConfigDict(extra="forbid")

    snapshot_id: str = Field(min_length=1)
    policy_version: str = Field(min_length=1)
    input_snapshot_ids: list[str] = Field(default_factory=list)
    potential: float = Field(ge=0, le=100)
    potential_low: float = Field(ge=0, le=100)
    potential_high: float = Field(ge=0, le=100)
    confidence: float = Field(ge=0, le=100)
    confidence_low: float = Field(ge=0, le=100)
    confidence_high: float = Field(ge=0, le=100)
    components: list[ComponentScore] = Field(default_factory=list)
    provenance: list[ScoreProvenance] = Field(default_factory=list)
    independence: IndependenceSummary
    wtp_tier: WtpTier
    market_estimate: MarketEstimate
    competition_direction: CompetitionDirection
    trend: TrendSignal
    niche_override: NicheOverride | None = None
    created_at: datetime

    @model_validator(mode="after")
    def _ranges_are_ordered(self) -> ScoreSnapshot:
        if not self.potential_low <= self.potential <= self.potential_high:
            raise ValueError(
                "the potential range is not ordered: "
                f"low {self.potential_low}, score {self.potential}, "
                f"high {self.potential_high}"
            )
        if not self.confidence_low <= self.confidence <= self.confidence_high:
            raise ValueError(
                "the confidence range is not ordered: "
                f"low {self.confidence_low}, score {self.confidence}, "
                f"high {self.confidence_high}"
            )
        return self


def _sort_by(key: str) -> Callable[[list], list]:
    """Return a sorter that puts records in the order of one of their fields."""

    def sorter(records: list) -> list:
        return sorted(records, key=lambda record: record[key])

    return sorter


def _sort_signals(signals: list) -> list:
    """Put the willingness-to-pay signals and their evidence ids in order."""
    return sorted(
        (
            {**signal, "evidence_ids": sorted(signal["evidence_ids"])}
            for signal in signals
        ),
        key=lambda signal: (
            signal["kind"],
            signal["detail"],
            signal["evidence_ids"],
        ),
    )


# The lists whose order carries no meaning, and the rule that puts each one
# in a canonical order. The path is the chain of keys that leads to the list.
# This table is the only place that knows them, so the canonical form cannot
# drift as fields are added, and a list that does carry meaning is left
# alone.
_LIST_SORTERS: tuple[tuple[tuple[str, ...], Callable[[list], list]], ...] = (
    (("pain_observations",), _sort_by("pain_id")),
    (("evidence",), _sort_by("evidence_id")),
    (("buildability", "blockers"), sorted),
    (("competition", "evidence_ids"), sorted),
    (("wtp", "signals"), _sort_signals),
)


def _sort_at_path(data: dict, path: tuple[str, ...], sorter: Callable[[list], list]) -> None:
    """Apply one sorter to the list at the path, when the path is there."""
    target = data
    for key in path[:-1]:
        target = target.get(key) if isinstance(target, dict) else None
        if not isinstance(target, dict):
            return
    leaf = path[-1]
    if isinstance(target.get(leaf), list):
        target[leaf] = sorter(target[leaf])


def _canonical_inputs(inputs: BaseModel) -> dict:
    """Return the inputs in the canonical form the snapshot id hashes.

    The order of a list of records carries no meaning, so those lists are
    sorted: the same records in another order are the same input, and the
    same input has the same id.
    """
    data = inputs.model_dump(mode="json")
    for path, sorter in _LIST_SORTERS:
        _sort_at_path(data, path, sorter)
    return data


def snapshot_payload(
    potential_inputs: PotentialInputs,
    confidence_inputs: ConfidenceInputs,
    policy: ScoringPolicy,
    *,
    as_of: datetime,
    input_snapshot_ids: Sequence[str],
) -> dict:
    """Return the canonical payload the snapshot id hashes.

    The module docstring says what the payload covers and why.
    """
    if as_of.tzinfo is None or as_of.tzinfo.utcoffset(as_of) is None:
        raise ScoringError(
            "as_of_not_aware",
            "as_of must be timezone-aware: a naive timestamp cannot be "
            "compared with an aware one",
        )
    return {
        "policy": policy.model_dump(mode="json"),
        "as_of": as_of.astimezone(timezone.utc).isoformat(),
        "input_snapshot_ids": sorted(input_snapshot_ids),
        "potential": _canonical_inputs(potential_inputs),
        "confidence": _canonical_inputs(confidence_inputs),
    }


def snapshot_id(
    potential_inputs: PotentialInputs,
    confidence_inputs: ConfidenceInputs,
    policy: ScoringPolicy,
    *,
    as_of: datetime,
    input_snapshot_ids: Sequence[str],
) -> str:
    """Return the id of the snapshot the inputs make under the policy.

    The module docstring says what the id covers and why.
    """
    payload = json.dumps(
        snapshot_payload(
            potential_inputs,
            confidence_inputs,
            policy,
            as_of=as_of,
            input_snapshot_ids=input_snapshot_ids,
        ),
        sort_keys=True,
    )
    return "snap_" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def build_snapshot(
    potential_inputs: PotentialInputs,
    confidence_inputs: ConfidenceInputs,
    policy: ScoringPolicy,
    *,
    input_snapshot_ids: Sequence[str] = (),
    as_of: datetime,
) -> ScoreSnapshot:
    """Build the stored record of one score.

    The module docstring says what the id covers and why.
    """
    potential = score_potential(potential_inputs, policy)
    confidence = score_confidence(confidence_inputs, policy, as_of=as_of)
    trend = compute_trend(confidence_inputs.evidence)
    # The ids are references, not a sequence, so they are sorted once and
    # the same list is hashed and stored.
    source_ids = sorted(input_snapshot_ids)

    return ScoreSnapshot(
        snapshot_id=snapshot_id(
            potential_inputs,
            confidence_inputs,
            policy,
            as_of=as_of,
            input_snapshot_ids=source_ids,
        ),
        policy_version=policy.policy_version,
        input_snapshot_ids=source_ids,
        potential=potential.score,
        potential_low=potential.low,
        potential_high=potential.high,
        confidence=confidence.score,
        confidence_low=confidence.low,
        confidence_high=confidence.high,
        components=potential.components + confidence.components,
        provenance=potential.provenance + confidence.provenance,
        independence=confidence.independence,
        wtp_tier=potential_inputs.wtp.tier,
        market_estimate=potential_inputs.market,
        competition_direction=potential_inputs.competition.direction,
        trend=trend,
        niche_override=potential.niche_override,
        # The reference time is stored as the same instant in UTC, so the
        # record does not carry the offset the caller happened to use.
        created_at=as_of.astimezone(timezone.utc),
    )

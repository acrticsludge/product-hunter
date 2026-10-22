"""The behaviour of the clustering engine, row by row of the spec's matrix.

The two doubles live here and not in the package: a scripted embedder that
answers a vector per canonical text, and a scripted adjudicator that answers
a decision per pair of pain ids. No test in this file reaches a network, a
model or an embedding package.
"""

from __future__ import annotations

import hashlib
import math
import re
from datetime import datetime, timezone

import pytest
from pydantic import BaseModel

from product_hunter.clustering import clustering_artifacts
from product_hunter.clustering.adjudication import Adjudication, AdjudicationDecision
from product_hunter.clustering.clusters import DecisionKind, HeldReason
from product_hunter.clustering.engine import ClusteringEngine
from product_hunter.clustering.problems import PainPoint, canonical_text
from product_hunter.clustering.similarity import MixedVectorSpaceError, VectorSpace
from product_hunter.config import ClusteringConfig
from product_hunter.normalization import Domain

CLUSTER_ID = re.compile(r"^cl_[0-9a-f]{16}$")
DIMENSIONS = 8


def _axis(index: int) -> tuple[float, ...]:
    """A unit vector on one axis."""
    return tuple(1.0 if position == index else 0.0 for position in range(DIMENSIONS))


def _towards(cosine: float, index: int = 0) -> tuple[float, ...]:
    """A unit vector at the given cosine to the axis vector."""
    offset = math.sqrt(max(0.0, 1.0 - cosine * cosine))
    return tuple(
        (cosine if position == index else 0.0)
        + (offset if position == (index + 1) % DIMENSIONS else 0.0)
        for position in range(DIMENSIONS)
    )


def _space(model: str = "a-model", version: str = "1") -> VectorSpace:
    return VectorSpace.of(
        provider="local", model=model, model_version=version, dimensions=DIMENSIONS
    )


def _config(**overrides) -> ClusteringConfig:
    data = {
        "version": 1,
        "algorithm_version": "cluster-v1",
        "prompt_version": "cluster-prompt-v1",
        "embedding": {
            "provider": "local",
            "model": "a-model",
            "model_version": "1",
            "dimensions": DIMENSIONS,
        },
        "similarity": {
            "merge_threshold": 0.86,
            "ambiguous_threshold": 0.72,
            "domain_mismatch_penalty": 0.15,
            "max_candidates": 5,
            "min_neighbors_checked": 5,
        },
        "adjudication": {"role": "clustering", "enabled": True, "max_calls": 50},
    }
    similarity = dict(data["similarity"])
    similarity.update(overrides.pop("similarity", {}))
    data["similarity"] = similarity
    adjudication = dict(data["adjudication"])
    adjudication.update(overrides.pop("adjudication", {}))
    data["adjudication"] = adjudication
    data.update(overrides)
    return ClusteringConfig.model_validate(data)


def _pain(**overrides) -> PainPoint:
    data = {
        "pain_id": "pp-1",
        "doc_id": "doc-1",
        "source_id": "src-1",
        "span_id": "sp-1",
        "quote": "we paste the invoice totals by hand every month",
        "domain": Domain.finance,
        "actor": "the bookkeeping team",
        "action": "paste invoice totals by hand",
        "object": "invoice totals",
        "consequence": "a Saturday of manual work every month",
        "pain_statement": "the bookkeeping team pastes invoice totals by hand",
        "normalized_key": "finance:paste-invoice-totals-by-hand:invoice-totals",
        "normalization_version": "norm-v1",
    }
    data.update(overrides)
    return PainPoint(**data)


class ScriptedEmbedder:
    """Answers a vector per canonical text from a table the test writes.

    A text that is not in the table gets a deterministic fallback vector, so
    an unexpected comparison fails a test loudly instead of silently scoring
    zero.
    """

    def __init__(
        self, vectors: dict[str, tuple[float, ...]], space: VectorSpace
    ) -> None:
        self.vectors = vectors
        self._space = space
        self.calls: list[list[str]] = []

    @property
    def space(self) -> VectorSpace:
        return self._space

    def embed(self, texts):
        self.calls.append(list(texts))
        return [self.vectors.get(text, self._fallback(text)) for text in texts]

    def _fallback(self, text: str) -> tuple[float, ...]:
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        return tuple(byte / 255.0 for byte in digest[:DIMENSIONS])


class ScriptedAdjudicator:
    """Answers a decision per pair of pain ids.

    A pair that is not in the table gets the default the test sets, so the
    ambiguous path is reachable without a special case. A value that is an
    exception is raised, which is how the failure path is reached.
    """

    def __init__(
        self,
        decisions: dict[tuple[str, str], object] | None = None,
        default: object = AdjudicationDecision.same,
    ) -> None:
        self.decisions = decisions or {}
        self.default = default
        self.calls: list[tuple[str, str]] = []

    def adjudicate(self, left, right) -> Adjudication:
        key = tuple(sorted((left.pain_id, right.pain_id)))
        self.calls.append(key)
        answer = self.decisions.get(key, self.default)
        if isinstance(answer, Exception):
            raise answer
        return Adjudication(
            decision=answer,
            confidence=0.9,
            reason="the scripted answer",
            provider="test-provider",
            model="test-model",
            model_version="test-model-v1",
            prompt_version="cluster-prompt-v1",
            config_version="clustering-v1-test",
        )


def _embedder(*pairs) -> ScriptedEmbedder:
    """An embedder that answers the given vector for each pain point."""
    vectors = {canonical_text(pain): vector for pain, vector in pairs}
    return ScriptedEmbedder(vectors, _space())


def _pair(cosine: float) -> tuple[tuple[float, ...], tuple[float, ...]]:
    """Two vectors whose cosine is the given one."""
    return _axis(0), _towards(cosine, 0)


def _basis(result) -> dict[str, str]:
    return {assignment.pain_id: assignment.basis for assignment in result.assignments}


# --- the matrix ------------------------------------------------------------


def test_obvious_duplicates_join_one_cluster() -> None:
    first = _pain(pain_id="pp-1", doc_id="doc-1")
    second = _pain(pain_id="pp-2", doc_id="doc-2")
    left, right = _pair(0.99)
    result = ClusteringEngine(
        _embedder((first, left), (second, right)), _config()
    ).cluster([first, second])
    assert len(result.clusters) == 1
    assert result.clusters[0].member_ids == ["pp-1", "pp-2"]
    assert _basis(result)["pp-2"] == "identical_normalized_fields"


def test_the_same_words_in_two_domains_do_not_merge() -> None:
    first = _pain(pain_id="pp-1", domain=Domain.software, actor="the platform team")
    second = _pain(pain_id="pp-2", domain=Domain.finance, actor="the bookkeeping team")
    left, right = _pair(0.95)
    adjudicator = ScriptedAdjudicator(default=AdjudicationDecision.different)
    result = ClusteringEngine(
        _embedder((first, left), (second, right)), _config(), adjudicator
    ).cluster([first, second])
    assert len(result.clusters) == 2
    assert adjudicator.calls == [("pp-1", "pp-2")]


def test_the_domain_penalty_keeps_two_domains_out_of_the_merge_band() -> None:
    first = _pain(pain_id="pp-1", domain=Domain.software, actor="the platform team")
    second = _pain(pain_id="pp-2", domain=Domain.finance, actor="the bookkeeping team")
    left, right = _pair(0.85)
    # The penalty takes the score under the ambiguous threshold, so the pair
    # is never a candidate and no adjudicator is asked.
    result = ClusteringEngine(
        _embedder((first, left), (second, right)), _config()
    ).cluster([first, second])
    assert len(result.clusters) == 2
    assert result.held == []
    assert _basis(result)["pp-2"] == "new_cluster"


def test_the_same_problem_in_other_words_stays_together() -> None:
    first = _pain(pain_id="pp-1", doc_id="doc-1", quote="the first sentence")
    second = _pain(
        pain_id="pp-2",
        doc_id="doc-2",
        quote="the second sentence",
        action="re-key the invoice totals",
        pain_statement="the bookkeeping team re-keys the invoice totals",
    )
    left, right = _pair(0.80)
    adjudicator = ScriptedAdjudicator(default=AdjudicationDecision.same)
    result = ClusteringEngine(
        _embedder((first, left), (second, right)), _config(), adjudicator
    ).cluster([first, second])
    assert len(result.clusters) == 1
    assert result.clusters[0].member_ids == ["pp-1", "pp-2"]
    assert _basis(result)["pp-2"] == "adjudicated_same"


def test_two_pain_points_with_no_shared_words_stay_together() -> None:
    first = _pain(pain_id="pp-1", doc_id="doc-1", quote="the first sentence")
    second = _pain(
        pain_id="pp-2",
        doc_id="doc-2",
        quote="the second sentence",
        actor="the controller",
        action="reconcile the ledger by spreadsheet",
        object="ledger",
        consequence="a week of corrections every quarter",
        pain_statement="the controller reconciles the ledger by spreadsheet",
    )
    left, right = _pair(0.75)
    adjudicator = ScriptedAdjudicator(default=AdjudicationDecision.same)
    result = ClusteringEngine(
        _embedder((first, left), (second, right)), _config(), adjudicator
    ).cluster([first, second])
    assert len(result.clusters) == 1
    assert result.clusters[0].member_ids == ["pp-1", "pp-2"]


def test_the_domain_penalty_does_not_depend_on_the_anchor() -> None:
    """A cluster that holds two domains is penalised the same way in both id orders.

    The two members sit in different domains and the newcomer sits in the
    domain of one of them. The penalty on the centroid of such a cluster may
    not depend on which member has the smallest pain id: the newcomer is
    closest to the centroid, and the centroid carries the penalty because a
    member sits in another domain.
    """
    angle = math.radians(13.0)
    member = (math.cos(angle), -math.sin(angle), 0.0) + (0.0,) * (DIMENSIONS - 3)
    other = (math.cos(angle), math.sin(angle), 0.0) + (0.0,) * (DIMENSIONS - 3)
    newcomer = (1.0, 0.0, 0.0) + (0.0,) * (DIMENSIONS - 3)
    scores = []
    for ids in (("pp-1", "pp-2", "pp-3"), ("pp-2", "pp-1", "pp-3")):
        first = _pain(
            pain_id=ids[0],
            doc_id="doc-1",
            quote="the first sentence",
            domain=Domain.software,
            actor="the platform team",
            action="roll the release by hand",
            object="the release",
            consequence="an hour of work every week",
            pain_statement="the platform team rolls the release by hand",
        )
        second = _pain(
            pain_id=ids[1],
            doc_id="doc-2",
            quote="the second sentence",
            domain=Domain.finance,
            actor="the controller",
            action="reconcile the ledger by spreadsheet",
            object="the ledger",
            consequence="a week of corrections every quarter",
            pain_statement="the controller reconciles the ledger by spreadsheet",
        )
        third = _pain(
            pain_id=ids[2],
            doc_id="doc-3",
            quote="the third sentence",
            domain=Domain.software,
            actor="the platform team",
            action="roll the release by script",
            object="the release",
            consequence="an hour of work every week",
            pain_statement="the platform team rolls the release by script",
        )
        embedder = _embedder(
            (first, member), (second, other), (third, newcomer)
        )
        adjudicator = ScriptedAdjudicator(default=AdjudicationDecision.same)
        result = ClusteringEngine(
            embedder, _config(), adjudicator
        ).cluster([first, second, third])
        # The two members are one cluster, and the newcomer joins it.
        assert len(result.clusters) == 1
        assert result.clusters[0].member_ids == ["pp-1", "pp-2", "pp-3"]
        assignment = next(
            item for item in result.assignments if item.pain_id == "pp-3"
        )
        scores.append(assignment.confidence)
    # The newcomer is closest to the centroid, and the centroid carries the
    # penalty: the score is the one of the member in its own domain.
    assert scores[0] == pytest.approx(scores[1])
    assert scores[0] == pytest.approx(math.cos(angle), abs=0.002)


def test_a_vector_of_another_length_stops_the_run() -> None:
    class _WrongLength(ScriptedEmbedder):
        def embed(self, texts):
            return [tuple(1.0 for _ in range(2)) for _ in texts]

    pain = _pain(pain_id="pp-1")
    with pytest.raises(MixedVectorSpaceError):
        ClusteringEngine(_WrongLength({}, _space()), _config()).cluster([pain])


def test_a_budget_of_no_calls_holds_the_pain_point() -> None:
    first = _pain(pain_id="pp-1")
    second = _pain(
        pain_id="pp-2",
        doc_id="doc-2",
        quote="the second sentence",
        action="re-key the invoice totals",
    )
    left, right = _pair(0.80)
    adjudicator = ScriptedAdjudicator(default=AdjudicationDecision.same)
    result = ClusteringEngine(
        _embedder((first, left), (second, right)),
        _config(adjudication={"max_calls": 0}),
        adjudicator,
    ).cluster([first, second])
    assert len(result.held) == 1
    assert result.held[0].reason is HeldReason.ambiguous_unadjudicated
    assert result.held[0].detail == "the adjudication call budget is exhausted"
    assert adjudicator.calls == []


def test_a_decision_names_the_cluster_it_was_about() -> None:
    first = _pain(pain_id="pp-1")
    second = _pain(
        pain_id="pp-2",
        doc_id="doc-2",
        quote="another sentence",
        action="re-key the invoice totals",
    )
    left, right = _pair(0.80)
    adjudicator = ScriptedAdjudicator(default=AdjudicationDecision.same)
    result = ClusteringEngine(
        _embedder((first, left), (second, right)), _config(), adjudicator
    ).cluster([first, second])
    decisions = [
        decision
        for decision in result.decisions
        if decision.kind is DecisionKind.adjudication
    ]
    assert decisions
    assert decisions[0].cluster_id == result.clusters[0].cluster_id


def test_a_decision_carries_the_confidence_of_the_adjudication() -> None:
    first = _pain(pain_id="pp-1")
    second = _pain(
        pain_id="pp-2",
        doc_id="doc-2",
        quote="another sentence",
        action="re-key the invoice totals",
    )
    left, right = _pair(0.80)
    adjudicator = ScriptedAdjudicator(default=AdjudicationDecision.same)
    result = ClusteringEngine(
        _embedder((first, left), (second, right)), _config(), adjudicator
    ).cluster([first, second])
    decisions = [
        decision
        for decision in result.decisions
        if decision.kind is DecisionKind.adjudication
    ]
    assert decisions
    assert decisions[0].confidence == pytest.approx(0.9)


def test_a_repost_is_reviewed_and_the_pair_is_adjudicated() -> None:
    first = _pain(pain_id="pp-1", doc_id="doc-1", quote="the same sentence")
    second = _pain(
        pain_id="pp-2",
        doc_id="doc-2",
        quote="the same sentence",
        action="re-key the invoice totals",
    )
    left, right = _pair(0.80)
    adjudicator = ScriptedAdjudicator(default=AdjudicationDecision.same)
    result = ClusteringEngine(
        _embedder((first, left), (second, right)), _config(), adjudicator
    ).cluster([first, second])
    duplicates = [
        decision
        for decision in result.decisions
        if decision.kind is DecisionKind.duplicate
    ]
    assert len(duplicates) == 1
    assert duplicates[0].relation == "repost"
    assert duplicates[0].decision == "duplicate"
    assert any(
        decision.kind is DecisionKind.adjudication for decision in result.decisions
    )


def test_an_ambiguous_answer_holds_the_pain_point() -> None:
    first = _pain(pain_id="pp-1")
    second = _pain(
        pain_id="pp-2",
        doc_id="doc-2",
        quote="the second sentence",
        action="re-key the invoice totals",
    )
    left, right = _pair(0.80)
    adjudicator = ScriptedAdjudicator(default=AdjudicationDecision.ambiguous)
    result = ClusteringEngine(
        _embedder((first, left), (second, right)), _config(), adjudicator
    ).cluster([first, second])
    assert len(result.held) == 1
    assert result.held[0].pain_id == "pp-2"
    assert result.held[0].reason is HeldReason.ambiguous_unresolved
    assert len(result.clusters) == 1
    assert result.clusters[0].member_ids == ["pp-1"]
    assert all(assignment.pain_id != "pp-2" for assignment in result.assignments)


def test_a_failed_adjudication_holds_the_pain_point() -> None:
    first = _pain(pain_id="pp-1")
    second = _pain(
        pain_id="pp-2",
        doc_id="doc-2",
        quote="the second sentence",
        action="re-key the invoice totals",
    )
    left, right = _pair(0.80)
    adjudicator = ScriptedAdjudicator(
        {("pp-1", "pp-2"): RuntimeError("the provider is down")}
    )
    result = ClusteringEngine(
        _embedder((first, left), (second, right)), _config(), adjudicator
    ).cluster([first, second])
    assert len(result.held) == 1
    assert result.held[0].reason is HeldReason.adjudicator_failed
    assert len(result.clusters) == 1
    assert result.clusters[0].member_ids == ["pp-1"]


def test_without_an_adjudicator_an_ambiguous_score_holds_the_pain_point() -> None:
    first = _pain(pain_id="pp-1")
    second = _pain(
        pain_id="pp-2",
        doc_id="doc-2",
        quote="the second sentence",
        action="re-key the invoice totals",
    )
    left, right = _pair(0.80)
    result = ClusteringEngine(
        _embedder((first, left), (second, right)), _config()
    ).cluster([first, second])
    assert len(result.held) == 1
    assert result.held[0].reason is HeldReason.ambiguous_unadjudicated
    assert len(result.clusters) == 1


def test_the_same_input_gives_the_same_ids() -> None:
    first = _pain(pain_id="pp-1")
    second = _pain(pain_id="pp-2", doc_id="doc-2", quote="another sentence")
    left, right = _pair(0.99)
    engine = ClusteringEngine(
        _embedder((first, left), (second, right)), _config()
    )
    one = engine.cluster([first, second])
    two = engine.cluster([second, first])
    assert [cluster.cluster_id for cluster in one.clusters] == [
        cluster.cluster_id for cluster in two.clusters
    ]
    assert one.model_dump() == two.model_dump()


def test_a_different_input_gives_a_different_id() -> None:
    first = _pain(pain_id="pp-1")
    second = _pain(pain_id="pp-3", doc_id="doc-3", quote="another sentence")
    third = _pain(pain_id="pp-9", doc_id="doc-9", quote="a third sentence")
    left, right = _pair(0.99)
    engine = ClusteringEngine(
        _embedder((first, left), (second, right), (third, left)), _config()
    )
    one = engine.cluster([first, second])
    two = engine.cluster([third, second])
    assert one.clusters[0].cluster_id != two.clusters[0].cluster_id


def test_no_id_carries_a_timestamp() -> None:
    pain = _pain(pain_id="pp-1")
    result = ClusteringEngine(
        _embedder((pain, _axis(0))), _config()
    ).cluster([pain])
    cluster_id = result.clusters[0].cluster_id
    assert CLUSTER_ID.match(cluster_id)
    assert "2026" not in cluster_id
    assert "T" not in cluster_id


def test_a_new_cluster_checks_its_neighbours() -> None:
    pains = [
        _pain(
            pain_id=f"pp-{index}",
            doc_id=f"doc-{index}",
            quote=f"a sentence {index}",
            action=f"action {index}",
            object=f"object {index}",
        )
        for index in range(7)
    ]
    embedder = _embedder(
        *[(pain, _towards(0.0, index)) for index, pain in enumerate(pains)]
    )
    result = ClusteringEngine(embedder, _config()).cluster(pains)
    assert len(result.clusters) == 7
    # Every new cluster checks the configured number of nearest pain points
    # of the corpus, and a neighbour is recorded below the threshold too.
    assert len(result.neighbor_checks) == 7 * 5
    assert all(not check.above_threshold for check in result.neighbor_checks)


def test_the_result_carries_no_count_of_mentions_or_people() -> None:
    pain = _pain(pain_id="pp-1")
    result = ClusteringEngine(
        _embedder((pain, _axis(0))), _config()
    ).cluster([pain])
    forbidden = ("mention", "author", "people", "person", "community", "follower")
    names = _field_names(type(result))
    assert not [name for name in names if any(word in name for word in forbidden)]
    assert "member_ids" in names


# --- the wiring ------------------------------------------------------------


def test_the_artifacts_carry_the_kinds_the_contract_declares() -> None:
    pains = [
        _pain(
            pain_id=f"pp-{index}",
            doc_id=f"doc-{index}",
            quote=f"a sentence {index}",
            action=f"action {index}",
            object=f"object {index}",
        )
        for index in range(4)
    ]
    embedder = _embedder(
        *[(pain, _towards(0.0, index)) for index, pain in enumerate(pains)]
    )
    result = ClusteringEngine(embedder, _config()).cluster(pains)
    artifacts = clustering_artifacts(
        result,
        run_id="run-1",
        tool_call_id="call-1",
        created_at=datetime(2026, 10, 21, 12, 0, tzinfo=timezone.utc),
    )
    kinds = {artifact.kind for artifact in artifacts}
    assert kinds == {
        "cluster_assignments",
        "similarity_checks",
        "cluster_neighbors_checked",
        "neighbor_check",
        "duplicate_review",
    }
    assert all(artifact.stage == "clustering" for artifact in artifacts)
    assert all(
        artifact.produced_by_tool_call_id == "call-1" for artifact in artifacts
    )


def test_the_neighbour_check_is_one_artifact_per_cluster() -> None:
    pains = [
        _pain(
            pain_id=f"pp-{index}",
            doc_id=f"doc-{index}",
            quote=f"a sentence {index}",
            action=f"action {index}",
            object=f"object {index}",
        )
        for index in range(4)
    ]
    embedder = _embedder(
        *[(pain, _towards(0.0, index)) for index, pain in enumerate(pains)]
    )
    result = ClusteringEngine(embedder, _config()).cluster(pains)
    artifacts = clustering_artifacts(
        result,
        run_id="run-1",
        tool_call_id="call-1",
        created_at=datetime(2026, 10, 21, 12, 0, tzinfo=timezone.utc),
    )
    checks = [
        artifact
        for artifact in artifacts
        if artifact.kind == "cluster_neighbors_checked"
    ]
    assert len(checks) == len(result.clusters)
    # The metric of the research contract reads the smallest of them.
    assert min(artifact.item_count for artifact in checks) == 3


def _field_names(model: type[BaseModel]) -> list[str]:
    """Every field name of a record and of the records it carries."""
    names: list[str] = []
    for name, field in model.model_fields.items():
        names.append(name)
        for candidate in _models_in(field.annotation):
            names.extend(_field_names(candidate))
    return names


def _models_in(annotation) -> list:
    """The records a field carries, if it carries any."""
    from product_hunter.clustering.clusters import (
        Assignment,
        Cluster,
        ClusterDecision,
        HeldPainPoint,
        NeighborCheck,
    )

    known = [
        Cluster,
        Assignment,
        ClusterDecision,
        NeighborCheck,
        HeldPainPoint,
    ]
    if hasattr(annotation, "__args__"):
        found: list = []
        for argument in annotation.__args__:
            found.extend(_models_in(argument))
        return found
    if annotation in known:
        return [annotation]
    return []

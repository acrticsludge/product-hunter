"""The shapes of the clustering package: the fingerprint, the space, the config."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from product_hunter.clustering.adjudication import (
    AdjudicationDecision,
    AdjudicationError,
    LlmAdjudicator,
)
from product_hunter.clustering.clusters import (
    Cluster,
    ClusterProfile,
    ClusterStatus,
    DecisionKind,
    HeldReason,
    Membership,
    NeighborCheck,
)
from product_hunter.clustering.problems import (
    PainPoint,
    canonical_text,
    canonicalize,
    fingerprint,
)
from product_hunter.clustering.similarity import (
    EmbeddingUnavailable,
    MixedVectorSpaceError,
    SentenceTransformerEmbedder,
    VectorSpace,
    cosine_similarity,
    require_same_space,
    space_id,
)
from product_hunter.config import (
    ClusteringConfig,
    ClusteringConfigError,
    ClusteringError,
    load_clustering_config,
)
from product_hunter.llm.base import GenerationResponse
from product_hunter.normalization import Domain


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


def _space(model: str = "a-model", version: str = "1") -> VectorSpace:
    return VectorSpace.of(
        provider="local", model=model, model_version=version, dimensions=3
    )


def _config(**overrides) -> dict:
    data = {
        "version": 1,
        "algorithm_version": "cluster-v1",
        "prompt_version": "cluster-prompt-v1",
        "embedding": {
            "provider": "local",
            "model": "a-model",
            "model_version": "1",
            "dimensions": 3,
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
    data.update(overrides)
    return data


def _write_config(path: Path, data: dict) -> Path:
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path


# --- the canonical form ----------------------------------------------------


def test_the_fingerprint_ignores_the_prose() -> None:
    first = _pain()
    second = _pain(
        pain_id="pp-2",
        pain_statement="the bookkeeping team spends a Saturday pasting totals",
    )
    assert fingerprint(first) == fingerprint(second)


def test_the_fingerprint_notices_a_changed_field() -> None:
    first = _pain()
    second = _pain(pain_id="pp-2", consequence="three days of manual work")
    assert fingerprint(first) != fingerprint(second)


def test_the_fingerprint_ignores_the_case_of_the_fields() -> None:
    first = _pain()
    second = _pain(pain_id="pp-2", actor="The Bookkeeping Team")
    assert fingerprint(first) == fingerprint(second)


def test_the_fingerprint_follows_the_normalization_version() -> None:
    first = _pain()
    second = _pain(pain_id="pp-2", normalization_version="norm-v2")
    assert fingerprint(first) != fingerprint(second)


def test_the_canonical_text_names_every_field_in_a_fixed_order() -> None:
    text = canonical_text(_pain())
    labels = [line.split(":")[0] for line in text.splitlines()]
    assert labels == [
        "domain",
        "actor",
        "action",
        "object",
        "consequence",
        "pain_statement",
    ]
    assert "finance" in text
    assert "invoice totals" in text


def test_the_canonical_text_is_the_same_for_the_same_pain_point() -> None:
    pain = _pain()
    assert canonical_text(pain) == canonical_text(pain)
    assert canonicalize(pain).canonical_text == canonical_text(pain)


def test_canonicalize_carries_the_key_and_the_fingerprint() -> None:
    pain = _pain()
    problem = canonicalize(pain)
    assert problem.normalized_key == pain.normalized_key
    assert problem.fingerprint == fingerprint(pain)
    assert problem.pain_id == pain.pain_id


def test_a_pain_point_carries_no_field_the_wave_does_not_know() -> None:
    with pytest.raises(ValidationError):
        PainPoint(**{**_pain().model_dump(), "mentions": 3})


# --- the vector space ------------------------------------------------------


def test_the_space_id_names_the_model() -> None:
    first = _space("a-model", "1")
    second = _space("another-model", "1")
    third = _space("a-model", "2")
    assert first.space_id == space_id("local", "a-model", "1", 3)
    assert len({first.space_id, second.space_id, third.space_id}) == 3


def test_the_space_id_cannot_be_written_by_hand() -> None:
    with pytest.raises(ValidationError):
        VectorSpace(
            provider="local",
            model="a-model",
            model_version="1",
            dimensions=3,
            space_id="not-the-digest",
        )


def test_the_real_embedder_refuses_when_the_package_is_missing() -> None:
    if importlib.util.find_spec("sentence_transformers") is not None:
        pytest.skip("the sentence-transformers package is installed")
    with pytest.raises(EmbeddingUnavailable) as excinfo:
        SentenceTransformerEmbedder(_space("a-model")).embed(["x"])
    assert "sentence-transformers" in str(excinfo.value)
    assert excinfo.value.reason_code == "embedding_unavailable"


def test_two_spaces_cannot_be_compared() -> None:
    with pytest.raises(MixedVectorSpaceError) as excinfo:
        require_same_space(_space("a-model").space_id, _space("another-model").space_id)
    detail = str(excinfo.value)
    assert _space("a-model").space_id in detail
    assert _space("another-model").space_id in detail
    assert excinfo.value.reason_code == "mixed_vector_space"


def test_the_same_space_compares() -> None:
    require_same_space(_space("a-model").space_id, _space("a-model").space_id)


def test_vectors_of_another_length_cannot_be_compared() -> None:
    with pytest.raises(MixedVectorSpaceError):
        cosine_similarity((1.0, 0.0), (1.0, 0.0, 0.0))


def test_the_cosine_of_two_vectors() -> None:
    assert cosine_similarity((1.0, 0.0), (1.0, 0.0)) == pytest.approx(1.0)
    assert cosine_similarity((1.0, 0.0), (0.0, 1.0)) == pytest.approx(0.0)
    assert cosine_similarity((1.0, 0.0), (0.6, 0.8)) == pytest.approx(0.6)


# --- the config ------------------------------------------------------------


def test_the_shipped_config_loads() -> None:
    config = load_clustering_config()
    assert config.similarity.ambiguous_threshold < config.similarity.merge_threshold
    assert config.embedding.model
    assert config.adjudication.role == "clustering"


def test_the_config_refuses_bands_that_overlap(tmp_path: Path) -> None:
    data = _config()
    data["similarity"]["ambiguous_threshold"] = 0.9
    with pytest.raises(ClusteringConfigError) as excinfo:
        load_clustering_config(_write_config(tmp_path / "clustering.yaml", data))
    assert excinfo.value.reason_code == "clustering_config_invalid"


def test_the_config_refuses_a_threshold_outside_the_unit_interval(tmp_path: Path) -> None:
    data = _config()
    data["similarity"]["merge_threshold"] = 1.4
    with pytest.raises(ClusteringConfigError):
        load_clustering_config(_write_config(tmp_path / "clustering.yaml", data))


def test_the_config_refuses_a_min_neighbors_checked_below_one(tmp_path: Path) -> None:
    data = _config()
    data["similarity"]["min_neighbors_checked"] = 0
    with pytest.raises(ClusteringConfigError):
        load_clustering_config(_write_config(tmp_path / "clustering.yaml", data))


def test_the_config_refuses_an_embedding_block_without_a_model(tmp_path: Path) -> None:
    data = _config()
    data["embedding"]["model"] = ""
    with pytest.raises(ClusteringConfigError):
        load_clustering_config(_write_config(tmp_path / "clustering.yaml", data))


def test_the_config_refuses_a_file_it_cannot_read(tmp_path: Path) -> None:
    with pytest.raises(ClusteringConfigError):
        load_clustering_config(tmp_path / "missing.yaml")


def test_the_config_refuses_a_file_it_cannot_parse(tmp_path: Path) -> None:
    path = tmp_path / "clustering.yaml"
    path.write_text("version: [unclosed\n", encoding="utf-8")
    with pytest.raises(ClusteringConfigError):
        load_clustering_config(path)


def test_the_config_version_shows_a_changed_threshold() -> None:
    first = ClusteringConfig.model_validate(_config())
    data = _config()
    data["similarity"]["merge_threshold"] = 0.9
    second = ClusteringConfig.model_validate(data)
    assert first.config_version != second.config_version
    assert first.config_version.startswith("clustering-v1-")


# --- the shapes ------------------------------------------------------------


def test_the_statuses_are_the_four_the_wave_names() -> None:
    assert {status.value for status in ClusterStatus} == {
        "active",
        "merged",
        "split",
        "dissolved",
    }


def test_the_decision_kinds_are_the_three_the_store_holds() -> None:
    assert {kind.value for kind in DecisionKind} == {
        "duplicate",
        "ambiguous",
        "adjudication",
    }


def test_the_held_reasons_are_the_three_the_spec_names() -> None:
    assert {reason.value for reason in HeldReason} == {
        "ambiguous_unresolved",
        "ambiguous_unadjudicated",
        "adjudicator_failed",
    }


def test_a_membership_keeps_an_open_interval() -> None:
    membership = Membership(
        member_id="mem-1",
        cluster_id="cl-1",
        pain_id="pp-1",
        valid_from="2026-10-21T10:00:00+00:00",
        assignment_confidence=0.9,
        basis="semantic",
    )
    assert membership.valid_to is None


def test_a_cluster_carries_its_profile_and_its_members() -> None:
    cluster = Cluster(
        cluster_id="cl-1",
        version=1,
        status=ClusterStatus.active,
        label="the team pastes totals by hand",
        profile=ClusterProfile(
            domain=Domain.finance,
            actor="the bookkeeping team",
            action="paste invoice totals by hand",
            object="invoice totals",
            consequence="a Saturday of manual work",
        ),
        anchor_pain_id="pp-1",
        member_ids=["pp-1", "pp-2"],
    )
    assert cluster.profile.domain is Domain.finance
    assert cluster.member_ids == ["pp-1", "pp-2"]


def test_a_neighbour_check_says_whether_it_was_above_the_threshold() -> None:
    check = NeighborCheck(
        cluster_id="cl-1", pain_id="pp-9", score=0.4, above_threshold=False
    )
    assert check.above_threshold is False


# --- the errors ------------------------------------------------------------


@pytest.mark.parametrize(
    "error",
    [
        ClusteringError,
        ClusteringConfigError,
        MixedVectorSpaceError,
        EmbeddingUnavailable,
        AdjudicationError,
    ],
)
def test_every_error_carries_a_reason_code(error: type[Exception]) -> None:
    assert issubclass(error, ClusteringError)
    assert error.reason_code


# --- the adjudicator -------------------------------------------------------


class _StubGenerator:
    """A generator that answers with a scripted body."""

    name = "stub"

    def __init__(self, text: str) -> None:
        self.text = text
        self.prompts: list[str] = []

    def generate(self, request):
        self.prompts.append(request.prompt)
        return GenerationResponse(
            text=self.text,
            provider="stub-provider",
            model="stub-model",
            extractor_version="stub-v1",
            input_chars=len(request.prompt),
            latency_ms=1,
            provider_version="stub-model-v1",
        )

    def check(self) -> dict[str, object]:
        return {"ok": True}


def _adjudicator(text: str) -> LlmAdjudicator:
    return LlmAdjudicator(
        _StubGenerator(text),
        prompt_version="cluster-prompt-v1",
        config_version="clustering-v1-test",
    )


def test_the_adjudication_carries_the_provenance_of_the_call() -> None:
    left = canonicalize(_pain(pain_id="pp-1"))
    right = canonicalize(_pain(pain_id="pp-2", action="re-key the totals"))
    answer = _adjudicator(
        '{"decision": "same", "confidence": 0.8, "reason": "both name the same work"}'
    ).adjudicate(left, right)
    assert answer.decision is AdjudicationDecision.same
    assert answer.confidence == pytest.approx(0.8)
    assert answer.reason == "both name the same work"
    assert answer.provider == "stub-provider"
    assert answer.model == "stub-model"
    assert answer.model_version == "stub-model-v1"
    assert answer.prompt_version == "cluster-prompt-v1"
    assert answer.config_version == "clustering-v1-test"


def test_an_answer_that_is_not_json_is_a_failed_adjudication() -> None:
    left = canonicalize(_pain(pain_id="pp-1"))
    right = canonicalize(_pain(pain_id="pp-2", action="re-key the totals"))
    with pytest.raises(AdjudicationError) as excinfo:
        _adjudicator("the two look the same to me").adjudicate(left, right)
    assert excinfo.value.reason_code == "adjudication_failed"
    assert "the two look the same" not in str(excinfo.value)


def test_an_answer_that_names_another_decision_is_a_failed_adjudication() -> None:
    left = canonicalize(_pain(pain_id="pp-1"))
    right = canonicalize(_pain(pain_id="pp-2", action="re-key the totals"))
    with pytest.raises(AdjudicationError):
        _adjudicator('{"decision": "maybe", "confidence": 0.5, "reason": "hmm"}').adjudicate(
            left, right
        )


def test_the_prompt_marks_the_pain_points_as_data() -> None:
    left = canonicalize(_pain(pain_id="pp-1"))
    right = canonicalize(_pain(pain_id="pp-2", action="re-key the totals"))
    generator = _StubGenerator(
        '{"decision": "same", "confidence": 0.8, "reason": "the same work"}'
    )
    adjudicator = LlmAdjudicator(
        generator, prompt_version="cluster-prompt-v1", config_version="clustering-v1-test"
    )
    adjudicator.adjudicate(left, right)
    prompt = generator.prompts[0]
    assert "<<<UNTRUSTED_SOURCE_START>>>" in prompt
    assert prompt.count("<<<UNTRUSTED_SOURCE_START>>>") == 2
    assert left.canonical_text in prompt
    assert right.canonical_text in prompt

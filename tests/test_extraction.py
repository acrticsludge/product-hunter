from __future__ import annotations

import hashlib
import json
import socket
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from product_hunter import extraction
from product_hunter.config import ExtractionConfig, Limits, load_config
from product_hunter.documents import (
    Document,
    DocumentChunk,
    DocumentError,
    parse_documents,
)
from product_hunter.extraction import (
    Candidate,
    ExtractedCandidate,
    ExtractionParseError,
    Frequency,
    PainReason,
    PainValidity,
    PromptTooLarge,
    build_prompt,
    extract_documents,
    parse_output,
)
from product_hunter.fetch import FetchRecord
from product_hunter.llm.base import (
    GenerationRequest,
    GenerationResponse,
    ProviderError,
)

# One source text with the six shapes the rubric has to tell apart.
GENUINE = (
    "Every month the team\u2019s export breaks and the totals have to be pasted "
    "into a spreadsheet by hand, which takes a whole Saturday every time."
)
NEGATIVE = "None of these apps are any good."
QUESTION = "Is there a way to reconcile the payments automatically?"
FEATURE = "It would be great if the export had a filter for unpaid invoices."
INFORMATIONAL = "The vendor portal posts a PDF statement on the first of the month."
INCIDENTAL = "The cafe downstairs is closed for renovation this week."

DOC_TEXT = " ".join(
    [GENUINE, NEGATIVE, QUESTION, FEATURE, INFORMATIONAL, INCIDENTAL]
)


class FakeGenerator:
    """A TextGenerator that replays scripted answers and records every prompt."""

    name = "fake"

    def __init__(
        self,
        *answers: str,
        provider: str = "gen-provider",
        model: str = "gen-model",
        extractor_version: str = "phase1-0.2",
        error: Exception | None = None,
    ) -> None:
        self._answers = list(answers)
        self._provider = provider
        self._model = model
        self._extractor_version = extractor_version
        self._error = error
        self.prompts: list[str] = []
        self.requests: list[GenerationRequest] = []

    def generate(self, request: GenerationRequest) -> GenerationResponse:
        self.prompts.append(request.prompt)
        self.requests.append(request)
        if self._error is not None:
            raise self._error
        text = self._answers.pop(0) if self._answers else '{"candidates":[]}'
        return GenerationResponse(
            text=text,
            provider=self._provider,
            model=self._model,
            extractor_version=self._extractor_version,
            input_chars=len(request.prompt),
            latency_ms=7,
        )

    def check(self) -> dict[str, object]:
        return {"ok": True}


def make_config(**limits: object) -> ExtractionConfig:
    """A config with the shipped values, so the test never reads the env."""
    base = Limits(
        max_documents=10,
        max_calls=10,
        max_input_chars=12_000,
        timeout_seconds=120,
        min_quote_chars=20,
        max_quote_chars=2000,
        chunk_chars=8000,
        chunk_overlap_chars=400,
        max_candidates_per_chunk=8,
    )
    return ExtractionConfig(
        version=1,
        provider="fake",
        provider_id="cfg-provider",
        model_id="cfg-model",
        extractor_version="phase1-0.2",
        prompt_version="extract-v2",
        normalization_version="norm-v1",
        limits=base.model_copy(update=limits),
    )


def make_document(text: str = DOC_TEXT, *, doc_id: str = "doc_test") -> Document:
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return Document(
        doc_id=doc_id,
        fetch_id="fetch_test",
        source_id="hn_algolia",
        url="https://news.ycombinator.com/item?id=1",
        text=text,
        text_sha256=digest,
        text_path=f"data/processed/text/{digest}.txt",
        raw_path="data/raw/hn_algolia/abc123.json",
        raw_sha256="b" * 64,
    )


# The five fields a genuine candidate carries. The wording is the model's
# own; the code slugs it into the normalized key.
NORMALIZED_FIELDS = {
    "domain": "finance",
    "actor": "the bookkeeping team",
    "action": "paste invoice totals by hand",
    "object": "invoice totals",
    "consequence": "a Saturday of manual work every month",
}


def candidate(
    quote: str,
    validity: str,
    reason: str,
    statement: str,
    severity: int,
    frequency: str,
    confidence: float,
    rationale: str,
    *,
    with_normalized: bool = True,
    **overrides,
) -> dict:
    """One candidate as the model would answer it.

    A genuine candidate carries the five normalized fields unless the test
    asks for them to be left out. Keyword overrides replace a field, so
    ``domain=None`` is a candidate with no domain.
    """
    data = {
        "quote": quote,
        "pain_validity": validity,
        "reason": reason,
        "pain_statement": statement,
        "severity": severity,
        "frequency": frequency,
        "confidence": confidence,
        "rationale": rationale,
    }
    if with_normalized and validity == "genuine":
        data.update(NORMALIZED_FIELDS)
    data.update(overrides)
    return data


def valid_payload() -> str:
    """A model answer with all six shapes, one of them a genuine pain point."""
    return json.dumps(
        {
            "candidates": [
                candidate(
                    GENUINE,
                    "genuine",
                    "time_cost",
                    "the team spends a Saturday pasting invoice totals by hand",
                    3,
                    "monthly",
                    0.9,
                    "the text says the totals are pasted by hand every month",
                ),
                candidate(
                    NEGATIVE,
                    "not_pain",
                    "negative_opinion",
                    "the author dislikes the apps",
                    0,
                    "unknown",
                    0.8,
                    "the sentence only says the apps are not good",
                ),
                candidate(
                    QUESTION,
                    "not_pain",
                    "question",
                    "the author asks for automatic reconciliation",
                    0,
                    "unknown",
                    0.8,
                    "the sentence asks a question and states no cost",
                ),
                candidate(
                    FEATURE,
                    "not_pain",
                    "feature_request",
                    "the author wants a filter on the export",
                    0,
                    "unknown",
                    0.8,
                    "the sentence is a wish with no current cost",
                ),
                candidate(
                    INFORMATIONAL,
                    "not_pain",
                    "informational",
                    "the vendor portal posts a monthly statement",
                    0,
                    "unknown",
                    0.9,
                    "the sentence describes the portal and states no problem",
                ),
                candidate(
                    INCIDENTAL,
                    "not_pain",
                    "incidental",
                    "the cafe is closed for renovation",
                    0,
                    "one_off",
                    0.7,
                    "the sentence is an incidental annoyance with no workflow",
                ),
            ]
        }
    )


# --- the happy path -------------------------------------------------------


def test_valid_output_accepts_only_the_genuine_candidate():
    generator = FakeGenerator(valid_payload())

    batch = extract_documents(
        [make_document()], generator, make_config(max_candidates_per_chunk=10)
    )

    assert len(batch.candidates) == 6
    accepted = [c for c in batch.candidates if c.accepted]
    assert len(accepted) == 1
    assert accepted[0].pain_validity is PainValidity.genuine
    assert accepted[0].pain_id is not None
    assert accepted[0].reason is PainReason.time_cost
    assert accepted[0].frequency is Frequency.monthly
    assert accepted[0].severity == 3

    others = [c for c in batch.candidates if not c.accepted]
    assert len(others) == 5
    assert {c.reason for c in others} == {
        PainReason.negative_opinion,
        PainReason.question,
        PainReason.feature_request,
        PainReason.informational,
        PainReason.incidental,
    }
    assert all(c.pain_id is None for c in others)
    assert all(c.pain_validity is PainValidity.not_pain for c in others)
    # Every quote was found verbatim, so nothing was rejected.
    assert batch.rejects == []


def test_offsets_point_at_the_stored_quote():
    document = make_document()

    batch = extract_documents(
        [document], FakeGenerator(valid_payload()), make_config()
    )

    for candidate_ in batch.candidates:
        assert document.text[candidate_.char_start : candidate_.char_end] == (
            candidate_.quote
        )
        assert candidate_.quote in document.text
        assert candidate_.char_start == document.text.index(candidate_.quote)
        assert candidate_.char_end == candidate_.char_start + len(candidate_.quote)
        assert candidate_.match_mode == "exact"
        assert candidate_.occurrence == 1
        assert candidate_.model_quote == candidate_.quote


def test_stored_quote_is_the_document_text_not_the_model_quote():
    """A curly apostrophe matches canonically; the stored quote stays verbatim."""
    document = make_document()
    straight = GENUINE.replace("\u2019", "'")
    payload = json.dumps(
        {
            "candidates": [
                candidate(
                    straight,
                    "genuine",
                    "time_cost",
                    "the team spends a Saturday pasting invoice totals by hand",
                    3,
                    "monthly",
                    0.9,
                    "the text says the totals are pasted by hand every month",
                )
            ]
        }
    )

    batch = extract_documents([document], FakeGenerator(payload), make_config())

    stored = batch.candidates[0]
    assert stored.match_mode == "canonical"
    assert stored.model_quote == straight
    assert stored.quote == GENUINE
    assert "\u2019" in stored.quote
    assert document.text[stored.char_start : stored.char_end] == stored.quote


def test_paraphrased_quote_is_rejected_and_stores_nothing():
    paraphrase = "the team manually pastes the totals into a spreadsheet"
    payload = json.dumps(
        {
            "candidates": [
                candidate(
                    paraphrase,
                    "genuine",
                    "time_cost",
                    "the team spends a Saturday pasting invoice totals by hand",
                    3,
                    "monthly",
                    0.9,
                    "the text says the totals are pasted by hand every month",
                )
            ]
        }
    )

    generator = FakeGenerator(payload, extractor_version="phase1-0.9")

    batch = extract_documents([make_document()], generator, make_config())

    assert batch.candidates == []
    assert [r.reason_code for r in batch.rejects] == ["quote_not_found"]
    assert batch.rejects[0].model_quote == paraphrase
    assert batch.rejects[0].doc_id == "doc_test"
    # The reject records the version the provider actually answered with.
    assert batch.rejects[0].extractor_version == "phase1-0.9"
    assert batch.calls[0].status == "ok"
    assert batch.calls[0].candidate_count == 1
    assert batch.calls[0].rejected_count == 1


def test_a_quote_outside_the_chunk_shown_is_rejected(monkeypatch):
    """A quote the model was never shown is not evidence for a candidate."""
    document = make_document()
    chunk_start = document.text.index(GENUINE) + len(GENUINE) + 1
    chunk = DocumentChunk(
        doc_id=document.doc_id,
        chunk_index=0,
        char_start=chunk_start,
        char_end=len(document.text),
        text=document.text[chunk_start:],
    )
    monkeypatch.setattr(
        "product_hunter.extraction.chunk_documents",
        lambda documents, *args, **kwargs: [chunk],
    )
    payload = json.dumps(
        {
            "candidates": [
                candidate(
                    GENUINE,
                    "genuine",
                    "time_cost",
                    "the team spends a Saturday pasting invoice totals by hand",
                    3,
                    "monthly",
                    0.9,
                    "the text says the totals are pasted by hand every month",
                )
            ]
        }
    )

    batch = extract_documents([document], FakeGenerator(payload), make_config())

    assert batch.candidates == []
    assert [r.reason_code for r in batch.rejects] == ["quote_outside_chunk"]
    assert batch.rejects[0].detail == "quote is outside the chunk the model was shown"
    assert batch.rejects[0].model_quote == GENUINE
    assert batch.rejects[0].doc_id == "doc_test"
    assert batch.calls[0].status == "ok"
    assert batch.calls[0].candidate_count == 1
    assert batch.calls[0].rejected_count == 1


def test_a_quote_that_only_overlaps_the_chunk_is_rejected(monkeypatch):
    """A quote that starts before the chunk is not evidence either."""
    document = make_document()
    # The chunk starts inside the genuine sentence, so the quote that spans
    # the whole sentence starts before the chunk and ends inside it.
    chunk_start = document.text.index(GENUINE) + 10
    chunk = DocumentChunk(
        doc_id=document.doc_id,
        chunk_index=0,
        char_start=chunk_start,
        char_end=len(document.text),
        text=document.text[chunk_start:],
    )
    monkeypatch.setattr(
        "product_hunter.extraction.chunk_documents",
        lambda documents, *args, **kwargs: [chunk],
    )
    payload = json.dumps(
        {
            "candidates": [
                candidate(
                    GENUINE,
                    "genuine",
                    "time_cost",
                    "the team spends a Saturday pasting invoice totals by hand",
                    3,
                    "monthly",
                    0.9,
                    "the text says the totals are pasted by hand every month",
                )
            ]
        }
    )

    batch = extract_documents([document], FakeGenerator(payload), make_config())

    assert batch.candidates == []
    assert [r.reason_code for r in batch.rejects] == ["quote_outside_chunk"]
    assert batch.rejects[0].detail == "quote is outside the chunk the model was shown"
    assert batch.rejects[0].model_quote == GENUINE
    assert batch.rejects[0].doc_id == "doc_test"
    assert batch.calls[0].status == "ok"
    assert batch.calls[0].candidate_count == 1
    assert batch.calls[0].rejected_count == 1


def test_weak_candidate_is_stored_but_not_accepted():
    payload = json.dumps(
        {
            "candidates": [
                candidate(
                    INCIDENTAL,
                    "weak",
                    "incidental",
                    "the cafe is closed for renovation",
                    1,
                    "one_off",
                    0.5,
                    "the sentence names no workflow and no cost",
                )
            ]
        }
    )

    batch = extract_documents([make_document()], FakeGenerator(payload), make_config())

    assert len(batch.candidates) == 1
    assert batch.candidates[0].accepted is False
    assert batch.candidates[0].pain_id is None
    assert batch.candidates[0].pain_validity is PainValidity.weak


# --- the normalized fields -------------------------------------------------


def test_a_genuine_candidate_without_the_normalized_fields_is_rejected():
    payload = json.dumps(
        {
            "candidates": [
                candidate(
                    GENUINE,
                    "genuine",
                    "time_cost",
                    "the team spends a Saturday pasting invoice totals by hand",
                    3,
                    "monthly",
                    0.9,
                    "the text says the totals are pasted by hand every month",
                    with_normalized=False,
                )
            ]
        }
    )

    with pytest.raises(ExtractionParseError) as excinfo:
        parse_output(payload, max_candidates=8)

    assert excinfo.value.reason_code == "schema_invalid"


@pytest.mark.parametrize(
    "field", ["domain", "actor", "action", "object", "consequence"]
)
def test_a_genuine_candidate_missing_one_normalized_field_is_rejected(field):
    payload = json.dumps(
        {
            "candidates": [
                candidate(
                    GENUINE,
                    "genuine",
                    "time_cost",
                    "a statement",
                    3,
                    "monthly",
                    0.9,
                    "a rationale",
                    **{field: None},
                )
            ]
        }
    )

    with pytest.raises(ExtractionParseError) as excinfo:
        parse_output(payload, max_candidates=8)

    assert excinfo.value.reason_code == "schema_invalid"


@pytest.mark.parametrize("field", ["actor", "action", "object", "consequence"])
def test_a_genuine_candidate_with_a_blank_field_is_rejected(field):
    payload = json.dumps(
        {
            "candidates": [
                candidate(
                    GENUINE,
                    "genuine",
                    "time_cost",
                    "a statement",
                    3,
                    "monthly",
                    0.9,
                    "a rationale",
                    **{field: "   "},
                )
            ]
        }
    )

    with pytest.raises(ExtractionParseError) as excinfo:
        parse_output(payload, max_candidates=8)

    assert excinfo.value.reason_code == "schema_invalid"


def test_a_genuine_candidate_with_an_unknown_domain_is_rejected():
    payload = json.dumps(
        {
            "candidates": [
                candidate(
                    GENUINE,
                    "genuine",
                    "time_cost",
                    "a statement",
                    3,
                    "monthly",
                    0.9,
                    "a rationale",
                    domain="widgets",
                )
            ]
        }
    )

    with pytest.raises(ExtractionParseError) as excinfo:
        parse_output(payload, max_candidates=8)

    assert excinfo.value.reason_code == "schema_invalid"


def test_an_accepted_candidate_stores_the_normalized_fields():
    batch = extract_documents(
        [make_document()], FakeGenerator(valid_payload()), make_config()
    )

    stored = batch.candidates[0]
    assert stored.accepted is True
    assert stored.domain == "finance"
    assert stored.actor == "the bookkeeping team"
    assert stored.action == "paste invoice totals by hand"
    assert stored.object == "invoice totals"
    assert stored.consequence == "a Saturday of manual work every month"
    assert stored.normalized_key == (
        "finance:paste-invoice-totals-by-hand:invoice-totals"
    )
    assert stored.normalization_version == "norm-v1"


def test_the_same_problem_with_other_wording_gets_the_same_key():
    def payload(action: str, object_: str) -> str:
        return json.dumps(
            {
                "candidates": [
                    candidate(
                        GENUINE,
                        "genuine",
                        "time_cost",
                        "a statement",
                        3,
                        "monthly",
                        0.9,
                        "a rationale",
                        action=action,
                        object=object_,
                    )
                ]
            }
        )

    first = extract_documents(
        [make_document()],
        FakeGenerator(payload("Paste invoice totals by hand", "Invoice totals")),
        make_config(),
    )
    second = extract_documents(
        [make_document()],
        FakeGenerator(payload("paste  invoice, totals by hand", "invoice totals")),
        make_config(),
    )

    assert first.candidates[0].normalized_key == second.candidates[0].normalized_key
    assert first.candidates[0].normalized_key == (
        "finance:paste-invoice-totals-by-hand:invoice-totals"
    )


def test_a_genuine_candidate_that_cannot_be_keyed_is_rejected():
    payload = json.dumps(
        {
            "candidates": [
                candidate(
                    GENUINE,
                    "genuine",
                    "time_cost",
                    "a statement",
                    3,
                    "monthly",
                    0.9,
                    "a rationale",
                    action="---",
                )
            ]
        }
    )

    batch = extract_documents([make_document()], FakeGenerator(payload), make_config())

    assert batch.candidates == []
    assert [r.reason_code for r in batch.rejects] == ["normalization_invalid"]
    assert batch.rejects[0].model_quote == GENUINE
    assert batch.rejects[0].doc_id == "doc_test"
    assert batch.rejects[0].provider == "gen-provider"
    assert batch.rejects[0].model == "gen-model"
    assert batch.rejects[0].extractor_version == "phase1-0.2"
    assert batch.calls[0].status == "ok"
    assert batch.calls[0].candidate_count == 1
    assert batch.calls[0].accepted_count == 0
    assert batch.calls[0].rejected_count == 1


def test_a_weak_candidate_keeps_the_model_fields_but_gets_no_key():
    payload = json.dumps(
        {
            "candidates": [
                candidate(
                    INCIDENTAL,
                    "weak",
                    "incidental",
                    "the cafe is closed for renovation",
                    1,
                    "one_off",
                    0.5,
                    "the sentence names no workflow and no cost",
                    domain="other",
                    actor="the staff",
                    action="work around the closure",
                    object="the cafe",
                    consequence="a day of disruption",
                )
            ]
        }
    )

    batch = extract_documents([make_document()], FakeGenerator(payload), make_config())

    stored = batch.candidates[0]
    assert stored.accepted is False
    assert stored.pain_id is None
    assert stored.normalized_key is None
    assert stored.domain == "other"
    assert stored.actor == "the staff"
    assert stored.action == "work around the closure"
    assert stored.object == "the cafe"
    assert stored.consequence == "a day of disruption"
    assert stored.normalization_version == "norm-v1"


# --- the stored key --------------------------------------------------------


def stored_candidate(**overrides) -> ExtractedCandidate:
    """A stored candidate, the way the extractor writes it."""
    values: dict[str, object] = {
        "candidate_id": "cand_1",
        "pain_id": "pp_cand_1",
        "doc_id": "doc_test",
        "fetch_id": "fetch_test",
        "source_id": "hn_algolia",
        "url": "https://news.ycombinator.com/item?id=1",
        "span_id": "ev_cand_1",
        "quote": GENUINE,
        "char_start": 0,
        "char_end": len(GENUINE),
        "match_mode": "exact",
        "model_quote": GENUINE,
        "occurrence": 1,
        "pain_validity": "genuine",
        "reason": "time_cost",
        "pain_statement": "the team spends a Saturday pasting invoice totals by hand",
        "severity": 3,
        "frequency": "monthly",
        "confidence": 0.9,
        "rationale": "the text says the totals are pasted by hand every month",
        "domain": "finance",
        "actor": "the bookkeeping team",
        "action": "paste invoice totals by hand",
        "object": "invoice totals",
        "consequence": "a Saturday of manual work every month",
        "normalized_key": "finance:paste-invoice-totals-by-hand:invoice-totals",
        "normalization_version": "norm-v1",
        "accepted": True,
        "provider": "fake",
        "model": "fake-model",
        "extractor_version": "phase1-0.3",
        "prompt_version": "extract-v3",
        "raw_path": "data/raw/hn_algolia/abc123.json",
        "raw_sha256": "b" * 64,
        "text_sha256": "c" * 64,
        "created_at": "2026-10-20T12:00:00+00:00",
    }
    values.update(overrides)
    return ExtractedCandidate(**values)


def test_an_accepted_candidate_with_its_own_key_is_stored():
    stored = stored_candidate()

    assert stored.accepted is True
    assert stored.normalized_key == "finance:paste-invoice-totals-by-hand:invoice-totals"


def test_an_accepted_candidate_with_a_tampered_key_is_refused():
    with pytest.raises(ValueError) as excinfo:
        stored_candidate(normalized_key="finance:paste-invoice-totals-by-hand:other")

    assert "paste-invoice-totals-by-hand" in str(excinfo.value)


@pytest.mark.parametrize(
    "field", ["domain", "actor", "action", "object", "consequence"]
)
def test_an_accepted_candidate_missing_a_normalized_field_is_refused(field):
    """An accepted record with a hole in its field set has no key to check."""
    with pytest.raises(ValidationError) as excinfo:
        stored_candidate(**{field: None})

    assert "needs every normalized field" in str(excinfo.value)
    assert field in str(excinfo.value)


def test_an_accepted_candidate_whose_fields_cannot_be_re_keyed_is_refused():
    with pytest.raises(ValueError) as excinfo:
        stored_candidate(action="---")

    assert "re-keyed" in str(excinfo.value)


def test_an_accepted_candidate_with_an_unknown_domain_is_refused():
    with pytest.raises(ValueError) as excinfo:
        stored_candidate(domain="widgets")

    assert "re-keyed" in str(excinfo.value)


def test_the_stored_normalization_version_is_stripped():
    """The record carries the version the key was built with."""
    config = make_config().model_copy(update={"normalization_version": " norm-v1 "})

    batch = extract_documents(
        [make_document()], FakeGenerator(valid_payload()), config
    )

    stored = batch.candidates[0]
    assert stored.normalization_version == "norm-v1"
    assert stored.normalized_key == "finance:paste-invoice-totals-by-hand:invoice-totals"


# --- invalid model output -------------------------------------------------


INVALID_OUTPUTS = [
    ("{not json", "invalid_json"),
    ("Here is the answer:\n" + valid_payload(), "invalid_json"),
    (valid_payload().replace('"rationale"', '"why"', 1), "schema_invalid"),
    (
        json.dumps(
            {
                "candidates": [
                    {
                        **candidate(
                            GENUINE,
                            "genuine",
                            "time_cost",
                            "a statement",
                            3,
                            "monthly",
                            0.9,
                            "a rationale",
                        ),
                        "notes": "an extra field",
                    }
                ]
            }
        ),
        "schema_invalid",
    ),
    (
        json.dumps(
            {
                "candidates": [
                    candidate(
                        GENUINE,
                        "bogus",
                        "time_cost",
                        "a statement",
                        3,
                        "monthly",
                        0.9,
                        "a rationale",
                    )
                ]
            }
        ),
        "schema_invalid",
    ),
    (
        json.dumps(
            {
                "candidates": [
                    candidate(
                        GENUINE,
                        "genuine",
                        "time_cost",
                        "a statement",
                        6,
                        "monthly",
                        0.9,
                        "a rationale",
                    )
                ]
            }
        ),
        "schema_invalid",
    ),
    (
        json.dumps(
            {
                "candidates": [
                    candidate(
                        GENUINE,
                        "genuine",
                        "time_cost",
                        "a statement",
                        3,
                        "monthly",
                        1.5,
                        "a rationale",
                    )
                ]
            }
        ),
        "schema_invalid",
    ),
    (
        json.dumps(
            {
                "candidates": [
                    candidate(
                        GENUINE,
                        "not_pain",
                        "negative_opinion",
                        "a statement",
                        3,
                        "monthly",
                        0.9,
                        "a rationale",
                    )
                ]
            }
        ),
        "schema_invalid",
    ),
    (
        json.dumps(
            {
                "candidates": [
                    candidate(
                        GENUINE,
                        "genuine",
                        "negative_opinion",
                        "a statement",
                        3,
                        "monthly",
                        0.9,
                        "a rationale",
                    )
                ]
            }
        ),
        "schema_invalid",
    ),
]


@pytest.mark.parametrize("payload,reason_code", INVALID_OUTPUTS)
def test_invalid_model_output_is_rejected(payload, reason_code):
    with pytest.raises(ExtractionParseError) as excinfo:
        parse_output(payload, max_candidates=8)
    assert excinfo.value.reason_code == reason_code

    batch = extract_documents(
        [make_document()], FakeGenerator(payload), make_config()
    )
    assert batch.candidates == []
    assert [r.reason_code for r in batch.rejects] == [reason_code]
    assert batch.calls[0].status == reason_code


def test_code_fenced_json_is_accepted():
    generator = FakeGenerator("```json\n" + valid_payload() + "\n```")

    batch = extract_documents([make_document()], generator, make_config())

    assert len(batch.candidates) == 6
    assert batch.rejects == []


def test_plain_code_fence_is_accepted():
    generator = FakeGenerator("```\n" + valid_payload() + "\n```")

    batch = extract_documents([make_document()], generator, make_config())

    assert len(batch.candidates) == 6


def test_more_candidates_than_the_limit_are_rejected():
    payload = valid_payload()

    with pytest.raises(ExtractionParseError) as excinfo:
        parse_output(payload, max_candidates=3)

    assert excinfo.value.reason_code == "schema_invalid"


def test_the_configured_limit_is_enforced_and_not_only_requested():
    """The shipped config asks for three; an answer with four is refused."""
    config = load_config()
    assert config.limits.max_candidates_per_chunk == 3
    payload = json.dumps(
        {
            "candidates": [
                candidate(
                    GENUINE,
                    "genuine",
                    "time_cost",
                    "the team spends a Saturday pasting invoice totals by hand",
                    3,
                    "monthly",
                    0.9,
                    "the text says the totals are pasted by hand every month",
                )
            ]
            * 4
        }
    )

    with pytest.raises(ExtractionParseError) as excinfo:
        parse_output(payload, max_candidates=config.limits.max_candidates_per_chunk)
    assert excinfo.value.reason_code == "schema_invalid"

    batch = extract_documents([make_document()], FakeGenerator(payload), config)
    assert batch.candidates == []
    assert [r.reason_code for r in batch.rejects] == ["schema_invalid"]
    assert batch.calls[0].status == "schema_invalid"


def test_more_candidates_than_the_schema_ceiling_are_rejected():
    payload = json.dumps(
        {
            "candidates": [
                candidate(
                    GENUINE,
                    "genuine",
                    "time_cost",
                    "a statement",
                    3,
                    "monthly",
                    0.9,
                    "a rationale",
                )
                for _ in range(11)
            ]
        }
    )

    with pytest.raises(ExtractionParseError) as excinfo:
        parse_output(payload, max_candidates=10)

    assert excinfo.value.reason_code == "schema_invalid"

    batch = extract_documents(
        [make_document()],
        FakeGenerator(payload),
        make_config(max_candidates_per_chunk=10),
    )
    assert batch.candidates == []
    assert [r.reason_code for r in batch.rejects] == ["schema_invalid"]


def test_empty_candidate_list_is_accepted():
    output = parse_output('{"candidates":[]}', max_candidates=3)

    assert output.candidates == []


@pytest.mark.parametrize(
    "validity,reason,severity",
    [
        ("genuine", "negative_opinion", 3),
        ("genuine", "time_cost", 1),
        ("weak", "time_cost", 3),
        ("weak", "question", 1),
        ("not_pain", "time_cost", 0),
        ("not_pain", "negative_opinion", 2),
    ],
)
def test_candidate_rejects_an_inconsistent_classification(validity, reason, severity):
    with pytest.raises(ValidationError):
        Candidate(
            quote=GENUINE,
            pain_validity=validity,
            reason=reason,
            pain_statement="a statement",
            severity=severity,
            frequency="unknown",
            confidence=0.5,
            rationale="a rationale",
        )


# --- ids and provenance ---------------------------------------------------


def test_ids_are_deterministic():
    first = extract_documents(
        [make_document()], FakeGenerator(valid_payload()), make_config()
    )
    second = extract_documents(
        [make_document()], FakeGenerator(valid_payload()), make_config()
    )

    assert [c.candidate_id for c in first.candidates] == [
        c.candidate_id for c in second.candidates
    ]
    assert [c.span_id for c in first.candidates] == [
        c.span_id for c in second.candidates
    ]
    assert [c.pain_id for c in first.candidates] == [
        c.pain_id for c in second.candidates
    ]

    stored = first.candidates[0]
    assert stored.candidate_id.startswith("cand_")
    assert stored.span_id.startswith("ev_")
    assert stored.pain_id.startswith("pp_")
    assert len(stored.candidate_id) == len("cand_") + 16
    assert len(stored.span_id) == len("ev_") + 16
    assert len(stored.pain_id) == len("pp_") + 16
    assert stored.pain_id == "pp_" + hashlib.sha256(
        f"{stored.span_id}|phase1-0.2".encode("utf-8")
    ).hexdigest()[:16]
    assert stored.span_id == "ev_" + hashlib.sha256(
        f"{stored.doc_id}|{stored.char_start}|{stored.char_end}|{stored.quote}".encode(
            "utf-8"
        )
    ).hexdigest()[:16]


def test_provenance_fields_are_recorded():
    document = make_document()

    batch = extract_documents(
        [document], FakeGenerator(valid_payload()), make_config()
    )

    stored = batch.candidates[0]
    assert stored.provider == "gen-provider"
    assert stored.model == "gen-model"
    assert stored.extractor_version == "phase1-0.2"
    assert stored.prompt_version == "extract-v2"
    assert stored.raw_path == "data/raw/hn_algolia/abc123.json"
    assert stored.raw_sha256 == "b" * 64
    assert stored.text_sha256 == document.text_sha256
    assert stored.doc_id == "doc_test"
    assert stored.fetch_id == "fetch_test"
    assert stored.source_id == "hn_algolia"
    assert stored.url == "https://news.ycombinator.com/item?id=1"
    assert stored.created_at


def test_call_records_the_counts_and_the_status():
    generator = FakeGenerator(valid_payload())

    batch = extract_documents([make_document()], generator, make_config())

    call = batch.calls[0]
    assert call.status == "ok"
    assert call.candidate_count == 6
    assert call.accepted_count == 1
    assert call.rejected_count == 0
    assert call.doc_id == "doc_test"
    assert call.chunk_index == 0
    assert call.provider == "gen-provider"
    assert call.model == "gen-model"
    assert call.extractor_version == "phase1-0.2"
    assert call.prompt_version == "extract-v2"
    assert call.input_chars == len(generator.prompts[0])
    assert call.latency_ms == 7
    assert call.error is None


def test_max_calls_stops_the_run_and_records_a_budget_reject():
    generator = FakeGenerator(valid_payload(), valid_payload())

    batch = extract_documents(
        [make_document(doc_id="doc_a"), make_document(doc_id="doc_b")],
        generator,
        make_config(),
        max_calls=1,
    )

    assert len(batch.calls) == 1
    assert len(generator.prompts) == 1
    assert [r.reason_code for r in batch.rejects] == ["budget_exceeded"]
    assert "1" in batch.rejects[0].detail


def test_max_documents_slices_the_input():
    batch = extract_documents(
        [make_document(doc_id="doc_a"), make_document(doc_id="doc_b")],
        FakeGenerator(valid_payload(), valid_payload()),
        make_config(),
        max_documents=1,
    )

    assert [d.doc_id for d in batch.documents] == ["doc_a"]
    assert len(batch.chunks) == 1
    assert len(batch.calls) == 1


def test_a_provider_error_is_recorded_and_the_run_continues():
    generator = FakeGenerator(error=ProviderError("the provider is down"))

    batch = extract_documents([make_document()], generator, make_config())

    assert batch.candidates == []
    assert [r.reason_code for r in batch.rejects] == ["provider_error"]
    assert batch.calls[0].status == "provider_error"
    assert batch.calls[0].error == "the provider is down"


# --- the prompt ------------------------------------------------------------


def test_prompt_carries_the_rubric_and_the_untrusted_fence():
    document = make_document()
    chunk = DocumentChunk(
        doc_id=document.doc_id,
        chunk_index=0,
        char_start=0,
        char_end=len(document.text),
        text=document.text,
    )

    prompt = build_prompt(chunk, max_candidates=3)

    assert "<<<UNTRUSTED_SOURCE_START>>>" in prompt
    assert "<<<UNTRUSTED_SOURCE_END>>>" in prompt
    assert document.text in prompt
    assert "json" in prompt
    assert "Return at most 3 candidates" in prompt
    for term in (
        "genuine",
        "weak",
        "not_pain",
        "recurring_manual_work",
        "time_cost",
        "money_cost",
        "error_or_delay",
        "compliance_or_risk",
        "negative_opinion",
        "question",
        "feature_request",
        "informational",
        "incidental",
        "daily",
        "weekly",
        "monthly",
        "occasional",
        "one_off",
        "unknown",
        "exact substring",
        "at least 20 characters",
        "actor or team",
        "severity",
    ):
        assert term in prompt


def test_prompt_carries_the_normalized_field_vocabulary():
    document = make_document()
    chunk = DocumentChunk(
        doc_id=document.doc_id,
        chunk_index=0,
        char_start=0,
        char_end=len(document.text),
        text=document.text,
    )

    prompt = build_prompt(chunk, max_candidates=3)

    for term in ("domain", "actor", "action", "object", "consequence", "normalized"):
        assert term in prompt
    for domain in (
        "software",
        "data",
        "operations",
        "finance",
        "sales",
        "marketing",
        "support",
        "hr",
        "legal",
        "security",
        "research",
        "education",
        "healthcare",
        "ecommerce",
        "other",
    ):
        assert domain in prompt


def test_prompt_refuses_to_exceed_the_limit():
    chunk = DocumentChunk(
        doc_id="doc_test",
        chunk_index=0,
        char_start=0,
        char_end=40_000,
        text="x" * 40_000,
    )

    with pytest.raises(PromptTooLarge):
        build_prompt(chunk, max_candidates=3, max_input_chars=1000)

    with pytest.raises(PromptTooLarge):
        build_prompt(chunk, max_candidates=3)


def test_prompt_fits_the_shipped_config_limit():
    config = load_config()
    chunk = DocumentChunk(
        doc_id="doc_test",
        chunk_index=0,
        char_start=0,
        char_end=config.limits.chunk_chars,
        text="x" * config.limits.chunk_chars,
    )

    prompt = build_prompt(
        chunk,
        max_candidates=config.limits.max_candidates_per_chunk,
        max_input_chars=config.limits.max_input_chars,
    )

    assert len(prompt) <= config.limits.max_input_chars


def test_a_brace_in_the_source_text_reaches_the_prompt_unchanged():
    # A scraped page is data. A brace in it is data too, and it has to reach
    # the model exactly as it was captured.
    text = 'the team pastes {every} total by hand, and {max_candidates} is odd'
    chunk = DocumentChunk(
        doc_id="doc_test",
        chunk_index=0,
        char_start=0,
        char_end=len(text),
        text=text,
    )

    prompt = build_prompt(chunk, max_candidates=3)

    assert text in prompt
    # The candidate count is the real one, and the text did not get a second
    # substitution.
    assert "Return at most 3 candidates" in prompt
    assert "Return at most {max_candidates} candidates" not in prompt
    # The json example still shows single braces, so the model reads the
    # shape it is asked to return.
    assert '{"candidates": []}' in prompt


def test_a_stray_brace_in_the_template_does_not_break_the_prompt(monkeypatch):
    # The prompt is assembled by literal replacement, so the template is not
    # a format string. A brace the template holds is written out as it is.
    # This is the property the old template did not have: it was a format
    # string, so one stray brace in it raised when the prompt was built.
    monkeypatch.setattr(
        extraction,
        "_PROMPT",
        "Return at most {max_candidates} candidates.\n{unbalanced\n{text}\n",
    )
    chunk = DocumentChunk(
        doc_id="doc_test",
        chunk_index=0,
        char_start=0,
        char_end=5,
        text="hello",
    )

    prompt = build_prompt(chunk, max_candidates=3)

    assert "Return at most 3 candidates." in prompt
    assert "{unbalanced" in prompt
    assert "hello" in prompt


def test_a_source_text_with_a_placeholder_is_not_substituted_again():
    text = "the page says {max_candidates} and {text} in its body"
    chunk = DocumentChunk(
        doc_id="doc_test",
        chunk_index=0,
        char_start=0,
        char_end=len(text),
        text=text,
    )

    prompt = build_prompt(chunk, max_candidates=3)

    assert text in prompt
    assert "Return at most 3 candidates" in prompt


# --- boundaries ------------------------------------------------------------


def test_extraction_never_opens_a_socket(monkeypatch):
    def explode(*args, **kwargs):
        raise AssertionError("the extraction step must not open a socket")

    monkeypatch.setattr(socket, "socket", explode)
    monkeypatch.setattr(socket, "create_connection", explode)

    batch = extract_documents(
        [make_document()], FakeGenerator(valid_payload()), make_config()
    )

    assert len(batch.candidates) == 6


def test_a_document_without_raw_provenance_is_refused(tmp_path):
    raw = json.dumps(
        {
            "hits": [
                {
                    "objectID": "1",
                    "comment_text": "A comment about a broken export.",
                    "author": "someone",
                    "created_at": "2026-01-01T00:00:00Z",
                }
            ]
        }
    ).encode("utf-8")
    digest = hashlib.sha256(raw).hexdigest()
    fetch = FetchRecord(
        fetch_id="fetch_test",
        source_id="hn_algolia",
        requested_url="https://news.ycombinator.com/item?id=1",
        final_url="https://news.ycombinator.com/item?id=1",
        status=200,
        content_type="application/json",
        raw_sha256=digest,
        raw_path=None,
        bytes=len(raw),
        fetched_at="2026-10-20T12:00:00+00:00",
    )

    with pytest.raises(DocumentError) as excinfo:
        parse_documents(fetch, raw, repo_root=tmp_path)

    assert excinfo.value.reason_code == "missing_raw_provenance"

    without_hash = fetch.model_copy(update={"raw_sha256": None})

    with pytest.raises(DocumentError) as excinfo:
        parse_documents(without_hash, raw, repo_root=tmp_path)

    assert excinfo.value.reason_code == "missing_raw_provenance"


def test_a_document_record_requires_raw_provenance():
    with pytest.raises(ValidationError):
        Document(
            doc_id="doc_test",
            fetch_id="fetch_test",
            source_id="hn_algolia",
            url="https://news.ycombinator.com/item?id=1",
            text="some text",
            text_sha256="c" * 64,
            text_path="data/processed/text/abc.txt",
        )


def test_created_at_uses_the_injected_clock():
    fixed = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)

    batch = extract_documents(
        [make_document()],
        FakeGenerator(valid_payload()),
        make_config(),
        now=lambda: fixed,
    )

    assert batch.candidates[0].created_at == fixed.isoformat()
    assert batch.calls[0].created_at == fixed.isoformat()

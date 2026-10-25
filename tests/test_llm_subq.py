"""The SubQ adapter: the client, the provider, the budget and the ledger.

Every test here is offline: the service is a mock transport, so no test
reaches the network. The one live test at the end is marked ``network`` and
skips itself when the key is absent.
"""

from __future__ import annotations

import os

import httpx
import pytest
from pydantic import SecretStr

from product_hunter.llm.base import GenerationRequest, GenerationResponse
from product_hunter.llm.decision import (
    DecisionBudgetExceeded,
    DecisionError,
    DecisionLimitReached,
    DecisionQuestion,
    DecisionRouter,
    DecisionUnavailable,
    ProviderHealth,
    QuestionType,
    TextDecisionProvider,
)
from product_hunter.llm.subq import (
    SubQClient,
    SubQConfig,
    SubQCredentialRejected,
    SubQProvider,
)

# A placeholder, not a credential: the real key never enters the repository.
KEY = "unit-test-placeholder"

OPTIONS = {"finance": "about money", "operations": "about the workflow"}


def answer(name: str, body: dict, *, input_tokens: int = 100) -> httpx.Response:
    """A good answer envelope for one question."""
    return httpx.Response(
        200,
        json={
            "model": "subq-snap-1.1",
            "answers": {name: body},
            "usage": {"input_tokens": input_tokens, "output_tokens": 0},
        },
    )


def refusal(status: int, message: str) -> httpx.Response:
    """The error envelope the decisions endpoint sends."""
    return httpx.Response(
        status,
        json={"detail": {"error_type": "validation_error", "message": message}},
    )


class FakeService:
    """The service, scripted: it replays the answers a test prepared."""

    def __init__(self, *results) -> None:
        self._results = list(results)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if not self._results:
            raise AssertionError("the service was called more times than scripted")
        result = self._results.pop(0)
        if isinstance(result, Exception):
            raise result
        if callable(result):
            return result(request)
        return result

    @property
    def calls(self) -> int:
        """How many requests the service received."""
        return len(self.requests)


class FakeTracker:
    """A budget tracker that records the spend and can refuse a call."""

    def __init__(self, *, refuse: str | None = None) -> None:
        self.refuse = refuse
        self.guarded: list[tuple[str, float]] = []
        self.recorded: list[tuple[str, float]] = []

    def guard(self, dimension: str, quantity: float, stage=None, source_id=None) -> None:
        self.guarded.append((dimension, quantity))
        if self.refuse == dimension:
            raise RuntimeError(f"the {dimension} budget is spent")

    def record(self, dimension: str, quantity: float, stage=None, source_id=None) -> None:
        self.recorded.append((dimension, quantity))


class FakeGenerator:
    """A text generator that answers every prompt with one JSON object."""

    name = "fake-text"

    def __init__(self, text: str) -> None:
        self.text = text
        self.requests: list[GenerationRequest] = []

    def generate(self, request: GenerationRequest) -> GenerationResponse:
        self.requests.append(request)
        return GenerationResponse(
            text=self.text,
            provider="fake-provider",
            model="fake-model",
            extractor_version="phase1-0.3",
            input_chars=len(request.prompt),
            latency_ms=7,
        )

    def check(self) -> dict[str, object]:
        return {"ok": True}


def build(service, **overrides):
    """A provider over a scripted service, and the waits it asked for."""
    settings = {
        "api_key": KEY,
        "max_retries": 2,
        "backoff_base_seconds": 0.01,
        "backoff_max_seconds": 30.0,
    }
    settings.update(overrides)
    config = SubQConfig(**settings)
    sleeps: list[float] = []
    client = SubQClient(
        config,
        client=httpx.Client(transport=httpx.MockTransport(service)),
        sleeper=sleeps.append,
    )
    return SubQProvider(config, client=client), sleeps


# --- the credential -------------------------------------------------------


def test_a_provider_without_a_key_reports_itself_disabled():
    provider, _ = build(FakeService(), api_key=None)
    assert provider.health() is ProviderHealth.disabled


def test_a_provider_without_a_key_refuses_to_work():
    provider, _ = build(FakeService(), api_key=None)
    with pytest.raises(DecisionUnavailable):
        provider.classify(["a text"], options=OPTIONS)


def test_the_router_uses_the_next_provider_when_the_key_is_missing():
    subq, _ = build(FakeService(), api_key=None)
    text = TextDecisionProvider(
        FakeGenerator('{"label": "finance", "probability": 0.8, "confidence": 0.9}')
    )
    router = DecisionRouter([subq, text])

    outcomes = router.classify(["a text"], options=OPTIONS)

    assert len(outcomes) == 1
    assert outcomes[0].label == "finance"
    assert outcomes[0].provenance.provider == "fake-provider"


def test_the_key_stays_out_of_the_config_repr():
    config = SubQConfig(api_key=KEY)
    assert KEY not in repr(config)


def test_the_key_stays_out_of_every_serialisation():
    config = SubQConfig(api_key=KEY)
    assert KEY not in repr(config)
    assert KEY not in config.model_dump_json()
    assert KEY not in str(config.model_dump())


def test_the_key_accessor_reads_the_credential():
    config = SubQConfig(api_key=KEY)
    assert config.key() == KEY


def test_an_empty_key_counts_as_no_credential():
    config = SubQConfig(api_key="")
    assert config.key() is None
    assert SubQProvider(config).health() is ProviderHealth.disabled


def test_an_http_base_url_is_refused():
    with pytest.raises(ValueError):
        SubQConfig(base_url="http://api.subq.ai/v1")


def test_a_loopback_http_base_url_is_accepted():
    config = SubQConfig(base_url="http://localhost:8080/v1")
    assert config.base_url == "http://localhost:8080/v1"


def test_an_ftp_base_url_is_refused():
    with pytest.raises(ValueError):
        SubQConfig(base_url="ftp://api.subq.ai/v1")


def test_the_client_it_makes_does_not_follow_redirects():
    client = SubQClient(SubQConfig(api_key=KEY))
    try:
        assert client._client.follow_redirects is False
    finally:
        client.close()


def test_a_redirect_is_not_followed():
    service = FakeService(
        httpx.Response(
            302, headers={"location": "https://elsewhere.example/v1/systemone"}
        )
    )
    provider, _ = build(service)

    outcomes = provider.classify(["a text"], options=OPTIONS)

    assert service.calls == 1
    assert outcomes[0].error is not None


def test_the_config_loader_reads_the_environment(monkeypatch):
    from product_hunter.config import load_subq_config

    monkeypatch.setenv("SUBQ_API_KEY", "key-from-the-environment")
    monkeypatch.setenv("SUBQ_MODEL", "subq-snap-1.1")
    monkeypatch.setenv("PH_SUBQ_MAX_RETRIES", "5")
    monkeypatch.setenv("PH_SUBQ_MAX_USD", "1.5")

    config = load_subq_config()

    assert config.key() == "key-from-the-environment"
    assert config.model == "subq-snap-1.1"
    assert config.max_retries == 5
    assert config.max_usd == 1.5


def test_the_config_loader_leaves_the_provider_disabled_without_a_key(monkeypatch):
    from product_hunter.config import load_subq_config

    monkeypatch.delenv("SUBQ_API_KEY", raising=False)
    monkeypatch.setattr("product_hunter.config.load_dotenv", lambda *a, **k: None)

    config = load_subq_config()

    assert config.api_key is None
    assert SubQProvider(config).health() is ProviderHealth.disabled


# --- the failures ---------------------------------------------------------


def test_a_body_that_is_not_the_documented_shape_is_an_item_failure():
    service = FakeService(
        answer("classification", {"type": "choice", "choice": "finance"}),
        answer(
            "classification",
            {
                "type": "choice",
                "choice": "finance",
                "probabilities": {"finance": 0.8},
                "confidence": 0.9,
            },
        ),
    )
    provider, _ = build(service)

    outcomes = provider.classify(["first", "second"], options=OPTIONS)

    assert len(outcomes) == 2
    assert outcomes[0].error is not None
    assert outcomes[1].error is None
    assert outcomes[1].label == "finance"


def test_a_refused_request_is_recorded_against_the_item():
    service = FakeService(
        refusal(422, "questions/classification/criteria: must be object"),
        answer(
            "classification",
            {
                "type": "choice",
                "choice": "finance",
                "probabilities": {"finance": 0.8},
                "confidence": 0.9,
            },
        ),
    )
    provider, _ = build(service)

    outcomes = provider.classify(["first", "second"], options=OPTIONS)

    assert outcomes[0].error is not None
    assert "must be object" in outcomes[0].error
    assert outcomes[1].error is None


def test_a_timeout_is_retried_and_then_raises():
    def timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    service = FakeService(timeout, timeout, timeout)
    provider, sleeps = build(service)

    with pytest.raises(DecisionUnavailable):
        provider.classify(["a text"], options=OPTIONS)

    assert service.calls == 3
    assert len(sleeps) == 2


def test_a_rate_limit_is_retried_and_retry_after_is_honoured():
    service = FakeService(
        httpx.Response(429, headers={"retry-after": "2"}, json={"detail": {}}),
        httpx.Response(429, headers={"retry-after": "2"}, json={"detail": {}}),
        httpx.Response(429, headers={"retry-after": "2"}, json={"detail": {}}),
    )
    provider, sleeps = build(service)

    with pytest.raises(DecisionUnavailable):
        provider.classify(["a text"], options=OPTIONS)

    assert service.calls == 3
    assert sleeps == [2.0, 2.0]


def test_a_refused_credential_raises_at_once_and_the_health_goes_unavailable():
    service = FakeService(refusal(401, "invalid api key"))
    provider, _ = build(service)

    with pytest.raises(SubQCredentialRejected):
        provider.classify(["a text"], options=OPTIONS)

    assert service.calls == 1
    assert provider.health() is ProviderHealth.unavailable


def test_a_provider_failure_makes_the_router_fall_back_to_the_text_provider():
    service = FakeService(
        httpx.Response(500, json={"detail": {}}),
        httpx.Response(500, json={"detail": {}}),
        httpx.Response(500, json={"detail": {}}),
    )
    subq, _ = build(service)
    text = TextDecisionProvider(
        FakeGenerator('{"label": "finance", "probability": 0.8, "confidence": 0.9}')
    )
    router = DecisionRouter([subq, text])

    outcomes = router.classify(["a text"], options=OPTIONS)

    assert len(outcomes) == 1
    assert outcomes[0].provenance.provider == "fake-provider"
    assert subq.health() is ProviderHealth.degraded


# --- the batch ------------------------------------------------------------


def test_every_item_of_a_batch_gets_an_outcome():
    good = answer(
        "classification",
        {
            "type": "choice",
            "choice": "finance",
            "probabilities": {"finance": 0.8},
            "confidence": 0.9,
        },
    )
    service = FakeService(good, refusal(422, "bad item"), good)
    provider, _ = build(service)

    outcomes = provider.classify(["a", "b", "c"], options=OPTIONS)

    assert [outcome.index for outcome in outcomes] == [0, 1, 2]
    assert outcomes[1].error is not None
    assert outcomes[0].error is None
    assert outcomes[2].error is None


def test_a_batch_over_the_item_limit_is_refused():
    provider, _ = build(FakeService(), max_batch_items=2)
    with pytest.raises(DecisionError) as excinfo:
        provider.classify(["a", "b", "c"], options=OPTIONS)
    assert "batch" in str(excinfo.value)


def test_every_outcome_carries_a_provenance_record():
    service = FakeService(
        answer(
            "classification",
            {
                "type": "choice",
                "choice": "finance",
                "probabilities": {"finance": 0.8},
                "confidence": 0.9,
            },
        )
    )
    provider, _ = build(service)

    outcomes = provider.classify(["a text"], options=OPTIONS)

    record = outcomes[0].provenance
    assert record.provider == "subq"
    assert record.model == "subq-snap-1.1"
    assert record.request_hash.startswith("sha256:")
    assert record.capability == "classify"
    assert record.input_tokens == 100
    assert record.usd_estimate == pytest.approx(100 / 1_000_000 * 0.04)
    assert record.cached is False


def test_the_failed_item_carries_a_provenance_record_too():
    service = FakeService(refusal(422, "bad item"))
    provider, _ = build(service)

    outcomes = provider.classify(["a text"], options=OPTIONS)

    record = outcomes[0].provenance
    assert record.provider == "subq"
    assert record.request_hash.startswith("sha256:")


# --- the budget -----------------------------------------------------------


def test_the_provider_refuses_a_call_that_would_break_the_call_limit():
    service = FakeService(
        answer(
            "classification",
            {
                "type": "choice",
                "choice": "finance",
                "probabilities": {"finance": 0.8},
                "confidence": 0.9,
            },
        )
    )
    provider, _ = build(service, max_calls=1)

    provider.classify(["a text"], options=OPTIONS)
    with pytest.raises(DecisionLimitReached) as excinfo:
        provider.classify(["another text"], options=OPTIONS)

    assert "call limit" in str(excinfo.value)
    assert service.calls == 1


def test_the_provider_refuses_a_call_that_would_break_the_token_limit():
    service = FakeService()
    provider, _ = build(service, max_input_tokens=1)

    with pytest.raises(DecisionLimitReached) as excinfo:
        provider.classify(["a text"], options=OPTIONS)

    assert "input token limit" in str(excinfo.value)
    assert service.calls == 0


def test_the_provider_refuses_a_call_that_would_break_the_cost_limit():
    service = FakeService()
    provider, _ = build(service, max_usd=0.0)

    with pytest.raises(DecisionLimitReached) as excinfo:
        provider.classify(["a text"], options=OPTIONS)

    assert "cost limit" in str(excinfo.value)
    assert service.calls == 0


def test_a_tracker_that_refuses_the_call_refuses_it_here():
    service = FakeService()
    provider, _ = build(service)
    provider.tracker = FakeTracker(refuse="subq_calls")

    with pytest.raises(DecisionBudgetExceeded) as excinfo:
        provider.classify(["a text"], options=OPTIONS)

    assert "subq_calls" in str(excinfo.value)
    assert service.calls == 0


def test_a_tracker_that_refuses_the_record_does_not_fail_the_call():
    class RefusingTracker(FakeTracker):
        def record(self, dimension, quantity, stage=None, source_id=None):
            raise RuntimeError("the usd_cost budget is spent")

    service = FakeService(
        answer(
            "classification",
            {
                "type": "choice",
                "choice": "finance",
                "probabilities": {"finance": 0.8},
                "confidence": 0.9,
            },
        )
    )
    provider, _ = build(service)
    provider.tracker = RefusingTracker()

    outcomes = provider.classify(["a text"], options=OPTIONS)

    assert outcomes[0].error is None
    assert outcomes[0].label == "finance"
    assert provider.spend.overruns
    assert "usd_cost" in provider.spend.overruns[0]


def test_the_ledger_keeps_only_the_last_few_overruns():
    class RefusingTracker(FakeTracker):
        def record(self, dimension, quantity, stage=None, source_id=None):
            raise RuntimeError("the budget is spent")

    service = FakeService(
        *[
            answer(
                "classification",
                {
                    "type": "choice",
                    "choice": "finance",
                    "probabilities": {"finance": 0.8},
                    "confidence": 0.9,
                },
            )
            for _ in range(8)
        ]
    )
    provider, _ = build(service)
    provider.tracker = RefusingTracker()

    for index in range(8):
        provider.classify([f"text {index}"], options=OPTIONS)

    assert len(provider.spend.overruns) == 5


def test_the_spend_is_recorded_in_the_tracker():
    service = FakeService(
        answer(
            "classification",
            {
                "type": "choice",
                "choice": "finance",
                "probabilities": {"finance": 0.8},
                "confidence": 0.9,
            },
            input_tokens=120,
        )
    )
    provider, _ = build(service)
    tracker = FakeTracker()
    provider.tracker = tracker

    provider.classify(["a text"], options=OPTIONS)

    assert ("subq_calls", 1) in tracker.recorded
    assert ("llm_input_tokens", 120) in tracker.recorded
    assert ("usd_cost", pytest.approx(120 / 1_000_000 * 0.04)) in [
        (name, pytest.approx(value)) for name, value in tracker.recorded
    ]


def test_the_ledger_counts_the_call_and_its_tokens():
    service = FakeService(
        answer(
            "classification",
            {
                "type": "choice",
                "choice": "finance",
                "probabilities": {"finance": 0.8},
                "confidence": 0.9,
            },
            input_tokens=120,
        )
    )
    provider, _ = build(service)

    provider.classify(["a text"], options=OPTIONS)

    assert provider.spend.calls == 1
    assert provider.spend.input_tokens == 120
    assert provider.spend.output_tokens == 0
    assert provider.spend.usd_estimate == pytest.approx(120 / 1_000_000 * 0.04)
    assert provider.spend.cache_hits == 0


def test_a_failed_call_still_counts_against_the_call_limit():
    service = FakeService(httpx.Response(500, json={"detail": {}}))
    provider, _ = build(service, max_calls=1, max_retries=0)

    with pytest.raises(DecisionUnavailable):
        provider.classify(["a text"], options=OPTIONS)

    # The request was sent and paid for, even though it failed.
    assert provider.spend.calls == 1
    with pytest.raises(DecisionLimitReached) as excinfo:
        provider.classify(["another text"], options=OPTIONS)

    assert "call limit" in str(excinfo.value)
    assert service.calls == 1


def test_a_cached_answer_is_free_even_at_the_call_limit():
    service = FakeService(_choice_answer())
    provider, _ = build(service, max_calls=1)

    first = provider.classify(["the same text"], options=OPTIONS)
    second = provider.classify(["the same text"], options=OPTIONS)

    assert first[0].provenance.cached is False
    assert second[0].provenance.cached is True
    assert service.calls == 1
    assert provider.spend.calls == 1
    assert provider.spend.cache_hits == 1


def test_the_real_spend_over_the_estimate_is_recorded_as_an_overrun():
    service = FakeService(
        answer(
            "classification",
            {
                "type": "choice",
                "choice": "finance",
                "probabilities": {"finance": 0.8},
                "confidence": 0.9,
            },
            input_tokens=100_000,
        )
    )
    # The estimate fits inside both limits, so the call starts; the real
    # usage the service reports does not, and that is recorded, not raised.
    provider, _ = build(service, max_input_tokens=50_000, max_usd=0.001)

    outcomes = provider.classify(["a text"], options=OPTIONS)

    assert outcomes[0].error is None
    assert provider.spend.input_tokens == 100_000
    assert any("input token limit" in overrun for overrun in provider.spend.overruns)
    assert any("cost limit" in overrun for overrun in provider.spend.overruns)


def test_a_refused_credential_stays_unavailable_until_the_credential_changes():
    service = FakeService(refusal(401, "invalid api key"))
    provider, _ = build(service)

    with pytest.raises(SubQCredentialRejected):
        provider.classify(["a text"], options=OPTIONS)

    assert provider.health() is ProviderHealth.unavailable
    assert provider.health() is ProviderHealth.unavailable

    # A new credential is a new chance: the fatal flag is cleared, and the
    # provider is tried again instead of being held down for a cooldown.
    # The config is shared with the client, so the credential is changed where
    # it is read from, the way an operator would change it.
    provider.config.api_key = SecretStr("a-different-placeholder")

    assert provider.health() is ProviderHealth.healthy


def test_a_label_outside_the_options_is_an_item_failure_naming_the_label():
    service = FakeService(
        answer(
            "classification",
            {
                "type": "choice",
                "choice": "legal",
                "probabilities": {"legal": 0.9},
                "confidence": 0.9,
            },
        ),
        _choice_answer(),
    )
    provider, _ = build(service)

    outcomes = provider.classify(["first", "second"], options=OPTIONS)

    assert outcomes[0].error is not None
    assert "legal" in outcomes[0].error
    assert outcomes[1].error is None
    assert outcomes[1].label == "finance"


def test_a_mid_batch_failure_carries_the_outcomes_already_paid_for():
    service = FakeService(
        _choice_answer(),
        httpx.Response(500, json={"detail": {}}),
    )
    provider, _ = build(service, max_retries=0)

    with pytest.raises(DecisionUnavailable) as excinfo:
        provider.classify(["first", "second"], options=OPTIONS)

    assert [outcome.index for outcome in excinfo.value.outcomes] == [0]
    assert excinfo.value.outcomes[0].label == "finance"


def test_a_duplicate_question_name_is_refused_before_the_call():
    service = FakeService()
    provider, _ = build(service)
    question = DecisionQuestion(
        name="classification",
        type=QuestionType.choice,
        criteria=OPTIONS,
    )

    with pytest.raises(DecisionError) as excinfo:
        provider.client.decide("a text", [question, question])

    assert "twice" in str(excinfo.value)
    assert service.calls == 0


def test_a_failure_that_is_not_a_transport_error_is_not_escaped():
    def undecodable(request: httpx.Request) -> httpx.Response:
        raise httpx.DecodingError("the body could not be decoded", request=request)

    service = FakeService(undecodable)
    provider, _ = build(service, max_retries=0)

    # DecodingError is a RequestError but not a TransportError. It must be
    # a provider failure the chain can fall through, not an httpx error
    # escaping the adapter.
    with pytest.raises(DecisionUnavailable) as excinfo:
        provider.classify(["a text"], options=OPTIONS)

    assert "DecodingError" in str(excinfo.value)
    assert service.calls == 1


# --- the idempotency ------------------------------------------------------


def test_the_same_question_about_the_same_text_twice_costs_one_call():
    service = FakeService(
        answer(
            "classification",
            {
                "type": "choice",
                "choice": "finance",
                "probabilities": {"finance": 0.8},
                "confidence": 0.9,
            },
        )
    )
    provider, _ = build(service)

    first = provider.classify(["the same text"], options=OPTIONS)
    second = provider.classify(["the same text"], options=OPTIONS)

    assert service.calls == 1
    assert first[0].provenance.cached is False
    assert second[0].provenance.cached is True
    assert first[0].provenance.request_hash == second[0].provenance.request_hash
    assert provider.spend.calls == 1
    assert provider.spend.cache_hits == 1


def _choice_answer() -> httpx.Response:
    """A good answer for the classification question."""
    return answer(
        "classification",
        {
            "type": "choice",
            "choice": "finance",
            "probabilities": {"finance": 0.8},
            "confidence": 0.9,
        },
    )


def test_the_cache_never_grows_past_its_cap():
    service = FakeService(*[_choice_answer() for _ in range(4)])
    provider, _ = build(service, max_cache_entries=2)

    for index in range(3):
        provider.classify([f"text {index}"], options=OPTIONS)

    assert len(provider.client._cache) == 2


def test_the_oldest_entry_is_evicted_when_the_cache_is_full():
    service = FakeService(*[_choice_answer() for _ in range(4)])
    provider, _ = build(service, max_cache_entries=2)

    provider.classify(["first"], options=OPTIONS)
    provider.classify(["second"], options=OPTIONS)
    provider.classify(["third"], options=OPTIONS)
    provider.classify(["first"], options=OPTIONS)

    # The first entry was evicted, so it went out again.
    assert service.calls == 4


def test_a_cache_of_zero_means_every_request_goes_out():
    service = FakeService(*[_choice_answer() for _ in range(2)])
    provider, _ = build(service, max_cache_entries=0)

    provider.classify(["the same text"], options=OPTIONS)
    provider.classify(["the same text"], options=OPTIONS)

    assert service.calls == 2
    assert provider.spend.cache_hits == 0


# --- the operations -------------------------------------------------------


def test_a_filter_outcome_carries_the_probability_and_the_gate():
    service = FakeService(answer("filter", {"type": "noul", "noul": 0.8}))
    provider, _ = build(service)

    outcomes = provider.filter_items(
        ["a text"],
        true_description="it costs money",
        false_description="it costs nothing",
        threshold=0.5,
    )

    assert outcomes[0].probability == 0.8
    assert outcomes[0].passed is True
    assert outcomes[0].label == "yes"


def test_a_filter_outcome_below_the_threshold_does_not_pass():
    service = FakeService(answer("filter", {"type": "noul", "noul": 0.4}))
    provider, _ = build(service)

    outcomes = provider.filter_items(
        ["a text"],
        true_description="it costs money",
        false_description="it costs nothing",
        threshold=0.5,
    )

    assert outcomes[0].passed is False
    assert outcomes[0].label == "no"


def test_a_ranking_is_ordered_by_the_weighted_score():
    service = FakeService(
        answer(
            "rank",
            {
                "type": "score",
                "score": 0.1,
                "legend": {"0": "low", "1": "high"},
                "probabilities": {"0": 0.9, "1": 0.1},
                "confidence": 0.8,
            },
        ),
        answer(
            "rank",
            {
                "type": "score",
                "score": 0.9,
                "legend": {"0": "low", "1": "high"},
                "probabilities": {"0": 0.1, "1": 0.9},
                "confidence": 0.8,
            },
        ),
    )
    provider, _ = build(service)

    outcomes = provider.rank(["a", "b"], levels=["low", "high"])

    assert [outcome.index for outcome in outcomes] == [1, 0]
    assert outcomes[0].score == pytest.approx(0.9)
    assert outcomes[1].score == pytest.approx(0.1)


# --- the capabilities -----------------------------------------------------


def test_the_capability_report_names_what_the_api_cannot_do():
    provider, _ = build(FakeService())

    capabilities = {state.name: state for state in provider.capabilities()}

    assert capabilities["text_generation"].available is False
    assert capabilities["text_generation"].reason
    assert capabilities["batch_decision"].available is False
    assert capabilities["batch_decision"].reason
    assert capabilities["classify"].available is True
    assert capabilities["filter_items"].available is True
    assert capabilities["rank"].available is True


def test_the_capability_check_costs_no_tokens():
    service = FakeService(
        httpx.Response(200, json={"object": "list", "data": [{"id": "subq-snap-1.1"}]})
    )
    provider, _ = build(service)

    report = provider.check()

    assert report["model"] == "subq-snap-1.1"
    assert provider.spend.calls == 0


# --- the environment file -------------------------------------------------


def test_the_env_example_ships_the_defaults():
    from product_hunter.config import repo_root

    text = (repo_root() / ".env.example").read_text(encoding="utf-8")
    values = {
        line.split("=", 1)[0]: line.split("=", 1)[1]
        for line in text.splitlines()
        if line and not line.startswith("#") and "=" in line
    }
    config = SubQConfig()

    assert values["SUBQ_BASE_URL"] == config.base_url
    assert values["SUBQ_MODEL"] == config.model
    assert float(values["PH_SUBQ_TIMEOUT_SECONDS"]) == config.timeout_seconds
    assert int(values["PH_SUBQ_MAX_RETRIES"]) == config.max_retries
    assert float(values["PH_SUBQ_BACKOFF_SECONDS"]) == config.backoff_base_seconds
    assert int(values["PH_SUBQ_MAX_CALLS"]) == config.max_calls
    assert int(values["PH_SUBQ_MAX_INPUT_TOKENS"]) == config.max_input_tokens
    assert float(values["PH_SUBQ_MAX_USD"]) == config.max_usd
    assert int(values["PH_SUBQ_MAX_BATCH_ITEMS"]) == config.max_batch_items
    assert float(values["PH_SUBQ_USD_PER_MTOK_INPUT"]) == config.usd_per_mtok_input
    assert int(values["PH_SUBQ_FAILURE_THRESHOLD"]) == config.failure_threshold
    assert float(values["PH_SUBQ_COOLDOWN_SECONDS"]) == config.cooldown_seconds
    assert int(values["PH_SUBQ_MAX_CACHE_ENTRIES"]) == config.max_cache_entries


def test_the_env_example_holds_no_credential():
    from product_hunter.config import repo_root

    text = (repo_root() / ".env.example").read_text(encoding="utf-8")

    assert "SUBQ_API_KEY=\n" in text


# --- the refusals and the malformed base URL ------------------------------


def test_a_refused_call_does_not_spend_the_call_budget():
    """A call that never went out must not count against the call limit.

    The request is counted after every refusal, so a limit that is reached by
    a refusal does not lock the provider out of the calls it may still make.
    """
    service = FakeService()
    provider, _ = build(service, max_input_tokens=1)

    with pytest.raises(DecisionLimitReached):
        provider.classify(["a text"], options=OPTIONS)

    assert provider.spend.calls == 0
    assert service.calls == 0


def test_a_refused_by_the_tracker_call_does_not_spend_the_call_budget():
    """The run budget refuses before the request, so nothing was sent."""
    service = FakeService()
    provider, _ = build(service)
    provider.tracker = FakeTracker(refuse="subq_calls")

    with pytest.raises(DecisionBudgetExceeded):
        provider.classify(["a text"], options=OPTIONS)

    assert provider.spend.calls == 0
    assert service.calls == 0


def test_a_base_url_without_a_host_is_refused():
    """A URL that cannot be built is refused where the message can name it."""
    with pytest.raises(ValueError):
        SubQConfig(api_key=KEY, base_url="https:/api.subq.ai/v1")


def test_a_base_url_with_a_credential_in_it_is_refused():
    """The endpoint goes into every provenance record, so no userinfo."""
    with pytest.raises(ValueError):
        SubQConfig(api_key=KEY, base_url="https://user:token@api.subq.ai/v1")


def test_a_url_that_cannot_be_built_does_not_stop_the_chain():
    """A provider failure of any shape hands the work to the next provider.

    The client is a real one here, so the URL is built for real. The request
    never leaves the machine: httpx refuses the URL before it connects.
    """
    config = SubQConfig(api_key=KEY, max_retries=0)
    provider = SubQProvider(config, client=SubQClient(config))
    # The config was valid when it was built; the URL is broken afterwards.
    provider.config.base_url = "https:/api.subq.ai/v1"
    fallback = FakeGenerator(
        '{"label": "finance", "probability": 0.5, "confidence": 0.5}'
    )
    router = DecisionRouter([provider, TextDecisionProvider(fallback)])

    try:
        outcomes = router.classify(["a text"], options=OPTIONS)
    finally:
        provider.client.close()

    assert [outcome.label for outcome in outcomes] == ["finance"]
    assert outcomes[0].provenance.provider == "fake-provider"


# --- the live test --------------------------------------------------------


@pytest.mark.network
def test_live_subq_lists_the_models_and_answers_one_question():
    key = os.environ.get("SUBQ_API_KEY")
    if not key:
        pytest.skip("SUBQ_API_KEY is not in the environment")
    provider = SubQProvider(SubQConfig(api_key=key))

    report = provider.check()
    assert "subq-snap-1.1" in report["models"]

    outcomes = provider.filter_items(
        [
            (
                "Every month the bookkeeping team exports the invoices to CSV "
                "and pastes the totals into a spreadsheet by hand."
            )
        ],
        true_description="the text names a real cost that someone pays",
        false_description="the text names no cost",
    )

    assert outcomes[0].error is None
    assert outcomes[0].probability is not None
    assert outcomes[0].provenance.provider == "subq"
    assert outcomes[0].provenance.input_tokens > 0

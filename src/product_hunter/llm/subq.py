"""The SubQ Snap decision provider: one HTTP client, one provider.

The provider is optional. With no credential it reports itself disabled and
the chain moves on. The client owns the timeout, the retry, the rate limit,
the request hash and the answer cache; the provider owns the capability
report, the health state, the spend ledger and the three operations. The
key is read from the environment and never enters a log line, an exception
message, a provenance record or a file.

The API decides over one state and has no batch endpoint, so a batch is one
request per item. The capability report says so in words.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections import OrderedDict
from collections.abc import Callable, Mapping, Sequence
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit

import httpx
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    ValidationError,
    model_validator,
)

from product_hunter.llm.base import ProviderError, first_error
from product_hunter.llm.catalog import utcnow
from product_hunter.llm.decision import (
    MAX_QUESTIONS,
    CapabilityState,
    DecisionAnswer,
    DecisionBudgetExceeded,
    DecisionError,
    DecisionLimitReached,
    DecisionOutcome,
    DecisionProvenance,
    DecisionQuestion,
    DecisionResponseInvalid,
    DecisionUnavailable,
    ProviderHealth,
    QuestionType,
    SpendTracker,
)

DEFAULT_BASE_URL = "https://api.subq.ai/v1"
DEFAULT_MODEL = "subq-snap-preview"

# The price of a million input tokens. The output is free.
DEFAULT_USD_PER_MTOK_INPUT = 0.05

# The dimensions the research contract already declares.
DIMENSION_CALLS = "subq_calls"
DIMENSION_TOKENS = "llm_input_tokens"
DIMENSION_USD = "usd_cost"

# The statuses that are worth another attempt: a rate limit and a server
# error. The others say something about the request or the credential, and
# a retry of those is a wasted call.
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}

# The characters per token used for the size estimate. A JSON body packs
# more tokens per character than prose, so three is deliberately low: the
# margin is there because the estimate must not understate the spend. The
# real count is reported by the service and replaces the estimate when the
# answer arrives.
_CHARS_PER_TOKEN = 3

# The bound on a message the service sent, so nothing unbounded reaches an
# exception message.
MAX_DETAIL_CHARS = 500

# The number of budget overruns the ledger keeps. The overrun is a record of
# what the tracker said, not a decision, so a few of them are enough.
MAX_OVERRUNS = 5


class SubQConfig(BaseModel):
    """Everything the provider needs, and the limits it holds itself to."""

    model_config = ConfigDict(extra="forbid")

    base_url: str = Field(default=DEFAULT_BASE_URL, min_length=1)
    model: str = Field(default=DEFAULT_MODEL, min_length=1)
    # The key is held in memory and read from the environment. It is a
    # secret, so it stays masked in the repr and in every serialisation.
    api_key: SecretStr | None = Field(default=None, repr=False)
    timeout_seconds: float = Field(default=60.0, gt=0)
    max_retries: int = Field(default=3, ge=0)
    backoff_base_seconds: float = Field(default=1.0, ge=0)
    backoff_max_seconds: float = Field(default=30.0, ge=0)
    max_calls: int = Field(default=1000, ge=0)
    max_input_tokens: int = Field(default=1_000_000, ge=0)
    max_usd: float = Field(default=5.0, ge=0)
    max_batch_items: int = Field(default=20, ge=1)
    usd_per_mtok_input: float = Field(default=DEFAULT_USD_PER_MTOK_INPUT, ge=0)
    failure_threshold: int = Field(default=3, ge=1)
    cooldown_seconds: float = Field(default=60.0, ge=0)
    max_cache_entries: int = Field(default=256, ge=0)

    @model_validator(mode="after")
    def _check_backoff(self) -> SubQConfig:
        """The cap must not be below the base, or the first wait is longer."""
        if self.backoff_max_seconds < self.backoff_base_seconds:
            raise ValueError(
                "backoff_max_seconds must not be below backoff_base_seconds, got "
                f"{self.backoff_max_seconds} and {self.backoff_base_seconds}"
            )
        return self

    @model_validator(mode="after")
    def _check_base_url(self) -> SubQConfig:
        """The credential must not travel in the clear.

        The base URL is operator-configurable, so a plain http URL would put
        the bearer token on the wire in the clear. https is required, and
        http is allowed only for a loopback address, which never leaves the
        machine.
        """
        parsed = urlsplit(self.base_url)
        if parsed.scheme not in ("https", "http"):
            raise ValueError(
                f"the SubQ base URL must be https, got {parsed.scheme!r}"
            )
        if parsed.scheme == "http" and parsed.hostname not in (
            "localhost",
            "127.0.0.1",
            "::1",
        ):
            raise ValueError(
                "the SubQ base URL must be https unless it points at localhost"
            )
        if not parsed.hostname:
            # A URL with no host cannot be called, and httpx reports it as a
            # plain ValueError rather than an httpx error, so the refusal
            # belongs here where the message can name the URL.
            raise ValueError(
                f"the SubQ base URL has no host: {self.base_url!r}"
            )
        if parsed.username or parsed.password:
            # The endpoint goes into every provenance record, so credentials
            # in the URL would be written down with every answer.
            raise ValueError(
                "the SubQ base URL must not carry a username or a password"
            )
        return self

    def key(self) -> str | None:
        """The credential, or None when there is none.

        An empty string counts as no credential.
        """
        value = self.api_key.get_secret_value() if self.api_key is not None else ""
        return value or None

    @property
    def decisions_url(self) -> str:
        """The URL of the decisions endpoint."""
        return f"{self.base_url.rstrip('/')}/systemone"

    @property
    def models_url(self) -> str:
        """The URL of the model list, which is free."""
        return f"{self.base_url.rstrip('/')}/models"


class SubQCredentialRejected(DecisionUnavailable):
    """The credential or the model is refused, so the provider stops.

    The refusal concerns the provider and not one item: every later call
    would fail the same way, so the provider reports itself unavailable.
    """


class SubQUsage(BaseModel):
    """The token counts the service reported.

    The usage block is a free-form accounting block, so an extra field the
    service adds later is ignored rather than turning every answer into an
    item failure. The answer envelope above it stays strict.
    """

    model_config = ConfigDict(extra="ignore")

    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)


class SubQEnvelope(BaseModel):
    """The answer envelope of the decisions endpoint.

    A field the service adds later is ignored rather than turning every
    answer into an item failure. The fields the contract names stay
    required, so a body without them is still refused.
    """

    model_config = ConfigDict(extra="ignore")

    model: str
    answers: dict[str, DecisionAnswer]
    usage: SubQUsage = Field(default_factory=SubQUsage)


class SubQResult(BaseModel):
    """One answered request, with what it cost and where it came from."""

    model_config = ConfigDict(extra="forbid")

    answers: dict[str, DecisionAnswer]
    usage: SubQUsage = Field(default_factory=SubQUsage)
    request_hash: str
    attempt_count: int = 0
    latency_ms: int = 0
    cached: bool = False


class SubQSpend(BaseModel):
    """What the provider spent, and what it saved."""

    model_config = ConfigDict(extra="forbid")

    # Requests made, not successful answers: a request that failed was
    # still sent and still counts against the call limit.
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    usd_estimate: float = 0.0
    cache_hits: int = 0
    # The budget overruns the tracker reported after a call. The call
    # happened and the answers stand, so an overrun is recorded and not
    # raised.
    overruns: list[str] = Field(default_factory=list)


class SubQClient:
    """One HTTP client for the decisions endpoint.

    The client owns the timeout, the retry, the rate limit, the request
    hash and the answer cache. The API has no idempotency key, so the cache
    is the idempotency: the same question about the same text is answered
    from the cache without a call and without a cost. The cache is bounded,
    so a long-running process does not grow without limit.
    """

    def __init__(
        self,
        config: SubQConfig,
        *,
        client: httpx.Client | None = None,
        sleeper: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.config = config
        # A redirect is not followed: a 30x would carry the bearer token to
        # a second host. A client the caller injects is the caller's choice.
        self._client = (
            client if client is not None else httpx.Client(follow_redirects=False)
        )
        self._owns_client = client is None
        self._sleeper = sleeper
        self._clock = clock
        self._cache: OrderedDict[str, SubQResult] = OrderedDict()

    def close(self) -> None:
        """Close the client, when this object made it."""
        if self._owns_client:
            self._client.close()

    def decide(
        self,
        state: str,
        questions: Sequence[DecisionQuestion],
        *,
        authorize: Callable[[], None] | None = None,
    ) -> SubQResult:
        """Answer the questions about one state, or refuse the request.

        ``authorize`` is asked before every HTTP request, so a budget that
        counts requests sees the attempt even when the attempt fails. A
        cached answer is returned before ``authorize`` is asked, because it
        makes no request and costs nothing.
        """
        if not questions:
            raise DecisionError("a decision request needs at least one question")
        if len(questions) > MAX_QUESTIONS:
            raise DecisionError(
                f"a decision request takes at most {MAX_QUESTIONS} questions, "
                f"got {len(questions)}"
            )
        # The questions travel as a dict keyed by the name, so two questions
        # with the same name would collapse into one and only one would be
        # answered.
        seen: set[str] = set()
        for question in questions:
            if question.name in seen:
                raise DecisionError(
                    f"a decision request names the question {question.name!r} "
                    "twice"
                )
            seen.add(question.name)
        request_hash = self.request_hash(state, questions)
        cached = self._cache.get(request_hash)
        if cached is not None:
            # The entry is the most recently used one, so it is the last to
            # be evicted. No request is made and authorize is not asked: a
            # repeated question is free even at the call limit.
            self._cache.move_to_end(request_hash)
            # A cached answer costs nothing, so it carries no usage and no
            # attempt. The provenance says the answer came from the cache.
            return cached.model_copy(
                update={
                    "usage": SubQUsage(),
                    "attempt_count": 0,
                    "latency_ms": 0,
                    "cached": True,
                }
            )
        body = {
            "model": self.config.model,
            "state": state,
            "questions": {
                question.name: question.as_payload() for question in questions
            },
        }
        # Authorize every attempt before it is made: a budget that counts
        # requests sees the attempt even when the attempt fails, so a retry
        # is a second request and a second call. A cached answer is returned
        # above, so authorize is never asked for a hit.
        started = self._clock()
        attempts = 0
        while True:
            if authorize is not None:
                authorize()
            attempts += 1
            try:
                response = self._client.post(
                    self.config.decisions_url,
                    json=body,
                    headers=self._headers(),
                    timeout=self.config.timeout_seconds,
                )
            except httpx.TimeoutException as exc:
                if attempts > self.config.max_retries:
                    raise DecisionUnavailable(
                        f"the SubQ request timed out after {attempts} attempts"
                    ) from exc
                self._sleeper(self._delay(attempts - 1, None))
                continue
            except httpx.RequestError as exc:
                if attempts > self.config.max_retries:
                    raise DecisionUnavailable(
                        f"the SubQ request failed after {attempts} attempts: "
                        f"{type(exc).__name__}"
                    ) from exc
                self._sleeper(self._delay(attempts - 1, None))
                continue
            except (httpx.InvalidURL, ValueError) as exc:
                # A URL that cannot be built is not worth a retry, and httpx
                # reports it as a plain ValueError rather than an httpx error,
                # so without this clause it would escape the chain.
                raise DecisionUnavailable(
                    f"the SubQ request could not be sent: "
                    f"{type(exc).__name__}: {exc}"
                ) from exc
            status = response.status_code
            if status == 200:
                break
            if status in _RETRYABLE_STATUS:
                if attempts > self.config.max_retries:
                    raise DecisionUnavailable(
                        f"the SubQ request failed with HTTP {status} after "
                        f"{attempts} attempts"
                    )
                self._sleeper(self._delay(attempts - 1, response))
                continue
            if status in (401, 402, 403):
                raise SubQCredentialRejected(
                    f"the SubQ request was refused with HTTP {status}: "
                    f"{_error_detail(response)}"
                )
            raise DecisionResponseInvalid(
                f"the SubQ request was refused with HTTP {status}: "
                f"{_error_detail(response)}"
            )
        latency_ms = round((self._clock() - started) * 1000)
        result = self._parse(response, request_hash, attempts, latency_ms)
        self._remember(result)
        return result

    def _remember(self, result: SubQResult) -> None:
        """Keep the answer under its hash, and keep the cache bounded.

        The cap is on the number of entries, so a long-running process does
        not grow without limit. A cap of zero means no cache at all: every
        request goes out.
        """
        cap = self.config.max_cache_entries
        if cap == 0:
            return
        self._cache[result.request_hash] = result
        self._cache.move_to_end(result.request_hash)
        while len(self._cache) > cap:
            self._cache.popitem(last=False)

    def models(self) -> list[str]:
        """The model ids the service offers. The call is free."""
        try:
            response = self._client.get(
                self.config.models_url,
                headers=self._headers(),
                timeout=self.config.timeout_seconds,
            )
        except httpx.RequestError as exc:
            raise DecisionUnavailable(
                f"the SubQ model list failed: {type(exc).__name__}"
            ) from exc
        except (httpx.InvalidURL, ValueError) as exc:
            # The same reason as in decide(): a URL that cannot be built is
            # reported as a plain ValueError, and it must not escape.
            raise DecisionUnavailable(
                f"the SubQ model list could not be sent: "
                f"{type(exc).__name__}: {exc}"
            ) from exc
        if response.status_code != 200:
            raise DecisionUnavailable(
                f"the SubQ model list failed with HTTP {response.status_code}"
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise DecisionResponseInvalid("the SubQ model list is not JSON") from exc
        rows = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(rows, list):
            raise DecisionResponseInvalid("the SubQ model list has no 'data' array")
        return [
            row["id"]
            for row in rows
            if isinstance(row, dict) and isinstance(row.get("id"), str)
        ]

    def request_hash(self, state: str, questions: Sequence[DecisionQuestion]) -> str:
        """The digest of the model, the state and the questions.

        The same question about the same text always has the same hash, so
        the cache can answer a repeated request without a call.
        """
        canonical = json.dumps(
            {
                "model": self.config.model,
                "state": state,
                "questions": {
                    question.name: question.as_payload() for question in questions
                },
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def estimate_tokens(
        self, state: str, questions: Sequence[DecisionQuestion]
    ) -> int:
        """The size of a request in tokens, for the budget check.

        The estimate is the size of the body over three characters per token.
        The real count is reported by the service and replaces it.
        """
        body = json.dumps(
            {
                "model": self.config.model,
                "state": state,
                "questions": {
                    question.name: question.as_payload() for question in questions
                },
            },
            ensure_ascii=False,
        )
        return max(1, len(body) // _CHARS_PER_TOKEN)

    def _parse(
        self,
        response: httpx.Response,
        request_hash: str,
        attempts: int,
        latency_ms: int,
    ) -> SubQResult:
        """Parse the answer envelope, or refuse the body by name."""
        try:
            payload = response.json()
        except ValueError as exc:
            raise DecisionResponseInvalid("the SubQ answer is not JSON") from exc
        if not isinstance(payload, dict):
            raise DecisionResponseInvalid("the SubQ answer is not a JSON object")
        try:
            envelope = SubQEnvelope.model_validate(payload)
        except ValidationError as exc:
            raise DecisionResponseInvalid(
                f"the SubQ answer is not the documented shape: {first_error(exc)}"
            ) from exc
        return SubQResult(
            answers=envelope.answers,
            usage=envelope.usage,
            request_hash=request_hash,
            attempt_count=attempts,
            latency_ms=latency_ms,
        )

    def _headers(self) -> dict[str, str]:
        """The request headers, with the bearer token."""
        headers = {"Accept": "application/json"}
        key = self.config.key()
        if key:
            headers["Authorization"] = f"Bearer {key}"
        return headers

    def _delay(self, retries: int, response: httpx.Response | None) -> float:
        """The wait before the next attempt, with the rate limit honoured."""
        wait = _retry_after(response) if response is not None else None
        if wait is None:
            wait = self.config.backoff_base_seconds * (2**retries)
        return min(wait, self.config.backoff_max_seconds)


class SubQProvider:
    """The SubQ Snap decision provider.

    The provider owns the capability report, the health state, the spend
    ledger and the three operations. A failure that concerns one item is
    recorded against that item and the batch continues; a failure that
    concerns the whole provider is raised, and the chain moves on.
    """

    name = "subq"

    def __init__(
        self,
        config: SubQConfig,
        *,
        client: SubQClient | None = None,
        tracker: SpendTracker | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.config = config
        self.client = client if client is not None else SubQClient(config)
        self.tracker = tracker
        self.spend = SubQSpend()
        self._clock = clock
        self._failures = 0
        self._opened_at: float | None = None
        # The credential that was refused with a fatal failure. While it is
        # set and unchanged, the provider stays unavailable: probing it with
        # the same broken credential would never succeed.
        self._fatal: str | None = None

    # --- health ----------------------------------------------------------

    def health(self) -> ProviderHealth:
        """What the provider reports about itself."""
        if not self.config.key():
            return ProviderHealth.disabled
        # A refused credential keeps the provider unavailable until the
        # credential changes. When the operator changes the key, clear the
        # flag and the circuit and carry on: the provider may work again.
        if self._fatal is not None:
            if self._fatal == self.config.key():
                return ProviderHealth.unavailable
            self._fatal = None
            self._failures = 0
            self._opened_at = None
        if self._opened_at is not None:
            if self._clock() - self._opened_at < self.config.cooldown_seconds:
                return ProviderHealth.unavailable
            # The cooldown has passed, so the provider is tried again. It is
            # degraded until a call succeeds.
            return ProviderHealth.degraded
        if self._failures:
            return ProviderHealth.degraded
        return ProviderHealth.healthy

    def capabilities(self) -> list[CapabilityState]:
        """What the API can do, and what it cannot do."""
        return [
            CapabilityState(
                name="text_generation",
                available=False,
                reason=(
                    "the Decisions API answers structured questions and returns "
                    "no free text"
                ),
            ),
            CapabilityState(
                name="batch_decision",
                available=False,
                reason=(
                    "the Decisions API decides over one state; an array state is "
                    "one decision about the list, so a batch costs one request "
                    "per item"
                ),
            ),
            CapabilityState(
                name="classify",
                available=True,
                reason="a choice question returns the option and its probability",
            ),
            CapabilityState(
                name="filter_items",
                available=True,
                reason="a noul question returns the probability of the true branch",
            ),
            CapabilityState(
                name="rank",
                available=True,
                reason=(
                    "a score question returns the level, the probabilities of "
                    "the levels and the confidence"
                ),
            ),
        ]

    def check(self) -> dict[str, object]:
        """Verify the model is offered. The call is free.

        The model list costs nothing, so a check never spends a token.
        """
        models = self.client.models()
        if self.config.model not in models:
            raise DecisionUnavailable(
                f"the model {self.config.model!r} is not in the SubQ model list"
            )
        return {"model": self.config.model, "models": models}

    # --- the operations --------------------------------------------------

    def classify(
        self,
        items: Sequence[str],
        *,
        options: Mapping[str, str],
        instructions: str = "",
    ) -> list[DecisionOutcome]:
        """Give every item one label out of the options."""
        question = DecisionQuestion(
            name="classification",
            type=QuestionType.choice,
            instructions=instructions
            or "Choose the option that fits the text best.",
            criteria=dict(options),
        )

        def build(
            answer: DecisionAnswer, index: int, provenance: DecisionProvenance
        ) -> DecisionOutcome:
            if answer.choice not in options:
                raise DecisionResponseInvalid(
                    f"the service answered {_bounded(answer.choice or '')!r}, which "
                    "is not one of the options"
                )
            return _choice_outcome(answer, index, provenance)

        return self._run(items, question, "classify", build)

    def filter_items(
        self,
        items: Sequence[str],
        *,
        true_description: str,
        false_description: str,
        instructions: str = "",
        threshold: float = 0.5,
    ) -> list[DecisionOutcome]:
        """Say whether every item passes the gate."""
        question = DecisionQuestion(
            name="filter",
            type=QuestionType.noul,
            instructions=instructions
            or "Decide whether the text passes the gate.",
            criteria={"true": true_description, "false": false_description},
        )
        return self._run(
            items,
            question,
            "filter_items",
            lambda answer, index, provenance: _noul_outcome(
                answer, index, threshold, provenance
            ),
        )

    def rank(
        self,
        items: Sequence[str],
        *,
        levels: Sequence[str],
        instructions: str = "",
    ) -> list[DecisionOutcome]:
        """Score every item on the scale and order the items by the score."""
        question = DecisionQuestion(
            name="rank",
            type=QuestionType.score,
            instructions=instructions or "Rate the text on the scale.",
            criteria=list(levels),
        )
        outcomes = self._run(items, question, "rank", _score_outcome)
        return sorted(outcomes, key=lambda outcome: outcome.score or 0.0, reverse=True)

    # --- the batch -------------------------------------------------------

    def _run(
        self,
        items: Sequence[str],
        question: DecisionQuestion,
        capability: str,
        build: Callable[[DecisionAnswer, int, DecisionProvenance], DecisionOutcome],
    ) -> list[DecisionOutcome]:
        """Ask about every item, one request per item."""
        self._require_credential()
        if len(items) > self.config.max_batch_items:
            raise DecisionError(
                f"a batch of {len(items)} items is over the limit of "
                f"{self.config.max_batch_items} items per call"
            )
        outcomes: list[DecisionOutcome] = []
        for index, item in enumerate(items):
            # The accounting happens around the request, not around the
            # answer: authorize is asked before the HTTP call and counts the
            # request even when it fails. A cached answer is free and is not
            # authorized at all.
            def authorize(item: str = item) -> None:
                # 1. the provider's own call limit. The request is counted
                # further down, after every refusal, so a call that was never
                # made does not spend the budget. A request that was made and
                # failed still counts, because it was made.
                limit = self.config.max_calls
                if self.spend.calls >= limit:
                    raise DecisionLimitReached(
                        f"the SubQ call limit of {limit} is reached"
                    )
                # 2. the provider's own token limit, against the estimate.
                estimate = self.client.estimate_tokens(item, [question])
                if self.spend.input_tokens + estimate > self.config.max_input_tokens:
                    raise DecisionLimitReached(
                        f"the SubQ input token limit of {self.config.max_input_tokens} "
                        f"would be broken: {self.spend.input_tokens} + {estimate}"
                    )
                # 3. the provider's own dollar limit, against the estimate.
                usd = self._usd(estimate)
                if self.spend.usd_estimate + usd > self.config.max_usd:
                    raise DecisionLimitReached(
                        f"the SubQ cost limit of {self.config.max_usd} USD would be "
                        f"broken: {self.spend.usd_estimate} + {usd}"
                    )
                # 4. the tracker guards, the run budget.
                if self.tracker is not None:
                    self._guard_tracker(DIMENSION_CALLS, 1)
                    self._guard_tracker(DIMENSION_TOKENS, estimate)
                    self._guard_tracker(DIMENSION_USD, usd)
                # 5. the request is about to go out, so it is counted.
                self.spend.calls += 1
                # 6. the tracker records the call. A refusal here is recorded
                # as an overrun and does not fail the request.
                if self.tracker is not None:
                    try:
                        self.tracker.record(DIMENSION_CALLS, 1)
                    except Exception as exc:
                        self._note_overrun(
                            f"the subq_calls budget reported an overrun: {exc}"
                        )

            try:
                result = self.client.decide(item, [question], authorize=authorize)
            except DecisionBudgetExceeded as exc:
                # The run budget said no: re-raise so the router does not
                # fall through to a provider the run did not allow. The
                # outcomes already paid for travel with the refusal.
                raise self._carried(exc, outcomes) from exc
            except DecisionLimitReached as exc:
                # The provider's own limit said no: the chain exists for
                # that, so it reaches the router with what was answered.
                raise self._carried(exc, outcomes) from exc
            except DecisionResponseInvalid as exc:
                # The body is about this item, so the batch continues.
                outcomes.append(
                    self._failed(index, capability, str(exc), question, item)
                )
                continue
            except SubQCredentialRejected as exc:
                self._fail(fatal=True)
                raise self._carried(exc, outcomes) from exc
            except DecisionUnavailable as exc:
                self._fail()
                raise self._carried(exc, outcomes) from exc
            except ProviderError:
                self._fail()
                raise
            self._record_spend(result)
            self._succeed()
            answer = result.answers.get(question.name)
            if answer is None:
                outcomes.append(
                    self._failed(
                        index,
                        capability,
                        f"the answer carries no question named {question.name!r}",
                        question,
                        item,
                    )
                )
                continue
            try:
                outcomes.append(
                    build(answer, index, self._provenance(capability, result))
                )
            except DecisionResponseInvalid as exc:
                # The answer is about this item only: a label outside the
                # options is that item's failure, not the batch's.
                outcomes.append(
                    self._failed(index, capability, str(exc), question, item)
                )
        return outcomes

    def _carried(
        self, exc: DecisionUnavailable, outcomes: Sequence[DecisionOutcome]
    ) -> DecisionUnavailable:
        """The refusal, carrying the outcomes already paid for.

        The router keeps those outcomes and asks the next provider only
        about the items that are still missing, so a mid-batch failure is
        never bought twice.
        """
        if not outcomes:
            return exc
        return type(exc)(str(exc), outcomes=outcomes)

    def _failed(
        self,
        index: int,
        capability: str,
        detail: str,
        question: DecisionQuestion,
        item: str,
    ) -> DecisionOutcome:
        """One item that the service did not answer."""
        return DecisionOutcome(
            index=index,
            error=detail,
            provenance=DecisionProvenance(
                provider=self.name,
                model=self.config.model,
                endpoint=self.config.decisions_url,
                request_hash=self.client.request_hash(item, [question]),
                capability=capability,
                decided_at=utcnow(),
            ),
        )

    def _provenance(
        self, capability: str, result: SubQResult
    ) -> DecisionProvenance:
        """The record of the call that produced the answer."""
        return DecisionProvenance(
            provider=self.name,
            model=self.config.model,
            endpoint=self.config.decisions_url,
            request_hash=result.request_hash,
            capability=capability,
            attempt_count=result.attempt_count,
            latency_ms=result.latency_ms,
            input_tokens=result.usage.input_tokens,
            output_tokens=result.usage.output_tokens,
            usd_estimate=self._usd(result.usage.input_tokens),
            cached=result.cached,
            decided_at=utcnow(),
        )

    # --- the budget ------------------------------------------------------

    def _require_credential(self) -> None:
        """Refuse the call when there is no credential to make it with."""
        if not self.config.key():
            raise DecisionUnavailable(
                "the SubQ provider has no credential, so it is disabled"
            )

    def _guard_tracker(self, dimension: str, quantity: float) -> None:
        """Ask the tracker whether the call may start.

        The tracker is reached through a protocol, so this module does not
        know its error tree. A tracker that refuses the call refuses it
        here, and the refusal names the dimension.
        """
        assert self.tracker is not None
        try:
            self.tracker.guard(dimension, quantity)
        except Exception as exc:
            raise DecisionBudgetExceeded(
                f"the {dimension} budget refused the call: {exc}"
            ) from exc

    def _record_spend(self, result: SubQResult) -> None:
        """Record what the call spent, in the ledger and in the tracker.

        ``spend.calls`` was already incremented by ``authorize`` before the
        request started, so it is not counted here again: the request was
        made whether or not it succeeded.
        """
        if result.cached:
            self.spend.cache_hits += 1
            return
        tokens = result.usage.input_tokens
        usd = self._usd(tokens)
        self.spend.input_tokens += tokens
        self.spend.output_tokens += result.usage.output_tokens
        self.spend.usd_estimate += usd
        if self.tracker is not None:
            self._record_tracker(DIMENSION_TOKENS, tokens)
            self._record_tracker(DIMENSION_USD, usd)
        # The real usage can exceed the conservative estimate. When it does,
        # record the break so it is visible instead of silent. A recorded
        # overrun is not an exception: the call already happened and the
        # answer is real.
        if self.spend.input_tokens > self.config.max_input_tokens:
            self._note_overrun(
                f"the SubQ input token limit of {self.config.max_input_tokens} "
                f"is broken: {self.spend.input_tokens} input tokens"
            )
        if self.spend.usd_estimate > self.config.max_usd:
            self._note_overrun(
                f"the SubQ cost limit of {self.config.max_usd} USD is broken: "
                f"{self.spend.usd_estimate:.6f} USD"
            )

    def _record_tracker(self, dimension: str, quantity: float) -> None:
        """Record the spend in the tracker, and keep an overrun out of the way.

        The tracker records the spend and then reports an overrun. The call
        already happened and the answers stand, so the overrun is recorded
        in the ledger and not raised: a completed call that turned into a
        failure would make the chain run the batch again and pay for it
        twice.
        """
        assert self.tracker is not None
        try:
            self.tracker.record(dimension, quantity)
        except Exception as exc:
            # The catch is blind on purpose: the tracker is reached through
            # a protocol, so its error tree is unknown here, and a tracker
            # that reports an overrun must not fail a call that happened.
            self._note_overrun(f"the {dimension} budget reported an overrun: {exc}")

    def _note_overrun(self, message: str) -> None:
        """Keep the last few overruns, so the ledger stays bounded."""
        self.spend.overruns.append(message)
        del self.spend.overruns[:-MAX_OVERRUNS]

    def _usd(self, tokens: int) -> float:
        """The estimated price of the input tokens."""
        return tokens / 1_000_000 * self.config.usd_per_mtok_input

    # --- the circuit -----------------------------------------------------

    def _fail(self, *, fatal: bool = False) -> None:
        """Record a provider failure and open the circuit when it must."""
        self._failures += 1
        if fatal:
            # The credential or the model is wrong: hold the refused
            # credential so the provider stays unavailable until it changes.
            self._fatal = self.config.key()
            self._failures = max(self._failures, self.config.failure_threshold)
        if self._failures >= self.config.failure_threshold:
            self._opened_at = self._clock()

    def _succeed(self) -> None:
        """Close the circuit: one success ends the failure run."""
        self._failures = 0
        self._opened_at = None
        self._fatal = None


def _choice_outcome(
    answer: DecisionAnswer, index: int, provenance: DecisionProvenance
) -> DecisionOutcome:
    """The outcome of a choice answer: the label and its probability."""
    probabilities = answer.probabilities or {}
    return DecisionOutcome(
        index=index,
        label=answer.choice,
        probability=probabilities.get(answer.choice or ""),
        confidence=answer.confidence,
        provenance=provenance,
    )


def _noul_outcome(
    answer: DecisionAnswer,
    index: int,
    threshold: float,
    provenance: DecisionProvenance,
) -> DecisionOutcome:
    """The outcome of a noul answer: the probability of yes and the gate."""
    probability = answer.noul or 0.0
    passed = probability >= threshold
    return DecisionOutcome(
        index=index,
        label="yes" if passed else "no",
        probability=probability,
        score=1.0 if passed else 0.0,
        passed=passed,
        provenance=provenance,
    )


def _score_outcome(
    answer: DecisionAnswer, index: int, provenance: DecisionProvenance
) -> DecisionOutcome:
    """The outcome of a score answer: the weighted score of the item."""
    return DecisionOutcome(
        index=index,
        label=_legend_label(answer),
        score=_weighted_score(answer),
        confidence=answer.confidence,
        provenance=provenance,
    )


def _weighted_score(answer: DecisionAnswer) -> float:
    """The probability-weighted score, which may fall between two levels.

    The probabilities of a score answer are keyed by the level index, and
    the legend names the level each index stands for.
    """
    weighted = 0.0
    total = 0.0
    for key, probability in (answer.probabilities or {}).items():
        try:
            level = int(key)
        except (TypeError, ValueError):
            return answer.score or 0.0
        weighted += probability * level
        total += probability
    if total <= 0:
        return answer.score or 0.0
    return weighted / total


def _legend_label(answer: DecisionAnswer) -> str | None:
    """The level the score fell in, named by the legend."""
    score = answer.score
    if score is None:
        return None
    legend = answer.legend or {}
    best: str | None = None
    best_distance = 0.5
    for key, description in legend.items():
        try:
            level = int(key)
        except (TypeError, ValueError):
            continue
        distance = abs(level - score)
        if distance < best_distance:
            best = description
            best_distance = distance
    return best


def _retry_after(response: httpx.Response | None) -> float | None:
    """The wait the rate limit asked for, in seconds."""
    if response is None:
        return None
    value = response.headers.get("retry-after")
    if not value:
        return None
    value = value.strip()
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if when is None:
        return None
    now = utcnow()
    if when.tzinfo is None:
        when = when.replace(tzinfo=now.tzinfo)
    return max(0.0, (when - now).total_seconds())


def _error_detail(response: httpx.Response) -> str:
    """The message the service sent.

    The error envelope is ``{"detail": {"error_type": ..., "message": ...}}``.
    Any other shape is named and not quoted, so nothing unbounded reaches an
    exception message.
    """
    try:
        payload = response.json()
    except ValueError:
        return "the service sent no detail"
    detail = payload.get("detail") if isinstance(payload, dict) else None
    if isinstance(detail, dict):
        message = detail.get("message")
        error_type = detail.get("error_type")
        if isinstance(message, str) and message:
            if isinstance(error_type, str) and error_type:
                return _bounded(f"{error_type}: {message}")
            return _bounded(message)
    return "the service sent no detail"


def _bounded(message: str) -> str:
    """The message from the service, with its length bounded.

    The service is another party, so its message is bounded before it
    reaches an exception message or a record.
    """
    if len(message) <= MAX_DETAIL_CHARS:
        return message
    return message[:MAX_DETAIL_CHARS] + "..."

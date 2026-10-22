# Spec: SubQ Snap as an optional decision provider

Status: written at stage 1 of the wave 3 session 5 run.
Risk: declared high by the orchestrator (a provider credential is read and used).
SubQ Snap at stage 0 answered `medium` for the risk (p = 0.958, confidence 0.938),
`backend` for the lane (p = 0.987) and `deep` for the depth (p = 0.966). One call,
444 input tokens, model `subq-snap-preview`. The adapter floor in
`.loop/adapter.json` is medium, so the run stayed at the declared high.

## 1. What this is

Product Hunter gets one more decision provider. SubQ Snap answers structured
questions about text: is this a real pain, which of these five areas does it
belong to, how severe is it on this scale. The provider is optional. With no
credential, with the API down, or with the model withdrawn, Product Hunter keeps
running on the providers it already has.

The provider is reached through the routing layer. Nothing in the research
pipeline names SubQ, and no pipeline stage is modified. The adapter is a
library with no caller, which is the same shape as the other wave 2 subsystems.

### Non-goals

- SubQ is never a hard dependency. No import of the SubQ module may be needed
  for the pipeline to run.
- The model routing of `config/model_routing.yaml` is not redesigned. The
  thirteen `ModelRole` members, the `ModelRouter`, and the catalogue stay as
  they are.
- No free-text generation through SubQ. The Decisions API does not return text,
  and a second endpoint would double the surface for a use the text model
  already serves.
- No persistence. The provider records spend and provenance in memory and
  returns them. A table for it arrives with the wave that owns the migration.
- SubQ is not assumed to be the better model. It is one candidate in a chain,
  and the benchmark gate of spec 38A is what would promote it.

## 2. The API contract, verified live

The official documentation was read on 2026-10-22
(`docs.subq.ai/endpoints/decisions-api`, `docs.subq.ai/endpoints/models-api`,
`docs.subq.ai/using-the-api/authentication`, `docs.subq.ai/using-the-api/models`,
`docs.subq.ai/using-the-api/data-retention`) and every shape below was then
confirmed against the live service with the account key held in the local
OpenCode configuration. Where the live service and the page disagree, the live
service wins and the difference is written down here.

### Endpoints

| Purpose | Method | Path | Cost |
|---|---|---|---|
| Decisions | POST | `/v1/systemone` | input tokens only |
| Model list | GET | `/v1/models` | free |

Base URL `https://api.subq.ai/v1`. Authentication is a bearer token in the
`Authorization` header. The key is read from the environment and never written
to a file, a log, or an exception message.

### Request body

```json
{
  "model": "subq-snap-preview",
  "state": "the text, object or array to evaluate",
  "questions": {"<name>": {"type": "noul", "instructions": "...", "criteria": {}}}
}
```

`questions` holds 1 to 16 entries and a name is at most 100 characters. The
criteria shape depends on the question type, and the live service refuses the
wrong shape with a 422:

| Type | `criteria` shape | Verified |
|---|---|---|
| `noul` | object with a `true` and a `false` key | 200 |
| `choice` | object mapping an option name to a description, 2 to 255 options | 200 |
| `score` | array of 2 to 10 level descriptions, ordered | 200 |

An object criteria on a `score` question is refused with
`questions/<name>/criteria: must be array`. An array criteria on a `noul` or
`choice` question is refused with `must be object`. The documentation shows the
score legend as an object, which is the response shape, not the request shape.

### Response body

```json
{
  "model": "subq-snap-preview",
  "answers": {
    "<name>": {"type": "noul", "noul": 0.985}
  },
  "usage": {"input_tokens": 107, "output_tokens": 0}
}
```

A `noul` answer carries only `type` and `noul`. A `choice` answer carries
`type`, `choice`, `probabilities` and `confidence`. A `score` answer carries
`type`, `score`, `legend`, `probabilities` and `confidence`. The score `legend`
is keyed by the level index as a string. `output_tokens` is always 0 and the
price is $0.05 per million input tokens.

### Errors

The Decisions endpoint answers with a `detail` object:

```json
{"detail": {"error_type": "validation_error", "message": "questions: must have at least 1 question"}}
```

| Status | Meaning | Retry |
|---|---|---|
| 400 | request over the token limit, or the model refused the request | no |
| 401 | missing or invalid credential | no |
| 402 | no credits left | no |
| 403 | the key may not use the requested model | no |
| 422 | invalid request, or the model is not a decision model | no |
| 429 | rate limit | yes |
| 5xx | server error, 503 when temporarily unavailable | yes |

A 422 names the offending path, for example
`questions/severity/criteria: must be array`. The OpenAI-compatible endpoints
use a different envelope, an `error` object instead of `detail`. The adapter
reads the Decisions envelope and treats any other shape as a malformed
response.

## 3. The API limitation that shapes the design

The Decisions API decides over one state. It has no batch endpoint. A live
probe sent three items as an array state with one question per item and the
service answered each question about the whole array: the item about a
warehouse crew was labelled with the finance option that the first item
earned, and an item about coffee was labelled the same way. The array state is
one decision about a list, not one decision per list entry.

So a batch is one request per item. The cost of a batch of N items is N
requests and N times the state tokens. The adapter must not pretend otherwise,
and the capability report must say so in words.

## 4. The design

Three pieces, each with one owner.

### 4.1 `src/product_hunter/llm/decision.py`, the provider-agnostic layer

It holds the shapes a decision has, the health a provider reports, the
provenance of an answer, and the router that picks a provider.

- `QuestionType`: `noul`, `choice`, `score`.
- `DecisionQuestion`: the name, the type, the instructions and the criteria,
  with a validator that refuses the criteria shape the type does not take.
- `DecisionAnswer`: the typed answer, with a validator that refuses an answer
  whose fields do not match its type.
- `DecisionOutcome`: one item's answer, or the reason there is none, with the
  provenance of the call that produced it.
- `DecisionProvenance`: the provider, the model, the endpoint, the request
  hash, the attempt count, the latency, the token counts, the estimated cost,
  whether the answer came from the cache, and the moment of the call.
- `ProviderHealth`: `healthy`, `degraded`, `unavailable`, `disabled`.
- `DecisionProvider`: the protocol every provider implements, with the three
  operations: `classify`, `filter_items`, `rank`.
- `DecisionRouter`: the chain. It takes the first provider that is enabled and
  healthy, and falls back to the next one when a provider cannot answer. A
  provider that fails mid-batch hands over the outcomes it already paid for, so
  the next provider is asked only about the items that are still missing. A
  provider that returns the wrong number of outcomes is refused by name. The
  router also reports the capabilities of the first provider it would use.
- `TextDecisionProvider`: the fallback over the existing `TextGenerator`. It
  asks the text model for a small JSON object and parses it. It reuses the
  model routing that already exists, so the fallback costs no new plumbing. The
  item is data, and the untrusted-region markers are removed from it before the
  prompt is built, so an item cannot close the region early.

The error types reuse `ProviderError` from `llm/base.py`, so a caller that
catches a provider error catches a decision error too. Two refusals are told
apart. `DecisionBudgetExceeded` is the run saying it has no money left: the
router re-raises it, because the next provider would spend what the run did not
allow. `DecisionLimitReached` is one provider's own limit: the router falls
through it like any other provider failure.

`DecisionUnavailable` carries the outcomes the provider answered before it
failed, and the router keeps them rather than buying those items twice.

### 4.2 `src/product_hunter/llm/subq.py`, the adapter

- `SubQConfig`: the base URL, the model, the key, the timeout, the retry
  bounds, the budget limits, the batch limit, the price per million input
  tokens, the failure threshold and the cooldown.
- `SubQClient`: one HTTP client that owns the retry, the timeout, the rate
  limit handling, the request cache and the request hash.
- `SubQProvider`: the provider. It owns the capability report, the health
  state, the spend ledger and the three operations.

### 4.3 `config/decision_routing.yaml`, the chain

```yaml
version: 1
providers:
  - name: subq
    enabled: true
  - name: text
    enabled: true
    model_role: classification
```

The order of the list is the order of preference. An operator who does not want
SubQ removes it from the list or sets `enabled: false`, and the text provider
takes the work. The `model_role` names the `ModelRole` the text fallback
resolves; it is the existing routing, not a new one.

The loader lives in `config.py` next to the other config loaders, and the
router builder lives in `llm/__init__.py` next to the other builders. The
builder takes either one generator for every text entry or a
`generator_for_role` resolver, and asks it for the `model_role` the file names.
Both modules import the SubQ module only when they are asked for it, so
importing the config or the package never loads it.

## 5. The operations

### Classification

One `choice` question with the options as criteria. The answer is the option
name with its probability and the confidence of the decision. A label from the
options that the service did not choose is still a valid answer with a low
probability, and the outcome records the probability rather than hiding it. A
label outside the options is not an answer to the question: it is recorded as
that item's failure, and the failure names the label.

### Filtering

One `noul` question whose criteria are the `true` and `false` descriptions of
the gate. The outcome carries the probability of yes and a boolean that
compares it with the threshold the caller gave. The default threshold is 0.5.

### Ranking

One `score` question whose criteria are the ordered level descriptions. The
outcome carries the probability-weighted score, which may fall between two
levels, and the confidence. The ranking is the items ordered by that score.
SubQ does not offer a listwise ranker, so the ranking is one score per item
and a sort. The capability report says so.

The score scale is not the same in the two providers, and the value is not
rescaled. The SubQ score is in level-index space: 0 for the first level, 1 for
the second, and so on. The text provider's score is 0 to 1. A score is therefore
compared only with scores from the provider that produced it.

### The batch, in both providers

A batch is a list of items and the provider answers every one of them. A
batch of no items is not a batch: the router answers it with an empty list
and asks no provider, because a provider that returned nothing would be
read as a failure.

Both providers cap the batch, because a batch is a number of calls and a
number of outcomes. The SubQ provider caps it at `max_batch_items` and the
text fallback at twenty items. A batch over the cap is refused before the
first call, and the refusal names the limit.

A provider that fails part of the way through a batch keeps the outcomes it
already paid for and raises with them attached. The router then asks the next
provider only about the missing items, and returns one outcome per item in item
order, so a partial answer is never bought twice. A provider that returns fewer
outcomes than it was asked is not accepted: the router records the mismatch by
name, with both counts, and asks the next provider.

## 6. Failure semantics

The rule is one line: a failure that concerns the whole provider raises, a
failure that concerns one item is recorded against that item.

| Failure | Where it lands |
|---|---|
| No credential | the provider reports `disabled` and is skipped |
| 401, 402, 403 | raised as a provider failure; the provider stays `unavailable` until the credential changes |
| 429 after the retries | raised as a provider failure, health goes `degraded` |
| 5xx, a timeout, or any other httpx request failure after the retries | raised as a provider failure |
| 422 | recorded against the item, the batch continues |
| A body that is not the documented shape | recorded against the item |
| A label outside the options | recorded against the item, and the failure names the label |
| The run budget would be exceeded | raised as `DecisionBudgetExceeded`, the call does not start, the chain does not fall through |
| The provider's own call, token or dollar limit would be exceeded | raised as `DecisionLimitReached`, the call does not start, the chain falls through |

The router falls back to the next provider in the chain when a provider raises,
and also when every item of a batch failed. A batch where some items failed
keeps those failures and is not re-run, so a partial answer is never paid for
twice. The router keeps the message of every provider it skipped or that
failed, so a reader can tell which provider failed after a fallback. The list
is bounded to the last ten messages.

A failed call opens a circuit. After the configured number of consecutive
provider failures the provider reports `unavailable` and is skipped until the
cooldown has passed. One success closes the circuit. A refused credential is
fatal rather than slow: the provider holds the refused key and reports
`unavailable` while that key is unchanged, because a probe with the same broken
credential could never succeed. A new credential clears the flag and the
provider is tried again.

## 7. Budget accounting

The provider counts the requests, the input tokens, the output tokens, the
estimated dollars and the cache hits. The estimate is
`input_tokens / 1_000_000 * usd_per_mtok_input`, because the output is free.

The accounting is per request, not per answer. A request is counted before it
starts, so a request that failed still binds the call limit, and a retry is a
second request and a second call. The checks run in one order: the call count,
then the token estimate, then the dollar estimate, then the tracker's guards,
and only then is the request counted. The count therefore moves after every
refusal, so a call that never went out does not spend the budget and cannot
lock the provider out of the calls it may still make. The token estimate uses
three characters per token, deliberately low so the estimate does not
understate the spend; the real usage the service reports replaces it when the
answer arrives. A call that would break a limit does not start.

A cached answer is returned before any of that: it makes no request and costs
nothing, so a repeated question is free even at the call limit.

When a budget tracker is supplied, the provider also records into it, under
the `subq_calls`, `llm_input_tokens` and `usd_cost` dimensions the research
contract already declares. The tracker is used through a small protocol, so the
provider does not import the research package and the two do not form a cycle.
A tracker that cannot answer refuses the call.

The tracker has two moments, and they are not the same. The guard is asked
before the call and a refusal there stops the call: the spend has not
happened yet. The record is asked after the call, and the tracker records
the spend first and only then reports that the spend left the budget
behind. That report does not undo the call, so the provider keeps the
answers and writes the overrun into its ledger instead of raising. A
completed call that turned into a failure would make the chain run the
batch again and pay for it twice. The ledger keeps the last five overruns,
so a long run does not grow without limit.

The provider applies the same rule to its own limits. The real usage can exceed
the conservative estimate, and when it takes `input_tokens` past
`max_input_tokens` or the estimated dollars past `max_usd`, the break is
recorded in the ledger rather than raised: the call already happened and its
answers are real.

## 8. Provenance and idempotency

Every outcome carries the provenance of the call that produced it. The request
hash is the SHA-256 of the canonical JSON of the model, the state and the
questions, so the same question about the same text always has the same hash.

The client keeps the answer under that hash, and a repeated request is
answered from the cache without a call and without a cost. The API has no
idempotency key, so this is a client-side guarantee and the report must say
so.

The cache is bounded, because a long-running process would otherwise grow
without limit. It holds at most `max_cache_entries` answers, the least
recently used one is dropped when it grows past that, and a cap of zero
means no cache at all: every request goes out.

The cache is consulted before the budget: a hit is answered without asking the
budget for permission. No request is made, so the hit carries an empty usage,
no attempt and no latency, and its provenance says the answer came from the
cache. The call the hit replaced is never counted.

## 9. Credentials

| Name | Meaning |
|---|---|
| `SUBQ_API_KEY` | the bearer token, read from the environment only |
| `SUBQ_BASE_URL` | the base URL, `https://api.subq.ai/v1` by default |
| `SUBQ_MODEL` | the model id, `subq-snap-preview` by default |

The three names are the ones SubQ documents, so the same key works for the
loop tooling and for this repository. The other settings are
`PH_SUBQ_TIMEOUT_SECONDS`, `PH_SUBQ_MAX_RETRIES`, `PH_SUBQ_BACKOFF_SECONDS`,
`PH_SUBQ_MAX_CALLS`, `PH_SUBQ_MAX_INPUT_TOKENS`, `PH_SUBQ_MAX_USD`,
`PH_SUBQ_MAX_BATCH_ITEMS`, `PH_SUBQ_USD_PER_MTOK_INPUT`,
`PH_SUBQ_FAILURE_THRESHOLD`, `PH_SUBQ_COOLDOWN_SECONDS` and
`PH_SUBQ_MAX_CACHE_ENTRIES`.

`.env.example` names the values and holds no value. The key never enters the
repository, a log line, an exception message, a provenance record or a test
fixture.

The key is held as a secret, so it is masked in the repr and in every
serialisation of the config. A config that is dumped to a record holds a
mask, and a key that is read back goes through one accessor.

The base URL is operator-configurable, so it is checked before the first
call. A plain `http` URL would put the bearer token on the wire in the
clear, so `https` is required and `http` is accepted only for a loopback
address, which never leaves the machine. A URL with no host cannot be called
at all, and httpx reports it as a plain exception rather than an httpx error,
so it is refused where the message can name the URL. A URL that carries a
username or a password is refused too, because the endpoint goes into every
provenance record. The client follows no redirect, so a 30x cannot carry the
token to a second host.

## 10. Tests

The unit tests cover the missing credential, the malformed body, the timeout,
the rate limit, the provider failure, the fallback, the batch, the provenance,
the budget and the idempotency. They use a fake transport, so no test reaches
the network.

They also cover the rules that are easy to get wrong: a request failure that
is not a transport error, a failed call that still binds the call limit, a
cached answer that is free at the call limit, a real spend that exceeds the
estimate, a refused credential that keeps the provider unavailable until the
credential changes, the untrusted markers removed from an item, a run budget
refusal that does not fall through, a label outside the options, the outcomes
that survive a fallback, a provider that returns too few outcomes, a repeated
question name, the generator resolved for a model role, and the rule that
importing the package does not load the SubQ module. That last one runs in a
subprocess, so the import order of the test session does not decide it.

One live test is marked `network` and skips itself when the key is absent. It
lists the models and asks one question. The wave runs it once with the real
key and records the result in the report.

## 11. Acceptance

- `uv run pytest` is green.
- The provider works with no credential, and the router falls through to the
  next provider in the chain.
- A SubQ failure never stops the chain.
- Every outcome carries a provenance record.
- The budget refuses a call that would break a limit, and the refusal names the
  limit.
- The call limit binds against the requests made, failed ones included, and a
  cached answer is free even at that limit.
- A run budget refusal does not fall through to the next provider.
- The outcomes a provider paid for before it failed are not paid for again.
- Real usage past a limit is recorded as an overrun, not raised.
- A label outside the options is recorded against the item, and the failure
  names the label.
- A refused credential keeps the provider unavailable until the credential
  changes.
- Importing the config or the package does not load the SubQ module.
- The capability report names the operations the API cannot support.
- The live smoke test is recorded with its real result, or it is recorded as
  blocked.

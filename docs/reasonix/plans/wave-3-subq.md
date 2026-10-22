# Plan: the SubQ Snap decision provider

Status: written at stage 3 of the wave 3 session 5 run.
Spec: [wave-3-subq.md](../specs/wave-3-subq.md).
SubQ Snap was asked to choose between the thin adapter and the provider layer.
It chose the provider layer (p = 0.948, confidence 0.896) and gave the coupling
risk a probability of 0.023. One call, 466 input tokens, model
`subq-snap-preview`.

## 1. The files

| File | What it holds |
|---|---|
| `src/product_hunter/llm/decision.py` | the answer shapes, the health states, the provenance, the provider protocol, the text fallback, the router |
| `src/product_hunter/llm/subq.py` | the SubQ config, the HTTP client, the capability report, the spend ledger, the health state, the provider |
| `src/product_hunter/llm/__init__.py` | the router builder, next to the other builders |
| `src/product_hunter/config.py` | the decision routing config, the SubQ config loader, the env overrides |
| `config/decision_routing.yaml` | the provider chain |
| `.env.example` | the credential names and the settings, no values |
| `docs/TOOLING.md` | the env table, updated |
| `tests/test_llm_decision.py` | the shapes, the router, the text fallback |
| `tests/test_llm_subq.py` | the adapter, and the live test |

The import graph stays a tree. `llm/decision.py` and `llm/subq.py` import
`llm/base.py` and `llm/catalog.py` only. `config.py` imports `llm/subq.py` for
the config model, the way it already imports `llm/routing.py` for the roles.
Nothing in `llm/` imports `config.py` at run time, so the cycle that would come
from `config.py` importing `research.budget.py` never forms. The budget
tracker is used through a protocol instead.

## 2. The ladder

The work reuses before it writes.

| Need | What is used | What is not written |
|---|---|---|
| A provider call | `httpx.Client`, already a dependency | a new HTTP stack |
| A provider error | `ProviderError` and `ProviderUnavailable` from `llm/base.py` | a second error tree |
| A timestamp | `utcnow` from `llm/catalog.py` | a clock helper |
| The fallback | `TextGenerator` from `llm/base.py` | a second text client |
| The fallback model | `ModelRole` and the existing router | a new model catalogue |
| The budget | the `subq_calls`, `llm_input_tokens` and `usd_cost` dimensions of the research contract | a new ledger format |
| A JSON answer from a text model | the fence-stripping and the strict parse of `extraction.py` | a loose parser |

## 3. The order of the work

1. `llm/decision.py`: the shapes first, then the protocol, the text fallback
   and the router. The router is written against the protocol, so the SubQ
   provider can be written afterwards against the same protocol.
2. `llm/subq.py`: the config, the client, then the provider.
3. `config.py` and `config/decision_routing.yaml`: the chain.
4. `llm/__init__.py`: the builder.
5. The tests, one file per module.
6. `.env.example` and `docs/TOOLING.md`.

## 4. The decisions worth writing down

**The request is one item.** The live API decides over a state, and a probe
showed that an array state is one decision about a list. A batch is therefore a
loop of single-item requests. The capability report says this in words.

**The criteria shape is checked before the request.** The service refuses the
wrong shape with a 422 that names the path. The adapter checks it first, so a
bad question never costs a call.

**A 422 is an item failure, not a provider failure.** Every item of a batch
sends the same question, so a 422 on one item is about that item's state. The
batch continues and the outcome carries the message.

**The 401, 402 and 403 are provider failures.** They say the credential or the
model is wrong, and every later call would fail the same way.

**The retry follows the fetch layer.** A transport error, a timeout, a 429 and
a 5xx are retried with a backoff that honours `Retry-After` and is capped. The
other statuses are not retried, because a retry of a 422 is a wasted call.

**The cache is the idempotency.** The API has no idempotency key, so the
client keeps the answer under the request hash and answers a repeated request
from it. The provenance says whether the answer came from the cache.

**The budget refuses before it spends.** The call count, the token estimate
and the dollar estimate are checked before the request. The real usage is
recorded when the answer arrives.

**The router falls back on a raise and on an empty batch.** A provider that
raised cannot answer. A provider where every item failed cannot answer either.
A batch where some items failed keeps the failures, so a partial answer is
not paid for twice.

**The text fallback asks for a small JSON object.** The model answer is parsed
strictly, and a body that is not the shape is an item failure. The fallback is
the same operation with a different transport, so the router needs no special
case for it.

## 5. The test matrix

| Case | What it proves |
|---|---|
| no credential | the provider is `disabled` and the router uses the next one |
| a body that is not the documented shape | the outcome records the failure and the batch continues |
| a timeout | the call is retried and then raises |
| a 429 | the call is retried, `Retry-After` is honoured, then it raises |
| a 401 | the call raises at once, health goes `unavailable` |
| a provider failure | the router falls back to the text provider |
| a batch | every item gets an outcome, and a bad item does not stop the others |
| the provenance | the record names the provider, the model and the request hash |
| the budget | the call that would break a limit does not start |
| the idempotency | the same question twice costs one call |
| the capabilities | the report names the operations the API cannot support |
| the live test | one real call, marked `network`, skipped without a key |

## 6. What this plan does not do

- It does not wire the provider into the research pipeline. The adapter is a
  library with no caller, and the pipeline is not modified.
- It does not add a table, a migration or a CLI command.
- It does not benchmark SubQ against the cheap model. Spec 38A asks for that
  before SubQ is promoted into the default path, and the benchmark is its own
  piece of work.
- It does not run the batch in parallel. A parallel fan-out needs its own
  budget discipline, and the rate limit is a real constraint. Sequential is the
  honest first version.

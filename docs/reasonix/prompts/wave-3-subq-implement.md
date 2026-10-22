# Implement prompt: the SubQ Snap decision provider

You are implementing one node of a loop graph. The work is fully specified. Read
these three files before you write a line:

- `docs/reasonix/specs/wave-3-subq.md` — the spec, with the verified API contract
- `docs/reasonix/plans/wave-3-subq.md` — the plan, the file list, the decisions
- `.loop/adapter.json` — the rules that bind this repository

The repository root is the current working directory. Work only on the branch
`wave3/subq-integration`. Do not touch `main`. Do not merge another branch.

## What to build

Two new modules, two touched modules, one new config file, one env file, one
doc, two test files.

```
src/product_hunter/llm/decision.py      new
src/product_hunter/llm/subq.py           new
src/product_hunter/llm/__init__.py       touched: the router builder
src/product_hunter/config.py             touched: the decision routing config and the SubQ config loader
config/decision_routing.yaml             new
.env.example                             touched: the credential names
docs/TOOLING.md                          touched: the env table
tests/test_llm_decision.py               new
tests/test_llm_subq.py                   new
```

Nothing else. The research pipeline is not touched. `config/model_routing.yaml`
is not touched.

## The API, verified live

Base URL `https://api.subq.ai/v1`. Bearer token in `Authorization`.

`POST /v1/systemone`

```json
{
  "model": "subq-snap-preview",
  "state": "text to evaluate",
  "questions": {
    "<name>": {"type": "noul", "instructions": "...", "criteria": {...}}
  }
}
```

The criteria shape depends on the type, and the service refuses the wrong one
with a 422:

| Type | criteria | Answer fields |
|---|---|---|
| `noul` | object with a `true` and a `false` key | `type`, `noul` (0..1). No `confidence`. |
| `choice` | object mapping an option name to a description, 2 to 255 options | `type`, `choice`, `probabilities`, `confidence` |
| `score` | array of 2 to 10 level descriptions, ordered | `type`, `score`, `legend`, `probabilities`, `confidence` |

The response:

```json
{"model": "subq-snap-preview",
 "answers": {"<name>": {"type": "noul", "noul": 0.985}},
 "usage": {"input_tokens": 107, "output_tokens": 0}}
```

The score `legend` is keyed by the level index as a string. `output_tokens` is
always 0 and the price is $0.05 per million input tokens.

The error envelope of the Decisions endpoint is
`{"detail": {"error_type": "...", "message": "..."}}`.

`GET /v1/models` answers `{"object": "list", "data": [{"id": "..."}]}` and is
free. Use it for the capability check, so a check costs no tokens.

Questions: 1 to 16 per request, names at most 100 characters.

## The rules that bind the work

- SubQ is never a hard dependency. The pipeline runs without it.
- SubQ is selected through the routing chain, never hard-coded.
- The existing model routing is not redesigned. The `ModelRole` members, the
  `ModelRouter` and `config/model_routing.yaml` stay as they are.
- The import graph stays a tree. Nothing under `llm/` imports
  `product_hunter.config` or `product_hunter.research` at run time. The budget
  tracker is used through a protocol.
- The key is read from the environment, never written to a file, a log, an
  exception message, a provenance record or a fixture.
- A test never reaches the network, except the one live test, which skips
  itself when `SUBQ_API_KEY` is absent and is marked `network`.
- Never change a test to make it pass.

## The house style

Read `src/product_hunter/llm/base.py`, `src/product_hunter/llm/routing.py` and
`src/product_hunter/fetch.py` before you write. Match them: a module docstring
that says what the module owns and what it does not, `from __future__ import
annotations`, pydantic models with `ConfigDict(extra="forbid")`, errors that
carry a reason, a type hint on every function, and a comment that explains a
why and not a what.

## The interfaces

`llm/decision.py` holds:

- `QuestionType` (an enum of `noul`, `choice`, `score`)
- `DecisionQuestion` (name, type, instructions, criteria) with a validator that
  refuses the criteria shape the type does not take
- `DecisionAnswer` (type, noul, choice, probabilities, confidence, score,
  legend) with a validator that refuses an answer whose fields do not match its
  type
- `DecisionProvenance` (provider, model, endpoint, request_hash, capability,
  attempt_count, latency_ms, input_tokens, output_tokens, usd_estimate, cached,
  decided_at)
- `DecisionOutcome` (index, label, probability, score, confidence, error,
  provenance)
- `ProviderHealth` (an enum of `healthy`, `degraded`, `unavailable`, `disabled`)
- `CapabilityState` (name, available, reason)
- `DecisionProvider` (a runtime-checkable protocol: `name`, `health()`,
  `capabilities()`, `classify()`, `filter_items()`, `rank()`)
- `DecisionError(ProviderError)`, `DecisionUnavailable(DecisionUnavailable)`,
  `DecisionBudgetExceeded(DecisionUnavailable)`,
  `DecisionResponseInvalid(ProviderError)`
- `SpendTracker` (a protocol with `guard` and `record`)
- `TextDecisionProvider`: the fallback over the existing `TextGenerator`. It
  asks the model for one small JSON object per item and parses it strictly.
- `DecisionRouter`: the chain. It skips a provider that is `disabled` or
  `unavailable`, falls back when a provider raises, and falls back when every
  item of a batch failed. It never re-runs a batch where some items failed.

`llm/subq.py` holds:

- `SubQConfig`: base_url, model, api_key, timeout_seconds, max_retries,
  backoff_base_seconds, backoff_max_seconds, max_calls, max_input_tokens,
  max_usd, max_batch_items, usd_per_mtok_input, failure_threshold,
  cooldown_seconds.
- `SubQClient`: one HTTP client. It owns the timeout, the retry, the rate
  limit handling, the request hash and the answer cache. Its `decide()` takes
  one state and one list of questions and returns the parsed answers with the
  usage.
- `SubQProvider`: the provider. It owns the capability report, the health
  state, the spend ledger and the three operations.
- `SubQSpend`: calls, input_tokens, output_tokens, usd_estimate, cache_hits.

## The behaviour the tests will check

1. With no key the provider reports `disabled` and the router uses the next
   provider in the chain.
2. A body that is not the documented shape is recorded as an item failure and
   the batch continues.
3. A timeout is retried and then raises.
4. A 429 is retried, `Retry-After` is honoured, and then it raises.
5. A 401 raises at once and the health goes `unavailable`.
6. A provider failure makes the router fall back to the text provider.
7. Every item of a batch gets an outcome, and a bad item does not stop the
   others.
8. Every outcome carries a provenance record with the provider, the model and
   the request hash.
9. A call that would break a budget limit does not start, and the refusal names
   the limit.
10. The same question about the same text twice costs one call.
11. The capability report names the operations the API cannot support, with a
    reason for each. The API cannot return text, and it cannot decide over a
    list of items in one call.

## Before you finish

Run `uv run pytest` and make it green. Then run `uv run ruff check src tests`
if ruff is available, and fix what it reports. Report the test count, the
files you wrote, and anything in the spec you could not do.

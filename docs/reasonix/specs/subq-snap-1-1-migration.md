# Spec: Migrate the SubQ decision provider to `subq-snap-1.1`

Status: draft for implementation.
Owner: backend (llm/subq).
Risk: low. No auth, schema, payment, secret, or infra change. The change is a model id, a price constant, a question cap, and config/test defaults.

## 1. What this is

The SubQ decision provider still defaults to `subq-snap-preview`. That id no longer appears in the service's model list, which now offers `subq-snap-1.1` only. With the old default, `SubQProvider.check()` fails, the provider reports unavailable, and the chain falls through to the text provider for every decision. This spec migrates the default to the model the service actually offers, updates the two constants that moved with it, and raises the question cap to the new limit.

No protocol change is involved: endpoint, request body, response envelope, answer shapes, and error envelope are identical in the new docs.

## 2. Non-goals

- No image input support. The API gained it, but this migration does not add it.
- No context-window pre-flight guard. A too-large request still surfaces as a 400 item failure.
- No change to the routing chain, the budget accounting, or the spend ledger shape.
- No change to historical docs, reports, or handoffs that cite the old id. They are records of past runs.
- No new dependency.

## 3. The API contract, verified against the latest docs

Source: https://docs.subq.ai/endpoints/decisions-api/ and https://docs.subq.ai/using-the-api/models/, read 2026-10-25.

| Item | Old | New |
|---|---|---|
| Model id | `subq-snap-preview` | `subq-snap-1.1` |
| Status | Public preview | Public preview |
| Endpoint | POST /v1/systemone | POST /v1/systemone |
| Price | $0.05 / 1M input tokens | $0.04 / 1M input tokens |
| Output price | free | free |
| Questions per request | 1 to 16 | 1 to 128 |
| Question name length | up to 100 chars | up to 100 chars |
| Choice options | 2 to 255 | 2 to 255 |
| Score levels | 2 to 10 | 2 to 10 |
| Context window | not documented | 384K tokens (393,216) |
| Images | not supported | up to 4, 4 MiB each, 8 MiB total |

## 4. What changes

### 4.1 `src/product_hunter/llm/subq.py`

- `DEFAULT_MODEL` becomes `"subq-snap-1.1"`.
- `DEFAULT_USD_PER_MTOK_INPUT` becomes `0.04`.

No other constant or shape in this module changes. The client, the retry, the cache, the spend ledger, and the circuit breaker are unaffected.

### 4.2 `src/product_hunter/llm/decision.py`

- `MAX_QUESTIONS` becomes `128`. The comment above it is updated to match.

### 4.3 `.env.example`

- `SUBQ_MODEL=subq-snap-1.1`.
- `PH_SUBQ_USD_PER_MTOK_INPUT=0.04`.

These two lines must stay in lockstep with the defaults above, because `test_the_env_example_ships_the_defaults` compares them field by field.

### 4.4 `docs/TOOLING.md`

- The `SUBQ_MODEL` row of the credentials table names `subq-snap-1.1` as the default.

## 5. Tests

`tests/test_llm_subq.py` must move with the code, not be weakened:

- Mock response envelopes use `"model": "subq-snap-1.1"`.
- `test_the_config_loader_reads_the_environment` sets and asserts `subq-snap-1.1`.
- Provenance record assertion uses `subq-snap-1.1`.
- `test_the_capability_check_costs_no_tokens` feeds and asserts `subq-snap-1.1`.
- The three spend assertions (`usd_estimate` and the tracker tuple) use `0.04` instead of `0.05`.
- The env-example test needs no edit beyond the file change, since it compares values.

No new test is required for the migration itself. A test for the 128-question boundary is optional and left to a follow-up.

## 6. Acceptance

- `uv run pytest` is green, including `tests/test_llm_subq.py` and `tests/test_llm_decision.py`.
- `SubQConfig().model == "subq-snap-1.1"` and `SubQConfig().usd_per_mtok_input == 0.04`.
- `.env.example` agrees with `SubQConfig()` field by field.
- `MAX_QUESTIONS == 128`.
- The live smoke test, run once with a real key, lists `subq-snap-1.1` and answers one question. Recorded in the report with call, token, and cost counts, or recorded as blocked if the key is absent.

## 7. Rollback

Revert the four files. There is no data migration and no schema change, so rollback is a single commit revert with no side effects.

# Plan: Migrate the SubQ decision provider to `subq-snap-1.1`

Spec: `docs/specs/subq-snap-1-1-migration.md`.
Risk: low. No auth, schema, payment, secret, or infra path.

## 1. Scope

Files touched, all inside the declared radius:

- `src/product_hunter/llm/subq.py`
- `src/product_hunter/llm/decision.py`
- `.env.example`
- `docs/TOOLING.md`
- `tests/test_llm_subq.py`

No other source file references the model id or the price constant. Historical docs and reports are not edited.

## 2. Ordered steps

1. Edit `src/product_hunter/llm/subq.py`: set `DEFAULT_MODEL = "subq-snap-1.1"` and `DEFAULT_USD_PER_MTOK_INPUT = 0.04`.
2. Edit `src/product_hunter/llm/decision.py`: set `MAX_QUESTIONS = 128` and update the comment.
3. Edit `.env.example`: `SUBQ_MODEL=subq-snap-1.1` and `PH_SUBQ_USD_PER_MTOK_INPUT=0.04`.
4. Edit `docs/TOOLING.md`: default model line to `subq-snap-1.1`.
5. Edit `tests/test_llm_subq.py`: mock envelopes, config-loader test, provenance assertion, capability-check fixture, and the three spend assertions to the new id and price.
6. Run `uv run pytest tests/test_llm_subq.py tests/test_llm_decision.py`. Expect green.
7. Run the full suite `uv run pytest` to confirm no other test pins the old values.
8. Run the live smoke test once with the real key and record the result, or record it as blocked.

## 3. Verification

- `uv run pytest` green.
- A quick check: `python -c "from product_hunter.llm.subq import SubQConfig; c=SubQConfig(); print(c.model, c.usd_per_mtok_input)"` prints `subq-snap-1.1 0.04`.
- `MAX_QUESTIONS` import check prints 128.
- Live smoke test result recorded with call, token, and cost counts, or recorded as blocked.

## 4. Rollback

Single commit revert of the five files. No data or schema side effects.

## 5. Out of scope, recorded for later

- Image input support on the decisions endpoint.
- A pre-flight guard against the 384K context window.
- A test for the 128-question boundary.
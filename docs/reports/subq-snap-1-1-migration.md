# SubQ Snap migration to `subq-snap-1.1`

RUN_ID: `ses_e8e0b71f5ffefYohw2fcrls90Y-20261025`
Date: 2026-10-25
Risk: declared medium (repo adapter floor); the change itself is constants and
docs. Spec: `docs/reasonix/specs/subq-snap-1-1-migration.md`. Plan:
`docs/reasonix/plans/subq-snap-1-1-migration.md`.

Status: implemented, verified, and committed. The full adapter gate was red on a
test outside this radius when the five edits were made; it went green once that
unrelated defect was repaired in its own commit (see "The blocker, resolved").

## What changed

| File | Change |
|---|---|
| `src/product_hunter/llm/subq.py` | `DEFAULT_MODEL` → `"subq-snap-1.1"`; `DEFAULT_USD_PER_MTOK_INPUT` → `0.04` |
| `src/product_hunter/llm/decision.py` | `MAX_QUESTIONS` → `128`, comment above it updated |
| `.env.example` | `SUBQ_MODEL=subq-snap-1.1`; `PH_SUBQ_USD_PER_MTOK_INPUT=0.04` |
| `docs/TOOLING.md` | `SUBQ_MODEL` credentials row: default is `subq-snap-1.1` |
| `tests/test_llm_subq.py` | 11 pins moved: mock envelope, config loader set/assert, provenance model and estimate, tracker and ledger estimates, capability-check fixture and assert, live-test model assert |

`git diff --stat`: 5 files, 17 insertions, 17 deletions. No logic, no new
dependency, no new test. Historical handoffs, reports and specs that cite the
old id are records of past runs and were left alone.

## Evidence

Targeted gate:

```
uv run pytest tests/test_llm_subq.py tests/test_llm_decision.py -q
107 passed, 1 skipped in 0.89s
```

The skip is `test_live_subq_lists_the_models_and_answers_one_question`, marked
`network`; it is skipped when `SUBQ_API_KEY` is absent from the test process.
The live check was run separately (below).

Import checks:

```
SubQConfig() -> subq-snap-1.1 0.04
MAX_QUESTIONS -> 128
```

Old-id sweep, empty as required:

```
git grep -n "subq-snap-preview" -- src tests .env.example docs/TOOLING.md
(no output)
```

Live smoke, one call, key from the operator's local env, never printed:

```
models: ['subq-snap-1.1', 'subq-snap-preview']
default model offered: True
model subq-snap-1.1, passed true, probability 0.816, 115 input tokens,
0 output tokens, $0.0000046 estimated, 1 attempt, 333 ms, health healthy
```

Stage 0 also made one weighted Snap call before the edit: HTTP 200,
`"model": "subq-snap-1.1"`, 288 input tokens, 0 output, risk medium
(p = 0.896), lane backend (p = 0.993).

## The blocker, resolved

The first `uv run pytest` (the adapter gate) after the edit was red:

```
1 failed, 2764 passed, 1 skipped in 354.30s
tests/test_independence_identity.py::test_the_policy_minima_mirror_the_research_contract
AssertionError: assert 10 == 1
```

`config/independence.yaml` sets `min_independent_authors: 10`.
`config/research_contract.yaml` under `evidence_thresholds.validation` sets
`1`; the same file sets `10` in a second block. The test compares the two.

Attribution, so nobody blames this run: the test file, both config files and
`src/product_hunter/independence/` are byte-identical to HEAD
(`git diff --quiet` clean), so the failure reproduces without any of this
diff. It was pre-existing and outside the declared five-file radius. It was
not edited here. It blocked a commit, not the correctness of this change.

Repaired separately, in its own commit boundary, as required:
`fix(research-contract): restore the validation independent-author minimum to 10`.
The evidence for choosing the contract side over the policy side is in
`docs/reports/independence-contract-minima.md`. That commit holds neither these
five files nor this report.

Gate after the repair:

```
uv run pytest
2765 passed, 1 skipped, 2 warnings in 354.76s
```

## Observation on the premise

The spec says the old id "no longer appears in the service's model list". The
live list on 2026-10-25 returns `['subq-snap-1.1', 'subq-snap-preview']`, so
the old id is still advertised. The migration is still the right one: 1.1 is
the current model, carries the new price and the 128-question cap, and answered
a live question. Treat the "withdrawn" wording as stale, and expect the old id
to stop answering when the service retires it.

## Rollback

Revert the five files. No schema, no data, no migration.

## Not checked

- No 128-question boundary test (spec leaves it as an optional follow-up).
- Image input and the 384K context guard are out of scope by the spec.

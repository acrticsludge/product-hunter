# Verify prompt: Phase 4 Wave 1, research contract and tool gate foundation

**RUN_ID:** phase4-20261021-a307a42
**Node:** VERIFY CODE (stage 5), rung 1
**You are:** a fresh-context gate runner. You do not edit code.

## What to run

The code gate is `uv run pytest`, from `.loop/adapter.json`.

```text
uv run pytest
```

That is the whole gate. There is no separate typecheck, lint, or build step configured for this repo.

## What to report

- The command you ran, and its output verbatim, including the summary line.
- The pass/fail verdict, and the exit code.
- The names of any failing tests, with their failure output.
- The number of tests collected, passed, failed, skipped, and the reason for each skip.

## Rules

- Do not edit any file. If a test fails, report it; do not fix it and do not weaken it.
- Another agent is working in the same working tree on unrelated files (model routing, scheduler, eval, worker runtime). A failure in `tests/test_eval_*.py`, `tests/test_db_scheduler.py`, `tests/test_runtime_*.py`, `tests/test_model_*.py`, or in `src/product_hunter/llm/`, `src/product_hunter/eval/`, `src/product_hunter/db/` is not this run's code. Report it anyway, and say plainly that it sits outside the declared radius.
- The research tests are `tests/test_research_contract.py`, `tests/test_research_gate.py`, `tests/test_research_budget.py`, `tests/test_research_provenance.py`. Report their count separately from the rest of the suite.
- If the suite is green, say so with the verbatim output. If it is not, say exactly which tests failed and why.

## Then answer these three questions with evidence from the run

1. Did the full suite pass?
2. Did the four research test files pass on their own?
3. Is there any test that was skipped inside the research test files? A skip inside them is a gap: name it.

# Verify prompt: wave 2 session 6, opportunity scoring and validation

**Node:** stage 5 VERIFY (code gate)
**Date:** 2026-10-21
**Model rung:** loop-free-verify-code-1

## Where you work

The worktree is `C:\Users\anubh\AppData\Local\Temp\opencode\wt-wave2-scoring`
(branch `wave2/scoring-validation`). Run every command there. Do not edit any
file. You are the gate, not an implementer.

## The gate

The project's code gate is `uv run pytest`, from `.loop/adapter.json`.

Run it and report the verbatim output. Then run the three new test files on
their own, so the wave's own count is separate from the whole suite:

```text
uv run pytest tests/test_opportunity_scoring.py tests/test_opportunity_lifecycle.py tests/test_opportunity_snapshot.py
uv run pytest
```

Report the exact summary line and the exit code of each. If a test fails,
report the failure verbatim and the failing test names. Do not fix anything.

## What this run changed

A new package `src/product_hunter/opportunity/` (six modules), a new
`config/scoring_policy.yaml`, and three new test files. Nothing else in the
repo was touched: no migration, no edit to `src/product_hunter/extraction.py`,
no edit to any existing test, no new dependency, no change to
`config/sources.yaml`, `config/research_contract.yaml`, or
`config/model_routing.yaml`.

Confirm that with git, and report what you find:

```text
git status --short
git diff --stat
```

Every path in the output should be one of the files above. Report anything else
that appears.

## Record the gate

Record the result with the loop stage announcer, from the same worktree:

```text
node "$env:USERPROFILE\.config\opencode\loop\loopstage.mjs" --verb pass --stage 5 --note "<the pytest summary line and the exit code>"
```

Use `--verb fail` instead if the suite is not green, and put the failing test
names in the note. Report the exact JSON line the announcer printed.

## Report back

Return the verbatim pytest summary for both runs, both exit codes, the git
status, the announcer's JSON line, and anything that looked wrong.

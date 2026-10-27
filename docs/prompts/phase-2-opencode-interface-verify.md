# Verify prompt — Phase 2 OpenCode control interface

Dispatched to: `loop-free-verify-code-1`, twice.

## The gate

`.loop/adapter.json` declares the code gate as:

```text
uv run pytest
```

The verifier runs it from the repository root, through the loopgate wrapper,
with no narrowing, no `-k`, and no path filter. It reports the command, the
exit code, and the verbatim summary.

## The attribution rule

Another agent edits this tree at the same time, so a failure inside a file that
is not part of this change (the model routing tests, the scheduler tests, the
eval tests, `src/product_hunter/research/`, `src/product_hunter/llm/`,
`src/product_hunter/eval/`, `src/product_hunter/runtime/`) is not caused by this
run. The report says which failures are inside this run's files and which are
not.

## What the two runs found

Run 1: 717 passed, exit 0.

Run 2, after the harden fixes: 783 passed, exit 0.

Between the two, the suite went red for a while with 22 failures, all of them
in the other agent's model routing and scheduler tests while that agent was
editing the routing config and the scheduler. This run's files stayed green
throughout. The second gate run is the one that counts.

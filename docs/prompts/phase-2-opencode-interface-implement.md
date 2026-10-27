# Implement prompt â€” Phase 2 OpenCode control interface

Read first, in this order:

1. `docs/reasonix/handoffs/phase-2-opencode-interface.md` (handoff)
2. `docs/reasonix/specs/phase-2-opencode-interface.md` (spec)
3. `docs/reasonix/plans/phase-2-opencode-interface.md` (plan)

The plan is the contract. Follow it. Where the plan and the plan's own
wording disagree, the spec wins, and say so in your report.

## What to build

| File | Change |
| --- | --- |
| `src/product_hunter/registry.py` | new. The state layer |
| `src/product_hunter/cli.py` | add the `research` subcommand that delegates to `registry.main` |
| `tests/test_registry.py` | new. The test list in the plan |
| `.opencode/plugins/product-hunter.ts` | new. The v2 plugin registering the `product_hunter` tool |
| `.opencode/commands/*.md` | new. Ten command templates |
| `README.md` | document the command and the OpenCode surface |

## Rules

- Touch only the files above. Another agent is working in this tree. Do not
  edit, revert, or stage anything else.
- `src/product_hunter/cli.py` changed since the plan was written. Read it
  first. Your edit is additive: one subparser, one dispatch branch, one
  import inside that branch.
- Write the tests before the code that they cover, and run them as you go.
- Never weaken a test to make it pass. A failing test is the signal.
- No new dependency. The plugin uses the Node standard library only. The
  state layer uses what the repo already has.
- The plugin must not import from a package that resolves only inside the
  global OpenCode config directory.
- The subprocess call passes an argument vector. No shell string.
- The commit message for the change is `phase2: add OpenCode control
  interface`. Do not commit and do not push. The orchestrator commits.
- Read `AGENTS.md` and the ponytail skill before editing. The smallest
  complete change wins.

## Live check

After the plugin file exists, confirm the tool loads. The orchestrator holds
the tool catalog, so report the file path and the expected tool name and let
the orchestrator run the live check.

## Report back

- the files you changed, with line counts
- the test result, verbatim
- anything the plan left open, and what you did about it
- anything you could not verify

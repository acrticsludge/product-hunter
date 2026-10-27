# Review prompt — Phase 2 OpenCode control interface

Dispatched to: `loop-free-review-1` (a different model family from the
implementer, by design).

## The change under review

- `src/product_hunter/registry.py` (new) the state layer
- `src/product_hunter/cli.py` the additive `research` subcommand only
- `tests/test_registry.py` (new)
- `.opencode/plugins/product-hunter.ts` (new) the OpenCode v2 plugin
- `.opencode/commands/*.md` (new) ten command templates
- `README.md` the "OpenCode control surface" section and the `ph research`
  bullet only

Another agent is editing the same tree. Their uncommitted work (the `ph bench`
command, `src/product_hunter/eval/`, the model routing files, and the rename of
the documentation tree to `docs/reasonix`) is not part of this change and is
not to be
reviewed.

## The contract

- `docs/reasonix/specs/phase-2-opencode-interface.md`
- `docs/reasonix/plans/phase-2-opencode-interface.md`

The rule that must hold: OpenCode is the interaction surface, the worker owns
research execution, SQLite owns run state. The interface requests work and reads
durable state. It never runs a research stage, never treats chat history as
state, and never reports a run as active when the database does not say so.

## What the reviewer was asked to try to break

A lost update, a state nothing can move on from, a second run for an id that
already exists, a bare `continue` that picks a run it should not, an argument
that reaches a place it should not, a test that asserts the implementation
rather than the behaviour, a command template that would make the model guess a
run id or treat the chat as state, wording in the rendered text that could
mislead a reader about what is running, and dead or duplicated logic.

## Report format

Findings as Must / Should / Nice, each with the file, the line, the concrete way
it goes wrong, and the smallest fix. Then what was checked and found clean, a
verdict line, and a lean line.

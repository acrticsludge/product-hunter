# Harden prompt — Phase 2 OpenCode control interface

Dispatched to: `loop-free-harden`, three rounds.

## The change under review

- `src/product_hunter/registry.py` (new)
- `src/product_hunter/cli.py` the additive `research` subcommand only
- `tests/test_registry.py` (new)
- `.opencode/plugins/product-hunter.ts` (new)
- `.opencode/commands/*.md` (new)
- `README.md` the "OpenCode control surface" section only

## Ground to cover

1. Input trust: the objective and the research id come from a chat. Pattern,
   length, path separators, quotes, newlines, very long values, empty values.
2. Command execution: the plugin spawns a subprocess. An argument vector, never
   a shell string. No value can become a flag. A bounded timeout. Bounded
   output. A failing or hostile child cannot hang the tool or leak the
   environment.
3. SQL: every value a bound parameter. No user value reaches a table or a
   column name.
4. State: a lost update between two interface processes. A refused transition
   that changes nothing. A run that is never left claiming work in flight. The
   lease read from the row, never assumed.
5. Secrets and paths: no secret, environment value, or path outside the project
   in the text the model receives.
6. Observability: what a refused command leaves behind, and whether the durable
   trace answers who asked and when.
7. The command templates: the right action, the question tool for the two
   ambiguous cases, and no template that tells the model to run a stage or to
   treat chat history as state.

## The three rounds

Round 1 raised four findings: an unbounded stdout buffer in the plugin, an
absolute path in the missing-database message, no durable trace of a request,
and a read staleness nicety.

Round 2 confirmed the first three were fixed and raised two more: an argument
that begins with a dash read as an option, and a limit that was not clamped on
the server side.

Round 3 confirmed those two were fixed, checked the concurrent rewrite of the
plugin that another agent made, and returned `HARDEN: pass` with no new
finding.

The read staleness finding stays waived. The writes re-check their
preconditions inside a transaction, so a stale read cannot corrupt a
transition.

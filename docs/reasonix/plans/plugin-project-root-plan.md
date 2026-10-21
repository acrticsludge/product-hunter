# Plugin Project Root — Implementation Plan

**Date:** 2026-10-21
**Spec:** `docs/reasonix/specs/plugin-project-root-spec.md`
**Lane:** backend tooling. One source file changes.

## 1. Goal

Make the `product_hunter` tool reach the registry again. The tool call
`product_hunter(action: "list")` must return the registry text instead of the
`ModuleNotFoundError` that came from `uv` being pointed at a directory with no
`pyproject.toml`.

## 2. The change

One file: `.opencode/plugins/product-hunter.ts`.

The plugin needs the project root for two things: the `--project` argument and
the working directory of the child process. Today it takes both from
`toolCtx?.directory` with `process.cwd()` as the fallback, and neither is the
project root in this runtime.

The new rule mirrors `config.repo_root()` in `src/product_hunter/config.py`:
the project root is the nearest directory above the starting point that holds
a `pyproject.toml`. The plugin starts the walk at its own file, so the answer
does not depend on what the tool context carries.

### The resolver

A module-level helper in the plugin file:

- input: a starting directory
- output: the nearest ancestor directory (including the start) that holds a
  `pyproject.toml`, or nothing when there is none
- the walk ends at the filesystem root

Standard library only: `node:fs` for the file check, `node:path` for the
walk, `node:url` to turn `import.meta.url` into a directory. The plugin already
uses `node:child_process`; the Node standard library is the house rule here.

### The order of candidates

1. the plugin file's own directory
2. the tool context directory, if the plugin context carries a project
   location
3. `process.cwd()`

The first candidate that yields a root wins. If none does, the plugin spawns
in the working directory and the error text says which directory it used.

### The error text

The spawn failure message carries the directory that was used. A bridge error
that does not say where it looked is the defect this whole change is about, so
the fix carries that one fact.

## 3. What does not change

- `spawn` keeps an argument vector, `shell: false`, and the 30 second timeout.
- The action list, the input schema, the tool name, the tool description, and
  the `payload.text` return path are untouched.
- No file under `src/`, `db/`, or `config/` changes. The registry, the worker,
  and the research pipeline are not part of this change.
- No test changes. There is no test for the plugin today, and this change does
  not add one, because the repository has no JavaScript test runner. The
  check is the live tool call.

## 4. The checks

| Check | Command | What it proves |
|---|---|---|
| live tool | `product_hunter` with `action: list` | the tool answers, which is accept criterion A1 |
| code gate | `uv run pytest` | the repository suite still passes, criterion A6 |
| diff scope | `git diff --stat` | no file outside the radius changed, criterion A2 |
| spawn shape | read the diff | the argv list and `shell: false` survive, criterion A4 |

A live tool call needs the plugin to be reloaded. If the running session keeps
the old plugin, the report says so and the check moves to a restarted session.

## 5. Rollback

Revert `.opencode/plugins/product-hunter.ts`. The registry never changed, so
nothing else needs to be undone.

## 6. Radius

`.opencode/plugins/product-hunter.ts`, `docs/reasonix/specs/plugin-project-root-spec.md`,
`docs/reasonix/plans/plugin-project-root-plan.md`, the four dispatch prompts
under `docs/reasonix/prompts/`, `docs/reasonix/handoffs/plugin-project-root-handoff.md`,
and `docs/reasonix/reports/plugin-project-root.md`.

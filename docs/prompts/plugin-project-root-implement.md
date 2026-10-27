# Prompt — implement the plugin project-root resolution

Node: `loop-free-implement-1`
Stage: 4 IMPLEMENT
Risk: medium
Blast radius: `.opencode/plugins/product-hunter.ts`. The documents under
`docs/reasonix/**` are written by the orchestrator, not by you.

## Read first

1. `docs/reasonix/specs/plugin-project-root-spec.md` — the contract. Accept
   criteria A1 to A6 are the definition of done.
2. `docs/reasonix/plans/plugin-project-root-plan.md` — the design, the
   candidate order, and the checks.
3. `docs/reasonix/handoffs/plugin-project-root-handoff.md` — the observed
   behaviour, the file map, and the house rules.
4. `.opencode/plugins/product-hunter.ts` — the file you change.
5. `src/product_hunter/config.py`, function `repo_root()` — the house rule you
   mirror in TypeScript.

## The defect, already diagnosed

The plugin spawns the registry like this:

```
uv run --project <directory> python -m product_hunter.registry <action> ...
```

`<directory>` is `toolCtx?.directory || process.cwd()`. In this runtime neither
is the project root, so `uv` received a directory with no `pyproject.toml`,
built a bare environment on its default interpreter, and the import of
`product_hunter` failed:

```
the registry exited with 1: ...cpython-3.13...python.exe: Error while finding
module specification for 'product_hunter.registry'
(ModuleNotFoundError: No module named 'product_hunter')
```

`uv run --project "C:\Anubhav\Web Dev Projects\product-hunter" python -m
product_hunter.registry list` answers normally with
`{"ok":true,"action":"list","message":"0 research run(s)","text":"no research runs", ...}`.
The registry is fine. The bridge pointed `uv` at the wrong directory.

## What to build

One file: `.opencode/plugins/product-hunter.ts`.

A module-level resolver that returns the nearest ancestor directory, starting
at a given directory, that holds a `pyproject.toml`, and nothing when there is
none. The walk ends at the filesystem root. Standard library only:
`node:fs`, `node:path`, `node:url`.

This is the same rule `config.repo_root()` already uses on the Python side. Do
not invent a second definition of the project root, and do not hardcode a
path.

Use it in this order, first hit wins:

1. the plugin file's own directory, found from `import.meta.url`
2. the tool context directory
3. `process.cwd()`

The chosen directory is the `--project` argument and the `cwd` of the child
process. When no candidate yields a root, the plugin still spawns, in the
working directory, and the failure text says which directory it used.

Put the directory into the spawn failure message. A bridge error that does not
say where it looked is the defect this change is about.

## Rules

- `spawn` keeps an argument vector, `shell: false`, and the 30 second timeout.
  A command string is forbidden.
- The action list, the input schema, the tool name, the tool description, and
  the `payload.text` return path do not change.
- No file under `src/`, `db/`, or `config/` changes.
- No test changes, and no test is added. This repository has no JavaScript
  test runner. The check is the live tool call, listed below.
- Node standard library only. No new dependency.
- The smallest change that fixes the bridge. No option, flag, configuration
  value, or fallback path that nobody asked for.
- Comments explain why, in one line. No comment that restates the code.
- English prose. No emoji, no exclamation marks.

## Check your own work before you report

1. Read the diff. Confirm one file changed, the argv list survived, and
   `shell: false` survived.
2. Run `uv run pytest` from the repository root and report the result.
3. Run the resolver by hand from the repository root to show the directory it
   returns, for example with a one-line `bun` or `node` script that imports
   the plugin file. If neither runtime is available, say so plainly instead
   of claiming a result you did not see.

You cannot call the `product_hunter` tool yourself, because the tool is what
you are fixing. The orchestrator makes that call after you report.

## Report back

- The file you changed, with the line count of the diff.
- The verbatim tail of the `uv run pytest` output.
- The directory the resolver returns, and how you observed it.
- Every place where you deviated from the spec, and why.
- Anything you could not build safely, and what is missing.

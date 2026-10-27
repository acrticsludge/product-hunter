# Plugin Project Root — Definition

**Date:** 2026-10-21
**Lane:** backend tooling. No UI file is touched, so no `DESIGN.md` is required.
**Source of truth:** `docs/reasonix/specs/phase-2-opencode-interface.md` (the plugin transport), `docs/reasonix/plans/phase-2-opencode-interface.md` (`.opencode/plugins/product-hunter.ts`), `src/product_hunter/config.py` (`repo_root()`)
**Risk:** medium, the floor this repository already carries.

## 1. The defect

`product_hunter` is the control surface for the research registry. The plugin
spawns the registry with an argument vector and no shell:

```
uv run --project <directory> python -m product_hunter.registry <action> ...
```

`directory` came from the tool context, with the process working directory as
the fallback. In this runtime the tool context carries no project root, so `uv`
was pointed at a directory without a `pyproject.toml`. `uv` answered with a
bare interpreter, the package was not importable, and every action failed:

```
the registry exited with 1: ...cpython-3.13...python.exe: Error while finding
module specification for 'product_hunter.registry'
(ModuleNotFoundError: No module named 'product_hunter')
```

The same command pointed at the repository root answers normally:

```
uv run --project "C:\Anubhav\Web Dev Projects\product-hunter" python -m product_hunter.registry list
{"ok":true,"action":"list","message":"0 research run(s)","text":"no research runs", ...}
```

The registry is sound. The bridge pointed `uv` at the wrong place.

## 2. The rule for finding the project root

The Python package already answers this question. `config.repo_root()` walks up
from the package file and returns the first directory that holds a
`pyproject.toml`. The plugin gets the same rule, for the same reason: one
definition of the project root, not two.

The plugin therefore resolves the project root itself, walking up from the
plugin file. A candidate with no `pyproject.toml` anywhere above it is
rejected. When no candidate yields a root, the plugin spawns in the working
directory and the failure message names the directory it used. `uv` accepts
any directory, so the manifest check is what keeps a wrong directory from
turning into a confusing import error.

The walk takes the nearest manifest it finds, which is the same limit the
Python rule has: a `pyproject.toml` belonging to another project above the
checkout would be taken as the root. The plugin file ships with its own
checkout, so in practice the nearest manifest is this repository's.

The bridge's argv shape is in scope for the same reason the directory is.
The values now ride in the equals form, `--objective=`, `--research-id=`,
`--chat-id=`, `--limit=`, so a value that begins with a dash arrives as a
value and not as an option. The registry parser accepts the form, and the
phase 2 harden round had already recorded it as the contract. That change
came from concurrent work in the same file, and it is noted here so the diff
in this run is accounted for.

## 3. Accept criteria

| # | Criterion | How it is proved |
|---|---|---|
| A1 | the tool answers | `product_hunter` with `action: list` returns the registry text instead of an error |
| A2 | the registry is unchanged | the diff touches no file under `src/`, `db/`, or `config/` |
| A3 | the project root is confirmed, never assumed | the plugin checks for `pyproject.toml` before it spawns `uv` |
| A4 | the spawn stays an argument vector | the diff keeps `spawn` with an argv list and no shell string |
| A5 | a failure names the directory | the error text carries the directory that was used, so the next failure is diagnosable |
| A6 | the test suite still passes | `uv run pytest` is green |

## 4. Assumptions

- The plugin file keeps living in `.opencode/plugins/`, three levels below the
  project root. The walk depends on that only as a starting point, since it
  searches upward rather than counting levels.
- The plugin file is loaded from its own path, and the running server loads it
  as a `file:` URL. A loader that bundled plugins elsewhere would leave the
  walk without a manifest, and the plugin would then fall back to the working
  directory and fail with a message that names the directory it used. The tool
  context carries no usable project root, which is the defect this change
  fixes, so it is insurance rather than a second working way in.
- No behaviour of the registry, the worker, or the research pipeline changes.
  The fix is inside the bridge.

## 5. Radius

`.opencode/plugins/product-hunter.ts`, this document, the plan, the four
dispatch prompts, the handoff, and the report. Nothing else. A change that
needs more goes back to this stage and says so.

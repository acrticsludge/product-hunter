# Plugin project root — Report

**Date:** 2026-10-21
**Spec:** `docs/reasonix/specs/plugin-project-root-spec.md`
**Plan:** `docs/reasonix/plans/plugin-project-root-plan.md`
**Risk:** medium, the floor this repository already carries.

## 1. What was broken

The `product_hunter` tool could not reach the registry. Every action failed
with the same text:

```
the registry exited with 1: C:\Users\anubh\AppData\Roaming\uv\python\cpython-3.13-windows-x86_64-none\python.exe: Error while finding module specification for 'product_hunter.registry' (ModuleNotFoundError: No module named 'product_hunter')
```

The plugin passed the directory it read from the tool context to
`uv run --project`. That directory held no `pyproject.toml`, so `uv` built a
bare environment on its default interpreter and the package was not
importable. The registry was never reached, and the error said nothing about
the directory that caused it.

The same command with the repository as the project answered normally, which
is how the fault was placed in the bridge rather than in the registry.

## 2. What changed

| File | Change |
|---|---|
| `.opencode/plugins/product-hunter.ts` | 55 insertions, 12 deletions against the pre-change baseline |
| `tests/test_plugin_project_root.py` | new, 2 tests |
| `docs/reasonix/specs/plugin-project-root-spec.md` | the contract, corrected twice during review |
| `docs/reasonix/plans/plugin-project-root-plan.md`, `docs/reasonix/prompts/plugin-project-root-*.md`, `docs/reasonix/handoffs/plugin-project-root-handoff.md` | the trail |

The rule is the one the Python package already uses. `config.repo_root()`
walks up from the package file and takes the first directory that holds a
`pyproject.toml`. The plugin now does the same from its own file, and it
checks the manifest before it hands a directory to `uv`. A candidate with no
manifest anywhere above it is rejected, so a wrong directory cannot turn into
a confusing import error.

The failure messages now name the directory they used, in all six spawn
failure paths. That was the defect this change exists to remove: a bridge
error that does not say where it looked.

## 3. The evidence

The code gate, run by the verify node on a quiet tree:

```
........................................................................ [ 95%]
....................................                                     [100%]
828 passed in 11.88s
[loopgate] PASS  exit 0    in 0m13s
```

A second independent run of the same gate, after the tree went quiet, gave
`828 passed in 11.44s`, exit 0.

The new test file on its own: `2 passed in 0.58s`, not a skip.

The live tool, before and after:

```
before:  the registry exited with 1: ...ModuleNotFoundError: No module named 'product_hunter'
after:   3 research run(s)
RH-20261021-0001  manual inventory reconciliation for small retailers  PAUSED  PLAN  0%  last 2026-10-21T07:37:35.251607+00:00
RH-20261021-0003  Track the billing pain  QUEUED  PLAN  0%  last 2026-10-21T07:49:20.417250+00:00
RH-20261021-0002  shift handover paperwork for nurses  QUEUED  PLAN  0%  last 2026-10-21T07:37:46.487168+00:00
```

The harden node found nothing and returned `VERDICT: ship`. The review node
returned `Verdict: ship` in three rounds.

## 4. What the reviewers raised, and what happened to it

Taken and applied:

- the comment above the candidate chain promised a confirmation the last
  candidate never got
- the timeout, the two output overflow paths and the no-JSON message did not
  name the directory, so the criterion was only half met
- `fileURLToPath(import.meta.url)` could throw while the module loads, which
  would take the whole tool down
- the resolver had no check, and the live call covered one of its outcomes
- the spec claimed the fallbacks were a second working way in, which they are
  not
- the new test hard-failed when the plugin file was absent, and its second
  test could not fail for the case it named
- a comment that no longer matched the code

Declined, with the reason:

- taking the project root from the plugin setup context as the first
  candidate. The walk already mirrors the Python rule on the same checkout,
  the field name cannot be confirmed against the running server, and
  `location.directory` may be a session directory rather than the project
  root.
- caching the resolved root. The walk costs 79 microseconds against a
  subprocess that costs hundreds of milliseconds.
- replacing `statSync` with `existsSync` for four lines. The Python rule
  tests `is_file()`, and `existsSync` would accept a directory named
  `pyproject.toml`. One rule, not two nearly identical ones.

## 5. Waivers

- The sub-question check was unavailable. The SubQ endpoint answered every
  request with a validation error on the question schema, and publishes no
  schema to answer it from. Nine calls were made, all with the same 422. The
  risk classification was made without it.
- The second test in the new file does not execute its assertions on this
  machine, because a walk from the pytest temporary directory finds no
  manifest up to `C:\`. The first test carries the weight here.
- The ancestor assertion compares a resolved path with an unresolved one. It
  holds on this machine, and it would fail on a correct resolver where the
  temporary directory is reached through a symlink.
- No type check was run, because the repository has no `package.json` and no
  `tsconfig.json`. The file parses and runs under Node 24 and Bun 1.4.

## 6. Not checked

- The candidate order is proven by the live call only, which shows the first
  candidate winning. The second and third candidates were never walked.
- The skip paths in the new test, for a missing node and for a node that
  cannot run a TypeScript file, were exercised by the implementer and not by
  the gate.
- The plugin was reloaded by the running opencode host on the file change.
  Whether that reload is guaranteed on every host is not established.

## 7. The concurrent actor

Another actor was editing this repository throughout the run. It changed
`config/model_routing.yaml`, `src/product_hunter/db/model_routing.py`,
`src/product_hunter/db/jobs.py`, `src/product_hunter/db/__init__.py`,
`src/product_hunter/cli.py`, several test files, and the `buildArgs`
function in the plugin file itself, where the values now ride in the equals
form.

One gate run was red because of that actor: five collection errors, all
`ImportError: cannot import name 'record_model_executions' from
'product_hunter.db'`, from a package caught mid-write. The failure is in
Python code the plugin cannot affect, and the import resolves again once the
write settles. Every gate result quoted in this report is from a run on a
tree that was quiet.

The equals-form change in `buildArgs` is in the committed diff. It was
reviewed on its merits: the registry parser accepts the form, it stops a
value that begins with a dash from being read as an option, and the phase 2
harden round had already recorded it as the contract. The spec notes it, so
the diff is accounted for.

## 8. The rule, for the next reader

The project root is the nearest directory at or above the starting point that
holds a `pyproject.toml`. The Python package and the plugin apply the same
rule, each from its own file, and each is inside the same checkout. A
manifest belonging to another project would be taken as the root. That limit
is the same on both sides, and it is written down in the spec.

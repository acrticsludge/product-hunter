# Prompt — harden the plugin project-root change

Node: `loop-free-harden`
Stage: 6 HARDEN
Risk: medium
Blast radius under review: `.opencode/plugins/product-hunter.ts`

You report gaps. You do not edit anything.

## What the change does

The `product_hunter` OpenCode plugin spawns the research registry:

```
uv run --project <directory> python -m product_hunter.registry <action> ...
```

The change replaces the way `<directory>` is chosen. It used to come from the
tool context with the process working directory as the fallback. It now comes
from a resolver that walks up from the plugin file, then the tool context,
then the working directory, and returns the nearest directory that holds a
`pyproject.toml`.

Read the diff, the whole file, and the spec at
`docs/reasonix/specs/plugin-project-root-spec.md`.

The plugin file is untracked, so `git diff` shows nothing for it. The change
is in two files outside the repository:

- `C:\Users\anubh\AppData\Local\Temp\opencode\product-hunter.ts.baseline` —
  the file before the change
- `C:\Users\anubh\AppData\Local\Temp\opencode\product-hunter.diff` — the
  unified diff, produced with `git diff --no-index`

The five hunks are the standard library imports, the `findProjectRoot`
resolver, the module level `PLUGIN_DIR`, the two spawn failure messages that
now name the directory, and the candidate order that replaces the old
`String(toolCtx?.directory || process.cwd())`.

## What to look for

- **Command injection.** The spawn must keep an argument vector, `shell: false`,
  and no command string. The action, the research id, the objective, and the
  limit all come from the model or the user, and they are arguments to a
  process, never part of a shell line. Check that nothing concatenates them
  into a string, and that no value reaches a path.
- **Directory confusion.** The resolver decides where code runs. Check that it
  cannot be steered by untrusted input, that it cannot walk to an unexpected
  parent, and that the path it returns is one this repository recognises.
- **Path handling on Windows.** The path contains a space
  (`C:\Anubhav\Web Dev Projects\product-hunter`). Check that the code does not
  quote, escape, or split the path by hand.
- **The timeout and the failure path.** The child process must stay bounded,
  and a failure must not leave a process running.
- **Secrets.** No secret, key, or environment value may be written to a
  message, a log, or a file by this change.
- **Observability.** A failure that does not say where it looked is the defect
  this change exists to remove. Check that the directory reaches the error
  text, and that no sensitive value could ride along with it.

The repository's forbidden rules are in `.loop/adapter.json`. Two of them bear
on this file: no shell string for a subprocess, and no secret committed to the
repository.

## Rules

- You cannot edit. Report the gap, the file and line, and the reason it
  matters.
- Do not report a style opinion as a finding. A finding needs a consequence:
  what breaks, and under what.
- "Nothing found" is a valid result. Say it plainly when the change holds up.

## Report back

- The verdict, in one line: `VERDICT: ship`, `VERDICT: fix`, or
  `VERDICT: block`.
- Each finding with a file and line, what it allows, and the smallest change
  that closes it.
- The checks you ran, and the ones you could not run.

# Prompt — verify the plugin project-root change

Node: `loop-free-verify-code-1`
Stage: 5 VERIFY
Risk: medium
Blast radius under test: `.opencode/plugins/product-hunter.ts`

You run the gate and report. You do not edit anything.

## The gate

The repository gate is declared in `.loop/adapter.json`:

```json
"gates": { "code": ["uv run pytest"] }
```

Run it from the repository root, exactly as written:

```
uv run pytest
```

Report the verbatim tail of the output, the exit code, and the pass and fail
counts. A gate that did not run is `unknown`, and `unknown` is a failure, not
a pass.

## The other checks

The plugin file is untracked, so `git diff` shows nothing for it. The change
is in two files outside the repository:

- `C:\Users\anubh\AppData\Local\Temp\opencode\product-hunter.ts.baseline` —
  the file before the change
- `C:\Users\anubh\AppData\Local\Temp\opencode\product-hunter.diff` — the
  unified diff, produced with `git diff --no-index`

Read both. The diff has five hunks: the standard library imports, the
`findProjectRoot` resolver, the module level `PLUGIN_DIR`, the two spawn
failure messages that now name the directory, and the candidate order that
replaces the old `String(toolCtx?.directory || process.cwd())`.

The spec is `docs/reasonix/specs/plugin-project-root-spec.md`. Check each
criterion and say which ones the evidence covers:

- A2: the diff touches no file under `src/`, `db/`, or `config/`. Use
  `git status --short` and `git diff --stat`, and note that the working tree
  was already dirty before this change, so separate the pre-existing
  modifications from the ones this change made. The pre-existing baseline is
  in the handoff.
- A4: the spawn in `.opencode/plugins/product-hunter.ts` still uses an
  argument vector and `shell: false`, with no command string.
- A6: `uv run pytest` is green.

A1, A3 and A5 need the live tool, which the orchestrator runs after you
report. Say so, do not guess.

## Rules

- You cannot edit. If a check fails, report the failure and the evidence.
- Do not change a test to make it pass. A failing test is the signal.
- Do not run the full suite twice to get a nicer number. One run, reported as
  it came out.

## Report back

- The gate command, the exit code, the verbatim tail, and the counts.
- A line per accept criterion: covered or not, with the evidence.
- The verdict, in one line: `GATE: code` followed by `RESULT: pass`, `fail`,
  or `unknown`.

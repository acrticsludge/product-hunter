# Prompt — review the plugin project-root change

Node: `loop-free-review-1`
Stage: 7 REVIEW
Risk: medium
Blast radius under review: `.opencode/plugins/product-hunter.ts`

You are the adversarial reader. The gates have already run. Your job is to
try to prove the change wrong before it ships.

## The change in one paragraph

The `product_hunter` OpenCode plugin spawns the research registry with
`uv run --project <directory> python -m product_hunter.registry <action>`.
Before this change, `<directory>` came from the tool context with
`process.cwd()` as the fallback. In this runtime that pointed `uv` at a
directory with no `pyproject.toml`, so every tool call failed with
`ModuleNotFoundError: No module named 'product_hunter'`. The change resolves
the directory from the plugin file, the tool context, or the working
directory, taking the first candidate whose upward walk finds a
`pyproject.toml`, and it puts the directory into the failure message.

## Read

1. `docs/reasonix/specs/plugin-project-root-spec.md` — the contract and the
   accept criteria.
2. `docs/reasonix/plans/plugin-project-root-plan.md` — the design.
3. `docs/reasonix/handoffs/plugin-project-root-handoff.md` — the observed
   behaviour and the baseline.
4. The diff, and the whole of `.opencode/plugins/product-hunter.ts`.
5. `src/product_hunter/config.py`, function `repo_root()` — the house rule
   the change mirrors.

## The six passes

Work through them in order and report each one.

1. **Correct.** Does the resolver find the right directory? What happens when
   the plugin file is not three levels below the root, when the file is
   bundled elsewhere, when the walk reaches the filesystem root, when a
   `pyproject.toml` exists but belongs to a different project?
2. **Safe.** Can the change break a working path? Does the spawn keep its
   argument vector and `shell: false`? Is the timeout still bounded?
3. **Load.** The tool is called a handful of times per session. Does the
   resolver walk the filesystem on every call, and is that cost acceptable?
4. **Tested.** The repository has no JavaScript test runner, so the change has
   no unit test. The check is the live tool call. Say whether that is enough
   and what would still be untested.
5. **Fast.** The change should add nothing that runs on the hot path beyond
   the walk itself.
6. **Lean.** Is every line needed? Is there a shorter version that still
   fixes the defect? Report the line count a leaner version would save. Do
   not invent a saving you cannot show.

## Rules

- Quote the reviewer's own findings in plain English. No vague advice.
- Severity matters. `Must fix` means the change is unsafe or wrong. `Should
  fix` means it works but something is off. `Nice` means a preference.
- Do not demand features nobody asked for. A change that fixes one defect
  does not need a configuration file.
- "Nothing found" is a valid result. Say it plainly.
- Do not edit anything.

## Report back

- `Verdict: ship`, `Verdict: fix`, or `Verdict: object`, on its own line.
- The six passes, each with a finding or `nothing found`.
- Each finding: severity, file and line, what it allows, and the smallest
  change that closes it.
- `Lean: -N lines possible`, with the evidence for N.

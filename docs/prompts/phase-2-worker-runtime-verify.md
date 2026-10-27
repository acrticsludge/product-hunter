# Prompt — run the code gate for the worker runtime

Node: `loop-free-verify-code-1`
Stage: 5 VERIFY
Command: `uv run pytest` (from `.loop/adapter.json`)

## The situation

The repository working tree is shared with another session that is actively
writing code. Its in-flight files are uncommitted. The gate therefore has two
contexts and both matter.

## What to run

1. The repository gate, from the repository root:
   `uv run pytest`
   Report the verbatim summary line and the full list of failures if there are
   any.

2. If, and only if, the repository gate reports failures in files outside this
   radius, run the same gate against a clean checkout of the base commit with
   this change applied. There is already such a checkout at
   `%TEMP%\opencode\ph-gate` (a detached git worktree at `205a4ba` with the
   eight new runtime files copied in). Run there:
   ```
   $env:PYTHONPATH = "<worktree>\src"
   & "C:\Anubhav\Web Dev Projects\product-hunter\.venv\Scripts\python.exe" -m pytest
   ```
   That is the gate for this change alone: the base commit plus the worker
   runtime package and its two test files. The expected result is
   `398 passed`.

3. Also run the two new test files on their own, from the repository root:
   `uv run pytest tests/test_runtime_worker.py tests/test_runtime_lock.py`
   Report the summary line.

## Rules

- Do not edit, create, delete or move any file in the repository. You are
  reading and running only.
- Do not fix a failing test. A failing test is the signal.
- Do not touch the worktree at `%TEMP%\opencode\ph-gate` except to run the
  commands above. Do not remove it.
- Name every failure by file and test, and say plainly which of the three
  commands it came from.

## Report back

- The verbatim output of each command, including the summary line.
- The pass or fail verdict for the gate on this change.
- The list of failures, if any, split into "inside the worker runtime radius"
  and "outside it", with the reason you believe each belongs there.

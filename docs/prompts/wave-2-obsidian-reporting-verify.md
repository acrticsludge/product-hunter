# Verify: Wave 2 Session 8 — Reporting and Obsidian Projection

## Where to work

```
C:\Anubhav\Web Dev Projects\product-hunter\.worktrees\wave2-obsidian-reporting
```

Branch `wave2/obsidian-reporting`. Do not modify main. Do not commit. Do not
edit any source or test file: this node runs the gate and reports what it
found.

## The gate

The adapter defines the code gate as:

```
uv run pytest
```

Run it from the worktree root. Report the command and the result verbatim:
the pass and fail counts, and the first failure output in full if anything
fails.

## What to check beyond the suite

1. **The whole suite passes**, not only the four new projection test files.
   A change in `src/product_hunter/cli.py` can break a command test.
2. **No test was weakened.** Compare the new test files against the intent in
   `docs/reasonix/specs/wave-2-obsidian-reporting.md` section 12. A skipped
   test, an `xfail`, a loosened assertion or a deleted test is a finding, and
   so is a test that asserts nothing.
3. **The gate is real.** Confirm the four test files actually run and that
   they fail when the behaviour is broken. Prove it by a temporary, reverted
   experiment on a scratch copy: for example, break the sanitizer in a
   scratch copy of the file, run the suite, see it go red, and restore the
   file exactly. Report what you did and what happened. If you cannot prove
   the gate bites, say so.
4. **The diff is inside the declared radius.** The only changed file outside
   the new package and the new test files is
   `src/product_hunter/cli.py`. There must be no change under `db/`, no
   migration, no new dependency in `pyproject.toml`, and no change to any
   pre-existing test.
5. **The projection never writes to the database.** Read
   `src/product_hunter/projection/source.py` and `engine.py` and confirm the
   read side issues no `INSERT`, `UPDATE`, `DELETE` or `CREATE` against the
   authoritative database, and that the engine writes only under the vault
   root.

## Report back

Give me the gate line verbatim, the pass and fail counts, the answer to each
of the five checks above with the evidence, and a list of anything you could
not check. If the gate fails, say so plainly and quote the failure. Do not
fix it; that is the next node's work.

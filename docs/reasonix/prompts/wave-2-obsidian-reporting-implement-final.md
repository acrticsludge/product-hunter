# Implement fixes: Wave 2 Session 8 — final review follow-ups

## Where to work

```
C:\Anubhav\Web Dev Projects\product-hunter\.worktrees\wave2-obsidian-reporting
```

Branch: `wave2/obsidian-reporting`. Do not touch main. Do not commit.

The re-review returned `ship with follow-ups`. Close the follow-ups below.

## 1. The frontmatter parse can raise and break every later run

`engine.py` `_has_marker` catches `yaml.YAMLError` only. `yaml.safe_load` on
a deeply nested frontmatter raises `RecursionError`, which is not a
`YAMLError`, so it escapes `_has_marker` → `_scan_notes` → `_prune` →
`project()`. The notes are written before the prune, so nothing is
corrupted, but the manifest is never written, so every later run fails the
same way, and through the outbox the task retries for ever.

Fix: catch it. A note whose frontmatter cannot be parsed is not the
projection's note, so the parse failure means "not ours", exactly like a
frontmatter without the key.

Add a test: a note with a deeply nested frontmatter in the vault, the
manifest gone, and a run that finishes and writes the manifest.

## 2. An empty manifest leaves the prune silent

A manifest that parses, has a `notes` key, and holds an empty object still
returns an empty list from `_known_notes`, so a stale note survives. Every
other wrong shape falls through to the scan.

Fix: treat an empty notes dict as no notes, and fall through to the scan
like the other wrong shapes.

Add a test with a `{"notes": {}}` manifest and a deleted opportunity.

## 3. A value that ends with a backtick shows its delimiters

`_code` picks a delimiter longer than any run inside the value, but a value
that ends with a backtick merges with the closing delimiter, and the code
span does not form.

Fix: pad the content with one space on each side. CommonMark strips one
space from each end of a code span, so the value then shows as it is. A
value with no backtick keeps the plain single-backtick form, so the notes
that are already right do not change.

Add a test for a value that ends with a backtick and for a value that is
only a backtick.

## 4. The daily briefs index is missing

The spec's vault tree holds `00-Dashboard/Daily Briefs/README.md`, an index
of the daily briefs that live in `03-Daily-Reports/`. Nothing writes it.

Fix: write it, next to the dashboard README, and create the folder.

## 5. The new package's `__init__.py` is not sorted

`src/product_hunter/projection/__init__.py` has an unsorted import block and
an unsorted `__all__`. The repository has no lint configuration, but this
file is new and it should read like the rest of the package.

Fix: sort the imports and the `__all__`. Change nothing else in the file.

## 6. The daily brief is nine copies of the same block

`render.py` `render_daily_brief` is 84 lines of nine identical
heading / blank / body / blank blocks. A small section helper takes about
thirty lines out of it, and the reviewer named it as the largest remaining
piece of duplication.

Fix it only if you can prove the output does not move. Write a test that
pins the whole brief body first, do the change, and show the body is
byte-identical afterwards. If the body changes in any way, put the change
back and leave the function as it is. A duplicated block that renders
correctly is cheaper than a refactor that moves a byte.

## The rules

- Small changes, each with a test.
- Do not weaken an existing assertion.
- Do not touch any file outside:
  `src/product_hunter/projection/__init__.py`,
  `src/product_hunter/projection/engine.py`,
  `src/product_hunter/projection/render.py`,
  `src/product_hunter/projection/vault.py`,
  `tests/test_projection_render.py`,
  `tests/test_projection_engine.py`.

## The check

```
uv run pytest
```

The whole suite, from the worktree root. Report the command and the result
verbatim, then each fix with its file and line, then the tests you added,
then whether the brief body stayed byte-identical.

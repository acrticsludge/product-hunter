# Implement fixes: Wave 2 Session 8 — hardening findings

## Where to work

```
C:\Anubhav\Web Dev Projects\product-hunter\.worktrees\wave2-obsidian-reporting
```

Branch `wave2/obsidian-reporting`. Do not touch main. Do not commit.

## What the hardening pass found

Two findings. Fix both, with a test for each.

### Finding 1: a Dataview query introducer survives in scraped text

`neutralize()` in `src/product_hunter/projection/sanitize.py` escapes the
characters that give text meaning in a vault, but a line that begins with
`= ` keeps its introducer. Dataview and other plugins read that shape as a
query, and the spec forbids scraped text from reaching a plugin action.

Fix: in `_escape_line_starts`, escape a line that starts with `= ` as well as
the ones that start with `#`, `|` and `---`. Keep the escape narrow. Do not
escape every `=` in the text: prose, table rows and URLs that contain `=`
must not change, and a note that a reader reads must not gain a backslash in
the middle of a sentence. A backslash before `=` renders as `=`, so the
visible text of a line that starts with `= ` does not change.

### Finding 2: a lone surrogate crashes the write and turns the task into a poison pill

A value holding a lone surrogate (`\ud800`) reaches a note, and the write
raises `UnicodeEncodeError`. Through the outbox the task then goes back to
pending, fails again on the same row, and never recovers. The projection has
to survive malformed data, because the data is not under this layer's
control.

Fix it at the root and in the last resort:

1. `neutralize()` drops the lone surrogates, in the same place it drops the
   control characters.
2. `yaml_scalar()` drops them too. The frontmatter path does not go through
   `neutralize()`, so a title that holds one would still crash the write.
3. `content_hash()` cannot raise. Encode with a replacement so a hash is
   always computable.
4. The engine's write cannot raise on this. Open the file so that a
   character that cannot be encoded is replaced rather than fatal.

The replacement has to be the same in the hash and in the file, so the hash
still describes the bytes on disk.

## The rules

- The change is small. Four small edits in `sanitize.py` and `engine.py`, and
  the tests that cover them. Do not restructure anything while you are in
  there.
- A test for each finding, in `tests/test_projection_sanitize.py` and
  `tests/test_projection_engine.py`. The sanitize test proves the escape
  happens and that ordinary text with an `=` in it does not change. The
  engine test proves that a note holding a lone surrogate is written, that
  the vault holds it, and that a second run over the same state writes
  nothing.
- Do not weaken an existing assertion. If an existing test fails because of
  your change, the change is wrong.
- Do not touch any file outside:
  `src/product_hunter/projection/sanitize.py`,
  `src/product_hunter/projection/engine.py`,
  `tests/test_projection_sanitize.py`,
  `tests/test_projection_engine.py`.

## The check

```
uv run pytest
```

The whole suite, from the worktree root. It must be green. Report the
command and the result verbatim, the four edits you made, and the two tests
you added.

# Implement fixes: Wave 2 Session 8 — broken note links

## Where to work

```
C:\Anubhav\Web Dev Projects\product-hunter\.worktrees\wave2-obsidian-reporting
```

Branch: `wave2/obsidian-reporting`. Do not touch main. Do not commit.

## The finding

`render.py` `_note_link` emits `[[path]` with one closing bracket, so
Obsidian cannot resolve the link. Every note that carries a link to another
note is affected, which is most of the vault: the dashboard, the brief, the
market notes, the daily-briefs index.

Fix: emit a proper wikilink, `[[path]]`, so the link resolves to the note
at that path. Make the new `_wikilink` helper and `_note_link` agree, so
the vault is consistent, and use one helper for both.

A link into a folder path resolves to the note of that name in that folder,
so the path and the file name have to keep matching. Check that a link to an
opportunity note resolves to the file the projection wrote, and add a test
that says so.

The golden bodies that carry a link have to move with the fix. Update them
to the corrected bytes, and say in the report which goldens you changed and
why.

## Two small things with it

- `src/product_hunter/projection/__init__.py` has no
  `from __future__ import annotations`. Every other package in the
  repository has it. Add it, and change nothing else in the file.
- `00-Dashboard/README.md` does not link the daily-briefs index that now
  exists. Add the link, the way it links the other dashboard notes.

## The rules

- Small changes, each with a test.
- Do not weaken an existing assertion. A golden body that changes because
  the link was wrong is a golden body that gets corrected, not an assertion
  that gets loosened.
- Do not touch any file outside:
  `src/product_hunter/projection/__init__.py`,
  `src/product_hunter/projection/render.py`,
  `tests/test_projection_render.py`,
  `tests/test_projection_engine.py`.

## The check

```
uv run pytest
```

The whole suite, from the worktree root. Report the command and the result
verbatim, then each fix with its file and line, then the goldens you
changed, then the tests you added.

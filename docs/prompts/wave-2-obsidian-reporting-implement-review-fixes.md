# Implement fixes: Wave 2 Session 8 — review findings

## Where to work

```
C:\Anubhav\Web Dev Projects\product-hunter\.worktrees\wave2-obsidian-reporting
```

Branch: `wave2/obsidian-reporting`. Do not touch main. Do not commit.

The review returned `block`. The findings are below, each with the fix. Fix
every one of them, with a test for each.

## Must fix

### 1. A tilde fence opens a code fence

`sanitize.py` escapes the backtick, so a backtick fence cannot open. It never
touches `~`, and `~~~` is a code fence in CommonMark and in Obsidian. A
scraped value that reaches a note body outside a quote fence opens one.

Fix: in `_escape_line_starts`, escape a line that begins with `~~~` the way
the other line starts are escaped. A tilde fence also closes a fence, so the
escape has to apply wherever a fence could open or close, which is the start
of a line.

### 2. The line-start escapes only fire at column 0

CommonMark allows up to three leading spaces and still reads an ATX heading,
a blockquote, a thematic break, a table row or a fence. The current check
tests `line.startswith(...)`, so `   # heading` and `   = this.file.name`
survive untouched.

Fix: measure the indentation, and when it is three spaces or fewer, escape
the marker that follows it. The backslash goes immediately before the
marker, so `   # heading` becomes `   \# heading` and the indentation stays
what it was. Four spaces or more is an indented code block, which is inert
already; leave it alone.

### 3. A wrong-shaped manifest silently disables the prune

`engine.py` `_known_notes` returns an empty list when the manifest parses but
holds no `notes` dict, and `_prune` then has nothing to prune. The scan by
frontmatter marker never runs, so the note of a deleted opportunity stays in
the vault for ever.

Fix: fall back to the scan whenever the manifest does not yield a notes
dict, not only when the manifest file is missing. Add a test that writes a
manifest of the wrong shape and shows the stale note going away.

## Should fix

### 4. The prune can delete a note the projection never wrote

The marker check in `_has_marker` is a substring search over the frontmatter
block. A hand-written note whose frontmatter mentions the marker string is
treated as the projection's own note and is deleted.

Fix: read the `source` key of the frontmatter and compare it with the marker
exactly. The project has PyYAML, so parse the frontmatter block and compare
the key; a note whose frontmatter does not parse, or has no `source` key, or
has a different value, is not the projection's note. Do not delete it.

### 5. The canonical path does not fall back field by field

Spec section 4.3 says the projection falls back "when the table is absent, or
when a row leaves a field empty". `_row_draft` only falls back for the
title, the statement and the spans, so a canonical row with an empty
`severity`, `domain`, `actor`, `action`, `object` or `consequence` reads
`not recorded` while the linked pain point holds the value.

Fix: fill those fields from the matched pain-point group when the canonical
value is empty. The group is already resolved.

### 6. `_code` deletes the backtick instead of keeping it

`neutralize` turns a backtick into `` \` `` and `_code` then removes the
backslash and the backtick together, so the note shows a value the database
does not hold. A URL is the value a reader may copy out of a note and
follow.

Fix: keep the backtick inside the code span. Use a delimiter long enough to
hold the longest run of backticks in the value, which is what CommonMark
asks for.

### 7. The daily brief does not carry the sections the spec names

Spec section 7 names nine sections. The brief has no competitors section,
and its "Top opportunities" section holds the opportunities first seen that
day rather than the top ones.

Fix: make "Top opportunities" use the ranked snapshot, and add a "New
competitors" section that reads `not recorded` until the competitors table
lands. The rule for a table that does not exist yet is the rule for a field
that does not exist yet: say so, do not leave the section out.

### 8. The vault does not have the seven fixed top-level folders

The layout names the seven folders but nothing creates them, so a vault built
from a database with no experiments, no requests and nothing archived has
four of the seven, and `00-Dashboard/README.md` is never written at all.

Fix: create the seven top-level folders on every run, and write the
dashboard README.

### 9. `ph project --outbox` rebuilds the whole vault once per task, and once
more

The handler rebuilds everything and the command projects once more after the
drain, so the cost is tasks times the vault.

Fix: build at most once per drain, and do not build again after a drain that
already built. The command must still produce a vault when there is no
pending task.

## Nice to have

### 10. A missing value is rendered as a zero

With no evidence at all the summary prints `Busiest source share: 0.00`. The
share is unmeasurable with zero mentions, and the rule for a value the
database does not hold is `not recorded`.

Fix: print `not recorded` when there are no mentions.

### 11. Two markets can share one file

`market_path` uses `slug` while the source and community paths use the
component helper. `C++` and `C#` both slugify to `c`, so one market note
overwrites the other.

Fix: build the market path the way the source and community paths are built.

## Lean

The reviewer found about seventy-five lines that could go. Take the two
consolidations that remove real duplication and are safe at this point:

- `render_source` and `render_community` are the same note twice. One shared
  helper for the evidence list saves about a dozen lines.
- `source.py` has three spellings of "the first non-empty value". One
  helper saves about fifteen lines.

Leave the rest of `render.py` alone. A late refactor of the six render
helpers is not worth the risk, and the reviewer marked them as lean, not as
defects.

## The rules

- Small changes, each with a test. Do not restructure while you are in
  there.
- Do not weaken an existing assertion. If an existing test fails because of
  your change, the change is wrong.
- Do not touch any file outside:
  `src/product_hunter/projection/sanitize.py`,
  `src/product_hunter/projection/engine.py`,
  `src/product_hunter/projection/render.py`,
  `src/product_hunter/projection/source.py`,
  `src/product_hunter/projection/vault.py`,
  `src/product_hunter/cli.py`,
  `tests/test_projection_sanitize.py`,
  `tests/test_projection_render.py`,
  `tests/test_projection_source.py`,
  `tests/test_projection_engine.py`.

## The check

```
uv run pytest
```

The whole suite, from the worktree root. It must be green, and the count
must have grown by the number of tests you added. Report the command and
the result verbatim, then each fix with its file and line, then the tests
you added.

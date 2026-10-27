# Implement: Wave 2 Session 8 — Reporting and Obsidian Projection

## Where to work

Work in the worktree, not the main checkout:

```
C:\Anubhav\Web Dev Projects\product-hunter\.worktrees\wave2-obsidian-reporting
```

Branch `wave2/obsidian-reporting`. Do not touch `main`. Do not create
another worktree. Do not commit.

## Read first

1. `docs/reasonix/specs/wave-2-obsidian-reporting.md` — the contract. Section
   numbers below refer to it.
2. `docs/reasonix/plans/wave-2-obsidian-reporting.md` — the architecture, the
   module surface, the step list and the test plan.
3. `src/product_hunter/db/outbox.py` — the transactional outbox you plug
   into. Reuse it; do not write a second queue.
4. `src/product_hunter/db/store.py`, function `_enqueue_projection` — the
   `obsidian` target that is already enqueued and has no handler.
5. `src/product_hunter/research/evidence.py` and
   `src/product_hunter/research/persistence.py` — the house style for data
   models and for a wave that does not own the schema.
6. `src/product_hunter/safety.py` — the house style for handling hostile
   input.
7. `db/migrations/0001_initial.sql` — the columns you read.
8. `src/product_hunter/cli.py` — the command style, `_db_path`, `_root`,
   `_require_db`, and the `db` command group.

## What to build

A new package `src/product_hunter/projection/` with the seven modules of
plan section 1.2, and a `ph project` command.

The public surface, the record shapes, the module boundaries and the step
list are in the plan. Follow them. The behaviours that matter:

- **The database is read-only.** The projection opens nothing and writes
  nothing. The connection is passed in.
- **Deterministic.** No wall-clock time reaches a note. The reference date is
  the newest timestamp in the data unless the caller passes one. Every list
  is sorted by an explicit key. Numbers are written at a fixed precision.
  Two runs over the same state produce the same bytes.
- **Idempotent.** A note is written only when the bytes differ, so a second
  run touches no file and changes no file time.
- **Regenerable.** The vault can be deleted and rebuilt. A note whose
  opportunity is gone is removed. The manifest at the vault root
  (`.product-hunter-projection.json`) holds the written notes; when it is
  missing, the engine finds its own notes by the frontmatter marker and
  prunes the ones that are gone.
- **Untrusted content.** Every string that came from a page, a quote, a
  community name or a source title goes through the sanitizer. The rules are
  in spec section 8. A quote is rendered inside a fenced block tagged `text`.
  A URL is written as inline code, never as a link, and a scheme other than
  `http` or `https` is replaced.
- **Missing means missing.** A field the database does not hold renders as
  `not recorded`. Never a zero, never an estimate.
- **Every material claim names the evidence behind it.**

## The read contract

Read spec section 4 before you write the reader. The important part: the
`opportunities` table does not exist in this repository yet, so the reader
must work from the pain points when that table is absent, and use it when it
is there. `contract.py` holds the DDL of the canonical tables; the tests
apply that same constant to a temporary database, so the contract is pinned
by a test.

Ask `PRAGMA table_info` for the columns you want and select only the ones
that exist. A sibling session that lands a table with a different shape
must degrade the projection, not crash it.

## Files you may write

```
src/product_hunter/projection/__init__.py
src/product_hunter/projection/contract.py
src/product_hunter/projection/models.py
src/product_hunter/projection/sanitize.py
src/product_hunter/projection/render.py
src/product_hunter/projection/vault.py
src/product_hunter/projection/source.py
src/product_hunter/projection/engine.py
src/product_hunter/cli.py
tests/test_projection_sanitize.py
tests/test_projection_render.py
tests/test_projection_source.py
tests/test_projection_engine.py
```

Nothing else. In particular, no migration, no new dependency, no change to
`db/`, and no change to any existing test.

## Style

The repository has a voice. Read a couple of the existing modules and match
it.

- A module docstring says what the module holds and what it refuses to do.
  It explains why, in one or two sentences, not a paragraph of restating the
  code.
- Data records are pydantic models with `extra="forbid"`, the way
  `research/evidence.py` does it.
- Comments explain the why, and only where the code cannot show it. A
  comment that repeats the code is noise; do not write it.
- Type hints everywhere. No `Any`.
- `from __future__ import annotations` at the top of every module.
- No new third-party dependency. The project has pydantic, httpx,
  beautifulsoup4, lxml, PyYAML and python-dotenv. The layer needs pydantic
  and the standard library, and nothing else. Use `json.dumps` for the YAML
  scalar: a JSON string is a valid YAML double-quoted scalar.
- Write the docstrings and the comments as prose a person would write. No
  filler, no marketing, no sentence that lists three things because three
  is a nice number.

## Tests

Write the four test files from plan section 3. They must cover the matrix
in spec section 12: deterministic rendering, idempotent projection, a
changed opportunity, a deleted opportunity, malformed source text,
malicious markdown, HTML, duplicate projection tasks, a failed projection
and its retry, and stable filenames and ids.

Each test builds its own temporary database and its own temporary vault. No
test touches the repository's own database, and no test writes outside
`tmp_path`.

Do not weaken an assertion to make a test pass. If a test cannot pass, the
code is wrong; fix the code.

## Checks

Run the code gate from the worktree root before you report back:

```
uv run pytest
```

It must be green, and it must be green for the whole suite, not only for
your new files. Report the exact command and its exact result.

## Report back

Give me: the files you wrote with their line counts, the public surface you
exposed, the test results verbatim, and the two or three decisions you made
that the spec left open, with the reason. Also name what you did not build
and what you did not check.

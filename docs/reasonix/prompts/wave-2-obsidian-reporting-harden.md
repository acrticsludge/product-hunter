# Harden: Wave 2 Session 8 — Reporting and Obsidian Projection

## Where to work

```
C:\Anubhav\Web Dev Projects\product-hunter\.worktrees\wave2-obsidian-reporting
```

Branch `wave2/obsidian-reporting`. Do not modify main. Do not commit. You
report gaps; you do not fix them. The hardening pass is a read.

## The contract

The layer turns authoritative SQLite state into Markdown notes in an Obsidian
vault. Scraped text is untrusted. SQLite stays authoritative and the vault is
a one-way projection.

Read these first:

- `docs/reasonix/specs/wave-2-obsidian-reporting.md` — the contract, and
  section 8 in particular, which lists what the sanitizer must neutralize.
- `docs/reasonix/plans/wave-2-obsidian-reporting.md` — the architecture.
- `src/product_hunter/projection/sanitize.py`, `vault.py`, `source.py`,
  `engine.py`, `render.py`, `contract.py`.
- `src/product_hunter/cli.py`, the `ph project` command.
- `.loop/adapter.json` — the rules this repository holds itself to.

## What to examine

### Security

1. **Untrusted content.** Every string from a page, a quote, a community name
   or a source title must be inert in the note. Try to break it. HTML, a
   script tag, an `onerror` attribute, a wikilink, an embed, a Templater
   expression, a Dataview inline query, a Dataview fence, a callout, a
   comment, a heading, a table row, a blockquote, a frontmatter delimiter, a
   `javascript:` URL, a `data:` URL, a `file:` URL, a link whose target is
   hostile, a control character, a null byte, a lone surrogate, a very long
   line, a CRLF, a form feed, a Unicode bidi override, a zero-width joiner.
   A note must never contain markup that Obsidian would act on.
2. **Frontmatter injection.** A title, a community name or a status that
   contains a newline, a colon, a quote or a `---` must not break out of the
   frontmatter, and must not change the shape of the YAML.
3. **Path traversal.** The vault layout builds file names from scraped
   titles. A title of `../../etc/passwd`, of `..`, of a Windows reserved
   name, of a name with a colon, of a name that is all dots, or of a name
   that is very long must not write outside the vault root, and must not
   write a file that cannot be deleted on Windows.
4. **SQL.** The reader builds SQL text from a table name and a column list.
   Check where those come from and whether a hostile value can reach them.
5. **The filesystem.** The engine writes through a temporary file and a
   rename. Look for a path where a reader sees a half-written note, where a
   crash leaves litter, where a symlink in the vault redirects a write, and
   where a path is built from a value that came from a page.
6. **The database.** The projection must not write to it. The outbox path
   may write task bookkeeping, and that is the outbox's own connection.
7. **Secrets.** No key, token or password reaches a note, and no note
   carries a field that has no business being published to a human.

### Observability

8. **Entry and branch.** The repository's convention is that a failure
   returns a structured error and the command prints JSON. Check that the
   `ph project` command says what happened on every failure path, including
   a missing database, an unreadable vault, a malformed note, and a failed
   projection, and that it never prints a traceback where a message belongs.
9. **The failure law.** When the projection fails, SQLite must stay
   authoritative and the task must be retryable. Check that the outbox
   handler lets the exception out, that the task goes back to pending, and
   that the next attempt can succeed.

### Robustness

10. **Malformed input.** A quote that is not valid text, a document with a
    null byte, a row with a missing column, a number stored as a string, a
    `NaN` or an infinite float, a date in the wrong format, a table that
    exists with a different shape, a table that exists but is empty.
11. **Scale.** A quote of a million characters, ten thousand opportunities,
    a note that would be a hundred megabytes. The projection must not hang
    or fill the disk without a bound.

## How to report

For each finding: what it is, where it lives (file and line), how a reader
or an attacker reaches it, and what the fix would be. Rank the findings by
what actually hurts, and separate the ones that are exploitable from the ones
that are untidy.

Say plainly when something is already handled, and name the check that
proves it. A report that says "no findings" without evidence is worth
nothing; a report that quotes the line of code that stops the attack is
worth a lot.

Do not report a style preference as a finding. Do not report the absence of
the canonical tables as a finding; the spec explains why they are not here.

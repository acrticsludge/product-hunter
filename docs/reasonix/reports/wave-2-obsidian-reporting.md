# Wave 2 Session 8: reporting and Obsidian projection layer, wave report

**RUN_ID:** wave2-s8-20261021-obsidian-reporting
**Node:** PRODUCTIONIZE (stage 8)
**Date:** 2026-10-21
**Risk:** declared medium, floor medium
**Branch:** `wave2/obsidian-reporting`
**Code commit:** `9a892bbb0c846ff0b590c8877acdad524bbf3ba3`
**Report commit:** the commit that carries this file, which is the second
commit on the branch

## What this session delivered

The human-readable layer: a projection of the authoritative database into an
Obsidian vault. SQLite stays the source of truth. The vault is a one-way
projection that can be deleted and rebuilt, and nothing reads a note back
into the database.

The new package `src/product_hunter/projection` holds seven modules. The
reader (`source.py`) turns the database into a snapshot. The renderers
(`render.py`) turn the snapshot into note bodies. The layout (`vault.py`)
decides where a note lives. The engine (`engine.py`) writes the notes that
changed, prunes the ones that are gone, and keeps the manifest. The
sanitizer (`sanitize.py`) makes sure that text a web page said cannot act
inside a vault. The contract (`contract.py`) names the tables the projection
reads and the columns it takes from them.

`ph project` builds the vault. `ph project --outbox` drains the
transactional outbox instead, which is the path the architecture wants: the
`obsidian` projection target that `db/store.py` has been enqueueing since the
outbox landed had no handler, and every task it produced failed with
`no handler for target 'obsidian'`. `Projector.handle_projection_task()` is
that handler.

The session lands no migration and owns no table. Section "Integration
requirements" says why, and what another session has to do.

## Gate evidence

The code gate is `uv run pytest`, from `.loop/adapter.json`.

```text
uv run pytest
1105 passed in 19.76s
exit code 0
```

The projection package on its own:

```text
uv run pytest tests/test_projection_sanitize.py tests/test_projection_render.py tests/test_projection_source.py tests/test_projection_engine.py
125 passed in 5.68s
exit code 0
```

The 125 split as 35 sanitizer, 35 render, 22 source and 33 engine tests. The
1105 is the whole suite, and it is the suite that matters: the new command
lives in `src/product_hunter/cli.py`, which the command tests cover.

The gate was also proven to bite rather than merely to pass. The HTML escape
in the sanitizer was removed on a scratch copy of the file, the suite went
red on `test_html_is_escaped` and
`test_a_leading_quote_does_not_open_a_blockquote`, and the file was restored
byte for byte: the sha256 of the file was
`1533E5F408858198BFF4C67285A15D5DA7C99F72941981670CE1E08C5C3F867A` before
and after the restore. The file has changed since, through the hardening
fixes below. The implementer did the same for every later fix round and
reported which of the new tests fail against the old code.

One caveat on the gate: the code-gate subagent was unavailable at the
provider for the whole session (three attempts on rung 1, one on rung 2, both
reporting an endpoint or model failure). The gate was run in this session
instead, and the five checks that node would have done were run here too: the
whole suite passes rather than only the new files; no test carries a skip, an
xfail or a loosened assertion; the gate bites; the diff sits inside the
declared radius with no change under `db/`, no migration, no new dependency
and no change to any pre-existing test; and the projection issues no write
statement against the database, only `PRAGMA table_info` and `SELECT`.

## The projection design

### One writer, one reader, and nothing in between

The renderer never touches the filesystem and the reader never writes. The
engine is the only module that writes, and it writes only under the vault
root. That split is what makes the layer cheap to test: the renderer is
tested on strings, the engine on a temporary directory, and the reader on a
temporary database.

The database connection is passed in, and `ph project` opens it with
`read_only=True`, which is a `mode=ro` URI. A reviewer checked the database
file's bytes after a run and they were identical.

### Deterministic, idempotent, regenerable

**Deterministic.** No wall-clock time reaches a note. The reference date is
the newest timestamp in the data unless the caller passes one with
`--as-of`. Every list is sorted by an explicit key, and every number is
written at a fixed precision. Two vaults built from one database are
byte-identical, and a reviewer confirmed it by building two and comparing.

**Idempotent.** A note is written only when the bytes on disk differ, so a
second run over unchanged state touches no file and changes no file time. A
reviewer compared every `st_mtime_ns` before and after a second run: no
change.

**Regenerable.** The vault can be deleted and rebuilt. The engine keeps a
manifest at the vault root, `.product-hunter-projection.json`, that maps each
written note to its id, kind and hash. A note the snapshot no longer holds is
removed, which is what happens when an opportunity is deleted. When the
manifest is missing, or corrupt, or holds a shape the engine does not
recognise, the engine finds its own notes by the `source` key in their
frontmatter and prunes the ones that are gone. A reviewer tried six manifest
shapes (absent, not JSON, no `notes` key, `notes` a list, `notes` a string,
`notes` an empty object) and the stale note went away in all six.

### The read contract, and why there is no migration

The projection reads the tables that exist today: `pain_points`, `evidence`,
`evidence_pain_links`, `documents`, `communities`, `authors`, `author_links`,
`propagation_edges`, `sources`, `human_decisions`, `research_runs`,
`research_artifacts` and `research_events`.

The product spec also names `opportunities`, `scores`, `score_snapshots`,
`claims`, `claim_evidence`, `competitors`, `experiments` and
`research_requests`. Three of those tables are the scoring session's to
build, and this session does not own a table.

`db/migrations.py` refuses a duplicate migration number and refuses a gap in
the numbering, so a migration added here would collide with whichever
sibling session picks `0003` next, and would break `migrate()` for the whole
repository. The rule this session follows is the one
`research/persistence.py` already set: a wave that does not own the schema
documents the DDL it expects and leaves the migration to the wave that
persists the rows.

The DDL therefore lives in `contract.py` as `CANONICAL_DDL`, and the test
suite applies that same constant to a temporary database, which pins the
contract. The reader asks `PRAGMA table_info` which columns a table has and
selects only those, so a table that lands with a different shape degrades the
projection instead of crashing it.

While the table is absent, the reader falls back to the pain points and the
evidence, and the six fields that only a scored opportunity can fill read
`not recorded`: willingness to pay, competition, counter-evidence, potential,
confidence and uncertainty. When the table is there, each field falls back to
the matched pain-point group only if the canonical row leaves it empty, so a
canonical value always wins.

### What a note says

The opportunity note carries the fifteen fields the mission names, in order:
the opportunity, the normalized problem, the evidence summary, the
independent evidence, the communities, the severity, the willingness to pay,
the competition, the counter-evidence, the potential, the confidence, the
trend, the uncertainty, the lifecycle, and the recommended next validation
experiment. A field the database does not hold reads `not recorded`, and the
frontmatter holds `null` for it. A missing value is never rendered as a
zero; the reviewer checked that and it held.

The evidence summary counts, and it does not conflate them:

```text
Mentions: 2
Canonical documents: 2
Independent authors: 2
Uncertain authors: 0
Independent communities: 1
Propagated copies: 0
Busiest source share: 1.00
```

The note then says the same thing in a sentence, because the numbers alone
do not say it: two mentions are not two people. The counts follow the
product spec's section 12: the mention count is the number of spans, the
canonical count is the number of distinct document content hashes, the
author count comes from `author_links` with the low-confidence links counted
as uncertain rather than independent, the community count comes from the
documents, and the propagated count comes from `propagation_edges`.

Every material claim names the evidence behind it. The normalized problem
line ends with the span ids, and each quote is rendered with the span it
came from.

### The vault

```text
Product-Hunter-Vault/
├── 00-Dashboard/          README, Top Opportunities, Recent Discoveries,
│                          Rising Opportunities, Falling Opportunities,
│                          Needs Human Review, Daily Briefs/README
├── 01-Ideas/              one folder per score band
├── 02-Research/          Sources, Communities, Markets, Trends
├── 03-Daily-Reports/     one note per day
├── 04-Experiments/       Active, Completed, Failed
├── 05-Research-Requests/
└── 99-Archive/           Already-Solved, Rejected, Insufficient-Evidence, Stale, Dead
```

The seven top-level folders are the ones the product spec fixes, and the
engine creates all seven on every run, so a vault built from an empty
database still has the shape the spec describes. The band folders follow the
spec's emoji names. There is one addition: `⚫ Unscored`. An opportunity with
no recorded potential cannot be filed under a score band it does not belong
to, and calling it "50-69 Weak Evidence" would be a lie in a file name.

A note's file name carries a slug of the title and eight hex characters of
the hash of the opportunity id, so two opportunities with the same title stay
apart and the id in the frontmatter stays stable when the title changes. The
archive folders are chosen by the lifecycle; every other lifecycle, including
`CANDIDATE`, stays in `01-Ideas`.

### Scraped text cannot act

Everything a page said is data. The sanitizer escapes the characters that
give text meaning inside a vault: `&`, `<` and `>` so no HTML tag and no
embedded script survives; the backtick so no code fence can be opened, and a
tilde fence is escaped as well; `[[` so a wikilink and an embed stay text;
`{{` and `}}` so a Templater expression is not evaluated; `%%` so a comment
and a Dataview inline block stay text; a line-leading `#`, `|`, `---` and
`= ` so scraped text cannot make a heading, a table row, a frontmatter
delimiter or a Dataview query. The angle brackets are escaped everywhere, so
a blockquote cannot open either. The line-start escapes fire after an
indentation of up to three spaces, because CommonMark still reads a heading,
a rule or a query there. A URL whose scheme is not `http` or `https` is
replaced with `blocked:<scheme>`, and a URL is written as inline code so a
reader cannot click straight from the vault into a hostile address.

A quote is rendered inside a fenced block tagged `text`, and the escaping
still runs, so the fence cannot be closed from inside.

The frontmatter is written with `json.dumps`, which is a valid YAML
double-quoted scalar, so a title holding a newline, a colon, a quote or a
line of `---` cannot break out of the frontmatter.

### The failure law

A projection that fails leaves the database alone, because the projection
never writes to it. The outbox handler lets the exception out, so
`process_outbox` records the failure and returns the task to `pending` with a
backoff, and the next attempt can succeed. A reviewer proved the whole
chain: the failure leaves the task pending with the error set, the retry
succeeds, and the task reaches `done`.

## What was found and fixed, in order

Every pass below returned a verdict, every accepted finding was fixed, and
every behavioural fix has a test that pins it. The prompts for each round are
in `docs/reasonix/prompts/`.

### 1. Security and observability pass, two findings

- A Dataview query introducer survived in scraped text. A line that begins
  with `= ` kept its introducer, and a Dataview-style plugin reads that
  shape as a query. Now escaped.
- A lone surrogate crashed the UTF-8 write. The exception escaped the
  projection, and through the outbox the task went back to pending, failed
  again on the same row, and never recovered: a poison pill. The surrogate
  is now dropped at the root, in the sanitizer and in the frontmatter
  writer, and the hash and the write use the same replacement, so the hash
  still describes the bytes on disk.

The re-pass came back clean, with the hostile probes re-run against the fixed
code: HTML, script tags, wikilinks, embeds, Templater, Dataview, callouts,
comments, headings, table rows, blockquotes, frontmatter delimiters, hostile
URL schemes, hostile link targets, control characters, path traversal and
frontmatter injection all hold.

### 2. Adversarial review, `block`, eleven findings

Three were must-fix:

- A `~~~` fence opened a code fence. The sanitizer escaped the backtick and
  never touched the tilde.
- The line-start escapes only fired at column 0, so `   # heading` and
  `   = this.file.name` survived.
- A manifest that parsed but held no `notes` dict silently disabled the
  prune, so the note of a deleted opportunity stayed in the vault for ever.

Five were should-fix: the prune could delete a hand-written note, because the
marker check was a substring search rather than a check of the `source` key;
the canonical reader did not fall back field by field; `_code` deleted a
backtick instead of keeping it inside the code span, so a note showed a URL
the database never captured; the daily brief had no competitors section and
put the day's new opportunities under "Top opportunities"; and the seven
top-level folders were never created, so a vault built from a thin database
had four of them. Two were nice-to-have: a missing value printed as `0.00`,
and two markets could share one file name.

All eleven were fixed, and the re-review confirmed each one with the same
script that had proved the original.

### 3. Final follow-ups, and a defect the implementer found

- The frontmatter parse in `_has_marker` caught `yaml.YAMLError` only, and a
  deeply nested frontmatter raises `RecursionError`, which escaped into
  `project()`. The notes were written before the prune, so nothing was
  corrupted, but the manifest was never written, so every later run failed
  the same way. Now caught.
- A manifest holding an empty `notes` object still left the prune silent.
- A value ending with a backtick merged with the closing delimiter of its
  code span, so the span did not form.
- The daily-briefs index from the spec's tree was never written.

While refactoring the daily brief, the implementer found a defect the
review had missed: the wikilinks were emitted with one closing bracket
(`[[path]`), so no link in the vault resolved. The fix closes the link, and
two tests now check that every wikilink in a built vault resolves to a note
on disk and that none points outside the vault.

### 4. Lean

The reviewer named about 85 lines that could go, mostly from nine repeated
blocks in the daily brief and from six render helpers that could be three.
The brief was refactored, with the body pinned before the change and proven
byte-identical after it over eight snapshot shapes. The two consolidations
that remove real duplication were taken. The rest of the render helpers were
left alone: a late refactor of code the reviewer had just proved correct
costs more than the lines it saves, and the duplicates are readable.

## Integration requirements

1. **Land the canonical DDL.** `contract.py` holds `CANONICAL_DDL`. A
   migration must create `opportunities`, `opportunity_scores`,
   `opportunity_evidence`, `opportunity_claims`, `experiments` and
   `research_requests`, with a number that does not collide with a sibling
   session's migration. The projection reads them when they exist and falls
   back when they do not, so the order of the merge does not matter.
2. **Reconcile the column map.** If the scoring session lands `opportunities`
   with different column names, change the map in `contract.py` and nothing
   else. The reader already tolerates a column that is not there.
3. **Feed the outbox.** The `obsidian` target is enqueued by the run import.
   A worker that drains the outbox with the handler from `engine.py` closes
   the loop. `ph project --outbox` does the same from the command line.
4. **Own the lifecycle writes.** The projection reads `human_decisions` for
   the lifecycle, and matches the decision against the opportunity id or one
   of its pain point ids. Whoever writes those rows decides the vocabulary;
   the reader maps the text onto the spec's list of states and treats
   anything it does not recognise as `CANDIDATE`.
5. **Point the vault at a real directory.** The default is
   `data/vault/Product-Hunter-Vault`, inside the gitignored `data/`. A
   deployment that keeps the vault elsewhere passes `--vault`.
6. **Decide what a human edit means.** A note a person edited is overwritten
   by the next projection, because the projection is one-way. The spec asks
   for that to be flagged rather than silently lost. This session reports
   the count of notes it wrote and the count it left alone, and the report
   line is the flag; a real marker needs a decision about where the
   human's copy lives, and that decision belongs to the two-way work.

## What was skipped, and what was not checked

Skipped on purpose:

- **Two-way synchronisation.** Out of scope by the mission, and out of scope
  by the spec until a later integration.
- **A migration and any new table.** Section "The read contract" says why.
- **The competitors and claims tables.** They are named in the spec, they
  are not in the contract, and a field with no table behind it reads
  `not recorded`.
- **A note-size cap and a `shortcut:` marker.** A quote of a million
  characters renders, and the layer is read-only, so an attacker cannot
  inject size through it. If a later session lets scraped text reach the
  projection at scale, both want revisiting.

Not checked:

- **Obsidian itself.** The vault was never opened in the app. The link
  resolution, the code spans and the escaping were checked against the
  CommonMark rules and against a test that walks every wikilink in a built
  vault, not against the renderer. A vault with three `README.md` files
  relies on Obsidian preferring the full path in the link; every target was
  confirmed to resolve to exactly one file, but the app was not run.
- **The real shape of the `opportunities` table.** It does not exist in this
  branch. The canonical path was exercised against `CANONICAL_DDL` and
  against a deliberately wrong shape, not against the table a sibling
  session will land.
- **A ten thousand opportunity vault.** The load numbers are from three
  hundred. The drain was O(tasks x vault) until the review found it and the
  fix made it one build per drain, which is O(vault), but the large number
  was not measured.
- **The Dataview plugin's own parser.** The `= ` introducer is escaped
  because the spec forbids plugin syntax from scraped text, not because a
  running Dataview was observed to evaluate it.
- **The tab case in the sanitizer.** A tab-indented marker is not escaped,
  and is inert only because a tab expands to the next multiple-of-four
  column, so the marker lands in an indented code block.

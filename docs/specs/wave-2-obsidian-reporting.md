# Wave 2 Session 8: Reporting and Obsidian Projection

Status: DEFINE
Branch: `wave2/obsidian-reporting`
Contract version: `projection-v1`

## 1. What this session delivers

A read-only projection layer that turns authoritative SQLite state into
human-readable Markdown notes in an Obsidian vault.

SQLite stays the source of truth. The vault is a projection that can be
deleted and rebuilt at any time. Nothing in the vault is ever read back into
the database.

The session delivers:

- a `product_hunter.projection` package: read contract, models, sanitizer,
  renderers, vault layout, projection engine;
- a `ph project` command that builds or refreshes the vault, and an
  `--outbox` mode that drains the transactional outbox instead;
- a handler for the `obsidian` projection target that `db/store.py` already
  enqueues but nothing handles today;
- tests covering determinism, idempotency, deletion, malformed input,
  malicious input, duplicate tasks, and retry after failure;
- a report at `docs/reasonix/reports/wave-2-obsidian-reporting.md`.

## 2. Non-goals

Two-way synchronisation is out of scope. A human editing a generated note must
not change database state, and a note edited by a human must not be
overwritten by the next projection without being flagged. Neither direction
exists in this session.

The session also lands no migration, owns no table, and adds no dependency.
Section 4 explains why.

## 3. The vault

```
Product-Hunter-Vault/
├── 00-Dashboard/
│   ├── README.md
│   ├── Top Opportunities.md
│   ├── Recent Discoveries.md
│   ├── Rising Opportunities.md
│   ├── Falling Opportunities.md
│   ├── Needs Human Review.md
│   └── Daily Briefs/README.md
├── 01-Ideas/
│   ├── 🔥 90-100 Exceptional/
│   ├── 🟢 80-89 Strong/
│   ├── 🟡 70-79 Interesting/
│   ├── ⚪ 50-69 Weak Evidence/
│   ├── 🔴 00-49 Low Potential/
│   └── ⚫ Unscored/
├── 02-Research/
│   ├── Sources/<source_id>.md
│   ├── Communities/<community_id>.md
│   ├── Markets/<market>.md
│   └── Trends/README.md
├── 03-Daily-Reports/<YYYY-MM-DD>.md
├── 04-Experiments/
│   ├── Active/<experiment_id>.md
│   ├── Completed/<experiment_id>.md
│   └── Failed/<experiment_id>.md
├── 05-Research-Requests/<request_id>.md
└── 99-Archive/
    ├── Already-Solved/<note>.md
    ├── Rejected/<note>.md
    ├── Insufficient-Evidence/<note>.md
    ├── Stale/<note>.md
    └── Dead/<note>.md
```

The seven top-level folders are fixed by the product spec. `⚫ Unscored/` is
the one addition: an opportunity with no recorded potential cannot be filed
under a score band it does not belong to, and calling an unscored opportunity
"50-69 Weak Evidence" would be a lie in the file name.

The vault lives at `data/vault/Product-Hunter-Vault` by default. `data/` is
already in `.gitignore`, so generated notes never reach git.

## 4. The read contract

The projection reads the authoritative database and writes nothing back. It
needs a stable set of tables to read from, and three of them do not exist yet.

### 4.1 Tables that exist today

| Table | What the projection takes from it |
| --- | --- |
| `pain_points` | the problem statement, the normalized fields, severity, frequency, the grouping key |
| `evidence` | the quote, the span, the match mode, the capture time |
| `evidence_pain_links` | which evidence supports which pain point |
| `documents` | the source URL, the title, the author, the capture time, the content hash |
| `communities` | the community a document belongs to |
| `authors`, `author_links` | who said it, and how strongly the identity is known |
| `propagation_edges` | which mentions are copies of one another |
| `sources` | the source type and the terms status |
| `human_decisions` | the recorded lifecycle of an opportunity |
| `research_runs`, `research_artifacts`, `research_events` | research reports and queued research requests |
| `projection_tasks` | the outbox the projection is driven from |

### 4.2 Tables that do not exist yet

The product spec names `opportunities`, `scores`, `score_snapshots`, `claims`,
`claim_evidence`, `competitors`, `experiments` and `research_requests`. The
first five are the scoring session's to build, and `experiments` and
`research_requests` have no owner yet.

This session does not create them. `db/migrations.py` rejects a duplicate
migration number and refuses a gap in the numbering, so a migration added
here would collide with whichever sibling session picks `0003` next and would
break `migrate()` for the whole repository. The rule is followed here by
`research/persistence.py`, which documents its planned DDL in the module
docstring and leaves the migration to the wave that persists the rows.

The DDL the projection expects therefore lives in
`product_hunter/projection/contract.py` as `CANONICAL_DDL`, and the wave
integrator can land it verbatim. The test suite applies that same constant to
a temporary database, which pins the contract.

```sql
CREATE TABLE opportunities (
    opportunity_id         TEXT PRIMARY KEY NOT NULL,
    title                  TEXT NOT NULL,
    normalized_problem     TEXT,
    domain                 TEXT,
    actor                  TEXT,
    action                 TEXT,
    object                 TEXT,
    consequence            TEXT,
    severity               REAL,
    frequency              TEXT,
    wtp_tier               TEXT,
    competition            REAL,
    potential              REAL,
    confidence             REAL,
    trend                  TEXT,
    uncertainty            TEXT,
    lifecycle              TEXT,
    recommended_experiment TEXT,
    first_seen             TEXT,
    last_updated           TEXT,
    created_at             TEXT NOT NULL,
    updated_at             TEXT NOT NULL
);

CREATE TABLE opportunity_scores (
    score_id       TEXT PRIMARY KEY NOT NULL,
    opportunity_id TEXT NOT NULL,
    potential      REAL,
    confidence     REAL,
    created_at     TEXT NOT NULL
);

CREATE TABLE opportunity_evidence (
    opportunity_id TEXT NOT NULL,
    span_id        TEXT NOT NULL,
    stance         TEXT NOT NULL,
    created_at     TEXT NOT NULL,
    PRIMARY KEY (opportunity_id, span_id, stance)
);

CREATE TABLE opportunity_claims (
    claim_id       TEXT PRIMARY KEY NOT NULL,
    opportunity_id TEXT NOT NULL,
    statement      TEXT NOT NULL,
    kind           TEXT,
    span_id        TEXT,
    created_at     TEXT NOT NULL
);

CREATE TABLE experiments (
    experiment_id  TEXT PRIMARY KEY NOT NULL,
    opportunity_id TEXT,
    title          TEXT NOT NULL,
    hypothesis     TEXT,
    status         TEXT NOT NULL,
    started_at     TEXT,
    completed_at   TEXT,
    result         TEXT
);

CREATE TABLE research_requests (
    request_id TEXT PRIMARY KEY NOT NULL,
    title      TEXT NOT NULL,
    body       TEXT,
    status     TEXT NOT NULL,
    created_at TEXT NOT NULL
);
```

### 4.3 Two ways to read an opportunity

`opportunities` rows are the preferred source. When the table is absent, or
when a row leaves a field empty, the projection falls back to what the
existing tables can prove:

| Field | Canonical source | Fallback |
| --- | --- | --- |
| identity | `opportunities.opportunity_id` | `opp_` + first 16 hex of sha256 of the grouping key |
| title | `opportunities.title` | the pain statement of the group |
| normalized problem | the normalized columns | the pain point's domain, actor, action, object and consequence |
| severity | `opportunities.severity` | the highest severity in the group |
| evidence | `opportunity_evidence` | `evidence_pain_links` |
| independent evidence | counted from the linked documents | counted from the linked documents |
| communities | counted from the linked documents | counted from the linked documents |
| lifecycle | `opportunities.lifecycle` | the latest `human_decisions` row for the opportunity or for one of its pain points |
| potential, confidence, trend | `opportunities` and `opportunity_scores` | not derivable; reported as not recorded |
| WTP tier, competition, counter-evidence, uncertainty, recommended experiment | `opportunities` | not derivable; reported as not recorded |

The grouping key is `pain_points.normalized_key` when it is set, and the
pain point id otherwise. Two pain points that normalize to the same key are
one opportunity.

"Reported as not recorded" means the note prints the field name and the words
`not recorded`, never a zero and never an estimate. A reader who sees a
missing potential must be able to tell it apart from a potential of zero.

### 4.4 Column tolerance

The reader asks `PRAGMA table_info` for the tables it wants and selects only
the columns that exist. A sibling session that lands `opportunities` with a
different shape degrades the projection instead of crashing it. The mapping
lives in one file, `contract.py`, so reconciling with a sibling is a change
in one place.

## 5. The notes

Twelve note kinds. Each one names the file it produces.

| Kind | File |
| --- | --- |
| Opportunity note | `01-Ideas/<band>/<slug>--<hash8>.md` |
| Evidence reference | `02-Research/Sources/<source_id>.md`, `02-Research/Communities/<community_id>.md` |
| Research report | `02-Research/Markets/<market>.md`, `02-Research/Trends/README.md` |
| Daily brief | `03-Daily-Reports/<YYYY-MM-DD>.md` |
| Top opportunities | `00-Dashboard/Top Opportunities.md` |
| Recent discoveries | `00-Dashboard/Recent Discoveries.md` |
| Rising opportunities | `00-Dashboard/Rising Opportunities.md` |
| Falling opportunities | `00-Dashboard/Falling Opportunities.md` |
| Human-review queue | `00-Dashboard/Needs Human Review.md` |
| Experiment | `04-Experiments/<Active\|Completed\|Failed>/<experiment_id>.md` |
| Research request | `05-Research-Requests/<request_id>.md` |
| Archive | `99-Archive/<reason>/<slug>--<hash8>.md` |

The file name carries a slug of the title and eight hex characters of the
hash of the opportunity id. The slug keeps the vault readable, the hash keeps
two opportunities with the same title apart, and the id in the frontmatter
keeps the identity stable when the title changes.

Every note starts with frontmatter:

```yaml
---
id: opp_1a2b3c4d5e6f7a8b
kind: opportunity
contract: projection-v1
content_hash: 3f0c...  # sha256 of the body, 64 hex characters
status: PROMISING
potential: 91
confidence: 0.87
first_seen: 2026-10-18
last_updated: 2026-10-21
---
```

String values are written as double-quoted YAML scalars with the escapes the
format requires. A title that contains a newline, a colon or a line of
`---` cannot break out of the frontmatter, because the value stays on one
line inside quotes.

## 6. The opportunity report

The opportunity note carries every field the mission names, in this order.

1. **Opportunity** — the title and the one-line statement of the problem.
2. **Normalized problem** — domain, actor, action, object, consequence.
3. **Evidence summary** — mentions, canonical documents, independent authors,
   uncertain authors, independent communities, propagated copies, and the
   share of mentions that comes from the busiest source.
4. **Independent evidence** — the same counts, stated separately, because
   147 mentions and 147 people are different claims.
5. **Communities** — the named communities the evidence comes from.
6. **Severity** — the severity figure, or not recorded.
7. **WTP** — the willingness-to-pay tier, or not recorded.
8. **Competition** — the competition figure, or not recorded.
9. **Counter-evidence** — the quotes that argue against the thesis, each
   linked to its span, or not recorded.
10. **Potential** — the potential figure, or not recorded.
11. **Confidence** — the confidence figure, or not recorded.
12. **Trend** — rising, falling, flat, or not recorded.
13. **Uncertainty** — the stated uncertainty, or not recorded.
14. **Lifecycle** — the opportunity state from the spec's list.
15. **Recommended next validation experiment** — the experiment, or not
    recorded.

Each material claim names the evidence it rests on. The normalized problem
line ends with the span ids behind it, and the counter-evidence section links
each quote to its span. A claim with no evidence behind it does not appear as
a fact.

Independence is counted, never estimated. The counts are:

- **mentions**: the number of evidence spans linked to the opportunity.
- **canonical documents**: the number of distinct document content hashes.
- **independent authors**: the number of distinct authors behind those
  documents.
- **uncertain authors**: authors whose identity link carries low confidence.
- **independent communities**: the number of distinct communities.
- **propagated copies**: spans whose document is a propagation target in
  `propagation_edges`.

## 7. The dashboard, the brief and the queue

**Top opportunities** lists the opportunities with the highest potential, and
falls back to severity when potential is not recorded. The table shows rank,
title, potential, confidence, evidence count and the link to the note.

**Recent discoveries** lists the opportunities first seen within seven days
of the reference date.

**Rising and falling** compare the newest score in `opportunity_scores` with
the one before it. With no score history the two notes say so and list
nothing.

**Needs human review** lists the opportunities that need a person, and says
why each one is queued. An opportunity is queued when its recorded lifecycle
is `HUMAN_REVIEW`, when it carries counter-evidence, or when it has fewer
than two independent authors and no human decision has been recorded yet.

**The daily brief** covers one day and carries the top opportunities, the new
evidence, the opportunities moving up and down, the rejected ideas, the new
competitors, the major counter-evidence, the recommended validation
experiments, and the notable trends. A brief is written for every day that
has activity, and the date comes from the data rather than from the clock.

**The research report** for a market is the set of opportunities and evidence
under one `domain`, with the source and community spread. **Trends** is an
index of the movements the score history shows.

**Research requests** are the runs that are queued and waiting. An
experiment note carries the hypothesis, the status and the result.

## 8. Untrusted content

Scraped text is data, not markup. Every string that came from a page, a
quote, a community name or a source title passes through the sanitizer
before it reaches a note.

The sanitizer:

- escapes `&`, `<` and `>`, so no HTML tag and no embedded script survives;
- escapes the backtick, so scraped text cannot open a code fence and cannot
  carry a `dataview` or `dataviewjs` block;
- escapes `[` and `]` at the start of a wikilink, so `[[note]]` and
  `![[embed.png]]` stay text;
- escapes `{{` and `}}`, so a Templater expression is not evaluated;
- escapes `%%`, so a comment or a Dataview inline block stays text;
- escapes a leading `#`, so scraped text cannot make a heading;
- escapes a leading `|`, so scraped text cannot break a table;
- escapes a leading `>`, so scraped text cannot open a blockquote;
- escapes a leading `---`, so scraped text cannot close the frontmatter;
- replaces the scheme of a URL that is not `http` or `https` with
  `blocked:`, so a `javascript:`, `data:`, `file:` or `vbscript:` URL cannot
  be followed.

A URL is written as inline code, not as a link, so a reader cannot click
straight from the vault into a hostile address. The same escaping applies to
the frontmatter values.

Quotes are rendered inside a fenced block tagged `text`. The escaping above
still runs, so the fence cannot be closed from inside, and the fence itself
keeps the quote from being read as markup at all.

Nothing a page says can reach a shell, a plugin action or the database. The
projection only writes files under the vault root.

## 9. Determinism, idempotency, regeneration

**Deterministic.** Two runs over the same database state produce the same
bytes. No wall-clock time reaches a note: the reference date is the newest
timestamp in the data unless the caller passes one. Every list is sorted by
an explicit key. Every number is written at a fixed precision. The content
hash is the sha256 of the body.

**Idempotent.** A note is written only when the bytes on disk differ, so a
second run over unchanged state touches nothing and leaves the file times
alone. Running the projection twice is the same as running it once.

**Regenerable.** The vault can be deleted and rebuilt. The engine keeps a
manifest at the vault root, `.product-hunter-projection.json`, that maps each
written file to its id and hash. A note that is in the manifest and no longer
in the snapshot is removed, which is what happens when an opportunity is
deleted. When the manifest is missing, the engine finds its own notes by the
`source: product-hunter-projection` marker in the frontmatter and removes
the ones that are no longer in the snapshot.

**A failure leaves the database alone.** The projection opens the database
read-only and never writes to it. A write that fails leaves the previous
note on disk, and the outbox task goes back to pending with a backoff, so
the next worker run tries again.

## 10. The outbox

`db/store.py` already enqueues a projection task with the target `obsidian`
when a run is imported, and `process_outbox` fails any task whose target has
no handler. This session registers the handler.

The handler ignores the task payload beyond the run id and rebuilds the
vault, because the vault is a projection of the whole state and a partial
rebuild would be wrong. Rebuilding is cheap and idempotent, so the same
handler serves both the outbox path and the direct command.

`ph project` builds the vault straight away. `ph project --outbox` claims
and processes the pending `obsidian` tasks through `process_outbox`, so a
failure is recorded on the task and retried with the backoff the outbox
already implements.

## 11. The command

```
ph project [--db PATH] [--vault PATH] [--outbox] [--as-of YYYY-MM-DD]
```

It prints a JSON summary: the notes written, the notes left alone, the notes
removed, and the vault path. The exit code is 0 on success and 1 on failure.

## 12. The tests

| Test | What it proves |
| --- | --- |
| deterministic rendering | the same snapshot renders to the same bytes twice |
| idempotent projection | a second run writes nothing and leaves the file times alone |
| changed opportunity | a changed field changes the note and the hash |
| deleted opportunity | the note of a removed opportunity is deleted from the vault |
| malformed source text | a quote that is not valid UTF-8, or holds a control character, is handled |
| malicious markdown | a wikilink, an embed, a Templater expression and a callout in a quote stay inert |
| HTML | a script tag in a quote is escaped |
| duplicate projection tasks | two tasks for the same opportunity leave one note |
| failed projection and retry | a failed write leaves the task pending and the next run succeeds |
| stable filenames and ids | the same opportunity always lands on the same path, and a title change keeps the id |

The suite also checks that the sanitizer leaves ordinary prose alone, that a
frontmatter value cannot break out of the frontmatter, and that a URL with a
hostile scheme is blocked.

## 13. Acceptance criteria

1. `ph project` builds the vault from a database that holds evidence, and the
   vault holds the twelve note kinds with the seven top-level folders.
2. Running `ph project` twice changes no file and changes no file time.
3. A note for a deleted opportunity is gone after the next run.
4. No note contains an unescaped HTML tag, a wikilink, a code fence, a
   Templater expression or a non-`http` URL that came from scraped text.
5. Every opportunity note carries the fifteen fields of section 6, with
   `not recorded` in place of any field the database does not hold.
6. The outbox handler completes the `obsidian` tasks that
   `db/store.py` enqueues, and a failing projection leaves the task pending.
7. The layer reads the database and never writes to it.
8. The tests of section 12 pass under `uv run pytest`.

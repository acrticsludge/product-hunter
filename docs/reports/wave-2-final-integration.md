# Wave 2 final integration report

**Run ID:** wave-2-final-integration-2026-10-22
**Branch:** `main`
**Integration branch:** `integration/wave2`
**Base:** `59e29fa` (the Wave 1 integration, `984c278`)
**Fork point of the eight session branches:** `3d7bc68`
**Risk:** declared `high`, floor `medium`. The run touches `db/migrations/**`
and `src/product_hunter/fetch.py`, both adapter high-risk paths, so stages 2, 6
and 7 were not skippable.
**Adapter:** Product Hunter, 8 forbidden rules in force
**Spec:** [../specs/wave-2-final-integration.md](../specs/wave-2-final-integration.md)
**Plan:** [../plans/wave-2-final-integration.md](../plans/wave-2-final-integration.md)

```text
GOAL: reconcile eight parallel Wave 2 branches into one coherent, tested,
      secure tree on main, and leave the repository in a clean state
TIER: free (models used per node in order)
  stage 0-3   orchestrator: longcat-2.5-preview-free
  stage 5     verify-code: the first two dispatches failed at the provider,
              the third ran and observed the gate
  stage 6     harden: step-5-preview-free
  stage 7     review: step-5-preview-free, verdict fix-then-ship, every
              finding closed
  stage 8-10  orchestrator: longcat-2.5-preview-free
RISK: declared=high, adapter floor=medium (schema, fetch path)
ADAPTER: Product Hunter [repo] 8 forbidden rules in force
STAGES: 11/11 complete, skipped: none. Stage 5b visual was not required; the
  adapter sets visualRequired to never and no UI file is in the radius.
GATES: uv run pytest -> 2138 passed, exit 0
CHANGES: 202 files, 58259 insertions, 273 deletions, one integration commit
HARDEN: two findings, one fixed and one corrected. The collection package no
  longer logs an untrusted URL. The extraction prompt finding was wrong and
  says so.
REVIEW: fix-then-ship, twice. Round 1: 2 Must-fix, 6 Should-fix, 4 Nice-to-have.
  Round 2: 1 Must-fix, and the load limits put in numbers. All closed.
  Lean: -65 lines possible, 23 taken.
WAIVERS: none
STOPPED BECAUSE: all pass and review ship
NEXT: the wave 2 stage handler is the next wave's work
```

## 1. The eight branches, inspected before anything was merged

Every branch was read before it was merged: its commit history, its changed
files, its diff against the base, its session report, its tests, and its
imports. The map that came out of that inspection is in the spec.

| Branch | Head | Delivers | Migration claimed |
|---|---|---|---|
| `wave2/evidence-pipeline` | `2c9eb8f` | The evidence chain: document versions, span observations, lineage, derived strength and reliability, and three triggers that make a pain point require a verified evidence span | `0003_evidence_pipeline.sql` |
| `wave2/discovery-engine` | `cc4cf7e` | The discovery engine and the query frontier: eight query families, controlled variants, novelty and saturation, discovery budgets | none |
| `wave2/collection-engine` | `11724d7` | The collection layer: the collector ladder, the per-source run state, the attempt ledger, a request budget inside `fetch.py` | `0003_collection.sql` |
| `wave2/clustering` | `e425e72` | Problem clustering: normalisation, similarity, candidate generation, assignment, merge and split, cluster lineage and versions | `0003_clustering.sql` |
| `wave2/independence` | `51e7dec` | The independence graph: author and community identity, propagation detection, deterministic analytics, concentration checks | `0003_independence.sql` |
| `wave2/scoring-validation` | `2e47038` | Opportunity scoring: Potential and Confidence on separate scales, willingness-to-pay tiers, market ranges, lifecycle transitions, content-addressed snapshots | none |
| `wave2/investigation` | `da5f201` | The investigation engine: competitor search, counter-evidence, coverage, budgets, checkpoints, the startup-killer pass, a bridge into the Wave 1 gate | none |
| `wave2/obsidian-reporting` | `b40144a` | Reporting and the Obsidian projection: sanitising, deterministic rendering, the vault layout, an outbox handler, the `ph project` command | none |

The eight packages are disjoint. No package and no module name was
implemented twice across branches, so nothing had to be deleted for
duplication. One duplication exists inside a branch: the independence package
carries five copies of a `_hash_id` helper and two copies of a `_pairs`
helper. That is a lean finding, not a merge decision, and it is in section 12.

## 2. The shape of the conflict

Seven of the eight branches fork from `3d7bc68`, the commit before the Wave 1
integration. The eighth had already been merged against `main`. That single
fact decided most of the work: merging a branch written against the old tree
onto a tree that had moved is a reconciliation, not an addition.

The overlap was smaller than the branch count suggested:

| File | Touched by | Conflict |
|---|---|---|
| `src/product_hunter/db/__init__.py` | evidence, independence | Two sets of public re-exports. Resolved by keeping both. |
| `tests/test_db_migrations.py` | evidence, collection, clustering, independence | Each branch moved a version assertion and added a case for its own migration. |
| `docs/reasonix/ARCHITECTURE.md` | seven branches | Each branch described its own subsystem in the one document. |
| `src/product_hunter/db/migrations.py` | evidence, and Wave 1 | The statement splitter. |
| `src/product_hunter/db/store.py` | evidence, and Wave 1 | The document-version import path against the outbox. |
| `src/product_hunter/research/__init__.py` | investigation, and Wave 1 | The gate re-exports against the investigation re-exports. |
| `src/product_hunter/cli.py` | obsidian, and Wave 1 | `ph db status` against `ph project`. |
| `src/product_hunter/fetch.py` | collection alone | The request budget. |
| `src/product_hunter/config.py` | clustering alone | The clustering configuration loader. |
| `src/product_hunter/research/evidence.py` | investigation alone | Twelve `ClaimKind` members. |

Nothing else was touched by more than one branch.

## 3. The migration number

Five files claimed `0003`: the Wave 1 research gate on `main`, and the
collection, evidence, clustering and independence migrations. The loader
refuses a duplicate number and refuses a gap, so an unreconciled merge fails
at `db init` rather than silently. The Wave 1 report had already recorded
this as a known break, and named two of the four claimants.

A SubQ Snap decision at stage 1 chose the pipeline order of spec §10 at 0.91
confidence:

```
0001 initial
0002 model_routing
0003 research_gate          (Wave 1, unchanged)
0004 collection             (renamed from 0003_collection.sql)
0005 evidence_pipeline      (renamed from 0003_evidence_pipeline.sql)
0006 clustering             (renamed from 0003_clustering.sql)
0007 independence           (renamed from 0003_independence.sql)
```

No foreign key between the four points at a table another of the four
creates. Every reference resolves into `0001`, so the order is a readability
choice and not a constraint.

The renumbering was done as each branch landed rather than at the end,
because a gap makes `migrate()` refuse to run and the tree would not have
been runnable between two merges. That changed the merge order: the evidence
branch had to move behind the collection branch, so collection took `0004`
and evidence took `0005`. The plan records the change and the reason.

## 4. The duplicated column

The evidence migration and the independence migration both add
`documents.live_status TEXT NOT NULL DEFAULT 'live'`. The definitions are
identical. One of them owns it: the evidence migration, because it is the
migration that models the document lifecycle. The `ALTER TABLE` line is out
of the independence migration, and a comment there says which migration owns
the column and why.

## 5. The three conflicts that were not textual

**The evidence triggers refused two projection fixtures.**
`0005_evidence_pipeline.sql` installs a trigger that refuses an
`evidence_pain_links` row whose pain point is backed by another span. Two
projection tests linked a seeded pain point to a span that did not back it.
The trigger is the rule the adapter asks for, so the fixtures were changed:
each now carries a pain point of its own, backed by its own span. The
assertions under test, about invalid UTF-8 and control characters in a
quote, are unchanged.

**`ph db status` reported 28 tables when the schema held 45.**
The command carried a written-down list of tables. The Wave 2 migrations
added 17 more and the command kept reporting the old 28. The list is read
from the database now, and a table name that is not a plain identifier is
skipped because the name goes into the count query. The command had no
test; there is one now, and it names the Wave 2 tables.

**The migration runner cut a statement on a semicolon.**
The runner split the SQL on `;`, so a trigger body was cut in half and a
semicolon inside a string literal ended a statement early. The evidence
migration needs triggers, so the runner now splits where
`sqlite3.complete_statement` says a statement ends. The buffer is joined once
per semicolon and the join is used for both the check and the statement, so
the cost is the semicolons of the file times the length of a statement. A
migration file holds tens of semicolons, so this is nothing.

## 6. The evidence chain after the integration

```text
raw bytes  ->  data/raw/<source_id>/<sha256><ext>     (RawStore, atomic write)
            ->  FetchRecord                          (data/raw/fetches.jsonl)
            ->  Document                             (text_sha256, parser_version, content_version)
            ->  DocumentChunk
            ->  evidence span                        (quote, char range, match_mode)
            ->  pain_points row                      (accepted = true)
            ->  evidence_pain_links row              (the span that backs the pain point)
            ->  cluster_members row                  (valid_from, valid_to, never deleted)
            ->  opportunity score snapshot           (content-addressed, names its inputs)
```

What the Wave 2 packages added to it:

A document carries a content version, so a re-capture of the same URL is a
new version of the same document rather than a conflict, and the version it
leaves behind stays in `document_versions`.

A span carries an observation history. The same span seen again in another
version of the document is another observation, written with the extractor,
the model, the prompt version and the quote that was seen.
`evidence_lineage` records every lifecycle event.

The derived facts carry the version of the rule that produced them, so a
later rule cannot silently rewrite an old row.

The database enforces the weakest link. Three triggers make a pain point
impossible without a verified span, and make an evidence link impossible when
the pain point is backed by another span. The check is in the schema, so it
holds for every writer.

The clustering layer keeps the history. A membership that closes is closed
with a `valid_to` and never deleted, a version row holds one snapshot of a
cluster, and a lineage edge points from a merged or split cluster to its
successor.

The opportunity layer makes a score reproducible. A snapshot is
content-addressed, names the policy version it was computed under, and names
the input snapshot ids it was computed from.

The independence layer gives an author an identity. An author carries an
identity key, an identity confidence, the basis of that confidence, a
deletion status and a first and last seen date. The identity key is a hash.
No raw handle is stored in it.

## 7. Discovery and collection

The distinction the specification draws is kept. Discovery owns what to
search: the ten query families of spec §7A, the controlled variants, the
frontier with its novelty and saturation bookkeeping, and the discovery
budgets. Collection owns how to fetch: the collector ladder, the per-source
run state, the attempt ledger, and the request budget.

The states that must not be confused are separated by construction.
`collection_items` holds one row per attempt, so a failure is not erased by
the retry that follows it. The reason vocabulary separates a terminal refusal
(`robots_blocked`, `terms_not_permitted`, `unsafe_url`, `collector_unavailable`)
from a failure (`http_error`, `network_error`) and from a refusal that
produced no capture. `TERMINAL_REASONS` decides terminality by the reason and
not by the outcome, because an HTTP error may clear and a robots refusal will
not.

The discovery frontier records a parent query, the family, the novelty yield,
the duplicate yield, the source yield, the independent-author yield and the
last-seen time, and it stops on the budget or the saturation rule rather than
on a fixed number of results.

## 8. Independence

Independence is computed by code. The identity resolver resolves authors and
communities, the propagation detector finds reposts, quotes, cross-posts and
syndication, and the analytics layer counts raw mentions, canonical
observations, independent authors, uncertain authors, independent
communities, propagation-adjusted evidence and source concentration.

A model may supply a signal. The policy refuses a configuration that would
let a weak or model-supplied signal reach a merge threshold, so the count is
not declared by a model. The propagation-adjusted count and the concentration
checks are what stop a large body of evidence from one community, one author
cluster or one propagation chain from satisfying an independent-evidence
threshold.

Two limits remain. Nothing writes a `propagation_edges` row, so the table
holds the columns the analysis needs and no data. The identity key is an
unsalted hash, and the resolver is quadratic in the number of authors.

## 9. Scoring, investigation and the research contract

Potential and Confidence stay separate. Potential considers severity,
frequency, the willingness-to-pay tier, the reachable market, the competitive
gap, buildability and recurrence. Confidence considers independent authors
discounted for identity uncertainty, community diversity, the
source-reliability mix, temporal persistence, the propagation-adjusted count,
extraction agreement and the counter-evidence coverage. The willingness-to-pay
tiers are `W0` to `W4`, and a niche override is recorded with its reason. The
market is a range with its assumptions, not a number.

The investigation engine researches an opportunity rather than only scoring
it. Competitor and counter-evidence query families come from code-side
templates. A zero result is only reachable on a complete coverage: the
counter-evidence result refuses `none_found` unless the coverage is complete,
and an incomplete result carries no items. A search that did not run reads as
incomplete, not as "no competitor exists".

The research contract is enforced by code. The gate refuses a stage whose
tool never ran, an artifact that is missing or invalid, a stage that did not
search every mandatory family, and a budget overrun measured from the stored
rows. `require_promotable()` raises `GateBlocked`. The verdict carries the
fingerprint of the contract it was judged under.

What the gate still cannot do is matter: nothing calls it. The Wave 2
investigation engine bridges into it through `investigation_stage_execution()`,
and the discovery store writes a discovery record as a tool-call-shaped row.
Neither is called by a worker.

## 10. Model routing

No model is named in the business logic. All thirteen roles of spec §3 exist
as `ModelRole` members and as configured chains in
`config/model_routing.yaml`. The router resolves a role against the
catalogue OpenCode discovers at runtime and falls through the chain at call
time, recording the provider, the model, the catalogue version and the
fallback index.

Only the `extraction` role is bound to a live call path. The Wave 2 engines
take a resolved model identity as an input and record it, which is the seam a
caller resolves the role through: the clustering adjudicator writes
`provider`, `model`, `model_version` and `prompt_version` into every
decision row, and the similarity layer derives the vector space id from the
embedding model's identity.

One documentation claim was corrected. The clustering session report says the
adjudication model comes from `ModelRole.clustering`. No Wave 2 package
imports `ModelRole`; the package takes the identity as a parameter. The seam
exists and the report overstated it.

No `DEEPSEEK_API_KEY` was added. The repository holds no provider secret, and
the model that wrote these sessions is not the product's runtime model.

## 11. The Obsidian projection

SQLite stays authoritative. The vault is a projection, and it is a
projection in the strict sense: the layer is read-only against SQLite,
writes only under the vault root, and a note a human edited is overwritten by
the next projection without a flag saying so. Two-way synchronisation is not
built, and it is not pulled in.

The projection is deterministic, idempotent and regenerable. No wall-clock
time reaches a note, the reference date is the newest timestamp in the data,
a note is written only when its bytes differ, and the vault can be deleted
and rebuilt. `ph project` on an empty store writes 8 notes and the second
run reports 8 unchanged and 0 written.

Scraped text is data. The sanitiser escapes HTML, wikilinks, embeds,
Templater expressions, Dataview queries, comments, headings, table rows,
blockquotes, code fences and frontmatter delimiters, and blocks every URL
whose scheme is not `http` or `https`. A frontmatter value goes through
`json.dumps`, so a quote or a newline cannot break out of the YAML scalar. A
value the database does not hold reads `not recorded`, never a zero.

`ph project --outbox` drains the outbox through the projection's handler,
which closes the Wave 1 gap that no outbox handler existed.

## 12. Review

The review node read the diff against `main` and the connected code, and
tried to prove the change wrong across six passes: correct, safe, load,
tested, fast, lean. Its verdict was **fix-then-ship**. It found two Must-fix
defects, six Should-fix defects and four Nice-to-have findings. Every one of
them is closed, and the two that mattered most are worth naming here.

**The document and its spans could disagree about liveness.** The re-capture
branch of `import_document_version` wrote the status straight onto the
document row, while the same-text branch called `set_document_status`, which
writes the document and every span of it. A re-capture of a document that had
been closed since the last capture therefore left the document `deleted` and
its spans `live`. The independent-evidence count filters on
`e.live_status = 'live'`, so a deleted document's evidence still counted as
independent evidence and inflated the confidence band of a pain point. The
re-capture branch now calls `set_document_status` as well. The regression
test was checked by mutation: with the fix reverted, the span stays `live`
and the test fails.

**The extraction "security fix" was a fix for a bug that never existed.**
The hardening pass reported that a brace in a scraped page raised a
`KeyError` or a `ValueError` inside the extraction prompt and stopped the
run. That is wrong, and the review node was right to challenge it:
`str.format` does not re-parse a substituted value, so the pre-fix template
never raised on a brace in the page text. The change was kept, because the
template no longer has to carry doubled braces and a stray brace in it is
written out as it is, but it was relabelled from a security fix to a
maintainability change. The two tests that came with it asserted nothing the
old code did not do, so they were rewritten, and the rewritten ones were
checked by mutation: with the format-string build put back, all three fail.

The six Should-fix findings and the four Nice-to-have findings were closed
as follows.

| Finding | State |
|---|---|
| The checkpoint failure event dropped the exception text, and a caller that passes no sink hears nothing | The event carries a bounded, redacted detail with the target URL taken out of it, and the silence of the default is now a labelled `shortcut:` marker |
| The URL assertion in the checkpoint test compared whole values, so a URL inside another value would pass | It compares every value as a string |
| The report's section 12 was a self-referential stub | This section |
| `documents.deleted_at` had no writer, and the independence analysis read it | `set_document_status` writes it, so the column holds the instant a document left the live set |
| The report claimed the migration splitter was not quadratic, and it is | The claim is corrected above, and the code joins the buffer once per semicolon instead of twice |
| A comment in `fetch.py` still spoke of a log the collection layer no longer writes | Corrected |
| The clustering engine computed every cosine twice | The candidate pass hands its member similarities to the scorer, so each pair is compared once |
| `cli.py` had two import statements for one module | Merged |
| `_strip_comment_lines` drops a line that starts with `--`, including one inside a trigger body | No migration has one, and the splitting is now driven by `sqlite3.complete_statement`, so the comment strip is the only thing that could mangle a body. Left as it is, and recorded here. |
| The report's risk wording did not match the adapter | The adapter's `floorRisk` is `medium` and the run declared `high`; the wording in the header now says both |

The review found the quadratic paths real and already labelled, and put
numbers on them: at ten times the volume the independence resolver is first
to break, at 10k authors it is 50 million account pairs, and the clustering
engine is second, at roughly 50 million pain-point pairs each compared twice
at 384 dimensions. Both limits are recorded in section 16.

The review also checked and found clean: the migration sequence and the
renumbered files, the three triggers against every writer, the claim that the
collection branch writes no pain points, the SQL interpolation surface, the
filesystem paths, the Obsidian injection surface, the URL fetching path, the
shell and process surface, the determinism of the ids and the ordering, and
the `ph db status` table list. The `INSERT OR IGNORE` path of the store was
checked specifically, and it does not suppress the trigger.

The review did not check: the live behaviour of `ph project --outbox` against
a real non-empty vault, every branch of the four largest new packages, the
`eval/` package, the eight session branches in isolation, the semantics of
`config/sources.yaml`, the Windows reserved device names in the vault, and
the §44 table line by line.

### The second round

A second review node ran against the tree after the first round's fixes. It
found one Must-fix that the first round's fixes had introduced, and it put
numbers on the load limits.

**The evidence module wrote a column that belongs to a later migration.** The
first round's fix made `set_document_status` write `documents.deleted_at`, so
that the column the independence analysis reads would hold data instead of a
null. But `documents.deleted_at` is created by the independence migration,
and the code that writes it lives with the evidence migration. Against a
database at version 5 the evidence package raised
`sqlite3.OperationalError: no such column: deleted_at`. The column has moved
to the evidence migration, which owns the document lifecycle and the code
that writes it, and the independence migration no longer adds it. The
regression test migrates to version 5 alone and calls `set_document_status`,
and it was checked by mutation: with the column moved back, it fails with the
reviewer's exact error.

The second round's other findings:

| Finding | State |
|---|---|
| The projection never read liveness, so the vault showed a deleted document's quote as ordinary live evidence | **Fixed.** The reader takes `live_status` from the span, falls back to the document, and the rendered entry carries a status line when the quote is no longer live. A live quote renders exactly as it did before, so every existing note is unchanged. |
| The checkpoint failure event carried the error class but not the message, so "database is locked" and "disk I/O error" looked the same | **Fixed.** The event carries a bounded detail with the target URL taken out of it. |
| The `shortcut:` marker for the silent default was in the type's docstring, where a reader of the method would not find it | Moved to the method that drops the event. |
| The decision record and the clustering spec named the old migration number and described the old splitter | Corrected. |
| The collection report still said a broken checkpoint "is logged" | Corrected. |
| The clustering duplicate review is O(P²) with one decision per pair, and the independence resolver is O(A²) with no cap | **Not fixed, and recorded.** The numbers are in section 16. A cap is new behaviour and new configuration, and this is an integration. |
| `authors.deleted_at` has no writer, and `documents.deleted_at` had none | `documents.deleted_at` has a writer now. `authors.deleted_at` still has none; only `authors.deletion_status` is written, and it is recorded in section 16. |
| The `evidence_lineage` query joins through `evidence_pain_links` to reach the evidence, which the trigger makes equivalent to the pain point's own span | Left as it is. The join is one more table to scan per cluster, and it is the shape the store has always had. |
| The five copies of `_hash_id` in the independence package | Left as they are. The two reviews disagreed on this: one called it the house pattern, one called it churn. It is a one-line helper repeated, and consolidating it buys nothing at runtime. |

The second round named the behaviours that have no test. The two that matter
for trust are covered now: the evidence module against its own migration, and
the projection's liveness. The rest are recorded in section 16.

## 13. Security review

The hardening pass ran over the whole integrated tree against spec §37 and
§37A. Its findings:

| Finding | Severity | State |
|---|---|---|
| The extraction prompt was built with `str.format` over a template that receives the page text | none | **Corrected.** The hardening pass reported this as a crash path, and that was wrong: `str.format` does not re-parse a substituted value, so the pre-fix template never raised on a brace in the page text. The build was changed to a literal replacement anyway, because the template then no longer has to carry doubled braces, and it was relabelled from a security fix to a maintainability change. The three tests around it were rewritten so that they fail against the old build. |
| The collection package imported `logging` and logged a URL that came from the network, breaking the rule the other library packages follow and putting untrusted text in a log line | Must | **Fixed.** The collection layer now hands a structured event to the caller, which owns the observability policy. The event names the source, the scope and the error class, carries a bounded detail with the target URL taken out of it, and never carries a URL. The three tests that watched the log now watch the event, and the checkpoint test asserts the URL's absence value by value. |
| A table name read from `sqlite_master` was interpolated into the count query | Nice | **Fixed.** The identifier is quoted, on top of the identifier check that was already there. |
| The window between the URL admission check and the connect is not closed, so a rebind inside the window is not caught | Should | **Open.** The label was already on the code and states the fix. Closing it means pinning the resolved address in the transport, which changes the shared fetch path. It is reported as the standing security residual. |

The pass checked and found clean: shell and process execution (no
`shell=True`, no command string, no `eval`, no `pickle`; the two subprocess
uses pass an argument vector to the documented OpenCode CLI), filesystem
writes and path traversal (the projection is the only writer, the vault
resolver refuses a path that escapes the root, and no file name is built from
untrusted text without sanitising), Obsidian injection, SSRF and URL safety
(scheme allowlist, no userinfo, no IP literal, host allowlist against
`config/sources.yaml`, private and loopback blocking, DNS resolution before
connect, per-hop redirect re-validation, a redirect limit, and no
HTTPS-to-HTTP downgrade), secrets, SQL injection, determinism and
idempotency.

The pass did not check: the test suite and the `eval/` package, and the
migrations beyond the diff it reviewed.

## 14. Test results

| Run | Result |
|---|---|
| `uv run pytest` on `main` at `59e29fa`, before the run | 1085 passed |
| `uv run pytest` after merge 1, the projection | 1210 passed |
| `uv run pytest` after merge 2, the collection layer | 1293 passed |
| `uv run pytest` after merge 3, the evidence pipeline | 1333 passed |
| `uv run pytest` after merge 4, the discovery engine | 1498 passed |
| `uv run pytest` after merge 5, the clustering engine | 1605 passed |
| `uv run pytest` after merge 6, the independence analysis | 1686 passed |
| `uv run pytest` after merge 7, the opportunity scoring | 1954 passed |
| `uv run pytest` after merge 8, the investigation engine | 2129 passed |
| `uv run pytest` after the `ph db status` fix | 2131 passed |
| `uv run pytest` after the security fixes | 2133 passed |
| `uv run pytest` after the documentation pass | 2133 passed |
| `uv run pytest` after the first review round | 2135 passed |
| `uv run pytest` after the second review round | 2138 passed |
| `uv run pytest` from the committed state | **2138 passed, exit 0** |

The verify node observed the gate independently and reported
`2131 passed in 77.89s (0:01:17)`, exit 0, 72 test files, no skips. The seven
tests that came after it are the two the security fixes added, the one the
liveness regression needed, the one that replaced the extraction test that
asserted nothing, and the three the second review round needed.

Every number in the table above was measured. The counts at each merge were
re-derived by collecting the tests at each merge commit in a throwaway
worktree, not taken from the run's notes.

Per-file counts are in the verify node's report. The largest new files are
`tests/test_opportunity_lifecycle.py` at 156, `tests/test_clustering_store.py`
at 48, `tests/test_research_investigation_pipeline.py` at 63, and
`tests/test_evidence_pipeline.py` at 40.

No assertion was weakened, skipped or deleted anywhere in this run. The
three tests that watched the collection layer's log now watch its event, and
the assertion they make is stronger: the event's shape is checked field by
field, and the URL's absence is asserted.

## 15. Spec audit

The comparison is against `Product-Hunter-Full-Spec.md`. The states are
`DONE`, `PARTIALLY DONE`, `DEFERRED`, `BLOCKED` and `NOT IMPLEMENTED`.

### The implementation phases of §38A

| Phase | State | What is true |
|---|---|---|
| 1, hand-prove extraction and normalization | `PARTIALLY DONE` | The chain fails closed, the quote check is exact, the ids are deterministic and every accepted pain point traces to a span. The acceptance criterion of 100 manually inspected spans is not evidenced: the evaluation set holds 30 cases and 0 human labels. |
| 2, evidence core | `DONE` | The schema, the store, the fenced jobs and checkpoints, the transactional outbox, and the crash-recovery tests. `propagation_edges` has its columns and no writer, which is the one gap. |
| 3, semantics and independence | `PARTIALLY DONE` | Clustering, cluster lineage, entity resolution and propagation-aware independence are built and tested. The embedding model was never run, Qdrant and DuckDB do not exist, and the cluster merge and split checks run against fixtures rather than against a labelled evaluation set. |
| 4, research gate and adversarial validation | `PARTIALLY DONE` | The contract, the gate, the budget, the query families, the scoring, the confidence, the competitor search and the counter-evidence are built and tested. The gate still has no caller, so the enforcement does not run in the pipeline. |
| 5, OpenCode interface | `DONE` | The plugin, the ten commands, the state layer and the cross-chat continuation are in place, and the plugin runs the state layer with an argument vector rather than a shell string. |
| 6, controlled scale and projections | `PARTIALLY DONE` | The Obsidian projection, the daily brief, the model routing and the source budgets are built. The wider source set, the Qdrant and DuckDB adapters, the crawler and browser rungs and the managed scraper are not. |
| 7, optional heavy infrastructure | `DEFERRED` | By design. Nothing from it was pulled into the core. |
| 8, operational hardening | `NOT IMPLEMENTED` | No retention tiers, no backup or restore, no disk watermark, no parser-drift canary, and the migrations are forward-only with no rollback notes. This is the largest single gap in operational readiness. |

### The thirty-two items of §44

| # | Item | State |
|---|---|---|
| 1 | `product-hunter` is available inside OpenCode | `DONE` |
| 2 | OpenCode can create, inspect, pause, resume, retry and cancel runs | `DONE` |
| 3 | A worker owns execution independently of chat lifetime | `PARTIALLY DONE`; the worker runtime owns execution and nothing is wired to it |
| 4 | It can autonomously discover public evidence | `PARTIALLY DONE`; the discovery engine is built and has no caller |
| 5 | It can collect and preserve provenance | `PARTIALLY DONE`; the collection layer is built and has no caller |
| 6 | It can identify independence with propagation-aware deterministic analysis | `PARTIALLY DONE`; the analysis is built, the propagation edges have no writer |
| 7 | It can extract and normalize pain points | `DONE` |
| 8 | It can semantically cluster problems | `PARTIALLY DONE`; built, no live embedding model, no caller |
| 9 | It can score opportunities with separate potential and confidence | `PARTIALLY DONE`; built, no caller, no `opportunities` table |
| 10 | It can use SubQ for high-volume structured decisions | `DEFERRED`, spec §38A Phase 7 |
| 11 | It can route simple tasks to fast models | `DONE`; the chains are configured and fall through |
| 12 | It can route hard tasks to a reasoning model | `DONE`, the same way |
| 13 | It can research competitors and alternatives | `PARTIALLY DONE`; built, no caller |
| 14 | It can actively seek counter-evidence | `PARTIALLY DONE`; built, no caller |
| 15 | It can reject weak or already-solved opportunities | `PARTIALLY DONE`; the lifecycle and the gate can refuse, nothing drives them |
| 16 | It can track opportunity history and cluster lineage | `PARTIALLY DONE`; the cluster lineage is in the database, the score history is not |
| 17 | It can detect stale opportunities | `PARTIALLY DONE`; the lifecycle knows its states, no scheduler drives it |
| 18 | It can generate validation experiments | `PARTIALLY DONE`; the experiment record is built and the projection reads it when the table exists |
| 19 | It can write and continuously update the Obsidian vault as a projection | `DONE`; `ph project` and `ph project --outbox` |
| 20 | It can run scheduled jobs without manual intervention | `PARTIALLY DONE`; the scheduler exists and nothing starts it |
| 21 | It can recover from failures, crashes, shutdowns and stale workers | `DONE`; the crash-recovery suite kills a real child process |
| 22 | Research state is idempotent and protected by leases and fencing | `DONE` |
| 23 | Cross-store projections converge through a transactional outbox | `PARTIALLY DONE`; the Obsidian target has a handler and the outbox runs, the other stores do not exist |
| 24 | Untrusted internet content cannot directly trigger tools or side effects | `DONE` for the paths that exist |
| 25 | Structured LLM outputs pass schema and deterministic validation | `DONE` |
| 26 | The system records spend, tool provenance, evidence provenance and checkpoint history | `PARTIALLY DONE`; the spend ledger has one writer, the discovery store |
| 27 | Research IDs remain stable when the worker moves from laptop to cloud | `DONE`; the ids are content-derived |
| 28 | `/list-research` and cross-chat continuation operate from persistent state | `DONE` |
| 29 | The system can explain why an opportunity received its score | `PARTIALLY DONE`; the snapshot names its policy and its inputs, the end-to-end trace to source evidence is not built |
| 30 | Required research gates fail closed when mandatory evidence is missing | `PARTIALLY DONE`; the gate fails closed in the library and nothing calls it |
| 31 | The implementation phases have passed their stated acceptance criteria | `NOT MET`; phases 3, 4, 6 and 8 have not |
| 32 | Source legality, retention, backup, drift and budgets are operationally verified | `NOT MET`; the operational work of §38B does not exist |

Twelve items are met, seventeen are partly met, one is deferred and two are
not met. Item 31 says the phases must pass their acceptance criteria before
the system is production-ready, and item 32 says the operational work must be
verified before unattended long-term operation. Neither condition is met, so
the system is not production-ready for unattended operation, and the
specification says so itself.

## 16. Known limitations

1. **No stage handler.** Seven of the eight Wave 2 packages are libraries
   with a store and no caller. The projection is the exception. The missing
   piece is a handler that claims a job, runs one stage, records its tool
   calls and artifacts in the gate's tables, and advances the run. It is the
   one thing that would make the whole pipeline runnable.
2. **The DNS rebind window.** The URL admission check and the connect are
   not one operation. It is labelled in the code, and it is the standing
   security residual.
3. **The propagation edge table has no writer.** The independence analysis
   reads propagation edges and the propagation detector finds them, but
   nothing writes a row.
4. **The embedding path never ran.** The local embedding model is imported
   lazily and is not installed, so the clustering similarity has only been
   exercised against fixtures.
5. **The clustering comparison is quadratic.** Every pain point is compared
   with every other one. It is labelled in the code, and the nearest-neighbour
   index is the fix. The second review measured it: 200 pain points take 2.9
   seconds, 400 take 12, and 800 take 53. The duplicate review is a second
   quadratic sweep, and it holds one decision per pair, so at 8,000 pain
   points it would hold 3.16 million decisions in memory and write 3.16
   million rows before the first one lands. A cap on the decisions a run
   records is the fix, and it is a configuration this wave did not add.
6. **The identity resolver is quadratic**, and the identity key is an
   unsalted hash. The second review measured it: 500 authors take 0.7
   seconds, 1,000 take 2.7, 2,000 take 13, and 4,000 take 54. At 40,000
   authors one resolve is about ninety minutes, and the community resolver is
   quadratic on top. Bucketing the comparison by the normalised handle, and
   refusing a scope above a configured size the way the propagation detector
   already does, is the fix.
7. **Seven of the twelve budget dimensions have no writer.** The spend
   ledger has one writer, the discovery store. The gate reports the
   unmeasured dimensions per run rather than pretending they are zero.
8. **No operational work.** No retention, no backup, no restore canary, no
   disk watermark, no parser-drift canary, and the migrations are
   forward-only with no rollback notes.
9. **The evaluation set has no labels.** 30 cases, 0 labelled, so the
   benchmark can measure quote fidelity and nothing else.
10. **The lint and type gates are not configured.** Ruff and mypy are not
    project dependencies and there is no configuration for either.
11. **The `network` pytest marker is inert.** No test carries it.
12. **`authors.deleted_at` has no writer.** Only `authors.deletion_status` is
    written. The independence analysis reads the author record, not the
    timestamp, so nothing depends on the column holding data.
13. **A few behaviours have no test.** The second review named them: the
    collection duplicate branch that reuses an earlier attempt, the live path
    of the contract-overrun report, the experiment and research-request
    renderers, the projector's own path guard, the clustering store's
    redundant lineage join, the sentence-transformers embedder, which has
    never run because the model is not installed, and the naive timestamp
    fallbacks in the independence store.

## 17. Deferred functionality

None of this was pulled into the core, and none of it was quietly built.

| Integration | State | Reason |
|---|---|---|
| SubQ Snap | `DEFERRED` | Spec §38A Phase 7, behind a benchmark gate. The pipeline never reads the key. |
| Apify | `DEFERRED` | Spec §38A Phase 7. The MCP server is configured in `opencode.json`; the worker does not use it. |
| Crawl4AI | `DEFERRED` | Spec §8 and §38A Phase 7. The ladder refuses the rung with `collector_unavailable`. |
| Large-scale Playwright collection | `DEFERRED` | Spec §8 and §37A, which requires browser isolation first. |
| Langfuse | `DEFERRED` | Spec §34, only after a Phase 7 benchmark. |
| Redis, Temporal, BullMQ | `DEFERRED` | Spec §38 excludes them from the initial core. |
| Obsidian two-way synchronisation | `DEFERRED` | Spec §38A Phase 7. The projection is one-way and says so. |
| Qdrant and DuckDB | `DEFERRED` | Spec §38A Phase 6 production adapters; the similarity layer takes an embedding identity and a space id instead. |
| LangChain, LangGraph, LangSmith, LiteLLM, OpenTelemetry, Prometheus | `DEFERRED` | Spec §38, deliberately excluded. |

## 18. The remaining blockers

Nothing blocks the integration commit. These block the next wave.

1. **A stage handler that records tool calls and artifacts.** The gate, the
   persistence, the fence, the budget and the query plan are in place, and
   the investigation engine bridges into the gate. The handler is what makes
   them bite.
2. **A `spend_ledger` writer for the remaining budget dimensions.**
3. **A promotion path.** The gate can refuse; nothing is refused yet.
4. **A writer for `propagation_edges`**, so the independence analysis counts
   real propagation rather than the absence of it.
5. **The `opportunities` table**, so a score has a row to live in and the
   projection can read it.
6. **The operational work of spec §38B.** This is the reason the system is
   not production-ready for unattended long-term operation.
7. **A labelled evaluation set**, so the acceptance criteria of spec §38A
   can be measured rather than asserted.

## 19. The final commit

One commit lands the integrated tree on `main`:

```
wave2: integrate intelligence pipeline
```

It carries the eight merged branches, the conflict resolutions, the
migration renumbering, the security fixes and this documentation. The
per-branch merge commits stay on `integration/wave2`, and every session
commit stays on its own `wave2/*` branch, so nothing is rewritten and
nothing is lost.

Nothing was pushed. The remote still points at `3d7bc68`.

### The state the repository was left in

One working tree, on `main`, in the repository itself. The eight Wave 2
worktrees and the `.worktrees/` folder inside the repository were removed,
and the worktree the Wave 1 integration created was removed once `main`
came back to the repository. Every branch was checked against the integrated
tree before its worktree went, and the tree of `main` and the tree of
`integration/wave2` are the same digest.

What is left:

| | |
|---|---|
| Working tree | the repository, on `main` |
| Branches | `main`, `integration/wave2`, and the eight `wave2/*` session branches |
| Untracked | nothing |
| Secrets | none tracked, `.env.example` holds names only |
| Temporary files | the run's scratch files were removed |

One empty directory named `product-hunter-main` is left beside the
repository. A running memory server from another session holds a handle on
it, so the operating system refuses to remove it. It holds no file, and git
no longer knows it as a worktree.

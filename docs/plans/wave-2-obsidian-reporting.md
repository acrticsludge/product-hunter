# Wave 2 Session 8: Reporting and Obsidian Projection — Plan

Status: PLAN
Spec: `docs/reasonix/specs/wave-2-obsidian-reporting.md`
Contract version: `projection-v1`

## 1. Architecture

### 1.1 The shape of the layer

```
SQLite (read-only)
      │
      ▼
product_hunter.projection.source      builds a Snapshot from the database
      │
      ▼
product_hunter.projection.models      Snapshot: opportunities, sources, communities,
      │                               markets, experiments, requests, reference date
      ▼
product_hunter.projection.render      pure functions: Snapshot -> note bodies
      │
      ▼
product_hunter.projection.vault       note kind -> path, band, archive reason
      │
      ▼
product_hunter.projection.engine      writes changed files, prunes stale ones,
                                      keeps the manifest, handles the outbox task
      │
      ▼
data/vault/Product-Hunter-Vault/
```

The renderer never touches the filesystem and the source never writes. The
engine is the only module that writes, and it writes only under the vault
root. That split is what makes the tests cheap: the renderer is tested on
strings, the engine on a temporary directory, and the source on a temporary
database.

### 1.2 The modules

| Module | Holds | Depends on |
| --- | --- | --- |
| `contract.py` | `CANONICAL_DDL`, `CONTRACT_VERSION`, the table and column map, the capability check | stdlib |
| `models.py` | the pydantic records the layer passes around | pydantic |
| `sanitize.py` | `neutralize`, `safe_url`, `yaml_scalar`, `slug`, `content_hash` | stdlib |
| `render.py` | one renderer per note kind | models, sanitize, vault |
| `vault.py` | the folder layout, the score bands, the archive reasons, the file names | sanitize |
| `source.py` | `SqliteProjectionSource`, the reader | models, contract, stdlib sqlite3 |
| `engine.py` | `Projector`, the outbox handler, the manifest | models, render, vault, db.outbox |

`sanitize.py` and `vault.py` have no dependency on the database, and
`render.py` has no dependency on the filesystem. Nothing imports `engine`
except `cli.py`.

### 1.3 The public surface

```python
# product_hunter/projection/__init__.py
from product_hunter.projection.contract import CANONICAL_DDL, CONTRACT_VERSION
from product_hunter.projection.engine import ProjectionError, ProjectResult, Projector
from product_hunter.projection.models import (
    CommunityRef, EvidenceRef, EvidenceSummary, Experiment, MarketReport,
    Opportunity, ResearchRequest, Snapshot, SourceRef,
)
from product_hunter.projection.sanitize import (
    content_hash, neutralize, safe_url, slug, yaml_scalar,
)
from product_hunter.projection.source import SqliteProjectionSource
from product_hunter.projection.vault import VaultLayout
```

`Projector` is the entry point:

```python
projector = Projector(vault_root, source=SqliteProjectionSource(conn))
result = projector.project(as_of=None)      # -> ProjectResult
projector.handle_projection_task(task)       # -> None, raises on failure
```

`ProjectResult` carries `written`, `unchanged`, `removed` and the list of
`paths`, in that order, so the CLI can print a stable JSON summary.

### 1.4 The records

```python
class EvidenceRef(BaseModel):
    span_id: str
    quote: str
    stance: str            # supporting | counter
    source_id: str
    source_type: str
    url: str
    community_id: str | None
    community_name: str
    author_id: str | None
    author_handle: str
    document_id: str
    captured_at: str
    content_hash: str      # the document content hash

class EvidenceSummary(BaseModel):
    mentions: int
    canonical_documents: int
    independent_authors: int
    uncertain_authors: int
    independent_communities: int
    propagated_copies: int
    source_concentration: float   # share of mentions from the busiest source

class Opportunity(BaseModel):
    opportunity_id: str
    title: str
    statement: str
    domain: str
    actor: str
    action: str
    object: str
    consequence: str
    severity: float | None
    frequency: str
    wtp_tier: str | None
    competition: float | None
    potential: float | None
    confidence: float | None
    trend: str
    uncertainty: str | None
    lifecycle: str
    recommended_experiment: str | None
    first_seen: str
    last_updated: str
    evidence: list[EvidenceRef]
    evidence_summary: EvidenceSummary
    claims: list[str]          # statements, each with the spans behind it
    communities: list[str]
```

A field the database does not hold is `None` and renders as `not recorded`.
That is the only way a missing value is allowed to appear.

### 1.5 The outbox path

`db/store.py` enqueues a task with the target `obsidian` when a run is
imported. `db/outbox.py` already implements claim, lease, complete, fail with
backoff, and lease recovery. This session adds a handler and nothing else.

```python
def handle_projection_task(self, task: ProjectionTask) -> None:
    self.project()
```

The handler rebuilds the whole vault, because a partial rebuild would be
wrong: the vault is a projection of the entire state. The rebuild is
idempotent, so the cost of rebuilding is the cost of comparing bytes.

`process_outbox` catches the exception, calls `fail_projection` and schedules
the retry. The database is never touched by the projection, so a failed
projection cannot roll anything back.

## 2. The steps

Each step names the files it writes and the check that closes it.

### Step 1 — the contract and the models

Files: `contract.py`, `models.py`.

`contract.py` holds the DDL from section 4.2 of the spec, the contract
version, and the table and column map used by the reader. The reader asks
`PRAGMA table_info` which columns exist, so a sibling table with a different
shape degrades instead of crashing.

`models.py` holds the records of section 1.4. Every record is a pydantic
model with `extra="forbid"`, matching the style of `research/evidence.py`.

Check: the models import and validate a filled record.

### Step 2 — the sanitizer

Files: `sanitize.py`, `tests/test_projection_sanitize.py`.

`neutralize(text)` applies the escapes of section 8 of the spec, in an order
that cannot be undone by a later escape: the ampersand first, then the
angle brackets, then the markup characters.

`safe_url(url)` returns the URL when the scheme is `http` or `https`, and
`blocked:<scheme>` otherwise. It never raises, because a hostile URL in a
quote must not stop a projection.

`yaml_scalar(value)` returns `json.dumps(value, ensure_ascii=False)`. A JSON
string is a valid YAML double-quoted scalar, so the frontmatter takes the
standard library's escaping instead of a second implementation of it.

`slug(text)` returns a lowercase, dash-separated slug of at most sixty
characters, with every character outside `[a-z0-9]` dropped. A title that
slugifies to nothing gives `untitled`.

`content_hash(body)` returns the sha256 of the body bytes.

Check: the sanitizer tests of section 12 of the spec, and a test that
ordinary prose comes through unchanged.

### Step 3 — the vault layout

Files: `vault.py`.

`VaultLayout` maps a note kind and its record to a path below the vault
root. It owns the seven top-level folders, the six score bands, the five
archive reasons, and the file naming.

The band comes from the potential: 90 to 100 is Exceptional, 80 to 89
Strong, 70 to 79 Interesting, 50 to 69 Weak Evidence, 0 to 49 Low Potential.
A missing potential gives the Unscored band.

The archive reason comes from the lifecycle: `ALREADY_SOLVED`, `REJECTED`,
`INSUFFICIENT_EVIDENCE`, `STALE` and `DEAD` each have a folder. Every other
lifecycle, `CANDIDATE` included, stays in `01-Ideas`.

Check: the band and archive tests, and a test that a path never leaves the
vault root.

### Step 4 — the renderers

Files: `render.py`, `tests/test_projection_render.py`.

One renderer per note kind, each a pure function that returns the note body
as a string. The engine adds the frontmatter and the hash, so the renderers
never see a hash and the hash never changes the body.

The opportunity renderer writes the fifteen fields of section 6 of the spec
in order, and names the evidence behind each material claim.

The renderers format a float with two decimals, or `not recorded` when the
value is `None`. They never print a zero for a missing value.

Check: the renderer tests, including the golden body of one opportunity and
the rule that a missing field reads `not recorded`.

### Step 5 — the reader

Files: `source.py`, `tests/test_projection_source.py`.

`SqliteProjectionSource(conn)` reads the database into a `Snapshot`. It
opens nothing: the connection is passed in, and the caller decides whether
it is read-only.

The reader builds the opportunities from `opportunities` when the table is
there, and from the pain points otherwise. The fallback groups the pain
points by `normalized_key`, falling back to the pain point id.

The independence counts come from the linked documents: distinct authors
through `author_links`, distinct communities through `documents`, distinct
document content hashes for the canonical count, and `propagation_edges` for
the propagated copies. An author whose link confidence is below `0.5` counts
as uncertain rather than independent.

The lifecycle comes from the latest `human_decisions` row whose entity id is
the opportunity id or one of its pain point ids. The decision text is mapped
onto the spec's lifecycle list; an unknown text leaves the lifecycle at
`CANDIDATE` and is not guessed.

The reference date is the newest timestamp among the documents, the evidence
and the decisions, unless the caller passes one.

Check: the source tests, which build a temporary database with the tables
the projection reads, insert evidence, and assert the counts.

### Step 6 — the engine

Files: `engine.py`, `tests/test_projection_engine.py`.

`Projector.project()` renders every note, compares it with the file on disk,
writes the ones that differ, removes the ones the manifest lists but the
snapshot no longer holds, and writes the manifest.

A note is written to a temporary file in the same directory and then moved
into place, so a reader never sees half a note.

The manifest is a JSON file at the vault root. It maps a relative path to
the id, the kind and the hash. When the manifest is missing, the engine finds
its own notes by the frontmatter marker and prunes the ones that are gone.

`handle_projection_task` calls `project` and lets the exception out, so the
outbox records the failure and schedules the retry.

Check: the engine tests of section 12 of the spec.

### Step 7 — the command

Files: `cli.py`.

`ph project [--db PATH] [--vault PATH] [--outbox] [--as-of YYYY-MM-DD]`
prints a JSON summary and returns 0 or 1.

The `--outbox` mode claims the pending `obsidian` tasks and runs them
through `process_outbox`, which is the path the spec's architecture wants.
The default mode builds the vault straight away.

The command reuses `_db_path`, `_root` and `_require_db` from `cli.py`, so
the path handling matches the other commands.

Check: the command runs against a temporary database and produces a vault.

### Step 8 — the documentation

Files: `docs/reasonix/ARCHITECTURE.md`.

Add the projection layer to the architecture document, with the rule that
SQLite stays authoritative and the vault is a projection.

## 3. The test plan

Four test files, one per concern.

`tests/test_projection_sanitize.py` covers the escapes: HTML, a script tag, a
wikilink, an embed, a Templater expression, a Dataview inline block, a
comment, a heading, a table row, a blockquote, a frontmatter break, and a
hostile URL scheme. It also covers the cases that must stay unchanged: plain
prose, a URL, a number, and text with no special characters.

`tests/test_projection_render.py` covers the note bodies: the fifteen fields
of an opportunity, the evidence links, the counts, the sorted tables, the
`not recorded` marker, and the golden body of one opportunity.

`tests/test_projection_source.py` covers the reader: the counts, the
grouping, the lifecycle from a human decision, the reference date, the
canonical path when the `opportunities` table exists, and the fallback when
it does not.

`tests/test_projection_engine.py` covers the writer: determinism,
idempotency, a changed opportunity, a deleted opportunity, a duplicate task,
a failed write and the retry, the stable file name, and the prune of a note
that is gone.

Every test builds its own temporary database and its own temporary vault. No
test touches the repository's own database.

## 4. What this session does not do

- No migration, and no change to any existing table.
- No dependency. The layer uses pydantic and the standard library, both
  already in the project.
- No two-way synchronisation. A note edited by a human is not read back, and
  the next projection overwrites it. The spec asks for that to be flagged
  rather than silently lost, so the engine notes in its summary how many
  notes it overwrote, and the report explains that the flag is a report line
  rather than a feature.
- No Git tracking of the vault. `data/` is already ignored.
- No change to the scoring, discovery or investigation sessions.

## 5. Integration requirements

These are the things another session or the wave integrator has to do.

1. **Land the canonical DDL.** `contract.py` holds `CANONICAL_DDL`. A
   migration must create those tables, with a number that does not collide
   with a sibling session's migration. The projection reads them when they
   exist and falls back when they do not, so the order does not matter.
2. **Reconcile the column map.** If the scoring session lands `opportunities`
   with different column names, change the map in `contract.py` and nothing
   else.
3. **Feed the outbox.** The `obsidian` target is already enqueued by the run
   import. A worker that drains the outbox with the handler from
   `engine.py` closes the loop.
4. **Own the lifecycle writes.** The projection reads `human_decisions` for
   the lifecycle. Whoever writes those rows decides the vocabulary; the
   reader maps the text onto the spec's lifecycle list and treats anything
   it does not recognise as `CANDIDATE`.
5. **Point the vault at a real directory.** The default is
   `data/vault/Product-Hunter-Vault`. A deployment that keeps the vault
   elsewhere passes `--vault`.

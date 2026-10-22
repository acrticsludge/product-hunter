# Wave 2 Session 5: source independence and entity resolution

**Status:** approved for implementation.
**Spec basis:** `docs/reasonix/specs/Product-Hunter-Full-Spec.md` §12, §15, §15A,
§22, §38A Phase 3; `.opencode/skills/source-independence/SKILL.md`;
`.opencode/skills/entity-resolution/SKILL.md`.
**Adapter:** Product Hunter (`floorRisk: medium`). `db/migrations/**` is a
high-risk path, so stages 2, 6, and 7 are not skippable.

---

## 1. Purpose

The system must distinguish raw mentions from independent evidence. A run that
collects 147 mentions of the same complaint must not report 147 people, and it
must not let a language model declare the number either. Independence is a
computed property of a graph of authors, communities, observations, and
propagation edges. The graph is the record; the numbers are a function of it.

This wave builds the graph, the resolution, the propagation detection, and the
deterministic analytics. It does not score opportunities, size markets, analyse
competitors, or project to Obsidian.

## 2. Scope

### In scope

1. Author (entity) resolution across platforms.
2. Community resolution across platforms.
3. Author identity confidence.
4. Author links (author to observation, and author to author).
5. Suspected same-person relationships.
6. Cross-post detection.
7. Repost detection.
8. Quote and propagation detection.
9. Propagation edges.
10. The independence graph.
11. Independent-author counting.
12. Independent-community counting.
13. Source concentration.
14. Propagation-adjusted evidence count.
15. Uncertainty accounting.

### Out of scope

Scoring, market sizing, competitor analysis, Obsidian projection, Qdrant
embedding, DuckDB materialisation. DuckDB remains the analytical surface per
§16, but this wave computes the numbers in Python over the authoritative SQLite
state so the numbers are reproducible without a running DuckDB.

## 3. Vocabulary

The unit of analysis is a **mention**: one observed post, issue, comment, or
page that carries at least one evidence span in scope. A mention is a
document, not an extracted quote. One document with three quoted spans is three
extractions of one mention.

| Term | Meaning |
|---|---|
| mention | one in-scope document |
| raw mention | a mention with its text, author, community, and source |
| canonical content | a group of mentions that carry the same content |
| independent author | a resolved identity group that is confidently one person |
| uncertain author | an identity group that may or may not be a distinct person |
| independent community | a canonical community contributing root observations |
| propagation edge | a directed relation from one mention to a later mention |
| author cluster | an identity group: one account or a set of accounts that may be one person |

### 3.1 Why a mention is a document

The spec's worked shape is `147 mentions -> 91 canonical observations`. A
mention count that counted extracted quotes would collapse for a reason that
has nothing to do with independence. The unit is the document, and the
extraction count travels separately.

## 4. The LLM boundary

> The LLM may provide signals. The analytics layer determines the final metric.

Concretely:

- A model may emit an `IdentitySignal` of kind `model_signal` for a pair of
  authors. It is weighted by `identity.signal_weights.model_signal` and nothing
  more.
- `model_signal` is a **weak** signal. A merge requires at least one **strong**
  signal (`identity_key`, `same_platform_handle`). A model signal alone can
  therefore never produce a merge, whatever the model says and whatever the
  configured weight. The policy validator refuses a configuration in which
  `model_signal` could reach `merge_threshold` on its own.
- No number in `IndependenceMetrics` can be supplied by a caller. The metrics
  object is built only by `analyze()`, from the graph.
- Every metric carries the policy version that produced it, and the report
  stores the graph edges it was computed from.

## 5. Policy

`config/independence.yaml` holds the whole policy. It is versioned. Nothing
below is a constant in code.

```yaml
version: independence-v1

identity:
  standalone_handle_confidence: 0.7   # a stable platform handle, nothing else
  identity_key_bonus: 0.2             # an identity key raises the confidence
  profile_url_bonus: 0.1              # a profile URL raises it a little
  confident_threshold: 0.6            # at or above this, a group is a person
  merge_threshold: 0.85               # at or above this, a pair is one person
  suspect_threshold: 0.5              # at or above this, a pair is a suspect edge
  conflict_score_cap: 0.5             # a contradicted pair never scores higher
  cross_source_name_weight: 0.6       # same community name on two platforms
  signal_weights:
    identity_key: 0.95
    same_platform_handle: 0.9
    cross_linking: 0.55
    model_signal: 0.35
    signature_reuse: 0.3
    community_overlap: 0.25
    display_name: 0.2
  negative_signal_weights:
    identity_key_conflict: 1.0
    explicit_disavow: 1.0

propagation:
  repost_similarity_threshold: 0.9    # token-set similarity
  syndication_similarity_threshold: 0.75
  quote_min_overlap: 0.6              # longest verbatim block / shorter text
  min_comparable_chars: 40            # below this, no comparison is made
  max_scope_mentions: 2000            # a bigger scope is refused; split it

concentration:
  max_single_source_share: 0.8
  max_single_community_share: 0.6
  max_single_author_cluster_share: 0.4
  max_single_propagation_chain_share: 0.3
  min_independent_authors: 10
  min_independent_communities: 3
```

`min_independent_authors` and `min_independent_communities` mirror
`evidence_thresholds.validation` in `config/research_contract.yaml`. A test
asserts the two files agree, so neither drifts silently.

### 5.1 Policy validation

`IndependencePolicy` refuses to load when:

- any weight is outside `[0, 1]`;
- `merge_threshold` is not above `suspect_threshold`;
- `confident_threshold` is above `merge_threshold`;
- `model_signal` is a strong signal, or its weight reaches `merge_threshold` on
  its own (the model may not merge);
- any single weak signal reaches `merge_threshold` on its own;
- the effective weak ceiling, `min(sum of the weak weights, suspect_threshold)`,
  reaches `merge_threshold` (weak signals alone may not merge);
- `display_name` reaches `suspect_threshold` (a name match alone may not even
  create a suspect edge);
- a similarity threshold is outside `(0, 1]`;
- a concentration share is outside `(0, 1]`;
- a minimum is negative;
- `max_text_chars` is not positive;
- `max_scope_mentions` is not positive.

`suspect_threshold` and `confident_threshold` are not ordered against each
other: the first scores a pair and the second scores a group, so they are not
on one scale.

## 6. Records

All records are pydantic models with `extra="forbid"`, in
`src/product_hunter/independence/records.py`.

### 6.1 Inputs

```python
class AuthorObservation(BaseModel):
    author_id: str
    source_id: str
    handle: str
    display_name: str | None = None
    identity_key: str | None = None      # hashed before storage, never stored raw
    profile_url: str | None = None
    community_ids: list[str] = []
    doc_ids: list[str] = []
    first_seen_at: str | None = None
    last_seen_at: str | None = None
    deletion_status: str = "active"       # active | deleted | unknown

class CommunityObservation(BaseModel):
    community_id: str
    source_id: str
    name: str
    url: str | None = None
    alias_of: str | None = None            # explicit canonical mapping

class MentionObservation(BaseModel):
    doc_id: str
    source_id: str
    community_id: str | None = None
    author_id: str | None = None
    url: str = ""
    text: str = ""
    text_sha256: str = ""
    published_at: str | None = None
    live_status: str = "live"              # live | deleted
    deleted_at: str | None = None
    span_ids: list[str] = []
```

`live_status` is read here and counted by `deleted_source_mentions`, but no
writer in `src/` sets it to `deleted` yet. The collector marks a document
deleted in a later wave; until it does, the counter counts the documents a
caller marked.

### 6.2 Relationships

Every relationship record carries the five things the mission names: detection
method, confidence, provenance, timestamp, and the source and evidence ids.

```python
class RelationshipProvenance(BaseModel):
    source_ids: list[str] = []        # the platform(s) involved
    doc_ids: list[str] = []           # the observations involved
    span_ids: list[str] = []          # the evidence spans involved
    detail: str = ""                  # a short human-readable note
```

```python
class AuthorIdentityLink(BaseModel):
    link_id: str
    author_id: str
    related_author_id: str
    relation: str            # same-person-merged | same-person-suspect
    detection_method: str    # the signal that produced it
    confidence: float
    signals: list[str] = []  # every signal that fired, positive and negative
    conflict: bool = False
    provenance: RelationshipProvenance
    detected_at: str         # ISO-8601, injected by the caller
```

```python
class CommunityLink(BaseModel):
    link_id: str
    community_id: str
    related_community_id: str
    relation: str            # same-community-merged | same-community-suspect
    detection_method: str
    confidence: float
    provenance: RelationshipProvenance
    detected_at: str
```

```python
class PropagationEdge(BaseModel):
    edge_id: str
    from_doc_id: str          # the earlier observation
    to_doc_id: str            # the derived observation
    relation: str             # repost | quote | crosspost | syndication
    detection_method: str
    confidence: float
    provenance: RelationshipProvenance
    detected_at: str
```

### 6.3 Resolution outputs

```python
class AuthorCluster(BaseModel):
    cluster_id: str
    status: str               # resolved | suspect
    member_author_ids: list[str]
    confidence: float
    canonical_author_id: str | None = None
    basis: list[str] = []     # the links that produced the group
```

```python
class IndependenceMetrics(BaseModel):
    policy_version: str
    raw_mentions: int
    canonical_content_count: int
    independent_author_count: int
    uncertain_author_count: int
    independent_community_count: int
    propagation_adjusted_evidence: int
    source_concentration: float
    author_cluster_concentration: float
    community_concentration: float
    propagation_chain_concentration: float
    propagation_chain_count: int
    unattributed_mentions: int
    deleted_source_mentions: int
    conflicting_identity_pairs: int
    suspect_identity_groups: int
    uncertain_identity_groups: int
    independent_community_ids: list[str] = []
    independent_author_cluster_ids: list[str] = []
```

```python
class ConcentrationViolation(BaseModel):
    code: str
    detail: str
    observed: float
    limit: float

class ConcentrationVerdict(BaseModel):
    satisfied: bool
    violations: list[ConcentrationViolation] = []
    metrics: IndependenceMetrics
```

## 7. Identity resolution

`resolve_authors(authors, mentions, policy) -> list[AuthorIdentityLink]`

1. **Pair candidates.** Every unordered pair of distinct author observations is a
   candidate. The pair set is sorted, so the output order is stable.
2. **Signals.** For each pair, the following signals are evaluated:

   | Signal | Fires when | Kind |
   |---|---|---|
   | `identity_key` | both carry a non-empty identity key and the keys match | strong |
   | `same_platform_handle` | same `source_id` and the normalised handles match | strong |
   | `cross_linking` | one account's profile links to the other | weak |
   | `model_signal` | a caller-supplied signal names the pair | weak |
   | `signature_reuse` | the two accounts share a signature or phrasing | weak |
   | `community_overlap` | the two share at least one community | weak |
   | `display_name` | the normalised display names match | weak |
   | `identity_key_conflict` | both carry identity keys and the keys differ | negative |
   | `explicit_disavow` | a caller-supplied signal says the pair is not one person | negative |

   Normalisation for handle and display name: strip, casefold, replace `@` and
   `.` with a word boundary, and collapse a run of whitespace into one space.
   `Jane Doe`, `jane.doe`, and `Jane  Doe` fold to `jane doe`; `@JaneDoe` folds
   to `janedoe` and stays distinct, and so does `ab cd` from `a bcd`.
3. **Score.** `score = sum(weight of each fired positive signal) - sum(weight of
   each fired negative signal)`, clamped to `[0, 1]`.
4. **Caps.**
   - A negative signal fired and no positive signal fired: the pair earns no
     edge at all, and it is not counted as a conflict. Two accounts with
     different identity keys are two strangers, not a contradiction.
   - A negative signal fired and a positive signal fired: the score is
     `conflict_score_cap`, `conflict = True`, and the edge is a
     `same-person-suspect`, never a merge.
   - If no strong signal fired, `score = min(score, suspect_threshold)`.
5. **Edge.**
   - `score >= merge_threshold` and not `conflict` -> `same-person-merged`.
   - `score >= suspect_threshold` -> `same-person-suspect`.
   - otherwise no edge. A pair with only a display-name match produces no edge
     at all, because `display_name` alone stays below `suspect_threshold`.
6. **Provenance.** The edge records every signal that fired, the source ids,
   the doc ids of both accounts, and the timestamp the caller injected.

A caller-supplied signal is a `model_signal` or an `explicit_disavow`. The
resolver accepts them as data and applies the configured weight. It never
accepts a caller-supplied "these are the same person" verdict.

### 7.1 Author identity confidence

`author_confidence(author) -> float`

- no handle: `0.0`.
- handle present: `standalone_handle_confidence`.
- `+ identity_key_bonus` when an identity key is present.
- `+ profile_url_bonus` when a profile URL is present.
- capped at `1.0`.
- an author that is part of a conflicted pair is capped at
  `conflict_score_cap`.

### 7.2 Identity groups (author clusters)

`build_author_clusters(authors, links) -> list[AuthorCluster]`

- The graph has one node per author observation, plus one **anonymous node**
  per canonical content group that has no author. An anonymous node has
  confidence `0.0` and is never an independent author.
- The groups are the connected components over all `same-person-merged` and
  `same-person-suspect` edges.
- A group whose edges are all merges is `resolved`. A group that contains at
  least one suspect edge is `suspect`.
- `cluster.confidence` is the highest member confidence, or the confidence of
  the merge edge that produced the group, whichever is larger.
- `canonical_author_id` is the member with the highest confidence, ties broken
  by the smallest `author_id`.
- `member_confidences` holds each member's own §7.1 confidence, keyed by
  `author_id`; `confidence` stays the group's figure.

Handles and display names compare in normalised form: strip, casefold, replace
`@` and `.` with a word boundary, and collapse a run of whitespace into one
space. `Jane Doe`, `jane.doe`, and `Jane  Doe` fold to `jane doe`; `@JaneDoe`
folds to `janedoe` and stays distinct, as do `ab cd` and `a bcd`.

### 7.3 Which groups are people

- A group is an **independent author** when it is `resolved` and its
  confidence is at or above `confident_threshold`.
- Every other group is an **uncertain author**, counted once per group, never
  once per account. A suspect pair is one possible person, not two.
- A group that is `suspect` is uncertain even when its members are confident
  accounts: the accounts might be the same person, and counting both would
  inflate the number of people.

### 7.4 Community resolution

`resolve_communities(communities, policy) -> list[CommunityLink]`

- An explicit `alias_of` produces a `same-community-merged` link with
  confidence `1.0` and detection method `explicit_alias`.
- Two communities on different sources with the same normalised name produce a
  `same-community-suspect` link with confidence
  `cross_source_name_weight`, detection method `same_name_cross_source`.
- Two communities on the same source with the same name are the same
  community and produce no link.
- The canonical community of a group is the member with the smallest
  `community_id`; merged groups use the alias target.

Independent communities are counted over the canonical communities, so two
platform copies of one community count once.

## 8. Propagation detection

`detect_propagation(mentions, policy) -> list[PropagationEdge]`

Every unordered pair of mentions is compared. A pair is skipped when either
text is shorter than `min_comparable_chars`, unless the two `text_sha256`
values are equal, which is an exact copy and needs no text.

A scope holds at most `max_scope_mentions` mentions. A larger scope is
refused, and the caller splits it and runs it twice.

### 8.1 Text measures

- **Token set similarity**: the token sets of the two texts, lowercased, split
  on non-alphanumerics, tokens shorter than two characters dropped. The score
  is the Jaccard index of the two sets.
- **Longest verbatim block**: the length of the longest common contiguous
  block of the two texts, found with `difflib.SequenceMatcher`.

### 8.2 Classification

For a pair, in this order:

1. Token-set similarity at or above `repost_similarity_threshold`:
   - different `source_id` -> `crosspost`, detection method
     `same_content_cross_source`;
   - same `source_id` -> `repost`, detection method `same_content_same_source`.
2. Otherwise, longest verbatim block at or above `quote_min_overlap` times the
   length of the shorter text -> `quote`, detection method
   `verbatim_quote_overlap`.
3. Otherwise, token-set similarity at or above
   `syndication_similarity_threshold` -> `syndication`, detection method
   `near_duplicate_content`.

The longest verbatim block is quadratic in the length of the two texts, so a
cheap screen runs in front of the matcher. A quote needs a verbatim block of
at least `quote_min_overlap * min_comparable_chars` characters, and a common
block of that length holds a common run of that length. The two texts are
therefore compared on the hashes of their runs of that length first: a pair
whose sets do not intersect cannot share such a run, cannot earn a quote, and
never pays for the matcher. The screen is a necessary condition, so it never
rejects a real quote, and the matcher still decides every pair that passes it.

### 8.3 Direction

The edge points from the earlier observation to the later one. "Earlier" is
`published_at` when both mentions carry a parseable timestamp, compared as
datetimes, so mixed-precision values such as `10:00:00.500Z` and `10:00:00Z`
order by time rather than by codepoint. When one or both lack a timestamp, or
either does not parse, the pair is ordered by `doc_id`, which is deterministic.
The direction never depends on iteration order.

The flat `from_span_id` and `to_span_id` columns hold the first evidence span of
each side (the head of the provenance span list); the complete span set is in
the provenance JSON. The column is a convenience, never the record.

The confidence of an edge is the measure that fired, capped at `1.0`: the
token-set similarity for a repost, crosspost, or syndication; the verbatim
overlap ratio for a quote.

### 8.4 Canonical content and derivation

- A **canonical content group** is a connected component of the mention graph
  over the edges with relation `repost`, `crosspost`, or `syndication`. A
  quote does not merge content, because a quote adds the quoting author's own
  words.
- A canonical group is **derived** when one of its mentions carries a `quote`
  edge whose source mention sits in a different group. A group that is only a
  copy of another group is not derived, because the copy is in the same group.
- **Propagation chains** are the connected components of the mention graph over
  all propagation edges. A chain with one mention is not a chain.

## 9. The independence graph

`build_graph(mentions, authors, communities, links, edges) -> IndependenceGraph`

The graph holds:

- the mentions, keyed by `doc_id`;
- the author observations and the identity links between them;
- the community observations and the community links between them;
- the propagation edges;
- the canonical content groups;
- the author clusters;
- the propagation chains.

It is a data structure. It reads no clock, opens no file, and runs no query.

## 10. Analytics

`analyze(graph, policy) -> ConcentrationVerdict`

The metrics, all computed from the graph:

| Metric | Definition |
|---|---|
| `raw_mentions` | the number of in-scope mentions |
| `canonical_content_count` | the number of canonical content groups |
| `independent_author_count` | the number of resolved, confident identity groups |
| `uncertain_author_count` | every other identity group, counted once each, including the anonymous groups |
| `independent_community_count` | the number of canonical communities that contribute at least one root observation |
| `propagation_adjusted_evidence` | the number of canonical content groups that are not derived |
| `source_concentration` | the largest share of raw mentions held by one source |
| `community_concentration` | the largest share of raw mentions held by one canonical community |
| `author_cluster_concentration` | the largest share of raw mentions held by one identity group |
| `propagation_chain_concentration` | the largest share of raw mentions held by one propagation chain |

A **root observation** is a mention in a canonical content group that is not
derived.

The concentration shares all use the same base, the raw mention count, so the
four numbers are comparable and a report can show them side by side.

### 10.1 Uncertainty accounting

| Counter | Meaning |
|---|---|
| `unattributed_mentions` | mentions with no author |
| `deleted_source_mentions` | mentions whose source is gone |
| `conflicting_identity_pairs` | pairs whose signals contradict each other |
| `suspect_identity_groups` | identity groups that may be one person |
| `uncertain_identity_groups` | every group that is not a confident person |
| `propagation_chain_count` | chains with at least two mentions |

A deleted mention stays in every count. Deletion is a status, not a removal:
dropping it would change a number a past report already used. The deletion
counter tells the reader how much of the count rests on content that can no
longer be re-checked.

### 10.2 Concentration check

`check_concentration(metrics, policy) -> ConcentrationVerdict`

The verdict is `satisfied` when no rule is broken. The rules:

| Code | Rule |
|---|---|
| `independent_authors_below_minimum` | `independent_author_count >= min_independent_authors` |
| `independent_communities_below_minimum` | `independent_community_count >= min_independent_communities` |
| `source_concentration_exceeded` | `source_concentration <= max_single_source_share` |
| `community_concentration_exceeded` | `community_concentration <= max_single_community_share` |
| `author_cluster_concentration_exceeded` | `author_cluster_concentration <= max_single_author_cluster_share` |
| `propagation_chain_concentration_exceeded` | `propagation_chain_concentration <= max_single_propagation_chain_share` |

Each violation carries the observed value and the limit that was broken.

## 11. Persistence

Migration `db/migrations/0003_independence.sql`.

### 11.1 New tables

```sql
CREATE TABLE author_identity_links (
    link_id            TEXT PRIMARY KEY NOT NULL,
    author_id          TEXT NOT NULL REFERENCES authors(author_id),
    related_author_id  TEXT NOT NULL REFERENCES authors(author_id),
    relation           TEXT NOT NULL,
    detection_method   TEXT NOT NULL,
    confidence         REAL NOT NULL,
    signals_json       TEXT NOT NULL DEFAULT '[]',
    conflict           INTEGER NOT NULL DEFAULT 0,
    provenance_json    TEXT NOT NULL DEFAULT '{}',
    detected_at        TEXT NOT NULL,
    UNIQUE (author_id, related_author_id, detection_method)
);

CREATE TABLE community_links (
    link_id              TEXT PRIMARY KEY NOT NULL,
    community_id         TEXT NOT NULL REFERENCES communities(community_id),
    related_community_id TEXT NOT NULL REFERENCES communities(community_id),
    relation             TEXT NOT NULL,
    detection_method     TEXT NOT NULL,
    confidence           REAL NOT NULL,
    provenance_json      TEXT NOT NULL DEFAULT '{}',
    detected_at          TEXT NOT NULL,
    UNIQUE (community_id, related_community_id, detection_method)
);

CREATE TABLE author_clusters (
    cluster_id            TEXT PRIMARY KEY NOT NULL,
    status                TEXT NOT NULL,
    canonical_author_id   TEXT REFERENCES authors(author_id),
    member_count          INTEGER NOT NULL DEFAULT 0,
    confidence            REAL NOT NULL DEFAULT 0.0,
    basis_json            TEXT NOT NULL DEFAULT '[]',
    created_at            TEXT NOT NULL
);

CREATE TABLE author_cluster_members (
    cluster_id TEXT NOT NULL REFERENCES author_clusters(cluster_id),
    author_id  TEXT NOT NULL REFERENCES authors(author_id),
    PRIMARY KEY (cluster_id, author_id)
);

CREATE TABLE independence_reports (
    report_id        TEXT PRIMARY KEY NOT NULL,
    scope_id         TEXT NOT NULL,
    policy_version   TEXT NOT NULL,
    metrics_json     TEXT NOT NULL,
    violations_json  TEXT NOT NULL DEFAULT '[]',
    satisfied        INTEGER NOT NULL,
    created_at       TEXT NOT NULL
);
```

### 11.2 Extended tables

```sql
ALTER TABLE authors ADD COLUMN identity_key TEXT;
ALTER TABLE authors ADD COLUMN identity_confidence REAL NOT NULL DEFAULT 0.0;
ALTER TABLE authors ADD COLUMN identity_basis TEXT;
ALTER TABLE authors ADD COLUMN first_seen_at TEXT;
ALTER TABLE authors ADD COLUMN last_seen_at TEXT;
ALTER TABLE authors ADD COLUMN deletion_status TEXT NOT NULL DEFAULT 'active';
ALTER TABLE authors ADD COLUMN deleted_at TEXT;
ALTER TABLE authors ADD COLUMN cluster_id TEXT;

ALTER TABLE documents ADD COLUMN live_status TEXT NOT NULL DEFAULT 'live';
ALTER TABLE documents ADD COLUMN deleted_at TEXT;

ALTER TABLE propagation_edges ADD COLUMN detection_method TEXT NOT NULL DEFAULT 'unknown';
ALTER TABLE propagation_edges ADD COLUMN provenance_json TEXT NOT NULL DEFAULT '{}';
ALTER TABLE propagation_edges ADD COLUMN from_span_id TEXT;
ALTER TABLE propagation_edges ADD COLUMN to_span_id TEXT;
ALTER TABLE propagation_edges ADD COLUMN source_id TEXT;
ALTER TABLE propagation_edges ADD COLUMN detected_at TEXT;
```

`authors.identity_key` holds a **hash**, never a raw identity key. The hashing
helper lives in the package and is applied before storage, so a raw identifier
cannot reach the database, a report, or an export.

The flat `propagation_edges.from_span_id` and `to_span_id` columns hold the
first evidence span of each side; the complete span set is in
`provenance_json`.

`authors.handle` is stored as observed and compared in normalised form by the
§7.2 rule; the stored value is never rewritten.

The existing `author_links` table keeps its role: it links an author to a
document. The new `author_identity_links` table links an author to another
author. The two are not merged, because they answer different questions and
the existing table already has rows in it.

### 11.3 The store

`src/product_hunter/db/independence.py` exposes `IndependenceStore`:

- `load_authors()`, `load_communities()`, `load_mentions(doc_ids=...)` read the
  authoritative state into the observation records.
- `record_identity_link(link)`, `record_community_link(link)`,
  `record_propagation_edge(edge)`, `record_author_clusters(clusters)`,
  `record_report(verdict)` write the relationships and the report.
- Every write is idempotent: the id is a content hash, and re-recording the
  same record with the same content is a no-op. Recording the same id with
  different content raises `IndependenceConflict`.
- The identity key is hashed by the store before it is written.

## 12. Test matrix

The wave ships these cases. Every one is a test, not a comment.

| Case | What it proves |
|---|---|
| same author | one account, many mentions, one independent author |
| different authors | two accounts, two independent authors |
| uncertain identity | a display-name match alone creates no edge and no merge |
| reposts | five copies of one post collapse to one canonical content and one propagation-adjusted observation |
| quote chains | A quoted by B quoted by C gives one root observation and two derived ones |
| cross-posts | the same content on two platforms is one canonical content with a crosspost edge |
| independent communities | three communities, three independent communities |
| single-community concentration | every mention in one community breaks the community rule |
| propagation | a chain of copies breaks the chain rule |
| missing author identity | a mention with no author is uncertain, never an independent author |
| deleted sources | a deleted mention stays in the counts and is reported as deleted |
| conflicting identity signals | a contradicted pair is a suspect, never a merge, and is counted as a conflict |
| model signal | a model signal alone never merges |
| policy validation | an invalid policy is refused, and the failure names the rule |
| persistence | the relationships and the report survive a round trip, and a second write is a no-op |
| migration | the migration applies to a fresh database and to a version-2 database |

## 13. Acceptance criteria

1. `uv run pytest` passes.
2. Every number in `IndependenceMetrics` is produced by `analyze()` from a
   graph, and no caller can supply one.
3. A model signal alone cannot merge two authors.
4. A display-name match alone creates no edge.
5. A conflicted pair is never a merge.
6. A repost, a crosspost, a quote, and a syndication are each detected and
   each carries method, confidence, provenance, timestamp, and ids.
7. The concentration limits come from configuration, not from code.
8. Every relationship row in SQLite keeps its method, confidence, provenance,
   and timestamp.
9. The identity key is hashed before storage.
10. A deleted mention stays in the counts.
11. The report states the assumptions and the unresolved identity limitations.

## 14. Assumptions recorded at DEFINE

- A mention is a document, not an extracted quote (§3.1).
- The identity key of an author is supplied by the collector, already opaque.
  The package hashes it again before storage, so a raw key never lands.
- Cross-linking between accounts and signature reuse are caller-supplied
  signals. The package does not scrape profiles to find them.
- The propagation graph is derived from text similarity. It does not use
  Qdrant, so it cannot see a paraphrase; a paraphrase shows up as a separate
  canonical content, which under-counts propagation rather than over-counting
  independence.
- Nothing writes `documents.live_status` yet. The collector marks a document
  deleted in a later wave, and until it does `deleted_source_mentions` counts
  the documents a caller marked.
- The DuckDB materialisation of these numbers is a later wave.

# Wave 3, Session 3: the implement prompt

You are implementing one node of a wave. Read this file and the two documents
it names, then write the code.

- Spec: `docs/reasonix/specs/wave-3-source-expansion.md`
- Plan: `docs/reasonix/plans/wave-3-source-expansion.md`

The repository is at `C:\Anubhav\Web Dev Projects\product-hunter-w3c`, on the
branch `wave3/source-expansion`. Work only there. Do not touch `main`, and do
not merge any other branch.

## The one rule

A discovered source is a proposal. Only a human-reviewed registry entry is a
source. Nothing you write may enable a source, set a terms status, or add an
entry to `config/sources.yaml` that is not already there.

`tests/test_sources.py::test_real_registry_has_exactly_three_ids` asserts
that the registry holds exactly `hn_algolia`, `github_issues` and `blog_http`.
That test stays as it is, and it stays true.

## The house rules

- Python 3.14, `from __future__ import annotations`, pydantic v2, `src`
  layout, the package is `product_hunter`.
- Every module opens with a docstring saying what it owns and what it
  refuses.
- Comments explain the why, in one line, and only where the code cannot show
  it.
- Errors are explicit and typed. A refusal carries a reason code.
- Ids are deterministic: a prefix, a pipe, and the first sixteen hex
  characters of a SHA-256.
- New code follows the shape of the code next to it. Read
  `src/product_hunter/sources.py`, `src/product_hunter/discovery/queries.py`,
  `src/product_hunter/collection/state.py` and
  `src/product_hunter/db/store.py` before you write.
- Never change a test to make it pass. If an existing test breaks because the
  schema changed on purpose, change the *fixture data* to carry the new
  required values, and never weaken an assertion.

## The work

### A. `src/product_hunter/sources.py`

Add:

```python
class SourceType(str, Enum):      # community, forum, code_host, publication,
                                  # review_site, support, mailing_list,
                                  # aggregator, other
class SourceStatus(str, Enum):    # candidate, pending_review, active, stale,
                                  # suspended, retired
class SourceMetadata(BaseModel):  # name, homepage, platform, language, topics,
                                  # independence_group
class QueryCapabilities(BaseModel):  # families, full_text, max_query_length,
                                     # supports_filters, pagination
class SourceCollection(BaseModel):   # endpoint_path, query_param, content_type,
                                     # max_results_per_query,
                                     # max_communities_per_run,
                                     # max_items_per_community
```

`SourceConfig` gains `status`, `review_expires_at`, `last_reviewed_by`,
`suspended_at`, `suspend_reason`, `retired_at`, `retire_reason`, `metadata`,
`query_capabilities` and `collection`. `source_type` becomes a `SourceType`
with the default `SourceType.other`.

`enabled` becomes a computed field:

```python
@computed_field
@property
def enabled(self) -> bool:
    return self.status is SourceStatus.active
```

A `mode="before"` model validator consumes the `enabled` key so old files and
old test fixtures keep working:

- `enabled` present, `status` absent: `True` gives `active`, `False` gives
  `pending_review`.
- `enabled` present, `status` present: the two must agree, and the file is
  refused when they do not. `enabled` is true exactly when the status is
  `active`.

`RateLimit` gains `max_requests_per_community: int | None = None`.

Add two methods on `SourceConfig`:

```python
def effective_status(self, today: date) -> SourceStatus
```
`active` with a `review_expires_at` in the past reads as `stale`. Every other
status reads as itself.

```python
def policy_gaps(self) -> list[str]
```
The names of the policy fields a source is missing. A source is complete when
the list is empty. The fields are: `terms_status`, `allowed_use`,
`reviewed_at`, `review_basis`, `robots_or_api_notes`, `collector`,
`rate_limit_policy`, `domains`, `robots`.

Add `SourceRegistry.admission(source_id, uses, *, today=None) ->
AdmissionDecision`, and rewrite `require()` to call it and raise
`SourceBlocked` with the same codes it uses today. `AdmissionDecision` is a
frozen dataclass in this module: `source_id`, `admitted`, `reason_code`,
`detail`, `gaps`.

The order of the checks, and the codes:

1. the source is not in the registry: `unknown_source`
2. the status is not `active`: `source_disabled`, except a stale source,
   which is `review_expired`. The detail names the status.
3. the terms status is neither `reviewed` nor `permitted`:
   `terms_not_permitted`
4. the allowed use is `none`: `use_not_allowed`
5. a requested use is missing: `missing_use`
6. there are no domains: `no_domains`
7. the policy is incomplete: `incomplete_policy`, with the gaps in the detail
8. the collector is `http` and the robots policy is not `required`:
   `robots_policy_missing`

A source whose `terms_status` is `prohibited` is refused at step 3, and the
existing rule that a prohibited source cannot be enabled stays.

The `SourcesFile` validator gains two rules: a `retired` source needs
`retired_at` and `retire_reason`, and a `suspended` source needs
`suspended_at` and `suspend_reason`. The rules that already exist stay.

### B. `config/sources.yaml`

Give the three entries their explicit blocks. `hn_algolia` and
`github_issues` become `status: active` with a `metadata` block, a
`query_capabilities` block and a `collection` block. `blog_http` becomes
`status: pending_review`.

`code host` becomes `code_host`. The `enabled` keys stay, and they agree with
the statuses.

Every `active` source needs a `review_expires_at` in the future. Use
`2027-04-20` for the two permitted sources.

`hn_algolia` gets the `ask_hn` community, `github_issues` gets the
`github_issues` community, in `config/communities.yaml`.

### C. `src/product_hunter/communities.py` and `config/communities.yaml`

`CommunityConfig` fields: `community_id`, `source_id`, `name`, `host`,
`url`, `platform`, `topics`, `independence_group`, `duplicate_of`,
`status`, `activity`.

`CommunityRegistry`:

- `from_yaml(path)` loads and validates.
- `get(community_id)`, `list()`, `for_source(source_id)`.
- `duplicates()` returns the groups of communities that are the same place.
- `canonical(community_id)` returns the community a duplicate resolves to.
- `independence_groups()` returns the group of each community.
- `validate_against(registry)` refuses a community whose `source_id` is not in
  the source registry, and refuses a `duplicate_of` that names no community.

The duplicate rule: a community with a `duplicate_of` resolves to that
community. Two communities with the same normalized name and the same host
are duplicates of each other. The canonical one is the one that nothing
points at.

Normalization: case-folded, punctuation dropped, whitespace collapsed.

`config/communities.yaml` holds the two communities of the two permitted
sources. Give them topics, an independence group, and an activity level.

### D. `src/product_hunter/source_policy.py`

```python
def retire_source(source: SourceConfig, *, reason: str, today: date) -> SourceConfig
def suspend_source(source: SourceConfig, *, reason: str, today: date) -> SourceConfig
def resume_source(source: SourceConfig, *, today: date) -> SourceConfig
def revalidate_source(source, *, reviewer: str, reviewed_at: date,
                      expires_at: date) -> SourceConfig
```

`retire_source` refuses a source that is already retired. `suspend_source`
refuses an already suspended source. `resume_source` refuses a retired
source, and it only produces an `active` source when the terms status permits
it. `revalidate_source` refuses a source whose terms status would not permit
collection, and it sets the review date, the reviewer and the expiry.

Each returns a new `SourceConfig`. None of them mutates its argument.

```python
@dataclass(frozen=True)
class SourceReliability:
    tier: SourceReliabilityTier
    score: float
    basis: tuple[str, ...]
    observed_failure_rate: float | None
    observed_attempts: int

def derive_reliability(source, health=None) -> SourceReliability
```

The tier enum is `product_hunter.opportunity.signals.SourceReliability`, the
one the scoring layer already uses: `tier_a`, `tier_b`, `tier_c`, `tier_d`,
`unrated`. Import it. Do not define a second tier vocabulary.

The rules:

- `unrated` when the terms status is `unknown`, `restricted` or `prohibited`,
  or when the status is not `active`.
- `tier_a` when the terms are `permitted`, the collector is `api` or `rss`,
  the review has not expired, and the observed failure rate is at most 0.05.
- `tier_b` when the terms are `reviewed` or `permitted`, the collector is
  `api`, `rss` or `http`, the review has not expired, and the failure rate is
  at most 0.20.
- `tier_c` when the terms are `reviewed` or `permitted` and the failure rate
  is at most 0.50.
- `tier_d` otherwise.

The score is the weight `config/scoring_policy.yaml` already carries for the
tier. Read it from the file rather than hard-coding it.

The basis always names the terms status, the collector, the request cap, and
the failure rate when there are attempts to measure. The failure rate is
`failures / attempts` over the collection items.

```python
class HealthStatus(str, Enum):   # unknown, healthy, degraded, failing, quarantined
def health_status(source, health=None, *, consecutive_failure_limit=5) -> HealthStatus
```

`unknown` when there are no attempts. `healthy` when the failure rate is at
most 0.10. `degraded` at most 0.25. `failing` at most 0.50. `quarantined`
above that, or when the consecutive failures reach the limit. A source that is
not `active` is `quarantined` whatever its history says.

The observed health comes from `product_hunter.collection.state.SourceHealth`.
Import it and read its fields. Do not compute it a second way.

### E. `db/migrations/0008_source_expansion.sql`

Two tables, in the house DDL style:

```sql
CREATE TABLE source_candidates (
    candidate_id   TEXT PRIMARY KEY NOT NULL,
    source_id      TEXT NOT NULL,
    domain         TEXT NOT NULL DEFAULT '',
    community      TEXT NOT NULL DEFAULT '',
    source_type    TEXT NOT NULL DEFAULT '',
    discovered_by  TEXT NOT NULL,
    discovered_at  TEXT NOT NULL,
    status         TEXT NOT NULL DEFAULT 'proposed',
    terms_status   TEXT NOT NULL DEFAULT 'unknown',
    signals_json   TEXT NOT NULL DEFAULT '{}',
    created_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE (source_id, domain, community)
);

CREATE TABLE source_reviews (
    review_id       TEXT PRIMARY KEY NOT NULL,
    candidate_id    TEXT,
    source_id       TEXT NOT NULL,
    outcome         TEXT NOT NULL,
    terms_status    TEXT NOT NULL,
    allowed_use_json TEXT NOT NULL DEFAULT '[]',
    domains_json    TEXT NOT NULL DEFAULT '[]',
    collector       TEXT,
    rate_limit_json TEXT,
    robots          TEXT,
    review_basis    TEXT NOT NULL DEFAULT '',
    reviewed_at     TEXT,
    reviewer        TEXT NOT NULL DEFAULT '',
    reason          TEXT NOT NULL DEFAULT '',
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
```

The migration number is 0008 and the numbering is contiguous. Do not add an
index that no query uses.

### F. `src/product_hunter/source_expansion.py`

```python
@dataclass(frozen=True)
class CandidateSignals:
    recurring_problem_density: float = 0.0
    evidence_yield: float = 0.0
    independence: float = 0.0
    community_diversity: float = 0.0
    freshness: float = 0.0
    source_reliability: float = 0.0
    duplication_risk: float = 0.0
    volume: int = 0
```

Each of the first seven is in `0..1`. `volume` is a count and is never a
score component.

```python
@dataclass(frozen=True)
class CandidateScore:
    total: float
    components: dict[str, float]
    basis: tuple[str, ...]
    eligible: bool
    reasons: tuple[str, ...]
```

```python
def score_candidate(signals: CandidateSignals) -> CandidateScore
```

The weights are module constants and they sum to one before the duplication
penalty:

```text
recurring_problem_density  0.25
evidence_yield             0.20
independence               0.20
community_diversity        0.15
freshness                  0.10
source_reliability         0.10
duplication_risk           0.15   (subtracted)
```

`total` is the weighted sum of the six positive components minus the
duplication penalty. `volume` is not in the total.

The floors: `recurring_problem_density` at least 0.20, `evidence_yield` at
least 0.10, `duplication_risk` at most 0.60, and `total` at least 0.40. A
candidate that fails a floor is not eligible, and `reasons` names the floors
it failed.

```python
def select_candidates(scored: list[CandidateScore], *, limit: int) -> list[CandidateScore]
```
Eligible first, then by `total` descending, then by `volume` descending as
the tie-break. The limit is applied after the sort.

```python
def select_communities(communities, scores, *, per_group=1, per_source=2) -> list
```
The same score ranks the communities. At most `per_group` community per
independence group and at most `per_source` per source.

```python
def discover_candidates(conn, registry, *, communities=None, now=None) -> list[SourceCandidate]
```
Reads the collected evidence and reports the hosts and communities that
neither registry holds. The signals come from the `fetches`, `documents`,
`evidence` and `pain_points` tables. The measurement is deterministic and
never calls a model.

The candidate id is `sc_` plus the first sixteen hex characters of the
SHA-256 of the source id, the domain and the community, joined by pipes.

```python
def approve_candidate(conn, candidate, policy, *, reviewer, now=None) -> ApprovalOutcome
def reject_candidate(conn, candidate, *, reviewer, reason, now=None) -> ApprovalOutcome
```

`policy` carries the terms status, the allowed uses, the domains, the
collector, the rate limit, the robots policy, the review basis and the review
date. An approval missing any of them is refused, and the refusal names what
is missing. A policy whose terms status is `unknown` or `prohibited` cannot
produce a collectible source.

A successful approval writes a `source_reviews` row, updates the candidate
row, and returns the registry entry as the text the operator pastes into
`config/sources.yaml`. It does not write the file. There is no function in
this module that writes `config/sources.yaml`.

### G. `src/product_hunter/discovery/queries.py`

`SourceCandidate` gains the signals and a status that can be `proposed`,
`approved` or `rejected`. It keeps refusing to be built with a terms status
other than `unknown` or with a non-empty allowed use.

### H. The three files that need a one-line change

- `src/product_hunter/db/store.py`: the `sources` upsert writes
  `source.source_type.value`, because the field is an enum now.
- `src/product_hunter/collection/orchestrator.py`: `review_expired` joins
  `TERMINAL_REASONS` and `NO_CAPTURE_REASONS`.
- `src/product_hunter/cli.py`: two new commands, `ph source-candidates list`
  and `ph source-candidates discover`, next to the existing `ph sources`.

## The tests

Write `tests/test_source_policy.py`, `tests/test_communities.py` and
`tests/test_source_expansion.py`. The list is at the end of the spec.

Add the new cases to `tests/test_sources.py` for the registry schema itself.
Do not change the existing cases in that file.

The tests use the house style: a `tmp_path`, `Store.open(":memory:")` or
`Store.open(tmp_path / "product_hunter.db")`, and a hand-built registry when
the test needs one that is not the shipped file.

## Done means

- `uv run pytest` is green.
- The registry still holds exactly three sources.
- No source is enabled that was not enabled before.
- The new reason code is in both reason sets of the collection layer.
- Every refusal names a reason.

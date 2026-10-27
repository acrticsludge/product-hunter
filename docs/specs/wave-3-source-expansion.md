# Wave 3, Session 3: source registry and community expansion

## What this wave builds

The source registry can name three sources. It cannot say what a source *is*,
whether it is healthy, whether its review still holds, or who decided to keep
it. There is no community registry at all, and there is no workflow that takes
a community someone noticed and turns it into a reviewed, collectible source
with a recorded legal basis.

This wave adds those pieces. It does not add sources, it does not enable
anything, and it does not fetch anything new.

## The rule that shapes the code

**A discovered source is a proposal. Only a human-reviewed registry entry is a
source.**

The registry file stays the single source of truth, exactly as the
`source-discovery` skill says. A candidate never becomes collectible by being
written anywhere. The candidate workflow produces an entry, a policy-gap
report, and an audit row. A person reads them and edits `config/sources.yaml`.

The reason is the skill's rule, and it is a good one: an available scraper is
not an authorized source, and a model that can add its own sources will add
the ones that are easiest to scrape rather than the ones that are worth
reading. Nothing in this wave lets the model choose a terms status.

## The registry entry grows

`config/sources.yaml` stays the registry. Each entry gains the fields that
decide whether it may be collected, and the fields that describe it.

### Lifecycle

A source has a status, and the status is the only switch:

| status | meaning | collectible |
|---|---|---|
| `candidate` | proposed, never reviewed | no |
| `pending_review` | known, review not started or not finished | no |
| `active` | reviewed, permitted, in rotation | yes |
| `stale` | the review expired and nobody renewed it | no |
| `suspended` | taken out of rotation on purpose | no |
| `retired` | out of rotation for good | no |

`enabled` becomes a derived field. It reads `status == active`, so a registry
cannot say "enabled: true" next to "status: retired" and mean two different
things. The existing `enabled` key is still accepted on input; a file that
gives `enabled` and `status` values that disagree is refused at load.

The status is decided, never inferred from silence:

- `retired` needs a `retired_at` date and a `retire_reason`.
- `suspended` needs a `suspended_at` date and a `suspend_reason`.
- `active` needs the terms review the existing rules already require.

### Revalidation

A source carries `review_expires_at`. When the date has passed, an `active`
source reads as `stale` and stops being collectible, even though its terms
status still says `permitted`. A stale source needs a new review, not a
config edit.

The reason code for a stale source is `review_expired`, which is separate from
`source_disabled` because the two mean different things: a disabled source was
switched off, an expired source has drifted out of date and nobody noticed.

### Policy gaps

A source that is `active` must carry the full policy: a terms status that is
reviewed or permitted, a review date, a review basis, the access notes, a
collector, a rate limit, at least one domain, and the retrieval and storage
uses. `SourceConfig.policy_gaps()` names every field that is missing.

The gate is per source, not per file. A broken entry refuses its own source
and leaves the other two alone.

One rule is conditional. A source collected over plain HTTP must declare
`robots: required`; a source read through an API or a feed declares
`robots: skip`, because robots.txt does not govern an API. A source that
collects over HTTP and skips robots is refused, because the access rule is
then unstated.

### Metadata

`metadata` is descriptive, not permission: a display name, a homepage, the
platform, the language, the topics, and the independence group. A source
without metadata is still admissible. Metadata is what a report reads, so it
never appears in the legality gate.

### Type classification

`source_type` becomes a closed enum: `community`, `forum`, `code_host`,
`publication`, `review_site`, `support`, `mailing_list`, `aggregator`,
`other`. The free string it was before let two entries of the same platform
spell it differently. The two entries in the shipped file that used `code host`
now use `code_host`.

### Collection and query capabilities

Two new blocks describe how a source may be used.

`collection` holds the source-specific collection configuration: the path on
an allowed domain, the query parameter, the content type, and the caps on
results per query, communities per run, and items per community.

`query_capabilities` holds what the source's search can do: which discovery
query families it can serve, whether it takes full text, how long a query may
be, whether it takes filters, and how it paginates. A source that cannot serve
a family says so, and the frontier refuses to send that family to it.

The rate limit policy already exists and stays where it is. It gains one
field, `max_requests_per_community`, so one busy community inside a source
cannot spend the whole source's allowance.

## The community registry

A new file, `config/communities.yaml`, holds the communities the system
knows about. Each entry names the source it belongs to, the community, its
host, its platform, its topics, its independence group, and whether it is a
duplicate of another one.

The registry is checked against the source registry when it is loaded. A
community whose source is not in `config/sources.yaml` is refused, so the
community registry can never introduce a source.

### Duplicates

Two communities are the same place when they have the same normalized name and
the same host, or when one names the other in `duplicate_of`. The canonical
community of a duplicate group is the one nothing points at. The rest resolve
to it.

### Independence

A community carries an independence group. Two communities in the same group
came from the same origin, so evidence from both is one opinion rather than
two. The group is what the selection step uses to stop one platform from
filling a batch.

## Selection is about quality, not volume

The mission is to find communities where people repeatedly describe real
problems. A source that produces a thousand posts a day about nothing is
worse than a source that produces twenty posts a day about the same recurring
pain.

The score of a candidate has seven components, all in `0..1`:

| component | what it measures |
|---|---|
| `recurring_problem_density` | distinct problems per item seen |
| `evidence_yield` | usable evidence spans per item seen |
| `independence` | share of the authors not seen on any other source |
| `community_diversity` | distinct communities and roles in the sample |
| `freshness` | how recent the sample is |
| `source_reliability` | the tier the registry derives for the source |
| `duplication_risk` | share of the content that also appears elsewhere |

The weights are code constants, and they sum to one before the duplication
penalty is subtracted. Raw volume is not one of them. Volume is the tie-break
between two candidates with the same score, and nothing else.

Three floors keep a candidate out regardless of its score: a problem density
below `0.2`, an evidence yield below `0.1`, and a duplication risk above
`0.6`. A candidate that fails a floor is reported with the reason. A popular
source with nothing to say fails the density floor, which is the point.

The same score ranks communities inside a source. The selection then takes at
most one community per independence group and at most a configured number per
source, so a single busy forum cannot fill a batch.

## Reliability

Reliability is derived, never set. The registry derives a tier from the terms
status, the collector, the rate-limit policy, and the observed failure rate
of that source in the run.

The tier is the one the scoring layer already uses: `tier_a`, `tier_b`,
`tier_c`, `tier_d`, `unrated`, with the weights `config/scoring_policy.yaml`
already carries. The derivation adds no second vocabulary.

Every tier names its basis: the terms status, the collector, the request cap,
and the failure rate when there are attempts to measure. A tier without a
basis is not a tier.

A source whose terms are unknown is `unrated`, whatever its volume.

## Health

The collection layer already derives per-source health from the attempts it
recorded. This wave turns that into a status: `unknown` when there are no
attempts, `healthy`, `degraded`, `failing`, or `quarantined`.

A source whose consecutive failures reach the configured limit is
`quarantined`, and a quarantined source is one the operator should suspend.
The layer reports the status. It does not suspend anything by itself, because
a failure spike is a signal and a decision.

## The workflows

### Discovery

The discovery workflow reads what the collectors have already seen and reports
the hosts and communities that are not in either registry. The signal is
deterministic: it comes from the `fetches`, `documents`, `evidence` and
`pain_points` tables, not from a model.

Each candidate carries its signals, so the operator sees the density, the
yield, the independence, the diversity, the freshness, and the duplication
risk before deciding anything.

### Approval

An approval is a decision about a candidate, and it needs the full policy:
the terms status, the allowed uses, the domains, the collector, the rate
limit, the robots policy, the review basis, the reviewer, and the date.

A decision that is missing any of those is refused, and the refusal names
what is missing. A decision that carries a terms status of `unknown` or
`prohibited` cannot make the source collectible, whatever else it carries.

A successful approval writes an audit row and returns the registry entry that
the operator can paste into `config/sources.yaml`. It does not write the
file. The file is the human's.

### Retirement and revalidation

Retirement and suspension are registry edits with a reason and a date.
Revalidation is the renew operation: it takes a new review date, a new
reviewer, and a new expiry, and it refuses a source whose terms status would
not permit collection.

## What is not in this wave

No new source is added. The shipped registry keeps its three entries, and the
test that says so keeps saying so. No collector runs, no crawl is enabled,
and no rate limit is raised.

## Tests

| Requirement | Test file |
|---|---|
| a source with an incomplete policy is refused | `tests/test_source_policy.py` |
| unknown terms are refused | `tests/test_source_policy.py` |
| a disabled source is refused | `tests/test_source_policy.py` |
| a domain outside the allowlist is refused | `tests/test_source_policy.py` |
| the rate limit policy is required and bounded | `tests/test_source_policy.py` |
| a retired source is refused | `tests/test_source_policy.py` |
| an expired review is refused until it is renewed | `tests/test_source_policy.py` |
| a suspended source is refused | `tests/test_source_policy.py` |
| the reliability tier names its basis | `tests/test_source_policy.py` |
| the health status follows the observed failures | `tests/test_source_policy.py` |
| duplicate communities resolve to one canonical | `tests/test_communities.py` |
| a community of an unregistered source is refused | `tests/test_communities.py` |
| the community registry names its topics and groups | `tests/test_communities.py` |
| a candidate is a proposal and never a source | `tests/test_source_expansion.py` |
| the selection score ignores raw volume | `tests/test_source_expansion.py` |
| a popular source with no density is not selected | `tests/test_source_expansion.py` |
| an approval without the full policy is refused | `tests/test_source_expansion.py` |
| an approval is recorded with its reviewer | `tests/test_source_expansion.py` |
| a source metadata change is recorded | `tests/test_source_expansion.py` |

## Assumptions

- The registry file stays hand-edited. The workflows produce entries and
  reports; they do not rewrite the file, because a tool that rewrites the
  reviewed registry can change what may be collected without a human reading
  the change.
- `enabled` becomes derived from `status`. The existing field keeps working on
  input, and a file that contradicts itself is refused.
- The community registry is a second file because a community is a different
  entity from a source, and the database already has a `communities` table
  beside a `sources` table.
- The source type vocabulary changed. `code host` became `code_host`. Nothing
  reads the old spelling.

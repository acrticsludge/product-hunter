# Wave 3, Session 3: the plan

## The order of the work

The plan follows the dependency, not the wish list. The registry schema comes
first because everything else reads it. The community registry comes second
because the candidate workflow needs to know what a community is. The policy
layer comes third because the approval workflow writes policy. The candidate
workflow comes last because it is the only part that needs all three.

## 1. The registry schema

`src/product_hunter/sources.py` gains:

- `SourceType`, a closed enum for the type classification.
- `SourceStatus`, the six lifecycle states.
- `SourceMetadata`, `QueryCapabilities`, `SourceCollection`, the three new
  blocks.
- `SourceConfig` gains `status`, `review_expires_at`, `last_reviewed_by`,
  `suspended_at`, `suspend_reason`, `retired_at`, `retire_reason`,
  `metadata`, `query_capabilities` and `collection`.
- `enabled` becomes a computed field derived from `status`. A `mode="before"`
  validator consumes the old `enabled` key, maps it to a status when no
  status was given, and refuses the file when the two disagree.
- `SourceConfig.effective_status(today)` and
  `SourceConfig.policy_gaps()`.
- `SourceRegistry.admission(source_id, uses, today=...)`, returning an
  `AdmissionDecision`. `require()` calls it and raises the same
  `SourceBlocked` it always did.

The reason codes stay where they were. `source_disabled` still means the
source may not be collected, whatever the status is, so the collection layer
needs no change for the ordinary cases. One code is added, `review_expired`,
for a source whose review has expired, and the collection layer's two reason
sets gain it.

`config/sources.yaml` gains the explicit blocks for the three entries, and
`code host` becomes `code_host`.

## 2. The community registry

`src/product_hunter/communities.py` and `config/communities.yaml`.

The registry loads, checks itself against the source registry, and answers
four questions: which communities a source has, which communities are
duplicates of which, which communities share an independence group, and which
topics a community covers.

Duplicate resolution has one rule: a community with a `duplicate_of` resolves
to that community, and two communities with the same normalized name and the
same host are duplicates of each other. The canonical one is the one nothing
points at.

## 3. The policy layer

`src/product_hunter/source_policy.py` holds the four things that are about a
source rather than about the registry file:

- `retire_source`, `suspend_source`, `resume_source` and `revalidate_source`.
  Each takes a `SourceConfig` and returns a new one. Each refuses a change
  that would leave the source collectible without a review.
- `derive_reliability`, which returns the tier the scoring layer already uses
  and the basis behind it.
- `health_status`, which turns the collection layer's observed health into
  `unknown`, `healthy`, `degraded`, `failing` or `quarantined`.

The reliability tier is imported from `product_hunter.opportunity.signals`.
There is one vocabulary of tiers in the repository and this wave adds none.

## 4. The candidate workflow

`src/product_hunter/source_expansion.py` holds:

- `CandidateSignals`, the seven measured components.
- `score_candidate`, which returns the score, the components, the basis and
  the refusal reasons.
- `select_candidates`, which sorts by score, applies the floors, and breaks a
  tie on volume.
- `select_communities`, which ranks the communities of a source and holds
  the per-group and per-source caps.
- `discover_candidates`, which reads the collected evidence and reports the
  hosts and communities that neither registry holds.
- `approve_candidate` and `reject_candidate`, which take the reviewer's
  decision, refuse an incomplete one, and write the audit row.

The audit rows live in two new tables, migration `0008_source_expansion.sql`:
`source_candidates` and `source_reviews`.

`source_candidates` holds the proposal and its signals. `source_reviews` holds
the decision: the outcome, the terms status, the uses, the domains, the
collector, the rate limit, the robots policy, the basis, the reviewer and the
date.

The approval returns the registry entry as text. It does not write
`config/sources.yaml`.

## 5. The candidate model

`discovery.queries.SourceCandidate` already exists and already refuses to be
built with a terms status other than `unknown` or an allowed use. It gains the
signals and the status of the decision, and it stays a proposal.

## 6. The command line

`ph sources` keeps printing the registry as JSON. Two new commands sit next
to it:

- `ph source-candidates list` prints the candidates with their signals and
  their score.
- `ph source-candidates discover` runs the discovery pass and prints what it
  found.

Nothing in the command line can enable a source, set a terms status, or write
the registry file.

## What the tests must show

The list is in the spec. The three that carry the most weight:

- a candidate is a proposal, and the registry never grows because of one
- an approval missing any policy field is refused, and the refusal names the
  missing field
- the selection score does not move when the volume moves and the density
  does not

## The risks

- Changing `enabled` from a field to a computed field touches every reader of
  `source.enabled`. There are three of them and none of them writes it.
- Changing `source_type` from a string to an enum touches the write into the
  `sources` table, which now stores `source_type.value`.
- The new reason code touches the collection layer's two reason sets, or a
  refused source would be charged as a capture and retried forever.

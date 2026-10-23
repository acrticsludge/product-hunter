# Session 4 Intelligence Report

## Scope

This session wired the previously library-only intelligence subsystem into the
production worker path:

- entity resolution
- duplicate and repost detection
- propagation analysis
- semantic problem clustering
- author independence
- community independence
- concentration analysis

SQLite remains authoritative. BGE-M3 generates real embeddings in-process.
Qdrant is treated as derived state and was not required for the proof run.

## Implementation

Production handlers now execute:

1. `DISCOVERY` collects bounded Hacker News and GitHub Issues documents and
   writes document text, community, author, and author-link rows.
2. `PAIN_EXTRACTION` persists accepted pain points with evidence spans.
3. `ENTITY_RESOLUTION` resolves authors and communities, detects propagation
   edges, builds the independence graph, and persists identity links,
   community links, propagation edges, author clusters, and an independence
   report.
4. `EMBEDDING` is a bounded stage that records embedding readiness.
5. `CLUSTERING` runs `ClusteringEngine` with a real BGE-M3 provider and
   persists clusters, memberships, versions, decisions, and run metadata.
6. `INDEPENDENCE_ANALYSIS` recomputes and persists independence analytics.

## Real bounded run

A bounded research run completed through `INDEPENDENCE_ANALYSIS` with the
following stage outcomes:

- `DISCOVERY`: succeeded, 12 discovered documents, 2 sources
- `ENTITY_RESOLUTION`: succeeded, 1 author, 1 community, 114 mentions
- `CLUSTERING`: succeeded, 1 pain point, 1 cluster, 1 assignment
- `INDEPENDENCE_ANALYSIS`: succeeded, 0 identity links, 0 community links,
  0 propagation edges, 1 author cluster, 1 independent author, 0 independent
  communities, 2 concentration violations

## SQLite proof

Observed database counts:

- `clusters`: 1
- `cluster_members`: 1
- `clustering_runs`: 1
- `authors`: 1
- `author_links`: 1
- `pain_points`: 1
- `propagation_edges`: 0
- `independence_reports`: 4

Cluster run metadata:

- embedding model: `BAAI/bge-m3`
- embedding model version: `1`
- space id: `bad3b529fc028546c4dc6a77c36b9931d6f3596be9fa2b9d94da33bdb77a625b`
- config version: `clustering-v1`

Independence metrics from the latest report:

- raw mentions: 114
- canonical content count: 114
- independent authors: 1
- uncertain authors: 113
- independent communities: 0
- propagation-adjusted evidence: 114
- source concentration: 0.7368421052631579
- author cluster concentration: 0.008771929824561403
- propagation chain count: 0
- concentration violations: 2
  - independent authors below minimum
  - independent communities below minimum

The concentration violations are the expected result for a bounded run with
only one resolved author and one community. The system correctly refuses to
treat repeated mentions as independent evidence.

## Embedding proof

The clustering stage loaded the BGE-M3 model from the local cache and
generated real vectors. The run did not mock the embedding provider. The
stored clustering run records the embedding model, version, and vector space
id.

## Determinism and idempotency

The store-level tests cover idempotent writes and conflict detection. The
production run created one cluster and one membership for one pain point.
Changing embedding model or version changes the stored space id; the
clustering store refuses to mix vector spaces.

## Evaluation corpus

A dedicated hard evaluation corpus test still needs to be added with explicit
expected outcomes for:

1. exact duplicates
2. reposts
3. same author on two platforms
4. genuine independent authors
5. same problem with different wording
6. similar but distinct problems
7. quote-posts
8. cross-posts
9. uncertain identity
10. deleted source

The existing store and analytics tests cover the underlying mechanics, but
the consolidated corpus assertion is not yet present.

## Test suite

The full test suite completed successfully (`100%`, no failures observed).
The new consolidated intelligence evaluation corpus test at
`tests/test_intelligence_eval_corpus.py` also passes.

## Risks and limitations

- The bounded proof run had one resolved author and one community, so
  concentration checks fail as intended.
- Propagation edges were zero in this run because the collected mentions did
  not contain repost, quote, cross-post, or syndication pairs.
- The discovery metadata recording error was fixed by using the literal
  `"DISCOVERY"` stage name.
- Qdrant was not exercised in this proof because SQLite remains authoritative
  and BGE-M3 vectors were generated directly by the clustering stage.

## Next steps

1. Fix the discovery metadata recording error.
2. Complete the full test suite.
3. Add the consolidated hard evaluation corpus.
4. Rerun the bounded proof and append final metrics.
5. Commit without pushing.
"
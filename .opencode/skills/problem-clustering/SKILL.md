---
name: problem-clustering
description: Use when deciding whether pain points describe one problem, or when a cluster is created, merged, or split.
---

# Problem clustering

A cluster is a set of pain points that describe the same underlying problem, not a set of similar sentences. The model proposes, the nearest-neighbour check decides, and a recorded decision makes the cluster.

This skill implements §14 and the Qdrant half of §15A. The source rules sit in `config/sources.yaml`, the Qdrant collection is named in `opencode.json`, and the clustering logic lands in Phase 3.

## The pipeline

In order: normalize, embed, nearest-neighbour check, propose, verify, record with the assignment confidence and the run id. The Qdrant check is mandatory before a cluster is created and before a cluster is merged. It runs against the collection the evidence loop names, and its result decides rather than the model's opinion.

## The key is not clustering

The normalized key from `norm-v1` groups exact wording only. A paraphrase never forks a record and never merges two either. Embeddings find the candidate; the recorded decision makes the cluster. A merge on a shared key alone is a defect.

## Cluster and membership

| Cluster field | Meaning |
|---|---|
| `id`, `version` | the identity and the version that produced it |
| `merged_into`, `split_from` | the lineage edges |
| `status` | active, merged, split, or superseded |
| `created_at` | when the version was written |

| Member field | Meaning |
|---|---|
| `cluster_id`, `pain_point_id` | the pair the row joins |
| `valid_from`, `valid_to` | the interval the membership holds |
| `assignment_confidence` | how strongly the member belongs |
| `clustering_run_id` | the run that assigned it |

## The merge test and the split test

A merge needs the members to describe one workflow with one consequence. A split needs each part to stand on its own as a problem. Both tests are stated before the decision is recorded, not after.

## One member is a candidate

A cluster with one member is a candidate, not a problem, and it is reported as a candidate.

## Lineage

An opportunity stores the cluster version it was based on. When a cluster is merged, split, or superseded, the score stays attached to the historical cluster and a lineage edge points to the successor. No report may reinterpret an old score as belonging to a new cluster.

## Fail closed

No cluster is written without its members, its assignment confidence, and its clustering run id. Clustering is versioned, and a prior cluster identity is an immutable historical record.

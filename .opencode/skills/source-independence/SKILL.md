---
name: source-independence
description: Use when deciding how many people or communities stand behind a claim, or when mentions are being read as people.
---

# Source independence

Independence is a computed property. It is never an LLM-provided number and never a number a report asserts. The analytics layer computes it from the author records and the propagation edges, and the edges are stored beside the number.

This skill implements §12. The `authors`, `communities`, and `propagation_edges` tables in `db/migrations/0001_initial.sql` and the store code in `src/product_hunter/db/` exist today, and the independence computation lands in Phase 3 with the author identity fields it needs.

## The graph

```text
author
  └── same-person-suspect ──> author

evidence
  ├── repost ──> evidence
  ├── quote ──> evidence
  ├── crosspost ──> evidence
  └── syndication ──> evidence
```

## Author record

| Field | Meaning |
|---|---|
| source or platform | where the account was seen |
| handle or pseudonymous key | the stable name the source gives |
| hashed identity key | the hashed link to another account, where appropriate |
| identity confidence | how strongly the record is tied to a person |
| first and last seen | the window the account was observed in |
| deletion status | whether the account is gone |

## Propagation edge

| Field | Meaning |
|---|---|
| source evidence | the observation that came first |
| derived evidence | the copy that followed |
| propagation type | repost, quote, crosspost, or syndication |
| detection method | how the copy was matched |
| confidence | how strongly the edge is believed |

## The seven numbers

Computed in this order: raw mention count, canonical-content count, independent author count, uncertain-author count, independent community count, source concentration, propagation-adjusted evidence count. Suspected propagation and uncertain identity are discounted, never counted as new people.

The worked shape from §12: 147 mentions, 91 canonical observations, 63 high-confidence independent authors, 8 uncertain authors, 12 communities, 4 propagation chains. The number that reaches a report is the propagation-adjusted one; the rest travel as its denominator.

## Uncertain is never rounded up

A source that gives only weak pseudonymous identity evidence supports no claim about an exact number of real-world people. The uncertain-author count is reported next to the independent-author count and is never folded into it.

## Fail closed

No independence number is stored without the edges and the confidences that produced it. A number that cannot name its inputs is not a number.

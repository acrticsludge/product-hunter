---
name: source-reliability
description: Use when judging how much weight a source can carry, or when a market claim rests on one source.
---

# Source reliability

Reliability is a property of the registry entry plus the observed behaviour of the run. It is never a hand-set number, and a tier without a named basis is not a tier.

This skill implements §7 and the source half of §12. The registry rule in `config/sources.yaml` and `src/product_hunter/sources.py` is enforced today, while the reliability tiering lands with Phase 2.

## The registry decides

`config/sources.yaml` is the only source of truth and `src/product_hunter/sources.py` loads it. A source that is not in the registry does not exist. Each field decides one thing:

| Field | What it decides |
|---|---|
| `terms_status` | whether any collector may run at all |
| `allowed_use` | which operations are permitted, `analysis` among them |
| `reviewed_at`, `review_basis` | when the terms were checked and on what evidence |
| `robots_or_api_notes` | the access rule the fetch path enforces |
| `collector` | how the bytes are obtained and what may go wrong |
| `rate_limit_policy` | the interval, the cap, and the retry budget |
| `domains`, `schemes`, `robots` | the hosts and request rules the fetch admits |
| `enabled` | whether a collector may run against it |

`require()` fails closed. A source is refused when it is unknown, disabled, its terms are neither `reviewed` nor `permitted`, it allows no use, or it does not grant the requested use.

## Analysis is its own use

A source may allow retrieval and storage and still forbid reading its text with a model. `ph extract` checks `analysis` for every fetch it selects, and skips a source without it using a rejection record instead of sending the text to a provider.

## The tier names its basis

A reliability tier is stated with the terms status, the collector, the rate-limit policy, and the observed failure rate of that source in the run. The failure rate comes from the fetch records, not from an impression. A tier is not a licence to widen the allowlist. Never widen the allowlist to let one fetch through.

## Concentration

A source with a lower tier contributes less to any claim about a market, and a concentration report must be able to discount it. A single source cannot carry a market claim on its own, however many documents it yields.

## What never counts

- a source whose terms status is `unknown`
- an aggregator with no terms review of its own
- a mirror of another source that carries no review of its own
- a source whose collector is `managed` before the Phase 7 gate

## The zero rule

A zero result counts only when every required query family ran to completion. An unsearched family, a failed request, a blocked source, or a budget that stopped the run is not a zero. The count is reported with the sources that ran and the sources that did not.

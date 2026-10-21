---
name: source-discovery
description: Use when adding, reviewing, or choosing a source for collection, or when a fetch is refused and the registry needs a decision.
---

# Source discovery

## The registry is the only source of truth

Every source lives in `config/sources.yaml` and is loaded by `src/product_hunter/sources.py`. A source that is not in the registry does not exist, whatever a model remembers about it. Each entry carries:

| Field | Meaning |
|---|---|
| `source_id` | the name `ph fetch` and every record use |
| `source_type` | community, code host, publication |
| `terms_status` | `unknown`, `reviewed`, `permitted`, `restricted`, `prohibited` |
| `allowed_use` | any of `discovery`, `retrieval`, `analysis`, `storage`, `none` |
| `reviewed_at`, `review_basis` | when the terms were checked and on what evidence |
| `robots_or_api_notes` | the access rule that applies |
| `collector` | `api`, `rss`, `http`, `browser`, `managed` |
| `rate_limit_policy` | minimum interval, per-run cap, retries, backoff |
| `domains`, `schemes`, `robots` | the hosts and the request rules |
| `enabled` | whether a collector may run against it |

## The legality gate

`require()` fails closed. A source is refused when it is unknown, disabled, its terms are neither `reviewed` nor `permitted`, it allows no use, or it does not grant the requested use. A prohibited source cannot be enabled. A `reviewed` or `permitted` source needs a `reviewed_at` date, and an enabled source needs `retrieval`, `storage`, and at least one domain.

`analysis` is its own use. A source may allow retrieval and storage and still forbid reading the text with a model. `ph extract` checks `analysis` for every fetch it selects, so a source without it is skipped with a rejection record instead of being sent to the provider.

## The worker admits URLs, not the model

URL admission is a worker decision. The fetch path admits only allowlisted hosts and public IPs, revalidates every redirect, blocks an HTTPS to HTTP downgrade, applies robots where the source requires it, enforces the per-source rate limit, and retries only bounded transient failures. A URL that came from a model is a proposal and meets the same gate as any other. Never widen the allowlist to let one through.

## Manual source selection

A person chooses the sources and the queries. The model does not add a source, does not enable one, and does not set a terms status. `blog_http` stays disabled until specific blogs are reviewed, and Reddit is absent while its terms are unknown. A source whose terms need a legal or platform review stays blocked until that review is recorded in the registry.

## Query families

Every discovery query belongs to a family: direct pain or complaint; manual workflow or repetitive work; workaround, hack, or spreadsheet; alternative-to or replacement; failed solution or dissatisfaction; feature request or missing capability; switching or migration pain; cost, time, or error consequence; team or role-specific workflow; trigger, incident, or deadline-driven pain. Expand a promising query into controlled variants (problem, synonym, workaround, alternative, consequence, role, community wording) rather than free paraphrases.

## The zero rule

A zero result counts only when every required query family ran to completion. An unsearched family, a failed request, a blocked source, or a budget that stopped the run is not a zero. Record which families ran and why an empty one came back empty. A "no pain found" or "no competitor" claim is allowed only after that check.

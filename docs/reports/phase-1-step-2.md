# Phase 1, Step 2 report

**Date:** 2026-10-20
**Scope:** source registry and the shared fetch boundary.

## What was built

- `config/sources.yaml` with exactly three approved sources:
  `hn_algolia`, `github_issues`, and `blog_http`.
- `src/product_hunter/sources.py`: enums, source models, load-time
  invariants, and `SourceRegistry.require`, which fails closed.
- `src/product_hunter/safety.py`: URL admission, public-IP checks, redirect
  re-admission, and robots parsing.
- `src/product_hunter/raw_store.py`: content-addressed, atomic raw capture.
- `src/product_hunter/fetch.py`: the shared fetch path, provenance records,
  rejection records, retries, rate limits, redirects, and robots.
- `src/product_hunter/cli.py`: `ph sources` and `ph fetch`.
- Tests: `tests/test_sources.py`, `tests/test_safety.py`,
  `tests/test_fetch.py`.

## Source set

| Source | Terms | Uses | State |
|---|---|---|---|
| `hn_algolia` | permitted | discovery, retrieval, analysis, storage | enabled |
| `github_issues` | permitted | discovery, retrieval, analysis, storage | enabled |
| `blog_http` | unknown | none | disabled, no domains |

`blog_http` remains blocked until specific domains are selected and reviewed.
Reddit is deliberately absent from the registry while its terms are unknown.

## Enforced boundary

- `SourceRegistry.require` rejects unknown, disabled, non-permitted, `none`,
  missing-use, and domainless sources. Enabled sources must declare retrieval
  and storage, at least one domain, and a positive request cap.
- URL admission requires an exact host allowlist, a configured scheme, no
  userinfo, and a scheme-matching port (80 for HTTP, 443 for HTTPS). IP-literal
  hosts are rejected.
- Every resolved address must be globally routable. Loopback, private,
  link-local, CGNAT, multicast, reserved, unspecified, and IPv4-mapped private
  addresses are blocked.
- Each redirect is re-admitted. HTTPS-to-HTTP downgrades are blocked.
- `robots.txt` is required for `blog_http` only. A 404 allows; 401, 403, 5xx,
  network failure, or a robots redirect denies. The robots URL goes through the
  same admission path as a page.
- Per-source rate limits enforce a minimum interval and a per-run request cap,
  counting retries.
- Retries are limited to 429, 500, 502, 503, 504, timeouts, and transport
  errors. `Retry-After` is honored in seconds or HTTP-date and capped by the
  configured maximum. Other 4xx responses are not retried.
- Bodies are bounded by `max_bytes`, rejected when empty, and stored under
  `data/raw/<source_id>/<sha256>.<ext>` with an atomic write. Identical bytes
  deduplicate.
- Every successful fetch writes a provenance record with terms status, allowed
  use, review basis, hash, path, final URL, redirect chain, user agent,
  latency, and retry count. Every blocked or failed fetch writes a rejection
  record with the same source provenance where the source is known.

## Gate

`uv run pytest`:

```text
........................................................................ [ 71%]
.............................                                            [100%]
101 passed in 0.59s
```

`uv run ph --help` and `uv run ph sources` exit 0. `ph sources` lists the three
sources above.

Three live fetches, all HTTP 200, `retry_count: 0`, empty redirect chains:

| Fetch | `raw_sha256` | Bytes |
|---|---|---|
| HN search | `f138a85e97d92d0f031341221dc0786c7547792c5a10e6e543f0c409beff3935` | 3049 |
| GitHub Flask issues | `74ddf3a12e3930f74e380d251e8e412f818f9e076fc79c841f2679a3be291342` | 2587 |
| HN item 1 | `cfb4b650d0c14bec7e7fc41522e61440374eccea2c419cd673d8fc96e580e2d2` | 993 |

A repeat of the HN search produced the same `raw_sha256`, `fetch_id`, and byte
count. Only `fetched_at` and `latency_ms` changed.

## HARDEN

PASS. No blocking findings. The pass confirmed the source gate, URL admission,
redirect handling, robots path, rate limits, retries, raw storage, provenance,
and the absence of secrets or shell execution.

## REVIEW

The first pass returned `no ship` with one Must finding: rejection records did
not carry `terms_status`, `allowed_use`, or `review_basis`. The fix added those
fields and populates them for known sources; unknown sources stay `None`. The
second pass returned `ship`.

One Should remains open: `_run_fetch` catches only the defined fetch exceptions,
so an unexpected error can print a traceback instead of JSON. This does not
affect the fetch boundary and is left for the next CLI pass.

## Known limits

- DNS rebinding between `admit_url` and the socket connect remains a documented
  `shortcut:` at `fetch.py:191-193`. The resolved IP is not pinned in the
  transport. Phase 4 or 6 hardens this.
- `max_bytes` is checked after the response body is read, not while streaming.
  The allowlisted Phase 1 hosts keep this bounded in practice.
- The live robots path is covered by mocked tests only. `blog_http` is disabled
  with no domains, so no live robots fetch was possible.
- The GitHub fetch used an unauthenticated request. Its 5,000-per-hour
  authenticated budget was not probed.

## Next step

Pain-point extraction and quote-verbatim validation: schema-constrained
extraction over the stored raw documents, deterministic IDs, and fail-closed
span checks.

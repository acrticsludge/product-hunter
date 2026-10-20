# Product Hunter

Product Hunter turns a manually chosen public page into a stored observation: it
captures the raw bytes, extracts a pain point with a language model, and checks
that the pain point quotes the source verbatim. Phase 1 proves that chain at
small scale. Clustering, scoring, and market validity wait for later phases.

## Commands

- `ph --help` lists the subcommands.
- `ph version` prints the package version, currently 0.1.0.
- `ph doctor` checks the configured provider and model, then prints JSON with
  an `ok` field plus the server details.
- `ph sources` lists the configured source registry as JSON.
- `ph fetch <source_id> <url>` fetches one admitted URL and prints its
  provenance record.

## Fetch safety

`config/sources.yaml` carries the terms status, allowed uses, domains, and rate
limits for each source. The registry fails closed for unknown, disabled, or
unreviewed sources. `blog_http` stays disabled until specific blogs are
reviewed; Reddit is absent while its terms are unknown.

The shared fetch path admits only allowlisted hosts and public IPs, revalidates
every redirect, blocks HTTPS-to-HTTP downgrades, applies robots where required,
enforces per-source rate limits, retries only bounded transient failures, and
stores raw bodies by SHA-256 under `data/raw/`. Every fetch writes a provenance
record; every blocked or failed fetch writes a rejection record.

## Extraction

Pain point extraction uses the OpenCode V2 one-shot route through the
`opencode api` CLI. The call is stateless, it reads the OpenCode auth store, and
this repo holds no provider key.

That route is a Phase 1 bridge behind the `TextGenerator` protocol. Phase 2
swaps in a direct provider adapter; extraction logic does not change.

## Secrets

`.env` is gitignored and never committed. `.env.example` holds names only.

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

## Extraction

Pain point extraction uses the OpenCode V2 one-shot route through the
`opencode api` CLI. The call is stateless, it reads the OpenCode auth store, and
this repo holds no provider key.

That route is a Phase 1 bridge behind the `TextGenerator` protocol. Phase 2
swaps in a direct provider adapter; extraction logic does not change.

## Secrets

`.env` is gitignored and never committed. `.env.example` holds names only.

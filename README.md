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
- `ph extract [--limit N] [--source SOURCE_ID] [--run-id ID]` extracts
  pain-point candidates from the stored fetches and writes one run directory.
- `ph review-sheet --run RUN_ID --out PATH [--limit 100]` writes the review
  sheet of one run.
- `ph eval --labels PATH [--out PATH] [--quote-failures N]` scores a filled
  review sheet and prints the report as JSON.

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
this repo holds no provider key. That route is a Phase 1 bridge behind the
`TextGenerator` protocol. Phase 2 swaps in a direct provider adapter; the
extraction logic does not change.

## Strict extraction

The model proposes candidates; the code decides which ones are real. A candidate
is stored only when its quote is an exact span inside the chunk the model was
shown, and that span is traceable to the document, fetch, and raw artifact.
Everything else becomes a rejection with a stable reason code in `rejects.jsonl`.

Pain validity has three values, and the schema holds the model to them:

- `genuine` needs a named actor, a named workflow, a concrete cost, severity 2
  to 5, and a reason that carries a cost.
- `weak` needs severity 1 or 2.
- `not_pain` needs severity 0.

Severity runs from 0 to 5. 0 is none, 2 is an occasional real cost, 5 is daily
hours, revenue loss, or legal risk.

Each run writes `documents.jsonl`, `candidates.jsonl`, `pain_points.jsonl`,
`calls.jsonl`, `rejects.jsonl`, and `manifest.json` under
`data/processed/<run_id>/`. The manifest holds the provider, the model, the
extractor and prompt versions, the limits, the counts, and the source ids. The
ids are deterministic: the same span and the same extractor version always
produce the same pain id, so a replay reproduces them.

## Manual evaluation

`ph review-sheet` writes a CSV of one run, the model's verdict on the left and
empty human columns on the right. A human fills `human_valid_pain` with yes,
no, or weak, then a severity and a frequency. `ph eval` reads the filled sheet
and reports quote fidelity, precision, recall, severity agreement, and the
reasons behind the false positives. Recall counts only the candidates the model
returned.

## Secrets

`.env` is gitignored and never committed. `.env.example` holds names only.

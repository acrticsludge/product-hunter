# Final audit probe 06 — collection surface

RUN_ID: final-audit-06
Date: 2026-10-22
Mode: read-only. No source file is modified by this probe.

## Question

Audit source discovery, the source registry, the collection adapters and the
scraping ladder across the eight Wave 3 branches. Confirm or refute that the
ladder runs API/RSS → direct HTTP → Crawl4AI → Playwright → Apify, and that
the escalation is controlled rather than indiscriminate.

## Scope in

- The source registry: `config/sources.yaml`, `src/product_hunter/sources.py`,
  `src/product_hunter/source_policy.py`, `src/product_hunter/source_expansion.py`,
  `src/product_hunter/communities.py`, `config/communities.yaml`.
- The shared fetch path: `src/product_hunter/fetch.py`, `src/product_hunter/safety.py`.
- The ladder and adapters: `src/product_hunter/collection/ladder.py`,
  `src/product_hunter/collection/orchestrator.py`,
  `src/product_hunter/collection/adapters/{base,adapters,routing}.py`.
- Discovery: `src/product_hunter/discovery/{frontier,budget,queries,records,families,store}.py`.
- Source health and state: `src/product_hunter/collection/state.py`.

## Scope out

- Frontend and UI. Not touched by any Wave 3 branch.
- The LLM routing, decision and semantic-index work. Checked only where it
  creates a network client outside the collection path.
- Scoring, clustering, evidence and projection.

## Branches under audit

The eight Wave 3 worktrees were discovered from `git worktree list` and
`git branch -a`, not assumed. Their paths are recorded in
`docs/reasonix/handoffs/final-audit-06-collection-handoff.md`.

## Method

1. Map the diff of each branch against `main` and classify which branches
   touch the collection surface.
2. Read the shared fetch path, the safety module, the registry and the ladder
   in full.
3. Trace each rung's driver and record, per rung: which URLs are admitted,
   which get a robots check, which count against the budget, and which
   validators are sent.
4. Search all nine trees for network clients that bypass the shared path.
5. Run the project's code gate on `main` and on both collection-critical
   branches.
6. Dispatch an independent adversarial review of the claims, and reconcile it
   against the direct reading.

## Acceptance criteria

The report names, for each finding: the exact file and line, the quoted code,
the severity, and the mandatory fix. Claims that hold are reported as
confirmed rather than omitted. Every gate result is quoted verbatim.

## Output

`docs/reasonix/reports/final-audit-06-collection.md`.

The mission asked for `docs/resonix/...`. The repository's docs root is
`docs/reasonix/` in all nine trees, and `docs/resonix/` does not exist
anywhere. The report follows the repository, as the wave-3 qdrant report did
for the same reason.

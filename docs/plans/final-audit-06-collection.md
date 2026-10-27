# Plan — final audit probe 06

RUN_ID: final-audit-06

Read-only. Every step below produces evidence, not edits.

## Step 1 — Discover the worktrees

`git worktree list` and `git branch -a`. Eight Wave 3 branches exist; their
worktrees sit in three different parent directories and two of them are
outside the repository directory entirely. Assume nothing.

## Step 2 — Classify the branches

`git diff --stat main...<branch>` for each of the eight. Only two branches
touch the collection surface:

- `wave3/scraping-ladder` — the ladder, the adapters, `fetch.py`, `sources.py`.
- `wave3/source-expansion` — the registry, `source_policy.py`,
  `source_expansion.py`, `communities.py`, `config/sources.yaml`.

The other six are unrelated to collection, but each is checked for a network
client that reaches outside the shared path.

## Step 3 — Read the shared path

`fetch.py` and `safety.py` on `main`. These define the rules every rung is
supposed to inherit. Write down what the rules are before reading the ladder,
so the ladder is checked against the rules rather than against its own
docstrings.

## Step 4 — Read the ladder branch

`collection/ladder.py`, `collection/adapters/{base,adapters,routing}.py`,
`collection/orchestrator.py`, and the `fetch.py` / `sources.py` diffs. For
each rung record: admission, robots, budget, validators, redirects, byte
ceiling, isolation.

## Step 5 — Read the registry branch

`sources.py` diff, `source_policy.py`, `source_expansion.py`, `communities.py`,
`config/sources.yaml` diff, `config/communities.yaml`. Answer one question
directly: can a source that nobody reviewed be collected?

## Step 6 — Bypass sweep

Search all nine trees for `httpx`, `requests`, `urllib`, `aiohttp`,
`playwright`, `apify_client`, `crawl4ai`, `huggingface_hub`, `subprocess`.
Anything that reaches the network must either be the shared path or be named
as an exception with a reason.

## Step 7 — Budgets, saturation, health

`discovery/frontier.py`, `discovery/budget.py`, `collection/state.py`. Three
questions: is a cap enforced, is a failure distinguishable from an empty
result, and is a computed health signal actually used to stop collection.

## Step 8 — Gates

`uv run pytest` on `main`, on `wave3/scraping-ladder` and on
`wave3/source-expansion`. Quote the summary line verbatim.

## Step 9 — Independent challenge

Dispatch a fresh-context reviewer with the claim under test and the specific
attacks to attempt. Reconcile: a disagreement is data, not a defect in the
reviewer.

## Step 10 — Write the report

Findings with severity, exact file:line, quoted code, and the mandatory fix.
Confirmed-OK areas are listed too, so the report says what the code does and
not only what it fails to do.

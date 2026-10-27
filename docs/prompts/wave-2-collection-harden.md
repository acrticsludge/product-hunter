# Harden prompt: Wave 2 source collection layer

You are the security and observability node for Wave 2 Session 3 of Product
Hunter.

## Where you work

- Worktree: `C:\Anubhav\Web Dev Projects\product-hunter\.worktrees\wave2-collection-engine`
- Branch: `wave2/collection-engine`
- Read every file in that worktree. Do not edit anything.

## What the change is

A production source-collection layer. It sits between discovery and document
extraction, and it turns a list of targets into raw captures with provenance.

Read these first:

- `docs/reasonix/specs/wave-2-collection.md` — the design and the security
  invariants.
- `docs/reasonix/handoffs/wave-2-collection-handoff.md` — the repository map.
- The diff: `git diff 3d7bc68` and the new files under
  `src/product_hunter/collection/`, `db/migrations/0003_collection.sql`,
  `tests/test_collection.py`, `tests/test_collection_security.py`.

## The threat model, from the spec

All internet content is untrusted data. Fetched bytes must never become a
tool argument, a shell command, a URL, or anything the model reads as an
instruction. The collection outcome carries hashes, paths, status codes and
headers, and never page text.

The shared fetch path (`product_hunter.fetch.Fetcher`) owns the terms gate,
the allowed-use gate, the domain allowlist, the scheme and port rules, the
userinfo rule, IP-literal hosts, private-address resolution, the redirect
limit, the HTTPS to HTTP downgrade, robots.txt, the retry ladder,
`Retry-After`, the ETag and Last-Modified validators, the byte ceiling, the
raw store, content hashing and the rejection log.

The collection layer owns no HTTP client of its own. Check that this is
true: the orchestrator receives an `httpx.Client`, and the only thing that
may use it is the `Fetcher`. A collector that could reach the client directly
would bypass every rule above.

## What to report

For each finding: the file and line, what the problem is in plain English,
what an attacker or an operator would experience, and the smallest change
that fixes it.

Look at, in this order:

1. **Policy bypass.** Any path from the collection layer to the network that
   does not go through the `Fetcher`. Any place a source's terms, allowed
   use, domains, schemes or robots policy are re-implemented instead of
   delegated, which would let the two drift apart.
2. **SSRF and URL safety.** Whether a target can reach a loopback, private,
   link-local, carrier-grade NAT or metadata address, directly or through a
   redirect. Whether the DNS answer is checked before the request and again
   on every hop. Whether the scheme, port and userinfo rules hold.
3. **Injection.** Whether any byte of a fetched body can reach a shell, a
   tool, a URL or a file path. Whether the detail strings that land in the
   database can carry a page. Whether a source id or a URL from an
   untrusted place can escape its column or its file path.
4. **Budget and quota.** Whether a caller can make the run exceed the
   source's per-run request cap, the contract's per-run cap or the
   per-stage cap, including across a restart, and including through
   redirects, robots.txt fetches and retries.
5. **Provenance and audit.** Whether every attempt leaves a durable record
   with its reason, its status and its terms status. Whether a failure can
   pass without a trace. Whether the record can be overwritten by a later
   attempt in a way that loses the first.
6. **Observability.** Whether an operator can tell what the run did, what it
   refused and why, from the database alone, without reading the logs.
7. **Idempotency and crash safety.** Whether a crash between two targets
   loses work, double-charges a budget, or loses the record of an attempt
   that already happened.

## Rules

- Read-only. Report gaps; do not fix them.
- Do not edit, create or delete any file, and do not commit.
- Every finding must name a file and a line, and must be reproducible or
  provable from the code. "This could be a problem" is not a finding.
- Say plainly when you find nothing. A clean verdict is a valid verdict.
- Do not report style, naming or taste.
- Rank the findings: must, should, nice.

## The re-harden round

The first pass returned four findings. Here is what was done with each, so the
second pass can judge the result instead of repeating the first one.

1. **An unexpected exception left no trace.** Fixed. `collect` now wraps the
   ladder lookup and the collector call and records the attempt as `failed`
   with the exception class named. `KeyboardInterrupt` and `SystemExit` keep
   propagating. Two tests, one of which fails without the fix.
2. **The contract's overrun was swallowed in silence.** Fixed. `_charge` and
   `_replay_tracker` now log a warning naming the dimension, the scope and
   the limit, and the spend stays recorded. `_replay_tracker` replays each
   dimension in its own `try`, so an overrun on one does not cost the other
   its spend.
3. **The byte ceiling caps storage and not the transfer.** Not fixed, and it
   will not be fixed in this wave. The Fetcher's request path may not change
   here: `tests/test_fetch.py` wraps the client in a `RecordingClient` that
   only implements `get`, and that test file is out of this wave's reach. The
   limit is now a `shortcut:` comment in `fetch.py` that says what the limit
   is and what closing it costs. Judge whether that note is an honest
   account of the exposure or whether it hides something.
4. **The DNS rebinding note understated what is guaranteed.** Sharpened. The
   comment now says what is guaranteed (resolve and check every address
   before the request, resolve and check again on every hop) and what is not
   (the window between the check and the connect), with pinning the resolved
   address as the upgrade.

Look hard for anything the first pass missed. Say plainly when an area is
clean.

## The second re-harden round

The second pass returned six findings. Here is what was done with each.

1. **The checkpoint failure erased the attempt record.** Fixed.
   `_write_checkpoint` now runs after the transaction commits, catches any
   failure and logs a warning naming the source, the URL and the exception.
   A broken checkpoint can neither roll the attempt back nor fail a
   collection that already happened.
2. **The rejection log's detail was unbounded.** Fixed. `fetch.py` bounds it
   the way the collection layer bounds its own detail, at 1000 characters.
3. **An unavailable collector was retried forever.** Fixed.
   `collector_unavailable` is in `TERMINAL_REASONS` now, and the `duplicate`
   outcome carries the item's own reason and detail instead of empty
   strings.
4. **The replay and the live tracker disagreed on the stage.** Fixed.
   `_replay_tracker` records both dimensions with `stage=STAGE`, the stage
   `_charge` uses.
5. **The DNS rebinding window.** Accepted, not fixed, and the decision is
   recorded in `docs/reasonix/prompts/wave-2-collection-harden-fixes-2.md`.
   Pinning the resolved address in the transport would change how the shared
   fetch path issues its requests, and the test file that owns that path is
   out of this wave's reach. The `shortcut:` comment states what is
   guaranteed and names the upgrade. Treat this one as closed by an explicit
   decision unless the acknowledgement itself is wrong.
6. **The byte ceiling.** The second pass confirmed the note is honest. No
   code change.

One known detail, not a finding: `CollectionPlanner.budget_refusal` checks
the per-source dimension without a stage, while `_charge` and
`_replay_tracker` record it with one. The shipped contract has no per-stage
limit for that dimension, so nothing differs today. It is a latent
inconsistency, not a live bug.

Report the areas, and say whether anything is left.

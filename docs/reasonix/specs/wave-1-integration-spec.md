# Wave 1 integration spec

**Date:** 2026-10-21
**Scope:** reconcile the Wave 0 and Wave 1 sessions into one coherent, tested,
production-ready state on `main`.
**Status:** implemented. This document records the definition the run was held
to, not a wish list.

## Why this run exists

Eight parallel sessions wrote into this repository over two waves. They did
not follow the original branch and worktree plan. Some worked in the shared
working tree and adapted to each other's changes in place. By the time the
waves finished, the repository had:

- one branch (`main`) carrying every committed wave, plus a dirty shared tree
  that a Wave 2 session was actively writing into,
- two subsystems that existed as complete libraries with no caller and no
  persistence, and
- documentation that described a state the code had already left behind.

The run had to take the repository as it actually was and make it coherent.

## The definition

### 1. Understand before changing

Inspect the git state, the branches, the worktrees, the diff, the repository
structure, the full specification, and the run report of every completed
session. Build a change map: what completed, what partially completed, what
overlapped, what duplicated, what conflicted, what is missing. Do not merge
blindly and do not reset good work.

### 2. Reconcile, do not just merge

Where two sessions built the same thing, keep the stronger implementation and
remove the duplicate. One authoritative implementation per responsibility.
The worker stays the autonomous authority and OpenCode stays the control and
interface layer. A chat history must never become the persistence mechanism.

### 3. Model routing is configurable

Model selection must be genuinely configurable and must not hard-code a
permanent model list into the product's business logic. The runtime must
discover the available models through the intended OpenCode integration and
assign them by role, with configurable fallbacks, and must record the
provider, the model, and the version in provenance. No `DEEPSEEK_API_KEY`.
The coding model that wrote the sessions must not become the product's
permanent runtime model.

### 4. The Research Contract and the Tool Gate are enforced by code

A serious opportunity must not be promoted if a mandatory research stage
failed, timed out, exceeded a hard budget, or produced an invalid artifact.
The contract must be enforceable rather than described in a prompt: required
stages, required capabilities, required artifacts, evidence thresholds,
deterministic quality predicates, budgets, stopping criteria, retry policy,
promotion requirements, provenance, stage attempts, tool calls,
counter-evidence, competitor research, source independence, concentration
checks, and non-trivial zero-result handling.

The query families and the mandatory query templates must be worker-assigned.
They must not be left entirely to a language model.

### 5. The evidence chain stays traceable

Raw source to document to evidence span to claim to cluster to opportunity.
Every material claim traceable to captured source evidence: source URL,
source and document id, capture metadata, content hash, the exact quote,
author and community where applicable, the extraction model and version, the
extraction run, the provenance, the propagation and repost information, and
the contradiction and counter-evidence. A free-floating summary is not
authoritative evidence.

### 6. Internet content is untrusted data

Scraped pages, forum posts, GitHub issues, snippets, and metadata must not
become shell commands, arbitrary filesystem operations, credentials, Git
pushes, external posts, or arbitrary tool calls. Check the prompt-injection
boundary, the SSRF protections, the URL and redirect validation, the source
admission, the terms status, the robots handling, the credential handling,
the subprocess invocation, the filesystem restrictions, the external side
effects, and the destructive operations. Do not weaken safety to make a test
pass.

### 7. SQLite stays the authority

Test the migration initialisation, the migration failure behaviour, the WAL
configuration, the busy timeout, the idempotent writes, the deterministic
ids, the job claiming, the lease renewal, the lease expiry, the lease-epoch
fencing, the duplicate execution, the retry, the dead-letter, the worker
crash recovery, the checkpoint recovery, the outbox recovery, the projection
retry, and the partial artifact handling. A stale worker must not be able to
write after losing its lease. If an end-to-end crash and restart test is
missing, add one.

### 8. Everything is tested, and the results are not fabricated

Run the full suite and the narrower suites. Diagnose the root cause of a
failure, fix it, re-run the smallest relevant test, then re-run the full
suite. Do not fabricate a result and do not weaken a test to go green.

### 9. The specification is compared, honestly

Produce a checklist of DONE, PARTIALLY DONE, NOT DONE, and DEFERRED BY
DESIGN. Do not invent missing functionality. Respect the phase boundaries.
Do not pull the deferred Phase 7 integrations into the core because the
specification mentions them.

### 10. The documentation describes the code

Architecture, worker and runtime, database, recovery, scheduler, model
routing, the Research Contract, the Tool Gate, security, evaluation, the
OpenCode integration, the tooling and MCP setup, the current phase and
status, and the deferred integrations. Remove the stale claims. Do not
document functionality that does not exist.

### 11. Git hygiene

Remove the accidental generated junk, keep the secrets untracked, keep the
temporary logs out of the commit, and correct the `.gitignore`. Preserve the
valid historical commits and commit the valid uncommitted work. Do not push.

### 12. Stop

Understand, reconcile, test, harden, document, commit, verify, stop. Do not
start a new major feature after the integration and do not redesign the
project.

## The blast radius

Declared at stage 0 with the `--files` list: `src/product_hunter/**`,
`tests/**`, `docs/reasonix/**`, `docs/TOOLING.md`, `README.md`, `config/**`,
`db/**`, `.opencode/**`, `pyproject.toml`, `.gitignore`, `.env.example`.

The risk was declared `high`. The run touches the database schema, the
security boundary, and the research contract, and §38A of the specification
makes stages 2, 6, and 7 non-skippable at that risk level.

## The stages

| Stage | Who | Output |
|---|---|---|
| 0 | orchestrator | The blast radius, the risk, the repository map |
| 4 | `loop-free-implement-1` | The three gap fixes |
| 5 | `loop-free-verify-code-1` | The code gate |
| 6 | `loop-free-harden` | The security and observability pass |
| 7 | `loop-free-review-1` | The adversarial review, twice |
| 8 | orchestrator | The documentation and the checklist |
| 9, 10 | orchestrator, `loop-free-scribe` | The commit and the report |

Stage 5b, the visual gate, was not required: the adapter sets
`visualRequired` to `never` and no UI file changed.

## What the run was allowed to leave out

The Wave 2 sessions own the clustering, the collection engine, the discovery
engine, the evidence pipeline, the independence analysis, the investigation,
the scoring and validation, and the Obsidian reporting. This run does not
build them, does not merge them, and does not let them block the integration.
It builds the seam they need: the persistence, the fence, and the query plan
that a stage handler codes against.

# Wave 0 Tooling Setup — Definition

**Date:** 2026-10-21
**Lane:** backend tooling. No UI file is touched, so no `DESIGN.md` is required.
**Source of truth:** `docs/reasonix/specs/Product-Hunter-Full-Spec.md` (§5 skills, §6 MCP layer, §6A tool enforcement, §38A phases)
**Phase under setup:** between §38A Phase 2 (evidence core, in progress) and §38A Phase 3 (semantics and independence, next)

---

## 1. What this wave is for

The next implementation step is Phase 3, which introduces embeddings, clustering, entity resolution, propagation-aware independence counting, and DuckDB analytics. Phase 3 needs a vector store, an analytics engine, and a written method for deciding what counts as independent evidence. None of that is reachable from the current environment, so this wave prepares it.

This wave also records what is deliberately not being installed, so a later phase does not have to re-derive the decisions.

## 2. Scope

In scope:

- audit every MCP the machine already exposes, and record which ones answer a real call
- add the MCP servers the next phase needs, and only those
- audit the 192 global skills and the 5 project skills, then create the project skills Phase 2 and Phase 3 need
- write `docs/TOOLING.md` as the single place that states what is available, what is verified, what is deferred, and why
- record the credentials that a later phase will need, without asking for them and without writing a value

Out of scope, and stated here so a reviewer can check it:

- any change under `src/product_hunter/`
- any change under `db/`, including migrations
- any change to `config/sources.yaml`
- any Phase 2 implementation work
- any change to the research pipeline's behaviour
- any push, and any secret committed to the repository

## 3. The decision rules

Three rules decide every install in this wave.

**Rule 1, the phase rule.** A capability is installed only when the phase that needs it is the next phase or the phase in progress. The spec's phase table (§38A) is the calendar. Phase 7 holds Apify, Crawl4AI, large-scale Playwright, SubQ Snap, and richer observability, and §38 puts Redis, Temporal, and Langfuse in the same "only after measured need" column. Those stay deferred.

**Rule 2, the reuse rule.** An existing capability is reused, never reinstalled. GitHub, Playwright, and chrome-devtools are already configured and answer real calls. OpenCode already ships `websearch` and `webfetch` as native tools, and the worker already ships `ph fetch`, so a separate Fetch MCP would duplicate a working path. The spec says the same thing in §6: the MCP layer is an interactive surface, while the Python worker owns authoritative collection.

**Rule 3, the no-secret rule.** No credential is requested during setup, and no credential value is written anywhere. A capability that needs a key is recorded as needing one, with a name-only placeholder in `.env.example`.

## 4. Accept criteria

| # | Criterion | How it is proved |
|---|---|---|
| A1 | every existing MCP is probed with a real call, and the result is recorded | `docs/TOOLING.md` carries a per-server verdict with the call that was made |
| A2 | no server is reinstalled while it works | the installed list in `docs/TOOLING.md` contains only servers that were absent before |
| A3 | each new server is reachable, not merely configured | the new server answers a tool call in this session, or the exact reason it cannot yet |
| A4 | a deferred capability carries a reason and a phase | `docs/TOOLING.md` has a deferral table with a phase column |
| A5 | no secret is written | `git diff` shows no value for any key, token, or password |
| A6 | no application code changed | the diff touches no file under `src/`, `db/`, or `config/` |
| A7 | the environment still starts | `opencode mcp list` runs, and OpenCode starts against the project config |
| A8 | the new skills are discoverable | each skill file parses with a name and a description, and the project skill directory lists them |

## 5. The skill rule

The spec's §5 list has 30 research skills. The machine already carries 192 global skills and 5 project skills, so the list is not a blank sheet.

The 5 project skills already shipped with Phase 1: `source-discovery`, `pain-point-extraction`, `pain-point-normalization`, `evidence-provenance`, `evidence-validation`. They are the reference for house style, and they stay as they are.

The global set has no research-methodology skill. `customer-research` runs primary interviews for a market, `competitor-profiling` profiles competitor marketing sites, `doubt-driven-development` reviews code, and `pricing` reasons about packaging. None of them encodes how this pipeline decides that two complaints are one problem or that three posts are one person. No skill is duplicated, and no unrelated skill is installed.

The five skills created in this wave are the ones Phase 2 and Phase 3 need: `source-reliability`, `source-independence`, `entity-resolution`, `problem-clustering`, and `frequency-analysis`. The rest of the §5 list belongs to Phase 4's research gate and Phase 6's projections, and is deferred with a phase.

New skills are created in `.opencode/skills/`, not installed from a registry. The methodology is specific to this repository's schema, its deterministic id rules, its fail-closed posture, and the spec sections it implements. A third-party skill could not be specific to those, and a generic one would be a duplicate of the five that already exist.

## 6. Assumptions

Two readings were open. Both are recorded here rather than asked, because the request is unattended and both have a conservative default.

- "The current implementation phases" is read as Phase 2 in progress plus Phase 3 next, with Phase 4 as the boundary. Phase 4 capabilities are deferred.
- "Install" for a skill means "create it in the project", because the §5 skills are this project's methodology and no reputable package for them exists.

## 7. Radius

`opencode.json`, `docs/TOOLING.md`, this document, the plan, the dispatch prompts, the report, the handoff, five new files under `.opencode/skills/`, and `.env.example`. Nothing else. A change that needs more goes back to this stage and says so.

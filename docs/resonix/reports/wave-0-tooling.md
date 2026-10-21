# Wave 0 Tooling Setup — Run report

**Run ID:** wave-0-tooling-2026-10-21
**Date:** 2026-10-21
**Goal:** prepare the OpenCode environment for Product Hunter, with no application code, no schema change, and no pipeline change.

```text
GOAL: prepare the OpenCode environment for Product Hunter: audit the MCPs, add only what Phase 3 needs, add the Phase 2 and Phase 3 skills, and record the rest as deferred
TIER: free (models used per node in order)
  stage 0-3  orchestrator: longcat-2.5-preview-free
  stage 4    loop-free-implement-1: longcat-2.5-preview-free
  stage 5    loop-free-verify-code-1: step-5-preview-free
  stage 6    loop-free-harden: step-5-preview-free
  stage 7    loop-free-review-1: step-5-preview-free
  stage 8-10 orchestrator and loop-free-scribe: longcat-2.5-preview-free
RISK: declared=medium floor=medium (tooling configuration, agent-readable skill content, a names-only credential placeholder)
ADAPTER: Product Hunter [repo] 8 forbidden rules in force
STAGES: 11/11 complete, skipped: none. Stage 5b visual was not required, the adapter sets visualRequired to never and no UI file changed.
GATES: uv run pytest -> 747 passed in 9.45s, exit 0
CHANGES: 20 files, 2 modified and 18 new. See the file table below.
HARDEN: two passes. First pass returned F1 to F4. The second pass closed F1, F2 and F3, and left F4 unchanged and failing closed. It raised one new commit blocker, the operator's apify entry, and the review ruled to commit it as the operator wrote it.
REVIEW: Verdict: ship. Two commit-time items, both handled. Lean: -42 lines possible, 28 of them taken.
WAIVERS: none
STOPPED BECAUSE: all pass and review ship
NEXT: ready to commit
```

---

## 1. The file table

| Path | Change | Lines |
|---|---|---|
| `opencode.json` | new. Three MCP servers | 28 |
| `.gitignore` | `*.duckdb` added | 1 added |
| `.env.example` | the empty name `SUBQ_API_KEY=` and its comment | 3 added |
| `.opencode/skills/source-reliability/SKILL.md` | new | 50 |
| `.opencode/skills/source-independence/SKILL.md` | new | 58 |
| `.opencode/skills/entity-resolution/SKILL.md` | new | 43 |
| `.opencode/skills/problem-clustering/SKILL.md` | new | 50 |
| `.opencode/skills/frequency-analysis/SKILL.md` | new | 44 |
| `docs/TOOLING.md` | new, the audit record | 187 |
| `docs/resonix/specs/wave-0-tooling-spec.md` | new, the definition | 79 |
| `docs/resonix/plans/wave-0-tooling-plan.md` | new, the plan and the MCP matrix | 142 |
| `docs/resonix/prompts/04-implement.md` | new, the implement dispatch | 177 |
| `docs/resonix/prompts/04b-implement-pins.md` | new, the pin and Apify dispatch | 44 |
| `docs/resonix/prompts/04c-implement-review-fixes.md` | new, the skill grounding dispatch | 102 |
| `docs/resonix/prompts/05-verify.md` | new, the verify dispatch | 84 |
| `docs/resonix/prompts/06-harden.md` | new, the first hardening dispatch | 90 |
| `docs/resonix/prompts/06b-harden.md` | new, the second hardening dispatch | 70 |
| `docs/resonix/prompts/07-review.md` | new, the review dispatch | 99 |
| `docs/resonix/reports/wave-0-tooling-handoff.md` | new, the handoff | 115 |
| `docs/resonix/reports/wave-0-tooling.md` | new, this report | 127 |

## 2. MCPs verified

Each of these answered a real call. The call and the result are in `docs/TOOLING.md` section 2.

`github`, `playwright`, `chrome-devtools`, `context7`, `cloudflare-api`, `cloudflare-docs`, `better-auth`, `design-systems`, `sentry`, `neon`, `Framelink_Figma_MCP`, `astro`, `astryx`, `nova-memory`, `supabase`, `apify`, and the two this wave added, `qdrant` and `duckdb`.

## 3. MCPs installed

Two, both for §38A Phase 3, both local processes, neither carrying a credential.

- `qdrant`, the official `qdrant/mcp-server-qdrant`, pinned to `0.8.1`, running `uvx --python 3.13 mcp-server-qdrant@0.8.1`. It starts and lists `qdrant-find` and `qdrant-store`. A search returns `All connection attempts failed`, because no Qdrant instance runs yet. Phase 3 starts it.
- `duckdb`, the community `mcp-server-duckdb`, pinned to `1.1.0`, running `uvx --python 3.13 --with "mcp<2" mcp-server-duckdb@1.1.0 --db-path db/product_hunter.duckdb`. The live tool answers `SELECT 1 AS ok` with `[(1,)]`.

The operator added a third, `apify`, as a remote server, and authenticated it with OAuth. It answers, and it is in the file because the operator put it there.

## 4. MCPs deferred

| Deferred | Phase | Why |
|---|---|---|
| Search MCP | 4 | OpenCode's native `websearch` covers the interactive role, and the worker's own search adapter is authoritative for the pipeline |
| Fetch MCP | 4 | OpenCode's native `webfetch` and the worker's `ph fetch` already cover it |
| Crawl4AI, large-scale Playwright | 7 | §38A gates them behind a measured need |
| SubQ Snap | optional | the §38A verification gate needs a benchmark before it enters the critical path |
| Langfuse, Redis, Temporal | optional | §38 lists them as deliberately not required in the initial core |
| Obsidian | 6 | the projection lands in Phase 6 |

`postman`, `vercel`, `postiz`, `MCP_DOCKER` and `supabase` answer but are not usable as configured. None is required by this specification, so none was repaired.

## 5. Skills installed

Five, created in `.opencode/skills/`, each naming the specification section it implements, the repository files it governs, and the phase that owns the implementation.

`source-reliability` (§7, §12), `source-independence` (§12), `entity-resolution` (§12, §15A), `problem-clustering` (§14, §15A), `frequency-analysis` (§16).

## 6. Skills already present

Five from Phase 1, byte-identical to `HEAD`: `source-discovery`, `pain-point-extraction`, `pain-point-normalization`, `evidence-provenance`, `evidence-validation`.

## 7. Skills deferred

Twenty of the thirty names in §5, each with its phase and its reason in `docs/TOOLING.md` section 7. The registry at `skills.sh` was searched for all thirty, through the `skills` command-line tool, and each candidate's source page was read before the decision. The registry holds no official or reputable equivalent for this pipeline's methodology. The one official candidate, `firecrawl/cli@firecrawl-search`, needs a `FIRECRAWL_API_KEY` and a managed service, and it sits beside Apify and Crawl4AI in the specification's tool ladder.

## 8. Credentials still needed

None was requested, and no value was written anywhere in this repository.

| Name | Needed by | Effect while missing |
|---|---|---|
| `SUBQ_API_KEY` | the optional SubQ Snap verification gate | nothing. The pipeline never reads it. |
| `FIRECRAWL_API_KEY` | only if the Phase 4 search decision goes that way | nothing. The skill is not installed. |
| an Apify token | only if a Phase 7 benchmark justifies Apify | nothing. The operator already authenticated the server through OAuth, and the worker does not use it. |

The machine's global `~/.config/opencode/opencode.jsonc` holds several credentials in plaintext. That file is outside this repository, and the Phase 1 plan already recommends moving those values to environment variables. That work is still open.

## 9. What the gates said

- Code gate, `uv run pytest`, exit 0, `747 passed in 9.45s`. The count moved from 627 to 641 to 747 across the run, because a parallel session is adding tests while this wave works.
- The verify node confirmed the config parses, the servers are live, the skills parse, the pre-existing skills are untouched, and no secret is present.
- The hardening node ran twice. The first pass raised F1 to F4; the second pass confirmed F1, F2 and F3 closed, F4 unchanged and failing closed, and raised one new item, the operator's Apify entry.
- The review read the diff and returned `Verdict: ship`, with two commit-time items: the stale path list, which would have staged the deletion of the product specification, and a second hunk in `.gitignore` that this wave did not author. Both are handled in the commit.

## 10. What was not checked

- The Qdrant server was never connected to a live instance, because no instance runs. The tool call fails with a refused connection, and that is the honest state.
- The DuckDB server is read-write, and no read-only mode was exercised.
- The five new skills have no automated test. A skill is a document, and the only check available today is that it parses and that its front matter is well formed.
- The `uvx` supply-chain exposure is reduced by the version pins, not removed. Transitive dependencies still resolve at launch.
- The machine's global config was read but never written, so the plaintext credentials there are unchanged.
- The Apify server was exercised with a catalogue search and a documentation search, not with a paid Actor run.

## 11. Commit

The commit is the one with the message `chore: prepare OpenCode tooling for Product Hunter`. Its hash is in the chat run report, and it can be found with `git log --grep "prepare OpenCode tooling"`. It stages explicit paths only. It pushes nothing.

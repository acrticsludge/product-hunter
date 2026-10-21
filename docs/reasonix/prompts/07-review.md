# Stage 7 dispatch: adversarial review of the Wave 0 tooling diff

**Node:** `loop-free-review-1`
**Repository:** `C:\Anubhav\Web Dev Projects\product-hunter`

---

## What you are reviewing

A tooling wave. The baseline is commit `205a4ba`, and the wave's own paths are all new or tiny edits.

| Path | Change |
|---|---|
| `opencode.json` | new. Three MCP servers: `qdrant` and `duckdb` as local `uvx` processes, `apify` as a remote server |
| `.gitignore` | `*.duckdb` added |
| `.env.example` | the empty name `SUBQ_API_KEY=` plus a comment |
| `.opencode/skills/source-reliability/SKILL.md` | new |
| `.opencode/skills/source-independence/SKILL.md` | new |
| `.opencode/skills/entity-resolution/SKILL.md` | new |
| `.opencode/skills/problem-clustering/SKILL.md` | new |
| `.opencode/skills/frequency-analysis/SKILL.md` | new |
| `docs/TOOLING.md` | new |
| `docs/reasonix/specs/wave-0-tooling-spec.md` | new |
| `docs/reasonix/plans/wave-0-tooling-plan.md` | new |
| `docs/reasonix/prompts/04-implement.md`, `04b-implement-pins.md`, `05-verify.md`, `06-harden.md`, `06b-harden.md` | new |
| `docs/reasonix/reports/wave-0-tooling-handoff.md` | new |

The definition is `docs/reasonix/specs/wave-0-tooling-spec.md` and its acceptance criteria are in section 4. The plan is `docs/reasonix/plans/wave-0-tooling-plan.md`. The audit record is `docs/TOOLING.md`.

The code gate passed: `uv run pytest`, exit 0, 641 passed, 1 skipped. The wave touched no Python.

## How to read the working tree

**A parallel session is writing application code into this repository right now.** The working tree carries that work as modified and untracked files under `src/`, `tests/`, `config/`, `db/migrations/`, and `docs/reasonix/`. That work is not part of this wave and is not a finding against it.

Review the wave's paths only. To see the wave's own changes, use the explicit path list above. Do not treat the parallel session's files as part of the diff, and do not read them as a defect of the wave. Note that `git diff` shows nothing for the new files, because they are untracked until the commit.

## What the contract says the wave had to achieve

1. Audit every MCP the machine exposes and record which ones answer a real call.
2. Install only the MCPs the current implementation phases need, and record the rest as deferred with a reason.
3. Audit the installed skills, and install or create only the ones the upcoming phases need, preferring an official or reputable source over a duplicate.
4. Add no credential, write no credential value, and record the credentials a later phase needs.
5. Write `docs/TOOLING.md`, and claim nothing works that was not actually verified.
6. Touch no application code, no database, no migration, and no pipeline behaviour.

## What to do

Read the wave's paths, then try to prove the wave wrong. Specifically:

1. Does the work match its own acceptance criteria in the spec, A1 through A8? For each, say met, partially met, or not met, with the evidence.
2. Is the "install only what the next phase needs" claim true, or did the wave install something the phase table does not require? Check the two local servers and the remote one against §38A of `docs/reasonix/specs/Product-Hunter-Full-Spec.md`.
3. Are the deferrals honest? Check every deferred MCP and every deferred skill against the specification, and say whether any deferral is a real gap dressed as a deferral.
4. Is `docs/TOOLING.md` accurate? Compare every claim in it against what the wave actually observed. A server called working without a real call, a version that is wrong, a file path that does not exist, or a claim that the wave cannot support is a finding.
5. Do the five new skills overstate what the code does today? They describe Phase 2 and Phase 3 behaviour, and Phase 3 code does not exist yet. Check whether a skill states a rule as if it were implemented when the implementation is a later phase, and whether a skill could make an agent claim a number it has not earned. Compare the five against the five Phase 1 skills for the same problem.
6. Is anything in the wave a violation of the forbidden rules in `.loop/adapter.json`? Is any credential, key, or token value present? Is the global machine config touched in any way?
7. Is the wave over-engineered, under-engineered, or exactly the right size? A file that duplicates the global config, a config key that does nothing, a skill that repeats another skill, or a document that restates the plan is bloat. A missing guard is worse. Use the lean pass: could anything be deleted without loss, and is anything missing that the next phase will need?
8. Does the commit plan hold? The wave intends to commit exactly the paths listed above, with explicit paths, no `git add -A`, and no push. Is anything in that list unsafe to commit, and is anything missing from it that should be committed?

## The one decision you must rule on

The operator hand-edited `opencode.json` to add this entry, after the wave's plan was written:

```json
"apify": {
  "type": "remote",
  "url": "https://mcp.apify.com/",
  "enabled": true
}
```

The operator authenticated it with OAuth, and it answers: `search-actors` returned real Actor records and `search-apify-docs` returned documentation. The same edit also dropped two package version pins, which the wave restored, so the entry is now byte-identical to what the operator wrote.

The hardening pass raised this as a commit blocker, on these grounds:

- It is a remote server on a paid platform. An agent in this repository can call Actors, and the calls are billed to the account that authorised it. The token is not in the repository, so a clone without that authorisation cannot use it.
- §38A places Apify in Phase 7, behind a measured need, and nothing in Phase 2 or Phase 3 uses it.
- It carries no `timeout`, unlike the two local servers.

The counter-argument: the operator installed it deliberately, in this project, with their own account, and told the orchestrator to continue. Reverting or disabling it without their consent would undo their decision, and the risk concerns their own account on their own machine.

Rule on it. Say whether the entry should be committed as it stands, committed disabled, or left out of the commit, and give the reason. If your verdict is that it must not be committed as it stands, say what the orchestrator should do instead, given that the operator may not be watching.

## Constraints

- You review, you do not edit. Do not stage, commit, stash, clean, or revert anything.
- Do not read the parallel session's application code as part of this diff.
- Anything you write goes under `C:\Users\anubh\AppData\Local\Temp\opencode\`, never in the repository.

## What to report back

Plain text:

- a verdict line, in the form `Verdict: ship` or `Verdict: do not ship`
- the lean line, in the form `Lean: -N lines possible`, with the list of what could go
- your findings as Must, Should, and Nice, each with the file, the evidence, and the concrete change
- the ruling on the Apify entry
- the answer to the eight numbered questions
- the checks you did not run

# Stage 5 dispatch: verify the Wave 0 tooling changes

**Node:** `loop-free-verify-code-1`
**Repository:** `C:\Anubhav\Web Dev Projects\product-hunter`

---

## 1. The situation you are verifying

A tooling wave changed eight paths and no application code:

```
opencode.json                                            (new)
.gitignore                                               (+*.duckdb)
.env.example                                             (+empty SUBQ_API_KEY name)
.opencode/skills/source-reliability/SKILL.md             (new)
.opencode/skills/source-independence/SKILL.md            (new)
.opencode/skills/entity-resolution/SKILL.md              (new)
.opencode/skills/problem-clustering/SKILL.md             (new)
.opencode/skills/frequency-analysis/SKILL.md             (new)
```

**A second agent is writing application code into this repository at the same time.** Files under `src/`, `tests/`, `config/`, `db/migrations/`, and `docs/resonix/` are being created and modified right now by a parallel session working on later phases of the spec. That work is not part of this wave and is not in this wave's radius. It was already present when the tooling wave started its last step.

That matters for how you read the gate result, so handle it as described below. Do not modify, revert, stash, clean, or commit anything.

## 2. The code gate

The repository's gate is `uv run pytest`, declared in `.loop/adapter.json` as `gates.code`.

Run it in the repository root and report the result verbatim: the command, the exit code, the pytest summary line, and the full list of failing test ids with their first assertion line.

If the suite fails, attribute the failures before reporting them:

1. Re-run the suite once. A parallel writer can leave a half-written file that fails collection, so a second run tells you whether the failure is stable.
2. If it still fails, check whether the failing test ids point at files that this wave never touched, which is every file under `src/` and `tests/` except none, because this wave touched no Python at all. List the failing test id against the file it comes from.
3. Build a pristine baseline to compare against. Do not use `git worktree`, `git stash`, or `git checkout`, because the parallel session is using the same working tree. Use this instead:

```
New-Item -ItemType Directory -Force C:\Users\anubh\AppData\Local\Temp\opencode\ph_baseline
git archive HEAD | tar -x -C C:\Users\anubh\AppData\Local\Temp\opencode\ph_baseline
```

Then run `uv run pytest` in that extracted copy and report its summary line. `HEAD` is commit `205a4ba`, the state before this wave. If the baseline copy fails the same way, the failure predates this wave.

State plainly in your report which of these three cases you observed: the suite passes, the suite fails for reasons in the parallel session's files, or the suite fails in the baseline too.

## 3. The configuration checks

Run these and report the raw output of each.

1. `opencode.json` parses as JSON. Use `Get-Content -Raw opencode.json | ConvertFrom-Json` and report whether it throws.
2. The file contains exactly two MCP servers, named `qdrant` and `duckdb`, both with `type` `local`, both with a `command` array, both with `timeout` 120000, and the qdrant one with the three environment variables `QDRANT_URL`, `COLLECTION_NAME`, and `EMBEDDING_MODEL`.
3. No secret value appears anywhere in the wave's files. Check `opencode.json`, `.env.example`, and the five new skill files for anything that looks like a key, token, password, or bearer value. The only permitted credential reference is the empty name `SUBQ_API_KEY=`.
4. The `.gitignore` change is exactly one line, `*.duckdb`, and `*.duckdb` is not matched by any tracked file.
5. The `.env.example` change is the comment line and the empty `SUBQ_API_KEY=` name, and no other line changed. Report the diff of that file.

## 4. The skill checks

1. All five new skill files parse: the YAML front matter has exactly the keys `name` and `description`, the `name` matches the directory name, and the `description` is a single line that starts with "Use when".
2. The five pre-existing skills are byte-identical to `HEAD`. Check them with `git diff --stat HEAD -- .opencode/skills/source-discovery .opencode/skills/pain-point-extraction .opencode/skills/pain-point-normalization .opencode/skills/evidence-provenance .opencode/skills/evidence-validation` and report the output, which should be empty.
3. No new skill file contains a banned phrase from the repository's own writing rules: "In today's", "delve", "tapestry", "not merely", or the em-dash character. Report the result of a literal search for each.

## 5. The MCP checks

Both servers were already installed and exercised over the real MCP stdio protocol by the orchestrator, and both now appear in this session's live tool catalog, which proves OpenCode loaded the project config and merged it with the global config rather than replacing it.

Do not re-run the servers. Instead:

1. Call the `duckdb` MCP tool with a read-only query and report the result, for example `SELECT 1 AS ok`. The database is `db/product_hunter.duckdb`, which the server creates on first connect. Report whether the file was created and where.
2. Call the `qdrant` MCP tool `qdrant-find` with a short query and report the exact error. The expected error is a refused connection to `http://localhost:6333`, because no Qdrant instance is running yet. If it returns something else, say so plainly.
3. Report whether `db/product_hunter.duckdb` is ignored by git, using `git status --short --ignored -- db/`.

## 6. What to report back

Plain text, in this order:

- the code gate result, with the three-way attribution from section 2
- each configuration check from section 3, with its raw output
- each skill check from section 4
- each MCP check from section 5
- anything that contradicts this brief, with the evidence

You must not edit, create, delete, move, stage, or commit any file in the repository. You are reading and running commands only.

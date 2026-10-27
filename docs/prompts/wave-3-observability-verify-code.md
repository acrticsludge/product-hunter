# Verify node: the code gate

**You are the gate runner for Wave 3 Session 7.** You run the gate and
report. You do not fix anything, you do not edit a file, and you do not
commit.

**Working directory.** The worktree you are already in. The branch is
`wave3/observability-evaluation`.

**The gate.** The adapter declares it in `.loop/adapter.json`:

```json
"gates": { "code": ["uv run pytest"] }
```

The baseline before this wave was 2138 tests, all green. The wave added an
observability package, a migration, an evaluation expansion and their tests,
so the suite must be larger and must still be green.

## Do this

1. Run `uv run pytest` from the working directory. Keep the full output and
   the exit code.
2. If it is green, confirm the migration works end to end:

   ```text
   uv run python -c "from product_hunter.db import connect, migrate; c=connect(':memory:'); print('schema version', migrate(c))"
   ```

   It must print `schema version 8` and raise nothing.
3. Confirm the new commands are wired:

   ```text
   uv run python -m product_hunter.cli --help
   uv run python -m product_hunter.cli observe metrics --help
   uv run python -m product_hunter.cli observe trace --help
   ```

   The first must list the `observe` group. Neither of the other two may
   raise.
4. Confirm the migration is additive and reversible in the sense the repo
   means: the new table is `model_comparisons`, the loader refuses a
   duplicate number and a gap, and `0008` follows `0007` with nothing missing
   between them.

## Record the gate

After the run, record the result:

```text
node "$env:USERPROFILE\.config\opencode\loop\loopstage.mjs" --verb pass --stage 5 --note "<the verbatim pytest summary and the exit code>"
```

If the suite failed, use `--verb fail` and put the failing test names in the
note. Never use `--verb skip`: a gate that did not run is not a gate that
passed. If `node` is not on PATH, say so in your report instead.

## The report

Give the verbatim pytest summary line, the exit code, the schema version, the
outcome of the three command checks, and the name and assertion of every
test that failed. Do not paraphrase the counts.

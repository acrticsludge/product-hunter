# Handoff — plugin project root

**Run:** loop-free, 2026-10-21
**Baseline:** branch `main`, HEAD `205a4ba` ("Phase 2 step 1: authoritative
SQLite evidence core"), working tree dirty before this change. The tree is
mixed: the Phase 2 work is in flight and uncommitted, and this run did not
touch it. The pre-existing modifications are listed in section 5 so the
reviewer can separate them from this change.

## 1. What was observed

The `product_hunter` tool was called with `action: list`. It returned this,
verbatim:

```
the registry exited with 1: C:\Users\anubh\AppData\Roaming\uv\python\cpython-3.13-windows-x86_64-none\python.exe: Error while finding module specification for 'product_hunter.registry' (ModuleNotFoundError: No module named 'product_hunter')
```

The registry was never reached. The error came from the child process.

## 2. The reproduction

The same command, pointed at the repository, answers:

```
uv run --project "C:\Anubhav\Web Dev Projects\product-hunter" python -m product_hunter.registry list
{"ok":true,"action":"list","message":"0 research run(s)","text":"no research runs","run":null,"runs":[],"needs_selection":false,"code":null}
```

The same command, pointed at a directory with no `pyproject.toml`, reproduces
the failure byte for byte:

```
uv run --project "C:\Users\anubh\.config\opencode" python -m product_hunter.registry list
...cpython-3.13-windows-x86_64-none\python.exe: Error while finding module specification for 'product_hunter.registry' (ModuleNotFoundError: No module named 'product_hunter')
```

`uv` accepts any directory for `--project`. When the directory holds no
project, it builds a bare environment on its default interpreter, the package
is not importable, and the import error surfaces in place of the real cause.

## 3. The root cause

`.opencode/plugins/product-hunter.ts`, lines 161 to 163:

```typescript
const directory = String(toolCtx?.directory || process.cwd())
const sessionID = toolCtx?.sessionID ? String(toolCtx.sessionID) : undefined
const result = await runRegistry(directory, buildArgs(input, sessionID))
```

That `directory` is the `--project` argument and the working directory of the
child process (lines 59 to 63). The tool context carried no project root in
this runtime, so the plugin fell back to the process working directory, which
is not the repository either.

The session's own directory is the repository, and it was verified from the
opencode session store rather than assumed:

```
session_v2 -> ('ses_ea2c6ab9fffeJ2fPemcOo9zsg3', '44767c2b...', None,
               'C:/Anubhav/Web Dev Projects/product-hunter', '', 'tidy-wizard')
```

The sibling plugin `~/.config/opencode/plugins/loop-guard.ts` reads the
project root from its own plugin context (line 911) and does find this
repository, because it reads the adapter at `.loop/adapter.json`.

## 4. The house rule to reuse

`src/product_hunter/config.py`, lines 105 to 110:

```python
def repo_root() -> Path:
    """Return the repository root by walking up from this file to pyproject.toml."""
    for parent in Path(__file__).resolve().parents:
        if (parent / "pyproject.toml").is_file():
            return parent
    raise RuntimeError("repository root not found: no pyproject.toml above the package")
```

One definition of the project root, mirrored in TypeScript. The plugin file
sits in `.opencode/plugins/`, so the same walk from the plugin file finds the
same directory.

## 5. The tree, before this change

Repository root: `C:\Anubhav\Web Dev Projects\product-hunter`

| Path | What it holds |
|---|---|
| `src/product_hunter/registry.py` | the state layer the plugin drives, `python -m product_hunter.registry` entry point |
| `src/product_hunter/config.py` | `repo_root()`, `database_path()`, the `PH_` environment overrides |
| `src/product_hunter/cli.py` | the `ph` console script |
| `src/product_hunter/runtime/worker.py` | the worker that owns execution |
| `tests/test_registry.py` | the registry contract tests, a temporary SQLite file per test |
| `db/product_hunter.db` | the run database, path from `database_path()` |
| `.loop/adapter.json` | the adapter: the code gate, the risk floor, the forbidden rules |
| `.opencode/plugins/product-hunter.ts` | the file this change touches |
| `.opencode/commands/*.md` | the ten command files, one per action |

Modified before this change, and not touched by it:
`.env.example`, `.gitignore`, `README.md`, `src/product_hunter/cli.py`,
`src/product_hunter/config.py`, `src/product_hunter/db/__init__.py`,
`src/product_hunter/db/checkpoints.py`, `src/product_hunter/db/store.py`,
`src/product_hunter/db/worker.py`, `src/product_hunter/llm/__init__.py`,
`src/product_hunter/llm/opencode.py`, `tests/test_db_migrations.py`,
`tests/test_db_store.py`.

Untracked before this change: `.opencode/**`, `config/model_routing.yaml`,
`config/research_contract.yaml`, `db/migrations/0002_model_routing.sql`,
`docs/TOOLING.md`, `docs/reasonix/**`, `docs/reasonix/reports/**`, `eval/**`,
`opencode.json`, `src/product_hunter/registry.py`, `src/product_hunter/db/model_routing.py`,
`src/product_hunter/db/scheduler.py`, `src/product_hunter/eval/**`,
`src/product_hunter/llm/catalog.py`, `src/product_hunter/llm/discovery.py`,
`src/product_hunter/llm/routing.py`, `src/product_hunter/research/**`,
`src/product_hunter/runtime/**`, and the new test files.

Commands used: `uv run pytest`, `uv run python -m product_hunter.registry <action>`.

## 6. Constraints that apply

- The repository has no JavaScript test runner and no `package.json`. The
  plugin is TypeScript loaded by opencode, so the change carries no unit test.
  The check is the live tool call. A gather pass over `tests/` confirmed that
  no test file references `.opencode/plugins/product-hunter.ts` or the
  plugin's directory resolution. The only OpenCode-related test is
  `tests/test_llm_contract.py`, which covers the LLM provider
  (`product_hunter.llm.opencode`), not the plugin.
- The house document trail lives in `docs/reasonix/specs`, `docs/reasonix/plans`,
  `docs/reasonix/prompts` and `docs/reasonix/handoffs`, with reports in
  `docs/reasonix/reports`. A dispatch prompt carries a node, stage, risk and blast
  radius header, then `Read first`, `What to build`, `Rules`, `Check your own
  work before you report`, and `Report back`.
- `docs/TOOLING.md` does not state how the plugin finds the project root. The
  rule in this change is stated for the first time by the spec and the plan.

## 7. The final state

The change shipped. The plugin resolves the project root with the rule
`config.repo_root()` already uses, and every spawn failure names the
directory it used.

- The diff against the pre-change baseline is 55 insertions and 12 deletions
  in `.opencode/plugins/product-hunter.ts`, plus one new test file.
- The code gate passed twice on a quiet tree, `828 passed`, exit 0, from two
  independent runners.
- The live tool answers: `3 research run(s)`.
- The harden node found nothing. The review node returned `Verdict: ship` in
  three rounds, and the findings it raised were applied except three, which
  are declined with reasons in the report.

One thing changed under this run that the next reader needs to know. Another
actor was editing this repository the whole time. It changed the argv shape
in `buildArgs` to the equals form, which is in the shipped diff and is
recorded in the spec. It also moved the reports folder from `docs/reports/`
to `docs/reasonix/reports/`, so this run's report sits at
`docs/reasonix/reports/plugin-project-root.md`.
- The forbidden rules in `.loop/adapter.json` bind this file: the spawn keeps
  an argument vector with no shell string, and no secret is written anywhere.
- A live tool call needs the plugin to be reloaded. If the running session
  keeps the old plugin, the report says so and the check moves to a restarted
  session.

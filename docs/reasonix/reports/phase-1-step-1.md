# Phase 1, Step 1 report

**Date:** 2026-10-20
**Scope:** the step 1 scaffold, including the follow-up pass that closed three gaps.

## Files created

- `pyproject.toml`, `.python-version`, `.gitignore`, `.env.example`
- `.loop/adapter.json`, `config/extraction.yaml`
- `src/product_hunter/__init__.py`, `src/product_hunter/cli.py`,
  `src/product_hunter/config.py`
- `src/product_hunter/llm/__init__.py`, `src/product_hunter/llm/base.py`,
  `src/product_hunter/llm/opencode.py`
- `tests/test_smoke.py`, `tests/test_llm_contract.py`
- `README.md`, `docs/reasonix/reports/phase-1-step-1.md`

## Follow-up changes

- `src/product_hunter/llm/base.py`: `TextGenerator.check()` added to the
  protocol. `doctor()` already called it, so the protocol now matches.
- `src/product_hunter/llm/opencode.py`: `json.dumps(body)` became
  `json.dumps(body, ensure_ascii=False)`, so non-ASCII excerpts stay readable
  instead of ballooning into `\uXXXX` escapes on the command line.
- `README.md`: project README, with the route named as OpenCode V2.
- `docs/reasonix/reports/phase-1-step-1.md`: this report.

## Review fixes

The first review returned `no ship` with two Must findings. Both are fixed:

- The input guard trusted a caller-declared `input_chars`. That field is gone.
  `generate()` now uses `len(request.prompt)`, guards the configured limit, and
  also guards the serialized body against `MAX_COMMAND_LINE_CHARS = 30_000`
  before spawning the CLI.
- Pydantic lax mode accepted `{"data":{"text":123}}` by coercion. The generate
  envelope, server info, and model registry now validate with `strict=True`.

A third finding, `doctor()` leaking a traceback for non-provider errors, is
fixed by a CLI-boundary `except Exception` that prints JSON and returns 1.

The second review returned `ship` with no Must findings. It left one Should
note: the body guard counts Python code points, while Windows counts UTF-16
units. The default `max_input_chars` of 12,000 keeps the body far below the
limit, so this is deferred to step 2 as a hardening note.

## Commands and verbatim output

`uv run pytest`:

```text
.................                                                        [100%]
17 passed in 0.17s
```

`uv run ph --help` (exit 0):

```text
usage: ph [-h] {doctor,version} ...

Product Hunter: evidence-driven pain point research.

positional arguments:
  {doctor,version}
    doctor          check the configured provider and model
    version         print the package version

options:
  -h, --help        show this help message and exit
```

`uv run ph version`:

```text
0.1.0
```

`uv run ph doctor`:

```text
{"ok": true, "version": "2.1.0", "providerID": "opencode-go", "modelID": "deepseek-v4.1-flash", "status": "active", "enabled": true}
```

## Provider failure note

The implementer node failed at the provider after writing the code. Rung 1 hit
an unavailable endpoint; rung 2 failed on an expired key. The code and tests were
on disk before that failure, and the verifier later ran the gate and recorded
PASS at stage 5.

## Gates and review

- Code gate: PASS. `uv run pytest` 17 passed; `ph --help`, `ph version`, and a
  live `ph doctor` all exit 0.
- HARDEN: PASS. Two low observations, both addressed or deferred: strict
  Pydantic validation is now in place, and the `config/sources.yaml` rule will
  become enforceable in step 2 when that file exists.
- REVIEW: `ship` on the second pass, after the two Must fixes.
- Acceptance run: not part of step 1, which is a scaffold gate only.

## Next step

Build the source registry and the fetch safety layer: `config/sources.yaml`,
URL admission, robots, retry, and the content-addressed raw store.

Known hardening note for step 2: measure the command-line guard in UTF-16 code
units, not Python code points, or keep `max_input_chars` well below 30,000.

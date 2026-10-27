# Dispatch prompt — Stage 6 HARDEN

**Node:** `loop-free-harden`
**Repo:** `C:\Anubhav\Web Dev Projects\product-hunter`
**Mandate:** report gaps. You cannot edit.

---

## What to harden

The dynamic model router. The files in scope:

- `src/product_hunter/llm/catalog.py`
- `src/product_hunter/llm/discovery.py`
- `src/product_hunter/llm/routing.py`
- `src/product_hunter/llm/opencode.py` (the new `run_opencode_cli` argv runner)
- `src/product_hunter/llm/__init__.py`
- `src/product_hunter/config.py` (the `RoutingConfig` / `load_routing_config` additions)
- `src/product_hunter/cli.py` (the new `ph models` command)
- `src/product_hunter/db/model_routing.py`
- `db/migrations/0002_model_routing.sql`
- `config/model_routing.yaml`

Read `docs/reasonix/specs/model-routing.md` for what the change is supposed to do, and
`docs/reasonix/plans/model-routing-implementation-plan.md` for what it promised.

## The binding rules from `.loop/adapter.json`

- Never call the OpenCode server over raw HTTP; use the documented `opencode api` CLI.
- Never use `shell=True` or a command string for an OpenCode subprocess; pass an argv list.
- Never add a provider secret to this repo.
- Never fetch a URL that is not declared in `config/sources.yaml`.
- Never pass untrusted page text as a shell, tool, or URL argument.
- Never change a test to make it pass.

## Questions to answer

1. **Subprocess safety.** `run_opencode_cli` is the single seam where a subprocess
   starts. Is every call an argv list with `shell=False`? Can any value that reaches
   the argv be influenced by page text, model output, or a database row rather than
   by operator-controlled config? Is the timeout enforced? What happens on a
   non-zero exit, a missing binary, and a prompt larger than the command line?
2. **Output handling.** The model route returns about 490 KB of JSON and 624 entries.
   Is it bounded? Can a hostile or broken server make the parser allocate without
   limit, recurse without limit, or accept a payload that should be refused?
3. **Pattern matching.** Role candidates are globs matched with `fnmatchcase` against
   the catalogue. Is there a way a pattern or a model id causes catastrophic
   behaviour, a wrong match, or a match on a provider the operator did not intend?
   Note that `fnmatch` treats `[` as a character class — is that a hazard for a
   pattern read from a YAML file?
4. **SQL.** Every statement in `db/model_routing.py` and the new migration. Any
   string interpolation of a value into SQL? Any way to write a row that should be
   refused?
5. **The inventory.** `record_catalog` mass-updates every row to `missing` and then
   restores the rows in the current catalogue. Can a partial failure leave the
   inventory in a state that loses a model id, or resurrect a retired one? Is the
   transaction real?
6. **Provenance integrity.** The execution log is the audit trail. Can two different
   models be recorded for the same call, can a success be recorded for a call that
   failed, can a caller forge a `provider_id` that does not match the model that
   ran? Is the error text safe to store and safe to print?
7. **Observability.** The spec asks for provider, model id, role, selection source,
   fallback, catalog version, prompt/config version, latency, success/failure,
   run and stage. Is every one of those actually recorded? Is there any log or
   stderr output that leaks a secret or a whole model answer? Does a discovery
   failure leave any trace a human can see?
8. **Fail-closed behaviour.** "A model disappearing must NOT crash the pipeline."
   Trace what happens when the catalogue is empty, when every candidate is
   unavailable, when the CLI is missing, and when the server returns malformed JSON.
   Is any of those an unhandled crash, an infinite loop, or a silent success?

## Reporting rules

- Quote the code. A finding without a file, a line and a quoted snippet is not a
  finding.
- Sort into **Must fix**, **Should fix**, **Nice to have**.
- A finding needs a concrete failure: an input, a state, and what goes wrong. If you
  cannot describe how it fails, it is a preference, not a finding.
- Say plainly when you find nothing. Do not manufacture findings to look thorough.
- Do not edit any file. Do not run the full test suite; the tree is shared and other
  agents are mid-run.

## One design point to weigh in on

`RoutingError` in `llm/routing.py` currently derives from `Exception`, not from
`ProviderError` in `llm/base.py`. The existing fail-closed paths in
`extraction.py` catch `ProviderError`. If the router is later wired into extraction,
a `NoModelAvailable` would escape those handlers. Say whether this is a real defect
now, a latent one, or fine as it stands, and what the smallest correct fix is.

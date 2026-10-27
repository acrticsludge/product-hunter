# Review prompt: research run observation

**RUN_ID:** robs-20261021-ea2759ad
**Node:** REVIEW (stage 7), rung 1
**You are:** an adversarial reviewer with fresh context, and you read the change to try to prove it wrong. You do not edit.

## What is under review

This run is a read-only observation. The user's request was to call the `product_hunter` tool with `action=list`, then with `action=status` and `research_id=RH-20261021-0003`, and print both outputs verbatim.

The run changed no product code. Its entire diff is eight new files under `docs/reasonix/`:

- `docs/reasonix/specs/research-run-observation.md`
- `docs/reasonix/plans/research-run-observation.md`
- `docs/reasonix/prompts/research-run-observation-implement.md`
- `docs/reasonix/prompts/research-run-observation-verify-code.md`
- `docs/reasonix/prompts/research-run-observation-harden.md`
- `docs/reasonix/prompts/research-run-observation-review.md`
- `docs/reasonix/handoffs/research-run-observation-handoff.md`
- `docs/reasonix/reports/research-run-observation.md`

Seven of the eight exist when you read this. The report file is written at stage 10, after this review, so its absence is expected and is not a gap in the run.

## Scope, and what to ignore

Another agent is working in this tree right now, and its changes are not this run's. Ignore every modified file under `src/`, `tests/`, `config/`, `db/`, and every untracked file that is not one of the eight above, including `eval/`, `src/product_hunter/research/`, `src/product_hunter/eval/`, and `src/product_hunter/db/scheduler.py`. Two files from an earlier phase, `docs/reasonix/handoffs/phase-2-opencode-interface.md` and `docs/reasonix/reports/phase-2-opencode-interface.md`, are staged in the index and are not this run's work either.

Judge this run's eight files on their own. If you want the run's own diff, read those eight files directly rather than trusting a tree-wide `git diff`.

## What to check

1. **The deliverable is exact.** `docs/reasonix/handoffs/research-run-observation-handoff.md` reproduces the two tool outputs. Re-run the tool yourself and compare byte for byte: call `product_hunter` with `action=list`, then with `action=status` and `research_id=RH-20261021-0003`. You may make exactly those two calls and no other action. Report any character of difference, including whitespace and trailing newlines.
2. **The docs do not overstate.** Every claim about the code should hold against the file as it is now. Check the citations in the spec and the handoff against the current source, and check that no sentence claims the read actions are guaranteed not to write, that the database is opened read-only, that reads are audited, or that the read path is risk-free. Quote anything that overstates.
3. **The stage-6 findings are recorded honestly.** The handoff lists four findings. Check each against the code: right severity, right location, and a smallest fix that would actually work. Flag any finding that is understated, overstated, or described in a way that hides the risk.
4. **The gate evidence is accurate.** The handoff reports `uv run pytest` at 875 passed, exit 0, plus 50 passed for `tests/test_registry.py` alone, plus two earlier failing runs. Say whether that account is honest and complete, and whether the claim that the failures belonged to the other agent's live edits is supported.
5. **No test weakening and no radius breach.** Confirm that no test file was touched, no assertion was weakened, and no file outside the eight was written by this run. Check the staged index too: the run's commit must be limited to its own paths.
6. **The prose.** These are documents a human will read. Flag the patterns the house rules ban: filler openers and throat-clearing, rule-of-three lists, contrast templates such as "not X, but Y", more than one em dash per 500 words, paragraphs of near-identical length, and any sentence that sounds generated rather than written. Quote the sentence, name the pattern.

## Rules

- You do not edit anything.
- You do not run the test suite. The gate result is in the handoff for you to audit, not to reproduce.
- You do not start a worker.
- Do not call `product_hunter` with any action other than `list` and `status`.
- Be specific. Every finding needs a file, a line, and a consequence.

## What to report

Findings grouped as `Must fix`, `Should fix`, and `Nice to have`, each with file, line, the problem in one sentence, and what you would do about it. Then:

- A `Verdict:` line, exactly one of `ship`, `ship with fixes`, or `do not ship`.
- A `Lean:` line stating how many lines could be removed from the eight files without losing anything, or `none` if there is nothing to cut.
- A note on anything you could not check.

---

# Re-review: did the fixes land, and did they land correctly

The first pass returned `ship with fixes`. The orchestrator applied the fixes. This is a verification pass over that patch, not a fresh audit. Same rules as above: you do not edit, you do not run the test suite, and you may make only the two `product_hunter` calls named above.

1. **The Must-fix items.** Check each against the file as it is now, and check that the fix is correct rather than merely present.
   - The `connect()` finding: the handoff's smallest fix must not leave the `finally: conn.close()` path raising `UnboundLocalError` and masking the original error. Confirm the fix as written actually avoids that.
   - The finding-1 restatement: confirm the handoff now says the `try` must be guarded or `conn` initialised, rather than "move the call inside the `try`".
   - The finding-3 restatement: confirm the handoff no longer claims `read_only=True` removes the sidecars, and that it says a WAL database needs a `-shm` next to it.
   - The finding-4 restatement: confirm it now says the newline path is closed by `create_run()`'s whitespace collapse at `src/product_hunter/registry.py:521`, and that the risk is a hardening note rather than a live injection path.
2. **The gate account.** The handoff now carries the raw command output, the four failing test names, and the caveat that neither the 875 nor the 50 reproduces on the tree as it stands. Check that the account is now honest and complete against the current `tests/test_registry.py` and the current tree.
3. **The index.** The handoff lists the staged paths. Check that list against the real index now and say whether it still matches, since another agent may have moved it.
4. **Regression sweep.** Re-read the three files that changed most, the handoff, the spec and the plan, and look for anything the fix broke: a citation pointing at the wrong line, a claim that no longer matches the code, a stage row that claims a result that has not happened, or prose that now contradicts the file next to it.
5. **Nothing new.** Did the fix introduce an inaccuracy anywhere else in the eight files? Name it.

Report a `Verdict:` line, a `Lean:` line, the per-item confirmation for the items above, and anything you could not check.

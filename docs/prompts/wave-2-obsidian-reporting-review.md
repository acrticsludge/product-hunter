# Review: Wave 2 Session 8 — Reporting and Obsidian Projection

## Where to work

```
C:\Anubhav\Web Dev Projects\product-hunter\.worktrees\wave2-obsidian-reporting
```

Branch `wave2/obsidian-reporting`. Do not modify main. Do not commit. You
review; you do not fix.

## The diff under review

Everything in this worktree that is not on `main`:

- `src/product_hunter/projection/` — a new package of seven modules.
- `src/product_hunter/cli.py` — one new command, `ph project`.
- `tests/test_projection_sanitize.py`, `tests/test_projection_render.py`,
  `tests/test_projection_source.py`, `tests/test_projection_engine.py`.
- `docs/reasonix/specs/`, `docs/reasonix/plans/`, `docs/reasonix/prompts/`.

Start from the contract and the plan, then read the code:

- `docs/reasonix/specs/wave-2-obsidian-reporting.md` — what the layer must do.
- `docs/reasonix/plans/wave-2-obsidian-reporting.md` — how it is built.
- `src/product_hunter/projection/*.py` and the `ph project` command.

Use the `ponytail-review` skill. Its rubric is the one to apply: is the logic
right, is it safe, does it hold under real load, is the risky code tested, is
it fast enough, and is every line needed. Its finding format is the one to
use: `Must fix`, `Should fix`, `Nice to have`, each explained in plain
English, ending with a `Verdict:` line and a `Lean:` line that says how many
lines could go.

## What to attack

The reviewer who tries to prove this wrong looks for:

1. **The contract.** Does the implementation do what the spec says, field by
   field? Are the fifteen report fields present, in order, with `not recorded`
   in place of anything the database does not hold? Is a missing value ever
   rendered as a zero?
2. **Determinism.** Find any input that makes the same state produce two
   different outputs. Wall-clock time, an unsorted set, a dict that is
   iterated in insertion order, a float printed at a varying precision, a
   timezone that depends on the machine.
3. **Idempotency.** Find any path where a second run over unchanged state
   writes a file, changes a file time, or leaves a temporary file behind.
4. **The prune.** What happens when an opportunity is deleted? What happens
   when the manifest is missing, corrupt, or holds a path outside the vault?
   Can the prune delete a file it did not write?
5. **The reader.** The `opportunities` table does not exist in this
   repository. Check the fallback path, the canonical path, and the path
   where the table exists with a different shape. Check the independence
   counts against the spec's definitions in section 6 and section 12 of the
   full product spec at
   `docs/reasonix/specs/Product-Hunter-Full-Spec.md`.
6. **The sanitizer.** The spec's section 8 lists what must be neutralized.
   Try to get markup through it that Obsidian would act on. The two fixes
   from the hardening pass are in place; look for a third gap.
7. **The outbox.** `db/store.py` enqueues a task with the target `obsidian`.
   Does the handler do what it must, does a failure leave the task pending,
   and does the projection ever write to the database?
8. **The tests.** Do they assert the behaviour, or do they assert the
   implementation? Is there a behaviour in the spec that no test covers? A
   test that would pass against a broken implementation is a finding.
9. **The lean question.** What can go? The plan said the layer would be
   small. `render.py` is large; find the parts that are not carrying their
   weight, and say how many lines could leave.

## Report

Give me the findings in the `ponytail-review` format, with the file and line
for each, and a `Verdict:` line of exactly one of `ship`, `ship with
follow-ups`, or `block`. If you cannot prove the layer is correct, say which
part you could not prove, rather than praising the rest.

Quote the code you are reviewing. Do not report a style preference as a
finding. Do not report the absence of the canonical opportunity tables: the
spec explains why they are not here, and the reader is built to degrade when
they arrive.

# Wave 3 Session 4: review the advanced collection ladder

Node: REVIEW. Branch: `wave3/scraping-ladder`. Base: `00a6ca3`.

Read the diff against the base commit and the new files. The contract is
`docs/reasonix/specs/wave-3-scraping-ladder.md`.

## Try to prove the code wrong

1. A way to reach the network that skips the source policy.
2. A way a captured page's content could become an executable argument, a
   URL, a command or a path.
3. A credential reaching a log, a metadata field, an error detail or a
   stored artifact.
4. A rate-limit or budget escape: a retry that skips the per-source cap or
   the minimum gap, a fallback chain that spends more than it is charged
   for.
5. A wrong escalation: a policy refusal that escalates to a more
   expensive tool, or a tool failure that escalates to a rung the source
   did not declare.
6. A wrong outcome classification, or a lost attempt.
7. A crash, an unhandled exception or a resource leak.
8. A claim in the spec that the code does not make.
9. A test that asserts something weaker than it appears to, or one that
   would pass with the control removed.

## The output

MUST FIX, SHOULD FIX, NOTE, a one-line verdict, a `Lean:` line, and what
was not checked. Cite `file:line`. Do not report style preferences. If a
control is correct, do not write a paragraph about it.

The reviewer's model is a different family from the implementer's on
purpose. Read the connected code, not only the diff.

# Dispatch — Stage 7 REVIEW (adversarial)

**Node:** `loop-free-review-1`
**Purpose:** try to prove the work wrong. The node cannot edit.
**Three passes:** the first returned do-not-ship, the second ship-with-fixes, the
third ship.

## What was asked of the node, first pass

The node was given the mission itself as the acceptance criteria, and told that
the spec was written by the same author as the code, so agreement between them
proves nothing. It was asked to judge the code against the mission.

It was asked to attack, specifically: the non-trivial zero, the claim validator,
the opportunity lifecycle, the startup killer, the query families, the
idempotency and the resume, the tests (a test that would still pass if the rule
it covers were broken is the finding), the docs against the code, the leanness of
the surface, and all fourteen mission deliverables.

Findings were to carry a severity, the file and line, the path or the code that
shows it, and the smallest fix. It was told to be adversarial and not to pad.

## What came back, first pass

`do-not-ship`. Two must, eight should, five nice.

The two musts were the wave's own headline guarantees, and both were false:

- **M1.** A resumed investigation lost the evidence and reached the opposite
  conclusion. The checkpoint held the attempt but not the classified results, so
  a resumed run re-derived everything from nothing and published
  `no_strong_competitor` and `no_counter_evidence_found` as supported claims
  after the searches had actually found a competitor and twenty-three
  counter-evidence items. The test that should have caught it used an executor
  that returned nothing, so there was nothing to lose.
- **M2.** `request.areas` could drop the counter-evidence area, so a run that had
  never searched a mandatory counter-evidence family ended `supported`. The same
  hole crashed the pipeline with a `KeyError`.

The node also found that the query families, the coverage rule, the transition
table and the seven code-generated families were good, and that no path to
`VALIDATED` without a human existed.

## The fix round, and the second pass

All fifteen findings were fixed. The node was then asked to verify each one by
re-running its own attacks, to look for the regressions the fixes could have
caused, and to re-state the mission checklist.

Second pass: `ship-with-fixes`, 0 must, 5 should, 8 nice. Both musts were
confirmed closed by re-running the probes. The mission checklist moved to
delivered on all fourteen items, and the mission test list moved from
"checkpoint/resume and idempotent investigation are present but vacuous" to both
being real.

The node withdrew one of its own lean claims, agreeing that replacing the
fallback ladder would have recommended `HUMAN_REVIEW` for a contradicted
opportunity.

## The final pass

The last thirteen findings were fixed, and the node was asked for a focused
confirmation: verify the thirteen, check the one deletion for lost coverage,
check the three widened test fixtures for weakening, and sweep the six changed
places for regressions.

Third pass: **`ship`**, 0 must, 0 should, 2 nice. The two nice findings were
one-liners (the checkpoint cap did not hold across a resume, and a stale line in
the handoff), and the orchestrator closed both.

It recorded `loopstage --verb pass --stage 7`.

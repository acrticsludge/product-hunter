# Dispatch — Stage 6 HARDEN (security and observability)

**Node:** `loop-free-harden`
**Purpose:** report the gaps in the new surface. The node cannot edit.
**Twice:** the first pass raised findings, the second confirmed the fixes.

## What was asked of the node, first pass

Read the spec, the handoff, the wave 1 substrate, `safety.py`, `fetch.py` and
section 37A of the master spec, then assess:

1. Untrusted text reaching a message, a reason or a persisted field. Trace every
   free-text string the executor supplies and name the sink it reaches.
2. URL handling: whether this wave fetches, whether a credential can reach a
   persisted field, and whether the domain extraction survives a malformed URL, a
   port, an IDN host or an IPv6 literal.
3. The result-ref and artifact-ref shapes the bridge produces.
4. The zero-result rule as a security control, in both directions: can a zero
   the investigation did not authorise be accepted, and can a real zero be blocked?
5. Whether a hostile executor can exceed a budget, and whether the spend is
   proportional to what was consumed.
6. Unbounded growth in every list the executor can grow.
7. Determinism: set or dict iteration order leaking into an output, and whether
   the output differs under two `PYTHONHASHSEED` values.
8. Validation at the trust boundary: fields where a nonsense value would be
   accepted and would change a conclusion.
9. Observability: whether a failed investigation is diagnosable from what it
   returns.
10. The never-cut list: validation at trust boundaries, error handling, security,
    accessibility.

Findings were to carry a severity, the file and line, the path from the
untrusted input to the sink, and the smallest fix. Documented limits in the
spec's section 9 were not to be reported as findings, but an understated limit was
to be called out.

## What came back

Six findings: one must (unbounded executor-supplied lists, a memory blowup), three
should (credentials persisted in plaintext, spend not proportional to consumption,
a zero floor that silently switched the check off) and two nice (an artifact id
carrying executor characters, an unbounded `result_count`). The node also
confirmed the untrusted-text rule, the no-fetch rule, the `_host_of`
crash-safety, the artifact ref shape, the zero-rule direction, the
budget-check-before-call rule and the reasons hygiene as clean.

It recorded `loopstage --verb fail --stage 6`.

## The fix round

The five findings were fixed in the implementation dispatch, and one residual was
closed further than instructed: a URL with a malformed port no longer keeps its
credentials, because the port is dropped and the host and path are kept. A URL
the parser cannot read at all is not stored.

## What the second pass confirmed

The node was asked to verify each fix by reading the code and by re-running its
own attacks, to look for the regressions the fixes could have caused, and to
confirm the interaction with the wave 1 gate.

All six were confirmed closed, with the fail-closed behaviour of the over-cap
refusal checked end to end: the failed attempt makes the area incomplete, the run
ends `incomplete`, the zero is refused, and the wave 1 gate agrees. The radius
was confirmed clean: only `research/evidence.py` (additive) and
`research/__init__.py` (re-exports) differ from the wave 1 package.

It recorded `loopstage --verb pass --stage 6`.

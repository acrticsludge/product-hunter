# Wave 3 Session 4: report the advanced collection ladder

Node: REPORT. Branch: `wave3/scraping-ladder`.

The report goes to `docs/reasonix/reports/wave-3-scraping-ladder.md`. The
mission wrote `docs/resonix/reports/...`; the repository's docs root is
`docs/reasonix`, so the file is there, the way the Wave 2 report handled
the same slip.

## What the report must say

- the commit hash
- the four adapters and what each one takes
- the rung table and how the fallback order is decided
- the tests, with the gate result verbatim
- the credential blockers: what is needed and what is not there
- the security findings, including the ones that were closed and the ones
  that are open
- what was skipped and what was not checked, named rather than buried

## The rule about honesty

The crawler, the browser and the managed client are not installed in this
environment and there are no credentials for them. The report says so
plainly. No live service was contacted and no live success is claimed.

The drivers were exercised against their protocol, and the three that
talk to a library were exercised against a fake library that records what
it was asked to do. That is what the tests show, and the report must not
imply more.

## The house voice

Plain sentences. No rule of three. No em-dash asides. No "not just X,
but Y". No word that only appears in marketing copy. The report is read
by an operator who wants to know what to trust.

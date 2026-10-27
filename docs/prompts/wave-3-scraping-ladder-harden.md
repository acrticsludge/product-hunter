# Wave 3 Session 4: harden the advanced collection ladder

Node: HARDEN. Branch: `wave3/scraping-ladder`.

This node reports. It does not edit.

## The threat list to hold the code to

| threat | the control that must hold |
|---|---|
| SSRF through a browser | every URL the tool requests goes through `UrlGate.admit` |
| a redirect into a private address | every hop goes through `UrlGate.redirect`, which re-resolves and re-checks |
| private, loopback, link-local and metadata addresses | `admit_url` resolves and refuses, the same rule the shared path applies |
| an IP-literal host, userinfo, a scheme outside the allowlist, a nonstandard port | `admit_url` refuses each |
| DNS rebinding | the host is resolved before the request and again on every hop |
| a browser that keeps a profile | the context is created fresh, with no storage state |
| a browser that holds cookies | a fresh context has no cookies, and the adapter never writes one |
| a browser that downloads | downloads are off in the policy and in the context |
| a browser that reaches a local file | `file:` is not in the scheme allowlist |
| a page that carries instructions | the body is stored as bytes and hashed, and the result carries no page text |
| a credential in a log or a result | the token is redacted from every detail and never enters `metadata` |
| an actor that a page chose | the actor id is configuration, and a source cannot name one |
| a tool that runs arbitrary page script | the browser runs with no permissions, no extensions, a bounded navigation time and no service worker |

## The rates to hold the code to

- a retry is a request: it waits the source's minimum gap and it counts
  against the per-source cap
- the contract's caps are charged per tool attempt, and a fallback that
  ran two tools is charged two fetches
- the concurrency bound is taken before the budget slot, so the gap is
  waited out inside the bound
- no IP rotation and no rate-limit bypass anywhere

## The record to hold the code to

- every attempt leaves a row with a reason code, a tool and a bounded
  detail
- no unbounded string, no URL and no page text ride into a log or a row
- a capture taken by a tool is written to the fetch log, and the line
  parses as a `FetchRecord`, because `Store.import_run` reads the log
  that way

## The output

MUST FIX, SHOULD FIX, NOTE, then a one-line verdict, then what was not
checked. Cite `file:line`. Quote the code you rely on. Do not report
style preferences and do not manufacture findings to look thorough.

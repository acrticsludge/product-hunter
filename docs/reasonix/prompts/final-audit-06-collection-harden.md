# Prompt — final audit 06, harden

RUN_ID: final-audit-06

Role: mandatory security and observability pass, stage 6. Read-only. Report
gaps; change nothing.

## Task

Take the collection path apart the way an attacker would, then say what is
left standing.

## Trees

- `C:\Anubhav\Web Dev Projects\product-hunter` (main, shared baseline)
- `C:\Anubhav\Web Dev Projects\product-hunter-wt\wave3-scraping-ladder`
- `C:\Anubhav\Web Dev Projects\product-hunter-w3c`

## Questions

1. **Robots.** Which URLs get a robots.txt check on each rung? Entry only, or
   redirect hops too? On the browser and crawler rungs, are sub-resource
   requests checked, or only the entry URL? Is robots consulted for the
   managed rung beyond the entry URL?

2. **SSRF and private addresses.** Does every URL a browser or crawler touches
   pass the same public-IP check as the HTTP path? Is the resolved address
   pinned for the browser's own DNS? Does the IPv6 handling cover 6to4
   (`2002::/16`) and Teredo (`2001::/32`) addresses that embed a private IPv4
   address? `uv run python -c "..."` is available for the `ipaddress`
   behaviour.

3. **Rate-limit accounting.** How many actual HTTP requests does one browser
   capture make, and how many of them count against
   `source.rate_limit.max_requests_per_run` and `min_interval_seconds`?

4. **Caching.** Are ETag and Last-Modified used on every rung or only on the
   HTTP rung? Where is `Validators` consumed?

5. **Redirects.** On the browser rungs, are redirect hops checked against the
   domain allowlist and the https-downgrade rule?

6. **Downloads.** Blocked on every rung? Any path where a browser writes to
   disk?

7. **Browser isolation.** Fresh context per capture? Any profile, storage
   state, cookie jar, persistent context or shared browser state?

8. **Managed scraper.** What is sent to the actor? Any domain allowlist in the
   actor input? Does the token reach a log, a capture, an error message, a
   database row or a metadata dict? Any cost ceiling?

9. **Escalation control.** Is the canonical order enforced by code or only
   described in a docstring? Can a source config skip rungs or run backwards?

10. **Bypass.** Any network path in any tree that avoids
    `Fetcher` / `admit_url` / `UrlGate`?

11. **Observability.** Does every attempt leave a record, including a refused
    policy decision, a ladder exhaustion and a tool that was not installed? Is
    the rung that actually ran recorded?

## Output

Numbered answers, each CONFIRMED-OK or FINDING with severity, file:line,
quoted code and consequence. Then the findings that must be fixed before the
Wave 3 branches are integrated, in priority order. No edits.

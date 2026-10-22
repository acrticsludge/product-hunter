# Prompt — final audit 06, gather

RUN_ID: final-audit-06

Role: read-only data gathering. No edits.

## Task

Map the collection surface across nine trees and report what exists.

## Trees

1. `C:\Anubhav\Web Dev Projects\product-hunter` (main)
2. `C:\Anubhav\Web Dev Projects\product-hunter-wt\wave3-scraping-ladder`
3. `C:\Anubhav\Web Dev Projects\product-hunter-w3c`
4. `C:\Anubhav\Web Dev Projects\product-hunter-w3a`
5. `C:\Anubhav\Web Dev Projects\product-hunter-wt\wave3-analytics`
6. `C:\Anubhav\Web Dev Projects\product-hunter-wt\wave3-observability`
7. `C:\Anubhav\Web Dev Projects\product-hunter\.worktrees\wave3-opencode-control-plane`
8. `C:\Users\anubh\AppData\Local\Temp\opencode\wt-wave3-qdrant`
9. `C:\Users\anubh\.local\share\opencode\worktree\44767c\wave3\subq-integration`

## Questions

A. Every network call site: `httpx`, `requests`, `urllib`, `aiohttp`,
   `playwright`, `apify_client`, `crawl4ai`, `huggingface_hub`, `subprocess`.
   For each hit: file, line, the line, and whether it passes through
   `admit_url` or `SourceRegistry.require`.

B. Discovery saturation: the mechanism, the caps, and whether any production
   caller consults it. Per-run, per-source and per-family query caps, with
   values and file:line, and whether the counters survive a restart.

C. Source health: the structures, how `consecutive_failures` is computed, and
   whether any quarantine path actually stops collection.

D. Failed versus empty: the exact reason codes and the code that separates
   the two.

E. Wave 3 changes to discovery and to `collection/state.py`, branch by branch.

F. `config/sources.yaml` in every tree: the `collector:` values, any
   `fallbacks:` values, and any difference from main.

## Output

Plain text, structured, with file:line citations and quoted key lines. No
edits, no summaries that lose detail.

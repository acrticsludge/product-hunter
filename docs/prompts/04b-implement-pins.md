# Stage 4 dispatch, follow-up 2: restore the version pins

**Node:** `loop-free-implement-1` (same session as follow-up 1)
**Radius (only this path may be written):**

```
opencode.json
```

---

## Situation

The operator hand-edited `opencode.json` to add the Apify MCP server as a remote server. That entry is theirs and it stays exactly as written.

The same edit also dropped the two package version pins that follow-up 1 applied. The pins were the fix for a HARDEN finding, and the file the operator edited was a copy from before that fix, so the pins were lost as a side effect rather than a decision. The pinning decision stands and this dispatch restores it.

## The change

Add the version suffix to the two package names, and change nothing else in the file:

- `mcp-server-qdrant` becomes `mcp-server-qdrant@0.8.1`
- `mcp-server-duckdb` becomes `mcp-server-duckdb@1.1.0`

Keep the `apify` entry byte-identical:

```json
"apify": {
  "type": "remote",
  "url": "https://mcp.apify.com/",
  "enabled": true
}
```

Keep the operator's formatting, including the multi-line `command` array for the duckdb server. Do not reformat the file, do not reorder the servers, and do not add, remove, or rename any server.

## What to report back

- the exact final text of `opencode.json`
- confirmation that it parses as JSON, using `Get-Content -Raw opencode.json | ConvertFrom-Json`
- the line count
- anything else you think is wrong, with the reason, without fixing it and without widening the radius

Do not run the servers, start any container, or write any other file.

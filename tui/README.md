# ph-tui

OpenTUI (React) interactive wrapper over the `ph` CLI.

## Dev

```bash
cd tui
bun install
bun run src/index.tsx
```

Or via the launcher:

```bash
ph tui
```

Requires Bun >= 1.4 and Python 3.12+ with the project venv (`uv`).

## Architecture

- `src/index.tsx` — entrypoint (createCliRenderer + createRoot).
- `src/App.tsx` — layout, nav tree, views.
- `src/bridge.ts` — NDJSON bridge client (spawns `uv run ph tui-bridge`).
- `src/store.ts` — zustand store.
- `src/theme.ts` — palette/tokens.

The bridge (`src/product_hunter/tui_bridge.py`) holds a read-only SQLite
connection and streams snapshots, logs, and command results.
EOF
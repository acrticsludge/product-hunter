# Spec — Interactive OpenTUI Wrapper for `ph` CLI

**Slug:** `ph-tui`
**Status:** Draft (Plan mode)
**Date:** 2026-10-26
**Owner:** product-hunter

## 1. Goal

Add an interactive terminal UI (`ph tui`) that provides ergonomic, full-parity access to every `ph` command and data surface, with per-entity scoping (runs, probes, jobs, sources, …), live progress, scoped logs, and an OpenCode-inspired visual design. The user must never need to memorize command syntax or parse raw JSON.

## 2. Non-goals

- Reimplementing product-hunter logic in TypeScript. The TUI is a presentation layer; all writes go through the existing `ph` commands.
- Replacing the CLI. `ph` remains the source of truth and automation interface.
- A web/GUI app. Terminal only.

## 3. Constraints

- OpenTUI requires Bun ≥ 1.4. Bun is available (`1.4.2`); network install of `@opentui/core` works.
- Plan mode forbids code edits; implementation happens in build mode.
- All data access must respect `PH_DB_PATH` and never hardcode paths.
- Read-only DB access in the live bridge must not block writers (use `busy_timeout`, read-only connection).
- No secrets may appear in records, logs, or TUI state.

## 4. Architecture

```
┌─────────────────────────────┐  NDJSON stdin/stdout  ┌──────────────────────────────┐
│ tui/  (Bun + OpenTUI React) │ ◄──────────────────► │ src/product_hunter/          │
│ panels, forms, keybindings, │  snapshot/delta/log/  │ tui_bridge.py                │
│ theme, progress, log tail   │  ack/result           │ read-only SQLite poll 250ms  │
└─────────────────────────────┘                       │ + shells `ph` for actions     │
                                                     │ + tails log files (tagged)   │
                                                     └────────────┬─────────────────┘
                                                                  │
                                                                  ▼
                                                     SQLite (db/product_hunter.db)
                                                     + data/processed/, logs/
```

- **Bridge** (`src/product_hunter/tui_bridge.py`): long-lived Python subprocess. Holds one read-only SQLite connection; polls tables every 250ms; emits `snapshot` only when a content hash changes. Accepts command requests on stdin (JSON), executes them by shelling out to `ph` (argv list, no shell), returns `{ok, stdout, stderr, exit_code}`. Tails log files and emits `log` lines tagged with `run_id`/`probe_id`/`probe_public_id`.
- **TUI** (`tui/`): Bun + OpenTUI React. Spawns bridge, consumes NDJSON, maintains an in-memory store, renders panels. Sends command requests over stdin.
- **Launcher**: new `ph tui` command checks for Bun, sets `PH_DB_PATH`, execs the TUI entrypoint. Hidden `ph tui-bridge` starts the bridge (or the TUI spawns the Python module directly).

## 5. Navigation model (k9s + lazygit inspired)

- Two-pane layout: left **nav tree** (collapsible groups with count badges), right **content**.
- Master-detail: selecting a group shows a list; `Enter` drills into an entity's detail; `Esc` goes back one level; breadcrumbs at top (`Home › Runs › RH-… › Logs`).
- Contextual footer keybinds; `?` always shows the current view's keys.
- Consistency: `Enter` open, `Esc` back, `l` logs, `/` filter, `r` refresh, `f` follow, `q` quit, `:` command palette, `z` zoom pane.
- Command mode `:` jumps to any view/command (full-parity fallback).
- `/pattern` filters the current list.
- State (scroll, selection) preserved across tab switches.
- Filtering and keybindings are global; actions are contextual to the focused entity.

## 6. Per-entity structure (ergonomic core)

Logs are never shown globally by default; they are reached through the owning entity.

### Research Runs
- List: run_id, objective (truncated), status, stage, progress %, resumes, updated_at, probe_id.
- Detail sub-tabs (`1`–`6`): Overview (progress bar, stage/step, budget spent vs limits, worker_active), Jobs, Checkpoints, Events, Artifacts, Logs.
- Logs tab scoped to that run: tails `data/processed/<run_id>/` + lines mentioning the run_id.
- Contextual keys: `c` continue, `p` pause, `r` retry, `x` cancel, `g` report, `e` extract.

### Probes
- List: public_id, probe_id, status, objective, cycles (total/success/failed/blocked), backoff multiplier, next_cycle_at countdown, current_run_id, worker_active.
- Detail sub-tabs (`1`–`6`): Overview, Cycles, Active Run (link), Errors, Logs (probe-scoped), Report.
- Keys: `s` start, `p` pause, `u` resume, `x` stop, `r` report, `n` new.

### Other entities
Jobs & Leases, Sources & Candidates, Database, Index, Project, Models & Bench, Autonomy, Observe — each with its own scoped detail and (where applicable) scoped logs.

### Home (Dashboard)
Health summary, recent runs/probes with mini progress bars, recent errors, quick actions.

## 7. Full-parity command coverage

Every `ph` command must be invocable. Dedicated panels exist for the high-traffic surfaces (Runs, Probes, Jobs, DB, Sources, Index, Project, Models/Bench, Autonomy, Observe, Logs). Any command without a dedicated form is reachable via the Command Palette (`:`), which renders its JSON output in a scrollable pane. This guarantees parity from day one even before polished forms land.

Command groups to cover: `doctor`, `version`, `sources`, `source-candidates`, `fetch`, `extract`, `review-sheet`, `eval`, `report`, `models`, `bench`, `db`, `autonomy`, `project`, `index`, `research`, `probe`, `observe`.

## 8. Visual design (OpenCode-inspired)

**Palette** (dark base, single source of truth in `tui/src/theme.ts`):
- Background: `#0b0d10` base, `#13161b` panel, `#1a1e24` elevated/hover.
- Text: `#e6e8eb` primary, `#9aa0a6` secondary, `#5c6370` muted.
- Accent: `#f5a524` amber — focus, active tab, primary action (used sparingly).
- Status: success `#3fb950`, warning `#d29922`, error `#f85149`, info `#58a6ff`, running `#a371f7`.
- Borders: `#2a2f36` default, `#3a4149` focused panel → accent on focus.

**Typography & structure**
- Monospace throughout; bold only for headers/active item; no ALL-CAPS except tiny section labels.
- Panels: thin box-drawing borders with the title in the top border; focused panel border switches to accent.
- Breadcrumbs top-left, muted, current segment in accent.
- Status badges as colored pills: `● RUNNING`, `✓ SUCCESS`, `⏸ PAUSED`, `✗ FAILED`, `… QUEUED`.

**Components**
- Progress bars: filled `█` accent, empty `░` muted, percent + stage label, animated shimmer while active.
- Lists: hover highlight only; selected row has accent left bar `▌` + elevated bg.
- Logs: level-colored prefixes (`ERR` red, `WARN` yellow, `INFO` blue), timestamps muted, entity id in accent, pulsing `● LIVE` follow indicator.
- Forms: inline labels, accent focus ring, red validation under field.
- Empty states: centered muted text + hint keybind.
- Toasts: bottom-right, colored left border by severity, auto-dismiss 3s.

**Motion**: restrained 120ms transitions on selection/focus; spinner only for active operations; log follow pulses.

**Responsive**: below ~80 cols collapse nav tree to icons, hide secondary columns, tabs become dropdown (OpenTUI breakpoints).

**Theming**: tokens in `tui/src/theme.ts`; optional `PH_THEME=light` override (v2).

## 9. Live updates

- Bridge polls SQLite every 250ms; emits `snapshot` only when content hash changes → no render storms.
- Log files tailed with `fs.watch` + incremental read; lines tagged with entity ids.
- Progress bars, job stage chips, probe countdowns update instantly.

## 10. Data contract (NDJSON)

Bridge → TUI lines:
- `{type: "snapshot", hash, data: {db_status, runs, probes, jobs, sources, ...}}`
- `{type: "delta", hash, changes: {...}}` (optional; v1 may just resend snapshot)
- `{type: "log", level, ts, entity: {kind, id}, line}`
- `{type: "result", req_id, ok, stdout, stderr, exit_code}`
- `{type: "error", message}`

TUI → bridge lines:
- `{type: "cmd", req_id, argv: [...]}` (argv list, never a shell string)
- `{type: "subscribe", entities: [...]}` (optional filter)

## 11. Security / safety

- Bridge opens SQLite read-only with `busy_timeout`.
- Actions execute via `ph` with argv lists (no `shell=True`).
- No secrets in snapshots, logs, or TUI state; bridge redacts known secret env vars.
- `ph tui` respects `PH_DB_PATH`; never writes outside declared paths.

## 12. Risks & mitigations

| Risk | Mitigation |
|---|---|
| Process churn / slow actions | Bridge keeps one read connection; actions shell `ph` (opens own conn) |
| SQLite contention | Read-only + busy_timeout; actions serialize through `ph` |
| Scope creep (full parity) | Command Palette fallback guarantees parity day one |
| OpenTUI version churn | Pin `@opentui/core@0.7.0` |
| Large snapshots | Hash + delta; tag logs by entity |
| No worker active | TUI shows "worker inactive" badge; actions still work |

## 13. Acceptance criteria

1. `ph tui` launches the UI when Bun is present; prints a helpful message otherwise.
2. Home, Runs, Probes, Jobs, DB, Sources, Index, Project, Models/Bench, Autonomy, Observe, Logs, Command Palette views render without errors.
3. Selecting a run and pressing `Enter` shows its detail with all 6 sub-tabs; Logs tab shows only that run's logs.
4. Progress bars update within 1s of a DB change.
5. Every `ph` command is invocable (dedicated form or Command Palette).
6. No secrets appear in any snapshot or log line.
7. Bridge survives a `ph` action failure and reports it as a toast, not a crash.

## 14. Open questions (resolved)

- Q1 Safety gate on actions: **No** (run directly).
- Q2 Logs to tail: **Both** `logs/` and `data/processed/<run_id>/`.
- Q3 Directory name: **`tui/`**.
- Visual style: OpenCode-inspired dark theme (section 8).
EOF
echo written
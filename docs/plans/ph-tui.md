# Plan — Interactive OpenTUI Wrapper for `ph` CLI

**Slug:** `ph-tui`
**Spec:** `spec.md` (same dir)
**Mode:** Build (implement after switching agents)
**Risk:** medium (new module + new CLI command + new dependency; no auth/payment/secret/infra paths)

## Phase 0 — Feasibility (DONE)

- [x] Bun available (1.4.2).
- [x] `@opentui/core@0.7.0` installs from npm with network.
- [x] `ph` JSON output contract confirmed (commands print one JSON object).

## Phase 1 — Bridge (Python)

Deliverable: `src/product_hunter/tui_bridge.py` + hidden CLI command `ph tui-bridge`.

Tasks:
1. Create `tui_bridge.py` with:
   - arg parsing (`--db` optional, defaults to `PH_DB_PATH`/config).
   - read-only SQLite connection with `PRAGMA busy_timeout=5000`.
   - snapshot builder: db_status counts, runs (via `registry` helpers or direct SQL), probes (via `probes` helpers), jobs, sources, autonomy status. Hash with `hashlib.sha256` of canonical JSON.
   - poll loop 250ms; emit `snapshot` only when hash changes.
   - stdin reader thread: parse JSON lines `{type: "cmd", req_id, argv}`; execute via `subprocess.run(argv, capture_output=True, text=True, cwd=PROJECT_ROOT, timeout=300)`; emit `result`.
   - log tailer: watch `logs/` and `data/processed/` with `watchdog` or polling; emit `log` lines tagged with entity id (best-effort regex for `run_...`/`PRB-...`/`prb_...`).
   - redaction of env secrets in emitted stdout/stderr.
   - graceful shutdown on SIGTERM/SIGINT.
2. Add `tui-bridge` to `cli.py` dispatch (hidden, no help emphasis) calling `tui_bridge.main()`.
3. Unit test `tests/test_tui_bridge.py`: snapshot schema, hash stability, cmd execution, redaction.

## Phase 2 — TUI scaffold (Bun)

Deliverable: `tui/` project runnable via `bun run tui/src/index.tsx`.

Tasks:
1. `tui/package.json` with pinned deps: `@opentui/core@0.7.0`, `@opentui/react@...`, typescript, bun-types.
2. `tui/tsconfig.json`.
3. `tui/src/theme.ts` — palette + tokens (section 8).
4. `tui/src/bridge.ts` — spawn `python -m product_hunter.tui_bridge` (or `ph tui-bridge`), NDJSON reader, command sender, reconnect.
5. `tui/src/store.ts` — in-memory store + hash-based update + scoped log buffers.
6. `tui/src/layout.tsx` — two-pane layout, nav tree, breadcrumbs, footer keybinds, toast.
7. `tui/src/nav.ts` — navigation state machine (home/list/detail/sub-tab), back stack.
8. `tui/src/keybinds.ts` — global + contextual key maps.
9. `tui/src/components/`: Panel, Badge, ProgressBar, Table, LogLine, EmptyState, FormField, Toast.
10. `tui/src/index.tsx` — entrypoint wiring.

## Phase 3 — Read-only views

Tasks (one component file per view):
1. `views/Home.tsx` — dashboard.
2. `views/Runs.tsx` — list + detail with 6 sub-tabs.
3. `views/Probes.tsx` — list + detail with 6 sub-tabs.
4. `views/Jobs.tsx`.
5. `views/Database.tsx`.
6. `views/Sources.tsx`.
7. `views/Index.tsx`.
8. `views/Project.tsx`.
9. `views/ModelsBench.tsx`.
10. `views/Autonomy.tsx`.
11. `views/Observe.tsx`.
12. `views/Logs.tsx` (system + per-entity scoped).
13. `views/CommandPalette.tsx` — fuzzy search over all `ph` commands, renders JSON output.
14. `views/Help.tsx`.

Each view: list → `Enter` detail → sub-tabs via number keys; `Esc` back; `l` opens scoped logs.

## Phase 4 — Actions & forms

Tasks:
1. Form components for: probe create/start/pause/resume/stop/report; run continue/pause/retry/cancel/report/extract/review-sheet; db recover/import-run/check; index sync/rebuild/search; project build; fetch; extract; bench run/compare; source-candidates discover.
2. Wire contextual keys (`c`, `p`, `r`, `x`, `s`, `u`, …) to open the relevant form/modal.
3. Confirmation-free execution (per Q1); toast on result.

## Phase 5 — Live mode & polish

Tasks:
1. 250ms-driven progress bars + job stage chips + probe countdowns.
2. Log follow (`f`) with `● LIVE` pulse.
3. Timestamp toggle (`t`), wrap toggle (`w`) in logs.
4. Responsive breakpoints (nav collapse, column hiding).
5. Empty states + error states.
6. Reconnect on bridge exit.

## Phase 6 — Integration & docs

Tasks:
1. Add `ph tui` command to `cli.py`: check Bun, set `PH_DB_PATH`, exec TUI (use `os.execvp` or subprocess; pass through stdio). If Bun missing, print install hint and exit 1.
2. Add `tui/README.md` with dev/run instructions.
3. Update root `README.md` with a "TUI" section.
4. Smoke test: launch `ph tui` under a pseudo-TTY (`script`/`tmux`) and assert it renders the Home view without crashing.

## Phase 7 — Hardening

Tasks:
1. Self-review: redaction, no secrets, read-only bridge, argv-only execution.
2. Run `uv run pytest` targeted (bridge unit tests + existing suite).
3. Verify no regression in CLI JSON output.

## Ordering & dependencies

- Phase 1 and 2 can start in parallel (bridge contract is in spec section 10).
- Phase 3 depends on Phase 2 scaffold.
- Phase 4 depends on Phase 3 views.
- Phase 5 depends on Phase 4.
- Phase 6–7 last.

## File radius (declared for commit)

- `src/product_hunter/tui_bridge.py` (new)
- `src/product_hunter/cli.py` (edit: tui + tui-bridge commands)
- `tui/**` (new)
- `tests/test_tui_bridge.py` (new)
- `README.md` (edit)
- `tui/README.md` (new)

## Effort estimate

- Phase 1: ~1 day
- Phase 2: ~1 day
- Phase 3: ~2 days
- Phase 4: ~1.5 days
- Phase 5: ~1 day
- Phase 6–7: ~0.5 day

Total ~7 days for one developer.

## Out of scope for v1

- Light theme (v2).
- In-terminal editing of files (use external editor).
- Multi-machine/remote bridge (local only).
EOF
echo written
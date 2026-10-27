// tui/src/App.tsx
import React, { useEffect, useState } from "react";
import { useKeyboard } from "@opentui/react";
import { Bridge } from "./bridge";
import { useStore } from "./store";
import { theme, statusColor, statusBadge } from "./theme";

const SECTIONS = [
  { id: "home", label: "Home", icon: "*" },
  { id: "runs", label: "Research Runs", icon: ">" },
  { id: "probes", label: "Probes", icon: "@" },
  { id: "jobs", label: "Jobs", icon: "#" },
  { id: "db", label: "Database", icon: "D" },
  { id: "sources", label: "Sources", icon: "S" },
  { id: "index", label: "Index", icon: "I" },
  { id: "project", label: "Project", icon: "P" },
  { id: "models", label: "Models/Bench", icon: "M" },
  { id: "autonomy", label: "Autonomy", icon: "A" },
  { id: "observe", label: "Observe", icon: "O" },
  { id: "logs", label: "System Logs", icon: "L" },
  { id: "palette", label: "Command Palette", icon: ":" },
  { id: "help", label: "Help", icon: "?" },
];

let BRIDGE: Bridge | null = null;

function ProgressBar({ value, width = 20 }: { value: number; width?: number }) {
  const v = Math.max(0, Math.min(100, value || 0));
  const filled = Math.round((v / 100) * width);
  const empty = width - filled;
  return (
    <text>
      <span style={{ color: theme.accent }}>{"█".repeat(filled)}</span>
      <span style={{ color: theme.muted }}>{"░".repeat(empty)}</span>
      <span style={{ color: theme.textSecondary }}> {v}%</span>
    </text>
  );
}

function Badge({ status }: { status: string }) {
  return <text style={{ color: statusColor(status) }}>{statusBadge(status)}</text>;
}

function HomeView() {
  const db = useStore((s) => s.dbStatus);
  const runs = useStore((s) => s.runs);
  const probes = useStore((s) => s.probes);
  const autonomy = useStore((s) => s.autonomy);
  return (
    <scrollbox style={{ flexGrow: 1, padding: 1 }}>
      <box style={{ flexDirection: "column", gap: 1 }}>
        <text style={{ color: theme.accent }}>Dashboard</text>
        <text style={{ color: theme.textSecondary }}>
          schema v{db?.schema_version ?? "—"} · tables {Object.keys(db?.tables ?? {}).length} · jobs q{db?.jobs?.queued ?? 0}/r{db?.jobs?.running ?? 0}/f{db?.jobs?.failed ?? 0}/x{db?.jobs?.expired ?? 0}
        </text>
        <text style={{ color: theme.textSecondary }}>
          autonomy: {autonomy?.worker_active ? "worker active" : "worker inactive"} · running {autonomy?.running_runs ?? 0} · stalled {autonomy?.stalled_runs ?? 0}
        </text>
        <text style={{ color: theme.textSecondary }}>
          probes: {probes.length} total · running {probes.filter((p) => String(p.status).toUpperCase() === "RUNNING").length}
        </text>
        <text style={{ color: theme.accent, marginTop: 1 }}>Recent runs</text>
        {runs.slice(0, 8).map((r, i) => (
          <box key={String(r.run_id ?? r.id)} style={{ flexDirection: "row", gap: 2 }}>
            <text style={{ color: theme.text, width: 22 }}>{String(r.run_id ?? r.id).slice(0, 20)}</text>
            <Badge status={String(r.status ?? "")} />
            <ProgressBar value={Number(r.progress_percent ?? 0)} width={14} />
            <text style={{ color: theme.muted }}>{String(r.current_stage ?? "—")}</text>
          </box>
        ))}
        {runs.length === 0 && <text style={{ color: theme.muted }}>No runs yet.</text>}
        <text style={{ color: theme.accent, marginTop: 1 }}>Recent probes</text>
        {probes.slice(0, 6).map((p) => (
          <box key={String(p.public_id)} style={{ flexDirection: "row", gap: 2 }}>
            <text style={{ color: theme.text, width: 18 }}>{String(p.public_id)}</text>
            <Badge status={String(p.status ?? "")} />
            <text style={{ color: theme.muted }}>{String(p.objective ?? "").slice(0, 30)}</text>
          </box>
        ))}
        {probes.length === 0 && <text style={{ color: theme.muted }}>No probes.</text>}
      </box>
    </scrollbox>
  );
}

function RunsView() {
  const runs = useStore((s) => s.runs);
  const nav = useStore((s) => s.nav);
  const openDetail = useStore((s) => s.openDetail);
  const goBack = useStore((s) => s.goBack);
  const setDetailTab = useStore((s) => s.setDetailTab);
  const scopedLogs = useStore((s) => s.scopedLogs);
  const selectedIndex = useStore((s) => s.selectedIndex);
  const setSelectedIndex = useStore((s) => s.setSelectedIndex);
  const selected = nav.selectedId ? runs.find((r) => String(r.run_id ?? r.id) === nav.selectedId) : null;

  if (nav.selectedId && selected) {
    const runId = String(selected.run_id ?? selected.id);
    const tabs = ["Overview", "Jobs", "Checkpoints", "Events", "Artifacts", "Logs"];
    const logs = scopedLogs.get(`run:${runId}`) || [];
    return (
      <box style={{ flexDirection: "column", flexGrow: 1, padding: 1, gap: 1 }}>
        <text style={{ color: theme.accent }}>{runId}  <Badge status={String(selected.status ?? "")} /></text>
        <text style={{ color: theme.textSecondary }}>{String(selected.objective ?? "")}</text>
        <text style={{ color: theme.muted }}>tabs: {tabs.map((t, i) => `${i + 1} ${t}`).join("  ")}</text>
        <scrollbox style={{ flexGrow: 1 }}>
          {nav.detailTab === 0 && (
            <box style={{ flexDirection: "column", gap: 1 }}>
              <text>stage: {String(selected.current_stage ?? "—")}  step: {String(selected.current_step ?? "—")}</text>
              <text>progress: <ProgressBar value={Number(selected.progress_percent ?? 0)} width={32} /></text>
              <text>resumes: {String(selected.resume_count ?? 0)}  updated: {String(selected.updated_at ?? "—")}</text>
              <text>worker: {selected.worker_active ? "active" : "inactive"}</text>
            </box>
          )}
          {nav.detailTab === 5 && (
            <box style={{ flexDirection: "column" }}>
              {logs.length === 0 && <text style={{ color: theme.muted }}>No logs for this run yet.</text>}
              {logs.map((l, i) => (
                <text key={i} style={{ color: l.level === "ERR" ? theme.error : l.level === "WARN" ? theme.warning : theme.textSecondary }}>
                  {l.ts.slice(11, 19)} [{l.level}] {l.line}
                </text>
              ))}
            </box>
          )}
          {nav.detailTab !== 0 && nav.detailTab !== 5 && (
            <text style={{ color: theme.muted }}>Tab {tabs[nav.detailTab]} not yet wired.</text>
          )}
        </scrollbox>
        <text style={{ color: theme.muted }}>Esc back · 1-6 tabs</text>
      </box>
    );
  }

  return (
    <box style={{ flexDirection: "column", flexGrow: 1, padding: 1, gap: 1 }}>
      <text style={{ color: theme.accent }}>Research Runs ({runs.length})</text>
      <scrollbox style={{ flexGrow: 1 }}>
        {runs.map((r, i) => {
          const id = String(r.run_id ?? r.id);
          const active = i === selectedIndex;
          return (
            <box key={id} style={{ flexDirection: "row", gap: 2, backgroundColor: active ? theme.elevated : undefined }}>
              <text style={{ color: active ? theme.accent : theme.text, width: 24 }}>{active ? "▌ " : "  "}{id.slice(0, 20)}</text>
              <Badge status={String(r.status ?? "")} />
              <ProgressBar value={Number(r.progress_percent ?? 0)} width={12} />
              <text style={{ color: theme.muted, width: 12 }}>{String(r.current_stage ?? "—")}</text>
            </box>
          );
        })}
        {runs.length === 0 && <text style={{ color: theme.muted }}>No runs.</text>}
      </scrollbox>
      <text style={{ color: theme.muted }}>↑↓ select · Enter open · Esc back</text>
    </box>
  );
}

function ProbesView() {
  const probes = useStore((s) => s.probes);
  const nav = useStore((s) => s.nav);
  const openDetail = useStore((s) => s.openDetail);
  const goBack = useStore((s) => s.goBack);
  const setDetailTab = useStore((s) => s.setDetailTab);
  const scopedLogs = useStore((s) => s.scopedLogs);
  const selectedIndex = useStore((s) => s.selectedIndex);
  const setSelectedIndex = useStore((s) => s.setSelectedIndex);
  const selected = nav.selectedId ? probes.find((p) => String(p.public_id) === nav.selectedId) : null;

  if (nav.selectedId && selected) {
    const pid = String(selected.public_id);
    const tabs = ["Overview", "Cycles", "Active Run", "Errors", "Logs", "Report"];
    const logs = scopedLogs.get(`probe:${pid}`) || scopedLogs.get(`probe:${String(selected.probe_id)}`) || [];
    return (
      <box style={{ flexDirection: "column", flexGrow: 1, padding: 1, gap: 1 }}>
        <text style={{ color: theme.accent }}>{pid}  <Badge status={String(selected.status ?? "")} /></text>
        <text style={{ color: theme.textSecondary }}>{String(selected.objective ?? "")}</text>
        <text style={{ color: theme.muted }}>tabs: {tabs.map((t, i) => `${i + 1} ${t}`).join("  ")}</text>
        <scrollbox style={{ flexGrow: 1 }}>
          {nav.detailTab === 0 && (
            <box style={{ flexDirection: "column", gap: 1 }}>
              <text>probe_id: {String(selected.probe_id ?? "—")}</text>
              <text>cycles: {String(selected.total_cycles ?? 0)} (ok {String(selected.successful_cycles ?? 0)}, fail {String(selected.failed_cycles ?? 0)}, blocked {String(selected.blocked_cycles ?? 0)})</text>
              <text>backoff: {String(selected.backoff_multiplier ?? 1)}x · next: {String(selected.next_cycle_at ?? "—")}</text>
              <text>active run: {String(selected.current_run_id ?? "—")}</text>
              <text>worker: {selected.worker_active ? "active" : "inactive"}</text>
              {selected.last_error ? <text style={{ color: theme.error }}>error: {String(selected.last_error)}</text> : null}
            </box>
          )}
          {nav.detailTab === 4 && (
            <box style={{ flexDirection: "column" }}>
              {logs.length === 0 && <text style={{ color: theme.muted }}>No logs for this probe yet.</text>}
              {logs.map((l, i) => (
                <text key={i} style={{ color: l.level === "ERR" ? theme.error : l.level === "WARN" ? theme.warning : theme.textSecondary }}>
                  {l.ts.slice(11, 19)} [{l.level}] {l.line}
                </text>
              ))}
            </box>
          )}
          {nav.detailTab !== 0 && nav.detailTab !== 4 && (
            <text style={{ color: theme.muted }}>Tab {tabs[nav.detailTab]} not yet wired.</text>
          )}
        </scrollbox>
        <text style={{ color: theme.muted }}>Esc back · 1-6 tabs</text>
      </box>
    );
  }

  return (
    <box style={{ flexDirection: "column", flexGrow: 1, padding: 1, gap: 1 }}>
      <text style={{ color: theme.accent }}>Probes ({probes.length})</text>
      <scrollbox style={{ flexGrow: 1 }}>
        {probes.map((p, i) => {
          const active = i === selectedIndex;
          return (
            <box key={String(p.public_id)} style={{ flexDirection: "row", gap: 2, backgroundColor: active ? theme.elevated : undefined }}>
              <text style={{ color: active ? theme.accent : theme.text, width: 20 }}>{active ? "▌ " : "  "}{String(p.public_id)}</text>
              <Badge status={String(p.status ?? "")} />
              <text style={{ color: theme.muted, width: 28 }}>{String(p.objective ?? "").slice(0, 26)}</text>
              <text style={{ color: theme.textSecondary }}>cycles {String(p.total_cycles ?? 0)}</text>
            </box>
          );
        })}
        {probes.length === 0 && <text style={{ color: theme.muted }}>No probes. Press n to create.</text>}
      </scrollbox>
      <text style={{ color: theme.muted }}>↑↓ select · Enter open · Esc back</text>
    </box>
  );
}

function JobsView() {
  const db = useStore((s) => s.dbStatus);
  const jobs = useStore((s: any) => (s as any).jobs ?? []);
  return (
    <box style={{ flexDirection: "column", flexGrow: 1, padding: 1, gap: 1 }}>
      <text style={{ color: theme.accent }}>Jobs ({jobs.length})</text>
      <scrollbox style={{ flexGrow: 1 }}>
        {jobs.map((j: any, i: number) => (
          <box key={String(j.id)} style={{ flexDirection: "row", gap: 2 }}>
            <text style={{ color: theme.text, width: 26 }}>{String(j.id).slice(0, 24)}</text>
            <text style={{ color: theme.muted, width: 12 }}>{String(j.status ?? "—")}</text>
            <text style={{ color: theme.textSecondary, width: 12 }}>{String(j.stage ?? "—")}</text>
            <text style={{ color: theme.muted }}>{String(j.run_id ?? "—").slice(0, 16)}</text>
          </box>
        ))}
        {jobs.length === 0 && <text style={{ color: theme.muted }}>No jobs. schema v{db?.schema_version ?? "—"}</text>}
      </scrollbox>
    </box>
  );
}

function GenericView({ title }: { title: string }) {
  const db = useStore((s) => s.dbStatus);
  return (
    <box style={{ flexDirection: "column", flexGrow: 1, padding: 1, gap: 1 }}>
      <text style={{ color: theme.accent }}>{title}</text>
      <text style={{ color: theme.muted }}>schema v{db?.schema_version ?? "—"} · tables {Object.keys(db?.tables ?? {}).length}</text>
      <text style={{ color: theme.muted }}>Use Command Palette (:) to run any ph command.</text>
    </box>
  );
}

function CommandPaletteView() {
  const [input, setInput] = useState("");
  const [output, setOutput] = useState("");
  const bridge = BRIDGE;
  const run = async () => {
    if (!bridge) return;
    const parts = input.trim().split(/\s+/).filter(Boolean);
    if (parts.length === 0) return;
    const argv = ["ph", ...parts];
    const r = await bridge.sendCmd(argv);
    setOutput((r.ok ? r.stdout : r.stderr) || "(no output)");
  };
  return (
    <box style={{ flexDirection: "column", flexGrow: 1, padding: 1, gap: 1 }}>
      <text style={{ color: theme.accent }}>Command Palette</text>
      <box style={{ flexDirection: "row", gap: 1 }}>
        <text style={{ color: theme.muted }}>ph</text>
        {React.createElement("input" as any, {
          value: input,
          onInput: (v: string) => setInput(v),
          onSubmit: () => run(),
          placeholder: "db status",
          style: { flexGrow: 1, color: theme.text, backgroundColor: theme.panel },
        })}
      </box>
      <text style={{ color: theme.muted }}>Enter to run</text>
      <scrollbox style={{ flexGrow: 1 }}>
        <text style={{ color: theme.textSecondary }}>{output}</text>
      </scrollbox>
    </box>
  );
}

function HelpView() {
  return (
    <scrollbox style={{ flexGrow: 1, padding: 1 }}>
      <box style={{ flexDirection: "column", gap: 1 }}>
        <text style={{ color: theme.accent }}>Keybinds</text>
        <text style={{ color: theme.textSecondary }}>↑↓  navigate lists / nav</text>
        <text style={{ color: theme.textSecondary }}>Enter  open / submit</text>
        <text style={{ color: theme.textSecondary }}>Esc  back</text>
        <text style={{ color: theme.textSecondary }}>1-6  switch detail tabs</text>
        <text style={{ color: theme.textSecondary }}>:  command palette</text>
        <text style={{ color: theme.textSecondary }}>q  quit</text>
        <text style={{ color: theme.muted, marginTop: 1 }}>Nav: Home, Runs, Probes, Jobs, DB, Sources, Index, Project, Models, Autonomy, Observe, Logs, Palette, Help</text>
      </box>
    </scrollbox>
  );
}

export function App() {
  const bridge = React.useMemo(() => new Bridge(), []);
  const setSnapshot = useStore((s) => s.setSnapshot);
  const appendLog = useStore((s) => s.appendLog);
  const pushToast = useStore((s) => s.pushToast);
  const nav = useStore((s) => s.nav);
  const navigate = useStore((s) => s.navigate);
  const goBack = useStore((s) => s.goBack);
  const openDetail = useStore((s) => s.openDetail);
  const setDetailTab = useStore((s) => s.setDetailTab);
  const selectedIndex = useStore((s) => s.selectedIndex);
  const setSelectedIndex = useStore((s) => s.setSelectedIndex);
  const runs = useStore((s) => s.runs);
  const probes = useStore((s) => s.probes);

  useEffect(() => {
    BRIDGE = bridge;
    bridge.start(process.env.PH_DB_PATH);
    const off = bridge.onMessage((m: any) => {
      if (m.type === "snapshot") setSnapshot(m);
      else if (m.type === "log") appendLog(m);
      else if (m.type === "error") pushToast("error", m.message);
      else if (m.type === "result") {
        if (!m.ok) pushToast("error", (m.stderr || "command failed").slice(0, 120));
        else pushToast("success", "done");
      }
    });
    return () => {
      off();
      bridge.stop();
    };
  }, [bridge, setSnapshot, appendLog, pushToast]);

  useKeyboard((key: any) => {
    // global
    if (key.name === "q") { process.exit(0); return; }
    if (key.name === "escape") { goBack(); return; }
    // number keys switch tabs in detail views
    if (nav.selectedId && /^[1-6]$/.test(key.name)) {
      setDetailTab(Number(key.name) - 1);
      return;
    }
    // navigation within nav list when on home? We'll handle section list via up/down only when no list.
    const section = nav.section;
    const inDetail = !!nav.selectedId && (section === "runs" || section === "probes");
    // if in list (not detail), up/down moves selection
    if (!inDetail && !nav.selectedId) {
      const listLen = section === "runs" ? runs.length : section === "probes" ? probes.length : SECTIONS.length;
      if (key.name === "up") { setSelectedIndex(Math.max(0, selectedIndex - 1)); return; }
      if (key.name === "down") { setSelectedIndex(Math.min(Math.max(0, listLen - 1), selectedIndex + 1)); return; }
      if (key.name === "return" || key.name === "enter") {
        if (section === "home") return;
        if (section === "runs" && runs[selectedIndex]) { openDetail(String(runs[selectedIndex].run_id ?? runs[selectedIndex].id)); return; }
        if (section === "probes" && probes[selectedIndex]) { openDetail(String(probes[selectedIndex].public_id)); return; }
        // nav section switch: if selectedIndex within SECTIONS, switch section
        if (selectedIndex >= 0 && selectedIndex < SECTIONS.length && section === "home") {
          navigate(SECTIONS[selectedIndex].id);
          setSelectedIndex(0);
        }
        return;
      }
    }
    // when in a detail view, Esc handled above (goBack)
  });

  // nav section switching via left/right? Use Tab to cycle sections.
  useKeyboard((key: any) => {
    if (key.name === "tab") {
      const idx = SECTIONS.findIndex((s) => s.id === nav.section);
      const next = (idx + 1) % SECTIONS.length;
      navigate(SECTIONS[next].id);
      setSelectedIndex(0);
    }
  });

  const currentSection = nav.section;
  const listLen = currentSection === "runs" ? runs.length : currentSection === "probes" ? probes.length : SECTIONS.length;

  // keep selectedIndex in range
  useEffect(() => {
    if (selectedIndex >= listLen && listLen > 0) setSelectedIndex(listLen - 1);
  }, [listLen, selectedIndex, setSelectedIndex]);

  return (
    <box style={{ flexDirection: "row", width: "100%", height: "100%", backgroundColor: theme.bg }}>
      {/* nav tree */}
      <box style={{ width: 26, flexDirection: "column", borderStyle: "single", borderColor: theme.border, paddingX: 1, paddingY: 0 }}>
        <box style={{ paddingY: 1 }}>
          <text style={{ color: theme.accent }}>ph tui</text>
        </box>
        <scrollbox style={{ flexGrow: 1 }}>
          {SECTIONS.map((s) => {
            const active = currentSection === s.id;
            return (
              <box key={s.id} style={{ flexDirection: "row", backgroundColor: active ? theme.elevated : undefined, paddingX: 1, paddingY: 0 }}>
                <text style={{ color: active ? theme.accent : theme.textSecondary }}>
                  {active ? "▌ " : "  "}{s.icon} {s.label}
                </text>
              </box>
            );
          })}
        </scrollbox>
        <box style={{ paddingY: 1 }}>
          <text style={{ color: theme.muted }}>Tab next · q quit · ? help</text>
        </box>
      </box>

      {/* content */}
      <box style={{ flexGrow: 1, flexDirection: "column" }}>
        <box style={{ paddingX: 1, paddingY: 0 }}>
          <text style={{ color: theme.muted }}>
            {nav.backStack.length > 0 ? "Esc back · " : ""}{currentSection}
            {nav.selectedId ? ` › ${nav.selectedId}` : ""}
          </text>
        </box>
        <box style={{ flexGrow: 1 }}>
          {currentSection === "home" && <HomeView />}
          {currentSection === "runs" && <RunsView />}
          {currentSection === "probes" && <ProbesView />}
          {currentSection === "jobs" && <JobsView />}
          {currentSection === "palette" && <CommandPaletteView />}
          {currentSection === "help" && <HelpView />}
          {!["home", "runs", "probes", "jobs", "palette", "help"].includes(currentSection) && <GenericView title={SECTIONS.find((s) => s.id === currentSection)?.label ?? currentSection} />}
        </box>
        <box style={{ position: "absolute", bottom: 1, right: 1, flexDirection: "column" }}>
          {useStore.getState().toasts.slice(-3).map((t) => (
            <text key={t.id} style={{ color: t.level === "error" ? theme.error : t.level === "success" ? theme.success : theme.info }}>
              {t.message}
            </text>
          ))}
        </box>
      </box>
    </box>
  );
}

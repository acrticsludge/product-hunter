// tui/src/store.ts — in-memory store with hash-based updates.
import { create } from "zustand";
import type { BridgeSnapshot, BridgeLog, BridgeResult } from "./bridge";

export type Run = Record<string, unknown>;
export type Probe = Record<string, unknown>;

type NavState = {
  section: string;
  selectedId: string | null;
  detailTab: number;
  backStack: Array<{ section: string; selectedId: string | null; detailTab: number }>;
};

export type Toast = { id: number; level: "info" | "success" | "error"; message: string };

type StoreState = {
  hash: string | null;
  dbStatus: BridgeSnapshot["data"]["db_status"] | null;
  runs: Run[];
  probes: Probe[];
  jobs: Array<Record<string, unknown>>;
  autonomy: BridgeSnapshot["data"]["autonomy"] | null;
  sources: Array<Record<string, unknown>>;
  logs: BridgeLog[];
  scopedLogs: Map<string, BridgeLog[]>; // key: "run:<id>" | "probe:<id>"
  nav: NavState;
  selectedIndex: number;
  setSelectedIndex: (i: number) => void;
  toasts: Toast[];
  lastResult: BridgeResult | null;
  setSnapshot: (snap: BridgeSnapshot) => void;
  appendLog: (log: BridgeLog) => void;
  navigate: (section: string, selectedId?: string | null) => void;
  openDetail: (id: string | null, tab?: number) => void;
  setDetailTab: (tab: number) => void;
  goBack: () => void;
  pushToast: (level: Toast["level"], message: string) => void;
  removeToast: (id: number) => void;
  setLastResult: (r: BridgeResult | null) => void;
};

let toastCounter = 0;

export const useStore = create<StoreState>((set, get) => ({
  hash: null,
  dbStatus: null,
  runs: [],
  probes: [],
  jobs: [],
  autonomy: null,
  sources: [],
  logs: [],
  scopedLogs: new Map(),
  nav: { section: "home", selectedId: null, detailTab: 0, backStack: [] },
  selectedIndex: 0,
  setSelectedIndex: (i) => set({ selectedIndex: i }),
  toasts: [],
  lastResult: null,

  setSnapshot: (snap) => {
    set({
      hash: snap.hash,
      dbStatus: snap.data.db_status,
      runs: snap.data.runs,
      probes: snap.data.probes,
      jobs: (snap.data as any).jobs ?? [],
      autonomy: snap.data.autonomy,
      sources: snap.data.sources,
    });
  },

  appendLog: (log) => {
    set((state) => {
      const logs = [...state.logs, log].slice(-2000);
      const scoped = new Map(state.scopedLogs);
      const id = log.entity?.id;
      if (id) {
        const key = `${log.entity.kind}:${id}`;
        const arr = [...(scoped.get(key) || []), log].slice(-500);
        scoped.set(key, arr);
      }
      return { logs, scopedLogs: scoped };
    });
  },

  navigate: (section, selectedId = null) => {
    set((state) => ({
      nav: {
        section,
        selectedId,
        detailTab: 0,
        backStack: [...state.nav.backStack, state.nav],
      },
    }));
  },

  openDetail: (id, tab = 0) => {
    set((state) => ({
      nav: {
        ...state.nav,
        selectedId: id,
        detailTab: tab,
        backStack: [...state.nav.backStack, state.nav],
      },
    }));
  },

  setDetailTab: (tab) => set((state) => ({ nav: { ...state.nav, detailTab: tab } })),

  goBack: () => {
    const stack = get().nav.backStack;
    if (stack.length === 0) return;
    const prev = stack[stack.length - 1];
    set({ nav: { ...prev, backStack: stack.slice(0, -1) } });
  },

  pushToast: (level, message) => {
    const id = ++toastCounter;
    set((state) => ({ toasts: [...state.toasts, { id, level, message }] }));
    setTimeout(() => get().removeToast(id), 3000);
  },

  removeToast: (id) => set((state) => ({ toasts: state.toasts.filter((t) => t.id !== id) })),

  setLastResult: (r) => set({ lastResult: r }),
}));

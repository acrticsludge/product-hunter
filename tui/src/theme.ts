// tui/src/theme.ts — OpenCode-inspired dark palette (single source of truth).
export const theme = {
  bg: "#0b0d10",
  panel: "#13161b",
  elevated: "#1a1e24",
  hover: "#22272e",
  text: "#e6e8eb",
  textSecondary: "#9aa0a6",
  muted: "#5c6370",
  accent: "#f5a524",
  success: "#3fb950",
  warning: "#d29922",
  error: "#f85149",
  info: "#58a6ff",
  running: "#a371f7",
  border: "#2a2f36",
  borderFocused: "#f5a524",
  borderStrong: "#3a4149",
} as const;

export type Theme = typeof theme;

export const statusColor = (status: string): string => {
  const s = (status || "").toUpperCase();
  if (s === "RUNNING" || s === "LEASED") return theme.running;
  if (s === "SUCCESS" || s === "COMPLETED" || s === "DONE") return theme.success;
  if (s === "PAUSED") return theme.warning;
  if (s === "FAILED" || s === "DEAD" || s === "CANCELLED" || s === "ERROR") return theme.error;
  if (s === "QUEUED" || s === "CREATED") return theme.info;
  return theme.textSecondary;
};

export const statusBadge = (status: string): string => {
  const s = (status || "").toUpperCase();
  if (s === "RUNNING" || s === "LEASED") return "● RUNNING";
  if (s === "SUCCESS" || s === "COMPLETED" || s === "DONE") return "✓ SUCCESS";
  if (s === "PAUSED") return "⏸ PAUSED";
  if (s === "FAILED" || s === "DEAD" || s === "CANCELLED" || s === "ERROR") return "✗ FAILED";
  if (s === "QUEUED" || s === "CREATED") return "… QUEUED";
  return s || "—";
};

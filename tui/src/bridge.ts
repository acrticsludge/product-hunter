// tui/src/bridge.ts — NDJSON bridge client.
import { spawn, type ChildProcess } from "node:child_process";
import { createInterface } from "node:readline";

export type BridgeSnapshot = {
  hash: string;
  data: {
    db_status: {
      schema_version: number;
      tables: Record<string, number>;
      jobs: { queued: number; running: number; failed: number; expired: number };
      outbox_pending: number;
      projection_tasks: number;
    };
    runs: Array<Record<string, unknown>>;
    probes: Array<Record<string, unknown>>;
    autonomy: { worker_active: boolean; running_runs: number; stalled_runs: number };
    sources: Array<Record<string, unknown>>;
  };
};

export type BridgeLog = {
  type: "log";
  level: string;
  ts: string;
  entity: { kind: string; id: string | null };
  line: string;
};

export type BridgeResult = {
  type: "result";
  req_id: string;
  ok: boolean;
  stdout: string;
  stderr: string;
  exit_code: number;
};

export type BridgeMessage =
  | { type: "snapshot"; hash: string; data: BridgeSnapshot["data"] }
  | BridgeLog
  | BridgeResult
  | { type: "error"; message: string };

export class Bridge {
  private proc: ChildProcess | null = null;
  private listeners: Array<(m: BridgeMessage) => void> = [];
  private pending = new Map<string, (r: BridgeResult) => void>();
  private reqCounter = 0;

  start(dbPath?: string): void {
    const env = { ...process.env };
    const args = ["ph", "tui-bridge"];
    if (dbPath) args.push("--db", dbPath);
    this.proc = spawn("uv", ["run", ...args], {
      stdio: ["pipe", "pipe", "pipe"],
      env,
      cwd: process.env.PH_PROJECT_ROOT || new URL("../..", import.meta.url).pathname,
    });
    const rl = createInterface({ input: this.proc.stdout! });
    rl.on("line", (line) => {
      try { const fs = require('node:fs'); fs.appendFileSync('/tmp/opencode/bridge-lines.txt', `listeners=${this.listeners.length} :: ${line.slice(0,60)}\\n`); } catch {}
      if (!line.trim()) return;
      try {
        const msg = JSON.parse(line) as BridgeMessage;
        if (msg.type === "result") {
          const cb = this.pending.get(msg.req_id);
          if (cb) {
            cb(msg as BridgeResult);
            this.pending.delete(msg.req_id);
          }
        }
        for (const l of this.listeners) l(msg);
      } catch {
        // ignore malformed lines
      }
    });
    this.proc.on("exit", () => {
      for (const l of this.listeners) l({ type: "error", message: "bridge exited" });
    });
  }

  onMessage(cb: (m: BridgeMessage) => void): () => void {
    this.listeners.push(cb);
    return () => {
      this.listeners = this.listeners.filter((x) => x !== cb);
    };
  }

  sendCmd(argv: string[]): Promise<BridgeResult> {
    const req_id = `req-${++this.reqCounter}`;
    return new Promise((resolve) => {
      this.pending.set(req_id, resolve);
      const line = JSON.stringify({ type: "cmd", req_id, argv }) + "\n";
      this.proc?.stdin?.write(line);
      // timeout safety
      setTimeout(() => {
        if (this.pending.has(req_id)) {
          this.pending.delete(req_id);
          resolve({ type: "result", req_id, ok: false, stdout: "", stderr: "timeout", exit_code: -1 });
        }
      }, 310000);
    });
  }

  stop(): void {
    try {
      this.proc?.stdin?.end();
      this.proc?.kill();
    } catch {
      // ignore
    }
  }
}

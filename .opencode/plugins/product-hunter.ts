// The OpenCode control surface for Product Hunter.
//
// One tool, one action per call. The Python state layer owns what an action
// means and the wording of every reply; this file only carries the request
// across and hands the text back. The worker still owns the execution, so the
// tool never runs a research stage.
//
// The subprocess call passes an argument vector and never a command string,
// and the plugin uses the Node standard library only.
import { spawn } from "node:child_process"
import { statSync } from "node:fs"
import { dirname, join, resolve } from "node:path"
import { fileURLToPath } from "node:url"

const TIMEOUT_MS = 30_000

// The registry answers with one JSON object. A child that writes more than
// this is not the registry: the output is dropped, the child is killed, and
// the tool reports the overflow instead of buffering without bound.
const MAX_STDOUT_BYTES = 1024 * 1024
const MAX_STDERR_BYTES = 64 * 1024

// The ten commands map onto nine actions: /continue-research and /continue
// are the same action.
const ACTIONS = [
  "create",
  "list",
  "status",
  "continue",
  "resume",
  "pause",
  "retry",
  "cancel",
  "log",
]

type RegistryResult = {
  ok: boolean
  action: string
  message: string
  text: string
  run?: unknown
  runs?: unknown[]
  needs_selection?: boolean
  code?: string | null
}

// The project root is the nearest directory at or above the start that holds a
// pyproject.toml, the same rule config.repo_root() applies on the Python side.
export function findProjectRoot(start: string | undefined): string | undefined {
  if (!start) return undefined
  let dir = resolve(start)
  while (true) {
    try {
      if (statSync(join(dir, "pyproject.toml")).isFile()) return dir
    } catch {
      // no manifest here, keep walking up
    }
    const parent = dirname(dir)
    if (parent === dir) return undefined
    dir = parent
  }
}

// The plugin file's own directory is the first candidate: it is the tree the
// plugin ships in, whatever the tool context happens to carry. fileURLToPath
// throws for a URL that is not a file: URL, and a module level throw takes
// the whole tool down, so the directory is optional.
const PLUGIN_DIR = (() => {
  try {
    return dirname(fileURLToPath(import.meta.url))
  } catch {
    return undefined
  }
})()

function buildArgs(input: any, sessionID: string | undefined): string[] {
  const action = String(input?.action ?? "")
  const args: string[] = [action]
  // Every value rides in the equals form. A value that starts with a dash is
  // then a value and not an option, so the child parses what was sent.
  if (action === "create") {
    if (input?.objective) args.push(`--objective=${String(input.objective)}`)
  } else if (input?.research_id) {
    args.push(`--research-id=${String(input.research_id)}`)
  }
  // The session id is the requester: it is sent for every action, so every
  // event the registry writes names who asked for it.
  if (sessionID) args.push(`--chat-id=${sessionID}`)
  if (input?.limit !== undefined && input?.limit !== null) {
    args.push(`--limit=${String(input.limit)}`)
  }
  return args
}

function runRegistry(
  directory: string,
  args: string[],
): Promise<{ ok: boolean; payload?: RegistryResult; error?: string }> {
  return new Promise((resolve) => {
    const child = spawn(
      "uv",
      ["run", "--project", directory, "python", "-m", "product_hunter.registry", ...args],
      { cwd: directory, shell: false, windowsHide: true },
    )
    let stdout = ""
    let stderr = ""
    let stdoutBytes = 0
    let stderrBytes = 0
    let settled = false
    const settle = (outcome: { ok: boolean; payload?: RegistryResult; error?: string }) => {
      if (settled) return
      settled = true
      clearTimeout(timer)
      resolve(outcome)
    }
    const timer = setTimeout(() => {
      child.kill()
      settle({
        ok: false,
        error: `the registry did not answer within ${TIMEOUT_MS / 1000} seconds in ${directory}`,
      })
    }, TIMEOUT_MS)
    child.stdout.on("data", (chunk: Buffer) => {
      if (settled) return
      stdoutBytes += chunk.length
      if (stdoutBytes > MAX_STDOUT_BYTES) {
        child.kill()
        settle({ ok: false, error: `the registry output exceeded 1 MiB in ${directory}` })
        return
      }
      stdout += chunk.toString()
    })
    child.stderr.on("data", (chunk: Buffer) => {
      if (settled) return
      stderrBytes += chunk.length
      if (stderrBytes > MAX_STDERR_BYTES) {
        child.kill()
        settle({ ok: false, error: `the registry error output exceeded 64 KiB in ${directory}` })
        return
      }
      stderr += chunk.toString()
    })
    child.on("error", (err: Error) => {
      settle({
        ok: false,
        error: `the registry could not be started in ${directory}: ${err.message}`,
      })
    })
    child.on("close", (code) => {
      // A refused command still prints its JSON, so the message reaches the
      // model as text instead of as a raw blob. Only output that is not JSON
      // becomes an error, and the error names the directory.
      let payload: RegistryResult | undefined
      try {
        payload = JSON.parse(stdout)
      } catch {
        payload = undefined
      }
      if (payload !== undefined) {
        settle({ ok: true, payload })
        return
      }
      if (code !== 0) {
        settle({
          ok: false,
          error: `the registry exited with ${code} in ${directory}: ${stderr.trim() || stdout.trim()}`,
        })
        return
      }
      settle({ ok: false, error: `the registry did not answer with JSON in ${directory}` })
    })
  })
}

export default {
  id: "product-hunter",
  async setup(ctx: any) {
    await ctx.tool.transform((editor: any) => {
      editor.add({
        name: "product_hunter",
        description:
          "Control the research runs of Product Hunter. One action per call: create a run, list the runs, show one run, continue, resume, pause, retry, cancel, or read the log of a run. The worker owns the execution, so this tool records requests and reads durable state; it never runs a research stage. A run with no live worker says so.",
        input: {
          type: "object",
          properties: {
            action: {
              type: "string",
              enum: ACTIONS,
              description: "What to do with the research runs.",
            },
            research_id: {
              type: "string",
              description:
                "The run to act on, as RH-YYYYMMDD-NNNN. Not used by list or create.",
            },
            objective: {
              type: "string",
              description:
                "What the run should find, in the user's words. Required by create.",
            },
            limit: {
              type: "integer",
              minimum: 1,
              maximum: 500,
              description: "How many entries to return. Used by list and log.",
            },
          },
          required: ["action"],
          additionalProperties: false,
        },
        output: { type: "string" },
        options: { codemode: false },
        execute: async (input: any, toolCtx: any) => {
          const action = String(input?.action ?? "")
          if (!ACTIONS.includes(action)) {
            return {
              output: `Unknown action "${action}". Expected one of: ${ACTIONS.join(", ")}.`,
              metadata: { ok: false, code: "invalid_request" },
            }
          }
          // uv accepts any directory, so a root is handed over only when its
          // pyproject.toml confirms it. The working directory is the last resort.
          const directory =
            findProjectRoot(PLUGIN_DIR) ??
            findProjectRoot(toolCtx?.directory ? String(toolCtx.directory) : undefined) ??
            findProjectRoot(process.cwd()) ??
            process.cwd()
          const sessionID = toolCtx?.sessionID ? String(toolCtx.sessionID) : undefined
          const result = await runRegistry(directory, buildArgs(input, sessionID))
          if (!result.ok || !result.payload) {
            return {
              output: result.error ?? "the registry could not be reached",
              metadata: { ok: false, code: "internal_error" },
            }
          }
          const payload = result.payload
          return {
            output: payload.text || payload.message,
            metadata: {
              ok: payload.ok,
              action: payload.action,
              message: payload.message,
              code: payload.code ?? null,
              needs_selection: Boolean(payload.needs_selection),
              run: payload.run ?? null,
              runs: payload.runs ?? [],
            },
          }
        },
      })
    })
  },
}

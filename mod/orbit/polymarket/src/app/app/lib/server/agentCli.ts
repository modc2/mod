// One way to ask the local `claude` CLI a question, shared by every agent
// route in this app (strat-chat, score-agent). The prompt goes in on STDIN,
// not argv: these prompts embed JSON and user prose, and an argv-sized prompt
// would be both truncated and quoting-sensitive. No API key of its own — it
// inherits whatever credentials the box's CLI is signed in with, and when the
// CLI is missing it says so plainly instead of answering from nothing.

import { spawn } from "child_process";
import { existsSync, readFileSync } from "fs";
import { join } from "path";

import { stateDir } from "./ownerToken";

/** Opus by default — these are judgment calls asked one at a time, not a
    fleet. Overridable per deployment. */
export const AGENT_MODEL = process.env.POLYMARKET_CHAT_MODEL || "claude-opus-5";

/** The REAL system CLI, by absolute path. The Next server runs under `npx`,
    which prepends every ancestor node_modules/.bin to PATH — and /root/mod
    carries a stale @anthropic-ai/claude-code whose shim predates the newer
    flags. A bare "claude" resolves to that. */
export const CLAUDE_BIN = process.env.POLYMARKET_CLAUDE_BIN
  || (existsSync("/usr/local/bin/claude") ? "/usr/local/bin/claude" : "claude");

/** How the spawned CLI authenticates. The box's interactive `claude` login
    (~/.claude/.credentials.json) can be expired for months on a headless
    host; agent routes run on a long-lived OAuth token instead — from the env,
    or from the module's own secret dir (~/.mod/polymarket/), where the fleet
    keeps everything key-shaped. Absent both, the run fails with the CLI's own
    auth error, visibly. */
export function oauthToken(): string | null {
  if (process.env.CLAUDE_CODE_OAUTH_TOKEN) return process.env.CLAUDE_CODE_OAUTH_TOKEN;
  try {
    const t = readFileSync(join(stateDir(), "claude_oauth_token"), "utf8").trim();
    return t || null;
  } catch {
    return null;
  }
}

/** The env every spawned CLI gets: the process env plus that token. */
export function claudeEnv(): NodeJS.ProcessEnv {
  const token = oauthToken();
  return { ...process.env, ...(token ? { CLAUDE_CODE_OAUTH_TOKEN: token } : {}) };
}

/** Past this the user is better served by an error than a spinner. */
const TIMEOUT_MS = 120_000;

export type AgentRun = { ok: true; text: string } | { ok: false; error: string };

export interface RunOpts {
  /** Tool-using runs (MCP) legitimately take longer than a plain answer. */
  timeoutMs?: number;
  /** Extra CLI flags — how a route straps an MCP sandbox on
      (`--restricted --tools "" --mcp-config … --allowedTools …`). */
  extraArgs?: string[];
}

export function runClaude(prompt: string, model: string = AGENT_MODEL, opts: RunOpts = {}): Promise<AgentRun> {
  const timeoutMs = opts.timeoutMs || TIMEOUT_MS;
  return new Promise((resolve) => {
    let child;
    try {
      child = spawn(CLAUDE_BIN, [
        "-p",
        "--output-format", "json",
        "--model", model,
        ...(opts.extraArgs || []),
      ], { stdio: ["pipe", "pipe", "pipe"], env: claudeEnv() });
    } catch (e) {
      resolve({ ok: false, error: `could not start the claude CLI: ${e instanceof Error ? e.message : String(e)}` });
      return;
    }

    let out = "";
    let err = "";
    let settled = false;
    const finish = (r: AgentRun) => {
      if (settled) return;
      settled = true;
      resolve(r);
    };

    const timer = setTimeout(() => {
      child.kill("SIGKILL");
      finish({ ok: false, error: `the agent did not answer within ${timeoutMs / 1000}s` });
    }, timeoutMs);

    child.stdout.on("data", (d) => { out += String(d); });
    child.stderr.on("data", (d) => { err += String(d); });
    child.on("error", (e) => {
      clearTimeout(timer);
      finish({ ok: false, error: `claude CLI unavailable: ${e.message}` });
    });
    child.on("close", (code) => {
      clearTimeout(timer);
      if (code !== 0) {
        finish({ ok: false, error: err.trim().slice(0, 400) || `claude CLI exited ${code}` });
        return;
      }
      // `--output-format json` wraps the answer in a run record; `result` is
      // the model's text. A plain-text stdout is accepted too, so a CLI whose
      // envelope changes degrades to "still works" rather than "broken".
      try {
        const env = JSON.parse(out) as { result?: unknown; is_error?: boolean };
        if (typeof env.result === "string") {
          finish({ ok: true, text: env.result });
          return;
        }
      } catch {
        // not the envelope — fall through
      }
      finish(out.trim() ? { ok: true, text: out } : { ok: false, error: "the agent returned nothing" });
    });

    // A CLI that exits before reading STDIN makes this write emit EPIPE, and
    // an unhandled stream 'error' would take the whole Next server down with
    // it. Swallow it here; `error`/`close` above report the failure.
    child.stdin.on("error", () => {});
    child.stdin.end(prompt);
  });
}

/** The model's JSON object, dug out of whatever it wrapped it in — fenced
    blocks and stray prose are both survivable. Returns null when no candidate
    parses to an object passing `accept`. */
export function digJson<T>(raw: string, accept: (o: Record<string, unknown>) => T | null): T | null {
  const text = raw.trim();
  const candidates: string[] = [];
  const fenced = text.match(/```(?:json)?\s*([\s\S]*?)```/);
  if (fenced) candidates.push(fenced[1]);
  candidates.push(text);
  const first = text.indexOf("{");
  const last = text.lastIndexOf("}");
  if (first >= 0 && last > first) candidates.push(text.slice(first, last + 1));

  for (const c of candidates) {
    try {
      const o = JSON.parse(c.trim()) as Record<string, unknown>;
      const v = accept(o);
      if (v !== null) return v;
    } catch {
      // try the next shape
    }
  }
  return null;
}

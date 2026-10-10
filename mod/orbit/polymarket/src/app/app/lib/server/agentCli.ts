// One way to ask the local `claude` CLI a question, shared by every agent
// route in this app (strat-chat, score-agent). The prompt goes in on STDIN,
// not argv: these prompts embed JSON and user prose, and an argv-sized prompt
// would be both truncated and quoting-sensitive. No API key of its own — it
// inherits whatever credentials the box's CLI is signed in with, and when the
// CLI is missing it says so plainly instead of answering from nothing.

import { spawn } from "child_process";
import { existsSync, readFileSync } from "fs";
import { homedir } from "os";
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
    auth error, visibly.

    A pinned token gets revoked (it has been, four times), and then EVERY agent
    route dies at once while the box login one directory over is perfectly
    alive. So a pinned token the API refuses is remembered as dead — keyed by
    the value, so writing a fresh token into the file revives it with no
    restart — and runs fall back to the box login instead. */
const TOKEN_FILE = () => join(stateDir(), "claude_oauth_token");
const deadTokens = new Set<string>();

/** The build module's host credential keeper (~/.mod/build/private/
    claude_host.json): a 5-min loop that publishes a known-good token when
    root's login (or the owner's session) is alive. Third in line after the
    env and the module's own pinned token — it's what keeps every CLI-spawning
    module from dying together on "OAuth session expired". */
function keeperToken(): string | null {
  try {
    const o = JSON.parse(
      readFileSync(join(homedir(), ".mod", "build", "private", "claude_host.json"), "utf8"),
    ) as { ready?: boolean; token?: string; expires_at?: number };
    if (!o?.ready || typeof o.token !== "string" || !o.token) return null;
    if (o.expires_at && o.expires_at <= Date.now() / 1000 + 60) return null;
    return o.token;
  } catch {
    return null;
  }
}

export function oauthToken(): string | null {
  const candidates = [
    process.env.CLAUDE_CODE_OAUTH_TOKEN || "",
    (() => {
      try {
        return readFileSync(TOKEN_FILE(), "utf8").trim();
      } catch {
        return "";
      }
    })(),
    keeperToken() || "",
  ];
  for (const t of candidates) if (t && !deadTokens.has(t)) return t;
  return null;
}

/** The env every spawned CLI gets: the process env plus the pinned token —
    or, with `token` null, the env with any token stripped so the CLI uses
    the box's own login. */
export function claudeEnv(token: string | null = oauthToken()): NodeJS.ProcessEnv {
  const env = { ...process.env };
  // A server restarted from inside an agent session inherits that session's
  // CLAUDE_CODE_* vars (child-session markers, a dead parent's socket, an
  // sk-ant-oat… in ANTHROPIC_API_KEY the CLI rejects as an API key). Scrub
  // them all so the child authenticates on its own feet.
  for (const k of Object.keys(env)) if (k.startsWith("CLAUDE_CODE_")) delete env[k];
  if (env.ANTHROPIC_API_KEY?.startsWith("sk-ant-oat")) delete env.ANTHROPIC_API_KEY;
  return token ? { ...env, CLAUDE_CODE_OAUTH_TOKEN: token } : env;
}

/** The CLI's words for "these credentials are no good" — expired, revoked,
    or never valid. Anything else (timeouts, model errors) is not retried. */
const AUTH_FAIL = /failed to authenticate|oauth|revoked|invalid (api key|bearer|token)|\b401\b|please run \/login/i;

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

export async function runClaude(prompt: string, model: string = AGENT_MODEL, opts: RunOpts = {}): Promise<AgentRun> {
  const token = oauthToken();
  const run = await runOnce(prompt, model, opts, claudeEnv(token));
  if (run.ok !== false || !token || !AUTH_FAIL.test(run.error)) return run;
  // The pinned token is the problem, not the question: retire it and ask
  // again on the box login. If that fails too, its error is the honest one.
  deadTokens.add(token);
  console.warn(`[agentCli] pinned claude token refused (${run.error.slice(0, 120)}) — falling back to the box login; write a fresh token to ${TOKEN_FILE()} to re-pin`);
  return runOnce(prompt, model, opts, claudeEnv(null));
}

const goodTokens = new Set<string>();

/** claudeEnv() for a run that can't retry (a detached, streaming spawn like
    the lab's): the pinned token is tried once with a one-word Haiku ask the
    first time each token value is seen, and dropped for the box login if
    the API refuses it. */
export async function checkedClaudeEnv(): Promise<NodeJS.ProcessEnv> {
  const token = oauthToken();
  if (!token || goodTokens.has(token)) return claudeEnv(token);
  const probe = await runOnce("Reply with OK.", "haiku", { timeoutMs: 30_000 }, claudeEnv(token));
  if (probe.ok === false) {
    if (AUTH_FAIL.test(probe.error)) deadTokens.add(token);
  } else goodTokens.add(token);
  return claudeEnv(oauthToken());
}

function runOnce(prompt: string, model: string, opts: RunOpts, env: NodeJS.ProcessEnv): Promise<AgentRun> {
  const timeoutMs = opts.timeoutMs || TIMEOUT_MS;
  return new Promise((resolve) => {
    let child;
    try {
      child = spawn(CLAUDE_BIN, [
        "-p",
        "--output-format", "json",
        "--model", model,
        ...(opts.extraArgs || []),
      ], { stdio: ["pipe", "pipe", "pipe"], env });
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
        // A failed run's REASON usually rides the stdout envelope (`result`:
        // "Failed to authenticate: …"), not stderr — surface it, or a revoked
        // OAuth token reads as a bare "exited 1" in every console.
        let reason = "";
        try {
          const env = JSON.parse(out) as { result?: unknown };
          if (typeof env.result === "string") reason = env.result;
        } catch {
          // not the envelope
        }
        finish({ ok: false, error: (reason || err.trim()).slice(0, 400) || `claude CLI exited ${code}` });
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

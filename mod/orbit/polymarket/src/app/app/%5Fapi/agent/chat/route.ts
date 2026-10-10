// /polymarket/_api/agent/chat — THE DESK CHAT: the left-column agent with
// hands, streamed.
//
// Where /_api/help-agent is a guide with no tools, this route runs the
// claude CLI with the module's OWN MCP server (src/mcp.py, stdio) as its
// only toolbox: it can answer anything (general knowledge included), read
// the board/desk/strats, and operate them. The sandbox is the tool list —
// `--restricted --tools ""` plus `--strict-mcp-config` means pm_* tools and
// literally nothing else: no shell, no files, no web.
//
// MONEY AND STRAT CHANGES STOP FOR THE OWNER. The enforcement is NOT here
// and NOT in the prompt — it is the approval gate inside mcp.py's dispatcher
// (armed by POLYMARKET_AGENT_RUN below), which parks every gated call as a
// file this route surfaces as an `approval` event on the same SSE stream.
// The console renders the card; /_api/agent/approvals records the answer;
// a dead stream declines its leftovers on the way out. No prompt can route
// around a gate that sits under the agent.
//
// Owner-gated like every agent route (it spends inference AND can reach
// owner-gated tools). One POST = one turn; `session` resumes the CLI's own
// conversation so multi-turn keeps tool results in context.

import { spawn } from "child_process";
import { NextResponse } from "next/server";

import { AGENT_MODEL, CLAUDE_BIN, checkedClaudeEnv } from "../../../lib/server/agentCli";
import { declineRunLeftovers, listApprovals } from "../../../lib/server/agentApprovals";
import { CONSOLE_MAP } from "../../../lib/server/consoleMap";
import { mcpServerPath } from "../../../lib/server/lab";
import { bearer, verifyOwnerToken } from "../../../lib/server/ownerToken";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

/** A tool-using, approval-pausing run legitimately takes a while; past this
    the user is better served by an error than a zombie. The approval TTL
    (180s default) is well inside it, so a parked call always resolves. */
const RUN_TIMEOUT_MS = 8 * 60_000;
const APPROVAL_POLL_MS = 700;
const PING_MS = 15_000;
const MAX_TURNS = 24;

const TOOL_PREFIX = "mcp__polymarket__";

function chatPrompt(page: string): string {
  return [
    `You are the DESK AGENT of a self-hosted Polymarket copy-trading console, talking to its OWNER in the console's left column. You are a chatbot first: answer ANY question they ask — about this console, about markets, about anything — from your own knowledge when no tool is needed. You also have hands: the pm_* tools are the console's own API.`,
    ``,
    CONSOLE_MAP,
    ``,
    page ? `THE OWNER IS CURRENTLY ON: ${page}` : ``,
    `HOW TO OPERATE`,
    `- Numbers and addresses about THIS deployment come from tools, never memory: pm_copy_book is the desk, pm_strats lists their strats (ids come from there), pm_live_sessions/pm_live_gates explain live money, pm_top_traders/pm_trader research leaders.`,
    `- YOU CAN DRIVE THE CONSOLE: pm_console_open turns the owner's browser to any page (it is free, no approval). When they ask where something is or how to do something, OPEN the right screen and then say what to look at on it. After you create or change a strat, open MY STRATS; after sizing the copy book, open the COPY tab; when recommending a trader, open their profile. One navigation per answer — don't flip pages while they read.`,
    `- GUIDED STRAT WORK is your core job. To CREATE: research with pm_top_traders (prefer sort=best or steady, min_history_days on long windows), check each pick with pm_trader (sub-hour candle share disqualifies), prove it with pm_copy_basket, then pm_strat_create and open MY STRATS. To MANAGE: pm_strats + the saved backtests tell you what is working — judge by the HOLDOUT/OOS numbers, not the headline; propose pm_strat_update patches or retirement (pm_strat_delete) with the evidence in one line.`,
    `- Strats: pm_strat_create / pm_strat_update / pm_strat_delete change the owner's saved strats; creations land PAUSED. The copy desk (dollars against one trader) is pm_copy_allocate / pm_copy_remove / pm_copy_start / pm_copy_stop.`,
    `- APPROVALS: money-moving and strat-changing calls pause and show the owner an APPROVE/DECLINE card in this chat — that is by design, so just make the call and say you've asked. A result starting "NOT EXECUTED" means they declined or let it lapse: read their note, adapt, and never retry the same call unprompted. Tool results for strat changes can carry rejected fields — report them honestly.`,
    `- Money honesty: DRY RUN and backtests spend nothing; autoExecute/REAL spends real money — never suggest it casually, and never claim something ran when the result says otherwise.`,
    `- Before a money action, state the plan in one line (who, how much, mode), then act. Prefer backtest → allocate → dry-run → (owner flips REAL in the browser).`,
    ``,
    `STYLE: plain prose, two to five sentences unless they ask for detail; name exact tabs/buttons when pointing at the console. No markdown tables.`,
  ].filter(Boolean).join("\n");
}

interface ChatBody {
  message: string;
  /** CLI session id from a previous turn's `start`/`done` event. */
  session?: string;
  page?: string;
}

function deny() {
  return NextResponse.json({ error: "unauthorized", gate: "polymarket-access" }, { status: 401 });
}

/** The CLI reports a dead credential as the run's ANSWER (cost $0) — surface
    it as an error, never as an assistant reply. */
const AUTH_FAIL_ANSWER = /^Failed to authenticate/;

type Ev = Record<string, unknown> & { type: string };

/** Translate one claude stream-json message into console events — the same
    contract as hyperliquid's agent.py `_events`. */
function* cliEvents(msg: Record<string, unknown>): Generator<Ev> {
  const t = msg.type;
  if (t === "system" && msg.subtype === "init") {
    yield { type: "start", session_id: msg.session_id, model: msg.model };
  } else if (t === "assistant") {
    const content = (msg.message as { content?: unknown[] })?.content;
    for (const c of Array.isArray(content) ? content : []) {
      const part = c as { type?: string; text?: string; name?: string; input?: unknown };
      if (part.type === "text" && part.text?.trim()) {
        yield { type: "text", text: part.text };
      } else if (part.type === "tool_use") {
        yield { type: "tool", name: String(part.name || "").replace(TOOL_PREFIX, ""), args: part.input || {} };
      }
    }
  } else if (t === "user") {
    const content = (msg.message as { content?: unknown[] })?.content;
    for (const c of Array.isArray(content) ? content : []) {
      const part = c as { type?: string; is_error?: boolean };
      if (part.type === "tool_result") yield { type: "tool_done", error: Boolean(part.is_error) };
    }
  } else if (t === "result") {
    yield {
      type: "done",
      answer: typeof msg.result === "string" ? msg.result : "",
      session_id: msg.session_id,
      cost_usd: msg.total_cost_usd,
      turns: msg.num_turns,
    };
  }
}

export async function POST(req: Request) {
  if (!verifyOwnerToken(bearer(req))) return deny();

  let body: ChatBody;
  try {
    body = (await req.json()) as ChatBody;
  } catch {
    return NextResponse.json({ error: "bad json" }, { status: 400 });
  }
  const message = String(body?.message || "").trim();
  if (!message) return NextResponse.json({ error: "need {message}" }, { status: 400 });
  // The session id enters a child argv — UUID alphabet or it doesn't ride.
  const session = /^[0-9a-fA-F-]{8,64}$/.test(String(body.session || "")) ? String(body.session) : "";
  const page = String(body.page || "").slice(0, 200);

  const runId = `chat_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 8)}`;

  // The gate arms on POLYMARKET_AGENT_RUN; the TTL/state-dir ride along so a
  // test deployment's overrides reach the child too.
  const mcpEnv: Record<string, string> = { POLYMARKET_AGENT_RUN: runId };
  for (const k of ["POLYMARKET_ACCESS_DIR", "POLYMARKET_APPROVAL_TTL", "POLYMARKET_API_URL"]) {
    if (process.env[k]) mcpEnv[k] = String(process.env[k]);
  }
  const mcpConfig = JSON.stringify({
    mcpServers: { polymarket: { command: "python3", args: [mcpServerPath()], env: mcpEnv } },
  });

  const args = [
    "-p", message,
    "--output-format", "stream-json", "--verbose",
    "--model", AGENT_MODEL,
    "--max-turns", String(MAX_TURNS),
    "--restricted", "--tools", "",
    "--mcp-config", mcpConfig, "--strict-mcp-config",
    // The whole server is allowed: the dangerous half parks at mcp.py's
    // approval gate, which no tool-list edit can widen past the owner.
    "--allowedTools", "mcp__polymarket",
    "--append-system-prompt", chatPrompt(page),
  ];
  if (session) args.push("--resume", session);

  const env = await checkedClaudeEnv();

  const encoder = new TextEncoder();
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      let closed = false;
      const send = (ev: Ev) => {
        if (closed) return;
        try {
          controller.enqueue(encoder.encode(`data: ${JSON.stringify(ev)}\n\n`));
        } catch {
          closed = true;
        }
      };

      let child: ReturnType<typeof spawn>;
      try {
        child = spawn(CLAUDE_BIN, args, { stdio: ["ignore", "pipe", "pipe"], env });
      } catch (e) {
        send({ type: "error", error: `could not start the claude CLI: ${e instanceof Error ? e.message : String(e)}` });
        controller.close();
        return;
      }

      // Approval cards ride the SAME stream — no second channel to lose.
      const seen = new Map<string, string | null>();
      const approvalTimer = setInterval(() => {
        try {
          for (const a of listApprovals(runId)) {
            const prev = seen.get(a.id);
            // Navigation entries are pre-decided, never pending: forward each
            // once as a `nav` event and the browser does the router.push.
            if (a.kind === "nav") {
              if (prev === undefined) {
                seen.set(a.id, a.decision);
                send({ type: "nav", id: a.id, path: String(a.args?.path || ""), label: a.summary });
              }
              continue;
            }
            if (prev === undefined) {
              seen.set(a.id, a.decision);
              if (!a.decision) {
                send({ type: "approval", approval: a });
              }
            } else if (prev !== a.decision && a.decision) {
              seen.set(a.id, a.decision);
              send({ type: "approval_done", id: a.id, decision: a.decision, note: a.note });
            }
          }
        } catch {
          // a torn read is retried next tick; the gate itself fails closed
        }
      }, APPROVAL_POLL_MS);
      const pingTimer = setInterval(() => send({ type: "ping" }), PING_MS);

      let stderrTail = "";
      let sawDone = false;
      const finish = (lastWords?: Ev) => {
        if (closed) return;
        clearInterval(approvalTimer);
        clearInterval(pingTimer);
        clearTimeout(watchdog);
        // Fail closed: a card nobody can answer anymore is a decline.
        try { declineRunLeftovers(runId, "the chat stream closed before an answer"); } catch { /* gate TTL covers it */ }
        if (lastWords) send(lastWords);
        closed = true;
        try { controller.close(); } catch { /* already gone */ }
      };

      const watchdog = setTimeout(() => {
        child.kill("SIGKILL");
        finish({ type: "error", error: `the agent did not finish within ${RUN_TIMEOUT_MS / 60000} minutes` });
      }, RUN_TIMEOUT_MS);

      let buf = "";
      child.stdout?.on("data", (d: Buffer) => {
        buf += d.toString();
        let nl: number;
        while ((nl = buf.indexOf("\n")) >= 0) {
          const line = buf.slice(0, nl).trim();
          buf = buf.slice(nl + 1);
          if (!line) continue;
          let msg: Record<string, unknown>;
          try {
            msg = JSON.parse(line) as Record<string, unknown>;
          } catch {
            continue;
          }
          for (const ev of cliEvents(msg)) {
            if ((ev.type === "done" || ev.type === "text") &&
                AUTH_FAIL_ANSWER.test(String(ev.answer ?? ev.text ?? ""))) {
              send({ type: "error", error: String(ev.answer ?? ev.text), auth: true });
              continue;
            }
            if (ev.type === "done") sawDone = true;
            send(ev);
          }
        }
      });
      child.stderr?.on("data", (d: Buffer) => {
        stderrTail = (stderrTail + d.toString()).slice(-800);
      });
      child.on("error", (e) => finish({ type: "error", error: `claude CLI unavailable: ${e.message}` }));
      child.on("close", (code) => {
        if (code !== 0 && !sawDone) {
          finish({ type: "error", error: (stderrTail.trim() || `claude CLI exited ${code}`).slice(0, 400) });
        } else {
          finish();
        }
      });

      // The browser went away: kill the run; `close` fires and fails closed.
      req.signal?.addEventListener("abort", () => child.kill("SIGKILL"));
    },
  });

  return new Response(stream, {
    headers: {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache, no-transform",
      Connection: "keep-alive",
    },
  });
}

// /polymarket/api/strat-chat — the agent behind every strat's CHAT tab.
//
// You describe what you want the strat to do; an agent answers in the strat's
// own vocabulary and, when the ask implies a settings change, proposes a PATCH
// you can read and apply. It never writes anything: this route returns a
// proposal, the browser shows the diff, and the strat only changes when you
// press APPLY.
//
// Three things keep that safe:
//
//   1. OWNER-GATED. Same Bearer token the Rust API issues (server/ownerToken),
//      same gate /api/hub uses. A strat is private and this route spends money
//      on inference, so it verifies before doing anything.
//   2. NARROW CONTRACT. The model's patch is validated against lib/stratPatch
//      — known paths, known types, known ranges — before it leaves this file.
//      A hallucinated parameter comes back as a visible rejection, not a write.
//   3. NO EXECUTION. The agent has no tools. It reads the strat, its backtest
//      and its live session as text, and writes JSON back. It cannot place an
//      order, start a session, or touch the watchlist.
//
// The model runs through the `claude` CLI already installed on the box (the
// same binary the fleet's other consoles drive), so this needs no API key of
// its own and inherits whatever credentials that CLI is signed in with. When
// the CLI is missing the route says so plainly — a strat chat that silently
// answers from nothing would be worse than one that admits it can't.

import { spawn } from "child_process";
import { NextResponse } from "next/server";

import { bearer, verifyOwnerToken } from "../../lib/server/ownerToken";
import {
  currentSettings, paramReference, validatePatch,
  type PatchEntry,
} from "../../lib/stratPatch";
import type { SavedIndex } from "../../lib/types";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

/** Opus by default — this is judgment work over a strategy's parameters, and
    the console asks one question at a time rather than running a fleet. */
const MODEL = process.env.POLYMARKET_CHAT_MODEL || "claude-opus-5";
/** A parameter question is not a research task; past this the user is better
    served by an error than by a spinner. */
const TIMEOUT_MS = 120_000;
/** Turns of history sent back. Enough to hold a thread, small enough that the
    prompt stays a prompt. */
const MAX_HISTORY = 12;

interface ChatMessage {
  role: "user" | "assistant";
  content: string;
}

interface ChatRequest {
  strat: SavedIndex;
  messages: ChatMessage[];
  /** Optional context the console already has on screen: the strat's latest
      backtest, its live session state. Passed as text, never interpreted. */
  context?: {
    backtest?: string;
    live?: string;
  };
}

function deny() {
  return NextResponse.json({ error: "unauthorized", gate: "polymarket-access" }, { status: 401 });
}

/** Everything the agent is told, in one string.
 *
 *  The reference and the current settings both come from lib/stratPatch, so
 *  the agent is briefed on exactly the parameters the validator will accept —
 *  a prompt that listed more would just generate rejections. */
function buildPrompt(body: ChatRequest): string {
  const { strat } = body;
  const watchlist = strat.traders?.filter((t) => t.enabled !== false) ?? [];
  const originates = !!strat.momentum;

  const history = body.messages
    .slice(-MAX_HISTORY)
    .map((m) => `${m.role === "user" ? "USER" : "YOU"}: ${m.content}`)
    .join("\n\n");

  return [
    `You are the strategy assistant for a Polymarket copy-trading console. You are talking to the person who owns this strategy, about this strategy only.`,
    ``,
    `WHAT THE STRATEGY IS`,
    `Name: ${strat.name}`,
    `Kind: ${originates
      ? "ORIGINATION — it has no need of a watchlist; it reads a market's own price tape and buys the outcome whose odds are rising, selling when the move flips."
      : `COPY — it mirrors ${watchlist.length} watched trader(s), sized proportionally to what they risk.`}`,
    `Watchlist: ${watchlist.length} enabled trader(s)`,
    ``,
    `CURRENT SETTINGS (paths are exactly the keys a patch may use)`,
    JSON.stringify(currentSettings(strat), null, 2),
    ``,
    `PARAMETERS YOU MAY CHANGE`,
    paramReference(),
    ``,
    body.context?.backtest ? `LATEST BACKTEST\n${body.context.backtest}\n` : ``,
    body.context?.live ? `LIVE SESSION\n${body.context.live}\n` : ``,
    `HOW THIS SYSTEM ACTUALLY BEHAVES — do not contradict these:`,
    `- Entry gates are BUY-only. Exits (stop-loss, take-profit, momentum flip) are never gated, so tightening a filter can never strand a position.`,
    `- Polymarket's order floor is max($1, 5 shares × price). At 60¢ that is $3.00, so a minTrade under it does not produce smaller orders — it produces skipped ones.`,
    `- The live engine clamps its poll cadence up to 30 seconds. Asking for faster does nothing.`,
    `- Sub-hour markets (5-minute Up/Down candles) resolve faster than a poller can react to a copied trade. A copy strat that wants them is structurally behind; an origination strat reading the candle's own tape is not.`,
    `- Changing capital rescales every order, because sizing is proportional.`,
    ``,
    `HOW TO ANSWER`,
    `- Be concrete and brief. Two or three sentences of plain prose is usually right; no headers, no bullet lists unless you are genuinely enumerating.`,
    `- Say what a change will DO to the strategy's behaviour, not what the parameter is called.`,
    `- Propose a patch only when the person asked for a change or clearly wants one. Questions get answers, not edits.`,
    `- Change the fewest parameters that accomplish the ask. Do not tidy neighbouring settings you were not asked about.`,
    `- If the ask cannot be done with these parameters (it needs different traders, a different strategy kind, or a feature that does not exist), say so plainly and propose nothing.`,
    `- If a request would plainly hurt the strategy, say so in one sentence — then still propose it if they asked for it. It is their strategy.`,
    ``,
    `CONVERSATION`,
    history,
    ``,
    `Reply with ONE JSON object and nothing else — no prose outside it, no markdown fence:`,
    `{"reply": "<your answer to the person>", "patch": {"<param path>": <new value>}, "rationale": "<one line on why these values, or empty>"}`,
    `Use {} for "patch" when you are not proposing a change. Use null as a value only to clear a nullable parameter.`,
  ].filter(Boolean).join("\n");
}

/** The model's JSON, dug out of whatever it wrapped it in. Fenced blocks and
    stray prose are both survivable; a response with no object at all is not. */
function parseReply(raw: string): { reply: string; patch: unknown; rationale?: string } | null {
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
      if (typeof o.reply === "string") {
        return {
          reply: o.reply,
          patch: o.patch,
          rationale: typeof o.rationale === "string" ? o.rationale : undefined,
        };
      }
    } catch {
      // try the next shape
    }
  }
  return null;
}

/** Run the prompt through the local `claude` CLI and hand back its text.
 *
 *  The prompt goes in on STDIN, not argv: it embeds the strat's JSON and the
 *  user's words, and an argv-sized prompt would be both truncated and quoting-
 *  sensitive. */
function runAgent(prompt: string): Promise<{ ok: true; text: string } | { ok: false; error: string }> {
  return new Promise((resolve) => {
    let child;
    try {
      child = spawn(process.env.POLYMARKET_CLAUDE_BIN || "claude", [
        "-p",
        "--output-format", "json",
        "--model", MODEL,
      ], { stdio: ["pipe", "pipe", "pipe"] });
    } catch (e) {
      resolve({ ok: false, error: `could not start the claude CLI: ${e instanceof Error ? e.message : String(e)}` });
      return;
    }

    let out = "";
    let err = "";
    let settled = false;
    const finish = (r: { ok: true; text: string } | { ok: false; error: string }) => {
      if (settled) return;
      settled = true;
      resolve(r);
    };

    const timer = setTimeout(() => {
      child.kill("SIGKILL");
      finish({ ok: false, error: `the agent did not answer within ${TIMEOUT_MS / 1000}s` });
    }, TIMEOUT_MS);

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

    child.stdin.end(prompt);
  });
}

export async function POST(req: Request) {
  if (!verifyOwnerToken(bearer(req))) return deny();

  let body: ChatRequest;
  try {
    body = (await req.json()) as ChatRequest;
  } catch {
    return NextResponse.json({ error: "bad json" }, { status: 400 });
  }
  if (!body?.strat?.id || !Array.isArray(body.messages) || body.messages.length === 0) {
    return NextResponse.json({ error: "need {strat, messages}" }, { status: 400 });
  }

  const run = await runAgent(buildPrompt(body));
  if (run.ok === false) return NextResponse.json({ error: run.error }, { status: 502 });

  const parsed = parseReply(run.text);
  if (!parsed) {
    // The model answered, just not in the shape asked for. Its prose is still
    // worth showing — losing a good answer to a formatting slip helps nobody.
    return NextResponse.json({
      reply: run.text.trim().slice(0, 4000),
      entries: [] as PatchEntry[],
      rejected: ["the agent's reply wasn't valid JSON, so any patch it meant to propose was dropped"],
    });
  }

  const { entries, rejected } = validatePatch(body.strat, parsed.patch);
  return NextResponse.json({
    reply: parsed.reply,
    rationale: parsed.rationale,
    entries,
    rejected,
    model: MODEL,
  });
}

// /polymarket/_api/help-agent — the agent in the left column (the green mark toggles it).
//
// A guide, not an operator: you ask it anything about the console ("where do
// I put money in", "why is my live session not trading", "what does HOLDOUT
// mean") and it answers from a briefing on how this console is actually laid
// out — naming the tab, button or page where the answer lives. It has no
// tools: it cannot place an order or start a session. It does SEE the copy
// book (a read-only snapshot, bookSnapshot below), so "why isn't it trading"
// gets a real answer. The worst it can do is give directions.
//
// Owner-gated with the same Bearer token as the other agent routes
// (strat-chat, trader-agent) because it spends money on inference. Runs
// through the same `claude` CLI harness, so it needs no API key of its own.

import { NextResponse } from "next/server";

import { API_BASE } from "../../lib/polymarket";
import { AGENT_MODEL, digJson, runClaude } from "../../lib/server/agentCli";
import { CONSOLE_MAP } from "../../lib/server/consoleMap";
import { bearer, verifyOwnerToken } from "../../lib/server/ownerToken";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

/** Turns of history sent back — same budget as strat-chat. */
const MAX_HISTORY = 12;

interface ChatMessage {
  role: "user" | "assistant";
  content: string;
}

interface HelpRequest {
  messages: ChatMessage[];
  /** Where in the console the user is asking from ("/strats", "/traders/0x…"),
      so "this page" means something. Display context only. */
  page?: string;
}

function deny() {
  return NextResponse.json({ error: "unauthorized", gate: "polymarket-access" }, { status: 401 });
}

interface BookRow {
  address: string;
  allocationUsd?: number;
  enabled?: boolean;
  live?: {
    status?: string; running?: boolean; autoExecute?: boolean;
    balance?: number; ordersPlaced?: number; ordersFailed?: number;
    error?: string | null; cycles?: number;
    ledger?: { lastFillAt?: number; realized?: number; buys?: number; sells?: number } | null;
  } | null;
}

const ago = (ms: number) => {
  const m = Math.max(0, (Date.now() - ms) / 60_000);
  return m < 90 ? `${Math.round(m)}m ago` : m < 2880 ? `${Math.round(m / 60)}h ago` : `${Math.round(m / 1440)}d ago`;
};

/** The person's copy book as a few plain lines — what the COPY tab shows,
    read through the same owner-gated GET the tab uses. Eyes only: the agent
    still cannot change any of it. Null (and the prompt says so) when the API
    is down, so a dead engine reads as "can't see", never as "nothing runs". */
async function bookSnapshot(token: string, eoa: string): Promise<string | null> {
  try {
    const res = await fetch(`${API_BASE}/copy/book?eoa=${encodeURIComponent(eoa)}`, {
      headers: { authorization: `Bearer ${token}` },
      signal: AbortSignal.timeout(4000),
      cache: "no-store",
    });
    if (!res.ok) return null;
    const book = (await res.json()) as {
      allocations?: BookRow[];
      totals?: { allocatedUsd?: number; traders?: number; running?: number; executing?: number };
    };
    const t = book.totals || {};
    const lines = [
      `${t.traders ?? 0} traders in the copy book, $${(t.allocatedUsd ?? 0).toFixed(0)} allocated, ${t.running ?? 0} running (${t.executing ?? 0} REAL).`,
    ];
    for (const a of (book.allocations || []).slice(0, 25)) {
      const l = a.live;
      const who = `${a.address.slice(0, 6)}…${a.address.slice(-4)} $${(a.allocationUsd ?? 0).toFixed(0)}`;
      if (!l) { lines.push(`- ${who}: never started${a.enabled === false ? " (disabled)" : ""}`); continue; }
      const bits = [
        l.running ? `RUNNING ${l.autoExecute ? "REAL" : "PAPER"}` : (l.status || "stopped").toUpperCase(),
        typeof l.balance === "number" ? `trading balance $${l.balance.toFixed(2)}` : "",
        `${l.ordersPlaced ?? 0} orders placed, ${l.ordersFailed ?? 0} failed`,
        l.ledger?.lastFillAt ? `last fill ${ago(l.ledger.lastFillAt)}` : "no fills yet",
        typeof l.ledger?.realized === "number" ? `realized $${l.ledger.realized.toFixed(2)}` : "",
        l.error ? `ERROR: ${String(l.error).slice(0, 160)}` : "",
      ].filter(Boolean);
      lines.push(`- ${who}: ${bits.join(", ")}`);
    }
    return lines.join("\n");
  } catch {
    return null;
  }
}

function buildPrompt(body: HelpRequest, book: string | null): string {
  const history = body.messages
    .slice(-MAX_HISTORY)
    .map((m) => `${m.role === "user" ? "USER" : "YOU"}: ${m.content}`)
    .join("\n\n");

  return [
    `You are the help agent for a Polymarket copy-trading console — the assistant in the left column, opened by the green mark at the top-left. Your job is to get the person unstuck and pointed at the right control.`,
    ``,
    CONSOLE_MAP,
    ``,
    body.page ? `THE PERSON IS CURRENTLY ON: ${body.page}` : ``,
    ``,
    book
      ? `THEIR COPY BOOK RIGHT NOW (read-only, live from the engine — use it to answer "is it running / why no trades" questions; a REAL session with no fill for days usually means the trader hasn't traded, or the trading balance is below the order floor):\n${book}`
      : `THEIR COPY BOOK: unavailable right now (the engine didn't answer) — say you can't see it rather than guessing.`,
    ``,
    `HOW TO ANSWER`,
    `- Two or three sentences of plain prose. Name the exact tab, button, or page — "open the side panel's MONEY tab", not "navigate to the funding section". If they want step-by-step help, give numbered steps, one control per step.`,
    `- Answer only from the map above. If they ask about something the console doesn't have, say it doesn't exist rather than improvising a feature.`,
    `- You are a guide with no hands: you cannot click, trade, or change settings for them. When they want a strat's settings changed, send them to that strat's CHAT tab, which can propose the change.`,
    `- Money questions get the honest caveat: LIVE trades real money, BACKTEST doesn't.`,
    ``,
    `CONVERSATION`,
    history,
    ``,
    `Reply with ONE JSON object and nothing else — no prose outside it, no markdown fence:`,
    `{"reply": "<your answer to the person>"}`,
  ].filter(Boolean).join("\n");
}

export async function POST(req: Request) {
  const token = bearer(req);
  const owner = verifyOwnerToken(token);
  if (!token || !owner) return deny();

  let body: HelpRequest;
  try {
    body = (await req.json()) as HelpRequest;
  } catch {
    return NextResponse.json({ error: "bad json" }, { status: 400 });
  }
  if (!Array.isArray(body?.messages) || body.messages.length === 0) {
    return NextResponse.json({ error: "need {messages}" }, { status: 400 });
  }

  const run = await runClaude(buildPrompt(body, await bookSnapshot(token, owner)));
  if (run.ok === false) return NextResponse.json({ error: run.error }, { status: 502 });

  const parsed = digJson(run.text, (o) =>
    typeof o.reply === "string" ? { reply: o.reply } : null,
  );
  // A formatting slip loses the envelope, not the answer — same degradation
  // as strat-chat.
  return NextResponse.json({
    reply: (parsed?.reply ?? run.text.trim()).slice(0, 4000),
    model: AGENT_MODEL,
  });
}

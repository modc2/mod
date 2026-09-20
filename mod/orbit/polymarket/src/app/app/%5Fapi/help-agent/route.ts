// /polymarket/_api/help-agent — the agent behind the header's ✚ HELP icon.
//
// A guide, not an operator: you ask it anything about the console ("where do
// I put money in", "why is my live session not trading", "what does HOLDOUT
// mean") and it answers from a briefing on how this console is actually laid
// out — naming the tab, button or page where the answer lives. It has no
// tools: it cannot place an order, start a session, or read your wallet. The
// worst it can do is give directions.
//
// Owner-gated with the same Bearer token as the other agent routes
// (strat-chat, trader-agent) because it spends money on inference. Runs
// through the same `claude` CLI harness, so it needs no API key of its own.

import { NextResponse } from "next/server";

import { AGENT_MODEL, digJson, runClaude } from "../../lib/server/agentCli";
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

// The console, as a map the model may not contradict. This is the entire
// knowledge base — the agent knows what's written here and nothing else, so
// when the console changes shape, change this briefing with it.
const CONSOLE_MAP = `
THE CONSOLE'S MAP — this is the ground truth; never invent a feature not listed here.

PAGES (tabs in the top-left):
- "/" — THE BOARD. Traders ranked by ROI over the last 30 days by default; the
  rank window and score function are changeable from the board's own controls.
  ▦ SCORE MARKET and market views live here too.
- "/strats" — build and manage strategies. STRATS | TRADES pills at the top,
  + NEW to create one. Each strat card opens allocation, its traders, and its
  own CHAT tab (an agent that can propose settings changes you APPLY).
- "/traders/<address>" — one trader's profile: PnL curve first, their trades,
  filters and a backtest simulator in the right rail, + ADD to put them on
  your bench.
- "/copy" — the copy desk (roster + allocation chart).

THE SIDE PANEL (the ▯ icon next to the wallet chip opens/closes it):
- INDEX tab — your strats and the money on each (ALLOCATION), the bench you're
  building, and the copy book.
- MONEY tab — top up your trading balance, take money out, or send it.
- BACKTEST tab — replay the bench against history on simulated money.
- LIVE tab — run the bench against the real book with real money.

THE SEARCH BAR (below the header) is three things in one:
- typing live-filters the board underneath;
- pasting a 0x wallet address + ENTER teleports to that trader's profile;
- describing a trader in words + ENTER runs the TRADER SCOUT, an agent that
  queries the live leaderboard and answers with clickable addresses.

THE WALLET CHIP (top-right): the dot is bright green when trading is enabled
(CLOB authenticated), amber when connected but not yet trading-enabled, gray
when disconnected. Clicking it opens the ACCOUNTS block in the side panel,
where wallets are added and switched. ×N on the chip = N known accounts.

HOW THE MACHINE BEHAVES (facts, do not contradict):
- A strat copies its watched traders' trades, sized proportionally to your
  capital against each trader's own book.
- Entry gates are BUY-only; exits are never gated.
- Polymarket's order floor is max($1, 5 shares × price) — tiny capital means
  skipped orders, not smaller ones.
- The live engine polls no faster than every 30 seconds.
- Backtests can run HOLDOUT (stats frozen as-of a date, then replayed forward)
  to test a strat honestly.
- Sign-in is wallet-based (MetaMask personal_sign); agent features (this one,
  the scout, strat chat) run on the owner's inference and need sign-in.
`.trim();

function buildPrompt(body: HelpRequest): string {
  const history = body.messages
    .slice(-MAX_HISTORY)
    .map((m) => `${m.role === "user" ? "USER" : "YOU"}: ${m.content}`)
    .join("\n\n");

  return [
    `You are the help agent for a Polymarket copy-trading console — the little assistant behind the header's help icon. Your job is to get the person unstuck and pointed at the right control.`,
    ``,
    CONSOLE_MAP,
    ``,
    body.page ? `THE PERSON IS CURRENTLY ON: ${body.page}` : ``,
    ``,
    `HOW TO ANSWER`,
    `- Two or three sentences of plain prose. Name the exact tab, button, or page — "open the side panel's MONEY tab", not "navigate to the funding section".`,
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
  if (!verifyOwnerToken(bearer(req))) return deny();

  let body: HelpRequest;
  try {
    body = (await req.json()) as HelpRequest;
  } catch {
    return NextResponse.json({ error: "bad json" }, { status: 400 });
  }
  if (!Array.isArray(body?.messages) || body.messages.length === 0) {
    return NextResponse.json({ error: "need {messages}" }, { status: 400 });
  }

  const run = await runClaude(buildPrompt(body));
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

// /polymarket/api/trader-agent — the agent behind the top bar.
//
// You describe the trader you want ("consistent winners over 30 days with
// real volume, nothing sub-hour") and an agent goes and FINDS them — not from
// its own memory, but through this module's own MCP server (src/mcp.py): the
// same pm_top_traders / pm_trader research tools any external agent gets. The
// sandbox is the tool list — `--restricted --tools ""` + `--strict-mcp-config`
// + a read-only allowlist, so the scout can query leaderboards and inspect
// wallets and literally nothing else. No desk tools, no live tools, no shell.
//
// Owner-gated with the same Bearer token as the other agent routes — this
// spends money on inference. The reply is a shortlist of addresses the UI
// renders as clickable rows; every address is regex-validated here before it
// reaches the browser.

import { NextResponse } from "next/server";

import { AGENT_MODEL, digJson, runClaude } from "../../lib/server/agentCli";
import { mcpServerPath } from "../../lib/server/lab";
import { bearer, verifyOwnerToken } from "../../lib/server/ownerToken";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

/** Research only. The scout finds traders; a human copies them. */
const SCOUT_TOOLS = ["pm_health", "pm_markets", "pm_top_traders", "pm_trader"]
  .map((t) => `mcp__polymarket__${t}`);

/** pm_top_traders answers from the warm cache in seconds, but a cold window
    pays for a full aggregation (minutes) — the timeout must survive one. */
const SCOUT_TIMEOUT_MS = 240_000;
const MAX_ROWS = 8;

const ADDR_RE = /^0x[a-f0-9]{40}$/;

interface AskRequest {
  /** The trader the user wants, in their words. */
  ask: string;
  /** The board's current ranking window, so "recent" means something. */
  days?: number;
  /** Wallets the owner's strats already copy — so "find me NEW traders"
      means new, not the roster read back. The console sends them; capped. */
  known?: string[];
}

interface ScoutTrader {
  address: string;
  label: string;
  stat: string;
  why: string;
  /** Already on the owner's roster — the UI badges instead of celebrating. */
  tracked?: boolean;
}

const MAX_KNOWN = 100;

function knownSet(body: AskRequest): Set<string> {
  const out = new Set<string>();
  for (const a of Array.isArray(body.known) ? body.known : []) {
    if (typeof a !== "string") continue;
    const addr = a.trim().toLowerCase();
    if (ADDR_RE.test(addr)) out.add(addr);
    if (out.size >= MAX_KNOWN) break;
  }
  return out;
}

function deny() {
  return NextResponse.json({ error: "unauthorized", gate: "polymarket-access" }, { status: 401 });
}

function buildPrompt(body: AskRequest, known: Set<string>): string {
  const roster = known.size > 0
    ? [
        ``,
        `ALREADY TRACKED — wallets the owner's strats copy today (${known.size}):`,
        [...known].join(" "),
        `When the ask wants new / fresh / undiscovered traders, these do NOT count as finds — exclude them from your shortlist. Otherwise you may still put one forward when it is genuinely the best fit; its "why" should say it's already on their roster.`,
      ]
    : [];
  return [
    `You are the trader scout of a self-hosted Polymarket copy-trading console, answering its owner. Your job: FIND the top traders that match the ask, using the MCP tools — never from memory. An address you did not read out of a tool result does not exist.`,
    ``,
    `TOOLS`,
    `- pm_top_traders — the leaderboard. Windows via days (1/7/30/…), sorts: pnl, roi, winRate, sharpe, volume. Use min_history_days on long windows so a week-old wallet can't fake a 30-day record, and active_hours to drop dormant leaders. Query MORE THAN ONE window/sort when the ask is about consistency.`,
    `- pm_trader — one wallet's recent flow. Use it on your shortlist: it reports short_dated_share, the fraction of flow in sub-hour Up/Down candles that NO copy-trader can mirror. A leader above ~0.5 there is usually a disqualification, and always worth flagging.`,
    `- pm_markets / pm_health — market lookup and feed coverage, when the ask needs them.`,
    ``,
    `THE ASK (their board currently ranks over ${body.days || 30} days)`,
    body.ask,
    ...roster,
    ``,
    `RULES`,
    `- 2-6 tool calls is the right budget: leaderboard first, then pm_trader on the few you'd actually put forward. Don't inspect all twenty.`,
    `- Rank by fit to THE ASK, not by raw pnl.`,
    `- "stat" is the evidence line, from tool numbers: e.g. "30D +$41k pnl · 62% win · $310k vol". "why" is one short clause of judgment: why this one, or the caveat.`,
    `- If the ask isn't really a trader search (a question, a market ask), answer it in "reply" and return an empty traders list.`,
    ``,
    `Reply with ONE JSON object and nothing else — no prose outside it, no markdown fence:`,
    `{"reply": "<one or two sentences: what you searched and the judgment calls>", "traders": [{"address": "0x…", "label": "<short handle: leaderboard name/pseudonym if the tools gave one, else a 2-3 word description>", "stat": "<the numbers>", "why": "<one clause>"}]}`,
    `At most ${MAX_ROWS} traders, best fit first.`,
  ].join("\n");
}

export async function POST(req: Request) {
  if (!verifyOwnerToken(bearer(req))) return deny();

  let body: AskRequest;
  try {
    body = (await req.json()) as AskRequest;
  } catch {
    return NextResponse.json({ error: "bad json" }, { status: 400 });
  }
  if (!body?.ask?.trim()) {
    return NextResponse.json({ error: "need {ask}" }, { status: 400 });
  }

  const known = knownSet(body);
  const mcpConfig = JSON.stringify({
    mcpServers: { polymarket: { command: "python3", args: [mcpServerPath()] } },
  });
  const run = await runClaude(buildPrompt(body, known), AGENT_MODEL, {
    timeoutMs: SCOUT_TIMEOUT_MS,
    extraArgs: [
      "--restricted", "--tools", "",
      "--mcp-config", mcpConfig, "--strict-mcp-config",
      "--allowedTools", SCOUT_TOOLS.join(","),
      "--max-turns", "16",
    ],
  });
  if (run.ok === false) return NextResponse.json({ error: run.error }, { status: 502 });

  const parsed = digJson(run.text, (o) => {
    if (typeof o.reply !== "string") return null;
    const rows = Array.isArray(o.traders) ? o.traders : [];
    const traders: ScoutTrader[] = [];
    for (const r of rows) {
      if (!r || typeof r !== "object") continue;
      const t = r as Record<string, unknown>;
      const address = String(t.address || "").trim().toLowerCase();
      if (!ADDR_RE.test(address)) continue; // an invented address never renders
      traders.push({
        address,
        label: String(t.label || "").slice(0, 60),
        stat: String(t.stat || "").slice(0, 120),
        why: String(t.why || "").slice(0, 200),
        ...(known.has(address) ? { tracked: true } : {}),
      });
      if (traders.length >= MAX_ROWS) break;
    }
    return { reply: o.reply.slice(0, 2000), traders };
  });
  if (!parsed) {
    // The model answered, just not in the shape asked for — show its prose
    // rather than losing a good answer to a formatting slip.
    return NextResponse.json({ reply: run.text.trim().slice(0, 2000), traders: [] });
  }

  return NextResponse.json({ ...parsed, model: AGENT_MODEL });
}

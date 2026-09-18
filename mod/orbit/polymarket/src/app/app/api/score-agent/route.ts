// /polymarket/api/score-agent — the agent behind the SCORE editor's ASK box.
//
// You describe the trader you want on top ("consistent winners with real
// volume, hide anyone under $500 traded") and an agent writes the score for
// you — an expression when ranking is enough, a JS or Python function when
// rows need to be hidden too. Nothing is trusted blind: the formula lands in
// the same editor box the user types into, compiles IN THE BROWSER through
// the exact same path (useScore), and shows ERR there if the agent wrote
// nonsense. This route proposes text; the editor decides if it runs.
//
// Owner-gated with the same Bearer token as /api/strat-chat — this spends
// money on inference. The model runs through the local `claude` CLI
// (lib/server/agentCli), so no API key lives here.

import { NextResponse } from "next/server";

import { AGENT_MODEL, digJson, runClaude } from "../../lib/server/agentCli";
import { bearer, verifyOwnerToken } from "../../lib/server/ownerToken";
import { FORMULA_VARS, SCORE_PRESETS, SCORE_VAR_HINTS } from "../../lib/scoreFormula";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

/** sanitizeRatios caps a saved formula at 4000 chars — the agent's output
    obeys the same ceiling so what it writes can always be saved as a chip. */
const MAX_FORMULA = 4000;

interface AskRequest {
  /** What the user asked for, in their words. */
  ask: string;
  /** The formula currently in the box — context, and the base for "make it
      also …" style follow-ups. */
  formula?: string;
  /** Days in the ranking window, so "recent" means something concrete. */
  days?: number;
}

function deny() {
  return NextResponse.json({ error: "unauthorized", gate: "polymarket-access" }, { status: 401 });
}

function buildPrompt(body: AskRequest): string {
  const vars = FORMULA_VARS.map((v) => `  ${v} — ${SCORE_VAR_HINTS[v]}`).join("\n");
  const presets = SCORE_PRESETS.map((p) => `  ${p.label}: ${p.formula}`).join("\n");
  return [
    `You write the SCORE for a Polymarket trader leaderboard. The score runs in the user's browser on every trader in the pool and decides the ranking (biggest score first). You are answering the person who owns the board.`,
    ``,
    `EACH TRADER IS A ROW OF NUMBERS over the last ${body.days || 30} days:`,
    vars,
    ``,
    `Sentinels: winRate, resolveRate, exitEntry and consistency are -1 when there is nothing to judge yet (nothing settled/closed, or a PnL curve too flat/short) — a formula that uses them should usually treat -1 as "unknown" (gate it with a null return), never multiply by it. winRate and resolveRate are 0-100 percents, not 0-1 fractions. winRate = settled positions that MADE MONEY (a profitable early scalp counts); resolveRate = settled buys whose token FINISHED AT $1 (the buy-and-hold hit rate) — an ask about "the outcome resolving their way" means resolveRate.`,
    ``,
    `THREE SHAPES THE SCORE BOX ACCEPTS (detected from the text itself):`,
    `1. EXPRESSION — plain arithmetic over the variables: "100 * pnl / volume". Ranks only, hides nobody. Prefer this when the ask is purely about ordering.`,
    `2. JS FUNCTION BODY — statements ending in \`return\`; the variables are in scope, Math is available. \`return null\` HIDES the trader from the board entirely; \`return <number>\` is their score. Use this when the ask filters ("hide anyone under…", "only traders that…").`,
    `3. PYTHON — \`def score(pnl, volume, **rest): …\` returning a number, or None to hide. Runs in-browser via Pyodide. Only choose Python when the user asks for it.`,
    ``,
    `Built-in presets, for vocabulary (don't just restate one unless it IS the answer):`,
    presets,
    ``,
    body.formula ? `CURRENT SCORE IN THE BOX:\n${body.formula}\n` : ``,
    `THE ASK`,
    body.ask,
    ``,
    `RULES`,
    `- Write the SIMPLEST shape that does the whole job. No filtering → expression. Filtering → JS function body.`,
    `- Use ONLY the variables listed. There is no price history, no per-trade data, no async, no fetch — one row of numbers per trader.`,
    `- Keep it short and readable; a comment line only where a threshold isn't self-evident.`,
    `- If the ask can't be computed from these variables (e.g. "traders who are early", "avoid sports"), say so in the reply, and give the closest computable score if a reasonable one exists — otherwise return an empty formula.`,
    `- If the ask is a question about the score rather than a request for one, answer it and return an empty formula.`,
    ``,
    `Reply with ONE JSON object and nothing else — no prose outside it, no markdown fence:`,
    `{"reply": "<one or two plain sentences: what this score does and any judgment call you made>", "formula": "<the score text, or empty string>"}`,
  ].filter(Boolean).join("\n");
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

  const run = await runClaude(buildPrompt(body));
  if (run.ok === false) return NextResponse.json({ error: run.error }, { status: 502 });

  const parsed = digJson(run.text, (o) =>
    typeof o.reply === "string"
      ? { reply: o.reply, formula: typeof o.formula === "string" ? o.formula : "" }
      : null,
  );
  if (!parsed) {
    // The model answered, just not in the shape asked for — show its prose
    // rather than losing a good answer to a formatting slip.
    return NextResponse.json({ reply: run.text.trim().slice(0, 2000), formula: "" });
  }

  return NextResponse.json({
    reply: parsed.reply,
    formula: parsed.formula.trim().slice(0, MAX_FORMULA),
    model: AGENT_MODEL,
  });
}

// /polymarket/api/lab — the STRAT LAB: an agent that hunts for the best strat
// this deployment's data supports, backtesting and refining until confident.
//
//   GET                     list runs (id, goal, status, verdict flag)
//   GET  ?id=lab_x          one run: streamed steps + final verdict
//   POST {goal?, maxExperiments?}   start a run (one at a time)
//   POST ?candidate=1 {params, windows}
//                           the bench: backtest ONE candidate param set over
//                           the cached feeds, walk-forward included. This is
//                           what the agent's pm_lab_backtest tool calls; the
//                           console's VIBE editor calls it too.
//   POST ?draft=1 {ask}     plain words → a candidate param set (one no-tool
//                           model turn). Returns {note, params}; the console
//                           drops the params into the VIBE editor — testing
//                           and saving stay human presses.
//   DELETE ?id=lab_x        kill a running agent
//
// Owner-gated like /api/hub and /api/strat-chat: this route spends inference
// and CPU, and a run's verdict names the owner's own trading data.

import { NextResponse } from "next/server";

import { bearer, verifyOwnerToken } from "../../lib/server/ownerToken";
import {
  candidateBacktest, draftCandidate, labRunning, listLabRuns, readLabRun,
  startLabRun, stopLabRun,
} from "../../lib/server/lab";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";
/** A 3-window candidate replay over a cold-ish cache can take a while. */
export const maxDuration = 600;

function deny() {
  return NextResponse.json({ error: "unauthorized", gate: "polymarket-access" }, { status: 401 });
}

export async function GET(req: Request) {
  if (!verifyOwnerToken(bearer(req))) return deny();
  const id = new URL(req.url).searchParams.get("id");
  if (id) {
    const run = readLabRun(id);
    if (!run) return NextResponse.json({ error: "no such run" }, { status: 404 });
    return NextResponse.json(run);
  }
  return NextResponse.json({ runs: listLabRuns(), active: labRunning() });
}

export async function POST(req: Request) {
  if (!verifyOwnerToken(bearer(req))) return deny();
  const url = new URL(req.url);
  let body: Record<string, unknown>;
  try {
    body = (await req.json()) as Record<string, unknown>;
  } catch {
    body = {};
  }

  if (url.searchParams.get("draft") === "1") {
    const ask = typeof body.ask === "string" ? body.ask.trim() : "";
    if (!ask) return NextResponse.json({ error: "need {ask}" }, { status: 400 });
    try {
      return NextResponse.json(await draftCandidate(ask.slice(0, 4000)));
    } catch (e) {
      return NextResponse.json(
        { error: e instanceof Error ? e.message : String(e) },
        { status: 502 },
      );
    }
  }

  if (url.searchParams.get("candidate") === "1") {
    const params = body.params;
    if (!params || typeof params !== "object") {
      return NextResponse.json({ error: "need {params}" }, { status: 400 });
    }
    try {
      const result = await candidateBacktest(params as Record<string, unknown>, body.windows);
      return NextResponse.json(result);
    } catch (e) {
      return NextResponse.json(
        { error: e instanceof Error ? e.message : String(e) },
        { status: 409 },
      );
    }
  }

  try {
    const meta = startLabRun({
      goal: typeof body.goal === "string" ? body.goal : undefined,
      maxExperiments: typeof body.maxExperiments === "number" ? body.maxExperiments : undefined,
    });
    return NextResponse.json({ started: true, run: meta });
  } catch (e) {
    return NextResponse.json(
      { error: e instanceof Error ? e.message : String(e) },
      { status: 409 },
    );
  }
}

export async function DELETE(req: Request) {
  if (!verifyOwnerToken(bearer(req))) return deny();
  const id = new URL(req.url).searchParams.get("id");
  if (!id) return NextResponse.json({ error: "need ?id" }, { status: 400 });
  return NextResponse.json({ stopped: stopLabRun(id) });
}

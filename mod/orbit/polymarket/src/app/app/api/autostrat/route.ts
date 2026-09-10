// /polymarket/api/autostrat — the strat FACTORY's switchboard.
//
//   GET                     settings + the run in flight + recent runs
//   POST {enabled?, intervalSecs?}   flip/retune the 1-minute loop
//   POST ?run=1 {theme?}    RANDOM NEW STRAT — one run right now; returns the
//                           run id immediately, the console polls GET for it
//
// Owner-gated like /api/lab: every run spends inference and a bench slot.
// The loop itself lives in lib/server/autoStrat.ts and is started from
// instrumentation.ts — this route only reads and writes its settings file.

import { NextResponse } from "next/server";

import {
  autoStratActive, listRuns, readSettings, triggerAutoStrat, writeSettings,
} from "../../lib/server/autoStrat";
import { bearer, verifyOwnerToken } from "../../lib/server/ownerToken";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";
export const maxDuration = 60;

function deny() {
  return NextResponse.json({ error: "unauthorized", gate: "polymarket-access" }, { status: 401 });
}

function status() {
  const active = autoStratActive();
  return {
    settings: readSettings(),
    active: active ? { id: active.id, at: active.at, origin: active.origin, theme: active.theme } : null,
    runs: listRuns().slice(0, 20),
  };
}

export async function GET(req: Request) {
  if (!verifyOwnerToken(bearer(req))) return deny();
  return NextResponse.json(status());
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

  if (url.searchParams.get("run") === "1") {
    try {
      const run = triggerAutoStrat("click",
        typeof body.theme === "string" ? body.theme.slice(0, 200) : undefined);
      return NextResponse.json({ started: true, id: run.id, theme: run.theme });
    } catch (e) {
      return NextResponse.json(
        { error: e instanceof Error ? e.message : String(e) },
        { status: 409 },
      );
    }
  }

  const patch: Record<string, unknown> = {};
  if (typeof body.enabled === "boolean") patch.enabled = body.enabled;
  if (Number.isFinite(Number(body.intervalSecs))) patch.intervalSecs = Number(body.intervalSecs);
  writeSettings(patch);
  return NextResponse.json(status());
}

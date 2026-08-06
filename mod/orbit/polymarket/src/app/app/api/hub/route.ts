// /polymarket/api/hub — the console's window onto the background worker.
//
//   GET  ?days=1        the worker's cached backtests for that window + its
//                       schedule (last pass, next pass, strats covered)
//   POST {days, strats} publish the roster the worker should keep warm
//   POST ?run=1         run a pass now
//
// Owner-gated with the same token the Rust API issues (see server/ownerToken).
// The strats in the manifest are the user's own saved strategies — params
// only, no keys, no wallet state — but they're still private, and this route
// can start real network work, so it verifies before doing anything.

import { NextResponse } from "next/server";

import { bearer, verifyOwnerToken } from "../../lib/server/ownerToken";
import {
  readCache, readManifest, runPass, triggerPass, workerRunning, writeManifest,
} from "../../lib/server/hubWorker";
import type { SavedIndex } from "../../lib/types";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

function deny() {
  return NextResponse.json(
    { error: "unauthorized", gate: "polymarket-access" },
    { status: 401 },
  );
}

export async function GET(req: Request) {
  if (!verifyOwnerToken(bearer(req))) return deny();
  const url = new URL(req.url);
  const days = Number(url.searchParams.get("days") || 0);
  const cache = readCache();
  // Cards address results by key; the file stores them per window. Hand back
  // just the requested window, un-suffixed, which is the shape the hub merges.
  const suffix = `@${days}d`;
  const results: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(cache.results)) {
    if (!days || k.endsWith(suffix)) results[days ? k.slice(0, -suffix.length) : k] = v;
  }
  return NextResponse.json({
    status: { ...cache.status, running: workerRunning() },
    results,
  });
}

export async function POST(req: Request) {
  if (!verifyOwnerToken(bearer(req))) return deny();
  const url = new URL(req.url);

  if (url.searchParams.get("run") === "1") {
    // Fire-and-forget: a full pass outlives any reasonable request timeout.
    const queued = triggerPass();
    return NextResponse.json({ queued, status: readCache().status });
  }

  let body: { days?: number; strats?: SavedIndex[] };
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: "bad json" }, { status: 400 });
  }
  const strats = Array.isArray(body.strats) ? body.strats : [];
  const days = Number(body.days) > 0 ? Number(body.days) : readManifest().days;
  writeManifest({ days, strats, at: Date.now() });

  // First manifest ever (or first after a wipe) — no point making the user
  // wait two hours for numbers that could start now.
  const cache = readCache();
  if (!cache.status.at) triggerPass();

  return NextResponse.json({ ok: true, strats: strats.length, days, status: readCache().status });
}

/** Owner-triggered synchronous pass — used by the MCP tool, which wants the
    result rather than a "queued". */
export async function PUT(req: Request) {
  if (!verifyOwnerToken(bearer(req))) return deny();
  const cache = await runPass();
  return NextResponse.json({ status: cache.status, count: Object.keys(cache.results).length });
}

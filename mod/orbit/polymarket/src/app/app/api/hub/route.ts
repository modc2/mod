// /polymarket/api/hub — the console's window onto the background worker.
//
//   GET  ?days=1        the worker's cached backtests for that window + its
//                       schedule (last pass, next pass, strats covered) and
//                       the fetch loop's feed-cache coverage
//   POST {days, strats} publish the roster the worker should keep warm
//   POST {resolve:[id]} how those markets resolved, from the store (no network)
//   POST ?run=1         replay now, out of the cached feeds
//   POST ?refresh=1     top up the feed cache now (the only network work)
//
// Owner-gated with the same token the Rust API issues (see server/ownerToken).
// The strats in the manifest are the user's own saved strategies — params
// only, no keys, no wallet state — but they're still private, and this route
// can start real network work, so it verifies before doing anything.

import { NextResponse } from "next/server";

import { bearer, verifyOwnerToken } from "../../lib/server/ownerToken";
import {
  readCache, readFeedStatus, readManifest, runPass, triggerPass, triggerRefresh,
  workerRunning, writeManifest,
} from "../../lib/server/hubWorker";
import { knownResolutions, resolutionCoverage } from "../../lib/server/resolutionStore";
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
    status: { ...cache.status, running: workerRunning(), feeds: readFeedStatus() },
    results,
  });
}

export async function POST(req: Request) {
  if (!verifyOwnerToken(bearer(req))) return deny();
  const url = new URL(req.url);

  if (url.searchParams.get("refresh") === "1") {
    // Fire-and-forget: a fetch cycle outlives any reasonable request timeout.
    const queued = triggerRefresh();
    return NextResponse.json({ queued, feeds: readFeedStatus() });
  }

  if (url.searchParams.get("run") === "1") {
    const queued = triggerPass();
    return NextResponse.json({ queued, status: readCache().status });
  }

  let body: { days?: number; windows?: number[]; strats?: SavedIndex[]; resolve?: string[] };
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: "bad json" }, { status: 400 });
  }

  // `{resolve: [conditionId, …]}` — what did these markets pay out? The
  // BACKTEST tab asks before replaying, so the browser settles its unsold
  // inventory against the same facts the worker's cards do instead of marking
  // it at the last price a leader printed.
  //
  // Answered from the store ONLY: a console tab must never be able to start
  // hundreds of upstream lookups, and anything the store doesn't know yet the
  // replay reports as MARKED rather than pretending. The worker's own passes
  // fill the store in the background.
  if (Array.isArray(body.resolve)) {
    const legs = knownResolutions(body.resolve.filter((c) => typeof c === "string"));
    return NextResponse.json({
      resolved: Object.fromEntries(legs),
      coverage: resolutionCoverage(),
    });
  }
  const strats = Array.isArray(body.strats) ? body.strats : [];
  const prev = readManifest();
  const days = Number(body.days) > 0 ? Number(body.days) : prev.days;
  // The worker adds its own mandatory window; whatever arrives here is the
  // console asking for EXTRA coverage, so an old client that sends only `days`
  // still gets the 1-day pass.
  const windows = Array.isArray(body.windows)
    ? body.windows.map(Number).filter((d) => Number.isFinite(d) && d > 0)
    : [days];
  writeManifest({ days, windows, strats, at: Date.now() });

  // A new manifest can name traders the store has never seen; get them
  // fetching now rather than at the next cycle. The pass itself replays out
  // of the cache, so it's safe to run alongside.
  triggerRefresh();
  // First manifest ever (or first after a wipe) — no point making the user
  // wait a full cycle for numbers that could start now.
  const cache = readCache();
  if (!cache.status.at) triggerPass();

  return NextResponse.json({
    ok: true, strats: strats.length, days,
    status: { ...readCache().status, feeds: readFeedStatus() },
  });
}

/** Owner-triggered synchronous pass — used by the MCP tool, which wants the
    result rather than a "queued". */
export async function PUT(req: Request) {
  if (!verifyOwnerToken(bearer(req))) return deny();
  const cache = await runPass();
  return NextResponse.json({ status: cache.status, count: Object.keys(cache.results).length });
}

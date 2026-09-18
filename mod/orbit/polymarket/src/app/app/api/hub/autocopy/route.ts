// /polymarket/api/hub/autocopy — the AUTO COPY board's settings + results.
//
//   GET                       current settings, last pass status, one card per
//                             top-PnL trader (train window vs test window)
//   POST {trainDays, testDays, count, enabled}
//                             update the settings. Values are clamped into a
//                             valid non-overlapping split (both ≥ 1 day,
//                             train + test ≤ 30 — the feed's own ceiling) and
//                             the CLAMPED settings are returned, so the UI
//                             shows what will actually run. A changed split
//                             wipes stale cards and queues a fresh pass.
//   POST ?run=1               replay now, out of the cached feeds
//
// Owner-gated like /api/hub — same token, same reasoning.

import { NextResponse } from "next/server";

import { bearer, verifyOwnerToken } from "../../../lib/server/ownerToken";
import {
  readAutoCopySettings, readAutoCopyState, writeAutoCopySettings,
  type AutoCopySettings,
} from "../../../lib/server/autoCopy";
import { triggerPass, triggerRefresh, workerRunning } from "../../../lib/server/hubWorker";
import { MAX_LOOKBACK_DAYS } from "../../../lib/polymarket";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

function deny() {
  return NextResponse.json(
    { error: "unauthorized", gate: "polymarket-access" },
    { status: 401 },
  );
}

function snapshot() {
  const state = readAutoCopyState();
  return {
    settings: readAutoCopySettings(),
    maxLookbackDays: MAX_LOOKBACK_DAYS,
    status: { ...state.status, running: state.status.running || workerRunning() },
    // Rank order — the board renders this list as-is.
    cards: Object.values(state.results).sort((a, b) => a.rank - b.rank),
  };
}

export async function GET(req: Request) {
  if (!verifyOwnerToken(bearer(req))) return deny();
  return NextResponse.json(snapshot());
}

export async function POST(req: Request) {
  if (!verifyOwnerToken(bearer(req))) return deny();
  const url = new URL(req.url);

  if (url.searchParams.get("run") === "1") {
    // The board replays inside the ordinary worker pass — one queue, one lock.
    const queued = triggerPass();
    return NextResponse.json({ queued, ...snapshot() });
  }

  let body: Partial<AutoCopySettings>;
  try {
    body = (await req.json()) as Partial<AutoCopySettings>;
  } catch {
    return NextResponse.json({ error: "bad json" }, { status: 400 });
  }

  const prev = readAutoCopySettings();
  const saved = writeAutoCopySettings({ ...prev, ...body });
  const changed =
    saved.trainDays !== prev.trainDays || saved.testDays !== prev.testDays ||
    saved.count !== prev.count || saved.enabled !== prev.enabled;
  if (changed && saved.enabled) {
    // New roster/windows may name traders the store has never seen — start
    // fetching now, and let the next pass (also queued) fill the cards in.
    triggerRefresh();
    triggerPass();
  }
  return NextResponse.json({ ok: true, ...snapshot() });
}

// /polymarket/_api/hub/autocopy — the AUTO COPY board's settings + results.
//
//   GET                       current settings, last pass status, one card per
//                             top-PnL trader (train window vs test window)
//   POST {trainDays, testDays, count, enabled,
//         rankBy, minHistoryDays, minTrades, minConsistency}
//                             update the settings. Windows are clamped into a
//                             valid non-overlapping split (both ≥ 1 day,
//                             train + test ≤ 30 — the feed's own ceiling);
//                             the vetting floors decide which traders enter
//                             the test at all (history/trades/consistency,
//                             0 = off) and rankBy picks the leaderboard the
//                             roster comes off ("steady" | "pnl"). The
//                             CLAMPED settings are returned, so the UI shows
//                             what will actually run. Any change wipes stale
//                             cards and queues a fresh pass.
//   POST ?run=1               replay now, out of the cached feeds
//
// Owner-gated like /_api/hub — same token, same reasoning.

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
  // Any field change re-queues: windows and roster size relabel the cards,
  // and the vetting floors / rank change WHICH traders get tested at all.
  const changed = JSON.stringify(saved) !== JSON.stringify(prev);
  if (changed && saved.enabled) {
    // New roster/windows may name traders the store has never seen — start
    // fetching now, and let the next pass (also queued) fill the cards in.
    triggerRefresh();
    triggerPass();
  }
  return NextResponse.json({ ok: true, ...snapshot() });
}

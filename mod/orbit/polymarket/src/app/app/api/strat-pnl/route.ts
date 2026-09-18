// /polymarket/api/strat-pnl — the strat cards' 7-day PnL curves.
//
//   GET ?days=7   stratId → [t, pnl] points from the sidecar's history file
//                 (lib/server/stratPnl.ts samples it every 10 minutes).
//
// Owner-gated like /api/autostrat: the series is the owner's money, and this
// is a single-owner console.

import { NextResponse } from "next/server";

import { readStratPnlSeries, sampleStratPnl } from "../../lib/server/stratPnl";
import { bearer, verifyOwnerToken } from "../../lib/server/ownerToken";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export async function GET(req: Request) {
  if (!verifyOwnerToken(bearer(req))) {
    return NextResponse.json({ error: "unauthorized", gate: "polymarket-access" }, { status: 401 });
  }
  const url = new URL(req.url);
  const days = Math.min(Math.max(Number(url.searchParams.get("days")) || 7, 1), 30);
  const out = readStratPnlSeries(days);
  // An empty file means the sidecar hasn't sampled since deploy — kick one off
  // so the cards fill in on the next poll instead of the next 10-min tick.
  if (Object.keys(out.series).length === 0) void sampleStratPnl();
  return NextResponse.json(out);
}

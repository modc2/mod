// /polymarket/api/basket — replay a SET of traders, each on its own money.
//
// The console answers this in the browser (components/BasketSim.tsx), which is
// the right place for a screen you're actively splitting money on. This route
// is the OTHER front end: an agent over MCP (`pm_copy_basket`) asks the same
// question and gets the same numbers, because both call lib/basketSim.ts.
//
// It replays out of the worker's on-disk feed store and resolution store —
// the same bytes the hub's cards are computed from — so a call costs CPU, not
// a burst of paginated /activity walks. A leader nobody has fetched yet comes
// back as a sleeve with `warming: true` and a reason, never as a $0 that reads
// like breaking even.
//
// Owner-gated with the token the Rust API issues, same as /api/hub.

import { NextResponse } from "next/server";

import { bearer, mintOwnerToken, verifyOwnerToken } from "../../lib/server/ownerToken";
import { feedSession } from "../../lib/server/feedFetcher";
import { knownResolutions } from "../../lib/server/resolutionStore";
import { fetchTraderBankrolls } from "../../lib/liveSessions";
import { API_BASE, serverAuthHeaders, setServerAuthToken } from "../../lib/polymarket";
import { thinCurve, type TraderFeed } from "../../lib/hubReplay";
import {
  basketTotal, compareToEqualSplit, equalSplit, runBasketSim, sleeveFloor, weightedSplit,
  type BasketFeeds, type BasketLeg,
} from "../../lib/basketSim";
import type { PolymarketPosition, PolymarketTrade } from "../../lib/types";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";
/** A ten-leg basket with floors and a ladder is a lot of replays. */
export const maxDuration = 300;

const ADDR_RE = /^0x[0-9a-fA-F]{40}$/;
/** Ceiling on how many names one call may replay. A basket is a shortlist —
    anything past this is a screen, and the leaderboard is the tool for that. */
const MAX_LEGS = 20;

function deny() {
  return NextResponse.json({ error: "unauthorized", gate: "polymarket-access" }, { status: 401 });
}

interface Body {
  legs?: { address?: string; allocationUsd?: number; label?: string; enabled?: boolean; params?: unknown }[];
  /** Seed the roster from the copy desk instead of naming legs. */
  fromDesk?: boolean;
  /** Rescale whatever roster we end up with to this total, proportions kept. */
  total?: number;
  /** …or ignore the amounts entirely and divide `total` evenly. */
  split?: "equal" | "weighted";
  days?: number;
  /** Also replay the same total divided evenly, and report the edge. */
  compare?: boolean;
  /** Also find the smallest amount at which each leg trades at all. */
  floors?: boolean;
  /** Also replay the whole split at a range of totals. */
  ladder?: number[];
}

function parseLegs(raw: Body["legs"]): BasketLeg[] {
  const seen = new Set<string>();
  const out: BasketLeg[] = [];
  for (const l of raw ?? []) {
    const address = String(l?.address ?? "").trim().toLowerCase();
    if (!ADDR_RE.test(address) || seen.has(address)) continue;
    seen.add(address);
    out.push({
      address,
      allocationUsd: Number.isFinite(l?.allocationUsd) ? Math.max(0, Number(l!.allocationUsd)) : 0,
      label: typeof l?.label === "string" ? l.label : null,
      enabled: l?.enabled !== false,
      ...(l?.params && typeof l.params === "object" ? { params: l.params as BasketLeg["params"] } : {}),
    });
  }
  return out.slice(0, MAX_LEGS);
}

/** The copy desk, as a basket. Server-side read so an agent can say "replay
    what I'm actually copying" without restating the roster. */
async function deskLegs(): Promise<BasketLeg[]> {
  const res = await fetch(`${API_BASE}/copy/book`, {
    headers: { "Content-Type": "application/json", ...serverAuthHeaders() },
  });
  if (!res.ok) return [];
  const book = (await res.json()) as {
    allocations?: { address: string; label?: string | null; allocationUsd: number; enabled: boolean; params?: unknown }[];
  };
  return parseLegs(book.allocations ?? []);
}

export async function POST(req: Request) {
  if (!verifyOwnerToken(bearer(req))) return deny();

  // The API is owner-gated and there is no fetch patch in Node: mint the same
  // token the worker uses so the copy-book read and the bankroll lookup below
  // are authorized even when the background worker has never run (its
  // `authenticate` is what otherwise sets this, and it can be disabled).
  const minted = mintOwnerToken();
  if (minted) setServerAuthToken(minted);

  let body: Body;
  try {
    body = (await req.json()) as Body;
  } catch {
    return NextResponse.json({ error: "bad json" }, { status: 400 });
  }

  const days = Math.max(1, Math.min(30, Number(body.days) || 7));
  let legs = body.fromDesk ? await deskLegs() : parseLegs(body.legs);
  if (legs.length === 0) {
    return NextResponse.json({
      error: body.fromDesk
        ? "the copy desk is empty — there is nothing to replay"
        : "no legs — pass legs:[{address, allocationUsd}] or fromDesk:true",
    }, { status: 400 });
  }

  // Sizing, if the caller wants us to do it. `total` with no split rescales
  // the amounts they gave (conviction kept); `split:"equal"` overrides them.
  if (body.total && body.total > 0) {
    legs = body.split === "equal" ? equalSplit(legs, body.total) : weightedSplit(legs, body.total);
  } else if (body.split === "equal") {
    legs = equalSplit(legs, basketTotal(legs));
  }

  // ── Feeds, out of the worker's stores ──
  const session = feedSession();
  const cache = new Map<string, Promise<TraderFeed>>();
  const trades = new Map<string, PolymarketTrade[]>();
  const positions = new Map<string, PolymarketPosition[]>();
  const active = legs.filter((l) => l.enabled !== false && l.allocationUsd > 0);
  for (const leg of active) {
    const feed = await session.load(leg.address, cache);
    trades.set(leg.address, feed.trades);
    positions.set(leg.address, feed.positions);
  }
  const bankrolls = await fetchTraderBankrolls(active.map((l) => l.address))
    .catch(() => new Map<string, number>());

  // Resolutions from the store only — a route a client can call must never
  // start hundreds of upstream lookups. Anything unknown is reported as
  // MARKED in `settled`, which is what `confidence` below is for.
  const cutoff = Date.now() - days * 86400_000;
  const touched = new Set<string>();
  for (const feed of trades.values()) {
    for (const t of feed) if (t.timestamp >= cutoff && t.conditionId) touched.add(t.conditionId);
  }
  const resolved = knownResolutions(touched);

  const feeds: BasketFeeds = { trades, positions, bankrolls, resolved };
  const opts = { days };

  const run = runBasketSim(legs, feeds, opts);
  const p = run.portfolio;

  // Leaders the feed store has never fetched. Their sleeves replay empty, and
  // that is a fact about the cache, not about the trader — so it is reported
  // separately from every "this leg was gated" note.
  const noHistory = active
    .filter((l) => (trades.get(l.address) ?? []).length === 0)
    .map((l) => l.address);

  return NextResponse.json({
    days,
    total: basketTotal(legs),
    portfolio: {
      capital: p.capital,
      net: p.net,
      roi: Math.round(p.pct * 100) / 100,
      endEquity: p.endEquity,
      trades: p.trades,
      copied: p.executed,
      observed: p.observed,
      volume: p.volume,
      drawdown: Math.round(p.drawdown * 100) / 100,
      // The two numbers that separate a basket from a list of backtests.
      legsTrading: p.legsTrading,
      legs: p.legs,
      idleUsd: p.idleUsd,
      // How much of this is a settled market rather than a last-observed mark.
      confidence: Math.round(p.confidence * 100) / 100,
      curve: thinCurve(p.equity),
    },
    sleeves: run.sleeves.map((s) => ({
      address: s.address,
      label: s.label,
      allocationUsd: s.allocationUsd,
      share: Math.round(s.weight * 1000) / 10,
      net: s.net,
      roi: Math.round(s.pct * 100) / 100,
      trades: s.trades,
      copied: s.executed,
      observed: s.observed,
      ratio: s.ratio,
      confidence: Math.round(s.confidence * 100) / 100,
      // Present ⇒ this leg placed nothing, and this is which gate refused it.
      note: s.note,
      warming: noHistory.includes(s.address) || undefined,
    })),
    // Two names on the same market are two real positions — the desk would
    // place both orders, so a basket is less diversified than its name count.
    overlap: { markets: run.overlap.markets, legs: run.overlap.legsPaired },
    /** Herfindahl over the legs' P&L: 1 = one name made all of it. */
    concentration: Math.round(run.concentration * 100) / 100,
    ...(body.compare
      ? { comparison: compareToEqualSplit(legs, feeds, opts, p.net) }
      : {}),
    ...(body.floors
      ? {
          floors: Object.fromEntries(
            active.map((l) => [l.address, sleeveFloor(l, feeds, opts)]),
          ),
        }
      : {}),
    ...(Array.isArray(body.ladder) && body.ladder.length > 0
      ? {
          ladder: body.ladder
            .filter((t) => Number.isFinite(t) && t > 0)
            .slice(0, 10)
            .map((t) => {
              const r = runBasketSim(weightedSplit(legs, t), feeds, opts);
              return {
                total: t,
                net: r.portfolio.net,
                roi: Math.round(r.portfolio.pct * 100) / 100,
                legsTrading: r.portfolio.legsTrading,
                copied: r.portfolio.executed,
              };
            }),
        }
      : {}),
    feeds: {
      // `noHistory` is the addresses; `warming` below is the session's own
      // count of feeds it wanted and didn't have. Different things, so they
      // get different names rather than one key that means both.
      noHistory,
      ...session.stats,
    },
    note: noHistory.length > 0
      ? `${noHistory.length} leader(s) have no cached history yet — their sleeves replayed empty. `
        + "The fetch loop picks them up on its next cycle; ask again in a few minutes."
      : undefined,
  });
}

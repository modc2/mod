// Built-in DEFAULT STRATS — curated starting points shown in the header
// strat picker. They are not stored anywhere: each is a recipe (risk params
// + trade filters + a leaderboard seeding rule), and "forking" one just
// materializes a fresh SavedIndex owned by the user, then asynchronously
// seeds it with the CURRENT top traders matching the recipe. Trader lists
// are resolved at fork time on purpose — a hardcoded address list would rot
// within weeks, while "top 10 crypto traders over the last 7 days" stays
// fresh forever.
//
// Deliberately NO sub-hour "Up or Down" COPY template: copying HFT bots on
// 5-min markets with a mirror lag is a structural loss (see the movoaev8
// postmortem). The crypto template leans on a conviction notional floor to
// bias away from that flow. BTC 5-MIN DELTA is the one deliberate exception
// and it does NOT copy anyone — it originates from the live candle's own
// near-live price tape (momentum.candles), with tiny sizing and a single
// position slot precisely because that lane is still HFT turf.
//
// FILTER is the one template whose watchlist is deliberately WIDE (20): its
// point is that the strat picks the roster itself, every scan, from the
// traders' own realized returns — see `TraderFilter`.
//
// Templates WITHOUT an explicit price band (sports/politics/weather) inherit
// the engine-wide likely-to-win default: BUYs below DEFAULT_MIN_ENTRY_PRICE
// (60¢) are not mirrored. LONGSHOT HUNTER's explicit 2–20¢ band is the
// deliberate opt-out for users who want that exposure.

import { SavedIndex } from "./types";
import {
  equalWeightTraders,
  saveIndex,
  setActiveIndexId,
  uniqueIndexName,
  updateIndex,
} from "./indexStore";
import { fetchTopTraderAddresses } from "./polymarket";

export interface StratTemplate {
  slug: string;
  name: string;
  /** One-liner shown under the name in the picker gallery. */
  description: string;
  /** Leaderboard query used to seed traders at fork time. */
  seed: {
    days?: number;
    minPerDay?: number;
    category?: string;
    marketQuery?: string;
    count?: number;
  };
  /** Strat fields layered over the blank-strat defaults. */
  params: Partial<SavedIndex>;
}

export const DEFAULT_STRATS: StratTemplate[] = [
  {
    slug: "filter",
    name: "FILTER",
    description:
      "Watches 20 traders, copies only the 5 with the best score right now — re-ranked every scan on their own realized returns, so a leader who starts bleeding is dropped automatically.",
    // A wide net on purpose: the filter is what makes the roster small, and
    // it can only pick from who it can see. 20 in, 5 copied.
    seed: { days: 14, count: 20 },
    params: {
      filter: {
        metric: "score",
        topN: 5,
        // Positive expected edge required — a trader who is top-5 on a
        // watchlist where everyone is losing still isn't worth copying.
        minScore: 0,
        // 3 closed trades is where the stats stop being noise (see
        // TraderRoiStats.sampleSize).
        minSamples: 3,
      },
      tradeFilters: { sides: "buy", minPrice: 0.6 },
    },
  },
  {
    slug: "top-allstars",
    name: "TOP 10 ALL-STARS",
    description:
      "Equal-weight the 10 best PnL traders of the last 7 days. Likely winners only (≥60¢) — skips the longshot / 5-min bot flow that a mirror lag can't win.",
    seed: { days: 7, count: 10 },
    // Favorite-side floor: never copy a sub-60¢ longshot buy. This is the
    // single most damaging leak on an unfiltered "all markets" strat — the
    // leader's 7–38¢ 5-min "Up or Down" bot buys, mirrored late, decay
    // straight to -100% (see the movoaev8 postmortem). Matches the engine's
    // DEFAULT_MIN_ENTRY_PRICE, kept explicit here so the picker shows it.
    params: {
      tradeFilters: { sides: "buy", minPrice: 0.6 },
    },
  },
  {
    slug: "conservative-favorites",
    name: "CONSERVATIVE FAVORITES",
    description: "Copy only 65–95¢ favorite buys with ≥$50 conviction. Small, slow, steady.",
    seed: { days: 14, count: 10 },
    params: {
      maxTrade: 25,
      maxPerCycle: 1,
      maxOpenPositions: 5,
      tradeFilters: { sides: "buy", minPrice: 0.65, maxPrice: 0.95, minNotional: 50 },
    },
  },
  {
    slug: "longshot-hunter",
    name: "LONGSHOT HUNTER",
    description: "2–20¢ longshot buys only, tiny sizing — high variance, capped downside.",
    seed: { days: 30, count: 10 },
    params: {
      maxTrade: 10,
      maxPerCycle: 2,
      tradeFilters: { sides: "buy", minPrice: 0.02, maxPrice: 0.2 },
    },
  },
  {
    slug: "crypto-majors",
    name: "CRYPTO MAJORS",
    description: "Top crypto traders, crypto markets only; $100+ conviction trades to skip Up/Down bot noise.",
    seed: { days: 7, category: "crypto", count: 10 },
    params: {
      tradeFilters: { sides: "buy", categories: ["crypto"], minNotional: 100, minPrice: 0.1, maxPrice: 0.9 },
    },
  },
  {
    slug: "sports-sharps",
    name: "SPORTS SHARPS",
    description: "Top sports bettors, sports markets only.",
    seed: { days: 7, category: "sports", count: 10 },
    params: {
      tradeFilters: { categories: ["sports"] },
    },
  },
  {
    slug: "politics-desk",
    name: "POLITICS DESK",
    description: "Top politics traders over 14 days, politics markets only.",
    seed: { days: 14, category: "politics", count: 10 },
    params: {
      tradeFilters: { categories: ["politics"] },
    },
  },
  {
    slug: "btc-momentum",
    name: "BTC MOMENTUM",
    description:
      "No copying — watches Bitcoin markets' own odds and buys the side that's rising (50¢→60¢ = ride it), sells when the move flips. Skips sub-90-min markets.",
    // Origination-only: no watchlist to seed. The strat trades straight
    // from CLOB price history, so it runs with zero traders enrolled.
    seed: { count: 0 },
    params: {
      marketQuery: "bitcoin",
      maxPerCycle: 2,
      maxTrade: 25,
      momentum: {
        lookbackMinutes: 60,
        minRiseCents: 5,
        maxPositions: 5,
      },
    },
  },
  {
    slug: "btc-5min-delta",
    name: "BTC 5-MIN DELTA",
    description:
      "Watches the LIVE Bitcoin 5-minute Up/Down candle: buys the ≥60¢ side while its odds are still climbing (+5¢ over 2m), sells the moment the delta flips −5¢, winners auto-redeem. Tiny fixed sizes — this lane is HFT turf.",
    // Origination-only: trades the candle's own price tape, zero traders.
    seed: { count: 0 },
    params: {
      capital: 100,
      maxPerCycle: 1,
      minTrade: 1,
      maxTrade: 10,
      // Wants 15s cycles — a 5-minute candle only lives ~20 of them and the
      // exit flip has to be seen with time left to act on it. The engine's
      // 30s rate-limit floor (MIN_POLL_MINUTES) clamps it up, so this strat
      // actually sees ~10 cycles per candle.
      livePollMinutes: 0.25,
      momentum: {
        candles: { slugPrefix: "btc-updown-5m", periodMinutes: 5 },
        // The user's rule, verbatim: ≥60¢ side, ±5¢ delta band.
        lookbackMinutes: 2,
        minRiseCents: 5,
        exitDropCents: 5,
        minPrice: 0.6,
        maxPrice: 0.9,
        maxPositions: 1,
        // Entries need ≥1 minute of candle left; the default 90 would veto
        // every sub-hour entry outright.
        minMinutesToClose: 1,
      },
    },
  },
  {
    slug: "weather-edge",
    name: "WEATHER EDGE",
    description: "Traders active on temperature markets — a niche where models beat vibes.",
    seed: { days: 14, marketQuery: "temperature", count: 10 },
    params: {
      marketQuery: "temperature",
    },
  },
];

/// Leaderboard rosters are cached briefly: the strat browser resolves one per
/// template to backtest it, and a fork made moments later must get the SAME
/// traders — otherwise the card's number describes a strat you didn't get.
const ROSTER_PREFIX = "poly_tpl_roster_";
const ROSTER_TTL_MS = 3 * 3600_000;

/// The traders a template would seed itself with right now.
export async function templateRoster(t: StratTemplate): Promise<string[]> {
  const count = t.seed.count ?? 10;
  if (count === 0) return [];
  const key = ROSTER_PREFIX + t.slug;
  if (typeof window !== "undefined") {
    try {
      const raw = localStorage.getItem(key);
      if (raw) {
        const c = JSON.parse(raw) as { addrs: string[]; ts: number };
        if (Date.now() - c.ts < ROSTER_TTL_MS && c.addrs?.length > 0) return c.addrs;
      }
    } catch {
      // Unreadable entry — fall through and re-query.
    }
  }
  const addrs = await fetchTopTraderAddresses(
    {
      days: t.seed.days,
      minPerDay: t.seed.minPerDay,
      category: t.seed.category,
      marketQuery: t.seed.marketQuery,
    },
    count,
  );
  if (typeof window !== "undefined" && addrs.length > 0) {
    try {
      localStorage.setItem(key, JSON.stringify({ addrs, ts: Date.now() }));
    } catch {
      // Quota — a re-query is cheap enough.
    }
  }
  return addrs;
}

/// A template as the SavedIndex forking it produces — the recipe's params
/// layered over the blank-strat defaults, with a resolved watchlist.
///
/// Both callers go through here: `forkDefaultStrat` (which then persists it)
/// and the strat browser's backtest (`lib/stratScan.ts`, which never persists
/// anything). That's deliberate — a template's card has to describe the strat
/// you'd actually get, so the two can't be allowed to define it differently.
export function templateIndex(t: StratTemplate, addrs: string[] = [], now = Date.now()): SavedIndex {
  return {
    id: now.toString(36),
    name: uniqueIndexName(t.name),
    forkedFrom: t.slug,
    traders: addrs.length > 0 ? equalWeightTraders(addrs) : [],
    backtestDays: 7,
    rebalanceMinutes: 0.5,
    livePollMinutes: 0.5,
    capital: 1000,
    minTrade: 1,
    maxTrade: 100,
    maxPerCycle: 3,
    ...t.params,
    createdAt: now,
    updatedAt: now,
  };
}

/// Fork a template into a real, user-owned strat and make it active.
/// Returns the new strat immediately (empty trader list); trader seeding
/// runs async and reports back through `onSeeded` so callers can re-render
/// and push the seeded version to the server.
export function forkDefaultStrat(
  t: StratTemplate,
  onSeeded?: (seeded: SavedIndex) => void,
): SavedIndex {
  const idx = templateIndex(t);
  saveIndex(idx);
  setActiveIndexId(idx.id);

  // Origination-only templates (seed.count: 0) trade from market data and
  // deliberately start with an empty watchlist — nothing to seed.
  if ((t.seed.count ?? 10) === 0) return idx;

  templateRoster(t).then((addrs) => {
    if (addrs.length === 0) return;
    const traders = equalWeightTraders(addrs);
    updateIndex(idx.id, { traders, updatedAt: Date.now() });
    onSeeded?.({ ...idx, traders });
  });

  return idx;
}

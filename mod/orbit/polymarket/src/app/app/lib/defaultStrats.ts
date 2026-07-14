// Built-in DEFAULT STRATS — curated starting points shown in the header
// strat picker. They are not stored anywhere: each is a recipe (risk params
// + trade filters + a leaderboard seeding rule), and "forking" one just
// materializes a fresh SavedIndex owned by the user, then asynchronously
// seeds it with the CURRENT top traders matching the recipe. Trader lists
// are resolved at fork time on purpose — a hardcoded address list would rot
// within weeks, while "top 10 crypto traders over the last 7 days" stays
// fresh forever.
//
// Deliberately NO sub-hour "Up or Down" template: copying HFT bots on 5-min
// markets with a ~60s mirror lag is a structural loss (see the movoaev8
// postmortem). The crypto template leans on a conviction notional floor to
// bias away from that flow.

import { SavedIndex } from "./types";
import {
  equalWeightTraders,
  loadIndexes,
  saveIndex,
  setActiveIndexId,
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
    slug: "top-allstars",
    name: "TOP 10 ALL-STARS",
    description: "Equal-weight the 10 best PnL traders of the last 7 days, all markets.",
    seed: { days: 7, count: 10 },
    params: {},
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
    slug: "weather-edge",
    name: "WEATHER EDGE",
    description: "Traders active on temperature markets — a niche where models beat vibes.",
    seed: { days: 14, marketQuery: "temperature", count: 10 },
    params: {
      marketQuery: "temperature",
    },
  },
];

/** "CRYPTO MAJORS" → "CRYPTO MAJORS 2" when the name is already taken. */
function uniqueName(base: string): string {
  const names = new Set(loadIndexes().map((i) => i.name));
  if (!names.has(base)) return base;
  for (let n = 2; n < 1000; n++) {
    const cand = `${base} ${n}`;
    if (!names.has(cand)) return cand;
  }
  return `${base} ${Date.now().toString(36)}`;
}

/// Fork a template into a real, user-owned strat and make it active.
/// Returns the new strat immediately (empty trader list); trader seeding
/// runs async and reports back through `onSeeded` so callers can re-render
/// and push the seeded version to the server.
export function forkDefaultStrat(
  t: StratTemplate,
  onSeeded?: (seeded: SavedIndex) => void,
): SavedIndex {
  const now = Date.now();
  const idx: SavedIndex = {
    id: now.toString(36),
    name: uniqueName(t.name),
    traders: [],
    backtestDays: 7,
    rebalanceMinutes: 1,
    livePollMinutes: 1,
    capital: 1000,
    minTrade: 1,
    maxTrade: 100,
    maxTradesPerHour: 10,
    maxPerCycle: 3,
    ...t.params,
    createdAt: now,
    updatedAt: now,
  };
  saveIndex(idx);
  setActiveIndexId(idx.id);

  fetchTopTraderAddresses(
    {
      days: t.seed.days,
      minPerDay: t.seed.minPerDay,
      category: t.seed.category,
      marketQuery: t.seed.marketQuery,
    },
    t.seed.count ?? 10,
  ).then((addrs) => {
    if (addrs.length === 0) return;
    const traders = equalWeightTraders(addrs);
    updateIndex(idx.id, { traders, updatedAt: Date.now() });
    onSeeded?.({ ...idx, traders });
  });

  return idx;
}

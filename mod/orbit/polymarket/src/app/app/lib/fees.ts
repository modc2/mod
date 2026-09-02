// ── The cost model — what a trade actually costs, in dollars ──
//
// This file exists because every sim in this console used to book ZERO
// friction: `TAKER_FEE_BPS = 0`, `GAS_PER_TRADE_USD = 0`, and so the FEES row
// under every backtest read "$0.00". That was true once. It is not true now:
// Polymarket charges a taker fee on most categories, and the fee is largest
// exactly in the 40–60¢ band where this module measured most of its losses.
//
// Two costs, two very different sizes:
//
//   PLATFORM FEE — charged by the matcher at match time, taker side only:
//
//       fee = rate x p x (1 - p) x shares            (docs.polymarket.com/…/fees)
//
//     `rate` is per-category (crypto 7%, sports 5%, politics 4%, geopolitics
//     free). The dollar fee peaks at 50¢ and is symmetric — a fill at 30¢ and
//     one at 70¢ pay the same. On a crypto market at 50¢ that is 1.75% of
//     notional PER SIDE, i.e. 7% round trip. Nothing about that is noise.
//
//   GAS — Polygon gas, paid by whoever submits the transaction. Almost never
//     the trader: CLOB fills are matched on-chain by Polymarket's operator and
//     redeems/withdrawals go through its relayer, all of which are paid for by
//     Polymarket. What the user DOES pay for is deploying their proxy wallet
//     once and funding it. So gas here is a small real number (cents), priced
//     off the live Polygon base fee and the live POL price rather than
//     invented — see `fetchGasQuote`. Pretending it is zero and pretending it
//     is $0.50/trade are both lies; this prices it.
//
// The rate for a given market is resolved in three ways, best first:
//
//   1. OBSERVED, in this market. The data-api's `usdcSize` is the USDC that
//      actually moved, which is `price x size` PLUS the fee on a BUY and MINUS
//      it on a SELL. Divide the difference by `p(1-p)shares` and the market's
//      own rate falls out, exactly, from data the console already fetched.
//   2. OBSERVED, in this market's CATEGORY. Some other market of the same kind
//      in the same feeds charged a fee, so this one charges it too.
//   3. THE PUBLISHED TABLE for the category, when nothing was measurable.
//   4. DEFAULT. 5% — the published "Other / General" rate.
//
// A zero measurement is never taken as "this market is free", and that
// distinction is the whole reason for step 2. MAKERS ARE NEVER CHARGED, so
// every fill of a market-making leader measures 0 — and a copy engine chasing
// that leader with a marketable order is a TAKER on every one of them. A
// leader with 151 maker fills in weather markets would otherwise have booked
// a free replay for a copier who would really have paid 5% of every 50c fill.
// The only genuinely free markets are the fee-free CATEGORY (geopolitics), and
// the table already says so.

import type { PolymarketTrade } from "./types";

// ── Published fee schedule ─────────────────────────────────────────────
// docs.polymarket.com/polymarket-learn/trading/fees, read 2026-09-01.
// Maker rate is 0 in every category — makers are never charged.

export type FeeCategory =
  | "crypto" | "sports" | "finance" | "politics" | "economics"
  | "culture" | "weather" | "mentions" | "tech" | "geopolitics" | "other";

export const TAKER_FEE_RATE: Record<FeeCategory, number> = {
  crypto: 0.07,
  sports: 0.05,
  finance: 0.04,
  politics: 0.04,
  economics: 0.05,
  culture: 0.05,
  weather: 0.05,
  mentions: 0.04,
  tech: 0.04,
  geopolitics: 0,   // Polymarket takes nothing on world-events markets
  other: 0.05,
};

/** Maker rebate as a fraction of the taker fee, per category — what the
    market maker on the other side gets back. Informational: this console is
    a taker, so it pays fees and never collects these. */
export const MAKER_REBATE_RATE: Record<FeeCategory, number> = {
  crypto: 0.20, sports: 0.15, finance: 0.25, politics: 0.25, economics: 0.25,
  culture: 0.25, weather: 0.25, mentions: 0.25, tech: 0.25, geopolitics: 0,
  other: 0.25,
};

/** Weighted-volume multiplier per category in the Taker Rebate Program —
    `wV = size x (1 - price) x weight`. Drives `rebateTierFor`. */
export const WV_CATEGORY_WEIGHT: Record<FeeCategory, number> = {
  sports: 1.0,
  politics: 1.3, finance: 1.3, mentions: 1.3, tech: 1.3,
  economics: 1.7, culture: 1.7, weather: 1.7, other: 1.7,
  crypto: 2.3,
  geopolitics: 0,
};

export const DEFAULT_TAKER_RATE = TAKER_FEE_RATE.other;
/** Rates the matcher actually publishes — an observed rate is snapped to the
    nearest of these when it lands within `RATE_SNAP_TOLERANCE`. */
export const KNOWN_RATES = [0, 0.03, 0.04, 0.05, 0.07];
const RATE_SNAP_TOLERANCE = 0.004;
/** Below this, a "measured" rate is rounding noise, not a fee. `usdcSize` is
    reported to 6dp, and near the boundaries the denominator `p(1-p)shares` is
    small enough that a sub-cent discrepancy divides out to a fraction of a
    percent. Taking the max across a market's fills makes that noise the
    winner: one 99.9¢ fill off by $0.00001 used to elect a 0.1% "rate", which
    then snapped to 0 and declared the whole market free. The published ladder
    starts at 3%, so anything under half a percent is not a rate. */
export const MIN_MEANINGFUL_RATE = 0.005;

/** Taker Rebate Program tiers — 30-day weighted volume → % of fees paid back
    daily in pUSD. Not netted into any P&L here (a rebate only applies from
    the moment the tier is reached), reported as "what you'd be earning". */
export const REBATE_TIERS = [
  { tier: 0, name: "NONE",     wv: 0,          rebate: 0 },
  { tier: 1, name: "BRONZE",   wv: 2_000,      rebate: 0.03 },
  { tier: 2, name: "SILVER",   wv: 20_000,     rebate: 0.08 },
  { tier: 3, name: "GOLD",     wv: 200_000,    rebate: 0.18 },
  { tier: 4, name: "PLATINUM", wv: 1_000_000,  rebate: 0.32 },
  { tier: 5, name: "DIAMOND",  wv: 4_000_000,  rebate: 0.44 },
  { tier: 6, name: "OBSIDIAN", wv: 10_000_000, rebate: 0.50 },
] as const;

export type RebateTier = (typeof REBATE_TIERS)[number];

export function rebateTierFor(weightedVolume: number): RebateTier {
  let hit: RebateTier = REBATE_TIERS[0];
  for (const t of REBATE_TIERS) if (weightedVolume >= t.wv) hit = t;
  return hit;
}

/** Weighted volume one taker fill earns toward the rebate tier. */
export function weightedVolume(notional: number, price: number, category: FeeCategory): number {
  if (!(notional > 0)) return 0;
  const p = Math.min(Math.max(price, 0), 1);
  return notional * (1 - p) * (WV_CATEGORY_WEIGHT[category] ?? 1);
}

// ── The formula ────────────────────────────────────────────────────────

/** `fee = rate x p x (1 - p) x shares`, in USDC. Symmetric around 50¢, zero
    at the boundaries — which is why settling a resolved position (p = 0 or 1)
    costs nothing, and why the worst fee band is exactly a coin flip. */
export function takerFeeUsd(shares: number, price: number, rate: number): number {
  if (!(shares > 0) || !(rate > 0)) return 0;
  const p = Math.min(Math.max(price, 0), 1);
  return rate * p * (1 - p) * shares;
}

/** Same fee, expressed against the dollars going in rather than the shares —
    `shares = notional / price`, so this is `rate x (1 - p) x notional`. */
export function takerFeeOnNotional(notional: number, price: number, rate: number): number {
  if (!(notional > 0) || !(price > 0)) return 0;
  return takerFeeUsd(notional / price, price, rate);
}

/** The fee as a fraction of the notional traded — what "1.75%" means when the
    console says a 50¢ crypto fill costs 1.75%. */
export function feePctOfNotional(price: number, rate: number): number {
  const p = Math.min(Math.max(price, 0), 1);
  if (!(p > 0) || !(rate > 0)) return 0;
  return rate * (1 - p);
}

/** Headroom to reserve on top of a buy's notional so the matcher's fee doesn't
 *  bounce the order for insufficient balance, when the fill price isn't known
 *  yet.
 *
 *  Note the asymmetry that makes this NOT `takerFeeUsd(shares, 0.5, rate)`:
 *  against a fixed number of SHARES the fee peaks at 50¢, but against a fixed
 *  number of DOLLARS it is `rate x (1 - p) x notional`, which grows as the
 *  price falls — $100 buys twenty times more shares at 5¢ than at $1. The bound
 *  as p → 0 is the whole rate, so that is what we reserve. Being short here is
 *  a failed order; being long is a few idle cents. Pass a `price` when you have
 *  one and it prices the fill exactly instead. */
export function feeHeadroomUsd(notional: number, price?: number, rate = DEFAULT_TAKER_RATE): number {
  if (!(notional > 0)) return 0;
  if (price !== undefined && price > 0) return takerFeeOnNotional(notional, price, rate);
  return rate * notional;
}

/** Round-trip cost of entering at `entry` and exiting at `exit`, per dollar of
    entry notional. This is the number that decides whether a copied edge
    survives: a 50¢ crypto entry exiting at 50¢ pays 7% of the position — 3.5%
    in, 3.5% out. The same round trip at 90¢ pays 1.4%. */
export function roundTripFeePct(entry: number, exit: number, rate: number): number {
  if (!(entry > 0)) return 0;
  const shares = 1 / entry;
  return takerFeeUsd(shares, entry, rate) + takerFeeUsd(shares, exit, rate);
}

// ── 1. OBSERVED — read the rate straight off a fill ────────────────────

/** The fee this fill actually paid, from the USDC that actually moved.
    `null` when the row predates `usdcSize` or the numbers don't reconcile. */
export function observedFeeUsd(t: Pick<PolymarketTrade, "side" | "price" | "size" | "usdcSize">): number | null {
  const usdc = t.usdcSize;
  if (!Number.isFinite(usdc as number) || !((usdc as number) > 0)) return null;
  const gross = t.price * t.size;
  if (!(gross > 0)) return null;
  // BUY: fee rides on top of the notional. SELL: it comes out of the proceeds.
  const fee = t.side === "SELL" ? gross - (usdc as number) : (usdc as number) - gross;
  if (!Number.isFinite(fee)) return null;
  // A cent of float noise either way is not a fee, and a "fee" worth more than
  // a fifth of the trade is a mis-parse, not a charge.
  if (fee < -0.01 || fee > gross * 0.2) return null;
  return Math.max(0, fee);
}

/** The market's fee RATE implied by one fill — `fee / (p(1-p)shares)`.
    `null` when the fill can't price it (no `usdcSize`, or a fill so far out at
    the boundary that the denominator is noise). A maker fill returns 0. */
export function observedRate(t: Pick<PolymarketTrade, "side" | "price" | "size" | "usdcSize">): number | null {
  const fee = observedFeeUsd(t);
  if (fee === null) return null;
  const denom = t.price * (1 - t.price) * t.size;
  if (!(denom > 1e-6)) return null;
  const rate = fee / denom;
  if (!Number.isFinite(rate) || rate < 0 || rate > 0.2) return null;
  return rate;
}

/** Snap a measured rate to the published ladder — fills round their USDC to
    6dp, so a 5% market measures as 0.0499…. Anything off the ladder is kept
    as measured rather than forced. */
export function snapRate(rate: number): number {
  let best = rate;
  let bestGap = RATE_SNAP_TOLERANCE;
  for (const r of KNOWN_RATES) {
    const gap = Math.abs(rate - r);
    if (gap <= bestGap) { best = r; bestGap = gap; }
  }
  return best;
}

// ── 2. CATEGORY — infer from the market's name ─────────────────────────
// Ordered: the FIRST list that matches wins, so "Will the U.S. invade Iran"
// lands on geopolitics (fee-free) before "invade" ever reaches politics, and
// "btc-updown-5m" lands on crypto before "up or down" looks like anything
// else. Slug and title are both searched — recurring series carry their whole
// identity in the slug ("mlb-sea-bos-2026-09-01-total-7pt5").

const CATEGORY_KEYWORDS: [FeeCategory, string[]][] = [
  ["crypto", [
    "bitcoin", "btc", "ethereum", "eth-", "ether", "solana", "sol-", "xrp",
    "dogecoin", "doge", "crypto", "altcoin", "memecoin", "stablecoin", "defi",
    "nft", "binance", "coinbase listing", "updown", "up or down", "hyperliquid",
  ]],
  ["geopolitics", [
    "ceasefire", "invade", "invasion", "war ", "at war", "nato", "sanction",
    "hostage", "missile", "airstrike", "troops", "peace deal", "peace plan",
    "nuclear test", "annex", "coup", "regime change", "military strike",
  ]],
  ["economics", [
    "fed ", "federal reserve", "fomc", "interest rate", "rate cut", "rate hike",
    "cpi", "inflation", "unemployment", "jobs report", "gdp", "recession",
    "jerome powell", "basis points", " bps ",
  ]],
  ["finance", [
    "stock", "s&p", "nasdaq", "dow jones", "earnings", "ipo", "market cap",
    "share price", "bankrupt", "acquisition", "merger",
  ]],
  ["sports", [
    "nba", "nfl", "mlb", "nhl", "ncaa", "ufc", "atp", "wta", "epl", "laliga",
    "soccer", "football", "basketball", "baseball", "hockey", "tennis", "golf",
    "boxing", "olympic", "world cup", "champions league", "super bowl",
    "playoff", "grand prix", "formula 1", "f1-", " vs. ", " vs ", "o/u",
    "total-", "moneyline", "spread-", "lol-", "dota", "csgo", "cs2", "valorant",
    "esports", "-win-on-",
    // Daily team markets ("Will Bristol City FC win on 2026-09-01?") carry
    // no sport in the title at all — only the shape does. Both forms are
    // here because ~40% of these rows arrive without a slug.
    " win on ", " fc ", " cf ", "united win", "-vs-",
  ]],
  ["politics", [
    "election", "president", "senate", "congress", "governor", "parliament",
    "prime minister", "nomination", "impeach", "cabinet", "republican",
    "democrat", "primary", "poll", "approval rating", "vote",
  ]],
  ["tech", [
    "openai", "gpt", "claude", "anthropic", "gemini", "llm", "ai model",
    "apple", "google", "microsoft", "tesla", "spacex", "starship", "chip",
  ]],
  ["mentions", ["mention", "say the word", "tweet", "posts about", "how many times"]],
  ["culture", [
    "oscar", "grammy", "emmy", "box office", "album", "movie", "rotten tomatoes",
    "billboard", "netflix", "time person of the year", "nobel",
  ]],
  ["weather", ["hurricane", "temperature", "rainfall", "snowfall", "tornado", "wildfire", "sea ice"]],
];

/** Best-effort category for a market, from its title and slug. */
export function categoryForMarket(title?: string, slug?: string): FeeCategory {
  const hay = `${slug ?? ""} ${title ?? ""}`.toLowerCase();
  if (!hay.trim()) return "other";
  for (const [cat, kws] of CATEGORY_KEYWORDS) {
    for (const kw of kws) if (hay.includes(kw)) return cat;
  }
  return "other";
}

/** Published taker rate for a market we have no fills for. */
export function inferredRate(title?: string, slug?: string): number {
  return TAKER_FEE_RATE[categoryForMarket(title, slug)];
}

// ── The fee book — one resolved rate per market ────────────────────────

export type RateSource = "observed" | "inferred" | "default";

export interface FeeRateInfo {
  rate: number;
  source: RateSource;
  category: FeeCategory;
  /** Fills the rate was measured from (0 when inferred). */
  samples: number;
}

/** Enough of a trade to price it. Anything with these fields works — leader
    feed rows, the user's own fills, a hand-built fixture. */
export type PriceableFill = Pick<PolymarketTrade,
  "conditionId" | "side" | "price" | "size" | "usdcSize"> & { market?: string; slug?: string };

/**
 * conditionId → the taker rate that market charges.
 *
 * Built by walking whatever fills are already in hand. A market that shows a
 * fee on ANY fill charges that fee to takers; a market with several fills and
 * no fee anywhere is genuinely fee-free (geopolitics) rather than unmeasured,
 * so it books 0 instead of falling back to a guess.
 */
export class FeeBook {
  private seen = new Map<string, { maxRate: number; samples: number; paid: number }>();
  private names = new Map<string, { market?: string; slug?: string }>();
  /** category → the highest rate any market of that kind was seen charging in
      these feeds. This is what stops a market-making leader from replaying as
      free: their own fills are all maker fills, but some other weather market
      in the same feeds charged 5%, and the copier is a taker. */
  private byCategory = new Map<FeeCategory, number>();
  /** Markets whose rate the book had to model rather than measure. */
  private modelledHits = new Set<string>();
  /** Markets resolved to a zero rate — the fee-free category. */
  private freeHits = new Set<string>();

  observe(t: PriceableFill): void {
    const id = (t.conditionId || "").toLowerCase();
    if (!id) return;
    const rate = observedRate(t);
    if (rate === null) return;
    const cur = this.seen.get(id) ?? { maxRate: 0, samples: 0, paid: 0 };
    cur.samples++;
    // Noise is not evidence — see MIN_MEANINGFUL_RATE.
    if (rate >= MIN_MEANINGFUL_RATE) {
      if (rate > cur.maxRate) cur.maxRate = rate;
      cur.paid++;
    }
    this.seen.set(id, cur);
    if (!this.names.has(id)) this.names.set(id, { market: t.market, slug: t.slug });
    if (rate >= MIN_MEANINGFUL_RATE) {
      const cat = categoryForMarket(t.market, t.slug);
      const snapped = snapRate(rate);
      if (snapped > (this.byCategory.get(cat) ?? 0)) this.byCategory.set(cat, snapped);
    }
  }

  observeAll(fills: Iterable<PriceableFill>): this {
    for (const t of fills) this.observe(t);
    return this;
  }

  static from(feeds: Iterable<Iterable<PriceableFill>>): FeeBook {
    const book = new FeeBook();
    for (const feed of feeds) book.observeAll(feed);
    return book;
  }

  info(conditionId: string, title?: string, slug?: string): FeeRateInfo {
    const id = (conditionId || "").toLowerCase();
    const hit = this.seen.get(id);
    const known = this.names.get(id);
    const category = categoryForMarket(title ?? known?.market, slug ?? known?.slug);
    const samples = hit?.samples ?? 0;
    // 1. This market charged a fee, and we watched it happen.
    if (hit && hit.maxRate >= MIN_MEANINGFUL_RATE) {
      return { rate: snapRate(hit.maxRate), source: "observed", category, samples };
    }
    // 2. A market of the same kind charged one. Our fills here were maker
    //    fills (or there were none) — the copier's will not be.
    const seenInCategory = this.byCategory.get(category) ?? 0;
    if (seenInCategory > 0) {
      return { rate: seenInCategory, source: "observed", category, samples };
    }
    // 3/4. Nothing measurable anywhere: the published table, then the general
    //      rate. A zero here means the fee-free category, not an empty sample.
    const hay = `${slug ?? known?.slug ?? ""} ${title ?? known?.market ?? ""}`.trim();
    const out: FeeRateInfo = hay
      ? { rate: TAKER_FEE_RATE[category], source: "inferred", category, samples }
      : { rate: DEFAULT_TAKER_RATE, source: "default", category: "other", samples };
    if (out.rate > 0) this.modelledHits.add(id);
    else this.freeHits.add(id);
    return out;
  }

  rateFor(conditionId: string, title?: string, slug?: string): number {
    return this.info(conditionId, title, slug).rate;
  }

  /** What the book knows, for the "where did this number come from?" line.
      Counts markets the ledger actually PRICED, not every market it saw. */
  coverage(): { markets: number; measured: number; feeFree: number; modelled: number } {
    let measured = 0;
    for (const v of this.seen.values()) if (v.maxRate >= MIN_MEANINGFUL_RATE) measured++;
    return {
      markets: this.seen.size,
      measured,
      feeFree: this.freeHits.size,
      modelled: this.modelledHits.size,
    };
  }
}

// ── GAS — Polygon, priced live ─────────────────────────────────────────
//
// Who pays what, on Polymarket:
//   CLOB fill        operator submits the match          → user pays nothing
//   redeem           relayer submits `redeemPositions`   → user pays nothing
//   withdraw         relayer submits the transfer        → user pays nothing
//   proxy deploy     user submits `createProxy`          → USER PAYS, once
//   USDC approval    user submits `approve`              → USER PAYS, once per spender
//   deposit          user submits an ERC-20 transfer     → USER PAYS, per deposit
//
// So a copy session's gas is a handful of cents at Polygon prices, and it is
// FIXED — it does not scale with trade count. Booking $0.50/trade would have
// been as wrong as booking $0.

export const POLYGON_GAS_UNITS = {
  /** ERC-20 transfer of USDC into the trading wallet. */
  deposit: 65_000,
  /** `approve` for the exchange / CTF spenders. */
  approval: 55_000,
  /** Gnosis-safe proxy `createProxy` — the one-time trading-wallet deploy. */
  proxyDeploy: 320_000,
  /** `redeemPositions` — relayer-paid today, priced here for the case where
      the console ever submits it directly. */
  redeem: 180_000,
} as const;

export type GasOp = keyof typeof POLYGON_GAS_UNITS;

export interface GasQuote {
  /** Polygon gas price, gwei. */
  gasPriceGwei: number;
  /** POL/USD. */
  polUsd: number;
  asOf: number;
  source: "live" | "fallback";
}

/** Used until a live quote lands: Polygon's floor is 25–30 gwei and POL has
    traded 0.15–0.35 through 2026. Deliberately not optimistic. */
export const FALLBACK_GAS_QUOTE: GasQuote = {
  gasPriceGwei: 30,
  polUsd: 0.25,
  asOf: 0,
  source: "fallback",
};

export function gasUsd(units: number, quote: GasQuote = FALLBACK_GAS_QUOTE): number {
  return units * quote.gasPriceGwei * 1e-9 * quote.polUsd;
}

export function opGasUsd(op: GasOp, quote: GasQuote = FALLBACK_GAS_QUOTE): number {
  return gasUsd(POLYGON_GAS_UNITS[op], quote);
}

/** On-chain operations a deployment actually pays for. Fills are absent on
    purpose — they are relayer-matched and cost the trader nothing. */
export interface GasOps {
  proxyDeploys?: number;
  approvals?: number;
  deposits?: number;
  redeems?: number;
}

/** What a fresh deployment pays before it has traded once: deploy the proxy,
    approve the exchange + the CTF, fund it. */
export const NEW_DEPLOYMENT_GAS_OPS: GasOps = { proxyDeploys: 1, approvals: 2, deposits: 1 };

export function sessionGasUsd(ops: GasOps, quote: GasQuote = FALLBACK_GAS_QUOTE): number {
  return (
    (ops.proxyDeploys ?? 0) * opGasUsd("proxyDeploy", quote) +
    (ops.approvals ?? 0) * opGasUsd("approval", quote) +
    (ops.deposits ?? 0) * opGasUsd("deposit", quote) +
    (ops.redeems ?? 0) * opGasUsd("redeem", quote)
  );
}

const POLYGON_RPC = "https://polygon-rpc.com";
const POL_PRICE_URL =
  "https://api.coinbase.com/v2/prices/POL-USD/spot";

/** Live Polygon gas price + POL price. Falls back rather than throwing —
    a cost model that can't render because a price feed blinked is worse than
    one that renders a slightly stale number and says so. */
export async function fetchGasQuote(
  fetchImpl: typeof fetch = fetch,
): Promise<GasQuote> {
  const quote: GasQuote = { ...FALLBACK_GAS_QUOTE, asOf: Date.now(), source: "fallback" };
  let gotGas = false;
  let gotPrice = false;
  try {
    const res = await fetchImpl(POLYGON_RPC, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ jsonrpc: "2.0", id: 1, method: "eth_gasPrice", params: [] }),
    });
    const json = await res.json();
    const wei = Number(BigInt(json?.result ?? "0x0"));
    if (wei > 0) { quote.gasPriceGwei = wei / 1e9; gotGas = true; }
  } catch { /* keep the floor */ }
  try {
    const res = await fetchImpl(POL_PRICE_URL);
    const json = await res.json();
    const px = Number(json?.data?.amount);
    if (px > 0) { quote.polUsd = px; gotPrice = true; }
  } catch { /* keep the floor */ }
  if (gotGas && gotPrice) quote.source = "live";
  return quote;
}

// ── Cost breakdown — what the panels render ────────────────────────────

export interface FeeBucket {
  rate: number;
  category: FeeCategory;
  fees: number;
  volume: number;
  txs: number;
}

export interface CostBreakdown {
  /** Platform (taker) fees, USD. */
  fees: number;
  /** Polygon gas, USD. */
  gas: number;
  /** Executed notional the fees were charged on. */
  volume: number;
  /** Taker fills charged. */
  txs: number;
  /** Fees as basis points of executed notional — the one number that makes
      "$41 of fees" comparable across window sizes. */
  effectiveBps: number;
  /** Fees + gas as a % of gross P&L; null when gross P&L <= 0. */
  dragPct: number | null;
  /** Per-rate split, biggest bill first. */
  buckets: FeeBucket[];
  /** Weighted volume earned, and the rebate tier it reaches. */
  weightedVolume: number;
  tier: RebateTier;
  /** Where the rates came from. */
  coverage: { markets: number; measured: number; feeFree: number; modelled: number };
  gasQuote: GasQuote;
}

/** Accumulates fees as a sim books them, and reports the breakdown. */
export class CostLedger {
  private buckets = new Map<string, FeeBucket>();
  private wv = 0;
  fees = 0;
  volume = 0;
  txs = 0;

  constructor(
    readonly book: FeeBook = new FeeBook(),
    readonly gasQuote: GasQuote = FALLBACK_GAS_QUOTE,
  ) {}

  /** Charge one taker fill and return the fee in USD. */
  charge(args: {
    conditionId: string; market?: string; slug?: string;
    shares: number; price: number; notional: number;
  }): number {
    const info = this.book.info(args.conditionId, args.market, args.slug);
    const fee = takerFeeUsd(args.shares, args.price, info.rate);
    const key = `${info.category}:${info.rate}`;
    const b = this.buckets.get(key)
      ?? { rate: info.rate, category: info.category, fees: 0, volume: 0, txs: 0 };
    b.fees += fee;
    b.volume += args.notional;
    b.txs++;
    this.buckets.set(key, b);
    this.fees += fee;
    this.volume += args.notional;
    this.txs++;
    this.wv += weightedVolume(args.notional, args.price, info.category);
    return fee;
  }

  /** Fee for a fill WITHOUT booking it — for gates that need to know the cost
      before deciding to trade. */
  quote(conditionId: string, shares: number, price: number, market?: string, slug?: string): number {
    return takerFeeUsd(shares, price, this.book.rateFor(conditionId, market, slug));
  }

  breakdown(gasOps: GasOps, grossPnl: number): CostBreakdown {
    const gas = sessionGasUsd(gasOps, this.gasQuote);
    const total = this.fees + gas;
    return {
      fees: this.fees,
      gas,
      volume: this.volume,
      txs: this.txs,
      effectiveBps: this.volume > 0 ? (this.fees / this.volume) * 10_000 : 0,
      dragPct: grossPnl > 0 ? (total / grossPnl) * 100 : null,
      buckets: [...this.buckets.values()].sort((a, b) => b.fees - a.fees),
      weightedVolume: this.wv,
      tier: rebateTierFor(this.wv),
      coverage: this.book.coverage(),
      gasQuote: this.gasQuote,
    };
  }
}

/** Sum two breakdowns — a strat that both copies and originates runs the two
    replays separately and shows one cost row. Buckets on the same
    (category, rate) merge; coverage adds. */
export function mergeCostBreakdowns(
  a: CostBreakdown, b: CostBreakdown, grossPnl: number,
): CostBreakdown {
  const buckets = new Map<string, FeeBucket>();
  for (const src of [a.buckets, b.buckets]) {
    for (const x of src) {
      const key = `${x.category}:${x.rate}`;
      const cur = buckets.get(key) ?? { rate: x.rate, category: x.category, fees: 0, volume: 0, txs: 0 };
      cur.fees += x.fees;
      cur.volume += x.volume;
      cur.txs += x.txs;
      buckets.set(key, cur);
    }
  }
  const fees = a.fees + b.fees;
  // Gas is per DEPLOYMENT and both halves ran on ONE wallet, so the bill does
  // not double — take the larger, not the sum.
  const gas = Math.max(a.gas, b.gas);
  const volume = a.volume + b.volume;
  const wv = a.weightedVolume + b.weightedVolume;
  return {
    fees,
    gas,
    volume,
    txs: a.txs + b.txs,
    effectiveBps: volume > 0 ? (fees / volume) * 10_000 : 0,
    dragPct: grossPnl > 0 ? ((fees + gas) / grossPnl) * 100 : null,
    buckets: [...buckets.values()].sort((x, y) => y.fees - x.fees),
    weightedVolume: wv,
    tier: rebateTierFor(wv),
    coverage: {
      markets: a.coverage.markets + b.coverage.markets,
      measured: a.coverage.measured + b.coverage.measured,
      feeFree: a.coverage.feeFree + b.coverage.feeFree,
      modelled: a.coverage.modelled + b.coverage.modelled,
    },
    gasQuote: a.gasQuote.source === "live" ? a.gasQuote : b.gasQuote,
  };
}

/** Gas renders to 2dp like every other dollar figure, except a real Polygon
    bill is often smaller than a cent — which "$0.00" spells as "free". */
export function fmtGasUsd(usd: number): string {
  if (usd <= 0) return "$0.00";
  if (usd < 0.01) return `$${usd.toFixed(4)}`;
  return `$${usd.toFixed(2)}`;
}

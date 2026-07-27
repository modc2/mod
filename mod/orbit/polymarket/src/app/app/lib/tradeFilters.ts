// Semantic per-trade filter — the gate that makes a strat UNIQUE.
//
// `marketQuery` (lib/marketQuery.ts) restricts a strat to markets whose TITLE
// matches a topic. This file is the orthogonal, attribute-level gate: given one
// observed trade, decide whether the strat should mirror it based on the
// trade's OWN properties — side, the leader's fill price, the leader's USD
// notional, and the market's category bucket.
//
// Every active dimension is AND-ed. An unset dimension is a no-op (passes
// everything), so an empty `TradeFilters` ⇒ mirror everything. Two strats
// watching the same traders with different filters copy different slices of the
// same flow.
//
// Mirror of `trade_passes_filters` in src/api/src/live_engine.rs — keep the two
// in sync (Rust live engine must gate identically to the browser engine).

import { TradeFilters } from "./types";
import { matchMarketCategory } from "./polymarket";

/** Default entry-probability floor: a BUY below this price (= implied
    probability) is skipped when the strat expresses NO explicit price band
    (neither `minPrice` nor `maxPrice`). Likely-to-win entries by default —
    longshot flow is only copied when a strat opts in with its own band
    (an explicit `minPrice: 0` also disables the floor). SELLs (exits) are
    never floored. Mirror of DEFAULT_MIN_ENTRY_PRICE in live_engine.rs. */
export const DEFAULT_MIN_ENTRY_PRICE = 0.6;

/** The minimal trade shape the gate needs. `PolymarketTrade` and the strat's
    `TraderTrade` both satisfy it. `notional` is optional — when absent we
    derive it from price × size. */
export interface FilterableTrade {
  side: "BUY" | "SELL";
  price: number;
  size: number;
  market: string;
  notional?: number;
}

/** True if ANY dimension of `filters` is actually constraining. Used by the UI
    to render an "active" pill and to short-circuit the gate. */
export function tradeFiltersActive(filters: TradeFilters | undefined | null): boolean {
  if (!filters) return false;
  return (
    (filters.sides != null && filters.sides !== "both") ||
    filters.minPrice != null ||
    filters.maxPrice != null ||
    filters.minNotional != null ||
    filters.maxNotional != null ||
    (Array.isArray(filters.categories) && filters.categories.length > 0)
  );
}

/** Apply the semantic per-trade gate. Returns true ⇒ mirror this trade.
    Empty/undefined filters ⇒ BUYs still face the `DEFAULT_MIN_ENTRY_PRICE`
    favorites-only floor; everything else passes. */
export function tradeMatchesFilters(
  trade: FilterableTrade,
  filters: TradeFilters | undefined | null,
): boolean {
  const f = filters ?? {};

  // ── Side ──
  if (f.sides === "buy" && trade.side !== "BUY") return false;
  if (f.sides === "sell" && trade.side !== "SELL") return false;

  // ── Entry price band (0–1) ──
  // No explicit band at all ⇒ BUYs default to the likely-to-win floor. Any
  // explicit minPrice/maxPrice (incl. minPrice: 0) is the strat's own
  // opinion and wins; SELLs are exits and are never floored.
  const noPriceBand = f.minPrice == null && f.maxPrice == null;
  const effectiveMin =
    f.minPrice ?? (noPriceBand && trade.side === "BUY" ? DEFAULT_MIN_ENTRY_PRICE : undefined);
  if (effectiveMin != null && trade.price < effectiveMin) return false;
  if (f.maxPrice != null && trade.price > f.maxPrice) return false;

  // ── Trade size band (USD notional) ──
  const notional = trade.notional ?? trade.price * trade.size;
  if (f.minNotional != null && notional < f.minNotional) return false;
  if (f.maxNotional != null && notional > f.maxNotional) return false;

  // ── Category — market title must match at least one selected bucket ──
  if (Array.isArray(f.categories) && f.categories.length > 0) {
    if (!f.categories.some((c) => matchMarketCategory(trade.market, c))) return false;
  }

  return true;
}

/** Short human summary of the active filters — for log rows / UI chips.
    Empty string when nothing is constraining. */
export function describeTradeFilters(filters: TradeFilters | undefined | null): string {
  if (!tradeFiltersActive(filters)) return "";
  const f = filters!;
  const parts: string[] = [];
  if (f.sides && f.sides !== "both") parts.push(f.sides === "buy" ? "buys only" : "sells only");
  if (f.minPrice != null || f.maxPrice != null) {
    const lo = f.minPrice != null ? `${Math.round(f.minPrice * 100)}¢` : "0¢";
    const hi = f.maxPrice != null ? `${Math.round(f.maxPrice * 100)}¢` : "100¢";
    parts.push(`price ${lo}–${hi}`);
  }
  if (f.minNotional != null || f.maxNotional != null) {
    const lo = f.minNotional != null ? `$${f.minNotional}` : "$0";
    const hi = f.maxNotional != null ? `$${f.maxNotional}` : "∞";
    parts.push(`size ${lo}–${hi}`);
  }
  if (Array.isArray(f.categories) && f.categories.length > 0) {
    parts.push(f.categories.join("/"));
  }
  return parts.join(" · ");
}

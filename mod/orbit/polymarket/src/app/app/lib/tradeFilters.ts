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

/* There is deliberately no implicit entry-price floor. A 60¢ "likely to win"
   default used to apply to BUYs whenever a strat set no price band, and it was
   the single largest source of "the engine sees 57 leader entries and copies
   none": a filter nobody chose, silently rejecting most of the flow. A price
   band is now exactly what the strat says it is. Mirror of live_engine.rs. */

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
    Empty/undefined filters ⇒ everything passes. */
export function tradeMatchesFilters(
  trade: FilterableTrade,
  filters: TradeFilters | undefined | null,
): boolean {
  const f = filters ?? {};

  // ── Side ──
  if (f.sides === "buy" && trade.side !== "BUY") return false;
  if (f.sides === "sell" && trade.side !== "SELL") return false;

  // ── Entry price band (0–1) ──
  // Only the band the strat actually set. Nothing implicit.
  if (f.minPrice != null && trade.price < f.minPrice) return false;
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

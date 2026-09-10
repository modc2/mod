// Semantic per-trade filter — the gate that makes a strat UNIQUE.
//
// `marketQuery` (lib/marketQuery.ts) restricts a strat to markets whose TITLE
// matches a topic. This file is the orthogonal, attribute-level gate: given one
// observed trade, decide whether the strat should mirror it based on the
// trade's OWN properties — side, the leader's fill price, the leader's USD
// notional, and the market's category bucket — plus ONE property of the market
// around it: MARKET SENTIMENT, which way the crowd has moved the odds on the
// outcome they bought (lib/marketSentiment.ts). That last one is the only
// dimension that needs data the trade doesn't carry, which is what the
// optional `FilterContext` below exists for.
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
import {
  sentimentFilterActive, sentimentReject, describeSentiment,
  type SentimentLookup,
} from "./marketSentiment";

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
  /** CTF outcome token, under either of the two names the codebase uses for
      it (`asset` on a data-api trade, `tokenId` on an engine one). Only the
      SENTIMENT dimension reads it, and only when one is set. */
  asset?: string;
  tokenId?: string;
  /** ms epoch. A replay reads sentiment as of the trade; live reads it now. */
  timestamp?: number;
}

/** Everything the gate needs that is NOT on the trade.
 *
 *  There is exactly one such thing, and it is the reason this parameter exists:
 *  market sentiment is a property of the MARKET, so it has to be fetched, and
 *  `tradeMatchesFilters` is called from a dozen synchronous render paths that
 *  cannot await anything. So the fetch happens once, upstream
 *  (`warmSentiment`), and arrives here as a pure lookup.
 *
 *  Omitting the context is always safe: a strat with no sentiment filter never
 *  needed it, and one WITH a sentiment filter but no book reads every market
 *  as `unknown`, which its own `unknown` policy then decides — pass by
 *  default. A caller that forgets to warm the book gets a filter that does
 *  nothing, never one that silently rejects the entire flow. */
export interface FilterContext {
  sentiment?: SentimentLookup;
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
    (Array.isArray(filters.categories) && filters.categories.length > 0) ||
    sentimentFilterActive(filters.sentiment)
  );
}

/** Apply the semantic per-trade gate. Returns true ⇒ mirror this trade.
    Empty/undefined filters ⇒ everything passes. */
export function tradeMatchesFilters(
  trade: FilterableTrade,
  filters: TradeFilters | undefined | null,
  ctx?: FilterContext,
): boolean {
  return tradeFilterReject(trade, filters, ctx) === null;
}

/** Same gate, naming the dimension that rejected — mirror of
    `trade_filter_reject` in live_engine.rs. The funnel and the LIVE panel's
    gate tally read this, so "the strat copied nothing" always has a WHY. */
export function tradeFilterReject(
  trade: FilterableTrade,
  filters: TradeFilters | undefined | null,
  ctx?: FilterContext,
): string | null {
  const f = filters ?? {};

  // ── Side ──
  if (f.sides === "buy" && trade.side !== "BUY") return "side";
  if (f.sides === "sell" && trade.side !== "SELL") return "side";

  // ── Entry price band (0–1) ──
  // Only the band the strat actually set. Nothing implicit.
  if (f.minPrice != null && trade.price < f.minPrice) return "price";
  if (f.maxPrice != null && trade.price > f.maxPrice) return "price";

  // ── Trade size band (USD notional) ──
  const notional = trade.notional ?? trade.price * trade.size;
  if (f.minNotional != null && notional < f.minNotional) return "size";
  if (f.maxNotional != null && notional > f.maxNotional) return "size";

  // ── Category — market title must match at least one selected bucket ──
  if (Array.isArray(f.categories) && f.categories.length > 0) {
    if (!f.categories.some((c) => matchMarketCategory(trade.market, c))) return "category";
  }

  // ── Market sentiment — which way the crowd moved THEIR outcome token ──
  // The only dimension that needs data off the trade. No book ⇒ every market
  // reads `unknown`, and the filter's own `unknown` policy (pass by default)
  // decides. See the FilterContext doc above.
  if (sentimentFilterActive(f.sentiment)) {
    const reason = sentimentReject(ctx?.sentiment?.(trade), f.sentiment);
    if (reason) return reason;
  }

  return null;
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
  const sent = describeSentiment(f.sentiment);
  if (sent) parts.push(sent);
  return parts.join(" · ");
}

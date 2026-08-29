// THE IDENTITY TEMPLATE — a copied trader, as a runnable strategy.
//
// The COPY DESK's unit is an allocation: one leader and the dollars behind
// them. Nothing downstream knows what an allocation is — the backtest replays
// *strats*, the live engine runs *strats*, the ledger buckets by *strat id* —
// so this is where a leader becomes one.
//
// An IDENTITY strat is a strat whose watchlist is exactly one trader at weight
// 1, with `identity` set to their address. That field is the class marker: the
// hub badges it, the strat sidebar badges it, and nothing re-seeds its
// watchlist from a leaderboard the way an index strat's is re-seeded.
//
// This file is the browser/worker half. `api/src/copy.rs::identity_strat` is
// the server half, and it is the one the live engine uses. They are pinned
// against each other by `lib/strats/identity.fixture.json` — change a default
// in one and the other's test fails. Do not add a knob to one alone.

import type { SavedIndex, SizingModel, TradeFilters } from "./types";

/** Backtest/sizing lookback in days. */
export const IDENTITY_BACKTEST_DAYS = 7;
/** Live poll cadence in minutes — 30s. Fast enough to reach a leader's fill
    while the price is still near theirs, slow enough not to draw 429s. */
export const IDENTITY_POLL_MINUTES = 0.5;
/** Order floor in USDC (the CLOB's own hard floor is $1). */
export const IDENTITY_MIN_TRADE = 1;
/** Per-order ceiling in USDC. */
export const IDENTITY_MAX_TRADE = 100;
/** Mirrors placed per cycle before the rest defer. */
export const IDENTITY_MAX_PER_CYCLE = 3;
/** Concurrent open positions per leader. */
export const IDENTITY_MAX_OPEN_POSITIONS = 10;
/** Sell once the bid decays to this fraction of entry. */
export const IDENTITY_STOP_LOSS = 0.75;
/** Liquidate a position that has run to this absolute price. */
export const IDENTITY_TAKE_PROFIT = 0.99;
/** Refuse a market resolving sooner than this many minutes. Sub-hour Up/Down
    candles resolve before a poller can react — copying them is a structural
    loss, measured, not a hunch. */
export const IDENTITY_MIN_MINUTES_TO_CLOSE = 60;
/** Refuse a leader trade older than this many seconds — a backlog after a
    fetch outage enters at prices the leader never paid. */
export const IDENTITY_MAX_TRADE_AGE_SEC = 300;
/** Copy the leader's CONVICTION (our allocation across the capital they
    deployed), not their bankroll fraction: a small book copying a whale
    places real orders under `flow` and nothing at all under `bankroll`. */
export const IDENTITY_SIZING: SizingModel = "flow";
/** How many times the allocation may be deployed across one window. */
export const IDENTITY_TURNOVER = 1;

/** Per-allocation overrides. Every field optional — absent means "template
    default", which is what keeps a ten-leader book readable. Mirror of
    `AllocationParams` in api/src/copy.rs. */
export interface AllocationParams {
  minTrade?: number;
  maxTrade?: number;
  maxPerCycle?: number;
  maxOpenPositions?: number;
  pollMinutes?: number;
  backtestDays?: number;
  sizing?: SizingModel;
  turnover?: number;
  /** 0 is a real value — stop-loss OFF — so these are `number | undefined`,
      never defaulted with `||`. */
  stopLoss?: number;
  takeProfit?: number;
  minMinutesToClose?: number;
  maxTradeAgeSec?: number;
  marketQuery?: string;
  /** The per-TRADE half of the gate: side, the leader's fill-price band, the
      leader's notional band. `marketQuery` picks the markets, this picks the
      trades inside them — the two halves lib/semanticFilter.ts compiles one
      typed sentence into. Absent ⇒ mirror everything in those markets.
      Mirror of `trade_filters` in api/src/copy.rs. */
  tradeFilters?: TradeFilters;
}

/** One line of the copy book. Mirror of `Allocation` in api/src/copy.rs; this
    is the shape `/copy/book` returns per trader. */
export interface Allocation {
  address: string;
  /** `null` as well as absent: the API omits nothing, it sends JSON null. */
  label?: string | null;
  allocationUsd: number;
  enabled: boolean;
  params?: AllocationParams;
  notes?: string | null;
  addedAt: number;
  updatedAt: number;
  /** Server-derived; present on anything read back from `/copy/book`. */
  strategyId?: string;
  /** Server-derived live session summary — see `CopyBookRow` in copyBook.ts. */
  live?: unknown;
}

/** `copy-<address without 0x>`. Deterministic on purpose: the engine session
    key, the ledger bucket, the persisted config and the backtest card all
    derive it from the address, so there is no table to fall out of sync. */
export function strategyIdFor(address: string): string {
  return `copy-${address.trim().toLowerCase().replace(/^0x/, "")}`;
}

/** The inverse — read a strat id back to the leader it copies. Returns null
    for anything that isn't a copy-desk id (an old hub strat's opaque base36
    id must NOT decode to an address, or the ledger would attribute one
    strat's P&L to some leader). */
export function addressFromStrategyId(strategyId: string): string | null {
  const rest = strategyId.startsWith("copy-") ? strategyId.slice(5) : null;
  if (!rest || !/^[0-9a-fA-F]{40}$/.test(rest)) return null;
  return `0x${rest.toLowerCase()}`;
}

export function shortAddress(address: string): string {
  const a = (address || "").trim();
  return a.length < 12 ? a : `${a.slice(0, 6)}…${a.slice(-4)}`;
}

/** Display name: the label if there is one, else the short address. */
export function allocationName(alloc: Allocation): string {
  const label = (alloc.label ?? "").trim();
  return label || `COPY ${shortAddress(alloc.address)}`;
}

/** An allocation, as a strat. See the file header — and note the two poll
    fields are deliberately the same number: a backtest aggregated coarser than
    the engine polls would promise fills at prices the engine never sees. */
export function identityStrat(alloc: Allocation): SavedIndex {
  const p = alloc.params ?? {};
  const poll = p.pollMinutes ?? IDENTITY_POLL_MINUTES;
  return {
    id: strategyIdFor(alloc.address),
    name: allocationName(alloc),
    identity: alloc.address,
    traders: [{ address: alloc.address, weight: 1, enabled: true }],
    capital: alloc.allocationUsd,
    backtestDays: p.backtestDays ?? IDENTITY_BACKTEST_DAYS,
    rebalanceMinutes: poll,
    livePollMinutes: poll,
    minTrade: p.minTrade ?? IDENTITY_MIN_TRADE,
    maxTrade: p.maxTrade ?? IDENTITY_MAX_TRADE,
    maxPerCycle: p.maxPerCycle ?? IDENTITY_MAX_PER_CYCLE,
    maxOpenPositions: p.maxOpenPositions ?? IDENTITY_MAX_OPEN_POSITIONS,
    sizing: p.sizing ?? IDENTITY_SIZING,
    turnover: p.turnover ?? IDENTITY_TURNOVER,
    stopLoss: p.stopLoss ?? IDENTITY_STOP_LOSS,
    takeProfit: p.takeProfit ?? IDENTITY_TAKE_PROFIT,
    minMinutesToClose: p.minMinutesToClose ?? IDENTITY_MIN_MINUTES_TO_CLOSE,
    maxTradeAgeSec: p.maxTradeAgeSec ?? IDENTITY_MAX_TRADE_AGE_SEC,
    marketQuery: p.marketQuery ?? "",
    // Only when the allocation carries one. An always-present `undefined` is
    // the same object either way in JS, but the Rust half must omit the key
    // too — identity.fixture.json compares them exactly.
    ...(p.tradeFilters ? { tradeFilters: p.tradeFilters } : {}),
    fundsMode: "SIM",
    forkedFrom: "copy-desk-identity",
    liveEnabled: alloc.enabled,
    createdAt: alloc.addedAt,
    updatedAt: alloc.updatedAt,
  };
}

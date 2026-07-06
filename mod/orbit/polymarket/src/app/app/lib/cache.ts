// Hourly cache layer for polymarket data
// Keys are scoped by address + hour so we never refetch within the same hour.
// Trades are also indexed by conditionId for fast per-market lookups.

import { PolymarketTrade } from "./types";

const CACHE_PREFIX = "poly_hr_";
const TRADE_CID_PREFIX = "poly_cid_";
const HOUR_MS = 60 * 60 * 1000;

// ── Core hourly cache ──────────────────────────────────────────

function hourKey(address: string, dataType: string): string {
  const now = new Date();
  const hour = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}-${String(now.getHours()).padStart(2, "0")}`;
  return `${CACHE_PREFIX}${dataType}_${address.toLowerCase()}_${hour}`;
}

export function getCached<T>(address: string, dataType: string): T | null {
  if (typeof window === "undefined") return null;
  try {
    const key = hourKey(address, dataType);
    const raw = localStorage.getItem(key);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    return parsed.data as T;
  } catch {
    return null;
  }
}

export function setCache<T>(address: string, dataType: string, data: T): void {
  if (typeof window === "undefined") return;
  try {
    const key = hourKey(address, dataType);
    localStorage.setItem(key, JSON.stringify({ data, ts: Date.now() }));
    pruneOldCache();
  } catch {
    // localStorage might be full — try to free space and retry once
    try {
      pruneOldCache(2 * HOUR_MS); // aggressive prune: 2h
      const key = hourKey(address, dataType);
      localStorage.setItem(key, JSON.stringify({ data, ts: Date.now() }));
    } catch {
      // truly full — give up
    }
  }
}

// ── Trade cache with conditionId index ─────────────────────────

/**
 * Cache a complete trade history for an address.
 * Also builds a per-conditionId index so downstream consumers
 * can pull trades for a single market without scanning the full array.
 */
export function setTradeCache(address: string, trades: PolymarketTrade[]): void {
  // Store the full array under the standard hourly key
  setCache(address, "trades_full", trades);

  // Build and store a conditionId → trade[] index.
  // Each CID key stores an array of trades for that market, keyed by
  // address + CID + hour so different traders don't collide.
  if (typeof window === "undefined") return;
  try {
    const cidMap = new Map<string, PolymarketTrade[]>();
    for (const t of trades) {
      const cid = t.conditionId;
      if (!cid) continue;
      let arr = cidMap.get(cid);
      if (!arr) { arr = []; cidMap.set(cid, arr); }
      arr.push(t);
    }

    const now = new Date();
    const hour = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}-${String(now.getHours()).padStart(2, "0")}`;
    const addr = address.toLowerCase();

    // Store a manifest of all CIDs for this address (for pruning)
    const cidKeys: string[] = [];
    for (const [cid, cidTrades] of cidMap) {
      const key = `${TRADE_CID_PREFIX}${addr}_${cid}_${hour}`;
      cidKeys.push(key);
      localStorage.setItem(key, JSON.stringify({ data: cidTrades, ts: Date.now() }));
    }
    localStorage.setItem(
      `${TRADE_CID_PREFIX}manifest_${addr}_${hour}`,
      JSON.stringify({ keys: cidKeys, ts: Date.now() }),
    );
  } catch {
    // localStorage full — CID index is best-effort
  }
}

/**
 * Get full cached trade history for an address.
 */
export function getTradeCache(address: string): PolymarketTrade[] | null {
  return getCached<PolymarketTrade[]>(address, "trades_full");
}

/**
 * Get cached trades for a specific conditionId from a trader's cache.
 */
export function getTradesByCid(address: string, conditionId: string): PolymarketTrade[] | null {
  if (typeof window === "undefined") return null;
  try {
    const now = new Date();
    const hour = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}-${String(now.getHours()).padStart(2, "0")}`;
    const key = `${TRADE_CID_PREFIX}${address.toLowerCase()}_${conditionId}_${hour}`;
    const raw = localStorage.getItem(key);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    return parsed.data as PolymarketTrade[];
  } catch {
    return null;
  }
}

// ── Market data cache (TTL-based, not hourly) ────────────────

const MARKET_PREFIX = "poly_mkt_";
const MARKET_TTL_MS = 5 * 60 * 1000; // 5 minutes

export function getMarketCache(cacheKey: string): unknown[] | null {
  if (typeof window === "undefined") return null;
  try {
    const key = `${MARKET_PREFIX}${cacheKey}`;
    const raw = localStorage.getItem(key);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    if (Date.now() - parsed.ts > MARKET_TTL_MS) {
      localStorage.removeItem(key);
      return null;
    }
    return parsed.data as unknown[];
  } catch {
    return null;
  }
}

export function setMarketCache(cacheKey: string, data: unknown[]): void {
  if (typeof window === "undefined") return;
  try {
    const key = `${MARKET_PREFIX}${cacheKey}`;
    localStorage.setItem(key, JSON.stringify({ data, ts: Date.now() }));
  } catch {
    // ignore
  }
}

// ── Pruning ────────────────────────────────────────────────────

function pruneOldCache(maxAge: number = 24 * HOUR_MS): void {
  if (typeof window === "undefined") return;
  const cutoff = Date.now() - maxAge;
  const keysToRemove: string[] = [];
  const prefixes = [CACHE_PREFIX, TRADE_CID_PREFIX, MARKET_PREFIX];
  for (let i = 0; i < localStorage.length; i++) {
    const key = localStorage.key(i);
    if (!key || !prefixes.some((p) => key.startsWith(p))) continue;
    try {
      const raw = localStorage.getItem(key);
      if (raw) {
        const { ts } = JSON.parse(raw);
        if (ts < cutoff) keysToRemove.push(key);
      }
    } catch {
      keysToRemove.push(key!);
    }
  }
  for (const k of keysToRemove) {
    localStorage.removeItem(k);
  }
}

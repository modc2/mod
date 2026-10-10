"use client";

// One balance sweep, shared. The MONEY tab has several blocks that all want
// "what does this wallet hold on every chain" (the liquidity strip, the
// bridge rows) — each doing its own sweep is 5 chains × 3 tokens of RPC per
// mount. This module runs the sweep ONCE per address, caches it at module
// scope, and every hook instance subscribes to the same copy. Reads go
// straight to the public RPCs in lib/networks — no indexer, no API key.

import {
  Contract,
  JsonRpcProvider,
  formatEther,
  formatUnits,
} from "ethers";
import { useCallback, useEffect, useState } from "react";
import { NETWORKS, withRpcFallback } from "./networks";

const ERC20_ABI = ["function balanceOf(address) view returns (uint256)"];

/** Per-chain holdings. `null` = the read failed (distinct from 0 = confirmed
    empty) — never render null as $0. */
export interface ChainHolding {
  usdc: number | null;
  usdt: number | null;
  native: number | null;
}

export type Holdings = Record<string, ChainHolding>;

interface CacheEntry {
  holdings: Holdings;
  sweptAt: number; // 0 = sweep in flight, never finished
  loading: boolean;
}

const TTL_MS = 60_000;
const cache = new Map<string, CacheEntry>();
const listeners = new Set<() => void>();

function notify() {
  listeners.forEach((fn) => fn());
}

async function sweep(address: string): Promise<void> {
  const entry: CacheEntry = cache.get(address) ?? { holdings: {}, sweptAt: 0, loading: false };
  if (entry.loading) return; // one sweep at a time per address
  entry.loading = true;
  cache.set(address, entry);
  notify();

  await Promise.all(
    NETWORKS.map(async (net) => {
      const read = async <T,>(fn: (p: JsonRpcProvider) => Promise<T>): Promise<T | null> => {
        try {
          return await withRpcFallback(net, (url) => fn(new JsonRpcProvider(url)));
        } catch {
          return null;
        }
      };
      const [usdcRaw, usdtRaw, nativeRaw] = await Promise.all([
        read((p) => new Contract(net.usdc, ERC20_ABI, p).balanceOf(address) as Promise<bigint>),
        read((p) => new Contract(net.usdt, ERC20_ABI, p).balanceOf(address) as Promise<bigint>),
        read((p) => p.getBalance(address)),
      ]);
      const prev = entry.holdings[net.id];
      // Fresh object references on every update — consumers memoize on the
      // `holdings` identity, so an in-place mutation would never repaint.
      entry.holdings = {
        ...entry.holdings,
        [net.id]: {
          // Keep the last good number over a failed re-read.
          usdc: usdcRaw != null ? Number(formatUnits(usdcRaw, 6)) : prev?.usdc ?? null,
          usdt: usdtRaw != null ? Number(formatUnits(usdtRaw, 6)) : prev?.usdt ?? null,
          native: nativeRaw != null ? Number(formatEther(nativeRaw)) : prev?.native ?? null,
        },
      };
      notify(); // chains land one by one — paint each as it arrives
    }),
  );

  entry.loading = false;
  entry.sweptAt = Date.now();
  notify();
}

export interface ChainBalances {
  holdings: Holdings;
  loading: boolean;
  sweptAt: number;
  refresh: () => void;
}

/** Subscribe to the shared sweep for `address`. Sweeps on first mount and
    whenever `refresh()` is called; a mount within TTL reuses the cache. */
export function useChainBalances(address: string | null | undefined): ChainBalances {
  const [, bump] = useState(0);

  useEffect(() => {
    const fn = () => bump((n) => n + 1);
    listeners.add(fn);
    return () => { listeners.delete(fn); };
  }, []);

  useEffect(() => {
    if (!address) return;
    const entry = cache.get(address);
    if (!entry || (!entry.loading && Date.now() - entry.sweptAt > TTL_MS)) {
      void sweep(address);
    }
  }, [address]);

  const refresh = useCallback(() => {
    if (address) void sweep(address);
  }, [address]);

  const entry = address ? cache.get(address) : undefined;
  return {
    holdings: entry?.holdings ?? {},
    loading: entry?.loading ?? false,
    sweptAt: entry?.sweptAt ?? 0,
    refresh,
  };
}

/** Stablecoin dollars sitting on chains OTHER than Polygon — the "you could
    bridge this in" number. Native assets are excluded (they're not priced
    here); the bridge rows surface them individually. */
export function stablesOffPolygon(holdings: Holdings): number {
  return NETWORKS.filter((n) => n.id !== "polygon").reduce((sum, n) => {
    const h = holdings[n.id];
    return sum + (h?.usdc ?? 0) + (h?.usdt ?? 0);
  }, 0);
}

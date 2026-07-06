"use client";

import { useEffect, useState, useCallback } from "react";
import { Contract, JsonRpcProvider, formatUnits } from "ethers";
import { useAuth } from "../context/AuthContext";
import { networkById, withRpcFallback } from "./networks";

/// Polls the connected wallet's USDC.e balance on Polygon — the actual
/// settlement asset for Polymarket trades. Independent of the
/// WalletFundingPanel's per-network cache so any header/badge can read it.
export function usePolygonUsdc(intervalMs: number = 15_000): {
  balance: number | null;
  loading: boolean;
  error: string | null;
  refresh: () => void;
} {
  const { auth } = useAuth();
  const [balance, setBalance] = useState<number | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [tick, setTick] = useState(0);

  const fetchNow = useCallback(async () => {
    if (!auth.address) { setBalance(null); return; }
    const pol = networkById("polygon");
    if (!pol) return;
    setLoading(true);
    setError(null);
    try {
      const raw: bigint = await withRpcFallback(pol, async (url) => {
        const provider = new JsonRpcProvider(url);
        const usdc = new Contract(
          pol.usdc,
          ["function balanceOf(address) view returns (uint256)"],
          provider,
        );
        return usdc.balanceOf(auth.address!);
      });
      setBalance(Number(formatUnits(raw, 6)));
    } catch (e) {
      setError(e instanceof Error ? e.message.slice(0, 120) : String(e));
    } finally {
      setLoading(false);
    }
  }, [auth.address]);

  useEffect(() => { void fetchNow(); }, [fetchNow, tick]);

  useEffect(() => {
    if (!auth.address) return;
    const t = setInterval(() => setTick((x) => x + 1), intervalMs);
    return () => clearInterval(t);
  }, [auth.address, intervalMs]);

  return {
    balance,
    loading,
    error,
    refresh: () => setTick((x) => x + 1),
  };
}

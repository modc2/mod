"use client";

// LiveAutoResume — runs once after AuthContext has rehydrated CLOB creds.
// If a `poly_live_session` localStorage record exists AND the user's wallet
// + CLOB creds are available, it restarts the copy engine with the saved
// config. Stopping the engine explicitly clears the record (in
// CopyEngineContext.stopLive), so this only fires when the user wanted live
// to keep running across reloads. No UI — pure side-effect component.

import { useEffect, useRef } from "react";
import { useAuth } from "../context/AuthContext";
import { useCopyEngine, getPersistedLive } from "../context/CopyEngineContext";
import { loadIndexes } from "../lib/indexStore";

export default function LiveAutoResume() {
  const { auth } = useAuth();
  const { isLive, startLive } = useCopyEngine();
  // Once-per-mount guard. We don't want every render to keep retrying — if
  // the rehydration window passes without auth being ready, the user can
  // still manually GO LIVE from the LIVE tab.
  const attemptedRef = useRef(false);

  useEffect(() => {
    if (attemptedRef.current) return;
    if (isLive) {
      attemptedRef.current = true;
      return;
    }
    const rec = getPersistedLive();
    if (!rec) return;
    // Need the same EOA the engine was running under + CLOB creds in hand.
    if (!auth.connected || !auth.address || !auth.clobCreds) return;
    if (auth.address.toLowerCase() !== rec.address.toLowerCase()) return;

    const strat = loadIndexes().find((s) => s.id === rec.strategyId);
    if (!strat) return;
    const traders = strat.traders.filter((t) => t.enabled !== false);
    // Momentum strats originate from market prices — an empty watchlist is
    // a valid running configuration for them, so only copy strats bail here.
    if (traders.length === 0 && !strat.momentum) return;

    attemptedRef.current = true;
    startLive({
      strategyId: strat.id,
      traders,
      capital: rec.capital,
      intervalMs: rec.intervalMs,
      creds: auth.clobCreds,
      address: auth.address,
      minOrderSize: rec.minOrderSize,
      maxSlippageBps: rec.maxSlippageBps,
      // Restore the strat's market-topic filter + semantic trade filters from
      // the saved strat (not the persisted session record, which doesn't carry
      // them).
      marketQuery: strat.marketQuery,
      tradeFilters: strat.tradeFilters,
      momentum: strat.momentum,
    });
  }, [auth.connected, auth.address, auth.clobCreds, isLive, startLive]);

  return null;
}

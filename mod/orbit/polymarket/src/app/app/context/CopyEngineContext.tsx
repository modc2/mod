"use client";

import { createContext, useContext, useState, useRef, useCallback, useEffect, ReactNode } from "react";
import { CopyEngine, CopyEngineState, CopyEngineConfig } from "../lib/copyEngine";
import { getOwnerAddress } from "../lib/access";
import { stopLiveSession } from "../lib/liveSessions";

const API_URL = process.env.NEXT_PUBLIC_API_URL || "/api/polymarket";

interface CopyEngineContextValue {
  engineState: CopyEngineState | null;
  isLive: boolean;
  /** Strategy id currently being run (null when stopped). Exposed so the
      global LIVE indicator can show which strat is running and let users
      switch from anywhere in the app. */
  activeStrategyId: string | null;
  /** Whether the backend long-running engine is active (survives tab close). */
  backendRunning: boolean;
  /** Per-trader ms epoch of the backend engine's last successful data-api
      pull, keyed by lowercased address. Sourced from the backend rather than
      the browser engine so the SYNC panel stays truthful after a tab reload
      (the backend keeps polling; the browser engine may not be attached). */
  backendTraderSync: Record<string, number>;
  /** Cadence the backend engine is actually polling at (ms), or null when no
      backend session is running. Differs from the strat's requested interval
      whenever the engine's floor or fan-out widening kicked in. */
  backendIntervalMs: number | null;
  /** Whether the backend engine places REAL orders (false = dry-run: mirrors
      are logged but nothing is sent to the CLOB). */
  autoExecute: boolean;
  /** Flip real order placement on/off for the backend session. Resolves true
      when the backend acknowledged the change. */
  setAutoExecute: (on: boolean) => Promise<boolean>;
  startLive: (config: CopyEngineConfig) => void;
  stopLive: () => void;
  pauseLive: () => void;
  resumeLive: () => void;
  clearLog: () => void;
  /** One-shot catch-up: scan the last N hours of trades for every
      enrolled trader and copy the ones above the notional floor.
      Resolves with placement stats. Requires a running engine. */
  catchUp: (opts: {
    lookbackHours: number;
    minNotional: number;
    topN?: number;
    sellWinners?: boolean;
    onProgress?: (msg: string) => void;
  }) => Promise<{ scanned: number; placed: number; failed: number; skipped: number; sold: number }>;
}

// Persisted live-session record. Holds only the *config* needed to rebuild
// the engine on reload; sensitive bits (clobCreds) are sourced from
// AuthContext's per-EOA cache, so we don't double-store them.
interface PersistedLive {
  strategyId: string;
  address: string;
  capital: number;
  intervalMs: number;
  minOrderSize: number;
  maxSlippageBps: number;
  startedAt: number;
}

const LIVE_KEY = "poly_live_session";

function loadPersistedLive(): PersistedLive | null {
  try {
    const raw = typeof localStorage !== "undefined" ? localStorage.getItem(LIVE_KEY) : null;
    if (!raw) return null;
    const obj = JSON.parse(raw) as PersistedLive;
    if (!obj.strategyId || !obj.address || !obj.intervalMs) return null;
    return obj;
  } catch {
    return null;
  }
}

function savePersistedLive(rec: PersistedLive): void {
  try { localStorage.setItem(LIVE_KEY, JSON.stringify(rec)); } catch {}
}

function clearPersistedLive(): void {
  try { localStorage.removeItem(LIVE_KEY); } catch {}
}

export function getPersistedLive(): PersistedLive | null {
  return loadPersistedLive();
}

// ── Backend engine helpers ──────────────────────────────────────

async function backendStart(config: CopyEngineConfig): Promise<boolean> {
  try {
    // Forward every strat-supplied tunable to the backend live engine.
    // Undefined fields are omitted so the engine's own defaults apply — the
    // engine hardcodes nothing, so whatever the strat sets is what runs.
    const body = {
      eoa: config.address,
      strategyId: config.strategyId,
      address: config.address,
      traders: config.traders.map((t) => ({
        address: t.address,
        weight: t.weight ?? 1,
        enabled: t.enabled !== false,
      })),
      capital: config.capital,
      intervalMs: config.intervalMs,
      minOrderSize: config.minOrderSize,
      maxSlippageBps: config.maxSlippageBps,
      ...(config.maxOrderSize !== undefined && { maxOrderSize: config.maxOrderSize }),
      ...(config.backtestDays !== undefined && { backtestDays: config.backtestDays }),
      ...(config.minShares !== undefined && { minShares: config.minShares }),
      // Strat's top-N per-cycle cap → engine's max orders per cycle.
      ...(config.maxPerCycle !== undefined && { maxOrdersPerCycle: config.maxPerCycle }),
      // Concurrent open-positions cap — engine skips new-token BUYs past it.
      ...(config.maxOpenPositions !== undefined && { maxOpenPositions: config.maxOpenPositions }),
      // Per-position stop-loss (fraction of entry to defend). Send an
      // explicit 0 too: the engine treats 0 as OFF, while OMITTING the field
      // now means "server default" (0.75) — dropping the 0 here would
      // silently re-arm a stop the user turned off.
      ...(config.stopLoss !== undefined && { stopLoss: config.stopLoss }),
      // Per-position take-profit (absolute bid level; 0.99 = liquidate once
      // the market runs to the top tick). Same explicit-0-is-OFF contract.
      ...(config.takeProfit !== undefined && { takeProfit: config.takeProfit }),
      ...(config.autoExecute !== undefined && { autoExecute: config.autoExecute }),
      // Market-topic filter — backend only mirrors trades in matching markets.
      ...(config.marketQuery && { marketQuery: config.marketQuery }),
      // Semantic per-trade filters (side / price band / size band / category).
      // Omitted when empty so the backend's no-op defaults apply.
      ...(config.tradeFilters && { tradeFilters: config.tradeFilters }),
      // Trader FILTER — the backend re-ranks the watchlist every cycle and
      // copies only the top scorers. Omitted ⇒ every enabled trader is copied.
      ...(config.filter && { filter: config.filter }),
      // Price-momentum origination — the general, watchlist-free strategy
      // path. The backend engine tracks candidate markets' price history and
      // originates entries/exits from the moves themselves, so a strat with
      // ZERO traders still runs 24/7 server-side. Omitting this used to
      // silently strand momentum strats in the browser engine only.
      ...(config.momentum && { momentum: config.momentum }),
    };
    const res = await fetch(`${API_URL}/live/start`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    return res.ok;
  } catch {
    return false;
  }
}

// Stop ONE funded strat. A wallet can run several sessions at once (see
// lib/liveSessions.ts) — omitting strategyId is the backend's "stop the whole
// wallet", which would take the user's other funded strats down too.
async function backendStop(eoa: string, strategyId: string | null): Promise<boolean> {
  return stopLiveSession(eoa, strategyId ?? undefined);
}

interface BackendStatus {
  running: boolean;
  config?: Record<string, unknown>;
  state?: {
    status: string;
    lastCycleAt: number | null;
    nextCycleAt: number | null;
    cycleCount: number;
    totalOrdersPlaced: number;
    totalOrdersFailed: number;
    totalVolumeMirrored: number;
    balance: number | null;
    /** Cash + mark value of this session's positions — the number every
        proportional mirror is sized as a fraction of (live_engine.rs
        `AccountValue`). */
    accountValue?: number | null;
    log: Array<{ id: string; timestamp: number; type: string; reason?: string }>;
    observedTrades: Array<{
      id: string; timestamp: number; trader: string; market: string;
      conditionId: string; side: string; size: number; price: number; notional: number;
      score?: number; sharpe?: number;
    }>;
    error: string | null;
    traderCursors: Record<string, number>;
    traderLastSync: Record<string, number>;
    /** Cadence the backend loop is ACTUALLY running at — the strat's request
        after the engine's rate-limit floor and fan-out widening. */
    effectiveIntervalMs?: number;
  };
}

// Status for ONE session. Without a strategyId the backend answers for
// whichever of the wallet's sessions it finds first — fine for the "is
// anything running?" probe on mount, wrong once several strats are funded.
async function backendStatus(eoa: string, strategyId?: string | null): Promise<BackendStatus | null> {
  try {
    const qs = new URLSearchParams({ eoa });
    if (strategyId) qs.set("strategyId", strategyId);
    const res = await fetch(`${API_URL}/live/status?${qs.toString()}`);
    if (!res.ok) return null;
    return await res.json() as BackendStatus;
  } catch {
    return null;
  }
}

// ── Context ─────────────────────────────────────────────────────

const CopyEngineContext = createContext<CopyEngineContextValue>({
  engineState: null,
  isLive: false,
  activeStrategyId: null,
  backendRunning: false,
  backendTraderSync: {},
  backendIntervalMs: null,
  autoExecute: false,
  setAutoExecute: async () => false,
  startLive: () => {},
  stopLive: () => {},
  pauseLive: () => {},
  resumeLive: () => {},
  clearLog: () => {},
  catchUp: async () => ({ scanned: 0, placed: 0, failed: 0, skipped: 0, sold: 0 }),
});

export function useCopyEngine() {
  return useContext(CopyEngineContext);
}

export function CopyEngineProvider({ children }: { children: ReactNode }) {
  const engineRef = useRef<CopyEngine | null>(null);
  const [engineState, setEngineState] = useState<CopyEngineState | null>(null);
  const [isLive, setIsLive] = useState(false);
  const [activeStrategyId, setActiveStrategyId] = useState<string | null>(null);
  const [backendRunning, setBackendRunning] = useState(false);
  const [backendTraderSync, setBackendTraderSync] = useState<Record<string, number>>({});
  const [backendIntervalMs, setBackendIntervalMs] = useState<number | null>(null);
  const [autoExecute, setAutoExecuteState] = useState(false);
  // EOA + strat used for backend polling — set on start, cleared on stop.
  // The strat id scopes every backend call to THIS session, so stopping or
  // re-arming the browser-attached strat leaves the wallet's other funded
  // strats running (see lib/liveSessions.ts).
  const backendEoaRef = useRef<string | null>(null);
  const backendStrategyRef = useRef<string | null>(null);

  // Flip real order placement for the backend session via /live/execution.
  // The backend persists the flag with the session config, and /live/start
  // inherits it when a re-post omits autoExecute — so it survives restarts
  // and poll-interval hot-restarts.
  const setAutoExecute = useCallback(async (on: boolean) => {
    const eoa = backendEoaRef.current ?? getOwnerAddress();
    if (!eoa) return false;
    try {
      const res = await fetch(`${API_URL}/live/execution`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ eoa, strategyId: backendStrategyRef.current, autoExecute: on }),
      });
      if (!res.ok) return false;
      const j = (await res.json()) as { autoExecute?: boolean };
      setAutoExecuteState(!!j.autoExecute);
      return true;
    } catch {
      return false;
    }
  }, []);

  const startLive = useCallback((config: CopyEngineConfig) => {
    // Stop existing browser engine if any
    if (engineRef.current) {
      engineRef.current.stop();
    }

    const engine = new CopyEngine(config);
    engineRef.current = engine;

    engine.subscribe((s) => setEngineState(s));
    engine.start();
    setIsLive(true);
    setActiveStrategyId(config.strategyId);
    setEngineState(engine.getState());

    // Persist the session so a page reload can restart the browser engine.
    savePersistedLive({
      strategyId: config.strategyId,
      address: config.address,
      capital: config.capital,
      intervalMs: config.intervalMs,
      minOrderSize: config.minOrderSize,
      maxSlippageBps: config.maxSlippageBps,
      startedAt: Date.now(),
    });

    // Also start the backend long-running engine so it survives tab close.
    backendEoaRef.current = config.address;
    backendStrategyRef.current = config.strategyId;
    // Reflect an explicitly-requested execution mode immediately — the
    // status poll confirms it within 5s, but the EXECUTING/DRY RUN pill
    // shouldn't lie in the meantime.
    if (config.autoExecute !== undefined) {
      setAutoExecuteState(config.autoExecute);
    }
    void backendStart(config).then((ok) => {
      setBackendRunning(ok);
    });
  }, []);

  const stopLive = useCallback(() => {
    // Stop browser engine
    if (engineRef.current) {
      engineRef.current.stop();
      setEngineState(engineRef.current.getState());
    }
    setIsLive(false);
    setActiveStrategyId(null);
    clearPersistedLive();

    // Stop the backend engine for THIS strat only — other funded strats on
    // the same wallet keep running.
    const eoa = backendEoaRef.current;
    if (eoa) {
      void backendStop(eoa, backendStrategyRef.current);
      backendEoaRef.current = null;
      backendStrategyRef.current = null;
    }
    setBackendRunning(false);
  }, []);

  const pauseLive = useCallback(() => {
    if (engineRef.current) {
      engineRef.current.pause();
      setEngineState(engineRef.current.getState());
    }
  }, []);

  const resumeLive = useCallback(() => {
    if (engineRef.current) {
      engineRef.current.resume();
      setEngineState(engineRef.current.getState());
    }
  }, []);

  const clearLog = useCallback(() => {
    setEngineState((prev) => prev ? { ...prev, log: [] } : null);
  }, []);

  // CATCH UP — delegates to the in-browser engine's one-shot backfill.
  // Returns zeros when there's no live engine yet so the UI can disable
  // the button cleanly instead of throwing.
  const catchUp = useCallback(async (opts: {
    lookbackHours: number;
    minNotional: number;
    topN?: number;
    sellWinners?: boolean;
    onProgress?: (msg: string) => void;
  }) => {
    if (!engineRef.current) {
      return { scanned: 0, placed: 0, failed: 0, skipped: 0, sold: 0 };
    }
    return engineRef.current.catchUp(opts);
  }, []);

  // Poll backend status every 5s while live. Merge backend observed trades
  // into the frontend engine state so the user sees trades the backend
  // picked up even when the browser engine missed them (e.g. tab was
  // backgrounded). Also detects backend running on page load.
  useEffect(() => {
    const eoa = backendEoaRef.current;
    if (!eoa && !isLive) return;

    let cancelled = false;
    const poll = async () => {
      const pollEoa = backendEoaRef.current;
      if (!pollEoa) return;
      const status = await backendStatus(pollEoa, backendStrategyRef.current);
      if (cancelled) return;
      // Mirror the backend's execution mode (running session or persisted
      // snapshot) so the DRY RUN / EXECUTING toggle always tells the truth.
      if (status?.config) {
        setAutoExecuteState(!!(status.config as { autoExecute?: boolean }).autoExecute);
      }
      if (status?.running) {
        setBackendRunning(true);
        // Per-trader sync freshness + the cadence actually in force. Read
        // straight off the backend so the SYNC panel answers "is every trader
        // I picked being polled?" even with no browser engine attached.
        setBackendTraderSync(status.state?.traderLastSync ?? {});
        setBackendIntervalMs(status.state?.effectiveIntervalMs || null);
        // Account value is measured by the BACKEND only (it reads the
        // deposit wallet's cash and marks its own positions each cycle), and
        // it's what every proportional mirror is sized against — so surface
        // the backend's number rather than leaving the card blank.
        const backendAccount = status.state?.accountValue;
        if (backendAccount != null) {
          setEngineState((prev) =>
            prev && prev.accountValue !== backendAccount ? { ...prev, accountValue: backendAccount } : prev,
          );
        }
        // Merge backend observed trades into engine state if the browser
        // engine isn't providing them (backend sees trades even when the
        // tab is minimized / CPU-throttled).
        if (status.state && engineRef.current) {
          const backendObs = status.state.observedTrades ?? [];
          if (backendObs.length > 0) {
            const current = engineRef.current.getState();
            const existingIds = new Set(current.observedTrades.map((t) => t.id));
            const newFromBackend = backendObs
              .filter((t) => !existingIds.has(t.id))
              .map((t) => ({
                id: t.id,
                timestamp: t.timestamp,
                trader: t.trader,
                market: t.market,
                conditionId: t.conditionId,
                side: t.side as "BUY" | "SELL",
                size: t.size,
                price: t.price,
                notional: t.notional,
                // Backend may not stamp scores yet — defer to engine's
                // own observed-trade enrichment for the score column.
                score: t.score ?? 0,
                sharpe: t.sharpe ?? 0,
              }));
            if (newFromBackend.length > 0) {
              const merged = [...newFromBackend, ...current.observedTrades]
                .sort((a, b) => b.timestamp - a.timestamp)
                .slice(0, 500);
              setEngineState((prev) => prev ? { ...prev, observedTrades: merged } : prev);
            }
          }
        }
      } else {
        setBackendRunning(false);
        setBackendIntervalMs(null);
      }
    };

    void poll();
    const t = setInterval(poll, 5000);
    return () => { cancelled = true; clearInterval(t); };
  }, [isLive, backendRunning]);

  // On mount: check if a backend engine is already running (from a previous
  // session / tab close). If so, mark as live so the UI reflects it. A fresh
  // browser has no persisted session even when the backend engine has been
  // running for days — fall back to the signed-in owner's EOA so the console
  // still finds and reflects it.
  useEffect(() => {
    const persisted = loadPersistedLive();
    const addr = persisted?.address ?? getOwnerAddress();
    if (!addr) return;
    backendEoaRef.current = addr;
    // Re-attach to the strat this browser last drove. Without a persisted
    // session we probe unscoped — "is anything running for this wallet?".
    backendStrategyRef.current = persisted?.strategyId ?? null;
    void backendStatus(addr, backendStrategyRef.current).then((status) => {
      if (status?.config) {
        setAutoExecuteState(!!(status.config as { autoExecute?: boolean }).autoExecute);
      }
      if (status?.running) {
        setBackendRunning(true);
        // The browser engine will be restored separately by the existing
        // auto-resume logic in LivePanel (which checks persisted creds).
        // Here we just ensure the UI knows the backend is active.
      }
    });
  }, []);

  // Do NOT stop the browser engine on unmount — the backend keeps running.
  // The browser engine cleanup only happens on explicit stopLive().

  return (
    <CopyEngineContext.Provider value={{
      engineState, isLive, activeStrategyId, backendRunning,
      backendTraderSync, backendIntervalMs,
      autoExecute, setAutoExecute,
      startLive, stopLive, pauseLive, resumeLive, clearLog, catchUp,
    }}>
      {children}
    </CopyEngineContext.Provider>
  );
}
